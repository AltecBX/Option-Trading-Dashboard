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
import shutil
import tempfile
import threading
import time
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


class _NoNetSession:
    """A session that refuses instantly instead of dialling out.

    setUp clears JERRY_NO_NET so the hydration paths under test actually
    run, which also re-opens the real network for anything a test forgot to
    stub. The keyless calendar path (added in v4.62) does not go through
    _x_get, so the stub_search helper does not cover it — and those calls
    carry ewhispers.TIMEOUT, fifteen seconds, inside a background refresh
    thread that tearDown only waits five seconds for.

    That is the whole flake: on a runner where the connection HANGS rather
    than being refused, the worker is still in a socket timeout when
    wait_idle gives up, and five tests fail with "background work did not
    finish". It passes wherever DNS fails fast, which is why it survived
    green runs. Killing the transport makes an unstubbed call fail in
    microseconds — the worker's `finally` clears the flag either way — and
    means no test in this file can quietly depend on the internet.

    That last sentence was once only true of the Base classes, and the gap
    bit: the standalone classes configured their own data dirs, configure()
    without this factory resets the session to a REAL one, and their tests
    passed for months only because X rate-limits most non-browser callers —
    the limiter was the de-facto stub. The night a runner got a real answer,
    two of them failed against production behaving correctly. EVERY
    configure() call in this file must pass a session factory; there is a
    guard test asserting so.
    """

    def get(self, *a, **kw):
        raise RuntimeError("test attempted a real network call — stub the "
                           "fetch it needs (_fetch_syndication, "
                           "_timeline_post_ids, _x_get) instead")

    post = get

    def close(self):
        pass


def _join_workers(timeout=10.0):
    """Wait for ewhispers' own background threads to actually END.

    The flags are not enough on their own. setUp resets _REFRESHING to
    False, so a worker still running from the PREVIOUS test becomes
    invisible: the next test's wait_idle sees a clear flag, returns at
    once, and the old thread goes on to call the new test's stub. That is
    exactly how "expected 1 search, got 2" happens, and it is why the
    flags alone left a rare cross-test flake behind.

    A boolean cannot be joined; a thread can. Both workers are named, so
    they can be found and waited out no matter whose flag says what.
    """
    for t in threading.enumerate():
        if t is threading.current_thread():
            continue
        if str(t.name).startswith("ewhispers-"):
            t.join(timeout)


