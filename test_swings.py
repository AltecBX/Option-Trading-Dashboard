"""Swing-rhythm regression tests (v4.12).

The extreme target tier IS one specific historical move — the largest in
the lookback — so its ETA must use that move's OWN duration
(days_of_max), not days_max (the longest duration across all swings,
which may belong to a different, smaller move). Pairing pct_max with an
unrelated duration can imply a speed no swing ever had (user-reported,
8-16-2026: "+140% by <6 weeks>" on a name whose big move took months).
"""
import unittest

import swings


def _sw(pct, days):
    return {"trading_days": days, "pct_change": pct}


class TestRhythm(unittest.TestCase):
    def test_days_of_max_is_the_largest_moves_own_duration(self):
        r = swings._rhythm([
            _sw(46.52, 6), _sw(30.10, 6),
            _sw(22.00, 35),    # longest swing, but a small move
            _sw(235.00, 28),   # largest move
            _sw(38.00, 5),
        ])
        self.assertEqual(r["days_max"], 35)       # unchanged meaning
        self.assertEqual(r["days_of_max"], 28)    # the big move's own clock

    def test_days_of_max_handles_down_swings(self):
        r = swings._rhythm([_sw(-15.59, 2), _sw(-32.32, 30), _sw(-15.66, 15)])
        self.assertEqual(r["days_of_max"], 30)    # abs() picks -32.32%

    def test_rhythm_still_none_below_two_swings(self):
        self.assertIsNone(swings._rhythm([_sw(10.0, 3)]))


if __name__ == "__main__":
    unittest.main()
