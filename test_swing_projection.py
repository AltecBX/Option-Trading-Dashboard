"""Tests for the swing reversal projection — swing_projection.py and its
integration into swings.analyze().

These are not tests that the arithmetic runs; they are tests that it
cannot lie in the specific ways this feature could:

  a cohort that quietly includes the unfinished swing or excludes the
  swings that kept falling, remaining days computed as a difference of
  medians going negative, a next-swing table that drops small rebounds
  and flatters every bounce number, touch probabilities that are 100%
  by construction, context computed with future bars, a what-if race
  that resolves same-bar ambiguity in the trade's favor, an earnings
  gap quietly becoming evidence for an ordinary bounce.

Every fixture is synthetic and hand-built: pivots are constructed
directly (the engine's real input), bars are interpolated between them so
crossings land deterministically. Nothing touches a network.
"""
from __future__ import annotations

import math
import unittest

import swing_projection as sp


# ── fixture builders ────────────────────────────────────────────────────────

def build_series(legs, start_price=100.0, bar_frac=0.0005):
    """Construct (pivots, dates, highs, lows, closes) from a leg spec:
    [(signed_pct, days), ...] alternating in sign, first leg's direction
    taken from its sign. A trailing leg spec with days<0 marks the ACTIVE
    (in-progress) leg: its pivot is appended as the running extreme, the
    way the zigzag emits it. Bars interpolate geometrically between pivot
    prices, so the first bar at or past any intermediate level is exact."""
    prices = [start_price]
    idxs = [0]
    kinds = ["low" if legs[0][0] > 0 else "high"]
    for pct, days in legs:
        d = abs(days)
        p0 = prices[-1]
        p1 = p0 * (1.0 + pct / 100.0)
        idxs.append(idxs[-1] + d)
        prices.append(p1)
        kinds.append("high" if pct > 0 else "low")
    n = idxs[-1] + 1
    closes = [None] * n
    for k in range(len(idxs) - 1):
        i0, i1 = idxs[k], idxs[k + 1]
        p0, p1 = prices[k], prices[k + 1]
        for j in range(i0, i1 + 1):
            t = (j - i0) / (i1 - i0) if i1 > i0 else 0.0
            closes[j] = p0 * math.exp(t * math.log(p1 / p0))
    highs = [c * (1 + bar_frac) for c in closes]
    lows = [c * (1 - bar_frac) for c in closes]
    dates = []
    y, m, d0 = 2020, 1, 1
    for j in range(n):
        dates.append(f"{y:04d}-{m:02d}-{d0:02d}")
        d0 += 1
        if d0 > 28:
            d0 = 1
            m += 1
            if m > 12:
                m = 1
                y += 1
    pivots = [(idxs[k], prices[k], kinds[k]) for k in range(len(idxs))]
    return pivots, dates, highs, lows, closes


def down_history(sizes, bounce=25.0, active=-10.0):
    """A history of declines of the given sizes (each followed by a +bounce
    rally), ending in an ACTIVE decline of `active` percent. Leg durations
    are 10 bars each so crossings and remainders are easy to reason about."""
    legs = []
    for s in sizes:
        legs.append((-abs(s), 10))
        legs.append((bounce, 10))
    legs.append((active, 10))            # active leg, pivot = running extreme
    return build_series(legs)


class TestLegs(unittest.TestCase):

    def test_alternating_legs_and_the_active_leg_is_never_one(self):
        piv, dates, H, L, C = down_history([20, 30, 25])
        legs = sp.build_legs(piv, dates, H, L, C)
        # 3 declines + 3 bounces completed; the trailing active decline is
        # excluded — an unfinished move can never be a historical statistic.
        self.assertEqual(len(legs), 6)
        self.assertEqual([l["dir"] for l in legs],
                         ["down", "up", "down", "up", "down", "up"])

    def test_next_leg_chaining(self):
        piv, dates, H, L, C = down_history([20, 30])
        legs = sp.build_legs(piv, dates, H, L, C)
        self.assertIs(legs[0]["next"], legs[1])
        self.assertIsNone(legs[-1]["next"])

    def test_sizes_and_days_are_exact(self):
        piv, dates, H, L, C = down_history([20])
        legs = sp.build_legs(piv, dates, H, L, C)
        self.assertAlmostEqual(legs[0]["abs_pct"], 20.0, places=6)
        self.assertEqual(legs[0]["days"], 10)

    def test_a_leg_containing_a_split_date_is_excluded(self):
        piv, dates, H, L, C = down_history([20, 30, 25])
        legs = sp.build_legs(piv, dates, H, L, C)
        split_day = dates[legs[2]["start_i"] + 3]
        legs2 = sp.build_legs(piv, dates, H, L, C, split_dates={split_day})
        self.assertIsNone(legs2[0]["excluded"])
        self.assertIsNotNone(legs2[2]["excluded"])
        self.assertIn("split", legs2[2]["excluded"])

    def test_an_incredible_single_day_move_excludes_the_leg(self):
        piv, dates, H, L, C = down_history([20, 30, 25])
        j = 5                          # inside the first decline
        C2 = list(C)
        C2[j] = C2[j - 1] * 0.4        # a -60% day: not a price
        legs = sp.build_legs(piv, dates, H, L, C2)
        self.assertIsNotNone(legs[0]["excluded"])
        self.assertIn("single-day", legs[0]["excluded"])


class TestPointInTimeContext(unittest.TestCase):

    def test_range_position_endpoints(self):
        H = [float(10 + i) for i in range(40)]
        L = [h - 1.0 for h in H]
        win_hi = max(H[20:40])
        win_lo = min(L[20:40])
        self.assertAlmostEqual(
            sp.range_position(H, L, win_hi, 39, 20), 100.0, places=6)
        self.assertAlmostEqual(
            sp.range_position(H, L, win_lo, 39, 20), 0.0, places=6)

    def test_range_position_needs_a_full_window(self):
        H = [10.0] * 10
        L = [9.0] * 10
        self.assertIsNone(sp.range_position(H, L, 9.5, 9, 20))

    def test_range_position_is_point_in_time(self):
        """The value at bar i must equal the value computed on a series
        truncated at bar i — future bars can never leak in."""
        H = [10 + (i * 7 % 13) for i in range(60)]
        L = [h - 1.0 for h in H]
        C = [h - 0.5 for h in H]
        i = 39
        full = sp.range_position(H, L, C[i], i, 20)
        trunc = sp.range_position(H[:i + 1], L[:i + 1], C[i], i, 20)
        self.assertEqual(full, trunc)

    def test_regime_is_point_in_time_and_unknown_when_short(self):
        C = [100.0] * 199
        self.assertEqual(sp.regime_at(C, 198, 200), "unknown")
        C = [100.0] * 200 + [150.0]
        self.assertEqual(sp.regime_at(C, 200, 200), "uptrend")
        C2 = [100.0] * 200 + [50.0]
        self.assertEqual(sp.regime_at(C2, 200, 200), "downtrend")

    def test_atr_matches_hand_calculation(self):
        H = [11.0] * 20
        L = [9.0] * 20
        C = [10.0] * 20
        self.assertAlmostEqual(sp.atr_at(H, L, C, 19, 14), 2.0, places=9)


class TestCrossing(unittest.TestCase):

    def test_crossing_is_the_first_bar_at_the_threshold(self):
        piv, dates, H, L, C = down_history([20])
        leg = sp.build_legs(piv, dates, H, L, C)[0]
        ci = sp.crossing_index(leg, H, L, 10.0)
        thr = leg["start_price"] * 0.90
        self.assertLessEqual(L[ci], thr)
        self.assertTrue(all(L[j] > thr
                            for j in range(leg["start_i"] + 1, ci)))

    def test_no_crossing_when_the_leg_never_went_that_far(self):
        piv, dates, H, L, C = down_history([20])
        leg = sp.build_legs(piv, dates, H, L, C)[0]
        self.assertIsNone(sp.crossing_index(leg, H, L, 35.0))


