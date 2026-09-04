"""test_v481_board_identity.py — the defects the live v4.80 board showed.

Reported: clicking the Rank header changed the arrow and appeared to ADD
duplicate rows. Two separate faults were behind the screenshot.

  1. IDENTITY. The board keyed a row by symbol|strategy|expiration|short
     strike. Three TSLA put credit spreads shared a short strike of 357.5
     and differed only in the wing; two AAPL iron condors shared a put
     wing. Same key, different trades. React reconciles a keyed list by
     key, so re-sorting a list with duplicate keys leaves stale rows
     mounted — the duplicates the user saw. The same collision merged the
     risk-pathway map and the forward-test records.

  2. STALENESS. The board persists to disk and reloads on restart, and
     nothing dropped a symbol evaluated on an earlier day. Contracts
     EXPIRING TODAY were still on the board labeled "1 day", carrying
     yesterday's spot and yesterday's quotes, under a header stamped with
     today's newest scan time.

  3. TIMESTAMPS. `as_of` was naive, so a UTC server's 10:39 stamp rendered
     as 10:39 in a viewer's own zone whose clock read 6:39, and the age
     beside it came out negative.
"""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import sell_scan as ss
import sp_engine as E


def _spread(short_strike, long_strike, **kw):
    base = {"symbol": "TSLA", "strategy": "put_credit_spread", "side": "put",
            "expiration": "2026-09-11", "short_strike": short_strike,
            "long_strike": long_strike, "short_call": None, "long_call": None}
    base.update(kw)
    return base


class ContractIdentity(unittest.TestCase):
    def test_spreads_sharing_a_short_strike_are_different_contracts(self):
        a = _spread(357.5, 327.5)      # 30-wide
        b = _spread(357.5, 337.5)      # 20-wide
        c = _spread(357.5, 345.0)      # 12.5-wide
        ids = {E.contract_id(x) for x in (a, b, c)}
        self.assertEqual(len(ids), 3, "the wing is part of the trade's identity")

    def test_condors_sharing_a_put_wing_are_different_contracts(self):
        a = {"symbol": "AAPL", "strategy": "iron_condor", "side": "both",
             "expiration": "2026-09-11", "short_strike": 322.5, "long_strike": 312.5,
             "short_call": 332.5, "long_call": 342.5}
        b = dict(a, long_strike=305.0, long_call=350.0)
        self.assertNotEqual(E.contract_id(a), E.contract_id(b))

    def test_identity_is_stable_and_side_aware(self):
        a = _spread(357.5, 337.5)
        self.assertEqual(E.contract_id(a), E.contract_id(dict(a)))
        self.assertNotEqual(E.contract_id(a), E.contract_id(dict(a, side="call")))
        self.assertNotEqual(E.contract_id(a), E.contract_id(dict(a, expiration="2026-09-18")))
        self.assertNotEqual(E.contract_id(a), E.contract_id(dict(a, symbol="BE")))

    def test_missing_legs_do_not_collide_with_present_ones(self):
        single = _spread(357.5, None, strategy="cash_secured_put")
        self.assertNotEqual(E.contract_id(single), E.contract_id(_spread(357.5, 337.5)))

    def test_a_float_and_its_integer_are_one_strike(self):
        self.assertEqual(E.contract_id(_spread(210.0, 190.0)),
                         E.contract_id(_spread(210, 190)))

    def test_prediction_key_is_the_same_identity_plus_the_mode(self):
        a, b = _spread(357.5, 327.5), _spread(357.5, 337.5)
        self.assertNotEqual(ss.prediction_key(a, "balanced"), ss.prediction_key(b, "balanced"))
        self.assertNotEqual(ss.prediction_key(a, "balanced"), ss.prediction_key(a, "income"))


