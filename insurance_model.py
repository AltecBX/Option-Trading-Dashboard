"""insurance_model.py — what an insurer is worth, on its own terms (Phase 5).

An insurer collects money now for claims it will pay later, and the pile of
money in between — the float — is neither debt nor spare cash. A generic
leverage model reads it as borrowings and a generic cash-flow model reads it
as free cash. Both readings are wrong, which is why Phase 2 put "SPECIALIZED
MODEL REQUIRED" on the screen and why this module exists.

WHICH INSURER

The first question is not what an insurer is worth. It is what kind of
insurer it is, because the answer decides which numbers mean anything.

Divide claims by premiums for Progressive and you get 66%, which is its loss
ratio and means what it says. Do the same arithmetic for MetLife and you get
99%, for Principal Financial 129% and for Brighthouse 248%. Those are not
loss ratios. A life insurer's premiums exclude the fee and spread income
that is most of what it earns, and its benefits include interest credited to
policyholder accounts and the annual change in reserves held for policies
that will pay out decades from now. The numerator and the denominator are
describing different businesses.

So this module classifies first — property and casualty, life, health,
reinsurance or multiline — from the insurer's own annual report, checked
against its SEC industry code, and REFUSES when the two cannot agree.

Measured across forty-two US insurers, thirty-seven classify: fifteen
property-casualty, nine life, six health, five reinsurance and two
multiline. Four of the five refusals have no readable "Item 1. Business"
heading in their annual report at all — Cincinnati Financial, Equitable,
Berkshire Hathaway and Alleghany — and the fifth is American International
Group, whose report this reader cannot find one in either. A refusal costs a
screen. A wrong subtype puts a number on the screen that is arithmetic
rather than a measurement.

THE COMBINED RATIO, AND WHY IT IS USUALLY BLANK

The combined ratio is the loss ratio plus the expense ratio: below 100 the
insurer made money underwriting, above it the investment income has to cover
the difference. It is the single most useful number about a property-casualty
insurer, and it is mostly missing here.

The loss side is fine — every one of the thirty-six insurers measured tags
claims incurred and premiums earned. The expense side is not. Only five tag
`OtherUnderwritingExpense`, the concept that with acquisition-cost
amortisation makes up the GAAP underwriting expense. What the rest tag is a
scattering of `InsuranceCommissionsAndFees`, `OtherCostAndExpenseOperating`
and `GeneralAndAdministrativeExpense` — different scopes, and for some filers
those are revenue lines rather than costs.

There is a tempting shortcut: take total benefits, losses and expenses and
subtract the claims. Measured, it produces believable-looking numbers for
pure property-casualty insurers (Travelers 88.6, Chubb 85.3) and nonsense
everywhere else (Cigna 716, Equitable 1,134), because the total sweeps in
interest credited, annuity costs and, for the health insurers, the cost of
dispensing prescriptions. It is a ratio manufactured from unrelated concepts,
so it is not used. The combined ratio is reported where the actual
underwriting expense is filed and blank with its reason where it is not.

Everything ratioed against premiums is also checked for COMPATIBILITY: the
numerator and the denominator must cover the same twelve months. Allstate
tags a property-casualty premium series that stopped in 2018 next to an
all-claims series that runs to today, and dividing one by the other gives a
loss ratio of 123%. The alignment test catches it and reports nothing.

WHAT IT IS WORTH

Book value, and what the insurer earns on it. An insurer's assets are mostly
marked-to-market securities, so book value is closer to a real number here
than in almost any other industry, and the multiple of it that the insurer
deserves comes from its return on equity in the same formula the bank model
uses:

    Justified price to book = (ROE − g) ÷ (cost of equity − g)

Nothing here is scored, summed or ranked into a single number.
"""
from __future__ import annotations

import math

import bank_model as bm
import fair_value as fv

INSURANCE_MODEL_VERSION = "invest-insurance-1.0.0"

BUSINESS_TYPE = "INSURANCE"

# Which metric family each subtype supports, from fundamentals. Imported
# lazily through the caller's `fund` module so this file has no import cycle.
UNDERWRITING = "UNDERWRITING"      # loss ratio means what it says
BENEFIT = "BENEFIT"                # claims over premiums is the benefit ratio
SPREAD = "SPREAD"                  # neither; read on premiums, spread and book

DEFAULTS = {
    # A combined ratio above this, sustained, says the insurer is paying out
    # more than it takes in and relying on its investments to make up the
    # difference. Called out, never scored.
    "insurance_combined_ratio_alarm": 100.0,
    # Prior-year reserve development worse than this share of premiums earned
    # is called adverse. A small figure either way is ordinary re-estimation.
    "insurance_adverse_development_pct": 1.0,
    # The peer group must have this many insurers of the SAME subtype before
    # a matched comparison is used instead of all insurers.
    "insurance_min_subtype_peers": 5,
    # As in the bank model: enough peers, and enough explanatory power,
    # before profitability is priced off a fitted line rather than a median.
    "insurance_min_peers_for_regression": 8,
    "insurance_min_regression_r2": 0.20,
    # The justified multiple's range comes from moving the return on equity
    # rather than from a distribution of prices, because it is a formula.
    "insurance_justified_roe_haircut_pct": 3.0,
    "insurance_justified_roe_uplift_pct": 2.0,
}


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


