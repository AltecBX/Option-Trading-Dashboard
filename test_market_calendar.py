"""Guards for market_calendar.py — the days the market does not trade.

These are checked against the NYSE's published calendars, not against the
module's own output, so a wrong rule fails here rather than on a live
Tuesday. The Labor Day case is the one that started this: before the
calendar existed, market_open() said True on September 7, 2026.
"""

from __future__ import annotations

import re
import unittest
from datetime import date, datetime, time as dtime, timedelta

import market_calendar as mc


# The NYSE's own published full-day closures. Typed out, not generated.
PUBLISHED = {
    2024: ["2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
           "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25"],
    2025: ["2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
           "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25"],
    2026: ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
           "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"],
    2027: ["2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
           "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"],
}

PUBLISHED_EARLY = {
    2024: ["2024-07-03", "2024-11-29", "2024-12-24"],
    2025: ["2025-07-03", "2025-11-28", "2025-12-24"],
    2026: ["2026-11-27", "2026-12-24"],
    2027: ["2027-11-26"],
}


def _d(s: str) -> date:
    return date.fromisoformat(s)


class TestPublishedCalendar(unittest.TestCase):
    def test_full_day_closures_match_the_nyse(self):
        for year, days in PUBLISHED.items():
            got = {d.isoformat() for d in mc.holidays(year)
                   if mc.holidays(year)[d] != "Unscheduled closure"}
            self.assertEqual(got, set(days), f"{year} holidays")

    def test_early_closes_match_the_nyse(self):
        for year, days in PUBLISHED_EARLY.items():
            got = []
            d = date(year, 1, 1)
            while d.year == year:
                if mc.is_early_close(d):
                    got.append(d.isoformat())
                d += timedelta(days=1)
            self.assertEqual(got, days, f"{year} early closes")


class TestTheBugThatStartedThis(unittest.TestCase):
    def test_labor_day_2026_is_not_a_session(self):
        self.assertFalse(mc.is_session(_d("2026-09-07")))
        self.assertFalse(mc.is_open(datetime(2026, 9, 7, 11, 0)))

    def test_the_day_after_labor_day_is(self):
        self.assertTrue(mc.is_session(_d("2026-09-08")))
        self.assertTrue(mc.is_open(datetime(2026, 9, 8, 11, 0)))

    def test_a_weekday_alone_is_not_enough(self):
        """The old rule was `weekday() < 5 and 9:30 <= t < 16:00`. Every one
        of these is a weekday inside those hours and still shut."""
        for iso in ("2026-01-01", "2026-04-03", "2026-05-25", "2026-07-03",
                    "2026-09-07", "2026-11-26", "2026-12-25"):
            d = _d(iso)
            self.assertLess(d.weekday(), 5, iso)
            self.assertFalse(mc.is_open(datetime(d.year, d.month, d.day, 11, 0)), iso)


class TestObservedDates(unittest.TestCase):
    def test_saturday_holiday_moves_back_to_friday(self):
        # July 4, 2026 is a Saturday; the market shuts Friday July 3.
        self.assertTrue(mc.is_holiday(_d("2026-07-03")))
        self.assertFalse(mc.is_early_close(_d("2026-07-03")))

    def test_sunday_holiday_moves_forward_to_monday(self):
        # July 4, 2027 is a Sunday; the market shuts Monday July 5.
        self.assertTrue(mc.is_holiday(_d("2027-07-05")))

    def test_new_years_on_a_saturday_takes_no_day(self):
        """The exception everyone gets wrong: January 1, 2028 is a Saturday
        and the NYSE does NOT close Friday December 31, 2027."""
        self.assertTrue(mc.is_session(_d("2027-12-31")))
        self.assertFalse(mc.is_holiday(_d("2027-12-31")))

    def test_new_years_on_a_sunday_is_observed_the_next_day(self):
        # January 1, 2023 was a Sunday; the market shut Monday January 2.
        self.assertTrue(mc.is_holiday(_d("2023-01-02")))

    def test_juneteenth_did_not_exist_before_2022(self):
        self.assertFalse(mc.is_holiday(_d("2021-06-18")))   # a Friday, traded
        self.assertTrue(mc.is_holiday(_d("2022-06-20")))    # observed Monday

    def test_good_friday_tracks_easter(self):
        for year, iso in ((2024, "2024-03-29"), (2025, "2025-04-18"),
                          (2026, "2026-04-03"), (2027, "2027-03-26")):
            self.assertEqual(mc.easter(year) - timedelta(days=2), _d(iso))

    def test_unscheduled_closures_are_honoured(self):
        self.assertFalse(mc.is_session(_d("2012-10-30")))   # Hurricane Sandy
        self.assertFalse(mc.is_session(_d("2025-01-09")))   # Carter


