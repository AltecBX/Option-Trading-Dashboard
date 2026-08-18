"""Read a small, named set of operating metrics out of SEC filing tables.

Company Facts is XBRL, and XBRL is the financial statements. It does not
carry client assets, assets under management, net new assets, a published
combined ratio or a published funds-from-operations figure, because none of
those is a line of a balance sheet or an income statement. They live in
tables — in the 10-Q, and above all in the earnings-release exhibit attached
to an 8-K.

This is not web scraping. Every document read here comes from the app's own
SEC transport, by accession number, out of the EDGAR archive: the same
filings the rest of the Investment tab is built on. Nothing is fetched from
a company website, nothing is read out of a PDF, and no language model is
asked what a row means.

The rules that keep it honest:

  * A controlled taxonomy. A short list of metrics, each with an explicit
    list of row labels. "Assets" is not client assets. "Net flows" from one
    segment is not company-wide net new assets.
  * Exact label matching after normalisation, never "close enough". A row
    label either is one of the listed aliases or it is not a match. Funds
    from operations is the one exception and it is not a loosening: a REIT
    publishes five different measures all called FFO, so that label is
    parsed by an explicit grammar that says which basis each one is, and
    only the common-shareholder basis is used.
  * A period on every number, and the number taken from the COLUMN that
    period heads. Reading the first figure in a row is right when the
    current period is printed first and wrong when it is printed last;
    Affiliated Managers prints 2025 before 2026, and its assets under
    management would otherwise be a year out of date.
  * Units read from the most specific statement that covers the number —
    see filing_units.py. Never from how large the number looks.
  * Segment figures are not company figures. A combined ratio printed for
    Personal Insurance is not the company's combined ratio, and a table the
    filer heads "by segment" is not read for a company-wide measure.
  * Ambiguity refused. Two plausible rows produce
    "N/A — AMBIGUOUS TABLE MATCH", not a coin toss.
  * Provenance on everything: accession, document, table, row label, column
    label, period, unit, where the unit came from, the raw text and the
    parsed number.
  * Cache by accession and document. A filing never changes, so a reading of
    it never changes, and a later filing writes a new entry rather than
    editing an old one.

What comes out of here corroborates the reconstructed figures; it replaces
them only where the provenance and the period are both strong, and where the
two disagree materially the disagreement is shown rather than resolved in
favour of the nicer number.
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import date

import filing_units as units

TABLES_VERSION = "invest-filing-tables-2.0.0"

HIGH, MODERATE, LOW = "HIGH", "MODERATE", "LOW"

# ── the controlled taxonomy ─────────────────────────────────────────────────
#
# BALANCE is a stock measured at a date; FLOW is a movement over a period.
# Confusing the two is the difference between "clients hold $13 trillion" and
# "clients added $13 trillion", so the kind is carried and checked.

BALANCE, FLOW, RATIO = "BALANCE", "FLOW", "RATIO"

MONEY, RATIO_FAMILY = "MONEY", "RATIO"

# A REIT publishes several measures all called funds from operations, and
# they are not the same number. Simon Property Group prints FFO of the
# Operating Partnership ($1,184,945 thousand), FFO allocable to limited
# partners, Dilutive FFO allocable to common stockholders ($1,010,258) and
# Real Estate FFO ($1,248,564) in ONE table. Only the third is what a share
# of Simon is entitled to, and comparing either of the others against a
# reconstruction built from the income statement reports a mismatch that is
# really a definition difference.
FFO_COMMON = "FFO ATTRIBUTABLE TO COMMON SHAREHOLDERS"
FFO_PARTNERSHIP = "OPERATING PARTNERSHIP FFO"
FFO_CORE = "CORE FFO"
FFO_NORMALIZED = "NORMALIZED FFO"
FFO_AFFO = "ADJUSTED FFO"
FFO_OTHER = "COMPANY-DEFINED FFO"
FFO_PER_SHARE = "FFO PER SHARE"
FFO_UNQUALIFIED = "FUNDS FROM OPERATIONS, BASIS NOT STATED"

_FFO_WORD = re.compile(r"(?i)\b(?:funds from operations|a?ffo)\b")
_FFO_RULES = (
    (re.compile(r"(?i)per\s+(?:diluted\s+|basic\s+|common\s+|and\s+)*share"
                r"|\bffops\b"), FFO_PER_SHARE),
    (re.compile(r"(?i)\boperating partnership\b|allocable to (?:limited "
                r"partners|unitholders)|attributable to (?:limited "
                r"partners|unitholders)"), FFO_PARTNERSHIP),
    (re.compile(r"(?i)\badjusted funds from operations\b|\baffo\b"), FFO_AFFO),
    (re.compile(r"(?i)\bcore\b"), FFO_CORE),
    (re.compile(r"(?i)\bnormali[sz]ed\b"), FFO_NORMALIZED),
    (re.compile(r"(?i)(?:attributable|available|allocable) to .{0,60}?"
                r"common (?:stockholders|shareholders|shares)"), FFO_COMMON),
    (re.compile(r"(?i)\bexcluding\b|\bnon-?cash\b|\breal estate ffo\b|"
                r"\bestimated\b|\bguidance\b|\badjusted\b"), FFO_OTHER),
)


def ffo_basis(label: str) -> str | None:
    """Which of a REIT's several funds-from-operations measures this row is.

    Returns None when the label is not a funds-from-operations row at all.
    The order matters: "Dilutive FFO allocable to common stockholders" is a
    common-shareholder measure even though it also says "dilutive", and
    "FFO of the Operating Partnership excluding non-cash impacts" is a
    partnership measure before it is anything else.
    """
    lab = (label or "")
    if not _FFO_WORD.search(lab):
        return None
    for rx, basis in _FFO_RULES:
        if rx.search(lab):
            return basis
    return FFO_UNQUALIFIED


METRICS: dict[str, dict] = {
    "client_assets": {
        "label": "Client assets",
        "kind": BALANCE, "unit": "USD", "family": MONEY,
        "aliases": ("total client assets", "client assets",
                    "customer equity", "total customer equity",
                    "total platform assets", "platform assets",
                    "total client asset balances"),
        "min": 1e8, "max": 5e13,
    },
    "assets_under_administration": {
        "label": "Assets under administration",
        "kind": BALANCE, "unit": "USD", "family": MONEY,
        "aliases": ("total assets under administration",
                    "assets under administration",
                    "client assets under administration",
                    "total client assets under administration"),
        "min": 1e8, "max": 5e13,
    },
    "assets_under_management": {
        "label": "Assets under management",
        "kind": BALANCE, "unit": "USD", "family": MONEY,
        "aliases": ("total assets under management", "assets under management",
                    "total aum", "ending assets under management",
                    "ending aum", "aum", "assets under management aum",
                    "total assets under management aum"),
        "min": 1e8, "max": 5e13,
    },
    "advisory_assets": {
        "label": "Advisory assets",
        "kind": BALANCE, "unit": "USD", "family": MONEY,
        "aliases": ("total advisory assets", "advisory assets",
                    "advisory and brokerage assets"),
        "min": 1e8, "max": 5e13,
    },
    "net_new_assets": {
        "label": "Net new assets",
        "kind": FLOW, "unit": "USD", "family": MONEY,
        "aliases": ("net new assets", "total net new assets",
                    "net new client assets", "core net new assets"),
        "min": -5e12, "max": 5e12,
    },
    "net_flows": {
        "label": "Net flows",
        "kind": FLOW, "unit": "USD", "family": MONEY,
        "aliases": ("net flows", "total net flows", "long-term net flows",
                    "net client cash flows", "total net client cash flows"),
        "min": -5e12, "max": 5e12,
    },
    "published_combined_ratio": {
        "label": "Published combined ratio",
        "kind": RATIO, "unit": "percent", "family": RATIO_FAMILY,
        "aliases": ("combined ratio", "total combined ratio",
                    "gaap combined ratio"),
        "min": 30.0, "max": 300.0,
    },
    "published_loss_ratio": {
        "label": "Published loss ratio",
        "kind": RATIO, "unit": "percent", "family": RATIO_FAMILY,
        "aliases": ("loss ratio", "loss and loss adjustment expense ratio",
                    "loss & loss adjustment expense ratio",
                    "loss and loss expense ratio"),
        "min": 10.0, "max": 250.0,
    },
    "published_expense_ratio": {
        "label": "Published expense ratio",
        "kind": RATIO, "unit": "percent", "family": RATIO_FAMILY,
        "aliases": ("expense ratio", "underwriting expense ratio"),
        "min": 1.0, "max": 90.0,
    },
    "published_ffo": {
        "label": "Published funds from operations",
        "kind": FLOW, "unit": "USD", "family": MONEY,
        "aliases": (),
        # Matched by the basis grammar above rather than by an alias list,
        # because the same four words name five different measures.
        "ffo_basis": FFO_COMMON,
        "min": 1e6, "max": 5e11,
    },
}

AMBIGUOUS = "N/A — AMBIGUOUS TABLE MATCH"
MIXED_SCOPE = "N/A — SEGMENT FIGURE, NOT COMPANY-WIDE"


# ── HTML into tables ────────────────────────────────────────────────────────

_TABLE = re.compile(rb"(?is)<table\b[^>]*>(.*?)</table>")
_ROW = re.compile(rb"(?is)<tr\b[^>]*>(.*?)</tr>")
_CELL = re.compile(rb"(?is)<t([dh])\b([^>]*)>(.*?)</t\1>")
_TAGS = re.compile(rb"(?is)<(script|style)[^>]*>.*?</\1>")
_ANY = re.compile(rb"(?s)<[^>]+>")


def _text(chunk: bytes) -> str:
    s = _ANY.sub(b" ", _TAGS.sub(b" ", chunk)).decode("utf-8", "replace")
    s = _html.unescape(s)
    return re.sub(r"[\s ​]+", " ", s).strip()


def _cells(row: bytes) -> list[str]:
    """A row's cells, with the spacer cells that make filing tables
    unreadable dropped, and a lone currency sign folded into the number
    that follows it."""
    got = [_text(m.group(3)) for m in _CELL.finditer(row)]
    out: list[str] = []
    pending = ""
    for c in got:
        if not c:
            continue
        if c in ("$", "€", "£"):
            pending = c
            continue
        if c == "%" and out:
            out[-1] = out[-1] + "%"
            continue
        out.append((pending + c).strip() if pending else c)
        pending = ""
    return out


def tables(raw: bytes, limit: int = 400) -> list[dict]:
    """Every table in the document, as label-and-cells rows.

    `context` is the text immediately before the table, which is where a
    filer puts the caption and the "(in millions)" that goes with it.
    """
    out = []
    for m in list(_TABLE.finditer(raw))[:limit]:
        rows = [_cells(r.group(1)) for r in _ROW.finditer(m.group(1))]
        rows = [r for r in rows if r]
        if not rows:
            continue
        start = max(0, m.start() - 2500)
        out.append({"index": len(out), "rows": rows,
                    "context": _text(raw[start:m.start()])[-500:]})
    return out


# ── periods ─────────────────────────────────────────────────────────────────

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"))}
_MON3 = {m[:3]: i + 1 for i, m in enumerate(_MONTHS)}
_MONTH_ALT = "|".join(list(_MONTHS) + list(_MON3))

_DATE_FULL = re.compile(
    r"(?i)\b(" + _MONTH_ALT + r")\.?\s+(\d{1,2})\s*,?\s*(\d{4})\b")
# "6/30/2026" — T. Rowe Price heads every column this way.
_DATE_SLASH = re.compile(r"\b(1[0-2]|0?[1-9])/(3[01]|[12]\d|0?[1-9])/((?:19|20)\d{2})\b")
# "30-Jun-26" — Franklin Resources heads every column this way, and without
# it every one of its assets-under-management columns names no period and the
# whole reading is refused.
_DATE_DMY = re.compile(r"(?i)\b(3[01]|[12]\d|0?[1-9])[\-/\s](" + _MONTH_ALT +
                       r")[\-/\s](\d{2}|\d{4})\b")
# "Q2 2026", "2Q'26" — Blackstone heads every column with the curly
# apostrophe form.
_QUARTER = re.compile(r"(?i)\b(?:Q([1-4])[\s\-'’]?(\d{2,4})|([1-4])Q[\s\-'’]?(\d{2,4}))\b")
_YEAR_ONLY = re.compile(r"\b(19|20)(\d{2})\b")
# "Three Months Ended June 30," with the year on the row below it — the
# commonest heading in every filing measured. The day and the year arrive in
# different cells, so they are paired rather than read from one token.
_DATE_NO_YEAR = re.compile(r"(?i)\b(" + _MONTH_ALT + r")\.?\s+(\d{1,2})\s*,")

_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
# A quarter marker with no year beside it — "Q2" over a column whose year is
# printed in the row below.
_QUARTER_MARK = re.compile(r"(?i)(?:^|\s)(?:Q([1-4])|([1-4])Q)(?:\s|$)")


def _year_of(text: str) -> int:
    y = int(text)
    return y if y > 100 else (2000 + y if y < 70 else 1900 + y)


def period_of(label: str) -> dict | None:
    """The date a column header names, and how precisely it names it."""
    lab = (label or "").strip()
    if not lab:
        return None
    m = _DATE_FULL.search(lab)
    if m:
        mon = _MONTHS.get(m.group(1).lower()) or _MON3.get(m.group(1).lower()[:3])
        try:
            return {"date": date(int(m.group(3)), mon,
                                 int(m.group(2))).isoformat(),
                    "precision": "day", "text": m.group(0)}
        except ValueError:
            return None
    m = _DATE_SLASH.search(lab)
    if m:
        try:
            return {"date": date(int(m.group(3)), int(m.group(1)),
                                 int(m.group(2))).isoformat(),
                    "precision": "day", "text": m.group(0)}
        except ValueError:
            return None
    m = _DATE_DMY.search(lab)
    if m:
        mon = _MONTHS.get(m.group(2).lower()) or _MON3.get(m.group(2).lower()[:3])
        try:
            return {"date": date(_year_of(m.group(3)), mon,
                                 int(m.group(1))).isoformat(),
                    "precision": "day", "text": m.group(0)}
        except ValueError:
            return None
    m = _QUARTER.search(lab)
    if m:
        q = int(m.group(1) or m.group(3))
        yr = m.group(2) or m.group(4)
        mon, day = _QUARTER_END[q]
        return {"date": date(_year_of(yr), mon, day).isoformat(),
                "precision": "quarter", "text": m.group(0)}
    m = _YEAR_ONLY.search(lab)
    if m:
        return {"date": f"{m.group(1)}{m.group(2)}-12-31",
                "precision": "year", "text": m.group(0)}
    return None


# A column that compares this period against the last one is not a period.
_COMPARISON = re.compile(
    r"(?i)(%\s*change|change\s*%|\bvs\.?\b|versus|growth|variance|"
    r"increase\s*\(decrease\)|year[- ]over[- ]year|yoy|qoq)")

# Year-to-date and quarter figures are not interchangeable.
_YTD = re.compile(r"(?i)(year[- ]to[- ]date|\bytd\b|six months|nine months|"
                  r"twelve months|full year)")
_QTD = re.compile(r"(?i)(three months|quarter ended|for the quarter)")


def window_of(label: str, context: str = "") -> str:
    """Whether a figure covers a quarter, a year to date, or something the
    table does not say.

    Realty Income heads one table "Three months ended June 30, | Six months
    ended June 30," and prints both, so the heading names two windows at
    once. Guessing which column was taken is how a quarter of funds from
    operations gets compared against a year of it and reported as a 300%
    mismatch. Both present means AMBIGUOUS, and an ambiguous window is not
    compared with anything.
    """
    both = f"{context} {label}"
    ytd, qtd = bool(_YTD.search(both)), bool(_QTD.search(both))
    if ytd and qtd:
        return "AMBIGUOUS"
    if ytd:
        return "YEAR TO DATE"
    if qtd:
        return "QUARTER"
    return "UNSTATED"


# ── company-wide or one segment ─────────────────────────────────────────────
#
# Travelers prints a combined ratio five times in one filing: once for the
# company and once for each of Business Insurance, Bond & Specialty
# Insurance, Personal Insurance and Personal Automobile. All five rows carry
# the same label. What tells them apart is what the filer wrote above the
# table — "CONSOLIDATED OVERVIEW ... Consolidated Results of Operations"
# against "Segment Income by Major Component and Combined Ratio — Business
# Insurance" — so that is what is read.

CONSOLIDATED, SEGMENT, UNSTATED_SCOPE = "CONSOLIDATED", "SEGMENT", "UNSTATED"

_CONSOLIDATED = re.compile(
    r"(?i)\bconsolidated\b|\btotal company\b|\bcompany[- ]wide\b|"
    r"\bin total\b|\btotal underwriting\b|\btotals?\b\s*[-—:]")
_SEGMENT = re.compile(r"(?i)\bsegments?\b|\bby (?:business|line of business)\b")


def scope_of(context: str, head_text: str = "") -> str:
    """Whether a table says it is about the whole company or one segment."""
    both = f"{context or ''} {head_text or ''}"
    if _CONSOLIDATED.search(both):
        return CONSOLIDATED
    if _SEGMENT.search(both):
        return SEGMENT
    return UNSTATED_SCOPE


# ── numbers ─────────────────────────────────────────────────────────────────

_NUM = re.compile(r"^\(?\s*[\$€£]?\s*(-?[\d,]+(?:\.\d+)?)\s*\)?\s*%?$")


def parse_number(cell: str):
    """A table cell as a number, or None. Parentheses mean negative."""
    c = (cell or "").strip()
    if not c or c in ("—", "–", "-", "N/A", "n/a", "*"):
        return None
    m = _NUM.match(c)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if c.startswith("(") or c.strip().startswith("( "):
        v = -v
    return v


def normalise_label(text: str) -> str:
    """A row label reduced to the words that matter, so that
    "Total client assets (1)" and "Total  Client Assets:" are the same
    label and "Client assets — advised" is not.

    Parentheses always come off. Filers put three different things in them
    and none belongs in the label: a footnote marker, an abbreviation
    (Raymond James writes 'Assets under administration ("AUA")'), and the
    scale (Interactive Brokers writes 'Customer Equity (in billions)'). The
    scale is read separately, by filing_units, and it takes priority over
    the table's own caption precisely because it is more local.
    """
    s = (text or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)                # footnotes, abbreviations, scale
    s = re.sub(r"[‘’'“”\"]", "", s)
    s = re.sub(r"[^a-z0-9%&/\- ]+", " ", s)
    s = re.sub(r"\s+\d+\s*$", " ", s)               # a trailing footnote number
    s = re.sub(r"\s+", " ", s).strip(" :-")
    return s


def matches_metric(row_label: str, spec: dict) -> bool:
    """Is this row the metric, on the metric's own terms?"""
    want_basis = spec.get("ffo_basis")
    if want_basis:
        return ffo_basis(row_label) == want_basis
    return normalise_label(row_label) in set(spec.get("aliases") or ())


