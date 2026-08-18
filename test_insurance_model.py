"""Tests for insurance_model — subtype, underwriting, reserves and the
insurer fair-value methods.

Five claims this panel makes in writing are asserted here rather than
trusted:

  1. The SUBTYPE decides which numbers exist. Claims over premiums is a loss
     ratio for a property-casualty insurer and is refused outright for a life
     insurer, whose premiums leave out most of what it earns.
  2. A ratio is only computed when its numerator and denominator cover the
     SAME twelve months. Allstate's premium series stops in 2018 and its
     claims series runs to today; dividing them gives 123%.
  3. The combined ratio is built from the actual underwriting expense or not
     at all. It is never assembled from whatever cost line happens to exist.
  4. ADVERSE reserve development lowers the price this screen will pay,
     through the confidence mechanism, rather than adding a warning beside a
     number it did not change.
  5. An insurer whose kind cannot be established is refused.
"""

import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import fair_value as FV
import fundamentals as F
import insurance_model as IM


# ── a filer built by hand, so every assertion has one cause ────────────────

def _e(unit, rows):
    return {"units": {unit: rows}}


def quarters(value, n=12, start_year=2023):
    out, y, q = [], start_year, 0
    months = [("01-01", "03-31"), ("04-01", "06-30"),
              ("07-01", "09-30"), ("10-01", "12-31")]
    for _ in range(n):
        s, e = months[q]
        out.append({"start": f"{y}-{s}", "end": f"{y}-{e}", "val": value,
                    "filed": f"{y}-{e[:2]}-15", "form": "10-Q"})
        q += 1
        if q == 4:
            q, y = 0, y + 1
    return out


def instants(values, start_year=2023):
    out, y, q = [], start_year, 0
    ends = ["03-31", "06-30", "09-30", "12-31"]
    for v in values:
        out.append({"end": f"{y}-{ends[q]}", "val": v,
                    "filed": f"{y}-{ends[q]}", "form": "10-Q"})
        q += 1
        if q == 4:
            q, y = 0, y + 1
    return out


def insurer_facts(premiums=1_000_000_000.0, losses=650_000_000.0,
                  dac=100_000_000.0, other_uw=120_000_000.0,
                  development=-20_000_000.0, equity=8_000_000_000.0,
                  net_income=400_000_000.0, shares=100_000_000.0,
                  goodwill=500_000_000.0, reserves=6_000_000_000.0,
                  investments=20_000_000_000.0, nii=180_000_000.0,
                  assets=30_000_000_000.0, stale_premiums=False):
    gaap = {
        "StockholdersEquity": _e("USD", instants([equity] * 12)),
        "Goodwill": _e("USD", instants([goodwill] * 12)),
        "Assets": _e("USD", instants([assets] * 12)),
        "NetIncomeLossAvailableToCommonStockholdersBasic":
            _e("USD", quarters(net_income)),
        "WeightedAverageNumberOfDilutedSharesOutstanding":
            _e("shares", quarters(shares)),
        "PolicyholderBenefitsAndClaimsIncurredNet": _e("USD", quarters(losses)),
        "LiabilityForClaimsAndClaimsAdjustmentExpense":
            _e("USD", instants([reserves] * 12)),
        "Investments": _e("USD", instants([investments] * 12)),
        "NetInvestmentIncome": _e("USD", quarters(nii)),
    }
    if premiums is not None:
        # A stale premium series is the Allstate case: it stops years before
        # the claims series it would be divided by.
        gaap["PremiumsEarnedNet"] = _e(
            "USD", quarters(premiums, n=4, start_year=2018) if stale_premiums
            else quarters(premiums))
    if dac is not None:
        gaap["DeferredPolicyAcquisitionCostAmortizationExpense"] = _e(
            "USD", quarters(dac))
    if other_uw is not None:
        gaap["OtherUnderwritingExpense"] = _e("USD", quarters(other_uw))
    if development is not None:
        gaap["SupplementalInformationForPropertyCasualtyInsuranceUnderwriters"
             "PriorYearClaimsAndClaimsAdjustmentExpense"] = _e(
                 "USD", quarters(development))
    return {"facts": {"us-gaap": gaap}}


