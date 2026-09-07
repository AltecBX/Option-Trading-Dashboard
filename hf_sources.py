"""hf_sources.py — what the filings and files actually say.

Every function here turns one verified source into evidence rows, and every
row carries the same five things: an evidence CLASS, the date the fact
describes (`as_of`), the date it became public (`public_on`), a `source`
string naming the file or endpoint, and — only when the class permits it —
the manager it is about. The last rule is enforced by `evidence()` itself:
an anonymous class cannot name a fund, and a test proves it.

The parsers are pure and are exercised on captured filings in fixtures/hf.
The fetchers go through one injectable transport so the tests never touch
the network and production never re-reads a filing twice — a filing is
immutable, so a parsed one is cached forever by accession number.

Sources (HEDGE_FUND_INTEL.md §2):
  * EDGAR submissions JSON     — every filing a CIK has made, with dates
  * EDGAR filing folders       — the 13F information table XML, the 13F
                                  cover page (amendment type, totals, the
                                  successor named in a 13F-NT), and the
                                  structured SCHEDULE 13D XML
  * EDGAR daily form index     — every filing of every form, one file a day
  * SEC fails-to-deliver file  — the only free, official CUSIP → symbol map
  * RSS / Google News          — a manager's own channel, and press about them
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

HF_SOURCES_VERSION = "1.0.0"

# ── evidence classes ────────────────────────────────────────────────────────
VERIFIED = "VERIFIED FUND ACTIVITY"
PRIME_BROKER = "PRIME BROKER AGGREGATE DATA"
REGULATORY = "REGULATORY POSITIONING DATA"
FLOW_PROXY = "INSTITUTIONAL FLOW PROXY"
INFERENCE = "MODEL INFERENCE"
EVIDENCE_CLASSES = (VERIFIED, PRIME_BROKER, REGULATORY, FLOW_PROXY, INFERENCE)
# Sub-types of VERIFIED, weakest last. A FILING is a position. A STATEMENT is
# what the manager said in their own channel. PRESS is what an outlet reported
# they said or did. Only the first is a position.
VERIFIED_SUBTYPES = ("FILING", "STATEMENT", "PRESS")


def evidence(cls: str, subtype: str | None, as_of: str | None, public_on: str | None,
             source: str, fund: str | None = None, **fields) -> dict:
    """One evidence row. Raises rather than stores when a row would break
    the attribution rule — anonymous activity can never carry a fund's name."""
    if cls not in EVIDENCE_CLASSES:
        raise ValueError(f"unknown evidence class {cls!r}")
    if fund is not None and cls != VERIFIED:
        raise ValueError(f"{cls} rows cannot be attributed to a fund ({fund})")
    if cls == VERIFIED and subtype not in VERIFIED_SUBTYPES:
        raise ValueError(f"VERIFIED rows need a sub-type in {VERIFIED_SUBTYPES}")
    row = {"class": cls, "subtype": subtype, "as_of": as_of, "public_on": public_on,
           "source": source}
    if fund is not None:
        row["fund"] = fund
    row.update(fields)
    return row


def attribution_ok(rows) -> bool:
    """True when no anonymous row names a fund. Used by the tests and by the
    store before it writes anything."""
    return all(("fund" not in r) or (r.get("class") == VERIFIED) for r in (rows or []))


# ── transport ───────────────────────────────────────────────────────────────
_FETCH = None            # (url) -> bytes; injected. Defaults to sec_filings' throttled fetch.
_POST = None             # (url, body) -> bytes; injected. FINRA's query API only.
_DATA_DIR: Path | None = None
_NOW_FN = None
_LOCK = threading.RLock()
_MEM: dict[str, tuple[float, object]] = {}

FOREVER = 10 * 365 * 86400.0
TTL = {"submissions": 6 * 3600.0, "index_today": 3600.0, "ftd": 15 * 86400.0,
       "feed": 3600.0, "news": 3600.0, "folder": FOREVER, "doc": FOREVER}

EDGAR_SUB = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
EDGAR_FOLDER = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
EDGAR_DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
EDGAR_DAILY = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}/form.{ymd}.idx"
FTD_PAGE = "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data"
GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def configure(fetch_fn=None, data_dir=None, now_fn=None, post_fn=None) -> None:
    global _FETCH, _DATA_DIR, _NOW_FN, _POST
    _FETCH = fetch_fn
    _POST = post_fn
    _DATA_DIR = Path(data_dir) if data_dir else None
    _NOW_FN = now_fn
    if _DATA_DIR is not None:
        try:
            (_DATA_DIR / "hf" / "cache").mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass


def available() -> bool:
    import os
    return not os.environ.get("JERRY_NO_NET")


def _now() -> datetime:
    if _NOW_FN:
        try:
            n = _NOW_FN()
            if isinstance(n, datetime):
                return n
        except Exception:  # noqa: BLE001
            pass
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().date()


def _default_fetch(url: str) -> bytes:
    import sec_filings as _sec
    return _sec._fetch(url, timeout=30)  # noqa: SLF001


def _default_post(url: str, body: bytes) -> bytes:
    """FINRA's query API is the only source here that needs a POST. Kept
    separate from the SEC transport so its throttle is not spent on a host
    that does not ask for one."""
    import urllib.request
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "JerryTrade dashboard (hedge fund intelligence)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        # 204 means "no such report yet" and has no body — an empty answer,
        # not an error, and the caller reads it as "not published".
        return resp.read() if resp.status != 204 else b""


def _cache_path(key: str) -> Path | None:
    if _DATA_DIR is None:
        return None
    return _DATA_DIR / "hf" / "cache" / (hashlib.sha1(key.encode()).hexdigest() + ".json")


