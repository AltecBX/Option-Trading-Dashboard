"""Tests for broker_model — the broker-dealer test, book value, operating
economics and the broker fair-value methods.

Four claims this panel makes in writing are asserted here rather than
trusted:

  1. Whether a filer is a BROKER-DEALER at all is answered from its balance
     sheet, not from its industry code. Code 6211 holds Charles Schwab,
     Goldman Sachs and BlackRock; only two of those three are brokers.
  2. CLIENT ASSETS are not in the filings and stay blank. They are never
     estimated from the balance sheet.
  3. Leverage divides consolidated assets by consolidated equity. Dividing
     them by the parent's own equity puts Interactive Brokers at 42 times
     against a group figure nearer 14.
  4. A broker with a high return on equity is priced above its book and one
     with a low return below it, by formula rather than by assertion.
"""

import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import broker_model as BK
import fair_value as FV
import fundamentals as F


# ── a filer built by hand ──────────────────────────────────────────────────

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


def broker_facts(equity=5_000_000_000.0, total_equity=None,
                 assets=60_000_000_000.0, net_income=250_000_000.0,
                 shares=100_000_000.0, goodwill=200_000_000.0,
                 revenue=1_500_000_000.0, comp=600_000_000.0,
                 nii=500_000_000.0, receivables=3_000_000_000.0,
                 segregated=8_000_000_000.0, commissions=300_000_000.0,
                 deposits=None, stale_evidence=False):
    gaap = {
        "StockholdersEquity": _e("USD", instants([equity] * 12)),
        "Goodwill": _e("USD", instants([goodwill] * 12)),
        "Assets": _e("USD", instants([assets] * 12)),
        "NetIncomeLossAvailableToCommonStockholdersBasic":
            _e("USD", quarters(net_income)),
        "WeightedAverageNumberOfDilutedSharesOutstanding":
            _e("shares", quarters(shares)),
        "Revenues": _e("USD", quarters(revenue)),
    }
    if total_equity is not None:
        gaap["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"] = \
            _e("USD", instants([total_equity] * 12))
    if comp is not None:
        gaap["LaborAndRelatedExpense"] = _e("USD", quarters(comp))
    if nii is not None:
        gaap["InterestIncomeExpenseNet"] = _e("USD", quarters(nii))
    start = 2010 if stale_evidence else 2023
    if receivables is not None:
        gaap["ReceivablesFromCustomers"] = _e(
            "USD", instants([receivables] * 12, start_year=start))
    if segregated is not None:
        gaap["CashAndSecuritiesSegregatedUnderFederalAndOtherRegulations"] = _e(
            "USD", instants([segregated] * 12, start_year=start))
    if commissions is not None:
        gaap["BrokerageCommissionsRevenue"] = _e(
            "USD", quarters(commissions, start_year=start))
    if deposits is not None:
        gaap["Deposits"] = _e("USD", instants([deposits] * 12))
    return {"facts": {"us-gaap": gaap}}


def metrics(subtype="RETAIL", price=100.0, **kw):
    return BK.metrics(F, broker_facts(**kw), price=price,
                      shares_outstanding=100_000_000.0, subtype=subtype)


# ── is it a broker at all ──────────────────────────────────────────────────

