"""Guards for hf_sources.py — the parsers, on real captured filings.

Every fixture under fixtures/hf is a document EDGAR or the SEC actually
served on September 6, 2026: Pershing Square Inc.'s Q1 and Q2 2026 13F
tables and cover pages, the old Pershing entity's 13F-NT naming the
successor, a live SCHEDULE 13D/A in the structured XML the SEC adopted in
December 2024, a cut of the SEC fails-to-deliver file, a day's EDGAR form
index, and Michael Burry's Substack feed. Nothing here touches the network.
"""

from __future__ import annotations

import os
import json
import unittest
from pathlib import Path

import hf_sources as S

FX = Path(__file__).resolve().parent / "fixtures" / "hf"


def _b(name: str) -> bytes:
    return (FX / name).read_bytes()


class Evidence(unittest.TestCase):
    def test_anonymous_classes_cannot_name_a_fund(self):
        for cls in (S.PRIME_BROKER, S.REGULATORY, S.FLOW_PROXY, S.INFERENCE):
            with self.assertRaises(ValueError, msg=cls):
                S.evidence(cls, None, "2026-09-01", "2026-09-04", "x", fund="Citadel")

    def test_verified_rows_carry_a_fund_and_a_subtype(self):
        row = S.evidence(S.VERIFIED, "FILING", "2026-06-30", "2026-08-14", "EDGAR", fund="Pershing Square")
        self.assertEqual(row["fund"], "Pershing Square")
        with self.assertRaises(ValueError):
            S.evidence(S.VERIFIED, None, "2026-06-30", "2026-08-14", "EDGAR", fund="Pershing Square")
        with self.assertRaises(ValueError):
            S.evidence(S.VERIFIED, "RUMOUR", "2026-06-30", "2026-08-14", "EDGAR", fund="Pershing Square")

    def test_unknown_class_is_refused(self):
        with self.assertRaises(ValueError):
            S.evidence("HEARSAY", None, None, None, "x")

    def test_attribution_ok_is_the_store_s_gate(self):
        good = [S.evidence(S.VERIFIED, "FILING", None, None, "e", fund="A"),
                S.evidence(S.REGULATORY, None, None, None, "cftc")]
        self.assertTrue(S.attribution_ok(good))
        smuggled = [{"class": S.REGULATORY, "fund": "Citadel"}]
        self.assertFalse(S.attribution_ok(smuggled))


