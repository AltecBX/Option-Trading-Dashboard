"""test_math_contract.py (v3.64) — enforces the options-math contract in
metrics.py across every Python module that prices, ranks or estimates:

  1. metrics.py reproduces fixtures/options_math.json bit-for-bit (the same
     JSON the JS engine is matched against in test_strategy_fixtures.js).
  2. Cross-module identity: option_reprice and backtest price/Greek calls
     return EXACTLY what metrics returns for identical inputs.
  3. Model sanity: put-call parity (with and without dividends), vega and
     theta conventions verified by finite differences.
  4. normalize_iv / rank_and_percentile / one_sigma_move / year_fraction
     behavior, including the documented edge cases.
  5. risk_free_rate: provider plumbing, bounds guard, labeled fallback.
  6. The shared rank: storage._iv_history_compute_rank is the metrics rank.
  7. juice.build_strategies: iron-fly POP uses the true 1σ (the v3.63 bug
     fed the ATM straddle into a formula expecting 1σ), and every POP is
     tagged with its basis ("delta" or "one_sigma").

Run:  python3 -m unittest test_math_contract
No network, no paid keys, fully deterministic.
"""
import json
import math
import os
import tempfile
import unittest

os.environ.setdefault("JERRY_DATA_DIR", tempfile.mkdtemp(prefix="jerry_math_"))

import metrics
from metrics import (
    _bs_delta, _bs_gamma, _bs_price, _bs_theta, _bs_vega, _norm_cdf,
    normalize_iv, one_sigma_move, rank_and_percentile, risk_free_rate,
    set_rate_provider, year_fraction, DAYS_PER_YEAR, RISK_FREE_FALLBACK,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "options_math.json")


def _cases():
    with open(FIXTURES) as f:
        return json.load(f)["cases"]


class TestFixtures(unittest.TestCase):
    """metrics.py must reproduce the committed fixture file exactly.
    If this fails, either metrics changed without regenerating fixtures
    (run fixtures/generate_math_fixtures.py and commit) or drifted."""

    def test_fixture_file_exists_and_is_populated(self):
        cases = _cases()
        self.assertGreater(len(cases), 300)

    def test_metrics_reproduces_fixtures(self):
        for c in _cases():
            T = year_fraction(c["days"])
            a = (c["spot"], c["strike"], T, c["sigma"])
            kw = {"r": c["r"], "q": c["q"]}
            for name, got in (
                ("price", _bs_price(*a, c["side"], **kw)),
                ("delta", _bs_delta(*a, c["side"], **kw)),
                ("gamma", _bs_gamma(*a, **kw)),
                ("theta", _bs_theta(*a, c["side"], **kw)),
                ("vega", _bs_vega(*a, **kw)),
            ):
                self.assertAlmostEqual(got, c[name], places=12,
                                       msg=f"{name} drifted for {c}")


class TestCrossModuleIdentity(unittest.TestCase):
    """Identical inputs → identical pricing and Greeks in every module."""

    GRID = [(100.0, 100.0, 30, 0.32), (480.0, 500.0, 7, 0.22),
            (25.0, 27.5, 90, 0.55), (1025.5, 980.0, 3, 0.40)]

    def test_option_reprice_matches_metrics(self):
        import option_reprice as rp
        for spot, strike, days, sigma in self.GRID:
            T = year_fraction(days)
            for side in ("call", "put"):
                self.assertEqual(rp.bs_price(spot, strike, T, 0.045, sigma, side),
                                 _bs_price(spot, strike, T, sigma, side, r=0.045))
                d, g, t, v = rp.greeks(spot, strike, T, 0.045, sigma, side)
                self.assertEqual(d, _bs_delta(spot, strike, T, sigma, side, r=0.045))
                self.assertEqual(g, _bs_gamma(spot, strike, T, sigma, r=0.045))
                self.assertEqual(t, _bs_theta(spot, strike, T, sigma, side, r=0.045))
                self.assertEqual(v, _bs_vega(spot, strike, T, sigma, r=0.045))

    def test_backtest_matches_metrics(self):
        import backtest as bt
        for spot, strike, days, sigma in self.GRID:
            T = year_fraction(days)
            for side in ("call", "put"):
                # Explicit rate → exact identity with the canonical engine.
                self.assertEqual(bt._bs_price(spot, strike, T, sigma, side, r=0.045),
                                 _bs_price(spot, strike, T, sigma, side, r=0.045))
                self.assertEqual(bt._bs_delta(spot, strike, T, sigma, side, r=0.045),
                                 _bs_delta(spot, strike, T, sigma, side, r=0.045))
                # Default rate → the app risk-free rate (live or labeled
                # fallback), not a hardcoded constant.
                app_r = risk_free_rate()[0]
                self.assertEqual(bt._bs_price(spot, strike, T, sigma, side),
                                 _bs_price(spot, strike, T, sigma, side, r=app_r))

    def test_reprice_implied_vol_roundtrip(self):
        import option_reprice as rp
        px = _bs_price(100.0, 105.0, year_fraction(30), 0.35, "call", r=0.045)
        iv = rp.implied_vol(px, 100.0, 105.0, year_fraction(30), 0.045, "call")
        self.assertAlmostEqual(iv, 0.35, places=4)