class TestBrokerEvidence(unittest.TestCase):
    def test_a_real_broker_clears_the_test(self):
        ev = BK.broker_evidence(F, broker_facts())
        self.assertTrue(ev["is_broker"])
        keys = {e["key"] for e in ev["evidence"]}
        self.assertIn("customer_receivables", keys)
        self.assertIn("segregated_cash", keys)
        self.assertIn("brokerage_commissions", keys)

    def test_an_asset_manager_does_not(self):
        # No customer money and no brokerage revenue: BlackRock, T. Rowe
        # Price, Invesco. Every one of them sits in a broker industry code.
        ev = BK.broker_evidence(
            F, broker_facts(receivables=None, segregated=None,
                            commissions=None))
        self.assertFalse(ev["is_broker"])
        self.assertIn("no receivables from customers", ev["reason"])
        self.assertIn("also covers exchanges and asset managers", ev["reason"])

    def test_one_piece_of_evidence_is_enough(self):
        ev = BK.broker_evidence(
            F, broker_facts(receivables=None, segregated=None))
        self.assertTrue(ev["is_broker"])

    def test_stale_evidence_does_not_count(self):
        # A firm that held customer money fifteen years ago and does not now
        # is not a broker today. Evercore and PJT Partners tag investment
        # banking revenue whose series stopped in 2018.
        ev = BK.broker_evidence(F, broker_facts(stale_evidence=True))
        self.assertFalse(ev["is_broker"])

    def test_a_rounding_line_of_customer_cash_does_not_count(self):
        # Ameriprise Financial parks nine hundred million dollars of
        # segregated cash against a hundred and ninety-eight billion of
        # assets. It is a wealth and insurance group, not a broker-dealer.
        ev = BK.broker_evidence(
            F, broker_facts(assets=200_000_000_000.0, receivables=None,
                            segregated=900_000_000.0, commissions=None))
        self.assertFalse(ev["is_broker"])

    def test_material_customer_cash_does_count(self):
        ev = BK.broker_evidence(
            F, broker_facts(assets=60_000_000_000.0, receivables=None,
                            segregated=8_000_000_000.0, commissions=None))
        self.assertTrue(ev["is_broker"])
        self.assertAlmostEqual(
            ev["evidence"][0]["share_of_assets_pct"], 100.0 / 7.5, places=4)

    def test_revenue_evidence_is_not_measured_against_the_balance_sheet(self):
        # Commissions are a broker's revenue whatever the balance sheet
        # looks like, so the materiality test does not apply to them.
        ev = BK.broker_evidence(
            F, broker_facts(assets=200_000_000_000.0, receivables=None,
                            segregated=None, commissions=300_000_000.0))
        self.assertTrue(ev["is_broker"])

    def test_a_filer_that_fails_the_test_is_refused_with_the_reason(self):
        m = BK.metrics(F, broker_facts(receivables=None, segregated=None,
                                       commissions=None),
                       price=100.0, shares_outstanding=1e8, subtype="RETAIL")
        self.assertFalse(m["available"])
        self.assertIsNone(m["subtype"])
        self.assertIn("category error", m["reason"])


class TestBrokerSubtype(unittest.TestCase):
    def _text(self, retail=0, institutional=0):
        return ". ".join(["retail brokerage accounts"] * retail
                         + ["institutional securities and investment banking"]
                         * institutional) + "."

    def test_retail_dominant(self):
        self.assertEqual(F.broker_subtype(self._text(retail=20))[0], "RETAIL")

    def test_institutional_dominant(self):
        self.assertEqual(
            F.broker_subtype(self._text(institutional=20))[0], "INSTITUTIONAL")

    def test_both_strong_is_diversified(self):
        self.assertEqual(
            F.broker_subtype(self._text(retail=12, institutional=12))[0],
            "DIVERSIFIED")

    def test_an_unreadable_report_leaves_the_mix_undetermined_not_refused(self):
        # Unlike an insurer's subtype, this does not change which numbers
        # are valid — so the model still runs.
        self.assertEqual(F.broker_subtype("")[0], "UNDETERMINED")
        m = metrics(subtype="UNDETERMINED")
        self.assertTrue(m["available"])
        self.assertIn("mix not determined", m["subtype_label"])


# ── book value and returns ─────────────────────────────────────────────────

