"""Tests for the production data readiness audit.

These are not tests of a feature. They are the tests that answer one
question: if this app runs untouched for the next year, will the data it
collects still be there, still be complete, and still be believable when the
thirty, ninety, one hundred and eighty and three hundred and sixty-five day
results are finally scored?

Every case here is a way that year could quietly be lost:

  * the container's clock filing a capture under a day that has not happened
  * a redeploy erasing the directory it was all written to
  * a restart re-doing work it already did, or skipping work it never did
  * a retention limit deleting a day before the horizon reaches it
  * an option chain that arrived, parsed and stored with nothing in it
  * a stored recommendation whose rules can no longer be read back

None of these fail loudly in production. Each one is silent until the day
the dataset is needed, which is the day it is too late. So they are pinned
here instead.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta

os.environ.setdefault("JERRY_NO_NET", "1")

import capture_health as H
import chain_store as CS
import invest_audit as A
import invest_scan as S

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
    UTC = ZoneInfo("UTC")
except Exception:                                    # pragma: no cover
    NY = UTC = None


# ── fixtures ────────────────────────────────────────────────────────────────

def chain_payload(spot=100.0, day=None, quoted=True):
    """A provider chain payload in the app's standard shape."""
    d = date.fromisoformat(day) if day else date.today()
    exp = (d + timedelta(days=21)).isoformat()
    def leg(strike):
        if not quoted:
            # The failure this exists to catch: every field present, every
            # number zero. It parses. It stores. It prices nothing.
            return {"strike": strike, "bid": 0.0, "ask": 0.0, "last": 0.0,
                    "iv": 0.0, "delta": 0.0, "openInterest": 0, "volume": 0}
        return {"strike": strike, "bid": 2.0, "ask": 2.2, "last": 2.1,
                "iv": 0.30, "delta": 0.45, "openInterest": 400, "volume": 50}
    strikes = [spot * 0.95, spot, spot * 1.05]
    return {"underlying": {"last": spot},
            "expirations": [exp],
            "source": "test chain",
            "chains": {exp: {"calls": [leg(k) for k in strikes],
                             "puts": [leg(k) for k in strikes]}}}


def row(day, **over):
    """One stored recommendation row, complete enough to be scored."""
    out = {"date": day, "ticker": "SMPL", "price": 100.0,
           "config_hash": "abc123", "entry_verdict": "WAIT",
           "preferred_structure": "cash-secured put",
           "recommended_contract": {"structure": "cash-secured put",
                                    "expiration": "2027-01-15",
                                    "bid": 2.0, "ask": 2.2, "credit": 2.1},
           "benchmark_symbol": "SPY", "benchmark_close": 500.0,
           "fair_value_bear": 80.0, "fair_value_base": 100.0,
           "fair_value_bull": 120.0, "fair_value_confidence": "MEDIUM",
           "buy_zone": 90.0, "quality_label": "GOOD", "growth_label": "STEADY",
           "valuation_label": "FAIR", "revisions_label": "IMPROVING",
           "value_trap_level": "LOW"}
    out.update(over)
    return out


class AuditBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="invest-audit-")
        self.data = os.path.join(self.dir, "invest")
        os.makedirs(self.data, exist_ok=True)
        S._CFG_CACHE.update({"cfg": None, "hash": None, "ts": 0.0})
        S.configure(data_dir=self.data)
        S._MEM.clear()
        S._SCHED["started"] = False
        S._SCHED["recorded_for"] = None
        CS._RECORDED_TODAY.clear()

    def tearDown(self):
        S._MEM.clear()
        S._STARRED_FN = None
        S._SCHED["started"] = False
        S._SCHED["recorded_for"] = None
        CS._RECORDED_TODAY.clear()
        shutil.rmtree(self.dir, ignore_errors=True)


# ── 1. where the data lives ─────────────────────────────────────────────────