# ── compatibility ───────────────────────────────────────────────────────────

def same_period(a: dict, b: dict) -> bool:
    """Do two trailing-twelve-month figures cover the same twelve months?

    The ratio of two numbers that describe different periods is not a ratio
    of anything. Allstate's premium series ends in 2018 and its claims series
    ends today; dividing them gives 123%, which looks like a catastrophic
    loss ratio and is a date mismatch.
    """
    if not a or not b:
        return False
    if a.get("value") is None or b.get("value") is None:
        return False
    return (a.get("period_end") and a.get("period_end") == b.get("period_end")
            and a.get("period_start") == b.get("period_start"))


def ratio_of(numerator: dict, denominator: dict, basis: str,
             mismatch_note: str = "") -> dict:
    """numerator ÷ denominator as a percentage, or the reason there is none."""
    if numerator.get("value") is None:
        return _na(numerator.get("reason") or "Not reported in a "
                                              "machine-readable form.")
    if denominator.get("value") is None:
        return _na(denominator.get("reason") or "Not reported in a "
                                                "machine-readable form.")
    if not same_period(numerator, denominator):
        return _na(
            f"The two figures this ratio needs do not cover the same twelve "
            f"months — one runs to {numerator.get('period_end')} and the "
            f"other to {denominator.get('period_end')}. Dividing them would "
            f"produce a number that looks like a ratio and is a date "
            f"mismatch. {mismatch_note}".strip())
    d = _num(denominator["value"])
    if not d or d <= 0:
        return _na("The denominator is not positive.")
    return _ok(_num(numerator["value"]) / d * 100.0, basis,
               period_end=numerator.get("period_end"))


# ── the whole picture ───────────────────────────────────────────────────────

