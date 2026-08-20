"""korea_lead_engine.py — pure math for KOREA LEAD.

Korea finishes trading before New York starts. That ordering is the whole
premise: whatever Seoul decided about memory and semiconductors today is
already a completed, published fact by the time a U.S. chip stock has to
choose an opening price. This module measures whether that fact has ever
told us anything, and refuses to say more than the measurement supports.

Design rules — the same discipline as gap_engine.py:

  Pure + stdlib.  No I/O, no network, no wall clock, no persistence. Every
  input arrives as an argument; korea_lead.py owns fetching and caching.
  A function here given the same bars returns the same answer forever.

  One direction of time.  A Korean session dated D is matched to the U.S.
  session dated D, because Korea closed first. Nothing later than D is
  allowed to touch an observation dated D, and `align()` takes a `through`
  date it will not read past. There is no code path here that can see a
  U.S. close before it computes a U.S. open.

  Sign is never averaged away.  KOSPI +3% and KOSPI −3% are different
  events, so every conditional table is split by sign before it is split
  by magnitude. A bucket that mixed them would report the average of two
  behaviours that may be opposites.

  No n, no number.  Every rate ships with its sample size and a Wilson
  interval, both from metrics.py, and is called a MATCH RATE — never a
  probability. Nothing here has been calibrated as a forecast.

  Nothing is weighted.  There is no composite Korea score in this file and
  no weight on any input, because no weight has been validated. KOSPI,
  Samsung and SK Hynix are reported next to each other and the reader does
  the combining.

Return definitions, stated once and used everywhere:

  KOREA SIGNAL        close / prior close − 1     (each Korean symbol)
  U.S. OPENING GAP    open / prior close − 1      ← the predictive target
  U.S. OPEN TO CLOSE  close / same-day open − 1   ← diagnostic
  U.S. FULL DAY       close / prior close − 1     ← diagnostic

All four are percentages. They are never mixed: the opening gap and the
full day differ by exactly the open-to-close move, and treating any two of
them as interchangeable is how a signal that only predicts the open gets
sold as a signal that predicts the day.
"""

from __future__ import annotations

import math

from metrics import percentile, wilson_interval

ENGINE_VERSION = "korea-lead-1.0.0"

# Part of every cache key. If the meaning of an observation changes — a new
# return definition, a different alignment rule — this string changes with
# it, and yesterday's cached statistics stop being served for today's
# question. Renaming it is how a definition change is announced.
SIGNAL_DEFINITION = "kospi-close-to-close-vs-us-opening-gap-same-date-v1"

# A daily series that has been adjusted for splits and spinoffs should never
# produce an overnight move this large. The biggest genuine overnight moves
# in this universe are earnings gaps around +37%; the smallest ordinary
# split ratio (3-for-2) would show up as −33% in an UNADJUSTED series, so a
# limit here has to sit above the real moves and below nothing in
# particular. It is set clear of every real move measured across the target
# list and is deliberately NOT called a split detector: it does not know
# what happened, only that no adjusted series should print this, so the day
# is excluded and counted rather than explained.
MAX_CREDIBLE_MOVE_PCT = 50.0

# Magnitude buckets, in percent, as [low, high). A KOSPI move of exactly
# 1.00% belongs to the 1–2 bucket, not the 0–1 bucket; the boundary belongs
# to the bucket it opens.
BUCKET_EDGES = ((0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0),
                (5.0, float("inf")))

# The three measurements a target is scored on. The first is the one Korea
# Lead is actually about; the other two exist to show that it is not.
MEASURES = ("opening_gap", "open_to_close", "full_day")

MEASURE_LABEL = {
    "opening_gap": "Opening gap",
    "open_to_close": "Open to close",
    "full_day": "Full day",
}


# ── small helpers ───────────────────────────────────────────────────────────

def bar_date(bar) -> str:
    """The session date of a daily bar, as YYYY-MM-DD.

    Bars reach this module from two different loaders. One stamps a plain
    date, the other stamps a date with a noon-Eastern time on it so that a
    browser cannot render it as the previous day. Both mean the same
    session, and only the first ten characters carry that meaning.
    """
    if isinstance(bar, dict):
        raw = bar.get("date") or bar.get("day") or ""
    else:
        raw = bar
    return str(raw)[:10]


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _pct_change(now, before) -> float | None:
    a, b = _num(now), _num(before)
    if a is None or b is None or b <= 0 or a <= 0:
        return None
    return (a / b - 1.0) * 100.0


# A daily series has its sessions one day apart, give or take a weekend, so
# the MEDIAN spacing between consecutive bars is 1 day. A weekly series
# medians at 7 and a monthly one at about 30. This limit sits well clear of
# all three, and it exists because a provider can silently downgrade
# granularity while still answering 200 with a plausible-looking series:
# asking one of them for the longest available daily history returns
# MONTHLY bars, and a monthly Korean series correlated against daily U.S.
# opens would produce a number that is wrong and looks fine. Spacing is
# checked rather than trusted.
DAILY_MAX_MEDIAN_SPACING_DAYS = 4.0


def session_spacing_days(bars) -> float | None:
    """Median calendar days between consecutive sessions, or None when
    there are too few bars to tell."""
    rows = _by_date(bars)
    if len(rows) < 20:
        return None
    from datetime import date as _date
    gaps = []
    for i in range(1, len(rows)):
        try:
            gaps.append((_date.fromisoformat(rows[i][0])
                         - _date.fromisoformat(rows[i - 1][0])).days)
        except ValueError:
            continue
    return percentile(gaps, 0.5) if gaps else None


def is_daily_series(bars, limit: float = DAILY_MAX_MEDIAN_SPACING_DAYS) -> dict:
    """Whether a series really is what it claims to be.

    Returns {"daily": bool, "spacing_days": float|None, "reason": str|None}.
    An undecidable series — too few bars to measure spacing at all — is
    allowed through as daily and says so, because refusing a short series
    on suspicion would take out every newly-listed ticker.
    """
    sp = session_spacing_days(bars)
    if sp is None:
        return {"daily": True, "spacing_days": None,
                "reason": "too few sessions to measure the spacing"}
    if sp > limit:
        return {"daily": False, "spacing_days": sp,
                "reason": (f"sessions are a median of {sp:g} calendar days "
                           f"apart, which is not a daily series — the "
                           f"provider appears to have returned "
                           f"{'weekly' if sp < 15 else 'monthly'} bars")}
    return {"daily": True, "spacing_days": sp, "reason": None}


