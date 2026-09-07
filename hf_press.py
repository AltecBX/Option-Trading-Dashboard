"""hf_press.py — the prime-broker channel, read strictly.

Goldman Sachs, Morgan Stanley and JPMorgan prime brokerage tell their clients
each week what hedge funds did. That research is not public, but the wires
quote it: Reuters runs the "HEDGE FLOW" series, Bloomberg and CNBC carry the
same notes. This module turns those headlines into rows classed
PRIME BROKER AGGREGATE DATA — secondhand by definition, corroborating by
design, never able to create a verdict on its own.

It is pure: text in, rows out. No network, no clock, no files.

Four things it refuses to do, each learned from the captured corpus:

  * **It will not read a bank that is merely mentioned.** "JPMorgan Chase
    & Co. Shares Purchased by Smith Group Asset Management" names a bank and
    a purchase and has nothing to do with hedge fund positioning. A bank
    counts only where the sentence cites it as the source — "Goldman says",
    "JPMorgan data shows", "says Morgan Stanley".

  * **It will not read performance as positioning.** "Hedge funds suffered
    their worst month against the S&P 500 in twenty years" is a fact about
    returns. Any return word rejects the item outright. That loses a few
    genuine positioning headlines, which is the cheaper mistake.

  * **It will not read a foreign book as the American one.** "Hedge funds
    cut Asia tech holdings in second-largest selloff" is true and is not
    evidence about the market this board measures. A stated non-US region
    keeps the headline visible and bars it from evidence.

  * **It will not invent the period.** Headlines say "last week", "in July",
    "for a fourth consecutive week". None of that is machine-readable, so
    `as_of` stays empty and only `public_on` — the date the wire published —
    is ever claimed. The headline itself is carried so the reader sees what
    was actually said.

One more, learned from the crowding fix in `hf_pulse`: when five outlets
repeat one Goldman note, that is one note. Evidence rows are deduplicated by
bank, question and direction so a syndicated claim cannot vote five times.

HEDGE_FUND_INTEL.md §5e, §7 phase 3.
"""

from __future__ import annotations

import re
from datetime import date

import hf_sources as S

HF_PRESS_VERSION = "1.0.0"

# The four questions the Pulse asks. A claim that does not land on one of
# these is not evidence about positioning, whatever else it may be.
ABOUT = ("exposure", "leverage", "longs", "shorts")

MAX_AGE_DAYS = 10          # a prime-broker note describes the week just ended
SUMMARY_CHARS = 300        # how much of the article blurb is read for claims

# ── who is speaking ─────────────────────────────────────────────────────────

BANKS = {
    "Goldman Sachs": r"goldman(?:\s+sachs)?",
    "Morgan Stanley": r"morgan\s+stanley",
    "JPMorgan": r"jpmorgan|j\.?\s?p\.?\s*morgan",
    "Bank of America": r"bank\s+of\s+america|\bbofa\b",
    "UBS": r"\bubs\b",
    "Barclays": r"barclays",
    "Citi": r"\bciti(?:group|bank)?\b",
    "Deutsche Bank": r"deutsche\s+bank",
}

# A bank counts only as the cited source of the claim. Each pattern takes the
# bank's own alternation as {b}.
_ATTRIBUTION = (
    r"{b}\s*(?:'s|’s)?\s+(?:says?|said|notes?|noted|data|figures|numbers|flags?|flagged|"
    r"warns?|warned|estimates?|reports?|wrote|writes|adds?|shows?|showed|told|calculates?)",
    r"(?:says?|said|according\s+to|per|citing|from|by)\s+{b}",
    r"{b}\s*(?:'s|’s)?\s+(?:prime\s+broker(?:age)?|note|desk|research|clients?|report|analysts?|team)",
    r"{b}\s+prime",
)

# Outlets whose reporting is taken as evidence. Everything else is captured
# and shown, and does not raise confidence in anything.
WIRES = ("reuters", "bloomberg", "financial times", "wall street journal", "wsj",
         "cnbc", "marketwatch", "barron", "business insider", "yahoo finance",
         "investing.com", "the globe and mail", "hedgeweek", "institutional investor",
         "pensions & investments", "financial news london", "the business times")
