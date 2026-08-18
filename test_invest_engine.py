"""Tests for invest_engine — the arithmetic behind the Investment tab.

Two things are pinned hardest here, because the tab makes claims about them
in writing on screen:

  1. The earnings-per-share breakdown RECONCILES. The panel says the three
     contributions add up to the total change; that is asserted, not trusted.
  2. Nothing invents a number. A percentage change off a loss, a P/E on
     negative earnings, an index built on a negative base — each returns None
     with the reason left to the caller to say in words.
"""

import math
import os
import unittest
from datetime import date, timedelta

os.environ.setdefault("JERRY_NO_NET", "1")

import invest_engine as E


class TestSafeArithmetic(unittest.TestCase):
    def test_safe_div_refuses_zero_and_junk(self):
        self.assertIsNone(E.safe_div(1, 0))
        self.assertIsNone(E.safe_div(None, 2))
        self.assertIsNone(E.safe_div("x", 2))
        self.assertAlmostEqual(E.safe_div(10, 4), 2.5)

    def test_pe_is_none_on_negative_earnings(self):
        # A negative P/E is an artifact, not a cheap stock. Printing "-14x"
        # invites exactly the wrong reading, so it is never printed.
        self.assertIsNone(E.price_earnings(10.0, -2.0))
        self.assertIsNone(E.price_earnings(10.0, 0.0))
        self.assertAlmostEqual(E.price_earnings(100.0, 5.0), 20.0)

    def test_market_cap_needs_both_halves_positive(self):
        self.assertIsNone(E.market_cap(None, 1e9))
        self.assertIsNone(E.market_cap(10.0, 0))
        self.assertAlmostEqual(E.market_cap(10.0, 1e9), 1e10)

    def test_yields(self):
        self.assertAlmostEqual(E.earnings_yield(5.0, 100.0), 0.05)
        self.assertAlmostEqual(E.fcf_yield(2e9, 4e10), 0.05)
        self.assertIsNone(E.fcf_yield(2e9, 0))


class TestGrowth(unittest.TestCase):
    def test_ordinary_growth(self):
        g = E.growth(112.0, 100.0)
        self.assertAlmostEqual(g["pct"], 12.0)
        self.assertEqual(g["direction"], "up")
        self.assertEqual(g["note"], "")

    def test_no_percentage_from_a_loss(self):
        # -100 -> -50 is not "+50% growth" and is not "-50%" either. The
        # percentage is withheld and the words describe what happened.
        g = E.growth(-50.0, -100.0)
        self.assertIsNone(g["pct"])
        self.assertEqual(g["direction"], "up")
        self.assertIn("narrowed", g["note"])

    def test_widening_loss_reads_down(self):
        g = E.growth(-150.0, -100.0)
        self.assertIsNone(g["pct"])
        self.assertEqual(g["direction"], "down")
        self.assertIn("widened", g["note"])

    def test_turning_profitable_and_swinging_to_loss(self):
        up = E.growth(5.0, -10.0)
        self.assertIsNone(up["pct"])
        self.assertEqual(up["direction"], "up")
        self.assertIn("Turned profitable", up["note"])
        # A POSITIVE base keeps its percentage even when the result is a
        # loss — that arithmetic is well defined — but it also carries the
        # sentence, because "-150%" on its own reads like a decline rather
        # than a change of sign.
        down = E.growth(-5.0, 10.0)
        self.assertAlmostEqual(down["pct"], -150.0)
        self.assertEqual(down["direction"], "down")
        self.assertIn("Swung to a loss", down["note"])

    def test_zero_base_and_missing_inputs(self):
        self.assertIsNone(E.growth(5.0, 0.0)["pct"])
        g = E.growth(5.0, None)
        self.assertIsNone(g["pct"])
        self.assertEqual(g["direction"], "unknown")


class TestLogDecomposition(unittest.TestCase):
    P = {"revenue": 100.0, "net_income": 10.0, "shares": 50.0}
    C = {"revenue": 120.0, "net_income": 15.0, "shares": 48.0}

    def test_contributions_reconcile_exactly(self):
        d = E.log_decomposition(self.P, self.C)
        self.assertEqual(d["method"], "log")
        total = sum(c["value"] for c in d["contributions"])
        self.assertAlmostEqual(total, d["total"], places=10)

    def test_total_is_the_actual_log_change_in_eps(self):
        d = E.log_decomposition(self.P, self.C)
        expected = math.log((15.0 / 48.0) / (10.0 / 50.0)) * 100.0
        self.assertAlmostEqual(d["total"], expected, places=10)

    def test_buyback_contributes_positively(self):
        # The share count FELL from 50 to 48, which lifts earnings per share.
        d = E.log_decomposition(self.P, self.C)
        share = [c for c in d["contributions"] if c["driver"] == "Share count"][0]
        self.assertGreater(share["value"], 0)

    def test_refuses_when_anything_is_non_positive(self):
        for bad in ({"revenue": 0.0, "net_income": 10.0, "shares": 50.0},
                    {"revenue": 100.0, "net_income": -10.0, "shares": 50.0},
                    {"revenue": 100.0, "net_income": 10.0, "shares": 0.0}):
            self.assertIsNone(E.log_decomposition(bad, self.C))
            self.assertIsNone(E.log_decomposition(self.P, bad))


