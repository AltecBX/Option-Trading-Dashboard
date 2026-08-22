"""swing_projection.py — where does this stock historically reverse from
HERE, how soon, and what usually happens afterward?

swings.py already answers "how far does this stock normally run?" — the
unconditional rhythm. This module answers the harder, conditional question
a trader at the hard right edge actually has:

    Given that the current swing has ALREADY travelled X%, where have
    swings that travelled at least X% historically ended, how many more
    days did they take from this exact point, and what did the opposite
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

CONDITION ON THE EXTREME, MEASURE FROM THE PRICE

  A stock that touched −22% and bounced to −18% HAS reached −22%; the
  candidate bottom is the extreme, not today's close. So cohort membership
  conditions on the running extreme of the current swing, while every
  distance shown to the user is measured from the CURRENT price — the
  number a trade would actually be entered at.

ONE DIRECTION OF TIME

  Every historical quantity at a swing's "crossing" — the first bar where
  that swing had travelled as far as the current one has — uses only bars
  at or before that crossing: the 20-day range position, the 200-day
  trend regime, the ATR. Remaining days are each swing's own crossing→end
  count, never a difference of medians (which can go negative). The
  in-progress final leg is never part of any statistic.

CHAIN OVER EVERYTHING, FILTER ONLY THE CURRENT DIRECTION

  The zigzag emits alternating legs; display tables keep only legs ≥ the
  min-move filter. The NEXT swing after a cohort member must be the actual
  next leg whatever its size — a +9% bounce after a −20% decline is the
  honest outcome, and dropping sub-filter rebounds would inflate every
  bounce statistic on the panel.

Pure math: stdlib only, plus wilson_interval from metrics.py (the same
interval the rest of the app uses — never reimplemented). No I/O, no
network, no clock. swings.py owns data and merges the result into its
payload under "reversal".
"""
from __future__ import annotations

from typing import Any

from metrics import wilson_interval

ENGINE_VERSION = "swing-projection-1.0.0"

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
    # Next-swing touch ladder, in percent from the reversal pivot. These
    # must sit ABOVE the zigzag sensitivity or they are circular: a
    # reversal only becomes a confirmed swing once the counter-move exceeds
    # the sensitivity threshold, so "P(bounce ≥ anything below it)" is 100%
    # by definition and says nothing. Levels at or under the active
    # threshold are dropped from the ladder and the floor is disclosed.
    "next_touch_ladder": (15.0, 20.0, 30.0),
    # Earnings contamination: a report within this many trading days of the
    # reversal pivot, or inside the next-swing window, tags the episode.
    "earnings_near_days": 2,
    # Clean episodes required before contaminated ones are excluded from
    # the next-swing statistics rather than merely disclosed.
    "min_clean_next_n": 6,
    # A single close-to-close day move beyond this is not a price; it is an
    # unadjusted corporate action or bad data. The leg containing it is
    # excluded and counted. Same convention as the rest of the app.
    "max_credible_day_move_pct": 50.0,
    # Context windows.
    "range_window": 20,
    "regime_sma_n": 200,
    "atr_n": 14,
    # Whether the cohort is additionally filtered to swings whose crossing
    # happened in the SAME trend regime as today. OFF by default; the
    # walk-forward validation decides whether it earns its place, and the
    # payload always discloses whether it was applied.
    "regime_filter": False,
}


