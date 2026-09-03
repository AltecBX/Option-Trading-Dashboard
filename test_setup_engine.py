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
        # FRACTIONS in [0, 1], exactly as premium_edge returns them. An
        # earlier version of this fixture wrote percentages here, which made
        # the engine's unit bug invisible: a 6% chance of assignment was
        # reported on screen as a 99.9% keep rate. Never let a fixture speak
        # a different dialect from the producer it stands in for.
        "p_itm_model": max(0.02, delta * 0.55),
        "p_touch_model": min(1.0, delta * 0.9),
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

    def test_the_model_is_asked_at_the_distance_actually_solved_for(self):
        """The second opinion is worthless if it answers a different question.

        The distance does not exist until `required_distance` has solved for
        it, so a caller passes a callable and the engine asks the model
        about the strike it is really considering.
        """
        asked = []

        def probe(dist_pct):
            asked.append(dist_pct)
            return 9.0

        c = SE.delta_ceiling(measured(CALM, ORDINARY), model_touch_pct=probe)
        self.assertTrue(c["raised"])
        self.assertEqual(len(asked), 1)
        self.assertAlmostEqual(asked[0], c["min_distance_pct"], places=6)

    def test_a_callable_second_opinion_can_still_block_the_widening(self):
        c = SE.delta_ceiling(measured(CALM, ORDINARY),
                             model_touch_pct=lambda d: 40.0)
        self.assertFalse(c["raised"])
        self.assertEqual(c["basis"], "model-disagrees")

    def test_a_second_opinion_that_raises_is_treated_as_no_opinion(self):
        """A broken probe must not silently widen or silently block."""
        def boom(_d):
            raise RuntimeError("no model today")

        c = SE.delta_ceiling(measured(CALM, ORDINARY), model_touch_pct=boom)
        self.assertTrue(c["raised"])
        self.assertIsNone(c["model_touch_pct"])

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

    def test_the_widening_can_actually_pay_more_premium(self):
        """The point of the whole feature, asserted rather than assumed.

        When the measured floor sits CLOSER than the default band's strikes,
        the market quotes that distance at a higher delta, and the engine is
        allowed to sell it. If this ever stops being reachable the feature is
        decorative: it would only ever confirm the default.
        """
        calm = measured({2.0: {"rate": 20, "n": 400}, 3.0: {"rate": 12, "n": 400},
                         5.0: {"rate": 4, "n": 400}},
                        {2.0: {"rate": 55, "n": 3000}, 3.0: {"rate": 44, "n": 3000},
                         5.0: {"rate": 30, "n": 3000}})
        ceiling = SE.delta_ceiling(calm)
        self.assertTrue(ceiling["raised"])
        self.assertLess(ceiling["min_distance_pct"], 5.0)
        # A strike beyond the floor that the market still quotes richly —
        # exactly the case the feature exists for.
        rung = contract(104, 0.27, 0.95)
        ladder = LADDER + [rung]
        self.assertGreater(abs(rung["dist_pct"]), ceiling["min_distance_pct"])
        out = SE.recommend("T", 100.0, "call", "2026-09-18", 26, ladder,
                           SE.directional_bias(), ceiling)
        self.assertTrue(out["ok"])
        self.assertGreater(abs(out["delta"]), SE.DEFAULT_DELTA_BAND[1],
                           "the measured path never sold closer than the default")
        default_only = SE.recommend("T", 100.0, "call", "2026-09-18", 26, ladder,
                                    SE.directional_bias(),
                                    SE.delta_ceiling(measured({}, {})))
        self.assertGreater(out["credit"], default_only["credit"])

    def test_evidence_can_only_add_candidates_never_remove_them(self):
        """Gathering history must never turn a trade into no trade.

        A measured floor the market quotes just under the default band's
        lower edge used to reject every strike — including the one the
        default rule would have taken without complaint. The reward for
        having 181 windows of history was no recommendation at all.
        """
        ceiling = SE.delta_ceiling(measured(CALM, ORDINARY))
        floor = ceiling["min_distance_pct"]
        # One strike, inside the floor, priced squarely in the default band.
        near = [contract(round(100.0 * (1 + (floor - 1.0) / 100.0), 2), 0.18, 0.60)]
        widened = SE.recommend("T", 100.0, "call", "2026-09-18", 26, near,
                               SE.directional_bias(), ceiling)
        plain = SE.recommend("T", 100.0, "call", "2026-09-18", 26, near,
                             SE.directional_bias(), SE.delta_ceiling(measured({}, {})))
        self.assertTrue(plain["ok"], "the default rule should take this strike")
        self.assertTrue(widened["ok"],
                        "evidence removed a trade the default rule would take")
        self.assertEqual(widened["strike"], plain["strike"])

    def test_a_negative_expected_value_sale_is_refused_not_ranked(self):
        """A high win rate bought at a losing price is not a setup.

        Ranking such a contract first and letting the confidence badge carry
        the bad news puts "Sell a put · MODERATE CONFIDENCE · expected value
        −$20" on screen, which reads as a recommendation because it is one.
        """
        losing = [contract(108, 0.16, 0.48, ev=-20.0),
                  contract(112, 0.09, 0.24, ev=-5.0)]
        out = SE.recommend("T", 100.0, "call", "2026-09-18", 26, losing,
                           SE.directional_bias(), SE.delta_ceiling(measured({}, {})))
        self.assertFalse(out["ok"])
        self.assertTrue(out["negative_ev"])
        self.assertIn("loses money", out["reason"])
        # It still says which contract came closest, so the refusal is
        # inspectable rather than a shrug.
        self.assertEqual(out["closest"]["strike"], 108)

    def test_a_break_even_sale_is_refused_too(self):
        flat = [contract(108, 0.16, 0.48, ev=0.0)]
        out = SE.recommend("T", 100.0, "call", "2026-09-18", 26, flat,
                           SE.directional_bias(), SE.delta_ceiling(measured({}, {})))
        self.assertFalse(out["ok"])

    def test_a_positive_expected_value_sale_still_goes_through(self):
        out = SE.recommend("T", 100.0, "call", "2026-09-18", 26, LADDER,
                           SE.directional_bias(), SE.delta_ceiling(measured({}, {})))
        self.assertTrue(out["ok"])
        self.assertGreater(out["ev_per_contract"], 0)

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

    def test_opposing_gamma_moves_the_whole_window_not_just_its_ceiling(self):
        """A pullback must leave a window a real chain can land in.

        Scaling only the top of 0.15–0.22 leaves 0.15–0.176 — 2.6 delta
        points wide. Strikes on a real chain step further apart than that,
        so the band ends up straddling the gap between two strikes and the
        card reports "no contract" rather than recommending a safer one.
        Pulling back should mean "sell further out", not "sell nothing".
        """
        base = SE.delta_ceiling(measured({}, {}))
        g = SE.gex_context(self.OPPOSING, 100.0, 106.0, "call")
        after = SE.apply_gex_to_ceiling(base, g)
        lo0, hi0 = base["band"]
        lo1, hi1 = after["band"]
        self.assertLess(lo1, lo0, "the floor of the band did not move")
        self.assertLess(hi1, hi0)
        self.assertGreaterEqual(hi1 - lo1, (hi0 - lo0) * 0.75,
                                f"band collapsed to {lo1:.3f}–{hi1:.3f}")

    def test_opposing_gamma_actually_moves_the_chosen_strike_further_out(self):
        """The behavioural claim, end to end.

        A pullback that leaves the default band untouched changes nothing at
        all: the same strikes stay eligible and the same one gets picked, so
        the note about negative gamma is decoration. An opposing reading has
        to cost the trade something real — here, distance.
        """
        base = SE.delta_ceiling(measured({}, {}))
        g = SE.gex_context(self.OPPOSING, 100.0, 106.0, "call")
        after = SE.apply_gex_to_ceiling(base, g)
        # Deltas that step straight over the 0.150–0.176 sliver, which is
        # what a real chain does at this strike spacing.
        ladder = [contract(104, 0.24, 0.90), contract(106, 0.19, 0.62),
                  contract(108, 0.14, 0.40), contract(110, 0.10, 0.24)]
        plain = SE.recommend("T", 100.0, "call", "2026-09-18", 26, ladder,
                             SE.directional_bias(), base)
        pulled = SE.recommend("T", 100.0, "call", "2026-09-18", 26, ladder,
                              SE.directional_bias(), after, gex_block=self.OPPOSING)
        self.assertTrue(plain["ok"], plain.get("reason"))
        self.assertTrue(pulled["ok"], pulled.get("reason"))
        self.assertGreater(pulled["strike"], plain["strike"],
                           "the opposing reading did not move the strike at all")

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


