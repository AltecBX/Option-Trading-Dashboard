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

    def test_a_week_after_a_cut_short_week_is_not_anchored_mid_week(self):
        """Codex, #424: if Thursday and Friday are missing, the next week must
        not be measured from Wednesday's close. Both weeks drop."""
        bars = tape(4)
        wk1 = START + timedelta(weeks=1)
        cut = {(wk1 + timedelta(days=3)).isoformat(), (wk1 + timedelta(days=4)).isoformat()}
        weeks = ts.friday_weeks([b for b in bars if b["date"] not in cut])
        self.assertEqual([START.isoformat(), (START + timedelta(weeks=3)).isoformat()],
                         [w["start"] for w in weeks])

    def test_a_holiday_friday_still_anchors_the_next_week(self):
        # Good Friday 2025-04-18: the week ends Thursday, and that is its close.
        bars = [b for b in tape(16) if b["date"] != "2025-04-18"]
        weeks = ts.friday_weeks(bars)
        self.assertEqual(16, len(weeks))
        after = [w for w in weeks if w["start"] == "2025-04-21"][0]
        thursday = [b for b in bars if b["date"] == "2025-04-17"][0]["close"]
        self.assertAlmostEqual(thursday, after["anchor"])

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

    def test_a_tap_earlier_this_week_counts_even_after_a_pullback(self):
        """Codex, #424: the trigger was tapped Tuesday and price slipped back.
        That is the sale, not a reason to wait for a second tap."""
        trig = self.anchor * 1.09
        week = [{"date": self.monday.isoformat(), "close": self.anchor * 1.03,
                 "high": self.anchor * 1.035, "low": self.anchor * 1.0},
                {"date": (self.monday + timedelta(days=1)).isoformat(), "close": self.anchor * 1.06,
                 "high": trig * 1.001, "low": self.anchor * 1.03}]
        wed = self.monday + timedelta(days=2)
        d = ts.decide(self.anchor * 1.07, self.bars + week, wed, self.friday, 0.45)
        self.assertEqual("sell_at_trigger", d["action"], d["reason"])
        self.assertEqual((self.monday + timedelta(days=1)).isoformat(), d["tap"]["first_seen"])
        self.assertFalse(d["tap"]["at_spot"])
        self.assertIn("was tapped", d["reason"])
        self.assertEqual(3, d["wait"]["sessions_at_tap"], "priced on the sessions actually left")
        # and the caller can say so directly
        d = ts.decide(self.anchor * 1.07, self.bars, wed, self.friday, 0.45, tapped=True)
        self.assertEqual("sell_at_trigger", d["action"])

    def test_a_tapped_skip_does_not_say_sell(self):
        """Codex, #424: when the tap has lost money historically, the reason
        must not tell the caller to sell."""
        def climb(g):
            return [(g * (i + 1) / 5, g * (i + 1) / 5) for i in range(5)]
        gains = [0.05, 0.08, 0.11, 0.14, 0.17, 0.20]
        bars = tape(30, overrides={i: climb(gains[i % len(gains)]) for i in range(30)})
        mon = next_monday(30)
        anchor = bars[-1]["close"]
        d = ts.decide(anchor * 1.30, bars, mon + timedelta(days=3), mon + timedelta(days=4), 0.20)
        self.assertEqual("skip", d["action"], d["reason"])
        self.assertIn("do not sell", d["reason"])
        self.assertNotIn("sell the", d["reason"])

    def test_on_expiry_friday_there_is_no_fallback_to_wait_for(self):
        """Codex, #424: a two-session Thursday fallback priced on Friday made
        'wait' possible on the day the option expires."""
        early = [(0.09, 0.07), (0.06, 0.05), (0.05, 0.04), (0.04, 0.03), (0.03, 0.02)]
        bars = tape(30, early)
        mon = next_monday(30)
        fri = mon + timedelta(days=4)
        d = ts.decide(bars[-1]["close"], bars, fri, fri, 0.45)
        self.assertEqual(0, d["wait"]["fallback"]["sessions"])
        self.assertEqual(0.0, d["wait"]["p_hit"])
        self.assertNotEqual("wait", d["action"], d["reason"])
        thu = ts.decide(bars[-1]["close"], bars, fri - timedelta(days=1), fri, 0.45)
        self.assertEqual(1, thu["wait"]["fallback"]["sessions"], "Thursday's fallback is Friday alone")

    def test_monday_never_says_sell_now(self):
        """Codex, #424: the rule the system starts from. The same tape that
        says sell_now on Tuesday says wait on Monday, numbers kept."""
        late = [(0.005, 0.0), (0.01, -0.005), (0.015, -0.01), (0.02, -0.015), (0.03, -0.03)]
        bars = tape(30, late)
        mon = next_monday(30)
        tue = ts.decide(bars[-1]["close"], bars, mon + timedelta(days=1), mon + timedelta(days=4), 0.45)
        self.assertEqual("sell_now", tue["action"])
        d = ts.decide(bars[-1]["close"], bars, mon, mon + timedelta(days=4), 0.45)
        self.assertEqual("wait", d["action"])
        self.assertTrue(d["monday_rule"])
        self.assertIn("no Monday sale", d["reason"])
        self.assertGreater(d["now"]["ev"], d["wait"]["ev"], "the numbers are reported as they are")

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



