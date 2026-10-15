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
                     notify_fn=lambda t, m: (self.pushes.append((t, m)), {"ok": True})[1],
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
        self.assertIn("Shorts are piling into BUILD, an Elevated-debt stock", b["text"])
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



class ShortedDebt(Harness):
    """v5.41: the Live Scanner's shorted high-debt list."""

    def board(self):
        # JUMP: High debt, 19% short, alerted. BUILD: Elevated, 12% short,
        # alerted. HEAVY: High, 8% short (Yahoo's figure, no FINRA row), not
        # alerted. LIGHTSHORT: High but 3% short. LOWDEBT: heavily short, Low debt.
        return [dict(r) for r in ROWS] + [
            {"symbol": "HEAVY", "debt_risk": "High", "short_pct": 8.0, "last": 20.0, "change": -1.0,
             "debt_reasons": ["Burning cash."]},
            {"symbol": "LIGHTSHORT", "debt_risk": "High", "short_pct": 3.0},
            {"symbol": "LOWDEBT2", "debt_risk": "Low", "short_pct": 30.0},
        ]

    def test_who_is_on_it_and_in_what_order(self):
        ST.check()
        got = ST.shorted_debt(self.board(), {"JUMP": {"last": 12.5, "change_pct": -4.2}})
        syms = [r["symbol"] for r in got["rows"]]
        self.assertEqual(["JUMP", "BUILD", "FALL", "SMALLBUILD", "HEAVY"], syms,
                         "alerted first (High before Elevated), then the most shorted: 15%, 11%, 8%")
        self.assertNotIn("LIGHTSHORT", syms, "3% short is under the 5% line")
        self.assertNotIn("LOWDEBT2", syms, "heavily shorted but not indebted")
        self.assertEqual("2026-09-30", got["settlement"])
        j = got["rows"][0]
        self.assertAlmostEqual(-4.2, j["change_pct"], msg="today's move comes from the scanner")
        self.assertAlmostEqual(19.0, j["short_pct"], places=1, msg="FINRA's shares over the float")
        self.assertIn("Shorts are piling into JUMP", j["alert"])
        h = got["rows"][4]
        self.assertAlmostEqual(8.0, h["short_pct"], msg="Yahoo's figure when FINRA has no row")
        self.assertAlmostEqual(-1.0, h["change_pct"], msg="the board's move when the scanner has none")
        self.assertIsNone(h["si_chg"])
        self.assertEqual("Burning cash.", h["reason"])

    def test_without_reports_it_still_lists_from_the_profile(self):
        got = ST.shorted_debt(self.board(), None, store={"settlements": {}, "alerts": []})
        self.assertEqual(["HEAVY"], [r["symbol"] for r in got["rows"]],
                         "only HEAVY has a short figure without FINRA's reports")
        self.assertIsNone(got["settlement"])

    def test_the_list_is_capped(self):
        rows = [{"symbol": f"S{i:03d}", "debt_risk": "High", "short_pct": 6.0 + i} for i in range(60)]
        got = ST.shorted_debt(rows, None, store={"settlements": {}, "alerts": []})
        self.assertEqual(ST.SHORTED_KEEP, len(got["rows"]))
        self.assertEqual(60, got["total"])
        self.assertEqual("S059", got["rows"][0]["symbol"])



class Codex428b(Harness):
    """Codex, #428 (second review)."""

    def test_a_failed_push_is_tried_again(self):
        results = [{"ok": False, "error": "ntfy timed out"}, {"ok": True}]

        def notify(t, m):
            self.pushes.append((t, m))
            return results.pop(0)
        ST._NOTIFY_FN = notify
        r = ST.check()
        self.assertEqual(2, r["new"])
        self.assertFalse(r["pushed"], "the provider said it failed")
        r = ST.check()
        self.assertTrue(r["pushed"], "the next check tries the push again")
        self.assertEqual(2, len(self.pushes))
        self.assertTrue(ST.check().get("pushed") in (False, None))
        self.assertEqual(2, len(self.pushes), "and stops once it went through")
        self.assertEqual(2, len(ST.snapshot()["alerts"]), "the alerts are raised once")

    def test_no_push_provider_is_not_a_delivery(self):
        ST._NOTIFY_FN = lambda t, m: None          # push not configured
        self.assertFalse(ST.check()["pushed"])

    def test_a_board_still_scanning_is_not_used(self):
        full = [dict(x) for x in ROWS]
        self.board = {"rows": full[:1], "status": {"scanning": True, "last_scan": None}}
        ST._BOARD_FN = lambda: self.board
        r = ST.check()
        self.assertFalse(r["ok"])
        self.assertIn("scan", r["reason"])
        self.board = {"rows": full, "status": {"scanning": False, "last_scan": "2026-10-15T13:00:00"}}
        self.assertEqual(2, ST.check()["new"], "BUILD was in a later chunk and is not lost")


