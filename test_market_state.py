"""Tests for market_state.py — the live layer.

Two behaviours here are worth more than the rest: which trading session the
states belong to (the calendar date is the wrong answer on a weekend and
pre-market), and the rule that live candle extremes come from the regular
session only. Both are the kind of thing that looks fine in a screenshot
taken at 2 PM on a Tuesday and is wrong every weekend.
"""

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import market_state as MS
import strat_states as ST

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    ET = timezone.utc


def et(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def row(symbol, sector, *, cap=1e11, change=0.5, hi=105.0, lo=100.0,
        prev_hi=104.0, prev_lo=99.0, day="2026-08-21", strat=True):
    stored = {tf: {"cur_key": ST.period_key(day, tf), "cur_high": hi,
                   "cur_low": lo, "cur_days": 1, "cur_start": day,
                   "prev_key": "prior", "prev_high": prev_hi, "prev_low": prev_lo,
                   "state": ST.state_of(prev_hi, prev_lo, hi, lo)}
              for tf in ST.TIMEFRAMES}
    r = {"symbol": symbol, "sector": sector, "market_cap": cap,
         "change": change, "last": hi, "company": f"{symbol} Corporation"}
    if strat:
        r["strat"] = stored
    return r


class Harness:
    """A configured market_state with a fixed clock and a fake board."""

    def __init__(self, rows, now, quotes=None, scanning=False):
        self.dir = Path(tempfile.mkdtemp())
        self.rows = rows
        self.quotes = quotes or {}
        self.calls = {"quotes": 0, "board": 0}
        self._now = now
        self._saved = MS._now_et
        MS._now_et = lambda: self._now                      # noqa: SLF001
        MS.configure(schwab_getter=lambda: self,
                     board_getter=self._board,
                     data_dir=self.dir)
        MS._QUOTES = None                                   # noqa: SLF001
        MS.invalidate()
        self.scanning = scanning

    # ── the fake board and the fake broker ──────────────────────────────
    def _board(self, with_strat=True):
        self.calls["board"] += 1
        rows = self.rows if with_strat else [
            {k: v for k, v in r.items() if k != "strat"} for r in self.rows]
        return {"rows": rows,
                "status": {"last_scan": "2026-08-21T18:00:00Z",
                           "scanning": self.scanning}}

    def get_quotes(self, symbols):
        self.calls["quotes"] += 1
        return {s: self.quotes[s] for s in symbols if s in self.quotes}

    def get_quote(self, symbol):
        return self.quotes.get(symbol)

    def get_candles(self, *a, **k):
        return None

    def get_price_history(self, *a, **k):
        return None

    def close(self):
        MS._now_et = self._saved                            # noqa: SLF001
        MS._QUOTES = None                                   # noqa: SLF001
        MS.invalidate()
        shutil.rmtree(self.dir, ignore_errors=True)


class TestSessionPhases(unittest.TestCase):
    def test_the_regular_session(self):
        s = MS.market_status(et(2026, 8, 21, 11, 0))        # a Friday
        self.assertEqual(s["phase"], "open")
        self.assertTrue(s["is_open"])
        self.assertTrue(s["live_ok"])

    def test_pre_market_is_not_live(self):
        s = MS.market_status(et(2026, 8, 21, 7, 30))
        self.assertEqual(s["phase"], "pre")
        self.assertFalse(s["live_ok"])

    def test_after_hours_still_counts_todays_session(self):
        s = MS.market_status(et(2026, 8, 21, 17, 0))
        self.assertEqual(s["phase"], "post")
        self.assertTrue(s["live_ok"])
        self.assertEqual(s["session_date"], "2026-08-21")

    def test_a_weekend_is_closed_and_says_why(self):
        s = MS.market_status(et(2026, 8, 22, 12, 0))        # Saturday
        self.assertEqual(s["phase"], "closed")
        self.assertFalse(s["trading_day"])
        self.assertIn("weekend", s["reason"])

    def test_a_holiday_is_closed_and_named(self):
        s = MS.market_status(et(2026, 7, 3, 12, 0))         # Independence Day observed
        self.assertFalse(s["trading_day"])
        self.assertIn("Independence Day", s["reason"])


class TestWhichSessionTheStatesBelongTo(unittest.TestCase):
    """The calendar date is the wrong answer twice a week. Bucketing by it
    on a Sunday opens a daily candle in a Sunday bucket, finds it empty, and
    blanks every state on the dashboard all weekend."""

    def test_a_weekend_reads_the_last_trading_day(self):
        self.assertEqual(MS.session_date(et(2026, 8, 23, 12, 0)).isoformat(),
                         "2026-08-21")

    def test_pre_market_reads_the_previous_close(self):
        self.assertEqual(MS.session_date(et(2026, 8, 21, 7, 0)).isoformat(),
                         "2026-08-20")

    def test_once_open_it_reads_today(self):
        self.assertEqual(MS.session_date(et(2026, 8, 21, 10, 0)).isoformat(),
                         "2026-08-21")

    def test_a_holiday_walks_back_past_it(self):
        # Saturday July 4th 2026 is observed on Friday the 3rd, so the last
        # session before the weekend is Thursday the 2nd.
        self.assertEqual(MS.session_date(et(2026, 7, 4, 12, 0)).isoformat(),
                         "2026-07-02")

    def test_the_period_keys_follow_the_session_not_the_calendar(self):
        keys = MS.today_keys(et(2026, 8, 23, 12, 0))        # Sunday
        self.assertEqual(keys["D"], "2026-08-21")

    def test_states_survive_the_weekend(self):
        h = Harness([row("AAPL", "Technology")], et(2026, 8, 23, 12, 0))
        try:
            out = MS.context()
            self.assertEqual(out["breadth"]["D"]["n"], 1)
            self.assertEqual(out["breadth"]["D"]["counts"]["2U"], 1)
        finally:
            h.close()


class TestLiveExtremes(unittest.TestCase):
    def test_a_live_high_moves_the_state_during_the_session(self):
        r = row("AAPL", "Technology", hi=103.0, lo=100.0,
                prev_hi=104.0, prev_lo=99.0)               # inside, settled
        h = Harness([r], et(2026, 8, 21, 11, 0),
                    quotes={"AAPL": {"high": 106.0, "low": 100.0, "last": 105.5,
                                     "regular_last": 105.5, "change_pct": 1.4}})
        try:
            det = MS.sector_detail("XLK")
            self.assertEqual(det["rows"][0]["states"]["D"], "2U")
            self.assertTrue(det["rows"][0]["live"])
        finally:
            h.close()

    def test_a_pre_market_print_cannot_flip_a_state(self):
        """A hundred shares through yesterday's high at 7 AM is not a 2U,
        and Schwab's regular-session high is stale or zero pre-market
        anyway."""
        r = row("AAPL", "Technology", hi=103.0, lo=100.0,
                prev_hi=104.0, prev_lo=99.0)
        h = Harness([r], et(2026, 8, 21, 7, 30),
                    quotes={"AAPL": {"high": 999.0, "low": 100.0, "last": 950.0,
                                     "regular_last": 950.0}})
        try:
            det = MS.sector_detail("XLK")
            self.assertFalse(det["rows"][0]["live"])
            self.assertEqual(det["status"]["phase"], "pre")
            # Pre-market the current session has not begun, so the reading is
            # the settled one: yesterday's candle against the day before.
            self.assertIsNotNone(det["rows"][0]["states"]["D"])
        finally:
            h.close()

    def test_the_last_trade_extends_a_lagging_high(self):
        """A quote can arrive with the high a tick behind the print that just
        set it. Extending the range to include a price that traded can never
        shrink it, so the merge stays idempotent."""
        hi, lo = MS._live_extremes(                          # noqa: SLF001
            {"high": 100.0, "low": 98.0, "regular_last": 101.5}, True)
        self.assertEqual(hi, 101.5)
        self.assertEqual(lo, 98.0)

    def test_a_zero_or_inverted_quote_yields_nothing(self):
        for q in ({"high": 0, "low": 0}, {"high": 90, "low": 100}, {}):
            self.assertEqual(MS._live_extremes(q, True), (None, None))  # noqa: SLF001

    def test_live_extremes_are_refused_when_the_session_is_not_live(self):
        self.assertEqual(
            MS._live_extremes({"high": 100.0, "low": 98.0}, False),      # noqa: SLF001
            (None, None))


class TestSectorGrouping(unittest.TestCase):
    def test_the_aliases_all_land_on_the_right_etf(self):
        for name, etf in (("Technology", "XLK"), ("Information Technology", "XLK"),
                          ("Financial Services", "XLF"), ("Financials", "XLF"),
                          ("Healthcare", "XLV"), ("Health Care", "XLV"),
                          ("Consumer Cyclical", "XLY"), ("Consumer Defensive", "XLP"),
                          ("Basic Materials", "XLB"), ("Real Estate", "XLRE"),
                          ("Communication Services", "XLC")):
            self.assertEqual(MS.sector_etf(name), etf, name)

    def test_the_etf_ticker_itself_resolves(self):
        self.assertEqual(MS.sector_etf("xlk"), "XLK")

    def test_an_unknown_sector_is_none_not_a_twelfth_bucket(self):
        self.assertIsNone(MS.sector_etf("Cryptid Wrangling"))
        self.assertIsNone(MS.sector_etf(""))
        self.assertIsNone(MS.sector_etf(None))

    def test_there_are_exactly_eleven(self):
        self.assertEqual(len(MS.SPDR_SECTORS), 11)
        self.assertEqual(len({s["etf"] for s in MS.SPDR_SECTORS}), 11)

    def test_unclassified_names_are_counted_and_reported(self):
        h = Harness([row("AAPL", "Technology"), row("ZZZ", "Cryptid Wrangling")],
                    et(2026, 8, 21, 11, 0))
        try:
            s = MS.sectors()
            self.assertEqual(s["universe"], 2)
            self.assertEqual(s["classified"], 1)
            self.assertEqual(s["unclassified"], 1)
        finally:
            h.close()

    def test_unclassified_names_still_count_in_whole_market_breadth(self):
        """They are left out of the sector cards, not out of the market."""
        h = Harness([row("AAPL", "Technology"), row("ZZZ", "Cryptid Wrangling")],
                    et(2026, 8, 21, 11, 0))
        try:
            self.assertEqual(MS.context()["breadth"]["D"]["n"], 2)
        finally:
            h.close()

    def test_a_symbol_with_no_stored_states_is_skipped_and_reported(self):
        h = Harness([row("AAPL", "Technology"),
                     row("NEW", "Technology", strat=False)],
                    et(2026, 8, 21, 11, 0))
        try:
            s = MS.sectors()
            self.assertEqual(s["symbols_without_states"], 1)
            self.assertEqual(s["universe"], 1)
        finally:
            h.close()


class TestSectorCards(unittest.TestCase):
    def _eleven(self, now=None):
        rows = []
        for i, meta in enumerate(MS.SPDR_SECTORS):
            for j in range(4):
                up = (i + j) % 3 == 0
                rows.append(row(f"S{i}{j}", meta["name"],
                                cap=1e11 * (j + 1),
                                change=0.5 if up else -0.5,
                                hi=105.0 if up else 98.0,
                                lo=100.0 if up else 92.0))
        return Harness(rows, now or et(2026, 8, 21, 11, 0))

    def test_leaders_and_laggards_never_overlap(self):
        """With few ranked sectors, a naive top-3 / bottom-3 labels the same
        sector both a leader and a laggard on the same screen."""
        h = self._eleven()
        try:
            s = MS.sectors()
            self.assertFalse(set(s["leaders"]) & set(s["laggards"]))
        finally:
            h.close()

    def test_a_sector_with_no_directional_names_is_unranked_not_last(self):
        rows = [row("A1", "Technology", hi=103.0, lo=100.0,
                    prev_hi=104.0, prev_lo=99.0),       # inside
                row("B1", "Energy")]                     # 2U
        h = Harness(rows, et(2026, 8, 21, 11, 0))
        try:
            s = MS.sectors()
            tech = next(c for c in s["sectors"] if c["etf"] == "XLK")
            self.assertIsNone(tech["up_share_d"])
            self.assertNotIn("XLK", s["leaders"])
            self.assertNotIn("XLK", s["laggards"])
        finally:
            h.close()

    def test_a_sector_card_carries_every_timeframe(self):
        h = self._eleven()
        try:
            card = MS.sectors()["sectors"][0]
            for tf in ST.TIMEFRAMES:
                self.assertIn(tf, card["breadth"])
        finally:
            h.close()

    def test_the_median_change_is_not_the_average(self):
        rows = [row("A", "Energy", change=1.0), row("B", "Energy", change=1.0),
                row("C", "Energy", change=-100.0)]
        h = Harness(rows, et(2026, 8, 21, 11, 0))
        try:
            card = next(c for c in MS.sectors()["sectors"] if c["etf"] == "XLE")
            self.assertEqual(card["median_change_pct"], 1.0)
        finally:
            h.close()

    def test_an_unknown_sector_key_is_refused_by_name(self):
        h = Harness([row("A", "Energy")], et(2026, 8, 21, 11, 0))
        try:
            out = MS.sector_detail("Cryptid Wrangling")
            self.assertFalse(out["ok"])
            self.assertIn("eleven", out["error"])
        finally:
            h.close()


class TestTheIntradayBreadthSeries(unittest.TestCase):
    def test_it_records_nothing_while_the_market_is_shut(self):
        """A point at 3 AM is the previous close repeated. A flat line
        through the small hours reads as market data and is an idle poller."""
        h = Harness([row("A", "Energy")], et(2026, 8, 22, 3, 0))
        try:
            self.assertEqual(MS.context()["series"], [])
            self.assertFalse(list(h.dir.glob("strat_breadth_*.json")))
        finally:
            h.close()

    def test_it_records_a_point_while_open(self):
        h = Harness([row("A", "Energy")], et(2026, 8, 21, 11, 0))
        try:
            series = MS.context()["series"]
            self.assertEqual(len(series), 1)
            self.assertEqual(series[0]["counts"]["D"]["2U"], 1)
            self.assertTrue(list(h.dir.glob("strat_breadth_2026-08-21.json")))
        finally:
            h.close()

    def test_a_second_read_inside_the_sample_window_does_not_add_a_point(self):
        h = Harness([row("A", "Energy")], et(2026, 8, 21, 11, 0))
        try:
            MS.context()
            MS.invalidate()
            self.assertEqual(len(MS.context()["series"]), 1)
        finally:
            h.close()

    def test_the_series_survives_a_restart(self):
        h = Harness([row("A", "Energy")], et(2026, 8, 21, 11, 0))
        try:
            MS.context()
            MS.invalidate()
            MS._QUOTES = None                                # noqa: SLF001
            self.assertEqual(len(MS.context()["series"]), 1)
        finally:
            h.close()


class TestTheMarketMap(unittest.TestCase):
    def test_it_groups_by_sector_and_sizes_by_market_value(self):
        rows = [row("BIG", "Technology", cap=3e12, change=1.0),
                row("SMALL", "Technology", cap=1e9, change=-1.0)]
        h = Harness(rows, et(2026, 8, 21, 11, 0))
        try:
            m = MS.market_map()
            tech = next(s for s in m["sectors"] if s["etf"] == "XLK")
            self.assertEqual(tech["children"][0]["symbol"], "BIG")
            self.assertEqual(tech["market_cap"], 3e12 + 1e9)
        finally:
            h.close()

    def test_the_tail_is_trimmed_and_the_trim_is_reported(self):
        rows = [row(f"S{i}", "Energy", cap=1e9 * (i + 1)) for i in range(10)]
        h = Harness(rows, et(2026, 8, 21, 11, 0))
        try:
            sec = MS.market_map(limit_per_sector=4)["sectors"][0]
            self.assertEqual(sec["shown"], 4)
            self.assertEqual(sec["dropped"], 6)
            self.assertEqual(sec["constituents"], 10)
        finally:
            h.close()

    def test_a_name_without_a_market_value_is_left_out(self):
        rows = [row("A", "Energy", cap=1e11), row("B", "Energy", cap=None)]
        h = Harness(rows, et(2026, 8, 21, 11, 0))
        try:
            sec = MS.market_map()["sectors"][0]
            self.assertEqual([c["symbol"] for c in sec["children"]], ["A"])
        finally:
            h.close()


class TestCaching(unittest.TestCase):
    def test_every_panel_shares_one_quote_batch(self):
        """A dashboard polling four panels should cost one quote pass, not
        four."""
        h = Harness([row("A", "Energy")], et(2026, 8, 21, 11, 0),
                    quotes={"A": {"high": 106.0, "low": 100.0, "last": 105.0}})
        try:
            MS.sectors(); MS.context(); MS.market_map()
            self.assertEqual(h.calls["quotes"], 1)
        finally:
            h.close()

    def test_invalidate_forces_a_rebuild(self):
        h = Harness([row("A", "Energy")], et(2026, 8, 21, 11, 0))
        try:
            MS.sectors()
            before = h.calls["board"]
            MS.sectors()
            self.assertEqual(h.calls["board"], before)     # served from cache
            MS.invalidate()
            MS.sectors()
            self.assertGreater(h.calls["board"], before)
        finally:
            h.close()

    def test_a_board_getter_without_the_flag_still_works(self):
        """Older getters take no arguments. The live layer must not require a
        signature change in a module it does not own."""
        MS.configure(board_getter=lambda: {"rows": [], "status": {}})
        MS.invalidate()
        try:
            self.assertEqual(MS.sectors()["universe"], 0)
        finally:
            MS.configure(board_getter=lambda with_strat=True: {"rows": [], "status": {}})
            MS.invalidate()


class TestIntradayBucketing(unittest.TestCase):
    """Sixty-minute and four-hour candles are built from thirty-minute bars
    anchored to the 9:30 open. Bucketing by wall-clock hour instead puts the
    9:30-10:00 half hour in a 9:00 bucket that never existed, and every
    candle after it straddles two real ones."""

    def _bars(self, day, n, start_hour=9, start_min=30):
        out = []
        t = datetime(day.year, day.month, day.day, start_hour, start_min, tzinfo=ET)
        for i in range(n):
            ts = int(t.timestamp() * 1000) + i * 30 * 60_000
            out.append({"ts": ts, "open": 100 + i, "high": 101 + i,
                        "low": 99 + i, "close": 100 + i, "volume": 1})
        return out

    def test_sixty_minutes_is_two_half_hour_bars(self):
        from datetime import date as _date
        bars = self._bars(_date(2026, 8, 21), 4)
        out = MS._bucket_intraday(bars, 60)                 # noqa: SLF001
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["bars"], 2)

    def test_four_hours_is_eight_half_hour_bars(self):
        from datetime import date as _date
        bars = self._bars(_date(2026, 8, 21), 13)           # a full session
        out = MS._bucket_intraday(bars, 240)                # noqa: SLF001
        self.assertEqual(len(out), 2)                       # 9:30-13:30, 13:30-close
        self.assertEqual(out[0]["bars"], 8)
        self.assertEqual(out[1]["bars"], 5)

    def test_the_first_bucket_starts_at_the_open_not_on_the_hour(self):
        from datetime import date as _date
        bars = self._bars(_date(2026, 8, 21), 2)
        out = MS._bucket_intraday(bars, 60)                 # noqa: SLF001
        self.assertIn("09:30", out[0]["start"])

    def test_pre_market_bars_are_excluded(self):
        from datetime import date as _date
        early = self._bars(_date(2026, 8, 21), 2, start_hour=7)
        regular = self._bars(_date(2026, 8, 21), 2)
        out = MS._bucket_intraday(early + regular, 60)      # noqa: SLF001
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["bars"], 2)

    def test_a_bucket_takes_the_extremes_of_its_bars(self):
        from datetime import date as _date
        bars = self._bars(_date(2026, 8, 21), 2)
        out = MS._bucket_intraday(bars, 60)                 # noqa: SLF001
        self.assertEqual(out[0]["high"], max(b["high"] for b in bars))
        self.assertEqual(out[0]["low"], min(b["low"] for b in bars))

    def test_no_bars_is_an_empty_list_not_a_crash(self):
        self.assertEqual(MS._bucket_intraday([], 60), [])   # noqa: SLF001


class TestConfig(unittest.TestCase):
    def test_the_tunables_are_bounded(self):
        saved = (MS.QUOTE_TTL, MS.SAMPLE_SECONDS, MS.SERIES_KEEP_DAYS)
        try:
            MS.set_config({"quote_ttl_seconds": -5, "series_sample_seconds": 1,
                           "series_keep_days": 9999})
            self.assertGreaterEqual(MS.QUOTE_TTL, 1)
            self.assertGreaterEqual(MS.SAMPLE_SECONDS, 30)
            self.assertLessEqual(MS.SERIES_KEEP_DAYS, 60)
        finally:
            (MS.QUOTE_TTL, MS.SAMPLE_SECONDS, MS.SERIES_KEEP_DAYS) = saved

    def test_a_missing_key_leaves_the_default_alone(self):
        before = MS.QUOTE_TTL
        MS.set_config({})
        self.assertEqual(MS.QUOTE_TTL, before)


if __name__ == "__main__":
    unittest.main()
