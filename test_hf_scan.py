"""Guards for hf_scan.py and the Phase 2 providers, offline.

A fake transport answers the exact URLs the providers build, using rows cut
from the real files: the CFTC's Socrata JSON, FINRA's short-interest CSV and
Reg SHO pipe-delimited file, and the OFR's gzipped series. That proves the
parsers, the weekly store, the week-over-week comparison and the headline
handed to the fund cards — with no network and no clock.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import hf_pulse as P
import hf_scan as SC
import hf_sources as S

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def cftc_rows(market: str, n: int = 30) -> list[dict]:
    """Weekly rows in Socrata's shape, newest first."""
    d0 = date(2026, 9, 1)
    out = []
    for i in range(n):
        out.append({
            "report_date_as_yyyy_mm_dd": (d0 - timedelta(weeks=i)).isoformat() + "T00:00:00.000",
            "open_interest_all": str(2_000_000 - i * 1000),
            "lev_money_positions_long": str(150_000 + i * 100),
            "lev_money_positions_short": str(480_000 - i * 200),
            "lev_money_positions_spread": "1000",
            "traders_lev_money_long_all": "61", "traders_lev_money_short_all": "74",
            "asset_mgr_positions_long": "900000", "asset_mgr_positions_short": "100000",
            "dealer_positions_long_all": "200000", "dealer_positions_short_all": "300000",
        })
    return out


SI_CSV = (
    '"accountingYearMonthNumber","symbolCode","issueName","issuerServicesGroupExchangeCode",'
    '"marketClassCode","currentShortPositionQuantity","previousShortPositionQuantity","stockSplitFlag",'
    '"averageDailyVolumeQuantity","daysToCoverQuantity","revisionFlag","changePercent",'
    '"changePreviousNumber","settlementDate"\n'
    '"20260814","AAPL","Apple Inc.","Q","NNM","116327753","141606163",,"46000000","2.53",,"-17.85","-25278410","2026-08-14"\n'
    '"20260814","OCGN","Ocugen, Inc.","Q","NNM","101800000","100900000",,"4300000","23.90",,"0.85","900000","2026-08-14"\n'
    '"20260814","AACAF","Some Foreign Ordinary","U","OTC","4213640","4200000",,"746","1000.00",,"0.32","13640","2026-08-14"\n'
    '"20260814","MSFT","Microsoft Corp","Q","NNM","40000000","44000000",,"20000000","2.00",,"-9.09","-4000000","2026-08-14"\n'
)

SHVOL = ("Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
         "20260904|AAPL|500000|100|1000000|B,Q,N\n"
         "20260904|MSFT|300000|50|1000000|B,Q,N\n")

OFR_JSON = {"timeseries": {
    "FPF-ALLQHF_GAVN10_LEVERAGERATIO_AVERAGE": {
        "metadata": {"description": {"name": "Top 10 largest funds: leverage"}},
        "timeseries": {"aggregation": [["2025-12-31", 23.909], ["2026-03-31", 23.722]]}},
    "FPF-BORROW_PRIMEBROKER_SUM": {
        "metadata": {"description": {"name": "prime brokerage borrowing"}},
        "timeseries": {"aggregation": [["2025-12-31", 3.259e12], ["2026-03-31", 3.221e12]]}},
}}


class FakeWeb:
    def __init__(self, si_published=("2026-08-14",)):
        self.calls, self.posts = [], []
        self.si_published = set(si_published)

    def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        if "publicreporting.cftc.gov" in url:
            # urlencode writes spaces as "+" and "&" as "%26"; decode before
            # looking for the market name in the $where clause.
            from urllib.parse import unquote_plus
            decoded = unquote_plus(url)
            for m in S.CFTC_MARKETS:
                if f"contract_market_name='{m['market']}'" in decoded:
                    return json.dumps(cftc_rows(m["market"])).encode()
            return b"[]"
        if "cdn.finra.org" in url:
            return SHVOL.encode()
        if "financialresearch.gov" in url:
            return gzip.compress(json.dumps(OFR_JSON).encode())   # the OFR always gzips
        raise RuntimeError(f"no fixture for {url}")

    def post(self, url: str, body: bytes) -> bytes:
        self.posts.append((url, json.loads(body)))
        payload = json.loads(body)
        sett = payload["compareFilters"][0]["fieldValue"]
        if sett not in self.si_published:
            return b""              # FINRA answers 204 for a report not yet out
        return SI_CSV.encode() if payload.get("offset", 0) == 0 else b""


class Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop("JERRY_NO_NET", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.web = FakeWeb()
        S._MEM.clear()  # noqa: SLF001
        S.configure(fetch_fn=self.web.fetch, post_fn=self.web.post,
                    data_dir=self.tmp.name, now_fn=lambda: NOW)
        SC._STATE.update({"board": None, "as_of": None, "refreshing": False,  # noqa: SLF001
                          "error": None, "week": None})
        SC.configure(data_dir=self.tmp.name, sector_fn=lambda s: {"AAPL": "Technology",
                                                                  "MSFT": "Technology"}.get(s),
                     now_fn=lambda: NOW)

    def tearDown(self):
        S.configure(fetch_fn=None, post_fn=None)
        S._MEM.clear()  # noqa: SLF001
        self.tmp.cleanup()


class Providers(Base):
    def test_cftc_rows_become_net_and_gross(self):
        s = S.cftc_series("E-MINI S&P 500")
        self.assertEqual(len(s), 30)
        self.assertEqual(s[0]["date"], "2026-09-01")
        self.assertEqual(s[0]["lev_net"], 150_000 - 480_000)
        self.assertEqual(s[0]["lev_gross"], 150_000 + 480_000)
        self.assertEqual(s[0]["traders_long"], 61)
        self.assertGreater(s[0]["date"], s[1]["date"], "newest first")

    def test_settlement_dates_are_the_15th_and_month_end_with_the_lag(self):
        d = S.finra_settlements(date(2026, 9, 6))
        self.assertIn("2026-08-14", d, "the 15th fell on a Saturday, so Friday the 14th")
        self.assertIn("2026-07-31", d)
        self.assertNotIn("2026-08-31", d, "published about eight business days later — not yet out")
        self.assertEqual(d, sorted(d, reverse=True))

    def test_short_interest_parses_and_carries_the_previous_period(self):
        rows = S.finra_short_interest("2026-08-14")
        self.assertEqual(len(rows), 4)
        a = rows["AAPL"]
        self.assertEqual(a["short"], 116327753.0)
        self.assertEqual(a["short_prev"], 141606163.0)
        self.assertEqual(a["change"], a["short"] - a["short_prev"])
        self.assertEqual(a["days_to_cover"], 2.53)

    def test_an_unpublished_settlement_is_empty_not_zero(self):
        self.assertEqual(S.finra_short_interest("2026-08-31"), {})

    def test_short_volume_gives_a_market_wide_share(self):
        out = S.finra_short_volume(date(2026, 9, 4))
        self.assertEqual(out["n"], 2)
        self.assertAlmostEqual(out["market_share"], 40.0)

    def test_the_ofr_series_is_gunzipped(self):
        out = S.ofr_leverage()
        self.assertEqual(out["lev_top10"]["value"], 23.722)
        self.assertEqual(out["lev_top10"]["prev"], 23.909)
        self.assertEqual(out["lev_top10"]["as_of"], "2026-03-31")

    def test_a_post_is_cached_by_its_body_not_its_url(self):
        S.finra_short_interest("2026-08-14")
        n = len(self.web.posts)
        S.finra_short_interest("2026-08-14")
        self.assertEqual(len(self.web.posts), n, "the second pull is served from cache")

    def test_offline_asks_for_nothing(self):
        os.environ["JERRY_NO_NET"] = "1"
        try:
            S._MEM.clear()  # noqa: SLF001
            before = len(self.web.calls) + len(self.web.posts)
            self.assertEqual(S.cftc_series("E-MINI S&P 500"), [])
            self.assertEqual(S.finra_short_interest("2026-08-14"), {})
            self.assertEqual(len(self.web.calls) + len(self.web.posts), before)
        finally:
            os.environ.pop("JERRY_NO_NET", None)


class Gathering(Base):
    def test_sector_rollup_reports_what_it_could_place(self):
        agg, by_sector = SC.gather_short_interest()
        self.assertEqual(agg["settlement"], "2026-08-14")
        self.assertEqual(agg["n_symbols"], 4)
        self.assertEqual(agg["sector_coverage"]["placed"], 2, "only AAPL and MSFT have a sector")
        self.assertEqual(agg["sector_coverage"]["of"], 4)
        self.assertEqual(by_sector, {}, "two symbols is below the floor, so no sector is reported")

    def test_a_sector_is_reported_once_it_clears_the_floor(self):
        SC.configure(data_dir=self.tmp.name, sector_fn=lambda s: "Technology", now_fn=lambda: NOW)
        import hf_scan
        orig = hf_scan._knob  # noqa: SLF001
        hf_scan._knob = lambda n: (1 if n == "sector_min_symbols" else orig(n))  # noqa: SLF001
        try:
            _, by_sector = SC.gather_short_interest()
        finally:
            hf_scan._knob = orig  # noqa: SLF001
        self.assertIn("Technology", by_sector)
        self.assertEqual(by_sector["Technology"]["n_symbols"], 4)

    def test_missing_sources_are_named_not_silent(self):
        raw = SC.gather()
        self.assertTrue(any("ETF" in u for u in raw["_failed"]))
        self.assertTrue(any("tide" in u.lower() for u in raw["_failed"]))
        self.assertTrue(raw["cftc"], "the CFTC still answered")


class SectorTide(Base):
    """The Unusual Whales client unwraps the envelope and returns the rows as
    a bare list; the raw endpoint returns {data, date}. Production hit the
    first shape and the gather raised "'list' object has no attribute 'get'",
    which the board reported as the source being unavailable."""

    class UW:
        def __init__(self, shape):
            self.shape = shape

        def sector_tide(self, sector, date=None):
            rows = [{"date": "2026-09-04", "net_call_premium": "3000000",
                     "net_put_premium": "1000000"}]
            return rows if self.shape == "list" else {"data": rows, "date": "2026-09-04"}

    def _tide(self, shape):
        SC.configure(data_dir=self.tmp.name, uw_getter=lambda: self.UW(shape), now_fn=lambda: NOW)
        return SC.gather_tide()

    def test_a_bare_list_is_read(self):
        out = self._tide("list")
        self.assertEqual(out["Technology"]["net_premium"], 2_000_000.0)
        self.assertEqual(out["Technology"]["as_of"], "2026-09-04")

    def test_an_enveloped_payload_is_read_too(self):
        out = self._tide("dict")
        self.assertEqual(out["Technology"]["net_premium"], 2_000_000.0)
        self.assertEqual(out["Technology"]["as_of"], "2026-09-04")

    def test_uw_sector_names_are_folded_onto_the_app_s(self):
        out = self._tide("list")
        self.assertIn("Consumer Discretionary", out, "UW calls it Consumer Cyclical")
        self.assertIn("Consumer Staples", out, "UW calls it Consumer Defensive")
        self.assertIn("Materials", out, "UW calls it Basic Materials")
        self.assertNotIn("Consumer Cyclical", out)


class TheWeeklyRecord(Base):
    def test_a_reading_is_stored_under_its_iso_week(self):
        b = SC.build()
        self.assertEqual(b["week"], "2026-W36")
        p = Path(self.tmp.name) / "hf" / "pulse" / "2026-W36.json"
        self.assertTrue(p.exists())
        self.assertEqual(json.loads(p.read_text())["week"], "2026-W36")

    def test_the_first_reading_has_nothing_to_compare_against(self):
        self.assertFalse(SC.build()["changed"]["available"])

    def test_a_rerun_in_the_same_week_compares_against_last_week_not_itself(self):
        SC.build()
        prior = SC.snapshot_for("2026-W36")
        prior["week"] = "2026-W35"
        prior["exposure"]["verdict"] = P.REDUCING
        (Path(self.tmp.name) / "hf" / "pulse" / "2026-W35.json").write_text(json.dumps(prior))
        b = SC.build()
        self.assertEqual(b["week"], "2026-W36")
        self.assertTrue(b["changed"]["available"])
        self.assertEqual(b["changed"]["since"], "2026-W35")

    def test_history_lists_every_week_newest_first(self):
        SC.build()
        for wk in ("2026-W34", "2026-W35"):
            snap = SC.snapshot_for("2026-W36")
            snap["week"] = wk
            (Path(self.tmp.name) / "hf" / "pulse" / f"{wk}.json").write_text(json.dumps(snap))
        h = SC.history()
        self.assertEqual([x["week"] for x in h], ["2026-W36", "2026-W35", "2026-W34"])
        self.assertIn("exposure", h[0]["verdicts"])

    def test_a_new_week_makes_the_stored_reading_stale(self):
        SC.build()
        self.assertFalse(SC._stale())  # noqa: SLF001
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW + timedelta(days=7))
        self.assertTrue(SC._stale(), "a new week always earns a fresh reading")  # noqa: SLF001

    def test_the_store_refuses_a_smuggled_attribution(self):
        board = {"week": "2026-W36", "questions": [{"inputs": [{"class": S.REGULATORY, "fund": "Citadel"}]}]}
        with self.assertRaises(ValueError):
            SC._save_snapshot(board)  # noqa: SLF001


