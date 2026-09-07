"""Hedge Fund Intelligence — what is worth waking someone for (Phase 6).

Everything before this phase put facts on a page you had to go and look at.
This decides which of those facts should reach a phone instead.

That is a different problem from displaying them, and a harder one: a board
that pushes too often is a board whose notifications get muted, at which
point it is worth less than the page it came from. So the rules here are
mostly about NOT sending:

  * **A change, never a state.** "Financials is crowded" is true for weeks
    at a time and is not news on any of them. "Financials just became
    crowded" is news exactly once. Every alert here is the edge of a change,
    compared against the state at the last alert rather than against last
    week's board — otherwise a rebuild inside one week fires twice, and a
    week the board never ran hides the change entirely.

  * **Never twice.** Every alert carries a key derived from the event, not
    from the moment it was noticed. A 13D filed on the 3rd is the same event
    whether it is seen on the 4th or the 6th.

  * **The first run primes and sends nothing.** Switching alerts on would
    otherwise deliver every open state at once — a dozen pushes describing
    a board that has been sitting there quietly for weeks. The first pass
    records what it found and stays silent, so the first thing that ever
    arrives is a genuine change.

  * **A quiet week sends nothing, and that is success.** Most weeks nothing
    here should fire. Silence is the ordinary output and is never treated
    as a failure to report.

  * **An anonymous class still cannot name a fund.** Crowding comes from
    CFTC futures, which say nothing about who is in them. A crowding alert
    that named a manager would invent the one fact the whole board refuses
    to invent, so the rule is enforced here too rather than assumed.

  * **Both dates, always.** A 13D found today may have been filed three days
    ago and describe a position taken a week before that. A push that reads
    as though it just happened would be a lie of tense.

It is pure: dictionaries in, a dictionary out. No network, no files, no
clock, no sending — the caller supplies the state and does the delivery.
"""

from __future__ import annotations

from datetime import date

import hf_sources as S

HF_ALERT_VERSION = "1.0.0"

# The kinds, and what each is worth waking someone for.
ACTIVIST = "ACTIVIST FILING"
CROWDING = "CROWDING CHANGE"
FILING = "NEW FILING"
REPORT = "REPORT READY"

# 1 is "worth a buzz", 0 is "worth seeing next time you look".
PRIORITY = {ACTIVIST: 1, CROWDING: 1, FILING: 0, REPORT: 0}

# A SCHEDULE 13D means an activist has crossed 5% with intent, and it carries
# a five-business-day filing clock. 13G is the passive cousin and is not an
# event; the fund cards already refuse to treat it as activity.
ACTIVIST_FORMS = ("SC 13D", "SC 13D/A")

DEFAULT_KINDS = (ACTIVIST, CROWDING, FILING, REPORT)
CAP = 6                 # pushes per run, however much is waiting
CROWDED = "CROWDED"


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def long_date(s) -> str:
    """Month Day, Year — never an ISO string, on a phone least of all."""
    d = _d(s)
    return d.strftime("%B %-d, %Y") if d else "an unstated date"


def _days_ago(s, today: date | None) -> int | None:
    d = _d(s)
    return (today - d).days if (d and today) else None


# ── what the board is currently saying ──────────────────────────────────────

def crowding_state(board: dict | None) -> dict:
    """{market key: state} for every market the board read.

    Kept as its own function because it is stored between runs: the
    comparison that decides a crowding alert is against the state at the
    last alert, not against the previous week."""
    rows = ((board or {}).get("crowding") or {}).get("markets") or []
    return {r.get("key"): r.get("state") for r in rows if r.get("key")}


# ── the candidates ──────────────────────────────────────────────────────────

def activist_filings(funds: dict | None, today: date | None = None) -> list[dict]:
    """Every SCHEDULE 13D by a watched manager. Attributable, so it may name
    the fund — it comes from a filing that fund signed."""
    out = []
    for row in (funds or {}).get("new_filings") or []:
        form = str(row.get("form") or "").upper()
        if not any(form.startswith(f) for f in ACTIVIST_FORMS):
            continue
        out.append({
            "kind": ACTIVIST, "key": f"13d:{row.get('accession') or ''}:{row.get('key') or ''}",
            "fund": row.get("manager"), "form": row.get("form"),
            "issuer": row.get("company"), "public_on": row.get("filed"),
            "days_ago": _days_ago(row.get("filed"), today),
            "class": S.VERIFIED, "sub": "FILING",
        })
    return out


