"""Tests for bank_model — tangible book, profitability, credit and the bank
fair-value methods.

Four claims this panel makes in writing are asserted here rather than trusted:

  1. A MISSING component refuses the answer. A bank that does not tag its
     preferred stock has an unknown share of its equity belonging to somebody
     other than the common shareholder, and treating that as zero would
     overstate tangible book for every share. Bank of America is that bank.
  2. PROFITABILITY changes what tangible book is worth. A bank earning
     exactly its cost of equity is worth exactly its tangible book; one
     earning more is worth a premium and one earning less a discount. That is
     asserted as a table, not as a direction.
  3. The peer comparison prices a bank at ITS OWN profitability where the
     relationship across the group is real, and falls back to the median with
     a stated reason where it is not.
  4. What is NOT net interest margin is not called net interest margin.
"""

import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import bank_model as BM
import fair_value as FV
import fundamentals as F


# ── a filer built by hand, so every assertion has one cause ────────────────

def _dur(name, unit, rows):
    return {"units": {unit: rows}}


def _inst(name, unit, rows):
    return {"units": {unit: rows}}


def quarters(value, n=12, start_year=2023, unit="USD"):
    """n consecutive quarterly duration facts, each worth `value`."""
    out, y, q = [], start_year, 0
    months = [("01-01", "03-31"), ("04-01", "06-30"),
              ("07-01", "09-30"), ("10-01", "12-31")]
    for i in range(n):
        s, e = months[q]
        out.append({"start": f"{y}-{s}", "end": f"{y}-{e}", "val": value,
                    "filed": f"{y}-{e[:2]}-15", "form": "10-Q"})
        q += 1
        if q == 4:
            q, y = 0, y + 1
    return out


def instants(values, unit="USD", start_year=2023):
    """One balance-sheet reading per quarter end."""
    out, y, q = [], start_year, 0
    ends = ["03-31", "06-30", "09-30", "12-31"]
    for v in values:
        out.append({"end": f"{y}-{ends[q]}", "val": v,
                    "filed": f"{y}-{ends[q]}", "form": "10-Q"})
        q += 1
        if q == 4:
            q, y = 0, y + 1
    return out


def bank_facts(preferred=2_000_000_000.0, goodwill=5_000_000_000.0,
               intangibles=500_000_000.0, equity=50_000_000_000.0,
               net_income=1_500_000_000.0, shares=1_000_000_000.0,
               assets=600_000_000_000.0, capital_ratio=0.118,
               preferred_dividends=None):
    gaap = {
        "StockholdersEquity": _inst("", "USD", instants([equity] * 12)),
        "Goodwill": _inst("", "USD", instants([goodwill] * 12)),
        "Assets": _inst("", "USD", instants([assets] * 12)),
        "NetIncomeLossAvailableToCommonStockholdersBasic":
            _dur("", "USD", quarters(net_income)),
        "WeightedAverageNumberOfDilutedSharesOutstanding":
            _dur("", "shares", quarters(shares)),
        "InterestIncomeExpenseNet": _dur("", "USD", quarters(3_000_000_000.0)),
        "NoninterestIncome": _dur("", "USD", quarters(1_000_000_000.0)),
        "NoninterestExpense": _dur("", "USD", quarters(2_200_000_000.0)),
        "InterestExpenseDeposits": _dur("", "USD", quarters(400_000_000.0)),
        "Deposits": _inst("", "USD", instants([400_000_000_000.0] * 12)),
        "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss":
            _inst("", "USD", instants([300_000_000_000.0] * 12)),
        "FinancingReceivableAllowanceForCreditLossWriteOffs":
            _dur("", "USD", quarters(200_000_000.0)),
        "FinancingReceivableExcludingAccruedInterestNonaccrualStatus":
            _inst("", "USD", instants([1_500_000_000.0] * 12)),
        "CommonEquityTierOneCapitalToRiskWeightedAssets":
            _inst("", "pure", instants([capital_ratio] * 12)),
    }
    if intangibles is not None:
        gaap["IntangibleAssetsNetExcludingGoodwill"] = _inst(
            "", "USD", instants([intangibles] * 12))
    if preferred is not None:
        gaap["PreferredStockValue"] = _inst("", "USD",
                                            instants([preferred] * 12))
    if preferred_dividends is not None:
        gaap["PreferredStockDividendsIncomeStatementImpact"] = _dur(
            "", "USD", quarters(preferred_dividends))
    return {"facts": {"us-gaap": gaap}}


