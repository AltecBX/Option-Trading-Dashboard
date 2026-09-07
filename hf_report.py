"""hf_report.py — the weekly hedge fund report, assembled and kept forever.

One document a week, built from three things that already exist: the Pulse
board (`hf_pulse` answered the aggregate questions), the Named Fund Watch
records (`hf_watch` read EDGAR), and the prime-broker headlines (`hf_press`).
Nothing here measures anything. If a number is in the report, some other
module computed it and this one carried it across with its date and its
evidence class attached.

That restraint is the point. A report that did its own arithmetic could
disagree with the board it claims to summarise, and the reader would have no
way to tell which was right.

It is pure: dictionaries in, a dictionary out. No network, no files, and no
clock — the caller passes the date, so a report for any week can be rebuilt
from stored inputs and comes out identical.

Three rules it keeps:

  * **The two layers never blend.** Anonymous aggregate findings and named
    fund activity are separate sections with separate evidence classes. No
    sentence in the aggregate section may carry a fund's name, and no fund's
    record may be inferred from the aggregate.

  * **The prior report is the input for "what changed".** Not a memory, not
    a guess — the stored document from last week, diffed. When there is no
    prior report the section says so instead of showing an empty list that
    looks like "nothing changed".

  * **Disagreement is printed.** Conflicts between sources, and between the
    banks quoted this week, get their own section and are never collapsed
    into an average.

HEDGE_FUND_INTEL.md §5e.
"""

from __future__ import annotations

from datetime import date

import hf_press as PRESS
import hf_pulse as P
import hf_sources as S

# 1.1.0 — `n_acted` counts managers who filed something during THIS report's
# week, where 1.0.0 counted every manager carrying the FILED SINCE state. The
# reports are kept forever, so a stored document has to say which rule
# produced it: two reports both stamped 1.0.0 would otherwise mean different
# things. Bump this whenever the stored shape or a field's meaning changes.
# 1.1.1 — the funds block carries `sentence`, which the card renders verbatim.
HF_REPORT_VERSION = "1.1.1"

# The four aggregate questions, in the order the report tells them, with the
# heading each one is printed under.
CONCLUSIONS = (
    ("exposure", "Overall exposure"),
    ("leverage", "Leverage"),
    ("longs", "The long book"),
    ("shorts", "The short book"),
)

LIMITATIONS = (
    "The prime-broker lines are secondhand: a bank's summary of its own clients, quoted by a "
    "reporter. They can support what the measured data says and can never produce a verdict.",
    "Between quarterly filings a named fund's activity is UNKNOWN. That is the normal state for "
    "roughly three hundred days a year and it is printed in that word.",
    "Four of the eleven sectors have no leveraged-fund futures contract, so Technology, "
    "Materials, Real Estate and Consumer Discretionary are read from flows and short interest "
    "only. Each sector row says how many inputs it had.",
    "Multi-strategy and quantitative 13F filings are hedge ledgers, not views, and are not "
    "summarised as opinions about a sector.",
    "Nothing here is scored against what happened next. The report records positioning; it does "
    "not claim positioning predicts returns.",
)


# ── small pure helpers ──────────────────────────────────────────────────────

def long_date(s) -> str | None:
    """Month Day, Year — the only date format this dashboard shows a reader."""
    try:
        d = date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _q(board: dict, key: str) -> dict:
    return (board or {}).get(key) or {}


def _decided(verdict) -> bool:
    """A verdict that actually says something. NO DATA and a blank both mean
    the question was asked and not answered, and neither belongs in a
    summary sentence."""
    return bool(verdict) and verdict != P.NO_DATA


# ── the four aggregate conclusions ──────────────────────────────────────────

def conclusion(board: dict, key: str, title: str) -> dict:
    """One question, carried across whole: the verdict, how confident and
    why, how long it has read that way, what fed it, and what disagreed."""
    q = _q(board, key)
    return {"key": key, "title": title, "question": q.get("question"),
            "verdict": q.get("verdict") or P.NO_DATA,
            "decided": _decided(q.get("verdict")),
            "confidence": q.get("confidence") or {},
            "streak": q.get("streak") or {},
            "persistence": q.get("persistence") or {},
            "inputs": q.get("inputs") or [],
            "conflicts": q.get("conflicts") or [],
            "missing": q.get("missing") or [],
            "note": q.get("note"),
            "as_of": board.get("cftc_as_of"),
            "as_of_text": long_date(board.get("cftc_as_of"))}


