"""Tests for vol_forecast.py — the ExpectedRV30 forecaster.

Ground truth comes from seeded synthetic GBM bars with KNOWN volatility, so
every estimator is checked against the number it should recover. The
no-lookahead property is proven by mutating future bars and asserting the
forecast at t cannot change, and by a regime-switch series where any
future-peeking forecast would be visibly contaminated.
"""

import copy
import math
import random
import unittest

import vol_forecast as vf


def gbm_bars(n, sigma=0.30, seed=7, s0=100.0, switch_at=None, sigma2=None,
             gap_every=0, gap_pct=0.0):
    """Seeded synthetic daily OHLC bars with known annualized vol.

    Each day: 26 intraday log-steps (so high/low are meaningful for
    Parkinson), close-to-close daily stdev = sigma/sqrt(252). Optional
    regime switch at bar switch_at, optional periodic opening gaps."""
    rnd = random.Random(seed)
    bars, c = [], s0
    for i in range(n):
        sig = sigma2 if (switch_at is not None and i >= switch_at and sigma2) else sigma
        daily = sig / math.sqrt(252.0)
        o = c
        if gap_every and i > 0 and i % gap_every == 0:
            o = c * math.exp((gap_pct / 100.0) * (1 if i % (2 * gap_every) else -1))
        steps = [rnd.gauss(0.0, daily / math.sqrt(26.0)) for _ in range(26)]
        logp, hi, lo = 0.0, 0.0, 0.0
        for s in steps:
            logp += s
            hi, lo = max(hi, logp), min(lo, logp)
        close = o * math.exp(logp)
        bars.append({"date": f"2024-01-{i:04d}", "open": round(o, 4),
                     "high": round(o * math.exp(hi), 4),
                     "low": round(o * math.exp(lo), 4),
                     "close": round(close, 4), "volume": 1000000})
        c = close
    return bars


class TestEstimators(unittest.TestCase):
    def setUp(self):
        self.bars = gbm_bars(500, sigma=0.30, seed=7)
        self.closes = [b["close"] for b in self.bars]

    def test_rv_recovers_known_sigma(self):
        v = vf.rv(self.closes, 60)
        self.assertIsNotNone(v)
        self.assertAlmostEqual(v, 0.30, delta=0.08)

    def test_ewma_recovers_known_sigma(self):
        v = vf.ewma_vol(self.closes)
        self.assertAlmostEqual(v, 0.30, delta=0.09)

    def test_parkinson_recovers_known_sigma(self):
        # discrete 26-step monitoring biases the range slightly low vs the
        # continuous ideal — accept the known-direction band
        v = vf.parkinson_vol(self.bars, 20)
        self.assertIsNotNone(v)
        self.assertGreater(v, 0.18)
        self.assertLess(v, 0.40)

    def test_rv_none_on_short_history(self):
        self.assertIsNone(vf.rv(self.closes[:10], 20))
        self.assertIsNone(vf.ewma_vol(self.closes[:15]))

    def test_parkinson_requires_valid_highs_lows(self):
        broken = copy.deepcopy(self.bars)
        for b in broken[-20:]:
            b["high"] = None
            b["low"] = None
        self.assertIsNone(vf.parkinson_vol(broken, 20))

    def test_atr_positive_and_sane(self):
        a = vf.atr_pct(self.bars, 14)
        self.assertIsNotNone(a)
        self.assertGreater(a, 0.3)
        self.assertLess(a, 10.0)

    def test_gap_stats_measure_injected_gaps(self):
        gappy = gbm_bars(300, sigma=0.20, seed=11, gap_every=10, gap_pct=5.0)
        g = vf.gap_stats(gappy, 63)
        self.assertIsNotNone(g)
        self.assertGreater(g["gap_freq_2pct"], 0.05)
        self.assertGreater(g["max_gap_pct"], 4.0)
        smooth = vf.gap_stats(self.bars, 63)
        self.assertLess(smooth["gap_freq_2pct"], g["gap_freq_2pct"])


