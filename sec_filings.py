"""What a company told the SEC, turned into a reason a stock gapped.

Why this module exists: the biggest premarket movers happen because the
company filed something — it is selling shares, it is being bought, the FDA
answered, a trial read out, the auditors are gone. EDGAR is the source of
record for all of it: free, authoritative, and live within minutes of
acceptance. Several of these have no other source at all. The FDA never
publishes a Complete Response Letter; the company does.

Three layers, cheapest first:

  * **metadata** — form type, filing date, acceptance timestamp and 8-K
    item numbers, straight from data.sec.gov. Some of it is already an
    answer: item 1.03 is bankruptcy, item 3.01 is a delisting notice, a
    SC 14D9 means somebody is tendering for the shares. No interpretation,
    no document, no extra request.
  * **cover page** — read once for an offering, to separate a stock sale
    from a bond sale. A utility selling notes is not dilution and is
    dropped rather than mislabeled.
  * **the filing's own words** — for what metadata cannot say: which side
    of a merger the company is on, whether a trial met its endpoint,
    whether the FDA approved or refused. Every rule here has to clear the
    same gates, because every filing mentions these things constantly:
    not hedged, not a recap of something dated weeks ago, not permission
    to run a study rather than to sell a product.

Historical offering tags stay form-derived — the ambiguous forms
(424B2/424B3/424B7/FWP, which are debt takedowns, merger prospectuses and
resale registrations about as often as they are offerings) are left out of
history entirely. Better a missing tag than a wrong one, everywhere.

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
# Filers split words across styling tags — "Jul</span>y 30, 2026" is real,
# from Aethlon's reverse-split 8-K. Every tag becoming a space turns that
# into "Jul y 30" in a sentence Jerry reads.
#
# Closed up only for a SINGLE inline tag sitting between two word
# characters. A close-then-open pair — "approved</span><span>the" — is the
# seam between two separately styled words, and joining those is far worse
# than the blemish being fixed: "authorizedthe repurchase" would stop the
# classifier's own patterns from matching. One tag splits a word; two tags
# separate them.
_MIDWORD_TAG = re.compile(
    r"(?i)(?<=\w)</?(?:span|font|b|i|em|strong|u|sup|sub|small|ix:[a-z:]+)"
    r"(?:\s[^>]*)?>(?=\w)")


def _plain(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace")
    s = _TAGS.sub(" ", s)
    s = _ANY_TAG.sub(" ", _MIDWORD_TAG.sub("", s))
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
        return None                     # shares issued in a deal — the M&A
        #                                 tagger handles that, not this one
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

# ── the taxonomy that costs nothing ─────────────────────────────────────────
#
# An 8-K's item numbers and a filing's form type are already in the
# submissions feed, and several of them mean exactly one thing. Bankruptcy,
# a delisting notice, a restatement, a tender offer for the company's shares
# — all of it is free, dated, and needs no document read at all.

_ITEM_TAGS = {
    "1.03": ("BANKRUPTCY", "Bankruptcy or receivership (8-K item 1.03)"),
    "5.01": ("BUYOUT", "Control of the company changed hands (8-K item 5.01)"),
    "2.01": ("DEAL CLOSED", "Completed an acquisition or disposition (8-K item 2.01)"),
    "3.01": ("DELISTING NOTICE",
             "Exchange notice: a continued-listing rule is not being met (8-K item 3.01)"),
    "4.02": ("RESTATEMENT",
             "Previously issued financial statements can no longer be relied on (8-K item 4.02)"),
    "4.01": ("AUDITOR CHANGE", "Changed accountants (8-K item 4.01)"),
    "2.05": ("RESTRUCTURING", "Costs of an exit or disposal plan (8-K item 2.05)"),
    "2.06": ("IMPAIRMENT", "Material asset impairment (8-K item 2.06)"),
    "5.02": ("LEADERSHIP CHANGE", "Officer or director change (8-K item 5.02)"),
}
# Forms only a company in a deal ever files.
_FORM_TAGS = (
    (("SC 14D9", "SC TO-T", "SC TO-C"),
     ("BUYOUT", "Tender offer outstanding for this company's shares")),
    (("DEFM14A", "PREM14A"), ("MERGER VOTE", "Shareholder vote on a merger")),
)
# Matched whole, not by prefix, because the amendments mean something else.
# A first SC 13D is somebody declaring an activist position; the ninety-odd
# SC 13D/A amendments behind it are that holder trimming, adding or leaving,
# and the form alone cannot say which — so only the original is tagged.
_FORM_EXACT = {
    "SC 13D": ("ACTIVIST STAKE",
               "A holder crossed 5% and filed with intent to influence "
               "the company (Schedule 13D)"),
    "SCHEDULE 13D": ("ACTIVIST STAKE",
                     "A holder crossed 5% and filed with intent to influence "
                     "the company (Schedule 13D)"),
    "NT 10-K": ("LATE FILING",
                "Notified the SEC the annual report will be late (Form NT 10-K)"),
    "NT 10-Q": ("LATE FILING",
                "Notified the SEC the quarterly report will be late (Form NT 10-Q)"),
}
# When one filing carries several of these, the most consequential wins.
_RANK = ["LEADERSHIP CHANGE", "AUDITOR CHANGE", "RESTRUCTURING", "IMPAIRMENT",
         "INSIDER SELLING", "INSIDER BUYING", "BUYBACK",
         "DEAL CLOSED", "MERGER VOTE", "INDEX ADD", "INDEX DROP",
         "GUIDANCE RAISED", "GUIDANCE CUT",
         "LATE FILING", "DELISTING NOTICE", "SHORT REPORT",
         "ACTIVIST STAKE", "REVERSE SPLIT",
         "RESTATEMENT", "MERGER DEAL", "TRIAL SUCCESS", "TRIAL FAILURE",
         "FDA APPROVAL", "FDA REJECTION", "BUYOUT", "BANKRUPTCY"]
# Above this, reading the document cannot say anything more important.
_STRONG = {"BANKRUPTCY", "BUYOUT"}
# A pending deal fixes the price the stock trades to, which is exactly the
# situation where its own gap history stops describing it.
_PENDING_DEAL = {"BUYOUT", "MERGER DEAL", "MERGER VOTE"}
# A reverse split restates every historical price and share count at once.
# The gap statistics underneath are still arithmetic on the old scale.
_RESCALES = {"REVERSE SPLIT"}


def _rank(kind: str | None) -> int:
    return _RANK.index(kind) if kind in _RANK else -1


def outranks_offering(kind: str | None) -> bool:
    """Whether this event explains a gap better than a share sale does. An
    officer change or a restructuring charge does not; a company that cannot
    file its financials on time, a delisting notice, a restatement or a deal
    does."""
    return _rank(kind) >= _RANK.index("LATE FILING")


def pins_the_price(kind: str | None) -> bool:
    return kind in _PENDING_DEAL


def rescales_history(kind: str | None) -> bool:
    """Whether this event changed the price scale itself, which makes the
    stock's own gap history arithmetic on numbers that no longer exist."""
    return kind in _RESCALES

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
# Trial readouts move a biotech harder than most approvals do.
_TRIAL_FAIL = [
    r"did not (?:meet|achieve)[^.;]{0,40}\b(?:primary|co-primary) endpoint",
    r"failed to (?:meet|achieve)[^.;]{0,40}\b(?:primary|co-primary) endpoint",
    r"missed (?:its |the )?primary endpoint",
    r"(?:discontinu\w+|halt\w+|terminat\w+)[^.;]{0,50}\b(?:phase \d|trial|study)\b"
    r"[^.;]{0,60}(?:futility|safety|lack of efficacy)",
]
_TRIAL_WIN = [
    r"(?:met|achieved)[^.;]{0,40}\b(?:primary|co-primary) endpoint",
    r"statistically significant[^.;]{0,60}\bprimary endpoint",
]
# Being bought is a different animal from buying: the target's price pins to
# the deal, the acquirer's does not.
_TARGET = [
    r"acquisition of the company by",
    r"(?:will|to) be acquired by",
    r"to acquire all (?:of )?the (?:company'?s? )?(?:issued and )?outstanding shares",
    r"tender offer[^.;]{0,80}all (?:of )?the outstanding shares",
    r"per share, net to the seller in cash",
]
_ACQUIRER = [
    r"the company (?:has agreed|entered into[^.;]{0,60}) to acquire",
    r"(?:completed|closed)[^.;]{0,40}acquisition of",
]
_MERGER_ANY = [
    r"agreement and plan of merger",
    r"definitive merger agreement",
    r"merger agreement",
]
# "$77.00 per share" — the number the stock is now pinned to.
_DEAL_PRICE = re.compile(r"\$\s?(\d{1,4}(?:\.\d{2})?)\s+(?:in cash )?per share", re.I)
# Companies do respond to short-seller reports on the record — usually a
# day or two after the gap, which the recap guard handles.
_SHORT_REPORT = [
    r"short[- ]seller(?:'s)? report",
    r"report (?:published|issued) by[^.;]{0,40}short",
    r"responds? to (?:the )?(?:recent )?short[- ]seller",
]
# Guidance moved OUTSIDE a quarterly release is a preannouncement, and
# those gap hard. (Quarterly 8-Ks are skipped before any of this runs.)
_GUIDE_UP = [
    r"(?:rais\w+|increas\w+|rais\w+ its)[^.;]{0,50}\b(?:guidance|outlook|forecast)\b",
    r"\b(?:guidance|outlook)\b[^.;]{0,40}(?:raised|increased)",
]
_GUIDE_DOWN = [
    r"(?:lower\w+|reduc\w+|cut\w*|withdraw\w+|suspend\w+)[^.;]{0,50}"
    r"\b(?:guidance|outlook|forecast)\b",
    r"\b(?:guidance|outlook)\b[^.;]{0,40}(?:lowered|reduced|withdrawn)",
]
# A board authorising a buyback is the company bidding for its own stock.
# The trap is that most repurchase language is housekeeping: a Rule 10b5-1
# plan set up to execute a program authorised months ago, or a quarterly
# report of shares already bought. Only a new authorisation is news.
_BUYBACK = [
    r"(?:board|directors)[^.;]{0,60}(?:approv\w+|authoriz\w+|adopt\w+)"
    r"[^.;]{0,60}(?:share |stock |common stock )?repurchase",
    r"(?:approv\w+|authoriz\w+)[^.;]{0,40}repurchase[^.;]{0,40}"
    r"(?:program|plan|of up to|up to)",
    r"new[^.;]{0,20}(?:share |stock )?repurchase (?:program|authorization)",
]
# Executing an existing programme, or reporting the balance left on one, is
# not authorising one. "remaining" is doing the most work here: Tyler's June
# 8-K reports "we have remaining authorization ... to repurchase up to
# $332.7 million", which reads exactly like an authorisation and is a
# balance. Blocking the word costs the rare filing that announces a new
# programme alongside the old one's remainder, which is the safe direction.
_NOT_NEW_BUYBACK = ("10b5-1", "rule 10b5-1", "previously authorized",
                    "previously approved", "existing repurchase",
                    "under the program", "remaining")
