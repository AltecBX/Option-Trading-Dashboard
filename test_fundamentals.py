"""Tests for fundamentals.py — rebuilding company accounts from SEC XBRL.

Every fixture in this file reproduces a filing shape that was MEASURED on
live EDGAR before the module was written, and each test names the company
that taught it:

  · cumulative cash-flow statements          (every 10-Q filer)
  · 52/53-week retail fiscal calendars       (Costco: a 252-day Q3 year-to-date)
  · non-additive weighted-average shares     (Microsoft: 7.4bn, not 30bn)
  · a dead concept with a live sibling       (Robinhood: revenue under two tags)
  · restated comparatives                    (a 2021 quarter refiled in 2023)
  · stock splits                             (per-share history re-expressed)
  · a missing quarter                        (Exxon: no continuous year)
  · annual-only filers                       (Alibaba: Form 20-F, no quarters)
  · IFRS foreign private issuers             (TSMC in TWD, Novo Nordisk in DKK)
  · pre-revenue companies                    (Cingulate: no revenue concept)

Nothing here touches the network.
"""

import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import fundamentals as F


# ── fixture builders ───────────────────────────────────────────────────────

def fact(start, end, val, form="10-Q", filed="2026-01-01", fy=2026, fp="Q1"):
    return {"start": start, "end": end, "val": val, "form": form,
            "filed": filed, "fy": fy, "fp": fp}


def concept(unit, rows):
    return {"units": {unit: rows}}


def facts(us_gaap=None, dei=None, ifrs=None, name="Test Co", cik=1):
    out = {"entityName": name, "_cik": cik, "facts": {}}
    if us_gaap is not None:
        out["facts"]["us-gaap"] = us_gaap
    if dei is not None:
        out["facts"]["dei"] = dei
    if ifrs is not None:
        out["facts"]["ifrs-full"] = ifrs
    return out


def four_quarters(concept_name, unit, vals, year=2025, filed="2026-02-01"):
    """Four discrete calendar quarters, oldest first."""
    spans = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
             ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31")]
    rows = [fact(s, e, v, filed=filed) for (s, e), v in zip(spans, vals)]
    return {concept_name: concept(unit, rows)}


class TestQuarterClassification(unittest.TestCase):
    def test_calendar_quarters(self):
        self.assertEqual(F.quarters_spanned(90), 1)
        self.assertEqual(F.quarters_spanned(92), 1)
        self.assertEqual(F.quarters_spanned(181), 2)
        self.assertEqual(F.quarters_spanned(273), 3)
        self.assertEqual(F.quarters_spanned(365), 4)

    def test_retail_52_53_week_calendars(self):
        # Costco: 12-week quarters, a 16-week fourth quarter, and a 36-week
        # (252-day) third-quarter year-to-date. A fixed 273 +/- 20 window
        # rejects that 252 and the whole company returns nothing.
        self.assertEqual(F.quarters_spanned(84), 1)     # 12 weeks
        self.assertEqual(F.quarters_spanned(112), 1)    # 16 weeks
        self.assertEqual(F.quarters_spanned(168), 2)    # 24 weeks
        self.assertEqual(F.quarters_spanned(252), 3)    # 36 weeks
        self.assertEqual(F.quarters_spanned(364), 4)    # 52 weeks
        self.assertEqual(F.quarters_spanned(371), 4)    # 53 weeks

    def test_rejects_periods_that_are_not_whole_quarters(self):
        self.assertIsNone(F.quarters_spanned(31))       # a month
        self.assertIsNone(F.quarters_spanned(140))      # a transition stub
        self.assertIsNone(F.quarters_spanned(730))      # two years
        self.assertIsNone(F.quarters_spanned(0))


