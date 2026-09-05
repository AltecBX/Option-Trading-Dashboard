"""spike_scan.py — SOLD INTO STRENGTH: today's runs, priced (v4.82).

The workflow this replaces, done by hand: sort the day's biggest movers,
judge how extreme each move is, open the chain, find today's expiry, read
the premium, and decide whether the odds justify it — by which time the
volatility premium has usually collapsed.

What it does instead, every couple of minutes while the market is open:

  Stage 1  free    rank the watchlist board by how big today's move is IN
                   THE STOCK'S OWN SIGMA. Percent is the wrong ruler: 3.6%
                   on a name running at 81% annualised volatility is an
                   ordinary session, 16.4% on a 51%-vol name is a 4.7-sigma
                   event, and sorting on percent puts those two backwards.
  Stage 2  chains  one bounded chain call per candidate, SAME-DAY expiries
                   only, and every call strike above the current price is
                   priced against the measured record in spike_evidence.
  Stage 3  free    rank by the only number that decides the trade —
                   the credit on the screen MINUS what the measured history
                   says that call settles for. No volatility model is
                   involved in the verdict.

The ranking figure is dollars of expected edge per contract, not a
probability and not a score, because a probability cannot price a sale and a
score cannot be checked against a fill.

Two refusals are absolute here. A stock spiking on a takeover or merger
headline is never listed: that is the one move that does not come back, and
it is also the move that quietly leaves a survivorship hole in any history
measured on names that still exist. And a call with no bid is not a trade.
"""
from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import spike_evidence as sev

SPIKE_SCAN_VERSION = "spike-scan-1.0.0"

# Providers, wired by options_dashboard
_SCHWAB = None
_BOARD_FN = None
_BARS_FN = None
_MARKET_OPEN_FN = None
_NOW_FN = None
_CATALYST_FN = None
_MINUTE_DAY_FN = None
_DATA_DIR: Path | None = None

_LOCK = threading.RLock()
_STATE: dict = {
    "rows": [], "as_of": None, "scanning": False, "thread": None, "error": None,
    "universe": 0, "scanned": 0, "candidates": [], "refused": [], "last_req": 0.0,
    "by_symbol": {}, "profile": None, "profile_day": None,
}
_TABLES: dict = {}           # symbol -> (day, ticker table)
_SIGMAS: dict = {}           # symbol -> (day, daily sigma)

WORKER_IDLE_SECS = 300       # stop the loop when nobody is looking
CYCLE_SECS = 120             # a spike's premium decays fast; so does this board
SESSION_OPEN = dtime(9, 30)
SESSION_CLOSE = dtime(16, 0)

DEFAULTS = {
    "_doc": ("Sold into strength (spike_scan.py, SPIKE_FADE.md). Which of today's "
             "runs pay enough to sell a same-day call above them. The move and the "
             "strike are measured in the stock's own daily sigma."),
    "select": {"min_move_sigma": 1.5, "min_price": 5.0, "max_candidates": 14,
               "max_dte": 0, "strike_count": 40, "min_beyond_sigma": 0.0,
               "max_rows_per_symbol": 3, "top_n": 25},
    "liquidity": {"min_bid": 0.05, "max_spread_pct": 25.0, "min_oi": 50,
                  "min_volume": 0, "min_underlying_dollar_volume": 2e7},
    "edge": {"min_edge_per_contract": 5.0, "min_credit": 0.10},
    "events": {"refuse_kinds": ["BUYOUT", "MERGER DEAL", "MERGER VOTE", "DEAL CLOSED"]},
    "scan": {"cycle_seconds": CYCLE_SECS, "profile_days": 15, "profile_symbol": "SPY"},
}


def configure(schwab_getter=None, board_getter=None, bars_fn=None, market_open_fn=None,
              now_fn=None, catalyst_fn=None, minute_day_fn=None, data_dir=None) -> None:
    global _SCHWAB, _BOARD_FN, _BARS_FN, _MARKET_OPEN_FN, _NOW_FN, _CATALYST_FN
    global _MINUTE_DAY_FN, _DATA_DIR
    _SCHWAB, _BOARD_FN, _BARS_FN = schwab_getter, board_getter, bars_fn
    _MARKET_OPEN_FN, _NOW_FN, _CATALYST_FN = market_open_fn, now_fn, catalyst_fn
    _MINUTE_DAY_FN = minute_day_fn
    _DATA_DIR = Path(data_dir) if data_dir else None


