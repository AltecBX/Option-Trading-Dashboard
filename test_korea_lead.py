"""Tests for KOREA LEAD — korea_lead_engine.py and korea_lead.py.

The point of most of these is not that the arithmetic is right. It is that
the arithmetic cannot quietly start reading the future. A Korean session
dated D is matched to the U.S. session dated D because Korea closed first;
shift that by one in either direction and the study either measures
yesterday's news or tomorrow's, and both mistakes produce a plausible
number rather than an error. So the alignment is tested by construction:
fixtures are built where the U.S. gap is a deterministic function of ONE
specific day's Korean move, and the tests assert which day the engine
found.

Every fixture is synthetic and every dependency is injected. Nothing here
touches a network.
"""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import korea_lead as kl
import korea_lead_engine as kle

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


# ── fixture builders ────────────────────────────────────────────────────────

# The fixtures are anchored to their LAST session rather than their first,
# because what most of these tests turn on is whether the newest bar is the
# one the clock says it should be. A series built forwards from a fixed
# start lands wherever the length puts it, which is how the live case —
# Korea's newest bar IS today's Seoul session — went untested.
FIXTURE_END = "2026-03-04"          # a Wednesday; Seoul's date at NOW below


def weekdays_back(n, end=FIXTURE_END):
    """`n` weekday sessions ending on `end`, oldest first."""
    d = date.fromisoformat(end)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def kbars(moves, base=1000.0, end=FIXTURE_END):
    """Korean daily bars whose close-to-close returns are exactly `moves`
    (percent), one per weekday session, ENDING on `end`."""
    days = weekdays_back(len(moves) + 1, end)
    bars = [{"date": days[0].isoformat(), "open": base, "high": base,
             "low": base, "close": base}]
    px = base
    for d, m in zip(days[1:], moves):
        px = px * (1.0 + m / 100.0)
        bars.append({"date": d.isoformat(), "open": px, "high": px,
                     "low": px, "close": px})
    return bars


def ubars(gaps, o2c=None, base=100.0, end=FIXTURE_END):
    """U.S. daily bars with exactly the requested opening gaps (percent from
    the prior close) and, optionally, open-to-close moves. Ends on `end`."""
    o2c = o2c or [0.0] * len(gaps)
    days = weekdays_back(len(gaps) + 1, end)
    bars = [{"date": days[0].isoformat(), "open": base, "high": base,
             "low": base, "close": base}]
    prev = base
    for d, g, c in zip(days[1:], gaps, o2c):
        op = prev * (1.0 + g / 100.0)
        cl = op * (1.0 + c / 100.0)
        bars.append({"date": d.isoformat(), "open": op,
                     "high": max(op, cl), "low": min(op, cl), "close": cl})
        prev = cl
    return bars


def dates_of(bars):
    return [b["date"] for b in bars]


# ── the three return definitions ────────────────────────────────────────────

class TestReturnDefinitions(unittest.TestCase):
    """Each of the four measurements, checked against arithmetic done by
    hand — and against each other, because the gap and the full day differ
    by exactly the move after the open and nothing else."""

    def test_close_to_close_is_close_over_prior_close(self):
        bars = [{"date": "2026-01-05", "open": 1, "high": 1, "low": 1, "close": 100.0},
                {"date": "2026-01-06", "open": 1, "high": 1, "low": 1, "close": 103.0}]
        got = kle.close_to_close(bars)
        self.assertAlmostEqual(got["2026-01-06"], 3.0, places=9)

    def test_the_first_session_has_no_return_at_all(self):
        """It is absent, not zero. A session with no prior close did not
        finish flat — it simply cannot be measured."""
        bars = kbars([1.0, 2.0])
        got = kle.close_to_close(bars)
        self.assertNotIn(bars[0]["date"], got)
        self.assertEqual(len(got), 2)

    def test_opening_gap_is_open_over_prior_close(self):
        bars = [{"date": "2026-01-05", "open": 90, "high": 101, "low": 89, "close": 100.0},
                {"date": "2026-01-06", "open": 98.0, "high": 99, "low": 97, "close": 99.0}]
        m = kle.us_measures(bars)["2026-01-06"]
        self.assertAlmostEqual(m["opening_gap"], -2.0, places=9)

    def test_open_to_close_is_close_over_same_day_open(self):
        bars = [{"date": "2026-01-05", "open": 90, "high": 101, "low": 89, "close": 100.0},
                {"date": "2026-01-06", "open": 98.0, "high": 104, "low": 97, "close": 102.9}]
        m = kle.us_measures(bars)["2026-01-06"]
        self.assertAlmostEqual(m["open_to_close"], 5.0, places=9)

    def test_full_day_is_close_over_prior_close(self):
        bars = [{"date": "2026-01-05", "open": 90, "high": 101, "low": 89, "close": 100.0},
                {"date": "2026-01-06", "open": 98.0, "high": 104, "low": 97, "close": 102.9}]
        m = kle.us_measures(bars)["2026-01-06"]
        self.assertAlmostEqual(m["full_day"], 2.9, places=9)

    def test_the_gap_and_the_move_after_it_compose_into_the_full_day(self):
        """(1+gap)(1+open_to_close) = 1+full_day. If this ever fails, two of
        the three are being measured from different prices."""
        bars = ubars([-3.2, 1.7, 0.4], o2c=[2.5, -1.1, 0.9])
        for m in kle.us_measures(bars).values():
            composed = (1 + m["opening_gap"] / 100.0) * (1 + m["open_to_close"] / 100.0)
            self.assertAlmostEqual(composed, 1 + m["full_day"] / 100.0, places=9)

    def test_a_session_missing_an_open_is_dropped_not_guessed(self):
        bars = [{"date": "2026-01-05", "open": 90, "high": 101, "low": 89, "close": 100.0},
                {"date": "2026-01-06", "open": None, "high": 104, "low": 97, "close": 102.0},
                {"date": "2026-01-07", "open": 101.0, "high": 104, "low": 97, "close": 103.0}]
        got = kle.us_measures(bars)
        self.assertNotIn("2026-01-06", got)
        # and the day after still measures against the day before it in the
        # SERIES, which is the last bar present — not against a hole
        self.assertIn("2026-01-07", got)

    def test_a_move_no_adjusted_series_should_print_is_excluded(self):
        """A 2-for-1 split left unadjusted looks like a −50% overnight move.
        It is dropped and never averaged into anything."""
        bars = [{"date": "2026-01-05", "open": 200, "high": 205, "low": 195, "close": 200.0},
                {"date": "2026-01-06", "open": 100.0, "high": 101, "low": 99, "close": 100.0}]
        self.assertNotIn("2026-01-06", kle.us_measures(bars))
        self.assertNotIn("2026-01-06", kle.close_to_close(
            [{"date": "2026-01-05", "open": 1, "high": 1, "low": 1, "close": 200.0},
             {"date": "2026-01-06", "open": 1, "high": 1, "low": 1, "close": 100.0}]))

    def test_a_large_but_real_earnings_gap_survives(self):
        """+37% is the biggest genuine overnight move measured across this
        feature's target list. The credibility limit must not eat it."""
        bars = [{"date": "2026-01-05", "open": 100, "high": 101, "low": 99, "close": 100.0},
                {"date": "2026-01-06", "open": 137.0, "high": 140, "low": 136, "close": 138.0}]
        m = kle.us_measures(bars)
        self.assertIn("2026-01-06", m)
        self.assertAlmostEqual(m["2026-01-06"]["opening_gap"], 37.0, places=9)


# ── alignment: the whole premise ────────────────────────────────────────────