# "a 1-for-5 reverse stock split" — for a small cap this is often the whole
# reason the price moved, and it rewrites every historical price at once.
_REVERSE_SPLIT = [
    r"reverse (?:stock |share )?split",
]
_NOT_SPLIT = ("no reverse", "not effect", "does not intend", "reverse split ratio to be",
              "forward split")
_SPLIT_RATIO = re.compile(
    r"(?:1|one)[\s-]*(?:for|:)[\s-]*(\d{1,3})(?:\.\d+)?\s*(?:reverse)?", re.I)
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


def classify_filing(text: str, items: str = "", filed: str | None = None):
    """(kind, quoted sentence) for an event this filing is announcing, or
    (None, "") — which is the answer for the overwhelming majority of
    filings and always the answer when anything is uncertain.

    Rules run most-consequential first, and every one of them has to clear
    the same three gates: not hedged (every filing warns about what MIGHT
    happen), not a recap of something dated weeks ago, and — for approvals —
    not permission to run a study rather than to sell a product.
    """
    if _ITEM_EARNINGS in (items or ""):
        # a quarterly release recaps the year's news; the gap that day is
        # the earnings gap, and earnings already outranks every tag here
        return None, ""
    flat = _PAREN.sub(" ", text)          # drop ("FDA")-style defined terms
    low = flat.lower()

    def hit(pats, kind, extra_block=()):
        for pat in pats:
            for m in re.finditer(pat, low):
                s = _sentence_at(low, m.start())
                if any(h in s for h in _HEDGE) or any(h in s for h in _RECAP):
                    continue
                if any(h in s for h in extra_block):
                    continue
                if _is_recap(s, filed):
                    continue
                return kind, re.sub(r"\s+", " ",
                                    _sentence_at(flat, m.start())).strip()
        return None, ""

    for pats, kind, block in (
            (_TARGET, "BUYOUT", ()),
            (_FDA_REJECT, "FDA REJECTION", ()),
            (_FDA_APPROVE, "FDA APPROVAL", _NOT_MARKETING),
            (_TRIAL_FAIL, "TRIAL FAILURE", ()),
            (_TRIAL_WIN, "TRIAL SUCCESS", ()),
            (_SHORT_REPORT, "SHORT REPORT", ()),
            (_REVERSE_SPLIT, "REVERSE SPLIT", _NOT_SPLIT),
            (_GUIDE_DOWN, "GUIDANCE CUT", ()),
            (_GUIDE_UP, "GUIDANCE RAISED", ()),
            (_ACQUIRER, "DEAL CLOSED", ()),
            (_BUYBACK, "BUYBACK", _NOT_NEW_BUYBACK),
            (_MERGER_ANY, "MERGER DEAL", ())):
        kind, quote = hit(pats, kind, block)
        if kind:
            if kind == "BUYOUT":
                # The number that matters most is the price the stock is now
                # pinned to, and it is often a sentence or two away from the
                # one that proves it is a buyout — so look across the filing.
                price = _DEAL_PRICE.search(flat[:60_000])
                if price:
                    return kind, f"${price.group(1)} per share · {quote}"
            if kind == "REVERSE SPLIT":
                # The ratio is the whole story: 1-for-5 means the quoted
                # price quintuples overnight for arithmetic reasons alone.
                ratio = _SPLIT_RATIO.search(quote)
                if ratio:
                    return kind, f"1-for-{ratio.group(1)} · {quote}"
            return kind, quote
    return None, ""


