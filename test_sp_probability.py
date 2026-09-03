"""sp_probability — the invariants a probability engine must never break.

Weighted toward the ways it could be wrong: a probability outside [0,1], a
farther strike that reads as riskier, a call and put that disagree by
symmetry, a touch below a finish, a "conservative" bound that is not below
the point estimate, a fixture that drifts from the code's defaults.
"""
from __future__ import annotations

import json
import math
import random
import unittest
from pathlib import Path

import premium_edge as pe
import sp_probability as sp
import vol_forecast as vf


def _bars(n=400, seed=1, vol=0.02, start=100.0):
    rnd = random.Random(seed)
    out, px = [], start
    for i in range(n):
        px *= (1 + rnd.gauss(0, vol))
        out.append({"date": f"d{i}", "open": px, "high": px * (1 + abs(rnd.gauss(0, 0.006))),
                    "low": px * (1 - abs(rnd.gauss(0, 0.006))), "close": px, "volume": 1})
    return out


class TestBoundsAndSymmetry(unittest.TestCase):
    def test_every_probability_is_inside_the_unit_interval(self):
        for sigma in (0.05, 0.3, 1.2):
            for dte in (1, 7, 45, 120):
                for strike in (50, 90, 99.9, 100, 100.1, 110, 200):
                    for side in ("put", "call"):
                        t = dte / 365.0
                        for model in ("lognormal", "student_t"):
                            for fn in (lambda: sp.p_itm(100, strike, sigma, t, side, model=model),
                                       lambda: sp.p_touch(100, strike, sigma, t, model=model),
                                       lambda: sp.p_touch(100, strike, sigma, t, "daily", model=model),
                                       lambda: sp.p_profit(100, strike, sigma, t, side, 1.0, 0.01, model=model)):
                                p = fn()
                                self.assertIsNotNone(p)
                                self.assertGreaterEqual(p, 0.0)
                                self.assertLessEqual(p, 1.0)

    def test_p0_and_p_itm_are_complements(self):
        for strike in (85, 95, 105, 115):
            for side in ("put", "call"):
                a = sp.p_itm(100, strike, 0.3, 0.1, side)
                b = sp.p_expire_worthless(100, strike, 0.3, 0.1, side)
                self.assertAlmostEqual(a + b, 1.0, places=9)

    def test_put_and_call_agree_with_premium_edge(self):
        for strike, side in ((90, "put"), (110, "call"), (95, "put"), (105, "call")):
            self.assertAlmostEqual(sp.p_itm(100, strike, 0.3, 0.1, side),
                                   pe.p_itm(100, strike, 0.3, 0.1, side), places=4)

    def test_touch_is_never_below_finish_in_the_money(self):
        for strike, side in ((90, "put"), (95, "put"), (105, "call"), (120, "call")):
            for model in ("lognormal", "student_t"):
                pit = sp.p_itm(100, strike, 0.3, 0.12, side, model=model)
                pt = sp.p_touch(100, strike, 0.3, 0.12, model=model)
                self.assertGreaterEqual(pt + 1e-12, pit)


