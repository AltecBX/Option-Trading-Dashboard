"""test_schedules.py (v3.64) — the maintained macro schedule (CPI/FOMC
tables in treasury.MACRO_SCHEDULE) must be valid, coherent, and NOT about
to expire. This suite intentionally FAILS when the schedule runs out (or
is inside the 21-day early-warning window) so the repo demands an update
before the UI ever has to show "Schedule requires update".

To fix a failure: append next year's dates from the official calendars
(bls.gov CPI schedule, federalreserve.gov FOMC calendar), bump
valid_through + updated in treasury.MACRO_SCHEDULE, re-run.

Run:  python3 -m unittest test_schedules
"""
import unittest
from datetime import date, timedelta

import treasury
from treasury import MACRO_SCHEDULE, schedule_status

WARN_DAYS = 21


class TestScheduleValidity(unittest.TestCase):
    def test_schedule_not_expired_or_expiring(self):
        st = schedule_status()
        self.assertTrue(
            st["ok"] and st["days_left"] > WARN_DAYS,
            f"MACRO_SCHEDULE expires {st['valid_through']} "
            f"({st['days_left']} days left). Append next year's CPI dates "
            "(bls.gov) and FOMC dates (federalreserve.gov) to "
            "treasury.MACRO_SCHEDULE and bump valid_through/updated.")

    def test_schedule_structure(self):
        self.assertIn("valid_through", MACRO_SCHEDULE)
        self.assertIn("updated", MACRO_SCHEDULE)
        date.fromisoformat(MACRO_SCHEDULE["valid_through"])
        date.fromisoformat(MACRO_SCHEDULE["updated"])
        self.assertTrue(MACRO_SCHEDULE["cpi"])
        self.assertTrue(MACRO_SCHEDULE["fomc"])

    def test_all_dates_parse_and_match_their_year(self):
        for year, dates in MACRO_SCHEDULE["cpi"].items():
            self.assertEqual(len(dates), 12, f"CPI {year}: expected 12 releases")
            for d in dates:
                self.assertEqual(date.fromisoformat(d).year, int(year), d)
            self.assertEqual(dates, sorted(dates), f"CPI {year} not sorted")
        fomc = MACRO_SCHEDULE["fomc"]
        self.assertEqual(fomc, sorted(fomc), "FOMC dates not sorted")
        for d in fomc:
            date.fromisoformat(d)

    def test_coverage_reaches_valid_through(self):
        # The claimed validity window must actually be backed by dates:
        # both tables extend into the valid_through year.
        vt_year = date.fromisoformat(MACRO_SCHEDULE["valid_through"]).year
        cpi_years = {int(y) for y in MACRO_SCHEDULE["cpi"]}
        fomc_years = {date.fromisoformat(d).year for d in MACRO_SCHEDULE["fomc"]}
        self.assertIn(vt_year, cpi_years, "valid_through year has no CPI dates")
        self.assertIn(vt_year, fomc_years, "valid_through year has no FOMC dates")


class TestExpiryBehavior(unittest.TestCase):
    """When the schedule DOES expire, consumers must say so — never
    silently recycle old dates."""

    def test_status_flips_after_valid_through(self):
        vt = date.fromisoformat(MACRO_SCHEDULE["valid_through"])
        self.assertTrue(schedule_status(vt)["ok"])
        st = schedule_status(vt + timedelta(days=1))
        self.assertFalse(st["ok"])
        self.assertEqual(st["days_left"], -1)

    def test_events_flag_needs_update_when_expired(self):
        # Freeze "now" past the validity window and confirm _events()
        # returns null dates + explicit needs_update, not stale dates.
        real_now = treasury._et_now
        try:
            from datetime import datetime
            frozen = datetime.fromisoformat(
                MACRO_SCHEDULE["valid_through"] + "T12:00:00")
            frozen = frozen.replace(tzinfo=real_now().tzinfo) + timedelta(days=40)
            treasury._et_now = lambda: frozen
            ev = treasury._events()
            self.assertIsNone(ev["next_cpi"]["date"])
            self.assertEqual(ev["next_cpi"]["status"], "needs_update")
            self.assertIsNone(ev["next_fomc"]["date"])
            self.assertEqual(ev["next_fomc"]["status"], "needs_update")
            self.assertIn("requires update", ev["schedule"]["note"])
        finally:
            treasury._et_now = real_now

    def test_events_ok_inside_window(self):
        # With a date safely inside the window (day after `updated`), the
        # events payload must carry real forward dates and status ok.
        real_now = treasury._et_now
        try:
            from datetime import datetime
            frozen = datetime.fromisoformat(
                MACRO_SCHEDULE["updated"] + "T12:00:00")
            frozen = frozen.replace(tzinfo=real_now().tzinfo)
            treasury._et_now = lambda: frozen
            ev = treasury._events()
            self.assertIsNotNone(ev["next_cpi"]["date"])
            self.assertEqual(ev["next_cpi"]["status"], "ok")
            self.assertIsNotNone(ev["next_fomc"]["date"])
            self.assertGreaterEqual(ev["next_fomc"]["days"], 0)
        finally:
            treasury._et_now = real_now


if __name__ == "__main__":
    unittest.main()
