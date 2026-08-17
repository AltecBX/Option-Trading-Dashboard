"""Tests for gap_engine.py — the pure math of the Premarket Gap Fade &
Rebound scanner.

Ground truth is synthetic bars with KNOWN gaps and KNOWN intraday paths, so
every probability, ordering claim and exclusion is checked against a number
derivable by hand. The critical spec properties proven here:
  - current and official gap calculations are exact
  - a day that hit +9% premarket but opened +1% still exists as an event,
    and only qualifies at times AFTER it actually crossed the threshold
  - future premarket bars cannot leak into earlier decisions (mutation test)
  - splits/dividends cannot fabricate gap events
  - gap-up and gap-down logic are exact mirrors
  - same-bar target+stop resolves AGAINST the trade; daily-only data never
    claims ordering
  - no probability is emitted without its sample size
  - earnings events never contaminate non-earnings statistics
  - hysteresis prevents flip-flapping but escalations bypass it
"""

import math
import unittest
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import gap_engine as ge
from metrics import wilson_interval

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 14)


def cfg():
    c, _ = ge.config(refresh=True)
    return c


def ts_at(h, m, day=DAY):
    return int(datetime.combine(day, dtime(h, m), tzinfo=ET).timestamp() * 1000)


def mk_daily(closes_gaps, start=date(2025, 1, 6), base=100.0, vol=1_000_000):
    """Build daily bars from a list of (gap_pct, intraday spec) where the
    intraday spec is (low_off_pct, high_off_pct, close_off_pct) relative to
    that day's open. Trading days only (Mon-Fri)."""
    bars, px, d = [], base, start
    for gap, (lo_off, hi_off, cl_off) in closes_gaps:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o = px * (1.0 + gap / 100.0)
        lo = o * (1.0 + lo_off / 100.0)
        hi = o * (1.0 + hi_off / 100.0)
        cl = o * (1.0 + cl_off / 100.0)
        bars.append({"date": d.isoformat() + "T12:00:00-04:00",
                     "open": o, "high": max(hi, o, cl), "low": min(lo, o, cl),
                     "close": cl, "volume": vol})
        px = cl
        d += timedelta(days=1)
    return bars


def flat_days(n, jitter=0.0):
    """n boring days: no gap, small range, unchanged close."""
    return [(jitter, (-0.5, 0.5, 0.0))] * n


def mk_minute(day=DAY, start_hm=(9, 30), spec=None, o=100.0):
    """Minute bars from (minute_offset, low, high, close) tuples in % of o.
    First bar's open is o."""
    out = []
    for i, (lo, hi, cl) in enumerate(spec or []):
        ts = ts_at(*start_hm, day) + i * 60000
        out.append({"ts": ts, "open": o if i == 0 else out[-1]["close"],
                    "high": o * (1 + hi / 100.0), "low": o * (1 + lo / 100.0),
                    "close": o * (1 + cl / 100.0), "volume": 10_000})
    return out


class TestGapMath(unittest.TestCase):
    def test_current_pm_gap_spec_example(self):
        # spec's worked example: 154 -> 169 premarket = +9.74%
        self.assertAlmostEqual(ge.live_gap_pct(169.0, 154.0), 9.7403, places=3)

    def test_official_gap(self):
        self.assertAlmostEqual(ge.official_gap_pct(103.0, 100.0), 3.0, places=9)
        self.assertAlmostEqual(ge.official_gap_pct(97.0, 100.0), -3.0, places=9)
        self.assertIsNone(ge.official_gap_pct(0, 100.0))
        self.assertIsNone(ge.official_gap_pct(100.0, None))


