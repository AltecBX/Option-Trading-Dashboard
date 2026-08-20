"""Tests for forward_test — reading the snapshot store forward.

The three rules this engine exists to enforce are asserted here rather than
trusted, and the lookahead ones are asserted the hard way:

  1. NO LOOKAHEAD. An outcome computed against the full price series must be
     IDENTICAL to one computed against a series truncated at the horizon,
     and identical again with everything before the recommendation removed.
     If anything inside reached past the horizon, those three disagree.
  2. NO INCOMPLETE HORIZONS. A horizon that has not passed in calendar time,
     or that the price series does not reach, produces nothing at all.
  3. NO SUBSTITUTION. The contract scored is the contract that was
     recommended — that strike, that expiry, that credit.

And the one thing it refuses to do: draw a conclusion from a small sample.
"""

import os
import json
import unittest
from datetime import date, timedelta

os.environ.setdefault("JERRY_NO_NET", "1")

import forward_test as FT
import invest_scan as S


def series(prices, start=date(2025, 1, 1)):
    out, d = [], start
    for p in prices:
        out.append({"date": d.isoformat(), "close": float(p),
                    "high": float(p) * 1.02, "low": float(p) * 0.98})
        d += timedelta(days=1)
    return out


FLAT = FT.index_bars(series([100.0] * 500))
UP = FT.index_bars(series([100.0 + i * 0.2 for i in range(500)]))
TODAY = "2026-12-31"


def row(day="2025-01-01", **kw):
    base = {"date": day, "ticker": "TEST", "price": 100.0,
            "entry_verdict": "BUY SHARES", "verdict": "ATTRACTIVE",
            "preferred_structure": "SHARES",
            "fair_value_bear": 80.0, "fair_value_base": 110.0,
            "fair_value_bull": 140.0, "buy_zone": 88.0,
            "quality_label": "STRONG", "growth_label": "STEADY",
            "valuation_label": "CHEAP", "valuation_self_percentile": 70.0,
            "revisions_label": "RISING", "value_trap_level": "LOW",
            "fair_value_confidence": "HIGH", "business_type": "STANDARD",
            "sic": "3571", "config_hash": "cfg1",
            # Benchmark-relative scoring needs the benchmark's close on the
            # day, recorded on the day. Rows without it are still scored on
            # their own return; these fixtures exercise the comparison.
            "benchmark_symbol": "SPY", "benchmark_close": 500.0}
    base.update(kw)
    return base


class TestNoLookahead(unittest.TestCase):
    def test_the_audit_passes_on_a_clean_outcome(self):
        a = FT.lookahead_audit(row(), UP, 90, TODAY)
        self.assertTrue(a["ok"], a)
        self.assertEqual(a["differences"], {})
        self.assertIn("Identical", a["reason"])

    def test_the_audit_passes_across_every_horizon(self):
        for h in FT.HORIZONS:
            a = FT.lookahead_audit(row(), UP, h, TODAY, FLAT)
            self.assertTrue(a["ok"], f"{h}: {a}")

    def test_prices_after_the_horizon_cannot_change_the_outcome(self):
        spike = series([100.0 + i * 0.2 for i in range(120)]
                       + [10_000.0] * 380)
        full = FT.outcome(row(), FT.index_bars(spike), 90, TODAY)
        cut = FT.outcome(row(), FT.index_bars(spike[:120]), 90, TODAY)
        self.assertEqual(full["return_pct"], cut["return_pct"])
        self.assertEqual(full["max_favorable_excursion_pct"],
                         cut["max_favorable_excursion_pct"])

    def test_prices_before_the_recommendation_cannot_change_it(self):
        early = series([500.0] * 30 + [100.0 + i * 0.2 for i in range(470)])
        recommended_on = early[30]["date"]
        with_history = FT.outcome(row(day=recommended_on),
                                  FT.index_bars(early), 90, TODAY)
        without = FT.outcome(row(day=recommended_on),
                             FT.index_bars(early[30:]), 90, TODAY)
        self.assertEqual(with_history["return_pct"], without["return_pct"])
        self.assertEqual(with_history["max_adverse_excursion_pct"],
                         without["max_adverse_excursion_pct"])

    def test_the_entry_price_is_the_one_that_was_recorded(self):
        # Not the close on that day — the price the recommendation was
        # actually written against.
        o = FT.outcome(row(price=50.0), UP, 90, TODAY)
        self.assertEqual(o["price"], 50.0)
        self.assertAlmostEqual(o["return_pct"],
                               (o["end_price"] / 50.0 - 1.0) * 100.0)


