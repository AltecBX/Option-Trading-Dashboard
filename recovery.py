"""recovery.py — Prior High Recovery scanner (v3.91).

Finds stocks that made a significant high, corrected meaningfully, and are
now showing evidence the correction has ended — BEFORE they get back to the
prior high — then ranks them by historically measured probability of
revisiting that high versus hitting an objective invalidation level first.

Measured vs estimated, stated plainly:
  * Prices, levels, structure (prior high / correction low / bounce high /
    higher low), indicators, and reward-to-risk are MEASURED from
    split-adjusted daily bars.
  * Probabilities and time-to-target stats come from a historical study
    (recovery_model.json, produced by recovery_fit.py) — empirical lookup
    tables with sample sizes attached, not guesses.  When the artifact is
    missing, a setup falls outside the model's population (only Early /
    Confirmed Recovery stages are in it), or its bucket has too few
    historical examples, the scanner says so instead of inventing a number.
  * The historical universe is today's watchlist (survivors) — delisted
    stocks are absent, which flatters absolute hit rates.  Disclosed in the
    payload and the UI.

Lookahead discipline: `detect_setup(bars)` describes the LAST bar of
whatever slice it is given, using only that slice.  The historical study
replays each day through `iter_days`, which evaluates the same `_eval_at`
logic against precomputed series in which entry i depends only on bars
≤ i (EMA/RSI/ATR/MACD are forward recursions; the rolling prior-high uses
a window that ends before i; "unbroken since the high" is capped at i).
test_recovery.py asserts `iter_days` output equals `detect_setup` on the
truncated slice — a historical signal is identical whether future candles
exist in the dataset or not.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:  # noqa: BLE001
    _YF_OK = False

try:
    import analyst_board  # HEAVY_SCAN_LOCK — one heavy universe scan at a time
    _AB_OK = True
except Exception:  # noqa: BLE001
    _AB_OK = False

# ── wiring (set by options_dashboard at import time) ────────────────────────

_SCHWAB_GETTER = None
_BOARD_GETTER = None
_DATA_DIR: Path | None = None


def configure(schwab_getter=None, board_getter=None, data_dir=None) -> None:
    global _SCHWAB_GETTER, _BOARD_GETTER, _DATA_DIR
    _SCHWAB_GETTER = schwab_getter
    _BOARD_GETTER = board_getter
    _DATA_DIR = (Path(data_dir) / "recovery") if data_dir else None
    if data_dir:
        _restore_board()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── configuration ───────────────────────────────────────────────────────────
# Defaults are the FALLBACK; production values come from recovery_model.json's
# "config" section, which the historical study selected (recovery_fit.py).

CFG: dict[str, Any] = {
    "lookback": 252,          # window for the prior significant high
    "min_days_since_high": 10,
    "min_significance": 15,   # min(bars it topped before, bars unbroken since)
    "min_depth": 0.10,        # correction must be at least this deep
    "max_depth": 0.60,        # beyond this it's a collapse, not a correction
    "min_days_since_low": 2,
    "min_history": 120,       # bars required before we say anything
    "zz_frac": 0.25,          # zigzag reversal = max(zz_min, depth*zz_frac)
    "zz_min": 0.04,
    "hl_eps": 0.005,          # higher low must clear the correction low by this
    "inval_mode": "struct",   # struct = higher low when present, else corr low
    "inval_buffer": 0.005,    # invalidation sits this far under the level
    "horizon": 60,            # outcome horizon (trading days)
    "min_price": 3.0,         # penny-stock floor
    "min_dollar_vol": 2e6,    # 20d avg dollar volume floor
    "rr_lo": 0.10,            # scanner surfaces RR in [rr_lo, rr_hi)
    "rr_hi": 0.95,
}

MIN_BUCKET_N = 30             # smallest cohort we will quote a probability from
MODEL_STAGES = ("early", "confirmed")   # the population the model was fit on

_SECTOR_ETF = {
    "Technology": "XLK", "Information Technology": "XLK",
    "Financial Services": "XLF", "Financials": "XLF",
    "Healthcare": "XLV", "Health Care": "XLV",
    "Energy": "XLE", "Consumer Cyclical": "XLY", "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP", "Consumer Staples": "XLP",
    "Industrials": "XLI", "Basic Materials": "XLB", "Materials": "XLB",
    "Utilities": "XLU", "Real Estate": "XLRE",
    "Communication Services": "XLC",
}


# ── indicator series (pure; entry i depends only on closes[:i+1]) ───────────

def _ema_series(closes: list[float], span: int) -> list[float | None]:
    if not closes:
        return []
    k = 2.0 / (span + 1)
    out: list[float | None] = [None] * len(closes)
    if len(closes) < span:
        return out
    e = sum(closes[:span]) / span
    out[span - 1] = e
    for i in range(span, len(closes)):
        e = closes[i] * k + e * (1 - k)
        out[i] = e
    return out


def _rsi14(closes: list[float]) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < 15:
        return out
    gains = losses = 0.0
    for i in range(1, 15):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / 14, losses / 14
    out[14] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(15, n):
        d = closes[i] - closes[i - 1]
        ag = (ag * 13 + max(d, 0.0)) / 14
        al = (al * 13 + max(-d, 0.0)) / 14
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _atr14(bars: list[dict]) -> list[float | None]:
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < 15:
        return out
    trs = []
    for i in range(1, n):
        h, lo, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    a = sum(trs[:14]) / 14
    out[14] = a
    for i in range(15, n):
        a = (a * 13 + trs[i - 1]) / 14
        out[i] = a
    return out


def _macd_hist_series(closes: list[float]) -> list[float | None]:
    """MACD(12,26,9) histogram; signal EMA seeded over the first 9 valid
    MACD values, then recursive — entry i uses closes[:i+1] only."""
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    n = len(closes)
    out: list[float | None] = [None] * n
    k = 2.0 / 10.0
    sig = None
    seed: list[float] = []
    for i in range(n):
        if e12[i] is None or e26[i] is None:
            continue
        m = e12[i] - e26[i]
        if sig is None:
            seed.append(m)
            if len(seed) == 9:
                sig = sum(seed) / 9.0
                out[i] = m - sig
            continue
        sig = m * k + sig * (1 - k)
        out[i] = m - sig
    return out


def _median(vals):
    v = sorted(vals)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _zigzag(highs: list[float], lows: list[float], pct: float):
    """Percentage zigzag → alternating (idx, price, kind) pivots (same
    contract as swings._zigzag; re-implemented so this module stands alone)."""
    n = len(highs)
    if n < 3:
        return []
    pivots = [(0, lows[0], "low")]
    direction = 1
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


def spy_regime_map(spy_close: dict[str, float] | None):
    """Trailing-only SPY regime per date: close vs SMA200 with SMA50 as the
    uptrend qualifier (same rules as patterns._spy_regimes).  Returns
    (sorted_dates, {date: regime}) for bisect lookup."""
    if not spy_close:
        return [], {}
    dates = sorted(spy_close)
    closes = [spy_close[d] for d in dates]
    csum = [0.0]
    for c in closes:
        csum.append(csum[-1] + c)
    out = {}
    for i, d in enumerate(dates):
        if i + 1 < 200:
            out[d] = None
            continue
        s50 = (csum[i + 1] - csum[i + 1 - 50]) / 50
        s200 = (csum[i + 1] - csum[i + 1 - 200]) / 200
        out[d] = ("uptrend" if closes[i] > s200 and s50 > s200
                  else "downtrend" if closes[i] < s200 else "chop")
    return dates, out


def _regime_at(reg_dates: list[str], reg_map: dict, d: str):
    if not reg_dates:
        return None
    j = bisect.bisect_right(reg_dates, d) - 1
    return reg_map.get(reg_dates[j]) if j >= 0 else None


# ── structural precompute (every entry i depends only on bars ≤ i) ──────────

def precompute(bars: list[dict], cfg: dict | None = None) -> dict:
    c = {**CFG, **(cfg or {})}
    n = len(bars)
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b.get("volume") or 0 for b in bars]

    # peak_idx[i]: first-occurrence argmax of highs over the window
    # [max(0, i+1-lookback), i+1-min_days_since_high) — a window that ends
    # min_days_since_high bars BEFORE i, so a fresh spike is never its own
    # "prior" high.  Monotonic deque; equal highs keep the earlier index.
    lb, gap = c["lookback"], c["min_days_since_high"]
    peak_idx: list[int | None] = [None] * n
    dq: list[int] = []
    for i in range(n):
        j = i - gap                      # newest index allowed into the window
        if j >= 0:
            while dq and highs[dq[-1]] < highs[j]:
                dq.pop()
            dq.append(j)
        lo_bound = i + 1 - lb
        while dq and dq[0] < lo_bound:
            dq.pop(0)
        peak_idx[i] = dq[0] if dq else None

    # prev_ge[j]: nearest k<j with highs[k] >= highs[j] (for "left" span).
    prev_ge: list[int | None] = [None] * n
    stack: list[int] = []
    for j in range(n):
        while stack and highs[stack[-1]] < highs[j]:
            stack.pop()
        prev_ge[j] = stack[-1] if stack else None
        stack.append(j)

    # next_ge[j]: nearest k>j with highs[k] >= highs[j] (capped at i when
    # read, so no future leaks into an as-of-i evaluation).
    next_ge: list[int | None] = [None] * n
    stack = []
    for j in range(n - 1, -1, -1):
        while stack and highs[stack[-1]] < highs[j]:
            stack.pop()
        next_ge[j] = stack[-1] if stack else None
        stack.append(j)

    # corr_idx[i]: first-occurrence argmin of lows over (peak_idx[i], i].
    corr_idx: list[int | None] = [None] * n
    t = None
    for i in range(n):
        p = peak_idx[i]
        if p is None:
            t = None
        elif t is None or peak_idx[i - 1] != p or t <= p:
            t = p + 1 if p + 1 <= i else None
            if t is not None:
                for j in range(p + 1, i + 1):
                    if lows[j] < lows[t]:
                        t = j
        elif lows[i] < lows[t]:
            t = i
        corr_idx[i] = t

    # rolling 20-day average volume ENDING YESTERDAY (vavg[i] = mean of
    # vols[i-20..i-1]) — matches detect-time relvol/dollar-vol semantics.
    vavg: list[float | None] = [None] * n
    vsum = [0]
    for v in vols:
        vsum.append(vsum[-1] + v)
    for i in range(n):
        s = max(0, i - 20)
        cnt = i - s
        vavg[i] = (vsum[i] - vsum[s]) / cnt if cnt > 0 else None

    return {
        "cfg": c, "n": n,
        "closes": closes, "highs": highs, "lows": lows, "vols": vols,
        "peak_idx": peak_idx, "prev_ge": prev_ge, "next_ge": next_ge,
        "corr_idx": corr_idx, "vavg": vavg,
        "e9": _ema_series(closes, 9), "e20": _ema_series(closes, 20),
        "e50": _ema_series(closes, 50), "rsi": _rsi14(closes),
        "atr": _atr14(bars), "macd": _macd_hist_series(closes),
    }


def _eval_at(bars: list[dict], i: int, pre: dict,
             spy_close: dict | None = None, sector_close: dict | None = None,
             reg_dates: list | None = None, reg_map: dict | None = None) -> dict | None:
    """The one detection implementation: describe the setup as of bar i
    using only information ≤ i.  detect_setup and iter_days both call this."""
    c = pre["cfg"]
    if i + 1 < c["min_history"]:
        return None
    closes, highs, lows, vols = (pre["closes"], pre["highs"], pre["lows"],
                                 pre["vols"])
    C = closes[i]
    if not C or C < c["min_price"]:
        return None

    # 1. prior significant high
    w_start = max(0, i + 1 - c["lookback"])
    w_end = i + 1 - c["min_days_since_high"]
    if w_end - w_start < 5:
        return None
    p = pre["peak_idx"][i]
    if p is None:
        return None
    H = highs[p]
    if H <= 0:
        return None
    days_since_high = i - p

    # significance = min(bars it stood above before, bars unbroken since)
    pg = pre["prev_ge"][p]
    if pg is not None:
        left = min(p - pg - 1, 504)
    else:
        left = min(p, 504)
        if p <= 504:
            left = max(left, days_since_high)     # IPO edge: ran off the start
    ng = pre["next_ge"][p]
    first_break = ng if ng is not None else (i + 1)
    unbroken = min(first_break, i + 1) - p - 1
    significance = min(left, unbroken)
    if significance < c["min_significance"]:
        return None

    # 2. correction low after the high
    t = pre["corr_idx"][i]
    if t is None or t <= p:
        return None
    L = lows[t]
    depth = (H - L) / H
    if depth < c["min_depth"] or depth > c["max_depth"]:
        return None
    days_since_low = i - t
    span = H - L
    if span <= 0:
        return None
    rr = (C - L) / span
    dist = (H - C) / H

    # 3. bounce structure via zigzag on the post-low leg
    rev = max(c["zz_min"], depth * c["zz_frac"])
    piv = _zigzag(highs[t:i + 1], lows[t:i + 1], rev) if i - t >= 2 else []
    bounce_high = higher_low = None
    bounce_i = higher_low_i = None
    for k in range(1, len(piv)):
        idx, price, kind = piv[k]
        confirmed = k < len(piv) - 1              # trailing pivot in-progress
        if kind == "high" and bounce_high is None and confirmed:
            bounce_high, bounce_i = price, t + idx
        elif (kind == "low" and bounce_high is not None and higher_low is None
              and confirmed and price > L * (1 + c["hl_eps"])):
            higher_low, higher_low_i = price, t + idx
    broke_bounce = bool(bounce_high is not None and C > bounce_high)
    has_higher_low = higher_low is not None

    # 4. day-window breakout evidence (prior N-day highs, excluding today)
    def _prior_high(nn):
        s = max(0, i - nn)
        seg = highs[s:i]
        return max(seg) if seg else None

    break3 = bool((h3 := _prior_high(3)) and C > h3)
    break5 = bool((h5 := _prior_high(5)) and C > h5)
    break10 = bool((h10 := _prior_high(10)) and C > h10)
    close_above_prev_high = bool(i >= 1 and C > highs[i - 1])

    # 5. indicators (reads from causal series)
    e9, e20, e50 = pre["e9"], pre["e20"], pre["e50"]
    rsi, atr, macd = pre["rsi"], pre["atr"], pre["macd"]
    ema9, ema20, ema50 = e9[i], e20[i], e50[i]

    def _slope(series, d=3):
        if i - d < 0 or series[i] is None or series[i - d] is None:
            return None
        return (series[i] - series[i - d]) / series[i - d]

    s9, s20 = _slope(e9), _slope(e20)
    above9 = bool(ema9 and C > ema9)
    above20 = bool(ema20 and C > ema20)
    above50 = bool(ema50 and C > ema50)
    cross920 = bool(ema9 and ema20 and ema9 > ema20 and i >= 3
                    and e9[i - 3] is not None and e20[i - 3] is not None
                    and e9[i - 3] <= e20[i - 3])
    r_now = rsi[i]
    r_prev = rsi[i - 3] if i >= 3 else None
    rsi_rising = bool(r_now is not None and r_prev is not None and r_now > r_prev)
    rsi_cross50 = bool(r_now is not None and r_prev is not None
                       and r_now > 50 >= r_prev)
    hist_now = macd[i]
    hist_prev = macd[i - 3] if i >= 3 else None
    macd_improving = bool(hist_now is not None and hist_prev is not None
                          and hist_now > hist_prev)
    macd_cross = bool(hist_now is not None and hist_prev is not None
                      and hist_now > 0 >= hist_prev)

    # volume + ATR
    av = pre["vavg"][i]
    relvol = (vols[i] / av) if av else None
    dollar_vol = av * C if av else None
    atr_now = atr[i]
    atr_then = atr[i - 20] if i >= 20 else None
    atr_pct = (atr_now / C) if (atr_now and C) else None
    atr_expanding = bool(atr_now and atr_then and atr_now > atr_then)

    # relative strength vs SPY / sector over 20 bars (date-aligned closes)
    def _rs(bench: dict | None):
        if not bench or i < 20:
            return None
        b_now = bench.get(bars[i]["date"][:10])
        b_then = bench.get(bars[i - 20]["date"][:10])
        if not b_now or not b_then or not closes[i - 20]:
            return None
        return (closes[i] / closes[i - 20] - 1) - (b_now / b_then - 1)

    rs_spy = _rs(spy_close)
    rs_sector = _rs(sector_close)

    if reg_dates is None and spy_close:
        reg_dates, reg_map = spy_regime_map(spy_close)
    regime = _regime_at(reg_dates or [], reg_map or {}, bars[i]["date"][:10])

    # trend before the original high (60-bar run-up into it)
    pre_trend = (closes[p] / closes[p - 60] - 1) if (p >= 60 and closes[p - 60]) else None

    # 6. stage
    hl_broken = bool(has_higher_low and C < higher_low)
    evidence = sum([has_higher_low, broke_bounce, break5, break10, above9,
                    bool(s9 and s9 > 0), above20, rsi_rising, macd_improving])
    if C > H:
        stage = "breakout"
    elif dist <= 0.015:
        stage = "prior_high_test"
    elif rr >= 0.72:
        stage = "approaching"
    elif hl_broken:
        stage = "failed"
    elif (has_higher_low and broke_bounce) or (rr >= 0.35 and above20
                                               and bool(s20 and s20 > 0)
                                               and has_higher_low):
        stage = "confirmed"
    elif rr >= 0.05 and evidence >= 3:
        stage = "early"
    else:
        stage = "bottoming"

    # 7. invalidation + reward/risk
    buf = c["inval_buffer"]
    if c["inval_mode"] == "corr_low" or not has_higher_low:
        inval = L * (1 - buf)
        inval_basis = "correction low"
    else:
        inval = higher_low * (1 - buf)
        inval_basis = "higher low"
    risk_pct = (C - inval) / C if C else None
    upside_pct = (H - C) / C if C else None
    rr_ratio = (upside_pct / risk_pct) if (risk_pct and risk_pct > 0
                                           and upside_pct is not None) else None

    return {
        "close": round(C, 4),
        "prior_high": round(H, 4),
        "prior_high_date": bars[p]["date"][:10],
        "corr_low": round(L, 4),
        "corr_low_date": bars[t]["date"][:10],
        "depth": round(depth, 4),
        "recovery_ratio": round(rr, 4),
        "dist_to_high": round(dist, 4),
        "days_since_high": days_since_high,
        "days_since_low": days_since_low,
        "significance": significance,
        "bounce_high": round(bounce_high, 4) if bounce_high else None,
        "bounce_high_date": bars[bounce_i]["date"][:10] if bounce_i is not None else None,
        "higher_low": round(higher_low, 4) if higher_low else None,
        "higher_low_date": bars[higher_low_i]["date"][:10] if higher_low_i is not None else None,
        "broke_bounce": broke_bounce,
        "has_higher_low": has_higher_low,
        "break3": break3, "break5": break5, "break10": break10,
        "close_above_prev_high": close_above_prev_high,
        "above_ema9": above9, "above_ema20": above20, "above_ema50": above50,
        "ema9_slope": round(s9, 5) if s9 is not None else None,
        "ema20_slope": round(s20, 5) if s20 is not None else None,
        "ema_cross_920": cross920,
        "rsi": round(r_now, 2) if r_now is not None else None,
        "rsi_rising": rsi_rising, "rsi_cross50": rsi_cross50,
        "macd_hist": round(hist_now, 5) if hist_now is not None else None,
        "macd_improving": macd_improving, "macd_cross": macd_cross,
        "relvol": round(relvol, 2) if relvol is not None else None,
        "dollar_vol": round(dollar_vol) if dollar_vol else None,
        "atr_pct": round(atr_pct, 4) if atr_pct is not None else None,
        "atr_expanding": atr_expanding,
        "rs_spy": round(rs_spy, 4) if rs_spy is not None else None,
        "rs_sector": round(rs_sector, 4) if rs_sector is not None else None,
        "regime": regime,
        "trend_before_high": round(pre_trend, 4) if pre_trend is not None else None,
        "evidence": evidence,
        "stage": stage,
        "invalidation": round(inval, 4),
        "inval_basis": inval_basis,
        "risk_pct": round(risk_pct, 4) if risk_pct is not None else None,
        "upside_pct": round(upside_pct, 4) if upside_pct is not None else None,
        "reward_risk": round(rr_ratio, 2) if rr_ratio is not None else None,
        "date": bars[i]["date"][:10],
    }


def detect_setup(bars: list[dict], cfg: dict | None = None,
                 spy_close: dict | None = None,
                 sector_close: dict | None = None) -> dict | None:
    """Prior-high-recovery structure as of bars[-1].  Uses ONLY the bars
    passed in — history is replayed by slicing.  None when no qualifying
    prior high + correction exists."""
    if not bars:
        return None
    pre = precompute(bars, cfg)
    return _eval_at(bars, len(bars) - 1, pre, spy_close, sector_close)


def iter_days(bars: list[dict], cfg: dict | None = None,
              spy_close: dict | None = None, sector_close: dict | None = None,
              start: int | None = None):
    """Yield (i, setup) for every bar where a setup exists — the historical
    replay used by recovery_fit.py.  Equivalent to detect_setup(bars[:i+1])
    at every i (proved by test_recovery.py), but O(n) overall."""
    pre = precompute(bars, cfg)
    reg_dates, reg_map = spy_regime_map(spy_close)
    lo = start if start is not None else pre["cfg"]["min_history"] - 1
    for i in range(max(0, lo), len(bars)):
        s = _eval_at(bars, i, pre, spy_close, sector_close, reg_dates, reg_map)
        if s is not None:
            yield i, s


# ── outcome labeling (backtest only — needs bars AFTER the signal) ──────────

def label_outcome(bars: list[dict], i: int, target: float, inval: float,
                  horizon: int = 60, exceed_pct: float = 0.01) -> dict | None:
    """Race target (via highs) vs invalidation (via lows) over the next
    `horizon` bars after signal index i.  Daily bars cannot order touches
    inside one bar: a same-bar double touch is 'ambiguous' and counts
    AGAINST the setup (same convention as patterns.py)."""
    entry = bars[i]["close"]
    if not entry or i + 1 >= len(bars):
        return None
    end = min(len(bars) - 1, i + horizon)
    hit_day = fail_day = None
    ambiguous = False
    mfe = mae = 0.0
    max_high = 0.0
    for k in range(i + 1, end + 1):
        hi, lo = bars[k]["high"], bars[k]["low"]
        mfe = max(mfe, (hi - entry) / entry * 100.0)
        mae = min(mae, (lo - entry) / entry * 100.0)
        max_high = max(max_high, hi)
        up = hi >= target
        dn = lo <= inval
        if up and dn:
            ambiguous = True
            fail_day = k - i          # conservative: counted as a failure
            break
        if up:
            hit_day = k - i
            break
        if dn:
            fail_day = k - i
            break
    win = hit_day is not None
    f20 = min(i + 20, end)
    return {
        "win": win,
        "fail": fail_day is not None,
        "ambiguous": ambiguous,
        "days_to_target": hit_day,
        "days_to_fail": fail_day,
        "bars_available": end - i,
        "mfe": round(mfe, 3),
        "mae": round(mae, 3),
        "fwd20": round((bars[f20]["close"] - entry) / entry * 100.0, 3),
        "fwd30": round((bars[end]["close"] - entry) / entry * 100.0, 3),
        "exceeded": bool(max_high >= target * (1 + exceed_pct)),
        "hit5": bool(win and hit_day is not None and hit_day <= 5),
        "hit10": bool(win and hit_day is not None and hit_day <= 10),
        "hit20": bool(win and hit_day is not None and hit_day <= 20),
        "hit30": bool(win and hit_day is not None and hit_day <= 30),
    }


# ── the probability model (artifact-driven; nothing invented) ───────────────

_MODEL: dict | None = None
_MODEL_TRIED = False
_MODEL_LOCK = threading.Lock()

MODEL_FILE = Path(__file__).resolve().parent / "recovery_model.json"


def _load_model() -> dict | None:
    global _MODEL, _MODEL_TRIED
    with _MODEL_LOCK:
        if _MODEL is not None or _MODEL_TRIED:
            return _MODEL
        _MODEL_TRIED = True
        try:
            _MODEL = json.loads(MODEL_FILE.read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"[recovery] model artifact unavailable: {exc}", file=sys.stderr)
            _MODEL = None
        return _MODEL


def _cfg() -> dict:
    m = _load_model()
    if m and isinstance(m.get("config"), dict):
        return {**CFG, **m["config"]}
    return dict(CFG)


def model_features(setup: dict) -> dict[str, float]:
    """Numeric feature vector the fitted model consumes.  Kept in lockstep
    with recovery_fit.py — scoring refuses to run if any trained feature
    is missing here."""
    def _b(x):
        return 1.0 if x else 0.0
    rv = setup.get("relvol")
    return {
        "recovery_ratio": setup["recovery_ratio"],
        "depth": setup["depth"],
        "dist_to_high": setup["dist_to_high"],
        "has_higher_low": _b(setup.get("has_higher_low")),
        "broke_bounce": _b(setup.get("broke_bounce")),
        "break10": _b(setup.get("break10")),
        "above_ema20": _b(setup.get("above_ema20")),
        "above_ema50": _b(setup.get("above_ema50")),
        "ema20_slope_pos": _b((setup.get("ema20_slope") or 0) > 0),
        "rsi": (setup.get("rsi") or 50.0) / 100.0,
        "rsi_rising": _b(setup.get("rsi_rising")),
        "macd_improving": _b(setup.get("macd_improving")),
        "relvol": min(rv, 5.0) if rv is not None else 1.0,
        "rs_spy": max(-0.5, min(0.5, setup.get("rs_spy") or 0.0)),
        "regime_up": _b(setup.get("regime") == "uptrend"),
        "log_days_since_low": math.log1p(setup.get("days_since_low") or 0),
        "log_significance": math.log1p(setup.get("significance") or 0),
        "trend_before_high": max(-0.5, min(1.5, setup.get("trend_before_high") or 0.0)),
        "atr_pct": min(setup.get("atr_pct") or 0.03, 0.15),
    }


def score_setup(setup: dict) -> dict:
    """Recovery Score (0-100), model probability, and the empirical stats
    bucket for this setup.  Everything traceable to the artifact; returns
    {'available': False, 'reason': ...} when no honest number exists."""
    if setup.get("stage") not in MODEL_STAGES:
        return {"available": False,
                "reason": "model covers Early/Confirmed Recovery stages"}
    m = _load_model()
    if not m or not m.get("weights"):
        return {"available": False, "reason": "no historical model artifact"}
    feats = model_features(setup)
    names = m.get("feature_names") or []
    if not names or any(nm not in feats for nm in names):
        return {"available": False, "reason": "model/feature mismatch"}
    mu = m.get("feature_mean") or {}
    sd = m.get("feature_std") or {}
    z = m.get("intercept") or 0.0
    contribs = {}
    for nm in names:
        s = sd.get(nm) or 1.0
        x = (feats[nm] - (mu.get(nm) or 0.0)) / (s if s > 1e-9 else 1.0)
        w = (m["weights"].get(nm) or 0.0)
        z += w * x
        contribs[nm] = round(w * x, 4)
    p_raw = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

    deciles = m.get("deciles") or []
    bucket = None
    for d in deciles:
        if d["lo"] <= p_raw < d["hi"]:
            bucket = d
            break
    if bucket is None and deciles:
        bucket = deciles[-1] if p_raw >= deciles[-1]["lo"] else deciles[0]
    if not bucket or (bucket.get("n") or 0) < MIN_BUCKET_N:
        return {"available": False, "reason": "insufficient historical sample",
                "p_raw": round(p_raw, 4)}

    p_emp = bucket["p_win"]
    lo = m.get("p_floor", 0.2)
    hi = m.get("p_ceil", 0.8)
    quality = max(0.0, min(1.0, (p_emp - lo) / (hi - lo) if hi > lo else 0.5))
    return {
        "available": True,
        "p_raw": round(p_raw, 4),
        "p_win": round(p_emp, 4),          # target before invalidation, ≤30d
        "p_exceed": bucket.get("p_exceed"),
        "hit5": bucket.get("hit5"), "hit10": bucket.get("hit10"),
        "hit20": bucket.get("hit20"), "hit30": bucket.get("hit30"),
        "median_days": bucket.get("median_days"),
        "avg_days": bucket.get("avg_days"),
        "median_mfe": bucket.get("median_mfe"),
        "median_mae": bucket.get("median_mae"),
        "n": bucket.get("n"),
        "decile": bucket.get("decile"),
        "horizon": (m.get("meta") or {}).get("horizon", 60),
        "recovery_score": round(quality * 100, 1),
        "contribs": contribs,
    }


def opportunity_score(setup: dict, scored: dict) -> float | None:
    """Blend of probability, remaining upside, reward/risk and structural
    quality.  Weights fixed and documented (not fitted): probability is the
    anchor (45%), upside 25%, reward-to-risk 20%, structure 10%."""
    if not scored.get("available"):
        return None
    p = scored["p_win"]
    upside = min((setup.get("upside_pct") or 0.0) / 0.25, 1.0)   # 25%+ caps
    rr = min((setup.get("reward_risk") or 0.0) / 4.0, 1.0)       # 4:1 caps
    quality = (scored.get("recovery_score") or 0.0) / 100.0
    return round(100 * (0.45 * p + 0.25 * upside + 0.20 * rr + 0.10 * quality), 1)


def explain(setup: dict, scored: dict) -> str:
    """Short factual explanation from measured values only."""
    stage_words = {
        "bottoming": "Bottoming", "early": "Early Recovery",
        "confirmed": "Confirmed Recovery", "approaching": "Approaching Prior High",
        "prior_high_test": "Prior High Test", "breakout": "Breakout",
        "failed": "Failed Recovery",
    }
    parts = [stage_words.get(setup["stage"], setup["stage"]) + "."]
    parts.append(f"{setup['depth'] * 100:.1f}% correction from the "
                 f"{setup['prior_high_date']} high.")
    parts.append(f"Recovered {setup['recovery_ratio'] * 100:.0f}% of the decline.")
    if setup.get("has_higher_low"):
        parts.append(f"Formed a higher low at {setup['higher_low']:.2f}.")
    if setup.get("broke_bounce"):
        parts.append(f"Broke the bounce high at {setup['bounce_high']:.2f}.")
    elif setup.get("break5"):
        parts.append("Broke the 5-day high.")
    ema_bits = []
    if setup.get("above_ema9") and (setup.get("ema9_slope") or 0) > 0:
        ema_bits.append("9 EMA")
    if setup.get("above_ema20") and (setup.get("ema20_slope") or 0) > 0:
        ema_bits.append("20 EMA")
    if ema_bits:
        parts.append(f"Above a rising {' and '.join(ema_bits)}.")
    if setup.get("relvol") is not None and setup["relvol"] >= 1.3:
        parts.append(f"Relative volume {setup['relvol']:.1f}.")
    parts.append(f"Currently {setup['dist_to_high'] * 100:.1f}% below the prior high.")
    if scored.get("available"):
        parts.append(f"Historical setups in this score decile reached the "
                     f"prior high before invalidation within "
                     f"{scored.get('horizon', 60)} trading days "
                     f"{scored['p_win'] * 100:.0f}% of the time "
                     f"(sample: {scored['n']}).")
    else:
        parts.append("No probability shown: " +
                     (scored.get("reason") or "insufficient historical data") + ".")
    return " ".join(parts)


# ── bar acquisition ─────────────────────────────────────────────────────────

def _bars_from_schwab(symbol: str, days: int) -> list[dict] | None:
    try:
        c = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
        if c is None:
            return None
        return c.get_price_history(symbol, days=days) or None
    except Exception:  # noqa: BLE001
        return None


def _bars_from_yf(symbol: str, period: str = "2y") -> list[dict] | None:
    if not _YF_OK:
        return None
    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d",
                                       auto_adjust=False)
        return _df_to_bars(df)
    except Exception:  # noqa: BLE001
        return None


def _df_to_bars(df) -> list[dict] | None:
    try:
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if df is None or len(df) < 30:
            return None
        rows = []
        for idx, r in df.iterrows():
            v = r.get("Volume")
            rows.append({"date": str(idx)[:10],
                         "open": float(r["Open"]), "high": float(r["High"]),
                         "low": float(r["Low"]), "close": float(r["Close"]),
                         "volume": int(v) if v == v else 0})
        return rows
    except Exception:  # noqa: BLE001
        return None


def get_bars(symbol: str, days: int = 500) -> list[dict] | None:
    """Schwab-first single-symbol daily bars, yfinance fallback."""
    if os.environ.get("JERRY_NO_NET") == "1":
        return None
    bars = _bars_from_schwab(symbol, days)
    if bars:
        return bars
    period = "2y" if days <= 500 else ("5y" if days <= 1300 else "10y")
    return _bars_from_yf(symbol, period)


# ── universe scan (board scanner, range_scan shape) ─────────────────────────

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {"scanning": False, "scanned": 0, "total": 0,
                          "last_scan": None, "rows": [], "error": None,
                          "universe_size": 0, "spy_regime": None,
                          "sector_trend": {}}
_THREAD: threading.Thread | None = None

_DETAIL_CACHE: dict[str, tuple[float, dict]] = {}
_DETAIL_TTL = 300.0
_DETAIL_LOCK = threading.Lock()

_SCAN_CHUNK = 40


def _sector_map() -> dict[str, str]:
    try:
        board = _BOARD_GETTER() if _BOARD_GETTER else None
        out = {}
        for r in (board or {}).get("rows") or []:
            sym, sec = r.get("symbol"), r.get("sector")
            if sym and sec:
                out[sym.upper()] = sec
        return out
    except Exception:  # noqa: BLE001
        return {}


def _close_map(bars: list[dict] | None) -> dict[str, float]:
    return {b["date"][:10]: b["close"] for b in (bars or [])}


def _etf_trend(closes: dict[str, float] | None) -> dict | None:
    """5- and 20-session % change of a sector ETF — the 'is this group
    moving up' signal shown on sector tags."""
    if not closes or len(closes) < 21:
        return None
    ds = sorted(closes)
    cl = [closes[d] for d in ds]
    if not cl[-6] or not cl[-21]:
        return None
    return {"chg5": round(cl[-1] / cl[-6] - 1, 4),
            "chg20": round(cl[-1] / cl[-21] - 1, 4)}