class ThirteenF(unittest.TestCase):
    def test_the_q2_table_reads_every_row(self):
        rows = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        self.assertEqual(len(rows), 15)
        amzn = next(r for r in rows if r["issuer"] == "AMAZON COM INC")
        self.assertEqual(amzn["cusip"], "023135106")
        self.assertEqual(amzn["shares"], 8563857.0)
        self.assertEqual(amzn["value"], 2041109677.0, "values are dollars, not thousands")
        self.assertIsNone(amzn["put_call"])
        self.assertEqual(amzn["share_type"], "SH")

    def test_the_cover_page(self):
        p = S.parse_13f_primary(_b("ps_13f_2026-06-30_primary.xml"))
        self.assertEqual(p["period_end"], "2026-06-30")
        self.assertEqual(p["report_type"], "13F HOLDINGS REPORT")
        self.assertFalse(p["is_amendment"])
        self.assertEqual(p["entries"], 15.0)
        self.assertEqual(p["value_total"], 19465692772.0)
        self.assertEqual(p["filer_cik"], 2026053)
        self.assertEqual(p["signed_on"], "2026-08-14")

    def test_the_notice_names_the_successor(self):
        p = S.parse_13f_primary(_b("ps_13f_nt_2026-06-30_primary.xml"))
        self.assertEqual(p["report_type"], "13F NOTICE")
        self.assertEqual(p["filer_cik"], 1336528)
        succ = S.successor_from_notice(p)
        self.assertEqual(succ["cik"], 2026053)
        self.assertIn("PERSHING SQUARE INC", succ["name"])

    def test_a_holdings_report_has_no_successor(self):
        p = S.parse_13f_primary(_b("ps_13f_2026-06-30_primary.xml"))
        self.assertIsNone(S.successor_from_notice(p))

    def test_a_split_line_is_one_position(self):
        """Pershing lists Howard Hughes twice in Q2 (two sub-manager lines).
        Summed, it is one position; unsummed, the diff would call the second
        line a new position."""
        rows = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        hhh = [r for r in rows if "HOWARD HUGHES" in r["issuer"]]
        self.assertEqual(len(hhh), 2)
        agg = S._aggregate(rows)  # noqa: SLF001
        self.assertEqual(len(agg), 14)
        k = next(k for k in agg if agg[k]["issuer"].startswith("HOWARD HUGHES"))
        self.assertEqual(agg[k]["shares"], sum(r["shares"] for r in hhh))

    def test_the_quarter_diff(self):
        q1 = S.parse_13f_table(_b("ps_13f_2026-03-31.xml"))
        q2 = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        d = S.diff_positions(q1, q2)
        self.assertEqual(d["n_prev"], 1)
        self.assertEqual(d["n_now"], 14)
        self.assertEqual(len(d["new"]), 13)
        self.assertEqual(len(d["exited"]), 0)
        hhh = [r for r in d["increased"] + d["reduced"] if "HOWARD HUGHES" in r["issuer"]]
        self.assertEqual(len(hhh) + d["unchanged"], 1, "Howard Hughes appears exactly once")
        self.assertTrue(all(r["shares_prev"] is None for r in d["new"]))
        self.assertGreater(d["value_total_now"], d["value_total_prev"])

    def test_the_diff_ranks_by_size(self):
        q2 = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        d = S.diff_positions(None, q2)
        vals = [r["value_now"] for r in d["new"]]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_exits_are_listed(self):
        q1 = S.parse_13f_table(_b("ps_13f_2026-03-31.xml"))
        q2 = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        d = S.diff_positions(q2, q1)
        self.assertEqual(len(d["exited"]), 13)
        self.assertTrue(all(r["delta_pct"] == -100.0 for r in d["exited"]))

    def test_concentration_and_options_share(self):
        q2 = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        c = S.concentration(q2)
        self.assertEqual(c["n"], 15)
        self.assertGreater(c["top10_weight"], 85.0)
        self.assertEqual(c["options_value_share"], 0.0)
        opt = q2 + [{"issuer": "X", "cusip": "000000000", "value": c["value_total"], "shares": 1, "put_call": "Put"}]
        self.assertEqual(S.concentration(opt)["options_value_share"], 50.0)

    def test_top_positions_carry_weights_that_sum_sensibly(self):
        q2 = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        top = S.top_positions(q2, 5)
        self.assertEqual(len(top), 5)
        self.assertEqual(top[0]["issuer"], "UBER TECHNOLOGIES INC")
        self.assertLessEqual(sum(t["weight"] for t in top), 100.0)


class Sectors(unittest.TestCase):
    def test_sector_rollup_reports_unmapped_instead_of_hiding_it(self):
        q2 = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        cus = S.parse_ftd((FX / "cnsfails_sample.txt").read_text(encoding="latin-1"))
        known = {"AMZN": "Consumer Discretionary", "MSFT": "Technology", "META": "Communication Services"}
        out = S.sector_exposure(q2, cus, lambda s: known.get(s))
        names = [s["sector"] for s in out["sectors"]]
        self.assertEqual(set(names), set(known.values()))
        self.assertEqual(out["unmapped"]["n"], 15 - 3)
        total = sum(s["weight"] for s in out["sectors"]) + out["unmapped"]["weight"]
        self.assertAlmostEqual(total, 100.0, places=6)

    def test_a_sector_function_that_throws_counts_as_unmapped(self):
        q2 = S.parse_13f_table(_b("ps_13f_2026-06-30.xml"))
        cus = S.parse_ftd((FX / "cnsfails_sample.txt").read_text(encoding="latin-1"))

        def boom(_):
            raise RuntimeError("no")
        out = S.sector_exposure(q2, cus, boom)
        self.assertEqual(out["sectors"], [])
        self.assertEqual(out["unmapped"]["n"], 15)


class CusipMap(unittest.TestCase):
    def test_the_fails_file_pairs_cusip_with_symbol(self):
        m = S.parse_ftd((FX / "cnsfails_sample.txt").read_text(encoding="latin-1"))
        self.assertGreater(len(m), 300)
        self.assertEqual(m["023135106"], "AMZN")
        self.assertEqual(m["594918104"], "MSFT")

    def test_every_pershing_cusip_is_in_the_cut(self):
        m = S.parse_ftd((FX / "cnsfails_sample.txt").read_text(encoding="latin-1"))
        for r in S.parse_13f_table(_b("ps_13f_2026-06-30.xml")):
            self.assertIn(r["cusip"], m, r["issuer"])


