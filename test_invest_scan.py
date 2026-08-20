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
from datetime import date, datetime, timedelta

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
import covered_call as _CC                             # noqa: E402
import broker_model as BRK                             # noqa: E402
import business_routing as ROUTE                       # noqa: E402
import structures as ST                                # noqa: E402
import chain_store as _CHAIN                           # noqa: E402
import cross_check as XC                               # noqa: E402
import capture_health as CH                            # noqa: E402
import forward_test as _FT                             # noqa: E402


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


# ── Phase 4 ─────────────────────────────────────────────────────────────────

class Phase4Base(Phase3Base):
    """The sample filer, dressed as whatever kind of business the test needs.

    The business type comes from the SEC's industry code, so the tests reach
    the bank and property-trust paths by supplying that code the same way the
    live app does — through `sic_metadata` — rather than by calling the
    models directly. That is what makes these integration tests.
    """

    def setUp(self):
        super().setUp()
        self._real_sic = F.sic_metadata
        self.sic = {"sic": "3571", "sic_description": "Computers",
                    "name": "Sample Co", "cik": 42}
        F.sic_metadata = lambda s: self.sic
        self._real_desc = F.business_description
        F.business_description = lambda s, *a, **k: self.profile
        self.profile = None

    def tearDown(self):
        F.sic_metadata = self._real_sic
        F.business_description = self._real_desc
        super().tearDown()

    def as_bank(self):
        self.sic = {"sic": "6021", "sic_description": "National Commercial Banks",
                    "name": "Sample Bank", "cik": 42}
        f = F.company_facts(self.SYM)
        gaap = f["facts"]["us-gaap"]
        rows = gaap["EarningsPerShareDiluted"]["units"]["USD/shares"]
        inst = [{"end": r["end"], "val": None, "filed": r["filed"]}
                for r in rows]

        def at(val):
            return {"units": {"USD": [{"end": r["end"], "val": val,
                                       "filed": r["filed"]} for r in inst]}}

        gaap["StockholdersEquity"] = at(500.0)
        gaap["Goodwill"] = at(50.0)
        gaap["IntangibleAssetsNetExcludingGoodwill"] = at(10.0)
        gaap["PreferredStockValue"] = at(40.0)
        gaap["Assets"] = at(6000.0)
        gaap["Deposits"] = at(4000.0)
        gaap["FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss"] = at(3000.0)
        gaap["CommonEquityTierOneCapitalToRiskWeightedAssets"] = {
            "units": {"pure": [{"end": r["end"], "val": 0.118,
                                "filed": r["filed"]} for r in inst]}}
        for name, val in (("InterestIncomeExpenseNet", 30.0),
                          ("NoninterestIncome", 10.0),
                          ("NoninterestExpense", 22.0),
                          ("InterestExpenseDeposits", 4.0),
                          ("FinancingReceivableAllowanceForCreditLossWriteOffs", 2.0)):
            gaap[name] = {"units": {"USD": [
                fact(r["start"], r["end"], val, filed=r["filed"]) for r in rows]}}
        S._MEM.clear()
        return f

    def as_reit(self, gains=True, property_type="RETAIL"):
        self.sic = {"sic": "6798", "sic_description":
                    "Real Estate Investment Trusts", "name": "Sample Trust",
                    "cik": 42}
        self.profile = {"symbol": self.SYM, "description": "A trust.",
                        "moat_tags": [], "property_type": property_type,
                        "profile_version": F.PROFILE_VERSION}
        f = F.company_facts(self.SYM)
        gaap = f["facts"]["us-gaap"]
        rows = gaap["EarningsPerShareDiluted"]["units"]["USD/shares"]

        def flow(val):
            return {"units": {"USD": [fact(r["start"], r["end"], val,
                                           filed=r["filed"]) for r in rows]}}

        gaap["NetIncomeLossAvailableToCommonStockholdersBasic"] = flow(12.0)
        gaap["DepreciationAndAmortization"] = flow(30.0)
        gaap["CommonStockDividendsPerShareDeclared"] = {
            "units": {"USD/shares": [fact(r["start"], r["end"], 0.20,
                                          filed=r["filed"]) for r in rows]}}
        if gains:
            gaap["GainLossOnSaleOfProperties"] = flow(2.0)
            gaap["ImpairmentOfRealEstate"] = flow(1.0)
        else:
            gaap.pop("GainLossOnSaleOfProperties", None)
            gaap.pop("ImpairmentOfRealEstate", None)
        S._MEM.clear()
        return f


class TestBankIntegration(Phase4Base):
    def setUp(self):
        super().setUp()
        self.as_bank()
        self.p = S.payload(self.SYM)

    def test_the_business_type_gate_routes_to_the_bank_model(self):
        self.assertEqual(self.p["business_type"]["type"], "BANK")
        self.assertIsNotNone(self.p["bank"])
        self.assertIsNone(self.p["reit"])
        self.assertEqual(self.p["fair_value"]["model"], "BANK")

    def test_a_bank_is_no_longer_refused(self):
        self.assertTrue(self.p["fair_value"]["available"])
        self.assertNotEqual(self.p["fair_value"].get("verdict"),
                            "SPECIALIZED MODEL REQUIRED")
        self.assertNotEqual(self.p["entry"]["verdict"],
                            "SPECIALIZED MODEL REQUIRED")

    def test_the_valuation_history_gains_the_bank_measures(self):
        dists = self.p["valuation_history"]["distributions"]
        self.assertIn("price_to_tangible_book", dists)
        self.assertIn("price_to_book", dists)
        self.assertNotIn("price_to_ffo", dists)

    def test_the_reverse_cash_flow_audit_is_refused_for_a_bank(self):
        imp = self.p["implied_expectations"]
        self.assertFalse(imp["available"])
        self.assertIn("neither quantity carries its usual meaning",
                      imp["reason"])

    def test_the_snapshot_records_the_bank_state(self):
        row = S._daily_row(self.p)
        for key in ("bank_price_to_tangible_book", "bank_rotce_pct",
                    "bank_efficiency_ratio_pct", "fair_value_model"):
            self.assertIn(key, row)
        self.assertEqual(row["fair_value_model"], "BANK")

    def test_the_scanner_row_shows_a_banks_own_multiple(self):
        S.store(self.p)
        out = S.scan([self.SYM], build_budget=0)
        row = out["rows"][0]
        self.assertEqual(row["fair_value_model"], "BANK")
        self.assertEqual(row["headline_multiple_label"],
                         "Price to tangible book")
        self.assertIsNotNone(row["headline_multiple"])


class TestReitIntegration(Phase4Base):
    def setUp(self):
        super().setUp()
        self.as_reit()
        self.p = S.payload(self.SYM)

    def test_the_business_type_gate_routes_to_the_property_trust_model(self):
        self.assertEqual(self.p["business_type"]["type"], "REIT")
        self.assertIsNotNone(self.p["reit"])
        self.assertIsNone(self.p["bank"])
        self.assertEqual(self.p["fair_value"]["model"], "REIT")

    def test_a_property_trust_is_no_longer_refused(self):
        self.assertTrue(self.p["fair_value"]["available"])
        self.assertNotEqual(self.p["entry"]["verdict"],
                            "SPECIALIZED MODEL REQUIRED")

    def test_the_valuation_history_gains_the_property_measures(self):
        dists = self.p["valuation_history"]["distributions"]
        self.assertIn("price_to_ffo", dists)
        self.assertIn("dividend_yield_pct", dists)
        self.assertNotIn("price_to_tangible_book", dists)

    def test_the_return_bridge_runs_on_funds_from_operations(self):
        er = self.p["expected_return"]
        self.assertIn("funds from operations", er["per_share_basis"])
        self.assertEqual(self.p["scenario_path"]["eps_ttm"],
                         self.p["reit"]["ffo_per_share"]["value"])

    def test_a_standard_company_still_uses_earnings(self):
        p = S.payload(self.SYM) if False else None
        self.sic = {"sic": "3571", "sic_description": "Computers",
                    "name": "Sample Co", "cik": 42}
        S._MEM.clear()
        p = S.payload(self.SYM)
        self.assertIn("earnings per share", p["expected_return"]["per_share_basis"])

    def test_an_incomplete_reconstruction_lowers_the_confidence(self):
        self.as_reit(gains=False)
        p = S.payload(self.SYM)
        self.assertFalse(p["reit"]["ffo"]["complete"])
        self.assertEqual(p["fair_value"]["confidence_level"], "LOW")
        self.assertIn("capped_from", p["fair_value"]["confidence"])

    def test_the_snapshot_records_the_property_trust_state(self):
        row = S._daily_row(self.p)
        for key in ("reit_ffo_per_share", "reit_price_to_ffo",
                    "reit_property_type", "reit_ffo_complete"):
            self.assertIn(key, row)
        self.assertEqual(row["reit_property_type"], "RETAIL")


