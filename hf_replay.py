"""Hedge Fund Intelligence — replaying the four weekly verdicts (Phase 5).

`hf_grade.py` can grade three years of crowding because crowding is computed
from the CFTC series and nothing else: truncate that series at week W and you
have exactly what the board would have said in week W. The four weekly
verdicts were left ungraded because they also read short interest, ETF flows
and the short-volume share, none of which the board kept historically.

This module reconstructs them. It is the same idea, applied to inputs that
arrive on four different clocks.

Three rules hold it honest:

  * **The real functions, never a copy.** A replayed week calls
    `hf_pulse.exposure`, `hf_pulse.leverage` and `hf_pulse.longs_and_shorts`
    — the very functions the live board calls. A reimplementation here would
    drift from the board the first time either changed, and would then be
    grading something the board never said.

  * **Input-complete or not at all.** A verdict is reconstructed for week W
    only when every input that decides it TODAY is available for W. This is
    the rule that matters most. `shorts` is built from futures, short
    interest and the short-volume share; rebuilt from futures alone it is a
    different answer wearing the same name, and grading it would tell you
    nothing about the answers the board actually publishes. A question with
    a missing input is skipped for that week, with the reason recorded, and
    the panel says how deep each question got and why it stopped.

    `REQUIRES` below is not a guess. Removing each optional input and
    counting what the verdict was built from gives: exposure 5 inputs -> 4,
    leverage 3 -> 3, longs 3 -> 3, shorts 5 -> 3. So leverage and longs are
    decided by the futures alone and replay to the full depth of the CFTC
    series with no other source at all; exposure needs the ETF flows; shorts
    needs both FINRA readings.

  * **Point in time, on each input's own clock.** The CFTC report describes
    Tuesday of week W and is published that Friday, so a row dated in week W
    was in hand before W closed. Short interest is published about eight
    business days after its settlement date, so week W sees the newest
    reading whose publication date has passed — not the newest settlement.
    Daily files are public the evening of their session.

It is pure: dictionaries in, a dictionary out. No network, no files, no
clock. The caller supplies the weeks and every series, so a replay is
reproducible forever.
"""

from __future__ import annotations

from datetime import date, timedelta

import hf_pulse as P

HF_REPLAY_VERSION = "1.0.0"

# The non-futures inputs each question needs before its replay is the same
# reading the board publishes. Measured, not assumed — see the module
# docstring. An empty tuple means the futures decide it alone.
REQUIRES: dict[str, tuple[str, ...]] = {
    "exposure": ("etf_flows",),
    "leverage": (),
    "longs": (),
    "shorts": ("short_interest", "short_volume"),
}

QUESTIONS = ("exposure", "leverage", "longs", "shorts")

# Every verdict takes its streak and its flat band from the S&P contract. A
# week whose truncated book has lost that market would still produce a
# verdict, but not the one the board would have reached, so it is skipped
# like any other missing input.
ANCHOR = "sp500"

# How many sessions the live gatherers read. `gather_etf_flows` sums the
# creations reported over the last five sessions; `gather_short_volume`
# builds its history from the last twenty. A replay that used a different
# window would compute a different band and a different percentile.
FLOW_SESSIONS = 5
SHVOL_DAYS = 20


# ── weeks ───────────────────────────────────────────────────────────────────

def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_end(week: str) -> date | None:
    """The Sunday that closes an ISO week — the moment a fact had to be
    public by to have been in hand that week."""
    try:
        y, w = str(week).split("-W")
        return date.fromisocalendar(int(y), int(w), 7)
    except (ValueError, AttributeError):
        return None


