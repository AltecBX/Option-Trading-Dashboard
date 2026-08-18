"""bank_model.py — what a bank is worth, on a bank's own terms (Phase 4).

Everything the generic model measures is meaningless for a bank. Borrowing
is the raw material rather than the risk, so net debt says nothing; deposits
are a liability that costs less than nothing in a good year; and free cash
flow moves with the loan book rather than with profit. Phase 2 recognised
that and put "SPECIALIZED MODEL REQUIRED" on the screen. This module is the
specialized model.

WHAT A BANK IS WORTH

A bank's balance sheet is mostly financial claims carried near their value,
so what it OWNS is knowable in a way a factory's assets are not. That makes
book value the natural anchor, and tangible book — book value with goodwill
and other intangibles taken out — the conservative one, because goodwill
paid for an acquisition cannot absorb a loan loss.

What a bank is worth ABOVE that book depends on how much it earns on it.
A bank compounding twenty percent on tangible common equity is worth a
large premium to that equity; one earning six percent, below what its
shareholders could get elsewhere, is worth a discount. So the cheapest
price-to-tangible-book in a peer group is not the cheapest bank — it is
usually the least profitable one. Two of the five valuation methods here
account for that directly: the peer method prices the bank off a fitted
relationship between profitability and multiple where one holds across the
group, and one method is nothing but that relationship written out:

    Justified price to tangible book = (ROTCE − g) ÷ (Cost of equity − g)

which is the standard dividend-discount result written in return-on-equity
terms. Its inputs are all shown, and its cost of equity is the same
market-wide figure the Phase 3 reverse discounted cash flow uses — the ten
year Treasury yield plus a stated equity risk premium — never a per-company
number manufactured to make the answer come out.

WHAT IS REFUSED

Net interest MARGIN is a ratio of net interest income to average
INTEREST-EARNING assets. Not one of the fifteen banks measured tags either
that ratio or the earning-asset base in machine-readable form, so the ratio
reported here is net interest income over average TOTAL assets, under that
name, and never called net interest margin.

Bank of America tags no preferred-equity concept at all. Its tangible book
value is therefore refused rather than computed as though its preferred
stock were zero, which would overstate the common equity behind every share.

Nothing here is scored, summed or ranked into a single number.
"""
from __future__ import annotations

import math

import fair_value as fv

BANK_MODEL_VERSION = "invest-bank-1.0.0"

BUSINESS_TYPE = "BANK"

DEFAULTS = {
    # Cost of equity = ten-year Treasury + this premium. The same stated
    # convention the Phase 3 reverse discounted cash flow uses, so the two
    # screens never disagree about what a shareholder's alternative is.
    "bank_equity_risk_premium_pct": 5.0,
    # Long-run growth in the justified multiple. Bounded well below the cost
    # of equity: at g → cost of equity the formula divides by nothing and
    # returns any answer you like.
    "bank_terminal_growth_pct": 3.0,
    # The justified multiple is a formula, not a market observation, so its
    # range comes from moving the return on equity rather than from a
    # distribution of prices.
    "bank_justified_rotce_haircut_pct": 3.0,
    "bank_justified_rotce_uplift_pct": 2.0,
    # A fitted peer relationship needs enough banks to be a relationship.
    "bank_min_peers_for_regression": 8,
    # Below this the fitted line is treated as no relationship at all.
    "bank_min_regression_r2": 0.20,
    # Credit and funding trends compare the latest twelve months with the
    # twelve before them.
    "bank_trend_min_periods": 8,
}

# How many quarters back the "a year ago" balance-sheet reading is taken
# from, for the averages that return-on-equity ratios need.
_YEAR_DAYS = 366
_YEAR_TOLERANCE_DAYS = 120