def _sector_summary(rows: list[dict], sector_trend: dict) -> list[dict]:
    """Group board rows by sector + attach each sector ETF's momentum,
    biggest cluster first."""
    counts: dict[str, int] = {}
    for r in rows:
        sec = r.get("sector") or "Other"
        counts[sec] = counts.get(sec, 0) + 1
    out = []
    for sec, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        etf = _SECTOR_ETF.get(sec)
        tr = (sector_trend or {}).get(etf) if etf else None
        out.append({"sector": sec, "etf": etf, "count": cnt,
                    "chg5": tr.get("chg5") if tr else None,
                    "chg20": tr.get("chg20") if tr else None})
    return out


def _build_row(symbol: str, bars: list[dict] | None, spy_close: dict,
               sector_closes: dict, sectors: dict, cfg: dict) -> dict | None:
    if not bars or len(bars) < cfg["min_history"]:
        return None
    sec = sectors.get(symbol)
    etf = _SECTOR_ETF.get(sec or "")
    setup = detect_setup(bars, cfg, spy_close=spy_close,
                         sector_close=sector_closes.get(etf))
    if setup is None:
        return None
    if setup["stage"] in ("breakout", "failed"):
        return None
    if not (cfg["rr_lo"] <= setup["recovery_ratio"] < cfg["rr_hi"]):
        return None
    if setup["days_since_low"] < cfg["min_days_since_low"]:
        return None
    if (setup.get("dollar_vol") or 0) < cfg["min_dollar_vol"]:
        return None
    scored = score_setup(setup)
    opp = opportunity_score(setup, scored)
    return {
        "ticker": symbol,
        "sector": sec,
        **{k: setup[k] for k in (
            "close", "prior_high", "prior_high_date", "corr_low",
            "corr_low_date", "depth", "recovery_ratio", "dist_to_high",
            "days_since_high", "days_since_low", "stage", "has_higher_low",
            "broke_bounce", "bounce_high", "higher_low", "relvol", "rs_spy",
            "invalidation", "inval_basis", "risk_pct", "upside_pct",
            "reward_risk", "significance", "evidence")},
        "prob": scored if scored.get("available") else
                {"available": False, "reason": scored.get("reason")},
        "opportunity": opp,
        "explain": explain(setup, scored),
    }