class TheBoard(Base):
    def test_every_date_is_stamped_and_spelled_out(self):
        b = SC.build()
        self.assertEqual(b["cftc_as_of"], "2026-09-01")
        self.assertEqual(b["dates"]["cftc_as_of"], "September 1, 2026")
        self.assertEqual(b["dates"]["short_interest"], "August 14, 2026")
        self.assertEqual(b["dates"]["ofr"], "March 31, 2026")

    def test_the_questions_are_all_answered(self):
        b = SC.build()
        self.assertEqual(len(b["questions"]), 4)
        for q in b["questions"]:
            self.assertIn("verdict", q)
            self.assertIn("confidence", q)

    def test_no_input_anywhere_names_a_fund(self):
        b = SC.build()
        rows = SC._all_inputs(b)  # noqa: SLF001
        self.assertTrue(rows)
        self.assertTrue(S.attribution_ok(rows))

    def test_offline_says_it_has_not_read_yet_and_starts_nothing(self):
        os.environ["JERRY_NO_NET"] = "1"
        try:
            snap = SC.snapshot()
        finally:
            os.environ.pop("JERRY_NO_NET", None)
        self.assertTrue(snap["ok"])
        self.assertFalse(snap["available"])
        self.assertFalse(snap["refreshing"], "offline never starts a worker")
        self.assertIn("not been read yet", snap["note"])

    def test_status_reports_what_is_missing(self):
        SC.build()
        st = SC.status()
        self.assertTrue(st["available"])
        self.assertEqual(st["week"], "2026-W36")
        self.assertEqual(st["cftc_as_of"], "2026-09-01")
        self.assertTrue(st["unavailable"])