def metrics(subtype="P&C", price=60.0, **kw):
    return IM.metrics(F, insurer_facts(**kw), price=price,
                      shares_outstanding=100_000_000.0, subtype=subtype)


# ── subtype classification ─────────────────────────────────────────────────

class TestSubtypeClassification(unittest.TestCase):
    def _text(self, **counts):
        parts = []
        parts += ["property and casualty insurance"] * counts.get("pc", 0)
        parts += ["life insurance products"] * counts.get("life", 0)
        parts += ["health insurance plans"] * counts.get("health", 0)
        parts += ["we act as a reinsurer for ceding companies"] * counts.get("reins", 0)
        return ". ".join(parts) + "."

    def test_property_casualty_is_recognised(self):
        sub, hits = F.insurer_subtype(self._text(pc=20), sic=6331)
        self.assertEqual(sub, "P&C")
        self.assertGreaterEqual(hits["pc"], 20)

    def test_life_is_recognised(self):
        self.assertEqual(F.insurer_subtype(self._text(life=20), 6311)[0], "LIFE")

    def test_health_plans_are_recognised_by_code_and_text_together(self):
        self.assertEqual(
            F.insurer_subtype(self._text(health=20), 6324)[0], "HEALTH")

    def test_reinsurance_beats_property_casualty_when_it_dominates(self):
        sub, _ = F.insurer_subtype(self._text(pc=9, reins=20), 6331)
        self.assertEqual(sub, "REINSURANCE")

    def test_both_life_and_property_casualty_is_multiline(self):
        sub, _ = F.insurer_subtype(self._text(pc=15, life=15), 6331)
        self.assertEqual(sub, "MULTILINE")

    def test_a_life_insurer_selling_health_cover_stays_a_life_insurer(self):
        # Globe Life, Unum and Aflac mention both about equally and their
        # economics are a life insurer's. Only the industry code settles it.
        sub, _ = F.insurer_subtype(self._text(life=20, health=22), 6311)
        self.assertEqual(sub, "LIFE")

    def test_a_health_plan_code_with_no_health_language_is_refused(self):
        self.assertIsNone(F.insurer_subtype(self._text(pc=20), 6324)[0])

    def test_a_thin_mention_classifies_nothing(self):
        self.assertIsNone(F.insurer_subtype(self._text(pc=3), 6331)[0])

    def test_no_annual_report_classifies_nothing(self):
        self.assertEqual(F.insurer_subtype("", 6331), (None, {}))

    def test_an_unclassifiable_insurer_is_refused_outright(self):
        m = IM.metrics(F, insurer_facts(), price=60.0,
                       shares_outstanding=100_000_000.0, subtype=None)
        self.assertFalse(m["available"])
        self.assertIn("what kind of insurer", m["reason"].lower())
        # And the refusal explains WHY the subtype matters, not just that it
        # is missing.
        self.assertIn("loss ratio", m["reason"])


# ── compatibility ──────────────────────────────────────────────────────────

class TestCompatibility(unittest.TestCase):
    def test_same_period_requires_both_ends_to_match(self):
        a = {"value": 1.0, "period_start": "2025-01-01", "period_end": "2025-12-31"}
        b = {"value": 2.0, "period_start": "2025-01-01", "period_end": "2025-12-31"}
        self.assertTrue(IM.same_period(a, b))
        self.assertFalse(IM.same_period(a, {**b, "period_end": "2024-12-31"}))
        self.assertFalse(IM.same_period(a, {**b, "period_start": "2024-01-01"}))
        self.assertFalse(IM.same_period(a, {**b, "value": None}))

    def test_a_stale_premium_series_refuses_the_loss_ratio(self):
        m = metrics(stale_premiums=True)
        self.assertIsNone(m["loss_ratio_pct"]["value"])
        self.assertIn("same twelve months", m["loss_ratio_pct"]["reason"])
        self.assertIn("date mismatch", m["loss_ratio_pct"]["reason"])

    def test_a_matched_pair_computes(self):
        m = metrics()
        self.assertAlmostEqual(m["loss_ratio_pct"]["value"], 65.0, places=6)

    def test_a_zero_denominator_is_refused_rather_than_dividing(self):
        r = IM.ratio_of({"value": 5.0, "period_start": "a", "period_end": "b"},
                        {"value": 0.0, "period_start": "a", "period_end": "b"},
                        "basis")
        self.assertIsNone(r["value"])
        self.assertIn("not positive", r["reason"])


