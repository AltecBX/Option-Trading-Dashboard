"""Guards for hf_obs.py — the raw observation log and the document stamps.

The log is the one part of this system that is never rewritten, so a mistake
in it is permanent. These guards are weighted accordingly:

  1. The RULES: two dates, an evidence class that cannot name a fund, a
     quality that must be one of the seven, no zero-fill.
  2. APPEND ONLY: a second write never touches the first, and a month's file
     holds only that month.
  3. The SCHEMA STAMP: an unknown schema is refused, not guessed at, and an
     unstamped document written before stamping is still read.
  4. The WIRING: a build writes the log, and a log that cannot be written
     never costs the board.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import hf_health as HL
import hf_obs as OBS
import hf_pulse as P
import hf_routes as RT
import hf_scan as SC
import hf_sources as S
import test_hf_scan as F

AT = "2026-09-06T12:00:00+00:00"


def stay_offline(case):
    """Hold JERRY_NO_NET=1 for the whole test, whatever the caller set.

    Not just tidiness. `hf_watch.snapshot()` kicks a real EDGAR sweep in a
    daemon thread when the flag is absent, and the routes test calls it. A
    test whose hermeticity depends on how it was launched is not hermetic —
    running this file directly must be as offline as CI is."""
    was = os.environ.get("JERRY_NO_NET")
    os.environ["JERRY_NO_NET"] = "1"

    def restore():
        if was is None:
            os.environ.pop("JERRY_NO_NET", None)
        else:
            os.environ["JERRY_NO_NET"] = was
    case.addCleanup(restore)


@contextlib.contextmanager
def offline_fixtures():
    """Lift JERRY_NO_NET for the fake transport, and put it straight back.

    The providers refuse to fetch while the flag is set, so a fixture-driven
    build needs it lifted — but `hf_watch.snapshot()` ALSO reads it, and with
    the flag gone it kicks a real EDGAR sweep in a daemon thread. CI caught
    that as `OSError: Directory not empty` when the temporary store was torn
    down underneath a thread still writing fund records into it. The deeper
    problem was worse than the race: a test that clears the flag for its whole
    run can reach the real network, which is the one thing JERRY_NO_NET
    exists to prevent. So the window is exactly the calls that need it."""
    was = os.environ.pop("JERRY_NO_NET", None)
    try:
        yield
    finally:
        if was is not None:
            os.environ["JERRY_NO_NET"] = was


class TheRecordKeepsItsRules(unittest.TestCase):
    def test_a_reading_carries_both_dates_and_a_stamp(self):
        r = OBS.observation("cftc.tff", S.REGULATORY, "lev_net", -330000,
                            observed_at=AT, as_of="2026-09-01",
                            public_on="2026-09-04", market="sp500",
                            prev=-318000, units="contracts")
        self.assertEqual(r["schema"], OBS.SCHEMA)
        self.assertEqual(r["as_of"], "2026-09-01")
        self.assertEqual(r["public_on"], "2026-09-04")
        self.assertEqual(r["change"], -12000)
        self.assertEqual(r["quality"], OBS.OK)
        self.assertIn("hf_obs", r["engine"])

    def test_an_anonymous_class_can_never_name_a_fund(self):
        for cls in (S.REGULATORY, S.FLOW_PROXY, S.PRIME_BROKER, S.INFERENCE):
            with self.assertRaises(ValueError):
                OBS.observation("cftc.tff", cls, "lev_net", 1, observed_at=AT,
                                fund="Citadel Advisors")
        # The one class that may: a filing IS the fund's own activity.
        r = OBS.observation("edgar.13f", S.VERIFIED, "value", 1, observed_at=AT,
                            fund="Pershing Square")
        self.assertEqual(r["fund"], "Pershing Square")

    def test_an_unknown_class_or_quality_is_refused(self):
        with self.assertRaises(ValueError):
            OBS.observation("x", "SOMETHING NEW", "m", 1, observed_at=AT)
        with self.assertRaises(ValueError):
            OBS.observation("x", S.REGULATORY, "m", 1, observed_at=AT, quality="FINE")
        with self.assertRaises(ValueError):
            OBS.observation("x", S.REGULATORY, "m", 1, observed_at="")

    def test_a_change_is_only_computed_from_two_numbers(self):
        # A direction ("-1" as a string) or a missing previous reading must not
        # silently become an arithmetic result.
        self.assertIsNone(OBS.observation("x", S.REGULATORY, "m", 5,
                                          observed_at=AT)["change"])
        self.assertIsNone(OBS.observation("x", S.REGULATORY, "m", "up",
                                          observed_at=AT, prev="down")["change"])
        self.assertIsNone(OBS.observation("x", S.REGULATORY, "m", True,
                                          observed_at=AT, prev=False)["change"])
        self.assertEqual(OBS.observation("x", S.REGULATORY, "m", 5, observed_at=AT,
                                         prev=2)["change"], 3)

    def test_a_health_line_is_about_the_source_not_the_market(self):
        h = OBS.health("uw.sector_tide", OBS.UNAVAILABLE, observed_at=AT,
                       evidence_class=S.FLOW_PROXY, note="returned nothing")
        self.assertEqual(h["metric"], OBS.HEALTH)
        self.assertEqual(h["quality"], OBS.UNAVAILABLE)
        # A provider that said nothing must not read as a measurement of zero.
        self.assertIsNone(h["value"])


class TheLogOnlyEverAppends(unittest.TestCase):
    def setUp(self):
        stay_offline(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        OBS.configure(data_dir=self.tmp.name)

    def _rec(self, at=AT, metric="lev_net", value=1, source="cftc.tff"):
        return OBS.observation(source, S.REGULATORY, metric, value, observed_at=at)

    def test_a_second_write_leaves_the_first_alone(self):
        self.assertEqual(OBS.append([self._rec(value=1)]), 1)
        first = Path(self.tmp.name, "hf", "obs", "2026-09.jsonl").read_text()
        self.assertEqual(OBS.append([self._rec(value=2)]), 1)
        after = Path(self.tmp.name, "hf", "obs", "2026-09.jsonl").read_text()
        self.assertTrue(after.startswith(first), "the first line was rewritten")
        self.assertEqual(len(OBS.read()), 2)

    def test_each_month_gets_its_own_file(self):
        OBS.append([self._rec(at="2026-09-06T12:00:00+00:00"),
                    self._rec(at="2026-10-01T09:00:00+00:00"),
                    self._rec(at="2026-10-31T23:59:59+00:00")])
        self.assertEqual(OBS.months(), ["2026-09", "2026-10"])
        self.assertEqual(len(OBS.read(since="2026-10")), 2)

    def test_reading_filters_without_opening_every_month(self):
        OBS.append([self._rec(at="2026-08-01T00:00:00+00:00", source="a"),
                    self._rec(at="2026-09-01T00:00:00+00:00", source="b"),
                    self._rec(at="2026-09-02T00:00:00+00:00", metric="lev_gross",
                              source="b")])
        self.assertEqual([r["source"] for r in OBS.read(source="b")], ["b", "b"])
        self.assertEqual([r["metric"] for r in OBS.read(metric="lev_gross")],
                         ["lev_gross"])
        self.assertEqual(len(OBS.read(until="2026-08-31")), 1)

    def test_a_line_from_a_future_schema_is_skipped_and_counted(self):
        OBS.append([self._rec()])
        p = Path(self.tmp.name, "hf", "obs", "2026-09.jsonl")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"schema": OBS.SCHEMA + 1, "observed_at": AT,
                                 "source": "cftc.tff", "metric": "lev_net",
                                 "value": 9}) + "\n")
        self.assertEqual(len(OBS.read()), 1, "a schema this build cannot read was used")
        st = OBS.stats()
        self.assertEqual(st["n_lines"], 2)
        self.assertEqual(st["n_unreadable"], 1)

    def test_a_corrupt_line_costs_only_itself(self):
        OBS.append([self._rec()])
        p = Path(self.tmp.name, "hf", "obs", "2026-09.jsonl")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
        OBS.append([self._rec(value=3)])
        self.assertEqual(len(OBS.read()), 2)

    def test_with_no_directory_configured_nothing_is_written(self):
        OBS.configure(data_dir=None)
        self.assertEqual(OBS.append([self._rec()]), 0)
        self.assertFalse(OBS.stats()["available"])


class TheStampSaysWhatEachDocumentIs(unittest.TestCase):
    def test_a_stamp_names_the_kind_the_schema_and_the_engine(self):
        d = OBS.stamp({"week": "2026-W36"}, "pulse", engine="hf_scan 1.2.0",
                      created_at=AT)
        self.assertEqual(d["doc"], "pulse")
        self.assertEqual(d["doc_schema"], OBS.DOC_SCHEMAS["pulse"])
        self.assertEqual(d["doc_engine"], "hf_scan 1.2.0")
        self.assertEqual(d["created_at"], AT)
        self.assertEqual(d["week"], "2026-W36", "stamping changed the document")

    def test_an_undeclared_kind_is_refused(self):
        with self.assertRaises(ValueError):
            OBS.stamp({}, "something_new", engine="x", created_at=AT)

    def test_all_seven_persisted_kinds_are_declared(self):
        self.assertEqual(set(OBS.DOC_SCHEMAS),
                         {"pulse", "report", "grades", "replay", "alerts",
                          "shvol", "fund"})

    def test_an_unstamped_document_is_still_read(self):
        # Everything on disk today is unstamped. Refusing it would throw away
        # history to enforce a rule about the future.
        self.assertTrue(OBS.accept({"week": "2026-W36"}, "pulse"))
        self.assertIsNone(OBS.why_refused({"week": "2026-W36"}, "pulse"))

    def test_a_document_from_a_future_schema_is_refused_with_a_reason(self):
        d = {"doc_schema": OBS.DOC_SCHEMAS["pulse"] + 1}
        self.assertFalse(OBS.accept(d, "pulse"))
        self.assertIn("schema", OBS.why_refused(d, "pulse"))
        self.assertFalse(OBS.accept("not a document", "pulse"))


class TheBoardWritesTheLog(unittest.TestCase):
    """The wiring, against the same offline fixtures the rest of the suite
    uses. `_MEM` is cleared either side so a cached body cannot leak between
    tests."""

    def setUp(self):
        stay_offline(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = F.FakeWeb()
        S._MEM.clear()                                          # noqa: SLF001
        S.configure(fetch_fn=self.web.fetch, post_fn=self.web.post,
                    data_dir=self.tmp.name, now_fn=lambda: F.NOW)
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: F.NOW,
                     funds_fn=lambda: {"managers": [], "new_filings": []},
                     sector_fn=lambda s: {"AAPL": "Technology"}.get(s))
        self.addCleanup(lambda: S.configure(fetch_fn=None, post_fn=None))

    def test_a_build_writes_the_raw_readings_and_says_how_many(self):
        with offline_fixtures():
            board = SC.build()
        recs = OBS.read()
        self.assertGreater(board["observations_logged"], 20)
        self.assertEqual(len(recs), board["observations_logged"])
        # The CFTC readings the verdicts are built from are all there.
        cftc = [r for r in recs if r["source"] == "cftc.tff" and r["metric"] == "lev_net"]
        self.assertEqual(len(cftc), board["sources"]["cftc_markets"])
        for r in cftc:
            self.assertTrue(r["as_of"] and r["public_on"], "a reading with one date")
            self.assertGreater(r["public_on"], r["as_of"],
                               "a report cannot be public before the day it describes")
            self.assertEqual(r["units"], "contracts")

    def test_the_two_legs_are_kept_not_only_the_net(self):
        # A net that did not move can hide two legs that did, and no later
        # question can recover them once they are gone.
        with offline_fixtures():
            SC.build()
        metrics = {r["metric"] for r in OBS.read() if r["source"] == "cftc.tff"}
        self.assertLessEqual({"lev_net", "lev_gross", "lev_long", "lev_short",
                              "open_interest"}, metrics)

    def test_every_provider_writes_a_health_line_whether_or_not_it_answered(self):
        with offline_fixtures():
            SC.build()
        health = {r["source"]: r for r in OBS.read() if r["metric"] == OBS.HEALTH}
        self.assertEqual(set(health), {src for src, _ in SC.OBS_SOURCES.values()})
        # The fixtures answer for CFTC and say nothing for the paid channels.
        self.assertEqual(health["cftc.tff"]["quality"], OBS.OK)
        # No Unusual Whales key here, which is a DECISION and not a fault.
        self.assertEqual(health["uw.sector_tide"]["quality"], OBS.AUTH_MISSING)
        # A source that said nothing is not a reading of zero.
        self.assertIsNone(health["uw.sector_tide"]["value"])
        self.assertFalse([r for r in OBS.read()
                          if r["source"] == "uw.sector_tide" and r["metric"] != OBS.HEALTH])

    def test_a_reading_never_changes_evidence_class_on_the_way_in(self):
        # The log must agree with the engine about what kind of evidence each
        # provider is, or a later recomputation would weigh it differently
        # from the board that was actually shown.
        self.assertEqual(SC.OBS_SOURCES["cftc"][1], S.REGULATORY)
        self.assertEqual(SC.OBS_SOURCES["short_volume"][1], S.FLOW_PROXY)
        self.assertEqual(SC.OBS_SOURCES["etf_flows"][1], S.FLOW_PROXY)
        self.assertEqual(SC.OBS_SOURCES["press"][1], S.PRIME_BROKER)
        with offline_fixtures():
            SC.build()
        for r in OBS.read():
            self.assertIn(r["class"], S.EVIDENCE_CLASSES)
            self.assertNotIn("fund", r)

    def test_building_the_records_touches_no_file(self):
        with offline_fixtures():
            raw = SC.gather()
        before = sorted(Path(self.tmp.name).rglob("*"))
        recs = SC.observations(raw, AT)
        self.assertTrue(recs)
        self.assertEqual(sorted(Path(self.tmp.name).rglob("*")), before)

    def test_a_log_that_cannot_be_written_never_costs_the_board(self):
        def boom(_records):
            raise OSError("disk is full")
        real, OBS.append = OBS.append, boom
        try:
            with offline_fixtures():
                board = SC.build()
        finally:
            OBS.append = real
        self.assertEqual(board["observations_logged"], 0)
        self.assertTrue([u for u in board["unavailable"] if "Observation log" in u])
        self.assertTrue(board.get("questions"), "the board itself was lost")

    def test_the_stored_documents_carry_their_stamps(self):
        with offline_fixtures():
            SC.build()
            SC.build_report()
        pulse = json.loads(Path(self.tmp.name, "hf", "pulse",
                                f"{SC.week_key(F.NOW.date())}.json").read_text())
        self.assertEqual(pulse["doc"], "pulse")
        self.assertEqual(pulse["doc_schema"], OBS.DOC_SCHEMAS["pulse"])
        self.assertIn("hf_scan", pulse["doc_engine"])
        self.assertTrue(pulse["created_at"])

    def test_a_stored_board_from_a_future_schema_is_refused_not_guessed_at(self):
        with offline_fixtures():
            SC.build()
        p = Path(self.tmp.name, "hf", "pulse", f"{SC.week_key(F.NOW.date())}.json")
        doc = json.loads(p.read_text())
        doc["doc_schema"] = OBS.DOC_SCHEMAS["pulse"] + 1
        p.write_text(json.dumps(doc))
        self.assertIsNone(SC.snapshot_for(SC.week_key(F.NOW.date())))
        # The index is a SECOND document with its own stamp, checked where it
        # is read. Its rows are still this build's shape, so the summary
        # survives while the board itself is refused — which is the honest
        # answer, not a hole: nothing reinterprets the unreadable file.
        self.assertTrue(SC.history(), "the index row was thrown away too")
        idx = Path(self.tmp.name, "hf", "pulse", "index.jsonl")
        rows = [json.loads(ln) for ln in idx.read_text().splitlines() if ln.strip()]
        idx.write_text("".join(json.dumps({**r, "schema": OBS.SCHEMA + 1}) + "\n"
                               for r in rows))
        self.assertEqual(SC.history(), [],
                         "an index row from a schema this build cannot read was used")


if __name__ == "__main__":
    unittest.main()


class SourceHealthIsAQueryOverTheLog(unittest.TestCase):
    """Pure, so no store is needed: hand `hf_health` records and a moment."""

    NOW = "2026-09-06T12:00:00+00:00"

    def _health(self, source, quality, at, note=None):
        return OBS.health(source, quality, observed_at=at,
                          evidence_class=S.REGULATORY, note=note)

    def _reading(self, source, as_of, at, public_on=None):
        return OBS.observation(source, S.REGULATORY, "lev_net", 1, observed_at=at,
                               as_of=as_of, public_on=public_on or as_of)

    def test_a_source_that_answered_with_current_data_is_working(self):
        recs = [self._health("cftc.tff", OBS.OK, self.NOW),
                self._reading("cftc.tff", "2026-09-01", self.NOW)]
        r = HL.assess("cftc.tff", recs, self.NOW)
        self.assertEqual(r["state"], HL.HEALTHY)

    def test_a_source_that_answers_with_a_frozen_period_is_stale(self):
        # The failure that is invisible without both dates: it replies every
        # time, and the data it replies with stopped moving months ago.
        recs = [self._health("cftc.tff", OBS.OK, self.NOW),
                self._reading("cftc.tff", "2026-05-01", self.NOW)]
        r = HL.assess("cftc.tff", recs, self.NOW)
        self.assertEqual(r["state"], HL.STALE)
        self.assertIn("same old period", r["why"] + " same old period")

    def test_a_source_that_did_not_answer_is_down_with_how_long(self):
        recs = [self._health("cftc.tff", OBS.OK, "2026-09-01T12:00:00+00:00"),
                self._reading("cftc.tff", "2026-09-01", "2026-09-01T12:00:00+00:00"),
                self._health("cftc.tff", OBS.UNAVAILABLE, self.NOW, "returned nothing")]
        r = HL.assess("cftc.tff", recs, self.NOW)
        self.assertEqual(r["state"], HL.DOWN)
        self.assertIn("5 days", r["why"])

    def test_no_key_configured_is_a_decision_not_a_breakage(self):
        recs = [self._health("uw.sector_tide", OBS.AUTH_MISSING, self.NOW)]
        r = HL.assess("uw.sector_tide", recs, self.NOW)
        self.assertEqual(r["state"], HL.NOT_CONFIGURED)
        card = HL.card(recs, self.NOW, sources=["uw.sector_tide"])
        self.assertEqual(card["n_broken"], 0, "a missing key was reported as broken")

    def test_a_source_never_checked_is_not_reported_as_fine(self):
        r = HL.assess("cftc.tff", [], self.NOW)
        self.assertEqual(r["state"], HL.UNKNOWN)
        self.assertNotEqual(r["state"], HL.HEALTHY)

    def test_a_same_day_reading_is_not_half_a_day_stale(self):
        # A settlement dated today describes the whole day. Treating the date
        # as a midnight instant would make every same-day figure look stale.
        recs = [self._health("finra.short_volume", OBS.OK, self.NOW),
                self._reading("finra.short_volume", "2026-09-06", self.NOW)]
        self.assertEqual(HL.assess("finra.short_volume", recs, self.NOW)["state"],
                         HL.HEALTHY)

    def test_every_source_the_board_gathers_has_a_declared_cadence(self):
        # A source with no cadence can never be called stale, so a new
        # provider that skips this table would be silently unmonitored.
        self.assertEqual({src for src, _ in SC.OBS_SOURCES.values()},
                         set(HL.EXPECTED))
        for src, spec in HL.EXPECTED.items():
            self.assertIn(src, HL.LABELS)
            self.assertGreater(spec["every_hours"], 0)
            self.assertTrue(spec["cadence"])

    def test_the_worst_state_leads_the_card(self):
        recs = [self._health("cftc.tff", OBS.OK, self.NOW),
                self._reading("cftc.tff", "2026-09-01", self.NOW),
                self._health("ofr.form_pf", OBS.UNAVAILABLE, self.NOW, "no answer")]
        card = HL.card(recs, self.NOW, sources=["cftc.tff", "ofr.form_pf"])
        self.assertEqual(card["rows"][0]["state"], HL.DOWN)
        self.assertIn("Not answering", card["headline"])


class TheDailyViewSaysHowDeepItIs(unittest.TestCase):
    def setUp(self):
        stay_offline(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = F.FakeWeb()
        S._MEM.clear()                                          # noqa: SLF001
        S.configure(fetch_fn=self.web.fetch, post_fn=self.web.post,
                    data_dir=self.tmp.name, now_fn=lambda: F.NOW)
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: F.NOW,
                     funds_fn=lambda: {"managers": [], "new_filings": []})
        self.addCleanup(lambda: S.configure(fetch_fn=None, post_fn=None))

    def test_an_empty_record_says_so_instead_of_drawing_nothing(self):
        d = SC.daily()
        self.assertFalse(d["available"])
        self.assertIn("Nothing has been recorded", d["depth_note"])

    def test_a_young_record_says_how_young(self):
        with offline_fixtures():
            SC.build()
        d = SC.daily()
        self.assertTrue(d["rows"])
        self.assertIn("less than two days old", d["depth_note"])

    def test_two_reads_of_one_day_are_one_point_not_two(self):
        with offline_fixtures():
            SC.build()
            SC.build()
        d = SC.daily()
        row = [r for r in d["rows"] if r["metric"] == "lev_net"][0]
        self.assertEqual(row["n_days"], 1, "the same day was counted twice")
        self.assertEqual(len(row["points"]), 1)

    def test_an_undated_claim_stays_out_of_a_day_view(self):
        with offline_fixtures():
            SC.build()
        d = SC.daily()
        self.assertFalse([r for r in d["rows"] if r["metric"] == "prime_broker_claim"],
                         "a claim with no as_of was drawn as a daily reading")

    def test_source_health_reads_the_log_the_board_wrote(self):
        with offline_fixtures():
            SC.build()
        card = SC.source_health()
        by = {r["source"]: r for r in card["rows"]}
        self.assertEqual(by["cftc.tff"]["state"], HL.HEALTHY)
        self.assertEqual(by["uw.sector_tide"]["state"], HL.NOT_CONFIGURED)
        self.assertGreater(card["n_records"], 0)

    def test_the_health_card_carries_the_newest_date_each_source_gave(self):
        # The live board caught this and the unit tests did not. `source_health`
        # filtered the query to health lines only, so `assess` saw no readings:
        # every row came back with no newest date, and `hours_since_data` was
        # always None — which made the STALE branch unreachable in production
        # while the panel cheerfully said everything was working. The pure
        # module was right; the wiring around it threw half the record away.
        with offline_fixtures():
            SC.build()
        rows = {r["source"]: r for r in SC.source_health()["rows"]}
        for src in ("cftc.tff", "finra.short_interest", "finra.short_volume"):
            self.assertTrue(rows[src]["newest_as_of"],
                            f"{src} reported no newest date through the wiring")
            self.assertIsNotNone(rows[src]["hours_since_data"],
                                 f"{src} could never be judged stale")

    def test_a_source_frozen_in_the_past_reads_as_stale_through_the_wiring(self):
        # End to end, not against hf_health directly: a reading whose date
        # stopped moving must reach the panel AS stale.
        with offline_fixtures():
            SC.build()
        at = SC._now().isoformat(timespec="seconds")          # noqa: SLF001
        OBS.append([OBS.observation("cftc.tff", S.REGULATORY, "lev_net", 1,
                                    observed_at=at, as_of="2026-01-05",
                                    public_on="2026-01-08", market="sp500")])
        # Drop the current readings so only the frozen one is left for cftc.
        p = Path(self.tmp.name, "hf", "obs", "2026-09.jsonl")
        keep = [ln for ln in p.read_text().splitlines()
                if '"source":"cftc.tff"' not in ln or '"as_of":"2026-01-05"' in ln
                or '"metric":"health"' in ln]
        p.write_text("\n".join(keep) + "\n")
        row = {r["source"]: r for r in SC.source_health()["rows"]}["cftc.tff"]
        self.assertEqual(row["state"], HL.STALE, row["why"])
        self.assertIn("old", row["why"])

    def test_with_no_log_health_says_nothing_was_checked_not_all_is_well(self):
        card = SC.source_health()
        self.assertEqual(card["n_broken"], 0)
        self.assertEqual({r["state"] for r in card["rows"]}, {HL.UNKNOWN})
        self.assertIn("No source has been checked yet", card["headline"])


class TheBoardKeepsEveryBuild(unittest.TestCase):
    def setUp(self):
        stay_offline(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = F.FakeWeb()
        S._MEM.clear()                                          # noqa: SLF001
        S.configure(fetch_fn=self.web.fetch, post_fn=self.web.post,
                    data_dir=self.tmp.name, now_fn=lambda: F.NOW)
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: F.NOW,
                     funds_fn=lambda: {"managers": [], "new_filings": []})
        self.addCleanup(lambda: S.configure(fetch_fn=None, post_fn=None))

    def test_three_builds_of_one_week_leave_three_revisions(self):
        # The overwrite used to destroy this: a week rebuilt fourteen times
        # kept only the last, so a mid-week reversal vanished.
        with offline_fixtures():
            for _ in range(3):
                SC.build()
        week = SC.week_key(F.NOW.date())
        self.assertEqual(len(SC.revisions(week)), 3)
        self.assertEqual(len(SC.history()), 1, "history shows one row per week")

    def test_history_reads_the_index_not_four_hundred_boards(self):
        with offline_fixtures():
            SC.build()
        idx = Path(self.tmp.name, "hf", "pulse", "index.jsonl")
        self.assertTrue(idx.exists())
        # Break every stored board. If history() were still opening them it
        # would come back empty; reading the index, it does not.
        for p in Path(self.tmp.name, "hf", "pulse").glob("*.json"):
            p.write_text("{ not json")
        self.assertEqual(len(SC.history()), 1)

    def test_a_store_written_before_the_index_still_reads(self):
        with offline_fixtures():
            SC.build()
        Path(self.tmp.name, "hf", "pulse", "index.jsonl").unlink()
        rows = SC.history()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["verdicts"]["exposure"])

    def test_reindexing_an_old_store_appends_without_duplicating(self):
        with offline_fixtures():
            SC.build()
        Path(self.tmp.name, "hf", "pulse", "index.jsonl").unlink()
        self.assertEqual(SC.rebuild_index()["added"], 1)
        self.assertEqual(SC.rebuild_index()["added"], 0, "a board was indexed twice")

    def test_a_week_whose_file_is_gone_is_not_still_history(self):
        with offline_fixtures():
            SC.build()
        week = SC.week_key(F.NOW.date())
        Path(self.tmp.name, "hf", "pulse", f"{week}.json").unlink()
        self.assertEqual(SC.history(), [])
        # The revision rows survive, because nothing here is ever rewritten.
        self.assertEqual(len(SC.revisions(week)), 1)


class TheAlertLayerHasItsOwnClock(unittest.TestCase):
    def setUp(self):
        stay_offline(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = F.FakeWeb()
        S._MEM.clear()                                          # noqa: SLF001
        S.configure(fetch_fn=self.web.fetch, post_fn=self.web.post,
                    data_dir=self.tmp.name, now_fn=lambda: F.NOW)
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: F.NOW,
                     funds_fn=lambda: {"managers": [], "new_filings": []},
                     push_ready_fn=lambda: False)
        self.addCleanup(lambda: S.configure(fetch_fn=None, post_fn=None))

    def test_it_can_be_asked_to_look_without_waiting_for_the_pulse(self):
        with offline_fixtures():
            SC.build()
        out = SC.alerts_check()
        self.assertTrue(out["ok"])
        self.assertTrue(out["checked"])

    def test_the_watch_sweep_is_given_a_way_to_say_a_filing_landed(self):
        # Injected, never imported: hf_watch must not import hf_scan.
        import hf_watch as HW
        import inspect
        self.assertIn("after_sweep_fn", inspect.signature(HW.configure).parameters)
        src = Path(__file__).resolve().parent.joinpath("hf_watch.py").read_text()
        self.assertNotIn("import hf_scan", src)
        self.assertIn("_AFTER_SWEEP_FN()", src)

    def test_a_listener_that_throws_does_not_fail_the_sweep(self):
        import hf_watch as HW
        src = Path(__file__).resolve().parent.joinpath("hf_watch.py").read_text()
        i = src.index("if _AFTER_SWEEP_FN is not None:")
        self.assertIn("except Exception", src[i:i + 400])

    def test_switched_off_means_it_says_so_rather_than_looking(self):
        real = SC._alert_knob                                    # noqa: SLF001
        SC._alert_knob = lambda name: False if name == "enabled" else real(name)  # noqa: SLF001
        try:
            out = SC.alerts_check()
        finally:
            SC._alert_knob = real                                # noqa: SLF001
        self.assertFalse(out["checked"])
        self.assertIn("switched off", out["note"])


class TheConfidenceClaimIsHonest(unittest.TestCase):
    def test_it_says_corroborating_and_never_independent(self):
        # Independence is a statistical claim this code does not establish:
        # four channels can be four views of one liquidation.
        out = P.confidence([
            {"key": "a", "class": S.REGULATORY, "direction": -1, "weight": 1.0},
            {"key": "b", "class": S.FLOW_PROXY, "direction": -1, "weight": 1.0},
        ])
        self.assertIn("corroborating", out["why"])
        self.assertNotIn("independent", out["why"])

    def test_the_maths_did_not_change_with_the_word(self):
        one = [{"key": "a", "class": S.REGULATORY, "direction": -1, "weight": 1.0}]
        self.assertEqual(P.confidence(one)["level"], "LOW")
        two = one + [{"key": "b", "class": S.FLOW_PROXY, "direction": -1, "weight": 1.0}]
        self.assertEqual(P.confidence(two)["level"], "MODERATE")
        three = two + [{"key": "c", "class": S.PRIME_BROKER, "direction": -1, "weight": 1.0}]
        self.assertEqual(P.confidence(three)["level"], "HIGH")
        # A supporting input still cannot create a verdict on its own.
        self.assertEqual(P.confidence(
            [{"key": "s", "class": S.FLOW_PROXY, "direction": -1, "weight": 0.5}])["level"],
            "NONE")


class TheRoutesLeftTheMonolith(unittest.TestCase):
    """Step 12. The routes can now be tested by CALLING them, which is the
    whole point of taking them out of a 12,000-line request handler."""

    def setUp(self):
        stay_offline(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = F.FakeWeb()
        S._MEM.clear()                                          # noqa: SLF001
        S.configure(fetch_fn=self.web.fetch, post_fn=self.web.post,
                    data_dir=self.tmp.name, now_fn=lambda: F.NOW)
        SC.configure(data_dir=self.tmp.name, now_fn=lambda: F.NOW,
                     funds_fn=lambda: {"managers": [], "new_filings": []})
        import hf_watch as HW
        HW.configure(data_dir=self.tmp.name, now_fn=lambda: F.NOW)
        RT.configure(push_configured_fn=lambda: False)
        self.addCleanup(lambda: S.configure(fetch_fn=None, post_fn=None))

    def test_every_section_answers_with_a_body_and_a_status(self):
        with offline_fixtures():
            SC.build()
        for section in ("", "status", "pulse", "pulse/status", "pulse/history",
                        "grades", "grades/status", "replay", "replay/status",
                        "names", "alerts", "health", "daily", "obs", "cache",
                        "config", "press", "watchlist"):
            body, status = RT.handle(section, {})
            self.assertIsInstance(body, dict, f"{section} returned no document")
            self.assertEqual(status, 200, f"{section} answered {status}")

    def test_a_section_nobody_declared_is_a_404_not_a_crash(self):
        body, status = RT.handle("not-a-thing", {})
        self.assertEqual(status, 404)
        self.assertIn("unknown hf section", body["error"])

    def test_a_route_that_needs_an_argument_says_so_rather_than_guessing(self):
        for section, key in (("fund", "key"), ("pulse/revisions", "week")):
            body, status = RT.handle(section, {})
            self.assertEqual(status, 400, f"{section} accepted no {key}")

    def test_the_config_route_reports_every_engine_version(self):
        body, _ = RT.handle("config", {})
        for k in ("version", "scan", "pulse", "report", "press", "grade",
                  "replay", "names", "alerts", "obs", "health", "sources"):
            self.assertTrue(body.get(k), f"config did not report {k}")
        # Asked, never inferred from a sender that exists either way.
        self.assertFalse(body["push"])

    def test_asking_a_route_never_starts_a_background_sweep(self):
        # CI caught this as `OSError: Directory not empty` — a daemon EDGAR
        # sweep was still writing fund records while the temporary store was
        # being torn down. The race was the symptom. The cause was that the
        # test had cleared JERRY_NO_NET for its whole run, so a route could
        # reach the real network, which is the one thing that flag exists to
        # prevent. Both are now impossible, and this is what proves it.
        import threading
        before = {t.name for t in threading.enumerate()}
        for section in ("", "status", "pulse", "health"):
            RT.handle(section, {})
        started = {t.name for t in threading.enumerate()} - before
        self.assertFalse([n for n in started if n.startswith(("hf-watch", "hf-pulse"))],
                         f"a route started a live sweep: {started}")
        self.assertEqual(os.environ.get("JERRY_NO_NET"), "1",
                         "the offline flag was not held for the test")

    def test_it_knows_nothing_about_http(self):
        src = Path(__file__).resolve().parent.joinpath("hf_routes.py").read_text()
        for forbidden in ("self.", "_send_json", "no_store", "send_header",
                          "BaseHTTPRequestHandler"):
            self.assertNotIn(forbidden, src,
                             f"hf_routes reached into the HTTP layer ({forbidden})")
