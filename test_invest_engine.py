"""Tests for invest_engine — the arithmetic behind the Investment tab.

Two things are pinned hardest here, because the tab makes claims about them
in writing on screen:

  1. The earnings-per-share breakdown RECONCILES. The panel says the three
     contributions add up to the total change; that is asserted, not trusted.
  2. Nothing invents a number. A percentage change off a loss, a P/E on
     negative earnings, an index built on a negative base — each returns None
     with the reason left to the caller to say in words.
"""

import math
import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import invest_engine as E


class TestSafeArithmetic(unittest.TestCase):
    def test_safe_div_refuses_zero_and_junk(self):
        self.assertIsNone(E.safe_div(1, 0))
        self.assertIsNone(E.safe_div(None, 2))
        self.assertIsNone(E.safe_div("x", 2))
        self.assertAlmostEqual(E.safe_div(10, 4), 2.5)

    def test_pe_is_none_on_negative_earnings(self):
        # A negative P/E is an artifact, not a cheap stock. Printing "-14x"
        # invites exactly the wrong reading, so it is never printed.
        self.assertIsNone(E.price_earnings(10.0, -2.0))
        self.assertIsNone(E.price_earnings(10.0, 0.0))
        self.assertAlmostEqual(E.price_earnings(100.0, 5.0), 20.0)

    def test_market_cap_needs_both_halves_positive(self):
        self.assertIsNone(E.market_cap(None, 1e9))
        self.assertIsNone(E.market_cap(10.0, 0))
        self.assertAlmostEqual(E.market_cap(10.0, 1e9), 1e10)

    def test_yields(self):
        self.assertAlmostEqual(E.earnings_yield(5.0, 100.0), 0.05)
        self.assertAlmostEqual(E.fcf_yield(2e9, 4e10), 0.05)
        self.assertIsNone(E.fcf_yield(2e9, 0))


class TestGrowth(unittest.TestCase):
    def test_ordinary_growth(self):
        g = E.growth(112.0, 100.0)
        self.assertAlmostEqual(g["pct"], 12.0)
        self.assertEqual(g["direction"], "up")
        self.assertEqual(g["note"], "")

    def test_no_percentage_from_a_loss(self):
        # -100 -> -50 is not "+50% growth" and is not "-50%" either. The
        # percentage is withheld and the words describe what happened.
        g = E.growth(-50.0, -100.0)
        self.assertIsNone(g["pct"])
        self.assertEqual(g["direction"], "up")
        self.assertIn("narrowed", g["note"])

    def test_widening_loss_reads_down(self):
        g = E.growth(-150.0, -100.0)
        self.assertIsNone(g["pct"])
        self.assertEqual(g["direction"], "down")
        self.assertIn("widened", g["note"])

    def test_turning_profitable_and_swinging_to_loss(self):
        up = E.growth(5.0, -10.0)
        self.assertIsNone(up["pct"])
        self.assertEqual(up["direction"], "up")
        self.assertIn("Turned profitable", up["note"])
        # A POSITIVE base keeps its percentage even when the result is a
        # loss — that arithmetic is well defined — but it also carries the
        # sentence, because "-150%" on its own reads like a decline rather
        # than a change of sign.
        down = E.growth(-5.0, 10.0)
        self.assertAlmostEqual(down["pct"], -150.0)
        self.assertEqual(down["direction"], "down")
        self.assertIn("Swung to a loss", down["note"])

    def test_zero_base_and_missing_inputs(self):
        self.assertIsNone(E.growth(5.0, 0.0)["pct"])
        g = E.growth(5.0, None)
        self.assertIsNone(g["pct"])
        self.assertEqual(g["direction"], "unknown")


class TestLogDecomposition(unittest.TestCase):
    P = {"revenue": 100.0, "net_income": 10.0, "shares": 50.0}
    C = {"revenue": 120.0, "net_income": 15.0, "shares": 48.0}

    def test_contributions_reconcile_exactly(self):
        d = E.log_decomposition(self.P, self.C)
        self.assertEqual(d["method"], "log")
        total = sum(c["value"] for c in d["contributions"])
        self.assertAlmostEqual(total, d["total"], places=10)

    def test_total_is_the_actual_log_change_in_eps(self):
        d = E.log_decomposition(self.P, self.C)
        expected = math.log((15.0 / 48.0) / (10.0 / 50.0)) * 100.0
        self.assertAlmostEqual(d["total"], expected, places=10)

    def test_buyback_contributes_positively(self):
        # The share count FELL from 50 to 48, which lifts earnings per share.
        d = E.log_decomposition(self.P, self.C)
        share = [c for c in d["contributions"] if c["driver"] == "Share count"][0]
        self.assertGreater(share["value"], 0)

    def test_refuses_when_anything_is_non_positive(self):
        for bad in ({"revenue": 0.0, "net_income": 10.0, "shares": 50.0},
                    {"revenue": 100.0, "net_income": -10.0, "shares": 50.0},
                    {"revenue": 100.0, "net_income": 10.0, "shares": 0.0}):
            self.assertIsNone(E.log_decomposition(bad, self.C))
            self.assertIsNone(E.log_decomposition(self.P, bad))


