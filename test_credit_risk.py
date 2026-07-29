"""test_credit_risk.py — the Merton structural-credit engine on pinned
fixtures. Run:  python3 -m unittest test_credit_risk
"""
import math
import unittest

import credit_risk as cr
from metrics import _norm_cdf


class TestMertonSolver(unittest.TestCase):
    def test_roundtrip_consistency(self):
        # Solve, then verify the solution actually reproduces the observed
        # equity through the Merton call formula (the defining property).
        m = cr.merton_solve(equity=40.0, equity_vol=0.55, debt=80.0, r=0.04)
        self.assertIsNotNone(m)
        V, vol_V = m["V"], m["vol_V"]
        T = cr.T_YEARS
        d1 = (math.log(V / 80.0) + (0.04 + 0.5 * vol_V ** 2) * T) / (vol_V * math.sqrt(T))
        d2 = d1 - vol_V * math.sqrt(T)
        E_model = V * _norm_cdf(d1) - 80.0 * math.exp(-0.04 * T) * _norm_cdf(d2)
        self.assertAlmostEqual(E_model, 40.0, delta=0.05)

    def test_leverage_monotonic_realistic_regime(self):
        # More debt on the same equity → wider spread across the realistic
        # leverage range. (At EXTREME leverage with equity vol held fixed,
        # Merton's implied asset vol compresses — the model is saying "if
        # a 76%-levered firm only shows 40% equity vol, its assets must be
        # rock-stable" — so the naive always-monotonic intuition is wrong;
        # in reality leverage arrives WITH rising equity vol, tested below.)
        spreads = []
        for debt in (10, 40, 80):
            m = cr.merton_solve(50.0, 0.40, float(debt), 0.04)
            spreads.append(m["spread_bps"])
        self.assertEqual(spreads, sorted(spreads))
        self.assertGreater(spreads[-1], spreads[0] + 30)

    def test_joint_leverage_and_vol_move_widens(self):
        # The realistic deterioration: price falls (equity shrinks) AND
        # equity vol rises → spread must widen decisively.
        healthy = cr.merton_solve(80.0, 0.30, 60.0, 0.04)["spread_bps"]
        stressed = cr.merton_solve(40.0, 0.55, 60.0, 0.04)["spread_bps"]
        self.assertGreater(stressed, healthy * 3)

    def test_vol_monotonic(self):
        s_lo = cr.merton_solve(50.0, 0.25, 80.0, 0.04)["spread_bps"]
        s_hi = cr.merton_solve(50.0, 0.60, 80.0, 0.04)["spread_bps"]
        self.assertGreater(s_hi, s_lo)

    def test_negligible_debt_is_zero_spread(self):
        # The NVDA case: huge equity, tiny debt → ~0 bps, no false alarm.
        m = cr.merton_solve(4_000_000.0, 0.45, 10_000.0, 0.04)   # $4T vs $10B
        self.assertLess(m["spread_bps"], 1.0)
        self.assertLess(m["pd"], 1e-6)

    def test_no_debt(self):
        m = cr.merton_solve(100.0, 0.5, 0.0, 0.04)
        self.assertEqual(m["spread_bps"], 0.0)
        self.assertEqual(m["leverage"], 0.0)

    def test_degenerate_inputs(self):
        self.assertIsNone(cr.merton_solve(0.0, 0.5, 10.0, 0.04))
        self.assertIsNone(cr.merton_solve(10.0, 0.0, 10.0, 0.04))

    def test_distressed_name_prices_hundreds_of_bps(self):
        # Deep leverage + high vol → a spread in genuinely distressed
        # territory (hundreds of bps), bounded (not thousands of %).
        m = cr.merton_solve(equity=15.0, equity_vol=0.80, debt=100.0, r=0.04)
        self.assertGreater(m["spread_bps"], 200)
        self.assertLess(m["spread_bps"], 5000)


class TestHelpers(unittest.TestCase):
    def test_default_point_kmv(self):
        self.assertEqual(cr.default_point(10.0, 40.0, None), 30.0)
        self.assertEqual(cr.default_point(None, None, 100.0), 75.0)
        self.assertIsNone(cr.default_point(None, None, None))

    def test_series_shape_and_direction(self):
        from datetime import date, timedelta
        d0 = date(2026, 1, 2)
        # Price slides 30% (with real day-to-day wiggle so HV is nonzero)
        # → the model spread must WIDEN along the series.
        closes = [100.0 * (1 - 0.3 * i / 199) * (1 + 0.012 * ((-1) ** i))
                  for i in range(200)]
        bars = [{"date": (d0 + timedelta(days=i)).isoformat(), "close": c}
                for i, c in enumerate(closes)]
        s = cr.merton_series(bars, shares_out=1.0, debt_point=60.0, r=0.04)
        self.assertGreater(len(s), 100)
        self.assertGreater(s[-1]["spread_bps"], s[0]["spread_bps"])
        self.assertIn("dd", s[0])

    def test_interpret_negligible_leverage(self):
        txt = cr.interpret([{"spread_bps": 0.1}], leverage_pct=0.3)
        self.assertIn("negligible", txt)
        self.assertIn("put-skew", txt)

    def test_interpret_widening(self):
        series = [{"spread_bps": 40.0}] * 21 + [{"spread_bps": 90.0}]
        txt = cr.interpret(series, leverage_pct=30.0)
        self.assertIn("WIDENING", txt)


class TestSkewGauge(unittest.TestCase):
    def test_gauge_fields(self):
        calls = [{"strike": 110, "delta": 0.25, "iv": 0.30, "bid": 1.0, "ask": 1.2}]
        puts = [{"strike": 90, "delta": -0.25, "iv": 0.38, "bid": 1.5, "ask": 1.7},
                {"strike": 80, "delta": -0.10, "iv": 0.45, "bid": 0.5, "ask": 0.7}]
        g = cr.skew_gauge(calls, puts, spot=100.0, dte=120)
        self.assertAlmostEqual(g["rr25_vol_pts"], 8.0, places=6)
        self.assertEqual(g["crash_put"]["strike"], 80)
        self.assertEqual(g["crash_put"]["otm_pct"], 20.0)
        # 0.6 mid / 100 spot × 365/120 → ~1.83%/yr
        self.assertAlmostEqual(g["crash_put"]["annualized_pct"], 1.83, delta=0.02)

    def test_gauge_none_when_empty(self):
        self.assertIsNone(cr.skew_gauge([], [], 100.0, 30))


if __name__ == "__main__":
    unittest.main()