def config() -> dict:
    """DEFAULTS overlaid by thresholds.json → spike, so every floor here is
    a number the reader can see and change."""
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        import premium_edge as pe
        full = json.loads((Path(__file__).resolve().parent / "thresholds.json").read_text())
        cfg = pe._deep_merge(cfg, full.get("spike") or {})  # noqa: SLF001
        dd = getattr(pe, "_DATA_DIR", None)
        if dd:
            p = Path(dd) / "thresholds.json"
            if p.exists():
                cfg = pe._deep_merge(cfg, (json.loads(p.read_text()).get("spike") or {}))  # noqa: SLF001
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


# ── the session clock ───────────────────────────────────────────────────────
def elapsed_fraction(now: datetime | None = None) -> float:
    """How much of the regular session has already gone. Before the open it
    is 0; after the bell, 1."""
    n = now or _now()
    o = n.replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0)
    c = n.replace(hour=SESSION_CLOSE.hour, minute=SESSION_CLOSE.minute, second=0, microsecond=0)
    if n <= o:
        return 0.0
    if n >= c:
        return 1.0
    return (n - o).total_seconds() / (c - o).total_seconds()


def session_profile(refresh: bool = False) -> list | None:
    """How much of a session's variance is still ahead at each point of the
    day, MEASURED from a liquid proxy's own minute bars over recent
    sessions.

    Risk does not arrive evenly through the day — the open and the close
    carry far more of it than midday — and this trade is priced almost
    entirely on how much is left. Without this the module falls back to the
    clock and labels the answer MODELED. Built once a day; a handful of
    calls, not per candidate."""
    cfg = config()
    today = _now().date().isoformat()
    with _LOCK:
        if _STATE["profile"] and _STATE["profile_day"] == today and not refresh:
            return _STATE["profile"]
    if not _MINUTE_DAY_FN:
        return None
    sym = str((cfg["scan"] or {}).get("profile_symbol") or "SPY")
    ndays = int((cfg["scan"] or {}).get("profile_days") or 15)
    buckets = 26                     # 15-minute blocks across a 6.5-hour session
    acc = [0.0] * buckets
    used = 0
    d = _now().date()
    tries = 0
    while used < ndays and tries < ndays * 3:
        tries += 1
        d -= timedelta(days=1)
        if d.weekday() > 4:
            continue
        try:
            bars = _MINUTE_DAY_FN(sym, d.isoformat())
        except Exception:  # noqa: BLE001
            bars = None
        if not bars or len(bars) < 100:
            continue
        var = [0.0] * buckets
        prev = None
        for b in bars:
            c = _num(b.get("close"))
            ts = b.get("datetime") or b.get("time") or b.get("ts")
            if c is None or c <= 0 or ts is None:
                continue
            try:
                t = datetime.fromtimestamp(float(ts) / 1000.0)
            except Exception:  # noqa: BLE001
                continue
            mins = (t.hour - SESSION_OPEN.hour) * 60 + (t.minute - SESSION_OPEN.minute)
            if mins < 0 or mins >= 390:
                continue
            if prev and prev > 0:
                var[min(buckets - 1, mins * buckets // 390)] += math.log(c / prev) ** 2
            prev = c
        tot = sum(var)
        if tot <= 0:
            continue
        run = 0.0
        for i in range(buckets):
            run += var[i]
            acc[i] += 1.0 - run / tot          # share still ahead after bucket i
        used += 1
    if used < 3:
        return None
    prof = [(0.0, 1.0)] + [((i + 1) / buckets, max(0.0, min(1.0, acc[i] / used)))
                           for i in range(buckets)]
    with _LOCK:
        _STATE["profile"] = prof
        _STATE["profile_day"] = today
        _STATE["profile_days_used"] = used
    return prof


# ── per-symbol caches ───────────────────────────────────────────────────────
def _bars(sym: str) -> list | None:
    if not _BARS_FN:
        return None
    try:
        return _BARS_FN(sym)
    except Exception:  # noqa: BLE001
        return None


def _table_for(sym: str, bars: list) -> dict | None:
    """This ticker's own record of what it does after a run, built at most
    once a day — it is a pass over ten years of bars."""
    today = _now().date().isoformat()
    hit = _TABLES.get(sym)
    if hit and hit[0] == today:
        return hit[1]
    try:
        t = sev.ticker_table(bars)
    except Exception:  # noqa: BLE001
        return None
    _TABLES[sym] = (today, t)
    return t


def _sigma_for(sym: str, bars: list) -> float | None:
    today = _now().date().isoformat()
    hit = _SIGMAS.get(sym)
    if hit and hit[0] == today:
        return hit[1]
    s = sev.daily_sigma(bars)
    if s:
        _SIGMAS[sym] = (today, s)
    return s


# ── stage 1: which names have actually run, in their own terms ──────────────
def stage1(cfg: dict | None = None) -> tuple[list, int]:
    """Rank the board by the size of today's move IN SIGMA. Free: it reads
    the board the app already keeps and the bars it already caches."""
    cfg = cfg or config()
    st = cfg["select"]
    board = {}
    try:
        board = (_BOARD_FN() if _BOARD_FN else {}) or {}
    except Exception:  # noqa: BLE001
        board = {}
    out = []
    rows = board.get("rows") or []
    for r in rows:
        sym = r.get("symbol")
        last = _num(r.get("last"))
        chg = _num(r.get("change"))
        if not sym or not last or chg is None or last < float(st["min_price"]):
            continue
        if chg <= 0:
            continue                       # this is a call-selling board
        prev_close = last / (1.0 + chg / 100.0)
        if prev_close <= 0:
            continue
        bars = _bars(sym)
        if not bars or len(bars) < 60:
            continue
        sigma = _sigma_for(sym, bars)
        if not sigma:
            continue
        move_s = sev.in_sigma(last, prev_close, sigma)
        if move_s is None or move_s < float(st["min_move_sigma"]):
            continue
        dollar_vol = (_num(r.get("avg_volume")) or 0) * last
        out.append({"symbol": sym, "last": last, "change_pct": chg,
                    "prev_close": prev_close, "sigma": sigma,
                    "sigma_annual": sigma * math.sqrt(252),
                    "move_sigma": move_s, "dollar_volume": dollar_vol,
                    "sector": r.get("sector")})
    out.sort(key=lambda c: -c["move_sigma"])
    return out[: int(st["max_candidates"])], len(rows)


# ── stage 2: one chain per candidate ────────────────────────────────────────
def _same_day_expiries(chain: dict, today: date, max_dte: int) -> list[str]:
    out = []
    for e in (chain.get("chains") or {}):
        try:
            d = date.fromisoformat(str(e)[:10])
        except (TypeError, ValueError):
            continue
        if 0 <= (d - today).days <= max_dte:
            out.append(e)
    return sorted(out)


def _liquidity(row: dict, cfg: dict) -> tuple[bool, list]:
    lq = cfg["liquidity"]
    bid, ask = _num(row.get("bid")) or 0.0, _num(row.get("ask")) or 0.0
    why = []
    if bid < float(lq["min_bid"]):
        why.append(f"no real bid ({bid:.2f})")
    mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else 0.0
    spread_pct = ((ask - bid) / mid * 100.0) if mid > 0 else 999.0
    if spread_pct > float(lq["max_spread_pct"]):
        why.append(f"spread {spread_pct:.0f}% of mid")
    oi = _num(row.get("openInterest")) or 0
    if oi < float(lq["min_oi"]):
        why.append(f"open interest {oi:.0f}")
    return (not why), why


def _catalyst_refusal(sym: str, cfg: dict) -> str | None:
    """A takeover is the one spike that never comes back."""
    if not _CATALYST_FN:
        return None
    try:
        cat = _CATALYST_FN(sym) or {}
    except Exception:  # noqa: BLE001
        return None
    kind = str(cat.get("kind") or "").upper()
    if kind in {str(k).upper() for k in cfg["events"]["refuse_kinds"]}:
        return f"{kind.lower()} headline — the one move that does not come back"
    return None


def analyze(cand: dict, chain: dict, bars: list, cfg: dict | None = None,
            now: datetime | None = None, profile: list | None = None) -> dict | None:
    """Every same-day call above the current price on one name, priced
    against the measured record."""
    cfg = cfg or config()
    st = cfg["select"]
    n = now or _now()
    today = n.date()
    exps = _same_day_expiries(chain, today, int(st["max_dte"]))
    if not exps:
        return None
    sym = cand["symbol"]
    table = _table_for(sym, bars)
    elapsed = elapsed_fraction(n)
    spot = _num((chain.get("underlying") or {}).get("last")) or cand["last"]
    rows = []
    for exp in exps:
        calls = ((chain.get("chains") or {}).get(exp) or {}).get("calls") or []
        for r in calls:
            k = _num(r.get("strike"))
            bid = _num(r.get("bid"))
            if not k or k <= spot or bid is None:
                continue
            a = sev.assess(spot=spot, strike=k, prev_close=cand["prev_close"], bars=bars,
                           credit=bid, elapsed_frac=elapsed, profile=profile, table=table)
            if not a:
                continue
            if a["beyond_sigma"] < float(st["min_beyond_sigma"]):
                continue
            ok, why = _liquidity(r, cfg)
            ask = _num(r.get("ask")) or 0.0
            mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else bid
            ev = a["evidence"]
            rows.append({
                "symbol": sym, "expiration": str(exp)[:10], "strike": k,
                "spot": spot, "prev_close": cand["prev_close"],
                "move_pct": a["move_pct"], "strike_pct": a["strike_pct"],
                "move_sigma": a["move_sigma"], "strike_sigma": a["strike_sigma"],
                "beyond_sigma": a["beyond_sigma"],
                "sigma_daily": a["sigma_daily"], "sigma_annual": a["sigma_annual"],
                "credit": bid, "ask": ask, "mid": mid,
                "spread_pct": (((ask - bid) / mid * 100.0) if mid > 0 else None),
                "oi": _num(r.get("openInterest")), "volume": _num(r.get("volume")),
                "iv": _num(r.get("iv")), "delta": _num(r.get("delta")),
                "p_close_above": ev["p_close_above"], "p_touch": ev["p_touch"],
                "p_finishes_at_high": ev["p_finishes_at_high"],
                "grade": ev["grade"], "n_own": ev["n_own"], "weight_own": ev["weight_own"],
                "clamped": ev["clamped"],
                "settles": a["settlement"]["dollars"],
                "settles_full_session": a["settlement"]["full_session_dollars"],
                "session_scale": a["settlement"]["session_scale"],
                "session_basis": a["settlement"]["session_basis"],
                "elapsed": elapsed,
                "edge_per_share": a["edge_per_share"],
                "edge_per_contract": a["edge_per_contract"],
                "liquidity_ok": ok, "liquidity_why": why,
                "sector": cand.get("sector"),
                "dollar_volume": cand.get("dollar_volume"),
            })
    if not rows:
        return None
    return {"symbol": sym, "spot": spot, "move_sigma": cand["move_sigma"],
            "change_pct": cand["change_pct"], "sigma_annual": cand["sigma_annual"],
            "n_strikes": len(rows), "rows": rows,
            "table_grade": (rows[0]["grade"] if rows else None)}


def qualify(rows: list, cfg: dict | None = None) -> tuple[list, list]:
    """Split the priced strikes into the ones worth listing and the ones
    refused, each with the reason."""
    cfg = cfg or config()
    ed, lq = cfg["edge"], cfg["liquidity"]
    keep, refused = [], []
    for r in rows:
        why = []
        if not r["liquidity_ok"]:
            why += r["liquidity_why"]
        if (r["credit"] or 0) < float(ed["min_credit"]):
            why.append(f"credit {r['credit']:.2f} below the floor")
        if (r["dollar_volume"] or 0) < float(lq["min_underlying_dollar_volume"]):
            why.append("the underlying is too thin to manage")
        if r["edge_per_contract"] is None or r["edge_per_contract"] < float(ed["min_edge_per_contract"]):
            why.append(f"the measured settlement leaves only "
                       f"${(r['edge_per_contract'] or 0):.0f} a contract")
        if why:
            refused.append({**r, "why": why})
        else:
            keep.append(r)
    return keep, refused


# ── the scan ────────────────────────────────────────────────────────────────
def _scan(sc) -> None:
    cfg = config()
    st = cfg["select"]
    n = _now()
    cands, universe = stage1(cfg)
    profile = session_profile()
    today = n.date().isoformat()
    to_date = (n.date() + timedelta(days=int(st["max_dte"]))).isoformat()
    per_symbol, all_rows, refused_all = {}, [], []

    def one(c):
        sym = c["symbol"]
        stop = _catalyst_refusal(sym, cfg)
        if stop:
            return sym, None, [{"symbol": sym, "why": [stop], "gate": "event"}]
        try:
            chain = sc.get_option_chain(sym, expiration=today, to_date=to_date,
                                        strike_count=int(st["strike_count"]))
        except Exception as exc:  # noqa: BLE001
            return sym, None, [{"symbol": sym, "why": [f"chain unavailable: {exc}"],
                                "gate": "data"}]
        bars = _bars(sym)
        if not chain or not bars:
            return sym, None, [{"symbol": sym, "why": ["no chain or no bars"], "gate": "data"}]
        res = analyze(c, chain, bars, cfg, n, profile)
        if not res:
            return sym, None, [{"symbol": sym, "why": ["no same-day calls above the price"],
                                "gate": "select"}]
        keep, ref = qualify(res["rows"], cfg)
        res["rows"] = keep
        return sym, res, ref

    with ThreadPoolExecutor(max_workers=4) as ex:
        for sym, res, ref in ex.map(one, cands):
            refused_all.extend(ref or [])
            if not res:
                continue
            res["rows"].sort(key=lambda r: -(r["edge_per_contract"] or -1e9))
            cap = int(st["max_rows_per_symbol"])
            res["rows"] = res["rows"][:cap] if cap > 0 else res["rows"]
            per_symbol[sym] = res
            all_rows.extend(res["rows"])

    all_rows.sort(key=lambda r: -(r["edge_per_contract"] or -1e9))
    for i, r in enumerate(all_rows, 1):
        r["rank"] = i
    with _LOCK:
        _STATE["rows"] = all_rows
        _STATE["by_symbol"] = per_symbol
        _STATE["candidates"] = cands
        _STATE["refused"] = refused_all
        _STATE["universe"] = universe
        _STATE["scanned"] = len(cands)
        _STATE["as_of"] = n.replace(microsecond=0).isoformat()
        _STATE["error"] = None


def _loop() -> None:
    cfg = config()
    cycle = float((cfg["scan"] or {}).get("cycle_seconds") or CYCLE_SECS)
    try:
        while True:
            with _LOCK:
                idle = time.time() - _STATE["last_req"] > WORKER_IDLE_SECS
            if idle or not _market_open():
                break
            sc = _SCHWAB() if _SCHWAB else None
            if sc is None:
                with _LOCK:
                    _STATE["error"] = "the broker connection is not available"
                break
            t0 = time.time()
            try:
                _scan(sc)
            except Exception as exc:  # noqa: BLE001
                with _LOCK:
                    _STATE["error"] = str(exc)
            time.sleep(max(15.0, cycle - (time.time() - t0)))
    finally:
        with _LOCK:
            _STATE["scanning"] = False
            _STATE["thread"] = None


def snapshot(top_n: int | None = None) -> dict:
    """The board. Starts the worker on first read and lets it die when the
    tab is closed, so a scanner this eager costs nothing when nobody is
    looking at it."""
    cfg = config()
    top = int(top_n or cfg["select"]["top_n"])
    with _LOCK:
        _STATE["last_req"] = time.time()
        open_now = _market_open()
        if open_now and not _STATE["scanning"]:
            _STATE["scanning"] = True
            t = threading.Thread(target=_loop, name="spike-scan", daemon=True)
            _STATE["thread"] = t
            t.start()
        rows = list(_STATE["rows"])[:top]
        refused = list(_STATE["refused"])
        cands = list(_STATE["candidates"])
        out = {
            "ok": True, "version": SPIKE_SCAN_VERSION,
            "evidence_version": sev.SPIKE_EVIDENCE_VERSION,
            "as_of": _STATE["as_of"], "scanning": _STATE["scanning"],
            "market_open": open_now, "error": _STATE["error"],
            "universe": _STATE["universe"], "scanned": _STATE["scanned"],
            "rows": rows, "n_rows": len(_STATE["rows"]),
            "candidates": [{k: c[k] for k in ("symbol", "move_sigma", "change_pct",
                                              "sigma_annual", "last")} for c in cands],
            "refused": refused[:40],
            "elapsed": elapsed_fraction(),
            "session_profile": ("MEASURED from %d sessions of minute bars"
                                % _STATE.get("profile_days_used", 0)) if _STATE.get("profile")
                               else "MODELED (clock)",
            "prior": {"n_sessions": sev.universe_prior().get("n_sessions"),
                      "n_names": sev.universe_prior().get("n_names")},
        }
    if not out["rows"]:
        out["no_trade"] = True
        out["no_trade_reason"] = (
            "The market is closed — this board only means anything while a move is live."
            if not open_now else
            "Nothing has run far enough today to be worth selling into. Most sessions "
            "are like this; a board that always has something on it is not measuring "
            "anything." if not cands else
            "Names have run, but no same-day call on them clears the floors — see what "
            "was refused and why.")
    else:
        out["no_trade"] = False
    return out


def detail(symbol: str) -> dict:
    sym = (symbol or "").upper()
    with _LOCK:
        _STATE["last_req"] = time.time()
        res = (_STATE["by_symbol"] or {}).get(sym)
        refused = [r for r in (_STATE["refused"] or []) if r.get("symbol") == sym]
    if not res:
        return {"ok": False, "symbol": sym,
                "error": "not on today's board — it has not run far enough, or the "
                         "scan has not reached it"}
    return {"ok": True, **res, "refused": refused}


def status() -> dict:
    with _LOCK:
        return {"version": SPIKE_SCAN_VERSION, "as_of": _STATE["as_of"],
                "scanning": _STATE["scanning"], "error": _STATE["error"],
                "universe": _STATE["universe"], "scanned": _STATE["scanned"],
                "rows": len(_STATE["rows"]),
                "profile_days": _STATE.get("profile_days_used", 0)}
