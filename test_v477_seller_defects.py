"""v4.77 — defects the seller audit found in code that every unit test passed.

Each test here fails against the code before the fix. They are grouped by
the defect, and each docstring says what the defect did to a real trade.
"""
from __future__ import annotations

import json
import random
import unittest
from datetime import date
from pathlib import Path

import edge_scan
import setup_engine as SE
import setup_scan as SS
import vol_forecast as vf


# ── 1. The gap-calibrated forecast never ran live ───────────────────────────
class TestTheConfiguredForecastIsTheCalibratedOne(unittest.TestCase):
    """v4.65 measured that raw Parkinson under-reads vol by ~7.5 points and
    replaced it with the gap-calibrated PARK20C in vol_forecast.GLOBAL_WEIGHTS.
    thresholds.json still named PARK20, and expected_rv30 prefers the config,
    so the deployed forecast kept the bias the release note said was gone."""

    def test_thresholds_name_the_calibrated_range_voice(self):
        cfg = json.loads(Path(__file__).with_name("thresholds.json").read_text())
        w = cfg["premium_edge"]["forecast"]["global_weights"]
        self.assertIn("PARK20C", w)
        self.assertNotIn("PARK20", w, "raw Parkinson is the bias v4.65 removed")

    def test_the_live_blend_uses_the_calibrated_voice(self):
        cfg = json.loads(Path(__file__).with_name("thresholds.json").read_text())
        fcfg = cfg["premium_edge"]["forecast"]
        rnd = random.Random(7)
        bars, px = [], 100.0
        for i in range(400):
            px *= (1 + rnd.gauss(0, 0.02))
            o = px
            h, lo = o * 1.003, o * 0.997
            bars.append({"date": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
                         "open": o, "high": h, "low": lo, "close": o, "volume": 1})
        out = vf.expected_rv30(bars, fcfg)
        self.assertIsNotNone(out)
        comps = out["components"]
        self.assertGreater(comps["PARK20C"], comps["PARK20"])
        raw = vf._blend(comps, {"RV20": .3, "EWMA94": .35, "PARK20": .35},
                        out["anchor"], fcfg["anchor_shrink"])
        cal = vf._blend(comps, {"RV20": .3, "EWMA94": .35, "PARK20C": .35},
                        out["anchor"], fcfg["anchor_shrink"])
        self.assertGreater(cal, raw)
        self.assertAlmostEqual(out["erv30"], round(cal, 4), places=4)


# ── 2. The Premium Edge chain fetch was unbounded ───────────────────────────
class TestTheEdgeChainFetchIsBoundedInDates(unittest.TestCase):
    """get_option_chain sends a date range only when `expiration` is set.
    edge_scan passed `to_date` alone, so Schwab was asked for every listed
    expiration — the unbounded request v4.60 removed elsewhere — and the
    documented "one call, 95 days out" never went over the wire."""

    def test_the_call_carries_both_dates(self):
        calls = []

        class _SC:
            def get_price_history(self, sym, days=260):
                return [{"date": f"2025-01-{i % 28 + 1:02d}", "open": 100, "high": 101,
                         "low": 99, "close": 100, "volume": 1} for i in range(200)]

            def get_option_chain(self, sym, expiration=None, strike_count=60, to_date=None):
                calls.append({"expiration": expiration, "to_date": to_date,
                              "strike_count": strike_count})
                return None

            def rate_usage(self):
                return 0

        saved = edge_scan._SCHWAB
        try:
            edge_scan._SCHWAB = lambda: _SC()
            edge_scan.analyze_symbol("AAPL", intent="premium_only",
                                     now=date(2026, 9, 3))
        finally:
            edge_scan._SCHWAB = saved
        self.assertEqual(len(calls), 1, calls)
        c = calls[0]
        self.assertEqual(c["expiration"], "2026-09-03")
        self.assertIsNotNone(c["to_date"])
        self.assertGreater(c["to_date"], c["expiration"])


# ── 3. Calendar days were counted as trading bars ───────────────────────────
class TestTheHorizonIsInTradingBars(unittest.TestCase):
    """A 45-DTE option was measured over 45 bars, about 63 calendar days —
    every touch rate came from a window ~40% longer than the trade."""

    def test_calendar_days_convert_to_sessions(self):
        self.assertEqual(SS.horizon_bars(45), 31)
        self.assertEqual(SS.horizon_bars(7), 5)
        self.assertEqual(SS.horizon_bars(30), 21)
        self.assertEqual(SS.horizon_bars(0), 1)
        self.assertEqual(SS.horizon_bars(None), 1)

    def test_the_measurement_uses_the_converted_horizon(self):
        src = Path(__file__).with_name("setup_scan.py").read_text()
        self.assertIn("horizon = horizon_bars(dte)", src)
        self.assertNotIn("horizon = max(1, int(round(dte)))", src)