class TestIncompleteHorizons(unittest.TestCase):
    def test_a_horizon_that_has_not_passed_produces_nothing(self):
        self.assertIsNone(FT.outcome(row(day="2026-12-01"), UP, 90,
                                     "2026-12-31"))

    def test_a_price_series_that_stops_short_produces_nothing(self):
        short = FT.index_bars(series([100.0] * 30))
        self.assertIsNone(FT.outcome(row(), short, 90, TODAY))

    def test_the_boundary_day_itself_completes(self):
        s = FT.index_bars(series([100.0] * 95))
        self.assertIsNotNone(FT.outcome(row(), s, 90, TODAY))
        self.assertIsNone(FT.outcome(row(), s, 180, TODAY))

    def test_a_row_with_no_recorded_price_produces_nothing(self):
        self.assertIsNone(FT.outcome(row(price=None), UP, 90, TODAY))

    def test_the_observation_builder_counts_what_it_skipped(self):
        out = FT.observations({"TEST": [row(day="2026-12-01")]},
                              {"TEST": series([100.0] * 500)}, "2026-12-31")
        self.assertEqual(out["n"], 0)
        self.assertEqual(out["skipped"]["incomplete_horizon"], 4)


class TestOutcomeMath(unittest.TestCase):
    def setUp(self):
        self.o = FT.outcome(row(), UP, 90, TODAY, FLAT)

    def test_excursions_bracket_the_return(self):
        self.assertLessEqual(self.o["max_adverse_excursion_pct"],
                             self.o["return_pct"])
        self.assertGreaterEqual(self.o["max_favorable_excursion_pct"],
                                self.o["return_pct"])

    def test_the_benchmark_is_measured_over_the_same_days(self):
        self.assertAlmostEqual(self.o["benchmark_return_pct"], 0.0, places=9)
        self.assertAlmostEqual(self.o["excess_return_pct"],
                               self.o["return_pct"], places=9)

    def test_range_containment_is_checked_against_the_published_range(self):
        inside = FT.outcome(row(fair_value_bear=0.0, fair_value_bull=1e6),
                            UP, 90, TODAY)
        outside = FT.outcome(row(fair_value_bear=0.0, fair_value_bull=1.0),
                             UP, 90, TODAY)
        self.assertTrue(inside["range_contained_outcome"])
        self.assertFalse(outside["range_contained_outcome"])

    def test_distance_from_base_is_relative_to_the_published_base(self):
        o = FT.outcome(row(fair_value_base=100.0), UP, 90, TODAY)
        self.assertAlmostEqual(o["distance_from_base_pct"],
                               (o["end_price"] / 100.0 - 1.0) * 100.0)

    def test_a_missing_range_leaves_containment_unanswered(self):
        o = FT.outcome(row(fair_value_bear=None, fair_value_bull=None),
                       UP, 90, TODAY)
        self.assertIsNone(o["range_contained_outcome"])


class TestSectorRelative(unittest.TestCase):
    def _build(self, n, sic="3571"):
        hist, bars = {}, {}
        for i in range(n):
            sym = f"S{i}"
            hist[sym] = [row(ticker=sym, sic=sic)]
            hist[sym][0]["ticker"] = sym
            bars[sym] = series([100.0 + i * 0.05 * j for j in range(500)])
        return hist, bars

    def test_too_few_of_an_industry_leaves_it_unavailable(self):
        hist, bars = self._build(3)
        out = FT.observations(hist, bars, TODAY)
        for r in out["rows"]:
            self.assertIsNone(r["sector_relative_return_pct"])
            self.assertIn("fewer than the 5", r["sector_note"])

    def test_a_company_is_left_out_of_the_median_it_is_measured_against(self):
        hist, bars = self._build(7)
        out = FT.observations(hist, bars, TODAY)
        rows = [r for r in out["rows"] if r["horizon"] == 90]
        self.assertEqual(len(rows), 7)
        for r in rows:
            self.assertIsNotNone(r["sector_relative_return_pct"])
            self.assertEqual(r["sector_n"], 6)
            self.assertIn("other companies", r["sector_note"])
        # With the subject excluded, the relative figures cannot all be zero.
        self.assertTrue(any(abs(r["sector_relative_return_pct"]) > 1e-9
                            for r in rows))