class TestBookAndReturns(unittest.TestCase):
    def test_book_and_tangible_book_per_share(self):
        m = metrics()
        self.assertAlmostEqual(m["book_per_share"]["value"], 50.0, places=6)
        self.assertAlmostEqual(m["tangible_book_per_share"]["value"], 48.0,
                               places=6)

    def test_price_to_book_and_price_to_tangible_book(self):
        m = metrics(price=100.0)
        self.assertAlmostEqual(m["price_to_book"]["value"], 2.0, places=6)
        self.assertAlmostEqual(m["price_to_tangible_book"]["value"],
                               100.0 / 48.0, places=6)

    def test_return_on_equity(self):
        m = metrics()
        # 1bn of trailing profit on 5bn of equity.
        self.assertAlmostEqual(m["return_on_equity_pct"]["value"], 20.0,
                               places=6)


class TestLeverage(unittest.TestCase):
    def test_leverage_uses_consolidated_equity_on_both_sides(self):
        # A group whose public company owns a quarter of it: consolidated
        # assets of 60bn against parent equity of 5bn would read 12 times,
        # and against the group's own 20bn reads 3.
        m = metrics(total_equity=20_000_000_000.0)
        self.assertAlmostEqual(m["assets_to_equity"]["value"], 3.0, places=6)
        self.assertIn("minority interests included",
                      m["assets_to_equity"]["basis"])

    def test_book_value_per_share_still_uses_the_parent_equity(self):
        m = metrics(total_equity=20_000_000_000.0)
        self.assertAlmostEqual(m["book_per_share"]["value"], 50.0, places=6)

    def test_a_broker_with_no_minority_interests_is_unaffected(self):
        m = metrics()
        self.assertAlmostEqual(m["assets_to_equity"]["value"], 12.0, places=6)


class TestBankingOperation(unittest.TestCase):
    def test_material_deposits_are_flagged(self):
        m = metrics(deposits=30_000_000_000.0)      # half the balance sheet
        self.assertTrue(m["has_banking_operation"])
        self.assertIn("a material part of what it does is banking",
                      m["banking_note"])

    def test_no_deposits_is_said_plainly(self):
        m = metrics()
        self.assertFalse(m["has_banking_operation"])
        self.assertIn("funded as a broker rather than as a bank",
                      m["banking_note"])

    def test_small_deposits_are_not_material(self):
        m = metrics(deposits=1_000_000_000.0)       # under two percent
        self.assertFalse(m["has_banking_operation"])
        self.assertIn("not a material part", m["banking_note"])


# ── operating economics ────────────────────────────────────────────────────

class TestOperating(unittest.TestCase):
    def test_compensation_ratio(self):
        m = metrics()
        self.assertAlmostEqual(m["compensation_ratio_pct"]["value"], 40.0,
                               places=6)

    def test_net_interest_share_of_revenue(self):
        m = metrics()
        self.assertAlmostEqual(
            m["net_interest_share_of_revenue_pct"]["value"], 100.0 / 3.0,
            places=6)
        self.assertIn("closer to a bank",
                      m["net_interest_share_of_revenue_pct"]["basis"])

    def test_transaction_revenue_says_it_is_a_floor(self):
        m = metrics()
        self.assertAlmostEqual(m["transaction_revenue"]["value"],
                               1_200_000_000.0, places=0)
        self.assertIn("floor rather than a total",
                      m["transaction_revenue"]["basis"])

    def test_untagged_operating_profit_is_reported_as_missing(self):
        m = metrics()
        self.assertIsNone(m["operating_margin_pct"]["value"])
        self.assertTrue(m["operating_margin_pct"]["reason"])

    def test_gross_interest_income_is_never_called_net(self):
        m = metrics(nii=None)
        self.assertIsNone(m["net_interest_income"]["value"])
        self.assertIn("not the same figure", m["net_interest_income"]["reason"])


