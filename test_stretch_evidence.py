"""test_stretch_evidence.py — what a stock does AFTER it reaches its usual line.

The guards that keep the evidence honest:

  * an event is the FIRST bar that reached the level, and nothing before
    it is credited to the seller — the outcome is measured from that bar on;
  * a week that never reached the level is not an event at all;
  * puts are measured on lows and closes-below, never by flipping the call
    numbers;
  * the sigma is point-in-time: the sigma known at the week's anchor;
  * the comparable set prefers the exact sessions-left bucket, and when it
    widens it says so and only ever widens toward MORE room to run;
  * thin own evidence is pooled and graded POOLED, never passed off as the
    stock's own;
  * the like-for-like check against selling Monday morning is the same
    0.84-sigma rule on two entries, and the line rule's strike sits
    further from the anchor.
"""
import math
import unittest
from datetime import date, timedelta

import stretch_evidence as se


def _bars(weeks, start=date(2025, 1, 6), px=100.0, spec=None, sigma=0.02, seed=1):
    """`spec` maps week index → list of (open, high, low, close) as
    multiples of the prior close; unspecified weeks are a quiet random walk
    so the sigma window is always populated."""
    import random
    rng = random.Random(seed)
    out, d, p = [], start, px
    for w in range(weeks):
        days = (spec or {}).get(w)
        for i in range(5):
            if days:
                o, h, lo, c = days[i]
                bar = {"open": p * o, "high": p * h, "low": p * lo, "close": p * c}
                p = p * c
            else:
                r = rng.gauss(0, sigma)
                o = p
                p = p * math.exp(r)
                bar = {"open": o, "high": max(o, p) * 1.003, "low": min(o, p) * 0.997, "close": p}
            out.append({"date": (d + timedelta(days=i)).isoformat(), **bar})
        d += timedelta(days=7)
    return out


class TheEventIsTheFirstCrossing(unittest.TestCase):
    def test_outcome_is_measured_from_the_crossing_bar_on(self):
        # Mon flat, Tue reaches the level, Wed higher still, Fri closes back.
        days = [{"high": 100, "low": 99, "close": 100}, {"high": 103, "low": 100, "close": 102},
                {"high": 106, "low": 101, "close": 105}, {"high": 105, "low": 100, "close": 101},
                {"high": 102, "low": 99, "close": 100}]
        idx = se._crossing(days, 103, "call")
        self.assertEqual(idx, 1)
        left, beyond, term = se._outcome(days, idx, 103.0, "call", 0.05)
        self.assertEqual(left, 3)
        self.assertAlmostEqual(beyond, math.log(106 / 103) / 0.05)
        self.assertAlmostEqual(term, math.log(100 / 103) / 0.05)
        self.assertLess(term, 0, "closed back below the line reads as a win for the seller")

    def test_nothing_before_the_crossing_counts(self):
        # Monday nearly gets there and Tuesday sells off; the level is only
        # reached on Thursday, so Monday's high must not appear in `beyond`
        # and Tuesday's low must not appear in anything.
        days = [{"high": 103, "low": 99, "close": 100}, {"high": 101, "low": 90, "close": 99},
                {"high": 100, "low": 97, "close": 98}, {"high": 104, "low": 98, "close": 103},
                {"high": 105, "low": 102, "close": 104}]
        idx = se._crossing(days, 103.5, "call")
        self.assertEqual(idx, 3)
        left, beyond, _term = se._outcome(days, idx, 103.5, "call", 0.05)
        self.assertEqual(left, 1)
        self.assertAlmostEqual(beyond, math.log(105 / 103.5) / 0.05)

    def test_a_week_that_never_reached_the_level_is_not_an_event(self):
        days = [{"high": 101, "low": 99, "close": 100}] * 5
        self.assertIsNone(se._crossing(days, 103, "call"))
        self.assertIsNone(se._crossing(days, 97, "put"))

    def test_puts_are_measured_on_lows_and_closes_below(self):
        days = [{"high": 101, "low": 96, "close": 97}, {"high": 98, "low": 93, "close": 94},
                {"high": 96, "low": 94, "close": 95}, {"high": 97, "low": 95, "close": 96},
                {"high": 99, "low": 96, "close": 98}]
        idx = se._crossing(days, 96.5, "put")
        self.assertEqual(idx, 0)
        left, beyond, term = se._outcome(days, idx, 96.5, "put", 0.05)
        self.assertEqual(left, 4)
        self.assertAlmostEqual(beyond, math.log(96.5 / 93) / 0.05)
        self.assertAlmostEqual(term, math.log(96.5 / 98) / 0.05)
        self.assertLess(term, 0, "closed back ABOVE a put line is the seller's win")