# A bank publishing its own commentary is first-party, not press.
FIRST_PARTY = ("goldman sachs", "morgan stanley", "j.p. morgan", "jpmorgan", "ubs", "barclays")

WIRE, FIRST, OTHER = "WIRE", "FIRST PARTY", "OTHER"

# ── what is being said ──────────────────────────────────────────────────────

# A headline must be about hedge funds. Without this every bank press release
# in the feed arrives as positioning.
_SUBJECT = re.compile(r"hedge\s+fund|hedge-fund", re.I)

# Returns, not positions. Any of these rejects the item.
_PERFORMANCE = re.compile(
    r"\bunderperform\w*|\boutperform\w*|\bperformance\b|\breturns?\b|\bgains?\b|\blosses\b|"
    r"\blost\b|\blosing\b|\bprofit\w*|\bworst\s+(?:month|year|quarter|week)\b|"
    r"\bbest\s+(?:month|year|quarter|week)\b|\bstellar\b|\bdented\b|\bhurt\s+by\b|"
    r"\bsuffer\w*\b|\bmeltdown\b|\bblow(?:s|n)?\s+up\b|\bshut(?:ting)?\s+down\b|"
    r"\bassets\s+under\s+management\b|\blaunch\w*\b|\bhir\w+\b|\bfees?\b", re.I)

_LEVERAGE_UP = re.compile(
    r"\b(?:record|near-record|high|higher|rising|rose|raised?|raising|boost\w*|increas\w*|"
    r"elevated|added\s+to|ramp\w*\s+up|climb\w*)\s+(?:net\s+|gross\s+)?leverage\b|"
    r"\bleverage\s+(?:rose|rises|rising|climbed|jumped|hit|hits|reach\w*|at\s+(?:a\s+)?record|"
    r"near\s+record|increas\w*|higher)\b|\bre-?lever\w*\b|\bgross(?:ing)?\s+up\b|"
    r"\b(?:gross|net)\s+exposure\s+(?:rose|rises|climbed|jumped|increas\w*|higher)\b", re.I)
_LEVERAGE_DOWN = re.compile(
    r"\bde-?lever\w*\b|\bde-?gross\w*\b|\b(?:cut|cutting|lower\w*|reduc\w*|trim\w*|slash\w*|"
    r"unwind\w*|paring|pared)\s+(?:their\s+)?(?:net\s+|gross\s+)?leverage\b|"
    r"\bleverage\s+(?:fell|falls|dropped|declined|slid|down)\b|"
    r"\b(?:gross|net)\s+exposure\s+(?:fell|falls|dropped|declined|slid)\b", re.I)

_SHORT_ADD = re.compile(
    # "short" used as a verb on something: "aggressively short financial
    # stocks". The noun forms that mean the opposite — short covering, a
    # short squeeze, short interest — cannot match, because this requires a
    # traded thing to follow.
    r"\bshort\s+(?:\w+\s+){0,2}(?:stocks?|shares?|equities|equity|names?|sectors?)\b|"
    r"\bshort(?:ing|ed)\b|\badd\w*\s+(?:to\s+)?shorts?\b|\bbuild\w*\s+shorts?\b|"
    r"\bnew\s+shorts?\b|\bshort\s+bets?\b|\bbearish\s+bets?\b|\bshort\s+position\w*\s+"
    r"(?:rose|grew|increas\w*|climbed)\b|\braised?\s+shorts?\b|\bpiling\s+into\s+shorts?\b", re.I)
_SHORT_COVER = re.compile(
    r"\bshort\s+cover\w*\b|\bcover\w*\s+(?:their\s+)?shorts?\b|\bsqueez\w*\b|"
    r"\bbuy\w*\s+to\s+cover\b|\bunwind\w*\s+shorts?\b|\bclos\w*\s+(?:out\s+)?shorts?\b|"
    r"\bshort\s+position\w*\s+(?:fell|dropped|declined|shrank)\b", re.I)

_LONG_WORD = re.compile(r"\blongs?\b|\blong\s+(?:positions?|book|exposure|side|bets?)\b", re.I)