class TestAlignment(unittest.TestCase):
    """Korean session D against U.S. session D, with nothing shifted."""

    def test_the_same_calendar_date_is_matched(self):
        k = kbars([1.0, -2.0, 3.0])
        u = ubars([0.5, -0.4, 0.9])
        got = kle.align(kle.close_to_close(k), kle.us_measures(u))
        self.assertEqual([o["date"] for o in got["observations"]],
                         dates_of(k)[1:])

    def test_the_engine_finds_the_same_day_relationship_not_the_next_day(self):
        """The fixture is built so the U.S. gap is exactly one third of the
        SAME day's Korean move. Correct alignment recovers a perfect
        correlation; any shift does not."""
        moves = [2.4, -1.8, 3.6, -0.9, 1.2, -3.3, 0.6, 2.7, -2.1, 1.5]
        k = kbars(moves)
        u = ubars([m / 3.0 for m in moves])
        obs = kle.align(kle.close_to_close(k), kle.us_measures(u))["observations"]
        self.assertAlmostEqual(kle.pearson([o["korea"] for o in obs],
                                           [o["opening_gap"] for o in obs]),
                               1.0, places=9)

    def test_a_lagged_or_lookahead_pairing_does_not_reproduce_it(self):
        """Deliberately mis-pair the two series by one session in each
        direction. Neither may come back as the perfect relationship the
        correct pairing finds — this is the tripwire that a future change
        to the matching rule has to survive."""
        moves = [2.4, -1.8, 3.6, -0.9, 1.2, -3.3, 0.6, 2.7, -2.1, 1.5]
        korea = kle.close_to_close(kbars(moves))
        us = kle.us_measures(ubars([m / 3.0 for m in moves]))
        kdays = sorted(korea)
        for shift in (1, -1):
            shifted = {}
            for i, d in enumerate(kdays):
                j = i + shift
                if 0 <= j < len(kdays):
                    shifted[kdays[j]] = korea[d]
            obs = kle.align(shifted, us)["observations"]
            r = kle.pearson([o["korea"] for o in obs],
                            [o["opening_gap"] for o in obs])
            self.assertLess(abs(r), 0.99,
                            f"a shift of {shift} sessions still looked perfect")

    def test_a_us_holiday_skips_the_korean_session_and_never_rolls_it(self):
        """Korea traded on a day New York was shut. That observation is
        dropped and counted — it must not reappear attached to the next
        U.S. session, which is a different day's news."""
        k = kbars([1.0, 2.0, 3.0, 4.0])
        u = ubars([0.1, 0.2, 0.3, 0.4])
        udays = dates_of(u)
        missing = udays[2]                       # a U.S. holiday
        u = [b for b in u if b["date"] != missing]
        got = kle.align(kle.close_to_close(k), kle.us_measures(u))
        self.assertNotIn(missing, [o["date"] for o in got["observations"]])
        self.assertEqual(got["skipped"]["korea_only"], 1)
        # the session AFTER the holiday still carries its own Korean move
        after = [o for o in got["observations"] if o["date"] == udays[3]]
        self.assertEqual(len(after), 1)
        self.assertAlmostEqual(after[0]["korea"],
                               kle.close_to_close(k)[udays[3]], places=9)

    def test_a_korean_holiday_skips_the_us_session(self):
        k = kbars([1.0, 2.0, 3.0, 4.0])
        u = ubars([0.1, 0.2, 0.3, 0.4])
        skip = dates_of(k)[2]
        k = [b for b in k if b["date"] != skip]
        got = kle.align(kle.close_to_close(k), kle.us_measures(u))
        self.assertNotIn(skip, [o["date"] for o in got["observations"]])
        self.assertGreaterEqual(got["skipped"]["us_only"], 1)

    def test_nothing_dated_after_through_is_ever_built(self):
        k = kbars([1.0, 2.0, 3.0, 4.0])
        u = ubars([0.1, 0.2, 0.3, 0.4])
        cut = dates_of(u)[2]
        got = kle.align(kle.close_to_close(k), kle.us_measures(u), through=cut)
        self.assertTrue(all(o["date"] <= cut for o in got["observations"]))
        self.assertEqual(got["skipped"]["after_through"], 2)

    def test_a_repeated_date_is_one_session_not_two(self):
        """A feed that re-publishes a settled bar has not invented a second
        trading day. The later copy wins and the count does not double."""
        u = ubars([1.0, 2.0])
        dup = dict(u[-1])
        dup["close"] = dup["close"] * 1.05      # the re-published copy
        got = kle.us_measures(u + [dup])
        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(got[dup["date"]]["close"], dup["close"], places=9)

    def test_extras_ride_along_on_the_matched_dates(self):
        k = kbars([1.0, -2.0])
        s = kbars([4.0, -5.0])
        obs = kle.align(kle.close_to_close(k), kle.us_measures(ubars([0.5, 0.5])),
                        extras={"samsung": kle.close_to_close(s)})["observations"]
        self.assertAlmostEqual(obs[0]["samsung"], 4.0, places=6)
        self.assertAlmostEqual(obs[1]["samsung"], -5.0, places=6)

    def test_a_missing_extra_is_none_not_zero(self):
        k = kbars([1.0, -2.0])
        obs = kle.align(kle.close_to_close(k),
                        kle.us_measures(ubars([0.5, 0.5])),
                        extras={"hynix": {}})["observations"]
        self.assertIsNone(obs[0]["hynix"])


# ── correlation and rates ───────────────────────────────────────────────────

