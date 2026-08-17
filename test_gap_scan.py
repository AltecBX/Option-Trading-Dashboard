"""Tests for gap_scan.py — event store build/merge, minute enrichment,
PM-only qualifier discovery, the live funnel, staleness gating, hysteresis
across scans, the walk-forward grid, and journal/replay records. A
FakeSchwab serves deterministic synthetic quotes and minute days, so every
path runs offline.
"""

import json
import tempfile
import unittest
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import gap_engine as ge
import gap_scan as gs

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 8, 14)          # a Friday


def ts_at(day, h, m):
    return int(datetime.combine(day, dtime(h, m), tzinfo=ET).timestamp() * 1000)


def daily_bars(gap_days, n=260, base=100.0, end=TODAY):
    """Daily bars ending the day BEFORE `end`. gap_days: {offset_from_end:
    (gap_pct, lo_off, hi_off, cl_off)} where offset 1 = most recent bar."""
    days = []
    d = end - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    bars, px = [], base
    for i, day in enumerate(days):
        off = len(days) - i
        gap, lo, hi, cl = gap_days.get(off, (0.0, -0.5, 0.5, 0.0))
        o = px * (1 + gap / 100.0)
        bars.append({"date": day.isoformat() + "T12:00:00-04:00",
                     "open": o, "high": o * (1 + hi / 100.0),
                     "low": o * (1 + lo / 100.0),
                     "close": o * (1 + cl / 100.0), "volume": 1_000_000})
        px = bars[-1]["close"]
    return bars


def minute_day(day, prev_close, pm_peak_pct, open_gap_pct, fade_to_pct):
    """Extended-hours day: PM climbs to pm_peak by 8:30, drifts to
    open_gap by 9:30, session fades linearly to fade_to_pct by 11:00."""
    bars = []
    # premarket 7:00-9:29
    for i in range(150):
        if i <= 90:
            lvl = pm_peak_pct * i / 90.0
        else:
            lvl = pm_peak_pct + (open_gap_pct - pm_peak_pct) * (i - 90) / 59.0
        px = prev_close * (1 + lvl / 100.0)
        bars.append({"ts": ts_at(day, 7, 0) + i * 60000, "open": px,
                     "high": px * 1.001, "low": px * 0.999, "close": px,
                     "volume": 4000})
    # regular session 9:30-11:00
    o = prev_close * (1 + open_gap_pct / 100.0)
    for i in range(91):
        lvl = open_gap_pct + (fade_to_pct - open_gap_pct) * i / 90.0
        px = prev_close * (1 + lvl / 100.0)
        bars.append({"ts": ts_at(day, 9, 30) + i * 60000, "open": o if i == 0 else px,
                     "high": px * 1.001, "low": px * 0.999, "close": px,
                     "volume": 20000})
    return bars


class FakeSchwab:
    def __init__(self, gap_days=None, minute_days=None, quotes=None):
        self.gap_days = gap_days or {}
        self.minute_days = minute_days or {}
        self.quotes = quotes or {}
        self.minute_calls = []

    def get_price_history(self, sym, days=260):
        return daily_bars(self.gap_days)

    def get_intraday_day(self, sym, date_iso, extended=False):
        self.minute_calls.append((sym, date_iso, extended))
        return self.minute_days.get(date_iso)

    def get_intraday(self, sym, minutes_back=480, extended=False):
        return self.minute_days.get(TODAY.isoformat())

    def get_quote(self, sym):
        return self.quotes.get(sym)

    def get_quotes(self, syms):
        return {s: self.quotes[s] for s in syms if s in self.quotes}

    def rate_usage(self):
        return 0


def quote(last, prev_close, age=5.0, bid=None, ask=None):
    return {"last": last, "close_prev": prev_close, "stale_seconds": age,
            "bid": bid if bid is not None else last * 0.999,
            "ask": ask if ask is not None else last * 1.001,
            "trade_time_ms": None, "session": "extended",
            "volume": 100000, "extended_volume": 400000}


