"""Tests for the SEC EDGAR offering/dilution reader.

Every fixture in here is the real shape of a real filing — the cover-page
wording is quoted from actual 424B5s, S-3s and 8-Ks that were fetched from
EDGAR while this module was written. The rules that matter:

  * a bond deal is never called dilution
  * a merger prospectus is never called an offering
  * a number only reaches the label when the sentence around it binds it to
    the deal — no scraping the first dollar figure on the page
  * with no network, everything goes quiet instead of guessing
"""

import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import sec_filings as sf


def clear() -> None:
    sf._SUB_CACHE.clear()
    sf._DOC_CACHE.clear()
    sf._FDA_CACHE.clear()
    sf._FDA_LOADED.clear()
    sf._TICKERS[0], sf._TICKERS[1] = 0.0, {}
    sf.configure(cik_fn=None)
    # importing options_dashboard anywhere in the suite points the FDA cache
    # at the real data directory; these tests must never read from it or
    # write into it
    sf._DATA_DIR = None


class Net:
    """Turns the network on for the duration of a test, with every fetch
    served from fixtures."""

    def __init__(self, json_by_url=None, text_by_url=None):
        self.json = json_by_url or {}
        self.text = text_by_url or {}
        self.calls = []

    def __enter__(self):
        clear()
        self._no_net = os.environ.pop("JERRY_NO_NET", None)
        self._fj, self._f = sf._fetch_json, sf._fetch
        sf._fetch_json = self._get_json
        sf._fetch = self._get_bytes
        return self

    def __exit__(self, *exc):
        sf._fetch_json, sf._fetch = self._fj, self._f
        if self._no_net is not None:
            os.environ["JERRY_NO_NET"] = self._no_net
        clear()
        return False

    def _get_json(self, url):
        self.calls.append(url)
        if url in self.json:
            return self.json[url]
        raise OSError(f"no fixture for {url}")

    def _get_bytes(self, url, limit=None, timeout=12):
        self.calls.append(url)
        if url in self.text:
            return self.text[url].encode("utf-8")
        raise OSError(f"no fixture for {url}")


def submissions(rows: list[dict]) -> dict:
    """The data.sec.gov 'recent' block: column arrays, not row objects."""
    keys = ("accessionNumber", "filingDate", "acceptanceDateTime", "form",
            "items", "primaryDocument")
    recent = {k: [] for k in keys}
    for i, r in enumerate(rows):
        recent["accessionNumber"].append(r.get("acc", f"0001234567-26-{i:06d}"))
        recent["filingDate"].append(r["date"])
        recent["acceptanceDateTime"].append(
            r.get("accepted", r["date"] + "T11:00:00.000Z"))
        recent["form"].append(r["form"])
        recent["items"].append(r.get("items", ""))
        recent["primaryDocument"].append(r.get("doc", "doc.htm"))
    return {"filings": {"recent": recent}}


SUB_URL = sf._SUB_URL.format(cik=999)


def doc_url(acc: str, doc: str = "doc.htm") -> str:
    return sf._ARCHIVE.format(cik=999, acc=acc.replace("-", ""), doc=doc)


# Cover-page openings, quoted from the real filings they are modeled on.
COVER_STOCK = ("424B5 Filed Pursuant to Rule 424(b)(5) PROSPECTUS SUPPLEMENT "
               "Ocular Therapeutix, Inc. 37,909,018 Shares Common Stock We are "
               "offering 37,909,018 shares of our common stock, par value "
               "$0.0001 per share.")
COVER_PRELIM = ("424B5 The information in this preliminary prospectus supplement "
                "is not complete and may be changed. MicroVision, Inc. Shares of "
                "Common Stock We are offering shares of our common stock.")
COVER_ATM = ("424B5 PROSPECTUS SUPPLEMENT $1,000,000,000 Common Stock This "
             "supplement relates to the sale of our shares of common stock "
             "having an aggregate offering price of up to $1,000,000,000 from "
             "time to time through our sales agent in at-the-market offerings.")
COVER_NOTES = ("424B2 PROSPECTUS SUPPLEMENT $350,000,000 Evergy, Inc. 4.250% "
               "Notes due 2029 We are offering $350,000,000 aggregate principal "
               "amount of 4.250% Notes due 2029.")
COVER_CONVERT = ("424B5 PROSPECTUS SUPPLEMENT $200,000,000 1.75% Convertible "
                 "Senior Notes due 2030 We are offering $200,000,000 aggregate "
                 "principal amount of convertible senior notes.")
COVER_MERGER = ("424B3 PROSPECTUS LETTER TO STOCKHOLDERS OF LIVEPERSON, INC. On "
                "April 21, 2026 LivePerson, SoundHound AI and Merger Sub entered "
                "into a Merger Agreement.")
COVER_RESALE = ("424B7 PROSPECTUS SUPPLEMENT 12,000,000 Shares of Common Stock "
                "This prospectus supplement relates to the resale of shares by "
                "the selling stockholders identified herein. We will not receive "
                "any proceeds.")


class TestFormMapping(unittest.TestCase):
    def test_priced_prospectus_is_an_offering(self):
        for form in ("424B1", "424B4", "424B5"):
            kind, label = sf._from_form({"form": form, "items": ""})
            self.assertEqual(kind, "OFFERING", form)
            self.assertIn(form, label)

    def test_shelf_registrations_are_dilution(self):
        kind, label = sf._from_form({"form": "S-3ASR", "items": ""})
        self.assertEqual(kind, "DILUTION")
        self.assertIn("Automatic shelf", label)
        kind, label = sf._from_form({"form": "S-1", "items": ""})
        self.assertEqual(kind, "DILUTION")
        self.assertIn("Registration statement", label)

    def test_8k_item_302_is_a_private_placement(self):
        kind, label = sf._from_form({"form": "8-K", "items": "1.01,3.02,9.01"})
        self.assertEqual(kind, "OFFERING")
        self.assertIn("Private placement", label)

    def test_ordinary_8k_and_unrelated_forms_are_not_tagged(self):
        self.assertIsNone(sf._from_form({"form": "8-K", "items": "2.02,9.01"}))
        for form in ("10-Q", "4", "SCHEDULE 13G", "S-8", "EFFECT"):
            self.assertIsNone(sf._from_form({"form": form, "items": ""}), form)


