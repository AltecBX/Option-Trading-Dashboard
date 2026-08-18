"""reit_model.py — what a property trust is worth, on its own terms (Phase 4).

A property trust's reported earnings are close to meaningless as a measure
of what it produces. Buildings are depreciated on a schedule that has
nothing to do with whether they are worth more or less than last year, and
that charge runs straight through the income statement — so a trust whose
properties are appreciating can report shrinking earnings for a decade.
Realty Income earned $1.27 billion last year and generated $4.0 billion of
funds from operations. Valuing it on the first number would be arithmetic
performed on the wrong quantity.

FUNDS FROM OPERATIONS, AND WHERE IT COMES FROM

The industry answer is funds from operations, defined by NAREIT as

    net income available to common
      + depreciation and amortisation of real estate
      − gains on sales of property
      + impairments of property

NOT ONE of the twenty large US property trusts measured tags a funds-from-
operations concept in its machine-readable filings. Every headline FFO
figure published by these trusts lives only in the prose of a press release.
So this module RECONSTRUCTS it from the four filed components above, and
says so everywhere it appears. It is never presented as the trust's own
published figure, because it is not one — and a trust's own headline is
usually "core" or "adjusted" funds from operations, which strips out more
still and is a different number again.

The reconstruction is not uniformly clean. Gains on property sales are
tagged sporadically: Realty Income tags them every quarter, Prologis stopped
in 2019, Simon Property Group does not tag them at all. When the gain
component is missing, the reconstruction cannot remove gains, and in a year
with disposals it therefore reads HIGH. That is stated on screen as part of
the figure rather than hidden.

Two things keep this honest enough to value on. First, the completeness of
the reconstruction is reported, and an incomplete one is ranked below a
complete one as a valuation method. Second — and this is the real defence —
the valuation compares today's price-to-funds-from-operations against this
trust's OWN history and against its peers, with all three sides computed by
this same reconstruction. A systematic bias in the reconstruction largely
cancels in a comparison that applies it to both sides.

ADJUSTED FUNDS FROM OPERATIONS IS REFUSED

Adjusted funds from operations subtracts recurring maintenance capital
spending, straight-line rent and the amortisation of above- and below-market
leases. No trust measured separates recurring maintenance capital spending
from development spending in machine-readable form, and only thirteen of
twenty tag a straight-line rent adjustment at all. There is no honest way to
compute it, so it is not computed. The specification asked for it "where
reliably available"; it is not, anywhere.

PROPERTY TYPE

The SEC gives every property trust the same industry code, so a data-centre
trust and a shopping-centre trust are indistinguishable by code. They do not
trade at the same multiple and never have. The type is therefore read from
the trust's own annual report — see `fundamentals.property_type` — and peer
comparison prefers trusts of the same type when enough of them exist.
"""
from __future__ import annotations

import math

import fair_value as fv

REIT_MODEL_VERSION = "invest-reit-1.0.0"

BUSINESS_TYPE = "REIT"

DEFAULTS = {
    # A property-type peer set needs enough members to be a distribution;
    # below this the comparison widens to all property trusts and says so.
    "reit_min_property_type_peers": 5,
    # A payout above this share of funds from operations is called out. It
    # is a flag for attention, not a verdict: trusts are legally obliged to
    # distribute most of their taxable income, so high payouts are normal
    # and it is the direction and the margin that matter.
    "reit_high_payout_pct": 90.0,
    # The reconstruction is called complete when the gain and impairment
    # components are tagged for at least this many of the trailing four
    # quarters.
    "reit_min_adjustment_quarters": 4,
}

# The reconstruction's own confidence ranks. A complete one outranks the
# peer method; an incomplete one does not.
_RANK_SELF_COMPLETE = 3.4
_RANK_SELF_PARTIAL = 1.8

# Year-over-year move in reconstructed funds from operations above which an
# incomplete reconstruction is called out as probably carrying a one-off gain.
_SUSPECT_GROWTH_PCT = 25.0


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


# ── funds from operations ───────────────────────────────────────────────────