class TestClientAssets(unittest.TestCase):
    def test_client_assets_are_blank_with_the_measurement_behind_it(self):
        m = metrics()
        for k in ("client_assets", "client_asset_growth_pct", "net_new_assets"):
            self.assertIsNone(m[k]["value"], k)
            self.assertIn("not in the machine-readable filings", m[k]["reason"])
        self.assertIn("2020", m["client_assets"]["reason"])

    def test_they_are_never_estimated_from_the_balance_sheet(self):
        m = metrics()
        self.assertIn("says nothing about how much of its customers' money",
                      m["client_assets"]["reason"])

    def test_advisory_fees_are_refused_separately(self):
        m = metrics()
        self.assertIsNone(m["asset_management_revenue"]["value"])
        self.assertIn("not tagged separately",
                      m["asset_management_revenue"]["reason"])


# ── peers ──────────────────────────────────────────────────────────────────

class TestPeers(unittest.TestCase):
    def _rows(self, n_match, n_other):
        rows = [{"symbol": f"R{i}", "subtype": "RETAIL", "price_to_book": 2.0,
                 "return_on_equity_pct": 18.0} for i in range(n_match)]
        rows += [{"symbol": f"I{i}", "subtype": "INSTITUTIONAL",
                  "price_to_book": 1.2, "return_on_equity_pct": 11.0}
                 for i in range(n_other)]
        return rows

    def test_enough_matched_brokers_narrows(self):
        out = BK.subtype_peers(self._rows(6, 3), "RETAIL")
        self.assertTrue(out["matched"])
        self.assertEqual(out["n"], 6)

    def test_too_few_widens_and_says_so(self):
        out = BK.subtype_peers(self._rows(2, 6), "RETAIL")
        self.assertFalse(out["matched"])
        self.assertEqual(out["n"], 8)

    def test_an_undetermined_mix_cannot_match(self):
        out = BK.subtype_peers(self._rows(6, 3), "UNDETERMINED")
        self.assertFalse(out["matched"])
        self.assertIn("could not be read from its annual report",
                      out["reason"])


# ── fair value ─────────────────────────────────────────────────────────────

class TestBrokerFairValue(unittest.TestCase):
    def _methods(self, brk=None, peers=None, ten_year=4.0):
        brk = brk or {**metrics(), "eps_ttm": 10.0}
        return BK.methods(brk, {"raw_values": {}}, peers or {}, ten_year, {})

    def test_every_method_is_stamped_for_brokers(self):
        for m in self._methods():
            self.assertEqual(m.get("specialized_for"), "BROKER")

    def test_the_justified_multiple_prices_profitability(self):
        m = next(x for x in self._methods() if x["key"] == "broker_justified")
        self.assertTrue(m["available"])
        # ROE 20, cost of equity 9, growth 3 → (20-3)/(9-3).
        self.assertAlmostEqual(m["detail"]["multiple_base"], 17.0 / 6.0,
                               places=6)

    def test_a_low_return_earns_a_discount_to_book(self):
        brk = {**metrics(net_income=75_000_000.0), "eps_ttm": 3.0}
        m = next(x for x in BK.methods(brk, {"raw_values": {}}, {}, 4.0, {})
                 if x["key"] == "broker_justified")
        # 300m on 5bn is 6%, below the 9% cost of equity.
        self.assertLess(m["detail"]["multiple_base"], 1.0)
        self.assertLess(m["base"], 50.0)

    def test_a_broker_earning_below_its_growth_rate_refuses_rather_than_zeroes(self):
        brk = {**metrics(net_income=25_000_000.0), "eps_ttm": 1.0}
        m = next(x for x in BK.methods(brk, {"raw_values": {}}, {}, 4.0, {})
                 if x["key"] == "broker_justified")
        self.assertFalse(m["available"])
        self.assertIn("does not fit", m["reason"])

    def test_the_wider_range_reflects_a_broker_swinging_more(self):
        m = next(x for x in self._methods() if x["key"] == "broker_justified")
        self.assertIn("−4 and +3", m["detail"]["note"])

    def test_the_peer_note_talks_about_brokers_and_book_value(self):
        peers = {"pb_multiples": [2.0] * 9, "roe_pcts": [15.0] * 9,
                 "level": "DIRECT PEERS", "matched": True}
        m = next(x for x in self._methods(peers=peers)
                 if x["key"] == "broker_peers_pb")
        note = (m.get("detail") or {}).get("note") or ""
        self.assertIn("brokers", note)
        self.assertIn("price to book", note)
        self.assertNotIn("banks", note)
        self.assertNotIn("tangible book", note)

    def test_the_buy_zone_comes_out_of_the_shared_engine(self):
        out = FV.fair_value(self._methods(), price=100.0,
                            business_type={"type": "BROKER"})
        self.assertTrue(out["available"])
        self.assertIsNotNone(out["buy_zone"])
        self.assertLess(out["buy_zone"], out["credited"])


