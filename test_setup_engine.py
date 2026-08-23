"""Tests for setup_engine.py — the recommendation engine.

This engine can lose real money in a way the rest of the app cannot, because
its whole job is to say "sell closer to the money than you normally would".
The tests are therefore weighted toward the ways that could be WRONG rather
than the ways it works:

  - it must never widen the delta band without a real, measured sample
  - it must never widen it on a sample that does not beat the baseline
  - it must never widen it when the model disagrees with the measurement
  - it must size on the Wilson LOWER bound, not the point estimate
  - gamma exposure must never open a trade, only shrink or stop one
  - every widening must be traceable to a distance, an n and a keep rate

The single most important test in this file is
`test_the_delta_is_not_derived_from_the_target_it_is_solved_for`, which
guards against the circularity the first version of this engine shipped
with: mapping a measured keep rate to an "implied delta" of (1 - keep)
returns (1 - target) whatever the data says. It looks like analysis and
computes nothing.
"""

import unittest

import setup_engine as SE


def measured(cond, base=None, ):
    return SE.measured_touch(cond, base)


CALM = {3.0: {"rate": 22, "n": 120}, 5.0: {"rate": 11, "n": 120},
        8.0: {"rate": 4, "n": 120}}
ORDINARY = {3.0: {"rate": 34, "n": 2000}, 5.0: {"rate": 21, "n": 2000},
            8.0: {"rate": 11, "n": 2000}}


def contract(k, delta, bid, spot=100.0, side="call", oi=800, spread=4.0,
             liq=True, ev=None):
    return {
        "strike": k, "side": side, "delta": delta, "bid": bid, "ask": bid * 1.04,
        "dist_pct": (k / spot - 1.0) * 100.0,
        "credit_exec": bid, "credit_basis": "bid (resting-order floor)",
        "ev_per_contract": (bid * 100 * 0.18) if ev is None else ev,
        "ev_per_tail": 0.35, "es5_per_share": 2.1,
        "p_itm_model": max(2.0, delta * 100 * 0.55),
        "p_touch_model": delta * 100 * 0.9,
        "prem_pct_collateral": bid / spot * 100, "annualized_pct": 12.0,
        "spread_pct": spread, "oi": oi, "liquidity_ok": liq,
        "liquidity_notes": [] if liq else ["open interest below the floor"],
        "breakeven": k + bid, "collateral": spot * 100,
    }


LADDER = [contract(103, 0.30, 1.10), contract(106, 0.22, 0.72),
          contract(108, 0.16, 0.48), contract(112, 0.09, 0.24)]


class TestTheWilsonBound(unittest.TestCase):
    """The engine sizes on the lower bound. Nine wins from ten tries is a 90%
    point estimate and a 60% lower bound; the second number is the one that
    should decide how much money is at risk."""

    def test_a_small_sample_is_heavily_discounted(self):
        self.assertLess(SE.wilson_low(9, 10), 65.0)

    def test_a_large_sample_is_barely_discounted(self):
        self.assertGreater(SE.wilson_low(900, 1000), 87.0)

    def test_the_bound_is_always_below_the_point_estimate(self):
        for hits, n in ((1, 2), (5, 10), (50, 100), (500, 1000)):
            self.assertLess(SE.wilson_low(hits, n), hits / n * 100.0 + 1e-9)

    def test_a_degenerate_sample_is_none_not_a_number(self):
        self.assertIsNone(SE.wilson_low(0, 0))
        self.assertIsNone(SE.wilson_low(5, 2))