def conclusions(board: dict) -> list[dict]:
    return [conclusion(board, k, t) for k, t in CONCLUSIONS]


def trends(board: dict) -> list[dict]:
    """How persistent each answer is over two, four, eight and twelve weeks.

    A verdict that has read the same way for eight weeks is a different
    statement from the same verdict in its first week, and the brief asks for
    both to be visible."""
    out = []
    for key, title in CONCLUSIONS:
        q = _q(board, key)
        pers = q.get("persistence") or {}
        out.append({"key": key, "title": title, "verdict": q.get("verdict") or P.NO_DATA,
                    "streak": (q.get("streak") or {}).get("word"),
                    "windows": [{"weeks": w,
                                 "text": (pers.get(str(w)) or pers.get(w) or {}).get("text"),
                                 "same": (pers.get(str(w)) or pers.get(w) or {}).get("same"),
                                 "of": (pers.get(str(w)) or pers.get(w) or {}).get("of")}
                                for w in (2, 4, 8, 12)]})
    return out


# ── sectors and crowding ────────────────────────────────────────────────────

def sectors(board: dict) -> dict:
    """The sector rows exactly as the board produced them.

    An earlier draft slimmed each row to a handful of scalars. Two things
    were wrong with that. The card's sector table reads `confidence` and
    `streak` as objects and rendered a dash for every one of them, and a
    stored report lost the inputs behind each sector — so a week read back a
    year later could show a verdict and never show what produced it. A
    report is a permanent record, so the rows are carried whole."""
    sec = (board or {}).get("sectors") or {}
    return {"bought": list(sec.get("most_bought") or []),
            "sold": list(sec.get("most_sold") or []),
            "rows": list(sec.get("rows") or []),
            "no_futures": sec.get("no_futures") or [],
            "ranked_by": sec.get("ranked_by")}


def crowding(board: dict) -> dict:
    cr = (board or {}).get("crowding") or {}
    return {"crowded": cr.get("crowded") or [], "decrowding": cr.get("decrowding") or [],
            "names": cr.get("names") or [], "rule": cr.get("rule"),
            "n_markets": len(cr.get("markets") or [])}


# ── the named layer ─────────────────────────────────────────────────────────

def fund_activity(funds: dict, week_start: str | None = None) -> dict:
    """What the watched managers actually did, and how many of them are in
    the state the reader should expect: nothing new since the last quarter.

    FILED SINCE means an event landed after the manager's last quarterly
    report — it does NOT mean the event happened this week, and the state
    persists until the next 13F arrives. Listing every such manager under a
    heading that says "this week" repeated the same four names for months.
    So the section is filtered by when each filing became public, and the
    count of managers still carrying the state is reported separately."""
    managers = (funds or {}).get("managers") or []
    acted, filed_since, unknown, ceased, not_read = [], 0, 0, 0, 0
    for m in managers:
        act = m.get("activity") or {}
        st = act.get("state")
        if st == "FILED SINCE":
            filed_since += 1
            items = [i for i in (act.get("items") or [])
                     if not week_start or (i.get("public_on") or i.get("as_of") or "") >= week_start]
            if items:
                acted.append({"key": m.get("key"), "name": m.get("name"),
                              "style": m.get("style"), "turnover": m.get("turnover"),
                              "state": st, "since": act.get("since"),
                              "since_text": long_date(act.get("since")),
                              "items": items, "class": S.VERIFIED})
        elif st == "CEASED":
            ceased += 1
        elif st == "NOT READ YET":
            not_read += 1
        else:
            unknown += 1
    return {"acted": acted, "n_acted": len(acted), "n_filed_since": filed_since,
            "n_unknown": unknown, "n_ceased": ceased, "n_not_read": not_read,
            "n_managers": len(managers), "since": week_start,
            "since_text": long_date(week_start), "class": S.VERIFIED,
            "note": ("UNKNOWN is the ordinary state. A 13F describes one day, arrives forty-five "
                     "days later, and says nothing about the weeks since."),
            "carrying_note": ("Managers whose last verified filing is newer than their last "
                              "holdings report. That state lasts until the next quarterly "
                              "report arrives, so most of them did not file anything this week.")}


