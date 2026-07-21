"""Tests for earnings_scan.py — every calculation and classification is a
pure function on plain dicts, so no network is touched here."""
import unittest

import earnings_scan as es


class TestImpliedMove(unittest.TestCase):
    def test_straddle_math(self):
        em = es.implied_move(100.0, 3.2, 2.8)
        self.assertEqual(em["dollars"], 6.0)
        self.assertEqual(em["pct"], 6.0)
        self.assertEqual(em["upper"], 106.0)
        self.assertEqual(em["lower"], 94.0)

    def test_missing_inputs(self):
        self.assertIsNone(es.implied_move(None, 3, 3))
        self.assertIsNone(es.implied_move(100, None, 3))
        self.assertIsNone(es.implied_move(100, 0, 3))


class TestHistStats(unittest.TestCase):
    def test_stats(self):
        h = es.hist_earnings_stats([4.0, -6.0, 2.0, 8.0, -1.0])
        self.assertEqual(h["n"], 5)
        self.assertAlmostEqual(h["avg_abs"], 4.2)
        self.assertEqual(h["med_abs"], 4.0)
        self.assertEqual(h["last"], -1.0)
        self.assertEqual(h["beat_dir_pct"], 60)

    def test_insufficient_and_outliers(self):
        self.assertIsNone(es.hist_earnings_stats([1.0, 2.0]))
        # 3 clean moves after an absurd outlier is dropped
        h = es.hist_earnings_stats([120.0, 3.0, -2.0, 4.0])
        self.assertEqual(h["n"], 3)


class TestImpliedVsHist(unittest.TestCase):
    def test_rich_cheap_fair(self):
        hist = {"avg_abs": 4.0}
        self.assertEqual(es.implied_vs_hist(6.0, hist)["label"], "rich")
        self.assertEqual(es.implied_vs_hist(2.5, hist)["label"], "cheap")
        self.assertEqual(es.implied_vs_hist(4.2, hist)["label"], "fair")
        self.assertIsNone(es.implied_vs_hist(None, hist))
        self.assertIsNone(es.implied_vs_hist(5.0, None))


class TestActualVsExpected(unittest.TestCase):
    def test_prefers_recorded_implied(self):
        r = es.actual_vs_expected(8.0, 5.0, 3.0)
        self.assertEqual(r["expected"], 5.0)
        self.assertIn("implied", r["basis"])
        self.assertEqual(r["label"], "exceeded")

    def test_falls_back_to_hist(self):
        r = es.actual_vs_expected(1.0, None, 4.0)
        self.assertIn("historical", r["basis"])
        self.assertEqual(r["label"], "undershot")

    def test_in_line(self):
        self.assertEqual(es.actual_vs_expected(4.0, 4.0, None)["label"], "in line")

    def test_no_basis(self):
        self.assertIsNone(es.actual_vs_expected(4.0, None, None))
        self.assertIsNone(es.actual_vs_expected(None, 5.0, 4.0))


class TestGap(unittest.TestCase):
    def test_gap_up_filled(self):
        g = es.gap_info(105.0, 100.0, 99.5, 106.0)
        self.assertEqual(g["gap_pct"], 5.0)
        self.assertEqual(g["fill_level"], 100.0)
        self.assertTrue(g["filled"])

    def test_gap_up_unfilled(self):
        g = es.gap_info(105.0, 100.0, 103.0, 108.0)
        self.assertFalse(g["filled"])

    def test_no_gap_no_fill_flag(self):
        g = es.gap_info(100.1, 100.0, 99.0, 101.0)
        self.assertIsNone(g["filled"])


class TestVwapStatus(unittest.TestCase):
    def test_reclaim(self):
        v = es.vwap_status(101.0, 100.0, opened_above=False)
        self.assertTrue(v["above"])
        self.assertEqual(v["event"], "reclaim")

    def test_rejection(self):
        v = es.vwap_status(99.0, 100.0, opened_above=True)
        self.assertFalse(v["above"])
        self.assertEqual(v["event"], "rejection")

    def test_no_event_without_open_side(self):
        self.assertIsNone(es.vwap_status(101.0, 100.0)["event"])
        self.assertIsNone(es.vwap_status(None, 100.0))


