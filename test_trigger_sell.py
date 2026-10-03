"""Guards for trigger_sell.py — WHEN to sell the week's call.

The module encodes one system: no Monday sale, a per-stock trigger at the
top of the usual weekly wick measured from Friday's close, a delta that
rises with extension only when the stock has earned it, and a wait-vs-sell
decision that prices the theta the wait costs. These tests pin that
arithmetic on synthetic tapes whose answers are known in advance.
"""
import math
import unittest
from datetime import date, timedelta

import metrics
import trigger_sell as ts
import weekly_sell as ws

START = date(2025, 1, 6)           # a Monday


# A week as (high, close) per session, each a fraction above Friday's close.
SPIKE_FADE = [(0.035, 0.03), (0.065, 0.06), (0.09, 0.07), (0.075, 0.04), (0.045, 0.02)]
GRIND_UP = [(0.004, 0.004), (0.008, 0.008), (0.012, 0.012), (0.016, 0.016), (0.02, 0.02)]


def tape(weeks, shape=SPIKE_FADE, *, start=START, first_close=100.0, overrides=None):
    """Daily bars for `weeks` complete weeks, plus the Friday before them so
    the first week has an anchor. Every weekday gets a bar (a holiday bar is
    tolerated), and each week is built from the PRIOR Friday's close, so the
    shape is exactly the weekly high and close the module should measure.
    `overrides` maps week index -> a different shape for that week."""
    bars = []
    pre = start - timedelta(days=3)              # the Friday before
    bars.append({"date": pre.isoformat(), "close": first_close,
                 "high": first_close, "low": first_close})
    anchor = first_close
    for wk in range(weeks):
        shp = (overrides or {}).get(wk, shape)
        mon = start + timedelta(weeks=wk)
        for i, (h, c) in enumerate(shp):
            d = mon + timedelta(days=i)
            close = anchor * (1 + c)
            bars.append({"date": d.isoformat(), "close": close,
                         "high": anchor * (1 + h), "low": close * 0.995})
        anchor = anchor * (1 + shp[-1][1])
    return bars


def next_monday(weeks, start=START):
    return start + timedelta(weeks=weeks)


class Weeks(unittest.TestCase):
    def test_each_week_is_measured_from_the_prior_friday(self):
        w = ts.friday_weeks(tape(3))
        self.assertEqual(3, len(w))
        self.assertAlmostEqual(0.09, w[0]["high_pct"], places=9)
        self.assertAlmostEqual(0.02, w[0]["term_pct"], places=9)
        self.assertAlmostEqual(100.0, w[0]["anchor"])
        self.assertAlmostEqual(102.0, w[1]["anchor"], places=9)
        self.assertEqual(2, w[0]["hit_idx"], "the weekly high printed on Wednesday")
        self.assertEqual(2, w[0]["sessions_left_after_hit"], "Thursday and Friday were left")

    def test_a_partial_week_is_dropped(self):
        bars = tape(3)[:-2]                      # the last week stops on Wednesday
        self.assertEqual(2, len(ts.friday_weeks(bars)))

    def test_the_first_week_without_an_anchor_is_dropped(self):
        bars = tape(3)[1:]                       # no Friday before week one
        self.assertEqual(2, len(ts.friday_weeks(bars)))