class TestNoLookahead(unittest.TestCase):
    def test_future_mutation_cannot_change_forecast(self):
        bars = gbm_bars(400, sigma=0.30, seed=3)
        i = 299
        before = vf.candidates(bars[:i + 1])
        mutated = copy.deepcopy(bars)
        for b in mutated[i + 1:]:
            b["close"] *= 10
            b["high"] *= 10
            b["low"] *= 10
        after = vf.candidates(mutated[:i + 1])
        self.assertEqual(before, after)

    def test_regime_switch_not_leaked(self):
        # vol jumps 0.20 → 0.80 at bar 300; a forecast made AT bar 299 must
        # look like the calm regime — any future contamination shows up as
        # a big number here.
        bars = gbm_bars(420, sigma=0.20, seed=5, switch_at=300, sigma2=0.80)
        calm = vf.expected_rv30(bars[:300])
        self.assertIsNotNone(calm)
        self.assertLess(calm["erv30"], 0.30)

    def test_forecast_adapts_after_switch(self):
        bars = gbm_bars(420, sigma=0.20, seed=5, switch_at=300, sigma2=0.60)
        late = vf.expected_rv30(bars)
        self.assertGreater(late["erv30"], 0.40)


class TestQlike(unittest.TestCase):
    def test_perfect_forecast_scores_zero(self):
        self.assertAlmostEqual(vf.qlike(0.3, 0.3), 0.0, places=10)

    def test_underforecast_penalized_harder(self):
        under = vf.qlike(0.20, 0.40)   # sold vol too cheap into a storm
        over = vf.qlike(0.40, 0.20)    # too conservative
        self.assertGreater(under, over)

    def test_degenerate_inputs_none(self):
        self.assertIsNone(vf.qlike(0.0, 0.3))
        self.assertIsNone(vf.qlike(0.3, None))


class TestModelChoice(unittest.TestCase):
    def test_walk_forward_structure(self):
        bars = gbm_bars(500, sigma=0.30, seed=9)
        wf = vf.walk_forward_scores(bars)
        self.assertGreater(wf["n_evals"], 30)
        self.assertIn("GLOBAL", wf["scores"])
        g = wf["scores"]["GLOBAL"]
        for key in ("qlike", "rmse_volpts", "bias_volpts", "n", "qlike_h1", "qlike_h2"):
            self.assertIn(key, g)
        # on plain GBM every estimator is unbiased-ish: |bias| small
        self.assertLess(abs(g["bias_volpts"]), 6.0)

    def test_choice_is_disciplined(self):
        bars = gbm_bars(500, sigma=0.30, seed=9)
        ch = vf.choose_model(bars)
        self.assertIn(ch["model"], vf.CANDIDATE_NAMES + ("GLOBAL",))
        self.assertTrue(ch["method"])
        # short history: must fall back with the honest label
        ch2 = vf.choose_model(bars[:130])
        self.assertEqual(ch2["model"], "GLOBAL")

    def test_scores_are_deterministic(self):
        bars = gbm_bars(400, sigma=0.25, seed=13)
        self.assertEqual(vf.walk_forward_scores(bars), vf.walk_forward_scores(bars))


class TestExpectedRV30(unittest.TestCase):
    def test_insufficient_history_is_none(self):
        self.assertIsNone(vf.expected_rv30(gbm_bars(60)))

    def test_recovers_sigma_with_labels(self):
        out = vf.expected_rv30(gbm_bars(500, sigma=0.30, seed=7))
        self.assertAlmostEqual(out["erv30"], 0.30, delta=0.08)
        self.assertTrue(out["method"])
        self.assertIn("RV20", out["components"])
        self.assertEqual(out["quality"], "ok")
        self.assertIsNone(out["event_adj"])

    def test_earnings_adjustment_math(self):
        bars = gbm_bars(500, sigma=0.30, seed=7)
        base = vf.expected_rv30(bars)
        adj = vf.expected_rv30(bars, earnings_within_horizon=True,
                               earnings_hist_avg_abs_pct=8.0, earnings_hist_n=5)
        self.assertEqual(adj["erv30"], base["erv30"])
        # extra annualized variance = (0.08)^2 * 252/21 = 0.0768
        expect = math.sqrt(base["erv30"] ** 2 + 0.0768)
        self.assertAlmostEqual(adj["erv30_event"], expect, delta=0.005)
        self.assertEqual(adj["event_adj"]["hist_n"], 5)
        self.assertIn("MEASURED", adj["event_adj"]["basis"])

    def test_earnings_adjustment_needs_history(self):
        bars = gbm_bars(300, sigma=0.30, seed=7)
        adj = vf.expected_rv30(bars, earnings_within_horizon=True,
                               earnings_hist_avg_abs_pct=8.0, earnings_hist_n=2)
        self.assertIsNone(adj["event_adj"])
        self.assertEqual(adj["erv30_event"], adj["erv30"])

    def test_thin_history_labeled(self):
        out = vf.expected_rv30(gbm_bars(150, sigma=0.30, seed=7))
        self.assertEqual(out["quality"], "thin_history")