def _scan_worker(symbols: list[str]) -> None:
    if _AB_OK:
        analyst_board.HEAVY_SCAN_LOCK.acquire()
    try:
        cfg = _cfg()
        sectors = _sector_map()
        spy_close = _close_map(_bars_from_yf("SPY", "2y")
                               or _bars_from_schwab("SPY", 500))
        etfs = sorted({_SECTOR_ETF[s] for s in sectors.values()
                       if s in _SECTOR_ETF})
        sector_closes: dict[str, dict] = {}
        for etf in etfs:
            m = _close_map(_bars_from_yf(etf, "2y"))
            if m:
                sector_closes[etf] = m
        sector_trend = {etf: tr for etf, m in sector_closes.items()
                        if (tr := _etf_trend(m))}
        rows: list[dict] = []
        for i in range(0, len(symbols), _SCAN_CHUNK):
            part = symbols[i:i + _SCAN_CHUNK]
            df = None
            try:
                df = yf.download(" ".join(part), period="2y", interval="1d",
                                 progress=False, group_by="ticker",
                                 threads=False, auto_adjust=False)
                multi = isinstance(df.columns, pd.MultiIndex)
                for sym in part:
                    try:
                        bars = _df_to_bars(df[sym] if multi else df)
                        r = _build_row(sym, bars, spy_close, sector_closes,
                                       sectors, cfg)
                        if r:
                            rows.append(r)
                    except Exception:  # noqa: BLE001
                        continue
            except Exception as exc:  # noqa: BLE001
                print(f"[recovery] chunk {i // _SCAN_CHUNK}: {exc}",
                      file=sys.stderr)
            finally:
                del df
            with _LOCK:
                _STATE["scanned"] = min(len(symbols), i + _SCAN_CHUNK)
            time.sleep(0.3)
        regime = None
        if spy_close:
            ds = sorted(spy_close)
            cl = [spy_close[d] for d in ds]
            if len(cl) >= 200:
                s50 = sum(cl[-50:]) / 50
                s200 = sum(cl[-200:]) / 200
                regime = ("uptrend" if cl[-1] > s200 and s50 > s200
                          else "downtrend" if cl[-1] < s200 else "chop")
        rows.sort(key=lambda r: -(r["opportunity"]
                                  if r.get("opportunity") is not None else -1))
        with _LOCK:
            _STATE.update({"rows": rows, "last_scan": _now_iso(),
                           "error": None, "spy_regime": regime,
                           "sector_trend": sector_trend})
        _persist_board()
        import gc
        gc.collect()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE["error"] = str(exc)
    finally:
        with _LOCK:
            _STATE["scanning"] = False
        if _AB_OK:
            analyst_board.HEAVY_SCAN_LOCK.release()


