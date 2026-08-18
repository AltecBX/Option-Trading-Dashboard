"""Which valuation model a company gets, and why.

Every fixture is built from the shape of a real filer measured across the
universe — the balance-sheet share, the revenue mix and the language of the
business chapter — but no test asserts an outcome for a ticker. What is
asserted is that a company with a broker's balance sheet is read as a
broker, that a company earning its living from running a market is not, and
that a company which is both gets neither model on its own.
"""
import unittest

import business_routing as BR


class FakeFund:
    """A stand-in for fundamentals: instants, durations and a newest period."""

    def __init__(self, instants=None, durations=None,
                 newest="2026-06-30", ends=None):
        self.instants = dict(instants or {})
        self.durations = dict(durations or {})
        self.newest = newest
        self.ends = dict(ends or {})

    def _newest_period(self, facts):
        return self.newest

    def _days_between(self, a, b):
        if not a or not b:
            return None
        from datetime import date
        try:
            return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
        except ValueError:
            return None

    def instant(self, facts, name, as_of=None):
        v = self.instants.get(name)
        return {"value": v, "as_of": self.ends.get(name, self.newest)}

    def metric(self, facts, name, as_of=None):
        v = self.durations.get(name)
        return {"value": v, "period_end": self.ends.get(name, self.newest)}


CORPORATE = {"revenue": 1000.0, "operating_cash_flow": 300.0, "capex": 40.0,
             "operating_income": 250.0}
BROKER_ACCOUNTS = {"revenue": 1000.0, "operating_cash_flow": -600.0,
                   "capex": 20.0}

FACTS = {"facts": {"us-gaap": {}}}


def route(instants=None, durations=None, sic="6211", phrases=None,
          ends=None, eps=2.0, cfg=None):
    fund = FakeFund(instants, {**CORPORATE, **(durations or {})}, ends=ends)
    return BR.route(fund, FACTS, sic=sic, phrases=phrases or {},
                    text_confidence="HIGH", eps_ttm=eps, cfg=cfg)


class TestABroker(unittest.TestCase):
    def test_customer_money_on_the_balance_sheet_makes_a_broker(self):
        got = route({"assets": 10000.0, "customer_receivables": 2000.0,
                     "segregated_cash": 800.0})
        self.assertEqual(got["business_class"], BR.BROKER)
        self.assertEqual(got["model"], "BROKER")
        self.assertIn("broker-dealer", " ".join(got["why"]))

    def test_a_broker_funded_like_a_bank_is_still_a_broker(self):
        got = route({"assets": 10000.0, "customer_receivables": 400.0,
                     "deposits": 7000.0})
        self.assertEqual(got["business_class"], BR.BROKER)
        self.assertIn(BR.BANK, got["secondary"])

    def test_trading_revenue_makes_a_dealer_without_customer_balances(self):
        """Virtu is a market maker with barely any customer balances."""
        got = route({"assets": 10000.0}, {"trading_gains": 700.0})
        self.assertEqual(got["business_class"], BR.BROKER)

    def test_a_thin_customer_balance_does_not_make_a_broker(self):
        """MarketAxess holds customer money worth 2% of its assets."""
        got = route({"assets": 10000.0, "segregated_cash": 200.0},
                    phrases={"exchange": ["a market venue",
                                          "an electronic marketplace"]})
        self.assertNotEqual(got["business_class"], BR.BROKER)


class TestAnExchange(unittest.TestCase):
    def test_market_data_and_clearing_revenue_settle_it(self):
        """Clearing margin on an exchange's books is not a brokerage."""
        got = route({"assets": 10000.0, "segregated_cash": 460.0},
                    {"market_data_revenue": 200.0,
                     "clearing_fees_revenue": 200.0})
        self.assertEqual(got["business_class"], BR.EXCHANGE)
        self.assertEqual(got["model"], "STANDARD")

    def test_a_venue_that_only_describes_itself_still_reaches_standard(self):
        got = route({"assets": 10000.0},
                    phrases={"exchange": ["a market venue", "a clearing house",
                                          "an electronic marketplace"]})
        self.assertEqual(got["business_class"], BR.EXCHANGE)
        self.assertEqual(got["model"], "STANDARD")

    def test_stale_venue_revenue_does_not_count(self):
        """Every exchange's market-data series stops in 2018."""
        got = route({"assets": 10000.0},
                    {"market_data_revenue": 200.0,
                     "clearing_fees_revenue": 200.0},
                    ends={"market_data_revenue": "2018-03-31",
                          "clearing_fees_revenue": "2018-03-31"})
        self.assertNotEqual(got["business_class"], BR.EXCHANGE)

    def test_a_venue_without_an_operating_company_s_accounts_is_not_routed(self):
        fund = FakeFund({"assets": 10000.0}, BROKER_ACCOUNTS)
        got = BR.route(fund, FACTS, sic="6200",
                       phrases={"exchange": ["a market venue",
                                             "a clearing house"]},
                       text_confidence="HIGH", eps_ttm=2.0)
        self.assertNotEqual(got["business_class"], BR.EXCHANGE)


