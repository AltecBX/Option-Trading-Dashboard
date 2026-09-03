"""test_sell_scan.py — Best Sales Today scanner: the chain consumer, the
per-mode board, NO TRADE, the audit trail and the evidence cache.

The chain is synthetic (Black-Scholes quotes at a fixed smile on a
random-walk history) so the tests are deterministic and need no provider;
what is asserted is the contract the UI and the forward grader rely on.
"""
import json
import math
import os
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import sell_scan as ss
import sp_engine as E


def _N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bs(spot, k, t, iv, side):
    d1 = (math.log(spot / k) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if side == "call":
        return spot * _N(d1) - k * _N(d2), _N(d1)
    return k * _N(-d2) - spot * _N(-d1), _N(d1) - 1


def _bars(n=900, seed=11, start=date(2023, 1, 2)):
    rng = random.Random(seed)
    out, px, d = [], 100.0, start
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        r = rng.gauss(0.0003, 0.016)
        o = px
        px = px * math.exp(r)
        hi, lo = max(o, px) * (1 + abs(rng.gauss(0, 0.004))), min(o, px) * (1 - abs(rng.gauss(0, 0.004)))
        out.append({"date": d.isoformat(), "open": o, "high": hi, "low": lo, "close": px, "volume": 2_000_000})
        d += timedelta(days=1)
    return out


def _chain(spot, today, base_iv=0.32, wide=False):
    chains = {}
    for dte in (14, 28, 45):
        exp = (today + timedelta(days=dte)).isoformat()
        t = dte / 365
        puts, calls = [], []
        k = math.floor(spot * 0.7 / 2.5) * 2.5
        while k <= spot * 1.3:
            m = math.log(k / spot)
            iv = base_iv + 0.25 * max(0.0, -m) + 0.05 * max(0.0, m)
            for side, lst in (("put", puts), ("call", calls)):
                px, dl = _bs(spot, k, t, iv, side)
                if px >= 0.03:
                    spread = max(0.02, px * (0.6 if wide else 0.04))
                    lst.append({"strike": k, "bid": round(px - spread / 2, 2), "ask": round(px + spread / 2, 2),
                                "iv": iv, "delta": round(dl, 4), "volume": 300, "openInterest": 2000,
                                "quote_age_s": 2, "bid_size": 30, "ask_size": 30})
            k += 2.5
        chains[exp] = {"puts": puts, "calls": calls}
    return {"underlying": {"last": spot}, "chains": chains, "source": "schwab"}


BARS = _bars()
TODAY = date.fromisoformat(BARS[-1]["date"])
SPOT = BARS[-1]["close"]


def _ctx(**kw):
    base = {"today": TODAY.isoformat(), "now": TODAY, "spot": SPOT, "market_open": True,
            "earnings_date": (TODAY + timedelta(days=90)).isoformat(),
            "vrp": {"vrp_ratio": 1.3, "vrp_points": 7.0}, "hist": {"percentile": 72},
            "iv30": {"iv30": 0.32}, "erv30": {"erv30": 0.25}, "source": "schwab",
            "bar_features": {"rv5_over_rv20": 0.95}, "vix_percentile": 35}
    base.update(kw)
    return base


class Scanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sellscan_")
        ss._STATE["symbols"].clear()
        ss._STATE["recorded"].clear()
        ss._EVIDENCE_MEM.clear()
        ss.configure(data_dir=cls.tmp,
                     board_getter=lambda: {"rows": [{"symbol": "SYN", "sector": "Technology", "avg_volume": 3e6}]},
                     market_open_fn=lambda: True, status_fn=lambda: {"serving": True},
                     sector_fn=lambda s: "Technology", spy_regime_fn=lambda: {"regime": "long"},
                     now_fn=lambda: TODAY)
        cls.entry = ss.on_chain("SYN", _chain(SPOT, TODAY), BARS, _ctx())

    def test_every_mode_is_evaluated_from_one_chain(self):
        self.assertIsNotNone(self.entry)
        self.assertEqual(set(self.entry["modes"]), set(ss.MODES_ALL))
        self.assertGreater(self.entry["n_candidates"], 20)
        for mode, pm in self.entry["modes"].items():
            self.assertEqual(pm["n_candidates"], self.entry["n_candidates"], mode)
            self.assertEqual(pm["n_qualified"], len(pm["qualified"]))

    def test_stored_candidates_are_compact(self):
        q = self.entry["modes"]["income"]["qualified"] or self.entry["modes"]["balanced"]["qualified"]
        self.assertTrue(q, "expected at least one qualified structure in income/balanced mode")
        for key in ("short", "short_call_leg", "context"):
            self.assertNotIn(key, q[0])
        for key in ("probability", "sell_quality", "gates", "objective_value", "quote"):
            self.assertIn(key, q[0])

    def test_event_mode_without_an_event_is_no_trade(self):
        # earnings are 90 days out: EVENT PREMIUM mode has nothing to sell
        self.assertEqual(self.entry["modes"]["event"]["n_qualified"], 0)
        snap = ss.snapshot("event", record=False)
        self.assertTrue(snap["no_trade"])
        self.assertIn("NO TRADE", snap["no_trade_reason"])

    def test_snapshot_contract(self):
        snap = ss.snapshot("balanced", record=False)
        for key in ("rows", "why_number_one", "risk_pathways", "why_others_failed", "per_symbol",
                    "portfolio", "no_trade", "no_trade_reason", "as_of", "age_hours", "config_hash",
                    "engine", "objective", "modes", "strategies"):
            self.assertIn(key, snap)
        if snap["rows"]:
            r = snap["rows"][0]
            self.assertEqual(r["rank"], 1)
            for key in ("p0_model", "p0_conservative", "p_touch", "p_profit", "ev_per_contract",
                        "es95_per_share", "sell_quality", "data_source", "data_ts", "credit_basis",
                        "dte_bucket", "confidence", "spread_pct", "oi"):
                self.assertIn(key, r)
            self.assertEqual(r["data_source"], "schwab")
            w = snap["why_number_one"]
            self.assertEqual(list(w)[:4], ["why_stock", "why_expiration", "why_strike", "why_side"])
            self.assertIn("why_second_is_second", w)
            self.assertNotIn("2026-", w["why_expiration"])       # spelled-out dates only
            # every rejection line is grouped by shape, with a count and the symbols
            for g in snap["why_others_failed"]:
                self.assertIn(g["gate"], ("data", "liquidity", "events", "edge", "tail", "probability"))
                self.assertGreater(g["n"], 0)
                self.assertEqual(g["symbols"], ["SYN"])
            self.assertEqual(sorted(snap["rows"], key=lambda x: x["rank"]), snap["rows"])

    def test_strategy_filter_and_top_n(self):
        snap = ss.snapshot("income", strategy="cash_secured_put", top_n=3, record=False)
        self.assertLessEqual(len(snap["rows"]), 3)
        for r in snap["rows"]:
            self.assertEqual(r["strategy"], "cash_secured_put")

    def test_mode_ordering_is_monotone_in_strictness(self):
        m = self.entry["modes"]
        self.assertLessEqual(m["conservative"]["n_qualified"], m["balanced"]["n_qualified"])
        self.assertLessEqual(m["balanced"]["n_qualified"], m["income"]["n_qualified"])

    def test_detail_for_unscanned_symbol_is_an_error_not_a_crash(self):
        d = ss.detail("NOPE", "balanced")
        self.assertFalse(d["ok"])
        self.assertIn("error", d)

    def test_detail_for_scanned_symbol(self):
        d = ss.detail("SYN", "income")
        self.assertTrue(d["ok"])
        self.assertIn("rows", d)
        self.assertIn("why_others_failed", d)
        self.assertEqual(d["ctx"]["sector"], "Technology")

    def test_predictions_recorded_once_per_day_per_contract(self):
        snap = ss.snapshot("income", record=True)
        n1 = len(ss.predictions())
        self.assertEqual(n1, snap["shown"])
        ss.snapshot("income", record=True)
        self.assertEqual(len(ss.predictions()), n1)
        rec = ss.predictions()[0]
        for key in ("key", "mode", "p0_model", "p0_conservative", "p_touch", "p_profit", "net_credit",
                    "sigma_h", "config_hash", "engine", "expiration", "short_strike", "day"):
            self.assertIn(key, rec)
        self.assertEqual(rec["day"], TODAY.isoformat())

    def test_evidence_table_is_cached_on_disk_and_restored_with_numeric_keys(self):
        cfg, _ = E.config()
        tab = ss.evidence_table("SYN", BARS, cfg)
        p = Path(self.tmp, "sell", "evidence", "SYN.json")
        self.assertTrue(p.exists())
        ss._EVIDENCE_MEM.pop("SYN", None)
        tab2 = ss.evidence_table("SYN", BARS, cfg)
        h = next(iter(tab2["cells"]["all"]))
        self.assertIsInstance(h, int)
        k = next(iter(tab2["cells"]["all"][h]))
        self.assertIsInstance(k, float)
        self.assertEqual(tab2["n_bars"], tab["n_bars"])

    def test_board_persists_and_reloads(self):
        p = Path(self.tmp, "sell", "board.json")
        self.assertTrue(p.exists())
        ss._STATE["symbols"].clear()
        ss._load_board()
        self.assertIn("SYN", ss._STATE["symbols"])

    def test_wide_markets_are_refused_by_the_liquidity_gate(self):
        entry = ss.on_chain("WIDE", _chain(SPOT, TODAY, wide=True), BARS, _ctx())
        self.assertEqual(entry["modes"]["balanced"]["n_qualified"], 0)
        gates = [g["gate"] for g in entry["modes"]["balanced"]["rejection_summary"]]
        self.assertIn("liquidity", gates)
        ss._STATE["symbols"].pop("WIDE", None)

    def test_no_structure_is_listed_twice(self):
        # two configured spread widths rounding to the same listed wing used
        # to yield two identical candidates (and two identical board rows)
        for mode, pm in self.entry["modes"].items():
            keys = [ss.prediction_key(c, mode) for c in pm["qualified"]]
            self.assertEqual(len(keys), len(set(keys)), mode)

    def test_empty_chain_is_ignored(self):
        self.assertIsNone(ss.on_chain("EMPTY", {"chains": {}}, BARS, _ctx()))
        self.assertIsNone(ss.on_chain("EMPTY", _chain(SPOT, TODAY), [], _ctx()))


if __name__ == "__main__":
    unittest.main()