class TestLatestFiled(unittest.TestCase):
    def test_latest_filing_wins_the_value(self):
        # The same quarter, restated. The newer number is the right one.
        e = concept("USD", [
            fact("2025-01-01", "2025-03-31", 100.0, filed="2025-05-01"),
            fact("2025-01-01", "2025-03-31", 104.0, filed="2026-02-01"),
        ])
        rows = F.latest_filed(e, "USD")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["val"], 104.0)

    def test_first_filed_is_kept_separately(self):
        # Value from the newest filing (restated, split-adjusted); DATE from
        # the oldest (when the market actually learned it). Without this a
        # 2021 quarter plots at the 2023 annual report that repeated it.
        e = concept("USD", [
            fact("2021-07-01", "2021-09-30", 10.0, filed="2021-10-29"),
            fact("2021-07-01", "2021-09-30", 10.0, filed="2023-11-03"),
        ])
        row = F.latest_filed(e, "USD")[0]
        self.assertEqual(row["filed"], "2023-11-03")
        self.assertEqual(row["first_filed"], "2021-10-29")

    def test_split_restatement_lands_on_todays_share_basis(self):
        # A 10-for-1 split: the pre-split $6.53 becomes $0.653 in the next
        # filing. Keeping the newest version is what puts the whole per-share
        # history on the same basis as split-adjusted prices.
        e = concept("USD/shares", [
            fact("2024-01-01", "2024-03-31", 6.53, filed="2024-05-01"),
            fact("2024-01-01", "2024-03-31", 0.653, filed="2024-08-01"),
        ])
        self.assertAlmostEqual(F.latest_filed(e, "USD/shares")[0]["val"], 0.653)

    def test_skips_instants_and_null_values(self):
        e = concept("USD", [{"end": "2025-03-31", "val": 5.0, "filed": "x"},
                            fact("2025-01-01", "2025-03-31", None)])
        self.assertEqual(F.latest_filed(e, "USD"), [])


class TestDiscreteQuarters(unittest.TestCase):
    def test_income_statement_quarters_pass_through(self):
        rows = F.latest_filed(concept("USD", [
            fact("2025-01-01", "2025-03-31", 10.0),
            fact("2025-04-01", "2025-06-30", 12.0)]), "USD")
        qs = F.discrete_quarters(rows)
        self.assertEqual([q["val"] for q in qs], [10.0, 12.0])
        self.assertFalse(any(q["derived"] for q in qs))

    def test_cumulative_cash_flow_is_differenced(self):
        # Cash-flow statements are year-to-date in every 10-Q: Q1 3 months,
        # Q2 6 months, Q3 9 months. Filtering for ~90-day periods finds only
        # Q1, and free cash flow comes out empty for almost every company.
        rows = F.latest_filed(concept("USD", [
            fact("2025-01-01", "2025-03-31", 100.0),
            fact("2025-01-01", "2025-06-30", 260.0),
            fact("2025-01-01", "2025-09-30", 400.0),
            fact("2025-01-01", "2025-12-31", 610.0)]), "USD")
        qs = F.discrete_quarters(rows)
        self.assertEqual([round(q["val"], 6) for q in qs], [100.0, 160.0, 140.0, 210.0])
        self.assertEqual([q["derived"] for q in qs], [False, True, True, True])

    def test_missing_fourth_quarter_recovered_from_the_annual_figure(self):
        rows = F.latest_filed(concept("USD", [
            fact("2025-01-01", "2025-03-31", 10.0),
            fact("2025-04-01", "2025-06-30", 11.0),
            fact("2025-07-01", "2025-09-30", 12.0),
            fact("2025-01-01", "2025-09-30", 33.0),
            fact("2025-01-01", "2025-12-31", 47.0, form="10-K")]), "USD")
        qs = F.discrete_quarters(rows)
        self.assertAlmostEqual(qs[-1]["val"], 14.0)
        self.assertEqual(qs[-1]["end"], "2025-12-31")

    def test_averages_are_never_differenced(self):
        # A six-month weighted-average share count minus a three-month one is
        # not a quarter of anything. Microsoft's four quarterly counts summed
        # would claim 30 billion shares outstanding instead of 7.4 billion.
        rows = F.latest_filed(concept("shares", [
            fact("2025-01-01", "2025-03-31", 7.45e9),
            fact("2025-01-01", "2025-06-30", 7.44e9)]), "shares")
        qs = F.discrete_quarters(rows, aggregate="mean")
        self.assertEqual(len(qs), 1)
        self.assertAlmostEqual(qs[0]["val"], 7.45e9)