# ── property-casualty arithmetic ───────────────────────────────────────────

class TestUnderwriting(unittest.TestCase):
    def test_loss_expense_and_combined_ratios(self):
        m = metrics()
        self.assertAlmostEqual(m["loss_ratio_pct"]["value"], 65.0, places=6)
        self.assertAlmostEqual(m["expense_ratio_pct"]["value"], 22.0, places=6)
        self.assertAlmostEqual(m["combined_ratio_pct"]["value"], 87.0, places=6)
        self.assertTrue(m["combined_ratio_pct"]["underwriting_profitable"])

    def test_underwriting_profit_is_premiums_times_one_minus_combined(self):
        m = metrics()
        # 4bn of premiums at an 87% combined ratio leaves 13% of it.
        self.assertAlmostEqual(m["underwriting_profit"]["value"],
                               4_000_000_000.0 * 0.13, places=0)

    def test_a_combined_ratio_above_a_hundred_is_called_a_loss(self):
        m = metrics(losses=900_000_000.0)
        self.assertGreater(m["combined_ratio_pct"]["value"], 100.0)
        self.assertFalse(m["combined_ratio_pct"]["underwriting_profitable"])

    def test_missing_underwriting_expense_refuses_the_combined_ratio(self):
        m = metrics(other_uw=None)
        self.assertIsNone(m["expense_ratio_pct"]["value"])
        self.assertIsNone(m["combined_ratio_pct"]["value"])
        self.assertIsNone(m["underwriting_profit"]["value"])
        # And the refusal names the shortcut it is refusing to take.
        why = m["combined_ratio_pct"]["reason"]
        self.assertIn("total benefits and expenses less claims", why)
        self.assertIn("unrelated concepts", why)
        # The loss ratio still stands: it has both of its own pieces.
        self.assertAlmostEqual(m["loss_ratio_pct"]["value"], 65.0, places=6)


class TestLifeAndHealth(unittest.TestCase):
    def test_a_life_insurer_gets_no_loss_ratio_at_all(self):
        m = metrics(subtype="LIFE")
        for k in ("loss_ratio_pct", "expense_ratio_pct", "combined_ratio_pct",
                  "underwriting_profit", "acquisition_cost_ratio_pct"):
            self.assertIsNone(m[k]["value"], k)
        why = m["loss_ratio_pct"]["reason"]
        self.assertIn("interest credited to policyholder accounts", why)
        self.assertIn("MetLife", why)

    def test_a_life_insurer_still_gets_book_value_and_returns(self):
        m = metrics(subtype="LIFE")
        self.assertTrue(m["available"])
        self.assertIsNotNone(m["price_to_book"]["value"])
        self.assertIsNotNone(m["return_on_equity_pct"]["value"])
        self.assertIsNotNone(m["investment_yield_pct"]["value"])

    def test_a_health_insurer_gets_a_benefit_ratio_and_no_combined_ratio(self):
        m = metrics(subtype="HEALTH")
        self.assertAlmostEqual(m["loss_ratio_pct"]["value"], 65.0, places=6)
        self.assertIn("Benefit ratio", m["loss_ratio_pct"]["basis"])
        self.assertIsNone(m["combined_ratio_pct"]["value"])
        self.assertIn("health insurers", m["combined_ratio_pct"]["reason"])

    def test_future_policy_benefits_are_asked_for_only_where_they_belong(self):
        self.assertIn("property-casualty insurer holds reserves",
                      metrics()["future_policy_benefits"]["reason"])
        # For a life insurer it is a real lookup, so an absent concept reads
        # as absent rather than as inapplicable.
        life = metrics(subtype="LIFE")["future_policy_benefits"]
        self.assertNotIn("does not apply", life.get("reason", ""))


