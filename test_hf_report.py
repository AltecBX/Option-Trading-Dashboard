"""test_hf_report.py (v4.87) — guards for the weekly report.

The report's one job is to carry other modules' findings across without
changing them, and to keep the two layers apart while doing it. So the guards
are mostly about restraint:

  * no figure appears that the board did not compute,
  * no aggregate row ever carries a fund's name,
  * "nothing to compare against" and "nothing changed" are different answers,
  * disagreement survives assembly instead of being averaged away.

Run:  python3 test_hf_report.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import hf_press as PRESS
import hf_pulse as P
import hf_report as R
import hf_sources as S


def question(name, verdict, *, level="MODERATE", classes=2, inputs=None, conflicts=None,
             streak_weeks=3, missing=None):
    return {"question": name, "verdict": verdict,
            "confidence": {"level": level, "classes": classes, "agree": 2, "disagree": 0,
                           "direction": 1, "why": "2 independent evidence classes agree"},
            "streak": {"weeks": streak_weeks, "direction": 1,
                       "word": f"{streak_weeks} consecutive weeks"},
            "persistence": {"2": {"same": 2, "of": 2, "text": "2 of the last 2 weeks"},
                            "4": {"same": 3, "of": 4, "text": "3 of the last 4 weeks"},
                            "8": {"same": 5, "of": 8, "text": "5 of the last 8 weeks"},
                            "12": {"same": 7, "of": 12, "text": "7 of the last 12 weeks"}},
            "inputs": inputs if inputs is not None else [
                {"key": "k1", "label": "S&P 500 futures, net position", "class": S.REGULATORY,
                 "value": -1.0, "change": -10.0, "direction": -1, "as_of": "2026-09-01",
                 "weight": 1.0}],
            "conflicts": conflicts or [], "missing": missing or [], "note": None}


def board(week="2026-W36", exposure="REDUCING", sector_verdicts=None, crowded=("S&P 500",)):
    sector_verdicts = sector_verdicts or {"Technology": "ADDING", "Energy": "REDUCING"}
    rows = [{"sector": s, "verdict": v, "confidence": {"level": "LOW", "classes": 1},
             "inputs_available": 3, "has_futures": s != "Technology",
             "move_size": 1.2, "move_size_text": "1.2× its typical weekly move",
             "streak": {"weeks": 2, "direction": 1, "word": "2 consecutive weeks"},
             "conflicts": [], "inputs": []}
            for s, v in sector_verdicts.items()]
    return {
        "version": "1.0.0", "week": week, "cftc_as_of": "2026-09-01",
        "dates": {"cftc_as_of": "September 1, 2026"},
        "exposure": question("Are hedge funds increasing or reducing exposure?", exposure),
        "leverage": question("Are hedge funds increasing or reducing leverage?", "RISING"),
        "longs": question("Are they reducing longs?", "ADDING"),
        "shorts": question("Are they adding shorts, or covering?", "MIXED"),
        "sectors": {"rows": rows, "most_bought": [r for r in rows if r["verdict"] == "ADDING"],
                    "most_sold": [r for r in rows if r["verdict"] == "REDUCING"],
                    "no_futures": ["Technology"], "ranked_by": "in their own terms"},
        "crowding": {"markets": [], "crowded": [{"market": m} for m in crowded],
                     "decrowding": [], "rule": "two facts, never one twice", "names": []},
        "sources": {"cftc_markets": 12}, "unavailable": [], "evidence_classes": [S.REGULATORY],
    }


def funds(states=("FILED SINCE", "UNKNOWN", "UNKNOWN"), filings=None, keys=None):
    keys = keys or [f"m{i}" for i in range(len(states))]
    managers = [{"key": k, "name": f"Manager {k}", "style": "activist", "turnover": "READABLE",
                 "status": "FILING",
                 "activity": {"state": st, "since": "2026-06-30",
                              "items": ([{"form": "SC 13D", "public_on": "2026-08-02",
                                          "as_of": "2026-07-30"}]
                                        if st == "FILED SINCE" else [])}}
                for k, st in zip(keys, states)]
    return {"managers": managers, "new_filings": filings or []}


class Purity(unittest.TestCase):
    def test_no_io_and_no_clock(self):
        src = Path(__file__).resolve().parent.joinpath("hf_report.py").read_text()
        body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        for bad in ("urlopen", "requests.", "socket", "datetime.now", "date.today",
                    "json.load", "Path("):
            self.assertNotIn(bad, body, f"hf_report must not use {bad}")
        self.assertIsNone(re.search(r"(?<![A-Za-z_.])open\s*\(", body))

    def test_the_same_inputs_build_the_same_report(self):
        a = R.build(board(), funds(), None, None, week="2026-W36", built_at="2026-09-07T09:00:00")
        b = R.build(board(), funds(), None, None, week="2026-W36", built_at="2026-09-07T09:00:00")
        self.assertEqual(a, b)


class Assembly(unittest.TestCase):
    def setUp(self):
        self.rep = R.build(board(), funds(), None, None, week="2026-W36",
                           built_at="2026-09-07T09:00:00", week_start="2026-08-31")

    def test_every_section_the_brief_asks_for_is_present(self):
        for key in ("summary", "conclusions", "trends", "sectors", "crowding", "funds",
                    "new_filings", "watchlist", "press", "conflicts", "changed",
                    "limitations", "unavailable", "sources"):
            self.assertIn(key, self.rep, key)

    def test_the_four_questions_are_carried_across_whole(self):
        self.assertEqual([c["key"] for c in self.rep["conclusions"]],
                         ["exposure", "leverage", "longs", "shorts"])
        ex = self.rep["conclusions"][0]
        self.assertEqual(ex["verdict"], "REDUCING")
        self.assertEqual(ex["confidence"]["level"], "MODERATE")
        self.assertTrue(ex["inputs"])
        self.assertEqual(ex["as_of_text"], "September 1, 2026")

    def test_dates_are_spelled_out(self):
        self.assertEqual(R.long_date("2026-09-01"), "September 1, 2026")
        self.assertIsNone(R.long_date("not a date"))
        self.assertNotRegex(str(self.rep["dates"]["as_of"]), r"^\d{4}-\d{2}-\d{2}$")

    def test_sector_rows_are_carried_whole(self):
        # A first draft flattened confidence to its level and streak to its
        # word. The card reads both as objects and drew a dash in every one
        # of those columns, and a stored report lost the inputs behind each
        # sector — so a week read back later could show a verdict with
        # nothing behind it. The rows are carried as the board made them.
        row = self.rep["sectors"]["rows"][0]
        self.assertIsInstance(row["confidence"], dict)
        self.assertIn("level", row["confidence"])
        self.assertIsInstance(row["streak"], dict)
        self.assertIn("word", row["streak"])
        self.assertIn("inputs", row)

    def test_trends_carry_all_four_windows(self):
        t = self.rep["trends"][0]
        self.assertEqual([w["weeks"] for w in t["windows"]], [2, 4, 8, 12])
        self.assertEqual(t["windows"][1]["same"], 3)

    def test_a_no_data_verdict_is_not_summarised_as_a_finding(self):
        b = board()
        b["exposure"] = question("exposure?", P.NO_DATA)
        rep = R.build(b, funds(), None, None, week="2026-W36")
        self.assertFalse(any("exposure" in s.lower() for s in rep["summary"]["bullets"]))

    def test_the_summary_never_claims_more_than_a_section_does(self):
        self.assertIn("nothing in this summary is stronger",
                      self.rep["summary"]["note"].lower())


class TwoLayersStayApart(unittest.TestCase):
    def test_no_aggregate_row_carries_a_fund_name(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        rows = [i for c in rep["conclusions"] for i in c["inputs"]]
        self.assertTrue(S.attribution_ok(rows))
        for r in rows:
            self.assertNotIn("fund", r)

    def test_named_activity_is_its_own_section_in_the_verified_class(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        self.assertEqual(rep["funds"]["class"], S.VERIFIED)
        self.assertEqual(rep["funds"]["acted"][0]["class"], S.VERIFIED)

    def test_a_manager_name_never_appears_in_an_aggregate_conclusion(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        blob = str(rep["conclusions"]) + str(rep["sectors"]) + str(rep["crowding"])
        self.assertNotIn("Manager m0", blob)


class FundActivity(unittest.TestCase):
    def test_filed_since_is_listed_and_unknown_is_counted(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        f = rep["funds"]
        self.assertEqual(f["n_acted"], 1)
        self.assertEqual(f["n_unknown"], 2)
        self.assertEqual(f["acted"][0]["since_text"], "June 30, 2026")

    def test_a_filing_from_weeks_earlier_is_not_activity_this_week(self):
        # FILED SINCE persists until the next 13F arrives, so without a date
        # filter the same four managers appeared under a heading that says
        # "this week" for months. Reported live: four managers "acted" in a
        # week whose new-filing sweep found nothing at all.
        rep = R.build(board(), funds(), None, None, week="2026-W36",
                      week_start="2026-08-31")
        self.assertEqual(rep["funds"]["n_acted"], 0, "the 13D was filed on August 2")
        self.assertEqual(rep["funds"]["n_filed_since"], 1, "the state is still counted")

    def test_a_filing_inside_the_week_is_activity_this_week(self):
        f = funds()
        f["managers"][0]["activity"]["items"] = [{"form": "SC 13D", "public_on": "2026-09-02"}]
        rep = R.build(board(), f, None, None, week="2026-W36", week_start="2026-08-31")
        self.assertEqual(rep["funds"]["n_acted"], 1)
        self.assertEqual(rep["funds"]["acted"][0]["items"][0]["public_on"], "2026-09-02")

    def test_without_a_week_boundary_every_filed_since_manager_is_listed(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        self.assertEqual(rep["funds"]["n_acted"], 1)

    def test_not_read_yet_is_not_counted_as_unknown(self):
        rep = R.build(board(), funds(("NOT READ YET", "NOT READ YET")), None, None, week="2026-W36")
        self.assertEqual(rep["funds"]["n_unknown"], 0)
        self.assertEqual(rep["funds"]["n_not_read"], 2)

    def test_a_first_run_says_so_rather_than_reporting_an_empty_cupboard(self):
        rep = R.build(board(), funds(("NOT READ YET",)), None, None, week="2026-W36")
        joined = " ".join(rep["summary"]["bullets"]).lower()
        self.assertIn("has not finished its first read", joined)
        self.assertNotIn("no watched manager filed", joined)

    def test_ceased_managers_are_counted_separately(self):
        rep = R.build(board(), funds(("CEASED", "UNKNOWN")), None, None, week="2026-W36")
        self.assertEqual(rep["funds"]["n_ceased"], 1)


class NewFilings(unittest.TestCase):
    ROWS = [{"form": "SC 13D", "manager": "Elliott", "filed": "2026-09-02", "cik": 1},
            {"form": "SC 13G", "manager": "Passive Co", "filed": "2026-09-02", "cik": 2},
            {"form": "13F-HR/A", "manager": "Restater", "filed": "2026-09-03", "cik": 3},
            {"form": "13F-NT", "manager": "Mover", "filed": "2026-09-03", "cik": 4},
            {"form": "SC 13D", "manager": "Old News", "filed": "2026-08-01", "cik": 5}]

    def test_a_13d_is_an_event_and_a_13g_is_not(self):
        got = R.new_filings({"new_filings": self.ROWS}, "2026-08-31")
        self.assertEqual([r["manager"] for r in got["events"]], ["Elliott"])
        self.assertNotIn("Passive Co", str(got))

    def test_an_amendment_to_a_holdings_report_is_listed(self):
        got = R.new_filings({"new_filings": self.ROWS}, "2026-08-31")
        self.assertEqual([r["manager"] for r in got["amendments"]], ["Restater"])

    def test_a_notice_is_listed_separately(self):
        got = R.new_filings({"new_filings": self.ROWS}, "2026-08-31")
        self.assertEqual([r["manager"] for r in got["notices"]], ["Mover"])

    def test_filings_before_the_week_start_are_not_this_week_s(self):
        got = R.new_filings({"new_filings": self.ROWS}, "2026-08-31")
        self.assertNotIn("Old News", str(got))
        self.assertEqual(got["since_text"], "August 31, 2026")

    def test_without_a_week_start_everything_supplied_is_used(self):
        got = R.new_filings({"new_filings": self.ROWS}, None)
        self.assertEqual(len(got["events"]), 2)


class Watchlist(unittest.TestCase):
    def test_the_first_report_says_there_is_nothing_to_compare(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        self.assertFalse(rep["watchlist"]["comparable"])
        self.assertIn("first stored report", rep["watchlist"]["note"])

    def test_an_added_manager_is_reported(self):
        first = R.build(board(), funds(keys=["a", "b"], states=("UNKNOWN", "UNKNOWN")),
                        None, None, week="2026-W36")
        second = R.build(board(week="2026-W37"),
                         funds(keys=["a", "b", "c"], states=("UNKNOWN",) * 3),
                         None, first, week="2026-W37")
        self.assertEqual([m["key"] for m in second["watchlist"]["added"]], ["c"])
        self.assertEqual(second["watchlist"]["removed"], [])

    def test_a_removed_manager_is_reported(self):
        first = R.build(board(), funds(keys=["a", "b"], states=("UNKNOWN", "UNKNOWN")),
                        None, None, week="2026-W36")
        second = R.build(board(week="2026-W37"), funds(keys=["a"], states=("UNKNOWN",)),
                         None, first, week="2026-W37")
        self.assertEqual([m["key"] for m in second["watchlist"]["removed"]], ["b"])


class Changes(unittest.TestCase):
    """"Nothing to compare against" and "nothing changed" are different
    answers and must never render the same."""

    def test_no_prior_report_is_not_comparable(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        self.assertFalse(rep["changed"]["comparable"])
        self.assertEqual(rep["changed"]["n"], 0)

    def test_an_identical_week_reports_zero_changes_but_is_comparable(self):
        first = R.build(board(), funds(), None, None, week="2026-W36")
        second = R.build(board(week="2026-W37"), funds(), None, first, week="2026-W37")
        self.assertTrue(second["changed"]["comparable"])
        self.assertEqual(second["changed"]["n"], 0)

    def test_a_flipped_verdict_is_a_change(self):
        first = R.build(board(exposure="REDUCING"), funds(), None, None, week="2026-W36")
        second = R.build(board(week="2026-W37", exposure="ADDING"), funds(), None, first,
                         week="2026-W37")
        rows = [c for c in second["changed"]["changes"] if c["kind"] == "VERDICT"]
        self.assertEqual(rows, [{"what": "Overall exposure", "from": "REDUCING",
                                 "to": "ADDING", "kind": "VERDICT"}])

    def test_a_sector_flip_is_a_change(self):
        first = R.build(board(), funds(), None, None, week="2026-W36")
        second = R.build(board(week="2026-W37",
                               sector_verdicts={"Technology": "REDUCING", "Energy": "REDUCING"}),
                         funds(), None, first, week="2026-W37")
        rows = [c for c in second["changed"]["changes"] if c["kind"] == "SECTOR"]
        self.assertEqual(rows[0]["what"], "Technology")

    def test_entering_and_leaving_crowding_are_both_changes(self):
        first = R.build(board(crowded=("S&P 500",)), funds(), None, None, week="2026-W36")
        second = R.build(board(week="2026-W37", crowded=("Nasdaq 100",)), funds(), None, first,
                         week="2026-W37")
        rows = {c["what"]: c for c in second["changed"]["changes"] if c["kind"] == "CROWDING"}
        self.assertEqual(rows["Nasdaq 100 crowding"]["to"], P.CROWDED)
        self.assertEqual(rows["S&P 500 crowding"]["from"], P.CROWDED)

    def test_a_confidence_move_is_reported(self):
        b = board(week="2026-W37")
        b["exposure"] = question("Are hedge funds increasing or reducing exposure?", "REDUCING",
                                 level="HIGH", classes=3)
        first = R.build(board(), funds(), None, None, week="2026-W36")
        second = R.build(b, funds(), None, first, week="2026-W37")
        rows = [c for c in second["changed"]["changes"] if c["kind"] == "CONFIDENCE"]
        self.assertEqual(rows[0]["to"], "HIGH")


class Conflicts(unittest.TestCase):
    def test_input_disagreement_inside_a_question_is_kept(self):
        b = board()
        b["exposure"] = question("exposure?", "REDUCING",
                                 conflicts=[{"label": "ETF creations", "class": S.FLOW_PROXY,
                                             "direction": 1, "change": 5.0}])
        rep = R.build(b, funds(), None, None, week="2026-W36")
        self.assertEqual(rep["n_conflicts"], 1)
        self.assertEqual(rep["conflicts"][0]["kind"], "INPUTS DISAGREE")

    def test_sector_disagreement_is_kept(self):
        b = board()
        b["sectors"]["rows"][0]["conflicts"] = [{"label": "short interest", "direction": -1}]
        rep = R.build(b, funds(), None, None, week="2026-W36")
        self.assertIn("Technology", [c["where"] for c in rep["conflicts"]])

    def test_two_banks_disagreeing_is_its_own_kind(self):
        press = {"conflicts": [{"about": "exposure",
                                "saying_up": [{"bank": "Morgan Stanley", "text": "bought",
                                               "public_on": "2026-09-02"}],
                                "saying_down": [{"bank": "Goldman Sachs", "text": "sold",
                                                 "public_on": "2026-09-01"}]}]}
        rep = R.build(board(), funds(), press, None, week="2026-W36")
        row = [c for c in rep["conflicts"] if c["kind"] == "BANKS DISAGREE"]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["where"], "Overall exposure")
        self.assertEqual(row[0]["n"], 2)

    def test_no_disagreement_produces_an_empty_list_not_a_missing_section(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        self.assertEqual(rep["conflicts"], [])
        self.assertEqual(rep["n_conflicts"], 0)


class Press(unittest.TestCase):
    def test_the_quotes_are_carried_with_their_class(self):
        got = PRESS.build([{"title": "Goldman Says Hedge Funds Sell US Tech Stocks at Record Pace",
                            "outlet": "Reuters", "published": "2026-09-04T07:00:00+00:00",
                            "summary": ""}], __import__("datetime").date(2026, 9, 6))
        rep = R.build(board(), funds(), got, None, week="2026-W36")
        self.assertEqual(rep["press"]["n_quotes"], 1)
        self.assertEqual(rep["press"]["class"], S.PRIME_BROKER)
        self.assertEqual(rep["press"]["banks"], ["Goldman Sachs"])

    def test_no_press_at_all_is_not_an_error(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        self.assertEqual(rep["press"]["n_quotes"], 0)
        self.assertEqual(rep["press"]["quotes"], [])


class Compare(unittest.TestCase):
    def setUp(self):
        self.a = R.build(board(week="2026-W36", exposure="REDUCING"), funds(), None, None,
                         week="2026-W36")
        self.b = R.build(board(week="2026-W37", exposure="ADDING"), funds(), None, self.a,
                         week="2026-W37")

    def test_the_older_week_is_shown_first_whatever_the_argument_order(self):
        one = R.compare(self.a, self.b)
        two = R.compare(self.b, self.a)
        self.assertEqual(one["older"]["week"], "2026-W36")
        self.assertEqual(two["older"]["week"], "2026-W36")
        self.assertEqual(one["conclusions"], two["conclusions"])

    def test_matching_and_moving_answers_are_counted(self):
        cmp = R.compare(self.a, self.b)
        self.assertEqual(cmp["n_different"], 1)
        self.assertEqual(cmp["n_same"], 3)
        ex = next(c for c in cmp["conclusions"] if c["key"] == "exposure")
        self.assertEqual((ex["older"]["verdict"], ex["newer"]["verdict"]), ("REDUCING", "ADDING"))

    def test_compare_and_what_changed_can_never_disagree(self):
        cmp = R.compare(self.a, self.b)
        self.assertEqual(cmp["changed"]["changes"], self.b["changed"]["changes"])

    def test_sectors_are_lined_up_by_name(self):
        cmp = R.compare(self.a, self.b)
        self.assertEqual(sorted(s["sector"] for s in cmp["sectors"]), ["Energy", "Technology"])
        self.assertTrue(all(s["same"] for s in cmp["sectors"]))

    def test_comparing_against_nothing_is_refused(self):
        self.assertFalse(R.compare(self.a, None)["ok"])

    def test_watchlist_changes_are_derived_from_the_two_reports_compared(self):
        # These rows used to be copied from the current report's own
        # `added`/`removed`, which were computed against the week BEFORE it.
        # Comparing week 36 with week 38 therefore showed week 38's changes
        # against week 37, missing what moved in week 37 itself.
        w36 = R.build(board(week="2026-W36"), funds(keys=["a"], states=("UNKNOWN",)),
                      None, None, week="2026-W36")
        w37 = R.build(board(week="2026-W37"), funds(keys=["a", "b"], states=("UNKNOWN",) * 2),
                      None, w36, week="2026-W37")
        w38 = R.build(board(week="2026-W38"), funds(keys=["a", "b", "c"], states=("UNKNOWN",) * 3),
                      None, w37, week="2026-W38")
        cmp = R.compare(w36, w38)
        moved = {r["what"] for r in cmp["changed"]["changes"] if r["kind"] == "WATCHLIST"}
        self.assertEqual(moved, {"Watchlist: Manager b", "Watchlist: Manager c"},
                         "both weeks' additions, not only the last one's")

    def test_a_removal_across_non_adjacent_weeks_is_seen(self):
        w36 = R.build(board(week="2026-W36"), funds(keys=["a", "b"], states=("UNKNOWN",) * 2),
                      None, None, week="2026-W36")
        w37 = R.build(board(week="2026-W37"), funds(keys=["a", "b"], states=("UNKNOWN",) * 2),
                      None, w36, week="2026-W37")
        w38 = R.build(board(week="2026-W38"), funds(keys=["a"], states=("UNKNOWN",)),
                      None, w37, week="2026-W38")
        cmp = R.compare(w36, w38)
        moved = [r for r in cmp["changed"]["changes"] if r["kind"] == "WATCHLIST"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["to"], "not watched")


class Digest(unittest.TestCase):
    def test_the_history_line_carries_the_four_verdicts(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36",
                      built_at="2026-09-07T09:00:00")
        d = R.digest(rep)
        self.assertEqual(d["week"], "2026-W36")
        self.assertEqual(d["as_of_text"], "September 1, 2026")
        self.assertEqual(set(d["verdicts"]), {"exposure", "leverage", "longs", "shorts"})
        self.assertEqual(d["crowded"], ["S&P 500"])
        self.assertEqual(d["n_acted"], 1)


class Limitations(unittest.TestCase):
    def test_the_report_states_what_it_cannot_do(self):
        rep = R.build(board(), funds(), None, None, week="2026-W36")
        blob = " ".join(rep["limitations"]).lower()
        for must in ("secondhand", "unknown", "four of the eleven sectors", "hedge ledgers"):
            self.assertIn(must, blob)


class EmptyInputs(unittest.TestCase):
    def test_an_empty_board_does_not_raise(self):
        rep = R.build({}, {}, None, None, week="2026-W36")
        self.assertEqual([c["verdict"] for c in rep["conclusions"]], [P.NO_DATA] * 4)
        self.assertFalse(any(c["decided"] for c in rep["conclusions"]))

    def test_no_funds_payload_does_not_raise(self):
        rep = R.build(board(), None, None, None, week="2026-W36")
        self.assertEqual(rep["funds"]["n_managers"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
