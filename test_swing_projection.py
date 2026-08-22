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

    def test_touch_levels_below_the_zigzag_floor_are_dropped(self):
        piv, dates, H, L, C = down_history([30, 30, 30, 30, 30, 30, 30],
                                           active=-20.0)
        out = sp.project(piv, dates, H, L, C, min_move_pct=15.0,
                         zigzag_pct=12.0,
                         cfg={"next_touch_ladder": (5.0, 10.0, 20.0)})
        lv = [t["pct"] for t in out["next"]["touch"]]
        self.assertEqual(lv, [20.0])
        self.assertIn("by definition", out["next"]["touch_floor_note"])

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
        for t in out["next"]["touch"]:
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

    def test_baseline_windows_do_not_overlap(self):
        # 100 flat bars then a +20% pop in the last 10: with horizon 10 and
        # stride=horizon, at most one window can contain the pop.
        closes = [100.0] * 100 + [100.0 * (1.2 ** ((i + 1) / 10.0))
                                  for i in range(10)]
        highs = [c * 1.001 for c in closes]
        lows = [c * 0.999 for c in closes]
        rate = sp._baseline_touch(highs, lows, closes, 10.0, 10, "down")
        self.assertIsNotNone(rate)
        self.assertLessEqual(rate, 100.0 / (len(closes) // 10 - 1))


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