# ── finding a metric in a document ──────────────────────────────────────────

# How many rows at the top of a table are read for units and periods. Trying
# to identify "the heading rows" exactly does not work: the first row of
# Schwab's client-asset table is ['Q2-26 % Change', '2026', '2025'], whose
# second cell parses as the number 2026. Six rows covers every multi-line
# heading measured, and reading a data row for a unit phrase or a date costs
# nothing.
_HEAD_ROWS = 6

# How far above a row to look for a section heading that states its unit.
# T. Rowe Price puts "Assets under management (in billions) (4)" two rows
# above "Ending assets under management" inside a table captioned in
# millions; BlackRock's equivalent heading is one row above. Twelve is
# generous and still inside one block of a table.
_SECTION_LOOKBACK = 12

_PRECISION_RANK = {"day": 3, "quarter": 2, "year": 1}


def _head_rows(rows: list[list[str]]) -> list[list[str]]:
    return rows[:_HEAD_ROWS]


def _paired_dates(blob: str) -> list[dict]:
    """Month-and-day headings paired with the years printed beside them."""
    days = list(_DATE_NO_YEAR.finditer(blob))
    if not days:
        return []
    years = {int(f"{m.group(1)}{m.group(2)}")
             for m in _YEAR_ONLY.finditer(blob)}
    out = []
    for d in days:
        mon = (_MONTHS.get(d.group(1).lower())
               or _MON3.get(d.group(1).lower()[:3]))
        for yr in years:
            try:
                out.append({"date": date(yr, mon, int(d.group(2))).isoformat(),
                            "precision": "day",
                            "text": f"{d.group(0)} {yr}"})
            except ValueError:
                continue
    return out


