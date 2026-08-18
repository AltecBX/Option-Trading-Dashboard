"""Which valuation model a company gets, and why — from its economics.

An SEC industry code is a filing convenience, not a description of a
business. Code 6211 holds Charles Schwab, Goldman Sachs and BlackRock. Code
6200 holds LPL Financial and the CME. Code 6282 holds Evercore and T. Rowe
Price. Phase 5 sent everything in those codes to the broker model, which
refused most of them, and the refusals were right for the wrong reason:
BlackRock is not a broker, but neither is it unvaluable — it is an ordinary
high-margin fee business the standard engine handles perfectly well.

This module decides from filed evidence instead:

  * what is on the balance sheet — customer receivables and segregated cash,
    deposits, loans, policy reserves — each as a share of total assets
  * what the revenue is made of — premiums, advisory fees, brokerage
    commissions, market data and clearing fees — each as a share of revenue
  * whether the accounts behave like a corporation's at all: capital
    expenditure reported, operating cash flow reported and positive and
    within sight of revenue. A broker fails that test because customer money
    moves through its operating cash flow; Goldman Sachs reports minus
    thirty-nine billion dollars of it against sixty-six billion of revenue,
    which is not a cash-flow problem, it is the wrong measure for the
    business.
  * what the company says it does, in the business chapter of its own annual
    report, but only when the reader is confident it found that chapter.

Materiality is the point throughout. Intercontinental Exchange holds
customer margin worth 4.6% of its assets because it runs clearing houses,
and Morgan Stanley holds customer balances worth 4.9% of its assets because
it is a broker. A threshold cannot tell those two apart, and nothing here
pretends it can: ICE earns 39.6% of its revenue from clearing fees and
market data, which settles it. Where the numbers do not settle it, the
answer is HYBRID and the reader is told which businesses disagree, not a
made-up single fair value.

The module returns a decision, the evidence behind it, and an exposure
profile. It never fabricates a sum of the parts: segment revenue and segment
income are not in SEC Company Facts at all, so a segment-weighted valuation
would be a guess wearing a decimal point.
"""

from __future__ import annotations

ROUTING_VERSION = "invest-routing-1.0.0"

# ── the classes ─────────────────────────────────────────────────────────────

STANDARD = "STANDARD"
BANK = "BANK"
INSURANCE = "INSURANCE"
REIT = "REIT"
BROKER = "BROKER"
EXCHANGE = "EXCHANGE"
ASSET_MANAGER = "ASSET_MANAGER"
HYBRID = "HYBRID"
CYCLICAL = "CYCLICAL"
UNPROFITABLE = "UNPROFITABLE"
UNSUPPORTED = "UNSUPPORTED"

CLASS_LABEL = {
    STANDARD: "Standard operating company",
    BANK: "Bank or lender",
    INSURANCE: "Insurer",
    REIT: "Real estate investment trust",
    BROKER: "Broker or dealer",
    EXCHANGE: "Exchange or market infrastructure",
    ASSET_MANAGER: "Asset manager",
    HYBRID: "More than one financial business",
    CYCLICAL: "Cyclical or commodity producer",
    UNPROFITABLE: "Currently unprofitable",
    UNSUPPORTED: "Not supported",
}

# Which valuation model each class is sent to. An exchange and an asset
# manager both go to the STANDARD engine, because both have ordinary
# corporate revenue, margins and free cash flow, and neither is valued on
# book. That is the whole point of separating them from BROKER.
CLASS_MODEL = {
    STANDARD: "STANDARD",
    BANK: "BANK",
    INSURANCE: "INSURANCE",
    REIT: "REIT",
    BROKER: "BROKER",
    EXCHANGE: "STANDARD",
    ASSET_MANAGER: "STANDARD",
    CYCLICAL: "STANDARD",
    UNPROFITABLE: "STANDARD",
    UNSUPPORTED: "",
}

CLASS_NOTE = {
    EXCHANGE: "An exchange sells access to a market and the data it "
              "produces. It has ordinary revenue, ordinary margins and "
              "ordinary free cash flow, so it is valued the ordinary way. "
              "Customer money on its balance sheet is clearing margin held "
              "for members, not a brokerage business.",
    ASSET_MANAGER: "An asset manager earns fees on other people's money. "
                   "The money it manages is not on its balance sheet, so "
                   "book value says little and revenue, margins and free "
                   "cash flow say a great deal. It is valued the ordinary "
                   "way.",
    HYBRID: "This company is more than one financial business at once, and "
            "each of those businesses is valued differently. Which model is "
            "right depends on which business dominates, and that is stated "
            "rather than assumed.",
}

