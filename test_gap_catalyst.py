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
