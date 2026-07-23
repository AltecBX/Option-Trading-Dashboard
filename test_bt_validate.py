"""test_bt_validate.py (B3) — deterministic tests for the validation suite.
Run:  python3 -m unittest test_bt_validate
"""
import math
import unittest
from datetime import date, timedelta

import bt_validate as bv


def curve(vals, start="2026-01-02"):
    d0 = date.fromisoformat(start)
    return [{"date": (d0 + timedelta(days=i)).isoformat(), "equity": v}
            for i, v in enumerate(vals)]


class TestCurveStats(unittest.TestCase):
    def test_max_drawdown(self):
        self.assertAlmostEqual(bv.max_drawdown_pct([100, 120, 90, 130]),
                               25.0, places=6)

    def test_sharpe_positive_grind(self):
        vals = [100_000 * (1.0004 ** i) for i in range(260)]
        s = bv.sharpe_from_curve(curve(vals))
        self.assertGreater(s["sharpe"], 5)          # steady grind = huge Sharpe
        self.assertGreater(s["cagr_pct"], 5)
        self.assertEqual(s["daily_n"], 259)

    def test_sharpe_needs_history(self):
        self.assertIsNone(bv.sharpe_from_curve(curve([100.0] * 5)))


class TestMonteCarlo(unittest.TestCase):
    PNLS = [200, 150, 180, -400, 220, 160, -350, 190, 210, -100,
            175, 205, -250, 230, 185, 195, -150, 240, 170, -300]

    def test_deterministic_with_seed(self):
        a = bv.monte_carlo(self.PNLS, 100_000, n_paths=2000, seed=7)
        b = bv.monte_carlo(self.PNLS, 100_000, n_paths=2000, seed=7)
        self.assertEqual(a, b)
        c = bv.monte_carlo(self.PNLS, 100_000, n_paths=2000, seed=8)
        self.assertNotEqual(a["max_dd_pct"], c["max_dd_pct"])

    def test_percentiles_ordered_and_ruin(self):
        mc = bv.monte_carlo(self.PNLS, 100_000, n_paths=3000, seed=1)
        dd = mc["max_dd_pct"]
        self.assertLessEqual(dd["p5"], dd["p50"])
        self.assertLessEqual(dd["p50"], dd["p95"])
        fe = mc["final_equity"]
        self.assertLessEqual(fe["p5"], fe["p95"])
        self.assertGreaterEqual(mc["risk_of_ruin_pct"], 0.0)
        # These P/Ls are tiny vs $100k equity — ruin (25% DD) is impossible.
        self.assertEqual(mc["risk_of_ruin_pct"], 0.0)

    def test_ruin_detected_when_sized_up(self):
        big = [p * 40 for p in self.PNLS]            # 40× the size
        mc = bv.monte_carlo(big, 100_000, n_paths=3000, seed=1)
        self.assertGreater(mc["risk_of_ruin_pct"], 1.0)

    def test_thin_sample_refused(self):
        self.assertIsNone(bv.monte_carlo([100, -50], 100_000))


class TestWalkForward(unittest.TestCase):
    def _dates(self, n=200):
        d0 = date(2025, 1, 1)
        return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]

    def test_consistent_strategy(self):
        # Runner: profits everywhere → WFE ≈ 1, all OOS folds positive.
        def runner(lo, hi):
            n = (date.fromisoformat(hi) - date.fromisoformat(lo)).days
            # Exactly $10/trade everywhere → WFE must be ~1.0. (n_trades
            # must equal n or integer division skews pnl-per-trade.)
            return {"total_pnl": n * 10.0, "n_trades": max(1, n), "win_rate": 60}
        wf = bv.walk_forward(self._dates(), runner, folds=4)
        self.assertEqual(wf["oos_positive_folds"], "4/4")
        self.assertAlmostEqual(wf["wf_efficiency"], 1.0, delta=0.15)
        self.assertEqual(wf["verdict"], "consistent out of sample")

    def test_curve_fit_strategy(self):
        # Runner: only makes money in-sample (first 70% of each fold).
        calls = {"n": 0}
        def runner(lo, hi):
            calls["n"] += 1
            is_call = calls["n"] % 2 == 1          # runner called IS, OOS, IS, OOS…
            pnl = 500.0 if is_call else -300.0
            return {"total_pnl": pnl, "n_trades": 10, "win_rate": 50}
        wf = bv.walk_forward(self._dates(), runner, folds=4)
        self.assertIn("curve-fit", wf["verdict"])

    def test_thin_refused(self):
        self.assertIsNone(bv.walk_forward(self._dates(10), lambda a, b: {}, folds=4))