def metrics(fund, facts, price=None, shares_outstanding=None,
            subtype: str | None = None, as_of: str | None = None,
            cfg=None) -> dict:
    """Every insurer measure this app can honestly report for THIS kind of
    insurer, each with its basis or each explaining its own absence."""
    cfg = cfg or {}
    basis_family = fund.INSURER_METRIC_BASIS.get(subtype or "")
    out = {"available": False, "reason": "",
           "version": INSURANCE_MODEL_VERSION,
           "subtype": subtype,
           "subtype_label": fund.INSURER_SUBTYPE_LABELS.get(subtype or "", ""),
           "metric_basis": basis_family or ""}
    if not subtype or not basis_family:
        out["reason"] = (
            "What kind of insurer this is could not be established from its "
            "annual report and its SEC industry code together. That matters "
            "more here than it looks: claims divided by premiums is a loss "
            "ratio for a property-casualty insurer and is not a ratio at all "
            "for a life insurer, whose premiums leave out most of what it "
            "earns and whose benefits include interest credited to "
            "policyholder accounts. Rather than apply one of those "
            "definitions and hope, nothing is measured.")
        return out

    price = _num(price)
    shares = _num(shares_outstanding)

    # ── book value ──────────────────────────────────────────────────────
    tce = bm.tangible_common_equity(fund, facts, as_of)
    out["tangible_common_equity"] = tce
    common_equity = tce.get("common_equity")
    if common_equity is None:
        eq = fund.instant(facts, "equity", as_of)
        pref = fund.preferred_equity(facts, as_of)
        if eq.get("value") is not None and pref.get("value") is not None:
            common_equity = _num(eq["value"]) - _num(pref["value"])
    out["common_equity"] = common_equity

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

    bvps = out["book_per_share"]["value"]
    tbvps = out["tangible_book_per_share"]["value"]
    out["price_to_book"] = (
        _ok(price / bvps, "Share price divided by book value per share")
        if (price and bvps and bvps > 0) else
        _na(out["book_per_share"].get("reason")
            or "Book value per share is not positive."))
    out["price_to_tangible_book"] = (
        _ok(price / tbvps,
            "Share price divided by tangible book value per share")
        if (price and tbvps and tbvps > 0) else
        _na(out["tangible_book_per_share"].get("reason")
            or "Tangible book value per share is not positive."))

    # ── returns ─────────────────────────────────────────────────────────
    ni = fund.net_income_to_common(facts, as_of)
    out["net_income_common"] = ni
    out["net_income_growth_pct"] = bm._growth_block(       # noqa: SLF001
        fund, facts, "net_income", as_of)

    avg_eq = bm.average_balance(fund, facts, "equity", as_of)
    avg_assets = bm.average_balance(fund, facts, "assets", as_of)
    out["average_equity"] = avg_eq
    out["return_on_equity_pct"] = _pack(
        bm.return_on(ni.get("value"), avg_eq.get("value")),
        f"Net income to common over {(avg_eq.get('basis') or '').lower()}",
        avg_eq.get("reason") or "Net income to common is not available.")

    avg_tce = None
    if (avg_eq.get("value") is not None and tce.get("value") is not None
            and common_equity):
        avg_tce = avg_eq["value"] * (tce["value"] / common_equity)
    out["return_on_tangible_common_equity_pct"] = _pack(
        bm.return_on(ni.get("value"), avg_tce),
        "Net income to common over average tangible common equity, with the "
        "intangible and preferred deductions carried at their latest "
        "reported share of equity. For an insurer carrying little goodwill "
        "this differs barely at all from the return on equity beside it.",
        tce.get("reason") or "Average equity is not available.")

    # ── premiums ────────────────────────────────────────────────────────
    prem = fund.metric(facts, "premiums_earned", as_of)
    out["premiums_earned"] = prem
    out["premium_growth_pct"] = bm._growth_block(          # noqa: SLF001
        fund, facts, "premiums_earned", as_of)
    out["premiums_written"] = fund.metric(facts, "premiums_written", as_of)

    # ── underwriting ────────────────────────────────────────────────────
    losses = fund.metric(facts, "losses_incurred", as_of)
    out["losses_incurred"] = losses
    out.update(_underwriting(fund, facts, prem, losses, basis_family,
                             subtype, as_of, cfg))

    # ── reserves ────────────────────────────────────────────────────────
    out.update(_reserves(fund, facts, prem, basis_family, as_of, cfg))

    # ── investments ─────────────────────────────────────────────────────
    nii = fund.metric(facts, "net_investment_income", as_of)
    out["net_investment_income"] = nii
    out["net_investment_income_growth_pct"] = bm._growth_block(  # noqa: SLF001
        fund, facts, "net_investment_income", as_of)
    avg_inv = bm.average_balance(fund, facts, "investments", as_of)
    out["investments"] = fund.instant(facts, "investments", as_of)
    out["investment_yield_pct"] = _pack(
        bm.return_on(nii.get("value"), avg_inv.get("value")),
        "Income earned on the investment portfolio over the average size of "
        "that portfolio",
        nii.get("reason") or avg_inv.get("reason") or "Not available.")

    # Life and health insurers carry a liability for benefits promised under
    # policies still in force. It is the thing their whole business turns on
    # and it is meaningless for a property-casualty insurer, so it is asked
    # for only where it belongs.
    if basis_family in (SPREAD, BENEFIT):
        out["future_policy_benefits"] = fund.instant(
            facts, "future_policy_benefits", as_of)
    else:
        out["future_policy_benefits"] = _na(
            "A property-casualty insurer holds reserves for claims already "
            "incurred rather than for benefits promised decades ahead, so "
            "this liability does not apply to it.")

    # ── capital ─────────────────────────────────────────────────────────
    # There is no insurer equivalent of a bank's regulatory capital ratio in
    # machine-readable filings: risk-based capital is filed with state
    # regulators, not with the SEC in XBRL. Equity against total assets is
    # what the filings actually support, and it is reported under that name.
    assets = fund.instant(facts, "assets", as_of)
    out["assets"] = assets
    # Total equity, minority interests included, because total ASSETS include
    # what those interests own. Book value per share above uses the parent's
    # own equity, because those are the shares being valued.
    tot_eq = fund.total_equity(facts, as_of)
    out["total_equity"] = tot_eq
    out["equity_to_assets_pct"] = (
        _ok(_num(tot_eq["value"]) / _num(assets["value"]) * 100.0,
            "All equity on the balance sheet as a share of total assets. "
            "This is NOT a risk-based capital ratio: insurers file those "
            "with their state regulators rather than with the SEC in "
            "machine-readable form, so what the filings support is reported "
            "under its own name. A life insurer's figure is far smaller than "
            "a property-casualty insurer's because its balance sheet carries "
            "policyholders' separate-account assets as well as its own.")
        if (_num(tot_eq.get("value")) and _num(assets.get("value"))) else
        _na(assets.get("reason") or tot_eq.get("reason")
            or "Total assets are not available."))
    out["equity_to_assets_trend"] = _capital_trend(fund, facts, as_of)

    out["diluted_share_trend_pct"] = bm._share_trend(fund, facts, as_of)  # noqa: SLF001
    out["book_value_per_share_trend_pct"] = _book_trend(fund, facts, as_of)

    out["available"] = any(
        (out.get(k) or {}).get("value") is not None
        for k in ("price_to_book", "price_to_tangible_book"))
    if not out["available"]:
        out["reason"] = (out["book_per_share"].get("reason")
                         or "This insurer's book value could not be measured "
                            "from its filings.")
    return out


def _pack(value, basis, reason) -> dict:
    return ({"value": value, "basis": basis, "reason": ""} if value is not None
            else {"value": None, "basis": "", "reason": reason})