# the old name, kept because the FDA rules are the ones worth calling out
classify_fda = classify_filing


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


def tag_from_metadata(row: dict) -> dict | None:
    """What the form type and 8-K item numbers alone say happened. Free —
    this data is already in the submissions feed — and unambiguous, which is
    why only the item codes that mean exactly one thing are listed."""
    form = (row.get("form") or "").upper()
    exact = _FORM_EXACT.get(form)
    if exact:
        return {"kind": exact[0], "label": exact[1], "quote": None,
                "date": row.get("date"), "accepted": row.get("accepted"),
                "url": row.get("url")}
    for prefixes, (kind, label) in _FORM_TAGS:
        if form.startswith(prefixes):
            return {"kind": kind, "label": label, "quote": None,
                    "date": row.get("date"), "accepted": row.get("accepted"),
                    "url": row.get("url")}
    if form != "8-K":
        return None
    items = [i.strip() for i in (row.get("items") or "").split(",") if i.strip()]
    best = None
    for code in items:
        tag = _ITEM_TAGS.get(code)
        if tag and (best is None or _rank(tag[0]) > _rank(best[0])):
            best = tag
    if not best:
        return None
    return {"kind": best[0], "label": best[1], "quote": None,
            "date": row.get("date"), "accepted": row.get("accepted"),
            "url": row.get("url")}


