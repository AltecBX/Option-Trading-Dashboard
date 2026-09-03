"""test_sp_forward.py — the forward-test grader and its calibration tables.

Invariants a seller would want proven before trusting a calibration page:
  * a put that never traded below the strike grades worthless + untouched;
  * a touch without a finish is a touch, not a loss;
  * a spread's modeled loss is capped at the width;
  * a condor grades on whichever wing broke;
  * nothing is graded before expiry or when the bars stop short of it;
  * grading is idempotent (a second pass grades nothing);
  * Brier / log loss / ECE are exact on known inputs; Wilson is the
    textbook interval; the Brier decomposition adds up;
  * slices under min_n are ACCRUING, never MEASURED;
  * the learning check reports, and is ACCRUING below its floor.
"""
import json
import math
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import sp_forward as fw


def _bars(start: date, closes, lows=None, highs=None):
    out = []
    d = start
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        lo = lows[i] if lows else c - 1
        hi = highs[i] if highs else c + 1
        out.append({"date": d.isoformat(), "open": c, "high": hi, "low": lo, "close": c, "volume": 1})
        d += timedelta(days=1)
    return out


def _rec(**kw):
    base = {"day": "2026-01-05", "key": "K", "symbol": "T", "mode": "balanced", "strategy": "cash_secured_put",
            "side": "put", "expiration": "2026-01-30", "dte": 25, "dte_bucket": "22-45", "delta": -0.15,
            "short_strike": 90.0, "long_strike": None, "spot": 100.0, "sigma_h": 0.30, "credit": 1.0,
            "net_credit": 0.9, "max_loss_per_share": 89.0, "p0_model": 0.9, "p0_conservative": 0.85,
            "p0_measured": 0.88, "p_touch": 0.2, "p_touch_measured": 0.18, "p_profit": 0.85}
    base.update(kw)
    return base


TODAY = date(2026, 3, 1)


class GradeRecord(unittest.TestCase):
    def test_put_never_reached_grades_worthless_untouched(self):
        bars = _bars(date(2026, 1, 6), [100] * 20)
        g = fw.grade_record(_rec(), bars, TODAY)
        self.assertEqual(g["expired_worthless"], 1)
        self.assertEqual(g["touched"], 0)
        self.assertAlmostEqual(g["pnl_per_share"], 0.9)
        self.assertEqual(g["profitable"], 1)
        self.assertEqual(g["basis"]["finish"], "MEASURED")
        self.assertIn("MODELED", g["basis"]["pnl"])
        self.assertIn("UNAVAILABLE", g["basis"]["early_profit_targets"])

    def test_touch_without_finish_is_a_touch_not_a_loss(self):
        lows = [99] * 20
        lows[7] = 88.0                       # one session dipped through 90
        bars = _bars(date(2026, 1, 6), [100] * 20, lows=lows)
        g = fw.grade_record(_rec(), bars, TODAY)
        self.assertEqual(g["touched"], 1)
        self.assertEqual(g["expired_worthless"], 1)
        self.assertGreater(g["max_excursion_sigma"], 0)

    def test_finish_through_strike_is_a_loss_bounded_by_width(self):
        closes = [100] * 18 + [70.0]      # 19 sessions: January 6 through January 30
        bars = _bars(date(2026, 1, 6), closes, lows=[c - 1 for c in closes])
        g = fw.grade_record(_rec(long_strike=85.0, width=5.0, max_loss_per_share=4.1), bars, TODAY)
        self.assertEqual(g["expired_worthless"], 0)
        # loss = (90-70) - (85-70) = 5 → pnl = 0.9 - 5
        self.assertAlmostEqual(g["pnl_per_share"], 0.9 - 5.0)
        self.assertEqual(g["profitable"], 0)
        self.assertAlmostEqual(g["loss_fraction_of_max"], round(5.0 / 4.1, 4))

    def test_call_side_uses_highs(self):
        highs = [101] * 20
        highs[3] = 115.0
        bars = _bars(date(2026, 1, 6), [100] * 20, highs=highs)
        g = fw.grade_record(_rec(side="call", short_strike=110.0, delta=0.15), bars, TODAY)
        self.assertEqual(g["touched"], 1)
        self.assertEqual(g["expired_worthless"], 1)

    def test_condor_grades_on_the_wing_that_broke(self):
        closes = [100] * 18 + [121.0]
        bars = _bars(date(2026, 1, 6), closes, highs=[c + 1 for c in closes])
        g = fw.grade_record(_rec(side="both", strategy="iron_condor", short_strike=90.0, long_strike=85.0,
                                 short_call=110.0, long_call=115.0, width=5.0, net_credit=1.5), bars, TODAY)
        self.assertEqual(g["expired_worthless"], 0)
        self.assertAlmostEqual(g["pnl_per_share"], 1.5 - 5.0)

    def test_not_graded_before_expiry(self):
        bars = _bars(date(2026, 1, 6), [100] * 20)
        self.assertIsNone(fw.grade_record(_rec(), bars, date(2026, 1, 20)))

    def test_not_graded_when_bars_stop_short(self):
        bars = _bars(date(2026, 1, 6), [100] * 5)      # ends Jan 12, expiry Jan 30
        self.assertIsNone(fw.grade_record(_rec(), bars, TODAY))

    def test_entry_bar_excluded_from_window(self):
        # a low on the entry day itself must not count as a touch
        bars = _bars(date(2026, 1, 5), [100] * 21, lows=[50.0] + [99] * 20)
        g = fw.grade_record(_rec(), bars, TODAY)
        self.assertEqual(g["touched"], 0)


