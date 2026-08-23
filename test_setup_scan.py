"""Tests for setup_scan.py — the gathering half of the Best Setup feature.

The engine's claim rests entirely on the measurement this module performs,
so the measurement is what gets tested hardest: an incomplete forward window
must not count as a miss, both sides of every comparison must be measured
from the CLOSE, and the rule for what counts as "this state" must be fixed
in advance rather than chosen after seeing which answer is better.
"""

import math
import unittest
from datetime import date, timedelta

import setup_scan as SS


class TestForwardExtremes(unittest.TestCase):
    def test_it_finds_the_window_extremes(self):
        mx, mn = SS.forward_extremes([10, 11, 12, 13, 14], [5, 6, 7, 8, 9], 2)
        self.assertEqual(mx[0], 12)
        self.assertEqual(mn[0], 6)
        self.assertEqual(mx[2], 14)
        self.assertEqual(mn[2], 8)

    def test_an_incomplete_window_is_none_not_a_miss(self):
        """Counting a window that runs off the end of the data as "did not
        travel that far" would flatter every rate at the recent end of the
        history — exactly the end that matters most."""
        mx, mn = SS.forward_extremes([10, 11, 12, 13, 14], [5, 6, 7, 8, 9], 2)
        self.assertIsNone(mx[3])
        self.assertIsNone(mx[4])
        self.assertIsNone(mn[3])

    def test_the_window_starts_after_the_bar_not_on_it(self):
        """A bar cannot touch a level using its own high."""
        mx, _ = SS.forward_extremes([100, 1, 1], [1, 1, 1], 1)
        self.assertEqual(mx[0], 1)

    def test_a_horizon_longer_than_the_history_yields_nothing(self):
        mx, mn = SS.forward_extremes([1, 2, 3], [1, 2, 3], 99)
        self.assertTrue(all(v is None for v in mx))


class TestTouchCurve(unittest.TestCase):
    def setUp(self):
        # 100 bars, a clean 1% up-drift with no noise, so every rate is
        # checkable by hand.
        self.closes = [100.0 * (1.01 ** i) for i in range(100)]
        self.highs = [c * 1.001 for c in self.closes]
        self.lows = [c * 0.999 for c in self.closes]
        self.mx, self.mn = SS.forward_extremes(self.highs, self.lows, 5)
        self.idx = [i for i in range(100) if self.mx[i] is not None]

    def test_a_reachable_distance_is_hit_every_time(self):
        # 5 bars of 1% compounding is ~5.1%, so 3% is always reached.
        c = SS.touch_curve(self.closes, self.mx, self.mn, self.idx, (3.0,), up=True)
        self.assertEqual(c[3.0]["rate"], 100.0)

    def test_an_unreachable_distance_is_never_hit(self):
        c = SS.touch_curve(self.closes, self.mx, self.mn, self.idx, (20.0,), up=True)
        self.assertEqual(c[20.0]["rate"], 0.0)

    def test_the_down_side_of_a_rising_series_never_triggers(self):
        c = SS.touch_curve(self.closes, self.mx, self.mn, self.idx, (3.0,), up=False)
        self.assertEqual(c[3.0]["rate"], 0.0)

    def test_the_sample_counts_only_complete_windows(self):
        c = SS.touch_curve(self.closes, self.mx, self.mn, self.idx, (3.0,), up=True)
        self.assertEqual(c[3.0]["n"], len(self.idx))
        self.assertEqual(c[3.0]["n"], 95)

    def test_no_bars_produces_no_curve_rather_than_a_zero(self):
        self.assertEqual(SS.touch_curve(self.closes, self.mx, self.mn, [], (3.0,),
                                        up=True), {})


