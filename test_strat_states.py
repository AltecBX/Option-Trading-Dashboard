"""Tests for strat_states.py — the candle-state engine.

The properties worth protecting here are the ones that are easy to get
subtly wrong and impossible to notice afterwards: the equality convention,
ISO week bucketing across a year boundary, and the fact that a stored
reading is a pair of EXTREMES rather than a state (a stored state goes
stale the moment price makes a new high; stored extremes never do).
"""

import unittest
from datetime import date

import strat_states as ST


class TestTheFourStates(unittest.TestCase):
    def test_inside_takes_out_neither_extreme(self):
        self.assertEqual(ST.state_of(10, 5, 9, 6), "1")

    def test_up_exceeds_the_high_and_holds_the_low(self):
        self.assertEqual(ST.state_of(10, 5, 11, 6), "2U")

    def test_down_breaks_the_low_and_holds_the_high(self):
        self.assertEqual(ST.state_of(10, 5, 9, 4), "2D")

    def test_outside_takes_out_both(self):
        self.assertEqual(ST.state_of(10, 5, 11, 4), "3")

    def test_a_missing_number_is_no_state_rather_than_a_guess(self):
        for args in ((None, 5, 9, 6), (10, None, 9, 6),
                     (10, 5, None, 6), (10, 5, 9, None)):
            self.assertIsNone(ST.state_of(*args))

    def test_a_low_above_its_high_is_corrupt_not_exotic(self):
        self.assertIsNone(ST.state_of(5, 10, 9, 6))
        self.assertIsNone(ST.state_of(10, 5, 6, 9))


class TestTheEqualityConvention(unittest.TestCase):
    """Taking out an extreme means EXCEEDING it. The alternative — touching
    counts — puts a 3 on every flat, thin day, which is why this is pinned
    down by test rather than left to whichever comparison got typed."""

    def test_matching_the_prior_high_exactly_is_not_taking_it_out(self):
        self.assertEqual(ST.state_of(10, 5, 10, 6), "1")

    def test_matching_the_prior_low_exactly_is_not_breaking_it(self):
        self.assertEqual(ST.state_of(10, 5, 9, 5), "1")

    def test_matching_both_exactly_is_an_inside_bar_not_an_outside_one(self):
        self.assertEqual(ST.state_of(10, 5, 10, 5), "1")

    def test_a_hair_above_the_high_is_directional(self):
        self.assertEqual(ST.state_of(10, 5, 10.0001, 6), "2U")


class TestCalendarBucketing(unittest.TestCase):
    def test_the_five_period_keys(self):
        d = date(2026, 8, 21)
        self.assertEqual(ST.period_key(d, "D"), "2026-08-21")
        self.assertEqual(ST.period_key(d, "M"), "2026-08")
        self.assertEqual(ST.period_key(d, "Q"), "2026-Q3")
        self.assertEqual(ST.period_key(d, "Y"), "2026")

    def test_quarters_land_on_the_right_boundaries(self):
        self.assertEqual(ST.period_key(date(2026, 3, 31), "Q"), "2026-Q1")
        self.assertEqual(ST.period_key(date(2026, 4, 1), "Q"), "2026-Q2")
        self.assertEqual(ST.period_key(date(2026, 12, 31), "Q"), "2026-Q4")

    def test_a_week_spanning_new_year_stays_one_week(self):
        """The reason W uses ISO weeks. A home-rolled (year, week-of-year)
        pair splits the week containing January 1st in two and invents a
        one-day weekly bar every January."""
        dec31 = ST.period_key(date(2026, 12, 31), "W")   # Thursday
        jan1 = ST.period_key(date(2027, 1, 1), "W")      # Friday, same week
        self.assertEqual(dec31, jan1)

    def test_monday_starts_a_new_week(self):
        fri = ST.period_key(date(2026, 8, 21), "W")
        mon = ST.period_key(date(2026, 8, 24), "W")
        self.assertNotEqual(fri, mon)

    def test_an_unreadable_date_buckets_nowhere(self):
        self.assertIsNone(ST.period_key("not-a-date", "D"))
        self.assertIsNone(ST.period_key(None, "W"))

    def test_aggregate_takes_the_extremes_of_each_bucket(self):
        dates = ["2026-08-17", "2026-08-18", "2026-08-19",
                 "2026-08-20", "2026-08-21", "2026-08-24"]
        highs = [10, 12, 11, 9, 13, 8]
        lows = [5, 7, 4, 6, 8, 3]
        weeks = ST.aggregate(dates, highs, lows, "W")
        self.assertEqual(len(weeks), 2)
        self.assertEqual(weeks[0]["high"], 13)
        self.assertEqual(weeks[0]["low"], 4)
        self.assertEqual(weeks[0]["days"], 5)
        self.assertEqual(weeks[1]["days"], 1)

    def test_a_calendar_bar_is_not_a_rolling_window(self):
        """A weekly bar changes once a week. A rolling five-day high changes
        every day and would make the weekly state churn daily."""
        dates = ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"]
        one = ST.aggregate(dates, [10, 10, 10, 10], [5, 5, 5, 5], "W")
        two = ST.aggregate(dates[:3], [10, 10, 10], [5, 5, 5], "W")
        self.assertEqual(one[0]["key"], two[0]["key"])

    def test_nan_bars_are_dropped_not_bucketed(self):
        nan = float("nan")
        out = ST.aggregate(["2026-08-17", "2026-08-18"], [10, nan], [5, nan], "D")
        self.assertEqual(len(out), 1)

    def test_limit_keeps_the_newest_buckets(self):
        dates = [f"2026-0{m}-15" for m in range(1, 7)]
        out = ST.aggregate(dates, [10] * 6, [5] * 6, "M", limit=2)
        self.assertEqual([b["key"] for b in out], ["2026-05", "2026-06"])


