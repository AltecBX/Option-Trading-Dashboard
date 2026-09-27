"""daily_edge.py — DAILY EDGE: when to sell the next-day call or put on the
six ETFs that list an expiry every weekday (SMH, QQQ, SPY, IWM, XLF, GLD).

What was measured (DAILY_EDGE.md has the tables): every hour of the last two
years on all six, a 20-delta-equivalent strike (0.84 of the ETF's own daily
sigma, scaled to the time left to the next close) sold for the NEXT session's
expiry, graded on that session's close. The only patterns kept are the ones
that held in BOTH years, and on most of the six:

  CALL ZONE   up between 0.15 and 0.90 of its own daily sigma from the prior
              close, between 10:30 and 3:30 ET. The next-day call finished
              through its strike 10-20% of the time versus 18-23% selling a
              call at every close. Holds on SMH, QQQ, SPY, IWM, XLF.
  GLD         the same up-zone is a PUT day, not a call day: gold's quiet up
              days kept going (calls broke 23%) while the put broke 12-13%.
  NO CALL     down more than 0.90 sigma: the next day tends to bounce. Calls
              sold into it broke 20-34% of the time versus 16-20% on an
              average day, on SMH, QQQ, SPY, IWM and XLF. Not on GLD.
  SMH CLOSE   (ten years of daily bars) the second down day in a row, or an
              up Friday, at the close: sell the put, not the call.

Everything else — a small red day, a flat day, an up day past 0.90 sigma —
had no edge that survived both years, and the card says so instead of
inventing one.

The strike is the one the evidence was measured on (0.84 sigma out over the
time left), taken from the live chain for the next session's expiry, with
its bid. The historical average loss past that strike, per share, is priced
from the same record, so the row can say whether the bid on the screen pays
for it. The first signal per ticker per day is written to a forward log and
graded after its expiry closes: the paper-trade record builds itself.
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import market_calendar as _cal

DAILY_EDGE_VERSION = "daily-edge-1.0.0"

UNIVERSE = ("SMH", "QQQ", "SPY", "IWM", "XLF", "GLD")
K_20D = 0.84                 # 20-delta equivalent, in sigma
FAIR_UNIT = 0.1119           # E[max(Z-0.84,0)] for a standard normal
ZONE_LO, ZONE_HI = 0.15, 0.90
NO_CALL_Z = -0.90
WINDOW_OPEN = dtime(10, 30)
WINDOW_CLOSE = dtime(15, 30)
CLOSE_RULES_FROM = dtime(15, 45)

# Measured Sep 2024 - Sep 2026 on hourly bars (last year, prior year). The
# side each ticker sells in its up-zone, the breach rate of that sale on the
# signal days, the breach rate of selling the same side at every close, and
# the average loss past the strike as a multiple of a random-walk fair value
# (what turns into dollars per share on the row).
EVIDENCE = {
    "SMH": {"side": "call", "days": (122, 122), "breach": (13.9, 17.2), "base": (22.2, 20.7), "loss_x": (0.64, 0.57)},
    "QQQ": {"side": "call", "days": (116, 133), "breach": (19.8, 11.3), "base": (23.4, 20.7), "loss_x": (0.87, 0.30)},
    "SPY": {"side": "call", "days": (135, 142), "breach": (13.3, 12.0), "base": (20.2, 18.2), "loss_x": (0.61, 0.37)},
    "IWM": {"side": "call", "days": (130, 133), "breach": (12.3, 17.3), "base": (18.6, 19.0), "loss_x": (0.55, 0.99)},
    "XLF": {"side": "call", "days": (147, 133), "breach": (12.9, 9.8), "base": (19.4, 19.4), "loss_x": (0.74, 0.85)},
    "GLD": {"side": "put", "days": (118, 103), "breach": (12.7, 11.7), "base": (17.0, 16.0), "loss_x": (0.44, 0.54)},
}
# SMH close rules. Hourly record (last year, prior year) and the ten-year
# daily record behind them.
CLOSE_RULES = {
    "down2": {"label": "2nd down day in a row, at the close", "side": "put",
              "days": (37, 47), "breach": (10.8, 8.5), "loss_x": (0.59, 0.76),
              "ten_year": "10y: put broke 15.8%, call broke 29.3% (467 days)"},
    "upfri": {"label": "up Friday, at the close (Monday expiry)", "side": "put",
              "days": (6, 9), "breach": (16.7, 0.0), "loss_x": (0.52, 0.0),
              "ten_year": "10y: put broke 9.9%, call broke 23.2% (233 Fridays)"},
}
NO_CALL_EVIDENCE = ("Calls sold on days down more than 0.9 sigma broke 20-34% of the time versus 16-20% "
                    "on an average day (SMH, QQQ, SPY, IWM, XLF; both years). GLD showed no such effect.")
NO_CALL_SYMBOLS = ("SMH", "QQQ", "SPY", "IWM", "XLF")

DEFAULTS = {
    "_doc": "Daily edge (daily_edge.py, DAILY_EDGE.md). Next-day call/put signals on the six daily-expiry ETFs.",
    "universe": list(UNIVERSE),
    "zone": {"lo_sigma": ZONE_LO, "hi_sigma": ZONE_HI, "no_call_sigma": NO_CALL_Z},
    "liquidity": {"min_bid": 0.05, "max_spread_pct": 30.0},
    "scan": {"cycle_seconds": 60, "background": True},
    "alerts": {"enabled": True, "priority": 0},
}

_SCHWAB = None
_QUOTES_FN = None
_BARS_FN = None
_NOW_FN = None
_NOTIFY_FN = None
_DATA_DIR: Path | None = None
_BASE_URL = "https://dashboard.jerrytrade.com"

_LOCK = threading.RLock()
_STATE: dict = {"rows": [], "as_of": None, "error": None, "scanning": False, "thread": None, "ticks": 0}
_BARS: dict = {}        # symbol -> (day, profile)
_FIRED: dict = {}       # "YYYY-MM-DD|SYM" -> fired record (persisted)
_GRADES: dict = {}      # "YYYY-MM-DD|SYM" -> grade, kept once the expiry has closed (persisted)


def configure(schwab_getter=None, quotes_fn=None, bars_fn=None, now_fn=None,
              notify_fn=None, data_dir=None, base_url=None) -> None:
    global _SCHWAB, _QUOTES_FN, _BARS_FN, _NOW_FN, _NOTIFY_FN, _DATA_DIR, _BASE_URL
    _SCHWAB, _QUOTES_FN, _BARS_FN = schwab_getter, quotes_fn, bars_fn
    _NOW_FN, _NOTIFY_FN = now_fn, notify_fn
    _DATA_DIR = Path(data_dir) if data_dir else None
    if base_url:
        _BASE_URL = str(base_url).rstrip("/")
    with _LOCK:
        _BARS.clear()
        _FIRED.clear()
        _FIRED.update(_load_json("daily_edge_fired.json", {}))
        _GRADES.clear()
        _GRADES.update(_load_json("daily_edge_grades.json", {}))


def config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        import premium_edge as pe
        full = json.loads((Path(__file__).resolve().parent / "thresholds.json").read_text())
        cfg = pe._deep_merge(cfg, full.get("daily_edge") or {})  # noqa: SLF001
        dd = getattr(pe, "_DATA_DIR", None)
        if dd:
            p = Path(dd) / "thresholds.json"
            if p.exists():
                cfg = pe._deep_merge(cfg, (json.loads(p.read_text()).get("daily_edge") or {}))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass
    return cfg


# ── small helpers ────────────────────────────────────────────────────────────
def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    if _NOW_FN:
        try:
            n = _NOW_FN()
            if isinstance(n, datetime):
                return n
        except Exception:  # noqa: BLE001
            pass
    return datetime.now().astimezone()


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


def _avg(pair) -> float:
    vals = [v for v in pair if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


# ── the ETF's own yardstick, from daily bars ─────────────────────────────────
def profile_from_bars(bars: list, today: date) -> dict | None:
    """Sigma (stdev of the last 20 close-to-close returns), the prior close
    and whether the prior session was a down day — all from sessions BEFORE
    today, so a partial bar for today never leaks in."""
    rows = []
    for b in bars or []:
        d = str(b.get("date") or "")[:10]
        c = _num(b.get("close"))
        if d and c and c > 0 and d < today.isoformat():
            rows.append((d, c))
    rows.sort()
    if len(rows) < 22:
        return None
    closes = [c for _, c in rows]
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    last20 = rets[-20:]
    mu = sum(last20) / 20.0
    sd = math.sqrt(sum((r - mu) ** 2 for r in last20) / 19.0)
    return {"sigma": sd, "prev_close": closes[-1], "prev_date": rows[-1][0],
            "prev_day_ret": rets[-1], "closes": rows[-60:]}


def _profile(sym: str, today: date) -> dict | None:
    with _LOCK:
        hit = _BARS.get(sym)
        if hit and hit[0] == today.isoformat():
            return hit[1]
    if not _BARS_FN:
        return None
    try:
        bars = _BARS_FN(sym)
    except Exception:  # noqa: BLE001
        bars = None
    prof = profile_from_bars(bars or [], today)
    if prof:
        with _LOCK:
            _BARS[sym] = (today.isoformat(), prof)
    return prof


# ── the rules ────────────────────────────────────────────────────────────────
def session_fraction_left(now: datetime) -> float:
    span = _cal.session_span(now.date())
    if not span:
        return 0.0
    o, c = span
    t = now.hour * 60 + now.minute
    o_m, c_m = o.hour * 60 + o.minute, c.hour * 60 + c.minute
    if t <= o_m:
        return 1.0
    return max(0.0, (c_m - t) / float(c_m - o_m))


def model_strike(spot: float, sigma: float, side: str, frac_left: float) -> float:
    """The strike the evidence was measured on: 0.84 sigma out over what is
    left of today plus the whole next session."""
    s = sigma * math.sqrt(frac_left + 1.0)
    return spot * (1 + K_20D * s) if side == "call" else spot * (1 - K_20D * s)


def hist_loss_per_share(spot: float, sigma: float, frac_left: float, loss_x: float) -> float:
    """Average loss past that strike on the signal days, per share: the
    measured multiple of a random-walk fair value, in dollars today."""
    return loss_x * FAIR_UNIT * sigma * math.sqrt(frac_left + 1.0) * spot


def classify(sym: str, z: float, now: datetime, prof: dict, cfg: dict | None = None) -> dict:
    """What the record says about this moment. Returns
    {state, side, rule, reason}. state is one of SELL, WAIT, NO_CALL,
    NO_EDGE, CLOSED."""
    cfg = cfg or DEFAULTS
    zc = cfg.get("zone") or DEFAULTS["zone"]
    lo, hi, nc = float(zc["lo_sigma"]), float(zc["hi_sigma"]), float(zc["no_call_sigma"])
    ev = EVIDENCE.get(sym) or EVIDENCE["SMH"]
    zone_side = ev["side"]
    d = now.date()
    span = _cal.session_span(d)
    if not span:
        return {"state": "CLOSED", "side": None, "rule": None, "reason": "The market is shut today."}
    o, c = span
    t = now.time()
    if t < o or t >= c:
        return {"state": "CLOSED", "side": None, "rule": None,
                "reason": "Outside market hours." if t < o else "The market has closed for the day."}
    win_close = min(WINDOW_CLOSE, (datetime.combine(d, c) - timedelta(minutes=30)).time())
    in_zone = lo <= z <= hi
    # SMH close rules, the last 15 minutes
    if sym == "SMH" and t >= min(CLOSE_RULES_FROM, (datetime.combine(d, c) - timedelta(minutes=15)).time()):
        if z < 0 and (prof.get("prev_day_ret") or 0) < 0:
            return {"state": "SELL", "side": "put", "rule": "down2",
                    "reason": "Second down day in a row into the close: sell the put, not the call."}
        if d.weekday() == 4 and z > 0:
            return {"state": "SELL", "side": "put", "rule": "upfri",
                    "reason": "Up on a Friday into the close: sell Monday's put."}
    if z <= nc and sym in NO_CALL_SYMBOLS:
        return {"state": "NO_CALL", "side": None, "rule": "no_call",
                "reason": "Down more than 0.9 sigma. The next day tends to bounce: no calls."}
    if in_zone and t < WINDOW_OPEN:
        return {"state": "WAIT", "side": zone_side, "rule": "zone",
                "reason": "In the zone, but the record starts at 10:30. Wait for it to hold."}
    if in_zone and WINDOW_OPEN <= t <= win_close:
        what = "the call" if zone_side == "call" else "the put (gold's up days keep going)"
        return {"state": "SELL", "side": zone_side, "rule": "zone",
                "reason": f"Quietly up, inside the zone: sell {what} for the next session."}
    if in_zone:
        return {"state": "NO_EDGE", "side": None, "rule": None,
                "reason": "The zone's window closed at 3:30."}
    if z > hi:
        return {"state": "NO_EDGE", "side": None, "rule": None,
                "reason": "Up past 0.9 sigma: no edge either way in the record."}
    return {"state": "NO_EDGE", "side": None, "rule": None,
            "reason": "Inside the no-edge band. Skip."}


# ── the contract ─────────────────────────────────────────────────────────────
def pick_contract(chain: dict | None, expiry: date, side: str, target: float, cfg: dict) -> dict | None:
    """The listed strike nearest the measured strike, rounded AWAY from the
    money, with a real bid and a fillable spread."""
    if not chain:
        return None
    exp = (chain.get("chains") or {}).get(expiry.isoformat()) or {}
    rows = exp.get("calls" if side == "call" else "puts") or []
    lq = cfg.get("liquidity") or DEFAULTS["liquidity"]
    best = None
    for o in rows:
        k = _num(o.get("strike"))
        bid, ask = _num(o.get("bid")) or 0.0, _num(o.get("ask")) or 0.0
        if k is None:
            continue
        if side == "call" and k < target - 1e-9:
            continue
        if side == "put" and k > target + 1e-9:
            continue
        if bid < float(lq["min_bid"]) or ask <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2.0
        if mid <= 0 or (ask - bid) / mid * 100.0 > float(lq["max_spread_pct"]):
            continue
        dist = abs(k - target)
        if best is None or dist < best[0]:
            best = (dist, {"strike": k, "bid": round(bid, 2), "ask": round(ask, 2), "mid": round(mid, 2),
                           "delta": _num(o.get("delta")), "oi": _num(o.get("openInterest"))})
    return best[1] if best else None


# ── one pass ─────────────────────────────────────────────────────────────────
def _quotes(syms: list) -> dict:
    if not _QUOTES_FN:
        return {}
    try:
        got = _QUOTES_FN(syms) or {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for s, q in got.items():
        last = _num((q or {}).get("last"))
        pc = _num((q or {}).get("close_prev"))
        if last and last > 0:
            out[str(s).upper()] = {"last": last, "prev_close": pc if pc and pc > 0 else None}
    return out


def build_row(sym: str, q: dict | None, prof: dict | None, now: datetime, cfg: dict, sc=None) -> dict:
    ev = EVIDENCE.get(sym) or EVIDENCE["SMH"]
    zc = cfg.get("zone") or DEFAULTS["zone"]
    row = {"symbol": sym, "zone_side": ev["side"], "evidence": ev, "state": "NO_DATA",
           "reason": "No quote or history yet.", "side": None, "rule": None}
    if not prof or not q:
        return row
    sigma = prof["sigma"]
    pc = q.get("prev_close") or prof["prev_close"]
    last = q["last"]
    move = last / pc - 1.0
    z = move / sigma if sigma > 0 else 0.0
    row.update({
        "last": round(last, 2), "prev_close": round(pc, 2), "move_pct": round(move * 100, 2),
        "sigma_pct": round(sigma * 100, 2), "z": round(z, 2),
        "zone_lo_pct": round(float(zc["lo_sigma"]) * sigma * 100, 2),
        "zone_hi_pct": round(float(zc["hi_sigma"]) * sigma * 100, 2),
        "no_call_pct": round(float(zc["no_call_sigma"]) * sigma * 100, 2),
        "prev_day_down": (prof.get("prev_day_ret") or 0) < 0,
    })
    c = classify(sym, z, now, prof, cfg)
    row.update(c)
    if c["state"] in ("SELL", "WAIT") and c["side"]:
        frac = session_fraction_left(now)
        expiry = _cal.next_session(now.date())
        target = model_strike(last, sigma, c["side"], frac)
        rule_ev = CLOSE_RULES.get(c["rule"]) if c["rule"] in CLOSE_RULES else ev
        loss = hist_loss_per_share(last, sigma, frac, _avg(rule_ev["loss_x"]))
        row.update({"expiry": expiry.isoformat(), "model_strike": round(target, 2),
                    "hist_loss": round(loss, 2), "rule_evidence": rule_ev,
                    "note": ("Covered only with 100 shares; otherwise sell it as a spread."
                             if c["side"] == "call" else "Sell as a spread unless you want the shares.")})
        contract = None
        if sc is not None:
            try:
                chain = sc.get_option_chain(sym, expiration=expiry.isoformat(), strike_count=40)
                contract = pick_contract(chain, expiry, c["side"], target, cfg)
            except Exception:  # noqa: BLE001
                contract = None
        row["contract"] = contract
        if contract:
            row["edge"] = round(contract["bid"] - loss, 2)
            row["pays"] = contract["bid"] > loss
    return row


def _fire(row: dict, now: datetime, cfg: dict) -> None:
    """First SELL per ticker per day: remembered, logged, pushed. Only once
    a real contract with a bid is on the screen: a signal nobody could fill
    is not a trade, and it must not take the day's slot or the record."""
    if row.get("state") != "SELL":
        return
    if not (row.get("contract") or {}).get("bid"):
        return
    key = f"{now.date().isoformat()}|{row['symbol']}"
    with _LOCK:
        if key in _FIRED:
            row["fired"] = _FIRED[key]
            return
        ct = row.get("contract") or {}
        rec = {"date": now.date().isoformat(), "symbol": row["symbol"], "side": row["side"],
               "rule": row["rule"], "at": now.replace(microsecond=0).isoformat(),
               "spot": row["last"], "move_pct": row["move_pct"], "z": row["z"],
               "expiry": row.get("expiry"), "strike": ct.get("strike") or row.get("model_strike"),
               "bid": ct.get("bid"), "hist_loss": row.get("hist_loss"), "pushed": None}
        _FIRED[key] = rec
        for k in [k for k in _FIRED if k[:10] < (now.date() - timedelta(days=10)).isoformat()]:
            _FIRED.pop(k, None)
        _save_json("daily_edge_fired.json", _FIRED)
    p = _path("daily_edge_log.jsonl")
    if p:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:  # noqa: BLE001
            pass
    al = cfg.get("alerts") or {}
    if al.get("enabled", True) and _NOTIFY_FN:
        side = rec["side"].upper()
        bid = f" bid ${rec['bid']:.2f}" if rec.get("bid") is not None else ""
        title = f"{rec['symbol']} · sell the {side}"
        body = (f"{rec['symbol']} {row['move_pct']:+.2f}% ({row['z']:+.2f}σ). {row['reason']}\n"
                f"{rec['expiry']} {rec['strike']:g} {rec['side']}{bid}; "
                f"record's avg loss past it ${rec['hist_loss']:.2f}/sh.\n{_BASE_URL}/?tab=trade")
        try:
            res = _NOTIFY_FN(title, body, int(al.get("priority", 0)))
            if (res or {}).get("ok") if isinstance(res, dict) else res:
                rec["pushed"] = rec["at"]
                with _LOCK:
                    _save_json("daily_edge_fired.json", _FIRED)
        except Exception:  # noqa: BLE001
            pass
    row["fired"] = rec