def read_filing_event(symbol: str, row: dict, allow_read: bool = True) -> dict | None:
    """What one filing says happened — item codes first (free), then the
    document itself when it could say something more consequential.

    Verdicts are cached by accession. The document is immutable, so even a
    "nothing here" answer is worth keeping; an unread one never is.
    """
    meta = tag_from_metadata(row)
    if meta and meta["kind"] in _STRONG:
        return meta                      # nothing in the text can outrank this
    if row.get("form") != "8-K" or _ITEM_EARNINGS in (row.get("items") or ""):
        return meta
    acc = row.get("accession") or ""
    cache = _fda_store(symbol)
    if acc in cache:
        return cache[acc] or meta
    if not allow_read or not available():
        return meta          # unknown, not "nothing here" — never cache this
    verdict = None
    cik = cik_for(symbol)
    if cik:
        url = _FDA_TXT.format(cik=cik, acc=acc.replace("-", ""), accdash=acc)
        try:
            text = _plain(_fetch(url, limit=400_000, timeout=15))
            kind, quote = classify_filing(text, row.get("items", ""),
                                          row.get("date"))
            if kind:
                verdict = {"kind": kind, "quote": quote[:400], "label": None,
                           "date": row.get("date"),
                           "accepted": row.get("accepted"),
                           "url": row.get("url")}
        except Exception:
            return meta                # unread — try again on a later pass
    cache[acc] = verdict
    _fda_save(symbol)
    if verdict and _rank(verdict["kind"]) >= _rank(meta["kind"] if meta else None):
        return verdict
    return meta or verdict


