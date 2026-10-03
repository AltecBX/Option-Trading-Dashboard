"""Tests for when_to_sell.py: the ticker page's "When to sell" card."""

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import test_trigger_sell as T
import trigger_sell as ts
import when_to_sell as wts


class Days(unittest.TestCase):
    def test_a_weekend_decides_for_the_coming_monday(self):
        self.assertEqual(date(2026, 10, 5), wts.decision_day(date(2026, 10, 3)))   # Saturday
        self.assertEqual(date(2026, 10, 5), wts.decision_day(date(2026, 10, 4)))   # Sunday
        self.assertEqual(date(2026, 10, 7), wts.decision_day(date(2026, 10, 7)))   # Wednesday

    def test_the_expiry_is_this_weeks_last_session(self):
        self.assertEqual(date(2026, 10, 9), wts.week_expiry(date(2026, 10, 6)))
        self.assertEqual(date(2026, 10, 9), wts.week_expiry(date(2026, 10, 9)), "Friday is its own week")
        # Good Friday 2025-04-18: the weekly expires Thursday
        self.assertEqual(date(2025, 4, 17), wts.week_expiry(date(2025, 4, 15)))


class ImpliedVol(unittest.TestCase):
    def test_the_strikes_nearest_the_money(self):
        calls = [{"strike": 95, "iv": 0.9}, {"strike": 100, "iv": 0.40}, {"strike": 105, "iv": 0.2}]
        puts = [{"strike": 100, "iv": 50.0}, {"strike": 110, "iv": 0.1}]     # percent form is read too
        self.assertAlmostEqual(0.45, wts.atm_iv(calls, puts, 101.0))

    def test_nothing_usable(self):
        self.assertIsNone(wts.atm_iv([], [], 100.0))
        self.assertIsNone(wts.atm_iv([{"strike": 100, "iv": 0}], [], 100.0))


