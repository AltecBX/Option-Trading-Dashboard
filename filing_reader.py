"""Read the business chapter out of an annual report, and say how sure it is.

Phase 5 classified an insurer from Item 1 of its 10-K and refused five filers
outright because the chapter could not be found. Measured against fifty-six
real annual reports, the old reader failed on seven of them, and not one of
those failures was the filer's fault:

  * Ameriprise, Equitable and Interactive Brokers file a 10-K/A amendment
    after the 10-K. An amendment carries only the items it amends, so it has
    no Item 1 at all, and the reader took it because it was newest.
  * Berkshire Hathaway styles its chapter heading letter by letter, so the
    flattened text reads "Item 1. Busines s Description" and a plain
    \\bbusiness\\b never matches it. Cincinnati Financial ("I TEM 1.") and
    MarketAxess ("I tem 1.") break the same way, one letter earlier.
  * Morgan Stanley's chapter heading is the single word "Business", with the
    item number living only in the table of contents.

None of that is loose classification. It is a document reader that reads
three kinds of document and one kind of heading, meeting filers who write
four kinds of each. This module reads the document properly and reports how
it found what it found, so a caller can refuse on low confidence rather than
on a missing chapter.

Extraction is tried in a fixed order, best evidence first:

  A. table of contents anchor — the document's own link to Item 1
  B. heading element          — an element whose whole text IS the heading
  C. heading text boundary    — Item 1 through Item 1A in flattened text
  D. longest candidate        — the old heuristic, last resort, never HIGH

Nothing here parses a filing for numbers; that is filing_tables.py. Nothing
here decides what kind of business it is; that is business_routing.py. This
module answers one question — what does this company say it does — and
attaches the accession, document, method, boundaries and confidence behind
the answer.
"""

from __future__ import annotations

import re

READER_VERSION = "invest-filing-reader-1.0.0"

# The annual report forms worth reading, newest first. A 10-K/A is included
# because a full-restatement amendment does carry Item 1; the reader simply
# moves on to the underlying 10-K when it does not.
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F")

# How many annual filings to try before giving up. Three covers "the newest
# is a partial amendment, and so was the one before it".
MAX_FILINGS = 3

# 4MB used to be the read limit, which truncated thirty of fifty-six
# documents. Filings are immutable and cached by accession, so the document
# is fetched once, ever; reading all of it costs one fetch and some transient
# memory. Twenty megabytes clears the largest annual report measured
# (JPMorgan, Prudential and BlackRock all exceed twelve).
FETCH_LIMIT = 20_000_000

HIGH, MODERATE, LOW, FAILED = "HIGH", "MODERATE", "LOW", "FAILED"
_RANK = {FAILED: 0, LOW: 1, MODERATE: 2, HIGH: 3}

# A chapter's worth of prose. The shortest real Item 1 measured across the
# universe is Realty Income's, at 3,485 characters; a cross-reference or a
# contents entry never reaches a quarter of that.
MIN_CHAPTER = 1_500
MIN_CONFIDENT_CHAPTER = 3_000
MAX_BODY = 60_000

# How much of the chapter a classifier may read. The reader keeps up to
# MAX_BODY characters because a person reading the description should get
# the chapter, but what KIND of business it is must not depend on how much
# of the chapter the reader happened to keep. Palomar Holdings comes out a
# property-casualty insurer on forty thousand characters and a reinsurer on
# sixty thousand, purely because its reinsurance section sits at the end.
# Forty thousand is the budget Phase 5 classified on, so every Phase 5
# answer is unchanged and the better reader is purely additive.
CLASSIFY_CHARS = 40_000

# Callers that classify a business need at least this much confidence. A LOW
# extraction is shown to a reader as text and refused as evidence.
ACCEPTABLE = (HIGH, MODERATE)


def acceptable(confidence: str | None) -> bool:
    """Is this extraction good enough to classify a business from?"""
    return confidence in ACCEPTABLE


