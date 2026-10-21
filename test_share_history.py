"""Tests for share_history.py: shares outstanding from SEC cover pages (v5.45)."""

import json
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

import share_history as SH

CIK = 1045810


def facts(dei=(), filings=(), cik=CIK, name="TEST CORP", extra=None):
    """Company Facts: `dei` cover counts (as_of, shares, accn, form, filed);
    `filings` (accn, form, filed) every 10-Q/10-K the us-gaap facts came from."""
    rows = [{"end": a, "val": v, "accn": acc, "form": f, "filed": fd} for a, v, acc, f, fd in dei]
    gaap = [{"start": "2020-01-01", "end": fd, "val": 1, "accn": acc, "form": f, "filed": fd}
            for acc, f, fd in filings]
    out = {"cik": cik, "entityName": name, "facts": {
        "us-gaap": {"Revenues": {"units": {"USD": gaap}}}}}
    if rows:
        out["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": rows}}}
    out["facts"].update(extra or {})
    return out


def instance(counts, prefix="xbrli", other_axis=None):
    """An XBRL instance with cover counts: [(member or None, as_of, shares)]."""
    ctx, fx = [], []
    for i, (member, as_of, shares) in enumerate(counts):
        seg = (f'<{prefix}:segment><xbrldi:explicitMember dimension="{other_axis or "us-gaap:StatementClassOfStockAxis"}">'
               f'{member}</xbrldi:explicitMember></{prefix}:segment>') if member else ""
        ctx.append(f'<{prefix}:context id="c{i}"><{prefix}:entity><{prefix}:identifier scheme="x">1</{prefix}:identifier>'
                   f'{seg}</{prefix}:entity><{prefix}:period><{prefix}:instant>{as_of}</{prefix}:instant>'
                   f'</{prefix}:period></{prefix}:context>')
        fx.append(f'<dei:EntityCommonStockSharesOutstanding contextRef="c{i}" unitRef="shares" decimals="INF">'
                  f'{shares}</dei:EntityCommonStockSharesOutstanding>')
    return ("<?xml version='1.0'?><xbrl>" + "".join(ctx) + "".join(fx) + "</xbrl>").encode()


class Web:
    """SEC archive fake: accession -> instance bytes."""

    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def __call__(self, url, limit=None):
        self.calls.append(url)
        acc = url.split("/")[-2]
        acc = f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"
        if url.endswith("index.json"):
            if acc not in self.docs:
                raise OSError("404")
            return json.dumps({"directory": {"item": [{"name": "FilingSummary.xml"}, {"name": "R2.xml"},
                                                      {"name": "x-20260630_htm.xml"}, {"name": "x.xsd"}]}}).encode()
        return self.docs[acc]


def acc(n):
    return f"0001045810-26-{n:06d}"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.quote = None
        self.splits = []
        self.web = Web({})
        self.facts = None
        self.today = date(2026, 10, 21)
        self.config()

    def config(self):
        SH.configure(data_dir=self.tmp, facts_fn=lambda s: self.facts, fetch_fn=self.web,
                     splits_fn=lambda s: self.splits, quote_fn=lambda s: self.quote,
                     today_fn=lambda: self.today)