class Base(unittest.TestCase):
    def setUp(self):
        # Drain anything the previous test left running BEFORE clearing the
        # flags, so a leaked worker cannot be hidden by the reset below.
        _join_workers()
        self._tmp = tempfile.TemporaryDirectory()
        ew.configure(self._tmp.name, session_factory=_NoNetSession)
        self._today, ew._today = ew._today, lambda: TODAY
        ew._LAST_ATTEMPT_MONO = 0.0
        ew._REFRESHING = False
        ew._REHYDRATING = False
        for k in ("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN",
                  "EWHISPERS_MANUAL_URL", "JERRY_NO_NET"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.wait_idle()
        _join_workers()          # the flag can clear before the thread ends
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
        while (ew._REFRESHING or ew._REHYDRATING) and _t.monotonic() < end:
            _t.sleep(0.02)
        stuck = [n for n, v in (("_REFRESHING", ew._REFRESHING),
                                ("_REHYDRATING", ew._REHYDRATING)) if v]
        self.assertFalse(stuck,
                         f"background work did not finish after {timeout}s: "
                         f"{', '.join(stuck)} still set. Something this test "
                         f"triggered reached the network unstubbed, or a "
                         f"worker is not clearing its flag in `finally`.")


def _settle(timeout=5.0):
    """Wait out any background refresh started by a test.

    trigger_refresh spawns a thread. Before v4.62 a keyless deployment was
    turned away before that happened, so most tests could never start one;
    now they can, and a thread outliving its test lands inside a later one.
    """
    end = time.monotonic() + timeout
    while (ew._REFRESHING or ew._REHYDRATING) and time.monotonic() < end:
        time.sleep(0.02)


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
    def test_no_credentials_still_tries_the_keyless_path(self):
        """v4.62: missing credentials no longer means missing calendar."""
        res = ew.trigger_refresh(force=True)
        self.assertTrue(res["started"], res)
        self.assertEqual(res["mode"], "keyless")

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

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"calendar-bytes"


class _FakeResp:
    def __init__(self, status, text=None, content=None):
        self.status_code = status
        self.content = content if content is not None else (text or "").encode()
        self.text = text if text is not None else ""


class _FakeSession:
    """Serves the saved REAL captures for the example post: the syndication
    JSON (the official embed widget's own data feed), the oEmbed body, and
    fake image bytes for the media CDN. Each can be turned off to exercise
    the fallback ladder."""
    def __init__(self, syndication=True, oembed=True, syn_body=None, media=True):
        self.urls = []
        self.syndication, self.oembed = syndication, oembed
        self.syn_body, self.media = syn_body, media

    def get(self, url, **kw):
        self.urls.append(url)
        if url.startswith("https://pbs.twimg.com/"):
            return _FakeResp(200 if self.media else 404, content=FAKE_JPEG)
        if url.startswith(ew.SYNDICATION_URL):
            if self.syn_body is not None:
                return _FakeResp(200, self.syn_body)
            if self.syndication and EXAMPLE_ID in url:
                return _FakeResp(200, (FIX / "x_syndication_2085726194914242793.json").read_text())
            return _FakeResp(404, "{}")
        if url.startswith(ew.OEMBED_URL) and self.oembed and EXAMPLE_ID in url:
            return _FakeResp(200, (FIX / "x_oembed_2085726194914242793.json").read_text())
        return _FakeResp(404, "{}")


class TestManual(Base):
    def hydrated(self, url=EXAMPLE_URL, **session_kw):
        fake = _FakeSession(**session_kw)
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

    def test_manual_post_gets_the_full_size_image_via_syndication(self):
        # The REAL capture: the manual path yields the direct calendar image
        # (3840x2160) with no credentials of any kind — the frontend shows a
        # large native image, not a 550px tweet card.
        res, _fake = self.hydrated()
        self.assertTrue(res["ok"])
        rec = res["post"]
        self.assertEqual(rec["source"], "manual")
        self.assertEqual(rec["image_url"],
                         "https://pbs.twimg.com/media/HPH7loKWIAA7HRP?format=jpg&name=large")
        self.assertEqual(rec["image_url_full"],
                         "https://pbs.twimg.com/media/HPH7loKWIAA7HRP?format=jpg&name=4096x4096")
        self.assertEqual((rec["image_width"], rec["image_height"]), (3840, 2160))
        self.assertEqual(rec["week_start"], "2026-08-10")   # parsed from real text
        self.assertEqual(rec["published_at"], "2026-08-07T13:54:10")
        self.assertIn("SMCI", rec["tickers"])
        # The hydrated manual post is a first-class week entry.
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["post"]["post_id"], EXAMPLE_ID)
        self.assertEqual(out["showing"], "current")

    def test_syndication_down_falls_back_to_oembed(self):
        res, fake = self.hydrated(syndication=False)
        self.assertTrue(res["ok"])
        rec = res["post"]
        self.assertIsNone(rec["image_url"])                 # embed path in the UI
        self.assertEqual(rec["week_start"], "2026-08-10")   # still the right week
        self.assertEqual(rec["published_at"], "2026-08-07") # date-only from oEmbed
        self.assertIn("SMCI", rec["tickers"])
        self.assertNotIn("&mdash;", rec["text"] or "")
        self.assertNotIn("(@eWhispers)", rec["text"] or "") # attribution tail trimmed
        oembed_calls = [u for u in fake.urls if u.startswith(ew.OEMBED_URL)]
        self.assertTrue(oembed_calls and all("dnt=true" in u for u in oembed_calls))

    def test_syndication_rejects_a_foreign_author(self):
        d = json.loads((FIX / "x_syndication_2085726194914242793.json").read_text())
        d["user"]["screen_name"] = "TotallyNotEW"
        res, _ = self.hydrated(syn_body=json.dumps(d))
        self.assertFalse(res["ok"])
        self.assertIn("eWhispers", res["error"])

    def test_deleted_post_tombstone_falls_back_cleanly(self):
        res, _ = self.hydrated(syn_body=json.dumps({"__typename": "TweetTombstone"}))
        self.assertTrue(res["ok"])                          # oEmbed still answered
        self.assertIsNone(res["post"]["image_url"])
        self.assertEqual(res["post"]["week_start"], "2026-08-10")

    def test_syndication_media_off_the_x_cdn_is_dropped(self):
        d = json.loads((FIX / "x_syndication_2085726194914242793.json").read_text())
        d["mediaDetails"][0]["media_url_https"] = "https://evil.example/x.jpg"
        res, _ = self.hydrated(syn_body=json.dumps(d))
        self.assertTrue(res["ok"])
        self.assertIsNone(res["post"]["image_url"])

    def test_an_imageless_v387_save_upgrades_itself_once(self):
        # A manual post stored by v3.87 (oEmbed-only: no image, no
        # hydrated_at marker) must gain the full-size image on the next
        # get_weekly, in the background, without the user re-saving.
        st = ew._load_state()
        with ew._LOCK:
            st["manual_url"] = EXAMPLE_URL
            st["manual"] = {"post_id": EXAMPLE_ID, "post_url": EXAMPLE_URL,
                            "author": "eWhispers", "source": "manual",
                            "week_start": "2026-08-10", "week_end": "2026-08-14",
                            "image_url": None, "tickers": [], "text": "old"}
        fake = _FakeSession()
        ew.configure(self._tmp.name, session_factory=lambda: fake)
        # configure() drops in-memory state, so re-seed the on-disk file it
        # will re-load (simulates the server restart between versions).
        ew._STATE = st
        ew._save_state()
        ew.get_weekly()                                     # kicks the upgrade
        import time as _t
        end = _t.monotonic() + 5
        while ew._REHYDRATING and _t.monotonic() < end:
            _t.sleep(0.02)
        out = ew.get_weekly()
        self.assertIn("HPH7loKWIAA7HRP", out["post"]["image_url"] or "")

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
        ew.configure(self._tmp.name, session_factory=_NoNetSession)                        # fresh state load
        self.assertEqual(ew.get_weekly()["manual_url"], EXAMPLE_URL)


