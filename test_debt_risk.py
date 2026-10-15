"""Tests for debt_risk.py: the Debt & rates screen and card (v5.39)."""

import unittest
from datetime import date, timedelta
from pathlib import Path

import debt_risk as D

# A Yahoo profile shaped like CoreWeave's in October 2026: a lot of debt
# against little operating profit, burning cash, heavily shorted.
HEAVY = {"totalDebt": 51.6e9, "totalCash": 5.5e9, "ebitda": 3.8e9, "freeCashflow": -9.1e9,
         "marketCap": 36.3e9, "sharesShort": 60e6, "sharesShortPriorMonth": 50e6,
         "shortPercentOfFloat": 0.192, "financialCurrency": "USD", "currency": "USD"}
# Apple-like: debt, but more than covered.
LIGHT = {"totalDebt": 84e9, "totalCash": 62e9, "ebitda": 168e9, "freeCashflow": 108e9,
         "marketCap": 5.0e12, "sharesShort": 120e6, "sharesShortPriorMonth": 140e6,
         "shortPercentOfFloat": 0.008}
NET_CASH = {"totalDebt": 0.2e9, "totalCash": 9.4e9, "ebitda": 2.7e9, "freeCashflow": 2.2e9,
            "marketCap": 480e9, "shortPercentOfFloat": 0.4}


class Screen(unittest.TestCase):
    def test_the_numbers_and_their_units(self):
        f = D.screen_fields(HEAVY)
        self.assertAlmostEqual(46.1e9, f["net_debt"])
        self.assertAlmostEqual(12.13, f["net_debt_ebitda"], places=2)
        self.assertAlmostEqual(1.421, f["debt_to_mcap"], places=3)
        self.assertAlmostEqual(19.2, f["short_pct"], msg="a fraction becomes a percent")
        self.assertAlmostEqual(20.0, f["short_chg"], msg="60M short against 50M a month earlier")
        self.assertEqual("USD", f["debt_ccy"])

    def test_missing_stays_missing(self):
        f = D.screen_fields({"marketCap": 1e9})
        for k in ("debt_total", "net_debt", "net_debt_ebitda", "debt_to_mcap", "short_pct", "short_chg"):
            self.assertIsNone(f[k], k)
        self.assertIsNone(D.screen_fields(None)["debt_total"])

    def test_no_debt_ratio_against_negative_ebitda(self):
        f = D.screen_fields(dict(HEAVY, ebitda=-1e9))
        self.assertIsNone(f["net_debt_ebitda"])

    def test_a_foreign_filer_does_not_mix_currencies(self):
        # Novo Nordisk: debt in kroner, market value in dollars.
        f = D.screen_fields(dict(LIGHT, financialCurrency="DKK", currency="USD"))
        self.assertEqual("DKK", f["debt_ccy"])
        self.assertIsNone(f["debt_to_mcap"])
        self.assertIsNotNone(f["net_debt_ebitda"], "debt and EBITDA share a currency")


class Assess(unittest.TestCase):
    def test_heavy_debt_burning_cash_and_shorted_is_high_with_reasons(self):
        a = D.assess(D.screen_fields(HEAVY))
        self.assertEqual("High", a["level"])
        self.assertEqual(8, a["score"], "2 leverage + 2 size + 2 burn + 1 shorted + 1 shorts rising")
        text = " ".join(a["reasons"])
        for bit in ("12.1x", "142%", "Burning cash", "19.2%", "piling in"):
            self.assertIn(bit, text)

    def test_covered_debt_is_low(self):
        a = D.assess(D.screen_fields(LIGHT))
        self.assertEqual("Low", a["level"])
        self.assertEqual(0, a["score"])

    def test_net_cash_is_low_whatever_else(self):
        a = D.assess(D.screen_fields(NET_CASH))
        self.assertEqual("Low", a["level"])
        self.assertIn("More cash than debt", a["reasons"][0])

    def test_shorts_alone_are_not_debt_risk(self):
        # Heavily shorted and rising, but the debt is covered: shorting a
        # story is not the debt trade.
        a = D.assess(D.screen_fields(dict(LIGHT, shortPercentOfFloat=0.3, sharesShort=200e6)))
        self.assertEqual("Low", a["level"])

    def test_no_operating_profit_counts_as_heavy(self):
        f = D.screen_fields(dict(LIGHT, ebitda=-2e9, freeCashflow=1e9, marketCap=1e12))
        a = D.assess(f)
        self.assertEqual(2, a["score"])
        self.assertEqual("Elevated", a["level"])
        self.assertIn("No operating profit", a["reasons"][0])

    def test_thresholds(self):
        mid = D.screen_fields({"totalDebt": 30e9, "totalCash": 0, "ebitda": 10e9, "freeCashflow": 1e9,
                               "marketCap": 100e9})
        a = D.assess(mid)                      # 3.0x and 30%: one point each
        self.assertEqual(2, a["score"])
        self.assertEqual("Elevated", a["level"])

    def test_no_debt_figure_is_no_level(self):
        self.assertIsNone(D.assess(D.screen_fields({"marketCap": 1e9}))["level"])

    def test_a_foreign_filer_names_its_currency(self):
        a = D.assess(D.screen_fields(dict(HEAVY, financialCurrency="DKK")))
        self.assertIn("DKK", " ".join(a["reasons"]))
        self.assertNotIn("$", " ".join(a["reasons"]))


