"""Hedge Fund Intelligence — the names behind the crowd (Phase 5B).

The Pulse can say Financials is crowded and the short side already names
stocks: `hf_pulse.crowded_shorts` reads FINRA's short interest and lists the
most crowded single-name shorts, anonymously, because that data belongs to
nobody. The long side had no equivalent. This is it, and unlike the short
side it is fully attributable: every row here comes from a named manager's
own 13F, so the funds can be named beside it.

Four rules keep it from becoming fiction:

  * **An OPAQUE book is not a view.** A multi-strategy or quant 13F shows
    hedging, index exposure and the long leg of trades whose short leg never
    appears in it. The fund cards already refuse to summarise those as
    conviction; counting them here would be that same fiction at scale, so
    only READABLE managers are counted, and the card says how many were left
    out and why.

  * **A put is not ownership.** A 13F lists options alongside shares. A
    manager holding puts on a name is betting against it, and counting that
    row as one of the funds "in" the stock would report the position exactly
    backwards. Puts are separated out, not silently folded in.

  * **It counts what it can actually see.** Only each manager's ten largest
    positions are kept, so this measures how many managers hold a name among
    their ten largest — never "most owned", which would need whole books.
    Every label says so.

  * **Different managers, different quarters.** 13Fs land 45 days after
    their period and managers do not file together, so a consensus row can
    mix a June book with a March one. The span is carried on every row
    rather than a single date that would imply they were all true at once.

It is pure: dictionaries in, a dictionary out. No network, no files, no
clock.
"""

from __future__ import annotations

import hf_sources as S

HF_NAMES_VERSION = "1.0.0"

MIN_MANAGERS = 2       # one manager holding a stock is not a crowd
TOP = 15               # rows shown per table
READABLE = "READABLE"


def _is_put(row: dict) -> bool:
    return str(row.get("put_call") or "").strip().upper() == "PUT"


def _is_call(row: dict) -> bool:
    return str(row.get("put_call") or "").strip().upper() == "CALL"


def _label(row: dict) -> str | None:
    """What to call the position. The ticker when the CUSIP map knew it, the
    issuer name when it did not — never a blank row and never a bare CUSIP,
    which tells a reader nothing."""
    sym = (row.get("symbol") or "").strip().upper()
    return sym or (row.get("issuer") or "").strip() or None


def _key(row: dict) -> str | None:
    """Group by ticker where there is one, by issuer name where there is not.

    Grouping on CUSIP would be more precise and would also split the same
    company across share classes and option lines, which is not the question
    being asked."""
    return _label(row)


def eligible(managers: list[dict] | None) -> tuple[list[dict], dict]:
    """The managers whose books can be read as views, and a note on the rest.

    Returned together on purpose: a consensus of eleven managers means
    something different from a consensus of eleven out of thirty-two, and the
    reader is owed the denominator."""
    managers = managers or []
    keep, opaque, unread = [], [], []
    for m in managers:
        if not (m.get("top") or m.get("change")):
            unread.append(m.get("name") or m.get("key"))
        elif str(m.get("turnover") or "").upper() != READABLE:
            opaque.append(m.get("name") or m.get("key"))
        else:
            keep.append(m)
    return keep, {
        "n_counted": len(keep), "n_managers": len(managers),
        "n_opaque": len(opaque), "opaque": sorted(opaque),
        "n_not_read": len(unread), "not_read": sorted(unread),
        "why_opaque": ("A multi-strategy or quant 13F shows hedging and index exposure rather "
                       "than views, so those books are not counted here. The board refuses to "
                       "read them as conviction on the fund cards for the same reason."),
    }


def _gather(managers: list[dict], pick, *, min_managers: int, top: int) -> list[dict]:
    """Fold one bucket of every manager's filing into rows by name."""
    acc: dict[str, dict] = {}
    for m in managers:
        seen_here = set()
        for row in pick(m) or []:
            if _is_put(row):
                continue
            name = _key(row)
            if not name or name in seen_here:
                continue
            seen_here.add(name)
            slot = acc.setdefault(name, {
                "name": name, "symbol": (row.get("symbol") or "").strip().upper() or None,
                "issuer": row.get("issuer"), "managers": [], "value": 0.0,
                "as_of": [], "n_calls": 0,
            })
            slot["managers"].append(m.get("name") or m.get("key"))
            slot["value"] += float(row.get("value") or 0.0)
            if _is_call(row):
                slot["n_calls"] += 1
            if m.get("as_of"):
                slot["as_of"].append(m["as_of"])
    rows = []
    for slot in acc.values():
        if len(slot["managers"]) < min_managers:
            continue
        dates = sorted(d for d in slot["as_of"] if d)
        rows.append({**slot, "n_managers": len(slot["managers"]),
                     "managers": sorted(slot["managers"]),
                     "as_of_first": dates[0] if dates else None,
                     "as_of_last": dates[-1] if dates else None,
                     "class": S.VERIFIED, "sub": "FILING"})
    rows.sort(key=lambda r: (-r["n_managers"], -(r["value"] or 0.0), r["name"]))
    return rows[:top]


