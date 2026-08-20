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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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

    def test_a_path_spelled_like_the_mount_point_is_not_taken_on_trust(self):
        # A DETACHED Railway volume leaves an ordinary /data directory on the
        # container's own disk: same device as the root, erased on the next
        # deploy. Calling it PERSISTENT because of how it is spelled would
        # put a green line over the exact failure this report exists for.
        got = A.data_home("/data/invest")
        self.assertEqual(got["state"], A.UNKNOWN)
        self.assertIn("SAME filesystem", got["reason"])

    def test_an_unconfirmed_volume_is_a_capture_failure_not_a_pass(self):
        out = S.production_audit([], today="2026-08-18")
        out["home"] = {"state": A.UNKNOWN}
        state, reason = S._audit_state(out)
        self.assertEqual(state, A.FAILURE)
        self.assertIn("cannot be confirmed", reason)

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




# ── 12. what a past day was actually due ────────────────────────────────────

class TestExpectedOnTheDay(AuditBase):
    """Judging a past day against the watchlist as it stands NOW is not
    evidence about that day. Star a ticker this morning and yesterday would
    be reported as having missed it; unstar one and a real miss vanishes."""

    def setUp(self):
        super().setUp()
        H.configure(self.data)

    def test_a_day_that_recorded_what_it_was_due_is_judged_against_that(self):
        H.expect({H.SNAPSHOT: ["AAA", "BBB"]}, day="2026-08-14")
        H.record(H.SNAPSHOT, "AAA", True, day="2026-08-14")
        # CCC was starred this morning. It was never due on the 14th.
        got = H.day_status("2026-08-14", {H.SNAPSHOT: ["AAA", "BBB", "CCC"]})
        self.assertEqual(got["expected_basis"], "recorded")
        self.assertEqual(got["kinds"][H.SNAPSHOT]["missing"], ["BBB"])

    def test_unstarring_a_ticker_does_not_erase_the_day_it_was_missed(self):
        H.expect({H.SNAPSHOT: ["AAA", "BBB"]}, day="2026-08-14")
        H.record(H.SNAPSHOT, "AAA", True, day="2026-08-14")
        got = H.day_status("2026-08-14", {H.SNAPSHOT: ["AAA"]})
        self.assertEqual(got["kinds"][H.SNAPSHOT]["missing"], ["BBB"])
        self.assertEqual(got["state"], H.PARTIAL)

    def test_the_expectation_is_unioned_so_a_restart_cannot_shrink_it(self):
        H.expect({H.SNAPSHOT: ["AAA", "BBB"]}, day="2026-08-14")
        H.expect({H.SNAPSHOT: ["BBB", "CCC"]}, day="2026-08-14")
        got = H.expected_on("2026-08-14")
        self.assertEqual(got["expected"][H.SNAPSHOT], ["AAA", "BBB", "CCC"])

    def test_a_day_that_recorded_nothing_falls_back_and_says_so(self):
        got = H.expected_on("2026-08-14", {H.SNAPSHOT: ["AAA"]})
        self.assertEqual(got["basis"], "watchlist now")
        self.assertIn("standing in for it", got["note"])

    def test_a_run_that_was_abandoned_is_not_called_complete(self):
        # The trap in deriving expectation from a day's own attempts: a
        # ticker the run never reached leaves no trace at all, so the day
        # would look finished. It must not.
        H.expect({H.SNAPSHOT: ["AAA", "BBB", "CCC"]}, day="2026-08-14")
        H.record(H.SNAPSHOT, "AAA", True, day="2026-08-14")
        got = H.day_status("2026-08-14", {})
        self.assertEqual(got["state"], H.PARTIAL)
        self.assertEqual(got["kinds"][H.SNAPSHOT]["missing"], ["BBB", "CCC"])

    def test_the_audit_reports_which_basis_it_used(self):
        H.expect({k: ["AAA"] for k in H.KINDS}, day="2026-08-14")
        got = A.previous_day({}, today="2026-08-17")
        self.assertEqual(got["expected_basis"], "recorded")
        self.assertEqual(got["expected_symbols"], ["AAA"])


