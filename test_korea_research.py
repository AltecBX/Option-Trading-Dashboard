"""Tests for KOREA LEAD V2 — korea_research_engine.py and korea_research.py.

EVERY FIXTURE HERE IS FROZEN AND SYNTHETIC.

Not one assertion in this file depends on what a provider returned this
morning. A test whose expected value is a live correlation is not a test —
it is a tripwire that fires when a vendor revises a price, and it trains
whoever sees it red to stop reading it. The long-run relationships against
current provider data are reproduced by korea_research.validation()
instead, which is a report to be read rather than an assertion to be
satisfied.

So the statistics here are checked against values computed by hand, against
published worked examples, and against data built to have a known answer —
including data built so that a model which cheats gets it WRONG.
"""

import math
import tempfile
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import korea_lead as kl
import korea_lead_engine as kle
import korea_research as kr
import korea_research_engine as kre

ET = ZoneInfo("America/New_York")


def weekdays(n, end="2026-03-04"):
    d = date.fromisoformat(end)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def rows_from(xs, ys, extra=None):
    """Dated rows carrying one predictor and one outcome."""
    days = weekdays(len(xs))
    out = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        r = {"date": days[i].isoformat(), "korea": x, "gap": y,
             "opening_gap": y}
        for k, v in (extra or {}).items():
            r[k] = v[i]
        out.append(r)
    return out


# A deterministic pseudo-random stream, so a "noise" fixture is the same
# noise on every machine and every run.
def noise(n, seed=7, scale=1.0):
    out = []
    state = seed
    for _ in range(n):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        out.append(((state / 0x7FFFFFFF) - 0.5) * 2.0 * scale)
    return out


# ── the numerics ────────────────────────────────────────────────────────────

class TestNumerics(unittest.TestCase):

    def test_the_matrix_inverse_is_an_inverse(self):
        m = [[4.0, 7.0], [2.0, 6.0]]
        inv = kre.invert(m)
        prod = [[sum(m[i][k] * inv[k][j] for k in range(2)) for j in range(2)]
                for i in range(2)]
        self.assertAlmostEqual(prod[0][0], 1.0, places=10)
        self.assertAlmostEqual(prod[1][1], 1.0, places=10)
        self.assertAlmostEqual(prod[0][1], 0.0, places=10)

    def test_a_singular_design_is_refused_not_approximated(self):
        """Two identical columns carry the same information. Returning None
        is how that surfaces as a refusal rather than as an enormous
        meaningless coefficient."""
        self.assertIsNone(kre.invert([[1.0, 2.0], [2.0, 4.0]]))

    def test_the_t_distribution_matches_published_values(self):
        # two-sided p for t at df — standard table values
        self.assertAlmostEqual(kre.t_two_sided_p(2.0, 10), 0.07339, places=4)
        self.assertAlmostEqual(kre.t_two_sided_p(2.228, 10), 0.05, places=3)
        self.assertAlmostEqual(kre.t_two_sided_p(1.96, 100000), 0.05, places=3)
        self.assertAlmostEqual(kre.t_two_sided_p(0.0, 50), 1.0, places=9)

    def test_the_t_distribution_has_fatter_tails_than_the_normal(self):
        """The reason it is used at all instead of a normal approximation."""
        self.assertGreater(kre.t_two_sided_p(2.0, 5), kre.t_two_sided_p(2.0, 500))

    def test_median_and_mad_ignore_one_wild_value(self):
        calm = [1.0, 2.0, 3.0, 4.0, 5.0]
        wild = calm + [900.0]
        self.assertAlmostEqual(kre.median(calm), 3.0)
        self.assertAlmostEqual(kre.median(wild), 3.5)
        self.assertLess(kre.mad(wild), 3.0)

    def test_robust_z_is_none_when_history_has_no_spread(self):
        self.assertIsNone(kre.robust_z(5.0, [2.0] * 40))

    def test_percentile_of_counts_the_share_at_or_below(self):
        self.assertAlmostEqual(kre.percentile_of(5, list(range(1, 11))), 50.0)
        self.assertAlmostEqual(kre.percentile_of(100, list(range(1, 11))), 100.0)
        self.assertAlmostEqual(kre.percentile_of(0, list(range(1, 11))), 0.0)


# ── regression ──────────────────────────────────────────────────────────────