def _cfg(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if k in out and v is not None:
            out[k] = v
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
    uptrend, below is a downtrend, not enough history is unknown. The point
    is only to stop repeated falling-knife episodes being averaged with
    ordinary pullbacks inside established uptrends — anything fancier is a
    modeling exercise this panel does not need."""
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
    window = set(dates[i0:i1 + 1])
    return any(d in window for d in date_strs)


# ── the projection ──────────────────────────────────────────────────────────

def project(pivots, dates, highs, lows, closes, *,
            min_move_pct: float = 15.0,
            zigzag_pct: float | None = None,
            earnings_dates=None,
            split_dates=None,
            upcoming_earnings_days=None,
            cfg: dict | None = None) -> dict:
    """The full reversal read for the swing in progress.

    Definitions, exactly:

      CURRENT SWING   from the last confirmed opposite pivot to today.
                      Its size for cohort purposes is the EXTREME's
                      distance from the start; its distances to the zone
                      are measured from the CURRENT close.
      COHORT          completed same-direction legs (≥ min_move_pct, not
                      excluded) whose extreme reached at least the current
                      extreme's magnitude. When fewer than min_cohort_n
                      such legs exist, the zone is still computed but
                      carries insufficient=True and the wording downgrades.
      ZONE            p25 / median / p75 of the cohort's FINAL sizes,
                      converted to prices from the current swing's start.
                      This is the conditional distribution — where swings
                      that got this far actually ended.
      REMAINING       per-cohort-leg: crossing (first bar at the current
                      magnitude) → end. Days and additional adverse move
                      are medians of those per-leg remainders.
      NEXT SWING      the actual next zigzag leg after each cohort leg,
                      whatever its size, when completed. Median/p25/p75 of
                      size and days; touch rates with Wilson bounds;
                      target prices anchored at the median zone price.
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

    legs = build_legs(pivots, dates, highs, lows, closes,
                      split_dates=split_dates,
                      max_credible_day_move_pct=float(c["max_credible_day_move_pct"]))
    same_dir = [L for L in legs if L["dir"] == direction]
    usable = [L for L in same_dir
              if not L["excluded"] and L["abs_pct"] >= float(min_move_pct)]
    excluded_n = sum(1 for L in same_dir if L["excluded"])

    # Unconditional history of this direction — the base the completion
    # metrics compare against (§ "how much of a NORMAL swing is done").
    all_sizes = [L["abs_pct"] for L in usable]
    all_days = [L["days"] for L in usable]

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
            "range_pos_20": _r(rp_now, 0),
            "regime": regime_now,
            "atr": _r(atr_now),
            "move_atr": (_r(abs(cur_price - from_p) / atr_now, 1)
                         if atr_now else None),
            # §24 — two completion measures, never combined, and never to be
            # read as a reversal probability (§25): a swing can be at 120%
            # of the median and keep falling; the cohort numbers below are
            # what say how often that happened.
            "move_completion_pct": (_r(ext_abs / _median(all_sizes) * 100.0, 0)
                                    if all_sizes and _median(all_sizes) else None),
            "time_completion_pct": (_r(days_active / _median(all_days) * 100.0, 0)
                                    if all_days and _median(all_days) else None),
        },
        "history": {
            "n": len(usable),
            "excluded": excluded_n,
            "pct_p25": _r(_quantile(all_sizes, 0.25), 1),
            "pct_median": _r(_median(all_sizes), 1),
            "pct_p75": _r(_quantile(all_sizes, 0.75), 1),
            "days_p25": _r(_quantile(all_days, 0.25), 0),
            "days_median": _r(_median(all_days), 0),
            "days_p75": _r(_quantile(all_days, 0.75), 0),
        },
    })
    if not usable:
        out["reason"] = ("no completed "
                         + ("declines" if direction == "down" else "rallies")
                         + f" of at least {min_move_pct:g}% in the history")
        return out

    # ── the cohort: reached at least the current extreme ────────────────
    cohort = [L for L in usable if L["abs_pct"] >= ext_abs - 1e-9]
    filters = {"direction": direction,
               "reached_at_least_pct": _r(ext_abs, 1),
               "regime": None}
    regime_applied = False
    if c.get("regime_filter") and regime_now in ("uptrend", "downtrend"):
        # Tag each cohort leg with the regime AT ITS CROSSING — the moment
        # it looked like now — and keep same-regime legs, but only when
        # enough survive. Falling back is disclosed, never silent.
        tagged = []
        for L in cohort:
            ci = crossing_index(L, highs, lows, ext_abs)
            if ci is not None and regime_at(closes, ci, int(c["regime_sma_n"])) == regime_now:
                tagged.append(L)
        if len(tagged) >= int(c["min_cohort_n"]):
            cohort = tagged
            filters["regime"] = regime_now
            regime_applied = True
    insufficient = len(cohort) < int(c["min_cohort_n"])

    share_exceeded = (sum(1 for L in usable if L["abs_pct"] < ext_abs - 1e-9)
                      / len(usable) * 100.0)

    out["cohort"] = {
        "n": len(cohort),
        "n_direction": len(usable),
        "filters": filters,
        "regime_filter_applied": regime_applied,
        "insufficient": insufficient,
        "min_n": int(c["min_cohort_n"]),
        "share_of_history_already_exceeded_pct": _r(share_exceeded, 0),
        "note": (None if not insufficient else
                 (f"Only {len(cohort)} completed "
                  + ("decline" if direction == "down" else "rally")
                  + ("" if len(cohort) == 1 else "s")
                  + f" ever reached {ext_abs:.1f}% — everything below is a "
                    f"description of those few, not a reliable zone.")),
    }

    if not cohort:
        out["ok"] = True
        out["zone"] = None
        out["remaining"] = None
        out["next"] = None
        out["summary"] = (
            f"This {('decline' if direction == 'down' else 'rally')} of "
            f"{ext_abs:.1f}% has gone further than every completed "
            f"{('decline' if direction == 'down' else 'rally')} in the "
            f"history ({len(usable)} of them; largest "
            f"{max(all_sizes):.1f}%). There is no historical zone ahead of "
            f"it — the stock is in territory its own past cannot describe.")
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
    for L in cohort:
        ci = crossing_index(L, highs, lows, ext_abs)
        if ci is None:
            continue
        rem_days.append(L["end_i"] - ci)
        thr_px = _threshold_price(L["start_price"], direction, ext_abs)
        if thr_px > 0:
            more = abs(L["end_price"] / thr_px - 1.0) * 100.0
            more_move.append(more)
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
        "beyond_median": bool(
            (direction == "down" and cur_price < zone["median_price"]) or
            (direction == "up" and cur_price > zone["median_price"])),
        "more_move_median_pct": _r(med_more, 1),
        "more_move_p25_pct": _r(_quantile(more_move, 0.25), 1),
        "more_move_p75_pct": _r(_quantile(more_move, 0.75), 1),
        "days_median": _r(_median(rem_days), 0),
        "days_p25": _r(_quantile(rem_days, 0.25), 0),
        "days_p75": _r(_quantile(rem_days, 0.75), 0),
        "n": len(rem_days),
        "reversed_within": ladder,
    }

    # ── the next opposite swing ─────────────────────────────────────────
    nxt_all = [L for L in cohort if L.get("next") is not None
               and not (L["next"].get("excluded"))]
    earn = set(earnings_dates or ())
    near_d = int(c["earnings_near_days"])
    clean, dirty = [], []
    for L in nxt_all:
        nx = L["next"]
        contaminated = (_date_near(earn, L["end_i"], dates, near_d)
                        or _dates_between(earn, dates, nx["start_i"], nx["end_i"]))
        (dirty if contaminated else clean).append(L)
    use = clean if len(clean) >= int(c["min_clean_next_n"]) else nxt_all
    excluded_earn = len(nxt_all) - len(use)

    nxt = None
    if use:
        sizes = [L["next"]["abs_pct"] for L in use]
        ndays = [L["next"]["days"] for L in use]
        nmed, n25, n75 = _median(sizes), _quantile(sizes, 0.25), _quantile(sizes, 0.75)
        anchor = zone["median_price"]      # target anchored at the median zone

        def _tgt(abs_pct):
            if anchor is None or abs_pct is None:
                return None
            return _r(anchor * (1.0 + abs_pct / 100.0) if direction == "down"
                      else anchor * (1.0 - abs_pct / 100.0))

        # The circularity guard (§ the zigzag floor): a reversal only
        # becomes a confirmed swing once the counter-move exceeds the
        # sensitivity threshold, so every completed next swing is at least
        # that large BY DEFINITION. Touch levels at or under the floor are
        # dropped rather than shown as impressive 100%s that mean nothing.
        floor = float(zigzag_pct) if zigzag_pct else 0.0
        touch = []
        for step in c["next_touch_ladder"]:
            if float(step) <= floor + 0.5:
                continue
            k = sum(1 for s in sizes if s >= float(step) - 1e-9)
            wi = wilson_interval(k, len(sizes)) or {}
            touch.append({"pct": float(step),
                          "rate_pct": _r(k / len(sizes) * 100.0, 0),
                          "wilson_lo_pct": _r((wi.get("lo") or 0.0) * 100.0, 0),
                          "n": len(sizes), "count": k})

        # Reclaim/revisit the 20-day extreme AS OF each reversal pivot —
        # point-in-time, so the level is the one a reader saw that evening.
        reclaim_k = reclaim_n = 0
        for L in use:
            piv = L["end_i"]
            w0 = max(0, piv - int(c["range_window"]) + 1)
            nx = L["next"]
            if L["dir"] == "down":
                level = max(highs[w0:piv + 1])
                hit = nx["end_price"] >= level or \
                    any(highs[j] >= level for j in range(nx["start_i"], nx["end_i"] + 1))
            else:
                level = min(lows[w0:piv + 1])
                hit = nx["end_price"] <= level or \
                    any(lows[j] <= level for j in range(nx["start_i"], nx["end_i"] + 1))
            reclaim_n += 1
            reclaim_k += 1 if hit else 0
        wir = (wilson_interval(reclaim_k, reclaim_n) or {}) if reclaim_n else {}
        if direction == "down":
            w0 = max(0, last_i - int(c["range_window"]) + 1)
            level_now = _r(max(highs[w0:last_i + 1]))
        else:
            w0 = max(0, last_i - int(c["range_window"]) + 1)
            level_now = _r(min(lows[w0:last_i + 1]))

        nxt = {
            "kind": "bounce" if direction == "down" else "pullback",
            "n": len(use), "n_with_next": len(nxt_all),
            "earnings_excluded": excluded_earn,
            "earnings_tagged": len(dirty),
            "earnings_note": (
                None if excluded_earn == 0 else
                f"{excluded_earn} episode{'' if excluded_earn == 1 else 's'} "
                f"with earnings at the reversal or inside the follow-on move "
                f"are excluded from these statistics."),
            "earnings_included_note": (
                None if (use is clean or not dirty) else
                f"{len(dirty)} of these episodes had earnings at the reversal "
                f"or inside the follow-on swing; excluding them would leave "
                f"too few to measure, so they are included and counted."),
            "pct_median": _r(nmed, 1), "pct_p25": _r(n25, 1), "pct_p75": _r(n75, 1),
            "days_median": _r(_median(ndays), 0),
            "days_p25": _r(_quantile(ndays, 0.25), 0),
            "days_p75": _r(_quantile(ndays, 0.75), 0),
            "target_median_price": _tgt(nmed),
            "target_p25_price": _tgt(n25),
            "target_p75_price": _tgt(n75),
            "target_basis": ("anchored at the median historical "
                             + ("bottom" if direction == "down" else "top")
                             + " — the reversal has to happen first"),
            "touch": touch,
            "touch_floor_note": (
                None if not floor else
                f"Any confirmed reversal is at least {floor:.0f}% by "
                f"definition — a counter-move smaller than the chart's "
                f"sensitivity threshold would not register as a swing at "
                f"all, so only levels above it are worth a probability."),
            "reclaim_20d": {"rate_pct": _r(reclaim_k / reclaim_n * 100.0, 0)
                            if reclaim_n else None,
                            "wilson_lo_pct": _r((wir.get("lo") or 0.0) * 100.0, 0),
                            "n": reclaim_n, "level_now": level_now},
        }
        # §20 baseline for the touch ladder: the chance of the same-size
        # move within the same horizon after ANY day, from non-overlapping
        # windows. Shown only beside touch rates — completion measures get
        # no baseline because none makes sense for them.
        horizon = int(_median(ndays) or 0)
        if horizon >= 1:
            nxt["baseline"] = {
                "horizon_days": horizon,
                "touch": [
                    {"pct": t["pct"],
                     "rate_pct": _baseline_touch(highs, lows, closes,
                                                 t["pct"], horizon, direction)}
                    for t in touch
                ],
            }
    out["next"] = nxt

    out["ok"] = True
    out["flags"] = _flags(out, upcoming_earnings_days)
    out["summary"] = _summary(out, upcoming_earnings_days)
    return out


