"""perfection.py — "Priced for Perfection" pre-earnings scoring model (v3.67).

Answers ONE question before a company reports:

    How much future success is already in the price — and how vulnerable is
    the stock to falling even on strong results?

This is NOT a company-quality score. A great company can be a dangerous
earnings trade when valuation + positioning assume near-perfect execution.

Design contract (mirrors metrics.py / credit_risk.py):
  • This module is PURE — no network, no I/O. perfection_data.py gathers
    real provider data and calls assemble(); tests drive it with fixtures.
  • Versioned model: weights, bands and thresholds live in MODEL below and
    ship in every payload. Nothing is scattered in the UI.
  • Missing data is never fabricated. A component that cannot be computed
    is excluded, the remaining weights renormalize, coverage drops, and
    confidence falls. Below 50% weighted coverage NO composite is shown.
  • Whisper numbers must come from a legitimate, attributable source. No
    such free source is configured in this app, so the whisper slot ships
    empty ("No reliable whisper estimate available") and the model instead
    shows a separately-labeled MARKET-IMPLIED HURDLE derived from the
    reverse valuation — never called a whisper. The full whisper pathway
    (multi-source median/range, confidence weighting) exists and is
    unit-tested so a licensed source can be plugged in later.
  • Every number that reaches the UI is sanitized: no NaN, no infinity,
    denominators guarded. json.dumps(payload, allow_nan=False) must pass.

The composite is a weighted average of available component scores; each
component row carries score, weight (base + effective), contribution,
current values, benchmarks, signals, plain-English explanation, sources
and timestamps. Contributions reconcile to the composite exactly.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

MODEL = {
    "version": "1.0",
    "name": "priced_for_perfection",
    # Component weights, percent. Must sum to 100.
    "weights": {
        "execution_hurdle": 25,     # what the price demands vs consensus/history
        "valuation_stretch": 20,    # demandingness vs own history + peers + growth
        "expectations_gap": 15,     # consensus trajectory, whisper, guidance, dispersion
        "reaction_asymmetry": 15,   # do beats even get paid anymore?
        "momentum_stretch": 10,     # pre-earnings extension vs own distribution
        "crowding": 10,             # analyst/short/options positioning one-sidedness
        "conversion_risk": 5,       # growth → margins/FCF conversion shortfall
    },
    # Composite risk bands (inclusive bounds).
    "bands": [
        (0, 24, "Low"), (25, 49, "Moderate"), (50, 69, "Elevated"),
        (70, 84, "High"), (85, 100, "Extreme"),
    ],
    # Unprotected-long-risk = 70% composite + 30% reaction asymmetry
    # (the "even beats sell off" ingredient), banded on 40/60/80.
    "ulr": {"composite_w": 0.7, "reaction_w": 0.3,
            "bands": [(0, 39, "Low"), (40, 59, "Moderate"), (60, 79, "High"), (80, 100, "Extreme")]},
    # Weighted-coverage → confidence rating.
    "confidence": {"high": 85, "medium": 70, "low": 50},
    # Inside the 15% expectations component.
    "expectations_subweights": {"revisions": 35, "whisper": 35, "guidance": 20, "dispersion": 10},
    # Whisper handling by source confidence.
    "whisper_weight_by_confidence": {"high": 1.0, "medium": 0.5, "low": 0.0},
    # "Consensus is not the hurdle" fires when >= this many conditions hold.
    "warning_min_conditions": 3,
}

SECTOR_ETF = {
    "Technology": "XLK", "Communication Services": "XLC", "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP", "Energy": "XLE", "Financial Services": "XLF",
    "Financial": "XLF", "Healthcare": "XLV", "Industrials": "XLI",
    "Basic Materials": "XLB", "Real Estate": "XLRE", "Utilities": "XLU",
}


# ───────────────────────────── numeric hygiene ─────────────────────────────

def sanitize(obj):
    """Deep-clean a payload: every non-finite float becomes None, unknown
    object types become strings. Runs once on the final payload so the
    'no NaN/Infinity ever reaches the interface' contract holds structurally,
    not by per-field discipline."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    return str(obj)