def _month_end(iso: str) -> bool:
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        return (nxt - date(y, m, d)).days == 1
    except Exception:
        return False


def newest_period(texts, not_after: str | None = None) -> dict | None:
    """The most recent period any of these headings names.

    Used when the columns cannot be lined up with the row. A reporting
    period ends on the last day of a month; a heading that names 15 July or
    1 April is naming something else — the day the release was issued, an
    acquisition date — and must not outrank the quarter end printed beside
    it.
    """
    found = []
    for t in texts:
        for token in re.split(r"[|\n]", t or ""):
            per = period_of(token)
            if per:
                found.append(per)
    found += _paired_dates(" ".join(t or "" for t in texts))
    if not_after:
        found = [p for p in found if p["date"] <= not_after]
    if not found:
        return None
    return max(found, key=lambda p: (_month_end(p["date"]),
                                     _PRECISION_RANK[p["precision"]],
                                     p["date"]))


def _offset_of(head: list[str], width: int) -> int | None:
    """How far along a heading row sits above the data row, or None when it
    cannot be lined up at all.

    A data row begins with its label, so its first cell is a stub. Some
    heading rows carry that stub and some do not: Schwab's quarterly table
    heads its columns with a row reading ['2026', '2025', '2026', '2025']
    and no stub, so heading one sits above column one, not column zero.
    Reading it the other way took Schwab's client assets from the June 2025
    column and reported $10.8 trillion as this quarter's figure.
    """
    if len(head) == width - 1:
        return 1
    if len(head) == width:
        # A stub cell holds a label, not a date. Interactive Brokers puts
        # "Year over Year" in its stub and its columns start at one;
        # Schwab's heading row starts straight in with "2026" and its
        # columns start at zero. What tells them apart is whether the first
        # cell is itself a period.
        return 1 if period_of(head[0] if head else "") else 0
    return None


