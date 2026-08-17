"""Offering and dilution filings, read from SEC EDGAR.

Why this module exists: a stock that gaps down 20% premarket is very often
selling shares. Until now the app had no source for that, so those gaps
showed as UNTAGGED. EDGAR is the source of record — free, authoritative,
and live within minutes of acceptance — so the tag can be real instead of
guessed from a headline.

What is measured here and what is not:

  * form type, filing date, acceptance timestamp and 8-K item codes come
    straight from data.sec.gov. No interpretation, no scraping.
  * for TODAY's tag the filing's own cover page is read once to separate a
    stock sale from a bond sale. A utility selling notes is not dilution
    and is dropped rather than mislabeled.
  * historical tags are form-derived only — no document read — so the
    ambiguous forms (424B2/424B3/424B7/FWP, which are debt takedowns,
    merger prospectuses and resale registrations about as often as they
    are offerings) are simply left out of history. Better a missing tag
    than a wrong one.

Timezone note, verified against the data rather than assumed:
acceptanceDateTime is UTC despite its "Z"-less-than-obvious meaning.
Pinned three ways — Apple's 8-K item 2.02 lands at 20:30Z (its 4:30pm ET
release), JPMorgan's at 10:30Z (its 6:30am ET release), and across ~2,700
non-Section-16 filings the "filed the next business day" roll begins
exactly at 21:30Z, which is the SEC's 5:30pm ET cutoff.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                                   # pragma: no cover
    _ET = None

# SEC requires a descriptive User-Agent (see options_dashboard.load_ticker_index,
# which learned the same lesson). Browser-style agents are 403'd.
_UA = {"User-Agent": "WeeklyOptionsTimer admin@example.com",
       "Accept-Encoding": "identity"}     # urllib will not gunzip for us

_SUB_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# ── what each form means ────────────────────────────────────────────────────
# Selling shares NOW, off an already-effective registration.
_PRICED_FORMS = {"424B1", "424B4", "424B5"}
# Same family, but these are debt takedowns / merger prospectuses / resale
# registrations often enough that the form alone proves nothing. Tagged only
# when the document itself says what it is.
_PROOF_FORMS = {"424B2", "424B3", "424B7", "FWP"}
# Registering shares that CAN be sold — capacity, not a sale.
_SHELF_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR", "S-3MEF",
                "F-1", "F-1/A", "F-3", "F-3/A", "F-3ASR"}
# 8-K, Item 3.02: Unregistered Sales of Equity Securities — a PIPE or
# private placement. Equity by definition, so no document read is needed.
_ITEM_UNREGISTERED = "3.02"

_HIST_FORMS = _PRICED_FORMS | _SHELF_FORMS          # form-only is safe here

_SUB_TTL = 900.0          # 15 min: a 7am offering must not wait an hour
_TICKER_TTL = 7 * 86400.0
_DOC_CAP = 256            # filings are immutable; cache by accession
_MIN_GAP_S = 0.12         # SEC asks for <10 requests/second

_LOCK = threading.Lock()
_LAST_CALL = [0.0]
_SUB_CACHE: dict = {}     # SYMBOL -> (fetched_ts, rows)
_DOC_CACHE: dict = {}     # accession -> parsed cover-page facts
_FDA_CACHE: dict = {}     # SYMBOL -> {accession: verdict | None}
_FDA_LOADED: set = set()
_TICKERS: list = [0.0, {}]
_CIK_FN = None
_DATA_DIR = None


def configure(cik_fn=None, data_dir=None) -> None:
    """Inject the app's existing ticker->CIK lookup so this module does not
    re-download the 777KB SEC ticker file the dashboard already holds.

    data_dir gives the FDA reader somewhere to remember what each 8-K said.
    Filings never change, so a verdict — including "nothing here" — is worth
    keeping forever rather than re-reading the document every restart."""
    global _CIK_FN, _DATA_DIR
    _CIK_FN = cik_fn
    if data_dir:
        _DATA_DIR = Path(data_dir) / "sec_fda"
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:  # pragma: no cover
            _DATA_DIR = None


def available() -> bool:
    return not os.environ.get("JERRY_NO_NET")


# ── transport ───────────────────────────────────────────────────────────────

def _throttle() -> None:
    with _LOCK:
        wait = _MIN_GAP_S - (time.time() - _LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[0] = time.time()


def _fetch(url: str, limit: int | None = None, timeout: int = 12) -> bytes:
    _throttle()
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit) if limit else resp.read()


def _fetch_json(url: str) -> dict:
    return json.loads(_fetch(url).decode("utf-8", "replace"))


# ── ticker -> CIK ───────────────────────────────────────────────────────────

def cik_for(symbol: str) -> int | None:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    if _CIK_FN is not None:
        try:
            cik = _CIK_FN(sym)
            if cik:
                return int(cik)
        except Exception:
            pass
    if not available():
        return None
    if time.time() - _TICKERS[0] > _TICKER_TTL or not _TICKERS[1]:
        try:
            data = _fetch_json(_TICKERS_URL)
            _TICKERS[1] = {(v.get("ticker") or "").upper(): int(v.get("cik_str") or 0)
                           for v in data.values()}
            _TICKERS[0] = time.time()
        except Exception:
            return None
    return _TICKERS[1].get(sym) or None


# ── filings ─────────────────────────────────────────────────────────────────

def _accepted_et(raw: str) -> str | None:
    """acceptanceDateTime is UTC (see module docstring). Jerry reads the
    market in Eastern, so the label he sees is Eastern."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(str(raw)[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None
    dt = dt.replace(tzinfo=timezone.utc)
    return (dt.astimezone(_ET) if _ET else dt).isoformat(timespec="seconds")