class TestTTM(unittest.TestCase):
    QS = [{"start": "2025-01-01", "end": "2025-03-31", "val": 10.0, "filed": "2025-05-01"},
          {"start": "2025-04-01", "end": "2025-06-30", "val": 11.0, "filed": "2025-08-01"},
          {"start": "2025-07-01", "end": "2025-09-30", "val": 12.0, "filed": "2025-11-01"},
          {"start": "2025-10-01", "end": "2025-12-31", "val": 13.0, "filed": "2026-02-01"}]

    def test_sums_the_last_four_quarters(self):
        t = F.ttm(self.QS)
        self.assertAlmostEqual(t["value"], 46.0)
        self.assertEqual(t["period_end"], "2025-12-31")
        self.assertEqual(t["filed"], "2026-02-01")

    def test_mean_aggregate_averages_instead(self):
        t = F.ttm(self.QS, aggregate="mean")
        self.assertAlmostEqual(t["value"], 11.5)

    def test_needs_four_quarters(self):
        self.assertIsNone(F.ttm(self.QS[:3]))

    def test_rejects_a_discontinuous_year(self):
        # Exxon's Company Facts is currently missing 2025-09-30. Without the
        # continuity test the sum of what remains would be presented as a
        # twelve-month figure spanning fifteen months.
        broken = [self.QS[0], self.QS[1], self.QS[3],
                  {"start": "2026-01-01", "end": "2026-03-31", "val": 14.0,
                   "filed": "2026-05-01"}]
        self.assertIsNone(F.ttm(broken))

    def test_as_of_walks_the_series_back(self):
        t = F.ttm(self.QS, as_of="2025-09-30")
        self.assertIsNone(t)                      # only three quarters by then

    def test_first_filed_is_when_the_last_quarter_became_public(self):
        qs = [dict(q, first_filed=q["filed"]) for q in self.QS]
        self.assertEqual(F.ttm(qs)["first_filed"], "2026-02-01")


class TestConceptSelection(unittest.TestCase):
    def test_coverage_and_recency_beat_the_preference_order(self):
        # Robinhood tags revenue under `Revenues`; its
        # RevenueFromContractWithCustomer… series has 4 points and stops in
        # 2021. A fixed priority list picks the dead one.
        gaap = {}
        gaap.update(four_quarters("Revenues", "USD", [1.0, 2.0, 3.0, 4.0]))
        gaap["RevenueFromContractWithCustomerExcludingAssessedTax"] = concept("USD", [
            fact("2021-01-01", "2021-03-31", 99.0, filed="2021-05-01")])
        got = F.pick_concept(gaap, "revenue")
        self.assertEqual(got[0], "Revenues")

    def test_preference_order_breaks_a_genuine_tie(self):
        gaap = {}
        gaap.update(four_quarters("Revenues", "USD", [1.0, 2.0, 3.0, 4.0]))
        gaap.update(four_quarters("RevenueFromContractWithCustomerExcludingAssessedTax",
                                  "USD", [1.0, 2.0, 3.0, 4.0]))
        self.assertEqual(F.pick_concept(gaap, "revenue")[0],
                         "RevenueFromContractWithCustomerExcludingAssessedTax")

    def test_wrong_unit_is_ignored(self):
        gaap = {"Revenues": concept("EUR", [fact("2025-01-01", "2025-03-31", 1.0)])}
        self.assertIsNone(F.pick_concept(gaap, "revenue"))