def _best_period_in(text: str) -> dict | None:
    """The period a single heading cell names, when it names more than one.

    Progressive heads a column "July 15, 2026 June 30, 2026" — the day the
    release was issued and the quarter it is about, in one cell. A reporting
    period ends on the last day of a month, so that is the one taken.
    """
    found = []
    rest = text or ""
    for _ in range(4):
        per = period_of(rest)
        if not per:
            break
        found.append(per)
        cut = rest.find(per["text"])
        rest = rest[cut + len(per["text"]):] if cut >= 0 else ""
        if not rest.strip():
            break
    if not found:
        return None
    return max(found, key=lambda p: (_month_end(p["date"]),
                                     _PRECISION_RANK[p["precision"]],
                                     p["date"]))


def column_periods(heads: list[list[str]], width: int,
                   not_after: str | None = None) -> dict:
    """The period each column of a row of `width` cells sits under.

    Filing tables carry colspans that the spacer cells hide, so heading
    number four is not always above number four. What CAN be lined up is a
    heading row with as many cells as the data row, or one fewer: when one
    exists, every column is named, and Affiliated Managers' assets under
    management stops being read out of the 2025 column because 2025 is
    printed first.
    """
    out: dict[int, dict] = {}
    comparison: set[int] = set()
    blob = " ".join(" ".join(h) for h in heads)
    # A bare year in a column heading ("2026") is a year only because the
    # month and day are printed once, in the row above it.
    day_tokens = list(_DATE_NO_YEAR.finditer(blob))
    mon_day = None
    if day_tokens:
        d = day_tokens[0]
        mon_day = ((_MONTHS.get(d.group(1).lower())
                    or _MON3.get(d.group(1).lower()[:3])), int(d.group(2)))
    by_col: dict[int, list[str]] = {}
    for head in heads:
        off = _offset_of(head, width)
        if off is None:
            continue
        for j, cell in enumerate(head):
            i = j + off
            if 1 <= i < width:
                by_col.setdefault(i, []).append(cell or "")

    for i, cells in by_col.items():
        best = None
        quarter = next((m for c in cells
                        for m in [_QUARTER_MARK.search(c)] if m), None)
        for cell in cells:
            per = _best_period_in(cell)
            if per and per["precision"] == "year":
                yr = int(per["date"][:4])
                # BlackRock heads one column "Q2" and the one below it
                # "2026"; neither cell is a period and the pair is. Failing
                # that, a bare year takes the month and day the table
                # printed once, above every column.
                if quarter:
                    mon, day = _QUARTER_END[int(quarter.group(1)
                                                or quarter.group(2))]
                    per = {"date": date(yr, mon, day).isoformat(),
                           "precision": "quarter",
                           "text": f"{quarter.group(0)} {yr}"}
                elif mon_day:
                    try:
                        per = {"date": date(yr, mon_day[0],
                                            mon_day[1]).isoformat(),
                               "precision": "day", "text": f"{cell} {mon_day}"}
                    except ValueError:
                        pass
            if not per:
                continue
            if not_after and per["date"] > not_after:
                continue
            if best is None or _PRECISION_RANK[per["precision"]] > \
                    _PRECISION_RANK[best["precision"]]:
                best = per
        if best is not None:
            out[i] = best
        elif any(_COMPARISON.search(c) for c in cells):
            # Only a column that names no period at all can be dismissed as
            # a change column. Schwab's heading row puts "Percent Change"
            # over a span its spacer cells hide, and letting that override a
            # column that plainly says "2026" would throw the figure away.
            comparison.add(i)
    return {k: v for k, v in out.items() if v and k not in comparison}


