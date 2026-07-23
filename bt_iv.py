"""bt_iv.py — implied-volatility model v2 for backtests (Backtest v2, B2).

Replaces the flat HV20×1.1 proxy with a layered, LABELED model. Every
component is either calibrated from data the app actually has, or a
documented assumption — and the result says which was used, so the UI can
show "calibrated per-symbol" vs "assumed default" honestly.

    iv(day) = blend(HV20, HV60)            # base realized level
              × ratio                       # per-symbol IV/HV, calibrated
                                            #   from the app's own stored
                                            #   daily IV30 history (n≥20),
                                            #   else 1.10 assumed
              × vix_scaler(day)             # vol-regime scaling from ^VIX
                                            #   percentile (0.85…1.20)
              × earnings_ramp(day)          # IV builds into a known report
                                            #   (+35% peak, documented
                                            #   default) and crushes after
    floored at 8% annualized.

Pure functions over injected series — no network, fully unit-testable.
"""
from __future__ import annotations

import math
from datetime import date

IV_FLOOR = 0.08
DEFAULT_RATIO = 1.10          # assumed IV/HV when no calibration history
RATIO_BOUNDS = (0.8, 2.0)     # calibration clamped to sane territory
VIX_SCALE_RANGE = (0.85, 1.20)
EARNINGS_RAMP_PEAK = 0.35     # +35% IV at the report (documented default)
EARNINGS_RAMP_DAYS = 7        # ramp builds over the final week


def hv(closes: list, i: int, n: int) -> float | None:
    """Annualized realized vol of log returns over the n bars ending at i."""
    if i < n:
        return None
    rets = []
    for k in range(i - n + 1, i + 1):
        if closes[k - 1] and closes[k - 1] > 0 and closes[k] > 0:
            rets.append(math.log(closes[k] / closes[k - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 252)


def calibrate_ratio(iv_history: list, closes_by_date: dict) -> tuple[float, str]:
    """Per-symbol IV/HV ratio from the app's stored daily IV30 snapshots
    (storage.iv_history rows: {"date","iv"}). Matches each stored IV30
    against HV20 computed on that date; returns (ratio, "calibrated (n=N)")
    with n≥20 matched observations, else (DEFAULT_RATIO, "assumed")."""
    if not iv_history or not closes_by_date:
        return DEFAULT_RATIO, "assumed"
    dates = sorted(closes_by_date.keys())
    closes = [closes_by_date[d] for d in dates]
    idx = {d: i for i, d in enumerate(dates)}
    ratios = []
    for row in iv_history:
        d = str(row.get("date") or "")[:10]
        iv = row.get("iv")
        i = idx.get(d)
        if iv is None or iv <= 0 or i is None:
            continue
        h = hv(closes, i, 20)
        if h and h > 0.02:
            ratios.append(iv / h)
    if len(ratios) < 20:
        return DEFAULT_RATIO, "assumed"
    ratios.sort()
    med = ratios[len(ratios) // 2]
    lo, hi = RATIO_BOUNDS
    return max(lo, min(hi, med)), f"calibrated (n={len(ratios)})"


def vix_scaler_series(dates: list, vix_by_date: dict) -> tuple[list, str]:
    """Per-day regime multiplier from the ^VIX close's percentile within
    its trailing year: low-vol regimes price options a bit under the
    realized-vol base, panic regimes well over. Linear map of the
    percentile onto VIX_SCALE_RANGE. Missing VIX data → flat 1.0, labeled."""
    if not vix_by_date:
        return [1.0] * len(dates), "unavailable (flat 1.0)"
    vdates = sorted(vix_by_date.keys())
    vcloses = [vix_by_date[d] for d in vdates]
    vidx = {d: i for i, d in enumerate(vdates)}
    out = []
    lo, hi = VIX_SCALE_RANGE
    last = 1.0
    for d in dates:
        i = vidx.get(d)
        if i is None or i < 25:
            out.append(last)
            continue
        w = vcloses[max(0, i - 252):i + 1]
        below = sum(1 for v in w if v < vcloses[i])
        pct = below / len(w)
        s = lo + (hi - lo) * pct
        out.append(s)
        last = s
    return out, "VIX percentile (trailing year)"


def earnings_mult(d: date, earnings_dates: list) -> float:
    """IV ramp into a KNOWN report date: rises linearly over the final
    EARNINGS_RAMP_DAYS to ×(1+PEAK) on the day before the print, and back
    to ×1.0 from the day after (the crush is the disappearance of the
    ramp). Dates after are unaffected — realized-vol input already
    captures the post-print reality."""
    best = None
    for e in earnings_dates:
        delta = (e - d).days
        if 0 <= delta <= EARNINGS_RAMP_DAYS:
            best = delta if best is None else min(best, delta)
    if best is None:
        return 1.0
    return 1.0 + EARNINGS_RAMP_PEAK * (1.0 - best / EARNINGS_RAMP_DAYS)


def build_iv_series(bars: list, ratio: float, vix_scalers: list,
                    earnings_dates: list | None = None) -> list:
    """Per-bar annualized IV for the lifecycle engine. iv[k] uses ONLY
    data through bar k (no look-ahead); earnings dates are known in
    advance in reality, so using future report DATES is legitimate."""
    closes = [b["close"] for b in bars]
    edates = []
    for x in (earnings_dates or []):
        try:
            edates.append(date.fromisoformat(str(x)[:10]))
        except (ValueError, TypeError):
            continue
    out = []
    last = None
    for k, b in enumerate(bars):
        h20 = hv(closes, k, 20)
        h60 = hv(closes, k, 60)
        base = (0.6 * h20 + 0.4 * h60) if (h20 and h60) else (h20 or h60)
        if base is None:
            out.append(last)
            continue
        iv = base * ratio * (vix_scalers[k] if k < len(vix_scalers) else 1.0)
        iv *= earnings_mult(date.fromisoformat(b["date"][:10]), edates)
        iv = max(IV_FLOOR, iv)
        out.append(iv)
        last = iv
    return out


def model_meta(ratio: float, ratio_src: str, vix_src: str,
               has_earnings: bool) -> list:
    """Human-readable assumption lines for the result's modeled block."""
    return [
        f"IV base: 0.6·HV20 + 0.4·HV60 × {ratio:.2f} IV/HV ratio ({ratio_src})",
        f"Vol-regime scaling: {vix_src}",
        ("Earnings IV ramp: +%d%% into known report dates over the final %d days, "
         "crush after (documented default)" % (int(EARNINGS_RAMP_PEAK * 100), EARNINGS_RAMP_DAYS))
        if has_earnings else "Earnings IV ramp: no report dates available for this run",
        f"IV floor {IV_FLOOR:.0%} annualized",
    ]