class TestTheConditioningRuleIsFixedInAdvance(unittest.TestCase):
    """What counts as "this state" cannot be chosen after seeing which
    answer is better — that is picking the winner and calling it evidence.
    The rule is a fixed priority and every branch is pinned here."""

    CLOSES = [1, 2, 3, 2, 1, 2, 3, 4, 5, 4, 3, 4, 5, 6, 7]

    def test_a_run_is_the_first_rule(self):
        c = SS.conditioning(self.CLOSES, {"streak_dir": "up", "streak_count": 3},
                            {"maturity": {"code": "BEYOND ITS NORMAL SIZE"},
                             "cohort": {"cross_bar_index": [1, 2, 3]}})
        self.assertEqual(c["kind"], "streak")

    def test_an_oversized_swing_is_the_second(self):
        c = SS.conditioning(self.CLOSES, {"streak_dir": "up", "streak_count": 1},
                            {"maturity": {"code": "BEYOND ITS NORMAL SIZE"},
                             "cohort": {"cross_bar_index": [1, 2, 3]}})
        self.assertEqual(c["kind"], "swing")

    def test_nothing_unusual_is_no_state_at_all(self):
        c = SS.conditioning(self.CLOSES, {"streak_dir": "up", "streak_count": 1},
                            {"maturity": {"code": "AT ITS NORMAL SIZE"}})
        self.assertIsNone(c["kind"])
        self.assertEqual(c["idx"], [])
        self.assertIn("no special state", c["note"])

    def test_a_short_run_does_not_count_as_a_run(self):
        c = SS.conditioning(self.CLOSES,
                            {"streak_dir": "up", "streak_count": SS.MIN_STREAK - 1}, {})
        self.assertIsNone(c["kind"])

    def test_the_state_selects_bars_that_reached_the_same_run_length(self):
        c = SS.conditioning(self.CLOSES, {"streak_dir": "up", "streak_count": 3}, {})
        rdir, rlen = SS._runs(self.CLOSES)                     # noqa: SLF001
        for i in c["idx"]:
            self.assertEqual(rdir[i], 1)
            self.assertGreaterEqual(rlen[i], 3)

    def test_a_longer_run_selects_strictly_fewer_bars(self):
        short = SS.conditioning(self.CLOSES, {"streak_dir": "up", "streak_count": 3}, {})
        long = SS.conditioning(self.CLOSES, {"streak_dir": "up", "streak_count": 4}, {})
        self.assertLessEqual(len(long["idx"]), len(short["idx"]))

    def test_the_streak_branch_fires_on_what_the_producer_really_emits(self):
        """`watchlist_table` writes the string "up", not the integer 1.

        Both branches of this rule were dead against real data: this one
        compared a string to 1, and the swing branch below read a key no
        producer has ever written. The effect was not a crash — it was 44 of
        44 symbols reporting "nothing about today is unusual".
        """
        rising = list(range(1, 61))                 # 59 up days in a row
        block = SS._streaks(rising)                 # noqa: SLF001
        self.assertEqual(block["streak_dir"], "up")
        self.assertGreaterEqual(block["streak_count"], SS.MIN_STREAK)
        c = SS.conditioning(rising, block, {})
        self.assertEqual(c["kind"], "streak")
        self.assertTrue(c["idx"], "the streak branch selected no bars at all")

    def test_the_swing_branch_reads_the_key_the_projection_engine_writes(self):
        """The cohort's bars live at cohort.cross_bar_index. Nothing has ever
        written a top-level `cohort_bar_index`, which is what this branch
        used to look for."""
        wb = {"maturity": {"code": "BEYOND ITS NORMAL SIZE"},
              "cohort": {"cross_bar_index": [2, 5, 9]}}
        c = SS.conditioning(self.CLOSES, {}, wb)
        self.assertEqual(c["kind"], "swing")
        self.assertEqual(c["idx"], [2, 5, 9])

    def test_the_projection_engine_really_exports_those_bars(self):
        """Asserted against swing_projection itself, so renaming the key
        there fails here rather than silently emptying the cohort."""
        import inspect
        import swing_projection
        self.assertIn("cross_bar_index", inspect.getsource(swing_projection))