def _underwriting(fund, facts, prem, losses, basis_family, subtype,
                  as_of, cfg) -> dict:
    """Loss, expense and combined ratios — where they mean something."""
    out: dict = {}
    if basis_family == SPREAD:
        why = (
            "A life insurer's premiums leave out the fee and spread income "
            "that is most of what it earns, and its benefits include "
            "interest credited to policyholder accounts and the change in "
            "reserves for policies that pay out decades from now. Dividing "
            "one by the other produces a number — 99% for MetLife, 129% for "
            "Principal Financial — that looks like a loss ratio and is not "
            "one. It is refused rather than shown.")
        for k in ("loss_ratio_pct", "expense_ratio_pct", "combined_ratio_pct",
                  "underwriting_profit", "acquisition_cost_ratio_pct"):
            out[k] = _na(why)
        out["combined_ratio_trend"] = {"state": None, "reason": why}
        return out

    loss_label = ("Benefit ratio — the share of premiums paid out as medical "
                  "and other benefits" if basis_family == BENEFIT else
                  "Loss ratio — claims and the cost of settling them, as a "
                  "share of premiums earned")
    out["loss_ratio_pct"] = ratio_of(losses, prem, loss_label)

    dac = fund.metric(facts, "policy_acquisition_amortization", as_of)
    ouw = fund.metric(facts, "other_underwriting_expense", as_of)
    out["acquisition_cost_ratio_pct"] = ratio_of(
        dac, prem,
        "Amortisation of the commissions and other costs of writing the "
        "business, as a share of premiums earned")

    if basis_family == BENEFIT:
        # A health insurer's operating costs are not tagged as underwriting
        # expense by any of the seven measured, so the combined ratio has no
        # honest second half. The benefit ratio above is the standard measure
        # for these insurers anyway.
        why = ("None of the health insurers measured tags an underwriting "
               "expense in machine-readable form, so there is no honest "
               "second half to add to the benefit ratio. The benefit ratio "
               "above is the measure this industry is actually judged on.")
        out["expense_ratio_pct"] = _na(why)
        out["combined_ratio_pct"] = _na(why)
        out["underwriting_profit"] = _na(why)
        out["combined_ratio_trend"] = {"state": None, "reason": why}
        return out

    missing = (
        "The combined ratio is the loss ratio plus the expense ratio, and "
        "this insurer does not tag the underwriting expense that the second "
        "half needs. Only five of the thirty-six insurers measured tag it. "
        "The obvious substitute — total benefits and expenses less claims — "
        "sweeps in interest credited, annuity costs and, for some filers, "
        "the cost of goods sold, so it is a ratio assembled out of unrelated "
        "concepts rather than a measurement. Nothing is shown instead.")
    if dac.get("value") is None or ouw.get("value") is None \
            or not same_period(dac, prem) or not same_period(ouw, prem):
        out["expense_ratio_pct"] = _na(missing)
        out["combined_ratio_pct"] = _na(missing)
        out["underwriting_profit"] = _na(missing)
        out["combined_ratio_trend"] = {"state": None, "reason": missing}
        return out

    exp_total = {"value": _num(dac["value"]) + _num(ouw["value"]),
                 "period_end": prem.get("period_end"),
                 "period_start": prem.get("period_start"), "reason": ""}
    out["expense_ratio_pct"] = ratio_of(
        exp_total, prem,
        "Expense ratio — acquisition-cost amortisation plus other "
        "underwriting expense, as a share of premiums earned")
    lr = out["loss_ratio_pct"].get("value")
    er = out["expense_ratio_pct"].get("value")
    if lr is None or er is None:
        out["combined_ratio_pct"] = _na(missing)
        out["underwriting_profit"] = _na(missing)
        out["combined_ratio_trend"] = {"state": None, "reason": missing}
        return out
    cr = lr + er
    alarm = float(cfg_get(cfg, "insurance_combined_ratio_alarm"))
    out["combined_ratio_pct"] = _ok(
        cr,
        f"Loss ratio {lr:.1f}% plus expense ratio {er:.1f}%. Below "
        f"{alarm:.0f}% the insurer made money on the underwriting itself; "
        f"above it, the investment income has to cover the difference.",
        loss_ratio_pct=lr, expense_ratio_pct=er,
        underwriting_profitable=cr < alarm)
    out["underwriting_profit"] = _ok(
        _num(prem["value"]) * (1.0 - cr / 100.0),
        f"Premiums earned less claims and underwriting expense, over the "
        f"twelve months to {prem.get('period_end')}")
    out["combined_ratio_trend"] = _combined_trend(fund, facts, as_of, cfg)
    return out