class TestTheStreakVocabulary(unittest.TestCase):
    """The dialect the streak layer actually speaks.

    `watchlist_table` emits "up" / "down" / "flat". An earlier version of
    this engine compared that string against 1, which is silently false: no
    exception, no log line, just a streak layer that never votes and never
    conditions a measurement. Every symbol tested came back "nothing about
    today is unusual", which reads like a calm market rather than a bug.
    """

    def test_the_strings_the_producer_actually_emits(self):
        self.assertEqual(SE.streak_sign("up"), 1)
        self.assertEqual(SE.streak_sign("down"), -1)
        self.assertIsNone(SE.streak_sign("flat"))

    def test_the_integers_older_callers_use(self):
        self.assertEqual(SE.streak_sign(1), 1)
        self.assertEqual(SE.streak_sign(-1), -1)
        self.assertIsNone(SE.streak_sign(0))

    def test_nonsense_is_no_direction_rather_than_a_guess(self):
        for v in (None, "", "sideways", float("nan")):
            self.assertIsNone(SE.streak_sign(v), repr(v))

    def test_a_string_direction_actually_produces_a_streak_vote(self):
        """The end-to-end version: the bug was invisible at the unit level
        because the fixtures spoke integers too."""
        b = SE.directional_bias(
            streak_block={"streak_dir": "up", "streak_count": 6,
                          "longest_up": 7, "streak_times_before": 40,
                          "streak_winrate": 55})
        self.assertIn("streak", b["sources"])
        self.assertEqual(b["lean"], "fade_up")


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
            streak_block={"streak_dir": "up", "streak_count": 6, "longest_up": 7},
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
            streak_block={"streak_dir": "up", "streak_count": 4, "longest_up": 14})
        extreme = SE.directional_bias(
            streak_block={"streak_dir": "up", "streak_count": 4, "longest_up": 4})
        mw = modest["votes"][0]["weight"] if modest["votes"] else 0.0
        ew = extreme["votes"][0]["weight"] if extreme["votes"] else 0.0
        self.assertLess(mw, ew)


