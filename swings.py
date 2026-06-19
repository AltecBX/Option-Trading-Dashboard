"""swings.py — swing low→high pattern recognition (v1).

For one ticker: detect major swing lows and swing highs from daily bars
via a percentage zig-zag, measure each low→high up-move, derive the
stock's typical rhythm (days + % move), and — when price has just put in
a fresh swing low — project upside targets and a time window from the
prior swings.

Per swing: low date/price, high date/price, trading days, $ change,
% change, average daily % move, and whether it matches the rhythm.

Free data: yfinance daily history. Stdlib + yfinance/pandas/numpy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    import yfinance as yf
    import numpy as np
    _OK = True
except Exception:
    _OK = False


def _zigzag(highs, lows, pct):
    """Percentage zig-zag → alternating (idx, price, kind) pivots using
    intraday highs for tops and lows for bottoms. The final pivot is the
    in-progress extreme (may be unconfirmed)."""
    n = len(highs)
    if n < 3:
        return []
    pivots = [(0, lows[0], "low")]
    direction = 1            # 1 = in up-move (seeking high), -1 = down-move
    ext_i, ext_p = 0, highs[0]
    for i in range(1, n):
        if direction == 1:
            if highs[i] > ext_p:
                ext_p, ext_i = highs[i], i
            elif lows[i] <= ext_p * (1 - pct):
                pivots.append((ext_i, ext_p, "high"))
                direction, ext_p, ext_i = -1, lows[i], i
        else:
            if lows[i] < ext_p:
                ext_p, ext_i = lows[i], i
            elif highs[i] >= ext_p * (1 + pct):
                pivots.append((ext_i, ext_p, "low"))
                direction, ext_p, ext_i = 1, highs[i], i
    pivots.append((ext_i, ext_p, "high" if direction == 1 else "low"))
    return pivots


def _busday_offset(date_str: str, n: int) -> str:
    try:
        d = np.datetime64(date_str, "D")
        return str(np.busday_offset(d, n, roll="forward"))
    except Exception:
        return date_str


def analyze(symbol: str, period: str = "1y", pct: float = 0.08,
            min_move_pct: float = 10.0) -> dict:
    symbol = symbol.upper().strip()
    if not _OK:
        return {"symbol": symbol, "error": "yfinance unavailable"}
    try:
        hist = yf.Ticker(symbol).history(period=period, interval="1d")
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "error": f"history fetch failed: {exc}"}
    if hist is None or len(hist) < 20:
        return {"symbol": symbol, "error": "not enough price history"}

    highs = [float(x) for x in hist["High"]]
    lows = [float(x) for x in hist["Low"]]
    closes = [float(x) for x in hist["Close"]]
    dates = [d.strftime("%Y-%m-%d") for d in hist.index]

    pivots = _zigzag(highs, lows, pct)

    # Build low→high up-swings.
    swings = []
    for k in range(len(pivots) - 1):
        a, b = pivots[k], pivots[k + 1]
        if a[2] == "low" and b[2] == "high":
            low_i, low_p, _ = a
            high_i, high_p, _ = b
            days = high_i - low_i
            if days <= 0:
                continue
            pctc = (high_p - low_p) / low_p * 100.0
            if pctc < min_move_pct:
                continue
            swings.append({
                "low_date": dates[low_i], "low_price": round(low_p, 2),
                "high_date": dates[high_i], "high_price": round(high_p, 2),
                "trading_days": days,
                "dollar_change": round(high_p - low_p, 2),
                "pct_change": round(pctc, 2),
                "avg_daily_pct": round(pctc / days, 2),
            })

    # Rhythm across completed swings. Use the interquartile (25th–75th)
    # range as the "usual" band so a single outlier run doesn't blow out
    # the projection.
    rhythm = None
    if len(swings) >= 2:
        d = np.array([s["trading_days"] for s in swings], dtype=float)
        p = np.array([s["pct_change"] for s in swings], dtype=float)
        rhythm = {
            "count": len(swings),
            "days_min": int(d.min()), "days_max": int(d.max()),
            "days_p25": int(round(np.percentile(d, 25))),
            "days_p75": int(round(np.percentile(d, 75))),
            "days_median": round(float(np.median(d)), 1),
            "pct_min": round(float(p.min()), 1), "pct_max": round(float(p.max()), 1),
            "pct_p25": round(float(np.percentile(p, 25)), 1),
            "pct_p75": round(float(np.percentile(p, 75)), 1),
            "pct_median": round(float(np.median(p)), 1),
        }
        # A swing "matches the rhythm" when both its duration and move sit
        # inside the usual (IQR) band.
        for s in swings:
            s["matches_rhythm"] = (
                rhythm["days_p25"] <= s["trading_days"] <= rhythm["days_p75"] and
                rhythm["pct_p25"] <= s["pct_change"] <= rhythm["pct_p75"]
            )

    # Active setup + projection: price has just put in a fresh swing low
    # (last pivot is a low) → project from prior swings.
    projection = None
    last = pivots[-1] if pivots else None
    current_price = round(closes[-1], 2) if closes else None
    if last and last[2] == "low" and rhythm:
        low_i, low_p, _ = last
        low_date = dates[low_i]
        t_lo = round(low_p * (1 + rhythm["pct_p25"] / 100.0), 2)
        t_hi = round(low_p * (1 + rhythm["pct_p75"] / 100.0), 2)
        t_med = round(low_p * (1 + rhythm["pct_median"] / 100.0), 2)
        projection = {
            "from_low_date": low_date,
            "from_low_price": round(low_p, 2),
            "days_so_far": (len(dates) - 1) - low_i,
            "target_low": t_lo, "target_median": t_med, "target_high": t_hi,
            "pct_low": rhythm["pct_p25"], "pct_high": rhythm["pct_p75"],
            "window_start": _busday_offset(low_date, max(1, rhythm["days_p25"])),
            "window_end": _busday_offset(low_date, max(1, rhythm["days_p75"])),
            "to_target_median_pct": round((t_med - current_price) / current_price * 100.0, 1) if current_price else None,
        }

    return {
        "symbol": symbol,
        "current_price": current_price,
        "params": {"period": period, "pct": pct, "min_move_pct": min_move_pct},
        "swings": swings,
        "rhythm": rhythm,
        "projection": projection,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
