"""strat_states.py — candle-state classification and multi-timeframe breadth.

Pure functions only: no network, no disk, no clock. `watchlist_table.py` feeds
it the daily bars it already downloads, `market_state.py` merges the result
with a live quote and owns the caching, and the Sectors / Market Context tabs
render what comes out.

WHAT A STATE IS. Every bar is classified against the bar before it, on the
same timeframe, using nothing but the two highs and the two lows:

    1     inside      — this bar's range sits entirely inside the prior bar's.
                        Neither extreme was taken out. A coiled bar.
    2U    directional — the prior HIGH was exceeded and the prior LOW held.
    2D    directional — the prior LOW was broken and the prior HIGH held.
    3     outside     — BOTH the prior high and the prior low were taken out.

That is the whole vocabulary. It is deliberately a description of what
happened, not a prediction: this module never says a 2U is bullish, because
whether it is depends on the timeframe above it and this module cannot see
context it was not given.

THE EQUALITY RULE, STATED ONCE. Taking out an extreme means EXCEEDING it.
A bar whose high exactly equals the prior high has not taken it out, so it
counts as held. Every comparison below is therefore strict (`>` and `<`), and
a bar that exactly matches the prior bar on both ends is an inside bar. The
alternative convention (touching counts) makes 3s appear on every flat,
thin-volume day and is the reason this rule is written down rather than left
to whichever comparison got typed first.

CALENDAR PERIODS, NOT ROLLING WINDOWS. Weekly / monthly / quarterly / yearly
bars are built by grouping DAILY bars into calendar buckets — the week a date
falls in, the month, the quarter, the year — and taking the high and low of
each bucket. A "rolling 5-day high" is a different measurement that happens to
have the same units, and mixing the two produces a weekly state that changes
every single day, which is not what a weekly candle does.

THE CURRENT PERIOD IS ALWAYS IN PROGRESS. On any timeframe above daily, the
newest bucket is unfinished — a monthly bar on the 3rd of the month is three
days old. Its state is real and it is what a trader reads, but it can still
change before the month closes. Callers get `complete: False` on it so nothing
downstream can quietly treat a three-day-old monthly bar as settled.
"""

from __future__ import annotations

from datetime import date

STRAT_VERSION = "strat-states-1.0.0"

# Order matters: it is the stacking order of every breadth bar in the UI, and
# the legend is generated from it, so the two can never disagree.
STATES = ("1", "2U", "2D", "3")

STATE_LABEL = {
    "1": "Inside",
    "2U": "Directional up",
    "2D": "Directional down",
    "3": "Outside",
}

STATE_MEANING = {
    "1": ("Inside bar — the whole range sits inside the prior bar's range. "
          "Neither the prior high nor the prior low was taken out."),
    "2U": ("Directional up — the prior high was exceeded while the prior low "
           "held."),
    "2D": ("Directional down — the prior low was broken while the prior high "
           "held."),
    "3": ("Outside bar — both the prior high and the prior low were taken "
          "out in the same bar."),
}

# Timeframe keys, coarsest last. Everything that iterates timeframes iterates
# this, so adding one is a single-line change and the UI order follows.
TIMEFRAMES = ("D", "W", "M", "Q", "Y")

TIMEFRAME_LABEL = {
    "D": "Daily", "W": "Weekly", "M": "Monthly",
    "Q": "Quarterly", "Y": "Yearly",
}


# ══════════════════════════════════════════════════════════════════════════
# CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════

def state_of(prev_high, prev_low, cur_high, cur_low) -> str | None:
    """Classify one bar against the bar before it. Returns "1", "2U", "2D",
    "3", or None when any of the four numbers is missing or unusable.

    None is a first-class answer here, not a failure: the first bar of a
    symbol's history has nothing before it, and a symbol that listed this
    month has no prior monthly bar. Returning None keeps those out of the
    breadth tallies instead of inventing a state for them.
    """
    try:
        ph, pl = float(prev_high), float(prev_low)
        ch, cl = float(cur_high), float(cur_low)
    except (TypeError, ValueError):
        return None
    # A bar whose low is above its high is corrupt data, not an exotic state.
    if ph < pl or ch < cl:
        return None
    up = ch > ph                      # prior high EXCEEDED (see equality rule)
    down = cl < pl                    # prior low BROKEN
    if up and down:
        return "3"
    if up:
        return "2U"
    if down:
        return "2D"
    return "1"