def _combined_trend(fund, facts, as_of, cfg) -> dict:
    """Is the combined ratio better or worse than a year ago?

    Rebuilt from the same three trailing-twelve-month series a year apart,
    so the two ends of the comparison are computed identically.
    """
    def window(name, idx):
        pts = [p for p in fund.ttm_series(facts, name)
               if as_of is None or (p.get("period_end") or "") <= as_of]
        return pts[idx] if len(pts) >= 5 else None

    now, prior = {}, {}
    for name in ("premiums_earned", "losses_incurred",
                 "policy_acquisition_amortization",
                 "other_underwriting_expense"):
        now[name] = window(name, -1)
        prior[name] = window(name, -5)
    if any(v is None for v in list(now.values()) + list(prior.values())):
        return {"state": None,
                "reason": ("A year-earlier combined ratio cannot be rebuilt "
                           "from this insurer's filings, so there is nothing "
                           "to compare today's against.")}

    def cr(block):
        p = _num(block["premiums_earned"]["value"])
        if not p or p <= 0:
            return None
        ends = {block[n]["period_end"] for n in block}
        if len(ends) != 1:
            return None
        return sum(_num(block[n]["value"]) or 0.0
                   for n in ("losses_incurred",
                             "policy_acquisition_amortization",
                             "other_underwriting_expense")) / p * 100.0

    a, b = cr(now), cr(prior)
    if a is None or b is None:
        return {"state": None,
                "reason": ("The two ends of the comparison do not cover the "
                           "same twelve months as each other.")}
    move = a - b
    # A combined ratio is a cost, so RISING is the bad direction.
    state = ("DETERIORATING" if move > 2.0 else
             "IMPROVING" if move < -2.0 else "STEADY")
    return {"state": state, "change_pp": move, "now": a, "prior": b,
            "reason": "",
            "basis": (f"The combined ratio for the twelve months to "
                      f"{now['premiums_earned']['period_end']} was {a:.1f}%, "
                      f"against {b:.1f}% for the twelve months to "
                      f"{prior['premiums_earned']['period_end']}. Moves "
                      f"inside two points are called steady.")}


def _reserves(fund, facts, prem, basis_family, as_of, cfg) -> dict:
    """Reserves held, and whether the ones set aside in earlier years turned
    out to be enough."""
    out: dict = {}
    out["reserves"] = fund.instant(facts, "insurance_reserves", as_of)
    res_v = _num((out["reserves"] or {}).get("value"))
    prem_v = _num(prem.get("value"))
    out["reserves_to_premiums"] = (
        _ok(res_v / prem_v,
            "Reserves held for claims already incurred, as a multiple of a "
            "year's premiums. A long-tail insurer — liability, workers' "
            "compensation — carries a larger multiple than a car insurer "
            "because its claims take longer to settle, so the level says "
            "less than the direction does.")
        if (res_v and prem_v and prem_v > 0) else
        _na((out["reserves"] or {}).get("reason")
            or "Premiums earned are not available."))

    dev = fund.metric(facts, "prior_year_reserve_development", as_of)
    out["prior_year_reserve_development"] = dev
    if dev.get("value") is None:
        out["reserve_development_pct_premiums"] = _na(
            "This insurer does not tag the change in reserves held for "
            "earlier years' claims. Twelve of the thirty-six measured do "
            "not, so whether its old reserves are proving adequate cannot "
            "be read from its machine-readable filings.")
        out["reserve_development_state"] = {
            "state": None,
            "reason": out["reserve_development_pct_premiums"]["reason"]}
        return out

    pct = ratio_of(
        dev, prem,
        "The change during the last twelve months in reserves held for "
        "claims from earlier years, as a share of premiums earned. Negative "
        "means those reserves proved more than enough and money was "
        "released back into profit; positive means they did not and more "
        "had to be added.")
    out["reserve_development_pct_premiums"] = pct
    v = pct.get("value")
    if v is None:
        out["reserve_development_state"] = {"state": None,
                                            "reason": pct.get("reason") or ""}
        return out
    band = float(cfg_get(cfg, "insurance_adverse_development_pct"))
    state = ("ADVERSE" if v > band else
             "FAVOURABLE" if v < -band else "BROADLY NEUTRAL")
    out["reserve_development_state"] = {
        "state": state, "pct_premiums": v, "amount": dev.get("value"),
        "reason": "",
        "basis": (f"Reserves for earlier years' claims moved "
                  f"{v:+.1f}% of a year's premiums. Adverse development is "
                  f"the signal that matters most in this industry: it says "
                  f"the insurer under-estimated what it already owed, and "
                  f"it tends to repeat. Moves inside {band:.0f}% either way "
                  f"are ordinary re-estimation.")}
    return out


def _capital_trend(fund, facts, as_of=None) -> dict:
    """Is common equity growing or shrinking against total assets?"""
    eq = fund.instant_pick(facts, "equity", as_of)
    ast = fund.instant_pick(facts, "assets", as_of)
    if not eq or not ast:
        return {"state": None,
                "reason": "Equity or total assets are not on file."}
    eq_rows, ast_rows = eq[1], ast[1]
    if len(eq_rows) < 2 or len(ast_rows) < 2:
        return {"state": None,
                "reason": "Only one balance-sheet reading is on file."}
    eq_prior = bm._year_ago(eq_rows, as_of)                # noqa: SLF001
    ast_by_end = {r["end"]: r["val"] for r in ast_rows}
    if eq_prior is None or eq_prior["end"] not in ast_by_end \
            or eq_rows[-1]["end"] not in ast_by_end:
        return {"state": None,
                "reason": ("No pair of balance sheets a year apart carries "
                           "both equity and total assets.")}
    now = _num(eq_rows[-1]["val"]) / _num(ast_by_end[eq_rows[-1]["end"]]) * 100.0
    prior = _num(eq_prior["val"]) / _num(ast_by_end[eq_prior["end"]]) * 100.0
    move = now - prior
    state = ("STRENGTHENING" if move > 0.5 else
             "WEAKENING" if move < -0.5 else "STEADY")
    return {"state": state, "change_pp": move, "now": now, "prior": prior,
            "reason": "",
            "basis": (f"Common equity was {now:.1f}% of total assets at "
                      f"{eq_rows[-1]['end']}, against {prior:.1f}% at "
                      f"{eq_prior['end']}.")}