class TestCoverReading(unittest.TestCase):
    def cover(self, text: str) -> dict:
        with Net(text_by_url={"u": text}):
            return sf.read_cover("u", "acc")

    def test_stock_offering(self):
        c = self.cover(COVER_STOCK)
        self.assertEqual(c["security"], "equity")
        self.assertFalse(c["preliminary"])
        self.assertEqual(sf._label_from_cover("OFFERING", "424B5", c),
                         ("OFFERING", "Stock offering priced — 37.9M shares"))

    def test_preliminary_reads_announced_not_priced(self):
        c = self.cover(COVER_PRELIM)
        self.assertTrue(c["preliminary"])
        self.assertEqual(sf._label_from_cover("OFFERING", "424B5", c),
                         ("OFFERING", "Stock offering announced"))

    def test_at_the_market_program_named_with_its_size(self):
        c = self.cover(COVER_ATM)
        self.assertTrue(c["atm"])
        self.assertEqual(sf._label_from_cover("OFFERING", "424B5", c),
                         ("OFFERING", "At-the-market stock program — $1.0B"))

    def test_a_bond_deal_is_not_dilution(self):
        c = self.cover(COVER_NOTES)
        self.assertEqual(c["security"], "debt")
        self.assertIsNone(sf._label_from_cover("OFFERING", "424B2", c))

    def test_convertible_notes_are_dilution(self):
        # debt on paper, shares in fact — the one "notes" deal that counts
        c = self.cover(COVER_CONVERT)
        self.assertEqual(c["security"], "convertible")
        kind, label = sf._label_from_cover("OFFERING", "424B5", c)
        self.assertEqual(kind, "OFFERING")
        self.assertIn("Convertible notes", label)

    def test_merger_prospectus_is_never_an_offering(self):
        c = self.cover(COVER_MERGER)
        self.assertTrue(c["merger"])
        self.assertIsNone(sf._label_from_cover("OFFERING", "424B3", c))

    def test_resale_prospectus_is_dilution_not_a_raise(self):
        c = self.cover(COVER_RESALE)
        self.assertTrue(c["resale"])
        self.assertFalse(c["selling"])
        kind, label = sf._label_from_cover("OFFERING", "424B7", c)
        self.assertEqual(kind, "DILUTION")
        self.assertIn("selling stockholders", label)

    def test_unreadable_document_leaves_the_form_label_alone(self):
        with Net():                      # every fetch raises
            c = sf.read_cover("missing.htm", "acc")
        self.assertIsNone(c["security"])
        self.assertIsNone(c["size"])


class TestSizeLabel(unittest.TestCase):
    def test_only_bound_dollar_figures_count(self):
        # YXT's cover carried "aggregate market value of up to $6,581,275.96",
        # which is its shelf capacity, NOT the 500,000-ADS deal it priced.
        self.assertIsNone(sf._size_label(
            "we may sell securities having an aggregate market value of up to "
            "$6,581,275.96 pursuant to General Instruction I.B.5"))
        self.assertEqual(sf._size_label(
            "shares having an aggregate offering price of up to $75,000,000"),
            "$75M")

    def test_share_counts_read_from_the_cover_headline(self):
        self.assertEqual(sf._size_label("6,800,000 Shares of Common Stock"),
                         "6.8M shares")
        self.assertEqual(sf._size_label("250,000 shares of our common stock"),
                         "250K shares")

    def test_billions_round_to_one_decimal(self):
        self.assertEqual(sf._size_label(
            "an aggregate offering price of $1,000,000,000"), "$1.0B")


class TestAcceptanceTimestamps(unittest.TestCase):
    """acceptanceDateTime is UTC. Verified against Apple's 4:30pm ET earnings
    8-K (20:30Z) and JPMorgan's 6:30am one (10:30Z) — see the module
    docstring. Getting this backwards would put a premarket offering in the
    afternoon."""

    def test_utc_becomes_eastern(self):
        got = sf._accepted_et("2026-08-17T07:00:20.000Z")
        self.assertTrue(got.startswith("2026-08-17T03:00:20"), got)

    def test_afternoon_filing_stays_the_same_day(self):
        got = sf._accepted_et("2026-07-30T20:30:28.000Z")
        self.assertTrue(got.startswith("2026-07-30T16:30:28"), got)

    def test_garbage_is_dropped_not_guessed(self):
        self.assertIsNone(sf._accepted_et(""))
        self.assertIsNone(sf._accepted_et("not-a-date"))


class TestFilings(unittest.TestCase):
    def test_rows_are_flattened_newest_first_and_cached(self):
        sub = submissions([
            {"date": "2026-08-10", "form": "10-Q"},
            {"date": "2026-08-17", "form": "424B5"},
        ])
        with Net(json_by_url={SUB_URL: sub}) as net:
            sf.configure(cik_fn=lambda s: 999)
            rows = sf.filings("ZZZZ")
            self.assertEqual([r["form"] for r in rows], ["424B5", "10-Q"])
            sf.filings("ZZZZ")           # second call inside the TTL
            self.assertEqual(len(net.calls), 1, "submissions refetched")

    def test_offline_is_silent(self):
        clear()
        os.environ["JERRY_NO_NET"] = "1"
        self.assertFalse(sf.available())
        self.assertEqual(sf.filings("ZZZZ"), [])
        self.assertIsNone(sf.latest_event("ZZZZ", ("2026-08-17",)))
        self.assertEqual(sf.event_dates("ZZZZ"), {})


