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
            "benchmark_symbol": "SPY", "benchmark_close": 100.0}
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
        # actually written against. The snapshot is taken at the capture
        # hour and the close is the official one, so the two differ a
        # little and the recorded one wins.
        #
        # The gap here is one percent rather than the fifty it used to be:
        # a recorded price at half that day's close is not a capture taken a
        # few minutes early, it is a two-for-one split re-basing the series,
        # and a row whose basis moved is now refused rather than scored. See
        # TestPriceBasisChanged.
        o = FT.outcome(row(price=99.0), UP, 90, TODAY)
        self.assertEqual(o["price"], 99.0)
        self.assertAlmostEqual(o["return_pct"],
                               (o["end_price"] / 99.0 - 1.0) * 100.0)


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
                "benchmark_symbol": "SPY", "benchmark_close": 100.0}
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
                      recommended_contract={"structure": "PORTFOLIO SECURED PUT",
                                            "strike": 95.0, "credit": 2.1,
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
                      recommended_contract={"structure": "BUY-WRITE",
                                            "call_strike": 110.0,
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
                      recommended_contract={"structure": "BULL CALL SPREAD",
                                            "long_strike": 100.0,
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


    # ── the stored contract has to BE the recommended one ──
    def test_a_contract_from_another_structure_is_refused(self):
        # The comparator's preferred structure and the recommendation are not
        # always the same, and the stored contract is stamped with the
        # comparator's. A long-dated call has a strike and a price, exactly
        # like a short put, so the numbers alone cannot tell them apart.
        got = FT.eligibility(
            self._row(entry_verdict="SELL PORTFOLIO SECURED PUT",
                      recommended_contract={"structure": "LEAPS",
                                            "strike": 90.0, "debit": 20.0}),
            {"abc123"})
        self.assertIn(FT.WRONG_CONTRACT, got["reasons"])

    def test_an_unstamped_contract_cannot_be_confirmed_so_is_refused(self):
        got = FT.eligibility(
            self._row(entry_verdict="BUY LEAPS",
                      recommended_contract={"strike": 90.0, "debit": 20.0}),
            {"abc123"})
        self.assertIn(FT.WRONG_CONTRACT, got["reasons"])

    def test_the_two_vocabularies_are_treated_as_the_same_structure(self):
        # The verdict says SELL PORTFOLIO SECURED PUT; the comparator stamps
        # the contract PORTFOLIO SECURED PUT. Same thing.
        for verdict, stored in (("SELL PORTFOLIO SECURED PUT",
                                 "PORTFOLIO SECURED PUT"),
                                ("BUY LEAPS", "LEAPS"),
                                ("BUY-WRITE", "BUY-WRITE"),
                                ("BULL CALL SPREAD", "BULL CALL SPREAD")):
            self.assertTrue(FT._structures_agree(verdict, stored),
                            f"{verdict} vs {stored}")

    # ── the benchmark baseline is the one recorded on the day ──
    def test_the_benchmark_return_starts_from_the_recorded_close(self):
        # The series says the benchmark was at 100 that day; the row recorded
        # 50. The row wins — that is the number that existed when the call
        # was made, and it is why it is written down prospectively.
        bars = FT.index_bars(series([100.0] * 400))
        r = row(day="2025-01-01", benchmark_close=50.0)
        got = FT.outcome(r, UP, 90, TODAY, bars)
        self.assertAlmostEqual(got["benchmark_return_pct"], 100.0, places=6)
        self.assertIn("recorded on the day", got["benchmark_note"])

    def test_a_later_correction_to_the_benchmark_cannot_move_the_entry(self):
        bars_a = FT.index_bars(series([100.0] * 400))
        bars_b = FT.index_bars(series([120.0] * 400))   # the same days, revised
        r = row(day="2025-01-01", benchmark_close=100.0)
        a = FT.outcome(r, UP, 90, TODAY, bars_a)["benchmark_return_pct"]
        b = FT.outcome(r, UP, 90, TODAY, bars_b)["benchmark_return_pct"]
        # Both ends move with the series, but the ENTRY never does: the
        # difference here is the far end only, not a shifted baseline.
        self.assertAlmostEqual(a, 0.0, places=6)
        self.assertAlmostEqual(b, 20.0, places=6)

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


# ══════════════════════════════════════════════════════════════════════════
# THE CORRECTNESS PASS — benchmark identity and price basis
# ══════════════════════════════════════════════════════════════════════════


class TestBenchmarkIdentity(unittest.TestCase):
    """A row is measured against the index it was RECORDED against.

    Each stored recommendation names its own sector benchmark. The validation
    run used to fetch one series — the first watchlist symbol's — and hand it
    to every row, so a technology holding recorded against XLK was settled up
    using XLE's later close divided by XLK's starting close. Both series
    being flat, that reported a sixty-point excess return on a stock that had
    not moved.
    """

    XLK = FT.index_bars(series([230.0] * 500))
    XLE = FT.index_bars(series([90.0] * 500))

    def _row(self):
        return row(benchmark_symbol="XLK", benchmark_close=230.0)

    def test_the_wrong_index_by_name_is_refused_not_computed(self):
        o = FT.outcome(self._row(), FLAT, 30, TODAY, self.XLE,
                       benchmark_symbol="XLE")
        self.assertIsNone(o["benchmark_return_pct"])
        self.assertIsNone(o["excess_return_pct"])
        self.assertFalse(o["benchmark_relative_eligible"])
        self.assertIn("different index", o["benchmark_note"])

    def test_a_map_resolves_each_row_to_its_own_index(self):
        both = {"XLK": self.XLK, "XLE": self.XLE}
        o = FT.outcome(self._row(), FLAT, 30, TODAY, both)
        self.assertTrue(o["benchmark_relative_eligible"])
        self.assertAlmostEqual(o["benchmark_return_pct"], 0.0)
        self.assertAlmostEqual(o["excess_return_pct"], 0.0)

    def test_an_index_absent_from_the_map_is_not_substituted(self):
        o = FT.outcome(self._row(), FLAT, 30, TODAY, {"XLE": self.XLE})
        self.assertIsNone(o["benchmark_return_pct"])
        self.assertFalse(o["benchmark_relative_eligible"])

    def test_two_stocks_on_two_benchmarks_in_one_run(self):
        """The case the single-series run got wrong, end to end."""
        tech = row(day="2025-01-01", ticker="MSFT",
                   benchmark_symbol="XLK", benchmark_close=230.0)
        energy = row(day="2025-01-01", ticker="XOM",
                     benchmark_symbol="XLE", benchmark_close=90.0)
        obs = FT.observations(
            {"MSFT": [tech], "XOM": [energy]},
            {"MSFT": series([100.0] * 500), "XOM": series([100.0] * 500)},
            TODAY,
            {"XLK": series([230.0] * 500), "XLE": series([90.0] * 500)},
            horizons=(30,))
        self.assertEqual(obs["n"], 2)
        for r in obs["rows"]:
            self.assertTrue(r["benchmark_relative_eligible"], r["ticker"])
            # Flat stock against a flat benchmark is zero excess, whichever
            # sector the row belongs to.
            self.assertAlmostEqual(r["excess_return_pct"], 0.0)

    def test_the_map_is_matched_case_insensitively(self):
        # A row may have recorded its benchmark in either case; the index is
        # the same index either way.
        r = row(benchmark_symbol="xlk", benchmark_close=230.0)
        o = FT.outcome(r, FLAT, 30, TODAY, {"XLK": self.XLK})
        self.assertTrue(o["benchmark_relative_eligible"])

    def test_a_single_series_still_works_for_a_one_benchmark_caller(self):
        o = FT.outcome(self._row(), FLAT, 30, TODAY, self.XLK)
        self.assertTrue(o["benchmark_relative_eligible"])


class TestPriceBasisChanged(unittest.TestCase):
    """A split re-bases the price series under a stored recommendation.

    The row keeps the price it was written at. The provider returns
    split-adjusted history, so the same day now reads at a tenth of it and a
    flat stock scores −90%. Worse, the recommended put is settled against an
    underlying a tenth of its strike and reads as a total loss.

    Nothing is repaired. An option's terms after a corporate action are set
    by the clearing corporation and do not always equal simple split
    arithmetic, so the row is refused and left exactly as it was written.
    """

    def _split_series(self, factor):
        return FT.index_bars(series([500.0 / factor] * 500))

    def test_ten_for_one(self):
        r = row(price=500.0)
        self.assertIsNone(FT.outcome(r, self._split_series(10), 30, TODAY))

    def test_four_for_one(self):
        r = row(price=500.0)
        self.assertIsNone(FT.outcome(r, self._split_series(4), 30, TODAY))

    def test_two_for_one(self):
        r = row(price=500.0)
        self.assertIsNone(FT.outcome(r, self._split_series(2), 30, TODAY))

    def test_a_reverse_split(self):
        r = row(price=5.0)
        self.assertIsNone(
            FT.outcome(r, FT.index_bars(series([50.0] * 500)), 30, TODAY))

    def test_a_five_for_four_split_is_caught_inside_the_drift_band(self):
        """The case a tolerance band alone waves through.

        A five-for-four gives a ratio of exactly 1.25 and a four-for-five
        reverse gives 0.8 — both inside any drift band wide enough to allow
        an early capture — and a flat holding would then report a twenty-five
        percent return. The ratio is checked against the ones a corporate
        action actually produces, so the size of the split does not matter.
        """
        r = row(price=100.0)
        five_for_four = FT.index_bars(series([80.0] * 500))
        self.assertIsNone(FT.outcome(r, five_for_four, 30, TODAY))
        got = FT.basis_change(100.0, 80.0)
        self.assertEqual(got["what"], "a 5-for-4 split")

    def test_a_four_for_five_reverse_is_caught_inside_the_band(self):
        r = row(price=100.0)
        reverse = FT.index_bars(series([125.0] * 500))
        self.assertIsNone(FT.outcome(r, reverse, 30, TODAY))
        self.assertEqual(FT.basis_change(100.0, 125.0)["what"],
                         "a 5-for-4 reverse split")

    def test_a_four_for_three_and_a_three_for_two_are_caught(self):
        for close, name in ((75.0, "a 4-for-3 split"),
                            (200.0 / 3.0, "a 3-for-2 split")):
            got = FT.basis_change(100.0, close)
            self.assertIsNotNone(got, name)
            self.assertEqual(got["what"], name)

    def test_a_split_is_named_the_way_a_company_declares_one(self):
        # "a 1.25-for-1 split" is not a phrase anybody uses.
        for rec, close, want in ((100.0, 80.0, "a 5-for-4 split"),
                                 (500.0, 50.0, "a 10-for-1 split"),
                                 (5.0, 50.0, "a 10-for-1 reverse split"),
                                 (100.0, 40.0, "a 5-for-2 split")):
            self.assertEqual(FT.basis_change(rec, close)["what"], want)

    def test_drift_that_is_not_a_split_ratio_is_still_scored(self):
        """The band still does its job for an ordinary early capture."""
        for close in (99.6, 98.0, 92.0, 87.0, 83.0):
            self.assertIsNone(FT.basis_change(100.0, close),
                              f"{close} refused as a basis change")

    def test_no_split_is_scored_normally(self):
        r = row(price=100.0)
        o = FT.outcome(r, FLAT, 30, TODAY)
        self.assertIsNotNone(o)
        self.assertAlmostEqual(o["return_pct"], 0.0)

    def test_a_capture_taken_before_the_close_is_not_a_split(self):
        # The snapshot is the last trade at the capture hour and the series
        # carries the official close. A couple of percent apart is ordinary.
        o = FT.outcome(row(price=98.0), FLAT, 30, TODAY)
        self.assertIsNotNone(o)
        self.assertEqual(o["price"], 98.0)

    def test_a_real_crash_is_not_mistaken_for_a_split(self):
        """The test that matters most: a genuine collapse must still score.

        A stock that fell sixty percent that day fell sixty percent in the
        recorded price AND in that day's close — they describe the same day.
        Only a re-basing can separate them.
        """
        crashed = FT.index_bars(
            series([100.0] * 10 + [40.0] * 490))
        recommended_on = series([0] * 500)[10]["date"]
        o = FT.outcome(row(day=recommended_on, price=40.0), crashed, 30, TODAY)
        self.assertIsNotNone(o, "a real crash was refused as a split")
        self.assertAlmostEqual(o["return_pct"], 0.0)

    def test_the_recommended_contract_is_not_settled_against_a_new_basis(self):
        r = row(price=500.0, csp_strike=480.0, csp_credit=9.0,
                csp_expiration="2025-03-20",
                comparison_expiration="2025-03-20")
        got = FT.structure_outcome(r, self._split_series(10), TODAY)
        self.assertEqual(got["results"], {})
        self.assertIn("split", got["reason"])
        self.assertEqual(got["price_basis_changed"]["split"], 10.0)

    def test_the_run_reports_the_refusal_rather_than_hiding_it(self):
        obs = FT.observations(
            {"NVDA": [row(price=500.0)]},
            {"NVDA": series([50.0] * 500)}, TODAY, horizons=(30,))
        self.assertEqual(obs["n"], 0)
        self.assertEqual(obs["skipped"]["price_basis_changed"], 1)
        self.assertIn(FT.PRICE_BASIS_CHANGED, obs["excluded_by_reason"])

    def test_the_split_is_named_in_the_reason(self):
        got = FT.basis_change(500.0, 50.0)
        self.assertEqual(got["split"], 10.0)
        self.assertIn("10-for-1 split", got["what"])
        got = FT.basis_change(5.0, 50.0)
        self.assertIn("reverse split", got["what"])

    def test_an_unexplained_rebasing_is_refused_too(self):
        # A ratio that lands on no known split is still a basis change, and
        # an unexplained one is not more trustworthy than an explained one.
        got = FT.basis_change(500.0, 137.0)
        self.assertIsNotNone(got)
        self.assertIsNone(got["split"])
        self.assertIn("restated price series", got["what"])


class TestTheStoredContractIsTheRecommendedOne(unittest.TestCase):
    """Every actionable verdict records the contract IT named.

    The contract used to come from whichever structure the equal-capital
    comparator ranked first. Above the buy zone the recommendation is SELL
    PORTFOLIO SECURED PUT — the short-dated optimizer's pick — while the
    comparator has usually ranked SHARES or a long-dated call, so the row
    stored a contract belonging to a different structure at a different
    expiration. Forward validation rightly refused it, and the put the app
    had actually recommended was sitting unused in the same snapshot.
    """

    def _snap(self, verdict, preferred="SHARES"):
        return {
            "symbol": "AAPL", "as_of": "2026-08-19T17:00:00-04:00",
            "price": 240.0, "config_hash": "cfg1",
            "entry": {"verdict": verdict},
            "structures": {
                "chain_source": "schwab",
                "comparison": {
                    "preferred": preferred, "expiration": "2027-06-18",
                    "toss_up": False,
                    "rows": [
                        {"kind": "SHARES", "eligible": True,
                         "expiration": "2027-06-18", "dte": 303,
                         "contract": {"shares": 100, "entry": 240.0},
                         "liquidity": {}},
                        {"kind": "LEAPS", "eligible": True,
                         "expiration": "2027-06-18", "dte": 303,
                         "contract": {"strike": 230.0, "debit": 38.5,
                                      "delta": 0.62, "iv": 0.26},
                         "liquidity": {"bid": 38.0, "ask": 39.0, "mid": 38.5,
                                       "spread_pct": 2.6,
                                       "open_interest": 1200, "volume": 45}},
                        {"kind": "BUY-WRITE", "eligible": True,
                         "expiration": "2027-06-18", "dte": 303,
                         "contract": {"call_strike": 270.0,
                                      "call_credit": 12.4, "delta": 0.35},
                         "liquidity": {"bid": 12.4, "ask": 12.8, "mid": 12.6,
                                       "open_interest": 800}},
                        {"kind": "BULL CALL SPREAD", "eligible": True,
                         "expiration": "2027-06-18", "dte": 303,
                         "contract": {"long_strike": 240.0, "long_debit": 30.0,
                                      "short_strike": 280.0,
                                      "short_credit": 14.0, "net_debit": 16.0},
                         "liquidity": {
                             "long": {"bid": 29.5, "ask": 30.0, "mid": 29.75,
                                      "open_interest": 900, "volume": 20},
                             "short": {"bid": 14.0, "ask": 14.4, "mid": 14.2,
                                       "open_interest": 700, "volume": 15}}},
                    ]},
                "put": {"available": True, "clears_hurdle": True, "best": {
                    "kind": "PORTFOLIO SECURED PUT", "eligible": True,
                    "expiration": "2026-10-16", "dte": 58,
                    "contract": {"strike": 215.0, "credit": 4.30,
                                 "delta": -0.27, "iv": 0.28},
                    "liquidity": {"bid": 4.30, "ask": 4.45, "mid": 4.375,
                                  "spread_pct": 3.4, "open_interest": 4200,
                                  "volume": 310}}}},
            "benchmark": {"symbol": "XLK", "close": 230.0}}

    def _stored(self, verdict, preferred="SHARES"):
        return S._recommended_contract(self._snap(verdict, preferred))

    def _eligible(self, verdict, preferred="SHARES"):
        got = self._stored(verdict, preferred)
        return FT.eligibility(
            {"date": "2026-08-19", "ticker": "AAPL", "price": 240.0,
             "config_hash": "cfg1", "entry_verdict": verdict,
             "preferred_structure": preferred,
             "recommended_contract": got["recommended_contract"],
             "benchmark_symbol": "XLK", "benchmark_close": 230.0},
            known_hashes={"cfg1"})

    def test_a_secured_put_stores_the_optimizers_own_put(self):
        c = self._stored("SELL PORTFOLIO SECURED PUT")["recommended_contract"]
        self.assertEqual(c["structure"], "PORTFOLIO SECURED PUT")
        self.assertEqual(c["strike"], 215.0)
        self.assertEqual(c["expiration"], "2026-10-16")
        self.assertEqual(c["credit"], 4.30)
        self.assertEqual(c["dte"], 58)
        # The quote it was chosen on, not one fetched later.
        self.assertEqual(c["bid"], 4.30)
        self.assertEqual(c["ask"], 4.45)
        self.assertEqual(c["open_interest"], 4200)
        self.assertEqual(c["delta"], -0.27)
        self.assertEqual(c["iv"], 0.28)

    def test_a_secured_put_row_is_forward_test_eligible(self):
        """The whole point: this strategy can now accumulate evidence."""
        fit = self._eligible("SELL PORTFOLIO SECURED PUT")
        self.assertTrue(fit["eligible"], fit["reasons"])

    def test_leaps_stores_the_exact_long_dated_call(self):
        c = self._stored("BUY LEAPS")["recommended_contract"]
        self.assertEqual(c["structure"], "LEAPS")
        self.assertEqual(c["strike"], 230.0)
        self.assertEqual(c["debit"], 38.5)
        self.assertTrue(self._eligible("BUY LEAPS")["eligible"])

    def test_a_buy_write_stores_the_exact_call_it_writes(self):
        c = self._stored("BUY-WRITE")["recommended_contract"]
        self.assertEqual(c["structure"], "BUY-WRITE")
        self.assertEqual(c["call_strike"], 270.0)
        self.assertEqual(c["credit"], 12.4)
        self.assertTrue(self._eligible("BUY-WRITE")["eligible"])

    def test_a_spread_stores_both_legs_and_both_quotes(self):
        c = self._stored("BULL CALL SPREAD")["recommended_contract"]
        self.assertEqual(c["structure"], "BULL CALL SPREAD")
        self.assertEqual(c["long_strike"], 240.0)
        self.assertEqual(c["short_strike"], 280.0)
        # Entered for the NET debit; neither leg alone is what was paid.
        self.assertEqual(c["debit"], 16.0)
        self.assertEqual(c["legs"]["long"]["bid"], 29.5)
        self.assertEqual(c["legs"]["short"]["bid"], 14.0)
        self.assertEqual(c["legs"]["long"]["debit"], 30.0)
        self.assertEqual(c["legs"]["short"]["credit"], 14.0)
        self.assertTrue(self._eligible("BULL CALL SPREAD")["eligible"])

    def test_verdicts_that_name_no_option_store_none(self):
        for v in ("BUY SHARES", "WAIT", "AVOID", "TOSS UP"):
            got = self._stored(v)
            self.assertIsNone(got["recommended_contract"], v)
            self.assertIn("no option contract", got["recommended_contract_reason"])
            self.assertTrue(self._eligible(v)["eligible"], v)

    def test_the_structure_is_stamped_from_the_verdict_not_the_comparator(self):
        """A contract's fields cannot identify it.

        A long-dated call and a short put both carry a strike and a price, so
        the stamp has to come from the decision rather than from the shape.
        """
        for verdict, preferred, expect in (
                ("SELL PORTFOLIO SECURED PUT", "LEAPS", "PORTFOLIO SECURED PUT"),
                ("BUY LEAPS", "SHARES", "LEAPS"),
                ("BUY-WRITE", "LEAPS", "BUY-WRITE")):
            c = self._stored(verdict, preferred)["recommended_contract"]
            self.assertEqual(c["structure"], expect,
                             f"{verdict} preferred={preferred}")

    def test_the_recommended_structure_is_the_verdict(self):
        got = self._stored("SELL PORTFOLIO SECURED PUT", preferred="SHARES")
        self.assertEqual(got["recommended_structure"],
                         "SELL PORTFOLIO SECURED PUT")