class TheHeadline(Base):
    """The one sentence handed to the fund cards. It is the only thing that
    crosses between the two layers, and it crosses in one direction."""

    def test_before_any_reading_it_says_so_and_names_no_fund(self):
        h = SC.headline()
        self.assertFalse(h["available"])
        self.assertEqual(h["class"], S.INFERENCE)
        self.assertNotIn("fund", {k for k in h if k == "fund"})

    def test_after_a_reading_it_carries_its_class_date_and_disclaimer(self):
        SC.build()
        h = SC.headline()
        self.assertEqual(h["class"], S.INFERENCE)
        self.assertEqual(h["as_of_text"], "September 1, 2026")
        self.assertEqual(h["week"], "2026-W36")
        self.assertIn("not about any one fund", h["note"])

    def test_a_streak_with_no_direction_is_not_appended(self):
        """It read 'Overall exposure: reducing — no clear direction this
        week' until the streak was only quoted when it had one."""
        SC.build()
        text = SC.headline().get("text") or ""
        self.assertNotIn("no clear direction", text)

    def test_it_never_carries_a_fund_key(self):
        SC.build()
        self.assertNotIn("fund", SC.headline())


class WeekKeys(unittest.TestCase):
    def test_iso_weeks(self):
        self.assertEqual(SC.week_key(date(2026, 9, 6)), "2026-W36")
        self.assertEqual(SC.week_key(date(2026, 9, 7)), "2026-W37")

    def test_long_dates_are_spelled_out(self):
        self.assertEqual(SC.long_date("2026-09-01"), "September 1, 2026")
        self.assertIsNone(SC.long_date(None))


if __name__ == "__main__":
    unittest.main()