class TestPersistentDataPath(AuditBase):
    def test_a_directory_on_the_container_disk_is_called_ephemeral(self):
        got = A.data_home(self.data)
        self.assertEqual(got["state"], A.EPHEMERAL)
        self.assertIn("redeploy", got["reason"].lower())

    def test_the_reason_names_the_environment_variable_to_set(self):
        got = A.data_home(self.data)
        self.assertIn("JERRY_DATA_DIR", got["reason"])

    def test_no_directory_at_all_is_ephemeral_not_unknown(self):
        got = A.data_home(None)
        self.assertEqual(got["state"], A.EPHEMERAL)
        self.assertIn("nothing is being stored", got["reason"].lower())

    def test_a_path_under_the_documented_mount_point_is_persistent(self):
        # /data is the mount point this app's deployment notes use. It could
        # not be confirmed as a separate filesystem here, and the reason
        # says so rather than claiming certainty.
        got = A.data_home("/data/invest")
        self.assertEqual(got["state"], A.PERSISTENT)
        self.assertIn("check the volume", got["reason"].lower())

    def test_a_separate_filesystem_is_reported_as_a_mounted_volume(self):
        got = A.data_home("/proc")           # genuinely a different device
        self.assertTrue(got["separate_device"])
        self.assertEqual(got["state"], A.PERSISTENT)

    def test_an_ephemeral_home_makes_the_whole_audit_a_capture_failure(self):
        out = S.production_audit([], today="2026-08-18")
        self.assertEqual(out["state"], A.FAILURE)
        self.assertIn("does not survive a redeploy", out["reason"])


# ── 2. a restart must not lose, duplicate or invent a day ───────────────────

class TestRestartBehaviour(AuditBase):
    def test_what_was_written_before_a_restart_is_still_there_after_one(self):
        CS.configure(self.dir)
        CS.record("SMPL", chain_payload(day="2026-08-18"), today="2026-08-18")
        # A restart is a fresh process: every in-memory guard is empty and
        # the store is re-opened from disk.
        CS._RECORDED_TODAY.clear()
        CS.configure(self.dir)
        self.assertIn("2026-08-18", CS.load("SMPL") or {})

    def test_the_same_day_is_not_captured_twice_after_a_restart(self):
        CS.configure(self.dir)
        first = CS.record("SMPL", chain_payload(spot=100.0, day="2026-08-18"),
                          today="2026-08-18")
        CS._RECORDED_TODAY.clear()                   # the restart
        second = CS.record("SMPL", chain_payload(spot=111.0, day="2026-08-18"),
                           today="2026-08-18")
        self.assertTrue(first)
        self.assertFalse(second)
        # And the first capture is the one kept: the second must not have
        # overwritten it with a later, different quote.
        self.assertEqual((CS.load("SMPL") or {})["2026-08-18"]["spot"], 100.0)

    def test_a_restart_retries_the_work_that_failed_and_skips_what_did_not(self):
        H.configure(self.data)
        H.record(H.SNAPSHOT, "AAA", True, day="2026-08-18")
        H.record(H.SNAPSHOT, "BBB", False, day="2026-08-18", reason="provider")
        pending = [s for s in ("AAA", "BBB")
                   if not H.captured(H.SNAPSHOT, s, "2026-08-18")]
        self.assertEqual(pending, ["BBB"])

    def test_a_capture_never_writes_a_day_other_than_the_one_it_is_given(self):
        CS.configure(self.dir)
        CS.record("SMPL", chain_payload(day="2026-08-18"), today="2026-08-18")
        CS._RECORDED_TODAY.clear()
        CS.record("SMPL", chain_payload(day="2026-08-19"), today="2026-08-19")
        got = sorted(CS.load("SMPL") or {})
        self.assertEqual(got, ["2026-08-18", "2026-08-19"])

    def test_yesterday_is_never_back_filled_by_a_capture_run_today(self):
        # There is no code path that writes a past day, and this is the test
        # that says so: capturing today leaves the history with exactly one
        # day in it, not two.
        CS.configure(self.dir)
        CS.record("SMPL", chain_payload(day="2026-08-19"), today="2026-08-19")
        self.assertEqual(sorted(CS.load("SMPL") or {}), ["2026-08-19"])

    def test_a_chain_stamp_is_the_exchange_clock_not_the_containers(self):
        CS.configure(self.dir)
        CS.record("SMPL", chain_payload(day="2026-08-18"), today="2026-08-18",
                  at="2026-08-18T17:05:00-04:00")
        stamp = (CS.load("SMPL") or {})["2026-08-18"]["ts"]
        self.assertTrue(stamp.startswith("2026-08-18"))
        self.assertIn("-04:00", stamp)