def _num(x) -> float | None:
    """Coerce to a finite float or None. The single gate all inputs pass."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _rnd(x, d=1):
    v = _num(x)
    return None if v is None else round(v, d)


def _div(a, b) -> float | None:
    a, b = _num(a), _num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def winsorize(values: list[float], p: float = 0.02) -> list[float]:
    """Clip a sample to its [p, 1-p] quantiles (guards percentile inputs)."""
    vals = sorted(v for v in (_num(x) for x in values) if v is not None)
    if len(vals) < 5:
        return vals
    k = max(0, min(len(vals) - 1, int(p * len(vals))))
    lo, hi = vals[k], vals[len(vals) - 1 - k]
    return [clamp(v, lo, hi) for v in vals]


def pct_rank(value, history: list | None, min_n: int = 8) -> float | None:
    """Percentile (0-100) of value inside its own winsorized history.

    Returns None (never a guess) when the sample is too small.
    """
    v = _num(value)
    if v is None or not history:
        return None
    hist = winsorize(history)
    if len(hist) < min_n:
        return None
    below = sum(1 for h in hist if h < v)
    equal = sum(1 for h in hist if h == v)
    return clamp((below + 0.5 * equal) / len(hist) * 100.0)


def scale(value, lo_anchor: float, hi_anchor: float) -> float | None:
    """Linear 0→100 map between documented anchors (used ONLY where no
    distribution exists to take a percentile against; anchors ship in the
    payload so the mapping is inspectable)."""
    v = _num(value)
    if v is None:
        return None
    if hi_anchor == lo_anchor:
        return None
    return clamp((v - lo_anchor) / (hi_anchor - lo_anchor) * 100.0)


def classify(score) -> str | None:
    s = _num(score)
    if s is None:
        return None
    for lo, hi, label in MODEL["bands"]:
        if lo <= s <= hi:
            return label
    return "Extreme" if s > 100 else "Low"


def ulr_label(composite, reaction_score) -> str | None:
    c = _num(composite)
    if c is None:
        return None
    r = _num(reaction_score)
    cfg = MODEL["ulr"]
    s = cfg["composite_w"] * c + cfg["reaction_w"] * (r if r is not None else c)
    for lo, hi, label in cfg["bands"]:
        if lo <= s <= hi:
            return label
    return "Extreme"


def _mean(vals):
    xs = [v for v in (_num(x) for x in vals) if v is not None]
    return sum(xs) / len(xs) if xs else None


def _median(vals):
    xs = sorted(v for v in (_num(x) for x in vals) if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _blend(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    """Weighted blend of available sub-signals → (score, coverage_fraction).

    Missing parts renormalize; coverage is the fraction of weight that had
    real data behind it.
    """
    have = [(s, w) for s, w in parts if _num(s) is not None and w > 0]
    total_w = sum(w for _, w in parts if w > 0)
    if not have or total_w <= 0:
        return None, 0.0
    used_w = sum(w for _, w in have)
    score = sum(s * w for s, w in have) / used_w
    return clamp(score), used_w / total_w


# ───────────────────────── reverse valuation (DCF) ─────────────────────────

def dcf_value(revenue0, growth, fcf_margin_start, fcf_margin_end,
              years, discount, terminal_growth) -> float | None:
    """PV of an N-year FCF ramp + Gordon terminal value. Pure forward model."""
    r0, g = _num(revenue0), _num(growth)
    m0, m1 = _num(fcf_margin_start), _num(fcf_margin_end)
    n, dr, tg = int(years or 0), _num(discount), _num(terminal_growth)
    if None in (r0, g, m0, m1, dr, tg) or n < 1 or r0 <= 0 or dr <= tg:
        return None
    pv = 0.0
    rev = r0
    fcf = 0.0
    for yr in range(1, n + 1):
        rev *= (1.0 + g)
        margin = m0 + (m1 - m0) * yr / n
        fcf = rev * margin
        pv += fcf / (1.0 + dr) ** yr
    tv = fcf * (1.0 + tg) / (dr - tg)
    pv += tv / (1.0 + dr) ** n
    return pv


def implied_growth_solve(ev, revenue0, fcf_margin_start, fcf_margin_end,
                         years=5, discount=0.10, terminal_growth=0.025) -> float | None:
    """Revenue CAGR the current EV implies, by bisection (value is monotonic
    in growth). Returns a decimal (0.18 = 18%/yr) or None."""
    target = _num(ev)
    if target is None or target <= 0:
        return None
    lo, hi = -0.50, 1.50
    f = lambda g: dcf_value(revenue0, g, fcf_margin_start, fcf_margin_end,
                            years, discount, terminal_growth)
    v_lo, v_hi = f(lo), f(hi)
    if v_lo is None or v_hi is None:
        return None
    if target <= v_lo:
        return lo
    if target >= v_hi:
        return hi
    for _ in range(80):
        mid = (lo + hi) / 2.0
        v = f(mid)
        if v is None:
            return None
        if v < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def implied_margin_solve(ev, revenue0, growth, fcf_margin_start,
                         years=5, discount=0.10, terminal_growth=0.025) -> float | None:
    """Terminal FCF margin the EV requires GIVEN consensus growth (bisection
    on the margin ramp end; value is monotonic in the end margin)."""
    target = _num(ev)
    if target is None or target <= 0:
        return None
    lo, hi = 0.0, 0.80
    f = lambda m1: dcf_value(revenue0, growth, fcf_margin_start, m1,
                             years, discount, terminal_growth)
    v_lo, v_hi = f(lo), f(hi)
    if v_lo is None or v_hi is None:
        return None
    if target <= v_lo:
        return lo
    if target >= v_hi:
        return hi
    for _ in range(80):
        mid = (lo + hi) / 2.0
        v = f(mid)
        if v is None:
            return None
        if v < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ───────────────────────────── component builders ──────────────────────────
# Each takes a prepared raw-input dict (perfection_data.py's job), returns a
# component dict or None when it cannot be computed honestly:
# {score, coverage, current, benchmarks, signals_up, signals_down, explain,
#  sources, detail}

MIN_COMPONENT_COVERAGE = 0.4   # a component standing on <40% of its own
                               # sub-signals is excluded, not shown thin


def _component(name, label, score, coverage, current, benchmarks,
               up, down, explain, sources, detail=None):
    s = _num(score)
    if s is None:
        return None
    if coverage is not None and coverage < MIN_COMPONENT_COVERAGE:
        return None
    return {
        "key": name, "label": label, "score": round(clamp(s), 1),
        "coverage": round(clamp(coverage * 100.0), 0) if coverage is not None else None,
        "current": current, "benchmarks": benchmarks,
        "signals_up": up, "signals_down": down,
        "explain": explain, "sources": sources, "detail": detail or {},
    }


def build_execution_hurdle(d: dict, assumptions: dict) -> dict | None:
    """Component 1 (25%): reverse-valuation — what the price already demands."""
    ev = _num(d.get("enterprise_value"))
    rev0 = _num(d.get("revenue_ttm"))
    if ev is None or rev0 is None or rev0 <= 0 or ev <= 0:
        return None
    a = assumptions
    m_now = _num(d.get("fcf_margin_ttm"))
    m_best = _num(d.get("fcf_margin_best"))
    m_target = _num(a.get("margin_target"))
    if m_target is None:
        # Default target: midpoint of today's margin and the best of the
        # last ~3y (visible + overridable). Falls back sensibly when thin.
        if m_now is not None and m_best is not None:
            m_target = (m_now + m_best) / 2.0
        else:
            m_target = m_now if m_now is not None else m_best
    if m_target is None:
        return None
    m_start = m_now if m_now is not None else m_target
    yrs, dr, tg = int(a["horizon_years"]), float(a["discount_rate"]), float(a["terminal_growth"])

    implied_g = implied_growth_solve(ev, rev0, m_start, m_target, yrs, dr, tg)
    if implied_g is None:
        return None
    cons_g = _num(d.get("consensus_rev_growth"))       # next-FY revenue growth, decimal
    hist_g = _num(d.get("revenue_cagr_3y"))            # realized, decimal
    req_margin = implied_margin_solve(ev, rev0, cons_g if cons_g is not None else implied_g,
                                      m_start, yrs, dr, tg)

    gap_cons = (implied_g - cons_g) * 100.0 if cons_g is not None else None   # pp
    gap_hist = (implied_g - hist_g) * 100.0 if hist_g is not None else None   # pp
    margin_gap = (req_margin - m_best) * 100.0 if (req_margin is not None and m_best is not None) else None

    # Anchors (documented, shipped in detail): a price implying growth equal
    # to consensus scores ~35; implying consensus+10pp scores ~90. History
    # gap anchored a touch wider. Margin-above-best adds the remainder.
    s_cons = scale(gap_cons, -5.0, 10.0)
    s_hist = scale(gap_hist, -5.0, 12.0)
    s_marg = scale(margin_gap, -5.0, 8.0)
    score, cov = _blend([(s_cons, 45), (s_hist, 35), (s_marg, 20)])
    if score is None:
        return None

    sens = {}
    for tag, ddr, dtg in (("dr-1", -0.01, 0.0), ("dr+1", 0.01, 0.0),
                          ("tg-05", 0.0, -0.005), ("tg+05", 0.0, 0.005)):
        g = implied_growth_solve(ev, rev0, m_start, m_target, yrs, dr + ddr, tg + dtg)
        sens[tag] = _rnd(g * 100.0, 1) if g is not None else None

    checklist = []
    if gap_cons is not None and gap_cons > 1.0:
        checklist.append(f"Revenue must compound ~{implied_g * 100:.0f}%/yr for {yrs}y — "
                         f"{gap_cons:.1f}pp ABOVE the ~{cons_g * 100:.0f}% analysts model")
    if margin_gap is not None and margin_gap > 1.0:
        checklist.append(f"FCF margin must reach ~{req_margin * 100:.0f}% — above the "
                         f"~{m_best * 100:.0f}% best of the last 3 years")
    if gap_hist is not None and gap_hist > 2.0 and hist_g is not None:
        checklist.append(f"Growth must ACCELERATE: price implies ~{implied_g * 100:.0f}%/yr vs "
                         f"~{hist_g * 100:.0f}% actually delivered over 3y")
    rev_disp = d.get("revisions_direction")
    val_pct = _num(d.get("valuation_hist_pctile"))
    if val_pct is not None and val_pct >= 80 and rev_disp == "up":
        checklist.append("Guidance must be RAISED — estimates just rose and the multiple is "
                         "stretched, so in-line guidance likely reads as a miss")
    fcf_gap = _num(d.get("rev_vs_fcf_growth_gap"))
    if fcf_gap is not None and fcf_gap > 10.0:
        checklist.append(f"Free cash flow must start matching growth — revenue is compounding "
                         f"{fcf_gap:.0f}pp faster than FCF")

    up, down = [], []
    if gap_cons is not None:
        (up if gap_cons > 0 else down).append(
            f"Price implies {implied_g * 100:.1f}%/yr revenue growth vs consensus {cons_g * 100:.1f}% "
            f"({gap_cons:+.1f}pp)")
    if margin_gap is not None:
        (up if margin_gap > 0 else down).append(
            f"Required FCF margin {req_margin * 100:.1f}% vs best-3y {m_best * 100:.1f}%")
    if gap_hist is not None and gap_hist <= 0:
        down.append(f"Implied growth is BELOW the delivered 3y CAGR ({hist_g * 100:.1f}%)")

    return _component(
        "execution_hurdle", "Implied execution hurdle", score, cov,
        {"implied_rev_cagr_pct": _rnd(implied_g * 100, 1),
         "consensus_rev_growth_pct": _rnd(cons_g * 100, 1) if cons_g is not None else None,
         "revenue_cagr_3y_pct": _rnd(hist_g * 100, 1) if hist_g is not None else None,
         "required_fcf_margin_pct": _rnd(req_margin * 100, 1) if req_margin is not None else None,
         "fcf_margin_ttm_pct": _rnd(m_now * 100, 1) if m_now is not None else None,
         "fcf_margin_best_pct": _rnd(m_best * 100, 1) if m_best is not None else None,
         "market_cap": d.get("market_cap"), "enterprise_value": ev,
         "revenue_ttm": rev0},
        {"gap_vs_consensus_pp": _rnd(gap_cons, 1), "gap_vs_history_pp": _rnd(gap_hist, 1),
         "required_margin_gap_pp": _rnd(margin_gap, 1)},
        up, down,
        (f"Reverse DCF: at a {dr * 100:.0f}% discount rate and {tg * 100:.1f}% terminal growth, "
         f"today's enterprise value requires ~{implied_g * 100:.1f}%/yr revenue growth for {yrs} years "
         f"at a {m_target * 100:.0f}% end FCF margin."),
        d.get("sources_execution") or [],
        {"assumptions": {"horizon_years": yrs, "discount_rate": dr, "terminal_growth": tg,
                         "fcf_margin_start": _rnd(m_start, 4), "fcf_margin_target": _rnd(m_target, 4),
                         "share_count": d.get("share_count"), "net_debt": d.get("net_debt"),
                         "starting_revenue": rev0},
         "sensitivity_implied_cagr_pct": sens,
         "anchors": {"gap_vs_consensus_pp": [-5, 10], "gap_vs_history_pp": [-5, 12],
                     "required_margin_gap_pp": [-5, 8]},
         "checklist": checklist})


def build_valuation_stretch(d: dict) -> dict | None:
    """Component 2 (20%): demandingness vs own history, peers and growth."""
    hist_pct = _num(d.get("evs_hist_pctile"))
    pe_hist_pct = _num(d.get("pe_hist_pctile"))
    peer_pct = _num(d.get("peer_fwd_pe_pctile"))
    exp30 = _num(d.get("evs_expansion_30d_pct"))
    exp90 = _num(d.get("evs_expansion_90d_pct"))
    exp180 = _num(d.get("evs_expansion_180d_pct"))
    peg = _num(d.get("peg"))

    own = _mean([hist_pct, pe_hist_pct])
    expansion = _mean([scale(exp30, -10, 25), scale(exp90, -15, 40), scale(exp180, -20, 60)])
    growth_adj = scale(peg, 0.8, 3.5)
    score, cov = _blend([(own, 45), (peer_pct, 25), (expansion, 20), (growth_adj, 10)])
    if score is None:
        return None

    up, down = [], []
    if hist_pct is not None:
        (up if hist_pct >= 70 else down).append(
            f"Trailing EV/S in the {hist_pct:.0f}th percentile of its own {d.get('evs_hist_years', 3)}y history")
    if peer_pct is not None:
        (up if peer_pct >= 70 else down).append(
            f"Forward P/E richer than {peer_pct:.0f}% of {d.get('peer_count', 0)} {d.get('peer_basis', 'sector')} peers")
    if exp90 is not None:
        (up if exp90 > 5 else down).append(f"EV/S multiple {exp90:+.0f}% over 90 days")
    if peg is not None:
        (up if peg >= 2 else down).append(f"PEG {peg:.2f} — {'demanding' if peg >= 2 else 'reasonable'} vs expected growth")

    return _component(
        "valuation_stretch", "Valuation stretch", score, cov,
        {"forward_pe": d.get("forward_pe"), "ev_to_revenue": d.get("ev_to_revenue"),
         "ev_to_ebitda": d.get("ev_to_ebitda"), "peg": peg,
         "price_to_fcf": d.get("price_to_fcf"), "fcf_yield_pct": d.get("fcf_yield_pct")},
        {"evs_hist_pctile": _rnd(hist_pct, 0), "pe_hist_pctile": _rnd(pe_hist_pct, 0),
         "peer_fwd_pe_pctile": _rnd(peer_pct, 0), "peer_median_fwd_pe": d.get("peer_median_fwd_pe"),
         "evs_expansion_pct": {"d30": _rnd(exp30, 1), "d90": _rnd(exp90, 1), "d180": _rnd(exp180, 1)}},
        up, down,
        ("Measures whether the valuation is unusually DEMANDING — vs the stock's own multi-year "
         "range, sector peers and expected growth — not whether the absolute multiple is high."),
        d.get("sources_valuation") or [],
        {"note": "History percentiles use TRAILING EV/S and P/E rebuilt from daily price × shares "
                 "and quarterly financials (forward-multiple history is not free); current levels "
                 "and the peer comparison use forward numbers."})


def build_expectations_gap(d: dict) -> dict | None:
    """Component 3 (15%): consensus trajectory / whisper / guidance / dispersion."""
    sw = MODEL["expectations_subweights"]
    rev7 = _num(d.get("eps_rev_pct_7d"))
    rev30 = _num(d.get("eps_rev_pct_30d"))
    rev90 = _num(d.get("eps_rev_pct_90d"))
    updown = _num(d.get("revisions_up_minus_down"))
    s_moves = _mean([scale(rev7, -1.5, 4), scale(rev30, -3, 8), scale(rev90, -5, 15)])
    s_updn = scale(updown, -6, 8)
    s_revisions = _mean([s_moves, s_updn])

    # Whisper: only a legitimate, attributable source counts. Weight by its
    # confidence (high=full, medium=half, low=excluded), else exclude fully.
    wh = d.get("whisper") or {}
    s_whisper = None
    wh_weight = 0.0
    wh_conf = (wh.get("confidence") or "").lower()
    if wh.get("available"):
        mult = MODEL["whisper_weight_by_confidence"].get(wh_conf, 0.0)
        gap = _num(wh.get("eps_gap_pct"))
        if mult > 0 and gap is not None:
            s_whisper = scale(gap, -2, 8)
            wh_weight = sw["whisper"] * mult

    gd = d.get("guidance") or {}
    s_guid = None
    if gd.get("available"):
        ggap = _num(gd.get("vs_consensus_pct"))
        if ggap is not None:
            s_guid = scale(ggap, -4, 6)

    disp = _num(d.get("eps_dispersion_pct"))          # (high-low)/|avg| × 100
    accel = _num(d.get("required_accel_pp"))          # next-q growth − trailing growth
    s_disp = None
    if disp is not None or accel is not None:
        # LOW dispersion = a crowded, single-point consensus → less room for
        # an in-line print to satisfy anyone. Inverted scale.
        s_tight = scale(disp, 40, 5) if disp is not None else None
        s_acc = scale(accel, -5, 15) if accel is not None else None
        s_disp = _mean([s_tight, s_acc])

    score, cov = _blend([
        (s_revisions, sw["revisions"]),
        (s_whisper, wh_weight),
        (s_guid, sw["guidance"] if s_guid is not None else 0),
        (s_disp, sw["dispersion"]),
    ])
    # Coverage vs the FULL sub-weight set (missing whisper/guidance reduce it).
    full = sum(sw.values())
    used = (sw["revisions"] if s_revisions is not None else 0) + wh_weight \
        + (sw["guidance"] if s_guid is not None else 0) + (sw["dispersion"] if s_disp is not None else 0)
    if score is None:
        return None

    up, down = [], []
    if rev30 is not None:
        (up if rev30 > 0.5 else down).append(
            f"Consensus EPS {'raised' if rev30 > 0 else 'cut'} {abs(rev30):.1f}% in 30d "
            f"({'bar rising into the print' if rev30 > 0 else 'bar falling'})")
    if updown is not None and updown != 0:
        (up if updown > 0 else down).append(
            f"{'+' if updown > 0 else ''}{updown:.0f} net {'upward' if updown > 0 else 'downward'} analyst revisions (30d)")
    if wh.get("available") and _num(wh.get("eps_gap_pct")) is not None:
        g = _num(wh.get("eps_gap_pct"))
        (up if g > 0 else down).append(f"Whisper EPS {g:+.1f}% vs published consensus")
    if disp is not None and disp < 8:
        up.append(f"Tight estimate dispersion ({disp:.1f}%) — a crowded consensus")
    mih = d.get("market_implied_hurdle") or {}

    return _component(
        "expectations_gap", "Expectations & whisper gap", score, used / full,
        {"consensus_eps": d.get("consensus_eps"), "consensus_revenue": d.get("consensus_revenue"),
         "consensus_eps_analysts": d.get("consensus_eps_analysts"),
         "eps_rev_pct": {"d7": _rnd(rev7, 2), "d30": _rnd(rev30, 2), "d90": _rnd(rev90, 2)},
         "revisions_up_30d": d.get("revisions_up_30d"), "revisions_down_30d": d.get("revisions_down_30d"),
         "eps_dispersion_pct": _rnd(disp, 1), "required_accel_pp": _rnd(accel, 1)},
        {"whisper": {
            "available": bool(wh.get("available")),
            "note": wh.get("note") or ("No reliable whisper estimate available — no legitimate, "
                                       "attributable whisper source is configured; nothing is inferred "
                                       "or invented in its place."),
            "median_eps": wh.get("median_eps"), "range": wh.get("range"),
            "sources": wh.get("sources"), "source_count": wh.get("source_count"),
            "confidence": wh.get("confidence"), "asof": wh.get("asof"),
            "eps_gap_pct": _rnd(wh.get("eps_gap_pct"), 1) if wh.get("available") else None,
            "revenue_gap_pct": _rnd(wh.get("revenue_gap_pct"), 1) if wh.get("available") else None},
         "guidance": {
            "available": bool(gd.get("available")),
            "note": gd.get("note") or ("No structured company-guidance feed is available from the "
                                       "configured free providers."),
            "vs_consensus_pct": _rnd(gd.get("vs_consensus_pct"), 1) if gd.get("available") else None},
         "market_implied_hurdle": mih},
        up, down,
        ("Tracks how the bar itself is moving: published consensus and its revisions, any credible "
         "whisper (none configured — stated, not faked), guidance where available, and how crowded "
         "the estimate range is. The separately-labeled MARKET-IMPLIED HURDLE comes from the reverse "
         "valuation — it is a derived requirement, never a whisper."),
        d.get("sources_expectations") or [],
        {"subweights": sw, "whisper_weight_applied": wh_weight,
         "subweight_coverage_pct": round(used / full * 100.0, 0)})


def build_reaction_asymmetry(d: dict) -> dict | None:
    """Component 4 (15%): does good news still get paid? From real events."""
    events = d.get("events") or []
    scored = [e for e in events if _num(e.get("reaction_1d_pct")) is not None
              and e.get("beat_consensus") is not None]
    if len(scored) < 4:
        return None
    beats = [e for e in scored if e["beat_consensus"]]
    misses = [e for e in scored if not e["beat_consensus"]]
    fades = [e for e in beats if e["reaction_1d_pct"] <= 0]
    beat_fade_freq = _div(len(fades), len(beats))
    avg_beat = _mean([e["reaction_1d_pct"] for e in beats])
    avg_miss = _mean([e["reaction_1d_pct"] for e in misses])
    # Weakening-reaction trend: are newer beats paid less than older ones?
    trend = None
    if len(beats) >= 4:
        newest = _mean([e["reaction_1d_pct"] for e in beats[:len(beats) // 2]])
        oldest = _mean([e["reaction_1d_pct"] for e in beats[len(beats) // 2:]])
        if newest is not None and oldest is not None:
            trend = newest - oldest   # negative = beats paying less now

    s_fade = scale(beat_fade_freq * 100.0 if beat_fade_freq is not None else None, 0, 75)
    s_pay = scale(avg_beat, 6, -3) if avg_beat is not None else None   # small/negative pay = risk
    s_trend = scale(trend, 2, -6) if trend is not None else None
    score, cov = _blend([(s_fade, 50), (s_pay, 30), (s_trend, 20)])
    if score is None:
        return None

    exceed = [e for e in scored if e.get("exceeded_implied") is True]
    n_impl = sum(1 for e in scored if e.get("exceeded_implied") is not None)

    saturation = bool(beat_fade_freq is not None and len(beats) >= 4 and
                      (beat_fade_freq >= 0.4 or (avg_beat is not None and avg_beat <= 0.5)))
    up, down = [], []
    if beat_fade_freq is not None and beats:
        line = (f"Beat published consensus in {len(beats)} of the last {len(scored)} quarters, "
                f"but shares fell or went nowhere after {len(fades)} of those beats")
        (up if beat_fade_freq >= 0.3 else down).append(line)
    if avg_beat is not None:
        (up if avg_beat < 1 else down).append(f"Average 1-day reaction to a beat: {avg_beat:+.1f}%")
    if avg_miss is not None:
        up.append(f"Average reaction to a miss: {avg_miss:+.1f}% (the downside when the bar isn't cleared)")
    if trend is not None and trend < -1:
        up.append(f"Reactions to beats are WEAKENING ({trend:+.1f}pp newer-vs-older halves)")

    return _component(
        "reaction_asymmetry", "Earnings reaction asymmetry", score, cov,
        {"events_analyzed": len(scored), "beats": len(beats), "misses": len(misses),
         "beat_fade_count": len(fades),
         "beat_fade_freq_pct": _rnd(beat_fade_freq * 100.0, 0) if beat_fade_freq is not None else None,
         "avg_reaction_after_beat_pct": _rnd(avg_beat, 1),
         "avg_reaction_after_miss_pct": _rnd(avg_miss, 1),
         "beat_reaction_trend_pp": _rnd(trend, 1),
         "moves_exceeding_implied": f"{len(exceed)}/{n_impl}" if n_impl else None},
        {"classification_note": ("Quarters are classified against PUBLISHED consensus (and the "
                                 "options-implied move where recorded). Historical whisper and "
                                 "guidance hurdles are not available from the configured providers, "
                                 "so beat-vs-whisper splits cannot be shown — beats that FADED are "
                                 "the observable fingerprint of a missed higher bar.")},
        up, down,
        "Measures whether clearing the published bar has actually been getting paid — recently and on average.",
        d.get("sources_reactions") or [],
        {"good_news_saturation": saturation, "events": events})


def build_momentum_stretch(d: dict) -> dict | None:
    """Component 5 (10%): pre-earnings extension vs the stock's own distribution."""
    rel60_pct = _num(d.get("rel60_hist_pctile"))
    ma50_pct = _num(d.get("ma50_dist_hist_pctile"))
    near_hi = _num(d.get("from_52wk_high_pct"))
    runup = _num(d.get("runup_20d_pct"))
    s_hi = scale(near_hi, -20, 0)
    s_run = scale(runup, 0, 18)
    score, cov = _blend([(rel60_pct, 40), (ma50_pct, 25), (s_hi, 15), (s_run, 20)])
    if score is None:
        return None
    up, down = [], []
    if rel60_pct is not None:
        (up if rel60_pct >= 75 else down).append(
            f"60d return vs sector is in the {rel60_pct:.0f}th percentile of its own 3y distribution")
    if runup is not None:
        (up if runup >= 8 else down).append(f"20-day pre-earnings run-up {runup:+.1f}%")
    if near_hi is not None and near_hi >= -3:
        up.append("Sitting at/near the 52-week high into the print")
    if _num(d.get("drift_since_last_er_pct")) is not None:
        v = _num(d.get("drift_since_last_er_pct"))
        (up if v >= 15 else down).append(f"Drift since last earnings: {v:+.1f}%")
    return _component(
        "momentum_stretch", "Price & relative momentum stretch", score, cov,
        {"returns_pct": d.get("returns_pct"), "vs_sector_pct": d.get("vs_sector_pct"),
         "vs_market_pct": d.get("vs_market_pct"), "ma_distance_pct": d.get("ma_distance_pct"),
         "from_52wk_high_pct": _rnd(near_hi, 1), "runup_20d_pct": _rnd(runup, 1),
         "drift_since_last_er_pct": d.get("drift_since_last_er_pct"),
         "rvol20": d.get("rvol20"), "gap_days_60d": d.get("gap_days_60d"),
         "vol20_vs_120_ratio": d.get("vol20_vs_120_ratio")},
        {"rel60_hist_pctile": _rnd(rel60_pct, 0), "ma50_dist_hist_pctile": _rnd(ma50_pct, 0),
         "sector_etf": d.get("sector_etf"), "benchmark": d.get("benchmark", "SPY")},
        up, down,
        ("Separates healthy trend from an unusually EXTENDED pre-earnings setup by ranking today's "
         "relative return and MA-distance inside the stock's own 3-year distribution."),
        d.get("sources_momentum") or [])


