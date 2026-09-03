"""One fetch per thing, however many ask for it (v4.75).

Three places in the build path asked the same source for the same data more
than once, and the broker's rate limit was what paid for it:

* `SchwabClient.get_price_history` cached by the caller's day count, while
  Schwab serves whole-year buckets. The weekly loader's 394 days and the
  daily loader's 580 days are the same two-year request; a dozen scan
  callers ask for 10, 30, 200, 400, 730, 900, 1400 days of one symbol.
  Each was its own call.
* `/api/ticker` had a ten-second fresh cache but nothing for the seconds a
  build takes, so a symbol switched away from and back to mid-build was
  built twice, concurrently.
* Both earnings loaders fetched yfinance's earnings_dates, in parallel,
  in every build.

Every test here fails against the code before the fix: it counts the calls
that reach the source and asserts the count is one.
"""
from __future__ import annotations

import threading
import time
import types
import unittest
from datetime import date, timedelta

import pandas as pd

import options_dashboard as od
import schwab_client


# ── helpers ─────────────────────────────────────────────────────────────────
def _client() -> schwab_client.SchwabClient:
    """A client with no token, no disk and no network — only the fields
    the history path touches."""
    c = schwab_client.SchwabClient.__new__(schwab_client.SchwabClient)
    c._lock = threading.RLock()
    c._cache = {}
    c._inflight = {}
    c._req_log = []
    c._serving_lock = threading.Lock()
    c._last_ok_at = 0.0
    c._consecutive_failures = 0
    return c


def _candles(n: int) -> dict:
    """`n` daily candles in Schwab's shape, closes 0..n-1 so any slice of
    the series identifies exactly which bars it holds."""
    base_ms = 1_600_000_000_000
    return {"candles": [
        {"datetime": base_ms + i * 86_400_000, "open": i, "high": i + 1,
         "low": i - 1, "close": float(i), "volume": 100 + i}
        for i in range(n)
    ]}


class _CountingGet:
    """Stands in for SchwabClient._get: records every call, answers after
    an optional delay so concurrent callers overlap."""

    def __init__(self, answer, delay: float = 0.0):
        self.answer = answer
        self.delay = delay
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, url, params):
        with self._lock:
            self.calls.append(dict(params))
        if self.delay:
            time.sleep(self.delay)
        return self.answer(params) if callable(self.answer) else self.answer


def _closes(bars) -> list[float]:
    return [b["close"] for b in bars]


# ── price history: one fetch per year-bucket ────────────────────────────────
class TestOneBucketServesEveryDayCount(unittest.TestCase):
    def setUp(self):
        self.c = _client()
        self.get = _CountingGet(_candles(700))
        self.c._get = self.get

    def test_the_weekly_and_daily_loaders_share_one_fetch(self):
        # 394 and 580 calendar days are both Schwab's two-year bucket.
        weekly = self.c.get_price_history("AAPL", days=394)
        daily = self.c.get_price_history("AAPL", days=580)
        self.assertEqual(len(self.get.calls), 1, self.get.calls)
        self.assertEqual(self.get.calls[0]["period"], 2)
        self.assertEqual(len(weekly), 394)
        self.assertEqual(len(daily), 580)
        # Both are tails of the same series, exactly as `out[-days:]` was.
        self.assertEqual(_closes(weekly), _closes(daily)[-394:])
        self.assertEqual(_closes(daily), [float(i) for i in range(120, 700)])

    def test_a_short_ask_is_sliced_from_a_wider_cached_bucket(self):
        self.c.get_price_history("AAPL", days=580)      # period 2 cached
        month = self.c.get_price_history("AAPL", days=30)  # period 1 wanted
        self.assertEqual(len(self.get.calls), 1, "a wider bucket answers a narrower ask")
        self.assertEqual(_closes(month), [float(i) for i in range(670, 700)])

    def test_a_wider_ask_still_reaches_the_broker(self):
        self.c.get_price_history("AAPL", days=394)      # period 2
        self.c.get_price_history("AAPL", days=900)      # period 3: not covered
        self.assertEqual([p["period"] for p in self.get.calls], [2, 3])

    def test_symbols_do_not_share_buckets(self):
        self.c.get_price_history("AAPL", days=30)
        self.c.get_price_history("MSFT", days=30)
        self.assertEqual(len(self.get.calls), 2)
        self.assertEqual([p["symbol"] for p in self.get.calls], ["AAPL", "MSFT"])

    def test_the_bucket_ladder_is_unchanged(self):
        ladder = {10: 1, 260: 1, 261: 2, 620: 2, 621: 3, 1000: 3, 1001: 5,
                  1750: 5, 1751: 10, 3560: 10, 3561: 15, 5400: 15, 5401: 20}
        for days, period in ladder.items():
            self.assertEqual(schwab_client.SchwabClient._hist_period(days), period, days)

    def test_no_answer_is_none_and_is_not_cached(self):
        self.c._get = _CountingGet(None)
        self.assertIsNone(self.c.get_price_history("AAPL", days=30))
        self.assertIsNone(self.c.get_price_history("AAPL", days=30))
        self.assertEqual(len(self.c._get.calls), 2, "a failure must not be pinned")

    def test_an_answer_with_no_candles_is_an_empty_list(self):
        self.c._get = _CountingGet({"candles": []})
        self.assertEqual(self.c.get_price_history("AAPL", days=30), [])


