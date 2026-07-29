"""credit_risk.py — Bloomberg-style credit monitor WITHOUT Bloomberg.

Honesty first: single-name CDS quotes (e.g. "NVDA 5Y CDS") are OTC dealer
data sold by S&P Global/Markit, Bloomberg and ICE — no free source exists,
so this app does NOT show "the CDS". It shows the three things the CDS is
made of, from data we legitimately have:

  1. MERTON MODEL 5Y SPREAD — the standard structural-credit estimate
     (Moody's-KMV lineage): treat equity as a call option on the firm's
     assets; solve for asset value/vol from market cap + equity vol +
     debt; the model's risky-bond discount → a credit spread in bps.
     Computed DAILY over the past year → a trendable series.
  2. CRASH-PUT SKEW — dealers hedge CDS with deep OTM puts, so the cost
     of downside protection in the listed options market is the live
     equity-market credit-fear gauge (computed by the caller from the
     chain; helpers here).
  3. REAL traded credit-index spreads (ICE BofA IG/BBB/HY OAS via FRED)
     — fetched by the endpoint layer, genuinely traded data.

Every output is labeled with its inputs and its nature (model vs market).
Pure math here — no network; the endpoint layer injects data.
"""
from __future__ import annotations

import math

from metrics import _norm_cdf, year_fraction

T_YEARS = 5.0                 # the classic 5Y tenor
LGD = 0.60                    # loss-given-default assumption (40% recovery,
                              # the CDS market's standard quoting convention)
MIN_LEVERAGE = 0.005          # debt below 0.5% of assets → spread ≈ 0, flagged


def merton_solve(equity: float, equity_vol: float, debt: float,
                 r: float, t_years: float = T_YEARS,
                 iters: int = 200) -> dict | None:
    """Solve the Merton two-equation system for asset value V and asset
    vol σV given observed equity value E, equity vol σE and the debt face
    D (default point):
        E = V·Φ(d1) − D·e^{−rT}·Φ(d2)
        σE·E = Φ(d1)·σV·V
    Fixed-point iteration (robust for the leverage ranges of listed
    equities). Returns None when inputs are degenerate."""
    if equity <= 0 or equity_vol <= 0 or debt < 0 or t_years <= 0:
        return None
    if debt == 0:
        return {"V": equity, "vol_V": equity_vol, "d1": float("inf"),
                "d2": float("inf"), "pd": 0.0, "dd": float("inf"),
                "spread_bps": 0.0, "leverage": 0.0}
    sqrtT = math.sqrt(t_years)

    def sigma_v_for(V: float) -> float:
        """Inner fixed point: σV consistent with (V, σE) via the delta
        relation σE·E = Φ(d1)·σV·V. Converges in a handful of steps."""
        sv = max(1e-4, equity_vol * equity / V)
        for _ in range(60):
            d1_ = (math.log(V / debt) + (r + 0.5 * sv * sv) * t_years) / (sv * sqrtT)
            nd1 = _norm_cdf(d1_)
            sv_new = equity_vol * equity / max(1e-12, V * nd1)
            sv_new = min(sv_new, 8.0)
            if abs(sv_new - sv) < 1e-10:
                return sv_new
            sv = sv_new
        return sv

    def model_equity(V: float) -> float:
        sv = sigma_v_for(V)
        d1_ = (math.log(V / debt) + (r + 0.5 * sv * sv) * t_years) / (sv * sqrtT)
        d2_ = d1_ - sv * sqrtT
        return V * _norm_cdf(d1_) - debt * math.exp(-r * t_years) * _norm_cdf(d2_)

    # Outer bisection on V: model equity is increasing in V. Bounds:
    # V=E (equity floor: assets at least the equity) gives model ≤ E;
    # V=E+D gives model ≥ E (debt worth at most its discounted face).
    lo, hi = equity, equity + debt
    for _ in range(iters):
        V = 0.5 * (lo + hi)
        if model_equity(V) < equity:
            lo = V
        else:
            hi = V
    V = 0.5 * (lo + hi)
    vol_V = sigma_v_for(V)
    d1 = (math.log(V / debt) + (r + 0.5 * vol_V * vol_V) * t_years) / (vol_V * sqrtT)
    d2 = d1 - vol_V * sqrtT
    pd = _norm_cdf(-d2)                     # risk-neutral 5Y default prob
    # Merton credit spread: yield of the risky zero over the risk-free.
    #   B = V·Φ(−d1) + D·e^{−rT}·Φ(d2);  s = −(1/T)·ln(B / (D·e^{−rT}))
    B = V * _norm_cdf(-d1) + debt * math.exp(-r * t_years) * _norm_cdf(d2)
    Drf = debt * math.exp(-r * t_years)
    spread = -math.log(max(1e-12, B / Drf)) / t_years if Drf > 0 else 0.0
    return {"V": V, "vol_V": vol_V, "d1": d1, "d2": d2,
            "dd": d2,                        # distance to default (5Y, risk-neutral)
            "pd": pd,
            "spread_bps": max(0.0, spread * 10_000),
            "leverage": debt / V if V > 0 else None}


def default_point(short_debt: float | None, long_debt: float | None,
                  total_debt: float | None) -> float | None:
    """KMV convention: default point = short-term debt + ½ long-term debt.
    Falls back to 75% of total debt when the split isn't available
    (documented approximation)."""
    if short_debt is not None or long_debt is not None:
        return (short_debt or 0.0) + 0.5 * (long_debt or 0.0)
    if total_debt is not None:
        return 0.75 * total_debt
    return None