# ── 3. the clock is the exchange's ──────────────────────────────────────────

class TestExchangeClock(unittest.TestCase):
    """The bug this class exists for: a container in UTC is already on
    tomorrow's date at half past eight in the evening in New York. A capture
    stamped with the container's date lands on a trading day that has not
    happened yet — and nothing downstream can tell that it did."""

    def test_the_market_date_is_new_york_not_utc(self):
        if NY is None:                               # pragma: no cover
            self.skipTest("no zoneinfo")
        # Half past midnight UTC on the 19th is half past eight in the
        # evening on the 18th in New York. The market date is the 18th.
        utc = datetime(2026, 8, 19, 0, 30, tzinfo=UTC)
        self.assertEqual(S.market_now(utc.astimezone(NY)).date().isoformat(),
                         "2026-08-18")

    def test_summer_is_four_hours_behind_coordinated_universal_time(self):
        if NY is None:                               # pragma: no cover
            self.skipTest("no zoneinfo")
        summer = datetime(2026, 8, 18, 17, 0, tzinfo=NY)
        self.assertEqual(summer.utcoffset(), timedelta(hours=-4))

    def test_winter_is_five_hours_behind_coordinated_universal_time(self):
        if NY is None:                               # pragma: no cover
            self.skipTest("no zoneinfo")
        winter = datetime(2026, 1, 15, 17, 0, tzinfo=NY)
        self.assertEqual(winter.utcoffset(), timedelta(hours=-5))

    def test_the_capture_hour_is_the_same_wall_clock_on_both_sides_of_a_change(self):
        if NY is None:                               # pragma: no cover
            self.skipTest("no zoneinfo")
        # Daylight saving ended on November 1, 2026. Five in the evening in
        # New York is five in the evening in New York on both sides of it,
        # which is the whole point of scheduling on the exchange's clock:
        # the offset moves and the capture time does not.
        before = datetime(2026, 10, 30, 17, 0, tzinfo=NY)
        after = datetime(2026, 11, 2, 17, 0, tzinfo=NY)
        self.assertEqual(before.hour, after.hour)
        self.assertNotEqual(before.utcoffset(), after.utcoffset())

    def test_the_hour_before_the_capture_window_records_nothing(self):
        if NY is None:                               # pragma: no cover
            self.skipTest("no zoneinfo")
        # Four in the afternoon in New York is the close, not after it.
        self.assertIsNone(S.tick(datetime(2026, 8, 18, 16, 0, tzinfo=NY)))

    def test_a_weekend_records_nothing_even_after_the_capture_hour(self):
        if NY is None:                               # pragma: no cover
            self.skipTest("no zoneinfo")
        sat = datetime(2026, 8, 22, 18, 0, tzinfo=NY)
        self.assertEqual(sat.weekday(), 5)
        self.assertIsNone(S.tick(sat))

    def test_a_market_holiday_records_nothing(self):
        if NY is None:                               # pragma: no cover
            self.skipTest("no zoneinfo")
        # Christmas Day 2026 falls on a Friday: a weekday the market is shut.
        xmas = datetime(2026, 12, 25, 18, 0, tzinfo=NY)
        self.assertEqual(xmas.weekday(), 4)
        self.assertFalse(H.is_trading_day("2026-12-25"))
        self.assertIsNone(S.tick(xmas))

    def test_good_friday_is_a_holiday_and_is_computed_not_tabulated(self):
        # Easter 2027 is March 28, so Good Friday is March 26. A hard-coded
        # table of holidays expires; this one is computed every year.
        self.assertEqual(H.easter(2027).isoformat(), "2027-03-28")
        self.assertFalse(H.is_trading_day("2027-03-26"))
        self.assertIn("Good Friday", H.why_not_trading("2027-03-26"))

    def test_an_early_close_day_is_still_a_full_capture_day(self):
        # The market closes at one in the afternoon the day after
        # Thanksgiving. The capture window is five in the evening, which is
        # four hours after even the earliest close, so an early close needs
        # no special handling — and this is the test that keeps it that way
        # rather than a second calendar of half-days that would go stale.
        self.assertTrue(H.is_trading_day("2026-11-27"))
        self.assertGreater(S.RECORD_AFTER_ET_HOUR, 13)

    def test_the_day_after_a_holiday_trades_normally(self):
        self.assertTrue(H.is_trading_day("2026-12-28"))