# ── v2: regime-conditional triggers ─────────────────────────────────────────
import json
import os
from pathlib import Path

# A synthetic table in the real one's shape, in percent points. Edges are
# [min, q20, q40, q60, q80, max] of last week's Friday-to-Friday close.
TABLE = {
    "SYN": {"unconditional_trigger": 9.0,
            "prior_week_edges": [-5.0, -0.5, 0.5, 2.0, 8.0, 15.0],
            "conditional": [
                {"quintile": 1, "prior_lo": -5.0, "prior_hi": -0.5, "trigger70": 12.0, "trigger50": 10.0},
                {"quintile": 2, "prior_lo": -0.5, "prior_hi": 0.5, "trigger70": 8.0, "trigger50": 6.0},
                {"quintile": 3, "prior_lo": 0.5, "prior_hi": 2.0, "trigger70": 7.0, "trigger50": 5.5},
                {"quintile": 4, "prior_lo": 2.0, "prior_hi": 8.0, "trigger70": 6.0, "trigger50": 5.0},
                {"quintile": 5, "prior_lo": 8.0, "prior_hi": 15.0, "trigger70": 4.0, "trigger50": 3.0}]},
    "SPCX": {"unconditional_trigger": 11.0},
}

# A three-week cycle: a big up week that closes near its high (+11%), a
# quiet week after it whose wick tops near +4%, then an ordinary week.
UP = [(0.03, 0.03), (0.06, 0.06), (0.09, 0.09), (0.12, 0.11), (0.12, 0.11)]
AFTER_UP = [(0.02, 0.01), (0.04, 0.02), (0.03, 0.0), (0.01, -0.005), (0.0, -0.01)]
NORMAL = [(0.02, 0.02), (0.05, 0.04), (0.07, 0.05), (0.04, 0.02), (0.02, 0.01)]


def regime_tape(weeks=38, after_up=None):
    """NORMAL, UP, AFTER_UP repeated, ending on an UP week so the next
    Monday starts in the top quintile."""
    cyc = [NORMAL, UP, AFTER_UP]
    shapes = {}
    k = 0
    for i in range(weeks):
        s = cyc[i % 3]
        if s is AFTER_UP and after_up is not None:
            s = after_up(k)
            k += 1
        shapes[i] = s
    return tape(weeks, overrides=shapes)


def _real_table():
    """The real table, wherever it has been put: $JERRY_REGIME_TABLE, the
    repo's data/ folder, or the workspace path it was built in."""
    here = Path(__file__).resolve().parent
    for p in (os.environ.get("JERRY_REGIME_TABLE"),
              here / "data" / "conditional_triggers.json",
              Path.home() / "workspace/goals/catch-stock-trends-before-they-move/hidden_files/conditional_triggers.json"):
        if p and Path(p).is_file():
            return json.loads(Path(p).read_text())
    return None


REAL = _real_table()


