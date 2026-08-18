"""fundamentals.py — reported company fundamentals from SEC EDGAR Company Facts.

sec_filings.py answers "what just happened" (8-Ks, offerings, Form 4s). This
module answers "what does this business actually earn", from the same source
and over the same transport: it imports sec_filings for the ticker→CIK map,
the request throttle and the SEC-mandated User-Agent, so there is exactly one
SEC client in the app.

Everything here comes from
    https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
which is the machine-readable version of every number a company has XBRL-
tagged in its own filings. No vendor, no key, no estimate — only what the
company reported and signed.

WHAT THE DATA ACTUALLY LOOKS LIKE (measured, not assumed — 17 tickers probed
before a line of this was written):

* Concepts are NOT consistent across filers. Robinhood's revenue lives in
  `Revenues` (72 datapoints); its `RevenueFromContractWithCustomer…` has 4
  datapoints and stops in 2021. A fixed priority list picks the dead one, so
  concepts are chosen by COVERAGE AND RECENCY instead.

* Income-statement facts are discrete quarters. CASH-FLOW facts are
  cumulative year-to-date: Q1 is 3 months, Q2 is 6, Q3 is 9, FY is 12. Naive
  90-day filtering finds only Q1 and free cash flow comes out empty for
  almost every company. `discrete_quarters` differences them.

* The same period is reported many times as later filings restate it (and
  re-express it after a stock split). The latest `filed` wins, which is also
  what makes per-share history split-consistent with split-adjusted prices.

* Retail filers use 52/53-week fiscal calendars: Costco's Q3 year-to-date is
  252 days, not 273. Quarter classification is proportional, not a fixed
  window, or Costco returns nothing.

WHAT THIS MODULE REFUSES TO ANSWER (each verified against live EDGAR):

* IFRS / foreign private issuers (TSM, NVO) file under the `ifrs-full`
  taxonomy in TWD and DKK. The ADR-to-ordinary ratio and the FX rate are not
  in the filings, so a per-share number computed from them and a USD ADR
  price would be fiction. Returns N/A with the currency named.
* Annual-only filers (BABA files a 20-F, no 10-Qs) have no quarterly facts,
  so there is no trailing-twelve-month anything. N/A with the reason.
* Thin histories (Exxon's companyfacts currently holds six period-ends and no
  annual report at all) cannot form four contiguous quarters. N/A.
* Pre-revenue companies (Cingulate) have no revenue concept. N/A, not zero.

Nothing here ever returns 0 to mean "unknown".
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path

try:
    import sec_filings as _sec
except Exception:                                    # pragma: no cover
    _sec = None

SCHEMA_VERSION = "1.0"

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_FACTS_TTL = 12 * 3600.0        # companyfacts changes at most once a quarter
_MEM: dict = {}                 # SYMBOL -> (fetched_ts, facts dict)
_MEM_MAX = 24                   # each payload is 0.3–3 MB; keep the lid on
_LOCK = threading.RLock()
_DATA_DIR: Path | None = None

# A quarter is 91.31 days on average. Retail 52/53-week calendars stretch a
# "quarter" to 84 or 112 days and a three-quarter year-to-date to 252, so
# classification is proportional with a tolerance rather than a fixed window.
_Q_DAYS = 91.31
_Q_TOL = 26.0
_MAX_Q = 4


def configure(data_dir=None) -> None:
    """Give the facts cache somewhere to live. Optional: without it the
    module still works, just from memory and the network."""
    global _DATA_DIR
    if data_dir:
        _DATA_DIR = Path(data_dir) / "invest" / "facts"
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:                            # pragma: no cover
            _DATA_DIR = None
    else:
        _DATA_DIR = None


def available() -> bool:
    return _sec is not None and _sec.available()


# ── transport (borrowed wholesale from sec_filings) ─────────────────────────

def _fetch_facts(cik: int) -> dict:
    return _sec._fetch_json(_FACTS_URL.format(cik=cik))    # noqa: SLF001


def _cache_path(symbol: str) -> Path | None:
    if _DATA_DIR is None:
        return None
    safe = re.sub(r"[^A-Z0-9._-]", "", symbol.upper())
    return _DATA_DIR / f"{safe}.json" if safe else None


def _atomic_write(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(path)


def company_facts(symbol: str, max_age: float = _FACTS_TTL) -> dict | None:
    """Raw Company Facts for a ticker, memory- then disk-cached.

    Returns None when the ticker has no CIK (ETFs, indices, foreign tickers
    absent from the SEC map) or the fetch fails and nothing is cached.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    with _LOCK:
        hit = _MEM.get(sym)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]

    path = _cache_path(sym)
    disk = None
    if path is not None and path.exists():
        try:
            disk = json.loads(path.read_text())
            if time.time() - float(disk.get("_fetched_ts") or 0) < max_age:
                _remember(sym, disk)
                return disk
        except Exception:
            disk = None

    if not available():
        return disk
    cik = _sec.cik_for(sym)
    if not cik:
        return disk
    try:
        facts = _fetch_facts(int(cik))
    except Exception:
        return disk                                  # last good beats nothing
    facts["_fetched_ts"] = time.time()
    facts["_cik"] = int(cik)
    if path is not None:
        try:
            _atomic_write(path, facts)
        except Exception:                            # pragma: no cover
            pass
    _remember(sym, facts)
    return facts


