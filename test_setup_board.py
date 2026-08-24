"""Tests for setup_board.py — the daily "what is worth selling" list.

This board's failure mode is not a crash, it is a plausible-looking list.
A name that should have been skipped, ranked first, reads exactly like a
name that earned its place. So the tests are weighted toward the rejections
and toward the honesty of the ranking figure:

  - earnings inside the option's life must EXCLUDE, never bonus (this is
    the exact inversion of the Premium Edge scan, and getting the sign
    wrong would silently hand a hold-to-expiry seller the trades that hurt
    them most)
  - a merely fair premium is not a reason to take assignment risk
  - a negative expected value is never ranked, only skipped
  - a percentile quoted from six observations is not a percentile
  - every skipped name carries the reason it was skipped
"""

import unittest

import setup_board as SB


def board_row(sym, rvol_rank=50, last=100.0, mcap=5e10, avol=5e6,
              days_to_earnings=None):
    return {"ticker": sym, "rvol_rank": rvol_rank, "rvol": 24.0, "last": last,
            "market_cap": mcap, "avg_volume": avol,
            "days_to_earnings": days_to_earnings}


def scan_row(sym, vrp_ratio=1.30, vrp_percentile=80.0, hist_n=200,
             ev=0.35, liquidity_ok=True, earnings_inside=False,
             danger="LOW", roc=1.8, dte=35.0):
    return {"symbol": sym, "spot": 100.0, "data_ok": True,
            "vrp_ratio": vrp_ratio, "vrp_points": 6.0,
            "vrp_percentile": vrp_percentile, "hist_n": hist_n,
            "iv30": 0.28, "erv30": 0.22,
            "best_expiry": "2026-09-18", "best_dte": dte, "best_strike": 92.0,
            "best_delta": -0.18, "best_credit": 0.85, "best_roc_pct": roc,
            "best_ev": ev, "best_kind": "short_put",
            "liquidity_ok": liquidity_ok, "earnings_inside": earnings_inside,
            "danger": danger, "earnings_date": None, "premium_class": "rich"}


class TestEarningsIsAnExclusionNotABonus(unittest.TestCase):
    """The single most important sign in this module.

    The Premium Edge scan adds 30 points for earnings inside a week,
    because a trader who closes before the print harvests that premium. A
    seller who HOLDS TO EXPIRY through the report underwrites it instead.
    Same data, opposite sign. Getting this backwards would rank the most
    dangerous names first while looking entirely reasonable.
    """

    def test_earnings_inside_the_horizon_is_dropped(self):
        out = SB.stage1([board_row("AAA", days_to_earnings=10)], horizon_days=45)
        self.assertEqual(out["candidates"], [])
        self.assertIn("earnings inside the option's life", out["dropped"])

    def test_earnings_beyond_the_horizon_survives(self):
        out = SB.stage1([board_row("AAA", days_to_earnings=90)], horizon_days=45)
        self.assertEqual([c["symbol"] for c in out["candidates"]], ["AAA"])

    def test_earnings_just_past_expiry_is_still_treated_as_inside(self):
        """Reported dates drift, and unconfirmed ones drift more."""
        out = SB.stage1([board_row("AAA", days_to_earnings=46)], horizon_days=45)
        self.assertEqual(out["candidates"], [])

    def test_no_known_earnings_date_does_not_exclude(self):
        out = SB.stage1([board_row("AAA", days_to_earnings=None)], horizon_days=45)
        self.assertEqual(len(out["candidates"]), 1)

    def test_a_high_volatility_name_with_earnings_never_outranks_a_clean_one(self):
        rows = [board_row("HOT", rvol_rank=99, days_to_earnings=5),
                board_row("CLEAN", rvol_rank=40, days_to_earnings=None)]
        out = SB.stage1(rows, horizon_days=45)
        self.assertEqual([c["symbol"] for c in out["candidates"]], ["CLEAN"])

    def test_the_gate_repeats_the_exclusion_on_real_chain_data(self):
        g = SB.gate(scan_row("AAA", earnings_inside=True))
        self.assertFalse(g["ok"])
        self.assertTrue(any("Earnings" in r for r in g["reasons"]))