def _unmapped(managers: list[dict]) -> dict:
    """Positions whose CUSIP the map did not know. Reported, never dropped
    silently — the same rule the sector roll-up keeps."""
    n = held = 0
    for m in managers:
        for row in (m.get("top") or []):
            held += 1
            if not (row.get("symbol") or "").strip():
                n += 1
    return {"n": n, "of": held,
            "note": ("Positions whose CUSIP the ticker map did not know. They are still counted, "
                     "under the issuer name from the filing rather than a ticker.")}


def _puts(managers: list[dict]) -> list[dict]:
    """Names held as PUTS by two or more managers — a bet against, kept apart
    from the holdings tables rather than folded into them backwards."""
    acc: dict[str, dict] = {}
    for m in managers:
        for row in (m.get("top") or []) + ((m.get("change") or {}).get("new") or []) \
                 + ((m.get("change") or {}).get("increased") or []):
            if not _is_put(row):
                continue
            name = _key(row)
            if not name:
                continue
            slot = acc.setdefault(name, {"name": name, "managers": set(),
                                         "class": S.VERIFIED, "sub": "FILING"})
            slot["managers"].add(m.get("name") or m.get("key"))
    rows = [{**v, "managers": sorted(v["managers"]), "n_managers": len(v["managers"])}
            for v in acc.values() if len(v["managers"]) >= MIN_MANAGERS]
    rows.sort(key=lambda r: (-r["n_managers"], r["name"]))
    return rows


def build(managers: list[dict] | None = None, *, min_managers: int = MIN_MANAGERS,
          top: int = TOP, as_of: str | None = None) -> dict:
    """Which names the readable books agree on, and which they are leaving."""
    counted, basis = eligible(managers)
    held = _gather(counted, lambda m: m.get("top"), min_managers=min_managers, top=top)
    bought = _gather(counted, lambda m: ((m.get("change") or {}).get("new") or [])
                     + ((m.get("change") or {}).get("increased") or []),
                     min_managers=min_managers, top=top)
    sold = _gather(counted, lambda m: ((m.get("change") or {}).get("reduced") or [])
                   + ((m.get("change") or {}).get("exited") or []),
                   min_managers=min_managers, top=top)
    dates = sorted({m["as_of"] for m in counted if m.get("as_of")})
    return {
        "version": HF_NAMES_VERSION, "as_of": as_of,
        "held": held, "bought": bought, "sold": sold,
        "puts": _puts(counted),
        "basis": basis, "unmapped": _unmapped(counted),
        "min_managers": min_managers,
        "periods": {"first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                    "n": len(dates)},
        "class": S.VERIFIED,
        "note": ("Counted from each manager's ten largest reported positions, so this says how "
                 "many readable books hold a name among their ten largest — not how many own it. "
                 "A 13F describes one day and arrives 45 days later, and managers do not file "
                 "together, so a row can mix one manager's June book with another's March."),
        "limitations": [
            "Only the ten largest positions of each manager are kept, so a name held in "
            "eleventh place by everyone would not appear here at all.",
            "13F holdings are long positions and options; short sales are not reported in "
            "them at all, so nothing here says a manager is net long a name.",
            "Managers whose books are not readable as views — multi-strategy and quant — are "
            "not counted. The number left out is shown beside the number counted.",
        ],
    }


def headline(card: dict | None) -> dict:
    """One sentence, or an honest refusal."""
    card = card or {}
    held = card.get("held") or []
    basis = card.get("basis") or {}
    if not held:
        return {"available": False,
                "text": ("No name is held among the ten largest by "
                         f"{card.get('min_managers', MIN_MANAGERS)} or more of the "
                         f"{basis.get('n_counted', 0)} readable books yet.")}
    first = held[0]
    return {"available": True, "row": first,
            "text": (f"{first['name']} is held among the ten largest positions of "
                     f"{first['n_managers']} of the {basis.get('n_counted', 0)} readable books "
                     f"— more than any other name.")}