class TestConcurrentAsksShareOneFetch(unittest.TestCase):
    def _run(self, c, asks, workers=8):
        results = {}
        errors = []

        def go(i, days):
            try:
                results[i] = c.get_price_history("NVDA", days=days)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=go, args=(i, asks[i % len(asks)]))
                   for i in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertFalse(errors, errors)
        return results

    def test_eight_overlapping_asks_make_one_broker_call(self):
        c = _client()
        c._get = _CountingGet(_candles(300), delay=0.3)
        results = self._run(c, asks=[260, 10, 30, 200])
        self.assertEqual(len(c._get.calls), 1, c._get.calls)
        self.assertEqual(len(results), 8)
        for i, bars in results.items():
            days = [260, 10, 30, 200][i % 4]
            self.assertEqual(_closes(bars), [float(x) for x in range(300 - days, 300)], i)

    def test_a_failed_fetch_is_not_retried_by_those_who_waited_on_it(self):
        c = _client()
        c._get = _CountingGet(None, delay=0.3)
        results = self._run(c, asks=[260], workers=5)
        self.assertEqual(len(c._get.calls), 1, "waiters must not pile on after a failure")
        self.assertEqual(list(results.values()), [None] * 5)
        self.assertEqual(c._inflight, {}, "nothing left in flight")

    def test_the_flight_is_released_after_success(self):
        c = _client()
        c._get = _CountingGet(_candles(50))
        c.get_price_history("NVDA", days=10)
        self.assertEqual(c._inflight, {})


class TestTheCacheStaysBounded(unittest.TestCase):
    def test_expired_entries_are_swept_past_the_cap(self):
        c = _client()
        c._CACHE_MAX = 5
        for i in range(5):
            c._cache_set(f"old:{i}", i, ttl=-1)     # already expired
        c._cache_set("live:0", 0, ttl=60)            # 6th entry: over the cap
        self.assertEqual(set(c._cache), {"live:0"})

    def test_soonest_to_expire_goes_first_when_all_are_live(self):
        c = _client()
        c._CACHE_MAX = 3
        c._cache_set("a", 1, ttl=30)
        c._cache_set("b", 1, ttl=10)
        c._cache_set("c", 1, ttl=20)
        c._cache_set("d", 1, ttl=40)
        self.assertEqual(set(c._cache), {"a", "c", "d"})
        self.assertLessEqual(len(c._cache), 3)


# ── /api/ticker: one build shared by everyone waiting for it ────────────────
class TestOneBuildServesEveryRequestForIt(unittest.TestCase):
    KEY = ("TSLA", 52, False, None)

    def setUp(self):
        od._TICKER_FRESH.clear()
        od._TICKER_LAST_GOOD.clear()
        od._TICKER_INFLIGHT.clear()
        self.builds = 0
        self._lock = threading.Lock()

    def _slow_builder(self, delay=0.3, fail=False):
        def build():
            with self._lock:
                self.builds += 1
            time.sleep(delay)
            if fail:
                raise ValueError("No data for TSLA.")
            return {"symbol": "TSLA", "built": self.builds}
        return build

    def _concurrent(self, builder, n=6):
        out, errs = {}, {}

        def go(i):
            try:
                out[i] = od._ticker_payload(self.KEY, builder)
            except Exception as exc:  # noqa: BLE001
                errs[i] = exc

        ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=10)
        return out, errs

    def test_six_concurrent_requests_build_once(self):
        out, errs = self._concurrent(self._slow_builder())
        self.assertFalse(errs, errs)
        self.assertEqual(self.builds, 1)
        self.assertEqual(len(out), 6)
        self.assertEqual({id(v) for v in out.values()}, {id(next(iter(out.values())))},
                         "everyone gets the one built payload")
        self.assertEqual(od._TICKER_INFLIGHT, {})

    def test_a_failed_build_fails_every_request_that_waited_on_it(self):
        out, errs = self._concurrent(self._slow_builder(fail=True))
        self.assertEqual(self.builds, 1, "a failure is not retried by the waiters")
        self.assertEqual(out, {})
        self.assertEqual(len(errs), 6)
        self.assertTrue(all(str(e) == "No data for TSLA." for e in errs.values()), errs)
        self.assertEqual(od._TICKER_INFLIGHT, {})

    def test_a_fresh_payload_is_served_without_building(self):
        od._ticker_payload(self.KEY, self._slow_builder(delay=0))
        od._ticker_payload(self.KEY, self._slow_builder(delay=0))
        self.assertEqual(self.builds, 1)

    def test_a_stale_payload_is_rebuilt(self):
        od._ticker_payload(self.KEY, self._slow_builder(delay=0))
        od._TICKER_FRESH[self.KEY] = (time.time() - od._TICKER_TTL - 1, {"old": True})
        got = od._ticker_payload(self.KEY, self._slow_builder(delay=0))
        self.assertEqual(self.builds, 2)
        self.assertNotIn("old", got)

    def test_the_last_good_payload_survives_a_failed_rebuild(self):
        first = od._ticker_payload(self.KEY, self._slow_builder(delay=0))
        od._TICKER_FRESH.clear()
        with self.assertRaises(ValueError):
            od._ticker_payload(self.KEY, self._slow_builder(delay=0, fail=True))
        self.assertIs(od._TICKER_LAST_GOOD[self.KEY], first)


