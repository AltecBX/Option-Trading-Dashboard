"""Tests for share_history.py: shares outstanding from SEC cover pages (v5.45)."""

import json
import tempfile
import threading
import time
import unittest
from datetime import date, datetime
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

    def tearDown(self):
        SH._clock = time.time

    def config(self):
        # One clock for this thread and the background build: a shifted
        # clock reaches only the calling thread (test_time_travel).
        SH._clock = lambda: 1_800_000_000.0
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
        for part in ("facts_fn=_shist_facts", "fetch_fn=_shist_fetch", "splits_fn=_shist_splits", "_ticker_info"):
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
        SH._RESULTS["AAPL"] = (SH._clock() - SH.PARTIAL_TTL_S - 1, r)
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
        # Never the app's own cache: a test's fake facts must not be read
        # back by the next run, or by the app.
        self.saved_dir = F._DATA_DIR
        F._DATA_DIR = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.F._fetch_facts, self.F._sec.cik_for, self.F.available = self.saved
        self.F._FETCH_ERRORS.clear()
        self.F._MEM.clear()
        self.F._DATA_DIR = self.saved_dir

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
        # Never the app's own cache: a test's fake facts must not be read
        # back by the next run, or by the app.
        self.saved_dir = F._DATA_DIR
        F._DATA_DIR = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.F._sec.cik_for, self.F._sec.ticker_map_ready, self.F.available = self.saved
        self.F._FETCH_ERRORS.clear()
        self.F._MEM.clear()
        self.F._DATA_DIR = self.saved_dir

    def test_no_ticker_map_is_could_not_ask(self):
        F = self.F
        F._sec.cik_for = lambda s: None
        F._sec.ticker_map_ready = lambda: False
        self.assertIsNone(F.company_facts("QQQQ", max_age=0))
        self.assertTrue(F.last_fetch_failed("QQQQ"))
        F._sec.ticker_map_ready = lambda: True        # the map is there and has no such ticker
        self.assertIsNone(F.company_facts("QQQQ", max_age=0))
        self.assertFalse(F.last_fetch_failed("QQQQ"))


class StaleTickerMap(unittest.TestCase):
    """Codex, #430: SEC's ticker map is a week old and its refresh fails."""

    def setUp(self):
        import sec_filings as SF
        self.SF = SF
        self.saved = (list(SF._TICKERS), SF._CIK_FN, SF.available, SF._fetch_json,
                      list(getattr(SF, "_TICKERS_FAILED", [False])))
        SF._CIK_FN = None
        SF.available = lambda: True
        SF._TICKERS[:] = [time.time() - SF._TICKER_TTL - 60, {"AAPL": 320193}]

    def tearDown(self):
        SF = self.SF
        SF._TICKERS[:], SF._CIK_FN, SF.available, SF._fetch_json, failed = self.saved
        if hasattr(SF, "_TICKERS_FAILED"):
            SF._TICKERS_FAILED[:] = failed

    def test_the_old_map_still_answers_and_is_not_proof_of_absence(self):
        SF = self.SF

        def down(url):
            raise TimeoutError("SEC timed out")
        SF._fetch_json = down
        self.assertEqual(320193, SF.cik_for("AAPL"), "a stale map still knows Apple")
        self.assertFalse(SF.ticker_map_ready(), "the refresh failed: absence proves nothing")
        self.assertIsNone(SF.cik_for("NEWCO"))
        self.assertFalse(SF.ticker_map_ready())
        SF._fetch_json = lambda url: {"0": {"ticker": "NEWCO", "cik_str": 99}}
        self.assertEqual(99, SF.cik_for("NEWCO"))
        self.assertTrue(SF.ticker_map_ready())


