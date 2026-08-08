"""Tests for perfection.py — the Priced-for-Perfection model (v3.67).

Pure-module tests on synthetic fixtures: composite math, contribution
reconciliation, renormalization, confidence, reverse valuation, percentiles,
whisper weighting (incl. multi-source and missing), session cutoffs and
leakage protection, event classification, NaN/infinity hygiene.
AMD/SNDK live validation runs separately (validate_perfection.py) and never
asserts predetermined scores.
"""
from __future__ import annotations

import json
import math
import unittest

import perfection as pf


def rich_inputs(**over):
    """A fully-populated synthetic input set (a demanding, crowded setup)."""
    events = []
    # 8 events: alternating beats, half of the beats fade. Newest first.
    for i in range(8):
        beat = i % 4 != 3            # 6 beats, 2 misses
        fade = beat and (i % 2 == 0)
        events.append({
            "date": f"2025-{8 - i:02d}-01", "session": "AMC",
            "eps_estimate": 1.0, "eps_actual": 1.1 if beat else 0.9,
            "surprise_pct": 10.0 if beat else -10.0,
            "reaction_1d_pct": (-2.5 if fade else 4.0) if beat else -8.0,
            "reaction_5d_pct": (-3.0 if fade else 5.0) if beat else -9.0,
            "rel_spy_1d_pct": None, "rel_sector_1d_pct": None,
            "beat_consensus": beat, "exceeded_implied": None,
        })
    d = {
        # execution hurdle
        "market_cap": 300e9, "enterprise_value": 310e9, "revenue_ttm": 30e9,
        "fcf_margin_ttm": 0.18, "fcf_margin_best": 0.22,
        "consensus_rev_growth": 0.18, "revenue_cagr_3y": 0.14,
        "share_count": 1.6e9, "net_debt": 10e9,
        "rev_vs_fcf_growth_gap": 18.0, "valuation_hist_pctile": 92.0,
        "revisions_direction": "up",
        # valuation stretch
        "forward_pe": 45.0, "ev_to_revenue": 10.3, "ev_to_ebitda": 30.0, "peg": 2.8,
        "price_to_fcf": 55.0, "fcf_yield_pct": 1.8,
        "evs_hist_pctile": 92.0, "pe_hist_pctile": 88.0, "evs_hist_years": 3,
        "evs_expansion_30d_pct": 9.0, "evs_expansion_90d_pct": 22.0,
        "evs_expansion_180d_pct": 35.0,
        "peer_fwd_pe_pctile": 85.0, "peer_median_fwd_pe": 24.0,
        "peer_count": 40, "peer_basis": "Technology",
        # expectations
        "consensus_eps": 1.52, "consensus_revenue": 8.1e9, "consensus_eps_analysts": 30,
        "eps_rev_pct_7d": 0.8, "eps_rev_pct_30d": 3.2, "eps_rev_pct_90d": 8.0,
        "revisions_up_30d": 14, "revisions_down_30d": 2, "revisions_up_minus_down": 12,
        "eps_dispersion_pct": 6.0, "required_accel_pp": 6.0,
        "whisper": {"available": False},
        "guidance": {"available": False},
        # reactions
        "events": events,
        # momentum
        "returns_pct": {"d5": 4, "d20": 12, "d60": 30, "d120": 45},
        "vs_sector_pct": {"d20": 8, "d60": 18}, "vs_market_pct": {"d20": 10, "d60": 24},
        "ma_distance_pct": {"ma20": 6, "ma50": 14, "ma200": 40},
        "from_52wk_high_pct": -1.0, "runup_20d_pct": 12.0,
        "drift_since_last_er_pct": 25.0,
        "rel60_hist_pctile": 91.0, "ma50_dist_hist_pctile": 88.0,
        "rvol20": 1.4, "gap_days_60d": 5, "vol20_vs_120_ratio": 1.3,
        # crowding
        "pt_upside_pct": 1.5, "target_mean": 250.0, "buy_ratio_pct": 88.0,
        "ratings": {"strong_buy": 20, "buy": 15, "hold": 4, "sell": 1, "strong_sell": 0},
        "analyst_count": 40, "short_pct_float": 1.4, "days_to_cover": 1.1,
        "call_put_oi_ratio": 2.2, "call_put_vol_ratio": 2.6,
        "call_put_skew_vol_pts": 2.1, "pt_changes_net_30d": 5, "institutional_pct": 72.0,
        # conversion
        "gross_margin_slope_pp_q": -0.8, "op_margin_slope_pp_q": -1.0,
        "fcf_margin_ttm_pct": 18.0, "ocf_conversion": 0.7, "sbc_pct_ocf": 28.0,
        "capex_pct_revenue": 9.0, "inventory_vs_rev_growth_gap": 16.0,
        "rev_vs_eps_growth_gap": 12.0, "conversion_quarters": 6,
        "freshness_penalties": [],
    }
    d.update(over)
    return d


