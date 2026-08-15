"""test_timing.py — Friday 0DTE timing engine acceptance tests (spec §27).

Every unit-testable acceptance test from the spec runs here, offline and
deterministic (synthetic quotes, seeded simulation, temp data dirs). The
mapping to the spec's numbered list is noted on each test. Live-data
behaviors (Schwab wiring, real tape capture) are exercised by
test_http_smoke.py and by the deployed app.

Run:  python3 -m unittest test_timing -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, time as _dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import intraday
import timing_engine as te
import intraday_option_store as tape

ET = ZoneInfo("America/New_York")

# evaluate() hard-blocks whenever the market is closed (§15). The suite
# must be deterministic at any run time, so the harness holds the market
# open for the whole process.
intraday.market_open = lambda now=None: True


def _future_friday(days_min: int = 3) -> str:
    d = date.today() + timedelta(days=days_min)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d.isoformat()


def make_chain(spot=940.0, expiry=None, iv=0.85, quote_age=5, spread=0.10):
    """Normalized-chain factory matching schwab_client._normalize_chain."""
    expiry = expiry or _future_friday()
    strikes = [round(spot + off, 1) for off in
               (-40, -30, -20, -10, -5, 0, 5, 10, 20, 30, 40, 50)]
    calls, puts = [], []
    import math as _m
    for k in strikes:
        dist = abs(k - spot) / spot
        call_mid = max(spot * 0.012 * _m.exp(-dist * 28) + max(spot - k, 0) * 0.9, 0.03)
        put_mid = max(spot * 0.012 * _m.exp(-dist * 28) + max(k - spot, 0) * 0.9, 0.03)
        for rows, mid, kind in ((calls, call_mid, "call"), (puts, put_mid, "put")):
            sign = 1 if kind == "call" else -1
            d_est = 0.5 - sign * (k - spot) / spot * 6.0
            rows.append({"strike": k, "bid": round(mid - spread / 2, 2),
                         "ask": round(mid + spread / 2, 2),
                         "last": round(mid + 0.07, 2),          # last ≠ bid on purpose
                         "bid_size": 12, "ask_size": 9,
                         "volume": 500, "openInterest": 1200,
                         "iv": iv, "delta": max(min(d_est * sign, 1.0), -1.0),
                         "theta": -0.5, "gamma": 0.03, "vega": 0.05,
                         "occ": f"TST {expiry} {kind[0].upper()}{k}",
                         "quote_age_s": quote_age})
    return {"underlying": {"symbol": "TST", "last": spot, "bid": spot - 0.02,
                           "ask": spot + 0.02, "name": "Test Corp"},
            "expirations": [expiry],
            "chains": {expiry: {"calls": calls, "puts": puts}},
            "source": "schwab"}


class Harness:
    """Configure timing_engine against synthetic data in a temp dir."""

    def __init__(self, chain=None, bars=None):
        self.dir = tempfile.mkdtemp(prefix="jerry_timing_")
        self.chain = chain
        self.bars = bars or []
        # Deterministic overlay: no event blocks in tests (a test run that
        # happens to land inside a real FOMC window must not flake).
        overlay = {"events": {"block_windows_min": {"fomc": 0, "cpi": 0, "jobs": 0}},
                   "engine": {"state_cache_seconds": 0}}
        (Path(self.dir) / "thresholds.json").write_text(json.dumps(overlay))
        te.configure(self.dir,
                     chain_fn=lambda sym, exp: self.chain,
                     quote_fn=lambda sym: {"last": (self.chain or {}).get(
                         "underlying", {}).get("last")},
                     intraday_fn=lambda sym: list(self.bars) if sym == "TST" else [],
                     push_fn=None, et_tz=ET)
        te.config(refresh=True)
        te._CLOCK.update({"drift_s": None, "source": None, "ts": 0.0})
        with te._STATE_LOCK:
            te._HYST.clear()
            te._LAST_SEEN.clear()
            te._EVAL_CACHE.clear()


# ── §1 math (acceptance 1, 8) ───────────────────────────────────────────────

class TestMinuteMath(unittest.TestCase):
    def setUp(self):
        Harness()

    def test_two_hours_not_zero_days(self):
        """Acceptance 1: at 2:00pm on expiration Friday, T ≈ 2 hours."""
        now = datetime(2026, 8, 21, 14, 0, tzinfo=ET)
        T = te.time_to_expiry_years(now, date(2026, 8, 21))
        self.assertAlmostEqual(T * 365.0 * 24.0, 2.0, places=6)
        self.assertGreater(T, 0.0)

    def test_daily_boundary_matches_app_convention(self):
        """4pm Thursday → Friday close = exactly 1/365 (the metrics.py
        calendar convention at the daily boundary)."""
        now = datetime(2026, 8, 20, 16, 0, tzinfo=ET)
        T = te.time_to_expiry_years(now, date(2026, 8, 21))
        self.assertAlmostEqual(T, 1.0 / 365.0, places=9)

    def test_early_close_honored(self):
        """Nov 27 2026 (day after Thanksgiving) closes 13:00 ET."""
        now = datetime(2026, 11, 27, 12, 0, tzinfo=ET)
        mins = te.minutes_to_close(now, date(2026, 11, 27))
        self.assertAlmostEqual(mins, 60.0, places=3)

    def test_reprice_intraday_uses_minutes(self):
        rp = te.reprice_intraday(940.0, 948.5, 945.0, "call", 0.55, 120, 90)
        self.assertGreater(rp["premium"], 3.5)          # extrinsic present
        self.assertGreater(rp["premium"], 948.5 - 945.0)
        self.assertLess(rp["theta_per_min"], 0.0)
        self.assertTrue(0.0 <= rp["p_itm_terminal"] <= 1.0)

    def test_expired_collapses_to_intrinsic(self):
        rp = te.reprice_intraday(950.0, 950.0, 945.0, "call", 0.55, 0, 0)
        self.assertAlmostEqual(rp["premium"], 5.0, places=4)

    def test_p_itm_and_p_touch_are_separate(self):
        """Acceptance 8: two quantities, touch ≥ ITM for OTM contracts."""
        iv = 0.9
        pt = te.touch_probability(940.0, 960.0, iv, 240, "call")
        rp = te.reprice_intraday(940.0, 940.0, 960.0, "call", iv, 240, 240)
        self.assertGreater(pt, rp["p_itm_terminal"])

    def test_clock_convention_invariance(self):
        """§1/§12: P(touch) is invariant to the clock convention as long as
        backout and consumption share one clock (σ√T is the invariant)."""
        mid, spot, strike, mins = 5.35, 940.0, 945.0, 180.0
        iv_cal = te.implied_vol_intraday(mid, spot, strike, mins, "call")
        T_cal = te._t_years(mins)
        # A trading-clock convention: tau = minutes / (252 × 390) years.
        T_trad = mins / (252.0 * 390.0)
        iv_trad = iv_cal * (T_cal / T_trad) ** 0.5
        import math
        d_cal = math.log(strike / spot) / (iv_cal * math.sqrt(T_cal))
        d_trad = math.log(strike / spot) / (iv_trad * math.sqrt(T_trad))
        self.assertAlmostEqual(d_cal, d_trad, places=9)

    def test_low_premium_iv_guard(self):
        """§1 [V3]: backing IV out of a 0.03 bid is flagged UNUSABLE."""
        self.assertEqual(te.iv_usability(0.03, 0.08), "unusable_low_premium")
        self.assertEqual(te.iv_usability(2.0, 4.4), "unusable_spread")
        self.assertEqual(te.iv_usability(5.9, 6.1), "ok")


# ── §2 benchmarks (acceptance 2, 3) + Amendment E ───────────────────────────

class TestBenchmarks(unittest.TestCase):
    def _snap(self, ts, bid, last=None, mid=None, beyond=False):
        return {"ts": ts, "bid": bid, "last": last if last is not None else bid + 0.3,
                "mid": mid if mid is not None else bid + 0.1,
                "beyond_strike": beyond}

    def test_four_benchmarks_stay_separate(self):
        """Acceptance 3: raw (last), mid, executable (bid), durable highs
        are distinct values tracked separately."""
        b = tape._bench_new()
        t0 = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
        for i, bid in enumerate([2.0, 2.2, 6.0, 2.1, 2.3]):
            ts = (t0 + timedelta(seconds=30 * i)).isoformat()
            tape._bench_update(b, self._snap(ts, bid), 60.0, None)
        self.assertEqual(b["exec_high"], 6.0)            # single-snapshot spike
        self.assertEqual(b["raw_high"], 6.3)             # last ≠ bid
        self.assertEqual(b["mid_high"], 6.1)
        # The 6.0 printed for ONE 30s snapshot — not durable at 60s.
        self.assertNotEqual(b["durable_high"], 6.0)

    def test_durable_defined_in_seconds(self):
        """Amendment E: durable = bid sustained ≥ 60 SECONDS, robust to
        cadence. A 3-snapshot × 30s run qualifies; the same bid for one
        snapshot never does."""
        b = tape._bench_new()
        t0 = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
        seq = [3.0, 5.0, 5.1, 5.05, 3.2]                 # 5.0+ held 60s (3 snaps)
        for i, bid in enumerate(seq):
            ts = (t0 + timedelta(seconds=30 * i)).isoformat()
            tape._bench_update(b, self._snap(ts, bid), 60.0, None)
        self.assertIsNotNone(b["durable_high"])
        self.assertAlmostEqual(b["durable_high"], 5.0, places=2)
        self.assertEqual(b["exec_high"], 5.1)

    def test_touch_recorded(self):
        b = tape._bench_new()
        ts = datetime(2026, 8, 21, 11, 0, tzinfo=ET).isoformat()
        tape._bench_update(b, self._snap(ts, 4.0, beyond=True), 60.0, None)
        self.assertTrue(b["touched"])
        self.assertEqual(b["touch_ts"], ts)


# ── §9 simulation (acceptance 15, 16) ───────────────────────────────────────

class TestSimulation(unittest.TestCase):
    def setUp(self):
        Harness()
        self.cfg, _ = te.config()

    def _snap(self, **kw):
        base = dict(spot=940.0, strike=965.0, kind="call", iv=0.80,
                    minutes_remaining=240, bid=2.50, spread_frac=0.05,
                    trend_dir=0, widen=False,
                    limits=te._limits_for("income_only", self.cfg),
                    look_interval_min=30, latency_min=3, min_improve_frac=0.08)
        base.update(kw)
        return base

    def test_joint_coherence(self):
        """Acceptance 15: P(touch), P(ITM), PBetter come from one path set
        and cannot contradict: ITM ⊆ touched; PBetter monotone in horizon."""
        s = te.simulate(self._snap(), self.cfg, seed=7)
        self.assertLessEqual(s["p_itm"], s["p_touch"] + 1e-12)
        pb = s["p_better"]
        self.assertLessEqual(pb["h5"], pb["h30"] + 1e-12)
        self.assertLessEqual(pb["h30"], pb["close"] + 1e-12)
        self.assertGreaterEqual(s["expected_extra_raw"], s["expected_extra"] - 1e-12)

    def test_touch_close_to_analytic_when_driftless(self):
        iv = te.implied_vol_intraday(2.55, 940.0, 965.0, 240, "call")
        s = te.simulate(self._snap(iv=iv), self.cfg, seed=11)
        an = te.touch_probability(940.0, 965.0, s["sigma_used"], 240, "call")
        self.assertLess(abs(s["p_touch"] - an), 0.05)

    def test_replay_byte_identical(self):
        """Acceptance 16 / Amendment B: same seed → identical outputs."""
        a = te.simulate(self._snap(), self.cfg, seed=123)
        b = te.simulate(self._snap(), self.cfg, seed=123)
        for k in ("p_touch", "p_itm", "expected_extra", "wait_edge",
                  "capture_mean", "mc_se"):
            self.assertEqual(a[k], b[k], k)

    def test_intent_changes_admissibility_and_wait_edge(self):
        """Acceptance 19: identical market state, different intent →
        different WAIT EDGE (wheel waits more cheaply near danger)."""
        near = self._snap(strike=945.0, bid=5.25,
                          iv=te.implied_vol_intraday(5.35, 940.0, 945.0, 240, "call"))
        inc = te.simulate(near, self.cfg, seed=42)
        whl = te.simulate(dict(near, limits=te._limits_for("wheel_acceptable", self.cfg)),
                          self.cfg, seed=42)
        self.assertGreater(whl["wait_edge"], inc["wait_edge"])

    def test_hazardous_premium_surfaces(self):
        near = self._snap(strike=943.0, bid=6.0,
                          iv=te.implied_vol_intraday(6.1, 940.0, 943.0, 240, "call"))
        s = te.simulate(near, self.cfg, seed=5)
        self.assertGreater(s["expected_extra_raw"], 0.0)
        self.assertGreaterEqual(s["hazardous_premium"], 0.0)

    def test_trend_shading_moves_touch(self):
        """Drift shading toward the strike raises touch probability."""
        base = self._snap(strike=952.0, bid=1.8,
                          iv=te.implied_vol_intraday(1.9, 940.0, 952.0, 240, "call"))
        flat = te.simulate(base, self.cfg, seed=9)
        trend = te.simulate(dict(base, trend_dir=1), self.cfg, seed=9)
        self.assertGreater(trend["p_touch"], flat["p_touch"])


# ── evaluate() end-to-end (acceptance 2, 12, 13, 22) ────────────────────────

class TestEvaluate(unittest.TestCase):
    def test_credit_is_bid_not_last(self):
        """Acceptance 2: current executable credit uses bid, not last."""
        h = Harness(chain=make_chain())
        exp = h.chain["expirations"][0]
        st = te.evaluate("TST", 945.0, "call", exp, contracts=3)
        self.assertIsNone(st.get("blocked"), st.get("reason"))
        row = next(r for r in h.chain["chains"][exp]["calls"] if r["strike"] == 945.0)
        self.assertEqual(st["credit"]["bid"], row["bid"])
        self.assertNotEqual(st["credit"]["bid"], row["last"])

    def test_stale_quote_blocks(self):
        """Acceptance 12: stale quotes can never produce a sell state."""
        h = Harness(chain=make_chain(quote_age=999))
        exp = h.chain["expirations"][0]
        st = te.evaluate("TST", 945.0, "call", exp)
        self.assertEqual(st["state"], "BLOCKED")
        self.assertEqual(st["blocked"]["code"], "stale_quote")

    def test_on_strike_never_high_score(self):
        """Acceptance 13: spot sitting on the strike + income limits →
        never a sell-zone score while limits are violated."""
        h = Harness(chain=make_chain(spot=940.0))
        exp = h.chain["expirations"][0]
        st = te.evaluate("TST", 940.0, "call", exp)
        self.assertIsNone(st.get("blocked"), st.get("reason"))
        self.assertFalse(st["risk"]["admissible"])
        self.assertLessEqual(st["score"], 40)
        self.assertNotIn(st["state"], ("SELL ZONE", "STRONG SELL ZONE"))
        self.assertIn("hazardous", st["reason"])

    def test_market_closed_blocks_before_stale_quote(self):
        """Weekend/after-hours: the block reason is market_closed (calm,
        expected), never stale_quote (alarming) — frozen quotes outside
        the session are the definition of a closed market, not a fault."""
        h = Harness(chain=make_chain(quote_age=94608))   # Friday's close, seen Saturday
        exp = h.chain["expirations"][0]
        was = intraday.market_open
        intraday.market_open = lambda now=None: False
        try:
            st = te.evaluate("TST", 945.0, "call", exp, force=True)
        finally:
            intraday.market_open = was
        self.assertEqual(st["state"], "BLOCKED")
        self.assertEqual(st["blocked"]["code"], "market_closed")
        self.assertIn("stays watched", st["blocked"]["detail"])
        self.assertIn(exp, st["blocked"]["detail"])

    def test_clock_drift_blocks(self):
        """Acceptance 22: drift beyond the bound blocks and says why."""
        h = Harness(chain=make_chain())
        te._CLOCK.update({"drift_s": 90.0, "source": "test", "ts": 9e12})
        exp = h.chain["expirations"][0]
        st = te.evaluate("TST", 945.0, "call", exp)
        self.assertEqual(st["state"], "BLOCKED")
        self.assertEqual(st["blocked"]["code"], "clock_drift")
        self.assertIn("drift", st["reason"])

    def test_wide_spread_blocks(self):
        h = Harness(chain=make_chain(spread=4.0, spot=940.0))
        exp = h.chain["expirations"][0]
        st = te.evaluate("TST", 980.0, "call", exp)     # cheap contract, huge spread
        self.assertEqual(st["state"], "BLOCKED")
        self.assertIn(st["blocked"]["code"], ("bad_spread", "invalid_quote"))

    def test_state_validates_against_schema(self):
        """§35: the emitted state honors engine_state.schema.json."""
        h = Harness(chain=make_chain())
        exp = h.chain["expirations"][0]
        st = te.evaluate("TST", 950.0, "call", exp, contracts=2)
        errs = te.validate_state(st)
        self.assertEqual(errs, [], errs)

    def test_decision_logged_and_replayable(self):
        """Acceptance 16/§35: every displayed decision replays from its
        stored inputs + seed, and the joint outputs match exactly."""
        h = Harness(chain=make_chain())
        exp = h.chain["expirations"][0]
        st = te.evaluate("TST", 950.0, "call", exp)
        rep = te.replay(st["decision_id"])
        self.assertTrue(rep.get("match"), rep)

    def test_layer_badge_present(self):
        """§21: every state names the layer that produced it."""
        h = Harness(chain=make_chain())
        exp = h.chain["expirations"][0]
        st = te.evaluate("TST", 950.0, "call", exp)
        self.assertEqual(st["layer"], "SIMULATION PRIOR")
        self.assertIn("confidence", st)


# ── §34 hysteresis (acceptance 17) ──────────────────────────────────────────

class TestHysteresis(unittest.TestCase):
    def setUp(self):
        Harness()
        self.cfg, _ = te.config()
        with te._STATE_LOCK:
            te._HYST.clear()

    def test_small_wiggle_does_not_flip(self):
        d1, _ = te._apply_hysteresis("k1", "WAIT", 38.0, 0.01, self.cfg)
        self.assertEqual(d1, "WAIT")
        d2, info = te._apply_hysteresis("k1", "GETTING CLOSE", 42.0, 0.01, self.cfg)
        self.assertEqual(d2, "WAIT")                    # 42 is inside the margin
        self.assertTrue(info["held"])

    def test_hard_cross_flips_immediately(self):
        te._apply_hysteresis("k2", "WAIT", 30.0, 0.01, self.cfg)
        d, info = te._apply_hysteresis("k2", "GETTING CLOSE", 55.0, 0.01, self.cfg)
        self.assertEqual(d, "GETTING CLOSE")            # 55 clears 40.5 + margin

    def test_persistence_flips_after_n(self):
        te._apply_hysteresis("k3", "WAIT", 38.0, 0.01, self.cfg)
        te._apply_hysteresis("k3", "GETTING CLOSE", 42.0, 0.01, self.cfg)
        d, _ = te._apply_hysteresis("k3", "GETTING CLOSE", 43.0, 0.01, self.cfg)
        self.assertEqual(d, "GETTING CLOSE")            # persisted 2 evals

    def test_escalations_exempt(self):
        """Acceptance 17 exemption: risk escalations fire immediately."""
        te._apply_hysteresis("k4", "WAIT", 30.0, 0.01, self.cfg)
        d, info = te._apply_hysteresis("k4", "BLOCKED", None, 0.01, self.cfg)
        self.assertEqual(d, "BLOCKED")
        self.assertFalse(info["held"])

    def test_margin_scales_with_mc_se(self):
        """Amendment B: margin ≥ k × MC standard error (in score points)."""
        te._apply_hysteresis("k5", "WAIT", 39.0, 0.20, self.cfg)   # SE 20pp
        d, info = te._apply_hysteresis("k5", "GETTING CLOSE", 52.0, 0.20, self.cfg)
        self.assertEqual(d, "WAIT")                     # margin 2×20=40pts holds it
        self.assertGreaterEqual(info["margin"], 40.0)


# ── §30 portfolio (acceptance 20) + Amendment D ─────────────────────────────

class TestPortfolio(unittest.TestCase):
    def test_shared_shock_sums_per_leg(self):
        """Acceptance 20: the strip's tail scenario equals the sum of
        per-position scenarios under the SHARED shock."""
        exp = _future_friday()
        h = Harness(chain=make_chain(expiry=exp))
        legs = [{"symbol": "TST", "strike": 945.0, "kind": "call", "expiry": exp,
                 "credit": 5.0, "contracts": 2},
                {"symbol": "TST", "strike": 930.0, "kind": "put", "expiry": exp,
                 "credit": 4.0, "contracts": 1}]
        roll = te.portfolio_rollup(legs)
        self.assertEqual(len(roll["legs"]), 2)
        for s in ("-2.0", "2.0"):
            total = roll["shock_table"][s]["all_in"]
            per = sum(l["scenarios"][float(s)]["all_in"] for l in roll["legs"]
                      if "scenarios" in l)
            self.assertAlmostEqual(total, per, delta=2.0)   # per-leg rounding
        self.assertIn("risk_budget_used_pct", roll)

    def test_intent_changes_shock_view(self):
        """Amendment D: a covered call under a +2% rip is capped upside,
        not cash bleeding — wheel all-in ≥ income option-only view."""
        exp = _future_friday()
        h = Harness(chain=make_chain(expiry=exp))
        leg = [{"symbol": "TST", "strike": 945.0, "kind": "call", "expiry": exp,
                "credit": 5.0, "contracts": 1}]
        te.set_intent("TST", "call", "wheel_acceptable")
        wheel = te.portfolio_rollup(leg)
        te.set_intent("TST", "call", "income_only")
        income = te.portfolio_rollup(leg)
        w_up = wheel["legs"][0]["scenarios"][2.0]
        i_up = income["legs"][0]["scenarios"][2.0]
        self.assertGreater(w_up["all_in"], i_up["all_in"])
        self.assertEqual(i_up["all_in"], i_up["option"])


# ── §20 management + §33 final hour (acceptance 21) ─────────────────────────

class TestManagementAndFinalHour(unittest.TestCase):
    def test_tripwire_and_breach(self):
        exp = _future_friday()
        h = Harness(chain=make_chain(expiry=exp, spot=940.0))
        pos = {"symbol": "TST", "strike": 945.0, "kind": "call", "expiry": exp,
               "credit": 2.0, "contracts": 1}
        m = te.manage_position(pos)
        row = next(r for r in h.chain["chains"][exp]["calls"] if r["strike"] == 945.0)
        mid = (row["bid"] + row["ask"]) / 2
        if mid >= 4.0:
            self.assertIn(m["state"], ("TRIPWIRE", "STRIKE THREATENED", "BREACHED"))
        self.assertTrue(m["tripwire_armed"])
        pos2 = {"symbol": "TST", "strike": 935.0, "kind": "call", "expiry": exp,
                "credit": 9.0, "contracts": 1}
        m2 = te.manage_position(pos2)
        self.assertEqual(m2["state"], "BREACHED")

    def test_final_hour_pennies_recommendation(self):
        """Acceptance 21: in the final window with a near strike and a
        pennies cost, the buy-to-close recommendation appears."""
        cfg, _ = te.config()
        rp = {"gamma": 0.08, "delta": 0.4}
        sim = {"p_itm": 0.30, "p_touch": 0.45}
        fh = te._final_hour(spot=514.4, strike=515.0, kind="call", bid=0.04,
                            mid=0.045, mins=25, rp_now=rp, sim=sim, cfg=cfg)
        self.assertTrue(fh["active"])
        self.assertIn("PENNIES", fh["recommend"])
        self.assertTrue(fh["exercise_watch"]["active"])

    def test_final_hour_inactive_outside_window(self):
        cfg, _ = te.config()
        fh = te._final_hour(940.0, 990.0, "call", 1.0, 1.05, 200,
                            {"gamma": 0.01, "delta": 0.1},
                            {"p_itm": 0.05, "p_touch": 0.1}, cfg)
        self.assertIsNone(fh)


# ── §4 fills + §15 events + tape plumbing ───────────────────────────────────

class TestFillsEventsTape(unittest.TestCase):
    def test_log_fill_never_blocks_on_missing_fields(self):
        h = Harness(chain=make_chain())
        out = te.log_fill({"symbol": "TST", "strike": 945.0, "kind": "call",
                           "expiry": h.chain["expirations"][0],
                           "credit": 5.2, "contracts": 2, "mode": "resting"})
        self.assertTrue(out.get("ok"), out)
        out2 = te.log_fill({"symbol": "TST"})           # nulls, no crash
        self.assertTrue(out2.get("ok") or "error" not in out2, out2)
        fills = te.list_fills()
        self.assertGreaterEqual(len(fills), 1)
        self.assertEqual(fills[0]["mode"], "resting")
        self.assertIn("entry_state", fills[0])

    def test_event_context_fomc_widen_and_block(self):
        """§15: events widen ranges near their timestamps; block windows
        are config-driven per event class."""
        h = Harness()
        cfg = {"events": {"range_widen_window_min": 20,
                          "block_windows_min": {"fomc": 15}}}
        fomc_day = datetime(2026, 9, 16, 14, 5, tzinfo=ET)
        ctx = te._event_context(fomc_day, cfg)
        self.assertTrue(any(e["kind"] == "fomc" for e in ctx["events"]))
        self.assertTrue(ctx["widen"])
        self.assertIsNotNone(ctx["block"])
        far = datetime(2026, 9, 16, 10, 0, tzinfo=ET)
        ctx2 = te._event_context(far, cfg)
        self.assertIsNone(ctx2["block"])

    def test_candidates_roundtrip(self):
        h = Harness()
        exp = _future_friday()
        te.add_candidate("MU", exp, "put", 945.0, contracts=3)
        rows = te.list_candidates()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "put")
        te.remove_candidate(rows[0]["key"])
        self.assertEqual(len(te.list_candidates()), 0)

    def test_degradation_ladder(self):
        cfg = {"degrade_ladder": [
            {"when_req_per_min_over": 21, "action": "discovery_paused"},
            {"when_req_per_min_over": 25, "action": "p2_seconds=120"},
            {"when_req_per_min_over": 28, "action": "p1_seconds=60"}]}

        class FakeSC:
            def __init__(self, n):
                self._n = n

            def rate_usage(self):
                return self._n

        tape._SCHWAB_GETTER = lambda: FakeSC(10)
        self.assertIsNone(tape._degradation(cfg))
        tape._SCHWAB_GETTER = lambda: FakeSC(23)
        self.assertEqual(tape._degradation(cfg), "discovery_paused")
        tape._SCHWAB_GETTER = lambda: FakeSC(29)
        self.assertEqual(tape._degradation(cfg), "p1_seconds=60")
        tape._SCHWAB_GETTER = None

    def test_cadence_respects_degradation(self):
        cfg = {"p1_seconds": 30, "p2_seconds": 60, "discovery_seconds": 300}
        self.assertEqual(tape._cadence(1, cfg, None), 30)
        self.assertEqual(tape._cadence(1, cfg, "p1_seconds=60"), 60)
        self.assertEqual(tape._cadence(3, cfg, "discovery_paused"), float("inf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