class TestCalibration(unittest.TestCase):
    def _obs(self, n, verdict="BUY SHARES", **kw):
        hist, bars = {}, {}
        for i in range(n):
            sym = f"T{i}"
            r = row(ticker=sym, entry_verdict=verdict, **kw)
            r["ticker"] = sym
            hist[sym] = [r]
            bars[sym] = series([100.0 + j * 0.1 for j in range(500)])
        return FT.observations(hist, bars, TODAY)

    def test_an_empty_store_says_what_it_is_waiting_for(self):
        cal = FT.calibration({"rows": []})
        self.assertFalse(cal["available"])
        self.assertIn("aged far enough", cal["reason"])
        self.assertIn("never back-fills", cal["reason"])

    def test_a_small_sample_refuses_to_report_a_median(self):
        cal = FT.calibration(self._obs(5))
        block = cal["horizons"]["90"]["overall"]
        self.assertFalse(block["sufficient"])
        self.assertEqual(block["verdict"], FT.INSUFFICIENT)
        self.assertNotIn("median_return_pct", block)
        self.assertIn("Nothing is concluded", block["reason"])

    def test_a_large_enough_sample_reports(self):
        cal = FT.calibration(self._obs(40))
        block = cal["horizons"]["90"]["overall"]
        self.assertTrue(block["sufficient"])
        self.assertIsNotNone(block["median_return_pct"])
        self.assertEqual(block["n"], 40)

    def test_there_is_no_accuracy_score_anywhere(self):
        cal = FT.calibration(self._obs(40))
        text = repr(cal).lower()
        for banned in ("accuracy_score", "investment_score", "overall_score"):
            self.assertNotIn(banned, text)

    def test_every_horizon_gets_its_own_block(self):
        cal = FT.calibration(self._obs(40))
        self.assertEqual(sorted(cal["horizons"].keys()),
                         ["180", "30", "365", "90"])

    def test_a_contest_needs_both_sides_to_be_large_enough(self):
        rows = self._obs(40)["rows"] + self._obs(2, verdict="WAIT")["rows"]
        cal = FT.calibration({"rows": rows})
        c = cal["horizons"]["90"]["attractive_versus_wait"]
        self.assertEqual(c["verdict"], FT.INSUFFICIENT)
        self.assertIn("Both sides need", c["reason"])

    def test_a_contest_with_both_sides_reports_the_gap(self):
        rows = self._obs(40)["rows"] + self._obs(40, verdict="WAIT")["rows"]
        cal = FT.calibration({"rows": rows})
        c = cal["horizons"]["90"]["attractive_versus_wait"]
        self.assertIn(c["verdict"], ("AHEAD", "BEHIND", "LEVEL"))
        self.assertIn("percentage points", c["reason"])
        self.assertIn("description of that stretch", c["reason"])

    def test_valuation_percentiles_are_bucketed_in_plain_english(self):
        self.assertEqual(FT._percentile_bucket(95),
                         "Cheapest fifth of its own history")
        self.assertEqual(FT._percentile_bucket(5),
                         "Dearest fifth of its own history")
        self.assertIsNone(FT._percentile_bucket(None))


