"""test_bt_options.py (B1) — the options lifecycle engine on deterministic
synthetic fixtures. Every structure, every management path, assignment,
dividends, expiry settlement, buying power, and the no-look-ahead fill.
No network, no randomness.  Run:  python3 -m unittest test_bt_options
"""
import unittest
from datetime import date, timedelta

from metrics import _bs_price, year_fraction
import bt_options as bo


def mk_bars(start="2026-01-02", closes=None, oh=0.0, ol=0.0):
    """Daily bars on consecutive calendar days. oh/ol pad high/low around
    the close so intra-bar checks have room; open = previous close."""
    d0 = date.fromisoformat(start)
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append({"date": (d0 + timedelta(days=i)).isoformat(),
                    "open": prev, "high": max(prev, c) + oh,
                    "low": min(prev, c) - ol, "close": c})
        prev = c
    return out


def flat_bars(n, px=100.0, **kw):
    return mk_bars(closes=[px] * n, **kw)


IV = 0.25
MGMT_NONE = {"hold_to_expiry": True}


class TestBuilders(unittest.TestCase):
    def test_strike_by_delta_sides(self):
        kp = bo.strike_by_delta(100, IV, 45, 0.30, "put")
        kc = bo.strike_by_delta(100, IV, 45, 0.30, "call")
        self.assertLess(kp, 100)
        self.assertGreater(kc, 100)
        # returned strike's delta really is ~0.30 (within snap increment)
        from metrics import _bs_delta
        self.assertAlmostEqual(abs(_bs_delta(100, kp, year_fraction(45), IV, "put")),
                               0.30, delta=0.06)

    def test_structures_shape(self):
        s = bo.build_structure("short_put", 100, IV, 45, {})
        self.assertEqual(len(s["legs"]), 1)
        self.assertEqual(s["legs"][0]["qty"], -1)
        self.assertFalse(s["defined_risk"])

        st = bo.build_structure("short_strangle", 100, IV, 45, {})
        rights = sorted(L["right"] for L in st["legs"])
        self.assertEqual(rights, ["call", "put"])
        self.assertTrue(all(L["qty"] == -1 for L in st["legs"]))

        ic = bo.build_structure("iron_condor", 100, IV, 45, {})
        self.assertEqual(len(ic["legs"]), 4)
        self.assertTrue(ic["defined_risk"])
        self.assertGreater(ic["width"], 0)

        ps = bo.build_structure("put_credit_spread", 100, IV, 45, {})
        ks = sorted(L["strike"] for L in ps["legs"])
        short = next(L for L in ps["legs"] if L["qty"] == -1)
        self.assertEqual(short["strike"], ks[1])          # short the higher put

        cc = bo.build_structure("covered_call", 100, IV, 45, {})
        self.assertEqual(cc["stock"], 100)

        fly = bo.build_structure("iron_fly", 100, IV, 45, {})
        shorts = [L for L in fly["legs"] if L["qty"] == -1]
        self.assertEqual(shorts[0]["strike"], shorts[1]["strike"])  # ATM body

    def test_bp_formulas(self):
        legs = [{"right": "put", "strike": 95, "qty": -1}]
        self.assertEqual(bo.bp_requirement("short_put", legs, 100, 1.5, None), 9500)
        legs2 = [{"right": "put", "strike": 95, "qty": -1},
                 {"right": "put", "strike": 90, "qty": 1}]
        self.assertEqual(bo.bp_requirement("put_credit_spread", legs2, 100, 1.2, 5.0),
                         (5.0 - 1.2) * 100)

    def test_spread_model_monotonic(self):
        # near-dated wider than far-dated; floor respected; capped at 50%.
        self.assertGreater(bo.option_spread(1.0, 100, 100, 1),
                           bo.option_spread(1.0, 100, 100, 45))
        self.assertEqual(bo.option_spread(0.03, 100, 100, 45), 0.02)
        self.assertLessEqual(bo.option_spread(10.0, 100, 60, 0.5), 5.0)


