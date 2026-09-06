"""Guards for hf_watch.py — the Named Fund Watch, offline.

The whole pipeline runs here against an in-memory EDGAR: a fake transport
that answers the exact URLs the module asks for with the captured fixtures.
That proves the successor follow (old Pershing → Pershing Square Inc.), the
quarter diff, the two dates on every fact, the UNKNOWN rule, and the store's
refusal to write a record that attributes anonymous data to a fund — with
no network and no clock dependence.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import hf_sources as S
import hf_watch as W

FX = Path(__file__).resolve().parent / "fixtures" / "hf"
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

OLD, NEW = 1336528, 2026053
ACC_NT = "0001172661-26-003777"
ACC_Q2 = "0001172661-26-003800"
ACC_Q1 = "0001172661-26-002336"


def _sub(cik: int, rows: list[tuple]) -> bytes:
    """A submissions feed in EDGAR's column layout."""
    forms, filed, period, acc, doc, acc_dt = zip(*rows) if rows else ([], [], [], [], [], [])
    return json.dumps({"cik": str(cik), "name": f"ENTITY {cik}",
                       "filings": {"recent": {"form": list(forms), "filingDate": list(filed),
                                              "reportDate": list(period), "accessionNumber": list(acc),
                                              "primaryDocument": list(doc),
                                              "acceptanceDateTime": list(acc_dt)}}}).encode()


def _folder(names: list[str]) -> bytes:
    return json.dumps({"directory": {"item": [{"name": n, "type": "file"} for n in names]}}).encode()


class FakeEdgar:
    """Answers the URLs hf_sources builds; records every ask."""

    def __init__(self):
        self.calls = []
        self.docs = {
            S.EDGAR_SUB.format(cik=OLD): _sub(OLD, [
                ("13F-NT", "2026-08-14", "2026-06-30", ACC_NT, "primary_doc.xml", "2026-08-14T20:01:00.000Z"),
                ("13F-HR", "2026-05-15", "2026-03-31", "0001172661-26-002336", "primary_doc.xml", "2026-05-15T20:01:00.000Z"),
            ]),
            S.EDGAR_SUB.format(cik=NEW): _sub(NEW, [
                ("SCHEDULE 13G", "2026-08-14", "", "0000000000-26-000009", "primary_doc.xml", "2026-08-14T21:00:00.000Z"),
                ("13F-HR", "2026-08-14", "2026-06-30", ACC_Q2, "primary_doc.xml", "2026-08-14T20:02:00.000Z"),
                ("13F-HR", "2026-05-15", "2026-03-31", ACC_Q1, "primary_doc.xml", "2026-05-15T20:02:00.000Z"),
            ]),
            S.EDGAR_FOLDER.format(cik=OLD, acc=ACC_NT.replace("-", "")): _folder(["primary_doc.xml"]),
            S.EDGAR_DOC.format(cik=OLD, acc=ACC_NT.replace("-", ""), doc="primary_doc.xml"): (FX / "ps_13f_nt_2026-06-30_primary.xml").read_bytes(),
            S.EDGAR_FOLDER.format(cik=NEW, acc=ACC_Q2.replace("-", "")): _folder(["primary_doc.xml", "infotable.xml"]),
            S.EDGAR_DOC.format(cik=NEW, acc=ACC_Q2.replace("-", ""), doc="primary_doc.xml"): (FX / "ps_13f_2026-06-30_primary.xml").read_bytes(),
            S.EDGAR_DOC.format(cik=NEW, acc=ACC_Q2.replace("-", ""), doc="infotable.xml"): (FX / "ps_13f_2026-06-30.xml").read_bytes(),
            S.EDGAR_FOLDER.format(cik=NEW, acc=ACC_Q1.replace("-", "")): _folder(["primary_doc.xml", "infotable.xml"]),
            S.EDGAR_DOC.format(cik=NEW, acc=ACC_Q1.replace("-", ""), doc="primary_doc.xml"): (FX / "ps_13f_2026-03-31_primary.xml").read_bytes(),
            S.EDGAR_DOC.format(cik=NEW, acc=ACC_Q1.replace("-", ""), doc="infotable.xml"): (FX / "ps_13f_2026-03-31.xml").read_bytes(),
            S.EDGAR_FOLDER.format(cik=NEW, acc="000000000026000009"): _folder(["primary_doc.xml"]),
            S.EDGAR_DOC.format(cik=NEW, acc="000000000026000009", doc="primary_doc.xml"): (FX / "schedule_13d_a_sample.xml").read_bytes(),
            "https://michaeljburry.substack.com/feed": (FX / "burry_substack.xml").read_bytes(),
        }

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url in self.docs:
            return self.docs[url]
        if "news.google.com" in url:
            return b"<rss><channel></channel></rss>"
        raise RuntimeError(f"fake EDGAR has no {url}")


