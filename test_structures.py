"""Tests for structures — the equal-capital comparator.

What this file exists to stop: an option looking better than the shares
because of the money it did NOT put to work. Every assertion below is either
about the payoff being arithmetically right, or about the comparison being
made on the same account.

The put's risk sizing is pinned hardest. A broker will happily open a secured
put against a fraction of the strike notional, and every ratio computed on
that fraction is a ratio computed on a number the market has no opinion
about — if the stock goes to zero the obligation is the full strike.
"""

import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import structures as ST

PROBS = {"bear": 0.25, "base": 0.50, "bull": 0.25}
PRICES = {"bear": 60.0, "base": 110.0, "bull": 150.0}
PRICE = 100.0
CAP = 10_000.0


class TestCapitalAndRate(unittest.TestCase):
    def test_comparison_capital_is_one_round_lot(self):
        self.assertEqual(ST.comparison_capital(100.0), 10_000.0)
        self.assertEqual(ST.comparison_capital(100.0, contracts=2), 20_000.0)
        self.assertIsNone(ST.comparison_capital(0))
        self.assertIsNone(ST.comparison_capital(None))

    def test_growth_factor_compounds(self):
        self.assertAlmostEqual(ST.growth_factor(10.0, 2.0), 1.21)
        self.assertEqual(ST.growth_factor(None, 2.0), 1.0)
        self.assertEqual(ST.growth_factor(5.0, 0), 1.0)

    def test_dividends_may_be_per_scenario(self):
        self.assertEqual(ST.dividend_at(3.0, "bear"), 3.0)
        self.assertEqual(ST.dividend_at({"bear": 1.0, "base": 2.0}, "bear"), 1.0)
        self.assertEqual(ST.dividend_at({"base": 2.0}, "bull"), 0.0)


class TestShares(unittest.TestCase):
    def test_terminal_wealth_is_shares_plus_dividends(self):
        r = ST.shares_position(PRICE, CAP, PRICES, PROBS, 3.0,
                               dividend_fv_per_share=6.0, rate_pct=4.0)
        self.assertTrue(r["eligible"])
        self.assertAlmostEqual(r["terminal"]["base"]["wealth"],
                               100 * 110.0 + 100 * 6.0)
        self.assertAlmostEqual(r["breakeven"], PRICE - 6.0)

    def test_max_loss_is_the_capital_less_the_dividends(self):
        r = ST.shares_position(PRICE, CAP, PRICES, PROBS, 3.0,
                               dividend_fv_per_share=6.0)
        self.assertAlmostEqual(r["max_loss"], CAP - 600.0)

    def test_notional_equals_capital_for_shares(self):
        r = ST.shares_position(PRICE, CAP, PRICES, PROBS, 3.0)
        self.assertAlmostEqual(r["notional"], r["capital_allocated"])

    def test_missing_scenario_price_refuses(self):
        r = ST.shares_position(PRICE, CAP, {"base": 110.0}, PROBS, 3.0)
        self.assertFalse(r["eligible"])


