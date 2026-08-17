"""Tests for the Gap Scan catalyst tagger in options_dashboard.

The tag drives what Jerry sees in the Catalyst column, so the mapping from
real analyst-feed rows to UPGRADE / DOWNGRADE / ANALYST ACTION is pinned
here. The rule that matters: labels are quoted from the data (firm + grade
change), never inferred, and a row the feed itself calls 'reiterate' or
'unknown' produces NO tag rather than a vague one.
"""

import os
import unittest
from datetime import date, timedelta

os.environ.setdefault("JERRY_NO_NET", "1")

import options_dashboard as od


# Read at CALL time, never at import: _gap_catalyst asks the calendar what
# "today" is, and the time-travel guard imports modules outside its freeze —
# so an import-time constant would hold the real date while the code under
# test saw the shifted one. A fixed date would also make these tests pass
# only in the week they were written.
def days_now() -> tuple:
    t = date.today()
    return (t.isoformat(), (t - timedelta(days=1)).isoformat())


def stale_day() -> str:
    return (date.today() - timedelta(days=45)).isoformat()


class FakeBoard:
    def __init__(self, actions):
        self._actions = actions

    def get_board(self):
        return {"actions": self._actions}


class FakeAnalystClient:
    def __init__(self, history):
        self._history = history

    def get_client(self):
        return self

    def get_analyst_data(self, symbol, current_price=None, force_refresh=False):
        return {"history": self._history}


class CatalystBase(unittest.TestCase):
    def setUp(self):
        self._board = (od._ANALYST_BOARD_AVAILABLE, od._analyst_board)
        self._client = (od._ANALYST_AVAILABLE, od._analyst_client)

    def tearDown(self):
        od._ANALYST_BOARD_AVAILABLE, od._analyst_board = self._board
        od._ANALYST_AVAILABLE, od._analyst_client = self._client

    def wire(self, board_actions=None, history=None):
        od._ANALYST_BOARD_AVAILABLE = board_actions is not None
        od._analyst_board = FakeBoard(board_actions or [])
        od._ANALYST_AVAILABLE = history is not None
        od._analyst_client = FakeAnalystClient(history or [])