class TestEventExtraction(unittest.TestCase):
    def test_official_gap_qualifies_and_small_does_not(self):
        bars = mk_daily(flat_days(30) + [(6.0, (-3.0, 1.0, -2.0))]
                        + flat_days(3) + [(2.0, (-1.0, 1.0, 0.0))])
        evs = ge.extract_daily_events(bars, cfg())
        self.assertEqual(len(evs), 1)
        e = evs[0]
        self.assertEqual(e["direction"], "up")
        self.assertAlmostEqual(e["official_gap_pct"], 6.0, places=1)
        self.assertIsNone(e["exclusion"])
        self.assertIn("OFFICIAL", e["qualified_by"])

    def test_daily_outcomes_up_gap(self):
        # up 6%, low -3% from open, high +1%, close -2%
        bars = mk_daily(flat_days(30) + [(6.0, (-3.0, 1.0, -2.0))])
        e = ge.extract_daily_events(bars, cfg())[0]
        d = e["outcomes"]["daily"]
        self.assertEqual(d["basis"], "DAILY ONLY")
        self.assertAlmostEqual(d["fav_pct"], 3.0, places=1)
        self.assertAlmostEqual(d["adv_pct"], 1.0, places=1)
        self.assertFalse(d["continued"])         # closed below open
        self.assertFalse(d["gap_filled"])        # low -3% < gap 6% -> not filled
        self.assertAlmostEqual(d["end_ret_pct"], 2.0, places=1)  # short gained 2%

    def test_gap_fill_detected(self):
        # up 4% and the low returns below prior close -> filled
        bars = mk_daily(flat_days(30) + [(4.0, (-4.5, 0.5, -4.0))])
        e = ge.extract_daily_events(bars, cfg())[0]
        self.assertTrue(e["outcomes"]["daily"]["gap_filled"])

    def test_mirror_symmetry_up_down(self):
        # identical geometry mirrored: outcomes must be numerically equal
        up = mk_daily(flat_days(30) + [(6.0, (-3.0, 1.0, -2.0))])
        dn = mk_daily(flat_days(30) + [(-6.0, (-1.0, 3.0, 2.0))])
        eu = ge.extract_daily_events(up, cfg())[0]["outcomes"]["daily"]
        ed = ge.extract_daily_events(dn, cfg())[0]["outcomes"]["daily"]
        self.assertAlmostEqual(eu["fav_pct"], ed["fav_pct"], places=6)
        self.assertAlmostEqual(eu["adv_pct"], ed["adv_pct"], places=6)
        self.assertEqual(eu["continued"], ed["continued"])
        self.assertAlmostEqual(eu["end_ret_pct"], ed["end_ret_pct"], places=6)

    def test_earnings_tagging(self):
        bars = mk_daily(flat_days(30) + [(7.0, (-2.0, 1.0, -1.0))])
        d = ge._bar_date(bars[-1])
        evs = ge.extract_daily_events(bars, cfg(), earnings_dates={d})
        self.assertEqual(evs[0]["catalyst_kind"], "EARNINGS")
        # morning-after-AMC also counts
        prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
        evs2 = ge.extract_daily_events(bars, cfg(), earnings_dates={prev})
        self.assertEqual(evs2[0]["catalyst_kind"], "EARNINGS")


class TestCorporateActions(unittest.TestCase):
    def test_declared_split_excluded(self):
        bars = mk_daily(flat_days(30) + [(-50.0, (-1.0, 1.0, 0.0))])
        d = ge._bar_date(bars[-1])
        evs = ge.extract_daily_events(bars, cfg(), split_dates={d})
        self.assertEqual(evs[0]["exclusion"], "EXCLUDE_SPLIT")
        self.assertNotIn("daily", evs[0]["outcomes"])

    def test_heuristic_split_price_and_volume_signature(self):
        # raw bars across a 2:1 forward split: price halves AND share volume
        # doubles -> excluded even without declared actions
        bars = mk_daily(flat_days(30) + [(-50.0, (-1.0, 1.0, 0.0))])
        bars[-1]["volume"] = 2_000_000     # 2x the 1M baseline
        evs = ge.extract_daily_events(bars, cfg())
        self.assertEqual(evs[0]["exclusion"], "EXCLUDE_SPLIT")

    def test_real_crash_not_mistaken_for_split(self):
        # a genuine -50% news gap with 8x volume does NOT match the
        # reciprocal volume signature -> stays a real event
        bars = mk_daily(flat_days(30) + [(-50.0, (-5.0, 5.0, -2.0))])
        bars[-1]["volume"] = 8_000_000
        evs = ge.extract_daily_events(bars, cfg())
        self.assertIsNone(evs[0]["exclusion"])

    def test_special_dividend_excluded(self):
        bars = mk_daily(flat_days(30) + [(-5.0, (-1.0, 1.0, 0.0))])
        d = ge._bar_date(bars[-1])
        prev_close = bars[-2]["close"]
        evs = ge.extract_daily_events(bars, cfg(),
                                      div_by_date={d: prev_close * 0.05})
        self.assertEqual(evs[0]["exclusion"], "EXCLUDE_DIVIDEND")

    def test_ordinary_dividend_not_excluded(self):
        bars = mk_daily(flat_days(30) + [(-5.0, (-1.0, 1.0, 0.0))])
        d = ge._bar_date(bars[-1])
        evs = ge.extract_daily_events(bars, cfg(),
                                      div_by_date={d: 0.30})
        self.assertIsNone(evs[0]["exclusion"])


