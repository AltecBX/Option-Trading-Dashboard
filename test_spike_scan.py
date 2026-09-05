"""test_spike_scan.py — the board that sells into today's runs.

The guards here are the ones that keep a fast board honest:

  * the ruler is sigma, so a 3.6% day on a wild name does not outrank a
    16% day on a quiet one;
  * only same-day expiries, and only strikes ABOVE where the stock is;
  * the ranking is credit minus the measured settlement, and that number
    improves as the session runs out — the whole basis of the trade;
  * a takeover headline is refused outright, not ranked;
  * nothing is listed without a real bid, and every refusal keeps its reason;
  * the worker does not run when the market is shut or nobody is looking.
"""
import math
import unittest
from datetime import date, datetime, timedelta

import spike_scan as sk
import spike_evidence as sev


def _walk(n=400, start=100.0, step=0.03, seed=5):
    import random
    rng = random.Random(seed)
    out, px = [], start
    d = date(2024, 1, 1)
    for _ in range(n):
        while d.weekday() > 4:
            d += timedelta(days=1)
        r = rng.gauss(0, step)
        o = px
        px = px * math.exp(r)
        out.append({"date": d.isoformat(), "open": o, "close": px,
                    "high": max(o, px) * 1.004, "low": min(o, px) * 0.996, "volume": 5e6})
        d += timedelta(days=1)
    return out


BARS = {"RUN": _walk(step=0.03, seed=5), "CALM": _walk(step=0.005, seed=6),
        "WILD": _walk(step=0.06, seed=7)}
TODAY = date.fromisoformat(BARS["RUN"][-1]["date"])


def _n(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _chain(spot, expiry=None, rich=1.0, bid_ok=True, oi=800):
    """A same-day call ladder. `rich` scales the credit so a test can make a
    strike worth selling or not."""
    exp = (expiry or TODAY).isoformat()
    t = 3.0 / 6.5 / 252
    iv = 1.4
    calls = []
    step = max(0.5, round(spot * 0.01, 2))
    k = math.floor(spot / step) * step
    while k <= spot * 1.4:
        d1 = (math.log(spot / k) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
        px = (spot * _n(d1) - k * _n(d1 - iv * math.sqrt(t))) * rich
        if px >= 0.02:
            calls.append({"strike": round(k, 2),
                          "bid": round(px * 0.95, 2) if bid_ok else 0.0,
                          "ask": round(px * 1.05, 2), "iv": iv, "delta": 0.3,
                          "openInterest": oi, "volume": 200})
        k += step
    return {"underlying": {"last": spot}, "chains": {exp: {"calls": calls, "puts": []}}}


def _board(rows):
    return {"rows": rows}


def _wire(rows, now_hour=14, catalyst=None):
    sk._TABLES.clear()
    sk._SIGMAS.clear()
    sk.configure(
        board_getter=lambda: _board(rows), bars_fn=lambda s: BARS.get(s),
        market_open_fn=lambda: True, catalyst_fn=catalyst,
        now_fn=lambda: datetime(TODAY.year, TODAY.month, TODAY.day, now_hour, 0).astimezone())


class Stage1(unittest.TestCase):
    def test_the_ruler_is_sigma_not_percent(self):
        # WILD moves 12% — three times CALM's 4% — but on its own volatility
        # that is the SMALLER event, and the board must say so.
        _wire([{"symbol": "CALM", "last": 104.0, "change": 4.0, "avg_volume": 5e6},
               {"symbol": "WILD", "last": 112.0, "change": 12.0, "avg_volume": 5e6}])
        cands, universe = sk.stage1()
        self.assertEqual(universe, 2)
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0]["symbol"], "CALM",
                         "the smaller percentage was the bigger event for that stock")
        self.assertGreater(cands[0]["move_sigma"], cands[1]["move_sigma"])
        self.assertLess(cands[0]["change_pct"], cands[1]["change_pct"],
                        "and it got there on a smaller percentage move")

    def test_a_small_move_in_its_own_terms_is_not_a_candidate(self):
        _wire([{"symbol": "WILD", "last": 101.0, "change": 1.0, "avg_volume": 5e6}])
        self.assertEqual(sk.stage1()[0], [])

    def test_down_days_are_not_on_a_call_selling_board(self):
        _wire([{"symbol": "CALM", "last": 96.0, "change": -4.0, "avg_volume": 5e6}])
        self.assertEqual(sk.stage1()[0], [])

    def test_penny_names_are_skipped(self):
        _wire([{"symbol": "CALM", "last": 2.0, "change": 25.0, "avg_volume": 5e6}])
        self.assertEqual(sk.stage1()[0], [])

    def test_a_name_without_history_cannot_be_measured_so_is_not_offered(self):
        _wire([{"symbol": "NOPE", "last": 50.0, "change": 20.0, "avg_volume": 5e6}])
        self.assertEqual(sk.stage1()[0], [])