class TestRegression(unittest.TestCase):

    def test_the_slope_matches_a_hand_computed_value(self):
        y = [1, 3, 2, 5, 4, 6, 5, 8, 7, 9] * 8
        X = [[i % 10] for i in range(80)]
        m = kre.ols(y, X, names=["x"])
        self.assertAlmostEqual(m["beta"][1], 0.8, places=9)
        self.assertAlmostEqual(m["beta"][0], 1.4, places=9)
        self.assertAlmostEqual(m["r2"], 0.88, places=9)

    def test_a_perfect_line_has_r_squared_one(self):
        X = [[float(i)] for i in range(100)]
        y = [3.0 + 2.0 * i for i in range(100)]
        m = kre.ols(y, X, names=["x"])
        self.assertAlmostEqual(m["r2"], 1.0, places=9)
        self.assertAlmostEqual(m["beta"][1], 2.0, places=9)

    def test_the_standard_errors_are_the_robust_ones(self):
        m = kre.ols([float(i % 7) for i in range(90)],
                    [[float(i)] for i in range(90)], names=["x"])
        self.assertIn("HC1", m["se_method"])

    def test_robust_errors_react_to_heteroskedasticity(self):
        """Noise that grows with x is exactly the case classical standard
        errors get wrong, and the reason every t here is HC1. The two
        methods must not agree on this data."""
        n = 300
        xs = [float(i) / 30.0 for i in range(n)]
        eps = noise(n, seed=3)
        y = [1.0 + 0.5 * x + e * (1.0 + x) for x, e in zip(xs, eps)]
        m = kre.ols(y, [[x] for x in xs], names=["x"])
        robust_se = next(p["se"] for p in m["params"] if p["name"] == "x")
        # classical se, computed here for the comparison only
        resid = m["resid"]
        s2 = sum(v * v for v in resid) / (m["n"] - 2)
        mx = sum(xs) / len(xs)
        sxx = sum((x - mx) ** 2 for x in xs)
        classical_se = math.sqrt(s2 / sxx)
        self.assertGreater(abs(robust_se - classical_se) / classical_se, 0.05)

    def test_too_few_rows_is_refused(self):
        self.assertIsNone(kre.ols([1.0, 2.0, 3.0], [[1.0], [2.0], [3.0]]))

    def test_rows_with_a_missing_value_are_dropped_not_imputed(self):
        y = [float(i) for i in range(80)]
        X = [[float(i)] for i in range(80)]
        X[5] = [None]
        m = kre.ols(y, X, names=["x"])
        self.assertEqual(m["n"], 79)

    def test_predict_applies_the_fitted_line(self):
        m = kre.ols([2.0 * i for i in range(80)],
                    [[float(i)] for i in range(80)], names=["x"])
        self.assertAlmostEqual(kre.predict(m, [10.0]), 20.0, places=6)

    def test_predict_refuses_a_row_with_a_hole_in_it(self):
        m = kre.ols([2.0 * i for i in range(80)],
                    [[float(i)] for i in range(80)], names=["x"])
        self.assertIsNone(kre.predict(m, [None]))


class TestIncremental(unittest.TestCase):

    def test_a_variable_that_only_echoes_the_baseline_adds_almost_nothing(self):
        """The whole point of the incremental test. `echo` is the baseline
        with a little noise on it — which is what a real echo variable looks
        like. It correlates with the outcome nearly as well as the baseline
        does, and contributes almost nothing once the baseline is there."""
        n = 400
        base = noise(n, seed=11, scale=2.0)
        eps = noise(n, seed=12, scale=0.5)
        jitter = noise(n, seed=13, scale=0.05)
        y = [0.7 * b + e for b, e in zip(base, eps)]
        echo = [b + j for b, j in zip(base, jitter)]
        # the echo on its own looks like a fine predictor
        alone = kre.ols(y, [[e] for e in echo], names=["echo"])
        self.assertGreater(alone["r2"], 0.4)
        # and adds essentially nothing once the baseline is present
        got = kre.incremental(y, [[b] for b in base],
                             [[e, b] for e, b in zip(echo, base)],
                             names=["echo", "base"])
        self.assertTrue(got["ok"])
        self.assertLess(abs(got["delta_r2"]), 0.01)

    def test_an_exact_duplicate_column_is_refused_rather_than_fitted(self):
        """A perfectly collinear design has no unique answer. Refusing is
        how that surfaces, instead of two enormous coefficients that cancel
        and a t-statistic built on nothing."""
        n = 400
        base = noise(n, seed=15, scale=2.0)
        y = [0.7 * b for b in base]
        got = kre.incremental(y, [[b] for b in base],
                             [[b, b] for b in base], names=["copy", "base"])
        self.assertFalse(got["ok"])
        self.assertIsNone(kre.ols(y, [[b, b] for b in base]))

    def test_a_variable_carrying_new_information_adds_a_lot(self):
        n = 400
        base = noise(n, seed=21, scale=2.0)
        fresh = noise(n, seed=22, scale=2.0)
        eps = noise(n, seed=23, scale=0.3)
        y = [0.5 * b + 0.9 * f + e for b, f, e in zip(base, fresh, eps)]
        got = kre.incremental(y, [[b] for b in base],
                             [[f, b] for f, b in zip(fresh, base)],
                             names=["fresh", "base"])
        self.assertGreater(got["delta_r2"], 0.4)
        added = next(p for p in got["added"] if p["name"] == "fresh")
        self.assertGreater(added["t"], 5.0)

    def test_two_models_fitted_on_different_rows_are_refused(self):
        """R² compared across two different samples is meaningless, so it
        is not reported at all."""
        n = 200
        base = noise(n, seed=31)
        extra = noise(n, seed=32)
        extra[0] = None
        y = noise(n, seed=33)
        got = kre.incremental(y, [[b] for b in base],
                             [[e, b] for e, b in zip(extra, base)],
                             names=["extra", "base"])
        self.assertFalse(got["ok"])
        self.assertIn("not comparable", got["reason"])