def _baseline_touch(highs, lows, closes, pct, horizon_days, direction):
    """P(a move of `pct` percent in the NEXT-swing direction within
    `horizon_days`) measured from every non-overlapping historical day —
    the bar the conditional touch rates have to beat. Windows step by the
    horizon so one episode is never counted twice."""
    n = len(closes)
    hits = tries = 0
    i = 0
    while i + horizon_days < n:
        c0 = closes[i]
        if c0 and c0 > 0:
            if direction == "down":      # next swing is a bounce → upside touch
                level = c0 * (1.0 + pct / 100.0)
                hit = any(highs[j] >= level
                          for j in range(i + 1, i + 1 + horizon_days))
            else:
                level = c0 * (1.0 - pct / 100.0)
                hit = any(lows[j] <= level
                          for j in range(i + 1, i + 1 + horizon_days))
            tries += 1
            hits += 1 if hit else 0
        i += horizon_days
    return _r(hits / tries * 100.0, 0) if tries else None


def _flags(out, upcoming_earnings_days):
    flags = []
    cur = out.get("current") or {}
    cohort = out.get("cohort") or {}
    rem = out.get("remaining") or {}
    if cohort.get("insufficient"):
        flags.append("THIN SAMPLE")
    if rem and rem.get("beyond_median"):
        flags.append("BEYOND THE MEDIAN ZONE")
    share = cohort.get("share_of_history_already_exceeded_pct")
    if share is not None and share >= 75:
        flags.append(f"DEEPER THAN {share:.0f}% OF HISTORY")
    if upcoming_earnings_days is not None and 0 <= upcoming_earnings_days <= 7:
        flags.append(f"EARNINGS IN {int(upcoming_earnings_days)} DAY"
                     + ("" if upcoming_earnings_days == 1 else "S"))
    if cur.get("regime") == "downtrend" and out.get("direction") == "down":
        flags.append("DOWNTREND — declines have historically run further in downtrends"
                     if (out.get("cohort") or {}).get("regime_filter_applied")
                     else "DOWNTREND")
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
    when the evidence is thin."""
    d = out["direction"]
    cur = out["current"]
    zone = out.get("zone")
    rem = out.get("remaining")
    nxt = out.get("next")
    cohort = out.get("cohort") or {}
    word = "down" if d == "down" else "up"
    turn = "bottomed" if d == "down" else "topped"
    parts = [
        f"{'Down' if d == 'down' else 'Up'} "
        f"{abs(cur['pct']):.1f}% over {cur['days']} trading day"
        f"{'' if cur['days'] == 1 else 's'} from the "
        f"{_pretty_date(cur['from_date'])} swing "
        f"{'high' if d == 'down' else 'low'}."
    ]
    if not zone or not rem:
        parts.append("No completed historical swing has gone this far, so "
                     "there is no zone to project.")
        return " ".join(parts)
    n = cohort.get("n") or 0
    noun = ("decline" if d == "down" else "rally") if n == 1 else \
        ("declines" if d == "down" else "rallies")
    qual = ("Only " if cohort.get("insufficient") else "") \
        + f"{n} historical {noun} reached at least {cur['extreme_abs_pct']:.1f}%"
    parts.append(
        f"{qual}; they {turn} at a median of {zone['median_abs_pct']:.1f}% "
        f"(typically {zone['p25_abs_pct']:.1f}%–{zone['p75_abs_pct']:.1f}%), "
        f"putting the median historical "
        f"{'bottom' if d == 'down' else 'top'} near "
        f"${zone['median_price']:,.2f} with a typical zone of "
        f"${zone['band_low_price']:,.2f}–${zone['band_high_price']:,.2f}.")
    if rem.get("beyond_median"):
        parts.append(
            f"Price (${cur['price']:,.2f}) is already "
            f"{'below' if d == 'down' else 'above'} the median "
            f"{'bottom' if d == 'down' else 'top'} — this swing has outrun "
            f"its own median precedent.")
    elif rem.get("to_median_pct") is not None:
        eta = (f" and about {rem['days_median']:.0f} more trading day"
               f"{'' if rem['days_median'] == 1 else 's'}"
               if rem.get("days_median") is not None else "")
        parts.append(
            f"The median {'bottom' if d == 'down' else 'top'} is "
            f"{abs(rem['to_median_pct']):.1f}% "
            f"{'below' if d == 'down' else 'above'} the current price"
            f"{eta}, based on where those same swings stood at this depth.")
    if nxt and nxt.get("pct_median") is not None:
        parts.append(
            f"After they {turn}, the median "
            f"{'rebound' if d == 'down' else 'pullback'} was "
            f"{nxt['pct_median']:.1f}% (typically {nxt['pct_p25']:.1f}%–"
            f"{nxt['pct_p75']:.1f}%) over a median {nxt['days_median']:.0f} "
            f"trading days — from the median "
            f"{'bottom' if d == 'down' else 'top'} that projects to about "
            f"${nxt['target_median_price']:,.2f}.")
    if upcoming_earnings_days is not None and 0 <= upcoming_earnings_days <= 7:
        parts.append(f"Earnings are {int(upcoming_earnings_days)} trading day"
                     + ("" if upcoming_earnings_days == 1 else "s")
                     + " away — a scheduled catalyst history cannot price.")
    if cohort.get("insufficient"):
        parts.append("Treat the zone as description, not evidence — the "
                     "sample is thin.")
    return " ".join(parts)


# ── the what-if: target vs stop from RIGHT HERE ─────────────────────────────

def what_if(pivots, dates, highs, lows, closes, *,
            target_pct, stop_pct,
            min_move_pct: float = 15.0,
            split_dates=None,
            cfg: dict | None = None) -> dict:
    """If the trade were entered at the current swing's depth, which side
    hit first — historically?

    Entry is simulated at each cohort leg's CROSSING of the current
    magnitude, at the threshold price — the moment and level where that
    historical swing looked like this one does now. That places the extra
    adverse move BEFORE the reversal inside the trade, which is the real
    risk of buying a falling knife and the reason this is not measured
    from the (unknowable in advance) bottom.

    The race runs bar by bar to the end of the FOLLOWING opposite swing —
    a bounce trade that outlives the bounce is a different trade. Both
    levels inside one daily bar is AMBIGUOUS: the intraday order is
    unknowable from daily data, and ambiguity is counted against the
    trade, never for it. On the entry bar itself only the adverse side can
    be scored (price crossed the entry moving adversely; the favorable
    side may have printed before entry existed).
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
              and L["abs_pct"] >= float(min_move_pct)
              and L["abs_pct"] >= ext_abs - 1e-9]

    target_first = stop_first = ambiguous = neither = 0
    days_to_t, days_to_s = [], []
    episodes = []
    for L in cohort:
        ci = crossing_index(L, highs, lows, ext_abs)
        if ci is None:
            continue
        entry = _threshold_price(L["start_price"], direction, ext_abs)
        if direction == "down":            # long the bounce
            t_level = entry * (1.0 + t / 100.0)
            s_level = entry * (1.0 - s / 100.0)
        else:                              # short the pullback
            t_level = entry * (1.0 - t / 100.0)
            s_level = entry * (1.0 + s / 100.0)
        nx = L.get("next")
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
        episodes.append({"date": dates[ci], "verdict": verdict,
                         "days": vdays})
    n = len(episodes)
    if n == 0:
        out["reason"] = ("no historical swing ever reached the current depth "
                         "— nothing to race")
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
        "episodes": episodes[-12:],
        "note": (f"Entry simulated the day each of the {n} comparable "
                 f"historical swings first reached {ext_abs:.1f}%, raced to "
                 f"the end of the following swing. Ambiguous means both "
                 f"levels printed inside one daily bar — the order is "
                 f"unknowable, and it is never counted in the trade's "
                 f"favor."),
    })
    return out


