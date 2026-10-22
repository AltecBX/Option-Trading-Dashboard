"""test_credit_risk.py — the Merton structural-credit engine on pinned
fixtures. Run:  python3 -m unittest test_credit_risk
"""
import math
import unittest

import credit_risk as cr
from metrics import _norm_cdf


class TestMertonSolver(unittest.TestCase):
    def test_roundtrip_consistency(self):
        # Solve, then verify the solution actually reproduces the observed
        # equity through the Merton call formula (the defining property).
        m = cr.merton_solve(equity=40.0, equity_vol=0.55, debt=80.0, r=0.04)
        self.assertIsNotNone(m)
        V, vol_V = m["V"], m["vol_V"]
        T = cr.T_YEARS
        d1 = (math.log(V / 80.0) + (0.04 + 0.5 * vol_V ** 2) * T) / (vol_V * math.sqrt(T))
        d2 = d1 - vol_V * math.sqrt(T)
        E_model = V * _norm_cdf(d1) - 80.0 * math.exp(-0.04 * T) * _norm_cdf(d2)
        self.assertAlmostEqual(E_model, 40.0, delta=0.05)

    def test_leverage_monotonic_realistic_regime(self):
        # More debt on the same equity → wider spread across the realistic
        # leverage range. (At EXTREME leverage with equity vol held fixed,
        # Merton's implied asset vol compresses — the model is saying "if
        # a 76%-levered firm only shows 40% equity vol, its assets must be
        # rock-stable" — so the naive always-monotonic intuition is wrong;
        # in reality leverage arrives WITH rising equity vol, tested below.)
        spreads = []
        for debt in (10, 40, 80):
            m = cr.merton_solve(50.0, 0.40, float(debt), 0.04)
            spreads.append(m["spread_bps"])
        self.assertEqual(spreads, sorted(spreads))
        self.assertGreater(spreads[-1], spreads[0] + 30)

    def test_joint_leverage_and_vol_move_widens(self):
        # The realistic deterioration: price falls (equity shrinks) AND
        # equity vol rises → spread must widen decisively.
        healthy = cr.merton_solve(80.0, 0.30, 60.0, 0.04)["spread_bps"]
        stressed = cr.merton_solve(40.0, 0.55, 60.0, 0.04)["spread_bps"]
        self.assertGreater(stressed, healthy * 3)

    def test_vol_monotonic(self):
        s_lo = cr.merton_solve(50.0, 0.25, 80.0, 0.04)["spread_bps"]
        s_hi = cr.merton_solve(50.0, 0.60, 80.0, 0.04)["spread_bps"]
        self.assertGreater(s_hi, s_lo)

    def test_negligible_debt_is_zero_spread(self):
        # The NVDA case: huge equity, tiny debt → ~0 bps, no false alarm.
        m = cr.merton_solve(4_000_000.0, 0.45, 10_000.0, 0.04)   # $4T vs $10B
        self.assertLess(m["spread_bps"], 1.0)
        self.assertLess(m["pd"], 1e-6)

    def test_no_debt(self):
        m = cr.merton_solve(100.0, 0.5, 0.0, 0.04)
        self.assertEqual(m["spread_bps"], 0.0)
        self.assertEqual(m["leverage"], 0.0)

    def test_degenerate_inputs(self):
        self.assertIsNone(cr.merton_solve(0.0, 0.5, 10.0, 0.04))
        self.assertIsNone(cr.merton_solve(10.0, 0.0, 10.0, 0.04))

    def test_distressed_name_prices_hundreds_of_bps(self):
        # Deep leverage + high vol → a spread in genuinely distressed
        # territory (hundreds of bps), bounded (not thousands of %).
        m = cr.merton_solve(equity=15.0, equity_vol=0.80, debt=100.0, r=0.04)
        self.assertGreater(m["spread_bps"], 200)
        self.assertLess(m["spread_bps"], 5000)