NAREIT_FORMULA = ("Net income available to common shareholders, plus "
                  "depreciation and amortisation of property, less gains on "
                  "property sales, plus impairments of property")


def _quarters(fund, facts, name: str, as_of: str | None = None) -> list:
    gaap = facts.get("facts", {}).get("us-gaap") or {}
    got = fund.pick_concept(gaap, name)
    if not got:
        return []
    qs = got[2]
    if as_of:
        qs = [q for q in qs if q["end"] <= as_of]
    return qs


def _concept_of(fund, facts, name: str) -> str | None:
    gaap = facts.get("facts", {}).get("us-gaap") or {}
    got = fund.pick_concept(gaap, name)
    return got[0] if got else None


def _window(quarters: list, ends: list) -> tuple[float, int]:
    """Sum of a lumpy component over the four period ends that make up the
    trailing twelve months, and how many of them were actually tagged.

    A quarter with no tag contributes nothing and is COUNTED as missing.
    That distinction is the whole point: "the trust sold nothing" and "the
    trust did not tag what it sold" produce the same zero and mean opposite
    things.
    """
    by_end = {q["end"]: _num(q["val"]) for q in quarters or []}
    total, found = 0.0, 0
    for e in ends:
        v = by_end.get(e)
        if v is not None:
            total += v
            found += 1
    return total, found


def funds_from_operations(fund, facts, as_of: str | None = None,
                          cfg=None) -> dict:
    """Reconstructed trailing-twelve-month funds from operations."""
    cfg = cfg or {}
    ni_q = _quarters(fund, facts, "net_income_common", as_of)
    if len(ni_q) < 4:
        ni_q = _quarters(fund, facts, "net_income", as_of)
        ni_concept = _concept_of(fund, facts, "net_income")
    else:
        ni_concept = _concept_of(fund, facts, "net_income_common")
    if len(ni_q) < 4:
        return _na("Fewer than four quarters of reported income are on file, "
                   "so a trailing-twelve-month figure cannot be built.")
    dep_q = _quarters(fund, facts, "real_estate_depreciation", as_of)
    if len(dep_q) < 4:
        return _na("This trust does not report four contiguous quarters of "
                   "depreciation and amortisation, which is the single "
                   "largest add-back in the definition. Without it, funds "
                   "from operations cannot be reconstructed.")

    ends = [q["end"] for q in ni_q[-4:]]
    ni = sum(_num(q["val"]) or 0.0 for q in ni_q[-4:])
    dep, dep_found = _window(dep_q, ends)
    if dep_found < 4:
        return _na(f"Depreciation and amortisation is tagged for only "
                   f"{dep_found} of the four quarters in the trailing year, "
                   f"so the add-back would cover part of a year against a "
                   f"full year of income.")
    gain, gain_found = _window(
        _quarters(fund, facts, "gain_on_sale_real_estate", as_of), ends)
    imp, imp_found = _window(
        _quarters(fund, facts, "real_estate_impairment", as_of), ends)

    need = int(cfg_get(cfg, "reit_min_adjustment_quarters"))
    complete = gain_found >= need and imp_found >= need
    value = ni + dep - gain + imp

    if complete:
        caveat = ""
    else:
        missing = []
        if gain_found < need:
            missing.append(f"gains on property sales ({gain_found} of 4 "
                           f"quarters tagged)")
        if imp_found < need:
            missing.append(f"impairments ({imp_found} of 4 quarters tagged)")
        caveat = (
            "This reconstruction is INCOMPLETE: " + " and ".join(missing) +
            ". Gains that were not tagged cannot be removed, so in a year "
            "with property sales this figure reads high. It is still used to "
            "compare against this trust's own history and its peers, because "
            "all three sides are computed the same way, but it is ranked "
            "below a complete reconstruction as a valuation method.")

    return _ok(value,
               NAREIT_FORMULA + ", reconstructed from the filed components. "
               "This is NOT the trust's own published figure: no property "
               "trust tags funds from operations in machine-readable form, "
               "and the figure a trust headlines is usually a further-"
               "adjusted 'core' or 'adjusted' number.",
               complete=complete, caveat=caveat,
               period_end=ends[-1],
               filed=ni_q[-1].get("filed"),
               first_filed=ni_q[-1].get("first_filed"),
               components={"net_income_common": ni, "depreciation": dep,
                           "gains_on_sale": gain, "impairments": imp,
                           "gain_quarters_tagged": gain_found,
                           "impairment_quarters_tagged": imp_found,
                           "net_income_concept": ni_concept,
                           "depreciation_concept": _concept_of(
                               fund, facts, "real_estate_depreciation")},
               source="SEC EDGAR Company Facts (XBRL)")