# ── Persistence ────────────────────────────────────────────────────────────

class TestImageCache(Base):
    """The card loads the calendar from OUR server: downloaded from X once,
    then served from disk — ad-blockers on the browser can't touch it."""

    def seed(self, **kw):
        fake = _FakeSession(**kw)
        ew.configure(self._tmp.name, session_factory=lambda: fake)
        self.assertTrue(ew.set_manual(EXAMPLE_URL)["ok"])
        return fake

    def test_payload_offers_the_proxy_paths(self):
        self.seed()
        post = ew.get_weekly()["post"]
        self.assertEqual(post["image_proxy"],
                         f"/api/ewhispers/image?id={EXAMPLE_ID}&size=large")
        self.assertEqual(post["image_proxy_full"],
                         f"/api/ewhispers/image?id={EXAMPLE_ID}&size=full")

    def test_downloads_once_then_serves_from_disk(self):
        fake = self.seed()
        b1, ct = ew.get_image(EXAMPLE_ID, "large")
        self.assertEqual(ct, "image/jpeg")
        self.assertEqual(b1, FAKE_JPEG)
        n_media = len([u for u in fake.urls if "pbs.twimg.com" in u])
        b2, _ = ew.get_image(EXAMPLE_ID, "large")
        self.assertEqual(b2, FAKE_JPEG)
        self.assertEqual(len([u for u in fake.urls if "pbs.twimg.com" in u]), n_media,
                         "second read must come from disk, not X")
        # ...and once cached it survives with the network off entirely.
        os.environ["JERRY_NO_NET"] = "1"
        b3, _ = ew.get_image(EXAMPLE_ID, "large")
        self.assertEqual(b3, FAKE_JPEG)

    def test_full_size_is_cached_separately(self):
        self.seed()
        _, ct = ew.get_image(EXAMPLE_ID, "full")
        self.assertEqual(ct, "image/jpeg")

    def test_bad_requests_and_unknown_posts_are_refused(self):
        self.seed()
        self.assertIsNone(ew.get_image("../../etc/passwd", "large")[0])
        self.assertIsNone(ew.get_image(EXAMPLE_ID, "orig")[0])
        self.assertIsNone(ew.get_image("99999999999", "large")[0])

    def test_failed_upstream_is_not_cached_and_recovers(self):
        fake = self.seed()
        fake.media = False                      # CDN failing
        b, reason = ew.get_image(EXAMPLE_ID, "large")
        self.assertIsNone(b)
        self.assertIn("http_404", reason)
        fake.media = True                       # recovered → next call works
        b2, ct = ew.get_image(EXAMPLE_ID, "large")
        self.assertEqual((b2, ct), (FAKE_JPEG, "image/jpeg"))