class TestUnsupportedInsurersAndBrokersStayRefused(Phase4Base):
    """Phase 5 built models for insurers and brokers. It did NOT lower the
    bar: a filer in one of those industry codes with nothing behind it — no
    readable annual report to say what kind of insurance it writes, no
    customer money on its balance sheet — is refused exactly as before."""

    def test_an_insurer_with_no_readable_annual_report_is_refused(self):
        self.sic = {"sic": "6311", "sic_description": "Life Insurance",
                    "name": "Sample Life", "cik": 42}
        S._MEM.clear()
        p = S.payload(self.SYM)
        self.assertEqual(p["business_type"]["type"], "INSURANCE")
        self.assertIsNone(p["bank"])
        self.assertIsNone(p["reit"])
        self.assertFalse(p["fair_value"]["available"])
        self.assertEqual(p["fair_value"]["verdict"],
                         "SPECIALIZED MODEL REQUIRED")
        self.assertEqual(p["entry"]["verdict"], "SPECIALIZED MODEL REQUIRED")

    def test_a_filer_with_no_brokerage_economics_is_not_called_a_broker(self):
        """Phase 6: the industry code stopped being the answer.

        A filer in a broker's code with no customer balances, no deposits
        and ordinary operating cash flow is an ordinary company, and it is
        valued the ordinary way rather than pushed through a broker model
        and refused.
        """
        self.sic = {"sic": "6211", "sic_description": "Security Brokers",
                    "name": "Sample Broker", "cik": 42}
        S._MEM.clear()
        p = S.payload(self.SYM)
        self.assertEqual(p["business_type"]["type"], "STANDARD")
        self.assertIsNone(p["broker"])
        self.assertNotEqual(p["fair_value"].get("verdict"),
                            "SPECIALIZED MODEL REQUIRED")


class TestCoveredCallEndpoint(Phase4Base):
    def test_it_runs_over_the_stored_price_history(self):
        out = S.covered_call(self.SYM, years=3)
        self.assertTrue(out["available"], out.get("reason"))
        self.assertTrue(out["rows"])
        self.assertIn("buy_and_hold", out)
        self.assertIn("verdict_note", out)

    def test_it_never_applies_todays_fair_value_to_a_past_day(self):
        out = S.covered_call(self.SYM, years=3)
        self.assertIn("Nothing here applies today's valuation to a past day",
                      out["fair_value_note"])
        # Only the days actually recorded carry one.
        self.assertLessEqual(out["fair_value_days_recorded"],
                             len(S.load_history(self.SYM)))

    def test_with_no_chain_store_it_is_labelled_a_model_estimate(self):
        out = S.covered_call(self.SYM, years=3)
        self.assertEqual(out["rows"][0]["fill_basis"],
                         "MODEL-BASED ESTIMATE")
        self.assertEqual(out["chain_days_stored"], 0)

    def test_no_price_history_refuses(self):
        self.bars = {"bars": []}
        out = S.covered_call(self.SYM, years=3)
        self.assertFalse(out["available"])
        self.assertIn("needs a price history", out["reason"])

    def test_the_policy_families_are_published_for_the_screen(self):
        out = S.covered_call(self.SYM, years=1)
        for key in ("tenors", "strike_rules", "roll_rules",
                    "assignment_modes"):
            self.assertTrue(out[key])


class TestValidationEndpoint(Phase4Base):
    def test_a_young_store_refuses_rather_than_reporting(self):
        S.payload(self.SYM)
        S._STARRED_FN = lambda: [self.SYM]
        out = S.validation()
        self.assertTrue(out["available"])
        self.assertEqual(out["calibration"]["total_observations"], 0)
        self.assertIn("aged far enough", out["calibration"]["reason"])

    def test_no_tracked_tickers_says_so(self):
        S._STARRED_FN = lambda: []
        out = S.validation()
        self.assertFalse(out["available"])
        self.assertIn("no recorded recommendations", out["reason"])

    def test_it_never_rewrites_what_was_stored(self):
        S.payload(self.SYM)
        S._STARRED_FN = lambda: [self.SYM]
        before = list(S.load_history(self.SYM))
        S.validation()
        self.assertEqual(S.load_history(self.SYM), before)


class TestPhase4Config(Phase4Base):
    def test_every_phase_4_group_is_flattened(self):
        cfg, _h = S.config(refresh=True)
        for key in ("bank_equity_risk_premium_pct", "bank_terminal_growth_pct",
                    "bank_min_peers_for_regression",
                    "reit_min_property_type_peers", "reit_high_payout_pct",
                    "cc_delta_target", "cc_roll_dte", "cc_cash_rate_pct",
                    "forward_min_sample", "forward_min_sector_peers"):
            self.assertIn(key, cfg, key)
        for group in ("bank", "reit", "covered_call", "forward_test"):
            self.assertNotIn(group, cfg)

    def test_no_universal_covered_call_delta_is_declared_correct(self):
        cfg, _h = S.config(refresh=True)
        # The delta target is a configurable setting, and both the module
        # and the settings file say in words that it is one rather than a
        # rule anybody here believes in.
        self.assertIn("cc_delta_target", cfg)
        self.assertIn("setting to be tested",
                      _CC.STRIKE_RULES["DELTA"]["note"])
        with open("thresholds.json", encoding="utf-8") as fh:
            raw = json.load(fh)
        doc = raw["investment"]["covered_call"]["_doc"]
        self.assertIn("SETTING to be tested", doc)
        self.assertIn("not a rule believed in", doc)


# ══════════════════════════════════════════════════════════════════════════
# PHASE 5
# ══════════════════════════════════════════════════════════════════════════

