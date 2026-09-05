"""test_spike_evidence.py — what a stock does after it has already run.

The module answers one question with real money behind it: if I sell a call
struck above the level this stock has already reached today, what does the
measured record say it pays back? The guards below are the properties that
have to hold for that answer to be trustworthy.

  * sigma is the unit, and it is computed point-in-time (a session never
    sees its own return in its own volatility);
  * a strike further out is never MORE likely to be closed above, and never
    settles for more — a table that violates that is broken, not subtle;
  * you cannot close above a strike you never touched;
  * a ticker with little of its own history is answered by the universe,
    says so, and never claims to be MEASURED;
  * the clock only ever reduces the settlement, and says whether the
    reduction was measured or assumed;
  * off the edge of the measured grid it clamps and flags, rather than
    extrapolating a number nobody measured.
"""
import json
import math
import unittest
from pathlib import Path

import spike_evidence as se


def _walk(n=400, start=100.0, step=0.01, seed=3):
    """A deterministic series with a known, steady daily sigma."""
    import random
    rng = random.Random(seed)
    out, px = [], start
    for i in range(n):
        r = rng.gauss(0, step)
        o = px
        px = px * math.exp(r)
        hi, lo = max(o, px) * 1.004, min(o, px) * 0.996
        out.append({"date": f"2024-{1 + i // 31:02d}-{1 + i % 28:02d}",
                    "open": o, "high": hi, "low": lo, "close": px, "volume": 1e6})
    return out


def _with_spike(bars, at=-1, high_mult=1.20, close_mult=1.05):
    """Force one session to run hard and close well off its high."""
    b = [dict(x) for x in bars]
    pc = b[at - 1]["close"]
    b[at]["open"] = pc * 1.01
    b[at]["high"] = pc * high_mult
    b[at]["close"] = pc * close_mult
    b[at]["low"] = pc * 0.99
    return b


class Sigma(unittest.TestCase):
    def test_daily_sigma_is_point_in_time(self):
        bars = _walk(120)
        # a huge move appended must not change the sigma computed BEFORE it
        before = se.daily_sigma(bars, end=len(bars))
        bars2 = bars + [{"date": "2024-12-31", "open": 100, "high": 200,
                         "low": 100, "close": 190, "volume": 1}]
        self.assertAlmostEqual(before, se.daily_sigma(bars2, end=len(bars)), places=12)

    def test_sigma_needs_enough_bars(self):
        self.assertIsNone(se.daily_sigma(_walk(6)))

    def test_in_sigma_converts_a_move_to_the_stocks_own_units(self):
        # a 16% run on a stock whose daily sigma is 3.2% is about five sigma
        s = se.in_sigma(116.0, 100.0, 0.032)
        self.assertAlmostEqual(s, math.log(1.16) / 0.032, places=9)
        self.assertAlmostEqual(s, 4.64, places=1)

    def test_in_sigma_refuses_nonsense(self):
        for args in ((0, 100, 0.03), (100, 0, 0.03), (100, 100, 0), (None, 100, 0.03)):
            self.assertIsNone(se.in_sigma(*args))

    def test_the_same_percentage_is_a_different_event_on_a_wilder_stock(self):
        quiet = se.in_sigma(103.6, 100.0, 0.015)
        wild = se.in_sigma(103.6, 100.0, 0.051)
        self.assertGreater(quiet, 2.0)
        self.assertLess(wild, 1.0)


