"""Guards for hf_scan.py and the Phase 2 providers, offline.

A fake transport answers the exact URLs the providers build, using rows cut
from the real files: the CFTC's Socrata JSON, FINRA's short-interest CSV and
Reg SHO pipe-delimited file, and the OFR's gzipped series. That proves the
parsers, the weekly store, the week-over-week comparison and the headline
handed to the fund cards — with no network and no clock.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import tempfile
import unittest
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import hf_grade as GR
import hf_names as NM
import hf_pulse as P
import hf_replay as RP
import hf_report as RPT
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


# One Reuters headline in the shape Google News actually returns: the outlet
# in <source> and appended to the title after a dash.
GNEWS_RSS = (
    '<?xml version="1.0"?><rss version="2.0"><channel>'
    "<item><title>Goldman Says Hedge Funds Sell US Tech Stocks at Record Pace - Reuters</title>"
    "<link>https://example.invalid/a</link>"
    "<pubDate>Fri, 04 Sep 2026 07:00:00 GMT</pubDate>"
    "<source>Reuters</source></item>"
    "<item><title>Hedge funds had their worst month in twenty years - CNBC</title>"
    "<link>https://example.invalid/b</link>"
    "<pubDate>Thu, 03 Sep 2026 07:00:00 GMT</pubDate>"
    "<source>CNBC</source></item>"
    "</channel></rss>")


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
        if "news.google.com" in url:
            return GNEWS_RSS.encode()
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
                          "error": None, "week": None, "report": None, "report_week": None,
                          "report_at": None, "report_refreshing": False, "report_error": None,
                          # The grade keys reset too. Without this a stored
                          # card from an earlier test in the same class
                          # survives into the next one and the failure looks
                          # like the product's.
                          "grades": None, "grades_at": None, "grades_refreshing": False,
                          "grades_error": None, "grades_retry_at": None,
                          "replay": None, "replay_at": None, "replay_refreshing": False,
                          "replay_error": None, "replay_retry_at": None})
        self.funds = {"managers": [{"key": "acme", "name": "Acme Capital", "status": "FILING",
                                    "turnover": "READABLE",
                                    "activity": {"state": "UNKNOWN", "since": "2026-06-30",
                                                 "items": []}}],
                      "new_filings": []}
        SC.configure(data_dir=self.tmp.name, sector_fn=lambda s: {"AAPL": "Technology",
                                                                  "MSFT": "Technology"}.get(s),
                     now_fn=lambda: NOW, funds_fn=lambda: self.funds)

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


class ThePressChannel(Base):
    """The prime-broker headlines reach the board, and only as support."""

    def test_the_feed_is_read_and_filtered(self):
        got = SC.gather_press()
        self.assertEqual(got["n_quotes"], 1, "one of the two headlines is positioning")
        self.assertEqual(got["quotes"][0]["bank"], "Goldman Sachs")
        self.assertEqual(got["quotes"][0]["about"], "exposure")
        self.assertEqual(got["n_captured"], 2, "both were read; one was not counted")

    def test_a_quote_enters_the_board_as_supporting_evidence_only(self):
        b = SC.build()
        rows = [i for i in b["exposure"]["inputs"] if i["class"] == S.PRIME_BROKER]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["weight"], 0.5, "a bank quote can corroborate, never decide")

    def test_the_board_reports_what_the_press_gave_it(self):
        b = SC.build()
        self.assertEqual(b["sources"]["press_quotes"], 1)
        self.assertEqual(b["sources"]["press_banks"], ["Goldman Sachs"])
        self.assertNotIn("Prime broker press unavailable", b["unavailable"])

    def test_a_dead_feed_is_named_rather_than_silently_missing(self):
        real = S.prime_broker_news
        S.prime_broker_news = lambda *a, **k: []
        try:
            b = SC.build()
        finally:
            S.prime_broker_news = real
        self.assertIn("Prime broker press unavailable", b["unavailable"])
        self.assertEqual(b["sources"]["press_quotes"], 0)


class TheReportRecord(Base):
    """A report is a record of what was known when it was written, so a
    rebuild adds a revision rather than erasing the earlier one."""

    def test_a_report_is_stored_under_its_iso_week(self):
        rep = SC.build_report()
        self.assertEqual(rep["week"], "2026-W36")
        p = Path(self.tmp.name) / "hf" / "reports" / "2026-W36.json"
        self.assertTrue(p.exists())
        self.assertEqual(json.loads(p.read_text())["week"], "2026-W36")

    def test_a_rebuild_appends_a_revision_and_never_overwrites(self):
        first = SC.build_report()
        SC.build_report()
        doc = json.loads((Path(self.tmp.name) / "hf" / "reports" / "2026-W36.json").read_text())
        self.assertEqual([r["revision"] for r in doc["revisions"]], [1, 2])
        self.assertEqual(SC.report_for("2026-W36", 1)["built_at"], first["built_at"])

    def test_the_newest_revision_is_the_current_report(self):
        SC.build_report()
        rep = SC.build_report()
        self.assertEqual(SC.report_for("2026-W36")["built_at"], rep["built_at"])

    def test_only_the_configured_number_of_revisions_is_kept(self):
        # thresholds.json overrides DEFAULTS, so the knob is what has to be
        # replaced here — setting DEFAULTS alone made this assert nothing.
        with self._keep(2):
            for _ in range(4):
                SC.build_report()
        doc = json.loads((Path(self.tmp.name) / "hf" / "reports" / "2026-W36.json").read_text())
        self.assertEqual(len(doc["revisions"]), 2)
        self.assertEqual(doc["n_revisions"], 4, "the count of builds is not lost")

    def test_a_week_rollover_rereads_the_pulse_instead_of_refiling_last_week(self):
        # The board must belong to THIS week before a report is built from
        # it. Taking last week's stored reading filed the report under last
        # week, which _report_stale then judged stale forever — so every
        # look at the card appended another revision to a week already over.
        SC.build()
        self.assertEqual(SC._STATE["board"]["week"], "2026-W36")  # noqa: SLF001
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW + timedelta(days=7),
                     funds_fn=lambda: self.funds)
        S.configure(fetch_fn=self.web.fetch, post_fn=self.web.post,
                    data_dir=self.tmp.name, now_fn=lambda: NOW + timedelta(days=7))
        rep = SC.build_report()
        self.assertEqual(rep["week"], "2026-W37", "the report belongs to the current week")
        self.assertFalse(SC._report_stale(), "and is not immediately stale again")  # noqa: SLF001

    @contextlib.contextmanager
    def _keep(self, n: int):
        """Force the retention limit, whatever thresholds.json says."""
        real = SC._report_knob  # noqa: SLF001
        SC._report_knob = lambda name: (n if name == "keep_revisions" else real(name))  # noqa: SLF001
        try:
            yield
        finally:
            SC._report_knob = real  # noqa: SLF001

    def test_revision_numbers_stay_unique_after_trimming(self):
        # With keep_revisions=2, build 3 stores revisions [2, 3]. Counting
        # what is on disk then numbered build 4 as revision 3 again, and
        # asking for revision 3 returned the older of the two.
        with self._keep(2):
            for _ in range(4):
                SC.build_report()
        doc = json.loads((Path(self.tmp.name) / "hf" / "reports" / "2026-W36.json").read_text())
        nums = [r["revision"] for r in doc["revisions"]]
        self.assertEqual(nums, sorted(set(nums)), "no duplicate revision numbers")
        self.assertEqual(nums, [3, 4])
        self.assertEqual(doc["n_revisions"], 4, "the count of builds keeps rising")
        self.assertEqual(SC.report_for("2026-W36", 4)["built_at"],
                         SC.report_for("2026-W36")["built_at"])


    def test_a_rebuild_in_the_same_week_diffs_against_last_week_not_itself(self):
        SC.build_report()
        prior = SC.report_for("2026-W36")
        prior["week"] = "2026-W35"
        prior["conclusions"][0]["verdict"] = "ADDING"
        (Path(self.tmp.name) / "hf" / "reports" / "2026-W35.json").write_text(
            json.dumps({"week": "2026-W35", "n_revisions": 1,
                        "revisions": [{"revision": 1, "built_at": "x", "report": prior}]}))
        rep = SC.build_report()
        self.assertTrue(rep["changed"]["comparable"])
        self.assertEqual(rep["changed"]["since"], "2026-W35")

    def test_an_older_stored_report_is_normalised_on_read_not_on_disk(self):
        SC.build_report()
        p = Path(self.tmp.name) / "hf" / "reports" / "2026-W36.json"
        doc = json.loads(p.read_text())
        # Rewrite the stored revision as a pre-1.1.1 document.
        doc["revisions"][0]["report"]["version"] = "1.0.0"
        doc["revisions"][0]["report"]["funds"].pop("sentence", None)
        doc["revisions"][0]["report"]["funds"].pop("n_filed_since", None)
        doc["revisions"][0]["report"]["funds"]["n_acted"] = 3
        p.write_text(json.dumps(doc))
        got = SC.report_for("2026-W36")
        self.assertTrue(got["funds"]["sentence"], "the card has something to render")
        self.assertTrue(got["funds"]["sentence_filled_in"])
        self.assertIn("newer than their last holdings report", got["funds"]["sentence"])
        again = json.loads(p.read_text())
        self.assertNotIn("sentence", again["revisions"][0]["report"]["funds"],
                         "the stored bytes are untouched")


    def test_history_lists_every_week_newest_first_with_its_revision_count(self):
        SC.build_report()
        SC.build_report()
        h = SC.report_history()
        self.assertEqual([x["week"] for x in h], ["2026-W36"])
        self.assertEqual(h[0]["n_revisions"], 2)
        self.assertIn("exposure", h[0]["verdicts"])

    def test_the_report_carries_the_named_layer_it_was_given(self):
        rep = SC.build_report()
        self.assertEqual(rep["funds"]["n_managers"], 1)
        self.assertEqual(rep["watchlist"]["n"], 1)

    def test_the_store_refuses_a_smuggled_attribution(self):
        rep = {"week": "2026-W36",
               "conclusions": [{"inputs": [{"class": S.REGULATORY, "fund": "Citadel"}]}]}
        with self.assertRaises(ValueError):
            SC.save_report(rep)

    def test_a_new_week_makes_the_stored_report_stale(self):
        SC.build_report()
        self.assertFalse(SC._report_stale())  # noqa: SLF001
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW + timedelta(days=7),
                     funds_fn=lambda: self.funds)
        self.assertTrue(SC._report_stale())  # noqa: SLF001

    def test_a_stored_report_is_reloaded_on_start(self):
        SC.build_report()
        SC._STATE.update({"report": None, "report_week": None, "report_at": None})  # noqa: SLF001
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, funds_fn=lambda: self.funds)
        self.assertEqual(SC._STATE["report_week"], "2026-W36")  # noqa: SLF001

    def test_comparing_two_stored_weeks(self):
        SC.build_report()
        rep = SC.report_for("2026-W36")
        rep = json.loads(json.dumps(rep))
        rep["week"] = "2026-W35"
        rep["conclusions"][0]["verdict"] = "ADDING"
        (Path(self.tmp.name) / "hf" / "reports" / "2026-W35.json").write_text(
            json.dumps({"week": "2026-W35", "n_revisions": 1,
                        "revisions": [{"revision": 1, "built_at": "x", "report": rep}]}))
        cmp = SC.report_compare("2026-W37", "2026-W36")
        self.assertFalse(cmp["ok"], "a week with no stored report cannot be compared")
        cmp = SC.report_compare("2026-W35", "2026-W36")
        self.assertTrue(cmp["ok"])
        self.assertEqual(cmp["older"]["week"], "2026-W35")
        self.assertEqual(cmp["n_different"], 1)

    def test_the_week_start_is_the_monday_of_the_iso_week(self):
        self.assertEqual(SC.week_start(date(2026, 9, 6)), "2026-08-31")   # a Sunday
        self.assertEqual(SC.week_start(date(2026, 9, 7)), "2026-09-07")   # a Monday

    def test_asking_for_a_week_that_was_never_stored(self):
        out = SC.report(week="2099-W01")
        self.assertFalse(out["ok"])
        self.assertIn("2099-W01", out["error"])

    def test_status_reports_what_is_stored(self):
        SC.build_report()
        st = SC.report_status()
        self.assertTrue(st["available"])
        self.assertEqual(st["week"], "2026-W36")
        self.assertEqual(st["weeks_stored"], 1)
        self.assertEqual(st["version"], RPT.HF_REPORT_VERSION)
        self.assertEqual(st["code_version"], RPT.HF_REPORT_VERSION)

    def test_the_status_version_describes_the_stored_report_not_the_code(self):
        # A report written under an older version keeps saying so until it
        # is rebuilt. Reporting the running module's number instead would
        # claim a shape the stored document does not have.
        SC.build_report()
        with SC._LOCK:  # noqa: SLF001
            SC._STATE["report"]["version"] = "0.9.0"  # noqa: SLF001
        st = SC.report_status()
        self.assertEqual(st["version"], "0.9.0", "it describes what is stored")
        self.assertEqual(st["code_version"], RPT.HF_REPORT_VERSION)


class FakeUW:
    """Only the two methods the grader touches."""
    def __init__(self, envelope=True, rows=None):
        self.envelope, self.calls = envelope, []
        self.rows = rows if rows is not None else [
            {"date": "2026-08-31", "close": "110.5"},
            {"date": "2026-08-24", "close": "108.0"},
            {"date": "not a date", "close": "1"},
            {"date": "2026-08-17", "close": None},
        ]

    def ohlc(self, ticker, candle_size="1w", timeframe="3Y"):
        self.calls.append((ticker, candle_size, timeframe))
        return {"data": self.rows, "date": "2026-08-31"} if self.envelope else self.rows


class TheCrowdingBackfill(Base):
    """Crowding is CFTC-only, so a truncated series reproduces exactly what
    the board would have said in that week — which is what makes three years
    gradeable when only one week has ever been stored."""

    def test_each_reconstructed_week_carries_its_own_date(self):
        # The first cut carried the market's original as_of into every
        # window, so 204 reconstructed rows all landed in the same week.
        cftc = S.cftc_all()
        rows = SC.crowded_history(cftc, min_history=12)
        weeks = {r["week"] for r in rows}
        self.assertGreater(len(weeks), 1, "every week collapsed onto one key")
        self.assertEqual(len(weeks), len({r["as_of"] for r in rows}))

    def test_a_window_never_sees_a_week_that_came_later(self):
        cftc = S.cftc_all()
        rows = SC.crowded_history(cftc, min_history=12)
        newest = max(r["as_of"] for r in rows)
        self.assertEqual(newest, cftc["sp500"]["as_of"],
                         "the newest reconstructed week is the newest real one")

    def test_each_market_goes_as_deep_as_its_own_history(self):
        # Slicing every market to the shortest one's depth threw away two
        # years of the S&P for the sake of a contract with 12 weeks.
        cftc = S.cftc_all()
        short_key = sorted(cftc)[0]
        cftc[short_key] = {**cftc[short_key], "series": cftc[short_key]["series"][:14]}
        rows = SC.crowded_history(cftc, min_history=12)
        per = {}
        for r in rows:
            per[r["key"]] = per.get(r["key"], 0) + 1
        self.assertLessEqual(per.get(short_key, 0), 2)
        self.assertGreater(max(per.values()), 10, "the long series is not capped by the short one")

    def test_every_row_carries_a_state_and_a_side(self):
        rows = SC.crowded_history(S.cftc_all(), min_history=12)
        self.assertTrue(rows)
        for r in rows[:20]:
            self.assertIn(r["state"], ("CROWDED", "DE-CROWDING", "NORMAL"))
            self.assertIn(r["side"], ("long", "short"))

    def test_no_cftc_data_is_not_a_crash(self):
        self.assertEqual(SC.crowded_history({}), [])


class TheCloseProvider(Base):
    def test_the_envelope_shape_is_read(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     uw_getter=lambda: FakeUW(envelope=True))
        got = SC.gather_weekly_closes(["SPY"])
        self.assertEqual(got["SPY"]["2026-W36"], 110.5)
        self.assertEqual(got["SPY"]["2026-W35"], 108.0)

    def test_the_bare_list_shape_is_read_too(self):
        # The same difference that broke the sector tide in v4.86.
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     uw_getter=lambda: FakeUW(envelope=False))
        got = SC.gather_weekly_closes(["SPY"])
        self.assertEqual(got["SPY"]["2026-W36"], 110.5)

    def test_unparseable_rows_are_skipped_not_guessed(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     uw_getter=lambda: FakeUW(envelope=True))
        got = SC.gather_weekly_closes(["SPY"])
        self.assertEqual(len(got["SPY"]), 2, "the bad date and the null close are dropped")

    def test_weekly_candles_are_requested_not_daily(self):
        uw = FakeUW()
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, uw_getter=lambda: uw)
        SC.gather_weekly_closes(["SPY", "QQQ"])
        self.assertEqual([c[1] for c in uw.calls], ["1w", "1w"])

    def test_no_client_is_an_empty_answer_not_a_crash(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, uw_getter=lambda: None)
        self.assertEqual(SC.gather_weekly_closes(["SPY"]), {})

    def test_every_graded_proxy_is_asked_for(self):
        syms = SC.grade_symbols()
        for want in ("SPY", "QQQ", "IWM", "DIA", "XLF", "XLK"):
            self.assertIn(want, syms)
        self.assertEqual(len(syms), len(set(syms)))


class AFailedGradeIsNotCached(Base):
    """An outage must not be stored as a finished answer."""

    def test_no_closes_means_nothing_is_stored(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: None)
        SC.build_grades()
        self.assertFalse((Path(self.tmp.name) / "hf" / "grades.json").exists())
        self.assertIsNone(SC._STATE.get("grades_at"))  # noqa: SLF001
        self.assertIn("could not be graded", SC._STATE.get("grades_error") or "")  # noqa: SLF001

    def test_the_payload_reports_unavailable_rather_than_an_empty_table(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: None)
        SC.build_grades()
        out = SC.grades()
        self.assertFalse(out["available"])
        # It waits rather than retrying immediately — the cooldown is what
        # stops an outage becoming a loop of full reconstructions.
        self.assertFalse(SC._grades_stale())  # noqa: SLF001
        self.assertTrue(SC.grade_status()["retry_after"])

    def test_a_failed_build_sets_a_cooldown_instead_of_looping(self):
        # Leaving it permanently stale meant every look at the panel started
        # another full reconstruction, and the panel polls every twenty
        # seconds while one runs.
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: None)
        SC.build_grades()
        self.assertFalse(SC._grades_stale(), "it waits instead of retrying at once")  # noqa: SLF001
        self.assertTrue(SC._STATE["grades_retry_at"])  # noqa: SLF001
        self.assertIn("try again", SC._STATE["grades_error"])  # noqa: SLF001

    def test_the_cooldown_lifts(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: None)
        SC.build_grades()
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW + timedelta(hours=3),
                     funds_fn=lambda: self.funds, uw_getter=lambda: None)
        self.assertTrue(SC._grades_stale(), "after the cooldown it tries again")  # noqa: SLF001

    def test_a_successful_build_clears_the_cooldown(self):
        # An explicit rebuild does not consult staleness, so it runs while
        # the cooldown is still standing — a person asking is not a loop —
        # and a build that priced something has to take the stamp away with
        # it, or the next automatic refresh would still be held back.
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: None)
        SC.build_grades()
        self.assertTrue(SC._STATE["grades_retry_at"])  # noqa: SLF001
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: FakeUW())
        SC.build_grades()
        self.assertIsNone(SC._STATE["grades_retry_at"])  # noqa: SLF001
        self.assertIsNone(SC.grade_status()["retry_after"])
        self.assertTrue(SC.grades()["available"])

    def test_a_successful_grade_is_stored_and_fresh(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: FakeUW())
        SC.build_grades()
        self.assertTrue((Path(self.tmp.name) / "hf" / "grades.json").exists())
        self.assertFalse(SC._grades_stale())  # noqa: SLF001
        self.assertTrue(SC.grades()["available"])

    def test_the_status_version_describes_the_stored_card_not_the_code(self):
        # A grade card is rebuilt weekly, so after a deploy the stored one
        # can be several versions behind. A status saying the new number
        # while serving the old card is how a counting bug survived a live
        # check: the field said 1.0.1, the card was 1.0.0, and the count in
        # it still meant the old thing.
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: FakeUW())
        SC.build_grades()
        with SC._LOCK:  # noqa: SLF001
            SC._STATE["grades"]["version"] = "0.9.0"  # noqa: SLF001
        st = SC.grade_status()
        self.assertEqual(st["version"], "0.9.0", "it describes what is stored")
        self.assertEqual(st["code_version"], GR.HF_GRADE_VERSION)

    def test_the_status_carries_both_crowded_counts(self):
        # "33 graded" says nothing without "out of 51 crowded", so the route
        # has to hand both through. The counts are injected rather than
        # built: this fixture grades no crowded weeks, so a version of this
        # test that built a card would have compared 0 against 0 and passed
        # while carrying nothing.
        with SC._LOCK:  # noqa: SLF001
            SC._STATE["grades"] = {"version": GR.HF_GRADE_VERSION, "n_market_weeks": 1303,  # noqa: SLF001
                                   "n_crowded_weeks": 51, "n_crowded_weeks_graded": 33}
        st = SC.grade_status()
        self.assertEqual(st["n_market_weeks"], 1303)
        self.assertEqual(st["n_crowded_weeks"], 51)
        self.assertEqual(st["n_crowded_weeks_graded"], 33)


class WeekKeys(unittest.TestCase):
    def test_iso_weeks(self):
        self.assertEqual(SC.week_key(date(2026, 9, 6)), "2026-W36")
        self.assertEqual(SC.week_key(date(2026, 9, 7)), "2026-W37")

    def test_long_dates_are_spelled_out(self):
        self.assertEqual(SC.long_date("2026-09-01"), "September 1, 2026")
        self.assertIsNone(SC.long_date(None))


if __name__ == "__main__":
    unittest.main()


class TheWeekAReadingDescribes(Base):
    """The board stamps a snapshot with the week it RAN in; the CFTC report
    it read is dated the Tuesday before. Crowding has always been filed
    under the data week, so filing the verdicts under the build week priced
    the two from weeks a week apart inside a single card."""

    def test_a_reading_is_keyed_by_its_data_not_its_build_week(self):
        # Exactly the live shape: built in 2026-W37, reading data dated
        # 2026-09-01, which is 2026-W36.
        row = {"week": "2026-W37", "cftc_as_of": "2026-09-01", "verdicts": {"longs": "ADDING"}}
        self.assertEqual(SC.data_week(row), "2026-W36")
        self.assertNotEqual(SC.data_week(row), row["week"])

    def test_a_reading_with_no_data_date_keeps_its_stamp(self):
        # Older stored weeks predate cftc_as_of. Dropping them would lose
        # real record; guessing a week for them would invent one.
        self.assertEqual(SC.data_week({"week": "2026-W20", "cftc_as_of": None}), "2026-W20")
        self.assertEqual(SC.data_week({"week": "2026-W20"}), "2026-W20")

    def test_a_broken_data_date_falls_back_rather_than_crashing(self):
        self.assertEqual(SC.data_week({"week": "2026-W20", "cftc_as_of": "not-a-date"}),
                         "2026-W20")

    def test_the_stored_readings_the_grader_sees_use_the_data_week(self):
        SC.build()
        rows = SC.stored_readings()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(set(r), {"week", "verdicts"})
        weeks = {r["week"] for r in rows}
        # The board built at NOW reads CFTC dated 2026-09-01 (2026-W36).
        self.assertIn("2026-W36", weeks)


class TheReplayWiring(Base):
    def _cftc(self):
        return S.cftc_all()

    def test_a_replay_is_built_stored_and_read_back(self):
        card = SC.build_replay()
        self.assertGreater(card["n_weeks"], 0)
        self.assertTrue((Path(self.tmp.name) / "hf" / "replay.json").exists())
        again = SC.load_replay()
        self.assertEqual(again["n_weeks"], card["n_weeks"])

    def test_the_payload_leaves_the_week_by_week_rows_out(self):
        # Hundreds of rows nothing renders would be the largest thing on the
        # wire on this board.
        SC.build_replay()
        out = SC.replay()
        self.assertTrue(out["available"])
        self.assertNotIn("weeks", out)
        self.assertIn("coverage", out)
        self.assertIn("sources", out)

    def test_the_status_describes_the_stored_card_not_the_code(self):
        SC.build_replay()
        with SC._LOCK:  # noqa: SLF001
            SC._STATE["replay"]["version"] = "0.9.0"  # noqa: SLF001
        st = SC.replay_status()
        self.assertEqual(st["version"], "0.9.0")
        self.assertEqual(st["code_version"], RP.HF_REPLAY_VERSION)

    def test_the_replayed_weeks_reach_the_grader(self):
        SC.build_replay()
        replayed = SC.replayed_readings()
        self.assertGreater(len(replayed), 1, "more than the single stored week")
        for r in replayed:
            self.assertEqual(set(r), {"week", "verdicts"})

    def test_a_stored_week_beats_a_replayed_one_for_the_same_week(self):
        # The board's own record is what it published; the replay is only
        # what it would have published. They must never both be graded.
        SC.build()
        SC.build_replay()
        stored = SC.stored_readings()
        replayed = SC.replayed_readings()
        overlap = {r["week"] for r in stored} & {r["week"] for r in replayed}
        self.assertEqual(overlap, set(), "a week is graded once")

    def test_the_grade_card_says_where_its_readings_came_from(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: FakeUW())
        SC.build()
        SC.build_replay()
        card = SC.build_grades()
        src = card["readings_from"]
        self.assertGreaterEqual(src["recorded"], 1)
        self.assertGreater(src["replayed"], 1)
        self.assertEqual(card["n_readings"], src["recorded"] + src["replayed"])

    def test_a_replay_that_reconstructed_nothing_waits_instead_of_looping(self):
        SC.build_replay()          # a real one first, to prove the contrast
        with SC._LOCK:             # noqa: SLF001
            SC._STATE.update({"replay": None, "replay_at": None,  # noqa: SLF001
                              "replay_retry_at": None})
        # No CFTC at all: nothing can be reconstructed.
        with mock.patch.object(S, "cftc_all", lambda: {}):
            card = SC.build_replay()
        self.assertEqual(card["n_readings"], 0)
        self.assertFalse(SC._replay_stale(), "it waits rather than rebuilding on every look")  # noqa: SLF001
        self.assertTrue(SC.replay_status()["retry_after"])
        self.assertIn("try again", SC._STATE["replay_error"])  # noqa: SLF001

    def test_the_cooldown_lifts(self):
        with mock.patch.object(S, "cftc_all", lambda: {}):
            SC.build_replay()
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW + timedelta(hours=3),
                     funds_fn=lambda: self.funds)
        self.assertTrue(SC._replay_stale())  # noqa: SLF001


class TheShortVolumeCache(Base):
    def test_it_keeps_one_number_per_session_not_the_files(self):
        shares = SC.gather_shvol_history(budget=3)
        self.assertTrue(shares)
        p = Path(self.tmp.name) / "hf" / "shvol_daily.json"
        self.assertTrue(p.exists())
        doc = json.loads(p.read_text())
        self.assertEqual(set(doc), {"shares", "n", "saved_at"})
        for v in doc["shares"].values():
            self.assertIsInstance(v, float)

    def test_a_second_run_resumes_rather_than_refetching(self):
        first = SC.gather_shvol_history(budget=2)
        calls_before = len([u for u in self.web.calls if "CNMSshvol" in u])
        second = SC.gather_shvol_history(budget=2)
        calls_after = len([u for u in self.web.calls if "CNMSshvol" in u])
        self.assertGreaterEqual(len(second), len(first), "the cache only deepens")
        self.assertGreater(calls_after, calls_before, "it walked further back, not over the same days")
        # Nothing already cached was asked for twice.
        fetched = [u for u in self.web.calls if "CNMSshvol" in u]
        self.assertEqual(len(fetched), len(set(fetched)))

    def test_a_corrupt_cache_is_an_empty_one_not_a_crash(self):
        p = Path(self.tmp.name) / "hf" / "shvol_daily.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        self.assertEqual(SC.load_shvol(), {})


class TheFlowHistory(Base):
    def test_no_client_is_reported_not_guessed(self):
        daily, note = SC.gather_flow_history()
        self.assertEqual(daily, {})
        self.assertFalse(note["available"])
        self.assertIn("endpoint", note["why"])

    def test_a_partial_universe_is_refused(self):
        # The live board sums every fund the daily endpoint returns. Summing
        # whichever ones happened to answer is a different total.
        class Partial:
            def sector_flow(self):
                return {"data": [{"ticker": "SPY"}, {"ticker": "XLF"}, {"ticker": "XLK"}]}

            def etf_in_outflow(self, ticker, start_date=None, end_date=None):
                if ticker != "SPY":
                    return {"data": []}
                return {"data": [{"date": "2026-09-04", "change": 1000.0}]}

        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: Partial())
        daily, note = SC.gather_flow_history()
        self.assertEqual(daily, {})
        self.assertFalse(note["available"])
        self.assertIn("would not be the total", note["why"])
        self.assertEqual(note["n_with_rows"], 1)

    def test_the_whole_universe_sums_per_session(self):
        class Whole:
            def sector_flow(self):
                return {"data": [{"ticker": "SPY"}, {"ticker": "XLF"}]}

            def etf_in_outflow(self, ticker, start_date=None, end_date=None):
                return {"data": [{"date": "2026-09-04", "change": 100.0},
                                 {"date": "2026-09-03", "change": 50.0}]}

        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, uw_getter=lambda: Whole())
        daily, note = SC.gather_flow_history()
        self.assertTrue(note["available"])
        self.assertAlmostEqual(daily["2026-09-04"], 200.0, msg="both funds counted")
        self.assertEqual(note["n_with_rows"], 2)

    def test_the_parser_skips_rows_it_cannot_read_rather_than_guessing(self):
        got = SC.parse_in_outflow({"data": [
            {"date": "2026-09-04", "change": 10.0},
            {"date": "2026-09-04", "change": 5.0},      # same session, added
            {"date": "2026-09-03"},                      # no figure
            {"change": 99.0},                            # no date
            "not a row",
            {"date": "2026-09-02", "change": "not a number"},
        ]})
        self.assertAlmostEqual(got["2026-09-04"], 15.0)
        self.assertNotIn("2026-09-03", got)
        self.assertNotIn("2026-09-02", got)

    def test_a_bare_list_parses_like_an_envelope(self):
        rows = [{"date": "2026-09-04", "change": 7.0}]
        self.assertEqual(SC.parse_in_outflow(rows), SC.parse_in_outflow({"data": rows}))


class TheNamesBehindTheCrowd(Base):
    def _wire(self, rows):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, positions_fn=lambda: rows)

    def test_it_reads_the_injected_positions_not_an_imported_module(self):
        self._wire([
            {"key": "a", "name": "Alpha", "turnover": "READABLE", "as_of": "2026-06-30",
             "top": [{"symbol": "UBER", "issuer": "Uber", "value": 2e9}], "change": {}},
            {"key": "b", "name": "Beta", "turnover": "READABLE", "as_of": "2026-06-30",
             "top": [{"symbol": "UBER", "issuer": "Uber", "value": 1e9}], "change": {}},
        ])
        out = SC.names()
        self.assertTrue(out["available"])
        self.assertEqual([r["name"] for r in out["held"]], ["UBER"])
        self.assertEqual(out["held"][0]["managers"], ["Alpha", "Beta"])

    def test_an_opaque_book_is_left_out_even_when_it_is_the_biggest(self):
        self._wire([
            {"key": "a", "name": "Alpha", "turnover": "READABLE", "as_of": "2026-06-30",
             "top": [{"symbol": "UBER", "value": 1.0}], "change": {}},
            {"key": "b", "name": "Beta", "turnover": "READABLE", "as_of": "2026-06-30",
             "top": [{"symbol": "UBER", "value": 1.0}], "change": {}},
            {"key": "c", "name": "Citadel Advisors", "turnover": "OPAQUE", "as_of": "2026-06-30",
             "top": [{"symbol": "UBER", "value": 9e12}], "change": {}},
        ])
        out = SC.names()
        self.assertEqual(out["held"][0]["n_managers"], 2)
        self.assertNotIn("Citadel Advisors", out["held"][0]["managers"])
        self.assertEqual(out["basis"]["n_opaque"], 1)

    def test_with_nothing_injected_it_is_unavailable_not_a_crash(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, funds_fn=lambda: self.funds)
        out = SC.names()
        self.assertFalse(out["available"])
        self.assertEqual(out["held"], [])
        self.assertFalse(out["headline"]["available"])

    def test_a_provider_that_throws_is_an_empty_card(self):
        def boom():
            raise RuntimeError("EDGAR is down")
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW,
                     funds_fn=lambda: self.funds, positions_fn=boom)
        self.assertFalse(SC.names()["available"])

    def test_the_card_is_stamped_and_versioned(self):
        self._wire([])
        out = SC.names()
        self.assertEqual(out["version"], NM.HF_NAMES_VERSION)
        self.assertEqual(out["scan_version"], SC.HF_SCAN_VERSION)
        self.assertEqual(out["as_of"], NOW.isoformat(timespec="seconds"))


class TheStoredReplaySurvivesARestart(Base):
    """Every deploy restarts the process. A card that takes minutes to build
    and is already on disk must come back with it."""

    def test_configure_brings_the_stored_replay_back(self):
        SC.build_replay()
        stored = SC._STATE["replay"]["n_weeks"]  # noqa: SLF001
        self.assertGreater(stored, 0)
        # A restart: same data directory, empty memory.
        with SC._LOCK:  # noqa: SLF001
            SC._STATE.update({"replay": None, "replay_at": None})  # noqa: SLF001
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, funds_fn=lambda: self.funds)
        self.assertIsNotNone(SC._STATE["replay"], "the card on disk was ignored")  # noqa: SLF001
        self.assertEqual(SC._STATE["replay"]["n_weeks"], stored)  # noqa: SLF001

    def test_and_it_is_not_rebuilt_on_the_first_look(self):
        # The bug's visible cost: the route reported nothing and kicked a
        # multi-minute rebuild while a good card sat on disk.
        SC.build_replay()
        with SC._LOCK:  # noqa: SLF001
            SC._STATE.update({"replay": None, "replay_at": None})  # noqa: SLF001
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, funds_fn=lambda: self.funds)
        self.assertFalse(SC._replay_stale(), "a freshly loaded card is not stale")  # noqa: SLF001
        out = SC.replay()
        self.assertTrue(out["available"])
        self.assertFalse(out["refreshing"])

    def test_no_stored_card_is_still_an_honest_empty(self):
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, funds_fn=lambda: self.funds)
        self.assertIsNone(SC._STATE["replay"])  # noqa: SLF001
        self.assertTrue(SC._replay_stale())  # noqa: SLF001

    def test_a_corrupt_stored_card_does_not_stop_start_up(self):
        p = Path(self.tmp.name) / "hf" / "replay.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, funds_fn=lambda: self.funds)
        self.assertIsNone(SC._STATE["replay"])  # noqa: SLF001