def scan(now: datetime | None = None, sc=None) -> list:
    cfg = config()
    n = now or _now()
    syms = [s for s in (cfg.get("universe") or UNIVERSE) if s in EVIDENCE]
    qs = _quotes(syms)
    if sc is None and _SCHWAB:
        try:
            sc = _SCHWAB()
        except Exception:  # noqa: BLE001
            sc = None
    rows = []
    for s in syms:
        r = build_row(s, qs.get(s), _profile(s, n.date()), n, cfg, sc)
        _fire(r, n, cfg)
        if "fired" not in r:
            r["fired"] = _FIRED.get(f"{n.date().isoformat()}|{s}")
        rows.append(r)
    with _LOCK:
        _STATE.update({"rows": rows, "as_of": n.replace(microsecond=0).isoformat(),
                       "error": None if qs else "no live quotes", "ticks": _STATE["ticks"] + 1})
    return rows


# ── the forward record: every first signal, graded after its expiry ─────────
def grade(rec: dict, closes: dict) -> dict | None:
    """closes: {YYYY-MM-DD: close}. None until the expiry has closed."""
    c = _num(closes.get(rec.get("expiry") or ""))
    k = _num(rec.get("strike"))
    if c is None or k is None:
        return None
    loss = max(c - k, 0.0) if rec["side"] == "call" else max(k - c, 0.0)
    bid = _num(rec.get("bid"))
    return {"close": c, "breached": loss > 0, "loss": round(loss, 2),
            "pnl": round(bid - loss, 2) if bid is not None else None}