# ── headings ────────────────────────────────────────────────────────────────
#
# Filers style headings letter by letter — a drop cap, letter spacing, a
# single character wrapped for kerning — and each styled letter becomes its
# own element. sec_filings._plain deliberately does NOT close up a
# close-then-open tag pair, because "authorized</span><span>the" must not
# become one word. The cost is that "Busines</span><span>s" stays split, so
# every heading pattern here tolerates whitespace between any two letters.
# That tolerance is safe in a short anchored heading and would not be safe in
# body text.

def _spaced(word: str) -> str:
    return r"\s*".join(word)


_GAP = r"[\s​‌‍﻿.\-—–:)|]*"

ITEM1_HDR = re.compile(_spaced("item") + _GAP + r"1" + _GAP
                       + _spaced("business"), re.I)
ITEM1A_HDR = re.compile(_spaced("item") + _GAP + r"1a" + _GAP
                        + _spaced("risk") + r"\s*" + _spaced("factors"), re.I)

# Anchor labels. The label is the text of the link in the contents list, so
# it is short and complete — "Item 1. Business", "Item 1", "Business".
_LBL_ITEM1 = re.compile(r"^item\s*1\b[\s.:\-—–)|]*(business)?\.?$", re.I)
_LBL_BUSINESS = re.compile(r"^business\.?$", re.I)
_LBL_ITEM1A = re.compile(r"^item\s*1a\b[\s.:\-—–)|]*(risk\s+factors)?\.?$", re.I)
_LBL_RISK = re.compile(r"^risk\s+factors\.?$", re.I)

_A_TAG = re.compile(rb'(?is)<a\b[^>]*href\s*=\s*["\']#([^"\'#]+)["\'][^>]*>'
                    rb'(.{0,300}?)</a>')
_ID_ATTR = rb'(?is)<[a-z][a-z0-9]*[^>]*\b(?:id|name)\s*=\s*["\']%s["\']'
_TAGS_OUT = re.compile(rb'(?s)<[^>]+>')

# Block elements whose entire text can be a chapter heading.
_BLOCK = re.compile(
    rb'(?is)<(p|div|h[1-6]|td|th|span|font|b|strong)\b[^>]*>(.{0,400}?)</\1>')

# A body that opens like this is a pointer at the chapter, not the chapter.
_CROSSREF = re.compile(
    r"^\s*(see\b|refer to\b|as (described|discussed|set forth|defined)\b"
    r"|for (a|more|further|additional)\b|included in\b|contained in\b"
    r"|incorporated (herein )?by reference\b|is (set forth|described) in\b)",
    re.I)

# Risk-factor prose that has bled past a missing Item 1A boundary.
_RISK_MARKERS = (
    "could adversely affect", "may adversely affect", "risks and uncertainties",
    "we may not be able to", "could have a material adverse",
    "there can be no assurance",
)

_SENTENCE = re.compile(r"[.!?]\s+[A-Z(]")

# A contents block: a label, then a page number, over and over.
_INDEX_ROW = re.compile(r"[A-Za-z][A-Za-z &,'’/\-()]{3,70}?\s+[A-Z]?-?\s?\d{1,3}\s")


# ── document cleanup ────────────────────────────────────────────────────────
#
# A modern 10-K opens with a hidden <ix:header> holding thousands of
# machine-readable facts, and scatters display:none blocks through the body.
# Neither is text a reader ever sees, and both wreck heading detection.
_IX_HEADER = re.compile(rb"(?is)<ix:header.*?</ix:header>")
_HIDDEN_DIV = re.compile(rb"(?is)<div[^>]*display:\s*none[^>]*>.*?</div>")


def clean_document(raw: bytes) -> bytes:
    return _HIDDEN_DIV.sub(b" ", _IX_HEADER.sub(b" ", raw))


def _label_of(chunk: bytes) -> str:
    txt = _TAGS_OUT.sub(b" ", chunk).decode("utf-8", "ignore")
    return re.sub(r"[\s ​]+", " ", txt).strip()