# ── walk-forward validation (offline; never a CI assertion) ─────────────────

def validate(pivots, dates, highs, lows, closes, *,
             min_move_pct: float = 15.0,
             cfg: dict | None = None,
             variant: str = "conditional") -> dict:
    """Score the projection against what actually happened, chronologically.

    For each completed swing (after enough prior history exists), stand at
    its crossing of the PRIOR unconditional median — a natural alert
    moment — build the projection from strictly earlier legs only, then
    compare with the swing's actual end and the actual next swing.

      variant "unconditional"  zone = prior rhythm p25/median/p75 (what
                               the tab effectively projects today)
      variant "conditional"    zone = final sizes of prior legs that
                               reached at least the alert magnitude
      variant "regime"         conditional, additionally same-regime at
                               the crossing when enough survive

    Reports median absolute errors and band coverage; a p25–p75 band
    should cover ~50% of outcomes — materially less is overconfident,
    materially more is vague. Run offline over real symbols; the CI tests
    exercise the arithmetic on frozen fixtures instead.
    """
    c = _cfg(cfg)
    legs = build_legs(pivots, dates, highs, lows, closes,
                      max_credible_day_move_pct=float(c["max_credible_day_move_pct"]))
    events = []
    for idx, L in enumerate(legs):
        if L["excluded"] or L["abs_pct"] < float(min_move_pct):
            continue
        prior = [P for P in legs[:idx]
                 if P["dir"] == L["dir"] and not P["excluded"]
                 and P["abs_pct"] >= float(min_move_pct)]
        if len(prior) < 4:
            continue
        alert_abs = _median([P["abs_pct"] for P in prior])
        if alert_abs is None or L["abs_pct"] < alert_abs:
            continue                      # this swing never triggered an alert
        ci = crossing_index(L, highs, lows, alert_abs)
        if ci is None:
            continue
        pool = [P for P in prior if P["abs_pct"] >= alert_abs - 1e-9]
        if variant == "unconditional":
            pool = prior
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
        rem = []
        for P in pool:
            cj = crossing_index(P, highs, lows, alert_abs)
            if cj is not None:
                rem.append(P["end_i"] - cj)
        d25, dmed, d75 = (_quantile(rem, 0.25), _median(rem),
                          _quantile(rem, 0.75))
        actual = L["abs_pct"]
        actual_days = L["end_i"] - ci
        ev = {
            "dir": L["dir"], "date": dates[ci], "n_pool": len(pool),
            "size_err": abs(actual - med) if med is not None else None,
            "size_covered": (p25 is not None and p75 is not None
                             and p25 - 1e-9 <= actual <= p75 + 1e-9),
            "days_err": (abs(actual_days - dmed)
                         if dmed is not None else None),
            "days_covered": (d25 is not None and d75 is not None
                             and d25 - 1e-9 <= actual_days <= d75 + 1e-9),
        }
        nx = L.get("next")
        if nx and not nx.get("excluded"):
            nsizes = []
            for P in pool:
                pn = P.get("next")
                if pn and not pn.get("excluded"):
                    nsizes.append(pn["abs_pct"])
            if len(nsizes) >= 3:
                q1, q3 = _quantile(nsizes, 0.25), _quantile(nsizes, 0.75)
                nm = _median(nsizes)
                ev["next_err"] = abs(nx["abs_pct"] - nm) if nm is not None else None
                ev["next_covered"] = (q1 is not None and q3 is not None
                                      and q1 - 1e-9 <= nx["abs_pct"] <= q3 + 1e-9)
        events.append(ev)

    def _agg(key_err, key_cov):
        errs = [e[key_err] for e in events if e.get(key_err) is not None]
        covs = [e[key_cov] for e in events if key_cov in e]
        return {"mae": _r(_median(errs), 2) if errs else None,
                "coverage_pct": (_r(sum(covs) / len(covs) * 100.0, 0)
                                 if covs else None),
                "n": len(errs)}

    out = {"variant": variant, "events": len(events),
           "size": _agg("size_err", "size_covered"),
           "days": _agg("days_err", "days_covered"),
           "next": _agg("next_err", "next_covered")}
    for d in ("up", "down"):
        sub = [e for e in events if e["dir"] == d]
        errs = [e["size_err"] for e in sub if e.get("size_err") is not None]
        covs = [e["size_covered"] for e in sub]
        out[f"{d}_size"] = {"mae": _r(_median(errs), 2) if errs else None,
                            "coverage_pct": (_r(sum(covs) / len(covs) * 100.0, 0)
                                             if covs else None),
                            "n": len(sub)}
    return out