class TestTangibleBook(unittest.TestCase):
    def test_tangible_common_equity_removes_preferred_and_intangibles(self):
        t = BM.tangible_common_equity(F, bank_facts())
        # 50bn equity − 2bn preferred − 5bn goodwill − 0.5bn intangibles.
        self.assertAlmostEqual(t["value"], 42_500_000_000.0, places=0)
        self.assertAlmostEqual(t["common_equity"], 48_000_000_000.0, places=0)

    def test_untagged_preferred_refuses_when_the_bank_plainly_has_some(self):
        # No preferred BALANCE, but it pays preferred dividends every
        # quarter. That is Bank of America, and treating its preferred as
        # zero would credit the common shareholder with somebody else's
        # equity — so the answer is refused rather than overstated.
        t = BM.tangible_common_equity(
            F, bank_facts(preferred=None, preferred_dividends=100_000_000.0))
        self.assertIsNone(t["value"])
        self.assertIn("preferred", t["reason"].lower())
        self.assertIn("preferred dividends", t["reason"])
        self.assertIn("belongs to the preferred holders", t["reason"])

    def test_no_preferred_anywhere_deducts_zero_rather_than_refusing(self):
        # No preferred balance, no preferred dividend, no preferred shares.
        # That is the filings saying there is none — Chubb, Aflac, Hanover —
        # and refusing there would blank the book value of a large share of
        # the insurers and brokers this app covers for no reason at all.
        t = BM.tangible_common_equity(F, bank_facts(preferred=None))
        self.assertAlmostEqual(t["value"], 44_500_000_000.0, places=0)
        self.assertAlmostEqual(t["common_equity"], 50_000_000_000.0, places=0)
        pref = F.preferred_equity(bank_facts(preferred=None))
        self.assertEqual(pref["value"], 0.0)
        self.assertIn("no preferred", pref["basis"].lower())

    def test_missing_goodwill_refuses(self):
        facts = bank_facts()
        del facts["facts"]["us-gaap"]["Goodwill"]
        t = BM.tangible_common_equity(F, facts)
        self.assertIsNone(t["value"])
        self.assertIn("Goodwill", t["reason"])

    def test_absent_other_intangibles_is_treated_as_none_to_deduct(self):
        # Goodwill and preferred are the required pieces. A bank with no
        # separately tagged other intangibles genuinely has nothing more to
        # take out, and the component is reported as absent beside it.
        t = BM.tangible_common_equity(F, bank_facts(intangibles=None))
        self.assertAlmostEqual(t["value"], 43_000_000_000.0, places=0)
        self.assertTrue(t["components"]["intangibles_reason"])


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.m = BM.metrics(F, bank_facts(), price=40.0,
                            shares_outstanding=1_000_000_000.0)

    def test_price_to_tangible_book_and_price_to_book(self):
        self.assertTrue(self.m["available"])
        self.assertAlmostEqual(self.m["tangible_book_per_share"]["value"], 42.5)
        self.assertAlmostEqual(self.m["book_per_share"]["value"], 48.0)
        self.assertAlmostEqual(self.m["price_to_tangible_book"]["value"],
                               40.0 / 42.5, places=6)
        self.assertAlmostEqual(self.m["price_to_book"]["value"], 40.0 / 48.0,
                               places=6)

    def test_return_on_tangible_common_equity_exceeds_return_on_equity(self):
        roe = self.m["return_on_equity_pct"]["value"]
        rotce = self.m["return_on_tangible_common_equity_pct"]["value"]
        # Tangible common equity is smaller than total equity, so the same
        # profit is a larger return on it. Always.
        self.assertGreater(rotce, roe)

    def test_efficiency_ratio_is_expense_over_revenue(self):
        # 2.2bn expense over (3.0 + 1.0)bn revenue, per quarter, so 55%.
        self.assertAlmostEqual(self.m["efficiency_ratio_pct"]["value"], 55.0,
                               places=4)

    def test_what_is_not_net_interest_margin_is_not_called_that(self):
        block = self.m["net_interest_income_to_average_assets_pct"]
        self.assertIsNotNone(block["value"])
        self.assertIn("NOT net interest margin", block["basis"])
        self.assertNotIn("net_interest_margin", self.m)

    def test_capital_ratio_names_which_ratio_was_actually_tagged(self):
        cap = self.m["capital_ratio"]
        self.assertAlmostEqual(cap["value"], 11.8, places=4)
        self.assertIn("Common equity tier one", cap["label"])

    def test_a_ratio_filed_as_a_percent_is_not_multiplied_again(self):
        facts = bank_facts()
        facts["facts"]["us-gaap"]["CommonEquityTierOneCapitalToRiskWeightedAssets"] = \
            {"units": {"pure": instants([11.8] * 12)}}
        cap = F.instant_ratio(facts, "capital_ratio")
        self.assertAlmostEqual(cap["value"], 11.8, places=4)

    def test_credit_and_funding_measures_are_present(self):
        for key in ("charge_off_rate_pct", "nonperforming_rate_pct",
                    "deposit_cost_pct", "loan_growth_pct",
                    "deposit_growth_pct", "diluted_share_trend_pct"):
            self.assertIsNotNone(self.m[key]["value"], key)

    def test_missing_measures_carry_a_reason_and_never_a_zero(self):
        facts = bank_facts()
        del facts["facts"]["us-gaap"]["FinancingReceivableAllowanceForCreditLossWriteOffs"]
        m = BM.metrics(F, facts, price=40.0, shares_outstanding=1e9)
        self.assertIsNone(m["charge_off_rate_pct"]["value"])
        self.assertTrue(m["charge_off_rate_pct"]["reason"])


