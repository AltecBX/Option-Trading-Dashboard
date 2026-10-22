"""Shares outstanding over time, from the companies' own SEC filings (v5.45).

Step 1 of charting market capitalization: the share count each day's price is
multiplied by. Every point is a number a company filed, never an estimate:

* The cover page of each 10-Q and 10-K states the shares outstanding "as of"
  a recent date (dei:EntityCommonStockSharesOutstanding). SEC's Company
  Facts summary carries it for a company with one class of stock.
* A company with several classes (Alphabet A/B/C, Meta A/B) tags the count
  once per class, and Company Facts drops those. They are read from each
  filing's XBRL instance instead, once: filings never change, so what a
  filing said is kept for good, including "nothing here".
* Counts are filed in the share basis of their date. Prices are
  split-adjusted to today's basis, so every count is put on today's basis
  with the splits that happened after it. Apple's 2020 four-for-one split,
  NVIDIA's 2024 ten-for-one: the market cap does not step on the split day.

Between two reports the chart uses the last reported count and says how old
it is. Nothing is interpolated: a company that issues shares every week
(Strategy's at-the-market sales) is behind by what it issued since its last
report, and the check against Yahoo's market cap today says so in words.

Not supported, with the reason shown: ETFs and funds (no SEC share count),
foreign filers (yearly counts of home-market shares; the ADR ratio is not in
SEC data), and anything before a company's first electronic count
(2009-2011 for most).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

MAX_AGE_DAYS = 200          # a count older than this is not today's (a missed filing): a gap
NEAR_SPLIT_DAYS = 10        # a count dated this close before a split may already be on the new basis
YAHOO_WARN_PCT = 3.0        # today's count against Yahoo's implied count: warn beyond this
BIG_MOVE_PCT = 50.0         # report-to-report change worth naming
RESULT_TTL_S = 12 * 3600    # Company Facts changes at most once a quarter
PARTIAL_TTL_S = 600         # some filings could not be read: try again soon
INSTANCE_READ_BYTES = 1_500_000   # cover facts sit at the top of an instance file
SCHEMA = 2                  # v5.46 added price_check: a history kept from before is rebuilt

FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT", "10-KT/A"}
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,11}")
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
CLASS_AXIS = "StatementClassOfStockAxis"

# Share classes that do not trade one-for-one with the ticker's own class,
# as how many of the ticker's shares one share of the other class is worth,
# from the date each ratio took effect (newest first). Only economics fixed
# in the charter belong here. Berkshire: one Class A share converts into
# 1,500 Class B shares since Class B alone split 50-for-1 on 2010-01-21, and
# into 30 before it, in the B shares of the day; the split itself then
# carries the old B count onto today's basis (Codex, #430).
_BRK_B = [("2010-01-21", {"CommonClassA": 1500.0}), ("0000-01-01", {"CommonClassA": 30.0})]
_BRK_A = [("2010-01-21", {"CommonClassB": 1.0 / 1500.0}), ("0000-01-01", {"CommonClassB": 1.0 / 30.0})]
CLASS_WEIGHTS: dict[str, list] = {"BRK-B": _BRK_B, "BRK.B": _BRK_B, "BRK-A": _BRK_A, "BRK.A": _BRK_A}

_LOCK = threading.RLock()
_DATA_DIR: Optional[Path] = None
_FACTS_FN: Optional[Callable[[str], Optional[dict]]] = None
_FETCH_FN: Optional[Callable[..., bytes]] = None
_SPLITS_FN: Optional[Callable[[str], Iterable]] = None
_QUOTE_FN: Optional[Callable[[str], Optional[Mapping]]] = None
_TODAY_FN: Optional[Callable[[], date]] = None
_PRICES_FN: Optional[Callable[[str], Iterable]] = None
_FILINGS_FN: Optional[Callable[[str], Iterable]] = None
_RESULTS: dict = {}          # SYMBOL -> (built_ts, result)
_clock = time.time           # every cache stamp and age; the tests pin it
_BUILDING: dict = {}         # SYMBOL -> threading.Event


def configure(data_dir=None, facts_fn=None, fetch_fn=None, splits_fn=None,
              quote_fn=None, today_fn=None, prices_fn=None, filings_fn=None) -> None:
    """facts_fn(symbol) -> SEC Company Facts (fundamentals.company_facts).
    fetch_fn(url, limit=None) -> bytes, polite to SEC (sec_filings._fetch).
    splits_fn(symbol) -> [(date_iso, ratio)], ratio 10 for ten-for-one,
    0.1 for one-for-ten. quote_fn(symbol) -> {price, market_cap} today
    (Yahoo), for the check. prices_fn(symbol) -> [(date_iso, close)], one
    close a month, split-adjusted and not dividend-adjusted (v5.46): what a
    chart's own prices are checked against before it multiplies them by
    these counts. filings_fn(symbol) -> [{form, date, accession}], the
    company's own SEC filing list (v5.46), for the reports Company Facts
    missed. All optional; tests pass fakes."""
    global _DATA_DIR, _FACTS_FN, _FETCH_FN, _SPLITS_FN, _QUOTE_FN, _TODAY_FN, _PRICES_FN, _FILINGS_FN
    with _LOCK:
        _DATA_DIR = Path(data_dir) / "share_history" if data_dir else None
        _FACTS_FN, _FETCH_FN, _SPLITS_FN = facts_fn, fetch_fn, splits_fn
        _QUOTE_FN, _TODAY_FN, _PRICES_FN, _FILINGS_FN = quote_fn, today_fn, prices_fn, filings_fn
        _RESULTS.clear()


def _today() -> date:
    if _TODAY_FN:
        try:
            return _TODAY_FN()
        except Exception:  # noqa: BLE001
            pass
    return date.today()


def _d(iso: str) -> date:
    return date.fromisoformat(str(iso)[:10])


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# ── reading a filing's cover page ───────────────────────────────────────────
_CONTEXT = re.compile(r"<(?:[\w-]+:)?context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</(?:[\w-]+:)?context>", re.S)
_MEMBER = re.compile(r"<(?:[\w-]+:)?explicitMember\b[^>]*\bdimension=\"([^\"]+)\"[^>]*>\s*([^<\s]+)\s*<", re.S)
_INSTANT = re.compile(r"<(?:[\w-]+:)?instant>\s*([^<\s]+)\s*<")
_COVER_FACT = re.compile(r"<(?:[\w-]+:)?EntityCommonStockSharesOutstanding\b([^>]*)>\s*([^<]+?)\s*<", re.S)
_CONTEXT_REF = re.compile(r"\bcontextRef=\"([^\"]+)\"")


def _local(qname: str) -> str:
    return qname.split(":", 1)[-1]


def class_label(member: str) -> str:
    """us-gaap:CommonClassAMember -> "Common Class A"."""
    name = _local(member)
    if name.endswith("Member"):
        name = name[:-6]
    return re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name).strip()


def parse_cover(xml: str) -> Optional[dict]:
    """The cover-page share count of one XBRL instance: {as_of, shares,
    classes:[{member, label, shares, as_of}] or None}. Only contexts with no
    dimension or the share-class dimension alone count; a co-registrant's
    count (LegalEntityAxis) is another company's shares. With classes, the
    total is their sum. None when the filing states no count."""
    contexts = {}
    for cid, body in _CONTEXT.findall(xml):
        members = _MEMBER.findall(body)
        inst = _INSTANT.search(body)
        contexts[cid] = (members, inst.group(1) if inst else None)
    plain, classes = [], []
    for attrs, raw in _COVER_FACT.findall(xml):
        ref = _CONTEXT_REF.search(attrs)
        val = _f(re.sub(r"[,\s]", "", raw))
        if not ref or val is None or ref.group(1) not in contexts:
            continue
        members, inst = contexts[ref.group(1)]
        if not inst:
            continue
        if not members:
            plain.append((inst, val))
        elif len(members) == 1 and _local(members[0][0]) == CLASS_AXIS:
            classes.append({"member": _local(members[0][1]), "label": class_label(members[0][1]),
                            "shares": val, "as_of": inst})
    if classes:
        seen, uniq = set(), []
        for c in classes:                       # the same fact can be tagged twice
            key = (c["member"], c["as_of"], c["shares"])
            if key not in seen:
                seen.add(key)
                uniq.append(c)
        return {"as_of": max(c["as_of"] for c in uniq), "shares": sum(c["shares"] for c in uniq),
                "classes": uniq}
    if plain:
        inst, val = max(plain)
        return {"as_of": inst, "shares": val, "classes": None}
    return None


def _cover_path(accession: str) -> Optional[Path]:
    if _DATA_DIR is None:
        return None
    return _DATA_DIR / "covers" / f"{accession}.json"


def read_filing_cover(cik: int, accession: str) -> Optional[dict]:
    """What one filing's cover page says, read once and kept: a filing never
    changes. {"cover": {...} or None}; None only when it could not be read
    (try again next time)."""
    path = _cover_path(accession)
    if path is not None and path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            pass
    if _FETCH_FN is None:
        return None
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    try:
        idx = json.loads(_FETCH_FN(f"{base}/index.json").decode("utf-8", "replace"))
        names = [i.get("name", "") for i in (idx.get("directory") or {}).get("item") or []]
    except Exception:  # noqa: BLE001
        return None
    inst = [n for n in names if n.lower().endswith(".xml")
            and not re.match(r"(?i)^(FilingSummary|R\d+|.*_(cal|def|lab|pre))\.xml$", n)]
    # The extracted instance of an inline filing ends _htm.xml; prefer it.
    inst.sort(key=lambda n: (not n.lower().endswith("_htm.xml"), len(n)))
    cover = None
    if inst:
        try:
            raw = _FETCH_FN(f"{base}/{inst[0]}", limit=INSTANCE_READ_BYTES)
            cover = parse_cover(raw.decode("utf-8", "replace"))
            if cover is None and len(raw) >= INSTANCE_READ_BYTES:
                cover = parse_cover(_FETCH_FN(f"{base}/{inst[0]}").decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return None
    rec = {"cover": cover, "instance": inst[0] if inst else None}
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rec))
            tmp.replace(path)
        except Exception:  # noqa: BLE001
            pass
    return rec


# ── the history ─────────────────────────────────────────────────────────────
def _filings(facts: Mapping) -> dict:
    """accession -> {form, filed} for every 10-Q/10-K the facts came from."""
    out: dict = {}
    for ns in ("dei", "us-gaap"):
        for entry in (facts.get("facts", {}).get(ns) or {}).values():
            for rows in (entry.get("units") or {}).values():
                for r in rows:
                    acc, form = r.get("accn"), str(r.get("form") or "").upper()
                    if acc and form in FORMS:
                        prev = out.get(acc)
                        if prev is None or str(r.get("filed") or "") < prev["filed"]:
                            out[acc] = {"form": form, "filed": str(r.get("filed") or "")}
    return out


def _all_forms(facts: Mapping) -> set:
    forms = set()
    for ns, tags in (facts.get("facts") or {}).items():
        for entry in tags.values():
            for rows in (entry.get("units") or {}).values():
                forms.update(str(r.get("form") or "").upper() for r in rows)
    return forms


def _companyfacts_counts(facts: Mapping) -> dict:
    """accession -> {as_of, shares} from the single-class cover counts."""
    entry = (facts.get("facts", {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding") or {}
    out: dict = {}
    for r in (entry.get("units") or {}).get("shares") or []:
        acc, val = r.get("accn"), _f(r.get("val"))
        if not acc or val is None or not r.get("end"):
            continue
        prev = out.get(acc)
        if prev is None or r["end"] > prev["as_of"]:
            out[acc] = {"as_of": r["end"], "shares": val}
    return out


def _weights(symbol: str, as_of: str = "9999-12-31") -> dict:
    """The class conversion ratios in force on `as_of`; {} for an ordinary company."""
    for since, w in CLASS_WEIGHTS.get(str(symbol).upper(), []):
        if as_of >= since:
            return w
    return {}


def _weighted(cover: Mapping, weights: Mapping) -> float:
    if not cover.get("classes") or not weights:
        return float(cover["shares"])
    return sum(c["shares"] * weights.get(c["member"].removesuffix("Member"), 1.0)
               for c in cover["classes"])


def _splits(symbol: str) -> list:
    """[(date, ratio)]. Raises when the split history could not be had:
    counts left on a mix of share bases must not be shown (Codex, #430)."""
    if _SPLITS_FN is None:
        return []
    raw = list(_SPLITS_FN(symbol) or [])
    out = []
    for d, r in raw:
        r = _f(r)
        if r and r > 0 and abs(r - 1.0) > 1e-9:
            out.append((str(d)[:10], r))
    return sorted(out)


def _factor(as_of: str, splits: list) -> float:
    f = 1.0
    for d, r in splits:
        if d > as_of:
            f *= r
    return f


def _place_on_todays_basis(points: list, splits: list) -> None:
    """Multiply each count by the splits after its date. A count dated in the
    days just before a split may already be on the new basis (the shares are
    distributed before the ex-date): there, the factor that agrees with the
    neighbouring counts wins."""
    for p in points:
        p["split_factor"] = _factor(p["as_of"], splits)
    for i, p in enumerate(points):
        near = [(d, r) for d, r in splits
                if 0 < (_d(d) - _d(p["as_of"])).days <= NEAR_SPLIT_DAYS]
        if not near:
            continue
        alt = p["split_factor"]
        for _d0, r in near:
            alt /= r
        others = [q["reported"] * q["split_factor"] for j, q in enumerate(points)
                  if j != i and abs((_d(q["as_of"]) - _d(p["as_of"])).days) <= 200
                  and not any(0 < (_d(d) - _d(q["as_of"])).days <= NEAR_SPLIT_DAYS for d, _r in splits)]
        if not others:
            continue
        ref = sorted(others)[len(others) // 2]
        if abs(math.log(p["reported"] * alt / ref)) < abs(math.log(p["reported"] * p["split_factor"] / ref)):
            p["split_factor"] = alt
            p["basis_note"] = "already on the post-split basis when counted"
    for p in points:
        p["shares"] = p["reported"] * p["split_factor"]


def _snap_split(ratio: float) -> Optional[float]:
    try:
        import fundamentals as F
        return F._snap_split(ratio)
    except Exception:  # noqa: BLE001
        return None


def _money_shares(v: float) -> str:
    return f"{v / 1e9:,.2f}B" if v >= 1e9 else f"{v / 1e6:,.1f}M"


def _day_label(iso: str) -> str:
    d = _d(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def build(symbol: str) -> dict:
    """The full history for one ticker. May read filings (a minute for a
    multi-class company's first time); get() runs it in the background."""
    sym = str(symbol or "").upper().strip()
    base = {"ok": True, "symbol": sym, "max_age_days": MAX_AGE_DAYS, "schema": SCHEMA,
            "built_at": datetime.now().isoformat(timespec="seconds")}
    if not _SYMBOL.fullmatch(sym):
        return dict(base, ok=False, status="unsupported", reason="Not a stock symbol.")
    facts = None
    if _FACTS_FN is not None:
        try:
            facts = _FACTS_FN(sym)
        except Exception:  # noqa: BLE001
            # Could not ask is not "nothing there": tried again in minutes,
            # and never kept on disk (Codex, #430).
            return dict(base, status="unavailable", partial=True,
                        reason="SEC could not be reached just now; trying again in a few minutes.")
    if not facts or not facts.get("facts"):
        return dict(base, status="unsupported",
                    reason="No SEC share counts for this symbol: ETFs, funds and indexes file none, "
                           "and neither do companies outside SEC reporting.")
    filings = _filings(facts)
    # Company Facts can miss a report for months: Coca-Cola's 10-Q of
    # Jul 29, 2026 was not in it in October, nor were four other companies'
    # from that week out of 36 sampled (v5.46). The company's own filing
    # list adds every 10-Q/10-K it lacks, and those covers are read.
    unlisted = False
    if _FILINGS_FN is not None and filings:
        try:
            for r in _FILINGS_FN(sym) or []:
                acc, form = r.get("accession"), str(r.get("form") or "").upper()
                if acc and form in FORMS and acc not in filings:
                    filings[acc] = {"form": form, "filed": str(r.get("date") or "")[:10]}
        except Exception:  # noqa: BLE001
            unlisted = True
    if not filings:
        forms = _all_forms(facts)
        if forms & FOREIGN_FORMS or "ifrs-full" in (facts.get("facts") or {}):
            return dict(base, status="unsupported", entity=facts.get("entityName"),
                        reason="A foreign filer: it reports its share count once a year, in home-market "
                               "shares, and SEC data does not say how many of those one US-listed share "
                               "stands for.")
        return dict(base, status="unsupported", entity=facts.get("entityName"),
                    reason="No quarterly or annual reports (10-Q/10-K) in SEC data for this symbol.")
    cik = int(facts.get("cik") or 0)
    weights = _weights(sym)
    from_facts = _companyfacts_counts(facts)
    points, unread = [], 0

    def add(acc, meta, cover, source):
        points.append({"as_of": cover["as_of"], "reported": _weighted(cover, _weights(sym, cover["as_of"])),
                       "classes": cover.get("classes"), "form": meta["form"], "filed": meta["filed"],
                       "accession": acc, "source": source})

    def read_all(accs):
        nonlocal unread
        for acc in accs:
            rec = read_filing_cover(cik, acc)
            if rec is None:
                unread += 1
            elif rec.get("cover"):
                add(acc, filings[acc], rec["cover"], "filing")

    plain = [a for a in filings if a in from_facts]
    if weights:                                  # class economics: read every cover
        read_all(sorted(filings))
    else:
        for acc in plain:
            c = from_facts[acc]
            add(acc, filings[acc], {"as_of": c["as_of"], "shares": c["shares"], "classes": None},
                "companyfacts")
        read_all(sorted(a for a in filings if a not in from_facts))

    # A company whose summary count is one class of several: the newest
    # cover says so, and every cover is read instead.
    if plain and not weights:
        newest = max(plain, key=lambda a: filings[a]["filed"])
        rec = read_filing_cover(cik, newest)
        if rec is None:
            unread += 1                         # not proof of one class: checked again soon
        cov = (rec or {}).get("cover")
        if cov and cov.get("classes") and abs(cov["shares"] / from_facts[newest]["shares"] - 1) > 0.01:
            points = []
            read_all(sorted(filings))

    # One point per as-of date: the first filing to state it.
    by_date: dict = {}
    for p in sorted(points, key=lambda p: (p["as_of"], p["filed"])):
        by_date.setdefault(p["as_of"], p)
    points = [by_date[k] for k in sorted(by_date)]
    if not points:
        if unread:
            return dict(base, status="unavailable", partial=True, entity=facts.get("entityName"),
                        reason="SEC filings could not be read just now; trying again in a few minutes.")
        return dict(base, status="unsupported", entity=facts.get("entityName"),
                    reason="Its filings state no share count on the cover page.")

    try:
        splits = _splits(sym)
    except Exception:  # noqa: BLE001
        return dict(base, status="unavailable", partial=True, entity=facts.get("entityName"),
                    reason="The split history could not be fetched just now; trying again in a few minutes.")
    _place_on_todays_basis(points, splits)
    warnings = []
    # A count that jumps 5x or more and comes straight back, with the counts
    # either side of it agreeing, is a units error in that filing: Pentair's
    # 10-Q for Apr 2, 2011 filed 146,546,358,546 shares between two of about
    # 146.5 million (v5.46). It is left out, and said; the days it covered
    # use the count before it. The newest count has nothing after it to show
    # it wrong, and stays.
    rejected = []
    for i in range(len(points) - 2, 0, -1):
        a, b, c = points[i - 1]["shares"], points[i]["shares"], points[i + 1]["shares"]
        if not (a and b and c):
            continue
        up, back, ends = b / a, c / b, c / a
        if ((up >= 5 and back <= 0.2) or (up <= 0.2 and back >= 5)) and 0.5 <= ends <= 2:
            bad = points.pop(i)
            rejected.insert(0, {k: bad.get(k) for k in ("as_of", "shares", "form", "filed", "accession")})
            warnings.append(f"The count filed for {_day_label(bad['as_of'])} ({_money_shares(bad['shares'])} shares, "
                            f"{bad['form']}) is about {max(up, 1 / up):,.0f}x the counts either side of it: a units "
                            "error in the filing. It is left out; those days use the count before it.")
    moves = []
    for a, b in zip(points, points[1:]):
        ratio = b["shares"] / a["shares"] if a["shares"] else None
        if not ratio:
            continue
        change = (ratio - 1.0) * 100.0
        snapped = _snap_split(ratio)
        split_like = snapped is not None and (snapped >= 1.9 or snapped <= 1 / 1.9)
        if split_like or abs(change) >= BIG_MOVE_PCT:
            moves.append({"from": a["as_of"], "to": b["as_of"], "change_pct": round(change, 1),
                          "split_like": split_like})
        if split_like:
            warnings.append(f"The share count changes {ratio:.3g}x between {_day_label(a['as_of'])} and "
                            f"{_day_label(b['as_of'])} with no split in the price data: possibly a split "
                            "the prices were not adjusted for. Check before trusting the chart there.")
        elif ratio >= 5 or ratio <= 0.2:
            warnings.append(f"The share count changes {ratio:.3g}x between {_day_label(a['as_of'])} and "
                            f"{_day_label(b['as_of'])}: a very large issue or a filing error. Check "
                            "before trusting the chart there.")

    today = _today()
    last = points[-1]
    age = (today - _d(last["as_of"])).days
    latest = {"as_of": last["as_of"], "shares": last["shares"], "age_days": age,
              "form": last["form"], "filed": last["filed"]}
    if age > MAX_AGE_DAYS:
        warnings.append(f"The newest share count is from {_day_label(last['as_of'])}, {age} days ago: "
                        "too old to stand for today, so recent days have no market cap.")
    yahoo = None
    if _QUOTE_FN is not None:
        try:
            q = _QUOTE_FN(sym) or {}
        except Exception:  # noqa: BLE001
            q = {}
        price, mcap = _f(q.get("price")), _f(q.get("market_cap"))
        if price and mcap and price > 0:
            implied = mcap / price
            diff = (implied / last["shares"] - 1.0) * 100.0
            yahoo = {"price": price, "market_cap": mcap, "implied_shares": implied,
                     "diff_pct": round(diff, 2)}
            if abs(diff) > YAHOO_WARN_PCT:
                more = "more" if diff > 0 else "fewer"
                why = (f"Shares {'issued' if diff > 0 else 'bought back'} since that report would explain it"
                       if age > 30 else "Yahoo may count classes or units the filing does not")
                warnings.append(f"Yahoo's market cap today implies {_money_shares(implied)} shares, "
                                f"{abs(diff):.1f}% {more} than the {_money_shares(last['shares'])} in the "
                                f"{_day_label(last['as_of'])} filing. {why}; the chart uses the filed count.")
    if last.get("classes"):
        parts = ", ".join(f"{c['label']} {_money_shares(c['shares'])}" for c in last["classes"] if c["shares"])
        note = " (weighted by their fixed conversion ratio)" if weights else ""
        warnings.append(f"Share classes counted together{note}: {parts}.")
    if unread:
        warnings.append(f"{unread} filing{'s' if unread != 1 else ''} could not be read from SEC just now; "
                        "the history is retried in a few minutes.")
    # The closes a chart's prices are checked against (v5.46). A chart whose
    # prices are dividend-adjusted, or on another split basis, would draw a
    # market cap too low in the past: the page compares before it draws.
    price_check = None
    if _PRICES_FN is not None:
        try:
            closes = [[str(d)[:10], _f(c)] for d, c in (_PRICES_FN(sym) or [])]
            closes = [[d, c] for d, c in closes if c and c > 0]
        except Exception:  # noqa: BLE001
            closes = []
        if closes:
            price_check = {"closes": closes,
                           "source": "Yahoo daily closes, one a month: split-adjusted, not dividend-adjusted"}
        else:
            warnings.append("Yahoo's unadjusted closes could not be fetched just now, so the chart's prices "
                            "could not be checked against them; retried in a few minutes.")
    if unlisted:
        warnings.append("SEC's list of filings could not be read just now, so a report missing from its "
                        "summary data would be missed; retried in a few minutes.")
    stale = bool(facts.get("_stale"))
    if stale:
        when = facts.get("_fetched_ts")
        since = (f" from {_day_label(datetime.fromtimestamp(float(when)).date().isoformat())}"
                 if when else "")
        warnings.append(f"SEC's data could not be refreshed just now, so this uses its last copy{since}; "
                        "the history is retried in a few minutes.")
    for p in points:
        p["shares"] = round(p["shares"], 2)
    unchecked = _PRICES_FN is not None and price_check is None
    return dict(base, status="ok", entity=facts.get("entityName"), cik=cik,
                partial=bool(unread) or stale or unchecked or unlisted, points=points, rejected=rejected, price_check=price_check, splits=[{"date": d, "ratio": r} for d, r in splits],
                coverage={"from": points[0]["as_of"], "to": last["as_of"]},
                latest=latest, checks={"yahoo": yahoo, "big_moves": moves, "unread_filings": unread},
                warnings=warnings,
                source="SEC filings: the shares-outstanding count on each 10-Q and 10-K cover page")


# ── serving it ──────────────────────────────────────────────────────────────
def _result_path(sym: str) -> Optional[Path]:
    if _DATA_DIR is None:
        return None
    return _DATA_DIR / f"{re.sub(r'[^A-Z0-9._-]', '_', sym)}.json"


def _cached(sym: str) -> Optional[tuple]:
    with _LOCK:
        hit = _RESULTS.get(sym)
    if hit:
        return hit
    path = _result_path(sym)
    if path is not None and path.exists():
        try:
            rec = json.loads(path.read_text())
            if rec.get("schema") != SCHEMA:
                return None                     # kept by an older version: rebuilt
            hit = (float(rec.pop("_built_ts")), rec)
            with _LOCK:
                _RESULTS[sym] = hit
            return hit
        except Exception:  # noqa: BLE001
            return None
    return None


def _store(sym: str, res: dict) -> None:
    ts = _clock()
    with _LOCK:
        _RESULTS[sym] = (ts, res)
        if len(_RESULTS) > 500:
            _RESULTS.pop(next(iter(_RESULTS)))
    path = _result_path(sym)
    if path is not None and res.get("status") in ("ok", "unsupported"):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(res, _built_ts=ts)))
            tmp.replace(path)
        except Exception:  # noqa: BLE001
            pass


def get(symbol: str, wait: float = 3.0) -> dict:
    """The cached history, or a build started in the background. Answers
    within `wait` seconds: "building" (with the stale history if there is
    one) while a multi-class company's filings are being read."""
    sym = str(symbol or "").upper().strip()
    if not _SYMBOL.fullmatch(sym):
        return build(sym)
    hit = _cached(sym)
    ttl = PARTIAL_TTL_S if (hit and hit[1].get("partial")) else RESULT_TTL_S
    if hit and _clock() - hit[0] < ttl and hit[1].get("status") != "building":
        return hit[1]
    with _LOCK:
        ev = _BUILDING.get(sym)
        mine = ev is None
        if mine:
            ev = threading.Event()
            _BUILDING[sym] = ev
    if mine:
        def run():
            try:
                res = build(sym)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "symbol": sym, "status": "error", "reason": str(exc)[:200]}
            if res.get("status") not in ("ok", "unsupported"):
                # A refresh that could not be done is tried again soon, and
                # does not take the last good history away (Codex, #430).
                res["partial"] = True
                if hit and hit[1].get("status") == "ok":
                    prev = dict(hit[1])
                    note = (f"A refresh just failed ({res.get('reason') or res.get('status')}); this is the "
                            f"history built {prev.get('built_at', 'earlier')}, refreshed again in a few minutes.")
                    prev["warnings"] = [w for w in prev.get("warnings") or []
                                        if not w.startswith("A refresh just failed")] + [note]
                    prev["partial"] = True
                    res = prev
            _store(sym, res)
            with _LOCK:
                _BUILDING.pop(sym, None)
            ev.set()
        threading.Thread(target=run, daemon=True, name=f"share-history:{sym}").start()
    ev.wait(max(0.0, wait))
    now = _cached(sym)
    if now and (not hit or now[0] > hit[0]):
        return now[1]
    out = dict(hit[1]) if hit else {"ok": True, "symbol": sym, "status": "building"}
    out["refreshing"] = True
    if not hit:
        out["reason"] = "Reading this company's SEC filings for its share counts; this takes up to a minute the first time."
    return out