class TestSessionLength(unittest.TestCase):
    def test_a_regular_session_is_six_and_a_half_hours(self):
        self.assertEqual(mc.session_seconds(_d("2026-09-08")), 23400.0)
        self.assertEqual(mc.close_time(_d("2026-09-08")), dtime(16, 0))

    def test_a_half_day_is_three_and_a_half(self):
        self.assertEqual(mc.session_seconds(_d("2026-11-27")), 12600.0)
        self.assertEqual(mc.close_time(_d("2026-11-27")), dtime(13, 0))

    def test_a_closed_day_has_no_session(self):
        self.assertEqual(mc.session_seconds(_d("2026-09-07")), 0.0)
        self.assertIsNone(mc.session_span(_d("2026-09-07")))

    def test_the_bell_on_a_half_day_is_one_oclock(self):
        d = _d("2026-11-27")
        self.assertTrue(mc.is_open(datetime(d.year, d.month, d.day, 12, 59)))
        self.assertFalse(mc.is_open(datetime(d.year, d.month, d.day, 13, 1)))
        # The old rule would have called 2pm on this day a live market.
        self.assertFalse(mc.is_open(datetime(d.year, d.month, d.day, 14, 0)))


class TestCallerWindows(unittest.TestCase):
    def test_the_collector_window_shifts_with_the_bell(self):
        """intraday_option_store starts 5 minutes early and runs 5 past."""
        d = _d("2026-11-27")                       # half day
        args = dict(open_time=dtime(9, 25), close_pad_minutes=5)
        self.assertTrue(mc.is_open(datetime(d.year, d.month, d.day, 9, 26), **args))
        self.assertTrue(mc.is_open(datetime(d.year, d.month, d.day, 13, 4), **args))
        self.assertFalse(mc.is_open(datetime(d.year, d.month, d.day, 13, 6), **args))

    def test_the_collector_still_respects_holidays(self):
        d = _d("2026-09-07")
        self.assertFalse(mc.is_open(datetime(d.year, d.month, d.day, 10, 0),
                                    open_time=dtime(9, 25), close_pad_minutes=5))


class TestNeighbours(unittest.TestCase):
    def test_next_session_skips_the_holiday(self):
        self.assertEqual(mc.next_session(_d("2026-09-04")), _d("2026-09-08"))

    def test_previous_session_skips_the_holiday(self):
        self.assertEqual(mc.previous_session(_d("2026-09-08")), _d("2026-09-04"))

    def test_they_accept_a_datetime_too(self):
        self.assertEqual(mc.next_session(datetime(2026, 9, 4, 15, 0)), _d("2026-09-08"))


class TestPlainLanguage(unittest.TestCase):
    def test_a_holiday_is_named_not_just_refused(self):
        self.assertIn("Labor Day", mc.describe(_d("2026-09-07")))

    def test_a_weekend_says_weekend(self):
        self.assertIn("weekend", mc.describe(_d("2026-09-06")))

    def test_a_half_day_says_one_oclock(self):
        self.assertIn("1:00 PM", mc.describe(_d("2026-11-27")))

    def test_a_normal_day_says_four_oclock(self):
        self.assertIn("4:00 PM", mc.describe(_d("2026-09-08")))


class TestPurity(unittest.TestCase):
    def test_no_app_imports(self):
        with open("market_calendar.py", encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("import requests", "import schwab", "urllib", "Path("):
            self.assertNotIn(bad, src, f"market_calendar must stay pure: {bad}")
        # File I/O, but not the module's own is_open()/session_span() names.
        self.assertIsNone(re.search(r"(?<![_a-z])open\(", src),
                          "market_calendar must not read or write files")

    def test_every_year_answers(self):
        """A calendar that expires is worse than none — it is wrong silently."""
        for year in range(2015, 2041):
            self.assertGreaterEqual(len(mc.holidays(year)), 9, year)


if __name__ == "__main__":
    unittest.main()