class TestStatistics(unittest.TestCase):

    def test_pearson_against_a_hand_computed_value(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, 5.0, 4.0, 5.0]
        self.assertAlmostEqual(kle.pearson(xs, ys), 0.7745966692414834, places=10)

    def test_pearson_is_exactly_one_on_a_straight_line(self):
        self.assertAlmostEqual(kle.pearson([1, 2, 3], [10, 20, 30]), 1.0, places=12)
        self.assertAlmostEqual(kle.pearson([1, 2, 3], [30, 20, 10]), -1.0, places=12)

    def test_a_flat_series_has_no_correlation_rather_than_zero(self):
        self.assertIsNone(kle.pearson([1, 2, 3], [5, 5, 5]))

    def test_correlation_needs_at_least_three_pairs(self):
        self.assertIsNone(kle.pearson([1, 2], [3, 4]))
        self.assertIsNone(kle.spearman([1, 2], [3, 4]))

    def test_spearman_sees_a_monotone_relationship_pearson_understates(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [1.0, 2.0, 3.0, 4.0, 500.0]        # monotone, wildly non-linear
        self.assertAlmostEqual(kle.spearman(xs, ys), 1.0, places=12)
        self.assertLess(kle.pearson(xs, ys), kle.spearman(xs, ys))

    def test_tied_values_take_the_average_rank(self):
        self.assertEqual(kle._ranks([10.0, 20.0, 20.0, 30.0]), [1.0, 2.5, 2.5, 4.0])
        self.assertEqual(kle._ranks([5.0, 5.0, 5.0]), [2.0, 2.0, 2.0])

    def test_ties_do_not_invent_a_relationship(self):
        """Three of five x values are identical. Without average ranks the
        sort order would decide the answer."""
        xs = [1.0, 2.0, 2.0, 2.0, 3.0]
        ys = [9.0, 5.0, 5.0, 5.0, 1.0]
        self.assertAlmostEqual(kle.spearman(xs, ys), -1.0, places=12)

    def test_same_direction_counts_agreement(self):
        obs = [{"korea": 1.0, "opening_gap": 0.5},
               {"korea": -1.0, "opening_gap": -0.5},
               {"korea": 1.0, "opening_gap": -0.5}]
        sd = kle.same_direction(obs, "opening_gap")
        self.assertEqual((sd["k"], sd["n"]), (2, 3))
        self.assertAlmostEqual(sd["rate_pct"], 66.7, places=1)

    def test_an_exactly_flat_session_is_neither_agreement_nor_disagreement(self):
        obs = [{"korea": 1.0, "opening_gap": 0.5},
               {"korea": 1.0, "opening_gap": 0.0},
               {"korea": 0.0, "opening_gap": 0.5}]
        sd = kle.same_direction(obs, "opening_gap")
        self.assertEqual((sd["k"], sd["n"], sd["flat"]), (1, 1, 2))

    def test_the_wilson_interval_comes_from_the_shared_implementation(self):
        from metrics import wilson_interval
        obs = [{"korea": 1.0, "opening_gap": 1.0}] * 7 + \
              [{"korea": 1.0, "opening_gap": -1.0}] * 3
        sd = kle.same_direction(obs, "opening_gap")
        w = wilson_interval(7, 10)
        self.assertAlmostEqual(sd["lo_pct"], round(w["lo"] * 100.0, 1), places=6)
        self.assertAlmostEqual(sd["hi_pct"], round(w["hi"] * 100.0, 1), places=6)

    def test_a_small_sample_gets_a_wide_interval(self):
        few = kle.same_direction([{"korea": 1.0, "opening_gap": 1.0}] * 3
                                 + [{"korea": 1.0, "opening_gap": -1.0}], "opening_gap")
        many = kle.same_direction([{"korea": 1.0, "opening_gap": 1.0}] * 300
                                  + [{"korea": 1.0, "opening_gap": -1.0}] * 100,
                                  "opening_gap")
        self.assertAlmostEqual(few["rate_pct"], many["rate_pct"], places=1)
        self.assertGreater(few["hi_pct"] - few["lo_pct"],
                           many["hi_pct"] - many["lo_pct"])

    def test_no_observations_means_no_rate(self):
        self.assertIsNone(kle.same_direction([], "opening_gap"))
        self.assertIsNone(kle.distribution([]))

    def test_the_sample_count_is_pairs_actually_used(self):
        obs = [{"korea": 1.0, "opening_gap": 0.5},
               {"korea": None, "opening_gap": 0.5},
               {"korea": 1.0, "opening_gap": None}]
        self.assertEqual(kle.measure_stats(obs, "opening_gap")["n"], 1)

    def test_the_distribution_reports_what_happened(self):
        d = kle.distribution([1.0, 2.0, 3.0, 4.0])
        self.assertEqual(d["n"], 4)
        self.assertAlmostEqual(d["avg_pct"], 2.5, places=6)
        self.assertAlmostEqual(d["median_pct"], 2.5, places=6)
        self.assertAlmostEqual(d["p25_pct"], 1.75, places=6)
        self.assertAlmostEqual(d["p75_pct"], 3.25, places=6)


# ── buckets ─────────────────────────────────────────────────────────────────

class TestBuckets(unittest.TestCase):

    def test_every_boundary_belongs_to_the_bucket_it_opens(self):
        for pct, want in ((0.99, "+0to1"), (1.0, "+1to2"), (1.99, "+1to2"),
                          (2.0, "+2to3"), (2.99, "+2to3"), (3.0, "+3to5"),
                          (4.99, "+3to5"), (5.0, "+5toplus"), (12.0, "+5toplus")):
            self.assertEqual(kle.bucket_key(pct), want, f"{pct} landed wrong")

    def test_the_downside_boundaries_mirror_the_upside(self):
        for pct, want in ((-0.99, "-0to1"), (-1.0, "-1to2"), (-2.0, "-2to3"),
                          (-3.0, "-3to5"), (-5.0, "-5toplus")):
            self.assertEqual(kle.bucket_key(pct), want, f"{pct} landed wrong")

    def test_exactly_zero_has_no_signed_bucket(self):
        self.assertIsNone(kle.bucket_key(0.0))
        self.assertIsNone(kle.bucket_key(None))

    def test_up_and_down_are_never_combined(self):
        """Same magnitude, opposite signs, opposite U.S. behaviour. If the
        two were merged, the merged bucket would report a 50% match rate
        and hide both facts."""
        obs = ([{"korea": 3.5, "opening_gap": 2.0}] * 10
               + [{"korea": -3.5, "opening_gap": 2.0}] * 10)
        rows = {r["bucket"]: r for r in kle.bucket_rows(obs)}
        self.assertEqual(set(rows), {"+3to5", "-3to5"})
        self.assertEqual(rows["+3to5"]["same_direction"]["rate_pct"], 100.0)
        self.assertEqual(rows["-3to5"]["same_direction"]["rate_pct"], 0.0)

    def test_the_label_reads_as_english(self):
        self.assertEqual(kle.bucket_label("-3to5"), "KOSPI down 3% to 5%")
        self.assertEqual(kle.bucket_label("+5toplus"), "KOSPI up more than 5%")

    def test_bucket_rows_are_ordered_down_then_up_by_size(self):
        obs = [{"korea": k, "opening_gap": 1.0} for k in
               (0.5, 1.5, -0.5, -4.0, 6.0)]
        got = [r["bucket"] for r in kle.bucket_rows(obs)]
        self.assertEqual(got, ["-0to1", "-3to5", "+0to1", "+1to2", "+5toplus"])


# ── today, matched against history ──────────────────────────────────────────

class TestImpliedGap(unittest.TestCase):

    def test_it_reads_only_the_matching_bucket(self):
        obs = ([{"korea": 3.4, "opening_gap": -2.0}] * 10
               + [{"korea": 1.1, "opening_gap": 9.0}] * 10)
        got = kle.implied_gap(obs, 3.9)
        self.assertEqual(got["bucket"], "+3to5")
        self.assertEqual(got["n"], 10)
        self.assertAlmostEqual(got["distribution"]["median_pct"], -2.0, places=6)

    def test_too_few_matched_sessions_refuses_to_show_a_rate(self):
        obs = [{"korea": 3.4, "opening_gap": 1.0}] * 3
        got = kle.implied_gap(obs, 3.5, min_n=8)
        self.assertFalse(got["usable"])
        self.assertIsNone(got["same_direction"])
        self.assertIn("3 matched session", got["reason"])

    def test_a_flat_korean_session_matches_nothing(self):
        got = kle.implied_gap([{"korea": 1.0, "opening_gap": 1.0}] * 20, 0.0)
        self.assertFalse(got["usable"])
        self.assertIsNone(got["bucket"])

    def test_an_unreadable_korean_session_matches_nothing(self):
        got = kle.implied_gap([{"korea": 1.0, "opening_gap": 1.0}] * 20, None)
        self.assertFalse(got["usable"])
        self.assertIn("not produced a usable move", got["reason"])


class TestChipConfirmation(unittest.TestCase):

    def test_both_agreeing_is_strong(self):
        got = kle.chip_confirmation(1.0, 2.0, 3.0)
        self.assertEqual(got["state"], kle.CONFIRMATION_STRONG)

    def test_neither_agreeing_is_divergence(self):
        got = kle.chip_confirmation(1.0, -2.0, -3.0)
        self.assertEqual(got["state"], kle.CONFIRMATION_DIVERGENCE)

    def test_one_each_way_is_mixed(self):
        got = kle.chip_confirmation(1.0, 2.0, -3.0)
        self.assertEqual(got["state"], kle.CONFIRMATION_MIXED)
        self.assertEqual(got["agree"], 1)

    def test_a_missing_chip_name_is_never_counted_as_agreement(self):
        """Samsung unreadable and SK Hynix up must not read STRONG — only
        one of the two was actually confirmed."""
        got = kle.chip_confirmation(1.0, None, 3.0)
        self.assertEqual(got["state"], kle.CONFIRMATION_MIXED)
        self.assertEqual(got["missing"], ["samsung"])
        self.assertIn("samsung", got["detail"])

    def test_a_missing_chip_name_is_never_assumed_flat_or_down(self):
        got = kle.chip_confirmation(1.0, None, -3.0)
        self.assertEqual(got["agree"], 0)
        self.assertEqual(got["readable"], 1)
        self.assertNotEqual(got["state"], kle.CONFIRMATION_DIVERGENCE)

    def test_without_kospi_there_is_nothing_to_confirm(self):
        self.assertEqual(kle.chip_confirmation(None, 1.0, 2.0)["state"],
                         kle.CONFIRMATION_UNAVAILABLE)

    def test_with_neither_chip_name_it_is_unavailable(self):
        self.assertEqual(kle.chip_confirmation(1.0, None, None)["state"],
                         kle.CONFIRMATION_UNAVAILABLE)


class TestPremarketComparison(unittest.TestCase):

    def test_the_residual_is_the_difference_in_points(self):
        got = kle.premarket_comparison(-3.2, -1.4)
        self.assertAlmostEqual(got["residual_pct"], 1.8, places=6)
        self.assertEqual(got["state"], kle.COMPARISON_CONFIRMING)

    def test_opposite_directions_are_labelled_diverging(self):
        got = kle.premarket_comparison(-3.2, 1.1)
        self.assertEqual(got["state"], kle.COMPARISON_DIVERGING)

    def test_no_share_is_computed_when_the_signs_disagree(self):
        """A share of a move in the other direction is not a quantity. The
        obvious bug here is printing -34% and calling it 'priced in'."""
        got = kle.premarket_comparison(-3.2, 1.1)
        self.assertFalse(got["share_shown"])
        self.assertIsNone(got["share_pct"])

    def test_no_share_is_computed_when_the_expectation_is_near_zero(self):
        got = kle.premarket_comparison(0.02, 0.9)
        self.assertEqual(got["state"], kle.COMPARISON_CONFIRMING)
        self.assertFalse(got["share_shown"])
        self.assertIsNone(got["share_pct"])

    def test_a_share_is_shown_when_both_conditions_hold(self):
        got = kle.premarket_comparison(-3.0, -1.5)
        self.assertTrue(got["share_shown"])
        self.assertAlmostEqual(got["share_pct"], 50.0, places=6)

    def test_a_missing_side_produces_no_comparison(self):
        self.assertEqual(kle.premarket_comparison(None, 1.0)["state"],
                         kle.COMPARISON_UNAVAILABLE)
        self.assertEqual(kle.premarket_comparison(1.0, None)["state"],
                         kle.COMPARISON_UNAVAILABLE)


class TestEdgeAndBias(unittest.TestCase):

    def _stats(self, n, agree, p, s):
        obs = ([{"korea": 1.0, "opening_gap": 1.0}] * agree
               + [{"korea": 1.0, "opening_gap": -1.0}] * (n - agree))
        st = kle.measure_stats(obs, "opening_gap")
        st["pearson"], st["spearman"] = p, s
        return st

    def test_a_small_sample_is_never_described_as_strong(self):
        got = kle.edge_strength(self._stats(10, 10, 0.9, 0.9))
        self.assertEqual(got["state"], kle.EDGE_UNAVAILABLE)

    def test_a_coin_flip_inside_the_interval_is_never_above_weak(self):
        got = kle.edge_strength(self._stats(60, 32, 0.4, 0.4))
        self.assertIn(got["state"], (kle.EDGE_WEAK, kle.EDGE_NONE))

    def test_disagreeing_correlations_are_treated_as_no_strength(self):
        """Linear says one thing, rank says the opposite — usually a couple
        of outliers carrying the result. It never reads above WEAK."""
        got = kle.edge_strength(self._stats(200, 130, 0.45, -0.40))
        self.assertIn(got["state"], (kle.EDGE_WEAK, kle.EDGE_NONE))

    def test_strong_needs_both_the_correlation_and_the_conservative_rate(self):
        self.assertEqual(kle.edge_strength(self._stats(200, 130, 0.4, 0.4))["state"],
                         kle.EDGE_STRONG)
        # same rate, weaker correlation -> not strong
        self.assertNotEqual(kle.edge_strength(self._stats(200, 130, 0.12, 0.12))["state"],
                            kle.EDGE_STRONG)

    def test_the_gates_are_overridable(self):
        st = self._stats(200, 130, 0.4, 0.4)
        got = kle.edge_strength(st, {"strong_corr": 0.9, "strong_lo_pct": 99.0})
        self.assertNotEqual(got["state"], kle.EDGE_STRONG)

    def test_both_edges_are_judged_by_the_same_function(self):
        """The opening gap and the after-open move must never be held to
        different standards — whatever separates them is the data."""
        obs = [{"date": f"2026-01-{1 + i % 28:02d}", "korea": 1.0,
                "opening_gap": 1.0, "open_to_close": 1.0} for i in range(200)]
        st = kle.study(obs)
        self.assertEqual(st["opening_gap_edge"]["state"],
                         st["after_open_edge"]["state"])

    def test_the_bias_follows_the_matched_history_not_koreas_sign(self):
        """Korea is up, but the sessions that looked like this consistently
        opened this ticker LOWER. The bias must say DOWN."""
        obs = [{"korea": 3.5, "opening_gap": -2.0}] * 30
        got = kle.opening_gap_bias(3.5, kle.implied_gap(obs, 3.5))
        self.assertEqual(got["state"], kle.BIAS_DOWN)

    def test_a_rate_that_does_not_clear_a_coin_flip_is_mixed(self):
        obs = ([{"korea": 3.5, "opening_gap": 2.0}] * 6
               + [{"korea": 3.5, "opening_gap": -2.0}] * 6)
        got = kle.opening_gap_bias(3.5, kle.implied_gap(obs, 3.5))
        self.assertEqual(got["state"], kle.BIAS_MIXED)

    def test_no_matched_history_is_no_data(self):
        got = kle.opening_gap_bias(3.5, kle.implied_gap([], 3.5))
        self.assertEqual(got["state"], kle.BIAS_NONE)


# ── the stateful side ───────────────────────────────────────────────────────

class KoreaLeadCase(unittest.TestCase):
    """Base fixture: four Korean series and one U.S. target, all injected."""

    KOREA_MOVES = [1.2, -0.4, 3.1, -2.6, 0.8, 5.4, -1.1, 2.2, -3.7, 0.3,
                   1.9, -0.7, 4.2, -1.3, 0.5, 2.8, -2.2, 1.4, -0.6, 3.3,
                   -4.1, 0.9, 1.7, -1.9, 2.5, -0.3, 3.8, -2.9, 1.1, -1.6,
                   0.7, 2.1, -3.2, 1.8, -0.8, 4.6, -1.4, 0.4, 2.3, -2.4]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 3, 4, 7, 30, tzinfo=ET)   # a Wednesday
        self.korea = {
            "^KS11": kbars(self.KOREA_MOVES),
            "005930.KS": kbars([m * 1.4 for m in self.KOREA_MOVES]),
            "000660.KS": kbars([m * 1.6 for m in self.KOREA_MOVES]),
            "KRW=X": kbars([-m * 0.2 for m in self.KOREA_MOVES]),
        }
        self.us = ubars([m / 3.0 for m in self.KOREA_MOVES],
                        o2c=[0.1 * (-1) ** i for i in range(len(self.KOREA_MOVES))])
        self.quote = {"last": 101.5, "close_prev": 100.0, "open": None,
                      "stale_seconds": 9.0}
        self.korea_calls = []
        self.daily_calls = []
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(kl.configure)

    def _korea(self, sym, days):
        self.korea_calls.append(sym)
        return {"bars": list(self.korea.get(sym) or []), "source": "test",
                "meta": {"name": sym, "timezone": "Asia/Seoul"}}

    def _daily(self, sym, days):
        self.daily_calls.append(sym)
        return {"bars": list(self.us), "source": "test"}