class TestMinutePath(unittest.TestCase):
    def test_target_before_stop_true_ordering(self):
        # short from open 100: dips to -2.2% at minute 3 (target 2 hit),
        # squeezes to +3.5% at minute 8 (stop would hit LATER)
        spec = [(-0.2, 0.2, 0.0), (-1.0, 0.1, -0.9), (-1.5, 0.0, -1.4),
                (-2.2, 0.0, -2.0), (-1.0, 0.5, 0.0), (0.0, 1.0, 0.8),
                (0.5, 2.0, 1.8), (1.0, 3.0, 2.8), (2.0, 3.5, 3.0)]
        m = ge.minute_path_outcomes(mk_minute(spec=spec), "up", cfg())
        p = m["pairs"]["t2_s3"]
        self.assertEqual(p["outcome"], "target")
        self.assertEqual(p["minutes"], 3)
        self.assertFalse(p["intrabar"])
        # the tighter stop pair sees the later squeeze correctly
        p2 = m["pairs"]["t4_s3"]
        self.assertEqual(p2["outcome"], "stop")

    def test_same_bar_ambiguity_resolves_against_trade(self):
        # minute 1 prints BOTH -2.5% low and +3.5% high -> stop first
        spec = [(-0.2, 0.2, 0.0), (-2.5, 3.5, 0.0)]
        m = ge.minute_path_outcomes(mk_minute(spec=spec), "up", cfg())
        p = m["pairs"]["t2_s3"]
        self.assertEqual(p["outcome"], "stop")
        self.assertTrue(p["intrabar"])

    def test_mfe_mae_short(self):
        spec = [(-0.5, 0.5, 0.0), (-1.5, 1.2, -1.0), (-3.0, 0.0, -2.5)]
        m = ge.minute_path_outcomes(mk_minute(spec=spec), "up", cfg())
        self.assertAlmostEqual(m["mfe_pct"], 3.0, places=2)
        self.assertAlmostEqual(m["mae_pct"], 1.2, places=2)

    def test_mfe_mae_long_mirror(self):
        spec = [(-0.5, 0.5, 0.0), (-1.2, 1.5, 1.0), (0.0, 3.0, 2.5)]
        m = ge.minute_path_outcomes(mk_minute(spec=spec), "down", cfg())
        self.assertAlmostEqual(m["mfe_pct"], 3.0, places=2)
        self.assertAlmostEqual(m["mae_pct"], 1.2, places=2)

    def test_mae_before_target(self):
        # squeeze +1.8% first, then fade through -2.4%
        spec = [(-0.2, 1.8, 1.5), (0.0, 1.5, 0.5), (-2.4, 0.5, -2.0)]
        m = ge.minute_path_outcomes(mk_minute(spec=spec), "up", cfg())
        p = m["pairs"]["t2_s3"]
        self.assertEqual(p["outcome"], "target")
        self.assertAlmostEqual(p["mae_before_pct"], 1.8, places=2)

    def test_delayed_open_flag(self):
        spec = [(-0.5, 0.5, 0.0)] * 5
        m = ge.minute_path_outcomes(mk_minute(start_hm=(9, 42), spec=spec),
                                    "up", cfg())
        self.assertTrue(m["delayed_open"])
        m2 = ge.minute_path_outcomes(mk_minute(start_hm=(9, 30), spec=spec),
                                     "up", cfg())
        self.assertFalse(m2["delayed_open"])

    def test_time_to_levels(self):
        spec = [(-1.1, 0.2, -1.0), (-1.5, 0.0, -1.4), (-2.1, 0.0, -2.0),
                (-3.2, 0.0, -3.0)]
        m = ge.minute_path_outcomes(mk_minute(spec=spec), "up", cfg())
        self.assertEqual(m["time_to"]["1"], 0)
        self.assertEqual(m["time_to"]["2"], 2)
        self.assertEqual(m["time_to"]["3"], 3)
        self.assertNotIn("5", m["time_to"])


