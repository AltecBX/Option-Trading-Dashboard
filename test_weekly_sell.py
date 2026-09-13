"""Guards for weekly_sell.py — the strike engine.

The panel's whole claim is that its numbers come from this stock's own
history and the live chain, so these tests pin the arithmetic rather than
the wording: a percentile that means what it says, a touch band wider than
a terminal band, an expected value that can be re-added by hand, and gates
that actually reject an unfillable strike.
"""
import math
import unittest
from datetime import date, datetime, timedelta

import weekly_sell as ws


def bars_from_closes(closes, start=date(2025, 1, 6), *, span=0.0):
    """Daily bars from a list of closes. `span` widens each bar's high/low
    around its close as a fraction, so path and terminal numbers differ."""
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append({"date": d.isoformat(), "open": c, "close": c,
                    "high": c * (1 + span), "low": c * (1 - span)})
        d += timedelta(days=1)
    return out


def opt(strike, bid, ask, *, oi=500, vol=100, iv=0.35, delta=None):
    return {"strike": strike, "bid": bid, "ask": ask, "last": (bid + ask) / 2,
            "openInterest": oi, "volume": vol, "iv": iv, "delta": delta}


class SessionCount(unittest.TestCase):
    def test_today_counts_because_today_can_still_move(self):
        # Mon 2026-09-14 → Fri 2026-09-18 is five sessions including today.
        self.assertEqual(ws.sessions_until(date(2026, 9, 18), date(2026, 9, 14)), 5)

    def test_a_thursday_sale_of_a_friday_option_is_two_sessions(self):
        self.assertEqual(ws.sessions_until(date(2026, 9, 18), date(2026, 9, 17)), 2)

    def test_weekends_are_not_sessions(self):
        # Sat 9/12 and Sun 9/13 both lead into the same five weekdays.
        self.assertEqual(ws.sessions_until(date(2026, 9, 18), date(2026, 9, 12)), 5)
        self.assertEqual(ws.sessions_until(date(2026, 9, 18), date(2026, 9, 13)), 5)

    def test_a_holiday_is_not_a_session(self):
        # Thanksgiving 2026 is Thu 11-26. Mon 11-23 → Fri 11-27 is four.
        self.assertEqual(ws.sessions_until(date(2026, 11, 27), date(2026, 11, 23)), 4)

    def test_a_past_expiry_has_no_sessions(self):
        self.assertEqual(ws.sessions_until(date(2026, 9, 1), date(2026, 9, 14)), 0)

    def test_unreadable_input_is_none_not_a_guess(self):
        self.assertIsNone(ws.sessions_until("not-a-date", date(2026, 9, 14)))


class Percentile(unittest.TestCase):
    def test_the_ends_are_the_ends(self):
        v = [1, 2, 3, 4, 5]
        self.assertEqual(ws.percentile(v, 0), 1)
        self.assertEqual(ws.percentile(v, 1), 5)

    def test_the_middle_interpolates(self):
        self.assertEqual(ws.percentile([0, 10], 0.5), 5)
        self.assertAlmostEqual(ws.percentile([0, 10, 20, 30, 40], 0.25), 10)

    def test_empty_is_none_not_zero(self):
        self.assertIsNone(ws.percentile([], 0.5))


class ForwardWindows(unittest.TestCase):
    def test_each_window_reads_the_next_k_sessions_only(self):
        bars = bars_from_closes([100, 110, 90, 105, 100])
        w = ws.forward_windows(bars, 2)
        self.assertEqual(len(w), 3)           # 5 bars, k=2 → 3 window starts
        # From bar 0 (close 100) the next two closes are 110 and 90.
        self.assertAlmostEqual(w[0]["high"], 0.10, places=9)
        self.assertAlmostEqual(w[0]["low"], -0.10, places=9)
        self.assertAlmostEqual(w[0]["term"], -0.10, places=9)   # terminal = 90

    def test_touch_and_finish_are_different_numbers(self):
        # Down to 90 then back to 100: the path was touched, the close was not.
        bars = bars_from_closes([100, 90, 100])
        w = ws.forward_windows(bars, 2)[0]
        self.assertAlmostEqual(w["low"], -0.10, places=9)
        self.assertAlmostEqual(w["term"], 0.0, places=9)

    def test_a_partial_bar_dated_today_is_dropped(self):
        bars = bars_from_closes([100, 101, 102, 103])
        today = date.fromisoformat(bars[-1]["date"])
        self.assertEqual(len(ws.forward_windows(bars, 1)), 3)
        self.assertEqual(len(ws.forward_windows(bars, 1, drop_last_dated=today)), 2)

    def test_a_short_history_yields_nothing_rather_than_a_short_window(self):
        self.assertEqual(ws.forward_windows(bars_from_closes([100, 101]), 5), [])