class Clock(unittest.TestCase):
    def test_the_session_fraction_tracks_the_bell(self):
        d = datetime(2026, 9, 4, 9, 30).astimezone()
        self.assertEqual(sk.elapsed_fraction(d), 0.0)
        self.assertAlmostEqual(sk.elapsed_fraction(d.replace(hour=12, minute=45)), 0.5, places=2)
        self.assertEqual(sk.elapsed_fraction(d.replace(hour=16, minute=0)), 1.0)
        self.assertEqual(sk.elapsed_fraction(d.replace(hour=8, minute=0)), 0.0)
        self.assertEqual(sk.elapsed_fraction(d.replace(hour=19, minute=0)), 1.0)


class Pricing(unittest.TestCase):
    def setUp(self):
        _wire([{"symbol": "RUN", "last": 118.0, "change": 18.0, "avg_volume": 5e6}])
        self.cfg = sk.config()
        self.cand = sk.stage1(self.cfg)[0][0]

    def test_only_strikes_above_the_current_price_are_offered(self):
        res = sk.analyze(self.cand, _chain(118.0), BARS["RUN"], self.cfg)
        self.assertTrue(res["rows"])
        for r in res["rows"]:
            self.assertGreater(r["strike"], res["spot"])

    def test_only_same_day_expiries(self):
        far = _chain(118.0, expiry=TODAY + timedelta(days=7))
        self.assertIsNone(sk.analyze(self.cand, far, BARS["RUN"], self.cfg))

    def test_the_edge_is_the_credit_less_the_measured_settlement(self):
        res = sk.analyze(self.cand, _chain(118.0), BARS["RUN"], self.cfg)
        for r in res["rows"]:
            self.assertAlmostEqual(r["edge_per_share"], r["credit"] - r["settles"], places=9)
            self.assertAlmostEqual(r["edge_per_contract"], r["edge_per_share"] * 100, places=6)

    def test_the_same_strike_improves_as_the_session_runs_out(self):
        def edge_at(hour):
            _wire([{"symbol": "RUN", "last": 118.0, "change": 18.0, "avg_volume": 5e6}], now_hour=hour)
            cfg = sk.config()
            c = sk.stage1(cfg)[0][0]
            res = sk.analyze(c, _chain(118.0), BARS["RUN"], cfg)
            row = sorted(res["rows"], key=lambda r: r["strike"])[0]
            return row["edge_per_contract"], row["strike"]
        early, k1 = edge_at(10)
        late, k2 = edge_at(15)
        self.assertEqual(k1, k2)
        self.assertLess(early, late)

    def test_a_further_strike_settles_for_less(self):
        res = sk.analyze(self.cand, _chain(118.0), BARS["RUN"], self.cfg)
        by_k = sorted(res["rows"], key=lambda r: r["strike"])
        for a, b in zip(by_k, by_k[1:]):
            self.assertLessEqual(b["settles"], a["settles"] + 1e-9)
            self.assertLessEqual(b["p_close_above"], a["p_close_above"] + 1e-9)

    def test_every_row_carries_its_evidence_and_its_provenance(self):
        res = sk.analyze(self.cand, _chain(118.0), BARS["RUN"], self.cfg)
        for r in res["rows"]:
            for f in ("p_close_above", "p_touch", "p_finishes_at_high", "grade",
                      "n_own", "weight_own", "session_basis", "move_sigma",
                      "beyond_sigma", "sigma_annual", "settles_full_session"):
                self.assertIn(f, r)
            self.assertGreaterEqual(r["p_touch"], r["p_close_above"] - 1e-9)


