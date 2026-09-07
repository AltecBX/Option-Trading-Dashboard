"""Guards for hf_replay.py — replaying the four weekly verdicts.

The module's whole claim is that a replayed week is the answer the board
would have published that week. Most of these tests try to break that
claim: by leaking a fact that was not public yet, by rebuilding a verdict
from fewer inputs than decide it, and by drifting from the live functions.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path

import hf_pulse as P
import hf_replay as R


def series(n: int, *, end=date(2026, 9, 1), net0=100000, step=400) -> list[dict]:
    """Newest first, one row a week, the shape the CFTC provider returns."""
    return [{"date": (end - timedelta(weeks=i)).isoformat(),
             "lev_net": net0 - i * step, "lev_gross": 200000 + i * 300,
             "lev_long": 90000 - i * step, "lev_short": 110000 + i * step}
            for i in range(n)]


def book(n=60, markets=("sp500", "nasdaq", "russell", "djia")) -> dict:
    return {k: {"key": k, "market": k.upper(), "as_of": "2026-09-01", "series": series(n)}
            for k in markets}


class Weeks(unittest.TestCase):
    def test_week_key_and_end_agree(self):
        self.assertEqual(R.week_key(date(2026, 9, 1)), "2026-W36")
        self.assertEqual(R.week_end("2026-W36"), date(2026, 9, 6))
        self.assertEqual(R.week_key(R.week_end("2026-W36")), "2026-W36")

    def test_a_broken_week_string_is_not_a_crash(self):
        for bad in ("", "nonsense", "2026-W", None, "2026-W99"):
            self.assertIsNone(R.week_end(bad), repr(bad))

    def test_weeks_come_from_the_anchor_contract(self):
        # A week the CFTC never published is not a week the board could
        # have answered, so the calendar does not invent one.
        ws = R.weeks_in(book(10))
        self.assertEqual(len(ws), 10)
        self.assertEqual(ws, sorted(ws), "oldest first")


class Truncation(unittest.TestCase):
    def test_nothing_after_the_week_survives(self):
        cut = R.truncate_cftc(book(60), "2026-W20")
        end = R.week_end("2026-W20")
        for m in cut.values():
            for row in m["series"]:
                self.assertLessEqual(date.fromisoformat(row["date"]), end)

    def test_as_of_moves_with_the_window(self):
        # Leaving the market's original as_of is the bug that put 204
        # reconstructed crowding rows in a single week.
        cut = R.truncate_cftc(book(60), "2026-W20")
        for key, m in cut.items():
            self.assertEqual(m["as_of"], m["series"][0]["date"], key)
            self.assertNotEqual(m["as_of"], "2026-09-01")

    def test_every_market_is_cut_to_the_same_week(self):
        # The verdicts read four markets in one answer; two moments mixed
        # into one verdict is not a verdict the board ever reached.
        cut = R.truncate_cftc(book(60), "2026-W20")
        self.assertEqual(len({R.week_key(date.fromisoformat(m["as_of"])) for m in cut.values()}), 1)

    def test_a_market_with_no_history_that_far_back_drops_out(self):
        b = book(60)
        b["newcomer"] = {"key": "newcomer", "market": "NEW", "as_of": "2026-09-01",
                         "series": series(3)}
        cut = R.truncate_cftc(b, "2026-W20")
        self.assertNotIn("newcomer", cut)
        self.assertIn("sp500", cut)


class WhatWasPublicThatWeek(unittest.TestCase):
    def test_short_interest_is_keyed_on_publication_not_settlement(self):
        # A settlement inside week W is not public for another eight
        # business days. Reading it would hand the replay a number nobody
        # could have seen.
        setts = [{"settlement": "2026-05-15", "public_on": "2026-05-27", "total": 1.0},
                 {"settlement": "2026-05-29", "public_on": "2026-06-10", "total": 2.0}]
        got = R.short_interest_for(setts, "2026-W22")   # ends 2026-05-31
        self.assertEqual(got["settlement"], "2026-05-15",
                         "the later settlement was not public yet")

    def test_the_newest_published_reading_wins(self):
        setts = [{"settlement": "2026-05-15", "public_on": "2026-05-27", "total": 1.0},
                 {"settlement": "2026-05-29", "public_on": "2026-06-10", "total": 2.0}]
        self.assertEqual(R.short_interest_for(setts, "2026-W25")["settlement"], "2026-05-29")

    def test_nothing_published_yet_is_none_not_the_oldest_row(self):
        setts = [{"settlement": "2026-05-15", "public_on": "2026-05-27"}]
        self.assertIsNone(R.short_interest_for(setts, "2026-W10"))

    def test_short_volume_window_matches_the_live_gatherer(self):
        daily = {(date(2026, 9, 4) - timedelta(days=i)).isoformat():
                 40.0 + i * 0.1 for i in range(60)}
        got = R.short_volume_for(daily, "2026-W36", days=20)
        self.assertEqual(got["n_days"], 20)
        self.assertEqual(len(got["history"]), 20)
        self.assertLessEqual(date.fromisoformat(got["session"]), R.week_end("2026-W36"))
        self.assertEqual(got["history"][0], got["share"], "newest first, like the live shape")

    def test_short_volume_change_is_the_step_not_the_level(self):
        daily = {"2026-09-04": 47.5, "2026-09-03": 47.0, "2026-09-02": 46.0}
        got = R.short_volume_for(daily, "2026-W36", days=3)
        self.assertAlmostEqual(got["change"], 0.5)

    def test_a_half_filled_short_volume_window_is_refused(self):
        # The flat band comes from the spread of this input's own history,
        # so three sessions and twenty do not merely differ in confidence —
        # they can hand build_verdict a different band, and so a different
        # answer wearing the same name.
        daily = {"2026-09-04": 47.5, "2026-09-03": 47.0, "2026-09-02": 46.0}
        self.assertIsNone(R.short_volume_for(daily, "2026-W36", days=20))

    def test_a_half_filled_flow_window_is_refused(self):
        # A total summed over three sessions is not the five-session total
        # the board adds up.
        daily = {"2026-09-04": 100.0, "2026-09-03": 100.0, "2026-09-02": 100.0}
        self.assertIsNone(R.etf_flows_for(daily, "2026-W36", sessions=5))

    def test_etf_flows_sum_the_same_five_sessions(self):
        daily = {(date(2026, 9, 4) - timedelta(days=i)).isoformat(): 100.0
                 for i in range(30)}
        got = R.etf_flows_for(daily, "2026-W36", sessions=5)
        self.assertEqual(got["n_days"], 5)
        self.assertAlmostEqual(got["net_all"], 500.0)

    def test_a_future_session_never_enters_the_window(self):
        daily = {(date(2026, 9, 4) - timedelta(days=i)).isoformat(): 100.0 for i in range(5)}
        daily["2026-09-30"] = 999.0
        got = R.etf_flows_for(daily, "2026-W36", sessions=5)
        self.assertAlmostEqual(got["net_all"], 500.0, msg="September 30 had not happened")
        self.assertLessEqual(date.fromisoformat(got["as_of"]), R.week_end("2026-W36"))


class InputCompleteOrNotAtAll(unittest.TestCase):
    """The rule the whole module rests on."""

    def setUp(self):
        self.b = book(60)

    def test_leverage_and_longs_need_nothing_but_the_futures(self):
        out = R.replay_week("2026-W30", self.b)
        self.assertIn("leverage", out["verdicts"])
        self.assertIn("longs", out["verdicts"])

    def test_shorts_is_skipped_without_both_finra_readings(self):
        out = R.replay_week("2026-W30", self.b)
        self.assertNotIn("shorts", out["verdicts"])
        self.assertIn("short interest", out["skipped"]["shorts"])
        self.assertIn("short-volume share", out["skipped"]["shorts"])

    def test_shorts_is_still_skipped_with_only_one_of_the_two(self):
        # Half the evidence is not the same answer with a wider error bar;
        # it is a different answer wearing the same name.
        setts = [{"settlement": "2026-07-15", "public_on": "2026-07-24",
                  "total": 1.4e10, "change": 2e8, "n_symbols": 5900}]
        out = R.replay_week("2026-W30", self.b, settlements=setts)
        self.assertNotIn("shorts", out["verdicts"])
        self.assertIn("short-volume share", out["skipped"]["shorts"])
        self.assertNotIn("short interest", out["skipped"]["shorts"])

    def test_shorts_appears_once_both_are_there(self):
        setts = [{"settlement": "2026-07-15", "public_on": "2026-07-24",
                  "total": 1.4e10, "change": 2e8, "n_symbols": 5900}]
        daily = {(date(2026, 7, 24) - timedelta(days=i)).isoformat():
                 45.0 + (i % 3) * 0.2 for i in range(40)}
        out = R.replay_week("2026-W30", self.b, settlements=setts, short_volume_daily=daily)
        self.assertIn("shorts", out["verdicts"])

    def test_exposure_is_skipped_without_etf_creations(self):
        out = R.replay_week("2026-W30", self.b)
        self.assertNotIn("exposure", out["verdicts"])
        self.assertIn("ETF creations", out["skipped"]["exposure"])

    def test_a_replayed_verdict_carries_the_same_input_count_as_the_live_one(self):
        # The strongest form of the rule: the replay is only trustworthy if
        # it was built from as many inputs as the board builds from.
        daily = {(date(2026, 7, 24) - timedelta(days=i)).isoformat(): 1000.0
                 for i in range(20)}
        out = R.replay_week("2026-W30", self.b, etf_flow_daily=daily)
        cut = R.truncate_cftc(self.b, "2026-W30")
        live = P.exposure(cut, R.etf_flows_for(daily, "2026-W30"))
        self.assertEqual(out["n_inputs"]["exposure"], len(live["inputs"]))
        self.assertEqual(out["verdicts"]["exposure"], live["verdict"])

    def test_a_week_before_the_anchor_existed_answers_nothing(self):
        out = R.replay_week("2019-W02", self.b)
        self.assertEqual(out["verdicts"], {})
        self.assertEqual(set(out["skipped"]), set(R.QUESTIONS))
        for why in out["skipped"].values():
            self.assertIn("S&P 500", why)


class TheRealFunctions(unittest.TestCase):
    def test_the_replay_calls_hf_pulse_and_does_not_reimplement_it(self):
        # If this module ever grew its own copy of the verdict maths it
        # would drift from the board the first time either changed, and
        # would then be grading a board that does not exist.
        src = Path("hf_replay.py").read_text(encoding="utf-8")
        for fn in ("P.exposure(", "P.leverage(", "P.longs_and_shorts("):
            self.assertIn(fn, src)
        for word in ("def exposure", "def leverage", "def longs_and_shorts", "def build_verdict"):
            self.assertNotIn(word, src, "the verdict maths must live in hf_pulse only")

    def test_it_is_pure(self):
        import re
        src = Path("hf_replay.py").read_text(encoding="utf-8")
        for bad in (r"(?<![A-Za-z_.])open\s*\(", r"requests\.", r"urlopen",
                    r"datetime\.now", r"date\.today", r"Path\("):
            self.assertIsNone(re.search(bad, src), f"hf_replay must not {bad}")

    def test_the_same_inputs_replay_the_same_record(self):
        a = R.build(book(40))
        b = R.build(book(40))
        self.assertEqual(a, b)


class TheRecord(unittest.TestCase):
    def setUp(self):
        self.card = R.build(book(60), min_history=12)

    def test_the_earliest_week_has_history_behind_it(self):
        self.assertEqual(self.card["n_weeks"], 60 - 12)

    def test_readings_are_the_shape_the_grader_already_eats(self):
        import hf_grade as G
        rows = self.card["readings"]
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(set(r), {"week", "verdicts"})
        # It must actually pass through the grader untouched.
        closes = {"SPY": {r["week"]: 100.0 + i for i, r in enumerate(rows)}}
        out = G.grade_verdicts(rows, closes, min_n=2)
        self.assertIn("by_question", out)

    def test_a_week_with_no_answers_is_not_offered_to_the_grader(self):
        rows = R.readings([{"week": "2026-W01", "verdicts": {}},
                           {"week": "2026-W02", "verdicts": {"longs": "ADDING"}}])
        self.assertEqual([r["week"] for r in rows], ["2026-W02"])

    def test_coverage_reports_each_question_separately(self):
        cov = self.card["coverage"]["by_question"]
        self.assertGreater(cov["leverage"]["n_weeks"], 0)
        self.assertGreater(cov["longs"]["n_weeks"], 0)
        self.assertEqual(cov["shorts"]["n_weeks"], 0)
        self.assertEqual(cov["exposure"]["n_weeks"], 0)

    def test_coverage_says_why_a_question_stopped(self):
        cov = self.card["coverage"]["by_question"]["shorts"]
        self.assertEqual(cov["n_skipped"], self.card["n_weeks"])
        self.assertTrue(cov["why_skipped"])
        self.assertIn("short interest", cov["why_skipped"][0]["reason"])
        self.assertEqual(cov["why_skipped"][0]["weeks"], self.card["n_weeks"])

    def test_the_four_questions_do_not_share_a_floor(self):
        # leverage reaches the whole futures series; shorts only reaches as
        # far back as FINRA was read. One depth number would hide that.
        setts = [{"settlement": "2026-07-15", "public_on": "2026-07-24",
                  "total": 1.4e10, "change": 2e8, "n_symbols": 5900}]
        daily = {(date(2026, 9, 4) - timedelta(days=i)).isoformat(): 45.0
                 for i in range(60)}
        card = R.build(book(60), settlements=setts, short_volume_daily=daily, min_history=12)
        cov = card["coverage"]["by_question"]
        self.assertGreater(cov["leverage"]["n_weeks"], cov["shorts"]["n_weeks"])
        self.assertGreater(cov["shorts"]["n_weeks"], 0)

    def test_the_limitations_say_it_is_a_replay_not_an_archive(self):
        blob = " ".join(self.card["limitations"]).lower()
        self.assertIn("not answers it published at the time", blob)
        self.assertIn("replay, not an archive", blob)
        self.assertIn("do not reach equally far back", blob)

    def test_an_empty_book_is_an_empty_record_not_a_crash(self):
        card = R.build({})
        self.assertEqual(card["n_weeks"], 0)
        self.assertEqual(card["readings"], [])
        self.assertEqual(card["coverage"]["n_weeks_walked"], 0)

    def test_a_series_shorter_than_the_floor_replays_nothing(self):
        self.assertEqual(R.build(book(8), min_history=12)["n_weeks"], 0)


class NoLeakage(unittest.TestCase):
    def test_truncating_later_weeks_away_does_not_change_an_earlier_answer(self):
        """The property that makes the whole record point-in-time: what the
        board said in week W cannot depend on anything after W."""
        full = R.build(book(60), min_history=12)
        short = R.build({k: {**m, "series": m["series"][20:]}
                         for k, m in book(60).items()}, min_history=12)
        by_week = {r["week"]: r["verdicts"] for r in full["weeks"]}
        for r in short["weeks"]:
            if r["week"] in by_week:
                self.assertEqual(r["verdicts"], by_week[r["week"]], r["week"])


if __name__ == "__main__":
    unittest.main()
