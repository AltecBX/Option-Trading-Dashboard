"""A row with no sector is a source that went quiet, not a stock without one (v4.76).

The Sectors view groups the watchlist by a `sector` field that had exactly
one source: Yahoo's `.info`. Yahoo throttles that call during a 1,300-name
sweep and answers with an empty dict, so the scheduled scan ran, every row
came back with sector None, and the Sectors tab showed nothing. These guards
pin the fix: the SEC's SIC code maps onto the interface's eleven sector
labels, the scanner takes a remembered or SEC-derived label when Yahoo is
quiet, and a real Yahoo answer or the user's CSV always wins.
"""
from __future__ import annotations

import types
import unittest

import analyst_board
import sector_map as sm
import watchlist_table as wt


# Real SIC codes, read from SEC EDGAR submissions on September 3, 2026, and
# the sector Yahoo labels each name with. The three commented-out names are
# taxonomy disagreements between the SEC and Yahoo (Alphabet files as
# computer services; Visa as business services; GE as electrical
# equipment) — the fix never overrides a Yahoo label, so they only surface
# when Yahoo has never answered for the name.
REAL = {
    "AAPL": ("3571", sm.TECH), "MSFT": ("7372", sm.TECH), "NVDA": ("3674", sm.TECH),
    "AMZN": ("5961", sm.CYC), "TSLA": ("3711", sm.CYC), "XOM": ("2911", sm.ENERGY),
    "JPM": ("6021", sm.FIN), "PFE": ("2834", sm.HEALTH), "UNH": ("6324", sm.HEALTH),
    "PG": ("2840", sm.DEF), "KO": ("2080", sm.DEF), "WMT": ("5331", sm.DEF),
    "NEE": ("4911", sm.UTIL), "DUK": ("4931", sm.UTIL), "PLD": ("6798", sm.RE),
    "CAT": ("3531", sm.IND), "BA": ("3721", sm.IND), "LIN": ("2810", sm.MAT),
    "NUE": ("3312", sm.MAT), "T": ("4813", sm.COMM), "DIS": ("7990", sm.COMM),
    "NFLX": ("7841", sm.COMM), "MCD": ("5812", sm.CYC), "HD": ("5211", sm.CYC),
    "GS": ("6211", sm.FIN), "CRM": ("7372", sm.TECH), "LITE": ("3669", sm.TECH),
    "CIEN": ("3661", sm.TECH), "SNDK": ("3572", sm.TECH), "COIN": ("6199", sm.FIN),
    "MRNA": ("2836", sm.HEALTH), "CVS": ("5912", sm.HEALTH), "LMT": ("3760", sm.IND),
    "UPS": ("4210", sm.IND), "DAL": ("4512", sm.IND), "MAR": ("7011", sm.CYC),
    "SBUX": ("5810", sm.CYC), "NKE": ("3021", sm.CYC), "BX": ("6282", sm.FIN),
    "KMI": ("4922", sm.ENERGY), "SLB": ("1389", sm.ENERGY), "DE": ("3523", sm.IND),
    "ADM": ("2070", sm.DEF), "MO": ("2111", sm.DEF), "FCX": ("1000", sm.MAT),
    "DOW": ("2821", sm.MAT), "APD": ("2810", sm.MAT),
}


class TestTheSicCodeLandsWhereYahooPutsIt(unittest.TestCase):
    def test_forty_seven_real_filers(self):
        wrong = {s: (code, sm.sector_for_sic(code), want)
                 for s, (code, want) in REAL.items() if sm.sector_for_sic(code) != want}
        self.assertEqual(wrong, {})

    def test_every_label_is_one_the_interface_already_uses(self):
        # perfection.SECTOR_ETF is the sector → ETF map the app draws on;
        # a label outside it would be a twelfth sector nothing else knows.
        import perfection
        for label in sm.SECTORS:
            self.assertIn(label, perfection.SECTOR_ETF, label)
        for code in range(100, 10000):
            got = sm.sector_for_sic(code)
            if got is not None:
                self.assertIn(got, sm.SECTORS, code)

    def test_a_specific_code_beats_the_range_it_sits_in(self):
        self.assertEqual(sm.sector_for_sic("3571"), sm.TECH)     # computers, inside machinery
        self.assertEqual(sm.sector_for_sic("3531"), sm.IND)      # the machinery around it
        self.assertEqual(sm.sector_for_sic("6324"), sm.HEALTH)   # health plans, inside finance
        self.assertEqual(sm.sector_for_sic("6321"), sm.FIN)      # the insurance around it
        self.assertEqual(sm.sector_for_sic("4922"), sm.ENERGY)   # gas transmission, inside utilities
        self.assertEqual(sm.sector_for_sic("4923"), sm.UTIL)     # gas distribution

    def test_codes_arrive_in_every_shape(self):
        self.assertEqual(sm.sector_for_sic(3571), sm.TECH)
        self.assertEqual(sm.sector_for_sic(" 3571 "), sm.TECH)
        self.assertEqual(sm.sector_for_sic("571"), sm.DEF)      # 0571 is agriculture
        self.assertIsNone(sm.sector_for_sic(None))
        self.assertIsNone(sm.sector_for_sic(""))
        self.assertIsNone(sm.sector_for_sic("abcd"))
        self.assertIsNone(sm.sector_for_sic("0000"))