class Codex430c(Base):
    """Codex, #430 (fourth review): a lookup that failed is not an answer."""

    def test_no_split_history_is_not_no_splits(self):
        self.facts = facts(dei=[("2024-05-15", 2.46e9, acc(1), "10-Q", "2024-05-29")],
                           filings=[(acc(1), "10-Q", "2024-05-29")])
        self.web.docs = {acc(1): instance([(None, "2024-05-15", int(2.46e9))])}

        def throttled(sym):
            raise OSError("Yahoo said 429")
        SH.configure(data_dir=self.tmp, facts_fn=lambda s: self.facts, fetch_fn=self.web,
                     splits_fn=throttled, today_fn=lambda: self.today)
        r = SH.build("NVDA")
        self.assertEqual("unavailable", r["status"], "counts on a mix of share bases are not shown")
        self.assertTrue(r["partial"])
        self.assertIn("split history", r["reason"])
        SH.get("NVDA", wait=5)
        self.assertEqual([], list(Path(self.tmp).rglob("NVDA.json")))

    def test_the_server_says_when_it_could_not_ask(self):
        src = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()
        cfg = src[src.index("_shist.configure("):]
        cfg = cfg[:cfg.index("\n    )\n")]
        self.assertIn("splits_fn=_shist_splits", cfg)
        fn = src[src.index("def _shist_splits"):]
        fn = fn[:fn.index("\n\n\n")]
        self.assertIn('.get("ok")', fn, "a failed lookup raises rather than reading as no splits")
        facts = src[src.index("def _shist_facts"):]
        facts = facts[:facts.index("\n\n\n")]
        self.assertIn("available()", facts, "SEC switched off is could-not-ask too")


class Codex430d(Base):
    """Codex, #430 (fifth review): last good facts after a failed refresh."""

    def test_stale_facts_are_used_and_retried_soon(self):
        f = facts(dei=[("2026-07-17", 14.6e9, acc(1), "10-Q", "2026-08-01")],
                  filings=[(acc(1), "10-Q", "2026-08-01")])
        f["_fetched_ts"] = datetime(2026, 10, 19, 15, 0).timestamp()
        f["_stale"] = True
        self.facts = f
        self.web.docs = {acc(1): instance([(None, "2026-07-17", int(14.6e9))])}
        r = SH.build("AAPL")
        self.assertEqual("ok", r["status"], "the last good facts are still good")
        self.assertTrue(r["partial"], "and refreshed again soon")
        self.assertTrue(any("could not be refreshed" in w and "Oct 19, 2026" in w for w in r["warnings"]))

    def test_the_server_marks_them(self):
        src = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()
        fn = src[src.index("def _shist_facts"):]
        fn = fn[:fn.index("\n\n\n")]
        self.assertIn('_stale=True', fn)


class Codex430e(Base):
    """Codex, #430 (sixth review): a failed refresh keeps the last good history."""

    def setUp(self):
        super().setUp()
        self.facts = facts(dei=[("2026-07-17", 14.6e9, acc(1), "10-Q", "2026-08-01")],
                           filings=[(acc(1), "10-Q", "2026-08-01")])
        self.web.docs = {acc(1): instance([(None, "2026-07-17", int(14.6e9))])}

    def expire(self):
        ts, res = SH._RESULTS["AAPL"]
        SH._RESULTS["AAPL"] = (ts - SH.RESULT_TTL_S - 1, res)

    def test_sec_down_on_refresh(self):
        self.assertEqual("ok", SH.get("AAPL", wait=5)["status"])
        self.expire()

        def down(sym):
            raise OSError("SEC timed out")
        SH._FACTS_FN = down
        r = SH.get("AAPL", wait=5)
        self.assertEqual("ok", r["status"], "the last good history is still served")
        self.assertEqual(14.6e9, r["latest"]["shares"])
        self.assertTrue(r["partial"], "and retried soon")
        self.assertTrue(any("refresh just failed" in w for w in r["warnings"]))
        self.expire()
        r = SH.get("AAPL", wait=5)
        self.assertEqual(1, sum("refresh just failed" in w for w in r["warnings"]), "said once")

    def test_an_error_while_building_is_retried_soon(self):
        def broken(sym):
            raise RuntimeError("bug")
        saved = SH.build
        SH.build = broken
        try:
            r = SH.get("AAPL", wait=5)
        finally:
            SH.build = saved
        self.assertEqual("error", r["status"])
        self.assertTrue(r["partial"])