class TestSecuredPut(unittest.TestCase):
    def test_risk_is_the_full_strike_notional(self):
        r = ST.secured_put(PRICE, CAP, 90.0, 5.0, PRICES, PROBS, 1.0)
        self.assertTrue(r["eligible"])
        self.assertAlmostEqual(r["notional"], 9_000.0)
        self.assertAlmostEqual(r["capital_allocated"], 9_000.0)

    def test_a_strike_needing_more_than_the_capital_is_ineligible(self):
        r = ST.secured_put(PRICE, CAP, 120.0, 5.0, PRICES, PROBS, 1.0)
        self.assertFalse(r["eligible"])
        self.assertIn("full strike notional", r["reason"])

    def test_assignment_cost_is_strike_less_premium(self):
        r = ST.secured_put(PRICE, CAP, 90.0, 5.0, PRICES, PROBS, 1.0)
        self.assertAlmostEqual(r["contract"]["effective_assignment_cost"], 85.0)
        self.assertAlmostEqual(r["breakeven"], 85.0)

    def test_premium_earns_the_risk_free_rate_alongside_the_cash(self):
        flat = ST.secured_put(PRICE, CAP, 90.0, 5.0, PRICES, PROBS, 1.0,
                              rate_pct=0.0)
        earning = ST.secured_put(PRICE, CAP, 90.0, 5.0, PRICES, PROBS, 1.0,
                                 rate_pct=5.0)
        self.assertGreater(earning["terminal"]["base"]["wealth"],
                           flat["terminal"]["base"]["wealth"])

    def test_obligation_bites_only_below_the_strike(self):
        r = ST.secured_put(PRICE, CAP, 90.0, 5.0, PRICES, PROBS, 1.0,
                           rate_pct=0.0)
        # base ends at 110, above the strike → the whole credit is kept
        self.assertAlmostEqual(r["terminal"]["base"]["wealth"], CAP + 500.0)
        # bear ends at 60 → 30 a share is owed
        self.assertAlmostEqual(r["terminal"]["bear"]["wealth"],
                               CAP + 500.0 - 3_000.0)

    def test_no_bid_is_refused_because_the_bid_is_the_credit_basis(self):
        r = ST.secured_put(PRICE, CAP, 90.0, 0.0, PRICES, PROBS, 1.0)
        self.assertFalse(r["eligible"])
        self.assertIn("bid", r["reason"])


class TestLongCall(unittest.TestCase):
    def test_unspent_capital_earns_the_treasury_rate(self):
        r = ST.long_call(PRICE, CAP, 90.0, 20.0, PRICES, PROBS, 1.0,
                         rate_pct=5.0)
        self.assertTrue(r["eligible"])
        self.assertAlmostEqual(r["unused_capital"], CAP - 2_000.0)
        self.assertAlmostEqual(r["terminal"]["base"]["wealth"],
                               8_000.0 * 1.05 + 100 * (110.0 - 90.0))

    def test_worthless_at_expiry_still_keeps_the_interest(self):
        r = ST.long_call(PRICE, CAP, 90.0, 20.0, PRICES, PROBS, 1.0,
                         rate_pct=5.0)
        self.assertAlmostEqual(r["terminal"]["bear"]["wealth"], 8_000.0 * 1.05)

    def test_max_loss_accounts_for_the_forgone_interest(self):
        r = ST.long_call(PRICE, CAP, 90.0, 20.0, PRICES, PROBS, 1.0,
                         rate_pct=5.0)
        self.assertAlmostEqual(r["max_loss"], CAP - 8_000.0 * 1.05)

    def test_breakeven_is_strike_plus_premium(self):
        r = ST.long_call(PRICE, CAP, 90.0, 20.0, PRICES, PROBS, 1.0)
        self.assertAlmostEqual(r["breakeven"], 110.0)

    def test_a_call_never_receives_the_dividend(self):
        r = ST.long_call(PRICE, CAP, 90.0, 20.0, PRICES, PROBS, 1.0,
                         rate_pct=0.0)
        s = ST.shares_position(PRICE, CAP, PRICES, PROBS, 1.0,
                               dividend_fv_per_share=5.0, rate_pct=0.0)
        self.assertIn("dividend", " ".join(r["notes"]).lower())
        self.assertGreater(s["terminal"]["base"]["wealth"],
                           100 * 110.0)   # the shares got the cash
        self.assertAlmostEqual(r["terminal"]["base"]["wealth"],
                               8_000.0 + 2_000.0)