# ── reserves ───────────────────────────────────────────────────────────────

class TestReserves(unittest.TestCase):
    def test_favourable_development_is_negative_and_named(self):
        m = metrics()
        self.assertAlmostEqual(
            m["reserve_development_pct_premiums"]["value"], -2.0, places=6)
        self.assertEqual(m["reserve_development_state"]["state"], "FAVOURABLE")

    def test_adverse_development_is_positive_and_named(self):
        m = metrics(development=30_000_000.0)
        self.assertEqual(m["reserve_development_state"]["state"], "ADVERSE")
        self.assertIn("under-estimated what it already owed",
                      m["reserve_development_state"]["basis"])

    def test_a_small_movement_either_way_is_ordinary(self):
        m = metrics(development=5_000_000.0)          # 0.5% of premiums
        self.assertEqual(m["reserve_development_state"]["state"],
                         "BROADLY NEUTRAL")

    def test_an_untagged_development_series_is_reported_as_unknown(self):
        m = metrics(development=None)
        self.assertIsNone(m["reserve_development_pct_premiums"]["value"])
        self.assertIsNone(m["reserve_development_state"]["state"])
        self.assertIn("does not tag",
                      m["reserve_development_pct_premiums"]["reason"])

    def test_reserves_to_premiums_is_a_multiple_not_a_percentage(self):
        m = metrics()
        # 6bn of reserves against 4bn of trailing premiums.
        self.assertAlmostEqual(m["reserves_to_premiums"]["value"], 1.5, places=6)
        self.assertIn("long-tail", m["reserves_to_premiums"]["basis"])


# ── book value and returns ─────────────────────────────────────────────────

class TestBookAndReturns(unittest.TestCase):
    def test_book_and_tangible_book_per_share(self):
        m = metrics()
        self.assertAlmostEqual(m["book_per_share"]["value"], 80.0, places=6)
        self.assertAlmostEqual(m["tangible_book_per_share"]["value"], 75.0,
                               places=6)

    def test_price_to_book_and_price_to_tangible_book(self):
        m = metrics(price=120.0)
        self.assertAlmostEqual(m["price_to_book"]["value"], 1.5, places=6)
        self.assertAlmostEqual(m["price_to_tangible_book"]["value"], 1.6,
                               places=6)

    def test_return_on_equity_uses_average_equity(self):
        m = metrics()
        # 1.6bn of trailing profit on 8bn of equity.
        self.assertAlmostEqual(m["return_on_equity_pct"]["value"], 20.0,
                               places=6)

    def test_capital_is_named_for_what_it_is_and_not_a_risk_based_ratio(self):
        m = metrics()
        self.assertIn("NOT a risk-based capital ratio",
                      m["equity_to_assets_pct"]["basis"])

    def test_book_value_growth_needs_five_readings(self):
        m = metrics()
        self.assertIsNotNone(m["book_value_per_share_trend_pct"]["value"])


# ── peers ──────────────────────────────────────────────────────────────────

class TestPeers(unittest.TestCase):
    def _rows(self, n_match, n_other):
        rows = [{"symbol": f"M{i}", "subtype": "P&C", "price_to_book": 1.5,
                 "return_on_equity_pct": 15.0} for i in range(n_match)]
        rows += [{"symbol": f"L{i}", "subtype": "LIFE", "price_to_book": 0.8,
                  "return_on_equity_pct": 9.0} for i in range(n_other)]
        return rows

    def test_enough_matched_insurers_narrows_the_group(self):
        out = IM.subtype_peers(self._rows(6, 4), "P&C")
        self.assertTrue(out["matched"])
        self.assertEqual(out["n"], 6)
        self.assertTrue(all(r["subtype"] == "P&C" for r in out["rows"]))

    def test_too_few_matched_insurers_widens_and_says_so(self):
        out = IM.subtype_peers(self._rows(2, 6), "P&C")
        self.assertFalse(out["matched"])
        self.assertEqual(out["n"], 8)
        self.assertIn("do not trade at the same multiple", out["reason"])

    def test_an_unclassified_subject_cannot_match(self):
        out = IM.subtype_peers(self._rows(6, 4), None)
        self.assertFalse(out["matched"])
        self.assertIn("own subtype could not be established", out["reason"])

    def test_peer_inputs_drop_unusable_multiples(self):
        got = IM.peer_inputs([{"price_to_book": 1.2, "return_on_equity_pct": 10},
                              {"price_to_book": 0.0},
                              {"price_to_book": None}])
        self.assertEqual(got["n"], 1)
        self.assertEqual(got["pb_multiples"], [1.2])