class TestForcedRefreshWithoutCredentials(Base):
    def test_refresh_button_rehydrates_the_manual_post(self):
        # Save while the image feed is down → embed-only record...
        fake = _FakeSession(syndication=False)
        ew.configure(self._tmp.name, session_factory=lambda: fake)
        ew.set_manual(EXAMPLE_URL)
        self.assertIsNone(ew._load_state()["manual"]["image_url"])
        self.assertEqual(ew._load_state()["manual"]["image_status"],
                         "x_image_feed_unreachable")
        # ...the feed recovers, the user clicks Refresh: fixed, no API key.
        fake.syndication = True
        res = ew.trigger_refresh(force=True)
        self.assertTrue(res["started"])
        self.assertTrue(res["manual_rehydrate"])
        self.wait_idle()
        self.assertIn("HPH7loKWIAA7HRP", ew._load_state()["manual"]["image_url"])

    def test_unforced_refresh_without_creds_now_still_checks(self):
        """Contract change (v4.62): a deployment with no API key used to be
        turned away here, which meant no detection of any kind ever ran and
        the card served last week's calendar forever. The keyless timeline
        check needs no credentials, so it runs — still rate-limited by the
        normal interval."""
        res = ew.trigger_refresh()
        self.assertTrue(res["started"], res)


class TestPersistence(Base):
    def test_state_survives_a_restart(self):
        self.with_token()
        self.stub_search()
        ew._refresh_from_x()
        ew.configure(self._tmp.name, session_factory=_NoNetSession)                        # simulate reboot
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["post"]["post_id"], EXAMPLE_ID)

    def test_corrupt_state_file_starts_fresh(self):
        p = Path(self._tmp.name) / "ewhispers" / "state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        ew.configure(self._tmp.name, session_factory=_NoNetSession)
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


class TestNoConfigureCallLeavesTheNetworkOpen(unittest.TestCase):
    """configure() without a session factory silently restores the REAL
    network, because the factory argument defaults to None. That default is
    right for production and treacherous in a test file: it is the hole
    behind the 01:04 UTC double-red, where a standalone class configured
    its own data dir and thereby un-did the no-net armor two classes above
    it. This asserts, on this file's own source, that every configure()
    call pins a session — so the next class added to this file cannot
    reopen the hole by following the old pattern."""

    def test_every_configure_in_this_file_pins_a_session(self):
        import re
        src = Path(__file__).read_text()
        # The lookbehind excludes QUOTED occurrences — this test mentions
        # the call by name and must not flag its own strings.
        call = re.compile(r'(?<!["\'])\bew\.configure\(')
        bad = []
        for i, line in enumerate(src.splitlines(), 1):
            code = line.split("#", 1)[0]
            if not call.search(code):
                continue
            if "ew.configure(None)" in code:
                # the tearDown detach idiom: the data dir is dropped and the
                # next setUp re-arms before any test code runs again
                continue
            if "session_factory=" not in code:
                bad.append(f"line {i}: {line.strip()}")
        self.assertEqual(bad, [], "configure() calls that reopen the real "
                                  "network — pass session_factory=")


if __name__ == "__main__":
    unittest.main()


# ══════════════════════════════════════════════════════════════════════════
# THE PINNED POST IS THE ANSWER, NOT THE HIGHEST-SCORING RECENT ONE
# ══════════════════════════════════════════════════════════════════════════