class TestConfigSeparation(unittest.TestCase):
    def _rows(self, n, cfg):
        hist, bars = {}, {}
        for i in range(n):
            sym = f"C{cfg}{i}"
            r = row(ticker=sym, config_hash=cfg)
            r["ticker"] = sym
            hist[sym] = [r]
            bars[sym] = series([100.0 + j * 0.1 for j in range(500)])
        return FT.observations(hist, bars, TODAY)["rows"]

    def test_one_configuration_says_so(self):
        cal = FT.calibration({"rows": self._rows(40, "cfgA")})
        self.assertIn("one set of rules", cal["config_note"])
        self.assertNotIn("by_config", cal["horizons"]["90"])

    def test_several_configurations_are_broken_out_separately(self):
        rows = self._rows(40, "cfgA") + self._rows(40, "cfgB")
        cal = FT.calibration({"rows": rows})
        self.assertEqual(len(cal["config_hashes"]), 2)
        self.assertIn("mix rule sets", cal["config_note"])
        by_cfg = cal["horizons"]["90"]["by_config"]
        self.assertEqual(sorted(by_cfg.keys()), ["cfgA", "cfgB"])

    def test_separation_can_be_turned_off_deliberately(self):
        rows = self._rows(40, "cfgA") + self._rows(40, "cfgB")
        cal = FT.calibration({"rows": rows}, separate_configs=False)
        self.assertNotIn("by_config", cal["horizons"]["90"])


class TestExactContracts(unittest.TestCase):
    def _row(self, **kw):
        base = row(
            comparison_expiration="2025-07-01",
            csp_strike=90.0, csp_credit=2.5, csp_expiration="2025-04-01",
            leaps_strike=80.0, leaps_debit=28.0,
            leaps_expiration="2025-07-01",
            buy_write_call_strike=110.0, buy_write_credit=3.0)
        base.update(kw)
        return base

    def test_the_recommended_contract_is_the_one_scored(self):
        out = FT.structure_outcome(self._row(), UP, TODAY)
        put = out["results"]["PORTFOLIO SECURED PUT"]
        self.assertEqual(put["strike"], 90.0)
        self.assertEqual(put["credit"], 2.5)
        self.assertEqual(put["expiration"], "2025-04-01")

    def test_a_put_that_expires_out_of_the_money_keeps_the_credit(self):
        out = FT.structure_outcome(self._row(), UP, TODAY)
        put = out["results"]["PORTFOLIO SECURED PUT"]
        self.assertFalse(put["assigned"])
        self.assertAlmostEqual(put["profit"], 250.0)

    def test_a_put_that_expires_in_the_money_is_assigned(self):
        down = FT.index_bars(series([100.0 - i * 0.3 for i in range(500)]))
        out = FT.structure_outcome(self._row(), down, TODAY)
        put = out["results"]["PORTFOLIO SECURED PUT"]
        self.assertTrue(put["assigned"])
        self.assertLess(put["profit"], 250.0)

    def test_a_worthless_long_call_loses_the_whole_premium(self):
        down = FT.index_bars(series([100.0 - i * 0.3 for i in range(500)]))
        out = FT.structure_outcome(self._row(), down, TODAY)
        leaps = out["results"]["LEAPS"]
        self.assertTrue(leaps["expired_worthless"])
        self.assertAlmostEqual(leaps["profit"], -2800.0)

    def test_a_buy_write_forfeits_the_move_above_the_strike(self):
        out = FT.structure_outcome(self._row(), UP, TODAY)
        bw = out["results"]["BUY-WRITE"]
        self.assertTrue(bw["called_away"])
        self.assertGreater(bw["forfeited"], 0)
        shares = out["results"]["SHARES"]
        self.assertLess(bw["profit"], shares["profit"])

    def test_a_contract_that_has_not_expired_is_not_scored(self):
        out = FT.structure_outcome(
            self._row(csp_expiration="2027-01-01",
                      leaps_expiration="2027-01-01",
                      comparison_expiration="2027-01-01"), UP, TODAY)
        self.assertEqual(out["results"], {})
        self.assertIn("has reached its expiration", out["reason"])

    def test_the_report_refuses_a_small_sample(self):
        hist = {"TEST": [self._row()]}
        rep = FT.structure_report(hist, {"TEST": series([100.0 + i * 0.2
                                                         for i in range(500)])},
                                  TODAY)
        for _kind, block in rep["structures"].items():
            self.assertFalse(block["sufficient"])
            self.assertEqual(block["verdict"], FT.INSUFFICIENT)
            self.assertIn("not so a conclusion is drawn", block["reason"])

    def test_an_empty_store_says_there_is_nothing_to_score(self):
        rep = FT.structure_report({}, {}, TODAY)
        self.assertIn("reached its expiration yet", rep["reason"])
        self.assertEqual(rep["settled_recommendations"], 0)