def _get(url: str, ttl: float, kind: str = "bytes") -> bytes | None:
    """Memory, then disk, then the network. `kind` is only a label in the
    cache record so a human can read the cache directory."""
    now = time.time()
    with _LOCK:
        hit = _MEM.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    p = _cache_path(url)
    if p is not None and p.exists():
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if now - float(rec.get("ts") or 0) < ttl:
                raw = bytes.fromhex(rec["hex"])
                with _LOCK:
                    _MEM[url] = (float(rec["ts"]), raw)
                return raw
        except Exception:  # noqa: BLE001
            pass
    if not available():
        return None
    fetch = _FETCH or _default_fetch
    try:
        raw = fetch(url)
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    with _LOCK:
        _MEM[url] = (now, raw)
    if p is not None:
        try:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"ts": now, "kind": kind, "url": url,
                                       "hex": raw.hex()}), encoding="utf-8")
            tmp.replace(p)
        except Exception:  # noqa: BLE001
            pass
    return raw


def _post(url: str, body: bytes, key: str, ttl: float) -> bytes | None:
    """A cached POST. Keyed by `key` rather than the URL, because every page
    of a FINRA pull hits the same URL with a different body."""
    now = time.time()
    with _LOCK:
        hit = _MEM.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    p = _cache_path(key)
    if p is not None and p.exists():
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if now - float(rec.get("ts") or 0) < ttl:
                raw = bytes.fromhex(rec["hex"])
                with _LOCK:
                    _MEM[key] = (float(rec["ts"]), raw)
                return raw
        except Exception:  # noqa: BLE001
            pass
    if not available():
        return None
    post = _POST or _default_post
    try:
        raw = post(url, body)
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    with _LOCK:
        _MEM[key] = (now, raw)
    if p is not None:
        try:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"ts": now, "kind": "post", "url": key,
                                       "hex": raw.hex()}), encoding="utf-8")
            tmp.replace(p)
        except Exception:  # noqa: BLE001
            pass
    return raw


# ── XML helpers ─────────────────────────────────────────────────────────────

def _xml(raw: bytes) -> ET.Element | None:
    """Parse and strip namespaces, so `infoTable` is `infoTable` whether the
    filer wrote it bare, as `ns1:infoTable`, or under a default namespace."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _text(el: ET.Element | None, path: str, default=None):
    if el is None:
        return default
    hit = el.find(path)
    if hit is None or hit.text is None:
        return default
    t = hit.text.strip()
    return t if t != "" else default


def _num(v):
    try:
        f = float(str(v).replace(",", ""))
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _iso_from_mdy(s: str | None) -> str | None:
    """13F cover pages write dates as MM-DD-YYYY."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ── 13F: the information table ──────────────────────────────────────────────

def parse_13f_table(raw: bytes) -> list[dict]:
    """Every row of a 13F information table.

    `value` is in dollars: the SEC changed the unit from thousands to dollars
    for periods ending on or after December 31, 2022, and every table this
    feature reads is newer than that. `put_call` is None for a share
    position, "Put" or "Call" for a listed option position — the one place a
    13F discloses options."""
    root = _xml(raw)
    if root is None:
        return []
    out = []
    for it in root.iter("infoTable"):
        shares = _num(_text(it, "shrsOrPrnAmt/sshPrnamt"))
        pc = _text(it, "putCall")
        out.append({
            "issuer": _text(it, "nameOfIssuer"),
            "title": _text(it, "titleOfClass"),
            "cusip": (_text(it, "cusip") or "").upper() or None,
            "value": _num(_text(it, "value")),
            "shares": shares,
            "share_type": _text(it, "shrsOrPrnAmt/sshPrnamtType"),
            "put_call": pc.title() if pc else None,
            "discretion": _text(it, "investmentDiscretion"),
            "other_managers": _text(it, "otherManager"),
            "voting": {"sole": _num(_text(it, "votingAuthority/Sole")),
                       "shared": _num(_text(it, "votingAuthority/Shared")),
                       "none": _num(_text(it, "votingAuthority/None"))},
        })
    return out


def parse_13f_primary(raw: bytes) -> dict:
    """The 13F cover page: which period, whether it is an amendment and of
    what kind, how many rows and dollars the table should hold, and — on a
    13F-NT — who reports the book instead."""
    root = _xml(raw)
    if root is None:
        return {}
    others = []
    for om in root.iter("otherManager"):
        cik = _text(om, "cik")
        name = _text(om, "name")
        if cik or name:
            others.append({"cik": int(cik) if cik and cik.isdigit() else None, "name": name})
    filer_cik = _text(root, ".//filerInfo/filer/credentials/cik") or _text(root, ".//filer/credentials/cik")
    amend = (_text(root, ".//isAmendment") or "").lower() == "true"
    return {
        "period_end": _iso_from_mdy(_text(root, ".//periodOfReport")),
        "report_type": _text(root, ".//reportType"),
        "is_amendment": amend,
        "amendment_type": _text(root, ".//amendmentType"),
        "amendment_no": _num(_text(root, ".//amendmentNo")),
        "entries": _num(_text(root, ".//tableEntryTotal")),
        "value_total": _num(_text(root, ".//tableValueTotal")),
        "filer_cik": int(filer_cik) if filer_cik and filer_cik.isdigit() else None,
        "filer_name": _text(root, ".//filingManager/name"),
        "signed_on": _iso_from_mdy(_text(root, ".//signatureBlock/signatureDate")),
        "other_managers": others,
    }