class HistoryBand(unittest.TestCase):
    def setUp(self):
        # A deterministic sawtooth: a wide range of k-session outcomes.
        closes = [100 * (1 + 0.02 * math.sin(i / 3.0)) for i in range(300)]
        self.bars = bars_from_closes(closes, span=0.004)
        self.windows = ws.forward_windows(self.bars, 5)

    def test_the_percentile_means_what_it_says(self):
        band = ws.history_band(self.windows, 0.10)
        reached = sum(1 for w in self.windows if w["low"] <= band["low"] + 1e-12)
        share = reached / len(self.windows)
        self.assertLess(abs(share - 0.10), 0.03, f"10% band was reached {share:.0%}")

    def test_a_tighter_target_sits_further_away(self):
        wide = ws.history_band(self.windows, 0.25)
        tight = ws.history_band(self.windows, 0.05)
        self.assertLess(tight["low"], wide["low"])
        self.assertGreater(tight["high"], wide["high"])


class ImpliedBand(unittest.TestCase):
    def test_a_touch_band_is_wider_than_a_finish_band(self):
        touch = ws.implied_band(0.05, 0.10, touch=True)
        term = ws.implied_band(0.05, 0.10, touch=False)
        self.assertLess(touch["low"], term["low"])
        self.assertGreater(touch["high"], term["high"])

    def test_vol_scales_with_the_square_root_of_time(self):
        one = ws.sigma_for_sessions(0.252, 1)
        four = ws.sigma_for_sessions(0.252, 4)
        self.assertAlmostEqual(four / one, 2.0, places=6)

    def test_a_chain_quoting_whole_numbers_is_read_the_same_as_decimals(self):
        self.assertAlmostEqual(ws.sigma_for_sessions(35, 5),
                               ws.sigma_for_sessions(0.35, 5), places=9)

    def test_no_vol_is_none_not_a_flat_band(self):
        self.assertIsNone(ws.sigma_for_sessions(None, 5))
        self.assertIsNone(ws.implied_band(None, 0.1)["low"])


class StrikeMath(unittest.TestCase):
    """Every number re-derived by hand on three windows."""
    def setUp(self):
        self.spot = 100.0
        # Terminal outcomes -10%, 0%, +5%; paths reach -12%, -1%, +6%.
        self.windows = [
            {"low": -0.12, "high": 0.00, "term": -0.10, "date": None},
            {"low": -0.01, "high": 0.02, "term": 0.00, "date": None},
            {"low": 0.00, "high": 0.06, "term": 0.05, "date": None},
        ]

    def test_credit_breakeven_and_collateral(self):
        r = ws.evaluate_strike(opt(95, 0.90, 1.10), "put", self.spot,
                               self.windows, 7.0, None)
        self.assertAlmostEqual(r["credit"], 1.00)
        self.assertAlmostEqual(r["breakeven"], 94.00)
        self.assertAlmostEqual(r["collateral"], 94.00)
        self.assertAlmostEqual(r["cushion_pct"], 6.0)

    def test_touch_counts_the_path_and_itm_counts_the_close(self):
        r = ws.evaluate_strike(opt(95, 0.90, 1.10), "put", self.spot,
                               self.windows, 7.0, None)
        self.assertEqual(r["touch_n"], 1)      # only the -12% path reached 95
        self.assertEqual(r["itm_n"], 1)        # only the -10% close finished under
        r2 = ws.evaluate_strike(opt(99, 0.90, 1.10), "put", self.spot,
                                self.windows, 7.0, None)
        self.assertEqual(r2["touch_n"], 2)     # -12% and -1% both reached 99
        self.assertEqual(r2["itm_n"], 1)       # but only one closed below it

    def test_expected_value_is_the_average_of_the_real_outcomes(self):
        r = ws.evaluate_strike(opt(95, 0.90, 1.10), "put", self.spot,
                               self.windows, 7.0, None)
        # credit 1.00 each window; one window closes at 90 → loss 5.
        self.assertAlmostEqual(r["ev"], ((1 - 5) + 1 + 1) / 3)
        self.assertAlmostEqual(r["ev_pct"], r["ev"] / 94.0 * 100)

    def test_a_call_is_measured_upward(self):
        r = ws.evaluate_strike(opt(104, 0.40, 0.60), "call", self.spot,
                               self.windows, 7.0, None)
        self.assertEqual(r["touch_n"], 1)      # only the +6% path reached 104
        self.assertEqual(r["itm_n"], 1)        # the +5% close finished above
        self.assertAlmostEqual(r["collateral"], 100.0)   # covered: the shares

    def test_annualizing_uses_calendar_days_not_sessions(self):
        r = ws.evaluate_strike(opt(95, 0.90, 1.10), "put", self.spot,
                               self.windows, 7.0, None)
        self.assertAlmostEqual(r["ann_pct"], r["rbp_pct"] * 365 / 7)

    def test_the_expected_move_cushion_is_a_multiple(self):
        r = ws.evaluate_strike(opt(95, 0.90, 1.10), "put", self.spot,
                               self.windows, 7.0, 3.0)
        self.assertAlmostEqual(r["em_mult"], 6.0 / 3.0)

    def test_a_strike_with_no_quote_is_dropped_not_zeroed(self):
        self.assertIsNone(ws.evaluate_strike({"strike": 95}, "put", self.spot,
                                             self.windows, 7.0, None))


