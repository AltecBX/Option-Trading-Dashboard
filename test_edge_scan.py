"""Tests for edge_scan.py — funnel, per-symbol analysis, breach history,
EM calibration, the O(n) ERV series, the VRP threshold backtest, and Kelly
gating. Live data is replaced by a FakeSchwab serving seeded synthetic
bars + chains, so every path is deterministic and offline."""

import math
import tempfile
import unittest
from datetime import date, timedelta

import edge_scan as es
import premium_edge as pe
import vol_forecast as vf
from test_premium_edge import mk_chain, NOW
from test_vol_forecast import gbm_bars


def dated(bars, end=NOW):
    """Give synthetic bars real ISO dates ending just before `end`."""
    d = end - timedelta(days=1)
    for b in reversed(bars):
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        b["date"] = d.isoformat() + "T12:00:00-04:00"
        d -= timedelta(days=1)
    return bars


class FakeSchwab:
    def __init__(self, sigma=0.30, chain_sigma=0.45, seed=7):
        self.sigma, self.chain_sigma, self.seed = sigma, chain_sigma, seed
        self.chain_calls = 0

    def get_price_history(self, sym, days=260):
        return dated(gbm_bars(min(days, 600), sigma=self.sigma, seed=self.seed))

    def get_option_chain(self, sym, expiration=None, strike_count=60, to_date=None):
        self.chain_calls += 1
        return mk_chain(spot=100.0,
                        expiries={14: self.chain_sigma, 30: self.chain_sigma,
                                  45: self.chain_sigma - 0.02})

    def rate_usage(self):
        return 0


def wire(tmpdir, sc=None, board_rows=None, earn_next=None):
    sc = sc or FakeSchwab()
    calls = {"iv_appends": []}
    es.configure(
        schwab_getter=lambda: sc,
        board_getter=lambda: {"rows": board_rows or []},
        earnings_fn=lambda s: {"next": earn_next},
        earn_moves_fn=lambda s: {"avg_abs": 6.0, "n": 5},
        macro_fn=lambda: [{"kind": "CPI", "date": (NOW + timedelta(days=5)).isoformat()}],
        vix_fn=lambda: {(NOW - timedelta(days=i)).isoformat(): 18.0 + (i % 7)
                        for i in range(300)},
        iv_append_fn=lambda s, iv: calls["iv_appends"].append((s, iv)),
        market_open_fn=lambda now=None: False,
        data_dir=tmpdir,
    )
    pe.config(refresh=True)
    return sc, calls


class TestAnalyzeSymbol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.sc, self.calls = wire(self.tmp.name)

    def tearDown(self):
        es.configure(lambda: None, lambda: None)
        pe.configure(None)
        self.tmp.cleanup()

    def test_full_analysis_row(self):
        # chain IV 0.45 vs realized sigma 0.30 -> rich premium, sane row
        d = es.analyze_symbol("FAKE", intent="premium_only", record=False, now=NOW)
        self.assertTrue(d.get("row"), d.get("error"))
        r = d["row"]
        self.assertGreater(r["iv30"], 0.40)
        self.assertAlmostEqual(r["erv30"], 0.30, delta=0.08)
        self.assertGreater(r["vrp_ratio"], 1.15)
        self.assertIn(r["signal"], ("STRONG SELL VOL", "SELL VOL", "WATCH", "FAIR"))
        self.assertTrue(d["structures"]["structures"])
        self.assertTrue(all(s["kind"] in ("put_credit_spread", "call_credit_spread",
                                          "iron_condor")
                            for s in d["structures"]["structures"]))
        self.assertTrue(d["score_breakdown"])
        self.assertIn("config_hash", d["engine"])

    def test_record_writes_observation_and_iv_history(self):
        es.analyze_symbol("FAKE", record=True, now=NOW)
        obs = pe.load_observations("FAKE")
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["date"], NOW.isoformat())
        self.assertIn("vrp_points", obs[0])
        self.assertEqual(len(self.calls["iv_appends"]), 1)
        self.assertEqual(self.calls["iv_appends"][0][0], "FAKE")

    def test_forecast_choice_cached(self):
        es.analyze_symbol("FAKE", record=False, now=NOW)
        cache = es._load_fcast("FAKE")
        self.assertIn("choice", cache)
        self.assertIn("model", cache["choice"])

    def test_earnings_inside_classifies(self):
        _sc, _ = wire(self.tmp.name, earn_next=(NOW + timedelta(days=10)).isoformat())
        d = es.analyze_symbol("FAKE", record=False, now=NOW)
        self.assertTrue(d["row"]["earnings_inside"])
        self.assertIn(d["row"]["premium_class"], ("EVENT", "MIXED", "PURE"))
        self.assertIsNotNone(d["erv"]["event_adj"])

    def test_insufficient_history_honest(self):
        sc = FakeSchwab()
        sc.get_price_history = lambda sym, days=260: dated(gbm_bars(60, seed=1))
        wire(self.tmp.name, sc=sc)
        d = es.analyze_symbol("FAKE", record=False, now=NOW)
        self.assertFalse(d.get("data_ok", True))
        self.assertIn("history", d["error"])