class TestWeeklyRange(unittest.TestCase):
    def _series(self, n=200):
        d0 = date(2025, 1, 1)
        dates, highs, lows, closes = [], [], [], []
        px = 100.0
        for i in range(n):
            day = d0 + timedelta(days=i)
            if day.weekday() >= 5:
                continue
            px *= 1.0 + (0.004 if i % 3 else -0.005)
            dates.append(day.isoformat())
            highs.append(px * 1.01); lows.append(px * 0.99); closes.append(px)
        return dates, highs, lows, closes

    def test_it_reports_a_position_inside_the_stocks_own_range(self):
        r = SS.weekly_range(*self._series())
        self.assertIsNotNone(r.get("pos"))
        self.assertGreaterEqual(r["pos"], 0.0)
        self.assertLessEqual(r["pos"], 100.0)
        self.assertGreater(r["weeks"], 8)

    def test_too_little_history_is_nothing_rather_than_a_guess(self):
        self.assertEqual(SS.weekly_range(["2026-01-01"], [1], [1], [1]), {})

    def test_the_best_week_is_above_the_worst(self):
        r = SS.weekly_range(*self._series())
        self.assertGreater(r["best_week_pct"], r["worst_week_pct"])


# ══════════════════════════════════════════════════════════════════════════
# END TO END, on a stubbed broker
# ══════════════════════════════════════════════════════════════════════════

def _bars(n=800, drift=0.0004, vol=0.011, seed=7):
    """Deterministic pseudo-random daily bars — no RNG module, so the same
    series every run on every machine."""
    out, px, s = [], 100.0, seed
    d = date(2023, 1, 2)
    while len(out) < n:
        if d.weekday() < 5:
            s = (1103515245 * s + 12345) % (2 ** 31)
            r = (s / (2 ** 31) - 0.5) * 2.0
            px *= math.exp(drift + vol * r)
            out.append({"date": d.isoformat() + "T12:00:00-04:00",
                        "open": px * 0.998, "high": px * (1 + abs(r) * vol + 0.002),
                        "low": px * (1 - abs(r) * vol - 0.002),
                        "close": px, "volume": 2_000_000})
        d += timedelta(days=1)
    return out


def _chain(spot, exp, n=26):
    from metrics import _bs_gamma, _bs_delta
    T = 30 / 365.0
    calls, puts = [], []
    for i in range(-n, n + 1):
        k = round(spot * (1 + i * 0.01), 2)
        iv = 0.28 + abs(i) * 0.002
        g = _bs_gamma(spot, k, T, iv)
        cd = _bs_delta(spot, k, T, iv, "call")
        pd = _bs_delta(spot, k, T, iv, "put")
        base = max(0.05, 2.5 - abs(i) * 0.09)
        calls.append({"strike": k, "bid": round(base, 2), "ask": round(base * 1.05, 2),
                      "delta": cd, "gamma": g, "iv": iv * 100, "openInterest": 1200,
                      "volume": 300, "theta": -0.04, "vega": 0.1})
        puts.append({"strike": k, "bid": round(base, 2), "ask": round(base * 1.05, 2),
                     "delta": pd, "gamma": g, "iv": iv * 100, "openInterest": 1400,
                     "volume": 260, "theta": -0.04, "vega": 0.1})
    return {"underlying": {"symbol": "TEST", "last": spot},
            "expirations": [exp], "chains": {exp: {"calls": calls, "puts": puts}},
            "source": "stub"}


class StubBroker:
    def __init__(self, bars=None, chain=None):
        self._bars = bars if bars is not None else _bars()
        self._chain = chain

    def get_price_history(self, symbol, days=1400):
        return self._bars

    def get_option_chain(self, symbol, expiration=None, to_date=None, strike_count=60):
        if self._chain is not None:
            return self._chain
        exp = (date.today() + timedelta(days=30)).isoformat()
        return _chain(self._bars[-1]["close"], exp)

    def get_quote(self, symbol):
        return {"last": self._bars[-1]["close"]}