class TestSessionState(KoreaLeadCase):

    def test_seoul_before_the_open(self):
        got = kl.session_state(datetime(2026, 3, 4, 8, 30, tzinfo=KST))
        self.assertEqual(got["state"], kl.SESSION_BEFORE)
        self.assertFalse(got["final"])

    def test_seoul_still_trading(self):
        got = kl.session_state(datetime(2026, 3, 4, 13, 0, tzinfo=KST))
        self.assertEqual(got["state"], kl.SESSION_LIVE)
        self.assertFalse(got["final"])

    def test_seoul_closed_at_half_past_three(self):
        got = kl.session_state(datetime(2026, 3, 4, 15, 30, tzinfo=KST))
        self.assertEqual(got["state"], kl.SESSION_CLOSED)
        self.assertTrue(got["final"])

    def test_a_weekend_is_not_a_trading_day_rather_than_closed(self):
        got = kl.session_state(datetime(2026, 3, 7, 12, 0, tzinfo=KST))
        self.assertEqual(got["state"], kl.SESSION_NON_TRADING)

    def test_a_new_york_morning_is_a_seoul_evening(self):
        """The clock is converted, never assumed. 7:30am in New York on
        March 4 is 9:30pm the same day in Seoul, so Korea has closed."""
        got = kl.session_state(datetime(2026, 3, 4, 7, 30, tzinfo=ET))
        self.assertEqual(got["seoul_date"], "2026-03-04")
        self.assertEqual(got["seoul_time"], "21:30")
        self.assertEqual(got["state"], kl.SESSION_CLOSED)

    def test_a_new_york_evening_is_the_next_seoul_morning(self):
        """9pm Wednesday in New York is 11am THURSDAY in Seoul — Korea is
        already trading the next session. Reading the container's clock
        here would date that session a day early."""
        got = kl.session_state(datetime(2026, 3, 4, 21, 0, tzinfo=ET))
        self.assertEqual(got["seoul_date"], "2026-03-05")
        self.assertEqual(got["state"], kl.SESSION_LIVE)

    def test_the_live_session_marks_todays_reading_provisional(self):
        # 10pm Tuesday in New York is noon Wednesday in Seoul — mid-session.
        # The fixtures already end on that Wednesday.
        self.now = datetime(2026, 3, 3, 22, 0, tzinfo=ET)
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        got = kl.korea_today()
        self.assertEqual(got["session"]["state"], kl.SESSION_LIVE)
        self.assertEqual(got["session"]["seoul_date"], FIXTURE_END)
        self.assertTrue(got["series"]["kospi"]["provisional"])
        # a session still running is a signal, and it says it is not final
        self.assertTrue(got["signal"]["ok"])
        self.assertTrue(got["signal"]["provisional"])

    def test_a_closed_session_is_not_provisional(self):
        got = kl.korea_today()
        self.assertEqual(got["session"]["state"], kl.SESSION_CLOSED)
        self.assertFalse(got["series"]["kospi"]["provisional"])