class ThirteenD(unittest.TestCase):
    def test_the_structured_13d(self):
        d = S.parse_13d(_b("schedule_13d_a_sample.xml"))
        self.assertEqual(d["form"], "SCHEDULE 13D/A")
        self.assertEqual(d["issuer_name"], "Tutor Perini Corporation")
        self.assertEqual(d["percent_max"], 8.2)
        self.assertEqual(d["event_date"], "2026-08-31")
        self.assertEqual(len(d["reporting_persons"]), 4)
        self.assertEqual(d["filer_cik"], 906134)
        self.assertTrue(d["purpose"])

    def test_garbage_is_none_not_a_crash(self):
        self.assertIsNone(S.parse_13d(b"<html>not xml at all"))
        self.assertEqual(S.parse_13f_table(b"<html>"), [])
        self.assertEqual(S.parse_13f_primary(b"nope"), {})


class DailyIndex(unittest.TestCase):
    SAMPLE = (
        "Description:           Daily Index of EDGAR Dissemination Feed by Form Type\n"
        "Last Data Received:    September 04, 2026\n\n"
        "Form Type   Company Name                                                  CIK         Date Filed  File Name\n"
        "-----------------------------------------------------------------------------------------------------------\n"
        "13F-HR      Nordflint Capital Partners Fondsmaeglerselskab A/S            1815421     20260904    edgar/data/1815421/0001815421-26-000003.txt\n"
        "13F-HR/A    CITADEL ADVISORS LLC                                          1423053     20260904    edgar/data/1423053/0001104659-26-100000.txt\n"
        "SCHEDULE 13D     AMG Pantheon Infrastructure Fund, LLC                         2046200     20260904    edgar/data/2046200/0001104659-26-105535.txt\n"
        "SCHEDULE 13G/A   Some Holder  With Two  Spaces Inc                           1234567     20260904    edgar/data/1234567/0001234567-26-000001.txt\n"
        "8-K         SOMEBODY ELSE                                                 7777777     20260904    edgar/data/7777777/0007777777-26-000001.txt\n"
    )

    def test_watched_forms_only_and_fields(self):
        rows = S.parse_daily_index(self.SAMPLE)
        self.assertEqual([r["form"] for r in rows], ["13F-HR", "13F-HR/A", "SCHEDULE 13D", "SCHEDULE 13G/A"])
        cit = rows[1]
        self.assertEqual(cit["cik"], 1423053)
        self.assertEqual(cit["filed"], "2026-09-04")
        self.assertEqual(cit["accession"], "0001104659-26-100000")

    def test_a_company_name_with_double_spaces_survives(self):
        rows = S.parse_daily_index(self.SAMPLE)
        self.assertEqual(rows[3]["cik"], 1234567)
        self.assertIn("Two  Spaces", rows[3]["company"])

    def test_all_forms_when_asked(self):
        rows = S.parse_daily_index(self.SAMPLE, forms=())
        self.assertEqual(len(rows), 5)


class Feeds(unittest.TestCase):
    def test_the_substack_feed(self):
        items = S.parse_rss(_b("burry_substack.xml"))
        self.assertGreaterEqual(len(items), 15)
        self.assertTrue(all(i["title"] for i in items))
        self.assertTrue(all(i["link"] for i in items))
        self.assertTrue(items[0]["published"].startswith("2026-"))
        self.assertLessEqual(len(items[0]["summary"]), 400)

    def test_pubdate_formats(self):
        self.assertEqual(S._rss_date("Thu, 05 Feb 2026 08:00:00 GMT"), "2026-02-05T08:00:00+00:00")  # noqa: SLF001
        self.assertIsNone(S._rss_date("yesterday"))  # noqa: SLF001


