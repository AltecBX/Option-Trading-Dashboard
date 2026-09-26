"""Insider & Congress buying (v5.34).

Rows have the shape of UW's own spec examples for /api/insider/transactions
and /api/congress/recent-trades.
"""

from __future__ import annotations

import unittest
from datetime import date

import smart_buyers as SB

TODAY = date(2026, 9, 25)


def ins(ticker, name, amount, price, day, code="P", title="", form="4", **kw):
    return {"ticker": ticker, "owner_name": name, "amount": amount, "price": str(price),
            "transaction_date": day, "filing_date": day, "transaction_code": code,
            "officer_title": title, "formtype": form, "sector": "Technology",
            "marketcap": "1375122749418", "next_earnings_date": "2026-10-28", **kw}


def cong(ticker, name, day, kind="Buy", amount="$15,001 - $50,000", chamber="house"):
    return {"ticker": ticker, "name": name, "transaction_date": day, "txn_type": kind,
            "amounts": amount, "filed_at_date": day, "member_type": chamber}


class FakeUW:
    def __init__(self, insiders=None, congress=None):
        self.i, self.c, self.asked = insiders, congress, []

    def insider_buys(self, start_date, limit=500, min_value=0, page=0):
        self.asked.append(("insider_buys", start_date, limit, page))
        if isinstance(self.i, Exception):
            raise self.i
        return self.i if page == 0 else []

    def congress_recent(self, limit=200, date=None):
        self.asked.append(("congress_recent", limit, date))
        return self.c if date is None else []


INSIDERS = [
    # A cluster: three different people at ACME, including the CEO.
    ins("ACME", "DOE JANE", 10000, 50, "2026-09-20", title="CEO"),
    ins("ACME", "ROE RICK", 2000, 50, "2026-09-18", title="Director"),
    ins("ACME", "POE PAT", 1000, 51, "2026-09-10"),
    ins("ACME", "DOE JANE", 2000, 49, "2026-09-05", title="CEO"),       # same person twice
    # One big buyer at BIGCO.
    ins("BIGCO", "RICH RAY", 100000, 80, "2026-09-15", title="10% owner"),
    # Not buys, or too old, or not a trade.
    ins("ACME", "SELL SAM", -5000, 50, "2026-09-19", code="S"),
    ins("OLDCO", "OLD OLLY", 1000, 10, "2026-07-01"),
    ins("NOTE", "FORM ONE", 1000, 10, "2026-09-20", form="144"),
    ins("ZERO", "ZED", 1000, 0, "2026-09-20"),
]
CONGRESS = [
    cong("ACME", "Stephen Cohen", "2026-09-06"),
    cong("ACME", "Deborah Ross", "2026-09-12", amount="$1,001 - $15,000"),
    cong("MSFT", "Stephen Cohen", "2026-08-30"),
    cong("MSFT", "Stephen Cohen", "2026-08-01", kind="Sell"),
    cong("FHWVOX", "Fund Person", "2026-09-01"),          # a mutual fund, not a stock
    cong("TSLA", "Too Old", "2026-06-01"),
]


class Insiders(unittest.TestCase):
    def setUp(self):
        self.out = SB.build(FakeUW(INSIDERS, CONGRESS), watchlist=["bigco"], today=TODAY)

    def test_only_open_market_buys_in_30_days_grouped_by_stock(self):
        self.assertEqual(["ACME", "BIGCO"], [x["symbol"] for x in self.out["insiders"]],
                         "a cluster of three outranks one bigger buyer; sells, 144s, old rows and $0 are out")

    def test_a_cluster_counts_people_not_filings(self):
        acme = self.out["insiders"][0]
        self.assertEqual(3, acme["buyers"])
        self.assertTrue(acme["cluster"])
        self.assertTrue(acme["chief"], "the CEO bought")
        self.assertEqual(500000 + 98000 + 100000 + 51000, acme["value"])
        self.assertEqual({"name": "Doe Jane", "title": "CEO", "value": 598000.0, "date": "2026-09-20"},
                         acme["top"])
        self.assertIn("3 insiders bought $749K in 30 days. Biggest: CEO Doe Jane, $598K on Sep 20.", acme["text"])

    def test_the_watchlist_is_marked(self):
        by = {x["symbol"]: x for x in self.out["insiders"]}
        self.assertTrue(by["BIGCO"]["watchlist"])
        self.assertFalse(by["ACME"]["watchlist"])
        self.assertFalse(by["BIGCO"]["cluster"])

    def test_the_window_is_asked_of_uw(self):
        uw = FakeUW([], [])
        SB.build(uw, today=TODAY)
        self.assertIn(("insider_buys", "2026-08-26", 500, 0), uw.asked)


class Congress(unittest.TestCase):
    def setUp(self):
        self.out = SB.build(FakeUW(INSIDERS, CONGRESS), today=TODAY)

    def test_buys_of_listed_stocks_in_60_days(self):
        self.assertEqual(["ACME", "MSFT"], [x["symbol"] for x in self.out["congress"]])
        acme = self.out["congress"][0]
        self.assertEqual(2, acme["members"])
        self.assertEqual("Deborah Ross", acme["trades"][0]["name"], "newest first")
        self.assertIn("Latest: Deborah Ross, $1,001 - $15,000 on Sep 12.", acme["text"])

    def test_both_lists_meet(self):
        both = self.out["both"]
        self.assertEqual(["ACME"], [b["symbol"] for b in both])
        self.assertEqual("Insiders (3) and Congress (2) are both buying.", both[0]["text"])


