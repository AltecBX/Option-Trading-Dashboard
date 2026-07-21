"""earnings_scan.py — Earnings Opportunities scanner (v1).

Market-Chameleon-style "Earnings Today's Opportunities" workflow built on
JerryTrade's own providers — NOT a calendar. Discovers watchlist names
reporting soon (or that just reported), measures the options market's
implied earnings move against each name's own historical earnings moves,
reads the tape (gap, VWAP, opening range) for confirmation, scores every
name 0–100, and emits an explainable trade plan or an explicit NO TRADE.

Providers (all already in the stack):
  • watchlist_table board — universe + next_earnings/days_to_earnings,
    market cap, sector/industry, avg volume (cached, no extra fetching)
  • Schwab — quotes (extended-session aware), daily bars, minute bars
    (via intraday.vwap_series / split_premarket), option chains w/ greeks
  • yfinance fallback — daily bars, chains (BS greeks), earnings dates
  • ivrank board — HV-rank proxy for IV rank when available

Honesty rules: no consensus estimates exist for free → EPS surprise is out
of scope. "Actual vs expected move" uses the implied move THIS module
recorded before the print (persisted per symbol+date); until that history
accumulates, the comparison falls back to the name's historical average
move and says so. Anything unavailable renders as None → "—" in the UI,
never an estimate. If every provider is down, clearly-labeled seeded demo
rows keep the UI testable (board.demo = True).

All classification/scoring lives in pure functions on plain dicts so the
test suite exercises them without any network.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False

# Wired by options_dashboard at import time (same pattern as intraday.py).
_SCHWAB_GETTER = None
_WLBOARD_GETTER = None
_CHAIN_LOADER = None
_DATA_DIR: Path | None = None


def configure(schwab_getter, wlboard_getter, chain_loader, data_dir) -> None:
    global _SCHWAB_GETTER, _WLBOARD_GETTER, _CHAIN_LOADER, _DATA_DIR
    _SCHWAB_GETTER = schwab_getter
    _WLBOARD_GETTER = wlboard_getter
    _CHAIN_LOADER = chain_loader
    _DATA_DIR = Path(data_dir) if data_dir else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _et_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()


# ═══════════════════════════════════════════════════════════════════════════
#  Pure calculations (unit-tested, no I/O)
# ═══════════════════════════════════════════════════════════════════════════

def implied_move(spot: float, atm_call_mid: float | None, atm_put_mid: float | None) -> dict | None:
    """Expected move = ATM straddle mid. Returns pct, dollars, upper, lower."""
    if not spot or not atm_call_mid or not atm_put_mid:
        return None
    if atm_call_mid <= 0 or atm_put_mid <= 0:
        return None
    dollars = atm_call_mid + atm_put_mid
    return {
        "pct": round(dollars / spot * 100, 2),
        "dollars": round(dollars, 2),
        "upper": round(spot + dollars, 2),
        "lower": round(spot - dollars, 2),
    }


def hist_earnings_stats(moves: list[float]) -> dict | None:
    """moves = signed % reactions (close after vs close before). Needs >=3."""
    clean = [m for m in moves if m is not None and abs(m) < 80]
    if len(clean) < 3:
        return None
    mags = [abs(m) for m in clean]
    return {
        "n": len(clean),
        "avg_abs": round(sum(mags) / len(mags), 2),
        "med_abs": round(statistics.median(mags), 2),
        "last": round(clean[-1], 2),
        "beat_dir_pct": round(sum(1 for m in clean if m > 0) / len(clean) * 100, 0),
        "moves": [round(m, 2) for m in clean[-12:]],
    }


def implied_vs_hist(implied_pct: float | None, hist: dict | None) -> dict | None:
    """Rich/cheap read: implied move vs the name's own historical avg move."""
    if implied_pct is None or not hist or not hist.get("avg_abs"):
        return None
    ratio = implied_pct / hist["avg_abs"] if hist["avg_abs"] > 0 else None
    if ratio is None:
        return None
    label = "rich" if ratio >= 1.3 else "cheap" if ratio <= 0.75 else "fair"
    return {"ratio": round(ratio, 2), "label": label}


def actual_vs_expected(actual_pct: float | None, expected_pct: float | None,
                       hist_avg: float | None) -> dict | None:
    """Post-print: |actual| vs the pre-print implied move (preferred) or the
    historical average (fallback, labeled)."""
    if actual_pct is None:
        return None
    base, basis = None, None
    if expected_pct and expected_pct > 0:
        base, basis = expected_pct, "implied (recorded pre-print)"
    elif hist_avg and hist_avg > 0:
        base, basis = hist_avg, "historical average (no recorded implied)"
    if base is None:
        return None
    ratio = abs(actual_pct) / base
    label = "exceeded" if ratio >= 1.1 else "undershot" if ratio <= 0.6 else "in line"
    return {"ratio": round(ratio, 2), "label": label, "basis": basis,
            "actual": round(actual_pct, 2), "expected": round(base, 2)}


