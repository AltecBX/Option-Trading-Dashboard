"""Guards for hf_names.py — the names behind the crowd.

These rows name real funds against real stocks, which is the most
consequential thing this board does. The tests are written to break that:
by folding an OPAQUE book into the consensus, by counting a put as
ownership, by claiming "most owned" from ten-position samples, and by
implying a set of filings was true on one day.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import hf_names as N
import hf_sources as S


def mgr(key, name, turnover="READABLE", as_of="2026-06-30", top=None, change=None):
    return {"key": key, "name": name, "turnover": turnover, "as_of": as_of,
            "top": top or [], "change": change or {}}


def pos(symbol, value=1e9, issuer=None, put_call=None):
    return {"symbol": symbol, "issuer": issuer or f"{symbol} Inc.", "value": value,
            "cusip": f"{symbol}0000", "put_call": put_call}


class WhoIsCounted(unittest.TestCase):
    def test_an_opaque_book_is_not_a_view(self):
        # The fund cards refuse to summarise a multi-strategy 13F as
        # conviction. Counting it in a consensus would be that same fiction
        # at scale.
        ms = [mgr("a", "Readable A", top=[pos("MSFT")]),
              mgr("c", "Citadel Advisors", turnover="OPAQUE", top=[pos("MSFT")]),
              mgr("b", "Readable B", top=[pos("MSFT")])]
        counted, basis = N.eligible(ms)
        self.assertEqual([m["key"] for m in counted], ["a", "b"])
        self.assertEqual(basis["n_opaque"], 1)
        self.assertIn("Citadel Advisors", basis["opaque"])
        self.assertIn("hedging", basis["why_opaque"])

    def test_the_denominator_travels_with_the_count(self):
        # "Eleven managers agree" means something else out of thirty-two.
        ms = [mgr("a", "A", top=[pos("MSFT")]), mgr("b", "B", turnover="OPAQUE", top=[pos("MSFT")]),
              mgr("c", "C")]   # never read
        card = N.build(ms)
        b = card["basis"]
        self.assertEqual(b["n_counted"], 1)
        self.assertEqual(b["n_managers"], 3)
        self.assertEqual(b["n_opaque"], 1)
        self.assertEqual(b["n_not_read"], 1)

    def test_a_manager_never_read_is_not_silently_opaque(self):
        _, basis = N.eligible([mgr("c", "Unread", turnover="READABLE")])
        self.assertEqual(basis["n_not_read"], 1)
        self.assertEqual(basis["n_opaque"], 0)


class APutIsNotOwnership(unittest.TestCase):
    def test_a_put_never_counts_as_holding(self):
        ms = [mgr("a", "A", top=[pos("TSLA", put_call="Put")]),
              mgr("b", "B", top=[pos("TSLA", put_call="PUT")]),
              mgr("c", "C", top=[pos("TSLA", put_call="Put")])]
        card = N.build(ms)
        self.assertEqual(card["held"], [], "three managers betting against it is not consensus ownership")

    def test_puts_are_reported_separately_rather_than_dropped(self):
        ms = [mgr("a", "A", top=[pos("TSLA", put_call="Put")]),
              mgr("b", "B", top=[pos("TSLA", put_call="Put")])]
        card = N.build(ms)
        self.assertEqual([r["name"] for r in card["puts"]], ["TSLA"])
        self.assertEqual(card["puts"][0]["n_managers"], 2)

    def test_a_call_is_still_a_long_bet_and_is_counted(self):
        ms = [mgr("a", "A", top=[pos("NVDA", put_call="Call")]),
              mgr("b", "B", top=[pos("NVDA")])]
        card = N.build(ms)
        self.assertEqual([r["name"] for r in card["held"]], ["NVDA"])
        self.assertEqual(card["held"][0]["n_calls"], 1, "the call is counted and flagged")

    def test_a_manager_holding_both_shares_and_puts_counts_once_for_the_shares(self):
        ms = [mgr("a", "A", top=[pos("META"), pos("META", put_call="Put")]),
              mgr("b", "B", top=[pos("META")])]
        card = N.build(ms)
        row = card["held"][0]
        self.assertEqual(row["n_managers"], 2)
        self.assertEqual(sorted(row["managers"]), ["A", "B"], "A is counted once, not twice")


class WhatItCanActuallySee(unittest.TestCase):
    def test_one_manager_is_not_a_crowd(self):
        self.assertEqual(N.build([mgr("a", "A", top=[pos("AAPL")])])["held"], [])

    def test_two_is_the_floor_and_it_is_stated(self):
        ms = [mgr("a", "A", top=[pos("AAPL")]), mgr("b", "B", top=[pos("AAPL")])]
        card = N.build(ms)
        self.assertEqual(card["min_managers"], 2)
        self.assertEqual(card["held"][0]["n_managers"], 2)

    def test_the_wording_never_claims_most_owned(self):
        card = N.build([mgr("a", "A", top=[pos("AAPL")]), mgr("b", "B", top=[pos("AAPL")])])
        blob = (card["note"] + " " + " ".join(card["limitations"])).lower()
        self.assertIn("ten largest", blob)
        self.assertIn("not how many own it", blob)
        self.assertNotIn("most owned", blob)
        self.assertIn("eleventh place", blob, "it says what it would miss")

    def test_it_says_a_13f_carries_no_shorts(self):
        blob = " ".join(N.build([])["limitations"]).lower()
        self.assertIn("short sales are not reported", blob)

    def test_the_headline_refuses_when_nothing_is_shared(self):
        h = N.headline(N.build([mgr("a", "A", top=[pos("AAPL")])]))
        self.assertFalse(h["available"])
        self.assertIn("readable books", h["text"])

    def test_the_headline_says_the_count_and_the_denominator(self):
        ms = [mgr("a", "A", top=[pos("AAPL")]), mgr("b", "B", top=[pos("AAPL")]),
              mgr("z", "Z", turnover="OPAQUE", top=[pos("AAPL")])]
        h = N.headline(N.build(ms))
        self.assertTrue(h["available"])
        self.assertIn("2 of the 2 readable books", h["text"])
        self.assertIn("ten largest", h["text"])


class BoughtAndSold(unittest.TestCase):
    def setUp(self):
        self.ms = [
            mgr("a", "A", change={"new": [pos("NVDA")], "reduced": [pos("INTC")]}),
            mgr("b", "B", change={"increased": [pos("NVDA")], "exited": [pos("INTC")]}),
            mgr("c", "C", change={"new": [pos("NVDA")]}),
        ]
        self.card = N.build(self.ms)

    def test_new_and_increased_are_one_direction(self):
        self.assertEqual([r["name"] for r in self.card["bought"]], ["NVDA"])
        self.assertEqual(self.card["bought"][0]["n_managers"], 3)

    def test_reduced_and_exited_are_the_other(self):
        self.assertEqual([r["name"] for r in self.card["sold"]], ["INTC"])
        self.assertEqual(self.card["sold"][0]["n_managers"], 2)

    def test_a_manager_in_both_buckets_for_one_name_counts_once_each_way(self):
        ms = [mgr("a", "A", change={"new": [pos("X")], "increased": [pos("X")]}),
              mgr("b", "B", change={"new": [pos("X")]})]
        card = N.build(ms)
        self.assertEqual(card["bought"][0]["n_managers"], 2, "A added X once, not twice")


class DifferentManagersDifferentQuarters(unittest.TestCase):
    def test_a_row_carries_the_span_not_one_date(self):
        # 13Fs land 45 days after their period and managers do not file
        # together, so one date would imply they were all true at once.
        ms = [mgr("a", "A", as_of="2026-06-30", top=[pos("AAPL")]),
              mgr("b", "B", as_of="2026-03-31", top=[pos("AAPL")])]
        row = N.build(ms)["held"][0]
        self.assertEqual(row["as_of_first"], "2026-03-31")
        self.assertEqual(row["as_of_last"], "2026-06-30")

    def test_the_card_carries_the_span_too(self):
        ms = [mgr("a", "A", as_of="2026-06-30", top=[pos("AAPL")]),
              mgr("b", "B", as_of="2026-03-31", top=[pos("AAPL")])]
        p = N.build(ms)["periods"]
        self.assertEqual((p["first"], p["last"], p["n"]), ("2026-03-31", "2026-06-30", 2))

    def test_the_note_warns_that_quarters_are_mixed(self):
        self.assertIn("June book with another's March", N.build([])["note"])


class Attribution(unittest.TestCase):
    def test_every_row_is_verified_and_may_therefore_name_funds(self):
        ms = [mgr("a", "A", top=[pos("AAPL")]), mgr("b", "B", top=[pos("AAPL")])]
        card = N.build(ms)
        for bucket in ("held", "bought", "sold", "puts"):
            for row in card[bucket]:
                self.assertEqual(row["class"], S.VERIFIED)

    def test_the_rows_pass_the_attribution_rule(self):
        # The rule that guards this whole feature: anonymous classes may
        # never carry a fund. These rows are VERIFIED, so they may.
        ms = [mgr("a", "A", top=[pos("AAPL")]), mgr("b", "B", top=[pos("AAPL")])]
        rows = [{"class": r["class"], "fund": r["managers"][0]} for r in N.build(ms)["held"]]
        self.assertTrue(S.attribution_ok(rows))

    def test_the_managers_named_are_the_ones_that_actually_hold_it(self):
        ms = [mgr("a", "Alpha", top=[pos("AAPL")]), mgr("b", "Beta", top=[pos("MSFT")]),
              mgr("c", "Gamma", top=[pos("AAPL")])]
        row = [r for r in N.build(ms)["held"] if r["name"] == "AAPL"][0]
        self.assertEqual(row["managers"], ["Alpha", "Gamma"])
        self.assertNotIn("Beta", row["managers"])


class UnmappedAndOddInput(unittest.TestCase):
    def test_an_unknown_cusip_is_counted_under_its_issuer_not_dropped(self):
        p1 = {"symbol": "", "issuer": "Some Private Co", "value": 1e9, "cusip": "X"}
        card = N.build([mgr("a", "A", top=[p1]), mgr("b", "B", top=[dict(p1)])])
        self.assertEqual([r["name"] for r in card["held"]], ["Some Private Co"])
        self.assertIsNone(card["held"][0]["symbol"])
        self.assertEqual(card["unmapped"]["n"], 2)
        self.assertEqual(card["unmapped"]["of"], 2)

    def test_a_row_with_neither_ticker_nor_issuer_is_skipped_not_blank(self):
        blank = {"symbol": "", "issuer": "", "value": 1e9}
        card = N.build([mgr("a", "A", top=[blank]), mgr("b", "B", top=[dict(blank)])])
        self.assertEqual(card["held"], [])

    def test_no_managers_is_an_empty_card_not_a_crash(self):
        card = N.build([])
        self.assertEqual((card["held"], card["bought"], card["sold"]), ([], [], []))
        self.assertEqual(card["basis"]["n_counted"], 0)

    def test_none_is_the_same_as_empty(self):
        self.assertEqual(N.build(None)["held"], N.build([])["held"])

    def test_the_same_input_builds_the_same_card(self):
        ms = [mgr("a", "A", top=[pos("AAPL")]), mgr("b", "B", top=[pos("AAPL")])]
        self.assertEqual(N.build(ms), N.build(ms))


class Ordering(unittest.TestCase):
    def test_more_managers_wins_over_more_money(self):
        ms = [mgr("a", "A", top=[pos("SMALL", 1.0), pos("BIG", 1e12)]),
              mgr("b", "B", top=[pos("SMALL", 1.0)]),
              mgr("c", "C", top=[pos("SMALL", 1.0)])]
        names = [r["name"] for r in N.build(ms)["held"]]
        self.assertEqual(names[0], "SMALL", "three books beat one big cheque")

    def test_the_table_is_capped(self):
        ms = [mgr(str(i), f"M{i}", top=[pos(f"S{j}") for j in range(40)]) for i in range(3)]
        self.assertEqual(len(N.build(ms, top=15)["held"]), 15)


class Purity(unittest.TestCase):
    def test_no_io_no_clock(self):
        import re
        src = Path("hf_names.py").read_text(encoding="utf-8")
        for bad in (r"(?<![A-Za-z_.])open\s*\(", r"requests\.", r"urlopen",
                    r"datetime\.now", r"date\.today", r"Path\("):
            self.assertIsNone(re.search(bad, src), f"hf_names must not {bad}")


if __name__ == "__main__":
    unittest.main()
