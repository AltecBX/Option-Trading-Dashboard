"""test_ewhispers.py — the weekly Earnings Whispers calendar module (v3.87).

Driven by two REAL captures: the example post the feature was specified
against (x.com/eWhispers/status/2085726194914242793), whose text and publish
date were fetched live through X's official oEmbed endpoint
(fixtures/x_oembed_2085726194914242793.json), and an API-shaped search
response built around it (fixtures/x_search_ewhispers.json) that includes the
decoys detection must beat: a daily-reporters post, an unrelated chart post
NEWER than the calendar, and a repost. No network: HTTP is stubbed.

The clock is pinned to Wednesday 2026-08-12 (the week the example post
covers), so these tests describe a fixed world and cannot expire — the
lesson of test_whisper_sources going red on 2026-08-12.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import ewhispers as ew

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 8, 12)                     # the example post's Wednesday
EXAMPLE_ID = "2085726194914242793"
EXAMPLE_URL = f"https://x.com/eWhispers/status/{EXAMPLE_ID}"
TOKEN = "TESTTOKEN-shhh-1234567890"


_REAL_X_GET = ew._x_get


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        ew.configure(self._tmp.name)
        self._today, ew._today = ew._today, lambda: TODAY
        ew._LAST_ATTEMPT_MONO = 0.0
        ew._REFRESHING = False
        for k in ("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN",
                  "EWHISPERS_MANUAL_URL", "JERRY_NO_NET"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.wait_idle()
        ew._x_get = _REAL_X_GET
        ew._today = self._today
        ew.configure(None)
        self._tmp.cleanup()
        os.environ.pop("X_BEARER_TOKEN", None)
        os.environ["JERRY_NO_NET"] = "1"      # suite-wide convention: default closed

    # helpers ---------------------------------------------------------------
    def with_token(self):
        os.environ["X_BEARER_TOKEN"] = TOKEN

    def stub_search(self, body: str | None = None, status: int = 200):
        """Point the X API layer at a canned response; returns the call log."""
        calls = []
        text = body if body is not None else (FIX / "x_search_ewhispers.json").read_text()

        def fake(url):
            calls.append(url)
            return status, text
        ew._x_get = fake
        return calls

    def wait_idle(self, timeout=5.0):
        """Wait out any background refresh so tests never race each other."""
        import time as _t
        end = _t.monotonic() + timeout
        while ew._REFRESHING and _t.monotonic() < end:
            _t.sleep(0.02)
        self.assertFalse(ew._REFRESHING, "background refresh did not finish")


# ── Week logic ──────────────────────────────────────────────────────────────

class TestTradingWeek(Base):
    def test_midweek_maps_to_its_own_monday(self):
        # The spec's own example: Wednesday Aug 12 → week of Monday Aug 10.
        start, end = ew.trading_week(date(2026, 8, 12))
        self.assertEqual(start, date(2026, 8, 10))
        self.assertEqual(end, date(2026, 8, 14))

    def test_monday_and_friday_stay_in_week(self):
        self.assertEqual(ew.trading_week(date(2026, 8, 10))[0], date(2026, 8, 10))
        self.assertEqual(ew.trading_week(date(2026, 8, 14))[0], date(2026, 8, 10))

    def test_weekend_rolls_forward_to_the_coming_week(self):
        # Saturday/Sunday: the week that matters is the one about to start —
        # and it's when @eWhispers posts the next calendar.
        self.assertEqual(ew.trading_week(date(2026, 8, 15))[0], date(2026, 8, 17))
        self.assertEqual(ew.trading_week(date(2026, 8, 16))[0], date(2026, 8, 17))


class TestParseWeek(Base):
    def test_real_post_wording(self):
        self.assertEqual(ew.parse_week("#earnings for the week of August 10, 2026"),
                         "2026-08-10")

    def test_wording_variants(self):
        for text in (
            "Most anticipated earnings for the week of Aug 10th",
            "earnings for the week beginning 8/10/2026",
            "the week starting August 10",
        ):
            self.assertEqual(ew.parse_week(text, ref=TODAY), "2026-08-10", text)

    def test_non_monday_dates_normalize_to_their_monday(self):
        self.assertEqual(ew.parse_week("week of August 11, 2026"), "2026-08-10")
        self.assertEqual(ew.parse_week("week of August 14, 2026"), "2026-08-10")

    def test_missing_year_resolves_toward_the_publish_date(self):
        # "week of Jan 4" posted in late December must land FORWARD.
        self.assertEqual(ew.parse_week("week of January 4", ref=date(2026, 12, 28)),
                         "2027-01-04")
        # ...and "week of Dec 28" seen in early January points BACK.
        self.assertEqual(ew.parse_week("week of December 28", ref=date(2027, 1, 2)),
                         "2026-12-28")

    def test_garbage_is_none(self):
        for text in ("", "no dates here", "week of Fooember 99", "the week ahead looks busy"):
            self.assertIsNone(ew.parse_week(text, ref=TODAY), text)


# ── Candidate scoring ───────────────────────────────────────────────────────

def _fixture_tweets():
    d = json.loads((FIX / "x_search_ewhispers.json").read_text())
    return {t["id"]: t for t in d["data"]}


class TestScoring(Base):
    REF = "2026-08-10"

    def test_the_example_post_is_recognized(self):
        t = _fixture_tweets()[EXAMPLE_ID]
        conf, wk, reasons = ew.score_post(t["text"], has_image=True,
                                          published=date(2026, 8, 7), ref_week=self.REF)
        self.assertGreaterEqual(conf, ew.MIN_CONFIDENCE)
        self.assertEqual(wk, self.REF)
        self.assertTrue(any("current trading week" in r for r in reasons))

    def test_wording_change_still_passes(self):
        # The multi-signal design: a different headline keeps enough score.
        conf, wk, _ = ew.score_post(
            "Most anticipated earnings releases for the week beginning August 10, 2026 "
            "$AAPL $MSFT $NVDA $AMZN $META", has_image=True,
            published=date(2026, 8, 8), ref_week=self.REF)
        self.assertGreaterEqual(conf, ew.MIN_CONFIDENCE)
        self.assertEqual(wk, self.REF)

    def test_daily_post_scores_below_threshold(self):
        t = _fixture_tweets()["2087055500000000002"]
        conf, _, reasons = ew.score_post(t["text"], has_image=True,
                                         published=date(2026, 8, 11), ref_week=self.REF)
        self.assertLess(conf, ew.MIN_CONFIDENCE)
        self.assertTrue(any("daily" in r for r in reasons))

    def test_unrelated_chart_scores_below_threshold(self):
        t = _fixture_tweets()["2087401110000000001"]
        conf, wk, _ = ew.score_post(t["text"], has_image=True,
                                    published=date(2026, 8, 12), ref_week=self.REF)
        self.assertLess(conf, ew.MIN_CONFIDENCE)
        self.assertIsNone(wk)

    def test_hard_rejects(self):
        text = "#earnings for the week of August 10, 2026"
        self.assertEqual(ew.score_post(text, has_image=False)[0], 0.0)
        self.assertEqual(ew.score_post(text, has_image=True, is_retweet=True)[0], 0.0)
        self.assertEqual(ew.score_post(text, has_image=True, is_reply=True)[0], 0.0)


class TestTickers(Base):
    def test_cashtags_from_the_real_post(self):
        t = _fixture_tweets()[EXAMPLE_ID]
        ticks = ew.extract_tickers(t["text"])
        self.assertEqual(ticks[:4], ["NBIS", "SMCI", "RKLB", "CRWV"])
        self.assertIn("BRK.B", ticks)          # dotted class shares survive
        self.assertIn("B", ticks)              # single-letter symbols survive
        self.assertEqual(len(ticks), len(set(ticks)))


# ── Refresh from the X API (stubbed) ───────────────────────────────────────

class TestRefresh(Base):
    def test_search_stores_the_calendar_under_its_week(self):
        self.with_token()
        self.stub_search()
        ew._refresh_from_x()
        st = ew._load_state()
        self.assertEqual(st["last_status"], "ok")
        self.assertEqual(sorted(st["weeks"]), ["2026-08-10"])
        rec = st["weeks"]["2026-08-10"]
        self.assertEqual(rec["post_id"], EXAMPLE_ID)
        self.assertEqual(rec["post_url"], EXAMPLE_URL)
        self.assertEqual(rec["week_end"], "2026-08-14")
        self.assertEqual(rec["source"], "x")
        # Media metadata → the large display variant of the API's URL.
        self.assertEqual(rec["image_url"],
                         "https://pbs.twimg.com/media/GvWeekly0000004?format=jpg&name=large")
        self.assertEqual((rec["image_width"], rec["image_height"]), (1080, 1350))

    def test_newer_unrelated_posts_do_not_displace_the_calendar(self):
        # The fixture's chart + daily posts are NEWER than the calendar and
        # sit earlier in the response. Selection is by announced week, so the
        # card still shows the calendar.
        self.with_token()
        self.stub_search()
        ew._refresh_from_x()
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["post"]["post_id"], EXAMPLE_ID)
        self.assertEqual(out["showing"], "current")

    def test_next_weeks_calendar_becomes_a_second_entry_not_a_replacement(self):
        self.with_token()
        d = json.loads((FIX / "x_search_ewhispers.json").read_text())
        d["data"].insert(0, {
            "id": "2088300000000000009",
            "text": "#earnings for the week of August 17, 2026 https://t.co/next $INTC $WMT",
            "created_at": "2026-08-14T22:00:00.000Z",
            "attachments": {"media_keys": ["3_2085726190000000104"]},
        })
        self.stub_search(body=json.dumps(d))
        ew._refresh_from_x()
        st = ew._load_state()
        self.assertEqual(sorted(st["weeks"]), ["2026-08-10", "2026-08-17"])
        # Midweek (clock = Wed Aug 12) the card still shows Aug 10...
        self.assertEqual(ew.get_weekly()["post"]["post_id"], EXAMPLE_ID)
        # ...and on Saturday the relevant week rolls forward automatically.
        ew._today = lambda: date(2026, 8, 15)
        out = ew.get_weekly()
        self.assertEqual(out["post"]["post_id"], "2088300000000000009")
        self.assertEqual(out["week_start"], "2026-08-17")
        self.assertTrue(out["is_current_week"])

    def test_duplicate_weekly_posts_keep_the_strongest(self):
        self.with_token()
        d = json.loads((FIX / "x_search_ewhispers.json").read_text())
        d["data"].insert(0, {                  # weaker duplicate for the SAME week
            "id": "2086000000000000008",
            "text": "updated list for this week's earnings https://t.co/x",
            "created_at": "2026-08-09T12:00:00.000Z",
            "attachments": {"media_keys": ["3_2085726190000000104"]},
        })
        self.stub_search(body=json.dumps(d))
        ew._refresh_from_x()
        st = ew._load_state()
        self.assertEqual(st["weeks"].get("2026-08-10", {}).get("post_id"), EXAMPLE_ID)

    def test_rate_limit_keeps_the_cache(self):
        self.with_token()
        self.stub_search()
        ew._refresh_from_x()                   # seed the cache
        self.stub_search(body="", status=429)
        ew._refresh_from_x()
        st = ew._load_state()
        self.assertEqual(st["last_status"], "rate_limited")
        self.assertIn("2026-08-10", st["weeks"])          # survives the failure
        self.assertTrue(ew.get_weekly()["available"])     # card still serves

    def test_broken_payload_is_a_status_not_a_crash(self):
        self.with_token()
        self.stub_search(body="<html>maintenance</html>")
        ew._refresh_from_x()
        self.assertTrue(ew._load_state()["last_status"].startswith("bad_payload"))

    def test_media_urls_off_the_x_cdn_are_dropped(self):
        self.with_token()
        d = json.loads((FIX / "x_search_ewhispers.json").read_text())
        for m in d["includes"]["media"]:
            m["url"] = "https://evil.example/steal.jpg"
        self.stub_search(body=json.dumps(d))
        ew._refresh_from_x()
        rec = ew._load_state()["weeks"]["2026-08-10"]
        self.assertIsNone(rec["image_url"])    # frontend falls back to the embed

    def test_multiple_photos_keep_primary_plus_extras(self):
        self.with_token()
        d = json.loads((FIX / "x_search_ewhispers.json").read_text())
        d["data"][-1]["attachments"]["media_keys"] = [
            "3_2085726190000000104", "3_2087055500000000102"]
        self.stub_search(body=json.dumps(d))
        ew._refresh_from_x()
        rec = ew._load_state()["weeks"]["2026-08-10"]
        self.assertIn("GvWeekly0000004", rec["image_url"])
        self.assertEqual(len(rec["images"]), 1)
        self.assertIn("GvDaily00000002", rec["images"][0])


class TestRefreshGating(Base):
    def test_no_credentials_means_no_attempt(self):
        res = ew.trigger_refresh(force=True)
        self.assertFalse(res["started"])
        self.assertIn("credentials", res["reason"])

    def test_jerry_no_net_wins_over_everything(self):
        self.with_token()
        os.environ["JERRY_NO_NET"] = "1"
        res = ew.trigger_refresh(force=True)
        self.assertFalse(res["started"])
        self.assertIn("network disabled", res["reason"])

    def test_periodic_checks_are_rate_limited_but_force_is_not(self):
        self.with_token()
        self.stub_search()
        self.assertTrue(ew.trigger_refresh()["started"])
        self.wait_idle()
        self.assertFalse(ew.trigger_refresh()["started"])           # too soon
        self.assertTrue(ew.trigger_refresh(force=True)["started"])  # user's button
        self.wait_idle()


# ── The endpoint payload ───────────────────────────────────────────────────

class TestGetWeekly(Base):
    def seed(self):
        self.with_token()
        self.stub_search()
        ew._refresh_from_x()

    def test_envelope_for_the_current_week(self):
        self.seed()
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["week_start"], "2026-08-10")
        self.assertEqual(out["week_end"], "2026-08-14")
        self.assertEqual(out["week_label"], "Week of August 10, 2026")
        self.assertTrue(out["is_current_week"])
        self.assertEqual(out["weeks"], ["2026-08-10"])
        self.assertTrue(out["credentials"])
        self.assertIn("Earnings Whispers", out["attribution"])

    def test_week_navigation_normalizes_to_monday(self):
        self.seed()
        out = ew.get_weekly(week="2026-08-13")             # a Thursday
        self.assertEqual(out["requested_week"], "2026-08-10")
        self.assertTrue(out["available"])

    def test_empty_state_is_clean_and_labeled(self):
        out = ew.get_weekly()
        self.assertFalse(out["available"])
        self.assertIsNone(out["post"])
        self.assertTrue(out["note"])                       # human words, not a stack trace
        self.assertFalse(out["credentials"])

    def test_missing_current_week_falls_back_to_the_last_verified_post(self):
        self.seed()
        ew._today = lambda: date(2026, 8, 19)              # next Wednesday, nothing new found
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["showing"], "previous")
        self.assertEqual(out["week_start"], "2026-08-10")
        self.assertFalse(out["is_current_week"])
        self.assertIn("Week of August 10, 2026", out["note"])

    def test_the_bearer_token_never_appears_in_a_response(self):
        self.seed()
        blob = json.dumps(ew.get_weekly())
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("Bearer", blob)


# ── Manual fallback URL ────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class _FakeSession:
    """Serves the saved oEmbed capture for the example post, 404 otherwise."""
    def __init__(self):
        self.urls = []

    def get(self, url, **kw):
        self.urls.append(url)
        if url.startswith(ew.OEMBED_URL) and EXAMPLE_ID in url:
            return _FakeResp(200, (FIX / "x_oembed_2085726194914242793.json").read_text())
        return _FakeResp(404, "{}")


class TestManual(Base):
    def hydrated(self, url=EXAMPLE_URL):
        fake = _FakeSession()
        ew.configure(self._tmp.name, session_factory=lambda: fake)
        return ew.set_manual(url), fake

    def test_url_validation(self):
        ok = [
            EXAMPLE_URL,
            f"https://twitter.com/eWhispers/status/{EXAMPLE_ID}",
            f"https://mobile.twitter.com/ewhispers/statuses/{EXAMPLE_ID}",
            f"https://x.com/eWhispers/status/{EXAMPLE_ID}/photo/1",
            f"https://x.com/eWhispers/status/{EXAMPLE_ID}?s=20&t=abc",
        ]
        for u in ok:
            valid, canon, pid = ew._validate_post_url(u)
            self.assertTrue(valid, u)
            self.assertEqual(canon, EXAMPLE_URL)
            self.assertEqual(pid, EXAMPLE_ID)
        bad = [
            "https://x.com/SomeoneElse/status/123456789",   # wrong account
            "https://x.com/eWhispers",                       # not a post
            "http://x.com/eWhispers/status/123456789",       # not https
            "https://x.com.evil.example/eWhispers/status/123456789",
            "javascript:alert(1)",
            "https://earningswhispers.com/calendar",
            "",
        ]
        for u in bad:
            self.assertFalse(ew._validate_post_url(u)[0], u)

    def test_manual_post_hydrates_through_oembed(self):
        res, fake = self.hydrated()
        self.assertTrue(res["ok"])
        rec = res["post"]
        self.assertEqual(rec["source"], "manual")
        self.assertEqual(rec["week_start"], "2026-08-10")   # parsed from real text
        self.assertEqual(rec["published_at"], "2026-08-07") # Friday, from the embed
        self.assertIn("SMCI", rec["tickers"])
        self.assertNotIn("&mdash;", rec["text"] or "")
        self.assertNotIn("(@eWhispers)", rec["text"] or "") # attribution tail trimmed
        # dnt + omit_script are part of every oEmbed call we make.
        self.assertTrue(all("dnt=true" in u for u in fake.urls))
        # The hydrated manual post is a first-class week entry.
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["post"]["post_id"], EXAMPLE_ID)
        self.assertEqual(out["showing"], "current")

    def test_manual_without_network_still_serves_the_embed(self):
        os.environ["JERRY_NO_NET"] = "1"
        res = ew.set_manual(EXAMPLE_URL)
        self.assertTrue(res["ok"])
        self.assertIsNone(res["post"]["week_start"])        # nothing invented
        out = ew.get_weekly()
        self.assertTrue(out["available"])                   # embed still renders
        self.assertEqual(out["showing"], "manual")
        self.assertEqual(out["post"]["post_url"], EXAMPLE_URL)

    def test_clearing_the_manual_url(self):
        os.environ["JERRY_NO_NET"] = "1"
        ew.set_manual(EXAMPLE_URL)
        res = ew.set_manual("")
        self.assertTrue(res["ok"] and res["cleared"])
        self.assertFalse(ew.get_weekly()["available"])

    def test_env_seed_is_picked_up(self):
        os.environ["JERRY_NO_NET"] = "1"
        os.environ["EWHISPERS_MANUAL_URL"] = f"https://twitter.com/eWhispers/status/{EXAMPLE_ID}"
        ew.configure(self._tmp.name)                        # fresh state load
        self.assertEqual(ew.get_weekly()["manual_url"], EXAMPLE_URL)


# ── Persistence ────────────────────────────────────────────────────────────

class TestPersistence(Base):
    def test_state_survives_a_restart(self):
        self.with_token()
        self.stub_search()
        ew._refresh_from_x()
        ew.configure(self._tmp.name)                        # simulate reboot
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["post"]["post_id"], EXAMPLE_ID)

    def test_corrupt_state_file_starts_fresh(self):
        p = Path(self._tmp.name) / "ewhispers" / "state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        ew.configure(self._tmp.name)
        out = ew.get_weekly()                               # no crash, clean empty
        self.assertFalse(out["available"])

    def test_history_is_pruned_to_a_small_window(self):
        st = ew._load_state()
        for i in range(1, 15):
            wk = (date(2026, 1, 5) + (i * timedelta(days=7))).isoformat()
            st["weeks"][wk] = {"post_id": str(i), "week_start": wk}
        ew._prune_weeks(st)
        self.assertEqual(len(st["weeks"]), ew.KEEP_WEEKS)
        self.assertIn(max(st["weeks"]), st["weeks"])        # newest kept


if __name__ == "__main__":
    unittest.main()