class TestAverageBalance(unittest.TestCase):
    def test_average_uses_both_ends_of_the_year_when_both_exist(self):
        facts = bank_facts()
        facts["facts"]["us-gaap"]["StockholdersEquity"] = {
            "units": {"USD": instants([10.0, 20.0, 30.0, 40.0, 50.0, 60.0,
                                       70.0, 80.0])}}
        avg = BM.average_balance(F, facts, "equity")
        # Latest is 80 at 2024-12-31; a year earlier is 40 at 2023-12-31.
        self.assertAlmostEqual(avg["value"], 60.0)
        self.assertIn("Average of", avg["basis"])

    def test_without_a_year_ago_reading_it_says_so(self):
        facts = bank_facts()
        facts["facts"]["us-gaap"]["StockholdersEquity"] = {
            "units": {"USD": instants([10.0, 20.0])}}
        avg = BM.average_balance(F, facts, "equity")
        self.assertAlmostEqual(avg["value"], 20.0)
        self.assertIn("closing balance rather than an average", avg["basis"])


class TestJustifiedMultiple(unittest.TestCase):
    def test_a_bank_earning_its_cost_of_equity_is_worth_its_tangible_book(self):
        m = BM.justified_multiple(10.0, 10.0, 3.0)
        self.assertAlmostEqual(m["value"], 1.0, places=9)

    def test_the_multiple_rises_with_profitability(self):
        table = [(6.0, 10.0, 3.0), (10.0, 10.0, 3.0), (20.0, 10.0, 3.0)]
        vals = [BM.justified_multiple(*t)["value"] for t in table]
        self.assertLess(vals[0], 1.0)
        self.assertAlmostEqual(vals[1], 1.0, places=9)
        self.assertGreater(vals[2], 2.0)
        self.assertLess(vals[0], vals[1])
        self.assertLess(vals[1], vals[2])

    def test_a_bank_earning_below_its_growth_assumption_is_refused(self):
        m = BM.justified_multiple(2.0, 10.0, 3.0)
        self.assertIsNone(m["value"])
        self.assertIn("does not fit", m["reason"])

    def test_growth_too_close_to_the_cost_of_equity_is_refused(self):
        m = BM.justified_multiple(15.0, 9.5, 9.0)
        self.assertIsNone(m["value"])
        self.assertIn("not far enough above", m["reason"])

    def test_cost_of_equity_is_the_ten_year_plus_a_stated_premium(self):
        ke = BM.cost_of_equity(4.0, {"bank_equity_risk_premium_pct": 5.0})
        self.assertAlmostEqual(ke["value"], 9.0)
        self.assertIn("market-wide convention", ke["basis"])
        self.assertIsNone(BM.cost_of_equity(None)["value"])