class TestSpread(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(es.spread_quality(1.00, 1.04)["label"], "good")
        self.assertEqual(es.spread_quality(1.00, 1.10)["label"], "ok")
        self.assertEqual(es.spread_quality(1.00, 1.30)["label"], "poor")
        self.assertIsNone(es.spread_quality(0, 1.0))


def _base_row(**kw):
    row = {
        "ticker": "TST", "price": 100.0, "change_pct": 0.5, "days_to": 2,
        "reported_recently": False, "market_cap": 20e9, "avg_volume": 5e6,
        "rel_volume": 1.2, "options_volume": 20000, "open_interest": 60000,
        "weekly_options": True, "spread": {"pct": 3.0, "label": "good"},
        "implied": {"pct": 6.0, "dollars": 6.0, "upper": 106.0, "lower": 94.0},
        "hist": {"n": 8, "avg_abs": 4.0, "med_abs": 3.5, "last": 2.0,
                 "beat_dir_pct": 50, "moves": [2, -3, 4]},
        "iv_vs_hist": {"ratio": 1.5, "label": "rich"},
        "actual_vs_expected": None, "vwap": None, "gap": None,
        "mom5_pct": 0.0,
    }
    row.update(kw)
    return row


class TestClassify(unittest.TestCase):
    def test_high_premium_pre_earnings(self):
        cls = es.classify(_base_row())
        self.assertIn(cls["setup"], ("high_premium", "put_selling", "covered_call"))
        self.assertEqual(cls["action"], "sell_premium")

    def test_put_selling_flavor_from_beat_history(self):
        r = _base_row()
        r["hist"]["beat_dir_pct"] = 70
        self.assertEqual(es.classify(r)["setup"], "put_selling")

    def test_cheap_implied(self):
        cls = es.classify(_base_row(iv_vs_hist={"ratio": 0.6, "label": "cheap"}))
        self.assertEqual(cls["setup"], "cheap_implied")

    def test_confirmed_long_post_reclaim(self):
        r = _base_row(days_to=0, reported_recently=True, change_pct=5.0,
                      actual_vs_expected={"ratio": 1.4, "label": "exceeded",
                                          "basis": "implied (recorded pre-print)",
                                          "actual": 5.0, "expected": 3.5},
                      vwap={"above": True, "dist_pct": 1.0, "event": "reclaim", "vwap": 99.0},
                      gap={"gap_pct": 1.0, "fill_level": 95.0, "filled": False},
                      rel_volume=2.5)
        cls = es.classify(r)
        self.assertEqual(cls["setup"], "vwap_reclaim")
        self.assertEqual(cls["status"], "confirmed_long")
        self.assertEqual(cls["action"], "confirmed_entry")

    def test_late_entry_protection(self):
        # +5% day but stretched 5% above VWAP and beyond the EM upper band
        r = _base_row(days_to=0, reported_recently=True, change_pct=9.0, price=109.0,
                      vwap={"above": True, "dist_pct": 5.0, "event": None, "vwap": 103.8},
                      gap={"gap_pct": 6.0, "fill_level": 94.0, "filled": False},
                      actual_vs_expected={"ratio": 1.5, "label": "exceeded",
                                          "basis": "implied (recorded pre-print)",
                                          "actual": 9.0, "expected": 6.0},
                      rel_volume=3.0)
        cls = es.classify(r)
        self.assertTrue(cls["extended"])
        self.assertEqual(cls["status"], "late_extended")
        self.assertEqual(cls["action"], "already_extended")

    def test_no_trade_on_dead_volume_post(self):
        r = _base_row(days_to=0, reported_recently=True, rel_volume=0.3,
                      vwap={"above": True, "dist_pct": 0.2, "event": None, "vwap": 99.8})
        cls = es.classify(r)
        self.assertEqual(cls["status"], "no_trade")

    def test_no_trade_far_out(self):
        cls = es.classify(_base_row(days_to=30, iv_vs_hist=None))
        self.assertEqual(cls["setup"], "no_trade")


class TestPlanAndScore(unittest.TestCase):
    def _confirmed(self):
        return _base_row(days_to=0, reported_recently=True, change_pct=5.0,
                         vwap={"above": True, "dist_pct": 1.0, "event": "reclaim", "vwap": 99.0},
                         or_high=101.0, or_low=98.5, prev_high=97.0, prev_low=95.0,
                         actual_vs_expected={"ratio": 1.3, "label": "exceeded",
                                             "basis": "implied (recorded pre-print)",
                                             "actual": 5.0, "expected": 3.8},
                         rel_volume=2.2)

    def test_plan_levels(self):
        r = self._confirmed()
        cls = es.classify(r)
        plan = es.build_plan(r, cls)
        self.assertEqual(plan["bias"], "long")
        self.assertLess(plan["invalidation"], plan["entry"])
        self.assertGreater(plan["target1"], plan["entry"])
        self.assertGreater(plan["max_chase"], plan["entry"])
        self.assertLess(plan["max_chase"], plan["target1"])
        self.assertIsNotNone(plan["rr"])

    def test_no_plan_for_no_trade(self):
        r = _base_row(days_to=30, iv_vs_hist=None)
        cls = es.classify(r)
        self.assertIsNone(es.build_plan(r, cls))

    def test_score_bounds_and_explanations(self):
        r = self._confirmed()
        cls = es.classify(r)
        plan = es.build_plan(r, cls)
        sd = es.score_row(r, cls, plan, spy_chg=0.5)
        self.assertGreaterEqual(sd["score"], 0)
        self.assertLessEqual(sd["score"], 100)
        self.assertTrue(1 <= len(sd["reasons"]) <= 3)
        self.assertIn("components", sd)

    def test_confirmed_beats_unconfirmed(self):
        r1 = self._confirmed()
        c1 = es.classify(r1)
        s1 = es.score_row(r1, c1, es.build_plan(r1, c1), 0.5)["score"]
        r2 = _base_row(days_to=5, options_volume=100, open_interest=100,
                       spread={"pct": 20.0, "label": "poor"}, weekly_options=False,
                       iv_vs_hist=None, rel_volume=0.5)
        c2 = es.classify(r2)
        s2 = es.score_row(r2, c2, es.build_plan(r2, c2), 0.5)["score"]
        self.assertGreater(s1, s2)

    def test_no_trade_score_capped(self):
        r = _base_row(days_to=30, iv_vs_hist=None)
        cls = es.classify(r)
        sd = es.score_row(r, cls, None, None)
        self.assertLessEqual(sd["score"], 25)


class TestAlertsAndBuckets(unittest.TestCase):
    def test_alert_strings(self):
        r = _base_row(days_to=0)
        cls = es.classify(r)
        alerts = es.row_alerts(r, cls)
        self.assertIn("earnings today", alerts)
        self.assertIn("high premium detected", alerts)

    def test_bucket_routing(self):
        self.assertEqual(es.bucket({"status": "no_trade", "setup": "no_trade"}), "no_trade")
        self.assertEqual(es.bucket({"status": "late_extended", "setup": "gap_and_go"}), "extended")
        self.assertEqual(es.bucket({"status": "confirmation_pending", "setup": "gap_fill"}), "waiting")
        self.assertEqual(es.bucket({"status": "potential", "setup": "high_premium"}), "premium")
        self.assertEqual(es.bucket({"status": "confirmed_long", "setup": "vwap_reclaim",
                                    "reported_recently": True}), "post")
        self.assertEqual(es.bucket({"status": "watching", "setup": "pre_earnings_momentum",
                                    "reported_recently": False, "days_to": 0}), "today")
        self.assertEqual(es.bucket({"status": "watching", "setup": "no_trade",
                                    "reported_recently": False, "days_to": 3}), "pre")


class TestProviderFallback(unittest.TestCase):
    def test_demo_rows_labeled(self):
        rows = es._demo_board_rows()
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(r["demo"])
            self.assertTrue(r["ticker"].startswith("DEMO"))
            self.assertIn("bucket", r)

    def test_timing_classification(self):
        class TS:
            def __init__(self, h, m=0):
                self.hour, self.minute = h, m
        self.assertEqual(es._earnings_timing(TS(6)), "BMO")
        self.assertEqual(es._earnings_timing(TS(16, 5)), "AMC")
        self.assertEqual(es._earnings_timing(TS(12)), "unknown")


if __name__ == "__main__":
    unittest.main()
