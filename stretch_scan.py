"""stretch_scan.py — AT THE LINE: the names that have reached their usual
high or low, and what the calls above or the puts below pay.

The workflow this automates, done by hand today one ticker at a time: open
Analyze, look at where the stock's weekly highs usually land above Friday's
close, wait for the price to get there, then sell a call above it — or the
mirror image with puts after a drop. It works on a handful of followed
names because the watching is manual. This does the watching for the whole
watchlist, both sides, on two horizons:

  week   move measured from the PRIOR WEEK'S LAST CLOSE against the stock's
         usual weekly high/low (the dashed lines on the Analyze chart);
         the trade is the last listed expiry of this week.
  day    move measured from the PRIOR SESSION'S CLOSE against the usual
         daily high/low; the trade is a same-day expiry, which only the
         Monday/Wednesday/Friday names have.

Stage 1 is cheap: the board the app already keeps supplies the universe,
and one live-quote call per hundred names supplies where each is right now
(the board's own prices are rebuilt only twice a day). Each name's lines
(its usual high and low, in percent, and its sigma) are computed once from
daily bars and cached for days; the week's anchor is learned for nothing on
the first session of the week, when the previous close IS last week's close. Stage 2 spends one bounded chain call on each
name that has actually crossed a line, and prices every out-of-the-money
strike against what happened AFTER comparable crossings on this stock's
own record (stretch_evidence) — pooled with other names only when its own
record is thin, and graded so the card can say which.

A crossed line is a candidate, not a recommendation. READY means a real
contract on the screen cleared the risk limit and the fill gates; CROSSED
means the stock is there but nothing pays, and the row says why.

Alerts go out once per symbol, side and expiry, and that memory survives a
restart. The loop runs while the market is open whether or not anyone is
looking at the tab — that is the point of it.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import market_calendar as _cal
import stretch_evidence as se
import weekly_sell as ws

STRETCH_SCAN_VERSION = "stretch-scan-1.0.0"

_SCHWAB = None
_BOARD_FN = None
_BARS_FN = None
_MARKET_OPEN_FN = None
_NOW_FN = None
_CATALYST_FN = None
_QUOTES_FN = None
_NOTIFY_FN = None
_DATA_DIR: Path | None = None
_BASE_URL = "https://dashboard.jerrytrade.com"

_LOCK = threading.RLock()
_STATE: dict = {
    "rows": [], "near": [], "refused": [], "candidates": [], "as_of": None,
    "scanning": False, "thread": None, "error": None, "universe": 0, "scanned": 0,
    "warming": 0, "tick": None, "ticks": 0, "pushed_today": 0, "pushed_day": None,
}
_LINES: dict = {}       # symbol -> the stock's lines and sigma (persisted)
_ANCHORS: dict = {}     # symbol -> this week's anchor (persisted)
_PROFILES: dict = {}    # symbol -> (day, profile)  memory only
_POOL: dict = {}        # other names' crossings, in sigma (persisted)
_ALERTS: dict = {}      # symbol|side|expiry -> first_ready / pushed (persisted)
_CATALYSTS: dict = {}   # symbol -> (time, catalyst)  ten-minute memory
_ILLIQUID: dict = {}    # symbol -> {day, why}: chains too thin to trade (persisted)

CYCLE_SECS = 180
IDLE_SECS = 60
QUOTE_BATCH = 100       # symbols per live-quote call
CATALYST_TTL = 600

DEFAULTS = {
    "_doc": ("At the line (stretch_scan.py, STRETCH.md). Names that have reached "
             "their usual weekly or daily high/low, and which strikes beyond that "
             "level pay for the measured risk of the move continuing."),
    "select": {"min_price": 5.0, "max_candidates": 16, "near_fraction": 0.75,
               "line_quantile": 0.5, "strike_count": 40, "top_alts": 3,
               "max_itm": 0.30, "min_delta": 0.08, "max_delta": 0.50},
    "liquidity": {"min_underlying_dollar_volume": 2e7,
                  # per contract: no real bid, a spread past this, or open
                  # interest below this, and the strike is never priced
                  "min_bid": 0.05, "max_spread_pct": 25.0, "min_oi": 50,
                  # per name: fewer tradable strikes than this on the side
                  # at the expiry, and the whole name is refused for days
                  "min_tradable_strikes": 5, "illiquid_ttl_days": 5},
    "events": {"refuse_kinds": ["BUYOUT", "MERGER DEAL", "MERGER VOTE", "DEAL CLOSED"],
               "earnings_block": True},
    "scan": {"cycle_seconds": CYCLE_SECS, "cold_fetches_per_pass": 40,
             "lines_ttl_days": 5, "background": True},
    "alerts": {"enabled": True, "min_credit": 0.10, "priority": 0},
}


def configure(schwab_getter=None, board_getter=None, bars_fn=None, market_open_fn=None,
              now_fn=None, catalyst_fn=None, notify_fn=None, data_dir=None,
              base_url=None, quotes_fn=None) -> None:
    global _SCHWAB, _BOARD_FN, _BARS_FN, _MARKET_OPEN_FN, _NOW_FN, _CATALYST_FN
    global _NOTIFY_FN, _DATA_DIR, _BASE_URL, _QUOTES_FN
    _SCHWAB, _BOARD_FN, _BARS_FN = schwab_getter, board_getter, bars_fn
    _MARKET_OPEN_FN, _NOW_FN, _CATALYST_FN, _NOTIFY_FN = market_open_fn, now_fn, catalyst_fn, notify_fn
    _QUOTES_FN = quotes_fn
    _CATALYSTS.clear()
    _DATA_DIR = Path(data_dir) if data_dir else None
    if base_url:
        _BASE_URL = str(base_url).rstrip("/")
    _load_all()


def config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        import premium_edge as pe
        full = json.loads((Path(__file__).resolve().parent / "thresholds.json").read_text())
        cfg = pe._deep_merge(cfg, full.get("stretch") or {})  # noqa: SLF001
        dd = getattr(pe, "_DATA_DIR", None)
        if dd:
            p = Path(dd) / "thresholds.json"
            if p.exists():
                cfg = pe._deep_merge(cfg, (json.loads(p.read_text()).get("stretch") or {}))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass
    return cfg


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    if _NOW_FN:
        try:
            n = _NOW_FN()
            if isinstance(n, datetime):
                return n if n.tzinfo else n.astimezone()
        except Exception:  # noqa: BLE001
            pass
    return datetime.now().astimezone()


def _market_open() -> bool:
    if _MARKET_OPEN_FN:
        try:
            return bool(_MARKET_OPEN_FN())
        except Exception:  # noqa: BLE001
            return False
    return False


# ── persistence ─────────────────────────────────────────────────────────────
def _path(name: str) -> Path | None:
    return (_DATA_DIR / name) if _DATA_DIR else None


def _load_json(name: str, default):
    p = _path(name)
    if not p or not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return default


def _save_json(name: str, obj) -> None:
    p = _path(name)
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, separators=(",", ":")))
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass


def _load_all() -> None:
    with _LOCK:
        _LINES.clear(); _LINES.update(_load_json("stretch_lines.json", {}))
        _ANCHORS.clear(); _ANCHORS.update(_load_json("stretch_anchors.json", {}))
        _ALERTS.clear(); _ALERTS.update(_load_json("stretch_alerts.json", {}))
        _ILLIQUID.clear(); _ILLIQUID.update(_load_json("stretch_illiquid.json", {}))
        _POOL.clear()
        for h, sides in (_load_json("stretch_pool.json", {}) or {}).items():
            for s, cells in sides.items():
                for k, by_sym in cells.items():
                    if not isinstance(by_sym, dict):
                        continue          # an older, unattributed pool: rebuilt as names warm
                    _POOL.setdefault(h, {}).setdefault(s, {})[k] = {
                        sym: [tuple(e) for e in evs] for sym, evs in by_sym.items()}


def _save_pool() -> None:
    compact = {h: {s: {k: {sym: [[e[0], round(e[1], 3), round(e[2], 3)] for e in evs]
                           for sym, evs in by_sym.items()}
                       for k, by_sym in cells.items()}
                   for s, cells in sides.items()}
               for h, sides in _POOL.items()}
    _save_json("stretch_pool.json", compact)


# ── the calendar of the week ────────────────────────────────────────────────
def first_session_of_week(d: date) -> date:
    s = se.week_start(d)
    while not _cal.is_session(s):
        s += timedelta(days=1)
    return s


def last_session_of_week(d: date) -> date:
    s = se.week_start(d) + timedelta(days=4)
    while not _cal.is_session(s):
        s -= timedelta(days=1)
    return s


def sessions_left(today: date, expiry: date) -> int:
    """Full sessions AFTER today through the expiry. 0 for a same-day expiry."""
    n, d = 0, today
    while d < expiry:
        d += timedelta(days=1)
        if _cal.is_session(d):
            n += 1
    return n


def _long_date(d) -> str:
    try:
        return date.fromisoformat(str(d)[:10]).strftime("%A, %B %-d")
    except (TypeError, ValueError):
        return str(d)


# ── stage 1: the lines, the anchors, and who has crossed ────────────────────
def _bars(sym: str) -> list | None:
    if not _BARS_FN:
        return None
    try:
        return _BARS_FN(sym) or None
    except Exception:  # noqa: BLE001
        return None


def _profile_for(sym: str, today: date, cfg: dict, bars: list | None = None) -> dict | None:
    """This name's full record, computed once a day, and folded into the
    lines cache, the anchor cache and the pool as a side effect — every
    bars fetch teaches the scanner about one more name."""
    hit = _PROFILES.get(sym)
    if hit and hit[0] == today.isoformat():
        prof, fresh = hit[1], False
    else:
        bars = bars or _bars(sym)
        if not bars:
            return None
        prof = se.profile(bars, today, line_q=float(cfg["select"]["line_quantile"]))
        if not prof:
            return None
        fresh = True
    with _LOCK:
        _PROFILES[sym] = (today.isoformat(), prof)
        ln = prof["lines"]
        _LINES[sym] = {"day": today.isoformat(), "sigma_daily": prof["sigma_daily"],
                       "wk_hi": ln["week"]["high_pct"], "wk_lo": ln["week"]["low_pct"],
                       "d_hi": ln["day"]["high_pct"], "d_lo": ln["day"]["low_pct"],
                       "n_weeks": ln["week"]["n"], "n_days": ln["day"]["n"],
                       "best_hi": ln["week"]["best_high_pct"], "worst_lo": ln["week"]["worst_low_pct"]}
        a = prof["anchor"]
        if a.get("week_close") and (_ANCHORS.get(sym) or {}).get("week_start") != a["week_start"]:
            _ANCHORS[sym] = {"week_start": a["week_start"], "close": a["week_close"],
                             "source": "bars"}
        if fresh:
            se.add_to_pool(_POOL, prof, sym)
    return prof


def _days_since(day: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(str(day)[:10])).days
    except (TypeError, ValueError):
        return None


def contract_liquid(o: dict, lq: dict) -> tuple[bool, str | None]:
    """Is there anyone on the other side of this order? A strike with no
    real bid is not a trade at any price — and the strike engine would
    otherwise read its ASK as the credit. Open interest and the spread are
    the other two ways a fill fails."""
    bid, ask = _num(o.get("bid")) or 0.0, _num(o.get("ask")) or 0.0
    if bid < float(lq.get("min_bid", 0.05)):
        return False, f"no real bid ({bid:.2f})"
    if ask <= 0 or ask < bid:
        return False, "no usable ask"
    mid = (bid + ask) / 2.0
    spread_pct = (ask - bid) / mid * 100.0
    if spread_pct > float(lq.get("max_spread_pct", 25.0)):
        return False, f"spread {spread_pct:.0f}% of mid"
    oi = _num(o.get("openInterest")) or 0
    if oi < float(lq.get("min_oi", 50)):
        return False, f"open interest {oi:.0f}"
    return True, None


def chain_liquid(contracts: list, lq: dict) -> int:
    """How many strikes on this side, at this expiry, could actually be
    traded. A chain with a bid of zero on every put and a placeholder ask
    is the picture of nobody there."""
    return sum(1 for o in contracts if contract_liquid(o, lq)[0])


def _mark_illiquid(sym: str, today: date, why: str) -> None:
    with _LOCK:
        _ILLIQUID[sym] = {"day": today.isoformat(), "why": why}
        _save_json("stretch_illiquid.json", _ILLIQUID)


def _lines_fresh(rec: dict | None, today: date, ttl_days: int) -> bool:
    if not rec or not rec.get("day"):
        return False
    try:
        return (today - date.fromisoformat(rec["day"])).days <= ttl_days
    except ValueError:
        return False


def _trigger(move: float, line: float | None, sigma: float | None, side: str,
             near_fraction: float) -> dict | None:
    """One side's verdict on one horizon: crossed, near, or nothing. A call
    line is positive and needs the move at or above it; a put line is
    negative and needs the move at or below it."""
    if line is None or not sigma or sigma <= 0:
        return None
    if side == "call" and not (line > 0 and move > 0):
        return None
    if side == "put" and not (line < 0 and move < 0):
        return None
    frac = move / line
    if frac < near_fraction:
        return None
    return {"side": side, "state": "crossed" if frac >= 1.0 else "near",
            "move_pct": move * 100.0, "line_pct": line * 100.0,
            "move_sigma": abs(math.log1p(move)) / sigma,
            "line_sigma": abs(math.log1p(line)) / sigma, "fraction": frac}


def live_quotes(symbols: list) -> dict:
    """Where every name is RIGHT NOW. The watchlist board is rebuilt twice a
    day, so its `last` and `change` are hours old by mid-morning; a scanner
    that reads them would miss every crossing that happened since. One
    quote call per hundred names, every pass. Names a call cannot answer
    fall back to the board and are counted as such."""
    out: dict = {}
    if not _QUOTES_FN:
        return out
    for i in range(0, len(symbols), QUOTE_BATCH):
        batch = symbols[i:i + QUOTE_BATCH]
        try:
            got = _QUOTES_FN(batch) or {}
        except Exception:  # noqa: BLE001
            continue
        for sym, q in got.items():
            last = _num((q or {}).get("last"))
            chg = _num((q or {}).get("change_pct"))
            pc = _num((q or {}).get("close_prev"))
            if last and last > 0 and (chg is not None or (pc and pc > 0)):
                if chg is None:
                    chg = (last / pc - 1.0) * 100.0
                out[str(sym).upper()] = {"last": last, "change_pct": chg,
                                         "prev_close": pc if (pc and pc > 0) else None}
    return out


def stage1(cfg: dict | None = None, now: datetime | None = None) -> dict:
    cfg = cfg or config()
    st, sc = cfg["select"], cfg["scan"]
    n = now or _now()
    today = n.date()
    monday = se.week_start(today).isoformat()
    first = first_session_of_week(today)
    try:
        board = (_BOARD_FN() if _BOARD_FN else {}) or {}
    except Exception:  # noqa: BLE001
        board = {}
    rows = board.get("rows") or []
    ttl = int(sc.get("lines_ttl_days", 5))
    quotes = live_quotes([r.get("symbol") for r in rows if r.get("symbol")])
    n_live = 0
    ready, cold, skipped = [], [], []
    for r in rows:
        sym = r.get("symbol")
        q = quotes.get(str(sym).upper()) if sym else None
        if q:
            last, chg = q["last"], q["change_pct"]
            n_live += 1
        else:
            last, chg = _num(r.get("last")), _num(r.get("change"))
        if not sym or not last or chg is None or last < float(st["min_price"]):
            continue
        prev_close = (q or {}).get("prev_close") or (last / (1.0 + chg / 100.0))
        if prev_close <= 0:
            continue
        rec = {"symbol": sym, "last": last, "change_pct": chg, "prev_close": prev_close,
               "quote": "live" if q else "board",
               "dollar_volume": (_num(r.get("avg_volume")) or 0) * last,
               "sector": r.get("sector"), "next_earnings": r.get("next_earnings")}
        # The anchor for the week is free on its first session: the board's
        # own previous close is last week's last close. Every later day it
        # is remembered, and only a name never seen this week costs bars.
        anc = _ANCHORS.get(sym)
        if today == first:
            anc = {"week_start": monday, "close": prev_close, "source": rec["quote"]}
            _ANCHORS[sym] = anc
        if not (anc and anc.get("week_start") == monday and anc.get("close")):
            anc = None
        # A chain found too thin to trade is not looked at again for days —
        # there is nobody on the other side of the order, and a scanner that
        # keeps pricing it is the noise this board exists to remove.
        thin = _ILLIQUID.get(sym)
        if thin and _days_since(thin.get("day"), today) is not None \
                and _days_since(thin["day"], today) < int(cfg["liquidity"].get("illiquid_ttl_days", 5)):
            skipped.append({"symbol": sym, "why": [thin.get("why") or "options too thin to trade"],
                            "gate": "liquidity"})
            continue
        ln = _LINES.get(sym)
        if anc and _lines_fresh(ln, today, ttl):
            rec["anchor"], rec["lines"] = anc["close"], ln
            ready.append(rec)
        else:
            cold.append(rec)
    cold.sort(key=lambda c: -abs(c["change_pct"]))
    budget = int(sc.get("cold_fetches_per_pass", 40))
    warmed = 0
    for rec in cold[:budget]:
        prof = _profile_for(rec["symbol"], today, cfg)
        if not prof:
            continue
        anc, ln = _ANCHORS.get(rec["symbol"]), _LINES.get(rec["symbol"])
        if not (anc and anc.get("week_start") == monday and ln):
            continue
        rec["anchor"], rec["lines"] = anc["close"], ln
        ready.append(rec)
        warmed += 1
    if warmed:
        with _LOCK:
            _save_json("stretch_lines.json", _LINES)
            _save_json("stretch_anchors.json", _ANCHORS)
            _save_pool()
    near_f = float(st["near_fraction"])
    cands, near = [], []
    for rec in ready:
        ln = rec["lines"]
        sd = ln.get("sigma_daily")
        sw = sd * math.sqrt(se.SESSIONS_PER_WEEK) if sd else None
        move_d = rec["change_pct"] / 100.0
        move_w = rec["last"] / rec["anchor"] - 1.0
        trig = []
        for horizon, move, sig, hi, lo in (("day", move_d, sd, ln.get("d_hi"), ln.get("d_lo")),
                                           ("week", move_w, sw, ln.get("wk_hi"), ln.get("wk_lo"))):
            for side, line in (("call", hi), ("put", lo)):
                t = _trigger(move, line, sig, side, near_f)
                if t:
                    t["horizon"] = horizon
                    trig.append(t)
        rec["move_week_pct"], rec["sigma_daily"] = move_w * 100.0, sd
        crossed = [t for t in trig if t["state"] == "crossed"]
        for t in trig:
            if t["state"] == "near":
                near.append({"symbol": rec["symbol"], "horizon": t["horizon"], "side": t["side"],
                             "move_pct": t["move_pct"], "line_pct": t["line_pct"],
                             "fraction": t["fraction"], "last": rec["last"]})
        if crossed:
            rec["triggers"] = crossed
            rec["stretch_sigma"] = max(t["move_sigma"] for t in crossed)
            cands.append(rec)
    cands.sort(key=lambda c: -c["stretch_sigma"])
    near.sort(key=lambda x: -x["fraction"])
    with _LOCK:
        _STATE["warming"] = max(0, len(cold) - budget)
        _STATE["quotes_live"] = n_live
    return {"candidates": cands[: int(st["max_candidates"])], "near": near,
            "universe": len(rows), "n_crossed": len(cands), "warmed": warmed,
            "quotes_live": n_live, "skipped": skipped}


# ── stage 2: one chain per crossed name, every strike priced ────────────────
def _expiries(chain: dict) -> list[date]:
    out = []
    for e in (chain.get("chains") or {}):
        try:
            out.append(date.fromisoformat(str(e)[:10]))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _catalyst_refusal(sym: str, cfg: dict) -> str | None:
    """The filing-aware catalyst (earnings, EDGAR events, offerings, analyst
    actions, then headlines), remembered ten minutes a name so a candidate
    that stays on the board does not re-read its filings every pass."""
    if not _CATALYST_FN:
        return None
    hit = _CATALYSTS.get(sym)
    if hit and time.time() - hit[0] < CATALYST_TTL:
        cat = hit[1]
    else:
        try:
            cat = _CATALYST_FN(sym) or {}
        except Exception:  # noqa: BLE001
            return None
        _CATALYSTS[sym] = (time.time(), cat)
    kind = str(cat.get("kind") or "").upper()
    if kind in {str(k).upper() for k in cfg["events"]["refuse_kinds"]}:
        return f"{kind.lower()} headline — the one move that does not come back"
    return None


def _earnings_inside(rec: dict, today: date, expiry: date) -> str | None:
    ne = rec.get("next_earnings")
    try:
        d = date.fromisoformat(str(ne)[:10]) if ne else None
    except ValueError:
        d = None
    if d and today <= d <= expiry:
        return f"reports {_long_date(d)}, inside the trade"
    return None


_LADDER = ("strike", "delta", "credit", "bid", "itm_pct", "touch_pct", "ev_ann_pct",
           "cushion_pct", "liq_grade", "oi", "score")


def _ladder_row(row: dict, gate: str | None) -> dict:
    out = {k: row.get(k) for k in _LADDER}
    out["gate"] = gate
    return out


def evaluate(rec: dict, trig: dict, chain: dict, prof: dict, cfg: dict, today: date,
             pool: dict | None = None) -> dict:
    """One symbol, one side, one horizon → the row the board shows, READY
    or CROSSED with the reason."""
    st = cfg["select"]
    sym, horizon, side = rec["symbol"], trig["horizon"], trig["side"]
    spot = _num((chain.get("underlying") or {}).get("last")) or rec["last"]
    exps = _expiries(chain)
    base = {"symbol": sym, "horizon": horizon, "side": side, "spot": spot,
            "anchor": rec["anchor"] if horizon == "week" else rec["prev_close"],
            "anchor_label": ("the prior week's last close" if horizon == "week"
                             else "the prior session's close"),
            "move_pct": trig["move_pct"], "move_sigma": trig["move_sigma"],
            "line_pct": trig["line_pct"], "line_sigma": trig["line_sigma"],
            "line_price": (rec["anchor"] if horizon == "week" else rec["prev_close"]) * (1 + trig["line_pct"] / 100.0),
            "sigma_annual": prof["sigma_annual"], "sector": rec.get("sector"),
            "record_pct": (prof["lines"][horizon]["best_high_pct"] if side == "call"
                           else prof["lines"][horizon]["worst_low_pct"]),
            "n_windows": prof["lines"][horizon]["n"]}
    if horizon == "day":
        exp = today if today in exps else None
        if exp is None:
            return {**base, "state": "crossed", "expiration": None,
                    "why": [f"no option on this name expires today — the calendar, not the scanner"]}
    else:
        last = last_session_of_week(today)
        inside = [e for e in exps if today <= e <= last]
        exp = max(inside) if inside else None
        if exp is None:
            return {**base, "state": "crossed", "expiration": None,
                    "why": [f"no option on this name expires this week (by {_long_date(last)})"]}
    base["expiration"] = exp.isoformat()
    base["sessions_left"] = sessions_left(today, exp)
    if cfg["events"].get("earnings_block", True):
        e_why = _earnings_inside(rec, today, exp)
        if e_why:
            return {**base, "state": "crossed", "why": [e_why]}
    if (rec.get("dollar_volume") or 0) < float(cfg["liquidity"]["min_underlying_dollar_volume"]):
        return {**base, "state": "crossed", "why": ["the underlying is too thin to manage"]}
    ev = se.evidence(prof, horizon, side, trig["move_sigma"], base["sessions_left"], pool, exclude=sym)
    base.update({"n": ev["n"], "n_own": ev["n_own"], "n_pool": ev["n_pool"], "grade": ev["grade"],
                 "basis": ev["basis"], "level": ev["level"], "clamped": ev["clamped"],
                 "p_closed_back": ev["p_closed_back"],
                 "median_beyond_pct": (ev["median_beyond_pct"] * 100.0) if ev["median_beyond_pct"] is not None else None,
                 "p90_beyond_pct": (ev["p90_beyond_pct"] * 100.0) if ev["p90_beyond_pct"] is not None else None})
    if ev["grade"] == "THIN":
        return {**base, "state": "crossed",
                "why": [f"only {ev['n']} comparable crossings on record (needs {se.MIN_EVENTS}), "
                        f"even with the pool"]}
    contracts = ((chain.get("chains") or {}).get(exp.isoformat()) or {}).get(side + "s") or []
    lq = cfg["liquidity"]
    tradable = chain_liquid(contracts, lq)
    need = int(lq.get("min_tradable_strikes", 5))
    if tradable < need:
        ttl = int(lq.get("illiquid_ttl_days", 5))
        why = (f"options too thin to trade — {tradable} {side} strike{'s' if tradable != 1 else ''} "
               f"with a real bid, a fillable spread and open interest at this expiry (needs {need}); "
               f"skipped for {ttl} days")
        _mark_illiquid(sym, today, why)
        return {**base, "state": "crossed", "why": [why], "gate": "liquidity"}
    dte_cal = max(0.5, float((exp - today).days))
    lo_d, hi_d = float(st["min_delta"]), float(st["max_delta"])
    ladder, ok, gates, unquoted = [], [], Counter(), 0
    for o in contracts:
        k = _num(o.get("strike"))
        if k is None or (side == "call" and k <= spot) or (side == "put" and k >= spot):
            continue
        d = _num(o.get("delta"))
        if d is not None and not (lo_d <= abs(d) <= hi_d):
            continue
        # Never handed to the strike engine: with a zero bid it would read
        # the ask as the credit and price a sale nobody will take.
        if not contract_liquid(o, lq)[0]:
            unquoted += 1
            continue
        row = ws.evaluate_strike(o, side, spot, ev["windows"], dte_cal, None)
        if row is None:
            unquoted += 1
            continue
        gate = ws._gate(row, side, float(st["max_itm"]))  # noqa: SLF001
        ladder.append(_ladder_row(row, gate))
        if gate:
            gates[gate] += 1
        else:
            ok.append(row)
    ladder.sort(key=lambda r: r["strike"])
    base["ladder"] = ladder
    if not ok:
        if ladder:
            why = [f"every strike failed a gate — most often: {gates.most_common(1)[0][0]}"]
        elif unquoted:
            why = [f"none of the {unquoted} strikes in the delta range has a real bid, a fillable "
                   f"spread and open interest"]
        else:
            why = [f"no strike between {lo_d:.2f} and {hi_d:.2f} delta is listed"]
        return {**base, "state": "crossed", "why": why}
    ok.sort(key=lambda r: (-r["score"], -(r["ev_ann_pct"] or 0)))
    pick = ok[0]
    base.update({"state": "ready", "strike": pick["strike"], "delta": pick["delta"],
                 "credit": pick["credit"], "bid": pick["bid"], "ask": pick["ask"],
                 "itm_pct": pick["itm_pct"], "touch_pct": pick["touch_pct"],
                 "ev_ann_pct": pick["ev_ann_pct"], "cushion_pct": pick["cushion_pct"],
                 "strike_pct": (pick["strike"] / base["anchor"] - 1.0) * 100.0,
                 "liq_grade": pick["liq_grade"], "oi": pick["oi"], "spread_pct": pick["spread_pct"],
                 "iv": pick["iv"],
                 "alts": [_ladder_row(r, None) for r in ok[1:1 + int(st["top_alts"])]],
                 "why": ws._why(pick, side, "history")})  # noqa: SLF001
    return base


def _scan(sc, cfg: dict | None = None, now: datetime | None = None) -> None:
    cfg = cfg or config()
    n = now or _now()
    today = n.date()
    s1 = stage1(cfg, n)
    cands = s1["candidates"]
    to_date = last_session_of_week(today).isoformat()
    refused, rows = list(s1["skipped"]), []

    def one(c):
        sym = c["symbol"]
        stop = _catalyst_refusal(sym, cfg)
        if stop:
            return sym, [], [{"symbol": sym, "why": [stop], "gate": "event"}]
        try:
            chain = sc.get_option_chain(sym, expiration=today.isoformat(), to_date=to_date,
                                        strike_count=int(cfg["select"]["strike_count"]))
        except Exception as exc:  # noqa: BLE001
            return sym, [], [{"symbol": sym, "why": [f"the options feed could not be read: {exc}"],
                              "gate": "data"}]
        if chain is None:
            return sym, [], [{"symbol": sym, "why": ["the options feed did not answer for this name"],
                              "gate": "data"}]
        prof = _profile_for(sym, today, cfg)
        if not prof:
            return sym, [], [{"symbol": sym, "why": ["price history could not be read"],
                              "gate": "data"}]
        out, ref = [], []
        for t in c["triggers"]:
            r = evaluate(c, t, chain, prof, cfg, today, _POOL)
            if r["state"] != "ready":
                ref.append({"symbol": sym, "horizon": r["horizon"], "side": r["side"],
                            "why": r["why"],
                            "gate": r.get("gate") or ("expiry" if not r.get("expiration") else "select")})
            # A name with nothing expiring in the window is the calendar, not a
            # setup — most stocks list options only on Fridays. It is counted
            # among the refusals, where the reason is, and kept off the board.
            if not r.get("expiration"):
                continue
            r["key"] = f"{sym}|{r['horizon']}|{r['side']}|{r['expiration']}"
            # The alert memory is per symbol, side and EXPIRY: on a Friday the
            # day and the week resolve to the same contract, and that is one
            # alert, not two.
            r["alert_key"] = f"{sym}|{r['side']}|{r['expiration']}"
            out.append(r)
        return sym, out, ref

    with ThreadPoolExecutor(max_workers=4) as ex:
        for _sym, out, ref in ex.map(one, cands):
            rows.extend(out)
            refused.extend(ref)
    rows.sort(key=lambda r: (r["state"] != "ready", -r["move_sigma"]))
    stamp = n.replace(microsecond=0).isoformat()
    _mark_and_alert(rows, cfg, n)
    with _LOCK:
        _STATE.update({"rows": rows, "refused": refused, "near": s1["near"],
                       "candidates": [{k: c.get(k) for k in ("symbol", "last", "change_pct",
                                                              "move_week_pct", "stretch_sigma")}
                                      | {"triggers": [f"{t['horizon']} {t['side']}" for t in c["triggers"]]}
                                      for c in cands],
                       "universe": s1["universe"], "scanned": len(cands),
                       "n_crossed": s1["n_crossed"], "quotes_live": s1["quotes_live"],
                       "as_of": stamp, "tick": stamp,
                       "ticks": _STATE["ticks"] + 1, "error": None})


# ── alerts: once per symbol, side and expiry, remembered across restarts ────
def _mark_and_alert(rows: list, cfg: dict, now: datetime) -> None:
    al = cfg["alerts"]
    today = now.date().isoformat()
    stamp = now.replace(microsecond=0).isoformat()
    changed = False
    with _LOCK:
        if _STATE.get("pushed_day") != today:
            _STATE["pushed_day"], _STATE["pushed_today"] = today, 0
        for k in [k for k, v in _ALERTS.items() if (v.get("expiration") or "9999") < today]:
            _ALERTS.pop(k, None)
            changed = True
        for r in rows:
            rec = _ALERTS.get(r["alert_key"])
            if r["state"] != "ready":
                r["first_seen"], r["pushed"], r["is_new"] = (rec or {}).get("first_ready"), (rec or {}).get("pushed"), False
                continue
            if rec is None:
                rec = {"first_ready": stamp, "pushed": None, "expiration": r.get("expiration"),
                       "strike": r.get("strike"), "credit": r.get("credit")}
                _ALERTS[r["alert_key"]] = rec
                changed = True
                # The ledger records every first READY, whether or not a push
                # follows — a prediction below the push floor, or one made
                # with no channel configured, is still a prediction to grade.
                _log_alert(r, stamp)
            r["first_seen"], r["is_new"] = rec["first_ready"], rec["first_ready"] == stamp
            if (rec.get("pushed") is None and al.get("enabled", True) and _NOTIFY_FN
                    and (r.get("credit") or 0) >= float(al.get("min_credit", 0.10))):
                if _push(r, cfg):
                    rec["pushed"] = stamp
                    _STATE["pushed_today"] += 1
                    changed = True
            r["pushed"] = rec.get("pushed")
        if changed:
            _save_json("stretch_alerts.json", _ALERTS)


def alert_text(r: dict) -> tuple[str, str]:
    """The push. Ticker, side, expiry, the contract, where the stock is
    against its anchor and its line, the credit, the measured risk and the
    evidence behind it, and the link that opens Analyze on the name."""
    side = r["side"]
    title = f"{r['symbol']} {side} · at the line"
    exp_words = "today" if r["horizon"] == "day" and r.get("sessions_left") == 0 else _long_date(r["expiration"])
    body = (f"{r['symbol']} is {r['move_pct']:+.1f}% vs {r['anchor_label']} — past its usual "
            f"{r['line_pct']:+.1f}% ({r['horizon']}).\n"
            f"Sell the {exp_words} {r['strike']:g} {side}: ~${r['credit']:.2f} credit"
            f"{(' at %.2fΔ' % abs(r['delta'])) if r.get('delta') is not None else ''}.\n"
            f"Finished through a strike like this {r['itm_pct']:.0f}% of {r['n']} comparable "
            f"crossings ({r['grade'].lower()}); touched it {r['touch_pct']:.0f}%.\n"
            f"{_BASE_URL}/?symbol={r['symbol']}&tab=analyze")
    return title, body


def _push(r: dict, cfg: dict) -> bool:
    title, body = alert_text(r)
    try:
        res = _NOTIFY_FN(title, body, int(cfg["alerts"].get("priority", 0)))
    except Exception:  # noqa: BLE001
        return False
    return bool((res or {}).get("ok")) if isinstance(res, dict) else bool(res)


def _log_alert(r: dict, stamp: str) -> None:
    """The prediction log: every READY, with the numbers it was READY on,
    so the calls can be graded against what the stock then did. Separate
    from any record of what was actually traded."""
    p = _path("stretch_alerts.jsonl")
    if not p:
        return
    keep = ("key", "symbol", "horizon", "side", "expiration", "spot", "anchor", "move_pct",
            "line_pct", "move_sigma", "strike", "delta", "credit", "bid", "itm_pct", "touch_pct",
            "n", "n_own", "grade", "sessions_left")
    try:
        with p.open("a") as f:
            f.write(json.dumps({"at": stamp, **{k: r.get(k) for k in keep}}) + "\n")
    except Exception:  # noqa: BLE001
        pass


# ── the loop: runs while the market is open, whoever is looking ─────────────
def _loop() -> None:
    try:
        while True:
            cfg = config()
            cycle = float((cfg.get("scan") or {}).get("cycle_seconds") or CYCLE_SECS)
            if not _market_open():
                time.sleep(IDLE_SECS)
                continue
            sc = _SCHWAB() if _SCHWAB else None
            if sc is None:
                with _LOCK:
                    _STATE["error"] = "the broker connection is not available"
                time.sleep(IDLE_SECS)
                continue
            t0 = time.time()
            try:
                _scan(sc, cfg)
            except Exception as exc:  # noqa: BLE001
                with _LOCK:
                    _STATE["error"] = str(exc)
            time.sleep(max(15.0, cycle - (time.time() - t0)))
    finally:
        with _LOCK:
            _STATE["scanning"] = False
            _STATE["thread"] = None


def start_scheduler() -> None:
    with _LOCK:
        if _STATE["scanning"]:
            return
        _STATE["scanning"] = True
        t = threading.Thread(target=_loop, name="stretch-scan", daemon=True)
        _STATE["thread"] = t
        t.start()


def _phase(now: datetime) -> str:
    d = now.date()
    if not _cal.is_session(d):
        return "holiday" if d.weekday() < 5 else "weekend"
    o, c = _cal.session_span(d)
    return "pre" if now.time() < o else ("post" if now.time() >= c else "open")


def _why_nothing(st: dict, open_now: bool, now: datetime) -> str:
    if not open_now:
        ph = _phase(now)
        return {"pre": "The market has not opened yet; the board fills once it does.",
                "post": "The market is closed for the day. Anything READY was for today's session.",
                "holiday": f"The market is shut today ({_cal.describe(now.date())}).",
                "weekend": "It is the weekend."}.get(ph, "The market is closed.")
    if not st["tick"]:
        return "The first scan of the session has not finished yet."
    if st["scanned"] == 0:
        return (f"Nothing on the watchlist has reached its usual high or low yet "
                f"({len(st['near'])} within reach)."
                + (f" {st['warming']} names are still being measured." if st["warming"] else ""))
    if st["rows"]:
        return (f"{st['scanned']} names have crossed a line, but no strike on any of them "
                f"cleared the risk limit and the fill gates. Each says why.")
    return "Names have crossed a line but could not be priced — see what was refused."


def snapshot() -> dict:
    cfg = config()
    now = _now()
    open_now = _market_open()
    with _LOCK:
        st = dict(_STATE)
        rows = list(st["rows"])
    ready = [r for r in rows if r["state"] == "ready"]
    out = {
        "ok": True, "version": STRETCH_SCAN_VERSION, "evidence": se.SCHEMA,
        "as_of": st["as_of"], "scanning": st["scanning"], "market_open": open_now,
        "phase": _phase(now), "error": st["error"], "universe": st["universe"],
        "scanned": st["scanned"], "n_crossed": st.get("n_crossed", 0), "warming": st["warming"],
        "rows": rows, "n_ready": len(ready), "near": st["near"][:40],
        "candidates": st["candidates"], "refused": st["refused"][:40],
        "pool": se.pool_size(_POOL),
        "quotes_live": st.get("quotes_live", 0),
        "quotes": ("live" if _QUOTES_FN else "board only — the watchlist board is rebuilt twice a day, "
                   "so between rebuilds a crossing cannot be seen"),
        "lines_cached": len(_LINES), "alerts": {"pushed_today": st["pushed_today"],
                                                "enabled": bool(cfg["alerts"].get("enabled", True)),
                                                "configured": bool(_NOTIFY_FN)},
        "limits": {"max_itm": cfg["select"]["max_itm"], "min_delta": cfg["select"]["min_delta"],
                   "max_delta": cfg["select"]["max_delta"], "line_quantile": cfg["select"]["line_quantile"]},
        "cycle_seconds": cfg["scan"].get("cycle_seconds", CYCLE_SECS),
    }
    out["no_trade"] = not ready
    out["no_trade_reason"] = _why_nothing(st, open_now, now) if not ready else None
    return out


def detail(symbol: str) -> dict:
    sym = (symbol or "").upper()
    with _LOCK:
        rows = [r for r in _STATE["rows"] if r["symbol"] == sym]
        refused = [r for r in _STATE["refused"] if r.get("symbol") == sym]
        hit = _PROFILES.get(sym)
    if not rows and not refused:
        return {"ok": False, "symbol": sym,
                "error": "not on the board — it has not reached a line, or the scan has not reached it"}
    return {"ok": True, "symbol": sym, "rows": rows, "refused": refused,
            "profile": _compact_profile(hit[1]) if hit else None}


def _compact_profile(prof: dict) -> dict:
    return {k: prof[k] for k in ("schema", "n_weeks", "n_days", "sigma_daily", "sigma_weekly",
                                 "sigma_annual", "lines", "line_q", "anchor", "vs_monday")}


def profile_for(symbol: str, today: date | None = None) -> dict:
    """For Analyze: this one name's lines and what happened after they were
    reached, on its own record. Costs a bars fetch once a day."""
    sym = (symbol or "").upper()
    t = today or _now().date()
    prof = _profile_for(sym, t, config())
    if not prof:
        return {"ok": False, "symbol": sym,
                "error": "not enough daily history to measure this name (needs 12 complete weeks)"}
    out = {"ok": True, "symbol": sym, **_compact_profile(prof)}
    # What happens after a crossing at the line itself, both sides, this week
    # — the numbers the chart's dashed lines cannot say.
    after = {}
    for side in se.SIDES:
        ln = se.describe(prof, "week", side)
        if ln["sigma"] is None:
            continue
        ev = se.evidence(prof, "week", side, ln["sigma"], 3, _POOL, exclude=sym)
        after[side] = {"line": ln, "n": ev["n"], "n_own": ev["n_own"], "grade": ev["grade"],
                       "basis": ev["basis"], "p_closed_back": ev["p_closed_back"],
                       "median_beyond_pct": ev["median_beyond_pct"], "p90_beyond_pct": ev["p90_beyond_pct"]}
    out["after_the_line"] = after
    with _LOCK:
        out["live"] = [r for r in _STATE["rows"] if r["symbol"] == sym]
    return out


def alerts_log(days: int = 30) -> list[dict]:
    p = _path("stretch_alerts.jsonl")
    if not p or not p.exists():
        return []
    cut = (_now().date() - timedelta(days=days)).isoformat()
    out = []
    try:
        for line in p.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if (rec.get("at") or "") >= cut:
                out.append(rec)
    except Exception:  # noqa: BLE001
        return out
    return out[-500:]


def status() -> dict:
    with _LOCK:
        return {"version": STRETCH_SCAN_VERSION, "as_of": _STATE["as_of"], "tick": _STATE["tick"],
                "ticks": _STATE["ticks"], "scanning": _STATE["scanning"], "error": _STATE["error"],
                "universe": _STATE["universe"], "scanned": _STATE["scanned"],
                "rows": len(_STATE["rows"]), "warming": _STATE["warming"],
                "lines_cached": len(_LINES), "anchors_cached": len(_ANCHORS),
                "quotes_live": _STATE.get("quotes_live", 0), "quotes_fn": bool(_QUOTES_FN),
                "alerts_remembered": len(_ALERTS), "pushed_today": _STATE["pushed_today"],
                "illiquid_remembered": len(_ILLIQUID),
                "background": bool(config()["scan"].get("background", True))}
