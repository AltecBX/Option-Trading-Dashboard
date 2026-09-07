"""test_hf_press.py (v4.87) — guards for the prime-broker channel.

This module's whole job is to REFUSE things. The corpus it was written
against is 319 real Google News headlines captured on September 6, 2026, and
most of them are traps: a bank named as the subject rather than the source, a
story about returns wearing the vocabulary of positioning, a note about the
Korean market, a rotation headline whose direction depends on which half you
read. Every trap below is a real headline from that capture, quoted exactly.

The tests therefore assert what does NOT become evidence at least as hard as
what does. A parser that admits a bad row here would put a bank's name behind
a claim it never made.

Run:  python3 test_hf_press.py
"""

from __future__ import annotations

import re
import unittest
from datetime import date
from pathlib import Path

import hf_press as PR
import hf_sources as S

TODAY = date(2026, 9, 6)


def item(title, outlet=None, published="2026-09-04T07:00:00+00:00", summary="", link=None):
    return {"title": title, "outlet": outlet, "published": published,
            "summary": summary, "link": link}


class Purity(unittest.TestCase):
    """The parser must stay a function of its arguments. A clock or a socket
    in here would make a stored report unreproducible."""

    def test_no_io_and_no_clock(self):
        src = Path(__file__).resolve().parent.joinpath("hf_press.py").read_text()
        body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        for bad in ("urlopen", "requests.", "socket", "datetime.now", "date.today",
                    "json.load", "Path("):
            self.assertNotIn(bad, body, f"hf_press must not use {bad}")
        # `open(` but not `is_open(` or `.open(`; the same lookbehind trick the
        # hf_pulse purity guard needed.
        self.assertIsNone(re.search(r"(?<![A-Za-z_.])open\s*\(", body),
                          "hf_press must not open files")


class BankAttribution(unittest.TestCase):
    """A bank is the source only when the sentence says so."""

    def test_a_bank_named_as_the_subject_is_not_a_source(self):
        # Real headline. It names JPMorgan and a purchase and is about JPM's
        # own shares. Reading it as positioning would be a fabrication.
        t = "JPMorgan Chase & Co. $JPM Shares Purchased by Smith Group Asset Management LLC"
        self.assertIsNone(PR.bank_of(t))
        self.assertIsNone(PR.read(item(t, "MarketBeat")))  # not about hedge funds either

    def test_says_after_the_claim_is_attribution(self):
        self.assertEqual(PR.bank_of("Hedge funds sell energy stocks as oil slumps, says Goldman Sachs"),
                         "Goldman Sachs")

    def test_says_before_the_claim_is_attribution(self):
        self.assertEqual(PR.bank_of("Goldman Says Hedge Funds Sell US Tech Stocks at Record Pace"),
                         "Goldman Sachs")

    def test_data_shows_is_attribution(self):
        self.assertEqual(PR.bank_of("Hedge Funds Cut Tech Holdings at Record Pace, Goldman Data Shows"),
                         "Goldman Sachs")

    def test_morgan_stanley_and_jpmorgan_are_read(self):
        self.assertEqual(PR.bank_of("Macro hedge funds to dump $45 bln in equities, says Morgan Stanley"),
                         "Morgan Stanley")
        self.assertEqual(PR.bank_of("Hedge funds' tech trades in July, JPMorgan says"), "JPMorgan")

    def test_a_bank_with_no_attribution_verb_is_not_read(self):
        self.assertIsNone(PR.bank_of("Ripple Borrows $275 Million at 8.25% to Take On Goldman Sachs"))


class Subject(unittest.TestCase):
    def test_an_item_not_about_hedge_funds_is_dropped_entirely(self):
        self.assertIsNone(PR.read(item("Goldman Sachs raises its S&P 500 target", "Reuters")))

    def test_an_item_about_hedge_funds_is_kept_even_when_useless(self):
        row = PR.read(item("Hedge funds had a quiet week", "Some Blog"))
        self.assertIsNotNone(row)
        self.assertFalse(row["eligible"])


class PerformanceIsNotPositioning(unittest.TestCase):
    """Returns and positions are different facts. Any return word rejects."""

    def test_underperformance_is_rejected(self):
        t = ("Goldman says hedge funds suffered worst underperformance vs S&P 500 in July in "
             "more than 20 years of data")
        self.assertTrue(PR.is_performance(t))
        row = PR.read(item(t, "CNBC"))
        self.assertFalse(row["eligible"])
        self.assertIn("about returns, not positioning", row["why_not"])

    def test_losses_are_rejected(self):
        row = PR.read(item("Hedge funds suffer 40% losses as popular longs get obliterated",
                           "Crypto Briefing"))
        self.assertFalse(row["eligible"])

    def test_a_stellar_year_is_rejected(self):
        row = PR.read(item("Hedge funds on track to surpass last year's stellar returns, Goldman says",
                           "The Globe and Mail"))
        self.assertFalse(row["eligible"])


