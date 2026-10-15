"""Tests for short_trend.py: short-interest trend alerts for high-debt stocks (v5.40)."""

import tempfile
import unittest
from datetime import date

import short_trend as ST

SETTS = ["2026-07-15", "2026-07-31", "2026-08-14", "2026-08-31", "2026-09-15", "2026-09-30"]


def finra(shorts):
    """FINRA-shaped rows for one report: {SYM: short shares}."""
    return {s: {"symbol": s, "short": v, "days_to_cover": 2.0} for s, v in shorts.items()}


# Short shares per report, oldest first.
PATHS = {
    "JUMP": [50, 50, 50, 50, 50, 57],          # +14% in the newest report
    "BUILD": [40, 42, 41, 44, 46, 48],         # three rises, +17% over them
    "SMALLBUILD": [40, 42, 41, 42, 43, 44],    # three rises but only +7%
    "FALL": [60, 58, 55, 52, 50, 45],
    "THIN": [1, 1, 1, 1, 1, 2],                # doubles, but 0.2% of the float
    "LOWDEBT": [50, 50, 50, 50, 50, 60],       # a jump on a stock without debt risk
}
ROWS = [
    {"symbol": "JUMP", "debt_risk": "High", "float_shares": 300e6, "debt_reasons": ["Net debt is 6.0x EBITDA."]},
    {"symbol": "BUILD", "debt_risk": "Elevated", "float_shares": 400e6},
    {"symbol": "SMALLBUILD", "debt_risk": "High", "float_shares": 400e6},
    {"symbol": "FALL", "debt_risk": "High", "float_shares": 300e6},
    {"symbol": "THIN", "debt_risk": "High", "float_shares": 1000e6},
    {"symbol": "LOWDEBT", "debt_risk": "Low", "float_shares": 300e6},
]


def report(i):
    return finra({s: v[i] * 1e6 for s, v in PATHS.items()})


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pushes = []
        self.fetched = []
        self.published = set(SETTS)
        self.rows = [dict(r) for r in ROWS]
        self.today = date(2026, 10, 15)
        self.config()

    def config(self):
        def fetch(st):
            self.fetched.append(st)
            return report(SETTS.index(st)) if st in self.published else {}
        ST.configure(data_dir=self.tmp, fetch_fn=fetch,
                     settlements_fn=lambda today: sorted(SETTS, reverse=True),
                     public_on_fn=lambda st: "2026-10-12" if st == "2026-09-30" else st,
                     board_fn=lambda: self.rows,
                     notify_fn=lambda t, m: self.pushes.append((t, m)),
                     today_fn=lambda: self.today)


class Trend(unittest.TestCase):
    def test_change_run_and_points(self):
        t = ST.trend([(s, v * 1e6, 2.0) for s, v in zip(SETTS, PATHS["BUILD"])])
        self.assertAlmostEqual(4.3, t["change_pct"], places=1)
        self.assertEqual(3, t["rises"])
        self.assertAlmostEqual(17.1, t["run_pct"], places=1)
        self.assertEqual("2026-08-14", t["run_from"])
        self.assertEqual(6, len(t["points"]))

    def test_a_fall_has_no_run(self):
        t = ST.trend([(s, v, None) for s, v in zip(SETTS, PATHS["FALL"])])
        self.assertEqual(0, t["rises"])
        self.assertEqual(0.0, t["run_pct"])
        self.assertLess(t["change_pct"], 0)

    def test_one_report_is_no_trend(self):
        self.assertIsNone(ST.trend([("2026-09-30", 5e6, 1.0)]))
        self.assertIsNone(ST.trend([]))