class TestKoreaToday(KoreaLeadCase):

    def test_every_series_reports_its_own_session_date(self):
        got = kl.korea_today()
        for name in ("kospi", "samsung", "hynix", "usdkrw"):
            self.assertTrue(got["series"][name]["ok"], name)
            self.assertEqual(got["series"][name]["session_date"],
                             self.korea[kl.KOREA_SYMBOLS[name]][-1]["date"])

    def test_usd_krw_is_marked_out_of_the_model_and_says_why(self):
        got = kl.korea_today()["series"]["usdkrw"]
        self.assertFalse(got["in_model"])
        self.assertIn("London", got["excluded_reason"])
        self.assertIn("reading the future", got["excluded_reason"])

    def test_the_three_signal_series_are_in_the_model(self):
        got = kl.korea_today()["series"]
        for name in ("kospi", "samsung", "hynix"):
            self.assertTrue(got[name]["in_model"], name)

    def test_a_failed_series_is_unavailable_never_zero(self):
        self.korea["005930.KS"] = []
        got = kl.korea_today()["series"]["samsung"]
        self.assertFalse(got["ok"])
        self.assertIsNone(got["pct"])

    def test_a_failed_chip_name_does_not_become_agreement(self):
        self.korea["005930.KS"] = []
        got = kl.korea_today()["chip_confirmation"]
        self.assertIn("samsung", got["missing"])
        self.assertNotEqual(got["state"], kle.CONFIRMATION_STRONG)


class TestAnOlderKoreanSessionIsNotTodaysSignal(KoreaLeadCase):
    """A Korean move dated before today already had its own U.S. session —
    the one sharing its date. Carrying it forward onto this morning's open
    is exactly the roll-forward the historical alignment refuses to do, so
    the live panel must refuse it too. This is how it happens in practice:
    a Korean holiday, a provider running a day behind, or a failed refresh
    serving the stored copy."""

    def _stale_kospi(self):
        """KOSPI a session behind; everything else current."""
        self.korea["^KS11"] = kbars(self.KOREA_MOVES)[:-1]

    def test_the_signal_is_withheld_when_korea_last_traded_earlier(self):
        self._stale_kospi()
        got = kl.korea_today()
        self.assertFalse(got["signal"]["ok"])
        self.assertIsNone(got["signal"]["pct"])

    def test_the_reason_names_the_session_it_actually_has(self):
        self._stale_kospi()
        why = kl.korea_today()["signal"]["reason"]
        self.assertIn("newest Korean session on file", why)
        self.assertIn("2026", why)
        # house rule: dates on screen are Month Day, Year, never ISO
        self.assertNotRegex(why, r"\d{4}-\d{2}-\d{2}")

    def test_no_bias_is_shown_from_an_older_korean_session(self):
        self._stale_kospi()
        got = kl.payload("MU", "1y")
        self.assertEqual(got["opening_gap"]["bias"]["state"], kle.BIAS_NONE)
        self.assertFalse(got["opening_gap"]["implied"]["usable"])
        self.assertIn("newest Korean session on file",
                      got["opening_gap"]["implied"]["reason"])

    def test_no_premarket_comparison_against_an_older_korean_session(self):
        self._stale_kospi()
        got = kl.payload("MU", "1y")
        self.assertEqual(got["premarket_comparison"]["state"],
                         kle.COMPARISON_UNAVAILABLE)
        self.assertIsNone(got["premarket_comparison"]["residual_pct"])

    def test_a_bucket_with_ample_history_is_still_refused(self):
        """Not an accident of a thin bucket — the value never reaches the
        lookup at all. This fixture's bucket has plenty of matched
        sessions and the answer is still NO DATA."""
        self.korea["^KS11"] = kbars([1.5] * 60)[:-1]     # a session behind
        self.us = ubars([0.5] * 60)
        got = kl.payload("MU", "max")
        self.assertEqual(got["opening_gap"]["bias"]["state"], kle.BIAS_NONE)

    def test_the_current_session_is_of_course_still_a_signal(self):
        got = kl.korea_today()
        self.assertTrue(got["signal"]["ok"])
        self.assertEqual(got["signal"]["pct"], got["series"]["kospi"]["pct"])

    def test_a_weekend_has_no_signal(self):
        self.now = datetime(2026, 3, 7, 9, 0, tzinfo=ET)   # Saturday in Seoul
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        got = kl.korea_today()
        self.assertEqual(got["session"]["state"], kl.SESSION_NON_TRADING)
        self.assertFalse(got["signal"]["ok"])

    def test_an_unreadable_kospi_says_so_rather_than_naming_a_session(self):
        self.korea["^KS11"] = []
        got = kl.korea_today()
        self.assertFalse(got["signal"]["ok"])
        self.assertIn("KOSPI could not be read", got["signal"]["reason"])