def gap_info(today_open: float | None, prev_close: float | None,
             day_low: float | None, day_high: float | None) -> dict | None:
    if not today_open or not prev_close:
        return None
    gap_pct = (today_open / prev_close - 1) * 100
    filled = None
    if day_low is not None and day_high is not None:
        filled = (day_low <= prev_close <= day_high) if abs(gap_pct) > 0.3 else None
    return {"gap_pct": round(gap_pct, 2), "fill_level": round(prev_close, 2),
            "filled": filled}


def vwap_status(last: float | None, vwap: float | None,
                opened_above: bool | None = None) -> dict | None:
    """above/below + reclaim/rejection when we know where the session opened."""
    if last is None or vwap is None or vwap <= 0:
        return None
    above = last >= vwap
    dist = (last / vwap - 1) * 100
    event = None
    if opened_above is not None:
        if above and not opened_above:
            event = "reclaim"
        elif not above and opened_above:
            event = "rejection"
    return {"above": above, "dist_pct": round(dist, 2), "event": event,
            "vwap": round(vwap, 2)}


def spread_quality(bid: float | None, ask: float | None) -> dict | None:
    if not bid or not ask or bid <= 0 or ask <= bid:
        return None
    mid = (bid + ask) / 2
    pct = (ask - bid) / mid * 100
    label = "good" if pct <= 5 else "ok" if pct <= 12 else "poor"
    return {"pct": round(pct, 1), "label": label}


# ── Setup / status / action classification ──────────────────────────────────

def classify(row: dict) -> dict:
    """Pure rules → setup, status, best_action, plan. `row` carries the
    measured fields; anything None simply disables the rules that need it."""
    days = row.get("days_to")                       # negative = already reported
    reported = days is not None and days <= 0 and row.get("reported_recently")
    chg = row.get("change_pct")
    ivh = row.get("iv_vs_hist") or {}
    ave = row.get("actual_vs_expected") or {}
    vw = row.get("vwap") or {}
    gap = row.get("gap") or {}
    hist = row.get("hist") or {}
    em = row.get("implied") or {}
    spread = row.get("spread") or {}
    weekly = row.get("weekly_options")
    optvol = row.get("options_volume") or 0
    relvol = row.get("rel_volume")
    price = row.get("price")

    setup, status, action = "no_trade", "no_trade", "no_trade"
    direction = "neutral"

    liquid_opts = optvol >= 500 and spread.get("label") in ("good", "ok")
    extended = False
    if price and em.get("upper") and em.get("lower"):
        extended = price > em["upper"] * 1.02 or price < em["lower"] * 0.98
    if vw.get("dist_pct") is not None and abs(vw["dist_pct"]) > 4:
        extended = True

    if reported:
        big = ave.get("label") == "exceeded" or (chg is not None and hist.get("avg_abs") and abs(chg) > hist["avg_abs"])
        if vw.get("event") == "reclaim":
            setup, direction = "vwap_reclaim", "long"
        elif vw.get("event") == "rejection":
            setup, direction = "vwap_rejection", "short"
        elif gap.get("gap_pct") is not None and gap["gap_pct"] >= 2 and vw.get("above"):
            setup, direction = "gap_and_go", "long"
        elif gap.get("gap_pct") is not None and gap["gap_pct"] >= 2 and vw.get("above") is False:
            setup, direction = "gap_fill", "short"
        elif gap.get("gap_pct") is not None and gap["gap_pct"] <= -2 and vw.get("above") is False:
            setup, direction = "post_earnings_continuation", "short"
        elif big and chg is not None:
            setup = "post_earnings_continuation" if (vw.get("above") is None or (chg > 0) == bool(vw.get("above"))) else "post_earnings_reversal"
            direction = "long" if chg > 0 else "short"
        elif ave.get("label") == "undershot" and chg is not None:
            setup, direction = "post_earnings_reversal", ("short" if chg > 0 else "long")
        else:
            setup = "no_trade"
        # status
        if setup == "no_trade":
            status, action = "no_trade", "no_trade"
        elif extended:
            status, action = "late_extended", "already_extended"
        elif vw.get("above") is None:
            status, action = "confirmation_pending", "watch"
        elif setup in ("vwap_reclaim", "gap_and_go", "post_earnings_continuation") and direction == "long" and vw.get("above"):
            status, action = "confirmed_long", "confirmed_entry"
        elif direction == "short" and vw.get("above") is False:
            status, action = "confirmed_short", "confirmed_entry"
        else:
            status, action = "confirmation_pending", "enter_on_confirmation"
    elif days is not None and 0 < days <= 8:
        mom5 = row.get("mom5_pct")
        if ivh.get("label") == "rich" and liquid_opts:
            setup = "high_premium"
            direction = "neutral"
            status, action = "potential", "sell_premium"
        elif ivh.get("label") == "cheap" and liquid_opts:
            setup, status, action = "cheap_implied", "potential", "watch"
        elif mom5 is not None and mom5 >= 5:
            setup, direction, status, action = "pre_earnings_momentum", "long", "watching", "watch"
        elif mom5 is not None and mom5 <= -5:
            setup, direction, status, action = "pre_earnings_fade", "short", "watching", "watch"
        elif days <= 1:
            setup, status, action = "no_trade", "watching", "watch"
        else:
            setup, status, action = "no_trade", "watching", "watch"
        # premium-selling flavor
        if setup == "high_premium" and hist.get("beat_dir_pct") is not None:
            if hist["beat_dir_pct"] >= 60:
                setup = "put_selling"
            elif hist["beat_dir_pct"] <= 40:
                setup = "covered_call"
    else:
        setup, status, action = "no_trade", "no_trade", "no_trade"

    if relvol is not None and relvol < 0.6 and reported:
        status, action = "no_trade", "avoid"
        setup = "no_trade"

    return {"setup": setup, "status": status, "action": action,
            "direction": direction, "extended": extended}