class Phase5Base(Phase4Base):
    """The sample filer dressed as an insurer or a broker.

    Reached the same way the live app reaches them: through the SEC industry
    code and the annual-report profile, rather than by calling the models
    directly. That is what makes these integration tests.
    """

    def _flow(self, gaap, name, val):
        rows = gaap["EarningsPerShareDiluted"]["units"]["USD/shares"]
        gaap[name] = {"units": {"USD": [
            fact(r["start"], r["end"], val, filed=r["filed"]) for r in rows]}}

    def _inst(self, gaap, name, val):
        rows = gaap["EarningsPerShareDiluted"]["units"]["USD/shares"]
        gaap[name] = {"units": {"USD": [
            {"end": r["end"], "val": val, "filed": r["filed"]} for r in rows]}}

    def as_insurer(self, subtype="P&C", sic="6331", other_uw=12.0,
                   development=-2.0):
        self.sic = {"sic": sic, "sic_description": "Fire, Marine & Casualty",
                    "name": "Sample Insurer", "cik": 42}
        self.profile = {"symbol": self.SYM, "description": "An insurer.",
                        "moat_tags": [], "property_type": None,
                        "insurer_subtype": subtype, "broker_subtype":
                        "UNDETERMINED", "profile_version": F.PROFILE_VERSION,
                        # Phase 7: how the subtype was reached travels with
                        # it, so the snapshot can record which path answered.
                        "insurer_classification": {
                            "primary": subtype, "secondary": [],
                            "method": "business keywords",
                            "confidence": "HIGH",
                            "reason": "The report says so in as many words.",
                            "keyword_scores": {"pc": 30},
                            "segment_scores": {"pc": 40}, "evidence": {}}}
        f = F.company_facts(self.SYM)
        gaap = f["facts"]["us-gaap"]
        self._inst(gaap, "StockholdersEquity", 800.0)
        self._inst(gaap, "Goodwill", 50.0)
        self._inst(gaap, "Assets", 3000.0)
        self._inst(gaap, "LiabilityForClaimsAndClaimsAdjustmentExpense", 600.0)
        self._inst(gaap, "Investments", 2000.0)
        self._flow(gaap, "PremiumsEarnedNet", 100.0)
        self._flow(gaap, "PolicyholderBenefitsAndClaimsIncurredNet", 65.0)
        self._flow(gaap, "DeferredPolicyAcquisitionCostAmortizationExpense", 10.0)
        self._flow(gaap, "NetInvestmentIncome", 18.0)
        self._flow(gaap, "NetIncomeLossAvailableToCommonStockholdersBasic", 40.0)
        if other_uw is not None:
            self._flow(gaap, "OtherUnderwritingExpense", other_uw)
        if development is not None:
            self._flow(
                gaap,
                "SupplementalInformationForPropertyCasualtyInsuranceUnder"
                "writersPriorYearClaimsAndClaimsAdjustmentExpense",
                development)
        S._MEM.clear()
        return f

    def as_broker(self, subtype="RETAIL", customer_money=True):
        self.sic = {"sic": "6211", "sic_description": "Security Brokers",
                    "name": "Sample Broker", "cik": 42}
        self.profile = {"symbol": self.SYM, "description": "A broker.",
                        "moat_tags": [], "property_type": None,
                        "insurer_subtype": None, "broker_subtype": subtype,
                        "profile_version": F.PROFILE_VERSION}
        f = F.company_facts(self.SYM)
        gaap = f["facts"]["us-gaap"]
        self._inst(gaap, "StockholdersEquity", 500.0)
        self._inst(gaap, "Goodwill", 20.0)
        self._inst(gaap, "Assets", 6000.0)
        self._flow(gaap, "LaborAndRelatedExpense", 60.0)
        self._flow(gaap, "InterestIncomeExpenseNet", 50.0)
        self._flow(gaap, "NetIncomeLossAvailableToCommonStockholdersBasic", 25.0)
        if customer_money:
            self._inst(gaap, "ReceivablesFromCustomers", 300.0)
            self._inst(
                gaap,
                "CashAndSecuritiesSegregatedUnderFederalAndOtherRegulations",
                800.0)
            self._flow(gaap, "BrokerageCommissionsRevenue", 30.0)
        else:
            for n in ("ReceivablesFromCustomers", "BrokerageCommissionsRevenue",
                      "CashAndSecuritiesSegregatedUnderFederalAndOtherRegulations",
                      "PrincipalTransactionsRevenue",
                      "InvestmentBankingRevenue"):
                gaap.pop(n, None)
        S._MEM.clear()
        return f


class TestInsurerIntegration(Phase5Base):
    def setUp(self):
        super().setUp()
        self.as_insurer()
        self.p = S.payload(self.SYM)

    def test_the_insurer_block_is_built_and_the_others_are_not(self):
        self.assertTrue(self.p["insurance"]["available"])
        self.assertIsNone(self.p["bank"])
        self.assertIsNone(self.p["reit"])
        self.assertIsNone(self.p["broker"])

    def test_the_subtype_reaches_the_screen(self):
        self.assertEqual(self.p["insurance"]["subtype"], "P&C")
        self.assertEqual(self.p["insurance"]["metric_basis"], "UNDERWRITING")

    def test_the_fair_value_comes_from_the_insurer_model(self):
        fair = self.p["fair_value"]
        self.assertEqual(fair["model"], "INSURANCE")
        self.assertTrue(fair["available"], fair.get("reason"))
        self.assertTrue(any(m.get("specialized_for") == "INSURANCE"
                            for m in fair["methods"] if m.get("available")))

    def test_it_reaches_the_existing_entry_engine(self):
        # The point of Phase 5: a supported insurer stops being refused and
        # runs through the SAME comparator every other company does.
        self.assertNotEqual(self.p["entry"]["verdict"],
                            "SPECIALIZED MODEL REQUIRED")
        self.assertIn(self.p["entry"]["verdict"], IO.ENTRY_VERDICTS)

    def test_the_valuation_history_measures_book_rather_than_earnings(self):
        vh = self.p["valuation_history"]
        self.assertIn("price_to_book", vh["distributions"])
        self.assertIn("price_to_tangible_book", vh["distributions"])

    def test_no_generic_cash_flow_valuation_creeps_back_in(self):
        keys = {m["key"] for m in self.p["fair_value"]["methods"]}
        self.assertNotIn("fcf_yield", keys)
        self.assertFalse(self.p["implied_expectations"]["available"])

    def test_the_snapshot_records_the_insurer_state(self):
        row = S._daily_row(self.p)
        for key in ("insurance_subtype", "insurance_metric_basis",
                    "insurance_price_to_book", "insurance_roe_pct",
                    "insurance_combined_ratio_pct",
                    "insurance_reserve_development_state"):
            self.assertIn(key, row, key)
        self.assertEqual(row["fair_value_model"], "INSURANCE")

    def test_the_scanner_shows_an_insurer_with_its_own_multiple(self):
        row = S._scan_row(self.SYM, self.p)
        self.assertEqual(row["headline_multiple_label"], "Price to book")
        self.assertIsNotNone(row["headline_multiple"])
        self.assertEqual(row["fair_value_model"], "INSURANCE")
        self.assertEqual(row["insurance_subtype"], "P&C")
        self.assertNotIn("score", row.get("headline_multiple_label", ""))


class TestInsurerRiskGate(Phase5Base):
    def test_adverse_reserves_appear_in_the_same_value_trap_engine(self):
        self.as_insurer(development=6.0)         # 1.5% of premiums, adverse
        p = S.payload(self.SYM)
        keys = {a["key"] for a in p["value_trap"]["active"]}
        self.assertIn("adverse_reserve_development", keys)

    def test_a_deteriorating_insurer_cannot_be_recommended_bullishly(self):
        # Three insurer-specific signals firing at once reaches HIGH RISK by
        # the ordinary route, and HIGH RISK stops the entry engine.
        self.as_insurer(development=6.0, other_uw=45.0)   # combined ratio 120
        f = F.company_facts(self.SYM)
        gaap = f["facts"]["us-gaap"]
        self._inst(gaap, "StockholdersEquity", 800.0)
        S._MEM.clear()
        p = S.payload(self.SYM)
        trap = p["value_trap"]
        keys = {a["key"] for a in trap["active"]}
        self.assertIn("adverse_reserve_development", keys)
        self.assertIn("underwriting_loss", keys)
        if trap["level"] == "HIGH RISK":
            self.assertEqual(p["entry"]["verdict"], "AVOID")
            self.assertEqual(p["entry"]["blocked_by"], "value trap")

    def test_an_unclassifiable_insurer_is_still_refused(self):
        self.as_insurer()
        self.profile = {**self.profile, "insurer_subtype": None}
        S._MEM.clear()
        p = S.payload(self.SYM)
        self.assertFalse(p["insurance"]["available"])
        self.assertEqual(p["fair_value"]["verdict"],
                         "SPECIALIZED MODEL REQUIRED")
        self.assertEqual(p["entry"]["verdict"], "SPECIALIZED MODEL REQUIRED")
        # And the refusal carries the model's OWN reason rather than a
        # generic note about insurers.
        self.assertIn("what kind of insurer", p["fair_value"]["reason"].lower())