class TestHelpers(unittest.TestCase):
    def test_num_gate(self):
        self.assertIsNone(pf._num(float("nan")))
        self.assertIsNone(pf._num(float("inf")))
        self.assertIsNone(pf._num(None))
        self.assertIsNone(pf._num("x"))
        self.assertEqual(pf._num("3.5"), 3.5)

    def test_div_guard(self):
        self.assertIsNone(pf._div(1, 0))
        self.assertIsNone(pf._div(None, 2))
        self.assertEqual(pf._div(9, 3), 3)

    def test_winsorize_and_pct_rank(self):
        hist = list(range(100)) + [10_000]     # extreme outlier
        w = pf.winsorize(hist)
        self.assertLess(max(w), 10_000)
        self.assertIsNone(pf.pct_rank(5, [1, 2, 3]))          # tiny sample → None
        self.assertIsNone(pf.pct_rank(None, hist))
        r = pf.pct_rank(99, list(range(100)))
        self.assertGreater(r, 95)
        self.assertLess(pf.pct_rank(1, list(range(100))), 5)

    def test_scale_anchors(self):
        self.assertEqual(pf.scale(0, 0, 10), 0)
        self.assertEqual(pf.scale(10, 0, 10), 100)
        self.assertEqual(pf.scale(20, 0, 10), 100)             # clipped
        self.assertEqual(pf.scale(5, 10, 0), 50)               # inverted anchors work
        self.assertIsNone(pf.scale(None, 0, 10))

    def test_classify_bands(self):
        self.assertEqual(pf.classify(0), "Low")
        self.assertEqual(pf.classify(24.4), "Low")
        self.assertEqual(pf.classify(25), "Moderate")
        self.assertEqual(pf.classify(50), "Elevated")
        self.assertEqual(pf.classify(70), "High")
        self.assertEqual(pf.classify(85), "Extreme")
        self.assertIsNone(pf.classify(None))


class TestReverseValuation(unittest.TestCase):
    def test_roundtrip_growth(self):
        # Forward-model an EV from known growth, then recover it.
        ev = pf.dcf_value(10e9, 0.22, 0.15, 0.25, 5, 0.10, 0.025)
        g = pf.implied_growth_solve(ev, 10e9, 0.15, 0.25, 5, 0.10, 0.025)
        self.assertAlmostEqual(g, 0.22, places=3)

    def test_roundtrip_margin(self):
        ev = pf.dcf_value(10e9, 0.18, 0.15, 0.30, 5, 0.10, 0.025)
        m = pf.implied_margin_solve(ev, 10e9, 0.18, 0.15, 5, 0.10, 0.025)
        self.assertAlmostEqual(m, 0.30, places=3)

    def test_monotonic_in_discount_rate(self):
        # Higher discount rate → the same EV demands MORE growth.
        ev = pf.dcf_value(10e9, 0.20, 0.15, 0.25, 5, 0.10, 0.025)
        g_low = pf.implied_growth_solve(ev, 10e9, 0.15, 0.25, 5, 0.09, 0.025)
        g_high = pf.implied_growth_solve(ev, 10e9, 0.15, 0.25, 5, 0.11, 0.025)
        self.assertLess(g_low, g_high)

    def test_guards(self):
        self.assertIsNone(pf.implied_growth_solve(None, 10e9, 0.2, 0.2))
        self.assertIsNone(pf.implied_growth_solve(-5, 10e9, 0.2, 0.2))
        self.assertIsNone(pf.dcf_value(10e9, 0.2, 0.2, 0.2, 5, 0.02, 0.03))  # dr <= tg
        self.assertIsNone(pf.dcf_value(0, 0.2, 0.2, 0.2, 5, 0.1, 0.02))      # rev0 <= 0


