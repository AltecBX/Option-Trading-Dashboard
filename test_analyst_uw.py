"""The Unusual Whales leg of the analyst feed, and the board's fast lane.

The regression these pin: on the morning of 2026-09-11 D.A. Davidson
raised its PLTR target to $250. The stock moved pre-market on it. At
8:19 AM ET the dashboard's "Recent analyst updates" still showed the
firm's 8/04 row ($175 → $200), because the only per-firm feed was Yahoo's
upgrades_downgrades and Yahoo had not posted the note. Unusual Whales
had it. So the fast leg leads, Yahoo fills in, and the board polls the
whole market's tape every two minutes instead of sweeping 600 tickers
through Yahoo once at 8 AM.

Every fixture row here is shaped like a row the live UW endpoint
returned on 2026-09-13 (target as a string, timestamp in UTC, firm
spelled the way UW spells it), not a tidy row the code would like.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import analyst_client as ac

# The board persists to JERRY_DATA_DIR at import time; point it somewhere
# disposable BEFORE the import so a test run never touches ~/.jerry-dashboard.
_TMP = tempfile.mkdtemp(prefix="analyst-board-")
os.environ["JERRY_DATA_DIR"] = _TMP
import analyst_board as ab  # noqa: E402


# ── Fixtures: the real PLTR tape, verbatim shape ────────────────────────
UW_PLTR = [
    {"timestamp": "2026-09-11T15:28:43.000000Z", "ticker": "PLTR", "action": "maintained",
     "target": "250", "sector": "Technology", "recommendation": "buy",
     "analyst_name": "Gil Luria", "firm": "DA Davidson"},
    {"timestamp": "2026-08-04T14:15:17.000000Z", "ticker": "PLTR", "action": "maintained",
     "target": "200.0000", "sector": "Technology", "recommendation": "buy",
     "analyst_name": "Gil Luria", "firm": "DA Davidson"},
    {"timestamp": "2026-08-04T10:08:25.000000Z", "ticker": "PLTR", "action": "upgraded",
     "target": "200.0000", "sector": "Technology", "recommendation": "buy",
     "analyst_name": "Brad Zelnick", "firm": "Deutsche Bank"},
    {"timestamp": "2026-07-17T19:08:15.000000Z", "ticker": "PLTR", "action": "maintained",
     "target": "175.00", "sector": "Technology", "recommendation": "buy",
     "analyst_name": "Gil Luria", "firm": "DA Davidson"},
]

# Yahoo's rows for the same stock at 8:19 AM on 9/11: the 8/04 D.A. Davidson
# row is there (with the prior target Yahoo knows), the 9/11 one is not.
YF_PLTR = [
    {"date": "2026-09-11", "firm": "Rosenblatt", "analyst": None, "action_raw": "main",
     "action_class": "reiterate", "prior_grade": None, "new_grade": "Buy",
     "prior_target": None, "new_target": 225.0, "target_change_pct": None, "pt_action": None},
    {"date": "2026-08-04", "firm": "D.A. Davidson", "analyst": None, "action_raw": "main",
     "action_class": "target_change", "prior_grade": "Buy", "new_grade": "Buy",
     "prior_target": 175.0, "new_target": 200.0, "target_change_pct": 14.29, "pt_action": "raised"},
    {"date": "2026-08-04", "firm": "Deutsche Bank", "analyst": None, "action_raw": "up",
     "action_class": "upgrade", "prior_grade": "Hold", "new_grade": "Buy",
     "prior_target": None, "new_target": 200.0, "target_change_pct": None, "pt_action": None},
]


def _days_ago_stamp(days: int) -> str:
    """A UW-shaped UTC timestamp `days` ago, at 15:28 UTC."""
    dt = (datetime.now(timezone.utc) - timedelta(days=days)).replace(hour=15, minute=28, second=43, microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _today_et_stamp(hour: int, minute: int) -> str:
    """A UW-shaped UTC timestamp for today at hour:minute Eastern."""
    now = datetime.now(ac._et_zone()).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class NormalizeUWRows(unittest.TestCase):
    def test_the_target_string_becomes_a_number_and_the_clock_is_eastern(self):
        rows = ac.normalize_uw_rows(UW_PLTR, symbol="PLTR")
        top = rows[0]
        self.assertEqual(top["date"], "2026-09-11")
        self.assertEqual(top["time_et"], "11:28")          # 15:28 UTC in September
        self.assertEqual(top["new_target"], 250.0)
        self.assertEqual(top["firm"], "DA Davidson")
        self.assertEqual(top["analyst"], "Gil Luria")
        self.assertEqual(top["new_grade"], "Buy")
        self.assertEqual(top["source"], ac.UW_SOURCE)
        self.assertEqual(rows[2]["new_target"], 200.0)      # "200.0000"

    def test_a_note_after_the_close_belongs_to_the_day_it_printed_in_new_york(self):
        rows = ac.normalize_uw_rows([{**UW_PLTR[0], "timestamp": "2026-09-12T02:30:00Z"}], symbol="PLTR")
        self.assertEqual(rows[0]["date"], "2026-09-11")
        self.assertEqual(rows[0]["time_et"], "22:30")

    def test_the_prior_target_is_the_same_firms_earlier_note_and_nothing_else(self):
        rows = ac.normalize_uw_rows(UW_PLTR, symbol="PLTR")
        dad = [r for r in rows if r["firm"] == "DA Davidson"]
        self.assertEqual([r["new_target"] for r in dad], [250.0, 200.0, 175.0])
        self.assertEqual(dad[0]["prior_target"], 200.0)
        self.assertEqual(dad[0]["target_change_pct"], 25.0)
        self.assertEqual(dad[0]["prior_target_source"], "same firm, earlier note")
        self.assertEqual(dad[1]["prior_target"], 175.0)
        self.assertIsNone(dad[2]["prior_target"])            # nothing older: no guess
        # Deutsche Bank's $200 must not become D.A. Davidson's prior.
        db = [r for r in rows if r["firm"] == "Deutsche Bank"][0]
        self.assertIsNone(db["prior_target"])

    def test_a_maintained_rating_with_a_moved_target_is_a_target_change(self):
        rows = ac.normalize_uw_rows(UW_PLTR, symbol="PLTR")
        self.assertEqual(rows[0]["action_class"], "target_change")
        self.assertEqual(rows[0]["pt_action"], "raised")
        db = [r for r in rows if r["firm"] == "Deutsche Bank"][0]
        self.assertEqual(db["action_class"], "upgrade")     # UW said so; kept

    def test_the_caller_can_supply_older_history_for_the_prior(self):
        only_new = [UW_PLTR[0]]
        rows = ac.normalize_uw_rows(only_new, prior_lookup=lambda tk, firm, date: 200.0)
        self.assertEqual(rows[0]["prior_target"], 200.0)
        self.assertEqual(rows[0]["target_change_pct"], 25.0)
        rows = ac.normalize_uw_rows(only_new, prior_lookup=lambda tk, firm, date: None)
        self.assertIsNone(rows[0]["prior_target"])

    def test_a_row_with_no_target_survives_with_no_target(self):
        rows = ac.normalize_uw_rows([{**UW_PLTR[0], "target": None, "action": "initiated",
                                      "recommendation": "hold"}], symbol="PLTR")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["new_target"])
        self.assertEqual(rows[0]["action_class"], "initiate")
        self.assertEqual(rows[0]["new_grade"], "Hold")

    def test_rows_for_another_ticker_are_dropped_and_rows_without_a_time_are_dropped(self):
        rows = ac.normalize_uw_rows(
            [UW_PLTR[0], {**UW_PLTR[0], "ticker": "AAPL"}, {**UW_PLTR[0], "timestamp": None}],
            symbol="PLTR")
        self.assertEqual(len(rows), 1)


class FirmMatching(unittest.TestCase):
    def test_spellings_of_one_firm_agree(self):
        self.assertTrue(ac.same_firm("DA Davidson", "D.A. Davidson"))
        self.assertTrue(ac.same_firm("D.A. Davidson & Co", "DA Davidson"))
        self.assertTrue(ac.same_firm("Citi", "Citigroup"))
        self.assertTrue(ac.same_firm("Rosenblatt", "Rosenblatt Securities"))
        self.assertTrue(ac.same_firm("Mizuho", "Mizuho Securities"))

    def test_different_firms_do_not(self):
        self.assertFalse(ac.same_firm("RBC Capital", "Robert W. Baird"))
        self.assertFalse(ac.same_firm("UBS", "HSBC"))
        self.assertFalse(ac.same_firm("", "UBS"))
        self.assertFalse(ac.same_firm(None, None))


class MergeHistory(unittest.TestCase):
    def test_the_missing_9_11_row_leads_the_list(self):
        merged = ac.merge_history(ac.normalize_uw_rows(UW_PLTR, symbol="PLTR"), YF_PLTR)
        top = merged[0]
        self.assertEqual((top["date"], top["firm"], top["new_target"]), ("2026-09-11", "DA Davidson", 250.0))
        self.assertEqual(top["time_et"], "11:28")
        self.assertEqual(top["prior_target"], 200.0)
        self.assertEqual(top["target_change_pct"], 25.0)
        self.assertEqual(top["action_class"], "target_change")

    def test_one_row_per_firm_per_day_and_yahoo_fills_what_it_knows(self):
        merged = ac.merge_history(ac.normalize_uw_rows(UW_PLTR, symbol="PLTR"), YF_PLTR)
        keys = [(r["date"], ac.firm_key(r["firm"])) for r in merged]
        self.assertEqual(len(keys), len(set(keys)), keys)
        dad_0804 = [r for r in merged if r["date"] == "2026-08-04" and ac.same_firm(r["firm"], "DA Davidson")][0]
        self.assertEqual(dad_0804["source"], f"{ac.UW_SOURCE} + {ac.YF_SOURCE}")
        self.assertEqual(dad_0804["prior_grade"], "Buy")     # from Yahoo
        self.assertEqual(dad_0804["time_et"], "10:15")        # from UW
        self.assertEqual(dad_0804["analyst"], "Gil Luria")
        db = [r for r in merged if r["firm"] == "Deutsche Bank"][0]
        self.assertEqual(db["prior_grade"], "Hold")
        self.assertEqual(db["action_class"], "upgrade")
        # A Yahoo-only firm is kept, labelled as Yahoo's.
        rosen = [r for r in merged if r["firm"] == "Rosenblatt"][0]
        self.assertEqual(rosen["source"], ac.YF_SOURCE)
        self.assertEqual(rosen["new_target"], 225.0)

    def test_a_feed_that_saw_the_rating_move_outranks_one_that_saw_the_target_move(self):
        uw = ac.normalize_uw_rows([{**UW_PLTR[2], "action": "maintained"}], symbol="PLTR")
        self.assertEqual(uw[0]["action_class"], "reiterate")
        merged = ac.merge_history(uw, [YF_PLTR[2]])
        self.assertEqual(merged[0]["action_class"], "upgrade")

    def test_without_unusual_whales_the_list_is_yahoos(self):
        merged = ac.merge_history([], YF_PLTR)
        self.assertEqual([r["firm"] for r in merged], ["Rosenblatt", "D.A. Davidson", "Deutsche Bank"])
        self.assertTrue(all(r["source"] == ac.YF_SOURCE for r in merged))


class ClientPayload(unittest.TestCase):
    """get_analyst_data with the network replaced by the fixtures."""

    def _client(self, uw_rows, yf_rows):
        c = ac.AnalystClient()
        c.finnhub_key = ""
        calls = {"uw": 0, "yf": 0}

        def uw(symbol):
            calls["uw"] += 1
            return ac.normalize_uw_rows(uw_rows, symbol=symbol)

        def yf(symbol):
            calls["yf"] += 1
            return list(yf_rows)
        c._fetch_uw_history = uw
        c._fetch_yf_history = yf
        c._fetch_yf_targets_fallback = lambda symbol: {"target_mean": 195.73, "target_high": 255.0, "target_low": 80.0}
        return c, calls

    def test_the_card_payload_leads_with_the_unusual_whales_row(self):
        c, calls = self._client(UW_PLTR, YF_PLTR)
        p = c.get_analyst_data("PLTR", current_price=168.11)
        self.assertTrue(p["data_available"])
        self.assertEqual(p["history"][0]["new_target"], 250.0)
        self.assertEqual(p["history"][0]["time_et"], "11:28")
        self.assertEqual(p["history_sources"], [ac.UW_SOURCE, ac.YF_SOURCE])
        self.assertIn(ac.UW_SOURCE, p["source"])
        self.assertEqual((calls["uw"], calls["yf"]), (1, 1))

    def test_the_slow_leg_is_cached_for_the_half_hour_and_the_fast_leg_is_not(self):
        c, calls = self._client(UW_PLTR, YF_PLTR)
        c.get_analyst_data("PLTR", current_price=168.11)
        c._cache.pop("PLTR:16811:1")          # the 60-second payload cache expires
        c.get_analyst_data("PLTR", current_price=168.11)
        self.assertEqual(calls["yf"], 1, "Yahoo asked twice inside its TTL")
        self.assertEqual(calls["uw"], 2, "Unusual Whales must be asked again")

    def test_the_sweep_can_skip_the_fast_leg(self):
        c, calls = self._client(UW_PLTR, YF_PLTR)
        p = c.get_analyst_data("PLTR", fast=False)
        self.assertEqual(calls["uw"], 0)
        self.assertEqual(p["history"][0]["firm"], "Rosenblatt")
        self.assertEqual(p["history_sources"], [ac.YF_SOURCE])

    def test_the_headline_high_cannot_sit_below_the_new_row(self):
        # The reconcile step only folds in targets from the last 120 days,
        # so this note has to be recent RELATIVE TO THE CLOCK, not dated
        # 2026-09-11 — under the time-travel run that date is old news.
        fresh = [{**UW_PLTR[0], "timestamp": _days_ago_stamp(2)}]
        c, _ = self._client(fresh, [])
        c._fetch_yf_targets_fallback = lambda symbol: {"target_mean": 195.73, "target_high": 245.0, "target_low": 80.0}
        p = c.get_analyst_data("PLTR", current_price=168.11)
        self.assertEqual(p["history"][0]["new_target"], 250.0)
        self.assertGreaterEqual(p["targets"]["high"], 250.0)


class BoardFastLane(unittest.TestCase):
    def setUp(self):
        self._enrich = ab._enrich
        ab._enrich = lambda sym: {"ticker": sym, "sector": "Technology", "company": "Palantir Technologies",
                                  "market_cap": 4.0e11, "premarket_pct": 3.1, "vol_ratio": None,
                                  "news_count": None, "above_ma50": True, "above_ma200": True}
        with ab._LOCK:
            ab._STATE["actions"] = []
            ab._STATE["pushed"] = []
            ab._STATE["fast_last"] = None
            ab._STATE["fast_last_ts"] = 0.0
        try:
            ab._persist_path().unlink()
        except FileNotFoundError:
            pass

    def tearDown(self):
        ab._enrich = self._enrich

    def _seed_sweep_row(self):
        """A row the 8 AM Yahoo sweep would have put on the board: the 8/04
        D.A. Davidson $200 target, scored the same way production scores it."""
        row = {**YF_PLTR[1], "ticker": "PLTR", "source": ac.YF_SOURCE}
        with ab._LOCK:
            ab._STATE["actions"] = [ab.score_action(row, ab._enrich("PLTR"), 1)]

    def test_a_note_that_printed_this_morning_is_on_the_board_scored_and_pushed_once(self):
        self._seed_sweep_row()
        tape = [{**UW_PLTR[0], "timestamp": _today_et_stamp(9, 31)}]
        sent = []
        res = ab.refresh_fast_lane(["PLTR"], notify_fn=lambda t, b: sent.append((t, b)), rows=tape)
        self.assertEqual((res["ok"], res["added"], res["pushed"]), (True, 1, 1))
        board = ab.get_board()
        top = [a for a in board["actions"] if a.get("time_et")][0]
        self.assertEqual(top["ticker"], "PLTR")
        self.assertEqual(top["new_target"], 250.0)
        self.assertEqual(top["prior_target"], 200.0, "the sweep's row supplied the prior")
        self.assertEqual(top["target_change_pct"], 25.0)
        self.assertEqual(top["direction"], "bull")
        self.assertEqual(top["time_et"], "09:31")
        self.assertIn("score", top)
        self.assertEqual(board["status"]["fast_lane"]["added"], 1)
        title, body = sent[0]
        self.assertIn("PLTR", title)
        self.assertIn("DA Davidson", title)
        self.assertIn("$250", title)
        self.assertIn("from $200 (+25%)", body)
        self.assertIn("09:31 ET", body)
        # Same tape again: nothing new, nothing re-sent, and the key survived
        # to disk so a restart cannot send it either.
        res2 = ab.refresh_fast_lane(["PLTR"], notify_fn=lambda t, b: sent.append((t, b)), rows=tape)
        self.assertEqual((res2["added"], res2["pushed"], len(sent)), (0, 0, 1))
        on_disk = json.loads(ab._persist_path().read_text())
        self.assertTrue(any(k.startswith("PLTR|") for k in on_disk["pushed"]))

    def test_only_watchlist_names_and_real_actions_are_pushed(self):
        tape = [
            {**UW_PLTR[0], "timestamp": _today_et_stamp(9, 31)},                     # not on the list
            {**UW_PLTR[0], "ticker": "AAPL", "firm": "Jefferies", "action": "reiterated",
             "target": None, "timestamp": _today_et_stamp(9, 32)},                    # on the list, a reiteration
            {**UW_PLTR[0], "ticker": "MSFT", "firm": "UBS", "action": "downgraded",
             "recommendation": "hold", "target": "400", "timestamp": _today_et_stamp(9, 33)},
        ]
        sent = []
        res = ab.refresh_fast_lane(["AAPL", "MSFT"], notify_fn=lambda t, b: sent.append((t, b)), rows=tape)
        self.assertEqual(res["added"], 3)
        self.assertEqual([t for t, _ in sent], ["▼ MSFT: UBS downgraded to Hold"])

    def test_a_note_from_a_past_day_is_on_the_board_but_not_pushed(self):
        sent = []
        res = ab.refresh_fast_lane(["PLTR"], notify_fn=lambda t, b: sent.append((t, b)),
                                   rows=[UW_PLTR[0]], days=3650)
        self.assertEqual((res["added"], res["pushed"]), (1, 0))

    def test_the_daily_sweep_does_not_wipe_what_the_fast_lane_found(self):
        tape = [{**UW_PLTR[0], "timestamp": _today_et_stamp(9, 31)}]
        ab.refresh_fast_lane(["PLTR"], rows=tape)
        self.assertEqual(len(ab.get_board()["actions"]), 1)

        # The fast lane dates rows by the New York calendar; so must the
        # Yahoo rows this test hands the sweep, or after 8 PM ET the two
        # "todays" differ and one note becomes two rows for a bad reason.
        today = datetime.now(ac._et_zone()).date().isoformat()

        def sweep_with(yf_rows):
            class FakeClient:
                def get_analyst_data(self, sym, **kw):
                    return {"history": yf_rows, "source": ac.YF_SOURCE}
            real = ab.analyst_client.get_client
            ab.analyst_client.get_client = lambda: FakeClient()
            try:
                ab._STATE["scanning"] = True
                ab._scan_worker(["PLTR"], recent_days=2)
            finally:
                ab.analyst_client.get_client = real
            return ab.get_board()["actions"]

        # Yahoo has posted a DIFFERENT firm's note today and still not the
        # D.A. Davidson one: the sweep's row joins the fast lane's, and the
        # fast lane's is still there with its clock.
        rows = sweep_with([{**YF_PLTR[0], "date": today}])
        self.assertEqual(len(rows), 2, [(r["firm"], r["date"], r.get("source")) for r in rows])
        self.assertTrue(any(r.get("time_et") == "09:31" and r["new_target"] == 250.0 for r in rows))
        # Later Yahoo catches up with the same note: one row per firm per
        # day, and the row keeps the minute the fast lane saw it print.
        rows = sweep_with([{**YF_PLTR[1], "date": today, "new_target": 250.0, "prior_target": 200.0}])
        dad = [r for r in rows if ac.same_firm(r["firm"], "DA Davidson")]
        self.assertEqual(len(dad), 1, [(r["firm"], r["date"]) for r in rows])
        self.assertEqual(dad[0]["time_et"], "09:31")

    def test_an_unavailable_tape_is_reported_not_treated_as_quiet(self):
        res = ab.refresh_fast_lane(["PLTR"], rows=None) if False else None
        # fetch_uw_recent returns None without a key; refresh reports it.
        real = ab.fetch_uw_recent
        ab.fetch_uw_recent = lambda days=2: None
        try:
            res = ab.refresh_fast_lane(["PLTR"])
        finally:
            ab.fetch_uw_recent = real
        self.assertFalse(res["ok"])
        self.assertEqual(ab.get_board()["status"]["fast_lane"]["error"], "unusual whales unavailable")

    def test_merge_keeps_the_sweeps_row_and_takes_the_clock_from_the_fast_one(self):
        primary = [{"ticker": "PLTR", "firm": "D.A. Davidson", "date": "2026-08-04", "score": 40}]
        secondary = [{"ticker": "PLTR", "firm": "DA Davidson", "date": "2026-08-04",
                      "time_et": "10:15", "analyst": "Gil Luria", "score": 12},
                     {"ticker": "PLTR", "firm": "UBS", "date": "2026-08-04", "score": 30}]
        out = ab._merge_actions(primary, secondary)
        self.assertEqual(len(out), 2)
        self.assertEqual((out[0]["score"], out[0]["time_et"], out[0]["analyst"]), (40, "10:15", "Gil Luria"))


class FastLaneClock(unittest.TestCase):
    def setUp(self):
        with ab._LOCK:
            ab._STATE["fast_last_ts"] = 0.0

    def _at(self, wd, hour, minute):
        base = datetime(2026, 9, 7, tzinfo=ac._et_zone())        # a Monday
        return (base + timedelta(days=wd)).replace(hour=hour, minute=minute)

    def test_weekday_market_hours_only(self):
        self.assertTrue(ab.fast_lane_due(self._at(0, 9, 30)))
        self.assertTrue(ab.fast_lane_due(self._at(4, 4, 0)))
        self.assertFalse(ab.fast_lane_due(self._at(0, 3, 59)))
        self.assertFalse(ab.fast_lane_due(self._at(0, 20, 0)))
        self.assertFalse(ab.fast_lane_due(self._at(5, 9, 30)))    # Saturday
        self.assertFalse(ab.fast_lane_due(self._at(6, 9, 30)))    # Sunday

    def test_no_more_often_than_the_cadence(self):
        self.assertTrue(ab.fast_lane_due(self._at(0, 9, 30)))
        with ab._LOCK:
            ab._STATE["fast_last_ts"] = __import__("time").time()
        self.assertFalse(ab.fast_lane_due(self._at(0, 9, 30)))


class PushWording(unittest.TestCase):
    def test_every_figure_is_the_rows_own(self):
        row = {"ticker": "PLTR", "firm": "DA Davidson", "analyst": "Gil Luria", "direction": "bull",
               "action_class": "target_change", "new_target": 250.0, "prior_target": 200.0,
               "target_change_pct": 25.0, "new_grade": "Buy", "time_et": "11:28"}
        title, body = ab.compose_action_push(row)
        self.assertEqual(title, "▲ PLTR: DA Davidson raised target to $250")
        self.assertEqual(body, "from $200 (+25%) · Buy · Gil Luria · 11:28 ET · Unusual Whales")

    def test_an_upgrade_names_the_new_rating(self):
        row = {"ticker": "PLTR", "firm": "Deutsche Bank", "direction": "bull", "action_class": "upgrade",
               "new_target": 200.0, "new_grade": "Buy", "time_et": "06:08"}
        title, body = ab.compose_action_push(row)
        self.assertEqual(title, "▲ PLTR: Deutsche Bank upgraded to Buy")
        self.assertEqual(body, "target $200 · 06:08 ET · Unusual Whales")


if __name__ == "__main__":
    unittest.main()