def successor_from_notice(primary: dict) -> dict | None:
    """A 13F-NT names the manager that reports the book. The filer's own
    identity is not a successor; the first other manager is.

    Only a NOTICE can name a successor. A holdings report also lists "other
    managers" — the entities whose positions it includes — and Pershing
    Square Inc.'s Q2 2026 report lists the OLD Pershing entity there. Read
    naively, the new filer would point back at the old one."""
    if "NOTICE" not in (primary.get("report_type") or "").upper():
        return None
    own = primary.get("filer_cik")
    for om in primary.get("other_managers") or []:
        if om.get("cik") and om["cik"] != own:
            return om
    return None


# ── 13F: what changed ───────────────────────────────────────────────────────

def _pos_key(p: dict) -> tuple:
    return (p.get("cusip") or "", p.get("put_call") or "", (p.get("title") or "").upper())


def _aggregate(positions: list[dict]) -> dict[tuple, dict]:
    """One row per position key. A filer may list the same security twice —
    split across sub-managers or discretion types — and Pershing Square's
    Q2 2026 table does exactly that for Howard Hughes. Summing first is
    what keeps a split line from reading as a new position."""
    out: dict[tuple, dict] = {}
    for p in positions:
        k = _pos_key(p)
        if k in out:
            out[k]["shares"] = (out[k].get("shares") or 0) + (p.get("shares") or 0)
            out[k]["value"] = (out[k].get("value") or 0) + (p.get("value") or 0)
        else:
            out[k] = dict(p)
    return out


def diff_positions(prev: list[dict] | None, curr: list[dict]) -> dict:
    """Quarter-on-quarter changes, keyed by CUSIP + option side + class so a
    call and a share position on the same issuer are two positions, as they
    are in the filing. Shares drive the verdict; value is context, because
    value moves with price even when nothing was traded."""
    total = sum(p.get("value") or 0 for p in curr) or 0.0
    a = _aggregate(prev or [])
    b = _aggregate(curr)
    new, inc, red, exited, unchanged = [], [], [], [], 0

    def row(p, was=None):
        sh_prev = (was or {}).get("shares")
        sh_now = p.get("shares")
        return {"issuer": p.get("issuer"), "cusip": p.get("cusip"), "title": p.get("title"),
                "put_call": p.get("put_call"),
                "shares_prev": sh_prev, "shares_now": sh_now,
                "delta_shares": (sh_now or 0) - (sh_prev or 0) if (sh_now is not None or sh_prev is not None) else None,
                "delta_pct": ((sh_now or 0) / sh_prev - 1.0) * 100.0 if sh_prev else None,
                "value_now": p.get("value"),
                "weight_now": ((p.get("value") or 0) / total * 100.0) if total else None}

    for k, p in b.items():
        was = a.get(k)
        if was is None:
            new.append(row(p))
        else:
            ds = (p.get("shares") or 0) - (was.get("shares") or 0)
            if ds > 0:
                inc.append(row(p, was))
            elif ds < 0:
                red.append(row(p, was))
            else:
                unchanged += 1
    for k, was in a.items():
        if k not in b:
            r = row({"issuer": was.get("issuer"), "cusip": was.get("cusip"),
                     "title": was.get("title"), "put_call": was.get("put_call"),
                     "shares": None, "value": None}, was)
            r["delta_shares"] = -(was.get("shares") or 0)
            r["delta_pct"] = -100.0
            exited.append(r)
    by_size = lambda r: -abs(r.get("value_now") or 0)  # noqa: E731
    return {"new": sorted(new, key=by_size), "increased": sorted(inc, key=by_size),
            "reduced": sorted(red, key=by_size),
            "exited": sorted(exited, key=lambda r: -abs(r.get("delta_shares") or 0)),
            "unchanged": unchanged, "n_prev": len(a), "n_now": len(b),
            "value_total_now": total,
            "value_total_prev": sum(p.get("value") or 0 for p in (prev or [])) or 0.0}


def concentration(positions: list[dict]) -> dict:
    """How concentrated a book is, and how much of its reported value is
    listed options rather than shares. Both are what separate a book whose
    13F is a view from one whose 13F is a hedge ledger."""
    vals = sorted((p.get("value") or 0.0 for p in positions), reverse=True)
    total = sum(vals)
    if not total:
        return {"n": len(positions), "top10_weight": None, "hhi": None,
                "options_value_share": None, "value_total": 0.0}
    w = [v / total for v in vals]
    opt = sum((p.get("value") or 0.0) for p in positions if p.get("put_call")) / total
    return {"n": len(positions),
            "top10_weight": round(sum(w[:10]) * 100.0, 1),
            "hhi": round(sum(x * x for x in w) * 10000.0, 0),
            "options_value_share": round(opt * 100.0, 1),
            "value_total": total}


def top_positions(positions: list[dict], n: int = 10) -> list[dict]:
    total = sum(p.get("value") or 0.0 for p in positions) or 0.0
    rows = sorted(positions, key=lambda p: -(p.get("value") or 0.0))[:n]
    return [{"issuer": p.get("issuer"), "cusip": p.get("cusip"), "title": p.get("title"),
             "put_call": p.get("put_call"), "shares": p.get("shares"), "value": p.get("value"),
             "weight": ((p.get("value") or 0.0) / total * 100.0) if total else None}
            for p in rows]


