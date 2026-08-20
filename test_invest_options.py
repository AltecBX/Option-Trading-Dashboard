"""Tests for invest_options — the chain layer, the two optimizers and the
entry verdict.

The rules pinned here are the ones that are easy to break by accident and
expensive to get wrong:

  1. A put strike may NEVER exceed the buy zone, however rich the premium.
  2. A contract offered below its own intrinsic value is a stale quote, not
     an opportunity, and must not win a comparison.
  3. ExpectedRV30 is a thirty-day forecast and never appears in the LEAPS
     volatility context.
  4. HIGH value-trap risk, UNRELIABLE fair value confidence, a specialized
     business type and missing critical data each block every bullish answer.

Everything runs against a synthetic chain and a temporary data directory. No
network, no broker, no live quotes.
"""

import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta

os.environ.setdefault("JERRY_NO_NET", "1")

import fair_value as FV
import invest_options as IO
import structures as ST

TODAY = date(2026, 8, 18)
SPOT = 100.0


def exp_at(days):
    return (TODAY + timedelta(days=days)).isoformat()


def leg(strike, bid, ask, iv=0.28, oi=500):
    return {"strike": strike, "bid": bid, "ask": ask, "last": (bid + ask) / 2,
            "iv": iv, "volume": 50, "openInterest": oi, "delta": None}


def build_chain(dtes=(45, 400, 550), spot=SPOT):
    """A well-behaved chain: calls and puts every $5 from 50 to 150, priced
    at intrinsic plus a time value that grows with the tenor."""
    out = {"underlying": {"symbol": "TEST", "last": spot},
           "expirations": [], "chains": {}, "source": "test"}
    for d in dtes:
        t = d / 365.0
        calls, puts = [], []
        for k in range(50, 155, 5):
            tv = 0.28 * spot * (t ** 0.5) * 0.4
            c_int = max(0.0, spot - k)
            p_int = max(0.0, k - spot)
            calls.append(leg(float(k), round(c_int + tv, 2), round(c_int + tv + 0.4, 2)))
            puts.append(leg(float(k), round(p_int + tv, 2), round(p_int + tv + 0.4, 2)))
        key = exp_at(d)
        out["chains"][key] = {"calls": calls, "puts": puts}
        out["expirations"].append(key)
    return out


def bars(n=800, start=60.0, end=100.0):
    out = []
    for i in range(n):
        px = start + (end - start) * i / max(1, n - 1)
        # a little wobble so realized volatility is not zero
        px *= 1.0 + (0.02 if i % 3 == 0 else -0.015 if i % 3 == 1 else 0.0)
        d = (TODAY - timedelta(days=n - i)).isoformat()
        out.append({"date": d, "close": round(px, 2), "high": round(px * 1.01, 2),
                    "low": round(px * 0.99, 2), "open": round(px, 2),
                    "volume": 1_000_000})
    return out


def path():
    g = FV.growth_scenarios([2, 5, 8, 11, 14, 17, 20])
    m = FV.multiple_scenarios([18 + (i % 20) * 0.2 for i in range(400)])
    return {"price": SPOT, "eps_ttm": 5.0, "growth": g, "multiples": m,
            "dps_ttm": 2.0}


PROBS = {"bear": 0.25, "base": 0.50, "bull": 0.25}


class OptionsCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="invopt-")
        self.chain = build_chain()
        IO._CHAIN_MEM.clear()
        IO.configure(chain_fn=lambda s: self.chain,
                     bars_fn=lambda s, d: {"bars": bars()},
                     rate_fn=lambda y: {"pct": 4.0, "as_of": "2026-08-17",
                                        "source": "test curve"},
                     earnings_fn=lambda s: {"next": exp_at(20), "last": None},
                     data_dir=self.dir, today_fn=lambda: TODAY)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        IO.configure()
        IO._CHAIN_MEM.clear()