class BoardIdentity(unittest.TestCase):
    """The board's own rows, end to end, on a chain built to collide."""

    @classmethod
    def setUpClass(cls):
        import test_sell_scan as T
        cls.T = T
        cls.tmp = tempfile.mkdtemp(prefix="v481_")
        ss._STATE["symbols"].clear()
        ss._STATE["recorded"].clear()
        ss._EVIDENCE_MEM.clear()
        ss.configure(data_dir=cls.tmp, now_fn=lambda: T.TODAY)
        ss.on_chain("SYN", T._chain(T.SPOT, T.TODAY), T.BARS, T._ctx())

    def test_every_row_carries_a_unique_identity(self):
        for mode in ("conservative", "balanced", "income"):
            snap = ss.snapshot(mode, record=False)
            ids = [r["row_id"] for r in snap["rows"]]
            self.assertTrue(all(ids), f"{mode}: every row needs a row_id")
            self.assertEqual(len(ids), len(set(ids)), f"{mode}: row identities collide")

    def test_rows_sharing_a_short_strike_are_still_distinct_rows(self):
        snap = ss.snapshot("income", record=False)
        by_strike = {}
        for r in snap["rows"]:
            by_strike.setdefault((r["symbol"], r["expiration"], r["short_strike"]), []).append(r)
        shared = [v for v in by_strike.values() if len(v) > 1]
        for group in shared:
            self.assertEqual(len({r["row_id"] for r in group}), len(group))
            # and they really are different trades
            self.assertEqual(len({(r["long_strike"], r["short_call"], r["long_call"]) for r in group}),
                             len(group))

    def test_the_pathway_map_is_keyed_by_the_row_identity(self):
        snap = ss.snapshot("balanced", record=False)
        if not snap["rows"]:
            self.skipTest("no qualified rows in this fixture")
        pw = snap["risk_pathways"]
        self.assertTrue(pw)
        row_ids = {r["row_id"] for r in snap["rows"]}
        for k in pw:
            self.assertIn(k, row_ids, "a pathway key the UI cannot look up")

    def test_top_detail_carries_the_same_identity_as_the_rows(self):
        snap = ss.snapshot("balanced", record=False)
        if not snap["rows"]:
            self.skipTest("no qualified rows in this fixture")
        det = {c.get("row_id") for c in snap["top_detail"]}
        self.assertTrue(all(det))
        self.assertTrue(det <= {r["row_id"] for r in snap["rows"]})

    def test_sorting_by_any_column_never_changes_the_row_set(self):
        # the visible symptom: the count grew when a header was clicked.
        # The payload is what the client sorts, so its identity set must be
        # a set — sorting a list of unique keys cannot add a member.
        snap = ss.snapshot("balanced", record=False)
        ids = [r["row_id"] for r in snap["rows"]]
        for key in ("rank", "sell_quality", "p0_model", "credit", "symbol"):
            ordered = sorted(snap["rows"],
                             key=lambda r: (r.get(key) is None, r.get(key)))
            self.assertEqual(len(ordered), len(ids))
            self.assertEqual({r["row_id"] for r in ordered}, set(ids))