class PriceCheck(Base):
    """v5.46: the prices a market-cap chart multiplies are checked against
    Yahoo's unadjusted closes, one a month, carried in the history."""

    def setUp(self):
        super().setUp()
        self.facts = facts(dei=[("2026-07-17", 14.6e9, acc(1), "10-Q", "2026-08-01")],
                           filings=[(acc(1), "10-Q", "2026-08-01")])
        self.web.docs = {acc(1): instance([(None, "2026-07-17", int(14.6e9))])}
        self.refs = [("2026-06-01", 201.5), ("2026-07-01", 0), ("2026-08-03", "212.25"), ("2026-09-01", None)]

    def configure(self, prices_fn):
        SH.configure(data_dir=self.tmp, facts_fn=lambda s: self.facts, fetch_fn=self.web,
                     splits_fn=lambda s: self.splits, quote_fn=lambda s: self.quote,
                     today_fn=lambda: self.today, prices_fn=prices_fn)

    def test_the_closes_come_with_the_history(self):
        self.configure(lambda s: self.refs)
        r = SH.build("AAPL")
        self.assertEqual("ok", r["status"])
        self.assertFalse(r["partial"])
        self.assertEqual([["2026-06-01", 201.5], ["2026-08-03", 212.25]], r["price_check"]["closes"],
                         "only real prices")
        self.assertIn("not dividend-adjusted", r["price_check"]["source"])

    def test_closes_that_could_not_be_fetched_are_said_and_retried_soon(self):
        def throttled(sym):
            raise OSError("Yahoo said 429")
        self.configure(throttled)
        r = SH.build("AAPL")
        self.assertEqual("ok", r["status"], "the share counts are still good")
        self.assertIsNone(r["price_check"])
        self.assertTrue(r["partial"], "tried again in minutes")
        self.assertTrue(any("could not be checked" in w for w in r["warnings"]))

    def test_no_closes_is_the_same_as_could_not_fetch(self):
        self.configure(lambda s: [])
        r = SH.build("AAPL")
        self.assertIsNone(r["price_check"])
        self.assertTrue(r["partial"])

    def test_a_history_kept_on_disk_before_the_check_existed_is_rebuilt(self):
        self.configure(lambda s: self.refs)
        old = SH.build("AAPL")
        for k in ("price_check", "schema"):
            old.pop(k, None)
        path = Path(self.tmp) / "share_history" / "AAPL.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(old, _built_ts=SH._clock())))
        r = SH.get("AAPL", wait=5)
        self.assertEqual("ok", r["status"])
        self.assertTrue(r.get("price_check"), "rebuilt with the closes, not served without them")

    def test_the_server_passes_yahoos_unadjusted_closes(self):
        src = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()
        cfg = src[src.index("_shist.configure("):]
        cfg = cfg[:cfg.index("\n    )\n")]
        self.assertIn("prices_fn=_shist_prices", cfg)
        fn = src[src.index("def _shist_prices"):]
        fn = fn[:fn.index("\n\n\n")]
        self.assertIn('"ref_closes"', fn)
        self.assertIn('.get("ok")', fn, "a failed lookup raises rather than reading as no prices")


class FilingList(Base):
    """v5.46: SEC's Company Facts can miss a filing for months. Coca-Cola's
    10-Q of Jul 29, 2026 was not in it in October, nor were four other
    companies' from that week out of 36 sampled. The company's own filing
    list adds any 10-Q/10-K it lacks."""

    def setUp(self):
        super().setUp()
        self.facts = facts(dei=[("2026-04-28", 4.302e9, acc(1), "10-Q", "2026-04-30")],
                           filings=[(acc(1), "10-Q", "2026-04-30")])
        self.web.docs = {acc(1): instance([(None, "2026-04-28", int(4.302e9))]),
                         acc(2): instance([(None, "2026-07-24", int(4.299e9))])}
        self.listed = [{"form": "10-Q", "date": "2026-07-29", "accession": acc(2)},
                       {"form": "8-K", "date": "2026-07-21", "accession": acc(9)},
                       {"form": "10-Q", "date": "2026-04-30", "accession": acc(1)}]

    def configure(self, filings_fn):
        SH.configure(data_dir=self.tmp, facts_fn=lambda s: self.facts, fetch_fn=self.web,
                     splits_fn=lambda s: self.splits, quote_fn=lambda s: self.quote,
                     today_fn=lambda: self.today, filings_fn=filings_fn)

    def test_a_filing_company_facts_missed_is_read(self):
        self.configure(lambda s: self.listed)
        r = SH.build("KO")
        self.assertEqual("ok", r["status"])
        self.assertEqual(["2026-04-28", "2026-07-24"], [p["as_of"] for p in r["points"]])
        self.assertEqual(("filing", "10-Q", "2026-07-29"),
                         (r["points"][-1]["source"], r["points"][-1]["form"], r["points"][-1]["filed"]))
        self.assertEqual("2026-07-24", r["latest"]["as_of"])
        self.assertFalse(r["partial"])
        self.assertFalse(any(acc(9).replace("-", "") in u for u in self.web.calls), "only 10-Q and 10-K")

    def test_a_filing_list_that_could_not_be_had_is_said_and_retried_soon(self):
        def down(sym):
            raise OSError("SEC timed out")
        self.configure(down)
        r = SH.build("KO")
        self.assertEqual("ok", r["status"], "Company Facts' counts are still good")
        self.assertEqual(["2026-04-28"], [p["as_of"] for p in r["points"]])
        self.assertTrue(r["partial"])
        self.assertTrue(any("list of filings could not be read" in w for w in r["warnings"]))

    def test_the_server_passes_secs_filing_list(self):
        src = (Path(__file__).resolve().parent / "options_dashboard.py").read_text()
        cfg = src[src.index("_shist.configure("):]
        cfg = cfg[:cfg.index("\n    )\n")]
        self.assertIn("filings_fn=_shist_filings", cfg)
        fn = src[src.index("def _shist_filings"):]
        fn = fn[:fn.index("\n\n\n")]
        self.assertIn("_sec_filings.filings(", fn)
        self.assertIn("raise", fn, "an empty list is SEC not answering: every 10-Q filer has filings")