class TestWhatGetsStored(unittest.TestCase):
    """`read` stores EXTREMES, not a state. A stored state is wrong the
    moment price makes a new high; stored extremes are re-read against a
    live quote and are never stale."""

    def setUp(self):
        self.dates = ["2026-08-17", "2026-08-18", "2026-08-19",
                      "2026-08-20", "2026-08-21"]
        self.highs = [10, 12, 11, 9, 13]
        self.lows = [5, 7, 4, 6, 8]
        self.stored = ST.read(self.dates, self.highs, self.lows)

    def test_every_timeframe_carries_both_periods_extremes(self):
        for tf in ST.TIMEFRAMES:
            e = self.stored[tf]
            self.assertIn("cur_high", e)
            self.assertIn("prev_high", e)
            self.assertIn("cur_key", e)

    def test_the_daily_reading_compares_the_last_two_sessions(self):
        d = self.stored["D"]
        self.assertEqual(d["cur_high"], 13)
        self.assertEqual(d["prev_high"], 9)
        self.assertEqual(d["state"], "2U")

    def test_a_timeframe_with_only_one_period_has_no_state(self):
        # All five sessions are in one month, so there is no prior month.
        self.assertIsNone(self.stored["M"]["prev_key"])
        self.assertIsNone(self.stored["M"]["state"])

    def test_empty_input_produces_nothing_rather_than_zeros(self):
        self.assertEqual(ST.read([], [], []), {})


class TestTheLiveMerge(unittest.TestCase):
    def setUp(self):
        self.stored = ST.read(
            ["2026-08-20", "2026-08-21"], [9, 13], [6, 8])

    def _keys(self, day):
        return {tf: ST.period_key(day, tf) for tf in ST.TIMEFRAMES}

    def test_a_live_high_through_the_prior_high_turns_it_directional(self):
        stored = ST.read(["2026-08-20", "2026-08-21"], [13, 12], [6, 8])
        self.assertEqual(stored["D"]["state"], "1")
        out = ST.live_read(stored, self._keys("2026-08-21"), 14, 8)
        self.assertEqual(out["D"]["state"], "2U")
        self.assertTrue(out["D"]["live"])

    def test_the_merge_is_idempotent(self):
        """A live high already inside the stored range changes nothing, so
        re-reading the same quote can never drift the state."""
        keys = self._keys("2026-08-21")
        once = ST.live_read(self.stored, keys, 12, 9)
        self.assertEqual(once["D"]["cur_high"], 13)
        self.assertEqual(once["D"]["cur_low"], 8)

    def test_a_new_session_rolls_the_stored_candle_into_the_prior_slot(self):
        out = ST.live_read(self.stored, self._keys("2026-08-24"), 14, 12)
        d = out["D"]
        self.assertTrue(d["rolled"])
        self.assertEqual(d["prev_high"], 13)      # was the current candle
        self.assertEqual(d["prev_low"], 8)
        self.assertEqual(d["cur_high"], 14)       # is now the live session
        self.assertEqual(d["state"], "2U")

    def test_a_new_period_with_no_quote_shows_the_last_settled_candle(self):
        """Rolling without a quote would blank the state, and there are
        ordinary reasons for no quote: a weekend, pre-market, a scan a day
        behind, a disconnected broker. In all of them the last settled
        candle is the right thing to show — labelled with its own date, not
        implied to be today's."""
        out = ST.live_read(self.stored, self._keys("2026-08-24"), None, None)
        d = out["D"]
        self.assertFalse(d["rolled"])
        self.assertTrue(d["stale"])
        self.assertEqual(d["as_of"], "2026-08-21")
        self.assertEqual(d["state"], self.stored["D"]["state"])
        self.assertFalse(d["live"])

    def test_the_current_periods_candle_is_not_marked_stale(self):
        out = ST.live_read(self.stored, self._keys("2026-08-21"), None, None)
        self.assertFalse(out["D"]["stale"])
        self.assertEqual(out["D"]["as_of"], "2026-08-21")

    def test_a_rollover_needs_a_quote_to_open_the_new_candle(self):
        out = ST.live_read(self.stored, self._keys("2026-08-24"), 14, 12)
        self.assertTrue(out["D"]["rolled"])
        self.assertFalse(out["D"]["stale"])
        self.assertEqual(out["D"]["as_of"], "2026-08-24")

    def test_without_a_quote_the_settled_reading_survives(self):
        out = ST.live_read(self.stored, self._keys("2026-08-21"), None, None)
        self.assertEqual(out["D"]["state"], self.stored["D"]["state"])
        self.assertFalse(out["D"]["live"])

    def test_an_in_progress_candle_is_never_reported_complete(self):
        out = ST.live_read(self.stored, self._keys("2026-08-21"), 14, 8)
        for tf in out:
            self.assertFalse(out[tf]["complete"])