class TestPeerRelationship(unittest.TestCase):
    def test_a_real_relationship_prices_the_bank_at_its_own_profitability(self):
        # Eight banks where every extra point of return is worth 0.1 of
        # tangible book, exactly.
        rotce = [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
        mults = [0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
        out = BM.peer_fitted_multiple(20.0, rotce, mults)
        self.assertTrue(out["fitted"])
        self.assertAlmostEqual(out["value"], 2.0, places=6)
        # The median would have said 1.3 — the whole point of fitting.
        self.assertAlmostEqual(out["median"], 1.3, places=6)
        self.assertGreater(out["r2"], 0.99)

    def test_a_weak_relationship_falls_back_to_the_median_and_says_why(self):
        rotce = [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
        mults = [1.4, 0.7, 1.9, 0.8, 1.5, 0.9, 1.6, 1.0]
        out = BM.peer_fitted_multiple(20.0, rotce, mults)
        self.assertFalse(out["fitted"])
        self.assertAlmostEqual(out["value"], out["median"])
        self.assertIn("explains", out["reason"])

    def test_too_few_peers_falls_back_to_the_median(self):
        out = BM.peer_fitted_multiple(15.0, [8.0, 12.0, 16.0],
                                      [0.8, 1.2, 1.6])
        self.assertFalse(out["fitted"])
        self.assertIn("fewer than the 8", out["reason"])

    def test_a_negative_slope_is_not_used(self):
        rotce = [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
        mults = [2.0, 1.8, 1.6, 1.4, 1.2, 1.0, 0.8, 0.6]
        out = BM.peer_fitted_multiple(20.0, rotce, mults)
        self.assertFalse(out["fitted"])

    def test_regression_refuses_fewer_than_three_points(self):
        self.assertIsNone(BM.regress([1, 2], [1, 2]))
        self.assertIsNone(BM.regress([1, 1, 1], [1, 2, 3]))


class TestPointInTimeSeries(unittest.TestCase):
    def test_book_per_share_is_dated_at_the_filing_that_stated_it(self):
        s = BM.point_in_time_series(F, bank_facts())
        self.assertTrue(s["book_per_share"])
        self.assertTrue(s["tangible_book_per_share"])
        for row in s["book_per_share"]:
            self.assertTrue(row["first_filed"])
            self.assertAlmostEqual(row["value"], 48.0, places=6)
        for row in s["tangible_book_per_share"]:
            self.assertAlmostEqual(row["value"], 42.5, places=6)

    def test_untagged_preferred_means_no_series_rather_than_a_wrong_one(self):
        s = BM.point_in_time_series(
            F, bank_facts(preferred=None, preferred_dividends=100_000_000.0))
        self.assertEqual(s.get("book_per_share"), [])

    def test_a_bank_with_no_preferred_still_gets_a_series(self):
        s = BM.point_in_time_series(F, bank_facts(preferred=None))
        self.assertTrue(s["book_per_share"])
        for row in s["book_per_share"]:
            self.assertAlmostEqual(row["value"], 50.0, places=6)


class TestBankFairValue(unittest.TestCase):
    def _methods(self, ptbv_history=None, peers=None):
        bank = BM.metrics(F, bank_facts(), price=40.0,
                          shares_outstanding=1_000_000_000.0)
        bank["eps_ttm"] = 6.0
        hist = {"raw_values": {
            "price_to_tangible_book": {"5y": ptbv_history
                                       or [1.0 + (i % 100) / 200.0
                                           for i in range(1200)]},
            "price_to_book": {"5y": [0.9 + (i % 100) / 200.0
                                     for i in range(1200)]},
            "earnings_yield_pct": {"5y": [8.0 + (i % 100) / 50.0
                                          for i in range(1200)]},
        }, "regime": {"shifted": False}}
        return bank, BM.methods(bank, hist, peers or {}, ten_year_pct=4.0)

    def test_every_method_declares_it_was_built_for_a_bank(self):
        _bank, ms = self._methods()
        for m in ms:
            self.assertEqual(m["specialized_for"], "BANK")

    def test_a_bank_is_valued_rather_than_refused(self):
        bank, ms = self._methods()
        out = FV.fair_value(ms, price=40.0,
                            business_type={"type": "BANK", "note": ""})
        self.assertTrue(out["available"])
        self.assertIsNotNone(out["buy_zone"])
        self.assertLess(out["bear"], out["bull"])

    def test_generic_methods_do_not_unlock_a_bank(self):
        generic = [FV.method_self_history(6.0, [8.0] * 1250)]
        out = FV.fair_value(generic, price=40.0,
                            business_type={"type": "BANK", "note": ""})
        self.assertFalse(out["available"])
        self.assertEqual(out["verdict"], FV.SPECIALIZED)

    def test_an_insurer_is_still_refused_even_with_bank_methods(self):
        _bank, ms = self._methods()
        out = FV.fair_value(ms, price=40.0,
                            business_type={"type": "INSURANCE", "note": ""})
        self.assertFalse(out["available"])
        self.assertEqual(out["verdict"], FV.SPECIALIZED)

    def test_a_low_multiple_is_the_pessimistic_end(self):
        _bank, ms = self._methods()
        m = next(x for x in ms if x["key"] == "bank_self_ptbv")
        self.assertLess(m["detail"]["multiple_bear"], m["detail"]["multiple_bull"])
        self.assertLess(m["bear"], m["bull"])

    def test_the_justified_method_carries_its_inputs_on_screen(self):
        _bank, ms = self._methods()
        m = next(x for x in ms if x["key"] == "bank_justified")
        self.assertTrue(m["available"])
        for k in ("rotce_pct", "cost_of_equity_pct", "growth_pct"):
            self.assertIsNotNone(m["detail"][k])

    def test_the_buy_zone_falls_when_the_methods_disagree(self):
        _b, tight = self._methods(ptbv_history=[1.0] * 1200)
        _b2, wide = self._methods(ptbv_history=[0.4 + (i % 100) / 25.0
                                                for i in range(1200)])
        a = FV.fair_value(tight, price=40.0,
                          business_type={"type": "BANK", "note": ""})
        b = FV.fair_value(wide, price=40.0,
                          business_type={"type": "BANK", "note": ""})
        levels = FV.CONFIDENCE_LEVELS
        self.assertGreaterEqual(levels.index(b["confidence_level"]),
                                levels.index(a["confidence_level"]))

    def test_peer_inputs_drop_banks_without_a_usable_multiple(self):
        got = BM.peer_inputs([
            {"price_to_tangible_book": 1.2,
             "return_on_tangible_common_equity_pct": 14.0},
            {"price_to_tangible_book": None,
             "return_on_tangible_common_equity_pct": 9.0},
            {"price_to_tangible_book": -0.5,
             "return_on_tangible_common_equity_pct": 4.0},
        ])
        self.assertEqual(got["n"], 1)
        self.assertEqual(got["ptbv_multiples"], [1.2])


if __name__ == "__main__":                            # pragma: no cover
    unittest.main()