# Only these classes may be reached from a financial industry code by
# evidence. Banks, insurers and property trusts keep the classification they
# have had since Phase 2, because it is well tested and correct.
_SIC_BANK = ((6020, 6036), (6099, 6099), (6120, 6198))
_SIC_INSURANCE = ((6300, 6411),)
_SIC_REIT = ((6798, 6798), (6500, 6552), (6798, 6799))
# 6199 is "finance services", the code the SEC gives a filer that fits
# nowhere else — Coinbase files under it. It is answered from evidence
# alongside the broker codes rather than assumed to be a lender, which is
# what putting it at the top of the bank range used to do.
_SIC_BROKERISH = ((6199, 6299),)


# ── thresholds, every one of them measured ──────────────────────────────────

DEFAULTS = {
    # Customer receivables plus segregated cash, as a share of total assets.
    # Every genuine broker measured clears three percent: Stifel is thinnest
    # at 3.4%. MarketAxess sits at 2.0% and Ameriprise at 0.5%.
    "routing_customer_share_pct": 3.0,
    # Deposits as a share of assets. A broker funded like a bank — Stifel at
    # 72%, Raymond James at 67% — is still a broker, but the deposits are
    # what makes it one rather than an advisory firm.
    "routing_deposit_share_pct": 20.0,
    # Commissions, dealer trading and underwriting as a share of revenue.
    # Virtu is a market maker with barely any customer balances, and its
    # trading gains are most of what it earns; MarketAxess earns 5.9% from
    # principal trading and is a venue rather than a dealer.
    "routing_brokerage_share_pct": 30.0,
    # Policy reserves or future policy benefits as a share of assets. Apollo
    # is at 70% and KKR at 27% because both bought an annuity writer;
    # Berkshire is at 6.6%, which is why it is not routed as a pure insurer.
    "routing_insurance_share_pct": 10.0,
    # Premiums earned as a share of revenue.
    "routing_premium_share_pct": 10.0,
    # Investment advisory and management fees as a share of revenue. Below
    # twenty percent it is a business line, not the business. In practice
    # this measure is almost always silent: the concepts that carry it —
    # InvestmentAdvisoryFees, AssetManagementFees — stopped being tagged
    # between 2013 and 2018, when the revenue standard changed and everyone
    # folded them into a single revenue-from-contracts total. Every asset
    # manager measured has a series that ends in the 2010s, and the
    # freshness rule refuses all of them, which is why an asset manager is
    # recognised from its business chapter instead.
    "routing_advisory_share_pct": 20.0,
    # Market data plus clearing fees as a share of revenue. Nothing that is
    # not an exchange tags either concept — but almost no exchange tags them
    # currently either: the CME, Intercontinental Exchange and Cboe all have
    # series that stop in 2018, when the revenue standard changed and the
    # detail was folded into one revenue-from-contracts total. The measure
    # is kept because it is decisive when it is there, and the freshness
    # rule silences it when it is not, which is why an exchange is normally
    # recognised from its business chapter.
    "routing_exchange_share_pct": 5.0,
    # How many distinct kinds of market-infrastructure language the business
    # chapter has to use before the description alone counts as evidence.
    # Two catches Nasdaq, Tradeweb, Coinbase, MarketAxess and Virtu, and
    # leaves Goldman Sachs and Morgan Stanley out, both of which fail the
    # corporate-accounts test anyway.
    "routing_exchange_phrases": 2,
    # Asset-management language appears in every broker's chapter too, but a
    # broker is settled earlier, by the customer money on its balance sheet.
    # Two distinct kinds catches BlackRock, T. Rowe Price, Invesco, Franklin,
    # Affiliated Managers, Blackstone, KKR, Apollo and Ares.
    "routing_manager_phrases": 2,
    # A business this large is the business: everything else is disclosed
    # rather than modelled.
    "routing_dominant_share_pct": 65.0,
    # Operating cash flow as a share of revenue, for the "does this behave
    # like a corporation" test. Below the floor or above the ceiling the
    # figure is customer money moving, not the company's own cash.
    "routing_min_ocf_margin_pct": 2.0,
    "routing_max_ocf_margin_pct": 150.0,
}


