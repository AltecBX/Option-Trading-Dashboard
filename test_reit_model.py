"""Tests for reit_model — the funds-from-operations reconstruction, the
property type, and the property-trust fair-value methods.

Four claims this panel makes in writing are asserted here rather than trusted:

  1. Funds from operations is RECONSTRUCTED, and the reconstruction knows
     when it is incomplete. A quarter with no tagged gain and a quarter where
     nothing was sold produce the same zero and mean opposite things, so they
     are counted separately.
  2. An INCOMPLETE reconstruction costs the valuation confidence, which
     lowers the buy zone. The warning is priced in, not merely printed.
  3. ADJUSTED funds from operations is refused. Always, with the measurement
     behind the refusal.
  4. The distribution-yield history is CONTEXT: it can appear on screen and
     can never become the answer.
"""

import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import fair_value as FV
import fundamentals as F
import reit_model as RM


def quarters(values, start_year=2023):
    out, y, q = [], start_year, 0
    months = [("01-01", "03-31"), ("04-01", "06-30"),
              ("07-01", "09-30"), ("10-01", "12-31")]
    for v in values:
        s, e = months[q]
        out.append({"start": f"{y}-{s}", "end": f"{y}-{e}", "val": v,
                    "filed": f"{y}-{e[:2]}-20", "form": "10-Q"})
        q += 1
        if q == 4:
            q, y = 0, y + 1
    return out


def reit_facts(n=12, income=100.0, dep=250.0, gains=None, impair=None,
               shares=100.0, dps=1.0, debt=None):
    """A property trust that earns `income` and depreciates `dep` a quarter.

    With no gains and no impairments tagged, funds from operations comes to
    (income + dep) × 4 over the trailing year.
    """
    gaap = {
        "NetIncomeLossAvailableToCommonStockholdersBasic":
            {"units": {"USD": quarters([income] * n)}},
        "DepreciationAndAmortization":
            {"units": {"USD": quarters([dep] * n)}},
        "WeightedAverageNumberOfDilutedSharesOutstanding":
            {"units": {"shares": quarters([shares] * n)}},
        "CommonStockDividendsPerShareDeclared":
            {"units": {"USD/shares": quarters([dps] * n)}},
    }
    if gains is not None:
        gaap["GainLossOnSaleOfProperties"] = {"units": {"USD": quarters(gains)}}
    if impair is not None:
        gaap["ImpairmentOfRealEstate"] = {"units": {"USD": quarters(impair)}}
    if debt is not None:
        gaap["LongTermDebt"] = {"units": {"USD": [
            {"end": "2025-12-31", "val": debt, "filed": "2026-02-01"}]}}
        gaap["CashAndCashEquivalentsAtCarryingValue"] = {"units": {"USD": [
            {"end": "2025-12-31", "val": 0.0, "filed": "2026-02-01"}]}}
    return {"facts": {"us-gaap": gaap}}


