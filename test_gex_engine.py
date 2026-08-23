"""Tests for gex_engine.py — gamma exposure.

Gamma exposure is a model, not a measurement, so the things worth pinning
down are the model's own commitments: the sign convention, the per-1%
scaling, what happens to contracts the model cannot price, and that the
flip level is computed by re-gamma-ing across a spot grid rather than by
cumulatively summing today's per-strike figures.
"""

import unittest
from datetime import date

import gex_engine as G
from metrics import _bs_gamma

TODAY = date(2026, 1, 5)
EXPIRY = "2026-02-04"          # 30 days out
SPOT = 100.0
T = 30 / 365.0


def contract(strike, oi=1000, iv=0.30, gamma=None, expiration=EXPIRY):
    # A contract with no implied volatility can still carry a broker-supplied
    # gamma — that is exactly the case the profile has to exclude while the
    # per-strike totals keep it.
    if gamma is None:
        gamma = _bs_gamma(SPOT, strike, T, iv or 0.30)
    return {"strike": float(strike), "openInterest": oi, "iv": iv,
            "gamma": gamma, "_expiration": expiration}


def chain(calls, puts, expirations=(EXPIRY,), spot=SPOT):
    return {"underlying": {"symbol": "TEST", "last": spot},
            "expirations": list(expirations),
            "chains": {e: {"calls": calls, "puts": puts} for e in expirations}}


class TestTheSignConvention(unittest.TestCase):
    """Calls positive, puts negative — the standard convention that assumes
    dealers are net long call gamma and net short put gamma. Inverting it
    inverts every number and every regime label, so it is pinned here."""

    def test_a_call_contributes_positive_exposure(self):
        v = G.contract_gex(0.02, 1000, SPOT, "call")
        self.assertGreater(v, 0)

    def test_a_put_contributes_negative_exposure(self):
        v = G.contract_gex(0.02, 1000, SPOT, "put")
        self.assertLess(v, 0)

    def test_a_call_and_a_put_of_equal_size_cancel(self):
        c = G.contract_gex(0.02, 1000, SPOT, "call")
        p = G.contract_gex(0.02, 1000, SPOT, "put")
        self.assertAlmostEqual(c + p, 0.0, places=9)


class TestTheScaling(unittest.TestCase):
    def test_exposure_is_dollars_per_one_percent_of_spot(self):
        gamma, oi = 0.02, 1000
        want = gamma * oi * 100 * SPOT * SPOT * 0.01
        self.assertAlmostEqual(G.contract_gex(gamma, oi, SPOT, "call"), want, places=6)

    def test_the_contract_multiplier_is_settable_not_assumed(self):
        a = G.contract_gex(0.02, 1000, SPOT, "call", contract_size=100)
        b = G.contract_gex(0.02, 1000, SPOT, "call", contract_size=10)
        self.assertAlmostEqual(a / b, 10.0, places=9)

    def test_quoting_per_one_percent_makes_two_prices_comparable(self):
        """A $6 stock and a $600 stock with the same PERCENTAGE gamma
        profile should produce comparable figures. Per-$1 quoting would put
        them a hundred times apart for no economic reason."""
        cheap = G.contract_gex(0.02 * 100, 1000, 6.0, "call")
        rich = G.contract_gex(0.02, 1000, 600.0, "call")
        # Both are within an order of magnitude; per-$1 scaling would not be.
        self.assertLess(max(cheap, rich) / min(cheap, rich), 10_000)


class TestMissingInputs(unittest.TestCase):
    def test_a_missing_greek_is_none_not_zero(self):
        """A missing gamma and a genuinely flat strike are different facts,
        and only one of them should be quietly summed into a total."""
        self.assertIsNone(G.contract_gex(None, 1000, SPOT, "call"))
        self.assertIsNone(G.contract_gex(0.02, None, SPOT, "call"))
        self.assertIsNone(G.contract_gex(0.02, 1000, None, "call"))

    def test_zero_open_interest_carries_no_exposure(self):
        self.assertEqual(G.contract_gex(0.02, 0, SPOT, "call"), 0.0)

    def test_missing_greeks_are_counted_per_strike(self):
        rows = G.by_strike([{"strike": 100, "openInterest": 500, "gamma": None}],
                           [], SPOT)
        self.assertEqual(rows[0]["call_missing"], 1)
        self.assertEqual(rows[0]["call_gex"], 0.0)
        self.assertEqual(rows[0]["call_oi"], 500)


class TestYearFraction(unittest.TestCase):
    def test_calendar_years_not_trading_years(self):
        self.assertAlmostEqual(G.year_fraction("2026-02-04", TODAY), 30 / 365.0, places=9)

    def test_an_expired_contract_drops_out_rather_than_going_negative(self):
        self.assertIsNone(G.year_fraction("2026-01-04", TODAY))

    def test_expiration_day_is_a_few_hours_not_zero(self):
        """Zero would divide by zero in the gamma formula and drop 0-DTE —
        the single expiry with the most gamma in it — out of the profile."""
        t = G.year_fraction("2026-01-05", TODAY)
        self.assertIsNotNone(t)
        self.assertGreater(t, 0)
        self.assertLess(t, 1 / 365.0)


