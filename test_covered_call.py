"""Tests for covered_call — the path-dependent lifecycle, its ledger and its
comparison against simply owning the shares.

Five claims this simulator makes in writing are asserted here rather than
trusted:

  1. The ACCOUNTING HAS NO LEAKS. With no calls sold at all, terminal wealth
     equals buy and hold to the cent. This is the single strongest check
     available: every other number in the module flows through the same
     ledger.
  2. A ROLL NEVER ERASES A LOSS. Closing a losing call realizes the loss and
     it stays realized; the credit on the replacement is a separate event.
  3. ASSIGNMENT is a real event with real consequences. The shares are sold
     at the strike, the upside above it is recorded as forfeited, and the
     option that "won" does not hide it.
  4. FAIR-VALUE-AWARE strikes never sell the business cheaply for a premium —
     they only ever raise a strike, never lower one.
  5. HISTORICAL OPTION QUOTES ARE NEVER INVENTED. Where no chain snapshot
     exists, the price is a model output and the run says so.
"""

import os
import unittest
from datetime import date, timedelta

os.environ.setdefault("JERRY_NO_NET", "1")

import covered_call as CC


def bars(prices, start=date(2024, 1, 2)):
    """One bar per weekday, at the given closes."""
    out, d = [], start
    for p in prices:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append({"date": d.isoformat(), "close": float(p),
                    "high": float(p), "low": float(p)})
        d += timedelta(days=1)
    return out


FLAT = bars([100.0] * 200)
RAMP = bars([100.0 + i * 0.5 for i in range(200)])
FALL = bars([100.0 - i * 0.25 for i in range(200)])

POLICY = {"tenor": "MONTHLY", "strike_rule": "PERCENT",
          "roll_rule": "HOLD", "assignment": "END"}


def run(price_bars, policy=None, iv=0.30, **kw):
    kw.setdefault("rate_pct", 0.0)
    return CC.simulate(price_bars, [iv] * len(price_bars),
                       policy or POLICY, **kw)


class TestAccountingHasNoLeaks(unittest.TestCase):
    def test_no_calls_sold_equals_buy_and_hold_to_the_cent(self):
        # A credit floor no option can clear means the run never sells one,
        # so the account can only be the shares.
        r = run(RAMP, cfg={"cc_min_credit": 1e9, "cc_percent_otm": 0.05})
        self.assertEqual(r["calls_sold"], 0)
        self.assertAlmostEqual(r["terminal_wealth"],
                               r["buy_and_hold"]["terminal_wealth"], places=6)
        self.assertAlmostEqual(r["versus_buy_and_hold"], 0.0, places=6)

    def test_dividends_land_identically_in_both_runs(self):
        r = run(FLAT, cfg={"cc_min_credit": 1e9}, dividend_rate_ttm=4.0)
        self.assertGreater(r["dividend_income"], 0)
        self.assertAlmostEqual(r["dividend_income"],
                               r["buy_and_hold"]["dividend_income"], places=9)

    def test_an_ex_dividend_calendar_is_used_when_one_exists(self):
        day = FLAT[10]["date"]
        r = run(FLAT, cfg={"cc_min_credit": 1e9},
                dividends={day: 1.25})
        self.assertAlmostEqual(r["dividend_income"], 125.0, places=6)
        self.assertIn("actual ex-dividend dates", r["dividend_basis"])
        paid = [e for e in r["ledger"] if e["kind"] == "DIVIDEND"]
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid[0]["date"], day)

    def test_without_a_calendar_the_even_accrual_says_it_is_even(self):
        r = run(FLAT, cfg={"cc_min_credit": 1e9}, dividend_rate_ttm=4.0)
        self.assertIn("accrued evenly", r["dividend_basis"])
        self.assertIn("individual dates are not", r["dividend_basis"])

    def test_no_dividend_on_file_credits_nothing_and_says_so(self):
        r = run(FLAT, cfg={"cc_min_credit": 1e9})
        self.assertEqual(r["dividend_income"], 0.0)
        self.assertIn("none is credited", r["dividend_basis"])

    def test_cash_interest_accrues_on_cash_only(self):
        r = run(FLAT, cfg={"cc_min_credit": 1e9}, rate_pct=5.0)
        # No calls sold and no assignment, so there is never any cash.
        self.assertAlmostEqual(r["cash_interest"], 0.0, places=9)