def _remember(sym: str, facts: dict) -> None:
    with _LOCK:
        _MEM[sym] = (time.time(), facts)
        while len(_MEM) > _MEM_MAX:
            oldest = min(_MEM, key=lambda k: _MEM[k][0])
            _MEM.pop(oldest, None)


# ── fact plumbing ───────────────────────────────────────────────────────────

def _d(s: str) -> date:
    return date.fromisoformat(s)


def latest_filed(entry: dict, unit: str) -> list[dict]:
    """All duration facts for one concept+unit, with the LATEST FILED value
    winning for each (start, end) period.

    This is what makes restatements and stock splits behave. A company that
    splits 10-for-1 re-expresses every prior per-share figure in its next
    filing; keeping the newest filing's version of each period yields a
    series already on today's share basis, which is the same basis
    split-adjusted price history uses.
    """
    rows = (entry or {}).get("units", {}).get(unit, [])
    best: dict = {}
    first: dict = {}
    for r in rows:
        start, end = r.get("start"), r.get("end")
        if not start or not end or r.get("val") is None:
            continue
        key = (start, end)
        prev = best.get(key)
        if prev is None or (r.get("filed") or "") > (prev.get("filed") or ""):
            best[key] = r
        f = r.get("filed") or ""
        if f and (key not in first or f < first[key]):
            first[key] = f
    out = []
    for (start, end), r in best.items():
        try:
            dur = (_d(end) - _d(start)).days
        except ValueError:
            continue
        out.append({"start": start, "end": end, "dur": dur,
                    "val": float(r["val"]), "form": r.get("form"),
                    # `filed` is the LATEST filing that stated this period —
                    # the restated, split-adjusted version, which is the
                    # value to show. `first_filed` is when the period first
                    # became public, which is where it belongs on a chart.
                    # Every annual report repeats two years of comparatives,
                    # so without this split a quarter from 2021 would be
                    # plotted at the 2023 filing that mentioned it again.
                    "filed": r.get("filed"),
                    "first_filed": first.get((start, end)) or r.get("filed"),
                    "fy": r.get("fy"), "fp": r.get("fp")})
    out.sort(key=lambda r: (r["end"], r["dur"]))
    return out


def quarters_spanned(dur_days: float) -> int | None:
    """How many fiscal quarters a reporting period covers, or None if it is
    not a whole number of quarters (monthly stubs, transition periods)."""
    if dur_days <= 0:
        return None
    n = round(dur_days / _Q_DAYS)
    if 1 <= n <= _MAX_Q and abs(dur_days - n * _Q_DAYS) <= _Q_TOL:
        return int(n)
    return None