class TestChainAccess(OptionsCase):
    def test_expirations_are_filtered_by_days_to_expiration(self):
        got = IO.expirations_between(self.chain, 7, 75, TODAY)
        self.assertEqual([d for _e, d in got], [45.0])
        leaps = IO.expirations_between(self.chain, 270, 730, TODAY)
        self.assertEqual([d for _e, d in leaps], [400.0, 550.0])

    def test_dte_of_handles_junk(self):
        self.assertIsNone(IO.dte_of("not-a-date", TODAY))
        self.assertEqual(IO.dte_of(exp_at(10), TODAY), 10.0)

    def test_chain_is_cached(self):
        calls = {"n": 0}

        def counting(_s):
            calls["n"] += 1
            return self.chain
        IO._CHAIN_MEM.clear()
        IO.configure(chain_fn=counting, data_dir=self.dir, today_fn=lambda: TODAY)
        IO.chain("TEST")
        IO.chain("TEST")
        self.assertEqual(calls["n"], 1)

    def test_call_candidates_are_taken_from_the_money_outward(self):
        """A ninety-strike long-dated chain must not spend the whole budget on
        deep in-the-money contracts nobody trades."""
        wide = build_chain(dtes=(400,))
        key = exp_at(400)
        extra = [leg(float(k), 0.0, 0.0, oi=0) for k in range(5, 50, 5)]
        wide["chains"][key]["calls"] = extra + wide["chains"][key]["calls"]
        rows = IO.call_candidates("TEST", wide, key, 400.0, SPOT, path(),
                                  PROBS, {}, max_rows=5)
        self.assertTrue(rows)
        for r in rows:
            self.assertLess(abs(r["contract"]["strike"] - SPOT), 40.0)

    def test_a_provider_failure_returns_none_rather_than_raising(self):
        IO._CHAIN_MEM.clear()
        IO.configure(chain_fn=lambda s: (_ for _ in ()).throw(RuntimeError("x")),
                     data_dir=self.dir, today_fn=lambda: TODAY)
        self.assertIsNone(IO.chain("TEST"))


class TestQuoteQuality(OptionsCase):
    def test_a_call_offered_below_intrinsic_is_rejected(self):
        row = leg(15.0, 80.0, 82.0)          # intrinsic is 85 at a 100 spot
        q = IO.quote_quality(row, {}, spot=SPOT, side="call")
        self.assertFalse(q["priceable"])
        self.assertIn("intrinsic value", q["label"])

    def test_a_fair_deep_in_the_money_call_is_accepted(self):
        row = leg(15.0, 85.5, 86.5)
        q = IO.quote_quality(row, {}, spot=SPOT, side="call")
        self.assertTrue(q["priceable"])

    def test_a_put_bid_above_its_strike_is_rejected(self):
        row = leg(20.0, 25.0, 26.0)
        q = IO.quote_quality(row, {}, spot=SPOT, side="put")
        self.assertFalse(q["priceable"])
        self.assertIn("can never be worth", q["label"])

    def test_a_crossed_quote_is_rejected(self):
        row = leg(100.0, 9.0, 4.0)
        q = IO.quote_quality(row, {}, spot=SPOT, side="call")
        self.assertFalse(q["priceable"])
        self.assertIn("crossed", q["label"])

    def test_thin_liquidity_is_priceable_but_not_ok(self):
        row = leg(100.0, 4.0, 4.4, oi=1)
        q = IO.quote_quality(row, {}, spot=SPOT, side="call")
        self.assertTrue(q["priceable"])
        self.assertFalse(q["ok"])
        self.assertIn("open interest", q["label"])

    def test_a_wide_spread_is_named_with_its_width(self):
        row = leg(100.0, 1.0, 3.0)
        q = IO.quote_quality(row, {}, spot=SPOT, side="call")
        self.assertFalse(q["ok"])
        self.assertIn("bid-ask spread", q["label"])