class TestHelpers(unittest.TestCase):
    def test_default_point_kmv(self):
        self.assertEqual(cr.default_point(10.0, 40.0, None), 30.0)
        self.assertEqual(cr.default_point(None, None, 100.0), 75.0)
        self.assertIsNone(cr.default_point(None, None, None))

    def test_series_shape_and_direction(self):
        from datetime import date, timedelta
        d0 = date(2026, 1, 2)
        # Price slides 30% (with real day-to-day wiggle so HV is nonzero)
        # → the model spread must WIDEN along the series.
        closes = [100.0 * (1 - 0.3 * i / 199) * (1 + 0.012 * ((-1) ** i))
                  for i in range(200)]
        bars = [{"date": (d0 + timedelta(days=i)).isoformat(), "close": c}
                for i, c in enumerate(closes)]
        s = cr.merton_series(bars, shares_out=1.0, debt_point=60.0, r=0.04)
        self.assertGreater(len(s), 100)
        self.assertGreater(s[-1]["spread_bps"], s[0]["spread_bps"])
        self.assertIn("dd", s[0])

    def test_interpret_negligible_leverage(self):
        txt = cr.interpret([{"spread_bps": 0.1}], leverage_pct=0.3)
        self.assertIn("negligible", txt)
        self.assertIn("put-skew", txt)

    def test_interpret_widening(self):
        series = [{"spread_bps": 40.0}] * 21 + [{"spread_bps": 90.0}]
        txt = cr.interpret(series, leverage_pct=30.0)
        self.assertIn("WIDENING", txt)


class TestSkewGauge(unittest.TestCase):
    def test_gauge_fields(self):
        calls = [{"strike": 110, "delta": 0.25, "iv": 0.30, "bid": 1.0, "ask": 1.2}]
        puts = [{"strike": 90, "delta": -0.25, "iv": 0.38, "bid": 1.5, "ask": 1.7},
                {"strike": 80, "delta": -0.10, "iv": 0.45, "bid": 0.5, "ask": 0.7}]
        g = cr.skew_gauge(calls, puts, spot=100.0, dte=120)
        self.assertAlmostEqual(g["rr25_vol_pts"], 8.0, places=6)
        self.assertEqual(g["crash_put"]["strike"], 80)
        self.assertEqual(g["crash_put"]["otm_pct"], 20.0)
        # 0.6 mid / 100 spot × 365/120 → ~1.83%/yr
        self.assertAlmostEqual(g["crash_put"]["annualized_pct"], 1.83, delta=0.02)

    def test_gauge_none_when_empty(self):
        self.assertIsNone(cr.skew_gauge([], [], 100.0, 30))


def _bars(n=200, start="2026-01-02", wiggle=0.012):
    """Daily bars, flat with a day-to-day wiggle so HV is not zero."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    out = []
    for i in range(n):
        c = 100.0 * (1 + wiggle * ((-1) ** i))
        out.append({"date": (d0 + timedelta(days=i)).isoformat() + "T12:00:00-04:00",
                    "open": c, "high": c, "low": c, "close": c, "volume": 1000})
    return out


class TestFiledShareCounts(unittest.TestCase):
    """v5.48: a past day's market cap is that day's close times the share
    count filed for that day, not today's count."""

    def test_each_day_takes_its_own_count(self):
        bars = _bars()
        mid = bars[140]["date"][:10]
        first = bars[70]["date"][:10]
        # No count before the first filing; then 1.0; then an issue doubles it.
        filed = lambda day: None if day < first else (1.0 if day < mid else 2.0)
        s = cr.merton_series(bars, filed, debt_point=60.0, r=0.04)
        self.assertTrue(s)
        self.assertTrue(all(p["date"] >= first for p in s), "a day with no count is left out")
        before = [p for p in s if p["date"] < mid][-1]
        after = [p for p in s if p["date"] >= mid][0]
        self.assertGreater(before["leverage_pct"], after["leverage_pct"] * 1.5,
                           "half the shares before the issue: half the equity, more leverage")
        const = {p["date"]: p for p in cr.merton_series(bars, 2.0, 60.0, 0.04)}
        self.assertEqual(const[after["date"]], after, "the same as a constant count where they agree")
        self.assertNotEqual(const[before["date"]], before, "and not where today's count was wrong")

    def test_each_day_takes_its_own_debt(self):
        # v5.49: the default point a past day is measured against is the
        # balance sheet the company had then, not the latest.
        bars = _bars()
        mid = bars[140]["date"][:10]
        debt = lambda day: 30.0 if day < mid else 90.0
        s = cr.merton_series(bars, 1.0, debt, 0.04)
        before = [p for p in s if p["date"] < mid][-1]
        after = [p for p in s if p["date"] >= mid][0]
        self.assertGreater(after["leverage_pct"], before["leverage_pct"] * 2, "three times the debt after")
        none_before = cr.merton_series(bars, 1.0, lambda day: None if day < mid else 90.0, 0.04)
        self.assertTrue(none_before and all(p["date"] >= mid for p in none_before),
                        "a day with no balance sheet is left out")

    def test_a_constant_count_still_works(self):
        s = cr.merton_series(_bars(), 1.0, 60.0, 0.04)
        self.assertGreater(len(s), 100)


