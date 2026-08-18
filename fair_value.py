"""fair_value.py — Phase 3 valuation arithmetic for the Investment tab.

Pure functions only: no network, no disk, no clock. `fundamentals.py` reads
the filings, `peers.py` builds the comparison group, `invest_scan.py` owns the
providers, and this module answers four questions with the numbers they hand
it:

  1. What is a defensible Bear / Base / Bull value?      → fair_value()
  2. At what price do I actually want to own it?         → buy_zone, inside
                                                           the same result
  3. What return does today's price imply?               → expected_return()
  4. What growth is the market already pricing in?       → implied_growth()

Three rules are enforced here rather than left to the caller.

BASIS NEVER MIXES. Every valuation method carries the basis it was computed
on, and a method that would have to multiply an adjusted analyst forward
estimate by a GAAP trailing multiple is simply not offered. Trailing GAAP
earnings against peer trailing GAAP multiples is a comparison; trailing GAAP
earnings against peer FORWARD multiples is a category error with a number
attached.

METHODS ARE NOT AVERAGED. Averaging a good method with a bad one produces a
number worse than the good method alone and hides which was which. The Base
value is the single highest-confidence valid method. The other methods still
matter: they set the WIDTH of the range and they set the confidence, because
methods that disagree are methods that should not be trusted to two decimal
places.

LOWER CONFIDENCE LOWERS THE PRICE WE WILL PAY. The obvious-looking

    Adjusted margin of safety = Raw margin of safety × Confidence

runs the wrong way: it shrinks the discount demanded exactly when the
valuation is least trustworthy. Instead the confidence decides HOW FAR UP
from the Bear value the credited value is allowed to travel:

    Credited fair value = Bear + Confidence × (Base − Bear)

so an unreliable valuation credits nothing above the pessimistic case, and
the margin of safety is then applied on top of that.
"""

from __future__ import annotations

import math

FAIR_VALUE_VERSION = "invest-fairvalue-1.0.0"

SPECIALIZED = "SPECIALIZED MODEL REQUIRED"

CONFIDENCE_LEVELS = ("HIGH", "MODERATE", "LOW", "UNRELIABLE")

# Every knob here is exposed in thresholds.json under "investment".
# The disagreement bands are a stated convention, NOT an empirically
# calibrated result — nothing in this dashboard has tested that a 30% spread
# between two valuation methods predicts anything. They exist so that a wide
# disagreement visibly costs the valuation its confidence instead of being
# averaged quietly away.
DEFAULTS = {
    # Method A — percentiles of the company's own EARNINGS-YIELD history.
    # A high yield is a cheap price, so the pessimistic value sits at the
    # HIGH yield percentile. That inversion is the whole reason these are
    # named rather than written as 10/50/90 in three places.
    "self_bear_yield_percentile": 0.85,
    "self_base_yield_percentile": 0.50,
    "self_bull_yield_percentile": 0.15,
    # Method B — percentiles of the peer group's member multiples.
    "peer_bear_percentile": 0.25,
    "peer_bull_percentile": 0.75,
    # Method C — percentiles of the company's own FREE-CASH-FLOW-YIELD history.
    "fcf_bear_yield_percentile": 0.85,
    "fcf_base_yield_percentile": 0.50,
    "fcf_bull_yield_percentile": 0.15,
    "fcf_normalize_periods": 5,

    # Disagreement between methods, as (highest base − lowest base) ÷ lowest.
    "spread_high_max": 0.25,
    "spread_moderate_max": 0.50,
    "spread_low_max": 1.00,
    # How far above Bear the credited value may travel, per confidence level.
    "confidence_credit": {"HIGH": 1.0, "MODERATE": 0.65, "LOW": 0.35,
                          "UNRELIABLE": 0.0},
    "min_margin_of_safety": 0.20,

    # Expected return bridge.
    "horizon_years": 3.0,
    "growth_bear_percentile": 0.25,
    "growth_base_percentile": 0.50,
    "growth_bull_percentile": 0.75,
    "growth_floor_pct": -30.0,
    "growth_cap_pct": 40.0,
    "multiple_bear_percentile": 0.25,
    "multiple_base_percentile": 0.50,
    "multiple_bull_percentile": 0.75,
    # How long the multiple is assumed to take to reach its scenario target.
    # Used to price option horizons off the same scenario assumptions without
    # pretending a forty-five-day contract re-rates the whole way.
    "multiple_reversion_years": 3.0,

    # Implied expectations audit.
    "reverse_dcf_years": 5,
    "equity_risk_premium_pct": 4.5,
    "terminal_growth_pct": 3.0,
    "sensitivity_rate_step_pct": 1.0,
    "sensitivity_terminal_growths_pct": [2.0, 3.0, 4.0],

    # Scenario weights. Assumptions, shown and adjustable, never facts.
    "prob_bear": 0.25,
    "prob_base": 0.50,
    "prob_bull": 0.25,
}

SCENARIOS = ("bear", "base", "bull")