def _anchor_target(raw: bytes, match_label, limit: int = 3_000_000):
    """Where the contents list says a chapter begins, and what it called it.

    Returns (byte offset, label) or None. Only links in the front of the
    document are considered: a contents list is at the front, and a link from
    deep inside the report is a cross-reference.
    """
    for m in _A_TAG.finditer(raw[:limit]):
        label = _label_of(m.group(2))
        if not label or not match_label(label):
            continue
        anc = re.escape(m.group(1))
        tgt = re.search(_ID_ATTR % anc, raw)
        if tgt:
            return tgt.start(), label
    return None


def _heading_element(raw: bytes, pattern, limit: int = 6_000_000):
    """An element whose whole text is the chapter heading.

    A heading in its own element is unambiguous in a way a heading found in
    running text is not, and it survives the styling that breaks flat text.
    """
    for m in _BLOCK.finditer(raw[:limit]):
        label = _label_of(m.group(2))
        if not label or len(label) > 60:
            continue
        if pattern.match(label):
            return m.end(), label
    return None


# ── body cleanup ────────────────────────────────────────────────────────────

#
# A page break inside the chapter leaves "12 Table of Contents" in the text:
# the page number, then the running header of the next page. Both belong to
# the paper, not to the business, and both are styled letter by letter often
# enough ("Tab le of Contents") to need the same tolerance as the headings.
_LEAD_JUNK = re.compile(
    r"^[\s​‌‍﻿.,:;\-—–|)&]*\d{0,4}[\s​]*"
    r"(" + _spaced("table") + r"\s*" + _spaced("of") + r"\s*"
    + _spaced("contents") + r"|part\s+[iv]+\b|index\s+to\s+business"
    r"|description\b)?"
    r"[\s.,:;\-—–|)]*", re.I)


def _strip_heading(body: str) -> str:
    """Drop a repeated heading, part label or contents caption off the front."""
    for _ in range(3):
        before = body
        body = ITEM1_HDR.sub(" ", body, count=1) if ITEM1_HDR.match(body) else body
        body = _LEAD_JUNK.sub("", body, count=1)
        if body == before:
            break
    return body.strip()


def _drop_index_block(body: str) -> str:
    """Cut a chapter's own contents list off the front.

    MetLife, Prudential, Brighthouse and Apollo open Item 1 with an index of
    the chapter — "Business Overview 5 Segments 7 Regulation 21" — which is
    not prose and would otherwise be quoted back as the description.

    The cut is bounded by the first sentence, and that bound is the whole
    safety of it. A contents list contains no sentences: nothing in it ends
    with a full stop and starts again with a capital. Ordinary prose, on the
    other hand, is full of label-then-number shapes — "as of December 31,
    2025", "Note 4" — and an unbounded version of this rule sliced Visa,
    Apollo, Simon Property and six others off mid-sentence. Rows are counted
    only where no sentence has begun yet.
    """
    head = body[:6_000]
    first_sentence = _SENTENCE.search(head)
    stop = first_sentence.start() if first_sentence else len(head)
    rows = [m for m in _INDEX_ROW.finditer(head) if m.end() <= stop]
    if len(rows) < 6:
        return body
    rest = body[rows[-1].end():].lstrip(" .:;-—–")
    return rest if len(rest) >= MIN_CHAPTER else body


def _looks_like_contents(body: str) -> bool:
    """A contents block is page numbers with labels between them."""
    sample = body[:3_000]
    digits = sum(c.isdigit() for c in sample)
    return bool(sample) and digits / len(sample) > 0.09 \
        and len(_SENTENCE.findall(sample)) < 3


def _risk_bleed(body: str, bounded: bool) -> bool:
    """Risk Factors text running on past a boundary that was never found."""
    if bounded:
        return False
    low = body.lower()
    return sum(low.count(m) for m in _RISK_MARKERS) >= 6


def _tidy(body: str, strip_lead_in=None) -> str:
    body = _strip_heading(body)
    body = _drop_index_block(body)
    body = _strip_heading(body)
    if strip_lead_in is not None:
        # The caller's opener trimmer removes section words, and removing
        # "Business Overview" from "Business Overview & Strategy" leaves the
        # ampersand behind. One more pass takes the orphan with it.
        body = _strip_heading(strip_lead_in(body))
    return body.strip()[:MAX_BODY]