# ── multiple testing ────────────────────────────────────────────────────────

class TestBenjaminiHochberg(unittest.TestCase):

    def test_the_published_worked_example(self):
        ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.60]
        qs = kre.benjamini_hochberg(ps)
        # q_i = min over ranks >= i of p_(k) * m / k
        self.assertAlmostEqual(qs[0], 0.006, places=6)
        self.assertAlmostEqual(qs[1], 0.024, places=6)
        self.assertAlmostEqual(qs[2], 0.0504, places=6)
        self.assertAlmostEqual(qs[3], 0.0504, places=6)
        self.assertAlmostEqual(qs[4], 0.0504, places=6)
        self.assertAlmostEqual(qs[5], 0.60, places=6)

    def test_q_values_never_decrease_as_p_increases(self):
        ps = [0.0001, 0.002, 0.01, 0.03, 0.2, 0.4, 0.9]
        qs = kre.benjamini_hochberg(ps)
        self.assertEqual(qs, sorted(qs))

    def test_a_q_value_is_never_smaller_than_its_p_value(self):
        ps = [0.01, 0.02, 0.03, 0.04]
        for p, q in zip(ps, kre.benjamini_hochberg(ps)):
            self.assertGreaterEqual(q, p - 1e-12)

    def test_missing_p_values_do_not_count_toward_the_correction(self):
        with_none = kre.benjamini_hochberg([0.01, None, 0.02])
        without = kre.benjamini_hochberg([0.01, 0.02])
        self.assertIsNone(with_none[1])
        self.assertAlmostEqual(with_none[0], without[0], places=12)

    def test_pure_noise_mostly_survives_correction_as_insignificant(self):
        """Forty tests at the five percent level produce a couple of small
        p-values from nothing at all. That is what the correction is for."""
        ps = [(i + 1) / 40.0 for i in range(40)]
        qs = kre.benjamini_hochberg(ps)
        self.assertEqual(sum(1 for q in qs if q < 0.05), 0)


# ── walk-forward: the no-lookahead guarantee ────────────────────────────────