# ── fair value ─────────────────────────────────────────────────────────────

class TestInsurerFairValue(unittest.TestCase):
    def _methods(self, ins=None, peers=None, ten_year=4.0):
        ins = ins or {**metrics(), "eps_ttm": 16.0}
        return IM.methods(ins, {"raw_values": {}}, peers or {}, ten_year, {})

    def test_every_method_is_stamped_for_insurers(self):
        for m in self._methods():
            self.assertEqual(m.get("specialized_for"), "INSURANCE")

    def test_the_justified_multiple_is_the_return_over_the_cost_of_equity(self):
        m = next(x for x in self._methods() if x["key"] == "insurance_justified")
        self.assertTrue(m["available"])
        # ROE 20, cost of equity 4 + 5 = 9, growth 3 → (20-3)/(9-3) = 2.833×
        self.assertAlmostEqual(m["detail"]["multiple_base"], 17.0 / 6.0,
                               places=6)
        self.assertAlmostEqual(m["base"], 80.0 * 17.0 / 6.0, places=4)

    def test_an_insurer_earning_its_cost_of_equity_is_worth_its_book(self):
        ins = {**metrics(net_income=180_000_000.0), "eps_ttm": 7.2}
        # 720m on 8bn of equity is 9%, exactly the cost of equity below.
        m = next(x for x in IM.methods(ins, {"raw_values": {}}, {}, 4.0, {})
                 if x["key"] == "insurance_justified")
        self.assertAlmostEqual(m["detail"]["multiple_base"], 1.0, places=6)

    def test_a_missing_treasury_yield_refuses_the_justified_method(self):
        m = next(x for x in self._methods(ten_year=None)
                 if x["key"] == "insurance_justified")
        self.assertFalse(m["available"])
        self.assertIn("ten-year Treasury", m["reason"])

    def test_the_peer_method_needs_a_real_group(self):
        m = next(x for x in self._methods(peers={"pb_multiples": [1.2, 1.4],
                                                 "level": "DIRECT PEERS"})
                 if x["key"] == "insurance_peers_pb")
        self.assertFalse(m["available"])
        self.assertIn("fewer than", m["reason"])

    def test_the_peer_method_prices_off_profitability_where_it_holds(self):
        peers = {"pb_multiples": [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2],
                 "roe_pcts": [6, 8, 10, 12, 14, 16, 18, 20],
                 "level": "DIRECT PEERS", "matched": True}
        m = next(x for x in self._methods(peers=peers)
                 if x["key"] == "insurance_peers_pb")
        self.assertTrue(m["available"])
        self.assertTrue(m["detail"]["fitted"])
        # This insurer earns 20%, the top of the group, so it should be
        # priced ABOVE the group median of 1.5.
        self.assertGreater(m["detail"]["multiple_base"], 1.5)

    def test_the_peer_note_talks_about_insurers_and_book_value(self):
        # The regression is shared with the bank model, and its wording was
        # a bank's: an insurer's screen explained itself in terms of banks
        # and tangible book, which is a sentence about a different company.
        peers = {"pb_multiples": [1.5] * 9, "roe_pcts": [12.0] * 9,
                 "level": "DIRECT PEERS", "matched": True}
        m = next(x for x in self._methods(peers=peers)
                 if x["key"] == "insurance_peers_pb")
        note = (m.get("detail") or {}).get("note") or ""
        self.assertIn("insurers", note)
        self.assertIn("price to book", note)
        self.assertNotIn("banks", note)
        self.assertNotIn("tangible book", note)

    def test_the_buy_zone_is_the_credited_value_less_the_margin_of_safety(self):
        out = FV.fair_value(self._methods(), price=90.0,
                            business_type={"type": "INSURANCE"})
        self.assertTrue(out["available"])
        conf = FV.cfg_get({}, "confidence_credit")[out["confidence_level"]]
        credited = out["bear"] + conf * (out["base"] - out["bear"])
        self.assertAlmostEqual(out["credited"], credited, places=6)
        self.assertAlmostEqual(
            out["buy_zone"],
            credited * (1 - FV.cfg_get({}, "min_margin_of_safety")),
            places=6)