class Freshness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v481f_")
        ss._STATE["symbols"].clear()
        ss._STATE["recorded"].clear()
        ss._EVIDENCE_MEM.clear()
        import test_sell_scan as T
        self.T = T
        ss.configure(data_dir=self.tmp, now_fn=lambda: T.TODAY)
        ss.on_chain("SYN", T._chain(T.SPOT, T.TODAY), T.BARS, T._ctx())

    def _age_entry(self, days=0, hours=0):
        with ss._LOCK:
            e = ss._STATE["symbols"]["SYN"]
            e["as_of"] = (ss._now() - timedelta(days=days, hours=hours)).replace(
                microsecond=0).isoformat()

    def test_a_symbol_from_an_earlier_day_is_dropped_not_ranked(self):
        self._age_entry(days=1)
        snap = ss.snapshot("income", record=False)
        self.assertEqual(snap["rows"], [])
        self.assertEqual(snap["n_symbols"], 0)
        self.assertEqual(snap["stale_dropped"], 1)
        self.assertEqual(snap["stale_symbols"], ["SYN"])
        self.assertTrue(snap["no_trade"])
        self.assertIn("earlier session", snap["no_trade_reason"])

    def test_a_symbol_evaluated_today_is_kept(self):
        snap = ss.snapshot("income", record=False)
        self.assertEqual(snap["stale_dropped"], 0)
        self.assertEqual(snap["n_symbols"], 1)

    def test_an_intraday_board_past_the_age_limit_is_dropped(self):
        cfg, _ = E.config()
        limit = float(cfg["scan"]["max_board_age_hours"])
        self._age_entry(hours=limit + 1)
        # same calendar day only if the limit doesn't cross midnight; the
        # rule is an OR, so either way this must not be ranked
        self.assertEqual(ss.snapshot("income", record=False)["stale_dropped"], 1)

    def test_detail_refuses_a_stale_symbol_rather_than_answering_from_it(self):
        self._age_entry(days=1)
        d = ss.detail("SYN", "income")
        self.assertFalse(d["ok"])
        self.assertIn("earlier session", d["error"])

    def test_a_contract_expiring_today_is_not_offered(self):
        # the live board showed options expiring TODAY as "1 day" because
        # they were evaluated yesterday and never re-checked
        with ss._LOCK:
            e = ss._STATE["symbols"]["SYN"]
            for pm in e["modes"].values():
                for c in pm["qualified"]:
                    c["expiration"] = self.T.TODAY.isoformat()
        snap = ss.snapshot("income", record=False)
        self.assertEqual(snap["rows"], [])
        self.assertTrue(snap["no_trade"])

    def test_a_contract_that_already_expired_is_not_offered(self):
        with ss._LOCK:
            e = ss._STATE["symbols"]["SYN"]
            for pm in e["modes"].values():
                for c in pm["qualified"]:
                    c["expiration"] = (self.T.TODAY - timedelta(days=3)).isoformat()
        self.assertEqual(ss.snapshot("income", record=False)["rows"], [])


class Timestamps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v481t_")
        ss._STATE["symbols"].clear()
        ss._EVIDENCE_MEM.clear()

    def test_the_board_stamp_carries_a_timezone(self):
        ss.configure(data_dir=self.tmp, now_fn=None)
        stamp = ss._stamp()
        self.assertIsNotNone(datetime.fromisoformat(stamp).tzinfo,
                             "a naive stamp is read as the VIEWER's local time")

    def test_a_wired_market_clock_is_used_and_kept_aware(self):
        et = timezone(timedelta(hours=-4))
        fixed = datetime(2026, 9, 4, 6, 39, 53, tzinfo=et)
        ss.configure(data_dir=self.tmp, now_fn=lambda: fixed)
        self.assertEqual(ss._today(), date(2026, 9, 4))
        self.assertTrue(ss._stamp().startswith("2026-09-04T06:39:53"))
        self.assertIn("-04:00", ss._stamp())

    def test_the_age_of_a_fresh_stamp_is_never_negative(self):
        et = timezone(timedelta(hours=-4))
        ss.configure(data_dir=self.tmp, now_fn=lambda: datetime(2026, 9, 4, 6, 39, tzinfo=et))
        self.assertAlmostEqual(ss._age_hours(ss._stamp()), 0.0, places=2)
        # a UTC-stamped board read by an Eastern clock used to come out at
        # minus four hours; the offset makes the two comparable
        utc_stamp = datetime(2026, 9, 4, 10, 39, tzinfo=timezone.utc).isoformat()
        self.assertAlmostEqual(ss._age_hours(utc_stamp), 0.0, places=2)

    def test_a_naive_board_written_before_this_version_still_reads(self):
        ss.configure(data_dir=self.tmp, now_fn=None)
        naive = ss._now().replace(microsecond=0, tzinfo=None).isoformat()
        self.assertIsNotNone(ss._age_hours(naive))
        self.assertLess(abs(ss._age_hours(naive)), 0.01)


if __name__ == "__main__":
    unittest.main()
