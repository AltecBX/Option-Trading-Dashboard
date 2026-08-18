"""Reading the business chapter out of an annual report.

Every fixture here is a reduced version of a real document shape measured
across fifty-six filings: a table-of-contents anchor, a heading in its own
element, a heading split letter by letter, a cross-reference that points at
the chapter from somewhere later, a contents entry, an amendment with no
Item 1 at all. The reader has to tell them apart without being told which
is which.
"""
import unittest

import filing_reader as R


def plain(raw: bytes) -> str:
    """The flattener, close enough to sec_filings._plain for these fixtures:
    every tag becomes a space, and runs of whitespace collapse."""
    import re
    s = raw.decode("utf-8", "replace")
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s)


R.configure(plain_fn=plain)

CHAPTER = (
    "Sample Industries, Inc. designs and manufactures precision instruments "
    "for laboratories and hospitals. The company sells through a direct "
    "sales force in North America and through distributors elsewhere. Its "
    "products are used in clinical diagnostics, environmental testing and "
    "industrial quality control. The company operates four manufacturing "
    "plants and employs about six thousand people. Revenue is earned from "
    "instrument sales and from the consumables those instruments require, "
    "and the consumables produce most of the gross profit. "
) * 12

RISK = ("The following risks could adversely affect our business. Demand for "
        "our products may decline. We may not be able to obtain components. "
        "There can be no assurance that competition will not intensify. ") * 8


def doc(body: str) -> bytes:
    return ("<html><body>" + body + "</body></html>").encode()


class TestTableOfContentsAnchor(unittest.TestCase):
    """The document's own link to the chapter is the best evidence there is."""

    def setUp(self):
        self.raw = doc(
            '<table><tr><td><a href="#i1">Item 1. Business</a></td>'
            '<td>3</td></tr>'
            '<tr><td><a href="#i1a">Item 1A. Risk Factors</a></td>'
            '<td>19</td></tr></table>'
            '<div id="i1"><p>Item 1. Business</p><p>' + CHAPTER + '</p></div>'
            '<div id="i1a"><p>Item 1A. Risk Factors</p><p>' + RISK + '</p></div>')

    def test_it_follows_the_anchor_and_stops_at_risk_factors(self):
        got = R.extract(self.raw)
        self.assertEqual(got["method"], "table of contents anchor")
        self.assertEqual(got["confidence"], R.HIGH)
        self.assertIn("precision instruments", got["text"])
        self.assertNotIn("could adversely affect", got["text"])

    def test_it_reports_where_it_started_and_stopped(self):
        got = R.extract(self.raw)
        self.assertIn("Item 1", got["start_heading"])
        self.assertTrue(got["end_heading"] or got["bounded"])
        self.assertGreater(got["characters"], R.MIN_CONFIDENT_CHAPTER)

    def test_a_business_only_anchor_label_still_works(self):
        """Morgan Stanley's contents list says only "Business"."""
        raw = doc('<a href="#b">Business</a>'
                  '<a href="#r">Risk Factors</a>'
                  '<div id="b">' + CHAPTER + '</div>'
                  '<div id="r">' + RISK + '</div>')
        got = R.extract(raw)
        self.assertEqual(got["method"], "table of contents anchor")
        self.assertIn("precision instruments", got["text"])


class TestHeadingElement(unittest.TestCase):
    def test_a_heading_alone_in_its_element_is_found(self):
        raw = doc('<p>Some cover page text.</p>'
                  '<p><b>Item 1. Business</b></p><div>' + CHAPTER + '</div>'
                  '<p><b>Item 1A. Risk Factors</b></p><div>' + RISK + '</div>')
        got = R.extract(raw)
        self.assertIn(got["method"], ("heading element", "heading text boundary"))
        self.assertIn("precision instruments", got["text"])
        self.assertNotIn("could adversely affect", got["text"])