class TestTheEngineRefusesToWidenWithoutEvidence(unittest.TestCase):
    """Every one of these must return Jerry's default band. An engine that
    quietly sizes up on thin data does not improve a win rate; it just takes
    longer to find out."""

    def test_no_measured_history_at_all(self):
        c = SE.delta_ceiling(measured({}, {}))
        self.assertFalse(c["raised"])
        self.assertEqual(c["band"], SE.DEFAULT_DELTA_BAND)
        self.assertIsNone(c["min_distance_pct"])

    def test_a_sample_too_small_to_mean_anything(self):
        thin = measured({5.0: {"rate": 4, "n": 11}}, {5.0: {"rate": 30, "n": 900}})
        self.assertFalse(thin["usable"])
        self.assertIn("11", thin["reason"])
        c = SE.delta_ceiling(thin)
        self.assertFalse(c["raised"])
        self.assertEqual(c["band"], SE.DEFAULT_DELTA_BAND)

    def test_a_state_that_is_no_calmer_than_ordinary(self):
        flat = measured({5.0: {"rate": 30, "n": 400}}, {5.0: {"rate": 31, "n": 2000}})
        self.assertTrue(flat["usable"])
        self.assertFalse(flat["calmer_than_usual"])
        c = SE.delta_ceiling(flat)
        self.assertFalse(c["raised"])
        self.assertEqual(c["basis"], "measured-no-edge")

    def test_a_state_that_is_measurably_WORSE_than_ordinary(self):
        wild = measured({5.0: {"rate": 45, "n": 400}}, {5.0: {"rate": 21, "n": 2000}})
        c = SE.delta_ceiling(wild)
        self.assertFalse(c["raised"])

    def test_the_model_disagreeing_blocks_the_widening(self):
        """Two methods, and the trade only gets the benefit both agree on."""
        c = SE.delta_ceiling(measured(CALM, ORDINARY), model_touch_pct=40.0)
        self.assertFalse(c["raised"])
        self.assertEqual(c["basis"], "model-disagrees")
        self.assertEqual(c["band"], SE.DEFAULT_DELTA_BAND)

    def test_the_model_agreeing_allows_it(self):
        c = SE.delta_ceiling(measured(CALM, ORDINARY), model_touch_pct=9.0)
        self.assertTrue(c["raised"])

    def test_even_the_widest_measured_distance_falling_short(self):
        near = measured({2.0: {"rate": 40, "n": 300}, 4.0: {"rate": 30, "n": 300}},
                        {2.0: {"rate": 55, "n": 2000}, 4.0: {"rate": 45, "n": 2000}})
        c = SE.delta_ceiling(near)
        self.assertFalse(c["raised"])
        self.assertEqual(c["basis"], "measured-below-target")

    def test_the_default_band_is_the_rule_that_already_works(self):
        self.assertEqual(SE.DEFAULT_DELTA_BAND, (0.15, 0.22))


class TestTheDeltaIsSolvedForNotAssumed(unittest.TestCase):
    def test_the_delta_is_not_derived_from_the_target_it_is_solved_for(self):
        """THE test in this file.

        The first version of this engine mapped a measured keep rate to an
        "implied delta" of (1 - keep). That can only ever return
        (1 - target), whatever the data says, because the target is the
        input — it looks like analysis and computes nothing.

        The fix is that the measurement produces a DISTANCE, and the market
        independently quotes a delta at that distance. Two different calm
        states must therefore produce two different distances.
        """
        calmer = measured({3.0: {"rate": 10, "n": 200}, 5.0: {"rate": 4, "n": 200}},
                          {3.0: {"rate": 34, "n": 2000}, 5.0: {"rate": 21, "n": 2000}})
        rowdier = measured({3.0: {"rate": 26, "n": 200}, 5.0: {"rate": 16, "n": 200},
                            8.0: {"rate": 7, "n": 200}},
                           {3.0: {"rate": 40, "n": 2000}, 5.0: {"rate": 30, "n": 2000},
                            8.0: {"rate": 18, "n": 2000}})
        a = SE.required_distance(calmer)
        b = SE.required_distance(rowdier)
        self.assertTrue(a["ok"] and b["ok"])
        self.assertLess(a["distance_pct"], b["distance_pct"],
                        "a calmer state must need LESS distance for the same "
                        "target — if both return the same number the engine is "
                        "reading its own input back")

    def test_the_required_distance_is_interpolated_not_rounded_out(self):
        r = SE.required_distance(measured(CALM, ORDINARY))
        self.assertTrue(r["interpolated"])
        self.assertGreater(r["distance_pct"], 5.0)
        self.assertLess(r["distance_pct"], 8.0)

    def test_the_target_win_rate_is_held_constant_while_the_strike_moves(self):
        """The whole promise: more premium at the SAME measured odds."""
        for target in (80.0, 85.0, 90.0):
            r = SE.required_distance(measured(CALM, ORDINARY), target_win_pct=target)
            self.assertTrue(r["ok"])
            self.assertAlmostEqual(r["keep_pct_low"], target, places=6)

    def test_a_higher_target_demands_more_distance(self):
        near = SE.required_distance(measured(CALM, ORDINARY), target_win_pct=80.0)
        far = SE.required_distance(measured(CALM, ORDINARY), target_win_pct=90.0)
        self.assertLess(near["distance_pct"], far["distance_pct"])