class TestMonotonicity(unittest.TestCase):
    """Under identical assumptions, farther out of the money can never be
    modeled as MORE likely to finish or touch."""

    def test_p0_rises_as_the_strike_moves_away(self):
        for model in ("lognormal", "student_t"):
            puts = [sp.p_expire_worthless(100, k, 0.3, 0.1, "put", model=model) for k in (98, 95, 90, 85, 80, 70)]
            calls = [sp.p_expire_worthless(100, k, 0.3, 0.1, "call", model=model) for k in (102, 105, 110, 115, 120, 130)]
            self.assertEqual(puts, sorted(puts))
            self.assertEqual(calls, sorted(calls))

    def test_touch_falls_as_the_strike_moves_away(self):
        for mon in ("continuous", "daily"):
            pts = [sp.p_touch(100, k, 0.3, 0.1, mon) for k in (99, 95, 90, 85, 80)]
            self.assertEqual(pts, sorted(pts, reverse=True))

    def test_more_time_and_more_vol_raise_the_risk(self):
        a = sp.p_itm(100, 90, 0.3, 10 / 365, "put")
        b = sp.p_itm(100, 90, 0.3, 45 / 365, "put")
        c = sp.p_itm(100, 90, 0.5, 45 / 365, "put")
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_the_tail_correction_never_lowers_a_risk_or_breaks_order(self):
        zs = [-0.5, -1.0, -1.25, -1.5, -2.0, -2.5]
        raw = [sp.p_itm(100, 100 * math.exp(z * 0.3 * math.sqrt(0.1)), 0.3, 0.1, "put") for z in zs]
        adj = [sp.tail_corrected(p, z) for p, z in zip(raw, zs)]
        for r, a, z in zip(raw, adj, zs):
            if abs(z) <= 1.25:
                self.assertAlmostEqual(a, r, places=9)
            else:
                self.assertGreaterEqual(a, r)
        self.assertEqual(adj, sorted(adj, reverse=True))


class TestTheModelsDifferWhereTheyShould(unittest.TestCase):
    def test_student_t_has_fatter_far_tails_and_thinner_near_ones(self):
        near_ln = sp.p_itm(100, 97, 0.3, 0.1, "put")
        near_t = sp.p_itm(100, 97, 0.3, 0.1, "put", model="student_t")
        far_ln = sp.p_itm(100, 70, 0.3, 0.1, "put")
        far_t = sp.p_itm(100, 70, 0.3, 0.1, "put", model="student_t")
        self.assertLess(near_t, near_ln)
        self.assertGreater(far_t, far_ln)

    def test_daily_monitoring_touch_is_below_continuous(self):
        for k in (95, 90, 85):
            self.assertLess(sp.p_touch(100, k, 0.3, 0.1, "daily"),
                            sp.p_touch(100, k, 0.3, 0.1, "continuous"))

    def test_continuous_touch_is_the_reflection_principle(self):
        z = math.log(90 / 100) / (0.3 * math.sqrt(0.1))
        self.assertAlmostEqual(sp.p_touch(100, 90, 0.3, 0.1, "continuous"),
                               2 * sp.N(-abs(z)), places=9)

    def test_the_t_cdf_matches_known_values(self):
        self.assertAlmostEqual(sp.t_cdf(0.0, 4), 0.5, places=9)
        self.assertAlmostEqual(sp.t_cdf(2.132, 4), 0.95, places=3)      # t_{0.95,4}
        self.assertAlmostEqual(sp.t_cdf(-2.776, 4), 0.025, places=3)    # t_{0.975,4}
        self.assertAlmostEqual(sp.t_cdf(1.96, 1e6), sp.N(1.96), places=4)


class TestProfitAfterCosts(unittest.TestCase):
    def test_pop_is_above_p0_for_a_credit_and_zero_for_no_net_credit(self):
        p0 = sp.p_expire_worthless(100, 90, 0.3, 0.1, "put")
        pop = sp.p_profit(100, 90, 0.3, 0.1, "put", credit=1.5, costs_per_share=0.007)
        self.assertGreater(pop, p0)
        self.assertEqual(sp.p_profit(100, 90, 0.3, 0.1, "put", credit=0.005, costs_per_share=0.007), 0.0)

    def test_costs_lower_pop(self):
        a = sp.p_profit(100, 90, 0.3, 0.1, "put", credit=1.0, costs_per_share=0.0)
        b = sp.p_profit(100, 90, 0.3, 0.1, "put", credit=1.0, costs_per_share=0.5)
        self.assertLess(b, a)

    def test_breakeven_sides(self):
        self.assertEqual(sp.breakeven(90, 1.5, "put"), 88.5)
        self.assertEqual(sp.breakeven(110, 1.5, "call"), 111.5)


