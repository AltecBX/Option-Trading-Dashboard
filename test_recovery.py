"""Tests for the Prior High Recovery scanner (recovery.py + recovery_fit.py).

Everything runs on synthetic, deterministic bars — no network (JERRY_NO_NET
convention).  The centrepiece is TestNoLookahead: a historical evaluation
must be IDENTICAL whether future candles exist in the dataset or not, which
is proved by comparing the fast replay path (iter_days over the full
series) against detect_setup on the truncated slice, bar by bar.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import recovery as rec


def mk_bar(date, o, h, lo, c, v=1_000_000):
    return {"date": date, "open": o, "high": h, "low": lo, "close": c,
            "volume": v}


def synth_dates(n):
    """n synthetic trading dates (weekday-agnostic; format only matters)."""
    from datetime import date, timedelta
    d0 = date(2024, 1, 1)
    out = []
    d = d0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def ramp_bars(closes, vol=1_000_000):
    dates = synth_dates(len(closes))
    return [mk_bar(dates[i], c * 0.995, c * 1.005, c * 0.99, c, vol)
            for i, c in enumerate(closes)]


def textbook_recovery():
    """The spec's example, embedded in enough history to qualify:
    ramp to a 100 high → correction to 74 → bounce to 84 → higher low 78 →
    breakout day closing at 85."""
    closes = []
    closes += [50 + 50 * i / 149 for i in range(150)]           # ramp to 100
    closes += [100 - 26 * (i + 1) / 40 for i in range(40)]      # down to 74
    closes += [74 + 10 * (i + 1) / 10 for i in range(10)]       # bounce to 84
    closes += [84 - 6 * (i + 1) / 6 for i in range(6)]          # dip to 78
    closes += [78 + 7 * (i + 1) / 8 for i in range(8)]          # rally to 85ish
    bars = ramp_bars(closes)
    # pin the exact pivots so assertions are crisp (and clamp the neighbours
    # whose auto-generated ranges would otherwise poke past the pivot)
    bars[149]["high"] = 100.0                                    # prior high
    bars[148]["high"] = 99.8
    bars[189]["low"] = 74.0                                      # correction low
    bars[188]["low"] = 74.2
    bars[199]["high"] = 84.0                                     # bounce high
    bars[205]["low"] = 78.0                                      # higher low
    bars[-1]["close"] = 85.0
    bars[-1]["high"] = 85.3
    return bars


class Base(unittest.TestCase):
    def setUp(self):
        os.environ["JERRY_NO_NET"] = "1"
        self._tmp = tempfile.TemporaryDirectory()
        rec.configure(data_dir=self._tmp.name)
        with rec._LOCK:
            rec._STATE.update({"scanning": False, "scanned": 0, "total": 0,
                               "last_scan": None, "rows": [], "error": None,
                               "universe_size": 0, "spy_regime": None,
                               "sector_trend": {}})
        rec._DETAIL_CACHE.clear()
        # tests control the model explicitly — never the repo artifact
        rec._MODEL = None
        rec._MODEL_TRIED = True

    def tearDown(self):
        if rec._THREAD is not None:
            rec._THREAD.join(timeout=10)
        rec._MODEL = None
        rec._MODEL_TRIED = False
        rec.configure(data_dir=None)
        self._tmp.cleanup()
        os.environ["JERRY_NO_NET"] = "1"


FAKE_MODEL = {
    "feature_names": ["recovery_ratio", "dist_to_high"],
    "weights": {"recovery_ratio": 1.0, "dist_to_high": -1.0},
    "intercept": 0.0,
    "feature_mean": {"recovery_ratio": 0.4, "dist_to_high": 0.15},
    "feature_std": {"recovery_ratio": 0.2, "dist_to_high": 0.1},
    "p_floor": 0.10, "p_ceil": 0.60,
    "meta": {"horizon": 60},
    "deciles": [
        {"decile": 1, "lo": 0.0, "hi": 0.5, "n": 500, "p_win": 0.10,
         "p_exceed": 0.05, "hit5": 0.01, "hit10": 0.02, "hit20": 0.05,
         "hit30": 0.08, "median_days": 30, "avg_days": 28.0,
         "median_mfe": 5.0, "median_mae": -6.0},
        {"decile": 10, "lo": 0.5, "hi": 1.0, "n": 800, "p_win": 0.60,
         "p_exceed": 0.30, "hit5": 0.15, "hit10": 0.3, "hit20": 0.45,
         "hit30": 0.55, "median_days": 9, "avg_days": 12.0,
         "median_mfe": 7.0, "median_mae": -4.0},
    ],
}


class TestIndicators(unittest.TestCase):
    def test_ema_matches_hand_calc(self):
        closes = [1, 2, 3, 4, 5, 6.0]
        e = rec._ema_series(closes, 3)
        self.assertIsNone(e[1])
        self.assertAlmostEqual(e[2], 2.0)             # seed = mean(1,2,3)
        self.assertAlmostEqual(e[3], 2.0 + (4 - 2.0) * 0.5)
        # entry i must not depend on later closes (causality)
        e_cut = rec._ema_series(closes[:4], 3)
        self.assertAlmostEqual(e[3], e_cut[3])

    def test_rsi_bounds_and_direction(self):
        up = rec._rsi14([float(i) for i in range(40)])
        self.assertAlmostEqual(up[-1], 100.0)
        down = rec._rsi14([40.0 - i for i in range(40)])
        self.assertLess(down[-1], 1.0)

    def test_atr_positive(self):
        bars = ramp_bars([10 + 0.1 * i for i in range(30)])
        atr = rec._atr14(bars)
        self.assertIsNone(atr[13])
        self.assertGreater(atr[-1], 0)

    def test_zigzag_finds_pivots(self):
        highs = [10, 11, 12, 11, 10, 9, 10, 11, 12, 13]
        lows = [x - 0.5 for x in highs]
        piv = rec._zigzag(highs, lows, 0.10)
        kinds = [k for _, _, k in piv]
        self.assertEqual(kinds[0], "low")
        self.assertIn("high", kinds)


class TestStructure(Base):
    def test_textbook_example(self):
        s = rec.detect_setup(textbook_recovery())
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["prior_high"], 100.0)
        self.assertAlmostEqual(s["corr_low"], 74.0)
        self.assertAlmostEqual(s["depth"], 0.26, places=3)
        self.assertAlmostEqual(s["bounce_high"], 84.0)
        self.assertAlmostEqual(s["higher_low"], 78.0)
        self.assertTrue(s["has_higher_low"])
        self.assertTrue(s["broke_bounce"])
        self.assertEqual(s["stage"], "confirmed")
        # Recovery Ratio = (85 − 74) / (100 − 74)
        self.assertAlmostEqual(s["recovery_ratio"], (85 - 74) / 26, places=3)
        self.assertAlmostEqual(s["dist_to_high"], (100 - 85) / 100, places=3)
        # invalidation hangs off the higher low
        self.assertEqual(s["inval_basis"], "higher low")
        self.assertAlmostEqual(s["invalidation"], 78 * 0.995, places=3)
        # reward:risk = upside/risk
        up = (100 - 85) / 85
        rk = (85 - 78 * 0.995) / 85
        self.assertAlmostEqual(s["reward_risk"], round(up / rk, 2), places=2)

    def test_no_setup_without_meaningful_correction(self):
        closes = [50 + 50 * i / 149 for i in range(150)]
        closes += [100 - 4 * (i + 1) / 30 for i in range(30)]   # only −4%
        self.assertIsNone(rec.detect_setup(ramp_bars(closes)))

    def test_no_setup_on_short_history(self):
        self.assertIsNone(rec.detect_setup(ramp_bars([10.0] * 50)))

    def test_fresh_spike_is_not_its_own_prior_high(self):
        # flat tape, then a spike 3 days ago: the spike must not qualify —
        # the high window excludes the most recent min_days_since_high bars.
        closes = [50.0] * 150 + [50, 90, 88, 87]
        s = rec.detect_setup(ramp_bars(closes))
        self.assertIsNone(s)

    def test_collapse_beyond_max_depth_rejected(self):
        closes = [50 + 50 * i / 149 for i in range(150)]
        closes += [100 - 70 * (i + 1) / 40 for i in range(40)]  # −70% crash
        self.assertIsNone(rec.detect_setup(ramp_bars(closes)))

    def test_breakout_stage_when_above_prior_high(self):
        bars = textbook_recovery()
        bars[-1]["close"] = 101.5
        bars[-1]["high"] = 101.9
        s = rec.detect_setup(bars)
        self.assertEqual(s["stage"], "breakout")

    def test_failed_stage_when_higher_low_breaks(self):
        bars = textbook_recovery()
        bars[-1]["close"] = 76.0
        bars[-1]["high"] = 77.0
        bars[-1]["low"] = 75.5
        s = rec.detect_setup(bars)
        self.assertEqual(s["stage"], "failed")

    def test_approaching_stage(self):
        bars = textbook_recovery()
        bars[-1]["close"] = 94.0          # RR = 20/26 ≈ 0.77
        bars[-1]["high"] = 94.5
        s = rec.detect_setup(bars)
        self.assertEqual(s["stage"], "approaching")


class TestNoLookahead(Base):
    def _bars(self):
        # seeded pseudo-random walk with a real drawdown-recovery shape
        import random
        rng = random.Random(1234)
        closes = [100.0]
        for i in range(700):
            drift = 0.0006 if (i // 120) % 2 == 0 else -0.0012
            closes.append(max(5.0, closes[-1] * (1 + drift + rng.gauss(0, 0.02))))
        dates = synth_dates(len(closes))
        bars = []
        for i, c in enumerate(closes):
            spread = abs(rng.gauss(0, 0.01))
            bars.append(mk_bar(dates[i], c * (1 - spread / 2), c * (1 + spread),
                               c * (1 - spread), c, 2_000_000))
        return bars

    def test_replay_equals_truncated_slices(self):
        """THE critical guarantee: for every day, evaluating with the full
        future present (iter_days) gives byte-identical output to evaluating
        a dataset truncated at that day (detect_setup on the slice)."""
        bars = self._bars()
        replay = dict(rec.iter_days(bars))
        checked = 0
        for i in range(119, len(bars)):
            sliced = rec.detect_setup(bars[:i + 1])
            self.assertEqual(replay.get(i), sliced,
                             f"lookahead divergence at bar {i}")
            checked += 1
        self.assertGreater(checked, 500)
        self.assertGreater(len(replay), 50)   # the walk must produce setups

    def test_spy_context_is_trailing_only(self):
        bars = self._bars()
        spy = {b["date"]: 400 + i * 0.1 for i, b in enumerate(bars)}
        replay = dict(rec.iter_days(bars, spy_close=spy))
        for i in sorted(replay)[:40]:
            sliced_spy = {d: v for d, v in spy.items()
                          if d <= bars[i]["date"][:10]}
            self.assertEqual(replay[i],
                             rec.detect_setup(bars[:i + 1], spy_close=sliced_spy),
                             f"SPY-context lookahead at bar {i}")


class TestLabelOutcome(unittest.TestCase):
    def _flat(self, n, px=50.0):
        return ramp_bars([px] * n)

    def test_target_hit_first(self):
        bars = self._flat(40)
        bars[25]["high"] = 60.0
        out = rec.label_outcome(bars, 20, target=59.0, inval=45.0, horizon=30)
        self.assertTrue(out["win"])
        self.assertEqual(out["days_to_target"], 5)
        self.assertTrue(out["hit5"] and out["hit10"] and out["hit20"] and out["hit30"])
        self.assertFalse(out["fail"])

    def test_stop_hit_first(self):
        bars = self._flat(40)
        bars[23]["low"] = 44.0
        bars[27]["high"] = 60.0
        out = rec.label_outcome(bars, 20, target=59.0, inval=45.0, horizon=30)
        self.assertFalse(out["win"])
        self.assertTrue(out["fail"])
        self.assertEqual(out["days_to_fail"], 3)

    def test_same_bar_double_touch_counts_against(self):
        bars = self._flat(40)
        bars[24]["high"] = 60.0
        bars[24]["low"] = 44.0
        out = rec.label_outcome(bars, 20, target=59.0, inval=45.0, horizon=30)
        self.assertTrue(out["ambiguous"])
        self.assertFalse(out["win"])
        self.assertTrue(out["fail"])

    def test_neither_touched(self):
        out = rec.label_outcome(self._flat(90), 20, target=99.0, inval=1.0,
                                horizon=30)
        self.assertFalse(out["win"])
        self.assertFalse(out["fail"])
        self.assertEqual(out["bars_available"], 30)

    def test_horizon_5_flags_only_when_fast(self):
        bars = self._flat(60)
        bars[40]["high"] = 60.0            # 20 days out
        out = rec.label_outcome(bars, 20, target=59.0, inval=45.0, horizon=30)
        self.assertTrue(out["win"])
        self.assertFalse(out["hit5"])
        self.assertFalse(out["hit10"])
        self.assertTrue(out["hit20"])

    def test_exceed_flag(self):
        bars = self._flat(40)
        bars[25]["high"] = 62.0
        out = rec.label_outcome(bars, 20, target=59.0, inval=45.0, horizon=30)
        self.assertTrue(out["exceeded"])


class TestScoring(Base):
    def _setup(self):
        return rec.detect_setup(textbook_recovery())

    def test_no_model_no_numbers(self):
        s = self._setup()
        scored = rec.score_setup(s)
        self.assertFalse(scored["available"])
        self.assertIn("model", scored["reason"])
        self.assertIsNone(rec.opportunity_score(s, scored))
        text = rec.explain(s, scored)
        self.assertIn("No probability shown", text)
        self.assertNotIn("%.", text.split("No probability")[0][-20:])

    def test_fake_model_lookup(self):
        rec._MODEL = dict(FAKE_MODEL)
        s = self._setup()
        scored = rec.score_setup(s)
        self.assertTrue(scored["available"])
        self.assertIn(scored["p_win"], (0.10, 0.60))
        self.assertGreaterEqual(scored["n"], 500)
        self.assertEqual(scored["horizon"], 60)
        opp = rec.opportunity_score(s, scored)
        self.assertIsNotNone(opp)
        self.assertGreater(opp, 0)
        self.assertLessEqual(opp, 100)
        text = rec.explain(s, scored)
        self.assertIn("26.0% correction", text)
        self.assertIn("higher low", text.lower())
        self.assertIn(f"sample: {scored['n']}", text)

    def test_small_bucket_refused(self):
        m = dict(FAKE_MODEL)
        m["deciles"] = [dict(d, n=7) for d in FAKE_MODEL["deciles"]]
        rec._MODEL = m
        scored = rec.score_setup(self._setup())
        self.assertFalse(scored["available"])
        self.assertIn("insufficient", scored["reason"])

    def test_out_of_population_stage_refused(self):
        rec._MODEL = dict(FAKE_MODEL)
        s = dict(self._setup())
        s["stage"] = "approaching"
        scored = rec.score_setup(s)
        self.assertFalse(scored["available"])
        self.assertIn("Early/Confirmed", scored["reason"])

    def test_feature_mismatch_refused(self):
        m = dict(FAKE_MODEL)
        m["feature_names"] = ["recovery_ratio", "no_such_feature"]
        rec._MODEL = m
        scored = rec.score_setup(self._setup())
        self.assertFalse(scored["available"])

    def test_recovery_score_scales_with_bucket(self):
        rec._MODEL = dict(FAKE_MODEL)
        s = self._setup()
        scored = rec.score_setup(s)
        if scored["p_win"] == 0.60:
            self.assertAlmostEqual(scored["recovery_score"], 100.0)
        else:
            self.assertAlmostEqual(scored["recovery_score"], 0.0)


class TestScanJob(Base):
    def test_no_net_refused(self):
        r = rec.trigger_scan(["AAPL"])
        self.assertFalse(r["started"])
        self.assertIn("JERRY_NO_NET", r["reason"])

    def test_empty_watchlist_refused(self):
        os.environ.pop("JERRY_NO_NET", None)
        try:
            r = rec.trigger_scan([])
            self.assertFalse(r["started"])
            self.assertEqual(r["reason"], "watchlist empty")
        finally:
            os.environ["JERRY_NO_NET"] = "1"

    def test_busy_guard(self):
        with rec._LOCK:
            rec._STATE["scanning"] = True
        os.environ.pop("JERRY_NO_NET", None)
        try:
            r = rec.trigger_scan(["AAPL"])
            self.assertFalse(r["started"])
            self.assertEqual(r["reason"], "already scanning")
        finally:
            os.environ["JERRY_NO_NET"] = "1"
            with rec._LOCK:
                rec._STATE["scanning"] = False

    def test_board_envelope(self):
        b = rec.get_board()
        for k in ("as_of", "status", "count", "rows", "note", "model", "sectors"):
            self.assertIn(k, b)
        self.assertFalse(b["model"]["available"])

    def test_sector_summary_counts_and_trend(self):
        rows = [{"ticker": "A", "sector": "Technology"},
                {"ticker": "B", "sector": "Technology"},
                {"ticker": "C", "sector": "Energy"},
                {"ticker": "D", "sector": None}]
        trend = {"XLK": {"chg5": 0.01, "chg20": 0.05}}
        s = rec._sector_summary(rows, trend)
        self.assertEqual(s[0]["sector"], "Technology")
        self.assertEqual(s[0]["count"], 2)
        self.assertEqual(s[0]["etf"], "XLK")
        self.assertAlmostEqual(s[0]["chg20"], 0.05)
        by = {r["sector"]: r for r in s}
        self.assertIn("Other", by)          # null sector grouped, never dropped
        self.assertIsNone(by["Energy"]["chg20"])   # no trend data → no number

    def test_etf_trend_math(self):
        closes = {f"2026-01-{i:02d}": 100.0 + i for i in range(1, 26)}
        tr = rec._etf_trend(closes)
        self.assertAlmostEqual(tr["chg5"], 125 / 120 - 1, places=4)
        self.assertAlmostEqual(tr["chg20"], 125 / 105 - 1, places=4)
        self.assertIsNone(rec._etf_trend({"2026-01-01": 100.0}))
        self.assertIsNone(rec._etf_trend(None))

    def test_detail_no_net(self):
        d = rec.detail("AAPL")
        self.assertIn("error", d)
        self.assertIn("nothing fabricated", d["error"])

    def test_research_without_model(self):
        r = rec.research()
        self.assertFalse(r["available"])


class TestPersistence(Base):
    def test_board_round_trip(self):
        with rec._LOCK:
            rec._STATE["rows"] = [{"ticker": "TEST", "stage": "early"}]
            rec._STATE["last_scan"] = "2026-08-13T00:00:00+00:00"
            rec._STATE["sector_trend"] = {"XLK": {"chg5": 0.01, "chg20": 0.04}}
        rec._persist_board()
        with rec._LOCK:
            rec._STATE["rows"] = []
            rec._STATE["last_scan"] = None
            rec._STATE["sector_trend"] = {}
        rec._restore_board()
        with rec._LOCK:
            self.assertEqual(rec._STATE["rows"][0]["ticker"], "TEST")
            self.assertEqual(rec._STATE["last_scan"], "2026-08-13T00:00:00+00:00")
            self.assertAlmostEqual(rec._STATE["sector_trend"]["XLK"]["chg20"], 0.04)

    def test_corrupt_board_survived(self):
        p = rec._board_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        rec._restore_board()          # must not raise
        with rec._LOCK:
            self.assertEqual(rec._STATE["rows"], [])


class TestFitHelpers(unittest.TestCase):
    """recovery_fit.py math on synthetic data (no bars dir, no network)."""

    def test_logistic_recovers_signal(self):
        import numpy as np
        import recovery_fit as rf
        rng = np.random.default_rng(7)
        X = rng.normal(size=(3000, 4))
        true_w = np.array([1.2, -0.8, 0.0, 0.4])
        p = 1 / (1 + np.exp(-(X @ true_w + 0.3)))
        y = (rng.random(3000) < p).astype(float)
        w, b = rf.fit_logistic(X, y, l2=0.5)
        self.assertGreater(rf.auc(y, rf.predict(X, w, b)), 0.75)
        self.assertGreater(w[0], 0.8)
        self.assertLess(w[1], -0.5)

    def test_auc_extremes(self):
        import numpy as np
        import recovery_fit as rf
        y = np.array([0, 0, 1, 1.0])
        self.assertAlmostEqual(rf.auc(y, np.array([0.1, 0.2, 0.8, 0.9])), 1.0)
        self.assertAlmostEqual(rf.auc(y, np.array([0.9, 0.8, 0.2, 0.1])), 0.0)
        self.assertIsNone(rf.auc(np.ones(4), np.ones(4)))

    def test_outcome_stats_and_buckets(self):
        import recovery_fit as rf
        sigs = []
        for i in range(60):
            win = i % 3 == 0
            sigs.append({
                "features": {"recovery_ratio": 0.05 + (i % 10) * 0.1},
                "setup": {"days_since_low": 5, "has_higher_low": i % 2 == 0,
                          "broke_bounce": False, "relvol": 1.0,
                          "stage": "early", "regime": None},
                "outcome": {"win": win, "fail": not win, "ambiguous": False,
                            "days_to_target": 10 if win else None,
                            "days_to_fail": None if win else 8,
                            "bars_available": 60, "mfe": 5.0, "mae": -4.0,
                            "fwd20": 1.0, "fwd30": 2.0, "exceeded": win,
                            "hit5": False, "hit10": win, "hit20": win,
                            "hit30": win},
            })
        st = rf.outcome_stats(sigs)
        self.assertEqual(st["n"], 60)
        self.assertAlmostEqual(st["p_win"], 20 / 60, places=4)
        rows = rf.bucketize(sigs, lambda s: s["features"]["recovery_ratio"],
                            rf.RR_BUCKETS)
        self.assertEqual(sum(r["n"] for r in rows if r.get("n")), 60)

    def test_collect_signals_spacing_and_maturity(self):
        """End-to-end on a tiny synthetic bars dir: signals must be spaced,
        chronological, and only include decided/mature outcomes."""
        import gzip
        import recovery_fit as rf
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            bars = None
            import random
            rng = random.Random(99)
            closes = [100.0]
            for i in range(800):
                drift = 0.001 if (i // 100) % 2 == 0 else -0.0015
                closes.append(max(5, closes[-1] * (1 + drift + rng.gauss(0, 0.018))))
            dates = synth_dates(len(closes))
            bars = [mk_bar(dates[i], c * 0.995, c * 1.01, c * 0.99, c, 3_000_000)
                    for i, c in enumerate(closes)]
            with gzip.open(Path(td) / "SYN.json.gz", "wt") as f:
                json.dump(bars, f)
            sigs = rf.collect_signals(Path(td), ["SYN"], {}, None)
            if len(sigs) >= 2:
                d = [s["date"] for s in sigs]
                self.assertEqual(d, sorted(d))
                idx = {b["date"]: i for i, b in enumerate(bars)}
                gaps = [idx[d[j + 1]] - idx[d[j]] for j in range(len(d) - 1)]
                self.assertTrue(all(g >= rf.SPACING for g in gaps), gaps)
            for s in sigs:
                o = s["outcome"]
                self.assertTrue(o["win"] or o["fail"]
                                or o["bars_available"] >= rf.HORIZON)


if __name__ == "__main__":
    unittest.main()