class TestAnalystActionMapping(CatalystBase):
    def _row(self, cls, **kw):
        base = {"ticker": "MU", "date": days_now()[0], "action_class": cls,
                "firm": "Morgan Stanley", "prior_grade": "hold",
                "new_grade": "buy"}
        base.update(kw)
        return base

    def test_upgrade_named_and_labeled_from_data(self):
        self.wire(board_actions=[self._row("upgrade")])
        out = od._gap_analyst_action("MU", days_now())
        self.assertEqual(out["kind"], "UPGRADE")
        self.assertIn("Morgan Stanley", out["label"])
        self.assertIn("hold → buy", out["label"])

    def test_downgrade_named(self):
        self.wire(board_actions=[self._row("downgrade", prior_grade="buy",
                                           new_grade="hold")])
        out = od._gap_analyst_action("MU", days_now())
        self.assertEqual(out["kind"], "DOWNGRADE")
        self.assertIn("buy → hold", out["label"])

    def test_initiation_and_target_change_stay_generic(self):
        self.wire(board_actions=[self._row("initiate", prior_grade=None)])
        out = od._gap_analyst_action("MU", days_now())
        self.assertEqual(out["kind"], "ANALYST ACTION")
        self.assertIn("initiated", out["label"])

        self.wire(board_actions=[self._row("target_change",
                                           target_change_pct=12.4)])
        out = od._gap_analyst_action("MU", days_now())
        self.assertEqual(out["kind"], "ANALYST ACTION")
        self.assertIn("raised target", out["label"])
        self.assertIn("12%", out["label"])

        self.wire(board_actions=[self._row("target_change",
                                           target_change_pct=-8.0)])
        self.assertIn("cut target", od._gap_analyst_action("MU", days_now())["label"])

    def test_reiterate_and_unknown_produce_no_tag(self):
        for cls in ("reiterate", "unknown", ""):
            self.wire(board_actions=[self._row(cls)])
            self.assertIsNone(od._gap_analyst_action("MU", days_now()),
                              f"{cls!r} must not be tagged as a catalyst")

    def test_missing_grades_still_label_the_firm(self):
        self.wire(board_actions=[self._row("upgrade", prior_grade=None,
                                           new_grade=None)])
        out = od._gap_analyst_action("MU", days_now())
        self.assertEqual(out["kind"], "UPGRADE")
        self.assertEqual(out["label"], "Morgan Stanley")

    def test_stale_and_other_ticker_rows_ignored(self):
        self.wire(board_actions=[self._row("upgrade", date=stale_day())])
        self.assertIsNone(od._gap_analyst_action("MU", days_now()))
        self.wire(board_actions=[self._row("upgrade", ticker="AAPL")])
        self.assertIsNone(od._gap_analyst_action("MU", days_now()))

    def test_per_symbol_fallback_when_board_has_nothing(self):
        # a gapper outside the 8am board universe must still get tagged
        self.wire(board_actions=[],
                  history=[{"date": days_now()[1], "action_class": "downgrade",
                            "firm": "Citi", "prior_grade": "buy",
                            "new_grade": "neutral"}])
        out = od._gap_analyst_action("MU", days_now())
        self.assertEqual(out["kind"], "DOWNGRADE")
        self.assertIn("Citi", out["label"])

    def test_board_wins_over_per_symbol_lookup(self):
        self.wire(board_actions=[self._row("upgrade")],
                  history=[{"date": days_now()[0], "action_class": "downgrade",
                            "firm": "Citi"}])
        self.assertEqual(od._gap_analyst_action("MU", days_now())["kind"], "UPGRADE")

    def test_no_sources_is_silent(self):
        od._ANALYST_BOARD_AVAILABLE, od._analyst_board = False, None
        od._ANALYST_AVAILABLE, od._analyst_client = False, None
        self.assertIsNone(od._gap_analyst_action("MU", days_now()))


class FakeSec:
    """Stands in for sec_filings with whatever EDGAR would have returned."""

    def __init__(self, event=None, dates=None, fda=None, fda_dates_map=None):
        self._event, self._dates = event, dates or {}
        self._fda, self._fda_dates = fda, fda_dates_map or {}

    def latest_event(self, symbol, days):
        if self._event and str(self._event.get("date")) in days:
            return self._event
        return None

    def event_dates(self, symbol):
        return self._dates

    def latest_event_tag(self, symbol, days, budget=4):
        if self._fda and str(self._fda.get("date")) in days:
            return self._fda
        return None

    def event_tag_dates(self, symbol, dates, budget=8):
        return {d: k for d, k in self._fda_dates.items() if d in set(dates or [])}

    def outranks_offering(self, kind):
        import sec_filings as _sf
        return _sf.outranks_offering(kind)

    def pins_the_price(self, kind):
        import sec_filings as _sf
        return _sf.pins_the_price(kind)


class SecBase(CatalystBase):
    def setUp(self):
        super().setUp()
        self._sec = (od._SEC_AVAILABLE, od._sec_filings)

    def tearDown(self):
        od._SEC_AVAILABLE, od._sec_filings = self._sec
        super().tearDown()

    def sec(self, event=None, dates=None, fda=None, fda_dates_map=None):
        od._SEC_AVAILABLE = True
        od._sec_filings = FakeSec(event, dates, fda, fda_dates_map)