_SELL = re.compile(
    r"\bnet[\s-]?s(?:old|ell\w*)\b|\bsold\b|\bsell\w*\b|\bcut\w*\b|\btrim\w*\b|\bdump\w*\b|"
    r"\boffload\w*\b|\breduc\w*\b|\bslash\w*\b|\bexit\w*\b|\bflee\w*|\bfled\b|\bunload\w*\b|"
    r"\bpar(?:e|ed|ing)\b|\bde-?risk\w*\b|\blighten\w*\b|\bdial\w*\s+back\b|\bselloff\b|"
    r"\bsell-off\b|\bdivest\w*\b|\bdefensive\b|\brotat\w*\s+out\b|\bditch\w*\b|\bshed\b|"
    r"\bshedding\b|\babandon\w*\b|\bretreat\w*\b", re.I)
_BUY = re.compile(
    r"\bnet[\s-]?b(?:ought|uy\w*)\b|\bbought\b|\bbuy\w*\b|\badd\w*\s+to\b|\badding\b|"
    r"\bpil\w*\s+(?:back\s+)?into\b|\brush\w*\s+into\b|\bsnap\w*\s+up\b|\bscoop\w*\s+up\b|"
    r"\bboost\w*\b|\bramp\w*\s+up\b|\bload\w*\s+up\b|\bincreas\w*\b|\bbullish\s+bets?\b|"
    r"\bbuying\s+spree\b|\brotat\w*\s+into\b|\bbets?\s+on\s+rising\b", re.I)

# ── where ───────────────────────────────────────────────────────────────────

_NON_US = re.compile(
    r"\basia\w*\b|\bjapan\w*\b|\bkorea\w*\b|\bkospi\b|\bchina\b|\bchinese\b|\beurope\w*\b|"
    r"\bemerging\s+market\w*\b|\btaiwan\w*\b|\bindia\b|\blatin\s+america\w*\b|\bbrazil\w*\b|"
    r"\buk\b|\bbritain\b|\bbritish\b|\bgerman\w*\b|\bfrance\b|\bfrench\b|\bhong\s+kong\b", re.I)
_US = re.compile(r"\bu\.?s\.?\b|\bamerica\w*\b|\bwall\s+street\b|\bs&p\b|\bnasdaq\b|"
                 r"\brussell\b|\bdow\b", re.I)

# ── which sector ────────────────────────────────────────────────────────────