class TestWalkForward(unittest.TestCase):

    def test_nothing_is_scored_that_the_model_was_trained_on(self):
        n = 400
        xs = noise(n, seed=41, scale=2.0)
        rows = rows_from(xs, [0.5 * x for x in xs])
        got = kre.walk_forward(rows, {"korea": ["korea"]}, "gap",
                               min_train=250, step=25)
        self.assertTrue(got["ok"])
        self.assertEqual(got["models"]["korea"]["n"], n - 250)

    def test_a_relationship_that_reverses_catches_a_cheating_model(self):
        """The decisive test. The relationship is +1 for the first half and
        −1 for the second. A model fitted on everything at once sees the two
        cancel and looks harmless; a model that can only see the PAST is
        actively wrong after the reversal, and its out-of-sample direction
        accuracy must fall BELOW a coin flip. Any future change that leaks
        future data into a fold will push this number up and fail here."""
        n = 600
        xs = noise(n, seed=51, scale=2.0)
        ys = [(1.0 if i < n // 2 else -1.0) * x for i, x in enumerate(xs)]
        rows = rows_from(xs, ys)
        got = kre.walk_forward(rows, {"korea": ["korea"]}, "gap",
                               min_train=300, step=25)
        self.assertLess(got["models"]["korea"]["direction_pct"], 50.0)

    def test_a_genuine_relationship_is_found_out_of_sample(self):
        n = 600
        xs = noise(n, seed=61, scale=2.0)
        eps = noise(n, seed=62, scale=0.4)
        rows = rows_from(xs, [0.8 * x + e for x, e in zip(xs, eps)])
        got = kre.walk_forward(rows, {"korea": ["korea"], "zero": []}, "gap",
                               min_train=250, step=25)
        self.assertGreater(got["models"]["korea"]["direction_pct"], 80.0)
        self.assertLess(got["models"]["korea"]["mae_pct"],
                        got["models"]["zero"]["mae_pct"])

    def test_the_zero_baseline_predicts_zero(self):
        rows = rows_from(noise(400, seed=71), noise(400, seed=72))
        got = kre.walk_forward(rows, {"zero": []}, "gap",
                               min_train=250, step=25)
        self.assertAlmostEqual(got["models"]["zero"]["brier"], 0.25, places=9)

    def test_too_little_history_refuses_rather_than_scoring_one_fold(self):
        rows = rows_from(noise(60, seed=81), noise(60, seed=82))
        got = kre.walk_forward(rows, {"korea": ["korea"]}, "gap")
        self.assertFalse(got["ok"])
        self.assertIn("out-of-sample fold", got["reason"])

    def test_beating_the_baseline_needs_both_error_and_direction(self):
        walk = {"models": {
            "kospi": {"n": 100, "mae_pct": 1.0, "direction_pct": 60.0,
                      "brier": 0.24, "median_ae_pct": 0.8},
            "better_mae_only": {"n": 100, "mae_pct": 0.9, "direction_pct": 55.0,
                                "brier": 0.24, "median_ae_pct": 0.8},
            "better_both": {"n": 100, "mae_pct": 0.9, "direction_pct": 62.0,
                            "brier": 0.23, "median_ae_pct": 0.8},
        }}
        got = kre.compare_models(walk, "kospi")
        self.assertIn("better_both", got["beats_baseline"])
        self.assertNotIn("better_mae_only", got["beats_baseline"])

    def test_the_direction_probability_comes_from_real_residuals(self):
        """A normal assumption would put the same probability on a
        prediction whatever the shape of the errors around it. This uses
        the errors that actually occurred."""
        self.assertAlmostEqual(kre._emp_up(0.0, [-2.0, -1.0, 1.0, 2.0]), 0.5)
        self.assertAlmostEqual(kre._emp_up(5.0, [-2.0, -1.0, 1.0, 2.0]), 1.0)
        self.assertAlmostEqual(kre._emp_up(-5.0, [-2.0, -1.0, 1.0, 2.0]), 0.0)


class TestExpandingResidual(unittest.TestCase):

    def test_early_rows_get_nothing_rather_than_a_guess(self):
        rows = rows_from(noise(400, seed=91), noise(400, seed=92))
        kre.expanding_residual(rows, "gap", "korea", min_train=250)
        self.assertTrue(all(r["surprise"] is None for r in rows[:250]))
        self.assertTrue(any(r["surprise"] is not None for r in rows[250:]))

    def test_a_residual_cannot_see_the_session_it_describes(self):
        """The row after the training floor is fitted on exactly the rows
        before it — proven by fitting that same window by hand."""
        xs = noise(400, seed=101, scale=2.0)
        ys = [1.5 * x + 0.2 for x in xs]
        rows = rows_from(xs, ys)
        kre.expanding_residual(rows, "gap", "korea", min_train=250)
        m = kre.ols([r["gap"] for r in rows[:250]],
                    [[r["korea"]] for r in rows[:250]], names=["korea"])
        expect = rows[250]["gap"] - kre.predict(m, [rows[250]["korea"]])
        self.assertAlmostEqual(rows[250]["surprise"], expect, places=8)

    def test_a_later_change_in_the_relationship_cannot_reach_backwards(self):
        """Rewriting the tail must not alter a residual computed before it."""
        xs = noise(400, seed=111, scale=2.0)
        ys = [1.5 * x for x in xs]
        a = rows_from(xs, ys)
        kre.expanding_residual(a, "gap", "korea", min_train=250)
        before = a[260]["surprise"]
        ys2 = list(ys)
        for i in range(300, 400):
            ys2[i] = -99.0 * xs[i]
        b = rows_from(xs, ys2)
        kre.expanding_residual(b, "gap", "korea", min_train=250)
        self.assertAlmostEqual(b[260]["surprise"], before, places=9)


# ── placebo ─────────────────────────────────────────────────────────────────

class TestPlacebo(unittest.TestCase):

    def test_a_real_same_day_relationship_passes(self):
        xs = noise(500, seed=121, scale=2.0)
        eps = noise(500, seed=122, scale=0.6)
        rows = rows_from(xs, [0.8 * x + e for x, e in zip(xs, eps)])
        got = kre.placebo_table(rows, "korea", "gap", shuffles=100)
        self.assertEqual(got["verdict"], "PASSED")
        self.assertGreater(abs(got["correct"]), got["shuffled"]["max_abs"])
        self.assertAlmostEqual(got["shuffled"]["share_beating_correct_pct"], 0.0)

    def test_a_planted_off_by_one_is_caught(self):
        """The failure this exists for: the outcome really depends on the
        PREVIOUS session's input, so the same-day pairing is the wrong one
        and must not come out ahead of the shifted one."""
        n = 500
        xs = noise(n, seed=131, scale=2.0)
        ys = [0.0] + [0.9 * xs[i - 1] for i in range(1, n)]
        rows = rows_from(xs, ys)
        got = kre.placebo_table(rows, "korea", "gap", shuffles=100)
        shifted = max(abs(p["pearson"]) for p in got["placebos"])
        self.assertGreater(shifted, abs(got["correct"]))
        self.assertNotEqual(got["verdict"], "PASSED")

    def test_pure_noise_fails(self):
        rows = rows_from(noise(400, seed=141), noise(400, seed=142))
        got = kre.placebo_table(rows, "korea", "gap", shuffles=100)
        self.assertIn(got["verdict"], ("FAILED — random dates do as well as the "
                                       "real ones",
                                       "WEAK — a shifted alignment does nearly "
                                       "as well"))

    def test_the_shuffle_is_deterministic(self):
        rows = rows_from(noise(300, seed=151), noise(300, seed=152))
        a = kre.placebo_table(rows, "korea", "gap", shuffles=50)
        b = kre.placebo_table(rows, "korea", "gap", shuffles=50)
        self.assertEqual(a["shuffled"], b["shuffled"])


# ── rolling, yearly, regime ─────────────────────────────────────────────────

class TestGroupedViews(unittest.TestCase):

    def test_rolling_reports_one_value_per_session_after_the_window(self):
        rows = rows_from(noise(300, seed=161), noise(300, seed=162))
        got = kre.rolling_correlation(rows, "korea", "gap", 60)
        self.assertEqual(len(got), 300 - 59)
        self.assertTrue(all(x["n"] == 60 for x in got))

    def test_rolling_sees_a_relationship_turn_over(self):
        n = 400
        xs = noise(n, seed=171, scale=2.0)
        ys = [(1.0 if i < 200 else -1.0) * x for i, x in enumerate(xs)]
        rows = rows_from(xs, ys)
        got = kre.rolling_correlation(rows, "korea", "gap", 60)
        self.assertGreater(got[100]["r"], 0.9)
        self.assertLess(got[-1]["r"], -0.9)

    def test_by_year_splits_on_the_calendar(self):
        rows = []
        for y in (2024, 2025):
            for i in range(80):
                rows.append({"date": f"{y}-0{1 + i // 40}-{1 + i % 28:02d}",
                             "korea": 1.0 if i % 2 else -1.0,
                             "gap": (1.0 if i % 2 else -1.0) * (1 if y == 2024 else -1)})
        got = kre.by_year(rows, "korea", "gap")
        self.assertEqual([g["year"] for g in got], ["2024", "2025"])
        self.assertAlmostEqual(got[0]["same_direction_pct"], 100.0)
        self.assertAlmostEqual(got[1]["same_direction_pct"], 0.0)

    def test_the_regime_split_is_at_the_median_not_a_round_number(self):
        n = 400
        rows = rows_from(noise(n, seed=181), noise(n, seed=182),
                         extra={"vol": [float(i) for i in range(n)]})
        got = kre.split_by_regime(rows, "vol", "korea", "gap")
        self.assertTrue(got["ok"])
        self.assertAlmostEqual(got["cut"], kre.median([float(i) for i in range(n)]))
        self.assertEqual(sum(g["n"] for g in got["groups"]), n)

    def test_a_regime_split_needs_enough_for_both_halves(self):
        rows = rows_from(noise(80, seed=191), noise(80, seed=192),
                         extra={"vol": [float(i) for i in range(80)]})
        got = kre.split_by_regime(rows, "vol", "korea", "gap")
        self.assertFalse(got["ok"])
        self.assertIn("both halves", got["reason"])


# ── the two estimates, and the residual ─────────────────────────────────────

class TestEstimates(unittest.TestCase):

    def test_the_bands_are_empirical_not_sigma(self):
        n = 400
        xs = noise(n, seed=201, scale=2.0)
        eps = noise(n, seed=202, scale=1.0)
        rows = rows_from(xs, [1.0 * x + e for x, e in zip(xs, eps)])
        got = kre.regression_estimate(rows, "korea", "opening_gap", 1.0)
        self.assertTrue(got["ok"])
        self.assertIn("empirical", got["band_basis"])
        self.assertLess(got["band50"][0], got["expected_pct"])
        self.assertGreater(got["band50"][1], got["expected_pct"])
        # the 80% band must contain the 50% band
        self.assertLessEqual(got["band80"][0], got["band50"][0])
        self.assertGreaterEqual(got["band80"][1], got["band50"][1])

    def test_estimates_that_disagree_are_never_averaged(self):
        got = kre.compare_estimates(-1.0, 2.0)
        self.assertEqual(got["state"], kre.DISAGREE)
        self.assertNotIn("0.5", str(got.get("averaged", "")))
        self.assertIsNone(got.get("averaged"))

    def test_opposite_signs_always_disagree_however_close(self):
        got = kre.compare_estimates(-0.05, 0.05)
        self.assertEqual(got["state"], kre.DISAGREE)

    def test_close_estimates_agree(self):
        self.assertEqual(kre.compare_estimates(1.0, 1.2)["state"], kre.AGREE)

    def test_one_estimate_alone_is_said_to_be_alone(self):
        self.assertEqual(kre.compare_estimates(1.0, None)["state"],
                         "ONE ESTIMATE ONLY")


class TestResidualContext(unittest.TestCase):

    def _hist(self):
        return noise(400, seed=211, scale=2.0)

    def test_an_ordinary_residual_reads_in_line(self):
        got = kre.residual_context(0.0, self._hist(), expected_pct=1.0,
                                   actual_pct=1.0)
        self.assertEqual(got["label"], "IN LINE")

    def test_an_extreme_residual_is_flagged(self):
        got = kre.residual_context(9.0, self._hist(), expected_pct=1.0,
                                   actual_pct=10.0)
        self.assertIn(got["label"], ("OVERREACTION", "UNDERREACTION"))
        self.assertGreaterEqual(got["percentile"], 90.0)

    def test_opposite_signs_are_divergence_whatever_the_percentile(self):
        got = kre.residual_context(0.1, self._hist(), expected_pct=-2.0,
                                   actual_pct=1.0)
        self.assertEqual(got["label"], "DIVERGENCE")

    def test_too_little_history_refuses_a_label(self):
        got = kre.residual_context(1.0, [0.1, 0.2], expected_pct=1.0,
                                   actual_pct=2.0)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["label"])


class TestConvergence(unittest.TestCase):

    def test_a_planted_convergence_is_found(self):
        n = 400
        resid = noise(n, seed=221, scale=2.0)
        eps = noise(n, seed=222, scale=0.3)
        rows = [{"resid": r, "o2c": -0.8 * r + e} for r, e in zip(resid, eps)]
        got = kre.convergence_test(rows, "resid", "o2c")
        self.assertEqual(got["verdict"], "CONVERGES")
        self.assertGreater(got["slope_t"], 2.0)

    def test_no_relationship_is_reported_as_no_edge(self):
        rows = [{"resid": a, "o2c": b} for a, b in
                zip(noise(400, seed=231), noise(400, seed=232))]
        got = kre.convergence_test(rows, "resid", "o2c")
        self.assertEqual(got["verdict"], "NO MEASURABLE EDGE")

    def test_continued_divergence_is_named(self):
        n = 400
        resid = noise(n, seed=241, scale=2.0)
        eps = noise(n, seed=242, scale=0.3)
        rows = [{"resid": r, "o2c": 0.8 * r + e} for r, e in zip(resid, eps)]
        self.assertEqual(kre.convergence_test(rows, "resid", "o2c")["verdict"],
                         "DIVERGES FURTHER")


class TestRelationshipHealth(unittest.TestCase):

    def test_disagreeing_signs_are_unstable_and_nothing_else(self):
        got = kre.relationship_health(0.35, -0.30, 60, 252)
        self.assertEqual(got["state"], kre.HEALTH_UNSTABLE)
        self.assertFalse(got["same_sign"])

    def test_a_strong_agreeing_relationship_is_strong(self):
        self.assertEqual(kre.relationship_health(0.42, 0.38, 60, 252)["state"],
                         kre.HEALTH_STRONG)

    def test_a_faint_agreeing_relationship_is_weak(self):
        self.assertEqual(kre.relationship_health(0.06, 0.05, 60, 252)["state"],
                         kre.HEALTH_WEAK)

    def test_too_few_sessions_says_so_rather_than_guessing(self):
        self.assertEqual(kre.relationship_health(0.5, 0.5, 5, 20)["state"],
                         kre.HEALTH_INSUFFICIENT)

    def test_the_label_never_hides_the_numbers_that_made_it(self):
        got = kre.relationship_health(0.42, 0.38, 60, 252)
        for key in ("recent_r", "long_r", "recent_n", "long_n", "same_sign"):
            self.assertIn(key, got)


# ── the stateful research layer ─────────────────────────────────────────────

class ResearchCase(unittest.TestCase):
    """A frozen synthetic world: Korea leads the U.S. open by construction,
    and every series is injected."""

    N = 700

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 3, 4, 7, 30, tzinfo=ET)
        days = weekdays(self.N + 1)
        self.days = [d.isoformat() for d in days]
        kmoves = noise(self.N, seed=301, scale=1.5)
        self.k = self._bars(kmoves, 1000.0)
        self.sam = self._bars([m * 1.3 for m in kmoves], 60000.0)
        self.hyn = self._bars([m * 1.5 for m in kmoves], 90000.0)
        self.tw = self._bars(noise(self.N, seed=302, scale=1.2), 17000.0)
        self.tsmc = self._bars(noise(self.N, seed=303, scale=1.4), 600.0)
        self.nk = self._bars(noise(self.N, seed=304, scale=1.1), 38000.0)
        eps = noise(self.N, seed=305, scale=0.5)
        self.gaps = [0.6 * m + e for m, e in zip(kmoves, eps)]
        self.us = self._us(self.gaps)
        self.series = {
            "^KS11": self.k, "005930.KS": self.sam, "000660.KS": self.hyn,
            "^TWII": self.tw, "2330.TW": self.tsmc, "^N225": self.nk,
            "KRW=X": self._bars(noise(self.N, seed=306, scale=0.4), 1300.0),
        }
        kl.configure(daily_fn=lambda s, d: {"bars": list(self.us), "source": "test"},
                     korea_fn=lambda s, d: {"bars": list(self.series.get(s) or []),
                                            "source": "test", "meta": {}},
                     quote_fn=lambda s: {"last": 101.0, "close_prev": 100.0,
                                         "open": None},
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        kr.configure()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(kl.configure)
        self.addCleanup(kr.configure)

    def _bars(self, moves, base):
        px = base
        out = [{"date": self.days[0], "open": px, "high": px, "low": px,
                "close": px}]
        for i, m in enumerate(moves):
            px = px * (1.0 + m / 100.0)
            out.append({"date": self.days[i + 1], "open": px, "high": px,
                        "low": px, "close": px})
        return out

    def _us(self, gaps):
        px = 100.0
        out = [{"date": self.days[0], "open": px, "high": px, "low": px,
                "close": px}]
        for i, g in enumerate(gaps):
            op = px * (1.0 + g / 100.0)
            cl = op * (1.0 + (0.05 if i % 2 else -0.05) / 100.0)
            out.append({"date": self.days[i + 1], "open": op,
                        "high": max(op, cl), "low": min(op, cl), "close": cl})
            px = cl
        return out


class TestFrame(ResearchCase):

    def test_every_asian_input_lands_on_the_frame(self):
        f = kr.frame("SMH")
        self.assertTrue(f["ok"])
        for name in kr.ASIAN_INPUTS:
            self.assertGreater(f["coverage"][name], 100, name)

    def test_the_frame_carries_both_naming_conventions(self):
        row = kr.frame("SMH")["rows"][-1]
        self.assertAlmostEqual(row["gap"], row["opening_gap"], places=12)
        self.assertAlmostEqual(row["o2c"], row["open_to_close"], places=12)

    def test_today_is_not_in_its_own_frame(self):
        f = kr.frame("SMH")
        self.assertLess(f["last_date"], self.now.date().isoformat())

    def test_the_regime_column_is_knowable_before_the_session(self):
        """The volatility labelling a session must come from sessions
        strictly before it. Rewriting the tail of the U.S. series must not
        change the regime value on an earlier row."""
        before = {r["date"]: r["vol20"] for r in kr.frame("SMH")["rows"]}
        pivot = self.days[400]
        for b in self.us:
            if b["date"] > pivot:
                b["close"] *= 3.0
        kl.configure(daily_fn=lambda s, d: {"bars": list(self.us), "source": "t"},
                     korea_fn=lambda s, d: {"bars": list(self.series.get(s) or []),
                                            "source": "t", "meta": {}},
                     quote_fn=lambda s: None, data_dir=self.tmp.name,
                     now_fn=lambda: self.now)
        kr.configure()
        after = {r["date"]: r["vol20"] for r in kr.frame("SMH")["rows"]}
        checked = 0
        for d, v in before.items():
            if d <= pivot and v is not None and after.get(d) is not None:
                self.assertAlmostEqual(after[d], v, places=9)
                checked += 1
        self.assertGreater(checked, 100)

    def test_the_surprise_column_exists_and_starts_late(self):
        rows = kr.frame("SMH")["rows"]
        self.assertTrue(all(r["kospi_surprise"] is None
                            for r in rows[:kre.MIN_TRAIN_N]))
        self.assertTrue(any(r["kospi_surprise"] is not None for r in rows))


class TestPairMatrix(ResearchCase):

    def test_the_matrix_carries_a_q_value_per_cell(self):
        m = kr.pair_matrix("max", targets=["SMH"])
        self.assertTrue(m["cells"])
        for c in m["cells"]:
            self.assertIn("q", c)
            self.assertIn("significant", c)

    def test_the_planted_relationship_is_the_strongest_cell(self):
        m = kr.pair_matrix("max", targets=["SMH"])
        self.assertIn(m["cells"][0]["input"], ("kospi", "samsung", "hynix"))

    def test_the_control_is_labelled_a_control(self):
        m = kr.pair_matrix("max", targets=["SMH"])
        roles = {c["input"]: c["role"] for c in m["cells"]}
        self.assertEqual(roles.get("nikkei"), "control")
        self.assertEqual(roles.get("kospi"), "signal")
        self.assertEqual(roles.get("tsmc"), "research")

    def test_a_direct_measurement_is_preferred_and_labelled(self):
        got = kr.best_input_for("SMH")
        self.assertEqual(got["basis"], "DIRECT")
        self.assertIn(got["best"]["input"], ("kospi", "samsung", "hynix"))


class TestReport(ResearchCase):

    def test_the_report_carries_every_section(self):
        r = kr.report("SMH", "max")
        self.assertTrue(r["ok"])
        for key in ("measures", "incremental", "asia_control", "lead_lag",
                    "placebo", "rolling", "by_year", "regime", "surprise",
                    "convergence", "walk_forward", "model_comparison"):
            self.assertIn(key, r)

    def test_the_measures_are_actually_computed(self):
        r = kr.report("SMH", "max", heavy=False)
        self.assertIsNotNone(r["measures"]["opening_gap"]["pearson"])
        self.assertGreater(r["measures"]["opening_gap"]["pearson"], 0.5)

    def test_the_planted_signal_shows_incremental_information(self):
        r = kr.report("SMH", "max", heavy=False)
        self.assertGreater(r["incremental"]["kospi"]["delta_r2"], 0.3)

    def test_an_unrelated_control_shows_little(self):
        r = kr.report("SMH", "max", heavy=False)
        self.assertLess(r["incremental"]["nikkei"]["delta_r2"], 0.05)

    def test_the_asia_control_calls_a_planted_korea_signal_korea_specific(self):
        r = kr.report("SMH", "max", heavy=False)
        self.assertEqual(r["asia_control"]["verdict"], "KOREA-SPECIFIC")

    def test_the_placebo_passes_on_a_correctly_aligned_world(self):
        r = kr.report("SMH", "max", heavy=False)
        self.assertEqual(r["placebo"]["verdict"], "PASSED")

    def test_the_regime_basis_names_the_prior_close(self):
        r = kr.report("SMH", "max", heavy=False)
        self.assertIn("PRIOR close", r["regime"]["basis"])
        self.assertIn("VIX", r["regime"]["basis"])

    def test_the_report_is_json_serialisable(self):
        import json
        json.dumps(kr.report("SMH", "max", heavy=False))

    def test_a_ticker_with_no_history_is_refused_with_a_reason(self):
        kl.configure(daily_fn=lambda s, d: {"bars": [], "source": "t"},
                     korea_fn=lambda s, d: {"bars": list(self.series.get(s) or []),
                                            "source": "t", "meta": {}},
                     quote_fn=lambda s: None, data_dir=self.tmp.name,
                     now_fn=lambda: self.now)
        kr.configure()
        r = kr.report("NOPE", "max", heavy=False)
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])


