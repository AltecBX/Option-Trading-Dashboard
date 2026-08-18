"""filing_units.py — what scale a number in a filing table is printed on.

A filer captions a table "(in millions, except per-share data)" and then,
eighteen rows further down, prints assets under management in billions under
a heading of its own. T. Rowe Price does exactly that: "$1,893.4" means
$1.9 TRILLION, and reading it on the caption's scale makes it $1.9 billion.
BlackRock prints "$15,344,624" in the same kind of table and means millions.
Both are correct; the table caption is simply not the whole story.

So the scale is resolved from the MOST SPECIFIC statement that covers the
number, and the reading records which statement that was:

  1. the row's own label          "AUM (at period end, in billions)"
  2. a section heading above it   "Assets under management (in billions)"
  3. the column it sits under     a head cell reading "(in billions)"
  4. the table's heading rows     one scale, stated once, for the whole table
  5. the caption before the table "(in millions, except per share data)"
  6. nothing — UNKNOWN, and the number is refused

Two rules keep this honest:

  * NO MAGNITUDE GUESSING. "This number looks too big to be millions" is not
    evidence. A scale is read from words a person wrote, or it is unknown.
  * CONFLICT AT THE SAME LEVEL IS AMBIGUOUS. Two head cells that say
    different things about the same column do not get averaged or ranked by
    luck; the reading is refused.

The caption is held to a stricter form than the row and column are. The text
before a table is prose, and prose says things like "market returns which
decreased assets under management by $59 billion" — which is a sentence
about a change, not a statement about the table's scale. A caption is only
read when it uses the parenthesised bookkeeping form.
"""

from __future__ import annotations

import re

UNITS_VERSION = "invest-filing-units-1.0.0"

HIGH, MODERATE, LOW = "HIGH", "MODERATE", "LOW"

# ── the units this app understands ──────────────────────────────────────────
#
# `factor` turns the printed number into the metric's own unit: dollars for
# money, percent for a ratio, one share for a per-share figure.

UNITS: dict[str, dict] = {
    "units":       {"label": "dollars", "family": "MONEY", "factor": 1.0},
    "thousands":   {"label": "thousands", "family": "MONEY", "factor": 1e3},
    "millions":    {"label": "millions", "family": "MONEY", "factor": 1e6},
    "billions":    {"label": "billions", "family": "MONEY", "factor": 1e9},
    "trillions":   {"label": "trillions", "family": "MONEY", "factor": 1e12},
    "percent":     {"label": "percent", "family": "RATIO", "factor": 1.0},
    "basis points": {"label": "basis points", "family": "RATIO",
                     "factor": 0.01},
    "per share":   {"label": "per share", "family": "PER_SHARE",
                    "factor": 1.0},
}

AMBIGUOUS_UNIT = "AMBIGUOUS UNIT"
UNKNOWN_UNIT = "UNKNOWN"

# Where a unit was read from, most specific first. The order IS the
# precedence, and it is the whole point of this module.
SOURCES = ("row label", "section heading", "column header", "table heading",
           "caption")

_SOURCE_CONFIDENCE = {
    "row label": HIGH,
    "section heading": HIGH,
    "column header": HIGH,
    "table heading": MODERATE,
    "caption": LOW,
}


# ── reading a unit out of a piece of text ───────────────────────────────────
#
# Two strictnesses. STRUCTURAL applies to a row label, a section heading and a
# column header: those are short, written by the filer to label numbers, and
# "(billions)" on its own means what it says. STRICT applies to the caption,
# which is prose, and requires the bookkeeping form — "in billions",
# "$ in billions", "dollars in billions" — so that a sentence mentioning a
# billion of something is not mistaken for a statement about the table.

_SCALE_WORDS = (
    (r"trillions?", "trillions"),
    (r"billions?|bn\b", "billions"),
    (r"millions?|mm\b", "millions"),
    (r"thousands?|000s\b", "thousands"),
)

_STRICT = tuple(
    (re.compile(r"(?i)(?:\$\s*)?\b(?:in|dollars\s+in|amounts\s+in|"
                r"expressed\s+in|stated\s+in)\s+(?:" + w + r")"), name)
    for w, name in _SCALE_WORDS)

_STRUCTURAL = tuple(
    (re.compile(r"(?i)(?:^|[(,\-—:]|\bin\b|\$)\s*(?:" + w + r")\b"), name)
    for w, name in _SCALE_WORDS)

# A unit is DECLARED, not spotted. "% Change" is the name of a column and
# "except per-share data" is an exception to a scale; neither is a statement
# that the numbers below are percentages or per-share amounts. Only the
# declarative forms count.
_PERCENT = re.compile(r"(?i)(?:^|[(\[,])\s*(?:in\s+)?percent(?:ages?)?\s*[)\],]"
                      r"|(?:^|[(\[])\s*%\s*[)\]]|\bin\s+percent(?:ages?)?\b")
_BASIS_POINTS = re.compile(r"(?i)\bbasis\s+points\b|(?:^|[(\[,\s])bps\b")
_PER_SHARE = re.compile(r"(?i)\bin\s+per[\s\-]share\b"
                        r"|(?:^|[(\[])\s*per[\s\-]share(?:\s+amounts?)?\s*[)\]]")