class TestStage1Screens(unittest.TestCase):
    def test_cheap_stocks_are_dropped(self):
        out = SB.stage1([board_row("AAA", last=5.0)])
        self.assertEqual(out["candidates"], [])

    def test_small_companies_are_dropped(self):
        out = SB.stage1([board_row("AAA", mcap=1e8)])
        self.assertEqual(out["candidates"], [])

    def test_thin_volume_is_dropped(self):
        out = SB.stage1([board_row("AAA", avol=1000)])
        self.assertEqual(out["candidates"], [])

    def test_a_missing_volatility_history_is_dropped_not_defaulted(self):
        """Scoring a name with no history as zero would bury it silently;
        scoring it as average would promote a name nothing is known about."""
        out = SB.stage1([board_row("AAA", rvol_rank=None)])
        self.assertEqual(out["candidates"], [])
        self.assertIn("no volatility history yet", out["dropped"])

    def test_it_ranks_by_volatility_rank(self):
        rows = [board_row("LOW", rvol_rank=10), board_row("HIGH", rvol_rank=95),
                board_row("MID", rvol_rank=50)]
        out = SB.stage1(rows)
        self.assertEqual([c["symbol"] for c in out["candidates"]],
                         ["HIGH", "MID", "LOW"])

    def test_the_limit_caps_how_many_earn_a_chain_fetch(self):
        rows = [board_row(f"S{i}", rvol_rank=i) for i in range(60)]
        out = SB.stage1(rows, limit=5)
        self.assertEqual(len(out["candidates"]), 5)
        self.assertEqual(out["ranked"], 60)

    def test_it_reports_how_many_it_looked_at_versus_measured(self):
        """The board ranks everything and measures a few. Saying so is the
        difference between a screen and a claim to have checked the lot."""
        rows = [board_row(f"S{i}", rvol_rank=i) for i in range(60)]
        out = SB.stage1(rows, limit=5)
        self.assertEqual(out["considered"], 60)
        self.assertEqual(out["limit"], 5)
        self.assertIn("proxy", out["basis"])


class TestTheGates(unittest.TestCase):
    def test_a_clean_row_passes(self):
        self.assertTrue(SB.gate(scan_row("AAA"))["ok"])

    def test_a_merely_fair_premium_is_refused(self):
        g = SB.gate(scan_row("AAA", vrp_ratio=1.00))
        self.assertFalse(g["ok"])
        self.assertTrue(any("Fair pay" in r for r in g["reasons"]))

    def test_a_negative_expected_value_is_refused(self):
        g = SB.gate(scan_row("AAA", ev=-0.10))
        self.assertFalse(g["ok"])
        self.assertTrue(any("loses money" in r for r in g["reasons"]))

    def test_bad_liquidity_is_refused(self):
        g = SB.gate(scan_row("AAA", liquidity_ok=False))
        self.assertFalse(g["ok"])
        self.assertTrue(any("liquidity" in r for r in g["reasons"]))

    def test_a_dangerous_name_is_refused(self):
        self.assertFalse(SB.gate(scan_row("AAA", danger="HIGH"))["ok"])

    def test_missing_data_is_refused_rather_than_assumed_fine(self):
        r = scan_row("AAA"); r["data_ok"] = False
        self.assertFalse(SB.gate(r)["ok"])

    def test_every_refusal_states_a_reason(self):
        for kw in ({"vrp_ratio": 1.0}, {"ev": -1.0}, {"liquidity_ok": False},
                   {"danger": "EXTREME"}, {"earnings_inside": True}):
            g = SB.gate(scan_row("AAA", **kw))
            self.assertFalse(g["ok"], kw)
            self.assertTrue(g["reasons"] and all(r.strip() for r in g["reasons"]), kw)


class TestRichnessIsHonestAboutItsBasis(unittest.TestCase):
    def test_a_deep_history_uses_the_percentile(self):
        r = SB.richness(scan_row("AAA", vrp_percentile=92.0, hist_n=400))
        self.assertEqual(r["basis"], "percentile")
        self.assertEqual(r["value"], 92.0)
        self.assertIn("400", r["why"])

    def test_a_thin_history_falls_back_to_the_ratio_and_says_so(self):
        """A percentile from six readings is a coincidence with a decimal
        point, and quoting it would be the most flattering possible lie."""
        r = SB.richness(scan_row("AAA", vrp_percentile=99.0, hist_n=6))
        self.assertEqual(r["basis"], "ratio")
        self.assertNotEqual(r["value"], 99.0)
        self.assertIn("too few", r["why"])

    def test_no_reading_at_all_is_none_not_zero(self):
        r = SB.richness(scan_row("AAA", vrp_ratio=None, vrp_percentile=None,
                                 hist_n=0))
        self.assertIsNone(r["value"])
        self.assertEqual(r["basis"], "none")

    def test_the_ratio_fallback_is_monotone_in_the_ratio(self):
        a = SB.richness(scan_row("A", vrp_ratio=1.10, hist_n=0, vrp_percentile=None))
        b = SB.richness(scan_row("B", vrp_ratio=1.40, hist_n=0, vrp_percentile=None))
        self.assertLess(a["value"], b["value"])

    def test_the_two_bases_are_distinguishable_in_the_payload(self):
        deep = SB.richness(scan_row("A", hist_n=400))
        thin = SB.richness(scan_row("B", hist_n=3))
        self.assertNotEqual(deep["basis"], thin["basis"])