def cfg_get(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _na(reason: str, **kw) -> dict:
    return {"value": None, "reason": reason, **kw}


def _ok(value, basis: str, **kw) -> dict:
    return {"value": value, "reason": "", "basis": basis, **kw}


# ── the balance sheet ───────────────────────────────────────────────────────

def tangible_common_equity(fund, facts, as_of: str | None = None) -> dict:
    """Common equity with goodwill and other intangibles removed.

    Every component is required. A bank that does not tag its preferred
    stock has an unknown amount of its equity belonging to somebody other
    than the common shareholder, and there is no honest way to guess it.
    """
    eq = fund.instant(facts, "equity", as_of)
    if eq.get("value") is None:
        return _na("Shareholders' equity is not on file for this bank.")
    gw = fund.instant(facts, "goodwill", as_of)
    ia = fund.instant(facts, "intangible_assets", as_of)
    pref = fund.instant(facts, "preferred_equity", as_of)
    if gw.get("value") is None:
        return _na("Goodwill is not tagged in this bank's filings, so "
                   "tangible book value cannot be separated from book value.")
    if pref.get("value") is None:
        return _na("This bank does not tag its preferred stock in a "
                   "machine-readable form. Treating it as zero would credit "
                   "the common shareholder with equity that belongs to the "
                   "preferred holders, so tangible book value is left "
                   "unreported rather than overstated.")
    intangibles = _num(ia.get("value")) or 0.0
    tce = (_num(eq["value"]) - _num(gw["value"]) - intangibles
           - _num(pref["value"]))
    common = _num(eq["value"]) - _num(pref["value"])
    return _ok(tce,
               "Shareholders' equity less preferred stock, goodwill and "
               "other intangible assets, at the latest reported "
               "balance-sheet date",
               common_equity=common,
               as_of=eq.get("as_of"), filed=eq.get("filed"),
               components={"equity": eq.get("value"),
                           "preferred": pref.get("value"),
                           "goodwill": gw.get("value"),
                           "intangibles": ia.get("value"),
                           "intangibles_reason": ia.get("reason") or ""},
               source="SEC EDGAR Company Facts (XBRL)")


def _year_ago(rows, as_of: str | None = None):
    """The balance-sheet reading closest to a year before the latest one."""
    pts = [r for r in (rows or []) if r.get("val") is not None
           and (as_of is None or r["end"] <= as_of)]
    if len(pts) < 2:
        return None
    from datetime import date as _date

    def _d(s):
        try:
            return _date.fromisoformat(str(s)[:10])
        except ValueError:
            return None

    last = _d(pts[-1]["end"])
    if last is None:
        return None
    best, best_gap = None, None
    for r in pts[:-1]:
        d = _d(r["end"])
        if d is None:
            continue
        gap = abs((last - d).days - _YEAR_DAYS)
        if gap <= _YEAR_TOLERANCE_DAYS and (best_gap is None or gap < best_gap):
            best, best_gap = r, gap
    return best


def average_balance(fund, facts, name: str, as_of: str | None = None) -> dict:
    """The mean of the latest balance-sheet reading and the one a year before.

    A return on equity divides a YEAR of profit by equity. Dividing by the
    closing balance alone flatters a bank that shrank and punishes one that
    grew, so both ends of the year are used where both exist. Where the
    year-ago reading is missing the closing balance is used and said so.
    """
    gaap = facts.get("facts", {}).get("us-gaap") or {}
    for concept in fund.INSTANT_CONCEPTS[name]:
        entry = gaap.get(concept)
        if not entry:
            continue
        rows = fund.instant_series(entry)
        rows = [r for r in rows if as_of is None or r["end"] <= as_of]
        if not rows:
            continue
        latest = _num(rows[-1]["val"])
        prior = _year_ago(rows, as_of)
        if prior is not None:
            return _ok((latest + _num(prior["val"])) / 2.0,
                       f"Average of the {rows[-1]['end']} and "
                       f"{prior['end']} balance-sheet readings",
                       latest=latest, prior=_num(prior["val"]),
                       concept=concept)
        return _ok(latest,
                   f"The {rows[-1]['end']} balance-sheet reading. No reading "
                   f"about a year earlier is on file, so this is a closing "
                   f"balance rather than an average.",
                   latest=latest, prior=None, concept=concept)
    return _na("Not reported in a machine-readable form.")


# ── profitability ───────────────────────────────────────────────────────────

def return_on(numerator, denominator) -> float | None:
    n, d = _num(numerator), _num(denominator)
    if n is None or d is None or d <= 0:
        return None
    return n / d * 100.0


def justified_multiple(rotce_pct, cost_of_equity_pct, growth_pct) -> dict:
    """(ROTCE − g) ÷ (cost of equity − g).

    The dividend-discount model written in return-on-equity terms. It says
    the obvious thing precisely: a bank earning exactly its cost of equity
    is worth exactly its tangible book, one earning more is worth a premium,
    and one earning less is worth a discount.
    """
    r = _num(rotce_pct)
    ke = _num(cost_of_equity_pct)
    g = _num(growth_pct)
    if r is None or ke is None or g is None:
        return _na("Return on tangible common equity, the cost of equity or "
                   "the growth assumption is missing.")
    if ke - g < 1.0:
        return _na(f"The cost of equity ({ke:.1f}%) is not far enough above "
                   f"the growth assumption ({g:.1f}%) for this formula to "
                   f"mean anything — as the two converge it will return any "
                   f"answer at all.")
    m = (r - g) / (ke - g)
    if m <= 0:
        return _na(f"This bank earns {r:.1f}% on its tangible common equity, "
                   f"below the {g:.1f}% growth assumed for it. The formula "
                   f"returns a multiple at or below zero, which is a "
                   f"statement that the model does not fit rather than a "
                   f"valuation.")
    return _ok(m, f"(ROTCE {r:.1f}% − growth {g:.1f}%) ÷ (cost of equity "
                  f"{ke:.1f}% − growth {g:.1f}%)",
               rotce_pct=r, cost_of_equity_pct=ke, growth_pct=g)


# ── the peer relationship between profitability and price ───────────────────

def regress(xs, ys) -> dict | None:
    """Least-squares line through (x, y), with its coefficient of
    determination. Returns None when the inputs cannot support a line."""
    pts = [(a, b) for a, b in zip(xs or [], ys or [])
           if _num(a) is not None and _num(b) is not None]
    n = len(pts)
    if n < 3:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return None
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((p[1] - my) ** 2 for p in pts)
    r2 = (sxy ** 2) / (sxx * syy) if syy > 0 else 0.0
    return {"slope": slope, "intercept": intercept, "r2": r2, "n": n}


def peer_fitted_multiple(subject_rotce_pct, peer_rotce_pcts,
                         peer_multiples, cfg=None) -> dict:
    """What this bank's peers charge for a bank of THIS profitability.

    Where a real relationship exists between return on tangible common
    equity and price to tangible book across the group, the subject is
    priced off that line rather than off the group median — which is the
    whole point, because the median prices it as though every bank in the
    group earned the same return.

    Where the relationship is weak or the group is small, the median is used
    and the reason is stated. A fitted line through six points is not a
    relationship.
    """
    cfg = cfg or {}
    r = _num(subject_rotce_pct)
    pairs = [(a, b) for a, b in zip(peer_rotce_pcts or [], peer_multiples or [])
             if _num(a) is not None and _num(b) is not None and _num(b) > 0]
    need = int(cfg_get(cfg, "bank_min_peers_for_regression"))
    min_r2 = float(cfg_get(cfg, "bank_min_regression_r2"))
    median = fv.quantile([p[1] for p in pairs], 0.5) if pairs else None
    out = {"value": median, "fitted": False, "n": len(pairs),
           "median": median, "reason": "", "r2": None, "slope": None}
    if r is None:
        out["reason"] = ("This bank's own return on tangible common equity "
                         "could not be measured, so its peers' median "
                         "multiple is used unadjusted.")
        return out
    if len(pairs) < need:
        out["reason"] = (f"Only {len(pairs)} comparable banks report both a "
                         f"return on tangible common equity and a price to "
                         f"tangible book — fewer than the {need} needed "
                         f"before a fitted relationship means anything, so "
                         f"the group median is used.")
        return out
    fit = regress([p[0] for p in pairs], [p[1] for p in pairs])
    if fit is None or fit["r2"] < min_r2 or fit["slope"] <= 0:
        out["reason"] = (
            f"Across these {len(pairs)} banks, profitability explains "
            f"{(fit or {}).get('r2', 0.0) * 100:.0f}% of the difference in "
            f"price to tangible book — below the "
            f"{min_r2 * 100:.0f}% needed to price off the relationship, so "
            f"the group median is used instead.")
        out["r2"] = (fit or {}).get("r2")
        out["slope"] = (fit or {}).get("slope")
        return out
    val = fit["intercept"] + fit["slope"] * r
    if val <= 0:
        out["reason"] = ("The fitted relationship puts this bank's multiple "
                         "at or below zero, so the group median is used.")
        out["r2"], out["slope"] = fit["r2"], fit["slope"]
        return out
    out.update({"value": val, "fitted": True, "r2": fit["r2"],
                "slope": fit["slope"], "intercept": fit["intercept"],
                "reason": (f"Across {len(pairs)} comparable banks, every "
                           f"extra point of return on tangible common equity "
                           f"is worth {fit['slope']:.2f} of price to tangible "
                           f"book, and that relationship explains "
                           f"{fit['r2'] * 100:.0f}% of the spread between "
                           f"them. This bank is priced off that line at its "
                           f"own {r:.1f}% return, not off the group median "
                           f"of {median:.2f}.")})
    return out


# ── point-in-time per-share series, for the valuation history ───────────────

def point_in_time_series(fund, facts) -> dict:
    """Per-share book and tangible book, dated at the filing that first
    stated each of them.

    The same discipline the Phase 2 valuation history runs on: a value
    becomes effective on the day it was first filed, never on the day the
    period ended. Dividing a later price by an earlier book is the whole
    point; dividing it by a book nobody had yet is lookahead.
    """
    gaap = facts.get("facts", {}).get("us-gaap") or {}

    def series(name):
        for concept in fund.INSTANT_CONCEPTS[name]:
            entry = gaap.get(concept)
            if entry:
                rows = fund.instant_series(entry)
                if rows:
                    return rows
        return []

    eq = series("equity")
    if not eq:
        return {}
    gw = {r["end"]: r["val"] for r in series("goodwill")}
    ia = {r["end"]: r["val"] for r in series("intangible_assets")}
    pref = {r["end"]: r["val"] for r in series("preferred_equity")}
    shares = {p["period_end"]: p["value"]
              for p in fund.pit_series(facts, "diluted_shares")
              if p.get("value")}

    book, tangible = [], []
    for r in eq:
        end = r["end"]
        sh = shares.get(end)
        p = pref.get(end)
        if not sh or p is None:
            continue
        common = _num(r["val"]) - _num(p)
        stamp = {"period_end": end, "first_filed": r.get("first_filed"),
                 "filed": r.get("filed")}
        book.append({**stamp, "value": common / sh})
        g = gw.get(end)
        if g is not None:
            tce = common - _num(g) - (_num(ia.get(end)) or 0.0)
            tangible.append({**stamp, "value": tce / sh})
    return {"book_per_share": book, "tangible_book_per_share": tangible}


# ── the whole picture ───────────────────────────────────────────────────────

def metrics(fund, facts, price=None, shares_outstanding=None,
            as_of: str | None = None, cfg=None) -> dict:
    """Every bank measure this app can honestly report, each with its basis
    or each explaining its own absence."""
    cfg = cfg or {}
    out = {"available": False, "reason": "", "version": BANK_MODEL_VERSION}
    price = _num(price)
    shares = _num(shares_outstanding)

    tce = tangible_common_equity(fund, facts, as_of)
    out["tangible_common_equity"] = tce
    common_equity = tce.get("common_equity")
    if common_equity is None:
        eq = fund.instant(facts, "equity", as_of)
        pref = fund.instant(facts, "preferred_equity", as_of)
        if eq.get("value") is not None and pref.get("value") is not None:
            common_equity = _num(eq["value"]) - _num(pref["value"])
    out["common_equity"] = common_equity

    ni = fund.metric(facts, "net_income_common", as_of)
    if ni.get("value") is None:
        ni = fund.metric(facts, "net_income", as_of)
    out["net_income_common"] = ni

    avg_eq = average_balance(fund, facts, "equity", as_of)
    avg_assets = average_balance(fund, facts, "assets", as_of)
    avg_deposits = average_balance(fund, facts, "deposits", as_of)
    avg_loans = average_balance(fund, facts, "loans", as_of)
    out["average_equity"] = avg_eq
    out["average_assets"] = avg_assets

    # Book and tangible book per share. The share count is the same one the
    # market value uses, so price ÷ book per share and market value ÷ book
    # can never disagree with each other.
    out["book_per_share"] = (
        _ok(common_equity / shares,
            "Shareholders' equity less preferred stock, divided by shares "
            "outstanding")
        if (common_equity is not None and shares) else
        _na("Common equity or the share count is not available."))
    out["tangible_book_per_share"] = (
        _ok(tce["value"] / shares, tce.get("basis", "") + ", per share")
        if (tce.get("value") is not None and shares) else
        _na(tce.get("reason") or "The share count is not available."))

    pb = out["book_per_share"]["value"]
    ptb = out["tangible_book_per_share"]["value"]
    out["price_to_book"] = (
        _ok(price / pb, "Share price divided by book value per share")
        if (price and pb and pb > 0) else
        _na(out["book_per_share"].get("reason")
            or "Book value per share is not positive."))
    out["price_to_tangible_book"] = (
        _ok(price / ptb,
            "Share price divided by tangible book value per share")
        if (price and ptb and ptb > 0) else
        _na(out["tangible_book_per_share"].get("reason")
            or "Tangible book value per share is not positive."))

    # Returns. ROTCE uses AVERAGE tangible common equity where both ends of
    # the year exist; where only the closing balance does, that is said.
    out["return_on_equity_pct"] = _pack(
        return_on(ni.get("value"), avg_eq.get("value")),
        f"Net income to common over {avg_eq.get('basis', '').lower()}"
        if avg_eq.get("value") else "",
        avg_eq.get("reason") or "Net income to common is not available.")
    avg_tce = None
    if (avg_eq.get("value") is not None and tce.get("value") is not None
            and common_equity):
        # The intangible and preferred deductions are carried at the latest
        # date; applying the same ratio to the average equity keeps the
        # numerator and denominator on one basis without pretending a second
        # balance sheet was read.
        avg_tce = avg_eq["value"] * (tce["value"] / common_equity)
    out["return_on_tangible_common_equity_pct"] = _pack(
        return_on(ni.get("value"), avg_tce),
        "Net income to common over average tangible common equity, with the "
        "intangible and preferred deductions carried at their latest "
        "reported share of equity",
        tce.get("reason") or "Average equity is not available.")

    # Net interest income and its trend.
    nii = fund.metric(facts, "net_interest_income", as_of)
    out["net_interest_income"] = nii
    out["net_interest_income_growth_pct"] = _growth_block(
        fund, facts, "net_interest_income", as_of)
    out["net_interest_income_to_average_assets_pct"] = _pack(
        return_on(nii.get("value"), avg_assets.get("value")),
        "Net interest income over average total assets. This is NOT net "
        "interest margin: that ratio uses average interest-EARNING assets, "
        "and no bank measured tags either the ratio or the earning-asset "
        "base in machine-readable form, so it is reported here under the "
        "name of what it actually is.",
        nii.get("reason") or avg_assets.get("reason")
        or "Not available.")

    # Efficiency: what it costs to produce a dollar of revenue.
    nonint_inc = fund.metric(facts, "noninterest_income", as_of)
    nonint_exp = fund.metric(facts, "noninterest_expense", as_of)
    out["noninterest_income"] = nonint_inc
    out["noninterest_expense"] = nonint_exp
    rev = None
    if nii.get("value") is not None and nonint_inc.get("value") is not None:
        rev = _num(nii["value"]) + _num(nonint_inc["value"])
    out["revenue_ttm"] = rev
    out["efficiency_ratio_pct"] = _pack(
        return_on(nonint_exp.get("value"), rev),
        "Non-interest expense over net interest income plus fee income. "
        "Lower is better: it is the share of revenue spent running the bank.",
        nonint_exp.get("reason") or nonint_inc.get("reason")
        or "Not available.")

    # Funding.
    dep = fund.instant(facts, "deposits", as_of)
    out["deposits"] = dep
    out["deposit_growth_pct"] = _instant_growth(fund, facts, "deposits", as_of)
    dep_int = fund.metric(facts, "interest_expense_deposits", as_of)
    out["deposit_cost_pct"] = _pack(
        return_on(dep_int.get("value"), avg_deposits.get("value")),
        "Interest paid on deposits over average deposits",
        dep_int.get("reason") or avg_deposits.get("reason")
        or "Not available.")

    # Credit.
    out["loans"] = fund.instant(facts, "loans", as_of)
    out["loan_growth_pct"] = _instant_growth(fund, facts, "loans", as_of)
    nco = fund.metric(facts, "net_charge_offs", as_of)
    out["net_charge_offs"] = nco
    out["charge_off_rate_pct"] = _pack(
        return_on(nco.get("value"), avg_loans.get("value")),
        "Loans written off over the last twelve months, over average loans",
        nco.get("reason") or avg_loans.get("reason") or "Not available.")
    out["charge_off_trend"] = _trend(fund, facts, "net_charge_offs", as_of)
    npl = fund.instant(facts, "nonaccrual_loans", as_of)
    out["nonaccrual_loans"] = npl
    loans_v = (out["loans"] or {}).get("value")
    out["nonperforming_rate_pct"] = _pack(
        return_on(npl.get("value"), loans_v),
        "Loans on non-accrual status over loans outstanding",
        npl.get("reason") or "Not available.")

    # Capital.
    out["capital_ratio"] = fund.instant_ratio(facts, "capital_ratio", as_of)

    # Share count trend, from the same diluted series the rest of the app uses.
    out["diluted_share_trend_pct"] = _share_trend(fund, facts, as_of)

    out["available"] = any(
        (out.get(k) or {}).get("value") is not None
        for k in ("price_to_tangible_book", "price_to_book"))
    if not out["available"]:
        out["reason"] = (out["tangible_book_per_share"].get("reason")
                         or "This bank's book value could not be measured "
                            "from its filings.")
    return out


def _pack(value, basis, reason) -> dict:
    return ({"value": value, "basis": basis, "reason": ""} if value is not None
            else {"value": None, "basis": "", "reason": reason})


def _growth_block(fund, facts, name: str, as_of=None) -> dict:
    """Growth in a trailing-twelve-month figure against the year before it."""
    pts = [p for p in fund.ttm_series(facts, name)
           if as_of is None or (p.get("period_end") or "") <= as_of]
    if len(pts) < 5:
        return _na(f"Only {len(pts)} trailing-twelve-month readings are on "
                   f"file — a year-over-year comparison needs five.")
    now, prior = _num(pts[-1]["value"]), _num(pts[-5]["value"])
    if now is None or prior is None or prior <= 0:
        return _na("The year-earlier figure is not positive, so a percentage "
                   "change would not mean anything.")
    return _ok((now / prior - 1.0) * 100.0,
               f"{pts[-1]['period_end']} against {pts[-5]['period_end']}, "
               f"both trailing twelve months")


def _instant_growth(fund, facts, name: str, as_of=None) -> dict:
    """Growth in a balance-sheet figure against its reading a year earlier."""
    gaap = facts.get("facts", {}).get("us-gaap") or {}
    for concept in fund.INSTANT_CONCEPTS[name]:
        entry = gaap.get(concept)
        if not entry:
            continue
        rows = [r for r in fund.instant_series(entry)
                if as_of is None or r["end"] <= as_of]
        if len(rows) < 2:
            continue
        prior = _year_ago(rows, as_of)
        if prior is None or not _num(prior["val"]):
            continue
        return _ok((_num(rows[-1]["val"]) / _num(prior["val"]) - 1.0) * 100.0,
                   f"{rows[-1]['end']} against {prior['end']}")
    return _na("No balance-sheet reading about a year earlier is on file to "
               "compare against.")


def _trend(fund, facts, name: str, as_of=None) -> dict:
    """RISING / FALLING / STEADY over the last year against the one before.

    Deliberately three words and a pair of numbers rather than a score. The
    direction of a credit line is the thing worth knowing; a decimal place
    on it is not.
    """
    pts = [p for p in fund.ttm_series(facts, name)
           if as_of is None or (p.get("period_end") or "") <= as_of]
    if len(pts) < 5:
        return {"state": None, "reason": (f"Only {len(pts)} trailing-twelve-"
                                          f"month readings are on file.")}
    now, prior = _num(pts[-1]["value"]), _num(pts[-5]["value"])
    if now is None or prior is None or prior == 0:
        return {"state": None, "reason": "The year-earlier figure is zero."}
    change = (now / prior - 1.0) * 100.0
    state = ("RISING" if change > 15.0 else
             "FALLING" if change < -15.0 else "STEADY")
    return {"state": state, "change_pct": change, "now": now, "prior": prior,
            "reason": "",
            "basis": (f"The twelve months to {pts[-1]['period_end']} against "
                      f"the twelve months to {pts[-5]['period_end']}. Moves "
                      f"inside fifteen percent are called steady.")}


def _share_trend(fund, facts, as_of=None) -> dict:
    pts = [p for p in fund.pit_series(facts, "diluted_shares")
           if as_of is None or (p.get("period_end") or "") <= as_of]
    if len(pts) < 5:
        return _na("Not enough reported share counts to show a trend.")
    now, prior = _num(pts[-1]["value"]), _num(pts[-5]["value"])
    if not now or not prior:
        return _na("A reported share count is missing.")
    return _ok((now / prior - 1.0) * 100.0,
               f"Diluted shares at {pts[-1]['period_end']} against "
               f"{pts[-5]['period_end']}. Negative means the count is "
               f"shrinking.")


# ── the valuation methods ───────────────────────────────────────────────────

def cost_of_equity(ten_year_pct, cfg=None) -> dict:
    """Ten-year Treasury plus a stated equity risk premium."""
    cfg = cfg or {}
    t = _num(ten_year_pct)
    prem = float(cfg_get(cfg, "bank_equity_risk_premium_pct"))
    if t is None:
        return _na("The ten-year Treasury yield is not available.")
    return _ok(t + prem,
               f"The {t:.2f}% ten-year Treasury yield plus a stated "
               f"{prem:.1f}% equity risk premium. This is a market-wide "
               f"convention, adjustable in settings, not a figure fitted to "
               f"this bank.")


def methods(bank: dict, history: dict, peers: dict | None = None,
            ten_year_pct=None, cfg=None) -> list:
    """The bank fair-value methods, each built or each refusing.

    `history` carries the distributions of this bank's own price to tangible
    book and price to book, from the same point-in-time engine every other
    valuation history in the app uses.
    """
    cfg = cfg or {}
    peers = peers or {}
    out = []
    raw = (history or {}).get("raw_values") or {}

    tbvps = (bank.get("tangible_book_per_share") or {}).get("value")
    bvps = (bank.get("book_per_share") or {}).get("value")

    out.append(fv.method_multiple_history(
        "bank_self_ptbv", "Its own price to tangible book",
        "Tangible book value per share, priced at this bank's own "
        "point-in-time history of price to tangible book",
        tbvps, (raw.get("price_to_tangible_book") or {}).get("5y")
        or (raw.get("price_to_tangible_book") or {}).get("all"),
        cfg=cfg, rank=3.4, what="tangible book value per share",
        detail={"measure": "price_to_tangible_book"}))

    out.append(fv.method_multiple_history(
        "bank_self_pb", "Its own price to book",
        "Book value per share, priced at this bank's own point-in-time "
        "history of price to book",
        bvps, (raw.get("price_to_book") or {}).get("5y")
        or (raw.get("price_to_book") or {}).get("all"),
        cfg=cfg, rank=3.0, what="book value per share",
        detail={"measure": "price_to_book"}))

    # Its own earnings history — the Phase 3 method, unchanged. A bank's
    # earnings are real earnings; it is the balance sheet that needed a
    # different treatment.
    out.append(fv.method_self_history(
        (bank.get("eps_ttm")),
        ((raw.get("earnings_yield_pct") or {}).get("5y")
         or (raw.get("earnings_yield_pct") or {}).get("all")),
        cfg=cfg,
        regime_shifted=bool(((history or {}).get("regime") or {}).get("shifted")),
        window_label=(history or {}).get("window_label") or "5-year"))

    # Peers, priced off the profitability relationship where one exists.
    fitted = peer_fitted_multiple(
        (bank.get("return_on_tangible_common_equity_pct") or {}).get("value"),
        peers.get("rotce_pcts") or [], peers.get("ptbv_multiples") or [],
        cfg=cfg)
    out.append(fv.method_peer_multiple(
        "bank_peers_ptbv", "Comparable banks",
        ("Tangible book value per share at what comparable banks cost per "
         "unit of their own tangible book" +
         (", adjusted for how profitable this bank is against them"
          if fitted.get("fitted") else "")),
        tbvps, peers.get("ptbv_multiples") or [],
        base_multiple=fitted.get("value"), level=peers.get("level"),
        cfg=cfg, rank=2.2,
        detail={"fitted": fitted.get("fitted"), "r2": fitted.get("r2"),
                "slope": fitted.get("slope"),
                "median_multiple": fitted.get("median"),
                "note": fitted.get("reason")}))

    # The profitability-justified multiple.
    ke = cost_of_equity(ten_year_pct, cfg)
    rotce = (bank.get("return_on_tangible_common_equity_pct") or {}).get("value")
    g = float(cfg_get(cfg, "bank_terminal_growth_pct"))
    cut = float(cfg_get(cfg, "bank_justified_rotce_haircut_pct"))
    up = float(cfg_get(cfg, "bank_justified_rotce_uplift_pct"))
    basis = ("Tangible book value per share at the multiple its own "
             "profitability justifies: (return on tangible common equity − "
             "growth) ÷ (cost of equity − growth)")
    if tbvps is None or tbvps <= 0:
        out.append(fv._method("bank_justified", "What its profitability "
                              "justifies", basis,
                              reason=(bank.get("tangible_book_per_share") or {})
                              .get("reason") or "Tangible book value per "
                                                "share is not available."))
    elif ke.get("value") is None:
        out.append(fv._method("bank_justified", "What its profitability "
                              "justifies", basis, reason=ke["reason"]))
    else:
        mid = justified_multiple(rotce, ke["value"], g)
        lo = justified_multiple(None if rotce is None else rotce - cut,
                                ke["value"], g)
        hi = justified_multiple(None if rotce is None else rotce + up,
                                ke["value"], g)
        if mid.get("value") is None:
            out.append(fv._method("bank_justified",
                                  "What its profitability justifies", basis,
                                  reason=mid["reason"]))
        else:
            out.append(fv._method(
                "bank_justified", "What its profitability justifies", basis,
                bear=(tbvps * lo["value"]) if lo.get("value") else None,
                base=tbvps * mid["value"],
                bull=(tbvps * hi["value"]) if hi.get("value") else None,
                n=1, rank=2.6,
                detail={"multiple_base": mid["value"],
                        "multiple_bear": lo.get("value"),
                        "multiple_bull": hi.get("value"),
                        "rotce_pct": rotce, "cost_of_equity_pct": ke["value"],
                        "growth_pct": g,
                        "note": (f"The pessimistic and optimistic ends move "
                                 f"the return on tangible common equity by "
                                 f"−{cut:.0f} and +{up:.0f} points rather "
                                 f"than by a distribution of prices, because "
                                 f"this method is a formula and not a market "
                                 f"observation.")}))

    return fv.stamp(out, BUSINESS_TYPE)


def peer_inputs(rows: list) -> dict:
    """Pull the price-to-tangible-book and profitability of each peer bank
    out of whatever the peer builder collected for them."""
    ptbv, rotce = [], []
    for r in rows or []:
        m = _num(r.get("price_to_tangible_book"))
        p = _num(r.get("return_on_tangible_common_equity_pct"))
        if m is not None and m > 0:
            ptbv.append(m)
            rotce.append(p)
    return {"ptbv_multiples": ptbv, "rotce_pcts": rotce, "n": len(ptbv)}