def filings(symbol: str) -> list[dict]:
    """Recent filings for `symbol`, newest first, as flat rows. Cached 15
    minutes — long enough to be cheap across a 5-minute scan cycle, short
    enough that a filing accepted at 7:05am shows up in the 7:20am scan."""
    sym = (symbol or "").upper().strip()
    if not sym or not available():
        return []
    hit = _SUB_CACHE.get(sym)
    if hit and time.time() - hit[0] < _SUB_TTL:
        return hit[1]
    cik = cik_for(sym)
    if not cik:
        return []
    try:
        recent = (_fetch_json(_SUB_URL.format(cik=cik))
                  .get("filings", {}).get("recent", {}))
    except Exception:
        return []
    forms = recent.get("form") or []
    rows = []
    for i in range(len(forms)):
        try:
            acc = (recent["accessionNumber"][i] or "")
            rows.append({
                "form": (forms[i] or "").upper().strip(),
                "date": str(recent["filingDate"][i])[:10],
                "accepted": _accepted_et(recent["acceptanceDateTime"][i]),
                "items": (recent.get("items") or [""] * len(forms))[i] or "",
                "accession": acc,
                "url": _ARCHIVE.format(
                    cik=cik, acc=acc.replace("-", ""),
                    doc=(recent.get("primaryDocument") or [""] * len(forms))[i]),
            })
        except Exception:
            continue
    rows.sort(key=lambda r: (r["date"], r["accepted"] or ""), reverse=True)
    _SUB_CACHE[sym] = (time.time(), rows)
    return rows


# ── reading the filing itself ───────────────────────────────────────────────

_TAGS = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_ANY_TAG = re.compile(r"(?s)<[^>]+>")