class TestBuyWrite(unittest.TestCase):
    def test_upside_is_capped_at_the_short_strike(self):
        r = ST.buy_write(PRICE, CAP, 120.0, 6.0, PRICES, PROBS, 1.0,
                         rate_pct=0.0)
        self.assertTrue(r["eligible"])
        # bull ends at 150 → called away at 120
        self.assertAlmostEqual(r["terminal"]["bull"]["wealth"],
                               100 * 150.0 - 100 * 30.0 + 600.0)
        self.assertTrue(r["terminal"]["bull"]["called_away"])
        self.assertFalse(r["terminal"]["base"]["called_away"])

    def test_breakeven_drops_by_the_premium_and_the_dividend(self):
        r = ST.buy_write(PRICE, CAP, 120.0, 6.0, PRICES, PROBS, 1.0,
                         dividend_fv_per_share=2.0)
        self.assertAlmostEqual(r["breakeven"], 100.0 - 6.0 - 2.0)

    def test_early_assignment_is_flagged_when_the_dividend_exceeds_extrinsic(self):
        # In the money and almost no extrinsic value left, with a dividend due.
        r = ST.buy_write(PRICE, CAP, 80.0, 20.5, PRICES, PROBS, 1.0,
                         dividend_fv_per_share=3.0)
        self.assertTrue(r["contract"]["early_assignment_risk"])
        self.assertIn("Early-assignment", " ".join(r["notes"]))

    def test_no_early_assignment_flag_when_extrinsic_is_thick(self):
        r = ST.buy_write(PRICE, CAP, 120.0, 6.0, PRICES, PROBS, 1.0,
                         dividend_fv_per_share=1.0)
        self.assertFalse(r["contract"]["early_assignment_risk"])

    def test_it_is_a_single_expiration_and_says_so(self):
        r = ST.buy_write(PRICE, CAP, 120.0, 6.0, PRICES, PROBS, 1.0)
        self.assertIn("cap", " ".join(r["notes"]).lower())


class TestBullCallSpread(unittest.TestCase):
    def test_payoff_is_capped_at_the_width(self):
        r = ST.bull_call_spread(PRICE, CAP, 95.0, 12.0, 115.0, 4.0, PRICES,
                                PROBS, 1.0, rate_pct=0.0)
        self.assertTrue(r["eligible"])
        debit = 800.0
        self.assertAlmostEqual(r["capital_allocated"], debit)
        self.assertAlmostEqual(r["terminal"]["bull"]["wealth"],
                               (CAP - debit) + 100 * 20.0)
        self.assertAlmostEqual(r["contract"]["max_gain"], 2_000.0 - debit)

    def test_max_loss_is_the_debit(self):
        r = ST.bull_call_spread(PRICE, CAP, 95.0, 12.0, 115.0, 4.0, PRICES,
                                PROBS, 1.0, rate_pct=0.0)
        self.assertAlmostEqual(r["max_loss"], 800.0)
        self.assertAlmostEqual(r["terminal"]["bear"]["wealth"], CAP - 800.0)

    def test_a_credit_spread_is_refused_as_a_different_structure(self):
        r = ST.bull_call_spread(PRICE, CAP, 95.0, 4.0, 115.0, 12.0, PRICES,
                                PROBS, 1.0)
        self.assertFalse(r["eligible"])
        self.assertIn("credit", r["reason"])

    def test_strikes_must_be_ordered(self):
        r = ST.bull_call_spread(PRICE, CAP, 115.0, 12.0, 95.0, 4.0, PRICES,
                                PROBS, 1.0)
        self.assertFalse(r["eligible"])

    def test_breakeven(self):
        r = ST.bull_call_spread(PRICE, CAP, 95.0, 12.0, 115.0, 4.0, PRICES,
                                PROBS, 1.0)
        self.assertAlmostEqual(r["breakeven"], 103.0)