class TestGreeks(OptionsCase):
    def test_delta_is_filled_from_black_scholes_and_says_so(self):
        g = IO.contract_greeks(leg(100.0, 4.0, 4.4), SPOT, 100.0, 1.0, "call",
                               rate_pct=4.0)
        self.assertIsNotNone(g["delta"])
        self.assertIn("Black-Scholes", g["delta_source"])

    def test_a_delta_from_the_feed_is_kept(self):
        row = leg(100.0, 4.0, 4.4)
        row["delta"] = 0.42
        g = IO.contract_greeks(row, SPOT, 100.0, 1.0, "call")
        self.assertAlmostEqual(g["delta"], 0.42)
        self.assertEqual(g["delta_source"], "chain")


class TestVolatilityContext(OptionsCase):
    def test_leaps_context_never_uses_expected_rv30(self):
        out = IO.realized_vol_context(bars(), 500)
        self.assertTrue(out["available"])
        self.assertNotIn("erv30", out)
        self.assertIn("thirty-day forecast", out["note"])
        self.assertIn("rv_tenor", out)

    def test_the_measurement_window_matches_the_tenor(self):
        short = IO.realized_vol_context(bars(), 365)
        long = IO.realized_vol_context(bars(), 730)
        self.assertLess(short["tenor_trading_days"], long["tenor_trading_days"])

    def test_thin_history_is_refused_with_the_count(self):
        out = IO.realized_vol_context(bars(n=100), 500)
        self.assertFalse(out["available"])
        self.assertIn("100", out["reason"])


class TestLeapsObservationStore(OptionsCase):
    def test_recording_and_reading_back(self):
        rows = [{"exp": exp_at(400), "dte": 400, "strike": 100.0, "iv": 0.30,
                 "delta": 0.55, "mid": 12.0, "open_interest": 300}]
        self.assertTrue(IO.record_leaps_observation("TEST", SPOT, rows,
                                                    TODAY.isoformat()))
        got = IO.load_leaps_observations("TEST")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["rows"][0]["strike"], 100.0)

    def test_one_row_per_day_the_last_write_winning(self):
        for iv in (0.30, 0.35):
            IO.record_leaps_observation(
                "TEST", SPOT,
                [{"exp": exp_at(400), "dte": 400, "strike": 100.0, "iv": iv}],
                TODAY.isoformat())
        got = IO.load_leaps_observations("TEST")
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0]["rows"][0]["iv"], 0.35)

    def test_history_says_it_is_empty_rather_than_inventing_a_percentile(self):
        out = IO.leaps_iv_history("TEST", 400, 0.30)
        self.assertFalse(out["available"])
        self.assertIn("never back-fills", out["reason"])
        self.assertIsNone(out.get("percentile"))

    def test_a_percentile_appears_once_there_are_enough_observations(self):
        for i in range(30):
            day = (TODAY - timedelta(days=i)).isoformat()
            IO.record_leaps_observation(
                "TEST", SPOT,
                [{"exp": exp_at(400), "dte": 400, "strike": 100.0,
                  "iv": 0.20 + i * 0.005}], day)
        out = IO.leaps_iv_history("TEST", 400, 0.20)
        self.assertTrue(out["available"])
        self.assertEqual(out["n"], 30)
        self.assertLess(out["percentile"], 10.0)

    def test_only_matching_tenors_count(self):
        for i in range(30):
            day = (TODAY - timedelta(days=i)).isoformat()
            IO.record_leaps_observation(
                "TEST", SPOT,
                [{"exp": exp_at(30), "dte": 30, "strike": 100.0, "iv": 0.40}],
                day)
        out = IO.leaps_iv_history("TEST", 500, 0.30)
        self.assertFalse(out["available"])


