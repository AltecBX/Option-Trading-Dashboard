"""Tests for peers.py — who a company is comparable to, and what the group
is worth.

The two claims this file defends are the ones the tab makes on screen:

  1. A group is never valued by AVERAGING its members' price/earnings ratios.
     One member earning almost nothing produces a ratio in the hundreds and
     drags the average somewhere no member is. The aggregate is total market
     value over total earnings, which is what an index multiple means.
  2. A group below five members is not a distribution. It falls back a level
     and says so, rather than ranking a company against three others as
     though that were a percentile.

Nothing here touches the network: the industry index and the member metrics
are both injected.
"""

import json
import os
import shutil
import tempfile
import time
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import peers as P


class PeersBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="peers-test-")
        self.universe = []
        P.configure(universe_fn=lambda: self.universe,
                    quote_fn=lambda s: {"price": 100.0},
                    data_dir=self.dir)
        P._BUILDS.clear()
        P._INFLIGHT.clear()

    def tearDown(self):
        P._BUILDS.clear()
        P._INFLIGHT.clear()
        P._INDEX = None
        shutil.rmtree(self.dir, ignore_errors=True)

    def seed_index(self, mapping):
        """Put SIC codes in the index without going near EDGAR."""
        idx = P._load_index()
        now = time.time()
        for sym, sic in mapping.items():
            idx[sym] = {"sic": sic, "ts": now}
        P._save_index()


class TestGroupValuation(PeersBase):
    ROWS = [
        {"symbol": "A", "market_cap": 100e9, "eps_ttm": 5.0,
         "net_income_ttm": 5e9, "price": 50.0, "trailing_pe": 10.0},
        {"symbol": "B", "market_cap": 200e9, "eps_ttm": 4.0,
         "net_income_ttm": 8e9, "price": 100.0, "trailing_pe": 25.0},
        {"symbol": "C", "market_cap": 50e9, "eps_ttm": -1.0,
         "net_income_ttm": -2e9, "price": 20.0, "trailing_pe": None},
        {"symbol": "D", "market_cap": 10e9, "eps_ttm": 0.01,
         "net_income_ttm": 1e6, "price": 30.0, "trailing_pe": 3000.0},
        {"symbol": "E", "market_cap": 300e9, "eps_ttm": 10.0,
         "net_income_ttm": 20e9, "price": 200.0, "trailing_pe": 20.0},
    ]

    def test_aggregate_is_total_value_over_total_earnings(self):
        v = P.group_valuation(self.ROWS)
        # (100 + 200 + 10 + 300)bn of value over (5 + 8 + 0.001 + 20)bn earned
        self.assertAlmostEqual(v["aggregate_pe"], 610e9 / 33.001e9, places=4)
        self.assertAlmostEqual(v["aggregate_earnings_yield_pct"],
                               100.0 / v["aggregate_pe"], places=6)

    def test_the_aggregate_survives_a_near_zero_earner(self):
        # Member D earns a cent a share. An arithmetic average of the members'
        # ratios comes to about 764x, which describes nobody.
        v = P.group_valuation(self.ROWS)
        naive = sum(r["trailing_pe"] for r in self.ROWS if r["trailing_pe"]) / 4
        self.assertGreater(naive, 700)
        self.assertLess(v["aggregate_pe"], 25)

    def test_median_member_is_shown_beside_the_aggregate(self):
        v = P.group_valuation(self.ROWS)
        self.assertAlmostEqual(v["median_member_pe"], 22.5)

    def test_loss_makers_are_excluded_and_named(self):
        v = P.group_valuation(self.ROWS)
        self.assertEqual(v["n"], 5)
        self.assertEqual(v["n_profitable"], 4)
        self.assertEqual(v["n_excluded"], 1)
        self.assertEqual(v["excluded"], ["C"])

    def test_a_group_of_only_loss_makers_has_no_multiple(self):
        v = P.group_valuation([{"symbol": "X", "market_cap": 1e9,
                                "eps_ttm": -1.0, "net_income_ttm": -1e8,
                                "price": 10.0, "trailing_pe": None}])
        self.assertFalse(v["available"])
        self.assertIsNone(v["aggregate_pe"])
        self.assertIn("Every member of the group is loss-making", v["reason"])

    def test_an_empty_group_says_so(self):
        v = P.group_valuation([])
        self.assertFalse(v["available"])
        self.assertEqual(v["n"], 0)

    def test_rows_without_a_market_value_are_skipped(self):
        v = P.group_valuation(self.ROWS + [{"symbol": "Z", "market_cap": None,
                                            "eps_ttm": 1.0}])
        self.assertEqual(v["n"], 5)