class TestSplitLetters(unittest.TestCase):
    """Berkshire styles the heading letter by letter."""

    def test_a_heading_split_mid_word_is_still_a_heading(self):
        raw = doc('<p>Table of Contents Item 1. Business Description K-1 '
                  'Item 1A. Risk Factors K-24</p>'
                  '<p>Par t I Item 1. Busines<span>s</span> Description</p>'
                  '<div>' + CHAPTER + '</div>'
                  '<p>Item 1A. Ris<span>k</span> Factors</p>'
                  '<div>' + RISK + '</div>')
        got = R.extract(raw)
        self.assertNotEqual(got["confidence"], R.FAILED)
        self.assertIn("precision instruments", got["text"])

    def test_the_pattern_tolerates_a_gap_between_any_two_letters(self):
        self.assertTrue(R.ITEM1_HDR.search("I TEM 1. Business"))
        self.assertTrue(R.ITEM1_HDR.search("Item 1. B USINESS"))
        self.assertTrue(R.ITEM1_HDR.search("Item 1: Busines s"))
        self.assertTrue(R.ITEM1A_HDR.search("Item 1A. Ris k Factors"))


class TestRejections(unittest.TestCase):
    def test_a_contents_entry_alone_is_not_a_chapter(self):
        raw = doc('<p>Item 1. Business 3 Item 1A. Risk Factors 19 '
                  'Item 2. Properties 38 Item 3. Legal Proceedings 40</p>')
        got = R.extract(raw)
        self.assertEqual(got["confidence"], R.FAILED)
        self.assertTrue(got["reason"])

    def test_a_cross_reference_is_not_a_chapter(self):
        raw = doc('<p>Item 1. Business — see Part I, Item 1 of this report '
                  'for a description of our regulatory environment. ' +
                  ("Additional detail appears there. " * 200) + '</p>')
        got = R.extract(raw)
        self.assertNotEqual(got["confidence"], R.HIGH)

    def test_a_single_paragraph_reference_is_refused(self):
        raw = doc('<p>Item 1. Business. Incorporated by reference. See page '
                  'four. Nothing further.</p>')
        got = R.extract(raw)
        self.assertEqual(got["confidence"], R.FAILED)

    def test_risk_factor_text_running_on_lowers_confidence(self):
        raw = doc('<p>Item 1. Business</p><div>' + CHAPTER + RISK + RISK +
                  '</div>')
        got = R.extract(raw)
        self.assertIn(got["confidence"], (R.LOW, R.MODERATE))

    def test_nothing_at_all_is_a_named_failure(self):
        got = R.extract(doc("<p>An exhibit index and nothing else.</p>"))
        self.assertEqual(got["confidence"], R.FAILED)
        self.assertIn("No business chapter", got["reason"])


class TestMultipleItemOneStrings(unittest.TestCase):
    def test_the_first_full_chapter_wins_not_the_longest(self):
        """A cross-reference late in the report used to beat the chapter."""
        later = ("Regulation of our holding company liquidity is discussed "
                 "at length in this section. " * 400)
        raw = doc('<p>Item 1. Business 3 Item 1A. Risk Factors 19</p>'
                  '<p>Item 1. Business</p><div>' + CHAPTER + '</div>'
                  '<p>Item 1A. Risk Factors</p><div>' + RISK + '</div>'
                  '<p>see Part I, Item 1 — Business — Regulation</p>'
                  '<div>' + later + '</div>')
        got = R.extract(raw)
        self.assertIn("precision instruments", got["text"])
        self.assertNotIn("holding company liquidity", got["text"][:2000])


class TestOlderHtmlStructure(unittest.TestCase):
    def test_a_name_attribute_anchor_still_resolves(self):
        """Pre-2010 filings use <a name="..."> rather than id."""
        raw = doc('<a href="#x">Item 1. Business</a>'
                  '<a name="x"></a><font>' + CHAPTER + '</font>'
                  '<p>ITEM 1A. RISK FACTORS</p><font>' + RISK + '</font>')
        got = R.extract(raw)
        self.assertNotEqual(got["confidence"], R.FAILED)
        self.assertIn("precision instruments", got["text"])

    def test_an_inline_xbrl_header_is_cut_before_reading(self):
        noise = "<ix:header>" + ("us-gaap:AssetImpairmentCharges " * 500) \
                + "</ix:header>"
        raw = doc(noise + '<p>Item 1. Business</p><div>' + CHAPTER + '</div>'
                  '<p>Item 1A. Risk Factors</p><div>' + RISK + '</div>')
        got = R.extract(raw)
        self.assertNotIn("AssetImpairmentCharges", got["text"])
        self.assertIn("precision instruments", got["text"])


