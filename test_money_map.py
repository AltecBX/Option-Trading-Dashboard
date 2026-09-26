"""The Big Money Map (v5.33) reads UW's answers into plain sentences.

Every payload below has the shape of the example in UW's published
OpenAPI spec for that endpoint, so a test that passes here is reading
the fields UW really sends, not ones this file made up.
"""

from __future__ import annotations

import unittest
from datetime import date

import money_map as MM

TODAY = date(2026, 9, 25)


class FakeUW:
    def __init__(self, **over):
        self.calls = []
        self.data = {
            "gex_levels": {"call_wall": "600", "date": "2026-09-25", "gamma_flip": "560",
                           "gamma_magnet": "575", "nearby_flips": ["560", "561.5", "572"],
                           "put_wall": "550", "source": "vol", "time": "2026-09-25 13:35:11+00:00"},
            "max_pain": [{"expiry": "2026-09-19", "max_pain": "540"},
                         {"expiry": "2026-09-26", "max_pain": "570"},
                         {"expiry": "2026-10-03", "max_pain": "575"}],
            "darkpool_levels": [{"dark_pool_volume": 12000, "price": "571.25", "regular_volume": 35000},
                                {"dark_pool_volume": 910000, "price": "566.00", "regular_volume": 800000},
                                {"dark_pool_volume": 450000, "price": "580.50", "regular_volume": 300000},
                                {"dark_pool_volume": 300000, "price": "552.00", "regular_volume": 100000}],
            "variance_risk_premium": [
                {"date": "2026-09-24", "implied_volatility": "0.263456", "implied_volatility_days": 30,
                 "rank": "0.75", "realized_volatility": "0.213456", "realized_volatility_days": 21,
                 "risk_premium": "0.05", "ticker": "SPY"},
                {"date": "2026-08-24", "implied_volatility": "0.20", "realized_volatility": "0.25"}],
            "oi_change": [
                {"option_symbol": "SPY261016C00600000", "oi_diff_plain": 33088, "volume": 33177,
                 "prev_ask_volume": 32861, "prev_bid_volume": 235, "prev_multi_leg_volume": 100,
                 "prev_total_premium": "99711396.00"},
                {"option_symbol": "SPY261016P00540000", "oi_diff_plain": 5892, "volume": 9806,
                 "prev_ask_volume": 860, "prev_bid_volume": 8915, "prev_multi_leg_volume": 8000,
                 "prev_total_premium": "164375.00"},
                {"option_symbol": "SPY261016C00650000", "oi_diff_plain": -7000, "volume": 100,
                 "prev_ask_volume": 50, "prev_bid_volume": 50},
                {"option_symbol": "garbage", "oi_diff_plain": 90000}],
            "insider_transactions": [
                {"amount": 10000, "filing_date": "2026-09-12", "formtype": "4", "is_10b5_1": False,
                 "is_director": False, "officer_title": "CEO", "owner_name": "DOE JANE", "price": "210",
                 "transaction_code": "P", "transaction_date": "2026-09-11"},
                {"amount": -35921, "filing_date": "2026-08-12", "formtype": "4", "is_10b5_1": True,
                 "owner_name": "ROE RICHARD", "officer_title": "CFO", "price": "200",
                 "transaction_code": "S", "transaction_date": "2026-08-11"},
                {"amount": -5000, "filing_date": "2026-09-01", "formtype": "144", "owner_name": "X",
                 "price": "200", "transaction_code": "S", "transaction_date": "2026-09-01"},
                {"amount": 9999, "filing_date": "2026-09-02", "formtype": "4", "owner_name": "AWARD A",
                 "price": "0", "transaction_code": "A", "transaction_date": "2026-09-02"},
                {"amount": 50000, "filing_date": "2026-03-01", "formtype": "4", "owner_name": "OLD O",
                 "price": "100", "transaction_code": "P", "transaction_date": "2026-03-01"}],
            "congress_trades": [
                {"amounts": "$15,001 - $50,000", "filed_at_date": "2026-09-13", "member_type": "house",
                 "name": "Stephen Cohen", "ticker": "SPY", "transaction_date": "2026-09-06", "txn_type": "Buy"},
                {"amounts": "$1,000 - $15,000", "filed_at_date": "2026-07-13", "member_type": "house",
                 "name": "Deborah Ross", "ticker": "SPY", "transaction_date": "2026-07-01", "txn_type": "Sell"},
                {"amounts": "$1,000 - $15,000", "name": "Too Old", "transaction_date": "2025-01-01",
                 "txn_type": "Buy"}],
        }
        self.data.update(over)

    def __getattr__(self, name):
        if name in ("data", "calls"):
            raise AttributeError(name)

        def fn(*a, **k):
            self.calls.append((name, a, k))
            v = self.data.get(name)
            if isinstance(v, Exception):
                raise v
            return v
        return fn