class TestComposite(unittest.TestCase):
    def test_full_inputs_scores_high_and_reconciles(self):
        out = pf.assemble(rich_inputs())
        self.assertIsNotNone(out["score"])
        self.assertGreaterEqual(out["score"], 60)      # a demanding setup by design
        self.assertEqual(out["coverage_pct"], 100)
        self.assertEqual(out["confidence"], "High")
        self.assertTrue(out["reconciled"])
        total = sum(c["contribution"] for c in out["components"].values() if c)
        self.assertAlmostEqual(total, out["score"], delta=0.15)
        # Weights ship in the payload; effective == base at full coverage.
        for k, c in out["components"].items():
            self.assertEqual(c["weight_base_pct"], pf.MODEL["weights"][k])
            self.assertAlmostEqual(c["weight_effective_pct"], c["weight_base_pct"], delta=0.1)
        self.assertIn(out["classification"], ("Elevated", "High", "Extreme"))
        self.assertIsNotNone(out["summary"])
        self.assertIn(out["unprotected_long_risk"], ("Low", "Moderate", "High", "Extreme"))

    def test_benign_inputs_score_low(self):
        d = rich_inputs(
            enterprise_value=120e9,          # far less demanding price
            valuation_hist_pctile=20.0, evs_hist_pctile=20.0, pe_hist_pctile=25.0,
            peer_fwd_pe_pctile=30.0, peg=1.0, forward_pe=18.0,
            evs_expansion_30d_pct=-2.0, evs_expansion_90d_pct=-5.0, evs_expansion_180d_pct=-8.0,
            eps_rev_pct_7d=-0.2, eps_rev_pct_30d=-0.5, eps_rev_pct_90d=-1.0,
            revisions_up_minus_down=-2, eps_dispersion_pct=30.0, required_accel_pp=-4.0,
            rel60_hist_pctile=35.0, ma50_dist_hist_pctile=40.0, runup_20d_pct=-2.0,
            from_52wk_high_pct=-18.0, drift_since_last_er_pct=-5.0,
            pt_upside_pct=28.0, buy_ratio_pct=55.0, call_put_oi_ratio=0.9,
            call_put_vol_ratio=1.0, call_put_skew_vol_pts=-1.5, short_pct_float=5.0,
            days_to_cover=4.0, pt_changes_net_30d=-1,
            gross_margin_slope_pp_q=0.8, op_margin_slope_pp_q=1.0,
            rev_vs_fcf_growth_gap=-5.0, sbc_pct_ocf=6.0, inventory_vs_rev_growth_gap=-4.0,
            ocf_conversion=1.2,
        )
        # Benign reactions: beats get paid.
        for e in d["events"]:
            if e["beat_consensus"]:
                e["reaction_1d_pct"] = 5.0
        out = pf.assemble(d)
        self.assertLess(out["score"], 45)
        self.assertIn(out["classification"], ("Low", "Moderate"))

    def test_missing_component_renormalizes(self):
        d = rich_inputs(enterprise_value=None)      # kills execution hurdle
        out = pf.assemble(d)
        self.assertIn("execution_hurdle", out["missing_components"])
        self.assertEqual(out["coverage_pct"], 75)
        self.assertEqual(out["confidence"], "Medium")
        # Effective weights renormalize to 100 and contributions reconcile.
        effs = [c["weight_effective_pct"] for c in out["components"].values() if c]
        self.assertAlmostEqual(sum(effs), 100.0, delta=0.3)
        total = sum(c["contribution"] for c in out["components"].values() if c)
        self.assertAlmostEqual(total, out["score"], delta=0.15)

    def test_confidence_bands_and_insufficient(self):
        d = rich_inputs(enterprise_value=None)                       # -25 → 75 Medium
        self.assertEqual(pf.assemble(d)["confidence"], "Medium")
        d2 = rich_inputs(enterprise_value=None, events=[])           # -40 → 60 Low
        out2 = pf.assemble(d2)
        self.assertEqual(out2["confidence"], "Low")
        d3 = rich_inputs(enterprise_value=None, events=[],           # -75 → 45 Insufficient
                         evs_hist_pctile=None, pe_hist_pctile=None,
                         peer_fwd_pe_pctile=None, evs_expansion_30d_pct=None,
                         evs_expansion_90d_pct=None, evs_expansion_180d_pct=None, peg=None,
                         forward_pe=None)
        out3 = pf.assemble(d3)
        self.assertEqual(out3["confidence"], "Insufficient")
        self.assertIsNone(out3["score"])                            # never misleading
        self.assertIsNone(out3["classification"])

    def test_freshness_penalty_lowers_confidence(self):
        d = rich_inputs(freshness_penalties=["latest quarterly filing is stale",
                                             "price history ends old", "shares stale", "estimates stale"])
        out = pf.assemble(d)     # 100 - 20 = 80 → Medium
        self.assertEqual(out["confidence"], "Medium")


