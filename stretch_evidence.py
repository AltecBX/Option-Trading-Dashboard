"""stretch_evidence.py — what a stock does AFTER it reaches its usual high or low.

The workflow this measures, as it is done by eye today: look at the last N
weeks on the Analyze tab, note where the stock's weekly highs usually land
above Friday's close, wait for it to get there, and only then sell a call
above it — because the week usually closes below its high. The same in
reverse for puts. The Analyze charts already draw that line (the dashed
median high and median low). What they cannot say is what happened NEXT.

This module answers that from the daily bars, for calls and puts
separately, on two horizons:

  week   the line is measured from the PRIOR WEEK'S LAST CLOSE (the same
         anchor as the Analyze chart in Friday-baseline mode), and the
         trade is the expiry at the end of this week;
  day    the line is measured from the PRIOR SESSION'S CLOSE, and the
         trade is a same-day expiry (the Monday/Wednesday/Friday names).

For every past window (a week, a day) and every level on a small grid, it
finds the FIRST bar whose high reached the level (or whose low reached it,
for puts) — an entry a seller could actually have taken — and records only
what happened from that bar on:

  beyond   how much further the stock travelled past the level, in sigma
  term     where the window CLOSED relative to the level, in sigma;
           positive means it closed BEYOND the level, against the seller
  left     how many full sessions remained after the crossing bar

Everything is in the stock's OWN sigma known at the window's anchor, so
one name's record can be pooled with another's when its own is thin, and
so a 3% line on a wild stock and a 3% line on a quiet one are not treated
as the same event. Nothing after the crossing bar influences whether the
event is selected; nothing before it is credited to the seller.

What this deliberately does NOT claim: the time of day the level was
crossed (daily bars cannot see it — the crossing bar's whole remaining
range is charged to the seller, an upper bound); historical option
premiums (there are none here); or that the stock "must" come back.
A weekly high is the high of one week, not a ceiling.

Pure stdlib. Every number is reproducible from its inputs.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Sequence

import spike_evidence as sev

SCHEMA = "stretch/v1"

WEEK_LEVELS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)   # in WEEKLY sigma
DAY_LEVELS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)                  # in DAILY sigma
SESSIONS_PER_WEEK = 5
MIN_EVENTS = 20           # fewer comparable crossings than this = pool, or refuse
MIN_OWN_FOR_MOSTLY = 5    # below this the answer is essentially the pool's
LINE_PCT = 0.50           # the trigger line is the MEDIAN of the window's extreme
Z_20_DELTA = 0.84         # a 20-delta strike sits ~0.84 sigma out on a lognormal
MIN_WEEKS = 12

SIDES = ("call", "put")
HORIZONS = ("week", "day")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def rows_from_bars(bars: Sequence[dict]) -> list[dict]:
    """Daily bars → clean rows, oldest first, bad prints dropped."""
    out = []
    for b in bars or []:
        d = _as_date(b.get("date"))
        h, lo, c = _num(b.get("high")), _num(b.get("low")), _num(b.get("close"))
        if d is None or not h or not lo or not c or h <= 0 or lo <= 0 or c <= 0:
            continue
        out.append({"date": d, "high": h, "low": lo, "close": c,
                    "open": _num(b.get("open")) or c})
    out.sort(key=lambda r: r["date"])
    return out


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_groups(rows: Sequence[dict]) -> list[dict]:
    """Rows grouped into Monday-anchored weeks. Each carries the close that
    preceded it (`anchor`) and the index of its first row, so a
    point-in-time sigma can be taken at that index. The first week has
    no anchor and is dropped."""
    groups: list[dict] = []
    for i, r in enumerate(rows):
        ws = week_start(r["date"])
        if groups and groups[-1]["start"] == ws:
            groups[-1]["days"].append(r)
        else:
            groups.append({"start": ws, "days": [r], "i0": i})
    out = []
    for g in groups[1:]:
        anchor = rows[g["i0"] - 1]["close"]
        if anchor > 0:
            out.append({**g, "anchor": anchor})
    return out


def _crossing(days: Sequence[dict], level: float, side: str) -> int | None:
    """Index of the first bar that reached `level` — the entry."""
    for i, d in enumerate(days):
        if side == "call" and d["high"] >= level:
            return i
        if side == "put" and d["low"] <= level:
            return i
    return None


def _outcome(days: Sequence[dict], idx: int, level: float, side: str, sigma: float) -> tuple:
    """(left, beyond, term) from the crossing bar on, in sigma. The crossing
    bar's own remaining range is charged in full: daily bars cannot say
    whether its high came before or after the level was reached, so the
    seller is given the worse reading."""
    after = days[idx:]
    close = after[-1]["close"]
    if side == "call":
        beyond = math.log(max(d["high"] for d in after) / level) / sigma
        term = math.log(close / level) / sigma
    else:
        beyond = math.log(level / min(d["low"] for d in after)) / sigma
        term = math.log(level / close) / sigma
    return (len(days) - idx - 1, max(0.0, beyond), term)


def _pct_stats(vals: Sequence[float], best_is_max: bool, q: float = LINE_PCT) -> dict:
    """The line (the q-quantile of the window's extreme — the median by
    default, which is the dashed line the Analyze chart draws) and the
    record, which is context and never a ceiling."""
    if not vals:
        return {"median": None, "best": None, "n": 0}
    s = sorted(vals)
    line = _pct(s, q) if best_is_max else _pct(s, 1.0 - q)
    return {"median": line, "best": (max if best_is_max else min)(vals), "n": len(vals)}


def profile(bars: Sequence[dict], today: date | None = None,
            line_q: float = LINE_PCT) -> dict | None:
    """The whole record for one name: the trigger lines, the anchor for the
    week in progress, and every crossing event on both horizons and both
    sides. None when there is not enough history to say anything."""
    rows = rows_from_bars(bars)
    t = today or (rows[-1]["date"] if rows else date.today())
    this_monday = week_start(t)
    # The week in progress has no finished high or close; it is not history.
    hist = [r for r in rows if r["date"] < this_monday]
    weeks = week_groups(hist)
    if len(weeks) < MIN_WEEKS:
        return None
    sigma_now = sev.daily_sigma(rows)
    if not sigma_now:
        return None

    events: dict = {h: {s: {} for s in SIDES} for h in HORIZONS}
    wk_high, wk_low, wk_close = [], [], []
    for g in weeks:
        sd = sev.daily_sigma(hist, end=g["i0"])
        if not sd:
            continue
        sw = sd * math.sqrt(SESSIONS_PER_WEEK)
        a = g["anchor"]
        days = g["days"]
        wk_high.append(max(d["high"] for d in days) / a - 1.0)
        wk_low.append(min(d["low"] for d in days) / a - 1.0)
        wk_close.append(days[-1]["close"] / a - 1.0)
        for lvl in WEEK_LEVELS:
            for side in SIDES:
                L = a * math.exp(lvl * sw if side == "call" else -lvl * sw)
                idx = _crossing(days, L, side)
                if idx is None:
                    continue
                events["week"][side].setdefault(f"{lvl:g}", []).append(
                    _outcome(days, idx, L, side, sw))
    d_high, d_low = [], []
    for i in range(sev.SIGMA_WINDOW + 2, len(hist)):
        sd = sev.daily_sigma(hist, end=i)
        if not sd:
            continue
        pc = hist[i - 1]["close"]
        b = hist[i]
        d_high.append(b["high"] / pc - 1.0)
        d_low.append(b["low"] / pc - 1.0)
        for lvl in DAY_LEVELS:
            for side in SIDES:
                L = pc * math.exp(lvl * sd if side == "call" else -lvl * sd)
                idx = _crossing([b], L, side)
                if idx is None:
                    continue
                events["day"][side].setdefault(f"{lvl:g}", []).append(
                    _outcome([b], 0, L, side, sd))

    if len(wk_high) < MIN_WEEKS:
        return None
    hi, lo = _pct_stats(wk_high, True, line_q), _pct_stats(wk_low, False, line_q)
    dhi, dlo = _pct_stats(d_high, True, line_q), _pct_stats(d_low, False, line_q)
    sigma_week = sigma_now * math.sqrt(SESSIONS_PER_WEEK)
    lines = {
        "week": {"high_pct": hi["median"], "low_pct": lo["median"],
                 "close_pct": median(wk_close) if wk_close else None,
                 "best_high_pct": hi["best"], "worst_low_pct": lo["best"], "n": hi["n"],
                 "anchor": "the prior week's last close"},
        "day": {"high_pct": dhi["median"], "low_pct": dlo["median"],
                "best_high_pct": dhi["best"], "worst_low_pct": dlo["best"], "n": dhi["n"],
                "anchor": "the prior session's close"},
    }
    anchor_row = hist[-1]
    out = {
        "schema": SCHEMA, "n_bars": len(rows), "n_weeks": len(wk_high), "n_days": len(d_high),
        "sigma_daily": sigma_now, "sigma_weekly": sigma_week,
        "sigma_annual": sigma_now * math.sqrt(252),
        "lines": lines, "line_q": line_q,
        "anchor": {"week_start": this_monday.isoformat(),
                   "week_close": anchor_row["close"], "week_close_date": anchor_row["date"].isoformat(),
                   "prev_close": rows[-1]["close"] if rows[-1]["date"] < t else
                                 (rows[-2]["close"] if len(rows) > 1 else None),
                   "prev_close_date": (rows[-1]["date"] if rows[-1]["date"] < t else
                                       (rows[-2]["date"] if len(rows) > 1 else None))},
        "events": events,
        "weeks": [{"high": h, "low": l, "close": c} for h, l, c in zip(wk_high, wk_low, wk_close)],
    }
    if out["anchor"]["prev_close_date"]:
        out["anchor"]["prev_close_date"] = out["anchor"]["prev_close_date"].isoformat()
    out["vs_monday"] = {s: vs_monday(weeks, hist, s, lines["week"]) for s in SIDES}
    return out


def line_sigma(pct: float | None, sigma: float | None) -> float | None:
    """A percent line from the anchor, in sigma units."""
    if pct is None or not sigma:
        return None
    return abs(math.log1p(pct)) / sigma


def level_for(move_sigma: float, horizon: str) -> tuple[float, bool]:
    """The grid row at or below this move — never above, so the sample is
    always of crossings at least as large as the one in front of us.
    Below the grid it clamps to the first row and says so."""
    grid = WEEK_LEVELS if horizon == "week" else DAY_LEVELS
    pick = max((g for g in grid if g <= move_sigma + 1e-9), default=None)
    if pick is None:
        return grid[0], True
    return pick, False


def _select(evs: Sequence[tuple], left: int) -> tuple[list, str]:
    """Comparable crossings by how much of the window remained. The exact
    bucket when it is deep enough; otherwise every crossing with at least
    as much left — those had MORE room to run, so the result is an upper
    bound on the risk, and the basis says so."""
    exact = [e for e in evs if e[0] == left]
    if len(exact) >= MIN_EVENTS:
        return exact, f"crossings with exactly {left} session{'s' if left != 1 else ''} left"
    wider = [e for e in evs if e[0] >= left]
    return wider, (f"crossings with {left} or more sessions left (an upper bound — "
                   "earlier crossings had more room to run)")


def windows_from(evs: Sequence[tuple], side: str, sigma: float) -> list[dict]:
    """Events in sigma → the {low, high, term} fractions weekly_sell's strike
    engine scores, relative to the crossing level. `sigma` is the horizon
    sigma of the stock being priced, which is what lets pooled events from
    other names be read in this name's dollars."""
    out = []
    for _left, beyond, term in evs:
        if side == "call":
            out.append({"low": 0.0, "high": math.exp(beyond * sigma) - 1.0,
                        "term": math.exp(term * sigma) - 1.0})
        else:
            out.append({"low": math.exp(-beyond * sigma) - 1.0, "high": 0.0,
                        "term": math.exp(-term * sigma) - 1.0})
    return out


