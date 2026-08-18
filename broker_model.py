"""broker_model.py — what a broker is worth, on its own terms (Phase 5).

A broker's balance sheet carries other people's money. Customer cash sits
segregated under the SEC's customer-protection rule, customer securities are
held in custody, and margin loans to customers appear as receivables funded
by customer credit balances. A leverage ratio built for a manufacturer reads
all of that as the broker's own borrowings, and a free-cash-flow model reads
a swing in customer balances as cash the business generated. Both are wrong,
which is why Phase 2 refused and why this module exists.

IS IT EVEN A BROKER

The industry codes here are the widest in the SEC's list, and this is the
first problem to solve rather than the last. Code 6211, "Security Brokers,
Dealers & Flotation", holds Charles Schwab, Goldman Sachs AND BlackRock.
Code 6200 holds LPL Financial and the CME. Code 6282, "Investment Advice",
holds Evercore and T. Rowe Price. An asset manager and a derivatives
exchange are perfectly good businesses; neither is a broker-dealer, and
valuing either on the book-value logic below would be a category error
dressed up as a model.

So the question is answered from the BALANCE SHEET, not from the code and
not from the prose. A broker-dealer holds customer money and earns
brokerage revenue, and the filings say so in concepts an asset manager has
no reason to tag: receivables from customers, cash segregated under federal
regulations, brokerage commissions, principal transactions, investment
banking revenue. Each has to be CURRENT — Evercore and PJT Partners tag
investment banking revenue whose series stopped in 2018 — and the
balance-sheet ones have to be MATERIAL, because every financial company
parks a little cash somewhere.

Measured across twenty-four filers in those industry codes, this test admits
all ten genuine broker-dealers and one of the fourteen others. The one is
MarketAxess, whose bond-trading venue does operate a registered broker-dealer
holding forty-nine million dollars of segregated customer cash. That is a
real broker-dealer fact about a business that is really a trading venue, and
it is stated here rather than papered over: MarketAxess is valued on book
value by this model and should not be.

WHAT KIND OF BROKER

Retail, institutional, diversified — read from the annual report, and unlike
the insurer subtypes this one does NOT change which numbers are valid. A
retail broker and an institutional one are both read on book value, return
on equity and their own history of price to earnings. So where the report
cannot separate them the model still runs and the mix is reported as
undetermined, rather than the whole filer being refused for a label that
would not have changed a single number.

A broker that also runs a bank is flagged separately, from its filed
deposits rather than from its prose. Schwab, Raymond James, Stifel, Morgan
Stanley and Goldman Sachs all fund themselves substantially with deposits,
and that changes what their balance sheet is doing.

WHAT IS REFUSED

Client assets, assets under administration and net new assets are the
numbers this industry actually runs on, and they are not in the filings.
`PayablesToCustomers` is tagged by eight of the ten brokers measured and by
none of them since 2020; `AssetsUnderManagementCarryingAmount` appears once,
for LPL Financial, dated 2012. They are reported as unavailable with that
reason. They are not estimated from anything else.

Nothing here is scored, summed or ranked into a single number.
"""
from __future__ import annotations

import math

import bank_model as bm
import fair_value as fv

BROKER_MODEL_VERSION = "invest-broker-1.0.0"

BUSINESS_TYPE = "BROKER"

DEFAULTS = {
    # Deposits above this share of total assets make the deposit book a
    # material part of what the firm is, not a sideline.
    "broker_material_deposits_pct": 10.0,
    # A peer group needs this many broker-dealers before it is a group.
    "broker_min_subtype_peers": 5,
    "broker_min_peers_for_regression": 8,
    "broker_min_regression_r2": 0.20,
    "broker_justified_roe_haircut_pct": 4.0,
    "broker_justified_roe_uplift_pct": 3.0,
    # Assets to equity above this is called out. A broker-dealer is levered
    # by design; the direction of travel is what the flag is for.
    "broker_leverage_alarm": 15.0,
}