class TestFundsFromOperations(unittest.TestCase):
    def test_depreciation_is_added_back_to_income(self):
        ffo = RM.funds_from_operations(F, reit_facts())
        # Four quarters of 100 + 250.
        self.assertAlmostEqual(ffo["value"], 1400.0)
        self.assertIn("reconstructed", ffo["basis"])
        self.assertIn("NOT the trust's own published figure", ffo["basis"])

    def test_gains_are_removed_and_impairments_added_back(self):
        ffo = RM.funds_from_operations(
            F, reit_facts(gains=[20.0] * 12, impair=[5.0] * 12))
        # (100 + 250 − 20 + 5) × 4.
        self.assertAlmostEqual(ffo["value"], 1340.0)
        self.assertTrue(ffo["complete"])
        self.assertEqual(ffo["caveat"], "")

    def test_untagged_gains_make_the_reconstruction_incomplete(self):
        ffo = RM.funds_from_operations(F, reit_facts())
        self.assertFalse(ffo["complete"])
        self.assertIn("INCOMPLETE", ffo["caveat"])
        self.assertIn("reads high", ffo["caveat"])
        self.assertEqual(ffo["components"]["gain_quarters_tagged"], 0)

    def test_a_partly_tagged_gain_is_counted_partly(self):
        # Gains tagged for the first eight quarters only, so the trailing
        # four have none.
        facts = reit_facts()
        facts["facts"]["us-gaap"]["GainLossOnSaleOfProperties"] = {
            "units": {"USD": quarters([20.0] * 8)}}
        ffo = RM.funds_from_operations(F, facts)
        self.assertFalse(ffo["complete"])
        self.assertEqual(ffo["components"]["gain_quarters_tagged"], 0)

    def test_missing_depreciation_refuses_outright(self):
        facts = reit_facts()
        del facts["facts"]["us-gaap"]["DepreciationAndAmortization"]
        ffo = RM.funds_from_operations(F, facts)
        self.assertIsNone(ffo["value"])
        self.assertIn("largest add-back", ffo["reason"])

    def test_too_little_history_refuses(self):
        ffo = RM.funds_from_operations(F, reit_facts(n=3))
        self.assertIsNone(ffo["value"])
        self.assertIn("Fewer than four quarters", ffo["reason"])


class TestAdjustedFFO(unittest.TestCase):
    def test_it_is_always_refused_with_the_measurement_behind_it(self):
        a = RM.adjusted_ffo(F, reit_facts())
        self.assertIsNone(a["value"])
        self.assertIn("recurring maintenance", a["reason"])
        self.assertIn("only thirteen tag", a["reason"])

    def test_the_metrics_panel_refuses_it_too(self):
        m = RM.metrics(F, reit_facts(), price=50.0, shares_outstanding=100.0)
        self.assertIsNone(m["price_to_affo"]["value"])
        self.assertIsNone(m["payout_of_affo_pct"]["value"])
        self.assertTrue(m["price_to_affo"]["reason"])


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.m = RM.metrics(F, reit_facts(gains=[20.0] * 12,
                                          impair=[5.0] * 12, debt=4000.0),
                            price=50.0, shares_outstanding=100.0,
                            property_type="RETAIL")

    def test_per_share_and_multiple(self):
        self.assertTrue(self.m["available"])
        self.assertAlmostEqual(self.m["ffo_per_share"]["value"], 13.4)
        self.assertAlmostEqual(self.m["price_to_ffo"]["value"], 50.0 / 13.4,
                               places=6)

    def test_distribution_and_payout(self):
        self.assertAlmostEqual(self.m["dividends_per_share"]["value"], 4.0)
        self.assertAlmostEqual(self.m["dividend_yield_pct"]["value"], 8.0)
        self.assertAlmostEqual(self.m["payout_of_ffo_pct"]["value"],
                               4.0 / 13.4 * 100.0, places=6)

    def test_a_high_payout_is_flagged_but_not_condemned(self):
        m = RM.metrics(F, reit_facts(income=10.0, dep=20.0, dps=7.0,
                                     gains=[0.0] * 12, impair=[0.0] * 12),
                       price=50.0, shares_outstanding=100.0)
        self.assertEqual(m["payout_flag"]["level"], "HIGH")
        self.assertIn("must distribute", m["payout_flag"]["reason"])

    def test_what_is_not_net_debt_to_ebitdare_is_not_called_that(self):
        block = self.m["net_debt_to_ffo"]
        self.assertIsNotNone(block["value"])
        self.assertIn("NOT net debt to EBITDAre", block["basis"])

    def test_occupancy_and_same_store_are_refused_with_reasons(self):
        for key in ("occupancy", "same_store_noi_growth_pct"):
            self.assertIsNone(self.m[key]["value"])
            self.assertIn("tagged", self.m[key]["reason"])

    def test_the_property_type_label_is_spelled_out(self):
        self.assertEqual(self.m["property_type"], "RETAIL")
        self.assertIn("Shops", self.m["property_type_label"])

    def test_a_wild_swing_in_an_incomplete_reconstruction_is_called_out(self):
        # Income triples in the newest year with no gains tagged.
        facts = reit_facts()
        facts["facts"]["us-gaap"]["NetIncomeLossAvailableToCommonStockholdersBasic"] = \
            {"units": {"USD": quarters([100.0] * 8 + [900.0] * 4)}}
        m = RM.metrics(F, facts, price=50.0, shares_outstanding=100.0)
        self.assertTrue(m["reconstruction_warning"])
        self.assertIn("one-off gain", m["reconstruction_warning"])

    def test_a_complete_reconstruction_is_never_warned_about(self):
        self.assertEqual(self.m["reconstruction_warning"], "")