SEED = {"version": 1, "managers": [
    {"key": "pershing", "name": "Pershing Square", "people": ["Bill Ackman"], "style": "activist / concentrated",
     "turnover": "READABLE", "ciks": [{"cik": OLD, "from": None}], "feeds": [], "news_query": "Pershing Square"},
    {"key": "scion", "name": "Scion Asset Management", "people": ["Michael Burry"], "style": "concentrated",
     "turnover": "READABLE", "status": "CEASED", "ciks": [{"cik": 1649339, "from": None, "to": "2025-09-30"}],
     "feeds": [{"kind": "rss", "label": "Cassandra Unchained", "url": "https://michaeljburry.substack.com/feed"}],
     "news_query": "Michael Burry"},
]}


class Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop("JERRY_NO_NET", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.seed = Path(self.tmp.name) / "seed.json"
        self.seed.write_text(json.dumps(SEED))
        self.edgar = FakeEdgar()
        S._MEM.clear()  # noqa: SLF001
        S.configure(fetch_fn=self.edgar, data_dir=self.tmp.name, now_fn=lambda: NOW)
        W._STATE.update({"records": {}, "as_of": None, "refreshing": False, "errors": {},  # noqa: SLF001
                         "last_sweep": None, "new_filings": [], "cusips": 0})
        W.configure(data_dir=self.tmp.name, sector_fn=lambda s: {"AMZN": "Consumer Cyclical", "MSFT": "Technology"}.get(s),
                    sector_norm=lambda n: {"Consumer Cyclical": "Consumer Discretionary"}.get(n, n),
                    now_fn=lambda: NOW, seed_path=self.seed)

    def tearDown(self):
        S.configure(fetch_fn=None)
        S._MEM.clear()  # noqa: SLF001
        self.tmp.cleanup()


class Successor(Base):
    def test_the_notice_is_followed_to_the_new_filer(self):
        rec = W.fund("pershing")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["cik"], NEW, "the card read the 13F-NT and moved to the successor")
        self.assertEqual(rec["successor"]["cik"], NEW)
        self.assertEqual(rec["successor"]["as_of"], "2026-06-30")
        self.assertEqual(rec["successor"]["public_on"], "2026-08-14")
        self.assertTrue(any("13F-NT" in n for n in rec["notes"]))

    def test_the_successor_s_holdings_are_what_is_shown(self):
        rec = W.fund("pershing")
        self.assertEqual(rec["holdings"]["as_of"], "2026-06-30")
        self.assertEqual(rec["holdings"]["public_on"], "2026-08-14")
        self.assertEqual(rec["holdings"]["n"], 15)
        self.assertEqual(rec["holdings"]["prior_as_of"], "2026-03-31")