# Evidence that a filer is an actual broker-dealer rather than an asset
# manager or an exchange sharing its industry code. Each entry is
# (metric name, whether it is a balance-sheet instant, what it shows).
_BROKER_EVIDENCE = (
    ("customer_receivables", True,
     "it reports money owed to it by its own customers, which is what a "
     "margin loan is"),
    ("segregated_cash", True,
     "it holds cash and securities segregated for the benefit of customers, "
     "as the SEC's customer-protection rule requires of a broker-dealer"),
    ("brokerage_commissions", False,
     "it earns commissions on customer trades"),
    ("principal_transactions", False,
     "it earns revenue trading its own positions as a dealer"),
    ("investment_banking_revenue", False,
     "it earns underwriting and advisory revenue"),
)

# How far behind the filer's own newest fact a piece of evidence may be and
# still describe the business today. Evercore and PJT Partners tag investment
# banking revenue whose series stopped in 2018; Intercontinental Exchange's
# segregated cash stopped in 2015. None of the three is admitted on it.
_EVIDENCE_MAX_LAG = 400.0

# Customer money has to be a MATERIAL part of the balance sheet, not a
# rounding line. Ameriprise Financial parks nine hundred million dollars of
# segregated cash against a hundred and ninety-eight billion of assets —
# half a percent — and it is a wealth and insurance group rather than a
# broker-dealer. Every one of the ten genuine brokers measured clears one
# percent on at least one balance-sheet measure; the thinnest, Virtu, holds
# customer receivables worth 1.1% of its assets.
_EVIDENCE_MIN_SHARE_PCT = 1.0


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


# ── is it a broker-dealer at all ────────────────────────────────────────────

def broker_evidence(fund, facts, as_of: str | None = None) -> dict:
    """What in this filer's own balance sheet and income statement says it is
    a broker-dealer.

    Returns the evidence found and the reason when there is none. An empty
    list is the answer for BlackRock, T. Rowe Price and the CME, and that is
    the point of asking.
    """
    newest = fund._newest_period(facts)                     # noqa: SLF001
    assets = _num(fund.instant(facts, "assets", as_of).get("value"))
    found = []
    for name, is_instant, phrase in _BROKER_EVIDENCE:
        block = (fund.instant(facts, name, as_of) if is_instant
                 else fund.metric(facts, name, as_of))
        v = _num(block.get("value"))
        if v is None or v == 0:
            continue
        end = block.get("as_of") or block.get("period_end")
        lag = fund._days_between(end, as_of or newest)       # noqa: SLF001
        if lag is not None and lag > _EVIDENCE_MAX_LAG:
            continue
        share = None
        if is_instant and assets:
            share = abs(v) / assets * 100.0
            if share < _EVIDENCE_MIN_SHARE_PCT:
                continue
        found.append({"key": name, "phrase": phrase, "value": v,
                      "as_of": end, "concept": block.get("concept"),
                      "share_of_assets_pct": share})
    if found:
        return {"is_broker": True, "evidence": found, "reason": ""}
    return {
        "is_broker": False, "evidence": [],
        "reason": (
            "Nothing in this filer's machine-readable filings says it is a "
            "broker-dealer. It reports no receivables from customers, no "
            "cash segregated for customers under the SEC's "
            "customer-protection rule, no brokerage commissions, no dealer "
            "trading revenue and no underwriting or advisory revenue. Its "
            "SEC industry code puts it beside brokers, but that code also "
            "covers exchanges and asset managers, and this valuation is "
            "built on a broker's balance sheet. Forcing it through would be "
            "a category error with a number attached.")}


# ── the whole picture ───────────────────────────────────────────────────────