def is_directional(state: str | None) -> bool:
    return state in ("2U", "2D")


# ══════════════════════════════════════════════════════════════════════════
# CALENDAR BUCKETING
# ══════════════════════════════════════════════════════════════════════════

def _as_date(d) -> date | None:
    """Accept a date, a datetime, or an ISO-ish string. Anything else is
    None — a bar with an unreadable date cannot be bucketed and silently
    dropping it beats guessing which week it belonged to."""
    if isinstance(d, date):
        return d
    if hasattr(d, "date") and callable(getattr(d, "date")):
        try:
            return d.date()
        except Exception:  # noqa: BLE001
            return None
    try:
        s = str(d)[:10]
        y, m, dd = s.split("-")
        return date(int(y), int(m), int(dd))
    except Exception:  # noqa: BLE001
        return None


def period_key(d, timeframe: str) -> str | None:
    """The calendar bucket a date belongs to, as a sortable string.

        D  "2026-08-21"        the session itself
        W  "2026-W34"          ISO week, Monday-start
        M  "2026-08"
        Q  "2026-Q3"
        Y  "2026"

    ISO weeks are used for W because they are the only week numbering that is
    unambiguous across a year boundary: the week containing January 1st can
    belong to the previous ISO year, and a home-rolled (year, week_of_year)
    pair splits that week into two buckets, producing a phantom one-day weekly
    bar every January.
    """
    dt = _as_date(d)
    if dt is None:
        return None
    tf = (timeframe or "").upper()
    if tf == "D":
        return dt.isoformat()
    if tf == "W":
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    if tf == "M":
        return f"{dt.year:04d}-{dt.month:02d}"
    if tf == "Q":
        return f"{dt.year:04d}-Q{(dt.month - 1) // 3 + 1}"
    if tf == "Y":
        return f"{dt.year:04d}"
    return None


def aggregate(dates, highs, lows, timeframe: str, limit: int | None = None) -> list[dict]:
    """Collapse daily bars into calendar bars for one timeframe, oldest first.

    Each entry is {key, start, end, high, low, days} where `days` is how many
    daily bars went into it — the honest measure of how far into an unfinished
    period we are, and the only thing that tells a one-day-old monthly bar
    apart from a closed one.

    `limit` keeps only the newest N buckets. Everything downstream needs two
    (the current one and the one before it), so the scan stores 3 and nothing
    carries five years of monthly bars around for no reason.
    """
    n = min(len(dates or []), len(highs or []), len(lows or []))
    order: list[str] = []
    buckets: dict[str, dict] = {}
    for i in range(n):
        key = period_key(dates[i], timeframe)
        if key is None:
            continue
        try:
            hi, lo = float(highs[i]), float(lows[i])
        except (TypeError, ValueError):
            continue
        if hi != hi or lo != lo:                      # NaN
            continue
        iso = _as_date(dates[i])
        iso = iso.isoformat() if iso else None
        b = buckets.get(key)
        if b is None:
            buckets[key] = {"key": key, "start": iso, "end": iso,
                            "high": hi, "low": lo, "days": 1}
            order.append(key)
        else:
            if hi > b["high"]:
                b["high"] = hi
            if lo < b["low"]:
                b["low"] = lo
            b["end"] = iso
            b["days"] += 1
    out = [buckets[k] for k in order]
    if limit is not None and limit > 0:
        out = out[-limit:]
    return out


# ══════════════════════════════════════════════════════════════════════════
# THE PER-SYMBOL READ THAT GETS STORED
# ══════════════════════════════════════════════════════════════════════════