class TestModelSanity(unittest.TestCase):
    def test_put_call_parity(self):
        for r, q in ((0.045, 0.0), (0.04, 0.02)):
            for spot, strike, days, sigma in ((100, 100, 30, 0.3), (480, 500, 90, 0.2)):
                T = year_fraction(days)
                c = _bs_price(spot, strike, T, sigma, "call", r=r, q=q)
                p = _bs_price(spot, strike, T, sigma, "put", r=r, q=q)
                parity = spot * math.exp(-q * T) - strike * math.exp(-r * T)
                self.assertAlmostEqual(c - p, parity, places=9)

    def test_theta_is_dollars_per_calendar_day(self):
        # theta(T) ≈ (price(T - 1day) - price(T)) for small steps.
        spot, strike, days, sigma = 100.0, 100.0, 45, 0.30
        p_now = _bs_price(spot, strike, year_fraction(days), sigma, "call")
        p_next = _bs_price(spot, strike, year_fraction(days - 1), sigma, "call")
        th = _bs_theta(spot, strike, year_fraction(days), sigma, "call")
        self.assertLess(th, 0)
        self.assertAlmostEqual(p_next - p_now, th, delta=abs(th) * 0.05)

    def test_vega_is_dollars_per_vol_point(self):
        spot, strike, days, sigma = 100.0, 100.0, 45, 0.30
        p_lo = _bs_price(spot, strike, year_fraction(days), sigma, "call")
        p_hi = _bs_price(spot, strike, year_fraction(days), sigma + 0.01, "call")
        v = _bs_vega(spot, strike, year_fraction(days), sigma)
        self.assertAlmostEqual(p_hi - p_lo, v, delta=v * 0.02)

    def test_degenerate_inputs_collapse_to_intrinsic(self):
        self.assertEqual(_bs_price(100, 90, 0.0, 0.3, "call"), 10.0)
        self.assertEqual(_bs_price(100, 110, 0.0, 0.3, "put"), 10.0)
        self.assertEqual(_bs_price(100, 100, year_fraction(30), 0.0, "call"), 0.0)
        self.assertEqual(_bs_delta(0, 100, 0.1, 0.3, "call"), 0.5)
        self.assertEqual(_bs_delta(0, 100, 0.1, 0.3, "put"), -0.5)

    def test_norm_cdf_symmetry(self):
        self.assertAlmostEqual(_norm_cdf(0.0), 0.5, places=15)
        for x in (0.5, 1.0, 2.33):
            self.assertAlmostEqual(_norm_cdf(x) + _norm_cdf(-x), 1.0, places=12)


class TestHelpers(unittest.TestCase):
    def test_year_fraction(self):
        self.assertEqual(year_fraction(365), 1.0)
        self.assertEqual(year_fraction(0), 0.0)
        self.assertEqual(year_fraction(-3), 0.0)
        self.assertAlmostEqual(year_fraction(7), 7 / DAYS_PER_YEAR)

    def test_normalize_iv(self):
        self.assertEqual(normalize_iv(0.32), 0.32)          # decimal form
        self.assertEqual(normalize_iv(32.0), 0.32)          # percent form
        self.assertEqual(normalize_iv(320.0), 3.2)          # 320% as percent
        self.assertIsNone(normalize_iv(1500.0))             # >1000% garbage
        self.assertIsNone(normalize_iv(0))
        self.assertIsNone(normalize_iv(-0.2))
        self.assertIsNone(normalize_iv(None))
        self.assertIsNone(normalize_iv("abc"))
        self.assertIsNone(normalize_iv(float("nan")))
        self.assertEqual(normalize_iv(2.9), 2.9)            # documented tradeoff

    def test_one_sigma_move(self):
        self.assertAlmostEqual(one_sigma_move(100.0, 0.32, 365), 32.0, places=10)
        got = one_sigma_move(100.0, 32.0, 365)              # percent-form IV
        self.assertAlmostEqual(got, 32.0, places=10)
        self.assertIsNone(one_sigma_move(0, 0.32, 30))
        self.assertIsNone(one_sigma_move(100.0, None, 30))
        self.assertIsNone(one_sigma_move(100.0, 0.32, 0))

    def test_rank_and_percentile(self):
        hist = [float(x) for x in range(1, 22)]             # 1..21, n=21
        rk = rank_and_percentile(hist, 11.0)
        self.assertEqual(rk["rank"], 50.0)
        self.assertEqual(rk["n"], 21)
        self.assertAlmostEqual(rk["percentile"], 10 / 21 * 100, places=1)
        self.assertIsNone(rank_and_percentile([1.0] * 19, 1.0))   # n < 20
        flat = rank_and_percentile([5.0] * 25, 5.0)
        self.assertEqual(flat["rank"], 50.0)                # flat history
        top = rank_and_percentile(hist, 21.0)
        self.assertEqual(top["rank"], 100.0)
        clamped = rank_and_percentile(hist, 99.0)           # above the range
        self.assertEqual(clamped["rank"], 100.0)