class TwoDates(Base):
    def test_every_verified_row_carries_both_dates(self):
        rec = W.fund("pershing")
        self.assertTrue(rec["evidence"])
        for e in rec["evidence"]:
            self.assertEqual(e["class"], S.VERIFIED)
            self.assertIn("as_of", e)
            self.assertIn("public_on", e)
            self.assertEqual(e["fund"], "Pershing Square")

    def test_dates_are_spelled_out_for_the_card(self):
        rec = W.fund("pershing")
        self.assertEqual(rec["dates"]["last_as_of"], "June 30, 2026")
        self.assertEqual(rec["dates"]["last_public_on"], "August 14, 2026")
        self.assertEqual(rec["dates"]["prior_as_of"], "March 31, 2026")
        self.assertEqual(W.long_date("2026-09-07"), "September 7, 2026")
        self.assertIsNone(W.long_date(None))

    def test_the_filing_trail_lists_both_quarters(self):
        rec = W.fund("pershing")
        trail = [(f["form"], f["as_of"], f["public_on"]) for f in rec["filings"]]
        self.assertIn(("13F-HR", "2026-06-30", "2026-08-14"), trail)
        self.assertIn(("13F-HR", "2026-03-31", "2026-05-15"), trail)


class TheDiff(Base):
    def test_quarter_on_quarter(self):
        rec = W.fund("pershing")
        ch = rec["change"]
        self.assertEqual(len(ch["new"]), 13)
        self.assertEqual(len(ch["exited"]), 0)
        self.assertEqual(rec["concentration"]["n"], 15)
        self.assertEqual(rec["top"][0]["issuer"], "UBER TECHNOLOGIES INC")

    def test_sectors_use_the_normaliser_and_report_unmapped(self):
        rec = W.fund("pershing")
        # No CUSIP map is available offline, so nothing maps to a symbol —
        # and the roll-up says so instead of inventing a sector.
        self.assertEqual(rec["sectors"]["unmapped"]["n"], 15)
        self.assertEqual(rec["sectors"]["sectors"], [])

    def test_with_a_cusip_map_the_user_s_sectors_win_and_are_normalised(self):
        cus = S.parse_ftd((FX / "cnsfails_sample.txt").read_text(encoding="latin-1"))
        entry = W.registry()["managers"][0]
        rec = W._build(entry, cus)  # noqa: SLF001
        names = {s["sector"] for s in rec["sectors"]["sectors"]}
        self.assertIn("Consumer Discretionary", names, "Consumer Cyclical was folded onto the app's name")
        self.assertIn("Technology", names)
        self.assertEqual(rec["sectors"]["unmapped"]["n"], 13)
        self.assertEqual(rec["top"][3]["symbol"], "AMZN")


class TheRule(Base):
    def test_between_filings_is_unknown_in_that_word(self):
        rec = W.fund("pershing")
        self.assertEqual(rec["activity"]["state"], "UNKNOWN")
        self.assertEqual(rec["activity"]["since"], "2026-06-30")
        self.assertEqual(rec["activity"]["days_since"], (date(2026, 9, 6) - date(2026, 6, 30)).days)

    def test_a_passive_13g_does_not_count_as_activity(self):
        rec = W.fund("pershing")
        kinds = [e["kind"] for e in rec["events"]]
        self.assertIn("PASSIVE NOTICE", kinds)
        self.assertEqual(rec["activity"]["state"], "UNKNOWN")

    def test_a_13d_after_the_period_does(self):
        rec = {"status": "FILING", "holdings": {"as_of": "2026-06-30"},
               "events": [{"kind": "EVENT", "as_of": "2026-08-31", "form": "SCHEDULE 13D"}]}
        st = W.activity_state(rec, today=date(2026, 9, 6))
        self.assertEqual(st["state"], "FILED SINCE")
        self.assertEqual(len(st["items"]), 1)

    def test_a_13d_before_the_period_does_not(self):
        rec = {"status": "FILING", "holdings": {"as_of": "2026-06-30"},
               "events": [{"kind": "EVENT", "as_of": "2026-06-30", "form": "SCHEDULE 13D/A"}]}
        self.assertEqual(W.activity_state(rec, today=date(2026, 9, 6))["state"], "UNKNOWN")

    def test_ceased_is_its_own_state(self):
        rec = W.fund("scion")
        self.assertEqual(rec["activity"]["state"], "CEASED")
        self.assertEqual(rec["status"], "CEASED")

    def test_the_broader_trend_is_a_separate_block_in_its_own_class(self):
        rec = W.fund("pershing")
        bt = rec["broader_trend"]
        self.assertEqual(bt["class"], S.INFERENCE)
        self.assertFalse(bt["available"])
        self.assertNotIn("fund", bt)
        self.assertIn("not", bt["note"].lower())