class TestPremarketFeatures(unittest.TestCase):
    def _pm_fade_bars(self):
        """Premarket 7:00-9:29: climbs to +9% by 8:30 then fades to +1%."""
        prev_close = 100.0
        spec = []
        # 7:00-8:30 climb 0 -> +9% (90 minutes)
        for i in range(91):
            lvl = 9.0 * i / 90.0
            spec.append((lvl - 0.1, lvl + 0.1, lvl))
        # 8:31-9:29 fade +9% -> +1%
        for i in range(1, 60):
            lvl = 9.0 - 8.0 * i / 59.0
            spec.append((lvl - 0.1, lvl + 0.1, lvl))
        bars = []
        t0 = ts_at(7, 0)
        for i, (lo, hi, cl) in enumerate(spec):
            bars.append({"ts": t0 + i * 60000,
                         "open": prev_close * (1 + (spec[i - 1][2] if i else 0) / 100),
                         "high": prev_close * (1 + hi / 100.0),
                         "low": prev_close * (1 + lo / 100.0),
                         "close": prev_close * (1 + cl / 100.0),
                         "volume": 5000})
        return bars, prev_close

    def test_pm_first_cross_time_matching(self):
        # CRITICAL (§6): the +9%-at-8:30 day that opens +1% must qualify,
        # but only for decision times at/after the actual threshold cross
        bars, pc = self._pm_fade_bars()
        cross = ge.pm_first_cross(bars, pc, 5.0)
        self.assertIsNotNone(cross)
        self.assertEqual(cross["direction"], "up")
        # +5% is reached 50/90 of the way through the climb: minute ~50
        expect_ts = ts_at(7, 50)
        self.assertLess(abs(cross["ts"] - expect_ts), 3 * 60000)
        # a decision at 7:30 must NOT see this event as qualified
        self.assertGreater(cross["ts"], ts_at(7, 30))
        # a decision at 8:30 must
        self.assertLess(cross["ts"], ts_at(8, 30))

    def test_no_future_leak_in_pm_features(self):
        # features at 8:00 must be identical whether or not the fade after
        # 8:00 exists in the array (mutation test)
        bars, pc = self._pm_fade_bars()
        at = ts_at(8, 0)
        full = ge.pm_features(bars, pc, as_of_ts_ms=at)
        truncated = ge.pm_features([b for b in bars if b["ts"] <= at], pc,
                                   as_of_ts_ms=at)
        self.assertEqual(full, truncated)
        # and the final PM high (+9%) must NOT be visible at 8:00
        self.assertLess(full["pm_high_gap_pct"], 7.0)

    def test_known_high_low_at_time(self):
        bars, pc = self._pm_fade_bars()
        f = ge.pm_features(bars, pc, as_of_ts_ms=ts_at(8, 30))
        self.assertAlmostEqual(f["pm_gap_pct"], 9.0, delta=0.3)
        self.assertAlmostEqual(f["from_pm_high_pct"], 0.0, delta=0.3)
        f2 = ge.pm_features(bars, pc, as_of_ts_ms=ts_at(9, 29))
        self.assertAlmostEqual(f2["pm_gap_pct"], 1.1, delta=0.3)
        self.assertLess(f2["from_pm_high_pct"], -6.5)
        self.assertLess(f2["trend_30m_pct"], 0)

    def test_checkpoints_compact_and_faithful(self):
        bars, pc = self._pm_fade_bars()
        cps = ge.pm_checkpoints(bars, every_min=5)
        self.assertLess(len(cps), 40)
        # cumulative high at the last checkpoint equals the true PM high
        true_hi = max(b["high"] for b in bars)
        self.assertAlmostEqual(cps[-1][2], round(true_hi, 4), places=4)
        # monotone cumulative high
        his = [c[2] for c in cps]
        self.assertEqual(his, sorted(his))