def crowding_changes(board: dict | None, last: dict | None) -> list[dict]:
    """Markets that entered or left the crowded state since the last alert.

    Anonymous by construction: this reads CFTC futures, which say who is
    positioned but never which fund. No row here carries a fund."""
    now = crowding_state(board)
    last = last or {}
    rows = {r.get("key"): r for r in (((board or {}).get("crowding") or {}).get("markets") or [])}
    out = []
    for key, state in now.items():
        was = last.get(key)
        if was is None or was == state:
            continue
        if CROWDED not in (state, was):
            # NORMAL <-> DECROWDING is a wobble, not an event.
            continue
        m = rows.get(key) or {}
        out.append({
            "kind": CROWDING, "key": f"crowd:{key}:{was}>{state}:{m.get('as_of') or ''}",
            "market": m.get("market") or key, "state": state, "was": was,
            "side": m.get("side"), "as_of": m.get("as_of"),
            "entered": state == CROWDED,
            "class": S.REGULATORY,
        })
    return out


def new_filings(funds: dict | None, today: date | None = None) -> list[dict]:
    """A watched manager filing new holdings. Not the 13Ds — those are their
    own, louder kind — and never a 13G, which is a passive notice."""
    out = []
    for row in (funds or {}).get("new_filings") or []:
        form = str(row.get("form") or "").upper()
        if any(form.startswith(f) for f in ACTIVIST_FORMS) or form.startswith("SC 13G"):
            continue
        out.append({
            "kind": FILING, "key": f"filing:{row.get('accession') or ''}:{row.get('key') or ''}",
            "fund": row.get("manager"), "form": row.get("form"),
            "public_on": row.get("filed"),
            "days_ago": _days_ago(row.get("filed"), today),
            "class": S.VERIFIED, "sub": "FILING",
        })
    return out


def week_start(week) -> date | None:
    """The Monday an ISO week opened.

    The report is stamped with the week it COVERS, and built a day or two
    after that week closed. Saying "the week of" and then printing the build
    date would name a week the report is not about."""
    try:
        y, w = str(week).split("-W")
        return date.fromisocalendar(int(y), int(w), 1)
    except (ValueError, AttributeError):
        return None


def report_ready(report: dict | None) -> list[dict]:
    week = (report or {}).get("week")
    if not week:
        return []
    start = week_start(week)
    return [{"kind": REPORT, "key": f"report:{week}", "week": week,
             "week_start": start.isoformat() if start else None,
             "built_at": report.get("built_at"), "class": S.INFERENCE}]


def candidates(board=None, funds=None, report=None, last_crowding=None,
               today: date | None = None) -> list[dict]:
    """Everything that happened, before any decision about sending."""
    rows = (activist_filings(funds, today) + crowding_changes(board, last_crowding)
            + new_filings(funds, today) + report_ready(report))
    for r in rows:
        r["priority"] = PRIORITY.get(r["kind"], 0)
    rows.sort(key=lambda r: (-r["priority"], r["kind"], r["key"]))
    return rows


# ── the decision ────────────────────────────────────────────────────────────

def attribution_ok(rows) -> bool:
    """No anonymous alert may name a fund. The same rule the evidence layer
    keeps, enforced again here because a push is the one place a reader
    cannot click through to check."""
    return S.attribution_ok([{"class": r.get("class"), **({"fund": r["fund"]} if "fund" in r else {})}
                             for r in rows or []])