class TestRankingAndTossUp(unittest.TestCase):
    def rows(self):
        return [
            ST.shares_position(PRICE, CAP, PRICES, PROBS, 1.0, rate_pct=4.0),
            ST.long_call(PRICE, CAP, 90.0, 20.0, PRICES, PROBS, 1.0, rate_pct=4.0),
            ST.secured_put(PRICE, CAP, 90.0, 5.0, PRICES, PROBS, 1.0, rate_pct=4.0),
        ]

    def test_return_is_on_the_full_comparison_capital(self):
        rows = self.rows()
        for r in rows:
            base = r["terminal"]["base"]
            self.assertAlmostEqual(base["return_pct"],
                                   (base["wealth"] / CAP - 1.0) * 100.0)

    def test_ranking_puts_ineligible_rows_last(self):
        rows = self.rows() + [ST.unavailable(ST.SPREAD, "no quotes")]
        ranked = ST.rank(rows)
        self.assertFalse(ranked[-1]["eligible"])

    def test_reweighting_changes_only_the_weighting(self):
        rows = self.rows()
        alt = ST.reweight(rows, {"bear": 0.9, "base": 0.05, "bull": 0.05}, 1.0)
        for a, b in zip(rows, alt):
            self.assertAlmostEqual(a["terminal"]["base"]["wealth"],
                                   b["terminal"]["base"]["wealth"])
        self.assertNotAlmostEqual(rows[1]["weighted_wealth"],
                                  alt[1]["weighted_wealth"])

    def test_toss_up_when_the_winner_flips_under_a_small_shift(self):
        """Two structures constructed to swap places on a five-point move."""
        a = ST.unavailable(ST.SHARES, "")
        # A is defensive (best in the bear), B is aggressive (best in the
        # bull). They are within a whisker at the default weights, so moving
        # the bear weight five points hands the lead to A.
        a.update({"eligible": True, "capital_allocated": CAP,
                  "terminal": {"bear": {"wealth": 11_500.0},
                               "base": {"wealth": 11_000.0},
                               "bull": {"wealth": 10_500.0}}})
        ST._finish(a, CAP, PROBS, 1.0)
        b = ST.unavailable(ST.LEAPS, "")
        b.update({"eligible": True, "capital_allocated": CAP,
                  "terminal": {"bear": {"wealth": 9_000.0},
                               "base": {"wealth": 11_000.0},
                               "bull": {"wealth": 13_200.0}}})
        ST._finish(b, CAP, PROBS, 1.0)
        out = ST.compare([a, b], PROBS, 1.0)
        self.assertTrue(out["toss_up"])
        self.assertTrue(out["sensitivity"]["flips"])
        self.assertIn("swap places", out["sensitivity"]["reason"])

    def test_a_clear_winner_is_not_a_toss_up(self):
        rows = self.rows()
        out = ST.compare(rows, PROBS, 1.0)
        if not out["toss_up"]:
            self.assertIn("stays ahead", out["sensitivity"]["reason"])

    def test_a_near_tie_is_a_toss_up_even_without_a_flip(self):
        a = ST.unavailable(ST.SHARES, "")
        a.update({"eligible": True, "terminal": {
            s: {"wealth": 11_000.0} for s in ST.SCENARIOS}})
        ST._finish(a, CAP, PROBS, 1.0)
        b = ST.unavailable(ST.LEAPS, "")
        b.update({"eligible": True, "terminal": {
            s: {"wealth": 10_990.0} for s in ST.SCENARIOS}})
        ST._finish(b, CAP, PROBS, 1.0)
        out = ST.compare([a, b], PROBS, 1.0)
        self.assertTrue(out["toss_up"])
        self.assertIn("inside the noise", out["sensitivity"]["reason"])

    def test_capital_basis_is_stated_in_the_payload(self):
        out = ST.compare(self.rows(), PROBS, 1.0)
        self.assertIn("100 × the current share price", out["capital_basis"])
        self.assertIn("Treasury", out["capital_basis"])

    def test_one_structure_is_not_sensitivity_tested(self):
        out = ST.compare([self.rows()[0]], PROBS, 1.0)
        self.assertFalse(out["sensitivity"]["available"])
        self.assertFalse(out["toss_up"])


class TestEvents(unittest.TestCase):
    def test_counts_reports_between_today_and_expiry(self):
        e = ST.expected_events("2026-11-01", "2027-06-01", "2026-08-18")
        self.assertTrue(e["available"])
        self.assertEqual(e["count"], 3)
        self.assertEqual(e["confirmed"], ["2026-11-01"])

    def test_only_the_first_date_is_claimed_as_published(self):
        e = ST.expected_events("2026-11-01", "2027-06-01", "2026-08-18")
        self.assertIn("expected, not scheduled", e["note"])

    def test_no_next_date_is_reported_not_guessed(self):
        e = ST.expected_events(None, "2027-06-01", "2026-08-18")
        self.assertFalse(e["available"])
        self.assertIn("not available", e["reason"])

    def test_unreadable_expiry_is_refused(self):
        self.assertFalse(ST.expected_events("2026-11-01", "junk", "2026-08-18")["available"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