def column_header_text(heads: list[list[str]], width: int, idx: int) -> str:
    """Everything the heading rows say directly above one column."""
    parts = []
    for head in heads:
        off = _offset_of(head, width)
        if off is None:
            continue
        j = idx - off
        if 0 <= j < len(head):
            parts.append(head[j])
    return " | ".join(p for p in parts if p)


def column_zone_text(heads: list[list[str]], idx: int) -> str:
    """The last heading cell at or before this column that states a unit.

    BlackRock's earnings release prints two panels side by side in one HTML
    table: financial results "(in millions," on the left and net flows
    "(in billions)" on the right. Neither the caption nor the table as a
    whole can answer which scale a number is on; the cell that opens the
    panel it sits in can.
    """
    best = ""
    for head in heads:
        for i, cell in enumerate(head):
            if i > idx:
                break
            if units.units_in(cell):
                best = cell
    return best


def _section_heading(rows: list[list[str]], row_index: int) -> str:
    """The nearest heading above this row that states a unit for its block.

    A section heading is a row with one cell and no number in it: T. Rowe
    Price's "Assets under management (in billions) (4)", BlackRock's
    "Other :". Only one that states a unit is returned, because only that
    one answers the question being asked.
    """
    for i in range(row_index - 1, max(-1, row_index - _SECTION_LOOKBACK) - 1, -1):
        row = rows[i]
        if not row:
            continue
        if len(row) > 1 and any(parse_number(c) is not None for c in row[1:]):
            continue                       # a data row, not a heading
        text = " ".join(row)
        if units.units_in(text):
            return text
    return ""


# Filers lay two independent tables side by side inside one HTML table.
# BlackRock's earnings release puts financial results on the left and net
# flows by region on the right, so the row that begins "Total net flows"
# reads ['Total net flows', '$191,700', '$67,737', 'EMEA', '55', '68'] — two
# unrelated rows glued together, whose columns cannot be lined up with any
# heading. A word sitting where a number should be is the tell, and such a
# row is refused rather than read halfway.
_WORD_CELL = re.compile(r"[A-Za-z]{3,}")
_NOT_A_WORD = {"n/a", "n/m", "nm", "na", "nil"}


def _panelled(row: list[str]) -> bool:
    for cell in row[1:]:
        c = (cell or "").strip().lower()
        if c in _NOT_A_WORD:
            continue
        if _WORD_CELL.search(c):
            return True
    return False


def _candidate(metric: str, spec: dict, tab: dict, rows: list[list[str]],
               row_index: int, not_after: str | None) -> dict | None:
    """One row of one table, read as far as it can honestly be read."""
    row = rows[row_index]
    if _panelled(row):
        return None
    heads = _head_rows(rows)
    head_text = [" ".join(h) for h in heads]
    head_only = " ".join(head_text)
    caption = tab["context"]
    width = len(row)
    percols = column_periods(heads, width, not_after)

    # Which cell to read. When the columns are named, the newest named
    # column wins; when they are not, the first real number does, which is
    # right for a release that prints the current period first.
    order: list[int]
    if percols:
        newest = max(p["date"] for p in percols.values())
        order = [i for i in sorted(percols) if percols[i]["date"] == newest]
        aligned = True
    else:
        order = list(range(1, width))
        aligned = False
    table_period = newest_period(head_text, not_after) or \
        newest_period([caption], not_after)

    section = _section_heading(rows, row_index)
    wants_money = spec["family"] == MONEY
    for idx in order:
        cell = row[idx]
        is_pct = "%" in cell
        if wants_money and is_pct:
            continue
        value = parse_number(cell)
        if value is None:
            continue
        per = percols.get(idx) or table_period
        if not per:
            continue
        col_text = (column_header_text(heads, width, idx)
                    or column_zone_text(heads, idx))
        if spec["family"] == RATIO_FAMILY:
            # A ratio is printed as a percentage unless the filer says
            # basis points. Nobody publishes a combined ratio in basis
            # points, and the numbers themselves carry the % sign.
            got = units.units_in(f"{row[0]} {col_text}")
            unit = "basis points" if "basis points" in got else "percent"
            u = {"unit": unit, "source": "metric definition",
                 "confidence": HIGH, "raw_unit": unit, "reason": ""}
        else:
            u = units.resolve(row_label=row[0], section_heading=section,
                              column_header=col_text,
                              table_heading=head_only, caption=caption)
        return {
            "metric": metric, "table_index": tab["index"],
            "row_index": row_index, "column_index": idx,
            "hedged_caption": bool(u.get("from_hedged_caption")),
            "row_label": row[0], "section_heading": section,
            "column_label": (col_text or head_only)[-220:],
            "raw_text": cell, "raw_value": value,
            "unit": u.get("unit"), "unit_source": u.get("source"),
            "unit_confidence": u.get("confidence"),
            "unit_reason": u.get("reason") or "",
            "unit_overrides_caption": bool(u.get("overrides_caption")),
            "unit_overridden": u.get("overridden_unit") or "",
            "columns_aligned": aligned,
            "period": per,
            "scope": scope_of(caption, head_only),
            "window": window_of(head_only, caption),
            "context": caption[-160:],
        }
    # The row IS the metric and nothing in it could be read. Which of the
    # reasons applies matters to whoever reads the refusal, so it is carried
    # rather than dropped.
    numbers = [c for c in row[1:] if parse_number(c) is not None]
    if not numbers:
        why = "the row carries no number at all"
    elif aligned:
        why = ("the column the figure sits under names no period, or names "
               "one that had not happened when the filing was made")
    else:
        why = "the column the figure sits under names no period"
    return {"metric": metric, "table_index": tab["index"],
            "row_index": row_index, "row_label": row[0], "unit": None,
            "raw_value": None, "unusable": why, "unit_reason": why,
            "scope": scope_of(caption, head_only)}