class TestEligibility(unittest.TestCase):
    def test_us_gaap_filer_is_eligible(self):
        self.assertTrue(F.eligibility(facts(us_gaap={"NetIncomeLoss": {}}))["ok"])

    def test_ifrs_filer_is_refused_with_its_currency_named(self):
        # Novo Nordisk reports in DKK; TSMC in TWD. The ADR ratio and the FX
        # rate are not in the filings, so a per-share figure lined up against
        # a US dollar ADR price would be fiction.
        f = facts(ifrs={"Revenue": concept("DKK", [
            fact("2025-01-01", "2025-12-31", 3.09e11)])})
        elig = F.eligibility(f)
        self.assertFalse(elig["ok"])
        self.assertIn("DKK", elig["reason"])
        self.assertIn("ADR", elig["reason"])

    def test_missing_filer_is_refused(self):
        elig = F.eligibility(None)
        self.assertFalse(elig["ok"])
        self.assertIn("ETFs", elig["reason"])


class TestMetric(unittest.TestCase):
    def _good(self):
        gaap = {}
        gaap.update(four_quarters("Revenues", "USD", [100.0, 110.0, 120.0, 130.0]))
        gaap.update(four_quarters("NetIncomeLoss", "USD", [10.0, 11.0, 12.0, 13.0]))
        gaap.update(four_quarters("EarningsPerShareDiluted", "USD/shares",
                                  [0.10, 0.11, 0.12, 0.13]))
        gaap.update(four_quarters("WeightedAverageNumberOfDilutedSharesOutstanding",
                                  "shares", [100.0, 100.0, 100.0, 100.0]))
        return facts(us_gaap=gaap)

    def test_builds_a_ttm_with_full_provenance(self):
        m = F.metric(self._good(), "revenue")
        self.assertAlmostEqual(m["value"], 460.0)
        self.assertEqual(m["concept"], "Revenues")
        self.assertEqual(m["period_end"], "2025-12-31")
        self.assertIn("SEC EDGAR", m["source"])
        self.assertIn("trailing twelve months", m["basis"])
        self.assertEqual(m["reason"], "")

    def test_shares_use_the_average_not_the_sum(self):
        m = F.metric(self._good(), "diluted_shares")
        self.assertAlmostEqual(m["value"], 100.0)

    def test_missing_concept_says_so_rather_than_returning_zero(self):
        # Cingulate is pre-revenue: no revenue concept at all. Zero would be
        # a lie; N/A with the reason is the answer.
        m = F.metric(facts(us_gaap={}), "revenue")
        self.assertIsNone(m["value"])
        self.assertIn("does not report", m["reason"])

    def test_annual_only_filer_gets_its_own_reason(self):
        # Alibaba files a 20-F once a year. The concept exists; quarters
        # do not, so a trailing-twelve-month figure cannot be built.
        gaap = {"Revenues": concept("USD", [
            fact("2024-04-01", "2025-03-31", 1.0e11, form="20-F", filed="2025-06-01"),
            fact("2023-04-01", "2024-03-31", 9.0e10, form="20-F", filed="2024-06-01")])}
        m = F.metric(facts(us_gaap=gaap), "revenue")
        self.assertIsNone(m["value"])
        self.assertIn("only once a year", m["reason"])

    def test_too_little_history_counts_what_it_has(self):
        gaap = {"Revenues": concept("USD", [
            fact("2025-01-01", "2025-03-31", 1.0),
            fact("2025-04-01", "2025-06-30", 2.0)])}
        m = F.metric(facts(us_gaap=gaap), "revenue")
        self.assertIsNone(m["value"])
        self.assertIn("Only 2 quarters", m["reason"])

    def test_a_gap_in_the_year_is_reported_as_such(self):
        gaap = {"Revenues": concept("USD", [
            fact("2024-10-01", "2024-12-31", 1.0),
            fact("2025-01-01", "2025-03-31", 2.0),
            fact("2025-04-01", "2025-06-30", 3.0),
            # 2025-09-30 missing, exactly like Exxon today
            fact("2025-10-01", "2025-12-31", 4.0)])}
        m = F.metric(facts(us_gaap=gaap), "revenue")
        self.assertIsNone(m["value"])
        self.assertIn("continuous year", m["reason"])

    def test_annual_fallback_for_average_metrics(self):
        gaap = {"WeightedAverageNumberOfDilutedSharesOutstanding": concept("shares", [
            fact("2025-01-01", "2025-12-31", 5.0e8, form="10-K", filed="2026-02-01")])}
        m = F.metric(facts(us_gaap=gaap), "diluted_shares")
        self.assertAlmostEqual(m["value"], 5.0e8)


