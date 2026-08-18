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

  * A controlled taxonomy. Ten metrics, each with an explicit list of row
    labels. "Assets" is not client assets. "Net flows" from one segment is
    not company-wide net new assets.
  * Exact label matching after normalisation, never "close enough". A row
    label either is one of the listed aliases or it is not a match.
  * A period on every number. A figure whose column names no period is
    refused, because a number without a date cannot be trended, compared or
    checked.
  * Units read from the table, not assumed. "In millions" and "in billions"
    differ by a factor of a thousand, and a thousandfold error must never
    reach a screen.
  * Ambiguity refused. Two plausible rows produce
    "N/A — AMBIGUOUS TABLE MATCH", not a coin toss.
  * Provenance on everything: accession, document, table, row label, column
    label, period, unit, the raw text and the parsed number.
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

TABLES_VERSION = "invest-filing-tables-1.0.0"

HIGH, MODERATE, LOW = "HIGH", "MODERATE", "LOW"

# ── the controlled taxonomy ─────────────────────────────────────────────────
#
# BALANCE is a stock measured at a date; FLOW is a movement over a period.
# Confusing the two is the difference between "clients hold $13 trillion" and
# "clients added $13 trillion", so the kind is carried and checked.

BALANCE, FLOW, RATIO = "BALANCE", "FLOW", "RATIO"

METRICS: dict[str, dict] = {
    "client_assets": {
        "label": "Client assets",
        "kind": BALANCE, "unit": "USD",
        "aliases": ("total client assets", "client assets",
                    "customer equity", "total customer equity",
                    "total platform assets", "platform assets",
                    "total client asset balances"),
        "min": 1e8, "max": 5e13,
    },
    "assets_under_administration": {
        "label": "Assets under administration",
        "kind": BALANCE, "unit": "USD",
        "aliases": ("total assets under administration",
                    "assets under administration",
                    "client assets under administration",
                    "total client assets under administration"),
        "min": 1e8, "max": 5e13,
    },
    "assets_under_management": {
        "label": "Assets under management",
        "kind": BALANCE, "unit": "USD",
        "aliases": ("total assets under management", "assets under management",
                    "total aum", "ending assets under management",
                    "assets under management (aum)"),
        "min": 1e8, "max": 5e13,
    },
    "advisory_assets": {
        "label": "Advisory assets",
        "kind": BALANCE, "unit": "USD",
        "aliases": ("total advisory assets", "advisory assets",
                    "advisory and brokerage assets"),
        "min": 1e8, "max": 5e13,
    },
    "net_new_assets": {
        "label": "Net new assets",
        "kind": FLOW, "unit": "USD",
        "aliases": ("net new assets", "total net new assets",
                    "net new client assets", "core net new assets"),
        "min": -5e12, "max": 5e12,
    },
    "net_flows": {
        "label": "Net flows",
        "kind": FLOW, "unit": "USD",
        "aliases": ("net flows", "total net flows", "long-term net flows"),
        "min": -5e12, "max": 5e12,
    },
    "published_combined_ratio": {
        "label": "Published combined ratio",
        "kind": RATIO, "unit": "percent",
        "aliases": ("combined ratio", "total combined ratio",
                    "gaap combined ratio"),
        "min": 30.0, "max": 300.0,
    },
    "published_loss_ratio": {
        "label": "Published loss ratio",
        "kind": RATIO, "unit": "percent",
        "aliases": ("loss ratio", "loss and loss adjustment expense ratio",
                    "loss and loss expense ratio"),
        "min": 10.0, "max": 250.0,
    },
    "published_expense_ratio": {
        "label": "Published expense ratio",
        "kind": RATIO, "unit": "percent",
        "aliases": ("expense ratio", "underwriting expense ratio"),
        "min": 1.0, "max": 90.0,
    },
    "published_ffo": {
        "label": "Published funds from operations",
        "kind": FLOW, "unit": "USD",
        "aliases": ("funds from operations",
                    "funds from operations attributable to common stockholders",
                    "funds from operations available to common stockholders",
                    "diluted funds from operations"),
        "min": 1e6, "max": 5e11,
    },
}

AMBIGUOUS = "N/A — AMBIGUOUS TABLE MATCH"


# ── HTML into tables ────────────────────────────────────────────────────────

_TABLE = re.compile(rb"(?is)<table\b[^>]*>(.*?)</table>")
_ROW = re.compile(rb"(?is)<tr\b[^>]*>(.*?)</tr>")
_CELL = re.compile(rb"(?is)<t([dh])\b([^>]*)>(.*?)</t\1>")
_TAGS = re.compile(rb"(?is)<(script|style)[^>]*>.*?</\1>")
_ANY = re.compile(rb"(?s)<[^>]+>")


