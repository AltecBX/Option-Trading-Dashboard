"""Tests for premium_edge.py — IV30 interpolation, term structure, skew,
VRP, event classification, observation store, contract economics (with a
Monte Carlo cross-check of the closed-form expected shortfall), structure
selection per intent, score explainability, and signal gates.

Chains are built synthetically in the exact schwab_client._normalize_chain
shape, priced with the canonical metrics Black-Scholes, so every expected
value is computable independently inside the test.
"""

import json
import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from metrics import _bs_price, _bs_delta
import premium_edge as pe

NOW = date(2026, 8, 14)


def mk_chain(spot=100.0, expiries=None, spread_frac=0.06, skew_slope=0.0,
             volume=500, oi=1000, quote_age_s=10):
    """Synthetic normalized chain. expiries: {dte: sigma}. iv(K) = sigma +
    skew_slope * (-ln(K/S)) so positive slope makes downside puts richer."""
    expiries = expiries or {30: 0.40}
    chains = {}
    exps = []
    for dte, sigma in expiries.items():
        exp = (NOW + timedelta(days=dte)).isoformat()
        exps.append(exp)
        T = dte / 365.0
        calls, puts = [], []
        k = round(spot * 0.70, 2)
        while k <= spot * 1.30:
            iv_k = max(0.05, sigma + skew_slope * (-math.log(k / spot)))
            for side, rows in (("call", calls), ("put", puts)):
                px = _bs_price(spot, k, T, iv_k, side, r=0.0, q=0.0)
                hs = max(0.02, px * spread_frac / 2.0)
                rows.append({
                    "strike": round(k, 2), "bid": round(max(0.0, px - hs), 2),
                    "ask": round(px + hs, 2), "last": round(px, 2),
                    "bid_size": 10, "ask_size": 10, "volume": volume,
                    "openInterest": oi, "iv": round(iv_k, 4),
                    "delta": round(_bs_delta(spot, k, T, iv_k, side, r=0.04), 4),
                    "theta": -0.05, "gamma": 0.01, "vega": 0.1,
                    "occ": f"FAKE{k}", "quote_age_s": quote_age_s,
                })
            k = round(k + spot * 0.025, 2)
        chains[exp] = {"calls": calls, "puts": puts}
    return {"underlying": {"symbol": "FAKE", "last": spot, "bid": spot - 0.02,
                           "ask": spot + 0.02, "name": "Fake Corp"},
            "expirations": sorted(exps), "chains": chains, "source": "test"}


def cfg():
    c, _h = pe.config(refresh=True)
    return c


class TestIV30(unittest.TestCase):
    def test_variance_interpolation_exact(self):
        chain = mk_chain(expiries={20: 0.40, 40: 0.30})
        out = pe.iv30(chain, NOW, cfg())
        self.assertEqual(out["method"], "variance_interpolation")
        va = 0.40 ** 2 * (20 / 365.0)
        vb = 0.30 ** 2 * (40 / 365.0)
        vt = va + (vb - va) * ((30 - 20) / (40 - 20))
        expect = math.sqrt(vt / (30 / 365.0))
        self.assertAlmostEqual(out["iv30"], expect, delta=0.005)

    def test_single_expiry_falls_back_labeled(self):
        out = pe.iv30(mk_chain(expiries={25: 0.35}), NOW, cfg())
        self.assertEqual(out["method"], "nearest_expiry")
        self.assertAlmostEqual(out["iv30"], 0.35, delta=0.01)

    def test_wide_spreads_rejected(self):
        chain = mk_chain(expiries={20: 0.40, 40: 0.30}, spread_frac=1.2)
        self.assertIsNone(pe.iv30(chain, NOW, cfg()))

    def test_stale_quotes_rejected_only_when_open(self):
        chain = mk_chain(expiries={20: 0.40, 40: 0.30}, quote_age_s=5000)
        self.assertIsNone(pe.iv30(chain, NOW, cfg(), market_open=True))
        closed = pe.iv30(chain, NOW, cfg(), market_open=False)
        self.assertIsNotNone(closed)

    def test_dte_window_respected(self):
        # a 2-day and a 200-day expiry are both outside [5, 75]
        self.assertIsNone(pe.iv30(mk_chain(expiries={2: 0.6, 200: 0.3}), NOW, cfg()))