class TestShortPutLifecycle(unittest.TestCase):
    def test_expires_worthless_flat_market(self):
        bars = flat_bars(60)
        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60,
                                 MGMT_NONE, params={"dte": 45})
        self.assertIsNotNone(t)
        self.assertEqual(t["reason"], "expired")
        self.assertTrue(t["is_credit"])
        # Full credit kept minus open costs (no closing legs at expiry).
        want = t["credit"] * 100 - bo.leg_costs(1, 1)
        self.assertAlmostEqual(t["pnl"], want, places=2)
        self.assertEqual(t["priced"], "modeled")

    def test_no_lookahead_fill(self):
        # Entry price must come from bars[i+1].open, not bar i.
        closes = [100.0] * 5 + [130.0] * 55        # jump AFTER signal bar 4
        bars = mk_bars(closes=closes)
        t = bo.simulate_position(bars, 4, "short_put", [IV] * 60,
                                 MGMT_NONE, params={"dte": 45})
        # Fill bar open = close of bar 4 = 100 → strikes near 100, far OTM
        # of the 130 market forever → expires worthless.
        self.assertEqual(t["entry_date"], bars[5]["date"])
        self.assertLess(t["legs"][0]["strike"], 101)

    def test_profit_take_50(self):
        # Rally away from the put → cost-to-close collapses → PT triggers.
        closes = [100.0, 100.0] + [112.0] * 58
        bars = mk_bars(closes=closes)
        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60,
                                 {"profit_take_pct": 50}, params={"dte": 45})
        self.assertEqual(t["reason"], "target")
        # P/L ≈ 50% of credit minus exit spread & fees — must be positive
        # and below the full-credit amount.
        self.assertGreater(t["pnl"], 0)
        self.assertLess(t["pnl"], t["credit"] * 100)
        self.assertTrue(any(e["type"] == "profit_take" for e in t["events"]))

    def test_stop_2x_credit(self):
        # Crash through the strike → buy-back at 2× credit.
        closes = [100.0, 100.0, 100.0] + [80.0] * 57
        bars = mk_bars(closes=closes)
        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60,
                                 {"stop_x_credit": 2.0}, params={"dte": 45})
        self.assertEqual(t["reason"], "stop")
        # Loss ≈ 1× credit (paid 2×, received 1×) plus spread & fees.
        approx = -(t["credit"] * 100)
        self.assertLess(t["pnl"], approx * 0.5)          # a real loss
        self.assertGreater(t["pnl"], approx * 2.5)       # bounded, not runaway

    def test_dte_exit(self):
        bars = flat_bars(60)
        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60,
                                 {"exit_dte": 21}, params={"dte": 45})
        self.assertEqual(t["reason"], "dte_exit")
        d_exit = date.fromisoformat(t["exit_date"])
        d_exp = date.fromisoformat(t["expiry"])
        self.assertLessEqual((d_exp - d_exit).days, 21)
        self.assertGreater(t["pnl"], 0)   # theta collected in a flat market

    def test_deep_itm_assignment(self):
        # Collapse far through the strike → extrinsic dies → assigned.
        closes = [100.0, 100.0, 100.0] + [60.0] * 57
        bars = mk_bars(closes=closes)
        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60,
                                 MGMT_NONE, params={"dte": 45})
        self.assertEqual(t["reason"], "assigned_shares")
        self.assertIn("assigned_leg", t)
        self.assertLess(t["pnl"], 0)      # bought stock at strike, worth 60
        self.assertTrue(any(e["type"] == "assigned" for e in t["events"]))

    def test_expiry_itm_settlement(self):
        # Ends modestly ITM without dying extrinsic → settles at intrinsic.
        closes = [100.0] * 40 + [93.0] * 20
        bars = mk_bars(closes=closes)
        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60,
                                 MGMT_NONE, params={"dte": 45})
        if t["reason"] == "expired":
            k = t["legs"][0]["strike"]
            intrinsic = max(0.0, k - 93.0)
            want = (t["credit"] - intrinsic) * 100 - bo.leg_costs(1, 1)
            self.assertAlmostEqual(t["pnl"], want, places=2)
        else:
            self.assertEqual(t["reason"], "assigned_shares")