# How far behind a filer's own newest fact a piece of evidence may be and
# still describe the business today. The same 400 days the Phase 5 broker
# gate uses, for the same reason.
_MAX_EVIDENCE_LAG_DAYS = 400.0


def cfg_get(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _in(code, ranges) -> bool:
    return code is not None and any(lo <= code <= hi for lo, hi in ranges)


def _share(part, whole):
    p, w = _num(part), _num(whole)
    if p is None or not w or w <= 0:
        return None
    return abs(p) / w * 100.0


# ── what the business chapter says ──────────────────────────────────────────
#
# Each entry is a family of phrasings for one idea. A family counts once
# however many times it appears, so a chapter that says "exchange" forty
# times in its regulation section still scores one.

EXCHANGE_PHRASES = {
    "a market venue": (
        "national securities exchange", "futures exchange", "options exchange",
        "derivatives exchange", "stock exchange", "commodity exchange",
        "commodities exchange"),
    "a clearing house": (
        "clearing house", "clearinghouse", "clearing organization",
        "central counterparty"),
    "an electronic marketplace": (
        "electronic trading platform", "electronic platform",
        "electronic marketplace", "trading platform"),
    "listing services": (
        "listing venue", "listing services", "listing standards",
        "listing fees"),
    "selling market data": (
        "market data revenue", "market data products", "market data services",
        "market data fees", "market data business"),
    "a regulated contract market": (
        "designated contract market", "self-regulatory organization"),
}

MANAGER_PHRASES = {
    "money managed for others": ("assets under management",),
    "an investment management business": (
        "investment management business", "investment management services",
        "investment management segment", "investment management fees",
        "investment management company", "investment management firm"),
    "an asset management business": (
        "asset management business", "asset management services",
        "asset management segment", "asset management fees",
        "asset management company", "asset management firm"),
    "a registered adviser": (
        "registered investment adviser", "investment adviser to"),
    "funds sold to the public": (
        "mutual funds", "exchange-traded funds", "exchange traded funds"),
    # Apollo, Blackstone, KKR and Ares all use this exact phrase in their
    # first sentence, and none of them tags an advisory fee concept.
    "an alternative manager": (
        "alternative asset manager", "alternative investment manager",
        "alternative asset management", "alternative investment management"),
}

# A holding company that says it owns unrelated businesses is not the
# industry its SEC code names, however large one of those businesses is.
CONGLOMERATE_PHRASES = (
    "diverse business activities", "diversified holding company",
    "numerous other businesses", "unrelated businesses", "conglomerate",
)


def _families(text: str, families: dict) -> list[str]:
    low = (text or "").lower()
    return [name for name, words in families.items()
            if any(w in low for w in words)]


def conglomerate(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in CONGLOMERATE_PHRASES)


def phrase_evidence(text: str) -> dict:
    """What kinds of business the chapter describes, reduced to family names.

    This is computed once when the business chapter is read and cached with
    the company profile, so routing never needs sixty thousand characters of
    annual report in memory to answer a question about six phrases.
    """
    return {"exchange": _families(text, EXCHANGE_PHRASES),
            "manager": _families(text, MANAGER_PHRASES),
            "conglomerate": conglomerate(text)}


# ── do the accounts behave like a corporation's ─────────────────────────────

def corporate_accounts(fund, facts, as_of=None, cfg=None) -> dict:
    """Whether free cash flow means anything for this filer.

    An operating company reports capital expenditure and turns a positive,
    sane fraction of its revenue into operating cash. A broker or a bank does
    not: customer balances move through operating cash flow, so the figure
    swings from minus a hundred and sixty billion (JPMorgan) to plus two
    hundred and thirty percent of revenue (Interactive Brokers) without
    saying anything about the business.
    """
    rev = _num(fund.metric(facts, "revenue", as_of).get("value"))
    ocf = _num(fund.metric(facts, "operating_cash_flow", as_of).get("value"))
    capex = _num(fund.metric(facts, "capex", as_of).get("value"))
    lo = float(cfg_get(cfg, "routing_min_ocf_margin_pct"))
    hi = float(cfg_get(cfg, "routing_max_ocf_margin_pct"))
    margin = None if (ocf is None or not rev) else ocf / rev * 100.0
    if capex is None:
        return {"ok": False, "ocf_margin_pct": margin,
                "reason": "This filer reports no capital expenditure, so free "
                          "cash flow cannot be built for it."}
    if margin is None:
        return {"ok": False, "ocf_margin_pct": None,
                "reason": "This filer reports no operating cash flow against "
                          "revenue, so free cash flow cannot be built."}
    if margin < lo or margin > hi:
        return {"ok": False, "ocf_margin_pct": margin,
                "reason": (f"Operating cash flow is {margin:,.0f}% of revenue, "
                           f"outside the {lo:,.0f}% to {hi:,.0f}% band that "
                           f"an operating company's own cash falls in. Money "
                           f"belonging to customers is moving through the "
                           f"figure, so it is not free cash flow.")}
    return {"ok": True, "ocf_margin_pct": margin,
            "reason": "",
            "basis": (f"Capital expenditure is reported and operating cash "
                      f"flow is {margin:,.0f}% of revenue, which is an "
                      f"operating company's own cash rather than its "
                      f"customers'.")}


# ── the measured exposures ──────────────────────────────────────────────────

def exposures(fund, facts, phrases=None, text_usable=False,
              as_of=None, cfg=None) -> list[dict]:
    """Every business this filer is materially in, with the measurement.

    Shares are not comparable across families — deposits are a share of
    assets and premiums a share of revenue — so each exposure carries the
    measure it was judged on and nothing here averages them.
    """
    assets = _num(fund.instant(facts, "assets", as_of).get("value"))
    revenue = _num(fund.metric(facts, "revenue", as_of).get("value"))
    newest = None
    try:
        newest = fund._newest_period(facts)                   # noqa: SLF001
    except Exception:                                        # pragma: no cover
        newest = None

    def _current(block) -> bool:
        """Evidence has to describe the business NOW.

        Evercore tags investment banking revenue whose series stopped in
        2018; reading it today would call an advisory firm a dealer on the
        strength of a figure older than the phone it is read on. The same
        rule the Phase 5 broker gate already applies is applied here.
        """
        end = block.get("as_of") or block.get("period_end")
        try:
            lag = fund._days_between(end, as_of or newest)     # noqa: SLF001
        except Exception:                                    # pragma: no cover
            return True
        return lag is None or lag <= _MAX_EVIDENCE_LAG_DAYS

    def inst(name):
        block = fund.instant(facts, name, as_of)
        return _num(block.get("value")) if _current(block) else None

    def dur(name):
        block = fund.metric(facts, name, as_of)
        return _num(block.get("value")) if _current(block) else None

    customer = (inst("customer_receivables") or 0.0) + (inst("segregated_cash") or 0.0)
    reserves = max([x for x in (inst("insurance_reserves"),
                                inst("future_policy_benefits"),
                                inst("policyholder_liabilities")) if x] or [0.0])
    brokerage = sum(dur(k) or 0.0 for k in ("brokerage_commissions",
                                            "principal_transactions",
                                            "trading_gains",
                                            "investment_banking_revenue"))
    venue = sum(dur(k) or 0.0 for k in ("market_data_revenue",
                                        "clearing_fees_revenue"))
    # A lender shows up as deposits OR as loans, and a filer under the
    # catch-all finance code may show only one of the two.
    funding = max([x for x in (inst("deposits"), inst("loans")) if x] or [0.0])

    rows = [
        (BROKER, _share(customer or None, assets), "routing_customer_share_pct",
         "customer receivables and segregated cash as a share of total assets"),
        (BROKER, _share(brokerage or None, revenue), "routing_brokerage_share_pct",
         "commissions, dealer trading and underwriting as a share of revenue"),
        (BANK, _share(funding or None, assets), "routing_deposit_share_pct",
         "customer deposits or loans as a share of total assets"),
        (INSURANCE, _share(reserves or None, assets), "routing_insurance_share_pct",
         "policy reserves as a share of total assets"),
        (EXCHANGE, _share(venue or None, revenue), "routing_exchange_share_pct",
         "market data and clearing fees as a share of revenue"),
        (ASSET_MANAGER, _share(dur("investment_advisory_fees"), revenue),
         "routing_advisory_share_pct",
         "investment advisory and management fees as a share of revenue"),
    ]
    out = []
    for kind, share, key, measure in rows:
        if share is None:
            continue
        floor = float(cfg_get(cfg, key))
        out.append({"business": kind, "label": CLASS_LABEL[kind],
                    "share_pct": share, "measure": measure,
                    "threshold_pct": floor, "material": share >= floor,
                    "evidence": "filed figures"})

    # Premiums are a second, independent way to see an insurance business,
    # and the only one that catches a general insurer whose reserves are not
    # tagged under either concept.
    prem = _share(dur("premiums_earned"), revenue)
    if prem is not None:
        floor = float(cfg_get(cfg, "routing_premium_share_pct"))
        out.append({"business": INSURANCE, "label": CLASS_LABEL[INSURANCE],
                    "share_pct": prem, "measure":
                        "premiums earned as a share of revenue",
                    "threshold_pct": floor, "material": prem >= floor,
                    "evidence": "filed figures"})

    if text_usable and phrases:
        for kind, field, key in ((EXCHANGE, "exchange",
                                  "routing_exchange_phrases"),
                                 (ASSET_MANAGER, "manager",
                                  "routing_manager_phrases")):
            hits = list((phrases or {}).get(field) or ())
            need = int(cfg_get(cfg, key))
            if hits:
                out.append({"business": kind, "label": CLASS_LABEL[kind],
                            "share_pct": None,
                            "measure": "what the business chapter describes: "
                                       + ", ".join(hits),
                            "threshold_pct": None,
                            "material": len(hits) >= need,
                            "evidence": "business description"})
    return out


def _phrase_count(row) -> int:
    """How many kinds of description a text exposure rests on."""
    if not row:
        return 0
    measure = row.get("measure") or ""
    tail = measure.split(": ", 1)[-1]
    return len([p for p in tail.split(", ") if p.strip()])


def _material(rows, kind) -> dict | None:
    hits = [r for r in rows if r["business"] == kind and r["material"]]
    if not hits:
        return None
    # The strongest measurement of the same business is the one to quote.
    return max(hits, key=lambda r: (r["share_pct"] is not None,
                                    r.get("share_pct") or 0.0))


# ── the decision ────────────────────────────────────────────────────────────

def route(fund, facts, sic=None, business_text="", text_confidence=None,
          eps_ttm=None, ok: bool = True, as_of=None, cfg=None,
          text_usable=None, phrases=None) -> dict:
    """Which model this company gets, the evidence, and the exposure profile."""
    code = None
    try:
        code = int(str(sic).strip()) if sic not in (None, "") else None
    except (TypeError, ValueError):
        code = None

    if not ok or facts is None:
        return _out(UNSUPPORTED, code, [], [], "LOW",
                    ["There is not enough reported data to classify this "
                     "filer."])

    if text_usable is None:
        text_usable = text_confidence in ("HIGH", "MODERATE")
    if phrases is None:
        phrases = phrase_evidence(business_text)
    rows = exposures(fund, facts, phrases, text_usable, as_of, cfg)
    corp = corporate_accounts(fund, facts, as_of, cfg)
    why: list[str] = []

    # ── the three classifications Phases 2 to 5 already settle ──────────
    if _in(code, _SIC_BANK):
        why.append(f"Its SEC industry code {code} is a bank's, and its "
                   f"balance sheet is a lender's.")
        return _out(BANK, code, rows, [], "HIGH", why, corp)
    if _in(code, _SIC_REIT):
        why.append(f"Its SEC industry code {code} is a property trust's.")
        return _out(REIT, code, rows, [], "HIGH", why, corp)
    if _in(code, _SIC_INSURANCE):
        # …unless the company's own business chapter says it is a holding
        # company for unrelated businesses. Berkshire Hathaway files under an
        # insurance code and describes a railway, a utility group and a
        # collection of manufacturers in its first paragraph.
        if text_usable and (phrases or {}).get("conglomerate"):
            why.append("Its SEC industry code is an insurer's, but its own "
                       "business chapter describes a holding company owning "
                       "unrelated businesses, so it is not read as a pure "
                       "insurer.")
            ins = _material(rows, INSURANCE)
            secondary = [INSURANCE] if ins else []
            if ins:
                why.append(f"Insurance is {ins['share_pct']:,.1f}% by "
                           f"{ins['measure']}.")
            else:
                why.append("Its insurance business is not large enough in "
                           "its filed figures to value the whole company on.")
            primary = STANDARD if corp.get("ok") else INSURANCE
            if corp.get("ok"):
                why.append(corp.get("basis") or "")
            return _out(HYBRID, code, rows, secondary, "MODERATE", why, corp,
                        primary=primary)
        why.append(f"Its SEC industry code {code} is an insurer's.")
        return _out(INSURANCE, code, rows, [], "HIGH", why, corp)

    # ── the financial codes that mix three different businesses ─────────
    if _in(code, _SIC_BROKERISH):
        return _route_financial(code, rows, corp, why, cfg)

    # ── everything else, exactly as before ──────────────────────────────
    e = _num(eps_ttm)
    if e is not None and e <= 0:
        return _out(UNPROFITABLE, code, rows, [], "HIGH",
                    ["The company is losing money, so an earnings-based "
                     "valuation has no denominator."], corp)
    if code is None:
        return _out(UNSUPPORTED, None, rows, [], "LOW",
                    ["This filer has no SEC industry code on record."], corp)
    return _out(STANDARD, code, rows, [], "HIGH",
                ["Nothing in its filings shows a bank, insurance, property "
                 "or brokerage balance sheet, so the ordinary model applies."],
                corp)


def _route_financial(code, rows, corp, why, cfg) -> dict:
    """The 6199–6299 codes, where three unlike businesses share a number."""
    broker = _material(rows, BROKER)
    bank = _material(rows, BANK)
    insurance = _material(rows, INSURANCE)
    venue = next((r for r in rows if r["business"] == EXCHANGE
                  and r["material"] and r["evidence"] == "filed figures"), None)
    venue_text = next((r for r in rows if r["business"] == EXCHANGE
                       and r["material"] and r["evidence"] == "business description"),
                      None)
    manager = _material(rows, ASSET_MANAGER)
    manager_fees = next((r for r in rows if r["business"] == ASSET_MANAGER
                         and r["material"] and r["evidence"] == "filed figures"),
                        None)

    def secondaries(primary):
        got, seen = [], {primary}
        for kind, row in ((EXCHANGE, venue or venue_text), (BROKER, broker),
                          (BANK, bank), (INSURANCE, insurance),
                          (ASSET_MANAGER, manager)):
            if row is not None and kind not in seen:
                got.append(kind)
                seen.add(kind)
        return got

    # 1. Market data and clearing fees are decisive on their own. A company
    #    earning two fifths of its revenue from running a market is market
    #    infrastructure, and the member margin on its balance sheet is not a
    #    brokerage business — that is Intercontinental Exchange, which would
    #    otherwise be read as a broker on 4.6% customer balances.
    if venue is not None:
        why.append(f"It earns {venue['share_pct']:,.1f}% of its revenue from "
                   f"market data and clearing fees, which is the business of "
                   f"running a market rather than trading on one.")
        if corp.get("ok"):
            why.append(corp.get("basis") or "")
        return _out(EXCHANGE, code, rows, secondaries(EXCHANGE),
                    "HIGH", why, corp)

    # 2. Customer money on the balance sheet, or trading revenue in the
    #    income statement, makes a broker or a dealer.
    if broker is not None:
        why.append(f"{broker['measure'].capitalize()} are "
                   f"{broker['share_pct']:,.1f}%, which is a broker-dealer "
                   f"carrying customer positions or trading as principal.")
        if bank is not None:
            why.append(f"It is also funded like a bank: {bank['measure']} "
                       f"are {bank['share_pct']:,.1f}%.")
        return _out(BROKER, code, rows, secondaries(BROKER), "HIGH", why, corp)

    # 3. Fees on other people's money, in the filings rather than the prose.
    if manager_fees is not None and corp.get("ok") and insurance is None:
        why.append(f"Advisory and management fees are "
                   f"{manager_fees['share_pct']:,.1f}% of its revenue, and "
                   f"the money it manages is not on its balance sheet.")
        why.append(corp.get("basis") or "")
        return _out(ASSET_MANAGER, code, rows, secondaries(ASSET_MANAGER),
                    "HIGH", why, corp)

    # 4. A venue or a manager, described rather than tagged, and with an
    #    operating company's accounts. Both kinds of language turn up in
    #    both kinds of company — Franklin Resources' regulation section
    #    mentions clearing houses and national securities exchanges, and
    #    Intercontinental Exchange's mentions the funds it advises — so the
    #    one the chapter says MORE about wins, and a tie goes to the venue,
    #    because a manager describes exchanges it uses while a venue does
    #    not describe money it does not manage.
    if corp.get("ok") and insurance is None and (venue_text or manager):
        v_hits = _phrase_count(venue_text)
        m_hits = _phrase_count(manager)
        pick = EXCHANGE if (venue_text and v_hits >= m_hits) else ASSET_MANAGER
        row = venue_text if pick == EXCHANGE else manager
        if row is not None:
            why.append("Its own business chapter describes "
                       + row["measure"].split(": ", 1)[-1] + ".")
            if pick == EXCHANGE:
                why.append("No customer balances of any size sit on its "
                           "balance sheet, so it is not carrying brokerage "
                           "positions.")
            if venue_text and manager:
                why.append(f"Its chapter says more about running a market "
                           f"({v_hits} kinds of description) than about "
                           f"managing money ({m_hits})."
                           if pick == EXCHANGE else
                           f"Its chapter says more about managing money "
                           f"({m_hits} kinds of description) than about "
                           f"running a market ({v_hits}).")
            why.append(corp.get("basis") or "")
            return _out(pick, code, rows, secondaries(pick),
                        "MODERATE", why, corp)

    # 6. Two material financial businesses at once. Apollo and KKR each own
    #    an annuity writer whose reserves are most of the balance sheet while
    #    the fee business is what the market pays for; Ameriprise is a wealth
    #    manager and an annuity writer together. Nothing here averages them.
    live = [k for k in (INSURANCE, ASSET_MANAGER, BANK, EXCHANGE)
            if _material(rows, k) is not None]
    if len(live) >= 2:
        ranked = sorted(
            ((k, _material(rows, k)) for k in live),
            key=lambda kv: kv[1].get("share_pct") or 0.0, reverse=True)
        primary = ranked[0][0]
        for kind, row in ranked:
            if row.get("share_pct") is not None:
                why.append(f"{CLASS_LABEL[kind]}: {row['share_pct']:,.1f}% by "
                           f"{row['measure']}.")
            else:
                why.append(f"{CLASS_LABEL[kind]}: {row['measure']}.")
        why.append("Two different businesses are each large enough to matter "
                   "and each is valued a different way, so neither model is "
                   "applied on its own.")
        return _out(HYBRID, code, rows, [k for k in live if k != primary],
                    "MODERATE", why, corp, primary=primary)

    if len(live) == 1:
        kind = live[0]
        row = _material(rows, kind)
        if row.get("share_pct") is not None:
            why.append(f"{CLASS_LABEL[kind]} is the only material business: "
                       f"{row['share_pct']:,.1f}% by {row['measure']}.")
        else:
            why.append(f"{CLASS_LABEL[kind]} is the only material business, "
                       f"from {row['measure']}.")
        if kind in (ASSET_MANAGER, EXCHANGE) and not corp.get("ok"):
            why.append(corp.get("reason") or "")
            return _out(UNSUPPORTED, code, rows, [], "LOW", why, corp)
        return _out(kind, code, rows, [], "MODERATE", why, corp)

    # 7. Nothing material anywhere. If the accounts are an operating
    #    company's, the ordinary model is the right one — Evercore earns
    #    advisory fees, holds no customer money and turns a third of its
    #    revenue into cash.
    if corp.get("ok"):
        why.append("Its filings show no customer balances, no deposits and "
                   "no policy reserves of any size.")
        why.append(corp.get("basis") or "")
        return _out(STANDARD, code, rows, [], "MODERATE", why, corp)
    why.append(corp.get("reason") or "")
    why.append("Its industry code sits among brokers, exchanges and asset "
               "managers, and its own filings do not say which of those it "
               "is.")
    return _out(UNSUPPORTED, code, rows, [], "LOW", why, corp)


def _out(kind, code, rows, secondary, confidence, why, corp=None,
         primary=None) -> dict:
    primary = primary or (kind if kind != HYBRID else None)
    model = CLASS_MODEL.get(primary if kind == HYBRID else kind, "")
    return {
        "business_class": kind,
        "label": CLASS_LABEL[kind],
        "model": model,
        "primary": primary,
        "secondary": list(secondary or []),
        "sic": code,
        "confidence": confidence,
        "why": [w for w in why if w],
        "exposures": rows,
        "corporate_accounts": corp or {},
        "note": CLASS_NOTE.get(kind, ""),
        "version": ROUTING_VERSION,
    }