class TestBreadth(unittest.TestCase):
    def test_unknown_states_are_counted_apart_from_classified_ones(self):
        t = ST.tally(["2U", "2D", None, "1", "nonsense"])
        self.assertEqual(t["n"], 3)
        self.assertEqual(t["unknown"], 2)

    def test_percentages_always_sum_to_exactly_one_hundred(self):
        """Segments that add to 99.9 leave a visible sliver at the end of the
        stacked bar, which reads as a data problem and is only arithmetic."""
        for counts in ([1, 1, 1, 0], [1, 1, 1, 1], [7, 3, 3, 3], [1, 0, 0, 0],
                       [5, 5, 5, 1], [11, 7, 5, 3], [1, 2, 3, 994]):
            t = dict(zip(ST.STATES, counts))
            t["n"] = sum(counts)
            pct = ST.percentages(t)
            self.assertAlmostEqual(sum(pct.values()), 100.0, places=6,
                                   msg=f"counts={counts} pct={pct}")

    def test_an_empty_population_is_all_zeros_not_a_division_error(self):
        self.assertEqual(ST.percentages({"n": 0}), {s: 0.0 for s in ST.STATES})

    def test_breadth_reports_every_timeframe(self):
        rows = [{"D": {"state": "2U"}, "W": {"state": "1"}},
                {"D": {"state": "2D"}, "W": {"state": "1"}}]
        b = ST.breadth(rows)
        self.assertEqual(b["D"]["counts"]["2U"], 1)
        self.assertEqual(b["W"]["counts"]["1"], 2)
        self.assertEqual(b["M"]["n"], 0)          # nothing supplied one


class TestDirectionalShare(unittest.TestCase):
    """The sector ranking measure. It deliberately ignores 1s and 3s: an
    inside bar has no direction, and calling a 3 up or down needs a close,
    which is a different measurement than this module makes."""

    def test_it_is_a_share_of_the_directional_names_only(self):
        counts = {"1": 50, "2U": 3, "2D": 1, "3": 50}
        self.assertEqual(ST.directional_share(counts), 75.0)

    def test_no_directional_names_is_none_not_fifty(self):
        self.assertIsNone(ST.directional_share({"1": 10, "3": 10, "2U": 0, "2D": 0}))


class TestContinuity(unittest.TestCase):
    def test_all_timeframes_up_is_agreement(self):
        row = {"D": {"state": "2U"}, "W": {"state": "2U"}, "M": {"state": "2U"}}
        self.assertEqual(ST.continuity(row)["aligned"], "up")

    def test_one_disagreeing_timeframe_is_mixed(self):
        row = {"D": {"state": "2U"}, "W": {"state": "2D"}, "M": {"state": "2U"}}
        self.assertEqual(ST.continuity(row)["aligned"], "mixed")

    def test_inside_bars_do_not_break_agreement_they_just_do_not_count(self):
        row = {"D": {"state": "2U"}, "W": {"state": "1"}, "M": {"state": "2U"}}
        c = ST.continuity(row)
        self.assertEqual(c["aligned"], "up")
        self.assertEqual(c["directional"], 2)

    def test_a_single_directional_timeframe_is_not_agreement(self):
        row = {"D": {"state": "2U"}, "W": {"state": "1"}, "M": {"state": "1"}}
        self.assertIsNone(ST.continuity(row)["aligned"])


if __name__ == "__main__":
    unittest.main()