class TheLevels(unittest.TestCase):
    def setUp(self):
        self.m = MM.build(FakeUW(), "spy", spot=568.0, today=TODAY)

    def test_every_level_is_there_highest_first_with_its_distance(self):
        got = [(lv["kind"], lv["price"]) for lv in self.m["levels"]]
        self.assertEqual([("call_wall", 600.0), ("dark_pool", 580.5), ("gamma_magnet", 575.0),
                          ("max_pain", 570.0), ("dark_pool", 566.0), ("gamma_flip", 560.0),
                          ("dark_pool", 552.0), ("put_wall", 550.0)], got)
        cw = self.m["levels"][0]
        self.assertEqual("above", cw["side"])
        self.assertAlmostEqual(5.63, cw["pct"], places=2)
        self.assertEqual("below", self.m["levels"][-1]["side"])

    def test_max_pain_is_the_nearest_expiry_not_yet_passed(self):
        mp = [lv for lv in self.m["levels"] if lv["kind"] == "max_pain"]
        self.assertEqual(1, len(mp))
        self.assertEqual("2026-09-26", mp[0]["expiry"])
        self.assertEqual("Max pain (Sep 26)", mp[0]["label"])

    def test_only_the_three_busiest_dark_pool_prices(self):
        dp = [lv for lv in self.m["levels"] if lv["kind"] == "dark_pool"]
        self.assertEqual([566.0, 580.5, 552.0], [d["price"] for d in sorted(dp, key=lambda d: -d["shares"])])
        self.assertIn("910,000 shares", [d for d in dp if d["price"] == 566.0][0]["meaning"])

    def test_the_regime_follows_the_gamma_flip(self):
        self.assertEqual("calm", self.m["regime"]["state"])
        below = MM.build(FakeUW(), "SPY", spot=555.0, today=TODAY)
        self.assertEqual("wild", below["regime"]["state"])
        self.assertIn("extra room", below["regime"]["text"])

    def test_the_seller_lines_name_both_walls(self):
        sides = {s["side"]: s for s in self.m["seller"]}
        self.assertIn("put wall at $550 is 3.2% below", sides["put"]["text"])
        self.assertIn("call wall at $600 is 5.6% above", sides["call"]["text"])

    def test_without_a_price_there_is_no_distance_and_no_advice(self):
        m = MM.build(FakeUW(), "SPY", spot=None, today=TODAY)
        self.assertTrue(m["levels"])
        self.assertTrue(all(lv["pct"] is None for lv in m["levels"]))
        self.assertIsNone(m["regime"])
        self.assertEqual([], m["seller"])