# ── Phase 5: is today's recording good enough for tomorrow's scoring ───────

class TestRecordingCompleteness(unittest.TestCase):
    """Phase 5 asked whether production is collecting everything a future
    exact scoring pass needs. That question is answered forward, against the
    rows being written now — the old ones are never rewritten, so a gap in
    them is history rather than a bug."""

    def _row(self, **kw):
        # Everything a future exact scoring pass needs. The list grew when
        # the production audit asked what a row would have to carry to be
        # scored a year later without reading anything else — the verdict
        # and the price alone cannot say whether the call was right.
        base = {"date": "2026-06-01", "ticker": "AAA", "price": 100.0,
                "config_hash": "abc123", "entry_verdict": "BUY SHARES",
                "preferred_structure": "BUY SHARES",
                "recommended_contract": None,
                "recommended_contract_reason": "no contract",
                "benchmark_symbol": "XLF", "benchmark_close": 42.0,
                "fair_value_bear": 90.0, "fair_value_base": 120.0,
                "fair_value_bull": 150.0, "fair_value_confidence": "MEDIUM",
                "buy_zone": 96.0, "quality_label": "GOOD",
                "growth_label": "STEADY", "valuation_label": "FAIR",
                "revisions_label": "IMPROVING", "value_trap_level": "LOW"}
        base.update(kw)
        return base

    def test_a_complete_row_reports_complete(self):
        out = S.recording_audit({"AAA": [self._row(
            recommended_contract={"strike": 100.0, "expiration": "2026-09-18",
                                  "mid": 3.2, "quote_source": "schwab"})]})
        self.assertEqual(out["complete"], len(S.REQUIRED_FOR_SCORING))
        self.assertEqual(out["missing_examples"], [])
        self.assertIn("all", out["reason"])

    def test_a_missing_field_is_named_along_with_the_ticker(self):
        out = S.recording_audit({"AAA": [self._row(config_hash=None)]})
        self.assertIn("config_hash", out["missing_examples"])
        gap = next(f for f in out["fields"] if f["field"] == "config_hash")
        self.assertEqual(gap["missing"], ["AAA"])
        self.assertIn("cannot be scored", out["reason"])

    def test_a_missing_benchmark_is_caught(self):
        out = S.recording_audit({"AAA": [self._row(benchmark_symbol=None)]})
        self.assertIn("benchmark_symbol", out["missing_examples"])

    def test_only_the_latest_row_of_each_ticker_is_judged(self):
        # An old row lacking a field is not a fault; the current one is.
        old = self._row(date="2025-01-01", config_hash=None)
        new = self._row(recommended_contract={"strike": 1.0})
        out = S.recording_audit({"AAA": [old, new]})
        self.assertEqual(out["complete"], len(S.REQUIRED_FOR_SCORING))

    def test_every_required_field_carries_a_plain_english_description(self):
        for _key, what in S.REQUIRED_FOR_SCORING:
            self.assertGreater(len(what), 10)
            self.assertNotIn("_", what)

    def test_the_exact_contract_and_its_quote_are_both_required(self):
        keys = {k for k, _ in S.REQUIRED_FOR_SCORING}
        self.assertIn("recommended_contract", keys)
        self.assertIn("config_hash", keys)
        what = dict(S.REQUIRED_FOR_SCORING)["recommended_contract"]
        self.assertIn("quote", what)