class TestPointInTime(unittest.TestCase):
    def test_each_reading_is_dated_at_the_filing_that_stated_it(self):
        s = RM.point_in_time_series(F, reit_facts())
        pts = s["ffo_per_share"]
        self.assertEqual(len(pts), 9)          # twelve quarters, four-quarter windows
        for p in pts:
            self.assertTrue(p["first_filed"])
            self.assertAlmostEqual(p["value"], 14.0, places=6)

    def test_growth_needs_five_readings(self):
        out = RM.metrics(F, reit_facts(n=6), price=50.0,
                         shares_outstanding=100.0)
        self.assertIsNone(out["ffo_growth_pct"]["value"])
        self.assertIn("needs five", out["ffo_growth_pct"]["reason"])


class TestPropertyTypePeers(unittest.TestCase):
    def _rows(self, n_match, n_other):
        rows = [{"symbol": f"M{i}", "price_to_ffo": 15.0 + i,
                 "property_type": "DATA CENTER"} for i in range(n_match)]
        rows += [{"symbol": f"O{i}", "price_to_ffo": 10.0 + i,
                  "property_type": "RETAIL"} for i in range(n_other)]
        return rows

    def test_enough_matched_trusts_narrows_the_comparison(self):
        out = RM.property_type_peers(self._rows(6, 6), "DATA CENTER")
        self.assertTrue(out["matched"])
        self.assertEqual(out["n"], 6)
        self.assertIn("same kind of building", out["reason"])

    def test_too_few_matched_trusts_widens_and_says_so(self):
        out = RM.property_type_peers(self._rows(2, 8), "DATA CENTER")
        self.assertFalse(out["matched"])
        self.assertEqual(out["n"], 10)
        self.assertIn("looser", out["reason"])

    def test_an_unclassified_trust_widens_rather_than_guesses(self):
        out = RM.property_type_peers(self._rows(6, 6), None)
        self.assertFalse(out["matched"])
        self.assertIn("does not say clearly enough", out["reason"])


class TestPropertyTypeClassifier(unittest.TestCase):
    def test_a_clear_leader_is_classified(self):
        text = ("We own and operate data centers. Our data centers provide "
                "colocation and interconnection services across our data "
                "center portfolio of data centers.")
        got, scores = F.property_type(text)
        self.assertEqual(got, "DATA CENTER")
        self.assertGreaterEqual(scores["DATA CENTER"], 3)

    def test_a_close_runner_up_declines_rather_than_guesses(self):
        text = ("We own data centers and data centers and data centers. "
                "We also own industrial properties, industrial properties "
                "and industrial properties.")
        got, _s = F.property_type(text)
        self.assertIsNone(got)

    def test_a_thin_mention_is_not_a_classification(self):
        got, _s = F.property_type("We occasionally lease an office building.")
        self.assertIsNone(got)

    def test_empty_text_declines(self):
        self.assertEqual(F.property_type(""), (None, {}))

    def test_every_label_has_plain_english(self):
        for key in F.PROPERTY_TYPES:
            self.assertTrue(F.PROPERTY_TYPE_LABELS.get(key))