def trigger_scan(symbols: list[str] | None = None, force: bool = False) -> dict:
    global _THREAD
    if os.environ.get("JERRY_NO_NET") == "1":
        return {"started": False, "reason": "network disabled (JERRY_NO_NET)"}
    if not _YF_OK:
        return {"started": False, "reason": "yfinance unavailable"}
    with _LOCK:
        if _STATE["scanning"] and not force:
            return {"started": False, "reason": "already scanning"}
        syms = [s.upper() for s in dict.fromkeys(symbols or []) if s]
        if not syms:
            return {"started": False, "reason": "watchlist empty"}
        _STATE.update({"scanning": True, "scanned": 0, "total": len(syms),
                       "universe_size": len(syms), "error": None})
    _THREAD = threading.Thread(target=_scan_worker, args=(syms,),
                               daemon=True, name="recovery-scan")
    _THREAD.start()
    return {"started": True, "total": len(syms)}


def get_board() -> dict:
    with _LOCK:
        rows = list(_STATE["rows"])
        status = {k: _STATE[k] for k in ("scanning", "scanned", "total",
                                         "last_scan", "universe_size", "error")}
        regime = _STATE.get("spy_regime")
        sector_trend = dict(_STATE.get("sector_trend") or {})
    return {
        "as_of": _now_iso(),
        "status": status,
        "count": len(rows),
        "rows": rows,
        "sectors": _sector_summary(rows, sector_trend),
        "spy_regime": regime,
        "model": _model_meta(_load_model()),
        "note": ("Universe = your watchlist. Levels and structure are measured "
                 "from split-adjusted daily bars; probabilities are empirical "
                 "hit rates from the historical study in Research (sample "
                 "sizes shown). The study uses today's watchlist, so delisted "
                 "stocks are absent — absolute hit rates run a little hot."),
    }