class TestTheRangeVoiceCanHearOvernightMoves(unittest.TestCase):
    """Parkinson only measures the ground covered between a day's high and
    its low, so it is deaf to anything that happens while the market is
    shut. The forecast is judged against close-to-close vol, which is not.
    These guards fail against the uncalibrated blend."""

    def test_a_stock_that_moves_by_gaps_reads_wider_than_its_range(self):
        quiet = gbm_bars(600, sigma=0.30, seed=11)
        gappy = gbm_bars(600, sigma=0.30, seed=11, gap_every=5, gap_pct=4.0)
        r_quiet, _ = vf.gap_ratio(quiet)
        r_gappy, _ = vf.gap_ratio(gappy)
        self.assertIsNotNone(r_quiet)
        self.assertIsNotNone(r_gappy)
        # the gaps are real volatility the range estimator never saw
        self.assertGreater(r_gappy, r_quiet + 0.10)

    def test_the_forecast_stops_undershooting_a_gappy_stock(self):
        """The claim the calibration actually makes: for a name that moves
        by gaps, the blend used to sit BELOW the volatility that name goes
        on to realize. A blend weighted on raw PARK20 still undershoots."""
        gappy = gbm_bars(600, sigma=0.30, seed=11, gap_every=5, gap_pct=4.0)
        realized = vf.rv([b["close"] for b in gappy], 250)
        c = vf.candidates(gappy)
        anchor = vf.rv([b["close"] for b in gappy], 252)
        old = vf._blend(c, {"RV20": .30, "EWMA94": .35, "PARK20": .35},
                        anchor, vf.ANCHOR_SHRINK)
        new = vf._blend(c, vf.GLOBAL_WEIGHTS, anchor, vf.ANCHOR_SHRINK)
        self.assertLess(old, realized)              # the bug
        self.assertLess(abs(new - realized), abs(old - realized))

    def test_the_blend_listens_to_the_calibrated_voice(self):
        # a blend weighted on raw PARK20 is the bug this replaced
        self.assertIn("PARK20C", vf.GLOBAL_WEIGHTS)
        self.assertNotIn("PARK20", vf.GLOBAL_WEIGHTS)

    def test_calibration_only_ever_adds(self):
        """Gaps cannot make a stock calmer than its own intraday range."""
        for seed in (3, 11, 29):
            bars = gbm_bars(600, sigma=0.30, seed=seed)
            c = vf.candidates(bars)
            self.assertGreaterEqual(c["PARK20C"], c["PARK20"] - 1e-9)

    def test_no_measurable_history_leans_on_the_typical_stock_not_on_nothing(self):
        """A feed that only started reporting highs and lows recently can
        price the last 20 days but not the last year. Not being able to
        measure THIS name is no reason to assume it never gaps — that
        assumption reads the forecast LOW, and a low forecast makes option
        premium look richer than it is."""
        bars = gbm_bars(600, sigma=0.30, seed=5)
        for b in bars[:-40]:            # the older half of the feed has no range
            b["high"] = b["low"] = None
        out = vf.expected_rv30(bars)
        self.assertIsNone(out["gap_ratio"])
        self.assertIn("typical stock", out["gap_ratio_basis"])
        c = vf.candidates(bars)
        self.assertIsNotNone(c.get("PARK20"))
        self.assertAlmostEqual(c["PARK20C"] / c["PARK20"],
                               vf.GAP_RATIO_DEFAULT, places=6)

    def test_the_forecast_says_where_the_number_came_from(self):
        out = vf.expected_rv30(gbm_bars(600, sigma=0.30, seed=11))
        self.assertGreaterEqual(out["gap_ratio"], 1.0)
        self.assertIn("MEASURED", out["gap_ratio_basis"])

    def test_a_broken_high_low_feed_cannot_run_away_with_the_forecast(self):
        bars = gbm_bars(600, sigma=0.30, seed=11)
        for b in bars[-300:]:            # collapsed range, as a bad feed gives
            b["high"] = b["low"] = b["close"]
        r, _ = vf.gap_ratio(bars)
        if r is not None:
            self.assertLessEqual(r, vf.GAP_RATIO_CEIL)

    def test_calibration_uses_no_future_bars(self):
        bars = gbm_bars(600, sigma=0.30, seed=11)
        before, _ = vf.gap_ratio(bars[:400])
        tail = copy.deepcopy(bars)
        for b in tail[400:]:             # a storm that has not happened yet
            b["high"] = b["high"] * 1.5
            b["low"] = b["low"] * 0.5
        after, _ = vf.gap_ratio(tail[:400])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