def discrete_quarters(rows: list[dict], aggregate: str = "sum") -> list[dict]:
    """Reduce every reported period to a single fiscal quarter.

    Income-statement concepts already report discrete quarters, so the
    1-quarter facts are used as they are. Cash-flow concepts report
    cumulative year-to-date, so an n-quarter fact minus the (n-1)-quarter
    fact sharing its start date is the quarter that was added. The same
    subtraction recovers the fourth quarter of any company that reports it
    only inside the annual figure.

    Differencing is arithmetic on flows and is switched off for `mean`
    metrics, where a six-month weighted-average share count minus a
    three-month one is not a quarter of anything.
    """
    by_start: dict = {}
    for r in rows:
        n = quarters_spanned(r["dur"])
        if n:
            slot = by_start.setdefault(r["start"], {})
            # A duplicated span keeps the more recently filed row.
            if n not in slot or (r.get("filed") or "") > (slot[n].get("filed") or ""):
                slot[n] = r

    out: dict = {}
    for _start, by_n in by_start.items():
        for n in sorted(by_n):
            r = by_n[n]
            if n == 1:
                out.setdefault(r["end"], {**r, "derived": False})
                continue
            if aggregate != "sum":
                continue
            prev = by_n.get(n - 1)
            if prev is None:
                continue                             # gap: cannot difference
            out.setdefault(r["end"], {
                "start": prev["end"], "end": r["end"],
                "dur": (_d(r["end"]) - _d(prev["end"])).days,
                "val": r["val"] - prev["val"], "form": r.get("form"),
                "filed": r.get("filed"),
                "first_filed": max((r.get("first_filed") or ""),
                                   (prev.get("first_filed") or "")) or None,
                "fy": r.get("fy"), "fp": r.get("fp"),
                "derived": True})
    return sorted(out.values(), key=lambda r: r["end"])


def ttm(quarters: list[dict], as_of: str | None = None,
        aggregate: str = "sum") -> dict | None:
    """Trailing twelve months = the last four CONTIGUOUS quarters.

    The contiguity test is the point. A company that skipped a quarter in the
    dataset (Exxon is missing 2025-09-30 today) would otherwise silently
    produce a "twelve month" figure spanning fifteen months.

    `aggregate` is "sum" for flows (revenue, earnings, cash) and "mean" for
    stock-like quantities. A weighted-average diluted share count is NOT
    additive: adding Microsoft's four quarterly counts would claim it has
    thirty billion shares outstanding instead of seven and a half.
    """
    qs = [q for q in quarters if as_of is None or q["end"] <= as_of]
    if len(qs) < 4:
        return None
    last4 = qs[-4:]
    span = (_d(last4[-1]["end"]) - _d(last4[0]["start"])).days
    if not (330 <= span <= 400):
        return None
    for a, b in zip(last4, last4[1:]):
        if abs((_d(b["start"]) - _d(a["end"])).days) > 7:
            return None
    total = sum(q["val"] for q in last4)
    return {"value": total / 4.0 if aggregate == "mean" else total,
            "period_end": last4[-1]["end"],
            "period_start": last4[0]["start"],
            "filed": max((q["filed"] or "") for q in last4) or None,
            # A twelve-month figure becomes computable when the LAST of its
            # four quarters is first published, not when an annual report
            # later restates the whole set.
            "first_filed": max((q.get("first_filed") or "") for q in last4) or None,
            "quarters": len(last4), "span_days": span,
            "aggregate": aggregate,
            "derived": any(q.get("derived") for q in last4)}


# ── concept selection ───────────────────────────────────────────────────────
#
# Ordered by preference, but preference only breaks ties: the concept with
# the most recent quarterly data wins, then the one with the longest history.
# Measured reason: Robinhood tags revenue under `Revenues`, and its
# `RevenueFromContractWithCustomerExcludingAssessedTax` series died in 2021.