class TestAggregation(unittest.TestCase):
    def _events(self, n_fade=8, n_squeeze=2, with_minute=True):
        evs = []
        for i in range(n_fade + n_squeeze):
            fade = i < n_fade
            spec = ([(-0.3, 0.4, 0.1), (-1.2, 0.6, -1.0), (-2.3, 0.2, -2.1)]
                    if fade else
                    [(-0.2, 0.9, 0.8), (0.5, 2.2, 2.0), (1.5, 3.6, 3.4)])
            day = date(2026, 3, 2) + timedelta(days=i * 7)
            e = {"date": day.isoformat(), "direction": "up",
                 "official_gap_pct": 6.0, "prev_close": 94.0,
                 "open": 100.0, "high": 103.6 if not fade else 100.9,
                 "low": 97.7 if fade else 99.8,
                 "close": 97.9 if fade else 103.4,
                 "gap_vs_atr": 2.0, "catalyst_kind": "UNTAGGED",
                 "exclusion": None, "qualified_by": ["OFFICIAL"],
                 "outcomes": {}}
            e["outcomes"]["daily"] = ge.daily_outcomes(e)
            if with_minute:
                e["outcomes"]["minute"] = ge.minute_path_outcomes(
                    mk_minute(day=day, spec=spec), "up", cfg())
            evs.append(e)
        return evs

    def test_probabilities_carry_n_and_ci(self):
        st = ge.outcome_stats(self._events(), cfg())
        self.assertEqual(st["n"], 10)
        p2 = st["p_fav"]["2"]
        self.assertEqual(p2["n"], 10)
        self.assertEqual(p2["k"], 8)
        w = wilson_interval(8, 10)
        self.assertAlmostEqual(p2["p"], 80.0, places=1)
        self.assertAlmostEqual(p2["lo"], round(w["lo"] * 100, 1), places=1)
        # small n keeps the conservative bound well below the raw rate
        self.assertLess(p2["lo"], 60.0)

    def test_tbs_measured_from_minutes(self):
        st = ge.outcome_stats(self._events(), cfg())
        tbs = st["tbs"]
        self.assertIsNotNone(tbs)
        self.assertEqual(tbs["n"], 10)
        self.assertEqual(tbs["k"], 8)          # fades hit -2% before +3%
        self.assertEqual(st["basis"], "FULL PATH MEASURED")

    def test_daily_only_refuses_ordering(self):
        st = ge.outcome_stats(self._events(with_minute=False), cfg())
        self.assertIsNone(st["tbs"])
        self.assertEqual(st["basis"], "DAILY ONLY")
        self.assertEqual(st["tbs_basis"], "UNKNOWN / DAILY ONLY")
        # favorable rates still available from daily bars
        self.assertIsNotNone(st["p_fav"]["2"])

    def test_worst_analog_surfaced(self):
        st = ge.outcome_stats(self._events(), cfg())
        self.assertGreaterEqual(st["worst_adv_pct"], 3.5)
        self.assertTrue(st["worst_adv_date"])

    def test_empirical_ev_from_paths(self):
        st = ge.outcome_stats(self._events(), cfg())
        ev = st["ev"]
        self.assertEqual(ev["n"], 10)
        self.assertIn("MODELED", ev["basis"])
        # 8 wins ~ +2 less costs, 2 stop-outs ~ -3.45 less costs
        self.assertAlmostEqual(ev["mean_pct"], (8 * 2.0 - 2 * 3.45) / 10,
                               delta=0.35)