class Region(unittest.TestCase):
    """The board measures the American book. A foreign note is visible and
    is not evidence."""

    def test_asia_is_marked_non_us(self):
        self.assertEqual(PR.region_of("Hedge funds cut Asia tech holdings in second-largest selloff"),
                         "NON-US")

    def test_a_non_us_headline_is_captured_but_not_evidence(self):
        row = PR.read(item("Hedge funds cut Asia tech holdings, Goldman says", "Reuters"))
        self.assertFalse(row["eligible"])
        self.assertIn("a stated non-US market", row["why_not"])

    def test_us_wins_when_both_appear(self):
        self.assertEqual(PR.region_of("US and Asian hedge funds"), "US")

    def test_a_bank_name_is_not_a_place(self):
        # "Bank of America" contains America. Without stripping the bank
        # names first, this headline matched both the foreign and the US
        # patterns, resolved to US, and became eligible evidence about a
        # market it explicitly was not describing.
        t = "Hedge funds sell Asia tech stocks, Bank of America says"
        self.assertEqual(PR.region_of(t), "NON-US")
        row = PR.read(item(t, "Reuters"))
        self.assertFalse(row["eligible"])
        self.assertIn("a stated non-US market", row["why_not"])

    def test_stripping_the_bank_does_not_hide_a_real_us_headline(self):
        self.assertEqual(
            PR.region_of("Hedge funds sell US tech stocks, Bank of America says"), "US")

    def test_the_bank_is_still_read_from_the_same_headline(self):
        self.assertEqual(PR.bank_of("Hedge funds sell Asia tech stocks, Bank of America says"),
                         "Bank of America")

    def test_no_region_stated_is_allowed(self):
        self.assertIsNone(PR.region_of("Hedge funds sold equities last week"))


class Direction(unittest.TestCase):
    def test_plain_selling_is_exposure_down(self):
        self.assertEqual(PR.claims("Goldman Says Hedge Funds Sell US Tech Stocks at Record Pace"),
                         {"exposure": -1})

    def test_plain_buying_is_exposure_up(self):
        self.assertEqual(PR.claims("Goldman Says Hedge Funds Buy Stocks at Fastest Pace in 6 Months"),
                         {"exposure": 1})

    def test_a_rotation_headline_has_no_direction(self):
        # "ditch tech and buy essentials" — the first read of this corpus
        # called it BUYING, and the sector word was Technology, so the row
        # would have said hedge funds were buying the thing they sold.
        self.assertEqual(PR.claims("Hedge funds ditch tech and buy essentials, Goldman Sachs says"),
                         {"exposure": None})

    def test_leverage_reaching_a_high_is_read(self):
        got = PR.claims("Hedge fund leverage reaches five-year high, buying bank stocks, Goldman Sachs says")
        self.assertEqual(got.get("leverage"), 1)

    def test_deleveraging_is_read_as_falling(self):
        self.assertEqual(PR.claims("the largest deleveraging wave in hedge fund history").get("leverage"), -1)

    def test_a_squeeze_is_not_adding_shorts(self):
        # "squeezed from short bets" means forced OUT of shorts. Counting the
        # words "short bets" alone read this exactly backwards.
        self.assertIsNone(PR.claims(
            "Hedge funds squeezed from short bets amid surging meme stocks, Goldman Sachs says")["shorts"])

    def test_shorting_after_a_squeeze_is_ambiguous(self):
        self.assertIsNone(PR.claims(
            "Hedge funds resume shorting after biggest short squeeze since 2020")["shorts"])

    def test_explicit_shorting_is_read(self):
        self.assertEqual(PR.claims("Hedge funds 'aggressively' short financial stocks, says Goldman"),
                         {"shorts": 1})

    def test_an_explicit_long_book_claim_goes_to_longs_not_exposure(self):
        got = PR.claims("Hedge funds cut their long positions")
        self.assertEqual(got.get("longs"), -1)
        self.assertNotIn("exposure", got)

    def test_one_sentence_can_answer_two_different_questions(self):
        got = PR.claims("Hedge funds pile into US stocks as tech short covering accelerates")
        self.assertEqual(got.get("exposure"), 1)
        self.assertEqual(got.get("shorts"), -1)