def sector_exposure(positions: list[dict], symbol_of: dict, sector_of) -> dict:
    """Roll a book up by sector. CUSIPs the map does not know, and symbols the
    sector function cannot place, are reported as UNMAPPED with their weight
    — never dropped, never filed under a twelfth sector called Other."""
    total = sum(p.get("value") or 0.0 for p in positions) or 0.0
    acc: dict[str, dict] = {}
    unm_v, unm_n = 0.0, 0
    for p in positions:
        v = p.get("value") or 0.0
        sym = symbol_of.get(p.get("cusip") or "")
        sec = None
        if sym:
            try:
                sec = sector_of(sym)
            except Exception:  # noqa: BLE001
                sec = None
        if not sec:
            unm_v += v
            unm_n += 1
            continue
        s = acc.setdefault(sec, {"sector": sec, "value": 0.0, "n": 0})
        s["value"] += v
        s["n"] += 1
    rows = sorted(acc.values(), key=lambda s: -s["value"])
    for s in rows:
        s["weight"] = (s["value"] / total * 100.0) if total else None
    return {"sectors": rows,
            "unmapped": {"n": unm_n, "weight": (unm_v / total * 100.0) if total else None},
            "value_total": total}


# ── SCHEDULE 13D (structured XML, December 2024 onward) ─────────────────────

def parse_13d(raw: bytes) -> dict | None:
    """The fields that make an activist filing a fact rather than a headline."""
    root = _xml(raw)
    if root is None:
        return None
    persons = []
    for rp in root.iter("reportingPersonInfo"):
        pct = _num(_text(rp, "percentOfClass"))
        persons.append({"name": _text(rp, "reportingPersonName"),
                        "percent": pct,
                        "amount": _num(_text(rp, "aggregateAmountOwned")),
                        "sole_voting": _num(_text(rp, "soleVotingPower")),
                        "shared_voting": _num(_text(rp, "sharedVotingPower")),
                        "type": _text(rp, "typeOfReportingPerson")})
    pcts = [p["percent"] for p in persons if p.get("percent") is not None]
    filer = _text(root, ".//filerInfo/filer/filerCredentials/cik") or \
        _text(root, ".//filer/filerCredentials/cik")
    return {"form": _text(root, ".//submissionType"),
            "issuer_name": _text(root, ".//issuerName"),
            "issuer_cik": _text(root, ".//issuerCik"),
            "security_title": _text(root, ".//securityTitle") or _text(root, ".//securitiesClassTitle"),
            "event_date": _iso_from_mdy(_text(root, ".//dateOfEvent") or _text(root, ".//date")),
            "purpose": _text(root, ".//transactionPurpose"),
            "description": _text(root, ".//transactionDesc"),
            "reporting_persons": persons,
            "percent_max": max(pcts) if pcts else None,
            "filer_cik": int(filer) if filer and filer.isdigit() else None}


# ── EDGAR daily form index ──────────────────────────────────────────────────
_IDX_ROW = re.compile(r"^(?P<form>\S[^\n]*?)\s{2,}(?P<company>.*?)\s{2,}(?P<cik>\d+)\s+(?P<date>\d{8})\s+(?P<path>edgar/\S+)\s*$")
WATCHED_FORMS = ("13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
                 "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A")


def parse_daily_index(text: str, forms: tuple = WATCHED_FORMS) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _IDX_ROW.match(line)
        if not m:
            continue
        form = m.group("form").strip()
        if forms and form not in forms:
            continue
        d = m.group("date")
        out.append({"form": form, "company": m.group("company").strip(),
                    "cik": int(m.group("cik")), "filed": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                    "path": m.group("path"),
                    "accession": m.group("path").rsplit("/", 1)[-1].replace(".txt", "")})
    return out


def daily_index(day: date) -> list[dict]:
    q = (day.month - 1) // 3 + 1
    url = EDGAR_DAILY.format(year=day.year, q=q, ymd=day.strftime("%Y%m%d"))
    ttl = TTL["index_today"] if day >= _today() else FOREVER
    raw = _get(url, ttl, "daily-index")
    return parse_daily_index(raw.decode("latin-1")) if raw else []


# ── CUSIP → symbol (SEC fails-to-deliver file) ──────────────────────────────