def metrics(fund, facts, price=None, shares_outstanding=None,
            subtype: str | None = None, as_of: str | None = None,
            cfg=None) -> dict:
    """Every broker measure this app can honestly report, each with its basis
    or each explaining its own absence."""
    cfg = cfg or {}
    out = {"available": False, "reason": "",
           "version": BROKER_MODEL_VERSION}

    ev = broker_evidence(fund, facts, as_of)
    out["broker_evidence"] = ev
    if not ev["is_broker"]:
        out["reason"] = ev["reason"]
        out["subtype"] = None
        out["subtype_label"] = ""
        return out

    sub = subtype or "UNDETERMINED"
    out["subtype"] = sub
    out["subtype_label"] = fund.BROKER_SUBTYPE_LABELS.get(sub, "")

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
    avg_eq = bm.average_balance(fund, facts, "equity", as_of)
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
        "reported share of equity",
        tce.get("reason") or "Average equity is not available.")

    # ── the income statement ────────────────────────────────────────────
    rev = fund.metric(facts, "revenue", as_of)
    out["revenue_ttm"] = rev
    out["revenue_growth_pct"] = bm._growth_block(          # noqa: SLF001
        fund, facts, "revenue", as_of)
    out["eps_growth_pct"] = bm._growth_block(              # noqa: SLF001
        fund, facts, "net_income", as_of)

    op = fund.metric(facts, "operating_income", as_of)
    out["operating_income"] = op
    out["operating_margin_pct"] = _pack(
        bm.return_on(op.get("value"), rev.get("value")),
        "Operating profit as a share of revenue",
        op.get("reason") or rev.get("reason")
        or "Operating profit is not tagged separately by this filer.")

    comp = fund.metric(facts, "compensation_expense", as_of)
    out["compensation_expense"] = comp
    out["compensation_ratio_pct"] = _pack(
        bm.return_on(comp.get("value"), rev.get("value")),
        "Employee compensation and benefits as a share of revenue. It is the "
        "largest single cost at every broker, so the direction it moves says "
        "most of what there is to say about operating leverage here.",
        comp.get("reason") or rev.get("reason") or "Not available.")

    nii = fund.metric(facts, "net_interest_income", as_of)
    if nii.get("value") is None:
        gross = fund.metric(facts, "interest_income_gross", as_of)
        out["interest_income_gross"] = gross
        out["net_interest_income"] = _na(
            (nii.get("reason") or "") +
            " Interest income before funding costs is shown instead where it "
            "is tagged, and is not the same figure: it leaves out what the "
            "broker pays for the money it lends on.")
    else:
        out["interest_income_gross"] = fund.metric(
            facts, "interest_income_gross", as_of)
        out["net_interest_income"] = nii
    out["net_interest_share_of_revenue_pct"] = _pack(
        bm.return_on(nii.get("value"), rev.get("value")),
        "Net interest income as a share of total revenue. A broker earning "
        "most of its revenue this way is closer to a bank than to a "
        "commission business, and its earnings move with short-term rates "
        "rather than with trading volumes.",
        nii.get("reason") or rev.get("reason") or "Not available.")

    comm = fund.metric(facts, "brokerage_commissions", as_of)
    prin = fund.metric(facts, "principal_transactions", as_of)
    ib = fund.metric(facts, "investment_banking_revenue", as_of)
    out["brokerage_commissions"] = comm
    out["principal_transactions"] = prin
    out["investment_banking_revenue"] = ib
    parts = [b for b in (comm, prin) if b.get("value") is not None]
    out["transaction_revenue"] = (
        _ok(sum(_num(b["value"]) for b in parts),
            "Commissions on customer trades plus dealer trading revenue, "
            "over the trailing twelve months. Only the components this filer "
            "actually tags are included, so it is a floor rather than a "
            "total.")
        if parts else
        _na("This broker tags neither commissions nor dealer trading revenue "
            "separately from its total revenue."))

    # ── customer franchise ──────────────────────────────────────────────
    out["customer_receivables"] = fund.instant(
        facts, "customer_receivables", as_of)
    out["segregated_cash"] = fund.instant(facts, "segregated_cash", as_of)
    unavailable = (
        "Client assets are the number this industry is actually judged on and "
        "they are not in the machine-readable filings. Eight of the ten "
        "brokers measured tag a customer payables balance and not one has "
        "done so since 2020; a single filer tags assets under management, "
        "dated 2012. Every figure that circulates comes from press releases "
        "and monthly activity reports. It is left blank rather than "
        "estimated from the balance sheet, because a broker's own capital "
        "says nothing about how much of its customers' money it holds.")
    out["client_assets"] = _na(unavailable)
    out["client_asset_growth_pct"] = _na(unavailable)
    out["net_new_assets"] = _na(unavailable)
    out["asset_management_revenue"] = _na(
        "Advisory and asset-management fees are not tagged separately by any "
        "of the ten brokers measured. What they tag is a single "
        "revenue-from-contracts-with-customers total that mixes advisory "
        "fees with commissions, so splitting it out would be guesswork.")

    # ── balance sheet ───────────────────────────────────────────────────
    assets = fund.instant(facts, "assets", as_of)
    out["assets"] = assets
    # Total equity, minority interests included, because total ASSETS include
    # what those interests own. Interactive Brokers consolidates a group its
    # public company owns about a quarter of; dividing consolidated assets by
    # the public company's own equity puts its leverage at 42 times against a
    # group figure nearer 14. Book value per share above uses the parent's
    # own equity, because those are the shares being valued.
    tot_eq = fund.total_equity(facts, as_of)
    out["total_equity"] = tot_eq
    out["assets_to_equity"] = (
        _ok(_num(assets["value"]) / _num(tot_eq["value"]),
            "Total assets over all the equity on the balance sheet, "
            "minority interests included so that both sides describe the "
            "same consolidated group. A broker-dealer is levered by design — "
            "customer margin loans are assets funded by customer credit "
            "balances — so the level says less than the direction.")
        if (_num(tot_eq.get("value")) and _num(assets.get("value"))) else
        _na(assets.get("reason") or tot_eq.get("reason")
            or "Total equity is not available."))
    dep = fund.instant(facts, "deposits", as_of)
    out["deposits"] = dep
    dep_share = None
    if _num(dep.get("value")) and _num(assets.get("value")):
        dep_share = _num(dep["value"]) / _num(assets["value"]) * 100.0
    out["deposits_share_of_assets_pct"] = _pack(
        dep_share,
        "Customer deposits as a share of total assets",
        dep.get("reason") or "This broker reports no deposits.")
    material = float(cfg_get(cfg, "broker_material_deposits_pct"))
    out["has_banking_operation"] = bool(dep_share and dep_share >= material)
    out["banking_note"] = (
        (f"Deposits fund {dep_share:.0f}% of this firm's balance sheet, so a "
         f"material part of what it does is banking. Its earnings move with "
         f"short-term interest rates as well as with customer activity.")
        if out["has_banking_operation"] else
        ("Deposits are not a material part of this firm's funding."
         if dep_share is not None else
         "This firm reports no deposits, so it is funded as a broker rather "
         "than as a bank."))

    out["diluted_share_trend_pct"] = bm._share_trend(fund, facts, as_of)  # noqa: SLF001
    out["book_value_per_share_trend_pct"] = _book_trend(fund, facts, as_of)

    out["available"] = any(
        (out.get(k) or {}).get("value") is not None
        for k in ("price_to_book", "price_to_tangible_book"))
    if not out["available"]:
        out["reason"] = (out["book_per_share"].get("reason")
                         or "This broker's book value could not be measured "
                            "from its filings.")
    return out


