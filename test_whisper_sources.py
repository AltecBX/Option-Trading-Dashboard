"""Tests for whisper_sources.py (v3.68) — every provider and fallback path,
driven by SAVED FIXTURES captured from the real providers (fixtures/ew_*.json)
plus synthetic failure bodies. No network: the HTTP layer is stubbed.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import perfection
import whisper_sources as ws

FIX = Path(__file__).parent / "fixtures"


def _stub_get(responses):
    """Replace ws._get with a scripted responder. responses: list of
    (status, ctype, text) tuples consumed in order, or a callable(url)."""
    calls = []

    def fake(url, referer=None, accept="application/json"):
        calls.append(url)
        r = responses(url) if callable(responses) else responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    return fake, calls


class Base(unittest.TestCase):
    # The provider fixtures are REAL captures, so their event dates are fixed
    # in the past now (SE reports 2026-08-11). Production correctly refuses a
    # manual whisper entered AFTER the event it claims to describe
    # (whisper_sources.py:425), so any test that files one has to be standing
    # before that date. Pinning the clock keeps these tests about the merge
    # logic they are actually testing, instead of quietly expiring — this suite
    # went red on 2026-08-12 for exactly that reason, blocking an unrelated PR.
    CLOCK = "2026-08-01T12:00:00Z"

    def pin_clock(self, iso=None):
        """Freeze whisper_sources' notion of now for this test."""
        orig = ws._now_iso
        ws._now_iso = lambda: (iso or self.CLOCK)
        self.addCleanup(lambda: setattr(ws, "_now_iso", orig))

    def setUp(self):
        self._orig_get = ws._get
        self._tmp = tempfile.TemporaryDirectory()
        ws.configure(self._tmp.name)
        ws._CACHE.clear()
        ws._LAST_REQ.clear()
        os.environ.pop("JERRY_NO_NET", None)

    def tearDown(self):
        ws._get = self._orig_get
        self._tmp.cleanup()
        os.environ["JERRY_NO_NET"] = "1"