class TestReitFairValue(unittest.TestCase):
    def _build(self, complete=True, history=None):
        facts = reit_facts(gains=[20.0] * 12 if complete else None,
                           impair=[5.0] * 12 if complete else None)
        reit = RM.metrics(F, facts, price=50.0, shares_outstanding=100.0,
                          property_type="RETAIL")
        hist = {"raw_values": {
            "price_to_ffo": {"5y": history or [12.0 + (i % 100) / 50.0
                                               for i in range(1200)]},
            "dividend_yield_pct": {"5y": [5.0 + (i % 100) / 100.0
                                          for i in range(1200)]},
        }}
        peers = {"multiples": [13.0, 14.0, 15.0, 16.0, 17.0, 18.0],
                 "base_multiple": 15.5, "level": "DIRECT PEERS",
                 "matched": True, "property_type": "RETAIL",
                 "reason": "matched"}
        return reit, RM.methods(reit, hist, peers)

    def test_every_method_declares_it_was_built_for_a_property_trust(self):
        _r, ms = self._build()
        for m in ms:
            self.assertEqual(m["specialized_for"], "REIT")

    def test_a_property_trust_is_valued_rather_than_refused(self):
        _r, ms = self._build()
        out = FV.fair_value(ms, price=50.0,
                            business_type={"type": "REIT", "note": ""})
        self.assertTrue(out["available"])
        self.assertIsNotNone(out["buy_zone"])

    def test_the_distribution_history_never_becomes_the_answer(self):
        _r, ms = self._build()
        ctx = next(m for m in ms if m["key"] == "reit_dividend_yield")
        self.assertTrue(ctx["available"])
        self.assertTrue(ctx["context_only"])
        out = FV.fair_value(ms, price=50.0,
                            business_type={"type": "REIT", "note": ""})
        self.assertNotEqual(out["base_method"], "reit_dividend_yield")
        # It takes no part in the range or the disagreement either.
        self.assertEqual(out["n_methods"], 2)

    def test_a_context_only_method_alone_is_not_a_valuation(self):
        ctx = [m for m in self._build()[1] if m["key"] == "reit_dividend_yield"]
        out = FV.fair_value(ctx, price=50.0,
                            business_type={"type": "REIT", "note": ""})
        self.assertFalse(out["available"])

    def test_an_incomplete_reconstruction_lowers_the_buy_zone(self):
        good_r, good = self._build(complete=True)
        bad_r, bad = self._build(complete=False)
        cap_g, why_g = RM.confidence_cap(good_r)
        cap_b, why_b = RM.confidence_cap(bad_r)
        self.assertIsNone(cap_g)
        self.assertEqual(cap_b, "LOW")
        self.assertIn("lowers the price", why_b)
        a = FV.fair_value(good, price=50.0,
                          business_type={"type": "REIT", "note": ""})
        b = FV.fair_value(bad, price=50.0,
                          business_type={"type": "REIT", "note": ""},
                          confidence_cap=cap_b, confidence_cap_reason=why_b)
        self.assertEqual(b["confidence_level"], "LOW")
        self.assertLess(b["confidence_credit"], a["confidence_credit"])

    def test_the_confidence_cap_never_raises_confidence(self):
        conf = {"level": "LOW", "spread": 0.7, "reason": "x"}
        self.assertEqual(FV.cap_confidence(conf, "HIGH")["level"], "LOW")
        self.assertEqual(FV.cap_confidence(conf, None)["level"], "LOW")
        self.assertEqual(FV.cap_confidence(conf, "UNRELIABLE")["level"],
                         "UNRELIABLE")

    def test_the_incomplete_method_ranks_below_the_complete_one(self):
        _g, good = self._build(complete=True)
        _b, bad = self._build(complete=False)
        g = next(m for m in good if m["key"] == "reit_self_pffo")
        b = next(m for m in bad if m["key"] == "reit_self_pffo")
        self.assertGreater(g["confidence_rank"], b["confidence_rank"])

    def test_a_low_multiple_is_the_pessimistic_end(self):
        _r, ms = self._build()
        m = next(x for x in ms if x["key"] == "reit_self_pffo")
        self.assertLess(m["detail"]["multiple_bear"], m["detail"]["multiple_bull"])
        self.assertLess(m["bear"], m["bull"])


if __name__ == "__main__":                            # pragma: no cover
    unittest.main()
