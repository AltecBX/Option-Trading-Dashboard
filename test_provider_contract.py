"""Do the providers still return what the engines actually read?

Every other test in this repository feeds an engine a fixture. That is the
right way to test arithmetic and it is exactly how a whole dimension of the
Investment tab came to be dead in production without a single failing test:
`invest_scan.revisions_block` read `analyst_count`, `up_count` and
`down_count` off the estimates payload, the tests handed it
`{"analyst_count": 10}`, and the real `analyst_client.get_eps_estimates`
had never carried any of the three. Revisions was NOT RATED for every
company on every day, and the value-trap signal that fires when analysts are
cutting could not fire at all.

So these tests do the opposite. They take the payload the PROVIDER builds —
through its own normalisation code, with only the network stubbed — and
assert that every field a downstream engine reads is present in it. A future
provider change that silently drops a field fails here rather than being
discovered a year later in a calibration panel that never filled in.

Two rules this file follows:

  * The stub replaces the NETWORK, never the normalisation. What is under
    test is the shape the provider produces, so the provider's own code has
    to run.
  * A field being None is fine and is not a failure. The engines are built
    for N/A. A field being ABSENT is the failure, because absent and None
    are indistinguishable to a `.get()` and one of them means nobody
    noticed.
"""
from __future__ import annotations

import unittest

import analyst_client as AC
import invest_scan as S


# ── the contracts, written down ─────────────────────────────────────────────
#
# What each engine reads off the estimates payload. Taken from the call
# sites, not from the provider — that is the point.

# invest_scan.revisions_block
REVISIONS_READS = ("analyst_count", "up_count", "down_count")
# invest_scan.snapshot
SNAPSHOT_READS = ("current_year_eps", "next_year_eps",
                  "change_30d_pct", "change_90d_pct", "available", "reason")
# invest_scan.revisions_block, for the basis lines on screen
BASIS_READS = ("basis", "change_basis")


class _FakeFrame:
    """The smallest thing that behaves like the provider's estimate frame."""

    def __init__(self, rows: dict):
        self._rows = rows

    @property
    def index(self):
        return list(self._rows)

    @property
    def loc(self):
        return self._rows


class _FakeTicker:
    """yfinance's Ticker, with the two frames the estimates reader uses.

    The column names are the ones the library documents:
      get_earnings_estimate  -> numberOfAnalysts avg low high yearAgoEps growth
      get_eps_revisions      -> upLast7days upLast30days downLast7days
                                downLast30days
    """

    def __init__(self, estimate_row=None, revision_row=None):
        self._est = estimate_row
        self._rev = revision_row

    def get_earnings_estimate(self):
        if self._est is None:
            return None
        return _FakeFrame({"0y": self._est, "+1y": {"avg": 8.40}})

    def get_eps_revisions(self):
        if self._rev is None:
            return None
        return _FakeFrame({"0y": self._rev})

    @property
    def info(self):
        return {}


class TestEstimatesPayloadShape(unittest.TestCase):
    """The estimates payload carries every key its readers reach for."""

    def setUp(self):
        self.client = AC.AnalystClient()
        self.client.finnhub_key = ""
        self._real_yf = AC._YF_OK
        AC._YF_OK = True

    def tearDown(self):
        AC._YF_OK = self._real_yf

    def _payload(self, ticker, symbol="TEST"):
        import yfinance as yf
        real = yf.Ticker
        yf.Ticker = lambda sym: ticker
        try:
            self.client._cache.clear()       # noqa: SLF001
            return self.client.get_eps_estimates(symbol, force_refresh=True)
        finally:
            yf.Ticker = real

    def _full_ticker(self):
        return _FakeTicker(
            estimate_row={"avg": 7.50, "numberOfAnalysts": 31.0},
            revision_row={"upLast7days": 1.0, "downLast7days": 0.0,
                          "upLast30days": 2.0, "downLast30days": 8.0})

    def test_every_field_revisions_reads_is_present_when_data_exists(self):
        got = self._payload(self._full_ticker())
        self.assertTrue(got["available"])
        for key in REVISIONS_READS:
            self.assertIn(key, got,
                          f"invest_scan.revisions_block reads {key!r} and the "
                          f"estimates provider does not return it. This is "
                          f"how the Revisions dimension went permanently "
                          f"NOT RATED in production.")

    def test_every_field_revisions_reads_is_present_when_there_is_no_data(self):
        # The unavailable path must carry the same keys. A payload whose
        # shape changes with its content is a payload nobody can rely on.
        got = self._payload(_FakeTicker())
        self.assertFalse(got["available"])
        for key in REVISIONS_READS + SNAPSHOT_READS:
            self.assertIn(key, got, f"missing {key!r} on the unavailable path")

    def test_snapshot_and_basis_fields_are_present(self):
        got = self._payload(self._full_ticker())
        for key in SNAPSHOT_READS + BASIS_READS:
            self.assertIn(key, got)

    def test_the_analyst_count_is_the_providers_own_coverage_figure(self):
        got = self._payload(self._full_ticker())
        self.assertEqual(got["analyst_count"], 31.0)
        self.assertIn("yfinance", got["analyst_count_source"])

    def test_the_move_counts_are_the_thirty_day_ones(self):
        got = self._payload(self._full_ticker())
        self.assertEqual(got["up_count"], 2.0)
        self.assertEqual(got["down_count"], 8.0)
        # Two raising against eight cutting is −60 breadth.
        self.assertAlmostEqual(got["change_30d_pct"], -60.0)

    def test_a_missing_coverage_count_is_none_and_never_the_movers(self):
        """The number who MOVED is not the number who COVER.

        Standing one in for the other would refuse every well-covered company
        in a quiet month and rate a thinly covered one that happened to be
        busy. Absent means absent.
        """
        got = self._payload(_FakeTicker(
            estimate_row={"avg": 7.50},          # no numberOfAnalysts
            revision_row={"upLast30days": 9.0, "downLast30days": 1.0,
                          "upLast7days": 1.0, "downLast7days": 0.0}))
        self.assertIsNone(got["analyst_count"])
        self.assertEqual(got["analyst_count_source"], "")
        self.assertEqual(got["up_count"], 9.0)   # still reported for display


