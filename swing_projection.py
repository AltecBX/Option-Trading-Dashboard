"""swing_projection.py — where does this stock historically reverse from
HERE, how soon, is it already reacting, and what usually happens afterward?

swings.py already answers "how far does this stock normally run?" — the
unconditional rhythm. This module answers the harder, conditional question
a trader at the hard right edge actually has:

    Given that the current swing has ALREADY travelled X%, where have
    swings that travelled at least X% historically ended, how many more
    days did they take from this exact point, has this one already
    reached that area and started reacting, and what did the opposite
    swing that followed look like?

THE COHORT IS A SURVIVAL POPULATION, NOT A SIMILARITY WINDOW

  The obvious cohort — "swings of roughly the same size as this one" — is
  statistically wrong, and wrong in the dangerous direction. A ±window
  around −20% excludes the declines that went on to −40%, so "typical
  remaining downside" computed from it is biased toward zero exactly when
  the knife is still falling. The correct population for "given we are
  already −18%" is every completed same-direction swing whose extreme
  reached AT LEAST −18%. Zone, remaining move, remaining days and the
  reversed-within-another-X% rates all come from that one population, so
  no two numbers on the panel can quietly disagree about what "comparable"
  means.

THE ONLY FLOOR IS THE ZIGZAG ITSELF (v4.51)

  The display tables hide swings under min_move_pct (15% by default) —
  that is a reading convenience, and it must never define a statistical
  population. It used to: a 13% decline was excluded from the cohort of a
  12% decline, which deleted precisely the swings that ENDED shallow and so
  pushed the zone deeper — overstating how much adverse move was still to
  come, in the one situation where that error is expensive. The cohort now
  uses every completed same-direction leg regardless of the display filter.

  There is still one floor, and it is structural rather than chosen: a
  zigzag only turns after a counter-move of `sensitivity`, so no completed
  leg is smaller than the sensitivity setting. At Standard (12%) nothing
  under 12% exists to include; at Sensitive (8%) the old filter was
  throwing away the entire 8–15% band. The payload states this floor in
  words instead of implying a completeness the data cannot have.

CONDITION ON THE EXTREME, MEASURE FROM THE PRICE

  A stock that touched −22% and bounced to −18% HAS reached −22%; the
  candidate bottom is the extreme, not today's close. So cohort membership
  conditions on the running extreme of the current swing, while every
  distance shown to the user is measured from the CURRENT price — the
  number a trade would actually be entered at. The reversal STATUS below
  is derived from the extreme for the same reason: a stock whose low
  entered its historical bottom zone and has since risen 6% is a different
  situation from one still falling toward that zone, and the panel has to
  be able to tell them apart.

ONE DIRECTION OF TIME

  Every historical quantity at a swing's "crossing" — the first bar where
  that swing had travelled as far as the current one has — uses only bars
  at or before that crossing. Remaining days are each swing's own
  crossing→end count, never a difference of medians (which can go
  negative). The in-progress final leg is never part of any statistic.

PROBABILITIES ARE FIXED-HORIZON OR THEY ARE NOT COMPARABLE (v4.51)

  "How often did the follow-on swing exceed X%?" cannot be compared with
  "how often does any random day see X% within N days", because the swing
  population has no fixed horizon and — worse — a confirmed swing is at
  least the zigzag sensitivity BY DEFINITION, so any level under it scores
  100% and means nothing. Every probability on the panel is now
  "X% within N trading days", measured from the close of the reference
  bar, against a baseline computed with the SAME X and the SAME N from
  ordinary days. The completed-swing distribution survives where it is
  honest: median size, median duration, and the paired target band.

PAIRS, NOT PRODUCTS (v4.51)

  The follow-on target is no longer median-reversal × median-rebound. That
  product breaks the historical relationship between how deep a decline
  went and how big the bounce off it was. Each cohort episode contributes
  its OWN (final depth, next move) pair, projected from today's swing
  origin, and the target band is the distribution of those projections.

Pure math: stdlib only, plus wilson_interval from metrics.py (the same
interval the rest of the app uses — never reimplemented). No I/O, no
network, no clock. swings.py owns data and merges the result into its
payload under "reversal".
"""
from __future__ import annotations

from typing import Any

from metrics import wilson_interval

ENGINE_VERSION = "swing-projection-1.1.0"

# Judgment defaults. The route overlays the `swing_projection` section of
# thresholds.json over these — every number below is a starting hypothesis
# about wording and sufficiency, not a finding.
DEFAULTS = {
    # Below this many cohort members the projection refuses to sound sure:
    # zones render with an explicit thin-sample warning, and conditional
    # filters (regime) are not applied at all.
    "min_cohort_n": 6,
    # "Reversed within another X%" ladder, in percentage points of
    # additional adverse move past the crossing.
    "more_move_ladder": (3.0, 5.0, 10.0),
    # Fixed-horizon excursion probabilities for the follow-on move, as
    # (percent, trading days). Both the conditional rate (measured from the
    # historical reversal pivots) and the baseline (measured from ordinary
    # days) use the same pair, which is the only way the two numbers can be
    # subtracted from each other honestly.
    "touch_horizons": ((5.0, 5), (10.0, 10), (15.0, 20)),
    # Earnings contamination: a report within this many trading days of the
    # reversal pivot tags the episode.
    "earnings_near_days": 2,
    # Clean episodes required before contaminated ones are EXCLUDED from a
    # statistic rather than merely disclosed alongside it.
    "min_clean_n": 6,
    # A single close-to-close day move beyond this is not a price; it is an
    # unadjusted corporate action or bad data. The leg containing it is
    # excluded and counted. Same convention as the rest of the app.
    "max_credible_day_move_pct": 50.0,
    # Where a developing move stops being ordinary. Both numbers are shares
    # of the stock's own unconditional median swing, and both come from the
    # staged walk-forward run rather than from taste: below 100% the
    # conditional cohort is nearly the whole history and measurably adds
    # nothing over the plain rhythm; at 100% it starts to pay; past 125% the
    # unconditional band covers only a quarter of outcomes while the
    # conditional one still covers a third.
    "maturity_normal_pct": 100.0,
    "maturity_beyond_pct": 125.0,
    # ── how big a move counts as a swing, per stock (v4.54) ────────────
    # A single percentage threshold cannot mean the same thing on Coca-Cola
    # and on Coinbase. At a fixed 12% the watchlist segmented into anything
    # from 2.6 swings a year (KO) to 38 (COIN), and a low-volatility name
    # could end up with a single "down leg" spanning eighteen months and a
    # cohort of ONE comparable episode. The threshold is therefore scaled to
    # the stock's own travel: the median absolute `zigzag_window`-day move,
    # times k, clamped. Solving per symbol for a comparable number of swings
    # a year gives a threshold/travel ratio of 2.48 with a 0.987 correlation
    # across 22 symbols — hence k = 2.5.
    "adaptive_zigzag": True,
    "zigzag_k": 2.5,
    "zigzag_floor_pct": 6.0,
    "zigzag_ceiling_pct": 18.0,
    "zigzag_window": 20,
    # The three sensitivity settings are multipliers on k (and on the clamp),
    # so "Standard" means the same thing about a stock rather than the same
    # number on every stock.
    "sensitivity_multipliers": {"sensitive": 0.68, "standard": 1.0,
                                "major": 1.36},
    # The tables and the chart lines hide swings under this multiple of the
    # threshold. Today's defaults are 12% and 15%, a ratio of 1.25, so this
    # generalises what the app already does instead of inventing a rule: on
    # Coca-Cola a fixed 15% filter hid 28 of its 33 completed declines, which
    # is why a low-volatility chart could look like it had no down legs at
    # all while the projection was using them.
    "display_filter_ratio": 1.25,
    # Context windows.
    "range_window": 20,
    "regime_sma_n": 200,
    "atr_n": 14,
    # Whether the cohort is additionally filtered to swings whose crossing
    # happened in the SAME trend regime as today. OFF: walk-forward testing
    # rejected it (and range position, and velocity). The payload always
    # discloses whether it was applied.
    "regime_filter": False,
}

# Accepted-but-retired keys: present in older thresholds.json copies. They
# are read without error and ignored, so a stale config never breaks a card.
_RETIRED = ("next_touch_ladder", "min_clean_next_n")