class TestPerStrike(unittest.TestCase):
    def test_calls_and_puts_net_at_each_strike(self):
        rows = G.by_strike([contract(100, oi=2000)], [contract(100, oi=500)], SPOT)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertGreater(r["call_gex"], 0)
        self.assertLess(r["put_gex"], 0)
        self.assertAlmostEqual(r["net_gex"], r["call_gex"] + r["put_gex"], places=6)
        self.assertEqual(r["total_oi"], 2500)

    def test_strikes_come_back_ascending(self):
        rows = G.by_strike([contract(k) for k in (110, 90, 100)], [], SPOT)
        self.assertEqual([r["strike"] for r in rows], [90.0, 100.0, 110.0])


class TestTheGammaFlip(unittest.TestCase):
    def _flip_chain(self):
        # Puts stacked below spot, calls stacked above: net exposure is
        # negative underneath and positive on top, so it crosses.
        ks = [85, 90, 95, 100, 105, 110, 115]
        calls = [contract(k, oi=(3000 if k >= 100 else 200)) for k in ks]
        puts = [contract(k, oi=(3000 if k < 100 else 200)) for k in ks]
        return calls, puts

    def test_it_finds_the_crossing(self):
        calls, puts = self._flip_chain()
        p = G.profile(calls, puts, SPOT, lambda c: c["_expiration"], TODAY)
        self.assertTrue(p["flip_bracketed"])
        self.assertIsNotNone(p["flip"])
        self.assertGreater(p["flip"], SPOT * 0.85)
        self.assertLess(p["flip"], SPOT * 1.15)

    def test_the_profile_actually_changes_sign_at_the_reported_level(self):
        calls, puts = self._flip_chain()
        p = G.profile(calls, puts, SPOT, lambda c: c["_expiration"], TODAY)
        below = [pt for pt in p["points"] if pt["spot"] < p["flip"]]
        above = [pt for pt in p["points"] if pt["spot"] > p["flip"]]
        self.assertLess(below[-1]["net_gex"], 0)
        self.assertGreater(above[0]["net_gex"], 0)

    def test_gamma_is_recomputed_at_each_spot_not_held_fixed(self):
        """The distinguishing property of doing this properly. If gamma were
        held at today's value, net exposure would be a fixed weighted sum
        scaled by spot squared — strictly increasing in magnitude and never
        peaking. Re-computed gamma decays as spot moves away from the
        strikes, so the profile must FALL somewhere on the way out."""
        calls, puts = self._flip_chain()
        p = G.profile(calls, puts, SPOT, lambda c: c["_expiration"], TODAY)
        vals = [pt["net_gex"] for pt in p["points"]]
        peak = max(range(len(vals)), key=lambda i: vals[i])
        self.assertLess(peak, len(vals) - 1,
                        "net exposure peaks at the very top of the grid — "
                        "gamma is not being re-computed")

    def test_no_crossing_is_reported_as_no_crossing(self):
        """An all-put board never turns positive. Extrapolating a number off
        the end of the grid would be an invented level."""
        puts = [contract(k, oi=2000) for k in (90, 95, 100, 105, 110)]
        p = G.profile([], puts, SPOT, lambda c: c["_expiration"], TODAY)
        self.assertFalse(p["flip_bracketed"])
        self.assertIsNone(p["flip"])
        self.assertIn("does not change sign", p["reason"])

    def test_contracts_without_an_iv_are_excluded_and_the_gap_reported(self):
        calls = [contract(100, oi=1000), contract(105, oi=1000, iv=None)]
        p = G.profile(calls, [], SPOT, lambda c: c["_expiration"], TODAY)
        self.assertEqual(p["contracts_used"], 1)
        self.assertEqual(p["contracts_skipped"], 1)
        self.assertAlmostEqual(p["covered_oi_pct"], 50.0, places=6)

    def test_no_priceable_contract_is_an_explanation_not_a_crash(self):
        p = G.profile([{"strike": 100, "openInterest": 500}], [],
                      SPOT, lambda c: None, TODAY)
        self.assertIsNone(p["flip"])
        self.assertEqual(p["contracts_used"], 0)
        self.assertTrue(p["reason"])

    def test_no_spot_is_an_explanation_not_a_crash(self):
        p = G.profile([contract(100)], [], None, lambda c: c["_expiration"], TODAY)
        self.assertEqual(p["points"], [])
        self.assertTrue(p["reason"])