def _days(n, start=date(2026, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class RateMove(unittest.TestCase):
    def test_a_stock_that_falls_two_percent_per_tenth(self):
        days = _days(150)
        y, c = {}, {}
        yv, cv = 4.0, 100.0
        for i, d in enumerate(days):
            if i:
                dy = (0.05 if i % 3 == 0 else -0.03 if i % 3 == 1 else 0.01)   # percent points
                yv += dy
                cv *= 1 + (-2.0 * dy / 0.10) / 100.0                          # -2% per +0.10%
            y[d], c[d] = yv, cv
        rb = D.rate_beta(c, y)
        self.assertAlmostEqual(-2.0, rb["move_per_10bp"], places=1)
        self.assertLess(rb["r"], -0.99)
        self.assertEqual(D.BETA_SESSIONS, rb["n"], "only the last six months count")

    def test_only_days_both_traded_and_enough_of_them(self):
        days = _days(80)
        y = {d: 4.0 + (i % 2) * 0.05 for i, d in enumerate(days)}
        c = {d: 100.0 - (i % 2) for i, d in enumerate(days[:50])}
        self.assertIsNone(D.rate_beta(c, y), "49 paired days are too few")
        self.assertIsNone(D.rate_beta({}, y))

    def test_row_fields_carry_it_all(self):
        days = _days(130)
        y = {d: 4.0 + (i % 2) * 0.1 for i, d in enumerate(days)}
        c = {d: 100.0 - (i % 2) * 2 for i, d in enumerate(days)}
        r = D.row_fields(HEAVY, c, y)
        self.assertEqual("High", r["debt_risk"])
        self.assertLess(r["rate_move"], 0)
        self.assertTrue(r["debt_reasons"])
        self.assertIsNone(D.row_fields(HEAVY, None, y)["rate_move"])


# ── the filings ──────────────────────────────────────────────────────────
def _annual(tag_vals, end, filed, form="10-K"):
    return {tag: {"end": end, "val": v, "filed": filed, "form": form, "fy": int(end[:4]), "fp": "FY"}
            for tag, v in tag_vals.items()}


def facts_with(*row_sets, quarters=None):
    gaap = {}
    for rows in row_sets:
        for tag, r in rows.items():
            gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(r)
    for tag, qs in (quarters or {}).items():
        gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].extend(qs)
    return {"facts": {"us-gaap": gaap}}


M = {t: v for (_l, t), v in zip(D.MATURITY_TAGS, (6.7e9, 4.3e9, 2.4e9, 1.8e9, 2.1e9, 4.3e9))}
M_OLD = {t: 1e9 for _l, t in D.MATURITY_TAGS}


def _quarters(tag, vals, last_end="2026-06-30"):
    """Four quarterly 10-Q facts ending at `last_end`, oldest first."""
    ends = []
    d = date.fromisoformat(last_end)
    for _ in vals:
        ends.insert(0, d)
        d = (d.replace(day=1) - timedelta(days=1))
        d = (d.replace(day=1) - timedelta(days=1))
        d = (d.replace(day=1) - timedelta(days=1))
    out = []
    prev_end = ends[0]
    for _ in range(3):                       # the month-end three months before the first quarter's end
        prev_end = prev_end.replace(day=1) - timedelta(days=1)
    for e, v in zip(ends, vals):
        s = prev_end + timedelta(days=1)
        prev_end = e
        out.append({"start": s.isoformat(), "end": e.isoformat(), "val": v, "filed": (e + timedelta(days=40)).isoformat(),
                    "form": "10-Q", "fy": e.year, "fp": "Q"})
    return {tag: out}


class Maturities(unittest.TestCase):
    def test_the_latest_annual_schedule(self):
        f = facts_with(_annual(M_OLD, "2024-12-31", "2025-02-20"), _annual(M, "2025-12-31", "2026-02-20"))
        m = D.maturities(f)
        self.assertEqual("2025-12-31", m["as_of"])
        self.assertEqual(["Next 12 months", "Year 2", "Year 3", "Year 4", "Year 5", "After year 5"],
                         [b["label"] for b in m["buckets"]])
        self.assertAlmostEqual(21.6e9, m["total"])
        self.assertAlmostEqual(11.0e9, m["due_2y"])
        self.assertAlmostEqual(11.0 / 21.6, m["due_2y_share"], places=3)

    def test_quarterly_rows_are_not_read(self):
        q = {t: dict(r, form="10-Q") for t, r in _annual({t: 9e9 for _l, t in D.MATURITY_TAGS},
                                                          "2026-06-30", "2026-08-01").items()}
        f = facts_with(_annual(M, "2025-12-31", "2026-02-20"), q)
        self.assertEqual("2025-12-31", D.maturities(f)["as_of"])

    def test_a_restatement_wins(self):
        newer = _annual(dict(M, **{D.MATURITY_TAGS[0][1]: 7.7e9}), "2025-12-31", "2026-05-01", form="10-K/A")
        m = D.maturities(facts_with(_annual(M, "2025-12-31", "2026-02-20"), newer))
        self.assertAlmostEqual(7.7e9, m["buckets"][0]["value"])

    def test_not_reported(self):
        self.assertIsNone(D.maturities(facts_with()))
        self.assertIsNone(D.maturities(None))


class Interest(unittest.TestCase):
    def test_four_quarters_of_the_latest_tag(self):
        f = facts_with(quarters={**_quarters("InterestExpense", [1, 1, 1, 1], last_end="2023-09-30"),
                                 **_quarters("InterestExpenseDebt", [0.4e9, 0.4e9, 0.4e9, 0.42e9])})
        i = D.interest_ttm(f)
        self.assertEqual("InterestExpenseDebt", i["tag"], "the tag reported most recently wins")
        self.assertAlmostEqual(1.62e9, i["value"])

    def test_an_old_figure_is_not_current(self):
        f = facts_with(quarters=_quarters("InterestExpense", [1e9] * 4, last_end="2023-09-30"))
        self.assertIsNotNone(D.interest_ttm(f))
        self.assertIsNone(D.interest_ttm(f, as_of="2025-09-27"), "two years stale against the balance sheet")


class Card(unittest.TestCase):
    def setUp(self):
        days = _days(300, start=date(2025, 9, 1))
        self.y = {d: 4.0 + i * 0.004 for i, d in enumerate(days)}

    def test_card_from_row_and_filings(self):
        row = D.row_fields(HEAVY, None, None)
        f = facts_with(_annual(M, "2025-12-31", "2026-02-20"),
                       quarters={**_quarters("InterestExpenseDebt", [0.4e9] * 4),
                                 **_quarters("OperatingIncomeLoss", [-0.1e9] * 4)})
        c = D.card("crwv", row, f, self.y)
        self.assertEqual("CRWV", c["symbol"])
        self.assertEqual("High", c["screen"]["debt_risk"])
        self.assertAlmostEqual(1.6e9 / 51.6e9 * 100, c["avg_rate"], places=1)
        self.assertIsNone(c["coverage"], "no cover to speak of when operating profit is a loss")
        flags = " ".join(c["filing_flags"])
        self.assertIn("within two years", flags)
        self.assertIn("operating loss", flags)
        self.assertGreater(c["rates"]["change_1m"], 0)

    def test_thin_cover(self):
        row = D.row_fields(LIGHT, None, None)
        f = facts_with(quarters={**_quarters("InterestExpense", [1e9] * 4),
                                 **_quarters("OperatingIncomeLoss", [2e9] * 4)})
        c = D.card("X", row, f, self.y)
        self.assertEqual(2.0, c["coverage"])
        self.assertIn("only 2.0x", " ".join(c["filing_flags"]))

    def test_a_stock_off_the_watchlist_and_without_filings(self):
        c = D.card("ZZZ", None, None, None)
        self.assertFalse(c["on_watchlist"])
        self.assertIsNone(c["maturities"])
        self.assertIsNone(c["rates"])

    def test_yield_context(self):
        y = {"2025-10-10": 4.05, "2026-09-12": 4.96, "2026-10-13": 5.21}
        c = D.yield_context(y)
        self.assertEqual(5.21, c["now"])
        self.assertAlmostEqual(0.25, c["change_1m"])
        self.assertAlmostEqual(1.16, c["change_1y"])



class WatchlistWiring(unittest.TestCase):
    """The scan reads the yield once and rates every row it builds."""

    def setUp(self):
        import watchlist_table as W
        self.W = W
        self.saved = (W._YIELD_FN, dict(W._SCAN_YIELDS))

    def tearDown(self):
        self.W._YIELD_FN, self.W._SCAN_YIELDS = self.saved[0], self.saved[1]

    def test_a_row_gets_its_rate_move_and_rating(self):
        import pandas as pd
        W = self.W
        days = _days(140)
        W.set_yield_provider(lambda: [(d, 4.0 + (i % 2) * 0.1) for i, d in enumerate(days)])
        W._SCAN_YIELDS = W._load_yields()
        close = pd.Series([100.0 - (i % 2) * 2 for i in range(len(days))], index=pd.to_datetime(days))
        row = dict(D.screen_fields(HEAVY))
        W._debt_fields(row, close)
        self.assertEqual("High", row["debt_risk"])
        self.assertAlmostEqual(-2.0, row["rate_move"], places=1)
        self.assertTrue(row["debt_reasons"])

    def test_no_yield_no_rate_move_but_still_rated(self):
        import pandas as pd
        W = self.W
        def boom():
            raise RuntimeError("FRED down")
        W.set_yield_provider(boom)
        W._SCAN_YIELDS = W._load_yields()
        self.assertEqual({}, W._SCAN_YIELDS)
        row = dict(D.screen_fields(LIGHT))
        W._debt_fields(row, pd.Series([1.0, 2.0]))
        self.assertIsNone(row["rate_move"])
        self.assertEqual("Low", row["debt_risk"])



class Codex428(unittest.TestCase):
    """Codex, #428."""

    def test_unknown_cash_is_not_zero_cash(self):
        f = D.screen_fields({k: v for k, v in HEAVY.items() if k != "totalCash"})
        self.assertIsNone(f["net_debt"], "debt minus an unknown cash balance is unknown")
        self.assertIsNone(f["net_debt_ebitda"])
        self.assertIsNotNone(f["debt_to_mcap"], "gross debt against market value still stands")

    def test_the_ten_year_reads_the_shared_full_history(self):
        # treasury caches FRED series by id alone: asking for 13 months
        # would leave its 3-year correlation reader short for half an hour.
        calls = []
        rows = [(f"2023-{m:02d}-01", 4.0) for m in range(1, 13)] + [("2026-10-13", 5.21)]

        def fred(series_id, *args, **kw):
            calls.append((series_id, args, kw))
            return rows
        got = D.ten_year_series(fred, today=date(2026, 10, 15))
        self.assertEqual([("DGS10", (), {})], calls, "no start date: the default, shared history")
        self.assertEqual([("2026-10-13", 5.21)], got, "sliced to the last 400 days here")
        self.assertEqual([], D.ten_year_series(None))

    def test_a_market_value_recovered_later_still_counts(self):
        import watchlist_table as W

        class FastInfo:
            market_cap = 36.3e9

        class Tk:
            info = {k: v for k, v in HEAVY.items() if k != "marketCap"}
            info.update(shortName="CoreWeave")
            fast_info = FastInfo()

            def get_earnings_dates(self, limit=12):
                return None

        class FakeYF:
            @staticmethod
            def Ticker(sym):
                return Tk()

        saved = W.yf
        W._FUND_CACHE.pop("ZZTEST", None)
        try:
            W.yf = FakeYF
            out = W._fundamentals("ZZTEST")
        finally:
            W.yf = saved
            W._FUND_CACHE.pop("ZZTEST", None)
        self.assertEqual(36.3e9, out["market_cap"])
        self.assertAlmostEqual(51.6e9 / 36.3e9, out["debt_to_mcap"], places=3)


class Codex428c(unittest.TestCase):
    """Codex, #428, third review: the card's path for a stock off the
    watchlist recovers the market value before working anything out."""

    def test_the_card_path_recovers_the_market_value_first(self):
        class FastInfo:
            market_cap = 36.3e9

        class Tk:
            info = {k: v for k, v in HEAVY.items() if k != "marketCap"}
            fast_info = FastInfo()

        out = D.ticker_fields(Tk(), {}, {})
        self.assertEqual(36.3e9, out["market_cap"])
        self.assertAlmostEqual(51.6e9 / 36.3e9, out["debt_to_mcap"], places=3)
        full = D.row_fields(HEAVY, {}, {})
        self.assertEqual(full["debt_score"], out["debt_score"], "the size points count too")

    def test_a_full_profile_is_read_as_is_and_a_broken_one_is_empty(self):
        class Tk:
            info = dict(HEAVY)

            @property
            def fast_info(self):
                raise AssertionError("not needed when .info has the market value")

        self.assertEqual(HEAVY["marketCap"], D.ticker_fields(Tk(), {}, {})["market_cap"])

        class Broken:
            @property
            def info(self):
                raise RuntimeError("throttled")

            @property
            def fast_info(self):
                raise RuntimeError("throttled")

        out = D.ticker_fields(Broken(), {}, {})
        self.assertIsNone(out["market_cap"])
        self.assertIsNone(out["debt_risk"])

    def test_the_card_uses_it(self):
        src = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()
        body = src[src.index("def build_debt_card"):]
        body = body[:body.index("\ndef ")]
        self.assertIn("_dr.ticker_fields(", body)
        self.assertNotIn("_dr.row_fields(", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Codex428e(unittest.TestCase):
    """Codex, #428 (fifth review): a maturity schedule with gaps."""

    def test_a_missing_later_year_leaves_the_share_unknown(self):
        gappy = {k: v for k, v in M.items() if k != D.MATURITY_TAGS[3][1]}       # Year 4 untagged
        m = D.maturities(facts_with(_annual(gappy, "2025-12-31", "2026-02-20")))
        self.assertFalse(m["complete"])
        self.assertAlmostEqual(11.0e9, m["due_2y"], msg="the first two years are both stated")
        self.assertIsNone(m["due_2y_share"], "a share of an incomplete schedule is not a share")
        c = D.card("x", D.row_fields(HEAVY, None, None),
                   facts_with(_annual(gappy, "2025-12-31", "2026-02-20")), {})
        self.assertNotIn("within two years", " ".join(c["filing_flags"]), "no wall from a gap")

    def test_a_missing_year_two_leaves_the_two_years_unknown(self):
        only_first = {D.MATURITY_TAGS[0][1]: 6.7e9}
        m = D.maturities(facts_with(_annual(only_first, "2025-12-31", "2026-02-20")))
        self.assertIsNone(m["due_2y"], "year two unknown is not year two zero")
        self.assertIsNone(m["due_2y_share"])

    def test_a_full_schedule_is_unchanged(self):
        m = D.maturities(facts_with(_annual(M, "2025-12-31", "2026-02-20")))
        self.assertTrue(m["complete"])
        self.assertAlmostEqual(11.0 / 21.6, m["due_2y_share"], places=3)

    def test_the_card_shows_no_two_year_figure_it_does_not_have(self):
        src = (Path(__file__).resolve().parent / "app-cards.jsx").read_text()
        body = src[src.index("const mat = d && d.maturities;"):]
        body = body[:body.index("annual report, {drDate(mat.as_of)}")]
        self.assertIn("mat.due_2y != null", body)


class Codex428f(unittest.TestCase):
    """Codex, #428 (seventh review): the SEC figures are in dollars."""

    def test_a_foreign_filers_rate_is_not_dollars_over_local_debt(self):
        twd = dict(HEAVY, totalDebt=1.6e12, totalCash=0.2e12, ebitda=0.4e12, freeCashflow=-0.1e12,
                   financialCurrency="TWD", currency="USD")
        row = D.row_fields(twd, None, None)
        self.assertEqual("TWD", row["debt_ccy"])
        f = facts_with(quarters=_quarters("InterestExpenseDebt", [0.4e9] * 4))
        c = D.card("tsm", row, f, {})
        self.assertEqual("USD", c["filing_ccy"])
        self.assertIsNotNone(c["interest"])
        self.assertIsNone(c["avg_rate"], "US dollars of interest over Taiwan dollars of debt")

    def test_a_dollar_filer_keeps_its_rate(self):
        f = facts_with(quarters=_quarters("InterestExpenseDebt", [0.4e9] * 4))
        c = D.card("crwv", D.row_fields(HEAVY, None, None), f, {})
        self.assertAlmostEqual(1.6e9 / 51.6e9 * 100, c["avg_rate"], places=1)

    def test_the_card_labels_filing_amounts_in_the_filings_currency(self):
        src = (Path(__file__).resolve().parent / "app-cards.jsx").read_text()
        body = src[src.index("const mat = d && d.maturities;") - 400:]
        body = body[:body.index("annual report, {drDate(mat.as_of)}") + 400]
        self.assertIn("const fccy = ", body)
        self.assertIn("drMoney(d.interest.value, fccy)", body)
        self.assertIn("drMoney(mat.due_2y, fccy)", body)
        self.assertNotIn("drMoney(b.value, ccy)", src)


class Codex428g(unittest.TestCase):
    """Codex, #428 (eighth review): no debt is no debt risk."""

    def test_zero_debt_without_a_cash_figure_is_low(self):
        f = D.screen_fields({"totalDebt": 0, "ebitda": -2e9, "freeCashflow": -1.5e9, "marketCap": 3e9,
                             "shortPercentOfFloat": 0.25, "sharesShort": 60e6, "sharesShortPriorMonth": 40e6})
        self.assertIsNone(f["net_debt"], "cash unknown")
        a = D.assess(f)
        self.assertEqual("Low", a["level"])
        self.assertEqual(0, a["score"])
        self.assertIn("No debt", a["reasons"][0])

    def test_some_debt_still_counts(self):
        f = D.screen_fields({"totalDebt": 1e9, "ebitda": -2e9, "freeCashflow": -1.5e9, "marketCap": 3e9})
        self.assertNotEqual("Low", D.assess(f)["level"])


class Codex428i(unittest.TestCase):
    """Codex, #428 (ninth review): interest and the debt schedule."""

    def test_a_new_tag_without_a_full_year_does_not_hide_interest(self):
        f = facts_with(quarters={**_quarters("InterestExpense", [0.3e9] * 4, last_end="2026-06-30"),
                                 **_quarters("InterestExpenseDebt", [0.4e9] * 2, last_end="2026-09-30")})
        i = D.interest_ttm(f)
        self.assertIsNotNone(i, "the older tag has four quarters")
        self.assertEqual("InterestExpense", i["tag"])
        self.assertAlmostEqual(1.2e9, i["value"])

    def test_an_old_schedule_is_not_current(self):
        f = facts_with(_annual(M, "2022-12-31", "2023-02-20"))
        self.assertIsNotNone(D.maturities(f))
        self.assertIsNotNone(D.maturities(f, as_of="2023-09-30"), "nine months on, still the latest")
        self.assertIsNone(D.maturities(f, as_of="2026-06-30"), "three years on, that debt has come and gone")

    def test_the_card_checks_its_age(self):
        src = (Path(__file__).resolve().parent / "debt_risk.py").read_text()
        body = src[src.index("def card("):]
        nxt = body.find("\ndef ")
        self.assertIn("maturities(facts, as_of=bs_as_of)", body if nxt < 0 else body[:nxt])


class Codex428j(unittest.TestCase):
    """Codex, #428 (tenth review): the latest balance sheet, not the latest debt figure."""

    def test_a_schedule_as_old_as_the_last_debt_figure_is_still_old(self):
        f = facts_with(_annual(M, "2022-12-31", "2023-02-20"),
                       _annual({"LongTermDebt": 5e9}, "2022-12-31", "2023-02-20"),
                       _annual({"Assets": 9e10}, "2026-06-30", "2026-08-01", form="10-Q"))
        c = D.card("x", D.row_fields(HEAVY, None, None), f, {})
        self.assertIsNone(c["maturities"], "the balance sheet is from 2026: that schedule is gone")
        g = facts_with(_annual(M, "2025-12-31", "2026-02-20"),
                       _annual({"Assets": 9e10}, "2026-06-30", "2026-08-01", form="10-Q"))
        self.assertIsNotNone(D.card("x", D.row_fields(HEAVY, None, None), g, {})["maturities"])
