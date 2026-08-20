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


CONFIRMATION_STRONG = "STRONG"
CONFIRMATION_MIXED = "MIXED"
CONFIRMATION_DIVERGENCE = "DIVERGENCE"
CONFIRMATION_UNAVAILABLE = "UNAVAILABLE"


def chip_confirmation(kospi, samsung, hynix) -> dict:
    """Did Korea's two memory names go the way the index went?

    Descriptive only. There is no weighting and no score: this counts how
    many of the two chip names agree in SIGN with KOSPI and says so. KOSPI
    is a broad index and the stocks traded here are not, so a session where
    the index rose while Samsung and SK Hynix fell is worth seeing plainly
    rather than being averaged into a single number that reads as mild.

    A missing name is missing. It is never counted as agreement and never
    assumed flat — with only one chip name readable the answer is at best
    a partial one, and it says which name it is missing.
    """
    k = _num(kospi)
    parts = {"samsung": _num(samsung), "hynix": _num(hynix)}
    have = {n: v for n, v in parts.items() if v is not None and v != 0.0}
    missing = [n for n, v in parts.items() if v is None]
    out = {"state": CONFIRMATION_UNAVAILABLE, "agree": 0,
           "readable": len(have), "missing": missing, "detail": None}
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
    if agree == len(have) and len(have) == 2:
        out["state"] = CONFIRMATION_STRONG
        out["detail"] = (f"KOSPI is {way} and both Samsung Electronics and "
                         f"SK Hynix went the same way.")
    elif agree == 0:
        out["state"] = CONFIRMATION_DIVERGENCE
        out["detail"] = (f"KOSPI is {way} but "
                         + ("neither chip name" if len(have) == 2
                            else "the one readable chip name")
                         + " went with it.")
    else:
        out["state"] = CONFIRMATION_MIXED
        out["detail"] = (f"KOSPI is {way}; {agree} of the {len(have)} readable "
                         f"chip name{'s' if len(have) != 1 else ''} agreed.")
    if missing:
        # Never STRONG and never DIVERGENCE on one name: both of those words
        # claim something about a pair. And the reader is always told which
        # name is absent — an unread name is not a quiet one.
        if out["state"] in (CONFIRMATION_STRONG, CONFIRMATION_DIVERGENCE):
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
BIAS_NONE = "NO DATA"


def opening_gap_bias(today_korea_pct, implied: dict) -> dict:
    """Which way the matched history leans for this morning's open.

    The direction comes from the matched sessions, not from Korea's sign.
    A ticker whose matched sessions reliably opened the OTHER way from
    Korea is a real finding, not noise, so the test is whether the interval
    around the match rate EXCLUDES a coin flip — on either side. A rate
    whose honest range sits entirely below 50% says just as much as one
    entirely above it; only a range that straddles 50% says nothing.

    MIXED is a real answer and is returned whenever the range does straddle
    it, however flattering the point estimate looks.
    """
    out = {"state": BIAS_NONE, "detail": None}
    if not implied or not implied.get("usable"):
        out["detail"] = (implied or {}).get("reason") or \
            "No matched history for today's Korean move."
        return out
    sd = implied.get("same_direction") or {}
    med = (implied.get("distribution") or {}).get("median_pct")
    k = _num(today_korea_pct)
    if med is None or k is None or sd.get("lo_pct") is None \
            or sd.get("hi_pct") is None:
        out["detail"] = "The matched sessions did not produce a usable median."
        return out
    with_korea = sd["lo_pct"] > 50.0
    against_korea = sd["hi_pct"] < 50.0
    if not (with_korea or against_korea):
        out["state"] = BIAS_MIXED
        out["detail"] = (f"{sd.get('rate_pct')}% of {sd.get('n')} matched "
                         f"sessions opened the same way as Korea, but the "
                         f"honest range around that is {sd['lo_pct']}% to "
                         f"{sd['hi_pct']}% — a coin flip is inside it, so "
                         f"there is no lean to report.")
        return out
    out["state"] = BIAS_UP if med > 0 else (BIAS_DOWN if med < 0 else BIAS_MIXED)
    way = "higher" if med > 0 else "lower"
    if with_korea:
        how = (f"{sd.get('rate_pct')}% of them went the same way Korea did")
    else:
        how = (f"only {sd.get('rate_pct')}% of them went the same way Korea "
               f"did — this ticker's matched sessions opened AGAINST the "
               f"Korean move, consistently enough that the honest range "
               f"stays below a coin flip")
    out["detail"] = (f"The {sd.get('n')} sessions that matched today's Korean "
                     f"move opened {way} at the median ({med:+.2f}%), and "
                     f"{how}.")
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
