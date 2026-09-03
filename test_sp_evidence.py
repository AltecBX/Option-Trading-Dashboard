"""sp_evidence — measured breach history that cannot see the future, with
an honest denominator and a stated share of borrowed evidence."""
from __future__ import annotations

import json
import random
import unittest
from pathlib import Path

import sp_evidence as ev


def _bars(n=900, seed=1, vol=0.018, start=100.0, gap_every=0):
    rnd = random.Random(seed)
    out, px = [], start
    for i in range(n):
        o = px * (1 + (rnd.gauss(0, 0.02) if gap_every and i % gap_every == 0 else rnd.gauss(0, 0.002)))
        c = o * (1 + rnd.gauss(0, vol))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.005)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.005)))
        out.append({"date": f"d{i}", "open": o, "high": h, "low": lo, "close": c, "volume": 1})
        px = c
    return out


class TestNoLookahead(unittest.TestCase):
    def test_mutating_the_future_cannot_change_an_earlier_window(self):
        bars = _bars(700, seed=2)
        base = ev.breach_table(bars, horizons=(5, 21), ks=(1.0,))
        cut = 500
        mutated = [dict(b) for b in bars]
        for b in mutated[cut:]:
            for f in ("open", "high", "low", "close"):
                b[f] *= 2.5
        a = ev.breach_table(bars[:cut], horizons=(5, 21), ks=(1.0,))
        b = ev.breach_table(mutated[:cut], horizons=(5, 21), ks=(1.0,))
        self.assertEqual(a["cells"]["all"][21][1.0], b["cells"]["all"][21][1.0])
        self.assertEqual(a["cells"]["all"][5][1.0], b["cells"]["all"][5][1.0])
        full_m = ev.breach_table(mutated, horizons=(5, 21), ks=(1.0,))
        self.assertNotEqual(base["cells"]["all"][21][1.0], full_m["cells"]["all"][21][1.0])

    def test_the_state_read_uses_only_bars_up_to_i(self):
        closes = [100.0] * 30 + [101, 102, 103]
        st = ev._state_at(closes, 32, 0.2, 0.2)
        self.assertEqual(st["run"], "run_up")
        st2 = ev._state_at(closes + [90.0], 32, 0.2, 0.2)
        self.assertEqual(st, st2)