class TestPeerSelection(PeersBase):
    def test_same_four_digit_code_is_direct_peers(self):
        self.universe = ["ME", "P1", "P2", "P3", "P4", "P5", "OTHER"]
        self.seed_index({"ME": "3674", "P1": "3674", "P2": "3674",
                         "P3": "3674", "P4": "3674", "P5": "3674",
                         "OTHER": "2080"})
        g = P.peer_group("ME")
        self.assertEqual(g["level"], "DIRECT PEERS")
        self.assertNotIn("ME", g["peers"])
        self.assertNotIn("OTHER", g["peers"])
        self.assertEqual(len(g["peers"]), 5)

    def test_falls_back_to_the_industry_group_and_says_so(self):
        # Only two companies share the full code; five share the first three.
        self.universe = ["ME", "P1", "Q1", "Q2", "Q3", "Q4"]
        self.seed_index({"ME": "3674", "P1": "3674", "Q1": "3672",
                         "Q2": "3673", "Q3": "3679", "Q4": "3671"})
        g = P.peer_group("ME")
        self.assertEqual(g["level"], "INDUSTRY")
        self.assertIn("widened", g["reason"])

    def test_falls_back_to_the_sector(self):
        self.universe = ["ME", "S1", "S2", "S3", "S4", "S5"]
        # Same two-digit major group (36xx, electronic equipment), different
        # three-digit groups — so the industry level cannot fill and the
        # sector level can.
        self.seed_index({"ME": "3674", "S1": "3612", "S2": "3630",
                         "S3": "3651", "S4": "3661", "S5": "3690"})
        g = P.peer_group("ME")
        self.assertEqual(g["level"], "SECTOR")

    def test_a_broad_benchmark_is_labelled_as_context_only(self):
        self.universe = ["ME", "X1", "X2", "X3", "X4", "X5"]
        self.seed_index({"ME": "3674", "X1": "6021", "X2": "6798",
                         "X3": "2080", "X4": "7372", "X5": "1040"})
        g = P.peer_group("ME")
        self.assertEqual(g["level"], "BROAD BENCHMARK")
        self.assertIn("not as a like-for-like comparison", g["reason"])

    def test_too_few_indexed_companies_gives_no_group(self):
        self.universe = ["ME", "X1"]
        self.seed_index({"ME": "3674", "X1": "6021"})
        g = P.peer_group("ME")
        self.assertIsNone(g["level"])
        self.assertEqual(g["peers"], [])
        self.assertIn("too few to build a peer group", g["reason"])

    def test_no_industry_code_means_no_group(self):
        self.universe = ["ME"]
        self.seed_index({"ME": None})
        g = P.peer_group("ME")
        self.assertIsNone(g["level"])
        self.assertIn("No industry classification", g["reason"])

    def test_a_curated_list_overrides_the_industry_code(self):
        self.universe = ["ME", "P1", "P2", "P3", "P4", "P5"]
        self.seed_index({s: "3674" for s in self.universe})
        with open(os.path.join(self.dir, "invest", "peers_curated.json"), "w") as fh:
            json.dump({"ME": ["AAA", "BBB", "CCC", "DDD", "EEE"]}, fh)
        g = P.peer_group("ME")
        self.assertEqual(g["level"], "DIRECT PEERS")
        self.assertTrue(g["curated"])
        self.assertEqual(g["peers"], ["AAA", "BBB", "CCC", "DDD", "EEE"])
        self.assertIn("hand-written list is allowed to win", g["reason"])

    def test_a_curated_list_below_the_minimum_does_not_override(self):
        self.universe = ["ME", "P1", "P2", "P3", "P4", "P5"]
        self.seed_index({s: "3674" for s in self.universe})
        with open(os.path.join(self.dir, "invest", "peers_curated.json"), "w") as fh:
            json.dump({"ME": ["AAA", "BBB"]}, fh)
        g = P.peer_group("ME")
        self.assertFalse(g["curated"])
        self.assertEqual(g["level"], "DIRECT PEERS")
        self.assertIn("P1", g["peers"])

    def test_the_built_in_seed_list_is_used_when_nothing_is_indexed(self):
        self.universe = []
        g = P.peer_group("AAPL")
        self.assertTrue(g["curated"])
        self.assertIn("MSFT", g["peers"])

    def test_group_size_is_capped(self):
        self.universe = ["ME"] + [f"P{i}" for i in range(60)]
        self.seed_index({s: "3674" for s in self.universe})
        g = P.peer_group("ME")
        self.assertLessEqual(len(g["peers"]), P.MAX_GROUP)