class Sector(unittest.TestCase):
    def test_one_sector_word_is_read(self):
        self.assertEqual(PR.sector_of("Hedge funds sell energy stocks as oil slumps"), "Energy")

    def test_two_sector_words_give_no_sector(self):
        # Which side is which cannot be read from word order.
        self.assertIsNone(PR.sector_of("Hedge funds rotate from tech into healthcare"))

    def test_no_sector_word_gives_none(self):
        self.assertIsNone(PR.sector_of("Hedge funds sold equities"))


class Outlets(unittest.TestCase):
    def test_a_wire_counts(self):
        self.assertEqual(PR.outlet_tier("Reuters"), PR.WIRE)

    def test_the_bank_itself_is_first_party(self):
        self.assertEqual(PR.outlet_tier("Goldman Sachs"), PR.FIRST)

    def test_an_aggregator_does_not_count(self):
        self.assertEqual(PR.outlet_tier("finance.biggo.com"), PR.OTHER)

    def test_an_aggregator_headline_is_captured_and_not_evidence(self):
        row = PR.read(item("Hedge Funds Cut Tech Holdings at Record Pace, Goldman Data Shows",
                           "finance.biggo.com"))
        self.assertFalse(row["eligible"])
        self.assertIn("outlet is not a wire or the bank itself", row["why_not"])

    def test_the_outlet_is_recovered_from_the_title_when_the_field_is_missing(self):
        row = PR.read(item("Hedge funds sold equities, Goldman says - Reuters", None))
        self.assertEqual(row["outlet"], "Reuters")
        self.assertNotIn("Reuters", row["title"])


class EvidenceRows(unittest.TestCase):
    GOOD = "Goldman Says Hedge Funds Sell US Tech Stocks at Record Pace"

    def test_a_clean_wire_headline_becomes_a_quote(self):
        rows = [PR.read(item(self.GOOD, "Reuters", "2026-09-04T07:00:00+00:00"))]
        qs = PR.evidence(rows, TODAY)
        self.assertEqual(len(qs), 1)
        q = qs[0]
        self.assertEqual((q["about"], q["direction"], q["bank"]), ("exposure", -1, "Goldman Sachs"))
        self.assertEqual(q["sector"], "Technology")
        self.assertEqual(q["public_on"], "2026-09-04")

    def test_the_period_is_never_invented(self):
        qs = PR.evidence([PR.read(item(self.GOOD, "Reuters"))], TODAY)
        self.assertIsNone(qs[0]["as_of"])
        self.assertIn("period", qs[0]["note"])

    def test_one_note_carried_by_five_outlets_counts_once(self):
        # The lesson from the crowding fix: the same fact must not vote twice.
        rows = [PR.read(item(self.GOOD, o, "2026-09-0%dT07:00:00+00:00" % d))
                for d, o in enumerate(("Reuters", "Bloomberg", "CNBC", "MarketWatch",
                                       "Yahoo Finance"), start=1)]
        qs = PR.evidence(rows, TODAY)
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["carried_by"], 5)
        self.assertEqual(qs[0]["public_on"], "2026-09-01", "the earliest report is the one kept")

    def test_a_stale_headline_is_not_this_week_s_evidence(self):
        rows = [PR.read(item(self.GOOD, "Reuters", "2026-07-01T07:00:00+00:00"))]
        self.assertEqual(PR.evidence(rows, TODAY), [])

    def test_a_headline_dated_in_the_future_is_refused(self):
        rows = [PR.read(item(self.GOOD, "Reuters", "2027-01-01T07:00:00+00:00"))]
        self.assertEqual(PR.evidence(rows, TODAY), [])

    def test_every_quote_carries_the_prime_broker_class(self):
        rows = [PR.read(item(self.GOOD, "Reuters"))]
        self.assertEqual(rows[0]["class"], S.PRIME_BROKER)

    def test_a_quote_never_carries_a_fund_name(self):
        # attribution_ok refuses any non-VERIFIED row that carries a "fund"
        # key at all, even one set to None, so the check is that the key is
        # simply absent from everything the press channel produces.
        rows = [PR.read(item(self.GOOD, "Reuters"))]
        qs = PR.evidence(rows, TODAY)
        self.assertTrue(qs)
        for row in rows + qs:
            self.assertNotIn("fund", row)
        self.assertTrue(S.attribution_ok(rows))


class Conflicts(unittest.TestCase):
    def test_two_banks_disagreeing_is_a_finding(self):
        rows = [PR.read(item("Goldman says hedge funds sold equities", "Reuters")),
                PR.read(item("Hedge funds bought equities, Morgan Stanley says", "Bloomberg"))]
        qs = PR.evidence(rows, TODAY)
        cs = PR.conflicts(qs)
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["about"], "exposure")
        self.assertTrue(cs[0]["saying_up"] and cs[0]["saying_down"])

    def test_agreement_produces_no_conflict(self):
        rows = [PR.read(item("Goldman says hedge funds sold equities", "Reuters")),
                PR.read(item("Hedge funds sold equities, Morgan Stanley says", "Bloomberg"))]
        self.assertEqual(PR.conflicts(PR.evidence(rows, TODAY)), [])