def parse_ftd(text: str) -> dict[str, str]:
    """SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE.
    Only the middle two columns matter here; the file exists for another
    reason entirely, and happens to be the one free official list that
    pairs a CUSIP with the symbol it trades under."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        p = line.split("|")
        if len(p) < 3 or p[0].startswith("SETTLEMENT"):
            continue
        cusip, sym = p[1].strip().upper(), p[2].strip().upper()
        if len(cusip) == 9 and sym and cusip not in out:
            out[cusip] = sym
    return out


def cusip_map() -> dict[str, str]:
    """The newest fails-to-deliver file, found from the SEC catalogue page so
    the file name never has to be guessed. Refreshed every 15 days — the file
    itself is issued twice a month."""
    page = _get(FTD_PAGE, TTL["ftd"], "ftd-page")
    if not page:
        return {}
    m = re.search(rb'href="([^"]*cnsfails\d+[ab]\.zip)"', page)
    if not m:
        return {}
    href = m.group(1).decode()
    url = href if href.startswith("http") else "https://www.sec.gov" + href
    raw = _get(url, FOREVER, "ftd-zip")
    if not raw:
        return {}
    import io
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        text = z.read(z.namelist()[0]).decode("latin-1")
    except Exception:  # noqa: BLE001
        return {}
    return parse_ftd(text)


# ── EDGAR: submissions and filing documents ─────────────────────────────────

def submissions(cik: int) -> dict | None:
    raw = _get(EDGAR_SUB.format(cik=int(cik)), TTL["submissions"], "submissions")
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def recent_filings(sub: dict) -> list[dict]:
    """The submissions feed as rows: form, filed, period, accession, doc."""
    f = (sub or {}).get("filings", {}).get("recent", {}) or {}
    cols = ("form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "acceptanceDateTime")
    n = len(f.get("form") or [])
    out = []
    for i in range(n):
        out.append({"form": f["form"][i], "filed": f.get("filingDate", [None] * n)[i],
                    "period": f.get("reportDate", [None] * n)[i] or None,
                    "accession": f.get("accessionNumber", [None] * n)[i],
                    "doc": f.get("primaryDocument", [None] * n)[i],
                    "accepted": f.get("acceptanceDateTime", [None] * n)[i]})
    return out


def _folder(cik: int, accession: str) -> list[dict]:
    acc = accession.replace("-", "")
    raw = _get(EDGAR_FOLDER.format(cik=int(cik), acc=acc), TTL["folder"], "folder")
    if not raw:
        return []
    try:
        items = json.loads(raw.decode("utf-8", "replace")).get("directory", {}).get("item", [])
    except Exception:  # noqa: BLE001
        return []
    return [{"name": it.get("name"), "type": it.get("type")} for it in items]


def _doc(cik: int, accession: str, name: str) -> bytes | None:
    return _get(EDGAR_DOC.format(cik=int(cik), acc=accession.replace("-", ""), doc=name),
                TTL["doc"], "doc")


def fetch_13f(cik: int, accession: str) -> dict | None:
    """Cover page plus every position, parsed once and kept forever."""
    p = _cache_path(f"13f:{cik}:{accession}")
    if p is not None and p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))["parsed"]
        except Exception:  # noqa: BLE001
            pass
    files = _folder(cik, accession)
    names = [f["name"] for f in files if f.get("name")]
    primary_raw = _doc(cik, accession, "primary_doc.xml") if "primary_doc.xml" in names else None
    table_name = None
    for n in names:
        low = n.lower()
        if low.endswith(".xml") and low != "primary_doc.xml" and "xsl" not in low:
            table_name = n
            break
    table_raw = _doc(cik, accession, table_name) if table_name else None
    if primary_raw is None and table_raw is None:
        return None
    parsed = {"accession": accession, "cik": int(cik),
              "primary": parse_13f_primary(primary_raw) if primary_raw else {},
              "positions": parse_13f_table(table_raw) if table_raw else []}
    if p is not None:
        try:
            p.write_text(json.dumps({"ts": time.time(), "kind": "13f", "parsed": parsed}),
                         encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return parsed


def fetch_13d(cik: int, accession: str) -> dict | None:
    """The structured XML when the filing has one (December 2024 onward);
    otherwise None, and the caller keeps the metadata row."""
    files = _folder(cik, accession)
    if not any(f.get("name") == "primary_doc.xml" for f in files):
        return None
    raw = _doc(cik, accession, "primary_doc.xml")
    return parse_13d(raw) if raw else None


# ── RSS: a manager's own channel, and press about them ──────────────────────

def _rss_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(s.replace("GMT", "+0000"), fmt)
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def parse_rss(raw: bytes) -> list[dict]:
    root = _xml(raw)
    if root is None:
        return []
    out = []
    for it in root.iter("item"):
        desc = _text(it, "description") or ""
        desc = re.sub(r"<[^>]+>", " ", html.unescape(desc))
        desc = re.sub(r"\s+", " ", desc).strip()
        out.append({"title": html.unescape(_text(it, "title") or ""),
                    "link": _text(it, "link"),
                    "published": _rss_date(_text(it, "pubDate")),
                    "summary": desc[:400],
                    "outlet": _text(it, "source")})
    for it in root.iter("entry"):                      # Atom
        link = it.find("link")
        out.append({"title": html.unescape(_text(it, "title") or ""),
                    "link": link.get("href") if link is not None else None,
                    "published": _rss_date(_text(it, "published") or _text(it, "updated")),
                    "summary": re.sub(r"<[^>]+>", " ", html.unescape(_text(it, "summary") or ""))[:400],
                    "outlet": None})
    return out


def feed_items(url: str) -> list[dict]:
    raw = _get(url, TTL["feed"], "feed")
    return parse_rss(raw) if raw else []


def news_items(query: str) -> list[dict]:
    raw = _get(GNEWS.format(q=quote_plus(query)), TTL["news"], "news")
    return parse_rss(raw) if raw else []


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2 — the aggregate layer (Hedge Fund Pulse).
#
# Everything below is about the hedge fund universe, never about one fund.
# Each function returns rows classed REGULATORY POSITIONING DATA (an official
# filing or report about positions) or INSTITUTIONAL FLOW PROXY (anonymous
# market-structure activity that is evidence of behaviour, not a position).
# `evidence()` refuses to attach a fund name to either, and the store checks
# it again before writing.
# ══════════════════════════════════════════════════════════════════════════

CFTC_SOCRATA = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
FINRA_SI = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
FINRA_SHVOL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
OFR_FPF = "https://data.financialresearch.gov/hf/v1/series/dataset?dataset=fpf"

# The CFTC's Traders in Financial Futures report is the only WEEKLY, official,
# hedge-fund-specific positioning data that exists. "Leveraged Funds" is the
# category hedge funds report under. Positions are as of Tuesday; the report
# is published the following Friday at 3:30 PM ET.
#
# Two cautions the card must carry, both true of the level and not the change:
# leveraged funds are STRUCTURALLY net short equity index futures (they hedge
# long stock books and run basis trades), and the sector contracts are small
# and thinly held. The change and the percentile are the signal; the level is
# not. History depth is reported per market because it is not uniform — the
# Communication contract has traded for barely a year.
CFTC_MARKETS = [
    {"key": "sp500", "market": "E-MINI S&P 500", "label": "S&P 500", "kind": "index"},
    {"key": "nasdaq", "market": "NASDAQ-100 Consolidated", "label": "Nasdaq 100", "kind": "index"},
    {"key": "russell", "market": "RUSSELL E-MINI", "label": "Russell 2000", "kind": "index"},
    {"key": "djia", "market": "DJIA Consolidated", "label": "Dow Jones", "kind": "index"},
    {"key": "vix", "market": "VIX FUTURES", "label": "VIX", "kind": "vol"},
    {"key": "sec_staples", "market": "E-MINI S&P CONSU STAPLES INDEX", "label": "Consumer Staples",
     "kind": "sector", "sector": "Consumer Staples"},
    {"key": "sec_energy", "market": "E-MINI S&P ENERGY INDEX", "label": "Energy",
     "kind": "sector", "sector": "Energy"},
    {"key": "sec_financials", "market": "E-MINI S&P FINANCIAL INDEX", "label": "Financials",
     "kind": "sector", "sector": "Financials"},
    {"key": "sec_health", "market": "E-MINI S&P HEALTH CARE INDEX", "label": "Health Care",
     "kind": "sector", "sector": "Health Care"},
    {"key": "sec_industrials", "market": "E-MINI S&P INDUSTRIAL INDEX", "label": "Industrials",
     "kind": "sector", "sector": "Industrials"},
    {"key": "sec_utilities", "market": "E-MINI S&P UTILITIES INDEX", "label": "Utilities",
     "kind": "sector", "sector": "Utilities"},
    {"key": "sec_comm", "market": "E-MINI S&P COMMUNICATION INDEX", "label": "Communication Services",
     "kind": "sector", "sector": "Communication Services"},
]
# The four sectors with no leveraged-fund futures contract at all. Named here
# so the card can say WHICH inputs a sector is missing rather than showing a
# blank cell that looks like a scanner fault.
CFTC_SECTORS_MISSING = ("Technology", "Consumer Discretionary", "Materials", "Real Estate")

CFTC_WEEKS = 170                 # ~3¼ years, the depth Socrata reliably serves
TTL["cftc"] = 6 * 3600.0         # published Friday 3:30 PM ET; six hours is generous
TTL["finra_si"] = 12 * 3600.0    # twice a month
TTL["shvol"] = 6 * 3600.0        # daily file, posted that evening
TTL["ofr"] = 24 * 3600.0         # quarterly series


def _maybe_gunzip(raw: bytes) -> bytes:
    """The OFR host gzips its JSON whatever Accept-Encoding asks for, and
    urllib does not decompress for us. Sniffing the magic bytes is the only
    reliable test — the Content-Encoding header is not always set."""
    if raw[:2] == b"\x1f\x8b":
        import gzip
        try:
            return gzip.decompress(raw)
        except Exception:  # noqa: BLE001
            return raw
    return raw


def _num_or_none(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ── CFTC Traders in Financial Futures ───────────────────────────────────────

def parse_cftc(rows: list[dict]) -> list[dict]:
    """Weekly rows, newest first, with the three trader groups' net positions.

    Net = long − short contracts. `gross` = long + short is the size of the
    book on both sides, which is what "are they levering up?" reads: a fund
    that doubles both legs has taken more risk and no net change would show
    it. Spread positions are reported separately and are neither."""
    out = []
    for r in rows:
        d = str(r.get("report_date_as_yyyy_mm_dd") or "")[:10]
        lev_l, lev_s = _num_or_none(r.get("lev_money_positions_long")), _num_or_none(r.get("lev_money_positions_short"))
        am_l, am_s = _num_or_none(r.get("asset_mgr_positions_long")), _num_or_none(r.get("asset_mgr_positions_short"))
        dl_l, dl_s = _num_or_none(r.get("dealer_positions_long_all")), _num_or_none(r.get("dealer_positions_short_all"))
        if not d or lev_l is None or lev_s is None:
            continue
        out.append({
            "date": d,
            "open_interest": _num_or_none(r.get("open_interest_all")),
            "lev_long": lev_l, "lev_short": lev_s,
            "lev_net": lev_l - lev_s, "lev_gross": lev_l + lev_s,
            "lev_spread": _num_or_none(r.get("lev_money_positions_spread")),
            "traders_long": _num_or_none(r.get("traders_lev_money_long_all")),
            "traders_short": _num_or_none(r.get("traders_lev_money_short_all")),
            "asset_mgr_net": (am_l - am_s) if (am_l is not None and am_s is not None) else None,
            "dealer_net": (dl_l - dl_s) if (dl_l is not None and dl_s is not None) else None,
        })
    return sorted(out, key=lambda r: r["date"], reverse=True)


def cftc_series(market: str, weeks: int = CFTC_WEEKS) -> list[dict]:
    """One market's weekly Leveraged Funds history, newest first."""
    from urllib.parse import urlencode
    qs = urlencode({
        "$select": ("report_date_as_yyyy_mm_dd,open_interest_all,"
                    "lev_money_positions_long,lev_money_positions_short,lev_money_positions_spread,"
                    "traders_lev_money_long_all,traders_lev_money_short_all,"
                    "asset_mgr_positions_long,asset_mgr_positions_short,"
                    "dealer_positions_long_all,dealer_positions_short_all"),
        "$where": f"contract_market_name='{market}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(int(weeks)),
    })
    raw = _get(f"{CFTC_SOCRATA}?{qs}", TTL["cftc"], "cftc")
    if not raw:
        return []
    try:
        return parse_cftc(json.loads(raw.decode("utf-8", "replace")))
    except Exception:  # noqa: BLE001
        return []