class SingleClass(Base):
    def test_cover_counts_from_company_facts_need_no_filing_reads(self):
        self.facts = facts(dei=[("2026-07-17", 14.6e9, acc(3), "10-Q", "2026-08-01"),
                                ("2026-04-17", 14.7e9, acc(2), "10-Q", "2026-05-01"),
                                ("2026-01-16", 14.8e9, acc(1), "10-Q", "2026-01-30")],
                           filings=[(acc(1), "10-Q", "2026-01-30"), (acc(2), "10-Q", "2026-05-01"),
                                    (acc(3), "10-Q", "2026-08-01")])
        self.web.docs = {acc(3): instance([(None, "2026-07-17", int(14.6e9))])}
        r = SH.build("aapl")
        self.assertEqual("ok", r["status"])
        self.assertEqual(["2026-01-16", "2026-04-17", "2026-07-17"], [p["as_of"] for p in r["points"]])
        self.assertEqual({"companyfacts"}, {p["source"] for p in r["points"]})
        self.assertEqual(14.6e9, r["latest"]["shares"])
        self.assertEqual(96, r["latest"]["age_days"])
        self.assertEqual({"from": "2026-01-16", "to": "2026-07-17"}, r["coverage"])
        reads = [u for u in self.web.calls if not u.endswith("index.json")]
        self.assertEqual(1, len(reads), "only the newest cover, to check for share classes")

    def test_one_point_per_date_the_first_filing_to_state_it(self):
        self.facts = facts(dei=[("2026-02-10", 100.0, acc(2), "10-K/A", "2026-04-30"),
                                ("2026-02-10", 99.0, acc(1), "10-K", "2026-02-20")],
                           filings=[(acc(1), "10-K", "2026-02-20"), (acc(2), "10-K/A", "2026-04-30")])
        r = SH.build("X")
        self.assertEqual([("2026-02-10", 99.0, "10-K")],
                         [(p["as_of"], p["shares"], p["form"]) for p in r["points"]])

    def test_no_filing_states_a_count(self):
        self.facts = facts(filings=[(acc(1), "10-Q", "2026-05-01")])
        self.web.docs = {acc(1): b"<xbrl></xbrl>"}
        r = SH.build("X")
        self.assertEqual("unsupported", r["status"])
        self.assertIn("state no share count", r["reason"])


class Splits(Base):
    """Prices are split-adjusted to today's basis; so are the counts."""

    def nvda(self, extra=()):
        return facts(dei=[("2024-02-14", 2.47e9, acc(1), "10-K", "2024-02-21"),
                          ("2024-05-15", 2.46e9, acc(2), "10-Q", "2024-05-29"),
                          ("2024-08-21", 24.53e9, acc(3), "10-Q", "2024-08-28")] + list(extra),
                     filings=[(acc(1), "10-K", "2024-02-21"), (acc(2), "10-Q", "2024-05-29"),
                              (acc(3), "10-Q", "2024-08-28")] + [(e[2], e[3], e[4]) for e in extra])

    def test_a_count_before_a_split_is_put_on_todays_basis(self):
        self.facts = self.nvda()
        self.splits = [("2024-06-10", 10.0)]
        r = SH.build("NVDA")
        p = {x["as_of"]: x for x in r["points"]}
        self.assertEqual(24.6e9, p["2024-05-15"]["shares"])
        self.assertEqual(2.46e9, p["2024-05-15"]["reported"], "the filed number is kept beside it")
        self.assertEqual(10.0, p["2024-05-15"]["split_factor"])
        self.assertEqual(1.0, p["2024-08-21"]["split_factor"])
        self.assertEqual([{"date": "2024-06-10", "ratio": 10.0}], r["splits"])
        self.assertEqual([], r["checks"]["big_moves"], "no step left at the split")

    def test_a_reverse_split(self):
        self.facts = facts(dei=[("2011-03-01", 1.2e9, acc(1), "10-K", "2011-03-15"),
                                ("2011-08-01", 0.121e9, acc(2), "10-Q", "2011-08-05")],
                           filings=[(acc(1), "10-K", "2011-03-15"), (acc(2), "10-Q", "2011-08-05")])
        self.splits = [("2011-05-20", 0.1)]
        r = SH.build("PLUG")
        self.assertAlmostEqual(0.12e9, r["points"][0]["shares"])

    def test_a_count_taken_after_the_shares_were_split_but_before_the_ex_date(self):
        # Shares are distributed the day before the ex-date: a cover dated
        # in between already counts the new shares.
        self.facts = self.nvda(extra=[("2024-06-07", 24.5e9, acc(4), "10-Q/A", "2024-06-20")])
        self.splits = [("2024-06-10", 10.0)]
        r = SH.build("NVDA")
        p = {x["as_of"]: x for x in r["points"]}
        self.assertEqual(24.5e9, p["2024-06-07"]["shares"])
        self.assertEqual(1.0, p["2024-06-07"]["split_factor"])
        self.assertIn("post-split", p["2024-06-07"]["basis_note"])

    def test_a_pre_split_count_in_those_days_still_takes_the_split(self):
        self.facts = self.nvda(extra=[("2024-06-03", 2.455e9, acc(4), "10-Q/A", "2024-06-05")])
        self.splits = [("2024-06-10", 10.0)]
        p = {x["as_of"]: x for x in SH.build("NVDA")["points"]}
        self.assertEqual(24.55e9, p["2024-06-03"]["shares"])

    def test_a_split_missing_from_the_price_data_is_named(self):
        self.facts = self.nvda()
        self.splits = []
        r = SH.build("NVDA")
        self.assertTrue(any(m["split_like"] for m in r["checks"]["big_moves"]))
        self.assertTrue(any("possibly a split" in w for w in r["warnings"]))

    def test_real_dilution_is_kept_and_listed(self):
        self.facts = facts(dei=[("2013-01-01", 100e6, acc(1), "10-K", "2013-03-01"),
                                ("2013-04-01", 167e6, acc(2), "10-Q", "2013-05-01")],
                           filings=[(acc(1), "10-K", "2013-03-01"), (acc(2), "10-Q", "2013-05-01")])
        r = SH.build("PLUG")
        self.assertEqual(167e6, r["points"][1]["shares"], "a real issue is a fact, not a basis change")
        self.assertEqual([{"from": "2013-01-01", "to": "2013-04-01", "change_pct": 67.0, "split_like": False}],
                         r["checks"]["big_moves"])
        self.assertFalse(any("possibly a split" in w for w in r["warnings"]))