def forward(days: int = 120) -> dict:
    p = _path("daily_edge_log.jsonl")
    recs = []
    if p and p.exists():
        for line in p.read_text().splitlines():
            try:
                recs.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    cutoff = (_now().date() - timedelta(days=days)).isoformat()
    recs = [r for r in recs if (r.get("date") or "") >= cutoff]
    closes: dict = {}
    for s in {r["symbol"] for r in recs}:
        with _LOCK:
            hit = _BARS.get(s)
        for d, c in ((hit[1].get("closes") if hit else None) or []):
            closes.setdefault(s, {})[d] = c
    graded, open_, new = [], [], False
    for r in recs:
        key = f"{r.get('date')}|{r.get('symbol')}"
        with _LOCK:
            g = _GRADES.get(key)
        if g is None:
            g = grade(r, closes.get(r["symbol"], {}))
            if g:
                with _LOCK:
                    _GRADES[key] = g
                new = True
        (graded if g else open_).append({**r, **(g or {})})
    if new:
        with _LOCK:
            _save_json("daily_edge_grades.json", _GRADES)
    n = len(graded)
    pnl = [g["pnl"] for g in graded if g.get("pnl") is not None]
    return {"n": len(recs), "graded": n, "open": len(open_),
            "breach_pct": round(100.0 * sum(g["breached"] for g in graded) / n, 1) if n else None,
            "pnl_per_share": round(sum(pnl), 2) if pnl else None,
            "rows": sorted(graded + open_, key=lambda r: r.get("at") or "", reverse=True)[:60]}