class History(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def rec(self, week, theta=True, q=5):
        return {"week": week, "ticker": "LITE", "quintile": q, "trigger_pct": 0.14,
                "theta_dominant": theta, "theta_decay": -1.0, "uplift": 0.5}

    def test_one_record_per_week_and_this_week_is_left_out(self):
        wts.save_record(self.dir, self.rec("2026-09-21"))
        wts.save_record(self.dir, self.rec("2026-09-28", theta=False))
        wts.save_record(self.dir, self.rec("2026-09-28", theta=True))     # a later look the same week
        recs = wts.load_history(self.dir)["LITE"]
        self.assertEqual(["2026-09-21", "2026-09-28"], [r["week"] for r in recs])
        self.assertTrue(recs[-1]["theta_dominant"], "the last look of the week is the one kept")
        prior = wts.prior_records(self.dir, "lite", "2026-09-28")
        self.assertEqual(["2026-09-21"], [r["week"] for r in prior],
                         "loading the page twice in one week is not two weeks running")

    def test_only_recent_weeks_are_kept(self):
        mon = date(2026, 1, 5)
        for i in range(wts.HISTORY_KEEP + 5):
            wts.save_record(self.dir, self.rec((mon + timedelta(weeks=i)).isoformat()))
        self.assertEqual(wts.HISTORY_KEEP, len(wts.load_history(self.dir)["LITE"]))

    def test_no_dir_or_bad_file_is_harmless(self):
        wts.save_record(None, self.rec("2026-09-28"))
        self.assertEqual([], wts.prior_records(None, "LITE", "2026-09-28"))
        p = Path(self.dir) / "trigger_sell" / "history.json"
        p.parent.mkdir(parents=True)
        p.write_text("{not json")
        self.assertEqual({}, wts.load_history(self.dir))


class Table(unittest.TestCase):
    def test_the_committed_table_loads_and_answers(self):
        tbl = wts.load_table()
        self.assertIsNotNone(tbl, "data/conditional_triggers.json is missing")
        self.assertAlmostEqual(0.1454, ts.regime_trigger("LITE", 15.0, tbl), places=4)

    def test_a_missing_file_is_no_table(self):
        self.assertIsNone(wts.load_table(Path(tempfile.mkdtemp()) / "nope.json"))

    def test_a_changed_file_is_re_read(self):
        p = Path(tempfile.mkdtemp()) / "t.json"
        p.write_text(json.dumps({"as_of": "a"}))
        self.assertEqual("a", wts.load_table(p)["as_of"])
        p.write_text(json.dumps({"as_of": "bb"}))
        import os
        st = p.stat()
        os.utime(p, (st.st_atime, st.st_mtime + 5))
        self.assertEqual("bb", wts.load_table(p)["as_of"])


class Card(unittest.TestCase):
    def setUp(self):
        self.bars = T.regime_tape()
        self.mon = T.next_monday(38)
        self.spot = self.bars[-1]["close"]
        self.chain = [{"strike": round(self.spot) + k, "iv": 0.45} for k in (-5, 0, 5)]
        self.dir = tempfile.mkdtemp()

    def build(self, day, **kw):
        kw.setdefault("table", T.TABLE)
        return wts.build("SYN", spot=self.spot, bars=self.bars, calls=self.chain, puts=self.chain,
                         today=day, data_dir=self.dir, **kw)

    def test_a_tuesday_after_an_up_week_waits_for_the_regime_trigger(self):
        c = self.build(self.mon + timedelta(days=1))
        self.assertTrue(c["ok"], c.get("reason"))
        self.assertEqual("wait", c["action"])
        self.assertEqual("Wait for the trigger", c["headline"])
        self.assertAlmostEqual(0.04, c["trigger_pct"])
        self.assertAlmostEqual(round(self.spot * 1.04, 2), c["trigger_price"], places=2)
        self.assertTrue(c["regime"]["on"])
        self.assertEqual(5, c["regime"]["quintile"])
        self.assertAlmostEqual(11.0, c["regime"]["prior_week_pct"], places=2)
        self.assertAlmostEqual(0.09, c["regime"]["unconditional"])
        self.assertEqual("chain", c["iv_source"])
        self.assertFalse(c["weekend"])
        self.assertEqual((self.mon + timedelta(days=4)).isoformat(), c["expiry"])
        # the week's record is kept for next week's theta check
        self.assertEqual(self.mon.isoformat(), wts.load_history(self.dir)["SYN"][-1]["week"])

    def test_the_weekend_shows_mondays_plan_and_keeps_no_record(self):
        c = self.build(self.mon - timedelta(days=1))                 # the Sunday before
        self.assertTrue(c["weekend"])
        self.assertEqual(self.mon.isoformat(), c["decision_day"])
        self.assertEqual(5, c["sessions_now"])
        self.assertNotEqual("sell_now", c["action"], "Monday never says sell now")
        self.assertEqual({}, wts.load_history(self.dir))

    def test_no_table_uses_the_stocks_own_percentile(self):
        c = self.build(self.mon + timedelta(days=1), table=None)
        self.assertFalse(c["regime"]["on"])
        self.assertEqual("percentile", c["regime"]["source"])
        self.assertFalse(c["table"])

    def test_no_chain_falls_back_to_realized_volatility(self):
        c = wts.build("SYN", spot=self.spot, bars=self.bars, today=self.mon + timedelta(days=1),
                      data_dir=self.dir, hv=0.5, table=T.TABLE)
        self.assertEqual("realized", c["iv_source"])
        self.assertAlmostEqual(0.5, c["iv"])

    def test_it_never_raises(self):
        c = wts.build("SYN", spot=None, bars=None, today=self.mon, data_dir=self.dir)
        self.assertFalse(c["ok"])
        self.assertTrue(c["reason"])
        c = wts.build("SYN", spot=self.spot, bars=self.bars, today="not a date", data_dir=self.dir)
        self.assertFalse(c["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