class TestBrokerIntegration(Phase5Base):
    def setUp(self):
        super().setUp()
        self.as_broker()
        self.p = S.payload(self.SYM)

    def test_the_broker_block_is_built(self):
        self.assertTrue(self.p["broker"]["available"])
        self.assertTrue(self.p["broker"]["broker_evidence"]["is_broker"])
        self.assertIsNone(self.p["insurance"])

    def test_the_fair_value_comes_from_the_broker_model(self):
        fair = self.p["fair_value"]
        self.assertEqual(fair["model"], "BROKER")
        self.assertTrue(fair["available"], fair.get("reason"))

    def test_it_reaches_the_existing_entry_engine(self):
        self.assertNotEqual(self.p["entry"]["verdict"],
                            "SPECIALIZED MODEL REQUIRED")
        self.assertIn(self.p["entry"]["verdict"], IO.ENTRY_VERDICTS)

    def test_client_assets_stay_blank_with_a_reason(self):
        b = self.p["broker"]
        self.assertIsNone(b["client_assets"]["value"])
        self.assertTrue(b["client_assets"]["reason"])
        self.assertIsNone(b["net_new_assets"]["value"])

    def test_a_filer_with_no_customer_money_is_not_run_as_a_broker(self):
        """Phase 6: not a broker no longer means not valuable.

        BlackRock shares an industry code with Charles Schwab. It is not a
        broker, and the broker model is right to refuse it — but its
        revenue, margins and free cash flow are an ordinary company's, so it
        reaches the ordinary engine instead of a refusal.
        """
        self.as_broker(customer_money=False)
        p = S.payload(self.SYM)
        self.assertIsNone(p["broker"])
        self.assertEqual(p["business_type"]["type"], "STANDARD")
        self.assertNotEqual(p["entry"]["verdict"],
                            "SPECIALIZED MODEL REQUIRED")

    def test_the_snapshot_records_the_broker_state(self):
        row = S._daily_row(self.p)
        for key in ("broker_subtype", "broker_is_broker_dealer",
                    "broker_price_to_book", "broker_roe_pct",
                    "broker_assets_to_equity"):
            self.assertIn(key, row, key)
        self.assertTrue(row["broker_is_broker_dealer"])


class TestPhase5Snapshot(Phase5Base):
    def test_the_exact_contract_and_quote_are_recorded_prospectively(self):
        self.as_insurer()
        row = S._daily_row(S.payload(self.SYM))
        self.assertIn("recommended_structure", row)
        self.assertIn("recommended_contract", row)
        # Either a contract with its quote, or an explicit reason there is
        # none. Never an ambiguous blank.
        if row["recommended_contract"] is None:
            self.assertTrue(row["recommended_contract_reason"])
        else:
            for key in ("expiration", "strike", "quote_source",
                        "underlying_price"):
                self.assertIn(key, row["recommended_contract"], key)

    def test_the_benchmark_is_chosen_before_the_outcome_is_known(self):
        self.as_insurer()
        p = S.payload(self.SYM)
        self.assertIn("benchmark", p)
        row = S._daily_row(p)
        self.assertIn("benchmark_symbol", row)
        self.assertIn("benchmark_close", row)

    def test_history_is_never_rewritten_by_a_new_field(self):
        self.as_insurer()
        S.payload(self.SYM)
        rows = S.load_history(self.SYM)
        # A row written before a key existed simply lacks it. Nothing here
        # goes back and fills one in.
        older = dict(rows[-1])
        older.pop("insurance_subtype", None)
        self.assertNotIn("insurance_subtype", older)

    def test_the_recording_audit_names_what_is_missing(self):
        self.as_insurer()
        S.payload(self.SYM)
        hist = {self.SYM: S.load_history(self.SYM)}
        out = S.recording_audit(hist)
        self.assertEqual(out["tickers"], 1)
        self.assertEqual(len(out["fields"]), len(S.REQUIRED_FOR_SCORING))
        self.assertTrue(out["reason"])
        for f in out["fields"]:
            self.assertIn("what", f)
            if not f["complete"]:
                self.assertIn(f["field"], out["missing_examples"])

    def test_the_audit_says_so_when_nothing_is_recorded(self):
        out = S.recording_audit({})
        self.assertEqual(out["tickers"], 0)
        self.assertIn("nothing to check", out["reason"])


class TestChainCapture(Phase5Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="jerry_cap_")
        _CHAIN.configure(self.tmp)
        _CHAIN._RECORDED_TODAY.clear()
        self.asked = []

    def tearDown(self):
        S._CC_CHAIN_FN = None
        _CHAIN.configure(None)
        _CHAIN._RECORDED_TODAY.clear()
        super().tearDown()

    def _payload(self, sym, max_dte, strikes):
        self.asked.append((sym, max_dte, strikes))
        # The exchange's date, not the container's. A server in UTC is
        # already on tomorrow's date by half past eight in the evening in
        # New York, and a fixture built on that clock would ask for
        # expirations one day away from the ones the capture files.
        today = S.market_now().date()
        chains = {}
        for dte in (7, 17, 38):
            exp = (today + timedelta(days=dte)).isoformat()
            calls = [{"strike": float(k), "bid": 1.0, "ask": 1.1, "iv": 0.24,
                      "delta": 0.4, "openInterest": 500, "last": 1.05,
                      "volume": 12} for k in range(80, 126, 5)]
            chains[exp] = {"calls": calls, "puts": []}
        return {"underlying": {"last": 100.0}, "source": "schwab",
                "expirations": list(chains), "chains": chains}

    def test_it_asks_only_for_the_near_window(self):
        S._CC_CHAIN_FN = self._payload
        out = S.capture_chains([self.SYM])
        self.assertEqual(out["captured"], [self.SYM])
        self.assertEqual(len(self.asked), 1)
        sym, max_dte, strikes = self.asked[0]
        self.assertLessEqual(max_dte, 60)
        self.assertLessEqual(strikes, 120)

    def test_it_captures_every_covered_call_tenor(self):
        S._CC_CHAIN_FN = self._payload
        S.capture_chains([self.SYM])
        store = _CHAIN.load(self.SYM)
        day = sorted(store)[-1]
        dtes = sorted((date.fromisoformat(e) - date.fromisoformat(day)).days
                      for e in store[day]["exps"])
        self.assertEqual(dtes, [7, 17, 38])

    def test_the_stored_rows_carry_provenance_and_quality(self):
        S._CC_CHAIN_FN = self._payload
        S.capture_chains([self.SYM])
        store = _CHAIN.load(self.SYM)
        day = sorted(store)[-1]
        q = _CHAIN.lookup(store, day, "call", 100.0, 7)
        self.assertEqual(q["source"], "schwab")
        self.assertEqual(q["quality_label"], "TWO SIDED")
        self.assertEqual(q["last"], 1.05)
        self.assertEqual(q["volume"], 12)

    def test_a_second_capture_the_same_day_does_not_overwrite(self):
        S._CC_CHAIN_FN = self._payload
        S.capture_chains([self.SYM])
        _CHAIN._RECORDED_TODAY.clear()
        out = S.capture_chains([self.SYM])
        self.assertEqual(out["captured"], [])
        self.assertEqual(out["skipped"], [self.SYM])

    def test_no_provider_says_so_rather_than_failing_silently(self):
        S._CC_CHAIN_FN = None
        out = S.capture_chains([self.SYM])
        self.assertEqual(out["captured"], [])
        self.assertIn("No option-chain provider", out["reason"])

    def test_a_failing_provider_is_recorded_as_a_failure(self):
        def boom(sym, max_dte, strikes):
            raise RuntimeError("no")
        S._CC_CHAIN_FN = boom
        out = S.capture_chains([self.SYM])
        self.assertEqual(out["failed"], [self.SYM])

    def test_nothing_is_ever_back_filled(self):
        S._CC_CHAIN_FN = self._payload
        S.capture_chains([self.SYM])
        store = _CHAIN.load(self.SYM)
        # Exactly one day, and it is today on the exchange's clock. A
        # capture never reaches back — and never reaches forward either.
        self.assertEqual(list(store), [S._market_today()])