class MultiClass(Base):
    """Company Facts drops per-class counts: the filings are read."""

    def setUp(self):
        super().setUp()
        self.facts = facts(filings=[(acc(1), "10-Q", "2026-04-30"), (acc(2), "10-Q", "2026-07-30")],
                           name="Meta Platforms, Inc.")
        self.web.docs = {
            acc(1): instance([("us-gaap:CommonClassAMember", "2026-04-24", 2196000000),
                              ("us-gaap:CommonClassBMember", "2026-04-24", 342400000)]),
            acc(2): instance([("us-gaap:CommonClassAMember", "2026-07-24", 2205128509),
                              ("us-gaap:CommonClassBMember", "2026-07-24", 342377716)]),
        }

    def test_classes_are_added_up(self):
        r = SH.build("META")
        self.assertEqual("ok", r["status"])
        last = r["points"][-1]
        self.assertEqual(2205128509 + 342377716, last["shares"])
        self.assertEqual("filing", last["source"])
        self.assertEqual(["Common Class A", "Common Class B"], [c["label"] for c in last["classes"]])
        self.assertTrue(any("Share classes counted together" in w for w in r["warnings"]))

    def test_a_filing_is_read_once_ever(self):
        SH.build("META")
        n = len(self.web.calls)
        self.config()                                 # a restart
        SH.build("META")
        self.assertEqual(n, len(self.web.calls), "covers are kept: filings never change")

    def test_a_filing_that_could_not_be_read_is_tried_again(self):
        self.web.docs.pop(acc(1))
        r = SH.build("META")
        self.assertEqual(1, r["checks"]["unread_filings"])
        self.assertTrue(r["partial"])
        self.assertTrue(any("could not be read" in w for w in r["warnings"]))
        self.web.docs[acc(1)] = instance([("us-gaap:CommonClassAMember", "2026-04-24", 2196000000)])
        self.assertEqual(2, len(SH.build("META")["points"]))

    def test_a_co_registrants_count_is_not_this_companys(self):
        doc = instance([(None, "2026-07-24", 500000000),
                        ("us-gaap:SubsidiaryMember", "2026-07-24", 1000)], other_axis="dei:LegalEntityAxis")
        self.assertEqual({"as_of": "2026-07-24", "shares": 500000000.0, "classes": None},
                         SH.parse_cover(doc.decode()))

    def test_unprefixed_instances_and_odd_whitespace(self):
        doc = instance([("us-gaap:CommonClassAMember", "2012-07-27", " 1,467,762,401 ")], prefix="xbrli")
        doc = doc.replace(b"xbrli:", b"")
        c = SH.parse_cover(doc.decode())
        self.assertEqual(1467762401.0, c["shares"])

    def test_summary_count_that_is_one_class_of_several(self):
        # Company Facts carries a count with no class, but the cover splits
        # it into classes adding up to more: every cover is read instead.
        self.facts = facts(dei=[("2026-07-24", 2205128509, acc(2), "10-Q", "2026-07-30"),
                                ("2026-04-24", 2196000000, acc(1), "10-Q", "2026-04-30")],
                           filings=[(acc(1), "10-Q", "2026-04-30"), (acc(2), "10-Q", "2026-07-30")])
        r = SH.build("META")
        self.assertEqual(2205128509 + 342377716, r["points"][-1]["shares"])
        self.assertEqual({"filing"}, {p["source"] for p in r["points"]})