def adjusted_ffo(fund, facts) -> dict:
    """Always refused, with the measurement that says why.

    Kept as a function rather than left out so the screen can state the
    refusal in the same place the figure would have gone.
    """
    return _na(
        "Adjusted funds from operations subtracts recurring maintenance "
        "capital spending, straight-line rent and lease-intangible "
        "amortisation. Across twenty large property trusts, none separates "
        "recurring maintenance spending from development spending in "
        "machine-readable form and only thirteen tag a straight-line rent "
        "adjustment at all. Estimating it would mean inventing the largest "
        "of its three deductions, so it is not reported.")


# ── point-in-time series ────────────────────────────────────────────────────

def point_in_time_series(fund, facts) -> dict:
    """Funds from operations per share and dividends per share, each dated
    at the filing that first stated them.

    Built by walking the reported quarters forward and rebuilding the
    reconstruction at every step from only what had been filed by then. No
    step ever reads a component that was filed later, which is what makes
    the resulting price-to-funds-from-operations history a real history
    rather than today's arithmetic applied to old prices.
    """
    ni_q = _quarters(fund, facts, "net_income_common") \
        or _quarters(fund, facts, "net_income")
    dep_q = _quarters(fund, facts, "real_estate_depreciation")
    gain_q = _quarters(fund, facts, "gain_on_sale_real_estate")
    imp_q = _quarters(fund, facts, "real_estate_impairment")
    shares = {p["period_end"]: p["value"]
              for p in fund.pit_series(facts, "diluted_shares")
              if p.get("value")}
    if len(ni_q) < 4 or len(dep_q) < 4:
        return {}

    out = []
    for i in range(3, len(ni_q)):
        window = ni_q[i - 3: i + 1]
        ends = [q["end"] for q in window]
        sh = shares.get(ends[-1])
        if not sh:
            continue
        dep, dep_found = _window(dep_q, ends)
        if dep_found < 4:
            continue
        gain, _g = _window(gain_q, ends)
        imp, _i = _window(imp_q, ends)
        ni = sum(_num(q["val"]) or 0.0 for q in window)
        out.append({"period_end": ends[-1],
                    "first_filed": window[-1].get("first_filed")
                    or window[-1].get("filed"),
                    "filed": window[-1].get("filed"),
                    "value": (ni + dep - gain + imp) / sh})
    return {"ffo_per_share": out}


# ── the whole picture ───────────────────────────────────────────────────────