class TestConfidenceCap(unittest.TestCase):
    def test_adverse_development_holds_confidence_at_low(self):
        cap, why = IM.confidence_cap(metrics(development=30_000_000.0))
        self.assertEqual(cap, "LOW")
        self.assertIn("proving inadequate", why)

    def test_favourable_development_caps_nothing(self):
        self.assertEqual(IM.confidence_cap(metrics())[0], None)

    def test_the_cap_actually_lowers_the_buy_zone(self):
        ins = {**metrics(development=30_000_000.0), "eps_ttm": 16.0}
        methods = IM.methods(ins, {"raw_values": {}}, {}, 4.0, {})
        cap, why = IM.confidence_cap(ins)
        capped = FV.fair_value(methods, price=90.0,
                               business_type={"type": "INSURANCE"},
                               confidence_cap=cap, confidence_cap_reason=why)
        plain = FV.fair_value(methods, price=90.0,
                              business_type={"type": "INSURANCE"})
        self.assertLessEqual(capped["buy_zone"], plain["buy_zone"])


# ── risk flags ─────────────────────────────────────────────────────────────

class TestRiskSignals(unittest.TestCase):
    def test_adverse_development_fires(self):
        sig = IM.risk_signals(metrics(development=30_000_000.0))
        self.assertTrue(sig["adverse_reserve_development"]["active"])

    def test_favourable_development_does_not_fire(self):
        sig = IM.risk_signals(metrics())
        self.assertFalse(sig["adverse_reserve_development"]["active"])

    def test_an_underwriting_loss_fires(self):
        sig = IM.risk_signals(metrics(losses=900_000_000.0))
        self.assertTrue(sig["underwriting_loss"]["active"])
        self.assertIn("relies on its investments",
                      sig["underwriting_loss"]["detail"])

    def test_shrinking_premiums_fire(self):
        ins = metrics()
        ins["premium_growth_pct"] = {"value": -4.0, "reason": ""}
        self.assertTrue(IM.risk_signals(ins)["premiums_contracting"]["active"])

    def test_a_falling_return_needs_a_year_earlier_reading(self):
        ins = metrics()
        self.assertNotIn("roe_falling", IM.risk_signals(ins))
        self.assertTrue(IM.risk_signals(ins, prior_roe=30.0)["roe_falling"]["active"])

    def test_there_is_no_combined_insurance_risk_score(self):
        sig = IM.risk_signals(metrics(development=30_000_000.0))
        for v in sig.values():
            self.assertIn("active", v)
            self.assertIsInstance(v["active"], bool)
        self.assertNotIn("score", sig)


# ── junk input ─────────────────────────────────────────────────────────────

class TestJunkInput(unittest.TestCase):
    def test_empty_facts_refuse_rather_than_raise(self):
        m = IM.metrics(F, {"facts": {"us-gaap": {}}}, price=10.0,
                       shares_outstanding=1.0, subtype="P&C")
        self.assertFalse(m["available"])
        self.assertTrue(m["reason"])

    def test_no_price_leaves_the_multiples_blank_without_raising(self):
        m = IM.metrics(F, insurer_facts(), price=None,
                       shares_outstanding=100_000_000.0, subtype="P&C")
        self.assertIsNone(m["price_to_book"]["value"])
        self.assertIsNotNone(m["book_per_share"]["value"])

    def test_a_nonsense_subtype_is_refused(self):
        m = IM.metrics(F, insurer_facts(), price=60.0,
                       shares_outstanding=1e8, subtype="BANANA")
        self.assertFalse(m["available"])


if __name__ == "__main__":                             # pragma: no cover
    unittest.main()