@unittest.skipUnless(REAL, "conditional_triggers.json not found: set JERRY_REGIME_TABLE or put it in data/")
class RealTable(unittest.TestCase):
    """(a) and (c) on the real two-year table."""

    def test_lite_after_a_big_up_week(self):
        self.assertAlmostEqual(0.145, ts.regime_trigger("LITE", 15.0, REAL), delta=0.0051)
        self.assertNotAlmostEqual(0.1382, ts.regime_trigger("LITE", 15.0, REAL), places=3)

    def test_lite_after_a_crash_week(self):
        self.assertAlmostEqual(0.215, ts.regime_trigger("LITE", -9.0, REAL), delta=0.0051)

    def test_cohr_after_a_big_up_week(self):
        self.assertAlmostEqual(0.0925, ts.regime_trigger("COHR", 12.0, REAL), delta=0.0026)

    def test_tickers_without_quintiles_use_their_unconditional_trigger(self):
        for t in ("SPCX", "DRAM"):
            info = ts.regime_info(t, 15.0, REAL)
            if info["source"] == "missing":
                continue                  # not in this copy of the table at all
            self.assertEqual("unconditional", info["source"], t)
            self.assertAlmostEqual(info["unconditional"], ts.regime_trigger(t, 15.0, REAL))


class RegimeTrigger(unittest.TestCase):
    def test_each_quintile_gets_its_own_trigger(self):
        self.assertAlmostEqual(0.04, ts.regime_trigger("SYN", 11.0, TABLE))
        self.assertAlmostEqual(0.12, ts.regime_trigger("syn", -3.0, TABLE), msg="ticker case does not matter")
        self.assertAlmostEqual(0.07, ts.regime_trigger("SYN", 1.0, TABLE))

    def test_an_edge_value_lands_in_the_lower_quintile_and_outliers_clamp(self):
        """(b)"""
        e = TABLE["SYN"]["prior_week_edges"]
        self.assertEqual(1, ts.quintile_of(-0.5, e))
        self.assertEqual(2, ts.quintile_of(0.5, e))
        self.assertEqual(3, ts.quintile_of(2.0, e))
        self.assertEqual(4, ts.quintile_of(8.0, e))
        self.assertEqual(5, ts.quintile_of(15.0, e))
        self.assertEqual(1, ts.quintile_of(-40.0, e), "below the first edge clamps to 1")
        self.assertEqual(5, ts.quintile_of(60.0, e), "above the last edge clamps to 5")
        self.assertAlmostEqual(0.12, ts.regime_trigger("SYN", -40.0, TABLE))
        self.assertAlmostEqual(0.04, ts.regime_trigger("SYN", 60.0, TABLE))
        # four interior cut points work the same way
        self.assertEqual(2, ts.quintile_of(0.5, e[1:-1]))

    def test_a_ticker_without_quintiles_falls_back_to_its_unconditional_trigger(self):
        """(c)"""
        info = ts.regime_info("SPCX", 15.0, TABLE)
        self.assertEqual("unconditional", info["source"])
        self.assertAlmostEqual(0.11, ts.regime_trigger("SPCX", 15.0, TABLE))
        self.assertIsNone(ts.regime_trigger("NOPE", 15.0, TABLE), "not in the table at all")

    def test_table_shapes_and_units(self):
        frac = {"SYN": {"unconditional_trigger": 0.09,
                        "prior_week_edges": [-0.05, -0.005, 0.005, 0.02, 0.08, 0.15],
                        "conditional": [{"quintile": q, "trigger70": t} for q, t in
                                        ((1, .12), (2, .08), (3, .07), (4, .06), (5, .04))]}}
        self.assertAlmostEqual(0.04, ts.regime_trigger("SYN", 11.0, frac), msg="fraction-unit table")
        as_list = [dict(TABLE["SYN"], ticker="SYN")]
        self.assertAlmostEqual(0.04, ts.regime_trigger("SYN", 11.0, as_list))
        self.assertAlmostEqual(0.04, ts.regime_trigger("SYN", 11.0, {"tickers": TABLE}))
        self.assertAlmostEqual(0.04, ts.regime_trigger(None, 11.0, TABLE["SYN"]), msg="one entry passed directly")