def metrics(fund, facts, price=None, shares_outstanding=None,
            property_type=None, as_of: str | None = None, cfg=None) -> dict:
    """Every property-trust measure this app can honestly report."""
    cfg = cfg or {}
    out = {"available": False, "reason": "", "version": REIT_MODEL_VERSION,
           "property_type": property_type,
           "property_type_label": fund.PROPERTY_TYPE_LABELS.get(
               property_type or "", ""),
           "nareit_formula": NAREIT_FORMULA}
    price = _num(price)
    shares = _num(shares_outstanding)

    ffo = funds_from_operations(fund, facts, as_of, cfg)
    out["ffo"] = ffo
    out["affo"] = adjusted_ffo(fund, facts)

    dil = fund.metric(facts, "diluted_shares", as_of)
    out["diluted_shares"] = dil
    denom = _num(dil.get("value")) or shares
    out["ffo_per_share"] = (
        _ok(ffo["value"] / denom,
            "Reconstructed funds from operations over average diluted "
            "shares, which is the share count the industry definition uses")
        if (ffo.get("value") is not None and denom) else
        _na(ffo.get("reason") or "The diluted share count is not available."))

    fps = out["ffo_per_share"]["value"]
    out["price_to_ffo"] = (
        _ok(price / fps, "Share price over reconstructed funds from "
                         "operations per share")
        if (price and fps and fps > 0) else
        _na(out["ffo_per_share"].get("reason")
            or "Funds from operations per share is not positive."))
    out["price_to_affo"] = _na(out["affo"]["reason"])

    out["ffo_growth_pct"] = _ffo_growth(fund, facts, as_of, cfg)
    # A property portfolio's operating income does not move thirty percent
    # in a year. When the reconstruction says it did AND the reconstruction
    # is incomplete, the move is almost always an untagged one-off gain
    # sitting inside reported income rather than anything the buildings did.
    g = _num((out["ffo_growth_pct"] or {}).get("value"))
    out["reconstruction_warning"] = ""
    if g is not None and abs(g) > _SUSPECT_GROWTH_PCT and not ffo.get("complete"):
        out["reconstruction_warning"] = (
            f"The reconstruction moved {g:+.0f}% year over year. A property "
            f"portfolio's rents do not move that far, and this trust does "
            f"not tag its gains on property sales, so most of that swing is "
            f"very likely a one-off gain the reconstruction could not "
            f"remove. Treat the level of this figure as unreliable.")

    dps = fund.metric(facts, "dividends_per_share", as_of)
    out["dividends_per_share"] = dps
    d = _num(dps.get("value"))
    out["dividend_yield_pct"] = (
        _ok(d / price * 100.0,
            "Dividends declared over the last twelve months over the share "
            "price")
        if (d and price) else
        _na(dps.get("reason") or "The dividend is not available."))
    out["payout_of_ffo_pct"] = (
        _ok(d / fps * 100.0,
            "Dividends declared over reconstructed funds from operations "
            "per share")
        if (d and fps and fps > 0) else
        _na("Either the dividend or funds from operations per share is not "
            "available."))
    payout = out["payout_of_ffo_pct"]["value"]
    hi = float(cfg_get(cfg, "reit_high_payout_pct"))
    out["payout_flag"] = (
        {"level": "HIGH", "reason": (
            f"The distribution takes {payout:.0f}% of reconstructed funds "
            f"from operations, above the {hi:.0f}% marked for attention. "
            f"Property trusts must distribute most of their taxable income, "
            f"so a high payout is normal — what matters is whether it is "
            f"rising and how much room is left.")}
        if (payout is not None and payout > hi) else
        {"level": "NORMAL" if payout is not None else None,
         "reason": "" if payout is not None else
         out["payout_of_ffo_pct"].get("reason", "")})

    out["payout_of_affo_pct"] = _na(out["affo"]["reason"])

    # Balance sheet. Net debt to EBITDA for property is not tagged by any
    # trust measured, so what IS reportable is reported and the rest says so.
    nd = fund.net_debt(facts, as_of)
    out["net_debt"] = nd
    out["net_debt_to_ffo"] = (
        _ok(nd["value"] / ffo["value"],
            "Net borrowings over reconstructed funds from operations. This "
            "is NOT net debt to EBITDAre: no trust measured tags EBITDAre in "
            "machine-readable form, so the closest measure the filings "
            "support is reported under its own name.")
        if (nd.get("value") is not None and ffo.get("value")
            and ffo["value"] > 0) else
        _na(nd.get("reason") or ffo.get("reason") or "Not available."))

    out["occupancy"] = _na(
        "Occupancy is disclosed in the prose of every property trust's "
        "annual report and tagged in the machine-readable filings of none of "
        "the twenty measured, so there is nothing to read.")
    out["same_store_noi_growth_pct"] = _na(
        "Same-store net operating income is not tagged by any of the twenty "
        "property trusts measured. It appears only in press-release tables.")

    out["diluted_share_trend_pct"] = _share_trend(fund, facts, as_of)

    out["available"] = out["price_to_ffo"]["value"] is not None
    if not out["available"]:
        out["reason"] = out["price_to_ffo"].get("reason") or ffo.get("reason")
    return out


