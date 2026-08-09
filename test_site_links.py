"""Tests for site_links.py — Simply Wall St deep-link construction (v3.70).

The two reference URLs the user supplied are pinned as exact-match tests, so
any future change to the slug logic that breaks them fails CI.
"""
from __future__ import annotations

import unittest

import site_links as sl


class TestKnownGoodUrls(unittest.TestCase):
    """Exact reproductions of the two verified simplywall.st URLs."""

    def test_nvda(self):
        r = sl.simplywallst_url("NVDA", {
            "company": "NVIDIA Corporation", "sector": "Technology",
            "industry": "Semiconductors", "exchange": "NasdaqGS"})
        self.assertEqual(
            r["url"], "https://simplywall.st/stocks/us/semiconductors/nasdaq-nvda/nvidia")

    def test_sndk(self):
        r = sl.simplywallst_url("SNDK", {
            "company": "Sandisk Corporation", "sector": "Technology",
            "industry": "Computer Hardware", "exchange": "NasdaqGS"})
        self.assertEqual(
            r["url"], "https://simplywall.st/stocks/us/tech/nasdaq-sndk/sandisk")

    def test_never_claims_verified(self):
        # simplywall.st blocks this deployment, so the scheme could not be
        # verified end-to-end — the payload must say so.
        r = sl.simplywallst_url("NVDA", {"company": "NVIDIA Corporation",
                                         "industry": "Semiconductors",
                                         "exchange": "NasdaqGS"})
        self.assertFalse(r["verified"])
        self.assertIn("derived", r["note"])


class TestSlugify(unittest.TestCase):
    def test_legal_suffixes_stripped(self):
        self.assertEqual(sl.slugify("NVIDIA Corporation"), "nvidia")
        self.assertEqual(sl.slugify("Cisco Systems, Inc."), "cisco-systems")
        self.assertEqual(sl.slugify("Sandisk Corporation"), "sandisk")
        self.assertEqual(sl.slugify("Alphabet Inc."), "alphabet")
        self.assertEqual(sl.slugify("Barclays PLC"), "barclays")

    def test_dangling_connector_removed(self):
        # "& Co." → "and" + "co"; both must go, not leave "…-and".
        self.assertEqual(sl.slugify("JPMorgan Chase & Co."), "jpmorgan-chase")
        self.assertEqual(sl.slugify("The Procter & Gamble Company"), "procter-and-gamble")

    def test_accents_and_punctuation(self):
        self.assertEqual(sl.slugify("Nestlé S.A."), "nestle")
        self.assertEqual(sl.slugify("AT&T Inc."), "at-and-t")

    def test_empty_and_junk(self):
        self.assertEqual(sl.slugify(""), "")
        self.assertEqual(sl.slugify(None), "")
        self.assertEqual(sl.slugify("Inc."), "")          # nothing but a suffix


class TestSectorSlug(unittest.TestCase):
    def test_industry_wins_over_sector(self):
        # NVDA: industry "Semiconductors" must beat sector "Technology".
        self.assertEqual(sl.sector_slug("Semiconductors", "Technology"), "semiconductors")

    def test_sector_fallback(self):
        self.assertEqual(sl.sector_slug(None, "Technology"), "tech")
        self.assertEqual(sl.sector_slug(None, "Energy"), "energy")

    def test_substring_match(self):
        self.assertEqual(sl.sector_slug("Banks—Regional", None), "banks")

    def test_unknown_defaults_to_tech(self):
        self.assertEqual(sl.sector_slug("Nonexistent Widgetry", None), "tech")


class TestExchange(unittest.TestCase):
    def test_common_exchanges(self):
        self.assertEqual(sl.exchange_slug("NasdaqGS"), ("us", "nasdaq"))
        self.assertEqual(sl.exchange_slug("NMS"), ("us", "nasdaq"))
        self.assertEqual(sl.exchange_slug("NYSE"), ("us", "nyse"))
        self.assertEqual(sl.exchange_slug("NYQ"), ("us", "nyse"))
        self.assertEqual(sl.exchange_slug("TOR"), ("ca", "tsx"))
        # Substring matching handles yfinance's long-form names too.
        self.assertEqual(sl.exchange_slug("Toronto"), ("ca", "tsx"))
        self.assertIsNone(sl.exchange_slug("Nonexistent Exchange"))

    def test_missing(self):
        self.assertIsNone(sl.exchange_slug(None))
        self.assertIsNone(sl.exchange_slug(""))


class TestMissingData(unittest.TestCase):
    def test_no_company_no_link(self):
        r = sl.simplywallst_url("ZZZZ", {"exchange": "NasdaqGS", "company": None})
        self.assertIsNone(r["url"])
        self.assertIn("company name", r["reason"])

    def test_no_exchange_no_link(self):
        r = sl.simplywallst_url("ZZZZ", {"company": "Something Inc.", "exchange": None})
        self.assertIsNone(r["url"])
        self.assertIn("listing exchange", r["reason"])

    def test_empty_profile_never_guesses(self):
        r = sl.simplywallst_url("ZZZZ", {})
        self.assertIsNone(r["url"])
        r2 = sl.simplywallst_url("ZZZZ", None)
        self.assertIsNone(r2["url"])

    def test_no_symbol(self):
        self.assertIsNone(sl.simplywallst_url("", {"company": "X Inc."})["url"])


class TestUrlShape(unittest.TestCase):
    def test_five_segments_and_lowercase_ticker(self):
        r = sl.simplywallst_url("CSCO", {
            "company": "Cisco Systems, Inc.", "sector": "Technology",
            "industry": "Communication Equipment", "exchange": "NasdaqGS"})
        tail = r["url"].replace(sl.BASE + "/", "").split("/")
        self.assertEqual(len(tail), 4)                       # country/sector/ex-tick/name
        self.assertEqual(tail[0], "us")
        self.assertEqual(tail[2], "nasdaq-csco")             # ticker lowercased
        self.assertNotIn(" ", r["url"])

    def test_derived_block_is_transparent(self):
        r = sl.simplywallst_url("JPM", {
            "company": "JPMorgan Chase & Co.", "sector": "Financial Services",
            "industry": "Banks—Diversified", "exchange": "NYSE"})
        d = r["derived"]
        self.assertEqual(d["exchange"], "nyse")
        self.assertEqual(d["company_slug"], "jpmorgan-chase")
        self.assertEqual(d["sector_segment"], "banks")
        self.assertEqual(d["from_industry"], "Banks—Diversified")


if __name__ == "__main__":
    unittest.main()