def _book_trend(fund, facts, as_of=None) -> dict:
    """Growth in book value per share over the last year.

    Built from the same point-in-time series the valuation history runs on,
    so the trend and the multiple can never disagree about what book value
    per share was on a given day.
    """
    pts = (bm.point_in_time_series(fund, facts) or {}).get("book_per_share") or []
    pts = [p for p in pts if as_of is None or p["period_end"] <= as_of]
    if len(pts) < 5:
        return _na(f"Only {len(pts)} reported book values per share can be "
                   f"built — a year-over-year comparison needs five.")
    now, prior = _num(pts[-1]["value"]), _num(pts[-5]["value"])
    if not now or not prior or prior <= 0:
        return _na("The year-earlier book value per share is not positive.")
    return _ok((now / prior - 1.0) * 100.0,
               f"Book value per share at {pts[-1]['period_end']} against "
               f"{pts[-5]['period_end']}. Book value growing per share, plus "
               f"the dividend, is most of an insurer's long-run return.")


# ── peers ───────────────────────────────────────────────────────────────────

def subtype_peers(rows: list, subtype: str | None, cfg=None) -> dict:
    """Narrow a peer group to insurers of the same kind.

    A car insurer and a life insurer are both insurers and are not comparable
    investments: one is priced on underwriting margin and the other on the
    spread it earns between what it credits policyholders and what its
    portfolio yields. Where there are enough matched insurers the comparison
    is matched; where there are not it widens, and says so.
    """
    cfg = cfg or {}
    need = int(cfg_get(cfg, "insurance_min_subtype_peers"))
    usable = [r for r in (rows or [])
              if _num(r.get("price_to_book")) and _num(r["price_to_book"]) > 0]
    matched = [r for r in usable if r.get("subtype") == subtype] if subtype else []
    if subtype and len(matched) >= need:
        return {"rows": matched, "matched": True, "n": len(matched),
                "subtype": subtype,
                "reason": (f"Compared against {len(matched)} insurers writing "
                           f"the same kind of business.")}
    if not subtype:
        why = "This insurer's own subtype could not be established"
    else:
        why = (f"Only {len(matched)} insurers of the same kind have a usable "
               f"multiple, fewer than the {need} needed")
    return {"rows": usable, "matched": False, "n": len(usable),
            "subtype": subtype,
            "reason": (f"{why}, so the comparison widens to all "
                       f"{len(usable)} insurers in the group. A "
                       f"property-casualty insurer and a life insurer do not "
                       f"trade at the same multiple of book, so treat this "
                       f"comparison as looser than a matched one.")}


def peer_inputs(rows: list) -> dict:
    """Price to book, price to tangible book and profitability for each
    comparable insurer."""
    pb, ptbv, roe = [], [], []
    for r in rows or []:
        m = _num(r.get("price_to_book"))
        if m is None or m <= 0:
            continue
        pb.append(m)
        roe.append(_num(r.get("return_on_equity_pct")))
        t = _num(r.get("price_to_tangible_book"))
        if t is not None and t > 0:
            ptbv.append(t)
    return {"pb_multiples": pb, "ptbv_multiples": ptbv, "roe_pcts": roe,
            "n": len(pb)}


# ── risk flags ──────────────────────────────────────────────────────────────

RISK_SIGNALS = {
    "adverse_reserve_development": "Reserves for earlier years' claims are "
                                   "proving inadequate",
    "underwriting_deteriorating": "The combined ratio is getting worse",
    "underwriting_loss": "Underwriting is losing money",
    "premiums_contracting": "Premiums written are shrinking",
    "roe_falling": "Return on equity is falling",
    "book_value_falling": "Book value per share is falling",
    "capital_weakening": "Equity is shrinking against total assets",
    "investment_income_falling": "Investment income is falling",
}


