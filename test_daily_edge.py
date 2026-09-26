"""test_daily_edge.py — DAILY EDGE (v5.35).

The rules are the research, so the tests pin them: the zone in the ETF's
own sigma, the 10:30-3:30 window, GLD selling the put, the no-call gate only
where it held, SMH's two close rules, the strike the evidence was measured
on, a contract rounded away from the money with a real bid, the first
signal per ETF per day logged once and pushed once, and the forward record
graded only after its expiry closed.

Run:  python3 -m pytest test_daily_edge.py -q
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import daily_edge as de

ET = ZoneInfo("America/New_York")
MON = date(2026, 9, 28)      # a normal Monday session
FRI = date(2026, 10, 2)


def at(d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


def bars(n=40, start=100.0, step=0.01, last_down=False, end=MON):
    """Alternating +/- daily moves so sigma is known and stable."""
    out, c, d = [], start, end - timedelta(days=n * 2)
    for i in range(n):
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        if d >= end:
            break
        c *= (1 + step) if i % 2 == 0 else (1 - step)
        out.append({"date": d.isoformat() + "T12:00:00-04:00", "close": c})
    if last_down and out:
        prev = out[-2]["close"]
        out[-1]["close"] = prev * 0.99
    return out


PROF = {"sigma": 0.02, "prev_close": 100.0, "prev_day_ret": 0.005}


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    de.configure(data_dir=tmp_path)
    yield
    de.configure()


# ── the yardstick ────────────────────────────────────────────────────────────
def test_profile_uses_only_sessions_before_today():
    b = bars()
    b.append({"date": MON.isoformat() + "T12:00:00-04:00", "close": 999.0})   # today's partial bar
    p = de.profile_from_bars(b, MON)
    assert p["prev_close"] != 999.0
    assert p["prev_date"] < MON.isoformat()
    assert 0.009 < p["sigma"] < 0.012


def test_profile_needs_history():
    assert de.profile_from_bars(bars(n=10), MON) is None


def test_prev_day_down_flag():
    p = de.profile_from_bars(bars(last_down=True), MON)
    assert p["prev_day_ret"] < 0


# ── the rules ────────────────────────────────────────────────────────────────
def test_call_zone_inside_window_sells_call():
    c = de.classify("SMH", 0.5, at(MON, 12, 30), PROF)
    assert c["state"] == "SELL" and c["side"] == "call" and c["rule"] == "zone"


def test_zone_edges_are_inclusive():
    assert de.classify("SPY", 0.15, at(MON, 11, 0), PROF)["state"] == "SELL"
    assert de.classify("SPY", 0.90, at(MON, 11, 0), PROF)["state"] == "SELL"
    assert de.classify("SPY", 0.14, at(MON, 11, 0), PROF)["state"] == "NO_EDGE"
    assert de.classify("SPY", 0.91, at(MON, 11, 0), PROF)["state"] == "NO_EDGE"


def test_before_1030_waits_after_330_closes_window():
    assert de.classify("QQQ", 0.5, at(MON, 10, 0), PROF)["state"] == "WAIT"
    assert de.classify("QQQ", 0.5, at(MON, 15, 31), PROF)["state"] == "NO_EDGE"


def test_gld_sells_the_put_in_its_up_zone():
    c = de.classify("GLD", 0.5, at(MON, 13, 0), PROF)
    assert c["state"] == "SELL" and c["side"] == "put"


def test_no_call_gate_only_where_it_held():
    for s in ("SMH", "QQQ", "SPY", "IWM", "XLF"):
        assert de.classify(s, -1.2, at(MON, 14, 0), PROF)["state"] == "NO_CALL"
    assert de.classify("GLD", -1.2, at(MON, 14, 0), PROF)["state"] == "NO_EDGE"


def test_small_red_and_flat_are_skips():
    for z in (-0.5, -0.1, 0.0, 0.1):
        assert de.classify("IWM", z, at(MON, 12, 0), PROF)["state"] == "NO_EDGE"


def test_smh_second_down_day_at_close_sells_put():
    prof = dict(PROF, prev_day_ret=-0.01)
    c = de.classify("SMH", -0.3, at(MON, 15, 50), prof)
    assert c["state"] == "SELL" and c["side"] == "put" and c["rule"] == "down2"
    # not before the last fifteen minutes, and not on other ETFs
    assert de.classify("SMH", -0.3, at(MON, 15, 20), prof)["state"] == "NO_EDGE"
    assert de.classify("QQQ", -0.3, at(MON, 15, 50), prof)["state"] == "NO_EDGE"


def test_smh_up_friday_at_close_sells_put():
    c = de.classify("SMH", 0.3, at(FRI, 15, 50), PROF)
    assert c["state"] == "SELL" and c["side"] == "put" and c["rule"] == "upfri"
    assert de.classify("SMH", 0.3, at(MON, 15, 50), PROF)["state"] == "NO_EDGE"


def test_closed_outside_hours_and_weekends():
    assert de.classify("SPY", 0.5, at(MON, 9, 0), PROF)["state"] == "CLOSED"
    assert de.classify("SPY", 0.5, at(MON, 16, 5), PROF)["state"] == "CLOSED"
    assert de.classify("SPY", 0.5, at(date(2026, 9, 26), 12, 0), PROF)["state"] == "CLOSED"


# ── the strike and the contract ──────────────────────────────────────────────
def test_model_strike_is_084_sigma_over_time_left():
    k = de.model_strike(100.0, 0.02, "call", 0.0)
    assert k == pytest.approx(100 * (1 + 0.84 * 0.02))
    kp = de.model_strike(100.0, 0.02, "put", 1.0)
    assert kp == pytest.approx(100 * (1 - 0.84 * 0.02 * math.sqrt(2)))


def test_hist_loss_scales_with_multiple():
    assert de.hist_loss_per_share(100, 0.02, 0.0, 1.0) == pytest.approx(0.1119 * 2.0)
    assert de.hist_loss_per_share(100, 0.02, 0.0, 0.5) == pytest.approx(0.1119)


def chain_for(expiry: date, calls=(), puts=()):
    return {"chains": {expiry.isoformat(): {"calls": list(calls), "puts": list(puts)}}}


def test_pick_contract_rounds_away_from_money_and_needs_a_bid():
    exp = date(2026, 9, 29)
    ch = chain_for(exp, calls=[
        {"strike": 101.0, "bid": 0.80, "ask": 0.90, "delta": 0.30},
        {"strike": 102.0, "bid": 0.40, "ask": 0.45, "delta": 0.20},
        {"strike": 103.0, "bid": 0.00, "ask": 0.10, "delta": 0.10},
    ], puts=[
        {"strike": 98.0, "bid": 0.35, "ask": 0.40, "delta": -0.2},
        {"strike": 99.0, "bid": 0.70, "ask": 0.80, "delta": -0.3},
    ])
    c = de.pick_contract(ch, exp, "call", 101.6, de.DEFAULTS)
    assert c["strike"] == 102.0 and c["bid"] == 0.40
    p = de.pick_contract(ch, exp, "put", 98.6, de.DEFAULTS)
    assert p["strike"] == 98.0
    assert de.pick_contract(ch, exp, "call", 102.5, de.DEFAULTS) is None   # 103 has no bid


def test_pick_contract_refuses_wide_spread():
    exp = date(2026, 9, 29)
    ch = chain_for(exp, calls=[{"strike": 102.0, "bid": 0.10, "ask": 0.60}])
    assert de.pick_contract(ch, exp, "call", 101.5, de.DEFAULTS) is None


# ── a whole row, fired once ──────────────────────────────────────────────────
class FakeSchwab:
    def __init__(self):
        self.calls = 0

    def get_option_chain(self, sym, expiration=None, strike_count=40, to_date=None):
        self.calls += 1
        k = [{"strike": s, "bid": 0.9, "ask": 1.0, "delta": 0.2} for s in (101, 102, 103, 104)]
        return chain_for(date.fromisoformat(expiration), calls=k, puts=k)


def test_build_row_sell_prices_the_contract_and_the_record():
    now = at(MON, 12, 30)
    r = de.build_row("SMH", {"last": 101.0, "prev_close": 100.0}, PROF, now, de.DEFAULTS, FakeSchwab())
    assert r["state"] == "SELL" and r["side"] == "call"
    assert r["expiry"] == "2026-09-29"
    assert r["contract"]["strike"] >= r["model_strike"]
    assert r["hist_loss"] > 0 and r["edge"] == pytest.approx(0.9 - r["hist_loss"], abs=0.01)
    assert r["zone_lo_pct"] == pytest.approx(0.3) and r["zone_hi_pct"] == pytest.approx(1.8)


def test_build_row_skip_fetches_no_chain():
    sc = FakeSchwab()
    r = de.build_row("SPY", {"last": 100.05, "prev_close": 100.0}, PROF, at(MON, 12, 0), de.DEFAULTS, sc)
    assert r["state"] == "NO_EDGE" and sc.calls == 0 and "contract" not in r


def test_fire_logs_and_pushes_once_per_day(tmp_path):
    pushes = []
    de.configure(data_dir=tmp_path, notify_fn=lambda t, m, p=0: pushes.append((t, m)) or {"ok": True})
    now = at(MON, 12, 30)
    row = de.build_row("QQQ", {"last": 101.0, "prev_close": 100.0}, PROF, now, de.DEFAULTS, FakeSchwab())
    de._fire(row, now, de.DEFAULTS)
    row2 = de.build_row("QQQ", {"last": 101.2, "prev_close": 100.0}, PROF, now + timedelta(minutes=5),
                        de.DEFAULTS, FakeSchwab())
    de._fire(row2, now + timedelta(minutes=5), de.DEFAULTS)
    assert len(pushes) == 1 and "QQQ" in pushes[0][0]
    lines = (tmp_path / "daily_edge_log.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["side"] == "call"
    assert row2["fired"]["at"] == row["fired"]["at"]
    # remembered across a restart
    de.configure(data_dir=tmp_path)
    row3 = de.build_row("QQQ", {"last": 101.0, "prev_close": 100.0}, PROF, now, de.DEFAULTS, FakeSchwab())
    de._fire(row3, now + timedelta(minutes=9), de.DEFAULTS)
    assert len((tmp_path / "daily_edge_log.jsonl").read_text().splitlines()) == 1


# ── the forward record ──────────────────────────────────────────────────────
def test_grade_only_after_expiry_close():
    rec = {"side": "call", "strike": 102.0, "bid": 0.5, "expiry": "2026-09-29"}
    assert de.grade(rec, {}) is None
    g = de.grade(rec, {"2026-09-29": 103.0})
    assert g["breached"] and g["loss"] == 1.0 and g["pnl"] == -0.5
    g2 = de.grade({**rec, "side": "put", "strike": 98.0}, {"2026-09-29": 99.0})
    assert not g2["breached"] and g2["pnl"] == 0.5


def test_scan_end_to_end_with_fakes(tmp_path):
    de.configure(data_dir=tmp_path,
                 quotes_fn=lambda syms: {s: {"last": 101.0 if s == "SMH" else 100.0, "close_prev": 100.0}
                                         for s in syms},
                 bars_fn=lambda s: bars(step=0.02),
                 schwab_getter=lambda: FakeSchwab(),
                 now_fn=lambda: at(MON, 12, 30))
    snap = de.snapshot()
    assert snap["ok"] and len(snap["rows"]) == 6
    by = {r["symbol"]: r for r in snap["rows"]}
    assert by["SMH"]["state"] == "SELL"
    assert by["SPY"]["state"] == "NO_EDGE"
    assert snap["n_sell"] == 1 and snap["forward"]["n"] == 1


def test_no_fire_without_a_priced_contract(tmp_path):
    pushes = []
    de.configure(data_dir=tmp_path, notify_fn=lambda t, m, p=0: pushes.append(t) or {"ok": True})
    now = at(MON, 12, 30)
    row = de.build_row("SPY", {"last": 101.0, "prev_close": 100.0}, PROF, now, de.DEFAULTS, None)
    assert row["state"] == "SELL" and row["contract"] is None
    de._fire(row, now, de.DEFAULTS)
    assert not pushes and "fired" not in row
    assert not (tmp_path / "daily_edge_log.jsonl").exists()
    # a later pass with a real contract still gets the day's slot
    row2 = de.build_row("SPY", {"last": 101.0, "prev_close": 100.0}, PROF, now, de.DEFAULTS, FakeSchwab())
    de._fire(row2, now + timedelta(minutes=1), de.DEFAULTS)
    assert len(pushes) == 1 and row2["fired"]["bid"] == 0.9


def test_grades_survive_once_the_close_leaves_the_cache(tmp_path):
    de.configure(data_dir=tmp_path, now_fn=lambda: at(MON, 17, 0))
    rec = {"date": "2026-09-21", "symbol": "SMH", "side": "call", "strike": 102.0, "bid": 0.5,
           "expiry": "2026-09-22", "at": "2026-09-21T12:30:00-04:00"}
    (tmp_path / "daily_edge_log.jsonl").write_text(json.dumps(rec) + "\n")
    de._BARS["SMH"] = (MON.isoformat(), {"closes": [("2026-09-22", 103.0)]})
    f1 = de.forward()
    assert f1["graded"] == 1 and f1["breach_pct"] == 100.0
    de._BARS.clear()                      # the close is no longer in any cache
    f2 = de.forward()
    assert f2["graded"] == 1 and f2["open"] == 0
    de.configure(data_dir=tmp_path, now_fn=lambda: at(MON, 17, 0))    # and across a restart
    assert de.forward()["graded"] == 1