def evidence(prof: dict, horizon: str, side: str, move_sigma: float, left: int,
             pool: dict | None = None) -> dict:
    """The comparable record for a live crossing: this name's own events
    first; the pool (other names' crossings, in sigma) only when its own
    are thin, and graded so the card can say which it was."""
    lvl, clamped = level_for(move_sigma, horizon)
    key = f"{lvl:g}"
    own_all = ((prof.get("events") or {}).get(horizon) or {}).get(side, {}).get(key, [])
    own, basis = _select(own_all, left if horizon == "week" else 0)
    sigma = prof["sigma_weekly"] if horizon == "week" else prof["sigma_daily"]
    n_own = len(own)
    pooled: list = []
    if n_own < MIN_EVENTS and pool:
        cell = ((pool.get(horizon) or {}).get(side) or {}).get(key) or []
        pooled, _ = _select(cell, left if horizon == "week" else 0)
    n_pool = len(pooled)
    if n_own >= MIN_EVENTS:
        grade = "MEASURED"
    elif n_own + n_pool >= MIN_EVENTS:
        grade = "MOSTLY POOLED" if n_own >= MIN_OWN_FOR_MOSTLY else "POOLED"
    else:
        grade = "THIN"
    evs = list(own) + list(pooled)
    return {"level": lvl, "clamped": clamped, "basis": basis,
            "n_own": n_own, "n_pool": n_pool, "n": len(evs), "grade": grade,
            "windows": windows_from(evs, side, sigma),
            "p_closed_back": (sum(1 for e in evs if e[2] <= 0) / len(evs)) if evs else None,
            "median_beyond_pct": (math.exp(median(e[1] for e in evs) * sigma) - 1.0) if evs else None,
            "p90_beyond_pct": (math.exp(_pct(sorted(e[1] for e in evs), 0.9) * sigma) - 1.0) if evs else None}