# ── the loop ─────────────────────────────────────────────────────────────────
def _loop() -> None:
    try:
        while True:
            cfg = config()
            n = _now()
            span = _cal.session_span(n.date())
            live = bool(span) and span[0] <= n.time() < span[1]
            if live:
                try:
                    scan(n)
                except Exception as exc:  # noqa: BLE001
                    with _LOCK:
                        _STATE["error"] = str(exc)
            time.sleep(max(20.0, float((cfg.get("scan") or {}).get("cycle_seconds") or 60)) if live else 60.0)
    finally:
        with _LOCK:
            _STATE["scanning"] = False
            _STATE["thread"] = None


def start_scheduler() -> None:
    with _LOCK:
        if _STATE["scanning"]:
            return
        _STATE["scanning"] = True
        t = threading.Thread(target=_loop, name="daily-edge", daemon=True)
        _STATE["thread"] = t
        t.start()


def snapshot(refresh: bool = True) -> dict:
    n = _now()
    span = _cal.session_span(n.date())
    open_now = bool(span) and span[0] <= n.time() < span[1]
    if refresh or not _STATE["rows"]:
        try:
            scan(n)
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _STATE["error"] = str(exc)
    with _LOCK:
        st = dict(_STATE)
    rows = st["rows"]
    return {"ok": True, "version": DAILY_EDGE_VERSION, "as_of": st["as_of"], "market_open": open_now,
            "error": st["error"], "rows": rows,
            "n_sell": sum(1 for r in rows if r.get("state") == "SELL"),
            "window": {"open": WINDOW_OPEN.strftime("%H:%M"), "close": WINDOW_CLOSE.strftime("%H:%M")},
            "no_call_evidence": NO_CALL_EVIDENCE, "close_rules": CLOSE_RULES,
            "forward": {k: v for k, v in forward().items() if k != "rows"},
            "alerts": {"configured": bool(_NOTIFY_FN),
                       "enabled": bool((config().get("alerts") or {}).get("enabled", True))}}


def status() -> dict:
    with _LOCK:
        return {"version": DAILY_EDGE_VERSION, "scanning": _STATE["scanning"], "as_of": _STATE["as_of"],
                "ticks": _STATE["ticks"], "error": _STATE["error"], "fired_today": sum(
                    1 for k in _FIRED if k.startswith(_now().date().isoformat()))}