class Stats(unittest.TestCase):
    def test_brier_and_log_loss_exact(self):
        pairs = [(0.9, 1), (0.1, 0)]
        self.assertAlmostEqual(fw.brier(pairs), 0.01)
        self.assertAlmostEqual(fw.log_loss(pairs), -math.log(0.9))
        self.assertIsNone(fw.brier([]))

    def test_wilson_matches_textbook(self):
        lo, hi = fw.wilson(8, 10)
        self.assertAlmostEqual(lo, 0.4902, places=3)
        self.assertAlmostEqual(hi, 0.9433, places=3)
        self.assertEqual(fw.wilson(0, 0), (0.0, 1.0))

    def test_reliability_and_ece(self):
        pairs = [(0.92, 1)] * 9 + [(0.92, 0)] + [(0.6, 1)] * 5 + [(0.6, 0)] * 5
        b = fw.reliability(pairs)
        self.assertEqual(len(b), 2)
        hi = [x for x in b if x["bucket"].startswith("90%")][0]
        self.assertEqual(hi["n"], 10)
        self.assertAlmostEqual(hi["observed"], 0.9)
        self.assertTrue(hi["inside_ci"])
        self.assertAlmostEqual(fw.ece(b), (10 * 0.02 + 10 * 0.1) / 20, places=6)

    def test_decomposition_adds_up(self):
        pairs = [(0.92, 1)] * 9 + [(0.92, 0)] + [(0.6, 1)] * 5 + [(0.6, 0)] * 5
        b = fw.reliability(pairs)
        d = fw.brier_decomposition(pairs, b)
        # Murphy: Brier = REL − RES + UNC holds exactly when buckets are pure
        self.assertAlmostEqual(d["reliability"] - d["resolution"] + d["uncertainty"], fw.brier(pairs), places=4)

    def test_platt_recovers_identity_on_calibrated_data(self):
        import random
        rng = random.Random(3)
        pairs = []
        for _ in range(2000):
            p = rng.choice([0.7, 0.8, 0.9, 0.95])
            pairs.append((p, 1 if rng.random() < p else 0))
        a, b = fw.platt_fit(pairs)
        self.assertAlmostEqual(a, 1.0, delta=0.25)
        self.assertAlmostEqual(b, 0.0, delta=0.25)

    def test_learning_check_accrues_below_floor(self):
        out = fw.learning_check([(0.9, 1)] * 20)
        self.assertEqual(out["status"], "ACCRUING")
        self.assertFalse(out["recommended"])

    def test_learning_check_recommends_only_when_oos_better(self):
        import random
        rng = random.Random(5)
        # over-confident claims: says 0.95, delivers 0.75 → adjustment should help OOS
        pairs = [(0.95, 1 if rng.random() < 0.75 else 0) for _ in range(400)]
        out = fw.learning_check(pairs)
        self.assertEqual(out["status"], "MEASURED")
        self.assertTrue(out["recommended"])
        self.assertLess(out["brier_adjusted_oos"], out["brier_raw_oos"])
        # calibrated claims → nothing to learn
        pairs2 = [(0.8, 1 if rng.random() < 0.8 else 0) for _ in range(400)]
        self.assertFalse(fw.learning_check(pairs2)["recommended"])