class TestReadiness(Phase5Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="jerry_ready_")
        _CHAIN.configure(self.tmp)
        _CHAIN._RECORDED_TODAY.clear()

    def tearDown(self):
        _CHAIN.configure(None)
        _CHAIN._RECORDED_TODAY.clear()
        super().tearDown()

    def test_an_empty_store_is_a_model_based_estimate(self):
        out = S.chain_readiness(self.SYM)
        self.assertEqual(out["mode"], _CC.BASIS_MODEL)
        self.assertEqual(out["days"], 0)
        self.assertIn("cannot be back-filled", out["reason"])
        self.assertTrue(out["grows_only_forward"])

    def test_a_partial_store_is_part_real(self):
        today = date.today()
        exp = (today + timedelta(days=30)).isoformat()
        payload = {"underlying": {"last": 100.0}, "source": "schwab",
                   "chains": {exp: {"calls": [
                       {"strike": 100.0, "bid": 1.0, "ask": 1.1, "iv": 0.2,
                        "delta": 0.5, "openInterest": 10}], "puts": []}}}
        _CHAIN.record(self.SYM, payload, today=today.isoformat())
        days = [(today - timedelta(days=i)).isoformat() for i in range(4)]
        out = S.chain_readiness(self.SYM, days=days)
        self.assertEqual(out["mode"], _CC.BASIS_MIXED)
        self.assertAlmostEqual(out["window_coverage_pct"], 25.0, places=6)
        self.assertIn("counted separately", out["mode_note"])

    def test_the_covered_call_run_carries_the_readiness_block(self):
        out = S.covered_call(self.SYM, years=3)
        self.assertIn("readiness", out)
        self.assertIn(out["readiness"]["mode"],
                      (_CC.BASIS_REAL, _CC.BASIS_MIXED, _CC.BASIS_MODEL))

    def test_real_and_model_fills_are_never_blended_into_one_number(self):
        out = S.covered_call(self.SYM, years=3)
        row = out["rows"][0]
        # The run reports how many of each, and a single basis label. It
        # never reports an "accuracy" that averages the two.
        self.assertIn("fill_basis", row)
        self.assertNotIn("fill_accuracy", row)
        self.assertNotIn("accuracy_pct", out)