def risk_signals(ins: dict, prior_roe=None, cfg=None) -> dict:
    """Insurer-specific deterioration signals, in the shape the existing
    value-trap engine already grades.

    Each is either measurable and answered, or absent and reported as not
    measurable. None of them is a score, and there is deliberately no
    combined "insurance risk number": a cheap insurer releasing reserves it
    should be holding is a different problem from a cheap insurer whose book
    value is shrinking, and averaging them would hide both.
    """
    cfg = cfg or {}
    out: dict = {}
    alarm = float(cfg_get(cfg, "insurance_combined_ratio_alarm"))

    dev = (ins.get("reserve_development_state") or {})
    if dev.get("state"):
        out["adverse_reserve_development"] = {
            "active": dev["state"] == "ADVERSE",
            "detail": dev.get("basis") or ""}

    trend = (ins.get("combined_ratio_trend") or {})
    if trend.get("state"):
        out["underwriting_deteriorating"] = {
            "active": trend["state"] == "DETERIORATING",
            "detail": trend.get("basis") or ""}

    cr = (ins.get("combined_ratio_pct") or {}).get("value")
    if cr is not None:
        tail = ("pays out more than it takes in and relies on its investments "
                "to make up the difference" if cr > alarm else
                "makes money on the underwriting itself")
        out["underwriting_loss"] = {
            "active": cr > alarm,
            "detail": f"The combined ratio is {cr:.1f}%, so the insurer {tail}."}

    pg = (ins.get("premium_growth_pct") or {}).get("value")
    if pg is not None:
        out["premiums_contracting"] = {
            "active": pg < 0,
            "detail": f"Premiums earned are changing {pg:+.1f}% a year."}

    roe = (ins.get("return_on_equity_pct") or {}).get("value")
    p_roe = _num(prior_roe)
    if roe is not None and p_roe is not None:
        out["roe_falling"] = {
            "active": roe < p_roe - 2.0,
            "detail": (f"Return on equity is {roe:.1f}% against {p_roe:.1f}% "
                       f"a year earlier.")}

    bt = (ins.get("book_value_per_share_trend_pct") or {}).get("value")
    if bt is not None:
        out["book_value_falling"] = {
            "active": bt < 0,
            "detail": f"Book value per share is changing {bt:+.1f}% a year."}

    cap = (ins.get("equity_to_assets_trend") or {})
    if cap.get("state"):
        out["capital_weakening"] = {
            "active": cap["state"] == "WEAKENING",
            "detail": cap.get("basis") or ""}

    nii = (ins.get("net_investment_income_growth_pct") or {}).get("value")
    if nii is not None:
        out["investment_income_falling"] = {
            "active": nii < 0,
            "detail": f"Investment income is changing {nii:+.1f}% a year."}
    return out


# ── the valuation methods ───────────────────────────────────────────────────

def methods(ins: dict, history: dict, peers: dict | None = None,
            ten_year_pct=None, cfg=None) -> list:
    """The insurer fair-value methods, each built or each refusing."""
    cfg = cfg or {}
    peers = peers or {}
    out = []
    raw = (history or {}).get("raw_values") or {}

    bvps = (ins.get("book_per_share") or {}).get("value")
    tbvps = (ins.get("tangible_book_per_share") or {}).get("value")

    out.append(fv.method_multiple_history(
        "insurance_self_pb", "Its own price to book",
        "Book value per share, priced at this insurer's own point-in-time "
        "history of price to book. An insurer's assets are mostly securities "
        "carried at what they would fetch, so book value is closer to a real "
        "number here than in most industries.",
        bvps, (raw.get("price_to_book") or {}).get("5y")
        or (raw.get("price_to_book") or {}).get("all"),
        cfg=cfg, rank=3.4, what="book value per share",
        detail={"measure": "price_to_book"}))

    out.append(fv.method_multiple_history(
        "insurance_self_ptbv", "Its own price to tangible book",
        "Tangible book value per share — book value with goodwill and other "
        "intangibles taken out — priced at this insurer's own point-in-time "
        "history of the multiple.",
        tbvps, (raw.get("price_to_tangible_book") or {}).get("5y")
        or (raw.get("price_to_tangible_book") or {}).get("all"),
        cfg=cfg, rank=3.0, what="tangible book value per share",
        detail={"measure": "price_to_tangible_book"}))

    # Its own earnings history — the Phase 3 method, unchanged. An insurer's
    # earnings are real earnings; they are simply lumpier than most, because
    # a hurricane lands in one quarter and the premiums that pay for it were
    # collected over three years.
    out.append(fv.method_self_history(
        ins.get("eps_ttm"),
        ((raw.get("earnings_yield_pct") or {}).get("5y")
         or (raw.get("earnings_yield_pct") or {}).get("all")),
        cfg=cfg,
        regime_shifted=bool(((history or {}).get("regime") or {}).get("shifted")),
        window_label=(history or {}).get("window_label") or "5-year"))

    # Comparable insurers, priced off the profitability relationship where
    # one holds across the group — the same treatment the bank model gives
    # peer banks, and for the same reason: the cheapest price to book in a
    # group is usually the least profitable member of it, not a bargain.
    fitted = bm.peer_fitted_multiple(
        (ins.get("return_on_equity_pct") or {}).get("value"),
        peers.get("roe_pcts") or [], peers.get("pb_multiples") or [],
        cfg=cfg, kind="insurers", return_label="return on equity",
        multiple_label="price to book",
        min_peers=cfg_get(cfg, "insurance_min_peers_for_regression"),
        min_r2=cfg_get(cfg, "insurance_min_regression_r2"))
    out.append(fv.method_peer_multiple(
        "insurance_peers_pb",
        ("Comparable insurers writing the same kind of business"
         if peers.get("matched") else "Comparable insurers"),
        ("Book value per share at what comparable insurers cost per unit of "
         "their own book" +
         (", adjusted for how profitable this one is against them"
          if fitted.get("fitted") else "") + ". "
         + (peers.get("reason") or "")),
        bvps, peers.get("pb_multiples") or [],
        base_multiple=fitted.get("value"), level=peers.get("level"),
        cfg=cfg, rank=2.4 if peers.get("matched") else 1.9,
        detail={"fitted": fitted.get("fitted"), "r2": fitted.get("r2"),
                "slope": fitted.get("slope"),
                "median_multiple": fitted.get("median"),
                "matched_subtype": bool(peers.get("matched")),
                "subtype": peers.get("subtype"),
                "note": " ".join(x for x in (fitted.get("reason"),
                                             peers.get("reason")) if x)}))

    out.append(_justified(ins, ten_year_pct, cfg))
    return fv.stamp(out, BUSINESS_TYPE)