class TestTheRecommendation(unittest.TestCase):
    def _rec(self, **kw):
        ceiling = kw.pop("ceiling", None) or SE.delta_ceiling(measured(CALM, ORDINARY))
        bias = kw.pop("bias", None) or SE.directional_bias(
            range_block={"pos": 92},
            streak_block={"streak_dir": "up", "streak_count": 6, "longest_up": 7})
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

    def test_the_model_keep_rate_is_a_percentage_of_a_fraction_input(self):
        """The unit contract with premium_edge, pinned.

        `p_itm_model` arrives as a fraction: 0.0627 means a 6.27% chance of
        assignment, so the keep rate is 93.7% — not 99.9%. Reading the
        fraction as a percentage understates assignment risk by a factor of
        a hundred and puts a number on screen that would talk somebody into
        a trade they should not take.
        """
        c = contract(108, 0.16, 0.48)
        c["p_itm_model"] = 0.0627
        r = SE.recommend("T", 100.0, "call", "2026-09-18", 26, [c],
                         SE.directional_bias({}, {}, {}),
                         SE.delta_ceiling(measured({}, {})))
        self.assertAlmostEqual(r["p_keep_model"], 93.7, places=1)

    def test_a_keep_rate_can_never_exceed_a_hundred_percent(self):
        c = contract(108, 0.16, 0.48)
        c["p_itm_model"] = 1.0                    # certain assignment
        r = SE.recommend("T", 100.0, "call", "2026-09-18", 26, [c],
                         SE.directional_bias({}, {}, {}),
                         SE.delta_ceiling(measured({}, {})))
        self.assertAlmostEqual(r["p_keep_model"], 0.0, places=1)

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

    def test_an_illiquid_contract_is_refused_not_scored_down(self):
        """v4.77: liquidity is a gate. Before, an illiquid strike lost 40
        points and could still be the recommendation when its neighbours
        were worse — a trade that cannot be entered or exited at the quoted
        numbers is not a recommendation with a caveat."""
        bad = [contract(106, 0.22, 0.72, liq=False, spread=28.0)]
        r = self._rec(contracts=bad)
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("illiquid"))
        self.assertIn("liquidity gate", r["reason"])

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