def read(dates, highs, lows, timeframes=TIMEFRAMES) -> dict:
    """Everything a symbol needs to have its live state computed later,
    without keeping its bars around.

    Returns {timeframe: {prev_key, prev_high, prev_low,
                         cur_key, cur_high, cur_low, cur_days, state}} where
    `state` is the state as of the LAST DAILY BAR IN THE INPUT — the settled
    reading. `market_state.live()` recomputes it against a live quote; the
    stored value is what the board shows out of hours and what survives a
    restart.

    This is the whole reason the states are computed inside the watchlist
    scan: the scan already holds five years of daily bars per symbol, and
    this collapses them into about twenty numbers per symbol that the live
    layer can use forever without touching a bar again.
    """
    out: dict[str, dict] = {}
    for tf in timeframes:
        buckets = aggregate(dates, highs, lows, tf, limit=2)
        if not buckets:
            continue
        cur = buckets[-1]
        prev = buckets[-2] if len(buckets) >= 2 else None
        entry = {
            "cur_key": cur["key"], "cur_high": round(cur["high"], 4),
            "cur_low": round(cur["low"], 4), "cur_days": cur["days"],
            "cur_start": cur["start"],
            "prev_key": prev["key"] if prev else None,
            "prev_high": round(prev["high"], 4) if prev else None,
            "prev_low": round(prev["low"], 4) if prev else None,
        }
        entry["state"] = (state_of(entry["prev_high"], entry["prev_low"],
                                   entry["cur_high"], entry["cur_low"])
                          if prev else None)
        out[tf] = entry
    return out


def live_state(entry: dict | None, today_key, live_high=None, live_low=None) -> dict | None:
    """Re-read one stored timeframe entry against a live quote.

    `today_key` is `period_key(today, timeframe)` for this entry's timeframe —
    the bucket the current session belongs to.

    Two things can have changed since the scan wrote `entry`:

    1. A NEW PERIOD BEGAN. A scan that ran on Friday stores last week as the
       current weekly bar; read on Monday, that bar is finished and a new one
       has one day in it. Detected by comparing today's period key to the
       stored `cur_key` — when they differ, the stored current bucket becomes
       the prior one and the live session opens a fresh bucket.

    2. THE SAME PERIOD EXTENDED. Price made a new high or low since the scan.
       Merged by max/min, which makes the merge idempotent: re-reading with a
       live high that is already inside the stored range changes nothing.

    `live_high`/`live_low` are today's session extremes (Schwab's day high and
    day low). Omit them and this returns the STORED reading with `live: False`
    and, when that candle is not the current period's, `stale: True` and the
    candle's own key in `as_of`.

    A rollover only happens when there is a live quote to open the new period
    with. Rolling without one would blank the state — and there are ordinary
    reasons for no quote to arrive: it is a weekend, it is pre-market, the
    scan is a day behind, the broker is disconnected. In every one of those
    the last settled candle is the right thing to show, labelled with its own
    date, rather than a dashboard of dashes.
    """
    if not entry:
        return None
    tf_key = entry.get("_tf")
    cur_key = entry.get("cur_key")
    prev_high, prev_low = entry.get("prev_high"), entry.get("prev_low")
    cur_high, cur_low = entry.get("cur_high"), entry.get("cur_low")
    cur_days = entry.get("cur_days") or 0
    lh = _num(live_high)
    ll = _num(live_low)
    has_live = lh is not None and ll is not None and lh >= ll > 0
    moved = (today_key is not None and cur_key is not None
             and today_key != cur_key)
    rolled = False
    if has_live and moved:
        # A new period began AND price is trading in it: the stored candle is
        # finished and becomes the reference.
        rolled = True
        prev_high, prev_low = cur_high, cur_low
        cur_high, cur_low, cur_days = lh, ll, 1
        as_of = today_key
    elif has_live:
        cur_high = lh if cur_high is None else max(cur_high, lh)
        cur_low = ll if cur_low is None else min(cur_low, ll)
        as_of = cur_key
    else:
        as_of = cur_key
    if cur_high is None or cur_low is None:
        return {"state": None, "live": False, "rolled": rolled,
                "stale": bool(moved), "as_of": as_of,
                "prev_high": prev_high, "prev_low": prev_low,
                "cur_high": None, "cur_low": None, "cur_days": 0,
                "complete": False, "tf": tf_key}
    return {
        "state": state_of(prev_high, prev_low, cur_high, cur_low),
        "live": bool(has_live), "rolled": rolled,
        # True when the candle being reported is not the current period's —
        # the caller shows its date rather than implying it is today's.
        "stale": bool(moved and not rolled),
        "as_of": as_of,
        "prev_high": prev_high, "prev_low": prev_low,
        "cur_high": round(cur_high, 4), "cur_low": round(cur_low, 4),
        "cur_days": cur_days,
        # An in-progress bucket is never complete. Daily bars are complete
        # only once the session has closed, which this module cannot know —
        # so it reports False and lets the caller, which does know, say
        # otherwise.
        "complete": False, "tf": tf_key,
    }


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def live_read(stored: dict | None, today_keys: dict,
              live_high=None, live_low=None,
              timeframes=TIMEFRAMES) -> dict:
    """`live_state` across every timeframe. `today_keys` is
    {timeframe: period_key(today, timeframe)} — passed in rather than computed
    because this module owns no clock, and because computing it once per
    request instead of once per symbol is the difference between five
    date operations and six thousand."""
    out = {}
    for tf in timeframes:
        e = (stored or {}).get(tf)
        if not e:
            continue
        e = dict(e)
        e["_tf"] = tf
        r = live_state(e, today_keys.get(tf), live_high, live_low)
        if r is not None:
            out[tf] = r
    return out