class TestAssignment(unittest.TestCase):
    def setUp(self):
        self.r = run(RAMP, cfg={"cc_percent_otm": 0.05})

    def test_the_shares_are_sold_at_the_strike(self):
        events = [e for e in self.r["ledger"] if e["kind"] == "ASSIGNED"]
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertAlmostEqual(e["proceeds"], e["strike"] * 100.0)
        self.assertEqual(self.r["shares_at_end"], 0)

    def test_forfeited_upside_is_recorded_and_is_not_hidden_in_the_option(self):
        e = next(e for e in self.r["ledger"] if e["kind"] == "ASSIGNED")
        self.assertGreater(self.r["upside_forfeited"], 0)
        self.assertAlmostEqual(
            self.r["upside_forfeited"],
            (e["share_price"] - e["strike"]) * 100.0, places=6)
        # The option itself is recorded as a winner — it kept the whole
        # credit — which is exactly why the forfeited upside is a separate
        # line rather than netted into it.
        trade = next(t for t in self.r["trades"] if t["outcome"] == "ASSIGNED")
        self.assertTrue(trade["won"])
        self.assertAlmostEqual(trade["paid_per_share"], 0.0)

    def test_share_profit_is_capped_at_the_strike(self):
        e = next(e for e in self.r["ledger"] if e["kind"] == "ASSIGNED")
        self.assertAlmostEqual(self.r["share_pnl"],
                               (e["strike"] - 100.0) * 100.0, places=6)

    def test_ending_the_position_leaves_cash_and_stops(self):
        self.assertEqual(self.r["shares_at_end"], 0)
        self.assertGreater(self.r["cash_at_end"], 0)
        self.assertEqual(len([e for e in self.r["ledger"]
                              if e["kind"] == "BOUGHT"]), 0)

    def test_a_capped_rally_loses_to_owning_the_shares(self):
        self.assertLess(self.r["terminal_wealth"],
                        self.r["buy_and_hold"]["terminal_wealth"])
        # And the option win rate says the opposite, which is the trap the
        # note beside it exists to name.
        self.assertEqual(self.r["call_win_rate_pct"], 100.0)
        self.assertIn("NOT a measure of whether the strategy worked",
                      self.r["call_win_rate_note"])


class TestReentry(unittest.TestCase):
    def test_shares_are_repurchased_only_at_or_below_the_buy_zone(self):
        zone = {b["date"]: {"buy_zone": 105.0} for b in RAMP}
        r = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                       "roll_rule": "HOLD", "assignment": "RE_ENTER"},
                cfg={"cc_percent_otm": 0.05}, fair_value_by_day=zone)
        # The ramp never comes back below 105 after the assignment, so the
        # shares are never bought back.
        self.assertEqual(len([e for e in r["ledger"] if e["kind"] == "BOUGHT"]), 0)
        self.assertEqual(r["shares_at_end"], 0)

    def test_a_falling_price_below_the_buy_zone_is_repurchased(self):
        prices = [100.0 + i * 0.5 for i in range(40)] + \
                 [120.0 - i * 1.0 for i in range(60)]
        b = bars(prices)
        zone = {x["date"]: {"buy_zone": 90.0} for x in b}
        r = run(b, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                    "roll_rule": "HOLD", "assignment": "RE_ENTER"},
                cfg={"cc_percent_otm": 0.05}, fair_value_by_day=zone)
        bought = [e for e in r["ledger"] if e["kind"] == "BOUGHT"]
        self.assertTrue(bought)
        for e in bought:
            self.assertLessEqual(e["price"], 90.0)
            self.assertIn("buy zone", e["reason"])

    def test_unconditional_reentry_buys_back_regardless(self):
        # A market that dips after the assignment, so the repurchase is
        # affordable and the rule's indifference to price is what shows.
        prices = [100.0 + i * 0.5 for i in range(40)] + \
                 [118.0 - i * 0.2 for i in range(60)]
        b = bars(prices)
        r = run(b, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                    "roll_rule": "HOLD", "assignment": "RE_ENTER_ALWAYS"},
                cfg={"cc_percent_otm": 0.05})
        bought = [e for e in r["ledger"] if e["kind"] == "BOUGHT"]
        self.assertTrue(bought)
        self.assertIn("unconditional", bought[0]["reason"])

    def test_a_repurchase_the_account_cannot_afford_is_recorded(self):
        # Assigned at a strike well below where the price ran to, so the
        # proceeds no longer buy a hundred shares back. Sitting in cash from
        # then on is arithmetic, not a decision, and the ledger says so.
        r = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                       "roll_rule": "HOLD", "assignment": "RE_ENTER_ALWAYS"},
                cfg={"cc_percent_otm": 0.05})
        self.assertTrue(r["could_not_buy_back"])
        blocked = [e for e in r["ledger"] if e["kind"] == "COULD NOT BUY BACK"]
        self.assertEqual(len(blocked), 1)     # recorded once, not every day
        self.assertIn("without adding money", blocked[0]["reason"])
        self.assertGreater(blocked[0]["needed"], blocked[0]["cash"])