class RegimeCalibration(unittest.TestCase):
    def test_the_regime_replaces_the_percentile_and_keeps_the_iv_scaling(self):
        bars = regime_tape()
        plain = ts.calibrate(bars)
        cal = ts.calibrate(bars, regime_table=TABLE, prior_week_close_pct=11.0, ticker="SYN")
        self.assertEqual("percentile", plain["trigger_source"])
        self.assertEqual("regime", cal["trigger_source"])
        self.assertAlmostEqual(0.04, cal["trigger_pct"])
        self.assertEqual(5, cal["regime"]["quintile"])
        hot = ts.calibrate(bars, regime_table=TABLE, prior_week_close_pct=11.0, ticker="SYN",
                           iv_now=0.9, iv_median=0.4)
        self.assertAlmostEqual(0.04 * ts.IV_SCALE_HI, hot["trigger_pct"])
        self.assertEqual(plain["n_weeks"], cal["n_weeks"], "same clean weeks either way")

    def test_too_few_weeks_still_refuses_with_a_table(self):
        cal = ts.calibrate(regime_tape(9), regime_table=TABLE, prior_week_close_pct=11.0, ticker="SYN")
        self.assertFalse(cal["ok"])

    def test_a_ticker_the_table_cannot_answer_keeps_the_percentile(self):
        bars = regime_tape()
        cal = ts.calibrate(bars, regime_table=TABLE, prior_week_close_pct=11.0, ticker="NOPE")
        self.assertEqual("percentile", cal["trigger_source"])
        self.assertAlmostEqual(ts.calibrate(bars)["trigger_pct"], cal["trigger_pct"])

    def test_sessions_left_come_from_the_quintile_only(self):
        bars = regime_tape()
        e = TABLE["SYN"]["prior_week_edges"]
        # after an up week the 4% wick tops on Tuesday: Wed, Thu, Fri are left
        self.assertEqual(3, ts.quintile_sessions_left(bars, 5, 0.04, edges_pct=e))
        # the ordinary weeks (after a -1% week) reach 4% on Tuesday too, and
        # 7% on Wednesday
        self.assertEqual(2, ts.quintile_sessions_left(bars, 1, 0.07, edges_pct=e))
        self.assertIsNone(ts.quintile_sessions_left(bars, 5, 0.20, edges_pct=e))


class RegimeDecision(unittest.TestCase):
    def setUp(self):
        self.bars = regime_tape()
        self.mon = next_monday(38)
        self.fri = self.mon + timedelta(days=4)
        self.anchor = self.bars[-1]["close"]

    def test_decide_uses_the_regime_trigger_in_the_wait(self):
        """(d) After an +11% week the trigger is the top quintile's 4%, not
        the unconditional percentile, and the wait is priced there."""
        plain = ts.decide(self.anchor, self.bars, self.mon + timedelta(days=1), self.fri, 0.45)
        d = ts.decide(self.anchor, self.bars, self.mon + timedelta(days=1), self.fri, 0.45,
                      ticker="SYN", regime_table=TABLE)
        self.assertTrue(d["ok"], d.get("reason"))
        self.assertAlmostEqual(11.0, d["regime"]["prior_week_close_pct"], places=6)
        self.assertEqual(5, d["regime"]["quintile"])
        self.assertAlmostEqual(0.04, d["trigger_pct"])
        self.assertAlmostEqual(self.anchor * 1.04, d["trigger_price"])
        self.assertAlmostEqual(self.anchor * 1.04, d["wait"]["sale_price"])
        self.assertNotAlmostEqual(plain["trigger_price"], d["trigger_price"], places=2)
        self.assertNotAlmostEqual(plain["wait"]["ev"], d["wait"]["ev"], places=4)
        # the odds and the sessions come from the same quintile's weeks
        self.assertEqual("quintile", d["regime"]["basis"])
        self.assertEqual(1.0, d["wait"]["p_hit"], "every week after an up week reached 4%")
        self.assertEqual(4, d["wait"]["sessions_at_tap"], "a Tuesday tap: Tue, Wed, Thu, Fri")
        self.assertEqual(d["regime"]["weeks_in_quintile"], d["after_trigger"]["n_weeks"])

    def test_the_guardrail_reads_only_the_quintile_and_names_it(self):
        """(e) Stretched top-quintile weeks that do not pull back harder keep
        the delta at the money, and the reason says which quintile."""
        d = ts.decide(self.anchor, self.bars, self.mon + timedelta(days=1), self.fri, 0.45,
                      ticker="SYN", regime_table=TABLE)
        self.assertGreater(d["delta"]["uncapped"], ts.DELTA_MID)
        self.assertTrue(d["delta"]["capped"])
        self.assertEqual(ts.DELTA_MID, d["delta"]["adaptive"])
        self.assertIn("quintile 5", d["delta"]["why_capped"])
        self.assertFalse(d["after_trigger"]["deep_retrace_proven"])

    def test_without_a_table_nothing_changes(self):
        a = ts.decide(self.anchor, self.bars, self.mon + timedelta(days=1), self.fri, 0.45)
        self.assertFalse(a["regime"]["on"])
        self.assertEqual("percentile", a["calibration"]["trigger_source"])
        self.assertIsNone(a["history_record"]["quintile"])