def select(rows, sent_keys=None, *, primed: bool = True, cap: int = CAP,
           kinds=DEFAULT_KINDS) -> dict:
    """What to actually send, and why the rest was held back."""
    sent_keys = set(sent_keys or ())
    kinds = tuple(kinds)
    send, held = [], []
    for r in rows or []:
        if r["kind"] not in kinds:
            held.append({**r, "held": "this kind is switched off"})
        elif r["key"] in sent_keys:
            held.append({**r, "held": "already sent"})
        elif not primed:
            held.append({**r, "held": "the first run records what it finds and sends nothing"})
        else:
            send.append(r)
    over = send[cap:]
    send = send[:cap]
    for r in over:
        held.append({**r, "held": f"more than {cap} at once — it will go out on the next run"})
    return {"send": send, "held": held, "n_send": len(send), "n_held": len(held),
            "primed": primed, "cap": cap,
            # Everything seen is remembered whether or not it was sent. A
            # held alert must not arrive later as though it were new — the
            # first-run prime depends entirely on this.
            "remember": [r["key"] for r in (rows or [])]}


# ── the words ───────────────────────────────────────────────────────────────

def render(row: dict) -> dict:
    """The push itself: a title, a sentence, and a priority.

    Written to be read on a lock screen by someone who is not going to open
    the app to find out what it meant."""
    kind = row.get("kind")
    p = row.get("priority", PRIORITY.get(kind, 0))
    if kind == ACTIVIST:
        when = long_date(row.get("public_on"))
        ago = row.get("days_ago")
        age = (" — today" if ago == 0
               else " — yesterday" if ago == 1
               else f" — {ago} days ago" if isinstance(ago, int) and ago > 1
               else "")
        who = row.get("fund") or "A watched manager"
        what = row.get("issuer") or "a company it has not named here"
        return {"title": "Activist filing",
                "message": (f"{who} filed a {row.get('form') or 'SCHEDULE 13D'} on {what}. "
                            f"Filed {when}{age}. A 13D means a stake of more than 5% with "
                            "intent, and it had to be filed within five business days."),
                "priority": p}
    if kind == CROWDING:
        m = row.get("market") or "A market"
        side = row.get("side")
        leg = f" on the {side} side" if side in ("long", "short") else ""
        if row.get("entered"):
            msg = (f"{m} just became crowded{leg}, as of {long_date(row.get('as_of'))}. "
                   "That means the futures book is both unusually one-sided and unusually "
                   "large for its own history. It is not a forecast.")
        else:
            msg = (f"{m} is no longer crowded, as of {long_date(row.get('as_of'))}. "
                   f"It moved from {row.get('was')} to {row.get('state')}.")
        return {"title": "Crowding changed", "message": msg, "priority": p}
    if kind == FILING:
        when = long_date(row.get("public_on"))
        who = row.get("fund") or "A watched manager"
        return {"title": "New filing",
                "message": (f"{who} filed a {row.get('form') or 'report'}, public {when}. "
                            "A quarterly report describes one day and arrives about 45 days "
                            "later, so this is news about the past."),
                "priority": p}
    if kind == REPORT:
        return {"title": "Weekly report ready",
                "message": (f"The hedge fund report for the week of "
                            f"{long_date(row.get('week_start') or row.get('built_at'))} is "
                            "ready to read."),
                "priority": p}
    return {"title": "Hedge funds", "message": str(row.get("key") or ""), "priority": p}


def build(board=None, funds=None, report=None, *, last_crowding=None, sent_keys=None,
          primed: bool = True, cap: int = CAP, kinds=DEFAULT_KINDS,
          today: date | None = None, as_of: str | None = None) -> dict:
    """Everything the caller needs: what to send, what was held, and the new
    crowding state to remember."""
    rows = candidates(board, funds, report, last_crowding, today)
    picked = select(rows, sent_keys, primed=primed, cap=cap, kinds=kinds)
    for r in picked["send"]:
        r["rendered"] = render(r)
    return {
        "version": HF_ALERT_VERSION, "as_of": as_of,
        **picked,
        "n_candidates": len(rows),
        "crowding_state": crowding_state(board),
        "attribution_ok": attribution_ok(rows),
        "note": ("Alerts fire on a CHANGE, never on a state — a market that is crowded every "
                 "week is not news on any of them. Nothing is ever sent twice, the first run "
                 "records what it finds and stays silent so switching alerts on does not "
                 "deliver a backlog, and a week with nothing to say sends nothing at all."),
    }