def new_filings(funds: dict, week_start: str | None = None) -> dict:
    """The filings worth a heading: every 13D by a watched manager, because
    that is an event with a five-business-day clock, and every amendment to
    a holdings report, because a restatement changes a number already shown."""
    rows = (funds or {}).get("new_filings") or []
    if week_start:
        rows = [r for r in rows if (r.get("filed") or "") >= week_start]
    events = [r for r in rows if str(r.get("form", "")).upper().startswith("SC 13D")]
    amendments = [r for r in rows if str(r.get("form", "")).upper().endswith("/A")
                  and "13F" in str(r.get("form", "")).upper()]
    notices = [r for r in rows if "13F-NT" in str(r.get("form", "")).upper()]

    def slim(r: dict) -> dict:
        return {"form": r.get("form"), "manager": r.get("manager"), "key": r.get("key"),
                "company": r.get("company"), "cik": r.get("cik"),
                "filed": r.get("filed"), "filed_text": long_date(r.get("filed")),
                "accession": r.get("accession"), "class": S.VERIFIED}

    return {"events": [slim(r) for r in events], "amendments": [slim(r) for r in amendments],
            "notices": [slim(r) for r in notices], "n_all": len(rows),
            "since": week_start, "since_text": long_date(week_start),
            "note": ("A 13D is an event: a five percent activist stake, filed within five business "
                     "days. A 13G is a passive quarterly notice and is not activity.")}


def watchlist(funds: dict, prior: dict | None) -> dict:
    """Who is being watched, and what moved on and off the list since the
    last report. Without a prior report there is no such thing as a change,
    and the section says that rather than showing three empty lists."""
    managers = (funds or {}).get("managers") or []
    now = {m.get("key"): m for m in managers if m.get("key")}
    prev = ((prior or {}).get("watchlist") or {}).get("managers") or {}
    added = sorted(set(now) - set(prev))
    removed = sorted(set(prev) - set(now))
    status = []
    for k, m in sorted(now.items()):
        was = (prev.get(k) or {}).get("status")
        if was and was != m.get("status"):
            status.append({"key": k, "name": m.get("name"), "from": was, "to": m.get("status")})
    return {"n": len(now), "keys": sorted(now),
            "managers": {k: {"name": m.get("name"), "status": m.get("status"),
                             "turnover": m.get("turnover")} for k, m in now.items()},
            "added": [{"key": k, "name": (now.get(k) or {}).get("name")} for k in added],
            "removed": [{"key": k, "name": (prev.get(k) or {}).get("name")} for k in removed],
            "status_changes": status,
            "comparable": prior is not None,
            "note": (None if prior is not None else
                     "This is the first stored report, so there is nothing to compare the "
                     "watchlist against yet.")}


# ── conflicts ───────────────────────────────────────────────────────────────

def conflicts(board: dict, press: dict | None) -> list[dict]:
    """Every disagreement in one place: inputs pointing opposite ways inside
    a question, sectors whose inputs split, and banks quoted this week saying
    opposite things. The brief asks for this section never to be collapsed."""
    out = []
    for key, title in CONCLUSIONS:
        q = _q(board, key)
        rows = q.get("conflicts") or []
        if rows:
            out.append({"where": title, "kind": "INPUTS DISAGREE", "verdict": q.get("verdict"),
                        "rows": rows, "n": len(rows)})
    for r in ((board or {}).get("sectors") or {}).get("rows") or []:
        rows = r.get("conflicts") or []
        if rows:
            out.append({"where": r.get("sector"), "kind": "INPUTS DISAGREE",
                        "verdict": r.get("verdict"), "rows": rows, "n": len(rows)})
    for c in (press or {}).get("conflicts") or []:
        out.append({"where": dict(CONCLUSIONS).get(c.get("about"), c.get("about")),
                    "kind": "BANKS DISAGREE", "verdict": None,
                    "rows": [{"label": f"{q['bank']}: {q['text']}", "class": S.PRIME_BROKER,
                              "direction": d, "as_of": q.get("public_on")}
                             for d, side in ((1, "saying_up"), (-1, "saying_down"))
                             for q in c.get(side) or []],
                    "n": len(c.get("saying_up") or []) + len(c.get("saying_down") or [])})
    return out


# ── the summary a person reads first ────────────────────────────────────────