class TestStage1(unittest.TestCase):
    def test_cold_board_falls_back_to_watchlist(self):
        # a fresh deploy on a weekend has an EMPTY watchlist board (it only
        # rebuilds on weekday schedules) — the funnel must fall back to the
        # user's own watchlist, starred first, instead of scanning nothing
        # (user-reported, 8-16-2026: "Scan now" produced an empty board)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, board_rows=[])
            es._WATCHLIST_FN = lambda: {"starred": ["MU", "SNDK"],
                                        "all": ["AAPL", "MU", "GOOGL", "SNDK"]}
            cands = es._stage1_candidates(es._cfg())
            self.assertEqual(cands[:2], ["MU", "SNDK"])
            self.assertIn("AAPL", cands)
            es._WATCHLIST_FN = None

    def test_no_board_no_watchlist_is_empty(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, board_rows=[])
            self.assertEqual(es._stage1_candidates(es._cfg()), [])

    def test_gates_and_ranking(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            rows = [
                {"ticker": "GOOD", "last": 120.0, "market_cap": 5e10,
                 "avg_volume": 5e6, "rvol_rank": 80, "days_to_earnings": 5, "change": 2.0},
                {"ticker": "CHEAP", "last": 8.0, "market_cap": 5e10,
                 "avg_volume": 5e6, "rvol_rank": 95},
                {"ticker": "TINY", "last": 50.0, "market_cap": 1e9,
                 "avg_volume": 5e6, "rvol_rank": 95},
                {"ticker": "QUIET", "last": 90.0, "market_cap": 8e10,
                 "avg_volume": 3e6, "rvol_rank": 10, "change": 0.1},
            ]
            wire(tmp, board_rows=rows)
            cands = es._stage1_candidates(es._cfg())
            self.assertIn("GOOD", cands)
            self.assertNotIn("CHEAP", cands)
            self.assertNotIn("TINY", cands)
            self.assertEqual(cands[0], "GOOD")


class TestTheFunnelServesBothQuestions(unittest.TestCase):
    """One budget of chain fetches feeds two tabs that want opposite things.

    Premium Edge wants the richest premium, which lives around an earnings
    report, so the funnel adds a bonus for one falling inside the week. The
    sell board excludes any name with a report inside the option's life.
    Both are right for their own tab — but the board reads whatever THIS
    funnel picked, so the bonus was buying chains for names the board was
    guaranteed to refuse, then reporting "nothing qualifies today".
    """

    def _rows(self):
        # Two names an earnings-hungry funnel loves and a seller cannot use,
        # and three quiet ones a seller can, ranked below them on the proxy.
        return [
            {"ticker": "ERNA", "last": 100.0, "market_cap": 5e10,
             "avg_volume": 5e6, "rvol_rank": 70, "days_to_earnings": 3,
             "change": 3.0},
            {"ticker": "ERNB", "last": 100.0, "market_cap": 5e10,
             "avg_volume": 5e6, "rvol_rank": 65, "days_to_earnings": 14,
             "change": 2.0},
            {"ticker": "CALMA", "last": 100.0, "market_cap": 5e10,
             "avg_volume": 5e6, "rvol_rank": 40, "days_to_earnings": 80,
             "change": 0.2},
            {"ticker": "CALMB", "last": 100.0, "market_cap": 5e10,
             "avg_volume": 5e6, "rvol_rank": 30, "days_to_earnings": 90,
             "change": 0.1},
            {"ticker": "CALMC", "last": 100.0, "market_cap": 5e10,
             "avg_volume": 5e6, "rvol_rank": 20, "days_to_earnings": 120,
             "change": 0.1},
        ]

    def test_sellable_names_reach_the_scan_even_when_the_proxy_buries_them(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, board_rows=self._rows())
            cfg = es._cfg()
            cfg["scan"]["stage2_n"] = 2          # only the two earnings names
            cands = es._stage1_candidates(cfg, today="2026-08-26")
            self.assertEqual(cands[:2], ["ERNA", "ERNB"],
                             "Premium Edge must keep its event slate")
            for t in ("CALMA", "CALMB", "CALMC"):
                self.assertIn(t, cands, f"{t} is sellable and was never fetched")

    def test_the_event_slate_is_not_taken_from_premium_edge(self):
        """The seller slate is additional. Halving Premium Edge to pay for
        it would fix one tab by breaking the other."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, board_rows=self._rows())
            cfg = es._cfg()
            cfg["scan"]["stage2_n"] = 2
            cfg["scan"]["stage2_seller_n"] = 0
            base = es._stage1_candidates(cfg, today="2026-08-26")
            cfg["scan"]["stage2_seller_n"] = 3
            with_seller = es._stage1_candidates(cfg, today="2026-08-26")
            self.assertEqual(with_seller[:len(base)], base)
            self.assertGreater(len(with_seller), len(base))

    def test_a_name_with_earnings_inside_never_enters_the_seller_slate(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            rows = [{"ticker": "SOON", "last": 100.0, "market_cap": 5e10,
                     "avg_volume": 5e6, "rvol_rank": 90,
                     "days_to_earnings": 20, "change": 0.0}]
            wire(tmp, board_rows=rows)
            cfg = es._cfg()
            cfg["scan"]["stage2_n"] = 0
            cfg["scan"]["stage2_seller_n"] = 5
            self.assertEqual(es._stage1_candidates(cfg, today="2026-08-26"), [])

    def test_an_unknown_earnings_date_is_not_treated_as_no_earnings(self):
        """Missing is not absent. A name whose report date we do not know
        must not be handed to the board as safe to sell."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            rows = [{"ticker": "UNKN", "last": 100.0, "market_cap": 5e10,
                     "avg_volume": 5e6, "rvol_rank": 90, "change": 0.0}]
            wire(tmp, board_rows=rows)
            cfg = es._cfg()
            cfg["scan"]["stage2_n"] = 0
            cfg["scan"]["stage2_seller_n"] = 5
            self.assertEqual(es._stage1_candidates(cfg, today="2026-08-26"), [])

    def test_a_measured_richness_outranks_the_proxy(self):
        """The scan has been recording real iv30/erv30/vrp_ratio per ticker
        all along. For a name it has already measured there is no reason to
        rank by a proxy for richness when richness itself is on file."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, board_rows=self._rows())
            # CALMC sits LAST on the proxy but was measured paying 1.40x.
            pe.record_observation("CALMC", {"date": "2026-08-25",
                                            "vrp_ratio": 1.40})
            cfg = es._cfg()
            cfg["scan"]["stage2_n"] = 0
            cfg["scan"]["stage2_seller_n"] = 1
            self.assertEqual(es._stage1_candidates(cfg, today="2026-08-26"),
                             ["CALMC"])

    def test_a_stale_reading_is_not_evidence_about_today(self):
        """Premium moves. A fortnight-old number presented as current is
        worse than admitting we do not know."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, board_rows=self._rows())
            pe.record_observation("CALMC", {"date": "2026-06-01",
                                            "vrp_ratio": 1.90})
            self.assertIsNone(es._measured_richness("CALMC", "2026-08-26"))
            cfg = es._cfg()
            cfg["scan"]["stage2_n"] = 0
            cfg["scan"]["stage2_seller_n"] = 1
            # falls back to the proxy order, which puts CALMA first
            self.assertEqual(es._stage1_candidates(cfg, today="2026-08-26"),
                             ["CALMA"])

    def test_a_future_dated_reading_is_refused(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, board_rows=self._rows())
            pe.record_observation("CALMC", {"date": "2027-01-01",
                                            "vrp_ratio": 9.0})
            self.assertIsNone(es._measured_richness("CALMC", "2026-08-26"))

    def test_the_store_is_not_read_once_per_watchlist_name(self):
        """600 names on the board must not become 600 file opens per scan."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            rows = [{"ticker": f"S{i:03d}", "last": 100.0, "market_cap": 5e10,
                     "avg_volume": 5e6, "rvol_rank": 50 + (i % 40),
                     "days_to_earnings": 90, "change": 0.0}
                    for i in range(600)]
            wire(tmp, board_rows=rows)
            seen = []
            real = es._measured_richness
            es._measured_richness = lambda s, t: (seen.append(s), real(s, t))[1]
            try:
                cfg = es._cfg()
                cfg["scan"]["stage2_n"] = 24
                cfg["scan"]["stage2_seller_n"] = 10
                es._stage1_candidates(cfg, today="2026-08-26")
            finally:
                es._measured_richness = real
            self.assertLessEqual(len(seen), 10 * es.OBS_LOOKUP_MULTIPLE)
            self.assertGreater(len(seen), 0)

    def test_the_seller_horizon_matches_the_board_that_consumes_it(self):
        """If these drift apart the funnel silently starts buying chains the
        board refuses again — the whole bug this class exists for."""
        import setup_board as sb
        self.assertEqual(es.SELLER_HORIZON_DAYS,
                         45 + sb.EARNINGS_BUFFER_DAYS)


class TestBreachAndEM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = dated(gbm_bars(500, sigma=0.30, seed=21))
        pe.configure(None)
        cls.cfg, _ = pe.config(refresh=True)

    def test_breach_frequencies_near_model_on_lognormal_data(self):
        out = es.breach_stats(self.bars, self.cfg)
        self.assertIsNotNone(out)
        rows = [r for r in out["rows"] if r["horizon_td"] == 21 and r["k_sigma"] == 1.0]
        self.assertTrue(rows)
        r = rows[0]
        # synthetic data IS lognormal -> empirical should sit near the model
        self.assertAlmostEqual(r["put_itm_emp"], r["itm_model"], delta=0.10)
        self.assertAlmostEqual(r["put_touch_emp"], r["touch_model"], delta=0.12)
        self.assertGreaterEqual(r["put_touch_emp"], r["put_itm_emp"])
        self.assertIn("MEASURED", r["basis"])

    def test_breach_monotone_in_k(self):
        out = es.breach_stats(self.bars, self.cfg)
        rows = sorted((r for r in out["rows"] if r["horizon_td"] == 21),
                      key=lambda r: r["k_sigma"])
        touches = [r["put_touch_emp"] for r in rows]
        self.assertEqual(touches, sorted(touches, reverse=True))

    def test_em_calibration_near_theory_on_lognormal(self):
        out = es.em_calibration(self.bars, self.cfg)
        self.assertIsNotNone(out)
        self.assertAlmostEqual(out["inside_1x_pct"], 68.3, delta=10.0)
        self.assertGreater(out["inside_15x_pct"], out["inside_125x_pct"])
        self.assertIn("MODELED", out["em_basis"])

    def test_short_history_returns_none(self):
        self.assertIsNone(es.em_calibration(self.bars[:100], self.cfg))


class TestErvSeries(unittest.TestCase):
    def test_matches_pointwise_forecaster(self):
        bars = dated(gbm_bars(400, sigma=0.30, seed=3))
        pe.configure(None)
        cfg, _ = pe.config(refresh=True)
        series = es._erv_series(bars, cfg)
        self.assertIsNone(series[30])
        full = vf.expected_rv30(bars, cfg.get("forecast", {}))
        self.assertIsNotNone(series[-1])
        self.assertAlmostEqual(series[-1], full["erv30"], delta=0.03)

    def test_no_lookahead_in_series(self):
        bars = dated(gbm_bars(400, sigma=0.25, seed=4))
        pe.configure(None)
        cfg, _ = pe.config(refresh=True)
        a = es._erv_series(bars, cfg)
        mutated = [dict(b) for b in bars]
        for b in mutated[300:]:
            b["close"] *= 5
        b2 = es._erv_series(mutated, cfg)
        for i in range(60, 299):
            if a[i] is not None:
                self.assertAlmostEqual(a[i], b2[i], places=9)


class TestBacktestAndSizing(unittest.TestCase):
    def test_vrp_backtest_runs_offline(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            wire(tmp, sc=FakeSchwab(sigma=0.30, seed=17))
            out = es.run_vrp_backtest(["FAKE"], thresholds=[1.0, 1.2])
            self.assertNotIn("error", out)
            self.assertEqual(len(out["grid"]), 2)
            g0 = out["grid"][0]
            self.assertIn("n_trades", g0)
            self.assertIn("modeled", out["iv_basis"]["FAKE"])
            if g0.get("n_trades", 0) >= 20:
                self.assertIsNotNone(g0.get("worst_5pct"))
            self.assertIn("win rate", out["note"])
            # every displayed metric ships pre-rounded — the UI shows these raw
            # (user-reported, 8-16-2026: Max DD rendered as 21.51964976123858%)
            for g in out["grid"]:
                dd = g.get("max_drawdown_pct")
                if dd is not None:
                    self.assertEqual(dd, round(dd, 1))

    def test_kelly_gates_and_math(self):
        pe.configure(None)
        cfg, _ = pe.config(refresh=True)
        few = es.kelly_guidance([100.0] * 10, 1000.0, cfg)
        self.assertEqual(few["status"], "insufficient_outcomes")
        import random
        rnd = random.Random(5)
        pnls = [rnd.gauss(30.0, 120.0) for _ in range(200)]
        out = es.kelly_guidance(pnls, 1000.0, cfg)
        if out["status"] == "ok":
            rets = [p / 1000.0 for p in pnls]
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            self.assertAlmostEqual(out["full_kelly_frac"], mean / var, delta=0.01)
            self.assertLessEqual(out["suggested_frac"], 0.10 + 1e-9)
        lose = es.kelly_guidance([-10.0] * 50, 1000.0, cfg)
        self.assertEqual(lose["status"], "no_edge")


if __name__ == "__main__":
    unittest.main()