# ── 13. the archive, the retention limit and the growth rate ────────────────

class TestReviewFindings(AuditBase):
    def test_an_empty_archive_makes_every_stored_row_unrecoverable(self):
        # Not "do not check" — "nothing can be traced back to its rules".
        found = A.audit_history("SMPL", [row("2026-08-18")],
                                today="2026-08-18", known_hashes=set())
        self.assertEqual([f["finding"] for f in found],
                         [A.UNRECOVERABLE_CONFIG])

    def test_the_check_is_skipped_only_when_it_is_explicitly_skipped(self):
        found = A.audit_history("SMPL", [row("2026-08-18")],
                                today="2026-08-18", known_hashes=None)
        self.assertEqual(found, [])

    def test_the_long_dated_store_trims_at_the_limit_it_is_given(self):
        # The configured limit used to be reported by the audit and ignored
        # by the writer, so readiness described a retention rule nothing
        # enforced.
        import invest_options as O
        O.configure(data_dir=self.dir)
        for i in range(6):
            day = f"2026-06-{i + 1:02d}"
            O.record_leaps_observation("SMPL", 100.0,
                                       [{"exp": "2028-01-21", "dte": 600,
                                         "strike": 100.0, "iv": 0.3}],
                                       today=day, keep=3)
        got = O.load_leaps_observations("SMPL")
        self.assertEqual([r["date"] for r in got],
                         ["2026-06-04", "2026-06-05", "2026-06-06"])
        O.configure(data_dir=None)

    def test_each_store_is_measured_against_its_own_coverage(self):
        a = os.path.join(self.dir, "a")
        b = os.path.join(self.dir, "b")
        for d, size in ((a, 10_000), (b, 6_000)):
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "X.json"), "w") as fh:
                fh.write("x" * size)
        got = A.storage({
            "a": {"label": "A", "path": a, "symbols": 2, "days": 5},
            "b": {"label": "B", "path": b, "symbols": 1, "days": 2},
        }, symbols=2, days=5)
        rows = {r["store"]: r for r in got["stores"]}
        self.assertEqual(rows["a"]["bytes_per_symbol_day"], 1000)
        self.assertEqual(rows["b"]["bytes_per_symbol_day"], 3000)

    def test_a_store_that_does_not_grow_daily_projects_nothing(self):
        d = os.path.join(self.dir, "cfg")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "abc.json"), "w") as fh:
            fh.write("x" * 500)
        got = A.storage({"config_archive": {
            "label": "Configuration archive", "path": d,
            "per_ticker": False, "days": 0,
            "coverage": "does not grow daily"}}, symbols=4, days=10)
        r = got["stores"][0]
        self.assertIsNone(r["projected_mb_per_year"])
        self.assertIn("Configuration archive", got["unmeasured"])
        self.assertIn("short by that much", got["reason"])

    def test_a_store_that_does_not_grow_per_ticker_is_not_multiplied(self):
        d = os.path.join(self.dir, "log")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "2026-08-18.json"), "w") as fh:
            fh.write("x" * 1000)
        got = A.storage({"capture_log": {
            "label": "Capture-health log", "path": d,
            "per_ticker": False, "days": 1}}, symbols=40, days=1)
        r = got["stores"][0]
        self.assertEqual(r["measured_symbols"], 1)
        self.assertAlmostEqual(r["projected_mb_per_year"], 0.3, places=1)



# ── 14. the panel's own contract ────────────────────────────────────────────

