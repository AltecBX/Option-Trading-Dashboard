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


class LevelsForTheLiveScanner(unittest.TestCase):
    """v5.34: the gamma flip and the three busiest dark pool prices."""

    def test_the_two_levels(self):
        got = MM.live_levels(FakeUW(), "spy")
        self.assertEqual(560.0, got["gamma_flip"])
        self.assertEqual([[566.0, 910000], [580.5, 450000], [552.0, 300000]], got["dark"])

    def test_the_server_hands_them_to_the_scanner(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()
        at = src.index("_live.configure(")
        self.assertIn('levels_fn=lambda sym:', src[at:at + 1500])
        self.assertIn('live_levels(uw, sym)', src[at:at + 1500])

    def test_nothing_answered_is_none_and_half_is_half(self):
        self.assertIsNone(MM.live_levels(FakeUW(gex_levels=None, darkpool_levels=None), "SPY"))
        half = MM.live_levels(FakeUW(gex_levels=RuntimeError("x")), "SPY")
        self.assertIsNone(half["gamma_flip"])
        self.assertEqual(3, len(half["dark"]))


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


# ── v5.36 ──────────────────────────────────────────────────────────────────
FDA = [  # shape of UW's /api/market/fda-calendar example
    {"catalyst": "PDUFA Date", "drug": "Lifileucel", "end_date": "2026-10-12", "has_options": True,
     "indication": "Advanced Melanoma", "marketcap": "1000000000", "notes": None, "outcome": None,
     "outcome_brief": None, "source_link": None, "start_date": "2026-10-12", "status": "NDA", "ticker": "IOVA"},
    {"catalyst": "Phase 3 Readout", "drug": "X-1", "start_date": "2026-10-01", "end_date": "2026-12-31",
     "status": "Phase 3", "ticker": "IOVA", "indication": ""},
    {"catalyst": "PDUFA Date", "drug": "Done", "start_date": "2026-10-02", "end_date": "2026-10-02",
     "outcome": "Approved", "status": "NDA", "ticker": "IOVA"},
    {"catalyst": "AdCom", "drug": "Past", "start_date": "2026-09-01", "end_date": "2026-09-01",
     "status": "NDA", "ticker": "IOVA"},
    {"catalyst": "PDUFA Date", "drug": "Far", "start_date": "2026-12-20", "end_date": "2026-12-20",
     "status": "NDA", "ticker": "OTHR"},
]
SI = {"days_to_cover": "8.1", "fee_rate": "0.2782", "market_date": "2026-09-15", "rebate_rate": "3.3518",
      "short_interest": 75000, "short_shares_available": 10000000, "si_float": "0.241",
      "si_float_with_synth_long_pct_of_total_shares": "0.23", "symbol": "IOVA", "total_float": 4250000}
EARN = [  # UW /api/earnings/{ticker} example shape, newest first after sorting
    {"actual_eps": "2.45", "expected_move_perc": "0.0359", "post_earnings_move_1d": "-0.0724",
     "report_date": "2026-07-30", "report_time": "postmarket", "short_straddle_1d": "-0.5830"},
    {"actual_eps": "2.32", "expected_move_perc": "0.0261", "post_earnings_move_1d": "0.0500",
     "report_date": "2026-04-30", "report_time": "postmarket", "short_straddle_1d": "-0.4"},
    {"expected_move_perc": "0.04", "post_earnings_move_1d": "0.01", "report_date": "2026-01-30"},
    {"expected_move_perc": None, "post_earnings_move_1d": "0.2", "report_date": "2025-10-30"},
]
OIPS = [{"call_oi": 1123, "date": "2026-09-25", "put_oi": 24443, "strike": "90"},
        {"call_oi": 50000, "date": "2026-09-25", "put_oi": 100, "strike": "110"},
        {"call_oi": 90000, "date": "2026-09-25", "put_oi": 10, "strike": "120"},
        {"call_oi": 10, "date": "2026-09-25", "put_oi": 70000, "strike": "95"},
        {"call_oi": 5, "date": "2026-09-25", "put_oi": 5000, "strike": "80"},
        {"call_oi": 3000, "date": "2026-09-25", "put_oi": 3000, "strike": "130"}]
SEAS = [{"avg_change": 0.032, "max_change": 0.0635, "median_change": 0.0195, "min_change": -0.0727,
         "month": 9, "positive_closes": 8, "positive_months_perc": 0.8, "years": 10},
        {"avg_change": -0.011, "max_change": 0.04, "median_change": -0.01, "min_change": -0.09,
         "month": 10, "positive_closes": 4, "positive_months_perc": 0.4, "years": 10}]


class Warnings(unittest.TestCase):
    def test_fda_events_upcoming_undecided_soonest_first(self):
        ev = MM.fda_events(FDA, TODAY)
        self.assertEqual(["X-1", "Lifileucel", "Far"], [e["drug"] for e in ev])
        self.assertEqual("PDUFA Date for Lifileucel: Oct 12 (Advanced Melanoma).", ev[1]["text"])
        self.assertEqual("Phase 3 Readout for X-1: Oct 1 to Dec 31.", ev[0]["text"])
        self.assertEqual(["X-1", "Lifileucel"], [e["drug"] for e in MM.fda_events(FDA, TODAY, until=date(2026, 10, 16))])
        self.assertEqual([], MM.fda_events(FDA, TODAY, ticker="AAPL"))

    def test_squeeze_levels(self):
        hi = MM.squeeze_section(SI)
        self.assertEqual("high", hi["level"])
        self.assertIn("24.1% of the float is sold short, 8.1 days to cover (FINRA, 2026-09-15)", hi["text"])
        self.assertEqual("elevated", MM.squeeze_section({"si_float": "0.12", "days_to_cover": "2"})["level"])
        self.assertEqual("low", MM.squeeze_section({"si_float": "0.02", "days_to_cover": "1"})["level"])
        self.assertIsNone(MM.squeeze_section(None))
        self.assertIsNone(MM.squeeze_section({"symbol": "X"}))


class EarningsAndStrikes(unittest.TestCase):
    def test_expected_vs_actual(self):
        e = MM.earnings_section(EARN)
        self.assertEqual(3, e["n"], "a report without an expected move is skipped")
        self.assertEqual((3.4, 4.41), (round(e["expected_avg"], 1), round(e["actual_avg"], 2)))
        self.assertEqual(2, e["bigger"])
        self.assertEqual("under", e["state"])
        self.assertIn("options expected ±3.4% on average; the stock actually moved ±4.4% the next day, "
                      "more than expected 2 of 3 times", e["text"])
        self.assertIsNone(MM.earnings_section([]))

    def test_oi_walls_either_side_of_the_price(self):
        got = [(l["kind"], l["price"], l["contracts"]) for l in MM.oi_levels(OIPS, 100.0)]
        self.assertEqual([("oi_call", 120.0, 90000), ("oi_call", 110.0, 50000),
                          ("oi_put", 95.0, 70000), ("oi_put", 90.0, 24443)], got)
        self.assertEqual([], MM.oi_levels(OIPS, None))

    def test_seasonality_this_month_and_next_near_the_end(self):
        s = MM.seasonality_section(SEAS, date(2026, 9, 10))
        self.assertEqual("September: up 8 of the last 10 years, average +3.2% (worst -7.3%, best +6.3%).", s["text"])
        self.assertIsNone(s["next"])
        late = MM.seasonality_section(SEAS, date(2026, 9, 25))
        self.assertEqual("October", late["next"]["name"])
        self.assertIn("October: up 4 of the last 10 years", late["text"])

    def test_the_map_carries_them_all(self):
        uw = FakeUW(fda_calendar=[dict(r, ticker="SPY") for r in FDA], short_interest=SI,
                    earnings_history=EARN, oi_per_strike=[dict(r, strike=str(float(r["strike"]) * 5.68)) for r in OIPS],
                    seasonality_monthly=SEAS)
        m = MM.build(uw, "SPY", spot=568.0, today=TODAY)
        self.assertEqual("X-1", m["fda"][0]["drug"])
        self.assertEqual("high", m["squeeze"]["level"])
        self.assertEqual("under", m["earnings"]["state"])
        self.assertIn("October", m["seasonality"]["text"])
        kinds = {l["kind"] for l in m["levels"]}
        self.assertTrue({"oi_call", "oi_put"} <= kinds, "the OI walls are on the ladder")
        oi = [l for l in m["levels"] if l["kind"] == "oi_call"][0]
        self.assertIn("open call contracts", oi["meaning"])


class FridayWalls(unittest.TestCase):
    """Codex, #423: the Friday walls read ONE expiry's open interest, and
    that expiry's own max pain, not every expiry added together."""

    def contracts(self):
        return [{"option_symbol": "SPY261002C00580000", "open_interest": 90000},
                {"option_symbol": "SPY261002C00575000", "open_interest": 20000},
                {"option_symbol": "SPY261002C00590000", "open_interest": 50000},
                {"option_symbol": "SPY261002P00560000", "open_interest": 70000},
                {"option_symbol": "SPY261002P00550000", "open_interest": 1000},
                {"option_symbol": "SPY261002P00570000", "open_interest": 999999},   # above spot: not a floor
                {"option_symbol": "junk", "open_interest": 5}]

    def test_this_fridays_contracts_and_max_pain(self):
        uw = FakeUW(option_contracts=self.contracts(),
                    max_pain=[{"expiry": "2026-10-02", "max_pain": "566"}, {"expiry": "2026-09-26", "max_pain": "570"},
                              {"expiry": "2026-10-16", "max_pain": "560"}])
        w = MM.friday_walls(uw, "spy", spot=568.0, today=date(2026, 9, 29))
        self.assertEqual("2026-10-02", w["expiry"])
        asked = [c for c in uw.calls if c[0] == "option_contracts"][0]
        self.assertEqual(("SPY", "2026-10-02"), asked[1])
        got = [(l["kind"], l["price"]) for l in w["levels"]]
        self.assertEqual([("call_wall", 600.0), ("fri_call", 590.0), ("fri_call", 580.0), ("gamma_magnet", 575.0),
                          ("max_pain", 566.0), ("fri_put", 560.0), ("fri_put", 550.0), ("put_wall", 550.0)], got)
        self.assertNotIn("gamma_flip", [l["kind"] for l in w["levels"]])
        self.assertIn("expiring this Friday", w["levels"][1]["meaning"])

    def test_no_max_pain_for_that_expiry_is_none_not_the_next_one(self):
        uw = FakeUW(option_contracts=self.contracts(), max_pain=[{"expiry": "2026-10-16", "max_pain": "560"}])
        w = MM.friday_walls(uw, "SPY", spot=568.0, today=date(2026, 9, 29))
        self.assertNotIn("max_pain", [l["kind"] for l in w["levels"]])

    def test_a_friday_is_its_own_expiry_and_no_weeklies_says_so(self):
        self.assertEqual(date(2026, 10, 2), MM.next_friday(date(2026, 10, 2)))
        self.assertEqual(date(2026, 10, 9), MM.next_friday(date(2026, 10, 3)))
        w = MM.friday_walls(FakeUW(option_contracts=[]), "SPY", spot=568.0, today=date(2026, 9, 29))
        self.assertFalse(w["has_weekly"])

    def test_the_map_ladder_says_its_oi_is_every_expiry(self):
        self.assertIn("Across every expiry", MM.LEVEL_TEXT["oi_call"][1])


class BoardRisks(unittest.TestCase):
    def test_fda_inside_the_life_and_squeeze_only_for_a_short_call(self):
        uw = FakeUW(fda_calendar=FDA, short_interest=SI)
        rows = [{"symbol": "IOVA", "kind": "put_credit_spread", "expiration": "2026-10-16"},
                {"symbol": "OTHR", "kind": "call_credit_spread", "expiration": "2026-10-16"},
                {"symbol": "IOVA2", "kind": "iron_condor", "expiration": "2026-10-16"}]
        got = MM.board_risks(uw, rows, today=TODAY)
        self.assertEqual(["X-1", "Lifileucel"], [e["drug"] for e in got["IOVA"]["fda"]])
        self.assertEqual([], got["OTHR"]["fda"], "Dec 20 is after the Oct 16 expiry")
        self.assertIsNone(got["IOVA"]["squeeze"], "a put spread has no short call to squeeze")
        self.assertEqual("high", got["OTHR"]["squeeze"]["level"])
        self.assertEqual("high", got["IOVA2"]["squeeze"]["level"])
        asked = [c for c in uw.calls if c[0] == "fda_calendar"]
        self.assertEqual(1, len(asked), "one market-wide FDA call covers the board")

    def test_nothing_raises(self):
        uw = FakeUW(fda_calendar=RuntimeError("x"), short_interest=RuntimeError("y"))
        got = MM.board_risks(uw, [{"symbol": "A", "kind": "iron_condor", "expiration": "2026-10-16"}], today=TODAY)
        self.assertEqual({"A": {"fda": [], "squeeze": None}}, got)