def _pack(value, basis, reason) -> dict:
    return ({"value": value, "basis": basis, "reason": ""} if value is not None
            else {"value": None, "basis": "", "reason": reason})


def _book_trend(fund, facts, as_of=None) -> dict:
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
               f"{pts[-5]['period_end']}")


# ── peers ───────────────────────────────────────────────────────────────────

def subtype_peers(rows: list, subtype: str | None, cfg=None) -> dict:
    """Narrow a peer group to brokers of the same kind.

    Every row handed in has already passed the broker-dealer test, so this
    narrows within brokers rather than deciding what a broker is. Where a
    matched group is too small the comparison widens and says so — which
    costs precision and never costs honesty, because both kinds are read on
    the same measures.
    """
    cfg = cfg or {}
    need = int(cfg_get(cfg, "broker_min_subtype_peers"))
    usable = [r for r in (rows or [])
              if _num(r.get("price_to_book")) and _num(r["price_to_book"]) > 0]
    matched = ([r for r in usable if r.get("subtype") == subtype]
               if subtype and subtype != "UNDETERMINED" else [])
    if matched and len(matched) >= need:
        return {"rows": matched, "matched": True, "n": len(matched),
                "subtype": subtype,
                "reason": (f"Compared against {len(matched)} brokers of the "
                           f"same kind.")}
    if not subtype or subtype == "UNDETERMINED":
        why = ("This broker's own retail-or-institutional mix could not be "
               "read from its annual report")
    else:
        why = (f"Only {len(matched)} brokers of the same kind have a usable "
               f"multiple, fewer than the {need} needed")
    return {"rows": usable, "matched": False, "n": len(usable),
            "subtype": subtype,
            "reason": (f"{why}, so the comparison widens to all "
                       f"{len(usable)} broker-dealers in the group.")}