class TestPutOptimizer(OptionsCase):
    def test_no_strike_above_the_buy_zone_is_ever_offered(self):
        rows = IO.put_candidates("TEST", self.chain, exp_at(45), 45.0, SPOT,
                                 80.0, path(), PROBS, {})
        self.assertTrue(rows)
        for r in rows:
            self.assertLessEqual(r["contract"]["strike"], 80.0)

    def test_a_higher_buy_zone_admits_more_strikes(self):
        few = IO.put_candidates("TEST", self.chain, exp_at(45), 45.0, SPOT,
                                70.0, path(), PROBS, {})
        many = IO.put_candidates("TEST", self.chain, exp_at(45), 45.0, SPOT,
                                 95.0, path(), PROBS, {})
        self.assertLess(len(few), len(many))

    def test_without_a_buy_zone_nothing_is_offered_at_all(self):
        out = IO.best_short_put("TEST", self.chain, SPOT, None, path(), PROBS,
                                {}, today=TODAY)
        self.assertFalse(out["available"])
        self.assertIn("acquisition price has to come before the strike",
                      out["reason"])

    def test_no_qualifying_strike_produces_the_named_refusal(self):
        out = IO.best_short_put("TEST", self.chain, SPOT, 20.0, path(), PROBS,
                                {}, today=TODAY)
        self.assertFalse(out["available"])
        self.assertEqual(out["headline"],
                         "WAIT — INSUFFICIENT PREMIUM AT DESIRED OWNERSHIP PRICE")
        self.assertIn("opposite of the point", out["reason"])

    def test_an_inadequate_premium_does_not_clear_the_treasury_hurdle(self):
        out = IO.best_short_put("TEST", self.chain, SPOT, 60.0, path(), PROBS,
                                {}, today=TODAY)
        if out.get("best"):
            self.assertIsNotNone(out["hurdle_pct"])
            if not out["clears_hurdle"]:
                self.assertEqual(
                    out["headline"],
                    "WAIT — INSUFFICIENT PREMIUM AT DESIRED OWNERSHIP PRICE")
                self.assertIsNotNone(out["required_bid"])

    def test_the_required_bid_is_solved_not_searched(self):
        need = IO.required_put_bid(10_000.0, 90.0, 500.0, 1.0, 4.0, 5.0)
        self.assertIsNotNone(need)
        # Feeding that bid back in reproduces the target return.
        r = ST.secured_put(SPOT, 10_000.0, 90.0, need,
                           {"bear": 85.0, "base": 100.0, "bull": 115.0},
                           {"bear": 1.0, "base": 0.0, "bull": 0.0}, 1.0,
                           rate_pct=4.0)
        # expected obligation of 500 at a 100% bear weight = (90-85)*100
        self.assertAlmostEqual(r["weighted_annualized_pct"], 5.0, places=6)

    def test_effective_assignment_cost_is_on_every_candidate(self):
        rows = IO.put_candidates("TEST", self.chain, exp_at(45), 45.0, SPOT,
                                 95.0, path(), PROBS, {})
        for r in rows:
            k = r["contract"]
            self.assertAlmostEqual(k["effective_assignment_cost"],
                                   k["strike"] - k["credit"])
            self.assertAlmostEqual(r["notional"], k["strike"] * 100)