class TestRevisionsCanActuallyRate(unittest.TestCase):
    """The engine rates from the provider's real payload, end to end."""

    def setUp(self):
        self.client = AC.AnalystClient()
        self.client.finnhub_key = ""
        self._real_yf = AC._YF_OK
        AC._YF_OK = True

    def tearDown(self):
        AC._YF_OK = self._real_yf

    def _payload(self, ticker):
        import yfinance as yf
        real = yf.Ticker
        yf.Ticker = lambda sym: ticker
        try:
            self.client._cache.clear()           # noqa: SLF001
            return self.client.get_eps_estimates("TEST", force_refresh=True)
        finally:
            yf.Ticker = real

    def _snap(self, est):
        return {"estimate_change_30d_pct": est.get("change_30d_pct"),
                "estimate_change_90d_pct": est.get("change_90d_pct"),
                "eps_forward": est.get("current_year_eps"),
                "eps_next_year": est.get("next_year_eps"),
                "forward_eps_growth_pct": 12.0}

    def test_revisions_is_rated_from_the_real_payload(self):
        est = self._payload(_FakeTicker(
            estimate_row={"avg": 7.50, "numberOfAnalysts": 31.0},
            revision_row={"upLast7days": 1.0, "downLast7days": 0.0,
                          "upLast30days": 2.0, "downLast30days": 8.0}))
        block = S.revisions_block(self._snap(est), est, {})
        self.assertIsNotNone(block["score"],
                             "Revisions is unrated on a payload that carries "
                             "31 covering analysts")
        self.assertNotEqual(block["label"], "NOT RATED")
        self.assertEqual(block["analyst_count"], 31.0)

    def test_the_estimates_falling_signal_can_fire_from_the_real_payload(self):
        """The gate that stops a deteriorating company reading ATTRACTIVE.

        It needs the coverage count to open at all, which is what made this
        worth a test of its own: the signal was unreachable for every company
        for as long as the count was missing.
        """
        est = self._payload(_FakeTicker(
            estimate_row={"avg": 7.50, "numberOfAnalysts": 31.0},
            revision_row={"upLast7days": 0.0, "downLast7days": 3.0,
                          "upLast30days": 2.0, "downLast30days": 8.0}))
        block = S.revisions_block(self._snap(est), est, {})
        cfg_min = 4
        self.assertGreaterEqual(block.get("analyst_count") or 0, cfg_min,
                                "the trap gate cannot open")
        # −60 breadth is past the −20 cut, so the signal is ACTIVE.
        self.assertLess(block["change_30d"], -20.0)

    def test_thin_coverage_still_refuses_to_rate(self):
        est = self._payload(_FakeTicker(
            estimate_row={"avg": 7.50, "numberOfAnalysts": 2.0},
            revision_row={"upLast30days": 1.0, "downLast30days": 1.0,
                          "upLast7days": 0.0, "downLast7days": 0.0}))
        block = S.revisions_block(self._snap(est), est, {})
        self.assertEqual(block["label"], "NOT RATED")
        self.assertIn("2 analysts", block["reason"])


if __name__ == "__main__":                        # pragma: no cover
    unittest.main()