class TestTheBoard(unittest.TestCase):
    def test_it_ranks_the_richest_first(self):
        rows = [scan_row("MID", vrp_percentile=50.0),
                scan_row("TOP", vrp_percentile=95.0),
                scan_row("LOW", vrp_percentile=10.0)]
        out = SB.build(rows)
        self.assertEqual([r["symbol"] for r in out["rows"]],
                         ["TOP", "MID", "LOW"])

    def test_ties_break_on_return_for_the_same_money(self):
        rows = [scan_row("THIN", vrp_percentile=80.0, roc=0.5),
                scan_row("FAT", vrp_percentile=80.0, roc=3.0)]
        out = SB.build(rows)
        self.assertEqual([r["symbol"] for r in out["rows"]], ["FAT", "THIN"])

    def test_a_skipped_name_is_listed_with_its_reason_not_dropped(self):
        out = SB.build([scan_row("BAD", ev=-0.5)])
        self.assertEqual(out["rows"], [])
        self.assertEqual(len(out["skipped"]), 1)
        self.assertEqual(out["skipped"][0]["symbol"], "BAD")
        self.assertTrue(out["skipped"][0]["why"])

    def test_the_count_that_qualified_is_reported_separately_from_the_count_shown(self):
        rows = [scan_row(f"S{i}", vrp_percentile=float(i)) for i in range(20)]
        out = SB.build(rows, limit=5)
        self.assertEqual(out["shown"], 5)
        self.assertEqual(out["qualified"], 20)

    def test_an_empty_board_is_a_valid_answer(self):
        out = SB.build([scan_row("A", ev=-1.0), scan_row("B", vrp_ratio=1.0)])
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["qualified"], 0)
        self.assertEqual(len(out["skipped"]), 2)

    def test_nothing_at_all_does_not_crash(self):
        out = SB.build([])
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["qualified"], 0)

    def test_the_ranking_basis_is_stated_in_the_payload(self):
        out = SB.build([scan_row("A")])
        self.assertIn("realizes", out["basis"])

    def test_a_row_carries_the_trade_not_just_the_score(self):
        out = SB.build([scan_row("A")])
        row = out["rows"][0]
        for k in ("expiration", "strike", "delta", "credit", "roc_pct", "dte"):
            self.assertIsNotNone(row.get(k), k)


class TestTheBoardDoesNotOverclaim(unittest.TestCase):
    """The measured-distance work established there is no gap between the
    evidence-supported distance and the 15-22 delta band at an 85% target.
    This module must not quietly re-open that claim by "improving" a strike.

    Asserted behaviourally rather than by grepping the docstring: prose
    guards pass as long as the comment survives, which is not the same
    thing as the code still behaving that way.
    """

    def test_it_never_alters_the_trade_it_was_handed(self):
        src = scan_row("A")
        out = SB.build([src])
        row = out["rows"][0]
        self.assertEqual(row["strike"], src["best_strike"])
        self.assertEqual(row["delta"], src["best_delta"])
        self.assertEqual(row["credit"], src["best_credit"])
        self.assertEqual(row["expiration"], src["best_expiry"])
        self.assertEqual(row["dte"], src["best_dte"])

    def test_it_never_alters_the_trade_on_a_skipped_name_either(self):
        src = scan_row("A", ev=-1.0)
        out = SB.build([src])
        self.assertEqual(out["skipped"][0]["strike"], src["best_strike"])
        self.assertEqual(out["skipped"][0]["delta"], src["best_delta"])

    def test_reordering_the_input_cannot_change_any_trade(self):
        """Ranking is the only thing this module is allowed to do."""
        rows = [scan_row("A", vrp_percentile=10.0),
                scan_row("B", vrp_percentile=90.0)]
        fwd = SB.build(list(rows))
        rev = SB.build(list(reversed(rows)))
        key = lambda o: sorted((r["symbol"], r["strike"], r["delta"],
                                r["credit"]) for r in o["rows"])
        self.assertEqual(key(fwd), key(rev))

    def test_the_stage1_basis_admits_it_is_a_proxy(self):
        out = SB.stage1([board_row("A")])
        self.assertIn("proxy", out["basis"])
        self.assertIn("top names only", " ".join(out["basis"].split()))


if __name__ == "__main__":
    unittest.main()