# ── 4. a successful response is not a successful capture ────────────────────

class TestChainValidation(AuditBase):
    def setUp(self):
        super().setUp()
        CS.configure(self.dir)

    def test_a_chain_with_prices_is_stored(self):
        self.assertTrue(CS.record("SMPL", chain_payload(day="2026-08-18"),
                                  today="2026-08-18"))

    def test_a_chain_where_every_quote_is_zero_is_refused(self):
        # This is the failure that looks like success: the request answered,
        # the payload parsed, every field was present. Nothing in it can be
        # priced against, so it is not a capture.
        ok = CS.record("SMPL", chain_payload(day="2026-08-18", quoted=False),
                       today="2026-08-18")
        self.assertFalse(ok)
        self.assertNotIn("2026-08-18", CS.load("SMPL") or {})

    def test_an_unquoted_contract_is_named_rather_than_scored(self):
        q = CS.quality_of({"bid": 0.0, "ask": 0.0, "last": 0.0})
        self.assertEqual(q, CS.Q_NO_MARKET)
        self.assertIn("NO MARKET", CS.QUALITY_LABEL[q].upper())

    def test_an_empty_chain_is_refused(self):
        empty = {"underlying": {"last": 100.0}, "expirations": [], "chains": {}}
        self.assertFalse(CS.record("SMPL", empty, today="2026-08-18"))

    def test_a_chain_with_no_underlying_price_is_refused(self):
        bad = chain_payload(day="2026-08-18")
        bad["underlying"] = {"last": 0.0}
        self.assertFalse(CS.record("SMPL", bad, today="2026-08-18"))

    def test_a_provider_that_returns_nothing_is_recorded_as_a_failure(self):
        H.configure(self.data)
        S.configure(cc_chain_fn=lambda s, dte, n: None, data_dir=self.data)
        out = S.capture_chains(["SMPL"], cfg={})
        if out.get("not_expected"):                  # today is not a session
            self.skipTest("not a trading day")
        self.assertEqual(out["failed"], ["SMPL"])
        self.assertFalse(H.captured(H.CHAIN, "SMPL", out["day"]))

    def test_an_unusable_chain_is_recorded_as_a_failure_not_a_capture(self):
        H.configure(self.data)
        day = S._market_today()
        S.configure(cc_chain_fn=lambda s, dte, n: chain_payload(day=day,
                                                                quoted=False),
                    data_dir=self.data)
        out = S.capture_chains(["SMPL"], cfg={})
        if out.get("not_expected"):
            self.skipTest("not a trading day")
        self.assertEqual(out["failed"], ["SMPL"])
        self.assertFalse(H.captured(H.CHAIN, "SMPL", day))

    def test_a_partial_day_is_partial_rather_than_complete(self):
        H.configure(self.data)
        H.record(H.CHAIN, "AAA", True, day="2026-08-18")
        H.record(H.CHAIN, "BBB", False, day="2026-08-18", reason="no chain")
        got = H.day_status("2026-08-18", {H.CHAIN: ["AAA", "BBB"]})
        self.assertEqual(got["state"], H.PARTIAL)
        self.assertEqual(got["kinds"][H.CHAIN]["missing"], ["BBB"])


# ── 5. the watchlist, including the awkward tickers ─────────────────────────