class TestRolling(unittest.TestCase):
    def setUp(self):
        self.r = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                            "roll_rule": "DELTA", "assignment": "END"},
                     cfg={"cc_percent_otm": 0.05, "cc_roll_delta": 0.65})

    def test_a_roll_records_both_legs(self):
        closed = [e for e in self.r["ledger"] if e["kind"] == "CLOSED"]
        sold = [e for e in self.r["ledger"] if e["kind"] == "SOLD"]
        self.assertTrue(closed)
        # Every close is followed by a new sale, so there are at least as
        # many sales as closes.
        self.assertGreaterEqual(len(sold), len(closed))

    def test_a_roll_never_erases_the_loss_on_the_closed_leg(self):
        losers = [t for t in self.r["trades"]
                  if t["outcome"] == "CLOSED" and t["realized"] < 0]
        self.assertTrue(losers)
        # The realized losses are still in the ledger, and the net premium
        # is credit received less what it cost to get out.
        self.assertAlmostEqual(
            self.r["premium_net"],
            self.r["premium_income"] - self.r["option_close_cost"], places=6)
        self.assertLess(self.r["premium_net"], self.r["premium_income"])

    def test_the_close_cost_is_money_actually_paid(self):
        self.assertGreater(self.r["option_close_cost"], 0)
        paid = sum(e["cost"] for e in self.r["ledger"] if e["kind"] == "CLOSED")
        self.assertAlmostEqual(self.r["option_close_cost"], paid, places=6)

    def test_rolling_near_expiration_avoids_assignment(self):
        r = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                       "roll_rule": "DTE", "assignment": "END"},
                cfg={"cc_percent_otm": 0.05, "cc_roll_dte": 5})
        self.assertEqual(r["calls_assigned"], 0)
        self.assertGreater(r["calls_rolled_or_closed"], 0)
        self.assertEqual(r["roll_rate_pct"], 100.0)

    def test_holding_to_expiry_never_rolls(self):
        r = run(RAMP, cfg={"cc_percent_otm": 0.05})
        self.assertEqual(r["calls_rolled_or_closed"], 0)

    def test_rolling_only_for_credit_skips_a_roll_that_would_cost(self):
        base = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                          "roll_rule": "DELTA", "assignment": "END"},
                   cfg={"cc_percent_otm": 0.05, "cc_roll_delta": 0.65})
        strict = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                            "roll_rule": "DELTA", "assignment": "END"},
                     cfg={"cc_percent_otm": 0.05, "cc_roll_delta": 0.65,
                          "cc_roll_only_for_credit": True})
        self.assertLess(strict["calls_rolled_or_closed"],
                        base["calls_rolled_or_closed"])

    def test_rolling_when_the_strike_is_below_fair_value(self):
        fv = {b["date"]: {"base": 130.0} for b in RAMP}
        r = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                       "roll_rule": "BELOW_FAIR_VALUE", "assignment": "END"},
                cfg={"cc_percent_otm": 0.02}, fair_value_by_day=fv)
        closed = [e for e in r["ledger"] if e["kind"] == "CLOSED"]
        self.assertTrue(closed)
        self.assertIn("below the", closed[0]["reason"])


