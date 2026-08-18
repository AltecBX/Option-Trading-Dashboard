"""test_chain_store.py (B2) — snapshot round-trip, lookup semantics, and
real-quote precedence in the lifecycle engine + sensitivity harness.
Run:  python3 -m unittest test_chain_store
"""
import tempfile
import unittest
from datetime import date, timedelta

import chain_store as cs
import bt_options as bo
from test_bt_options import mk_bars, IV, MGMT_NONE


def chain_payload(spot=100.0, day="2026-01-02", dtes=(30, 45)):
    d0 = date.fromisoformat(day)
    chains = {}
    for dte in dtes:
        exp = (d0 + timedelta(days=dte)).isoformat()
        calls, puts = [], []
        for k in range(80, 121, 5):
            calls.append({"strike": float(k), "bid": 1.0, "ask": 1.1,
                          "iv": 0.24, "delta": 0.4, "openInterest": 500})
            puts.append({"strike": float(k), "bid": 2.0, "ask": 2.2,
                         "iv": 0.26, "delta": -0.3, "openInterest": 700})
        chains[exp] = {"calls": calls, "puts": puts}
    return {"underlying": {"last": spot},
            "expirations": list(chains.keys()), "chains": chains}


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jerry_chains_")
        cs.configure(self.tmp)
        cs._RECORDED_TODAY.clear()

    def test_record_and_load_roundtrip(self):
        ok = cs.record("TEST", chain_payload(), today="2026-01-02")
        self.assertTrue(ok)
        store = cs.load("TEST")
        self.assertIn("2026-01-02", store)
        self.assertEqual(store["2026-01-02"]["spot"], 100.0)
        # strike band filter: 80 is within 30% of 100 → kept
        exps = store["2026-01-02"]["exps"]
        self.assertEqual(len(exps), 2)

    def test_once_per_day_throttle(self):
        self.assertTrue(cs.record("TEST", chain_payload(), today="2026-01-02"))
        self.assertFalse(cs.record("TEST", chain_payload(), today="2026-01-02"))
        self.assertTrue(cs.record("TEST", chain_payload(day="2026-01-03"),
                                  today="2026-01-03"))

    def test_lookup_matches_dte_and_strike(self):
        cs.record("TEST", chain_payload(dtes=(30, 45)), today="2026-01-02")
        store = cs.load("TEST")
        q = cs.lookup(store, "2026-01-02", "put", 95.2, 45)
        self.assertIsNotNone(q)
        self.assertEqual(q["strike"], 95.0)          # snapped to listed
        self.assertEqual(q["bid"], 2.0)
        want_exp = (date(2026, 1, 2) + timedelta(days=45)).isoformat()
        self.assertEqual(q["expiry"], want_exp)

    def test_lookup_rejects_wrong_day_or_far_strike(self):
        cs.record("TEST", chain_payload(), today="2026-01-02")
        store = cs.load("TEST")
        self.assertIsNone(cs.lookup(store, "2026-01-03", "put", 95, 45))
        self.assertIsNone(cs.lookup(store, "2026-01-02", "put", 60.0, 45))

    def test_coverage(self):
        cs.record("TEST", chain_payload(), today="2026-01-02")
        store = cs.load("TEST")
        self.assertEqual(cs.coverage(store, ["2026-01-02", "2026-01-03"]), 0.5)


# ── Phase 5: what every observation must retain ────────────────────────────