class TestThePinnedPostIsPreferred(unittest.TestCase):
    """@eWhispers pins the current week's calendar and repins each weekend,
    so the pinned post answers "which post covers this week" by
    construction. Scoring fifty recent posts can be outvoted by a daily
    list — and when this week's post fails to clear the bar it silently
    serves LAST week's calendar, which is the reported symptom.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # The no-net session is not optional here. This class configures its
        # own data dir, and configure() without the factory RESETS the
        # session to a real one — which is how these tests reached the
        # actual internet for months and passed anyway: the syndication
        # endpoint rate-limits most non-browser callers, so the keyless
        # path "found nothing" and fell through to search exactly as the
        # assertions expected. X's rate limiter was the de-facto stub. The
        # night a GitHub runner got a real answer (01:04 UTC, both CI jobs
        # at once), the keyless path found the GENUINE weekly post, stored
        # it, and correctly stopped before searching — and every "falls
        # back to search" assertion read zero.
        ew.configure(Path(self.tmp), session_factory=_NoNetSession)
        ew._STATE = None                                    # noqa: SLF001
        ew._REFRESHING = False                              # noqa: SLF001
        ew._LAST_ATTEMPT_MONO = 0.0                         # noqa: SLF001
        os.environ["X_BEARER_TOKEN"] = "test-token"
        self._saved = {n: getattr(ew, n) for n in
                       ("_x_pinned_candidate", "_x_search_candidates",
                        "_timeline_post_ids", "_fetch_syndication", "_session")}
        # These tests are about the pinned-versus-search decision, so the
        # keyless timeline between those two steps must deterministically
        # find nothing — not "find nothing because the network refused".
        ew._timeline_post_ids = lambda limit=12: ("http_429", [])   # noqa: SLF001
        ew._fetch_syndication = lambda pid: None                    # noqa: SLF001

    def tearDown(self):
        # Anything monkeypatched here would otherwise still be installed
        # when a LATER class runs — and a background refresh thread landing
        # on a stale stub is a flake that only shows up sometimes.
        _settle()
        for n, v in self._saved.items():
            setattr(ew, n, v)
        os.environ.pop("X_BEARER_TOKEN", None)
        ew._STATE = None                                    # noqa: SLF001
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _week(self):
        return ew.trading_week()[0]

    def _pinned(self, text, with_photo=True, tid="9001"):
        photos = [{"media_key": "m1", "type": "photo",
                   "url": "https://pbs.twimg.com/media/x.jpg",
                   "width": 1200, "height": 900}] if with_photo else []
        return {"id": tid, "text": text, "created_at": self._week().isoformat()
                + "T12:00:00.000Z", "photos": photos,
                "is_retweet": False, "is_reply": False}

    def test_the_pinned_post_is_used_and_search_is_not_called(self):
        wk = self._week()
        text = (f"#Earnings for the week of {wk.strftime('%B %-d, %Y')} "
                f"$AAPL $MSFT $NVDA $AMD $KO $PG")
        called = []
        ew._x_pinned_candidate = lambda: ("ok", self._pinned(text))  # noqa: SLF001
        ew._x_search_candidates = lambda: (called.append(1), ("ok", []))[1]  # noqa: SLF001
        ew._refresh_from_x()                                # noqa: SLF001
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["post"]["post_id"], "9001")
        self.assertEqual(out["post_source"], "pinned")
        self.assertEqual(called, [], "search ran even though the pin answered")

    def test_a_pinned_post_whose_wording_hides_the_week_is_still_used(self):
        """A wording change must not throw away the one post they pin."""
        ew._x_pinned_candidate = lambda: (                  # noqa: SLF001
            "ok", self._pinned("This week's earnings $AAPL $MSFT $NVDA"))
        ew._x_search_candidates = lambda: ("ok", [])        # noqa: SLF001
        ew._refresh_from_x()                                # noqa: SLF001
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertTrue(out["week_assumed"])
        self.assertEqual(out["week_start"], self._week().isoformat())
        self.assertIn("assumed", (out["note"] or "").lower())

    def test_a_pinned_promo_is_not_shown_as_the_calendar(self):
        """Being pinned is evidence, not proof. Content still has to look
        like a weekly calendar."""
        ew._x_pinned_candidate = lambda: (                  # noqa: SLF001
            "ok", self._pinned("Subscribe to Earnings Whispers Pro today!",
                               with_photo=False))
        searched = []
        ew._x_search_candidates = lambda: (searched.append(1), ("ok", []))[1]  # noqa: SLF001
        ew._refresh_from_x()                                # noqa: SLF001
        self.assertEqual(len(searched), 1, "search was not used as fallback")
        self.assertFalse(ew.get_weekly()["available"])

    def test_no_pinned_post_falls_back_to_search(self):
        searched = []
        ew._x_pinned_candidate = lambda: ("no_pinned_post", None)   # noqa: SLF001
        ew._x_search_candidates = lambda: (searched.append(1), ("ok", []))[1]  # noqa: SLF001
        ew._refresh_from_x()                                # noqa: SLF001
        self.assertEqual(len(searched), 1)

    def test_a_failing_pinned_lookup_falls_back_rather_than_giving_up(self):
        searched = []
        ew._x_pinned_candidate = lambda: ("rate_limited", None)     # noqa: SLF001
        ew._x_search_candidates = lambda: (searched.append(1), ("ok", []))[1]  # noqa: SLF001
        ew._refresh_from_x()                                # noqa: SLF001
        self.assertEqual(len(searched), 1)

    def test_a_keyless_find_is_a_success_that_stops_the_search(self):
        """The deterministic recreation of the night both CI jobs went red.

        At 01:04 UTC a GitHub runner's request to the public timeline was
        actually ANSWERED — this suite could still reach the internet from
        this class — and the keyless path found the genuine weekly post,
        stored it, and correctly stopped before searching. Two tests then
        failed for asserting search "was not used as fallback": they were
        failing against production behaving RIGHT, in a world their setUp
        never controlled. The world is controlled now (no-net session,
        keyless stubbed empty in setUp), and this test pins the semantics
        the incident revealed: a keyless find for the reference week is an
        answer, and an answered question is not searched again."""
        wk = self._week()
        ew._timeline_post_ids = lambda limit=12: ("ok", ["777"])    # noqa: SLF001
        ew._fetch_syndication = lambda pid: {                       # noqa: SLF001
            "text": (f"#Earnings for the week of {wk.strftime('%B %-d, %Y')} "
                     f"$AAPL $MSFT $NVDA $AMD $KO"),
            "published_at": wk.isoformat() + "T12:00:00",
            "author_ok": True,
            "photos": [{"url": "https://pbs.twimg.com/media/x.jpg",
                        "width": 1200, "height": 900}]}
        searched = []
        ew._x_pinned_candidate = lambda: ("no_pinned_post", None)   # noqa: SLF001
        ew._x_search_candidates = lambda: (searched.append(1), ("ok", []))[1]  # noqa: SLF001
        ew._refresh_from_x()                                # noqa: SLF001
        self.assertEqual(searched, [], "the calendar was found — searching "
                                       "after an answer burns credits for "
                                       "nothing")
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["post_source"], "public-timeline")
        self.assertEqual(out["week_start"], wk.isoformat())

    def test_the_pinned_post_replaces_a_stale_previous_week(self):
        """The reported bug: last week's calendar on screen because this
        week's was never detected."""
        last = (self._week() - timedelta(days=7)).isoformat()
        st = ew._load_state()                               # noqa: SLF001
        st["weeks"][last] = {"post_id": "old", "week_start": last,
                             "week_end": last, "image_url": None,
                             "text": "last week", "tickers": [],
                             "confidence": 0.9, "source": "x"}
        ew._save_state()                                    # noqa: SLF001
        before = ew.get_weekly()
        self.assertEqual(before["showing"], "previous")

        wk = self._week()
        ew._x_pinned_candidate = lambda: (                  # noqa: SLF001
            "ok", self._pinned(
                f"#Earnings for the week of {wk.strftime('%B %-d, %Y')} "
                f"$AAPL $MSFT $NVDA $AMD $KO"))
        ew._x_search_candidates = lambda: ("ok", [])        # noqa: SLF001
        ew._refresh_from_x()                                # noqa: SLF001
        after = ew.get_weekly()
        self.assertEqual(after["showing"], "current")
        self.assertEqual(after["week_start"], wk.isoformat())
        self.assertIsNone(after["note"])