def cftc_all() -> dict[str, dict]:
    """Every market in CFTC_MARKETS, keyed. Twelve Socrata calls a week."""
    out = {}
    for m in CFTC_MARKETS:
        series = cftc_series(m["market"])
        if series:
            out[m["key"]] = {**m, "series": series, "weeks": len(series),
                             "as_of": series[0]["date"]}
    return out


# ── FINRA consolidated short interest ───────────────────────────────────────

def finra_settlements(today: date, n: int = 8, lag_business_days: int = 8) -> list[str]:
    """The settlement dates whose short-interest report should be public.

    FINRA settles twice a month — the 15th and the last business day — and
    publishes about eight business days later. Dates are only offered when
    that lag has elapsed, because asking for one that has not been published
    returns an empty 204 that looks exactly like a broken source."""
    import market_calendar as _cal

    def on_or_before(d: date) -> date:
        while not _cal.is_session(d):
            d -= timedelta(days=1)
        return d

    dates, y, m = [], today.year, today.month
    for _ in range(n + 4):
        mid = on_or_before(date(y, m, 15))
        nxt = date(y + (m == 12), (m % 12) + 1, 1)
        eom = on_or_before(nxt - timedelta(days=1))
        for s in (eom, mid):
            probe = s
            for _ in range(lag_business_days):
                probe = _cal.next_session(probe)
            if probe <= today and s not in dates:
                dates.append(s)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return [d.isoformat() for d in sorted(dates, reverse=True)[:n]]