class TestChipConfirmationComparesOneSessionAgainstItself(KoreaLeadCase):
    """A Samsung reading from yesterday beside a KOSPI reading from today is
    not agreement or disagreement about anything — it is two different days
    being compared. Before this was gated, that pair reported STRONG."""

    def test_a_chip_name_from_an_earlier_session_is_not_confirmation(self):
        self.korea["005930.KS"] = kbars([m * 1.4 for m in self.KOREA_MOVES])[:-1]
        got = kl.korea_today()["chip_confirmation"]
        self.assertNotEqual(got["state"], kle.CONFIRMATION_STRONG)
        self.assertIn("samsung", got["missing"])
        self.assertIn("samsung", got["detail"])

    def test_an_off_session_chip_name_cannot_produce_divergence_either(self):
        """Both words claim something about a pair that was measured on the
        same day. Neither may be reached on one."""
        self.korea["005930.KS"] = kbars([-m for m in self.KOREA_MOVES])[:-1]
        self.korea["000660.KS"] = kbars([-m for m in self.KOREA_MOVES])[:-1]
        got = kl.korea_today()["chip_confirmation"]
        self.assertEqual(got["state"], kle.CONFIRMATION_UNAVAILABLE)
        self.assertEqual(got["readable"], 0)

    def test_the_row_is_marked_off_session_so_the_reader_can_see_it(self):
        self.korea["000660.KS"] = kbars([m * 1.6 for m in self.KOREA_MOVES])[:-1]
        got = kl.korea_today()["series"]
        self.assertTrue(got["hynix"]["off_session"])
        self.assertFalse(got["samsung"]["off_session"])
        # the value itself is still shown, with its own date beside it
        self.assertIsNotNone(got["hynix"]["pct"])
        self.assertNotEqual(got["hynix"]["session_date"],
                            got["kospi"]["session_date"])

    def test_same_session_chip_names_confirm_normally(self):
        got = kl.korea_today()["chip_confirmation"]
        self.assertEqual(got["state"], kle.CONFIRMATION_STRONG)
        self.assertEqual(got["missing"], [])


class TestTheStudyCacheKnowsAboutSettings(KoreaLeadCase):
    """Editing the thresholds overlay changes how the edges are worded but
    does NOT change the observation span, so a key built only from the span
    kept hitting and kept serving the previous settings' answer until the
    next trading day rolled it over."""

    def test_the_settings_hash_is_part_of_the_key(self):
        obs = [{"date": "2026-01-05"}, {"date": "2026-03-03"}]
        self.assertIn(kl.config()[1], kl._stats_key("MU", "1y", obs))

    def test_changing_the_settings_changes_the_answer_immediately(self):
        from pathlib import Path
        first = kl.study("MU", "1y")
        self.assertEqual(kl.study("MU", "1y")["cached"], True)
        (Path(self.tmp.name) / "thresholds.json").write_text(json.dumps(
            {"korea_lead": {"edge_gates": {"min_n": 100000}}}))
        kl.config(refresh=True)
        after = kl.study("MU", "1y")
        self.assertFalse(after["cached"], "served a study built under the old settings")
        self.assertNotEqual(first["config_hash"], after["config_hash"])
        self.assertEqual(after["opening_gap_edge"]["state"], kle.EDGE_UNAVAILABLE)

    def test_the_payload_reports_the_settings_it_actually_used(self):
        got = kl.payload("MU", "1y")
        self.assertEqual(got["config_hash"], kl.config()[1])


class TestUsdKrwStaysOutOfTheModel(KoreaLeadCase):
    """The point-in-time protection, proven by changing the currency and
    showing that not one statistic moves."""

    def test_changing_usd_krw_changes_no_statistic(self):
        before = kl.payload("MU", "1y")
        self.korea["KRW=X"] = kbars([m * 9.0 for m in self.KOREA_MOVES])
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        after = kl.payload("MU", "1y", force=True)
        self.assertNotEqual(before["korea"]["series"]["usdkrw"]["pct"],
                            after["korea"]["series"]["usdkrw"]["pct"])
        self.assertEqual(before["opening_gap"]["stats"],
                         after["opening_gap"]["stats"])
        self.assertEqual(before["opening_gap"]["implied"],
                         after["opening_gap"]["implied"])
        self.assertEqual(before["opening_gap"]["bias"],
                         after["opening_gap"]["bias"])
        self.assertEqual(before["korea"]["chip_confirmation"],
                         after["korea"]["chip_confirmation"])

    def test_the_currency_is_not_one_of_the_signal_series(self):
        self.assertNotIn(kl.USDKRW, kl.SIGNAL_SERIES)


class TestNoFutureData(KoreaLeadCase):

    def test_todays_us_session_is_not_in_its_own_history(self):
        """Splice today's in-progress U.S. bar onto the daily feed the way
        the live loader does. It must not become an observation."""
        today = self.now.date().isoformat()
        self.us = self.us + [{"date": today, "open": 200.0, "high": 210.0,
                              "low": 199.0, "close": 205.0}]
        got = kl.observations("MU")
        self.assertTrue(got["ok"])
        self.assertNotIn(today, [o["date"] for o in got["observations"]])
        self.assertLess(got["through"], today)

    def test_history_runs_through_the_last_completed_session(self):
        got = kl.observations("MU")
        self.assertEqual(got["through"], got["observations"][-1]["date"])
        self.assertLess(got["through"], self.now.date().isoformat())

    def test_no_observation_can_postdate_the_through_date(self):
        got = kl.observations("MU")
        self.assertTrue(all(o["date"] <= got["through"]
                            for o in got["observations"]))


class TestWindows(KoreaLeadCase):

    def test_a_shorter_window_never_holds_more_sessions(self):
        ns = {}
        for w in ("60d", "1y", "3y", "max"):
            ns[w] = kl.study("MU", w)["n"]
        self.assertLessEqual(ns["60d"], ns["1y"])
        self.assertLessEqual(ns["1y"], ns["3y"])
        self.assertLessEqual(ns["3y"], ns["max"])

    def test_the_session_window_takes_the_most_recent_sessions(self):
        base = kl.observations("MU")["observations"]
        got = kl._window_slice(base, "60d")
        self.assertEqual(got, base[-60:])

    def test_an_unknown_window_falls_back_to_the_default(self):
        self.assertEqual(kl.payload("MU", "banana")["window"], kl.DEFAULT_WINDOW)

    def test_every_window_reports_its_own_sample_size(self):
        for row in kl.window_comparison("MU"):
            self.assertIn("n", row)
            self.assertIsInstance(row["n"], int)