class Berkshire(Base):
    def test_classes_with_fixed_economics_are_weighted(self):
        self.facts = facts(filings=[(acc(1), "10-Q", "2026-08-10")], name="BERKSHIRE HATHAWAY INC")
        self.web.docs = {acc(1): instance([("us-gaap:CommonClassAMember", "2026-07-29", 500000),
                                           ("us-gaap:CommonClassBMember", "2026-07-29", 1408000000)])}
        self.quote = {"price": 480.0, "market_cap": (500000 * 1500 + 1408000000) * 480.0}
        r = SH.build("BRK-B")
        self.assertEqual(500000 * 1500 + 1408000000, r["points"][-1]["shares"])
        self.assertEqual(0.0, r["checks"]["yahoo"]["diff_pct"])
        self.assertTrue(any("fixed conversion ratio" in w for w in r["warnings"]))
        a = SH.build("BRK-A")
        self.assertAlmostEqual(500000 + 1408000000 / 1500, a["points"][-1]["shares"], places=1)


class Unsupported(Base):
    def test_an_etf_has_no_sec_counts(self):
        self.facts = None
        r = SH.build("SPY")
        self.assertEqual("unsupported", r["status"])
        self.assertIn("ETFs", r["reason"])

    def test_a_foreign_filer(self):
        f = facts(name="Taiwan Semiconductor")
        f["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = [
            {"end": "2025-12-31", "val": 1, "accn": acc(1), "form": "20-F", "filed": "2026-04-10"}]
        self.facts = f
        r = SH.build("TSM")
        self.assertEqual("unsupported", r["status"])
        self.assertIn("foreign filer", r["reason"])

    def test_no_symbol_or_not_a_symbol(self):
        self.assertEqual("unsupported", SH.build("")["status"])
        for bad in ("../../etc/passwd", "A" * 20, "AAPL;rm", "<b>"):
            r = SH.get(bad)
            self.assertEqual("unsupported", r["status"], bad)
            self.assertEqual("Not a stock symbol.", r["reason"])
        self.assertEqual([], list(Path(self.tmp).rglob("*.json")), "nothing written for them")