class Codex428c(Harness):
    """Codex, #428 (third review): a report is judged on its full history."""

    def test_a_report_waits_for_the_ones_before_it(self):
        self.published = set(SETTS) - {"2026-08-31"}       # an older fetch fails
        r = ST.check()
        self.assertFalse(r["ok"])
        self.assertIn("earlier", r["reason"])
        self.assertEqual(["2026-08-31"], r["missing"])
        self.assertEqual([], self.pushes)
        self.assertEqual([], ST.snapshot()["alerts"])
        self.published = set(SETTS)                         # and comes back
        r = ST.check()
        self.assertEqual(1, r["fetched"])
        self.assertEqual(2, r["new"], "BUILD's three rises need the missing report")
        self.assertTrue(r["pushed"])
        self.assertIn("BUILD", self.pushes[0][1])

    def test_it_does_not_wait_forever(self):
        self.published = set(SETTS) - {"2026-08-31"}
        self.today = date(2026, 10, 16)                     # four days after it went public
        r = ST.check()
        self.assertTrue(r["ok"])
        self.assertEqual(["JUMP"], [a["symbol"] for a in ST.snapshot()["alerts"]],
                         "judged with what is on file")
        self.assertTrue(r["pushed"], "still news")

    def test_an_unknown_schedule_waits_too(self):
        def broken(today):
            raise RuntimeError("calendar unavailable")
        ST.check()                                          # the store is full
        ST._STORE["notified"] = []
        ST._STORE["alerts"] = []
        ST._SETTLEMENTS_FN = broken
        r = ST.check()
        self.assertFalse(r["ok"], "cannot tell what is missing")
        self.today = date(2026, 10, 16)
        self.assertTrue(ST.check()["ok"])


class SettledBoard(unittest.TestCase):
    """Codex, #428: the scanner, its shorted list, the on-screen list and the
    report check read the watchlist as of its last finished scan
    (watchlist_table.settled_rows, tested in test_watchlist_settled)."""

    def test_the_server_reads_it_everywhere(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()
        fn = src[src.index("def _scanner_board_rows"):]
        fn = fn[:fn.index("\n\n\n")]
        self.assertIn("_wltable.settled_rows()", fn)
        sh = src[src.index("def _shorted_live_rows"):]
        sh = sh[:sh.index("\n\n\n")]
        self.assertIn("shorted_debt(_scanner_board_rows())", sh)
        api = src[src.index('snap["short_debt"] = _strend.shorted_debt(') - 300:
                  src.index('snap["short_debt"] = _strend.shorted_debt(')]
        self.assertIn("board_rows = _scanner_board_rows()", api)
        cfg = src[src.index("_strend.configure("):]
        cfg = cfg[:cfg.index("\n    )\n")]
        self.assertIn("rows=_wltable.settled_rows()", cfg)


class Codex428d(unittest.TestCase):
    """Codex, #428 (fourth review): the wiring."""

    def src(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent / "options_dashboard.py").read_text()

    def test_reports_are_kept_only_when_whole(self):
        s = self.src()
        cfg = s[s.index("_strend.configure("):]
        cfg = cfg[:cfg.index("\n    )\n")]
        self.assertIn("finra_short_interest(st, complete_only=True)", cfg)

    def test_the_scanner_quotes_the_finished_board(self):
        s = self.src()
        live = s[s.index("_live.configure("):]
        live = live[:live.index("\n    )\n")]
        self.assertIn("universe_fn=lambda: _scanner_board_rows()", live)
        fn = s[s.index("def _scanner_board_rows"):]
        fn = fn[:fn.index("\n\n\n")]
        self.assertIn("settled_rows(", fn)

    def test_an_incomplete_fetch_is_not_kept(self):
        tmp = tempfile.mkdtemp()
        answers = {st: (None if st == "2026-08-31" else report(SETTS.index(st))) for st in SETTS}
        ST.configure(data_dir=tmp, fetch_fn=lambda st: answers[st],
                     settlements_fn=lambda today: sorted(SETTS, reverse=True),
                     public_on_fn=lambda st: "2026-10-12" if st == "2026-09-30" else st,
                     board_fn=lambda: [dict(r) for r in ROWS],
                     notify_fn=lambda t, m: {"ok": True}, today_fn=lambda: date(2026, 10, 15))
        r = ST.check()
        self.assertEqual(["2026-08-31"], r["missing"])
        answers["2026-08-31"] = report(SETTS.index("2026-08-31"))
        self.assertEqual(2, ST.check()["new"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