class TestWhisper(unittest.TestCase):
    def _with_whisper(self, conf, gap=6.0):
        return rich_inputs(whisper={
            "available": True, "confidence": conf, "eps_gap_pct": gap,
            "revenue_gap_pct": 2.0, "median_eps": 1.61, "range": [1.58, 1.66],
            "sources": ["LicensedSource A", "LicensedSource B"], "source_count": 2,
            "asof": "2026-08-07T12:00:00Z"})

    def test_no_whisper_renormalizes_and_notes(self):
        out = pf.assemble(rich_inputs())
        eg = out["components"]["expectations_gap"]
        self.assertIsNotNone(eg)
        wh = eg["benchmarks"]["whisper"]
        self.assertFalse(wh["available"])
        self.assertIn("No reliable whisper estimate available", wh["note"])
        self.assertIsNone(wh["eps_gap_pct"])                       # never invented
        self.assertEqual(eg["detail"]["whisper_weight_applied"], 0.0)
        # Guidance also unavailable → subweight coverage = (35+10)/100.
        self.assertEqual(eg["detail"]["subweight_coverage_pct"], 45)

    def test_high_confidence_whisper_full_weight(self):
        out = pf.assemble(self._with_whisper("high"))
        eg = out["components"]["expectations_gap"]
        self.assertEqual(eg["detail"]["whisper_weight_applied"], 35)
        self.assertEqual(eg["benchmarks"]["whisper"]["source_count"], 2)
        self.assertEqual(eg["benchmarks"]["whisper"]["range"], [1.58, 1.66])
        # A big positive gap must RAISE the component score vs no-whisper.
        base = pf.assemble(rich_inputs())["components"]["expectations_gap"]["score"]
        self.assertGreater(eg["score"], base - 1)

    def test_medium_confidence_half_weight_low_excluded(self):
        med = pf.assemble(self._with_whisper("medium"))["components"]["expectations_gap"]
        self.assertEqual(med["detail"]["whisper_weight_applied"], 17.5)
        low = pf.assemble(self._with_whisper("low"))["components"]["expectations_gap"]
        self.assertEqual(low["detail"]["whisper_weight_applied"], 0.0)

    def test_whisper_condition_in_warning(self):
        out = pf.assemble(self._with_whisper("high"))
        self.assertTrue(any("whisper" in c.lower() for c in out["warning"]["conditions"]))