class TestDollarBridge(unittest.TestCase):
    def test_reconciles_through_a_loss(self):
        prior = {"revenue": 100.0, "net_income": -10.0, "shares": 50.0}
        cur = {"revenue": 120.0, "net_income": 5.0, "shares": 60.0}
        d = E.dollar_bridge(prior, cur)
        self.assertEqual(d["method"], "dollar")
        self.assertAlmostEqual(sum(c["value"] for c in d["contributions"]),
                               d["total"], places=10)
        self.assertAlmostEqual(d["total"], 5.0 / 60.0 - (-10.0 / 50.0), places=10)

    def test_shapley_is_order_independent(self):
        # The whole reason for averaging over orderings: a single sequential
        # walk-down gives a different answer depending on which driver is
        # moved first. The Shapley values must not.
        prior = {"revenue": 90.0, "net_income": -4.0, "shares": 20.0}
        cur = {"revenue": 140.0, "net_income": 22.0, "shares": 33.0}
        a = E.dollar_bridge(prior, cur)
        b = E.dollar_bridge(dict(reversed(list(prior.items()))),
                            dict(reversed(list(cur.items()))))
        for ca, cb in zip(a["contributions"], b["contributions"]):
            self.assertEqual(ca["driver"], cb["driver"])
            self.assertAlmostEqual(ca["value"], cb["value"], places=12)

    def test_pre_revenue_falls_back_to_two_drivers(self):
        # A company with no revenue has no profit margin to split out. It
        # still gets an honest bridge rather than nothing.
        prior = {"revenue": 0.0, "net_income": -10.0, "shares": 5.0}
        cur = {"revenue": 0.0, "net_income": -20.0, "shares": 20.0}
        d = E.dollar_bridge(prior, cur)
        self.assertEqual([c["driver"] for c in d["contributions"]],
                         ["Net income", "Share count"])
        self.assertAlmostEqual(sum(c["value"] for c in d["contributions"]),
                               d["total"], places=10)
        self.assertIn("no profit margin", d["note"])

    def test_refuses_without_a_share_count(self):
        self.assertIsNone(E.dollar_bridge(
            {"revenue": 10.0, "net_income": 1.0, "shares": 0.0},
            {"revenue": 10.0, "net_income": 1.0, "shares": 5.0}))


class TestDecomposeSelection(unittest.TestCase):
    def test_prefers_logs_when_legal(self):
        d = E.decompose({"revenue": 100.0, "net_income": 10.0, "shares": 50.0},
                        {"revenue": 120.0, "net_income": 15.0, "shares": 48.0})
        self.assertTrue(d["available"])
        self.assertEqual(d["method"], "log")
        self.assertTrue(E.reconciles(d))

    def test_falls_back_to_dollars_when_logs_are_invalid(self):
        d = E.decompose({"revenue": 100.0, "net_income": -10.0, "shares": 50.0},
                        {"revenue": 120.0, "net_income": 15.0, "shares": 48.0})
        self.assertEqual(d["method"], "dollar")
        self.assertEqual(d["label"], "Dollar EPS Bridge")
        self.assertTrue(E.reconciles(d))

    def test_warns_but_still_draws_when_reported_eps_differs(self):
        # Realty Income's reported EPS sits 5% below net income over diluted
        # shares (preferred dividends, minority interests). The bridge is
        # still useful; it must say which earnings figure it describes.
        d = E.decompose({"revenue": 100.0, "net_income": 10.0, "shares": 50.0},
                        {"revenue": 120.0, "net_income": 15.0, "shares": 48.0},
                        reported_eps_current=0.25)     # identity gives 0.3125
        self.assertTrue(d["available"])
        self.assertTrue(d["warning"])
        self.assertIn("0.25", d["warning"])
        self.assertFalse(d["identity"]["ok"])

    def test_no_warning_inside_tolerance(self):
        d = E.decompose({"revenue": 100.0, "net_income": 10.0, "shares": 50.0},
                        {"revenue": 120.0, "net_income": 15.0, "shares": 48.0},
                        reported_eps_current=0.3120)
        self.assertEqual(d["warning"], "")

    def test_unavailable_with_a_reason_when_inputs_are_missing(self):
        d = E.decompose({"revenue": None, "net_income": None, "shares": None},
                        {"revenue": 1.0, "net_income": 1.0, "shares": 1.0})
        self.assertFalse(d["available"])
        self.assertIn("missing", d["reason"])

    def test_reconciles_rejects_an_unavailable_result(self):
        self.assertFalse(E.reconciles({"available": False}))
        self.assertFalse(E.reconciles(None))