class Judge(unittest.TestCase):
    def t(self, sym):
        return ST.trend([(s, v * 1e6, 2.0) for s, v in zip(SETTS, PATHS[sym])])

    def test_the_rules(self):
        by = {r["symbol"]: ST.judge(r, self.t(r["symbol"])) for r in ROWS}
        self.assertEqual("jump", by["JUMP"]["kind"])
        self.assertEqual("build", by["BUILD"]["kind"])
        self.assertIsNone(by["SMALLBUILD"], "three rises adding up to 7% is not a build")
        self.assertIsNone(by["FALL"])
        self.assertIsNone(by["THIN"], "0.2% of the float short is noise")
        self.assertIsNone(by["LOWDEBT"], "only High and Elevated debt")

    def test_the_sentence(self):
        a = ST.judge(ROWS[0], self.t("JUMP"))
        self.assertIn("Shorts are piling into JUMP, a High-debt stock", a["text"])
        self.assertIn("+14% to 57.0M shares in the Sep 30 report", a["text"])
        self.assertIn("19.0% of its float", a["text"])
        self.assertIn("2.0 days to cover", a["text"])
        self.assertIn("Net debt is 6.0x EBITDA.", a["text"])
        b = ST.judge(ROWS[1], self.t("BUILD"))
        self.assertIn("rising 3 reports in a row (+17% since the Aug 14 report)", b["text"])

    def test_without_a_float_the_profile_short_share_stands_in(self):
        row = {"symbol": "JUMP", "debt_risk": "High", "short_pct": 1.5}
        self.assertIsNone(ST.judge(row, self.t("JUMP")), "1.5% short is under the floor")
        self.assertIsNotNone(ST.judge(dict(row, short_pct=12.0), self.t("JUMP")))
        self.assertIsNone(ST.judge({"symbol": "JUMP", "debt_risk": "High"}, self.t("JUMP")),
                          "no way to tell how much of the float is short")


class Check(Harness):
    def test_the_first_check_fetches_alerts_and_pushes_once(self):
        r = ST.check()
        self.assertEqual(6, r["fetched"])
        self.assertEqual("2026-09-30", r["settlement"])
        self.assertEqual(2, r["new"])
        self.assertTrue(r["pushed"])
        self.assertEqual(1, len(self.pushes))
        self.assertIn("Shorts piling into 2 high-debt stocks", self.pushes[0][0])
        self.assertIn("JUMP +14%", self.pushes[0][1])
        snap = ST.snapshot()
        self.assertEqual(["JUMP", "BUILD"], [a["symbol"] for a in snap["alerts"]], "High before Elevated")
        again = ST.check()
        self.assertEqual(0, again["new"])
        self.assertEqual(0, again["fetched"], "reports on file are not fetched again")
        self.assertEqual(1, len(self.pushes), "one push per report")

    def test_it_survives_a_restart(self):
        ST.check()
        self.config()
        self.assertEqual("2026-09-30", ST.latest_settlement())
        self.assertEqual(0, ST.check()["new"])
        self.assertEqual(1, len(self.pushes))
        self.assertEqual(2, len(ST.snapshot()["alerts"]))

    def test_it_waits_for_the_watchlist_scan(self):
        self.rows = [{"symbol": r["symbol"]} for r in ROWS]
        r = ST.check()
        self.assertFalse(r["ok"])
        self.assertEqual([], self.pushes)
        self.rows = [dict(x) for x in ROWS]
        self.assertEqual(2, ST.check()["new"], "the report is not lost while it waited")

    def test_an_unpublished_report_is_tried_again(self):
        self.published = set(SETTS[:-1])
        ST.check()
        self.assertEqual("2026-09-15", ST.latest_settlement())
        self.published = set(SETTS)
        ST.check()
        self.assertEqual("2026-09-30", ST.latest_settlement())

    def test_a_stale_report_is_listed_but_not_pushed(self):
        self.today = date(2026, 11, 1)
        r = ST.check()
        self.assertEqual(2, r["new"])
        self.assertFalse(r["pushed"])
        self.assertEqual([], self.pushes)

    def test_only_the_kept_reports_stay(self):
        ST.check()
        self.assertEqual(ST.SETTLEMENTS_KEEP, len(ST.snapshot()["reports"]))

    def test_the_watchlist_and_card_fields(self):
        ST.check()
        rows = ST.board_fields([{"symbol": "BUILD"}, {"symbol": "NOPE"}])
        self.assertEqual(3, rows[0]["si_rises"])
        self.assertAlmostEqual(17.1, rows[0]["si_run"], places=1)
        self.assertNotIn("si_chg", rows[1])
        snap = ST.snapshot("jump")
        self.assertEqual("JUMP", snap["symbol"])
        self.assertEqual(6, len(snap["trend"]["points"]))
        self.assertEqual(["JUMP"], [a["symbol"] for a in snap["symbol_alerts"]])

    def test_no_sources_no_crash(self):
        ST.configure(data_dir=None)
        self.assertFalse(ST.check()["ok"])
        self.assertEqual([], ST.snapshot()["alerts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
