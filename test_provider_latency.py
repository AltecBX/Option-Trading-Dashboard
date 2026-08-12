"""test_provider_latency.py — a slow data provider must degrade ONE card,
never the whole page.

WHY THIS EXISTS
The server speaks HTTP/1.1 keep-alive and a browser opens only about six
connections per host. So request latency is not just a per-card annoyance: six
slow requests stall everything queued behind them. That is not theoretical —
it was measured while debugging the Simply Wall St panel, where /api/site_link
requests were issued and never answered because unrelated slow endpoints had
taken every connection. The site_link fix at the time was to stop calling the
slowest upstream (.info) on that path; this file guards the general property.

yfinance's .info is the slowest call in the app and sits on /api/ticker's
critical path. Two things must hold, and neither did before v3.86:

  1. COALESCING — concurrent callers wanting the same symbol share ONE
     upstream fetch. The old code released the cache lock before the network
     call, so N concurrent cold requests made N identical slow calls and each
     one held a connection.
  2. A DEADLINE — no caller waits indefinitely. The global socket timeout only
     rescues a DEAD connection; a slow-but-alive one could hold a request far
     longer. On timeout the caller gets {} (which every caller has always had
     to handle) and the fetch continues in the background, so the next request
     is instant rather than repeating the stall.
"""
from __future__ import annotations

import threading
import time
import unittest

import options_dashboard as od


class _SlowTicker:
    """Stand-in for yf.Ticker whose .info takes a controllable time."""

    calls = 0
    delay = 0.0
    lock = threading.Lock()

    def __init__(self, symbol):
        self.symbol = symbol
        with _SlowTicker.lock:
            _SlowTicker.calls += 1

    @property
    def info(self):
        time.sleep(_SlowTicker.delay)
        return {"shortName": f"{self.symbol} Inc", "currentPrice": 100.0,
                "dividendRate": 2.0}


class Base(unittest.TestCase):
    # A UNIQUE symbol per test. These tests deliberately leave slow fetches
    # running (that is the behaviour under test), so a shared symbol lets one
    # test's background thread write the cache during the next one. That race
    # is invisible at normal speed and showed up only under the time-travel
    # run — the tests must be independent, not merely usually-independent.
    _n = 0

    def setUp(self):
        Base._n += 1
        self.SYM = f"ZZLATENCY{Base._n}"
        self._real_yf = od.yf
        _SlowTicker.calls = 0
        _SlowTicker.delay = 0.0

        class _FakeYF:
            Ticker = _SlowTicker
        od.yf = _FakeYF
        self._clear()

    def tearDown(self):
        od.yf = self._real_yf
        self._clear()

    def _clear(self):
        with od._INFO_LOCK:
            od._INFO_CACHE.pop(self.SYM, None)
            od._INFO_INFLIGHT.pop(self.SYM, None)


class TestCoalescing(Base):
    def test_concurrent_cold_requests_make_one_upstream_call(self):
        _SlowTicker.delay = 0.30
        results, threads = [], []

        def go():
            results.append(od._ticker_info(self.SYM))

        for _ in range(8):
            threads.append(threading.Thread(target=go))
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        self.assertEqual(
            _SlowTicker.calls, 1,
            f"8 concurrent callers made {_SlowTicker.calls} upstream calls; each "
            "one occupies a browser connection, which is what stalls the page")
        self.assertEqual(len(results), 8)
        for r in results:
            self.assertEqual(r.get("shortName"), f"{self.SYM} Inc")


class TestDeadline(Base):
    def test_a_slow_provider_cannot_hold_a_request(self):
        _SlowTicker.delay = 5.0
        t0 = time.monotonic()
        got = od._ticker_info(self.SYM, max_wait=0.25)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.5,
                        f"waited {elapsed:.2f}s on a slow provider; the request "
                        "should give up and let the page finish")
        self.assertEqual(got, {}, "a timed-out lookup must return empty, not invent data")

    def test_the_background_fetch_still_warms_the_cache(self):
        """The deadline must not throw the work away — otherwise every request
        pays the same stall and the page never gets faster."""
        _SlowTicker.delay = 0.40
        self.assertEqual(od._ticker_info(self.SYM, max_wait=0.05), {})
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with od._INFO_LOCK:
                if self.SYM in od._INFO_CACHE:
                    break
            time.sleep(0.05)
        t0 = time.monotonic()
        warm = od._ticker_info(self.SYM)
        self.assertLess(time.monotonic() - t0, 0.2, "a warm lookup must not block")
        self.assertEqual(warm.get("shortName"), f"{self.SYM} Inc")
        self.assertEqual(_SlowTicker.calls, 1, "the retry must not refetch")

    def test_upstream_failure_is_not_cached_as_success(self):
        class _Broken(_SlowTicker):
            @property
            def info(self):
                raise RuntimeError("provider down")

        class _FakeYF:
            Ticker = _Broken
        od.yf = _FakeYF
        self.assertEqual(od._ticker_info(self.SYM, max_wait=2.0), {})
        with od._INFO_LOCK:
            self.assertNotIn(self.SYM, od._INFO_CACHE,
                             "a failed fetch must not be pinned for 12 hours")


class TestDividendLookupStaysNonBlocking(Base):
    """The greeks read dividend yield inside the option-chain hot path, so that
    lookup must never trigger the slow call at all — only read a warm cache."""

    def test_cold_dividend_lookup_does_no_network(self):
        _SlowTicker.delay = 5.0
        t0 = time.monotonic()
        q = od._dividend_yield_cached(self.SYM)
        self.assertLess(time.monotonic() - t0, 0.1)
        self.assertEqual(q, 0.0)
        self.assertEqual(_SlowTicker.calls, 0,
                         "the hot path must not reach upstream for dividends")


if __name__ == "__main__":
    unittest.main()