class TodayVsYahoo(Base):
    def setUp(self):
        super().setUp()
        self.facts = facts(dei=[("2026-07-24", 384.2e6, acc(1), "10-Q", "2026-08-03")],
                           filings=[(acc(1), "10-Q", "2026-08-03")])
        self.web.docs = {acc(1): instance([(None, "2026-07-24", int(384.2e6))])}

    def test_agreement(self):
        self.quote = {"price": 100.0, "market_cap": 384.2e6 * 100.0}
        r = SH.build("X")
        self.assertEqual(0.0, r["checks"]["yahoo"]["diff_pct"])
        self.assertFalse(any("Yahoo" in w for w in r["warnings"]))

    def test_shares_issued_since_the_last_report(self):
        self.quote = {"price": 142.72, "market_cap": 60.4e9}
        r = SH.build("MSTR")
        y = r["checks"]["yahoo"]
        self.assertAlmostEqual(10.15, y["diff_pct"], places=1)
        w = next(w for w in r["warnings"] if "Yahoo" in w)
        self.assertIn("more than the 384.2M in the Jul 24, 2026 filing", w)
        self.assertIn("Shares issued since that report", w)
        self.assertIn("the chart uses the filed count", w)

    def test_no_quote_no_check(self):
        self.quote = None
        self.assertIsNone(SH.build("X")["checks"]["yahoo"])

    def test_a_stale_count_is_not_today(self):
        self.today = date(2027, 3, 1)
        r = SH.build("X")
        self.assertEqual(220, r["latest"]["age_days"])
        self.assertTrue(any("too old to stand for today" in w for w in r["warnings"]))


class SharesOn(unittest.TestCase):
    H = {"max_age_days": 200, "points": [
        {"as_of": "2024-05-15", "shares": 24.6e9, "form": "10-Q", "filed": "2024-05-29"},
        {"as_of": "2024-08-21", "shares": 24.53e9, "form": "10-Q", "filed": "2024-08-28"}]}

    def test_the_last_count_on_or_before_the_day(self):
        self.assertIsNone(SH.shares_on(self.H, "2024-05-14"), "before the first count: a gap")
        s = SH.shares_on(self.H, "2024-05-15")
        self.assertEqual((24.6e9, "2024-05-15", 0), (s["shares"], s["as_of"], s["age_days"]))
        s = SH.shares_on(self.H, "2024-08-20T12:00:00-04:00")
        self.assertEqual((24.6e9, 97), (s["shares"], s["age_days"]))
        self.assertEqual(24.53e9, SH.shares_on(self.H, "2024-08-21")["shares"])

    def test_too_old_is_a_gap_not_a_guess(self):
        self.assertIsNotNone(SH.shares_on(self.H, "2025-03-09"))
        self.assertIsNone(SH.shares_on(self.H, "2025-03-10"))

    def test_bars_become_market_cap(self):
        bars = [{"date": "2024-05-10", "open": 1, "high": 1, "low": 1, "close": 1},
                {"date": "2024-06-10", "open": 120.0, "high": 125.0, "low": 118.0, "close": 121.79}]
        out = SH.market_cap_bars(self.H, bars)
        self.assertIsNone(out[0])
        self.assertEqual(121.79 * 24.6e9, out[1]["close"])
        self.assertEqual(118.0 * 24.6e9, out[1]["low"])
        self.assertEqual("2024-05-15", out[1]["shares"]["as_of"])


class Serving(Base):
    def setUp(self):
        super().setUp()
        self.facts = facts(dei=[("2026-07-17", 14.6e9, acc(1), "10-Q", "2026-08-01")],
                           filings=[(acc(1), "10-Q", "2026-08-01")])
        self.web.docs = {acc(1): instance([(None, "2026-07-17", int(14.6e9))])}

    def test_built_once_then_served_from_the_cache(self):
        r = SH.get("AAPL", wait=5)
        self.assertEqual("ok", r["status"])
        self.facts = None
        self.assertEqual("ok", SH.get("AAPL")["status"], "cached")
        SH._RESULTS.clear()
        self.assertEqual("ok", SH.get("AAPL")["status"], "and kept on disk")

    def test_a_slow_build_answers_building_and_finishes_in_the_background(self):
        gate = threading.Event()
        slow = self.facts

        def facts_fn(s):
            gate.wait(5)
            return slow
        SH.configure(data_dir=self.tmp, facts_fn=facts_fn, fetch_fn=self.web, today_fn=lambda: self.today)
        r = SH.get("AAPL", wait=0.05)
        self.assertEqual("building", r["status"])
        self.assertIn("up to a minute", r["reason"])
        self.assertEqual("building", SH.get("AAPL", wait=0.05)["status"], "one build at a time")
        gate.set()
        deadline = time.time() + 5
        while time.time() < deadline and SH.get("AAPL", wait=0.2)["status"] != "ok":
            pass
        self.assertEqual("ok", SH.get("AAPL")["status"])

    def test_a_partial_history_is_rebuilt_soon(self):
        self.assertLess(SH.PARTIAL_TTL_S, SH.RESULT_TTL_S)