class TestComparison(OptionsCase):
    def test_every_row_is_marked_at_one_expiration(self):
        out = IO.build_comparison("TEST", self.chain, SPOT, 95.0, path(),
                                  PROBS, {}, today=TODAY, bars=bars())
        self.assertTrue(out["available"])
        for r in out["rows"]:
            if r.get("eligible") and r.get("expiration"):
                self.assertEqual(r["expiration"], out["expiration"])

    def test_the_comparison_horizon_is_long_dated(self):
        out = IO.build_comparison("TEST", self.chain, SPOT, 95.0, path(),
                                  PROBS, {}, today=TODAY, bars=bars())
        self.assertGreaterEqual(out["dte"], 270)

    def test_an_unavailable_structure_keeps_its_reason(self):
        out = IO.build_comparison("TEST", self.chain, SPOT, 20.0, path(),
                                  PROBS, {}, today=TODAY, bars=bars())
        put = next(r for r in out["rows"] if r["kind"] == ST.PUT)
        self.assertFalse(put["eligible"])
        self.assertIn("buy zone", put["reason"])

    def test_no_long_dated_expiration_is_reported_not_faked(self):
        short = build_chain(dtes=(45,))
        out = IO.build_comparison("TEST", short, SPOT, 95.0, path(), PROBS, {},
                                  today=TODAY, bars=bars())
        self.assertFalse(out["available"])
        self.assertIn("Long-dated contracts do not exist", out["reason"])

    def test_downside_context_is_a_different_question_and_says_so(self):
        out = IO.build_comparison("TEST", self.chain, SPOT, 95.0, path(),
                                  PROBS, {}, today=TODAY, bars=bars())
        dc = out["downside_context"]
        self.assertTrue(dc["available"])
        self.assertLess(dc["tail_price"], SPOT)
        self.assertIn("different questions", dc["note"])

    def test_a_below_intrinsic_quote_cannot_win(self):
        """The bug this check exists for: a stale deep-in-the-money offer."""
        broken = build_chain()
        key = exp_at(400)
        broken["chains"][key]["calls"].insert(0, leg(5.0, 60.0, 62.0))
        out = IO.build_comparison("TEST", broken, SPOT, 95.0, path(), PROBS,
                                  {}, today=TODAY, bars=bars())
        for r in out["rows"]:
            if r["kind"] == ST.LEAPS and r.get("eligible"):
                self.assertNotEqual(r["contract"]["strike"], 5.0)

    def test_unspent_capital_earns_the_matching_treasury_rate(self):
        out = IO.build_comparison("TEST", self.chain, SPOT, 95.0, path(),
                                  PROBS, {}, today=TODAY, bars=bars())
        self.assertAlmostEqual(out["rate"]["pct"], 4.0)
        leaps = next(r for r in out["rows"] if r["kind"] == ST.LEAPS)
        self.assertGreater(leaps["unused_capital"], 0)