# the FDA-era names, kept so nothing downstream has to care that the reader
# now understands more than approvals and rejections
read_fda = read_filing_event


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


def _taggable(row: dict) -> bool:
    """8-Ks (minus the quarterly ones) plus the deal, activist and
    late-filing forms, all of which answer the question by existing."""
    form = (row.get("form") or "").upper()
    if form in _FORM_EXACT:
        return True
    if any(form.startswith(p) for p, _ in _FORM_TAGS):
        return True
    return form == "8-K" and _ITEM_EARNINGS not in (row.get("items") or "")


# ── who inside the company was buying or selling ────────────────────────────
#
# Form 4 is filed by the insider, not the company, but it lands in the
# company's own submissions feed — measured, not assumed: Form 4 was 5,873
# of the filings across the fifteen tickers checked, more than half of every
# feed. Which is the first problem. Almost none of it means anything.
#
# Across 200 consecutive Form 4s from nine tickers the transaction codes ran
# A (stock granted) 101, S (sold) 62, F (shares withheld for tax on a vest)
# 31, M (option exercised) 24, P (bought on the open market) 9. Grants,
# withholding and exercises are the mechanics of getting paid. Only P and S
# are somebody choosing to trade their own company at the market price.
#
# The second problem is the one that decides the whole design. Of those 345
# sales, 321 — 93% — were made under a Rule 10b5-1 plan: scheduled months
# ahead, non-discretionary by law, and therefore silent on what the seller
# thinks today. Of the ten purchases, zero were under a plan. Insiders sell
# for many reasons and buy for one, and here that is a measurement rather
# than a maxim. So: buying is reported, selling is reported only when it was
# discretionary, and everything else is dropped.

_XSL_SEG = re.compile(r"/xsl[^/]+/")        # the feed links the rendered view
_BUY, _SELL = "P", "S"
# A director putting $9,808 to work is not a signal; the same four insiders
# on one day putting $167K in is. Floors apply to the session's total, so a
# cluster of small buys still clears.
_MIN_BUY = 25_000.0
_MIN_SELL = 250_000.0