def equity_vol_series(closes: list, window: int = 60) -> list:
    """Rolling annualized equity vol (HV{window}) aligned to closes;
    None during warm-up."""
    out = [None] * len(closes)
    rets = [None]
    for i in range(1, len(closes)):
        rets.append(math.log(closes[i] / closes[i - 1])
                    if closes[i - 1] and closes[i - 1] > 0 and closes[i] > 0 else 0.0)
    for i in range(window, len(closes)):
        w = [x for x in rets[i - window + 1:i + 1] if x is not None]
        if len(w) < window // 2:
            continue
        m = sum(w) / len(w)
        var = sum((x - m) ** 2 for x in w) / (len(w) - 1)
        out[i] = math.sqrt(var * 252)
    return out


def merton_series(bars: list, shares_out: float, debt_point: float,
                  r: float, vol_window: int = 60) -> list:
    """Daily model-spread series over the given bars: market cap from
    shares × close (CURRENT share count — labeled approximation), rolling
    HV, constant most-recent default point (quarterly data barely moves
    inside a year). → [{date, spread_bps, dd, pd, leverage}]"""
    if not bars or not shares_out or shares_out <= 0 or debt_point is None:
        return []
    closes = [b["close"] for b in bars]
    vols = equity_vol_series(closes, vol_window)
    out = []
    for i, b in enumerate(bars):
        if vols[i] is None or not closes[i]:
            continue
        E = closes[i] * shares_out
        m = merton_solve(E, vols[i], debt_point, r)
        if m is None:
            continue
        out.append({"date": b["date"][:10],
                    "spread_bps": round(m["spread_bps"], 1),
                    "dd": round(m["dd"], 2) if math.isfinite(m["dd"]) else None,
                    "pd_pct": round(m["pd"] * 100.0, 3),
                    "leverage_pct": round((m["leverage"] or 0) * 100.0, 1)})
    return out


def skew_gauge(calls: list, puts: list, spot: float, dte: float) -> dict | None:
    """Equity-market credit-fear gauge from ONE live chain expiry:
      • 25Δ risk reversal (put IV − call IV, vol pts) — classic skew.
      • Crash insurance: annualized cost of the ~20%-OTM put, % of spot —
        what the market charges per year to insure a >20% collapse.
    Dealer CDS hedging flows show up here first."""
    if not calls or not puts or not spot or spot <= 0 or dte <= 0:
        return None

    def by_delta(rows, target):
        best, bd = None, 9e9
        for r_ in rows:
            d = r_.get("delta")
            iv = r_.get("iv")
            if d is None or iv is None or not iv:
                continue
            diff = abs(abs(float(d)) - target)
            if diff < bd:
                best, bd = r_, diff
        return best

    p25 = by_delta(puts, 0.25)
    c25 = by_delta(calls, 0.25)
    out = {}
    if p25 is not None and c25 is not None and p25.get("iv") and c25.get("iv"):
        out["rr25_vol_pts"] = round((float(p25["iv"]) - float(c25["iv"])) * 100.0, 2)
        out["put25_iv"] = round(float(p25["iv"]) * 100.0, 1)
        out["call25_iv"] = round(float(c25["iv"]) * 100.0, 1)
    # ~20% OTM put cost, annualized
    kk = spot * 0.80
    best, bd = None, 9e9
    for r_ in puts:
        k = r_.get("strike")
        if not k:
            continue
        mid = None
        b_, a_ = float(r_.get("bid") or 0), float(r_.get("ask") or 0)
        if b_ > 0 and a_ > 0:
            mid = (b_ + a_) / 2
        if mid is None or mid <= 0:
            continue
        if abs(k - kk) < bd:
            best, bd = (k, mid), abs(k - kk)
    if best and spot > 0:
        k, mid = best
        ann = mid / spot * (365.0 / max(dte, 1.0)) * 100.0
        out["crash_put"] = {"strike": k, "otm_pct": round((1 - k / spot) * 100.0, 1),
                            "cost_pct_of_spot": round(mid / spot * 100.0, 2),
                            "annualized_pct": round(ann, 2)}
    return out or None


def interpret(series: list, leverage_pct: float | None) -> str:
    """One honest sentence about what the model spread is doing."""
    if not series:
        return "Model spread unavailable — missing debt or price history."
    cur = series[-1]["spread_bps"]
    if leverage_pct is not None and leverage_pct < MIN_LEVERAGE * 100:
        return (f"Debt is negligible next to the market cap (leverage {leverage_pct}%) — "
                "the structural model prices essentially zero credit risk; watch the "
                "put-skew gauge and sector credit indices instead.")
    prior = [p["spread_bps"] for p in series[:-1][-21:]]
    if not prior:
        return f"Model 5Y spread ≈ {cur:.0f} bps."
    avg = sum(prior) / len(prior)
    if cur > avg * 1.3 and cur - avg > 5:
        return (f"Model 5Y spread ≈ {cur:.0f} bps, WIDENING (~{avg:.0f} bps a month ago) — "
                "the equity market is pricing rising balance-sheet risk.")
    if cur < avg * 0.77 and avg - cur > 5:
        return f"Model 5Y spread ≈ {cur:.0f} bps, tightening (~{avg:.0f} bps a month ago)."
    return f"Model 5Y spread ≈ {cur:.0f} bps, roughly stable over the past month."
