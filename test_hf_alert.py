"""Guards for hf_alert.py — what is worth waking someone for.

A push is the one place a reader cannot click through to check, so these
tests are mostly about restraint: not sending the same thing twice, not
sending a state as though it were a change, not emptying a backlog onto a
lock screen the first time alerts are switched on, and never naming a fund
in an alert built from data that says nothing about funds.
"""

from __future__ import annotations

import re
import unittest
from datetime import date
from pathlib import Path

import hf_alert as A
import hf_sources as S

TODAY = date(2026, 9, 7)


def board(**states):
    """A pulse board carrying one crowding row per named market."""
    return {"crowding": {"markets": [
        {"key": k, "market": k.replace("_", " ").title(), "state": v,
         "side": "long", "as_of": "2026-09-01"}
        for k, v in states.items()]}}


def funds(*rows):
    return {"new_filings": list(rows)}


def filing(form="SC 13D", accession="0001", manager="Elliott Management",
           key="elliott", company="ACME Corp", filed="2026-09-04"):
    return {"form": form, "accession": accession, "manager": manager, "key": key,
            "company": company, "filed": filed, "cik": 1}


class AChangeNeverAState(unittest.TestCase):
    def test_a_market_that_is_simply_crowded_is_not_an_alert(self):
        # True for weeks at a time, news on none of them.
        b = board(sec_financials="CROWDED")
        rows = A.crowding_changes(b, {"sec_financials": "CROWDED"})
        self.assertEqual(rows, [])

    def test_becoming_crowded_is_an_alert(self):
        rows = A.crowding_changes(board(sec_financials="CROWDED"),
                                  {"sec_financials": "NORMAL"})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["entered"])
        self.assertEqual(rows[0]["market"], "Sec Financials")

    def test_ceasing_to_be_crowded_is_an_alert_too(self):
        rows = A.crowding_changes(board(sec_energy="NORMAL"), {"sec_energy": "CROWDED"})
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["entered"])

    def test_a_wobble_between_normal_and_decrowding_is_not(self):
        # Neither side of that change is the crowded state, so nothing
        # crossed the line that matters.
        self.assertEqual(A.crowding_changes(board(djia="DECROWDING"), {"djia": "NORMAL"}), [])
        self.assertEqual(A.crowding_changes(board(djia="NORMAL"), {"djia": "DECROWDING"}), [])

    def test_a_market_seen_for_the_first_time_is_not_a_change(self):
        # No previous state is not the same as a previous state of NORMAL.
        self.assertEqual(A.crowding_changes(board(sec_new="CROWDED"), {}), [])

    def test_the_comparison_is_against_the_last_alert_not_last_week(self):
        # Two rebuilds inside one week must not fire twice. The caller keeps
        # the state from the last alert and passes it back.
        b = board(sec_financials="CROWDED")
        first = A.crowding_changes(b, {"sec_financials": "NORMAL"})
        self.assertEqual(len(first), 1)
        after = A.crowding_state(b)
        self.assertEqual(A.crowding_changes(b, after), [], "the second rebuild says nothing")


class NeverTwice(unittest.TestCase):
    def test_a_key_is_the_event_not_the_moment_it_was_noticed(self):
        seen_wed = A.activist_filings(funds(filing()), date(2026, 9, 4))[0]
        seen_sun = A.activist_filings(funds(filing()), date(2026, 9, 7))[0]
        self.assertEqual(seen_wed["key"], seen_sun["key"])
        self.assertNotEqual(seen_wed["days_ago"], seen_sun["days_ago"])

    def test_an_already_sent_alert_is_held(self):
        rows = A.candidates(funds=funds(filing()), today=TODAY)
        out = A.select(rows, sent_keys=[r["key"] for r in rows])
        self.assertEqual(out["send"], [])
        self.assertTrue(all(h["held"] == "already sent" for h in out["held"]))

    def test_everything_seen_is_remembered_even_when_held(self):
        # A held alert must not arrive later dressed as new.
        rows = A.candidates(funds=funds(filing()), today=TODAY)
        out = A.select(rows, primed=False)
        self.assertEqual(out["send"], [])
        self.assertEqual(set(out["remember"]), {r["key"] for r in rows})