# ── the transposed shape ────────────────────────────────────────────────────
#
# Invesco publishes assets under management as a table whose ROWS are dates
# and whose COLUMNS are asset classes, under a title row reading "Total
# Assets Under Management" and a heading row whose first data column is
# "Total". Read the ordinary way it yields nothing at all, because the row
# that carries the label carries no number. Read this way it yields the
# company-wide figure for every month it prints — and only the Total column
# is ever taken, so a strategy is never mistaken for the company.

_TOTAL = re.compile(r"(?i)^total$")


def _transposed(metric: str, spec: dict, tab: dict,
                not_after: str | None) -> list[dict]:
    rows = tab["rows"]
    if spec["family"] != MONEY or len(rows) < 3:
        return []
    title = rows[0]
    if len(title) != 1 or not matches_metric(title[0], spec):
        return []
    head = rows[1]
    total_idx = next((i for i, c in enumerate(head) if _TOTAL.match(c.strip())),
                     None)
    if total_idx is None or total_idx == 0:
        return []
    unit_text = " ".join([title[0], head[0], head[total_idx]])
    u = units.resolve(row_label=head[total_idx], section_heading=title[0],
                      column_header=head[0], table_heading=" ".join(head),
                      caption=tab["context"])
    out = []
    for row in rows[2:]:
        if len(row) != len(head):
            continue
        per = period_of(row[0])
        if not per or (not_after and per["date"] > not_after):
            continue
        value = parse_number(row[total_idx])
        if value is None:
            continue
        out.append({
            "metric": metric, "table_index": tab["index"],
            "row_index": 0, "column_index": total_idx,
            "row_label": title[0], "section_heading": row[0],
            "column_label": f"{head[total_idx]} | {row[0]}",
            "raw_text": row[total_idx], "raw_value": value,
            "unit": u.get("unit"), "unit_source": u.get("source"),
            "unit_confidence": u.get("confidence"),
            "unit_reason": u.get("reason") or "",
            "unit_overrides_caption": bool(u.get("overrides_caption")),
            "unit_overridden": u.get("overridden_unit") or "",
            "columns_aligned": True,
            "period": per,
            "scope": CONSOLIDATED,
            "window": "UNSTATED",
            "context": (unit_text + " " + tab["context"])[-160:],
            "layout": "periods in rows, total column",
        })
    return out


def find_metric(raw: bytes, metric: str, not_after: str | None = None) -> dict:
    """Every table row in this document that IS this metric."""
    spec = METRICS[metric]
    found: list[dict] = []
    for tab in tables(raw):
        rows = tab["rows"]
        for i, row in enumerate(rows):
            if not row or not matches_metric(row[0], spec):
                continue
            got = _candidate(metric, spec, tab, rows, i, not_after)
            if got:
                found.append(got)
        found.extend(_transposed(metric, spec, tab, not_after))
    return {"metric": metric, "candidates": found}


def _plausible(spec, value) -> bool:
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and value < lo:
        return False
    return not (hi is not None and value > hi)