def activity_sentence(f: dict) -> str:
    """One sentence about the named layer that is true in every state.

    Written as an explicit ladder because the first draft used "unknown or
    ceased" as the only test for having read anything, and fell through to
    the not-read-yet message whenever every manager was carrying an older
    filing — telling the reader that managers it had in fact read could not
    be described. Each branch below states only what its counts support."""
    total = f.get("n_managers") or 0
    acted, carrying = f.get("n_acted") or 0, f.get("n_filed_since") or 0
    unknown, ceased, unread = (f.get("n_unknown") or 0, f.get("n_ceased") or 0,
                               f.get("n_not_read") or 0)
    if acted:
        return f"{acted} watched manager(s) filed something verifiable this week."
    if not total:
        return "No managers are on the watchlist."
    if unread == total:
        # A fresh start before the first EDGAR sweep finishes. Saying "none
        # filed anything" here would report an empty cupboard as a finding.
        return (f"The Named Fund Watch has not finished its first read of EDGAR, so none of the "
                f"{total} watched managers can be described yet.")
    parts = []
    if unknown:
        parts.append(f"{unknown} remain in the ordinary UNKNOWN state between quarters")
    if carrying:
        parts.append(f"{carrying} still carry a filing newer than their last holdings report")
    if ceased:
        parts.append(f"{ceased} no longer file at all")
    if unread:
        parts.append(f"{unread} have not been read yet")
    # One "and", at the end. Joining every pair with ", and " produced
    # "27 remain UNKNOWN, and 4 still carry a filing, and 1 no longer file"
    # on the live card.
    if len(parts) > 1:
        joined = ", ".join(parts[:-1]) + ", and " + parts[-1]
    else:
        joined = parts[0] if parts else ""
    tail = f"; {joined}" if joined else ""
    return f"No watched manager filed anything this week{tail}."


def legacy_activity_sentence(f: dict) -> str:
    """The same sentence for a report stored before the week filter existed.

    Under that rule `n_acted` counted every manager carrying a filing newer
    than their last holdings report, whenever it was made — not managers who
    filed during the report's own week. Re-reading such a report with the
    current wording would put "this week" on a number that never meant it,
    which is a false statement about a document that is kept forever."""
    acted, total = f.get("n_acted") or 0, f.get("n_managers") or 0
    if acted:
        return (f"{acted} watched manager(s) had a filing newer than their last holdings report. "
                f"This report predates the weekly filter, so that count is not limited to the "
                f"week it covers.")
    if not total:
        return "No managers are on the watchlist."
    if (f.get("n_not_read") or 0) == total:
        return (f"The Named Fund Watch had not finished its first read of EDGAR, so none of the "
                f"{total} watched managers could be described.")
    return "No watched manager had a filing newer than their last holdings report."


def normalize(rep: dict) -> dict:
    """Make an older stored report renderable without changing what it said.

    The card renders `funds.sentence` verbatim, and reports written before
    that field existed have none — so opening one from the history showed no
    activity line at all. The sentence is filled in on READ, never written
    back: a stored document stays exactly the bytes that were stored, which
    is the whole promise of keeping them.

    Which wording is right depends on what `n_acted` meant when the report
    was written, and the version stamp cannot answer that on its own — one
    live revision carries the week-filtered counts under a 1.0.0 stamp,
    because it was built between the filter landing and the version moving.
    The presence of `n_filed_since` is the reliable tell: it arrived with
    the filter."""
    if not isinstance(rep, dict):
        return rep
    f = rep.get("funds")
    if not isinstance(f, dict) or f.get("sentence"):
        return rep
    out = dict(rep)
    out["funds"] = {**f, "sentence": (activity_sentence(f) if "n_filed_since" in f
                                      else legacy_activity_sentence(f)),
                    "sentence_filled_in": True}
    return out


def summary(board: dict, sec: dict, cr: dict, funds_block: dict, press: dict | None) -> dict:
    """Plain sentences. Every one of them is a verdict some other module
    reached, and none of them is stronger than that verdict was."""
    bullets = []
    ex, lev = _q(board, "exposure"), _q(board, "leverage")
    longs, shorts = _q(board, "longs"), _q(board, "shorts")
    if _decided(ex.get("verdict")):
        bullets.append(f"Overall exposure: {str(ex['verdict']).lower()}.")
    if _decided(lev.get("verdict")):
        bullets.append(f"Leverage, read gross: {str(lev['verdict']).lower()}.")
    if _decided(longs.get("verdict")):
        bullets.append(f"The long book: {str(longs['verdict']).lower()}.")
    if _decided(shorts.get("verdict")):
        bullets.append(f"The short book: {str(shorts['verdict']).lower()}.")
    if sec.get("bought"):
        bullets.append("Bought: " + ", ".join(r["sector"] for r in sec["bought"][:3]) + ".")
    if sec.get("sold"):
        bullets.append("Sold: " + ", ".join(r["sector"] for r in sec["sold"][:3]) + ".")
    if cr.get("crowded"):
        bullets.append("Crowded: " + ", ".join(r["market"] for r in cr["crowded"][:4]) + ".")
    if cr.get("decrowding"):
        bullets.append("De-crowding: " + ", ".join(r["market"] for r in cr["decrowding"][:4]) + ".")
    bullets.append(activity_sentence(funds_block))
    for q in ((press or {}).get("quotes") or [])[:2]:
        bullets.append(f"{q['bank']}, via {q['outlet']}: {q['text']}")
    return {"bullets": bullets,
            "text": " ".join(bullets[:4]),
            "note": ("Each line is a verdict reached elsewhere in this report, at the confidence "
                     "stated there. Nothing in this summary is stronger than the section it "
                     "came from.")}


