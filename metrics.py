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


# ── Options-math contract (v2.0) ─────────────────────────────────────────
# THE canonical Black-Scholes implementation for the whole app. Every other
# Python copy (option_reprice.py, backtest.py) now imports from here; the
# small JS engine in strategies.jsx is fixture-matched against this file by
# test_strategy_fixtures.js. Conventions, stated once:
#   • time:   T in YEARS = calendar_days / 365 (year_fraction helper).
#   • sigma:  annualized DECIMAL vol (0.32 = 32%). normalize_iv() validates.
#   • r:      annualized continuously-compounded rate, DECIMAL. Callers pass
#             risk_free_rate()[0]; the legacy default stays 0.045 so existing
#             call sites are bit-for-bit unchanged until they opt in.
#   • q:      continuous dividend yield, DECIMAL, default 0.
#   • theta:  dollars per CALENDAR day (year theta / 365).
#   • vega:   dollars per 1 vol POINT (standard vega / 100).

DAYS_PER_YEAR = 365.0
RISK_FREE_FALLBACK = 0.04          # used only when no live curve is wired
_RATE_PROVIDER = None              # callable -> (rate_decimal, source_str)


def year_fraction(calendar_days: float) -> float:
    """Calendar-day year fraction — the app-wide time convention."""
    return max(0.0, calendar_days) / DAYS_PER_YEAR


def set_rate_provider(fn) -> None:
    """Wire a live risk-free source: fn() -> (rate_decimal, source_str)."""
    global _RATE_PROVIDER
    _RATE_PROVIDER = fn


def risk_free_rate() -> tuple[float, str]:
    """(rate, source). Live/recently-cached 3-month Treasury yield when the
    provider is wired and healthy; otherwise a clearly-labeled fallback."""
    if _RATE_PROVIDER is not None:
        try:
            got = _RATE_PROVIDER()
            if got and got[0] is not None and 0.0 <= got[0] <= 0.25:
                return float(got[0]), str(got[1])
        except Exception:
            pass
    return RISK_FREE_FALLBACK, f"fallback constant {RISK_FREE_FALLBACK:.2%} (live curve unavailable)"


def normalize_iv(iv) -> float | None:
    """One IV validator for the whole app. Accepts decimal (0.32) or percent
    (32.0) forms; returns annualized DECIMAL or None when unusable.
    Threshold: values ≥ 3 are treated as percent-form (a real 300%+ decimal
    IV is rarer than a 3-point percent-form IV; documented tradeoff)."""
    try:
        v = float(iv)
    except (TypeError, ValueError):
        return None
    if v != v or v <= 0:
        return None
    if v >= 3.0:
        v = v / 100.0
    if v <= 0 or v > 10.0:            # >1000% → stale/garbage quote
        return None
    return v


def rank_and_percentile(history: list[float], current: float) -> dict | None:
    """THE shared rank implementation (was triplicated across storage.py,
    ivrank.py and an inline copy in options_dashboard.py).
    rank       = (current − min) / (max − min) × 100   (50 when flat)
    percentile = share of history strictly below current × 100
    Returns {"rank","percentile","n","min","max"} or None if n < 20."""
    vals = [v for v in history if v is not None and v == v]
    n = len(vals)
    if n < 20:
        return None
    lo, hi = min(vals), max(vals)
    rank = 50.0 if hi <= lo else (current - lo) / (hi - lo) * 100.0
    pct = sum(1 for v in vals if v < current) / n * 100.0
    return {"rank": round(max(0.0, min(100.0, rank)), 1),
            "percentile": round(pct, 1), "n": n,
            "min": round(lo, 4), "max": round(hi, 4)}


def one_sigma_move(spot: float, iv, calendar_days: float) -> float | None:
    """One-standard-deviation move in DOLLARS: S·σ·√T. This is the ONLY
    thing the app calls a '1σ move'. The ATM straddle price is a separate,
    differently-named measure (≈1.25σ under BS) — never interchangeable."""
    sigma = normalize_iv(iv)
    if not spot or spot <= 0 or sigma is None:
        return None
    T = year_fraction(calendar_days)
    if T <= 0:
        return None
    import math
    return spot * sigma * math.sqrt(T)


def _norm_cdf(x: float) -> float:
    # Exact via math.erf (v2.0 — replaces the A&S 7.1.26 approximation; the
    # two agree to ~7.5e-8, but exact means every Python module now shares
    # one definition with zero drift).
    import math
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_delta(spot: float, strike: float, T: float, sigma: float, side: str,
              r: float = 0.045, q: float = 0.0) -> float:
    """Black-Scholes delta (continuous dividend yield q). Falls back to
    0.5 / -0.5 for invalid inputs."""
    import math
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.5 if side == "call" else -0.5
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    dq = math.exp(-q * T)
    cd = _norm_cdf(d1)
    return dq * cd if side == "call" else dq * (cd - 1.0)


def _bs_theta(spot: float, strike: float, T: float, sigma: float, side: str,
              r: float = 0.045, q: float = 0.0) -> float:
    """Black-Scholes theta in dollars per CALENDAR DAY (not per-year). Used
    so a typical short-dated short premium shows a familiar small negative
    number like -0.08 rather than the per-year -29.20. Returns 0 on invalid
    inputs so the UI never shows NaN."""
    import math
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    dq = math.exp(-q * T)
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    term1 = -(spot * dq * pdf_d1 * sigma) / (2 * sqrtT)
    if side == "call":
        theta_year = term1 - r * strike * math.exp(-r * T) * _norm_cdf(d2) \
            + q * spot * dq * _norm_cdf(d1)
    else:
        theta_year = term1 + r * strike * math.exp(-r * T) * _norm_cdf(-d2) \
            - q * spot * dq * _norm_cdf(-d1)
    return theta_year / 365.0


def _bs_gamma(spot: float, strike: float, T: float, sigma: float,
              r: float = 0.045, q: float = 0.0) -> float:
    """Black-Scholes gamma. Same for calls and puts."""
    import math
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return math.exp(-q * T) * pdf_d1 / (spot * sigma * sqrtT)


def _bs_vega(spot: float, strike: float, T: float, sigma: float,
             r: float = 0.045, q: float = 0.0) -> float:
    """Black-Scholes vega per 1% IV change (i.e. /100). Same for calls and
    puts. Standard vega is per 1.0 IV change — dividing by 100 gives the
    familiar 'dollars per share per vol point' that brokers display."""
    import math
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return spot * math.exp(-q * T) * pdf_d1 * sqrtT / 100.0


def _bs_price(spot: float, strike: float, T: float, sigma: float, side: str,
              r: float = 0.045, q: float = 0.0) -> float:
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
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    dq = math.exp(-q * T)
    if side == "call":
        return spot * dq * _norm_cdf(d1) - strike * math.exp(-r * T) * _norm_cdf(d2)
    return strike * math.exp(-r * T) * _norm_cdf(-d2) - spot * dq * _norm_cdf(-d1)