def build_crowding(d: dict) -> dict | None:
    """Component 6 (10%): one-sided bullish positioning."""
    pt_upside = _num(d.get("pt_upside_pct"))
    s_pt = scale(pt_upside, 25, -5)      # price above/at consensus target = crowded
    buy_ratio = _num(d.get("buy_ratio_pct"))
    s_rec = scale(buy_ratio, 40, 95)
    skew = _num(d.get("call_put_skew_vol_pts"))       # 25Δ call IV − put IV
    cp_oi = _num(d.get("call_put_oi_ratio"))
    cp_vol = _num(d.get("call_put_vol_ratio"))
    s_opt = _mean([scale(skew, -3, 3), scale(cp_oi, 0.9, 2.5), scale(cp_vol, 1.0, 3.0)])
    si = _num(d.get("short_pct_float"))
    dtc = _num(d.get("days_to_cover"))
    s_si = None
    if si is not None:
        # Low short interest is only mild supporting evidence — capped weight
        # below, never decisive on its own (documented).
        s_si = _mean([scale(si, 6, 0.5), scale(dtc, 5, 0.8) if dtc is not None else None])
    s_ptchg = scale(_num(d.get("pt_changes_net_30d")), -3, 6)
    score, cov = _blend([(s_pt, 30), (s_rec, 20), (s_opt, 30), (s_si, 10), (s_ptchg, 10)])
    if score is None:
        return None
    up, down = [], []
    if pt_upside is not None:
        (up if pt_upside < 5 else down).append(
            f"Price {'is above' if pt_upside < 0 else f'has only {pt_upside:.0f}% upside to'} "
            f"the mean analyst target")
    if buy_ratio is not None:
        (up if buy_ratio >= 75 else down).append(f"{buy_ratio:.0f}% of ratings are Buy/Overweight")
    if skew is not None:
        (up if skew > 0.5 else down).append(f"25Δ options skew {skew:+.1f} vol pts ({'calls' if skew > 0 else 'puts'} bid)")
    if cp_oi is not None and cp_oi >= 1.8:
        up.append(f"Call/put open interest {cp_oi:.1f}× — bullish positioning stacked")
    if si is not None and si <= 2:
        up.append(f"Short interest only {si:.1f}% of float (few forced buyers left; supporting signal only)")
    return _component(
        "crowding", "Crowding & bullish positioning", score, cov,
        {"pt_upside_pct": _rnd(pt_upside, 1), "target_mean": d.get("target_mean"),
         "buy_ratio_pct": _rnd(buy_ratio, 0), "ratings": d.get("ratings"),
         "analyst_count": d.get("analyst_count"),
         "short_pct_float": _rnd(si, 2), "days_to_cover": _rnd(dtc, 1),
         "call_put_oi_ratio": _rnd(cp_oi, 2), "call_put_vol_ratio": _rnd(cp_vol, 2),
         "call_put_skew_vol_pts": _rnd(skew, 2), "oi_concentration": d.get("oi_concentration"),
         "pt_changes_net_30d": d.get("pt_changes_net_30d"),
         "institutional_pct": d.get("institutional_pct")},
        {"note": "Short interest alone never drives this score — it is a 10% supporting signal."},
        up, down,
        "How one-sided the boat is: analyst ratings and targets, options positioning and skew, short interest.",
        d.get("sources_crowding") or [])