class FilingErrors(Base):
    """v5.46: Pentair's 10-Q for Apr 2, 2011 filed 146,546,358,546 shares,
    a thousand times the 146.5 million on either side of it. A count that
    jumps by 5x or more and comes straight back is a units error in the
    filing: it is left out, and said."""

    def dei(self, rows):
        self.facts = facts(dei=[(a, v, acc(i), "10-Q", f) for i, (a, v, f) in enumerate(rows, 1)],
                           filings=[(acc(i), "10-Q", f) for i, (a, v, f) in enumerate(rows, 1)])
        last = len(rows)
        self.web.docs = {acc(last): instance([(None, rows[-1][0], int(rows[-1][1]))])}

    def test_a_count_a_thousand_times_its_neighbours_is_left_out(self):
        self.dei([("2010-12-31", 146531286.89, "2011-02-23"), ("2011-04-02", 146546358546.0, "2011-04-26"),
                  ("2011-07-02", 147063072.81, "2011-07-26"), ("2011-10-01", 146764808.25, "2011-10-26")])
        r = SH.build("PNR")
        self.assertEqual(["2010-12-31", "2011-07-02", "2011-10-01"], [p["as_of"] for p in r["points"]])
        self.assertEqual([("2011-04-02", 146546358546.0)], [(x["as_of"], x["shares"]) for x in r["rejected"]])
        self.assertTrue(any("Apr 2, 2011" in w and "left out" in w for w in r["warnings"]))
        self.assertFalse(any("very large issue" in w for w in r["warnings"]), "not reported as a move as well")
        self.assertEqual(146531286.89, SH.shares_on(r, "2011-05-02")["shares"], "those days use the count before it")

    def test_a_real_jump_that_stays_is_kept_and_flagged(self):
        # Tyco Flow Control's merger doubled Pentair's count in 2012, and a
        # 6x issue that stays is a 6x issue, however unusual.
        self.dei([("2012-06-30", 98.0e6, "2012-07-24"), ("2012-09-29", 600.0e6, "2012-10-23"),
                  ("2012-12-31", 601.0e6, "2013-02-22")])
        r = SH.build("PNR")
        self.assertEqual(3, len(r["points"]))
        self.assertEqual([], r["rejected"])
        self.assertTrue(any("very large issue" in w for w in r["warnings"]))

    def test_the_newest_count_cannot_be_judged_and_is_kept(self):
        self.dei([("2026-01-02", 146.5e6, "2026-02-01"), ("2026-04-02", 146.5e9, "2026-04-26")])
        r = SH.build("PNR")
        self.assertEqual(2, len(r["points"]), "nothing after it says it was wrong")
        self.assertTrue(any("very large issue" in w for w in r["warnings"]))