class TheProfile(unittest.TestCase):
    def test_needs_twelve_complete_weeks(self):
        self.assertIsNone(se.profile(_bars(8), today=date(2025, 3, 3)))
        self.assertIsNotNone(se.profile(_bars(30), today=date(2025, 8, 4)))

    def test_the_week_in_progress_is_not_history(self):
        bars = _bars(30)
        last = date.fromisoformat(bars[-1]["date"])
        mid_week = last - timedelta(days=2)             # a Wednesday inside the last week
        p = se.profile(bars, today=mid_week)
        # 30 weeks: the last is in progress, the first has no anchor, and the
        # two after it have no 12-bar sigma yet.
        self.assertEqual(p["n_weeks"], 26)
        self.assertEqual(p["anchor"]["week_start"], se.week_start(mid_week).isoformat())
        self.assertEqual(p["anchor"]["week_close_date"], (se.week_start(mid_week) - timedelta(days=3)).isoformat())

    def test_the_lines_are_the_median_extremes_from_the_anchor(self):
        # Every week: high +4%, low -2%, close +1% from the prior close.
        spec = {w: [(1.0, 1.04, 0.98, 1.01), (1.0, 1.0, 1.0, 1.0), (1.0, 1.0, 1.0, 1.0),
                    (1.0, 1.0, 1.0, 1.0), (1.0, 1.0, 1.0, 1.0)] for w in range(41)}
        # ...except the sigma window needs variance, so alternate the size.
        # 41 weeks: the first has no anchor, leaving twenty of each kind, so
        # the median sits halfway between them.
        for w in range(41):
            if w % 2:
                spec[w] = [(1.0, 1.06, 0.97, 1.02)] + spec[w][1:]
        p = se.profile(_bars(41, spec=spec), today=date(2025, 10, 20))
        self.assertAlmostEqual(p["lines"]["week"]["high_pct"], 0.05, places=3)
        self.assertAlmostEqual(p["lines"]["week"]["low_pct"], -0.025, places=3)
        self.assertEqual(p["lines"]["week"]["anchor"], "the prior week's last close")
        self.assertEqual(p["lines"]["day"]["anchor"], "the prior session's close")

    def test_the_line_quantile_is_a_choice_not_a_constant(self):
        bars = _bars(40)
        p50 = se.profile(bars, today=date(2025, 10, 13), line_q=0.5)
        p75 = se.profile(bars, today=date(2025, 10, 13), line_q=0.75)
        self.assertGreater(p75["lines"]["week"]["high_pct"], p50["lines"]["week"]["high_pct"])
        self.assertLess(p75["lines"]["week"]["low_pct"], p50["lines"]["week"]["low_pct"])
        self.assertEqual(p75["line_q"], 0.75)

    def test_the_record_is_context_not_the_line(self):
        p = se.profile(_bars(40), today=date(2025, 10, 13))
        self.assertGreater(p["lines"]["week"]["best_high_pct"], p["lines"]["week"]["high_pct"])
        self.assertLess(p["lines"]["week"]["worst_low_pct"], p["lines"]["week"]["low_pct"])

    def test_sigma_is_point_in_time(self):
        # A calm history that turns wild in the last ten weeks: the events of
        # the calm weeks must be scaled by the calm sigma, so a modest move
        # then still reads as a full-sigma crossing.
        spec = {}
        for w in range(40):
            if w < 30:
                spec[w] = [(1.0, 1.012, 0.99, 1.008), (1.0, 1.006, 0.994, 0.996),
                           (1.0, 1.01, 0.995, 1.005), (1.0, 1.004, 0.992, 0.997),
                           (1.0, 1.009, 0.996, 1.004)]
            else:
                spec[w] = [(1.0, 1.12, 0.90, 1.08), (1.0, 1.06, 0.94, 0.96),
                           (1.0, 1.10, 0.95, 1.05), (1.0, 1.04, 0.92, 0.97),
                           (1.0, 1.09, 0.96, 1.04)]
        p = se.profile(_bars(40, spec=spec), today=date(2025, 10, 13))
        n_calm = sum(len(v) for v in p["events"]["week"]["call"].values())
        self.assertGreater(n_calm, 20, "calm weeks still produced crossings in their own sigma")