def _f4(text: str, tag: str) -> str:
    """Form 4 wraps most values in <value> and some not at all, and writes
    booleans as both 1/0 and true/false."""
    m = re.search(rf"<{tag}>\s*(?:<value>\s*)?([^<]*)", text)
    return (m.group(1) or "").strip() if m else ""


def _f4_flag(text: str, tag: str) -> bool:
    return _f4(text, tag).lower() in ("1", "true")


def _f4_num(text: str, tag: str) -> float:
    raw = _f4(text, tag).replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def parse_form4(text: str) -> dict:
    """One Form 4 reduced to open-market dollars. Derivative transactions
    are ignored on purpose — an option exercise is compensation arriving,
    not a view being expressed."""
    roles = []
    if _f4_flag(text, "isOfficer"):
        roles.append(_f4(text, "officerTitle") or "officer")
    if _f4_flag(text, "isDirector"):
        roles.append("director")
    if _f4_flag(text, "isTenPercentOwner"):
        roles.append("10% owner")
    bought = sold = 0.0
    for m in re.finditer(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
                         text, re.S):
        blk = m.group(1)
        value = _f4_num(blk, "transactionShares") * _f4_num(blk, "transactionPricePerShare")
        code = _f4(blk, "transactionCode").upper()
        if code == _BUY:
            bought += value
        elif code == _SELL:
            sold += value
    return {"owner": _f4(text, "rptOwnerName"),
            "role": ", ".join(roles),
            "plan": _f4_flag(text, "aff10b5One"),
            "bought": bought, "sold": sold}


def _read_form4(symbol: str, row: dict, allow_read: bool = True) -> dict | None:
    acc = row.get("accession") or ""
    cache = _fda_store(symbol)
    if acc in cache:
        return cache[acc]
    if not allow_read or not available():
        return None                  # unknown, not "nothing" — never cached
    url = _XSL_SEG.sub("/", row.get("url") or "")
    if not url:
        return None
    try:
        rec = parse_form4(_fetch(url, limit=120_000, timeout=15)
                          .decode("utf-8", "replace"))
    except Exception:
        return None                            # unread — retry on a later pass
    if not (rec["bought"] or rec["sold"]):
        rec = None                             # a grant or a tax withholding
    cache[acc] = rec
    _fda_save(symbol)
    return rec


def _money(v: float) -> str:
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


def _insider_label(kind: str, people: list, total: float) -> str:
    verb = "bought" if kind == "INSIDER BUYING" else "sold"
    if len(people) == 1:
        who, role = people[0]
        who = f"{who} ({role})" if role else who
        head = f"{who} {verb} {_money(total)} of stock"
    else:
        head = f"{len(people)} insiders {verb} {_money(total)} of stock"
    if kind == "INSIDER SELLING":
        # The only reason this sale is here at all: it was a decision, not a
        # calendar entry. Say so, because 93% of insider sales are not.
        head += " — not under a scheduled plan"
    return head


