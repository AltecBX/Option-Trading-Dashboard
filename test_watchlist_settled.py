"""The watchlist board as of its last finished scan (Codex, #428).

A scan publishes the board after every chunk, so mid-scan it holds only the
stocks done so far. The Live Scanner's quotes and its Shorted high-debt
list read `settled_rows()`: the board the scan started from (the saved one
at boot) until a scan finishes."""

import unittest

import watchlist_table as W


class SettledRows(unittest.TestCase):
    KNOBS = ("yf", "_scan_one", "CHUNK", "_CACHE_PATH", "_load_yields", "_persist")

    def setUp(self):
        self.saved = {k: getattr(W, k) for k in self.KNOBS}
        self.state = dict(W._STATE)
        self.seen = []
        self.fail_persist_at = None
        self.persists = 0

        class DF(dict):
            columns = []

        class FakeYF:
            @staticmethod
            def download(*a, **k):
                return DF()

        self.bad_download = set()
        self.bad_symbol = set()

        def download(tickers, *a, **k):
            if set(tickers.split()) & self.bad_download:
                raise RuntimeError("Yahoo throttled the chunk")
            return DF()
        FakeYF.download = staticmethod(download)

        def scan_one(sym, sub, flow_fn, fresh, prior):
            self.seen.append((sym, [r["symbol"] for r in W.settled_rows()],
                              [r["symbol"] for r in W.get_board()["rows"]]))
            if sym in self.bad_symbol:
                return None
            return {"symbol": sym, "market_cap": {"AAA": 3, "BBB": 2, "CCC": 1}.get(sym, 0)}

        def persist():
            self.persists += 1
            if self.persists == self.fail_persist_at:
                raise OSError("disk full")
        W.yf, W._scan_one, W.CHUNK, W._CACHE_PATH = FakeYF, scan_one, 1, None
        W._load_yields, W._persist = (lambda: {}), persist
        # the board loaded from disk at boot: AAA and BBB, BBB with its bulk
        W._STATE.update(rows=[{"symbol": "AAA", "market_cap": 3},
                              {"symbol": "BBB", "market_cap": 2, "strat": [1, 2, 3]}],
                        scanning=False, last_scan="2026-10-14T22:00:00", error=None, settled=None)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(W, k, v)
        W._STATE.clear()
        W._STATE.update(self.state)

    def scan(self, syms):
        W.trigger_scan(syms)
        W._THREAD.join(10)
        self.assertFalse(W._STATE["scanning"])

    def test_the_first_scan_after_boot_keeps_the_saved_board(self):
        self.scan(["AAA", "BBB", "CCC"])
        sym, settled, board = self.seen[1]               # BBB, in the second chunk
        self.assertEqual("BBB", sym)
        self.assertEqual(["AAA"], board, "the board mid-scan holds the first chunk only")
        self.assertEqual(["AAA", "BBB"], settled, "the saved board stands in until the scan ends")
        self.assertEqual(["AAA", "BBB", "CCC"], [r["symbol"] for r in W.settled_rows()],
                         "the finished scan's board after it")
        self.assertIsNone(W._STATE.get("settled"), "nothing kept once the scan is done")

    def test_no_bulk(self):
        W._STATE["scanning"] = True
        W._STATE["settled"] = list(W._STATE["rows"])
        self.assertNotIn("strat", W.settled_rows()[1])
        W._STATE["scanning"] = False
        W._STATE["settled"] = None
        self.assertNotIn("strat", W.settled_rows()[1])

    def test_a_scan_that_fails_part_way_keeps_the_last_good_board(self):
        self.fail_persist_at = 2                          # after the second chunk is published
        self.scan(["AAA", "BBB", "CCC"])
        self.assertTrue(W._STATE["error"])
        self.assertEqual(["AAA", "BBB"], [r["symbol"] for r in W.get_board()["rows"]])
        self.assertEqual(["AAA", "BBB"], [r["symbol"] for r in W.settled_rows()])
        self.fail_persist_at = None
        W._STATE["rows"] = [{"symbol": "AAA"}]          # as if the failure had left less
        self.seen.clear()
        self.scan(["AAA", "BBB", "CCC"])
        self.assertEqual(["AAA", "BBB"], self.seen[1][1],
                         "the next scan stands on the last good board, not the broken one")

    def test_a_chunk_that_failed_keeps_its_stocks_from_the_held_board(self):
        # Codex, #428: a failed download is swallowed per chunk and the scan
        # still finishes; the held board fills in the stocks it lost.
        self.bad_download = {"BBB"}
        self.scan(["AAA", "BBB", "CCC"])
        self.assertEqual(["AAA", "CCC"], [r["symbol"] for r in W.get_board()["rows"]],
                         "the table shows what was scanned, as before")
        self.assertEqual(["AAA", "BBB", "CCC"], sorted(r["symbol"] for r in W.settled_rows()))
        bbb = next(r for r in W.settled_rows() if r["symbol"] == "BBB")
        self.assertEqual(2, bbb["market_cap"], "BBB as the last good scan had it")
        self.bad_download = set()
        self.scan(["AAA", "BBB", "CCC"])
        self.assertIsNone(W._STATE.get("settled"), "a whole scan needs nothing held")

    def test_a_stock_that_failed_alone_is_kept_too(self):
        self.bad_symbol = {"BBB"}
        self.scan(["AAA", "BBB", "CCC"])
        self.assertEqual(["AAA", "BBB", "CCC"], sorted(r["symbol"] for r in W.settled_rows()))

    def test_a_stock_taken_off_the_watchlist_is_not_carried(self):
        self.scan(["AAA", "CCC"])
        self.assertEqual(["AAA", "CCC"], [r["symbol"] for r in W.settled_rows()])


if __name__ == "__main__":
    unittest.main(verbosity=2)