class Build(unittest.TestCase):
    def test_the_block_reports_what_it_kept_and_what_it_saw(self):
        got = PR.build([item("Goldman Says Hedge Funds Sell US Tech Stocks at Record Pace", "Reuters"),
                        item("Hedge funds had a bad month", "finance.biggo.com"),
                        item("Goldman Sachs raises its S&P 500 target", "Reuters")], TODAY)
        self.assertEqual(got["n_quotes"], 1)
        self.assertEqual(got["n_captured"], 2, "the non-hedge-fund item is not captured at all")
        self.assertEqual(got["banks"], ["Goldman Sachs"])

    def test_duplicate_titles_are_read_once(self):
        one = item("Goldman Says Hedge Funds Sell US Tech Stocks at Record Pace", "Reuters")
        got = PR.build([one, dict(one)], TODAY)
        self.assertEqual(got["n_captured"], 1)

    def test_an_empty_feed_is_not_an_error(self):
        got = PR.build([], TODAY)
        self.assertEqual((got["n_quotes"], got["n_captured"]), (0, 0))
        self.assertEqual(got["conflicts"], [])


class CorpusRegression(unittest.TestCase):
    """Twelve real headlines from the September 6, 2026 capture, with the
    verdict each one must produce. If a lexicon change flips any of these,
    the change is wrong until this list is re-argued."""

    CASES = (
        ("Goldman Says Hedge Funds Sell US Tech Stocks at Record Pace", "Bloomberg.com", True),
        ("Hedge funds sell energy stocks as oil slumps, says Goldman Sachs", "Reuters", True),
        ("HEDGE FLOW Macro hedge funds to dump $45 bln in equities, says Morgan Stanley", "Reuters", True),
        ("Hedge funds 'aggressively' short financial stocks, says Goldman", "Reuters", True),
        ("Hedge funds ditch tech and buy essentials, Goldman Sachs says", "Reuters", False),
        ("HEDGE FLOW Hedge funds squeezed from short bets amid surging meme stocks, Goldman Sachs says",
         "Reuters", False),
        ("Goldman says hedge funds suffered worst underperformance vs S&P 500 in July", "CNBC", False),
        ("Hedge funds cut Asia tech holdings in second-largest selloff", "Investing.com", False),
        ("JPMorgan Chase & Co. $JPM Shares Purchased by Smith Group Asset Management LLC",
         "MarketBeat", False),
        ("Hedge Funds Cut Tech Holdings at Record Pace, Goldman Data Shows", "Briefs Finance", False),
        ("Citadel buys most of Situational's stock holdings after AI share rout, sources say",
         "Reuters", False),
        ("Hedge funds on track for another stellar year on AI boom", "Yahoo Finance", False),
    )

    def test_each_captured_headline_lands_where_it_should(self):
        for title, outlet, should in self.CASES:
            with self.subTest(title=title[:60]):
                row = PR.read(item(title, outlet))
                got = bool(row and row["eligible"])
                self.assertEqual(got, should,
                                 f"{title!r} -> {row['why_not'] if row else 'not about hedge funds'}")

    def test_no_captured_headline_ever_names_a_fund_as_the_source(self):
        # Citadel appears in the corpus as the SUBJECT of a story. It must
        # never come back as the attributed source of an aggregate claim.
        row = PR.read(item("Citadel buys most of Situational's stock holdings after AI share rout",
                           "Reuters"))
        self.assertTrue(row is None or row["bank"] is None)


class Queries(unittest.TestCase):
    def test_the_fetcher_asks_about_all_three_banks(self):
        blob = " ".join(S.PRIME_BROKER_QUERIES).lower()
        for bank in ("goldman", "morgan stanley", "jpmorgan"):
            self.assertIn(bank, blob)

    def test_the_fetcher_deduplicates_and_survives_a_failing_query(self):
        calls = []

        def fake(q):
            calls.append(q)
            if "Morgan Stanley" in q:
                raise RuntimeError("feed down")
            return [{"title": "Hedge funds sold equities, Goldman says", "outlet": "Reuters"}]

        real = S.news_items
        S.news_items = fake
        try:
            got = S.prime_broker_news()
        finally:
            S.news_items = real
        self.assertEqual(len(got), 1, "one unique title across every query")
        self.assertGreater(len(calls), 1, "a failing query does not stop the rest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