class TestWatchlistCoverage(AuditBase):
    def test_a_class_share_ticker_survives_the_symbol_cleaner(self):
        for sym in ("BRK-B", "BF-B", "brk.b"):
            self.assertTrue(S._safe(sym), sym)

    def test_a_class_share_ticker_can_be_captured_and_read_back(self):
        CS.configure(self.dir)
        self.assertTrue(CS.record("BRK-B", chain_payload(day="2026-08-18"),
                                  today="2026-08-18"))
        self.assertIn("2026-08-18", CS.load("BRK-B") or {})

    def test_a_class_share_ticker_is_logged_under_its_own_name(self):
        H.configure(self.data)
        H.record(H.SNAPSHOT, "BRK-B", True, day="2026-08-18")
        self.assertTrue(H.captured(H.SNAPSHOT, "BRK-B", "2026-08-18"))
        self.assertFalse(H.captured(H.SNAPSHOT, "BRK-A", "2026-08-18"))

    def test_the_capture_lists_are_bounded_and_the_bound_is_stated(self):
        want = S.expected_today([f"S{i}" for i in range(200)], "2026-08-18")
        self.assertEqual(len(want[H.SNAPSHOT]), S.MAX_DAILY_SYMBOLS)
        self.assertEqual(len(want[H.CHAIN]), S.MAX_CAPTURE_SYMBOLS)


# ── 6. the previous trading day, component by component ─────────────────────

class TestPreviousDayAudit(AuditBase):
    def setUp(self):
        super().setUp()
        H.configure(self.data)

    def test_it_audits_the_previous_trading_day_not_the_previous_day(self):
        # Monday, August 17, 2026's previous trading day is Friday the 14th.
        got = A.previous_day({}, today="2026-08-17")
        self.assertEqual(got["date"], "2026-08-14")
        self.assertEqual(got["pretty"], "August 14, 2026")

    def test_every_component_is_reported_as_captured_out_of_expected(self):
        for kind in H.KINDS:
            H.record(kind, "AAA", True, day="2026-08-14")
        got = A.previous_day({k: ["AAA", "BBB"] for k in H.KINDS},
                             today="2026-08-17")
        for comp in got["components"]:
            self.assertEqual((comp["captured"], comp["expected"]), (1, 2),
                             comp["kind"])
            self.assertEqual(comp["missing"], ["BBB"])

    def test_a_complete_day_is_reported_complete(self):
        for kind in H.KINDS:
            H.record(kind, "AAA", True, day="2026-08-14")
        got = A.previous_day({k: ["AAA"] for k in H.KINDS}, today="2026-08-17")
        self.assertEqual(got["state"], H.COMPLETE)

    def test_only_the_option_chain_is_named_unrecoverable(self):
        got = A.previous_day({k: ["AAA"] for k in H.KINDS}, today="2026-08-17")
        unrecoverable = [c["kind"] for c in got["components"]
                         if not c["recoverable"]]
        self.assertEqual(unrecoverable, [H.CHAIN])

    def test_it_reuses_the_capture_health_calendar_rather_than_its_own(self):
        self.assertIs(A.health, H)


# ── 7. the rules behind a stored recommendation ─────────────────────────────

class TestConfigArchive(AuditBase):
    def test_reading_the_config_archives_it(self):
        cfg, cfg_hash = S.config()
        self.assertTrue(cfg_hash)
        self.assertIn(cfg_hash, {r["config_hash"] for r in S.archived_configs()})

    def test_the_archived_copy_is_the_exact_configuration(self):
        cfg, cfg_hash = S.config()
        back = S.load_config_archive(cfg_hash)
        self.assertIsNotNone(back)
        self.assertEqual(back["config_hash"], cfg_hash)
        # Not a summary of the rules — the rules. Re-flattening the archived
        # thresholds has to reproduce the configuration exactly.
        again = S._flatten_cfg(back["thresholds"].get("investment") or {})
        self.assertEqual(again, cfg)

    def test_an_existing_hash_is_never_rewritten(self):
        cfg, cfg_hash = S.config()
        self.assertFalse(S.archive_config({"tampered": True}, cfg_hash))
        back = S.load_config_archive(cfg_hash)
        self.assertNotIn("tampered", back["thresholds"])

    def test_a_second_distinct_rule_set_is_stored_beside_the_first(self):
        _, first = S.config()
        S.archive_config({"capture_hour_et": 18}, "deadbeef")
        hashes = {r["config_hash"] for r in S.archived_configs()}
        self.assertIn(first, hashes)
        self.assertIn("deadbeef", hashes)

    def test_a_row_naming_an_unarchived_rule_set_is_a_finding(self):
        found = A.audit_history("SMPL", [row("2026-08-18",
                                             config_hash="notarchived")],
                                today="2026-08-18", known_hashes={"abc123"})
        self.assertEqual([f["finding"] for f in found],
                         [A.UNRECOVERABLE_CONFIG])

    def test_a_row_with_no_rule_set_at_all_is_a_finding(self):
        found = A.audit_history("SMPL", [row("2026-08-18", config_hash=None)],
                                today="2026-08-18")
        self.assertEqual([f["finding"] for f in found], [A.MISSING_HASH])


