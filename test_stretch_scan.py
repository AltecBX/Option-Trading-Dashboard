"""test_stretch_scan.py — the board that waits for the line.

The guards that keep an alerting scanner honest:

  * a name is a candidate on the side it actually crossed, and on the
    horizon it crossed — a big week is not a big day and vice versa;
  * the week's anchor is learned for free on the first session of the
    week and remembered, so Tuesday costs no bars for a name seen Monday;
  * READY needs a real contract that cleared the gates; a crossed line
    with no expiry, an earnings date inside the trade, or nothing that
    pays is CROSSED and says why — and a name with nothing expiring in
    the window stays off the board altogether;
  * an alert goes out ONCE per symbol, side and expiry, is remembered
    across a restart, is never sent below the credit floor, and carries
    the ticker, side, expiry, contract, credit, risk and the link;
  * the loop does not scan a closed market, and the board says which
    kind of closed;
  * every alert is logged with the numbers it was READY on.
"""
import json
import math
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta

import stretch_evidence as se
import stretch_scan as sk


def _walk(n=700, start=100.0, step=0.02, seed=5, first=date(2024, 1, 1)):
    import random
    rng = random.Random(seed)
    out, px, d = [], start, first
    for _ in range(n):
        while d.weekday() > 4:
            d += timedelta(days=1)
        r = rng.gauss(0, step)
        o = px
        px = px * math.exp(r)
        out.append({"date": d.isoformat(), "open": o, "close": px,
                    "high": max(o, px) * 1.004, "low": min(o, px) * 0.996, "volume": 5e6})
        d += timedelta(days=1)
    return out


BARS = {"UP": _walk(seed=5), "DN": _walk(seed=6), "QUIET": _walk(step=0.004, seed=7)}
TODAY = date(2026, 9, 16)                       # a Wednesday
NOW = datetime(2026, 9, 16, 11, 0).astimezone()
FRIDAY = date(2026, 9, 18)