def _plain(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace")
    s = _ANY_TAG.sub(" ", _TAGS.sub(" ", s))
    return re.sub(r"\s+", " ", html.unescape(s))


_SIZE_DOLLARS = re.compile(
    r"aggregate offering price of(?: up to)?\s*\$\s?([\d]{1,3}(?:,\d{3}){2,})",
    re.I)
_SIZE_SHARES = re.compile(
    r"([\d]{1,3}(?:,\d{3}){1,})\s+shares(?:\s+of)?(?:\s+our)?\s+common stock",
    re.I)


def _size_label(txt: str) -> str | None:
    """The deal size off the cover page — quoted, never computed, and only
    when the number is bound to the offering by the sentence around it. A
    stray dollar figure from a fee table is worse than no number at all."""
    m = _SIZE_DOLLARS.search(txt)
    if m:
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            v = 0.0
        if v >= 1e9:
            return f"${v / 1e9:.1f}B"
        if v >= 1e6:
            return f"${v / 1e6:.0f}M"
    m = _SIZE_SHARES.search(txt)
    if m:
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            return None
        if n >= 1e6:
            return f"{n / 1e6:.1f}M shares"
        if n >= 1e5:
            return f"{n / 1e3:.0f}K shares"
    return None


def read_cover(url: str, accession: str = "") -> dict:
    """What the filing says about itself. Only the first stretch of the
    document is read — a prospectus cover page names the security, the
    size and the manner of sale in its opening lines."""
    key = accession or url
    if key in _DOC_CACHE:
        return _DOC_CACHE[key]
    out = {"security": None, "atm": False, "preliminary": False,
           "resale": False, "merger": False, "selling": False, "size": None}
    if not url or not available():
        return out
    try:
        txt = _plain(_fetch(url, limit=180_000))
    except Exception:
        return out
    # Only the cover page counts. A capitalization table or a risk factor
    # deep in a stock prospectus will mention convertible notes the company
    # issued years ago — reading the whole document would call every deal
    # convertible.
    head = txt[:9000]
    low = head.lower()

    convertible = ("convertible note" in low or "convertible senior note" in low
                   or "convertible preferred" in low)
    equity = any(p in low for p in (
        "shares of common stock", "shares of our common stock", "common shares",
        "ordinary shares", "american depositary shares", "warrants to purchase"))
    debt = ("aggregate principal amount" in low
            or re.search(r"notes due \d{4}", low) is not None
            or "debentures" in low)

    if convertible:
        out["security"] = "convertible"     # debt on paper, dilution in fact
    elif equity and not debt:
        out["security"] = "equity"
    elif debt:
        out["security"] = "debt"
    out["atm"] = ("at the market" in low or "at-the-market" in low
                  or "sales agreement" in low)
    out["preliminary"] = "information in this preliminary prospectus" in low
    out["resale"] = ("selling stockholder" in low
                     or "selling securityholder" in low)
    out["merger"] = ("merger agreement" in low
                     or "letter to stockholders" in low)
    # "We are offering" — the company itself is the seller, today. Without
    # this, a resale prospectus reads like a fresh deal.
    out["selling"] = any(p in low for p in (
        "we are offering", "we are selling", "offered hereby",
        "aggregate offering price", "we may offer and sell"))
    out["size"] = _size_label(head)
    if len(_DOC_CACHE) > _DOC_CAP:
        _DOC_CACHE.clear()
    _DOC_CACHE[key] = out
    return out


# ── classification ──────────────────────────────────────────────────────────

def _from_form(row: dict) -> tuple[str, str] | None:
    """Kind + label from the form type alone. Everything here is a fact
    about the filing, never an inference about the company."""
    form, items = row.get("form", ""), row.get("items", "")
    if form == "8-K":
        if _ITEM_UNREGISTERED in items:
            return ("OFFERING", "Private placement — unregistered stock sale")
        return None
    if form in _PRICED_FORMS:
        return ("OFFERING", f"{form} prospectus filed")
    if form in _SHELF_FORMS:
        auto = "Automatic shelf" if form.endswith("ASR") else "Shelf"
        if form.startswith(("S-1", "F-1")):
            auto = "Registration statement"
            return ("DILUTION", f"{auto} filed ({form})")
        return ("DILUTION", f"{auto} registration filed ({form})")
    if form in _PROOF_FORMS:
        return ("OFFERING", f"{form} prospectus filed")
    return None


def _label_from_cover(kind: str, form: str, cover: dict) -> tuple[str, str] | None:
    """Refine (or reject) the form-only reading using the filing's own cover
    page. A bond deal is not dilution — it is dropped, not relabeled."""
    sec = cover.get("security")
    size = cover.get("size")
    tail = f" — {size}" if size else ""
    if cover.get("merger"):
        return None                     # share issuance, but this app has no M&A tag
    if sec == "debt":
        return None                     # notes, not shares: nobody is diluted
    if kind == "DILUTION":
        return None                     # shelf labels already come from the form
    if form in _PROOF_FORMS and not cover.get("selling"):
        # 424B3/424B7 are usually somebody ELSE selling shares they already
        # hold. That is supply hitting the tape, not a raise — say so.
        if cover.get("resale"):
            return ("DILUTION", f"Resale registration — selling stockholders{tail}")
        return None
    if sec == "convertible":
        return ("OFFERING", f"Convertible notes offering{tail}")
    if sec == "equity":
        if cover.get("atm"):
            return ("OFFERING", f"At-the-market stock program{tail}")
        state = "announced" if cover.get("preliminary") else "priced"
        return ("OFFERING", f"Stock offering {state}{tail}")
    if form in _PROOF_FORMS:
        return None                     # ambiguous form, no proof: stay quiet
    return None


def _fresh(row: dict, days: tuple) -> bool:
    return str(row.get("date", ""))[:10] in days


def latest_event(symbol: str, days: tuple) -> dict | None:
    """The freshest offering/dilution filing for `symbol` inside `days`,
    ready to use as a gap catalyst. The winner's cover page is read once so
    the label says what was actually sold."""
    if not available():
        return None
    for row in filings(symbol):
        if not _fresh(row, days):
            continue
        base = _from_form(row)
        if not base:
            continue
        kind, label = base
        if row.get("form") != "8-K":
            cover = read_cover(row.get("url", ""), row.get("accession", ""))
            refined = _label_from_cover(kind, row.get("form", ""), cover)
            if refined is None and (row.get("form") in _PROOF_FORMS
                                    or cover.get("security") == "debt"
                                    or cover.get("merger")):
                continue                # proved it is not dilution, or unproven
            if refined:
                kind, label = refined
        return {"kind": kind, "label": label, "form": row.get("form"),
                "date": row.get("date"), "accepted": row.get("accepted"),
                "url": row.get("url")}
    return None


def event_dates(symbol: str) -> dict:
    """{filing date -> 'OFFERING' | 'DILUTION'} across the symbol's filing
    history, for tagging past gap days. Form-derived only: the ambiguous
    forms are excluded rather than read, because confirming years of
    history one document at a time is not worth the requests."""
    out: dict = {}
    for row in filings(symbol):
        form = row.get("form", "")
        d = str(row.get("date", ""))[:10]
        if not d:
            continue
        if form == "8-K" and _ITEM_UNREGISTERED in (row.get("items") or ""):
            out.setdefault(d, "OFFERING")
        elif form in _HIST_FORMS:
            kind = "OFFERING" if form in _PRICED_FORMS else "DILUTION"
            # a priced deal outranks a shelf filed the same day
            if out.get(d) != "OFFERING":
                out[d] = kind
    return out


# ── FDA decisions, read out of the company's own 8-K ────────────────────────
#
# The FDA publishes approvals in openFDA, but on a weekly lag and under
# sponsor names that do not map cleanly to tickers (big pharma files through
# subsidiaries). It does not publish rejections at all — a Complete Response
# Letter reaches the market only because the company discloses it. Both
# halves of what Jerry asked for live in the same place: the 8-K the company
# files when it happens. That is same-morning, authoritative, and needs no
# name matching, because the filing IS the company's.
#
# The whole submission (`<accession>.txt`) carries the item body AND the
# press-release exhibit in one fetch, which is where the plain-English
# sentence lives.

_FDA_TXT = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{accdash}.txt"
_ITEM_EARNINGS = "2.02"

_FDA_APPROVE = [
    r"(?:u\.s\.\s+)?(?:fda|food and drug administration)\s+(?:has |have |had )?approved",
    r"approved by the (?:u\.s\.\s+)?(?:fda|food and drug administration)",
    r"(?:received|obtained|granted|announced)[^.;]{0,50}\bfda approval\b",
    r"\bfda\b[^.;]{0,50}\bapproval of (?:its|our|the)\b",
    r"approval (?:of|for) (?:its|our|the) (?:new drug application|biologics "
    r"license application|nda|bla|supplemental|premarket approval)",
]
_FDA_REJECT = [
    r"(?:received|receipt of|issued|delivered)[^.;]{0,60}complete response letter",
    r"complete response letter\s+(?:from|regarding|for|related)",
    r"complete response letter\s*(?:was\s+)?received",
    r"(?:fda|agency)[^.;]{0,40}(?:declined|refused) to approve",
    r"refus(?:ed|al) to file letter",
]
# Every filing warns about what the FDA *might* do. A hedged sentence is a
# risk factor, not an event.
_HEDGE = ("may ", "could ", "risk", " if ", "whether", "potential", "expect",
          "anticipat", "unable to", "failure to", "no assurance", "believe",
          "intend", "plan to", "seek", "would ", "goal", "hope")
# Permission to run a study is not permission to sell a product.
_NOT_MARKETING = ("approval to initiate", "approval to conduct", "ide approval",
                  "approval of the ind", "investigational new drug",
                  "clinical trial application", "protocol", "trial with")
_RECAP = ("previously disclosed", "previously announced", "as announced",
          "prior to", "last year")
_MONTHS = ("january|february|march|april|may|june|july|august|september|"
           "october|november|december")
_DATE_RE = re.compile(rf"({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I)
_MON_N = {m: i + 1 for i, m in enumerate(_MONTHS.split("|"))}
_PAREN = re.compile(r"\((?:[^()]{0,45})\)")
# "the U.S. Food and Drug Administration has approved" is ONE sentence — a
# naive split on "." truncates the quote to "...the U.S" and throws away the
# part Jerry needs to read.
_SENT = re.compile(r"(?<![A-Z])(?<!Inc)(?<!Ltd)(?<!Corp)(?<!No)(?<!Dr)"
                   r"(?<!Mr)(?<!Ms)(?<!Jr)(?<!St)[.;•]\s")
_STALE_DAYS = 10


def _sentence_at(text: str, pos: int) -> str:
    start = 0
    for m in _SENT.finditer(text, 0, pos):
        start = m.end()
    m = _SENT.search(text, pos)
    return text[start:(m.start() if m else min(len(text), pos + 400))]


def _is_recap(sentence: str, filed: str | None) -> bool:
    """True when the sentence pins the decision to a date well before the
    filing. A business update that mentions June's rejection in August is
    not August's news, and must not tag an August gap."""
    if not filed:
        return False
    try:
        fd = date.fromisoformat(filed)
    except ValueError:
        return False
    seen = []
    for m in _DATE_RE.finditer(sentence):
        try:
            seen.append(date(int(m.group(3)), _MON_N[m.group(1).lower()],
                             int(m.group(2))))
        except (ValueError, KeyError):
            continue
    return bool(seen) and all((fd - d).days > _STALE_DAYS for d in seen)


def classify_fda(text: str, items: str = "", filed: str | None = None):
    """(kind, quoted sentence) for an FDA decision announced in this filing,
    or (None, "") — which is the answer for the overwhelming majority of
    filings and always the answer when anything is uncertain."""
    if _ITEM_EARNINGS in (items or ""):
        # a quarterly release recaps the year's approvals; the gap that day
        # is the earnings gap, and earnings already outranks this tag
        return None, ""
    flat = _PAREN.sub(" ", text)          # drop ("FDA")-style defined terms
    low = flat.lower()
    for kind, pats in (("FDA REJECTION", _FDA_REJECT),
                       ("FDA APPROVAL", _FDA_APPROVE)):
        for pat in pats:
            for m in re.finditer(pat, low):
                s = _sentence_at(low, m.start())
                if any(h in s for h in _HEDGE) or any(h in s for h in _RECAP):
                    continue
                if kind == "FDA APPROVAL" and any(h in s for h in _NOT_MARKETING):
                    continue
                if _is_recap(s, filed):
                    continue
                return kind, re.sub(r"\s+", " ", _sentence_at(flat, m.start())).strip()
    return None, ""


def _fda_store(symbol: str) -> dict:
    sym = symbol.upper()
    if sym in _FDA_LOADED:
        return _FDA_CACHE.setdefault(sym, {})
    _FDA_LOADED.add(sym)
    cache = _FDA_CACHE.setdefault(sym, {})
    if _DATA_DIR:
        p = _DATA_DIR / f"{''.join(c for c in sym if c.isalnum() or c in '-_.')}.json"
        try:
            if p.exists():
                loaded = json.loads(p.read_text())
                if isinstance(loaded, dict):
                    cache.update(loaded)
        except Exception:
            pass
    return cache


def _fda_save(symbol: str) -> None:
    if not _DATA_DIR:
        return
    sym = symbol.upper()
    p = _DATA_DIR / f"{''.join(c for c in sym if c.isalnum() or c in '-_.')}.json"
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_FDA_CACHE.get(sym, {}), separators=(",", ":")))
        tmp.replace(p)
    except Exception:  # pragma: no cover
        pass