class TestOfferingTag(SecBase):
    def _filing(self, kind="OFFERING", **kw):
        base = {"kind": kind, "label": "Stock offering priced — 6.8M shares",
                "form": "424B5", "date": days_now()[0],
                "accepted": days_now()[0] + "T03:00:20-04:00",
                "url": "https://www.sec.gov/Archives/edgar/data/65770/x/form424b5.htm"}
        base.update(kw)
        return base

    def test_offering_is_tagged_with_its_filing_link(self):
        self.wire(board_actions=[])
        self.sec(event=self._filing())
        out = od._gap_offering("MU", days_now())
        self.assertEqual(out["kind"], "OFFERING")
        self.assertIn("6.8M shares", out["label"])
        self.assertIn("sec.gov", out["url"])
        self.assertEqual(out["filed"], days_now()[0] + "T03:00:20-04:00")

    def test_dilution_is_its_own_kind(self):
        self.sec(event=self._filing(kind="DILUTION",
                                    label="Shelf registration filed (S-3)"))
        self.assertEqual(od._gap_offering("MU", days_now())["kind"], "DILUTION")

    def test_no_sec_module_is_silent(self):
        od._SEC_AVAILABLE, od._sec_filings = False, None
        self.assertIsNone(od._gap_offering("MU", days_now()))
        self.assertEqual(od._gap_offering_dates("MU"), {})

    def test_a_failing_lookup_never_breaks_the_scan(self):
        class Boom:
            def latest_event(self, *a, **k):
                raise RuntimeError("EDGAR down")

            def event_dates(self, *a, **k):
                raise RuntimeError("EDGAR down")

        od._SEC_AVAILABLE, od._sec_filings = True, Boom()
        self.assertIsNone(od._gap_offering("MU", days_now()))
        self.assertEqual(od._gap_offering_dates("MU"), {})


class TestOfferingPriority(SecBase):
    def _no_earnings(self):
        real = od._gap_earn_hist
        od._gap_earn_hist = lambda s: set()
        self.addCleanup(lambda: setattr(od, "_gap_earn_hist", real))

    def test_an_offering_outranks_a_rating_change(self):
        # both are real and both are today; the share sale is why it gapped
        self._no_earnings()
        self.wire(board_actions=[{"ticker": "MU", "date": days_now()[0],
                                  "action_class": "downgrade", "firm": "Citi"}])
        self.sec(event={"kind": "OFFERING", "label": "Stock offering priced",
                        "form": "424B5", "date": days_now()[0], "url": "u"})
        self.assertEqual(od._gap_catalyst("MU")["kind"], "OFFERING")

    def test_earnings_still_outranks_an_offering(self):
        # earnings is the only catalyst that splits the statistics, so it has
        # to win even when the company also filed to sell stock
        self.wire(board_actions=[])
        self.sec(event={"kind": "OFFERING", "label": "Stock offering priced",
                        "form": "424B5", "date": days_now()[0], "url": "u"})
        real = od._gap_earn_hist
        od._gap_earn_hist = lambda s: set(days_now())
        try:
            self.assertEqual(od._gap_catalyst("MU")["kind"], "EARNINGS")
        finally:
            od._gap_earn_hist = real