class TestTheDistanceFloorBinds(unittest.TestCase):
    def test_a_low_delta_strike_inside_the_measured_distance_is_refused(self):
        """The measurement, not the quote, is the promise being made. A
        strike the market happens to price cheaply but which sits inside the
        distance history says is safe is not eligible."""
        ceiling = SE.delta_ceiling(measured(CALM, ORDINARY))
        self.assertAlmostEqual(ceiling["min_distance_pct"], 5.98, places=1)
        # 103 is only 3% out and priced at 0.30 delta — inside the band, but
        # inside the floor too.
        out = SE.recommend("T", 100.0, "call", "2026-09-18", 26, LADDER,
                           SE.directional_bias(), ceiling)
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(abs(out["strike"] / 100.0 - 1.0) * 100.0,
                                ceiling["min_distance_pct"])

    def test_without_a_floor_the_delta_band_alone_governs(self):
        ceiling = SE.delta_ceiling(measured({}, {}))
        out = SE.recommend("T", 100.0, "call", "2026-09-18", 26, LADDER,
                           SE.directional_bias(), ceiling)
        self.assertTrue(out["ok"])
        self.assertLessEqual(abs(out["delta"]), SE.DEFAULT_DELTA_BAND[1] + 1e-9)

    def test_the_hard_cap_is_never_exceeded(self):
        ceiling = SE.delta_ceiling(measured(CALM, ORDINARY))
        self.assertLessEqual(ceiling["band"][1], SE.MAX_DELTA_CEILING)
        rich = LADDER + [contract(101, 0.60, 2.40)]
        out = SE.recommend("T", 100.0, "call", "2026-09-18", 26, rich,
                           SE.directional_bias(), ceiling)
        self.assertLessEqual(abs(out["delta"]), SE.MAX_DELTA_CEILING)