class TestLatestEvent(unittest.TestCase):
    def wire(self, rows, texts=None):
        net = Net(json_by_url={SUB_URL: submissions(rows)}, text_by_url=texts or {})
        net.__enter__()
        sf.configure(cik_fn=lambda s: 999)
        self.addCleanup(net.__exit__)
        return net

    def test_todays_offering_is_labeled_from_the_document(self):
        self.wire([{"date": "2026-08-17", "form": "424B5", "acc": "0001-26-1",
                    "doc": "a.htm"}],
                  {doc_url("0001-26-1", "a.htm"): COVER_STOCK})
        out = sf.latest_event("ZZZZ", ("2026-08-17", "2026-08-16"))
        self.assertEqual(out["kind"], "OFFERING")
        self.assertIn("37.9M shares", out["label"])
        self.assertEqual(out["form"], "424B5")
        self.assertIn("edgar", out["url"])

    def test_a_bond_deal_does_not_block_a_real_one(self):
        # a utility pricing notes the same morning must be skipped, not
        # returned and not allowed to hide the 8-K behind it
        self.wire([{"date": "2026-08-17", "form": "424B5", "acc": "0001-26-1",
                    "doc": "n.htm"},
                   {"date": "2026-08-17", "form": "8-K", "items": "3.02",
                    "acc": "0001-26-2", "accepted": "2026-08-17T10:00:00.000Z"}],
                  {doc_url("0001-26-1", "n.htm"): COVER_NOTES})
        out = sf.latest_event("ZZZZ", ("2026-08-17",))
        self.assertEqual(out["kind"], "OFFERING")
        self.assertIn("Private placement", out["label"])

    def test_stale_filings_are_out_of_the_window(self):
        self.wire([{"date": "2026-06-01", "form": "424B5", "acc": "0001-26-1"}],
                  {doc_url("0001-26-1"): COVER_STOCK})
        self.assertIsNone(sf.latest_event("ZZZZ", ("2026-08-17", "2026-08-16")))

    def test_shelf_keeps_its_form_label_when_the_document_says_nothing(self):
        self.wire([{"date": "2026-08-17", "form": "S-3", "acc": "0001-26-1"}])
        out = sf.latest_event("ZZZZ", ("2026-08-17",))
        self.assertEqual(out["kind"], "DILUTION")
        self.assertIn("Shelf registration", out["label"])

    def test_8k_without_item_302_is_not_a_catalyst(self):
        self.wire([{"date": "2026-08-17", "form": "8-K", "items": "2.02,9.01"}])
        self.assertIsNone(sf.latest_event("ZZZZ", ("2026-08-17",)))


# 8-K text, quoted from the real filings this classifier was built against.
FDA_APPROVAL = (
    "Item 8.01 Other Events. On May 4, 2026, ADMA Biologics, Inc. (the "
    "\"Company\") issued a press release announcing that the U.S. Food and Drug "
    "Administration has approved the Company's label expansion supplemental "
    "Biologics License Application for ASCENIV.")
FDA_CRL = (
    "Item 8.01 Other Events. On June 2, 2026, Cingulate Inc. issued a press "
    "release announcing that the U.S. Food and Drug Administration (\"FDA\") "
    "has issued a Complete Response Letter (\"CRL\") for its New Drug "
    "Application for CTx-1301 for the treatment of ADHD.")
FDA_RECAP = (
    "On July 28, 2026, Grace Therapeutics announced receipt of FDA meeting "
    "minutes from a Type A Meeting held to discuss the Complete Response "
    "Letter issued on April 23, 2026 for the Company's GTx-104 new drug "
    "application.")
FDA_RISK = (
    "Forward-looking statements include the risk that the FDA may issue a "
    "Complete Response Letter, and that we could be unable to obtain FDA "
    "approval of our product candidate on the expected timeline.")
FDA_TRIAL = (
    "The Company was pleased to have recently received FDA approval to "
    "initiate the CGUARDIANS III clinical trial with its SwitchGuard NPS.")
BOARD_APPROVAL = (
    "On July 15, 2026, the Board of Directors has approved a quarterly cash "
    "dividend of $0.36 per share, payable September 1, 2026.")


class TestFilingClassifier(unittest.TestCase):
    """The rule that matters: the tag fires on a decision the company is
    announcing NOW, and stays silent on everything that merely mentions the
    FDA — risk factors, trial permissions, board votes, old news retold."""

    def test_approval_is_recognized_and_quoted(self):
        kind, quote = sf.classify_filing(FDA_APPROVAL, "8.01,9.01", "2026-05-04")
        self.assertEqual(kind, "FDA APPROVAL")
        self.assertIn("has approved", quote)
        self.assertIn("Food and Drug Administration", quote)
        # the quote must survive "U.S." — a naive sentence split truncates
        # it there and throws away the half that says what happened
        self.assertIn("ASCENIV", quote)

    def test_complete_response_letter_is_a_rejection(self):
        kind, quote = sf.classify_filing(FDA_CRL, "8.01", "2026-06-02")
        self.assertEqual(kind, "FDA REJECTION")
        self.assertIn("Complete Response Letter", quote)

    def test_risk_factor_boilerplate_is_not_an_event(self):
        # every biotech filing warns about exactly these two outcomes
        self.assertEqual(sf.classify_filing(FDA_RISK, "8.01", "2026-06-02")[0], None)

    def test_recap_of_an_older_decision_does_not_tag_todays_gap(self):
        self.assertEqual(sf.classify_filing(FDA_RECAP, "8.01", "2026-07-28")[0], None)

    def test_permission_to_run_a_trial_is_not_a_product_approval(self):
        self.assertEqual(sf.classify_filing(FDA_TRIAL, "8.01", "2026-05-04")[0], None)

    def test_a_board_approving_a_dividend_is_not_the_fda(self):
        self.assertEqual(sf.classify_filing(BOARD_APPROVAL, "8.01", "2026-07-15")[0], None)

    def test_earnings_filings_are_skipped_entirely(self):
        # a quarterly release recaps the year's approvals; that day's gap is
        # the earnings gap, and earnings already outranks this tag
        self.assertEqual(
            sf.classify_filing(FDA_APPROVAL, "2.02,9.01", "2026-05-04")[0], None)

    def test_a_dateless_announcement_still_counts(self):
        text = ("Celcuity Inc. today announced that the U.S. Food and Drug "
                "Administration has approved GEDATOLISIB for HR+/HER2- "
                "advanced breast cancer.")
        self.assertEqual(sf.classify_filing(text, "8.01", "2026-07-15")[0],
                         "FDA APPROVAL")