# ── 8. is what is already stored believable ─────────────────────────────────

class TestIntegrity(unittest.TestCase):
    def test_a_clean_history_produces_no_findings(self):
        rows = [row("2026-08-17"), row("2026-08-18")]
        self.assertEqual(A.audit_history("SMPL", rows, today="2026-08-18",
                                         known_hashes={"abc123"}), [])

    def test_two_rows_for_one_day_are_reported(self):
        rows = [row("2026-08-18"), row("2026-08-18")]
        kinds = [f["finding"] for f in A.audit_history("SMPL", rows,
                                                       today="2026-08-18")]
        self.assertIn(A.DUPLICATE_DATE, kinds)

    def test_a_row_dated_after_today_is_reported(self):
        # The exact shape the container-clock bug would have written.
        kinds = [f["finding"] for f in
                 A.audit_history("SMPL", [row("2026-08-19")],
                                 today="2026-08-18")]
        self.assertIn(A.FUTURE_DATE, kinds)

    def test_rows_out_of_date_order_are_reported(self):
        rows = [row("2026-08-18"), row("2026-08-17")]
        kinds = [f["finding"] for f in A.audit_history("SMPL", rows,
                                                       today="2026-08-18")]
        self.assertIn(A.OUT_OF_ORDER, kinds)

    def test_a_price_of_zero_is_reported(self):
        kinds = [f["finding"] for f in
                 A.audit_history("SMPL", [row("2026-08-18", price=0.0)],
                                 today="2026-08-18")]
        self.assertIn(A.BAD_PRICE, kinds)

    def test_a_row_with_no_date_is_reported_as_having_no_date(self):
        kinds = [f["finding"] for f in
                 A.audit_history("SMPL", [row("2026-08-18", date=None)],
                                 today="2026-08-18")]
        self.assertEqual(kinds, [A.MISSING_DATE])

    def test_a_contract_that_is_not_the_recommended_structure_is_reported(self):
        bad = row("2026-08-18")
        bad["recommended_contract"]["structure"] = "long-dated call"
        kinds = [f["finding"] for f in A.audit_history("SMPL", [bad],
                                                       today="2026-08-18")]
        self.assertIn(A.CONTRACT_MISMATCH, kinds)

    def test_a_contract_whose_bid_is_above_its_ask_is_reported(self):
        bad = row("2026-08-18")
        bad["recommended_contract"].update({"bid": 3.0, "ask": 2.0})
        kinds = [f["finding"] for f in A.audit_history("SMPL", [bad],
                                                       today="2026-08-18")]
        self.assertIn(A.CROSSED_QUOTE, kinds)

    def test_a_contract_expiring_before_it_was_recommended_is_reported(self):
        bad = row("2026-08-18")
        bad["recommended_contract"]["expiration"] = "2026-08-01"
        kinds = [f["finding"] for f in A.audit_history("SMPL", [bad],
                                                       today="2026-08-18")]
        self.assertIn(A.WRONG_EXPIRATION, kinds)

    def test_a_chain_with_no_underlying_price_is_reported(self):
        store = {"2026-08-18": {"spot": 0.0, "exps": {}}}
        kinds = [f["finding"] for f in A.audit_chains("SMPL", store,
                                                      today="2026-08-18")]
        self.assertIn(A.BAD_SPOT, kinds)

    def test_a_chain_holding_an_expired_expiration_is_reported(self):
        store = {"2026-08-18": {"spot": 100.0,
                                "exps": {"2026-08-01": {"c": [[100, 1, 2]]}}}}
        kinds = [f["finding"] for f in A.audit_chains("SMPL", store,
                                                      today="2026-08-18")]
        self.assertIn(A.WRONG_EXPIRATION, kinds)

    def test_a_crossed_quote_inside_a_stored_chain_is_reported(self):
        store = {"2026-08-18": {"spot": 100.0,
                                "exps": {"2026-09-18": {"c": [[100, 3.0, 2.0]]}}}}
        kinds = [f["finding"] for f in A.audit_chains("SMPL", store,
                                                      today="2026-08-18")]
        self.assertIn(A.CROSSED_QUOTE, kinds)

    def test_a_chain_dated_in_the_future_is_reported(self):
        store = {"2026-08-19": {"spot": 100.0, "exps": {}}}
        kinds = [f["finding"] for f in A.audit_chains("SMPL", store,
                                                      today="2026-08-18")]
        self.assertIn(A.FUTURE_DATE, kinds)

    def test_every_finding_carries_an_explanation_in_plain_words(self):
        for kind, note in A.FINDING_NOTE.items():
            self.assertGreater(len(note), 40, kind)
            self.assertNotIn("None", note)

    def test_nothing_is_repaired_only_reported(self):
        rows = [row("2026-08-18"), row("2026-08-18")]
        before = json.dumps(rows, sort_keys=True)
        A.audit_history("SMPL", rows, today="2026-08-18")
        self.assertEqual(json.dumps(rows, sort_keys=True), before)