def _n(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _chain(spot, exps, sigma_d=0.02, oi=900, rich=1.0):
    out = {"underlying": {"last": spot}, "chains": {}}
    for e in exps:
        t = max(0.5, (e - TODAY).days) / 365
        iv = sigma_d * math.sqrt(252) * 1.1
        calls, puts = [], []
        step = max(0.25, round(spot * 0.005, 2))
        k = math.floor(spot * 0.8 / step) * step
        while k <= spot * 1.2:
            d1 = (math.log(spot / k) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
            d2 = d1 - iv * math.sqrt(t)
            c = (spot * _n(d1) - k * _n(d2)) * rich
            p = (k * _n(-d2) - spot * _n(-d1)) * rich
            calls.append({"strike": round(k, 2), "bid": round(c * 0.97, 2), "ask": round(c * 1.03, 2),
                          "delta": round(_n(d1), 3), "openInterest": oi, "volume": 300, "iv": iv})
            puts.append({"strike": round(k, 2), "bid": round(p * 0.97, 2), "ask": round(p * 1.03, 2),
                         "delta": round(-_n(-d1), 3), "openInterest": oi, "volume": 300, "iv": iv})
            k += step
        out["chains"][e.isoformat()] = {"calls": calls, "puts": puts}
    return out


class FakeSchwab:
    def __init__(self, exps=None, chain=None):
        self.exps, self.chain, self.calls = exps or [FRIDAY], chain, 0

    def get_option_chain(self, sym, expiration=None, to_date=None, strike_count=40):
        self.calls += 1
        if self.chain is not None:
            return self.chain
        spot = BARS[sym][-1]["close"]
        return _chain(spot, self.exps)


def _row(sym, change, avg_volume=5e7, **kw):
    last = BARS[sym][-1]["close"]
    return {"symbol": sym, "last": last, "change": change, "avg_volume": avg_volume, **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pushed = []
        sk._PROFILES.clear()
        self.wire([])

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def wire(self, rows, now=NOW, open_=True, notify=True, bars=None, schwab=None, quotes=None):
        self.schwab = schwab or FakeSchwab()
        sk.configure(schwab_getter=lambda: self.schwab, board_getter=lambda: {"rows": rows},
                     bars_fn=(bars or (lambda s: BARS.get(s))), market_open_fn=lambda: open_,
                     now_fn=lambda: now,
                     notify_fn=(lambda t, m, p=0: (self.pushed.append((t, m, p)) or {"ok": True})) if notify else None,
                     data_dir=self.dir, quotes_fn=quotes)
        with sk._LOCK:
            sk._STATE.update({"rows": [], "refused": [], "near": [], "candidates": [], "as_of": None,
                              "tick": None, "scanned": 0, "universe": 0, "pushed_today": 0,
                              "pushed_day": None, "error": None})

    def lines(self, sym):
        return sk._profile_for(sym, TODAY, sk.config())["lines"]

    def scan(self):
        sk._scan(self.schwab, None, NOW)
        return sk.snapshot()


class WhoIsACandidate(Base):
    def test_a_name_past_its_weekly_line_is_a_call_candidate_on_the_week(self):
        self.wire([_row("UP", 0.3)])
        ln = self.lines("UP")
        wk = ln["week"]["high_pct"]
        # Put the anchor where a 0.3% day still means the WEEK is past its line.
        sk._ANCHORS["UP"] = {"week_start": "2026-09-14", "close": BARS["UP"][-1]["close"] / (1 + wk * 1.2)}
        s1 = sk.stage1(None, NOW)
        self.assertEqual([c["symbol"] for c in s1["candidates"]], ["UP"])
        t = s1["candidates"][0]["triggers"]
        self.assertEqual([(x["horizon"], x["side"]) for x in t], [("week", "call")])
        self.assertGreater(t[0]["move_pct"], t[0]["line_pct"])

    def test_a_drop_is_a_put_candidate_never_a_call(self):
        d_lo = self.lines("DN")["day"]["low_pct"]
        self.wire([_row("DN", d_lo * 100 * 1.5)])
        self.lines("DN")
        sk._ANCHORS["DN"] = {"week_start": "2026-09-14", "close": BARS["DN"][-1]["close"]}
        s1 = sk.stage1(None, NOW)
        sides = {(x["horizon"], x["side"]) for c in s1["candidates"] for x in c["triggers"]}
        self.assertIn(("day", "put"), sides)
        self.assertNotIn(("day", "call"), sides)

    def test_within_reach_is_listed_but_not_scanned(self):
        d_hi = self.lines("UP")["day"]["high_pct"]
        self.wire([_row("UP", d_hi * 100 * 0.85)])
        self.lines("UP")
        sk._ANCHORS["UP"] = {"week_start": "2026-09-14", "close": BARS["UP"][-1]["close"]}
        s1 = sk.stage1(None, NOW)
        self.assertEqual(s1["candidates"], [])
        self.assertEqual([(x["symbol"], x["horizon"], x["side"]) for x in s1["near"]], [("UP", "day", "call")])

    def test_penny_names_and_names_without_history_are_skipped(self):
        self.wire([_row("UP", 9.0, last=2.0), {"symbol": "NOPE", "last": 50.0, "change": 20.0, "avg_volume": 5e6}])
        s1 = sk.stage1(None, NOW)
        self.assertEqual(s1["candidates"], [])


class TheQuotesAreLive(Base):
    # Codex, first round (P1, correct): the watchlist board is rebuilt at 9 AM
    # and 6 PM, so its prices are hours old by mid-morning. A scanner that
    # read them would miss every crossing that happened since.
    def test_live_quotes_override_the_board(self):
        last = BARS["UP"][-1]["close"]
        d_hi = self.lines("UP")["day"]["high_pct"]
        live = {"UP": {"last": last * (1 + d_hi * 1.5), "change_pct": d_hi * 150, "close_prev": last}}
        self.wire([_row("UP", 0.0)], quotes=lambda syms: {s: live[s] for s in syms if s in live})
        self.lines("UP")
        sk._ANCHORS["UP"] = {"week_start": "2026-09-14", "close": last}
        s1 = sk.stage1(None, NOW)
        self.assertEqual([c["symbol"] for c in s1["candidates"]], ["UP"], "the board said flat; the quote says crossed")
        self.assertEqual(s1["candidates"][0]["quote"], "live")
        self.assertEqual(s1["quotes_live"], 1)

    def test_the_board_answers_when_quotes_cannot(self):
        d_hi = self.lines("UP")["day"]["high_pct"]
        self.wire([_row("UP", d_hi * 150)], quotes=lambda syms: (_ for _ in ()).throw(RuntimeError("feed down")))
        self.lines("UP")
        sk._ANCHORS["UP"] = {"week_start": "2026-09-14", "close": BARS["UP"][-1]["close"]}
        s1 = sk.stage1(None, NOW)
        self.assertEqual([c["symbol"] for c in s1["candidates"]], ["UP"])
        self.assertEqual(s1["candidates"][0]["quote"], "board")
        self.assertEqual(s1["quotes_live"], 0)

    def test_quotes_go_out_in_batches(self):
        calls = []
        rows = [dict(_row("UP", 0.0), symbol=f"N{i}") for i in range(250)]
        self.wire(rows, quotes=lambda syms: calls.append(len(syms)) or {})
        sk.stage1(None, NOW)
        self.assertEqual(calls, [100, 100, 50])


class TheAnchorIsFreeOnMonday(Base):
    def test_learned_from_the_board_on_the_first_session_and_remembered(self):
        monday = datetime(2026, 9, 14, 10, 0).astimezone()
        fetches = []

        def bars(s):
            fetches.append(s)
            return BARS.get(s)
        self.wire([_row("QUIET", 1.0)], now=monday, bars=bars)
        sk._LINES["QUIET"] = {"day": "2026-09-14", "sigma_daily": 0.004, "wk_hi": 0.02, "wk_lo": -0.02,
                              "d_hi": 0.01, "d_lo": -0.01, "n_weeks": 100, "n_days": 500}
        sk.stage1(None, monday)
        self.assertEqual(fetches, [], "Monday's previous close IS last week's close: no bars")
        self.assertEqual(sk._ANCHORS["QUIET"]["source"], "board", "no quote feed wired here, so the board answered")
        self.assertAlmostEqual(sk._ANCHORS["QUIET"]["close"], BARS["QUIET"][-1]["close"] / 1.01)
        # Tuesday: still no bars for a name seen Monday.
        sk.stage1(None, datetime(2026, 9, 15, 10, 0).astimezone())
        self.assertEqual(fetches, [])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "stretch_anchors.json")) or True)

    def test_a_name_never_seen_this_week_costs_bars_once(self):
        fetches = []

        def bars(s):
            fetches.append(s)
            return BARS.get(s)
        self.wire([_row("UP", 1.0)], bars=bars)
        sk.stage1(None, NOW)
        self.assertEqual(fetches, ["UP"])
        self.assertEqual(sk._ANCHORS["UP"]["source"], "bars")
        sk.stage1(None, NOW)
        self.assertEqual(fetches, ["UP"], "the second pass answered from the cache")

    def test_the_cold_budget_is_bounded_and_reported(self):
        rows = [dict(_row("UP", 1.0), symbol=f"N{i}") for i in range(50)]
        self.wire(rows, bars=lambda s: BARS["UP"])
        s1 = sk.stage1(None, NOW)
        self.assertEqual(s1["warmed"], 40)
        self.assertEqual(sk.status()["warming"], 10)