class TestSharesOutstanding(unittest.TestCase):
    def test_prefers_the_cover_page_count(self):
        f = facts(us_gaap={}, dei={"EntityCommonStockSharesOutstanding": concept(
            "shares", [{"end": "2026-07-17", "val": 1.459e10, "filed": "2026-07-31"}])})
        out = F.shares_outstanding(f)
        self.assertAlmostEqual(out["value"], 1.459e10)
        self.assertIn("cover page", out["basis"])

    def test_multi_class_filers_fall_back_and_say_so(self):
        # Robinhood and Shopify report the cover-page count PER SHARE CLASS,
        # and SEC Company Facts drops the per-class breakdown entirely.
        gaap = four_quarters("WeightedAverageNumberOfDilutedSharesOutstanding",
                             "shares", [9.2e8, 9.2e8, 9.2e8, 9.2e8])
        out = F.shares_outstanding(facts(us_gaap=gaap))
        self.assertAlmostEqual(out["value"], 9.2e8)
        self.assertIn("per share class", out["basis"])

    def test_nothing_on_file_is_an_explicit_na(self):
        out = F.shares_outstanding(facts(us_gaap={}))
        self.assertIsNone(out["value"])
        self.assertIn("No share count", out["reason"])


class TestFreeCashFlow(unittest.TestCase):
    def _fund(self, with_capex=True):
        gaap = {}
        gaap.update(four_quarters("Revenues", "USD", [100.0] * 4))
        gaap.update(four_quarters("NetCashProvidedByUsedInOperatingActivities",
                                  "USD", [50.0] * 4))
        if with_capex:
            gaap.update(four_quarters("PaymentsToAcquirePropertyPlantAndEquipment",
                                      "USD", [20.0] * 4))
        return facts(us_gaap=gaap)

    def test_operating_cash_minus_capital_spending(self):
        f = self._fund()
        ocf = F.metric(f, "operating_cash_flow")["value"]
        cap = F.metric(f, "capex")["value"]
        self.assertAlmostEqual(ocf - cap, 120.0)

    def test_banks_without_capital_spending_get_na_not_zero(self):
        # JPMorgan reports no comparable capital-expenditure line. Treating
        # the missing half as zero would print operating cash flow as if it
        # were free cash flow.
        m = F.metric(self._fund(with_capex=False), "capex")
        self.assertIsNone(m["value"])


class TestTTMSeries(unittest.TestCase):
    def test_points_carry_the_date_the_market_learned_them(self):
        gaap = {"EarningsPerShareDiluted": concept("USD/shares", [
            fact("2024-10-01", "2024-12-31", 1.0, filed="2025-02-01"),
            fact("2025-01-01", "2025-03-31", 1.1, filed="2025-05-01"),
            fact("2025-04-01", "2025-06-30", 1.2, filed="2025-08-01"),
            fact("2025-07-01", "2025-09-30", 1.3, filed="2025-11-01"),
            fact("2025-10-01", "2025-12-31", 1.4, filed="2026-02-01"),
            # the annual report restates the whole set
            fact("2024-10-01", "2024-12-31", 1.0, filed="2026-02-01")])}
        series = F.ttm_series(facts(us_gaap=gaap), "eps")
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0]["period_end"], "2025-09-30")
        self.assertEqual(series[0]["first_filed"], "2025-11-01")
        self.assertAlmostEqual(series[0]["value"], 4.6)