def _justified(ins: dict, ten_year_pct, cfg) -> dict:
    """Book value at the multiple this insurer's own profitability justifies.

    (ROE − g) ÷ (cost of equity − g), the dividend-discount result written in
    return-on-equity terms. An insurer earning exactly its cost of equity is
    worth exactly its book; one earning more is worth a premium and one
    earning less a discount. The formula and the cost of equity are the ones
    the bank model uses, deliberately: the equity risk premium is a
    market-wide convention and there is no honest reason for this screen to
    hold two of them.
    """
    basis = ("Book value per share at the multiple its own profitability "
             "justifies: (return on equity − growth) ÷ (cost of equity − "
             "growth)")
    key, label = "insurance_justified", "What its profitability justifies"
    bvps = (ins.get("book_per_share") or {}).get("value")
    if bvps is None or bvps <= 0:
        return fv._method(key, label, basis,                # noqa: SLF001
                          reason=(ins.get("book_per_share") or {}).get("reason")
                          or "Book value per share is not available.")
    ke = bm.cost_of_equity(ten_year_pct, cfg)
    if ke.get("value") is None:
        return fv._method(key, label, basis, reason=ke["reason"])  # noqa: SLF001
    roe = (ins.get("return_on_equity_pct") or {}).get("value")
    g = float(bm.cfg_get(cfg, "bank_terminal_growth_pct"))
    cut = float(cfg_get(cfg, "insurance_justified_roe_haircut_pct"))
    up = float(cfg_get(cfg, "insurance_justified_roe_uplift_pct"))
    mid = bm.justified_multiple(roe, ke["value"], g)
    if mid.get("value") is None:
        return fv._method(key, label, basis, reason=mid["reason"])  # noqa: SLF001
    lo = bm.justified_multiple(None if roe is None else roe - cut,
                               ke["value"], g)
    hi = bm.justified_multiple(None if roe is None else roe + up,
                               ke["value"], g)
    return fv._method(                                      # noqa: SLF001
        key, label, basis,
        bear=(bvps * lo["value"]) if lo.get("value") else None,
        base=bvps * mid["value"],
        bull=(bvps * hi["value"]) if hi.get("value") else None,
        n=1, rank=2.6,
        detail={"multiple_base": mid["value"],
                "multiple_bear": lo.get("value"),
                "multiple_bull": hi.get("value"),
                "roe_pct": roe, "cost_of_equity_pct": ke["value"],
                "growth_pct": g,
                "note": (f"The pessimistic and optimistic ends move the "
                         f"return on equity by −{cut:.0f} and +{up:.0f} "
                         f"points rather than by a distribution of prices, "
                         f"because this method is a formula and not a market "
                         f"observation.")})


def confidence_cap(ins: dict) -> tuple[str | None, str]:
    """How far the fair-value confidence must be held down, and why.

    An insurer releasing reserves it set aside for earlier years can report
    perfectly good earnings while its underwriting deteriorates, and the
    valuation methods cannot see that — they agree with each other on a book
    value that adverse development will later reduce. Capping confidence
    lowers the price this screen is willing to pay rather than adding a
    warning next to a number it did not change.
    """
    dev = (ins.get("reserve_development_state") or {})
    trend = (ins.get("combined_ratio_trend") or {})
    if dev.get("state") == "ADVERSE":
        return "LOW", (
            "Reserves held for earlier years' claims are proving inadequate: "
            + (dev.get("basis") or "") +
            " Confidence is held at LOW for that reason, which lowers the "
            "price this screen is willing to pay.")
    if trend.get("state") == "DETERIORATING":
        return "MODERATE", (
            "The combined ratio is getting worse: " + (trend.get("basis") or "")
            + " Confidence is held no higher than MODERATE for that reason.")
    return None, ""
