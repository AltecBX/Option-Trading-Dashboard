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

    def test_verdict_is_attached_and_configuration_is_stamped(self):
        s = S.snapshot(self.SYM, force=True)
        self.assertIn(s["verdict"]["verdict"], S.engine.VERDICTS)
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
        self.assertIn("attractive_spread_pp", cfg)
        self.assertEqual(len(h), 16)

    def test_data_dir_override_wins_key_by_key(self):
        (S._DATA_DIR.parent / "thresholds.json").write_text(
            json.dumps({"investment": {"attractive_spread_pp": 9.5}}))
        S._CFG_CACHE["cfg"] = None
        cfg, _h = S.config(refresh=True)
        self.assertAlmostEqual(cfg["attractive_spread_pp"], 9.5)
        self.assertIn("watch_spread_pp", cfg)          # repo defaults survive
        S._CFG_CACHE["cfg"] = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