def build_conversion_risk(d: dict) -> dict | None:
    """Component 7 (5%): is growth converting into margins and cash?"""
    gm_slope = _num(d.get("gross_margin_slope_pp_q"))
    om_slope = _num(d.get("op_margin_slope_pp_q"))
    fcf_gap = _num(d.get("rev_vs_fcf_growth_gap"))
    sbc = _num(d.get("sbc_pct_ocf"))
    inv_gap = _num(d.get("inventory_vs_rev_growth_gap"))
    ocf_conv = _num(d.get("ocf_conversion"))
    s_margin = _mean([scale(gm_slope, 1.0, -1.5), scale(om_slope, 1.5, -2.0)])
    s_fcf = _mean([scale(fcf_gap, 0, 25), scale(ocf_conv, 1.1, 0.5) if ocf_conv is not None else None])
    s_sbc = scale(sbc, 5, 35)
    s_inv = scale(inv_gap, 0, 30)
    score, cov = _blend([(s_margin, 35), (s_fcf, 35), (s_sbc, 15), (s_inv, 15)])
    if score is None:
        return None
    up, down = [], []
    if gm_slope is not None:
        (down if gm_slope > 0 else up).append(f"Gross margin {'expanding' if gm_slope > 0 else 'compressing'} "
                                              f"{abs(gm_slope):.1f}pp/quarter")
    if fcf_gap is not None:
        if fcf_gap > 10:
            up.append(f"Revenue growing {fcf_gap:.0f}pp faster than free cash flow — growth not yet converting")
        else:
            down.append(f"Free cash flow keeping up with revenue"
                        + (f" — growing {abs(fcf_gap):.0f}pp FASTER (strong conversion)" if fcf_gap < -5 else ""))
    if sbc is not None and sbc > 20:
        up.append(f"Stock-based comp is {sbc:.0f}% of operating cash flow")
    if inv_gap is not None and inv_gap > 10:
        up.append(f"Inventory growing {inv_gap:.0f}pp faster than revenue")
    return _component(
        "conversion_risk", "Fundamental conversion risk", score, cov,
        {"gross_margin_slope_pp_q": _rnd(gm_slope, 2), "op_margin_slope_pp_q": _rnd(om_slope, 2),
         "fcf_margin_ttm_pct": d.get("fcf_margin_ttm_pct"),
         "ocf_conversion": _rnd(ocf_conv, 2), "sbc_pct_ocf": _rnd(sbc, 1),
         "capex_pct_revenue": d.get("capex_pct_revenue"),
         "rev_vs_fcf_growth_gap": _rnd(fcf_gap, 1),
         "inventory_vs_rev_growth_gap": _rnd(inv_gap, 1),
         "rev_vs_eps_growth_gap": d.get("rev_vs_eps_growth_gap")},
        {"quarters_used": d.get("conversion_quarters")},
        up, down,
        "Rises when the valuation needs margin/cash-flow improvement that reported results are not yet showing.",
        d.get("sources_conversion") or [])