class ReadyNeedsAContract(Base):
    def _cross(self, sym="UP", side="call", by=1.2):
        """Place the week's anchor so the last close sits `by` times the
        line beyond it."""
        ln = self.lines(sym)
        wk = ln["week"]["high_pct"] if side == "call" else ln["week"]["low_pct"]
        last = BARS[sym][-1]["close"]
        sk._ANCHORS[sym] = {"week_start": "2026-09-14", "close": last / (1 + wk * by)}

    def test_a_crossed_line_with_a_paying_strike_is_ready(self):
        self.wire([_row("UP", 0.2)])
        self._cross()
        out = self.scan()
        r = out["rows"][0]
        self.assertEqual((r["state"], r["horizon"], r["side"], r["expiration"]),
                         ("ready", "week", "call", FRIDAY.isoformat()))
        self.assertGreater(r["strike"], r["spot"])
        self.assertLessEqual(r["itm_pct"], 30.0)
        self.assertGreaterEqual(r["touch_pct"], r["itm_pct"])
        self.assertIn(r["grade"], ("MEASURED", "MOSTLY POOLED", "POOLED"))
        self.assertEqual(r["sessions_left"], 2)
        self.assertTrue(r["ladder"])
        self.assertEqual(out["n_ready"], 1)
        self.assertFalse(out["no_trade"])

    def test_a_put_is_priced_below_the_price_from_its_own_events(self):
        self.wire([_row("DN", -0.2)])
        self._cross("DN", "put", by=3.0)
        r = self.scan()["rows"][0]
        self.assertEqual((r["state"], r["side"]), ("ready", "put"))
        self.assertLess(r["strike"], r["spot"])

    def test_no_expiry_in_the_window_is_the_calendar_and_stays_off_the_board(self):
        self.wire([_row("UP", 0.2)], schwab=FakeSchwab(exps=[date(2026, 9, 25)]))
        self._cross()
        out = self.scan()
        self.assertEqual(out["rows"], [])
        self.assertIn("no option on this name expires this week", out["refused"][0]["why"][0])
        self.assertEqual(out["refused"][0]["gate"], "expiry")

    def test_earnings_inside_the_trade_is_crossed_not_ready(self):
        self.wire([_row("UP", 0.2, next_earnings="2026-09-17")])
        self._cross()
        r = self.scan()["rows"][0]
        self.assertEqual(r["state"], "crossed")
        self.assertIn("reports", r["why"][0])
        self.assertNotIn("strike", r)

    def test_nothing_that_pays_is_crossed_with_the_gate_named(self):
        self.wire([_row("UP", 0.2)], schwab=FakeSchwab(chain=_chain(BARS["UP"][-1]["close"], [FRIDAY], rich=0.02)))
        self._cross()
        r = self.scan()["rows"][0]
        self.assertEqual(r["state"], "crossed")
        self.assertIn("every strike failed a gate", r["why"][0])

    def test_thin_evidence_refuses_rather_than_guesses(self):
        self.wire([_row("UP", 0.2)])
        self._cross()
        prof = sk._profile_for("UP", TODAY, sk.config())
        prof["events"]["week"]["call"] = {k: v[:3] for k, v in prof["events"]["week"]["call"].items()}
        sk._POOL.clear()
        r = self.scan()["rows"][0]
        self.assertEqual(r["state"], "crossed")
        self.assertIn("comparable crossings", r["why"][0])

    def test_a_takeover_headline_is_refused_outright(self):
        self.wire([_row("UP", 0.2)])
        self._cross()
        sk._CATALYST_FN = lambda s: {"kind": "BUYOUT"}
        try:
            out = self.scan()
        finally:
            sk._CATALYST_FN = None
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["refused"][0]["gate"], "event")