def latest_insider(symbol: str, days: tuple, budget: int = 6) -> dict | None:
    """Open-market insider trading that lands in `days`, rolled up by the
    session it could move.

    Rolled up rather than reported one filing at a time because insiders
    move together: four CING officers and directors each filed separately on
    the same day, and only their total says anything.
    """
    if not available():
        return None
    sessions: dict = {}
    reads = 0
    for row in filings(symbol):
        if (row.get("form") or "").upper() != "4":
            continue
        session = moves_session(row)
        if session not in days:
            continue
        fresh = (row.get("accession") or "") not in _fda_store(symbol)
        allow = (not fresh) or reads < budget
        if fresh and allow:
            reads += 1
        rec = _read_form4(symbol, row, allow_read=allow)
        if not rec:
            continue
        slot = sessions.setdefault(session, {"buy": 0.0, "sell": 0.0,
                                             "buyers": [], "sellers": [],
                                             "row": row})
        if rec["bought"]:
            slot["buy"] += rec["bought"]
            slot["buyers"].append((rec["owner"], rec["role"]))
        # A planned sale was decided months ago and is dropped here, which is
        # what removes 93% of the selling before anything reaches the screen.
        if rec["sold"] and not rec["plan"]:
            slot["sell"] += rec["sold"]
            slot["sellers"].append((rec["owner"], rec["role"]))
    best = None
    for session, s in sessions.items():
        if s["buy"] >= _MIN_BUY:
            kind, total, people = "INSIDER BUYING", s["buy"], s["buyers"]
        elif s["sell"] >= _MIN_SELL:
            kind, total, people = "INSIDER SELLING", s["sell"], s["sellers"]
        else:
            continue
        hit = {"kind": kind, "label": _insider_label(kind, people, total),
               "quote": None, "date": s["row"].get("date"),
               "accepted": s["row"].get("accepted"),
               "url": s["row"].get("url"), "value": total}
        if best is None or _rank(kind) > _rank(best["kind"]):
            best = hit
    return best


def latest_event_tag(symbol: str, days: tuple, budget: int = 4) -> dict | None:
    """The most consequential thing this company told the SEC inside `days`.

    Item codes and deal forms are free, so every filing in the window is
    read for those; the budget only limits how many documents get opened to
    look for what the metadata cannot say.
    """
    if not available():
        return None
    best, reads = None, 0
    for row in filings(symbol):
        if str(row.get("date", ""))[:10] not in days or not _taggable(row):
            continue
        fresh = (row.get("accession") or "") not in _fda_store(symbol)
        allow = (not fresh) or reads < budget
        if fresh and allow:
            reads += 1
        hit = read_filing_event(symbol, row, allow_read=allow)
        if not hit:
            continue
        better = _rank(hit["kind"]) > _rank(best["kind"] if best else None)
        # same verdict from two filings: keep the one that can quote the
        # deal — "$77.00 per share" beats "a tender offer exists"
        richer = (best is not None and hit["kind"] == best["kind"]
                  and hit.get("quote") and not best.get("quote"))
        if better or richer:
            best = hit
    # Form 4s are read on their own path — they are half the feed, they are
    # not 8-Ks, and only a session's total says anything. Merged by the same
    # rank, so a buyout still beats a director topping up.
    try:
        ins = latest_insider(symbol, days)
    except Exception:
        ins = None
    if ins and _rank(ins["kind"]) > _rank(best["kind"] if best else None):
        best = ins
    return best


def event_tag_dates(symbol: str, dates, budget: int = 8) -> dict:
    """{gap date -> kind} for the dates given.

    Only days that actually have a filing cost anything, the budget caps how
    many new documents one pass will open, and every verdict is remembered,
    so a symbol's history fills in over a few scans instead of stalling one.
    """
    want = {str(d)[:10] for d in (dates or []) if d}
    if not want or not available():
        return {}
    out: dict = {}
    reads = 0
    for row in filings(symbol):
        if not _taggable(row):
            continue
        session = moves_session(row)
        if session not in want:
            continue
        fresh = (row.get("accession") or "") not in _fda_store(symbol)
        allow = (not fresh) or reads < budget
        if fresh and allow:
            reads += 1
        verdict = read_filing_event(symbol, row, allow_read=allow)
        if verdict and _rank(verdict["kind"]) > _rank(out.get(session)):
            out[session] = verdict["kind"]
    return out


# the FDA-era names, kept so callers written against them keep working
latest_fda = latest_event_tag
fda_dates = event_tag_dates


def coverage(symbol: str) -> dict:
    """How far back the filing history actually reaches, so the UI can say
    so instead of implying the whole daily window is covered."""
    rows = filings(symbol)
    if not rows:
        return {"n": 0, "first": None, "last": None}
    return {"n": len(rows), "first": rows[-1]["date"], "last": rows[0]["date"]}
