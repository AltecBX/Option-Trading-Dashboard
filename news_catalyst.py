"""Catalysts that only ever exist as a headline.

Everything else the Gap Scan tags comes from a filing, because a filing is
the company speaking on the record. Two things that move stocks hard never
get filed by anybody:

  * a **short-seller report** — published on the author's own site, not to
    the SEC. The target sometimes files a response days later, by which
    time the gap has already happened.
  * an **index add or drop** — announced by S&P or FTSE Russell, not by the
    company. The company often puts out a press release, and that release
    is the first thing the tape sees.

So these are read out of the news feed the app already runs (Yahoo,
Finnhub, Google News, Finviz). That is weaker evidence than a filing and
is labeled as such everywhere it appears: the tag quotes the headline,
names the publisher, and links to the story so the claim can be checked.

Three rules keep it honest:

  * **named sources only.** A short-seller tag needs a firm that actually
    publishes short reports, or an explicit "short seller report" phrase —
    never the word "short" on its own, which appears in half of all market
    commentary.
  * **the story has to be about this company.** Per-symbol feeds carry
    adjacent stories constantly — Micron's feed served "Defiance Launches
    MUZ: The First 2X Short ETF for Micron" — so the ticker or the company
    name has to appear in the headline itself.
  * **two days, no older.** A stock gaps on the morning the report lands,
    not on the week of commentary that follows it.

Product launches were considered and deliberately left out: in a sample of
160 real headlines, every one that matched launch language was about a
different company than the feed it appeared in. See GAP_SCANNER.md.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Firms whose business IS publishing short theses. A name here is the
# evidence — "short interest rose" or "shares are heavily shorted" is not.
_SHORT_FIRMS = (
    "hindenburg", "muddy waters", "citron", "kerrisdale", "spruce point",
    "wolfpack", "hunterbrook", "culper", "scorpion capital", "grizzly research",
    "fuzzy panda", "bleecker street", "night market research", "blue orca",
    "viceroy research", "j capital", "white diamond", "bonitas research",
    "gotham city research", "iceberg research", "friendly bear",
)
_SHORT_PHRASE = re.compile(
    r"short[- ]seller(?:'s)?\s+report|short\s+report\b|short[- ]seller\s+"
    r"(?:alleges|claims|says|targets|attacks|flags)", re.I)
_SHORT_FIRM = re.compile("|".join(re.escape(f) for f in _SHORT_FIRMS), re.I)

_INDEX_ADD = re.compile(
    r"(?:added to|to join|joins|will be added to|set to join|inclusion in)\s+"
    r"(?:the\s+)?(?:s&p|russell|nasdaq[- ]100|dow jones|ftse)", re.I)
_INDEX_DROP = re.compile(
    r"(?:removed from|dropped from|deleted from|to be removed from)\s+"
    r"(?:the\s+)?(?:s&p|russell|nasdaq[- ]100|dow jones|ftse)", re.I)

_MAX_AGE_HOURS = 48
_SCAN_ITEMS = 25          # newest-first; older than this is not this morning


def _fresh(published: str | None, now: datetime, hours: int) -> bool:
    if not published:
        return False
    try:
        dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) <= timedelta(hours=hours) and dt <= now + timedelta(hours=6)


def _about(title: str, symbol: str, name: str | None) -> bool:
    """Is this headline about the company whose feed it arrived in? Feeds
    carry adjacent stories constantly, so the ticker or the company's own
    name has to be in the headline."""
    low = title.lower()
    if re.search(rf"\b{re.escape(symbol.lower())}\b", low):
        return True
    first = (name or "").replace(",", " ").split()
    if first and len(first[0]) >= 4:
        return first[0].lower() in low
    return False


def classify_headline(title: str):
    """(kind, why) for a headline that names a catalyst, else (None, "")."""
    firm = _SHORT_FIRM.search(title or "")
    if firm or _SHORT_PHRASE.search(title or ""):
        return "SHORT REPORT", (firm.group(0) if firm else "short-seller report")
    if _INDEX_DROP.search(title or ""):
        return "INDEX DROP", "index deletion"
    if _INDEX_ADD.search(title or ""):
        return "INDEX ADD", "index inclusion"
    return None, ""


def catalyst_from_news(symbol: str, feed: dict, name: str | None = None,
                       now: datetime | None = None,
                       max_age_hours: int = _MAX_AGE_HOURS) -> dict | None:
    """The freshest headline for `symbol` that names a real catalyst.

    `feed` is whatever news.get_news() returned — this module never fetches
    anything itself, so it stays testable and adds no requests of its own.
    """
    now = now or datetime.now(timezone.utc)
    for item in (feed or {}).get("items", [])[:_SCAN_ITEMS]:
        title = (item.get("title") or "").strip()
        if not title or not _fresh(item.get("published"), now, max_age_hours):
            continue
        if not _about(title, symbol, name):
            continue
        kind, why = classify_headline(title)
        if not kind:
            continue
        return {"kind": kind, "quote": title, "why": why,
                "source": item.get("source") or "news",
                "published": item.get("published"),
                "url": item.get("url"), "evidence": "headline"}
    return None