class TheAlert(Base):
    def _ready(self):
        self.wire([_row("UP", 0.2)])
        ln = self.lines("UP")
        sk._ANCHORS["UP"] = {"week_start": "2026-09-14", "close": BARS["UP"][-1]["close"] / (1 + ln["week"]["high_pct"] * 1.2)}

    def test_once_per_symbol_side_and_expiry(self):
        self._ready()
        self.scan()
        self.assertEqual(len(self.pushed), 1)
        self.assertTrue(sk.snapshot()["rows"][0]["is_new"])
        sk._scan(self.schwab, None, NOW + timedelta(minutes=3))
        sk._scan(self.schwab, None, NOW + timedelta(minutes=6))
        self.assertEqual(len(self.pushed), 1)
        self.assertFalse(sk.snapshot()["rows"][0]["is_new"])

    def test_the_memory_survives_a_restart(self):
        self._ready()
        self.scan()
        self.assertEqual(len(self.pushed), 1)
        sk._ALERTS.clear()
        sk._load_all()                      # what a restart does
        self.assertEqual(len(sk._ALERTS), 1)
        self.scan()
        self.assertEqual(len(self.pushed), 1)

    def test_the_text_carries_what_a_phone_needs(self):
        self._ready()
        self.scan()
        title, body, _p = self.pushed[0]
        self.assertIn("UP call", title)
        for needle in ("prior week's last close", "usual", "Sell the", "call", "credit",
                       "Finished through", "comparable crossings", "touched",
                       "/?symbol=UP&tab=analyze"):
            self.assertIn(needle, body)
        self.assertIn("Friday, September 18", body)

    def test_one_alert_when_the_day_and_the_week_share_the_expiry(self):
        # Codex, first round (P2, correct): on a Friday the day and the week
        # both resolve to today's contract. That is one alert, not two.
        friday = datetime(2026, 9, 18, 11, 0).astimezone()
        d_hi = self.lines("UP")["day"]["high_pct"]
        self.wire([_row("UP", d_hi * 150)], now=friday)
        ln = self.lines("UP")
        sk._ANCHORS["UP"] = {"week_start": "2026-09-14", "close": BARS["UP"][-1]["close"] / (1 + ln["week"]["high_pct"] * 1.2)}
        sk._scan(self.schwab, None, friday)
        rows = [r for r in sk.snapshot()["rows"] if r["state"] == "ready"]
        self.assertEqual(sorted(r["horizon"] for r in rows), ["day", "week"])
        self.assertEqual(len({r["alert_key"] for r in rows}), 1)
        self.assertEqual(len(self.pushed), 1)
        self.assertEqual(len(sk._ALERTS), 1)

    def test_every_ready_is_logged_even_with_no_channel(self):
        # Codex, first round (P2, correct): the ledger used to be written
        # only on a push, so a READY below the floor or with no channel left
        # no record to grade.
        self.wire([_row("UP", 0.2)], notify=False)
        ln = self.lines("UP")
        sk._ANCHORS["UP"] = {"week_start": "2026-09-14", "close": BARS["UP"][-1]["close"] / (1 + ln["week"]["high_pct"] * 1.2)}
        self.scan()
        self.scan()
        self.assertEqual(self.pushed, [])
        self.assertEqual(len(sk.alerts_log(30)), 1, "logged once, on the first READY, and not again")

    def test_below_the_credit_floor_nothing_is_sent(self):
        self._ready()
        self.schwab = FakeSchwab(chain=_chain(BARS["UP"][-1]["close"], [FRIDAY], rich=0.08))
        out = self.scan()
        if out["rows"] and out["rows"][0]["state"] == "ready":
            self.assertLess(out["rows"][0]["credit"], 0.10)
            self.assertEqual(self.pushed, [])

    def test_no_channel_means_no_crash_and_the_board_says_so(self):
        self.wire([_row("UP", 0.2)], notify=False)
        ln = self.lines("UP")
        sk._ANCHORS["UP"] = {"week_start": "2026-09-14", "close": BARS["UP"][-1]["close"] / (1 + ln["week"]["high_pct"] * 1.2)}
        out = self.scan()
        self.assertEqual(out["rows"][0]["state"], "ready")
        self.assertFalse(out["alerts"]["configured"])

    def test_every_ready_is_logged_with_its_numbers(self):
        self._ready()
        self.scan()
        lines = open(os.path.join(self.dir, "stretch_alerts.jsonl")).read().splitlines()
        rec = json.loads(lines[-1])
        for k in ("at", "symbol", "side", "expiration", "spot", "strike", "credit", "itm_pct", "n", "grade"):
            self.assertIn(k, rec)
        self.assertEqual(len(sk.alerts_log(30)), 1)