class TestEntryVerdict(OptionsCase):
    def snap(self, **kw):
        base = {"price": SPOT, "eps_ttm": 5.0,
                "business_type": {"type": "STANDARD", "label": "Standard",
                                  "note": ""},
                "value_trap": {"level": "LOW RISK", "active": []},
                "verdict": {"verdict": "WATCH", "reasons": [],
                            "what_would_change": []}}
        base.update(kw)
        return base

    def fair(self, **kw):
        base = {"available": True, "bear": 70.0, "base": 100.0, "bull": 130.0,
                "confidence_level": "HIGH", "confidence": {"reason": ""},
                "confidence_credit": 1.0, "margin_of_safety": 0.2,
                "credited": 100.0, "buy_zone": 80.0,
                "base_method_label": "Its own valuation history"}
        base.update(kw)
        return base

    def test_specialized_business_types_are_refused(self):
        for kind in ("BANK", "INSURANCE", "BROKER", "REIT"):
            out = IO.entry_verdict(
                self.snap(business_type={"type": kind, "note": "n/a here"}),
                self.fair(), {}, {})
            self.assertEqual(out["verdict"], "SPECIALIZED MODEL REQUIRED")
            self.assertEqual(out["blocked_by"], "business type")

    def test_high_trap_risk_blocks_everything_bullish(self):
        out = IO.entry_verdict(
            self.snap(value_trap={"level": "HIGH RISK",
                                  "active": [{"label": "Revenue deteriorating"}]}),
            self.fair(), {"available": True, "preferred": "SHARES"}, {})
        self.assertEqual(out["verdict"], "AVOID")
        self.assertEqual(out["blocked_by"], "value trap")

    def test_unreliable_fair_value_forces_wait(self):
        out = IO.entry_verdict(
            self.snap(), self.fair(confidence_level="UNRELIABLE"),
            {"available": True, "preferred": "SHARES"}, {})
        self.assertEqual(out["verdict"], "WAIT")
        self.assertEqual(out["blocked_by"], "fair value confidence")

    def test_no_price_is_insufficient_data(self):
        out = IO.entry_verdict(self.snap(price=None), self.fair(), {}, {})
        self.assertEqual(out["verdict"], "INSUFFICIENT DATA")

    def test_no_fair_value_is_wait_with_a_reason(self):
        out = IO.entry_verdict(self.snap(),
                               {"available": False, "reason": "thin history"},
                               {}, {})
        self.assertEqual(out["verdict"], "WAIT")
        self.assertIn("thin history", out["reasons"])

    def test_a_deteriorating_thesis_carries_the_business_verdict_through(self):
        out = IO.entry_verdict(
            self.snap(verdict={"verdict": "AVOID", "reasons": ["losing money"],
                               "what_would_change": ["earn a profit"]}),
            self.fair(), {}, {})
        self.assertEqual(out["verdict"], "AVOID")
        self.assertIn("losing money", out["reasons"])

    def test_inside_the_buy_zone_the_comparator_decides(self):
        comp = {"available": True, "preferred": ST.LEAPS, "toss_up": False,
                "expiration": exp_at(400), "capital": 10_000.0,
                "rows": [{"kind": ST.LEAPS, "weighted_annualized_pct": 12.0,
                          "worst_pnl": -500.0}]}
        out = IO.entry_verdict(self.snap(price=70.0), self.fair(), comp, {})
        self.assertEqual(out["verdict"], "BUY LEAPS")

    def test_a_toss_up_is_reported_as_a_toss_up(self):
        comp = {"available": True, "preferred": ST.SHARES, "toss_up": True,
                "sensitivity": {"reason": "they swap places"},
                "expiration": exp_at(400), "capital": 10_000.0, "rows": []}
        out = IO.entry_verdict(self.snap(price=70.0), self.fair(), comp, {})
        self.assertEqual(out["verdict"], "TOSS UP")
        self.assertEqual(out["blocked_by"], "probability sensitivity")

    def test_inside_the_zone_without_a_chain_still_buys_the_shares(self):
        out = IO.entry_verdict(self.snap(price=70.0), self.fair(),
                               {"available": False, "reason": "no chain"}, {})
        self.assertEqual(out["verdict"], "BUY SHARES")

    def test_above_the_zone_with_an_adequate_put_sells_the_put(self):
        put = {"available": True, "clears_hurdle": True,
               "required_bid": 1.0,
               "best": {"expiration": exp_at(45),
                        "contract": {"strike": 78.0, "credit": 2.0}}}
        out = IO.entry_verdict(self.snap(), self.fair(),
                               {"available": True, "preferred": ST.SHARES,
                                "toss_up": False, "rows": []}, put)
        self.assertEqual(out["verdict"], "SELL PORTFOLIO SECURED PUT")
        self.assertIn("76.00", " ".join(out["reasons"]))

    def test_above_the_zone_without_premium_waits_and_names_both_numbers(self):
        put = {"available": True, "clears_hurdle": False,
               "required_bid": 1.25, "reason": "not enough premium"}
        out = IO.entry_verdict(self.snap(), self.fair(), {"available": True,
                                                         "rows": []}, put)
        self.assertEqual(out["verdict"], "WAIT")
        line = " ".join(out["what_would_change"])
        self.assertIn("$80.00", line)         # the buy zone
        self.assertIn("$100.00", line)        # the current price
        self.assertIn("$1.25", line)          # the bid that would qualify

    def test_an_impossible_base_target_is_explained_rather_than_printed(self):
        out = IO.entry_verdict(
            self.snap(price=300.0),
            self.fair(confidence_level="LOW", confidence_credit=0.35),
            {"available": True, "rows": []}, {})
        line = " ".join(out["what_would_change"])
        self.assertIn("above the", line)
        self.assertIn("optimistic case", line)