class TestDollarBridge(unittest.TestCase):
    def test_reconciles_through_a_loss(self):
        prior = {"revenue": 100.0, "net_income": -10.0, "shares": 50.0}
        cur = {"revenue": 120.0, "net_income": 5.0, "shares": 60.0}
        d = E.dollar_bridge(prior, cur)
        self.assertEqual(d["method"], "dollar")
        self.assertAlmostEqual(sum(c["value"] for c in d["contributions"]),
                               d["total"], places=10)
        self.assertAlmostEqual(d["total"], 5.0 / 60.0 - (-10.0 / 50.0), places=10)

    def test_shapley_is_order_independent(self):
        # The whole reason for averaging over orderings: a single sequential
        # walk-down gives a different answer depending on which driver is
        # moved first. The Shapley values must not.
        prior = {"revenue": 90.0, "net_income": -4.0, "shares": 20.0}
        cur = {"revenue": 140.0, "net_income": 22.0, "shares": 33.0}
        a = E.dollar_bridge(prior, cur)
        b = E.dollar_bridge(dict(reversed(list(prior.items()))),
                            dict(reversed(list(cur.items()))))
        for ca, cb in zip(a["contributions"], b["contributions"]):
            self.assertEqual(ca["driver"], cb["driver"])
            self.assertAlmostEqual(ca["value"], cb["value"], places=12)

    def test_pre_revenue_falls_back_to_two_drivers(self):
        # A company with no revenue has no profit margin to split out. It
        # still gets an honest bridge rather than nothing.
        prior = {"revenue": 0.0, "net_income": -10.0, "shares": 5.0}
        cur = {"revenue": 0.0, "net_income": -20.0, "shares": 20.0}
        d = E.dollar_bridge(prior, cur)
        self.assertEqual([c["driver"] for c in d["contributions"]],
                         ["Net income", "Share count"])
        self.assertAlmostEqual(sum(c["value"] for c in d["contributions"]),
                               d["total"], places=10)
        self.assertIn("no profit margin", d["note"])

    def test_refuses_without_a_share_count(self):
        self.assertIsNone(E.dollar_bridge(
            {"revenue": 10.0, "net_income": 1.0, "shares": 0.0},
            {"revenue": 10.0, "net_income": 1.0, "shares": 5.0}))


class TestDecomposeSelection(unittest.TestCase):
    def test_prefers_logs_when_legal(self):
        d = E.decompose({"revenue": 100.0, "net_income": 10.0, "shares": 50.0},
                        {"revenue": 120.0, "net_income": 15.0, "shares": 48.0})
        self.assertTrue(d["available"])
        self.assertEqual(d["method"], "log")
        self.assertTrue(E.reconciles(d))

    def test_falls_back_to_dollars_when_logs_are_invalid(self):
        d = E.decompose({"revenue": 100.0, "net_income": -10.0, "shares": 50.0},
                        {"revenue": 120.0, "net_income": 15.0, "shares": 48.0})
        self.assertEqual(d["method"], "dollar")
        self.assertEqual(d["label"], "Dollar EPS Bridge")
        self.assertTrue(E.reconciles(d))

    def test_warns_but_still_draws_when_reported_eps_differs(self):
        # Realty Income's reported EPS sits 5% below net income over diluted
        # shares (preferred dividends, minority interests). The bridge is
        # still useful; it must say which earnings figure it describes.
        d = E.decompose({"revenue": 100.0, "net_income": 10.0, "shares": 50.0},
                        {"revenue": 120.0, "net_income": 15.0, "shares": 48.0},
                        reported_eps_current=0.25)     # identity gives 0.3125
        self.assertTrue(d["available"])
        self.assertTrue(d["warning"])
        self.assertIn("0.25", d["warning"])
        self.assertFalse(d["identity"]["ok"])

    def test_no_warning_inside_tolerance(self):
        d = E.decompose({"revenue": 100.0, "net_income": 10.0, "shares": 50.0},
                        {"revenue": 120.0, "net_income": 15.0, "shares": 48.0},
                        reported_eps_current=0.3120)
        self.assertEqual(d["warning"], "")

    def test_unavailable_with_a_reason_when_inputs_are_missing(self):
        d = E.decompose({"revenue": None, "net_income": None, "shares": None},
                        {"revenue": 1.0, "net_income": 1.0, "shares": 1.0})
        self.assertFalse(d["available"])
        self.assertIn("missing", d["reason"])

    def test_reconciles_rejects_an_unavailable_result(self):
        self.assertFalse(E.reconciles({"available": False}))
        self.assertFalse(E.reconciles(None))