class TestTermStructure(unittest.TestCase):
    def test_backwardation_detected(self):
        chain = mk_chain(expiries={7: 0.50, 30: 0.40, 60: 0.35})
        t = pe.term_structure(chain, NOW, cfg())
        self.assertEqual(t["shape"], "backwardation")
        self.assertGreaterEqual(t["front_back_ratio"], 1.08)

    def test_contango_detected(self):
        chain = mk_chain(expiries={7: 0.28, 30: 0.33, 60: 0.36})
        t = pe.term_structure(chain, NOW, cfg())
        self.assertEqual(t["shape"], "contango")

    def test_earnings_hump_flagged(self):
        earn = (NOW + timedelta(days=18)).isoformat()
        chain = mk_chain(expiries={7: 0.30, 21: 0.45, 45: 0.32})
        t = pe.term_structure(chain, NOW, cfg(), earnings_date=earn)
        self.assertTrue(t["humps"])
        self.assertTrue(t["humps"][0]["covers_earnings"])
        self.assertTrue(t["richest"]["covers_earnings"])

    def test_marks_present(self):
        chain = mk_chain(expiries={7: 0.4, 30: 0.4, 60: 0.4, 91: 0.4})
        t = pe.term_structure(chain, NOW, cfg())
        for k in ("iv7", "iv14", "iv30", "iv45", "iv60", "iv90"):
            self.assertIsNotNone(t["marks"][k], k)
            self.assertAlmostEqual(t["marks"][k], 0.40, delta=0.02)


class TestSkew(unittest.TestCase):
    def test_put_rich_skew_positive_rr(self):
        chain = mk_chain(expiries={30: 0.35}, skew_slope=0.25)
        s = pe.skew(chain, NOW, cfg())
        self.assertGreater(s["rr25_volpts"], 0)
        self.assertGreater(s["put_skew_volpts"], 0)
        self.assertLess(s["call_skew_volpts"], 0)
        self.assertIn("put_minus_call", s["convention"])

    def test_flat_skew_near_zero(self):
        s = pe.skew(mk_chain(expiries={30: 0.35}), NOW, cfg())
        self.assertAlmostEqual(s["rr25_volpts"], 0.0, delta=1.0)

    def test_ten_delta_reported_when_liquid(self):
        s = pe.skew(mk_chain(expiries={30: 0.45}, skew_slope=0.2), NOW, cfg())
        self.assertIsNotNone(s["put10_iv"])


class TestVRPAndEvents(unittest.TestCase):
    def test_vrp_numbers(self):
        v = pe.vrp_block(0.48, {"erv30": 0.36, "erv30_event": 0.36})
        self.assertAlmostEqual(v["vrp_points"], 12.0, places=1)
        self.assertAlmostEqual(v["vrp_ratio"], 1.333, places=2)
        self.assertAlmostEqual(v["vrp_variance"], 0.48 ** 2 - 0.36 ** 2, places=4)

    def test_event_share_drives_class(self):
        c = cfg()
        # earnings explains most of the gap -> EVENT
        v = pe.vrp_block(0.48, {"erv30": 0.36, "erv30_event": 0.44})
        self.assertGreaterEqual(v["event_share"], 0.60)
        cls = pe.classify_premium(v, True, [], c)
        self.assertEqual(cls["class"], "EVENT")
        # earnings explains little -> PURE even with earnings inside
        v2 = pe.vrp_block(0.48, {"erv30": 0.36, "erv30_event": 0.38})
        cls2 = pe.classify_premium(v2, True, [], c)
        self.assertEqual(cls2["class"], "PURE")
        # no earnings -> PURE regardless
        cls3 = pe.classify_premium(v, False, [], c)
        self.assertEqual(cls3["class"], "PURE")

    def test_mixed_band(self):
        v = pe.vrp_block(0.48, {"erv30": 0.36, "erv30_event": 0.41})
        cls = pe.classify_premium(v, True, [], cfg())
        self.assertEqual(cls["class"], "MIXED")


class TestObservationStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        pe.configure(self.tmp.name)
        pe.config(refresh=True)

    def tearDown(self):
        pe.configure(None)
        self.tmp.cleanup()

    def test_append_dedupe_and_load(self):
        pe.record_observation("FAKE", {"date": "2026-08-13", "vrp_points": 5.0})
        pe.record_observation("FAKE", {"date": "2026-08-14", "vrp_points": 6.0})
        pe.record_observation("FAKE", {"date": "2026-08-14", "vrp_points": 7.0})
        obs = pe.load_observations("FAKE")
        self.assertEqual(len(obs), 2)
        self.assertEqual(obs[-1]["vrp_points"], 7.0)

    def test_vrp_stats_full_history(self):
        rnd = random.Random(1)
        hist = [{"date": f"d{i}", "vrp_points": rnd.gauss(4.0, 2.0)} for i in range(100)]
        st = pe.vrp_stats(hist, 8.0, cfg())
        self.assertEqual(st["status"], "ok")
        self.assertEqual(st["n"], 100)
        vals = [h["vrp_points"] for h in hist]
        mean = sum(vals) / 100
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / 99)
        self.assertAlmostEqual(st["z"], (8.0 - mean) / std, delta=0.02)
        self.assertGreaterEqual(st["p99"], st["p95"])
        self.assertGreaterEqual(st["p95"], st["p90"])

    def test_insufficient_history_is_honest(self):
        hist = [{"date": f"d{i}", "vrp_points": float(i)} for i in range(30)]
        st = pe.vrp_stats(hist, 20.0, cfg())
        self.assertEqual(st["status"], "insufficient_history")
        self.assertEqual(st["n"], 30)
        self.assertEqual(st["min_required"], 60)