class Statements(Base):
    def test_a_ceased_manager_s_own_channel_is_read(self):
        rec = W.fund("scion")
        self.assertGreater(len(rec["statements"]), 0)
        subtypes = {e["subtype"] for e in rec["evidence"]}
        self.assertIn("STATEMENT", subtypes)
        self.assertNotIn("FILING", subtypes, "nothing filed in the window; statements are all there is")

    def test_statements_are_never_positions(self):
        rec = W.fund("scion")
        self.assertIsNone(rec["change"] and rec["change"].get("from_statements"))
        for s in rec["statements"]:
            self.assertIn("title", s)
            self.assertNotIn("shares", s)


class CrossCheck(Base):
    """What is compared is which names are largest, not how many rows each
    side used — EDGAR lists split lines, Unusual Whales dedupes tickers."""

    def _with_uw(self, rows):
        class UW:
            def institution_holdings(self, name, date=None, limit=500):
                return {"data": rows}
        W.configure(data_dir=self.tmp.name, uw_getter=lambda: UW(), sector_fn=lambda s: None,
                    now_fn=lambda: NOW, seed_path=self.seed)
        cus = S.parse_ftd((FX / "cnsfails_sample.txt").read_text(encoding="latin-1"))
        entry = W.registry()["managers"][0]
        return W._build(entry, cus)["crosscheck"]  # noqa: SLF001

    def test_same_largest_names_agree_despite_different_row_counts(self):
        q2 = S.parse_13f_table((FX / "ps_13f_2026-06-30.xml").read_bytes())
        cus = S.parse_ftd((FX / "cnsfails_sample.txt").read_text(encoding="latin-1"))
        # UW's view: one row per ticker, half the value, an extra tiny name.
        rows = [{"ticker": cus[p["cusip"]], "value": (p["value"] or 0) / 2, "sector": "Technology"} for p in q2]
        rows.append({"ticker": "ZZZ", "value": 1.0, "sector": "Energy"})
        cc = self._with_uw(rows)
        self.assertTrue(cc["available"])
        self.assertTrue(cc["agree"])
        self.assertEqual(cc["n_edgar"], 14, "aggregated positions, not raw lines")
        self.assertEqual(cc["n_edgar_lines"], 15)
        self.assertGreaterEqual(cc["top_overlap"], 7)

    def test_different_largest_names_is_a_conflict(self):
        rows = [{"ticker": t, "value": 1e9 - i, "sector": "Energy"} for i, t in
                enumerate(["XOM", "CVX", "COP", "OXY", "SLB", "HAL", "EOG", "PXD", "MPC", "VLO"])]
        cc = self._with_uw(rows)
        self.assertFalse(cc["agree"])
        self.assertEqual(cc["top_overlap"], 0)

    def test_too_few_names_to_compare_is_inconclusive_not_a_conflict(self):
        cc = self._with_uw([{"ticker": "UBER", "value": 1.0, "sector": "Technology"}])
        self.assertIsNone(cc["agree"])

    def test_uw_sectors_feed_the_rollup(self):
        q2 = S.parse_13f_table((FX / "ps_13f_2026-06-30.xml").read_bytes())
        cus = S.parse_ftd((FX / "cnsfails_sample.txt").read_text(encoding="latin-1"))
        rows = [{"ticker": cus[p["cusip"]], "value": p["value"], "sector": "Technology"} for p in q2]
        class UW:
            def institution_holdings(self, name, date=None, limit=500):
                return {"data": rows}
        W.configure(data_dir=self.tmp.name, uw_getter=lambda: UW(), sector_fn=lambda s: None,
                    now_fn=lambda: NOW, seed_path=self.seed)
        rec = W._build(W.registry()["managers"][0], cus)  # noqa: SLF001
        self.assertEqual(rec["sectors"]["unmapped"]["n"], 0)
        self.assertEqual(rec["sectors"]["sectors"][0]["sector"], "Technology")
        self.assertNotIn("sector_by_symbol", rec["crosscheck"], "the working map is not stored")