class Wiring(unittest.TestCase):
    SRC = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()

    def test_configured_with_the_apps_sources(self):
        cfg = self.SRC[self.SRC.index("_shist.configure("):]
        cfg = cfg[:cfg.index("\n    )\n")]
        for part in ("facts_fn=_shist_facts", "fetch_fn=_shist_fetch", "split_ratios", "_ticker_info"):
            self.assertIn(part, cfg)
        facts = self.SRC[self.SRC.index("def _shist_facts"):]
        facts = facts[:facts.index("\n\n\n")]
        self.assertIn("company_facts(", facts)
        self.assertIn("last_fetch_failed(", facts, "could not ask is raised, not None")
        fetch = self.SRC[self.SRC.index("def _shist_fetch"):]
        fetch = fetch[:fetch.index("\n\n\n")]
        self.assertIn("_sec_filings._fetch(", fetch, "SEC's polite, throttled client")
        self.assertIn("available()", fetch, "nothing fetched offline")

    def test_the_route(self):
        self.assertIn('parsed.path == "/api/share_history"', self.SRC)
        route = self.SRC[self.SRC.index('parsed.path == "/api/share_history"'):][:900]
        self.assertIn("_shist.get(", route)

    def test_split_ratios_come_with_the_split_dates(self):
        fn = self.SRC[self.SRC.index("def _gap_actions"):]
        fn = fn[:fn.index("\ndef ")]
        self.assertIn('"split_ratios"', fn)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Codex430(Base):
    """Codex, #430: a failure to read is not an answer."""

    def test_sec_unreachable_is_not_unsupported(self):
        def down(sym):
            raise OSError("SEC timed out")
        SH.configure(data_dir=self.tmp, facts_fn=down, fetch_fn=self.web, today_fn=lambda: self.today)
        r = SH.build("AAPL")
        self.assertEqual("unavailable", r["status"])
        self.assertTrue(r["partial"], "tried again in minutes, not hours")
        self.assertIn("could not be reached", r["reason"])
        SH.get("AAPL", wait=5)
        self.assertEqual([], list(Path(self.tmp).rglob("AAPL.json")), "not kept on disk")
        self.facts = facts(dei=[("2026-07-17", 14.6e9, acc(1), "10-Q", "2026-08-01")],
                           filings=[(acc(1), "10-Q", "2026-08-01")])
        self.web.docs = {acc(1): instance([(None, "2026-07-17", int(14.6e9))])}
        self.config()
        SH._RESULTS["AAPL"] = (time.time() - SH.PARTIAL_TTL_S - 1, r)
        self.assertEqual("ok", SH.get("AAPL", wait=5)["status"], "SEC back: the answer comes back")

    def test_a_failed_class_probe_is_not_proof_of_one_class(self):
        self.facts = facts(dei=[("2026-07-24", 2205128509, acc(2), "10-Q", "2026-07-30")],
                           filings=[(acc(2), "10-Q", "2026-07-30")])
        self.web.docs = {}                            # the newest cover cannot be read
        r = SH.build("META")
        self.assertEqual("ok", r["status"])
        self.assertTrue(r["partial"], "checked again soon")
        self.assertEqual(1, r["checks"]["unread_filings"])
        self.assertTrue(any("could not be read" in w for w in r["warnings"]))