# ── extraction ──────────────────────────────────────────────────────────────

def _grade(body: str, method: str, bounded: bool) -> tuple[str, str]:
    """Confidence, and the sentence explaining it."""
    if len(body) < MIN_CHAPTER:
        return FAILED, ("The business chapter of the annual report is shorter "
                        "than a chapter — what was found is a reference to it "
                        "rather than the chapter itself.")
    if _CROSSREF.match(body):
        return FAILED, ("What was found points at the business chapter "
                        "instead of being it.")
    if _looks_like_contents(body):
        return FAILED, ("What was found is the report's contents list, not "
                        "the business chapter.")
    if _risk_bleed(body, bounded):
        return LOW, ("The business chapter runs on into Risk Factors because "
                     "no heading marks where it ends, so the text mixes what "
                     "the company does with what could go wrong.")
    if method == "longest candidate":
        return LOW, ("The business chapter was picked by length rather than "
                     "by a heading, which is the reading of last resort.")
    if len(body) < MIN_CONFIDENT_CHAPTER:
        return LOW, (f"Only {len(body):,} characters of business chapter were "
                     f"found, which is short enough that something was "
                     f"probably missed.")
    if method in ("table of contents anchor", "heading element") and bounded:
        return HIGH, ""
    if bounded or method in ("table of contents anchor", "heading element"):
        return MODERATE, ""
    return LOW, ("The end of the business chapter was never found, so the "
                 "text may run past it.")


def extract(raw: bytes, strip_lead_in=None) -> dict:
    """Pull Item 1 out of one annual report document.

    `strip_lead_in` is fundamentals' own opener trimmer, passed in rather
    than imported so this module has no dependency on its caller.
    """
    doc = clean_document(raw)
    attempts: list[dict] = []

    def record(method, body, bounded, start_hdr, end_hdr):
        body = _tidy(body, strip_lead_in)
        conf, why = _grade(body, method, bounded)
        attempts.append({"method": method, "text": body, "confidence": conf,
                         "reason": why, "bounded": bounded,
                         "start_heading": start_hdr, "end_heading": end_hdr,
                         "characters": len(body)})
        return conf

    # ── A. the document's own contents link ─────────────────────────────
    a0 = _anchor_target(doc, lambda s: bool(_LBL_ITEM1.match(s)
                                            or _LBL_BUSINESS.match(s)))
    if a0:
        start, start_hdr = a0
        a1 = _anchor_target(doc, lambda s: bool(_LBL_ITEM1A.match(s)
                                                or _LBL_RISK.match(s)))
        end_hdr = ""
        end = None
        if a1 and a1[0] > start:
            end, end_hdr = a1
        seg = doc[start:end] if end else doc[start:start + 2_000_000]
        body = _plain_text(seg)
        bounded = end is not None
        if not bounded:
            m = ITEM1A_HDR.search(body)
            if m:
                body, bounded = body[:m.start()], True
                end_hdr = m.group(0)
        if record("table of contents anchor", body, bounded,
                  start_hdr, end_hdr) == HIGH:
            return attempts[-1]

    # ── B. a heading standing alone in its own element ──────────────────
    b0 = _heading_element(doc, ITEM1_HDR)
    if b0:
        start, start_hdr = b0
        seg = _plain_text(doc[start:start + 2_000_000])
        m = ITEM1A_HDR.search(seg)
        end_hdr = m.group(0) if m else ""
        body = seg[:m.start()] if m else seg
        if record("heading element", body, bool(m), start_hdr, end_hdr) == HIGH:
            return attempts[-1]

    # ── C. heading to heading in the flattened text ─────────────────────
    text = _plain_text(doc)
    cands = []
    for m in ITEM1_HDR.finditer(text):
        s = m.end()
        nxt = ITEM1A_HDR.search(text, s)
        seg = text[s: nxt.start() if nxt else min(len(text), s + MAX_BODY)]
        seg = re.sub(r"^[\s​‌‍﻿.:;\-—–]*\d{0,4}[\s​]*",
                     "", seg)
        # A contents entry is a page number and the next heading; a chapter
        # is thousands of characters of sentences.
        if seg.count(".") >= 3:
            cands.append((seg, bool(nxt), m.group(0),
                          nxt.group(0) if nxt else ""))
    # A document is written in order, so the chapter is the FIRST candidate
    # long enough to be one. Every cross-reference is by definition later.
    for seg, bounded, hdr, endhdr in cands:
        if len(seg) >= MIN_CHAPTER and not _CROSSREF.match(seg.lstrip()):
            if record("heading text boundary", seg, bounded, hdr, endhdr) == HIGH:
                return attempts[-1]
            break

    # ── D. the old heuristic, which can never be more than LOW ──────────
    if cands:
        seg, bounded, hdr, endhdr = max(cands, key=lambda c: len(c[0]))
        record("longest candidate", seg, bounded, hdr, endhdr)

    if not attempts:
        return {"method": "", "text": "", "confidence": FAILED,
                "reason": "No business chapter heading appears anywhere in "
                          "this document.",
                "bounded": False, "start_heading": "", "end_heading": "",
                "characters": 0}
    return max(attempts, key=lambda a: (_RANK[a["confidence"]],
                                        a["characters"]))