def _as_date(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


# ── truncating each input to what week W could see ──────────────────────────

def truncate_cftc(cftc: dict, week: str) -> dict:
    """Every market cut to the rows dated on or before week W closes.

    Unlike the crowding backfill, which walks one market at a time, the
    verdicts read four markets in a single answer, so they must all be cut
    to the same week or the answer mixes two moments.

    `as_of` moves with the window. Leaving the market's original `as_of` in
    place is the bug that put 204 reconstructed crowding rows in one week,
    and it would put every replayed verdict in the present."""
    end = week_end(week)
    if not end or not cftc:
        return {}
    out = {}
    for key, m in cftc.items():
        rows = [r for r in (m.get("series") or []) if (_as_date(r.get("date")) or date.max) <= end]
        if not rows:
            continue
        out[key] = {**m, "series": rows, "as_of": rows[0].get("date")}
    return out


def short_interest_for(settlements: list[dict] | None, week: str) -> dict | None:
    """The short-interest reading in hand during week W.

    Keyed on `public_on`, never on the settlement date. A settlement dated
    inside week W is not public for another eight business days, so using it
    would hand the replay a number nobody could have read — the exact leak
    the whole point-in-time exercise exists to prevent."""
    end = week_end(week)
    if not end or not settlements:
        return None
    seen = [s for s in settlements
            if (_as_date(s.get("public_on")) or date.max) <= end]
    if not seen:
        return None
    return max(seen, key=lambda s: (_as_date(s.get("public_on")) or date.min,
                                    str(s.get("settlement") or "")))


def short_volume_for(daily: dict | None, week: str, days: int = SHVOL_DAYS) -> dict | None:
    """The short-volume history as it stood at the end of week W.

    `daily` is {session date: market short share}, one number per file. The
    live gatherer walks back from today over the last twenty sessions; this
    walks back from the last session of week W over the same twenty, so the
    band and the percentile are computed from the same shape.

    A short window is refused rather than used. The flat band this input
    contributes is drawn from the spread of its own history, so three
    sessions and twenty sessions do not merely differ in confidence — they
    can hand `build_verdict` a different band and so a different answer.
    Early weeks of a still-deepening cache are skipped for that reason, and
    the coverage report says so."""
    end = week_end(week)
    if not end or not daily:
        return None
    sessions = sorted((d for d in daily if (_as_date(d) or date.max) <= end), reverse=True)
    window = sessions[:days]
    if len(window) < days:
        return None
    shares = [daily[d] for d in window]
    if any(s is None for s in shares):
        return None
    change = (shares[0] - shares[1]) if len(shares) > 1 else None
    return {"share": shares[0], "change": change, "session": window[0],
            "n_days": len(shares), "history": shares,
            "percentile": P.percentile(shares, shares[0])}


def etf_flows_for(daily: dict | None, week: str, sessions: int = FLOW_SESSIONS) -> dict | None:
    """Creations minus redemptions over the five sessions ending in week W.

    `daily` is {session date: net creations across the tracked ETFs}, which
    is what the live gatherer sums out of each ETF's in-and-out flow rows.
    Only `net_all` and `as_of` reach the verdict, so only those are built.
    A short window is refused for the same reason as the short-volume one:
    a total summed over three sessions is not the five-session total the
    board adds up."""
    end = week_end(week)
    if not end or not daily:
        return None
    days = sorted((d for d in daily if (_as_date(d) or date.max) <= end), reverse=True)
    window = days[:sessions]
    if len(window) < sessions:
        return None
    return {"net_all": sum(float(daily[d] or 0) for d in window),
            "as_of": window[0], "n_days": len(window)}


# ── one week ────────────────────────────────────────────────────────────────

def replay_week(week: str, cftc: dict, *, settlements=None, short_volume_daily=None,
                etf_flow_daily=None) -> dict:
    """The four verdicts as the board would have published them in week W,
    and for each one it could not reach, the input that was missing."""
    book = truncate_cftc(cftc, week)
    verdicts: dict[str, str] = {}
    skipped: dict[str, str] = {}
    if ANCHOR not in book:
        return {"week": week, "verdicts": {}, "n_inputs": {},
                "skipped": {q: "no S&P 500 futures history that far back" for q in QUESTIONS}}

    have = {
        "short_interest": short_interest_for(settlements, week),
        "short_volume": short_volume_for(short_volume_daily, week),
        "etf_flows": etf_flows_for(etf_flow_daily, week),
    }
    missing = {q: [k for k in REQUIRES[q] if not have.get(k)] for q in QUESTIONS}

    built: dict[str, dict] = {}
    # The real functions. Anything reimplemented here would be grading a
    # board that does not exist.
    if not missing["exposure"]:
        built["exposure"] = P.exposure(book, have["etf_flows"])
    if not missing["leverage"]:
        built["leverage"] = P.leverage(book, None)
    if not missing["longs"] or not missing["shorts"]:
        # One call answers both legs, so it runs when EITHER is reachable and
        # only the reachable one is kept. `longs` never reads the two FINRA
        # arguments, which is why it survives a week that has neither.
        longs, shorts = P.longs_and_shorts(book, have["short_interest"], have["short_volume"])
        if not missing["longs"]:
            built["longs"] = longs
        if not missing["shorts"]:
            built["shorts"] = shorts

    for q in QUESTIONS:
        if missing[q]:
            skipped[q] = "no " + " or ".join(_english(k) for k in missing[q]) + " for this week"
        elif q in built and built[q].get("verdict"):
            verdicts[q] = built[q]["verdict"]
        else:
            skipped[q] = "the inputs were present but produced no answer"
    return {"week": week, "verdicts": verdicts, "skipped": skipped,
            "n_inputs": {q: len(built[q].get("inputs") or []) for q in built},
            "as_of": (book.get(ANCHOR) or {}).get("as_of")}


def _english(key: str) -> str:
    return {"short_interest": "short interest",
            "short_volume": "short-volume share",
            "etf_flows": "ETF creations"}.get(key, key)


# ── the whole record ────────────────────────────────────────────────────────

def weeks_in(cftc: dict, anchor: str = ANCHOR) -> list[str]:
    """Every week the anchor contract reported, oldest first.

    Driven by the anchor rather than by a calendar range: a week the CFTC
    never published is not a week the board could have had an answer for."""
    rows = ((cftc or {}).get(anchor) or {}).get("series") or []
    seen = {week_key(d) for d in (_as_date(r.get("date")) for r in rows) if d}
    return sorted(seen)


def replay(cftc: dict, *, settlements=None, short_volume_daily=None, etf_flow_daily=None,
           min_history: int = 12) -> list[dict]:
    """Every week the record can reconstruct, oldest first.

    `min_history` is the shortest truncated series worth reading: the
    streaks, bands and percentiles inside a verdict are computed from the
    window itself, so the first few weeks of any series would be answered
    from almost no history."""
    weeks = weeks_in(cftc)
    if len(weeks) <= min_history:
        return []
    return [replay_week(w, cftc, settlements=settlements,
                        short_volume_daily=short_volume_daily,
                        etf_flow_daily=etf_flow_daily)
            for w in weeks[min_history:]]


def readings(rows: list[dict] | None) -> list[dict]:
    """The rows `hf_grade.grade_verdicts` already consumes, so the grader
    needs no new shape and no new code path: the replayed record and the
    weeks stored since the board began keeping them are the same thing."""
    return [{"week": r.get("week"), "verdicts": r.get("verdicts") or {}}
            for r in (rows or []) if (r.get("verdicts") or {})]


def coverage(rows: list[dict] | None) -> dict:
    """How deep each question got, and what stopped it.

    Reported per question rather than as one number, because the four do not
    share a floor: leverage and longs reach the whole futures series while
    shorts can only reach as far back as FINRA was read."""
    rows = rows or []
    out = {}
    for q in QUESTIONS:
        done = [r for r in rows if (r.get("verdicts") or {}).get(q)]
        reasons: dict[str, int] = {}
        for r in rows:
            why = (r.get("skipped") or {}).get(q)
            if why:
                reasons[why] = reasons.get(why, 0) + 1
        out[q] = {"n_weeks": len(done),
                  "first": done[0]["week"] if done else None,
                  "last": done[-1]["week"] if done else None,
                  "n_skipped": len(rows) - len(done),
                  "why_skipped": [{"reason": k, "weeks": v}
                                  for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])]}
    return {"by_question": out, "n_weeks_walked": len(rows),
            "note": ("A question is replayed for a week only when every input that decides it "
                     "today was public that week. Rebuilt from fewer inputs it would be a "
                     "different answer wearing the same name.")}


def build(cftc: dict, *, settlements=None, short_volume_daily=None, etf_flow_daily=None,
          min_history: int = 12, as_of: str | None = None, source: str | None = None) -> dict:
    """The replayed record, ready to store and to hand to the grader."""
    rows = replay(cftc, settlements=settlements, short_volume_daily=short_volume_daily,
                  etf_flow_daily=etf_flow_daily, min_history=min_history)
    cov = coverage(rows)
    return {"version": HF_REPLAY_VERSION, "as_of": as_of, "source": source,
            "weeks": rows, "readings": readings(rows), "coverage": cov,
            "n_weeks": len(rows), "n_readings": len(readings(rows)),
            "min_history": min_history,
            "limitations": [
                "These are the board's own answers recomputed from the data as it stood in "
                "each past week, not answers it published at the time. The board began "
                "keeping its weekly record in September 2026.",
                "A verdict is recomputed only when every input that decides it today was "
                "public that week, so the four questions do not reach equally far back. The "
                "depth of each is shown beside it.",
                "The recomputation calls the same functions the live board calls. If that "
                "reasoning changes, this record changes with it — it is a replay, not an "
                "archive of what was once said.",
            ]}