class Liquidity(unittest.TestCase):
    def test_a_tight_deep_strike_grades_above_a_wide_empty_one(self):
        good = ws.liquidity(opt(95, 1.00, 1.04, oi=5000, vol=900), 1.02)
        bad = ws.liquidity(opt(95, 0.50, 1.50, oi=3, vol=0), 1.00)
        self.assertGreater(good["score"], bad["score"])
        self.assertEqual(good["grade"], "A")
        self.assertEqual(bad["grade"], "D")

    def test_the_spread_is_reported_in_percent_of_mid(self):
        self.assertAlmostEqual(ws.liquidity(opt(95, 0.90, 1.10), 1.00)["spread_pct"], 20.0)


class Gates(unittest.TestCase):
    def setUp(self):
        self.windows = [{"low": -0.05, "high": 0.05, "term": 0.0, "date": None}] * 50

    def _row(self, o, side="put"):
        return ws.evaluate_strike(o, side, 100.0, self.windows, 7.0, None)

    def test_a_strike_history_finishes_through_too_often_is_refused(self):
        # Every window in this fixture closes flat, so a strike above spot
        # is assigned every time and one below it never is.
        hot = ws.evaluate_strike(opt(101, 0.95, 1.05), "put", 100.0, self.windows, 7.0, None)
        self.assertIn("history finished through it", ws._gate(hot, "put", 0.15))
        self.assertIsNone(ws._gate(self._row(opt(94, 0.95, 1.05)), "put", 0.15))

    def test_the_option_market_can_veto_a_strike_history_likes(self):
        # History never assigns this strike, but the chain's own delta puts
        # assignment at 30% — the engine refuses it and says which leg said so.
        row = self._row(opt(94, 0.95, 1.05, delta=-0.30))
        self.assertIn("option market puts assignment at 30%", ws._gate(row, "put", 0.15))

    def test_the_refusal_names_a_number_the_panel_also_prints(self):
        row = self._row(opt(94, 0.95, 1.05, delta=-0.30))
        reason = ws._gate(row, "put", 0.15)
        self.assertIn(f"{100 - row['p_otm']:.0f}%", reason)

    def test_a_penny_premium_is_not_a_trade(self):
        self.assertEqual(ws._gate(self._row(opt(90, 0.01, 0.03)), "put", 0.15),
                         "pays less than a nickel")

    def test_an_unfillable_spread_is_refused(self):
        self.assertEqual(ws._gate(self._row(opt(90, 0.50, 1.50)), "put", 0.15),
                         "bid/ask too wide to fill")

    def test_a_nickel_on_huge_collateral_is_not_income(self):
        # Five cents is a real trade on a $12 stock and a rounding error on
        # a $600 one. The floor has to scale with the money tied up.
        row = ws.evaluate_strike(opt(500, 0.05, 0.07), "put", 600.0,
                                 self.windows, 7.0, None)
        self.assertEqual(ws._gate(row, "put", 0.15),
                         "pays too little for the collateral it ties up")

    def test_a_real_credit_on_the_same_collateral_passes(self):
        row = ws.evaluate_strike(opt(500, 3.90, 3.94), "put", 600.0,
                                 self.windows, 7.0, None)
        self.assertIsNone(ws._gate(row, "put", 0.15))

    def test_a_deserted_strike_is_refused(self):
        self.assertEqual(ws._gate(self._row(opt(90, 0.95, 1.05, oi=2, vol=1)), "put", 0.15),
                         "almost no open interest or volume")

    def test_a_penny_wide_market_on_a_cheap_strike_still_fills(self):
        # 10c/12c is 18% of mid but only two cents wide — normal for the
        # far-OTM strikes this engine actually wants to sell.
        self.assertIsNone(ws._gate(self._row(opt(90, 0.10, 0.12)), "put", 0.15))

    def test_volume_alone_can_carry_a_new_strike(self):
        self.assertIsNone(ws._gate(self._row(opt(90, 0.95, 1.05, oi=0, vol=400)), "put", 0.15))