# Deal and trial wording, quoted from the real filings this was built against.
BUYOUT_8K = (
    "Item 1.01 Entry into a Material Definitive Agreement. Agreement and Plan "
    "of Merger On July 26, 2026, Forte Biosciences, Inc. entered into an "
    "Agreement and Plan of Merger with argenx BV and Avena Merger Sub Inc. The "
    "Merger Agreement provides for the acquisition of the Company by Parent in "
    "a two-step transaction. Pursuant to the Merger Agreement, Purchaser will "
    "commence a tender offer to acquire all of the Company's issued and "
    "outstanding shares of common stock for $77.00 per share, net to the "
    "seller in cash.")
ACQUIRER_8K = (
    "Item 8.01 Other Events. On June 3, 2026, the Company entered into a "
    "definitive agreement to acquire Northwind Systems LLC for $40 million in "
    "cash, expanding its manufacturing footprint.")
MERGER_VAGUE_8K = (
    "Item 1.01. On June 28, 2026, Theravance Biopharma, Inc. entered into an "
    "Agreement and Plan of Merger by and among the Company, Neon Maple Parent "
    "Inc. and Neon Maple Merger Sub.")
TRIAL_WIN_8K = (
    "In the Phase 3 pivotal trial, ALXN1840 met the primary endpoint by "
    "demonstrating rapid and sustained copper mobilization significantly "
    "greater than standard of care over 48 weeks.")
TRIAL_FAIL_8K = (
    "The study did not meet the primary endpoint of mean change in a patient's "
    "Urticaria Activity Score over seven days at 12 weeks at any dose.")
DEAL_RISK_8K = (
    "Forward-looking statements include the risk that the Company may be "
    "acquired on terms unfavorable to stockholders, or that a trial could fail "
    "to meet the primary endpoint.")


class TestMetadataTags(unittest.TestCase):
    """Item codes and deal forms are already in the submissions feed, so
    these tags cost nothing at all — no document is opened."""

    def test_item_codes_that_mean_exactly_one_thing(self):
        for items, kind in (("1.03,9.01", "BANKRUPTCY"),
                            ("3.01", "DELISTING NOTICE"),
                            ("4.02,9.01", "RESTATEMENT"),
                            ("5.02", "LEADERSHIP CHANGE"),
                            ("2.06", "IMPAIRMENT"),
                            ("2.01,9.01", "DEAL CLOSED")):
            got = sf.tag_from_metadata({"form": "8-K", "items": items})
            self.assertEqual(got["kind"], kind, items)
            self.assertIn("8-K item", got["label"])

    def test_the_most_consequential_item_wins(self):
        # a single 8-K routinely carries several items
        got = sf.tag_from_metadata({"form": "8-K", "items": "5.02,1.03,9.01"})
        self.assertEqual(got["kind"], "BANKRUPTCY")

    def test_deal_forms_only_a_company_in_a_deal_ever_files(self):
        for form, kind in (("SC 14D9", "BUYOUT"), ("SC TO-T/A", "BUYOUT"),
                           ("SC TO-C", "BUYOUT"), ("DEFM14A", "MERGER VOTE"),
                           ("PREM14A", "MERGER VOTE")):
            self.assertEqual(sf.tag_from_metadata({"form": form})["kind"], kind, form)

    def test_ordinary_filings_get_nothing(self):
        self.assertIsNone(sf.tag_from_metadata({"form": "8-K", "items": "7.01,9.01"}))
        self.assertIsNone(sf.tag_from_metadata({"form": "10-Q", "items": ""}))
        self.assertIsNone(sf.tag_from_metadata({"form": "4", "items": ""}))


