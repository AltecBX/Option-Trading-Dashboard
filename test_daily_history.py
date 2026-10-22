"""Tests for the Trade chart's 2Y/5Y/10Y history (v5.44): build_daily_history."""

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("JERRY_NO_NET", "1")

import options_dashboard as od


def rows(years):
    """load_daily's row shape, a weekday bar per day for `years` years."""
    out, d = [], datetime.today() - timedelta(days=int(years * 365.25))
    while d <= datetime.today():
        if d.weekday() < 5:
            out.append({"date": d.strftime("%Y-%m-%dT12:00:00-04:00"), "open": 10.0, "high": 11.123456,
                        "low": 9.5, "close": 10.5, "volume": 1234.0, "ma50": 10.1, "macd": None,
                        "secret_internal": "x"})
        d += timedelta(days=1)
    return out


class DailyHistory(unittest.TestCase):
    def setUp(self):
        self.asked = []
        self.saved = od.load_daily
        od._DAILY_HIST_CACHE.clear()

        def fake(symbol, days=90):
            self.asked.append((symbol, days))
            return rows(12)
        od.load_daily = fake

    def tearDown(self):
        od.load_daily = self.saved
        od._DAILY_HIST_CACHE.clear()

    def test_exactly_the_years_asked_for(self):
        r = od.build_daily_history("aapl", 10)
        self.assertTrue(r["ok"])
        self.assertEqual(("AAPL", 3652), self.asked[0], "asked in calendar days: the Yahoo fallback counts those")
        first = datetime.strptime(r["daily"][0]["date"][:10], "%Y-%m-%d")
        years = (datetime.today() - first).days / 365.25
        self.assertTrue(9.99 <= years <= 10.01, years)
        self.assertEqual(r["first"], r["daily"][0]["date"][:10])
        row = r["daily"][-1]
        self.assertEqual({"date", "open", "high", "low", "close", "volume", "ma50"}, set(row),
                         "the chart's fields only; empty indicators left out")
        self.assertEqual(11.1235, row["high"])
        self.assertEqual(1234, row["volume"])

    def test_kept_ten_minutes(self):
        od.build_daily_history("AAPL", 2)
        od.build_daily_history("AAPL", 2)
        self.assertEqual(1, len(self.asked))
        od.build_daily_history("AAPL", 5)
        self.assertEqual(2, len(self.asked), "each range on its own")

    def test_only_the_offered_ranges_and_real_symbols(self):
        self.assertFalse(od.build_daily_history("AAPL", 7)["ok"])
        self.assertIn("years must be one of 2, 5, 10", od.build_daily_history("AAPL", 7)["error"])
        for bad in ("", "../etc", "A" * 20, "AA PL"):
            self.assertFalse(od.build_daily_history(bad, 2)["ok"], bad)
        self.assertEqual([], self.asked, "nothing loaded for any of them")

    def test_no_bars_is_said_and_not_kept(self):
        od.load_daily = lambda symbol, days=90: []
        r = od.build_daily_history("ZZZZ", 2)
        self.assertFalse(r["ok"])
        self.assertIn("No daily history for ZZZZ", r["error"])
        self.assertEqual({}, od._DAILY_HIST_CACHE)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class GapActions(unittest.TestCase):
    """Codex, #430: _gap_actions says whether it could ask."""

    def setUp(self):
        self.saved = (od.yf, os.environ.get("JERRY_NO_NET"))
        od._GAP_ACT_CACHE.clear()
        os.environ.pop("JERRY_NO_NET", None)

    def tearDown(self):
        od.yf = self.saved[0]
        if self.saved[1] is not None:
            os.environ["JERRY_NO_NET"] = self.saved[1]
        od._GAP_ACT_CACHE.clear()

    def frame(self, splits=(), empty=False, days=None, closes=None):
        import pandas as pd
        if empty:
            return pd.DataFrame()
        idx = pd.to_datetime(days or ["2024-06-07", "2024-06-10", "2024-06-11"])
        sp = [0.0] * len(idx)
        for d, r in splits:
            sp[list(idx.strftime("%Y-%m-%d")).index(d)] = r
        dv = [0.0] * len(idx)
        dv[-1] = 0.01
        return pd.DataFrame({"Close": closes or [1200.0, 121.0, 122.0], "Dividends": dv,
                             "Stock Splits": sp}, index=idx)

    def fake(self, answers):
        calls = self.calls = []
        frame = self.frame

        class T:
            def __init__(self, s):
                pass

            def history(self, **kw):
                calls.append(kw)
                a = answers.pop(0)
                if isinstance(a, Exception):
                    raise a
                return frame(**a)

        class YF:
            Ticker = T
        od.yf = YF

    def test_a_failed_lookup_is_marked_and_tried_again_soon(self):
        self.fake([RuntimeError("Too Many Requests"), {"splits": [("2024-06-10", 10.0)]}])
        a = od._gap_actions("NVDA")
        self.assertFalse(a["ok"])
        self.assertTrue(self.calls[0].get("raise_errors"), "a failed download raises")
        self.assertTrue(od._gap_actions("NVDA") is a, "kept a little while")
        k, (ts, v) = next(iter(od._GAP_ACT_CACHE.items()))
        od._GAP_ACT_CACHE[k] = (ts - 601, v)          # eleven minutes on
        b = od._gap_actions("NVDA")
        self.assertTrue(b["ok"])
        self.assertEqual({"2024-06-10": 10.0}, b["split_ratios"])
        self.assertEqual({"2024-06-10"}, b["splits"])
        self.assertEqual({"2024-06-11": 0.01}, b["dividends"])

    def test_an_empty_download_is_a_failed_one(self):
        # yfinance can hand back an empty frame instead of raising: a real
        # ticker always has prices, so that is not "never split" (Codex, #430).
        self.fake([{"empty": True}])
        self.assertFalse(od._gap_actions("NVDA")["ok"])

    def test_no_splits_is_ok_when_the_prices_came(self):
        self.fake([{}])
        a = od._gap_actions("KO")
        self.assertTrue(a["ok"])
        self.assertEqual({}, a.get("split_ratios", {}))

    def test_offline_is_not_ok(self):
        os.environ["JERRY_NO_NET"] = "1"
        self.assertFalse(od._gap_actions("NVDA")["ok"])

    def test_one_unadjusted_close_a_month_for_the_market_cap_check(self):
        # v5.46: a market-cap chart checks its own prices against these
        # before it multiplies them by share counts. auto_adjust=False is
        # split-adjusted and NOT dividend-adjusted: the basis it needs.
        self.fake([{"days": ["2024-05-30", "2024-05-31", "2024-06-03", "2024-06-04", "2024-07-01"],
                    "closes": [10.0, 10.5, 11.0, float("nan"), 12.25]}])
        a = od._gap_actions("KO")
        self.assertIs(False, self.calls[0].get("auto_adjust"))
        self.assertEqual([("2024-05-30", 10.0), ("2024-06-03", 11.0), ("2024-07-01", 12.25)],
                         a["ref_closes"], "the first trading day of each month")

    def test_a_month_starting_on_a_missing_close_takes_the_next_one(self):
        self.fake([{"days": ["2024-06-03", "2024-06-04"], "closes": [float("nan"), 11.5]}])
        self.assertEqual([("2024-06-04", 11.5)], od._gap_actions("KO")["ref_closes"])