class FakeSec:
    """Just enough of sec_filings for the document-selection logic."""

    def __init__(self, rows, docs):
        self.rows, self.docs, self.fetched = rows, docs, []

    def filings(self, symbol):
        return list(self.rows)

    def _fetch(self, url, limit=None):
        self.fetched.append(url)
        return self.docs[url]


class TestWhichFilingIsRead(unittest.TestCase):
    """An amendment carries only the items it amends."""

    def setUp(self):
        self.amendment = doc('<p>Item 15. Exhibits and Financial Statement '
                             'Schedules. The exhibit index below lists the '
                             'exhibits filed with this amendment.</p>')
        self.tenk = doc('<a href="#i1">Item 1. Business</a>'
                        '<a href="#i1a">Item 1A. Risk Factors</a>'
                        '<div id="i1">' + CHAPTER + '</div>'
                        '<div id="i1a">' + RISK + '</div>')
        self.sec = FakeSec(
            [{"form": "10-K/A", "date": "2026-03-12", "url": "amend",
              "accession": "0001-26-2"},
             {"form": "10-K", "date": "2026-02-19", "url": "tenk",
              "accession": "0001-26-1"}],
            {"amend": self.amendment, "tenk": self.tenk})

    def test_it_falls_back_from_an_amendment_to_the_annual_report(self):
        got = R.business_section("AMP", self.sec)
        self.assertTrue(got["ok"])
        self.assertEqual(got["provenance"]["form"], "10-K")
        self.assertEqual(got["provenance"]["accession"], "0001-26-1")
        self.assertEqual(self.sec.fetched, ["amend", "tenk"])

    def test_it_stops_as_soon_as_a_filing_reads_cleanly(self):
        self.sec.rows = self.sec.rows[1:]
        got = R.business_section("AMP", self.sec)
        self.assertEqual(self.sec.fetched, ["tenk"])
        self.assertEqual(got["confidence"], R.HIGH)

    def test_provenance_names_the_accession_document_and_method(self):
        prov = R.business_section("AMP", self.sec)["provenance"]
        for key in ("symbol", "accession", "document", "form", "filed",
                    "method", "characters", "confidence", "reader_version"):
            self.assertIn(key, prov)
        self.assertEqual(prov["symbol"], "AMP")
        self.assertEqual(prov["reader_version"], R.READER_VERSION)

    def test_a_filer_with_no_annual_report_says_so(self):
        got = R.business_section("Y", FakeSec([], {}))
        self.assertFalse(got["ok"])
        self.assertIn("no annual report", got["reason"])

    def test_only_amendments_explains_what_an_amendment_is(self):
        sec = FakeSec([{"form": "10-K/A", "date": "2026-03-12", "url": "a",
                        "accession": "x"}], {"a": self.amendment})
        got = R.business_section("IBKR", sec)
        self.assertFalse(got["ok"])
        self.assertIn("amendment", got["reason"])


class TestConfidenceGate(unittest.TestCase):
    def test_only_high_and_moderate_may_classify_a_business(self):
        self.assertTrue(R.acceptable(R.HIGH))
        self.assertTrue(R.acceptable(R.MODERATE))
        self.assertFalse(R.acceptable(R.LOW))
        self.assertFalse(R.acceptable(R.FAILED))
        self.assertFalse(R.acceptable(None))


class TestChapterCleanup(unittest.TestCase):
    def test_a_chapter_index_is_cut_off_the_front(self):
        index = ("Business Overview 5 Segments and Corporate 7 Reinsurance 12 "
                 "Regulation 21 Human Capital 30 Available Information 34 ")
        body = R._tidy("Item 1. Business " + index + CHAPTER)
        self.assertTrue(body.startswith("Sample Industries"), body[:80])

    def test_prose_containing_dates_is_not_mistaken_for_an_index(self):
        """An unbounded version of that rule sliced nine filings mid-sentence."""
        body = R._tidy("Item 1. Business " + CHAPTER)
        self.assertTrue(body.startswith("Sample Industries"), body[:80])

    def test_a_page_break_header_is_dropped(self):
        body = R._tidy("Item 1. Business 12 Tab le of Contents " + CHAPTER)
        self.assertTrue(body.startswith("Sample Industries"), body[:80])


if __name__ == "__main__":
    unittest.main()