def _by_date(bars) -> list:
    """Bars sorted by session date, one per date, later duplicates winning.

    A feed that repeats a date is not a feed with two sessions in it. It is
    a feed that sent the same session twice, usually because a provisional
    bar was re-published after the close, and the later copy is the settled
    one. Keeping both would double-count that session in every correlation.
    """
    seen: dict = {}
    for b in bars or []:
        d = bar_date(b)
        if len(d) == 10 and d[4] == "-" and d[7] == "-":
            seen[d] = b
    return [(d, seen[d]) for d in sorted(seen)]


# ── return definitions ──────────────────────────────────────────────────────

def close_to_close(bars, max_move_pct: float = MAX_CREDIBLE_MOVE_PCT) -> dict:
    """{session date: close-to-close percent} for one market's daily bars.

    This is the Korea signal for every Korean symbol. The first session in
    the series has no prior close and therefore no return — it is absent
    from the result rather than present as a zero.
    """
    rows = _by_date(bars)
    out: dict = {}
    for i in range(1, len(rows)):
        d, b = rows[i]
        r = _pct_change(b.get("close"), rows[i - 1][1].get("close"))
        if r is None or abs(r) >= max_move_pct:
            continue
        out[d] = r
    return out


def us_measures(bars, max_move_pct: float = MAX_CREDIBLE_MOVE_PCT) -> dict:
    """{session date: {opening_gap, open_to_close, full_day}} for U.S. bars.

    All three at once, from the same two bars, so they cannot drift apart.
    A session is kept only when all three are computable and none of them
    exceeds the credibility limit — a day whose open is unusable makes the
    gap unusable, and a gap is the only reason this module reads U.S. bars.
    """
    rows = _by_date(bars)
    out: dict = {}
    for i in range(1, len(rows)):
        d, b = rows[i]
        prev_close = rows[i - 1][1].get("close")
        gap = _pct_change(b.get("open"), prev_close)
        o2c = _pct_change(b.get("close"), b.get("open"))
        full = _pct_change(b.get("close"), prev_close)
        if gap is None or o2c is None or full is None:
            continue
        # The limit is inclusive on purpose: an unadjusted 2-for-1 split
        # lands on exactly −50%, which is the single most likely way this
        # guard ever gets used.
        if max(abs(gap), abs(o2c), abs(full)) >= max_move_pct:
            continue
        out[d] = {"opening_gap": gap, "open_to_close": o2c, "full_day": full,
                  "prev_close": _num(prev_close), "open": _num(b.get("open")),
                  "close": _num(b.get("close"))}
    return out


# ── session alignment ───────────────────────────────────────────────────────

def align(korea_returns: dict, us: dict, through: str | None = None,
          extras: dict | None = None) -> dict:
    """Match Korean session D to U.S. session D and return the observations.

    Korea trades and closes before New York opens, so the two sessions that
    share a calendar date are already in the right order — Korea first.
    That is why nothing is shifted here. Shifting Korea a day forward would
    pair a Korean session with the U.S. session that PRECEDED it, and
    shifting it a day back would hand a Korean session U.S. information
    from its own future.

    A date only becomes an observation when BOTH markets actually traded
    it. When Korea traded and the U.S. did not — a U.S. holiday — that
    Korean session is skipped, not rolled forward into the next U.S.
    session several days later. Rolling it forward would quietly claim that
    a Korean move on Thursday still described a U.S. open on Tuesday, and
    the study cannot support that claim.

    `through` is an inclusive last date. Observations dated after it are
    not built at all, which is what keeps today's own unfinished session
    out of the history that today is being judged against.

    `extras` is {name: {date: pct}} for the other Korean symbols. They ride
    along on the matched dates so a later caller can ask what Samsung did
    on the same sessions without redoing the intersection.
    """
    obs = []
    skipped = {"us_only": 0, "korea_only": 0, "after_through": 0}
    korea_dates = set(korea_returns)
    us_dates = set(us)
    for d in sorted(us_dates | korea_dates):
        in_k, in_u = d in korea_dates, d in us_dates
        if through is not None and d > through:
            if in_k and in_u:
                skipped["after_through"] += 1
            continue
        if in_k and not in_u:
            skipped["korea_only"] += 1
            continue
        if in_u and not in_k:
            skipped["us_only"] += 1
            continue
        m = us[d]
        row = {"date": d, "korea": korea_returns[d],
               "opening_gap": m["opening_gap"],
               "open_to_close": m["open_to_close"],
               "full_day": m["full_day"]}
        for name, series in (extras or {}).items():
            row[name] = series.get(d)
        obs.append(row)
    return {"observations": obs, "skipped": skipped}


# ── correlation ─────────────────────────────────────────────────────────────

def pearson(xs, ys) -> float | None:
    """Pearson correlation. None below three pairs or on a flat series —
    a correlation with no variation to correlate is undefined, not zero."""
    pairs = [(a, b) for a, b in zip(xs, ys)
             if _num(a) is not None and _num(b) is not None]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sx = math.sqrt(sum((p[0] - mx) ** 2 for p in pairs))
    sy = math.sqrt(sum((p[1] - my) ** 2 for p in pairs))
    if sx <= 0 or sy <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pairs) / (sx * sy)