class TestObservationCompleteness(unittest.TestCase):
    """Phase 5 asked that every stored observation keep its date, underlying
    price, expiration, strike, side, bid, ask, last, volume, open interest,
    implied volatility, quote source, quote quality and event state."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jerry_chains5_")
        cs.configure(self.tmp)
        cs._RECORDED_TODAY.clear()

    def _rich(self, day="2026-01-02"):
        p = chain_payload(day=day)
        for sides in p["chains"].values():
            for side in ("calls", "puts"):
                for r in sides[side]:
                    r["last"] = 1.05
                    r["volume"] = 42
        return p

    def test_every_required_field_survives_the_round_trip(self):
        cs.record("TEST", self._rich(), today="2026-01-02",
                  source="schwab",
                  event={"next_earnings": "2026-01-20", "days_to_earnings": 18})
        q = cs.lookup(cs.load("TEST"), "2026-01-02", "call", 100.0, 30)
        for key in ("bid", "ask", "mid", "iv", "expiry", "strike", "last",
                    "volume", "open_interest", "quality", "quality_label",
                    "source", "captured_at", "event"):
            self.assertIn(key, q, key)
        self.assertEqual(q["last"], 1.05)
        self.assertEqual(q["volume"], 42)
        self.assertEqual(q["source"], "schwab")
        self.assertEqual(q["event"]["days_to_earnings"], 18)
        self.assertEqual(q["quality_label"], "TWO SIDED")

    def test_the_underlying_price_and_date_are_the_snapshot_key(self):
        cs.record("TEST", self._rich(), today="2026-01-02")
        snap = cs.load("TEST")["2026-01-02"]
        self.assertEqual(snap["spot"], 100.0)
        self.assertTrue(snap["ts"])
        self.assertEqual(snap["v"], cs.SCHEMA)

    def test_quality_tells_a_tradeable_market_from_a_one_sided_one(self):
        self.assertEqual(cs.quality_of({"bid": 1.0, "ask": 1.1}),
                         cs.Q_TWO_SIDED)
        self.assertEqual(cs.quality_of({"bid": 1.0, "ask": 3.0}), cs.Q_WIDE)
        self.assertEqual(cs.quality_of({"bid": 0.0, "ask": 1.1}),
                         cs.Q_ONE_SIDED)
        self.assertEqual(cs.quality_of({"bid": 1.0, "ask": 1.1,
                                        "quote_age_s": 4000}), cs.Q_STALE)

    def test_a_version_one_row_reads_as_not_recorded_rather_than_zero(self):
        # Six numbers is the pre-Phase-5 layout. Its last trade and volume
        # are UNKNOWN, and reading them as zero would say nothing traded.
        r = cs._unpack([100.0, 1.0, 1.1, 0.24, 0.4, 500])
        self.assertIsNone(r["last"])
        self.assertIsNone(r["volume"])
        self.assertEqual(r["quality"], cs.Q_UNKNOWN)
        self.assertEqual(r["open_interest"], 500)


class TestNoBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jerry_chains5b_")
        cs.configure(self.tmp)
        cs._RECORDED_TODAY.clear()

    def test_a_day_already_on_disk_is_never_replaced(self):
        cs.record("TEST", chain_payload(spot=100.0), today="2026-01-02")
        cs._RECORDED_TODAY.clear()           # a fresh process, same day
        self.assertFalse(cs.record("TEST", chain_payload(spot=222.0),
                                   today="2026-01-02"))
        self.assertEqual(cs.load("TEST")["2026-01-02"]["spot"], 100.0)

    def test_readiness_says_nothing_can_be_filled_in_afterwards(self):
        out = cs.readiness({})
        self.assertEqual(out["days"], 0)
        self.assertIn("cannot be back-filled", out["reason"])

    def test_readiness_counts_what_is_there(self):
        cs.record("TEST", chain_payload(dtes=(7, 17, 38)), today="2026-01-02")
        cs._RECORDED_TODAY.clear()
        cs.record("TEST", chain_payload(day="2026-01-05", dtes=(7, 17, 38)),
                  today="2026-01-05", source="schwab")
        out = cs.readiness(cs.load("TEST"),
                           ["2026-01-02", "2026-01-03", "2026-01-04",
                            "2026-01-05"])
        self.assertEqual(out["days"], 2)
        self.assertEqual(out["first"], "2026-01-02")
        self.assertEqual(out["last"], "2026-01-05")
        self.assertEqual(out["expirations"], 6)
        self.assertGreater(out["contracts"], 0)
        self.assertAlmostEqual(out["window_coverage_pct"], 50.0, places=6)
        self.assertIn("schwab", out["sources"])


class TestCoveredCallWindow(unittest.TestCase):
    """The capture has to cover what the covered-call policies actually
    read — and no more, because a daily capture of a watchlist has to be a
    small request rather than a whole chain."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jerry_chains5c_")
        cs.configure(self.tmp)
        cs._RECORDED_TODAY.clear()

    def test_all_three_covered_call_tenors_are_inside_the_kept_window(self):
        import covered_call as cc
        for t in cc.TENORS.values():
            self.assertLessEqual(t["target_dte"], cs.MAX_DTE)

    def test_expirations_beyond_the_window_are_dropped(self):
        cs.record("TEST", chain_payload(dtes=(30, 120)), today="2026-01-02")
        exps = cs.load("TEST")["2026-01-02"]["exps"]
        self.assertEqual(len(exps), 1)

    def test_strikes_far_from_the_money_are_dropped(self):
        p = chain_payload(dtes=(30,))
        exp = list(p["chains"])[0]
        p["chains"][exp]["calls"].append(
            {"strike": 500.0, "bid": 0.01, "ask": 0.05, "iv": 0.9,
             "delta": 0.01, "openInterest": 1})
        cs.record("TEST", p, today="2026-01-02")
        strikes = [r[0] for r in
                   cs.load("TEST")["2026-01-02"]["exps"][exp]["c"]]
        self.assertNotIn(500.0, strikes)
        # And the band still reaches a quarter above spot, which is as far as
        # the fair-value-aware strike rule can push a call.
        self.assertGreaterEqual(max(strikes), 120.0)