def _ffo_growth(fund, facts, as_of=None, cfg=None) -> dict:
    pts = (point_in_time_series(fund, facts) or {}).get("ffo_per_share") or []
    pts = [p for p in pts if as_of is None or p["period_end"] <= as_of]
    if len(pts) < 5:
        return _na(f"Only {len(pts)} trailing-twelve-month readings can be "
                   f"reconstructed — a year-over-year comparison needs five.")
    now, prior = _num(pts[-1]["value"]), _num(pts[-5]["value"])
    if now is None or prior is None or prior <= 0:
        return _na("The year-earlier figure is not positive.")
    return _ok((now / prior - 1.0) * 100.0,
               f"Reconstructed funds from operations per share for the "
               f"twelve months to {pts[-1]['period_end']} against the twelve "
               f"months to {pts[-5]['period_end']}")


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
               f"{pts[-5]['period_end']}. Property trusts routinely issue "
               f"shares to buy buildings, so a rising count is ordinary — "
               f"what matters is whether funds from operations per share "
               f"rose with it.")


# ── peers by property type ──────────────────────────────────────────────────

def property_type_peers(rows: list, property_type: str | None,
                        cfg=None) -> dict:
    """Narrow a peer group to trusts that own the same kind of building.

    A data-centre trust and a shopping-centre trust are both property trusts
    and are not comparable investments. Where there are enough matched
    trusts, the comparison is matched; where there are not, it widens to all
    property trusts and says which happened.
    """
    cfg = cfg or {}
    need = int(cfg_get(cfg, "reit_min_property_type_peers"))
    all_rows = [r for r in (rows or [])
                if _num(r.get("price_to_ffo")) and _num(r["price_to_ffo"]) > 0]
    matched = [r for r in all_rows if r.get("property_type") == property_type] \
        if property_type else []
    if property_type and len(matched) >= need:
        return {"rows": matched, "matched": True, "n": len(matched),
                "property_type": property_type,
                "reason": (f"Compared against {len(matched)} property trusts "
                           f"that own the same kind of building.")}
    if not property_type:
        why = ("This trust's annual report does not say clearly enough what "
               "kind of property it owns for a matched comparison")
    else:
        why = (f"Only {len(matched)} property trusts of the same type have a "
               f"usable multiple, fewer than the {need} needed")
    return {"rows": all_rows, "matched": False, "n": len(all_rows),
            "property_type": property_type,
            "reason": (f"{why}, so the comparison widens to all "
                       f"{len(all_rows)} property trusts in the group. "
                       f"Different kinds of property do not trade at the "
                       f"same multiple, so treat this comparison as looser "
                       f"than a matched one.")}


# ── the valuation methods ───────────────────────────────────────────────────

def confidence_cap(reit: dict) -> tuple[str | None, str]:
    """How far the fair-value confidence must be held down, and why.

    An incomplete reconstruction agrees with itself perfectly and is still
    working from a figure that reads high in a year with property sales.
    Method agreement cannot see that, so it is priced in here — and because
    confidence sets how far above the pessimistic case the valuation is
    credited, this lowers the buy zone rather than decorating it.
    """
    ffo = reit.get("ffo") or {}
    if ffo.get("value") is None or ffo.get("complete"):
        return None, ""
    return "LOW", (ffo.get("caveat") or "").split(".")[0] + \
        ". Confidence is held at LOW for that reason, which lowers the " \
        "price this screen is willing to pay."