class TheFirstRunPrimes(unittest.TestCase):
    def test_it_sends_nothing(self):
        rows = A.candidates(board=board(sec_financials="CROWDED"),
                            funds=funds(filing(), filing(accession="2", form="13F-HR")),
                            report={"week": "2026-W36"}, last_crowding={}, today=TODAY)
        out = A.select(rows, primed=False)
        self.assertEqual(out["n_send"], 0)
        self.assertGreater(out["n_held"], 0)
        self.assertIn("first run", out["held"][0]["held"])

    def test_but_it_remembers_everything_so_the_next_run_is_quiet(self):
        rows = A.candidates(funds=funds(filing()), today=TODAY)
        primed_run = A.select(rows, primed=False)
        second = A.select(rows, sent_keys=primed_run["remember"], primed=True)
        self.assertEqual(second["n_send"], 0, "priming must not merely delay the backlog")

    def test_and_a_genuine_change_after_priming_does_send(self):
        rows = A.candidates(funds=funds(filing()), today=TODAY)
        primed_run = A.select(rows, primed=False)
        later = A.candidates(funds=funds(filing(), filing(accession="NEW", company="Beta Inc")),
                             today=TODAY)
        out = A.select(later, sent_keys=primed_run["remember"], primed=True)
        self.assertEqual(out["n_send"], 1)
        self.assertEqual(out["send"][0]["issuer"], "Beta Inc")


class AQuietWeekSendsNothing(unittest.TestCase):
    def test_an_empty_board_produces_no_alerts_and_no_error(self):
        out = A.build()
        self.assertEqual(out["n_send"], 0)
        self.assertEqual(out["n_candidates"], 0)
        self.assertEqual(out["crowding_state"], {})

    def test_a_steady_board_produces_none_either(self):
        b = board(sp500="NORMAL", sec_financials="CROWDED")
        out = A.build(board=b, last_crowding=A.crowding_state(b), today=TODAY)
        self.assertEqual(out["n_send"], 0)


class Attribution(unittest.TestCase):
    def test_a_crowding_alert_never_names_a_fund(self):
        # It comes from CFTC futures, which say who is positioned and never
        # which fund. Naming one would invent the fact the board refuses to.
        rows = A.crowding_changes(board(sec_financials="CROWDED"), {"sec_financials": "NORMAL"})
        for r in rows:
            self.assertEqual(r["class"], S.REGULATORY)
            self.assertNotIn("fund", r)

    def test_an_activist_alert_may_name_one_because_the_fund_signed_it(self):
        r = A.activist_filings(funds(filing()), TODAY)[0]
        self.assertEqual(r["class"], S.VERIFIED)
        self.assertEqual(r["fund"], "Elliott Management")

    def test_the_whole_batch_passes_the_attribution_rule(self):
        rows = A.candidates(board=board(sec_financials="CROWDED"),
                            funds=funds(filing()), last_crowding={"sec_financials": "NORMAL"},
                            today=TODAY)
        self.assertTrue(A.attribution_ok(rows))
        self.assertTrue(A.build(board=board(sec_financials="CROWDED"), funds=funds(filing()),
                                last_crowding={"sec_financials": "NORMAL"},
                                today=TODAY)["attribution_ok"])

    def test_a_smuggled_fund_on_an_anonymous_row_is_caught(self):
        bad = [{"kind": A.CROWDING, "class": S.REGULATORY, "fund": "Citadel"}]
        self.assertFalse(A.attribution_ok(bad))


class WhichFilingsCount(unittest.TestCase):
    def test_a_13g_is_never_an_alert(self):
        # A passive notice is not activity; the fund cards already say so.
        f = funds(filing(form="SC 13G", accession="9"))
        self.assertEqual(A.activist_filings(f, TODAY), [])
        self.assertEqual(A.new_filings(f, TODAY), [])

    def test_a_13d_amendment_still_counts_as_activist(self):
        self.assertEqual(len(A.activist_filings(funds(filing(form="SC 13D/A")), TODAY)), 1)

    def test_a_13f_is_a_new_filing_not_an_activist_one(self):
        f = funds(filing(form="13F-HR"))
        self.assertEqual(A.activist_filings(f, TODAY), [])
        self.assertEqual(len(A.new_filings(f, TODAY)), 1)

    def test_a_13d_is_not_also_counted_as_an_ordinary_filing(self):
        f = funds(filing(form="SC 13D"))
        self.assertEqual(A.new_filings(f, TODAY), [])
        self.assertEqual(len(A.activist_filings(f, TODAY)), 1)


