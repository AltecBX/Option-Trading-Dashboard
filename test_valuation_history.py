"""test_valuation_history.py (v5.49) — Priced for Perfection's trailing
EV/sales history: each day's market cap on one split basis, and each day's
net debt from the balance sheet it had. Stubbed Yahoo and SEC; no network.
Run:  python3 -m unittest test_valuation_history
"""
import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import pandas as pd

import fundamentals
import perfection_data as pdm
import share_history

N = 750                        # three years of trading days
IDX = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=N)
SPLIT = IDX[-120]              # a ten-for-one split six months ago
DAY = lambda ts: ts.strftime("%Y-%m-%d")


def closes():
    """Split-adjusted closes, as Yahoo's auto_adjust=False gives them: 100,
    with a wiggle."""
    return [100.0 * (1 + 0.01 * ((-1) ** i)) for i in range(N)]


def income():
    """Twelve quarters of revenue 25B and net income 5B: TTM 100B and 20B."""
    cols = pd.date_range(end=IDX[-1], periods=13, freq="QE")[:-1]
    return pd.DataFrame([[25e9] * 12, [5e9] * 12], index=["Total Revenue", "Net Income"], columns=cols)


class Ticker:
    def __init__(self, shares, splits):
        self._shares, self.splits = shares, splits
        self.quarterly_income_stmt = income()

    def history(self, **k):
        return pd.DataFrame({"Close": closes()}, index=IDX)

    def get_shares_full(self, **k):
        return self._shares


AS_REPORTED = pd.Series([1e9, 10e9], index=[IDX[0], SPLIT])     # a tenth before the split
ADJUSTED = pd.Series([10e9, 10e9], index=[IDX[0], SPLIT])        # already on today's basis
SPLITS = pd.Series([10.0], index=[SPLIT])


class _Base(unittest.TestCase):
    def setUp(self):
        pdm._CACHE.clear()
        self.saved = (pdm._yft, share_history.get, fundamentals.company_facts)
        self.tk = Ticker(AS_REPORTED, SPLITS)
        pdm._yft = lambda sym: self.tk
        self.hist = {"ok": True, "status": "unsupported", "reason": "test"}
        share_history.get = lambda sym, wait=3.0: self.hist
        self.facts = None
        fundamentals.company_facts = lambda sym, *a, **k: self.facts

    def tearDown(self):
        pdm._yft, share_history.get, fundamentals.company_facts = self.saved
        pdm._CACHE.clear()

    def run_it(self, fund=None):
        issues = []
        out = pdm._valuation_history("TEST", fund or {"net_debt": 0.0}, issues)
        self.assertEqual([], issues)
        return out


class TestOneSplitBasis(_Base):
    def test_yahoos_counts_are_put_on_todays_split_basis(self):
        # Before the fix: split-adjusted prices times as-reported counts put
        # the market cap ten times too low before the split (Nvidia, June 2024).
        out = self.run_it()
        self.assertAlmostEqual(10.0, out["evs_current_trailing"], delta=0.2)
        self.assertLess(abs(out["evs_expansion_180d_pct"]), 5, "no step at the split")
        self.assertIn("today's split basis", out["note"])

    def test_a_count_on_the_new_basis_dated_just_before_the_split(self):
        # Nvidia: Yahoo has 24.6B on 2024-06-08, two days before the split
        # of 2024-06-10, and 2.56B on 2024-05-31.
        self.tk = Ticker(pd.Series([1e9, 10e9, 10e9], index=[IDX[0], SPLIT - pd.Timedelta(days=2), SPLIT]), SPLITS)
        out = self.run_it()
        self.assertLess(abs(out["evs_expansion_180d_pct"]), 5, "no step at the split")
        self.assertAlmostEqual(10.0, out["evs_current_trailing"], delta=0.2)

    def test_counts_already_on_todays_basis_are_left_alone(self):
        self.tk = Ticker(ADJUSTED, SPLITS)
        out = self.run_it()
        self.assertLess(abs(out["evs_expansion_180d_pct"]), 5, "not multiplied a second time")
        self.assertAlmostEqual(10.0, out["evs_current_trailing"], delta=0.2)

    def test_the_filed_counts_come_first(self):
        self.tk = Ticker(pd.Series([1.0], index=[IDX[0]]), SPLITS)     # nonsense if used
        one = {"as_of": DAY(IDX[0]), "shares": 10e9, "form": "10-K"}
        self.hist = {"ok": True, "status": "ok", "max_age_days": 10000, "points": [one], "latest": one,
                     "price_check": {"closes": [[DAY(IDX[i]), c] for i, c in enumerate(closes()) if i % 21 == 0]}}
        out = self.run_it()
        self.assertAlmostEqual(10.0, out["evs_current_trailing"], delta=0.2)
        self.assertIn("filed", out["note"])

    def test_a_share_history_still_being_read_is_not_kept_a_day(self):
        self.hist = {"ok": True, "status": "building", "reason": "Reading."}
        self.run_it()
        self.assertLessEqual(pdm._CACHE["shares:TEST"][2], 900, "asked again once the filed counts are in")


class TestNetDebtEachDay(_Base):
    def _sec(self, rows):
        return {"facts": {"us-gaap": {"LongTermDebtNoncurrent": {"units": {"USD": [
            {"end": d, "val": v, "filed": d, "form": "10-Q"} for d, v in rows]}}}}}

    def test_each_day_has_the_debt_it_had(self):
        # The company borrows as much as it is worth four months ago: its EV
        # doubled then. Held at the latest value, the history never moved.
        borrowed = IDX[-85]
        quarters = [IDX[i] for i in range(0, N, 63)] + [borrowed]
        self.facts = self._sec(sorted((DAY(q), 1000e9 if q >= borrowed else 0.0) for q in quarters))
        out = self.run_it({"net_debt": 1000e9})
        self.assertGreater(out["evs_expansion_180d_pct"], 80, "EV doubled when the debt came")
        self.assertIn("SEC balance sheet", out["note"])

    def test_a_stale_sec_history_holds_yahoos_latest(self):
        self.facts = self._sec([(DAY(IDX[0]), 5e9), (DAY(IDX[300]), 5e9)])
        out = self.run_it({"net_debt": 1000e9})
        self.assertLess(abs(out["evs_expansion_180d_pct"]), 5)
        self.assertIn("held at Yahoo's latest", out["note"])


if __name__ == "__main__":
    unittest.main()