class Prior(unittest.TestCase):
    def test_the_fixture_ships_and_is_well_formed(self):
        p = Path(__file__).resolve().parent / "fixtures" / "spike_universe.json"
        self.assertTrue(p.exists(), "the measured universe table must ship with the module")
        d = json.loads(p.read_text())
        self.assertGreater(d["n_sessions"], 100000)
        self.assertGreater(d["n_names"], 100)
        for mk, row in d["cells"].items():
            self.assertGreater(row["n"], 0, mk)
            for bk, cell in row["strikes"].items():
                self.assertGreaterEqual(cell["p_touch"], cell["p_close"] - 1e-9,
                                        f"{mk}/{bk}: closed above a strike it never touched")
                for f in ("p_close", "p_touch"):
                    self.assertGreaterEqual(cell[f], 0.0)
                    self.assertLessEqual(cell[f], 1.0)
                self.assertGreaterEqual(cell["settle_sigma"], 0.0)

    def test_a_further_strike_is_never_more_likely_nor_more_expensive(self):
        for move in (1.5, 2.0, 3.0, 4.0):
            prev_p, prev_s = 1.1, 1e9
            for beyond in (0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0):
                c = se.prior_cell(move, beyond)
                self.assertIsNotNone(c)
                self.assertLessEqual(c["p_close"], prev_p + 1e-9,
                                     f"move {move}, beyond {beyond}")
                self.assertLessEqual(c["settle_sigma"], prev_s + 1e-9)
                prev_p, prev_s = c["p_close"], c["settle_sigma"]

    def test_it_interpolates_between_measured_points(self):
        lo = se.prior_cell(3.0, 0.5)["p_close"]
        hi = se.prior_cell(4.0, 0.5)["p_close"]
        mid = se.prior_cell(3.5, 0.5)["p_close"]
        self.assertTrue(min(lo, hi) <= mid <= max(lo, hi))

    def test_off_the_grid_it_clamps_and_says_so(self):
        c = se.prior_cell(40.0, 0.5)
        self.assertTrue(c["move_clamped"])
        far = se.prior_cell(3.0, 99.0)
        self.assertTrue(far["clamped"])
        # and never invents a negative or absurd probability out there
        self.assertGreaterEqual(far["p_close"], 0.0)

    def test_the_measured_headline_holds(self):
        # a big mover rarely finishes at its high, and less often the bigger it is
        four = se.prior_cell(4.0, 0.0)["p_finishes_at_high"]
        one = se.prior_cell(1.0, 0.0)["p_finishes_at_high"]
        self.assertLess(four, one)
        self.assertLess(four, 0.10)


class Shrinkage(unittest.TestCase):
    def test_no_history_of_its_own_means_the_universe_answers(self):
        self.assertEqual(se.shrink(None, 0, 0.30), 0.30)
        self.assertEqual(se.shrink(0.9, 0, 0.30), 0.30)

    def test_plenty_of_its_own_history_wins(self):
        self.assertAlmostEqual(se.shrink(0.10, 100000, 0.30, kappa=60), 0.10, places=3)

    def test_a_thin_sample_lands_between_and_leans_on_the_universe(self):
        v = se.shrink(0.10, 10, 0.30, kappa=60)
        self.assertTrue(0.10 < v < 0.30)
        self.assertGreater(v, 0.25, "ten sessions should not overturn the universe")

    def test_without_a_universe_value_it_returns_what_it_has(self):
        self.assertEqual(se.shrink(0.42, 5, None), 0.42)


class Clock(unittest.TestCase):
    def test_the_fallback_is_the_clock_and_admits_it(self):
        r = se.remaining_session(0.5)
        self.assertAlmostEqual(r["variance_left"], 0.5)
        self.assertAlmostEqual(r["scale"], math.sqrt(0.5))
        self.assertIn("MODELED", r["basis"])

    def test_a_measured_profile_is_used_and_labeled(self):
        prof = [(0.0, 1.0), (0.5, 0.30), (1.0, 0.0)]
        r = se.remaining_session(0.5, prof)
        self.assertAlmostEqual(r["variance_left"], 0.30)
        self.assertEqual(r["basis"], "MEASURED")
        mid = se.remaining_session(0.25, prof)
        self.assertTrue(0.30 < mid["variance_left"] < 1.0)

    def test_the_clock_never_runs_backwards_or_past_the_bell(self):
        self.assertEqual(se.remaining_session(-5)["variance_left"], 1.0)
        self.assertEqual(se.remaining_session(9)["variance_left"], 0.0)

    def test_later_in_the_day_is_never_more_risk(self):
        prev = 1.1
        for e in (0.0, 0.25, 0.5, 0.75, 1.0):
            v = se.remaining_session(e)["scale"]
            self.assertLessEqual(v, prev + 1e-9)
            prev = v