def peer_inputs(rows: list) -> dict:
    """Price to book, price to tangible book and profitability for each
    comparable broker."""
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
    "revenue_contracting": "Revenue is shrinking",
    "earnings_falling": "Earnings are falling",
    "roe_falling": "Return on equity is falling",
    "book_value_falling": "Book value per share is falling",
    "leverage_rising": "The balance sheet is carrying more assets per unit "
                       "of equity",
    "compensation_ratio_rising": "More of each dollar of revenue is going to "
                                 "staff",
}


def risk_signals(brk: dict, prior=None, cfg=None) -> dict:
    """Broker-specific deterioration signals, in the shape the existing
    value-trap engine already grades.

    `prior` carries the year-earlier readings this app recorded for the same
    ticker, where it has any. Nothing here is inferred from a level alone: a
    broker at fifteen times assets to equity may be perfectly sound and a
    broker moving from eight to fifteen is doing something different from
    what it did last year.
    """
    cfg = cfg or {}
    prior = prior or {}
    out: dict = {}

    rg = (brk.get("revenue_growth_pct") or {}).get("value")
    if rg is not None:
        out["revenue_contracting"] = {
            "active": rg < 0,
            "detail": f"Revenue is changing {rg:+.1f}% a year."}

    eg = (brk.get("eps_growth_pct") or {}).get("value")
    if eg is not None:
        out["earnings_falling"] = {
            "active": eg < 0,
            "detail": f"Earnings are changing {eg:+.1f}% a year."}

    roe = (brk.get("return_on_equity_pct") or {}).get("value")
    p_roe = _num(prior.get("return_on_equity_pct"))
    if roe is not None and p_roe is not None:
        out["roe_falling"] = {
            "active": roe < p_roe - 2.0,
            "detail": (f"Return on equity is {roe:.1f}% against {p_roe:.1f}% "
                       f"a year earlier.")}

    bt = (brk.get("book_value_per_share_trend_pct") or {}).get("value")
    if bt is not None:
        out["book_value_falling"] = {
            "active": bt < 0,
            "detail": f"Book value per share is changing {bt:+.1f}% a year."}

    lev = (brk.get("assets_to_equity") or {}).get("value")
    p_lev = _num(prior.get("assets_to_equity"))
    if lev is not None and p_lev is not None:
        alarm = float(cfg_get(cfg, "broker_leverage_alarm"))
        out["leverage_rising"] = {
            "active": lev > p_lev + 1.0 and lev > alarm,
            "detail": (f"Total assets are {lev:.1f} times common equity, "
                       f"against {p_lev:.1f} a year earlier.")}

    comp = (brk.get("compensation_ratio_pct") or {}).get("value")
    p_comp = _num(prior.get("compensation_ratio_pct"))
    if comp is not None and p_comp is not None:
        out["compensation_ratio_rising"] = {
            "active": comp > p_comp + 2.0,
            "detail": (f"Staff costs take {comp:.1f}% of revenue, against "
                       f"{p_comp:.1f}% a year earlier.")}
    return out


# ── the valuation methods ───────────────────────────────────────────────────

