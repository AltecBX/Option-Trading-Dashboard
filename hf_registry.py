"""hf_registry.py — who is watched, and under which EDGAR identities.

A hedge fund is not a CIK. Pershing Square's book moved from one EDGAR
filer to another in 2026; Greenlight's did in 2024; JANA's in 2023. A
watchlist keyed by a single number would have shown each of them as
"stopped filing" — the precise opposite of the truth. So a manager here is
a person or firm with an ORDERED list of identities, and the current one is
whichever has no end date.

Two other things this module knows that the filings do not say:

  * `turnover` — READABLE when a quarter-on-quarter change in the 13F means
    something (a concentrated book), OPAQUE when it does not (multi-strategy
    and quant books whose top lines are index options and whose positions
    turn over in days). The card refuses to summarise an OPAQUE 13F as a
    view, and the flag is what makes it refuse.
  * `status` — FILING, SUCCESSOR (the current identity took over from an
    earlier one), or CEASED (Scion). CEASED is a fact about the manager, not
    a gap in the data.

Pure: no network, no I/O beyond reading the seed file and the user overlay
that the caller hands in. HEDGE_FUND_INTEL.md §5a.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

HF_REGISTRY_VERSION = "1.0.0"

TURNOVER = ("READABLE", "OPAQUE")
STATUS = ("FILING", "SUCCESSOR", "CEASED")

_SEED_PATH = Path(__file__).resolve().parent / "hf_watchlist.json"


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except (TypeError, ValueError):
        return None


def load_seed(path: Path | None = None) -> dict:
    p = path or _SEED_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def merge(seed: dict, overlay: dict | None) -> dict:
    """The user's edits over the shipped seed.

    Overlay shape: {"managers": [...], "removed": ["key", ...]}. A manager in
    the overlay with the same `key` replaces the seed entry wholesale; a new
    key is appended; a key in `removed` is dropped. Nothing in the seed is
    ever edited in place, so a rebuild cannot lose a user's change."""
    out = {"version": seed.get("version"), "managers": []}
    removed = set((overlay or {}).get("removed") or [])
    by_key = {m["key"]: m for m in (overlay or {}).get("managers") or [] if m.get("key")}
    seen = set()
    for m in seed.get("managers") or []:
        k = m.get("key")
        if not k or k in removed:
            continue
        out["managers"].append(by_key.get(k, m))
        seen.add(k)
    for k, m in by_key.items():
        if k not in seen and k not in removed:
            out["managers"].append(m)
    return out


def validate(entry: dict) -> list[str]:
    """Every reason an entry is not usable, in words. Empty means fine."""
    problems = []
    if not entry.get("key"):
        problems.append("missing key")
    if not entry.get("name"):
        problems.append("missing name")
    if entry.get("turnover") not in TURNOVER:
        problems.append(f"turnover must be one of {TURNOVER}")
    if entry.get("status", "FILING") not in STATUS:
        problems.append(f"status must be one of {STATUS}")
    ciks = entry.get("ciks") or []
    if not ciks:
        problems.append("at least one CIK is required")
    for c in ciks:
        try:
            if int(c.get("cik")) <= 0:
                problems.append("CIK must be positive")
        except (TypeError, ValueError, AttributeError):
            problems.append("CIK must be an integer")
    return problems


def current_cik(entry: dict) -> int | None:
    """The identity that reports the book today: the last one with no end
    date. A CEASED manager has none."""
    if entry.get("status") == "CEASED":
        return None
    for c in reversed(entry.get("ciks") or []):
        if not c.get("to"):
            try:
                return int(c["cik"])
            except (TypeError, ValueError):
                continue
    return None


def all_ciks(entry: dict) -> list[int]:
    out = []
    for c in entry.get("ciks") or []:
        try:
            out.append(int(c["cik"]))
        except (TypeError, ValueError):
            continue
    return out


def last_cik(entry: dict) -> int | None:
    """The most recent identity even if it has ended — what a CEASED
    manager's last filings sit under."""
    ids = all_ciks(entry)
    return ids[-1] if ids else None


def status_of(entry: dict) -> str:
    if entry.get("status") == "CEASED":
        return "CEASED"
    return "SUCCESSOR" if len(entry.get("ciks") or []) > 1 else "FILING"


def cik_index(registry: dict) -> dict[int, dict]:
    """CIK → manager entry, over every identity a manager has ever had, so
    a filing under an old CIK still lands on the right card."""
    out = {}
    for m in registry.get("managers") or []:
        for c in all_ciks(m):
            out[c] = m
    return out


def link_successor(entry: dict, new_cik: int, new_name: str, period_end: str) -> dict:
    """Record that `new_cik` reports the book from `period_end` on.

    Called when the current identity files a 13F-NT whose "other managers"
    block names the successor. Closes the old identity at the period the
    notice covered and appends the new one. Idempotent: a successor already
    linked is left alone."""
    e = json.loads(json.dumps(entry))
    ids = e.setdefault("ciks", [])
    if any(int(c.get("cik") or 0) == int(new_cik) for c in ids):
        return e
    for c in reversed(ids):
        if not c.get("to"):
            c["to"] = period_end
            c.setdefault("note", "")
            c["note"] = (c["note"] + " — " if c["note"] else "") + \
                f"filed a 13F-NT for {period_end}; successor {new_name}"
            break
    ids.append({"cik": int(new_cik), "from": period_end,
                "note": f"{new_name} — discovered from the 13F-NT"})
    return e


def describe(entry: dict) -> str:
    """One sentence for the tooltip: what kind of book this is and how far
    its 13F can be trusted as a view."""
    style = entry.get("style") or "fund"
    if entry.get("status") == "CEASED":
        return (f"{entry.get('name')} no longer files with the SEC. Anything shown is a "
                f"public statement, not a position.")
    if entry.get("turnover") == "OPAQUE":
        return (f"A {style} book. Its 13F shows thousands of hedged, fast-turnover lines "
                f"on one day 45 or more days ago — read it as what was hedged, never "
                f"as a view on a stock or a sector.")
    return (f"A {style} book. Concentrated enough that a quarter-on-quarter change "
            f"in its 13F usually means a decision was made.")