# ── 4. Overlapping windows were counted as independent trials ──────────────
class TestOverlappingWindowsAreNotIndependentTrials(unittest.TestCase):
    def test_consecutive_starts_collapse_to_the_non_overlapping_count(self):
        idx = list(range(100))
        self.assertEqual(SS.independent_windows(idx, 20), 5)
        self.assertEqual(SS.independent_windows(idx, 1), 100)
        self.assertEqual(SS.independent_windows([], 20), 0)
        self.assertEqual(SS.independent_windows([5, 6, 40, 41, 90], 20), 3)

    def test_the_curve_reports_both_counts(self):
        highs = [100 + (i % 7) for i in range(120)]
        lows = [95 - (i % 5) for i in range(120)]
        closes = [100.0] * 120
        mx, mn = SS.forward_extremes(highs, lows, 10)
        idx = [i for i in range(120) if mx[i] is not None]
        curve = SS.touch_curve(closes, mx, mn, idx, (1.0, 50.0), True, horizon=10)
        self.assertEqual(curve[1.0]["n"], len(idx))
        self.assertEqual(curve[1.0]["n_eff"], SS.independent_windows(idx, 10))
        self.assertLess(curve[1.0]["n_eff"], curve[1.0]["n"] / 5)

    def test_the_wilson_bound_runs_on_the_independent_count(self):
        cond = {"5.0": {"rate": 10.0, "n": 400, "n_eff": 20}}
        base = {"5.0": {"rate": 20.0, "n": 400, "n_eff": 20}}
        got = SE.measured_touch(cond, base, min_n=10)
        row = got["rows"][0]
        self.assertEqual(row["n"], 400)
        self.assertEqual(row["n_eff"], 20)
        self.assertLess(row["keep_pct_low"], 75.0)
        self.assertGreater(row["keep_pct_low"], 60.0)

    def test_the_sample_floor_is_on_independent_windows(self):
        cond = {"5.0": {"rate": 10.0, "n": 400, "n_eff": 12}}
        got = SE.measured_touch(cond, {}, min_n=30)
        self.assertFalse(got["usable"])
        self.assertIn("independent", got["reason"])
        self.assertEqual(got["max_n"], 12)
        self.assertEqual(got["max_n_raw"], 400)


# ── 5. forward_extremes: same answer, one pass ──────────────────────────────
class TestTheSlidingExtremesMatchTheBruteForce(unittest.TestCase):
    def test_random_series_agree_with_the_definition(self):
        rnd = random.Random(3)
        for _ in range(20):
            n = rnd.randint(1, 80)
            d = rnd.randint(1, 12)
            highs = [rnd.uniform(90, 110) for _ in range(n)]
            lows = [h - rnd.uniform(0, 5) for h in highs]
            mx, mn = SS.forward_extremes(highs, lows, d)
            for i in range(n):
                j0, j1 = i + 1, i + 1 + d
                if j1 > n:
                    self.assertIsNone(mx[i]); self.assertIsNone(mn[i])
                else:
                    self.assertEqual(mx[i], max(highs[j0:j1]))
                    self.assertEqual(mn[i], min(lows[j0:j1]))


# ── 6. Missing gamma data narrowed the default band ─────────────────────────
class TestAbsentGammaDataIsNotAReading(unittest.TestCase):
    def test_unknown_leaves_the_band_alone(self):
        self.assertEqual(SE.GEX_DELTA_ADJUST["unknown"], 1.0)
        ceiling = {"band": (0.15, 0.22), "default_band": (0.15, 0.22)}
        out = SE.apply_gex_to_ceiling(ceiling, {"verdict": "unknown"})
        self.assertEqual(out["band"], (0.15, 0.22))
        out2 = SE.apply_gex_to_ceiling(ceiling, {"verdict": "something-new"})
        self.assertEqual(out2["band"], (0.15, 0.22))

    def test_opposes_still_pulls_both_edges(self):
        out = SE.apply_gex_to_ceiling({"band": (0.15, 0.22), "default_band": (0.15, 0.22)},
                                      {"verdict": "opposes", "note": "x"})
        self.assertEqual(out["band"], (0.12, 0.176))