class TestFdaTag(SecBase):
    QUOTE = ("On May 4, 2026, the Company issued a press release announcing "
             "that the U.S. Food and Drug Administration has approved the "
             "Company's supplemental Biologics License Application for "
             "ASCENIV, a plasma-derived immune globulin, in pediatric "
             "patients two years of age and older.")

    def _no_earnings(self):
        real = od._gap_earn_hist
        od._gap_earn_hist = lambda s: set()
        self.addCleanup(lambda: setattr(od, "_gap_earn_hist", real))

    def _fda(self, kind="FDA APPROVAL"):
        return {"kind": kind, "quote": self.QUOTE, "date": days_now()[0],
                "accepted": days_now()[0] + "T07:05:47-04:00",
                "url": "https://www.sec.gov/Archives/edgar/data/1/x/form8k.htm"}

    def test_decision_is_quoted_and_trimmed_for_display(self):
        self.sec(fda=self._fda())
        out = od._gap_filing_event("ADMA", days_now())
        self.assertEqual(out["kind"], "FDA APPROVAL")
        self.assertEqual(out["quote"], self.QUOTE)          # full text for the tooltip
        self.assertLessEqual(len(out["label"]), 121)        # one line for the cell
        self.assertTrue(out["label"].endswith("…"))
        self.assertNotIn(" …", out["label"])                # cut on a word, not mid-word

    def test_short_quotes_are_left_alone(self):
        self.assertEqual(od._gap_trim("FDA approved it."), "FDA approved it.")

    def test_the_filing_preamble_is_dropped_so_the_verb_survives(self):
        # without this the visible line ends at "...the U.S. Food and Drug
        # Administration has…" — everything except what actually happened
        out = od._gap_trim(self.QUOTE)
        self.assertTrue(out.startswith("The U.S. Food and Drug Administration"), out)
        self.assertIn("has approved", out)

    def test_a_quote_that_is_all_preamble_is_left_alone(self):
        q = "The Company announced that it will host a call."
        self.assertEqual(od._gap_trim(q), q)

    def test_rejection_is_its_own_kind(self):
        self.sec(fda=self._fda("FDA REJECTION"))
        self.assertEqual(od._gap_filing_event("ADMA", days_now())["kind"], "FDA REJECTION")

    def test_no_module_and_failures_are_silent(self):
        od._SEC_AVAILABLE, od._sec_filings = False, None
        self.assertIsNone(od._gap_filing_event("ADMA", days_now()))
        self.assertEqual(od._gap_filing_event_dates("ADMA", [days_now()[0]]), {})

        class Boom:
            def latest_event_tag(self, *a, **k):
                raise RuntimeError("EDGAR down")

            def event_tag_dates(self, *a, **k):
                raise RuntimeError("EDGAR down")

        od._SEC_AVAILABLE, od._sec_filings = True, Boom()
        self.assertIsNone(od._gap_filing_event("ADMA", days_now()))
        self.assertEqual(od._gap_filing_event_dates("ADMA", [days_now()[0]]), {})

    def test_fda_outranks_an_offering_but_names_it_too(self):
        # approval in the morning, stock sale on the back of it — both are
        # real, and the second one is why the pop may not hold
        self._no_earnings()
        self.wire(board_actions=[])
        self.sec(event={"kind": "OFFERING", "label": "Stock offering priced",
                        "form": "424B5", "date": days_now()[0], "url": "u"},
                 fda=self._fda())
        out = od._gap_catalyst("ADMA")
        self.assertEqual(out["kind"], "FDA APPROVAL")
        self.assertIn("also filed", out["label"])
        self.assertIn("stock offering priced", out["label"])

    def test_earnings_still_outranks_an_fda_decision(self):
        self.wire(board_actions=[])
        self.sec(fda=self._fda())
        real = od._gap_earn_hist
        od._gap_earn_hist = lambda s: set(days_now())
        try:
            self.assertEqual(od._gap_catalyst("ADMA")["kind"], "EARNINGS")
        finally:
            od._gap_earn_hist = real

    def test_fda_outranks_a_rating_change(self):
        self._no_earnings()
        self.wire(board_actions=[{"ticker": "ADMA", "date": days_now()[0],
                                  "action_class": "upgrade", "firm": "Citi"}])
        self.sec(fda=self._fda())
        self.assertEqual(od._gap_catalyst("ADMA")["kind"], "FDA APPROVAL")