class TestManagementPlan(OptionsCase):
    def test_no_position_means_no_plan(self):
        p = IO.management_plan("WAIT", {}, {}, {}, {})
        self.assertFalse(p["available"])
        self.assertIn("nothing to manage", p["reason"])

    def test_the_put_plan_names_the_notional_and_the_rolling_rule(self):
        put = {"best": {"contract": {"strike": 90.0, "credit": 3.0},
                        "notional": 9_000.0}}
        p = IO.management_plan("SELL PORTFOLIO SECURED PUT", {},
                               {"base": 120.0}, {}, put)
        self.assertTrue(p["available"])
        text = " ".join(r["detail"] for r in p["specific"])
        self.assertIn("9,000", text)
        self.assertIn("NET CREDIT", text)
        self.assertIn("do not roll", text.lower())
        self.assertIn("covered-call workflow", text)

    def test_the_leaps_plan_refuses_mechanical_holding(self):
        comp = {"rows": [{"kind": ST.LEAPS,
                          "contract": {"expiration": exp_at(400)}}]}
        p = IO.management_plan("BUY LEAPS", {}, {"base": 120.0}, comp, {})
        text = " ".join(r["detail"] for r in p["specific"])
        self.assertIn("not a plan", text)
        self.assertIn("dividend", text)

    def test_every_plan_carries_the_four_common_exits(self):
        p = IO.management_plan("BUY SHARES", {}, {"base": 120.0, "buy_zone": 90.0},
                               {}, {})
        triggers = [r["trigger"] for r in p["common"]]
        self.assertIn("Thesis invalidation", triggers)
        self.assertIn("Fair value reached", triggers)
        self.assertIn("Estimate revisions deteriorate", triggers)
        self.assertIn("Value trap check flips", triggers)

    def test_no_orders_are_ever_placed(self):
        p = IO.management_plan("BUY SHARES", {}, {"base": 120.0}, {}, {})
        self.assertIn("No orders are placed", p["note"])