class TestRiskFreeRate(unittest.TestCase):
    def tearDown(self):
        set_rate_provider(None)

    def test_fallback_is_labeled(self):
        set_rate_provider(None)
        rate, source = risk_free_rate()
        self.assertEqual(rate, RISK_FREE_FALLBACK)
        self.assertIn("fallback", source)

    def test_provider_used_when_healthy(self):
        set_rate_provider(lambda: (0.0525, "3M T-bill 5.25% (test)"))
        rate, source = risk_free_rate()
        self.assertEqual(rate, 0.0525)
        self.assertIn("T-bill", source)

    def test_provider_garbage_falls_back(self):
        set_rate_provider(lambda: (7.5, "percent-form bug"))   # out of bounds
        rate, source = risk_free_rate()
        self.assertEqual(rate, RISK_FREE_FALLBACK)
        self.assertIn("fallback", source)
        set_rate_provider(lambda: None)
        self.assertEqual(risk_free_rate()[0], RISK_FREE_FALLBACK)

        def boom():
            raise RuntimeError("network down")
        set_rate_provider(boom)
        self.assertEqual(risk_free_rate()[0], RISK_FREE_FALLBACK)


class TestSharedRank(unittest.TestCase):
    def test_storage_iv_rank_is_the_metrics_rank(self):
        import storage
        hist = [{"date": f"2026-01-{i:02d}", "iv": 0.20 + i * 0.01}
                for i in range(1, 26)]
        cur = 0.37
        got = storage._iv_history_compute_rank(hist, cur)
        want = rank_and_percentile([r["iv"] for r in hist], cur)
        self.assertEqual(got["iv_rank"], want["rank"])
        self.assertEqual(got["iv_pct"], want["percentile"])
        self.assertEqual(got["iv_rank_days"], 25)
        # Below 20 entries → nulls, count still reported.
        thin = storage._iv_history_compute_rank(hist[:10], cur)
        self.assertIsNone(thin["iv_rank"])
        self.assertEqual(thin["iv_rank_days"], 10)


def _row(strike, bid, ask, delta=None):
    return {"strike": strike, "bid": bid, "ask": ask, "delta": delta}


class TestJuicePOP(unittest.TestCase):
    """Iron-fly POP must use the true 1σ move; every POP carries its basis."""

    def _chain(self):
        # spot 100, ATM straddle 4.00, symmetric wings (85/115 give the
        # condor its protective wings ≥$2.50 beyond the 90/110 shorts).
        calls = [_row(100.0, 1.90, 2.10, 0.50), _row(104.0, 0.45, 0.55, 0.30),
                 _row(110.0, 0.15, 0.25, 0.18), _row(115.0, 0.03, 0.07, 0.08)]
        puts = [_row(100.0, 1.90, 2.10, -0.50), _row(96.0, 0.45, 0.55, -0.30),
                _row(90.0, 0.15, 0.25, -0.18), _row(85.0, 0.03, 0.07, -0.08)]
        return calls, puts

    def _flies(self, one_sigma):
        import juice
        calls, puts = self._chain()
        strats = juice.build_strategies(100.0, 4.0, calls, puts, False,
                                        calls[0], puts[0], one_sigma=one_sigma)
        return {s["kind"]: s for s in strats}

    def test_iron_fly_pop_uses_one_sigma_not_straddle(self):
        by = self._flies(one_sigma=3.2)
        fly = by["iron_fly"]
        # credit = 4.00 straddle − 0.50 put wing − 0.50 call wing = 3.00
        self.assertEqual(fly["credit"], 3.0)
        want = round((2 * _norm_cdf(3.0 / 3.2) - 1) * 100, 0)
        self.assertEqual(fly["pop"], want)
        self.assertEqual(fly["pop_basis"], "one_sigma")
        # The old bug divided by the straddle — assert we did NOT do that.
        buggy = round((2 * _norm_cdf(3.0 / 4.0) - 1) * 100, 0)
        self.assertNotEqual(fly["pop"], buggy)

    def test_iron_fly_pop_falls_back_to_straddle_over_1_25(self):
        by = self._flies(one_sigma=None)      # no IV → σ estimated as em/1.25
        want = round((2 * _norm_cdf(3.0 / (4.0 / 1.25)) - 1) * 100, 0)
        self.assertEqual(by["iron_fly"]["pop"], want)
        self.assertEqual(by["iron_fly"]["pop_basis"], "one_sigma")

    def test_delta_based_pops_are_tagged(self):
        by = self._flies(one_sigma=3.2)
        st = by["short_strangle"]
        self.assertEqual(st["pop_basis"], "delta")
        self.assertEqual(st["pop"], round((1 - (0.18 + 0.18)) * 100, 0))
        for kind in ("iron_condor", "put_credit_spread", "call_credit_spread",
                     "csp", "covered_call"):
            self.assertEqual(by[kind]["pop_basis"], "delta", kind)


if __name__ == "__main__":
    unittest.main()