class TestGammaNeverOpensATrade(unittest.TestCase):
    SUPPORTING = {"ok": True, "summary": {"regime": "long"},
                  "profile": {"flip": 96.0},
                  "strikes": [{"strike": 105.0, "net_gex": 9e6},
                              {"strike": 110.0, "net_gex": 1e6}]}
    # Spot ABOVE the flip, so dealers are damping overall, but the specific
    # path to this strike runs through negative gamma. That is `opposes`:
    # a worse trade, not a refused one.
    OPPOSING = {"ok": True, "summary": {"regime": "short"},
                "profile": {"flip": 96.0},
                "strikes": [{"strike": 105.0, "net_gex": -9e6},
                            {"strike": 110.0, "net_gex": -1e6}]}
    # Spot BELOW the flip AND negative gamma on the path: the accelerating
    # case, which is refused outright.
    VETO = {"ok": True, "summary": {"regime": "short"},
            "profile": {"flip": 104.0},
            "strikes": [{"strike": 103.0, "net_gex": -9e6}]}

    def test_supportive_gamma_does_not_widen_the_band(self):
        """Gamma is a structure modifier, never a reason to take more risk."""
        base = SE.delta_ceiling(measured({}, {}))
        g = SE.gex_context(self.SUPPORTING, 100.0, 106.0, "call")
        self.assertEqual(g["verdict"], "supports")
        after = SE.apply_gex_to_ceiling(base, g)
        self.assertEqual(after["band"], base["band"])

    def test_opposing_gamma_pulls_the_band_back(self):
        wide = SE.delta_ceiling(measured(CALM, ORDINARY))
        g = SE.gex_context(self.OPPOSING, 100.0, 106.0, "call")
        self.assertEqual(g["verdict"], "opposes")
        after = SE.apply_gex_to_ceiling(wide, g)
        self.assertLess(after["band"][1], wide["band"][1])

    def test_a_negative_gamma_path_below_the_flip_is_a_veto(self):
        """Below the flip dealers amplify moves. With negative gamma also
        sitting on the path, an adverse move can accelerate through the
        trade — the one case the engine refuses outright."""
        g = SE.gex_context(self.VETO, 100.0, 106.0, "call")
        self.assertEqual(g["verdict"], "veto")
        after = SE.apply_gex_to_ceiling(SE.delta_ceiling(measured({}, {})), g)
        self.assertTrue(after["vetoed"])

    def test_the_veto_needs_BOTH_conditions_not_either(self):
        """Negative gamma alone, above the flip, is a worse trade — not a
        refused one. Conflating the two would refuse ordinary setups."""
        below_flip_only = SE.gex_context(
            {"ok": True, "summary": {"regime": "short"}, "profile": {"flip": 104.0},
             "strikes": [{"strike": 103.0, "net_gex": 9e6}]},   # POSITIVE wall
            100.0, 106.0, "call")
        self.assertNotEqual(below_flip_only["verdict"], "veto")
        neg_only = SE.gex_context(self.OPPOSING, 100.0, 106.0, "call")
        self.assertEqual(neg_only["verdict"], "opposes")

    def test_a_vetoed_setup_is_refused_not_discounted(self):
        out = SE.recommend("T", 100.0, "call", "2026-09-18", 26, LADDER,
                           SE.directional_bias(), SE.delta_ceiling(measured({}, {})),
                           gex_block=self.VETO)
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("vetoed"))

    def test_no_gamma_data_is_said_out_loud_not_assumed_benign(self):
        g = SE.gex_context(None, 100.0, 106.0, "call")
        self.assertEqual(g["verdict"], "unknown")
        self.assertFalse(g["available"])
        self.assertIn("not part of this recommendation", g["note"])

    def test_gamma_only_counts_on_the_path_between_spot_and_the_strike(self):
        """A wall beyond the strike, or behind spot, is not on the journey
        the trade actually depends on."""
        far = {"ok": True, "summary": {"regime": "long"}, "profile": {"flip": 96.0},
               "strikes": [{"strike": 130.0, "net_gex": 9e6}]}
        g = SE.gex_context(far, 100.0, 106.0, "call")
        self.assertEqual(g["verdict"], "neutral")


class TestDirectionalBias(unittest.TestCase):
    def test_it_never_becomes_a_price_forecast(self):
        b = SE.directional_bias(range_block={"pos": 92})
        self.assertIn(b["lean"], ("fade_up", "fade_down", None))
        joined = " ".join(b["why"]).lower()
        for banned in ("will rise", "will fall", "target price", "should go"):
            self.assertNotIn(banned, joined)

    def test_layers_agreeing_is_recorded_as_agreement(self):
        b = SE.directional_bias(
            range_block={"pos": 92},
            streak_block={"streak_dir": 1, "streak_count": 6, "longest_up": 7},
            swing_block={"direction": "up",
                         "maturity": {"code": "BEYOND ITS NORMAL SIZE"},
                         "cohort": {"n": 20}})
        self.assertEqual(b["lean"], "fade_up")
        self.assertEqual(b["agreement"], 100.0)
        self.assertFalse(b["conflict"])

    def test_layers_disagreeing_is_recorded_as_conflict(self):
        b = SE.directional_bias(
            range_block={"pos": 92},                       # stretched up
            swing_block={"direction": "down",              # stretched down
                         "maturity": {"code": "BEYOND ITS NORMAL SIZE"},
                         "cohort": {"n": 20}})
        self.assertTrue(b["conflict"])
        self.assertLess(b["agreement"], 100.0)

    def test_nothing_stretched_is_no_lean_rather_than_a_coin_flip(self):
        b = SE.directional_bias(range_block={"pos": 50})
        self.assertIsNone(b["lean"])
        self.assertEqual(b["votes"], [])

    def test_a_streak_is_weighed_against_this_stocks_own_record(self):
        """Four up days is unremarkable for one stock and a record for
        another, so the weight is relative, not absolute."""
        modest = SE.directional_bias(
            streak_block={"streak_dir": 1, "streak_count": 4, "longest_up": 14})
        extreme = SE.directional_bias(
            streak_block={"streak_dir": 1, "streak_count": 4, "longest_up": 4})
        mw = modest["votes"][0]["weight"] if modest["votes"] else 0.0
        ew = extreme["votes"][0]["weight"] if extreme["votes"] else 0.0
        self.assertLess(mw, ew)