def read_fda(symbol: str, row: dict) -> dict | None:
    """What one 8-K says about an FDA decision. Cached by accession — the
    document is immutable, so a "nothing here" verdict is worth keeping too."""
    acc = row.get("accession") or ""
    cache = _fda_store(symbol)
    if acc in cache:
        return cache[acc]
    if not available():
        return None          # unknown, not "nothing here" — never cache this
    verdict = None
    if row.get("form") == "8-K":
        cik = cik_for(symbol)
        if cik:
            url = _FDA_TXT.format(cik=cik, acc=acc.replace("-", ""), accdash=acc)
            try:
                text = _plain(_fetch(url, limit=400_000, timeout=15))
                kind, quote = classify_fda(text, row.get("items", ""),
                                           row.get("date"))
                if kind:
                    verdict = {"kind": kind, "quote": quote[:400],
                               "date": row.get("date"),
                               "accepted": row.get("accepted"),
                               "url": row.get("url")}
            except Exception:
                return None            # unread, not "nothing" — try again later
    cache[acc] = verdict
    _fda_save(symbol)
    return verdict


def _is_8k(row: dict) -> bool:
    return row.get("form") == "8-K" and _ITEM_EARNINGS not in (row.get("items") or "")


def _next_business(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def moves_session(row: dict) -> str | None:
    """Which session's gap this filing could explain, from the acceptance
    clock rather than the filing date.

    A 7am filing moves that morning. One accepted after the close moves the
    next morning — and EDGAR stamps such filings with the next business day
    already, so going by filing date alone would point a day too far.
    """
    acc = row.get("accepted") or ""
    if not acc:
        return str(row.get("date", ""))[:10] or None
    try:
        dt = datetime.fromisoformat(acc)
    except ValueError:
        return str(row.get("date", ""))[:10] or None
    if dt.hour >= 16:
        return _next_business(dt.date()).isoformat()
    return dt.date().isoformat()


def latest_fda(symbol: str, days: tuple, budget: int = 4) -> dict | None:
    """The FDA decision announced in an 8-K inside `days`, if there is one."""
    if not available():
        return None
    reads = 0
    for row in filings(symbol):
        if str(row.get("date", ""))[:10] not in days or not _is_8k(row):
            continue
        cached = _fda_store(symbol).get(row.get("accession") or "", "miss")
        if cached == "miss":
            if reads >= budget:
                break
            reads += 1
        hit = read_fda(symbol, row)
        if hit:
            return hit
    return None


def fda_dates(symbol: str, dates, budget: int = 8) -> dict:
    """{gap date -> 'FDA APPROVAL' | 'FDA REJECTION'} for the dates given.

    Only days that actually have an 8-K cost a read, the budget caps how
    many new documents one pass will open, and every verdict is remembered,
    so a symbol's history fills in over a few scans instead of stalling one.
    """
    want = {str(d)[:10] for d in (dates or []) if d}
    if not want or not available():
        return {}
    out: dict = {}
    reads = 0
    for row in filings(symbol):
        if not _is_8k(row):
            continue
        session = moves_session(row)
        if session not in want or session in out:
            continue
        if (row.get("accession") or "") not in _fda_store(symbol):
            if reads >= budget:
                continue
            reads += 1
        verdict = read_fda(symbol, row)
        if verdict:
            out[session] = verdict["kind"]
    return out


def coverage(symbol: str) -> dict:
    """How far back the filing history actually reaches, so the UI can say
    so instead of implying the whole daily window is covered."""
    rows = filings(symbol)
    if not rows:
        return {"n": 0, "first": None, "last": None}
    return {"n": len(rows), "first": rows[-1]["date"], "last": rows[0]["date"]}