def methods(brk: dict, history: dict, peers: dict | None = None,
            ten_year_pct=None, cfg=None) -> list:
    """The broker fair-value methods, each built or each refusing."""
    cfg = cfg or {}
    peers = peers or {}
    out = []
    raw = (history or {}).get("raw_values") or {}

    bvps = (brk.get("book_per_share") or {}).get("value")
    tbvps = (brk.get("tangible_book_per_share") or {}).get("value")

    # A broker's earnings ARE earnings — commissions, interest and fees are
    # ordinary revenue against ordinary costs — so its own history of price
    # to earnings is the strongest method here, unlike at a bank or a
    # property trust where the balance sheet had to lead.
    out.append(fv.method_self_history(
        brk.get("eps_ttm"),
        ((raw.get("earnings_yield_pct") or {}).get("5y")
         or (raw.get("earnings_yield_pct") or {}).get("all")),
        cfg=cfg,
        regime_shifted=bool(((history or {}).get("regime") or {}).get("shifted")),
        window_label=(history or {}).get("window_label") or "5-year"))

    out.append(fv.method_multiple_history(
        "broker_self_pb", "Its own price to book",
        "Book value per share, priced at this broker's own point-in-time "
        "history of price to book.",
        bvps, (raw.get("price_to_book") or {}).get("5y")
        or (raw.get("price_to_book") or {}).get("all"),
        cfg=cfg, rank=3.0, what="book value per share",
        detail={"measure": "price_to_book"}))

    out.append(fv.method_multiple_history(
        "broker_self_ptbv", "Its own price to tangible book",
        "Tangible book value per share — book value with goodwill and other "
        "intangibles taken out — priced at this broker's own point-in-time "
        "history of the multiple.",
        tbvps, (raw.get("price_to_tangible_book") or {}).get("5y")
        or (raw.get("price_to_tangible_book") or {}).get("all"),
        cfg=cfg, rank=2.8, what="tangible book value per share",
        detail={"measure": "price_to_tangible_book"}))

    fitted = bm.peer_fitted_multiple(
        (brk.get("return_on_equity_pct") or {}).get("value"),
        peers.get("roe_pcts") or [], peers.get("pb_multiples") or [],
        cfg=cfg, kind="brokers", return_label="return on equity",
        multiple_label="price to book",
        min_peers=cfg_get(cfg, "broker_min_peers_for_regression"),
        min_r2=cfg_get(cfg, "broker_min_regression_r2"))
    out.append(fv.method_peer_multiple(
        "broker_peers_pb",
        ("Comparable brokers of the same kind" if peers.get("matched")
         else "Comparable broker-dealers"),
        ("Book value per share at what comparable brokers cost per unit of "
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

    out.append(_justified(brk, ten_year_pct, cfg))
    return fv.stamp(out, BUSINESS_TYPE)


def _justified(brk: dict, ten_year_pct, cfg) -> dict:
    """Book value at the multiple this broker's own profitability justifies.

    A broker earning thirty percent on its equity deserves a large premium to
    book and a broker earning six percent deserves a discount, and this says
    so precisely: (ROE − g) ÷ (cost of equity − g). The pessimistic and
    optimistic ends move the return on equity by more than they do for a bank
    or an insurer, because a broker's earnings swing with trading volumes and
    with short-term interest rates in a way a loan book's do not.
    """
    basis = ("Book value per share at the multiple its own profitability "
             "justifies: (return on equity − growth) ÷ (cost of equity − "
             "growth)")
    key, label = "broker_justified", "What its profitability justifies"
    bvps = (brk.get("book_per_share") or {}).get("value")
    if bvps is None or bvps <= 0:
        return fv._method(key, label, basis,                # noqa: SLF001
                          reason=(brk.get("book_per_share") or {}).get("reason")
                          or "Book value per share is not available.")
    ke = bm.cost_of_equity(ten_year_pct, cfg)
    if ke.get("value") is None:
        return fv._method(key, label, basis, reason=ke["reason"])  # noqa: SLF001
    roe = (brk.get("return_on_equity_pct") or {}).get("value")
    g = float(bm.cfg_get(cfg, "bank_terminal_growth_pct"))
    cut = float(cfg_get(cfg, "broker_justified_roe_haircut_pct"))
    up = float(cfg_get(cfg, "broker_justified_roe_uplift_pct"))
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


def confidence_cap(brk: dict) -> tuple[str | None, str]:
    """How far the fair-value confidence must be held down, and why.

    Client assets are the thing that actually determines what a retail broker
    is worth and they are not in the filings. A valuation built on book value
    and earnings alone can agree with itself perfectly while missing that
    customers are leaving, so confidence is capped for a broker whose
    business is customer-driven and whose customer numbers cannot be read.
    """
    if not (brk or {}).get("available"):
        return None, ""
    if (brk.get("client_assets") or {}).get("value") is None \
            and brk.get("subtype") in ("RETAIL", "DIVERSIFIED"):
        return "MODERATE", (
            "This is a customer-driven brokerage and the size of that "
            "customer base — client assets, and whether they are growing — "
            "is not in the machine-readable filings. The valuation below is "
            "built on book value and earnings, which can look steady while "
            "customers leave, so confidence is held no higher than MODERATE.")
    return None, ""