def cfg_get(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


# ── small helpers ───────────────────────────────────────────────────────────

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clean(values):
    return [x for x in (_num(y) for y in (values or [])) if x is not None]


def quantile(values, q: float):
    """Linear-interpolated quantile, same convention as invest_engine."""
    vals = sorted(_clean(values))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * max(0.0, min(1.0, q))
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def _ordered(bear, base, bull):
    """Bear ≤ Base ≤ Bull, whatever order the percentiles came out in.

    Sorting rather than asserting: a company whose median multiple sits below
    its 25th-percentile peer multiple is a real thing, and the honest answer
    is a range with the pessimistic end at the bottom, not a crash.
    """
    vals = [v for v in (bear, base, bull) if v is not None]
    if len(vals) < 3:
        return bear, base, bull
    lo, mid, hi = sorted(vals)
    return lo, mid, hi


def _method(key, label, basis, bear=None, base=None, bull=None, n=0,
            reason="", rank=0.0, detail=None) -> dict:
    ok = base is not None and base > 0
    bear, base, bull = _ordered(bear, base, bull) if ok else (bear, base, bull)
    return {"key": key, "label": label, "basis": basis,
            "available": bool(ok), "bear": bear, "base": base, "bull": bull,
            "n": n, "reason": "" if ok else (reason or "Not available."),
            "confidence_rank": rank if ok else 0.0,
            "detail": detail or {}}


# ══════════════════════════════════════════════════════════════════════════
# THE THREE VALUATION METHODS
# ══════════════════════════════════════════════════════════════════════════

MIN_SELF_OBSERVATIONS = 250     # ≈ one trading year of daily valuation points
MIN_PEERS_FOR_VALUE = 5
MIN_FCF_PERIODS = 4


def method_self_history(eps_ttm, earnings_yields_pct, cfg=None,
                        regime_shifted: bool = False,
                        window_label: str = "5-year") -> dict:
    """A. Today's reported earnings at the multiples this company itself has
    actually traded at.

    The yields come from the Phase 2 point-in-time history: each day's price
    against the earnings that were PUBLIC on that day. Nothing here knows a
    figure before it was filed.
    """
    cfg = cfg or {}
    eps = _num(eps_ttm)
    ys = _clean(earnings_yields_pct)
    basis = ("GAAP trailing earnings per share, priced at this company's own "
             "point-in-time earnings-yield history")
    if eps is None or eps <= 0:
        return _method("self_history", "Its own valuation history", basis,
                       reason=("Trailing earnings are zero or negative, so "
                               "there is no earnings figure to place against "
                               "the history."))
    if len(ys) < MIN_SELF_OBSERVATIONS:
        return _method("self_history", "Its own valuation history", basis,
                       n=len(ys),
                       reason=(f"Only {len(ys)} daily valuation observations "
                               f"are available — fewer than the "
                               f"{MIN_SELF_OBSERVATIONS} this method needs "
                               f"before a percentile means anything."))
    # A HIGH yield is a CHEAP price, so the pessimistic value comes from the
    # high-yield end of the distribution.
    y_bear = quantile(ys, float(cfg_get(cfg, "self_bear_yield_percentile")))
    y_base = quantile(ys, float(cfg_get(cfg, "self_base_yield_percentile")))
    y_bull = quantile(ys, float(cfg_get(cfg, "self_bull_yield_percentile")))
    prices = []
    for y in (y_bear, y_base, y_bull):
        prices.append(eps / (y / 100.0) if y and y > 0 else None)
    if prices[1] is None:
        return _method("self_history", "Its own valuation history", basis,
                       n=len(ys),
                       reason="The median historical earnings yield is not "
                              "positive, so it cannot be turned into a price.")
    # Confidence rank: more history is better, a regime shift is worse — the
    # older half of the range was recorded under conditions the company is
    # no longer in.
    rank = 3.0 + min(1.0, len(ys) / 1250.0)
    if regime_shifted:
        rank -= 1.5
    return _method(
        "self_history", "Its own valuation history", basis,
        bear=prices[0], base=prices[1], bull=prices[2], n=len(ys), rank=rank,
        detail={"yield_bear_pct": y_bear, "yield_base_pct": y_base,
                "yield_bull_pct": y_bull, "eps": eps,
                "window": window_label, "regime_shifted": bool(regime_shifted),
                "note": ("The valuation level itself has shifted, so this "
                         "method is ranked below the others."
                         if regime_shifted else "")})


def method_peers_trailing(eps_ttm, peer_multiples, aggregate_pe=None,
                          level: str | None = None, cfg=None) -> dict:
    """B. Today's reported earnings at what comparable businesses cost.

    Trailing GAAP on both sides. The subject's GAAP earnings are multiplied
    by peer GAAP multiples — never by a forward multiple, which would price
    one company's audited past with another company's forecast future.
    """
    cfg = cfg or {}
    eps = _num(eps_ttm)
    mults = [m for m in _clean(peer_multiples) if m > 0]
    agg = _num(aggregate_pe)
    basis = ("GAAP trailing earnings per share at comparable companies' "
             "GAAP trailing multiples")
    if eps is None or eps <= 0:
        return _method("peers_trailing", "Comparable companies", basis,
                       reason="Trailing earnings are zero or negative.")
    if level in (None, "", "BROAD BENCHMARK"):
        return _method("peers_trailing", "Comparable companies", basis,
                       n=len(mults),
                       reason=("The only group available is a broad market "
                               "benchmark. Pricing this company off a group "
                               "that is not in its business would be "
                               "arithmetic rather than comparison."))
    if len(mults) < MIN_PEERS_FOR_VALUE:
        return _method("peers_trailing", "Comparable companies", basis,
                       n=len(mults),
                       reason=(f"Only {len(mults)} comparable companies have a "
                               f"positive trailing multiple — fewer than the "
                               f"{MIN_PEERS_FOR_VALUE} needed for a "
                               f"distribution."))
    m_bear = quantile(mults, float(cfg_get(cfg, "peer_bear_percentile")))
    m_bull = quantile(mults, float(cfg_get(cfg, "peer_bull_percentile")))
    # The aggregate group multiple — total market value of the profitable
    # members over their total earnings — is the base where it exists. It is
    # what a group actually costs; an average of ratios is not.
    m_base = agg if (agg and agg > 0) else quantile(mults, 0.5)
    rank = 2.0 + (0.6 if level == "DIRECT PEERS" else
                  0.3 if level == "INDUSTRY" else 0.0) \
        + min(0.4, len(mults) / 60.0)
    return _method(
        "peers_trailing", "Comparable companies", basis,
        bear=eps * m_bear, base=eps * m_base, bull=eps * m_bull,
        n=len(mults), rank=rank,
        detail={"multiple_bear": m_bear, "multiple_base": m_base,
                "multiple_bull": m_bull, "eps": eps, "level": level,
                "aggregate_used": bool(agg and agg > 0)})


def method_peers_forward(eps_forward, peer_forward_multiples,
                         aggregate_forward_pe=None, level: str | None = None,
                         cfg=None) -> dict:
    """B-forward. The same comparison on the analyst basis, both sides.

    This is the method the specification asks for first, and it stays
    unavailable until a provider supplies FORWARD multiples for the peer
    group. It is not approximated from trailing peer multiples: that would
    be exactly the silent basis mix this module exists to prevent.
    """
    cfg = cfg or {}
    eps = _num(eps_forward)
    mults = [m for m in _clean(peer_forward_multiples) if m > 0]
    basis = ("Adjusted analyst forward earnings per share at comparable "
             "companies' forward multiples")
    if eps is None or eps <= 0:
        return _method("peers_forward", "Comparable companies, forward basis",
                       basis,
                       reason=("No positive forward earnings estimate is "
                               "available for this company."))
    if len(mults) < MIN_PEERS_FOR_VALUE:
        return _method("peers_forward", "Comparable companies, forward basis",
                       basis, n=len(mults),
                       reason=("Forward multiples are not available for the "
                               "peer group. No free source publishes analyst "
                               "estimates for a whole industry, and building "
                               "this from trailing peer multiples would mix "
                               "an adjusted forward figure with a GAAP "
                               "trailing one."))
    if level in (None, "", "BROAD BENCHMARK"):
        return _method("peers_forward", "Comparable companies, forward basis",
                       basis, n=len(mults),
                       reason="Only a broad market benchmark is available.")
    agg = _num(aggregate_forward_pe)
    m_bear = quantile(mults, float(cfg_get(cfg, "peer_bear_percentile")))
    m_bull = quantile(mults, float(cfg_get(cfg, "peer_bull_percentile")))
    m_base = agg if (agg and agg > 0) else quantile(mults, 0.5)
    return _method(
        "peers_forward", "Comparable companies, forward basis", basis,
        bear=eps * m_bear, base=eps * m_base, bull=eps * m_bull,
        n=len(mults), rank=2.5,
        detail={"multiple_bear": m_bear, "multiple_base": m_base,
                "multiple_bull": m_bull, "eps": eps, "level": level})


def normalize_fcf(values, periods: int = 5) -> dict:
    """A representative free cash flow, not the last one.

    The median of the recent trailing-twelve-month readings, because one
    quarter of unusual working capital or a single large acquisition of
    equipment should not become the company's permanent cash generation.
    """
    vals = _clean(values)[-max(1, int(periods)):]
    if len(vals) < MIN_FCF_PERIODS:
        return {"available": False, "value": None, "n": len(vals),
                "reason": (f"Only {len(vals)} trailing free-cash-flow readings "
                           f"are on file — fewer than the {MIN_FCF_PERIODS} "
                           f"needed to call one of them normal.")}
    med = quantile(vals, 0.5)
    if med is None or med <= 0:
        return {"available": False, "value": med, "n": len(vals),
                "reason": ("Normalized free cash flow is not positive, so "
                           "there is nothing to apply a cash-flow yield to.")}
    return {"available": True, "value": med, "n": len(vals),
            "min": min(vals), "max": max(vals), "reason": ""}


def method_fcf(normalized_fcf, shares_outstanding, fcf_yields_pct,
               cfg=None) -> dict:
    """C. Normalized cash generation at the cash-flow yield this company has
    actually been valued on.

    The yield comes from the company's own history for the same reason the
    earnings method does: a "defensible normalized FCF yield" chosen by hand
    is a number picked to produce an answer.
    """
    cfg = cfg or {}
    fcf = _num(normalized_fcf)
    sh = _num(shares_outstanding)
    ys = _clean(fcf_yields_pct)
    basis = ("Normalized reported free cash flow at this company's own "
             "point-in-time free-cash-flow-yield history")
    if fcf is None or fcf <= 0:
        return _method("fcf", "Normalized free cash flow", basis,
                       reason="Normalized free cash flow is not positive.")
    if sh is None or sh <= 0:
        return _method("fcf", "Normalized free cash flow", basis,
                       reason="The share count is not available, so a "
                              "company-level value cannot be put per share.")
    if len(ys) < MIN_SELF_OBSERVATIONS:
        return _method("fcf", "Normalized free cash flow", basis, n=len(ys),
                       reason=(f"Only {len(ys)} daily free-cash-flow-yield "
                               f"observations are available — fewer than the "
                               f"{MIN_SELF_OBSERVATIONS} this method needs."))
    y_bear = quantile(ys, float(cfg_get(cfg, "fcf_bear_yield_percentile")))
    y_base = quantile(ys, float(cfg_get(cfg, "fcf_base_yield_percentile")))
    y_bull = quantile(ys, float(cfg_get(cfg, "fcf_bull_yield_percentile")))
    out = []
    for y in (y_bear, y_base, y_bull):
        out.append(fcf / (y / 100.0) / sh if y and y > 0 else None)
    if out[1] is None:
        return _method("fcf", "Normalized free cash flow", basis, n=len(ys),
                       reason="The median historical free-cash-flow yield is "
                              "not positive.")
    return _method(
        "fcf", "Normalized free cash flow", basis,
        bear=out[0], base=out[1], bull=out[2], n=len(ys), rank=2.2,
        detail={"yield_bear_pct": y_bear, "yield_base_pct": y_base,
                "yield_bull_pct": y_bull, "normalized_fcf": fcf,
                "shares": sh})


# ══════════════════════════════════════════════════════════════════════════
# COMBINING THEM
# ══════════════════════════════════════════════════════════════════════════

def disagreement(bases) -> float | None:
    """(highest − lowest) ÷ lowest across the methods' Base values."""
    vals = [v for v in _clean(bases) if v > 0]
    if len(vals) < 2:
        return None
    return (max(vals) - min(vals)) / min(vals)


def confidence_for(spread, n_methods: int, cfg=None) -> dict:
    """HIGH / MODERATE / LOW / UNRELIABLE, and why."""
    cfg = cfg or {}
    hi = float(cfg_get(cfg, "spread_high_max"))
    mod = float(cfg_get(cfg, "spread_moderate_max"))
    low = float(cfg_get(cfg, "spread_low_max"))
    if n_methods <= 0:
        return {"level": "UNRELIABLE", "spread": None,
                "reason": "No valuation method could be built at all."}
    if spread is None:
        # One method standing alone has nothing disagreeing with it, which is
        # not the same as agreement. It is capped below HIGH on purpose.
        return {"level": "MODERATE", "spread": None,
                "reason": ("Only one valuation method could be built, so "
                           "there is nothing to cross-check it against. A "
                           "single method is never rated HIGH here — silence "
                           "is not agreement.")}
    if spread <= hi:
        level = "HIGH"
    elif spread <= mod:
        level = "MODERATE"
    elif spread <= low:
        level = "LOW"
    else:
        level = "UNRELIABLE"
    return {"level": level, "spread": spread,
            "reason": (f"The {n_methods} valuation methods disagree by "
                       f"{spread * 100:.0f}% between the highest and the "
                       f"lowest. Bands: within {hi * 100:.0f}% is HIGH, "
                       f"{hi * 100:.0f}–{mod * 100:.0f}% MODERATE, "
                       f"{mod * 100:.0f}–{low * 100:.0f}% LOW, wider is "
                       f"UNRELIABLE. These bands are a stated convention, "
                       f"not a tested result.")}


def fair_value(methods, price=None, cfg=None,
               business_type: dict | None = None) -> dict:
    """Bear / Base / Bull, confidence, credited value and the buy zone."""
    cfg = cfg or {}
    btype = (business_type or {}).get("type")
    if btype in ("BANK", "INSURANCE", "BROKER", "REIT"):
        return {"available": False, "verdict": SPECIALIZED,
                "confidence": {"level": "UNRELIABLE", "spread": None,
                               "reason": SPECIALIZED},
                "methods": methods or [],
                "reason": ((business_type or {}).get("note") or "")
                + " A generic Bear/Base/Bull built on earnings and free cash "
                  "flow would be a confident-looking number with nothing "
                  "behind it.",
                "business_type": btype}
    if btype == "UNPROFITABLE":
        return {"available": False, "verdict": "INSUFFICIENT DATA",
                "confidence": {"level": "UNRELIABLE", "spread": None,
                               "reason": "The company is losing money."},
                "methods": methods or [],
                "reason": ("The company is not profitable. Every method here "
                           "prices earnings or cash generation, and there is "
                           "none to price."),
                "business_type": btype}

    valid = [m for m in (methods or []) if m.get("available")]
    if not valid:
        return {"available": False, "verdict": "INSUFFICIENT DATA",
                "confidence": confidence_for(None, 0, cfg),
                "methods": methods or [],
                "reason": ("None of the three valuation methods could be "
                           "built from what is on file for this company."),
                "business_type": btype}

    bases = [m["base"] for m in valid]
    spread = disagreement(bases)
    conf = confidence_for(spread, len(valid), cfg)
    credit_map = cfg_get(cfg, "confidence_credit") or {}
    credit = float(credit_map.get(conf["level"],
                                  DEFAULTS["confidence_credit"][conf["level"]]))

    chosen = max(valid, key=lambda m: (m.get("confidence_rank") or 0.0,
                                       m.get("n") or 0))
    base = chosen["base"]
    # The range widens automatically when methods disagree: the pessimistic
    # end is the most pessimistic anyone offered and the optimistic end the
    # most optimistic. Nothing is averaged.
    bear = min(m["bear"] for m in valid if m.get("bear") is not None) \
        if any(m.get("bear") is not None for m in valid) else None
    bull = max(m["bull"] for m in valid if m.get("bull") is not None) \
        if any(m.get("bull") is not None for m in valid) else None
    if bear is not None and bear > base:
        bear = min(bear, base)
    if bull is not None and bull < base:
        bull = max(bull, base)

    credited = None
    if bear is not None and base is not None:
        credited = bear + credit * (base - bear)
    mos = float(cfg_get(cfg, "min_margin_of_safety"))
    zone = credited * (1.0 - mos) if credited is not None else None

    p = _num(price)
    disc = None
    if p is not None and zone and zone > 0:
        # Negative = trading BELOW the buy zone (a discount); positive = above.
        disc = (p / zone - 1.0) * 100.0

    return {
        "available": True, "verdict": None,
        "bear": bear, "base": base, "bull": bull,
        "base_method": chosen["key"], "base_method_label": chosen["label"],
        "base_method_basis": chosen["basis"],
        "confidence": conf, "confidence_level": conf["level"],
        "confidence_credit": credit,
        "credited": credited, "margin_of_safety": mos, "buy_zone": zone,
        "price": p, "premium_to_buy_zone_pct": disc,
        "n_methods": len(valid), "methods": methods or [],
        "business_type": btype,
        "reason": "",
        "credit_note": (
            f"Credited fair value = Bear + {credit:.0%} × (Base − Bear). The "
            f"confidence decides how far above the pessimistic case the "
            f"valuation is credited, so LOWER confidence LOWERS the price we "
            f"are willing to pay. The buy zone is that credited value less a "
            f"{mos:.0%} margin of safety."),
        "version": FAIR_VALUE_VERSION,
    }


# ══════════════════════════════════════════════════════════════════════════
# EXPECTED RETURN BRIDGE
# ══════════════════════════════════════════════════════════════════════════
#
# Three deliberate refusals live in this section.
#
# 1. Buyback yield is NOT added on top. A buyback shrinks the diluted share
#    count, which raises earnings per share, which is already the first
#    contribution below. Adding it again double-counts the same cash.
# 2. Dividend yield is NOT added to the price return. A dividend is cash in
#    hand on a date; it is compounded to the horizon at a stated rate and
#    enters terminal wealth, which is what actually happens to it.
# 3. The multiple at the horizon is on the SAME basis as the multiple today.
#    A bridge that starts on trailing GAAP and lands on a forward adjusted
#    multiple has manufactured most of its own answer.

def growth_scenarios(growth_rates_pct, cfg=None) -> dict:
    """Bear / base / bull annual earnings growth from this company's own
    realized record, clamped to a stated band.

    Percentiles of realized year-over-year growth, not a forecast. The clamp
    matters: compounding the 75th percentile of one-year growth over three
    years is how a spreadsheet talks itself into 40% a year forever.
    """
    cfg = cfg or {}
    vals = _clean(growth_rates_pct)
    if len(vals) < 4:
        return {"available": False, "n": len(vals),
                "reason": (f"Only {len(vals)} year-over-year growth readings "
                           f"could be rebuilt from the filings — too few to "
                           f"take percentiles of.")}
    floor = float(cfg_get(cfg, "growth_floor_pct"))
    cap = float(cfg_get(cfg, "growth_cap_pct"))
    out = {"available": True, "n": len(vals), "floor_pct": floor,
           "cap_pct": cap, "reason": "", "clamped": []}
    for name, key in (("bear", "growth_bear_percentile"),
                      ("base", "growth_base_percentile"),
                      ("bull", "growth_bull_percentile")):
        raw = quantile(vals, float(cfg_get(cfg, key)))
        val = max(floor, min(cap, raw)) if raw is not None else None
        if raw is not None and val != raw:
            out["clamped"].append(name)
        out[name] = val
        out[f"{name}_raw"] = raw
    out["note"] = ("Percentiles of this company's own realized year-over-year "
                   f"earnings growth, clamped to {floor:.0f}%–{cap:.0f}% a "
                   f"year. It is a record, not a forecast.")
    return out


def multiple_scenarios(multiples, cfg=None, fallback=None) -> dict:
    """Bear / base / bull exit multiple from this company's own history."""
    cfg = cfg or {}
    vals = [m for m in _clean(multiples) if m > 0]
    if len(vals) < MIN_SELF_OBSERVATIONS:
        fb = _num(fallback)
        if fb is None or fb <= 0:
            return {"available": False, "n": len(vals),
                    "reason": (f"Only {len(vals)} valuation observations are "
                               f"available and no peer multiple could stand "
                               f"in, so there is no defensible multiple to "
                               f"exit at.")}
        return {"available": True, "n": 0, "bear": fb, "base": fb, "bull": fb,
                "source": "the comparable-company group",
                "reason": "",
                "note": ("This company's own multiple history is too short, "
                         "so all three scenarios use the peer group's "
                         "aggregate multiple. The scenarios then differ only "
                         "by earnings, which is stated rather than hidden.")}
    out = {"available": True, "n": len(vals), "reason": "",
           "source": "its own history",
           "note": "Percentiles of the multiple this company has actually "
                   "traded at over the window."}
    for name, key in (("bear", "multiple_bear_percentile"),
                      ("base", "multiple_base_percentile"),
                      ("bull", "multiple_bull_percentile")):
        out[name] = quantile(vals, float(cfg_get(cfg, key)))
    return out


def dividend_future_value(dps_ttm, growth_pct, years, rate_pct) -> dict:
    """Dividends received over the holding period, carried to the horizon.

    Quarterly payments, each growing with earnings and each compounded to the
    horizon at a stated reinvestment rate — the matching Treasury yield, not
    an assumed equity return. Modelling them quarterly rather than annually
    matters for the option horizons: a 45-day contract crosses about one
    dividend, and an annual model would either invent it or lose it.
    """
    dps = _num(dps_ttm)
    y = _num(years)
    r = _num(rate_pct)
    g = _num(growth_pct)
    if dps is None or dps <= 0 or y is None or y <= 0:
        return {"value": 0.0, "n_payments": 0, "available": dps is not None,
                "reason": ("This company pays no dividend." if dps == 0 else
                           "No dividend history is on file." if dps is None
                           else "")}
    r = 0.0 if r is None else r / 100.0
    g = 0.0 if g is None else g / 100.0
    per_quarter = dps / 4.0
    total = 0.0
    n = 0
    i = 1
    while i / 4.0 <= y + 1e-9:
        t = i / 4.0
        amount = per_quarter * ((1.0 + g) ** t)
        total += amount * ((1.0 + r) ** (y - t))
        n += 1
        i += 1
    return {"value": total, "n_payments": n, "available": True,
            "per_quarter_now": per_quarter, "reinvest_rate_pct": r * 100.0,
            "reason": "",
            "note": (f"{n} quarterly payment{'s' if n != 1 else ''}, each "
                     f"growing with earnings and each carried to the horizon "
                     f"at {r * 100:.2f}% — the matching Treasury yield, "
                     f"stated rather than assumed.")}


def scenario_path(price, eps_ttm, growth_pct, exit_multiple, years,
                  dps_ttm=None, rate_pct=None, reversion_years=None) -> dict:
    """One scenario, end to end: earnings, multiple, price, dividends,
    terminal wealth, and the exact logarithmic attribution between them.

    `reversion_years` is how long the multiple is assumed to take to travel
    from where it is today to the scenario's exit multiple. It exists because
    the same function has to price a three-year share thesis and a
    forty-five-day put, and snapping the multiple to its target inside six
    weeks would have the fundamental scenarios doing all the work at exactly
    the horizon where they explain the least. The journey is geometric, so a
    horizon one third of the way there covers one third of the LOG distance
    and the attribution below still reconciles exactly. Leave it None for the
    full move.
    """
    p0 = _num(price)
    e0 = _num(eps_ttm)
    g = _num(growth_pct)
    m1 = _num(exit_multiple)
    y = _num(years)
    if p0 is None or p0 <= 0 or e0 is None or e0 <= 0 or g is None \
            or m1 is None or m1 <= 0 or y is None or y <= 0:
        return {"available": False,
                "reason": ("A scenario needs a positive price, positive "
                           "trailing earnings, a growth rate and an exit "
                           "multiple. One of them is missing.")}
    m0 = p0 / e0
    rv = _num(reversion_years)
    if rv is not None and rv > 0 and m0 > 0:
        w = min(1.0, y / rv)
        m1 = m0 * ((m1 / m0) ** w)
    e1 = e0 * ((1.0 + g / 100.0) ** y)
    p1 = e1 * m1
    div = dividend_future_value(dps_ttm, g, y, rate_pct)
    tw = p1 + (div.get("value") or 0.0)
    price_cagr = (p1 / p0) ** (1.0 / y) - 1.0
    total_cagr = (tw / p0) ** (1.0 / y) - 1.0 if tw > 0 else None

    contributions = None
    if e1 > 0 and m1 > 0 and tw > 0:
        # ln(TW/P0) = ln(E1/E0) + ln(M1/M0) + ln(TW/P1), exactly, because
        # E1·M1 = P1 and E0·M0 = P0. Annualized so the bars read in the same
        # units as the CAGR beside them.
        contributions = [
            {"driver": "Earnings growth",
             "value": math.log(e1 / e0) / y * 100.0},
            {"driver": "Multiple change",
             "value": math.log(m1 / m0) / y * 100.0},
            {"driver": "Dividends",
             "value": math.log(tw / p1) / y * 100.0},
        ]
    return {
        "available": True, "reason": "",
        "years": y, "eps_start": e0, "eps_end": e1, "growth_pct": g,
        "multiple_start": m0, "multiple_end": m1,
        "price_start": p0, "price_end": p1,
        "multiple_target": _num(exit_multiple),
        "multiple_reversion_years": rv,
        "dividends": div, "terminal_wealth": tw,
        "price_cagr_pct": price_cagr * 100.0,
        "total_cagr_pct": None if total_cagr is None else total_cagr * 100.0,
        "contributions": contributions,
        "unit": "log points a year",
        "note": ("The three bars add up EXACTLY to the total, because "
                 "earnings times the multiple IS the price and the dividend "
                 "leg is the rest of terminal wealth. Buyback yield is not a "
                 "fourth bar: a buyback shows up as earnings growth, and "
                 "adding it again would count the same cash twice."),
    }


def expected_return(price, eps_ttm, growth, multiples, years=None,
                    dps_ttm=None, rate_pct=None, cfg=None,
                    probabilities=None, reversion_years=None) -> dict:
    """The three-scenario bridge from today's price to terminal wealth."""
    cfg = cfg or {}
    y = _num(years) if years is not None else float(cfg_get(cfg, "horizon_years"))
    g = growth or {}
    m = multiples or {}
    if not g.get("available") or not m.get("available"):
        return {"available": False, "years": y,
                "reason": (g.get("reason") if not g.get("available")
                           else m.get("reason"))
                or "Not enough history to build scenarios.",
                "growth": g, "multiples": m}
    probs = probabilities or scenario_probabilities(cfg)
    out = {"available": True, "years": y, "reason": "",
           "growth": g, "multiples": m, "probabilities": probs,
           "rate_pct": _num(rate_pct), "dps_ttm": _num(dps_ttm),
           "scenarios": {}}
    weighted_tw = 0.0
    weighted_ok = True
    for s in SCENARIOS:
        path = scenario_path(price, eps_ttm, g.get(s), m.get(s), y,
                             dps_ttm=dps_ttm, rate_pct=rate_pct,
                             reversion_years=reversion_years)
        out["scenarios"][s] = path
        if path.get("available"):
            weighted_tw += float(probs.get(s) or 0.0) * path["terminal_wealth"]
        else:
            weighted_ok = False
    p0 = _num(price)
    if weighted_ok and p0 and p0 > 0 and y > 0 and weighted_tw > 0:
        out["weighted_terminal_wealth"] = weighted_tw
        out["weighted_total_cagr_pct"] = (
            (weighted_tw / p0) ** (1.0 / y) - 1.0) * 100.0
    else:
        out["weighted_terminal_wealth"] = None
        out["weighted_total_cagr_pct"] = None
        out["available"] = any(v.get("available")
                               for v in out["scenarios"].values())
        if not out["available"]:
            out["reason"] = (out["scenarios"]["base"].get("reason")
                             or "No scenario could be built.")
    return out


def scenario_probabilities(cfg=None, override=None) -> dict:
    """Bear / base / bull weights. Assumptions, normalized, never facts."""
    cfg = cfg or {}
    src = override or {s: cfg_get(cfg, f"prob_{s}") for s in SCENARIOS}
    vals = {}
    for s in SCENARIOS:
        v = _num(src.get(s))
        vals[s] = max(0.0, v if v is not None else DEFAULTS[f"prob_{s}"])
    total = sum(vals.values())
    if total <= 0:
        vals = {s: DEFAULTS[f"prob_{s}"] for s in SCENARIOS}
        total = 1.0
    return {s: vals[s] / total for s in SCENARIOS}


# ══════════════════════════════════════════════════════════════════════════
# IMPLIED EXPECTATIONS — the reverse discounted cash flow
# ══════════════════════════════════════════════════════════════════════════
#
# This is an EXPECTATIONS instrument, not a valuation. It answers "what would
# this company have to do to be worth what it costs", which is a question
# with one honest answer, rather than "what is it worth", which a forward
# discounted cash flow answers with whatever the analyst put in.
#
# One unknown only: the five-year free-cash-flow growth rate. The discount
# rate and the terminal growth rate are STATED assumptions, displayed and
# varied across a grid, because a per-company weighted average cost of
# capital built from a beta estimated on five years of noise is a made-up
# number with three decimal places.

_G_LO, _G_HI = -0.60, 1.50


def dcf_value(fcf0, growth, years, discount, terminal_growth) -> float | None:
    """Present value of `years` of growing free cash flow plus a terminal
    value, all per the stated rates. Decimals, not percents."""
    f0 = _num(fcf0)
    g, r, gt = _num(growth), _num(discount), _num(terminal_growth)
    n = int(years or 0)
    if f0 is None or g is None or r is None or gt is None or n <= 0:
        return None
    if r <= gt:
        return None
    total = 0.0
    for t in range(1, n + 1):
        total += f0 * ((1.0 + g) ** t) / ((1.0 + r) ** t)
    terminal_fcf = f0 * ((1.0 + g) ** n) * (1.0 + gt)
    total += (terminal_fcf / (r - gt)) / ((1.0 + r) ** n)
    return total


def implied_growth(enterprise_value, fcf0, years=5, discount_pct=9.0,
                   terminal_growth_pct=3.0, tol: float = 1e-7,
                   max_iter: int = 200) -> dict:
    """Solve for the free-cash-flow growth the market is already paying for.

    Bisection on a bracketed interval rather than Newton-Raphson. The present
    value is monotonically increasing in the growth rate for positive cash
    flow, so bisection cannot diverge, cannot need a derivative and cannot
    wander off to a second root — none of which is true of a Newton step
    started at a bad guess on a function this convex.
    """
    ev = _num(enterprise_value)
    f0 = _num(fcf0)
    r = _num(discount_pct)
    gt = _num(terminal_growth_pct)
    n = int(years or 5)
    if ev is None or ev <= 0:
        return {"available": False,
                "reason": "Enterprise value is not available or not positive."}
    if f0 is None or f0 <= 0:
        return {"available": False,
                "reason": ("Normalized free cash flow is not positive, so "
                           "there is no cash flow whose growth the price "
                           "could be implying.")}
    if r is None or gt is None or r <= gt:
        return {"available": False,
                "reason": (f"The discount rate ({r}%) must exceed the terminal "
                           f"growth rate ({gt}%), or the terminal value is "
                           f"infinite.")}
    rr, gg = r / 100.0, gt / 100.0

    def f(g):
        v = dcf_value(f0, g, n, rr, gg)
        return None if v is None else v - ev

    lo_val, hi_val = f(_G_LO), f(_G_HI)
    if lo_val is None or hi_val is None:
        return {"available": False, "reason": "The model could not be valued."}
    if lo_val > 0:
        return {"available": False, "growth_pct": None, "bounded": "below",
                "reason": (f"Even at −{abs(_G_LO) * 100:.0f}% a year the "
                           f"discounted value exceeds the enterprise value. "
                           f"The market is implying a decline steeper than "
                           f"this model will search, which usually means the "
                           f"discount rate assumption is too low for this "
                           f"company rather than that the business is "
                           f"vanishing.")}
    if hi_val < 0:
        return {"available": False, "growth_pct": None, "bounded": "above",
                "reason": (f"Even at +{_G_HI * 100:.0f}% a year the discounted "
                           f"value falls short of the enterprise value. The "
                           f"price implies growth beyond what this model will "
                           f"search.")}
    lo, hi = _G_LO, _G_HI
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        v = f(mid)
        if v is None:
            return {"available": False, "reason": "The model could not be valued."}
        if abs(v) <= tol * max(1.0, abs(ev)):
            lo = hi = mid
            break
        if v < 0:
            lo = mid
        else:
            hi = mid
    g = (lo + hi) / 2.0
    return {"available": True, "growth_pct": g * 100.0, "reason": "",
            "enterprise_value": ev, "fcf0": f0, "years": n,
            "discount_pct": r, "terminal_growth_pct": gt,
            "method": "bisection on a bracketed interval",
            "iterations_bound": max_iter}


def implied_growth_grid(enterprise_value, fcf0, years=5, discount_pct=9.0,
                        terminal_growths_pct=None, rate_step_pct=1.0) -> dict:
    """The same solve across a grid of stated assumptions.

    A single implied-growth number reads like a measurement. The grid is what
    makes it read like what it is: the output of assumptions somebody chose.
    """
    r0 = _num(discount_pct)
    if r0 is None:
        return {"available": False, "reason": "No discount rate."}
    step = _num(rate_step_pct) or 1.0
    gts = _clean(terminal_growths_pct or DEFAULTS["sensitivity_terminal_growths_pct"])
    rates = [r0 - step, r0, r0 + step]
    cells = []
    vals = []
    for r in rates:
        row = []
        for gt in gts:
            res = implied_growth(enterprise_value, fcf0, years, r, gt)
            g = res.get("growth_pct") if res.get("available") else None
            if g is not None:
                vals.append(g)
            row.append({"discount_pct": r, "terminal_growth_pct": gt,
                        "growth_pct": g,
                        "reason": "" if res.get("available") else res.get("reason", "")})
        cells.append(row)
    return {"available": bool(vals), "rates_pct": rates,
            "terminal_growths_pct": gts, "cells": cells,
            "min_pct": min(vals) if vals else None,
            "max_pct": max(vals) if vals else None,
            "reason": "" if vals else ("No cell in the grid produced a "
                                       "solution inside the searched range."),
            "note": ("Rows are the discount rate, columns the terminal growth "
                     "rate. Both are stated assumptions rather than measured "
                     "quantities, which is why the answer is a grid.")}


def discount_rate(ten_year_pct, equity_risk_premium_pct=None, cfg=None) -> dict:
    """A stated discount rate: the long government yield plus a fixed equity
    premium.

    Deliberately NOT a per-company weighted average cost of capital. That
    would need a beta, and a beta estimated from five years of daily returns
    moves by whole points depending on the window chosen — a false precision
    that would then propagate into every cell of the grid above.
    """
    cfg = cfg or {}
    ty = _num(ten_year_pct)
    erp = _num(equity_risk_premium_pct)
    if erp is None:
        erp = float(cfg_get(cfg, "equity_risk_premium_pct"))
    if ty is None:
        return {"available": False, "pct": None,
                "reason": "The 10-year Treasury yield is not available."}
    return {"available": True, "pct": ty + erp, "ten_year_pct": ty,
            "equity_risk_premium_pct": erp, "reason": "",
            "basis": (f"{ty:.2f}% 10-year Treasury plus a stated "
                      f"{erp:.1f}-point equity risk premium. One assumption, "
                      f"displayed, varied across the grid — not a computed "
                      f"cost of capital dressed up as a measurement.")}


def expectations_gap(implied_pct, historical_pct) -> dict:
    """How far the market's implied growth sits above what the company has
    actually delivered."""
    i, h = _num(implied_pct), _num(historical_pct)
    if i is None or h is None:
        return {"available": False, "gap_pp": None,
                "reason": ("Needs both the market-implied growth and a "
                           "realized growth rate to compare it against.")}
    gap = i - h
    return {"available": True, "gap_pp": gap, "implied_pct": i,
            "historical_pct": h, "reason": "",
            "note": (f"The price implies {i:.1f}% a year against the "
                     f"{h:.1f}% a year this company has actually delivered — "
                     f"a gap of {gap:+.1f} points. A positive gap is not a "
                     f"sell signal; it is the size of the improvement being "
                     f"paid for in advance.")}


def cagr(first, last, years) -> float | None:
    a, b, y = _num(first), _num(last), _num(years)
    if a is None or b is None or y is None or a <= 0 or b <= 0 or y <= 0:
        return None
    return ((b / a) ** (1.0 / y) - 1.0) * 100.0