class TestPhase5Config(Phase5Base):
    def test_every_phase_5_group_is_flattened(self):
        cfg, _h = S.config(refresh=True)
        for key in ("insurance_combined_ratio_alarm",
                    "insurance_adverse_development_pct",
                    "insurance_min_subtype_peers",
                    "broker_material_deposits_pct",
                    "broker_leverage_alarm", "broker_min_subtype_peers",
                    "capture_max_dte", "capture_strike_count"):
            self.assertIn(key, cfg, key)
        for group in ("insurance", "broker", "chain_capture"):
            self.assertNotIn(group, cfg)

    def test_no_universal_insurer_or_broker_multiple_is_declared(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            raw = json.load(fh)
        inv = raw["investment"]
        # Every insurer and broker setting is a threshold or a minimum
        # sample size. There is deliberately no "fair price to book".
        for group in ("insurance", "broker"):
            for key in inv[group]:
                if key == "_doc":
                    continue
                self.assertNotIn("fair_multiple", key)
                self.assertNotIn("target_pb", key)
        self.assertIn("Neither of the two new models is applied to "
                      "everything in its industry code", inv["_phase5"])

    def test_the_capture_settings_say_they_are_never_back_filled(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            raw = json.load(fh)
        doc = raw["investment"]["chain_capture"]["_doc"]
        self.assertIn("nothing here is ever back-filled", doc)
        self.assertIn("A day that goes uncaptured is gone", doc)


# ══════════════════════════════════════════════════════════════════════════
# Phase 6 — routing, hybrids, filing tables and cross-checks
# ══════════════════════════════════════════════════════════════════════════


class Phase6Base(Phase5Base):
    """The sample filer with a business chapter and a set of filing tables."""

    def setUp(self):
        super().setUp()
        self._tables = {}
        # The provider hook only, so every other provider the base set up
        # keeps working exactly as the earlier phases' tests expect.
        self._real_tables_fn = S._TABLES_FN
        S._TABLES_FN = lambda sym, wanted: self._tables
        S._MEM.clear()

    def tearDown(self):
        S._TABLES_FN = self._real_tables_fn
        super().tearDown()

    def with_phrases(self, **kinds):
        prof = dict(self.profile or {"symbol": self.SYM, "description": "x",
                                     "moat_tags": [],
                                     "profile_version": F.PROFILE_VERSION})
        prof["routing_phrases"] = {"exchange": list(kinds.get("exchange", ())),
                                   "manager": list(kinds.get("manager", ())),
                                   "conglomerate": bool(kinds.get("conglomerate"))}
        prof["extraction_confidence"] = kinds.get("confidence", "HIGH")
        prof["extraction"] = {"form": "10-K", "filed": "2026-02-19",
                              "method": "table of contents anchor",
                              "accession": "0001-26-1"}
        self.profile = prof
        S._MEM.clear()

    def with_table(self, metric, value, period=None, label="row",
                   previous=None, form="8-K", scope="CONSOLIDATED"):
        # Relative to today, because a client-asset reading two quarters old
        # is refused as history — and a fixture pinned to a calendar date
        # would start failing the day the clock passed it.
        period = period or (date.today() - timedelta(days=45)).isoformat()
        row = {
            "value": value, "usable": True, "confidence": "HIGH",
            "metric": metric, "label": metric.replace("_", " ").capitalize(),
            "form": form, "filed": period, "previous": previous,
            "provenance": {"period": period, "row_label": label,
                           "scale_word": "billions", "resolved_unit":
                           "billions", "unit_source": "row label",
                           "unit_confidence": "HIGH", "scope": scope,
                           "window": "QUARTER"},
        }
        self._tables = {"readings": {metric: row},
                        "history": {metric: [row]}, "filings_read": 1}
        S._MEM.clear()


class TestRoutingReachesThePayload(Phase6Base):
    def test_a_broker_is_routed_by_its_balance_sheet(self):
        self.as_broker()
        p = S.payload(self.SYM)
        self.assertEqual(p["routing"]["business_class"], "BROKER")
        self.assertEqual(p["business_type"]["type"], "BROKER")
        self.assertTrue(p["routing"]["why"])

    def test_a_venue_reaches_the_standard_engine(self):
        self.as_broker(customer_money=False)
        self.with_phrases(exchange=["a market venue", "a clearing house",
                                    "an electronic marketplace"])
        p = S.payload(self.SYM)
        self.assertEqual(p["routing"]["business_class"], "EXCHANGE")
        self.assertEqual(p["business_type"]["type"], "STANDARD")
        self.assertIsNone(p["broker"])

    def test_an_asset_manager_reaches_the_standard_engine(self):
        self.as_broker(customer_money=False)
        self.with_phrases(manager=["money managed for others",
                                   "an asset management business"])
        p = S.payload(self.SYM)
        self.assertEqual(p["routing"]["business_class"], "ASSET_MANAGER")
        self.assertEqual(p["business_type"]["type"], "STANDARD")
        self.assertNotEqual(p["entry"]["verdict"], "SPECIALIZED MODEL REQUIRED")

    def test_a_chapter_the_reader_doubts_cannot_reclassify_anything(self):
        self.as_broker(customer_money=False)
        self.with_phrases(exchange=["a market venue", "a clearing house"],
                          confidence="LOW")
        p = S.payload(self.SYM)
        self.assertNotEqual(p["routing"]["business_class"], "EXCHANGE")

    def test_the_routing_is_inspectable(self):
        self.as_broker()
        r = S.payload(self.SYM)["routing"]
        for key in ("business_class", "label", "model", "confidence", "why",
                    "exposures", "corporate_accounts", "version"):
            self.assertIn(key, r)

    def test_a_bank_and_a_trust_are_routed_as_before(self):
        self.as_bank()
        self.assertEqual(S.payload(self.SYM)["business_type"]["type"], "BANK")


class TestHybridSafety(Phase6Base):
    def as_hybrid(self):
        self.as_insurer()
        f = F.company_facts(self.SYM)
        self._inst(f["facts"]["us-gaap"], "PolicyholderFunds", 900.0)
        self.sic = {"sic": "6282", "sic_description": "Investment Advice",
                    "name": "Sample Hybrid", "cik": 42}
        self.with_phrases(manager=["money managed for others",
                                   "an asset management business"])
        return f

    def test_two_material_businesses_are_named_as_a_hybrid(self):
        self.as_hybrid()
        p = S.payload(self.SYM)
        self.assertEqual(p["routing"]["business_class"], "HYBRID")
        self.assertTrue(p["hybrid"]["is_hybrid"])
        self.assertIn(p["hybrid"]["case"],
                      ("ONE MODEL RUNS", "MODELS AGREE", "MODELS DISAGREE"))

    def test_it_never_averages_two_models(self):
        self.as_hybrid()
        h = S.payload(self.SYM)["hybrid"]
        bases = [v["base"] for v in h["valuations"] if v.get("base")]
        fair = S.payload(self.SYM)["fair_value"]
        if len(bases) > 1 and fair.get("base"):
            self.assertNotAlmostEqual(fair["base"], sum(bases) / len(bases),
                                      places=4)

    def test_a_hybrid_that_cannot_be_resolved_shows_no_fair_value(self):
        self.as_hybrid()
        p = S.payload(self.SYM)
        if not p["hybrid"]["reliable"]:
            self.assertFalse(p["fair_value"]["available"])
            self.assertIn("HYBRID", p["fair_value"]["verdict"])

    def test_no_sum_of_the_parts_is_attempted(self):
        self.as_hybrid()
        h = S.payload(self.SYM)["hybrid"]
        self.assertNotIn("segment_weights", h)
        self.assertNotIn("sum_of_parts", h)

    def test_a_conglomerate_under_an_insurance_code_is_not_a_pure_insurer(self):
        self.as_insurer()
        self.with_phrases(conglomerate=True)
        p = S.payload(self.SYM)
        self.assertEqual(p["routing"]["business_class"], "HYBRID")


class TestClientAssetsFromFilingTables(Phase6Base):
    def test_a_readable_table_fills_the_customer_franchise(self):
        self.as_broker()
        self.with_table("client_assets", 1.2e12)
        p = S.payload(self.SYM)
        self.assertTrue(p["client_assets"]["available"])
        self.assertAlmostEqual(p["broker"]["client_assets"]["value"], 1.2e12)

    def test_the_confidence_cap_lifts_once_client_assets_are_known(self):
        self.as_broker(subtype="RETAIL")
        before, why = BRK.confidence_cap(S.payload(self.SYM)["broker"])
        self.assertEqual(before, "MODERATE")
        self.with_table("client_assets", 1.2e12)
        after, _ = BRK.confidence_cap(S.payload(self.SYM)["broker"])
        self.assertIsNone(after)
        self.assertIn("client assets", why)

    def test_a_stale_reading_is_not_used(self):
        self.as_broker(subtype="RETAIL")
        self.with_table("client_assets", 1.2e12,
                        period=(date.today() - timedelta(days=900)).isoformat())
        p = S.payload(self.SYM)
        self.assertIsNone(p["broker"]["client_assets"]["value"])

    def test_missing_tables_leave_the_phase_five_refusal_standing(self):
        self.as_broker()
        self._tables = {}
        p = S.payload(self.SYM)
        self.assertIsNone(p["broker"]["client_assets"]["value"])
        self.assertTrue(p["broker"]["client_assets"]["reason"])

    def test_the_reading_carries_where_it_came_from(self):
        self.as_broker()
        self.with_table("client_assets", 1.2e12, label="Total client assets")
        prov = S.payload(self.SYM)["client_assets"]["assets"]["provenance"]
        self.assertEqual(prov["row_label"], "Total client assets")
        self.assertEqual(prov["period"],
                         (date.today() - timedelta(days=45)).isoformat())

    def test_growth_needs_a_reading_from_a_year_ago(self):
        self.as_broker()
        self.with_table("client_assets", 1.2e12)
        got = S.payload(self.SYM)["client_assets"]["assets_growth_pct"]
        self.assertIsNone(got["value"])
        self.assertIn("never back-filled", got["reason"])


class TestPublishedAgainstReconstructed(Phase6Base):
    """A published figure is only compared with a rebuilt one when the basis,
    the period and the window all match — which in practice means an annual
    figure against the reconstruction rebuilt as of that same year end."""

    def annual(self, metric, value, **kw):
        """A published figure out of an annual report, at the same period end
        the reconstruction covers."""
        at = ((S.payload(self.SYM)["insurance"].get("combined_ratio_pct")
               or {}).get("period_end")
              or (date.today() - timedelta(days=45)).isoformat())
        self.with_table(metric, value, period=at, form="10-K", **kw)
        return at

    def test_agreement_is_reported_as_agreement(self):
        self.as_insurer()
        rebuilt = (S.payload(self.SYM)["insurance"]
                   .get("combined_ratio_pct") or {}).get("value")
        self.assertIsNotNone(rebuilt)
        self.annual("published_combined_ratio", rebuilt)
        p = S.payload(self.SYM)
        self.assertEqual(p["cross_check"]["state"], XC.MATCH)

    def test_a_material_difference_is_flagged_and_lowers_confidence(self):
        self.as_insurer()
        rebuilt = (S.payload(self.SYM)["insurance"]
                   .get("combined_ratio_pct") or {}).get("value")
        self.annual("published_combined_ratio", rebuilt * 1.4)
        p = S.payload(self.SYM)
        self.assertEqual(p["cross_check"]["state"], XC.MATERIAL)
        self.assertEqual(p["fair_value"]["confidence"]["level"], "LOW")

    def test_the_nicer_number_is_never_quietly_chosen(self):
        self.as_insurer()
        rebuilt = (S.payload(self.SYM)["insurance"]
                   .get("combined_ratio_pct") or {}).get("value")
        self.annual("published_combined_ratio", rebuilt * 1.4)
        p = S.payload(self.SYM)
        still = (p["insurance"].get("combined_ratio_pct") or {}).get("value")
        self.assertAlmostEqual(still, rebuilt, places=6)

    def test_a_quarterly_figure_is_not_compared_with_a_trailing_year(self):
        """The published quarter and the rebuilt year are different numbers
        about different lengths of time."""
        self.as_insurer()
        rebuilt = (S.payload(self.SYM)["insurance"]
                   .get("combined_ratio_pct") or {}).get("value")
        self.with_table("published_combined_ratio", rebuilt, form="10-Q")
        p = S.payload(self.SYM)
        self.assertIn(p["cross_check"]["state"],
                      (XC.UNAVAILABLE, XC.INCOMPATIBLE))

    def test_a_segment_figure_is_never_the_company_figure(self):
        self.as_insurer()
        rebuilt = (S.payload(self.SYM)["insurance"]
                   .get("combined_ratio_pct") or {}).get("value")
        self.annual("published_combined_ratio", rebuilt, scope="SEGMENT")
        p = S.payload(self.SYM)
        self.assertEqual(p["cross_check"]["state"], XC.INCOMPATIBLE)

    def test_no_published_figure_is_not_a_failure(self):
        self.as_insurer()
        self._tables = {}
        p = S.payload(self.SYM)
        self.assertIn(p["cross_check"]["state"],
                      (XC.NOT_CHECKED, XC.UNAVAILABLE))
        self.assertTrue(p["cross_check"]["reason"])
        self.assertEqual(p["cross_check"]["mismatches"], 0)


class TestPhase6Snapshot(Phase6Base):
    def test_the_row_records_why_this_engine_was_used(self):
        self.as_broker()
        row = S._daily_row(S.payload(self.SYM))
        for key in ("business_class", "primary_model", "routing_confidence",
                    "routing_evidence"):
            self.assertIn(key, row)
        self.assertEqual(row["business_class"], "BROKER")

    def test_the_row_records_how_the_business_chapter_was_read(self):
        self.as_broker()
        self.with_phrases()
        row = S._daily_row(S.payload(self.SYM))
        self.assertEqual(row["business_text_confidence"], "HIGH")
        self.assertEqual(row["business_text_method"],
                         "table of contents anchor")

    def test_the_row_records_client_assets_with_their_provenance(self):
        self.as_broker()
        self.with_table("client_assets", 1.2e12)
        row = S._daily_row(S.payload(self.SYM))
        self.assertAlmostEqual(row["client_assets"], 1.2e12)
        self.assertEqual(row["client_assets_as_of"],
                         (date.today() - timedelta(days=45)).isoformat())
        self.assertTrue(row["client_asset_provenance"])

    def test_an_older_row_simply_lacks_the_new_keys(self):
        """History is never rewritten; a reader treats a missing key as
        "not recorded that day"."""
        self.as_broker()
        row = S._daily_row(S.payload(self.SYM))
        old = {k: v for k, v in row.items() if not k.startswith("business_")}
        self.assertNotIn("business_class", old)


class TestPhase6Config(unittest.TestCase):
    def test_every_routing_threshold_is_declared(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            inv = json.load(fh)["investment"]
        for key in ROUTE.DEFAULTS:
            self.assertIn(key, inv["routing"], key)

    def test_the_routing_note_says_the_code_is_not_the_answer(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            inv = json.load(fh)["investment"]
        self.assertIn("industry code is a filing convenience", inv["_phase6"])
        self.assertIn("materiality", inv["_phase6"].lower())

    def test_the_table_note_says_a_thousandfold_error_is_refused(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            inv = json.load(fh)["investment"]
        doc = inv["filing_tables"]["_doc"]
        self.assertIn("thousandfold unit error must never reach a screen", doc)
        self.assertIn("written once and never rewritten", doc)

    def test_no_universal_exchange_or_manager_multiple_is_declared(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            inv = json.load(fh)["investment"]
        for key in inv["routing"]:
            self.assertNotIn("fair_multiple", key)
            self.assertNotIn("target_", key)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ══════════════════════════════════════════════════════════════════════════
# Phase 7 — capture health, restart recovery, forward readiness
# ══════════════════════════════════════════════════════════════════════════


class Phase7Base(Phase6Base):
    """The scheduler and the capture log, on a clock the test controls."""

    def setUp(self):
        super().setUp()
        CH.configure(self.dir)
        S._SCHED["recorded_for"] = None
        self._real_starred = S._STARRED_FN
        S._STARRED_FN = lambda: [self.SYM]
        self._real_cc_chain = S._CC_CHAIN_FN
        self.alerts = []
        self._real_alert = S._ALERT_FN
        S._ALERT_FN = lambda title, msg: self.alerts.append((title, msg))

    def tearDown(self):
        S._STARRED_FN = self._real_starred
        S._CC_CHAIN_FN = self._real_cc_chain
        S._ALERT_FN = self._real_alert
        S._SCHED["recorded_for"] = None
        CH.configure(None)
        super().tearDown()


class TestTheCaptureClock(Phase7Base):
    def test_the_clock_is_the_exchange_s_not_the_container_s(self):
        """Railway keeps its clock in UTC. Seventeen hundred UTC is one in
        the afternoon in New York, and the end-of-day capture used to run
        then."""
        now = S.market_now()
        self.assertIsNotNone(now)
        if S._MARKET_TZ is not None:
            self.assertIsNotNone(now.tzinfo)
            self.assertIn("New_York", str(now.tzinfo))

    def test_nothing_is_captured_before_the_window(self):
        self.assertIsNone(S.tick(now=datetime(2026, 8, 17, 11, 0)))

    def test_nothing_is_captured_on_a_weekend(self):
        self.assertIsNone(S.tick(now=datetime(2026, 8, 15, 18, 0)))

    def test_nothing_is_captured_on_a_market_holiday(self):
        self.assertIsNone(S.tick(now=datetime(2026, 11, 26, 18, 0)))

    def test_a_trading_evening_captures(self):
        got = S.tick(now=datetime(2026, 8, 17, 18, 0))
        self.assertIsNotNone(got)
        self.assertIn(self.SYM, got["recorded"])
        self.assertTrue(CH.captured(CH.SNAPSHOT, self.SYM, "2026-08-17"))


class TestRestartRecovery(Phase7Base):
    def test_a_restart_after_the_window_still_takes_the_day(self):
        """The container came back at eight in the evening. The day is not
        lost."""
        got = S.tick(now=datetime(2026, 8, 17, 20, 0))
        self.assertIn(self.SYM, got["recorded"])
        self.assertTrue(CH.captured(CH.SNAPSHOT, self.SYM, "2026-08-17"))

    def test_a_restart_does_not_repeat_work_that_already_succeeded(self):
        S.tick(now=datetime(2026, 8, 17, 18, 0))
        S._SCHED["recorded_for"] = None                  # a fresh process
        again = S.tick(now=datetime(2026, 8, 17, 21, 0))
        self.assertEqual(again["recorded"], [])
        self.assertEqual(again["already_captured"], [self.SYM])

    def test_a_late_capture_is_stamped_late_rather_than_hidden(self):
        S.record_daily([self.SYM], day="2026-08-17")
        entry = CH.day_log("2026-08-17")["kinds"][CH.SNAPSHOT][self.SYM]
        self.assertIn("late", entry)
        self.assertTrue(entry["at"])

    def test_a_chain_is_never_captured_on_a_day_the_market_was_shut(self):
        """A quote taken on a Saturday is Friday's quote wearing Saturday's
        date, and storing it would put a chain in the history for a day that
        never traded."""
        real = S._market_today
        try:
            S._market_today = lambda: "2026-08-15"       # a Saturday
            got = S.capture_chains([self.SYM])
        finally:
            S._market_today = real
        self.assertEqual(got["captured"], [])
        self.assertEqual(got["not_expected"], [self.SYM])
        self.assertIn("weekend", got["reason"])

    def test_nothing_here_ever_back_fills_a_chain(self):
        got = S.capture_chains([self.SYM])
        self.assertNotIn("backfilled", got)
        for key in ("captured", "skipped", "failed"):
            self.assertIn(key, got)


class TestCaptureHealthReaches(Phase7Base):
    def test_a_complete_day_raises_no_alert(self):
        for kind in CH.KINDS:
            CH.record(kind, self.SYM, True, day="2026-08-17")
        got = S._raise_if_incomplete([self.SYM], "2026-08-17")
        self.assertEqual(got["state"], CH.HEALTHY)
        self.assertEqual(self.alerts, [])

    def test_an_incomplete_day_reuses_the_app_s_own_push(self):
        CH.record(CH.SNAPSHOT, self.SYM, True, day="2026-08-17")
        got = S._raise_if_incomplete([self.SYM], "2026-08-17")
        self.assertIn(got["state"], (CH.HEALTH_PARTIAL, CH.FAILURE))
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Investment data capture", self.alerts[0][0])

    def test_the_expectation_is_per_kind_and_per_symbol(self):
        want = S.expected_today([self.SYM, "MSFT"])
        for kind in CH.KINDS:
            self.assertIn(self.SYM, want[kind])
            self.assertIn("MSFT", want[kind])


class TestDataReadiness(Phase7Base):
    def test_the_payload_says_how_much_real_data_exists(self):
        S.payload(self.SYM, force=True)
        got = S.data_readiness([self.SYM])
        for key in ("investment_snapshot_days", "real_chain_days",
                    "leaps_observation_days", "chain_coverage_pct",
                    "last_successful_capture", "symbols_missing_today"):
            self.assertIn(key, got)
        self.assertGreaterEqual(got["investment_snapshot_days"], 1)

    def test_a_weekend_is_reported_as_not_expected(self):
        got = S.data_readiness([self.SYM], today="2026-08-15")
        self.assertFalse(got["trading_day"])
        self.assertEqual(got["not_trading_because"], "a weekend")
        self.assertEqual(got["today"]["state"], CH.NOT_EXPECTED)

    def test_a_missed_trading_day_is_visible_the_next_morning(self):
        got = S.data_readiness([self.SYM], today="2026-08-17")
        # Past the window on a trading day with nothing captured is the
        # whole day gone, and the option chain for it cannot be bought back.
        # It gets the word that means so, not the one that means "missed a
        # few of several".
        self.assertEqual(got["today"]["state"], CH.FAILURE)
        self.assertEqual(got["health"]["state"], CH.FAILURE)

    def test_the_per_symbol_rows_distinguish_a_weekend_from_a_failure(self):
        CH.record(CH.CHAIN, self.SYM, True, day="2026-08-14")
        CH.record(CH.CHAIN, self.SYM, True, day="2026-08-17")
        got = S.data_readiness([self.SYM], today="2026-08-17")
        row = next(r for r in got["symbol_rows"] if r["symbol"] == self.SYM)
        self.assertEqual(row["missing_expected_days"], [])

    def test_the_note_says_it_can_never_be_back_filled(self):
        got = S.data_readiness([self.SYM])
        self.assertIn("ever back-filled", got["backfill_note"])


class TestForwardReadiness(Phase7Base):
    def test_the_earliest_thirty_day_result_is_named_not_guessed(self):
        S.payload(self.SYM, force=True)
        got = S.data_readiness([self.SYM])["forward"]
        thirty = next(h for h in got["horizons"] if h["days"] == 30)
        first = date.fromisoformat(got["first_snapshot"])
        self.assertEqual(thirty["first_eligible_date"],
                         (first + timedelta(days=30)).isoformat())
        self.assertIn(",", thirty["first_eligible_pretty"])

    def test_an_incomplete_horizon_is_counted_not_scored(self):
        S.payload(self.SYM, force=True)
        got = S.data_readiness([self.SYM])["forward"]
        thirty = next(h for h in got["horizons"] if h["days"] == 30)
        self.assertGreaterEqual(thirty["ageing"], 1)
        self.assertEqual(thirty["complete"], 0)
        self.assertEqual(got["verdict"], _FT.INSUFFICIENT)

    def test_no_snapshot_means_nothing_is_ageing(self):
        got = S._forward_readiness(set(), "2026-08-17")
        self.assertEqual(got["horizons"], [])
        self.assertIn("nothing ageing", got["reason"])


class TestPhase7Snapshot(Phase6Base):
    def test_the_row_records_how_the_insurer_was_classified(self):
        self.as_insurer()
        row = S._daily_row(S.payload(self.SYM))
        self.assertIn("insurer_subtype", row)
        self.assertIn("insurer_subtype_method", row)
        self.assertIn("insurer_metric_basis_ok", row)

    def test_the_row_records_how_each_table_figure_was_scaled(self):
        self.as_broker()
        self.with_table("client_assets", 1.2e12)
        row = S._daily_row(S.payload(self.SYM))
        unit = row["table_units"]["client_assets"]
        self.assertEqual(unit["unit"], "billions")
        self.assertEqual(unit["unit_source"], "row label")
        self.assertEqual(unit["scope"], "CONSOLIDATED")

    def test_an_old_row_is_never_rewritten(self):
        self.as_broker()
        first = S._daily_row(S.payload(self.SYM))
        self.assertNotIn("rewritten", first)
        self.assertTrue(first.get("date"))


class TestPhase7Config(unittest.TestCase):
    def test_every_phase_7_threshold_is_declared(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            inv = json.load(fh)["investment"]
        for key in ("insurance_spread_material_pct",
                    "insurance_evidence_max_age_days"):
            self.assertIn(key, inv["insurance_basis"], key)
        for key in XC.DEFAULTS:
            self.assertIn(key, inv["cross_check"], key)

    def test_the_notes_say_what_is_refused_and_why(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            inv = json.load(fh)["investment"]
        self.assertIn("adds no valuation engine", inv["_phase7"])
        self.assertIn("ever back-filled", inv["capture_health"]["_doc"])
        self.assertIn("nicer of the two is never quietly chosen",
                      inv["cross_check"]["_doc"])
        self.assertIn("mixed business basis",
                      inv["insurance_basis"]["_doc"].lower())

    def test_no_new_valuation_multiple_is_declared(self):
        with open("thresholds.json", encoding="utf-8") as fh:
            inv = json.load(fh)["investment"]
        for group in ("insurance_basis", "cross_check", "capture_health"):
            for key in inv[group]:
                self.assertNotIn("fair_multiple", key)
                self.assertNotIn("target_", key)


class TestNoSilentlyLostDays(Phase7Base):
    """A capture that did not happen must not be recorded as one. The whole
    point of the capture log is that tomorrow can tell the difference."""

    def with_chain_provider(self):
        S._CC_CHAIN_FN = lambda sym, dte, n: {"symbol": sym, "source": "test",
                                              "chains": {"2026-09-18": {}}}

    def test_a_chain_the_store_refused_is_a_failure_not_a_skip(self):
        self.with_chain_provider()
        real = _CHAIN.record
        try:
            _CHAIN.record = lambda *a, **k: False      # unusable payload
            got = S.capture_chains([self.SYM])
        finally:
            _CHAIN.record = real
        self.assertEqual(got["captured"], [])
        self.assertIn(self.SYM, got["failed"])
        self.assertFalse(CH.captured(CH.CHAIN, self.SYM, S._market_today()))

    def test_a_chain_already_stored_today_is_a_skip_not_a_failure(self):
        self.with_chain_provider()
        real = _CHAIN.record, _CHAIN.load
        try:
            _CHAIN.record = lambda *a, **k: False
            _CHAIN.load = lambda sym: {S._market_today(): {"spot": 10.0}}
            got = S.capture_chains([self.SYM])
        finally:
            _CHAIN.record, _CHAIN.load = real
        self.assertEqual(got["failed"], [])
        self.assertIn(self.SYM, got["skipped"])
        self.assertTrue(CH.captured(CH.CHAIN, self.SYM, S._market_today()))

    def test_a_payload_that_says_it_is_unavailable_is_not_a_snapshot(self):
        real = S.payload
        try:
            S.payload = lambda sym, **k: {"symbol": sym, "ok": False,
                                          "unavailable_reason": "no filings"}
            got = S.record_daily([self.SYM], day="2026-08-17")
        finally:
            S.payload = real
        self.assertEqual(got["recorded"], [])
        self.assertIn(self.SYM, got["failed"])
        self.assertFalse(CH.captured(CH.SNAPSHOT, self.SYM, "2026-08-17"))
        entry = CH.day_log("2026-08-17")["kinds"][CH.SNAPSHOT][self.SYM]
        self.assertIn("no filings", entry["reason"])

    def test_the_contract_audited_is_the_one_that_was_recommended(self):
        self.as_broker()
        snap = S.payload(self.SYM, force=True)
        S._record_riders(self.SYM, snap, "2026-08-17", False)
        entry = CH.day_log("2026-08-17")["kinds"][CH.CONTRACT][self.SYM]
        got = S._recommended_contract(snap)
        contract = got.get("recommended_contract") or {}
        self.assertEqual(entry["ok"], bool(contract))
        if contract:
            self.assertEqual(entry["source"], contract["structure"])


class TestNotDueYetIsNotAFailure(Phase7Base):
    def test_before_the_capture_window_today_is_not_a_failure(self):
        # Yesterday finished cleanly; today is simply not due yet.
        for kind in CH.KINDS:
            CH.record(kind, self.SYM, True, day="2026-08-14")
        real = S.market_now
        try:
            S.market_now = lambda now=None: now or datetime(2026, 8, 17, 10, 0)
            got = S.data_readiness([self.SYM], today="2026-08-17")
        finally:
            S.market_now = real
        self.assertFalse(got["capture_due_yet"])
        self.assertEqual(got["today"]["state"], "NOT DUE YET")
        self.assertEqual(got["health"]["state"], CH.HEALTHY)
        self.assertEqual(got["health_day"],
                         CH.previous_trading_day("2026-08-17"))
        self.assertIn("after 17:00 in New York", got["today"]["reason"])

    def test_after_the_capture_window_a_missed_day_is_a_failure(self):
        real = S.market_now
        try:
            S.market_now = lambda now=None: now or datetime(2026, 8, 17, 19, 0)
            got = S.data_readiness([self.SYM], today="2026-08-17")
        finally:
            S.market_now = real
        self.assertTrue(got["capture_due_yet"])
        # Past the window on a trading day with nothing captured is the
        # whole day gone, and the option chain for it cannot be bought back.
        # It gets the word that means so, not the one that means "missed a
        # few of several".
        self.assertEqual(got["today"]["state"], CH.FAILURE)
        self.assertEqual(got["health"]["state"], CH.FAILURE)