class Attribution(Base):
    def test_the_store_refuses_a_smuggled_attribution(self):
        rec = {"key": "x", "name": "X", "evidence": [{"class": S.REGULATORY, "fund": "X"}]}
        with self.assertRaises(ValueError):
            W._save_record(rec)  # noqa: SLF001

    def test_records_are_persisted_and_reloaded(self):
        W.fund("pershing")
        p = Path(self.tmp.name) / "hf" / "funds" / "pershing.json"
        self.assertTrue(p.exists())
        W._STATE["records"].clear()  # noqa: SLF001
        W.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, seed_path=self.seed)
        self.assertIn("pershing", W._STATE["records"])  # noqa: SLF001

    def test_a_record_from_an_older_module_version_is_not_reloaded(self):
        W.fund("pershing")
        p = Path(self.tmp.name) / "hf" / "funds" / "pershing.json"
        rec = json.loads(p.read_text())
        rec["watch_version"] = "0.9.0"
        p.write_text(json.dumps(rec))
        W._STATE["records"].clear()  # noqa: SLF001
        W.configure(data_dir=self.tmp.name, now_fn=lambda: NOW, seed_path=self.seed)
        self.assertNotIn("pershing", W._STATE["records"], "an old shape is re-read, never shown")  # noqa: SLF001


class Snapshot(Base):
    def test_offline_answers_not_read_yet(self):
        os.environ["JERRY_NO_NET"] = "1"
        try:
            snap = W.snapshot()
        finally:
            os.environ.pop("JERRY_NO_NET", None)
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["n_managers"], 2)
        self.assertEqual(snap["managers"][0]["activity"]["state"], "NOT READ YET")
        self.assertFalse(snap["refreshing"], "offline never starts a worker")
        self.assertIn("broader_trend", snap)

    def test_a_read_manager_summarises(self):
        W.fund("pershing")
        os.environ["JERRY_NO_NET"] = "1"
        try:
            snap = W.snapshot()
        finally:
            os.environ.pop("JERRY_NO_NET", None)
        m = next(x for x in snap["managers"] if x["key"] == "pershing")
        self.assertEqual(m["activity"]["state"], "UNKNOWN")
        self.assertEqual(m["dates"]["last_as_of"], "June 30, 2026")
        self.assertEqual(m["n_new"], 13)
        self.assertEqual(m["successor"]["cik"], NEW)

    def test_watchlist_overlay_validation(self):
        out = W.set_watchlist({"managers": [{"key": "bad", "name": "", "turnover": "X", "ciks": []}]})
        self.assertFalse(out["ok"])
        self.assertIn("bad", out["problems"])
        out = W.set_watchlist({"managers": [{"key": "new_one", "name": "New One", "turnover": "READABLE",
                                             "ciks": [{"cik": 999}]}], "removed": ["scion"]})
        self.assertTrue(out["ok"])
        keys = [m["key"] for m in W.registry()["managers"]]
        self.assertIn("new_one", keys)
        self.assertNotIn("scion", keys)

    def test_unknown_key_is_a_404_shaped_answer(self):
        out = W.fund("nobody")
        self.assertFalse(out["ok"])


class Budget(Base):
    def test_a_filing_is_fetched_once_and_kept(self):
        W.fund("pershing")
        n1 = sum(1 for u in self.edgar.calls if "infotable.xml" in u)
        W._STATE["records"].clear()  # noqa: SLF001
        S._MEM.clear()  # noqa: SLF001
        W.fund("pershing")
        n2 = sum(1 for u in self.edgar.calls if "infotable.xml" in u)
        self.assertEqual(n1, 2)
        self.assertEqual(n2, n1, "the parsed table is on disk; the network is not asked again")


if __name__ == "__main__":
    unittest.main()