class Picking(unittest.TestCase):
    def setUp(self):
        closes = [100 * (1 + 0.015 * math.sin(i / 4.0)) for i in range(300)]
        self.bars = bars_from_closes(closes, span=0.005)
        self.windows = ws.forward_windows(self.bars, 5)

    def test_between_two_safe_strikes_the_better_paid_one_wins(self):
        chain = [opt(94, 0.95, 1.05), opt(93, 0.10, 0.12)]
        out = ws.pick_side(chain, "put", 100.0, self.windows, 7.0, 0.15, 3.0, "history")
        self.assertEqual(out["pick"]["strike"], 94)
        self.assertEqual(len(out["alts"]), 1)

    def test_a_richer_but_constantly_breached_strike_loses(self):
        # 99 pays more but sits inside the floor entirely.
        chain = [opt(99, 2.00, 2.10), opt(94, 0.95, 1.05)]
        out = ws.pick_side(chain, "put", 100.0, self.windows, 7.0, 0.15, 3.0, "history")
        self.assertEqual(out["pick"]["strike"], 94)

    def test_between_two_equally_safe_strikes_the_paying_one_wins(self):
        # On a fairly priced chain every strike has about the same expected
        # value, and the ranking used to break the tie toward whichever was
        # emptiest and safest — the one paying nothing. Income breaks it now.
        chain = [opt(92, 0.14, 0.16, oi=900), opt(95, 1.10, 1.14, oi=900)]
        out = ws.pick_side(chain, "put", 100.0, self.windows, 7.0, 0.15, 3.0, "history")
        self.assertEqual(out["pick"]["strike"], 95)

    def test_paying_more_does_not_beat_being_assigned_constantly(self):
        # ...but income must not simply buy the win: a strike history closes
        # through half the time is still refused, however rich.
        chain = [opt(99.5, 4.00, 4.10, oi=900), opt(95, 1.10, 1.14, oi=900)]
        out = ws.pick_side(chain, "put", 100.0, self.windows, 7.0, 0.15, 3.0, "history")
        self.assertEqual(out["pick"]["strike"], 95)

    def test_nothing_sellable_says_so_and_names_the_reason(self):
        out = ws.pick_side([opt(90, 0.01, 0.02)], "put", 100.0, self.windows,
                           7.0, 0.15, 3.0, "history")
        self.assertIsNone(out["pick"])
        self.assertIn("nickel", out["note"])

    def test_the_pick_carries_its_reasons_in_words(self):
        out = ws.pick_side([opt(94, 0.95, 1.05)], "put", 100.0, self.windows,
                           7.0, 0.15, 3.0, "history")
        why = " ".join(out["pick"]["why"])
        self.assertIn("matched windows", why)
        self.assertIn("fill quality", why)


