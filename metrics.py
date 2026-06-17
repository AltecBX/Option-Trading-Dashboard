"""metrics.py (v1.40) — pure math split out of the options_dashboard
monolith: EMA, RSI, ATR indicators and the Black-Scholes price and
greeks used for chain backfill and repricing. Stdlib math only, no
state, no I/O. options_dashboard re-imports every name so call sites
are unchanged.
"""

import math


def _ema(series: list[float], period: int) -> list[float | None]:
    """Standard EMA: SMA-seeded for the first `period` bars, then
    recursive EMA = price × k + prior × (1-k), where k = 2/(period+1).
    Returns a list aligned with input; None for warm-up bars."""
    n = len(series)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1.0)
    sma = sum(series[:period]) / period
    out[period - 1] = sma
    for i in range(period, n):
        out[i] = series[i] * k + out[i - 1] * (1.0 - k)
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI(14). None until period bars have passed."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    gains = []
    losses = []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        gain = max(d, 0.0)
        loss = max(-d, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _atr(bars: list[dict], period: int = 14) -> list[float | None]:
    """Wilder ATR(14)."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    trs = [None]
    for i in range(1, n):
        h = bars[i]["high"]; l = bars[i]["low"]; pc = bars[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    out[period] = sum(trs[1:period + 1]) / period
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + trs[i]) / period
    return out


def _norm_cdf(x: float) -> float:
    # Abramowitz & Stegun 7.1.26 approximation — no scipy dependency
    import math
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1.0 + sign * y)


def _bs_delta(spot: float, strike: float, T: float, sigma: float, side: str, r: float = 0.045) -> float:
    """Black-Scholes delta. Falls back to 0.5 / -0.5 for invalid inputs."""
    import math
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.5 if side == "call" else -0.5
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    cd = _norm_cdf(d1)
    return cd if side == "call" else cd - 1.0


def _bs_theta(spot: float, strike: float, T: float, sigma: float, side: str, r: float = 0.045) -> float:
    """Black-Scholes theta in dollars per CALENDAR DAY (not per-year). Used
    so a typical short-dated short premium shows a familiar small negative
    number like -0.08 rather than the per-year -29.20. Returns 0 on invalid
    inputs so the UI never shows NaN."""
    import math
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    term1 = -(spot * pdf_d1 * sigma) / (2 * sqrtT)
    if side == "call":
        theta_year = term1 - r * strike * math.exp(-r * T) * _norm_cdf(d2)
    else:
        theta_year = term1 + r * strike * math.exp(-r * T) * _norm_cdf(-d2)
    return theta_year / 365.0


def _bs_gamma(spot: float, strike: float, T: float, sigma: float, r: float = 0.045) -> float:
    """Black-Scholes gamma. Same for calls and puts."""
    import math
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return pdf_d1 / (spot * sigma * sqrtT)


def _bs_vega(spot: float, strike: float, T: float, sigma: float, r: float = 0.045) -> float:
    """Black-Scholes vega per 1% IV change (i.e. /100). Same for calls and
    puts. Standard vega is per 1.0 IV change — dividing by 100 gives the
    familiar 'dollars per share per vol point' that brokers display."""
    import math
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return spot * pdf_d1 * sqrtT / 100.0


def _bs_price(spot: float, strike: float, T: float, sigma: float, side: str, r: float = 0.045) -> float:
    """Black-Scholes theoretical option price. Returns intrinsic value for
    invalid inputs (T<=0 or sigma<=0). Used for synthetic backtesting where
    we don't have real bid/ask history.
    """
    import math
    if spot <= 0 or strike <= 0:
        return 0.0
    # Expired or zero-vol: collapse to intrinsic value.
    if T <= 0 or sigma <= 0:
        if side == "call":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    if side == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * T) * _norm_cdf(d2)
    return strike * math.exp(-r * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