class TestTheDenominatorIsHonest(unittest.TestCase):
    def test_n_eff_is_the_non_overlapping_count(self):
        tab = ev.breach_table(_bars(800, seed=3), horizons=(21,), ks=(1.0,))
        c = tab["cells"]["all"][21][1.0]
        self.assertGreater(c["n"], 500)
        self.assertLessEqual(c["n_eff"], c["n"] // 21 + 1)
        self.assertGreaterEqual(c["n_eff"], c["n"] // 21 - 1)

    def test_the_interval_is_on_n_eff_not_n(self):
        tab = ev.breach_table(_bars(800, seed=3), horizons=(21,), ks=(1.0,))
        c = tab["cells"]["all"][21][1.0]
        width = c["put_itm_ci"]["hi"] - c["put_itm_ci"]["lo"]
        self.assertGreater(width, 0.12)

    def test_rates_are_bounded_and_touch_dominates_finish(self):
        tab = ev.breach_table(_bars(800, seed=5), horizons=(5, 21), ks=(0.5, 1.0, 2.0))
        for h in (5, 21):
            for k in (0.5, 1.0, 2.0):
                c = tab["cells"]["all"][h][k]
                for side in ("put", "call"):
                    self.assertGreaterEqual(c[f"{side}_touch"], c[f"{side}_itm"])
                    self.assertLessEqual(c[f"{side}_touch"], 1.0)
            self.assertGreaterEqual(tab["cells"]["all"][h][0.5]["put_itm"], tab["cells"]["all"][h][2.0]["put_itm"])


class TestExcursionFacts(unittest.TestCase):
    def test_overshoot_first_touch_and_gaps_are_reported(self):
        tab = ev.breach_table(_bars(900, seed=7, gap_every=17), horizons=(21,), ks=(1.0,))
        ctx = ev.strike_context(tab, 21, 1.0, "put")
        self.assertIsNotNone(ctx["overshoot_sigma"])
        self.assertGreater(ctx["overshoot_sigma"], 0)
        self.assertGreater(ctx["first_touch_bars"], 1)
        self.assertLessEqual(ctx["first_touch_bars"], 21)
        self.assertIsNotNone(ctx["gap_toward_strike_sigma_p95"])
        self.assertGreaterEqual(ctx["gap_toward_strike_sigma_max"], ctx["gap_toward_strike_sigma_p95"])
        self.assertIn("MEASURED", ctx["basis"])

    def test_no_table_means_none(self):
        self.assertIsNone(ev.strike_context(None, 21, 1.0, "put"))


class TestTheHierarchy(unittest.TestCase):
    def test_shrinkage_math_and_weights(self):
        s = ev.shrink(0.10, 40, 0.20, kappa=40)
        self.assertAlmostEqual(s["p"], 0.15, places=4)
        self.assertAlmostEqual(s["weight_own"], 0.5, places=3)
        s0 = ev.shrink(0.10, 0, 0.20, kappa=40)
        self.assertAlmostEqual(s0["p"], 0.20, places=4)
        self.assertEqual(s0["weight_own"], 0.0)
        sbig = ev.shrink(0.10, 4000, 0.20, kappa=40)
        self.assertLess(abs(sbig["p"] - 0.10), 0.002)

    def test_no_history_answers_with_the_prior_and_says_so(self):
        e = ev.evidence_for_strike(None, 21, 1.0, "put")
        self.assertEqual(e["weight_own"], 0.0)
        self.assertIn("prior", e["basis"])
        self.assertAlmostEqual(e["p_itm"], e["prior"]["itm"]["p"], places=6)

    def test_more_own_windows_means_more_own_weight(self):
        short = ev.breach_table(_bars(400, seed=8), horizons=(21,), ks=(1.0,))
        long_ = ev.breach_table(_bars(2500, seed=8), horizons=(21,), ks=(1.0,))
        a = ev.evidence_for_strike(short, 21, 1.0, "put")
        b = ev.evidence_for_strike(long_, 21, 1.0, "put")
        self.assertLess(a["weight_own"], b["weight_own"])
        self.assertGreater(b["n_eff"], a["n_eff"])

    def test_a_state_level_is_used_when_present_and_shrinks_toward_the_ticker(self):
        tab = ev.breach_table(_bars(2500, seed=9), horizons=(21,), ks=(0.5, 1.0, 2.0))
        base = ev.evidence_for_strike(tab, 21, 1.0, "put")
        st = ev.evidence_for_strike(tab, 21, 1.0, "put", state="run_up")
        self.assertEqual(len(st["levels"]), 2)
        self.assertEqual(st["levels"][1]["level"], "ticker_in_run_up")
        raw = st["levels"][1]["itm_raw"]
        lo, hi = sorted((raw, base["p_itm"]))
        self.assertGreaterEqual(st["p_itm"] + 1e-9, lo)
        self.assertLessEqual(st["p_itm"] - 1e-9, hi)

    def test_an_unknown_state_falls_back_to_all(self):
        tab = ev.breach_table(_bars(900, seed=10), horizons=(21,), ks=(1.0,))
        a = ev.evidence_for_strike(tab, 21, 1.0, "put", state="no_such_state")
        b = ev.evidence_for_strike(tab, 21, 1.0, "put")
        self.assertEqual(a["p_itm"], b["p_itm"])

    def test_interpolation_between_measured_distances(self):
        tab = ev.breach_table(_bars(1500, seed=11), horizons=(21,), ks=(1.0, 2.0))
        a = ev.evidence_for_strike(tab, 21, 1.0, "put")["levels"][0]["itm_raw"]
        b = ev.evidence_for_strike(tab, 21, 2.0, "put")["levels"][0]["itm_raw"]
        m = ev.evidence_for_strike(tab, 21, 1.5, "put")["levels"][0]["itm_raw"]
        self.assertAlmostEqual(m, (a + b) / 2, places=4)


class TestThePriorIsTheMeasuredFixture(unittest.TestCase):
    def test_prior_uses_the_pooled_ratio(self):
        fx = json.loads((Path(__file__).with_name("fixtures") / "sp_universe_calibration.json").read_text())
        p = ev.prior_itm(21, 2.0, "put")
        self.assertAlmostEqual(p["ratio"], fx["itm_by_k"]["2.0"]["ratio"], places=6)
        self.assertGreater(p["p"], p["model"])
        p1 = ev.prior_itm(21, 1.0, "put")
        self.assertLess(abs(p1["ratio"] - 1.0), 0.05)
        t = ev.prior_touch(21, 1.0)
        self.assertGreater(t["n_pool"], 10000)

    def test_prior_probabilities_are_ordered_in_k(self):
        ps = [ev.prior_itm(21, k, "put")["p"] for k in (0.5, 1.0, 1.5, 2.0)]
        self.assertEqual(ps, sorted(ps, reverse=True))


class TestCurrentState(unittest.TestCase):
    def test_reads_regime_and_run(self):
        bars = _bars(400, seed=12)
        c = bars[-4]["close"]
        for j, b in enumerate(bars[-3:]):
            b["close"] = c * (1.01 ** (j + 1))
        st = ev.current_state(bars)
        self.assertEqual(st["run"], "run_up")
        self.assertIn("all", st["states"])
        self.assertIn(st["vol"], ("vol_low", "vol_mid", "vol_high"))

    def test_short_history_is_all_only(self):
        st = ev.current_state(_bars(10))
        self.assertEqual(st["states"], ["all"])


if __name__ == "__main__":
    unittest.main()
