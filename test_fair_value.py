"""Tests for fair_value — Bear/Base/Bull, the buy zone, the return bridge and
the reverse discounted cash flow.

Four claims the tab makes in writing are asserted here rather than trusted:

  1. Methods are NOT averaged. The base value IS one method's value, and
     methods that disagree widen the range instead of being blended.
  2. Lower confidence LOWERS the price we will pay. This is the direction the
     obvious formula gets wrong, so it is pinned with an explicit table.
  3. The expected-return bars RECONCILE. The panel says the three
     contributions add up to the total; that is asserted.
  4. Basis never mixes. A method that would have to multiply an adjusted
     forward estimate by a GAAP trailing multiple is not offered at all.
"""

import math
import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import fair_value as FV


def yields(lo=3.0, hi=5.0, n=1250):
    """A synthetic earnings-yield history spread evenly between two levels."""
    return [lo + (hi - lo) * (i % 100) / 99.0 for i in range(n)]


class TestQuantiles(unittest.TestCase):
    def test_quantile_interpolates_and_survives_junk(self):
        self.assertEqual(FV.quantile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertAlmostEqual(FV.quantile([1, 2], 0.5), 1.5)
        self.assertIsNone(FV.quantile([], 0.5))
        self.assertIsNone(FV.quantile([None, "x"], 0.5))
        self.assertEqual(FV.quantile([7], 0.9), 7)


class TestSelfHistoryMethod(unittest.TestCase):
    def test_high_yield_percentile_is_the_pessimistic_price(self):
        m = FV.method_self_history(10.0, yields(4.0, 8.0))
        self.assertTrue(m["available"])
        # A high yield is a CHEAP price, so bear < base < bull in DOLLARS.
        self.assertLess(m["bear"], m["base"])
        self.assertLess(m["base"], m["bull"])
        self.assertGreater(m["detail"]["yield_bear_pct"],
                           m["detail"]["yield_bull_pct"])

    def test_refuses_negative_earnings(self):
        m = FV.method_self_history(-1.0, yields())
        self.assertFalse(m["available"])
        self.assertIn("negative", m["reason"])

    def test_refuses_a_short_history(self):
        m = FV.method_self_history(10.0, yields(n=100))
        self.assertFalse(m["available"])
        self.assertIn("100", m["reason"])

    def test_regime_shift_lowers_the_methods_confidence(self):
        a = FV.method_self_history(10.0, yields())
        b = FV.method_self_history(10.0, yields(), regime_shifted=True)
        self.assertLess(b["confidence_rank"], a["confidence_rank"])
        self.assertTrue(b["detail"]["regime_shifted"])


class TestPeerMethods(unittest.TestCase):
    def test_trailing_peers_use_the_aggregate_as_the_base(self):
        m = FV.method_peers_trailing(5.0, [10, 12, 15, 18, 20, 25],
                                     aggregate_pe=16.0, level="DIRECT PEERS")
        self.assertTrue(m["available"])
        self.assertAlmostEqual(m["base"], 5.0 * 16.0)
        self.assertTrue(m["detail"]["aggregate_used"])

    def test_broad_benchmark_is_refused(self):
        m = FV.method_peers_trailing(5.0, [10, 12, 15, 18, 20, 25],
                                     aggregate_pe=16.0, level="BROAD BENCHMARK")
        self.assertFalse(m["available"])
        self.assertIn("broad market benchmark", m["reason"])

    def test_too_few_peers_is_refused_with_the_count(self):
        m = FV.method_peers_trailing(5.0, [10, 12], level="INDUSTRY")
        self.assertFalse(m["available"])
        self.assertIn("2 comparable", m["reason"])

    def test_forward_method_refuses_rather_than_borrowing_trailing_multiples(self):
        """The basis rule, pinned. Peer FORWARD multiples do not exist for
        free, and the method must not quietly reach for trailing ones."""
        m = FV.method_peers_forward(6.0, [], level="DIRECT PEERS")
        self.assertFalse(m["available"])
        self.assertIn("mix", m["reason"])
        self.assertIn("forward", m["basis"].lower())


class TestFcfMethod(unittest.TestCase):
    def test_normalize_takes_the_median_not_the_last(self):
        n = FV.normalize_fcf([100, 105, 110, 90, 400])
        self.assertTrue(n["available"])
        self.assertEqual(n["value"], 105)

    def test_normalize_refuses_a_negative_median(self):
        n = FV.normalize_fcf([-10, -20, -30, -40, -50])
        self.assertFalse(n["available"])
        self.assertIn("not positive", n["reason"])

    def test_normalize_refuses_a_thin_history(self):
        n = FV.normalize_fcf([100, 110])
        self.assertFalse(n["available"])
        self.assertIn("2 trailing", n["reason"])

    def test_per_share_value_divides_by_the_share_count(self):
        m = FV.method_fcf(1_000_000.0, 100_000.0, yields(4.0, 6.0))
        self.assertTrue(m["available"])
        # value = normalized cash flow ÷ the median yield ÷ the share count
        y = m["detail"]["yield_base_pct"]
        self.assertAlmostEqual(m["base"], 1_000_000.0 / (y / 100.0) / 100_000.0)
        self.assertAlmostEqual(m["base"], 200.0, delta=3.0)


class TestDisagreementAndConfidence(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(FV.confidence_for(0.10, 2)["level"], "HIGH")
        self.assertEqual(FV.confidence_for(0.40, 2)["level"], "MODERATE")
        self.assertEqual(FV.confidence_for(0.80, 3)["level"], "LOW")
        self.assertEqual(FV.confidence_for(1.50, 3)["level"], "UNRELIABLE")

    def test_one_method_alone_is_never_high(self):
        c = FV.confidence_for(None, 1)
        self.assertEqual(c["level"], "MODERATE")
        self.assertIn("silence is not agreement", c["reason"])

    def test_no_method_is_unreliable(self):
        self.assertEqual(FV.confidence_for(None, 0)["level"], "UNRELIABLE")

    def test_disagreement_is_measured_off_the_lowest(self):
        self.assertAlmostEqual(FV.disagreement([100, 150]), 0.5)
        self.assertIsNone(FV.disagreement([100]))


class TestFairValueCombination(unittest.TestCase):
    def methods(self, bases=(100.0, 110.0)):
        out = []
        for i, b in enumerate(bases):
            out.append(FV._method(f"m{i}", f"Method {i}", "test basis",
                                  bear=b * 0.8, base=b, bull=b * 1.3,
                                  n=1000, rank=1.0 + i))
        return out

    def test_base_is_one_methods_value_not_an_average(self):
        ms = self.methods((100.0, 200.0))
        out = FV.fair_value(ms, price=90.0)
        self.assertIn(out["base"], (100.0, 200.0))
        self.assertNotAlmostEqual(out["base"], 150.0)
        # The highest-ranked method wins, and it is named.
        self.assertEqual(out["base_method"], "m1")

    def test_disagreement_widens_the_range(self):
        narrow = FV.fair_value(self.methods((100.0, 105.0)), price=90.0)
        wide = FV.fair_value(self.methods((100.0, 300.0)), price=90.0)
        self.assertGreater(wide["bull"] - wide["bear"],
                           narrow["bull"] - narrow["bear"])

    def test_lower_confidence_lowers_the_price_we_will_pay(self):
        """The direction the obvious formula gets wrong."""
        bear, base = 220.0, 300.0
        got = {}
        for level, credit in FV.DEFAULTS["confidence_credit"].items():
            got[level] = bear + credit * (base - bear)
        self.assertAlmostEqual(got["HIGH"], 300.0)
        self.assertAlmostEqual(got["UNRELIABLE"], 220.0)
        self.assertGreater(got["HIGH"], got["MODERATE"])
        self.assertGreater(got["MODERATE"], got["LOW"])
        self.assertGreater(got["LOW"], got["UNRELIABLE"])

    def test_buy_zone_falls_when_confidence_falls(self):
        high = FV.fair_value(self.methods((100.0, 105.0)), price=90.0)
        low = FV.fair_value(self.methods((100.0, 260.0)), price=90.0)
        self.assertEqual(high["confidence_level"], "HIGH")
        self.assertIn(low["confidence_level"], ("LOW", "UNRELIABLE"))
        self.assertLess(low["buy_zone"], high["buy_zone"])

    def test_buy_zone_applies_the_margin_of_safety(self):
        out = FV.fair_value(self.methods((100.0, 105.0)), price=90.0)
        self.assertAlmostEqual(out["buy_zone"],
                               out["credited"] * (1 - out["margin_of_safety"]))

    def test_premium_to_buy_zone_is_signed_the_readable_way(self):
        out = FV.fair_value(self.methods((100.0, 105.0)), price=200.0)
        self.assertGreater(out["premium_to_buy_zone_pct"], 0)
        cheap = FV.fair_value(self.methods((100.0, 105.0)), price=10.0)
        self.assertLess(cheap["premium_to_buy_zone_pct"], 0)

    def test_specialized_business_types_are_refused(self):
        for kind in ("BANK", "INSURANCE", "BROKER", "REIT"):
            out = FV.fair_value(self.methods(), price=90.0,
                                business_type={"type": kind, "note": "x"})
            self.assertFalse(out["available"])
            self.assertEqual(out["verdict"], FV.SPECIALIZED)

    def test_unprofitable_is_refused_with_its_own_reason(self):
        out = FV.fair_value(self.methods(), price=90.0,
                            business_type={"type": "UNPROFITABLE"})
        self.assertFalse(out["available"])
        self.assertIn("not profitable", out["reason"])

    def test_no_valid_method_is_insufficient_data(self):
        bad = [FV._method("m", "M", "b", reason="nope")]
        out = FV.fair_value(bad, price=90.0)
        self.assertFalse(out["available"])
        self.assertEqual(out["verdict"], "INSUFFICIENT DATA")


class TestGrowthAndMultipleScenarios(unittest.TestCase):
    def test_percentiles_are_ordered_and_clamped(self):
        g = FV.growth_scenarios([-80, -50, 0, 10, 20, 90, 500])
        self.assertTrue(g["available"])
        self.assertLessEqual(g["bear"], g["base"])
        self.assertLessEqual(g["base"], g["bull"])
        self.assertGreaterEqual(g["bear"], g["floor_pct"])
        self.assertLessEqual(g["bull"], g["cap_pct"])

    def test_clamped_scenarios_are_named(self):
        g = FV.growth_scenarios([-90, -85, -80, 200, 300, 400])
        self.assertTrue(set(g["clamped"]) & {"bear", "bull"})

    def test_thin_history_is_refused(self):
        g = FV.growth_scenarios([5, 6])
        self.assertFalse(g["available"])
        self.assertIn("2 year-over-year", g["reason"])

    def test_multiples_fall_back_to_the_peer_group_and_say_so(self):
        m = FV.multiple_scenarios([20, 21], fallback=18.0)
        self.assertTrue(m["available"])
        self.assertEqual(m["bear"], 18.0)
        self.assertIn("peer group", m["note"])

    def test_multiples_refuse_when_there_is_no_fallback_either(self):
        m = FV.multiple_scenarios([20, 21])
        self.assertFalse(m["available"])


class TestDividendsAndScenarioPaths(unittest.TestCase):
    def test_dividends_are_quarterly_and_compounded(self):
        d = FV.dividend_future_value(4.0, 0.0, 1.0, 0.0)
        self.assertEqual(d["n_payments"], 4)
        self.assertAlmostEqual(d["value"], 4.0)

    def test_dividends_earn_the_stated_rate(self):
        flat = FV.dividend_future_value(4.0, 0.0, 3.0, 0.0)["value"]
        earning = FV.dividend_future_value(4.0, 0.0, 3.0, 5.0)["value"]
        self.assertGreater(earning, flat)

    def test_no_dividend_is_zero_not_missing(self):
        d = FV.dividend_future_value(0.0, 5.0, 3.0, 4.0)
        self.assertEqual(d["value"], 0.0)
        self.assertIn("no dividend", d["reason"])

    def test_a_fractional_horizon_pays_the_right_number_of_dividends(self):
        d = FV.dividend_future_value(4.0, 0.0, 0.5, 0.0)
        self.assertEqual(d["n_payments"], 2)

    def test_contributions_reconcile_exactly(self):
        p = FV.scenario_path(100.0, 5.0, 10.0, 25.0, 3.0, dps_ttm=2.0,
                             rate_pct=4.0)
        self.assertTrue(p["available"])
        total = sum(c["value"] for c in p["contributions"])
        expected = math.log(p["terminal_wealth"] / 100.0) / 3.0 * 100.0
        self.assertAlmostEqual(total, expected, places=9)

    def test_terminal_wealth_is_price_plus_dividends_not_a_yield_addition(self):
        p = FV.scenario_path(100.0, 5.0, 10.0, 25.0, 3.0, dps_ttm=2.0,
                             rate_pct=4.0)
        self.assertAlmostEqual(p["terminal_wealth"],
                               p["price_end"] + p["dividends"]["value"])
        self.assertGreater(p["total_cagr_pct"], p["price_cagr_pct"])

    def test_multiple_reversion_scales_with_the_horizon(self):
        near = FV.scenario_path(100.0, 5.0, 0.0, 10.0, 0.3, reversion_years=3.0)
        far = FV.scenario_path(100.0, 5.0, 0.0, 10.0, 3.0, reversion_years=3.0)
        # Today's multiple is 20; the target is 10. A short horizon should only
        # travel part of the way there.
        self.assertGreater(near["multiple_end"], far["multiple_end"])
        self.assertAlmostEqual(far["multiple_end"], 10.0)

    def test_a_scenario_needs_positive_earnings(self):
        p = FV.scenario_path(100.0, -1.0, 10.0, 25.0, 3.0)
        self.assertFalse(p["available"])


class TestExpectedReturn(unittest.TestCase):
    def build(self, **kw):
        g = FV.growth_scenarios([2, 5, 8, 11, 14, 17, 20])
        m = FV.multiple_scenarios([18 + (i % 20) * 0.2 for i in range(400)])
        return FV.expected_return(100.0, 5.0, g, m, years=3.0, dps_ttm=2.0,
                                  rate_pct=4.0, **kw)

    def test_all_three_scenarios_and_a_weighted_result(self):
        e = self.build()
        self.assertTrue(e["available"])
        for s in ("bear", "base", "bull"):
            self.assertTrue(e["scenarios"][s]["available"])
        self.assertIsNotNone(e["weighted_total_cagr_pct"])

    def test_weights_normalize(self):
        p = FV.scenario_probabilities(override={"bear": 2, "base": 4, "bull": 2})
        self.assertAlmostEqual(sum(p.values()), 1.0)
        self.assertAlmostEqual(p["base"], 0.5)

    def test_negative_weights_are_floored_not_trusted(self):
        p = FV.scenario_probabilities(override={"bear": -1, "base": 1, "bull": 1})
        self.assertAlmostEqual(p["bear"], 0.0)
        self.assertAlmostEqual(sum(p.values()), 1.0)

    def test_unavailable_growth_blocks_the_bridge(self):
        e = FV.expected_return(100.0, 5.0, {"available": False, "reason": "thin"},
                               {"available": True}, years=3.0)
        self.assertFalse(e["available"])
        self.assertEqual(e["reason"], "thin")


class TestReverseDcf(unittest.TestCase):
    def test_round_trip(self):
        ev = FV.dcf_value(100.0, 0.08, 5, 0.09, 0.03)
        got = FV.implied_growth(ev, 100.0, 5, 9.0, 3.0)
        self.assertTrue(got["available"])
        self.assertAlmostEqual(got["growth_pct"], 8.0, places=3)

    def test_a_higher_price_implies_higher_growth(self):
        a = FV.implied_growth(2000.0, 100.0, 5, 9.0, 3.0)["growth_pct"]
        b = FV.implied_growth(3000.0, 100.0, 5, 9.0, 3.0)["growth_pct"]
        self.assertGreater(b, a)

    def test_terminal_growth_must_be_below_the_discount_rate(self):
        out = FV.implied_growth(2000.0, 100.0, 5, 3.0, 3.0)
        self.assertFalse(out["available"])
        self.assertIn("must exceed", out["reason"])

    def test_non_positive_cash_flow_is_refused(self):
        out = FV.implied_growth(2000.0, -5.0, 5, 9.0, 3.0)
        self.assertFalse(out["available"])
        self.assertIn("not positive", out["reason"])

    def test_out_of_range_is_named_rather_than_clamped(self):
        low = FV.implied_growth(1.0, 100.0, 5, 9.0, 3.0)
        self.assertFalse(low["available"])
        self.assertEqual(low["bounded"], "below")
        high = FV.implied_growth(10_000_000.0, 1.0, 5, 9.0, 3.0)
        self.assertFalse(high["available"])
        self.assertEqual(high["bounded"], "above")

    def test_the_solver_is_bracketed_not_newton(self):
        got = FV.implied_growth(2000.0, 100.0, 5, 9.0, 3.0)
        self.assertIn("bisection", got["method"])

    def test_sensitivity_grid_shape_and_monotonicity(self):
        grid = FV.implied_growth_grid(2000.0, 100.0, 5, 9.0)
        self.assertTrue(grid["available"])
        self.assertEqual(len(grid["cells"]), 3)
        self.assertEqual(len(grid["cells"][0]), 3)
        # A higher discount rate needs more growth to justify the same price.
        self.assertLess(grid["cells"][0][1]["growth_pct"],
                        grid["cells"][2][1]["growth_pct"])
        self.assertLessEqual(grid["min_pct"], grid["max_pct"])

    def test_discount_rate_is_a_stated_assumption(self):
        d = FV.discount_rate(4.5)
        self.assertTrue(d["available"])
        self.assertAlmostEqual(d["pct"], 4.5 + FV.DEFAULTS["equity_risk_premium_pct"])
        self.assertIn("not a computed cost of capital", d["basis"])

    def test_discount_rate_refuses_without_a_treasury_yield(self):
        self.assertFalse(FV.discount_rate(None)["available"])

    def test_expectations_gap(self):
        g = FV.expectations_gap(18.0, 6.0)
        self.assertTrue(g["available"])
        self.assertAlmostEqual(g["gap_pp"], 12.0)
        self.assertFalse(FV.expectations_gap(18.0, None)["available"])


class TestCagr(unittest.TestCase):
    def test_cagr(self):
        self.assertAlmostEqual(FV.cagr(100, 121, 2), 10.0)
        self.assertIsNone(FV.cagr(-100, 121, 2))
        self.assertIsNone(FV.cagr(100, 121, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