# ══════════════════════════════════════════════════════════════════════════
# BREADTH
# ══════════════════════════════════════════════════════════════════════════

def tally(states) -> dict:
    """Count states. `n` counts only classified bars; `unknown` counts the
    rest, and it is reported rather than dropped so a breadth bar built from
    40 of 60 names cannot look like it was built from 60."""
    out = {s: 0 for s in STATES}
    unknown = 0
    for s in (states or []):
        if s in out:
            out[s] += 1
        else:
            unknown += 1
    out["n"] = sum(out[s] for s in STATES)
    out["unknown"] = unknown
    return out


def percentages(counts: dict) -> dict:
    """Percentage of the classified population in each state, to one decimal.

    The four values are made to sum to exactly 100.0 by giving the largest
    bucket the rounding remainder. Without that the stacked bar's segments
    add to 99.9 or 100.1 and leave a visible sliver of background at the end
    of the bar — the kind of defect that reads as a data problem when it is
    only arithmetic.
    """
    n = (counts or {}).get("n") or 0
    if n <= 0:
        return {s: 0.0 for s in STATES}
    pct = {s: round(counts.get(s, 0) * 100.0 / n, 1) for s in STATES}
    drift = round(100.0 - sum(pct.values()), 1)
    if drift:
        biggest = max(STATES, key=lambda s: counts.get(s, 0))
        pct[biggest] = round(pct[biggest] + drift, 1)
    return pct


def breadth(rows, timeframes=TIMEFRAMES) -> dict:
    """Per-timeframe tallies + percentages over a list of per-symbol reads.

    `rows` is a list of {timeframe: {state: ...}} — whatever `live_read`
    returned for each symbol.
    """
    out = {}
    for tf in timeframes:
        counts = tally([(r.get(tf) or {}).get("state") for r in (rows or [])])
        out[tf] = {"counts": counts, "pct": percentages(counts),
                   "n": counts["n"], "unknown": counts["unknown"]}
    return out


def directional_share(counts: dict) -> float | None:
    """Share of the classified population that is directional UP, as a
    percentage — 2U over (2U + 2D), ignoring 1s and 3s.

    This is the sector leader/laggard measure. It ignores inside bars because
    an inside bar has no direction, and it ignores outside bars because a 3
    took out BOTH extremes and calling it up or down requires a close, which
    is a different measurement than the one this module makes. A sector that
    is all 1s and 3s returns None — no direction, rather than a misleading 50.
    """
    up = (counts or {}).get("2U", 0)
    down = (counts or {}).get("2D", 0)
    total = up + down
    if total <= 0:
        return None
    return round(up * 100.0 / total, 1)


def continuity(read_row: dict, timeframes=("D", "W", "M")) -> dict:
    """Do the timeframes agree? Counts how many of the given timeframes are
    2U versus 2D, and reports agreement only when every classified one points
    the same way.

    Full-timeframe continuity is the one composite this module computes,
    because it is a statement about the states themselves rather than an
    opinion about what they imply. `aligned` is None when fewer than two
    timeframes are directional — one agreeing timeframe is not agreement.
    """
    ups = downs = other = 0
    for tf in timeframes:
        s = (read_row.get(tf) or {}).get("state")
        if s == "2U":
            ups += 1
        elif s == "2D":
            downs += 1
        elif s is not None:
            other += 1
    directional = ups + downs
    aligned = None
    if directional >= 2:
        if downs == 0:
            aligned = "up"
        elif ups == 0:
            aligned = "down"
        else:
            aligned = "mixed"
    return {"up": ups, "down": downs, "other": other,
            "directional": directional, "aligned": aligned,
            "timeframes": list(timeframes)}