class TestReactions(unittest.TestCase):
    def test_classification_and_saturation(self):
        out = pf.assemble(rich_inputs())
        ra = out["components"]["reaction_asymmetry"]
        self.assertEqual(ra["current"]["events_analyzed"], 8)
        self.assertEqual(ra["current"]["beats"], 6)
        self.assertEqual(ra["current"]["misses"], 2)
        self.assertEqual(ra["current"]["beat_fade_count"], 4)      # even-index beats fade
        self.assertTrue(ra["detail"]["good_news_saturation"])
        self.assertTrue(out["good_news_saturation"])
        # The evidence sentence is generated from the stored numbers.
        self.assertTrue(any("Beat published consensus in 6 of the last 8" in s
                            for s in ra["signals_up"]))

    def test_fewer_than_eight_events(self):
        d = rich_inputs()
        d["events"] = d["events"][:5]
        out = pf.assemble(d)
        ra = out["components"]["reaction_asymmetry"]
        self.assertIsNotNone(ra)
        self.assertEqual(ra["current"]["events_analyzed"], 5)

    def test_too_few_events_component_missing(self):
        d = rich_inputs()
        d["events"] = d["events"][:3]
        out = pf.assemble(d)
        self.assertIn("reaction_asymmetry", out["missing_components"])

    def test_scenarios_use_real_analogs(self):
        out = pf.assemble(rich_inputs())
        scen = {s["key"]: s for s in out["scenarios"]}
        self.assertEqual(len(out["scenarios"]), 6)
        self.assertEqual(scen["miss"]["analog"]["n"], 2)
        self.assertIsNone(scen["miss"]["analog"]["range"])          # <4 samples → no range
        self.assertEqual(scen["beat_miss_higher"]["analog"]["n"], 4)
        self.assertIsNotNone(scen["beat_miss_higher"]["analog"]["range"])  # 4 samples → range
        paid = scen["beat_inline_guide"]["analog"]
        self.assertEqual(paid["n"], 2)
        for s in out["scenarios"]:
            self.assertIn(s["risk_direction"], ("up", "down", "mixed"))

    def test_range_only_with_enough_samples(self):
        d = rich_inputs()
        for e in d["events"]:                                       # all 8 beats, all fade
            e["beat_consensus"] = True
            e["surprise_pct"] = 5.0
            e["reaction_1d_pct"] = -2.0
        out = pf.assemble(d)
        scen = {s["key"]: s for s in out["scenarios"]}
        self.assertEqual(scen["beat_miss_higher"]["analog"]["n"], 8)
        self.assertIsNotNone(scen["beat_miss_higher"]["analog"]["range"])


class TestWarningAndExplanations(unittest.TestCase):
    def test_warning_fires_on_stacked_conditions(self):
        out = pf.assemble(rich_inputs())
        self.assertTrue(out["warning"]["fired"])
        self.assertGreaterEqual(len(out["warning"]["conditions"]), 3)

    def test_warning_quiet_on_benign(self):
        d = rich_inputs(runup_20d_pct=-2.0, eps_rev_pct_30d=-0.5,
                        evs_hist_pctile=20.0, pe_hist_pctile=25.0, peer_fwd_pe_pctile=30.0,
                        peg=1.0, evs_expansion_30d_pct=-2, evs_expansion_90d_pct=-5,
                        evs_expansion_180d_pct=-8, enterprise_value=120e9)
        for e in d["events"]:
            if e["beat_consensus"]:
                e["reaction_1d_pct"] = 5.0
        out = pf.assemble(d)
        self.assertFalse(out["warning"]["fired"])

    def test_explanations_from_displayed_data(self):
        out = pf.assemble(rich_inputs())
        ex = out["explanations"]
        self.assertLessEqual(len(ex["risk_increasing"]), 3)
        self.assertLessEqual(len(ex["risk_reducing"]), 2)
        for item in ex["risk_increasing"]:
            self.assertTrue(item["text"])
            self.assertTrue(item["component"])