class TestTheCohortIsASurvivalPopulation(unittest.TestCase):

    def test_only_swings_that_reached_the_current_depth_qualify(self):
        # Declines of 16, 22, 30, 40; active decline at -20%: cohort = the
        # three that reached 20%, never the 16% one.
        piv, dates, H, L, C = down_history([16, 22, 30, 40], active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["direction"], "down")
        self.assertEqual(out["cohort"]["n"], 3)
        self.assertEqual(out["cohort"]["n_direction"], 4)

    def test_the_zone_is_the_conditional_distribution(self):
        piv, dates, H, L, C = down_history([16, 22, 30, 40], active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        z = out["zone"]
        self.assertAlmostEqual(z["median_abs_pct"], 30.0, places=1)
        # Prices project from the ACTIVE swing's own start pivot.
        start = out["current"]["from_price"]
        self.assertAlmostEqual(z["median_price"], round(start * 0.70, 2),
                               places=2)

    def test_conditioning_is_on_the_extreme_not_the_close(self):
        # Active decline reached -20% then bounced to -14% (still inside the
        # same down leg). The cohort must condition on 20, not 14.
        legs = [(-16, 10), (25, 10), (-22, 10), (25, 10), (-30, 10),
                (25, 10), (-20, 10)]
        piv, dates, H, L, C = build_series(legs)
        # simulate the partial bounce: extend bars past the extreme, price
        # recovering, pivot stays the running low
        n0 = len(C)
        for k in range(1, 5):
            C.append(C[-1] * 1.018)
            H.append(C[-1] * 1.0005)
            L.append(C[-1] * 0.9995)
            dates.append(f"2021-12-{k:02d}")
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertAlmostEqual(out["current"]["extreme_abs_pct"], 20.0,
                               places=1)
        self.assertEqual(out["cohort"]["n"], 2)      # 22 and 30 reached 20
        self.assertLess(abs(out["current"]["pct"]),
                        out["current"]["extreme_abs_pct"])

    def test_a_swing_beyond_all_history_says_so_instead_of_inventing_a_zone(self):
        piv, dates, H, L, C = down_history([16, 18], active=-25.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertTrue(out["ok"])
        self.assertIsNone(out["zone"])
        self.assertIn("BEYOND ALL HISTORY", out["flags"])
        self.assertIn("cannot describe", out["summary"])

    def test_thin_cohorts_are_flagged_not_hidden(self):
        piv, dates, H, L, C = down_history([22, 30], active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertTrue(out["cohort"]["insufficient"])
        self.assertIn("THIN SAMPLE", out["flags"])
        self.assertIn("thin", out["summary"].lower())

    def test_min_cohort_n_reaches_the_arithmetic(self):
        piv, dates, H, L, C = down_history([22, 25, 28, 30, 32, 35, 40],
                                           active=-20.0)
        loose = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertFalse(loose["cohort"]["insufficient"])
        strict = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                            cfg={"min_cohort_n": 999})
        self.assertTrue(strict["cohort"]["insufficient"])


class TestRemaining(unittest.TestCase):

    def test_remaining_days_come_from_per_swing_remainders(self):
        # Every decline is 30% over 10 bars (log-linear), active at -20%:
        # each historical crossing of 20% sits ~6-7 bars in, so remaining
        # days must be ~3-4 — never negative, never a difference of medians.
        piv, dates, H, L, C = down_history([30, 30, 30], active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        rm = out["remaining"]
        self.assertGreaterEqual(rm["days_median"], 1)
        self.assertLessEqual(rm["days_median"], 5)

    def test_remaining_to_median_is_measured_from_the_current_price(self):
        piv, dates, H, L, C = down_history([30, 30, 30], active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        z, rm, cur = out["zone"], out["remaining"], out["current"]
        expect = (z["median_price"] - cur["price"]) / cur["price"] * 100.0
        self.assertAlmostEqual(rm["to_median_pct"], round(expect, 1),
                               places=1)

    def test_the_median_zone_is_never_behind_the_price(self):
        """Under the conditional cohort every member reached at least the
        current extreme, so the conditional median is at or beyond it BY
        CONSTRUCTION — price can never be past the median zone, and no
        'beyond the median' state exists to mislead. Depth relative to
        history is expressed as the share of swings already exceeded."""
        piv, dates, H, L, C = down_history([18, 20, 22, 40], active=-30.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertNotIn("beyond_median", out["remaining"])
        z, cur = out["zone"], out["current"]
        self.assertLessEqual(z["median_price"], cur["price"])
        self.assertGreaterEqual(
            out["cohort"]["share_of_history_already_exceeded_pct"], 75)
        self.assertTrue(any("DEEPER THAN" in f for f in out["flags"]))

    def test_the_reversed_within_ladder_counts_each_swing_once(self):
        piv, dates, H, L, C = down_history([21, 24, 30, 45], active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        for step in out["remaining"]["reversed_within"]:
            self.assertEqual(step["n"], out["remaining"]["n"])
            self.assertLessEqual(step["count"], step["n"])
        rates = [s["rate_pct"] for s in out["remaining"]["reversed_within"]]
        self.assertEqual(rates, sorted(rates))   # monotone in the ladder


class TestNextSwing(unittest.TestCase):

    def test_small_rebounds_are_not_dropped(self):
        """A +9% bounce after a -20% decline is below the 15% table filter
        but IS the honest outcome; dropping it would inflate every bounce
        statistic. build_series' active leg replaces it here with explicit
        legs: declines followed by 9/25/40 percent bounces."""
        legs = [(-20, 10), (9, 10), (-21, 10), (25, 10), (-22, 10), (40, 10),
                (-18, 10)]
        piv, dates, H, L, C = build_series(legs)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        nx = out["next"]
        self.assertEqual(nx["n"], 3)
        self.assertAlmostEqual(nx["pct_median"], 25.0, places=1)
        self.assertAlmostEqual(nx["pct_p25"], 17.0, places=0)

    def test_targets_are_anchored_at_the_median_zone_price(self):
        piv, dates, H, L, C = down_history([30, 30, 30], active=-20.0,
                                           bounce=25.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        z, nx = out["zone"], out["next"]
        self.assertAlmostEqual(nx["target_median_price"],
                               round(z["median_price"] * 1.25, 2), places=1)
        self.assertIn("reversal has to happen first", nx["target_basis"])

    def test_the_lifetime_touch_ladder_is_gone(self):
        """It was circular: a confirmed swing exceeds the zigzag threshold
        BY DEFINITION, so every level under it scored 100%. Fixed-horizon
        rates replaced it, and the old key must not come back."""
        piv, dates, H, L, C = down_history([30, 30, 30, 30, 30, 30, 30],
                                           active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         zigzag_pct=12.0)
        self.assertNotIn("touch", out["next"])
        self.assertNotIn("baseline", out["next"])
        self.assertTrue(out["next"]["horizon_touch"])

    def test_the_last_swings_unfinished_next_is_not_an_outcome(self):
        # The final decline's "next" is the active leg — unfinished, so it
        # must not enter the bounce statistics.
        piv, dates, H, L, C = down_history([20, 22, 25], active=-18.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertEqual(out["next"]["n"], out["next"]["n_with_next"])
        self.assertLessEqual(out["next"]["n"], 3)

    def test_wilson_bounds_ride_along(self):
        piv, dates, H, L, C = down_history([30] * 8, active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         zigzag_pct=12.0)
        for t in out["next"]["horizon_touch"]:
            self.assertIsNotNone(t["wilson_lo_pct"])
            self.assertLessEqual(t["wilson_lo_pct"], t["rate_pct"])


class TestEarnings(unittest.TestCase):

    def _series(self):
        return down_history([25, 26, 27, 28, 29, 30, 31, 32], active=-20.0)

    def test_contaminated_episodes_are_excluded_when_enough_clean_remain(self):
        piv, dates, H, L, C = self._series()
        legs = sp.build_legs(piv, dates, H, L, C)
        dirty_pivot = legs[0]["end_i"]
        earn = {dates[dirty_pivot]}
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         earnings_dates=earn, cfg={"min_clean_next_n": 3})
        nx = out["next"]
        self.assertEqual(nx["earnings_excluded"], 1)
        self.assertIn("excluded", nx["earnings_note"])

    def test_contamination_is_disclosed_when_excluding_would_starve_the_stats(self):
        piv, dates, H, L, C = self._series()
        legs = sp.build_legs(piv, dates, H, L, C)
        earn = {dates[l["end_i"]] for l in legs if l["dir"] == "down"}
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         earnings_dates=earn, cfg={"min_clean_next_n": 6})
        nx = out["next"]
        self.assertEqual(nx["earnings_excluded"], 0)
        self.assertGreater(nx["earnings_tagged"], 0)
        self.assertIn("included", nx["earnings_included_note"])

    def test_upcoming_earnings_flag(self):
        piv, dates, H, L, C = self._series()
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         upcoming_earnings_days=3)
        self.assertTrue(any("EARNINGS IN 3 DAYS" in f for f in out["flags"]))
        self.assertIn("Earnings are 3 trading days away", out["summary"])


class TestWhatIf(unittest.TestCase):

    def _race_series(self):
        """Two completed 20% declines each bouncing 25%, active at -15%.
        Crossings of 15% land mid-decline, so a tight stop loses the race
        to the remaining 5% of decline and a tight target wins after the
        bounce starts."""
        return down_history([20, 20, 20, 20], bounce=25.0, active=-15.0)

    def test_a_wide_stop_lets_the_target_win(self):
        piv, dates, H, L, C = self._race_series()
        out = sp.what_if(piv, dates, H, L, C, target_pct=8.0, stop_pct=30.0,
                         min_move_pct=15.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["p_target_first_pct"], 100.0)
        self.assertEqual(out["p_stop_first_pct"], 0.0)

    def test_a_tight_stop_dies_to_the_remaining_decline(self):
        piv, dates, H, L, C = self._race_series()
        out = sp.what_if(piv, dates, H, L, C, target_pct=8.0, stop_pct=2.0,
                         min_move_pct=15.0)
        self.assertEqual(out["p_stop_first_pct"], 100.0)

    def test_same_bar_ambiguity_is_never_counted_for_the_trade(self):
        # One completed decline; hand-build a bar after the crossing where
        # BOTH the +6% target and the -6% stop print.
        piv, dates, H, L, C = down_history([20, 20], active=-15.0)
        legs = sp.build_legs(piv, dates, H, L, C)
        leg = legs[0]
        ci = sp.crossing_index(leg, H, L, 15.0)
        entry = leg["start_price"] * 0.85
        H2, L2 = list(H), list(L)
        H2[ci + 1] = entry * 1.08
        L2[ci + 1] = entry * 0.92
        out = sp.what_if(piv, dates, H2, L2, C, target_pct=6.0, stop_pct=6.0,
                         min_move_pct=15.0)
        self.assertGreater(out["p_ambiguous_pct"], 0)
        self.assertIn("never counted", out["note"])

    def test_the_entry_bar_can_only_score_the_adverse_side(self):
        # Make the crossing bar itself plunge through the stop: stop-first
        # at day 0 — the favorable side may have printed before entry
        # existed, so it is not judged on that bar.
        piv, dates, H, L, C = down_history([20], active=-15.0)
        legs = sp.build_legs(piv, dates, H, L, C)
        leg = legs[0]
        ci = sp.crossing_index(leg, H, L, 15.0)
        L2 = list(L)
        entry = leg["start_price"] * 0.85
        L2[ci] = entry * 0.90
        out = sp.what_if(piv, dates, H, L2, C, target_pct=6.0, stop_pct=6.0,
                         min_move_pct=15.0)
        self.assertEqual(out["n"], 1)
        self.assertEqual(out["p_stop_first_pct"], 100.0)
        self.assertEqual(out["median_days_to_stop"], 0)

    def test_bad_inputs_are_refused(self):
        piv, dates, H, L, C = self._race_series()
        self.assertFalse(sp.what_if(piv, dates, H, L, C, target_pct=0,
                                    stop_pct=5, min_move_pct=15.0)["ok"])
        self.assertFalse(sp.what_if(piv, dates, H, L, C, target_pct=None,
                                    stop_pct=5, min_move_pct=15.0)["ok"])


class TestMirrorSymmetry(unittest.TestCase):

    def test_an_up_swing_mirrors_everything(self):
        legs = [(30, 10), (-20, 10), (32, 10), (-20, 10), (35, 10),
                (-20, 10), (25, 10)]
        piv, dates, H, L, C = build_series(legs)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["direction"], "up")
        z = out["zone"]
        start = out["current"]["from_price"]
        self.assertGreater(z["median_price"], start)
        self.assertEqual(out["next"]["kind"], "pullback")
        self.assertLess(out["next"]["target_median_price"],
                        z["median_price"])
        self.assertIn("topped", out["summary"])

    def test_up_swing_what_if_shorts_the_pullback(self):
        legs = [(30, 10), (-25, 10), (30, 10), (-25, 10), (30, 10),
                (-25, 10), (22, 10)]
        piv, dates, H, L, C = build_series(legs)
        out = sp.what_if(piv, dates, H, L, C, target_pct=8.0, stop_pct=30.0,
                         min_move_pct=15.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["direction"], "up")
        self.assertEqual(out["p_target_first_pct"], 100.0)


class TestCompletionIsNotAProbability(unittest.TestCase):

    def test_completion_and_reversal_rates_are_separate_fields(self):
        piv, dates, H, L, C = down_history([20, 20, 20, 40, 45, 50],
                                           active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        c = out["current"]
        # The move has completed ~2/3 of the median... completion says
        # nothing about reversal; the ladder is the reversal evidence and
        # lives in a different place with its own n.
        self.assertIsNotNone(c["move_completion_pct"])
        ladder = out["remaining"]["reversed_within"]
        self.assertTrue(ladder)
        for step in ladder:
            self.assertNotEqual(step["rate_pct"], c["move_completion_pct"])


class TestBaseline(unittest.TestCase):
    """The baseline exists to be subtracted from the conditional rate, so
    the two must ask the identical question: same percent, same horizon,
    same anchor (the bar's close)."""

    def _flat_then_pop(self):
        closes = [100.0] * 100 + [100.0 * (1.2 ** ((i + 1) / 10.0))
                                  for i in range(10)]
        return ([c * 1.001 for c in closes], [c * 0.999 for c in closes],
                closes)

    def test_only_bars_that_could_reach_it_count(self):
        highs, lows, closes = self._flat_then_pop()
        rate, n = sp.baseline_hit_rate(highs, lows, closes, 10.0, 10, "down")
        # Only the last ~10 flat bars are within 10 sessions of the pop.
        self.assertIsNotNone(rate)
        self.assertLess(rate, 20.0)
        self.assertEqual(n, len(closes) - 10)

    def test_an_incomplete_window_is_not_a_miss(self):
        highs, lows, closes = self._flat_then_pop()
        # The final bars have no 10-bar future: horizon_hit returns None and
        # baseline_hit_rate must not score them as failures.
        self.assertIsNone(sp.horizon_hit(highs, lows, closes[-1],
                                         len(closes) - 1, 10.0, 10, "down"))
        _rate, n = sp.baseline_hit_rate(highs, lows, closes, 10.0, 10, "down")
        self.assertEqual(n, len(closes) - 10)

    def test_conditional_and_baseline_use_the_same_pair(self):
        piv, dates, H, L, C = down_history([30] * 8, active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         zigzag_pct=12.0,
                         cfg={"touch_horizons": ((7.0, 6),)})
        t = out["next"]["horizon_touch"][0]
        self.assertEqual((t["pct"], t["days"]), (7.0, 6))
        base, n = sp.baseline_hit_rate(H, L, C, 7.0, 6, "down")
        self.assertEqual(t["baseline_pct"], base)
        self.assertEqual(t["baseline_n"], n)
        self.assertEqual(t["edge_pp"], round(t["rate_pct"] - base, 0))


class TestValidateWalkForward(unittest.TestCase):

    def test_events_only_use_prior_legs(self):
        """With declines growing over time, an early event's pool median
        must reflect only what came before it — the later, larger declines
        cannot leak backward."""
        sizes = [20, 21, 22, 23, 24, 40, 45, 50]
        piv, dates, H, L, C = down_history(sizes, active=-18.0)
        v = sp.validate(piv, dates, H, L, C, min_move_pct=15.0,
                        variant="conditional")
        self.assertGreater(v["events"], 0)
        self.assertIsNotNone(v["size"]["mae"])

    def test_variants_run(self):
        piv, dates, H, L, C = down_history([20, 22, 25, 28, 30, 33, 36, 40],
                                           active=-18.0)
        for variant in ("unconditional", "conditional", "regime"):
            v = sp.validate(piv, dates, H, L, C, min_move_pct=15.0,
                            variant=variant)
            self.assertEqual(v["variant"], variant)


class TestAnalyzeIntegration(unittest.TestCase):
    """swings.analyze() carries the reversal block additively — every
    pre-existing payload key intact, chart contract unchanged."""

    def _bars(self):
        piv, dates, H, L, C = down_history([22, 26, 30, 34], active=-20.0)
        return [{"date": dates[i], "open": C[i], "high": H[i], "low": L[i],
                 "close": C[i], "volume": 1000} for i in range(len(C))]

    def setUp(self):
        import swings
        self._swings = swings
        self._bench, self._earn = swings._fetch_bench, swings._fetch_earnings
        swings._fetch_bench = lambda period="1y": {}
        swings._fetch_earnings = lambda symbol: set()

    def tearDown(self):
        self._swings._fetch_bench = self._bench
        self._swings._fetch_earnings = self._earn

    def test_payload_keeps_every_existing_key_and_gains_reversal(self):
        res = self._swings.analyze("TEST", bars=self._bars())
        for key in ("symbol", "current_price", "swings", "down_swings",
                    "rhythm", "down_rhythm", "indicators", "analysis",
                    "projection", "bars", "as_of"):
            self.assertIn(key, res)
        rv = res["reversal"]
        self.assertTrue(rv["ok"])
        self.assertEqual(rv["direction"], "down")
        self.assertIsNotNone(rv["zone"])

    def test_what_if_params_flow_through(self):
        res = self._swings.analyze("TEST", bars=self._bars(),
                                   what_if_target_pct=8.0,
                                   what_if_stop_pct=30.0)
        wi = res["reversal"]["what_if"]
        self.assertTrue(wi["ok"])
        self.assertEqual(wi["target_pct"], 8.0)

    def test_split_dates_reach_the_engine(self):
        bars = self._bars()
        # a split inside the first decline: that leg leaves the cohort
        res0 = self._swings.analyze("TEST", bars=bars)
        res1 = self._swings.analyze("TEST", bars=bars,
                                    split_dates={bars[4]["date"]})
        self.assertEqual(res1["reversal"]["cohort"]["n"],
                         res0["reversal"]["cohort"]["n"] - 1)

    def test_summary_spells_out_dates(self):
        res = self._swings.analyze("TEST", bars=self._bars())
        s = res["reversal"]["summary"]
        self.assertNotRegex(s, r"\d{4}-\d{2}-\d{2}")
        self.assertRegex(s, r"(January|February|March|April|May|June|July|"
                            r"August|September|October|November|December) "
                            r"\d{1,2}, \d{4}")


class TestScannerRead(unittest.TestCase):
    """watchlist_table._swing_read carries the rz_ zone fields."""

    def test_bounce_candidate_fields(self):
        try:
            import watchlist_table as wt
        except Exception:
            self.skipTest("watchlist_table deps unavailable")
        if not getattr(wt, "_SWINGS_OK", False):
            self.skipTest("swings helpers unavailable")
        piv, dates, H, L, C = down_history([22, 26, 30, 34], active=-20.0)
        got = wt._swing_read(H, L, C, dates=dates)
        self.assertEqual(got.get("swing_dir"), "short")
        self.assertIsNotNone(got.get("rz_median_price"))
        self.assertIsNotNone(got.get("rz_dist_median_pct"))
        self.assertIsNotNone(got.get("rz_n"))
        self.assertIn("rz_in_zone", got)

    def test_pullback_candidate_fields(self):
        try:
            import watchlist_table as wt
        except Exception:
            self.skipTest("watchlist_table deps unavailable")
        if not getattr(wt, "_SWINGS_OK", False):
            self.skipTest("swings helpers unavailable")
        legs = [(30, 10), (-20, 10), (32, 10), (-20, 10), (35, 10),
                (-20, 10), (25, 10)]
        piv, dates, H, L, C = build_series(legs)
        got = wt._swing_read(H, L, C, dates=dates)
        self.assertEqual(got.get("swing_dir"), "long")
        self.assertIsNotNone(got.get("rz_next_target"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()


# ═══════════════════════════════════════════════════════════════════════════
#  v4.51 — the corrections pass
# ═══════════════════════════════════════════════════════════════════════════

class TestTheDisplayFilterNeverDefinesThePopulation(unittest.TestCase):
    """The 15% setting hides small swings from the TABLES. Using it as a
    statistical population deleted the shallow reversals — exactly the
    swings that ended early — and pushed every projected zone deeper."""

    def _mixed(self):
        # Declines of 12.5, 13, 14 (invisible to the table) and 16, 20, 30
        # (visible), each followed by a rally, then an active -12% decline.
        legs = []
        for s in (12.5, 13.0, 14.0, 16.0, 20.0, 30.0):
            legs += [(-s, 10), (25.0, 10)]
        legs.append((-12.0, 10))
        return build_series(legs)

    def test_a_12_percent_decline_keeps_the_shallow_declines(self):
        piv, dates, H, L, C = self._mixed()
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertEqual(out["cohort"]["n"], 6)
        # With the old floor the cohort was [16, 20, 30] and the median
        # bottom projected 20% down. Keeping 12.5/13/14 halves that.
        self.assertLess(out["zone"]["median_abs_pct"], 20.0)
        self.assertAlmostEqual(out["zone"]["median_abs_pct"], 15.0, places=0)

    def test_only_swings_that_never_reached_this_depth_are_excluded(self):
        legs = []
        for s in (8.0, 12.5, 13.0, 14.0, 16.0, 20.0, 30.0):
            legs += [(-s, 10), (25.0, 10)]
        legs.append((-12.0, 10))
        piv, dates, H, L, C = build_series(legs)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertEqual(out["cohort"]["n"], 6)          # the 8% is out
        self.assertEqual(out["cohort"]["n_direction"], 7)

    def test_changing_the_display_filter_does_not_move_the_cohort(self):
        piv, dates, H, L, C = self._mixed()
        a = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        b = sp.project(piv, dates, H, L, C, min_move_pct=25.0)
        c = sp.project(piv, dates, H, L, C, min_move_pct=1.0)
        for k in ("n", "share_of_history_already_exceeded_pct"):
            self.assertEqual(a["cohort"][k], b["cohort"][k])
            self.assertEqual(a["cohort"][k], c["cohort"][k])
        self.assertEqual(a["zone"], b["zone"])
        self.assertEqual(a["remaining"]["days_median"],
                         c["remaining"]["days_median"])
        self.assertFalse(a["cohort"]["filters"]["display_min_move_pct_applied"])
        # Only the visible-history block moves with the display filter.
        self.assertNotEqual(a["history"]["n"], b["history"]["n"])

    def test_the_only_floor_disclosed_is_the_zigzags_own(self):
        piv, dates, H, L, C = self._mixed()
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         zigzag_pct=12.0)
        # The active swing is 12% — exactly the sensitivity, so every
        # completed swing qualifies and the note says so rather than
        # implying the cohort was selective.
        self.assertIn("no completed swing is smaller than 12%",
                      out["cohort"]["floor_note"])

    def test_a_deeper_swing_discloses_that_the_display_filter_is_off(self):
        legs = []
        for s2 in (12.5, 13.0, 14.0, 16.0, 20.0, 30.0):
            legs += [(-s2, 10), (25.0, 10)]
        legs.append((-14.0, 10))
        piv, dates, H, L, C = build_series(legs)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         zigzag_pct=12.0)
        note = out["cohort"]["floor_note"]
        self.assertIn("12%", note)
        self.assertIn("15% display filter", note)
        self.assertEqual(out["cohort"]["n"], 4)   # 14, 16, 20 and 30

    def test_what_if_uses_the_same_unfiltered_population(self):
        piv, dates, H, L, C = self._mixed()
        proj = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        wi = sp.what_if(piv, dates, H, L, C, target_pct=10.0, stop_pct=5.0,
                        min_move_pct=15.0)
        self.assertEqual(wi["n"], proj["cohort"]["n"])


class TestReversalStatus(unittest.TestCase):
    """Status comes from the running EXTREME, so a stock that already
    reached its historical zone and bounced is not confused with one still
    falling into it."""

    def _zone(self, ext, cur, direction="down", lo=80.0, hi=90.0):
        return sp.zone_state(direction, ext, cur, lo, hi)

    def test_the_status_band_is_the_unconditional_one(self):
        """The survival cohort all ended at or beyond the current depth, so
        its shallow quartile sits at or past the extreme by construction —
        measuring status against it would make IN ZONE unreachable."""
        piv, dates, H, L, C = down_history([30, 32, 28, 31, 29, 33],
                                           active=-30.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        tz, z = out["typical_zone"], out["zone"]
        self.assertEqual(tz["n"], 6)                 # every completed decline
        self.assertLess(z["p25_abs_pct"] + 1e-9, 1e9)
        self.assertGreaterEqual(z["p25_abs_pct"],
                                out["current"]["extreme_abs_pct"])
        self.assertLess(tz["p25_abs_pct"], z["p25_abs_pct"])
        self.assertEqual(out["status"]["code"], "IN ZONE")

    def test_approaching(self):
        st = self._zone(ext=95.0, cur=95.0)
        self.assertEqual(st["code"], "APPROACHING")
        self.assertFalse(st["zone_touched"])

    def test_in_zone(self):
        st = self._zone(ext=85.0, cur=86.0)
        self.assertEqual(st["code"], "IN ZONE")
        self.assertTrue(st["extreme_in_zone"])
        self.assertAlmostEqual(st["off_extreme_pct"], 1.2, places=1)

    def test_bouncing_off_zone(self):
        st = self._zone(ext=84.0, cur=95.0)
        self.assertEqual(st["code"], "BOUNCING OFF ZONE")
        self.assertTrue(st["zone_touched"])
        self.assertFalse(st["current_in_zone"])
        self.assertGreater(st["off_extreme_pct"], 0)

    def test_fading_off_zone(self):
        st = self._zone(ext=116.0, cur=105.0, direction="up",
                        lo=110.0, hi=120.0)
        self.assertEqual(st["code"], "FADING OFF ZONE")
        self.assertTrue(st["zone_touched"])

    def test_beyond_typical_zone(self):
        st = self._zone(ext=70.0, cur=72.0)
        self.assertEqual(st["code"], "BEYOND TYPICAL ZONE")
        self.assertTrue(st["extreme_beyond_zone"])

    def test_no_confirmation_threshold_is_invented(self):
        """A cent above the band is already 'out of the band'. The zone is
        the yardstick; nothing else decides what counts as a reaction."""
        self.assertEqual(self._zone(ext=85.0, cur=90.01)["code"],
                         "BOUNCING OFF ZONE")
        self.assertEqual(self._zone(ext=85.0, cur=89.99)["code"], "IN ZONE")

    def test_the_extreme_can_be_in_the_zone_while_price_is_not(self):
        piv, dates, H, L, C = down_history([30, 32, 28, 31, 29, 33],
                                           active=-30.0)
        # Now let price recover 12% off that low WITHOUT confirming a new
        # pivot — the situation the whole status field exists for. The
        # pivot list is untouched; only bars are appended.
        last = C[-1]
        for k in range(1, 7):
            px = last * (1.0 + 0.12 * k / 6.0)
            C.append(px); H.append(px * 1.0005); L.append(px * 0.9995)
            dates.append(f"2031-01-{k:02d}")
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        st = out["status"]
        self.assertGreater(out["current"]["extreme_abs_pct"],
                           out["current"]["abs_pct"])
        self.assertGreater(st["off_extreme_pct"], 5.0)
        self.assertIn(st["code"], ("BOUNCING OFF ZONE", "IN ZONE",
                                   "BEYOND TYPICAL ZONE"))

    def test_beyond_history_has_no_band_at_all(self):
        piv, dates, H, L, C = down_history([20, 21, 22], active=-40.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertEqual(out["status"]["code"], "BEYOND HISTORY")
        self.assertIsNone(out["zone"])

    def test_the_status_is_not_duplicated_into_the_flags(self):
        """It has its own banner; a flag chip saying the same word is
        noise, and flags are reserved for conditions that change how much
        weight the projection deserves."""
        piv, dates, H, L, C = down_history([30] * 8, active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertNotIn(out["status"]["code"], out["flags"])


class TestPairedTargets(unittest.TestCase):
    """Each episode contributes its own (depth, follow-on) pair. A product
    of two medians describes an episode that never happened."""

    def _asymmetric(self):
        # The depth that sits in the MIDDLE of the depth distribution is
        # paired with an extreme follow-on move, so the median of the
        # projections cannot equal the projection of the two medians.
        legs = [(-20, 10), (50, 10), (-25, 10), (10, 10), (-30, 10), (60, 10),
                (-35, 10), (12, 10), (-40, 10), (55, 10), (-20, 10)]
        return build_series(legs)

    def test_pairs_are_preserved(self):
        piv, dates, H, L, C = self._asymmetric()
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        pairs = out["next"]["paired"]["pairs"]
        by_depth = {round(p["reversal_abs_pct"]): p["next_abs_pct"]
                    for p in pairs}
        self.assertAlmostEqual(by_depth[30], 60.0, places=0)
        self.assertAlmostEqual(by_depth[35], 12.0, places=0)

    def test_the_paired_median_differs_from_median_times_median(self):
        piv, dates, H, L, C = self._asymmetric()
        nx = sp.project(piv, dates, H, L, C, min_move_pct=15.0)["next"]
        self.assertTrue(nx["target_is_paired"])
        self.assertNotAlmostEqual(nx["target_median_price"],
                                  nx["target_simple_median_price"], places=1)

    def test_the_paired_target_is_the_primary_one(self):
        piv, dates, H, L, C = self._asymmetric()
        nx = sp.project(piv, dates, H, L, C, min_move_pct=15.0)["next"]
        self.assertEqual(nx["target_median_price"],
                         nx["paired"]["median_price"])
        self.assertIn("own follow-on move", nx["target_basis"])

    def test_each_projection_starts_from_todays_swing_origin(self):
        piv, dates, H, L, C = self._asymmetric()
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        origin = out["current"]["from_price"]
        for p in out["next"]["paired"]["pairs"]:
            expect = origin * (1 - p["reversal_abs_pct"] / 100.0)
            self.assertAlmostEqual(p["projected_reversal_price"], expect,
                                   places=1)


class TestGapThroughEntries(unittest.TestCase):
    """A level the market opened beyond never existed as a live price."""

    def test_a_gap_through_fills_at_the_open(self):
        leg = {"start_price": 100.0, "dir": "down", "start_i": 0, "end_i": 9}
        opens = [100.0] * 5 + [80.0] + [80.0] * 4
        px, gapped = sp.crossing_fill(leg, opens, "down", 10.0, 5)
        self.assertTrue(gapped)
        self.assertEqual(px, 80.0)

    def test_a_bar_that_traded_through_fills_at_the_level(self):
        leg = {"start_price": 100.0, "dir": "down", "start_i": 0, "end_i": 9}
        opens = [100.0] * 10
        px, gapped = sp.crossing_fill(leg, opens, "down", 10.0, 5)
        self.assertFalse(gapped)
        self.assertAlmostEqual(px, 90.0, places=6)

    def test_without_opens_the_gap_state_is_unknown_not_false(self):
        leg = {"start_price": 100.0, "dir": "down", "start_i": 0, "end_i": 9}
        px, gapped = sp.crossing_fill(leg, None, "down", 10.0, 5)
        self.assertIsNone(gapped)
        self.assertAlmostEqual(px, 90.0, places=6)

    def test_the_mirror_case_for_a_rally(self):
        leg = {"start_price": 100.0, "dir": "up", "start_i": 0, "end_i": 9}
        opens = [100.0] * 5 + [120.0] * 5
        px, gapped = sp.crossing_fill(leg, opens, "up", 10.0, 5)
        self.assertTrue(gapped)
        self.assertEqual(px, 120.0)

    def test_the_what_if_counts_its_gapped_fills(self):
        piv, dates, H, L, C = down_history([30] * 6, active=-20.0)
        opens = [c for c in C]
        wi = sp.what_if(piv, dates, H, L, C, opens=opens,
                        target_pct=10.0, stop_pct=5.0)
        self.assertIn("gapped_entries", wi)
        self.assertIn("gapped", wi["episodes"][0])

    def test_a_contaminated_following_leg_is_dropped_not_raced(self):
        piv, dates, H, L, C = down_history([30] * 6, active=-20.0)
        legs = sp.build_legs(piv, dates, H, L, C)
        # Declare a split in the MIDDLE of the second leg (the first
        # rally) — the follow-on path of the first decline. Not at its
        # start: that bar is the shared pivot, and excluding the decline
        # itself would prove nothing about the follow-on path.
        mid = (legs[1]["start_i"] + legs[1]["end_i"]) // 2
        split = {dates[mid]}
        clean = sp.what_if(piv, dates, H, L, C, target_pct=10.0, stop_pct=5.0)
        dirty = sp.what_if(piv, dates, H, L, C, target_pct=10.0, stop_pct=5.0,
                           split_dates=split)
        self.assertEqual(dirty["n"], clean["n"] - 1)
        self.assertEqual(dirty["excluded_contaminated"], 1)


class TestEarningsBetweenCrossingAndExtreme(unittest.TestCase):
    """The case the engine used to miss: a report that lands while the
    swing is already this deep, and takes it 25% lower."""

    def _series(self):
        return down_history([25, 26, 27, 28, 29, 30, 31, 32], active=-20.0)

    def _run_leg_dates(self, piv, dates, H, L, C, k):
        leg = [x for x in sp.build_legs(piv, dates, H, L, C)
               if x["dir"] == "down"][k]
        ci = sp.crossing_index(leg, H, L, 20.0)
        return dates[ci + 1]

    def test_a_report_during_the_run_contaminates_the_zone(self):
        piv, dates, H, L, C = self._series()
        earn = {self._run_leg_dates(piv, dates, H, L, C, k) for k in (0, 1)}
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         earnings_dates=earn)
        self.assertEqual(out["cohort"]["n_earnings_contaminated"], 2)
        self.assertTrue(out["cohort"]["earnings_excluded_applied"])
        self.assertEqual(out["cohort"]["n"], 6)
        self.assertIn("between this depth and the final turn",
                      out["cohort"]["earnings_note"])

    def test_it_actually_moves_the_zone(self):
        piv, dates, H, L, C = self._series()
        clean = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        earn = {self._run_leg_dates(piv, dates, H, L, C, k) for k in (6, 7)}
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         earnings_dates=earn)
        # Dropping the two DEEPEST declines has to make the zone shallower.
        self.assertLess(out["zone"]["median_abs_pct"],
                        clean["zone"]["median_abs_pct"])

    def test_too_few_clean_episodes_disclose_instead_of_excluding(self):
        piv, dates, H, L, C = self._series()
        earn = {self._run_leg_dates(piv, dates, H, L, C, k)
                for k in range(8)}
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         earnings_dates=earn)
        self.assertFalse(out["cohort"]["earnings_excluded_applied"])
        self.assertEqual(out["cohort"]["n"], 8)
        self.assertIn("too few to measure", out["cohort"]["earnings_note"])

    def test_the_source_of_the_dates_travels_with_them(self):
        piv, dates, H, L, C = self._series()
        earn = {self._run_leg_dates(piv, dates, H, L, C, 0)}
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         earnings_dates=earn,
                         earnings_meta={"label": "3 exact report dates plus "
                                                 "12 older SEC filing windows",
                                        "source": "yfinance + SEC"})
        self.assertIn("SEC filing windows", out["cohort"]["earnings_note"])
        self.assertEqual(out["cohort"]["earnings_source"], "yfinance + SEC")


class TestDeepEarningsHistory(unittest.TestCase):
    """The card reads ten years of prices; yfinance carries about four
    years of report dates. Absence of a date must not read as absence of a
    report, so SEC filing dates fill the gap — as windows, labelled."""

    def setUp(self):
        import options_dashboard as od
        self.od = od
        od._DEEP_EARN_CACHE.clear()
        self._gap = od._gap_earn_hist
        self._sec = od._sec_filing_dates

    def tearDown(self):
        self.od._gap_earn_hist = self._gap
        self.od._sec_filing_dates = self._sec
        self.od._DEEP_EARN_CACHE.clear()

    def test_the_two_sources_are_merged_and_counted(self):
        od = self.od
        od._gap_earn_hist = lambda s: {"2024-02-01", "2024-05-01"}
        od._sec_filing_dates = lambda s: ["2016-03-10", "2017-03-10",
                                          "2024-08-01"]
        out = od._deep_earn_hist("TEST")
        self.assertEqual(out["meta"]["n_reported"], 2)
        self.assertEqual(out["meta"]["n_proxy_filings"], 2)   # 2024-08 is new
        self.assertEqual(out["meta"]["earliest_exact"], "2024-02-01")
        self.assertIn("2024-02-01", out["dates"])
        self.assertIn("2016-03-10", out["dates"])

    def test_a_filing_date_becomes_a_window_not_a_day(self):
        od = self.od
        od._gap_earn_hist = lambda s: set()
        od._sec_filing_dates = lambda s: ["2016-03-10"]
        out = od._deep_earn_hist("TEST")
        self.assertIn("2016-03-10", out["dates"])
        self.assertIn("2016-03-04", out["dates"])       # release, days before
        self.assertNotIn("2016-03-11", out["dates"])    # never after
        self.assertEqual(out["meta"]["proxy_window_days"], 7)

    def test_exact_dates_win_where_they_exist(self):
        od = self.od
        od._gap_earn_hist = lambda s: {"2024-02-01"}
        od._sec_filing_dates = lambda s: ["2024-02-05", "2016-03-10"]
        out = od._deep_earn_hist("TEST")
        # The 2024 filing is inside the exact-coverage window, so it is not
        # re-expanded; only the pre-2024 one is.
        self.assertEqual(out["meta"]["n_proxy_filings"], 1)
        self.assertNotIn("2024-02-04", out["dates"])

    def test_a_failure_degrades_to_the_shallow_history(self):
        od = self.od
        od._gap_earn_hist = lambda s: {"2024-02-01"}

        def boom(_s):
            raise RuntimeError("SEC unavailable")
        od._sec_filing_dates = boom
        with self.assertRaises(RuntimeError):
            od._deep_earn_hist("TEST")     # the route wraps this in try/except

    def test_the_label_says_which_dates_are_proxies(self):
        od = self.od
        od._gap_earn_hist = lambda s: {"2024-02-01"}
        od._sec_filing_dates = lambda s: ["2016-03-10", "2017-03-10"]
        out = od._deep_earn_hist("TEST")
        self.assertIn("SEC filing windows", out["meta"]["label"])


class TestMaturityStages(unittest.TestCase):
    """Descriptive labels with walk-forward evidence behind the boundaries,
    never a multiplier on any number."""

    def test_early_in_the_move(self):
        piv, dates, H, L, C = down_history([30] * 8, active=-10.0)
        m = sp.project(piv, dates, H, L, C, min_move_pct=15.0)["maturity"]
        self.assertEqual(m["code"], "EARLY IN THE MOVE")
        self.assertIn("no more accurate here", m["note"])

    def test_at_its_normal_size(self):
        piv, dates, H, L, C = down_history([30] * 8, active=-31.0)
        m = sp.project(piv, dates, H, L, C, min_move_pct=15.0)["maturity"]
        self.assertEqual(m["code"], "AT ITS NORMAL SIZE")

    def test_beyond_its_normal_size(self):
        piv, dates, H, L, C = down_history([30] * 8, active=-39.0)
        m = sp.project(piv, dates, H, L, C, min_move_pct=15.0)["maturity"]
        self.assertEqual(m["code"], "BEYOND ITS NORMAL SIZE")

    def test_the_reference_is_the_unfiltered_population(self):
        legs = []
        for s in (13.0, 13.0, 30.0, 30.0):
            legs += [(-s, 10), (25.0, 10)]
        legs.append((-20.0, 10))
        piv, dates, H, L, C = build_series(legs)
        m = sp.project(piv, dates, H, L, C, min_move_pct=15.0)["maturity"]
        # Median of ALL four declines is ~21.5, not the ~30 the 15% table
        # would show.
        self.assertLess(m["ref_median_pct"], 25.0)

    def test_the_boundaries_are_configurable(self):
        piv, dates, H, L, C = down_history([30] * 8, active=-31.0)
        m = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                       cfg={"maturity_normal_pct": 200.0})["maturity"]
        self.assertEqual(m["code"], "EARLY IN THE MOVE")


class TestStagedValidation(unittest.TestCase):

    def test_every_stage_is_reported_separately(self):
        sizes = [20, 24, 22, 30, 26, 35, 28, 40, 25, 33]
        piv, dates, H, L, C = down_history(sizes, active=-18.0)
        v = sp.validate(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertEqual(set(v["stages"]),
                         {f"{s:.2f}" for s in sp.STAGES})
        deep = v["stages"]["1.25"]
        self.assertIn("size", deep)
        self.assertIn("more", deep)
        self.assertIn("days", deep)

    def test_a_shallow_stage_has_at_least_as_many_events_as_a_deep_one(self):
        sizes = [20, 24, 22, 30, 26, 35, 28, 40, 25, 33]
        piv, dates, H, L, C = down_history(sizes, active=-18.0)
        v = sp.validate(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertGreaterEqual(v["stages"]["0.25"]["events"],
                                v["stages"]["1.25"]["events"])

    def test_the_old_floored_cohort_is_available_as_a_variant(self):
        sizes = [20, 24, 22, 30, 26, 35, 28, 40, 25, 33]
        piv, dates, H, L, C = down_history(sizes, active=-18.0)
        v = sp.validate(piv, dates, H, L, C, min_move_pct=15.0,
                        variant="floor15")
        self.assertEqual(v["variant"], "floor15")
        self.assertGreater(v["events"], 0)

    def test_events_carry_their_stage(self):
        sizes = [20, 24, 22, 30, 26, 35, 28, 40, 25, 33]
        piv, dates, H, L, C = down_history(sizes, active=-18.0)
        v = sp.validate(piv, dates, H, L, C, min_move_pct=15.0,
                        with_events=True)
        self.assertTrue(all("stage" in e for e in v["event_list"]))


class TestBandPenetration(unittest.TestCase):
    """How far into the typical band the extreme actually travelled — a
    position, like the 20-day range position. It exists because "reached the
    band" is one bit that cannot tell a graze from a plunge."""

    def test_zero_at_the_near_edge_and_one_hundred_at_the_far_one(self):
        self.assertEqual(sp.zone_state("down", 90.0, 91.0, 80.0, 90.0)
                         ["band_penetration_pct"], 0.0)
        self.assertEqual(sp.zone_state("down", 80.0, 81.0, 80.0, 90.0)
                         ["band_penetration_pct"], 100.0)

    def test_it_goes_negative_before_the_band_and_past_100_beyond_it(self):
        self.assertLess(sp.zone_state("down", 95.0, 95.0, 80.0, 90.0)
                        ["band_penetration_pct"], 0)
        self.assertGreater(sp.zone_state("down", 76.0, 77.0, 80.0, 90.0)
                           ["band_penetration_pct"], 100)

    def test_a_rally_mirrors_it(self):
        self.assertEqual(sp.zone_state("up", 110.0, 109.0, 110.0, 120.0)
                         ["band_penetration_pct"], 0.0)
        self.assertEqual(sp.zone_state("up", 120.0, 119.0, 110.0, 120.0)
                         ["band_penetration_pct"], 100.0)
        self.assertLess(sp.zone_state("up", 105.0, 105.0, 110.0, 120.0)
                        ["band_penetration_pct"], 0)

    def test_a_degenerate_band_has_no_position_inside_it(self):
        self.assertIsNone(sp.zone_state("down", 85.0, 86.0, 85.0, 85.0)
                          ["band_penetration_pct"])

    def test_the_reaction_states_are_biased_to_the_shallow_edge(self):
        """The asymmetry the field exists to expose: with the SAME small
        bounce off the low, a graze reads as a reaction and a deep plunge
        reads as still in the zone — because leaving the band is a much
        bigger move from deep inside it."""
        graze = sp.zone_state("down", 90.0, 90.0 * 1.01, 80.0, 90.0)
        deep = sp.zone_state("down", 82.0, 82.0 * 1.01, 80.0, 90.0)
        self.assertEqual(graze["code"], "BOUNCING OFF ZONE")
        self.assertEqual(deep["code"], "IN ZONE")
        self.assertLess(graze["band_penetration_pct"],
                        deep["band_penetration_pct"])

    def test_it_travels_on_the_projection(self):
        piv, dates, H, L, C = down_history([30, 32, 28, 31, 29, 33],
                                           active=-30.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0)
        self.assertIsNotNone(out["status"]["band_penetration_pct"])


class TestAdaptiveThreshold(unittest.TestCase):
    """A single percentage cannot mean the same thing on a utility and on a
    small-cap rocket. These pin the scaling, the clamps, and the disclosure
    — not the accuracy, which measurement showed is unchanged."""

    def _walk(self, step_pct, n=400, start=100.0):
        """A series that travels `step_pct` per bar, alternating, so its
        20-day travel is predictable."""
        out, p, up = [start], start, True
        for i in range(n):
            p = p * (1 + (step_pct if up else -step_pct) / 100.0)
            out.append(p)
            if i % 20 == 19:
                up = not up
        return out

    def test_a_quiet_stock_gets_a_smaller_threshold_than_a_wild_one(self):
        quiet = sp.adaptive_zigzag_pct(self._walk(0.1), floor_pct=1.0)
        wild = sp.adaptive_zigzag_pct(self._walk(1.0), ceiling_pct=90.0)
        self.assertIsNotNone(quiet)
        self.assertLess(quiet, wild)

    def test_the_threshold_is_k_times_the_stocks_own_travel(self):
        c = self._walk(0.5)
        v = sp.typical_move_pct(c)
        got = sp.adaptive_zigzag_pct(c, k=2.5, floor_pct=0.0, ceiling_pct=99.0)
        self.assertAlmostEqual(got * 100.0, 2.5 * v, places=6)

    def test_the_clamps_bind(self):
        self.assertAlmostEqual(
            sp.adaptive_zigzag_pct(self._walk(0.05), floor_pct=6.0), 0.06, places=6)
        self.assertAlmostEqual(
            sp.adaptive_zigzag_pct(self._walk(3.0), ceiling_pct=18.0), 0.18, places=6)

    def test_the_multiplier_moves_the_clamps_too(self):
        """Otherwise 'Major' on a quiet stock would be pinned to the same
        floor as 'Sensitive' and the setting would do nothing."""
        c = self._walk(0.05)
        a = sp.adaptive_zigzag_pct(c, floor_pct=6.0, multiplier=1.0)
        b = sp.adaptive_zigzag_pct(c, floor_pct=6.0, multiplier=1.36)
        self.assertGreater(b, a)

    def test_too_little_history_measures_nothing(self):
        self.assertIsNone(sp.typical_move_pct([1.0, 2.0, 3.0]))
        self.assertIsNone(sp.adaptive_zigzag_pct([1.0, 2.0, 3.0]))

    def test_an_explicit_threshold_always_wins_and_says_so(self):
        z = sp.resolve_zigzag_pct(self._walk(0.5), explicit=0.12)
        self.assertEqual(z["pct"], 0.12)
        self.assertEqual(z["source"], "explicit")

    def test_no_history_falls_back_and_says_so(self):
        z = sp.resolve_zigzag_pct([1.0, 2.0, 3.0])
        self.assertEqual(z["source"], "fallback")
        self.assertAlmostEqual(z["pct"], 0.12, places=6)

    def test_the_sensitivity_settings_are_ordered(self):
        c = self._walk(0.5)
        lo = sp.resolve_zigzag_pct(c, sensitivity="sensitive")["pct"]
        mid = sp.resolve_zigzag_pct(c, sensitivity="standard")["pct"]
        hi = sp.resolve_zigzag_pct(c, sensitivity="major")["pct"]
        self.assertLess(lo, mid)
        self.assertLess(mid, hi)

    def test_an_unknown_sensitivity_is_standard(self):
        c = self._walk(0.5)
        self.assertEqual(sp.resolve_zigzag_pct(c, sensitivity="banana")["pct"],
                         sp.resolve_zigzag_pct(c, sensitivity="standard")["pct"])

    def test_the_display_filter_scales_with_the_threshold(self):
        """Left fixed at 15% it hid almost every completed decline on a quiet
        stock, so the chart looked like it had no down legs at all."""
        for sens in ("sensitive", "standard", "major"):
            z = sp.resolve_zigzag_pct(self._walk(0.5), sensitivity=sens)
            self.assertAlmostEqual(z["min_move_pct"],
                                   round(z["pct"] * 100.0 * 1.25, 1), places=1)

    def test_it_can_be_switched_off_by_config(self):
        z = sp.resolve_zigzag_pct(self._walk(0.5), cfg={"adaptive_zigzag": False})
        self.assertEqual(z["source"], "fallback")

    def test_the_resolution_is_fully_disclosed(self):
        z = sp.resolve_zigzag_pct(self._walk(0.5), sensitivity="major")
        for k in ("pct", "source", "multiplier", "typical_move_pct", "k",
                  "floor_pct", "ceiling_pct", "min_move_pct", "sensitivity"):
            self.assertIn(k, z)


class TestAnalyzeResolvesTheThreshold(unittest.TestCase):

    def setUp(self):
        import swings
        self.swings = swings
        self._b, self._e = swings._fetch_bench, swings._fetch_earnings
        swings._fetch_bench = lambda period="1y": {}
        swings._fetch_earnings = lambda symbol: set()

    def tearDown(self):
        self.swings._fetch_bench = self._b
        self.swings._fetch_earnings = self._e

    def _bars(self, step=0.5, n=600):
        p, up, out = 100.0, True, []
        for i in range(n):
            p = p * (1 + (step if up else -step) / 100.0)
            out.append({"date": f"2020-01-01", "open": p, "high": p * 1.005,
                        "low": p * 0.995, "close": p, "volume": 1})
            if i % 20 == 19:
                up = not up
        for i, b in enumerate(out):
            y, m, d = 2020 + i // 360, (i // 30) % 12 + 1, i % 28 + 1
            b["date"] = f"{y:04d}-{m:02d}-{d:02d}"
        return out

    def test_the_resolved_threshold_travels_in_the_payload(self):
        r = self.swings.analyze("TEST", bars=self._bars())
        z = r["params"]["zigzag"]
        self.assertIn(z["source"], ("adaptive", "fallback"))
        self.assertEqual(r["params"]["pct"], z["pct"])
        self.assertIsNotNone(r["params"]["min_move_pct"])

    def test_an_explicit_pct_is_still_honoured(self):
        r = self.swings.analyze("TEST", bars=self._bars(), pct=0.12)
        self.assertAlmostEqual(r["params"]["pct"], 0.12, places=6)
        self.assertEqual(r["params"]["zigzag"]["source"], "explicit")

    def test_an_explicit_display_filter_is_still_honoured(self):
        r = self.swings.analyze("TEST", bars=self._bars(), min_move_pct=25.0)
        self.assertEqual(r["params"]["min_move_pct"], 25.0)

    def test_the_sensitivity_name_changes_the_segmentation(self):
        bars = self._bars()
        a = self.swings.analyze("TEST", bars=bars, sensitivity="sensitive")
        b = self.swings.analyze("TEST", bars=bars, sensitivity="major")
        self.assertLess(a["params"]["pct"], b["params"]["pct"])


class TestTheUnbrokenZigzag(unittest.TestCase):
    """The chart's connector line should always alternate up, down, up. It
    used to be drawn from the display-filtered swing tables, so a run of
    small legs showed as a gap. These pin that the full leg list is present,
    contiguous, and — the part that matters — that it is presentation only."""

    def setUp(self):
        import swings
        self.swings = swings
        self._b, self._e = swings._fetch_bench, swings._fetch_earnings
        swings._fetch_bench = lambda period="1y": {}
        swings._fetch_earnings = lambda symbol: set()

    def tearDown(self):
        self.swings._fetch_bench = self._b
        self.swings._fetch_earnings = self._e

    def _bars(self):
        """Alternating legs of deliberately mixed size, so some fall under
        any sensible display filter and some do not."""
        legs = [(-20, 10), (25, 10), (-8, 6), (9, 6), (-30, 12), (35, 12),
                (-7, 5), (22, 10), (-18, 8)]
        piv, dates, H, L, C = build_series(legs)
        return [{"date": dates[i], "open": C[i], "high": H[i], "low": L[i],
                 "close": C[i], "volume": 1} for i in range(len(dates))]

    # An explicit threshold: the adaptive one would resolve high enough on
    # this synthetic series to merge the small legs away, and small legs are
    # the whole point of these tests.
    PCT = 0.05

    def test_the_legs_alternate_without_a_break(self):
        z = self.swings.analyze("TEST", bars=self._bars(), pct=self.PCT)["zigzag_legs"]
        self.assertGreater(len(z), 4)
        for i in range(len(z) - 1):
            self.assertNotEqual(z[i]["dir"], z[i + 1]["dir"])
            self.assertEqual(z[i]["end_date"], z[i + 1]["start_date"])

    def test_only_the_last_leg_is_the_unfinished_one(self):
        z = self.swings.analyze("TEST", bars=self._bars(), pct=self.PCT)["zigzag_legs"]
        self.assertTrue(z[-1]["active"])
        self.assertFalse(any(L["active"] for L in z[:-1]))

    def test_major_means_big_enough_for_the_tables(self):
        r = self.swings.analyze("TEST", bars=self._bars(), pct=self.PCT,
                                min_move_pct=15.0)
        for L in r["zigzag_legs"]:
            self.assertEqual(L["major"], abs(L["pct"]) >= 15.0)

    def test_the_small_legs_are_the_ones_the_tables_drop(self):
        r = self.swings.analyze("TEST", bars=self._bars(), pct=self.PCT,
                                min_move_pct=15.0)
        z = r["zigzag_legs"]
        major = [L for L in z if L["major"]]
        table = len(r["swings"]) + len(r["down_swings"])
        # Every leg the tables list is a major leg; the extras are exactly
        # the ones that used to leave gaps in the line.
        self.assertEqual(len(major), table)
        self.assertGreater(len(z), len(major))

    def test_the_display_filter_moves_the_flags_and_nothing_else(self):
        """The proof that this is presentation: change what the tables hide
        and the leg SET is identical — same count, same dates, same prices.
        Only which of them count as major moves."""
        bars = self._bars()
        a = self.swings.analyze("TEST", bars=bars, pct=self.PCT, min_move_pct=15.0)
        b = self.swings.analyze("TEST", bars=bars, pct=self.PCT, min_move_pct=5.0)
        za, zb = a["zigzag_legs"], b["zigzag_legs"]
        self.assertEqual(len(za), len(zb))
        for x, y in zip(za, zb):
            self.assertEqual((x["start_date"], x["end_date"], x["pct"]),
                             (y["start_date"], y["end_date"], y["pct"]))
        self.assertNotEqual([x["major"] for x in za], [y["major"] for y in zb])
        # and the projection itself is untouched by the display filter
        self.assertEqual(a["reversal"]["zone"], b["reversal"]["zone"])

    def test_it_is_not_consulted_by_the_projection(self):
        """A blunt guard: the engine module never mentions the chart's list."""
        with open("swing_projection.py", encoding="utf-8") as fh:
            self.assertNotIn("zigzag_legs", fh.read())