class TestCollectionStatus(AuditBase):
    def test_a_confirmed_writable_volume_is_ready_to_accumulate(self):
        status, reason = A.collection_status(
            {"state": A.PERSISTENT, "exists": True, "writable": True})
        self.assertEqual(status, A.READY)
        self.assertIn("Persistent volume confirmed", reason)

    def test_the_container_disk_is_blocked_and_says_what_is_lost(self):
        status, reason = A.collection_status({"state": A.EPHEMERAL})
        self.assertEqual(status, A.BLOCKED)
        self.assertIn("lost on redeploy", reason)

    def test_an_unconfirmed_volume_is_blocked_rather_than_assumed_good(self):
        status, reason = A.collection_status({"state": A.UNKNOWN})
        self.assertEqual(status, A.BLOCKED)
        self.assertIn("could not be confirmed", reason)

    def test_a_missing_home_is_blocked(self):
        self.assertEqual(A.collection_status({})[0], A.BLOCKED)

    def test_every_store_that_matters_names_a_path(self):
        out = S.production_audit([], today="2026-08-18")
        got = {r["key"]: r for r in out["paths"]}
        for key in ("root", "snapshots", "chains", "leaps", "capture",
                    "config"):
            self.assertIn(key, got, key)
            self.assertTrue(got[key]["path"], key)
            self.assertGreater(len(got[key]["what"]), 40, key)

    def test_only_the_option_chain_is_named_unrecoverable(self):
        out = S.production_audit([], today="2026-08-18")
        gone = [r["key"] for r in out["paths"] if not r["recoverable"]]
        self.assertEqual(gone, ["chains"])

    def test_the_audit_carries_the_status_line_and_its_reason(self):
        out = S.production_audit([], today="2026-08-18")
        self.assertIn(out["collection_status"], (A.READY, A.BLOCKED))
        self.assertIn(out["collection_reason"], A.FINDING.values())

    def test_a_volume_that_cannot_be_written_to_is_not_ready(self):
        # A read-only mount survives a redeploy and stores nothing. Saying
        # READY over it would certify a directory that loses every capture
        # at the moment it is taken rather than at the next deploy.
        status, reason = A.collection_status(
            {"state": A.PERSISTENT, "exists": True, "writable": False})
        self.assertEqual(status, A.BLOCKED)
        self.assertIn("cannot be written to", reason)

    def test_a_configured_path_with_no_directory_is_not_ready(self):
        status, reason = A.collection_status(
            {"state": A.PERSISTENT, "exists": False, "writable": False})
        self.assertEqual(status, A.BLOCKED)
        self.assertIn("does not exist", reason)

    def test_a_writable_confirmed_volume_is_the_only_ready_case(self):
        status, _ = A.collection_status(
            {"state": A.PERSISTENT, "exists": True, "writable": True})
        self.assertEqual(status, A.READY)

    def test_a_directory_the_app_created_is_not_a_store_with_data_in_it(self):
        # configure() makes these directories at startup, so existence alone
        # would report every one of them as written to on an empty install.
        out = S.production_audit([], today="2026-08-18")
        got = {r["key"]: r for r in out["paths"]}
        self.assertTrue(got["snapshots"]["exists"])
        self.assertEqual(got["snapshots"]["files"], 0)
        self.assertFalse(got["snapshots"]["written"])

    def test_a_store_with_something_in_it_says_how_much(self):
        # The configuration archive is written the first time the config is
        # read, so it is the one store with a file in it on a fresh install.
        out = S.production_audit([], today="2026-08-18")
        got = {r["key"]: r for r in out["paths"]}
        self.assertGreater(got["config"]["files"], 0)
        self.assertTrue(got["config"]["written"])

    def test_a_half_written_temporary_file_is_not_counted(self):
        d = os.path.join(self.dir, "store")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "x.json.tmp"), "w") as fh:
            fh.write("{}")
        self.assertEqual(A._holds(d), 0)
        with open(os.path.join(d, "x.json"), "w") as fh:
            fh.write("{}")
        self.assertEqual(A._holds(d), 1)

    def test_the_screen_says_not_persistent_rather_than_ephemeral(self):
        self.assertEqual(A.STORAGE_LABEL[A.EPHEMERAL], "NOT PERSISTENT")
        self.assertEqual(A.STORAGE_LABEL[A.PERSISTENT], "PERSISTENT")
        self.assertEqual(A.STORAGE_LABEL[A.UNKNOWN], "UNKNOWN")



# ── 15. the market clock, which is never the container's ────────────────────