class NeverRaises(unittest.TestCase):
    def test_a_failing_feed_is_listed_and_the_other_still_answers(self):
        out = SB.build(FakeUW(RuntimeError("down"), CONGRESS), today=TODAY)
        self.assertEqual(["insider_buys"], out["missing"])
        self.assertEqual([], out["insiders"])
        self.assertEqual(2, len(out["congress"]))

    def test_nothing_at_all(self):
        out = SB.build(FakeUW(None, None), today=TODAY)
        self.assertEqual(["insider_buys", "congress_recent"], out["missing"])
        self.assertEqual([], out["both"])


class EveryPageIsRead(unittest.TestCase):
    """Codex, #421: a busy month holds more than one call's rows. Every page
    inside the window is read before anything is grouped."""

    def test_insider_pages_until_a_short_one(self):
        full = [ins(f"T{i:04d}", f"P{i}", 100, 10, "2026-09-20") for i in range(SB.INSIDER_PAGE)]
        tail = [ins("LATE", "LATE ONE", 100, 10, "2026-09-20"), ins("LATE", "LATE TWO", 100, 10, "2026-09-19"),
                ins("LATE", "LATE THREE", 100, 10, "2026-09-18")]

        class Paged(FakeUW):
            def insider_buys(self, start_date, limit=500, min_value=0, page=0):
                self.asked.append(page)
                return [full, tail][page] if page < 2 else []
        uw = Paged()
        rows, cut = SB.fetch_insiders(uw, date(2026, 8, 26))
        self.assertEqual([0, 1], uw.asked)
        self.assertEqual(SB.INSIDER_PAGE + 3, len(rows))
        self.assertFalse(cut)
        out = SB.build(Paged(congress=[]), today=TODAY)
        late = [x for x in out["insiders"] if x["symbol"] == "LATE"]
        self.assertTrue(late and late[0]["cluster"], "the cluster on page two was found")
        self.assertEqual([], out["partial"])

    def test_insider_cap_and_a_failed_later_page_say_partial(self):
        full = [ins("X", f"P{i}", 1, 10, "2026-09-20") for i in range(SB.INSIDER_PAGE)]

        class Endless(FakeUW):
            def insider_buys(self, start_date, limit=500, min_value=0, page=0):
                return full
        rows, cut = SB.fetch_insiders(Endless(), date(2026, 8, 26))
        self.assertTrue(cut)
        self.assertEqual(SB.INSIDER_PAGE * SB.INSIDER_MAX_PAGES, len(rows))

        class Breaks(FakeUW):
            def insider_buys(self, start_date, limit=500, min_value=0, page=0):
                return full if page == 0 else None
        rows, cut = SB.fetch_insiders(Breaks(), date(2026, 8, 26))
        self.assertEqual((SB.INSIDER_PAGE, True), (len(rows), cut))
        self.assertEqual(["insider_buys"], SB.build(Breaks(congress=[]), today=TODAY)["partial"])

    def test_congress_pages_back_by_date_and_drops_repeats(self):
        first = [cong("AAA", f"M{i}", "2026-09-20") for i in range(SB.CONGRESS_PAGE - 1)] + \
                [cong("BBB", "Edge", "2026-09-10")]
        second = [cong("BBB", "Edge", "2026-09-10"), cong("OLD", "Earlier", "2026-09-01"),
                  cong("ANC", "Ancient", "2026-06-01")]

        class Dated(FakeUW):
            def congress_recent(self, limit=200, date=None):
                self.asked.append(date)
                return first if date is None else second if date == "2026-09-10" else []
        uw = Dated(insiders=[])
        rows, cut = SB.fetch_congress(uw, date(2026, 7, 28))
        self.assertEqual([None, "2026-09-10"], uw.asked)
        self.assertFalse(cut)
        self.assertEqual(SB.CONGRESS_PAGE + 2, len(rows), "the repeat at the seam is dropped")
        self.assertIn("OLD", [x["symbol"] for x in SB.build(Dated(insiders=[]), today=TODAY)["congress"]])

    def test_congress_stops_when_a_page_brings_nothing_new(self):
        same = [cong("AAA", f"M{i}", "2026-09-20") for i in range(SB.CONGRESS_PAGE)]

        class Stuck(FakeUW):
            def congress_recent(self, limit=200, date=None):
                self.asked.append(date)
                return same
        uw = Stuck(insiders=[])
        rows, cut = SB.fetch_congress(uw, date(2026, 7, 28))
        self.assertEqual(2, len(uw.asked), "a page of repeats ends the walk")
        self.assertEqual(SB.CONGRESS_PAGE, len(rows))


class TheClient(unittest.TestCase):
    def test_market_wide_calls_use_the_published_params(self):
        import unusual_whales_client as U
        c = U.UWClient("k")
        seen = []
        c._get = lambda key, params: seen.append((key, params)) or []
        c.insider_buys("2026-08-26", limit=900)
        c.congress_recent()
        c.insider_buys("2026-08-26", page=2)
        c.congress_recent(date="2026-09-10")
        self.assertEqual(("insider_transactions", {"transaction_codes[]": "P", "common_stock_only": "true",
                                                   "start_date": "2026-08-26", "limit": "500"}), seen[0])
        self.assertEqual(("congress_trades", {"limit": "200"}), seen[1])
        self.assertEqual("2", seen[2][1]["page"])
        self.assertEqual({"limit": "200", "date": "2026-09-10"}, seen[3][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