class Transport(unittest.TestCase):
    def test_offline_never_fetches(self):
        import os
        calls = []
        S.configure(fetch_fn=lambda url: calls.append(url) or b"{}")
        os.environ["JERRY_NO_NET"] = "1"
        try:
            self.assertIsNone(S.submissions(1423053))
            self.assertEqual(S.daily_index(__import__("datetime").date(2026, 9, 4)), [])
        finally:
            os.environ.pop("JERRY_NO_NET", None)
            S.configure(fetch_fn=None)
        self.assertEqual(calls, [])

    def test_an_injected_fetch_is_used_and_cached_in_memory(self):
        import os
        os.environ.pop("JERRY_NO_NET", None)
        calls = []

        def fetch(url):
            calls.append(url)
            return b'{"name": "X", "filings": {"recent": {"form": [], "filingDate": [], "reportDate": [], "accessionNumber": [], "primaryDocument": [], "acceptanceDateTime": []}}}'
        S._MEM.clear()  # noqa: SLF001
        S.configure(fetch_fn=fetch)
        try:
            self.assertEqual(S.submissions(42)["name"], "X")
            self.assertEqual(S.submissions(42)["name"], "X")
        finally:
            S.configure(fetch_fn=None)
            S._MEM.clear()  # noqa: SLF001
        self.assertEqual(len(calls), 1, "the second read is served from memory")


class TheXHandleCannotSmuggleAQuery(unittest.TestCase):
    """A handle reaches an X search. "BillAckman OR from:someone_else" would
    have returned a second account's posts, and hf_watch files them under
    the first manager's OWN words — the attribution rule this whole feature
    is built on. There is no user-supplied query any more."""

    def test_a_real_handle_is_accepted(self):
        for good in ("BillAckman", "@BillAckman", "a_b1", "x", "a" * 15):
            self.assertTrue(S.x_handle_ok(good), good)

    def test_anything_else_is_refused(self):
        for bad in ("Bill Ackman", "a OR from:b", "", None, "a" * 16, "from:x", "a-b", "a.b"):
            self.assertFalse(S.x_handle_ok(bad), repr(bad))

    def test_the_query_is_composed_not_accepted(self):
        self.assertEqual(S.x_query_for("@BillAckman"),
                         "from:BillAckman -is:retweet -is:reply")
        self.assertIsNone(S.x_query_for("BillAckman OR from:someone_else"))

    def test_a_hostile_handle_sends_no_request_at_all(self):
        sent = []
        S.configure_x(lambda url, tok: sent.append(url) or b"{}")
        os.environ["X_BEARER_TOKEN"] = "t"
        try:
            self.assertEqual(S.x_statements("BillAckman OR from:someone_else"), [])
            self.assertEqual(sent, [], "nothing reached the network")
            S.x_statements("BillAckman")
            self.assertEqual(len(sent), 1)
            self.assertIn("from%3ABillAckman", sent[0])
            self.assertNotIn("someone_else", sent[0])
        finally:
            os.environ.pop("X_BEARER_TOKEN", None)
            S.configure_x(None)

    def test_the_registry_refuses_a_bad_handle_and_any_query(self):
        import hf_registry as R
        base = {"key": "k", "name": "n", "turnover": "READABLE", "ciks": [{"cik": 1}]}
        self.assertEqual(R.validate({**base, "x_handle": "BillAckman"}), [])
        self.assertTrue(R.validate({**base, "x_handle": "a OR from:b"}))
        self.assertTrue(R.validate({**base, "x_query": "from:x"}),
                        "a free-form query is not accepted at all")


if __name__ == "__main__":
    unittest.main()