class TestCohort(unittest.TestCase):
    def _mixed_events(self):
        evs = []
        for i in range(30):
            earn = i % 3 == 0
            e = {"date": (date(2025, 1, 6) + timedelta(days=i * 9)).isoformat(),
                 "direction": "up", "official_gap_pct": 6.0 + (i % 5),
                 "prev_close": 94.0, "open": 100.0, "high": 101.0,
                 "low": 97.0, "close": 98.0, "gap_vs_atr": 2.0,
                 "catalyst_kind": "EARNINGS" if earn else "UNTAGGED",
                 "exclusion": None, "qualified_by": ["OFFICIAL"], "outcomes": {}}
            e["outcomes"]["daily"] = ge.daily_outcomes(e)
            evs.append(e)
        return evs

    def test_earnings_never_contaminate(self):
        evs = self._mixed_events()
        c = ge.select_cohort(evs, "up", 6.0, 2.0, is_earnings=False, cfg=cfg())
        self.assertTrue(c["events"])
        self.assertTrue(all(e["catalyst_kind"] != "EARNINGS" for e in c["events"]))
        c2 = ge.select_cohort(evs, "up", 6.0, 2.0, is_earnings=True, cfg=cfg())
        self.assertTrue(all(e["catalyst_kind"] == "EARNINGS" for e in c2["events"]))
        self.assertEqual(c2["population"], "EARNINGS")

    def test_direction_separated(self):
        evs = self._mixed_events()
        c = ge.select_cohort(evs, "down", -6.0, 2.0, False, cfg())
        self.assertEqual(c["n"], 0)

    def test_quality_labels(self):
        evs = self._mixed_events()
        c = ge.select_cohort(evs, "up", 6.0, 2.0, False, cfg())
        self.assertIn(c["quality"], ("HIGH", "MODERATE", "LOW"))
        # absurdly different setup -> smaller matched set or LOW quality
        c2 = ge.select_cohort(evs, "up", 60.0, 20.0, False, cfg())
        self.assertNotEqual(c2["quality"], "HIGH")


class TestSignals(unittest.TestCase):
    def _stats(self, p_lo=70.0, p=80.0, n=30, tbs_lo=65.0, tbs_n=30,
               mae_p90=4.0, cont_p=20.0, cont_lo=10.0):
        return {"n": n,
                "p_fav": {"2": {"p": p, "lo": p_lo, "hi": 95.0, "n": n, "k": int(n * p / 100)}},
                "tbs": {"p": 72.0, "lo": tbs_lo, "hi": 90.0, "n": tbs_n, "k": 20},
                "mae_p90_pct": mae_p90,
                "continuation": {"p": cont_p, "lo": cont_lo, "hi": 40.0, "n": n},
                }

    def test_strong_needs_everything(self):
        c = cfg()
        coh = {"quality": "HIGH"}
        s = ge.signal_for("up", self._stats(), coh, True, False, c)
        self.assertEqual(s["signal"], "STRONG FADE")
        # kill it via tail risk alone
        s2 = ge.signal_for("up", self._stats(mae_p90=9.0), coh, True, False, c)
        self.assertEqual(s2["signal"], "FADE")
        # kill it via missing tbs
        st = self._stats()
        st["tbs"] = None
        s3 = ge.signal_for("up", st, coh, True, False, c)
        self.assertEqual(s3["signal"], "FADE")
        self.assertIn("ordering unknown", s3["why"])
        # kill it via earnings catalyst
        s4 = ge.signal_for("up", self._stats(), coh, True, True, c)
        self.assertNotEqual(s4["signal"], "STRONG FADE")
        # kill it via LOW analog quality
        s5 = ge.signal_for("up", self._stats(), {"quality": "LOW"}, True, False, c)
        self.assertEqual(s5["signal"], "FADE")

    def test_probability_alone_is_not_strong(self):
        # sky-high fade rate but tiny sample -> conservative bound gates it
        c = cfg()
        st = self._stats(n=8, p=87.5, p_lo=52.0, tbs_n=8, tbs_lo=40.0)
        s = ge.signal_for("up", st, {"quality": "MODERATE"}, True, False, c)
        self.assertNotEqual(s["signal"], "STRONG FADE")

    def test_no_data_paths(self):
        c = cfg()
        s = ge.signal_for("up", self._stats(n=3), {"quality": "LOW"}, True, False, c)
        self.assertEqual(s["signal"], "NO DATA")
        s2 = ge.signal_for("up", self._stats(), {"quality": "HIGH"}, False, False, c)
        self.assertEqual(s2["signal"], "NO DATA")
        self.assertIn("live quote", s2["why"])

    def test_continuation_risk(self):
        c = cfg()
        s = ge.signal_for("up", self._stats(cont_p=70.0, cont_lo=60.0),
                          {"quality": "HIGH"}, True, False, c)
        self.assertEqual(s["signal"], "HOLD / CONTINUATION RISK")
        s2 = ge.signal_for("down", self._stats(cont_p=70.0, cont_lo=60.0),
                           {"quality": "HIGH"}, True, False, c)
        self.assertEqual(s2["signal"], "CONTINUATION LOWER RISK")

    def test_down_mirror_labels(self):
        c = cfg()
        s = ge.signal_for("down", self._stats(), {"quality": "HIGH"}, True, False, c)
        self.assertEqual(s["signal"], "STRONG REBOUND")