# "(in millions, except per-share data)" hedges: it names a scale and then
# takes something out of it without saying what. The exception is recorded so
# a caller can refuse to lean on the caption alone.
_HEDGED = re.compile(r"(?i)\b(?:thousands?|millions?|billions?|trillions?)"
                     r"[^)]{0,80}\bexcept\b")

# Everything after "except" in a caption is the exception, not the rule. It
# is cut off before units are read so that "except per-share data" cannot be
# mistaken for a declaration that the table is in per-share amounts.
_EXCEPT = re.compile(r"(?i)\bexcept\b.*$")


def units_in(text: str, strict: bool = False) -> list[str]:
    """Every unit this piece of text states, in the order they appear.

    More than one means the text does not settle the question by itself.
    """
    s = _EXCEPT.sub(" ", text or "")
    if not s.strip():
        return []
    found: list[tuple[int, str]] = []
    for rx, name in (_STRICT if strict else _STRUCTURAL):
        for m in rx.finditer(s):
            found.append((m.start(), name))
    for rx, name in ((_BASIS_POINTS, "basis points"),
                     (_PER_SHARE, "per share"),
                     (_PERCENT, "percent")):
        m = rx.search(s)
        if m:
            found.append((m.start(), name))
    found.sort()
    out: list[str] = []
    for _, name in found:
        # A scale word repeated in one caption ("in millions ... in millions")
        # is one statement, not two.
        if name not in out:
            out.append(name)
    return out


def hedged(text: str) -> bool:
    """Does this text name a scale and then except something from it?"""
    return bool(_HEDGED.search(text or ""))


def factor_of(unit: str | None) -> float | None:
    spec = UNITS.get(unit or "")
    return spec["factor"] if spec else None


def family_of(unit: str | None) -> str:
    spec = UNITS.get(unit or "")
    return spec["family"] if spec else ""


# ── resolving one number's unit ─────────────────────────────────────────────

def resolve(row_label: str = "", section_heading: str = "",
            column_header: str = "", table_heading: str = "",
            caption: str = "") -> dict:
    """The unit that applies to one number, and where it came from.

    Each level is asked in turn and the first that says anything wins. A
    level that says two different things stops the search with AMBIGUOUS
    rather than handing the question down to a vaguer level: if the column
    headings disagree about the column, the caption is not the tie-breaker.
    """
    levels = (("row label", row_label, False),
              ("section heading", section_heading, False),
              ("column header", column_header, False),
              ("table heading", table_heading, False),
              ("caption", caption, True))
    out = {"unit": None, "source": "", "confidence": "",
           "raw_unit": "", "reason": "", "hedged": hedged(caption or ""),
           "version": UNITS_VERSION}
    for name, text, strict in levels:
        got = units_in(text, strict=strict)
        if not got:
            continue
        if len(got) > 1:
            out["unit"] = None
            out["source"] = name
            out["reason"] = (
                f"The {name} states more than one unit — "
                + " and ".join(f"\"{g}\"" for g in got)
                + " — so which one covers this number is a guess worth a "
                  "factor of a thousand, and it is refused rather than "
                  "guessed at.")
            out["ambiguous"] = True
            return out
        out["unit"] = got[0]
        out["source"] = name
        out["raw_unit"] = got[0]
        out["confidence"] = _SOURCE_CONFIDENCE[name]
        # The caption named a scale and then excepted something from it
        # without saying what, and the caption is all there is. Which side of
        # the exception this row falls on is unknown, and the caller is told
        # so rather than being handed a number that may be a thousand times
        # too small.
        out["from_hedged_caption"] = (name == "caption" and hedged(caption or ""))
        if name != "caption":
            # Something more specific than the caption answered. Where the
            # caption said something else, that is worth recording: it is
            # exactly the case the hedge was warning about, and it is where
            # T. Rowe Price's billions come from.
            cap = units_in(caption or "", strict=True)
            if hedged(caption or "") or (cap and cap[0] != got[0]):
                out["overrides_caption"] = True
                if cap and cap[0] != got[0]:
                    out["overridden_unit"] = cap[0]
        return out
    out["reason"] = (
        "Nothing in this row, its heading, its column, the table or the "
        "caption says whether the figures are in thousands, millions or "
        "billions. The difference is a factor of a thousand, so the number "
        "is not used. It is never inferred from how large it looks.")
    return out


def normalise(raw_value, unit: str | None, wants_family: str) -> dict:
    """The printed number in the metric's own unit, or the reason there is
    no honest conversion.

    An impossible unit for the metric is refused: a percentage is not a
    quantity of dollars, and a per-share figure is not a company total.
    """
    out = {"value": None, "unit": unit, "family": family_of(unit),
           "reason": ""}
    if raw_value is None:
        out["reason"] = "There is no number in this cell."
        return out
    if not unit:
        out["reason"] = "The unit of this figure is not stated."
        return out
    fam = family_of(unit)
    if fam != wants_family:
        out["reason"] = (
            f"This figure is printed in {UNITS[unit]['label']}, which cannot "
            f"be a {'money amount' if wants_family == 'MONEY' else 'ratio'}. "
            f"Converting between the two would invent a number, so it is "
            f"refused.")
        return out
    out["value"] = float(raw_value) * UNITS[unit]["factor"]
    return out