class Refusals(unittest.TestCase):
    def setUp(self):
        _wire([{"symbol": "RUN", "last": 118.0, "change": 18.0, "avg_volume": 5e6}])
        self.cfg = sk.config()
        self.cand = sk.stage1(self.cfg)[0][0]

    def test_no_bid_is_no_trade(self):
        res = sk.analyze(self.cand, _chain(118.0, bid_ok=False), BARS["RUN"], self.cfg)
        if res:
            keep, refused = sk.qualify(res["rows"], self.cfg)
            self.assertEqual(keep, [])
            self.assertTrue(any("bid" in " ".join(r["why"]) for r in refused))

    def test_thin_open_interest_is_refused_with_the_reason(self):
        res = sk.analyze(self.cand, _chain(118.0, oi=1), BARS["RUN"], self.cfg)
        keep, refused = sk.qualify(res["rows"], self.cfg)
        self.assertEqual(keep, [])
        self.assertTrue(any("open interest" in " ".join(r["why"]) for r in refused))

    def test_a_fairly_priced_chain_leaves_no_edge_and_says_so(self):
        res = sk.analyze(self.cand, _chain(118.0, rich=0.25), BARS["RUN"], self.cfg)
        keep, refused = sk.qualify(res["rows"], self.cfg)
        self.assertEqual(keep, [])
        self.assertTrue(any("settlement" in " ".join(r["why"]) for r in refused))

    def test_a_rich_chain_does_qualify(self):
        res = sk.analyze(self.cand, _chain(118.0, rich=4.0), BARS["RUN"], self.cfg)
        keep, _ref = sk.qualify(res["rows"], self.cfg)
        self.assertTrue(keep, "a chain paying four times fair value must produce a trade")
        for r in keep:
            self.assertGreater(r["edge_per_contract"], 0)

    def test_a_takeover_spike_is_never_listed(self):
        cfg = sk.config()
        for kind in cfg["events"]["refuse_kinds"]:
            _wire([{"symbol": "RUN", "last": 118.0, "change": 18.0, "avg_volume": 5e6}],
                  catalyst=lambda s, k=kind: {"kind": k})
            self.assertIsNotNone(sk._catalyst_refusal("RUN", sk.config()))
        _wire([{"symbol": "RUN", "last": 118.0, "change": 18.0, "avg_volume": 5e6}],
              catalyst=lambda s: {"kind": "EARNINGS"})
        self.assertIsNone(sk._catalyst_refusal("RUN", sk.config()))

    def test_a_thin_underlying_is_refused(self):
        _wire([{"symbol": "RUN", "last": 118.0, "change": 18.0, "avg_volume": 100}])
        cfg = sk.config()
        cand = sk.stage1(cfg)[0][0]
        res = sk.analyze(cand, _chain(118.0, rich=4.0), BARS["RUN"], cfg)
        keep, refused = sk.qualify(res["rows"], cfg)
        self.assertEqual(keep, [])
        self.assertTrue(any("thin" in " ".join(r["why"]) for r in refused))


class Board(unittest.TestCase):
    def test_a_closed_market_starts_no_worker_and_says_why(self):
        sk.configure(board_getter=lambda: _board([]), bars_fn=lambda s: BARS.get(s),
                     market_open_fn=lambda: False)
        with sk._LOCK:
            sk._STATE["rows"] = []
        out = sk.snapshot()
        self.assertTrue(out["no_trade"])
        self.assertIn("closed", out["no_trade_reason"])
        self.assertFalse(out["scanning"])

    def test_the_payload_carries_what_the_card_needs(self):
        sk.configure(board_getter=lambda: _board([]), bars_fn=lambda s: BARS.get(s),
                     market_open_fn=lambda: False)
        out = sk.snapshot()
        for f in ("version", "evidence_version", "rows", "candidates", "refused",
                  "elapsed", "session_profile", "prior", "universe", "scanned"):
            self.assertIn(f, out)
        self.assertGreater(out["prior"]["n_sessions"], 100000)

    def test_detail_for_a_name_not_on_the_board_is_an_error_not_a_crash(self):
        d = sk.detail("NOPE")
        self.assertFalse(d["ok"])
        self.assertIn("error", d)

    def test_the_session_profile_falls_back_rather_than_inventing_one(self):
        sk.configure(board_getter=lambda: _board([]), bars_fn=lambda s: BARS.get(s),
                     market_open_fn=lambda: False, minute_day_fn=None)
        with sk._LOCK:
            sk._STATE["profile"] = None
            sk._STATE["profile_day"] = None
        self.assertIsNone(sk.session_profile())
        self.assertIn("MODELED", sk.snapshot()["session_profile"])


class Config(unittest.TestCase):
    def test_every_floor_is_published_in_thresholds(self):
        import json
        from pathlib import Path
        t = json.loads((Path(__file__).resolve().parent / "thresholds.json").read_text())
        self.assertIn("spike", t)
        for section in ("select", "liquidity", "edge", "events", "scan"):
            self.assertIn(section, t["spike"])

    def test_config_merges_over_the_defaults(self):
        cfg = sk.config()
        self.assertGreaterEqual(cfg["select"]["min_move_sigma"], 0.5)
        self.assertEqual(cfg["select"]["max_dte"], 0, "this board is same-day only")


if __name__ == "__main__":
    unittest.main()