class TestBuild(OptionsCase):
    def snap(self):
        return {"price": SPOT, "eps_ttm": 5.0,
                "business_type": {"type": "STANDARD", "note": ""},
                "value_trap": {"level": "LOW RISK", "active": []},
                "verdict": {"verdict": "WATCH", "reasons": [],
                            "what_would_change": []}}

    def fair(self):
        return {"available": True, "bear": 70.0, "base": 100.0, "bull": 130.0,
                "confidence_level": "HIGH", "confidence": {"reason": ""},
                "confidence_credit": 1.0, "margin_of_safety": 0.2,
                "credited": 100.0, "buy_zone": 90.0,
                "base_method_label": "Its own valuation history"}

    def test_a_full_build_produces_every_block(self):
        out = IO.build("TEST", self.snap(), self.fair(), path(), {},
                       probabilities=PROBS)
        self.assertTrue(out["available"])
        for key in ("comparison", "put", "market_risk", "entry", "plan"):
            self.assertIn(key, out)

    def test_a_specialized_type_short_circuits_before_any_chain_call(self):
        snap = self.snap()
        snap["business_type"] = {"type": "BANK", "note": "banks differ"}
        out = IO.build("TEST", snap, self.fair(), path(), {})
        self.assertFalse(out["available"])
        self.assertEqual(out["entry"]["verdict"], "SPECIALIZED MODEL REQUIRED")

    def test_no_chain_is_reported_honestly(self):
        IO._CHAIN_MEM.clear()
        IO.configure(chain_fn=lambda s: None, data_dir=self.dir,
                     today_fn=lambda: TODAY)
        out = IO.build("TEST", self.snap(), self.fair(), path(), {})
        self.assertFalse(out["available"])
        self.assertIn("No option chain", out["reason"])

    def test_recording_stores_long_dated_observations(self):
        IO.build("TEST", self.snap(), self.fair(), path(), {},
                 probabilities=PROBS, record=True)
        self.assertTrue(IO.load_leaps_observations("TEST"))

    def test_not_recording_leaves_the_store_untouched(self):
        IO.build("TEST", self.snap(), self.fair(), path(), {},
                 probabilities=PROBS, record=False)
        self.assertFalse(IO.load_leaps_observations("TEST"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTheCrossingEarningsLine(unittest.TestCase):
    """A short put that sits through a report says so on the action line.

    Information, not a rule. The premium is richer for exactly the reason
    the risk is higher, and this dashboard does not decide that trade-off
    for the reader — it refuses to make them work it out from two dates in
    two different panels.
    """

    def setUp(self):
        self.chain = build_chain()

    def test_a_report_inside_the_contract_is_named(self):
        got = IO._crosses_earnings("2026-10-16", "2026-09-30", "2026-08-19")
        self.assertTrue(got["known"])
        self.assertTrue(got["crosses"])
        self.assertEqual(got["label"], "crosses earnings")
        self.assertIn("September 30, 2026", got["reason"])
        self.assertIn("no strike is rejected", got["reason"])

    def test_a_report_after_expiry_does_not_cross(self):
        got = IO._crosses_earnings("2026-10-16", "2026-11-05", "2026-08-19")
        self.assertTrue(got["known"])
        self.assertFalse(got["crosses"])
        self.assertEqual(got["label"], "no earnings before expiry")

    def test_no_earnings_date_is_unknown_rather_than_a_clean_bill(self):
        got = IO._crosses_earnings("2026-10-16", None, "2026-08-19")
        self.assertFalse(got["known"])
        self.assertIsNone(got["crosses"])

    def test_a_stale_earnings_date_is_unknown_rather_than_a_clean_bill(self):
        # A "next" report that has already passed means the provider has not
        # updated. The real next date is about a quarter out and unknown, and
        # that is not the same as knowing no report falls inside the contract.
        got = IO._crosses_earnings("2026-10-16", "2026-08-01", "2026-08-19")
        self.assertFalse(got["known"])
        self.assertIsNone(got["crosses"])
        self.assertIn("already passed", got["reason"])

    def test_the_block_carries_it_for_the_chosen_put(self):
        out = IO.best_short_put("TEST", self.chain, SPOT, 95.0, path(), PROBS,
                                {}, market={"earnings_date": "2099-01-01"},
                                today=TODAY)
        self.assertIn("crosses_earnings", out)
        if out.get("best"):
            self.assertTrue(out["crosses_earnings"]["known"])
            self.assertFalse(out["crosses_earnings"]["crosses"])

    def test_crossing_a_report_changes_no_recommendation(self):
        """The rule stated explicitly: this gates nothing.

        The same chain, once with a report inside every contract and once
        with none, has to choose the same strike and reach the same hurdle
        verdict. Only the line on screen differs.
        """
        soon = IO.best_short_put("TEST", self.chain, SPOT, 95.0, path(), PROBS,
                                 {}, market={"earnings_date": TODAY.isoformat()},
                                 today=TODAY)
        never = IO.best_short_put("TEST", self.chain, SPOT, 95.0, path(), PROBS,
                                  {}, market={"earnings_date": "2099-01-01"},
                                  today=TODAY)
        self.assertEqual(soon["available"], never["available"])
        self.assertEqual(soon.get("clears_hurdle"), never.get("clears_hurdle"))
        self.assertEqual(soon.get("headline"), never.get("headline"))
        self.assertEqual((soon.get("best") or {}).get("contract", {}).get("strike"),
                         (never.get("best") or {}).get("contract", {}).get("strike"))
        self.assertEqual(len(soon.get("candidates") or []),
                         len(never.get("candidates") or []))
        # And the only difference is the line itself.
        self.assertNotEqual(soon["crosses_earnings"]["crosses"],
                            never["crosses_earnings"]["crosses"])
