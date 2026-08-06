"""Tests for playbook.py — the Options Playbook join (v3.66).

Everything runs on synthetic board fixtures: no network, no pandas needed
for the join itself. The trend._returns window math is covered too (skipped
cleanly if pandas is unavailable, mirroring how trend.py itself degrades).
"""
from __future__ import annotations

import unittest

import playbook


def _trend_row(sym, direction="up", score=60.0, **over):
    row = {
        "ticker": sym, "last": 100.0, "direction": direction, "score": score,
        "rsi": 55.0, "streak": 2, "from_high": -5.0, "from_low": 40.0,
        "above_ma200": direction == "up", "new_high": False, "new_low": False,
        "overbought": False, "oversold": False,
        "r1w": 1.0, "r1m": 4.0, "r3m": 9.0, "r6m": 15.0, "r1y": 30.0,
        "importance": "high", "reasons": ["above 200-DMA", "above 50-DMA"],
    }
    row.update(over)
    return row


def _iv_row(sym, rank=75.0, **over):
    rk = rank if rank is not None else 0
    regime = ("rich" if rk >= 70 else "elevated" if rk >= 50
              else "normal" if rk >= 30 else "cheap")
    row = {
        "ticker": sym, "last": 100.0, "hv": 40.0, "hv_low": 20.0, "hv_high": 60.0,
        "rank": rank, "percentile": rank, "rank_n": 250, "regime": regime,
        "expanding": False, "contracting": False, "score": rank,
        "importance": "high", "reasons": ["HV 40% (1y 20–60)"],
    }
    row.update(over)
    return row


def _board(rows, last_scan="2026-08-06T12:00:00+00:00", scanning=False):
    return {"as_of": last_scan, "count": len(rows), "rows": rows,
            "status": {"scanning": scanning, "scanned": len(rows), "total": len(rows),
                       "last_scan": last_scan, "universe_size": len(rows), "error": None}}


def _wl_board(rows):
    return {"rows": rows, "status": {"last_scan": "2026-08-06T11:00:00+00:00",
                                     "scanning": False, "scanned": len(rows),
                                     "total": len(rows), "error": None}}


class TestClassify(unittest.TestCase):
    def test_quadrant_matrix(self):
        # The user's exact decision rule, all four cells.
        self.assertEqual(playbook.classify("up", 20), "buy_calls")
        self.assertEqual(playbook.classify("down", 20), "buy_puts")
        self.assertEqual(playbook.classify("up", 80), "sell_puts")
        self.assertEqual(playbook.classify("down", 80), "sell_calls")

    def test_threshold_boundary(self):
        # rank exactly 50 → selling side (>=); 49.9 → buying side.
        self.assertEqual(playbook.classify("up", 50.0), "sell_puts")
        self.assertEqual(playbook.classify("up", 49.9), "buy_calls")


class TestConviction(unittest.TestCase):
    def test_bounds(self):
        for ts in (0, 50, 100):
            for rk in (0, 50, 100):
                c = playbook.conviction(ts, rk)
                self.assertGreaterEqual(c, 0.0)
                self.assertLessEqual(c, 100.0)
        self.assertEqual(playbook.conviction(100, 0), 100.0)
        self.assertEqual(playbook.conviction(100, 100), 100.0)
        self.assertEqual(playbook.conviction(0, 50), 0.0)

    def test_monotonic(self):
        # Stronger trend → higher conviction; rank further from 50 → higher.
        self.assertGreater(playbook.conviction(80, 70), playbook.conviction(40, 70))
        self.assertGreater(playbook.conviction(60, 90), playbook.conviction(60, 60))
        self.assertGreater(playbook.conviction(60, 10), playbook.conviction(60, 40))