def build_plan(row: dict, cls: dict) -> dict | None:
    """Levels-based trade plan. Only for actionable classifications."""
    if cls["action"] in ("no_trade", "avoid"):
        return None
    price = row.get("price")
    vw = (row.get("vwap") or {}).get("vwap")
    orh, orl = row.get("or_high"), row.get("or_low")
    pdh, pdl = row.get("prev_high"), row.get("prev_low")
    em = row.get("implied") or {}
    hist = row.get("hist") or {}
    if not price:
        return None
    d = cls["direction"]
    long_ = d != "short"
    ref_stop = None
    entry = None
    if cls["setup"] in ("vwap_reclaim", "vwap_rejection") and vw:
        entry = vw * (1.001 if long_ else 0.999)
        ref_stop = vw * (0.99 if long_ else 1.01)
    elif orh and orl:
        entry = orh if long_ else orl
        ref_stop = orl if long_ else orh
    elif pdh and pdl:
        entry = pdh if long_ else pdl
        ref_stop = (pdh + pdl) / 2
    else:
        entry = price
        ref_stop = price * (0.97 if long_ else 1.03)
    t1 = em.get("upper") if long_ else em.get("lower")
    if not t1 or (long_ and t1 <= entry) or (not long_ and t1 >= entry):
        avg = hist.get("avg_abs") or 4.0
        t1 = entry * (1 + avg / 100) if long_ else entry * (1 - avg / 100)
    t2 = None
    if hist.get("avg_abs") and em.get("pct") and hist["avg_abs"] > em["pct"]:
        t2 = entry * (1 + hist["avg_abs"] / 100) if long_ else entry * (1 - hist["avg_abs"] / 100)
    risk = abs(entry - ref_stop)
    reward = abs(t1 - entry)
    rr = round(reward / risk, 1) if risk > 0 else None
    max_chase = entry + (t1 - entry) * 0.33 if long_ else entry - (entry - t1) * 0.33
    if cls["setup"] in ("high_premium", "put_selling", "covered_call"):
        return {"bias": "neutral", "entry": None, "confirmation": None,
                "max_chase": None, "invalidation": em.get("lower") if em else None,
                "target1": None, "target2": None, "rr": None,
                "holding": "through the print (defined-risk premium sale)",
                "note": "Premium sale: structure via the Analyze tab chain (spreads/strangles at the EM boundaries)."}
    return {
        "bias": "long" if long_ else "short",
        "entry": round(entry, 2),
        "confirmation": round(entry, 2),
        "max_chase": round(max_chase, 2),
        "invalidation": round(ref_stop, 2),
        "target1": round(t1, 2),
        "target2": round(t2, 2) if t2 else None,
        "rr": rr,
        "holding": "intraday" if row.get("reported_recently") else "1–3 days",
    }


SCORE_WEIGHTS = {
    "liquidity": 10, "rel_volume": 10, "options_liquidity": 15, "weekly": 5,
    "iv_edge": 15, "move_vs_expected": 10, "confirmation": 15,
    "spread": 5, "market_align": 5, "risk_reward": 10,
}


