"""test_bt_iv.py (B2) — the layered IV model on deterministic fixtures.
Run:  python3 -m unittest test_bt_iv
"""
import math
import unittest
from datetime import date, timedelta

import bt_iv
from test_bt_options import mk_bars


def wavy_closes(n, px=100.0, amp=0.01):
    # Deterministic zig-zag → real, nonzero realized vol.
    return [px * (1 + amp * ((-1) ** i)) for i in range(n)]


class TestHV(unittest.TestCase):
    def test_hv_positive_on_wavy(self):
        closes = wavy_closes(80)
        h = bt_iv.hv(closes, 60, 20)
        self.assertGreater(h, 0.10)

    def test_hv_none_without_warmup(self):
        self.assertIsNone(bt_iv.hv([100.0] * 10, 5, 20))


class TestCalibration(unittest.TestCase):
    def _closes_by_date(self, closes, start="2026-01-02"):
        d0 = date.fromisoformat(start)
        return {(d0 + timedelta(days=i)).isoformat(): c for i, c in enumerate(closes)}

    def test_assumed_when_thin(self):
        r, src = bt_iv.calibrate_ratio([{"date": "2026-02-01", "iv": 0.3}], {})
        self.assertEqual((r, src), (bt_iv.DEFAULT_RATIO, "assumed"))

    def test_calibrated_ratio_recovered(self):
        # Stored IV = 1.5 × HV on every matched date → ratio ≈ 1.5.
        closes = wavy_closes(120)
        by_date = self._closes_by_date(closes)
        dates = sorted(by_date.keys())
        hist = []
        for i in range(30, 110):
            h = bt_iv.hv(closes, i, 20)
            hist.append({"date": dates[i], "iv": h * 1.5})
        r, src = bt_iv.calibrate_ratio(hist, by_date)
        self.assertAlmostEqual(r, 1.5, delta=0.05)
        self.assertIn("calibrated", src)

    def test_ratio_clamped(self):
        closes = wavy_closes(120)
        by_date = self._closes_by_date(closes)
        dates = sorted(by_date.keys())
        hist = [{"date": dates[i], "iv": bt_iv.hv(closes, i, 20) * 5.0}
                for i in range(30, 110)]
        r, _ = bt_iv.calibrate_ratio(hist, by_date)
        self.assertEqual(r, bt_iv.RATIO_BOUNDS[1])


class TestVixScaler(unittest.TestCase):
    def test_flat_when_unavailable(self):
        s, src = bt_iv.vix_scaler_series(["2026-01-02"], {})
        self.assertEqual(s, [1.0])
        self.assertIn("unavailable", src)

    def test_percentile_mapping(self):
        d0 = date(2025, 1, 1)
        dates = [(d0 + timedelta(days=i)).isoformat() for i in range(400)]
        # VIX grinds from 12 to 40 → late days sit at the top percentile.
        vix = {d: 12 + 28 * i / 399 for i, d in enumerate(dates)}
        s, src = bt_iv.vix_scaler_series(dates, vix)
        self.assertGreater(s[-1], s[50])
        self.assertLessEqual(max(s), bt_iv.VIX_SCALE_RANGE[1])
        self.assertGreaterEqual(min(s), min(1.0, bt_iv.VIX_SCALE_RANGE[0]))
        self.assertIn("percentile", src)


class TestEarningsRamp(unittest.TestCase):
    def test_ramp_shape(self):
        e = [date(2026, 3, 15)]
        self.assertEqual(bt_iv.earnings_mult(date(2026, 3, 1), e), 1.0)   # far out
        d3 = bt_iv.earnings_mult(date(2026, 3, 12), e)                    # 3 days out
        d0 = bt_iv.earnings_mult(date(2026, 3, 15), e)                    # report day
        after = bt_iv.earnings_mult(date(2026, 3, 16), e)                 # crushed
        self.assertGreater(d0, d3)
        self.assertGreater(d3, 1.0)
        self.assertAlmostEqual(d0, 1.0 + bt_iv.EARNINGS_RAMP_PEAK, places=6)
        self.assertEqual(after, 1.0)


class TestSeries(unittest.TestCase):
    def test_series_floor_and_no_lookahead_warmup(self):
        bars = mk_bars(closes=wavy_closes(120, amp=0.0005))  # tiny vol → floor
        s = bt_iv.build_iv_series(bars, 1.1, [1.0] * 120)
        self.assertTrue(all(v is None or v >= bt_iv.IV_FLOOR for v in s))
        self.assertIsNone(s[5])                              # warm-up honest None

    def test_earnings_bump_visible_in_series(self):
        bars = mk_bars(closes=wavy_closes(120))
        e_day = bars[100]["date"]
        s0 = bt_iv.build_iv_series(bars, 1.1, [1.0] * 120)
        s1 = bt_iv.build_iv_series(bars, 1.1, [1.0] * 120, earnings_dates=[e_day])
        self.assertGreater(s1[99], s0[99])                   # ramped the day before
        self.assertAlmostEqual(s1[110], s0[110], places=9)   # crushed after

    def test_meta_lines(self):
        lines = bt_iv.model_meta(1.32, "calibrated (n=40)", "VIX percentile (trailing year)", True)
        self.assertTrue(any("1.32" in x and "calibrated" in x for x in lines))
        self.assertEqual(len(lines), 4)


if __name__ == "__main__":
    unittest.main()