class TestAnAssetManager(unittest.TestCase):
    def test_a_manager_reaches_the_standard_engine(self):
        got = route({"assets": 500.0},
                    phrases={"manager": ["money managed for others",
                                         "an investment management business",
                                         "funds sold to the public"]})
        self.assertEqual(got["business_class"], BR.ASSET_MANAGER)
        self.assertEqual(got["model"], "STANDARD")

    def test_missing_assets_under_management_does_not_stop_it(self):
        """A profitable cash-generating manager is not left unvaluable."""
        got = route({"assets": 500.0},
                    phrases={"manager": ["money managed for others",
                                         "an asset management business"]})
        self.assertEqual(got["model"], "STANDARD")
        self.assertNotEqual(got["business_class"], BR.UNSUPPORTED)

    def test_a_manager_whose_chapter_mentions_exchanges_is_still_a_manager(self):
        """Franklin's regulation section names clearing houses and exchanges."""
        got = route({"assets": 500.0},
                    phrases={"manager": ["money managed for others",
                                         "an investment management business",
                                         "a registered adviser",
                                         "funds sold to the public"],
                              "exchange": ["a market venue",
                                           "a clearing house"]})
        self.assertEqual(got["business_class"], BR.ASSET_MANAGER)

    def test_a_venue_that_mentions_funds_is_still_a_venue(self):
        got = route({"assets": 500.0},
                    phrases={"exchange": ["a market venue", "a clearing house",
                                          "an electronic marketplace",
                                          "listing services"],
                              "manager": ["funds sold to the public"]})
        self.assertEqual(got["business_class"], BR.EXCHANGE)


class TestBanksInsurersAndTrustsAreUnchanged(unittest.TestCase):
    def test_a_bank_code_is_still_a_bank(self):
        self.assertEqual(route({}, sic="6021")["business_class"], BR.BANK)

    def test_an_insurance_code_is_still_an_insurer(self):
        self.assertEqual(route({}, sic="6331")["business_class"], BR.INSURANCE)

    def test_a_property_code_is_still_a_property_trust(self):
        self.assertEqual(route({}, sic="6798")["business_class"], BR.REIT)

    def test_an_ordinary_company_is_ordinary(self):
        got = route({}, sic="3571")
        self.assertEqual(got["business_class"], BR.STANDARD)
        self.assertEqual(got["model"], "STANDARD")

    def test_a_loss_maker_has_no_denominator(self):
        got = route({}, sic="3571", eps=-1.0)
        self.assertEqual(got["business_class"], BR.UNPROFITABLE)