class Codex431(Base):
    """Codex, #431: the filing list is asked when Company Facts has no
    reports at all, as for a company that listed months ago and whose first
    10-Q has not reached Company Facts (it holds the S-1's facts only)."""

    def setUp(self):
        super().setUp()
        self.facts = facts(filings=[(acc(5), "S-1", "2026-03-02")])
        self.web.docs = {acc(6): instance([(None, "2026-08-07", int(612e6))])}

    def configure(self, filings_fn):
        SH.configure(data_dir=self.tmp, facts_fn=lambda s: self.facts, fetch_fn=self.web,
                     splits_fn=lambda s: self.splits, quote_fn=lambda s: self.quote,
                     today_fn=lambda: self.today, filings_fn=filings_fn)

    def test_a_first_10q_company_facts_lacks_is_read(self):
        self.configure(lambda s: [{"form": "10-Q", "date": "2026-08-12", "accession": acc(6)},
                                  {"form": "S-1", "date": "2026-03-02", "accession": acc(5)}])
        r = SH.build("NEWCO")
        self.assertEqual("ok", r["status"])
        self.assertEqual([("2026-08-07", 612e6, "filing")], [(p["as_of"], p["shares"], p["source"]) for p in r["points"]])

    def test_no_reports_and_no_list_is_could_not_ask(self):
        def down(sym):
            raise OSError("SEC timed out")
        self.configure(down)
        r = SH.build("NEWCO")
        self.assertEqual("unavailable", r["status"], "not 'no reports' for 12 hours")
        self.assertTrue(r["partial"])

    def test_a_foreign_filer_is_still_unsupported(self):
        self.facts = facts(filings=[(acc(7), "20-F", "2026-04-01")])
        self.configure(lambda s: [{"form": "20-F", "date": "2026-04-01", "accession": acc(7)}])
        r = SH.build("TSM")
        self.assertEqual("unsupported", r["status"])
        self.assertIn("foreign filer", r["reason"])


class PriceBasis(unittest.TestCase):
    """v5.48: the server's own check of a price series against the history's
    unadjusted closes, the same rule the chart applies (market_cap.js), for
    the credit monitor's past market caps."""

    def bars(self, fn=lambda px, i: px):
        out = []
        for m in range(1, 13):
            out.append({"date": f"2025-{m:02d}-02T12:00:00-04:00", "close": fn(100.0 + m, m)})
        out.append({"date": "2025-12-20T12:00:00-04:00", "close": 120.0})
        return out

    def hist(self):
        return {"price_check": {"closes": [[f"2025-{m:02d}-02", 100.0 + m] for m in range(1, 13)]}}

    def test_the_same_closes_agree(self):
        r = SH.price_basis(self.hist(), self.bars())
        self.assertEqual(("ok", 12, 0), (r["status"], r["checked"], r["bad"]))

    def test_dividend_adjusted_closes_do_not(self):
        r = SH.price_basis(self.hist(), self.bars(lambda px, m: px * (1 - 0.003 * (12 - m))))
        self.assertEqual("mismatch", r["status"])
        self.assertEqual("2025-01-02", r["worst"]["date"])

    def test_one_bad_print_in_a_dozen_is_not_a_basis(self):
        r = SH.price_basis(self.hist(), self.bars(lambda px, m: px * 1.5 if m == 5 else px))
        self.assertEqual(("ok", 1), (r["status"], r["bad"]))

    def test_the_newest_week_is_not_compared(self):
        bars = [{"date": "2025-12-10", "close": 50.0}, {"date": "2025-12-16", "close": 60.0}]
        r = SH.price_basis({"price_check": {"closes": [["2025-12-10", 100.0]]}}, bars)
        self.assertEqual("unchecked", r["status"])

    def test_nothing_to_compare_is_unchecked(self):
        self.assertEqual("unchecked", SH.price_basis({}, self.bars())["status"])


class ExpiresIn(Base):
    """v5.48 (Codex, #433): how long until a kept history is rebuilt, for a
    caller holding a partial one."""

    def setUp(self):
        super().setUp()
        self.facts = facts(dei=[("2026-07-17", 14.6e9, acc(1), "10-Q", "2026-08-01")],
                           filings=[(acc(1), "10-Q", "2026-08-01")])
        self.web.docs = {acc(1): instance([(None, "2026-07-17", int(14.6e9))])}

    def test_nothing_kept_is_none(self):
        self.assertIsNone(SH.expires_in("AAPL"))

    def test_a_whole_history_has_its_twelve_hours(self):
        SH.get("AAPL", wait=5)
        self.assertAlmostEqual(SH.RESULT_TTL_S, SH.expires_in("AAPL"), delta=1)

    def test_a_partial_one_has_what_is_left_of_its_ten_minutes(self):
        SH.get("AAPL", wait=5)
        ts, res = SH._RESULTS["AAPL"]
        SH._RESULTS["AAPL"] = (ts - 590, dict(res, partial=True))
        self.assertAlmostEqual(10, SH.expires_in("AAPL"), delta=1)
        SH._RESULTS["AAPL"] = (ts - 900, dict(res, partial=True))
        self.assertEqual(0, SH.expires_in("AAPL"), "overdue is now, not negative")