def score_row(row: dict, cls: dict, plan: dict | None, spy_chg: float | None) -> dict:
    """0–100 with per-component detail + top reasons/risks."""
    comps: dict[str, tuple[float, str]] = {}

    mcap = row.get("market_cap") or 0
    avgv = row.get("avg_volume") or 0
    liq = min(1.0, mcap / 10e9) * 0.5 + min(1.0, avgv / 5e6) * 0.5
    comps["liquidity"] = (liq, f"mcap ${mcap/1e9:.1f}B, avg vol {avgv/1e6:.1f}M")

    rv = row.get("rel_volume")
    comps["rel_volume"] = (min(1.0, (rv or 0) / 2.5), f"rel volume {rv:.1f}×" if rv is not None else "rel volume unknown")

    ov = row.get("options_volume") or 0
    oi = row.get("open_interest") or 0
    ol = min(1.0, ov / 20000) * 0.6 + min(1.0, oi / 50000) * 0.4
    comps["options_liquidity"] = (ol, f"options vol {ov:,}, OI {oi:,}")

    comps["weekly"] = (1.0 if row.get("weekly_options") else 0.0,
                       "weekly options available" if row.get("weekly_options") else "no weeklys")

    ivh = row.get("iv_vs_hist")
    if ivh:
        edge = min(1.0, abs(ivh["ratio"] - 1) / 0.5)
        comps["iv_edge"] = (edge, f"implied {ivh['ratio']:.2f}× historical avg ({ivh['label']})")
    else:
        comps["iv_edge"] = (0.0, "implied-vs-historical unavailable")

    ave = row.get("actual_vs_expected")
    if ave:
        comps["move_vs_expected"] = (min(1.0, abs(ave["ratio"] - 1)),
                                     f"moved {ave['ratio']:.2f}× expected ({ave['label']})")
    elif row.get("days_to") is not None and row["days_to"] >= 0:
        prox = max(0.0, 1 - row["days_to"] / 8)
        comps["move_vs_expected"] = (prox, f"reports in {row['days_to']}d (catalyst proximity)")
    else:
        comps["move_vs_expected"] = (0.0, "no move data")

    if cls["status"] in ("confirmed_long", "confirmed_short"):
        comps["confirmation"] = (1.0, f"direction confirmed ({cls['status'].split('_')[1]})")
    elif cls["status"] == "confirmation_pending":
        comps["confirmation"] = (0.5, "waiting for confirmation")
    elif cls["status"] in ("late_extended",):
        comps["confirmation"] = (0.2, "confirmed but extended")
    else:
        comps["confirmation"] = (0.3 if cls["status"] in ("potential", "watching") else 0.0, cls["status"])

    sp = row.get("spread")
    comps["spread"] = ({"good": 1.0, "ok": 0.6, "poor": 0.1}.get(sp["label"], 0.0) if sp else 0.0,
                       f"ATM spread {sp['pct']}% ({sp['label']})" if sp else "spread unknown")

    chg = row.get("change_pct")
    if spy_chg is not None and chg is not None and cls["direction"] in ("long", "short"):
        aligned = (chg >= 0) == (spy_chg >= 0) if cls["direction"] == "long" else (chg >= 0) != (spy_chg >= 0)
        comps["market_align"] = (1.0 if aligned else 0.3,
                                 f"SPY {spy_chg:+.2f}% {'with' if aligned else 'against'} the trade")
    else:
        comps["market_align"] = (0.5, "market alignment n/a")

    if plan and plan.get("rr"):
        comps["risk_reward"] = (min(1.0, plan["rr"] / 3), f"R:R ≈ {plan['rr']}")
    else:
        comps["risk_reward"] = (0.0, "no plan / R:R")

    total = sum(SCORE_WEIGHTS[k] * v[0] for k, v in comps.items())
    if cls.get("extended"):
        total *= 0.6
    if cls["setup"] == "no_trade":
        total = min(total, 25.0)
    score = round(max(0.0, min(100.0, total)), 0)

    ranked = sorted(comps.items(), key=lambda kv: SCORE_WEIGHTS[kv[0]] * kv[1][0], reverse=True)
    reasons = [v[1] for _, v in ranked[:3] if v[0] > 0.3]
    risks = [v[1] for _, v in sorted(comps.items(), key=lambda kv: kv[1][0])[:2] if v[0] < 0.5]
    if cls.get("extended"):
        risks.insert(0, "already beyond the expected-move boundary / stretched from VWAP — chase risk")
    return {"score": score,
            "components": {k: {"pts": round(SCORE_WEIGHTS[k] * v[0], 1), "max": SCORE_WEIGHTS[k], "why": v[1]}
                           for k, v in comps.items()},
            "reasons": reasons, "risks": risks}


def row_alerts(row: dict, cls: dict) -> list[str]:
    out = []
    d = row.get("days_to")
    if d == 0:
        out.append("earnings today")
    elif d == 1:
        out.append("earnings tomorrow")
    ave = row.get("actual_vs_expected")
    if ave:
        if ave["label"] == "exceeded":
            out.append("moved above expected move")
        elif ave["label"] == "undershot":
            out.append("moved below expected move")
    ivh = row.get("iv_vs_hist")
    if ivh and ivh["label"] == "rich":
        out.append("high premium detected")
    elif ivh and ivh["label"] == "cheap":
        out.append("cheap implied move detected")
    vw = row.get("vwap") or {}
    if row.get("reported_recently"):
        if vw.get("event") == "reclaim":
            out.append("VWAP reclaim after earnings")
        elif vw.get("event") == "rejection":
            out.append("VWAP rejection after earnings")
        gap = row.get("gap") or {}
        if gap.get("filled"):
            out.append("gap fill attempt")
    if cls["status"] == "confirmed_long":
        out.append("confirmed earnings long")
    elif cls["status"] == "confirmed_short":
        out.append("confirmed earnings short")
    elif cls["status"] == "late_extended":
        out.append("extended — do not chase")
    elif cls["status"] == "no_trade" and row.get("reported_recently"):
        out.append("no trade after failed confirmation")
    return out