class TestStrikeRules(unittest.TestCase):
    def test_the_fair_value_rule_only_ever_raises_a_strike(self):
        low = CC.pick_strike("DELTA", 100.0, 30, 0.3, 4.0, cfg={})
        high = CC.pick_strike("FAIR_VALUE", 100.0, 30, 0.3, 4.0,
                              fair_base=140.0, cfg={})
        self.assertGreaterEqual(high["strike"], low["strike"])
        self.assertTrue(high["raised_by_fair_value"])

    def test_a_fair_value_below_the_delta_strike_leaves_it_alone(self):
        plain = CC.pick_strike("DELTA", 100.0, 30, 0.3, 4.0, cfg={})
        with_fv = CC.pick_strike("FAIR_VALUE", 100.0, 30, 0.3, 4.0,
                                 fair_base=60.0, cfg={})
        self.assertEqual(plain["strike"], with_fv["strike"])
        self.assertFalse(with_fv["raised_by_fair_value"])

    def test_the_buy_zone_rule_uses_the_credited_value(self):
        got = CC.pick_strike("BUY_ZONE", 100.0, 30, 0.3, 4.0,
                             fair_credited=150.0, cfg={})
        self.assertGreaterEqual(got["strike"], 150.0)
        self.assertIn("credited fair value", got["reason"])

    def test_the_percent_rule_is_a_distance_above_the_price(self):
        got = CC.pick_strike("PERCENT", 100.0, 30, 0.3, 4.0,
                             cfg={"cc_percent_otm": 0.10})
        self.assertGreaterEqual(got["strike"], 105.0)
        self.assertIn("above the share price", got["reason"])

    def test_a_minimum_distance_raises_a_too_close_strike(self):
        got = CC.pick_strike("PERCENT", 100.0, 30, 0.3, 4.0,
                             cfg={"cc_percent_otm": 0.0,
                                  "cc_min_otm_pct": 0.08})
        self.assertGreaterEqual(got["strike"], 108.0)
        self.assertIn("minimum distance", got["reason"])

    def test_no_universal_delta_is_declared_best(self):
        # The default is a SETTING with a note saying so, not a claim.
        self.assertIn("No delta is treated as correct",
                      CC.STRIKE_RULES["DELTA"]["note"])
        self.assertIn("cc_delta_target", CC.DEFAULTS)

    def test_a_fair_value_aware_run_sells_fewer_and_further_out(self):
        fv = {b["date"]: {"base": 140.0, "credited": 130.0} for b in RAMP}
        plain = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                           "roll_rule": "HOLD", "assignment": "RE_ENTER"},
                    cfg={"cc_percent_otm": 0.02}, fair_value_by_day=fv)
        aware = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "FAIR_VALUE",
                           "roll_rule": "HOLD", "assignment": "RE_ENTER"},
                    cfg={"cc_percent_otm": 0.02}, fair_value_by_day=fv)
        self.assertLessEqual(aware["calls_assigned"], plain["calls_assigned"])
        self.assertLessEqual(aware["upside_forfeited"],
                             plain["upside_forfeited"])