class TestHysteresis(unittest.TestCase):
    def test_flip_flap_suppressed(self):
        c = cfg()
        mem = ge.apply_hysteresis(None, "FADE", False, c)
        self.assertEqual(mem["displayed"], "FADE")
        # raw flips to MIXED once -> display holds
        mem = ge.apply_hysteresis(mem, "MIXED", False, c)
        self.assertEqual(mem["displayed"], "FADE")
        self.assertTrue(mem["held"])
        # raw returns to FADE -> pending cleared, still FADE
        mem = ge.apply_hysteresis(mem, "FADE", False, c)
        self.assertEqual(mem["displayed"], "FADE")
        self.assertFalse(mem["held"])
        # two consecutive MIXED -> flips
        mem = ge.apply_hysteresis(mem, "MIXED", False, c)
        self.assertEqual(mem["displayed"], "FADE")
        mem = ge.apply_hysteresis(mem, "MIXED", False, c)
        self.assertEqual(mem["displayed"], "MIXED")

    def test_escalation_bypasses(self):
        c = cfg()
        mem = ge.apply_hysteresis(None, "STRONG FADE", False, c)
        mem = ge.apply_hysteresis(mem, "NO DATA", False, c)
        self.assertEqual(mem["displayed"], "NO DATA")
        mem = ge.apply_hysteresis(None, "FADE", False, c)
        mem = ge.apply_hysteresis(mem, "MIXED", True, c)   # caller-flagged risk
        self.assertEqual(mem["displayed"], "MIXED")

    def test_raw_always_recorded(self):
        c = cfg()
        mem = ge.apply_hysteresis(None, "FADE", False, c)
        mem = ge.apply_hysteresis(mem, "MIXED", False, c)
        self.assertEqual(mem["raw"], "MIXED")
        self.assertEqual(mem["displayed"], "FADE")


class TestWhatChanged(unittest.TestCase):
    def test_diff_sentences(self):
        prev = {"pm_gap_pct": 7.8, "tbs_p": 68.0, "signal": "FADE",
                "from_pm_high_pct": -2.0, "direction": "up",
                "catalyst_kind": "UNTAGGED"}
        row = {"pm_gap_pct": 9.2, "tbs_p": 76.0, "signal": "STRONG FADE",
               "from_pm_high_pct": -0.1, "direction": "up",
               "catalyst_kind": "UNTAGGED"}
        s = ge.diff_summary(prev, row)
        self.assertIn("+7.8% → +9.2%", s)
        self.assertIn("68% → 76%", s)
        self.assertIn("PM high", s)
        self.assertIn("FADE → STRONG FADE", s)
        self.assertIsNone(ge.diff_summary(prev, prev))
        self.assertIsNone(ge.diff_summary(None, row))


class TestWilson(unittest.TestCase):
    def test_wilson_known_values(self):
        w = wilson_interval(8, 10)
        # canonical Wilson for 8/10 at 95%: (0.4902, 0.9433)
        self.assertAlmostEqual(w["lo"], 0.4902, places=3)
        self.assertAlmostEqual(w["hi"], 0.9433, places=3)
        self.assertIsNone(wilson_interval(0, 0))
        w2 = wilson_interval(0, 5)
        self.assertEqual(w2["p"], 0.0)
        self.assertGreater(w2["hi"], 0.0)


if __name__ == "__main__":
    unittest.main()