def bucket(row: dict) -> str:
    """Section assignment (each name lives in exactly one primary section)."""
    cls_status = row["status"]
    if cls_status == "no_trade":
        return "no_trade"
    if cls_status == "late_extended":
        return "extended"
    if cls_status in ("confirmation_pending",):
        return "waiting"
    if row.get("setup") in ("high_premium", "put_selling", "covered_call", "cheap_implied"):
        return "premium"
    if row.get("reported_recently"):
        return "post"
    if row.get("days_to") == 0:
        return "today"
    return "pre"


# ═══════════════════════════════════════════════════════════════════════════
#  Fetch + assemble (thin wrappers around providers)
# ═══════════════════════════════════════════════════════════════════════════

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {"scanning": False, "scanned": 0, "total": 0,
                          "last_scan": None, "rows": [], "error": None,
                          "demo": False, "spy_chg": None}


def _em_store_path() -> Path | None:
    return (_DATA_DIR / "earnings_em.json") if _DATA_DIR else None


def _em_store_load() -> dict:
    p = _em_store_path()
    try:
        return json.loads(p.read_text()) if p and p.exists() else {}
    except Exception:
        return {}


def _em_store_save(store: dict) -> None:
    p = _em_store_path()
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store))
    except Exception:
        pass


def _earnings_timing(ts) -> str:
    """BMO / AMC / unknown from a pandas timestamp's ET hour."""
    try:
        h = ts.hour + ts.minute / 60
        if h < 9.5 and h > 0:
            return "BMO"
        if h >= 15.5:
            return "AMC"
        if h == 0:
            return "unknown"
        return "unknown"
    except Exception:
        return "unknown"


def _yf_daily(symbol: str, days: int = 400):
    if not _YF_OK:
        return None
    try:
        h = yf.Ticker(symbol).history(period=f"{max(60, days)}d", auto_adjust=False)
        return h if h is not None and not h.empty else None
    except Exception:
        return None


def _hist_moves(symbol: str) -> tuple[dict | None, dict | None]:
    """(hist stats, next event {date, timing}) from yfinance earnings dates +
    daily closes. Reaction = close after the print vs close before it."""
    if not _YF_OK:
        return None, None
    try:
        ed = yf.Ticker(symbol).get_earnings_dates(limit=16)
        if ed is None or ed.empty:
            return None, None
        daily = _yf_daily(symbol, 900)
        closes = None
        if daily is not None:
            closes = {str(k.date()): float(v) for k, v in daily["Close"].dropna().items()}
        today = _et_now().date()
        moves, next_ev = [], None
        dates = sorted(closes.keys()) if closes else []
        for ts in sorted(ed.index):
            d = ts.date()
            timing = _earnings_timing(ts)
            if d > today:
                if next_ev is None:
                    next_ev = {"date": d.isoformat(), "timing": timing}
                continue
            if not closes:
                continue
            react_day = d.isoformat() if timing == "BMO" else None
            before_day = None
            if timing == "BMO":
                prior = [x for x in dates if x < d.isoformat()]
                before_day = prior[-1] if prior else None
                react_day = d.isoformat() if d.isoformat() in closes else None
            else:  # AMC or unknown → reaction is next session
                before_day = d.isoformat() if d.isoformat() in closes else None
                if before_day is None:
                    prior = [x for x in dates if x < d.isoformat()]
                    before_day = prior[-1] if prior else None
                after = [x for x in dates if x > d.isoformat()]
                react_day = after[0] if after else None
            if before_day and react_day and before_day in closes and react_day in closes:
                moves.append((closes[react_day] / closes[before_day] - 1) * 100)
        return hist_earnings_stats(moves), next_ev
    except Exception as exc:  # noqa: BLE001
        print(f"[earnscan] hist {symbol}: {exc}", file=sys.stderr)
        return None, None


def _chain_snapshot(symbol: str, spot: float) -> dict:
    """Implied move + options liquidity from the existing chain loader."""
    out: dict[str, Any] = {"implied": None, "options_volume": None, "open_interest": None,
                           "spread": None, "weekly_options": None, "atm_iv": None,
                           "options_available": False}
    if not _CHAIN_LOADER or not spot:
        return out
    try:
        from datetime import date as _date
        today = _et_now().date()
        fri = today + timedelta(days=(4 - today.weekday()) % 7 or 7) if today.weekday() >= 4 else today + timedelta(days=(4 - today.weekday()))
        calls, puts, exp, expirations = _CHAIN_LOADER(symbol, fri, None)
        if not calls or not puts:
            return out
        out["options_available"] = True
        if expirations:
            try:
                d0 = _date.fromisoformat(expirations[0])
                d1 = _date.fromisoformat(expirations[1]) if len(expirations) > 1 else None
                out["weekly_options"] = bool(d1 and (d1 - d0).days <= 9)
            except Exception:
                out["weekly_options"] = None
        atm_c = min(calls, key=lambda c: abs(c["strike"] - spot))
        atm_p = min(puts, key=lambda p: abs(p["strike"] - spot))
        def mid(c):
            return (c["bid"] + c["ask"]) / 2 if c.get("bid") and c.get("ask") else None
        out["implied"] = implied_move(spot, mid(atm_c), mid(atm_p))
        out["options_volume"] = int(sum((c.get("volume") or 0) for c in calls + puts))
        out["open_interest"] = int(sum((c.get("openInterest") or 0) for c in calls + puts))
        out["spread"] = spread_quality(atm_c.get("bid"), atm_c.get("ask"))
        iv = atm_c.get("iv") or atm_p.get("iv")
        out["atm_iv"] = round(iv * 100, 1) if iv and iv < 5 else (round(iv, 1) if iv else None)
    except Exception as exc:  # noqa: BLE001
        print(f"[earnscan] chain {symbol}: {exc}", file=sys.stderr)
    return out


