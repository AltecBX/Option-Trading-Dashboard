"""site_links.py — ticker-synced deep links to external research sites (v3.70).

Currently: Simply Wall St, whose stock URLs are

    https://simplywall.st/stocks/{country}/{sector}/{exchange}-{ticker}/{name}
    e.g. .../stocks/us/semiconductors/nasdaq-nvda/nvidia
         .../stocks/us/tech/nasdaq-sndk/sandisk

Only `{exchange}-{ticker}` uniquely identifies the company; `{sector}` and
`{name}` are Simply Wall St's own slugs, which do NOT map 1:1 from any
provider field we hold (NVDA's sector segment is "semiconductors" — its
yfinance *industry* — while SNDK's is "tech", closer to its yfinance
*sector*). So this module builds the best-supported URL from real cached
data and is explicit about which parts are derived:

  • country   — from the listing exchange (us / au / gb / ca …)
  • exchange  — yfinance exchange code → simplywall.st slug (NMS→nasdaq)
  • ticker    — exact
  • sector    — SWS_SECTOR map, keyed on yfinance industry FIRST (it is the
                more specific match, and matches the NVDA example), falling
                back to sector, then "tech"
  • name      — slugified company name with legal suffixes stripped

IMPORTANT: simplywall.st refuses all traffic from this deployment (403 to
plain requests, connection reset even from a real Chromium), so the slug
scheme could not be verified end-to-end here — only reproduced from the two
known-good URLs. Every part of the mapping lives in the two tables below so
a miss is a one-line correction, and `verified` ships in the payload so the
UI can be honest about it. Nothing here fabricates data: when the profile
is unavailable the endpoint says so rather than guessing a company slug.
"""
from __future__ import annotations

import re
import unicodedata

BASE = "https://simplywall.st/stocks"

# yfinance exchange code / name → (country segment, simplywall.st exchange slug)
EXCHANGE_MAP = {
    "NMS": ("us", "nasdaq"), "NASDAQGS": ("us", "nasdaq"), "NASDAQGM": ("us", "nasdaq"),
    "NASDAQCM": ("us", "nasdaq"), "NASDAQ": ("us", "nasdaq"), "NCM": ("us", "nasdaq"),
    "NGM": ("us", "nasdaq"),
    "NYQ": ("us", "nyse"), "NYSE": ("us", "nyse"), "NYS": ("us", "nyse"),
    "PCX": ("us", "nysearca"), "NYSEARCA": ("us", "nysearca"), "ASE": ("us", "nyseamerican"),
    "AMEX": ("us", "nyseamerican"), "NYSEAMERICAN": ("us", "nyseamerican"),
    "BATS": ("us", "batsc"), "OTC": ("us", "otcpk"), "PNK": ("us", "otcpk"),
    "TOR": ("ca", "tsx"), "TSX": ("ca", "tsx"), "VAN": ("ca", "tsxv"),
    "LSE": ("gb", "lse"), "ASX": ("au", "asx"), "GER": ("de", "xtra"),
}

# yfinance industry (preferred) or sector → simplywall.st sector segment.
# Keys are lowercase; matching is exact first, then substring.
SWS_SECTOR = {
    # semiconductors / hardware
    "semiconductors": "semiconductors",
    "semiconductor equipment & materials": "semiconductors",
    "semiconductor equipment and materials": "semiconductors",
    # software & tech services
    "software—infrastructure": "software", "software—application": "software",
    "software - infrastructure": "software", "software - application": "software",
    "information technology services": "software",
    "communication equipment": "tech", "computer hardware": "tech",
    "consumer electronics": "tech", "electronic components": "tech",
    "technology": "tech",
    "electronics & computer distribution": "tech",
    "scientific & technical instruments": "tech",
    "solar": "semiconductors",
    # healthcare
    "biotechnology": "pharmaceuticals-biotech",
    "drug manufacturers—general": "pharmaceuticals-biotech",
    "drug manufacturers—specialty & generic": "pharmaceuticals-biotech",
    "healthcare": "healthcare", "medical devices": "healthcare",
    "diagnostics & research": "healthcare", "healthcare plans": "healthcare",
    "medical instruments & supplies": "healthcare",
    # financials
    "banks—diversified": "banks", "banks—regional": "banks", "banks": "banks",
    "financial services": "diversified-financials",
    "capital markets": "diversified-financials",
    "asset management": "diversified-financials",
    "credit services": "diversified-financials",
    "insurance—diversified": "insurance", "insurance—property & casualty": "insurance",
    "insurance—life": "insurance", "insurance": "insurance",
    "real estate": "real-estate",
    # energy / materials / industrials
    "energy": "energy", "oil & gas integrated": "energy",
    "oil & gas e&p": "energy", "oil & gas midstream": "energy",
    "basic materials": "materials", "specialty chemicals": "materials",
    "chemicals": "materials", "gold": "materials", "steel": "materials",
    "industrials": "capital-goods", "aerospace & defense": "capital-goods",
    "farm & heavy construction machinery": "capital-goods",
    "specialty industrial machinery": "capital-goods",
    "railroads": "transportation", "airlines": "transportation",
    "integrated freight & logistics": "transportation",
    # consumer
    "consumer cyclical": "consumer-durables", "consumer defensive": "food-beverage-tobacco",
    "auto manufacturers": "automobiles", "internet retail": "retail",
    "specialty retail": "retail", "discount stores": "consumer-retailing",
    "restaurants": "consumer-services", "lodging": "consumer-services",
    "beverages—non-alcoholic": "food-beverage-tobacco",
    "packaged foods": "food-beverage-tobacco",
    "tobacco": "food-beverage-tobacco",
    "household & personal products": "household",
    # comms / utilities
    "communication services": "media", "entertainment": "media",
    "internet content & information": "media",
    "telecom services": "telecom", "utilities": "utilities",
}