# ── what changed since the stored report before this one ────────────────────

def changes(current: dict, prior: dict | None) -> dict:
    """A diff against last week's stored document.

    Comparing against a record rather than a memory is the whole reason the
    reports are kept. When there is no prior report this returns "not
    comparable", which is a different statement from "nothing changed"."""
    if not prior:
        return {"comparable": False, "since": None, "since_date": None, "changes": [], "n": 0,
                "note": "This is the first stored report, so there is nothing to compare against."}
    rows = []
    pc = {c["key"]: c for c in prior.get("conclusions") or []}
    for c in current.get("conclusions") or []:
        was = (pc.get(c["key"]) or {}).get("verdict")
        if was and was != c["verdict"]:
            rows.append({"what": c["title"], "from": was, "to": c["verdict"], "kind": "VERDICT"})
        wasc = ((pc.get(c["key"]) or {}).get("confidence") or {}).get("level")
        nowc = (c.get("confidence") or {}).get("level")
        if wasc and nowc and wasc != nowc:
            rows.append({"what": f"{c['title']} confidence", "from": wasc, "to": nowc,
                         "kind": "CONFIDENCE"})
    ps = {r["sector"]: r for r in (prior.get("sectors") or {}).get("rows") or []}
    for r in (current.get("sectors") or {}).get("rows") or []:
        was = (ps.get(r["sector"]) or {}).get("verdict")
        if was and was != r.get("verdict"):
            rows.append({"what": r["sector"], "from": was, "to": r.get("verdict"),
                         "kind": "SECTOR"})
    now_cr = {r.get("market") for r in (current.get("crowding") or {}).get("crowded") or []}
    was_cr = {r.get("market") for r in (prior.get("crowding") or {}).get("crowded") or []}
    for m in sorted(now_cr - was_cr):
        rows.append({"what": f"{m} crowding", "from": "not crowded", "to": P.CROWDED,
                     "kind": "CROWDING"})
    for m in sorted(was_cr - now_cr):
        rows.append({"what": f"{m} crowding", "from": P.CROWDED, "to": "no longer crowded",
                     "kind": "CROWDING"})
    # Derived from both reports here rather than reused from the current
    # report's own `added`/`removed`. Those were computed against whatever
    # report preceded this one, so comparing two NON-ADJACENT weeks showed
    # changes from the wrong interval — missing what moved in between and
    # listing what moved outside it.
    now_w = (current.get("watchlist") or {}).get("managers") or {}
    was_w = (prior.get("watchlist") or {}).get("managers") or {}
    for key in sorted(set(now_w) - set(was_w)):
        rows.append({"what": f"Watchlist: {(now_w.get(key) or {}).get('name') or key}",
                     "from": "not watched", "to": "watched", "kind": "WATCHLIST"})
    for key in sorted(set(was_w) - set(now_w)):
        rows.append({"what": f"Watchlist: {(was_w.get(key) or {}).get('name') or key}",
                     "from": "watched", "to": "not watched", "kind": "WATCHLIST"})
    return {"comparable": True, "since": prior.get("week"),
            "since_date": prior.get("as_of"), "since_text": long_date(prior.get("as_of")),
            "changes": rows, "n": len(rows)}


# ── assembly ────────────────────────────────────────────────────────────────