class TestTheRecommendation(unittest.TestCase):
    def _rec(self, **kw):
        ceiling = kw.pop("ceiling", None) or SE.delta_ceiling(measured(CALM, ORDINARY))
        bias = kw.pop("bias", None) or SE.directional_bias(
            range_block={"pos": 92},
            streak_block={"streak_dir": 1, "streak_count": 6, "longest_up": 7})
        return SE.recommend("T", 100.0, "call", "2026-09-18", 26,
                            kw.pop("contracts", LADDER), bias, ceiling, **kw)

    def test_it_names_the_side_expiration_strike_delta_and_credit(self):
        r = self._rec()
        for k in ("action", "expiration", "strike", "delta", "credit", "dte"):
            self.assertIsNotNone(r[k], k)
        self.assertEqual(r["action"], "Sell a call")

    def test_the_credit_is_the_bid_not_the_mid(self):
        """The only number a resting seller is actually promised."""
        r = self._rec()
        self.assertIn("bid", r["credit_basis"])

    def test_it_reports_both_the_measured_and_the_model_probability(self):
        r = self._rec()
        self.assertIsNotNone(r["p_keep_model"])
        self.assertIsNotNone(r["ceiling"].get("keep_pct_low"))

    def test_the_tail_loss_is_always_a_stated_risk(self):
        r = self._rec()
        self.assertTrue(any("worst 5%" in x for x in r["risks"]))

    def test_earnings_inside_the_option_life_is_always_a_stated_risk(self):
        r = self._rec(earnings_in_days=10)
        self.assertTrue(any("Earnings" in x for x in r["risks"]))

    def test_earnings_after_expiry_is_not_flagged(self):
        r = self._rec(earnings_in_days=90)
        self.assertFalse(any("Earnings" in x for x in r["risks"]))

    def test_a_default_band_trade_says_it_is_not_optimised(self):
        r = self._rec(ceiling=SE.delta_ceiling(measured({}, {})))
        self.assertFalse(r["band_raised"])
        self.assertTrue(any("conservative strike" in x for x in r["risks"]))

    def test_an_illiquid_contract_is_scored_down_and_disclosed(self):
        bad = [contract(106, 0.22, 0.72, liq=False, spread=28.0)]
        r = self._rec(contracts=bad)
        self.assertTrue(r["ok"])
        self.assertTrue(any("liquidity" in x.lower() for x in r["risks"]))
        self.assertLess(r["confidence"]["level"], 60)

    def test_conflicting_layers_cost_confidence_and_are_disclosed(self):
        conflicted = SE.directional_bias(
            range_block={"pos": 92},
            swing_block={"direction": "down",
                         "maturity": {"code": "BEYOND ITS NORMAL SIZE"},
                         "cohort": {"n": 20}})
        clean = SE.directional_bias(
            range_block={"pos": 92},
            swing_block={"direction": "up",
                         "maturity": {"code": "BEYOND ITS NORMAL SIZE"},
                         "cohort": {"n": 20}})
        a = self._rec(bias=conflicted)
        b = self._rec(bias=clean)
        self.assertLess(a["confidence"]["level"], b["confidence"]["level"])
        self.assertTrue(any("disagree" in x for x in a["risks"]))

    def test_no_eligible_contract_is_a_refusal_with_a_reason(self):
        r = self._rec(contracts=[contract(101, 0.62, 3.0)])
        self.assertFalse(r["ok"])
        self.assertTrue(r["reason"])

    def test_every_recommendation_carries_its_reasoning_and_its_risks(self):
        r = self._rec()
        self.assertGreaterEqual(len(r["why"]), 3)
        self.assertGreaterEqual(len(r["risks"]), 1)
        self.assertIn(r["confidence"]["label"], ("HIGH", "MODERATE", "LOW", "WEAK"))

    def test_the_engine_version_rides_along(self):
        self.assertEqual(self._rec()["version"], SE.SETUP_VERSION)


if __name__ == "__main__":
    unittest.main()
