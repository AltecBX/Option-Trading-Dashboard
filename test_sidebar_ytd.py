"""v5.19/v5.20 — year to date, one definition (ytd.py).

Jerry: "I want to put the YTD % underneath this" (the sidebar's P/E line),
then "Now make the YTD show on the watchlist chips too". Both read the live
price against last year's final close — the same anchor the watchlist
board's YTD column uses.
"""
import tempfile
import unittest
from datetime import datetime

import ytd


def bars(*pairs):
    return [{"date": d + "T12:00:00-04:00", "close": c} for d, c in pairs]


class TheYtdAnchor(unittest.TestCase):
    def setUp(self):
        # The module's clock is module-level state, and the class below
        # pins it. Start each of these from a known one, and hand it back
        # the same way, so neither class can decide the other's answers.
        ytd.configure(data_dir=None, bars_fn=None, now_fn=None)
        self.addCleanup(ytd.configure, data_dir=None, bars_fn=None, now_fn=None)

    def test_it_is_last_years_final_close_not_this_years_first(self):
        b = bars(("2025-12-30", 98.0), ("2025-12-31", 100.0),
                 ("2026-01-02", 104.0), ("2026-09-18", 177.46))
        self.assertEqual(100.0, ytd.base_close(b, year=2026))

    def test_bars_that_stop_short_of_last_year_give_nothing(self):
        b = bars(("2026-01-02", 104.0), ("2026-09-18", 177.46))
        self.assertIsNone(ytd.base_close(b, year=2026))

    def test_the_year_is_the_clocks_not_the_latest_bars(self):
        """Codex on #406: on January 1, before the first bar of the new
        year prints, the latest bar is dated last year. Its year would
        anchor two year-ends back and call all of last year YTD."""
        b = bars(("2024-12-31", 50.0), ("2025-06-02", 80.0), ("2025-12-31", 100.0))
        self.assertEqual(100.0, ytd.base_close(b, year=2026),
                         "January 1: last year's final close, YTD is flat")
        self.assertEqual(50.0, ytd.base_close(b, year=2025),
                         "December 31: still last year's anchor")

    def test_the_default_year_comes_from_the_modules_clock(self):
        """With no year given the anchor reads the clock the host wired in.

        Pinned, not `datetime.now()`: the first cut built its bars from the
        real year while the module answered from a clock a previous test
        had left pinned. Those agree on any ordinary day, which is why it
        passed here and went red in the suite's 400-days-forward run — the
        run that exists to catch exactly this."""
        ytd.configure(data_dir=None, bars_fn=None, now_fn=lambda: datetime(2031, 4, 2))
        b = bars(("2030-12-31", 100.0), ("2031-01-02", 104.0))
        self.assertEqual(100.0, ytd.base_close(b))
        # And with no clock wired at all, the machine's own year.
        ytd.configure(data_dir=None, bars_fn=None, now_fn=None)
        y = datetime.now().year
        self.assertEqual(100.0, ytd.base_close(
            bars((f"{y - 1}-12-31", 100.0), (f"{y}-01-02", 104.0))))

    def test_empty_and_broken_rows_give_nothing(self):
        self.assertIsNone(ytd.base_close([]))
        self.assertIsNone(ytd.base_close(None))
        self.assertIsNone(ytd.base_close([{"date": None, "close": 5}, {"date": "", "close": 6}]))

    def test_a_zero_close_is_not_a_base(self):
        b = bars(("2025-12-31", 0.0), ("2026-01-02", 104.0))
        self.assertIsNone(ytd.base_close(b, year=2026))