class TestEligibility(unittest.TestCase):
    """A stored recommendation is immutable. When something it needed was
    never written down it is excluded with a reason, never repaired and
    never deleted — and the requirements are conditional on what the
    recommendation actually was."""

    def _row(self, **kw):
        base = {"date": "2026-08-19", "ticker": "AAA", "price": 100.0,
                "config_hash": "abc123", "entry_verdict": "BUY SHARES",
                "preferred_structure": "BUY SHARES",
                "benchmark_symbol": "SPY", "benchmark_close": 500.0}
        base.update(kw)
        return base

    # ── the archive ──
    def test_rules_in_the_archive_make_a_row_scorable(self):
        got = FT.eligibility(self._row(), {"abc123"})
        self.assertTrue(got["eligible"])
        self.assertEqual(got["state"], FT.ELIGIBLE)

    def test_rules_missing_from_the_archive_exclude_the_row(self):
        got = FT.eligibility(self._row(), {"something else"})
        self.assertFalse(got["eligible"])
        self.assertEqual(got["state"], "NOT ELIGIBLE FOR FORWARD VALIDATION")
        self.assertEqual(got["reasons"], [FT.UNRECOVERABLE_CONFIG])
        self.assertIn("cannot be read back", got["notes"][0])

    def test_an_empty_archive_excludes_every_row(self):
        self.assertFalse(FT.eligibility(self._row(), set())["eligible"])

    def test_no_archive_means_the_question_is_not_asked(self):
        self.assertTrue(FT.eligibility(self._row(), None)["eligible"])

    def test_a_row_with_no_rules_at_all_is_excluded_when_an_archive_exists(self):
        got = FT.eligibility(self._row(config_hash=None), {"abc123"})
        self.assertEqual(got["reasons"], [FT.UNRECOVERABLE_CONFIG])

    def test_a_row_with_no_rules_is_not_condemned_when_nobody_asked(self):
        # `known_hashes=None` means the caller has no archive and is not
        # asking. Production always passes one.
        self.assertTrue(FT.eligibility(self._row(config_hash=None), None)
                        ["eligible"])

    # ── contracts, conditional on the structure ──
    def test_buy_shares_needs_no_contract(self):
        for verdict in ("BUY SHARES", "WAIT", "AVOID", "TOSS UP"):
            got = FT.eligibility(self._row(entry_verdict=verdict),
                                {"abc123"})
            self.assertTrue(got["eligible"], verdict)
            self.assertEqual(got["contract_required"], [], verdict)

    def test_a_put_needs_its_own_put(self):
        got = FT.eligibility(
            self._row(entry_verdict="SELL PORTFOLIO SECURED PUT"),
            {"abc123"})
        self.assertFalse(got["eligible"])
        self.assertIn(FT.NO_CONTRACT, got["reasons"])

    def test_a_put_with_its_strike_and_credit_is_scorable(self):
        got = FT.eligibility(
            self._row(entry_verdict="SELL PORTFOLIO SECURED PUT",
                      recommended_contract={"strike": 95.0, "credit": 2.1,
                                            "expiration": "2026-10-16"}),
            {"abc123"})
        self.assertTrue(got["eligible"], got["reasons"])

    def test_a_long_dated_call_needs_its_strike(self):
        got = FT.eligibility(
            self._row(entry_verdict="BUY LEAPS",
                      recommended_contract={"debit": 20.0}), {"abc123"})
        self.assertIn(FT.NO_CONTRACT, got["reasons"])

    def test_a_buy_write_needs_the_call_it_writes(self):
        got = FT.eligibility(
            self._row(entry_verdict="BUY-WRITE",
                      recommended_contract={"call_strike": 110.0,
                                            "credit": 1.4}), {"abc123"})
        self.assertTrue(got["eligible"], got["reasons"])

    def test_a_spread_needs_both_legs(self):
        one = FT.eligibility(
            self._row(entry_verdict="BULL CALL SPREAD",
                      recommended_contract={"long_strike": 100.0,
                                            "debit": 4.0}), {"abc123"})
        self.assertIn(FT.NO_CONTRACT, one["reasons"])
        both = FT.eligibility(
            self._row(entry_verdict="BULL CALL SPREAD",
                      recommended_contract={"long_strike": 100.0,
                                            "short_strike": 110.0,
                                            "debit": 4.0}), {"abc123"})
        self.assertTrue(both["eligible"], both["reasons"])

    def test_a_contract_with_no_price_cannot_be_settled(self):
        got = FT.eligibility(
            self._row(entry_verdict="BUY LEAPS",
                      recommended_contract={"strike": 100.0}), {"abc123"})
        self.assertIn(FT.NO_QUOTE, got["reasons"])

    def test_an_unknown_structure_is_asked_for_a_contract_not_waved_through(self):
        self.assertEqual(FT.contract_required_for("SOME NEW THING"),
                         ("strike",))

    def test_an_irrelevant_missing_field_does_not_condemn_a_row(self):
        # A BUY SHARES row has no put, no call and no legs. None of that is
        # a defect, and calling it one would throw away good days.
        got = FT.eligibility(self._row(recommended_contract=None), {"abc123"})
        self.assertTrue(got["eligible"])

    # ── the benchmark gates only the market comparison ──
    def test_a_missing_benchmark_does_not_exclude_the_row(self):
        got = FT.eligibility(self._row(benchmark_close=None), {"abc123"})
        self.assertTrue(got["eligible"])
        self.assertFalse(got["benchmark_relative"])
        self.assertIn("not against the market", got["benchmark_reason"])

    def test_a_recorded_benchmark_enables_the_market_comparison(self):
        self.assertTrue(FT.eligibility(self._row(), {"abc123"})
                        ["benchmark_relative"])

    def test_an_outcome_without_a_recorded_benchmark_is_not_compared(self):
        series = [(f"2026-0{6 + i // 30}-{(i % 30) + 1:02d}", 100.0 + i,
                   101.0 + i, 99.0 + i) for i in range(120)]
        series = FT.index_bars([{"date": d, "close": c, "high": h, "low": lo}
                               for d, c, h, lo in series])
        row = self._row(date=series[0][0], benchmark_close=None)
        got = FT.outcome(row, series, 30, series[-1][0], benchmark=series)
        self.assertIsNotNone(got)
        self.assertIsNone(got["benchmark_return_pct"])
        self.assertFalse(got["benchmark_relative_eligible"])
        self.assertIn("not against the market", got["benchmark_note"])

    # ── the report over a whole store ──
    def test_the_report_separates_what_can_be_scored_from_what_cannot(self):
        hist = {"AAA": [self._row(), self._row(date="2026-08-18",
                                               config_hash="gone")]}
        got = FT.eligibility_report(hist, {"abc123"})
        self.assertEqual((got["eligible"], got["not_eligible"]), (1, 1))
        self.assertEqual(got["by_reason"], {FT.UNRECOVERABLE_CONFIG: 1})
        self.assertIn("Nothing is repaired", got["reason"])

    def test_the_report_leaves_the_rows_untouched(self):
        hist = {"AAA": [self._row(config_hash="gone")]}
        before = json.dumps(hist, sort_keys=True)
        FT.eligibility_report(hist, {"abc123"})
        self.assertEqual(json.dumps(hist, sort_keys=True), before)

    def test_observations_exclude_unscorable_rows_and_count_them(self):
        bars = [{"date": f"2026-0{4 + i // 28}-{(i % 28) + 1:02d}",
                 "close": 100.0, "high": 101.0, "low": 99.0} for i in range(120)]
        hist = {"AAA": [self._row(date=bars[0]["date"], config_hash="gone")]}
        got = FT.observations(hist, {"AAA": bars}, bars[-1]["date"],
                             known_hashes={"abc123"})
        self.assertEqual(got["n"], 0)
        self.assertEqual(got["excluded_by_reason"],
                         {FT.UNRECOVERABLE_CONFIG: 1})
        self.assertTrue(got["config_archive_checked"])
        self.assertIn("left alone", got["excluded_note"])

    def test_observations_without_an_archive_score_everything(self):
        bars = [{"date": f"2026-0{4 + i // 28}-{(i % 28) + 1:02d}",
                 "close": 100.0, "high": 101.0, "low": 99.0} for i in range(120)]
        hist = {"AAA": [self._row(date=bars[0]["date"], config_hash="gone")]}
        got = FT.observations(hist, {"AAA": bars}, bars[-1]["date"])
        self.assertGreater(got["n"], 0)
        self.assertFalse(got["config_archive_checked"])

if __name__ == "__main__":                            # pragma: no cover
    unittest.main()