class TestProbabilitiesAndES(unittest.TestCase):
    def test_touch_geq_itm(self):
        for k in (85, 90, 95, 105, 110, 115):
            side = "put" if k < 100 else "call"
            t = pe.touch_prob(100, k, 0.35, 30 / 365)
            i = pe.p_itm(100, k, 0.35, 30 / 365, side)
            self.assertGreaterEqual(t + 1e-9, i, f"strike {k}")

    def test_p_itm_monotonic(self):
        vals = [pe.p_itm(100, k, 0.35, 30 / 365, "put") for k in (80, 90, 95, 100)]
        self.assertEqual(vals, sorted(vals))

    def test_es_matches_monte_carlo(self):
        spot, strike, sigma, T, credit = 100.0, 92.0, 0.40, 30 / 365, 1.20
        analytic = pe._tail_es_short(spot, strike, sigma, T, "put", credit, q=0.05)
        rnd = random.Random(42)
        s = sigma * math.sqrt(T)
        losses = []
        for _ in range(200_000):
            st_ = spot * math.exp(-0.5 * s * s + s * rnd.gauss(0, 1))
            losses.append(max(0.0, strike - st_))
        losses.sort(reverse=True)
        tail = losses[:10_000]
        mc = max(0.0, sum(tail) / len(tail) - credit)
        self.assertAlmostEqual(analytic, mc, delta=max(0.05, mc * 0.05))

    def test_es_decreases_with_distance(self):
        near = pe._tail_es_short(100, 95, 0.35, 30 / 365, "put", 0.0)
        far = pe._tail_es_short(100, 80, 0.35, 30 / 365, "put", 0.0)
        self.assertGreater(near, far)

    def test_call_side_es_positive_when_near(self):
        es = pe._tail_es_short(100, 105, 0.35, 30 / 365, "call", 0.50)
        self.assertGreater(es, 0)


class TestContractEconomics(unittest.TestCase):
    def test_ev_positive_when_iv_rich(self):
        # chain priced at IV 0.45, expected RV 0.30 -> seller collects excess
        chain = mk_chain(expiries={30: 0.45})
        exp = chain["expirations"][0]
        puts = chain["chains"][exp]["puts"]
        row = min(puts, key=lambda r: abs(abs(r["delta"]) - 0.25))
        m = pe.contract_economics(row, 100.0, "put", 30 / 365, 0.30, cfg())
        self.assertGreater(m["ev_per_share"], 0)
        self.assertEqual(m["credit_exec"], row["bid"])
        self.assertIn("bid", m["credit_basis"])
        self.assertIn("model", m["prob_basis"])

    def test_ev_negative_when_iv_cheap(self):
        chain = mk_chain(expiries={30: 0.25})
        exp = chain["expirations"][0]
        puts = chain["chains"][exp]["puts"]
        row = min(puts, key=lambda r: abs(abs(r["delta"]) - 0.30))
        m = pe.contract_economics(row, 100.0, "put", 30 / 365, 0.40, cfg())
        self.assertLess(m["ev_per_share"], 0)

    def test_breakeven_and_collateral(self):
        chain = mk_chain(expiries={30: 0.40})
        exp = chain["expirations"][0]
        row = min(chain["chains"][exp]["puts"], key=lambda r: abs(abs(r["delta"]) - 0.25))
        m = pe.contract_economics(row, 100.0, "put", 30 / 365, 0.35, cfg())
        self.assertAlmostEqual(m["breakeven"], row["strike"] - row["bid"], places=2)
        self.assertAlmostEqual(m["collateral"], row["strike"] * 100, delta=1)

    def test_liquidity_gate(self):
        ok, why = pe.liquidity_gate({"oi": 20, "volume": 0, "spread_pct": 25.0}, cfg())
        self.assertFalse(ok)
        self.assertTrue(any("open interest" in w for w in why))
        ok2, why2 = pe.liquidity_gate({"oi": 2000, "volume": 50, "spread_pct": 4.0}, cfg())
        self.assertTrue(ok2)