class TheCachesAreBounded(unittest.TestCase):
    """`_MEM` used to hold every response body for the life of the process,
    and the disk cache wrote each one as hex — exactly twice its size. A
    single Phase 5 replay build pushed hundreds of megabytes through both."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        S._MEM.clear()  # noqa: SLF001
        self.served = {}
        S.configure(fetch_fn=lambda url: self.served.get(url, b"x"),
                    data_dir=self.tmp.name)

    def tearDown(self):
        S.configure(fetch_fn=None, post_fn=None)
        S._MEM.clear()  # noqa: SLF001
        self.tmp.cleanup()

    def test_memory_never_grows_past_its_ceiling(self):
        body = b"a" * (256 * 1024)
        for i in range(400):                      # 100 MB offered
            url = f"https://example.invalid/{i}"
            self.served[url] = body
            S._get(url, ttl=9999)                 # noqa: SLF001
        stats = S.cache_stats()
        self.assertLessEqual(stats["memory_bytes"], S.MEM_MAX_BYTES)
        self.assertLess(stats["memory_entries"], 400, "it evicted rather than grew")

    def test_a_body_over_the_ceiling_is_returned_but_never_cached(self):
        huge = b"z" * (S.CACHE_MAX_BODY + 1)
        url = "https://example.invalid/huge"
        self.served[url] = huge
        self.assertEqual(S._get(url, ttl=9999), huge, "the caller still gets it")  # noqa: SLF001
        self.assertEqual(S.cache_stats()["memory_entries"], 0)
        self.assertEqual(S.cache_stats()["disk_files"], 0, "and nothing reached the volume")

    def test_the_least_recently_used_is_the_one_evicted(self):
        body = b"b" * (1024 * 1024)
        for i in range(40):
            url = f"https://example.invalid/lru{i}"
            self.served[url] = body
            S._get(url, ttl=9999)  # noqa: SLF001
            if i == 0:
                continue
            S._get("https://example.invalid/lru0", ttl=9999)   # keep touching the first  # noqa: SLF001
        self.assertIn("https://example.invalid/lru0", S._MEM,  # noqa: SLF001
                      "the constantly used entry survived")

    def test_the_disk_cache_no_longer_doubles_the_body(self):
        # Hex was measured at exactly 2.0x. Gzip plus base64 must beat the
        # body itself on compressible text, never mind hex.
        text = (b"Date|Symbol|ShortVolume|TotalVolume\n"
                b"20260904|AAPL|500000|1000000\n" * 4000)
        url = "https://example.invalid/finra"
        self.served[url] = text
        S._get(url, ttl=9999)  # noqa: SLF001
        stats = S.cache_stats()
        self.assertEqual(stats["disk_files"], 1)
        self.assertLess(stats["disk_bytes"], len(text),
                        "the record on disk is smaller than the body, not twice it")
        self.assertLess(stats["disk_bytes"], len(text) * 2 / 6,
                        "and far smaller than hex would have been")

    def test_a_record_written_by_the_old_version_is_still_read(self):
        # A cache file outlives the code that wrote it; re-fetching
        # everything on deploy day is the opposite of what a cache is for.
        import json as _json
        url = "https://example.invalid/legacy"
        p = S._cache_path(url)  # noqa: SLF001
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"ts": __import__("time").time(), "kind": "bytes",
                                  "url": url, "hex": b"legacy body".hex()}))
        self.assertEqual(S._get(url, ttl=9999), b"legacy body")  # noqa: SLF001

    def test_a_corrupt_record_falls_through_to_the_network(self):
        import json as _json
        url = "https://example.invalid/corrupt"
        self.served[url] = b"fresh"
        p = S._cache_path(url)  # noqa: SLF001
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"ts": __import__("time").time(), "gz": "not-base64!!"}))
        self.assertEqual(S._get(url, ttl=9999), b"fresh")  # noqa: SLF001

    def test_pruning_removes_oversized_files_and_keeps_the_rest(self):
        d = Path(self.tmp.name) / "hf" / "cache"
        d.mkdir(parents=True, exist_ok=True)
        (d / "big.json").write_text("x" * (S.CACHE_MAX_BODY + 10))
        (d / "small.json").write_text("x" * 500)
        out = S.prune_cache()
        self.assertEqual(out["removed"], 1)
        self.assertGreater(out["freed_bytes"], S.CACHE_MAX_BODY)
        self.assertTrue((d / "small.json").exists(), "an EDGAR document cached forever stays")
        self.assertFalse((d / "big.json").exists())

    def test_pruning_never_removes_by_age(self):
        # Entries under the ceiling may be filings cached forever, and each
        # one saves a round trip every time a fund card is built.
        d = Path(self.tmp.name) / "hf" / "cache"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "ancient.json"
        f.write_text('{"ts": 0, "gz": ""}')
        S.prune_cache()
        self.assertTrue(f.exists())
        self.assertIn("whatever their age", S.prune_cache()["kept_rule"])

    def test_clearing_memory_directly_does_not_confuse_the_budget(self):
        # The suites clear _MEM between tests; a stale byte count would then
        # evict everything on the next write.
        body = b"c" * (512 * 1024)
        for i in range(10):
            url = f"https://example.invalid/clr{i}"
            self.served[url] = body
            S._get(url, ttl=9999)  # noqa: SLF001
        S._MEM.clear()  # noqa: SLF001
        url = "https://example.invalid/after"
        self.served[url] = body
        S._get(url, ttl=9999)  # noqa: SLF001
        self.assertEqual(S.cache_stats()["memory_entries"], 1)
        self.assertEqual(S.cache_stats()["memory_bytes"], len(body))


class RecompressingTheOldRecords(unittest.TestCase):
    """Entries that expire get rewritten compactly on their own, but EDGAR
    filings are cached FOREVER and would stay at double size for the life of
    the volume. Converting them must not change a single byte of any body."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        S._MEM.clear()  # noqa: SLF001
        S.configure(data_dir=self.tmp.name)
        self.d = Path(self.tmp.name) / "hf" / "cache"
        self.d.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        S.configure(fetch_fn=None, post_fn=None)
        S._MEM.clear()  # noqa: SLF001
        self.tmp.cleanup()

    def _hex_record(self, name, body, ts=1234.5, kind="doc", url="u"):
        import json as _json
        p = self.d / f"{name}.json"
        p.write_text(_json.dumps({"ts": ts, "kind": kind, "url": url, "hex": body.hex()}))
        return p

    def test_the_body_survives_byte_for_byte(self):
        body = bytes(range(256)) * 400
        p = self._hex_record("a", body)
        S.recompress_cache()
        rec = json.loads(p.read_text())
        self.assertNotIn("hex", rec)
        self.assertEqual(S._decode(rec), body, "the bytes are identical")  # noqa: SLF001

    def test_the_timestamp_is_preserved_so_freshness_is_unchanged(self):
        # A converted entry that looked newly fetched would extend its own
        # TTL and serve stale data.
        p = self._hex_record("b", b"body", ts=999.0)
        S.recompress_cache()
        self.assertEqual(json.loads(p.read_text())["ts"], 999.0)

    def test_it_actually_shrinks_a_real_shaped_body(self):
        body = (b"Date|Symbol|ShortVolume|TotalVolume\n"
                b"20260904|AAPL|500000|1000000\n" * 3000)
        p = self._hex_record("c", body)
        before = p.stat().st_size
        out = S.recompress_cache()
        self.assertEqual(out["rewritten"], 1)
        self.assertLess(p.stat().st_size, before / 4)
        self.assertGreater(out["freed_bytes"], 0)

    def test_it_is_idempotent(self):
        self._hex_record("d", b"x" * 5000)
        first = S.recompress_cache()
        second = S.recompress_cache()
        self.assertEqual(first["rewritten"], 1)
        self.assertEqual(second["rewritten"], 0)
        self.assertEqual(second["already_compact"], 1)

    def test_it_is_batched_and_reports_what_is_left(self):
        for i in range(10):
            self._hex_record(f"batch{i}", b"y" * 2000)
        out = S.recompress_cache(limit=4)
        self.assertEqual(out["rewritten"], 4)
        self.assertEqual(out["remaining"], 6)
        while S.recompress_cache(limit=4)["remaining"]:
            pass
        self.assertEqual(S.recompress_cache()["rewritten"], 0, "the run completes")

    def test_an_unreadable_record_is_left_alone_not_deleted(self):
        # A cache entry nobody can read costs a re-fetch; deleting it on a
        # guess could throw away the only copy of something.
        p = self.d / "bad.json"
        p.write_text('{"ts": 1, "kind": "doc", "hex": "not-hex-at-all"}')
        S.recompress_cache()
        self.assertTrue(p.exists())

    def test_a_file_that_is_not_json_is_counted_not_crashed_on(self):
        (self.d / "junk.json").write_text("{not json")
        out = S.recompress_cache()
        self.assertGreaterEqual(out["unreadable"], 1)

    def test_a_converted_record_is_still_served_by_get(self):
        # The whole point: the cache keeps working across the conversion.
        body = b"still here"
        url = "https://example.invalid/converted"
        p = S._cache_path(url)  # noqa: SLF001
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ts": __import__("time").time(), "kind": "doc",
                                 "url": url, "hex": body.hex()}))
        S.recompress_cache()
        S._MEM.clear()  # noqa: SLF001
        S.configure(fetch_fn=lambda u: b"REFETCHED", data_dir=self.tmp.name)
        self.assertEqual(S._get(url, ttl=9999), body, "served from cache, not re-fetched")  # noqa: SLF001

    def test_an_empty_cache_is_not_an_error(self):
        out = S.recompress_cache()
        self.assertEqual((out["scanned"], out["rewritten"], out["remaining"]), (0, 0, 0))
