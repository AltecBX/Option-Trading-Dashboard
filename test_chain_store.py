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