class ThetaHistoryByRegime(unittest.TestCase):
    def test_two_weeks_running_means_the_same_quintile(self):
        rec = lambda q, dom: {"week": "2026-09-21", "ticker": "SYN", "quintile": q,
                              "trigger_pct": 0.04, "theta_dominant": dom}
        self.assertTrue(ts.theta_too_far(True, [rec(5, True)], 5))
        self.assertFalse(ts.theta_too_far(True, [rec(1, True)], 5), "a different regime is not 'running'")
        self.assertTrue(ts.theta_too_far(True, [rec(5, True), rec(1, False)], 5),
                        "the last record in the SAME quintile decides")
        self.assertFalse(ts.theta_too_far(True, [rec(5, False), rec(1, True)], 5))
        self.assertFalse(ts.theta_too_far(False, [rec(5, True)], 5))
        self.assertTrue(ts.theta_too_far(True, [True], None), "v1 booleans still work")

    def test_decide_returns_the_record_to_keep(self):
        bars = regime_tape()
        mon = next_monday(38)
        d = ts.decide(bars[-1]["close"], bars, mon + timedelta(days=1), mon + timedelta(days=4), 0.45,
                      ticker="SYN", regime_table=TABLE)
        r = d["history_record"]
        self.assertEqual({"week", "ticker", "quintile", "trigger_pct", "theta_dominant", "theta_decay", "uplift"},
                         set(r))
        self.assertEqual((mon.isoformat(), "SYN", 5), (r["week"], r["ticker"], r["quintile"]))


class PercentileByRegime(unittest.TestCase):
    def test_expected_premium_is_hit_odds_times_the_price_at_the_trigger(self):
        bars = regime_tape()
        e = ts.expected_weekly_premium("SYN", 5, 0.04, bars, TABLE, iv=0.45)
        sessions = ts.quintile_sessions_left(bars, 5, 0.04, edges_pct=TABLE["SYN"]["prior_week_edges"]) + 1
        k = ts.delta_strike(1.04, sessions, 0.45, ts.DELTA_MID)   # capped at the money (unproven)
        price = metrics._bs_price(1.04, k, sessions / 252.0, 0.45, "call")
        self.assertAlmostEqual(1.0 * price, e, places=9)
        self.assertEqual(0.0, ts.expected_weekly_premium("SYN", 5, 0.30, bars, TABLE, iv=0.45),
                         "a trigger nobody reaches collects nothing")

    def test_a_runner_regime_prefers_a_lower_percentile(self):
        """(f) After big up weeks this stock sometimes keeps running: the
        wick tops spread from +2% to +14%. Waiting for the 70th percentile
        gives up too many weeks; the premium-maximizing percentile is lower."""
        runs = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.14, 0.03, 0.05, 0.07, 0.09]

        def after_up(k):
            h = runs[k % len(runs)]
            return [(h * 0.4, h * 0.3), (h, h * 0.6), (h * 0.8, h * 0.3), (h * 0.5, 0.0), (h * 0.3, -0.01)]
        bars = regime_tape(39, after_up=after_up)
        res = ts.optimize_percentile("SYN", 5, bars, TABLE, iv=0.45)
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertLess(res["percentile"], 0.70)
        self.assertEqual(len(ts.PCTILE_GRID), len(res["grid"]))
        at70 = [g for g in res["grid"] if g["pctile"] == 0.70][0]
        self.assertGreater(res["premium"], at70["premium"])

    def test_a_thin_quintile_is_refused(self):
        res = ts.optimize_percentile("SYN", 2, regime_tape(), TABLE, iv=0.45)
        self.assertFalse(res["ok"])
        self.assertIn("quintile 2", res["reason"])