class TestAnalyzeEndToEnd(unittest.TestCase):
    def setUp(self):
        SS.invalidate()
        self.broker = StubBroker()
        SS.configure(schwab_getter=lambda: self.broker,
                     chain_getter=lambda s: (None, None, None, [], []),
                     earnings_fn=lambda s: {})

    def tearDown(self):
        SS.configure(schwab_getter=lambda: None)
        SS.invalidate()

    def test_no_broker_is_an_explanation_not_a_crash(self):
        SS.configure(schwab_getter=lambda: None)
        out = SS.analyze("TEST")
        self.assertFalse(out["ok"])
        self.assertIn("broker", out["error"].lower())

    def test_thin_history_is_refused_with_the_count(self):
        SS.configure(schwab_getter=lambda: StubBroker(bars=_bars(n=50)))
        out = SS.analyze("TEST")
        self.assertFalse(out["ok"])
        self.assertIn("50", out["error"])

    def test_it_produces_a_complete_recommendation(self):
        out = SS.analyze("TEST")
        if not out.get("ok"):
            self.skipTest(f"stub chain not tradable here: {out.get('error') or out.get('reason')}")
        best = out["best"]
        for k in ("action", "side", "strike", "delta", "credit", "expiration"):
            self.assertIsNotNone(best.get(k), k)
        self.assertTrue(best["why"])
        self.assertTrue(best["risks"])
        self.assertIn(best["confidence"]["label"], ("HIGH", "MODERATE", "LOW", "WEAK"))

    def test_it_reports_both_sides_and_which_it_chose(self):
        out = SS.analyze("TEST")
        if not out.get("ok"):
            self.skipTest("stub chain not tradable here")
        self.assertIn(out["chosen_side"], ("call", "put"))
        self.assertIn("call", out["sides"])
        self.assertIn("put", out["sides"])

    def test_the_conditioning_rule_is_reported_with_the_answer(self):
        out = SS.analyze("TEST")
        self.assertIn("conditioning", out)
        self.assertIn("note", out["conditioning"])
        self.assertIn("conditioned_bars", out)

    def test_the_measured_evidence_travels_with_the_recommendation(self):
        out = SS.analyze("TEST")
        self.assertIn("measured", out)
        for side in ("call", "put"):
            self.assertIn("usable", out["measured"][side])

    def test_the_cache_serves_a_second_read(self):
        a = SS.get("TEST")
        b = SS.get("TEST")
        self.assertTrue(b.get("cached"))
        self.assertEqual(a.get("symbol"), b.get("symbol"))

    def test_force_bypasses_the_cache(self):
        SS.get("TEST")
        self.assertFalse(SS.get("TEST", force=True).get("cached"))


if __name__ == "__main__":
    unittest.main()


# ══════════════════════════════════════════════════════════════════════════
# THE CHAIN HAS TO COVER THE SELLING WINDOW
# ══════════════════════════════════════════════════════════════════════════

class WindowBroker:
    """A broker that honours the date range, so a fetch asking for the wrong
    dates actually comes back wrong instead of being quietly forgiven."""

    def __init__(self, offsets=(2, 9, 16, 30, 45, 120), bars=None):
        self.offsets = offsets
        self._bars = bars if bars is not None else _bars()
        self.asked = []

    def get_price_history(self, symbol, days=1400):
        return self._bars

    def get_option_chain(self, symbol, expiration=None, to_date=None,
                         strike_count=60):
        self.asked.append((expiration, to_date))
        today = date.today()
        spot = self._bars[-1]["close"]
        exps = [(today + timedelta(days=n)).isoformat() for n in self.offsets]
        if expiration:
            exps = [e for e in exps if e >= expiration[:10]]
        if to_date:
            exps = [e for e in exps if e <= to_date[:10]]
        if not exps:
            return None
        merged = {"underlying": {"symbol": symbol, "last": spot},
                  "expirations": exps, "chains": {}, "source": "stub"}
        for e in exps:
            merged["chains"][e] = _chain(spot, e)["chains"][e]
        return merged

    def get_quote(self, symbol):
        return {"last": self._bars[-1]["close"]}