class TestCoveredCall(unittest.TestCase):
    def test_called_away_above_strike(self):
        closes = [100.0] * 30 + [120.0] * 30
        bars = mk_bars(closes=closes)
        t = bo.simulate_position(bars, 0, "covered_call", [IV] * 60,
                                 MGMT_NONE, params={"dte": 45})
        self.assertIn(t["reason"], ("called_away",))
        k = t["legs"][0]["strike"]
        # Stock gain capped at the strike + full credit kept.
        self.assertGreater(t["pnl"], 0)
        self.assertLessEqual(t["pnl"],
                             ((k - 100.0) + t["credit"]) * 100)

    def test_expires_otm_shares_retained(self):
        bars = flat_bars(60, px=100.0)
        t = bo.simulate_position(bars, 0, "covered_call", [IV] * 60,
                                 MGMT_NONE, params={"dte": 45})
        self.assertEqual(t["reason"], "expired")
        self.assertTrue(t.get("stock_retained"))
        want = t["credit"] * 100 - bo.leg_costs(1, 1)   # flat stock: option P/L only
        self.assertAlmostEqual(t["pnl"], want, places=2)

    def test_exdiv_early_assignment(self):
        # Deep ITM short call + ex-div where dividend > extrinsic → assigned
        # BEFORE expiry.
        closes = [100.0, 100.0, 100.0] + [118.0] * 57
        bars = mk_bars(closes=closes)
        exdiv = bars[10]["date"]
        t = bo.simulate_position(bars, 0, "covered_call", [IV] * 60,
                                 MGMT_NONE, params={"dte": 45, "target_delta": 0.45},
                                 dividends=[(exdiv, 2.50)])
        self.assertEqual(t["reason"], "called_away")
        self.assertLessEqual(t["exit_date"], exdiv)
        self.assertTrue(any("ex-div" in e.get("detail", "") for e in t["events"]
                            if e["type"] == "assigned"))


class TestDefinedRisk(unittest.TestCase):
    def test_condor_max_loss_bounded_by_width(self):
        # Violent crash: condor loss can never exceed (width − credit).
        closes = [100.0, 100.0, 100.0] + [50.0] * 57
        bars = mk_bars(closes=closes)
        t = bo.simulate_position(bars, 0, "iron_condor", [IV] * 60,
                                 MGMT_NONE, params={"dte": 45})
        self.assertIsNotNone(t)
        max_loss = (t["bp"])          # defined risk: BP = width − credit
        self.assertGreaterEqual(t["pnl"], -max_loss - bo.leg_costs(4, 1) * 2 - 50)

    def test_condor_profit_take(self):
        bars = flat_bars(60)
        t = bo.simulate_position(bars, 0, "iron_condor", [IV] * 60,
                                 {"profit_take_pct": 50}, params={"dte": 45})
        self.assertEqual(t["reason"], "target")
        self.assertGreater(t["pnl"], 0)

    def test_long_call_profit_take(self):
        closes = [100.0, 100.0] + [111.0] * 58
        bars = mk_bars(closes=closes)
        t = bo.simulate_position(bars, 0, "long_call", [IV] * 60,
                                 {"profit_take_pct": 100},
                                 params={"dte": 45, "strike": {"mode": "atm"}})
        self.assertEqual(t["reason"], "target")
        self.assertFalse(t["is_credit"])
        self.assertGreater(t["pnl"], 0)


class TestRoll(unittest.TestCase):
    def test_roll_signal(self):
        bars = flat_bars(60)
        t = bo.simulate_position(bars, 0, "short_put", [IV] * 60,
                                 {"roll_dte": 21}, params={"dte": 45})
        self.assertEqual(t["reason"], "roll")
        self.assertIn("roll_signal_i", t)
        self.assertGreater(t["roll_signal_i"], 0)
        # Rolled with time left, having collected some theta.
        self.assertGreater(t["pnl"], 0)


class TestAudit(unittest.TestCase):
    def test_events_and_marks_present(self):
        bars = flat_bars(60)
        t = bo.simulate_position(bars, 0, "short_strangle", [IV] * 60,
                                 {"profit_take_pct": 50}, params={"dte": 45})
        self.assertEqual(t["events"][0]["type"], "open")
        self.assertGreater(len(t["marks"]), 3)          # daily audit trail
        self.assertIsNotNone(t["pnl_on_bp"])
        self.assertEqual(t["dte"], 45)