class TestEmpiricalLibrary(unittest.TestCase):
    def test_a_thin_library_answers_none_not_a_substitute(self):
        lib = sp.standardized_moves(_bars(150), 10)
        self.assertLess(lib["n"], 200)
        self.assertIsNone(sp.p_itm(100, 90, 0.3, 0.1, "put", model="empirical", lib=lib))
        out = sp.contract_probabilities(100, 90, "put", 30, 0.3, 1.0, model="empirical",
                                        lib=lib, paths=False)
        self.assertIsNone(out["p_itm"])
        self.assertIn("insufficient", out)

    def test_the_library_is_sorted_and_labeled(self):
        bars = _bars(500, seed=4)
        a = sp.standardized_moves(bars, 10)
        self.assertEqual(a["z"], sorted(a["z"]))
        self.assertNotEqual(a["n"], 0)
        self.assertEqual(a["h"], 10)

    def test_merge_pools_and_sorts(self):
        a = sp.standardized_moves(_bars(400, seed=1), 5)
        b = sp.standardized_moves(_bars(400, seed=2), 5)
        m = sp.merge_libraries(a, b)
        self.assertEqual(m["n"], a["n"] + b["n"])
        self.assertEqual(m["z"], sorted(m["z"]))

    def test_a_rich_library_gives_sane_tail_rates(self):
        libs = [sp.standardized_moves(_bars(1500, seed=s), 10) for s in range(4)]
        lib = sp.merge_libraries(*libs)
        self.assertGreaterEqual(lib["n"], 200)
        p1 = sp.p_itm(100, 100 * math.exp(-1.0 * 0.3 * math.sqrt(10 / 252)), 0.3, 10 / 252, "put",
                      model="empirical", lib=lib)
        p2 = sp.p_itm(100, 100 * math.exp(-2.0 * 0.3 * math.sqrt(10 / 252)), 0.3, 10 / 252, "put",
                      model="empirical", lib=lib)
        self.assertGreater(p1, p2)
        self.assertLess(p1, 0.35); self.assertGreater(p1, 0.05)


class TestHorizonVolatility(unittest.TestCase):
    def test_the_21_day_bucket_reproduces_expected_rv30(self):
        bars = _bars(420, seed=9)
        cfg = {"global_weights": vf.GLOBAL_WEIGHTS, "anchor_shrink": 0.25, "anchor_window": 252,
               "min_history_bars": 120}
        erv = vf.expected_rv30(bars, cfg)
        got = sp.sigma_for_horizon(bars, 21, cfg)
        self.assertIsNotNone(erv); self.assertIsNotNone(got)
        self.assertAlmostEqual(got["sigma"], erv["erv30"], places=3)

    def test_every_bucket_uses_the_validated_blend_by_default(self):
        bars = _bars(420, seed=9)
        sig = {h: sp.sigma_for_horizon(bars, h)["sigma"] for h in (3, 10, 21, 45)}
        self.assertEqual(len(set(sig.values())), 1, sig)

    def test_a_config_override_can_change_one_bucket(self):
        bars = _bars(420, seed=9)
        base = sp.sigma_for_horizon(bars, 5)["sigma"]
        over = sp.sigma_for_horizon(bars, 5, {"horizon_blends": {"5": {"RV5": 1.0}}})["sigma"]
        self.assertNotEqual(base, over)

    def test_buckets_and_trading_days(self):
        self.assertEqual([sp.horizon_bucket(h) for h in (1, 7, 8, 15, 16, 31, 32, 60)],
                         ["5", "5", "10", "10", "21", "21", "42", "42"])
        self.assertAlmostEqual(sp.trading_days(365), 252.0, places=6)
        self.assertEqual(sp.trading_days(0), 0.25)

    def test_short_history_is_none(self):
        self.assertIsNone(sp.sigma_for_horizon(_bars(50), 21))