class TestEnginePrecedence(unittest.TestCase):
    def test_real_quote_entry_fill(self):
        bars = mk_bars(closes=[100.0] * 60)
        fill_day = bars[1]["date"]

        def quote_fn(day, right, strike, dte):
            if day != fill_day:
                return None
            return {"bid": 3.33, "ask": 3.43, "mid": 3.38, "iv": 0.3,
                    "expiry": "x", "strike": round(strike)}

        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60, MGMT_NONE,
                                 params={"dte": 45}, quote_fn=quote_fn)
        self.assertEqual(t["priced"], "real_quote")
        self.assertEqual(t["legs"][0]["entry_px"], 3.33)   # sold at REAL bid
        self.assertEqual(t["legs"][0]["fill_src"], "real")
        self.assertAlmostEqual(t["credit"], 3.33, places=4)

    def test_modeled_when_no_snapshot(self):
        bars = mk_bars(closes=[100.0] * 60)
        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60, MGMT_NONE,
                                 params={"dte": 45}, quote_fn=lambda *a: None)
        self.assertEqual(t["priced"], "modeled")
        self.assertEqual(t["legs"][0]["fill_src"], "model")

    def test_strike_collision_guard(self):
        # A quote_fn that snaps EVERY strike to 100 must not collapse the
        # strangle's two same-right legs... strangle legs differ by right,
        # so use an iron fly (two puts) to prove the guard.
        bars = mk_bars(closes=[100.0] * 60)

        def quote_fn(day, right, strike, dte):
            return {"bid": 1.0, "ask": 1.1, "mid": 1.05, "iv": 0.3,
                    "expiry": "x", "strike": 100.0}

        t = bo.simulate_position(bars, 0, "iron_fly", [IV] * 60, MGMT_NONE,
                                 params={"dte": 45}, quote_fn=quote_fn)
        if t is not None:
            put_strikes = [L["strike"] for L in t["legs"] if L["right"] == "put"]
            self.assertEqual(len(set(put_strikes)), len(put_strikes))
            self.assertEqual(t["priced"], "mixed")


if __name__ == "__main__":
    unittest.main()