class TestDealAndTrialText(unittest.TestCase):
    def test_being_acquired_is_a_buyout_and_names_the_price(self):
        kind, quote = sf.classify_filing(BUYOUT_8K, "1.01,9.01", "2026-07-27")
        self.assertEqual(kind, "BUYOUT")
        self.assertTrue(quote.startswith("$77.00 per share"), quote[:60])

    def test_buying_something_is_not_being_bought(self):
        kind, _ = sf.classify_filing(ACQUIRER_8K, "8.01", "2026-06-03")
        self.assertEqual(kind, "DEAL CLOSED")

    def test_a_deal_with_no_provable_side_stays_generic(self):
        # tagging this BUYOUT would be a guess about which company is which
        kind, _ = sf.classify_filing(MERGER_VAGUE_8K, "1.01", "2026-06-29")
        self.assertEqual(kind, "MERGER DEAL")

    def test_trial_readouts_both_ways(self):
        self.assertEqual(
            sf.classify_filing(TRIAL_WIN_8K, "7.01", "2026-06-30")[0], "TRIAL SUCCESS")
        self.assertEqual(
            sf.classify_filing(TRIAL_FAIL_8K, "8.01", "2026-06-29")[0], "TRIAL FAILURE")

    def test_deal_and_trial_risk_language_is_not_an_event(self):
        self.assertIsNone(sf.classify_filing(DEAL_RISK_8K, "8.01", "2026-06-29")[0])

    def test_guidance_moved_outside_a_quarterly_report(self):
        # Trex really did raise full-year guidance in a mid-July 8-K, which
        # is a preannouncement rather than an earnings gap
        raised = ("Trex Company Raises Full Year Guidance. Action aligns with "
                  "Trex's stated long term strategic priority to optimize its "
                  "channels for growth.")
        self.assertEqual(sf.classify_filing(raised, "7.01,9.01", "2026-07-13")[0],
                         "GUIDANCE RAISED")
        cut = ("The Company today lowered its full-year revenue outlook to a "
               "range of $410 million to $420 million.")
        self.assertEqual(sf.classify_filing(cut, "8.01", "2026-07-13")[0],
                         "GUIDANCE CUT")

    def test_guidance_inside_a_quarterly_release_is_left_to_earnings(self):
        # AMETEK raises guidance in its 2.02 release every quarter; that day
        # is the earnings gap and earnings already outranks this
        raised = "AMETEK Reports Record Second Quarter Results and Raises Full-Year Guidance"
        self.assertIsNone(sf.classify_filing(raised, "2.02,9.01", "2026-08-04")[0])

    def test_a_response_to_a_short_seller_is_tagged(self):
        text = ("Item 8.01. On August 5, 2026, the Company issued a press "
                "release responding to the short-seller report published "
                "earlier that day.")
        self.assertEqual(sf.classify_filing(text, "8.01", "2026-08-05")[0],
                         "SHORT REPORT")

    def test_ranking_puts_the_consequential_first(self):
        self.assertTrue(sf._rank("BANKRUPTCY") > sf._rank("BUYOUT")
                        > sf._rank("FDA APPROVAL") > sf._rank("MERGER VOTE")
                        > sf._rank("LEADERSHIP CHANGE") > sf._rank(None))

    def test_what_outranks_a_share_sale(self):
        for kind in ("BANKRUPTCY", "BUYOUT", "FDA REJECTION", "TRIAL FAILURE",
                     "RESTATEMENT", "DELISTING NOTICE"):
            self.assertTrue(sf.outranks_offering(kind), kind)
        for kind in ("LEADERSHIP CHANGE", "RESTRUCTURING", "IMPAIRMENT",
                     "AUDITOR CHANGE", "DEAL CLOSED", None):
            self.assertFalse(sf.outranks_offering(kind), kind)

    def test_a_live_deal_pins_the_price(self):
        for kind in ("BUYOUT", "MERGER DEAL", "MERGER VOTE"):
            self.assertTrue(sf.pins_the_price(kind), kind)
        for kind in ("FDA APPROVAL", "DEAL CLOSED", "EARNINGS", None):
            self.assertFalse(sf.pins_the_price(kind), kind)


class TestMovesSession(unittest.TestCase):
    """Which morning a filing can explain. EDGAR already rolls the FILING
    date forward for evening submissions, so going by that alone points a
    day too far."""

    def test_premarket_filing_moves_that_morning(self):
        self.assertEqual(
            sf.moves_session({"accepted": "2026-05-04T07:05:47-04:00",
                              "date": "2026-05-04"}), "2026-05-04")

    def test_after_the_close_moves_the_next_morning(self):
        self.assertEqual(
            sf.moves_session({"accepted": "2026-07-14T17:39:43-04:00",
                              "date": "2026-07-15"}), "2026-07-15")

    def test_friday_evening_lands_on_monday(self):
        self.assertEqual(
            sf.moves_session({"accepted": "2026-08-14T18:00:00-04:00",
                              "date": "2026-08-17"}), "2026-08-17")

    def test_missing_timestamp_falls_back_to_the_filing_date(self):
        self.assertEqual(sf.moves_session({"accepted": "", "date": "2026-08-17"}),
                         "2026-08-17")