class TestNormalize(unittest.TestCase):
    def test_indexes_to_100(self):
        out = E.normalize([{"date": "2024-01-01", "value": 50.0},
                           {"date": "2024-06-01", "value": 75.0}])
        self.assertAlmostEqual(out[0]["indexed"], 100.0)
        self.assertAlmostEqual(out[1]["indexed"], 150.0)

    def test_refuses_a_non_positive_base(self):
        # Indexing to 100 off a loss produces a line that inverts every time
        # the sign flips. An empty series plus a note beats a wrong chart.
        self.assertEqual(E.normalize([{"date": "2024-01-01", "value": -3.0},
                                      {"date": "2024-06-01", "value": -1.0}]), [])

    def test_skips_missing_points(self):
        out = E.normalize([{"date": "a", "value": 10.0},
                           {"date": "b", "value": None},
                           {"date": "c", "value": 20.0}])
        self.assertEqual(len(out), 2)


class TestVerdict(unittest.TestCase):
    BASE = {"price": 100.0, "eps_ttm": 8.0, "eps_forward": 9.0,
            "revenue_growth_pct": 12.0, "fcf_yield_pct": 5.0,
            "treasury_10y_pct": 4.2, "estimate_change_30d_pct": 5.0}

    def test_attractive_when_yield_clears_the_cushion(self):
        v = E.verdict(self.BASE)
        self.assertEqual(v["verdict"], "ATTRACTIVE")
        # 9.00 / 100 = 9% against 4.2% -> 4.8 points of spread
        self.assertAlmostEqual(v["spread_pp"], 4.8, places=6)

    def test_wait_names_the_price_and_the_earnings_that_would_change_it(self):
        v = E.verdict({**self.BASE, "price": 300.0})
        self.assertEqual(v["verdict"], "WAIT")
        line = " ".join(v["what_would_change"])
        self.assertIn("Reconsider below", line)
        # price at target = 9.00 / 0.062
        self.assertIn("145.16", line)
        self.assertIn("18.60", line)          # eps at target = 300 * 0.062

    def test_watch_between_the_two_thresholds(self):
        # Yield above the Treasury but below Treasury + 2 points.
        v = E.verdict({**self.BASE, "price": 170.0})
        self.assertEqual(v["verdict"], "WATCH")

    def test_avoid_when_losing_money(self):
        v = E.verdict({**self.BASE, "eps_ttm": -1.2, "eps_forward": None})
        self.assertEqual(v["verdict"], "AVOID")
        self.assertIn("losing money", v["reasons"][0])
        self.assertTrue(v["what_would_change"])

    def test_avoid_when_shrinking_and_being_cut(self):
        v = E.verdict({**self.BASE, "revenue_growth_pct": -6.0,
                       "estimate_change_30d_pct": -40.0})
        self.assertEqual(v["verdict"], "AVOID")

    def test_shrinking_alone_downgrades_a_cheap_stock_to_watch(self):
        v = E.verdict({**self.BASE, "revenue_growth_pct": -3.0})
        self.assertEqual(v["verdict"], "WATCH")
        self.assertTrue(any("shrinking" in r for r in v["reasons"]))

    def test_insufficient_data_without_price_or_earnings(self):
        self.assertEqual(E.verdict({"price": None})["verdict"], "INSUFFICIENT DATA")
        self.assertEqual(
            E.verdict({"price": 10.0, "eps_ttm": None,
                       "eps_forward": None})["verdict"], "INSUFFICIENT DATA")

    def test_basis_is_named_and_never_mixed(self):
        fwd = E.verdict(self.BASE)
        self.assertIn("analyst forward earnings estimate",
                      " ".join(fwd["reasons"]))
        trailing = E.verdict({**self.BASE, "eps_forward": None})
        self.assertIn("GAAP trailing twelve month earnings",
                      " ".join(trailing["reasons"]))

    def test_says_so_when_it_falls_back_to_the_assumed_treasury_yield(self):
        v = E.verdict({**self.BASE, "treasury_10y_pct": None})
        self.assertIn("live 10-year Treasury yield was unavailable",
                      " ".join(v["reasons"]))

    def test_thresholds_come_from_configuration(self):
        strict = E.verdict(self.BASE, {"attractive_spread_pp": 6.0})
        self.assertEqual(strict["verdict"], "WATCH")
        loose = E.verdict({**self.BASE, "price": 300.0},
                          {"attractive_spread_pp": -2.0})
        self.assertEqual(loose["verdict"], "ATTRACTIVE")

    def test_every_verdict_is_one_of_the_five_words(self):
        for snap in ({}, {"price": 1.0}, self.BASE,
                     {**self.BASE, "eps_ttm": -1.0, "eps_forward": None},
                     {**self.BASE, "price": 900.0}):
            self.assertIn(E.verdict(snap)["verdict"], E.VERDICTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