class TestStructureSelection(unittest.TestCase):
    def setUp(self):
        self.chain = mk_chain(expiries={14: 0.42, 30: 0.44, 45: 0.41})
        self.erv = {"erv30": 0.30, "erv30_event": 0.30}

    def test_premium_only_is_defined_risk_only(self):
        out = pe.select_structures(self.chain, NOW, "premium_only", self.erv, cfg())
        self.assertIsNotNone(out)
        kinds = {s["kind"] for s in out["structures"]}
        self.assertTrue(kinds <= {"put_credit_spread", "call_credit_spread", "iron_condor"})
        for s in out["structures"]:
            self.assertIsNotNone(s.get("max_loss"))

    def test_want_stock_returns_csps(self):
        out = pe.select_structures(self.chain, NOW, "want_stock", self.erv, cfg())
        self.assertTrue(all(s["kind"] == "cash_secured_put" for s in out["structures"]))
        self.assertTrue(all(0.15 <= abs(s["delta"]) <= 0.35 for s in out["structures"]))

    def test_own_stock_returns_covered_calls(self):
        out = pe.select_structures(self.chain, NOW, "own_stock", self.erv, cfg())
        self.assertTrue(all(s["kind"] == "covered_call" for s in out["structures"]))

    def test_condor_max_loss_math(self):
        out = pe.select_structures(self.chain, NOW, "premium_only", self.erv, cfg())
        condors = [s for s in out["structures"] if s["kind"] == "iron_condor"]
        if condors:
            c = condors[0]
            pw = c["short_put"] - c["long_put"]
            cw = c["long_call"] - c["short_call"]
            self.assertAlmostEqual(c["max_loss"], max(pw, cw) - c["credit"], delta=0.02)


class TestScoreAndSignal(unittest.TestCase):
    def test_score_explains_z_substitution(self):
        parts = {"vrp_ratio": 1.4,
                 "hist": {"status": "insufficient_history", "n": 9, "min_required": 60},
                 "best_ev_per_tail": 0.3, "liquidity_ok": True,
                 "skew_advantage": 3.0, "expected_moves_out": 1.2,
                 "danger": {"danger_score": 0, "reasons": []}, "premium_class": "PURE"}
        out = pe.edge_score(parts, cfg())
        self.assertGreater(out["score"], 40)
        notes = " ".join(b["note"] for b in out["breakdown"])
        self.assertIn("9 days of own-history", notes)

    def test_danger_and_event_penalties_reduce_score(self):
        base = {"vrp_ratio": 1.4, "hist": {"status": "ok", "n": 120, "z": 2.0, "percentile": 95},
                "best_ev_per_tail": 0.4, "liquidity_ok": True, "expected_moves_out": 1.3,
                "danger": {"danger_score": 0, "reasons": []}, "premium_class": "PURE"}
        hi = pe.edge_score(base, cfg())["score"]
        dangered = dict(base, danger={"danger_score": 80, "reasons": ["earnings before expiration"]})
        lo = pe.edge_score(dangered, cfg())["score"]
        self.assertLess(lo, hi)
        evented = dict(base, premium_class="EVENT")
        self.assertLess(pe.edge_score(evented, cfg())["score"], hi)

    def test_signal_gates(self):
        c = cfg()
        self.assertEqual(pe.signal_for(95, 1.5, "JUICY", False, c), "INSUFFICIENT DATA")
        self.assertEqual(pe.signal_for(95, 1.5, "DANGEROUS", True, c), "AVOID")
        self.assertEqual(pe.signal_for(30, 0.80, "JUICY", True, c), "CHEAP VOL")
        self.assertEqual(pe.signal_for(85, 1.4, "JUICY", True, c), "STRONG SELL VOL")
        self.assertEqual(pe.signal_for(70, 1.1, "JUICY", True, c), "SELL VOL")
        self.assertEqual(pe.signal_for(50, 1.0, "JUICY", True, c), "WATCH")
        self.assertEqual(pe.signal_for(20, 1.0, "JUICY", True, c), "FAIR")

    def test_danger_model_triggers(self):
        f = {"earnings_inside": True, "rv5_over_rv20": 2.0, "gap_freq_2pct": 0.15,
             "term_shape": "backwardation", "ret20_pct": -22.0, "spread_pct": 15.0,
             "liquidity_poor": True, "rr25_volpts": 10.0, "vix_percentile": 92.0}
        d = pe.danger_model(f, cfg())
        self.assertEqual(d["label"], "DANGEROUS")
        self.assertGreaterEqual(len(d["reasons"]), 6)
        calm = pe.danger_model({}, cfg())
        self.assertEqual(calm["label"], "JUICY")


class TestConfig(unittest.TestCase):
    def test_overlay_overrides_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "thresholds.json").write_text(json.dumps(
                {"premium_edge": {"history": {"min_observations": 99}}}))
            pe.configure(tmp)
            c, h1 = pe.config(refresh=True)
            self.assertEqual(c["history"]["min_observations"], 99)
            # untouched siblings survive the deep merge
            self.assertEqual(c["signal"]["strong_at"], 80)
        pe.configure(None)
        c2, h2 = pe.config(refresh=True)
        self.assertEqual(c2["history"]["min_observations"], 60)
        self.assertNotEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