class TestOnePayloadParser(unittest.TestCase):
    """The search and the pinned lookup must not drift apart."""

    def test_both_shapes_go_through_the_same_parser(self):
        media = {"includes": {"media": [{"media_key": "k", "type": "photo",
                                         "url": "https://pbs.twimg.com/a.jpg"}]}}
        one = dict(media, data={"id": "1", "text": "t",
                                "attachments": {"media_keys": ["k"]}})
        many = dict(media, data=[{"id": "1", "text": "t",
                                  "attachments": {"media_keys": ["k"]}}])
        a = ew._parse_tweets_payload(one)                   # noqa: SLF001
        b = ew._parse_tweets_payload(many)                  # noqa: SLF001
        self.assertEqual(a, b)
        self.assertEqual(len(a), 1)
        self.assertEqual(len(a[0]["photos"]), 1)


# ══════════════════════════════════════════════════════════════════════════
# NO API KEY IS NOT NO CALENDAR
# ══════════════════════════════════════════════════════════════════════════

class TestTheKeylessPath(unittest.TestCase):
    """A deployment with no X_BEARER_TOKEN used to be reduced to a weekly
    copy-paste: trigger_refresh returned early on the missing key, so no
    detection of any kind ran and the card served last week's calendar
    indefinitely.

    The public timeline feed leads with the PINNED post and the per-post
    feed hydrates it, both without credentials.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        ew.configure(Path(self.tmp), session_factory=_NoNetSession)
        ew._STATE = None                                    # noqa: SLF001
        os.environ.pop("X_BEARER_TOKEN", None)
        os.environ.pop("TWITTER_BEARER_TOKEN", None)
        os.environ.pop("JERRY_NO_NET", None)
        ew._REFRESHING = False                              # noqa: SLF001
        ew._LAST_ATTEMPT_MONO = 0.0                         # noqa: SLF001
        self._saved = {n: getattr(ew, n) for n in
                       ("_fetch_syndication", "_timeline_post_ids",
                        "_session", "_x_pinned_candidate",
                        "_x_search_candidates")}

    def tearDown(self):
        _settle()
        for n, v in self._saved.items():
            setattr(ew, n, v)
        ew._STATE = None                                    # noqa: SLF001
        os.environ["JERRY_NO_NET"] = "1"
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wk(self):
        return ew.trading_week()[0]

    def _weekly(self, week):
        return {"text": (f"#earnings for the week of "
                         f"{week.strftime('%B %d, %Y')} "
                         f"$NVDA $CRM $MRVL $CRWD $KSS $INTU"),
                "published_at": (week - timedelta(days=3)).isoformat() + "T13:00:00",
                "author_ok": True,
                "photos": [{"url": "https://pbs.twimg.com/media/abc.jpg",
                            "width": 3840, "height": 2160}]}

    def test_the_pinned_weekly_post_is_found_with_no_credentials(self):
        wk = self._wk()
        ew._timeline_post_ids = lambda limit=12: ("ok", ["555"])   # noqa: SLF001
        ew._fetch_syndication = lambda pid: self._weekly(wk)       # noqa: SLF001
        ew._refresh_from_x()                                       # noqa: SLF001
        out = ew.get_weekly()
        self.assertTrue(out["available"])
        self.assertEqual(out["showing"], "current")
        self.assertEqual(out["week_start"], wk.isoformat())
        self.assertEqual(out["post_source"], "public-timeline")

    def test_it_replaces_a_stale_previous_week(self):
        """The reported symptom, on a deployment with no key."""
        wk = self._wk()
        last = (wk - timedelta(days=7)).isoformat()
        st = ew._load_state()                               # noqa: SLF001
        st["weeks"][last] = {"post_id": "old", "week_start": last,
                             "week_end": last, "image_url": None,
                             "text": "last week", "tickers": [],
                             "confidence": 0.9, "source": "x"}
        ew._save_state()                                    # noqa: SLF001
        self.assertEqual(ew.get_weekly()["showing"], "previous")
        ew._timeline_post_ids = lambda limit=12: ("ok", ["555"])   # noqa: SLF001
        ew._fetch_syndication = lambda pid: self._weekly(wk)       # noqa: SLF001
        ew._refresh_from_x()                                       # noqa: SLF001
        self.assertEqual(ew.get_weekly()["showing"], "current")

    def test_a_daily_post_never_becomes_the_weekly_card(self):
        """Their daily posts outnumber the weekly one and carry images too."""
        daily = {"text": ("#earnings after the close on Monday, August 24, "
                          "2026, and before the open on Tuesday, August 25, "
                          "2026 $DKS $GRRR $BNS"),
                 "published_at": self._wk().isoformat() + "T21:00:00",
                 "author_ok": True,
                 "photos": [{"url": "https://pbs.twimg.com/media/d.jpg"}]}
        ew._timeline_post_ids = lambda limit=12: ("ok", ["777"])   # noqa: SLF001
        ew._fetch_syndication = lambda pid: daily                  # noqa: SLF001
        ew._refresh_from_x()                                       # noqa: SLF001
        self.assertFalse(ew.get_weekly()["available"])

    def test_a_post_from_another_account_is_ignored(self):
        bad = dict(self._weekly(self._wk()), author_ok=False)
        ew._timeline_post_ids = lambda limit=12: ("ok", ["888"])   # noqa: SLF001
        ew._fetch_syndication = lambda pid: bad                    # noqa: SLF001
        ew._refresh_from_x()                                       # noqa: SLF001
        self.assertFalse(ew.get_weekly()["available"])

    def test_a_rate_limited_timeline_fails_safely(self):
        ew._timeline_post_ids = lambda limit=12: ("rate_limited", [])  # noqa: SLF001
        ew._refresh_from_x()                                           # noqa: SLF001
        out = ew.get_weekly()
        self.assertFalse(out["available"])
        self.assertIn("no_credentials", str(out["last_status"]))

    def test_the_refresh_is_no_longer_gated_on_having_a_key(self):
        """The gate that made this whole path unreachable."""
        ew._timeline_post_ids = lambda limit=12: ("no_ids", [])    # noqa: SLF001
        r = ew.trigger_refresh(force=True)
        self.assertTrue(r.get("started"), r)
        self.assertEqual(r.get("mode"), "keyless")
        # trigger_refresh runs in a background thread; letting it outlive the
        # test lets it land inside a LATER one and double-count that test's
        # stubs. Wait it out.
        for _ in range(200):
            if not ew._REFRESHING:                                 # noqa: SLF001
                break
            time.sleep(0.02)
        self.assertFalse(ew._REFRESHING)                           # noqa: SLF001

    def test_id_extraction_is_blind_to_the_payload_shape(self):
        """Shape-blind on purpose: X can restructure the timeline without
        notice, and the per-post feed decides what each id really is."""
        class R:
            status_code = 200
            text = ('garbage {"entry_id":"tweet-2085726194914242793",'
                    '"x":1} 2085726194914242794 <a>short 123</a>')
        ew._session = lambda: type("S", (), {                      # noqa: SLF001
            "get": staticmethod(lambda *a, **k: R())})()
        status, ids = ew._timeline_post_ids()                      # noqa: SLF001
        self.assertEqual(status, "ok")
        self.assertIn("2085726194914242793", ids)
        self.assertIn("2085726194914242794", ids)
        self.assertNotIn("123", ids)

    def test_it_never_tells_jerry_to_get_an_api_key_he_does_not_need(self):
        """v4.62 made the lookup keyless. A banner still saying "needs an X
        API key" would send him after a problem a key would not fix."""
        ew._timeline_post_ids = lambda limit=12: ("rate_limited", [])  # noqa: SLF001
        ew._refresh_from_x()                                           # noqa: SLF001
        note = ew.get_weekly()["note"] or ""
        self.assertNotIn("needs an X API key", note)
        self.assertIn("no API key needed", note)
        self.assertIn("rate_limited", note)