class TestHybrids(unittest.TestCase):
    def test_two_material_businesses_make_a_hybrid(self):
        got = route({"assets": 10000.0, "policyholder_liabilities": 2000.0},
                    phrases={"manager": ["money managed for others",
                                         "an asset management business"]})
        self.assertEqual(got["business_class"], BR.HYBRID)
        self.assertTrue(got["secondary"])

    def test_the_hybrid_names_both_businesses_and_their_measures(self):
        got = route({"assets": 10000.0, "policyholder_liabilities": 7000.0},
                    phrases={"manager": ["money managed for others",
                                         "an asset management business"]})
        said = " ".join(got["why"])
        self.assertIn("Insurer", said)
        self.assertIn("Asset manager", said)
        self.assertIn("each is valued a different way", said)

    def test_it_does_not_take_whichever_classifier_fires_first(self):
        """Both exposures appear in the profile, not just the winner."""
        got = route({"assets": 10000.0, "policyholder_liabilities": 2000.0},
                    phrases={"manager": ["money managed for others",
                                         "an asset management business"]})
        kinds = {e["business"] for e in got["exposures"] if e["material"]}
        self.assertIn(BR.INSURANCE, kinds)
        self.assertIn(BR.ASSET_MANAGER, kinds)

    def test_a_conglomerate_under_an_insurance_code_is_not_a_pure_insurer(self):
        """Berkshire files under an insurance code and describes a railway."""
        got = route({"assets": 10000.0, "insurance_reserves": 600.0},
                    sic="6331", phrases={"conglomerate": True})
        self.assertEqual(got["business_class"], BR.HYBRID)
        self.assertIn("unrelated businesses", " ".join(got["why"]))

    def test_a_conglomerate_with_ordinary_accounts_uses_the_ordinary_model(self):
        got = route({"assets": 10000.0, "insurance_reserves": 600.0},
                    sic="6331", phrases={"conglomerate": True})
        self.assertEqual(got["model"], "STANDARD")

    def test_an_insurer_whose_chapter_could_not_be_read_is_not_reclassified(self):
        fund = FakeFund({"assets": 10000.0}, CORPORATE)
        got = BR.route(fund, FACTS, sic="6331", phrases={"conglomerate": True},
                       text_confidence="LOW", eps_ttm=2.0)
        self.assertEqual(got["business_class"], BR.INSURANCE)


class TestCorporateAccounts(unittest.TestCase):
    def test_customer_money_running_through_cash_flow_fails_the_test(self):
        fund = FakeFund({}, BROKER_ACCOUNTS)
        got = BR.corporate_accounts(fund, FACTS)
        self.assertFalse(got["ok"])
        self.assertIn("belonging to customers", got["reason"])

    def test_no_capital_expenditure_fails_the_test(self):
        fund = FakeFund({}, {"revenue": 1000.0, "operating_cash_flow": 300.0})
        self.assertFalse(BR.corporate_accounts(fund, FACTS)["ok"])

    def test_an_operating_company_passes_and_says_why(self):
        got = BR.corporate_accounts(FakeFund({}, CORPORATE), FACTS)
        self.assertTrue(got["ok"])
        self.assertIn("own cash", got["basis"])

    def test_cash_flow_far_above_revenue_is_customer_money(self):
        fund = FakeFund({}, {"revenue": 1000.0, "operating_cash_flow": 2400.0,
                             "capex": 10.0})
        self.assertFalse(BR.corporate_accounts(fund, FACTS)["ok"])


class TestExplainability(unittest.TestCase):
    def test_every_decision_carries_its_evidence(self):
        got = route({"assets": 10000.0, "customer_receivables": 2000.0})
        for key in ("business_class", "label", "model", "confidence", "why",
                    "exposures", "corporate_accounts", "version"):
            self.assertIn(key, got)
        self.assertTrue(got["why"])

    def test_an_exposure_carries_the_measure_it_was_judged_on(self):
        got = route({"assets": 10000.0, "customer_receivables": 2000.0})
        row = next(e for e in got["exposures"]
                   if e["business"] == BR.BROKER and e["material"])
        self.assertIn("share of total assets", row["measure"])
        self.assertEqual(row["evidence"], "filed figures")
        self.assertIsNotNone(row["threshold_pct"])

    def test_a_text_exposure_says_it_came_from_the_chapter(self):
        got = route({"assets": 500.0},
                    phrases={"manager": ["money managed for others",
                                         "an asset management business"]})
        row = next(e for e in got["exposures"]
                   if e["evidence"] == "business description")
        self.assertIn("business chapter", row["measure"])


class TestPhraseEvidence(unittest.TestCase):
    def test_it_reduces_a_chapter_to_family_names(self):
        got = BR.phrase_evidence(
            "We operate a national securities exchange and a clearing house, "
            "and our electronic trading platform serves members worldwide.")
        self.assertIn("a market venue", got["exchange"])
        self.assertIn("a clearing house", got["exchange"])
        self.assertFalse(got["conglomerate"])

    def test_a_family_counts_once_however_often_it_appears(self):
        got = BR.phrase_evidence("stock exchange " * 40)
        self.assertEqual(got["exchange"], ["a market venue"])

    def test_a_conglomerate_is_recognised(self):
        got = BR.phrase_evidence(
            "a holding company owning subsidiaries engaged in numerous "
            "diverse business activities")
        self.assertTrue(got["conglomerate"])


if __name__ == "__main__":
    unittest.main()