class LastWeekIsARealWeek(unittest.TestCase):
    """Codex, #425: last week's move is read only from the Friday closes of
    two back-to-back weeks. A missing or cut-short week would otherwise
    pass off a two-week (or Wednesday-to-Friday) move as last week's and
    pick the wrong quintile."""

    def _without(self, bars, week, days=range(5)):
        mon = START + timedelta(weeks=week)
        drop = {(mon + timedelta(days=i)).isoformat() for i in days}
        return [b for b in bars if b["date"] not in drop]

    def test_a_missing_calendar_week_is_not_a_prior_week_move(self):
        weeks = {w["start"]: w for w in ts.friday_weeks(self._without(tape(6), 2))}
        after_gap = weeks[(START + timedelta(weeks=4)).isoformat()]
        self.assertIsNone(after_gap["prior_week_pct"], "week 1's Friday to week 3's is two weeks")
        self.assertAlmostEqual(2.0, weeks[(START + timedelta(weeks=5)).isoformat()]["prior_week_pct"])

    def test_a_cut_short_week_is_not_a_prior_week_move(self):
        weeks = {w["start"]: w for w in ts.friday_weeks(self._without(tape(5), 1, days=(3, 4)))}
        self.assertIsNone(weeks[(START + timedelta(weeks=3)).isoformat()]["prior_week_pct"],
                          "week 1 ended on Wednesday")
        self.assertAlmostEqual(2.0, weeks[(START + timedelta(weeks=4)).isoformat()]["prior_week_pct"])

    def _decide(self, bars):
        mon = next_monday(38)
        return ts.decide(bars[-1]["close"], bars, mon + timedelta(days=1), mon + timedelta(days=4),
                         0.45, ticker="SYN", regime_table=TABLE)

    def test_decide_needs_last_week_itself(self):
        d = self._decide(self._without(regime_tape(), 37))
        self.assertIsNone(d["regime"]["prior_week_close_pct"])
        self.assertFalse(d["regime"]["on"])
        self.assertIsNone(d["regime"]["quintile"])

    def test_decide_needs_last_week_to_end_on_its_last_session(self):
        d = self._decide(self._without(regime_tape(), 37, days=(3, 4)))
        self.assertIsNone(d["regime"]["prior_week_close_pct"])
        self.assertFalse(d["regime"]["on"])

    def test_decide_needs_the_week_before_to_end_on_its_last_session(self):
        d = self._decide(self._without(regime_tape(), 36, days=(4,)))
        self.assertIsNone(d["regime"]["prior_week_close_pct"])
        self.assertTrue(self._decide(regime_tape())["regime"]["on"], "the full tape still reads it")


class TableWithoutEdges(unittest.TestCase):
    """Codex, #425: a table that gives only each quintile's prior_lo/prior_hi
    sorts the past weeks by those same bounds, not by cut points worked out
    afresh from the weeks."""

    BARE = {"SYN": {k: v for k, v in TABLE["SYN"].items() if k != "prior_week_edges"}}

    def test_the_bounds_become_the_edges(self):
        info = ts.regime_info("SYN", 11.0, self.BARE)
        self.assertEqual(5, info["quintile"])
        self.assertEqual(TABLE["SYN"]["prior_week_edges"], info["edges_pct"])
        self.assertEqual(1, ts.regime_info("SYN", -0.5, self.BARE)["quintile"], "on a bound: the lower one")

    def test_decide_sorts_history_the_same_way(self):
        bars = regime_tape()
        mon = next_monday(38)
        args = (bars[-1]["close"], bars, mon + timedelta(days=1), mon + timedelta(days=4), 0.45)
        full = ts.decide(*args, ticker="SYN", regime_table=TABLE)
        bare = ts.decide(*args, ticker="SYN", regime_table=self.BARE)
        self.assertEqual(full["regime"]["weeks_in_quintile"], bare["regime"]["weeks_in_quintile"])
        self.assertEqual("quintile", bare["regime"]["basis"])
        self.assertAlmostEqual(full["wait"]["p_hit"], bare["wait"]["p_hit"])
        self.assertAlmostEqual(full["wait"]["ev"], bare["wait"]["ev"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