class TestDeflatedSharpe(unittest.TestCase):
    def test_psr_at_benchmark_is_half(self):
        # Observed SR equal to the benchmark → P = 0.5 exactly (z = 0).
        self.assertAlmostEqual(bv.psr(0.1, 0.1, 100, 0.0, 3.0), 0.5, places=9)

    def test_dsr_penalizes_trials(self):
        d1 = bv.deflated_sharpe(1.5, 252, 0.0, 3.0, n_trials=1)
        d200 = bv.deflated_sharpe(1.5, 252, 0.0, 3.0, n_trials=200)
        self.assertGreater(d1["dsr"], d200["dsr"])   # more tries = higher hurdle
        self.assertGreater(d200["hurdle_sr_annual"], d1["hurdle_sr_annual"])

    def test_strong_sharpe_survives_with_enough_data(self):
        # A 3.0 Sharpe over THREE years clears a best-of-50 hurdle…
        d3y = bv.deflated_sharpe(3.0, 756, 0.0, 3.0, n_trials=50)
        self.assertGreaterEqual(d3y["dsr"], 0.95)
        self.assertIn("statistically real", d3y["verdict"])
        # …but the SAME Sharpe over one year does NOT — the estimator's own
        # noise is too large. This is the DSR doing its job.
        d1y = bv.deflated_sharpe(3.0, 252, 0.0, 3.0, n_trials=50)
        self.assertLess(d1y["dsr"], 0.95)

    def test_weak_sharpe_flagged(self):
        d = bv.deflated_sharpe(0.3, 252, 0.0, 3.0, n_trials=200)
        self.assertLess(d["dsr"], 0.5)
        self.assertIn("luckiest", d["verdict"])


class TestPlateau(unittest.TestCase):
    def test_plateau_vs_lone_peak(self):
        # Grid A: smooth plateau around (2,2). Grid B: lone spike at (2,2).
        def grid(spike):
            out = []
            for x in range(5):
                for y in range(5):
                    if spike:
                        v = 100.0 if (x, y) == (2, 2) else -20.0
                    else:
                        v = 100.0 - 10 * (abs(x - 2) + abs(y - 2))
                    out.append({"x": x, "y": y, "pnl": v})
            return out
        a = bv.plateau_score(grid(False), "x", "y", "pnl")
        b = bv.plateau_score(grid(True), "x", "y", "pnl")
        self.assertGreater(a["plateau"], 0.7)
        self.assertLess(b["plateau"], 0.2)
        self.assertEqual(a["best"]["x"], 2)
        # Robust pick on the spiky grid is NOT guaranteed to be the peak's
        # cell — just assert it exists and the note warns about peaks.
        self.assertIn("curve-fit", b["note"])


class TestRegimeMatrix(unittest.TestCase):
    def test_cells_and_concentration(self):
        trades = ([{"entry_date": "2026-01-10", "pnl": 500, "regime": "uptrend"}] * 8
                  + [{"entry_date": "2026-02-10", "pnl": -50, "regime": "downtrend"}] * 2)
        vol = {"2026-01-10": "low", "2026-02-10": "high"}
        rm = bv.regime_matrix(trades, vol)
        self.assertEqual(rm["cells"][0]["trend"], "uptrend")
        self.assertIsNotNone(rm["concentration_warning"])
        self.assertIn("uptrend", rm["concentration_warning"])

    def test_vol_terciles_shape(self):
        d0 = date(2025, 1, 1)
        vix = {(d0 + timedelta(days=i)).isoformat(): 12 + (i % 30) for i in range(400)}
        terc = bv.vol_terciles(vix)
        self.assertTrue(set(terc.values()) <= {"low", "mid", "high"})
        self.assertGreater(len(terc), 300)


if __name__ == "__main__":
    unittest.main()