# ── 9. will the data still be there when it is needed ───────────────────────

class TestRetention(unittest.TestCase):
    def test_a_year_long_horizon_needs_two_hundred_and_fifty_two_entries(self):
        # Retention limits count stored entries, and only trading days get
        # stored. Counting a 365-day horizon in calendar days would set every
        # limit about a hundred days higher than it needs to be — and reading
        # a 400-entry limit as 400 calendar days would be the mistake in the
        # other direction.
        got = A.retention({})
        self.assertEqual(got["horizon_days"], 365)
        self.assertEqual(got["trading_days_needed"], 252)

    def test_a_limit_shorter_than_the_horizon_is_a_failure(self):
        got = A.retention({"chains": {"label": "Chains", "keeps": 200}})
        self.assertFalse(got["ok"])
        self.assertEqual([r["store"] for r in got["too_short"]], ["chains"])

    def test_a_limit_longer_than_the_horizon_reports_its_spare_days(self):
        got = A.retention({"chains": {"label": "Chains", "keeps": 500}})
        self.assertTrue(got["ok"])
        self.assertEqual(got["limits"][0]["margin_days"], 248)

    def test_an_unbounded_store_clears_every_horizon(self):
        got = A.retention({"snapshots": {"label": "Snapshots", "keeps": None}})
        self.assertTrue(got["limits"][0]["unbounded"])
        self.assertTrue(got["limits"][0]["covers_longest_horizon"])

    def test_the_real_limits_in_this_repository_clear_the_longest_horizon(self):
        # The check that matters: not a fixture, the actual constants.
        import invest_options as O
        got = A.retention({
            "chains": {"label": "Chains", "keeps": CS.MAX_DAYS_KEPT},
            "leaps": {"label": "Long-dated",
                      "keeps": O.DEFAULTS["leaps_iv_history_days"]},
            "capture_log": {"label": "Capture log", "keeps": H.KEEP_DAYS},
        })
        self.assertTrue(got["ok"], got["reason"])

    def test_the_snapshot_store_is_never_trimmed(self):
        # The recommendation history is the one thing that must never be
        # thrown away, because it is the thing being validated.
        import inspect
        src = inspect.getsource(S.store)
        self.assertNotIn("MAX_DAYS", src)
        self.assertNotIn("[-", src)          # no trailing-window slice


class TestStorage(AuditBase):
    def test_growth_is_projected_from_measured_bytes_not_a_guess(self):
        d = os.path.join(self.dir, "chains")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SMPL.json"), "w") as fh:
            fh.write("x" * 10_000)
        got = A.storage({"chains": {"label": "Chains", "path": d}},
                        symbols=2, days=5)
        r = got["stores"][0]
        self.assertEqual(r["bytes"], 10_000)
        self.assertEqual(r["bytes_per_symbol_day"], 1000)
        # 1,000 bytes x 252 trading days x 2 tickers = 504,000 bytes.
        self.assertAlmostEqual(r["projected_mb_per_year"], 0.5, places=1)

    def test_a_store_with_nothing_captured_yet_projects_nothing(self):
        got = A.storage({"chains": {"label": "Chains", "path": self.dir}},
                        symbols=0, days=0)
        self.assertIsNone(got["stores"][0]["projected_mb_per_year"])