class TestEarningsWhispers(Base):
    def test_live_whisper_fixture(self):
        body = (FIX / "ew_getstocksdata_SE.json").read_text()
        ws._get, _ = _stub_get([(200, "application/json", body)])
        res = ws.fetch_earningswhispers("SE")
        self.assertEqual(res["status"], "ok")
        q = res["quote"]
        self.assertEqual(q["kind"], "provider_whisper")
        self.assertEqual(q["whisper_eps"], 1.04)
        self.assertEqual(q["consensus_eps"], 1.0)
        self.assertEqual(q["consensus_revenue"], 7340000000.0)
        self.assertEqual(q["earnings_date"], "2026-08-11")
        self.assertEqual(q["session"], "BMO")            # releaseTime 1
        self.assertTrue(q["confirmed"])
        self.assertIn("earningswhispers.com/stocks/SE", q["source_url"])
        self.assertTrue(q["asof"])

    def test_no_whisper_yet_still_consensus(self):
        body = (FIX / "ew_getstocksdata_AMD.json").read_text()
        ws._get, _ = _stub_get([(200, "application/json", body)])
        res = ws.fetch_earningswhispers("AMD")
        q = res["quote"]
        self.assertEqual(res["status"], "ok")
        self.assertEqual(q["kind"], "consensus_only")    # calendar+consensus, no whisper
        self.assertIsNone(q["whisper_eps"])              # never invented
        self.assertEqual(q["consensus_eps"], 1.61)
        self.assertEqual(q["earnings_date"], "2026-11-03")
        self.assertEqual(q["session"], "AMC")            # releaseTime 3

    def test_amc_session_mapping(self):
        body = (FIX / "ew_getstocksdata_CSCO.json").read_text()
        ws._get, _ = _stub_get([(200, "application/json", body)])
        q = ws.fetch_earningswhispers("CSCO")["quote"]
        self.assertEqual(q["session"], "AMC")
        self.assertEqual(q["whisper_eps"], 1.2)

    def test_204_no_data(self):
        ws._get, _ = _stub_get([(204, "", "")])
        self.assertEqual(ws.fetch_earningswhispers("XX")["status"], "no_data")

    def test_layout_change_html_body(self):
        ws._get, _ = _stub_get([(200, "text/html", "<!DOCTYPE html><html>...")])
        res = ws.fetch_earningswhispers("SE")
        self.assertEqual(res["status"], "layout_changed")
        self.assertIsNone(res["quote"])

    def test_layout_change_wrong_ticker(self):
        ws._get, _ = _stub_get([(200, "application/json", json.dumps({"ticker": "OTHER"}))])
        self.assertEqual(ws.fetch_earningswhispers("SE")["status"], "layout_changed")

    def test_whisper_sanity_check_discards_garbage(self):
        d = json.loads((FIX / "ew_getstocksdata_SE.json").read_text())
        d["whisper"] = 500.0                              # 500x consensus = parse garbage
        ws._get, _ = _stub_get([(200, "application/json", json.dumps(d))])
        q = ws.fetch_earningswhispers("SE")["quote"]
        self.assertIsNone(q["whisper_eps"])
        self.assertIn("sanity", q["validation_note"])

    def test_retry_on_5xx_then_success(self):
        body = (FIX / "ew_getstocksdata_SE.json").read_text()
        ws._get = self._orig_get                          # test the real retry loop
        seq = [(503, "text/html", "err"), (200, "application/json", body)]
        calls = {"n": 0}

        class _R:
            def __init__(self, s, c, t): self.status_code, self.text = s, t; self._c = c
            @property
            def headers(self): return {"Content-Type": self._c}

        class _S:
            def get(self, url, headers=None, timeout=None):
                r = seq[min(calls["n"], 1)]
                calls["n"] += 1
                return _R(*r)
        ws._SESSION_FACTORY = lambda: _S()
        ws.RATE_MIN_INTERVAL, orig = 0.0, ws.RATE_MIN_INTERVAL
        try:
            res = ws.fetch_earningswhispers("SE")
        finally:
            ws.RATE_MIN_INTERVAL = orig
            ws._SESSION_FACTORY = None
        self.assertEqual(res["status"], "ok")
        self.assertEqual(calls["n"], 2)                   # one retry, then success

    def test_4xx_no_retry(self):
        ws._get = self._orig_get
        calls = {"n": 0}

        class _R:
            status_code, text = 404, "not found"
            headers = {"Content-Type": "text/html"}

        class _S:
            def get(self, url, headers=None, timeout=None):
                calls["n"] += 1
                return _R()
        ws._SESSION_FACTORY = lambda: _S()
        ws.RATE_MIN_INTERVAL, orig = 0.0, ws.RATE_MIN_INTERVAL
        try:
            res = ws.fetch_earningswhispers("SE")
        finally:
            ws.RATE_MIN_INTERVAL = orig
            ws._SESSION_FACTORY = None
        self.assertEqual(res["status"], "http_404")
        self.assertEqual(calls["n"], 1)                   # 4xx never retries

    def test_cache_prevents_second_fetch(self):
        body = (FIX / "ew_getstocksdata_SE.json").read_text()
        fake, calls = _stub_get([(200, "application/json", body)])
        ws._get = fake
        ws.fetch_earningswhispers("SE")
        ws.fetch_earningswhispers("SE")
        self.assertEqual(len(calls), 1)

    def test_last_event_history(self):
        body = (FIX / "ew_epsdetails_CSCO.json").read_text()
        ws._get, _ = _stub_get([(200, "application/json", body)])
        res = ws.fetch_ew_last_event("CSCO")
        e = res["event"]
        self.assertEqual(res["status"], "ok")
        self.assertEqual(e["eps_actual"], 1.08)
        self.assertEqual(e["consensus_eps"], 1.04)
        self.assertEqual(e["whisper_eps"], 1.06)          # the whisper that stood
        self.assertEqual(e["estimate_range"], [1.03, 1.06])
        self.assertEqual(e["event_date"], "2026-05-13")


class TestOtherProviders(Base):
    def test_seekingalpha_blocked(self):
        ws._get, _ = _stub_get([(403, "text/html",
                                 '<html><meta name="description" content="px-captcha">')])
        res = ws.fetch_seekingalpha("AMD")
        self.assertEqual(res["status"], "blocked")
        self.assertIsNone(res["quote"])

    def test_seekingalpha_consensus_only_if_ever_reachable(self):
        body = json.dumps({"data": [{"attributes": {"eps_estimate": 1.5,
                                                    "revenue_estimate": 5.0e9}}]})
        ws._get, _ = _stub_get([(200, "application/json", body)])
        q = ws.fetch_seekingalpha("AMD")["quote"]
        self.assertEqual(q["kind"], "consensus_only")
        self.assertIsNone(q["whisper_eps"])               # SA never yields a whisper
        self.assertEqual(q["consensus_eps"], 1.5)

    def test_whispernumber_unreachable(self):
        ws._get, _ = _stub_get(lambda url: ConnectionError("TLS verify failed"))
        res = ws.fetch_whispernumber("AMD")
        self.assertEqual(res["status"], "unreachable")

    def test_whispernumber_no_public_data(self):
        ws._get, _ = _stub_get([(200, "text/html", "<html>register to view</html>")])
        self.assertEqual(ws.fetch_whispernumber("AMD")["status"], "no_public_data")

    def test_whispernumber_public_parse(self):
        ws._get, _ = _stub_get([(200, "text/html",
                                 "<div>The whisper number is $1.23 for ...</div>")])
        q = ws.fetch_whispernumber("AMD")["quote"]
        self.assertEqual(q["whisper_eps"], 1.23)
        self.assertEqual(q["kind"], "community_estimate")