class TestAssemble(unittest.TestCase):
    def test_join_and_quadrants(self):
        tb = _board([_trend_row("AAA", "up", 70), _trend_row("BBB", "down", 65),
                     _trend_row("CCC", "up", 50), _trend_row("DDD", "down", 45),
                     _trend_row("TONLY", "up", 90)])
        ib = _board([_iv_row("AAA", 85), _iv_row("BBB", 75), _iv_row("CCC", 20),
                     _iv_row("DDD", 25), _iv_row("IONLY", 99)])
        out = playbook.assemble(tb, ib)
        by = {r["ticker"]: r for r in out["rows"]}
        self.assertEqual(set(by), {"AAA", "BBB", "CCC", "DDD"})
        self.assertEqual(by["AAA"]["quadrant"], "sell_puts")
        self.assertEqual(by["BBB"]["quadrant"], "sell_calls")
        self.assertEqual(by["CCC"]["quadrant"], "buy_calls")
        self.assertEqual(by["DDD"]["quadrant"], "buy_puts")
        # Names in only one board are excluded and COUNTED — never fabricated.
        self.assertEqual(out["excluded"], {"trend_only": 1, "ivrank_only": 1})
        # Sorted by conviction descending.
        convs = [r["conviction"] for r in out["rows"]]
        self.assertEqual(convs, sorted(convs, reverse=True))
        # Returns pass through untouched.
        self.assertEqual(by["AAA"]["r1m"], 4.0)
        self.assertEqual(by["AAA"]["r1y"], 30.0)

    def test_summary_membership_and_cap(self):
        tb = _board([_trend_row(f"S{i:02d}", "up", 60) for i in range(20)])
        ib = _board([_iv_row(f"S{i:02d}", 80) for i in range(20)])
        out = playbook.assemble(tb, ib)
        self.assertEqual(len(out["summary"]["sell_puts"]), playbook.SUMMARY_CAP)
        self.assertEqual(out["summary"]["buy_calls"], [])
        for r in out["summary"]["sell_puts"]:
            self.assertEqual(r["quadrant"], "sell_puts")

    def test_watchlist_enrichment_and_earnings_flag(self):
        tb = _board([_trend_row("EAR", "up", 60), _trend_row("FAR", "up", 60),
                     _trend_row("UNK", "up", 60)])
        ib = _board([_iv_row("EAR", 80), _iv_row("FAR", 80), _iv_row("UNK", 80)])
        wl = _wl_board([
            {"symbol": "EAR", "sector": "Technology", "market_cap": 5e10,
             "days_to_earnings": 3, "next_earnings": "2026-08-09"},
            {"symbol": "FAR", "sector": "Energy", "market_cap": 1e10,
             "days_to_earnings": 40, "next_earnings": "2026-09-15"},
        ])
        out = playbook.assemble(tb, ib, wl)
        by = {r["ticker"]: r for r in out["rows"]}
        self.assertTrue(by["EAR"]["earnings_soon"])
        self.assertTrue(any("earnings in 3d" in f for f in by["EAR"]["flags"]))
        self.assertEqual(by["EAR"]["sector"], "Technology")
        self.assertIs(by["FAR"]["earnings_soon"], False)
        # Name absent from the watchlist board: enrichment fields stay None —
        # never a fabricated sector/date.
        self.assertIsNone(by["UNK"]["earnings_soon"])
        self.assertIsNone(by["UNK"]["sector"])

    def test_vol_trend_flags_depend_on_side(self):
        tb = _board([_trend_row("SEL", "up", 60), _trend_row("BUY", "up", 60)])
        ib = _board([_iv_row("SEL", 80, expanding=True),
                     _iv_row("BUY", 20, expanding=True)])
        out = playbook.assemble(tb, ib)
        by = {r["ticker"]: r for r in out["rows"]}
        self.assertTrue(any("selling early" in f for f in by["SEL"]["flags"]))
        self.assertTrue(any("tailwind" in f for f in by["BUY"]["flags"]))

    def test_empty_sources_guidance(self):
        empty = _board([], last_scan=None)
        out = playbook.assemble(empty, empty)
        self.assertEqual(out["rows"], [])
        self.assertEqual(len(out["missing"]), 2)
        self.assertIn("Trend board is empty", out["missing"][0])

    def test_sources_passthrough(self):
        tb = _board([_trend_row("AAA")], last_scan="2026-08-06T09:00:00+00:00")
        ib = _board([_iv_row("AAA")], scanning=True)
        out = playbook.assemble(tb, ib)
        self.assertEqual(out["sources"]["trend"]["last_scan"], "2026-08-06T09:00:00+00:00")
        self.assertTrue(out["sources"]["ivrank"]["scanning"])
        self.assertIsNone(out["sources"]["watchlist"])

    def test_rows_missing_rank_or_direction_skipped(self):
        tb = _board([_trend_row("OK"), _trend_row("BADDIR", direction="sideways")])
        ib = _board([_iv_row("OK"), _iv_row("BADDIR"), _iv_row("NORANK", rank=None)])
        out = playbook.assemble(tb, ib)
        self.assertEqual([r["ticker"] for r in out["rows"]], ["OK"])


class TestTrendReturns(unittest.TestCase):
    def test_window_math(self):
        try:
            import pandas as pd
        except Exception:
            self.skipTest("pandas unavailable")
        import trend
        # 260 bars ending at 200.0; known bases at each strict lookback.
        closes = pd.Series([100.0] * 260)
        closes.iloc[-1] = 200.0
        closes.iloc[-6] = 160.0     # 5 trading days back → r1w
        closes.iloc[-22] = 125.0    # 21 back → r1m
        closes.iloc[0] = 50.0       # earliest → r1y base
        r = trend._returns(closes)
        self.assertEqual(r["r1w"], 25.0)    # 200/160
        self.assertEqual(r["r1m"], 60.0)    # 200/125
        self.assertEqual(r["r3m"], 100.0)   # 200/100
        self.assertEqual(r["r1y"], 300.0)   # 200/50
        # Short history: strict windows go None instead of shrinking.
        short = pd.Series([100.0] * 30)
        short.iloc[-1] = 110.0
        r2 = trend._returns(short)
        self.assertEqual(r2["r1w"], 10.0)
        self.assertIsNone(r2["r3m"])
        self.assertIsNone(r2["r1y"])


if __name__ == "__main__":
    unittest.main()