class LastFetchFailed(unittest.TestCase):
    """fundamentals.last_fetch_failed: "could not ask" against "nothing there"."""

    def setUp(self):
        import fundamentals as F
        self.F = F
        self.saved = (F._fetch_facts, F._sec.cik_for, F.available)
        F._sec.cik_for = lambda s: 884394
        F.available = lambda: True
        F._MEM.clear()

    def tearDown(self):
        self.F._fetch_facts, self.F._sec.cik_for, self.F.available = self.saved
        self.F._FETCH_ERRORS.clear()
        self.F._MEM.clear()

    def test_a_404_is_nothing_there_and_a_timeout_is_could_not_ask(self):
        import urllib.error
        F = self.F

        def gone(cik):
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        F._fetch_facts = gone
        self.assertIsNone(F.company_facts("SPYX", max_age=0))
        self.assertFalse(F.last_fetch_failed("SPYX"))

        def slow(cik):
            raise TimeoutError("timed out")
        F._fetch_facts = slow
        self.assertIsNone(F.company_facts("ZZZZ", max_age=0))
        self.assertTrue(F.last_fetch_failed("zzzz"))
        F._fetch_facts = lambda cik: {"cik": cik, "facts": {"dei": {}}}
        self.assertIsNotNone(F.company_facts("ZZZZ", max_age=0))
        self.assertFalse(F.last_fetch_failed("ZZZZ"), "a good fetch clears it")


class Codex430b(Base):
    """Codex, #430 (second review)."""

    def test_berkshire_before_the_2010_b_split(self):
        # Class B split 50-for-1 on 2010-01-21 and Class A did not: before
        # it one A share converted into 30 B, after it into 1,500.
        self.facts = facts(filings=[(acc(1), "10-Q", "2009-11-06"), (acc(2), "10-Q", "2011-05-06")],
                           name="BERKSHIRE HATHAWAY INC")
        self.web.docs = {
            acc(1): instance([("us-gaap:CommonClassAMember", "2009-10-29", 1050000),
                              ("us-gaap:CommonClassBMember", "2009-10-29", 13800000)]),
            acc(2): instance([("us-gaap:CommonClassAMember", "2011-04-29", 940000),
                              ("us-gaap:CommonClassBMember", "2011-04-29", 1100000000)]),
        }
        self.splits = [("2010-01-21", 50.0)]
        b = {p["as_of"]: p["shares"] for p in SH.build("BRK-B")["points"]}
        self.assertEqual(1050000 * 1500 + 13800000 * 50, b["2009-10-29"], "in today's B shares")
        self.assertEqual(940000 * 1500 + 1100000000, b["2011-04-29"])
        self.splits = []                              # BRK-A never split
        a = {p["as_of"]: p["shares"] for p in SH.build("BRK-A")["points"]}
        self.assertAlmostEqual(1050000 + 13800000 / 30, a["2009-10-29"], places=2)
        self.assertAlmostEqual(940000 + 1100000000 / 1500, a["2011-04-29"], places=2)


class TickerMapDown(unittest.TestCase):
    """Codex, #430: a CIK lookup that could not be made is not "no CIK"."""

    def setUp(self):
        import fundamentals as F
        self.F = F
        self.saved = (F._sec.cik_for, F._sec.ticker_map_ready, F.available)
        F.available = lambda: True
        F._MEM.clear()

    def tearDown(self):
        self.F._sec.cik_for, self.F._sec.ticker_map_ready, self.F.available = self.saved
        self.F._FETCH_ERRORS.clear()
        self.F._MEM.clear()

    def test_no_ticker_map_is_could_not_ask(self):
        F = self.F
        F._sec.cik_for = lambda s: None
        F._sec.ticker_map_ready = lambda: False
        self.assertIsNone(F.company_facts("QQQQ", max_age=0))
        self.assertTrue(F.last_fetch_failed("QQQQ"))
        F._sec.ticker_map_ready = lambda: True        # the map is there and has no such ticker
        self.assertIsNone(F.company_facts("QQQQ", max_age=0))
        self.assertFalse(F.last_fetch_failed("QQQQ"))