class TestBuild(PeersBase):
    def setUp(self):
        super().setUp()
        self.metrics = {}
        self._real = P.member_metrics
        P.member_metrics = lambda s: self.metrics.get(s)

    def tearDown(self):
        P.member_metrics = self._real
        super().tearDown()

    def _row(self, sym, cap, eps, ni, ey, growth=5.0):
        return {"symbol": sym, "name": sym, "price": 100.0, "market_cap": cap,
                "eps_ttm": eps, "net_income_ttm": ni, "revenue_ttm": 1e9,
                "earnings_yield_pct": ey, "trailing_pe": 100.0 / eps if eps > 0 else None,
                "fcf_yield_pct": ey, "net_margin_pct": 10.0,
                "operating_margin_pct": 12.0, "revenue_growth_pct": growth,
                "eps_growth_pct": growth, "period_end": "2026-06-30"}

    def test_a_full_group_produces_a_valuation_and_ranks_the_subject(self):
        self.universe = ["ME", "P1", "P2", "P3", "P4", "P5"]
        self.seed_index({s: "3674" for s in self.universe})
        for i, s in enumerate(self.universe):
            self.metrics[s] = self._row(s, 10e9, 5.0, 5e8, 3.0 + i)
        out = P.build("ME")
        self.assertEqual(out["level"], "DIRECT PEERS")
        self.assertEqual(out["n_resolved"], 5)
        self.assertTrue(out["valuation"]["available"])
        self.assertIn("earnings_yield_pct", out["ranks"])
        self.assertEqual(out["ranks"]["earnings_yield_pct"]["n"], 5)

    def test_unpriceable_members_drop_the_group_below_the_minimum(self):
        self.universe = ["ME", "P1", "P2", "P3", "P4", "P5"]
        self.seed_index({s: "3674" for s in self.universe})
        self.metrics["ME"] = self._row("ME", 10e9, 5.0, 5e8, 5.0)
        self.metrics["P1"] = self._row("P1", 10e9, 5.0, 5e8, 5.0)
        out = P.build("ME")
        self.assertTrue(out.get("under_minimum"))
        self.assertFalse(out["valuation"].get("available"))
        self.assertIn("under the 5", out["reason"])
        self.assertEqual(out["ranks"], {})

    def test_no_group_returns_empty_rather_than_guessing(self):
        self.universe = ["ME"]
        self.seed_index({"ME": "3674"})
        out = P.build("ME")
        self.assertEqual(out["rows"], [])
        self.assertFalse(out["valuation"]["available"])


class TestIndex(PeersBase):
    def test_index_survives_a_round_trip(self):
        self.universe = ["AA", "BB"]
        self.seed_index({"AA": "1234", "BB": "5678"})
        P._INDEX = None
        idx = P._load_index()
        self.assertEqual(idx["AA"]["sic"], "1234")

    def test_status_counts_what_is_known(self):
        self.universe = ["AA", "BB", "CC"]
        self.seed_index({"AA": "1234", "BB": None})
        st = P.index_status()
        self.assertEqual(st["indexed"], 2)
        self.assertEqual(st["with_sic"], 1)
        self.assertEqual(st["universe"], 3)
        self.assertEqual(st["missing"], 1)

    def test_values_for_pulls_one_field(self):
        payload = {"rows": [{"earnings_yield_pct": 5.0},
                            {"earnings_yield_pct": None},
                            {"earnings_yield_pct": 7.0}]}
        self.assertEqual(P.values_for(payload, "earnings_yield_pct"), [5.0, 7.0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