def shares_on(history: Mapping, day: str) -> Optional[dict]:
    """The share count a chart multiplies `day`'s price by: the last count
    reported on or before it, with its age. None before the first count, or
    when the last one is older than MAX_AGE_DAYS (a gap, not a guess)."""
    pts = (history or {}).get("points") or []
    day = str(day)[:10]
    lo, hi = 0, len(pts)
    while lo < hi:
        mid = (lo + hi) // 2
        if pts[mid]["as_of"] <= day:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    p = pts[lo - 1]
    age = (_d(day) - _d(p["as_of"])).days
    if age > (history.get("max_age_days") or MAX_AGE_DAYS):
        return None
    return {"shares": p["shares"], "as_of": p["as_of"], "age_days": age, "form": p.get("form"),
            "filed": p.get("filed")}


def market_cap_bars(history: Mapping, bars: Iterable[Mapping]) -> list:
    """Daily bars ({date, open, high, low, close}) as market cap: each price
    times that day's share count, None where there is no count."""
    out = []
    for b in bars or []:
        s = shares_on(history, str(b.get("date"))[:10])
        if s is None:
            out.append(None)
            continue
        out.append({k: (round(_f(b.get(k)) * s["shares"], 2) if _f(b.get(k)) is not None else None)
                    for k in ("open", "high", "low", "close")} | {"date": b.get("date"), "shares": s})
    return out
