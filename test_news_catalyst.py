"""Tests for the headline-derived catalysts.

Every headline in here is real — pulled from the app's own news feed while
this was written. This is the weakest evidence the Gap Scan accepts, so the
rules that keep it from lying are the whole point:

  * a named short-selling firm or an explicit "short-seller report", never
    the word "short" on its own
  * the headline has to be ABOUT the company whose feed it arrived in
  * two days old at most
"""

import unittest
from datetime import datetime, timedelta, timezone

import news_catalyst as nc

NOW = datetime(2026, 8, 17, 13, 0, tzinfo=timezone.utc)


def item(title, hours_ago=2, source="Reuters", url="https://example.test/x"):
    return {"title": title, "source": source, "url": url,
            "published": (NOW - timedelta(hours=hours_ago)).isoformat()}


def feed(*items):
    return {"items": list(items)}


class TestHeadlineRules(unittest.TestCase):
    def test_named_short_sellers_count(self):
        for t in ("FTK Clocks Worst Day In Over 6 Years After Wolfpack "
                  "Research Flags Canceled Deal, Shorts Stock",
                  "Hindenburg Research takes aim at ACME Corp",
                  "Short Seller Hunterbrook Attacked Bloom Energy's Supply-Chain Claims",
                  "Muddy Waters says ACME stonewalled 11 questions"):
            kind, why = nc.classify_headline(t)
            self.assertEqual(kind, "SHORT REPORT", t[:40])
            self.assertTrue(why)

    def test_an_explicit_short_seller_report_counts_without_a_firm_name(self):
        self.assertEqual(
            nc.classify_headline("ACME stock plummets on short seller report")[0],
            "SHORT REPORT")

    def test_short_interest_is_not_a_short_report(self):
        # the trap: "short" appears in half of all market commentary
        for t in ("SoFi Stock Short Interest Builds",
                  "ACME shares are heavily shorted into earnings",
                  "Three short-term trades for a choppy tape",
                  "Defiance Launches MUZ: The First 2X Short ETF for Micron"):
            self.assertIsNone(nc.classify_headline(t)[0], t[:40])

    def test_index_adds_and_drops_are_told_apart(self):
        self.assertEqual(nc.classify_headline("Reddit Will Be Added to the S&P 500")[0],
                         "INDEX ADD")
        self.assertEqual(nc.classify_headline(
            "Elicio Therapeutics Added to Russell 2000 and Russell 3000 Indexes")[0],
            "INDEX ADD")
        self.assertEqual(nc.classify_headline(
            "ACME to be removed from the S&P MidCap 400")[0], "INDEX DROP")

    def test_ordinary_market_chatter_is_not_a_catalyst(self):
        for t in ("ACME Corp announces quarterly dividend",
                  "Why ACME stock is up 4% today",
                  "ACME unveils its next-generation widget"):
            self.assertIsNone(nc.classify_headline(t)[0], t[:40])


class TestFeedSelection(unittest.TestCase):
    def test_the_freshest_qualifying_headline_wins(self):
        got = nc.catalyst_from_news("ACME", feed(
            item("ACME Corp names new CFO"),
            item("Hindenburg Research publishes report on ACME Corp", hours_ago=3),
        ), "ACME Corp", now=NOW)
        self.assertEqual(got["kind"], "SHORT REPORT")
        self.assertIn("Hindenburg", got["quote"])
        self.assertEqual(got["evidence"], "headline")
        self.assertEqual(got["source"], "Reuters")
        self.assertTrue(got["url"])

    def test_a_story_about_another_company_is_ignored(self):
        # per-symbol feeds carry adjacent stories constantly
        got = nc.catalyst_from_news("MU", feed(
            item("Muddy Waters publishes short report on Techtronic"),
        ), "Micron Technology", now=NOW)
        self.assertIsNone(got)

    def test_the_ticker_alone_is_enough_to_claim_the_story(self):
        got = nc.catalyst_from_news("FTK", feed(
            item("FTK slides after Wolfpack Research report"),
        ), None, now=NOW)
        self.assertEqual(got["kind"], "SHORT REPORT")

    def test_last_week_is_not_this_morning(self):
        got = nc.catalyst_from_news("RDDT", feed(
            item("Reddit Will Be Added to the S&P 500", hours_ago=72),
        ), "Reddit Inc", now=NOW)
        self.assertIsNone(got, "a three-day-old story must not tag today's gap")
        stretched = nc.catalyst_from_news("RDDT", feed(
            item("Reddit Will Be Added to the S&P 500", hours_ago=72),
        ), "Reddit Inc", now=NOW, max_age_hours=96)
        self.assertEqual(stretched["kind"], "INDEX ADD")

    def test_undated_and_future_stories_are_dropped(self):
        self.assertIsNone(nc.catalyst_from_news("ACME", feed(
            {"title": "Hindenburg Research publishes report on ACME Corp",
             "published": None}), "ACME Corp", now=NOW))
        self.assertIsNone(nc.catalyst_from_news("ACME", feed(
            item("Hindenburg Research publishes report on ACME Corp",
                 hours_ago=-48)), "ACME Corp", now=NOW))

    def test_an_empty_or_broken_feed_is_silent(self):
        for f in ({}, {"items": []}, {"items": [{"title": ""}]}, None):
            self.assertIsNone(nc.catalyst_from_news("ACME", f, "ACME Corp", now=NOW))


if __name__ == "__main__":
    unittest.main()