SECTOR_WORDS = (
    ("Technology", r"\btech\w*\b|\bsemiconductor\w*\b|\bchip\w*\b|\bsoftware\b"),
    ("Health Care", r"\bhealth\s?care\b|\bhealth\b|\bbiotech\w*\b|\bpharma\w*\b|\bdrugmaker\w*\b"),
    ("Financials", r"\bfinancial\s+(?:stocks?|shares?|sector|names?)\b|\bfinancials\b|\bbank\s+stocks?\b"),
    ("Energy", r"\benergy\b|\boil\s+(?:stocks?|shares?|majors?)\b|\bgas\s+stocks?\b"),
    ("Utilities", r"\butilit(?:y|ies)\b"),
    ("Industrials", r"\bindustrial\w*\b"),
    ("Materials", r"\bmaterials\b|\bmining\b|\bminers\b|\bmetals\b"),
    ("Real Estate", r"\breal\s+estate\b|\breits?\b"),
    ("Consumer Discretionary", r"\bconsumer\s+(?:discretionary|cyclical)\b|\bretail(?:er)?s?\b"),
    ("Consumer Staples", r"\bconsumer\s+(?:staples|defensive)\b"),
    ("Communication Services", r"\bcommunication\s+services\b|\bmedia\s+stocks?\b|\btelecom\w*\b"),
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _norm(s) -> str:
    """Curly quotes and collapsed whitespace, so one lexicon matches both the
    wire's typography and an aggregator's."""
    t = str(s or "").replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"').replace("—", " ").replace("–", "-")
    return re.sub(r"\s+", " ", t).strip()


def outlet_of(item: dict) -> str | None:
    """Google News puts the outlet in <source>, and also appends it to the
    title after a dash. Atom entries carry neither, so the title is the
    fallback rather than an assumption that the field is present."""
    o = _norm(item.get("outlet"))
    if o:
        return o
    title = _norm(item.get("title"))
    m = re.search(r"\s-\s([^-]{2,40})$", title)
    return m.group(1).strip() if m else None


def strip_outlet(title: str, outlet: str | None) -> str:
    t = _norm(title)
    if outlet and t.lower().endswith(" - " + outlet.lower()):
        t = t[: -(len(outlet) + 3)].strip()
    return t


def outlet_tier(outlet: str | None) -> str:
    o = (outlet or "").lower()
    if not o:
        return OTHER
    if any(w in o for w in FIRST_PARTY):
        return FIRST
    return WIRE if any(w in o for w in WIRES) else OTHER


def bank_of(text: str) -> str | None:
    """The bank cited as the source, or nothing. Mention is not attribution."""
    t = _norm(text)
    for name, pat in BANKS.items():
        for shape in _ATTRIBUTION:
            if re.search(shape.format(b=f"(?:{pat})"), t, re.I):
                return name
    return None


def region_of(text: str) -> str | None:
    """"US", a named foreign region, or nothing said. A note about Korea is
    not evidence about the American book this board measures."""
    t = _norm(text)
    us, foreign = bool(_US.search(t)), bool(_NON_US.search(t))
    if foreign and not us:
        return "NON-US"
    return "US" if us else None


def sector_of(text: str) -> str | None:
    t = _norm(text)
    hits = [name for name, pat in SECTOR_WORDS if re.search(pat, t, re.I)]
    # Two sectors named in one headline is a rotation story, and which side
    # is which cannot be read from word order. It gets no sector.
    return hits[0] if len(hits) == 1 else None


def claims(text: str) -> dict:
    """Every question this sentence speaks to, and which way.

    A headline can honestly answer two different questions at once — buying
    stocks while covering shorts is one week's behaviour described twice, not
    one fact counted twice. Within a single question, a sentence carrying
    both directions is a sentence that does not say which, and that question
    gets None rather than a coin flip."""
    t = _norm(text)
    out: dict[str, int | None] = {}

    def put(key: str, up: bool, down: bool) -> None:
        if up and down:
            out[key] = None
        elif up:
            out[key] = 1
        elif down:
            out[key] = -1

    put("leverage", bool(_LEVERAGE_UP.search(t)), bool(_LEVERAGE_DOWN.search(t)))
    put("shorts", bool(_SHORT_ADD.search(t)), bool(_SHORT_COVER.search(t)))

    buy, sell = bool(_BUY.search(t)), bool(_SELL.search(t))
    if _LONG_WORD.search(t):
        put("longs", buy, sell)
    elif buy or sell:
        # No explicit "long" in the sentence, so the claim is read as overall
        # exposure and is not also posted to the long book. Mapping one
        # sentence onto both questions would be the same fact voting twice.
        put("exposure", buy, sell)
    return {k: v for k, v in out.items() if k in ABOUT}


def is_about_hedge_funds(text: str) -> bool:
    return bool(_SUBJECT.search(_norm(text)))


def is_performance(text: str) -> bool:
    return bool(_PERFORMANCE.search(_norm(text)))


# ── reading one item ────────────────────────────────────────────────────────

def read(item: dict) -> dict | None:
    """One feed item as a captured row, or nothing if it is not about hedge
    funds at all. A row that cannot be evidence still comes back, carrying
    the reason, because "we saw this and did not count it" is worth showing."""
    outlet = outlet_of(item)
    title = strip_outlet(item.get("title") or "", outlet)
    summary = _norm(item.get("summary"))[:SUMMARY_CHARS]
    blob = f"{title}. {summary}"
    if not title or not is_about_hedge_funds(blob):
        return None

    tier = outlet_tier(outlet)
    bank = bank_of(blob)
    region = region_of(blob)
    found = claims(blob)
    row = {"title": title, "outlet": outlet, "tier": tier, "bank": bank,
           "public_on": (item.get("published") or "")[:10] or None,
           "published_at": item.get("published"), "link": item.get("link"),
           "summary": summary, "region": region, "sector": sector_of(blob),
           "claims": found, "as_of": None, "class": S.PRIME_BROKER}

    why = []
    if is_performance(blob):
        why.append("about returns, not positioning")
    if not bank:
        why.append("no bank cited as the source")
    if tier == OTHER:
        why.append("outlet is not a wire or the bank itself")
    if region == "NON-US":
        why.append("a stated non-US market")
    if not any(v is not None for v in found.values()):
        why.append("no unambiguous direction")
    row["eligible"] = not why
    row["why_not"] = why
    return row


def _age_days(public_on: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(str(public_on)[:10])).days
    except (TypeError, ValueError):
        return None


def label_for(bank: str, about: str, direction: int, sector: str | None) -> str:
    what = {"exposure": ("buying equities", "selling equities"),
            "leverage": ("raising leverage", "cutting leverage"),
            "longs": ("adding to longs", "reducing longs"),
            "shorts": ("adding shorts", "covering shorts")}[about][0 if direction > 0 else 1]
    where = f" in {sector}" if sector else ""
    return f"{bank} prime brokerage: hedge funds {what}{where}"


def evidence(rows: list[dict], today: date, max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    """The eligible rows as quotes `hf_pulse.build` can fold in.

    Deduplicated by bank, question and direction: when Reuters, Bloomberg and
    four aggregators carry the same Goldman note, the desk wrote one note.
    The earliest wire report of a claim is the one kept, and the rest are
    counted so the report can say how widely it was carried."""
    best: dict[tuple, dict] = {}
    for r in rows or []:
        if not r.get("eligible"):
            continue
        age = _age_days(r.get("public_on"), today)
        if age is None or age < 0 or age > max_age_days:
            continue
        for about, direction in (r.get("claims") or {}).items():
            if direction is None or about not in ABOUT:
                continue
            key = (r["bank"], about, direction)
            q = {"about": about, "bank": r["bank"], "direction": direction,
                 "sector": r.get("sector"), "region": r.get("region"),
                 "outlet": r.get("outlet"), "tier": r.get("tier"),
                 "public_on": r.get("public_on"), "as_of": None,
                 "link": r.get("link"), "age_days": age,
                 "label": label_for(r["bank"], about, direction, r.get("sector")),
                 "text": r["title"], "carried_by": 1,
                 "note": ("Quoted from the bank's prime brokerage desk. The article states the "
                          "period in words rather than dates, so only the publication date is "
                          "claimed here.")}
            prev = best.get(key)
            if prev is None:
                best[key] = q
            else:
                prev["carried_by"] += 1
                earlier = (q["public_on"] or "") < (prev["public_on"] or "")
                if earlier or (prev["tier"] != WIRE and q["tier"] == WIRE):
                    q["carried_by"] = prev["carried_by"]
                    best[key] = q
    return sorted(best.values(), key=lambda q: (q["about"], -q["carried_by"], q["public_on"] or ""))


def conflicts(quotes: list[dict]) -> list[dict]:
    """Two banks quoted the same week saying opposite things about the same
    question. That is a finding, not something to average away."""
    out = []
    for about in ABOUT:
        rows = [q for q in quotes or [] if q["about"] == about]
        ups = [q for q in rows if q["direction"] > 0]
        downs = [q for q in rows if q["direction"] < 0]
        if ups and downs:
            out.append({"about": about,
                        "saying_up": [{"bank": q["bank"], "text": q["text"],
                                       "public_on": q["public_on"]} for q in ups],
                        "saying_down": [{"bank": q["bank"], "text": q["text"],
                                         "public_on": q["public_on"]} for q in downs]})
    return out


def build(items: list[dict], today: date, max_age_days: int = MAX_AGE_DAYS) -> dict:
    """Everything the press channel has to say this week."""
    rows, seen = [], set()
    for it in items or []:
        r = read(it)
        if r is None:
            continue
        key = (r["title"].lower(), r.get("outlet") or "")
        if key in seen:
            continue
        seen.add(key)
        rows.append(r)
    rows.sort(key=lambda r: (r.get("public_on") or ""), reverse=True)
    recent = [r for r in rows
              if (_age_days(r.get("public_on"), today) or 999) <= max_age_days]
    quotes = evidence(rows, today, max_age_days)
    return {"version": HF_PRESS_VERSION, "class": S.PRIME_BROKER,
            "quotes": quotes, "conflicts": conflicts(quotes),
            "captured": recent[:40], "n_captured": len(rows), "n_recent": len(recent),
            "n_quotes": len(quotes), "max_age_days": max_age_days,
            "banks": sorted({q["bank"] for q in quotes}),
            "note": ("Secondhand by definition. A prime-broker note is a bank's summary of its "
                     "own clients, quoted by a reporter. It can raise confidence in what the "
                     "measured data already says and can never produce a verdict alone.")}
