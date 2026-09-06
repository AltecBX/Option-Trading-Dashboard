"""US equity market calendar — which days the market actually trades.

Before this module, every "is the market open?" check in the app knew only
one thing: is it a weekday between 9:30 and 4:00. That is wrong about ten
full days a year and about nine half days. On Labor Day the dashboard
believed it was open, started its scanners, and called the broker over and
over for a market that was not trading — and the boards looked live while
showing nothing real.

Two separate mistakes live in that: a CLOSED day treated as open, and an
EARLY CLOSE (1:00 PM) treated as a 4:00 PM day. The second one is quieter
and, for the same-day option boards, more expensive: at 12:30 on the day
after Thanksgiving a 4:00 close says half the session is still ahead when
in fact there are thirty minutes left. Every "how much can still happen"
number downstream inherits that error.

The rules here are computed, not listed, so the calendar does not expire
at the end of a hardcoded year. The one thing that cannot be computed is
an unscheduled closure — a state funeral, a hurricane — so those are kept
as an explicit, dated set in ONE_OFF_CLOSURES.

Pure module: no network, no I/O, no app imports.
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta

__all__ = [
    "SESSION_OPEN", "REGULAR_CLOSE", "EARLY_CLOSE",
    "easter", "holidays", "is_holiday", "is_early_close", "is_session",
    "close_time", "session_span", "session_seconds", "is_open",
    "next_session", "previous_session", "describe",
]

SESSION_OPEN = dtime(9, 30)
REGULAR_CLOSE = dtime(16, 0)
EARLY_CLOSE = dtime(13, 0)

# Juneteenth became a market holiday in 2022. Before that the market traded.
JUNETEENTH_FROM = 2022

# Closures no rule can derive. Dates the NYSE shut outside its own calendar.
ONE_OFF_CLOSURES: frozenset[date] = frozenset({
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11),                       # Reagan, national day of mourning
    date(2007, 1, 2),                        # Ford, national day of mourning
    date(2012, 10, 29), date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),                       # George H. W. Bush
    date(2025, 1, 9),                        # Carter
})


# ── the moving pieces ───────────────────────────────────────────────────────

def easter(year: int) -> date:
    """Easter Sunday (Gregorian). Good Friday is two days earlier, and it is
    the only market holiday with no fixed date and no weekday rule."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lo = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lo) // 451
    month, day = divmod(h + lo - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth given weekday of a month (weekday: Monday=0)."""
    d = date(year, month, 1)
    shift = (weekday - d.weekday()) % 7
    return d + timedelta(days=shift + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last given weekday of a month.

    Walks back from the month's final day, not from the 28th — a month can
    run to the 31st, and starting at the 28th silently returns the
    second-to-last Monday in a year like 2027, where Memorial Day is
    May 31 and not May 24."""
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date | None:
    """Where a fixed-date holiday actually lands.

    Saturday moves back to Friday, Sunday forward to Monday. New Year's Day
    is the exception the rest of the world gets wrong: when January 1 falls
    on a Saturday the NYSE does NOT close the Friday before — it simply
    trades December 31 and takes no day. That returns None."""
    if d.weekday() == 5:                       # Saturday
        if d.month == 1 and d.day == 1:
            return None
        return d - timedelta(days=1)
    if d.weekday() == 6:                       # Sunday
        return d + timedelta(days=1)
    return d


# ── the calendar ────────────────────────────────────────────────────────────

def holidays(year: int) -> dict[date, str]:
    """Every full-day market closure in a year, as {date: name}.

    Includes the observed date, not the nominal one — July 4 on a Saturday
    closes the market on Friday July 3."""
    out: dict[date, str] = {}

    def put(d: date | None, name: str) -> None:
        if d is not None and d.year == year:
            out[d] = name

    # A New Year's Day that falls on a Sunday is observed on January 2 of the
    # SAME year; one that falls on a Saturday is not observed at all. The
    # following January 1 can also pull a holiday back into December.
    put(_observed(date(year, 1, 1)), "New Year's Day")
    put(_observed(date(year + 1, 1, 1)), "New Year's Day")
    put(_nth_weekday(year, 1, 0, 3), "Martin Luther King Jr. Day")
    put(_nth_weekday(year, 2, 0, 3), "Washington's Birthday")
    put(easter(year) - timedelta(days=2), "Good Friday")
    put(_last_weekday(year, 5, 0), "Memorial Day")
    if year >= JUNETEENTH_FROM:
        put(_observed(date(year, 6, 19)), "Juneteenth National Independence Day")
    put(_observed(date(year, 7, 4)), "Independence Day")
    put(_nth_weekday(year, 9, 0, 1), "Labor Day")
    put(_nth_weekday(year, 11, 3, 4), "Thanksgiving Day")
    put(_observed(date(year, 12, 25)), "Christmas Day")

    for d in ONE_OFF_CLOSURES:
        if d.year == year:
            out[d] = "Unscheduled closure"
    return out


def _as_date(d) -> date:
    return d.date() if isinstance(d, datetime) else d


def is_holiday(d) -> bool:
    """True if the market is closed all day for a holiday. Weekends are not
    holidays — they are simply not sessions; use is_session for that."""
    d = _as_date(d)
    return d in holidays(d.year)


def is_early_close(d) -> bool:
    """True on a 1:00 PM day: the eve of Independence Day when both it and
    the Fourth are weekdays, the Friday after Thanksgiving, and Christmas
    Eve when it is a session in its own right."""
    d = _as_date(d)
    if not is_session(d):
        return False
    if d.month == 7 and d.day == 3 and date(d.year, 7, 4).weekday() < 5:
        return True
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True
    return d.month == 12 and d.day == 24


def is_session(d) -> bool:
    """True if the market trades at all that day — regular or shortened."""
    d = _as_date(d)
    return d.weekday() < 5 and not is_holiday(d)


def close_time(d) -> dtime:
    """The bell for that date: 1:00 PM on a half day, otherwise 4:00 PM."""
    return EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE


def session_span(d) -> tuple[dtime, dtime] | None:
    """(open, close) for a trading day, or None if the market is shut."""
    d = _as_date(d)
    return (SESSION_OPEN, close_time(d)) if is_session(d) else None


def session_seconds(d) -> float:
    """How long that session runs, in seconds. 0 when the market is shut.
    23,400 on a normal day; 12,600 on a half day."""
    span = session_span(d)
    if span is None:
        return 0.0
    o, c = span
    return (c.hour - o.hour) * 3600.0 + (c.minute - o.minute) * 60.0


def is_open(now: datetime, open_time: dtime | None = None,
            close_pad_minutes: float = 0.0) -> bool:
    """Is the market trading at this exact moment?

    `open_time` and `close_pad_minutes` let a caller keep its own window —
    the option collector starts five minutes early and runs five minutes
    past the bell — without re-deriving the calendar."""
    d = now.date()
    if not is_session(d):
        return False
    end = datetime.combine(d, close_time(d)) + timedelta(minutes=close_pad_minutes)
    return (open_time or SESSION_OPEN) <= now.time() < end.time()


def next_session(d) -> date:
    """The next day the market trades, not counting the day given."""
    d = _as_date(d) + timedelta(days=1)
    while not is_session(d):
        d += timedelta(days=1)
    return d


def previous_session(d) -> date:
    """The last day the market traded before the day given."""
    d = _as_date(d) - timedelta(days=1)
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def describe(d) -> str:
    """One plain sentence about a date, for a tooltip or a board's status
    line. Says the holiday by name so nobody has to guess why it is shut."""
    d = _as_date(d)
    if d.weekday() >= 5:
        return "The market is closed — it is the weekend."
    name = holidays(d.year).get(d)
    if name:
        return f"The market is closed — {name}."
    if is_early_close(d):
        return "Short session — the market closes early, at 1:00 PM Eastern."
    return "Regular session — the market closes at 4:00 PM Eastern."