def _cfg(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if v is None:
            continue
        if k in out:
            out[k] = v
        elif k == "min_clean_next_n":          # old name for min_clean_n
            out["min_clean_n"] = v
    return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _median(vals):
    xs = sorted(v for v in (_num(v) for v in (vals or [])) if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _quantile(vals, q: float):
    xs = sorted(v for v in (_num(v) for v in (vals or [])) if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    i = (len(xs) - 1) * max(0.0, min(1.0, q))
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def _plural(n, one, many):
    return one if n == 1 else many


# ── point-in-time context ───────────────────────────────────────────────────

def range_position(highs, lows, close_val, i, window: int = 20):
    """Where a price sits inside the trailing `window`-bar high/low range at
    bar i, as 0–100. Uses only bars ≤ i — at a historical bar this is what
    a reader could have computed that evening."""
    if i + 1 < window:
        return None
    hi = max(highs[i - window + 1:i + 1])
    lo = min(lows[i - window + 1:i + 1])
    c = _num(close_val)
    if c is None or hi <= lo:
        return None
    return max(0.0, min(100.0, (c - lo) / (hi - lo) * 100.0))


def sma_at(closes, i, n):
    if i + 1 < n:
        return None
    seg = closes[i - n + 1:i + 1]
    return sum(seg) / n


def regime_at(closes, i, n: int = 200) -> str:
    """Deliberately crude and auditable: above the 200-day average is an
    uptrend, below is a downtrend, not enough history is unknown. Context
    only — walk-forward testing rejected it as a cohort filter."""
    m = sma_at(closes, i, n)
    if m is None:
        return "unknown"
    return "uptrend" if closes[i] >= m else "downtrend"


def atr_at(highs, lows, closes, i, n: int = 14):
    """Average true range at bar i from the prior n true ranges — the
    volatility yardstick that was knowable at bar i."""
    if i < n:
        return None
    trs = []
    for j in range(i - n + 1, i + 1):
        tr = max(highs[j] - lows[j],
                 abs(highs[j] - closes[j - 1]),
                 abs(lows[j] - closes[j - 1]))
        trs.append(tr)
    return sum(trs) / n if trs else None


# ── how big a move counts as a swing, for THIS stock ────────────────────────

def typical_move_pct(closes, window: int = 20):
    """The median absolute `window`-day percentage move — a robust read of
    how far this stock actually travels in a month.

    Median rather than mean or standard deviation: one crash should not
    redefine what an ordinary month looks like, and this number decides how
    the whole history gets cut into swings.
    """
    n = len(closes or ())
    if n < window + 30:
        return None
    moves = [abs(closes[i] / closes[i - window] - 1.0) * 100.0
             for i in range(window, n) if closes[i - window]]
    return _median(moves)


def adaptive_zigzag_pct(closes, *, k: float = 2.5, floor_pct: float = 6.0,
                        ceiling_pct: float = 18.0, window: int = 20,
                        multiplier: float = 1.0):
    """The zigzag threshold this stock deserves, as a FRACTION (0.12 = 12%),
    or None when there is not enough history to measure.

    A fixed threshold does not mean the same thing on a utility and on a
    small-cap rocket: the same 12% setting cut the watchlist into 2.6 swings
    a year at one end and 38 at the other, left low-volatility names with
    single "swings" spanning more than a year, and starved their cohorts —
    Coca-Cola's live projection stood on ONE comparable episode. Scaling the
    threshold to the stock's own travel fixes the segmentation.

    It does NOT make the projection more accurate, and this docstring is the
    place to say so: over 32 symbols the band coverage (45.2% fixed vs 44.9%
    adaptive) and the error relative to the swing being projected (27.0% vs
    26.9%) are unchanged. What changes is whether there is a sample to stand
    on at all — the median live cohort goes from 17 to 24 and the number of
    symbols projecting from fewer than six episodes goes from 5 in 32 to
    none.
    """
    v = typical_move_pct(closes, window)
    if not v:
        return None
    m = max(0.05, float(multiplier))
    return max(floor_pct * m, min(ceiling_pct * m, k * m * v)) / 100.0


def resolve_zigzag_pct(closes, cfg: dict | None = None,
                       sensitivity: str = "standard",
                       explicit=None) -> dict:
    """One place that answers "what threshold is this chart using, and why".

    Returns {pct, source, multiplier, typical_move_pct, k, floor, ceiling}
    with `pct` a fraction. An explicit number always wins — the caller asked
    for it — and is reported as such so the UI never claims a number was
    adaptive when it was typed.
    """
    c = _cfg(cfg)
    mults = c.get("sensitivity_multipliers") or {}
    mult = float(mults.get(sensitivity, 1.0))
    v = typical_move_pct(closes, int(c["zigzag_window"]))
    ratio = float(c["display_filter_ratio"])
    out = {"multiplier": mult, "sensitivity": sensitivity,
           "typical_move_pct": _r(v, 1), "k": float(c["zigzag_k"]),
           "display_filter_ratio": ratio,
           "floor_pct": float(c["zigzag_floor_pct"]) * mult,
           "ceiling_pct": float(c["zigzag_ceiling_pct"]) * mult,
           "window": int(c["zigzag_window"])}
    def _finish(pct, source, **extra):
        out.update({"pct": pct, "source": source,
                    "min_move_pct": _r(pct * 100.0 * ratio, 1)}, **extra)
        return out

    ex = _num(explicit)
    if ex and ex > 0:
        return _finish(ex, "explicit")
    if c.get("adaptive_zigzag"):
        ad = adaptive_zigzag_pct(closes, k=float(c["zigzag_k"]),
                                 floor_pct=float(c["zigzag_floor_pct"]),
                                 ceiling_pct=float(c["zigzag_ceiling_pct"]),
                                 window=int(c["zigzag_window"]),
                                 multiplier=mult)
        if ad:
            clamped = (abs(ad * 100.0 - out["floor_pct"]) < 1e-9
                       or abs(ad * 100.0 - out["ceiling_pct"]) < 1e-9)
            return _finish(ad, "adaptive", clamped=clamped)
    # Not enough history to measure the stock's own travel: fall back to the
    # legacy fixed threshold, scaled by the setting, and say so.
    return _finish(0.12 * mult, "fallback")


# ── legs ────────────────────────────────────────────────────────────────────

def build_legs(pivots, dates, highs, lows, closes,
               split_dates=None, max_credible_day_move_pct: float = 50.0) -> list:
    """Every COMPLETED zigzag leg, in order, with next-leg chaining.

    The final pivot is the in-progress extreme, so the last (pivots[-2] →
    pivots[-1]) pair is the active leg and is excluded here; nothing about
    an unfinished move may enter a historical statistic.

    A leg is EXCLUDED (kept in the list, flagged, skipped by every
    statistic) when it contains a split date, or a single close-to-close
    move beyond `max_credible_day_move_pct` — an adjusted daily series
    should never print such a day, so it is treated as a data artifact
    rather than explained.
    """
    splits = set(split_dates or ())
    legs = []
    for k in range(len(pivots) - 2):
        a, b = pivots[k], pivots[k + 1]
        ai, ap, akind = a
        bi, bp, _bkind = b
        if bi <= ai or not ap:
            continue
        direction = "up" if akind == "low" else "down"
        pct = (bp - ap) / ap * 100.0
        excluded = None
        for j in range(ai, bi + 1):
            if dates[j] in splits:
                excluded = "contains a declared split"
                break
            if j > 0 and closes[j - 1] > 0:
                day = abs(closes[j] / closes[j - 1] - 1.0) * 100.0
                if day >= max_credible_day_move_pct:
                    excluded = (f"contains a {day:.0f}% single-day move — "
                                f"beyond credibility for an adjusted series")
                    break
        legs.append({
            "dir": direction,
            "start_i": ai, "end_i": bi,
            "start_date": dates[ai], "end_date": dates[bi],
            "start_price": float(ap), "end_price": float(bp),
            "pct": pct, "abs_pct": abs(pct),
            "days": bi - ai,
            "excluded": excluded,
        })
    for k in range(len(legs) - 1):
        legs[k]["next"] = legs[k + 1]
    if legs:
        legs[-1]["next"] = None
    return legs


def crossing_index(leg: dict, highs, lows, threshold_abs_pct: float):
    """The first bar inside a leg where its running move had reached
    `threshold_abs_pct` — the moment that historical swing looked the way
    the current one looks now. Uses intraday extremes, matching how the
    zigzag itself measures. None when the leg never travelled that far."""
    sp = leg["start_price"]
    if sp <= 0:
        return None
    if leg["dir"] == "down":
        thr = sp * (1.0 - threshold_abs_pct / 100.0)
        for j in range(leg["start_i"] + 1, leg["end_i"] + 1):
            if lows[j] <= thr:
                return j
    else:
        thr = sp * (1.0 + threshold_abs_pct / 100.0)
        for j in range(leg["start_i"] + 1, leg["end_i"] + 1):
            if highs[j] >= thr:
                return j
    return None


def _threshold_price(start_price: float, direction: str, abs_pct: float) -> float:
    return (start_price * (1.0 - abs_pct / 100.0) if direction == "down"
            else start_price * (1.0 + abs_pct / 100.0))


def crossing_fill(leg: dict, opens, direction: str, abs_pct: float, ci: int):
    """The price a reader could ACTUALLY have transacted at when a historical
    swing first reached the current depth — and whether the market gapped
    through the level (v4.51).

    The theoretical level is `start_price` moved by `abs_pct`. A stock does
    not always trade there: if the session OPENED already beyond it, that
    level never existed as a live price on that bar and the first available
    price is the open. Using the theoretical level anyway invents a fill,
    flatters the entry, and quietly moves every downstream number (the
    remaining-move measurement and the what-if race both start here).

    Returns (price, gapped). With no opens supplied the theoretical level is
    used and `gapped` is None — unknown, not false.
    """
    thr = _threshold_price(leg["start_price"], direction, abs_pct)
    if not opens or ci is None or ci >= len(opens):
        return thr, None
    op = _num(opens[ci])
    if op is None or op <= 0:
        return thr, None
    if direction == "down":
        # Crossing downward: the level is live only if the bar opened at or
        # above it. An open below means the gap took the level away.
        return (thr, False) if op >= thr else (op, True)
    return (thr, False) if op <= thr else (op, True)


# ── earnings tagging ────────────────────────────────────────────────────────

def _date_near(date_strs, target_idx, dates, within_days: int) -> bool:
    """Is any tagged date within ±within_days TRADING bars of dates[target_idx]?
    Trading-bar distance, because the bars list is the trading calendar."""
    if not date_strs:
        return False
    lo = max(0, target_idx - within_days)
    hi = min(len(dates) - 1, target_idx + within_days)
    window = set(dates[lo:hi + 1])
    return any(d in window for d in date_strs)


def _dates_between(date_strs, dates, i0, i1) -> bool:
    if not date_strs:
        return False
    if i0 is None or i1 is None or i1 < i0:
        return False
    window = set(dates[max(0, i0):i1 + 1])
    return any(d in window for d in date_strs)


# ── fixed-horizon excursion (§ probabilities are fixed-horizon) ─────────────

def horizon_hit(highs, lows, anchor_close: float, i: int, pct: float,
                days: int, direction: str) -> bool | None:
    """Did price move `pct`% in the reversal direction within `days` trading
    bars of bar i, measured from bar i's CLOSE?

    The close, not the intraday extreme, on both sides of the comparison:
    a rebound measured from the day's low against a baseline measured from
    a close would flatter the conditional number by construction. None when
    the full horizon does not exist in the data — an incomplete window is
    not a miss.
    """
    n = len(highs)
    if i + days >= n:
        return None
    c0 = _num(anchor_close)
    if c0 is None or c0 <= 0:
        return None
    if direction == "down":                     # follow-on is a rebound
        level = c0 * (1.0 + pct / 100.0)
        return any(highs[j] >= level for j in range(i + 1, i + 1 + days))
    level = c0 * (1.0 - pct / 100.0)
    return any(lows[j] <= level for j in range(i + 1, i + 1 + days))


def baseline_hit_rate(highs, lows, closes, pct: float, days: int,
                      direction: str) -> tuple:
    """The same question asked of EVERY ordinary bar: (rate_pct, n).

    Windows overlap, so n counts bars rather than independent episodes and
    the rate carries no confidence interval — it is the unconditional
    frequency this stock offers, which is exactly what the conditional rate
    has to beat. Reported as a point estimate and labelled as one.
    """
    n = len(closes)
    hits = tries = 0
    for i in range(0, n - days):
        h = horizon_hit(highs, lows, closes[i], i, pct, days, direction)
        if h is None:
            continue
        tries += 1
        hits += 1 if h else 0
    return (_r(hits / tries * 100.0, 0) if tries else None), tries


# ── zone state: where the EXTREME stands, not just today's close ────────────

def zone_state(direction: str, ext_price: float, cur_price: float,
               band_low: float, band_high: float) -> dict:
    """Deterministic reversal status from the running extreme (v4.51).

    THE BAND HERE IS THE UNCONDITIONAL ONE, and that is a correction to the
    brief rather than an oversight. The conditional survival zone can never
    contain the running extreme: every member of that cohort ended at or
    beyond the current depth, so its shallow quartile sits at or past the
    extreme BY CONSTRUCTION and "IN ZONE" could never fire — the same dead
    state the beyond-the-median flag turned out to be. The question "has
    this decline reached the depth where this stock's declines usually
    end?" is a question about the WHOLE distribution of completed swings,
    so that is the band the status is measured against. The conditional
    zone keeps doing its own job: where THIS swing, having got here, is
    likely to finish.

    The status answers three yes/no questions in a fixed order, with no
    invented confirmation threshold: the band itself is the yardstick for
    "materially away from the extreme".

      APPROACHING           the extreme has not reached the near edge of
                            the zone yet.
      IN ZONE               the extreme is inside the band and price has
                            not left the band in the reversal direction.
      BOUNCING/FADING OFF   the extreme reached the zone and price has
        ZONE                since travelled back out of the band.
      BEYOND TYPICAL ZONE   the extreme is past the far (p75) edge and
                            price is still out there with it.
      (BEYOND HISTORY is decided by the caller: it means no completed swing
       ever reached this magnitude, so there is no band at all.)

    A known asymmetry, measured and disclosed rather than papered over
    (v4.52): BOUNCING/FADING can realistically only fire near the NEAR edge.
    Leaving the band requires price to travel back past that edge, which is
    a small move for an extreme that just grazed it and a very large one for
    an extreme sitting deep inside. Across 1,197 scanned symbols the median
    penetration of a reacting name was 14% of the band against 46% for one
    still IN ZONE. `band_penetration_pct` below is what makes that visible;
    ordering the scan by it was tested and rejected (see the scan ordering
    note in thresholds.json).
    """
    down = direction == "down"
    near_edge = band_high if down else band_low     # the edge reached first
    far_edge = band_low if down else band_high
    reached = (ext_price <= near_edge + 1e-9) if down else \
              (ext_price >= near_edge - 1e-9)
    beyond = (ext_price < far_edge - 1e-9) if down else \
             (ext_price > far_edge + 1e-9)
    in_zone_ext = reached and not beyond
    in_zone_cur = band_low - 1e-9 <= cur_price <= band_high + 1e-9
    left_band = (cur_price > band_high + 1e-9) if down else \
                (cur_price < band_low - 1e-9)
    off_pct = (abs(cur_price - ext_price) / ext_price * 100.0
               if ext_price else None)
    # How far INTO the band the extreme actually travelled, as a position
    # rather than a score: 0% = exactly at the near edge, 100% = at the far
    # edge, above 100% = out the other side, below 0% = not there yet. The
    # same kind of measurement as the 20-day range position. It exists
    # because "reached the band" is a single bit that treats a swing which
    # grazed the shallow edge as identical to one sitting in the middle of
    # it — and the shallow grazes are far more numerous.
    span = abs(near_edge - far_edge)
    pen = (abs(near_edge - ext_price) / span * 100.0 * (1 if reached else -1)
           if span > 1e-9 else None)
    if not reached:
        code = "APPROACHING"
    elif left_band:
        code = "BOUNCING OFF ZONE" if down else "FADING OFF ZONE"
    elif beyond and not in_zone_cur:
        code = "BEYOND TYPICAL ZONE"
    else:
        code = "IN ZONE"
    return {
        "code": code,
        "zone_touched": bool(reached),
        "extreme_in_zone": bool(in_zone_ext),
        "extreme_beyond_zone": bool(beyond),
        "current_in_zone": bool(in_zone_cur),
        "band_penetration_pct": _r(pen, 0),
        "off_extreme_pct": _r(off_pct, 1),
        "off_extreme_dollars": _r(abs(cur_price - ext_price)),
    }


_STATUS_MEANING = {
    "APPROACHING": ("The running {ext} has not reached the historical "
                    "{turn} zone yet."),
    "IN ZONE": ("The running {ext} is inside the zone where comparable "
                "swings historically {turned}."),
    "BOUNCING OFF ZONE": ("The running low reached the historical bottom "
                          "zone and price has since traded back above it."),
    "FADING OFF ZONE": ("The running high reached the historical top zone "
                        "and price has since traded back below it."),
    "BEYOND TYPICAL ZONE": ("The running {ext} is past the far edge of the "
                            "typical zone — deeper outcomes exist in the "
                            "record, but this is no longer the normal case."),
    "BEYOND HISTORY": ("No completed swing in this history ever reached this "
                       "magnitude, so there is no zone to project."),
}


def status_meaning(code: str, direction: str) -> str:
    down = direction == "down"
    return _STATUS_MEANING.get(code, "").format(
        ext="low" if down else "high",
        turn="bottom" if down else "top",
        turned="bottomed" if down else "topped")


# ── the projection ──────────────────────────────────────────────────────────

def project(pivots, dates, highs, lows, closes, *,
            opens=None,
            min_move_pct: float = 15.0,
            zigzag_pct: float | None = None,
            earnings_dates=None,
            earnings_meta: dict | None = None,
            split_dates=None,
            upcoming_earnings_days=None,
            cfg: dict | None = None) -> dict:
    """The full reversal read for the swing in progress.

    Definitions, exactly:

      CURRENT SWING   from the last confirmed opposite pivot to today.
                      Its size for cohort purposes is the EXTREME's
                      distance from the start; its distances to the zone
                      are measured from the CURRENT close.
      COHORT          every completed same-direction leg (not excluded for
                      a corporate action) whose extreme reached at least
                      the current extreme's magnitude. NOT filtered by
                      min_move_pct — that is a display setting. When fewer
                      than min_cohort_n such legs exist the zone is still
                      computed but carries insufficient=True.
      ZONE            p25 / median / p75 of the cohort's FINAL sizes,
                      converted to prices from the current swing's start.
      STATUS          where the running EXTREME stands against that zone,
                      and whether price has since moved away from it.
      REMAINING       per-cohort-leg: crossing (first bar at the current
                      magnitude, priced at what was actually fillable
                      there) → end. Days and additional adverse move are
                      medians of those per-leg remainders.
      NEXT SWING      the actual next zigzag leg after each cohort leg,
                      whatever its size, when completed. Median/p25/p75 of
                      size and days; a PAIRED target band that keeps each
                      episode's own (depth, rebound) relationship; and
                      fixed-horizon touch rates against a same-horizon
                      baseline.
    """
    c = _cfg(cfg)
    out: dict[str, Any] = {"ok": False, "engine": ENGINE_VERSION,
                           "reason": None}
    n_bars = len(closes)
    if len(pivots) < 2 or n_bars < 30:
        out["reason"] = "not enough pivots or bars to project from"
        return out

    # ── the current swing ───────────────────────────────────────────────
    last_pivot = pivots[-1]
    direction = "down" if last_pivot[2] == "low" else "up"
    start = None
    for p in reversed(pivots[:-1]):
        if (direction == "down" and p[2] == "high") or \
           (direction == "up" and p[2] == "low"):
            start = p
            break
    if start is None:
        out["reason"] = "no confirmed opposite pivot behind the active swing"
        return out
    from_i, from_p = start[0], float(start[1])
    ext_i, ext_p = last_pivot[0], float(last_pivot[1])
    cur_price = float(closes[-1])
    if from_p <= 0:
        out["reason"] = "degenerate start pivot"
        return out
    cur_pct = (cur_price - from_p) / from_p * 100.0
    ext_abs = abs((ext_p - from_p) / from_p * 100.0)
    days_active = (n_bars - 1) - from_i
    last_i = n_bars - 1
    off_ext_pct = (abs(cur_price - ext_p) / ext_p * 100.0) if ext_p else None

    legs = build_legs(pivots, dates, highs, lows, closes,
                      split_dates=split_dates,
                      max_credible_day_move_pct=float(c["max_credible_day_move_pct"]))
    same_dir = [L for L in legs if L["dir"] == direction]
    # THE POPULATION (v4.51): every completed leg in this direction. The
    # display filter no longer touches it — see the module docstring.
    pool = [L for L in same_dir if not L["excluded"]]
    excluded_n = sum(1 for L in same_dir if L["excluded"])
    # The visible-history rhythm, which the completion measures compare
    # against, DOES keep the display filter so the panel and the table
    # below it are talking about the same swings.
    shown = [L for L in pool if L["abs_pct"] >= float(min_move_pct)]
    all_sizes = [L["abs_pct"] for L in shown]
    all_days = [L["days"] for L in shown]

    regime_now = regime_at(closes, last_i, int(c["regime_sma_n"]))
    rp_now = range_position(highs, lows, cur_price, last_i,
                            int(c["range_window"]))
    atr_now = atr_at(highs, lows, closes, last_i, int(c["atr_n"]))

    out.update({
        "direction": direction,
        "current": {
            "pct": _r(cur_pct), "abs_pct": _r(abs(cur_pct)),
            "extreme_abs_pct": _r(ext_abs),
            "days": days_active,
            "from_price": _r(from_p), "from_date": dates[from_i],
            "extreme_price": _r(ext_p), "extreme_date": dates[ext_i],
            "price": _r(cur_price),
            # How far price has already recovered from the running extreme.
            # Zero while the extreme IS today's price.
            "off_extreme_pct": _r(off_ext_pct, 1),
            "off_extreme_dollars": _r(abs(cur_price - ext_p)),
            "range_pos_20": _r(rp_now, 0),
            "regime": regime_now,
            "atr": _r(atr_now),
            "move_atr": (_r(abs(cur_price - from_p) / atr_now, 1)
                         if atr_now else None),
            # Two completion measures, never combined, and never to be read
            # as a reversal probability: a swing can be at 120% of the
            # median and keep falling; the cohort numbers below are what say
            # how often that happened.
            "move_completion_pct": (_r(ext_abs / _median(all_sizes) * 100.0, 0)
                                    if all_sizes and _median(all_sizes) else None),
            "time_completion_pct": (_r(days_active / _median(all_days) * 100.0, 0)
                                    if all_days and _median(all_days) else None),
        },
        "history": {
            "n": len(shown),
            "excluded": excluded_n,
            "min_move_pct": float(min_move_pct),
            "pct_p25": _r(_quantile(all_sizes, 0.25), 1),
            "pct_median": _r(_median(all_sizes), 1),
            "pct_p75": _r(_quantile(all_sizes, 0.75), 1),
            "days_p25": _r(_quantile(all_days, 0.25), 0),
            "days_median": _r(_median(all_days), 0),
            "days_p75": _r(_quantile(all_days, 0.75), 0),
        },
    })
    if not pool:
        out["reason"] = ("no completed "
                         + ("declines" if direction == "down" else "rallies")
                         + " in the history to compare with")
        return out

    # ── the cohort: every completed leg that reached at least this far ──
    cohort_all = [L for L in pool if L["abs_pct"] >= ext_abs - 1e-9]
    floor = float(zigzag_pct) if zigzag_pct else None
    filters = {"direction": direction,
               "reached_at_least_pct": _r(ext_abs, 1),
               "display_min_move_pct_applied": False,
               "structural_floor_pct": _r(floor, 1) if floor else None,
               "regime": None}
    regime_applied = False
    if c.get("regime_filter") and regime_now in ("uptrend", "downtrend"):
        tagged = []
        for L in cohort_all:
            ci = crossing_index(L, highs, lows, ext_abs)
            if ci is not None and regime_at(closes, ci, int(c["regime_sma_n"])) == regime_now:
                tagged.append(L)
        if len(tagged) >= int(c["min_cohort_n"]):
            cohort_all = tagged
            filters["regime"] = regime_now
            regime_applied = True

    share_exceeded = (sum(1 for L in pool if L["abs_pct"] < ext_abs - 1e-9)
                      / len(pool) * 100.0)
    pool_sizes = [L["abs_pct"] for L in pool]
    out["maturity"] = _maturity(ext_abs, pool_sizes, c, direction)
    # The band the STATUS is measured against: where this stock's swings in
    # this direction have historically ended, unconditionally. Reachable by
    # definition — unlike the survival zone below, which the extreme can
    # never be inside. Prices are projected from today's swing origin, so
    # the comparison is like for like.
    t25, tmed, t75 = (_quantile(pool_sizes, 0.25), _median(pool_sizes),
                      _quantile(pool_sizes, 0.75))
    tlo, thi = sorted([_r(_threshold_price(from_p, direction, t25)),
                       _r(_threshold_price(from_p, direction, t75))])
    out["typical_zone"] = {
        "p25_abs_pct": _r(t25, 1), "median_abs_pct": _r(tmed, 1),
        "p75_abs_pct": _r(t75, 1), "max_abs_pct": _r(max(pool_sizes), 1),
        "median_price": _r(_threshold_price(from_p, direction, tmed)),
        "band_low_price": tlo, "band_high_price": thi,
        "n": len(pool_sizes),
        "basis": ("every completed " + ("decline" if direction == "down"
                                        else "rally") + " in the history, "
                  "not just the ones that got this far — the band a "
                  "developing swing can actually travel into"),
    }
    st = zone_state(direction, ext_p, cur_price, tlo, thi)
    st["meaning"] = status_meaning(st["code"], direction)
    out["status"] = st
    floor_note = None
    if floor:
        if ext_abs <= floor + 0.5:
            floor_note = (
                f"At this depth the cohort is simply every completed "
                f"{'decline' if direction == 'down' else 'rally'} in the "
                f"history: a zigzag only registers a swing after a "
                f"{floor:.0f}% counter-move, so no completed swing is smaller "
                f"than {floor:.0f}% and every one of them qualifies.")
        else:
            floor_note = (
                f"Chart sensitivity is {floor:.0f}%, so the record contains no "
                f"completed swing smaller than that. The 15% display filter on "
                f"the tables below is NOT applied here — every completed swing "
                f"that reached {ext_abs:.1f}% is in the cohort.")

    # ── earnings contamination, in all three places (v4.51) ────────────
    earn = set(earnings_dates or ())
    near_d = int(c["earnings_near_days"])
    episodes = []
    for L in cohort_all:
        ci = crossing_index(L, highs, lows, ext_abs)
        fill, gapped = crossing_fill(L, opens, direction, ext_abs, ci)
        nx = L.get("next")
        nx_ok = bool(nx and not nx.get("excluded"))
        episodes.append({
            "leg": L, "ci": ci, "fill": fill, "gapped": gapped,
            "next": nx if nx_ok else None,
            # A — between the crossing and the final extreme. A report that
            # landed while the swing was already this deep is what turns an
            # ordinary decline into a gap-driven one; it must not teach the
            # zone that the extra 25% was normal.
            "earn_run": _dates_between(earn, dates, ci, L["end_i"]) if ci is not None else False,
            # B — at or around the reversal pivot.
            "earn_pivot": _date_near(earn, L["end_i"], dates, near_d),
            # C — inside the following opposite swing.
            "earn_next": (_dates_between(earn, dates, nx["start_i"], nx["end_i"])
                          if nx_ok else False),
        })

    min_clean = int(c["min_clean_n"])
    with_cross = [e for e in episodes if e["ci"] is not None]
    clean_zone = [e for e in with_cross if not e["earn_run"]]
    zone_pop = clean_zone if len(clean_zone) >= min_clean else with_cross
    zone_clean_applied = zone_pop is clean_zone and len(clean_zone) != len(with_cross)
    cohort = [e["leg"] for e in zone_pop]
    insufficient = len(cohort) < int(c["min_cohort_n"])

    out["cohort"] = {
        "n": len(cohort),
        # The bar at which each past swing had come as far as this one has
        # now. Anything wanting to measure "what happened next, from here"
        # needs those bars, and re-deriving them elsewhere would let a
        # second definition of "this deep" drift away from this one.
        "cross_bar_index": [e["ci"] for e in zone_pop if e["ci"] is not None],
        "n_reached": len(cohort_all),
        "n_direction": len(pool),
        "n_earnings_contaminated": sum(1 for e in with_cross if e["earn_run"]),
        "n_clean": len(clean_zone),
        "earnings_excluded_applied": bool(zone_clean_applied),
        "filters": filters,
        "regime_filter_applied": regime_applied,
        "insufficient": insufficient,
        "min_n": int(c["min_cohort_n"]),
        "share_of_history_already_exceeded_pct": _r(share_exceeded, 0),
        "floor_note": floor_note,
        "earnings_note": _earn_note(len(with_cross), len(clean_zone),
                                    zone_clean_applied, direction, earnings_meta),
        "earnings_source": (earnings_meta or {}).get("source"),
        "note": (None if not insufficient else
                 (f"Only {len(cohort)} completed "
                  + _plural(len(cohort), "decline" if direction == "down" else "rally",
                            "declines" if direction == "down" else "rallies")
                  + f" ever reached {ext_abs:.1f}% — everything below is a "
                    f"description of those few, not a reliable zone.")),
    }

    if not cohort:
        out["ok"] = True
        out["zone"] = None
        out["remaining"] = None
        out["next"] = None
        biggest = max(pool_sizes, default=0.0)
        out["status"].update({
            "code": "BEYOND HISTORY", "zone_touched": True,
            "extreme_in_zone": False, "extreme_beyond_zone": True,
            "meaning": status_meaning("BEYOND HISTORY", direction)})
        out["summary"] = (
            f"This {'decline' if direction == 'down' else 'rally'} of "
            f"{ext_abs:.1f}% has gone further than every completed "
            f"{'decline' if direction == 'down' else 'rally'} in the "
            f"history ({len(pool)} of them; largest {biggest:.1f}%). There is "
            f"no historical zone ahead of it — the stock is in territory its "
            f"own past cannot describe.")
        out["flags"] = ["BEYOND ALL HISTORY"]
        return out

    # ── the reversal zone (conditional final sizes → prices) ────────────
    fin = [L["abs_pct"] for L in cohort]
    z25, zmed, z75 = (_quantile(fin, 0.25), _median(fin), _quantile(fin, 0.75))

    def _zone_price(abs_pct):
        return _r(_threshold_price(from_p, direction, abs_pct))

    zone = {
        "p25_abs_pct": _r(z25, 1), "median_abs_pct": _r(zmed, 1),
        "p75_abs_pct": _r(z75, 1),
        "max_abs_pct": _r(max(fin), 1),
        # For a decline the p25 (shallower) bound is the HIGHER price; the
        # UI shows the band low→high, so provide both orderings explicitly.
        "p25_price": _zone_price(z25),
        "median_price": _zone_price(zmed),
        "p75_price": _zone_price(z75),
        "n": len(cohort),
    }
    lo_px, hi_px = sorted([zone["p25_price"], zone["p75_price"]])
    zone["band_low_price"], zone["band_high_price"] = lo_px, hi_px
    out["zone"] = zone

    # ── remaining move and time, from each leg's own crossing ───────────
    rem_days, more_move = [], []
    gapped_n = 0
    for e in zone_pop:
        L, ci = e["leg"], e["ci"]
        rem_days.append(L["end_i"] - ci)
        ref = e["fill"]
        if e["gapped"]:
            gapped_n += 1
        if ref and ref > 0:
            more_move.append(abs(L["end_price"] / ref - 1.0) * 100.0)
    ladder = []
    for step in c["more_move_ladder"]:
        if more_move:
            k = sum(1 for m in more_move if m <= float(step) + 1e-9)
            wi = wilson_interval(k, len(more_move)) or {}
            ladder.append({
                "within_more_pct": float(step),
                "rate_pct": _r(k / len(more_move) * 100.0, 0),
                "wilson_lo_pct": _r((wi.get("lo") or 0.0) * 100.0, 0),
                "n": len(more_move), "count": k,
            })
    med_more = _median(more_move)
    to_median_pct = ((zone["median_price"] - cur_price) / cur_price * 100.0
                     if cur_price > 0 else None)
    out["remaining"] = {
        "to_median_pct": _r(to_median_pct, 1),
        "to_median_dollars": _r(zone["median_price"] - cur_price),
        "to_band_low_pct": _r((lo_px - cur_price) / cur_price * 100.0, 1),
        "to_band_high_pct": _r((hi_px - cur_price) / cur_price * 100.0, 1),
        # There is deliberately no "beyond the median" state here: every
        # cohort member reached at least the current extreme, so the
        # conditional median is at or beyond it BY CONSTRUCTION and the
        # extreme can never be past it. Depth relative to history is the
        # share_of_history_already_exceeded figure on the cohort.
        "more_move_median_pct": _r(med_more, 1),
        "more_move_p25_pct": _r(_quantile(more_move, 0.25), 1),
        "more_move_p75_pct": _r(_quantile(more_move, 0.75), 1),
        "days_median": _r(_median(rem_days), 0),
        "days_p25": _r(_quantile(rem_days, 0.25), 0),
        "days_p75": _r(_quantile(rem_days, 0.75), 0),
        "n": len(rem_days),
        "gapped_entries": gapped_n,
        "measured_from": ("the price actually available when each swing first "
                          "reached this depth — the session open when the "
                          "market gapped through the level"),
        "reversed_within": ladder,
    }

    # ── the next opposite swing ─────────────────────────────────────────
    out["next"] = _next_block(episodes, zone_pop, direction, from_p, zone,
                              dates, opens, highs, lows, closes, c, min_clean,
                              last_i)

    out["ok"] = True
    out["flags"] = _flags(out, upcoming_earnings_days)
    out["summary"] = _summary(out, upcoming_earnings_days)
    return out


def _maturity(ext_abs: float, pool_sizes: list, c: dict, direction: str) -> dict:
    """How far into a normal move this one is, and — from the staged
    walk-forward run, not from taste — how much the conditional projection
    is worth at that stage.

    The reference is the median of EVERY completed same-direction leg, the
    same population the cohort is drawn from, so the ratio and the cohort
    cannot disagree. (The panel's "completion vs median" figure is measured
    against the ≥15% display rhythm instead, because it sits beside the
    table of those swings.)

    The three states are not a score and never enter the arithmetic. They
    exist so the scanner cannot present a swing that has barely started with
    the same confidence as one that has already outrun its own history.
    """
    ref = _median(pool_sizes)
    if not ref:
        return {"code": "UNKNOWN", "ratio_pct": None, "ref_median_pct": None,
                "note": "no completed swings to compare this one against"}
    ratio = ext_abs / ref * 100.0
    normal = float(c["maturity_normal_pct"])
    beyond = float(c["maturity_beyond_pct"])
    word = "decline" if direction == "down" else "rally"
    if ratio < normal:
        code, note = "EARLY IN THE MOVE", (
            f"This {word} is {ratio:.0f}% of the size of a normal one "
            f"({ref:.1f}%). At this stage nearly every past swing qualifies "
            f"for the cohort, and walk-forward testing found the conditional "
            f"projection no more accurate here than the stock's plain "
            f"rhythm — read the zone as context, not as a signal.")
    elif ratio < beyond:
        code, note = "AT ITS NORMAL SIZE", (
            f"This {word} has reached {ratio:.0f}% of a normal one "
            f"({ref:.1f}%) — the depth at which conditioning on survivors "
            f"starts to beat the plain rhythm in walk-forward testing "
            f"(about a fifth less error).")
    else:
        code, note = "BEYOND ITS NORMAL SIZE", (
            f"This {word} is {ratio:.0f}% of a normal one ({ref:.1f}%). This "
            f"is where the unconditional rhythm fails worst — in testing its "
            f"band covered only a quarter of outcomes, because it keeps "
            f"projecting a turn the move has already passed. The cohort "
            f"below cannot make that mistake.")
    return {"code": code, "ratio_pct": _r(ratio, 0),
            "ref_median_pct": _r(ref, 1), "note": note}


def _earn_note(n_total, n_clean, applied, direction, meta) -> str | None:
    if not n_total:
        return None
    dirty = n_total - n_clean
    src = (meta or {}).get("label")
    tail = f" Earnings history: {src}." if src else ""
    if dirty == 0:
        return None
    noun = "decline" if direction == "down" else "rally"
    if applied:
        return (f"{dirty} of {n_total} comparable {noun}"
                f"{'' if n_total == 1 else 's'} had an earnings report "
                f"between this depth and the final turn; they are excluded "
                f"from the zone and the remaining-move numbers.{tail}")
    return (f"{dirty} of {n_total} comparable {noun}"
            f"{'' if n_total == 1 else 's'} had an earnings report between "
            f"this depth and the final turn. Excluding them would leave too "
            f"few to measure, so they are included and counted here.{tail}")


def _next_block(episodes, zone_pop, direction, from_p, zone, dates, opens,
                highs, lows, closes, c, min_clean, last_i):
    """Everything about the swing that follows the reversal.

    Three populations, kept apart on purpose:
      · size/duration      every episode with a completed next leg
      · paired target      those same episodes, each contributing its OWN
                           (final depth, next move) pair projected from
                           today's swing origin — never median × median
      · horizon touch      fixed (percent, days) excursions from the
                           reversal pivot's close, against the same
                           (percent, days) measured from ordinary days
    """
    have_next = [e for e in episodes if e["next"] is not None]
    if not have_next:
        return None
    clean = [e for e in have_next if not (e["earn_pivot"] or e["earn_next"])]
    use = clean if len(clean) >= min_clean else have_next
    excluded_earn = len(have_next) - len(use)

    sizes = [e["next"]["abs_pct"] for e in use]
    ndays = [e["next"]["days"] for e in use]
    nmed, n25, n75 = _median(sizes), _quantile(sizes, 0.25), _quantile(sizes, 0.75)

    # ── paired targets: each episode's own relationship, re-based on today
    paired_clean = [e for e in use if not e["earn_run"]]
    paired_use = paired_clean if len(paired_clean) >= min_clean else use
    pairs, targets = [], []
    for e in paired_use:
        depth = e["leg"]["abs_pct"]
        move = e["next"]["abs_pct"]
        rev_px = _threshold_price(from_p, direction, depth)
        tgt = (rev_px * (1.0 + move / 100.0) if direction == "down"
               else rev_px * (1.0 - move / 100.0))
        pairs.append({"reversal_abs_pct": _r(depth, 1),
                      "next_abs_pct": _r(move, 1),
                      "projected_reversal_price": _r(rev_px),
                      "projected_target_price": _r(tgt),
                      "date": e["leg"]["end_date"]})
        targets.append(tgt)
    paired = None
    if targets:
        paired = {
            "n": len(targets),
            "p25_price": _r(_quantile(targets, 0.25)),
            "median_price": _r(_median(targets)),
            "p75_price": _r(_quantile(targets, 0.75)),
            "pairs": pairs[-12:],
            "basis": ("each comparable episode's own depth and its own "
                      "follow-on move, both applied to today's swing origin "
                      "— the deep declines keep the rebounds they actually "
                      "produced"),
        }

    # The old product, retained as a diagnostic only. When it differs from
    # the paired median it is because depth and rebound are not independent.
    anchor = zone["median_price"]

    def _tgt(abs_pct):
        if anchor is None or abs_pct is None:
            return None
        return _r(anchor * (1.0 + abs_pct / 100.0) if direction == "down"
                  else anchor * (1.0 - abs_pct / 100.0))

    # ── fixed-horizon excursions from the reversal pivot's close ────────
    horizon = []
    for spec in c["touch_horizons"]:
        try:
            pct, days = float(spec[0]), int(spec[1])
        except (TypeError, ValueError, IndexError):
            continue
        if pct <= 0 or days <= 0:
            continue
        k = n = 0
        for e in use:
            piv = e["leg"]["end_i"]
            hit = horizon_hit(highs, lows, closes[piv], piv, pct, days, direction)
            if hit is None:
                continue
            n += 1
            k += 1 if hit else 0
        if not n:
            continue
        wi = wilson_interval(k, n) or {}
        base_rate, base_n = baseline_hit_rate(highs, lows, closes, pct, days,
                                              direction)
        cond = k / n * 100.0
        horizon.append({
            "pct": pct, "days": days,
            "rate_pct": _r(cond, 0),
            "wilson_lo_pct": _r((wi.get("lo") or 0.0) * 100.0, 0),
            "n": n, "count": k,
            "baseline_pct": base_rate,
            "baseline_n": base_n,
            "edge_pp": (_r(cond - base_rate, 0) if base_rate is not None else None),
        })

    # Reclaim/revisit the 20-day extreme AS OF each reversal pivot —
    # point-in-time, so the level is the one a reader saw that evening.
    reclaim_k = reclaim_n = 0
    win = int(c["range_window"])
    for e in use:
        piv = e["leg"]["end_i"]
        w0 = max(0, piv - win + 1)
        nx = e["next"]
        if direction == "down":
            level = max(highs[w0:piv + 1])
            hit = any(highs[j] >= level for j in range(nx["start_i"], nx["end_i"] + 1))
        else:
            level = min(lows[w0:piv + 1])
            hit = any(lows[j] <= level for j in range(nx["start_i"], nx["end_i"] + 1))
        reclaim_n += 1
        reclaim_k += 1 if hit else 0
    wir = (wilson_interval(reclaim_k, reclaim_n) or {}) if reclaim_n else {}
    w0 = max(0, last_i - win + 1)
    level_now = _r(max(highs[w0:last_i + 1]) if direction == "down"
                   else min(lows[w0:last_i + 1]))

    dirty = len(have_next) - len(clean)
    return {
        "kind": "bounce" if direction == "down" else "pullback",
        "n": len(use), "n_with_next": len(have_next),
        "earnings_excluded": excluded_earn,
        "earnings_tagged": dirty,
        "earnings_note": (
            None if excluded_earn == 0 else
            f"{excluded_earn} episode{'' if excluded_earn == 1 else 's'} with "
            f"earnings at the reversal or inside the follow-on move "
            f"{'is' if excluded_earn == 1 else 'are'} excluded from these "
            f"statistics."),
        "earnings_included_note": (
            None if (excluded_earn or not dirty) else
            f"{dirty} of these episodes had earnings at the reversal or inside "
            f"the follow-on swing; excluding them would leave too few to "
            f"measure, so they are included and counted."),
        "pct_median": _r(nmed, 1), "pct_p25": _r(n25, 1), "pct_p75": _r(n75, 1),
        "days_median": _r(_median(ndays), 0),
        "days_p25": _r(_quantile(ndays, 0.25), 0),
        "days_p75": _r(_quantile(ndays, 0.75), 0),
        "paired": paired,
        # The primary target is the paired median; the product survives only
        # as a diagnostic, and says so.
        "target_median_price": (paired or {}).get("median_price") or _tgt(nmed),
        "target_p25_price": (paired or {}).get("p25_price") or _tgt(n25),
        "target_p75_price": (paired or {}).get("p75_price") or _tgt(n75),
        "target_is_paired": bool(paired),
        "target_simple_median_price": _tgt(nmed),
        "target_basis": (
            ("Each comparable episode's own depth and its own follow-on move, "
             "projected from today's swing origin; the median of those "
             "projections. The reversal has to happen first.")
            if paired else
            ("Anchored at the median historical "
             + ("bottom" if direction == "down" else "top")
             + " — the reversal has to happen first.")),
        "horizon_touch": horizon,
        "horizon_note": (
            "Each rate is measured from the CLOSE of the historical reversal "
            "day over exactly that many trading days, and the baseline asks "
            "the identical question of every ordinary day in the history. "
            "Same target, same horizon — otherwise the two numbers are not "
            "comparable. Baseline windows overlap, so its count is bars, not "
            "independent episodes, and it carries no confidence interval. "
            "Read these as WHAT FOLLOWED A TURN, not as the chance of a turn: "
            "they are measured from bottoms that are only known to be bottoms "
            "afterwards. The what-if below is the version that starts where "
            "you would actually have to buy."),
        "reclaim_20d": {"rate_pct": _r(reclaim_k / reclaim_n * 100.0, 0)
                        if reclaim_n else None,
                        "wilson_lo_pct": _r((wir.get("lo") or 0.0) * 100.0, 0),
                        "n": reclaim_n, "level_now": level_now},
    }


def _flags(out, upcoming_earnings_days):
    flags = []
    cur = out.get("current") or {}
    cohort = out.get("cohort") or {}
    # The status has its own banner; repeating it here as a flag chip only
    # says the same thing twice. Flags are for conditions that change how
    # much weight the projection deserves.
    if cohort.get("insufficient"):
        flags.append("THIN SAMPLE")
    share = cohort.get("share_of_history_already_exceeded_pct")
    if share is not None and share >= 75:
        flags.append(f"DEEPER THAN {share:.0f}% OF HISTORY")
    if upcoming_earnings_days is not None and 0 <= upcoming_earnings_days <= 7:
        flags.append(f"EARNINGS IN {int(upcoming_earnings_days)} DAY"
                     + ("" if upcoming_earnings_days == 1 else "S"))
    if cur.get("regime") == "downtrend" and out.get("direction") == "down":
        flags.append("DOWNTREND")
    return flags


def _pretty_date(iso: str) -> str:
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    try:
        y, m, d = str(iso)[:10].split("-")
        return f"{months[int(m) - 1]} {int(d)}, {y}"
    except (ValueError, IndexError):
        return str(iso)


def _summary(out, upcoming_earnings_days) -> str:
    """One deterministic paragraph, assembled from the numbers already in
    the payload — never generated, never speculative, downgrades itself
    when the evidence is thin. It leads with the distinction that matters
    most at the right edge: today's price versus the running extreme."""
    d = out["direction"]
    cur = out["current"]
    zone = out.get("zone")
    rem = out.get("remaining")
    nxt = out.get("next")
    st = out.get("status") or {}
    cohort = out.get("cohort") or {}
    down = d == "down"
    turn = "bottomed" if down else "topped"
    ext_word = "low" if down else "high"
    parts = []
    if abs(cur["extreme_abs_pct"] - abs(cur["pct"])) >= 0.1:
        parts.append(
            f"Currently {abs(cur['pct']):.1f}% "
            f"{'below its swing high' if down else 'above its swing low'} but "
            f"already {'down' if down else 'up'} as much as "
            f"{cur['extreme_abs_pct']:.1f}% over {cur['days']} trading "
            f"{_plural(cur['days'], 'day', 'days')}, at the "
            f"{_pretty_date(cur['extreme_date'])} {ext_word} of "
            f"${cur['extreme_price']:,.2f}.")
    else:
        parts.append(
            f"{'Down' if down else 'Up'} {abs(cur['pct']):.1f}% over "
            f"{cur['days']} trading {_plural(cur['days'], 'day', 'days')} from "
            f"the {_pretty_date(cur['from_date'])} swing "
            f"{'high' if down else 'low'} — today's close IS the running "
            f"{ext_word}.")
    if not zone or not rem:
        parts.append("No completed historical swing has gone this far, so "
                     "there is no zone to project.")
        return " ".join(parts)
    n = cohort.get("n") or 0
    noun = _plural(n, "decline" if down else "rally",
                   "declines" if down else "rallies")
    qual = ("Only " if cohort.get("insufficient") else "") \
        + f"{n} prior {noun} reached at least {cur['extreme_abs_pct']:.1f}%"
    parts.append(
        f"{qual}; they {turn} at a median of {zone['median_abs_pct']:.1f}% "
        f"(typically {zone['p25_abs_pct']:.1f}%–{zone['p75_abs_pct']:.1f}%), a "
        f"historical {'bottom' if down else 'top'} zone of "
        f"${zone['band_low_price']:,.2f}–${zone['band_high_price']:,.2f}.")
    code = st.get("code")
    off = st.get("off_extreme_pct") or 0.0
    tz = out.get("typical_zone") or {}
    where = (f"the ${tz['band_low_price']:,.2f}–${tz['band_high_price']:,.2f} "
             f"band where its {'declines' if down else 'rallies'} usually end"
             if tz.get("band_low_price") is not None else "that band")
    if code in ("BOUNCING OFF ZONE", "FADING OFF ZONE"):
        parts.append(
            f"The running {ext_word} of ${cur['extreme_price']:,.2f} already "
            f"reached {where}, and price is now {off:.1f}% "
            f"{'above' if down else 'below'} it.")
    elif code == "IN ZONE":
        parts.append(
            f"The running {ext_word} of ${cur['extreme_price']:,.2f} is inside "
            f"{where}"
            + (f", with price {off:.1f}% {'above' if down else 'below'} it."
               if off else "."))
    elif code == "BEYOND TYPICAL ZONE":
        parts.append(
            f"The running {ext_word} of ${cur['extreme_price']:,.2f} is past "
            f"{where} — deeper outcomes exist in the record, but this is no "
            f"longer the normal case.")
    if rem.get("more_move_median_pct") is not None:
        eta = (f" and about {rem['days_median']:.0f} more trading "
               f"{_plural(rem['days_median'], 'day', 'days')}"
               if rem.get("days_median") is not None else "")
        parts.append(
            f"From this depth the median comparable swing still had "
            f"{abs(rem['more_move_median_pct']):.1f}% further "
            f"{'downside' if down else 'upside'}{eta} left before it {turn}.")
    if nxt and nxt.get("target_median_price") is not None:
        parts.append(
            f"After those turns the median "
            f"{'rebound' if down else 'pullback'} was {nxt['pct_median']:.1f}% "
            f"over {nxt['days_median']:.0f} trading days; pairing each "
            f"episode's own depth with its own follow-on move projects a "
            f"median target near ${nxt['target_median_price']:,.2f}.")
    if upcoming_earnings_days is not None and 0 <= upcoming_earnings_days <= 7:
        parts.append(f"Earnings are {int(upcoming_earnings_days)} trading "
                     + _plural(upcoming_earnings_days, "day", "days")
                     + " away — a scheduled catalyst history cannot price.")
    if cohort.get("insufficient"):
        parts.append("Treat the zone as description, not evidence — the "
                     "sample is thin.")
    return " ".join(parts)


# ── the what-if: target vs stop from RIGHT HERE ─────────────────────────────

def what_if(pivots, dates, highs, lows, closes, *,
            target_pct, stop_pct,
            opens=None,
            min_move_pct: float = 15.0,     # accepted, deliberately unused
            earnings_dates=None,
            split_dates=None,
            cfg: dict | None = None) -> dict:
    """If the trade were entered at the current swing's depth, which side
    hit first — historically?

    Entry is simulated at each cohort leg's CROSSING of the current
    magnitude, at the price that was actually available there: the
    theoretical threshold when the bar traded through it, the session OPEN
    when the market gapped past it overnight (v4.51). That places the extra
    adverse move BEFORE the reversal inside the trade, which is the real
    risk of buying a falling knife and the reason this is not measured from
    the (unknowable in advance) bottom.

    The race runs bar by bar to the end of the FOLLOWING opposite swing —
    a bounce trade that outlives the bounce is a different trade. Both
    levels inside one daily bar is AMBIGUOUS: the intraday order is
    unknowable from daily data, and ambiguity is counted against the
    trade, never for it. On the entry bar itself only the adverse side can
    be scored (price crossed the entry moving adversely; the favorable
    side may have printed before entry existed).

    Episodes whose FOLLOWING leg is excluded for a corporate action or an
    incredible print are dropped rather than raced through bad prices, and
    the count is reported.

    `min_move_pct` is accepted for call-site symmetry and is deliberately
    ignored: the display filter must not define this population either.
    """
    c = _cfg(cfg)
    out: dict[str, Any] = {"ok": False, "reason": None,
                           "target_pct": _r(_num(target_pct), 1),
                           "stop_pct": _r(_num(stop_pct), 1)}
    t = _num(target_pct)
    s = _num(stop_pct)
    if t is None or s is None or t <= 0 or s <= 0:
        out["reason"] = "target and stop must both be positive percentages"
        return out
    if len(pivots) < 2 or len(closes) < 30:
        out["reason"] = "not enough history"
        return out
    last_pivot = pivots[-1]
    direction = "down" if last_pivot[2] == "low" else "up"
    start = next((p for p in reversed(pivots[:-1])
                  if p[2] == ("high" if direction == "down" else "low")), None)
    if start is None:
        out["reason"] = "no active swing"
        return out
    from_p = float(start[1])
    ext_p = float(last_pivot[1])
    ext_abs = abs((ext_p - from_p) / from_p * 100.0) if from_p > 0 else None
    if not ext_abs:
        out["reason"] = "degenerate swing"
        return out

    legs = build_legs(pivots, dates, highs, lows, closes,
                      split_dates=split_dates,
                      max_credible_day_move_pct=float(c["max_credible_day_move_pct"]))
    cohort = [L for L in legs
              if L["dir"] == direction and not L["excluded"]
              and L["abs_pct"] >= ext_abs - 1e-9]

    earn = set(earnings_dates or ())
    target_first = stop_first = ambiguous = neither = 0
    contaminated = gapped = 0
    days_to_t, days_to_s = [], []
    episodes = []
    for L in cohort:
        ci = crossing_index(L, highs, lows, ext_abs)
        if ci is None:
            continue
        nx = L.get("next")
        if nx is not None and nx.get("excluded"):
            # The path this trade would have lived through is not a price
            # series we trust. Racing through it would manufacture a result.
            contaminated += 1
            continue
        entry, gap = crossing_fill(L, opens, direction, ext_abs, ci)
        if gap:
            gapped += 1
        if direction == "down":            # long the bounce
            t_level = entry * (1.0 + t / 100.0)
            s_level = entry * (1.0 - s / 100.0)
        else:                              # short the pullback
            t_level = entry * (1.0 - t / 100.0)
            s_level = entry * (1.0 + s / 100.0)
        end_i = nx["end_i"] if nx else L["end_i"]
        verdict, vdays = None, None
        # Entry bar: only the adverse side can be judged.
        j = ci
        adverse_hit = (lows[j] <= s_level if direction == "down"
                       else highs[j] >= s_level)
        if adverse_hit:
            verdict, vdays = "stop", 0
        else:
            for j in range(ci + 1, end_i + 1):
                t_hit = (highs[j] >= t_level if direction == "down"
                         else lows[j] <= t_level)
                s_hit = (lows[j] <= s_level if direction == "down"
                         else highs[j] >= s_level)
                if t_hit and s_hit:
                    verdict, vdays = "ambiguous", j - ci
                    break
                if s_hit:
                    verdict, vdays = "stop", j - ci
                    break
                if t_hit:
                    verdict, vdays = "target", j - ci
                    break
        if verdict is None:
            verdict = "neither"
        if verdict == "target":
            target_first += 1
            days_to_t.append(vdays)
        elif verdict == "stop":
            stop_first += 1
            days_to_s.append(vdays)
        elif verdict == "ambiguous":
            ambiguous += 1
        else:
            neither += 1
        episodes.append({"date": dates[ci], "verdict": verdict, "days": vdays,
                         "entry": _r(entry), "gapped": bool(gap),
                         "earnings": bool(
                             _dates_between(earn, dates, ci, end_i))})
    n = len(episodes)
    if n == 0:
        out["reason"] = ("no clean historical swing ever reached the current "
                         "depth — nothing to race")
        out["excluded_contaminated"] = contaminated
        return out
    wi = wilson_interval(target_first, n) or {}
    out.update({
        "ok": True, "n": n,
        "direction": direction,
        "entered_at_abs_pct": _r(ext_abs, 1),
        "p_target_first_pct": _r(target_first / n * 100.0, 0),
        "p_stop_first_pct": _r(stop_first / n * 100.0, 0),
        "p_ambiguous_pct": _r(ambiguous / n * 100.0, 0),
        "p_neither_pct": _r(neither / n * 100.0, 0),
        "wilson_lo_target_pct": _r((wi.get("lo") or 0.0) * 100.0, 0),
        "median_days_to_target": _r(_median(days_to_t), 0),
        "median_days_to_stop": _r(_median(days_to_s), 0),
        "excluded_contaminated": contaminated,
        "gapped_entries": gapped,
        "episodes": episodes[-12:],
        "note": (f"Entry simulated the day each of the {n} comparable "
                 f"historical swings first reached {ext_abs:.1f}%, at the "
                 f"price actually available that day — the session open on "
                 f"the {gapped} occasion{'' if gapped == 1 else 's'} the "
                 f"market gapped through the level — then raced to the end of "
                 f"the following swing. Ambiguous means both levels printed "
                 f"inside one daily bar: the order is unknowable, and it is "
                 f"never counted in the trade's favor."
                 + (f" {contaminated} episode"
                    f"{'' if contaminated == 1 else 's'} dropped for a split "
                    f"or an incredible print in the follow-on leg."
                    if contaminated else "")),
    })
    return out


# ── walk-forward validation (offline; never a CI assertion) ─────────────────

STAGES = (0.25, 0.50, 0.75, 1.00, 1.25)


def validate(pivots, dates, highs, lows, closes, *,
             opens=None,
             min_move_pct: float = 15.0,
             cfg: dict | None = None,
             variant: str = "conditional",
             stages=STAGES,
             with_events: bool = False) -> dict:
    """Score the projection against what actually happened, chronologically,
    AT EVERY STAGE OF A DEVELOPING MOVE (v4.51).

    For each completed swing, and for each stage — a fraction of the PRIOR
    unconditional median swing size — stand at the bar that swing first
    reached that depth, build the projection from strictly earlier legs
    only, and compare with what the swing actually did next.

      variant "unconditional"  zone = prior rhythm p25/median/p75 (what the
                               tab projected before this engine existed)
      variant "conditional"    zone = final sizes of prior legs that reached
                               at least the stage depth
      variant "regime"         conditional, additionally same-regime at the
                               crossing when enough survive

    Reports, per stage: median absolute error of the projected reversal
    size, of the REMAINING move from that depth, and of the remaining days;
    plus p25–p75 coverage, which should sit near 50% — materially less is
    overconfident, materially more is vague. Run offline over real symbols;
    the CI tests exercise the arithmetic on frozen fixtures instead.
    """
    c = _cfg(cfg)
    legs = build_legs(pivots, dates, highs, lows, closes,
                      max_credible_day_move_pct=float(c["max_credible_day_move_pct"]))
    events = []
    for idx, L in enumerate(legs):
        if L["excluded"]:
            continue
        prior = [P for P in legs[:idx]
                 if P["dir"] == L["dir"] and not P["excluded"]]
        if len(prior) < 4:
            continue
        base_med = _median([P["abs_pct"] for P in prior])
        if not base_med:
            continue
        for frac in stages:
            alert_abs = base_med * float(frac)
            if alert_abs <= 0 or L["abs_pct"] < alert_abs:
                continue          # this swing never reached this stage
            ci = crossing_index(L, highs, lows, alert_abs)
            if ci is None:
                continue
            pool = [P for P in prior if P["abs_pct"] >= alert_abs - 1e-9]
            if variant == "unconditional":
                pool = prior
            elif variant == "floor15":
                # What the engine did before v4.51: the display filter also
                # gated the cohort. Kept as a validation variant so the
                # removal can be measured rather than asserted.
                pool = [P for P in pool
                        if P["abs_pct"] >= float(min_move_pct) - 1e-9]
            elif variant == "regime":
                reg = regime_at(closes, ci, int(c["regime_sma_n"]))
                if reg in ("uptrend", "downtrend"):
                    tagged = [P for P in pool
                              if (lambda cj: cj is not None and
                                  regime_at(closes, cj, int(c["regime_sma_n"])) == reg)
                              (crossing_index(P, highs, lows, alert_abs))]
                    if len(tagged) >= int(c["min_cohort_n"]):
                        pool = tagged
            if len(pool) < 3:
                continue
            sizes = [P["abs_pct"] for P in pool]
            p25, med, p75 = (_quantile(sizes, 0.25), _median(sizes),
                             _quantile(sizes, 0.75))
            rem, more = [], []
            for P in pool:
                cj = crossing_index(P, highs, lows, alert_abs)
                if cj is None:
                    continue
                rem.append(P["end_i"] - cj)
                ref, _g = crossing_fill(P, opens, P["dir"], alert_abs, cj)
                if ref and ref > 0:
                    more.append(abs(P["end_price"] / ref - 1.0) * 100.0)
            d25, dmed, d75 = (_quantile(rem, 0.25), _median(rem),
                              _quantile(rem, 0.75))
            mmed = _median(more)
            actual = L["abs_pct"]
            actual_days = L["end_i"] - ci
            aref, _g = crossing_fill(L, opens, L["dir"], alert_abs, ci)
            actual_more = (abs(L["end_price"] / aref - 1.0) * 100.0
                           if aref and aref > 0 else None)
            ev = {
                "stage": float(frac), "dir": L["dir"], "date": dates[ci],
                "n_pool": len(pool),
                # The outcome itself, so a harness can express the error
                # RELATIVE to the swing. Absolute percentage points are not
                # comparable across zigzag settings: a smaller threshold
                # makes smaller swings and shrinks the error for free.
                "actual": actual, "actual_days": actual_days,
                "size_err": abs(actual - med) if med is not None else None,
                "size_covered": (p25 is not None and p75 is not None
                                 and p25 - 1e-9 <= actual <= p75 + 1e-9),
                "more_err": (abs(actual_more - mmed)
                             if (mmed is not None and actual_more is not None)
                             else None),
                "days_err": (abs(actual_days - dmed)
                             if dmed is not None else None),
                "days_covered": (d25 is not None and d75 is not None
                                 and d25 - 1e-9 <= actual_days <= d75 + 1e-9),
            }
            nx = L.get("next")
            if nx and not nx.get("excluded"):
                nsizes = [P["next"]["abs_pct"] for P in pool
                          if P.get("next") and not P["next"].get("excluded")]
                if len(nsizes) >= 3:
                    q1, q3 = _quantile(nsizes, 0.25), _quantile(nsizes, 0.75)
                    nm = _median(nsizes)
                    ev["next_err"] = abs(nx["abs_pct"] - nm) if nm is not None else None
                    ev["next_covered"] = (q1 is not None and q3 is not None
                                          and q1 - 1e-9 <= nx["abs_pct"] <= q3 + 1e-9)
            events.append(ev)

    def _agg(evs, key_err, key_cov=None):
        errs = [e[key_err] for e in evs if e.get(key_err) is not None]
        covs = [e[key_cov] for e in evs if key_cov and key_cov in e]
        return {"mae": _r(_median(errs), 2) if errs else None,
                "coverage_pct": (_r(sum(covs) / len(covs) * 100.0, 0)
                                 if covs else None),
                "n": len(errs)}

    out = {"variant": variant, "events": len(events),
           "size": _agg(events, "size_err", "size_covered"),
           "more": _agg(events, "more_err"),
           "days": _agg(events, "days_err", "days_covered"),
           "next": _agg(events, "next_err", "next_covered"),
           "stages": {}}
    for frac in stages:
        sub = [e for e in events if abs(e["stage"] - float(frac)) < 1e-9]
        row = {"events": len(sub),
               "size": _agg(sub, "size_err", "size_covered"),
               "more": _agg(sub, "more_err"),
               "days": _agg(sub, "days_err", "days_covered"),
               "next": _agg(sub, "next_err", "next_covered")}
        for d in ("up", "down"):
            row[d] = _agg([e for e in sub if e["dir"] == d],
                          "size_err", "size_covered")
        out["stages"][f"{float(frac):.2f}"] = row
    for d in ("up", "down"):
        out[f"{d}_size"] = _agg([e for e in events if e["dir"] == d],
                                "size_err", "size_covered")
    if with_events:
        # Offline harnesses pool events across symbols before taking a
        # median; a median of per-symbol medians is not the same number.
        out["event_list"] = events
    return out