class TestValidationIsNotATest(ResearchCase):
    """The live-data routine must be reachable and must say plainly that it
    is not asserted by anything."""

    def test_it_runs_and_labels_itself(self):
        got = kr.validation("max")
        self.assertIn("frozen fixtures", got["note"])
        self.assertTrue(got["pairs"])

    def test_every_pair_reports_its_own_span_and_source(self):
        for row in kr.validation("max")["pairs"]:
            if row.get("ok"):
                for key in ("n", "first_date", "last_date", "source"):
                    self.assertIn(key, row)


class TestMinuteCoverageIsHonest(ResearchCase):

    def test_it_refuses_the_friday_timing_study_and_says_why(self):
        got = kr.minute_coverage(["SMH"])
        self.assertEqual(got["verdict"],
                         "NOT MEASURABLE WITH THE DATA THIS APP HOLDS")
        self.assertEqual(len(got["reasons"]), 3)
        self.assertTrue(got["what_would_enable_it"])

    def test_it_reports_a_count_rather_than_a_conclusion(self):
        got = kr.minute_coverage(["SMH"])
        self.assertIn("fridays_with_minute_paths", got)
        self.assertIsInstance(got["fridays_with_minute_paths"], int)


class TestTheEnhancedPayload(ResearchCase):

    def test_the_payload_carries_the_second_estimate(self):
        p = kl.payload("SMH", "max")
        self.assertTrue(p["ok"])
        self.assertIn("estimates", p)
        self.assertIn("regression", p["estimates"])
        self.assertIn("agreement", p["estimates"])

    def test_the_two_estimates_are_never_averaged_into_one(self):
        p = kl.payload("SMH", "max")
        agree = p["estimates"]["agreement"]
        self.assertIn(agree["state"], (kre.AGREE, kre.DISAGREE,
                                       "ONE ESTIMATE ONLY"))
        self.assertNotIn("averaged", agree)

    def test_the_payload_carries_relationship_strength(self):
        p = kl.payload("SMH", "max")
        rel = p["relationship"]
        self.assertTrue(rel["ok"])
        self.assertEqual(rel["recent"]["n"], 60)
        self.assertEqual(rel["long"]["n"], 252)
        self.assertIn(rel["health"]["state"],
                      (kre.HEALTH_STRONG, kre.HEALTH_STABLE, kre.HEALTH_WEAK,
                       kre.HEALTH_UNSTABLE, kre.HEALTH_INSUFFICIENT))

    def test_the_spark_is_a_trail_not_a_single_number(self):
        p = kl.payload("SMH", "max")
        self.assertGreater(len(p["relationship"]["spark"]), 20)

    def test_every_series_reports_how_unusual_its_move_is(self):
        p = kl.payload("SMH", "max")
        for name in ("kospi", "samsung", "hynix"):
            s = p["korea"]["series"][name]
            self.assertIn("abs_percentile", s)
            self.assertIn("unusual", s)

    def test_the_currency_can_never_raise_the_unusual_flag(self):
        """It is context, and an extreme currency move is still context."""
        p = kl.payload("SMH", "max")
        self.assertFalse(p["korea"]["series"]["usdkrw"]["unusual"])

    def test_the_residual_is_judged_against_this_pairs_own_history(self):
        p = kl.payload("SMH", "max")
        self.assertIn("residual", p)
        self.assertIn("percentile", p["residual"])

    def test_the_payload_is_still_json_serialisable(self):
        import json
        json.dumps(kl.payload("SMH", "max"))


if __name__ == "__main__":
    unittest.main()