class TestUsToday(KoreaLeadCase):

    def test_before_the_open_it_is_the_premarket_gap(self):
        got = kl.us_today("MU")
        self.assertTrue(got["ok"])
        self.assertEqual(got["basis"], "premarket")
        self.assertAlmostEqual(got["gap_pct"], 1.5, places=6)

    def test_after_the_open_it_is_the_official_opening_gap(self):
        """From 9:30 the last trade has stopped being an opening price, so
        the comparison switches to the price it actually opened at — and
        says which one it is showing."""
        self.now = datetime(2026, 3, 4, 10, 15, tzinfo=ET)
        self.quote = {"last": 108.0, "close_prev": 100.0, "open": 102.0,
                      "stale_seconds": 3.0}
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        got = kl.us_today("MU")
        self.assertEqual(got["basis"], "official_open")
        self.assertAlmostEqual(got["gap_pct"], 2.0, places=6)

    def test_without_a_prior_close_there_is_no_gap(self):
        self.quote = {"last": 101.0, "close_prev": None, "open": None}
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        got = kl.us_today("MU")
        self.assertFalse(got["ok"])
        self.assertIsNone(got["gap_pct"])


class TestPayload(KoreaLeadCase):

    def test_the_payload_carries_every_section_the_panel_renders(self):
        got = kl.payload("MU", "1y")
        self.assertTrue(got["ok"])
        for key in ("as_of", "session", "sources", "korea", "target",
                    "opening_gap", "premarket_comparison", "after_open",
                    "diagnostics"):
            self.assertIn(key, got)

    def test_the_opening_gap_and_the_after_open_answer_are_separate(self):
        got = kl.payload("MU", "1y")
        self.assertIn("bias", got["opening_gap"])
        self.assertIn("edge", got["after_open"])
        self.assertIsNot(got["opening_gap"]["edge"], got["after_open"]["edge"])

    def test_the_buckets_arrive_already_split_by_sign(self):
        got = kl.payload("MU", "1y")["opening_gap"]
        self.assertTrue(all(b["sign"] == "up" for b in got["buckets_up"]))
        self.assertTrue(all(b["sign"] == "down" for b in got["buckets_down"]))

    def test_no_kospi_means_no_opening_gap_bias(self):
        """It does not fall back to Samsung, to SK Hynix or to yesterday."""
        self.korea["^KS11"] = []
        got = kl.payload("MU", "1y")
        self.assertFalse(got["ok"])
        self.assertIn("KOSPI", got["error"])
        self.assertIsNone(got["opening_gap"])

    def test_no_us_history_is_reported_rather_than_faked(self):
        self.us = []
        got = kl.payload("MU", "1y")
        self.assertFalse(got["ok"])
        self.assertIsNone(got["target"])

    def test_a_ticker_is_required(self):
        self.assertFalse(kl.payload("", "1y")["ok"])

    def test_the_sources_name_the_provider_and_the_symbol(self):
        got = kl.payload("MU", "1y")["sources"]
        self.assertEqual(got["korea"]["symbol"], "^KS11")
        self.assertEqual(got["us"]["symbol"], "MU")
        self.assertEqual(got["korea"]["source"], "test")

    def test_the_definition_and_the_versions_travel_with_the_answer(self):
        got = kl.payload("MU", "1y")
        self.assertEqual(got["signal_definition"], kle.SIGNAL_DEFINITION)
        self.assertEqual(got["engine"], kle.ENGINE_VERSION)
        self.assertTrue(got["config_hash"])

    def test_the_controls_are_flagged_as_controls(self):
        self.assertTrue(kl.payload("SPY", "1y")["target"]["is_control"])
        self.assertTrue(kl.payload("IGV", "1y")["target"]["is_control"])
        self.assertFalse(kl.payload("MU", "1y")["target"]["is_control"])

    def test_the_payload_is_json_serialisable(self):
        json.dumps(kl.payload("MU", "1y"))