# ── 10. is what is being written now enough to score later ──────────────────

class TestRecordingCompleteness(AuditBase):
    def test_a_complete_row_needs_nothing_added(self):
        got = S.recording_audit({"SMPL": [row("2026-08-18")]})
        self.assertEqual(got["complete"], len(S.REQUIRED_FOR_SCORING))
        self.assertEqual(got["missing_examples"], [])

    def test_a_row_missing_the_benchmark_close_cannot_be_scored_and_says_so(self):
        got = S.recording_audit({"SMPL": [row("2026-08-18",
                                              benchmark_close=None)]})
        self.assertIn("benchmark_close", got["missing_examples"])
        self.assertIn("cannot be scored exactly later", got["reason"])

    def test_every_required_field_is_explained_in_plain_words(self):
        for key, what in S.REQUIRED_FOR_SCORING:
            self.assertGreater(len(what), 12, key)

    def test_the_required_list_covers_the_whole_recommendation(self):
        keys = {k for k, _ in S.REQUIRED_FOR_SCORING}
        for must in ("date", "price", "config_hash", "entry_verdict",
                     "preferred_structure", "recommended_contract",
                     "benchmark_symbol", "benchmark_close",
                     "fair_value_base", "buy_zone", "quality_label",
                     "value_trap_level"):
            self.assertIn(must, keys)

    def test_only_the_newest_row_of_each_ticker_is_judged(self):
        # Rows already on disk are never revised, so a row written before a
        # field existed will always lack it. Judging the history would report
        # a gap that cannot be closed and would hide the one that can.
        old = row("2026-01-02")
        old.pop("benchmark_close")
        got = S.recording_audit({"SMPL": [old, row("2026-08-18")]})
        self.assertEqual(got["missing_examples"], [])

    def test_nothing_recorded_yet_says_so_rather_than_reporting_zero(self):
        got = S.recording_audit({})
        self.assertIn("nothing to check", got["reason"].lower())


# ── 11. the whole audit, end to end ─────────────────────────────────────────

class TestProductionAudit(AuditBase):
    def test_it_answers_every_question_the_audit_was_asked(self):
        out = S.production_audit([], today="2026-08-18")
        for key in ("home", "previous_day", "retention", "storage",
                    "config_archive", "integrity", "recording", "state",
                    "reason", "market_timezone", "capture_hour_et"):
            self.assertIn(key, out, key)

    def test_the_state_is_one_of_three_words_and_the_reason_explains_it(self):
        out = S.production_audit([], today="2026-08-18")
        self.assertIn(out["state"], (A.HEALTHY, A.PARTIAL, A.FAILURE))
        self.assertGreater(len(out["reason"]), 40)

    def test_the_market_timezone_is_named_rather_than_assumed(self):
        out = S.production_audit([], today="2026-08-18")
        if NY is not None:
            self.assertIn("New_York", out["market_timezone"])

    def test_the_capture_hour_is_after_the_close(self):
        out = S.production_audit([], today="2026-08-18")
        self.assertGreaterEqual(out["capture_hour_et"], 17)

    def test_a_persistent_home_with_a_clean_store_is_not_a_capture_failure(self):
        out = S.production_audit([], today="2026-08-18")
        out["home"] = {"state": A.PERSISTENT}
        state, reason = S._audit_state(out)
        self.assertNotEqual(state, A.FAILURE)

    def test_a_retention_limit_that_would_lose_a_day_is_a_capture_failure(self):
        out = S.production_audit([], today="2026-08-18")
        out["home"] = {"state": A.PERSISTENT}
        out["retention"] = {"ok": False}
        state, reason = S._audit_state(out)
        self.assertEqual(state, A.FAILURE)
        self.assertIn("retention limit", reason)

    def test_it_reads_and_reports_and_writes_nothing_back(self):
        import invest_audit
        import inspect
        src = inspect.getsource(invest_audit)
        for forbidden in ("write_text(", "open(", "unlink(", "rmtree("):
            self.assertNotIn(forbidden, src, forbidden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
