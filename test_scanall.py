"""Tests for the v3.98 server additions: the Market Calendar
stale-while-revalidate cache (_mc_snapshot) and the sequential "Scan all"
orchestrator (scanall_start/_scanall_worker). Everything is stubbed — no
network, no real scanners."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JERRY_DATA_DIR", tempfile.mkdtemp(prefix="jerry_scanall_"))
os.environ.setdefault("JERRY_NO_NET", "1")

import options_dashboard as od  # noqa: E402


class _StubScanner:
    """Board-scanner double: trigger flips scanning on, a worker thread
    flips it off after `duration`; records trigger calls + timestamps."""

    def __init__(self, name, last_scan_age_sec=None, duration=0.15):
        self.name = name
        self.duration = duration
        self.calls = []
        self.scanning = False
        self.last_scan = (
            (datetime.now(timezone.utc) - timedelta(seconds=last_scan_age_sec)).isoformat()
            if last_scan_age_sec is not None else None)

    def trigger_scan(self, syms, *a, **k):
        self.calls.append(time.monotonic())
        self.scanning = True

        def work():
            time.sleep(self.duration)
            self.last_scan = datetime.now(timezone.utc).isoformat()
            self.scanning = False

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    def get_board(self):
        return {"status": {"scanning": self.scanning,
                           "last_scan": self.last_scan}}


class TestScanAll(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for attr in ("_movers", "_rangescan", "_trend", "_ivrank", "_analyst_board",
                     "_MOVERS_AVAILABLE", "_RANGESCAN_AVAILABLE", "_TREND_AVAILABLE",
                     "_IVRANK_AVAILABLE", "_ANALYST_BOARD_AVAILABLE"):
            self._saved[attr] = getattr(od, attr)
        with od._SCANALL_LOCK:
            od._SCANALL_STATE.update({"running": False, "queue": [],
                                      "started": None, "finished": None})
        os.environ.pop("JERRY_NO_NET", None)

    def tearDown(self):
        for attr, val in self._saved.items():
            setattr(od, attr, val)
        os.environ["JERRY_NO_NET"] = "1"
        for _ in range(100):          # let any worker drain
            with od._SCANALL_LOCK:
                if not od._SCANALL_STATE["running"]:
                    break
            time.sleep(0.05)

    def _install(self, movers=None, rangescan=None, trend=None, ivrank=None,
                 analyst=None):
        od._movers, od._MOVERS_AVAILABLE = movers, movers is not None
        od._rangescan, od._RANGESCAN_AVAILABLE = rangescan, rangescan is not None
        od._trend, od._TREND_AVAILABLE = trend, trend is not None
        od._ivrank, od._IVRANK_AVAILABLE = ivrank, ivrank is not None
        od._analyst_board, od._ANALYST_BOARD_AVAILABLE = analyst, analyst is not None

    def _wait_done(self, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            st = od.scanall_status()
            if not st["running"]:
                return st
            time.sleep(0.05)
        self.fail("scan_all did not finish")

    def test_no_net_refused(self):
        os.environ["JERRY_NO_NET"] = "1"
        r = od.scanall_start(["AAPL"])
        self.assertFalse(r["started"])
        self.assertIn("JERRY_NO_NET", r["reason"])

    def test_sequential_and_skip_fresh(self):
        stale = _StubScanner("movers", last_scan_age_sec=None)         # never ran
        fresh = _StubScanner("trend", last_scan_age_sec=60)            # 1m old, TTL 6h
        never = _StubScanner("ivrank", last_scan_age_sec=None)
        self._install(movers=stale, trend=fresh, ivrank=never)
        r = od.scanall_start(["AAPL", "MSFT"])
        self.assertTrue(r["started"])
        st = self._wait_done()
        by = {q["key"]: q for q in st["queue"]}
        self.assertEqual(by["movers"]["state"], "done")
        self.assertEqual(by["trend"]["state"], "skipped")     # fresh → no trigger
        self.assertEqual(by["ivrank"]["state"], "done")
        self.assertEqual(len(fresh.calls), 0)
        self.assertEqual(len(stale.calls), 1)
        self.assertEqual(len(never.calls), 1)
        # sequential: ivrank triggered only after movers' worker finished
        self.assertGreaterEqual(never.calls[0] - stale.calls[0],
                                stale.duration * 0.8)

    def test_force_ignores_freshness(self):
        fresh = _StubScanner("movers", last_scan_age_sec=5)
        self._install(movers=fresh)
        od.scanall_start(["AAPL"], force=True)
        st = self._wait_done()
        self.assertEqual(st["queue"][0]["state"], "done")
        self.assertEqual(len(fresh.calls), 1)

    def test_busy_guard(self):
        slow = _StubScanner("movers", duration=0.5)
        self._install(movers=slow)
        self.assertTrue(od.scanall_start(["AAPL"])["started"])
        r2 = od.scanall_start(["AAPL"])
        self.assertFalse(r2["started"])
        self.assertEqual(r2["reason"], "already running")
        self._wait_done()

    def test_scanner_error_does_not_abort_chain(self):
        class Boom(_StubScanner):
            def trigger_scan(self, syms, *a, **k):
                raise RuntimeError("provider down")
        boom = Boom("movers")
        ok = _StubScanner("trend")
        self._install(movers=boom, trend=ok)
        od.scanall_start(["AAPL"])
        st = self._wait_done()
        by = {q["key"]: q for q in st["queue"]}
        self.assertEqual(by["movers"]["state"], "error")
        self.assertEqual(by["trend"]["state"], "done")


class TestCalendarCache(unittest.TestCase):
    def setUp(self):
        self._builders = (od.build_watchlist_earnings, od.build_economic_calendar)
        self.calls = []
        with od._MC_LOCK:
            for k in ("earnings", "economic"):
                od._MC_STATE[k].update({"ts": 0.0, "days": None,
                                        "payload": None, "refreshing": False})
        try:
            od._MC_PATH.unlink()
        except FileNotFoundError:
            pass

        def fake_earnings(days):
            self.calls.append(("earnings", days))
            return {"as_of": "now", "days": days, "count": 1,
                    "entries": [{"symbol": "T"}]}

        def fake_econ(days):
            self.calls.append(("economic", days))
            return {"as_of": "now", "days": days, "count": 1,
                    "events": [{"event": "CPI"}]}

        od.build_watchlist_earnings = fake_earnings
        od.build_economic_calendar = fake_econ

    def tearDown(self):
        od.build_watchlist_earnings, od.build_economic_calendar = self._builders
        try:
            od._MC_PATH.unlink()
        except FileNotFoundError:
            pass

    def test_first_call_builds_then_serves_cache(self):
        r1 = od._mc_snapshot("earnings", 35)
        self.assertEqual(r1["cache_age_sec"], 0)
        self.assertEqual(len(self.calls), 1)
        r2 = od._mc_snapshot("earnings", 35)
        self.assertEqual(len(self.calls), 1)          # served from cache
        self.assertIn("cache_age_sec", r2)
        self.assertEqual(r2["entries"], [{"symbol": "T"}])

    def test_stale_serves_old_and_refreshes_in_background(self):
        od._mc_snapshot("economic", 28)
        with od._MC_LOCK:
            od._MC_STATE["economic"]["ts"] = time.time() - 99999   # force stale
        r = od._mc_snapshot("economic", 28)
        self.assertEqual(r["events"], [{"event": "CPI"}])          # stale served
        self.assertGreater(r["cache_age_sec"], 1000)
        end = time.time() + 5
        while time.time() < end:
            with od._MC_LOCK:
                if not od._MC_STATE["economic"]["refreshing"] and \
                        time.time() - od._MC_STATE["economic"]["ts"] < 60:
                    break
            time.sleep(0.05)
        self.assertEqual(len([c for c in self.calls if c[0] == "economic"]), 2)

    def test_disk_round_trip(self):
        od._mc_snapshot("earnings", 35)
        self.assertTrue(od._MC_PATH.exists())
        with od._MC_LOCK:
            od._MC_STATE["earnings"].update({"ts": 0.0, "payload": None})
        od._mc_load_disk()
        with od._MC_LOCK:
            self.assertIsNotNone(od._MC_STATE["earnings"]["payload"])
            self.assertGreater(od._MC_STATE["earnings"]["ts"], 0)

    def test_failed_build_never_cached(self):
        od.build_watchlist_earnings = lambda days: {"error": "offline", "entries": []}
        r = od._mc_snapshot("earnings", 35)
        self.assertIn("error", r)
        with od._MC_LOCK:
            self.assertIsNone(od._MC_STATE["earnings"]["payload"])


if __name__ == "__main__":
    unittest.main()