CONCEPTS = {
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax",
                 "Revenues",
                 "RevenueFromContractWithCustomerIncludingAssessedTax",
                 "RevenuesNetOfInterestExpense",
                 "SalesRevenueNet", "SalesRevenueGoodsNet",
                 "SalesRevenueServicesNet"], "USD", "sum"),
    "net_income": (["NetIncomeLoss", "ProfitLoss",
                    "NetIncomeLossAvailableToCommonStockholdersBasic"], "USD", "sum"),
    "eps": (["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
             "IncomeLossFromContinuingOperationsPerDilutedShare"],
            "USD/shares", "sum"),
    "diluted_shares": (["WeightedAverageNumberOfDilutedSharesOutstanding",
                        "WeightedAverageNumberOfDilutedSharesOutstandingBasicAndDiluted",
                        "WeightedAverageNumberOfSharesOutstandingBasic"],
                       "shares", "mean"),
    "operating_cash_flow": (["NetCashProvidedByUsedInOperatingActivities",
                             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
                            "USD", "sum"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets",
               "PaymentsToAcquireOtherProductiveAssets",
               "PaymentsForCapitalImprovements"], "USD", "sum"),
}

# Human-readable basis shown next to every number in the UI.
BASIS = {
    "revenue": "GAAP trailing twelve months, as reported to the SEC",
    "net_income": "GAAP trailing twelve months, as reported to the SEC",
    "eps": "GAAP diluted, trailing twelve months, as reported to the SEC",
    "diluted_shares": "Average weighted-average diluted shares over the "
                      "trailing twelve months",
    "operating_cash_flow": "GAAP trailing twelve months, as reported to the SEC",
    "capex": "GAAP trailing twelve months, as reported to the SEC",
}


def pick_concept(gaap: dict, metric: str) -> tuple[str, str, list[dict]] | None:
    """(concept, unit, discrete quarterly series) for a metric, or None."""
    names, unit, agg = CONCEPTS[metric]
    best = None
    for rank, name in enumerate(names):
        entry = gaap.get(name)
        if not entry or unit not in (entry.get("units") or {}):
            continue
        qs = discrete_quarters(latest_filed(entry, unit), aggregate=agg)
        if not qs:
            continue
        score = (qs[-1]["end"], len(qs), -rank)
        if best is None or score > best[0]:
            best = (score, name, unit, qs)
    return (best[1], best[2], best[3]) if best else None


# ── filer eligibility ───────────────────────────────────────────────────────

def _currency_of(facts: dict) -> str | None:
    """Reporting currency of a non-US-GAAP filer, read off its own units."""
    for taxo in ("ifrs-full",):
        for entry in (facts.get("facts", {}).get(taxo) or {}).values():
            for unit in (entry.get("units") or {}):
                cur = unit.split("/")[0]
                if len(cur) == 3 and cur.isalpha():
                    return cur.upper()
    return None


def eligibility(facts: dict | None) -> dict:
    """Can this filer's numbers be used at all? {'ok': bool, 'reason': str}."""
    if not facts:
        return {"ok": False,
                "reason": "No SEC filer record for this symbol — funds, ETFs "
                          "and most foreign tickers do not file with EDGAR."}
    gaap = facts.get("facts", {}).get("us-gaap") or {}
    if not gaap:
        cur = _currency_of(facts)
        if cur:
            return {"ok": False,
                    "reason": f"Files under IFRS in {cur}. The ADR-to-ordinary "
                              f"share ratio and the exchange rate are not in the "
                              f"filings, so per-share figures cannot be lined up "
                              f"against a US dollar share price."}
        return {"ok": False,
                "reason": "This filer reports no US GAAP figures to the SEC."}
    return {"ok": True, "reason": ""}


# ── the public read ─────────────────────────────────────────────────────────

def _na(reason: str) -> dict:
    return {"value": None, "reason": reason}


def _annual_fact(gaap: dict, name: str, as_of: str | None) -> dict | None:
    """The most recent full-year fact for a metric, used as-is."""
    names, unit, _agg = CONCEPTS[name]
    best = None
    for concept in names:
        entry = gaap.get(concept)
        if not entry or unit not in (entry.get("units") or {}):
            continue
        for r in latest_filed(entry, unit):
            if quarters_spanned(r["dur"]) != 4:
                continue
            if as_of is not None and r["end"] > as_of:
                continue
            if best is None or r["end"] > best["end"]:
                best = r
    if not best:
        return None
    return {"value": best["val"], "period_end": best["end"],
            "period_start": best["start"], "filed": best.get("filed"),
            "quarters": 4, "span_days": best["dur"], "aggregate": "annual",
            "derived": False}