# ─────────────────────────────── assembly ──────────────────────────────────

def _confidence(coverage_pct: float, freshness_penalties: list[str]) -> str:
    c = MODEL["confidence"]
    eff = coverage_pct - 5.0 * len(freshness_penalties)
    if eff >= c["high"]:
        return "High"
    if eff >= c["medium"]:
        return "Medium"
    if eff >= c["low"]:
        return "Low"
    return "Insufficient"


def build_warning(components: dict, d: dict) -> dict:
    """'Consensus is not the hurdle' — fires on the spec's condition set."""
    conds = []
    wh = (d.get("whisper") or {})
    if wh.get("available") and _num(wh.get("eps_gap_pct")) is not None and wh["eps_gap_pct"] >= 2:
        conds.append(f"Credible whisper EPS is {wh['eps_gap_pct']:+.1f}% above consensus")
    runup = _num(d.get("runup_20d_pct"))
    if runup is not None and runup >= 8:
        conds.append(f"{runup:+.1f}% pre-earnings run-up in 20 days")
    rev30 = _num(d.get("eps_rev_pct_30d"))
    if rev30 is not None and rev30 >= 1:
        conds.append(f"Consensus EPS revised up {rev30:.1f}% in 30 days")
    vs = components.get("valuation_stretch")
    if vs and vs["score"] >= 70:
        conds.append(f"Valuation stretch scores {vs['score']:.0f}/100 vs history and peers")
    ra = components.get("reaction_asymmetry")
    if ra and (ra["current"].get("beat_fade_count") or 0) >= 2:
        conds.append(f"{ra['current']['beat_fade_count']} of the last {ra['current']['beats']} "
                     f"consensus beats still produced flat/negative reactions")
    eh = components.get("execution_hurdle")
    if eh and _num(eh["benchmarks"].get("gap_vs_consensus_pp")) is not None \
            and eh["benchmarks"]["gap_vs_consensus_pp"] >= 2:
        conds.append(f"Current price implies execution {eh['benchmarks']['gap_vs_consensus_pp']:+.1f}pp "
                     f"above published consensus growth")
    fired = len(conds) >= MODEL["warning_min_conditions"]
    return {"fired": fired, "conditions": conds,
            "min_conditions": MODEL["warning_min_conditions"],
            "headline": ("Consensus is NOT the hurdle: several signals say the real bar sits above "
                         "published estimates — a normal beat may not be enough.") if fired else None}