class TestFilingEventLookups(unittest.TestCase):
    def wire(self, rows, texts=None):
        net = Net(json_by_url={SUB_URL: submissions(rows)}, text_by_url=texts or {})
        net.__enter__()
        sf.configure(cik_fn=lambda s: 999)
        sf._FDA_CACHE.clear()
        sf._FDA_LOADED.clear()
        self.addCleanup(net.__exit__)
        return net

    def _txt(self, acc):
        return sf._FDA_TXT.format(cik=999, acc=acc.replace("-", ""), accdash=acc)

    def test_todays_decision_is_found_and_quoted(self):
        self.wire([{"date": "2026-05-04", "form": "8-K", "items": "8.01,9.01",
                    "acc": "0001-26-1", "accepted": "2026-05-04T07:05:47.000Z"}],
                  {self._txt("0001-26-1"): FDA_APPROVAL})
        out = sf.latest_event_tag("ZZZZ", ("2026-05-04", "2026-05-03"))
        self.assertEqual(out["kind"], "FDA APPROVAL")
        self.assertIn("ASCENIV", out["quote"])

    def test_verdicts_are_cached_including_the_empty_ones(self):
        net = self.wire([{"date": "2026-05-04", "form": "8-K", "items": "8.01",
                          "acc": "0001-26-1"}],
                        {self._txt("0001-26-1"): FDA_RISK})
        self.assertIsNone(sf.latest_event_tag("ZZZZ", ("2026-05-04",)))
        before = len(net.calls)
        self.assertIsNone(sf.latest_event_tag("ZZZZ", ("2026-05-04",)))
        self.assertEqual(len(net.calls), before, "re-read a document it had already judged")

    def test_history_maps_each_decision_to_the_session_it_moved(self):
        self.wire([{"date": "2026-05-04", "form": "8-K", "items": "8.01",
                    "acc": "0001-26-1", "accepted": "2026-05-04T07:05:00.000Z"},
                   {"date": "2026-06-03", "form": "8-K", "items": "8.01",
                    "acc": "0001-26-2", "accepted": "2026-06-02T21:00:00.000Z"}],
                  {self._txt("0001-26-1"): FDA_APPROVAL,
                   self._txt("0001-26-2"): FDA_CRL})
        got = sf.event_tag_dates("ZZZZ", ["2026-05-04", "2026-06-03", "2026-01-02"])
        self.assertEqual(got, {"2026-05-04": "FDA APPROVAL",
                               "2026-06-03": "FDA REJECTION"})

    def test_the_free_tag_still_lands_when_the_budget_is_spent(self):
        # item codes cost nothing, so a bankruptcy is never missed for the
        # want of a document read
        self.wire([{"date": "2026-05-04", "form": "8-K", "items": "1.03,9.01",
                    "acc": "0001-26-1"}])
        out = sf.latest_event_tag("ZZZZ", ("2026-05-04",), budget=0)
        self.assertEqual(out["kind"], "BANKRUPTCY")
        self.assertIsNone(out["quote"])

    def test_the_most_consequential_filing_of_the_day_wins(self):
        self.wire([{"date": "2026-05-04", "form": "8-K", "items": "5.02",
                    "acc": "0001-26-1"},
                   {"date": "2026-05-04", "form": "SC 14D9", "acc": "0001-26-2"}],
                  {self._txt("0001-26-1"): "An officer resigned."})
        self.assertEqual(sf.latest_event_tag("ZZZZ", ("2026-05-04",))["kind"],
                         "BUYOUT")

    def test_a_read_that_finds_more_than_the_item_code_wins(self):
        # item 8.01 says nothing; the document says the FDA approved a drug
        self.wire([{"date": "2026-05-04", "form": "8-K", "items": "8.01,9.01",
                    "acc": "0001-26-1"}],
                  {self._txt("0001-26-1"): FDA_APPROVAL})
        out = sf.latest_event_tag("ZZZZ", ("2026-05-04",))
        self.assertEqual(out["kind"], "FDA APPROVAL")
        self.assertIn("ASCENIV", out["quote"])

    def test_the_budget_caps_how_many_documents_one_pass_opens(self):
        rows, texts = [], {}
        for i in range(6):
            acc = f"0001-26-{i}"
            rows.append({"date": f"2026-05-0{i + 1}", "form": "8-K",
                         "items": "8.01", "acc": acc,
                         "accepted": f"2026-05-0{i + 1}T07:00:00.000Z"})
            texts[self._txt(acc)] = FDA_APPROVAL
        net = self.wire(rows, texts)
        dates = [r["date"] for r in rows]
        first = sf.event_tag_dates("ZZZZ", dates, budget=2)
        self.assertEqual(len(first), 2, "budget ignored")
        docs = [c for c in net.calls if c.endswith(".txt")]
        self.assertEqual(len(docs), 2)
        # the rest arrive on later passes, and nothing is read twice
        second = sf.event_tag_dates("ZZZZ", dates, budget=2)
        self.assertEqual(len(second), 4)

    def test_offline_is_silent(self):
        clear()
        os.environ["JERRY_NO_NET"] = "1"
        self.assertIsNone(sf.latest_event_tag("ZZZZ", ("2026-05-04",)))
        self.assertEqual(sf.event_tag_dates("ZZZZ", ["2026-05-04"]), {})


class TestEventDates(unittest.TestCase):
    def wire(self, rows):
        net = Net(json_by_url={SUB_URL: submissions(rows)})
        net.__enter__()
        sf.configure(cik_fn=lambda s: 999)
        self.addCleanup(net.__exit__)

    def test_history_uses_unambiguous_forms_only(self):
        self.wire([
            {"date": "2026-05-04", "form": "424B5"},
            {"date": "2026-04-30", "form": "S-3ASR"},
            {"date": "2026-04-20", "form": "424B3"},    # merger/resale: excluded
            {"date": "2026-04-19", "form": "424B2"},    # debt takedown: excluded
            {"date": "2026-04-10", "form": "8-K", "items": "3.02"},
            {"date": "2026-04-01", "form": "10-Q"},
        ])
        got = sf.event_dates("ZZZZ")
        self.assertEqual(got, {"2026-05-04": "OFFERING",
                               "2026-04-30": "DILUTION",
                               "2026-04-10": "OFFERING"})

    def test_a_priced_deal_outranks_a_shelf_filed_the_same_day(self):
        # companies routinely file the shelf and the takedown together
        self.wire([{"date": "2026-09-30", "form": "S-3ASR"},
                   {"date": "2026-09-30", "form": "424B5"}])
        self.assertEqual(sf.event_dates("ZZZZ")["2026-09-30"], "OFFERING")