class Calibration(unittest.TestCase):
    def test_a_nine_percent_wednesday_spike_sets_a_nine_percent_trigger(self):
        """(a) 30 weeks that rally ~9% by Wednesday and fade to +2% Friday."""
        cal = ts.calibrate(tape(30))
        self.assertTrue(cal["ok"])
        self.assertAlmostEqual(0.09, cal["trigger_pct"], places=6)
        self.assertAlmostEqual(0.07, cal["wick_score"], places=6)
        self.assertEqual(2, cal["median_sessions_left_after_hit"])

    def test_an_earnings_spike_does_not_set_the_trigger(self):
        """(b) A 30% earnings-week spike, once a quarter, lifts the top of the
        distribution when it counts and is gone from the calibration when
        its dates are given."""
        spike = [(0.05, 0.04), (0.30, 0.25), (0.28, 0.22), (0.25, 0.20), (0.24, 0.18)]
        weeks = (3, 16, 29)
        bars = tape(30, overrides={w: spike for w in weeks})
        eds = [(START + timedelta(weeks=w, days=1)).isoformat() for w in weeks]
        dirty = ts.calibrate(bars)
        clean = ts.calibrate(bars, earnings_dates=eds)
        self.assertEqual(30, dirty["n_weeks"])
        self.assertEqual(27, clean["n_weeks"])
        self.assertGreater(dirty["p90"], 0.10, "the spikes show up when they are not excluded")
        self.assertAlmostEqual(0.09, clean["p90"], places=6)
        self.assertAlmostEqual(0.09, clean["trigger_pct"], places=6)
        self.assertNotIn(0.30, [round(w["high_pct"], 2) for w in clean["moves"]])
        # one earnings date anywhere in the week removes that week
        friday = (START + timedelta(weeks=3, days=4)).isoformat()
        self.assertEqual(29, ts.calibrate(bars, earnings_dates=[friday])["n_weeks"])

    def test_too_few_clean_weeks_is_no_trigger(self):
        """(c) Fewer than 12 clean weeks: ok=False, and the reason says so."""
        cal = ts.calibrate(tape(11))
        self.assertFalse(cal["ok"])
        self.assertIn("only 11 clean weeks", cal["reason"])
        # earnings weeks count against the twelve
        eds = [(START + timedelta(weeks=i)).isoformat() for i in range(3)]
        cal = ts.calibrate(tape(14), earnings_dates=eds)
        self.assertFalse(cal["ok"])
        self.assertIn("only 11 clean weeks", cal["reason"])

    def test_the_trigger_is_a_percentile_not_an_average(self):
        shapes = {i: [(0.02, 0.01)] + SPIKE_FADE[1:2] + [(0.02 + 0.002 * i, 0.01)] + SPIKE_FADE[3:]
                  for i in range(20)}
        bars = tape(20, overrides=shapes)
        highs = sorted(w["high_pct"] for w in ts.weekly_high_moves(bars))
        cal = ts.calibrate(bars)
        self.assertAlmostEqual(ws.percentile(highs, 0.70), cal["trigger_pct"], places=12)

    def test_the_iv_regime_scales_the_trigger_inside_its_clamp(self):
        bars = tape(20)
        self.assertAlmostEqual(0.09 * math.sqrt(0.6 / 0.4), ts.calibrate(bars, iv_now=0.6, iv_median=0.4)["trigger_pct"])
        self.assertAlmostEqual(0.09 * ts.IV_SCALE_HI, ts.calibrate(bars, iv_now=200, iv_median=20)["trigger_pct"])
        self.assertAlmostEqual(0.09 * ts.IV_SCALE_LO, ts.calibrate(bars, iv_now=0.1, iv_median=0.9)["trigger_pct"])
        self.assertEqual(1.0, ts.iv_scale(None, 0.4))
        self.assertAlmostEqual(1.0, ts.iv_scale(42, 0.42), msg="percent and decimal forms agree")


class WickScore(unittest.TestCase):
    def test_a_spiky_fade_ranks_above_a_grind_up(self):
        """(d) The spike-and-fade tape is the candidate; the grind is not."""
        self.assertAlmostEqual(0.07, ts.wick_score(tape(20)), places=6)
        self.assertAlmostEqual(0.0, ts.wick_score(tape(20, GRIND_UP)), places=6)
        ranked = ts.rank_tickers({"GRND": tape(20, GRIND_UP), "SPKY": tape(20),
                                  "THIN": {"bars": tape(5), "earnings_dates": []}})
        self.assertEqual(["SPKY", "GRND", "THIN"], [r["symbol"] for r in ranked])
        self.assertFalse(ranked[-1]["ok"])
        self.assertIn("only 5 clean weeks", ranked[-1]["reason"])


class AfterTheTap(unittest.TestCase):
    def test_a_fading_wick_rarely_finishes_above_its_trigger(self):
        moves = ts.weekly_high_moves(tape(30))
        st = ts.after_trigger_stats(moves, 0.09)
        self.assertEqual(1.0, st["hit_rate"])
        self.assertEqual(0.0, st["p_finish_over"])
        self.assertAlmostEqual(-0.07, st["finish_median"], places=6)
        self.assertEqual(["low", "mid", "high"], [t["bucket"] for t in st["terciles"]])
        self.assertFalse(st["deep_retrace_proven"], "identical weeks prove nothing about depth")

    def test_deep_extension_must_retrace_harder_to_be_proven(self):
        # Weeks alternate a quiet drift with a run-up week that sets up the
        # stretch; the week AFTER a run-up spikes and fades much harder.
        shapes = {}
        for i in range(36):
            if i % 3 == 0:
                shapes[i] = [(0.03, 0.03), (0.05, 0.05), (0.06, 0.06), (0.06, 0.06), (0.06, 0.06)]
            elif i % 3 == 1:
                shapes[i] = [(0.03, 0.02), (0.06, 0.04), (0.09, 0.03), (0.05, -0.02), (0.02, -0.04)]
            else:
                shapes[i] = [(0.02, 0.01), (0.05, 0.03), (0.09, 0.06), (0.08, 0.05), (0.07, 0.04)]
        moves = ts.weekly_high_moves(tape(36, overrides=shapes))
        st = ts.after_trigger_stats(moves, 0.085)
        lo, hi = st["terciles"][0], st["terciles"][-1]
        self.assertGreater(hi["retrace_median"], lo["retrace_median"])
        self.assertTrue(st["deep_retrace_proven"])