# Legal-form suffixes stripped from the company slug ("NVIDIA Corporation" →
# "nvidia", "Sandisk Corporation" → "sandisk").
_SUFFIXES = (
    "corporation", "corp", "incorporated", "inc", "company", "co",
    "limited", "ltd", "plc", "holdings", "holding", "group", "sa", "nv",
    "ag", "se", "llc", "lp", "trust", "the",
)


def slugify(name: str) -> str:
    """Company name → simplywall.st-style slug."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    # Collapse dotted initialisms BEFORE punctuation is stripped, so "S.A."
    # becomes the single token "sa" (a known legal suffix) instead of the two
    # stray letters "s" and "a" that would survive into the slug.
    s = re.sub(r"(?:\b[a-z]\.){2,}", lambda m: m.group(0).replace(".", ""), s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    words = [w for w in s.split() if w]
    # Strip trailing legal-form words, plus any dangling connector left
    # behind by them ("JPMorgan Chase & Co." → and+co removed → "jpmorgan-chase",
    # not "jpmorgan-chase-and").
    while words and (words[-1] in _SUFFIXES or words[-1] in ("and", "of", "for")):
        words.pop()
    while words and words[0] in ("the",):
        words.pop(0)
    return "-".join(words)


def sector_slug(industry: str | None, sector: str | None) -> str:
    """Industry first (more specific, matches the NVDA example), then sector."""
    for raw in (industry, sector):
        if not raw:
            continue
        key = str(raw).strip().lower()
        if key in SWS_SECTOR:
            return SWS_SECTOR[key]
    for raw in (industry, sector):
        if not raw:
            continue
        key = str(raw).strip().lower()
        for k, v in SWS_SECTOR.items():
            if k in key or key in k:
                return v
    return "tech"


def exchange_slug(exchange: str | None) -> tuple[str, str] | None:
    if not exchange:
        return None
    key = str(exchange).strip().upper().replace(" ", "")
    if key in EXCHANGE_MAP:
        return EXCHANGE_MAP[key]
    for k, v in EXCHANGE_MAP.items():
        if k in key:
            return v
    return None


def simplywallst_url(symbol: str, profile: dict | None) -> dict:
    """Build the Simply Wall St deep link. Returns
    {url, verified, derived:{...}, note} — or {url: None, reason} when the
    profile data needed for a real URL is missing (never a guessed company)."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return {"url": None, "reason": "no symbol"}
    p = profile or {}
    company = p.get("company") or p.get("name")
    exch = exchange_slug(p.get("exchange"))
    name = slugify(company)
    if not exch or not name:
        missing = []
        if not exch:
            missing.append("listing exchange")
        if not name:
            missing.append("company name")
        return {
            "url": None,
            "reason": f"missing {' and '.join(missing)} for {sym} — no link built rather than guessing one",
            "derived": {"company": company, "exchange": p.get("exchange")},
        }
    country, ex = exch
    sec = sector_slug(p.get("industry"), p.get("sector"))
    url = f"{BASE}/{country}/{sec}/{ex}-{sym.lower()}/{name}"
    return {
        "url": url,
        "verified": False,
        "derived": {"country": country, "sector_segment": sec, "exchange": ex,
                    "ticker": sym.lower(), "company_slug": name,
                    "from_industry": p.get("industry"), "from_sector": p.get("sector"),
                    "from_company": company},
        "note": ("Built from the listing exchange, company name and sector. Only the "
                 f"'{ex}-{sym.lower()}' segment identifies the company; the sector and name "
                 "segments are Simply Wall St's own slugs and are derived here."),
    }