class _CreditBuild(unittest.TestCase):
    """build_credit_risk on stubbed sources: Schwab bars, Yahoo's profile,
    the share history and FRED."""

    def setUp(self):
        import options_dashboard as od
        self.od = od
        self.bars = _bars(300, start="2025-08-01")
        bars = self.bars

        class Schwab:
            def get_price_history(self, symbol, days=420):
                return list(bars)

            def get_option_chain(self, *a, **k):
                return {}

        class T:
            def __init__(self, s):
                self.info = {"sharesOutstanding": 3.0, "totalDebt": 100.0}
                self.balance_sheet = None

        class YF:
            Ticker = T

        self.saved = {k: getattr(od, k) for k in ("_schwab", "yf", "risk_free_rate", "_SHIST_AVAILABLE")}
        self.saved_get = od._shist.get if od._shist is not None else None
        self.saved_fred = od._treasury._fred_series
        od._schwab = lambda: Schwab()
        od.yf = YF
        od.risk_free_rate = lambda: (0.04, "test")
        od._treasury._fred_series = lambda *a, **k: []
        od._SHIST_AVAILABLE = True
        od._CREDIT_CACHE.clear()
        # SEC balance sheets (v5.49): none unless a test gives some.
        import fundamentals
        self.fnd = fundamentals
        self.saved_facts = fundamentals.company_facts
        self.facts = None
        fundamentals.company_facts = lambda sym, *a, **k: self.facts
        self.first = bars[100]["date"][:10]
        self.mid = bars[200]["date"][:10]
        self.hist = {"ok": True, "status": "ok", "symbol": "TEST", "max_age_days": 200,
                     "points": [{"as_of": self.first, "shares": 1.0, "form": "10-Q"},
                                {"as_of": self.mid, "shares": 2.0, "form": "10-Q"}],
                     "coverage": {"from": self.first, "to": self.mid},
                     "latest": {"as_of": self.mid, "shares": 2.0, "form": "10-Q"},
                     "price_check": {"closes": [[b["date"][:10], b["close"]] for b in bars[::21]]},
                     "source": "SEC filings"}
        self.ok = self.hist
        self.answer = lambda sym, wait=3.0: self.hist
        od._shist.get = lambda sym, wait=3.0: self.answer(sym, wait)

    def tearDown(self):
        od = self.od
        for k, v in self.saved.items():
            setattr(od, k, v)
        od._shist.get = self.saved_get
        od._treasury._fred_series = self.saved_fred
        od._CREDIT_CACHE.clear()
        self.fnd.company_facts = self.saved_facts