def _ranks(vals) -> list:
    """Competition-free average ranks: tied values all take the mean of the
    positions they occupy. Without this a run of equal values would be
    ordered arbitrarily and Spearman would report structure that is only an
    artifact of the sort."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs, ys) -> float | None:
    """Rank correlation — Pearson on average ranks. Says whether bigger
    Korean moves went with bigger U.S. gaps without assuming the
    relationship is a straight line, and without letting one crash
    dominate the answer the way a raw correlation can."""
    pairs = [(a, b) for a, b in zip(xs, ys)
             if _num(a) is not None and _num(b) is not None]
    if len(pairs) < 3:
        return None
    return pearson(_ranks([p[0] for p in pairs]), _ranks([p[1] for p in pairs]))


def same_direction(obs, measure: str) -> dict | None:
    """How often the U.S. measurement moved the same way Korea did.

    A session where either side finished exactly unchanged has no direction
    to agree or disagree with, so it is excluded from both the numerator
    and the denominator and reported separately as `flat`. Counting a zero
    as a fall is how a rate quietly picks up a bias it did not earn.
    """
    k = n = flat = 0
    for o in obs or []:
        a, b = _num(o.get("korea")), _num(o.get(measure))
        if a is None or b is None:
            continue
        if a == 0.0 or b == 0.0:
            flat += 1
            continue
        n += 1
        if (a > 0) == (b > 0):
            k += 1
    if not n:
        return None
    w = wilson_interval(k, n) or {}
    return {"k": k, "n": n, "flat": flat,
            "rate_pct": round(k / n * 100.0, 1),
            "lo_pct": round(w.get("lo", 0.0) * 100.0, 1),
            "hi_pct": round(w.get("hi", 0.0) * 100.0, 1)}


def distribution(vals) -> dict | None:
    """Average, median, 25th and 75th percentile of a list of moves —
    what actually happened, before any model is laid on top of it."""
    xs = [v for v in (_num(v) for v in (vals or [])) if v is not None]
    if not xs:
        return None
    return {"n": len(xs),
            "avg_pct": round(sum(xs) / len(xs), 3),
            "median_pct": round(percentile(xs, 0.5), 3),
            "p25_pct": round(percentile(xs, 0.25), 3),
            "p75_pct": round(percentile(xs, 0.75), 3)}


def measure_stats(obs, measure: str, signal: str = "korea") -> dict:
    """Everything measurable about one Korean signal against one U.S.
    measurement: both correlations, the same-direction rate with its
    interval, the sample size, and the distribution of the U.S. moves."""
    xs = [o.get(signal) for o in obs or []]
    ys = [o.get(measure) for o in obs or []]
    pairs = [(a, b) for a, b in zip(xs, ys)
             if _num(a) is not None and _num(b) is not None]
    p = pearson(xs, ys)
    s = spearman(xs, ys)
    return {"measure": measure, "signal": signal, "n": len(pairs),
            "pearson": None if p is None else round(p, 3),
            "spearman": None if s is None else round(s, 3),
            "same_direction": same_direction(obs, measure),
            "distribution": distribution(ys)}


# ── conditional buckets ─────────────────────────────────────────────────────

def bucket_key(pct) -> str | None:
    """Which signed magnitude bucket a Korean move falls into.

    Returns e.g. "+3to5" or "-0to1". A move of exactly zero has no
    direction, so it has no signed bucket and returns None rather than
    being assigned to the upside by the accident of `>= 0`.
    """
    v = _num(pct)
    if v is None or v == 0.0:
        return None
    sign = "+" if v > 0 else "-"
    mag = abs(v)
    for lo, hi in BUCKET_EDGES:
        if lo <= mag < hi:
            top = "plus" if hi == float("inf") else f"{hi:g}"
            return f"{sign}{lo:g}to{top}"
    return None


def bucket_label(key: str) -> str:
    """The bucket in words, for a screen: "KOSPI down 3% to 5%"."""
    if not key:
        return "unclassified"
    sign, rest = key[0], key[1:]
    lo, _, hi = rest.partition("to")
    way = "up" if sign == "+" else "down"
    if hi == "plus":
        return f"KOSPI {way} more than {lo}%"
    return f"KOSPI {way} {lo}% to {hi}%"


def bucket_rows(obs, measure: str = "opening_gap", signal: str = "korea") -> list:
    """One row per signed magnitude bucket that has observations in it.

    Upside and downside are never merged. Two buckets of the same size on
    opposite sides of zero are two different questions, and the answers
    have no obligation to be mirror images.
    """
    groups: dict = {}
    for o in obs or []:
        key = bucket_key(o.get(signal))
        if key is None or _num(o.get(measure)) is None:
            continue
        groups.setdefault(key, []).append(o)
    rows = []
    for key, rows_in in groups.items():
        vals = [o[measure] for o in rows_in]
        sd = same_direction(rows_in, measure)
        rows.append({"bucket": key, "label": bucket_label(key),
                     "sign": "up" if key[0] == "+" else "down",
                     "n": len(rows_in), "same_direction": sd,
                     "distribution": distribution(vals)})
    rows.sort(key=lambda r: (r["sign"] == "up", _edges_of(r["bucket"])[0]))
    return rows


def _edges_of(key: str):
    lo, _, hi = key[1:].partition("to")
    try:
        return (float(lo), float("inf") if hi == "plus" else float(hi))
    except ValueError:          # pragma: no cover - keys are built above
        return (0.0, 0.0)


# ── today, matched against history ──────────────────────────────────────────

def implied_gap(obs, today_korea_pct, measure: str = "opening_gap",
                signal: str = "korea", min_n: int = 8) -> dict:
    """What this target's opening gap did on the historical sessions whose
    Korean move looked like today's.

    Deliberately NOT a forecast. It is a lookup: find today's bucket, take
    every past session in it, and report the distribution of what followed
    together with the sample size and the interval. The same-direction
    figure is called a MATCH RATE for the whole length of this codebase,
    because it has never been calibrated against out-of-sample outcomes and
    a number called a probability would be claiming that it has.
    """
    key = bucket_key(today_korea_pct)
    out = {"korea_pct": _num(today_korea_pct), "bucket": key,
           "label": bucket_label(key) if key else None,
           "measure": measure, "usable": False, "reason": None,
           "n": 0, "same_direction": None, "distribution": None}
    if _num(today_korea_pct) is None:
        out["reason"] = "Korea has not produced a usable move for today yet."
        return out
    if key is None:
        out["reason"] = ("Korea finished exactly unchanged, which has no "
                         "direction to match against history.")
        return out
    rows = [o for o in (obs or [])
            if bucket_key(o.get(signal)) == key and _num(o.get(measure)) is not None]
    out["n"] = len(rows)
    if len(rows) < min_n:
        out["reason"] = (f"Only {len(rows)} matched session"
                         f"{'' if len(rows) == 1 else 's'} in this lookback — "
                         f"fewer than the {min_n} required before a rate is shown.")
        return out
    out["usable"] = True
    out["same_direction"] = same_direction(rows, measure)
    out["distribution"] = distribution([o[measure] for o in rows])
    return out


CONFIRMATION_CONFIRMED = "CONFIRMED"
CONFIRMATION_MIXED = "MIXED"
CONFIRMATION_DIVERGENCE = "DIVERGENCE"
CONFIRMATION_UNAVAILABLE = "UNAVAILABLE"

# Heuristic starting thresholds — NOT validated, and deliberately not a
# weighting. korea_lead.py overrides these from the settings file; they live
# here so the engine has one definition and the settings file has one place
# to disagree with it.
CONFIRMATION_GATES = {
    "min_same_sign_abs_pct": 0.3,        # each of the three must move this far
    "kospi_divergence_min_abs_pct": 0.5,  # the index must say something first
    "chip_opposite_min_pct": 1.0,         # how far a chip must go the other way
}


def chip_confirmation(kospi, samsung, hynix, gates: dict | None = None) -> dict:
    """Did Korea's two memory names go the way the index went — far enough
    that going the same way meant anything?

    Descriptive only. There is no weighting and no score: this counts how
    many of the two chip names agree in SIGN with KOSPI, and then asks
    whether the moves were large enough for the agreement to be worth a
    word. KOSPI is a broad index and the stocks traded here are not, so a
    session where the index rose while Samsung and SK Hynix fell is worth
    seeing plainly rather than being averaged into a single number that
    reads as mild.

    WHY MAGNITUDE AND NOT ONLY SIGN. Three series that all closed within
    five hundredths of a percent of unchanged are on the same side of zero
    by coin flip, and calling that CONFIRMED would make the strongest label
    the easiest one to earn on the quietest day of the year. So CONFIRMED
    additionally requires every one of the three to have travelled at least
    `min_same_sign_abs_pct`, and DIVERGENCE — a claim about a real
    disagreement — requires the index to have said something first and a
    chip name to have gone materially the other way.

    A missing name is missing. It is never counted as agreement and never
    assumed flat — with only one chip name readable the answer is at best
    a partial one, and it says which name it is missing.
    """
    g = dict(CONFIRMATION_GATES)
    g.update(gates or {})
    same_floor = abs(float(g["min_same_sign_abs_pct"]))
    div_floor = abs(float(g["kospi_divergence_min_abs_pct"]))
    opp_floor = abs(float(g["chip_opposite_min_pct"]))
    k = _num(kospi)
    parts = {"samsung": _num(samsung), "hynix": _num(hynix)}
    have = {n: v for n, v in parts.items() if v is not None and v != 0.0}
    missing = [n for n, v in parts.items() if v is None]
    out = {"state": CONFIRMATION_UNAVAILABLE, "agree": 0,
           "readable": len(have), "missing": missing, "detail": None,
           "gates": {"min_same_sign_abs_pct": same_floor,
                     "kospi_divergence_min_abs_pct": div_floor,
                     "chip_opposite_min_pct": opp_floor}}
    if k is None or k == 0.0:
        out["detail"] = ("KOSPI has no usable direction, so there is nothing "
                         "for the chip names to confirm.")
        return out
    if not have:
        out["detail"] = ("Neither Samsung Electronics nor SK Hynix could be "
                         "read, so KOSPI stands alone and unconfirmed.")
        return out
    agree = sum(1 for v in have.values() if (v > 0) == (k > 0))
    out["agree"] = agree
    way = "up" if k > 0 else "down"
    # Every one of the three big enough to have meant it.
    big_enough = (abs(k) >= same_floor
                  and all(abs(v) >= same_floor for v in have.values()))
    # A chip name that went materially the other way, with the index having
    # moved far enough for "the other way" to be a direction at all.
    against = [n for n, v in have.items()
               if (v > 0) != (k > 0) and abs(v) >= opp_floor]
    if agree == len(have) and len(have) == 2 and big_enough:
        out["state"] = CONFIRMATION_CONFIRMED
        out["detail"] = (f"KOSPI is {way} {abs(k):.2f}% and both Samsung "
                         f"Electronics and SK Hynix went the same way, each "
                         f"by at least {same_floor:g}%.")
    elif agree == len(have) and len(have) == 2:
        small = [f"{n} {have[n]:+.2f}%" for n in have
                 if abs(have[n]) < same_floor] or [f"KOSPI {k:+.2f}%"]
        out["state"] = CONFIRMATION_MIXED
        out["detail"] = (f"All three closed {way}, but not far enough to call "
                         f"it confirmation — {', '.join(small)} is inside the "
                         f"{same_floor:g}% floor, which is close enough to "
                         f"unchanged that the shared direction is not "
                         f"evidence of anything.")
    elif against and abs(k) >= div_floor:
        out["state"] = CONFIRMATION_DIVERGENCE
        out["detail"] = (f"KOSPI is {way} {abs(k):.2f}% but "
                         + " and ".join(f"{n} went {have[n]:+.2f}%"
                                        for n in against)
                         + f" — against the index by more than the "
                           f"{opp_floor:g}% that separates a disagreement "
                           f"from noise.")
    else:
        out["state"] = CONFIRMATION_MIXED
        reason = (f"KOSPI is {way}; {agree} of the {len(have)} readable chip "
                  f"name{'s' if len(have) != 1 else ''} agreed")
        if against and abs(k) < div_floor:
            reason += (f", and KOSPI's own {abs(k):.2f}% is under the "
                       f"{div_floor:g}% needed before disagreeing with it "
                       f"means anything")
        out["detail"] = reason + "."
    if missing:
        # Never CONFIRMED and never DIVERGENCE on one name: both of those
        # words claim something about a pair. And the reader is always told
        # which name is absent — an unread name is not a quiet one.
        if out["state"] in (CONFIRMATION_CONFIRMED, CONFIRMATION_DIVERGENCE):
            out["state"] = CONFIRMATION_MIXED
        out["detail"] += (" Read as partial: "
                          + " and ".join(missing) + " could not be read.")
    return out


COMPARISON_CONFIRMING = "CONFIRMING"
COMPARISON_DIVERGING = "DIVERGING"
COMPARISON_UNAVAILABLE = "UNAVAILABLE"


def premarket_comparison(implied_median_pct, actual_premarket_pct,
                         near_zero_pct: float = 0.15) -> dict:
    """Where the U.S. premarket sits against the gap history would suggest.

    Two numbers and their difference, and nothing more. In particular there
    is no "percentage already priced in": that ratio is meaningless when
    the two signs disagree — it would read as a large negative percentage
    of a move in the other direction — and it explodes when the expected
    value sits near zero, where a rounding difference becomes a division by
    almost nothing. Both cases are refused by name rather than rendered.

    The residual is a DIFFERENCE IN PERCENTAGE POINTS, not a prediction.
    A premarket that has moved less than history suggests may be about to
    move further, or history may simply not apply this morning; nothing in
    this function can tell the two apart.
    """
    exp = _num(implied_median_pct)
    act = _num(actual_premarket_pct)
    out = {"implied_pct": exp, "actual_pct": act, "residual_pct": None,
           "state": COMPARISON_UNAVAILABLE, "share_shown": False,
           "share_pct": None, "detail": None}
    if exp is None or act is None:
        out["detail"] = ("Needs both a matched historical gap and a live "
                         "premarket quote; one of them is missing.")
        return out
    out["residual_pct"] = round(act - exp, 3)
    if exp == 0.0 or act == 0.0:
        out["detail"] = ("One side is exactly flat, which has no direction to "
                         "confirm or diverge from.")
        return out
    if (exp > 0) == (act > 0):
        out["state"] = COMPARISON_CONFIRMING
        out["detail"] = ("The U.S. premarket is moving the same way the "
                         "matched Korean sessions moved this stock's open.")
        if abs(exp) >= near_zero_pct:
            out["share_shown"] = True
            out["share_pct"] = round(act / exp * 100.0, 1)
        else:
            out["detail"] += (" The share already covered is not shown: the "
                              "matched historical gap is too close to zero "
                              "for a ratio to mean anything.")
    else:
        out["state"] = COMPARISON_DIVERGING
        out["detail"] = ("The U.S. premarket is moving AGAINST the direction "
                         "the matched Korean sessions moved this stock's "
                         "open. No share-covered figure is shown, because a "
                         "share of a move in the other direction is not a "
                         "quantity.")
    return out


EDGE_NONE = "NO EDGE"
EDGE_WEAK = "WEAK HISTORICAL EDGE"
EDGE_MODERATE = "MODERATE"
EDGE_STRONG = "STRONG"
EDGE_UNAVAILABLE = "NOT MEASURED"


# Where one description of strength stops and the next begins. These are
# starting rules, not findings: they say how conservative the wording is,
# and every one of them is overridable from thresholds.json. Nothing in the
# app trades on them — they choose an adjective.
EDGE_GATES = {
    "min_n": 30,              # below this, strength is not described at all
    "floor_corr": 0.10,       # below this, the wording never rises above WEAK
    "none_corr": 0.05,        # below this it is NO EDGE rather than weak
    "moderate_corr": 0.18, "moderate_lo_pct": 53.0,
    "strong_corr": 0.30, "strong_lo_pct": 57.0,
}


def edge_strength(stats: dict, gates: dict | None = None) -> dict:
    """How much the evidence actually supports, for ONE measurement.

    Applied to the opening gap it describes the Korea relationship; applied
    to the open-to-close move it describes what is left after 9:30, and on
    the evidence so far those are very different answers. That is exactly
    why the two are never collapsed into one bullish-or-bearish word.

    The gates are the conservative ones the rest of this app uses: the
    LOWER end of the same-direction interval has to clear a coin flip
    before anything above WEAK is said, and both correlations have to
    agree. A rank correlation that disagrees with the linear one usually
    means a handful of outliers are carrying the result.
    """
    g = dict(EDGE_GATES)
    g.update(gates or {})
    min_n = int(g["min_n"])
    out = {"state": EDGE_UNAVAILABLE, "detail": None,
           "n": (stats or {}).get("n", 0)}
    if not stats or not stats.get("n") or stats["n"] < min_n:
        out["detail"] = (f"Fewer than {min_n} matched sessions — not enough to "
                         f"describe the strength of anything.")
        return out
    sd = stats.get("same_direction") or {}
    lo = sd.get("lo_pct")
    p, s = stats.get("pearson"), stats.get("spearman")
    if lo is None or p is None or s is None:
        out["detail"] = "The measurement did not produce a usable statistic."
        return out
    agree = (p > 0) == (s > 0)
    strength = min(abs(p), abs(s)) if agree else 0.0
    if lo <= 50.0 or strength < g["floor_corr"]:
        out["state"] = EDGE_NONE if strength < g["none_corr"] else EDGE_WEAK
        out["detail"] = (f"Same-direction rate {sd.get('rate_pct')}% over "
                         f"{sd.get('n')} sessions, but the conservative end of "
                         f"that range is {lo}% — a coin flip is inside it.")
        return out
    if strength >= g["strong_corr"] and lo >= g["strong_lo_pct"]:
        out["state"] = EDGE_STRONG
    elif strength >= g["moderate_corr"] and lo >= g["moderate_lo_pct"]:
        out["state"] = EDGE_MODERATE
    else:
        out["state"] = EDGE_WEAK
    out["detail"] = (f"Same-direction rate {sd.get('rate_pct')}% over "
                     f"{sd.get('n')} sessions, conservative end {lo}%; "
                     f"correlation {p} linear and {s} by rank.")
    return out


BIAS_UP = "UP"
BIAS_DOWN = "DOWN"
BIAS_MIXED = "MIXED"
BIAS_INCONCLUSIVE = "INCONCLUSIVE"
BIAS_UNSTABLE = "RELATIONSHIP UNSTABLE"
BIAS_NONE = "NO DATA"

# What the matched history must show before a direction is named. Overridden
# from the settings file; unvalidated starting points, not findings.
BIAS_GATES = {
    "min_n": 30,               # matched sessions before a direction is named
    "wilson_lower_min": 0.50,  # the conservative end must clear a coin flip
}


def opening_gap_bias(today_korea_pct, implied: dict, gates: dict | None = None,
                     relationship_unstable: bool = False,
                     relationship_detail: str | None = None) -> dict:
    """Which way the matched history leans for this morning's open.

    FOUR WAYS OF NOT HAVING AN ANSWER, AND THEY ARE NOT THE SAME THING.

      NO DATA               there is no matched history to read at all.
      INCONCLUSIVE          there is history, and it cannot establish a
                            direction — too few sessions, or an interval
                            that contains a coin flip.
      MIXED                 the evidence establishes a direction by
                            counting and then contradicts it by magnitude:
                            the sessions leaned one way and their median
                            gap went the other. Both facts are real; they
                            disagree.
      RELATIONSHIP UNSTABLE the recent and long-run relationship disagree
                            about which way this pair even runs. The
                            matched history may look perfectly decisive and
                            is describing a regime that has since changed.

    Collapsing INCONCLUSIVE into UNSTABLE — or either into MIXED — would
    tell a reader that the relationship has broken when in fact nothing is
    known yet, or that nothing is known when in fact something has broken.
    Those call for opposite responses, so they get different words.

    THE DIRECTION COMES FROM THE MATCHED SESSIONS, NOT FROM KOREA'S SIGN.
    A ticker whose matched sessions reliably opened the OTHER way from
    Korea is a real finding, not noise, so the test is whether the interval
    around the match rate EXCLUDES a coin flip — on either side. A rate
    whose honest range sits entirely below 50% says just as much as one
    entirely above it; only a range that straddles 50% says nothing.
    """
    g = dict(BIAS_GATES)
    g.update(gates or {})
    min_n = int(g["min_n"])
    lower_min = float(g["wilson_lower_min"]) * 100.0
    out = {"state": BIAS_NONE, "detail": None, "n": 0,
           "gates": {"min_n": min_n, "wilson_lower_min": lower_min / 100.0}}
    # An unstable relationship outranks every other reading. The matched
    # sessions below may be perfectly decisive about a relationship that no
    # longer holds, and a confident label drawn from them would be the most
    # dangerous thing on the panel.
    if relationship_unstable:
        out["state"] = BIAS_UNSTABLE
        out["detail"] = (relationship_detail
                         or ("The recent and long-run windows disagree about "
                             "which way this relationship runs, so no "
                             "direction is named from it."))
        return out
    if not implied or not implied.get("usable"):
        # A bucket that exists but is too thin is a different answer from a
        # bucket that does not exist.
        if implied and implied.get("n"):
            out["state"] = BIAS_INCONCLUSIVE
            out["n"] = int(implied["n"])
        out["detail"] = (implied or {}).get("reason") or \
            "No matched history for today's Korean move."
        return out
    sd = implied.get("same_direction") or {}
    med = (implied.get("distribution") or {}).get("median_pct")
    k = _num(today_korea_pct)
    n = int(sd.get("n") or implied.get("n") or 0)
    out["n"] = n
    if med is None or k is None or sd.get("lo_pct") is None \
            or sd.get("hi_pct") is None:
        out["state"] = BIAS_INCONCLUSIVE
        out["detail"] = "The matched sessions did not produce a usable median."
        return out
    if n < min_n:
        out["state"] = BIAS_INCONCLUSIVE
        out["detail"] = (f"{n} matched session{'' if n == 1 else 's'} — fewer "
                         f"than the {min_n} this panel requires before it "
                         f"names a direction. What they did is shown below; "
                         f"it is not called a lean.")
        return out
    with_korea = sd["lo_pct"] > lower_min
    against_korea = sd["hi_pct"] < (100.0 - lower_min)
    if not (with_korea or against_korea):
        out["state"] = BIAS_INCONCLUSIVE
        out["detail"] = (f"{sd.get('rate_pct')}% of {n} matched sessions "
                         f"opened the same way as Korea, but the honest range "
                         f"around that is {sd['lo_pct']}% to {sd['hi_pct']}% "
                         f"— a coin flip is inside it, so the evidence cannot "
                         f"establish a direction. That is not the same as the "
                         f"relationship having broken.")
        return out
    # The count says one thing. Does the SIZE of what followed agree? If the
    # sessions leaned with Korea, the median gap should lean Korea's way; if
    # they leaned against, it should lean the other way. When the two
    # disagree the honest word is MIXED — one direction established by
    # counting, contradicted by magnitude.
    implied_up = (k > 0) if with_korea else (k < 0)
    if med == 0 or ((med > 0) != implied_up):
        out["state"] = BIAS_MIXED
        counted = ("the same way Korea did" if with_korea
                   else "against the Korean move")
        out["detail"] = (
            f"Of {n} matched sessions {sd.get('rate_pct')}% opened "
            f"{counted}, which points {'higher' if implied_up else 'lower'} "
            f"— but their median gap was {med:+.2f}%, which points the other "
            f"way. The count and the size disagree, so no direction is named.")
        return out
    out["state"] = BIAS_UP if med > 0 else BIAS_DOWN
    way = "higher" if med > 0 else "lower"
    if with_korea:
        how = (f"{sd.get('rate_pct')}% of them went the same way Korea did")
    else:
        how = (f"only {sd.get('rate_pct')}% of them went the same way Korea "
               f"did — this ticker's matched sessions opened AGAINST the "
               f"Korean move, consistently enough that the honest range "
               f"stays below a coin flip")
    out["detail"] = (f"The {n} sessions that matched today's Korean "
                     f"move opened {way} at the median ({med:+.2f}%), and "
                     f"{how}.")
    return out


# ── how unusual, and how fresh ──────────────────────────────────────────────

UNUSUAL_NORMAL = "NORMAL"
UNUSUAL_UNUSUAL = "UNUSUAL"
UNUSUAL_EXTREME = "EXTREME"
UNUSUAL_UNKNOWN = "NOT MEASURED"

UNUSUAL_GATES = {"unusual_percentile": 90.0, "extreme_percentile": 97.0}


def unusual_state(percentile, gates: dict | None = None) -> dict:
    """How far into its own trailing distribution today's move sits.

    A percentile rather than a fixed percentage, because a fixed level ages
    in both directions: "KOSPI above one and a half percent is a big day"
    fires every week in a violent year and never once in a calm one. The
    percentile carries the volatility regime with it for free.

    The state is never returned alone — the percentile that produced it,
    the size of the sample it was ranked against and the lookback are all
    returned beside it, so a reader can disagree with the word by looking
    at the number.
    """
    g = dict(UNUSUAL_GATES)
    g.update(gates or {})
    p = _num(percentile)
    unusual_at = float(g["unusual_percentile"])
    extreme_at = float(g["extreme_percentile"])
    out = {"state": UNUSUAL_UNKNOWN, "percentile": p,
           "unusual_at": unusual_at, "extreme_at": extreme_at}
    if p is None:
        return out
    if p >= extreme_at:
        out["state"] = UNUSUAL_EXTREME
    elif p >= unusual_at:
        out["state"] = UNUSUAL_UNUSUAL
    else:
        out["state"] = UNUSUAL_NORMAL
    return out


FRESH_CURRENT = "CURRENT FOR SOURCE"
FRESH_DELAYED = "DELAYED"
FRESH_STALE = "STALE"
FRESH_UNAVAILABLE = "UNAVAILABLE"
FRESH_UNKNOWN = "AGE UNKNOWN"
FRESH_SETTLED = "SETTLED CLOSE"


def quote_freshness(age_seconds, current_max_s: float, delayed_max_s: float,
                    have_value: bool = True, settled: bool = False) -> dict:
    """How old a reading is, and whether it may be used to describe now.

    CURRENT FOR SOURCE is the strongest thing this can say, and it is
    deliberately weaker than it sounds. The Korean series here come from a
    delayed feed; a print from it that is two minutes old is the freshest
    thing this app can honestly hold, and it is still not the exchange's
    current price. Calling it "real time" would be describing a provider we
    do not have.

    AGE UNKNOWN is a separate state from STALE on purpose. A reading with
    no timestamp might be a second old or a day old, and the two call for
    the same caution but not the same sentence — "we cannot tell how old
    this is" is a statement about the provider, not about the market.
    """
    out = {"state": FRESH_UNAVAILABLE, "age_s": None, "age_min": None,
           "fresh_enough": False, "detail": None,
           "current_max_s": float(current_max_s),
           "delayed_max_s": float(delayed_max_s)}
    if not have_value:
        out["detail"] = "There is no reading to age."
        return out
    if settled:
        # A settled closing price is not stale at nine hours old; it is
        # finished. Ageing it against a twenty-minute limit would paint
        # every Korean close red by the time New York wakes up, which would
        # teach the reader to ignore the freshness row on the one morning it
        # matters.
        out.update({"state": FRESH_SETTLED, "fresh_enough": True,
                    "detail": ("The Korean session is settled, so this is a "
                               "final closing price rather than a live "
                               "reading. Its age is not a defect.")})
        a = _num(age_seconds)
        if a is not None:
            out["age_s"] = round(max(0.0, a), 1)
            out["age_min"] = round(max(0.0, a) / 60.0, 1)
        return out
    age = _num(age_seconds)
    if age is None:
        out["state"] = FRESH_UNKNOWN
        out["detail"] = ("The provider did not say when this reading was "
                         "taken, so its age cannot be checked. It is treated "
                         "as unverified rather than as current.")
        return out
    age = max(0.0, age)
    out["age_s"] = round(age, 1)
    out["age_min"] = round(age / 60.0, 1)
    if age <= float(current_max_s):
        out["state"] = FRESH_CURRENT
        out["fresh_enough"] = True
        out["detail"] = (f"{out['age_min']:g} minutes old — current for this "
                         f"source, which is a delayed feed and not the "
                         f"exchange's live price.")
    elif age <= float(delayed_max_s):
        out["state"] = FRESH_DELAYED
        out["detail"] = (f"{out['age_min']:g} minutes old — visibly behind, "
                         f"still usable as context.")
    else:
        out["state"] = FRESH_STALE
        out["detail"] = (f"{out['age_min']:g} minutes old — too old to "
                         f"describe the current session.")
    return out


def study(obs, signal: str = "korea", gates: dict | None = None) -> dict:
    """The full measured picture for one signal against one target: all
    three U.S. measurements, the conditional buckets, and how strong the
    opening-gap and after-open evidence actually is.

    The two edges are computed by the same function from the same kind of
    evidence, which is the point: whatever separates them is the data, not
    a different standard applied to each.
    """
    per = {m: measure_stats(obs, m, signal) for m in MEASURES}
    return {
        "n": len(obs or []),
        "first_date": obs[0].get("date") if obs else None,
        "last_date": obs[-1].get("date") if obs else None,
        "measures": per,
        "buckets": bucket_rows(obs, "opening_gap", signal),
        "opening_gap_edge": edge_strength(per["opening_gap"], gates),
        "after_open_edge": edge_strength(per["open_to_close"], gates),
    }


# ── the clock says what should be happening; the data says what is ──────────

SCHED_BEFORE = "BEFORE OPEN"
SCHED_LIVE = "SESSION IN PROGRESS"
SCHED_AFTER = "AFTER NORMAL CLOSE"
SCHED_NON_TRADING = "NOT A TRADING DAY"

DATA_UPDATING = "STILL UPDATING"
DATA_SETTLED = "SETTLED"
DATA_NO_SESSION = "NO KOREA SESSION TODAY"
DATA_UNKNOWN = "NOT ESTABLISHED"

FINALITY_GATES = {"quiet_minutes": 15, "fallback_final_kst": "18:30"}


def _hhmm_minutes(hhmm) -> int | None:
    try:
        h, _, m = str(hhmm).partition(":")
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def session_finality(scheduled_state: str, seoul_date: str, seoul_hhmm: str,
                     bar_date: str | None, steady_minutes=None,
                     readings: int = 0, observed_change: bool = False,
                     gates: dict | None = None) -> dict:
    """Is the Korean session actually over, or is that only what the clock
    thinks?

    WHY THE CLOCK IS NOT ENOUGH. Korea moves its trading day. The clearest
    case is the annual College Scholastic Ability Test, when the exchange
    opens and closes an hour later so the country's commute is quieter
    during the listening section — and there are others. No exam-day
    calendar ships with this app, and one should not: a hardcoded calendar
    is silently wrong the first year nobody updates it, and it would be
    wrong in the most dangerous direction, declaring a still-moving market
    final.

    WHY PROVIDER SCHEDULE METADATA IS NOT ENOUGH EITHER. It was checked.
    The chart endpoint carrying these series exposes no market-state field
    at all, and the trading-period block it does expose reports the KRX
    regular session as ending at 15:00 Seoul — which disagrees with the
    15:30 that the closing single-price auction actually settles at. Two
    sources disagree about the schedule and neither of them is the market.

    SO THE EVIDENCE IS THE DATA ITSELF. A session is FINAL when its value
    has stopped moving: observed unchanged across at least two readings for
    at least `quiet_minutes`. While Korean values are still advancing the
    session is PRELIMINARY no matter what time it is — 15:31 on an exam day
    is the middle of the session.

    "STILL MOVING" AND "NOT WATCHED LONG ENOUGH" ARE DIFFERENT ANSWERS, and
    conflating them was a bug in the first draft of this function. Having
    seen a value change three minutes ago is evidence that the market is
    open. Having first seen a value three minutes ago is evidence of
    nothing at all — the value may have been sitting there since lunchtime.
    Both block finality, and they say so in different words: the first is
    STILL UPDATING, the second is NOT ESTABLISHED.

    THE DOCUMENTED FALLBACK. Two readings may never exist: the app can be
    restarted, or simply not asked, for hours, and then there is nothing to
    compare. After `fallback_final_kst` a session whose bar exists is
    accepted as final without observed quiet. That time sits deliberately
    late — past the regular close, past the end of the provider's
    post-session window, and hours before the pre-open snapshot that
    depends on it — so its only cost is that a genuinely irregular session
    stays preliminary a little longer. Preferring PRELIMINARY is the whole
    design: a changing market called final is a wrong number presented as a
    settled one.
    """
    g = dict(FINALITY_GATES)
    g.update(gates or {})
    quiet_min = float(g["quiet_minutes"])
    cutoff = _hhmm_minutes(g["fallback_final_kst"])
    now_min = _hhmm_minutes(seoul_hhmm)
    steady = _num(steady_minutes)
    out = {"scheduled_state": scheduled_state, "data_state": DATA_UNKNOWN,
           "final": False, "seoul_date": seoul_date, "bar_date": bar_date,
           "steady_minutes": None if steady is None else round(steady, 1),
           "observed_change": bool(observed_change),
           "readings": int(readings or 0), "by_fallback": False,
           "quiet_minutes": quiet_min,
           "fallback_final_kst": g["fallback_final_kst"], "reason": None}
    past_cutoff = (cutoff is not None and now_min is not None
                   and now_min >= cutoff)

    if bar_date != seoul_date:
        # No bar carrying today's Seoul date. Before the close that is
        # simply the session not having printed one yet; after it, on a day
        # the exchange never opened, it is the answer.
        if scheduled_state == SCHED_NON_TRADING:
            out["data_state"] = DATA_NO_SESSION
            out["reason"] = ("Seoul is closed for the weekend, so there is no "
                             "session today to finalise.")
        elif scheduled_state == SCHED_AFTER or past_cutoff:
            out["data_state"] = DATA_NO_SESSION
            out["reason"] = ("The normal Korean close has passed and no bar "
                             "carries today's Seoul date. Either Korea did "
                             "not trade today — a public holiday, which this "
                             "app does not keep a calendar of — or the data "
                             "has not arrived. Neither is a finished "
                             "session, and neither is treated as one.")
        else:
            out["data_state"] = DATA_UNKNOWN
            out["reason"] = ("Today's Korean session has not printed a bar "
                             "yet.")
        return out

    if scheduled_state == SCHED_LIVE:
        out["data_state"] = DATA_UPDATING
        out["reason"] = ("Seoul is inside its normal trading hours, so "
                         "today's number is still moving.")
        return out

    if observed_change and steady is not None and steady < quiet_min:
        # This is the case the whole function exists for: we WATCHED the
        # value move after the hour it should have stopped. The clock says
        # the session is over and the market disagrees, and the market wins.
        out["data_state"] = DATA_UPDATING
        out["reason"] = (
            f"The normal Korean close has passed, but this session's value "
            f"was seen changing {steady:.0f} minute"
            f"{'' if round(steady) == 1 else 's'} ago — inside the "
            f"{quiet_min:g}-minute quiet period. Korea is still trading, so "
            f"the session is not final however late it runs. Korea "
            f"occasionally moves its trading day; the clock is not allowed "
            f"to overrule the data.")
        return out

    if readings >= 2 and steady is not None and steady >= quiet_min:
        out["data_state"] = DATA_SETTLED
        out["final"] = True
        out["reason"] = (f"Unchanged across {int(readings)} readings over "
                         f"{steady:.0f} minutes past the normal close — the "
                         f"market settled it, not the clock.")
        return out

    if past_cutoff:
        out["data_state"] = DATA_SETTLED
        out["final"] = True
        out["by_fallback"] = True
        out["reason"] = (
            f"Past {g['fallback_final_kst']} Seoul with today's bar on file. "
            f"There were not two readings to compare — the app may have been "
            f"restarted, or simply not asked — so this is the documented "
            f"conservative fallback rather than observed quiet.")
        return out

    out["data_state"] = DATA_UNKNOWN
    if steady is not None and steady < quiet_min:
        out["reason"] = (
            f"Today's bar is on file and the normal close has passed, but "
            f"this value has only been watched for {steady:.0f} minute"
            f"{'' if round(steady) == 1 else 's'} of the {quiet_min:g} "
            f"required. It has not been seen moving — it has not been "
            f"watched long enough for standing still to mean anything.")
    else:
        out["reason"] = ("Today's bar is on file and the normal close has "
                         "passed, but this is the only reading of it so far. "
                         "One reading cannot show whether the value is still "
                         "moving, so the session stays preliminary until a "
                         "second one does.")
    return out


# ── the self-check that stands between the data and a confident label ───────

def self_check(checks: list) -> dict:
    """Every condition that must hold before Korea Lead is allowed to sound
    confident, evaluated together and reported by name.

    The failure mode this exists to prevent is a panel that keeps its
    confident wording while one of its inputs quietly stops being true —
    a stale quote, a Korean series a session behind, a bucket that thinned
    out. Each check is a plain sentence, and the ones that failed are the
    reason the output was degraded.
    """
    rows = []
    for c in (checks or []):
        rows.append({"name": c.get("name"), "ok": bool(c.get("ok")),
                     "detail": c.get("detail"),
                     "blocking": bool(c.get("blocking", True))})
    failed = [r for r in rows if not r["ok"]]
    blocking = [r for r in failed if r["blocking"]]
    return {"checks": rows, "n": len(rows), "passed": len(rows) - len(failed),
            "failed": [r["name"] for r in failed],
            "blocking_failures": [r["name"] for r in blocking],
            "ok": not blocking,
            "detail": (None if not blocking else
                       "Degraded because " + "; ".join(
                           r["detail"] or r["name"] for r in blocking))}