class TestNormalize(unittest.TestCase):
    def test_indexes_to_100(self):
        out = E.normalize([{"date": "2024-01-01", "value": 50.0},
                           {"date": "2024-06-01", "value": 75.0}])
        self.assertAlmostEqual(out[0]["indexed"], 100.0)
        self.assertAlmostEqual(out[1]["indexed"], 150.0)

    def test_refuses_a_non_positive_base(self):
        # Indexing to 100 off a loss produces a line that inverts every time
        # the sign flips. An empty series plus a note beats a wrong chart.
        self.assertEqual(E.normalize([{"date": "2024-01-01", "value": -3.0},
                                      {"date": "2024-06-01", "value": -1.0}]), [])

    def test_skips_missing_points(self):
        out = E.normalize([{"date": "a", "value": 10.0},
                           {"date": "b", "value": None},
                           {"date": "c", "value": 20.0}])
        self.assertEqual(len(out), 2)




# ══════════════════════════════════════════════════════════════════════════
# PHASE 2
# ══════════════════════════════════════════════════════════════════════════

class TestDistributionMath(unittest.TestCase):
    def test_quantiles_interpolate(self):
        vals = [1, 2, 3, 4, 5]
        self.assertAlmostEqual(E.quantile(vals, 0.5), 3.0)
        self.assertAlmostEqual(E.quantile(vals, 0.10), 1.4)
        self.assertAlmostEqual(E.quantile(vals, 0.90), 4.6)
        self.assertAlmostEqual(E.quantile([7], 0.5), 7.0)
        self.assertIsNone(E.quantile([], 0.5))

    def test_percentile_rank_uses_mid_ranks(self):
        # A value equal to everything sits in the middle, not at an end.
        self.assertAlmostEqual(E.percentile_rank([1, 1, 1], 1), 50.0)
        self.assertAlmostEqual(E.percentile_rank([1, 2, 3, 4], 3), 62.5)
        self.assertAlmostEqual(E.percentile_rank([1, 2, 3, 4], 0), 0.0)
        self.assertIsNone(E.percentile_rank([], 3))

    def test_distribution_reports_the_shape_and_the_count(self):
        d = E.distribution(list(range(1, 101)), 25)
        self.assertEqual(d["n"], 100)
        self.assertAlmostEqual(d["median"], 50.5)
        self.assertAlmostEqual(d["p10"], 10.9)
        self.assertAlmostEqual(d["p90"], 90.1)
        self.assertAlmostEqual(d["percentile"], 24.5)

    def test_rank_within_flips_for_lower_is_better(self):
        peers = [10, 20, 30, 40, 50]
        self.assertGreaterEqual(E.rank_within(45, peers, True), 80)
        self.assertLessEqual(E.rank_within(45, peers, False), 20)

    def test_band_score_is_a_stated_fallback(self):
        self.assertEqual(E.band_score(30.0, [0, 8, 15, 25], True), 90.0)
        self.assertEqual(E.band_score(-1.0, [0, 8, 15, 25], True), 10.0)
        self.assertEqual(E.band_score(30.0, [0, 8, 15, 25], False), 10.0)
        self.assertIsNone(E.band_score(None, [0, 8, 15, 25]))