class Delta(unittest.TestCase):
    def test_extension_maps_to_the_policy_points(self):
        """(e) Past 1.5 wick-spreads the delta is 0.70 or more (in the money)."""
        self.assertEqual(0.30, ts.delta_for_extension(-0.4))
        self.assertEqual(0.30, ts.delta_for_extension(None))
        self.assertAlmostEqual(0.50, ts.delta_for_extension(1.0))
        self.assertAlmostEqual(0.70, ts.delta_for_extension(1.5))
        for e in (1.51, 2.0, 3.0, 50.0):
            self.assertGreaterEqual(ts.delta_for_extension(e), 0.70)
            self.assertLessEqual(ts.delta_for_extension(e), ts.DELTA_MAX)

    def test_the_strike_inverts_black_scholes_delta(self):
        for target in (0.20, 0.50, 0.70):
            k = ts.delta_strike(100.0, 3, 0.45, target)
            got = metrics._bs_delta(100.0, k, 3 / 252.0, 0.45, "call")
            self.assertAlmostEqual(target, got, places=6)
        self.assertLess(ts.delta_strike(100.0, 3, 0.45, 0.70), 100.0, "a 0.70 delta call is in the money")
        self.assertIsNone(ts.delta_strike(100.0, 0, 0.45, 0.2))

    def test_expected_value_is_weekly_sells_arithmetic(self):
        windows = [{"term": 0.00, "high": 0.02, "low": -0.01},
                   {"term": 0.10, "high": 0.11, "low": 0.00},
                   {"term": -0.05, "high": 0.01, "low": -0.06}]
        # credit 1.00 on a 105 strike from 100: losses 0, 5, 0 -> EV 1 - 5/3
        self.assertAlmostEqual(1.0 - 5.0 / 3.0, ts.ev_short_call(100.0, 105.0, 1.0, windows))
        row = ws.evaluate_strike({"strike": 105.0, "bid": 0.99, "ask": 1.01, "openInterest": 500,
                                  "volume": 100}, "call", 100.0, windows, 7, None)
        self.assertAlmostEqual(row["ev"], ts.ev_short_call(100.0, 105.0, row["credit"], windows))
        self.assertIsNone(ts.ev_short_call(100.0, 105.0, 1.0, []))