class TheChipsBases(unittest.TestCase):
    """v5.20: the same anchor for a handful of chips at once, cached so a
    sidebar full of chips does not cost a history fetch per poll."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.history = {
            "AAPL": bars(("2025-12-31", 200.0), ("2026-09-18", 250.0)),
            "PLTR": bars(("2025-12-31", 100.0), ("2026-09-18", 177.46)),
            # A symbol whose bars do not reach last year.
            "IPO": bars(("2026-03-02", 40.0), ("2026-09-18", 60.0)),
        }

        def bars_fn(sym, days):
            self.calls.append(sym)
            return self.history.get(sym)

        ytd.configure(data_dir=self.tmp.name, bars_fn=bars_fn,
                      now_fn=lambda: datetime(2026, 9, 18, 10, 30))
        # Leave the module as it was found: its state is global.
        self.addCleanup(ytd.configure, data_dir=None, bars_fn=None, now_fn=None)

    def test_it_answers_with_the_base_and_the_latest_close(self):
        out = ytd.bases(["AAPL", "PLTR"])
        self.assertEqual({"base": 200.0, "last": 250.0}, out["AAPL"])
        self.assertEqual({"base": 100.0, "last": 177.46}, out["PLTR"])

    def test_a_symbol_without_a_base_is_absent_rather_than_wrong(self):
        out = ytd.bases(["IPO", "AAPL"])
        self.assertNotIn("IPO", out)
        self.assertIn("AAPL", out)

    def test_the_second_ask_the_same_day_costs_no_fetch(self):
        ytd.bases(["AAPL", "PLTR"])
        self.assertEqual(["AAPL", "PLTR"], self.calls)
        again = ytd.bases(["AAPL", "PLTR"])
        self.assertEqual(["AAPL", "PLTR"], self.calls, "the cache did not hold")
        self.assertEqual(200.0, again["AAPL"]["base"])

    def test_a_symbol_with_no_base_is_not_asked_for_twice_today(self):
        ytd.bases(["IPO"])
        ytd.bases(["IPO"])
        self.assertEqual(["IPO"], self.calls, "a known miss was re-fetched")

    def test_a_failed_fetch_is_retried_rather_than_remembered(self):
        """A provider that answers with nothing is not the same as a symbol
        with no base: caching the failure would hide the chip until
        tomorrow."""
        ytd.bases(["NEW"])
        ytd.bases(["NEW"])
        self.assertEqual(["NEW", "NEW"], self.calls)
        self.history["NEW"] = bars(("2025-12-31", 10.0), ("2026-09-18", 12.0))
        self.assertEqual(10.0, ytd.bases(["NEW"])["NEW"]["base"])

    def test_a_new_day_refetches_so_the_latest_close_is_not_stale(self):
        ytd.bases(["AAPL"])
        ytd.configure(data_dir=self.tmp.name, bars_fn=lambda s, d: self.history.get(s),
                      now_fn=lambda: datetime(2026, 9, 19, 10, 30))
        self.history["AAPL"] = bars(("2025-12-31", 200.0), ("2026-09-19", 260.0))
        self.assertEqual(260.0, ytd.bases(["AAPL"])["AAPL"]["last"])

    def test_the_cache_survives_a_restart(self):
        ytd.bases(["AAPL"])
        calls = []
        ytd.configure(data_dir=self.tmp.name,
                      bars_fn=lambda s, d: calls.append(s) or self.history.get(s),
                      now_fn=lambda: datetime(2026, 9, 18, 15, 0))
        self.assertEqual(200.0, ytd.bases(["AAPL"])["AAPL"]["base"])
        self.assertEqual([], calls, "a restart re-fetched what was on disk")

    def test_it_asks_for_no_more_symbols_than_the_cap(self):
        ytd.bases([f"S{i}" for i in range(60)])
        self.assertEqual(ytd.MAX_SYMBOLS, len(self.calls))

    def test_duplicates_and_blanks_are_not_fetches(self):
        ytd.bases(["AAPL", "aapl", " ", None, "AAPL"])
        self.assertEqual(["AAPL"], self.calls)

    def test_nothing_raises_when_the_host_never_wired_it(self):
        ytd.configure(data_dir=None, bars_fn=None, now_fn=None)
        self.assertEqual({}, ytd.bases(["AAPL"]))

    def test_a_cold_sidebar_fetches_in_parallel_within_a_bound(self):
        """Codex on #407: ten symbols fetched one after another add up
        their provider latencies inside a foreground request, and a single
        slow name holds every chip blank for the sum. The fetches overlap
        now — bounded, so this never becomes a burst at the broker."""
        import threading
        import time
        live = 0
        peak = 0
        seen = threading.Lock()

        def slow(sym, days):
            nonlocal live, peak
            with seen:
                live += 1
                peak = max(peak, live)
            time.sleep(0.05)
            with seen:
                live -= 1
            return bars(("2025-12-31", 10.0), ("2026-09-18", 12.0))

        ytd.configure(data_dir=self.tmp.name, bars_fn=slow,
                      now_fn=lambda: datetime(2026, 9, 18, 10, 30))
        started = time.monotonic()
        out = ytd.bases([f"N{i}" for i in range(10)])
        elapsed = time.monotonic() - started
        self.assertEqual(10, len(out), "a parallel fetch lost symbols")
        self.assertGreater(peak, 1, "the fetches still ran one at a time")
        self.assertLessEqual(peak, ytd.FETCH_WORKERS,
                             f"{peak} fetches at once, above the {ytd.FETCH_WORKERS} bound")
        # Serial would be 10 x 50ms; the bound puts it near 100ms. The
        # ceiling is loose so a slow machine cannot make this flap.
        self.assertLess(elapsed, 0.4, f"ten fetches took {elapsed:.2f}s — they did not overlap")

    def test_each_symbol_reaches_the_cache_as_it_lands(self):
        """The cache used to be written only after the whole batch, so a
        second page load during a slow batch repeated all of it."""
        import threading
        held = threading.Event()

        def one_blocks(sym, days):
            if sym == "SLOW":
                # Long enough that a serial batch cannot reach FAST inside
                # the window below, whatever the machine is doing.
                held.wait(10.0)
            return bars(("2025-12-31", 10.0), ("2026-09-18", 12.0))

        ytd.configure(data_dir=self.tmp.name, bars_fn=one_blocks,
                      now_fn=lambda: datetime(2026, 9, 18, 10, 30))
        done = threading.Event()
        threading.Thread(target=lambda: (ytd.bases(["SLOW", "FAST"]), done.set()),
                         daemon=True).start()
        for _ in range(100):          # up to 1s, vs the slow symbol's 10
            with ytd._LOCK:
                if "FAST" in ytd._CACHE:
                    break
            threading.Event().wait(0.01)
        with ytd._LOCK:
            landed = "FAST" in ytd._CACHE
        held.set()
        done.wait(12.0)
        self.assertTrue(landed, "a finished symbol waited on the slow one to reach the cache")

    def test_a_provider_that_throws_is_not_an_outage(self):
        def boom(sym, days):
            self.calls.append(sym)
            if sym == "BAD":
                raise RuntimeError("provider down")
            return self.history.get(sym)

        ytd.configure(data_dir=self.tmp.name, bars_fn=boom,
                      now_fn=lambda: datetime(2026, 9, 18, 10, 30))
        out = ytd.bases(["BAD", "AAPL"])
        self.assertNotIn("BAD", out)
        self.assertEqual(200.0, out["AAPL"]["base"])


if __name__ == "__main__":
    unittest.main()