def methods(reit: dict, history: dict, peers: dict | None = None,
            cfg=None) -> list:
    """The property-trust fair-value methods, each built or each refusing."""
    cfg = cfg or {}
    peers = peers or {}
    out = []
    raw = (history or {}).get("raw_values") or {}
    ffo = reit.get("ffo") or {}
    fps = (reit.get("ffo_per_share") or {}).get("value")
    complete = bool(ffo.get("complete"))

    basis = ("Reconstructed funds from operations per share, priced at this "
             "trust's own point-in-time history of price to funds from "
             "operations. Both sides of the comparison use the same "
             "reconstruction.")
    m = fv.method_multiple_history(
        "reit_self_pffo", "Its own price to funds from operations", basis,
        fps, (raw.get("price_to_ffo") or {}).get("5y")
        or (raw.get("price_to_ffo") or {}).get("all"),
        cfg=cfg, rank=_RANK_SELF_COMPLETE if complete else _RANK_SELF_PARTIAL,
        what="reconstructed funds from operations per share",
        detail={"measure": "price_to_ffo",
                "reconstruction_complete": complete,
                "note": ffo.get("caveat") or ""})
    out.append(m)

    out.append(fv.method_peer_multiple(
        "reit_peers_pffo",
        ("Comparable trusts of the same property type"
         if peers.get("matched") else "Comparable property trusts"),
        ("Reconstructed funds from operations per share at what comparable "
         "trusts cost per unit of their own. " + (peers.get("reason") or "")),
        fps, peers.get("multiples") or [],
        base_multiple=peers.get("base_multiple"),
        level=peers.get("level"), cfg=cfg,
        rank=2.4 if peers.get("matched") else 1.9,
        detail={"matched_property_type": bool(peers.get("matched")),
                "property_type": peers.get("property_type"),
                "note": peers.get("reason") or ""}))

    # Dividend-yield history: CONTEXT, never the base. A trust's distribution
    # is set by its board and by tax law, so a price implied by its own yield
    # history says what the market has paid for this income stream — which is
    # worth seeing beside a valuation and is not a valuation on its own.
    ctx = _dividend_yield_method(reit, raw, cfg)
    out.append(ctx)
    return fv.stamp(out, BUSINESS_TYPE)


def _dividend_yield_method(reit: dict, raw: dict, cfg) -> dict:
    dps = _num((reit.get("dividends_per_share") or {}).get("value"))
    ys = (raw.get("dividend_yield_pct") or {}).get("5y") \
        or (raw.get("dividend_yield_pct") or {}).get("all")
    basis = ("The distribution priced at the yields this trust's own units "
             "have historically offered. Shown as CONTEXT beside the "
             "valuation and deliberately excluded from setting it: a "
             "distribution is set by a board and by tax law, so its history "
             "describes what buyers have paid for the income rather than "
             "what the properties produce.")
    key, label = "reit_dividend_yield", "What its distribution has yielded"
    if dps is None or dps <= 0:
        return fv._method(key, label, basis,
                          reason=(reit.get("dividends_per_share") or {})
                          .get("reason") or "No distribution is on file.")
    ys = [y for y in (ys or []) if _num(y) and _num(y) > 0]
    if len(ys) < fv.MIN_MULTIPLE_OBSERVATIONS:
        return fv._method(key, label, basis, n=len(ys),
                          reason=(f"Only {len(ys)} daily observations of this "
                                  f"trust's own distribution yield are "
                                  f"available."))
    # A HIGH yield is a CHEAP price, so the pessimistic value is at the high
    # end — the same inversion the Phase 3 earnings-yield method uses.
    y_bear = fv.quantile(ys, float(fv.cfg_get(cfg, "self_bear_yield_percentile")))
    y_base = fv.quantile(ys, float(fv.cfg_get(cfg, "self_base_yield_percentile")))
    y_bull = fv.quantile(ys, float(fv.cfg_get(cfg, "self_bull_yield_percentile")))
    m = fv._method(key, label, basis,
                   bear=dps / (y_bear / 100.0) if y_bear else None,
                   base=dps / (y_base / 100.0) if y_base else None,
                   bull=dps / (y_bull / 100.0) if y_bull else None,
                   n=len(ys), rank=0.0,
                   detail={"yield_bear_pct": y_bear, "yield_base_pct": y_base,
                           "yield_bull_pct": y_bull,
                           "dividends_per_share": dps,
                           "context_only": True})
    # Rank zero and flagged: the combining code picks the highest-ranked
    # method for the base, so this one can widen the range but can never
    # become the answer while any other method stands.
    m["context_only"] = True
    return m