class Walls(unittest.TestCase):
    def test_the_heaviest_open_interest_each_side_of_spot(self):
        calls = [opt(105, 1, 1.1, oi=200), opt(110, 0.5, 0.6, oi=9000)]
        puts = [opt(95, 1, 1.1, oi=4000), opt(90, 0.5, 0.6, oi=100)]
        w = ws.oi_walls(calls, puts, 100.0)
        self.assertEqual(w["call"]["strike"], 110)
        self.assertEqual(w["put"]["strike"], 95)

    def test_a_strike_on_the_wrong_side_of_spot_is_not_a_wall(self):
        # A huge put OI ABOVE spot is not the support level below it.
        puts = [opt(120, 20, 21, oi=99999), opt(95, 1, 1.1, oi=10)]
        self.assertEqual(ws.oi_walls([], puts, 100.0)["put"]["strike"], 95)


class WholePlan(unittest.TestCase):
    """End to end on a fixture that moves the way a real stock moves.

    A gentle fixture would let a broken engine pass: if history barely
    travels, every strike clears every floor. This walk runs at roughly 29%
    annualized with occasional jumps, and the chain is quoted at a matching
    implied vol, so the two legs of the floor are in the same league and the
    engine has to actually choose between them.
    """
    def setUp(self):
        import random
        rnd = random.Random(11)
        closes, px = [], 100.0
        for i in range(420):
            step = rnd.gauss(0, 0.018)
            if i % 47 == 0:                      # the occasional gap day
                step += rnd.choice((-0.05, 0.05))
            px *= (1 + step)
            closes.append(px)
        closes = [c * 100.0 / closes[-1] for c in closes]   # end at 100
        self.bars = bars_from_closes(closes, start=date(2024, 6, 3), span=0.008)
        self.last = date.fromisoformat(self.bars[-1]["date"])
        self.now = datetime.combine(self.last, datetime.min.time()) + timedelta(hours=13)
        self.exp = self.last + timedelta(days=(4 - self.last.weekday()) % 7 or 7)
        self.calls = [opt(k, round(v, 2), round(v + 0.04, 2), oi=800, iv=0.29)
                      for k, v in ((103, 1.60), (105, 1.05), (107, 0.62),
                                   (109, 0.34), (112, 0.15), (115, 0.07))]
        self.puts = [opt(k, round(v, 2), round(v + 0.04, 2), oi=800, iv=0.29)
                     for k, v in ((97, 1.55), (95, 1.02), (93, 0.60),
                                  (91, 0.33), (88, 0.14), (85, 0.06))]

    def plan(self, **kw):
        args = dict(spot=100.0, bars=self.bars, calls=self.calls, puts=self.puts,
                    expiration=self.exp, now=self.now)
        args.update(kw)
        return ws.build_plan(**args)

    def test_it_produces_a_pick_on_each_side(self):
        p = self.plan()
        self.assertTrue(p["ok"])
        self.assertIsNotNone(p["put"]["pick"])
        self.assertIsNotNone(p["call"]["pick"])

    def test_the_horizon_is_sessions_not_calendar_days(self):
        p = self.plan()
        self.assertEqual(p["sessions"], ws.sessions_until(self.exp, self.last))
        self.assertGreater(p["sample"]["n"], ws.MIN_SAMPLE)

    def test_the_zone_keeps_the_more_cautious_of_the_two_legs(self):
        p = self.plan()
        b = p["band"]
        for side, worse in (("low", min), ("high", max)):
            legs = [x for x in (b["history"][side], b["implied"][side]) if x is not None]
            self.assertAlmostEqual(b["zone"][side]["pct"], worse(legs), places=9)

    def test_the_zone_is_far_tighter_than_the_worst_case_extreme(self):
        # The complaint that started this: the worst thing the stock ever did
        # is not a target for next Friday. The zone must sit well inside the
        # widest window in the same history, both sides.
        p = self.plan()
        wins = ws.forward_windows(self.bars, p["sessions"])
        worst_low = min(w["low"] for w in wins) * 100.0
        best_high = max(w["high"] for w in wins) * 100.0
        z = p["band"]["zone"]
        self.assertGreater(z["low"]["pct"], worst_low)
        self.assertLess(z["high"]["pct"], best_high)
        # and it is not a rounding-error difference either
        self.assertLess(abs(z["low"]["pct"]), abs(worst_low) * 0.8)

    def test_the_ordinary_week_sits_inside_the_sell_zone(self):
        # Both are measured on where the week CLOSED, so the looser line can
        # never cross the tighter one — no reader has to reconcile two
        # different measures drawn on the same axis.
        b = self.plan()["band"]
        self.assertGreater(b["typical"]["low"]["pct"], b["zone"]["low"]["pct"])
        self.assertLess(b["typical"]["high"]["pct"], b["zone"]["high"]["pct"])

    def test_earnings_inside_the_expiry_is_flagged_in_the_verdict(self):
        p = self.plan(earnings_date=self.exp - timedelta(days=1))
        self.assertTrue(p["earnings"]["inside"])
        self.assertIn("earnings", p["verdict"]["line"])

    def test_earnings_after_the_expiry_is_not_flagged(self):
        p = self.plan(earnings_date=self.exp + timedelta(days=30))
        self.assertFalse(p["earnings"]["inside"])
        self.assertNotIn("earnings", p["verdict"]["line"])

    def test_the_users_own_strike_is_measured_the_same_way(self):
        p = self.plan(user_put=opt(99, 2.0, 2.1))
        u = p["put"]["user"]
        self.assertEqual(u["strike"], 99)
        self.assertIn("finished through it", u["gate"])

    def test_the_zone_is_an_assignment_line_not_a_touch_line(self):
        # A level is poked at far more often than it is closed through, so a
        # zone built on closes must sit inside the path band at the same odds.
        p = self.plan()
        wins = ws.forward_windows(self.bars, p["sessions"])
        path = ws.history_band(wins, ws.MAX_ITM)
        self.assertGreater(p["band"]["history"]["low"], path["low"] * 100.0 - 1e-9)

    def test_a_strike_reports_both_how_often_it_was_tested_and_assigned(self):
        pk = self.plan()["put"]["pick"]
        self.assertIsNotNone(pk["touch_pct"])
        self.assertIsNotNone(pk["itm_pct"])
        self.assertGreaterEqual(pk["touch_pct"], pk["itm_pct"])

    def test_the_put_and_call_sides_name_different_expected_value_bases(self):
        p = self.plan()
        self.assertEqual(p["put"]["pick"]["ev_basis"], "cash secured")
        self.assertEqual(p["call"]["pick"]["ev_basis"], "vs holding the shares")
        why = " ".join(p["call"]["pick"]["why"])
        self.assertIn("holding the shares", why)

    def test_a_chain_that_stops_short_relaxes_rather_than_going_blank(self):
        # Strikes that all sit inside the 15% line: the engine still answers,
        # marks the pick relaxed, and says which line it used.
        z = self.plan()["band"]["zone"]["low"]["price"]
        # just inside the strict line, still outside the looser one
        near = [opt(round(z + d, 2), 1.20, 1.26, oi=900) for d in (0.25, 0.50)]
        p = self.plan(puts=near)
        self.assertTrue(p["put"]["pick"]["relaxed"])
        self.assertIn("%", p["put"]["note"])
        self.assertIn("best one at", p["put"]["note"])

    def test_a_relaxed_pick_is_never_silently_presented_as_a_clean_one(self):
        p = self.plan()
        self.assertNotIn("relaxed", p["put"]["pick"])

    def test_no_chain_is_an_honest_no_not_a_pick(self):
        p = self.plan(calls=[], puts=[])
        self.assertFalse(p["ok"])
        self.assertTrue(p["note"])
        self.assertEqual(p["verdict"]["side"], "none")

    def test_a_dead_symbol_returns_a_reason_not_a_crash(self):
        p = self.plan(bars=[], calls=[], puts=[])
        self.assertFalse(p["ok"])
        self.assertIsNotNone(p["note"])

    def test_an_expired_contract_is_refused(self):
        p = self.plan(expiration=self.last - timedelta(days=7))
        self.assertFalse(p["ok"])
        self.assertIn("no sessions", p["note"])

    def test_no_price_is_refused(self):
        self.assertFalse(self.plan(spot=0)["ok"])

    def test_the_expected_move_prefers_the_quoted_straddle(self):
        p = self.plan()
        self.assertEqual(p["em"]["source"], "straddle")
        self.assertGreater(p["em"]["dollars"], 0)

    def test_every_published_number_is_finite(self):
        p = self.plan()
        def walk(node, path=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
            elif isinstance(node, float):
                self.assertTrue(math.isfinite(node), f"{path} is {node}")
        walk(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