class TestFillBasis(unittest.TestCase):
    def test_with_no_chain_store_every_fill_is_a_labelled_model_value(self):
        r = run(RAMP, cfg={"cc_percent_otm": 0.05})
        self.assertEqual(r["fill_basis"], CC.BASIS_MODEL)
        self.assertEqual(r["real_fill_pct"], 0.0)
        self.assertIn("never invented", r["fill_note"])
        for e in r["ledger"]:
            if e["kind"] in ("SOLD", "CLOSED"):
                self.assertEqual(e["source"], CC.MODEL)

    def test_a_real_snapshot_is_used_and_labelled(self):
        day = RAMP[0]["date"]
        exp = CC.pick_expiry(day, 38)
        store = {day: {"spot": 100.0,
                       "exps": {exp: {"c": [[105.0, 2.0, 2.2, 0.3, 0.3, 500]],
                                      "p": []}}}}
        r = run(RAMP, cfg={"cc_percent_otm": 0.05}, sym_store=store)
        sold = [e for e in r["ledger"] if e["kind"] == "SOLD"]
        self.assertEqual(sold[0]["source"], CC.REAL)
        self.assertEqual(r["real_fill_pct"], 100.0)
        # The one and only fill came from a stored chain, so the whole run
        # is a real backtest and is allowed to say so.
        self.assertEqual(r["fill_basis"], CC.BASIS_REAL)
        self.assertIn("recorded by this app", r["fill_note"])

    def test_a_run_with_some_stored_days_is_labelled_part_model(self):
        day = RAMP[0]["date"]
        exp = CC.pick_expiry(day, 38)
        store = {day: {"spot": 100.0,
                       "exps": {exp: {"c": [[105.0, 2.0, 2.2, 0.3, 0.3, 500]],
                                      "p": []}}}}
        r = run(RAMP, {"tenor": "MONTHLY", "strike_rule": "PERCENT",
                       "roll_rule": "DTE", "assignment": "RE_ENTER_ALWAYS"},
                cfg={"cc_percent_otm": 0.05, "cc_roll_dte": 5},
                sym_store=store)
        self.assertEqual(r["fill_basis"], CC.BASIS_MIXED)
        self.assertGreater(r["real_fill_pct"], 0.0)
        self.assertLess(r["real_fill_pct"], 100.0)

    def test_quote_falls_back_to_the_model_when_nothing_is_stored(self):
        q = CC.quote({}, "2024-01-02", 105.0, "2024-02-16", 100.0, 0.3, 4.0)
        self.assertEqual(q["source"], CC.MODEL)
        self.assertGreater(q["price"], 0.0)


class TestComparison(unittest.TestCase):
    def test_policies_are_ranked_and_none_is_called_universally_best(self):
        out = CC.compare_policies(FALL, [0.3] * len(FALL),
                                  CC.default_policies(), rate_pct=0.0)
        self.assertTrue(out["available"])
        values = [r["terminal_wealth"] for r in out["rows"]]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertIn("not evidence that any of these rules works in general",
                      out["verdict_note"])

    def test_every_policy_label_names_all_four_choices(self):
        for p in CC.default_policies():
            label = CC.policy_label(p)
            self.assertEqual(label.count("·"), 3, label)
            self.assertNotIn("?", label)

    def test_selling_calls_beats_owning_in_a_falling_market(self):
        r = run(FALL, cfg={"cc_percent_otm": 0.05})
        self.assertGreater(r["terminal_wealth"],
                           r["buy_and_hold"]["terminal_wealth"])

    def test_the_comparison_uses_identical_starting_capital(self):
        r = run(RAMP, cfg={"cc_percent_otm": 0.05})
        self.assertAlmostEqual(r["starting_capital"],
                               r["buy_and_hold"]["starting_capital"])
        self.assertAlmostEqual(r["starting_capital"], 100.0 * 100.0)

    def test_premium_is_measured_against_the_full_share_notional(self):
        r = run(FLAT, cfg={"cc_percent_otm": 0.05})
        self.assertIsNotNone(r["average_premium_pct_of_notional"])
        self.assertIn("FULL value of the hundred shares",
                      r["average_premium_note"])

    def test_too_little_history_refuses(self):
        short = bars([100.0] * 10)
        r = CC.simulate(short, [0.3] * 10, POLICY)
        self.assertFalse(r["available"])
        self.assertIn("needs a price history", r["reason"])

    def test_a_run_with_no_workable_policy_says_so(self):
        out = CC.compare_policies(bars([100.0] * 5), [0.3] * 5,
                                  CC.default_policies())
        self.assertFalse(out["available"])


class TestExpirySelection(unittest.TestCase):
    def test_a_listed_expiration_near_the_target_is_preferred(self):
        got = CC.pick_expiry("2024-01-02", 30,
                             ["2024-01-05", "2024-02-02", "2024-06-21"])
        self.assertEqual(got, "2024-02-02")

    def test_nothing_close_enough_returns_nothing(self):
        self.assertIsNone(CC.pick_expiry("2024-01-02", 30, ["2025-06-20"]))

    def test_without_a_calendar_it_lands_on_a_friday(self):
        got = CC.pick_expiry("2024-01-02", 30)
        self.assertEqual(date.fromisoformat(got).weekday(), 4)


if __name__ == "__main__":                            # pragma: no cover
    unittest.main()