def metric(facts: dict, name: str, as_of: str | None = None) -> dict:
    """One trailing-twelve-month metric with its full provenance, or an
    explicit N/A carrying the reason it could not be built."""
    gaap = facts.get("facts", {}).get("us-gaap") or {}
    agg = CONCEPTS[name][2]
    got = pick_concept(gaap, name)
    if not got and agg == "mean":
        # A filer that states a weighted-average share count only for the
        # full year has no quarters to pick from, but the twelve-month
        # average is right there and needs no reconstruction.
        annual = _annual_fact(gaap, name, as_of)
        if annual:
            return _metric_out(name, CONCEPTS[name][0][0], CONCEPTS[name][1],
                               annual)
    if not got:
        if any(c in gaap for c in CONCEPTS[name][0]):
            # The concept is there, it just never appears over a quarter.
            # Foreign private issuers on Form 20-F (Alibaba) report once a
            # year, and a trailing-twelve-month figure needs quarters.
            return _na("This company reports that figure only once a year, "
                       "so there are no quarterly numbers to build a "
                       "trailing-twelve-month figure from.")
        return _na("This company does not report that figure to the SEC.")
    concept, unit, qs = got
    t = ttm(qs, as_of=as_of, aggregate=agg)
    if not t and agg == "mean":
        # A filer that reports a weighted-average share count only for the
        # full year states the twelve-month average outright — no quarters
        # needed, and no differencing that would corrupt an average.
        t = _annual_fact(gaap, name, as_of)
    if not t:
        have = len([q for q in qs if as_of is None or q["end"] <= as_of])
        if have < 4:
            return _na(f"Only {have} quarter{'s' if have != 1 else ''} of "
                       f"{concept} are on file — a trailing-twelve-month "
                       f"figure needs four.")
        return _na("The four most recent quarters on file do not form a "
                   "continuous year (a period is missing or restated), so no "
                   "trailing-twelve-month figure can be built from them.")
    return _metric_out(name, concept, unit, t)


def _metric_out(name: str, concept: str, unit: str, t: dict) -> dict:
    return {"value": t["value"], "concept": concept, "unit": unit,
            "period_end": t["period_end"], "period_start": t["period_start"],
            "filed": t["filed"], "quarters_used": t["quarters"],
            "derived": t["derived"], "basis": BASIS[name],
            "source": "SEC EDGAR Company Facts (XBRL)",
            "reason": ""}


def ttm_series(facts: dict, name: str) -> list[dict]:
    """Every trailing-twelve-month point this company's history supports,
    each stamped with the date the market first learned it (`filed`).

    Plotting at `filed` rather than `period_end` is what keeps a chart free
    of lookahead: a quarter that ended in March was not knowable until it was
    filed in May.
    """
    gaap = facts.get("facts", {}).get("us-gaap") or {}
    got = pick_concept(gaap, name)
    if not got:
        return []
    _concept, _unit, qs = got
    agg = CONCEPTS[name][2]
    out = []
    for i in range(3, len(qs)):
        t = ttm(qs[: i + 1], aggregate=agg)
        if t and t["period_end"] == qs[i]["end"]:
            out.append({"period_end": t["period_end"], "filed": t["filed"],
                        "first_filed": t.get("first_filed"),
                        "value": t["value"]})
    return out


def quarterly(facts: dict, name: str) -> list[dict]:
    """Discrete quarterly series for a metric (used by the EPS bridge)."""
    gaap = facts.get("facts", {}).get("us-gaap") or {}
    got = pick_concept(gaap, name)
    return got[2] if got else []