class TickerTable(unittest.TestCase):
    def test_it_counts_this_stocks_own_runs(self):
        bars = _with_spike(_walk(300), at=-1, high_mult=1.20, close_mult=1.05)
        t = se.ticker_table(bars)
        self.assertGreater(t["n_sessions"], 200)
        self.assertTrue(t["cells"])
        for row in t["cells"].values():
            self.assertEqual(row["n"], row["n_eff"],
                             "one spike is one session, so trials are independent")

    def test_a_session_that_closed_far_off_its_high_is_recorded_that_way(self):
        bars = _with_spike(_walk(300), at=-1, high_mult=1.30, close_mult=1.02)
        t = se.ticker_table(bars)
        top = sorted((float(k) for k in t["cells"]), reverse=True)[0]
        self.assertLess(t["cells"][f"{top:g}"]["p_finishes_at_high"], 1.0)

    def test_an_empty_or_short_series_yields_nothing_rather_than_guessing(self):
        self.assertEqual(se.ticker_table([])["cells"], {})
        self.assertEqual(se.ticker_table(_walk(5))["cells"], {})


class Evidence(unittest.TestCase):
    def test_a_name_with_no_record_is_graded_pooled(self):
        ev = se.evidence_for_strike(3.0, 0.5, table=None)
        self.assertEqual(ev["grade"], "POOLED")
        self.assertEqual(ev["n_own"], 0)
        self.assertEqual(ev["weight_own"], 0.0)
        self.assertIsNone(ev["ci_own"])
        self.assertIsNotNone(ev["p_close_above"])

    def test_touching_is_never_rarer_than_closing_above(self):
        for move in (1.5, 3.0, 5.0):
            for beyond in (0.0, 0.5, 1.0, 2.0):
                ev = se.evidence_for_strike(move, beyond)
                self.assertGreaterEqual(ev["p_touch"], ev["p_close_above"] - 1e-9)

    def test_a_further_strike_is_safer_and_cheaper(self):
        prev_p, prev_s = 1.1, 1e9
        for beyond in (0.0, 0.25, 0.5, 1.0, 2.0, 3.0):
            ev = se.evidence_for_strike(4.0, beyond)
            self.assertLessEqual(ev["p_close_above"], prev_p + 1e-9)
            self.assertLessEqual(ev["settle_sigma"], prev_s + 1e-9)
            prev_p, prev_s = ev["p_close_above"], ev["settle_sigma"]

    def test_its_own_record_moves_the_answer_and_is_disclosed(self):
        bars = _with_spike(_walk(600), at=-1, high_mult=1.15, close_mult=1.03)
        t = se.ticker_table(bars)
        ev = se.evidence_for_strike(1.5, 0.5, table=t)
        self.assertGreater(ev["n_own"], 0)
        self.assertGreater(ev["weight_own"], 0.0)
        self.assertLessEqual(ev["weight_own"], 1.0)
        self.assertEqual(len(ev["levels"]), 2)
        self.assertEqual(ev["levels"][0]["level"], "this ticker")
        self.assertEqual(ev["levels"][1]["level"], "universe")

    def test_the_basis_names_the_upper_bound(self):
        ev = se.evidence_for_strike(4.0, 0.5)
        self.assertIn("whole remaining session", ev["basis"])