def wire(tmp, sc, watchlist=None, catalyst=None):
    gs._STATE.update(rows=[], as_of=None, error=None, scanning=False)
    gs._HYST.clear()
    gs._PREV_ROWS.clear()
    gs._DAILY_CACHE.clear()
    gs.configure(
        schwab_getter=lambda: sc,
        watchlist_fn=lambda: {"starred": [], "all": watchlist or ["FAKE"]},
        universe_fn=lambda: [],
        board_fn=lambda: {"rows": []},
        daily_fn=lambda s, d: {"bars": sc.get_price_history(s, d), "source": "schwab"},
        actions_fn=lambda s: {"splits": set(), "dividends": {}},
        earn_hist_fn=lambda s: set(),
        catalyst_fn=(catalyst or (lambda s: {"kind": "UNTAGGED"})),
        sector_etf_fn=lambda s: "XLK",
        data_dir=tmp,
    )
    ge.config(refresh=True)


# a stock with a habit: 12 prior +6% gaps that all faded 2.5% by 11:00
def fady_history():
    gap_days = {}
    minute_days = {}
    d = TODAY - timedelta(days=2)
    placed = 0
    off_by_date = {}
    # find weekday offsets for events every ~9 trading days
    bars = daily_bars({}, end=TODAY)
    dates = [b["date"][:10] for b in bars]
    for k in range(12):
        idx = len(dates) - 5 - k * 9
        off = len(dates) - idx
        gap_days[off] = (6.0, -3.0, 0.8, -2.5)
        off_by_date[dates[idx]] = off
    bars2 = daily_bars(gap_days, end=TODAY)
    for dt_, off in off_by_date.items():
        i = [b["date"][:10] for b in bars2].index(dt_)
        pc = bars2[i - 1]["close"]
        minute_days[dt_] = minute_day(date.fromisoformat(dt_), pc,
                                      pm_peak_pct=7.5, open_gap_pct=6.0,
                                      fade_to_pct=3.0)
    return gap_days, minute_days


class TestStoreBuild(unittest.TestCase):
    def test_daily_events_extracted_and_persisted(self):
        gap_days, _ = fady_history()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, FakeSchwab(gap_days))
            store = gs.load_store("FAKE")
            store = gs.refresh_daily_events("FAKE", store)
            self.assertGreaterEqual(len(store["events"]), 12)
            gs._save_store("FAKE", store)
            p = Path(tmp) / "gap" / "events" / "FAKE.json"
            self.assertTrue(p.exists())
            again = gs.load_store("FAKE")
            self.assertEqual(len(again["events"]), len(store["events"]))
            self.assertEqual(again["daily_source"], "schwab")

    def test_minute_enrichment_marks_and_merges(self):
        gap_days, minute_days = fady_history()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = FakeSchwab(gap_days, minute_days)
            wire(tmp, sc)
            store = gs.refresh_daily_events("FAKE", gs.load_store("FAKE"))
            used = gs.enrich_minutes("FAKE", store, budget=5, today=TODAY)
            self.assertEqual(used, 5)
            enriched = [e for e in store["events"]
                        if (e.get("outcomes") or {}).get("minute")]
            self.assertGreaterEqual(len(enriched), 1)
            m = enriched[0]["outcomes"]["minute"]
            self.assertEqual(m["basis"], "MINUTE PATH")
            # the 6%->3% fade crosses -2% target without a +3% squeeze
            self.assertEqual(m["pairs"]["t2_s3"]["outcome"], "target")
            # PM block archived compactly
            self.assertTrue(enriched[0].get("pm", {}).get("checkpoints"))
            self.assertIn("PM", enriched[0]["qualified_by"])
            # resumable: second call skips scanned dates
            calls_before = len(sc.minute_calls)
            gs.enrich_minutes("FAKE", store, budget=50, today=TODAY)
            self.assertLess(len(sc.minute_calls) - calls_before, 50)

    def test_pm_only_qualifier_discovered(self):
        # CRITICAL (§6): +9% premarket at 8:30, opens +1% -> daily extraction
        # misses it, minute enrichment on the hinted day must create it
        d = None
        bars = daily_bars({}, end=TODAY)
        d = bars[-3]["date"][:10]          # a recent day
        off = len(bars) - [b["date"][:10] for b in bars].index(d)
        # daily: open +1%, but high +2.5% (the hint), close flat
        gap_days = {off: (1.0, -1.0, 1.5, 0.0)}
        bars2 = daily_bars(gap_days, end=TODAY)
        i = [b["date"][:10] for b in bars2].index(d)
        pc = bars2[i - 1]["close"]
        minute_days = {d: minute_day(date.fromisoformat(d), pc,
                                     pm_peak_pct=9.0, open_gap_pct=1.0,
                                     fade_to_pct=-0.5)}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = FakeSchwab(gap_days, minute_days)
            wire(tmp, sc)
            store = gs.refresh_daily_events("FAKE", gs.load_store("FAKE"))
            self.assertFalse(any(e["date"] == d for e in store["events"]))
            gs.enrich_minutes("FAKE", store, budget=10, today=TODAY)
            ev = [e for e in store["events"] if e["date"] == d]
            self.assertEqual(len(ev), 1, "PM fade day must exist as an event")
            e = ev[0]
            self.assertEqual(e["qualified_by"], ["PM"])
            self.assertEqual(e["direction"], "up")
            self.assertIsNotNone(e.get("pm_first_cross_ts"))
            # crossed +5% on the way up, i.e. before 8:30
            self.assertLess(e["pm_first_cross_ts"],
                            ts_at(date.fromisoformat(d), 8, 30))
            self.assertAlmostEqual(e["official_gap_pct"], 1.0, delta=0.2)

    def test_uncrossed_hint_day_not_added(self):
        bars = daily_bars({}, end=TODAY)
        d = bars[-3]["date"][:10]
        off = len(bars) - [b["date"][:10] for b in bars].index(d)
        gap_days = {off: (1.0, -1.0, 3.0, 0.5)}   # hinted by daily high
        bars2 = daily_bars(gap_days, end=TODAY)
        i = [b["date"][:10] for b in bars2].index(d)
        pc = bars2[i - 1]["close"]
        # premarket never crossed 5%
        minute_days = {d: minute_day(date.fromisoformat(d), pc,
                                     pm_peak_pct=2.0, open_gap_pct=1.0,
                                     fade_to_pct=3.0)}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = FakeSchwab(gap_days, minute_days)
            wire(tmp, sc)
            store = gs.refresh_daily_events("FAKE", gs.load_store("FAKE"))
            gs.enrich_minutes("FAKE", store, budget=10, today=TODAY)
            self.assertFalse(any(e["date"] == d for e in store["events"]))