def shares_outstanding(facts: dict) -> dict:
    """Shares currently outstanding, for market cap.

    Preferred: the cover-page count (`dei:EntityCommonStockSharesOutstanding`),
    which is a point-in-time headcount dated within days of the filing.
    Companies with multiple share classes (Robinhood, Shopify) report it only
    per class, and Company Facts drops the per-class breakdown, so those fall
    back to the weighted-average diluted count with the basis said out loud.
    """
    dei = facts.get("facts", {}).get("dei") or {}
    entry = dei.get("EntityCommonStockSharesOutstanding")
    if entry:
        rows = entry.get("units", {}).get("shares") or []
        rows = [r for r in rows if r.get("val")]
        if rows:
            r = max(rows, key=lambda x: (x.get("end") or "", x.get("filed") or ""))
            return {"value": float(r["val"]), "as_of": r.get("end"),
                    "filed": r.get("filed"),
                    "basis": "Shares outstanding from the latest filing cover page",
                    "source": "SEC EDGAR Company Facts (dei)", "reason": ""}
    m = metric(facts, "diluted_shares")
    if m["value"]:
        return {"value": m["value"], "as_of": m["period_end"],
                "filed": m.get("filed"),
                "basis": "Average diluted shares over the trailing twelve "
                         "months (this filer reports its cover-page share "
                         "count per share class, which SEC Company Facts "
                         "does not carry)",
                "source": "SEC EDGAR Company Facts (XBRL)", "reason": ""}
    return _na("No share count on file.")


def fundamentals(symbol: str, as_of: str | None = None) -> dict:
    """Everything Phase 1 needs from the filings, for one ticker."""
    facts = company_facts(symbol)
    elig = eligibility(facts)
    out = {"symbol": (symbol or "").upper(), "ok": elig["ok"],
           "reason": elig["reason"], "schema": SCHEMA_VERSION,
           "source": "SEC EDGAR Company Facts (XBRL)",
           "entity_name": (facts or {}).get("entityName") or "",
           "cik": (facts or {}).get("_cik"),
           "fetched_ts": (facts or {}).get("_fetched_ts"),
           "metrics": {}}
    if not elig["ok"]:
        for name in CONCEPTS:
            out["metrics"][name] = _na(elig["reason"])
        out["shares_outstanding"] = _na(elig["reason"])
        return out

    for name in CONCEPTS:
        out["metrics"][name] = metric(facts, name, as_of=as_of)

    # Prior-year TTM, for year-over-year. Same four-contiguous-quarter rule,
    # measured one year back from the latest period end.
    for name in ("revenue", "eps", "net_income"):
        cur = out["metrics"][name]
        if not cur.get("value") and cur.get("value") != 0:
            out["metrics"][name]["prior"] = None
            continue
        cutoff = _minus_a_year(cur["period_end"])
        prior = metric(facts, name, as_of=cutoff)
        out["metrics"][name]["prior"] = (
            {"value": prior["value"], "period_end": prior["period_end"]}
            if prior.get("value") is not None else None)

    out["shares_outstanding"] = shares_outstanding(facts)
    ocf = out["metrics"]["operating_cash_flow"]
    cap = out["metrics"]["capex"]
    if ocf.get("value") is not None and cap.get("value") is not None:
        out["free_cash_flow"] = {
            "value": ocf["value"] - cap["value"],
            "period_end": ocf["period_end"], "filed": ocf.get("filed"),
            "basis": "Operating cash flow minus capital expenditure, "
                     "trailing twelve months, both as reported",
            "source": "SEC EDGAR Company Facts (XBRL)", "reason": ""}
    else:
        missing = "capital expenditure" if ocf.get("value") is not None else "operating cash flow"
        out["free_cash_flow"] = _na(
            f"Free cash flow needs both halves and this filer's {missing} is "
            f"not available. " + (cap.get("reason") or ocf.get("reason") or ""))
    return out


def _minus_a_year(iso: str) -> str:
    d = _d(iso)
    try:
        return (d.replace(year=d.year - 1) + timedelta(days=3)).isoformat()
    except ValueError:                               # Feb 29
        return (d.replace(year=d.year - 1, day=28) + timedelta(days=3)).isoformat()