class TestBuildUsesFiledCounts(_CreditBuild):
    """The endpoint: the history uses the filed counts (share_history);
    without them only today's spread is shown, from today's count, and said."""

    def test_the_history_uses_the_filed_counts(self):
        out = self.od.build_credit_risk("TEST")
        m = out["merton"]
        self.assertEqual("filed", m["inputs"]["shares_basis"])
        self.assertEqual(2.0, m["inputs"]["shares_outstanding"], "the newest filed count")
        dates = [p["date"] for p in m["series"]]
        self.assertGreaterEqual(min(dates), self.first, "no market cap before the first count")
        before = [p for p in m["series"] if p["date"] < self.mid][-1]
        after = [p for p in m["series"] if p["date"] >= self.mid][0]
        self.assertGreater(before["leverage_pct"], after["leverage_pct"] * 1.5,
                           "the day before the issue had half the shares, not today's count")
        self.assertIsNone(m.get("history_note"))
        self.assertTrue(any("filed" in n and "SEC" in n for n in out["notes"]))
        self.assertFalse(any("CURRENT share count" in n for n in out["notes"]))

    def test_without_filed_counts_only_today_is_shown(self):
        self.hist = {"ok": True, "status": "unsupported", "symbol": "TEST",
                     "reason": "A foreign filer: it reports its share count once a year."}
        out = self.od.build_credit_risk("TEST")
        m = out["merton"]
        self.assertEqual(1, len(m["series"]), "today's spread only, never a past one on today's count")
        self.assertEqual(self.bars[-1]["date"][:10], m["series"][0]["date"])
        self.assertEqual("today_only", m["inputs"]["shares_basis"])
        self.assertEqual(3.0, m["inputs"]["shares_outstanding"], "Yahoo's count, for today")
        self.assertIn("foreign filer", m["history_note"])

    def test_prices_on_another_basis_are_not_multiplied(self):
        self.hist = dict(self.hist, price_check={"closes": [[b["date"][:10], b["close"] * 1.3]
                                                            for b in self.bars[::21]]})
        out = self.od.build_credit_risk("TEST")
        m = out["merton"]
        self.assertEqual(1, len(m["series"]))
        self.assertIn("unadjusted closes", m["history_note"])

    def test_a_history_still_being_read_is_asked_again_soon(self):
        self.hist = {"ok": True, "status": "building", "symbol": "TEST", "refreshing": True,
                     "reason": "Reading this company's SEC filings for its share counts."}
        out = self.od.build_credit_risk("TEST")
        self.assertEqual(1, len(out["merton"]["series"]))
        self.assertIn("being read", out["merton"]["history_note"])
        self.hist = self.ok
        hit = self.od._CREDIT_CACHE["TEST"]
        self.od._CREDIT_CACHE["TEST"] = (hit[0] - self.od._CREDIT_RETRY_TTL - 1,) + tuple(hit[1:])
        again = self.od.build_credit_risk("TEST")
        self.assertEqual("filed", again["merton"]["inputs"]["shares_basis"], "asked again within minutes, not an hour")


    # Codex, #433: a partial history is said, and asked again when the share
    # history itself retries it.
    def test_a_partial_history_is_said(self):
        self.hist = dict(self.ok, partial=True,
                         warnings=["1 filing could not be read from SEC just now; the history is retried in a few minutes.",
                                   "Share classes counted together: Class A 1.00B."])
        out = self.od.build_credit_risk("TEST")
        m = out["merton"]
        self.assertEqual("filed", m["inputs"]["shares_basis"], "the counts it has are still the filed ones")
        self.assertIn("could not be read", m["history_note"] or "")
        self.assertNotIn("Share classes", m["history_note"] or "", "only what makes it incomplete")

    def test_retried_when_the_share_history_is(self):
        od = self.od
        self.hist = dict(self.ok, partial=True, warnings=["1 filing could not be read from SEC just now."])
        od.build_credit_risk("TEST")
        self.assertEqual(od._shist.PARTIAL_TTL_S, od._CREDIT_CACHE["TEST"][2],
                         "a partial history is retried when the share history is, not every two minutes")
        od._CREDIT_CACHE.clear()
        self.hist = {"ok": True, "status": "building", "symbol": "TEST", "refreshing": True, "reason": "Reading."}
        od.build_credit_risk("TEST")
        self.assertEqual(od._CREDIT_RETRY_TTL, od._CREDIT_CACHE["TEST"][2], "still being read: minutes")
        od._CREDIT_CACHE.clear()
        self.hist = self.ok
        od.build_credit_risk("TEST")
        self.assertEqual(od._CREDIT_TTL, od._CREDIT_CACHE["TEST"][2], "whole: an hour")

    # Codex, #433 (second review): the page is told when to ask again.
    def test_the_page_is_told_when_to_ask_again(self):
        od = self.od
        self.hist = {"ok": True, "status": "building", "symbol": "TEST", "refreshing": True, "reason": "Reading."}
        out = od.build_credit_risk("TEST")
        self.assertTrue(0 < out["merton"]["retry_after_s"] <= od._CREDIT_RETRY_TTL)
        hit = od._CREDIT_CACHE["TEST"]
        od._CREDIT_CACHE["TEST"] = (hit[0] - 100,) + tuple(hit[1:])
        self.assertLessEqual(od.build_credit_risk("TEST")["merton"]["retry_after_s"], od._CREDIT_RETRY_TTL - 100 + 1,
                             "from the cache: the time left, not the whole wait")
        self.assertNotIn("retry_after_s", od._CREDIT_CACHE["TEST"][1]["merton"], "the cached answer is not changed")
        od._CREDIT_CACHE.clear()
        self.hist = self.ok
        self.assertIsNone(od.build_credit_risk("TEST")["merton"].get("retry_after_s"), "whole: nothing to wait for")

    # Codex, #433 (third review): a partial history kept by another caller
    # may be seconds from its rebuild; the card waits that long, not ten minutes.
    def test_a_partial_history_is_retried_when_it_is_rebuilt(self):
        od = self.od
        saved = od._shist.expires_in
        od._shist.expires_in = lambda sym: 10.0
        try:
            self.hist = dict(self.ok, partial=True, warnings=["1 filing could not be read from SEC just now."])
            out = od.build_credit_risk("TEST")
            self.assertLessEqual(od._CREDIT_CACHE["TEST"][2], 30, "rebuilt with the share history, in seconds")
            self.assertLessEqual(out["merton"]["retry_after_s"], 31)
        finally:
            od._shist.expires_in = saved

    # Codex, #433 (fifth review): when no filed count reaches any day, the
    # spread is missing for want of share counts, not of debt.
    def test_no_count_for_these_days_is_not_called_missing_debt(self):
        class T:
            def __init__(self, s):
                self.info = {"sharesOutstanding": None, "totalDebt": 100.0}
                self.balance_sheet = None
        self.od.yf = type("YF", (), {"Ticker": T})
        old = {"as_of": "2024-01-02", "shares": 1.0, "form": "10-K"}
        self.hist = dict(self.ok, points=[old], latest=old, coverage={"from": old["as_of"], "to": old["as_of"]})
        m = self.od.build_credit_risk("TEST")["merton"]
        self.assertEqual([], m["series"])
        self.assertIsNotNone(m["inputs"]["default_point"], "the debt is there")
        self.assertNotIn("debt", m["unavailable_reason"])
        self.assertIn("share count", m["unavailable_reason"])

    # Codex, #433 (sixth review): a series that stops weeks ago (the newest
    # filed count too old, no current count from Yahoo) has no current
    # spread; its last point is not shown as today's.
    def test_a_series_that_stops_early_has_no_current_spread(self):
        class T:
            def __init__(self, s):
                self.info = {"sharesOutstanding": None, "totalDebt": 100.0}
                self.balance_sheet = None
        self.od.yf = type("YF", (), {"Ticker": T})
        old = {"as_of": self.bars[60]["date"][:10], "shares": 1.0, "form": "10-Q"}
        self.hist = dict(self.ok, points=[old], latest=old, coverage={"from": old["as_of"], "to": old["as_of"]})
        m = self.od.build_credit_risk("TEST")["merton"]
        self.assertTrue(m["series"], "the history it has is still drawn")
        self.assertLess(m["series"][-1]["date"], self.bars[-1]["date"][:10])
        self.assertIsNone(m["latest"], "not presented as the current spread")
        self.assertIn("No current model spread", m["interpretation"])
        self.assertIn(m["series"][-1]["date"], m["interpretation"])

    # Codex, #433 (seventh review): a recent listing with counts and debt
    # but under sixty days of prices is short of price history, not of
    # share counts.
    def test_a_recent_listing_is_short_of_prices_not_counts(self):
        bars = self.bars[-40:]

        class Schwab:
            def get_price_history(self, symbol, days=420):
                return list(bars)

            def get_option_chain(self, *a, **k):
                return {}
        self.od._schwab = lambda: Schwab()
        one = {"as_of": bars[0]["date"][:10], "shares": 1.0, "form": "10-Q"}
        self.hist = dict(self.ok, points=[one], latest=one, coverage={"from": one["as_of"], "to": one["as_of"]},
                         price_check={"closes": []})
        m = self.od.build_credit_risk("TEST")["merton"]
        self.assertEqual([], m["series"])
        self.assertNotIn("share count", m["unavailable_reason"])
        self.assertIn("60 trading days", m["unavailable_reason"])

    # v5.49: with Schwab down the model had no price history at all; it
    # now falls back to Yahoo, as the charts do.
    def test_without_schwab_the_prices_come_from_yahoo(self):
        import pandas as pd
        bars = self.bars

        class T:
            def __init__(self, s):
                self.info = {"sharesOutstanding": 3.0, "totalDebt": 100.0}
                self.balance_sheet = None

            def history(self, **k):
                idx = pd.to_datetime([b["date"][:10] for b in bars])
                c = [b["close"] for b in bars]
                return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": [1000] * len(c)}, index=idx)
        self.od.yf = type("YF", (), {"Ticker": T})
        self.od._schwab = lambda: None
        m = self.od.build_credit_risk("TEST")["merton"]
        self.assertIsNone(m["unavailable_reason"])
        self.assertTrue(m["series"], "drawn from Yahoo's prices")
        self.assertEqual(bars[-1]["date"][:10], m["series"][-1]["date"])


