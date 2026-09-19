"""v5.19 — the sidebar's YTD line measures from last year's final close.

Jerry: "I want to put the YTD % underneath this" (the P/E line). The
anchor is the last bar dated before January 1 of the latest bar's year —
the same day the watchlist board's YTD column uses — and the line stays
off when the bars do not reach back that far.
"""
import unittest

from options_dashboard import ytd_base


def bars(*pairs):
    return [{"date": d + "T12:00:00-04:00", "close": c} for d, c in pairs]


class TheYtdAnchor(unittest.TestCase):
    def test_it_is_last_years_final_close_not_this_years_first(self):
        b = bars(("2025-12-30", 98.0), ("2025-12-31", 100.0),
                 ("2026-01-02", 104.0), ("2026-09-18", 177.46))
        self.assertEqual(100.0, ytd_base(b))

    def test_bars_that_stop_short_of_last_year_give_nothing(self):
        b = bars(("2026-01-02", 104.0), ("2026-09-18", 177.46))
        self.assertIsNone(ytd_base(b))

    def test_empty_and_broken_rows_give_nothing(self):
        self.assertIsNone(ytd_base([]))
        self.assertIsNone(ytd_base(None))
        self.assertIsNone(ytd_base([{"date": None, "close": 5}, {"date": "", "close": 6}]))

    def test_a_zero_close_is_not_a_base(self):
        b = bars(("2025-12-31", 0.0), ("2026-01-02", 104.0))
        self.assertIsNone(ytd_base(b))

    def test_the_year_is_the_latest_bars_year_not_the_clock(self):
        # Bars ending in 2025 measure 2025 from the end of 2024.
        b = bars(("2024-12-31", 50.0), ("2025-03-03", 60.0), ("2025-12-31", 100.0))
        self.assertEqual(50.0, ytd_base(b))


if __name__ == "__main__":
    unittest.main()