def build(board: dict, funds: dict | None = None, press: dict | None = None,
          prior: dict | None = None, *, week: str | None = None,
          built_at: str | None = None, week_start: str | None = None) -> dict:
    """The whole document. Every figure in it was computed somewhere else."""
    board = board or {}
    funds = funds or {}
    sec, cr = sectors(board), crowding(board)
    funds_block = fund_activity(funds, week_start)
    # The card renders this verbatim rather than re-deriving it from the
    # counts, so the panel and the summary can never state different things
    # about the same week.
    funds_block["sentence"] = activity_sentence(funds_block)
    rep = {
        "version": HF_REPORT_VERSION,
        "pulse_version": board.get("version"),
        "press_version": (press or {}).get("version"),
        "week": week or board.get("week"),
        "built_at": built_at,
        "as_of": board.get("cftc_as_of"),
        "dates": {**(board.get("dates") or {}),
                  "as_of": long_date(board.get("cftc_as_of")),
                  "built_at": long_date(built_at)},
        "conclusions": conclusions(board),
        "trends": trends(board),
        "sectors": sec,
        "crowding": cr,
        "funds": funds_block,
        "new_filings": new_filings(funds, week_start),
        "watchlist": watchlist(funds, prior),
        "press": {"quotes": (press or {}).get("quotes") or [],
                  "captured": (press or {}).get("captured") or [],
                  "banks": (press or {}).get("banks") or [],
                  "n_quotes": (press or {}).get("n_quotes") or 0,
                  "n_captured": (press or {}).get("n_captured") or 0,
                  "class": S.PRIME_BROKER,
                  "note": (press or {}).get("note")},
        "conflicts": conflicts(board, press),
        "sources": board.get("sources") or {},
        "unavailable": board.get("unavailable") or [],
        "evidence_classes": board.get("evidence_classes") or [],
        "limitations": list(LIMITATIONS),
    }
    rep["summary"] = summary(board, sec, cr, funds_block, press)
    rep["changed"] = changes(rep, prior)
    rep["n_conflicts"] = len(rep["conflicts"])
    return rep


# ── comparing any two stored weeks ──────────────────────────────────────────

def compare(a: dict, b: dict) -> dict:
    """Two stored reports side by side, oldest first.

    The card offers this so positioning can be watched evolving rather than
    read one week at a time. It reuses `changes`, so "compare with week N"
    and "what changed this week" can never disagree."""
    if not a or not b:
        return {"ok": False, "error": "two stored reports are required"}
    older, newer = sorted([a, b], key=lambda r: str(r.get("week") or ""))
    side = []
    nc = {c["key"]: c for c in newer.get("conclusions") or []}
    oc = {c["key"]: c for c in older.get("conclusions") or []}
    for key, title in CONCLUSIONS:
        o, n = oc.get(key) or {}, nc.get(key) or {}
        side.append({"key": key, "title": title,
                     "older": {"verdict": o.get("verdict"),
                               "confidence": (o.get("confidence") or {}).get("level"),
                               "streak": (o.get("streak") or {}).get("word")},
                     "newer": {"verdict": n.get("verdict"),
                               "confidence": (n.get("confidence") or {}).get("level"),
                               "streak": (n.get("streak") or {}).get("word")},
                     "same": o.get("verdict") == n.get("verdict")})
    os_ = {r["sector"]: r for r in (older.get("sectors") or {}).get("rows") or []}
    ns_ = {r["sector"]: r for r in (newer.get("sectors") or {}).get("rows") or []}
    sectors_side = [{"sector": s,
                     "older": (os_.get(s) or {}).get("verdict"),
                     "newer": (ns_.get(s) or {}).get("verdict"),
                     "same": (os_.get(s) or {}).get("verdict") == (ns_.get(s) or {}).get("verdict")}
                    for s in sorted(set(os_) | set(ns_))]
    return {"ok": True,
            "older": {"week": older.get("week"), "as_of": older.get("as_of"),
                      "as_of_text": long_date(older.get("as_of"))},
            "newer": {"week": newer.get("week"), "as_of": newer.get("as_of"),
                      "as_of_text": long_date(newer.get("as_of"))},
            "conclusions": side, "sectors": sectors_side,
            "changed": changes(newer, older),
            "n_same": sum(1 for r in side if r["same"]),
            "n_different": sum(1 for r in side if not r["same"])}


def digest(rep: dict) -> dict:
    """The one line the history list shows for a stored week."""
    return {"week": rep.get("week"), "as_of": rep.get("as_of"),
            "as_of_text": long_date(rep.get("as_of")),
            "built_at": rep.get("built_at"),
            "verdicts": {c["key"]: c["verdict"] for c in rep.get("conclusions") or []},
            "crowded": [r.get("market") for r in (rep.get("crowding") or {}).get("crowded") or []],
            "n_changes": (rep.get("changed") or {}).get("n"),
            "n_conflicts": rep.get("n_conflicts"),
            "n_quotes": (rep.get("press") or {}).get("n_quotes"),
            "n_acted": (rep.get("funds") or {}).get("n_acted")}