# ── business description + moat tags (no runtime LLM) ───────────────────────
#
# The description comes from Item 1 "Business" of the latest 10-K, read once
# and cached by accession number forever, because a filing never changes.

# The `\s*` after the first letter is not decoration. Microsoft sets the
# first letter of a chapter heading as a drop cap in its own element, so the
# flattened text reads "ITEM 1. B USINESS" and a plain \bbusiness\b never
# matches it.
_BUSINESS_HDR = re.compile(r"item\s*1\s*[.\-—:)]*\s*b\s*usiness\b", re.I)
_RISK_HDR = re.compile(r"item\s*1a\s*[.\-—:)]*\s*r\s*isk\s+factors", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+")

# Modern 10-Ks are inline XBRL: the file opens with a hidden <ix:header>
# block holding thousands of machine-readable facts. It carries no tags of
# its own once tags are stripped, so Plug Power's description came out as
# "http://fasb.org/us-gaap/2025#AssetImpairmentCharges" repeated. Cut it out
# before the document is flattened to text.
_IX_HEADER = re.compile(rb"(?is)<ix:header.*?</ix:header>")
_HIDDEN_DIV = re.compile(rb"(?is)<div[^>]*display:\s*none[^>]*>.*?</div>")

_PROFILE_CACHE: dict = {}


def _profile_path(symbol: str) -> Path | None:
    if _DATA_DIR is None:
        return None
    safe = re.sub(r"[^A-Z0-9._-]", "", symbol.upper())
    return _DATA_DIR.parent / "profiles" / f"{safe}.json" if safe else None


def business_description(symbol: str, max_chars: int = 420) -> dict | None:
    """Short plain-English description of what the company does, quoted from
    its own 10-K. Returns None when no 10-K can be read."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    if sym in _PROFILE_CACHE:
        return _PROFILE_CACHE[sym]
    path = _profile_path(sym)
    if path is not None and path.exists():
        try:
            hit = json.loads(path.read_text())
            _PROFILE_CACHE[sym] = hit
            return hit
        except Exception:
            pass
    if not available():
        return None
    try:
        rows = _sec.filings(sym)
    except Exception:
        return None
    tenk = [r for r in rows if (r.get("form") or "").upper() in ("10-K", "10-K/A")]
    if not tenk:
        return None
    row = tenk[0]
    try:
        raw = _sec._fetch(row["url"], limit=4_000_000)      # noqa: SLF001
    except Exception:
        return None
    text = _sec._plain(_HIDDEN_DIV.sub(b" ", _IX_HEADER.sub(b" ", raw)))  # noqa: SLF001
    body = _item1_body(text)
    if not body:
        return None
    out = _trim_sentences(body, max_chars)
    if not out:
        return None
    profile = {"symbol": sym, "description": out,
               "moat_tags": moat_tags(body),
               "as_of": row.get("date"), "accession": row.get("accession"),
               "url": row.get("url"), "form": row.get("form"),
               "source": "SEC EDGAR — Item 1, Business, of the latest annual report",
               "fallback": False}
    _PROFILE_CACHE[sym] = profile
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, profile)
        except Exception:                            # pragma: no cover
            pass
    return profile


def _item1_body(text: str) -> str:
    """The text of Item 1, Business — not its table-of-contents entry.

    "Item 1. Business" appears at least twice in every annual report: once in
    the contents list, where the next words are a page number, and once at
    the chapter itself. The chapter is the last heading that has real prose
    between it and the Risk Factors heading that follows it.
    """
    starts = [m.end() for m in _BUSINESS_HDR.finditer(text)]
    if not starts:
        return ""
    best = ""
    for s in starts:
        nxt = _RISK_HDR.search(text, s)
        body = text[s: nxt.start() if nxt else min(len(text), s + 40_000)]
        body = re.sub(r"^[\s​‌‍﻿.:;\-—]*\d{0,4}[\s​‌‍﻿]*", "", body)
        body = _strip_lead_in(body)
        # A contents entry is a page number followed by the next heading;
        # a chapter is thousands of characters of sentences.
        if len(body) > len(best) and body.count(".") >= 3:
            best = body
    return best[:40_000].strip()


# Section headings and the "what the pronouns mean" paragraph that opens
# many Item 1s. Neither says anything about the business.
_LEAD_WORDS = re.compile(
    r"^[\s​‌‍﻿]*"
    r"(general|overview|company overview|business overview|background|"
    r"introduction|company background)\b[.\-—:]*[\s​‌‍﻿]*",
    re.I)
_LEAD_BOILERPLATE = re.compile(
    r"^(unless the context|in this annual report|references (in|to)|"
    r"as used (in|herein)|all references)", re.I)


_LEAD_CAPS = re.compile(r"^[\s​‌‍﻿]*(THE COMPANY|OUR COMPANY|OUR BUSINESS|"
                        r"THE BUSINESS|COMPANY OVERVIEW)\b[.\-—:]*[\s​‌‍﻿]*")


def _strip_lead_in(body: str) -> str:
    """Drop heading words and reference boilerplate from the front."""
    # An ALL-CAPS lead is a section heading ("THE COMPANY"); the same words
    # in sentence case are the sentence itself ("The Company designs …").
    body = _LEAD_CAPS.sub("", body)
    for _ in range(3):
        new = _LEAD_WORDS.sub("", body)
        if new == body:
            break
        body = new
    parts = _SENT.split(body.strip(), maxsplit=3)
    while parts and _LEAD_BOILERPLATE.match(parts[0].strip()):
        parts = parts[1:]
        body = " ".join(parts)
        parts = _SENT.split(body.strip(), maxsplit=3)
    # Again at the end: dropping the boilerplate sentence can expose the
    # heading that sat behind it.
    return _LEAD_WORDS.sub("", _LEAD_CAPS.sub("", body)).strip()


def _trim_sentences(text: str, max_chars: int) -> str:
    # Zero-width joiners survive tag stripping and render as stray gaps.
    text = re.sub(r"[​‌‍﻿]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    out = ""
    for s in _SENT.split(text):
        if len(out) + len(s) + 1 > max_chars:
            break
        out = f"{out} {s}".strip()
    return out or text[:max_chars].rsplit(" ", 1)[0]


# Moat tags are keyword rules over the company's own words. They are tags,
# never scores: a 1-to-10 "moat score" would imply a precision that reading
# Item 1 for keywords cannot possibly support.
_MOAT_RULES = [
    ("Patents & intellectual property",
     r"\b(patent|patents|intellectual property|proprietary technology|trade secret)"),
    ("Regulatory approval barrier",
     r"\b(FDA[- ]approved|regulatory approval|licensed by|BLA|NDA approval|"
     r"clinical trials required|regulated utility|certificate of need)"),
    ("Recurring & subscription revenue",
     r"\b(subscription|recurring revenue|renewal rate|annual recurring|"
     r"multi[- ]year contracts|maintenance contracts)"),
    ("Network effects",
     r"\b(network effect|two[- ]sided|marketplace connects|more (buyers|sellers|users) attract)"),
    ("Switching costs",
     r"\b(switching cost|deeply embedded|mission[- ]critical|integrated into (our|their) customers)"),
    ("Scale & cost advantage",
     r"\b(economies of scale|lowest[- ]cost producer|cost advantage|"
     r"purchasing power|scale advantage)"),
    ("Brand strength",
     r"\b(brand recognition|brand loyalty|iconic brand|trusted brand)"),
    ("Distribution & installed base",
     r"\b(installed base|distribution network|exclusive distribution|"
     r"shelf space|dealer network)"),
]
_MOAT_MAX = 3


def moat_tags(text: str) -> list[str]:
    """Up to three durable-advantage tags a company claims in its own 10-K.

    Ranked by how often the language appears, so a company that mentions
    patents forty times outranks one that mentions them once in a boilerplate
    risk sentence.
    """
    if not text:
        return []
    scored = []
    for label, pattern in _MOAT_RULES:
        n = len(re.findall(pattern, text, re.I))
        if n:
            scored.append((n, label))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [label for _n, label in scored[:_MOAT_MAX]]
