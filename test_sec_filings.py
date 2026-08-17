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
    sf._TICKERS[0], sf._TICKERS[1] = 0.0, {}
    sf.configure(cik_fn=None)


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


if __name__ == "__main__":
    unittest.main()