def _intraday_snapshot(symbol: str) -> dict:
    """VWAP / opening range / premarket levels via the existing engines."""
    out: dict[str, Any] = {"vwap": None, "or_high": None, "or_low": None,
                           "pm_high": None, "pm_low": None,
                           "day_high": None, "day_low": None, "day_volume": None}
    sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
    if sc is None:
        return out
    try:
        import intraday as _intra
        bars = sc.get_intraday(symbol, extended=True) or []
        if not bars:
            return out
        pm, reg = _intra.split_premarket(bars)
        if pm:
            out["pm_high"] = round(max(b["high"] for b in pm if b.get("high") is not None), 2)
            out["pm_low"] = round(min(b["low"] for b in pm if b.get("low") is not None), 2)
        if reg:
            vw = _intra.vwap_series(reg)
            last_close = next((b["close"] for b in reversed(reg) if b.get("close") is not None), None)
            opened_above = None
            if vw and vw.get("vwap"):
                first_vw = next((v for v in vw["vwap"] if v), None)
                first_close = next((b["close"] for b in reg if b.get("close") is not None), None)
                if first_vw and first_close is not None:
                    opened_above = first_close >= first_vw
            out["vwap"] = vwap_status(last_close, vw.get("last") if vw else None, opened_above)
            first30 = [b for b in reg if b["ts"] < reg[0]["ts"] + 30 * 60 * 1000]
            if first30:
                out["or_high"] = round(max(b["high"] for b in first30 if b.get("high") is not None), 2)
                out["or_low"] = round(min(b["low"] for b in first30 if b.get("low") is not None), 2)
            out["day_high"] = round(max(b["high"] for b in reg if b.get("high") is not None), 2)
            out["day_low"] = round(min(b["low"] for b in reg if b.get("low") is not None), 2)
            out["day_volume"] = int(sum(b.get("volume") or 0 for b in reg))
    except Exception as exc:  # noqa: BLE001
        print(f"[earnscan] intraday {symbol}: {exc}", file=sys.stderr)
    return out


def _quote(symbol: str) -> dict:
    sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
    if sc is not None:
        try:
            q = sc.get_quote(symbol)
            if q and q.get("last"):
                return {"price": q["last"], "change_pct": q.get("change_pct")}
        except Exception:
            pass
    d = _yf_daily(symbol, 10)
    if d is not None and len(d) >= 2:
        last, prev = float(d["Close"].iloc[-1]), float(d["Close"].iloc[-2])
        return {"price": round(last, 2), "change_pct": round((last / prev - 1) * 100, 2)}
    return {"price": None, "change_pct": None}