class Decide(unittest.TestCase):
    def setUp(self):
        self.bars = tape(30)
        self.monday = next_monday(30)
        self.friday = self.monday + timedelta(days=4)
        self.anchor = self.bars[-1]["close"]

    def test_the_spike_and_fade_tape_says_wait(self):
        """(a) On Monday, at Friday's close, waiting for the 9% tap beats a
        20-delta call now."""
        d = ts.decide(self.anchor, self.bars, self.monday, self.friday, 0.45)
        self.assertTrue(d["ok"], d.get("reason"))
        self.assertEqual("wait", d["action"], d["reason"])
        self.assertGreater(d["wait"]["ev"], d["now"]["ev"] * (1 + ts.WAIT_MARGIN))
        self.assertAlmostEqual(self.anchor * 1.09, d["trigger_price"], places=6)
        self.assertEqual(1.0, d["wait"]["p_hit"])
        self.assertEqual(3, d["wait"]["sessions_at_tap"], "a Wednesday tap leaves Wed, Thu and Fri")
        self.assertEqual(0.0, d["after_trigger"]["p_finish_over"])
        self.assertEqual(5, d["now"]["sessions"])

    def test_the_legs_add_up_to_the_difference(self):
        d = ts.decide(self.anchor, self.bars, self.monday, self.friday, 0.45)
        L = d["legs"]
        self.assertAlmostEqual(L["ev_diff"], sum(L["weighted"].values()) + L["risk_and_miss"], places=9)
        self.assertLess(L["theta_decay"], 0, "fewer sessions cost premium")
        self.assertGreater(L["strike_uplift"], 0, "the trigger unlocks a higher strike")
        self.assertGreater(L["delta_uplift"], 0, "the adaptive delta pays more than 0.20")
        self.assertAlmostEqual(d["wait"]["credit"] - d["now"]["credit"],
                               L["theta_decay"] + L["strike_uplift"] + L["delta_uplift"], places=9)

    def test_an_unproven_deep_extension_is_capped_at_the_money(self):
        d = ts.decide(self.anchor, self.bars, self.monday, self.friday, 0.45)
        self.assertGreater(d["delta"]["uncapped"], ts.DELTA_MID)
        self.assertTrue(d["delta"]["capped"])
        self.assertEqual(ts.DELTA_MID, d["delta"]["adaptive"])

    def test_a_tap_already_on_the_screen_sells_now(self):
        d = ts.decide(self.anchor * 1.095, self.bars, self.monday + timedelta(days=2), self.friday, 0.45)
        self.assertEqual("sell_at_trigger", d["action"])
        self.assertEqual(3, d["wait"]["sessions_at_tap"])

    def test_a_wednesday_decision_is_not_credited_with_monday_taps(self):
        early = [(0.09, 0.07), (0.06, 0.05), (0.05, 0.04), (0.04, 0.03), (0.03, 0.02)]
        bars = tape(30, early)
        mon = next_monday(30)
        anchor = bars[-1]["close"]
        d = ts.decide(anchor, bars, mon + timedelta(days=2), mon + timedelta(days=4), 0.45)
        self.assertEqual(0.0, d["wait"]["p_hit"], "every past tap came on Monday")

    def test_theta_two_weeks_running_flags_the_trigger(self):
        d = ts.decide(self.anchor, self.bars, self.monday, self.friday, 0.45, theta_history=[True])
        L = d["legs"]
        self.assertEqual(L["theta_dominant"] and True, L["trigger_too_far"])
        # Theta dominates when the tap comes late AND the delta stays low: a
        # stock drifting down whose wick only prints on Friday. The tap sale
        # (0.30 delta, one session) collects less than a 20 delta today.
        late = [(0.005, 0.0), (0.01, -0.005), (0.015, -0.01), (0.02, -0.015), (0.03, -0.03)]
        bars = tape(30, late)
        mon = next_monday(30)
        d = ts.decide(bars[-1]["close"], bars, mon, mon + timedelta(days=4), 0.45, theta_history=[True])
        self.assertTrue(d["legs"]["theta_dominant"])
        self.assertTrue(d["legs"]["trigger_too_far"])
        self.assertIn("too far out", d["legs"]["note"])
        d = ts.decide(bars[-1]["close"], bars, mon, mon + timedelta(days=4), 0.45, theta_history=[False])
        self.assertFalse(d["legs"]["trigger_too_far"], "one week is not two")

    def test_skip_when_neither_choice_pays(self):
        # Every week climbs all week and closes at its high, by a different
        # amount each week: a tapped week finishes ABOVE its trigger, so
        # neither a call now nor one at the tap has paid.
        def climb(g):
            return [(g * (i + 1) / 5, g * (i + 1) / 5) for i in range(5)]
        gains = [0.05, 0.08, 0.11, 0.14, 0.17, 0.20]
        bars = tape(30, overrides={i: climb(gains[i % len(gains)]) for i in range(30)})
        mon = next_monday(30)
        d = ts.decide(bars[-1]["close"], bars, mon, mon + timedelta(days=4), 0.20)
        self.assertEqual("skip", d["action"], d["reason"])

    def test_not_enough_history_is_said_not_guessed(self):
        bars = tape(8)
        mon = next_monday(8)
        d = ts.decide(bars[-1]["close"], bars, mon, mon + timedelta(days=4), 0.45)
        self.assertFalse(d["ok"])
        self.assertIn("clean weeks", d["reason"])


class Retime(unittest.TestCase):
    def test_spot_at_the_trigger_and_the_sessions_left(self):
        r = ts.retime_at_trigger(100.0, 0.074, date(2026, 9, 16), date(2026, 9, 18))
        self.assertAlmostEqual(107.4, r["spot"])
        self.assertEqual(3, r["sessions"], "Wednesday to Friday")


if __name__ == "__main__":
    unittest.main(verbosity=2)