class TheWords(unittest.TestCase):
    def test_an_activist_push_carries_the_date_and_the_lag(self):
        r = A.activist_filings(funds(filing(filed="2026-09-04")), TODAY)[0]
        r["priority"] = A.PRIORITY[A.ACTIVIST]
        out = A.render(r)
        self.assertIn("Elliott Management", out["message"])
        self.assertIn("ACME Corp", out["message"])
        self.assertIn("September 4, 2026", out["message"])
        self.assertIn("— 3 days ago", out["message"], "it must not read as though it just happened")
        self.assertEqual(out["priority"], 1)

    def test_a_filing_found_the_same_day_does_not_claim_days_ago(self):
        r = A.activist_filings(funds(filing(filed="2026-09-07")), TODAY)[0]
        self.assertIn("— today", A.render(r)["message"])
        self.assertNotIn("days ago", A.render(r)["message"])

    def test_one_day_reads_as_yesterday_not_as_one_days_ago(self):
        r = A.activist_filings(funds(filing(filed="2026-09-06")), TODAY)[0]
        self.assertIn("— yesterday", A.render(r)["message"])
        self.assertNotIn("1 days ago", A.render(r)["message"])

    def test_a_crowding_push_says_it_is_not_a_forecast(self):
        r = A.crowding_changes(board(sec_financials="CROWDED"), {"sec_financials": "NORMAL"})[0]
        out = A.render(r)
        self.assertIn("just became crowded", out["message"])
        self.assertIn("not a forecast", out["message"])
        self.assertIn("September 1, 2026", out["message"])

    def test_a_quarterly_filing_push_says_it_is_news_about_the_past(self):
        r = A.new_filings(funds(filing(form="13F-HR")), TODAY)[0]
        self.assertIn("news about the past", A.render(r)["message"])

    def test_no_push_ever_shows_an_iso_date(self):
        rows = A.candidates(board=board(sec_financials="CROWDED"),
                            funds=funds(filing(), filing(accession="2", form="13F-HR")),
                            report={"week": "2026-W36", "built_at": "2026-09-07T09:00:00"},
                            last_crowding={"sec_financials": "NORMAL"}, today=TODAY)
        self.assertTrue(rows)
        for r in rows:
            msg = A.render(r)["message"]
            self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", msg), msg)
            self.assertIsNone(re.search(r"\d{4}-W\d{2}", msg), msg)

    def test_the_report_push_names_the_week_it_covers_not_the_day_it_ran(self):
        # 2026-W36 opened Monday August 31 and the report is built after it
        # closes. Printing the build date would name a week the report is
        # not about.
        r = A.report_ready({"week": "2026-W36", "built_at": "2026-09-07T09:00:00"})[0]
        self.assertEqual(r["week_start"], "2026-08-31")
        msg = A.render(r)["message"]
        self.assertIn("August 31, 2026", msg)
        self.assertNotIn("September 7", msg)

    def test_a_report_with_an_unreadable_week_still_renders(self):
        r = A.report_ready({"week": "nonsense"})[0]
        self.assertIsNone(r["week_start"])
        self.assertIn("unstated date", A.render(r)["message"])

    def test_a_missing_date_reads_as_words_not_as_none(self):
        self.assertEqual(A.long_date(None), "an unstated date")
        self.assertEqual(A.long_date("not-a-date"), "an unstated date")


class TheCap(unittest.TestCase):
    def test_a_backlog_does_not_empty_onto_a_lock_screen(self):
        rows = A.candidates(funds=funds(*[filing(accession=str(i)) for i in range(20)]),
                            today=TODAY)
        out = A.select(rows, cap=6)
        self.assertEqual(out["n_send"], 6)
        self.assertTrue(any("more than 6" in h["held"] for h in out["held"]))

    def test_the_loudest_go_first(self):
        rows = A.candidates(board=board(sec_financials="CROWDED"),
                            funds=funds(filing(accession="d"),
                                        *[filing(accession=str(i), form="13F-HR") for i in range(9)]),
                            last_crowding={"sec_financials": "NORMAL"}, today=TODAY)
        out = A.select(rows, cap=2)
        self.assertEqual({r["kind"] for r in out["send"]}, {A.ACTIVIST, A.CROWDING})

    def test_a_kind_can_be_switched_off(self):
        rows = A.candidates(funds=funds(filing(form="13F-HR")), today=TODAY)
        out = A.select(rows, kinds=(A.ACTIVIST, A.CROWDING))
        self.assertEqual(out["n_send"], 0)
        self.assertIn("switched off", out["held"][0]["held"])

    def test_the_held_ones_are_still_remembered_at_the_cap(self):
        # Otherwise the overflow arrives next run as though it were new, and
        # a big week would trickle out for days.
        rows = A.candidates(funds=funds(*[filing(accession=str(i)) for i in range(10)]),
                            today=TODAY)
        out = A.select(rows, cap=3)
        self.assertEqual(len(out["remember"]), 10)


class Purity(unittest.TestCase):
    def test_no_io_no_clock_no_sending(self):
        src = Path("hf_alert.py").read_text(encoding="utf-8")
        for bad in (r"(?<![A-Za-z_.])open\s*\(", r"requests\.", r"urlopen",
                    r"datetime\.now", r"date\.today", r"Path\(", r"def send"):
            self.assertIsNone(re.search(bad, src), f"hf_alert must not {bad}")

    def test_the_same_input_decides_the_same_way(self):
        args = dict(board=board(sec_financials="CROWDED"), funds=funds(filing()),
                    last_crowding={"sec_financials": "NORMAL"}, today=TODAY)
        self.assertEqual(A.build(**args), A.build(**args))


if __name__ == "__main__":
    unittest.main()