def _build_row(wl: dict, spy_chg: float | None, em_store: dict) -> dict | None:
    sym = wl["symbol"]
    days_to = wl.get("days_to_earnings")
    q = _quote(sym)
    price = q["price"]
    row: dict[str, Any] = {
        "ticker": sym, "company": wl.get("company"),
        "sector": wl.get("sector"), "industry": wl.get("industry"),
        "market_cap": wl.get("market_cap"), "avg_volume": wl.get("avg_volume"),
        "report_date": wl.get("next_earnings"), "days_to": days_to,
        "price": price, "change_pct": q["change_pct"],
    }
    hist, next_ev = _hist_moves(sym)
    row["hist"] = hist
    row["timing"] = (next_ev or {}).get("timing", "unknown")
    if next_ev and not row["report_date"]:
        row["report_date"] = next_ev["date"]

    # daily context: prev day levels, gap, 5d momentum, rel volume
    daily = _yf_daily(sym, 30)
    sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
    bars = None
    if sc is not None:
        try:
            bars = sc.get_price_history(sym, days=30)
        except Exception:
            bars = None
    if bars and len(bars) >= 3:
        row["prev_high"] = round(float(bars[-2]["high"]), 2)
        row["prev_low"] = round(float(bars[-2]["low"]), 2)
        prev_close = float(bars[-2]["close"])
        row["gap"] = gap_info(float(bars[-1]["open"]), prev_close, float(bars[-1]["low"]), float(bars[-1]["high"]))
        if len(bars) >= 7 and bars[-6]["close"]:
            row["mom5_pct"] = round((float(bars[-1]["close"]) / float(bars[-6]["close"]) - 1) * 100, 2)
        vol_today = float(bars[-1].get("volume") or 0)
        if wl.get("avg_volume"):
            row["rel_volume"] = round(vol_today / wl["avg_volume"], 2) if vol_today else None
    elif daily is not None and len(daily) >= 3:
        row["prev_high"] = round(float(daily["High"].iloc[-2]), 2)
        row["prev_low"] = round(float(daily["Low"].iloc[-2]), 2)
        prev_close = float(daily["Close"].iloc[-2])
        row["gap"] = gap_info(float(daily["Open"].iloc[-1]), prev_close,
                              float(daily["Low"].iloc[-1]), float(daily["High"].iloc[-1]))
        if len(daily) >= 7:
            row["mom5_pct"] = round((float(daily["Close"].iloc[-1]) / float(daily["Close"].iloc[-6]) - 1) * 100, 2)
        vol_today = float(daily["Volume"].iloc[-1] or 0)
        if wl.get("avg_volume") and vol_today:
            row["rel_volume"] = round(vol_today / wl["avg_volume"], 2)

    row.update(_chain_snapshot(sym, price))
    row["iv_vs_hist"] = implied_vs_hist((row.get("implied") or {}).get("pct"), hist)

    reported_recently = days_to is not None and -4 <= days_to <= 0 and row.get("report_date")
    # "reported" requires the report datetime to be in the past
    if reported_recently and days_to == 0:
        now = _et_now()
        if row["timing"] == "AMC" and now.hour < 16:
            reported_recently = False
        if row["timing"] == "BMO" and now.hour < 8:
            reported_recently = False
    row["reported_recently"] = bool(reported_recently)

    # persist pre-print implied move; read it back post-print
    key = f"{sym}|{row.get('report_date')}"
    if not row["reported_recently"] and (row.get("implied") or {}).get("pct") and row.get("report_date"):
        if key not in em_store:
            em_store[key] = row["implied"]["pct"]
    expected_pre = em_store.get(key)
    if row["reported_recently"]:
        row["actual_vs_expected"] = actual_vs_expected(
            row.get("change_pct"), expected_pre, (hist or {}).get("avg_abs"))
        intr = _intraday_snapshot(sym)
        row.update(intr)
        if row.get("day_volume") and wl.get("avg_volume"):
            row["rel_volume"] = round(row["day_volume"] / wl["avg_volume"], 2)
    else:
        row["actual_vs_expected"] = None
        row["vwap"] = None

    cls = classify(row)
    plan = build_plan(row, cls)
    sc_ = score_row(row, cls, plan, spy_chg)
    row.update(cls)
    row["plan"] = plan
    row["score"] = sc_["score"]
    row["score_detail"] = sc_
    row["alerts"] = row_alerts(row, cls)
    row["bucket"] = bucket(row)
    row["confirm_text"] = (f"holds above {plan['confirmation']}" if plan and plan.get("confirmation") and cls["direction"] == "long"
                           else f"holds below {plan['confirmation']}" if plan and plan.get("confirmation")
                           else "await the print" if not row["reported_recently"] else "reclaim/reject VWAP")
    row["invalidate_text"] = (f"crosses {plan['invalidation']}" if plan and plan.get("invalidation")
                              else "n/a")
    return row


DEMO_ROWS = [
    {"symbol": "DEMO1", "company": "Demo Semiconductor", "sector": "Technology",
     "industry": "Semiconductors", "market_cap": 45e9, "avg_volume": 8e6,
     "next_earnings": None, "days_to_earnings": 0},
    {"symbol": "DEMO2", "company": "Demo Retail", "sector": "Consumer Cyclical",
     "industry": "Retail", "market_cap": 12e9, "avg_volume": 3e6,
     "next_earnings": None, "days_to_earnings": 1},
    {"symbol": "DEMO3", "company": "Demo Bank", "sector": "Financial",
     "industry": "Banks", "market_cap": 80e9, "avg_volume": 12e6,
     "next_earnings": None, "days_to_earnings": -1},
]