class TestRegimeShift(unittest.TestCase):
    def _pts(self, first, second, n=200):
        out = []
        for i in range(n):
            d = f"2021-{1 + i // 30:02d}-{1 + i % 28:02d}"
            out.append({"date": d, "value": first if i < n // 2 else second})
        # Dates must increase across the whole span for the split to work.
        base = date(2021, 1, 1)
        for i, p in enumerate(out):
            p["date"] = (base + timedelta(days=i * 7)).isoformat()
        return out

    def test_a_level_change_is_detected(self):
        # A step from 3% to 8% with no noise at all: the earlier period's own
        # spread is zero, which is exactly the case that used to make a shift
        # undetectable until the threshold grew a floor.
        pts = self._pts(3.0, 8.0)
        r = E.regime_shift(pts)
        self.assertTrue(r["available"])
        self.assertTrue(r["shifted"])
        self.assertGreater(abs(r["shift"]), r["threshold"])

    def test_a_stable_range_is_not_a_shift(self):
        import random
        random.seed(7)
        pts = self._pts(0, 0)
        for p in pts:
            p["value"] = 5.0 + random.uniform(-1.0, 1.0)
        r = E.regime_shift(pts)
        self.assertTrue(r["available"])
        self.assertFalse(r["shifted"])

    def test_too_little_history_says_so_instead_of_guessing(self):
        r = E.regime_shift([{"date": "2025-01-01", "value": 5.0}] * 10)
        self.assertFalse(r["available"])
        self.assertIn("too few", r["reason"].lower())


class TestBusinessType(unittest.TestCase):
    def test_sic_codes_classify_the_hard_cases(self):
        # Every code here was read off the live SEC submissions record.
        cases = {"6021": "BANK", "6022": "BANK", "6311": "INSURANCE",
                 "6331": "INSURANCE", "6798": "REIT", "6211": "BROKER",
                 "2911": "CYCLICAL", "1000": "CYCLICAL", "1040": "CYCLICAL",
                 "3571": "STANDARD", "7372": "STANDARD", "5331": "STANDARD"}
        for sic, kind in cases.items():
            self.assertEqual(E.business_type(sic)["type"], kind, sic)

    def test_a_loss_maker_is_classified_as_such(self):
        self.assertEqual(E.business_type("3571", eps_ttm=-2.0)["type"],
                         "UNPROFITABLE")

    def test_a_bank_stays_a_bank_even_at_a_loss(self):
        # Type is about the KIND of business, and a loss-making bank still
        # needs a bank's model rather than a generic one.
        self.assertEqual(E.business_type("6021", eps_ttm=-2.0)["type"], "BANK")

    def test_missing_or_unusable_codes(self):
        self.assertEqual(E.business_type(None)["type"], "UNSUPPORTED")
        self.assertEqual(E.business_type("abc")["type"], "UNSUPPORTED")
        self.assertEqual(E.business_type("3571", ok=False)["type"], "UNSUPPORTED")

    def test_banks_are_refused_the_measures_that_do_not_describe_them(self):
        bank = E.business_type("6021")
        for measure in ("fcf", "leverage", "roic"):
            self.assertFalse(E.allows(bank, measure), measure)
        self.assertTrue(E.allows(bank, "earnings_yield"))
        self.assertIn("Borrowing IS the raw material", bank["note"])

    def test_reits_are_refused_cash_flow_and_leverage(self):
        reit = E.business_type("6798")
        self.assertFalse(E.allows(reit, "fcf"))
        self.assertFalse(E.allows(reit, "leverage"))
        self.assertIn("funds from operations", reit["note"].lower())

    def test_standard_companies_get_everything(self):
        std = E.business_type("3571")
        for measure in ("fcf", "leverage", "roic", "operating_margin"):
            self.assertTrue(E.allows(std, measure), measure)


class TestQualityScoring(unittest.TestCase):
    def test_roic_uses_capital_actually_tied_up(self):
        # 100 of operating profit, 25% tax, 200 equity, 100 net debt
        self.assertAlmostEqual(E.roic(100, 0.25, 200, 100), 25.0)

    def test_roic_refuses_a_non_positive_capital_base(self):
        self.assertIsNone(E.roic(100, 0.25, 50, -200))
        self.assertIsNone(E.roic(None, 0.25, 200, 0))

    def test_effective_tax_rate_is_clamped_and_guarded(self):
        self.assertAlmostEqual(E.effective_tax_rate(21, 100), 0.21)
        self.assertIsNone(E.effective_tax_rate(21, -100))
        self.assertAlmostEqual(E.effective_tax_rate(900, 100), 0.60)

    def test_trend_slope_finds_a_grinding_margin(self):
        pts = [{"date": f"202{i}-01-01", "value": 20.0 - i} for i in range(5)]
        self.assertLess(E.trend_slope(pts), -0.9)
        self.assertIsNone(E.trend_slope(pts[:2]))

    def test_a_component_ranks_against_peers_when_there_are_enough(self):
        c = E.quality_component("roic", 30.0, [5, 10, 15, 20, 25])
        self.assertGreater(c["score"], 80)
        self.assertIn("ranked against 5", c["scored_against"])

    def test_a_component_falls_back_to_bands_and_says_so(self):
        c = E.quality_component("roic", 30.0, [5, 10])
        self.assertEqual(c["score"], 90.0)
        self.assertIn("absolute bands", c["scored_against"])

    def test_dilution_and_stock_comp_score_the_right_way_round(self):
        worse = E.quality_component("share_count_trend", 5.0, [])
        better = E.quality_component("share_count_trend", -3.0, [])
        self.assertLess(worse["score"], better["score"])
        rich = E.quality_component("sbc_pct_revenue", 15.0, [])
        lean = E.quality_component("sbc_pct_revenue", 0.5, [])
        self.assertLess(rich["score"], lean["score"])

    def test_a_disallowed_measure_says_specialized_model_required(self):
        c = E.quality_component("leverage", 2.0, [], allowed=False)
        self.assertIsNone(c["score"])
        self.assertEqual(c["reason"], E.SPECIALIZED)

    def test_a_missing_input_carries_its_reason(self):
        c = E.quality_component("roic", None, [], reason="Not reported.")
        self.assertIsNone(c["score"])
        self.assertEqual(c["reason"], "Not reported.")


class TestDimensionScoring(unittest.TestCase):
    def test_scores_on_what_exists_and_reports_coverage(self):
        comps = [{"key": "a", "score": 80.0}, {"key": "b", "score": 40.0},
                 {"key": "c", "score": None}]
        d = E.score_dimension(comps)
        self.assertAlmostEqual(d["score"], 60.0)
        self.assertEqual(d["coverage"], "2 of 3 inputs available")
        self.assertEqual(d["label"], "ABOVE AVERAGE")

    def test_a_missing_input_is_never_filled_with_a_neutral_fifty(self):
        # Averaging in a 50 for the unknown input would drag 80 to 65 and
        # invent an opinion out of an absence.
        d = E.score_dimension([{"key": "a", "score": 80.0},
                               {"key": "b", "score": 80.0},
                               {"key": "c", "score": None}])
        self.assertAlmostEqual(d["score"], 80.0)

    def test_too_few_inputs_is_not_rated(self):
        d = E.score_dimension([{"key": "a", "score": 80.0}, {"key": "b", "score": None}])
        self.assertIsNone(d["score"])
        self.assertEqual(d["label"], E.NOT_RATED)
        self.assertIn("fewer than the 2", d["reason"])


class TestValuationScore(unittest.TestCase):
    def test_own_history_leads_and_peers_confirm(self):
        v = E.valuation_score(80.0, 40.0)
        self.assertGreater(v["score"], 60.0)     # weighted toward own history
        self.assertLess(v["score"], 80.0)
        self.assertIn("its own history", v["basis"])
        self.assertIn("comparable companies", v["basis"])

    def test_a_regime_shift_halves_the_weight_on_old_history(self):
        plain = E.valuation_score(80.0, 40.0)
        shifted = E.valuation_score(80.0, 40.0, {"shifted": True})
        self.assertLess(shifted["score"], plain["score"])
        self.assertTrue(shifted["regime_shifted"])
        self.assertIn("down-weighted", shifted["basis"])

    def test_peers_alone_still_score(self):
        v = E.valuation_score(None, 70.0)
        self.assertAlmostEqual(v["score"], 70.0)

    def test_neither_is_not_rated_with_a_reason(self):
        v = E.valuation_score(None, None)
        self.assertIsNone(v["score"])
        self.assertIn("nothing to be cheap or expensive against", v["reason"])

    def test_target_price_and_eps_are_plain_arithmetic(self):
        self.assertAlmostEqual(E.price_for_percentile(8.0, 5.0), 160.0)
        self.assertAlmostEqual(E.eps_for_percentile(160.0, 5.0), 8.0)
        self.assertIsNone(E.price_for_percentile(-1.0, 5.0))
        self.assertIsNone(E.price_for_percentile(8.0, 0.0))


class TestRevisions(unittest.TestCase):
    def test_coverage_below_four_is_not_rated(self):
        r = E.revisions_score(40.0, 30.0, analyst_count=3)
        self.assertIsNone(r["score"])
        self.assertEqual(r["label"], E.NOT_RATED)
        self.assertIn("fewer than the 4", r["reason"])

    def test_unknown_coverage_is_not_rated(self):
        r = E.revisions_score(40.0, 30.0, analyst_count=None)
        self.assertEqual(r["label"], E.NOT_RATED)
        self.assertIn("not available", r["reason"])

    def test_enough_coverage_scores_and_orients_correctly(self):
        up = E.revisions_score(60.0, 40.0, analyst_count=20)
        down = E.revisions_score(-60.0, -40.0, analyst_count=20)
        self.assertGreater(up["score"], 70)
        self.assertLess(down["score"], 30)

    def test_missing_figures_with_good_coverage_still_not_rated(self):
        r = E.revisions_score(None, None, analyst_count=20)
        self.assertIsNone(r["score"])
        self.assertIn("No revision figures", r["reason"])


class TestUnderreaction(unittest.TestCase):
    def test_intensity_is_scaled_by_price_not_by_the_old_estimate(self):
        # A one-cent raise on a two-cent base is +50% and means nothing.
        # Per dollar of a $100 share price it is 0.01, which is the point.
        self.assertAlmostEqual(E.revision_intensity(0.03, 0.02, 100.0), 0.01)
        self.assertIsNone(E.revision_intensity(0.03, None, 100.0))
        self.assertIsNone(E.revision_intensity(0.03, 0.02, 0))

    def test_zscore_needs_a_real_cross_section(self):
        self.assertIsNone(E.zscore(5.0, [1, 2]))
        self.assertIsNone(E.zscore(5.0, [3, 3, 3, 3, 3]))
        self.assertGreater(E.zscore(10.0, [1, 2, 3, 4, 5]), 2.0)

    def test_underreaction_is_the_difference_and_stays_experimental(self):
        u = E.underreaction(1.5, 0.4)
        self.assertAlmostEqual(u["score"], 1.1)
        self.assertTrue(u["experimental"])
        self.assertIn("EXPERIMENTAL", u["note"])

    def test_underreaction_refuses_half_an_input(self):
        u = E.underreaction(1.5, None)
        self.assertFalse(u["available"])
        self.assertTrue(u["experimental"])


class TestValueTrap(unittest.TestCase):
    def test_three_signals_is_high_risk(self):
        t = E.value_trap({k: {"active": True, "detail": "x"} for k in
                          ("estimates_falling", "revenue_deteriorating",
                           "margin_deteriorating")})
        self.assertEqual(t["level"], "HIGH RISK")
        self.assertEqual(t["n_active"], 3)

    def test_one_signal_is_moderate(self):
        t = E.value_trap({"estimates_falling": {"active": True},
                          "revenue_deteriorating": {"active": False},
                          "margin_deteriorating": {"active": False}})
        self.assertEqual(t["level"], "MODERATE RISK")

    def test_measured_and_clear_is_low_risk(self):
        t = E.value_trap({k: {"active": False} for k in
                          ("estimates_falling", "revenue_deteriorating",
                           "margin_deteriorating", "fcf_deteriorating")})
        self.assertEqual(t["level"], "LOW RISK")

    def test_nothing_measurable_is_not_rated_not_low_risk(self):
        # Silence is not evidence of health.
        t = E.value_trap({})
        self.assertEqual(t["level"], E.NOT_RATED)
        self.assertEqual(len(t["unknown"]), len(E.TRAP_SIGNALS))
        self.assertIn("not evidence", t["reason"])

    def test_unmeasurable_signals_are_counted_separately(self):
        t = E.value_trap({"estimates_falling": {"active": True},
                          "revenue_deteriorating": None})
        self.assertEqual(t["n_active"], 1)
        self.assertTrue(any(u["key"] == "revenue_deteriorating"
                            for u in t["unknown"]))

    def test_thresholds_come_from_configuration(self):
        sig = {k: {"active": True} for k in ("estimates_falling",
                                             "revenue_deteriorating")}
        self.assertEqual(E.value_trap(sig)["level"], "MODERATE RISK")
        self.assertEqual(E.value_trap(sig, {"trap_high_signals": 2})["level"],
                         "HIGH RISK")

    def test_deteriorating_reads_the_direction(self):
        self.assertTrue(E.deteriorating(5.0, 10.0))
        self.assertFalse(E.deteriorating(10.0, 5.0))
        self.assertTrue(E.deteriorating(10.0, 5.0, worse_when_lower=False))
        self.assertIsNone(E.deteriorating(None, 5.0))
        self.assertFalse(E.deteriorating(9.0, 10.0, min_move=3.0))


class TestEarningsCycle(unittest.TestCase):
    def test_the_four_states(self):
        self.assertEqual(E.earnings_cycle("2026-08-18",
                                          next_date="2026-08-25")["state"],
                         "PRE-EARNINGS")
        self.assertEqual(E.earnings_cycle("2026-08-18",
                                          last_date="2026-08-05")["state"],
                         "POST-EARNINGS FRESH")
        self.assertEqual(E.earnings_cycle("2026-08-18", last_date="2026-06-01",
                                          next_date="2026-10-01")["state"],
                         "NORMAL")
        self.assertEqual(E.earnings_cycle("2026-08-18",
                                          last_date="2026-01-05")["state"],
                         "STALE")

    def test_no_dates_is_unknown_not_normal(self):
        self.assertEqual(E.earnings_cycle("2026-08-18")["state"], "UNKNOWN")

    def test_pre_earnings_wins_over_fresh(self):
        # Reported three weeks ago AND reports again next week: the next one
        # is what matters for trusting today's numbers.
        c = E.earnings_cycle("2026-08-18", next_date="2026-08-24",
                             last_date="2026-08-01")
        self.assertEqual(c["state"], "PRE-EARNINGS")

    def test_windows_are_configurable(self):
        c = E.earnings_cycle("2026-08-18", next_date="2026-09-10", pre_days=30)
        self.assertEqual(c["state"], "PRE-EARNINGS")


class TestDrawdowns(unittest.TestCase):
    def _bars(self):
        bars, price = [], 100.0
        base = date(2019, 1, 1)
        for i in range(1500):
            if 400 <= i < 460:
                price *= 0.98            # a crash
            elif 460 <= i < 700:
                price *= 1.005
            else:
                price *= 1.0005
            bars.append({"date": (base + timedelta(days=i)).isoformat(),
                         "close": price})
        return bars

    def test_finds_the_worst_fall_and_dates_it(self):
        d = E.drawdowns(self._bars())
        self.assertTrue(d["available"])
        self.assertLess(d["max"]["pct"], -40)
        self.assertLess(d["max"]["peak_date"], d["max"]["trough_date"])

    def test_named_stress_windows_appear_when_history_reaches_them(self):
        d = E.drawdowns(self._bars())
        labels = [w["label"] for w in d["windows"]]
        self.assertIn("2020 crash", labels)

    def test_too_little_history_says_so(self):
        d = E.drawdowns([{"date": "2026-01-01", "close": 10.0}] * 5)
        self.assertFalse(d["available"])
        self.assertIn("too few", d["reason"])


class TestPhase2Verdict(unittest.TestCase):
    BASE = {
        "price": 100.0, "eps_ttm": 5.0, "eps_forward": 5.5,
        "earnings_yield_pct": 5.0, "target_yield_pct": 4.0,
        "business_type": E.business_type("3571"),
        "quality": {"score": 75.0, "label": "ABOVE AVERAGE",
                    "coverage": "6 of 6 inputs available"},
        "growth": {"score": 65.0, "label": "ABOVE AVERAGE"},
        "valuation": {"score": 70.0, "label": "ABOVE AVERAGE",
                      "self_percentile": 72.0, "peer_percentile": 66.0},
        "revisions": {"score": 60.0, "label": "ABOVE AVERAGE",
                      "analyst_count": 25},
        "value_trap": {"level": "LOW RISK", "active": []},
    }

    def test_good_business_at_a_cheap_price_is_attractive(self):
        self.assertEqual(E.verdict(self.BASE)["verdict"], "ATTRACTIVE")

    def test_a_great_business_is_not_marked_expensive_by_a_universal_rule(self):
        # The Phase 1 failure this replaces: a high-quality company on a P/E
        # above ~15 was automatically WAIT. Here a 3% earnings yield is fine
        # so long as it is cheap FOR THIS COMPANY.
        rich = {**self.BASE, "price": 300.0, "earnings_yield_pct": 1.7,
                "valuation": {"score": 70.0, "self_percentile": 72.0,
                              "peer_percentile": 66.0}}
        self.assertEqual(E.verdict(rich)["verdict"], "ATTRACTIVE")

    def test_expensive_against_its_own_history_is_wait_with_a_target(self):
        v = E.verdict({**self.BASE,
                       "valuation": {"score": 20.0, "self_percentile": 18.0,
                                     "peer_percentile": 25.0}})
        self.assertEqual(v["verdict"], "WAIT")
        line = " ".join(v["what_would_change"])
        self.assertIn("18th percentile", line)
        self.assertIn("$125.00", line)          # 5.00 / 4% target yield
        self.assertIn("$4.00", line)            # 100 * 4%

    def test_high_trap_risk_overrides_cheapness(self):
        v = E.verdict({**self.BASE,
                       "valuation": {"score": 90.0, "self_percentile": 95.0},
                       "value_trap": {"level": "HIGH RISK", "active": [
                           {"key": "estimates_falling",
                            "label": "Forward earnings estimates are being cut"},
                           {"key": "revenue_deteriorating",
                            "label": "Revenue growth is deteriorating"},
                           {"key": "margin_deteriorating",
                            "label": "Operating margins are narrowing"}]}})
        self.assertEqual(v["verdict"], "AVOID")
        self.assertTrue(v["value_trap"])
        self.assertIn("exactly the pattern a value trap makes",
                      " ".join(v["reasons"]))

    def test_a_loss_maker_is_avoided(self):
        v = E.verdict({**self.BASE, "eps_ttm": -1.0, "eps_forward": None})
        self.assertEqual(v["verdict"], "AVOID")
        self.assertIn("losing money", v["reasons"][0])

    def test_banks_get_a_named_refusal_not_a_number(self):
        v = E.verdict({**self.BASE, "business_type": E.business_type("6021")})
        self.assertEqual(v["verdict"], E.SPECIALIZED)
        self.assertIn("Bank or lender", v["reasons"][0])
        self.assertTrue(v["what_would_change"])

    def test_reits_and_insurers_too(self):
        for sic in ("6798", "6311", "6211"):
            v = E.verdict({**self.BASE, "business_type": E.business_type(sic)})
            self.assertEqual(v["verdict"], E.SPECIALIZED, sic)

    def test_a_built_specialized_model_is_pointed_at_not_denied(self):
        """The generic scorecard still refuses, but it no longer claims the
        model does not exist when the model has just filled a panel."""
        for sic, block, panel in (("6021", "bank", "lender measures"),
                                  ("6798", "reit", "property trust measures"),
                                  ("6311", "insurance", "insurer measures"),
                                  ("6211", "broker", "broker measures")):
            v = E.verdict({**self.BASE, "business_type": E.business_type(sic),
                           block: {"available": True}})
            self.assertEqual(v["verdict"], E.SPECIALIZED, sic)
            said = " ".join(v["what_would_change"])
            self.assertIn(panel, said, sic)
            self.assertNotIn("cannot run", said, sic)

    def test_a_specialized_model_that_could_not_run_still_says_so(self):
        for sic, block in (("6311", "insurance"), ("6211", "broker")):
            v = E.verdict({**self.BASE, "business_type": E.business_type(sic),
                           block: {"available": False}})
            said = " ".join(v["what_would_change"])
            self.assertIn("cannot run on this company's filings", said, sic)
            self.assertNotIn("measures above", said, sic)

    def test_no_price_or_no_earnings_is_insufficient_data(self):
        self.assertEqual(E.verdict({"price": None})["verdict"],
                         "INSUFFICIENT DATA")
        self.assertEqual(
            E.verdict({**self.BASE, "eps_ttm": None,
                       "eps_forward": None})["verdict"], "INSUFFICIENT DATA")

    def test_an_unclassifiable_filer_is_insufficient_data(self):
        v = E.verdict({**self.BASE, "business_type": E.business_type(None)})
        self.assertEqual(v["verdict"], "INSUFFICIENT DATA")

    def test_no_valuation_anchor_is_watch_and_says_why(self):
        v = E.verdict({**self.BASE,
                       "valuation": {"score": None, "self_percentile": None,
                                     "peer_percentile": None}})
        self.assertEqual(v["verdict"], "WATCH")
        self.assertIn("no answer to whether the price is reasonable",
                      " ".join(v["what_would_change"]))

    def test_weak_quality_stops_attractive_even_when_cheap(self):
        v = E.verdict({**self.BASE, "quality": {"score": 20.0, "label": "WEAK",
                                                "coverage": "6 of 6"}})
        self.assertIn(v["verdict"], ("WATCH", "WAIT"))
        self.assertNotEqual(v["verdict"], "ATTRACTIVE")

    def test_strong_growth_cannot_hide_an_expensive_price(self):
        # The four vectors are independent on purpose: no amount of growth
        # turns an expensive valuation into ATTRACTIVE.
        v = E.verdict({**self.BASE, "growth": {"score": 99.0, "label": "STRONG"},
                       "valuation": {"score": 10.0, "self_percentile": 8.0}})
        self.assertEqual(v["verdict"], "WAIT")

    def test_thresholds_come_from_configuration(self):
        strict = E.verdict(self.BASE, {"attractive_valuation_score": 95.0})
        self.assertNotEqual(strict["verdict"], "ATTRACTIVE")

    def test_every_verdict_is_one_of_the_six_words(self):
        for snap in ({}, {"price": 1.0}, self.BASE,
                     {**self.BASE, "eps_ttm": -1.0, "eps_forward": None},
                     {**self.BASE, "business_type": E.business_type("6021")}):
            self.assertIn(E.verdict(snap)["verdict"], E.VERDICTS)

    def test_every_wait_or_avoid_states_what_would_change(self):
        for snap in ({**self.BASE, "valuation": {"score": 10.0,
                                                 "self_percentile": 8.0}},
                     {**self.BASE, "eps_ttm": -1.0, "eps_forward": None}):
            v = E.verdict(snap)
            self.assertIn(v["verdict"], ("WAIT", "AVOID"))
            self.assertTrue(v["what_would_change"], v["verdict"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
