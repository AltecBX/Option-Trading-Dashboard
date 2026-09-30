"""The Options Hotlist (v5.36).

Screener rows have the shape of UW's /api/screener/option-contracts
example. The anomaly screen's rows are not specified field by field in
UW's spec, so those tests use plausible names and check the reader takes
what is there and invents nothing."""

from __future__ import annotations

import unittest

import hotlist as HL


def chain(sym, right, strike, prem, vol, ask, bid, oi=1000, sweep=0, multi=0, expiry="2026-10-16"):
    cp = "C" if right == "call" else "P"
    return {"option_symbol": f"{sym}261016{cp}{int(strike * 1000):08d}", "option_type": right,
            "strike": str(strike), "expiry": expiry, "premium": str(prem), "volume": vol,
            "ask_side_volume": ask, "bid_side_volume": bid, "open_interest": oi,
            "sweep_volume": sweep, "multileg_volume": multi, "stock_price": "247.94",
            "sector": "Consumer Cyclical", "next_earnings_date": "2026-10-22"}


HOT = [chain("TSLA", "call", 255, 27723806, 264899, 119403, 122789, oi=18680, sweep=18260),
       chain("NVDA", "put", 170, 50_000_000, 100000, 80000, 10000, oi=20000, sweep=40000),
       chain("AAPL", "call", 250, 9_000_000, 50000, 5000, 40000, oi=100000, multi=30000),
       {"option_symbol": "junk", "volume": 5}]
RICH = {"data": [{"ticker": "IOVA", "score": "2.1", "iv": "0.95", "rv": "0.60"},
                 {"ticker": "PLTR", "score": "3.4", "iv": "0.70"},
                 {"symbol": "bbby"},
                 {"score": 9}]}
CHEAP = [{"ticker": "KO", "anomaly_score": -2.2, "implied_volatility": 12.0, "realized_volatility": 18.0}]


class FakeUW:
    def __init__(self, hot=HOT, rich=RICH, cheap=CHEAP):
        self.h, self.r, self.c, self.asked = hot, rich, cheap, []

    def hottest_chains(self, limit=50, min_premium=0, order=None):
        self.asked.append(("hottest_chains", limit, order))
        if isinstance(self.h, Exception):
            raise self.h
        return self.h

    def vol_anomaly_top(self, direction="short_vol", limit=50):
        self.asked.append(("vol_anomaly_top", direction))
        return self.r if direction == "short_vol" else self.c


class Hottest(unittest.TestCase):
    def setUp(self):
        self.out = HL.build(FakeUW(), watchlist=["aapl"])

    def test_biggest_money_first_and_how_it_printed(self):
        h = self.out["hottest"]
        self.assertEqual(["NVDA", "TSLA", "AAPL"], [x["symbol"] for x in h], "by premium; junk dropped")
        nv, ts, ap = h
        self.assertEqual(("bought", "bearish"), (nv["how"], nv["lean"]), "puts bought at the ask")
        self.assertEqual(("mixed", None), (ts["how"], ts["lean"]))
        self.assertEqual(("sold", "bearish"), (ap["how"], ap["lean"]), "calls sold at the bid")
        self.assertTrue(ap["watchlist"])
        self.assertEqual("NVDA Oct 16 $170 put: $50.0M traded on 100,000 contracts, 80% at the ask, mostly BOUGHT; "
                         "volume 5.0x the open interest (new positions), 40% sweeps (urgent orders).", nv["text"])
        self.assertIn("mostly part of spreads", ap["text"])

    def test_it_asks_uw_for_the_premium_order(self):
        uw = FakeUW()
        HL.build(uw)
        self.assertIn(("hottest_chains", 100, "premium"), uw.asked)


class Oddities(unittest.TestCase):
    def test_rich_reads_what_is_there_and_invents_nothing(self):
        r = HL.build(FakeUW())["rich"]
        self.assertEqual(["PLTR", "IOVA", "BBBY"], [x["symbol"] for x in r], "by score; a row with no symbol is dropped")
        iova = r[1]
        self.assertEqual((95.0, 60.0), (iova["iv"], iova["rv"]))
        self.assertIn("Options unusually EXPENSIVE: a seller's candidate (options price a 95% move against 60% realized; "
                      "anomaly score 2.10).", iova["text"])
        self.assertEqual("Options unusually EXPENSIVE: a seller's candidate.", r[2]["text"], "nothing known, nothing said")

    def test_cheap_takes_other_field_names_and_percent_values(self):
        c = HL.build(FakeUW())["cheap"]
        self.assertEqual("KO", c[0]["symbol"])
        self.assertEqual((12.0, 18.0, -2.2), (c[0]["iv"], c[0]["rv"], c[0]["score"]))
        self.assertIn("CHEAP", c[0]["text"])


class NeverRaises(unittest.TestCase):
    def test_a_failing_list_is_named(self):
        out = HL.build(FakeUW(hot=RuntimeError("x"), rich=None))
        self.assertEqual([], out["hottest"])
        self.assertEqual(["hottest_chains", "short_vol"], out["missing"])
        self.assertEqual(1, len(out["cheap"]))


class TheClient(unittest.TestCase):
    def test_params(self):
        import unusual_whales_client as U
        c = U.UWClient("k")
        seen = []
        c._get = lambda key, params: seen.append((key, params)) or []
        c.hottest_chains(limit=900, order="premium")
        c.vol_anomaly_top("anything")
        c.vol_anomaly_top("long_vol", limit=999)
        self.assertEqual(("hottest_chains", {"limit": "250", "order": "premium", "order_direction": "desc"}), seen[0])
        self.assertEqual({"direction": "short_vol", "limit": "50"}, seen[1][1], "an unknown direction is short_vol")
        self.assertEqual({"direction": "long_vol", "limit": "200"}, seen[2][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