def _text(chunk: bytes) -> str:
    s = _ANY.sub(b" ", _TAGS.sub(b" ", chunk)).decode("utf-8", "replace")
    s = _html.unescape(s)
    return re.sub(r"[\s ​]+", " ", s).strip()


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


# ── units ───────────────────────────────────────────────────────────────────

_SCALE = (
    (re.compile(r"in\s+(?:thousands|000s)", re.I), 1e3, "thousands"),
    (re.compile(r"in\s+millions", re.I), 1e6, "millions"),
    (re.compile(r"in\s+billions", re.I), 1e9, "billions"),
    (re.compile(r"in\s+trillions", re.I), 1e12, "trillions"),
)


def scale_of(text: str):
    """The multiplier a table states for its own numbers.

    Returns (multiplier, wording) or (None, "") when the table never says.
    A table that never says its scale is refused for a money metric rather
    than guessed at, because the guess is worth a factor of a thousand.
    """
    for rx, mult, word in _SCALE:
        if rx.search(text or ""):
            return mult, word
    return None, ""


# "(in millions, except per-share data)" — the caption hedges, and does not
# say which rows the exception covers. T. Rowe Price prints ending assets
# under management as "$1,893.4" inside such a table, meaning 1,893.4
# BILLION; reading it on the caption's scale makes a $1.9 trillion book of
# other people's money into $1.9 billion, an error of a thousandfold.
_HEDGED = re.compile(r"(?i)in\s+(?:thousands|millions|billions)[^)]{0,60}\bexcept\b")


def hedged_caption(text: str) -> bool:
    return bool(_HEDGED.search(text or ""))


def scale_for(row_label: str, head: str, context: str):
    """The scale that applies to one row, most local statement winning.

    Interactive Brokers prints "Customer Equity (in billions)" inside a
    table captioned in thousands, and Robinhood prints "Total Platform
    Assets (in billions)" inside a table captioned in millions. Reading the
    caption instead of the row understates both by a factor of a thousand,
    so the row's own words come first, then the table's heading rows, then
    the sentence immediately before the table.
    """
    for where, label in ((row_label, "row"), (head, "table"),
                         (context, "caption")):
        mult, word = scale_of(where)
        if mult:
            return mult, word, label
    return None, "", ""


# ── periods ─────────────────────────────────────────────────────────────────

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"))}
_MON3 = {m[:3]: i + 1 for i, m in enumerate(_MONTHS)}

_DATE_FULL = re.compile(
    r"(?i)\b(" + "|".join(_MONTHS) + r"|" + "|".join(_MON3) +
    r")\.?\s+(\d{1,2})\s*,?\s*(\d{4})\b")
# "6/30/2026" — T. Rowe Price heads every column this way.
_DATE_SLASH = re.compile(r"\b(1[0-2]|0?[1-9])/(3[01]|[12]\d|0?[1-9])/((?:19|20)\d{2})\b")
_QUARTER = re.compile(r"(?i)\b(?:Q([1-4])[\s\-']?(\d{2,4})|([1-4])Q[\s\-']?(\d{2,4}))\b")
_YEAR_ONLY = re.compile(r"\b(19|20)(\d{2})\b")
# "Three Months Ended June 30," with the year on the row below it — the
# commonest heading in every filing measured. The day and the year arrive in
# different cells, so they are paired rather than read from one token.
_DATE_NO_YEAR = re.compile(
    r"(?i)\b(" + "|".join(_MONTHS) + r"|" + "|".join(_MON3) +
    r")\.?\s+(\d{1,2})\s*,")

_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


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
    m = _QUARTER.search(lab)
    if m:
        q = int(m.group(1) or m.group(3))
        yr = m.group(2) or m.group(4)
        year = int(yr) if len(yr) == 4 else 2000 + int(yr)
        mon, day = _QUARTER_END[q]
        return {"date": date(year, mon, day).isoformat(),
                "precision": "quarter", "text": m.group(0)}
    m = _YEAR_ONLY.search(lab)
    if m:
        return {"date": f"{m.group(1)}{m.group(2)}-12-31",
                "precision": "year", "text": m.group(0)}
    return None


# A column that compares this period against the last one is not a period.
_COMPARISON = re.compile(
    r"(?i)(%\s*change|change\s*%|vs\.?|versus|growth|variance|"
    r"increase\s*\(decrease\)|year[- ]over[- ]year|yoy|qoq)")

