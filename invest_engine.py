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


# ── Phase 1 verdict ─────────────────────────────────────────────────────────
#
# Five words, no score. A 0-100 "investment score" would imply that the
# distance between 61 and 68 means something; it would not. Each verdict is
# reproducible by hand from numbers printed on the same screen, and every
# WAIT or AVOID says what would have to change.

VERDICTS = ("ATTRACTIVE", "WATCH", "WAIT", "AVOID", "INSUFFICIENT DATA")

DEFAULTS = {
    "attractive_spread_pp": 2.0,
    "watch_spread_pp": 0.0,
    "min_revenue_growth_pct": 0.0,
    "min_fcf_yield_pct": 0.0,
    "estimate_cut_pct": -5.0,
    "fallback_treasury_pct": 4.0,
}


def _cfg(cfg: dict | None, key: str):
    return (cfg or {}).get(key, DEFAULTS[key])


def _pct(x):
    return None if x is None else x * 100.0


def verdict(snap: dict, cfg: dict | None = None) -> dict:
    """ATTRACTIVE / WATCH / WAIT / AVOID / INSUFFICIENT DATA.

    `snap` is the normalized Investment snapshot. Every threshold comes from
    thresholds.json so the rule is configuration, not a magic number buried
    in a branch.
    """
    cfg = cfg or {}
    price = _num(snap.get("price"))
    eps_ttm = _num(snap.get("eps_ttm"))
    eps_fwd = _num(snap.get("eps_forward"))
    rev_growth = _num(snap.get("revenue_growth_pct"))
    fcfy = _num(snap.get("fcf_yield_pct"))
    ten_year = _num(snap.get("treasury_10y_pct"))
    # Revision BREADTH: the net share of covering analysts who raised rather
    # than cut, from -100 to +100. Not a percentage change in the estimate.
    rev_trend = _num(snap.get("estimate_change_30d_pct"))

    reasons: list[str] = []
    changes: list[str] = []

    if price is None or price <= 0:
        return _verdict_out("INSUFFICIENT DATA", ["No current share price."], [],
                            None, None)
    if eps_ttm is None and eps_fwd is None:
        return _verdict_out(
            "INSUFFICIENT DATA",
            ["No earnings per share on either basis — nothing to value "
             "against."], [], None, None)

    # One basis, chosen and named. Forward earnings are what a buyer pays
    # for; trailing GAAP is what has actually happened. They are never
    # averaged together.
    if eps_fwd is not None and eps_fwd != 0:
        eps, basis = eps_fwd, "analyst forward earnings estimate"
    else:
        eps, basis = eps_ttm, "GAAP trailing twelve month earnings"

    treasury = ten_year
    treasury_note = ""
    if treasury is None:
        treasury = _cfg(cfg, "fallback_treasury_pct")
        treasury_note = (f" (the live 10-year Treasury yield was unavailable, "
                         f"so the {treasury:.1f}% standing assumption from "
                         f"the configuration was used)")

    ey = earnings_yield(eps, price)
    ey_pct = _pct(ey)
    spread = None if ey_pct is None else ey_pct - treasury
    attractive_at = _cfg(cfg, "attractive_spread_pp")
    watch_at = _cfg(cfg, "watch_spread_pp")

    # Question 1 — is it a profitable business at all?
    if eps is not None and eps <= 0:
        reasons.append(
            f"The company is losing money: {basis} is "
            f"{eps:,.2f} per share.")
        changes.append("It would have to earn a profit before any valuation "
                       "test means anything.")
        return _verdict_out("AVOID", reasons, changes, ey_pct, spread)

    # Question 2 — is the business growing?
    shrinking = rev_growth is not None and rev_growth < _cfg(cfg, "min_revenue_growth_pct")
    if shrinking:
        reasons.append(f"Revenue is shrinking: {rev_growth:+.1f}% against the "
                       f"same twelve months a year earlier.")

    cutting = rev_trend is not None and rev_trend < _cfg(cfg, "estimate_cut_pct")
    if cutting:
        # Say what this number IS. The free provider publishes how many
        # analysts moved which way, not a history of the estimate itself, so
        # calling it "estimates are down 6%" would be describing a quantity
        # nobody measured.
        reasons.append(
            f"Analysts are cutting: over the last 30 days "
            f"{abs(rev_trend):.0f}% more of the covering analysts lowered "
            f"this year's earnings estimate than raised it.")

    if shrinking and cutting:
        changes.append("Revenue would have to stop shrinking and estimates "
                       "would have to stop falling.")
        return _verdict_out("AVOID", reasons, changes, ey_pct, spread)

    if fcfy is not None and fcfy < _cfg(cfg, "min_fcf_yield_pct"):
        reasons.append(f"Free cash flow is negative: a {fcfy:.1f}% free cash "
                       f"flow yield means the business consumed cash over the "
                       f"last twelve months rather than producing it.")

    # Question 3 — is it cheap against its own fundamentals?
    if spread is None:
        return _verdict_out("INSUFFICIENT DATA",
                            reasons + ["Earnings yield could not be computed."],
                            changes, ey_pct, spread)

    target_yield = (treasury + attractive_at) / 100.0
    price_at_target = safe_div(eps, target_yield)
    eps_at_target = price * target_yield

    reasons.append(
        f"At ${price:,.2f} a share the {basis} of ${eps:,.2f} is an earnings "
        f"yield of {ey_pct:.1f}%, against a 10-year Treasury yield of "
        f"{treasury:.2f}%{treasury_note}. That is "
        f"{spread:+.1f} percentage points of compensation for owning a "
        f"business instead of a government bond.")

    if shrinking or cutting or (fcfy is not None and fcfy < 0):
        state = "WATCH" if spread >= attractive_at else "WAIT"
        changes.append(
            f"The valuation is {'already' if spread >= attractive_at else 'not'} "
            f"in range; the business itself is what needs to change.")
        if spread < attractive_at and price_at_target:
            changes.append(_price_line(price_at_target, eps_at_target,
                                       attractive_at, treasury, basis))
        return _verdict_out(state, reasons, changes, ey_pct, spread)

    if spread >= attractive_at:
        return _verdict_out("ATTRACTIVE", reasons, changes, ey_pct, spread)
    if spread >= watch_at:
        changes.append(_price_line(price_at_target, eps_at_target,
                                   attractive_at, treasury, basis))
        return _verdict_out("WATCH", reasons, changes, ey_pct, spread)
    changes.append(_price_line(price_at_target, eps_at_target,
                               attractive_at, treasury, basis))
    return _verdict_out("WAIT", reasons, changes, ey_pct, spread)


def _price_line(price_at_target, eps_at_target, attractive_at, treasury, basis):
    return (f"Reconsider below ${price_at_target:,.2f} a share, or if the "
            f"{basis} rises to ${eps_at_target:,.2f}. Either one puts the "
            f"earnings yield at {treasury + attractive_at:.1f}% — the "
            f"10-year Treasury yield plus the {attractive_at:.1f} point "
            f"cushion this dashboard asks for.")


def _verdict_out(label, reasons, changes, ey_pct, spread):
    return {"verdict": label, "reasons": reasons, "what_would_change": changes,
            "earnings_yield_pct": ey_pct, "spread_pp": spread,
            "engine": ENGINE_VERSION}