def resolve(metric: str, candidates: list[dict]) -> dict:
    """One value, or a refusal that says why there is not one."""
    spec = METRICS[metric]
    out = {"metric": metric, "label": spec["label"], "value": None,
           "reason": "", "confidence": LOW, "kind": spec["kind"],
           "unit": spec["unit"], "provenance": None,
           "candidates": len(candidates)}
    if not candidates:
        want = (spec.get("aliases") or ("funds from operations attributable "
                                        "to common shareholders",))[0]
        out["reason"] = (f"No table in this filing has a row labelled "
                         f"\"{want}\".")
        return out

    priced, refused = [], []
    for c in candidates:
        if c.get("raw_value") is None:
            refused.append(c.get("unusable") or "the row carries no figure")
            continue
        if not c.get("unit"):
            refused.append(c.get("unit_reason") or "the unit is not stated")
            continue
        # A balance printed with a decimal fraction, in a table whose caption
        # names a scale and then excepts something from it without saying
        # what, and with nothing more specific anywhere. Filers print exact
        # millions as whole numbers and rounded billions with one decimal, so
        # T. Rowe Price's "1,893.4" inside a millions table is billions —
        # and reading it either way is a guess worth a factor of a thousand.
        if (c.get("hedged_caption") and spec["kind"] == BALANCE
                and float(c["raw_value"]) != int(c["raw_value"])):
            refused.append(
                "the table says its figures are in one scale \"except\" for "
                "some it does not name, this row is printed with a decimal, "
                "and nothing more specific says which scale applies to it — "
                "so it is a guess worth a factor of a thousand")
            continue
        norm = units.normalise(c["raw_value"], c["unit"], spec["family"])
        if norm["value"] is None:
            refused.append(norm["reason"])
            continue
        if not _plausible(spec, norm["value"]):
            refused.append("the figure is outside the range this measure "
                           "can plausibly take")
            continue
        priced.append({**c, "value": norm["value"]})

    if not priced:
        why = sorted({r for r in refused if r})
        out["reason"] = (
            f"A row labelled {spec['label'].lower()} was found but not used, "
            f"because " + "; and ".join(why or ["it carries no usable figure"])
            + ".")
        return out

    # A segment figure is never a company figure. Where the filer says which
    # tables are consolidated, only those are read; where it never says, the
    # unmarked ones are read and must agree with each other.
    by_scope = {s: [p for p in priced if p["scope"] == s]
                for s in (CONSOLIDATED, UNSTATED_SCOPE, SEGMENT)}
    chosen = by_scope[CONSOLIDATED] or by_scope[UNSTATED_SCOPE]
    if not chosen:
        out["reason"] = (
            f"{MIXED_SCOPE} — every row labelled {spec['label'].lower()} in "
            f"this filing sits in a table the company heads as one segment "
            f"rather than the whole business, so none of them describes the "
            f"company.")
        return out

    newest = max(p["period"]["date"] for p in chosen)
    same = [p for p in chosen if p["period"]["date"] == newest]
    if len({round(p["value"], 6) for p in same}) > 1:
        out["reason"] = (
            f"{AMBIGUOUS} — {len(same)} different rows in this filing are "
            f"labelled {spec['label'].lower()} for {newest} and they do not "
            f"agree, so none of them is used.")
        out["disagreeing"] = sorted({round(p["value"], 4) for p in same})[:6]
        return out

    best = same[0]
    out["value"] = best["value"]
    out["confidence"] = _grade(best)
    out["provenance"] = {
        "table_index": best["table_index"], "row_label": best["row_label"],
        "section_heading": best.get("section_heading") or "",
        "column_label": best["column_label"], "raw_text": best["raw_text"],
        "raw_value": best["raw_value"],
        "raw_unit": best.get("unit") or "",
        "resolved_unit": best.get("unit") or "",
        "unit_source": best.get("unit_source") or "",
        "unit_confidence": best.get("unit_confidence") or "",
        "unit_overrides_caption": bool(best.get("unit_overrides_caption")),
        "unit_overridden": best.get("unit_overridden") or "",
        "scale_word": best.get("unit") or "",
        "columns_aligned": bool(best.get("columns_aligned")),
        "column_index": best.get("column_index"),
        "period": best["period"]["date"],
        "period_precision": best["period"]["precision"],
        "period_is_month_end": _month_end(best["period"]["date"]),
        "scope": best.get("scope") or UNSTATED_SCOPE,
        "layout": best.get("layout") or "periods in columns",
        "window": best["window"], "table_context": best["context"],
        "method": "filing table row label match",
        "tables_version": TABLES_VERSION,
        "units_version": units.UNITS_VERSION,
    }
    if spec.get("ffo_basis"):
        out["provenance"]["basis"] = spec["ffo_basis"]
    return out


def _grade(best: dict) -> str:
    """How sure this reading is: the period and the unit both have to be
    firm before it counts as high."""
    day = best["period"]["precision"] == "day"
    firm_unit = (best.get("unit_confidence") == HIGH
                 or best.get("unit_source") == "metric definition")
    if day and firm_unit:
        return HIGH
    if day or firm_unit:
        return MODERATE
    return LOW


def read(raw: bytes, metric: str, not_after: str | None = None) -> dict:
    return resolve(metric, find_metric(raw, metric, not_after)["candidates"])


# ── sanity against what the last filing said ────────────────────────────────

OK, FLAGGED, UNIT_ERROR = "OK", "FLAGGED", "UNIT ERROR"


def continuity(metric: str, value, previous) -> dict:
    """Whether this reading is a sane step from the last one.

    A thousandfold jump is a unit error and the reading is refused. A large
    but possible move is FLAGGED and still shown, because continuity is not
    allowed to overrule what the filing says — it may raise a hand, and it
    may never quietly rewrite a number or its unit.
    """
    spec = METRICS[metric]
    p = previous
    if value is None or p in (None, 0):
        return {"ok": True, "state": OK, "reason": "", "ratio": None}
    ratio = value / p if p else None
    if ratio is not None and (ratio > 100 or (0 < ratio < 0.01)):
        return {"ok": False, "state": UNIT_ERROR, "ratio": ratio,
                "reason": (f"{spec['label']} would have moved by a factor of "
                           f"{ratio:,.0f} since the last filing, which is a "
                           f"unit error rather than a business event, so the "
                           f"new reading is refused.")}
    if spec["kind"] == BALANCE and ratio is not None and (ratio > 3 or ratio < 0.33):
        return {"ok": True, "state": FLAGGED, "ratio": ratio,
                "reason": (f"{spec['label']} has changed by "
                           f"{abs(ratio - 1) * 100:,.0f}% since the last "
                           f"filing. That is a large move for this measure, "
                           f"so it is shown with this note rather than "
                           f"silently — the filing's own figure and unit are "
                           f"used exactly as printed.")}
    return {"ok": True, "state": OK, "reason": "", "ratio": ratio}


# ── the cache ───────────────────────────────────────────────────────────────
#
# A filing is immutable, so a reading of one is too. The key is the
# accession and the document inside it; a later filing writes a new key and
# never edits an old one.

_MEM: dict = {}
_DIR = None


def configure(data_dir=None) -> None:
    global _DIR
    if not data_dir:
        _DIR = None
        return
    from pathlib import Path
    _DIR = Path(data_dir) / "invest" / "tables"
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
    except Exception:                                # pragma: no cover
        _DIR = None


def _key(accession: str, document: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "", f"{accession}__{document}")
    return f"{safe[:170]}__{TABLES_VERSION.rsplit('-', 1)[-1]}"


def cached(accession: str, document: str):
    k = _key(accession, document)
    if k in _MEM:
        return _MEM[k]
    if _DIR is not None:
        p = _DIR / f"{k}.json"
        if p.exists():
            try:
                got = json.loads(p.read_text())
                _MEM[k] = got
                return got
            except Exception:
                return None
    return None


def remember(accession: str, document: str, payload: dict) -> bool:
    """Write a reading once. An accession already on disk is never rewritten,
    because the filing behind it never changed."""
    k = _key(accession, document)
    if cached(accession, document) is not None:
        return False
    _MEM[k] = payload
    if _DIR is not None:
        p = _DIR / f"{k}.json"
        if p.exists():
            return False
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=1, sort_keys=True))
            tmp.replace(p)
        except Exception:                            # pragma: no cover
            return False
    return True


# ── which documents in a filing are worth reading ───────────────────────────

_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

# Forms that carry operating tables. The 8-K is where the earnings release
# exhibit lives, and the earnings release is where client assets, assets
# under management and a published combined ratio are actually printed.
TABLE_FORMS = ("8-K", "10-Q", "10-K")