class Settlement(unittest.TestCase):
    def test_dollars_are_sigma_times_the_share_price(self):
        ev = se.evidence_for_strike(4.0, 0.5)
        st = se.settlement(ev, sigma=0.03, prev_close=100.0)
        self.assertAlmostEqual(st["dollars"], ev["settle_sigma"] * 0.03 * 100.0, places=9)
        self.assertEqual(st["session_scale"], 1.0)

    def test_the_clock_only_ever_reduces_what_it_pays_back(self):
        ev = se.evidence_for_strike(4.0, 0.5)
        full = se.settlement(ev, 0.03, 100.0)["dollars"]
        prev = full
        for e in (0.0, 0.25, 0.5, 0.9):
            d = se.settlement(ev, 0.03, 100.0, elapsed_frac=e)["dollars"]
            self.assertLessEqual(d, prev + 1e-9)
            prev = d
        self.assertLess(prev, full)

    def test_it_says_whether_the_clock_was_measured_or_assumed(self):
        ev = se.evidence_for_strike(3.0, 0.5)
        self.assertIn("MODELED", se.settlement(ev, 0.03, 100.0, 0.5)["session_basis"])
        prof = [(0.0, 1.0), (0.5, 0.3), (1.0, 0.0)]
        self.assertEqual(se.settlement(ev, 0.03, 100.0, 0.5, prof)["session_basis"], "MEASURED")

    def test_missing_inputs_are_unavailable_not_zero(self):
        self.assertEqual(se.settlement({}, 0.03, 100.0)["basis"], "UNAVAILABLE")
        self.assertIsNone(se.settlement({}, 0.03, 100.0)["dollars"])


class Assess(unittest.TestCase):
    def setUp(self):
        self.bars = _walk(400, step=0.032)

    def test_the_edge_is_the_credit_less_what_it_pays_back(self):
        a = se.assess(spot=116.0, strike=117.0, prev_close=100.0, bars=self.bars,
                      credit=1.15, elapsed_frac=0.5)
        self.assertIsNotNone(a)
        self.assertAlmostEqual(a["edge_per_share"], 1.15 - a["settlement"]["dollars"], places=9)
        self.assertAlmostEqual(a["edge_per_contract"], a["edge_per_share"] * 100, places=6)

    def test_the_same_strike_flips_from_bad_to_good_as_the_session_runs_out(self):
        early = se.assess(116.0, 117.0, 100.0, self.bars, credit=1.15, elapsed_frac=0.0)
        late = se.assess(116.0, 117.0, 100.0, self.bars, credit=1.15, elapsed_frac=0.9)
        self.assertLess(early["edge_per_share"], late["edge_per_share"])

    def test_it_reports_the_move_in_both_units(self):
        a = se.assess(116.0, 117.0, 100.0, self.bars, credit=1.0)
        self.assertAlmostEqual(a["move_pct"], 16.0, places=6)
        self.assertAlmostEqual(a["strike_pct"], 17.0, places=6)
        self.assertGreater(a["move_sigma"], 0)
        self.assertGreater(a["strike_sigma"], a["move_sigma"])
        self.assertAlmostEqual(a["beyond_sigma"], a["strike_sigma"] - a["move_sigma"], places=9)

    def test_without_a_credit_it_still_reports_the_evidence(self):
        a = se.assess(116.0, 117.0, 100.0, self.bars)
        self.assertNotIn("edge_per_share", a)
        self.assertIsNotNone(a["evidence"]["p_close_above"])

    def test_too_little_history_returns_nothing_rather_than_a_guess(self):
        self.assertIsNone(se.assess(116.0, 117.0, 100.0, _walk(5), credit=1.0))

    def test_a_strike_below_the_current_price_is_not_pretended_to_be_safe(self):
        a = se.assess(116.0, 110.0, 100.0, self.bars, credit=7.0)
        self.assertLess(a["beyond_sigma"], 0)
        # already in the money: the measured close-above rate must be high
        self.assertGreater(a["evidence"]["p_close_above"], 0.3)


if __name__ == "__main__":
    unittest.main()