class Tables(unittest.TestCase):
    def _grades(self, n, side="put"):
        return [{"claims": {"p0_model": 0.9, "p_touch": 0.2}, "expired_worthless": 1 if i % 10 else 0,
                 "touched": 1 if i % 5 == 0 else 0, "side": side, "dte_bucket": "22-45", "delta": -0.15,
                 "mode": "balanced", "strategy": "cash_secured_put", "pnl_per_share": 0.5,
                 "profitable": 1, "max_excursion_sigma": 0.4} for i in range(n)]

    def test_status_labels_by_count(self):
        self.assertEqual(fw.build_calibration([])["status"], "UNAVAILABLE")
        self.assertEqual(fw.build_calibration(self._grades(10))["status"], "ACCRUING")
        c = fw.build_calibration(self._grades(40))
        self.assertEqual(c["status"], "MEASURED")
        self.assertEqual(c["fields"]["p0_model"]["overall"]["status"], "MEASURED")
        self.assertAlmostEqual(c["fields"]["p0_model"]["overall"]["observed_rate"], 0.9)
        # a slice with too few rows is ACCRUING even when the whole is MEASURED
        c2 = fw.build_calibration(self._grades(40) + self._grades(5, side="call"))
        self.assertEqual(c2["fields"]["p0_model"]["slices"]["side"]["call"]["status"], "ACCRUING")
        self.assertEqual(c2["fields"]["p0_model"]["slices"]["side"]["put"]["status"], "MEASURED")
        self.assertEqual(c2["labels"]["early_profit_targets"], "UNAVAILABLE")

    def test_fields_without_claims_are_unavailable(self):
        c = fw.build_calibration(self._grades(40))
        self.assertEqual(c["fields"]["p_profit"]["overall"]["status"], "UNAVAILABLE")


class EndToEnd(unittest.TestCase):
    def test_grade_pending_is_idempotent_and_persists(self):
        tmp = tempfile.mkdtemp(prefix="spf_")
        import sell_scan as ss
        ss.configure(data_dir=tmp)
        Path(tmp, "sell", "predictions").mkdir(parents=True, exist_ok=True)
        rec = _rec(day="2026-01-05", expiration="2026-01-30")
        Path(tmp, "sell", "predictions", "2026-01-05.jsonl").write_text(json.dumps(rec) + "\n")
        bars = _bars(date(2026, 1, 6), [100] * 40)
        fw._STATE["graded_keys"] = None
        fw.configure(data_dir=tmp, bars_fn=lambda s: bars)
        # predictions() looks back 400 days from today; keep the record inside that window
        if (date.today() - date(2026, 1, 5)).days > 400:
            self.skipTest("fixture date outside the 400-day prediction window")
        out = fw.grade_pending()
        self.assertEqual(out["graded_now"], 1)
        self.assertEqual(fw.grade_pending()["graded_now"], 0)
        self.assertEqual(len(fw.load_grades()), 1)
        cal = fw.calibration(refresh=True)
        self.assertEqual(cal["n_graded"], 1)
        self.assertTrue(Path(tmp, "sell", "calibration.json").exists())
        ss._DATA_DIR = None


if __name__ == "__main__":
    unittest.main()