# Filenames that are never worth fetching: the EDGAR index pages, and the
# R1.htm … R120.htm files, which are the XBRL viewer's own renderings of
# statements already available as Company Facts.
_SKIP_DOC = re.compile(r"(?i)(-index|index-headers|^R\d+\.htm|FilingSummary"
                       r"|MetaLinks|report\.css|Show\.js)")

# Realty Income files three or four 8-Ks between earnings — bond offerings,
# each carrying a 700KB indenture — and reading the largest document in each
# of those is both slow and pointless. What is dropped is what the filename
# identifies as something OTHER than the earnings release: an exhibit
# numbered anything but 99, and the 8-K cover body itself.
#
# The filter is written that way round on purpose. An EX-99 exhibit is not
# obliged to say so in its filename — T. Rowe Price's is
# "earningsreleaseq22026.htm" — and the EDGAR directory entry carries only a
# name and a size, not the exhibit type. Keeping everything that is not
# demonstrably something else costs one fetch and loses nothing.
_NOT_THE_RELEASE = re.compile(r"(?i)(?:ex|exhibit)[\-_\s]?(?!99)\d+"
                              r"|[\-_]8k\.htm")

# How many filings back to look, and how many documents inside each. Both
# are small on purpose: this is one extra fetch per document, throttled by
# the shared SEC transport.
MAX_FILINGS = 12
MAX_DOCS = 3
DOC_LIMIT = 12_000_000


def documents(sec, cik: int, accession: str, form: str = "") -> list[dict]:
    """The HTML documents inside one filing, largest first.

    An earnings release exhibit is the biggest HTML file in an 8-K, every
    time, because the rest of the filing is a two-paragraph cover page.
    """
    acc = (accession or "").replace("-", "")
    if not cik or not acc:
        return []
    try:
        raw = sec._fetch(_INDEX_URL.format(cik=cik, acc=acc))   # noqa: SLF001
        items = (json.loads(raw.decode("utf-8", "replace"))
                 .get("directory") or {}).get("item") or []
    except Exception:
        return []
    out = []
    for it in items:
        name = it.get("name") or ""
        if not name.lower().endswith((".htm", ".html")):
            continue
        if _SKIP_DOC.search(name):
            continue
        if form == "8-K" and _NOT_THE_RELEASE.search(name):
            continue
        try:
            size = int(it.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append({"name": name, "size": size,
                    "url": _DOC_URL.format(cik=cik, acc=acc, doc=name)})
    out.sort(key=lambda d: d["size"], reverse=True)
    return out


def read_document(sec, cik, accession, doc, wanted, filed=None) -> dict:
    """Every wanted metric in one document, read once and cached forever."""
    hit = cached(accession, doc["name"])
    if hit is not None:
        return hit
    try:
        raw = sec._fetch(doc["url"], limit=DOC_LIMIT)           # noqa: SLF001
    except Exception:
        return {"metrics": {}, "document": doc["name"],
                "accession": accession, "error": "document could not be read"}
    payload = {"accession": accession, "document": doc["name"],
               "url": doc["url"], "cik": cik, "metrics": {}}
    for name in wanted:
        got = read(raw, name, not_after=filed)
        if got["value"] is None and not got["reason"].startswith("No table"):
            payload["metrics"][name] = got
        elif got["value"] is not None:
            payload["metrics"][name] = got
    remember(accession, doc["name"], payload)
    return payload


def collect(sec, symbol: str, wanted=None, max_filings: int = MAX_FILINGS,
            max_docs: int = MAX_DOCS) -> dict:
    """The newest reading of each wanted metric, with what came before it.

    Readings are kept per filing, newest first, so a value can be trended
    and a sudden thousandfold move can be caught before it reaches a screen.
    Nothing already written is edited: a new filing adds a new entry.
    """
    wanted = tuple(wanted or METRICS.keys())
    sym = (symbol or "").upper().strip()
    out = {"symbol": sym, "readings": {}, "history": {},
           "filings_read": 0, "version": TABLES_VERSION}
    if not sym or sec is None:
        return out
    try:
        cik = sec.cik_for(sym)
        rows = sec.filings(sym)
    except Exception:
        return out
    if not cik or not rows:
        return out

    series: dict[str, list[dict]] = {name: [] for name in wanted}
    for row in [r for r in rows if r.get("form") in TABLE_FORMS][:max_filings]:
        acc = row.get("accession") or ""
        for doc in documents(sec, cik, acc, row.get("form") or "")[:max_docs]:
            payload = read_document(sec, cik, acc, doc, wanted,
                                    filed=row.get("date"))
            out["filings_read"] += 1
            for name, got in (payload.get("metrics") or {}).items():
                if got.get("value") is None:
                    continue
                series.setdefault(name, []).append({
                    "value": got["value"],
                    "confidence": got["confidence"],
                    "form": row.get("form"), "filed": row.get("date"),
                    "accession": acc, "document": doc["name"],
                    "provenance": got.get("provenance") or {},
                })

    for name, rowset in series.items():
        if not rowset:
            continue
        rowset.sort(key=lambda r: ((r["provenance"] or {}).get("period") or "",
                                   r["filed"] or ""), reverse=True)
        # One value per period; the first filing to report a period wins,
        # so a later restatement never rewrites what was already recorded.
        seen, keep = set(), []
        for r in rowset:
            per = (r["provenance"] or {}).get("period")
            if per in seen:
                continue
            seen.add(per)
            keep.append(r)
        newest = keep[0]
        prev = keep[1] if len(keep) > 1 else None
        check = continuity(name, newest["value"],
                           prev["value"] if prev else None)
        out["history"][name] = keep[:8]
        out["readings"][name] = {
            **newest, "metric": name, "label": METRICS[name]["label"],
            "kind": METRICS[name]["kind"], "unit": METRICS[name]["unit"],
            "continuity": check,
            "usable": bool(check["ok"]),
            "reason": "" if check["ok"] else check["reason"],
            "warning": check["reason"] if check["state"] == FLAGGED else "",
            "previous": prev["value"] if prev else None,
            "previous_period": ((prev["provenance"] or {}).get("period")
                                if prev else None),
        }
    return out