def _instant(end, val, form="10-Q"):
    return {"end": end, "val": val, "filed": end, "form": form}


class TestFiledDebt(_CreditBuild):
    """v5.49: the default point each day is from the balance sheet the
    company filed for it (SEC), not the latest one held for every day."""

    def setUp(self):
        super().setUp()
        one = {"as_of": self.bars[0]["date"][:10], "shares": 1.0, "form": "10-K"}
        self.hist = dict(self.ok, points=[one], latest=one,
                         coverage={"from": one["as_of"], "to": one["as_of"]})
        self.d1 = self.bars[30]["date"][:10]
        self.d2 = self.bars[200]["date"][:10]

    def _sec(self, short, long, last=None):
        return {"facts": {"us-gaap": {
            "LongTermDebtCurrent": {"units": {"USD": [_instant(d, v) for d, v in short]}},
            "LongTermDebtNoncurrent": {"units": {"USD": [_instant(d, v) for d, v in long]}}}}}

    def test_each_day_takes_the_balance_sheet_dated_before_it(self):
        self.facts = self._sec([(self.d1, 2.0), (self.d2, 8.0)], [(self.d1, 8.0), (self.d2, 32.0)])
        m = self.od.build_credit_risk("TEST")["merton"]
        i = m["inputs"]
        self.assertEqual("sec", i["default_point_source"])
        self.assertAlmostEqual(8.0 + 16.0, i["default_point"], msg="the newest balance sheet: short + half long")
        self.assertEqual(self.d2, i["default_point_as_of"])
        before = [p for p in m["series"] if p["date"] < self.d2][-1]
        after = [p for p in m["series"] if p["date"] >= self.d2][0]
        self.assertGreater(after["leverage_pct"], before["leverage_pct"] * 2.5,
                           "a quarter of the debt before the new balance sheet, not today's")

    def test_a_filer_that_stopped_tagging_its_debt_falls_back_to_yahoo(self):
        # Plug Power: its debt tags stop in 2024 while its filings go on.
        old = self.bars[0]["date"][:10]
        self.facts = self._sec([(old, 2.0)], [(old, 8.0)])
        m = self.od.build_credit_risk("TEST")["merton"]
        self.assertEqual("yahoo", m["inputs"]["default_point_source"])
        self.assertAlmostEqual(75.0, m["inputs"]["default_point"], msg="75% of Yahoo's total debt")
        self.assertIn("Yahoo", m["debt_note"])
        self.assertTrue(m["series"], "still drawn, from Yahoo's latest balance sheet")

    def test_no_sec_facts_says_where_the_debt_came_from(self):
        out = self.od.build_credit_risk("TEST")
        self.assertEqual("yahoo", out["merton"]["inputs"]["default_point_source"])
        self.assertTrue(any("Yahoo" in n and "default point" in n for n in out["notes"]))
        self.assertFalse(any("most recent quarterly balance sheet" in n for n in out["notes"]))

    def test_sec_debt_is_said_in_the_notes(self):
        self.facts = self._sec([(self.d1, 2.0), (self.d2, 8.0)], [(self.d1, 8.0), (self.d2, 32.0)])
        out = self.od.build_credit_risk("TEST")
        self.assertTrue(any("balance sheet" in n and "dated on or before" in n for n in out["notes"]))
        self.assertIsNone(out["merton"].get("debt_note"))


if __name__ == "__main__":
    unittest.main()
