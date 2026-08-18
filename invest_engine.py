"""invest_engine.py — the arithmetic behind the Investment tab.

Pure functions only: no network, no disk, no clock. `fundamentals.py` reads
the filings, `invest_scan.py` owns the providers and the store, and this
module does the maths in between so all of it is unit-testable offline.

Four questions, in the order the tab answers them:

  1. Is this a strong, profitable business?     → margins, free cash flow
  2. Are revenue and earnings growing?          → year-over-year, drivers
  3. Is it cheap against its own fundamentals?  → yields, against the 10-year
  4. What price would make it worth owning?     → the verdict's arithmetic

House rules enforced here rather than left to the caller:

* Nothing returns 0 for "unknown". Every function that cannot answer returns
  None or an explicit reason string.
* A percentage change off a negative or zero base is not a percentage change.
  A company that lost $100m and now loses $50m has not grown -50%; it has
  halved its loss, which the caller must say in words.
* Trailing GAAP earnings and forward analyst earnings are different bases and
  are never mixed inside one ratio. Every derived number carries the basis it
  was computed on.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from itertools import permutations

ENGINE_VERSION = "invest-1.0.0"

# How far reported EPS may sit from net income ÷ diluted shares before the
# breakdown has to say out loud that it is not describing the headline EPS.
#
# Measured on fourteen of this dashboard's own tickers: nine land inside 1.5%
# (Microsoft 0.02%, Costco 0.05%, JPMorgan 0.15%, Apple 1.46%). The ones that
# do not are exactly the cases the tolerance exists for — Realty Income at
# 5.3%, where preferred dividends and minority interests sit between net
# income and what common shareholders earn, and loss-makers like Plug Power
# (8.5%) and Cingulate (60%), where the share count used to divide a loss is
# the undiluted one. The bridge is still drawn for those; it is labelled.
EPS_IDENTITY_TOLERANCE = 0.03


# ── small helpers ───────────────────────────────────────────────────────────

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def safe_div(a, b):
    a, b = _num(a), _num(b)
    if a is None or b is None or b == 0:
        return None
    out = a / b
    return out if math.isfinite(out) else None


def growth(current, prior) -> dict:
    """Year-over-year change, with an honest answer when a percentage is
    meaningless.

    Returns {"pct": float|None, "direction": str, "note": str}. `pct` is only
    populated when the prior period is positive, because that is the only
    case where "up 12%" means what a reader thinks it means.
    """
    cur, pri = _num(current), _num(prior)
    if cur is None or pri is None:
        return {"pct": None, "direction": "unknown",
                "note": "No comparable year-ago period on file."}
    if pri > 0:
        # A positive base makes the percentage well defined whatever the
        # current value does, including a swing into a loss — that case gets
        # the number AND a sentence, because "-150%" alone reads like a
        # decline rather than a change of sign.
        pct = (cur / pri - 1.0) * 100.0
        note = ("" if cur >= 0 else
                f"Swung to a loss: from a profit of {pri:,.0f} to a loss of "
                f"{abs(cur):,.0f}.")
        return {"pct": pct, "direction": "up" if pct > 0 else
                ("flat" if pct == 0 else "down"), "note": note}
    if pri < 0 and cur < 0:
        note = ("Still a loss. It " +
                ("narrowed" if cur > pri else "widened") +
                f" from a loss of {abs(pri):,.0f} to a loss of {abs(cur):,.0f}.")
        return {"pct": None, "direction": "up" if cur > pri else "down",
                "note": note}
    if pri < 0 <= cur:
        return {"pct": None, "direction": "up",
                "note": f"Turned profitable: from a loss of {abs(pri):,.0f} "
                        f"to a profit of {cur:,.0f}. A percentage change from "
                        f"a loss is not a meaningful number."}
    return {"pct": None, "direction": "unknown",
            "note": "The year-ago figure is zero, so there is no percentage "
                    "to compute from it."}


# ── valuation ───────────────────────────────────────────────────────────────
#
# Yields are the internal language: they are additive against a Treasury
# yield, they survive a company earning nothing (the yield goes to zero
# rather than the ratio going to infinity), and they stay finite through a
# loss. P/E is still displayed, because that is the number everybody reads.

def earnings_yield(eps, price):
    """Trailing or forward earnings yield as a DECIMAL (0.05 = 5%)."""
    return safe_div(eps, price)


def fcf_yield(free_cash_flow, market_cap):
    return safe_div(free_cash_flow, market_cap)


def price_earnings(price, eps):
    """P/E, or None when earnings are zero or negative.

    A negative P/E is not a cheap stock, it is an arithmetic artifact, and
    printing "-14x" invites exactly the wrong reading.
    """
    e = _num(eps)
    if e is None or e <= 0:
        return None
    return safe_div(price, e)


def net_margin(net_income, revenue):
    return safe_div(net_income, revenue)


def market_cap(price, shares):
    p, s = _num(price), _num(shares)
    if p is None or s is None or p <= 0 or s <= 0:
        return None
    return p * s


# ── EPS decomposition ───────────────────────────────────────────────────────
#
# EPS = Revenue × (Net income ÷ Revenue) ÷ Diluted shares
#     = Revenue × Net margin ÷ Diluted shares
#
# Two ways to split a change in EPS between those three drivers. The log form
# is exact and scale-free but needs everything positive in both periods. The
# dollar form works through losses and is used whenever the logs are not
# defined, which is most of the interesting cases.

def _parts(period) -> tuple:
    return (_num((period or {}).get("revenue")),
            _num((period or {}).get("net_income")),
            _num((period or {}).get("shares")))


def log_decomposition(prior: dict, current: dict) -> dict | None:
    """Exact multiplicative attribution of the change in EPS.

        Δln(EPS) = Δln(Revenue) + Δln(Net margin) − Δln(Diluted shares)

    The three contributions sum to the total by construction — this is an
    identity, not an approximation, which is why it is preferred whenever it
    is legal. Returns None when any input is non-positive in either period,
    because the logarithm of a loss does not exist.
    """
    r0, n0, s0 = _parts(prior)
    r1, n1, s1 = _parts(current)
    vals = [r0, n0, s0, r1, n1, s1]
    if any(v is None or v <= 0 for v in vals):
        return None
    m0, m1 = n0 / r0, n1 / r1
    e0, e1 = n0 / s0, n1 / s1
    d_rev = math.log(r1 / r0)
    d_margin = math.log(m1 / m0)
    d_shares = -math.log(s1 / s0)
    total = math.log(e1 / e0)
    return {
        "method": "log",
        "label": "Earnings per share change",
        "unit": "log points",
        "total": total * 100.0,
        "eps_prior": e0, "eps_current": e1,
        "contributions": [
            {"driver": "Revenue", "value": d_rev * 100.0},
            {"driver": "Profit margin", "value": d_margin * 100.0},
            {"driver": "Share count", "value": d_shares * 100.0},
        ],
        "share_pct": _share_of(total, [d_rev, d_margin, d_shares]),
        "note": "Exact: the three contributions add up to the total change "
                "because Revenue × Margin ÷ Shares IS earnings per share.",
    }


def _share_of(total, parts):
    if not total:
        return None
    return [p / total * 100.0 for p in parts]


def dollar_bridge(prior: dict, current: dict) -> dict | None:
    """Attribution in dollars per share, valid through losses.

    Each driver's contribution is its Shapley value: the change in EPS from
    moving that one driver to its current value, averaged over every order in
    which the drivers could have been moved. Averaging over orderings is what
    removes the arbitrary "which factor gets the interaction term" choice
    that makes a naive sequential walk-down depend on the order it is
    written in. The Shapley values sum exactly to the change in EPS.

    Falls back to a two-driver split (earnings, share count) when revenue is
    missing or crosses zero, so a pre-revenue company still gets an honest
    bridge instead of nothing.
    """
    r0, n0, s0 = _parts(prior)
    r1, n1, s1 = _parts(current)
    if None in (n0, n1, s0, s1) or s0 <= 0 or s1 <= 0:
        return None

    if None in (r0, r1) or r0 <= 0 or r1 <= 0:
        drivers = ["Net income", "Share count"]
        base = {"Net income": n0, "Share count": s0}
        end = {"Net income": n1, "Share count": s1}

        def f(state):
            return state["Net income"] / state["Share count"]
        note = ("Revenue is zero or not reported for one of these periods, so "
                "there is no profit margin to split out. This bridge splits "
                "the change between earnings and share count only.")
    else:
        drivers = ["Revenue", "Profit margin", "Share count"]
        base = {"Revenue": r0, "Profit margin": n0 / r0, "Share count": s0}
        end = {"Revenue": r1, "Profit margin": n1 / r1, "Share count": s1}

        def f(state):
            return state["Revenue"] * state["Profit margin"] / state["Share count"]
        note = ("Earnings or margins are negative or cross zero, so the "
                "logarithmic split does not exist. Each bar is the driver's "
                "Shapley value — its effect averaged over every order the "
                "drivers could have moved in — and the bars add up exactly "
                "to the change in earnings per share.")

    contrib = {k: 0.0 for k in drivers}
    orders = list(permutations(drivers))
    for order in orders:
        state = dict(base)
        prev = f(state)
        for driver in order:
            state[driver] = end[driver]
            now = f(state)
            contrib[driver] += now - prev
            prev = now
    for k in contrib:
        contrib[k] /= len(orders)

    e0, e1 = f(base), f(end)
    return {
        "method": "dollar",
        "label": "Dollar EPS Bridge",
        "unit": "dollars per share",
        "total": e1 - e0,
        "eps_prior": e0, "eps_current": e1,
        "contributions": [{"driver": k, "value": contrib[k]} for k in drivers],
        "share_pct": None,
        "note": note,
    }


def eps_identity_ok(net_income, shares, reported_eps,
                    tolerance: float = EPS_IDENTITY_TOLERANCE) -> dict:
    """Does net income ÷ diluted shares actually reproduce reported EPS?

    When it does not — preferred dividends, income attributable to
    non-controlling interests, discontinued operations — a bridge built on
    the identity would not reconcile to the EPS shown at the top of the tab,
    and a bridge that does not reconcile is worse than no bridge.
    """
    derived = safe_div(net_income, shares)
    rep = _num(reported_eps)
    if derived is None or rep is None:
        return {"ok": False, "derived": derived, "reported": rep, "gap": None,
                "reason": "Net income, diluted shares or reported earnings "
                          "per share is missing."}
    gap = abs(derived - rep) / max(abs(rep), 1e-9)
    if gap <= tolerance:
        return {"ok": True, "derived": derived, "reported": rep, "gap": gap,
                "reason": ""}
    return {"ok": False, "derived": derived, "reported": rep, "gap": gap,
            "reason": (f"Net income divided by diluted shares comes to "
                       f"{derived:,.2f} against the {rep:,.2f} this company "
                       f"reports — {gap * 100:.1f}% apart. Preferred "
                       f"dividends, minority interests or discontinued "
                       f"operations sit between the two, so a driver "
                       f"breakdown built on that identity would not add up "
                       f"to the earnings per share shown above.")}


def decompose(prior: dict, current: dict, reported_eps_prior=None,
              reported_eps_current=None) -> dict:
    """Pick the honest attribution method and say which one was used.

    The breakdown always describes net income ÷ diluted shares, because that
    is the only quantity Revenue × Margin ÷ Shares can equal. When the
    company's own reported EPS differs from it by more than the tolerance,
    the result carries a warning naming both numbers rather than quietly
    presenting a bridge to a different EPS than the one shown above it.
    """
    out = log_decomposition(prior, current) or dollar_bridge(prior, current)
    if not out:
        return {"available": False, "method": None,
                "reason": "Revenue, net income or the diluted share count is "
                          "missing for one of the two periods, so there is "
                          "nothing to split."}

    warning = ""
    check = eps_identity_ok((current or {}).get("net_income"),
                            (current or {}).get("shares"),
                            reported_eps_current)
    if reported_eps_current is not None and not check["ok"] and check["gap"] is not None:
        warning = check["reason"]
    return {"available": True, "identity": check, "warning": warning, **out}


def reconciles(decomp: dict, tolerance: float = 1e-6) -> bool:
    """Do the displayed contributions add up to the displayed total?

    The tab states that they reconcile exactly, so this is asserted in the
    tests rather than trusted.
    """
    if not decomp or not decomp.get("available"):
        return False
    total = sum(c["value"] for c in decomp["contributions"])
    return abs(total - decomp["total"]) <= max(tolerance,
                                               abs(decomp["total"]) * 1e-9)


# ── chart normalization ─────────────────────────────────────────────────────

def normalize(points: list[dict], key: str = "value",
              base: float | None = None) -> list[dict]:
    """Rebase a series to 100 at its first point, for plotting price and
    earnings on one axis. Returns [] rather than a flat line when the base
    is zero or negative, because "100 × (−3 ÷ −5)" is not an index.
    """
    pts = [p for p in (points or []) if _num(p.get(key)) is not None]
    if not pts:
        return []
    b = _num(base) if base is not None else _num(pts[0][key])
    if b is None or b <= 0:
        return []
    return [{**p, "indexed": _num(p[key]) / b * 100.0} for p in pts]


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2 — the four-vector scorecard and everything it needs
# ══════════════════════════════════════════════════════════════════════════
#
# Phase 1 judged every company against one universal rule: earnings yield
# versus the 10-year Treasury plus a fixed cushion. That rule is arithmetic,
# not analysis. It marks Microsoft expensive for the same reason it marks a
# declining industrial cheap, and it has no way to tell a durable business
# trading at its own normal multiple from a melting one trading at a low one.
#
# Phase 2 replaces it with four INDEPENDENT dimensions — Quality, Growth,
# Valuation, Revisions — plus a value-trap check. They are deliberately never
# blended into one number: a single score would let strong growth hide an
# expensive price, which is the exact mistake the four-vector layout exists
# to prevent. Valuation is measured against the company's OWN history and
# against comparable businesses, never against a universal multiple.

SCORECARD_VERSION = "invest-scorecard-1.0.0"

# Below this, a peer group is not a distribution — it is a handful of
# companies, and ranking against it would read as precision it has not
# got. Scoring falls back to absolute bands and says so.
MIN_PEERS = 5

# Labels, not grades. A percentile is reported alongside so the label never
# has to carry more precision than it has.
SCORE_LABELS = ("WEAK", "BELOW AVERAGE", "AVERAGE", "ABOVE AVERAGE", "STRONG")
NOT_RATED = "NOT RATED"


def label_for(score) -> str:
    """Map a 0-100 rank onto one of five words."""
    s = _num(score)
    if s is None:
        return NOT_RATED
    if s >= 80:
        return "STRONG"
    if s >= 60:
        return "ABOVE AVERAGE"
    if s >= 40:
        return "AVERAGE"
    if s >= 20:
        return "BELOW AVERAGE"
    return "WEAK"


# ── distribution maths ──────────────────────────────────────────────────────

def quantile(values, q: float):
    """Linear-interpolated quantile. `q` in 0..1."""
    vals = sorted(v for v in (_num(x) for x in (values or [])) if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * max(0.0, min(1.0, q))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def percentile_rank(values, value):
    """Where `value` sits in `values`, 0-100.

    Ties count as half, which is the standard mid-rank convention and keeps
    a value equal to every observation at 50 rather than 0 or 100.
    """
    v = _num(value)
    vals = [x for x in (_num(y) for y in (values or [])) if x is not None]
    if v is None or not vals:
        return None
    below = sum(1 for x in vals if x < v)
    equal = sum(1 for x in vals if x == v)
    return (below + 0.5 * equal) / len(vals) * 100.0


def distribution(values, current=None) -> dict:
    """Median, 10th and 90th percentile, count, and where `current` sits."""
    vals = [x for x in (_num(y) for y in (values or [])) if x is not None]
    if not vals:
        return {"n": 0, "median": None, "p10": None, "p90": None,
                "percentile": None, "min": None, "max": None}
    return {"n": len(vals), "median": quantile(vals, 0.5),
            "p10": quantile(vals, 0.10), "p90": quantile(vals, 0.90),
            "min": min(vals), "max": max(vals),
            "percentile": percentile_rank(vals, current)}


def rank_within(value, peer_values, higher_is_better: bool = True):
    """0-100 rank of `value` among peers, oriented so 100 is always good."""
    pr = percentile_rank(peer_values, value)
    if pr is None:
        return None
    return pr if higher_is_better else 100.0 - pr


def band_score(value, bands, higher_is_better: bool = True):
    """Coarse absolute fallback when there are not enough peers to rank
    against. `bands` is four ascending cut points splitting 0-100 into five.

    Deliberately coarse: an absolute cut-off is a blunt instrument across
    industries, so this is used only as a stated fallback and never as the
    primary scoring path.
    """
    v = _num(value)
    if v is None or not bands or len(bands) != 4:
        return None
    steps = [10.0, 30.0, 50.0, 70.0, 90.0]
    idx = sum(1 for b in bands if v >= b)
    score = steps[idx]
    return score if higher_is_better else 100.0 - score


# ── valuation regime ────────────────────────────────────────────────────────

# A move of at least this share of the earlier median counts even when
# the earlier period had almost no spread of its own.
_REGIME_MIN_MOVE = 0.10


def regime_shift(points, recent_years: float = 2.0,
                 window_years: float = 5.0, ratio: float = 1.0) -> dict:
    """Has this company's own valuation range moved to a new level?

    Deliberately simple and inspectable: split the window into the recent
    slice and everything before it, and compare the two medians against the
    SPREAD of the earlier slice. A shift worth calling is one bigger than the
    earlier period's own 10th-to-90th-percentile spread — that is a level
    change, not a wiggle. No hidden model, and every number in the verdict
    sentence is one a reader can find on the chart.

    `points` is [{"date": iso, "value": float}, ...] oldest first.
    """
    pts = [p for p in (points or [])
           if p.get("date") and _num(p.get("value")) is not None]
    if len(pts) < 40:
        return {"available": False, "shifted": False,
                "reason": f"Only {len(pts)} valuation observations in the "
                          f"window — too few to say whether the level moved."}
    pts.sort(key=lambda p: p["date"])
    last = pts[-1]["date"]
    try:
        cut = (date.fromisoformat(last[:10])
               - timedelta(days=int(recent_years * 365.25))).isoformat()
    except ValueError:                               # pragma: no cover
        return {"available": False, "shifted": False,
                "reason": "Unreadable dates in the valuation history."}
    recent = [_num(p["value"]) for p in pts if p["date"] >= cut]
    earlier = [_num(p["value"]) for p in pts if p["date"] < cut]
    if len(recent) < 20 or len(earlier) < 20:
        return {"available": False, "shifted": False,
                "reason": "The window does not split into two periods long "
                          "enough to compare."}
    r_med, e_med = quantile(recent, 0.5), quantile(earlier, 0.5)
    e_spread = (quantile(earlier, 0.90) or 0) - (quantile(earlier, 0.10) or 0)
    shift = r_med - e_med
    # The bar is the earlier period's own spread — but with a floor, because
    # an earlier period that barely moved has a spread near zero, and without
    # the floor that stability would make a shift impossible to detect at
    # exactly the moment it is most obvious.
    threshold = max(abs(e_spread) * ratio, abs(e_med) * _REGIME_MIN_MOVE)
    shifted = threshold > 0 and abs(shift) > threshold
    return {"available": True, "shifted": bool(shifted),
            "recent_median": r_med, "earlier_median": e_med,
            "shift": shift, "earlier_spread": e_spread, "threshold": threshold,
            "recent_n": len(recent), "earlier_n": len(earlier),
            "split_date": cut, "recent_years": recent_years,
            "reason": ""}


# ── business type ───────────────────────────────────────────────────────────
#
# Standard Industrial Classification, assigned by the SEC and carried on
# every filer's submissions record. Used to REFUSE calculations, not to
# perform them: a bank has no meaningful net-debt-to-EBITDA, a REIT's
# earnings understate its cash generation, and neither is served by a generic
# free-cash-flow model dressed up as an answer.

BUSINESS_TYPES = ("STANDARD", "BANK", "INSURANCE", "REIT", "BROKER",
                  "CYCLICAL", "UNPROFITABLE", "UNSUPPORTED")

# SIC ranges, from the SEC's own office assignments.
_SIC_BANK = ((6020, 6036), (6099, 6099), (6120, 6199))
_SIC_INSURANCE = ((6300, 6411),)
_SIC_REIT = ((6798, 6798), (6500, 6552), (6798, 6799))
_SIC_BROKER = ((6200, 6299),)
_SIC_CYCLICAL = ((1000, 1099), (1200, 1499), (2911, 2911),
                 (3310, 3399), (2800, 2899), (1531, 1731))

_TYPE_LABEL = {
    "STANDARD": "Standard operating company",
    "BANK": "Bank or lender",
    "INSURANCE": "Insurer",
    "REIT": "Real estate investment trust",
    "BROKER": "Broker, exchange or asset manager",
    "CYCLICAL": "Cyclical or commodity producer",
    "UNPROFITABLE": "Currently unprofitable",
    "UNSUPPORTED": "Not supported",
}

# What the generic engine may compute for each type. A False here becomes
# "SPECIALIZED MODEL REQUIRED" on screen, never a plausible-looking number.
_TYPE_ALLOWS = {
    "STANDARD": {"fcf", "leverage", "roic", "operating_margin", "earnings_yield"},
    "CYCLICAL": {"fcf", "leverage", "roic", "operating_margin", "earnings_yield"},
    "UNPROFITABLE": {"fcf", "leverage", "operating_margin"},
    "BANK": {"earnings_yield"},
    "INSURANCE": {"earnings_yield"},
    "BROKER": {"earnings_yield"},
    "REIT": {"earnings_yield", "operating_margin"},
    "UNSUPPORTED": set(),
}

_TYPE_NOTE = {
    "BANK": "Borrowing IS the raw material of a bank, so net debt, free cash "
            "flow and return on invested capital do not mean for a bank what "
            "they mean for a manufacturer. Those measures are withheld rather "
            "than computed into a number that looks usable and is not.",
    "INSURANCE": "An insurer's balance sheet holds float — money owed to "
                 "policyholders — which a generic leverage or cash-flow model "
                 "reads as either debt or spare cash. Both readings are wrong.",
    "BROKER": "A broker's balance sheet carries customer assets and segregated "
              "cash, so leverage and free-cash-flow measures built for "
              "operating companies do not describe it.",
    "REIT": "Real estate trusts run large depreciation charges against "
            "properties that are not actually wearing out at that rate, so "
            "reported earnings understate cash generation. Funds from "
            "operations is the right measure, and it is what the property "
            "trust panel uses instead of reported earnings.",
    "UNPROFITABLE": "The company is losing money, so earnings-based valuation "
                    "has no denominator to work with.",
    "UNSUPPORTED": "There is not enough reported data to classify or value "
                   "this filer.",
}


def _in(sic_int, ranges) -> bool:
    return any(lo <= sic_int <= hi for lo, hi in ranges)


def business_type(sic: str | None, eps_ttm=None, ok: bool = True) -> dict:
    """Classify a filer before anything is computed for it."""
    if not ok:
        return _btype("UNSUPPORTED")
    code = None
    try:
        code = int(str(sic).strip()) if sic else None
    except (TypeError, ValueError):
        code = None
    if code is not None:
        if _in(code, _SIC_BANK):
            return _btype("BANK", code)
        if _in(code, _SIC_INSURANCE):
            return _btype("INSURANCE", code)
        if _in(code, _SIC_REIT):
            return _btype("REIT", code)
        if _in(code, _SIC_BROKER):
            return _btype("BROKER", code)
    e = _num(eps_ttm)
    if e is not None and e <= 0:
        return _btype("UNPROFITABLE", code)
    if code is not None and _in(code, _SIC_CYCLICAL):
        return _btype("CYCLICAL", code)
    if code is None:
        return _btype("UNSUPPORTED")
    return _btype("STANDARD", code)


def _btype(kind, code=None) -> dict:
    return {"type": kind, "label": _TYPE_LABEL[kind], "sic": code,
            "allows": sorted(_TYPE_ALLOWS[kind]),
            "note": _TYPE_NOTE.get(kind, "")}


def allows(btype: dict, measure: str) -> bool:
    return measure in set((btype or {}).get("allows") or ())


SPECIALIZED = "SPECIALIZED MODEL REQUIRED"


# ── quality ─────────────────────────────────────────────────────────────────
#
# Six inputs, chosen to be non-duplicative: how much the business earns on
# the capital it uses, how much of its profit shows up as cash, whether
# margins are widening or narrowing, whether the share count is being reduced
# or diluted away, how much of revenue goes out as stock to employees, and
# how levered the balance sheet is. Each is optional and each shows its own
# reason when absent — Microsoft tags no combined depreciation figure at all,
# and that is a missing input, not a failing grade.

QUALITY_INPUTS = ("roic", "fcf_conversion", "operating_margin_trend",
                  "share_count_trend", "sbc_pct_revenue", "leverage")

QUALITY_LABEL = {
    "roic": "Return on invested capital",
    "fcf_conversion": "Free cash flow conversion",
    "operating_margin_trend": "Operating margin trend",
    "share_count_trend": "Share count trend",
    "sbc_pct_revenue": "Stock compensation as a share of revenue",
    "leverage": "Net debt to operating profit",
}

QUALITY_HIGHER_IS_BETTER = {
    "roic": True, "fcf_conversion": True, "operating_margin_trend": True,
    "share_count_trend": False,          # a rising share count is dilution
    "sbc_pct_revenue": False,            # more stock issued is worse
    "leverage": False,                   # more debt is worse
}

# Absolute fallback bands, used ONLY when a peer group is too small to rank
# against, and always labelled as the fallback.
QUALITY_BANDS = {
    "roic": [0.0, 8.0, 15.0, 25.0],
    "fcf_conversion": [0.0, 60.0, 90.0, 120.0],
    "operating_margin_trend": [-3.0, -0.5, 0.5, 3.0],
    "share_count_trend": [-2.0, 0.0, 1.0, 3.0],
    "sbc_pct_revenue": [1.0, 3.0, 6.0, 12.0],
    "leverage": [0.0, 1.5, 3.0, 4.5],
}


def roic(operating_income, tax_rate, equity, net_debt_value):
    """Operating profit after tax over the capital funding the business.

    Invested capital is equity plus net debt: what shareholders and lenders
    have actually tied up. Returns None when the capital base is zero or
    negative, where the ratio stops meaning anything.
    """
    op = _num(operating_income)
    tr = _num(tax_rate)
    eq = _num(equity)
    nd = _num(net_debt_value)
    if op is None or eq is None:
        return None
    tr = 0.21 if tr is None else max(0.0, min(0.60, tr))
    invested = eq + (nd or 0.0)
    if invested <= 0:
        return None
    return op * (1.0 - tr) / invested * 100.0


def effective_tax_rate(tax_expense, pretax_income):
    t, p = _num(tax_expense), _num(pretax_income)
    if t is None or p is None or p <= 0:
        return None
    return max(0.0, min(0.60, t / p))


def trend_slope(points) -> float | None:
    """Least-squares slope per year of [{"date": iso, "value": pct}, ...].

    Used for the margin trend so a company whose operating margin has ground
    down for three years scores worse than one holding steady at the same
    level — a snapshot cannot tell those apart.
    """
    pts = [(p["date"], _num(p.get("value"))) for p in (points or [])
           if p.get("date") and _num(p.get("value")) is not None]
    if len(pts) < 4:
        return None
    pts.sort()
    try:
        base = date.fromisoformat(pts[0][0][:10])
        xs = [(date.fromisoformat(d[:10]) - base).days / 365.25 for d, _v in pts]
    except ValueError:                               # pragma: no cover
        return None
    ys = [v for _d, v in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def quality_component(key, value, peer_values=None, reason: str = "",
                      allowed: bool = True) -> dict:
    """One quality input scored, with the reason it got what it got."""
    label = QUALITY_LABEL[key]
    if not allowed:
        return {"key": key, "label": label, "value": None, "score": None,
                "basis": SPECIALIZED, "reason": SPECIALIZED,
                "scored_against": None}
    v = _num(value)
    if v is None:
        return {"key": key, "label": label, "value": None, "score": None,
                "basis": None, "reason": reason or "Not reported.",
                "scored_against": None}
    higher = QUALITY_HIGHER_IS_BETTER[key]
    peers = [x for x in (_num(y) for y in (peer_values or [])) if x is not None]
    if len(peers) >= MIN_PEERS:
        return {"key": key, "label": label, "value": v,
                "score": rank_within(v, peers, higher), "reason": "",
                "scored_against": f"ranked against {len(peers)} comparable companies"}
    return {"key": key, "label": label, "value": v,
            "score": band_score(v, QUALITY_BANDS[key], higher), "reason": "",
            "scored_against": "scored on absolute bands — too few comparable "
                              "companies to rank against"}


def score_dimension(components, min_inputs: int = 2) -> dict:
    """Average the components that exist and say how many that was.

    Never fills a missing input with a neutral 50: a company with two known
    inputs is scored on two, and the coverage is printed next to the score so
    a thin score is visibly thin.
    """
    scored = [c for c in components if c.get("score") is not None]
    if len(scored) < min_inputs:
        return {"score": None, "label": NOT_RATED,
                "coverage": f"{len(scored)} of {len(components)} inputs available",
                "n_scored": len(scored), "n_total": len(components),
                "components": components,
                "reason": (f"Only {len(scored)} of the {len(components)} inputs "
                           f"could be built from this company's filings — "
                           f"fewer than the {min_inputs} needed to score.")}
    avg = sum(c["score"] for c in scored) / len(scored)
    return {"score": avg, "label": label_for(avg),
            "coverage": f"{len(scored)} of {len(components)} inputs available",
            "n_scored": len(scored), "n_total": len(components),
            "components": components, "reason": ""}



# ── growth ──────────────────────────────────────────────────────────────────
#
# Deliberately NOT a re-listing of the quality inputs. Revenue growth and EPS
# growth are the outcomes; margin and share-count contribution come straight
# from the Phase 1 earnings decomposition rather than being recomputed, so
# the same fact never gets counted twice under two names.

GROWTH_INPUTS = ("revenue_growth", "eps_growth", "forward_eps_growth")

GROWTH_LABEL = {
    "revenue_growth": "Revenue growth, year over year",
    "eps_growth": "Earnings per share growth, year over year",
    "forward_eps_growth": "Forward earnings growth, next year against this",
}

GROWTH_BANDS = {
    "revenue_growth": [0.0, 5.0, 12.0, 25.0],
    "eps_growth": [0.0, 6.0, 15.0, 30.0],
    "forward_eps_growth": [0.0, 5.0, 12.0, 25.0],
}


def growth_component(key, value, peer_values=None, reason: str = "") -> dict:
    label = GROWTH_LABEL[key]
    v = _num(value)
    if v is None:
        return {"key": key, "label": label, "value": None, "score": None,
                "reason": reason or "Not available.", "scored_against": None}
    peers = [x for x in (_num(y) for y in (peer_values or [])) if x is not None]
    if len(peers) >= MIN_PEERS:
        return {"key": key, "label": label, "value": v,
                "score": rank_within(v, peers, True), "reason": "",
                "scored_against": f"ranked against {len(peers)} comparable companies"}
    return {"key": key, "label": label, "value": v,
            "score": band_score(v, GROWTH_BANDS[key], True), "reason": "",
            "scored_against": "scored on absolute bands — too few comparable "
                              "companies to rank against"}


def growth_drivers(decomp: dict) -> dict:
    """Margin and share-count contribution, lifted from the Phase 1 bridge
    rather than recomputed, so the same movement is never double-counted."""
    if not decomp or not decomp.get("available"):
        return {"available": False,
                "reason": (decomp or {}).get("reason")
                          or "No earnings breakdown available."}
    by = {c["driver"]: c["value"] for c in decomp.get("contributions") or []}
    return {"available": True, "method": decomp.get("method"),
            "unit": decomp.get("unit"),
            "revenue": by.get("Revenue"),
            "margin": by.get("Profit margin"),
            "share_count": by.get("Share count"),
            "net_income": by.get("Net income"),
            "total": decomp.get("total"),
            "note": "Taken from the earnings breakdown above — the same "
                    "movement is not counted twice under a second name."}


# ── valuation ───────────────────────────────────────────────────────────────
#
# Two independent readings, kept separate because they answer different
# questions: cheap against ITSELF (own-history percentile) and cheap against
# COMPARABLE BUSINESSES (peer percentile). A great company is allowed to
# trade at a high multiple; what matters is whether it is high FOR THIS
# COMPANY and high FOR THIS KIND OF COMPANY.

def valuation_score(self_percentile, peer_percentile,
                    regime: dict | None = None) -> dict:
    """0-100 where 100 is cheap. Own history leads; peers confirm.

    A detected regime shift halves the weight on the company's own history,
    because the older half of that history was recorded under conditions the
    company is no longer in.
    """
    sp, pp = _num(self_percentile), _num(peer_percentile)
    shifted = bool((regime or {}).get("shifted"))
    parts, weights, basis = [], [], []
    if sp is not None:
        w = 0.5 if shifted else 1.0
        parts.append(sp); weights.append(w)
        basis.append("its own history" + (" (down-weighted: the valuation "
                     "level has shifted)" if shifted else ""))
    if pp is not None:
        parts.append(pp); weights.append(1.0 if sp is None else 0.6)
        basis.append("comparable companies")
    if not parts:
        return {"score": None, "label": NOT_RATED,
                "self_percentile": sp, "peer_percentile": pp,
                "regime_shifted": shifted, "basis": "",
                "reason": "Neither a usable valuation history nor a peer group "
                          "could be built, so there is nothing to be cheap or "
                          "expensive against."}
    total_w = sum(weights)
    score = sum(p * w for p, w in zip(parts, weights)) / total_w
    return {"score": score, "label": label_for(score),
            "self_percentile": sp, "peer_percentile": pp,
            "regime_shifted": shifted,
            "basis": " and ".join(basis), "reason": ""}


def price_for_percentile(eps, yield_at_percentile):
    """The share price at which the earnings yield reaches a given level.

    This is what turns "the 41st percentile" into an actionable number, and
    it is plain arithmetic: price = earnings ÷ yield.
    """
    e, y = _num(eps), _num(yield_at_percentile)
    if e is None or y is None or y <= 0 or e <= 0:
        return None
    return e / (y / 100.0)


def eps_for_percentile(price, yield_at_percentile):
    p, y = _num(price), _num(yield_at_percentile)
    if p is None or y is None or y <= 0 or p <= 0:
        return None
    return p * y / 100.0


# ── revisions ───────────────────────────────────────────────────────────────
#
# Coverage gate first. Three analysts moving in the same direction is three
# people agreeing, not a signal, so below the minimum this reports NOT RATED
# rather than a number that looks like the ones above it.

MIN_ANALYSTS = 4


def revisions_score(change_30d, change_90d, up_count=None, down_count=None,
                    analyst_count=None, min_analysts: int = MIN_ANALYSTS) -> dict:
    n = _num(analyst_count)
    if n is None or n < min_analysts:
        return {"score": None, "label": NOT_RATED,
                "analyst_count": n, "change_30d": _num(change_30d),
                "change_90d": _num(change_90d),
                "up": _num(up_count), "down": _num(down_count),
                "reason": (f"Only {int(n)} analysts cover this company"
                           if n is not None else
                           "The number of covering analysts is not available")
                          + f" — fewer than the {min_analysts} this dashboard "
                            f"requires before calling a revision trend "
                            f"meaningful."}
    c30, c90 = _num(change_30d), _num(change_90d)
    parts = [x for x in (c30, c90) if x is not None]
    if not parts:
        return {"score": None, "label": NOT_RATED, "analyst_count": n,
                "change_30d": c30, "change_90d": c90,
                "up": _num(up_count), "down": _num(down_count),
                "reason": "No revision figures were returned by the estimate "
                          "provider."}
    # Revision breadth already runs -100..+100, so mapping it onto 0..100 is
    # a shift, not a fitted curve.
    weighted = (c30 if c90 is None else
                (0.65 * c30 + 0.35 * c90) if c30 is not None else c90)
    score = max(0.0, min(100.0, 50.0 + weighted / 2.0))
    return {"score": score, "label": label_for(score), "analyst_count": n,
            "change_30d": c30, "change_90d": c90,
            "up": _num(up_count), "down": _num(down_count), "reason": ""}


# ── revision underreaction (EXPERIMENTAL, unvalidated) ──────────────────────
#
# The idea being recorded, not asserted: when analysts raise numbers and the
# share price has not moved with them, the market may not have finished
# repricing. This dashboard has no evidence for that yet — the prospective
# snapshot store is what will eventually test it — so the score is displayed,
# labelled EXPERIMENTAL, and deliberately kept OUT of the verdict.

def revision_intensity(eps_now, eps_90d_ago, price):
    """Scaled by PRICE, not by the old estimate.

    Dividing by a near-zero prior estimate produces a number in the hundreds
    of percent that says more about the denominator than the revision. Per
    dollar of share price, a five-cent raise is a five-cent raise whatever
    the base was.
    """
    now, then, p = _num(eps_now), _num(eps_90d_ago), _num(price)
    if now is None or then is None or p is None or p <= 0:
        return None
    return (now - then) / p * 100.0


def zscore(value, population):
    v = _num(value)
    vals = [x for x in (_num(y) for y in (population or [])) if x is not None]
    if v is None or len(vals) < MIN_PEERS:
        return None
    mean = sum(vals) / len(vals)
    var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    return (v - mean) / sd


def underreaction(revision_z, price_reaction_z) -> dict:
    """Revision strength minus how much the price already moved for it."""
    rz, pz = _num(revision_z), _num(price_reaction_z)
    if rz is None or pz is None:
        return {"available": False, "score": None,
                "reason": "Needs both a revision z-score and a price-reaction "
                          "z-score, each standardised across at least "
                          f"{MIN_PEERS} comparable companies.",
                "experimental": True}
    return {"available": True, "score": rz - pz, "revision_z": rz,
            "price_reaction_z": pz, "experimental": True,
            "reason": "",
            "note": "EXPERIMENTAL and unvalidated. This dashboard has not "
                    "tested whether it predicts anything, it takes no part in "
                    "the verdict, and it is recorded daily so that it can be "
                    "tested honestly later."}


def relative_return(stock_return_pct, benchmark_return_pct):
    s, b = _num(stock_return_pct), _num(benchmark_return_pct)
    if s is None or b is None:
        return None
    return s - b


# ── value trap ──────────────────────────────────────────────────────────────
#
# The failure mode this exists to stop: a stock reaches the cheap end of its
# own history BECAUSE the business is deteriorating, and a valuation-led
# verdict reads the falling price as an opportunity. Cheapness is not
# evidence of value; it is the question, and this is the check on it.
#
# No universal thresholds. Each signal fires on a DIRECTION of travel — a
# thing getting worse — rather than on a level, because the level that counts
# as bad differs by industry and the level is already scored elsewhere.

TRAP_LEVELS = ("LOW RISK", "MODERATE RISK", "HIGH RISK")

TRAP_SIGNALS = {
    "estimates_falling": "Forward earnings estimates are being cut",
    "revenue_deteriorating": "Revenue growth is deteriorating",
    "margin_deteriorating": "Operating margins are narrowing",
    "fcf_deteriorating": "Free cash flow is deteriorating",
    "leverage_rising": "Borrowings are rising against earnings",
    "dilution_rising": "The share count is growing",
    "structural_change": "The business changed structurally",
    "cyclical_peak": "Earnings may be at a cyclical peak",
}


def value_trap(signals: dict, cfg: dict | None = None,
               extra_labels: dict | None = None) -> dict:
    """Grade deterioration, not cheapness.

    `signals` maps a key from TRAP_SIGNALS to either None (not measurable) or
    {"active": bool, "detail": str}. Unmeasurable signals are counted and
    reported, never treated as "fine".

    `extra_labels` adds business-specific signals to the SAME grading — an
    insurer's reserve development, a broker's leverage — rather than putting
    them in a parallel score of their own. That is the point: a cheap insurer
    whose old reserves are proving inadequate has to be able to reach HIGH
    RISK by the ordinary route, because HIGH RISK is what stops the entry
    engine recommending anything bullish.
    """
    cfg = cfg or {}
    high_at = int(cfg.get("trap_high_signals", 3))
    moderate_at = int(cfg.get("trap_moderate_signals", 1))
    active, inactive, unknown = [], [], []
    labels = {**TRAP_SIGNALS, **(extra_labels or {})}
    for key, label in labels.items():
        sig = (signals or {}).get(key)
        if sig is None:
            unknown.append({"key": key, "label": label})
        elif sig.get("active"):
            active.append({"key": key, "label": label,
                           "detail": sig.get("detail") or ""})
        else:
            inactive.append({"key": key, "label": label,
                             "detail": sig.get("detail") or ""})
    n = len(active)
    level = ("HIGH RISK" if n >= high_at else
             "MODERATE RISK" if n >= moderate_at else "LOW RISK")
    if not active and len(unknown) >= len(labels) - 1:
        return {"level": NOT_RATED, "active": [], "inactive": inactive,
                "unknown": unknown, "n_active": 0,
                "reason": "Almost none of the deterioration signals could be "
                          "measured for this filer, so silence here is not "
                          "evidence that nothing is wrong."}
    return {"level": level, "active": active, "inactive": inactive,
            "unknown": unknown, "n_active": n,
            "reason": ""}


def deteriorating(current, prior, worse_when_lower: bool = True,
                  min_move: float = 0.0) -> bool | None:
    """Is this measure moving the wrong way by more than `min_move`?"""
    c, p = _num(current), _num(prior)
    if c is None or p is None:
        return None
    move = c - p
    return (move < -abs(min_move)) if worse_when_lower else (move > abs(min_move))


# ── earnings cycle ──────────────────────────────────────────────────────────
#
# Where in the reporting cycle this ticker sits. Phase 2 uses it to say how
# much to trust an estimate; a later phase will use it for option entry
# timing, which is why the states are named for the calendar rather than for
# any trading rule.

CYCLE_STATES = ("PRE-EARNINGS", "POST-EARNINGS FRESH", "NORMAL", "STALE",
                "UNKNOWN")


def earnings_cycle(today, next_date=None, last_date=None,
                   pre_days: int = 14, fresh_days: int = 21,
                   stale_days: int = 100) -> dict:
    """PRE-EARNINGS / POST-EARNINGS FRESH / NORMAL / STALE."""
    def _d(x):
        if not x:
            return None
        try:
            return date.fromisoformat(str(x)[:10])
        except ValueError:
            return None

    t, nxt, last = _d(today), _d(next_date), _d(last_date)
    if t is None:
        return {"state": "UNKNOWN", "days_to_next": None, "days_since_last": None,
                "reason": "No date to measure from."}
    to_next = (nxt - t).days if nxt else None
    since_last = (t - last).days if last else None
    if to_next is not None and 0 <= to_next <= pre_days:
        return {"state": "PRE-EARNINGS", "days_to_next": to_next,
                "days_since_last": since_last, "next_date": next_date,
                "last_date": last_date,
                "reason": f"Reports in {to_next} day{'s' if to_next != 1 else ''}. "
                          f"Estimates and multiples can change sharply on the day."}
    if since_last is not None and 0 <= since_last <= fresh_days:
        return {"state": "POST-EARNINGS FRESH", "days_to_next": to_next,
                "days_since_last": since_last, "next_date": next_date,
                "last_date": last_date,
                "reason": f"Reported {since_last} day{'s' if since_last != 1 else ''} "
                          f"ago, so the trailing figures and the estimates are "
                          f"both current."}
    if since_last is not None and since_last > stale_days:
        return {"state": "STALE", "days_to_next": to_next,
                "days_since_last": since_last, "next_date": next_date,
                "last_date": last_date,
                "reason": f"The last report was {since_last} days ago. Trailing "
                          f"figures that old describe a quarter that has "
                          f"already been overtaken."}
    if to_next is None and since_last is None:
        return {"state": "UNKNOWN", "days_to_next": None, "days_since_last": None,
                "reason": "No earnings dates are available for this ticker."}
    return {"state": "NORMAL", "days_to_next": to_next,
            "days_since_last": since_last, "next_date": next_date,
            "last_date": last_date, "reason": ""}


# ── drawdown history ────────────────────────────────────────────────────────
#
# Context only, and Beta is deliberately absent: Beta compresses a whole
# distribution into one number that says nothing about what this stock
# actually did when things went wrong. What it actually did is right here.

_STRESS_WINDOWS = [
    ("2020 crash", "2020-02-01", "2020-06-30"),
    ("2022 drawdown", "2022-01-01", "2022-12-31"),
]


def drawdowns(bars, recent_days: int = 365) -> dict:
    """Worst peak-to-trough falls over the window, plus named stress periods."""
    pts = [(str(b.get("date"))[:10], _num(b.get("close")))
           for b in (bars or [])
           if b.get("date") and _num(b.get("close")) is not None]
    if len(pts) < 30:
        return {"available": False,
                "reason": f"Only {len(pts)} daily closes available — too few "
                          f"to measure a drawdown."}
    pts.sort()
    worst = _worst_drawdown(pts)
    cutoff = pts[-1][0][:4] and _shift_days(pts[-1][0], recent_days)
    recent = _worst_drawdown([p for p in pts if p[0] >= cutoff]) if cutoff else None
    windows = []
    for label, start, end in _STRESS_WINDOWS:
        seg = [p for p in pts if start <= p[0] <= end]
        if len(seg) >= 20:
            w = _worst_drawdown(seg)
            if w:
                windows.append({"label": label, **w})
    return {"available": True, "max": worst, "recent": recent,
            "recent_days": recent_days, "windows": windows,
            "from": pts[0][0], "to": pts[-1][0], "reason": ""}


def _shift_days(iso: str, days: int) -> str | None:
    try:
        return (date.fromisoformat(iso[:10]) - timedelta(days=days)).isoformat()
    except ValueError:                               # pragma: no cover
        return None


def _worst_drawdown(pts):
    if len(pts) < 2:
        return None
    peak, peak_date = pts[0][1], pts[0][0]
    worst = {"pct": 0.0, "peak": peak, "peak_date": peak_date,
             "trough": peak, "trough_date": peak_date}
    for d, v in pts:
        if v > peak:
            peak, peak_date = v, d
        if peak > 0:
            dd = (v / peak - 1.0) * 100.0
            if dd < worst["pct"]:
                worst = {"pct": dd, "peak": peak, "peak_date": peak_date,
                         "trough": v, "trough_date": d}
    return worst if worst["pct"] < 0 else None


# ══════════════════════════════════════════════════════════════════════════
# THE VERDICT
# ══════════════════════════════════════════════════════════════════════════
#
# Phase 1 asked one question — is the earnings yield above the 10-year
# Treasury plus a cushion — and that single rule marked Apple and Microsoft
# WAIT for no better reason than a P/E above about fifteen. A universal
# multiple threshold cannot distinguish an excellent business priced fairly
# from a poor one priced cheaply, so it has been removed rather than tuned.
#
# The verdict now reads the four vectors plus the value-trap check. There is
# still NO weighted composite: the rules below are conditions on named
# dimensions, so any answer can be re-derived by hand from the same screen.

VERDICTS = ("ATTRACTIVE", "WATCH", "WAIT", "AVOID", "INSUFFICIENT DATA",
            SPECIALIZED)

VERDICT_DEFAULTS = {
    "attractive_valuation_score": 55.0,   # 0-100, higher = cheaper
    "expensive_valuation_score": 35.0,
    "min_quality_score": 50.0,
    "min_growth_score": 40.0,
    "weak_revisions_score": 35.0,
    "trap_high_signals": 3,
    "trap_moderate_signals": 1,
    "target_percentile": 0.5,             # "cheap" = its own 5Y median yield
}


def _vcfg(cfg, key):
    return (cfg or {}).get(key, VERDICT_DEFAULTS[key])


# Which payload block holds the model built for each specialized business,
# and what that block is called on screen. Used only to write the sentence
# that tells the reader where the real answer is.
_SPECIALIZED_PANEL = {
    "BANK": ("bank", "The lender measures"),
    "REIT": ("reit", "The property trust measures"),
    "INSURANCE": ("insurance", "The insurer measures"),
    "BROKER": ("broker", "The broker measures"),
}


def verdict(snap: dict, cfg: dict | None = None) -> dict:
    """ATTRACTIVE / WATCH / WAIT / AVOID / INSUFFICIENT DATA /
    SPECIALIZED MODEL REQUIRED.

    `snap` carries the four dimension results, the trap state, the business
    type and enough raw numbers to write the "what would change" sentence.
    """
    cfg = cfg or {}
    reasons: list[str] = []
    changes: list[str] = []

    btype = snap.get("business_type") or {}
    quality = snap.get("quality") or {}
    growth_d = snap.get("growth") or {}
    valuation = snap.get("valuation") or {}
    revisions = snap.get("revisions") or {}
    trap = snap.get("value_trap") or {}

    price = _num(snap.get("price"))
    eps_ttm = _num(snap.get("eps_ttm"))
    eps_fwd = _num(snap.get("eps_forward"))

    if price is None or price <= 0:
        return _verdict_out("INSUFFICIENT DATA", ["No current share price."], [])
    if btype.get("type") == "UNSUPPORTED":
        return _verdict_out("INSUFFICIENT DATA",
                            [btype.get("note") or "This filer cannot be "
                             "classified from its SEC record."], [])
    if eps_ttm is None and eps_fwd is None:
        return _verdict_out("INSUFFICIENT DATA",
                            ["No earnings per share on either basis — there is "
                             "nothing to value against."], [])

    # Business types whose generic scorecard would be built on holes get a
    # named refusal instead of a confident-looking answer.
    if btype.get("type") in ("BANK", "INSURANCE", "BROKER", "REIT"):
        reasons.append(f"{btype.get('label')}. {btype.get('note')}")
        if valuation.get("self_percentile") is not None:
            reasons.append(_self_line(valuation, snap))
        # This four-dimension scorecard is the generic one, and it stays
        # refused for these businesses whatever else exists. What it says
        # next depends on whether a model built for the business actually
        # produced numbers on this page: if it did, the reader is sent to
        # it rather than told nothing was built.
        panel = _SPECIALIZED_PANEL.get(btype.get("type")) or ()
        built = bool(panel and (snap.get(panel[0]) or {}).get("available"))
        if built:
            changes.append(f"A verdict for this business type comes from the "
                           f"model built for it, not from this scorecard. "
                           f"{panel[1]} above, and the entry decision below "
                           f"it, are the answer for this company.")
        else:
            changes.append("A verdict for this business type needs a model "
                           "built for it — book value and net interest "
                           "margin for a lender, funds from operations for a "
                           "property trust. That model cannot run on this "
                           "company's filings, and a generic one would be "
                           "worse than none.")
        return _verdict_out(SPECIALIZED, reasons, changes)

    q, g = _num(quality.get("score")), _num(growth_d.get("score"))
    v = _num(valuation.get("score"))
    r = _num(revisions.get("score"))
    trap_level = trap.get("level")

    # ── outright disqualifiers ──
    if eps_ttm is not None and eps_ttm <= 0 and (eps_fwd is None or eps_fwd <= 0):
        reasons.append(f"The company is losing money: trailing earnings are "
                       f"${eps_ttm:,.2f} a share and no profitable forward "
                       f"estimate is available.")
        changes.append("It would have to earn a profit before an "
                       "earnings-based verdict means anything.")
        return _verdict_out("AVOID", reasons, changes)

    if trap_level == "HIGH RISK":
        reasons.append(_trap_line(trap))
        if v is not None and v >= _vcfg(cfg, "attractive_valuation_score"):
            reasons.append("It is cheap against its own history, and that is "
                           "exactly the pattern a value trap makes: the price "
                           "fell because the business is deteriorating, not "
                           "before it.")
        changes.append("The deterioration listed above would have to stop —"
                       " estimates steadying and the operating trend turning"
                       " — before cheapness counts as an opportunity.")
        return _verdict_out("AVOID", reasons, changes, trap=True)

    # ── the constructive cases ──
    if q is not None:
        reasons.append(f"Quality {quality.get('label')} "
                       f"({q:.0f} of 100, {quality.get('coverage')}).")
    else:
        reasons.append(f"Quality could not be scored — {quality.get('reason')}")
    if g is not None:
        reasons.append(f"Growth {growth_d.get('label')} ({g:.0f} of 100).")
    if v is not None:
        reasons.append(_self_line(valuation, snap))
    if r is not None:
        reasons.append(f"Analyst revisions {revisions.get('label')} across "
                       f"{int(revisions.get('analyst_count') or 0)} analysts.")
    elif revisions.get("reason"):
        reasons.append(revisions["reason"])
    if trap_level in ("MODERATE RISK", "HIGH RISK"):
        reasons.append(_trap_line(trap))

    good_quality = q is not None and q >= _vcfg(cfg, "min_quality_score")
    ok_growth = g is None or g >= _vcfg(cfg, "min_growth_score")
    cheap = v is not None and v >= _vcfg(cfg, "attractive_valuation_score")
    expensive = v is not None and v <= _vcfg(cfg, "expensive_valuation_score")
    weak_revisions = r is not None and r <= _vcfg(cfg, "weak_revisions_score")

    if v is None:
        changes.append("Neither a usable valuation history nor a peer group "
                       "could be built, so there is no answer to whether the "
                       "price is reasonable.")
        return _verdict_out("WATCH", reasons, changes)

    if good_quality and ok_growth and cheap and not weak_revisions \
            and trap_level in ("LOW RISK", NOT_RATED):
        return _verdict_out("ATTRACTIVE", reasons, changes)

    # Expensive is expensive whatever the quality: this is precisely where a
    # blended score would let a strong Growth reading carry an overpriced
    # stock into ATTRACTIVE, and the four vectors exist to stop that.
    if expensive:
        changes.append(_target_line(snap, valuation, cfg))
        return _verdict_out("WAIT", reasons, changes)

    if weak_revisions and not good_quality:
        changes.append("Estimates would have to stop falling, and the quality "
                       "inputs above would have to improve, before this reads "
                       "as anything better than a hold.")
        return _verdict_out("WATCH", reasons, changes)

    changes.append(_target_line(snap, valuation, cfg))
    return _verdict_out("WATCH", reasons, changes)


def ordinal(n) -> str:
    """21 -> "21st". Printing "21th percentile" undercuts every careful
    sentence around it."""
    v = _num(n)
    if v is None:
        return "—"
    i = int(round(v))
    if 10 <= i % 100 <= 20:
        return f"{i}th"
    return f"{i}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(i % 10, 'th') }".replace(" ", "")


def _self_line(valuation: dict, snap: dict) -> str:
    sp = _num(valuation.get("self_percentile"))
    pp = _num(valuation.get("peer_percentile"))
    bits = []
    if sp is not None:
        ey = _num(snap.get("earnings_yield_pct"))
        bits.append(
            f"Its trailing earnings yield of {ey:.1f}% sits at the "
            f"{ordinal(sp)} percentile of its own "
            f"{snap.get('valuation_window') or '5-year'} history"
            if ey is not None else
            f"Valuation sits at the {ordinal(sp)} percentile of its own history")
    if pp is not None:
        bits.append(f"the {ordinal(pp)} percentile against comparable companies")
    if valuation.get("regime_shifted"):
        bits.append("though the valuation level itself has shifted, so the "
                    "older half of that history describes a different market "
                    "for this stock")
    return ("Valuation: " + ", and ".join(bits) + ".") if bits else \
        "Valuation could not be placed against any history."


def _trap_line(trap: dict) -> str:
    names = [a["label"].lower() for a in (trap.get("active") or [])]
    if not names:
        return f"Value trap risk {trap.get('level')}."
    return (f"Value trap risk {trap.get('level')} — "
            + "; ".join(names) + ".")


def _target_line(snap: dict, valuation: dict, cfg: dict | None) -> str:
    """The price and the earnings figure that would move the verdict."""
    eps = _num(snap.get("eps_ttm"))
    price = _num(snap.get("price"))
    target_yield = _num(snap.get("target_yield_pct"))
    sp = _num(valuation.get("self_percentile"))
    if target_yield is None or eps is None or price is None:
        return ("A cheaper price, or higher earnings, would move this — the "
                "exact levels need a valuation history that is not available "
                "for this ticker.")
    at_price = price_for_percentile(eps, target_yield)
    at_eps = eps_for_percentile(price, target_yield)
    where = (f"Today's earnings yield is at the {ordinal(sp)} percentile of "
             f"its own history. " if sp is not None else "")
    if at_price is None or at_eps is None:
        return where + "Positive earnings are needed to price the target."
    return (f"{where}Reaching its own median valuation — an earnings yield of "
            f"{target_yield:.1f}% — means about ${at_price:,.2f} a share at "
            f"today's earnings, or trailing earnings of ${at_eps:,.2f} a share "
            f"at today's price.")


def _verdict_out(label, reasons, changes, trap: bool = False) -> dict:
    return {"verdict": label, "reasons": reasons, "what_would_change": changes,
            "value_trap": trap, "engine": ENGINE_VERSION,
            "scorecard": SCORECARD_VERSION}