def _model_meta(m: dict | None) -> dict:
    if not m:
        return {"available": False,
                "reason": "recovery_model.json missing — probabilities disabled"}
    meta = m.get("meta") or {}
    return {"available": True,
            "fitted": meta.get("fitted"),
            "data_from": meta.get("data_from"), "data_to": meta.get("data_to"),
            "n_signals": meta.get("n_signals"),
            "n_symbols": meta.get("n_symbols"),
            "test_period": meta.get("test_period"),
            "test_auc": meta.get("test_auc"),
            "test_brier": meta.get("test_brier"),
            "limitations": meta.get("limitations")}


# ── research payload (cohort tables from the artifact) ──────────────────────

def research() -> dict:
    m = _load_model()
    if not m:
        return {"available": False,
                "reason": "no historical study artifact (recovery_model.json)"}
    return {
        "available": True,
        "meta": _model_meta(m),
        "config": _cfg(),
        "deciles": m.get("deciles"),
        "cohorts": m.get("cohorts"),
        "feature_report": m.get("feature_report"),
        "regimes": m.get("regimes"),
        "stability": m.get("stability"),
        "sweep": m.get("sweep"),
        "notes": m.get("notes") or [],
    }


# ── per-symbol detail (chart levels + full explanation) ─────────────────────

