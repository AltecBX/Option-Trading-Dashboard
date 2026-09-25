"""The Live Scanner engine (v5.29), driven with fake quotes and a fake clock.

Every trigger is exercised by walking a symbol through the prices that
should fire it — and the prices just short of it — rather than by calling
the trigger function with hand-made state, so the sweep, the per-symbol
memory, the cooldowns and the conditions are all in the path.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import live_scan as LS

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 9, 24, tzinfo=ET)        # a Thursday, a full session


def at(h, m, s=0, day=DAY):
    return day.replace(hour=h, minute=m, second=s)


def row(sym, avg=5_000_000, cap=5e10, hi52=None, lo52=None):
    return {"symbol": sym, "avg_volume": avg, "market_cap": cap,
            "high_52w": hi52, "low_52w": lo52, "sector": "Technology"}


def quote(last, prev=100.0, opn=None, high=None, low=None, vol=1_000_000,
          ext=None, ext_vol=None):
    return {"regular_last": last, "last": last, "close_prev": prev,
            "open": opn if opn is not None else prev,
            "high": high if high is not None else last,
            "low": low if low is not None else last,
            "volume": vol, "extended_last": ext, "extended_volume": ext_vol,
            "name": "Fake"}


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.quotes = {}
        self.rows = [row("AAA"), row("BBB")]
        self.pushes = []
        self.clock = at(9, 30)
        LS.configure(quotes_fn=lambda syms: {s: self.quotes[s] for s in syms if s in self.quotes},
                     universe_fn=lambda: self.rows,
                     notify_fn=lambda t, m, p=0: self.pushes.append((t, m)),
                     now_fn=lambda: self.clock, data_dir=self.tmp.name)

    def tearDown(self):
        LS.stop_scheduler()
        self.tmp.cleanup()

    def only(self, *ids):
        """Keep just these setups switched on, so one trigger is tested at a time."""
        items = LS.setups()
        for s in items:
            s["enabled"] = s["id"] in ids
        LS.save_setups(items)

    def step(self, when, **quotes):
        self.clock = when
        self.quotes.update(quotes)
        return LS.sweep(when)

    def alerts(self, setup_id=None):
        a = LS.snapshot()["alerts"]
        return [x for x in a if setup_id is None or x["setup_id"] == setup_id]


class TheClock(unittest.TestCase):
    def test_the_phases_of_a_session_day(self):
        self.assertEqual("closed", LS.phase(at(3, 59)))
        self.assertEqual("pre", LS.phase(at(4, 0)))
        self.assertEqual("pre", LS.phase(at(9, 29)))
        self.assertEqual("open", LS.phase(at(9, 30)))
        self.assertEqual("open", LS.phase(at(15, 59)))
        self.assertEqual("post", LS.phase(at(16, 0)))
        self.assertEqual("closed", LS.phase(at(20, 0)))
        self.assertEqual("closed", LS.phase(at(11, 0, day=datetime(2026, 9, 26, tzinfo=ET))))  # Saturday

    def test_volume_is_u_shaped_not_linear(self):
        # A third of the way through the day by the clock is well under
        # half of it by volume, but the first half hour is heavy.
        self.assertEqual(0.0, LS.volume_fraction(at(9, 30)))
        self.assertAlmostEqual(0.13, LS.volume_fraction(at(10, 0)), places=2)
        self.assertEqual(1.0, LS.volume_fraction(at(16, 0)))
        steps = [LS.volume_fraction(at(9, 30) + timedelta(minutes=m)) for m in range(0, 391, 15)]
        self.assertEqual(steps, sorted(steps))


class NewHighOfDay(Harness):
    def test_fires_on_a_new_high_with_its_reason(self):
        self.only("new_hod")
        self.step(at(10, 0), AAA=quote(101, high=101, vol=2_000_000))
        self.assertEqual([], self.alerts(), "the first look only sets the baseline")
        self.step(at(10, 0, 30), AAA=quote(101.5, high=101.5, vol=2_100_000))
        a = self.alerts("new_hod")
        self.assertEqual(1, len(a))
        self.assertEqual("long", a[0]["side"])
        self.assertEqual("AAA", a[0]["symbol"])
        self.assertIn("New high of the day at $101.50", a[0]["why"])

    def test_the_cooldown_holds_the_next_one(self):
        self.only("new_hod")
        self.step(at(10, 0), AAA=quote(101, high=101, vol=2_000_000))
        self.step(at(10, 0, 30), AAA=quote(102, high=102, vol=2_100_000))
        self.step(at(10, 1), AAA=quote(103, high=103, vol=2_200_000))
        self.assertEqual(1, len(self.alerts("new_hod")))
        self.step(at(10, 16), AAA=quote(104, high=104, vol=2_400_000))    # 15 min later
        self.assertEqual(2, len(self.alerts("new_hod")))

    def test_the_first_minutes_are_ignored(self):
        self.only("new_hod")
        self.step(at(9, 31), AAA=quote(101, high=101, vol=300_000))
        self.step(at(9, 31, 30), AAA=quote(102, high=102, vol=400_000))
        self.assertEqual([], self.alerts())

    def test_conditions_stop_it_and_setup_check_says_which(self):
        self.only("new_hod")
        self.rows = [row("AAA", avg=200_000)]            # too thin
        self.step(at(10, 0), AAA=quote(101, high=101, vol=2_000_000))
        self.step(at(10, 0, 30), AAA=quote(102, high=102, vol=2_100_000))
        self.assertEqual([], self.alerts())
        chk = LS.check("AAA", at(10, 0, 30))
        hod = next(r for r in chk["setups"] if r["setup_id"] == "new_hod")
        self.assertTrue(hod["live"])
        self.assertTrue(any("average volume" in b for b in hod["blocked"]), hod)
        self.assertIn("Trigger is live, but", hod["verdict"])


class SetupCheckStatus(Harness):
    def test_an_ineligible_setup_is_never_reported_live(self):
        """Codex (#417): the check sorted on the raw trigger, so a switched-
        off setup, or a pre-market one during the session, whose trigger
        happened to be live ranked above the setups that explain things.
        Each row now carries one status, and eligibility decides it first."""
        items = LS.setups()
        for s in items:
            s["enabled"] = s["id"] != "new_hod"          # new_hod switched off
        LS.save_setups(items)
        self.step(at(10, 0), AAA=quote(101, high=101, vol=2_000_000))
        self.step(at(10, 0, 30), AAA=quote(102, high=102, vol=2_100_000))
        rows = {r["setup_id"]: r for r in LS.check("AAA", at(10, 0, 30))["setups"]}
        self.assertEqual("off", rows["new_hod"]["status"])
        self.assertEqual("session", rows["premarket_mover"]["status"])
        self.assertIn(rows["gap_down_extending"]["status"], ("idle", "blocked"))
        for r in rows.values():
            self.assertIn(r["status"], ("fired", "live", "live_blocked", "blocked", "idle", "session", "off"))


class Gaps(Harness):
    def test_gap_up_holding_fires_once_a_day(self):
        self.only("gap_up_holding")
        self.step(at(9, 50), AAA=quote(104, opn=103, high=104, vol=3_000_000))
        self.step(at(9, 50, 30), AAA=quote(104.1, opn=103, high=104.1, vol=3_100_000))
        a = self.alerts("gap_up_holding")
        self.assertEqual(1, len(a))
        self.assertIn("Gapped up 3.0%", a[0]["why"])
        self.step(at(10, 30), AAA=quote(104.5, opn=103, high=104.5, vol=4_000_000))
        self.assertEqual(1, len(self.alerts("gap_up_holding")))

    def test_gap_up_fading_is_a_short(self):
        self.only("gap_up_fading")
        self.step(at(10, 0), AAA=quote(103.5, opn=103, high=104, vol=3_000_000))
        self.step(at(10, 0, 30), AAA=quote(102.0, opn=103, high=104, vol=3_100_000))
        a = self.alerts("gap_up_fading")
        self.assertEqual(1, len(a))
        self.assertEqual("short", a[0]["side"])

    def test_a_small_gap_is_not_a_gap(self):
        self.only("gap_up_holding")
        self.step(at(9, 50), AAA=quote(101.2, opn=101, vol=3_000_000))
        self.step(at(9, 50, 30), AAA=quote(101.3, opn=101, vol=3_100_000))
        self.assertEqual([], self.alerts())


class BreakoutOnVolume(Harness):
    def test_a_break_needs_the_volume_too(self):
        self.only("range_break_up")
        t = at(10, 0)
        vol = 1_000_000
        # half an hour of quiet trade between 100 and 100.4, 10k shares/30s
        for i in range(70):
            vol += 10_000
            self.step(t + timedelta(seconds=30 * i), AAA=quote(100 + (i % 5) * 0.1, vol=vol))
        # a break on thin volume: nothing
        t2 = t + timedelta(seconds=30 * 70)
        self.step(t2, AAA=quote(100.8, vol=vol + 10_000))
        self.assertEqual([], self.alerts())
        # pull back, then break again on heavy volume
        self.step(t2 + timedelta(seconds=30), AAA=quote(100.3, vol=vol + 20_000))
        self.step(t2 + timedelta(seconds=60), AAA=quote(101.2, vol=vol + 500_000))
        a = self.alerts("range_break_up")
        self.assertEqual(1, len(a))
        self.assertIn("the recent 5-minute volume", a[0]["why"])


class HistoryIsLongEnough(unittest.TestCase):
    def test_the_tick_memory_covers_the_breakout_window(self):
        """The breakout compares the last 5 minutes with the 30 before, so
        it needs 35 minutes of ticks and a margin. At 35 exactly the oldest
        tick was always just gone and the trigger could never fire."""
        need = (5 + 30) * 60 + LS.SWEEP_OPEN_S * 2
        self.assertGreaterEqual(LS.TICKS_KEEP * LS.SWEEP_OPEN_S, need)


class FiveMinuteMove(Harness):
    def test_a_fast_move_either_way(self):
        self.only("move_5m")
        t = at(11, 0)
        for i in range(11):
            self.step(t + timedelta(seconds=30 * i), AAA=quote(100 - i * 0.15, vol=2_000_000 + i))
        a = self.alerts("move_5m")
        self.assertEqual(1, len(a))
        self.assertEqual("short", a[0]["side"])
        self.assertIn("in five minutes", a[0]["why"])


class AGapInSweeps(Harness):
    def test_ten_minutes_is_not_reported_as_five(self):
        self.only("move_5m")
        self.step(at(11, 0), AAA=quote(100, vol=2_000_000))
        self.step(at(11, 10), AAA=quote(98, vol=2_100_000))     # no sweeps in between
        self.assertEqual([], self.alerts())


class PreMarket(Harness):
    def test_premarket_mover_uses_the_extended_tape(self):
        self.only("premarket_mover", "new_hod")
        self.step(at(8, 0), AAA=quote(100, ext=101, ext_vol=10_000))
        self.step(at(8, 1), AAA=quote(100, ext=103, ext_vol=80_000))
        a = self.alerts()
        self.assertEqual(["premarket_mover"], [x["setup_id"] for x in a],
                         "only the pre-market setup runs before the bell")
        self.assertEqual("long", a[0]["side"])
        self.assertIn("Pre-market +3.0%", a[0]["why"])
        r = LS.snapshot()["rankings"]
        self.assertEqual("AAA", r["pm_gainers"][0]["symbol"])
        self.assertEqual([], r["gainers"], "before the bell the regular lists describe yesterday")


class PriorDayRange(Harness):
    def test_yesterdays_range_comes_from_the_scanner_itself(self):
        self.only("above_prior_high")
        for i in range(10):
            self.step(at(15, 50) + timedelta(seconds=30 * i),
                      AAA=quote(105, high=108, low=99, vol=5_000_000 + i))
        self.assertTrue(LS._path("session_hl.json").exists())
        nxt = DAY + timedelta(days=1)
        self.step(at(10, 0, day=nxt), AAA=quote(107, prev=105, high=107, low=104, vol=3_000_000))
        self.step(at(10, 0, 30, day=nxt), AAA=quote(108.5, prev=105, high=108.5, low=104, vol=3_100_000))
        a = self.alerts("above_prior_high")
        self.assertEqual(1, len(a))
        self.assertIn("yesterday's high of $108.00", a[0]["why"])

    def test_without_a_record_it_says_so(self):
        self.step(at(10, 0), AAA=quote(107, vol=3_000_000))
        chk = LS.check("AAA", at(10, 0))
        r = next(x for x in chk["setups"] if x["setup_id"] == "above_prior_high")
        self.assertIn("not known yet", r["detail"])


class Grading(Harness):
    def test_each_alert_is_graded_and_setups_carry_a_record(self):
        self.only("new_hod")
        self.step(at(10, 0), AAA=quote(100, high=100, vol=2_000_000))
        self.step(at(10, 0, 30), AAA=quote(100.5, high=100.5, vol=2_100_000))
        self.assertEqual(1, len(self.alerts()))
        # The day's high stays 100.5, so nothing else fires while it grades.
        self.step(at(10, 16), AAA=quote(101.0, high=100.5, vol=2_500_000))
        self.step(at(10, 31), AAA=quote(99.5, high=100.5, vol=2_600_000))
        self.step(at(11, 1), AAA=quote(102.0, high=100.5, vol=2_700_000))
        self.assertEqual(1, len(self.alerts()))
        g = self.alerts()[0]["grade"]
        self.assertAlmostEqual(0.5, g["r15"], places=1)
        self.assertAlmostEqual(-1.0, g["r30"], places=1)
        self.assertTrue(g["done"])
        rec = LS.track_records(fresh=True)["new_hod"]
        self.assertEqual(1, rec["n"])
        self.assertEqual(0, rec["followed"], "down at 30 minutes is not follow-through")

    def test_a_short_is_graded_in_its_own_direction(self):
        self.only("new_lod")
        self.step(at(10, 0), AAA=quote(100, low=100, vol=2_000_000))
        self.step(at(10, 0, 30), AAA=quote(99.5, low=99.5, vol=2_100_000))
        self.step(at(10, 31), AAA=quote(98.5, low=98.5, vol=2_600_000))
        # oldest first: the list is newest-first, and a later new low fires too
        g = [a for a in self.alerts() if a["setup_id"] == "new_lod"][-1]["grade"]
        self.assertGreater(g["r30"], 0, "a short that falls followed through")


def ms(dt):
    return int(dt.timestamp() * 1000)


class PreMarketUsesTheNewestTrade(Harness):
    """Jerry, with the Pre-market gainers list at 8:55: "Why is this stale.
    It should have the realtime on it." AKAM read $133.91, +21.28% on the
    list while the sidebar had it live at $126.37, +14.45%. The list read
    Schwab's EXTENDED price unconditionally; the newest pre-market trade
    was in the regular quote, and the extended field held an older print.
    The sidebar picks by trade time. So does the scanner now."""

    def aapl(self, reg, reg_t, ext, ext_t, vol=900_000, ext_vol=300_000, prev=110.41):
        q = quote(reg, prev=prev, vol=vol, ext=ext, ext_vol=ext_vol)
        q.update({"regular_trade_ms": ms(reg_t), "extended_trade_ms": ms(ext_t)})
        return q

    def test_the_newer_regular_print_wins_over_an_old_extended_one(self):
        yesterday_post = at(19, 30, day=DAY - timedelta(days=1))
        self.step(at(8, 54), AAA=self.aapl(126.0, at(8, 54), 133.91, yesterday_post))
        self.step(at(8, 55), AAA=self.aapl(126.37, at(8, 55), 133.91, yesterday_post))
        top = LS.snapshot()["rankings"]["pm_gainers"][0]
        self.assertEqual("AAA", top["symbol"])
        self.assertAlmostEqual(126.37, top["last"], places=2)
        self.assertAlmostEqual(14.45, top["pm_change_pct"], places=1)

    def test_a_newer_extended_print_wins_the_other_way(self):
        self.step(at(8, 54), AAA=self.aapl(110.41, at(16, 0, day=DAY - timedelta(days=1)), 118.0, at(8, 54)))
        top = LS.snapshot()["rankings"]["pm_gainers"][0]
        self.assertAlmostEqual(118.0, top["last"], places=2)

    def test_nothing_traded_today_is_not_a_premarket_mover(self):
        # Both prints are from yesterday: it has not traded pre-market yet.
        y = DAY - timedelta(days=1)
        self.step(at(8, 55), AAA=self.aapl(110.41, at(16, 0, day=y), 133.91, at(19, 30, day=y)))
        self.assertEqual([], LS.snapshot()["rankings"]["pm_gainers"])

    def test_premarket_sweeps_are_every_30_seconds(self):
        self.assertEqual(30, LS.SWEEP_PRE_S)


class TheBrokerClientCarriesBothPrintTimes(unittest.TestCase):
    """The scanner can only pick the newest print if the broker client
    hands over BOTH prints' times. Drives the real SchwabClient.get_quotes
    with a Schwab-shaped response (AKAM at 8:55, Jerry's case) instead of
    trusting a fake to have the fields."""

    def test_get_quotes_keeps_the_regular_and_extended_trade_times(self):
        import threading as _th
        import schwab_client as sc
        c = object.__new__(sc.SchwabClient)
        c._lock, c._cache = _th.RLock(), {}
        t_reg = ms(at(8, 55))
        t_ext = ms(at(19, 30, day=DAY - timedelta(days=1)))
        c._get = lambda url, params: {"AKAM": {
            "quote": {"lastPrice": 126.37, "closePrice": 110.41, "tradeTime": t_reg,
                      "totalVolume": 900_000, "openPrice": 0, "highPrice": 0, "lowPrice": 0},
            "extended": {"lastPrice": 133.91, "tradeTime": t_ext, "totalVolume": 300_000},
            "reference": {"description": "Akamai"}}}
        q = c.get_quotes(["AKAM"])["AKAM"]
        self.assertEqual(t_reg, q["regular_trade_ms"])
        self.assertEqual(t_ext, q["extended_trade_ms"])
        self.assertEqual(126.37, q["last"], "the client already picks the newer print")
        price, _vol = LS._premarket_print(q, at(8, 55))
        self.assertEqual(126.37, price, "and now the scanner does too")


class PreMarketGrading(Harness):
    def test_a_premarket_alert_is_graded_on_the_premarket_tape(self):
        """Codex (#416): before the bell `last` is cleared by design, so a
        pre-market alert went ungraded until 9:30 and then every horizon
        got the opening price."""
        self.only("premarket_mover")
        self.step(at(8, 0), AAA=quote(100, ext=101, ext_vol=10_000))
        self.step(at(8, 1), AAA=quote(100, ext=103, ext_vol=80_000))
        self.step(at(8, 16), AAA=quote(100, ext=104, ext_vol=90_000))
        self.step(at(8, 31), AAA=quote(100, ext=102, ext_vol=95_000))
        g = self.alerts()[0]["grade"]
        self.assertAlmostEqual((104 / 103 - 1) * 100, g["r15"], places=1)
        self.assertAlmostEqual((102 / 103 - 1) * 100, g["r30"], places=1)


class RestartKeepsYesterday(Harness):
    def test_a_restart_in_the_session_keeps_yesterdays_range(self):
        """Codex (#416): the day's own record overwrote yesterday's in the
        one file, so a restart mid-session lost the prior-day range."""
        self.only("above_prior_high")
        for i in range(10):
            self.step(at(15, 50) + timedelta(seconds=30 * i),
                      AAA=quote(105, high=108, low=99, vol=5_000_000 + i))
        nxt = DAY + timedelta(days=1)
        for i in range(10):                     # today's record gets written
            self.step(at(10, 0, day=nxt) + timedelta(seconds=30 * i),
                      AAA=quote(106, prev=105, high=107, low=104, vol=3_000_000 + i))
        LS.configure(quotes_fn=lambda syms: {s: self.quotes[s] for s in syms if s in self.quotes},
                     universe_fn=lambda: self.rows, now_fn=lambda: self.clock,
                     data_dir=self.tmp.name)
        self.only("above_prior_high")
        self.step(at(10, 30, day=nxt), AAA=quote(107, prev=105, high=107, low=104, vol=3_500_000))
        self.step(at(10, 30, 30, day=nxt), AAA=quote(108.5, prev=105, high=108.5, low=104, vol=3_600_000))
        a = self.alerts("above_prior_high")
        self.assertEqual(1, len(a), "yesterday's high survived the restart")
        self.assertIn("$108.00", a[0]["why"])


class Restart(Harness):
    def test_a_restart_does_not_fire_a_once_a_day_alert_again(self):
        self.only("gap_up_holding")
        self.step(at(9, 50), AAA=quote(104, opn=103, vol=3_000_000))
        self.step(at(9, 50, 30), AAA=quote(104.1, opn=103, vol=3_100_000))
        self.assertEqual(1, len(self.alerts()))
        LS.configure(quotes_fn=lambda syms: {s: self.quotes[s] for s in syms if s in self.quotes},
                     universe_fn=lambda: self.rows, now_fn=lambda: self.clock,
                     data_dir=self.tmp.name)
        self.step(at(10, 0), AAA=quote(104.2, opn=103, vol=3_200_000))
        self.step(at(10, 0, 30), AAA=quote(104.3, opn=103, vol=3_300_000))
        self.assertEqual(1, len(self.alerts("gap_up_holding")))


class Rankings(Harness):
    def test_lists_are_ordered_and_skip_untradeable_names(self):
        self.rows = [row("AAA"), row("BBB"), row("TINY", avg=50_000)]
        self.step(at(11, 0), AAA=quote(103), BBB=quote(98), TINY=quote(150))
        r = LS.snapshot()["rankings"]
        self.assertEqual(["AAA", "BBB"], [x["symbol"] for x in r["gainers"]])
        self.assertEqual("BBB", r["losers"][0]["symbol"])
        self.assertNotIn("TINY", [x["symbol"] for x in r["gainers"]])


class SetupsAreData(Harness):
    def test_unknown_triggers_are_refused_by_name_and_numbers_clamped(self):
        items = LS.setups()
        items[0]["params"]["after_min"] = 9999
        items.append({"name": "Mystery", "trigger": "telepathy"})
        out = LS.save_setups(items)
        self.assertEqual(["Mystery"], out["refused"])
        self.assertEqual(120, out["setups"][0]["params"]["after_min"])
        on_disk = json.loads(LS._path("setups.json").read_text())
        self.assertEqual(len(out["setups"]), len(on_disk))

    def test_a_list_with_nothing_valid_never_wipes_the_setups(self):
        before = LS.setups()
        out = LS.save_setups([{"name": "x", "trigger": "nope"}])
        self.assertFalse(out["ok"])
        self.assertEqual(before, LS.setups())
        self.assertFalse(LS.save_setups([])["ok"])
        self.assertEqual(before, LS.setups())

    def test_a_duplicate_keeps_both(self):
        items = LS.setups()
        dup = dict(items[0], name="New high of day, tight")
        out = LS.save_setups(items + [dup])
        ids = [s["id"] for s in out["setups"]]
        self.assertEqual(len(ids), len(set(ids)))


class Push(Harness):
    def test_a_setup_can_push_and_the_phone_is_rate_limited(self):
        items = LS.setups()
        for s in items:
            s["enabled"] = s["id"] == "new_hod"
            s["notify"] = s["id"] == "new_hod"
            s["cooldown_s"] = 0
        LS.save_setups(items)
        self.rows = [row(f"S{i}") for i in range(30)]
        t = at(10, 0)
        self.step(t, **{f"S{i}": quote(100, high=100, vol=2_000_000) for i in range(30)})
        self.step(t + timedelta(seconds=30), **{f"S{i}": quote(101, high=101, vol=2_000_000) for i in range(30)})
        self.assertEqual(30, len(self.alerts()))
        self.assertEqual(LS.PUSH_PER_HOUR, len(self.pushes))
        self.assertIn("New high of day", self.pushes[0][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