def form4(code="P", shares=10000, price=10.0, plan=False, officer=None,
          director=False, ten_pct=False, owner="Doe Jane",
          bool_style="text") -> str:
    """A Form 4 in the real shape — including the part that bit: EDGAR
    writes the same booleans as 1/0 in some filers' documents and
    true/false in others', and wraps most values in <value> but not all."""
    yes, no = ("true", "false") if bool_style == "text" else ("1", "0")
    rel = f"<isOfficer>{yes if officer else no}</isOfficer>"
    if officer:
        rel += f"<officerTitle>{officer}</officerTitle>"
    rel += f"<isDirector>{yes if director else no}</isDirector>"
    rel += f"<isTenPercentOwner>{yes if ten_pct else no}</isTenPercentOwner>"
    return f"""<?xml version="1.0"?><ownershipDocument>
      <documentType>4</documentType>
      <issuer><issuerTradingSymbol>ZZZZ</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId>
        <rptOwnerName>{owner}</rptOwnerName>
      </reportingOwnerId><reportingOwnerRelationship>{rel}
      </reportingOwnerRelationship></reportingOwner>
      <aff10b5One>{yes if plan else no}</aff10b5One>
      <nonDerivativeTable><nonDerivativeTransaction>
        <securityTitle><value>Common Stock</value></securityTitle>
        <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>{shares}</value></transactionShares>
          <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        </transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""


class TestFormFourParsing(unittest.TestCase):
    def test_open_market_purchase_is_counted(self):
        rec = sf.parse_form4(form4("P", 1000, 5.0))
        self.assertEqual(rec["bought"], 5000.0)
        self.assertEqual(rec["sold"], 0.0)

    def test_numeric_booleans_read_the_same_as_text_ones(self):
        # ADMA's filer writes true/false, CING's writes 1/0
        a = sf.parse_form4(form4(plan=True, officer="CFO", bool_style="text"))
        b = sf.parse_form4(form4(plan=True, officer="CFO", bool_style="num"))
        self.assertTrue(a["plan"] and b["plan"])
        self.assertEqual(a["role"], b["role"])

    def test_roles_are_spelled_out_from_the_filing(self):
        rec = sf.parse_form4(form4(officer="EVP and Chief Medical Officer",
                                   director=True))
        self.assertEqual(rec["role"], "EVP and Chief Medical Officer, director")

    def test_compensation_codes_are_not_trades(self):
        # A=granted, F=withheld for tax, M=option exercised, G=gift.
        # 156 of the 200 real Form 4s measured were exactly this.
        for code in ("A", "F", "M", "G", "C"):
            rec = sf.parse_form4(form4(code, 50000, 10.0))
            self.assertEqual((rec["bought"], rec["sold"]), (0.0, 0.0), code)


class TestInsiderRollup(unittest.TestCase):
    def wire(self, rows, docs):
        net = Net(json_by_url={SUB_URL: submissions(rows)}, text_by_url=docs)
        net.__enter__()
        sf.configure(cik_fn=lambda s: 999)
        self.addCleanup(net.__exit__)
        return net

    def test_a_cluster_of_small_buys_adds_up(self):
        # CING: four officers and directors filed separately on one day, and
        # only the total ($167K) says anything. The smallest was $9,808.
        rows, docs = [], {}
        for i, (who, amt) in enumerate([("Werth Peter J.", 98053),
                                        ("Schaffer Shane J.", 34317),
                                        ("Callahan Jennifer L.", 24515),
                                        ("Brams Matthew", 9808)]):
            acc = f"0001234567-26-{i:06d}"
            rows.append({"date": "2026-02-10", "form": "4", "acc": acc})
            docs[doc_url(acc)] = form4("P", amt, 1.0, owner=who, director=True)
        self.wire(rows, docs)
        hit = sf.latest_insider("ZZZZ", ("2026-02-10",))
        self.assertEqual(hit["kind"], "INSIDER BUYING")
        self.assertIn("4 insiders", hit["label"])
        self.assertIn("$167K", hit["label"])

    def test_a_lone_trivial_buy_is_dropped(self):
        acc = "0001234567-26-000000"
        self.wire([{"date": "2026-02-10", "form": "4", "acc": acc}],
                  {doc_url(acc): form4("P", 1946, 5.04)})     # $9,808
        self.assertIsNone(sf.latest_insider("ZZZZ", ("2026-02-10",)))

    def test_a_planned_sale_is_never_reported(self):
        # Robinhood's CEO sold $13.5M on 2026-07-08 under a 10b5-1 plan.
        # 93% of real insider sales look like this and none of them are news.
        acc = "0001234567-26-000000"
        self.wire([{"date": "2026-07-08", "form": "4", "acc": acc}],
                  {doc_url(acc): form4("S", 1000000, 13.5, plan=True,
                                       officer="Chief Executive Officer")})
        self.assertIsNone(sf.latest_insider("ZZZZ", ("2026-07-08",)))

    def test_a_discretionary_sale_is_reported_and_says_so(self):
        acc = "0001234567-26-000000"
        self.wire([{"date": "2026-04-14", "form": "4", "acc": acc}],
                  {doc_url(acc): form4("S", 100000, 101.0, plan=False,
                                       owner="Sadana Sumit",
                                       officer="EVP and Chief Business Officer")})
        hit = sf.latest_insider("ZZZZ", ("2026-04-14",))
        self.assertEqual(hit["kind"], "INSIDER SELLING")
        self.assertIn("not under a scheduled plan", hit["label"])
        self.assertIn("EVP and Chief Business Officer", hit["label"])

    def test_buying_outranks_selling_on_the_same_session(self):
        rows, docs = [], {}
        for i, (code, amt) in enumerate([("S", 400000), ("P", 100000)]):
            acc = f"0001234567-26-{i:06d}"
            rows.append({"date": "2026-03-09", "form": "4", "acc": acc})
            docs[doc_url(acc)] = form4(code, amt, 1.0, director=True)
        self.wire(rows, docs)
        self.assertEqual(sf.latest_insider("ZZZZ", ("2026-03-09",))["kind"],
                         "INSIDER BUYING")

    def test_an_evening_form4_moves_the_next_morning(self):
        # ADMA's Form 4s are accepted at 21:00 ET; that is tomorrow's gap.
        acc = "0001234567-26-000000"
        self.wire([{"date": "2026-07-28", "form": "4", "acc": acc,
                    "accepted": "2026-07-29T01:00:00.000Z"}],   # 21:00 ET
                  {doc_url(acc): form4("P", 20000, 10.0, director=True)})
        self.assertIsNone(sf.latest_insider("ZZZZ", ("2026-07-28",)))
        self.assertEqual(sf.latest_insider("ZZZZ", ("2026-07-29",))["kind"],
                         "INSIDER BUYING")

    def test_offline_reads_nothing_and_caches_nothing(self):
        clear()
        os.environ["JERRY_NO_NET"] = "1"
        self.assertIsNone(sf.latest_insider("ZZZZ", ("2026-02-10",)))


class TestOwnershipAndCorporateActions(unittest.TestCase):
    def wire(self, rows, docs=None):
        net = Net(json_by_url={SUB_URL: submissions(rows)}, text_by_url=docs or {})
        net.__enter__()
        sf.configure(cik_fn=lambda s: 999)
        self.addCleanup(net.__exit__)

    def test_a_first_13d_is_an_activist_stake(self):
        self.wire([{"date": "2026-05-04", "form": "SC 13D"}])
        self.assertEqual(sf.latest_event_tag("ZZZZ", ("2026-05-04",))["kind"],
                         "ACTIVIST STAKE")

    def test_a_13d_amendment_is_not(self):
        # 90 of the 121 real Schedule 13D filings measured were amendments —
        # the same holder adding, trimming or leaving, and the form cannot say.
        self.wire([{"date": "2026-05-04", "form": "SC 13D/A"}])
        self.assertIsNone(sf.latest_event_tag("ZZZZ", ("2026-05-04",)))

    def test_a_passive_13g_is_not_an_activist_stake(self):
        self.wire([{"date": "2026-05-04", "form": "SC 13G"}])
        self.assertIsNone(sf.latest_event_tag("ZZZZ", ("2026-05-04",)))

    def test_late_filing_notices_are_free_from_the_form_type(self):
        for form in ("NT 10-K", "NT 10-Q"):
            self.wire([{"date": "2026-05-04", "form": form}])
            self.assertEqual(sf.latest_event_tag("ZZZZ", ("2026-05-04",))["kind"],
                             "LATE FILING", form)

    def test_a_late_filing_outranks_an_offering(self):
        self.assertTrue(sf.outranks_offering("LATE FILING"))
        self.assertTrue(sf.outranks_offering("ACTIVIST STAKE"))
        self.assertTrue(sf.outranks_offering("REVERSE SPLIT"))
        # unchanged from before: these still lose to a share sale
        self.assertFalse(sf.outranks_offering("GUIDANCE CUT"))
        self.assertFalse(sf.outranks_offering("INSIDER BUYING"))
        self.assertFalse(sf.outranks_offering("BUYBACK"))

    def test_only_a_reverse_split_rescales_the_history(self):
        self.assertTrue(sf.rescales_history("REVERSE SPLIT"))
        for kind in ("BUYOUT", "OFFERING", "INSIDER BUYING", None):
            self.assertFalse(sf.rescales_history(kind))


# Quoted from the filings these were verified against: Aethlon Medical's
# reverse-split 8-K, Onity Group's buyback authorization, and Tyler
# Technologies' June 8-K, which reports a balance and must NOT tag.
SPLIT_8K = ("On July 30, 2026, Aethlon Medical, Inc., a Nevada corporation, "
            "filed a Certificate of Change pursuant to Section 78.209 of the "
            "Nevada Revised Statutes with the Secretary of State of the State "
            "of Nevada authorizing a 1-for-5 reverse stock split of the "
            "Company's issued and outstanding shares of common stock.")
BUYBACK_8K = ("Authorization of Share Repurchase Program. On June 1, 2026, "
              "Onity's Board of Directors authorized a share repurchase "
              "program for an aggregate amount of up to $20.0 million of the "
              "Company's issued and outstanding common stock.")
BUYBACK_BALANCE_8K = ("As of June 12, 2026, we have remaining authorization "
                      "from our Board of Directors to repurchase up to $332.7 "
                      "million of our common stock.")
BUYBACK_PLAN_8K = ("On June 12, 2026, Tyler entered into a Rule 10b5-1 "
                   "trading plan with a brokerage firm to repurchase up to "
                   "$150.0 million of its common stock.")


class TestCorporateActionClassifier(unittest.TestCase):
    def test_a_reverse_split_is_tagged_with_its_ratio(self):
        kind, quote = sf.classify_filing(SPLIT_8K, "", "2026-07-31")
        self.assertEqual(kind, "REVERSE SPLIT")
        self.assertTrue(quote.startswith("1-for-5 · "), quote)

    def test_a_new_buyback_authorization_is_tagged(self):
        kind, quote = sf.classify_filing(BUYBACK_8K, "", "2026-06-02")
        self.assertEqual(kind, "BUYBACK")
        self.assertIn("$20.0 million", quote)

    def test_the_balance_left_on_a_program_is_not_an_authorization(self):
        self.assertEqual(sf.classify_filing(BUYBACK_BALANCE_8K, "", "2026-06-12")[0],
                         None)

    def test_a_plan_to_execute_an_old_program_is_not_an_authorization(self):
        self.assertEqual(sf.classify_filing(BUYBACK_PLAN_8K, "", "2026-06-12")[0],
                         None)

    def test_a_quarterly_release_never_tags_a_buyback(self):
        self.assertEqual(sf.classify_filing(BUYBACK_8K, "2.02", "2026-06-02")[0],
                         None)

    def test_a_split_that_is_not_happening_is_not_tagged(self):
        txt = ("The Company does not intend to effect a reverse stock split "
               "at this time.")
        self.assertIsNone(sf.classify_filing(txt, "", "2026-06-02")[0])


class TestWordsSplitAcrossTags(unittest.TestCase):
    """Filers break words across styling tags. Every tag becoming a space
    turned Aethlon's "Jul</span>y 30, 2026" into "Jul y 30, 2026" in a
    sentence Jerry reads."""

    def test_a_word_split_by_an_inline_tag_is_rejoined(self):
        got = sf._plain(b"<p>On Jul</span>y 30, 2026, the Company filed.</p>")
        self.assertIn("July 30, 2026", got)

    def test_separate_words_are_still_kept_apart(self):
        got = sf._plain(b"<td>Item 1.01</td><td>Entry into an Agreement</td>")
        self.assertIn("1.01 Entry", got)

    def test_a_tag_between_words_still_separates_them(self):
        # The trap: a close-then-open pair is the seam between two styled
        # words. Fusing it to "approvedthe" would stop the classifier's own
        # patterns matching, which is far worse than the blemish being fixed.
        got = sf._plain(b"<span>approved</span><span>the offering</span>")
        self.assertIn("approved the offering", got)

    def test_a_split_word_still_reaches_the_classifier(self):
        txt = sf._plain(b"<p>On Jul</span>y 30, 2026, the Board authoriz"
                        b"<span>ed a 1-for-8 reverse stock split.</p>")
        kind, quote = sf.classify_filing(txt, "", "2026-07-31")
        self.assertEqual(kind, "REVERSE SPLIT")
        self.assertIn("1-for-8", quote)


if __name__ == "__main__":
    unittest.main()