class ComparableCrossings(unittest.TestCase):
    def _prof(self):
        prof = {"sigma_daily": 0.02, "sigma_weekly": 0.02 * math.sqrt(5),
                "events": {"week": {"call": {"1": []}, "put": {}}, "day": {"call": {}, "put": {}}}}
        return prof

    def test_exact_bucket_when_deep_enough(self):
        prof = self._prof()
        prof["events"]["week"]["call"]["1"] = [(3, 0.5, -0.2)] * 25 + [(4, 2.0, 1.5)] * 25
        ev = se.evidence(prof, "week", "call", 1.1, 3)
        self.assertEqual(ev["n_own"], 25)
        self.assertIn("exactly 3 sessions left", ev["basis"])
        self.assertEqual(ev["grade"], "MEASURED")

    def test_widens_only_toward_more_room_and_says_so(self):
        prof = self._prof()
        prof["events"]["week"]["call"]["1"] = ([(3, 0.5, -0.2)] * 10 + [(4, 2.0, 1.5)] * 15
                                               + [(1, 0.1, -0.5)] * 30)
        ev = se.evidence(prof, "week", "call", 1.1, 3)
        self.assertEqual(ev["n_own"], 25, "sessions-left 1 had less room to run and is excluded")
        self.assertIn("upper bound", ev["basis"])

    def test_the_level_never_rounds_up(self):
        self.assertEqual(se.level_for(1.4, "week"), (1.25, False))
        self.assertEqual(se.level_for(0.3, "week"), (0.5, True))
        self.assertEqual(se.level_for(2.9, "day"), (2.5, False))

    def test_thin_own_evidence_is_pooled_and_graded(self):
        prof = self._prof()
        prof["events"]["week"]["call"]["1"] = [(3, 0.5, -0.2)] * 4
        pool = {"week": {"call": {"1": [(3, 1.0, 0.3)] * 40}}}
        ev = se.evidence(prof, "week", "call", 1.0, 3, pool)
        self.assertEqual(ev["grade"], "POOLED")
        self.assertEqual((ev["n_own"], ev["n_pool"]), (4, 40))
        ev2 = se.evidence(prof, "week", "call", 1.0, 3, None)
        self.assertEqual(ev2["grade"], "THIN")
        self.assertEqual(ev2["windows"].__len__(), 4)

    def test_pooled_events_are_read_in_this_stocks_own_sigma(self):
        prof = self._prof()
        pool = {"week": {"call": {"1": [(3, 1.0, 1.0)] * 40}}}
        ev = se.evidence(prof, "week", "call", 1.0, 3, pool)
        sw = 0.02 * math.sqrt(5)
        self.assertAlmostEqual(ev["windows"][0]["high"], math.exp(sw) - 1)
        self.assertAlmostEqual(ev["windows"][0]["term"], math.exp(sw) - 1)

    def test_put_windows_point_down(self):
        prof = self._prof()
        prof["events"]["week"]["put"] = {"1": [(3, 1.0, 0.5)] * 25}
        ev = se.evidence(prof, "week", "put", 1.0, 3)
        w = ev["windows"][0]
        self.assertLess(w["low"], 0)
        self.assertLess(w["term"], 0, "closed below the put line = negative fraction")
        self.assertEqual(w["high"], 0.0)

    def test_the_pool_is_bounded(self):
        pool = {}
        prof = {"events": {"week": {"call": {"1": [(3, 0.5, 0.1)] * 3000}, "put": {}},
                           "day": {"call": {}, "put": {}}}}
        se.add_to_pool(pool, prof, cap=4000)
        se.add_to_pool(pool, prof, cap=4000)
        self.assertEqual(len(pool["week"]["call"]["1"]), 4000)


class VersusMonday(unittest.TestCase):
    def test_same_rule_two_entries_and_the_line_strike_sits_further_out(self):
        p = se.profile(_bars(60, sigma=0.02, seed=4), today=date(2026, 3, 2))
        v = p["vs_monday"]["call"]
        self.assertEqual(v["weeks"], p["n_weeks"])
        self.assertLessEqual(v["weeks_crossed"], v["weeks"])
        self.assertAlmostEqual(v["opportunity"], v["weeks_crossed"] / v["weeks"])
        self.assertGreater(v["line"]["strike_from_anchor_pct"], v["monday"]["strike_from_anchor_pct"])
        for leg in ("monday", "line"):
            self.assertGreaterEqual(v[leg]["touched"], v[leg]["finished_through"],
                                    "a strike cannot be finished through without being touched")
        vp = p["vs_monday"]["put"]
        self.assertLess(vp["line"]["strike_from_anchor_pct"], vp["monday"]["strike_from_anchor_pct"])

    def test_the_median_line_is_reached_about_half_the_time(self):
        p = se.profile(_bars(80, sigma=0.02, seed=9), today=date(2026, 7, 20))
        v = p["vs_monday"]["call"]
        self.assertGreater(v["opportunity"], 0.4)
        self.assertLess(v["opportunity"], 0.6)


if __name__ == "__main__":
    unittest.main()