class TestConfidenceCap(unittest.TestCase):
    def test_a_retail_broker_is_capped_for_unknown_client_assets(self):
        cap, why = BK.confidence_cap(metrics(subtype="RETAIL"))
        self.assertEqual(cap, "MODERATE")
        self.assertIn("customer base", why)

    def test_an_institutional_broker_is_not(self):
        self.assertIsNone(BK.confidence_cap(metrics(subtype="INSTITUTIONAL"))[0])

    def test_an_unavailable_broker_caps_nothing(self):
        self.assertEqual(BK.confidence_cap({"available": False}), (None, ""))


# ── risk flags ─────────────────────────────────────────────────────────────

class TestRiskSignals(unittest.TestCase):
    def test_shrinking_revenue_fires(self):
        brk = metrics()
        brk["revenue_growth_pct"] = {"value": -6.0, "reason": ""}
        self.assertTrue(BK.risk_signals(brk)["revenue_contracting"]["active"])

    def test_rising_leverage_needs_both_a_move_and_a_level(self):
        brk = metrics()
        brk["assets_to_equity"] = {"value": 18.0, "reason": ""}
        self.assertTrue(BK.risk_signals(
            brk, prior={"assets_to_equity": 10.0})["leverage_rising"]["active"])
        # The same move at a modest level is not a flag: a broker is levered
        # by design.
        brk["assets_to_equity"] = {"value": 8.0, "reason": ""}
        self.assertFalse(BK.risk_signals(
            brk, prior={"assets_to_equity": 5.0})["leverage_rising"]["active"])

    def test_a_rising_compensation_ratio_fires(self):
        brk = metrics()
        self.assertTrue(BK.risk_signals(
            brk, prior={"compensation_ratio_pct": 35.0}
        )["compensation_ratio_rising"]["active"])

    def test_signals_needing_a_year_earlier_reading_are_absent_without_one(self):
        sig = BK.risk_signals(metrics())
        self.assertNotIn("roe_falling", sig)
        self.assertNotIn("leverage_rising", sig)


# ── junk input ─────────────────────────────────────────────────────────────

class TestJunkInput(unittest.TestCase):
    def test_empty_facts_refuse_rather_than_raise(self):
        m = BK.metrics(F, {"facts": {"us-gaap": {}}}, price=10.0,
                       shares_outstanding=1.0, subtype="RETAIL")
        self.assertFalse(m["available"])
        self.assertTrue(m["reason"])

    def test_no_price_leaves_the_multiples_blank_without_raising(self):
        m = BK.metrics(F, broker_facts(), price=None,
                       shares_outstanding=1e8, subtype="RETAIL")
        self.assertIsNone(m["price_to_book"]["value"])
        self.assertIsNotNone(m["book_per_share"]["value"])

    def test_no_share_count_refuses_book_per_share(self):
        m = BK.metrics(F, broker_facts(), price=100.0,
                       shares_outstanding=None, subtype="RETAIL")
        self.assertIsNone(m["book_per_share"]["value"])
        self.assertFalse(m["available"])


if __name__ == "__main__":                             # pragma: no cover
    unittest.main()