class TestChains(unittest.TestCase):
    def test_roll_chain_links(self):
        bars = flat_bars(140)
        chain = bo.simulate_chain(bars, 0, "short_put", [IV] * 140,
                                  {"roll_dte": 21}, params={"dte": 45})
        self.assertGreaterEqual(len(chain), 2)          # rolled at least once
        self.assertTrue(all(t["chain_id"] == chain[0]["chain_id"] for t in chain))
        self.assertEqual([t["chain_seq"] for t in chain], list(range(len(chain))))
        # links are chronological and non-overlapping
        for a, b in zip(chain, chain[1:]):
            self.assertLessEqual(a["exit_date"], b["entry_date"])

    def test_wheel_assign_then_covered_calls(self):
        # Crash → put assigned; flat after → covered calls until expiry.
        closes = [100.0] * 3 + [70.0] * 137
        bars = mk_bars(closes=closes)
        chain = bo.simulate_chain(bars, 0, "wheel", [IV] * 140,
                                  MGMT_NONE, params={"dte": 30})
        self.assertGreaterEqual(len(chain), 2)
        self.assertEqual(chain[0]["structure"], "short_put")
        self.assertEqual(chain[0]["reason"], "assigned_shares")
        self.assertEqual(chain[1]["structure"], "covered_call")
        # CC links carry the put-strike basis, so their stock P/L is
        # measured from the assignment price, not the later spot.
        self.assertGreater(chain[0]["legs"][0]["strike"], 90)


class TestPortfolio(unittest.TestCase):
    def _mk(self, n_syms=3, n=90):
        bars_by = {}
        iv_by = {}
        for s in range(n_syms):
            bars_by[f"S{s}"] = flat_bars(n, px=100.0 + s)
            iv_by[f"S{s}"] = [IV] * n
        return bars_by, iv_by

    def test_bp_gating(self):
        bars_by, iv_by = self._mk(n_syms=6)
        sigs = [(bars_by[f"S{s}"][0]["date"], f"S{s}", 0) for s in range(6)]
        # Equity only fits ~2 cash-secured puts (~$9.5k BP each).
        res = bo.run_portfolio(sigs, bars_by, iv_by, "short_put", MGMT_NONE,
                               params={"dte": 45}, start_equity=20_000,
                               budget_per_trade=10_000, max_positions=10)
        opened = {t["symbol"] for t in res["trades"]}
        self.assertLessEqual(len(opened), 2)
        self.assertGreater(res["skipped_bp"], 0)

    def test_max_positions_gating(self):
        bars_by, iv_by = self._mk(n_syms=5)
        sigs = [(bars_by[f"S{s}"][0]["date"], f"S{s}", 0) for s in range(5)]
        res = bo.run_portfolio(sigs, bars_by, iv_by, "short_put", MGMT_NONE,
                               params={"dte": 45}, start_equity=1_000_000,
                               budget_per_trade=10_000, max_positions=2)
        self.assertLessEqual(len({t["symbol"] for t in res["trades"]}), 2)
        self.assertGreater(res["skipped_max_positions"], 0)

    def test_equity_curve_marks_open_positions(self):
        bars_by, iv_by = self._mk(n_syms=1)
        sigs = [(bars_by["S0"][0]["date"], "S0", 0)]
        res = bo.run_portfolio(sigs, bars_by, iv_by, "short_put", MGMT_NONE,
                               params={"dte": 45}, start_equity=50_000)
        curve = res["equity_curve"]
        self.assertGreater(len(curve), 10)              # daily marks, not just exits
        # Flat market short put: equity grinds UP with theta and ends at
        # start + realized.
        self.assertGreaterEqual(curve[-1]["equity"], curve[0]["equity"])
        final = 50_000 + sum(t["pnl"] for t in res["trades"])
        self.assertAlmostEqual(curve[-1]["equity"], final, places=2)


if __name__ == "__main__":
    unittest.main()
