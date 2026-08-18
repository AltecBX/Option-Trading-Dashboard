"""Tests for invest_scan — providers, staleness and the snapshot store.

The contract this file defends: the UI never talks to a provider, so every
provider failure has to have a defined, visible outcome. There are exactly
three, and all three are pinned here.

  1. Provider answers            → the live value, with its source.
  2. Provider fails, store has a
     recent value                → the stored value, flagged STALE with its age.
  3. Provider fails, store is
     empty or the value is past
     its shelf life              → N/A with the reason. Never a stale price
                                   presented as current.

Everything runs against a temporary data directory with injected providers;
no network, no real filings.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("JERRY_NO_NET", "1")

import fundamentals as F
import invest_scan as S


def iso(hours_ago=0.0):
    return (datetime.now().astimezone()
            - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def concept(unit, rows):
    return {"units": {unit: rows}}


def fact(start, end, val, form="10-Q", filed="2026-02-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


def four(name, unit, vals, filed="2026-02-01"):
    spans = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
             ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31")]
    return {name: concept(unit, [fact(s, e, v, filed=filed)
                                 for (s, e), v in zip(spans, vals)])}


def sample_facts():
    """A clean, profitable, growing US GAAP filer."""
    gaap = {}
    gaap.update(four("Revenues", "USD", [100.0, 110.0, 120.0, 130.0]))
    gaap.update(four("NetIncomeLoss", "USD", [10.0, 11.0, 12.0, 13.0]))
    gaap.update(four("EarningsPerShareDiluted", "USD/shares", [0.10, 0.11, 0.12, 0.13]))
    gaap.update(four("WeightedAverageNumberOfDilutedSharesOutstanding",
                     "shares", [100.0] * 4))
    gaap.update(four("NetCashProvidedByUsedInOperatingActivities", "USD", [15.0] * 4))
    gaap.update(four("PaymentsToAcquirePropertyPlantAndEquipment", "USD", [3.0] * 4))
    # A prior year, so year-over-year has something to compare against.
    prior = [("2024-01-01", "2024-03-31"), ("2024-04-01", "2024-06-30"),
             ("2024-07-01", "2024-09-30"), ("2024-10-01", "2024-12-31")]
    for name, unit, vals in (("Revenues", "USD", [80.0, 85.0, 90.0, 95.0]),
                             ("NetIncomeLoss", "USD", [7.0, 8.0, 9.0, 10.0]),
                             ("EarningsPerShareDiluted", "USD/shares",
                              [0.07, 0.08, 0.09, 0.10]),
                             ("WeightedAverageNumberOfDilutedSharesOutstanding",
                              "shares", [100.0] * 4)):
        gaap[name]["units"][unit] = ([fact(s, e, v, filed="2025-02-01")
                                      for (s, e), v in zip(prior, vals)]
                                     + gaap[name]["units"][unit])
    return {"entityName": "Sample Co", "_cik": 42, "_fetched_ts": time.time(),
            "facts": {"us-gaap": gaap,
                      "dei": {"EntityCommonStockSharesOutstanding": concept(
                          "shares", [{"end": "2026-01-31", "val": 100.0,
                                      "filed": "2026-02-01"}])}}}


class InvestBase(unittest.TestCase):
    SYM = "SMPL"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="invest-test-")
        self.quote = {"price": 10.0, "source": "test quote", "as_of": iso()}
        self.estimates = None
        self.ten_year = {"pct": 4.0, "as_of": "2026-08-17", "source": "UST curve"}
        S.configure(quote_fn=lambda s: self.quote,
                    estimates_fn=lambda s: self.estimates,
                    ten_year_fn=lambda: self.ten_year,
                    daily_fn=lambda s, d: self.bars,
                    data_dir=self.dir)
        self.bars = {}
        S._MEM.clear()
        F._MEM[self.SYM] = (time.time(), sample_facts())

    def tearDown(self):
        F._MEM.pop(self.SYM, None)
        S._MEM.clear()
        S._STARRED_FN = None
        S._SCHED["started"] = False
        S._SCHED["recorded_for"] = None
        shutil.rmtree(self.dir, ignore_errors=True)


class TestSnapshot(InvestBase):
    def test_core_numbers_are_built_from_the_filings(self):
        s = S.snapshot(self.SYM, force=True)
        self.assertTrue(s["ok"])
        self.assertAlmostEqual(s["revenue_ttm"], 460.0)
        self.assertAlmostEqual(s["eps_ttm"], 0.46)
        self.assertAlmostEqual(s["net_income_ttm"], 46.0)
        self.assertAlmostEqual(s["free_cash_flow_ttm"], 48.0)
        self.assertAlmostEqual(s["shares_outstanding"], 100.0)
        self.assertAlmostEqual(s["market_cap"], 1000.0)

    def test_derived_ratios(self):
        s = S.snapshot(self.SYM, force=True)
        self.assertAlmostEqual(s["trailing_pe"], 10.0 / 0.46)
        self.assertAlmostEqual(s["earnings_yield_pct"], 4.6)
        self.assertAlmostEqual(s["fcf_yield_pct"], 4.8)
        self.assertAlmostEqual(s["net_margin_pct"], 10.0)

    def test_year_over_year_uses_the_prior_twelve_months(self):
        s = S.snapshot(self.SYM, force=True)
        # 460 against 350
        self.assertAlmostEqual(s["revenue_growth_pct"], (460.0 / 350.0 - 1) * 100)
        self.assertAlmostEqual(s["eps_growth_pct"], (0.46 / 0.34 - 1) * 100, places=6)

    def test_forward_fields_are_na_without_an_estimate_provider(self):
        s = S.snapshot(self.SYM, force=True)
        for field in ("eps_forward", "eps_next_year", "forward_pe",
                      "forward_eps_growth_pct", "estimate_change_30d_pct"):
            self.assertIsNone(s[field], field)
        self.assertFalse(s["estimates_available"])

    def test_forward_fields_appear_when_the_provider_answers(self):
        self.estimates = {"available": True, "current_year_eps": 0.50,
                          "next_year_eps": 0.60, "change_30d_pct": 12.0,
                          "source": "test estimates", "as_of": iso()}
        s = S.snapshot(self.SYM, force=True)
        self.assertAlmostEqual(s["eps_forward"], 0.50)
        self.assertAlmostEqual(s["forward_pe"], 20.0)
        self.assertAlmostEqual(s["forward_eps_growth_pct"], 20.0)

    def test_every_field_carries_provenance(self):
        s = S.snapshot(self.SYM, force=True)
        for key in ("price", "treasury_10y", "estimates", "fundamentals"):
            self.assertIn(key, s["provenance"])
        self.assertEqual(s["provenance"]["price"]["source"], "test quote")
        self.assertIn("SEC EDGAR", s["provenance"]["fundamentals"]["source"])

    def test_the_base_snapshot_stamps_the_config_but_does_not_guess_a_verdict(self):
        # The Phase 2 verdict reads the four dimensions, the value-trap state
        # and the business type. None of those exist at snapshot time, so a
        # verdict here would be a guess dressed as an answer; payload() adds
        # it once the inputs are real.
        s = S.snapshot(self.SYM, force=True)
        self.assertNotIn("verdict", s)
        self.assertTrue(s["config_hash"])

    def test_ineligible_filer_says_why_and_shows_no_numbers(self):
        F._MEM["IFRS"] = (time.time(), {
            "entityName": "Foreign Co", "_cik": 9, "_fetched_ts": time.time(),
            "facts": {"ifrs-full": {"Revenue": concept("DKK", [
                fact("2025-01-01", "2025-12-31", 1.0)])}}})
        try:
            s = S.snapshot("IFRS", force=True)
            self.assertFalse(s["ok"])
            self.assertIn("DKK", s["unavailable_reason"])
            self.assertIsNone(s["revenue_ttm"])
            self.assertIsNone(s["eps_ttm"])
        finally:
            F._MEM.pop("IFRS", None)


class TestStaleness(InvestBase):
    def test_live_value_wins(self):
        s = S.snapshot(self.SYM, force=True)
        self.assertFalse(s["provenance"]["price"]["stale"])
        self.assertAlmostEqual(s["price"], 10.0)

    def test_falls_back_to_the_last_stored_value_flagged_stale(self):
        S.snapshot(self.SYM, force=True)                 # store a good price
        self.quote = None                                # provider goes down
        S._MEM.clear()
        s = S.snapshot(self.SYM, force=True)
        self.assertAlmostEqual(s["price"], 10.0)
        self.assertTrue(s["provenance"]["price"]["stale"])
        self.assertIsNotNone(s["provenance"]["price"]["age_hours"])
        self.assertIn("last value", s["provenance"]["price"]["reason"])

    def test_a_price_past_its_shelf_life_is_dropped_not_labelled(self):
        # A share price from three weeks ago is not stale, it is wrong.
        S.snapshot(self.SYM, force=True)
        latest = json.loads((S._paths(self.SYM)[1]).read_text())
        latest["provenance"]["price"]["as_of"] = iso(hours_ago=400)
        (S._paths(self.SYM)[1]).write_text(json.dumps(latest))
        self.quote = None
        S._MEM.clear()
        s = S.snapshot(self.SYM, force=True)
        self.assertIsNone(s["price"])
        self.assertIn("not shown", s["provenance"]["price"]["reason"])

    def test_estimates_tolerate_a_much_older_value_than_a_price(self):
        # An analyst estimate a week old is still the estimate; a share price
        # a week old is not the price. The limits differ on purpose.
        self.estimates = {"available": True, "current_year_eps": 0.5,
                          "next_year_eps": 0.6, "source": "test", "as_of": iso(hours_ago=200)}
        S.snapshot(self.SYM, force=True)
        self.estimates = None
        S._MEM.clear()
        s = S.snapshot(self.SYM, force=True)
        self.assertTrue(s["provenance"]["estimates"]["stale"])
        self.assertAlmostEqual(s["eps_forward"], 0.5)

    def test_no_provider_and_no_store_is_an_explicit_na(self):
        self.quote = None
        s = S.snapshot(self.SYM, force=True)
        self.assertIsNone(s["price"])
        self.assertTrue(s["provenance"]["price"]["reason"])


class TestStore(InvestBase):
    def test_one_row_per_day_and_the_last_write_wins(self):
        S.snapshot(self.SYM, force=True)
        self.quote = {"price": 11.0, "source": "test quote", "as_of": iso()}
        S._MEM.clear()
        S.snapshot(self.SYM, force=True)
        rows = S.load_history(self.SYM)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["price"], 11.0)

    def test_the_daily_row_holds_every_field_a_later_phase_needs(self):
        S.snapshot(self.SYM, force=True)
        row = S.load_history(self.SYM)[0]
        for field in ("date", "ticker", "price", "market_cap", "revenue_ttm",
                      "eps_ttm", "revenue_growth_pct", "eps_growth_pct",
                      "eps_forward", "eps_next_year", "forward_pe",
                      "earnings_yield_pct", "fcf_yield_pct",
                      "estimate_change_30d_pct", "treasury_10y_pct", "sources"):
            self.assertIn(field, row)
        self.assertEqual(row["ticker"], self.SYM)
        self.assertIn("SEC EDGAR", row["sources"]["fundamentals"])

    def test_writes_are_atomic_and_leave_no_temporary_files(self):
        S.snapshot(self.SYM, force=True)
        hist, latest = S._paths(self.SYM)
        self.assertTrue(hist.exists() and latest.exists())
        leftovers = list(hist.parent.glob("*.tmp")) + list(latest.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_a_corrupt_line_does_not_take_the_series_down(self):
        S.snapshot(self.SYM, force=True)
        hist, _ = S._paths(self.SYM)
        hist.write_text(hist.read_text() + "{not json\n")
        self.assertEqual(len(S.load_history(self.SYM)), 1)

    def test_history_survives_without_a_data_directory(self):
        S.configure(quote_fn=lambda s: self.quote, ten_year_fn=lambda: self.ten_year,
                    estimates_fn=lambda s: None, daily_fn=lambda s, d: {},
                    data_dir=None)
        S._MEM.clear()
        s = S.snapshot(self.SYM, force=True)
        self.assertTrue(s["ok"])
        self.assertEqual(S.load_history(self.SYM), [])


class TestDrivers(InvestBase):
    def test_reconciles_and_names_its_method(self):
        d = S.drivers(self.SYM)
        self.assertTrue(d["available"])
        self.assertEqual(d["method"], "log")
        self.assertTrue(d["reconciles"])
        self.assertAlmostEqual(sum(c["value"] for c in d["contributions"]),
                               d["total"], places=9)

    def test_refuses_an_ineligible_filer_with_the_reason(self):
        F._MEM["IFRS2"] = (time.time(), {
            "entityName": "Foreign Co", "_fetched_ts": time.time(),
            "facts": {"ifrs-full": {"Revenue": concept("TWD", [
                fact("2025-01-01", "2025-12-31", 1.0)])}}})
        try:
            d = S.drivers("IFRS2")
            self.assertFalse(d["available"])
            self.assertIn("TWD", d["reason"])
        finally:
            F._MEM.pop("IFRS2", None)


class TestHistory(InvestBase):
    def _bars(self, n=800, start_price=5.0):
        base = datetime(2024, 1, 1)
        return {"bars": [{"date": (base + timedelta(days=i)).date().isoformat(),
                          "close": start_price + i * 0.01} for i in range(n)],
                "source": "test bars"}

    def test_says_so_when_there_is_no_price_history(self):
        self.bars = {}
        h = S.history(self.SYM, years=3)
        self.assertEqual(h["price"], [])
        self.assertTrue(any("No daily price history" in n for n in h["notes"]))

    def test_price_is_indexed_to_100(self):
        self.bars = self._bars()
        h = S.history(self.SYM, years=5)
        self.assertGreater(len(h["price"]), 100)
        self.assertAlmostEqual(h["price"][0]["indexed"], 100.0)

    def test_forward_line_is_empty_and_says_why(self):
        # There is no free archive of past analyst consensus. Back-filling
        # today's estimate across last year's chart would manufacture a
        # history that never happened, so the line simply starts empty.
        self.bars = self._bars()
        h = S.history(self.SYM, years=3)
        self.assertEqual(h["eps_forward"], [])
        self.assertTrue(any("never back-filled" in n for n in h["notes"]))

    def test_forward_line_starts_at_the_first_recorded_snapshot(self):
        self.estimates = {"available": True, "current_year_eps": 0.5,
                          "next_year_eps": 0.6, "source": "test", "as_of": iso()}
        S.snapshot(self.SYM, force=True)
        self.bars = self._bars()
        h = S.history(self.SYM, years=3)
        self.assertEqual(len(h["eps_forward"]), 1)
        self.assertTrue(any("first day this dashboard recorded" in n
                            for n in h["notes"]))

    def test_years_is_clamped_to_three_or_five(self):
        self.bars = self._bars()
        self.assertEqual(S.history(self.SYM, years=1)["years"], 3)
        self.assertEqual(S.history(self.SYM, years=99)["years"], 5)


class TestPayloadAndScheduler(InvestBase):
    def test_payload_carries_every_section_the_tab_renders(self):
        p = S.payload(self.SYM)
        for key in ("verdict", "drivers", "history", "provenance",
                    "metric_detail", "stored_days"):
            self.assertIn(key, p)

    def test_payload_of_an_unknown_symbol_does_not_raise(self):
        p = S.payload("NOSUCHTICKER")
        self.assertFalse(p["ok"])
        self.assertFalse(p["drivers"]["available"])

    def test_scheduler_records_once_a_day_after_the_close(self):
        S._STARRED_FN = lambda: [self.SYM]
        morning = datetime(2026, 8, 18, 9, 0)
        self.assertIsNone(S.tick(now=morning))
        evening = datetime(2026, 8, 18, 18, 0)
        out = S.tick(now=evening)
        self.assertEqual(out["recorded"], [self.SYM])
        self.assertIsNone(S.tick(now=evening))          # not twice in a day

    def test_scheduler_is_bounded(self):
        S._STARRED_FN = lambda: [f"S{i}" for i in range(500)]
        out = S.tick(now=datetime(2026, 8, 18, 18, 0))
        self.assertLessEqual(len(out["recorded"]) + len(out["failed"]),
                             S.MAX_DAILY_SYMBOLS)


class TestConfig(InvestBase):
    def test_reads_the_investment_block_and_hashes_the_whole_file(self):
        S._CFG_CACHE["cfg"] = None
        cfg, h = S.config(refresh=True)
        self.assertIn("attractive_valuation_score", cfg)
        self.assertEqual(len(h), 16)

    def test_data_dir_override_wins_key_by_key(self):
        (S._DATA_DIR.parent / "thresholds.json").write_text(
            json.dumps({"investment": {"verdict": {"min_quality_score": 9.5}}}))
        S._CFG_CACHE["cfg"] = None
        cfg, _h = S.config(refresh=True)
        self.assertAlmostEqual(cfg["min_quality_score"], 9.5)
        # Repo defaults inside the same group survive the key-by-key merge.
        self.assertIn("attractive_valuation_score", cfg)
        self.assertIn("min_peers", cfg)
        S._CFG_CACHE["cfg"] = None



# ══════════════════════════════════════════════════════════════════════════
# PHASE 2
# ══════════════════════════════════════════════════════════════════════════

def daily_bars(n=1400, start=8.0, step=0.01, end_day=None):
    """A rising price series ending today, so windows line up with 'now'."""
    end_day = end_day or datetime.now().date()
    out = []
    for i in range(n):
        d = end_day - timedelta(days=n - 1 - i)
        out.append({"date": d.isoformat(), "close": start + i * step})
    return {"bars": out, "source": "test bars"}


def eps_history(quarters, first_filed_lag=35):
    """Quarterly EPS facts stretching back far enough for a 5-year window."""
    rows = []
    end = datetime.now().date() - timedelta(days=20)
    for i in range(quarters):
        q_end = end - timedelta(days=91 * i)
        q_start = q_end - timedelta(days=90)
        filed = (q_end + timedelta(days=first_filed_lag)).isoformat()
        rows.append(fact(q_start.isoformat(), q_end.isoformat(),
                         0.10 + 0.002 * (quarters - i), filed=filed))
    return list(reversed(rows))


class ValuationBase(InvestBase):
    def setUp(self):
        super().setUp()
        f = sample_facts()
        gaap = f["facts"]["us-gaap"]
        gaap["EarningsPerShareDiluted"]["units"]["USD/shares"] = eps_history(28)
        gaap["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"] = [
            fact(r["start"], r["end"], 100.0, filed=r["filed"])
            for r in eps_history(28)]
        for name, unit, val in (("NetCashProvidedByUsedInOperatingActivities", "USD", 20.0),
                                ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", 5.0)):
            gaap[name]["units"][unit] = [
                fact(r["start"], r["end"], val, filed=r["filed"])
                for r in eps_history(28)]
        F._MEM[self.SYM] = (time.time(), f)
        self.bars = daily_bars()


class TestValuationHistory(ValuationBase):
    def test_builds_a_point_in_time_series_and_a_distribution(self):
        v = S.valuation_history(self.SYM, years=5)
        self.assertTrue(v["available"])
        ey = v["distributions"]["earnings_yield_pct"]
        self.assertTrue(ey["available"])
        self.assertTrue(ey["5y"]["available"])
        for key in ("median", "p10", "p90", "n", "cheap_percentile"):
            self.assertIn(key, ey["5y"])
        self.assertGreater(ey["5y"]["n"], 60)

    def test_a_rising_price_against_flat_earnings_gets_expensive(self):
        # Price climbs all the way through the window while earnings barely
        # move, so today's earnings yield must sit at the cheap-percentile
        # floor of its own history.
        v = S.valuation_history(self.SYM, years=5)
        ey = v["distributions"]["earnings_yield_pct"]["5y"]
        self.assertLess(ey["cheap_percentile"], 15)

    def test_nothing_is_dated_before_it_was_filed(self):
        v = S.valuation_history(self.SYM, years=5)
        pts = v["series"]["earnings_yield_pct"]
        self.assertTrue(pts)
        # Every plotted point must use a quarter whose filing date has passed.
        for p in pts[:5] + pts[-5:]:
            self.assertLessEqual(p.get("period_end"), p["date"])

    def test_free_cash_flow_yield_uses_each_days_known_share_count(self):
        v = S.valuation_history(self.SYM, years=5)
        self.assertTrue(v["distributions"]["fcf_yield_pct"]["available"])

    def test_trailing_pe_is_oriented_so_cheap_is_still_a_high_percentile(self):
        v = S.valuation_history(self.SYM, years=5)
        ey = v["distributions"]["earnings_yield_pct"]["5y"]
        pe = v["distributions"]["trailing_pe"]["5y"]
        # A yield and a multiple run in opposite directions; both are
        # reported so that 100 means cheap.
        self.assertAlmostEqual(ey["cheap_percentile"], pe["cheap_percentile"],
                               delta=6.0)

    def test_too_little_price_history_is_refused_with_a_count(self):
        self.bars = daily_bars(n=40)
        v = S.valuation_history(self.SYM, years=5)
        self.assertFalse(v["available"])
        self.assertIn("daily closes", v["reason"])

    def test_no_price_history_at_all_is_refused(self):
        self.bars = {}
        v = S.valuation_history(self.SYM, years=5)
        self.assertFalse(v["available"])

    def test_an_ineligible_filer_is_refused_with_its_own_reason(self):
        F._MEM["IFRSV"] = (time.time(), {
            "entityName": "Foreign Co", "_fetched_ts": time.time(),
            "facts": {"ifrs-full": {"Revenue": concept("DKK", [
                fact("2025-01-01", "2025-12-31", 1.0)])}}})
        try:
            v = S.valuation_history("IFRSV", years=5)
            self.assertFalse(v["available"])
            self.assertIn("DKK", v["reason"])
        finally:
            F._MEM.pop("IFRSV", None)

    def test_a_split_leaves_no_step_in_the_history(self):
        # The same quarter restated at a tenth after a 10-for-1 split. The
        # latest filing supplies the value, so the series is already on
        # today's share basis and matches split-adjusted prices.
        f = F._MEM[self.SYM][1]
        rows = list(f["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"])
        first = rows[0]
        rows.append(fact(first["start"], first["end"], first["val"] / 10.0,
                         filed="2026-08-01"))
        f["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"] = rows
        F._MEM[self.SYM] = (time.time(), f)
        series = F.ttm_series(f, "eps")
        match = [r for r in series if r["period_end"] == first["end"]]
        if match:
            self.assertLess(match[0]["value"], first["val"])


class TestRegimeInScan(ValuationBase):
    def test_a_regime_block_is_attached(self):
        v = S.valuation_history(self.SYM, years=5)
        self.assertIn("shifted", v["regime"])


class TestQualityBlock(ValuationBase):
    def _facts(self):
        return F.company_facts(self.SYM)

    def test_scores_the_inputs_it_can_build_and_names_the_rest(self):
        btype = S.engine.business_type("3571")
        q = S.quality_block(self.SYM, self._facts(), btype, None)
        self.assertEqual(len(q["components"]), len(S.engine.QUALITY_INPUTS))
        for c in q["components"]:
            self.assertTrue(c["score"] is not None or c["reason"])

    def test_a_bank_is_refused_the_measures_that_do_not_describe_it(self):
        btype = S.engine.business_type("6021")
        q = S.quality_block(self.SYM, self._facts(), btype, None)
        by = {c["key"]: c for c in q["components"]}
        for key in ("roic", "fcf_conversion", "leverage"):
            self.assertEqual(by[key]["reason"], S.engine.SPECIALIZED, key)
            self.assertIsNone(by[key]["score"])

    def test_a_reit_keeps_margins_but_loses_cash_flow_and_leverage(self):
        btype = S.engine.business_type("6798")
        q = S.quality_block(self.SYM, self._facts(), btype, None)
        by = {c["key"]: c for c in q["components"]}
        self.assertEqual(by["fcf_conversion"]["reason"], S.engine.SPECIALIZED)
        self.assertEqual(by["leverage"]["reason"], S.engine.SPECIALIZED)
        self.assertNotEqual(by["operating_margin_trend"]["reason"],
                            S.engine.SPECIALIZED)

    def test_peer_ranking_is_used_when_a_real_group_exists(self):
        # Free cash flow conversion is the input this fixture can actually
        # build, so it is the one that must end up peer-ranked.
        rows = [{"symbol": f"P{i}", "fcf_conversion_pct": 20.0 * i}
                for i in range(6)]
        q = S.quality_block(self.SYM, self._facts(),
                            S.engine.business_type("3571"),
                            {"level": "DIRECT PEERS", "rows": rows})
        self.assertTrue(q["peer_ranked"])
        conv = next(c for c in q["components"] if c["key"] == "fcf_conversion")
        self.assertIn("ranked against 6", conv["scored_against"])

    def test_peer_ranked_is_false_when_nothing_was_actually_ranked(self):
        # The label is a claim on screen. A peer group that has no value for
        # any input this company can build has not ranked anything.
        rows = [{"symbol": f"P{i}", "roic_pct": 5.0 * i} for i in range(6)]
        q = S.quality_block(self.SYM, self._facts(),
                            S.engine.business_type("3571"),
                            {"level": "DIRECT PEERS", "rows": rows})
        self.assertFalse(q["peer_ranked"])

    def test_a_broad_benchmark_group_is_not_used_for_ranking(self):
        rows = [{"symbol": f"P{i}", "fcf_conversion_pct": 20.0 * i}
                for i in range(6)]
        q = S.quality_block(self.SYM, self._facts(),
                            S.engine.business_type("3571"),
                            {"level": "BROAD BENCHMARK", "rows": rows})
        self.assertFalse(q["peer_ranked"])


class TestRevisionsBlock(InvestBase):
    def test_below_four_analysts_is_not_rated(self):
        snap = {"estimate_change_30d_pct": 50.0, "estimate_change_90d_pct": 40.0}
        b = S.revisions_block(snap, {"analyst_count": 3}, {})
        self.assertIsNone(b["score"])
        self.assertEqual(b["label"], S.engine.NOT_RATED)

    def test_four_analysts_is_enough(self):
        snap = {"estimate_change_30d_pct": 50.0, "estimate_change_90d_pct": 40.0}
        b = S.revisions_block(snap, {"analyst_count": 4}, {})
        self.assertIsNotNone(b["score"])

    def test_the_gaap_and_adjusted_bases_are_kept_apart_in_words(self):
        b = S.revisions_block({}, {"analyst_count": 10}, {})
        self.assertIn("GAAP", b["gaap_note"])
        self.assertIn("adjusted", b["gaap_note"])


class TestValueTrapBlock(ValuationBase):
    def test_a_healthy_company_reads_low_or_not_rated(self):
        facts = F.company_facts(self.SYM)
        snap = S.snapshot(self.SYM, force=True)
        q = S.quality_block(self.SYM, facts, S.engine.business_type("3571"), None)
        t = S.value_trap_block(self.SYM, facts, snap, q, {},
                               {"analyst_count": 10}, {})
        self.assertIn(t["level"], ("LOW RISK", "MODERATE RISK",
                                   S.engine.NOT_RATED))

    def test_signals_that_cannot_be_measured_are_listed_as_unknown(self):
        facts = F.company_facts(self.SYM)
        snap = S.snapshot(self.SYM, force=True)
        q = S.quality_block(self.SYM, facts, S.engine.business_type("3571"), None)
        t = S.value_trap_block(self.SYM, facts, snap, q, {},
                               {"analyst_count": 10}, {})
        keys = {u["key"] for u in t["unknown"]} | {a["key"] for a in t["active"]} \
            | {i["key"] for i in t["inactive"]}
        self.assertEqual(keys, set(S.engine.TRAP_SIGNALS))


class TestEarningsCycleWiring(InvestBase):
    def test_the_cycle_uses_the_injected_dates(self):
        today = datetime.now().date()
        S.configure(quote_fn=lambda s: self.quote, ten_year_fn=lambda: self.ten_year,
                    estimates_fn=lambda s: None, daily_fn=lambda s, d: self.bars,
                    earnings_fn=lambda s: {
                        "next": (today + timedelta(days=5)).isoformat(),
                        "last": (today - timedelta(days=80)).isoformat()},
                    data_dir=self.dir)
        self.assertEqual(S._cycle(self.SYM, {})["state"], "PRE-EARNINGS")

    def test_no_dates_is_unknown(self):
        self.assertEqual(S._cycle(self.SYM, {})["state"], "UNKNOWN")


class TestPhase2Snapshot(ValuationBase):
    def test_the_daily_row_records_the_phase_2_state(self):
        S.payload(self.SYM, force=True)
        row = S.load_history(self.SYM)[-1]
        for field in ("quality_score", "quality_label", "growth_score",
                      "valuation_score", "revisions_label",
                      "valuation_self_percentile", "valuation_peer_percentile",
                      "regime_shifted", "value_trap_level", "value_trap_signals",
                      "business_type", "earnings_cycle", "peer_level",
                      "peer_aggregate_pe", "verdict", "underreaction_score",
                      "config_hash", "sic", "target_yield_pct"):
            self.assertIn(field, row, field)

    def test_history_is_never_overwritten_only_the_same_day_is_replaced(self):
        S.payload(self.SYM, force=True)
        first = S.load_history(self.SYM)
        self.quote = {"price": 99.0, "source": "test quote", "as_of": iso()}
        S._MEM.clear()
        S.payload(self.SYM, force=True)
        second = S.load_history(self.SYM)
        self.assertEqual(len(first), len(second))     # same calendar day
        self.assertAlmostEqual(second[-1]["price"], 99.0)

    def test_a_row_written_before_phase_2_is_readable_not_a_crash(self):
        hist, _ = S._paths(self.SYM)
        hist.write_text(json.dumps({"date": "2020-01-01", "ticker": self.SYM,
                                    "price": 5.0}) + "\n")
        rows = S.load_history(self.SYM)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("quality_score", rows[0])     # missing, not zero


class TestPhase2Payload(ValuationBase):
    def test_the_payload_carries_every_phase_2_section(self):
        p = S.payload(self.SYM, force=True)
        for key in ("quality", "growth", "valuation", "revisions", "value_trap",
                    "peers", "valuation_history", "business_type",
                    "earnings_cycle", "drawdowns", "underreaction", "verdict"):
            self.assertIn(key, p, key)

    def test_the_verdict_is_one_of_the_six_words(self):
        p = S.payload(self.SYM, force=True)
        self.assertIn(p["verdict"]["verdict"], S.engine.VERDICTS)

    def test_an_ineligible_filer_still_returns_a_full_shaped_payload(self):
        F._MEM["IFRSP"] = (time.time(), {
            "entityName": "Foreign Co", "_fetched_ts": time.time(),
            "facts": {"ifrs-full": {"Revenue": concept("TWD", [
                fact("2025-01-01", "2025-12-31", 1.0)])}}})
        try:
            p = S.payload("IFRSP", force=True)
            self.assertFalse(p["ok"])
            for key in ("quality", "growth", "valuation", "revisions",
                        "value_trap", "peers", "valuation_history", "verdict"):
                self.assertIn(key, p, key)
            self.assertEqual(p["verdict"]["verdict"], "INSUFFICIENT DATA")
        finally:
            F._MEM.pop("IFRSP", None)

    def test_underreaction_is_marked_experimental_and_stays_out_of_the_verdict(self):
        p = S.payload(self.SYM, force=True)
        self.assertTrue(p["underreaction"]["experimental"])
        reasons = " ".join(p["verdict"]["reasons"])
        self.assertNotIn("underreaction", reasons.lower())

    def test_drawdowns_are_computed_from_the_same_bars(self):
        p = S.payload(self.SYM, force=True)
        self.assertIn("available", p["drawdowns"])


class TestConfigFlattening(InvestBase):
    def test_grouped_thresholds_are_readable_as_one_flat_dict(self):
        S._CFG_CACHE["cfg"] = None
        S._CFG_FN = None
        cfg, _h = S.config(refresh=True)
        for key in ("attractive_valuation_score", "min_quality_score",
                    "min_peers", "min_analysts", "trap_high_signals",
                    "cycle_pre_days", "shift_ratio"):
            self.assertIn(key, cfg, key)
        self.assertNotIn("verdict", cfg)      # groups are folded, not nested
        S._CFG_CACHE["cfg"] = None


# ══════════════════════════════════════════════════════════════════════════
# PHASE 3
# ══════════════════════════════════════════════════════════════════════════

import fair_value as FV                                # noqa: E402
import invest_options as IO                            # noqa: E402
import structures as ST                                # noqa: E402


def phase3_chain(spot=10.0, today=None):
    """A synthetic chain with one short-dated and two long-dated expirations."""
    today = today or datetime.now().date()
    out = {"underlying": {"symbol": "SMPL", "last": spot},
           "expirations": [], "chains": {}, "source": "test"}
    for d in (45, 400, 550):
        t = d / 365.0
        calls, puts = [], []
        k = 4.0
        while k <= 16.0:
            tv = round(0.30 * spot * (t ** 0.5) * 0.4, 2)
            c_int = max(0.0, spot - k)
            p_int = max(0.0, k - spot)
            row = {"volume": 40, "openInterest": 400, "iv": 0.30, "delta": None}
            calls.append({**row, "strike": k, "bid": round(c_int + tv, 2),
                          "ask": round(c_int + tv + 0.05, 2)})
            puts.append({**row, "strike": k, "bid": round(p_int + tv, 2),
                         "ask": round(p_int + tv + 0.05, 2)})
            k += 1.0
        key = (today + timedelta(days=d)).isoformat()
        out["chains"][key] = {"calls": calls, "puts": puts}
        out["expirations"].append(key)
    return out


class Phase3Base(ValuationBase):
    def setUp(self):
        super().setUp()
        self.chain = phase3_chain()
        IO._CHAIN_MEM.clear()
        S.configure(quote_fn=lambda s: self.quote,
                    estimates_fn=lambda s: self.estimates,
                    ten_year_fn=lambda: self.ten_year,
                    daily_fn=lambda s, d: self.bars,
                    chain_fn=lambda s: self.chain,
                    rate_fn=lambda y: {"pct": 4.0, "as_of": "2026-08-17",
                                       "source": "UST curve"},
                    data_dir=self.dir)
        S._MEM.clear()
        S._CFG_CACHE["cfg"] = None

    def tearDown(self):
        IO._CHAIN_MEM.clear()
        IO.configure()
        S._CFG_CACHE["cfg"] = None
        super().tearDown()


class TestGrowthHistory(Phase3Base):
    def test_growth_is_measured_over_the_horizon_being_projected(self):
        facts = F.company_facts(self.SYM)
        h = S.eps_growth_history(facts, horizon_years=3.0)
        self.assertTrue(h["horizon_matched"])
        self.assertGreaterEqual(h["n"], S.MIN_GROWTH_WINDOWS)
        self.assertIn("3-year compound", h["note"])

    def test_a_short_window_falls_back_to_one_year_rates_and_says_so(self):
        facts = F.company_facts(self.SYM)
        h = S.eps_growth_history(facts, horizon_years=6.0)
        self.assertFalse(h["horizon_matched"])
        self.assertIn("ONE-year growth rates", h["note"])

    def test_the_window_stops_at_a_share_basis_break(self):
        f = F.company_facts(self.SYM)
        shares = f["facts"]["us-gaap"][
            "WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"]
        # A clean four-for-one split partway through the series.
        for r in shares[:10]:
            r["val"] = 400.0
        h = S.eps_growth_history(f, horizon_years=3.0)
        self.assertIsNotNone(h["basis"]["from"])
        self.assertIn("share basis changes", h["basis"]["reason"])


class TestFairValueBlock(Phase3Base):
    def test_builds_from_the_companys_own_history(self):
        vh = S.valuation_history(self.SYM, years=5, raw=True)
        snap = S.snapshot(self.SYM, force=True)
        cfg, _h = S.config()
        out = S.fair_value_block(snap, vh, {"level": "BROAD BENCHMARK", "rows": []},
                                 F.company_facts(self.SYM), cfg)
        self.assertTrue(out["available"])
        self.assertLessEqual(out["bear"], out["base"])
        self.assertLessEqual(out["base"], out["bull"])
        self.assertIsNotNone(out["buy_zone"])

    def test_the_forward_peer_method_is_offered_and_refused(self):
        vh = S.valuation_history(self.SYM, years=5, raw=True)
        snap = S.snapshot(self.SYM, force=True)
        cfg, _h = S.config()
        out = S.fair_value_block(snap, vh, {"level": "DIRECT PEERS", "rows": []},
                                 F.company_facts(self.SYM), cfg)
        fwd = next(m for m in out["methods"] if m["key"] == "peers_forward")
        self.assertFalse(fwd["available"])

    def test_a_bank_is_refused_outright(self):
        vh = S.valuation_history(self.SYM, years=5, raw=True)
        snap = S.snapshot(self.SYM, force=True)
        snap["business_type"] = {"type": "BANK", "note": "banks differ"}
        cfg, _h = S.config()
        out = S.fair_value_block(snap, vh, {}, F.company_facts(self.SYM), cfg)
        self.assertFalse(out["available"])
        self.assertEqual(out["verdict"], FV.SPECIALIZED)


class TestExpectedReturnBlock(Phase3Base):
    def test_scenarios_and_a_weighted_result(self):
        vh = S.valuation_history(self.SYM, years=5, raw=True)
        snap = S.snapshot(self.SYM, force=True)
        cfg, _h = S.config()
        out = S.expected_return_block(snap, vh, F.company_facts(self.SYM), {}, cfg)
        self.assertTrue(out["available"])
        self.assertIsNotNone(out["weighted_total_cagr_pct"])
        for s in ("bear", "base", "bull"):
            self.assertTrue(out["scenarios"][s]["available"])

    def test_the_analyst_basis_is_shown_beside_but_never_mixed_in(self):
        vh = S.valuation_history(self.SYM, years=5, raw=True)
        snap = S.snapshot(self.SYM, force=True)
        cfg, _h = S.config()
        out = S.expected_return_block(snap, vh, F.company_facts(self.SYM), {}, cfg)
        note = out["forward_growth_context"]["note"]
        self.assertIn("NOT mixed", note)
        self.assertIn("adjusted", out["forward_growth_context"]["basis"])

    def test_dividends_are_read_from_the_filings_or_explained(self):
        vh = S.valuation_history(self.SYM, years=5, raw=True)
        snap = S.snapshot(self.SYM, force=True)
        cfg, _h = S.config()
        out = S.expected_return_block(snap, vh, F.company_facts(self.SYM), {}, cfg)
        d = out["dividends_detail"]
        self.assertTrue(d["reason"] or d["value"] is not None)


class TestImpliedExpectationsBlock(Phase3Base):
    def block(self):
        snap = S.snapshot(self.SYM, force=True)
        cfg, _h = S.config()
        return S.implied_expectations_block(snap, F.company_facts(self.SYM), cfg)

    def test_enterprise_value_needs_net_debt_and_says_so_when_absent(self):
        out = self.block()
        if not out["available"]:
            self.assertTrue(out["reason"])
        else:
            self.assertIn("enterprise_value", out)

    def test_consensus_growth_is_never_invented(self):
        out = self.block()
        self.assertFalse(out["consensus_growth"]["available"])
        self.assertIn("will not print one it does not have",
                      out["consensus_growth"]["reason"])


class TestPhase3Payload(Phase3Base):
    def test_every_phase_3_block_is_present(self):
        p = S.payload(self.SYM, force=True)
        for key in ("fair_value", "expected_return", "implied_expectations",
                    "structures", "entry", "plan", "dividends",
                    "scenario_probabilities"):
            self.assertIn(key, p)

    def test_the_raw_valuation_arrays_never_reach_the_browser(self):
        p = S.payload(self.SYM, force=True)
        self.assertNotIn("raw_values", p["valuation_history"])

    def test_an_unreadable_filer_still_gets_every_key(self):
        p = S.payload("NOSUCHTICKER")
        for key in ("fair_value", "expected_return", "implied_expectations",
                    "structures", "entry", "plan"):
            self.assertIn(key, p)
        self.assertEqual(p["entry"]["verdict"], "INSUFFICIENT DATA")

    def test_the_entry_verdict_is_one_of_the_named_answers(self):
        p = S.payload(self.SYM, force=True)
        self.assertIn(p["entry"]["verdict"], IO.ENTRY_VERDICTS)

    def test_every_wait_or_avoid_says_what_would_change_it(self):
        p = S.payload(self.SYM, force=True)
        if p["entry"]["verdict"] in ("WAIT", "AVOID", "TOSS UP"):
            self.assertTrue(p["entry"]["what_would_change"])


class TestPhase3Snapshot(Phase3Base):
    def test_the_daily_row_records_the_state_that_produced_the_answer(self):
        S.payload(self.SYM, force=True)
        row = S.load_history(self.SYM)[-1]
        for field in ("fair_value_bear", "fair_value_base", "fair_value_bull",
                      "fair_value_confidence", "credited_fair_value",
                      "buy_zone", "premium_to_buy_zone_pct",
                      "expected_cagr_weighted_pct", "expected_price_base",
                      "implied_fcf_growth_pct", "scenario_probabilities",
                      "preferred_structure", "comparison_toss_up",
                      "structure_returns_pct", "csp_strike", "leaps_strike",
                      "buy_write_call_strike", "entry_verdict", "entry_reason",
                      "entry_flip_trigger", "config_hash"):
            self.assertIn(field, row)

    def test_old_rows_are_never_rewritten(self):
        S.snapshot(self.SYM, force=True)          # a Phase 1 shaped row
        hist, _latest = S._paths(self.SYM)
        rows = S.load_history(self.SYM)
        rows[0]["date"] = "2020-01-01"
        rows[0].pop("buy_zone", None)
        hist.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        S._MEM.clear()
        S.payload(self.SYM, force=True)
        after = S.load_history(self.SYM)
        old = next(r for r in after if r["date"] == "2020-01-01")
        self.assertNotIn("buy_zone", old)         # untouched, not back-filled
        self.assertIn("buy_zone", after[-1])


class TestScanner(Phase3Base):
    def test_a_recorded_ticker_produces_a_compact_row(self):
        S.payload(self.SYM, force=True)
        out = S.scan([self.SYM], build_budget=0)
        self.assertEqual(out["n"], 1)
        row = out["rows"][0]
        self.assertEqual(row["status"], "recorded")
        for field in ("quality_label", "growth_label", "valuation_label",
                      "revisions_label", "value_trap_level", "fair_value_base",
                      "buy_zone", "premium_to_buy_zone_pct",
                      "preferred_structure", "entry_verdict"):
            self.assertIn(field, row)

    def test_there_is_no_summed_investment_score(self):
        S.payload(self.SYM, force=True)
        out = S.scan([self.SYM], build_budget=0)
        row = out["rows"][0]
        for key in row:
            self.assertNotIn("total_score", key)
            self.assertNotIn("investment_score", key)
        self.assertIn("no column here is a total", out["note"].lower())

    def test_an_unrecorded_ticker_says_so_rather_than_showing_blanks(self):
        out = S.scan(["NEVERSEEN"], build_budget=0)
        row = out["rows"][0]
        self.assertEqual(row["status"], "not recorded yet")
        self.assertIn("never been opened", row["reason"])
        self.assertEqual(out["n_missing"], 1)

    def test_the_background_build_budget_is_respected(self):
        out = S.scan(["AAA", "BBB", "CCC", "DDD", "EEE"], build_budget=2)
        self.assertLessEqual(len(out["building"]), 2)


class TestPhase3Config(Phase3Base):
    def test_every_phase_3_group_is_flattened(self):
        cfg, _h = S.config(refresh=True)
        for key in ("min_margin_of_safety", "confidence_credit",
                    "horizon_years", "growth_cap_pct",
                    "multiple_reversion_years", "reverse_dcf_years",
                    "equity_risk_premium_pct", "toss_up_margin_pct",
                    "csp_min_dte", "leaps_max_dte", "min_open_interest"):
            self.assertIn(key, cfg, key)
        for group in ("fair_value", "expected_return", "structures",
                      "contracts", "implied_expectations"):
            self.assertNotIn(group, cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