class TestManualAndPIT(Base):
    def test_manual_roundtrip_and_event_binding(self):
        r = ws.add_manual("AMD", "Earnings Whispers (read manually)", eps=1.7,
                          url="https://www.earningswhispers.com/stocks/AMD",
                          next_earnings="2026-11-03")
        self.assertTrue(r["ok"])
        rows = ws.manual_for_event("AMD", "2026-11-03")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "user_supplied")
        # A DIFFERENT event never sees this entry.
        self.assertEqual(ws.manual_for_event("AMD", "2027-02-02"), [])

    def test_manual_requires_source_and_value(self):
        self.assertFalse(ws.add_manual("AMD", "", eps=1.7)["ok"])
        self.assertFalse(ws.add_manual("AMD", "somewhere")["ok"])

    def test_post_event_entry_excluded(self):
        ws.add_manual("AMD", "late note", eps=2.0, next_earnings="2020-01-01")
        # asof (today) is after that event date → excluded from that event.
        self.assertEqual(ws.manual_for_event("AMD", "2020-01-01"), [])

    def test_snapshot_leakage_guard(self):
        d = ws._snap_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "AMD.jsonl"
        lines = [
            {"date": "2026-10-30", "source": "earningswhispers", "whisper_eps": 1.7,
             "for_earnings": "2026-11-03"},
            {"date": "2026-11-04", "source": "earningswhispers", "whisper_eps": 9.9,
             "for_earnings": "2026-11-03"},   # post-event revision — must never win
        ]
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        snap = ws.whisper_for_event("AMD", "2026-11-03")
        self.assertEqual(snap["whisper_eps"], 1.7)
        self.assertEqual(snap["date"], "2026-10-30")
        # An event we hold nothing for → None, never backfilled.
        self.assertIsNone(ws.whisper_for_event("AMD", "2026-02-03"))

    def test_snapshot_once_per_source_per_day(self):
        q = {"source": "earningswhispers", "kind": "provider_whisper",
             "whisper_eps": 1.04, "consensus_eps": 1.0, "consensus_revenue": None,
             "earnings_date": "2026-08-11", "asof": ws._now_iso()}
        ws._store_snapshot("SE", q)
        ws._store_snapshot("SE", q)
        p = ws._snap_dir() / "SE.jsonl"
        self.assertEqual(len(p.read_text().splitlines()), 1)