class ThePremium(unittest.TestCase):
    def test_rich_when_options_price_more_than_the_stock_moves(self):
        p = MM.build(FakeUW(), "SPY", spot=568, today=TODAY)["premium"]
        self.assertEqual("rich", p["state"])
        self.assertEqual(26.3, p["iv"])
        self.assertEqual(21.3, p["rv"])
        self.assertEqual(5.0, p["gap"])
        self.assertIn("RICH", p["text"])

    def test_thin_and_fair(self):
        thin = MM.premium_section([{"date": "2026-09-24", "implied_volatility": "0.20", "realized_volatility": "0.25"}])
        self.assertEqual("thin", thin["state"])
        fair = MM.premium_section({"date": "2026-09-24", "implied_volatility": "0.21", "realized_volatility": "0.20"})
        self.assertEqual("fair", fair["state"])
        self.assertIsNone(MM.premium_section(None))


class WhatOpenedOvernight(unittest.TestCase):
    def test_new_positions_biggest_first_with_how_they_printed(self):
        op = MM.build(FakeUW(), "SPY", spot=568, today=TODAY)["opened"]
        self.assertEqual(2, len(op), "a shrinking OI and an unreadable symbol are skipped")
        call, put = op
        self.assertEqual(("call", 600.0, "2026-10-16", "bought", "bullish"),
                         (call["right"], call["strike"], call["expiry"], call["how"], call["lean"]))
        self.assertIn("+33,088 contracts of the Oct 16 $600 call opened, mostly bought at the ask ($99.7M)", call["text"])
        self.assertEqual(("sold", "bullish", True), (put["how"], put["lean"], put["spreads"]))
        self.assertIn("Mostly part of spreads", put["text"])

    def test_occ_symbols(self):
        self.assertEqual({"root": "BRK.B", "expiry": "2026-10-16", "right": "put", "strike": 412.5},
                         MM.parse_occ("BRK.B261016P00412500"))
        self.assertIsNone(MM.parse_occ("nope"))


class Insiders(unittest.TestCase):
    def test_buys_lead_and_notices_awards_and_old_rows_do_not_count(self):
        ins = MM.build(FakeUW(), "SPY", spot=568, today=TODAY)["insiders"]
        self.assertEqual("buying", ins["state"])
        self.assertEqual((1, 1), (ins["buyers"], ins["sellers"]))
        self.assertEqual(2_100_000, ins["buy_value"])
        self.assertIn("CEO Doe Jane, $2.1M on Sep 11", ins["text"])

    def test_only_selling_says_how_much_was_pre_planned(self):
        uw = FakeUW(insider_transactions=[
            {"amount": -1000, "formtype": "4", "is_10b5_1": True, "owner_name": "A", "price": "100",
             "transaction_code": "S", "transaction_date": "2026-09-01"},
            {"amount": -1000, "formtype": "4", "is_10b5_1": False, "owner_name": "B", "price": "100",
             "transaction_code": "S", "transaction_date": "2026-09-02"}])
        ins = MM.build(uw, "SPY", spot=568, today=TODAY)["insiders"]
        self.assertEqual("selling", ins["state"])
        self.assertIn("1 of 2 pre-planned", ins["text"])

    def test_nothing_is_quiet_not_missing(self):
        m = MM.build(FakeUW(insider_transactions=[]), "SPY", spot=568, today=TODAY)
        self.assertEqual("quiet", m["insiders"]["state"])
        self.assertNotIn("insider_transactions", m["missing"])


class Congress(unittest.TestCase):
    def test_six_months_newest_first(self):
        c = MM.build(FakeUW(), "SPY", spot=568, today=TODAY)["congress"]
        self.assertEqual((1, 1), (c["buys"], c["sells"]))
        self.assertEqual("Stephen Cohen", c["recent"][0]["name"])
        self.assertIn("Latest: Stephen Cohen bought $15,001 - $50,000 on Sep 6", c["text"])

    def test_none_in_six_months(self):
        c = MM.congress_section([], TODAY)
        self.assertIn("No congressional trades", c["text"])