class TestMarketClock(unittest.TestCase):
    """The bug this class exists for: at 9:22 in the evening in New York the
    readiness panel said NOT DUE YET and listed all twelve tickers as still
    to come. A container in coordinated universal time was already on the
    next day at 01:22, and 1 is less than the 17:00 capture hour — so the
    window had 'not opened'. The clock has no container fallback now."""

    def setUp(self):
        self.tz, self.src = S._MARKET_TZ, S._TZ_SOURCE

    def tearDown(self):
        S._MARKET_TZ, S._TZ_SOURCE = self.tz, self.src

    def test_with_no_time_zone_database_the_clock_is_still_new_york(self):
        S._MARKET_TZ = None
        now = S.market_now()
        self.assertIsNotNone(now.utcoffset())
        self.assertIn(now.utcoffset(), (timedelta(hours=-4),
                                        timedelta(hours=-5)))

    def test_the_evening_capture_window_opens_without_a_database(self):
        # 21:22 in New York is 01:22 the next day in coordinated universal
        # time. Read on the container's clock the window looks shut.
        S._MARKET_TZ = None
        utc = datetime(2026, 8, 20, 1, 22, tzinfo=timezone.utc)
        eastern = (utc + S._eastern_offset(utc)).replace(tzinfo=None)
        self.assertEqual(eastern.date().isoformat(), "2026-08-19")
        self.assertEqual(eastern.hour, 21)
        self.assertGreaterEqual(eastern.hour, S.RECORD_AFTER_ET_HOUR)

    def test_the_offset_is_right_on_both_sides_of_both_changes(self):
        for iso, want in (
                ("2026-01-15T18:00:00+00:00", -5),   # deep winter
                ("2026-08-19T18:00:00+00:00", -4),   # deep summer
                ("2026-03-08T06:59:00+00:00", -5),   # a minute before spring
                ("2026-03-08T07:00:00+00:00", -4),   # the moment it changes
                ("2026-11-01T05:59:00+00:00", -4),   # a minute before autumn
                ("2026-11-01T06:00:00+00:00", -5)):  # the moment it changes
            got = S._eastern_offset(datetime.fromisoformat(iso))
            self.assertEqual(got, timedelta(hours=want), iso)

    def test_daylight_saving_starts_on_the_second_sunday_in_march(self):
        self.assertEqual(S._nth_sunday(2026, 3, 2).isoformat(), "2026-03-08")
        self.assertEqual(S._nth_sunday(2027, 3, 2).isoformat(), "2027-03-14")

    def test_daylight_saving_ends_on_the_first_sunday_in_november(self):
        self.assertEqual(S._nth_sunday(2026, 11, 1).isoformat(), "2026-11-01")
        self.assertEqual(S._nth_sunday(2027, 11, 1).isoformat(), "2027-11-07")

    def test_every_instant_in_a_year_round_trips_without_a_database(self):
        # The offset for an INSTANT and the offset for a WALL READING are
        # different questions. Asking the second with the first's rule put
        # the hours after a change on the wrong side of it, and stamped the
        # moment an hour out. This sweeps the whole year at half-hour steps.
        S._MARKET_TZ = None
        u = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bad = []
        while u.year == 2026:
            local = S.eastern(u)
            if local.astimezone(timezone.utc) != u:
                bad.append(u.isoformat())
            u += timedelta(minutes=30)
        self.assertEqual(bad[:3], [], f"{len(bad)} instants did not round-trip")

    def test_the_hour_after_the_spring_change_is_daylight_time(self):
        S._MARKET_TZ = None
        got = S.eastern(datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc))
        self.assertEqual(got.strftime("%H:%M %Z"), "03:30 EDT")

    def test_the_repeated_november_hour_keeps_both_of_its_instants(self):
        # 01:30 on the first Sunday in November happens twice. Read back as
        # one instant, an hour of stamps would be wrong.
        S._MARKET_TZ = None
        first = S.eastern(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
        second = S.eastern(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))
        self.assertEqual(first.strftime("%H:%M %Z"), "01:30 EDT")
        self.assertEqual(second.strftime("%H:%M %Z"), "01:30 EST")
        self.assertNotEqual(first.astimezone(timezone.utc),
                            second.astimezone(timezone.utc))

    def test_the_clock_says_which_clock_it_is(self):
        got = S.market_clock()
        self.assertIn("New_York", got["zone"])
        self.assertIn(got["abbreviation"], ("EST", "EDT"))
        self.assertIn(got["utc_offset_hours"], (-4.0, -5.0))
        self.assertGreater(len(got["source"]), 20)

    def test_the_time_zone_database_is_a_declared_dependency(self):
        # zoneinfo reads the operating system's database, and a slim
        # container may carry none. Without this line the app silently
        # computes its own Eastern time instead, which works — but the
        # database is the better answer and it should not be left to chance.
        reqs = Path("requirements.txt").read_text()
        self.assertIn("tzdata", reqs)


