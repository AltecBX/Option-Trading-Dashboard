"""test_bt_plans.py (B5) — plan creation, checklist derivation, MC-based
sizing, persistence round-trip, and journal adherence math.
Run:  python3 -m unittest test_bt_plans
"""
import tempfile
import unittest

import bt_plans as bp


RESULT = {
    "structure": "short_put",
    "rules": {
        "instrument": "option",
        "entry": [{"type": "rsi", "period": 14, "op": "<=", "value": 40},
                  {"type": "market_regime", "regime": "uptrend"}],
        "earnings_filter": {"mode": "skip", "window": 5},
        "options": {"structure": "short_put", "dte": 45, "target_delta": 0.30,
                    "management": {"profit_take_pct": 50, "stop_x_credit": 2.0,
                                   "exit_dte": 21}},
    },
    "metrics": {"n_trades": 42, "win_rate": 78.6, "total_pnl": 6100.0,
                "max_drawdown_pct": 6.2, "sharpe": 1.4, "start_equity": 100000,
                "avg_return_on_bp_pct": 2.1},
    "monte_carlo": {"max_dd_pct": {"p5": 2.0, "p50": 5.0, "p95": 10.0}},
    "walk_forward": {"wf_efficiency": 0.8, "verdict": "consistent out of sample"},
    "deflated_sharpe": {"dsr": 0.96, "verdict": "statistically real after accounting for trials"},
    "sensitivity": {"verdict": "edge survives the full premium-assumption band (0.85×–1.15× IV)"},
}


class TestPlans(unittest.TestCase):
    def setUp(self):
        bp.configure(tempfile.mkdtemp(prefix="jerry_plans_"))

    def test_checklist_derivation(self):
        cl = bp.build_checklist(RESULT["rules"])
        joined = " | ".join(cl)
        self.assertIn("short put at ~30Δ, ~45 DTE", joined)
        self.assertIn("RSI(14) is ≤ 40", joined)
        self.assertIn("uptrend", joined)
        self.assertIn("No earnings inside the window", joined)
        self.assertIn("Take profit at 50% of the credit", joined)
        self.assertIn("Stop: buy back at 2.0× the credit", joined)
        self.assertIn("Exit by 21 DTE", joined)

    def test_sizing_from_mc(self):
        s = bp.sizing_guidance(RESULT, account_size=80_000)
        # P95 DD 10% at tested sizing → full allocation would breach 15%
        # only above 1.5×; fraction = min(1, 15/10) = 1.0.
        self.assertEqual(s["suggested_capital_fraction"], 1.0)
        deep = dict(RESULT, monte_carlo={"max_dd_pct": {"p95": 60.0}})
        s2 = bp.sizing_guidance(deep, account_size=80_000)
        self.assertEqual(s2["suggested_capital_fraction"], 0.25)
        self.assertEqual(s2["suggested_allocation"], 20_000)

    def test_create_list_archive_roundtrip(self):
        p = bp.create_plan(RESULT, rules_text="sell a 30 delta put …")
        self.assertEqual(p["status"], "active")
        self.assertIn("not automation", p["not_automation"].lower())
        self.assertEqual(p["evidence"]["dsr"], 0.96)
        plans = bp.list_plans()
        self.assertEqual(len(plans), 1)
        self.assertTrue(bp.set_status(p["id"], "archived"))
        self.assertEqual(bp.list_plans()[0]["status"], "archived")
        self.assertFalse(bp.set_status("nope", "archived"))
        self.assertFalse(bp.set_status(p["id"], "bogus"))

    def test_adherence_split(self):
        p = bp.create_plan(RESULT)
        journal = [
            # plan-tagged short-premium winners: sold 1.50 closed 0.40 ×(−2)
            {"plan_id": p["id"], "entry_premium": 1.50, "closed_premium": 0.40, "qty": -2},
            {"plan_id": p["id"], "entry_premium": 1.00, "closed_premium": 1.80, "qty": -1},
            # off-plan long winner
            {"entry_premium": 2.00, "closed_premium": 3.00, "qty": 1},
            # open trade (no closed premium) — ignored
            {"plan_id": p["id"], "entry_premium": 1.20, "qty": -1},
        ]
        a = bp.adherence(journal)
        self.assertEqual(len(a["plans"]), 1)
        row = a["plans"][0]
        self.assertEqual(row["n"], 2)
        self.assertAlmostEqual(row["pnl"], 220 - 80, places=2)
        self.assertEqual(row["win_rate"], 50.0)
        self.assertEqual(a["off_plan"]["n"], 1)
        self.assertAlmostEqual(a["off_plan"]["pnl"], 100.0, places=2)


if __name__ == "__main__":
    unittest.main()