def parse_short_interest(text: str) -> dict[str, dict]:
    """FINRA's CSV, keyed by symbol.

    `days_to_cover` is short interest over average daily volume — how many
    days of normal trading it would take the shorts to buy back. It is the
    field that separates a crowded short from a merely large one."""
    import csv
    import io
    out: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text)):
        sym = (row.get("symbolCode") or "").strip().upper()
        if not sym:
            continue
        cur = _num_or_none(row.get("currentShortPositionQuantity"))
        prev = _num_or_none(row.get("previousShortPositionQuantity"))
        out[sym] = {
            "symbol": sym, "name": (row.get("issueName") or "").strip(),
            "short": cur, "short_prev": prev,
            "change": (cur - prev) if (cur is not None and prev is not None) else None,
            "change_pct": _num_or_none(row.get("changePercent")),
            "adv": _num_or_none(row.get("averageDailyVolumeQuantity")),
            "days_to_cover": _num_or_none(row.get("daysToCoverQuantity")),
            "settlement": (row.get("settlementDate") or "").strip(),
        }
    return out


def finra_short_interest(settlement: str, page_size: int = 5000, max_pages: int = 8) -> dict[str, dict]:
    """Every symbol's short interest for one settlement date.

    About 22,000 rows, served 5,000 at a time. A settlement not yet published
    answers 204 with no body, which reads here as an empty dict — the caller
    then says the report is not out yet rather than that shorts went to zero."""
    body = json.dumps({"limit": page_size, "offset": 0,
                       "compareFilters": [{"compareType": "EQUAL", "fieldName": "settlementDate",
                                           "fieldValue": settlement}]}).encode()
    out: dict[str, dict] = {}
    for page in range(max_pages):
        payload = json.loads(body)
        payload["offset"] = page * page_size
        raw = _post(FINRA_SI, json.dumps(payload).encode(),
                    f"finra_si:{settlement}:{page}", TTL["finra_si"])
        if not raw:
            break
        chunk = parse_short_interest(raw.decode("utf-8", "replace"))
        if not chunk:
            break
        out.update(chunk)
        if len(chunk) < page_size:
            break
    return out


# ── FINRA daily short volume (Reg SHO) ──────────────────────────────────────

def parse_short_volume(text: str) -> dict:
    """Short volume as a share of total volume, per symbol and market-wide.

    This is PRESSURE, not a position: it counts shares sold short during the
    session, most of which are covered the same day by market makers. It sits
    in the flow-proxy class for exactly that reason, and it exists here to
    fill the two-week gap between short-interest reports."""
    rows, tot_short, tot_all = {}, 0.0, 0.0
    for line in text.splitlines():
        p = line.split("|")
        if len(p) < 5 or p[0].startswith("Date") or not p[1].strip():
            continue
        sym = p[1].strip().upper()
        sh, ex, tv = _num_or_none(p[2]), _num_or_none(p[3]), _num_or_none(p[4])
        if sh is None or tv is None or tv <= 0:
            continue
        rows[sym] = {"symbol": sym, "short": sh, "exempt": ex or 0.0, "total": tv,
                     "share": sh / tv * 100.0}
        tot_short += sh
        tot_all += tv
    return {"rows": rows, "n": len(rows), "market_share": (tot_short / tot_all * 100.0) if tot_all else None,
            "date": (text.splitlines()[1].split("|")[0] if len(text.splitlines()) > 1 else None)}


def finra_short_volume(day: date) -> dict | None:
    raw = _get(FINRA_SHVOL.format(ymd=day.strftime("%Y%m%d")), TTL["shvol"], "shvol")
    if not raw:
        return None
    out = parse_short_volume(raw.decode("latin-1"))
    out["session"] = day.isoformat()
    return out if out["n"] else None


# ── OFR Hedge Fund Monitor (Form PF aggregates) ─────────────────────────────

OFR_SERIES = {
    "lev_top10": "FPF-ALLQHF_GAVN10_LEVERAGERATIO_AVERAGE",
    "lev_11_50": "FPF-ALLQHF_GAVN11TO50_LEVERAGERATIO_AVERAGE",
    "lev_51plus": "FPF-ALLQHF_GAVN51_LEVERAGERATIO_AVERAGE",
    "borrow_prime": "FPF-BORROW_PRIMEBROKER_SUM",
    "borrow_repo": "FPF-BORROW_REPO_SUM",
}