class _FakeFund:
    """Stands in for fundamentals: answers sic_metadata from a table and
    counts the asks."""

    def __init__(self, table, avail=True):
        self.table = table
        self.avail = avail
        self.asks = []
        self._DATA_DIR = "already-configured"

    def available(self):
        return self.avail

    def sic_metadata(self, symbol):
        self.asks.append(symbol)
        row = self.table.get(symbol)
        return None if row is None else {"sic": row[0], "sic_description": row[1]}


class TestTheHintNeverRaises(unittest.TestCase):
    def setUp(self):
        self._real = sm._fund

    def tearDown(self):
        sm._fund = self._real

    def test_a_filer_gets_sector_and_industry(self):
        sm._fund = _FakeFund({"AAPL": ("3571", "Electronic Computers")})
        self.assertEqual(sm.sector_hint("AAPL"),
                         {"sector": sm.TECH, "industry": "Electronic Computers", "sic": "3571"})

    def test_no_filer_no_sec_and_a_raising_source_all_answer_blank(self):
        blank = {"sector": None, "industry": None, "sic": None}
        sm._fund = _FakeFund({})
        self.assertEqual(sm.sector_hint("ZZZZ"), blank)
        sm._fund = _FakeFund({"AAPL": ("3571", "x")}, avail=False)
        self.assertEqual(sm.sector_hint("AAPL"), blank)
        sm._fund = types.SimpleNamespace(available=lambda: True, _DATA_DIR="x",
                                         sic_metadata=lambda s: 1 / 0)
        self.assertEqual(sm.sector_hint("AAPL"), blank)
        sm._fund = None
        self.assertEqual(sm.sector_hint("AAPL"), blank)


class _HintStub:
    def __init__(self, table):
        self.table = table
        self.asks = []

    def sector_hint(self, symbol):
        self.asks.append(symbol)
        sec, ind = self.table.get(symbol, (None, None))
        return {"sector": sec, "industry": ind, "sic": None}


class TestTheScannerFillsOnlyABlank(unittest.TestCase):
    def setUp(self):
        self._real = wt._sector_map
        self.hint = _HintStub({"AAPL": (sm.TECH, "Electronic Computers")})
        wt._sector_map = self.hint

    def tearDown(self):
        wt._sector_map = self._real

    def test_yahoos_answer_is_kept_and_the_sec_is_not_asked(self):
        row = {"symbol": "AAPL", "sector": "Communication Services", "industry": "Internet"}
        wt._fill_sector(row, "AAPL", {"sector": sm.TECH})
        self.assertEqual(row["sector"], "Communication Services")
        self.assertEqual(self.hint.asks, [])

    def test_a_quiet_yahoo_takes_last_scans_label_first(self):
        row = {"symbol": "AAPL", "sector": None, "industry": None}
        wt._fill_sector(row, "AAPL", {"sector": "Communication Services", "industry": "Internet"})
        self.assertEqual(row["sector"], "Communication Services")
        self.assertEqual(row["industry"], "Internet")
        self.assertEqual(row["sector_source"], "prior")
        self.assertEqual(self.hint.asks, [], "a remembered label needs no SEC call")

    def test_a_quiet_yahoo_with_no_memory_takes_the_sec(self):
        row = {"symbol": "AAPL", "sector": None, "industry": None}
        wt._fill_sector(row, "AAPL", None)
        self.assertEqual(row["sector"], sm.TECH)
        self.assertEqual(row["industry"], "Electronic Computers")
        self.assertEqual(row["sector_source"], "sec")

    def test_a_name_the_sec_does_not_know_stays_blank(self):
        row = {"symbol": "ZZZZ", "sector": None}
        wt._fill_sector(row, "ZZZZ", {})
        self.assertIsNone(row["sector"])
        self.assertNotIn("sector_source", row)

    def test_a_raising_hint_leaves_the_row_alone(self):
        wt._sector_map = types.SimpleNamespace(sector_hint=lambda s: 1 / 0)
        row = {"symbol": "AAPL", "sector": None}
        wt._fill_sector(row, "AAPL", None)
        self.assertIsNone(row["sector"])

    def test_no_module_leaves_the_row_alone(self):
        wt._sector_map = None
        row = {"symbol": "AAPL", "sector": None}
        wt._fill_sector(row, "AAPL", None)
        self.assertIsNone(row["sector"])


class TestTheAnalystBoardFillsTheSameBlank(unittest.TestCase):
    """analyst_board._enrich files a name with no Yahoo sector under
    "Unknown" on the sector rollups. With Yahoo and Schwab both off, the
    SEC's label must reach the row."""

    def setUp(self):
        self._saved = (analyst_board._sector_map, analyst_board._YF_OK, analyst_board._SCHWAB_OK)
        analyst_board._YF_OK = False
        analyst_board._SCHWAB_OK = False

    def tearDown(self):
        analyst_board._sector_map, analyst_board._YF_OK, analyst_board._SCHWAB_OK = self._saved

    def test_sec_fills_the_sector_when_yahoo_is_off(self):
        analyst_board._sector_map = _HintStub({"AAPL": (sm.TECH, "Electronic Computers")})
        out = analyst_board._enrich("AAPL")
        self.assertEqual(out["sector"], sm.TECH)
        self.assertEqual(out["sector_source"], "sec")

    def test_an_unknown_filer_stays_none(self):
        analyst_board._sector_map = _HintStub({})
        out = analyst_board._enrich("ZZZZ")
        self.assertIsNone(out["sector"])
        self.assertNotIn("sector_source", out)


if __name__ == "__main__":
    unittest.main()