# ── earnings dates: fetched once, shared by both loaders ────────────────────
class _FakeTicker:
    def __init__(self, box):
        self.box = box

    @property
    def earnings_dates(self):
        with self.box["lock"]:
            self.box["reads"] += 1
        if self.box.get("delay"):
            time.sleep(self.box["delay"])
        if self.box.get("raise"):
            raise RuntimeError("yfinance: too many requests")
        return self.box["frame"]


class TestEarningsDatesAreFetchedOnce(unittest.TestCase):
    def setUp(self):
        od._EARN_CACHE.clear()
        od._EARN_INFLIGHT.clear()
        self._real_yf = od.yf
        soon = date.today() + timedelta(days=10)
        self.box = {"reads": 0, "lock": threading.Lock(), "delay": 0.0,
                    "frame": pd.DataFrame({"EPS Estimate": [1.0]},
                                          index=pd.to_datetime([soon.isoformat()]))}
        od.yf = types.SimpleNamespace(Ticker=lambda sym: _FakeTicker(self.box))

    def tearDown(self):
        od.yf = self._real_yf
        od._EARN_CACHE.clear()
        od._EARN_INFLIGHT.clear()

    def test_both_loaders_in_parallel_read_yfinance_once(self):
        self.box["delay"] = 0.25
        got = {}
        t1 = threading.Thread(target=lambda: got.setdefault("check", od.check_earnings("AMD")))
        t2 = threading.Thread(target=lambda: got.setdefault("hist", od.load_earnings_history("AMD", 52)))
        t1.start(); t2.start(); t1.join(10); t2.join(10)
        self.assertEqual(self.box["reads"], 1, "one fetch shared by both loaders")
        has, when = got["check"]
        self.assertFalse(has)                       # ten days out is past this week
        self.assertEqual(when, (date.today() + timedelta(days=10)).isoformat())
        self.assertEqual(got["hist"]["next"], when)

    def test_a_second_build_inside_the_hour_does_not_fetch(self):
        od.check_earnings("AMD")
        od.load_earnings_history("AMD", 52)
        od.check_earnings("AMD")
        self.assertEqual(self.box["reads"], 1)

    def test_a_failure_keeps_each_loaders_own_answer_and_is_not_cached(self):
        self.box["raise"] = True
        self.assertEqual(od.check_earnings("AMD"), (False, None))
        self.assertEqual(od.load_earnings_history("AMD", 52), {"past": [], "next": None})
        self.assertEqual(self.box["reads"], 2, "a failure must not be pinned")
        self.assertEqual(od._EARN_INFLIGHT, {})

    def test_an_empty_answer_is_kept_only_briefly(self):
        self.box["frame"] = pd.DataFrame()
        self.assertEqual(od.check_earnings("SPY"), (False, None))
        self.assertEqual(self.box["reads"], 1)
        od.check_earnings("SPY")
        self.assertEqual(self.box["reads"], 1, "an empty answer is shared inside one build")
        od._EARN_CACHE["SPY"] = (time.time() - od._EARN_EMPTY_TTL - 1, pd.DataFrame())
        od.check_earnings("SPY")
        self.assertEqual(self.box["reads"], 2, "but not for the hour a real answer gets")


if __name__ == "__main__":
    unittest.main()
