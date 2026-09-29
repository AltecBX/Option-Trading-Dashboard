"""Worth Selling Today graded in real dollars (v5.36).

Contract histories have the shape of UW's /api/option-contract/{id}/historic
example: daily rows under `chains` with nbbo_bid, nbbo_ask and last_price.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import report_card as RC

PICK_DAY = date(2026, 9, 1)


def put_spread(sym="GOOGL", short=305.0, long_=290.0, credit=1.66, expiry="2026-09-18"):
    return {"symbol": sym, "kind": "put_credit_spread", "trade": "Put credit spread",
            "expiration": expiry, "credit": credit, "max_loss": round(short - long_ - credit, 2), "spot": 322.0,
            "legs": [{"action": "sell", "right": "put", "strike": short},
                     {"action": "buy", "right": "put", "strike": long_}]}


def day(d, bid, ask, last=None):
    return {"date": d, "nbbo_bid": str(bid), "nbbo_ask": str(ask), "last_price": str(last if last is not None else ask),
            "volume": 10, "open_interest": 100}


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.hist = {}
        self.asked = []

    def tearDown(self):
        self.tmp.cleanup()

    def history(self, sym):
        self.asked.append(sym)
        return self.hist.get(sym)


class Symbols(unittest.TestCase):
    def test_occ(self):
        self.assertEqual("GOOGL260918P00305000", RC.occ("googl", "2026-09-18", "put", 305.0))
        self.assertEqual("BRK.B261016C00412500", RC.occ("BRK.B", "2026-10-16", "call", 412.5))
        self.assertIsNone(RC.occ("X", "nope", "put", 1))


class Recording(Harness):
    def test_a_pick_is_written_once_however_often_it_is_shown(self):
        self.assertEqual(1, RC.record(self.dir, [put_spread()], PICK_DAY))
        self.assertEqual(0, RC.record(self.dir, [put_spread()], date(2026, 9, 2)))
        self.assertEqual(1, RC.record(self.dir, [put_spread(short=300.0)], date(2026, 9, 2)),
                         "different strikes are a different pick")
        picks = RC.load(self.dir)
        self.assertEqual(["2026-09-01", "2026-09-02"], [p["date"] for p in picks])

    def test_a_row_without_legs_or_credit_is_not_a_pick(self):
        bad = [dict(put_spread(), legs=[]), dict(put_spread(), credit=None), dict(put_spread(), expiration=None)]
        self.assertEqual(0, RC.record(self.dir, bad, PICK_DAY))


class Grading(Harness):
    def test_a_finished_pick_is_priced_at_expiry_and_kept(self):
        RC.record(self.dir, [put_spread()], PICK_DAY)
        self.hist["GOOGL260918P00305000"] = [day("2026-09-10", 1.0, 1.2), day("2026-09-18", 0.0, 0.02),
                                             day("2026-09-19", 5.0, 6.0)]
        self.hist["GOOGL260918P00290000"] = [day("2026-09-18", 0.0, 0.01)]
        out = RC.report(self.dir, self.history, date(2026, 9, 25))
        self.assertEqual((1, 1, 100.0), (out["closed"], out["wins"], out["win_rate"]))
        # Bought back for 0.01 - 0.005 = 0.005; kept 1.66 - 0.005 = 1.655 a share.
        self.assertEqual(165.5, out["total_pnl"])
        self.assertIn("Of 1 finished pick, 1 made money (100%), +$166 in total", out["text"])
        self.asked.clear()
        RC.report(self.dir, self.history, date(2026, 9, 26))
        self.assertEqual([], self.asked, "a final grade is not fetched again")

    def test_an_open_pick_is_marked_to_market_and_can_lose(self):
        RC.record(self.dir, [put_spread(expiry="2026-10-16")], PICK_DAY)
        self.hist["GOOGL261016P00305000"] = [day("2026-09-24", 6.0, 6.2)]
        self.hist["GOOGL261016P00290000"] = [day("2026-09-24", 1.0, 1.1)]
        out = RC.report(self.dir, self.history, date(2026, 9, 25))
        self.assertEqual((0, 1), (out["closed"], out["open"]))
        # Buy back: 6.10 - 1.05 = 5.05 against 1.66 taken in: -339 a contract.
        self.assertEqual(-339.0, out["open_pnl"])
        self.assertIn("1 still open: -$339 if bought back today", out["text"])
        self.assertFalse(out["recent"][0]["win"])

    def test_missing_history_stays_ungraded_not_guessed(self):
        RC.record(self.dir, [put_spread()], PICK_DAY)
        out = RC.report(self.dir, self.history, date(2026, 9, 25))
        self.assertEqual((0, 0, 1), (out["closed"], out["open"], out["ungraded"]))
        self.assertIn("No pick has reached its expiry yet", out["text"])

    def test_not_graded_on_the_day_it_was_picked(self):
        RC.record(self.dir, [put_spread()], PICK_DAY)
        RC.report(self.dir, self.history, PICK_DAY)
        self.assertEqual([], self.asked)

    def test_the_budget_holds_and_the_rest_waits(self):
        RC.record(self.dir, [put_spread(sym=f"S{i}") for i in range(5)], PICK_DAY)
        RC.report(self.dir, self.history, date(2026, 9, 25), budget=4)
        self.assertEqual(4, len(self.asked), "two picks' legs, no more")

    def test_a_condor_counts_all_four_legs(self):
        condor = {"symbol": "AMD", "kind": "iron_condor", "trade": "Iron condor", "expiration": "2026-09-18",
                  "credit": 3.05, "legs": [{"action": "sell", "right": "put", "strike": 150.0},
                                           {"action": "buy", "right": "put", "strike": 142.0},
                                           {"action": "sell", "right": "call", "strike": 172.0},
                                           {"action": "buy", "right": "call", "strike": 180.0}]}
        RC.record(self.dir, [condor], PICK_DAY)
        for s, px in (("AMD260918P00150000", 0.0), ("AMD260918P00142000", 0.0),
                      ("AMD260918C00172000", 4.0), ("AMD260918C00180000", 0.0)):
            self.hist[s] = [day("2026-09-18", px, px)]
        out = RC.report(self.dir, self.history, date(2026, 9, 25))
        self.assertEqual(-95.0, out["total_pnl"], "3.05 in, 4.00 to close: a loss of $95")
        self.assertEqual(0, out["wins"])


class TheServer(unittest.TestCase):
    def test_the_board_records_only_todays_scan(self):
        src = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()
        at = src.index('if parsed.path == "/api/setup_board":')
        body = src[at:at + 6000]
        self.assertIn('_report_card.record(_STABLE_DIR, out["rows"], _today)', body)
        self.assertIn('_asof == _today.isoformat()', body)
        self.assertIn('if parsed.path == "/api/uw/report_card":', src)
        self.assertIn("uw.option_historic(sym)", src)

    def test_the_client_reads_the_chains_key(self):
        import unusual_whales_client as U
        self.assertEqual("chains", U.PEEL_KEY["option_historic"])
        self.assertEqual("/api/option-contract/{id}/historic", U.ENDPOINTS["option_historic"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