class TestAnalyze(unittest.TestCase):
    def _setup(self, tmp, q=None, catalyst=None):
        gap_days, minute_days = fady_history()
        # live premarket today: +6.2% and fresh
        minute_days[TODAY.isoformat()] = minute_day(
            TODAY, 100.0, pm_peak_pct=6.6, open_gap_pct=6.2, fade_to_pct=6.2)[:150]
        sc = FakeSchwab(gap_days, minute_days,
                        quotes={"FAKE": q or quote(106.2, 100.0)})
        wire(tmp, sc, catalyst=catalyst)
        # pre-enrich the store so stats have minute paths
        store = gs.refresh_daily_events("FAKE", gs.load_store("FAKE"))
        gs.enrich_minutes("FAKE", store, budget=20, today=TODAY)
        return sc

    def test_full_row(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self._setup(tmp)
            now = datetime.combine(TODAY, dtime(8, 45), tzinfo=ET)
            d = gs.analyze_symbol("FAKE", quote(106.2, 100.0), {"XLK": 0.2},
                                  now=now)
            r = d["row"]
            self.assertTrue(r["data_ok"])
            self.assertAlmostEqual(r["pm_gap_pct"], 6.2, delta=0.05)
            self.assertEqual(r["direction"], "up")
            self.assertGreaterEqual(r["n"], 10)
            self.assertGreaterEqual(r["n_minute"], 10)
            self.assertIsNotNone(r["p_fav"])
            self.assertEqual(r["p_fav"]["n"], r["n"])
            self.assertIsNotNone(r["tbs_p"])
            self.assertIn(r["signal"], ("STRONG FADE", "FADE"))
            self.assertEqual(r["sector"]["label"], "ISOLATED")
            self.assertIn(r["cohort_quality"], ("HIGH", "MODERATE"))
            # detail carries the full evidence
            self.assertIn("stats", d)
            self.assertIn("worst_adv_date", d["stats"])

    def test_stale_quote_forces_no_data(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self._setup(tmp)
            now = datetime.combine(TODAY, dtime(8, 45), tzinfo=ET)
            d = gs.analyze_symbol("FAKE", quote(106.2, 100.0, age=900.0),
                                  now=now)
            self.assertEqual(d["row"]["signal"], "NO DATA")
            self.assertFalse(d["row"]["live_ok"])
            self.assertIn("old", d["row"]["live_why"])

    def test_wide_spread_forces_no_data(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self._setup(tmp)
            now = datetime.combine(TODAY, dtime(8, 45), tzinfo=ET)
            q = quote(106.2, 100.0, bid=100.0, ask=112.0)
            d = gs.analyze_symbol("FAKE", q, now=now)
            self.assertEqual(d["row"]["signal"], "NO DATA")

    def test_earnings_catalyst_separates_population(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self._setup(tmp, catalyst=lambda s: {"kind": "EARNINGS",
                                                 "label": "reports BMO"})
            now = datetime.combine(TODAY, dtime(8, 45), tzinfo=ET)
            d = gs.analyze_symbol("FAKE", quote(106.2, 100.0), now=now)
            r = d["row"]
            # history has zero EARNINGS events -> population empty -> NO DATA,
            # never borrowing the non-earnings fade stats
            self.assertEqual(r["population"], "EARNINGS")
            self.assertEqual(r["signal"], "NO DATA")
            self.assertEqual(r["n"], 0)

    def test_hysteresis_across_evals(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self._setup(tmp)
            now = datetime.combine(TODAY, dtime(8, 45), tzinfo=ET)
            d1 = gs.analyze_symbol("FAKE", quote(106.2, 100.0), now=now)
            first = d1["row"]["signal"]
            self.assertIn(first, ("STRONG FADE", "FADE"))
            # a single degraded eval (fresh but small gap -> weaker raw) holds
            d2 = gs.analyze_symbol("FAKE", quote(103.1, 100.0), now=now)
            if d2["row"]["signal_raw"] != first:
                self.assertEqual(d2["row"]["signal"], first)
                self.assertTrue(d2["row"]["signal_held"])

    def test_what_changed_emitted(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self._setup(tmp)
            now = datetime.combine(TODAY, dtime(8, 45), tzinfo=ET)
            gs.analyze_symbol("FAKE", quote(106.2, 100.0), now=now)
            d2 = gs.analyze_symbol("FAKE", quote(108.0, 100.0), now=now)
            self.assertIn("6.2% → +8", d2["row"]["what_changed"] or "")

    def test_journal_written_and_replayable_fields(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self._setup(tmp)
            now = datetime.combine(TODAY, dtime(8, 45), tzinfo=ET)
            gs.analyze_symbol("FAKE", quote(106.2, 100.0), now=now)
            p = Path(tmp) / "gap" / "decisions" / f"{TODAY.isoformat()}.jsonl"
            self.assertTrue(p.exists())
            rec = json.loads(p.read_text().strip().splitlines()[-1])
            for k in ("symbol", "pm_gap_pct", "signal", "signal_raw",
                      "config_hash", "p_fav", "n", "data_basis", "ts_et"):
                self.assertIn(k, rec)


class TestFunnel(unittest.TestCase):
    def test_stage1_filters_and_ranks(self):
        gap_days, _ = fady_history()
        quotes = {
            "BIG": quote(109.0, 100.0),          # +9
            "MID": quote(104.0, 100.0),          # +4
            "SMALL": quote(101.0, 100.0),        # +1 -> filtered
            "CHEAP": quote(2.4, 2.2),            # < min price
            "STALE": quote(108.0, 100.0, age=999.0),  # kept in stage1, gated later
            "SPY": quote(500.0, 499.0),
            "QQQ": quote(400.0, 398.0),
        }
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = FakeSchwab(gap_days, {}, quotes)
            wire(tmp, sc, watchlist=["BIG", "MID", "SMALL", "CHEAP", "STALE"])
            cands, etf_gaps = gs._stage1(gs._cfg())
            syms = [s for s, _q in cands]
            self.assertEqual(syms[0], "BIG")
            self.assertIn("MID", syms)
            self.assertIn("STALE", syms)
            self.assertNotIn("SMALL", syms)
            self.assertNotIn("CHEAP", syms)
            self.assertNotIn("SPY", syms)        # context, not a candidate
            self.assertAlmostEqual(etf_gaps["SPY"], 0.2, delta=0.01)

    def test_board_contract_and_persistence(self):
        gap_days, minute_days = fady_history()
        minute_days[TODAY.isoformat()] = minute_day(
            TODAY, 100.0, 6.6, 6.2, 6.2)[:150]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = FakeSchwab(gap_days, minute_days,
                            {"FAKE": quote(106.2, 100.0),
                             "SPY": quote(500.0, 499.5)})
            wire(tmp, sc)
            gs._scan_worker()      # synchronous for the test
            b = gs.get_board()
            self.assertEqual(b["count"], 1)
            for k in ("scanning", "scanned", "total", "last_scan",
                      "universe_size", "error"):
                self.assertIn(k, b["status"])
            self.assertIn("survivorship", b["note"])
            self.assertEqual(b["rows"][0]["symbol"], "FAKE")
            # persisted and restorable
            gs._STATE["rows"] = []
            gs._restore_board()
            self.assertEqual(len(gs._STATE["rows"]), 1)

    def test_no_net_guard(self):
        import os
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, FakeSchwab())
            os.environ["JERRY_NO_NET"] = "1"
            try:
                r = gs.trigger_scan()
                self.assertFalse(r["started"])
            finally:
                os.environ.pop("JERRY_NO_NET", None)


class TestLiveQuoteRefresh(unittest.TestCase):
    """The board is scanned every few minutes, but the price must not be
    frozen between scans (user-reported 8-17-2026: detail showed $11.70
    while the live quote was $11.58)."""

    def _board(self, tmp):
        gap_days, minute_days = fady_history()
        minute_days[TODAY.isoformat()] = minute_day(TODAY, 100.0, 6.6, 6.2, 6.2)[:150]
        sc = FakeSchwab(gap_days, minute_days,
                        {"FAKE": quote(106.2, 100.0), "SPY": quote(500.0, 499.5)})
        wire(tmp, sc)
        # pin the clock to the fixture's premarket so the scan sees today's
        # synthetic PM tape (the worker otherwise reads the real wall clock)
        real = gs._now_et
        gs._now_et = lambda: datetime.combine(TODAY, dtime(8, 45), tzinfo=ET)
        try:
            gs._scan_worker()
        finally:
            gs._now_et = real
        return sc

    def test_price_and_gap_update_without_rescan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = self._board(tmp)
            before = gs.get_board()["rows"][0]
            self.assertAlmostEqual(before["price"], 106.2, places=2)
            stats_before = (before["p_fav"], before["tbs_p"], before["n"])
            sc.quotes["FAKE"] = quote(103.4, 100.0)
            out = gs.refresh_quotes()
            self.assertTrue(out["ok"])
            r = gs.get_board()["rows"][0]
            self.assertAlmostEqual(r["price"], 103.4, places=2)
            self.assertAlmostEqual(r["pm_gap_pct"], 3.4, places=2)
            self.assertTrue(r["price_as_of"])
            # history-derived numbers are untouched — they cannot move tick
            # to tick, and recomputing them would cost a full rescan
            self.assertEqual((r["p_fav"], r["tbs_p"], r["n"]), stats_before)
            self.assertEqual(out["quotes"]["FAKE"]["price"], 103.4)

    def test_pm_high_extends_monotonically(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = self._board(tmp)
            hi0 = gs.get_board()["rows"][0]["pm_high"]
            self.assertIsNotNone(hi0, "scan must record the known PM high")
            # a new print above the known high extends it; distance -> 0
            sc.quotes["FAKE"] = quote(hi0 * 1.02, 100.0)
            gs.refresh_quotes()
            r = gs.get_board()["rows"][0]
            self.assertGreater(r["pm_high"], hi0)
            self.assertAlmostEqual(r["from_pm_high_pct"], 0.0, places=2)
            # a lower print never lowers the known high
            peak = r["pm_high"]
            sc.quotes["FAKE"] = quote(peak * 0.97, 100.0)
            gs.refresh_quotes()
            r2 = gs.get_board()["rows"][0]
            self.assertAlmostEqual(r2["pm_high"], peak, places=6)
            self.assertLess(r2["from_pm_high_pct"], -2.0)

    def test_stale_quote_escalates_immediately(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = self._board(tmp)
            self.assertNotEqual(gs.get_board()["rows"][0]["signal"], "NO DATA")
            sc.quotes["FAKE"] = quote(106.2, 100.0, age=1200.0)
            gs.refresh_quotes()
            r = gs.get_board()["rows"][0]
            self.assertEqual(r["signal"], "NO DATA")
            self.assertFalse(r["live_ok"])
            self.assertIn("freshness", r["signal_why"])

    def test_direction_flip_escalates(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = self._board(tmp)
            self.assertEqual(gs.get_board()["rows"][0]["direction"], "up")
            sc.quotes["FAKE"] = quote(96.0, 100.0)     # gap flipped negative
            gs.refresh_quotes()
            r = gs.get_board()["rows"][0]
            self.assertEqual(r["signal"], "NO DATA")
            self.assertIn("flipped direction", r["signal_why"])

    def test_offline_and_empty_board_are_safe(self):
        import os
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self._board(tmp)
            os.environ["JERRY_NO_NET"] = "1"
            try:
                self.assertFalse(gs.refresh_quotes()["ok"])
            finally:
                os.environ.pop("JERRY_NO_NET", None)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, FakeSchwab())
            out = gs.refresh_quotes()
            self.assertTrue(out["ok"])
            self.assertEqual(out["quotes"], {})


class TestBacktestGrid(unittest.TestCase):
    def test_walk_forward_grid(self):
        gap_days, minute_days = fady_history()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = FakeSchwab(gap_days, minute_days)
            wire(tmp, sc)
            store = gs.refresh_daily_events("FAKE", gs.load_store("FAKE"))
            gs.enrich_minutes("FAKE", store, budget=20, today=TODAY)
            out = gs.backtest_grid("FAKE")
            self.assertGreaterEqual(out["n_minute_events"], 10)
            self.assertTrue(out["grid"])
            g0 = out["grid"][0]
            for k in ("direction", "target_pct", "stop_pct", "n", "win_rate",
                      "expectancy_pct", "h1_pct", "h2_pct", "worst_pct",
                      "robust"):
                self.assertIn(k, g0)
            # every fade reaches 2% -> t2/s3 must be robust on this history
            t2 = [g for g in out["grid"]
                  if g["target_pct"] == 2.0 and g["stop_pct"] == 3.0]
            self.assertTrue(t2 and t2[0]["robust"])
            self.assertIn("win rate", out["note"])

    def test_thin_store_returns_empty_grid(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, FakeSchwab())
            out = gs.backtest_grid("EMPTY")
            self.assertEqual(out["grid"], [])
            self.assertEqual(out["n_minute_events"], 0)


class TestRecordToday(unittest.TestCase):
    def test_record_appends_todays_event(self):
        gap_days, minute_days = fady_history()
        minute_days[TODAY.isoformat()] = minute_day(
            TODAY, 100.0, pm_peak_pct=7.0, open_gap_pct=6.0, fade_to_pct=3.5)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = FakeSchwab(gap_days, minute_days,
                            {"FAKE": quote(106.0, 100.0),
                             "SPY": quote(500.0, 499.5)})
            wire(tmp, sc)
            gs._scan_worker()
            self.assertEqual(gs.get_board()["count"], 1)
            # force "now" to be a weekday by monkeypatching _now_et
            real_now = gs._now_et
            gs._now_et = lambda: datetime.combine(TODAY, dtime(16, 15), tzinfo=ET)
            try:
                out = gs.record_today()
            finally:
                gs._now_et = real_now
            self.assertEqual(out["recorded"], 1)
            store = gs.load_store("FAKE")
            ev = [e for e in store["events"] if e["date"] == TODAY.isoformat()]
            self.assertEqual(len(ev), 1)
            self.assertTrue(ev[0].get("pm", {}).get("checkpoints"))
            self.assertIn("minute", ev[0]["outcomes"])


class TestEventsPayload(unittest.TestCase):
    def test_analog_inspection(self):
        gap_days, minute_days = fady_history()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sc = FakeSchwab(gap_days, minute_days)
            wire(tmp, sc)
            store = gs.refresh_daily_events("FAKE", gs.load_store("FAKE"))
            gs.enrich_minutes("FAKE", store, budget=6, today=TODAY)
            out = gs.events_payload("FAKE")
            self.assertGreaterEqual(out["n"], 12)
            e0 = out["events"][0]
            for k in ("date", "direction", "official_gap_pct", "qualified_by",
                      "catalyst_kind", "basis", "fav_pct", "adv_pct"):
                self.assertIn(k, e0)
            bases = {e["basis"] for e in out["events"]}
            self.assertTrue(bases & {"MINUTE PATH", "DAILY ONLY"})


if __name__ == "__main__":
    unittest.main()