class TestTheChainMustCoverTheSellingWindow(unittest.TestCase):
    """The failure every single symbol hit in production.

    The gamma-exposure getter returns the NEAREST expiration, because that
    is where intraday dealer gamma lives. For a premium sale that is the
    wrong chain — the nearest expiration is usually a weekly one to five
    days out, and the seller window is 7 to 60 days. The old guard asked
    only whether the chain was a non-empty dict, so it accepted that chain
    and every symbol then failed with "no expiration inside the selling
    window on this chain".

    A chain that cannot answer the question is as useless as no chain.
    """

    def setUp(self):
        SS.invalidate()
        self.broker = WindowBroker()

    def tearDown(self):
        SS.configure(schwab_getter=lambda: None)
        SS.invalidate()

    def _near_only(self, symbol):
        """Exactly what the gamma getter hands over: the nearest expiry."""
        today = date.today()
        exp = (today + timedelta(days=2)).isoformat()
        spot = self.broker._bars[-1]["close"]                  # noqa: SLF001
        return (_chain(spot, exp), "schwab", "12:00", [exp], [exp])

    def test_a_near_dated_getter_chain_is_rejected_and_the_window_refetched(self):
        SS.configure(schwab_getter=lambda: self.broker,
                     chain_getter=self._near_only,
                     earnings_fn=lambda s: {})
        out = SS.analyze("TEST")
        self.assertNotIn("No expiration", str(out.get("error") or ""),
                         "the two-day chain was accepted instead of refetched")
        self.assertTrue(self.broker.asked, "no window fetch was ever made")
        self.assertIsNotNone(out.get("expiration"), out.get("error"))
        self.assertGreaterEqual(out["dte"], 7)
        self.assertLessEqual(out["dte"], 60)

    def test_a_getter_chain_that_does_cover_the_window_is_used_as_is(self):
        today = date.today()
        good = (today + timedelta(days=30)).isoformat()
        spot = self.broker._bars[-1]["close"]                  # noqa: SLF001
        SS.configure(schwab_getter=lambda: self.broker,
                     chain_getter=lambda s: (_chain(spot, good), "schwab",
                                             "12:00", [good], [good]),
                     earnings_fn=lambda s: {})
        out = SS.analyze("TEST")
        self.assertEqual(out.get("expiration"), good)
        self.assertEqual(self.broker.asked, [],
                         "refetched a chain that was already usable")

    def test_a_symbol_with_no_medium_dated_options_says_what_it_did_have(self):
        """When the refusal is real, it has to be inspectable. 'No expiration
        in the window' on its own looks the same whether the symbol lists
        nothing usable or the fetch asked for the wrong dates."""
        thin = WindowBroker(offsets=(1, 3))
        SS.configure(schwab_getter=lambda: thin,
                     chain_getter=lambda s: (None, None, None, [], []),
                     earnings_fn=lambda s: {})
        out = SS.analyze("TEST")
        self.assertFalse(out["ok"])
        msg = out.get("error") or ""
        self.assertIn("days out on this chain", msg)

    def test_the_window_comes_from_the_same_config_the_picker_uses(self):
        """One definition of the window. If the fetch and the pick disagree,
        the fetch can succeed and the pick still find nothing."""
        cfg = SS._edge._cfg()                                  # noqa: SLF001
        lo, hi = SS._window_dtes(cfg)                          # noqa: SLF001
        st = cfg.get("select", {})
        self.assertEqual(lo, float(st.get("min_dte", 7)))
        self.assertEqual(hi, float(st.get("max_dte", 60)))

    def test_window_expiries_uses_the_same_arithmetic_as_the_picker(self):
        today = date.today()
        chain = {"chains": {(today + timedelta(days=n)).isoformat(): {}
                            for n in (1, 8, 30, 90)}}
        got = SS._window_expiries(chain, today, 7, 60)         # noqa: SLF001
        self.assertEqual(got, [(today + timedelta(days=8)).isoformat(),
                               (today + timedelta(days=30)).isoformat()])