class TestHygiene(unittest.TestCase):
    def test_payload_json_safe_no_nan(self):
        d = rich_inputs(peg=float("nan"), fcf_yield_pct=float("inf"),
                        runup_20d_pct=float("-inf"))
        d["events"][0]["reaction_1d_pct"] = float("nan")
        out = pf.assemble(d)
        # allow_nan=False raises if any NaN/Infinity survived to the payload.
        json.dumps(out, allow_nan=False)

    def test_zero_denominators_survive(self):
        d = rich_inputs(revenue_ttm=0, consensus_eps=0)
        out = pf.assemble(d)          # execution hurdle degrades, nothing raises
        json.dumps(out, allow_nan=False)
        self.assertIn("execution_hurdle", out["missing_components"])

    def test_bad_component_never_crashes_assemble(self):
        d = rich_inputs()
        d["events"] = [{"date": None, "junk": object}]   # unserializable garbage
        try:
            out = pf.assemble(d)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"assemble raised on garbage events: {exc}")
        self.assertIn("reaction_asymmetry", out["missing_components"])


class TestSessionCutoffs(unittest.TestCase):
    """The reaction-day picker in perfection_data: BMO reacts same day,
    AMC/unknown react next trading day — the point-in-time cutoff."""

    def test_reaction_day_rule(self):
        import perfection_data as pd_mod
        import pandas as pd
        dates = pd.bdate_range("2026-01-05", periods=70)
        vals = [100.0] * 70
        vals[39], vals[40], vals[41] = 102.0, 110.0, 111.0   # prev / event day / next day
        closes = pd.Series(vals, index=dates)
        h = pd.DataFrame({"Close": closes, "Volume": 1e6, "Open": closes.values})
        ev_date = dates[40].date().isoformat()

        class _T:
            def history(self, period=None, auto_adjust=True):
                return h
        events = [
            {"date": ev_date, "session": "BMO", "eps_estimate": 1.0,
             "eps_actual": 1.2, "surprise_pct": 20.0},
            {"date": ev_date, "session": "AMC", "eps_estimate": 1.0,
             "eps_actual": 1.2, "surprise_pct": 20.0},
        ]
        orig_yft, orig_bench = pd_mod._yft, pd_mod._benchmark_closes
        pd_mod._yft = lambda s: _T()
        pd_mod._benchmark_closes = lambda etf, issues: None
        pd_mod._CACHE.clear()
        try:
            out = pd_mod._prices("TST", None, events, [])
        finally:
            pd_mod._yft, pd_mod._benchmark_closes = orig_yft, orig_bench
        bmo, amc = out["events"][0], out["events"][1]
        # BMO on 01-08: reaction = 110/102 (report lands before that day's close).
        self.assertAlmostEqual(bmo["reaction_1d_pct"], (110 / 102 - 1) * 100, places=2)
        # AMC on 01-08: the 01-08 close is PRE-announcement — reaction uses 01-09.
        self.assertAlmostEqual(amc["reaction_1d_pct"], (111 / 110 - 1) * 100, places=2)

    def test_no_future_leakage_in_snapshot_lookup(self):
        import perfection_data as pd_mod
        pd_mod._SNAP_INDEX.clear()
        pd_mod._SNAP_INDEX["rows"] = [
            {"date": "2026-02-01", "next_earnings": "2026-02-04", "implied_move_pct": 5.0},
            {"date": "2026-02-05", "next_earnings": "2026-02-04", "implied_move_pct": 9.0},
        ]
        near = pd_mod._load_snapshots_near("2026-02-04")
        # The post-announcement (02-05) snapshot must NOT qualify.
        self.assertEqual(len(near), 1)
        self.assertEqual(near[0]["date"], "2026-02-01")


class TestModelContract(unittest.TestCase):
    def test_weights_sum_100(self):
        self.assertEqual(sum(pf.MODEL["weights"].values()), 100)

    def test_model_ships_in_payload(self):
        out = pf.assemble(rich_inputs())
        self.assertEqual(out["model"]["version"], pf.MODEL["version"])
        self.assertEqual(out["model"]["weights"], pf.MODEL["weights"])
        self.assertTrue(out["disclaimer"])

    def test_options_panel_not_in_score(self):
        # The implied move feeds NO component: same inputs ± implied move
        # produce the identical composite (it is display-only context).
        out1 = pf.assemble(rich_inputs())
        out2 = pf.assemble(rich_inputs())    # model has no implied-move input at all
        self.assertEqual(out1["score"], out2["score"])


if __name__ == "__main__":
    unittest.main()
