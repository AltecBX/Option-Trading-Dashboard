"""Did today's data actually get captured?

The option history cannot be back-filled, so a missed trading day is
permanent. These tests hold the code to three things: that a weekend is not
a failure, that a missed trading day IS one and says so the next morning,
and that nothing here ever creates data — a hole is reported, never filled.
"""
import shutil
import tempfile
import unittest
from datetime import date

import capture_health as C


class TestCalendar(unittest.TestCase):
    def test_a_weekend_is_not_a_trading_day(self):
        self.assertFalse(C.is_trading_day("2026-08-15"))     # Saturday
        self.assertFalse(C.is_trading_day("2026-08-16"))     # Sunday
        self.assertTrue(C.is_trading_day("2026-08-17"))      # Monday
        self.assertEqual(C.why_not_trading("2026-08-15"), "a weekend")

    def test_the_market_holidays_are_computed_not_typed(self):
        """A hard-coded table expires. These rules do not."""
        h2026 = C.holidays(2026)
        self.assertEqual(h2026[date(2026, 4, 3)], "Good Friday")
        self.assertEqual(h2026[date(2026, 11, 26)], "Thanksgiving Day")
        self.assertEqual(h2026[date(2026, 7, 3)], "Independence Day")
        self.assertEqual(h2026[date(2026, 1, 19)],
                         "Martin Luther King, Jr. Day")
        # And the year after, and the one after that, without a code change.
        self.assertEqual(C.holidays(2027)[date(2027, 3, 26)], "Good Friday")
        self.assertEqual(C.holidays(2030)[date(2030, 5, 27)], "Memorial Day")

    def test_a_holiday_is_not_a_missed_day(self):
        self.assertFalse(C.is_trading_day("2026-11-26"))
        self.assertEqual(C.why_not_trading("2026-11-26"), "Thanksgiving Day")

    def test_juneteenth_starts_when_it_started(self):
        self.assertNotIn(date(2021, 6, 18), C.holidays(2021))
        self.assertIn(date(2022, 6, 20), C.holidays(2022))

    def test_a_fixed_holiday_moves_off_the_weekend(self):
        # 4 July 2026 is a Saturday; the exchange closes the Friday before.
        self.assertIn(date(2026, 7, 3), C.holidays(2026))
        # Christmas 2027 is a Saturday, so the exchange closes Friday the 24th.
        self.assertIn(date(2027, 12, 24), C.holidays(2027))

    def test_trading_days_skip_the_closed_ones(self):
        self.assertEqual(C.trading_days("2026-11-25", "2026-11-30"),
                         ["2026-11-25", "2026-11-27", "2026-11-30"])

    def test_the_previous_trading_day_steps_over_a_long_weekend(self):
        self.assertEqual(C.previous_trading_day("2026-11-27"), "2026-11-25")


class HealthBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cap-health-")
        C.configure(self.dir)

    def tearDown(self):
        C.configure(None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def expect(self, *syms):
        return {k: list(syms) for k in C.KINDS}


class TestDayStatus(HealthBase):
    DAY = "2026-08-17"                                   # a Monday

    def test_everything_captured_is_complete(self):
        for kind in C.KINDS:
            C.record(kind, "AAPL", True, day=self.DAY)
        got = C.day_status(self.DAY, self.expect("AAPL"))
        self.assertEqual(got["state"], C.COMPLETE)
        self.assertEqual(got["missing"], {})

    def test_one_symbol_failing_is_partial(self):
        for kind in C.KINDS:
            C.record(kind, "AAPL", True, day=self.DAY)
            C.record(kind, "MSFT", False, day=self.DAY,
                     reason="the provider returned no chain")
        got = C.day_status(self.DAY, self.expect("AAPL", "MSFT"))
        self.assertEqual(got["state"], C.PARTIAL)
        self.assertEqual(got["missing"][C.CHAIN], ["MSFT"])
        self.assertIn("the provider returned no chain",
                      got["kinds"][C.CHAIN]["reasons"])

    def test_nothing_captured_on_a_trading_day_is_missed(self):
        got = C.day_status(self.DAY, self.expect("AAPL"))
        self.assertEqual(got["state"], C.MISSED)
        self.assertIn("cannot", got["reason"])

    def test_a_weekend_expects_nothing(self):
        got = C.day_status("2026-08-15", self.expect("AAPL"))
        self.assertEqual(got["state"], C.NOT_EXPECTED)
        self.assertIn("weekend", got["reason"])
        self.assertEqual(got["missing"], {})

    def test_a_holiday_expects_nothing(self):
        got = C.day_status("2026-11-26", self.expect("AAPL"))
        self.assertEqual(got["state"], C.NOT_EXPECTED)
        self.assertIn("Thanksgiving", got["reason"])

    def test_no_followed_symbols_expects_nothing(self):
        got = C.day_status(self.DAY, {})
        self.assertEqual(got["state"], C.NOT_EXPECTED)


class TestRecording(HealthBase):
    DAY = "2026-08-17"

    def test_a_late_capture_is_stamped_as_late(self):
        C.record(C.CHAIN, "AAPL", True, day=self.DAY, late=True,
                 source="schwab", records=6)
        got = C.day_status(self.DAY, {C.CHAIN: ["AAPL"]})
        self.assertEqual(got["kinds"][C.CHAIN]["state"], C.COMPLETE)
        self.assertEqual(got["kinds"][C.CHAIN]["late"], ["AAPL"])

    def test_a_successful_capture_is_recognised_as_already_done(self):
        self.assertFalse(C.captured(C.SNAPSHOT, "AAPL", self.DAY))
        C.record(C.SNAPSHOT, "AAPL", True, day=self.DAY)
        self.assertTrue(C.captured(C.SNAPSHOT, "AAPL", self.DAY))

    def test_a_failed_capture_is_not_mistaken_for_a_done_one(self):
        C.record(C.SNAPSHOT, "AAPL", False, day=self.DAY, reason="no quote")
        self.assertFalse(C.captured(C.SNAPSHOT, "AAPL", self.DAY))

    def test_the_log_survives_a_restart(self):
        C.record(C.SNAPSHOT, "AAPL", True, day=self.DAY)
        C.configure(self.dir)                            # a fresh process
        self.assertTrue(C.captured(C.SNAPSHOT, "AAPL", self.DAY))

    def test_the_log_never_creates_market_data(self):
        """It records that a capture happened. It holds no prices."""
        C.record(C.CHAIN, "AAPL", True, day=self.DAY, records=6)
        entry = (C.day_log(self.DAY)["kinds"][C.CHAIN])["AAPL"]
        self.assertEqual(sorted(entry),
                         ["at", "attempted", "late", "ok", "reason",
                          "records", "source"])


class TestHealth(HealthBase):
    DAY = "2026-08-17"

    def test_a_finished_day_is_healthy(self):
        for kind in C.KINDS:
            C.record(kind, "AAPL", True, day=self.DAY)
        got = C.health(self.expect("AAPL"), days_back=1, today=self.DAY)
        self.assertEqual(got["state"], C.HEALTHY)
        self.assertEqual(got["last_successful"], self.DAY)
        self.assertFalse(got["alert"])

    def test_a_part_finished_day_names_the_symbols(self):
        for kind in C.KINDS:
            C.record(kind, "AAPL", True, day=self.DAY)
        got = C.health(self.expect("AAPL", "MSFT"), days_back=1,
                       today=self.DAY)
        self.assertEqual(got["state"], C.HEALTH_PARTIAL)
        self.assertIn("MSFT", got["alert"])
        self.assertIn("incomplete for 1 followed symbol", got["alert"])

    def test_a_dead_day_is_a_capture_failure(self):
        got = C.health(self.expect("AAPL"), days_back=1, today=self.DAY)
        self.assertEqual(got["state"], C.FAILURE)
        self.assertIn("produced nothing", got["alert"])

    def test_a_weekend_reports_on_the_friday_before_it(self):
        """Asked on a Saturday, the report is about the last day the market
        traded — not about the Saturday, which expected nothing."""
        for kind in C.KINDS:
            C.record(kind, "AAPL", True, day="2026-08-14")   # the Friday
        got = C.health(self.expect("AAPL"), days_back=1, today="2026-08-15")
        self.assertEqual(got["state"], C.HEALTHY)
        self.assertEqual(got["last_trading_day"], "2026-08-14")
        self.assertFalse(got["alert"])

    def test_a_weekend_of_its_own_is_never_a_failure(self):
        got = C.day_status("2026-08-15", self.expect("AAPL"))
        self.assertEqual(got["state"], C.NOT_EXPECTED)

    def test_an_earlier_gap_is_still_reported(self):
        for kind in C.KINDS:
            C.record(kind, "AAPL", True, day=self.DAY)
        got = C.health(self.expect("AAPL"), days_back=3, today=self.DAY)
        self.assertEqual(got["state"], C.HEALTHY)
        self.assertTrue(got.get("earlier_gaps"))


class TestCoverage(HealthBase):
    def test_days_before_the_first_capture_are_not_missed(self):
        C.record(C.CHAIN, "AAPL", True, day="2026-08-17")
        C.record(C.CHAIN, "AAPL", True, day="2026-08-18")
        got = C.symbol_coverage("AAPL", first_day="2026-08-17",
                                today="2026-08-18")
        chain = got["kinds"][C.CHAIN]
        self.assertEqual(chain["captured"], 2)
        self.assertEqual(chain["first"], "2026-08-17")
        self.assertEqual(chain["missing_days"], [])

    def test_a_gap_after_the_first_capture_is_missed(self):
        C.record(C.CHAIN, "AAPL", True, day="2026-08-17")
        C.record(C.CHAIN, "AAPL", True, day="2026-08-19")
        got = C.symbol_coverage("AAPL", first_day="2026-08-17",
                                today="2026-08-19")
        self.assertEqual(got["kinds"][C.CHAIN]["missing_days"], ["2026-08-18"])

    def test_a_weekend_is_never_counted_as_a_missing_day(self):
        C.record(C.CHAIN, "AAPL", True, day="2026-08-14")   # Friday
        C.record(C.CHAIN, "AAPL", True, day="2026-08-17")   # Monday
        got = C.symbol_coverage("AAPL", first_day="2026-08-14",
                                today="2026-08-17")
        self.assertEqual(got["kinds"][C.CHAIN]["missing_days"], [])
        self.assertAlmostEqual(got["kinds"][C.CHAIN]["coverage_pct"], 100.0)


if __name__ == "__main__":                               # pragma: no cover
    unittest.main()