class TestPathsAndBounds(unittest.TestCase):
    def test_paths_are_deterministic_and_ordered(self):
        a = sp.profit_path_stats(100, 90, 0.3, 0.36, 45, "put", 1.2, n_paths=800, seed=3)
        b = sp.profit_path_stats(100, 90, 0.3, 0.36, 45, "put", 1.2, n_paths=800, seed=3)
        self.assertEqual(a, b)
        t = a["targets"]
        self.assertGreaterEqual(t["50"]["p_hit"], t["75"]["p_hit"])
        self.assertGreaterEqual(t["75"]["p_hit"], t["90"]["p_hit"])
        self.assertGreaterEqual(t["90"]["p_hit"], a["p_near_zero"])
        self.assertLessEqual(t["50"]["expected_days_if_hit"], t["90"]["expected_days_if_hit"])
        self.assertIn("MODELED", a["basis"])

    def test_paths_touch_agrees_with_the_closed_form_within_monte_carlo_error(self):
        a = sp.profit_path_stats(100, 90, 0.3, 0.30, 45, "put", 1.2, n_paths=4000, seed=5)
        closed_daily = sp.p_touch(100, 90, 0.3, 45 / 365, "daily")
        closed_cont = sp.p_touch(100, 90, 0.3, 45 / 365, "continuous")
        self.assertGreater(a["p_touch_paths"], closed_daily - 0.05)
        self.assertLess(a["p_touch_paths"], closed_cont + 0.05)

    def test_expected_shortfall_worsens_with_a_smaller_tail(self):
        es95 = sp.expected_shortfall(100, 90, 0.3, 0.12, "put", 1.2, q=0.05)
        es99 = sp.expected_shortfall(100, 90, 0.3, 0.12, "put", 1.2, q=0.01)
        self.assertGreater(es99, es95)
        self.assertGreater(es95, 0)

    def test_the_conservative_bound_sits_below_the_point_estimate(self):
        b = sp.conservative_bound(0.85, 40)
        self.assertLess(b["low"], 0.85)
        self.assertGreater(b["high"], 0.85)
        self.assertIsNone(sp.conservative_bound(0.85, 0))
        self.assertIsNone(sp.conservative_bound(None, 40))
        self.assertGreater(sp.conservative_bound(0.85, 200)["low"], b["low"])

    def test_the_full_set_is_labeled_and_coherent(self):
        out = sp.contract_probabilities(100, 90, "put", 45, 0.3, 1.2, 0.007, iv_entry=0.36,
                                        paths=False)
        self.assertEqual(out["monitoring"], "continuous")
        self.assertAlmostEqual(out["p_itm"] + out["p_expire_worthless"], 1.0, places=9)
        self.assertGreaterEqual(out["p_touch"], out["p_itm"])
        self.assertGreater(out["p_profit"], out["p_expire_worthless"])
        self.assertGreaterEqual(out["p_itm_tail_adjusted"], out["p_itm"])
        for k in ("terminal", "touch", "tail", "tail_adjustment"):
            self.assertIn(k, out["basis"])
        self.assertIn("model", out["basis"]["terminal"])
        self.assertNotIn("paths", out)


class TestTheFixtureMatchesTheDefaults(unittest.TestCase):
    def test_the_tail_table_is_the_measured_one(self):
        fx = json.loads(Path(__file__).with_name("fixtures").joinpath("sp_universe_calibration.json").read_text())
        for k, f in sp.TAIL_CORRECTION_DEFAULT.items():
            meas = fx["itm_by_k"][k]["ratio"]
            self.assertAlmostEqual(f, meas, places=2, msg=f"k={k}: code {f} vs fixture {meas}")
        self.assertEqual(fx["model_choice"]["terminal"], "lognormal")
        self.assertIn("continuous", fx["model_choice"]["touch"])
        self.assertGreaterEqual(fx["universe"]["n_symbols"], 50)


if __name__ == "__main__":
    unittest.main()