# The flattener lives in sec_filings; it is injected so this module can be
# tested against a string with no network module loaded at all.
_PLAIN = None


def configure(plain_fn=None) -> None:
    global _PLAIN
    _PLAIN = plain_fn


def _plain_text(raw: bytes) -> str:
    if _PLAIN is None:                                   # pragma: no cover
        raise RuntimeError("filing_reader.configure() was never called")
    return _PLAIN(raw)


# ── the whole job, over a company's annual filings ──────────────────────────

def business_section(symbol: str, sec, strip_lead_in=None,
                     max_filings: int = MAX_FILINGS) -> dict:
    """The business chapter of the newest annual report that actually has one.

    Filings are tried newest first. An amendment that carries only Part III
    fails extraction and the reader moves on to the 10-K underneath it, which
    is how Ameriprise, Equitable and Interactive Brokers get read at all.
    """
    sym = (symbol or "").upper().strip()
    out = {"ok": False, "text": "", "confidence": FAILED,
           "reason": "No annual report could be read for this filer.",
           "provenance": {}}
    if not sym or sec is None:
        return out
    try:
        rows = sec.filings(sym)
    except Exception:
        return out
    annual = [r for r in rows
              if (r.get("form") or "").upper() in ANNUAL_FORMS]
    if not annual:
        out["reason"] = ("This filer has filed no annual report that the SEC "
                         "full-text record carries — a company that has been "
                         "taken over or has gone private stops filing one.")
        return out

    best = None
    for row in annual[:max_filings]:
        try:
            raw = sec._fetch(row["url"], limit=FETCH_LIMIT)   # noqa: SLF001
        except Exception:
            continue
        got = extract(raw, strip_lead_in=strip_lead_in)
        got["provenance"] = {
            "symbol": sym,
            "cik": row.get("cik"),
            "accession": row.get("accession"),
            "document": (row.get("url") or "").rsplit("/", 1)[-1],
            "url": row.get("url"),
            "form": row.get("form"),
            "filed": row.get("date"),
            "method": got.get("method") or "",
            "start_heading": got.get("start_heading") or "",
            "end_heading": got.get("end_heading") or "",
            "characters": got.get("characters") or 0,
            "confidence": got.get("confidence"),
            "reader_version": READER_VERSION,
        }
        if best is None or _RANK[got["confidence"]] > _RANK[best["confidence"]]:
            best = got
        if acceptable(got["confidence"]):
            break

    if best is None:
        return out
    prov = best["provenance"]
    reason = best.get("reason") or ""
    if best["confidence"] == FAILED and prov.get("form", "").endswith("/A"):
        reason = (f"The newest annual filing is a {prov['form']} amendment, "
                  f"which carries only the parts it amends and no business "
                  f"chapter, and no earlier annual report could be read "
                  f"either. ") + reason
    return {"ok": best["confidence"] != FAILED, "text": best["text"],
            "confidence": best["confidence"], "reason": reason,
            "provenance": prov}