# ── 16. readiness dates itself, and resolves after the window ───────────────

class TestReadinessIsNeverStale(AuditBase):
    def setUp(self):
        super().setUp()
        H.configure(self.data)

    def test_it_says_when_it_was_calculated_on_the_exchange_clock(self):
        out = S.data_readiness(["AAA"])
        self.assertTrue(out["calculated_at"])
        # An offset, not a naive timestamp: the reader has to be able to see
        # which clock produced it.
        self.assertTrue(out["calculated_at"].endswith(("-04:00", "-05:00")))
        self.assertIn(out["market_clock"]["abbreviation"], ("EST", "EDT"))

    def test_after_the_capture_hour_today_has_a_real_state(self):
        # Whatever the answer is, it is one of the three. NOT DUE YET past
        # the window was the symptom of a wrong clock and is not a state the
        # panel is allowed to rest in.
        out = S.data_readiness(["AAA"])
        if out["capture_due_yet"] and out["trading_day"]:
            self.assertIn(out["today"]["state"],
                          (H.COMPLETE, H.PARTIAL, H.FAILURE))
            self.assertNotEqual(out["today"]["state"], S.NOT_DUE_YET)

    def test_a_trading_day_with_nothing_captured_is_a_capture_failure(self):
        day = "2026-08-19"
        if not H.is_trading_day(day):                # pragma: no cover
            self.skipTest("fixture day is not a trading day")
        out = S.data_readiness(["AAA"], today=day)
        self.assertEqual(out["today"]["state"], H.FAILURE)

    def test_before_the_capture_hour_not_due_yet_is_still_allowed(self):
        # The state is not banned — only banned from surviving the window.
        self.assertEqual(S.NOT_DUE_YET, "NOT DUE YET")


# ── 17. today, ticker by ticker ─────────────────────────────────────────────

class TestDayReport(AuditBase):
    def setUp(self):
        super().setUp()
        H.configure(self.data)
        CS.configure(self.dir)

    def _store(self, sym, day, **over):
        S.store({"symbol": sym, "as_of": day + "T17:05:00-04:00",
                 **{k: v for k, v in row(day, **over).items()
                    if k not in ("date", "ticker")}})

    def test_a_ticker_with_nothing_recorded_says_so(self):
        out = S.day_report(["AAA"], day="2026-08-19")
        r = out["rows"][0]
        self.assertFalse(r["snapshot"])
        self.assertFalse(r["forward_test_eligible"])
        self.assertEqual(r["why_not"], ["NO SNAPSHOT RECORDED"])

    def test_the_report_names_every_component_of_a_usable_day(self):
        out = S.day_report(["AAA"], day="2026-08-19")
        for key in ("snapshot", "option_chain", "leaps_observation",
                    "benchmark_close", "recommendation", "contract_required",
                    "config_archived", "forward_test_eligible"):
            self.assertIn(key, out["rows"][0], key)

    def test_it_is_dated_on_the_exchange_clock(self):
        out = S.day_report(["AAA"], day="2026-08-19")
        self.assertTrue(out["calculated_at"].endswith(("-04:00", "-05:00")))
        self.assertEqual(out["pretty"], "August 19, 2026")

    def test_a_day_is_only_fully_usable_when_every_ticker_is(self):
        out = S.day_report(["AAA", "BBB"], day="2026-08-19")
        self.assertFalse(out["first_fully_usable_day"])
        self.assertEqual(out["expected"], 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