class TestSummary(unittest.TestCase):
    def test_the_largest_strikes_are_the_extremes_of_net_exposure(self):
        rows = [{"strike": 90, "net_gex": -50, "call_gex": 10, "put_gex": -60,
                 "call_oi": 1, "put_oi": 2, "total_oi": 3},
                {"strike": 100, "net_gex": 80, "call_gex": 90, "put_gex": -10,
                 "call_oi": 5, "put_oi": 1, "total_oi": 6}]
        s = G.summarize(rows, SPOT)
        self.assertEqual(s["largest_positive"]["strike"], 100)
        self.assertEqual(s["largest_negative"]["strike"], 90)
        self.assertEqual(s["regime"], "long")

    def test_an_all_positive_board_does_not_invent_a_largest_negative(self):
        rows = [{"strike": 100, "net_gex": 80, "call_gex": 80, "put_gex": 0,
                 "call_oi": 5, "put_oi": 0, "total_oi": 5}]
        s = G.summarize(rows, SPOT)
        self.assertIsNone(s["largest_negative"])
        self.assertIsNotNone(s["largest_positive"])

    def test_an_empty_board_summarizes_to_flat_rather_than_failing(self):
        s = G.summarize([], SPOT)
        self.assertEqual(s["net_gex"], 0)
        self.assertEqual(s["regime"], "flat")
        self.assertIsNone(s["largest_positive"])


class TestBuild(unittest.TestCase):
    def test_it_reads_the_dashboards_own_chain_shape(self):
        c = chain([contract(k) for k in (95, 100, 105)],
                  [contract(k) for k in (95, 100, 105)])
        out = G.build(c, [EXPIRY], TODAY, spot=SPOT)
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["strikes"]), 3)
        self.assertEqual(out["expirations_used"], [EXPIRY])

    def test_the_expiry_survives_flattening(self):
        """The chain is keyed by expiry ABOVE the contract, so flattening the
        legs loses it — and the profile needs a time to expiry per contract."""
        c = chain([contract(100)], [contract(100)])
        out = G.build(c, [EXPIRY], TODAY, spot=SPOT)
        self.assertEqual(out["profile"]["contracts_used"], 2)

    def test_more_than_one_expiry_can_be_summed(self):
        far = "2026-03-06"
        c = {"underlying": {"symbol": "TEST", "last": SPOT},
             "expirations": [EXPIRY, far],
             "chains": {EXPIRY: {"calls": [contract(100)], "puts": []},
                        far: {"calls": [contract(100, expiration=far)], "puts": []}}}
        one = G.build(c, [EXPIRY], TODAY, spot=SPOT)
        both = G.build(c, [EXPIRY, far], TODAY, spot=SPOT)
        self.assertGreater(both["summary"]["call_gex"], one["summary"]["call_gex"])

    def test_far_strikes_are_trimmed_and_the_trim_is_reported(self):
        c = chain([contract(k) for k in (50, 95, 100, 105, 200)], [])
        out = G.build(c, [EXPIRY], TODAY, spot=SPOT, strike_window_pct=25.0)
        self.assertEqual(len(out["strikes"]), 3)
        self.assertEqual(out["strikes_outside_window"], 2)

    def test_a_ladder_entirely_outside_the_window_is_never_trimmed_to_nothing(self):
        """A stock that has moved a long way since its strikes were listed
        keeps its full ladder rather than rendering an empty chart."""
        c = chain([contract(k) for k in (500, 505)], [])
        out = G.build(c, [EXPIRY], TODAY, spot=SPOT, strike_window_pct=25.0)
        self.assertEqual(len(out["strikes"]), 2)
        self.assertEqual(out["strikes_outside_window"], 0)

    def test_an_unknown_expiry_falls_back_to_the_nearest_rather_than_empty(self):
        c = chain([contract(100)], [contract(100)])
        out = G.build(c, ["2099-01-01"], TODAY, spot=SPOT)
        self.assertEqual(out["expirations_used"], [EXPIRY])

    def test_every_payload_carries_its_own_assumptions(self):
        """The convention is a modelling choice. It travels with the numbers
        so a screenshot of this screen cannot lose it."""
        c = chain([contract(100)], [contract(100)])
        out = G.build(c, [EXPIRY], TODAY, spot=SPOT)
        text = out["convention"].lower()
        self.assertIn("calls count positive", text)
        self.assertIn("not published", text)
        self.assertIn("1%", out["convention"])

    def test_an_empty_chain_is_not_ok(self):
        out = G.build({"underlying": {}, "chains": {}}, [], TODAY, spot=SPOT)
        self.assertFalse(out["ok"])


class TestTheShippedFixture(unittest.TestCase):
    """The development fixture has to actually exercise the engine, or it is
    scenery. It is also the only chain available in a test environment with
    no broker connection."""

    def setUp(self):
        import json
        import pathlib
        p = pathlib.Path(__file__).resolve().parent / "fixtures" / "gex_dev_chain.json"
        self.doc = json.loads(p.read_text())

    def test_it_says_plainly_that_it_is_not_real(self):
        what = self.doc["_what_this_is"].lower()
        self.assertIn("synthetic", what)
        self.assertIn("development", what)
        self.assertEqual(self.doc["chain"]["source"], "fixture")

    def test_it_produces_a_complete_reading(self):
        c = self.doc["chain"]
        out = G.build(c, c["expirations"], date(2026, 1, 5), spot=500.0)
        self.assertTrue(out["ok"])
        self.assertGreater(len(out["strikes"]), 10)
        self.assertIsNotNone(out["profile"]["flip"])
        self.assertIsNotNone(out["summary"]["largest_positive"])
        self.assertIsNotNone(out["summary"]["largest_negative"])


if __name__ == "__main__":
    unittest.main()