def ofr_leverage() -> dict:
    """Industry leverage and borrowing from SEC Form PF, via the Office of
    Financial Research. Quarterly and about five months behind — the only
    OFFICIAL answer to "are hedge funds levering up?" that exists, and slow
    enough that it is context for the weekly picture, never the weekly
    picture itself. The lag is reported with every figure."""
    raw = _get(OFR_FPF, TTL["ofr"], "ofr")
    if not raw:
        return {}
    try:
        data = json.loads(_maybe_gunzip(raw).decode("utf-8", "replace")).get("timeseries") or {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for key, sid in OFR_SERIES.items():
        node = data.get(sid) or {}
        pts = [(str(p[0])[:10], _num_or_none(p[1]))
               for p in (node.get("timeseries", {}).get("aggregation") or [])
               if p and p[1] is not None]
        if not pts:
            continue
        name = ((node.get("metadata") or {}).get("description") or {}).get("name")
        out[key] = {"series_id": sid, "name": name, "points": pts,
                    "as_of": pts[-1][0], "value": pts[-1][1],
                    "prev": pts[-2][1] if len(pts) > 1 else None,
                    "n": len(pts)}
    return out


# ══════════════════════════════════════════════════════════════════════════
# PHASE 3 — the prime-broker channel.
#
# Goldman Sachs, Morgan Stanley and JPMorgan tell their prime brokerage
# clients each week what hedge funds did. That research is not public; the
# wires quote it, and Reuters runs the quotes as its "HEDGE FLOW" series.
# This fetches the headlines. `hf_press.py` decides which of them say
# anything, and refuses the ones that do not.
# ══════════════════════════════════════════════════════════════════════════

TTL["press"] = 3 * 3600.0        # the notes land Monday and Tuesday mornings

PRIME_BROKER_QUERIES = (
    '"hedge funds" Goldman Sachs prime brokerage',
    'Reuters HEDGE FLOW hedge funds Goldman Sachs',
    '"hedge funds" "Morgan Stanley" prime brokerage positioning',
    '"hedge funds" JPMorgan positioning equities',
    'hedge funds net leverage gross exposure Goldman',
    'hedge funds short covering Goldman Sachs',
)


def prime_broker_news(queries: tuple = PRIME_BROKER_QUERIES) -> list[dict]:
    """Every headline the prime-broker queries return, deduplicated by title.

    One query is not enough: the wires phrase the same note six ways and
    Google's ranking drops some of them depending on the words asked for. A
    query that fails is skipped rather than losing the rest — this channel
    is corroboration, and a partial read of it is still worth having."""
    seen, out = set(), []
    for q in queries:
        try:
            items = news_items(q)
        except Exception:  # noqa: BLE001
            continue
        for it in items:
            key = (it.get("title") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(it)
    return out


# ══════════════════════════════════════════════════════════════════════════
# PHASE 4 — a manager's own posts on X, when a token exists.
#
# The design has always listed this as optional, and it stays optional here.
# Without a bearer token every function below returns nothing and says why,
# which is the state of most deployments. A post is a STATEMENT — a claim,
# never a position — and it is carried in the same shape as the RSS channel
# so `hf_watch` renders it identically and cannot mistake it for a filing.
# ══════════════════════════════════════════════════════════════════════════

X_SEARCH = "https://api.twitter.com/2/tweets/search/recent"
TTL["x"] = 30 * 60.0


def x_token() -> str:
    """The bearer token, under either name the app has used."""
    return (os.environ.get("X_BEARER_TOKEN", "").strip()
            or os.environ.get("TWITTER_BEARER_TOKEN", "").strip())


def x_available() -> bool:
    return bool(x_token())


def _default_x_fetch(url: str, token: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept-Encoding": "identity",
        "User-Agent": "JerryTrade dashboard (hedge fund intelligence)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read() if resp.status != 204 else b""


_X_FETCH = None


def configure_x(fetch_fn=None) -> None:
    """Injected so a test can exercise the parser without a token."""
    global _X_FETCH
    _X_FETCH = fetch_fn


def parse_x(raw: bytes, handle: str | None = None) -> list[dict]:
    """X search results in the same shape the RSS channel produces, so a
    post and a newsletter entry render through one code path."""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for t in (data.get("data") or []):
        text = re.sub(r"\s+", " ", str(t.get("text") or "")).strip()
        if not text:
            continue
        tid = t.get("id")
        out.append({"title": text[:180], "summary": text[:400],
                    "published": (t.get("created_at") or "")[:19] + "+00:00"
                                 if t.get("created_at") else None,
                    "link": f"https://x.com/i/web/status/{tid}" if tid else None,
                    "outlet": f"X · {handle}" if handle else "X"})
    return out


def x_statements(query: str, handle: str | None = None, max_results: int = 10) -> list[dict]:
    """A manager's recent posts, or nothing at all.

    Returns an empty list on every failure path — no token, a rate limit, a
    changed schema — because this channel is optional and a fund card must
    not break when an optional channel is quiet."""
    token = x_token()
    if not token or not query:
        return []
    url = (f"{X_SEARCH}?query={quote_plus(query)}"
           f"&max_results={max(10, min(int(max_results), 100))}"
           f"&tweet.fields=created_at")
    fetch = _X_FETCH or _default_x_fetch
    try:
        raw = fetch(url, token)
    except Exception:  # noqa: BLE001
        return []
    return parse_x(raw or b"", handle)