def _pct(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def add_to_pool(pool: dict, prof: dict, cap: int = 4000) -> None:
    """Fold one name's events into the shared pool, in sigma. Bounded per
    cell so a thousand-name watchlist does not grow without limit."""
    for h in HORIZONS:
        for s in SIDES:
            for k, evs in ((prof.get("events") or {}).get(h, {}).get(s, {}) or {}).items():
                cell = pool.setdefault(h, {}).setdefault(s, {}).setdefault(k, [])
                cell.extend(evs)
                if len(cell) > cap:
                    del cell[: len(cell) - cap]


def vs_monday(weeks: Sequence[dict], hist: Sequence[dict], side: str, line: dict) -> dict | None:
    """The like-for-like check of the workflow itself, on the bars alone.

    Two ways to sell a 20-delta option this week. MONDAY: sell it at the
    open of the week, 0.84 weekly sigma from the anchor. THE LINE: wait
    for the stock to reach its usual high (the median line, in percent,
    as the chart draws it), then sell 0.84 sigma beyond THAT level over
    the sessions that remain. Same rule, different entry. Reports how
    often each finished through its strike, how often it was touched,
    how far from the anchor each strike sat, and how many weeks the
    line rule actually got a trade. Premium is not on the bars and is
    not invented here."""
    pct = line.get("high_pct") if side == "call" else line.get("low_pct")
    if pct is None or not weeks:
        return None
    n = fin_m = touch_m = 0
    crossed = fin_l = touch_l = back = 0
    dist_m, dist_l, cross_day = [], [], []
    for g in weeks:
        sd = sev.daily_sigma(hist, end=g["i0"])
        if not sd:
            continue
        sw = sd * math.sqrt(SESSIONS_PER_WEEK)
        a, days = g["anchor"], g["days"]
        hi_w = max(d["high"] for d in days)
        lo_w = min(d["low"] for d in days)
        close_w = days[-1]["close"]
        n += 1
        k_m = a * math.exp(Z_20_DELTA * sw if side == "call" else -Z_20_DELTA * sw)
        dist_m.append(k_m / a - 1.0)
        if side == "call":
            fin_m += close_w > k_m
            touch_m += hi_w >= k_m
        else:
            fin_m += close_w < k_m
            touch_m += lo_w <= k_m
        L = a * (1.0 + pct)
        idx = _crossing(days, L, side)
        if idx is None:
            continue
        crossed += 1
        cross_day.append(days[idx]["date"].weekday())
        left = max(0.5, len(days) - idx - 1)
        k_l = L * math.exp((Z_20_DELTA if side == "call" else -Z_20_DELTA) * sd * math.sqrt(left))
        dist_l.append(k_l / a - 1.0)
        after = days[idx:]
        if side == "call":
            fin_l += close_w > k_l
            touch_l += max(d["high"] for d in after) >= k_l
            back += close_w <= L
        else:
            fin_l += close_w < k_l
            touch_l += min(d["low"] for d in after) <= k_l
            back += close_w >= L
    if not n:
        return None
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    return {
        "line_pct": pct, "weeks": n, "weeks_crossed": crossed,
        "opportunity": crossed / n,
        "closed_back_inside": (back / crossed) if crossed else None,
        "typical_cross_day": (names[int(median(cross_day))] if cross_day else None),
        "monday": {"finished_through": fin_m / n, "touched": touch_m / n,
                   "strike_from_anchor_pct": median(dist_m) if dist_m else None},
        "line": {"finished_through": (fin_l / crossed) if crossed else None,
                 "touched": (touch_l / crossed) if crossed else None,
                 "strike_from_anchor_pct": median(dist_l) if dist_l else None},
    }


def describe(prof: dict, horizon: str, side: str) -> dict:
    """The trigger line for one horizon and side, in percent, sigma and
    dollars from its anchor — what the scanner waits for."""
    ln = prof["lines"][horizon]
    pct = ln["high_pct"] if side == "call" else ln["low_pct"]
    sigma = prof["sigma_weekly"] if horizon == "week" else prof["sigma_daily"]
    anchor = prof["anchor"]["week_close"] if horizon == "week" else prof["anchor"]["prev_close"]
    return {"pct": pct, "sigma": line_sigma(pct, sigma),
            "price": (anchor * (1.0 + pct)) if (anchor and pct is not None) else None,
            "anchor": anchor, "anchor_label": ln["anchor"], "n": ln["n"],
            "record_pct": ln["best_high_pct"] if side == "call" else ln["worst_low_pct"]}