def _demo_board_rows() -> list[dict]:
    """Deterministic, clearly-labeled demo rows so the UI is testable when
    every provider is down. No pretend live numbers — small seeded values."""
    today = _et_now().date()
    out = []
    seeds = [(0, 62, "high_premium", "potential", "sell_premium", 6.2, 4.1),
             (1, 48, "pre_earnings_momentum", "watching", "watch", 5.1, 5.8),
             (-1, 71, "post_earnings_continuation", "confirmed_long", "confirmed_entry", 7.4, 4.9)]
    for wl, (dd, score, setup, status, action, imp, hist_avg) in zip(DEMO_ROWS, seeds):
        rd = today + timedelta(days=dd)
        out.append({
            "ticker": wl["symbol"], "company": wl["company"], "sector": wl["sector"],
            "industry": wl["industry"], "market_cap": wl["market_cap"],
            "avg_volume": wl["avg_volume"], "report_date": rd.isoformat(),
            "days_to": dd, "timing": "AMC" if dd >= 0 else "BMO",
            "price": 100.0, "change_pct": 3.1 if dd < 0 else 0.4,
            "rel_volume": 2.4 if dd < 0 else 1.1,
            "implied": {"pct": imp, "dollars": imp, "upper": 100 + imp, "lower": 100 - imp},
            "hist": {"n": 8, "avg_abs": hist_avg, "med_abs": round(hist_avg - 0.4, 1), "last": 3.2,
                     "beat_dir_pct": 62, "moves": [4.2, -3.1, 5.0, -2.2, 6.1, 3.3, -4.8, 3.2]},
            "iv_vs_hist": {"ratio": round(imp / hist_avg, 2), "label": "rich" if imp / hist_avg >= 1.3 else "fair"},
            "options_volume": 25000, "open_interest": 90000, "options_available": True,
            "weekly_options": True, "atm_iv": 68.0,
            "spread": {"pct": 3.2, "label": "good"},
            "setup": setup, "status": status, "action": action,
            "direction": "long" if dd < 0 else "neutral", "extended": False,
            "score": score, "reported_recently": dd < 0,
            "plan": {"bias": "long", "entry": 101.2, "confirmation": 101.2, "max_chase": 103.1,
                     "invalidation": 99.4, "target1": 106.9, "target2": None, "rr": 2.9,
                     "holding": "intraday"} if dd < 0 else None,
            "score_detail": {"score": score, "components": {},
                             "reasons": ["DEMO DATA — providers unavailable"],
                             "risks": ["DEMO DATA"]},
            "alerts": [], "bucket": "post" if dd < 0 else ("premium" if setup == "high_premium" else "pre"),
            "confirm_text": "demo", "invalidate_text": "demo", "demo": True,
        })
    return out


def _spy_change() -> float | None:
    q = _quote("SPY")
    return q.get("change_pct")


def _scan_worker(symbols_meta: list[dict]) -> None:
    try:
        spy_chg = _spy_change()
        em_store = _em_store_load()
        rows = []
        for i, wl in enumerate(symbols_meta):
            try:
                r = _build_row(wl, spy_chg, em_store)
                if r:
                    rows.append(r)
            except Exception as exc:  # noqa: BLE001
                print(f"[earnscan] row {wl.get('symbol')}: {exc}", file=sys.stderr)
            with _LOCK:
                _STATE["scanned"] = i + 1
            time.sleep(0.15)
        _em_store_save(em_store)
        demo = False
        if not rows:
            rows = _demo_board_rows()
            demo = True
        rows.sort(key=lambda r: -r["score"])
        with _LOCK:
            _STATE.update({"rows": rows, "last_scan": _now_iso(), "error": None,
                           "demo": demo, "spy_chg": spy_chg})
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE["error"] = str(exc)
    finally:
        with _LOCK:
            _STATE["scanning"] = False


def trigger_scan(force: bool = False) -> dict:
    with _LOCK:
        if _STATE["scanning"] and not force:
            return {"started": False, "reason": "already scanning"}
    board = _WLBOARD_GETTER() if _WLBOARD_GETTER else None
    cands = []
    for r in (board or {}).get("rows", []):
        d = r.get("days_to_earnings")
        if d is not None and -4 <= d <= 8:
            cands.append({"symbol": r["symbol"], "company": r.get("company"),
                          "sector": r.get("sector"), "industry": r.get("industry"),
                          "market_cap": r.get("market_cap"), "avg_volume": r.get("avg_volume"),
                          "next_earnings": r.get("next_earnings"), "days_to_earnings": d})
    cands.sort(key=lambda c: (abs(c["days_to_earnings"]), -(c["market_cap"] or 0)))
    cands = cands[:80]
    with _LOCK:
        _STATE.update({"scanning": True, "scanned": 0, "total": len(cands)})
    threading.Thread(target=_scan_worker, args=(cands,), daemon=True).start()
    return {"started": True, "total": len(cands)}


def get_board() -> dict:
    with _LOCK:
        rows = list(_STATE["rows"])
        status = {k: _STATE[k] for k in ("scanning", "scanned", "total", "last_scan", "error")}
        demo = _STATE["demo"]
        spy = _STATE["spy_chg"]
    sections = {}
    for r in rows:
        sections.setdefault(r["bucket"], []).append(r["ticker"])
    return {"as_of": _now_iso(), "status": status, "demo": demo, "spy_chg": spy,
            "count": len(rows), "rows": rows, "sections": sections,
            "note": ("Universe = your watchlist names reporting within -4…+8 days. "
                     "Expected-move comparisons use the implied move recorded by this scanner "
                     "before each print; until that history accumulates the fallback is the "
                     "name's historical average move (labeled). EPS consensus/surprise has no "
                     "free source and is not shown.")}