# ── 7. Liquidity was a penalty, not a gate ──────────────────────────────────
def _contract(strike, delta, liquid=True, ev=40.0):
    return {"strike": strike, "delta": delta, "dist_pct": -8.0, "credit_exec": 1.2,
            "credit_basis": "bid", "ev_per_contract": ev, "p_itm_model": 0.17,
            "p_touch_model": 0.31, "es5_per_share": 4.0, "spread_pct": 5.0,
            "oi": 500 if liquid else 3, "liquidity_ok": liquid,
            "liquidity_notes": [] if liquid else ["open interest 3 < 100"],
            "prob_basis": "model (driftless lognormal at ExpectedRV30)"}


class TestAnIlliquidContractIsNotARecommendation(unittest.TestCase):
    def _run(self, contracts):
        return SE.recommend("XYZ", 100.0, "put", "2026-10-17", 44, contracts,
                            {"lean": None, "why": [], "conflict": False},
                            {"band": (0.15, 0.22), "default_band": (0.15, 0.22),
                             "raised": False, "why": "default"})

    def test_the_only_in_band_contract_being_illiquid_means_no_trade(self):
        out = self._run([_contract(92, -0.18, liquid=False, ev=80.0)])
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("illiquid"))
        self.assertIn("liquidity gate", out["reason"])

    def test_a_liquid_neighbour_wins_over_a_richer_illiquid_one(self):
        out = self._run([_contract(92, -0.18, liquid=False, ev=80.0),
                         _contract(91, -0.17, liquid=True, ev=30.0)])
        self.assertTrue(out["ok"])
        self.assertEqual(out["strike"], 91)


# ── 8. Touch probability spoke in fractions beside a percent ────────────────
class TestTouchAndKeepShareOneUnit(unittest.TestCase):
    def test_both_are_percent(self):
        out = SE.recommend("XYZ", 100.0, "put", "2026-10-17", 44, [_contract(92, -0.18)],
                           {"lean": None, "why": [], "conflict": False},
                           {"band": (0.15, 0.22), "default_band": (0.15, 0.22),
                            "raised": False, "why": "default"})
        self.assertTrue(out["ok"])
        self.assertAlmostEqual(out["p_keep_model"], 83.0, places=1)
        self.assertAlmostEqual(out["p_touch_model"], 31.0, places=1)
        self.assertIn("lognormal", out["prob_basis"])


# ── 9. A retryable failure was cached for 90 seconds ────────────────────────
class TestARetryableFailureIsNotCached(unittest.TestCase):
    def test_the_next_call_asks_again(self):
        calls = []
        saved = SS.analyze
        try:
            def fake(sym):
                calls.append(sym)
                return {"ok": False, "symbol": sym, "retryable": True,
                        "error": "the request did not complete"}
            SS.analyze = fake
            SS.invalidate()
            a = SS.get("RETRY")
            b = SS.get("RETRY")
            self.assertFalse(a.get("cached")); self.assertFalse(b.get("cached"))
            self.assertEqual(calls, ["RETRY", "RETRY"])
        finally:
            SS.analyze = saved
            SS.invalidate()

    def test_a_real_answer_is_still_cached(self):
        calls = []
        saved = SS.analyze
        try:
            def fake(sym):
                calls.append(sym)
                return {"ok": True, "symbol": sym}
            SS.analyze = fake
            SS.invalidate()
            SS.get("KEEP"); b = SS.get("KEEP")
            self.assertTrue(b.get("cached"))
            self.assertEqual(calls, ["KEEP"])
        finally:
            SS.analyze = saved
            SS.invalidate()


# ── 10. The breach model column was priced at a fixed 30 vol ───────────────
class TestTheBreachModelUsesTheSigmaItPlacedStrikesWith(unittest.TestCase):
    def test_a_low_vol_tape_gets_a_low_vol_model_column(self):
        rnd = random.Random(11)
        bars, px = [], 50.0
        for i in range(700):
            px *= (1 + rnd.gauss(0, 0.004))
            bars.append({"date": f"d{i}", "open": px, "high": px * 1.004,
                         "low": px * 0.996, "close": px, "volume": 1})
        out = edge_scan.breach_stats(bars, {"breach": {"horizons_td": [21],
                                                       "k_sigmas": [1.0],
                                                       "min_windows": 60}})
        row = out["rows"][0]
        # At k=1 the driftless P(ITM) is N(-1 + s/2) with s = σ√T; at σ=0.30
        # and T=21/252 that is ≈0.1693, at σ≈0.06 it is ≈0.1608. The column
        # must move with σ.
        self.assertLess(row["itm_model"], 0.165)
        self.assertGreater(row["itm_model"], 0.150)


if __name__ == "__main__":
    unittest.main()