# Year-to-date and quarter figures are not interchangeable.
_YTD = re.compile(r"(?i)(year[- ]to[- ]date|ytd|six months|nine months|"
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
    scale is read separately, from `scale_in`, and it takes priority over
    the table's own caption precisely because it is more local.
    """
    s = (text or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)                # footnotes, abbreviations, scale
    s = re.sub(r"[‘’'“”\"]", "", s)
    s = re.sub(r"[^a-z0-9%&/\- ]+", " ", s)
    s = re.sub(r"\s+\d+\s*$", " ", s)               # a trailing footnote number
    s = re.sub(r"\s+", " ", s).strip(" :-")
    return s


# ── finding a metric in a document ──────────────────────────────────────────

# How many rows at the top of a table are read for its scale and its
# periods. Trying to identify "the heading rows" exactly does not work: the
# first row of Schwab's client-asset table is ['Q2-26 % Change', '2026',
# '2025'], whose second cell parses as the number 2026. Six rows covers
# every multi-line heading measured, and reading a data row for a scale
# phrase or a date costs nothing.
_HEAD_ROWS = 6


def _head_rows(rows: list[list[str]]) -> list[list[str]]:
    return rows[:_HEAD_ROWS]


_PRECISION_RANK = {"day": 3, "quarter": 2, "year": 1}


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


def newest_period(texts, not_after: str | None = None) -> dict | None:
    """The most recent period any of these headings names.

    Column indexes cannot be trusted in a filing table: the heading rows
    carry colspans that the spacer cells hide, so the fourth heading is not
    above the fourth number. What CAN be trusted is that an earnings release
    prints the current period first and the comparatives after it — every
    release measured does — so the figure taken is the first one in the row
    and the period is the newest the heading names. Both facts are written
    into the provenance so the reading can be checked.
    """
    found = []
    for t in texts:
        for token in re.split(r"[|\n]", t or ""):
            per = period_of(token)
            if per:
                found.append(per)
    found += _paired_dates(" ".join(t or "" for t in texts))
    # A period after the filing date does not exist. A bare year in a
    # heading — "2026" above a set of quarter columns — reads as 31 December
    # and would otherwise beat the quarter the filing is actually about.
    if not_after:
        found = [p for p in found if p["date"] <= not_after]
    if not found:
        return None
    # A reporting period ends on the last day of a month. A heading that
    # names 15 July or 1 April is naming something else — the day the
    # release was issued, an acquisition date — and it must not outrank the
    # quarter end printed beside it.
    return max(found, key=lambda p: (_month_end(p["date"]),
                                     _PRECISION_RANK[p["precision"]],
                                     p["date"]))


def _month_end(iso: str) -> bool:
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        return (nxt - date(y, m, d)).days == 1
    except Exception:
        return False


def find_metric(raw: bytes, metric: str, not_after: str | None = None) -> dict:
    """Every table row in this document whose label IS one of the aliases."""
    spec = METRICS[metric]
    aliases = set(spec["aliases"])
    wants_money = spec["unit"] == "USD"
    found: list[dict] = []
    for tab in tables(raw):
        rows = tab["rows"]
        heads = _head_rows(rows)
        head_only = " ".join(" ".join(h) for h in heads)
        head_text = [" ".join(h) for h in heads]
        # Periods come from the table's own headings. The paragraph before a
        # table is prose, and prose carries the date the release was issued,
        # which is not the period the table is about: reading it gave
        # Progressive's June combined ratio a date of 15 July.
        per = (newest_period(head_text, not_after)
               or newest_period([tab["context"]], not_after))
        column_label = " | ".join(t for t in head_text + [tab["context"]]
                                  if t)[-220:]
        for row in rows:
            if normalise_label(row[0] if row else "") not in aliases:
                continue
            scale, scale_word, scale_where = scale_for(
                row[0], head_only, tab["context"])
            hedged = (scale_where in ("table", "caption")
                      and hedged_caption(head_only if scale_where == "table"
                                         else tab["context"]))
            for cell in row[1:]:
                # A percentage is never a money figure. Schwab prints two
                # "% change" columns before the dollars, and reading the
                # first number in the row without this check turns eleven
                # trillion dollars of client assets into eleven billion.
                is_pct = "%" in cell
                if wants_money and is_pct:
                    continue
                v = parse_number(cell)
                if v is None:
                    continue
                found.append({
                    "metric": metric, "table_index": tab["index"],
                    "row_label": row[0], "column_label": column_label,
                    "raw_text": cell, "raw_value": v,
                    "scale": scale if wants_money else 1.0,
                    "scale_word": scale_word if wants_money else "",
                    "scale_from": scale_where if wants_money else "",
                    "hedged": bool(wants_money and hedged),
                    "period": per,
                    "window": window_of(" ".join(head_text), tab["context"]),
                    "context": tab["context"][-160:],
                })
                break            # the first real number is the current period
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
        out["reason"] = (f"No table in this filing has a row labelled "
                         f"\"{spec['aliases'][0]}\".")
        return out

    priced = []
    hedged_dropped = 0
    for c in candidates:
        if spec["unit"] == "USD":
            if not c.get("scale"):
                continue                    # a money table that never says
            # A balance printed with a decimal fraction inside a table whose
            # caption hedges is not on the caption's scale: filers print
            # exact millions as whole numbers and rounded billions with one
            # decimal. BlackRock's "15,344,624" is millions and is used;
            # T. Rowe Price's "1,893.4" is billions in a millions table and
            # is refused rather than converted on a hunch.
            if (c.get("hedged") and spec["kind"] == BALANCE
                    and float(c["raw_value"]) != int(c["raw_value"])):
                hedged_dropped += 1
                continue
            value = c["raw_value"] * c["scale"]
        else:
            value = c["raw_value"]
        if not _plausible(spec, value):
            continue
        if not c.get("period"):
            continue
        priced.append({**c, "value": value})

    if not priced:
        why = []
        if any(not c.get("scale") for c in candidates) and spec["unit"] == "USD":
            why.append("the table never states whether its figures are in "
                       "thousands, millions or billions, and the difference "
                       "is a factor of a thousand")
        if any(not c.get("period") for c in candidates):
            why.append("the column the figure sits under names no period")
        if hedged_dropped:
            why.append("the table says its figures are in one scale \"except\" "
                       "for some it does not name, and this row is printed "
                       "with a decimal, so which scale applies to it is a "
                       "guess worth a factor of a thousand")
        if not why:
            why.append("every candidate figure is outside the range this "
                       "measure can plausibly take")
        out["reason"] = (f"A row labelled {spec['label'].lower()} was found "
                         f"but not used, because " + "; and ".join(why) + ".")
        return out

    distinct = {round(p["value"], 6) for p in priced}
    if len(distinct) > 1:
        # Same period, different numbers, and nothing in the filing says
        # which is the company-wide one.
        newest = max(p["period"]["date"] for p in priced)
        same = [p for p in priced if p["period"]["date"] == newest]
        if len({round(p["value"], 6) for p in same}) > 1:
            out["reason"] = (
                f"{AMBIGUOUS} — {len(same)} different rows in this filing are "
                f"labelled {spec['label'].lower()} for {newest} and they do "
                f"not agree, so none of them is used.")
            return out
        priced = same

    best = max(priced, key=lambda p: p["period"]["date"])
    out["value"] = best["value"]
    out["confidence"] = (HIGH if best["period"]["precision"] == "day"
                         else MODERATE)
    out["provenance"] = {
        "table_index": best["table_index"], "row_label": best["row_label"],
        "column_label": best["column_label"], "raw_text": best["raw_text"],
        "raw_value": best["raw_value"], "scale_word": best["scale_word"],
        "period": best["period"]["date"],
        "period_precision": best["period"]["precision"],
        "period_is_month_end": _month_end(best["period"]["date"]),
        "scale_from": best.get("scale_from") or "",
        "window": best["window"], "table_context": best["context"],
        "method": "filing table row label match",
        "tables_version": TABLES_VERSION,
    }
    return out


def read(raw: bytes, metric: str, not_after: str | None = None) -> dict:
    return resolve(metric, find_metric(raw, metric, not_after)["candidates"])


# ── sanity against what the last filing said ────────────────────────────────

def continuity(metric: str, value, previous) -> dict:
    """Whether this reading is a sane step from the last one.

    A thousandfold jump is a unit error, not news, and it is rejected here
    rather than drawn on a chart.
    """
    spec = METRICS[metric]
    p = previous
    if value is None or p in (None, 0):
        return {"ok": True, "reason": "", "ratio": None}
    ratio = value / p if p else None
    if ratio is not None and (ratio > 100 or (0 < ratio < 0.01)):
        return {"ok": False, "ratio": ratio,
                "reason": (f"{spec['label']} would have moved by a factor of "
                           f"{ratio:,.0f} since the last filing, which is a "
                           f"unit error rather than a business event, so the "
                           f"new reading is refused.")}
    if spec["kind"] == BALANCE and ratio is not None and (ratio > 3 or ratio < 0.33):
        return {"ok": False, "ratio": ratio,
                "reason": (f"{spec['label']} would have changed by "
                           f"{abs(ratio - 1) * 100:,.0f}% in one quarter, "
                           f"which no book of client money does, so the "
                           f"reading is held back for checking.")}
    return {"ok": True, "reason": "", "ratio": ratio}


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
    return safe[:180]


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

# How many filings back to look, and how many documents inside each. Both
# are small on purpose: this is one extra fetch per document, throttled by
# the shared SEC transport.
MAX_FILINGS = 8
MAX_DOCS = 3
DOC_LIMIT = 12_000_000


def documents(sec, cik: int, accession: str) -> list[dict]:
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
        for doc in documents(sec, cik, acc)[:max_docs]:
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
            "previous": prev["value"] if prev else None,
            "previous_period": ((prev["provenance"] or {}).get("period")
                                if prev else None),
        }
    return out