def detail(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"error": "symbol required"}
    now = time.monotonic()
    with _DETAIL_LOCK:
        hit = _DETAIL_CACHE.get(symbol)
        if hit and now - hit[0] < _DETAIL_TTL:
            return hit[1]
    if os.environ.get("JERRY_NO_NET") == "1":
        return {"symbol": symbol,
                "error": "network disabled (JERRY_NO_NET) — no data fetched, "
                         "nothing fabricated"}
    bars = get_bars(symbol, days=500)
    cfg = _cfg()
    if not bars or len(bars) < cfg["min_history"]:
        return {"symbol": symbol, "error": "not enough price history"}
    spy_close = _close_map(_bars_from_yf("SPY", "2y")
                           or _bars_from_schwab("SPY", 500))
    sectors = _sector_map()
    etf = _SECTOR_ETF.get(sectors.get(symbol) or "")
    setup = detect_setup(bars, cfg, spy_close=spy_close,
                         sector_close=_close_map(_bars_from_yf(etf, "2y")) if etf else None)
    if setup is None:
        out = {"symbol": symbol, "setup": None,
               "note": "No qualifying prior-high / correction structure "
                       "right now (needs a significant high plus a "
                       f"{cfg['min_depth'] * 100:.0f}%+ correction)."}
    else:
        scored = score_setup(setup)
        levels = [
            {"price": setup["prior_high"], "label": "Prior high",
             "kind": "prior_high"},
            {"price": setup["corr_low"], "label": "Correction low",
             "kind": "correction_low"},
            {"price": setup["invalidation"], "label": "Invalidation",
             "kind": "invalidation"},
        ]
        if setup.get("bounce_high"):
            levels.append({"price": setup["bounce_high"], "label": "Bounce high",
                           "kind": "bounce_high"})
        if setup.get("higher_low"):
            levels.append({"price": setup["higher_low"], "label": "Higher low",
                           "kind": "higher_low"})
        out = {"symbol": symbol, "setup": setup,
               "prob": scored if scored.get("available") else
                       {"available": False, "reason": scored.get("reason")},
               "opportunity": opportunity_score(setup, scored),
               "explain": explain(setup, scored),
               "levels": levels}
    with _DETAIL_LOCK:
        _DETAIL_CACHE[symbol] = (now, out)
        if len(_DETAIL_CACHE) > 64:
            oldest = min(_DETAIL_CACHE, key=lambda k: _DETAIL_CACHE[k][0])
            _DETAIL_CACHE.pop(oldest, None)
    return out


# ── board persistence (restart survival) ────────────────────────────────────

def _board_path() -> Path | None:
    return (_DATA_DIR / "board.json") if _DATA_DIR else None


def _persist_board() -> None:
    p = _board_path()
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            payload = {"rows": _STATE["rows"], "last_scan": _STATE["last_scan"],
                       "spy_regime": _STATE.get("spy_regime"),
                       "sector_trend": _STATE.get("sector_trend") or {}}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(p)
    except Exception as exc:  # noqa: BLE001
        print(f"[recovery] persist failed: {exc}", file=sys.stderr)


def _restore_board() -> None:
    p = _board_path()
    if not p:
        return
    try:
        if p.exists():
            payload = json.loads(p.read_text())
            with _LOCK:
                if not _STATE["rows"]:
                    _STATE["rows"] = payload.get("rows") or []
                    _STATE["last_scan"] = payload.get("last_scan")
                    _STATE["spy_regime"] = payload.get("spy_regime")
                    _STATE["sector_trend"] = payload.get("sector_trend") or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[recovery] restore failed: {exc}", file=sys.stderr)