class TestBusinessProfile(unittest.TestCase):
    def test_item_1_is_taken_over_its_contents_entry(self):
        text = ("MICROSOFT CORPORATION FORM 10-K INDEX PART I "
                "Item 1. Business 3 Item 1A. Risk Factors 14 Item 2. Properties 31 "
                "PART I ITEM 1. BUSINESS GENERAL Microsoft is a technology company. "
                "Our mission is to empower every person. We develop software. "
                "ITEM 1A. RISK FACTORS Our business faces risks.")
        body = F._item1_body(text)
        self.assertTrue(body.startswith("Microsoft is a technology company"))
        self.assertNotIn("Risk Factors 14", body)

    def test_drop_cap_headings_still_match(self):
        # Microsoft sets the first letter of a heading as a drop cap in its
        # own element, so the flattened text reads "ITEM 1. B USINESS".
        text = ("PART I ITEM 1. B USINESS Microsoft is a technology company. "
                "We build software. It is sold worldwide. "
                "ITEM 1A. R ISK FACTORS risks follow")
        self.assertTrue(F._item1_body(text).startswith("Microsoft is a technology"))

    def test_reference_boilerplate_is_dropped(self):
        text = ("Item 1. Business In this Annual Report on Form 10-K, references "
                "to the Company refer to Realty Income Corporation. THE COMPANY "
                "Realty Income is a real estate partner. We own properties. "
                "We were founded in 1969. Item 1A. Risk Factors x")
        body = F._item1_body(text)
        self.assertTrue(body.startswith("Realty Income is a real estate partner"))

    def test_sentence_trimming_never_cuts_mid_sentence(self):
        out = F._trim_sentences("One two three. Four five six. Seven eight.", 20)
        self.assertEqual(out, "One two three.")

    def test_zero_width_characters_are_stripped(self):
        self.assertEqual(F._trim_sentences("​Plug is building.", 100),
                         "Plug is building.")


class TestMoatTags(unittest.TestCase):
    def test_ranks_by_how_often_the_language_appears(self):
        text = ("Our patents and patent portfolio protect our proprietary "
                "technology. We rely on patents. Subscription revenue is "
                "recurring revenue.")
        tags = F.moat_tags(text)
        self.assertEqual(tags[0], "Patents & intellectual property")
        self.assertIn("Recurring & subscription revenue", tags)

    def test_capped_at_three(self):
        text = ("patent patent intellectual property FDA-approved regulatory "
                "approval subscription recurring revenue network effect "
                "switching cost economies of scale brand recognition "
                "installed base distribution network")
        self.assertLessEqual(len(F.moat_tags(text)), 3)

    def test_no_tags_rather_than_a_score(self):
        self.assertEqual(F.moat_tags("We sell widgets."), [])
        self.assertEqual(F.moat_tags(""), [])


class TestFundamentalsRollUp(unittest.TestCase):
    def test_refused_filer_fills_every_metric_with_the_same_reason(self):
        f = facts(ifrs={"Revenue": concept("TWD", [
            fact("2024-01-01", "2024-12-31", 2.89e12)])})
        F._MEM["ZZTEST"] = (__import__("time").time(), f)
        try:
            out = F.fundamentals("ZZTEST")
            self.assertFalse(out["ok"])
            self.assertIn("TWD", out["reason"])
            for name in F.CONCEPTS:
                self.assertIsNone(out["metrics"][name]["value"])
                self.assertIn("TWD", out["metrics"][name]["reason"])
        finally:
            F._MEM.pop("ZZTEST", None)

    def test_minus_a_year_handles_leap_days(self):
        self.assertTrue(F._minus_a_year("2024-02-29").startswith("2023-03-0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