class TestCollect(Base):
    def _adapters(self, ew_body=None, ew_status=200):
        body = ew_body if ew_body is not None else (FIX / "ew_getstocksdata_SE.json").read_text()
        ws._get, _ = _stub_get(lambda url: (
            (ew_status, "application/json" if ew_status == 200 else "", body)
            if "earningswhispers" in url else
            (403, "text/html", "px-captcha") if "seekingalpha" in url else
            ConnectionError("TLS")))
        return dict(ws.ADAPTERS)

    def test_provider_whisper_high_confidence_and_gaps(self):
        out = ws.collect("SE", consensus_eps=1.0, consensus_revenue=7.3e9,
                         next_earnings="2026-08-11", adapters=self._adapters())
        mi = out["model_input"]
        self.assertTrue(mi["available"])
        self.assertEqual(mi["confidence"], "high")        # fresh provider whisper
        self.assertAlmostEqual(mi["eps_gap_pct"], 4.0, places=1)
        self.assertEqual(mi["median_eps"], 1.04)
        self.assertEqual(mi["source_count"], 1)
        self.assertIn("Earnings Whispers", mi["sources"])
        st = out["panel"]["statuses"]
        self.assertEqual(st["seekingalpha"]["status"], "blocked")
        self.assertEqual(st["whispernumber"]["status"], "unreachable")
        self.assertEqual(out["panel"]["quick_links"]["earningswhispers"],
                         "https://www.earningswhispers.com/stocks/SE")

    def test_no_sources_available_false_note(self):
        out = ws.collect("ZZZ", consensus_eps=1.0,
                         adapters=self._adapters(ew_body="", ew_status=204))
        mi = out["model_input"]
        self.assertFalse(mi["available"])
        self.assertIn("No reliable whisper estimate available", mi["note"])
        self.assertIsNone(mi["eps_gap_pct"])              # never invented

    def test_consensus_conflict_switches_gap_basis(self):
        # Provider consensus 1.00 vs app consensus 2.00 (100% off — different
        # vintage). Headline gap must use the provider's own pair, labeled.
        out = ws.collect("SE", consensus_eps=2.0, next_earnings="2026-08-11",
                         adapters=self._adapters())
        mi = out["model_input"]
        self.assertTrue(out["panel"]["conflicts"])
        self.assertIn("SAME provider", mi["gap_basis"])
        self.assertAlmostEqual(mi["eps_gap_pct"], 4.0, places=1)   # 1.04 vs 1.00
        # naive mixing would have said (1.04-2.00)/2.00 = -48% — never shown.

    def test_user_entry_merges_and_dispersion_caps_confidence(self):
        self.pin_clock()
        ws.add_manual("SE", "MyNewsletter", eps=1.30,
                      url="https://example.com/note", next_earnings="2026-08-11")
        out = ws.collect("SE", consensus_eps=1.0, next_earnings="2026-08-11",
                         adapters=self._adapters())
        mi = out["model_input"]
        self.assertEqual(mi["source_count"], 2)
        self.assertEqual(mi["range"], [1.04, 1.30])       # both shown, none picked silently
        self.assertEqual(mi["median_eps"], 1.17)
        self.assertEqual(mi["confidence"], "low")         # 22% dispersion → low
        kinds = {s["kind"] for s in out["panel"]["sources"] if s.get("whisper_eps")}
        self.assertEqual(kinds, {"provider_whisper", "user_supplied"})

    def test_user_only_with_url_medium_without_low(self):
        self.pin_clock()
        ws.add_manual("QQ", "SomeSite", eps=1.5, url="https://x.y/z",
                      next_earnings="2026-09-01")
        out = ws.collect("QQ", consensus_eps=1.4, next_earnings="2026-09-01",
                         adapters=self._adapters(ew_body="", ew_status=204))
        self.assertEqual(out["model_input"]["confidence"], "medium")
        ws.add_manual("RR", "hearsay", eps=1.5, next_earnings="2026-09-01")
        out2 = ws.collect("RR", consensus_eps=1.4, next_earnings="2026-09-01",
                          adapters=self._adapters(ew_body="", ew_status=204))
        self.assertEqual(out2["model_input"]["confidence"], "low")

    def test_no_net_guard(self):
        os.environ["JERRY_NO_NET"] = "1"
        out = ws.collect("SE", consensus_eps=1.0)
        self.assertFalse(out["model_input"]["available"])
        self.assertIn("network disabled", out["model_input"]["note"])

    def test_panel_json_safe(self):
        out = ws.collect("SE", consensus_eps=1.0, next_earnings="2026-08-11",
                         adapters=self._adapters())
        json.dumps(out, allow_nan=False)


class TestModelIntegration(Base):
    def test_collected_whisper_drives_model_and_warning(self):
        """End-to-end: the EW fixture whisper flows through collect() into
        perfection.assemble — the component weights it and the warning can
        cite it. Uses the demanding synthetic input set from test_perfection."""
        from test_perfection import rich_inputs
        out = ws.collect("SE", consensus_eps=1.0, next_earnings="2026-08-11",
                         adapters=self._adapters())
        d = rich_inputs(whisper=out["model_input"])
        res = perfection.assemble(d)
        eg = res["components"]["expectations_gap"]
        self.assertEqual(eg["detail"]["whisper_weight_applied"], 35)   # high conf → full
        wh = eg["benchmarks"]["whisper"]
        self.assertTrue(wh["available"])
        self.assertEqual(wh["eps_gap_pct"], 4.0)
        self.assertIn("Earnings Whispers", wh["sources"])
        self.assertTrue(any("whisper" in c.lower() for c in res["warning"]["conditions"]))

    def _adapters(self, ew_body=None, ew_status=200):
        return TestCollect._adapters(self, ew_body=ew_body, ew_status=ew_status)


if __name__ == "__main__":
    unittest.main()