class TestDealsAndTheRestOfTheTaxonomy(SecBase):
    def _no_earnings(self):
        real = od._gap_earn_hist
        od._gap_earn_hist = lambda s: set()
        self.addCleanup(lambda: setattr(od, "_gap_earn_hist", real))

    def _event(self, kind, quote=None, label=None):
        return {"kind": kind, "quote": quote, "label": label,
                "date": days_now()[0],
                "accepted": days_now()[0] + "T06:06:00-04:00", "url": "u"}

    def _offering(self):
        return {"kind": "OFFERING", "label": "Stock offering priced",
                "form": "424B5", "date": days_now()[0], "url": "u"}

    def test_a_buyout_warns_that_the_history_stopped_applying(self):
        # the thing that would otherwise mislead: a stock pinned to a deal
        # price still shows a full set of fade statistics
        self.sec(fda=self._event("BUYOUT", quote="$77.00 per share · the "
                                 "acquisition of the Company by Parent"))
        out = od._gap_filing_event("FBRX", days_now())
        self.assertEqual(out["kind"], "BUYOUT")
        self.assertIn("$77.00", out["label"])
        self.assertIn("trades to that price", out["warning"])

    def test_events_that_do_not_pin_the_price_carry_no_warning(self):
        self.sec(fda=self._event("FDA APPROVAL", quote="The FDA has approved it."))
        self.assertNotIn("warning", od._gap_filing_event("ADMA", days_now()))

    def test_metadata_only_tags_use_their_label(self):
        self.sec(fda=self._event("DELISTING NOTICE",
                                 label="Exchange notice (8-K item 3.01)"))
        out = od._gap_filing_event("ZZZZ", days_now())
        self.assertIn("8-K item 3.01", out["label"])

    def test_a_share_sale_beats_a_routine_officer_change(self):
        # both are real and both are today; the raise explains the gap
        self._no_earnings()
        self.wire(board_actions=[])
        self.sec(event=self._offering(),
                 fda=self._event("LEADERSHIP CHANGE", label="Officer change"))
        self.assertEqual(od._gap_catalyst("ZZZZ")["kind"], "OFFERING")

    def test_but_a_delisting_notice_beats_the_share_sale(self):
        self._no_earnings()
        self.wire(board_actions=[])
        self.sec(event=self._offering(),
                 fda=self._event("DELISTING NOTICE", label="Exchange notice"))
        out = od._gap_catalyst("ZZZZ")
        self.assertEqual(out["kind"], "DELISTING NOTICE")
        self.assertIn("also filed", out["label"])

    def test_a_weak_event_still_shows_when_nothing_else_does(self):
        self._no_earnings()
        self.wire(board_actions=[])
        self.sec(fda=self._event("RESTRUCTURING", label="Exit costs"))
        self.assertEqual(od._gap_catalyst("ZZZZ")["kind"], "RESTRUCTURING")

    def test_earnings_outranks_even_a_bankruptcy_filing(self):
        # not because it matters more, but because earnings is the one tag
        # that decides which statistical population the day belongs to
        self.wire(board_actions=[])
        self.sec(fda=self._event("BANKRUPTCY", label="Chapter 11"))
        real = od._gap_earn_hist
        od._gap_earn_hist = lambda s: set(days_now())
        try:
            self.assertEqual(od._gap_catalyst("ZZZZ")["kind"], "EARNINGS")
        finally:
            od._gap_earn_hist = real


class TestCatalystPriority(CatalystBase):
    def test_earnings_outranks_a_rating_change(self):
        # a stock that reported AND was upgraded is an EARNINGS gap: that is
        # the population its statistics are measured against
        self.wire(board_actions=[{"ticker": "MU", "date": days_now()[0],
                                  "action_class": "upgrade", "firm": "MS"}])
        real_earn = od._gap_earn_hist
        od._gap_earn_hist = lambda s: set(days_now())
        try:
            out = od._gap_catalyst("MU")
        finally:
            od._gap_earn_hist = real_earn
        self.assertEqual(out["kind"], "EARNINGS")

    def test_untagged_when_nothing_is_known(self):
        self.wire(board_actions=[])
        real_earn, real_macro = od._gap_earn_hist, od._edge_macro_events
        od._gap_earn_hist = lambda s: set()
        od._edge_macro_events = lambda: []
        try:
            out = od._gap_catalyst("ZZZZ")
        finally:
            od._gap_earn_hist, od._edge_macro_events = real_earn, real_macro
        self.assertEqual(out["kind"], "UNTAGGED")
        self.assertIsNone(out["label"])


if __name__ == "__main__":
    unittest.main()