def build_scenarios(components: dict, d: dict) -> list[dict]:
    """Six-scenario matrix. Historical analogs come from the stored events;
    move ranges appear only with >=4 analog samples (median ± MAD)."""
    ra = components.get("reaction_asymmetry")
    events = (ra["detail"].get("events") if ra else None) or []
    beats = [e for e in events if e.get("beat_consensus") is True and _num(e.get("reaction_1d_pct")) is not None]
    misses = [e for e in events if e.get("beat_consensus") is False and _num(e.get("reaction_1d_pct")) is not None]
    fades = [e for e in beats if e["reaction_1d_pct"] <= 0]
    paid = [e for e in beats if e["reaction_1d_pct"] > 0]

    def stats(evs):
        vals = [e["reaction_1d_pct"] for e in evs]
        if len(vals) < 4:
            return {"n": len(vals), "median_pct": _rnd(_median(vals), 1), "range": None}
        med = _median(vals)
        mad = _median([abs(v - med) for v in vals])
        return {"n": len(vals), "median_pct": _rnd(med, 1),
                "range": [_rnd(med - 1.5 * mad, 1), _rnd(med + 1.5 * mad, 1)]}

    vs = components.get("valuation_stretch")
    compression_risk = bool(vs and vs["score"] >= 70)
    wh_avail = bool((d.get("whisper") or {}).get("available"))
    wh_note = None if wh_avail else ("no whisper source configured — the 'whisper' bar below uses the "
                                     "separately-labeled market-implied hurdle as the higher bar")
    hurdle_above = bool(components.get("execution_hurdle") and
                        _num(components["execution_hurdle"]["benchmarks"].get("gap_vs_consensus_pp")) is not None and
                        components["execution_hurdle"]["benchmarks"]["gap_vs_consensus_pp"] > 0)

    return [
        {"key": "miss", "label": "Misses consensus",
         "satisfies": [], "risk_direction": "down",
         "compression_risk": True,
         "analog": {"basis": f"the {len(misses)} historical misses", **stats(misses)} if misses
         else {"basis": "no historical misses in the window", "n": 0, "median_pct": None, "range": None}},
        {"key": "meet_miss_higher", "label": "Meets consensus, misses the higher bar",
         "satisfies": ["consensus (barely)"], "risk_direction": "down",
         "compression_risk": True, "note": wh_note,
         "analog": {"basis": f"the {len(fades)} beats that faded (closest observable analog)",
                    **stats(fades)} if fades else {"basis": "no analog events", "n": 0, "median_pct": None, "range": None}},
        {"key": "beat_miss_higher", "label": "Beats consensus, misses the higher bar",
         "satisfies": ["consensus"], "risk_direction": "down" if (hurdle_above or compression_risk) else "mixed",
         "compression_risk": compression_risk, "note": wh_note,
         "analog": {"basis": f"the {len(fades)} consensus beats that still faded", **stats(fades)}
         if fades else {"basis": "no beat-and-fade events on record", "n": 0, "median_pct": None, "range": None}},
        {"key": "beat_inline_guide", "label": "Beats consensus & higher bar, guides in line",
         "satisfies": ["consensus", "higher bar"], "risk_direction": "mixed",
         "compression_risk": compression_risk,
         "note": "guidance history is unavailable from configured providers — direction inferred from "
                 "valuation stretch only",
         "analog": {"basis": f"the {len(paid)} beats that got paid", **stats(paid)} if paid
         else {"basis": "no paid-beat events on record", "n": 0, "median_pct": None, "range": None}},
        {"key": "beat_raise", "label": "Beats everything and raises guidance",
         "satisfies": ["consensus", "higher bar", "guidance"], "risk_direction": "up",
         "compression_risk": compression_risk and hurdle_above,
         "analog": {"basis": f"best quartile of the {len(beats)} historical beats",
                    **stats(sorted(beats, key=lambda e: -e["reaction_1d_pct"])[:max(4, len(beats) // 2)])}
         if len(beats) >= 4 else {"basis": "insufficient history", "n": len(beats), "median_pct": None, "range": None}},
        {"key": "beat_all_compress", "label": "Beats all expectations but the multiple still compresses",
         "satisfies": ["consensus", "higher bar", "guidance"], "risk_direction": "down",
         "compression_risk": True,
         "note": ("live risk: valuation stretch scores "
                  f"{vs['score']:.0f}/100" if compression_risk else
                  "lower-probability here: valuation stretch is not extreme"),
         "analog": {"basis": "beats that faded despite strong numbers", **stats(fades)} if fades
         else {"basis": "no analog events", "n": 0, "median_pct": None, "range": None}},
    ]


def build_explanations(components: dict) -> dict:
    """Top-3 risk-increasing and top-2 risk-reducing factors, from real rows."""
    ups, downs = [], []
    for c in components.values():
        if not c:
            continue
        for s in c.get("signals_up") or []:
            ups.append({"component": c["label"], "text": s, "score": c["score"]})
        for s in c.get("signals_down") or []:
            downs.append({"component": c["label"], "text": s, "score": c["score"]})
    ups.sort(key=lambda x: -x["score"])
    downs.sort(key=lambda x: x["score"])
    return {"risk_increasing": ups[:3], "risk_reducing": downs[:2]}


def summarize(score, label, components: dict, warning: dict) -> str | None:
    if score is None:
        return None
    drivers = sorted([c for c in components.values() if c],
                     key=lambda c: -(c["score"] * c.get("_eff_weight", 0)))
    names = {"execution_hurdle": "valuation-implied execution", "valuation_stretch": "a stretched multiple",
             "expectations_gap": "a rising expectations bar", "reaction_asymmetry": "weak reactions to past beats",
             "momentum_stretch": "the pre-earnings run-up", "crowding": "crowded bullish positioning",
             "conversion_risk": "unproven cash conversion"}
    top = [names.get(c["key"], c["label"].lower()) for c in drivers[:3] if c["score"] >= 50]
    if label in ("High", "Extreme") and top:
        return (f"{label} perfection risk: " + ", ".join(top) +
                " suggest a normal beat may not be enough.")
    if label == "Elevated" and top:
        return (f"Elevated perfection risk: " + ", ".join(top) +
                " raise the bar above the published numbers.")
    if label == "Moderate":
        return "Moderate perfection risk: expectations are real but not extreme — an ordinary beat has room to work."
    return "Low perfection risk: little evidence that the price already demands perfection."


def assemble(inputs: dict, assumptions: dict | None = None) -> dict:
    """inputs: the prepared raw-metric dict from perfection_data (or tests).
    Returns the full, sanitized, reconciled payload."""
    a = dict(DEFAULT_ASSUMPTIONS)
    a.update({k: v for k, v in (assumptions or {}).items() if v is not None})

    builders = {
        "execution_hurdle": lambda: build_execution_hurdle(inputs, a),
        "valuation_stretch": lambda: build_valuation_stretch(inputs),
        "expectations_gap": lambda: build_expectations_gap(inputs),
        "reaction_asymmetry": lambda: build_reaction_asymmetry(inputs),
        "momentum_stretch": lambda: build_momentum_stretch(inputs),
        "crowding": lambda: build_crowding(inputs),
        "conversion_risk": lambda: build_conversion_risk(inputs),
    }
    components: dict[str, dict | None] = {}
    for key, fn in builders.items():
        try:
            components[key] = fn()
        except Exception:
            components[key] = None    # a bad input never takes down the score

    weights = MODEL["weights"]
    avail = {k: c for k, c in components.items() if c is not None}
    used_w = sum(weights[k] for k in avail)
    coverage_pct = used_w  # weights sum to 100
    composite = None
    if avail and used_w > 0:
        composite = sum(c["score"] * weights[k] for k, c in avail.items()) / used_w
    for k, c in components.items():
        if c is None:
            continue
        eff = weights[k] / used_w * 100.0 if used_w else 0.0
        c["weight_base_pct"] = weights[k]
        c["weight_effective_pct"] = round(eff, 1)
        c["contribution"] = round(c["score"] * eff / 100.0, 2)
        c["_eff_weight"] = eff

    penalties = list(inputs.get("freshness_penalties") or [])
    conf = _confidence(coverage_pct, penalties)
    show = conf != "Insufficient"
    score = round(clamp(composite), 1) if (composite is not None and show) else None
    label = classify(score)
    warning = build_warning(avail, inputs)
    scen = build_scenarios(avail, inputs)
    expl = build_explanations(avail)
    ra = avail.get("reaction_asymmetry")

    payload = {
        "model": {"version": MODEL["version"], "name": MODEL["name"],
                  "weights": weights, "bands": MODEL["bands"],
                  "confidence_thresholds": MODEL["confidence"],
                  "ulr": {k: v for k, v in MODEL["ulr"].items()}},
        "assumptions": a,
        "score": score,
        "classification": label,
        "unprotected_long_risk": ulr_label(score, ra["score"] if ra else None),
        "confidence": conf,
        "coverage_pct": round(coverage_pct, 0),
        "freshness_penalties": penalties,
        "summary": summarize(score, label, avail, warning),
        "components": {k: ({kk: vv for kk, vv in c.items() if kk != "_eff_weight"} if c else None)
                       for k, c in components.items()},
        "missing_components": [k for k, c in components.items() if c is None],
        "warning": warning,
        "scenarios": scen,
        "explanations": expl,
        "good_news_saturation": bool(ra and ra["detail"].get("good_news_saturation")),
        "disclaimer": ("A transparency tool, not a prediction, price target or personalized financial "
                       "advice. The score summarizes how demanding the setup is — not what will happen."),
    }
    # Reconciliation check (ships in payload; UI shows a warning if ever false).
    if score is not None:
        total = sum(c["contribution"] for c in avail.values())
        payload["reconciled"] = abs(total - score) <= 0.15
        payload["contribution_total"] = round(total, 2)
    return sanitize(payload)


DEFAULT_ASSUMPTIONS = {
    "horizon_years": 5,
    "discount_rate": 0.10,
    "terminal_growth": 0.025,
    "margin_target": None,     # None → midpoint(current, best-3y), shown in detail
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