class TestCaching(KoreaLeadCase):

    def test_the_korean_series_is_not_refetched_for_every_target(self):
        before = len(self.korea_calls)
        kl.payload("MU", "1y")
        first = len(self.korea_calls) - before
        kl.payload("NVDA", "1y")
        self.assertEqual(len(self.korea_calls) - before, first,
                         "a second target refetched Korea")

    def test_one_panel_load_reads_the_us_series_once(self):
        """The statistics, the matched set and the lookback comparison all
        start from the same daily series. The loader's own network call is
        cached upstream, but everything around it is not — each call re-runs
        an indicator pass over ten years of bars and asks for a live quote
        to splice today in. None of that can change between two calls a
        millisecond apart."""
        self.daily_calls.clear()
        kl.payload("MU", "1y")
        self.assertEqual(self.daily_calls, ["MU"],
                         f"read the U.S. series {len(self.daily_calls)} times")

    def test_a_second_target_reads_its_own_series_once(self):
        kl.payload("MU", "1y")
        self.daily_calls.clear()
        kl.payload("NVDA", "1y")
        self.assertEqual(self.daily_calls, ["NVDA"])

    def test_a_forced_refresh_rereads_the_us_series(self):
        kl.payload("MU", "1y")
        self.daily_calls.clear()
        kl.payload("MU", "1y", force=True)
        self.assertIn("MU", self.daily_calls)

    def test_a_cached_study_is_served_only_for_the_same_question(self):
        a = kl.study("MU", "1y")
        b = kl.study("MU", "1y")
        self.assertTrue(b["cached"])
        c = kl.study("MU", "3y")
        self.assertFalse(c["cached"])

    def test_a_forced_refresh_rebuilds(self):
        kl.study("MU", "1y")
        self.assertFalse(kl.study("MU", "1y", force=True)["cached"])

    def test_the_bars_survive_a_restart_through_the_disk_cache(self):
        kl.korea_bars("kospi")
        calls = len(self.korea_calls)
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        got = kl.korea_bars("kospi")
        self.assertTrue(got["bars"])
        self.assertEqual(len(self.korea_calls), calls,
                         "the disk cache was not used after a restart")

    def test_a_failed_refresh_serves_the_stored_bars_and_says_so(self):
        kl.korea_bars("kospi")

        def boom(sym, days):
            raise RuntimeError("provider down")
        kl._KOREA_FN = boom
        got = kl.korea_bars("kospi", force=True)
        self.assertTrue(got["bars"])
        self.assertTrue(got["stale"])
        self.assertIn("provider down", got["error"])

    def test_a_total_failure_is_reported_not_hidden(self):
        def boom(sym, days):
            raise RuntimeError("provider down")
        kl.configure(daily_fn=self._daily, korea_fn=boom,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        got = kl.korea_bars("kospi")
        self.assertFalse(got["ok"])
        self.assertEqual(got["bars"], [])


class TestASeriesMustActuallyBeDaily(unittest.TestCase):
    """A provider can answer 200 with a well-formed series at the wrong
    granularity. Asked for its longest daily history, the Korean source
    returns MONTHLY bars — about twelve a year instead of two hundred and
    forty-five — and says nothing about the swap. Correlated against daily
    U.S. opens that produces a number that is wrong and looks fine, so
    spacing is measured rather than trusted."""

    def _series(self, step_days, n=40):
        d = date(2020, 1, 6)
        out = []
        for _ in range(n):
            out.append({"date": d.isoformat(), "open": 100.0, "high": 100.0,
                        "low": 100.0, "close": 100.0})
            d += timedelta(days=step_days)
        return out

    def test_a_daily_series_passes(self):
        got = kle.is_daily_series(kbars([0.1] * 60))
        self.assertTrue(got["daily"])
        self.assertLessEqual(got["spacing_days"], 4.0)

    def test_a_weekly_series_is_refused_by_name(self):
        got = kle.is_daily_series(self._series(7))
        self.assertFalse(got["daily"])
        self.assertIn("weekly", got["reason"])

    def test_a_monthly_series_is_refused_by_name(self):
        got = kle.is_daily_series(self._series(30))
        self.assertFalse(got["daily"])
        self.assertIn("monthly", got["reason"])

    def test_weekends_and_holidays_do_not_make_a_daily_series_look_weekly(self):
        """Real daily bars skip weekends and public holidays. The median
        spacing has to survive that or the guard would refuse every real
        series it was built to protect."""
        bars = kbars([0.1] * 200)
        del bars[40:44]          # a long holiday week
        del bars[90]
        self.assertTrue(kle.is_daily_series(bars)["daily"])

    def test_too_few_bars_is_undecidable_rather_than_refused(self):
        got = kle.is_daily_series(kbars([0.1] * 5))
        self.assertTrue(got["daily"])
        self.assertIsNone(got["spacing_days"])
        self.assertIn("too few", got["reason"])


class TestTheGuardStopsAMonthlySeriesReachingTheStatistics(KoreaLeadCase):

    def test_monthly_korean_bars_are_refused_with_a_reason(self):
        d = date(2020, 1, 6)
        monthly = []
        for i in range(40):
            monthly.append({"date": d.isoformat(), "open": 1000.0,
                            "high": 1000.0, "low": 1000.0,
                            "close": 1000.0 + i})
            d += timedelta(days=30)
        self.korea["^KS11"] = monthly
        got = kl.payload("MU", "1y")
        self.assertFalse(got["ok"])
        self.assertIn("not usable", got["error"])
        self.assertIn("monthly", got["error"])
        self.assertIsNone(got["opening_gap"])

    def test_monthly_us_bars_are_refused_too(self):
        d = date(2020, 1, 6)
        monthly = []
        for i in range(40):
            monthly.append({"date": d.isoformat(), "open": 100.0 + i,
                            "high": 101.0 + i, "low": 99.0 + i,
                            "close": 100.5 + i})
            d += timedelta(days=30)
        self.us = monthly
        got = kl.payload("MU", "1y")
        self.assertFalse(got["ok"])
        self.assertIn("MU", got["error"])

    def test_a_healthy_pair_reports_its_spacing(self):
        got = kl.observations("MU")
        self.assertTrue(got["ok"])
        self.assertLessEqual(got["sources"]["korea"]["spacing_days"], 4.0)
        self.assertLessEqual(got["sources"]["us"]["spacing_days"], 4.0)


class TestTheYahooReaderDatesBarsOnTheirOwnExchange(unittest.TestCase):
    """The bar-dating rule, exercised against the two cases that actually
    differ in this app's own data."""

    def _fake(self, offset, stamps):
        body = json.dumps({"chart": {"result": [{
            "meta": {"gmtoffset": offset, "symbol": "X",
                     "exchangeTimezoneName": "Z"},
            "timestamp": stamps,
            "indicators": {"quote": [{
                "open": [1.0] * len(stamps), "high": [1.0] * len(stamps),
                "low": [1.0] * len(stamps), "close": [1.0] * len(stamps)}]},
        }]}}).encode()

        class Resp:
            def read(self_inner):
                return body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return Resp()

    def test_a_korean_bar_keeps_its_seoul_date(self):
        # 2026-08-20 00:00 UTC is 09:00 in Seoul — the session open.
        with mock.patch("urllib.request.urlopen",
                        return_value=self._fake(32400, [1787184000])):
            got = kl._yahoo_daily("^KS11")
        self.assertEqual(got["bars"][0]["date"], "2026-08-20")

    def test_a_london_stamped_bar_is_dated_on_its_london_day(self):
        """The USD/KRW case: 23:00 UTC on the 19th is midnight on the 20th
        in London, so the bar belongs to the 20th. Reading it as UTC would
        file every currency observation one day early."""
        with mock.patch("urllib.request.urlopen",
                        return_value=self._fake(3600, [1787180400])):
            got = kl._yahoo_daily("KRW=X")
        self.assertEqual(got["bars"][0]["date"], "2026-08-20")

    def test_the_longest_daily_range_is_never_requested(self):
        """Asked for its longest range, the provider silently answers with
        MONTHLY bars. The reader must never ask for it."""
        seen = []

        def spy(req, *a, **k):
            seen.append(req.full_url)
            return self._fake(32400, [1787184000])
        with mock.patch("urllib.request.urlopen", side_effect=spy):
            kl._yahoo_daily("^KS11", 5000)
            kl._yahoo_daily("^KS11", 2600)
            kl._yahoo_daily("^KS11", 300)
        self.assertTrue(seen)
        for url in seen:
            self.assertNotIn("range=max", url)

    def test_no_request_is_made_when_the_offline_flag_is_set(self):
        import os
        os.environ["JERRY_NO_NET"] = "1"
        try:
            with mock.patch("urllib.request.urlopen") as opener:
                with self.assertRaises(RuntimeError):
                    kl._yahoo_daily("^KS11")
                opener.assert_not_called()
        finally:
            os.environ.pop("JERRY_NO_NET", None)

    def test_a_bar_with_a_missing_price_is_skipped(self):
        body = json.dumps({"chart": {"result": [{
            "meta": {"gmtoffset": 32400},
            "timestamp": [1787097600, 1787184000],
            "indicators": {"quote": [{"open": [1.0, None], "high": [1.0, 1.0],
                                      "low": [1.0, 1.0], "close": [1.0, 1.0]}]},
        }]}}).encode()

        class Resp:
            def read(self_inner):
                return body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        with mock.patch("urllib.request.urlopen", return_value=Resp()):
            got = kl._yahoo_daily("^KS11")
        self.assertEqual(len(got["bars"]), 1)


class TestConfiguration(unittest.TestCase):

    def test_the_repo_defaults_load(self):
        cfg, h = kl.config(refresh=True)
        self.assertEqual(cfg["max_credible_move_pct"], 50.0)
        self.assertIn("min_n", cfg["edge_gates"])
        self.assertTrue(h)

    def test_documentation_keys_are_never_treated_as_settings(self):
        cfg, _ = kl.config(refresh=True)
        self.assertFalse([k for k in cfg if k.startswith("_")])
        self.assertFalse([k for k in cfg["edge_gates"] if k.startswith("_")])

    def test_a_data_dir_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path
            (Path(tmp) / "thresholds.json").write_text(json.dumps(
                {"korea_lead": {"implied_min_n": 99,
                                "edge_gates": {"min_n": 5}}}))
            kl.configure(data_dir=tmp)
            try:
                cfg, _ = kl.config(refresh=True)
                self.assertEqual(cfg["implied_min_n"], 99)
                self.assertEqual(cfg["edge_gates"]["min_n"], 5)
                # untouched keys keep the repo default
                self.assertEqual(cfg["edge_gates"]["strong_corr"], 0.30)
            finally:
                kl.configure()
                kl.config(refresh=True)


class TestTheSanityCheckAgainstTheResearch(KoreaLeadCase):
    """The independently-run study found the Korea relationship materially
    stronger for the U.S. OPENING GAP than for the move after 9:30. The
    fixture is built to have exactly that shape, and these assert the
    engine reports it that way round rather than flattening the two."""

    def test_a_strong_open_and_a_noisy_afternoon_read_differently(self):
        got = kl.payload("MU", "max")
        gap = got["opening_gap"]["stats"]
        after = got["after_open"]["stats"]
        self.assertGreater(gap["pearson"], 0.9)
        self.assertGreater(gap["same_direction"]["rate_pct"], 90.0)
        self.assertLess(abs(after["pearson"] or 0.0), 0.5)
        self.assertEqual(got["opening_gap"]["edge"]["state"], kle.EDGE_STRONG)
        self.assertNotEqual(got["after_open"]["edge"]["state"], kle.EDGE_STRONG)


if __name__ == "__main__":
    unittest.main()