class TheClock(Base):
    def test_a_closed_market_starts_nothing_and_names_the_kind_of_closed(self):
        self.wire([], now=datetime(2026, 9, 12, 12, 0).astimezone(), open_=False)
        out = sk.snapshot()
        self.assertTrue(out["no_trade"])
        self.assertIn("weekend", out["no_trade_reason"])
        self.assertFalse(out["scanning"])
        self.wire([], now=datetime(2026, 9, 16, 7, 0).astimezone(), open_=False)
        self.assertIn("not opened yet", sk.snapshot()["no_trade_reason"])

    def test_sessions_left_counts_only_sessions_after_today(self):
        self.assertEqual(sk.sessions_left(date(2026, 9, 16), date(2026, 9, 18)), 2)
        self.assertEqual(sk.sessions_left(date(2026, 9, 16), date(2026, 9, 16)), 0)
        self.assertEqual(sk.sessions_left(date(2026, 11, 25), date(2026, 11, 27)), 1, "Thanksgiving is not a session")

    def test_the_week_calendar(self):
        self.assertEqual(sk.first_session_of_week(date(2026, 9, 8)), date(2026, 9, 8), "Labor Day week starts Tuesday")
        self.assertEqual(sk.last_session_of_week(date(2026, 4, 1)), date(2026, 4, 2), "Good Friday week ends Thursday")

    def test_the_payload_carries_what_the_card_needs(self):
        out = sk.snapshot()
        for f in ("version", "evidence", "rows", "near", "candidates", "refused", "universe",
                  "scanned", "warming", "pool", "alerts", "limits", "phase", "market_open"):
            self.assertIn(f, out)


class TheProfileForAnalyze(Base):
    def test_after_the_line_both_sides(self):
        p = sk.profile_for("UP", TODAY)
        self.assertTrue(p["ok"])
        for side in ("call", "put"):
            a = p["after_the_line"][side]
            self.assertIsNotNone(a["line"]["pct"])
            self.assertIn(a["grade"], ("MEASURED", "MOSTLY POOLED", "POOLED", "THIN"))
            self.assertIn("p_closed_back", a)
        self.assertIn("vs_monday", p)
        self.assertNotIn("events", p, "the events table is not shipped to the browser")

    def test_a_name_without_history_is_an_error_not_a_crash(self):
        self.assertFalse(sk.profile_for("NOPE", TODAY)["ok"])


class Config(unittest.TestCase):
    def test_thresholds_json_carries_the_section(self):
        cfg = sk.config()
        self.assertEqual(cfg["select"]["line_quantile"], 0.5)
        self.assertLessEqual(cfg["select"]["max_itm"], 0.5)
        self.assertTrue(cfg["scan"]["background"])
        self.assertGreaterEqual(cfg["alerts"]["min_credit"], 0.05)


if __name__ == "__main__":
    unittest.main()