class NeverRaises(unittest.TestCase):
    def test_a_failing_or_empty_endpoint_is_listed_not_fatal(self):
        uw = FakeUW(gex_levels=RuntimeError("boom"), variance_risk_premium=None, congress_trades=None)
        m = MM.build(uw, "SPY", spot=568, today=TODAY)
        self.assertIn("gex_levels", m["missing"])
        self.assertIn("variance_risk_premium", m["missing"])
        self.assertIsNone(m["premium"])
        self.assertIsNone(m["congress"])
        self.assertTrue(any(lv["kind"] == "max_pain" for lv in m["levels"]), "the rest still answers")

    def test_the_insider_window_is_asked_of_uw(self):
        uw = FakeUW()
        MM.build(uw, "SPY", spot=568, today=TODAY)
        call = [c for c in uw.calls if c[0] == "insider_transactions"][0]
        self.assertEqual("2026-06-27", call[2]["start_date"])


class PremiumChecksForTheBoard(unittest.TestCase):
    """v5.34: the Worth Selling Today board asks for several at once."""

    def test_each_symbol_gets_its_verdict_and_a_failure_is_none(self):
        uw = FakeUW()
        vrp = uw.data["variance_risk_premium"]

        class Many:
            def variance_risk_premium(self, sym):
                if sym == "BAD":
                    raise RuntimeError("boom")
                if sym == "NONE":
                    return None
                return vrp
        got = MM.premium_checks(Many(), ["spy", "BAD", "NONE", "spy"])
        self.assertEqual({"SPY", "BAD", "NONE"}, set(got))
        self.assertEqual("rich", got["SPY"]["state"])
        self.assertIsNone(got["BAD"])
        self.assertIsNone(got["NONE"])

    def test_a_slow_answer_does_not_hold_the_board(self):
        import time

        class Slow:
            def variance_risk_premium(self, sym):
                if sym == "SLOW":
                    time.sleep(2)
                return [{"date": "2026-09-24", "implied_volatility": "0.3", "realized_volatility": "0.2"}]
        t = time.time()
        got = MM.premium_checks(Slow(), ["FAST", "SLOW"], budget_s=0.5)
        self.assertLess(time.time() - t, 1.5)
        self.assertEqual("rich", got["FAST"]["state"])
        self.assertIsNone(got["SLOW"])

    def test_no_client_method_is_all_none(self):
        self.assertEqual({"A": None}, MM.premium_checks(object(), ["a"]))


class TheClientKnowsTheNewPaths(unittest.TestCase):
    """The paths are UW's published ones (api/openapi, fetched for v5.33)."""

    def test_paths(self):
        import unusual_whales_client as U
        want = {
            "gex_levels": "/api/stock/{ticker}/gex-levels",
            "max_pain": "/api/stock/{ticker}/max-pain",
            "darkpool_levels": "/api/darkpool/{ticker}/price-levels",
            "oi_change": "/api/stock/{ticker}/oi-change",
            "insider_transactions": "/api/insider/transactions",
            "congress_trades": "/api/congress/recent-trades",
            "variance_risk_premium": "/api/stock/{ticker}/volatility/variance-risk-premium",
        }
        for k, v in want.items():
            self.assertEqual(v, U.ENDPOINTS.get(k), k)
            self.assertIn(k, U.TTL_BY_KEY, f"{k} has no cache time")

    def test_the_client_builds_the_right_urls(self):
        import unusual_whales_client as U
        c = U.UWClient("k")
        seen = []
        c._get = lambda key, params: seen.append((key, params)) or []
        c.gex_levels("spy")
        c.insider_transactions("spy", start_date="2026-06-27", limit=200)
        c.congress_trades("spy")
        c.oi_change("spy", limit=500)
        self.assertEqual(("gex_levels", {"ticker": "SPY"}), seen[0])
        self.assertEqual({"ticker_symbol": "SPY", "limit": "200", "start_date": "2026-06-27"}, seen[1][1])
        self.assertEqual("SPY", seen[2][1]["ticker"])
        self.assertEqual("100", seen[3][1]["limit"], "OI change is capped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
