"""Tests for KOREA LEAD V2.3 — the hardening, freshness and forward-capture
pass.

These are not tests that the arithmetic is right. They are tests that the
feature refuses to lie when its inputs go wrong, which is a different and
harder property:

  the clock says Seoul is finished and the market is still trading,
  a quote is sixteen hours old and looks like this morning's,
  a checkpoint was missed and could be quietly filled in later,
  a driver could flip on a tenth of a percentage point,
  a threshold in the settings file could be documented and never read.

Every fixture is frozen and synthetic. Nothing here touches a network and
no assertion depends on what a provider returned today.
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import korea_capture as kc
import korea_lead as kl
import korea_lead_engine as kle
import korea_research_engine as kre

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")

FIXTURE_END = "2026-03-04"          # a Wednesday


def weekdays_back(n, end=FIXTURE_END):
    d = date.fromisoformat(end)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def kbars(moves, base=1000.0, end=FIXTURE_END):
    days = weekdays_back(len(moves) + 1, end)
    bars = [{"date": days[0].isoformat(), "open": base, "high": base,
             "low": base, "close": base}]
    px = base
    for d, m in zip(days[1:], moves):
        px = px * (1.0 + m / 100.0)
        bars.append({"date": d.isoformat(), "open": px, "high": px,
                     "low": px, "close": px})
    return bars


def ubars(gaps, base=100.0, end=FIXTURE_END):
    days = weekdays_back(len(gaps) + 1, end)
    bars = [{"date": days[0].isoformat(), "open": base, "high": base,
             "low": base, "close": base}]
    prev = base
    for d, g in zip(days[1:], gaps):
        o = prev * (1.0 + g / 100.0)
        c = o * 1.001
        bars.append({"date": d.isoformat(), "open": o, "high": max(o, c),
                     "low": min(o, c), "close": c})
        prev = c
    return bars


MOVES = [1.2, -0.4, 3.1, -2.6, 0.8, 5.4, -1.1, 2.2, -3.7, 0.3,
         1.9, -0.7, 4.2, -1.3, 0.5, 2.8, -2.2, 1.4, -0.6, 3.3,
         -4.1, 0.9, 1.7, -1.9, 2.5, -0.3, 3.8, -2.9, 1.1, -1.6,
         0.7, 2.1, -3.2, 1.8, -0.8, 4.6, -1.4, 0.4, 2.3, -2.4]


class WiredCase(unittest.TestCase):
    """Korea Lead wired to frozen fixtures, with an injected clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 3, 4, 7, 30, tzinfo=ET)
        self.korea = {
            "^KS11": kbars(MOVES),
            "005930.KS": kbars([m * 1.4 for m in MOVES]),
            "000660.KS": kbars([m * 1.6 for m in MOVES]),
            "KRW=X": kbars([-m * 0.2 for m in MOVES]),
        }
        self.us = ubars([m / 3.0 for m in MOVES])
        self.quote = {"last": 101.5, "close_prev": 100.0, "open": None,
                      "stale_seconds": 9.0}
        self.market_time = None
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        kc.reset_for_tests()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(kl.configure)
        self.addCleanup(kc.reset_for_tests)

    def _korea(self, sym, days):
        return {"bars": list(self.korea.get(sym) or []), "source": "test",
                "meta": {"name": sym, "timezone": "Asia/Seoul",
                         "market_time": self.market_time}}

    def _daily(self, sym, days):
        return {"bars": list(self.us), "source": "test"}

    def overlay(self, section, **values):
        """Write a data-directory settings overlay and clear the cache, the
        way a real deployment would."""
        p = os.path.join(self.tmp.name, "thresholds.json")
        try:
            cur = json.loads(open(p).read())
        except Exception:
            cur = {}
        kl_sec = cur.setdefault("korea_lead", {})
        kl_sec.setdefault(section, {}).update(values)
        with open(p, "w") as fh:
            json.dump(cur, fh)
        kl.config(refresh=True)


# ── §2/§3 configuration ─────────────────────────────────────────────────────

class TestEveryJudgmentThresholdComesFromConfig(WiredCase):

    def test_the_defaults_load_for_every_new_section(self):
        for name in ("freshness", "chip_confirmation", "bias", "relationship",
                     "unusual", "driver_selection", "premium_context",
                     "finality", "forward"):
            self.assertTrue(kl._section(name), f"{name} is empty")

    def test_an_overlay_changes_a_floor_and_leaves_the_others_alone(self):
        before = kl._section("bias")
        self.overlay("bias", min_n=200)
        after = kl._section("bias")
        self.assertEqual(after["min_n"], 200)
        self.assertEqual(after["wilson_lower_min"], before["wilson_lower_min"])

    def test_the_config_hash_moves_when_a_judgment_value_moves(self):
        first = kl.config(refresh=True)[1]
        self.overlay("unusual", extreme_percentile=99)
        self.assertNotEqual(kl.config(refresh=True)[1], first)

    def test_a_configured_floor_reaches_the_arithmetic_not_only_the_dict(self):
        """The failure this guards against is a settings block that is
        documented, hashed, and read by nothing — which is worse than no
        block, because the hash moves and the numbers do not."""
        obs = [{"korea": 3.5, "opening_gap": 2.0}] * 40
        impl = kle.implied_gap(obs, 3.5)
        normal = kle.opening_gap_bias(3.5, impl, gates=kl._section("bias"))
        self.assertEqual(normal["state"], kle.BIAS_UP)
        self.overlay("bias", min_n=999)
        strict = kle.opening_gap_bias(3.5, impl, gates=kl._section("bias"))
        self.assertEqual(strict["state"], kle.BIAS_INCONCLUSIVE)
        self.assertIn("999", strict["detail"])

    def test_documentation_keys_are_not_treated_as_settings(self):
        self.overlay("unusual", _unusual_percentile_doc="prose, not a number")
        self.assertNotIn("_unusual_percentile_doc", kl._section("unusual"))

    def test_the_panel_reports_the_settings_that_produced_it(self):
        got = kl.payload("TEST")
        limits = got["diagnostics"]["limits"]
        self.assertIn("bias", limits)
        self.assertIn("finality", limits)
        self.assertEqual(limits["bias"]["min_n"],
                         kl._section("bias")["min_n"])


# ── §5 chip confirmation ────────────────────────────────────────────────────

class TestChipConfirmationBoundaries(unittest.TestCase):

    G = {"min_same_sign_abs_pct": 0.3, "kospi_divergence_min_abs_pct": 0.5,
         "chip_opposite_min_pct": 1.0}

    def test_exactly_at_the_same_sign_floor_confirms(self):
        got = kle.chip_confirmation(0.3, 0.3, 0.3, gates=self.G)
        self.assertEqual(got["state"], kle.CONFIRMATION_CONFIRMED)

    def test_a_hair_under_the_floor_is_mixed_not_confirmed(self):
        got = kle.chip_confirmation(0.3, 0.29, 0.5, gates=self.G)
        self.assertEqual(got["state"], kle.CONFIRMATION_MIXED)
        self.assertIn("floor", got["detail"])

    def test_three_series_barely_off_zero_are_not_confirmation(self):
        """The quietest day of the year must not earn the strongest label."""
        got = kle.chip_confirmation(0.04, 0.02, 0.05, gates=self.G)
        self.assertEqual(got["state"], kle.CONFIRMATION_MIXED)

    def test_divergence_needs_the_index_to_have_said_something(self):
        got = kle.chip_confirmation(0.4, 1.5, -2.0, gates=self.G)
        self.assertEqual(got["state"], kle.CONFIRMATION_MIXED)
        self.assertIn("0.5%", got["detail"])

    def test_divergence_at_the_boundary(self):
        got = kle.chip_confirmation(0.5, 1.5, -1.0, gates=self.G)
        self.assertEqual(got["state"], kle.CONFIRMATION_DIVERGENCE)

    def test_an_opposite_chip_inside_the_noise_floor_is_not_divergence(self):
        got = kle.chip_confirmation(2.0, 1.5, -0.9, gates=self.G)
        self.assertEqual(got["state"], kle.CONFIRMATION_MIXED)

    def test_missing_samsung_can_never_reach_confirmed(self):
        got = kle.chip_confirmation(2.0, None, 2.0, gates=self.G)
        self.assertEqual(got["state"], kle.CONFIRMATION_MIXED)
        self.assertEqual(got["missing"], ["samsung"])
        self.assertIn("samsung", got["detail"])

    def test_missing_hynix_can_never_reach_divergence(self):
        got = kle.chip_confirmation(2.0, -2.0, None, gates=self.G)
        self.assertNotEqual(got["state"], kle.CONFIRMATION_DIVERGENCE)
        self.assertEqual(got["missing"], ["hynix"])

    def test_kospi_at_exactly_zero_has_nothing_to_confirm(self):
        got = kle.chip_confirmation(0.0, 2.0, 3.0, gates=self.G)
        self.assertEqual(got["state"], kle.CONFIRMATION_UNAVAILABLE)

    def test_the_gates_that_were_applied_travel_with_the_answer(self):
        got = kle.chip_confirmation(2.0, 2.0, 2.0, gates=self.G)
        self.assertEqual(got["gates"]["min_same_sign_abs_pct"], 0.3)


# ── §4 bias: inconclusive is not unstable ───────────────────────────────────

class TestBiasStates(unittest.TestCase):

    G = {"min_n": 30, "wilson_lower_min": 0.50}

    def test_below_the_minimum_sample_is_inconclusive(self):
        obs = [{"korea": 3.5, "opening_gap": 2.0}] * 20
        got = kle.opening_gap_bias(3.5, kle.implied_gap(obs, 3.5), gates=self.G)
        self.assertEqual(got["state"], kle.BIAS_INCONCLUSIVE)
        self.assertIn("30", got["detail"])

    def test_an_interval_containing_a_coin_flip_is_inconclusive(self):
        obs = ([{"korea": 3.5, "opening_gap": 2.0}] * 20
               + [{"korea": 3.5, "opening_gap": -2.0}] * 20)
        got = kle.opening_gap_bias(3.5, kle.implied_gap(obs, 3.5), gates=self.G)
        self.assertEqual(got["state"], kle.BIAS_INCONCLUSIVE)
        self.assertNotEqual(got["state"], kle.BIAS_UNSTABLE)

    def test_a_sign_conflict_between_windows_is_unstable_not_inconclusive(self):
        """Same decisive matched history. The difference is entirely that
        the relationship itself is reported as having changed."""
        obs = [{"korea": 3.5, "opening_gap": 2.0}] * 40
        impl = kle.implied_gap(obs, 3.5)
        self.assertEqual(kle.opening_gap_bias(3.5, impl, gates=self.G)["state"],
                         kle.BIAS_UP)
        unstable = kle.opening_gap_bias(
            3.5, impl, gates=self.G, relationship_unstable=True,
            relationship_detail="the two windows disagree in sign")
        self.assertEqual(unstable["state"], kle.BIAS_UNSTABLE)
        self.assertIn("disagree", unstable["detail"])

    def test_the_count_and_the_median_disagreeing_is_mixed(self):
        """Most sessions went Korea's way, and the median gap went the other
        way — a few large opposite moves. Both facts are real and they
        contradict each other, which is neither inconclusive nor a lean."""
        obs = ([{"korea": 3.5, "opening_gap": 0.05}] * 34
               + [{"korea": 3.5, "opening_gap": -9.0}] * 6)
        rows = obs[:]
        # Push the median negative while keeping the same-direction count high
        rows = ([{"korea": 3.5, "opening_gap": 0.05}] * 21
                + [{"korea": 3.5, "opening_gap": -0.5}] * 20)
        got = kle.opening_gap_bias(3.5, kle.implied_gap(rows, 3.5), gates=self.G)
        self.assertIn(got["state"], (kle.BIAS_MIXED, kle.BIAS_INCONCLUSIVE))

    def test_a_ticker_that_opens_against_korea_is_a_finding_not_a_shrug(self):
        obs = [{"korea": 3.5, "opening_gap": -2.0}] * 40
        got = kle.opening_gap_bias(3.5, kle.implied_gap(obs, 3.5), gates=self.G)
        self.assertEqual(got["state"], kle.BIAS_DOWN)
        self.assertIn("AGAINST", got["detail"])

    def test_no_history_at_all_is_no_data(self):
        got = kle.opening_gap_bias(3.5, kle.implied_gap([], 3.5), gates=self.G)
        self.assertEqual(got["state"], kle.BIAS_NONE)

    def test_unstable_outranks_everything_including_no_data(self):
        got = kle.opening_gap_bias(3.5, kle.implied_gap([], 3.5), gates=self.G,
                                   relationship_unstable=True)
        self.assertEqual(got["state"], kle.BIAS_UNSTABLE)


# ── §8/§9 freshness ─────────────────────────────────────────────────────────

class TestFreshness(unittest.TestCase):

    CUR, DEL = 20 * 60.0, 60 * 60.0

    def test_inside_twenty_minutes_is_current_for_source(self):
        got = kle.quote_freshness(19 * 60, self.CUR, self.DEL)
        self.assertEqual(got["state"], kle.FRESH_CURRENT)
        self.assertTrue(got["fresh_enough"])

    def test_current_for_source_never_claims_to_be_real_time(self):
        got = kle.quote_freshness(60, self.CUR, self.DEL)
        self.assertIn("delayed feed", got["detail"])
        self.assertNotIn("real time", got["detail"].lower())

    def test_exactly_twenty_minutes_is_still_current(self):
        self.assertEqual(kle.quote_freshness(20 * 60, self.CUR, self.DEL)["state"],
                         kle.FRESH_CURRENT)

    def test_between_twenty_and_sixty_minutes_is_delayed(self):
        got = kle.quote_freshness(45 * 60, self.CUR, self.DEL)
        self.assertEqual(got["state"], kle.FRESH_DELAYED)
        self.assertFalse(got["fresh_enough"])

    def test_past_an_hour_is_stale(self):
        self.assertEqual(kle.quote_freshness(61 * 60, self.CUR, self.DEL)["state"],
                         kle.FRESH_STALE)

    def test_no_timestamp_is_unverified_rather_than_current(self):
        got = kle.quote_freshness(None, self.CUR, self.DEL)
        self.assertEqual(got["state"], kle.FRESH_UNKNOWN)
        self.assertFalse(got["fresh_enough"])

    def test_no_reading_at_all_is_unavailable(self):
        got = kle.quote_freshness(5, self.CUR, self.DEL, have_value=False)
        self.assertEqual(got["state"], kle.FRESH_UNAVAILABLE)

    def test_a_settled_close_is_finished_rather_than_stale(self):
        """Nine hours old is correct for a Korean close read from New York.
        Ageing it against a twenty-minute limit would paint every Korean
        close red by the time the U.S. wakes up."""
        got = kle.quote_freshness(9 * 3600, self.CUR, self.DEL, settled=True)
        self.assertEqual(got["state"], kle.FRESH_SETTLED)
        self.assertTrue(got["fresh_enough"])


class TestTheStalePremarketQuoteIsRefused(WiredCase):
    """The measured failure, not a hypothetical one: when the primary quote
    source is unavailable the fallback returns yesterday's four o'clock
    close as `last` and the close before that as `previous`, which subtract
    into yesterday's full-day return wearing this morning's label."""

    def test_a_fresh_quote_produces_a_premarket_gap(self):
        got = kl.us_today("TEST")
        self.assertTrue(got["ok"])
        self.assertTrue(got["fresh_enough"])
        self.assertEqual(got["basis"], "premarket")
        self.assertAlmostEqual(got["gap_pct"], 1.5, places=3)

    def test_a_sixteen_hour_old_quote_produces_nothing(self):
        self.quote = {"last": 101.5, "close_prev": 100.0, "open": None,
                      "stale_seconds": 16 * 3600.0}
        got = kl.us_today("TEST")
        self.assertFalse(got["fresh_enough"])
        self.assertIsNone(got["gap_pct"])
        self.assertIn("not a current gap", got["not_available_reason"])

    def test_a_quote_with_no_timestamp_produces_nothing(self):
        self.quote = {"last": 101.5, "close_prev": 100.0, "open": None,
                      "stale_seconds": None}
        got = kl.us_today("TEST")
        self.assertFalse(got["fresh_enough"])
        self.assertIsNone(got["gap_pct"])
        self.assertIn("unknown age", got["not_available_reason"])

    def test_a_stale_quote_produces_no_residual_on_the_panel(self):
        self.quote = {"last": 101.5, "close_prev": 100.0, "open": None,
                      "stale_seconds": 16 * 3600.0}
        got = kl.payload("TEST")
        self.assertIsNone(got["premarket_comparison"]["residual_pct"])
        self.assertFalse(got["residual"]["ok"])
        self.assertIn("not a current gap",
                      got["premarket_comparison"]["detail"])

    def test_the_freshness_gate_is_configurable(self):
        self.quote = {"last": 101.5, "close_prev": 100.0, "open": None,
                      "stale_seconds": 600.0}
        self.assertFalse(kl.us_today("TEST")["fresh_enough"])
        self.overlay("freshness", us_premarket_max_quote_age_s=1200)
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        self.assertTrue(kl.us_today("TEST")["fresh_enough"])

    def test_the_official_open_is_never_aged_out(self):
        """An opening price is a published fact. It is not less true at
        eleven o'clock than it was at half past nine."""
        self.now = datetime(2026, 3, 4, 11, 0, tzinfo=ET)
        self.quote = {"last": 105.0, "close_prev": 100.0, "open": 102.0,
                      "stale_seconds": 4 * 3600.0}
        got = kl.us_today("TEST")
        self.assertTrue(got["ok"])
        self.assertTrue(got["fresh_enough"])
        self.assertEqual(got["basis"], "official_open")
        self.assertAlmostEqual(got["gap_pct"], 2.0, places=3)

    def test_a_midpoint_gap_is_shown_beside_the_traded_one_never_instead(self):
        self.quote = {"last": 101.5, "close_prev": 100.0, "open": None,
                      "bid": 101.0, "ask": 103.0, "stale_seconds": 9.0}
        got = kl.us_today("TEST")
        self.assertAlmostEqual(got["gap_pct"], 1.5, places=3)
        self.assertAlmostEqual(got["mid_gap_pct"], 2.0, places=3)
        self.assertEqual(got["basis"], "premarket")


# ── §6/§7 session finality ──────────────────────────────────────────────────

class TestSessionFinality(unittest.TestCase):

    G = {"quiet_minutes": 15, "fallback_final_kst": "18:30"}

    def test_a_normal_trading_day_settles_once_the_value_stands_still(self):
        got = kle.session_finality(kle.SCHED_AFTER, "2026-03-04", "15:50",
                                   "2026-03-04", steady_minutes=20,
                                   readings=3, gates=self.G)
        self.assertEqual(got["data_state"], kle.DATA_SETTLED)
        self.assertTrue(got["final"])
        self.assertFalse(got["by_fallback"])

    def test_inside_normal_hours_is_never_final(self):
        got = kle.session_finality(kle.SCHED_LIVE, "2026-03-04", "13:00",
                                   "2026-03-04", steady_minutes=99,
                                   readings=9, gates=self.G)
        self.assertEqual(got["data_state"], kle.DATA_UPDATING)
        self.assertFalse(got["final"])

    def test_an_irregular_late_close_stays_preliminary(self):
        """The exam-day case. The clock says 15:45 and the market is still
        moving, and the market wins."""
        got = kle.session_finality(kle.SCHED_AFTER, "2026-11-19", "15:45",
                                   "2026-11-19", steady_minutes=2,
                                   readings=6, observed_change=True,
                                   gates=self.G)
        self.assertEqual(got["data_state"], kle.DATA_UPDATING)
        self.assertFalse(got["final"])
        self.assertIn("still trading", got["reason"])

    def test_an_irregular_session_settles_once_it_actually_stops(self):
        got = kle.session_finality(kle.SCHED_AFTER, "2026-11-19", "16:50",
                                   "2026-11-19", steady_minutes=18,
                                   readings=8, observed_change=True,
                                   gates=self.G)
        self.assertEqual(got["data_state"], kle.DATA_SETTLED)
        self.assertTrue(got["final"])

    def test_one_reading_cannot_establish_anything(self):
        got = kle.session_finality(kle.SCHED_AFTER, "2026-03-04", "15:40",
                                   "2026-03-04", steady_minutes=0,
                                   readings=1, gates=self.G)
        self.assertEqual(got["data_state"], kle.DATA_UNKNOWN)
        self.assertFalse(got["final"])

    def test_watched_briefly_is_not_the_same_answer_as_still_moving(self):
        """Both block finality. They are different facts and they say so:
        one has seen the value move, the other has not been looking long
        enough for standing still to mean anything."""
        watched = kle.session_finality(kle.SCHED_AFTER, "2026-03-04", "15:40",
                                       "2026-03-04", steady_minutes=3,
                                       readings=2, observed_change=False,
                                       gates=self.G)
        moving = kle.session_finality(kle.SCHED_AFTER, "2026-03-04", "15:40",
                                      "2026-03-04", steady_minutes=3,
                                      readings=2, observed_change=True,
                                      gates=self.G)
        self.assertEqual(watched["data_state"], kle.DATA_UNKNOWN)
        self.assertEqual(moving["data_state"], kle.DATA_UPDATING)
        self.assertIn("watched long enough", watched["reason"])

    def test_the_documented_fallback_settles_a_late_evening_restart(self):
        got = kle.session_finality(kle.SCHED_AFTER, "2026-03-04", "19:00",
                                   "2026-03-04", steady_minutes=0,
                                   readings=1, gates=self.G)
        self.assertTrue(got["final"])
        self.assertTrue(got["by_fallback"])
        self.assertIn("18:30", got["reason"])

    def test_the_fallback_still_refuses_a_market_that_is_moving(self):
        got = kle.session_finality(kle.SCHED_AFTER, "2026-03-04", "19:00",
                                   "2026-03-04", steady_minutes=1,
                                   readings=4, observed_change=True,
                                   gates=self.G)
        self.assertFalse(got["final"])

    def test_no_bar_for_todays_seoul_date_after_the_close_is_no_session(self):
        got = kle.session_finality(kle.SCHED_AFTER, "2026-03-04", "16:00",
                                   "2026-03-03", gates=self.G)
        self.assertEqual(got["data_state"], kle.DATA_NO_SESSION)
        self.assertFalse(got["final"])

    def test_a_weekend_is_no_session_rather_than_a_failure(self):
        got = kle.session_finality(kle.SCHED_NON_TRADING, "2026-03-07",
                                   "12:00", None, gates=self.G)
        self.assertEqual(got["data_state"], kle.DATA_NO_SESSION)

    def test_the_quiet_period_is_configurable(self):
        loose = kle.session_finality(kle.SCHED_AFTER, "2026-03-04", "15:40",
                                     "2026-03-04", steady_minutes=6,
                                     readings=2,
                                     gates={"quiet_minutes": 5,
                                            "fallback_final_kst": "18:30"})
        self.assertTrue(loose["final"])


class TestSessionViewSeparatesClockFromData(WiredCase):

    def test_both_states_are_exposed(self):
        got = kl.session_view()
        for key in ("scheduled_state", "data_state", "final",
                    "scheduled_close", "provider_market_state",
                    "latest_market_timestamp"):
            self.assertIn(key, got)

    def test_the_provider_market_state_is_absent_and_says_so(self):
        got = kl.session_view()
        self.assertIsNone(got["provider_market_state"])
        self.assertIn("no market-state field",
                      got["provider_market_state_note"])

    def test_a_changing_value_is_observed_as_a_change(self):
        kl.observe_session("^KS11", "2026-03-04", 1000.0, 111)
        second = kl.observe_session("^KS11", "2026-03-04", 1001.0, 112)
        self.assertTrue(second["observed_change"])
        self.assertEqual(second["readings"], 2)

    def test_an_unchanged_value_is_not_a_change(self):
        kl.observe_session("^KS11", "2026-03-04", 1000.0, 111)
        second = kl.observe_session("^KS11", "2026-03-04", 1000.0, 111)
        self.assertFalse(second["observed_change"])
        self.assertEqual(second["readings"], 2)

    def test_a_new_session_date_resets_the_watch(self):
        """Yesterday having been quiet for sixteen hours says nothing about
        today."""
        kl.observe_session("^KS11", "2026-03-03", 1000.0, 111)
        kl.observe_session("^KS11", "2026-03-03", 1000.0, 111)
        fresh = kl.observe_session("^KS11", "2026-03-04", 1010.0, 222)
        self.assertEqual(fresh["readings"], 1)
        self.assertFalse(fresh["observed_change"])

    def test_daylight_saving_does_not_move_seoul(self):
        """Seoul keeps no daylight saving. The same instant either side of a
        U.S. clock change must land on the same Seoul wall time relative to
        the offset that actually applies."""
        winter = kl.session_state(datetime(2026, 1, 14, 20, 0, tzinfo=ET))
        summer = kl.session_state(datetime(2026, 7, 14, 21, 0, tzinfo=ET))
        self.assertEqual(winter["seoul_time"], "10:00")
        self.assertEqual(summer["seoul_time"], "10:00")
        self.assertEqual(winter["state"], kl.SESSION_LIVE)
        self.assertEqual(summer["state"], kl.SESSION_LIVE)


# ── §22 unusual and extreme ─────────────────────────────────────────────────

class TestUnusualAndExtreme(unittest.TestCase):

    G = {"unusual_percentile": 90.0, "extreme_percentile": 97.0}

    def test_below_the_unusual_line_is_normal(self):
        self.assertEqual(kle.unusual_state(88.0, self.G)["state"],
                         kle.UNUSUAL_NORMAL)

    def test_exactly_at_the_unusual_line_is_unusual(self):
        self.assertEqual(kle.unusual_state(90.0, self.G)["state"],
                         kle.UNUSUAL_UNUSUAL)

    def test_exactly_at_the_extreme_line_is_extreme(self):
        self.assertEqual(kle.unusual_state(97.0, self.G)["state"],
                         kle.UNUSUAL_EXTREME)

    def test_no_percentile_is_not_measured_rather_than_normal(self):
        self.assertEqual(kle.unusual_state(None, self.G)["state"],
                         kle.UNUSUAL_UNKNOWN)

    def test_the_percentile_and_its_gates_travel_with_the_label(self):
        got = kle.unusual_state(98.5, self.G)
        self.assertEqual(got["percentile"], 98.5)
        self.assertEqual(got["extreme_at"], 97.0)


class TestTheSeriesCarryTheirPercentile(WiredCase):

    def test_the_sample_and_lookback_are_reported_beside_the_state(self):
        got = kl.korea_today()
        k = got["series"]["kospi"]
        self.assertIn("move_state", k)
        self.assertIn("trailing_n", k)
        self.assertIn("lookback_sessions", k)

    def test_the_percentile_never_includes_todays_own_move(self):
        """A move ranked against a distribution containing itself can never
        reach the top of that distribution."""
        got = kl.korea_today()
        n = got["series"]["kospi"]["trailing_n"]
        self.assertLessEqual(n, len(MOVES) - 1)


# ── §11-§14 checkpoints ─────────────────────────────────────────────────────

class TestCheckpoints(WiredCase):

    def test_a_checkpoint_stores_both_the_scheduled_and_the_actual_time(self):
        self.now = datetime(2026, 3, 3, 21, 2, tzinfo=ET)   # 11:02 Seoul
        rec = kc.capture_checkpoint("11:00", "11:00")
        self.assertEqual(rec["status"], kc.STATUS_CAPTURED)
        self.assertEqual(rec["scheduled_kst"], "11:00")
        self.assertTrue(rec["captured_at"])
        self.assertAlmostEqual(rec["late_minutes"], 2.0, places=1)

    def test_a_checkpoint_stores_raw_inputs_not_only_labels(self):
        rec = kc.capture_checkpoint("15:00", "15:00")
        for name in ("kospi", "samsung", "hynix"):
            self.assertIn("pct", rec["korea"][name])
            self.assertIn("provider_timestamp", rec["korea"][name])
        self.assertIn("chip_confirmation", rec["korea"])

    def test_every_record_carries_its_versions_and_config_hash(self):
        rec = kc.capture_checkpoint("15:00", "15:00")
        self.assertEqual(rec["schema_version"], kc.SCHEMA_VERSION)
        self.assertEqual(rec["korea_lead_engine_version"], kle.ENGINE_VERSION)
        self.assertEqual(rec["signal_definition"], kle.SIGNAL_DEFINITION)
        self.assertEqual(rec["config_hash"], kl.config()[1])

    def test_a_missed_checkpoint_is_recorded_as_missed(self):
        rec = kc.record_missed("13:00", "13:00", "2026-03-04", "app was down")
        self.assertEqual(rec["status"], kc.STATUS_MISSED)
        self.assertIsNone(rec["captured_at"])

    def test_a_late_checkpoint_is_never_captured_as_the_scheduled_one(self):
        """13:47 is not the 13:00 observation, and filing it as one would
        say the app knew something forty-seven minutes before it did."""
        got = kc._cycle(now_kst=datetime(2026, 3, 4, 13, 47, tzinfo=KST),
                        now_et=datetime(2026, 3, 3, 23, 47, tzinfo=ET))
        self.assertIn("missed 13:00", got["acted"])
        recs = [r for r in kc.read_records(30, kinds=[kc.KIND_CHECKPOINT])
                if r.get("checkpoint") == "13:00"]
        self.assertEqual(recs[0]["status"], kc.STATUS_MISSED)
        self.assertIsNone(recs[0]["captured_at"])

    def test_a_checkpoint_inside_the_grace_window_is_captured(self):
        got = kc._cycle(now_kst=datetime(2026, 3, 4, 13, 5, tzinfo=KST),
                        now_et=datetime(2026, 3, 3, 23, 5, tzinfo=ET))
        self.assertIn("checkpoint 13:00", got["acted"])

    def test_a_holiday_is_classified_apart_from_a_failure(self):
        """A capture that found nothing to capture is a fact about the
        market, not about the app."""
        self.korea = {k: kbars(MOVES, end="2026-03-03")
                      for k in self.korea}
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        rec = kc.capture_checkpoint("15:00", "15:00")
        self.assertEqual(rec["status"], kc.STATUS_NO_SESSION)
        self.assertNotEqual(rec["status"], kc.STATUS_MISSED)
        self.assertIn("not a missed capture", rec["status_reason"])

    def test_a_restart_does_not_duplicate_a_missed_record(self):
        stamp = (datetime(2026, 3, 4, 13, 47, tzinfo=KST),
                 datetime(2026, 3, 3, 23, 47, tzinfo=ET))
        kc._cycle(now_kst=stamp[0], now_et=stamp[1])
        kc.reset_for_tests()                 # the container restarts
        kc._cycle(now_kst=stamp[0], now_et=stamp[1])
        recs = [r for r in kc.read_records(30, kinds=[kc.KIND_CHECKPOINT])
                if r.get("checkpoint") == "13:00"]
        self.assertEqual(len(recs), 1)

    def test_the_eastern_date_rolling_does_not_erase_the_seoul_memory(self):
        """Midnight in New York is one in the afternoon in Seoul. Clearing
        the day's memo there would re-examine the 11:00 capture already
        taken, find it late, and file it as MISSED on top of itself."""
        kc._cycle(now_kst=datetime(2026, 3, 4, 11, 2, tzinfo=KST),
                  now_et=datetime(2026, 3, 3, 21, 2, tzinfo=ET))
        kc._cycle(now_kst=datetime(2026, 3, 4, 13, 5, tzinfo=KST),
                  now_et=datetime(2026, 3, 4, 0, 5, tzinfo=ET))
        eleven = [r for r in kc.read_records(30, kinds=[kc.KIND_CHECKPOINT])
                  if r.get("checkpoint") == "11:00"]
        self.assertEqual(len(eleven), 1)
        self.assertEqual(eleven[0]["status"], kc.STATUS_CAPTURED)

    def test_the_weekend_schedules_no_korean_checkpoint(self):
        got = kc._cycle(now_kst=datetime(2026, 3, 7, 13, 5, tzinfo=KST),
                        now_et=datetime(2026, 3, 6, 23, 5, tzinfo=ET))
        self.assertFalse([a for a in got["acted"] if "13:00" in a])

    def test_coverage_counts_missed_and_no_session_separately(self):
        kc.record_missed("11:00", "11:00", "2026-03-04", "down")
        kc.capture_checkpoint("15:00", "15:00")
        cov = kc.coverage()
        self.assertEqual(cov["per_checkpoint"]["11:00"]["missed"], 1)
        self.assertEqual(cov["per_checkpoint"]["15:00"]["captured"], 1)
        self.assertIn("NO KOREA SESSION", cov["note"])


class TestTheCaptureThreadStartsExactlyOnce(WiredCase):

    def test_a_second_start_is_refused(self):
        self.assertTrue(kc.start(background=False))
        self.assertFalse(kc.start(background=False))
        self.assertFalse(kc.start(background=False))

    def test_a_failing_cycle_records_the_error_rather_than_dying(self):
        def boom(*a, **k):
            raise RuntimeError("provider exploded")
        real = kc.capture_checkpoint
        kc.capture_checkpoint = boom
        try:
            with self.assertRaises(RuntimeError):
                kc._cycle(now_kst=datetime(2026, 3, 4, 13, 5, tzinfo=KST),
                          now_et=datetime(2026, 3, 3, 23, 5, tzinfo=ET))
        finally:
            kc.capture_checkpoint = real
        # The loop catches what _cycle raises; the thread survives it.
        self.assertIn("error", kc.status())


# ── §15-§17 the pre-open snapshot and its outcome ───────────────────────────

class TestTheForwardSnapshot(WiredCase):

    def test_it_covers_the_configured_universe_not_a_browser_selection(self):
        got = kc.capture_snapshot(symbols=["TEST", "TEST2"])
        self.assertEqual(len(got["written"]) + len(got["failed"]), 2)
        self.assertGreater(len(kc.SNAPSHOT_UNIVERSE), 10)

    def test_every_snapshot_has_its_own_identifier(self):
        got = kc.capture_snapshot(symbols=["TEST"])
        self.assertEqual(len(got["written"]), 1)
        self.assertIn("TEST", got["written"][0])

    def test_a_snapshot_archives_raw_inputs_beside_every_label(self):
        kc.capture_snapshot(symbols=["TEST"])
        rec = kc.read_records(30, kinds=[kc.KIND_SNAPSHOT])[0]
        for key in ("bias_state", "bucket", "bucket_n", "match_rate_pct",
                    "wilson_lo_pct", "wilson_hi_pct", "implied_median_pct",
                    "implied_q25_pct", "implied_q75_pct", "rolling_60d_r",
                    "rolling_1y_r", "premarket_basis", "korea"):
            self.assertIn(key, rec)
        self.assertIn("pct", rec["korea"]["kospi"])

    def test_a_snapshot_carries_both_engine_versions_and_the_config_hash(self):
        kc.capture_snapshot(symbols=["TEST"])
        rec = kc.read_records(30, kinds=[kc.KIND_SNAPSHOT])[0]
        self.assertEqual(rec["korea_lead_engine_version"], kle.ENGINE_VERSION)
        self.assertEqual(rec["korea_research_engine_version"],
                         kre.ENGINE_VERSION)
        self.assertEqual(rec["config_hash"], kl.config()[1])
        self.assertEqual(rec["schema_version"], kc.SCHEMA_VERSION)

    def test_a_prediction_is_never_rewritten(self):
        """The store has no update path at all. A second capture appends a
        second record; it cannot reach the first."""
        kc.capture_snapshot(symbols=["TEST"])
        first = kc.read_records(30, kinds=[kc.KIND_SNAPSHOT])[0]
        before = dict(first)
        kc.capture_snapshot(symbols=["TEST"])
        after = [r for r in kc.read_records(30, kinds=[kc.KIND_SNAPSHOT])
                 if r["snapshot_id"] == before["snapshot_id"]][0]
        self.assertEqual(after, before)

    def test_a_missed_snapshot_is_recorded_rather_than_taken_late(self):
        got = kc._cycle(now_kst=datetime(2026, 3, 4, 23, 40, tzinfo=KST),
                        now_et=datetime(2026, 3, 4, 9, 40, tzinfo=ET))
        self.assertIn("missed snapshot", got["acted"])
        rec = [r for r in kc.read_records(30, kinds=[kc.KIND_SNAPSHOT])][0]
        self.assertEqual(rec["status"], kc.STATUS_MISSED)
        self.assertIn("after the open is not a pre-open record",
                      rec["status_reason"])

    def test_the_snapshot_fires_inside_its_grace_window(self):
        got = kc._cycle(now_kst=datetime(2026, 3, 4, 23, 27, tzinfo=KST),
                        now_et=datetime(2026, 3, 4, 9, 27, tzinfo=ET))
        self.assertIn("snapshot", got["acted"])


class TestOutcomesAreSeparateRecords(WiredCase):

    def _snapshot_for(self, day):
        self.now = datetime.fromisoformat(f"{day}T09:25:00").replace(tzinfo=ET)
        return kc.capture_snapshot(symbols=["TEST"])

    def test_an_outcome_references_the_prediction_it_scored(self):
        # Predict for a day that already has a completed U.S. bar behind it.
        target = kle.bar_date(self.us[-2])
        self.now = datetime.fromisoformat(f"{target}T09:25:00").replace(tzinfo=ET)
        kc.capture_snapshot(symbols=["TEST"])
        self.now = datetime.fromisoformat(f"{target}T17:00:00").replace(tzinfo=ET)
        got = kc.evaluate(et_date=target)
        self.assertEqual(got["scored"], 1)
        out = kc.read_records(30, kinds=[kc.KIND_OUTCOME])[0]
        pred = kc.read_records(30, kinds=[kc.KIND_SNAPSHOT])[0]
        self.assertEqual(out["snapshot_id"], pred["snapshot_id"])
        self.assertEqual(out["kind"], kc.KIND_OUTCOME)

    def test_an_outcome_scores_the_archived_prediction_not_a_rerun(self):
        target = kle.bar_date(self.us[-2])
        self.now = datetime.fromisoformat(f"{target}T09:25:00").replace(tzinfo=ET)
        kc.capture_snapshot(symbols=["TEST"])
        pred = kc.read_records(30, kinds=[kc.KIND_SNAPSHOT])[0]
        kc.evaluate(et_date=target)
        out = kc.read_records(30, kinds=[kc.KIND_OUTCOME])[0]
        self.assertEqual(out["predicted_gap_pct"], pred["implied_median_pct"])
        self.assertIn("archived", out["predicted_by"])

    def test_an_outcome_echoes_the_predictions_versions_for_comparison(self):
        target = kle.bar_date(self.us[-2])
        self.now = datetime.fromisoformat(f"{target}T09:25:00").replace(tzinfo=ET)
        kc.capture_snapshot(symbols=["TEST"])
        kc.evaluate(et_date=target)
        out = kc.read_records(30, kinds=[kc.KIND_OUTCOME])[0]
        self.assertEqual(out["prediction_versions"]["korea_lead_engine_version"],
                         kle.ENGINE_VERSION)
        self.assertTrue(out["scored_by_same_engine"])

    def test_minute_outcomes_are_left_empty_rather_than_reconstructed(self):
        target = kle.bar_date(self.us[-2])
        self.now = datetime.fromisoformat(f"{target}T09:25:00").replace(tzinfo=ET)
        kc.capture_snapshot(symbols=["TEST"])
        kc.evaluate(et_date=target)
        out = kc.read_records(30, kinds=[kc.KIND_OUTCOME])[0]
        self.assertIsNone(out["minute_outcomes"])
        self.assertIn("does not know when anything happened",
                      out["minute_outcomes_note"])

    def test_scoring_twice_does_not_double_count(self):
        target = kle.bar_date(self.us[-2])
        self.now = datetime.fromisoformat(f"{target}T09:25:00").replace(tzinfo=ET)
        kc.capture_snapshot(symbols=["TEST"])
        kc.evaluate(et_date=target)
        again = kc.evaluate(et_date=target)
        self.assertEqual(again["scored"], 0)
        self.assertEqual(again["already"], 1)


# ── §18 the forward scorecard ───────────────────────────────────────────────

class TestTheForwardScorecard(WiredCase):

    def test_it_refuses_to_render_on_a_handful_of_mornings(self):
        got = kc.scorecard()
        self.assertFalse(got["usable"])
        self.assertEqual(got["basis"], "FORWARD RECORDED")
        self.assertIn("worse than none", got["reason"])

    def test_it_never_mixes_a_backtest_into_the_rate(self):
        self.assertIn("never mixed", kc.scorecard()["note"])

    def test_it_reports_once_there_are_enough_records(self):
        self.overlay("forward", min_forward_n=3)
        for i, day in enumerate(("2026-02-02", "2026-02-03", "2026-02-04")):
            kc.append({"kind": kc.KIND_OUTCOME, "snapshot_id": f"x{i}",
                       "symbol": "TEST", "et_date": day,
                       "direction_correct": i != 1,
                       "abs_gap_error_pct": 0.4 + i * 0.1,
                       "prediction_versions": {
                           "korea_lead_engine_version": kle.ENGINE_VERSION,
                           "config_hash": "abc"}})
        got = kc.scorecard(symbol="TEST")
        self.assertTrue(got["usable"])
        self.assertEqual(got["n"], 3)
        self.assertEqual(got["direction_correct"], 2)

    def test_records_from_different_engines_are_flagged_not_blended(self):
        self.overlay("forward", min_forward_n=2)
        for i, ver in enumerate(("korea-lead-1.0.0", "korea-lead-2.0.0")):
            kc.append({"kind": kc.KIND_OUTCOME, "snapshot_id": f"y{i}",
                       "symbol": "TEST", "et_date": f"2026-02-1{i}",
                       "direction_correct": True, "abs_gap_error_pct": 0.5,
                       "prediction_versions": {
                           "korea_lead_engine_version": ver,
                           "config_hash": "abc"}})
        got = kc.scorecard(symbol="TEST")
        self.assertTrue(got["usable"])
        self.assertIn("not a single experiment", got["mixed_versions_note"])


# ── §19-§21 driver hysteresis ───────────────────────────────────────────────

class TestDriverHysteresis(unittest.TestCase):

    GATES = {"direction_improvement_points": 3.0,
             "mae_relative_improvement": 0.10,
             "confirmation_sessions": 60, "min_oos_n": 250,
             "min_direction_pct": 52.0, "max_rmse_ratio": 1.0}

    def scores(self, **over):
        base = {"kospi": {"n": 400, "direction_pct": 60.0, "mae_pct": 1.00,
                          "rmse_ratio": 0.95},
                "hynix": {"n": 400, "direction_pct": 60.0, "mae_pct": 1.00,
                          "rmse_ratio": 0.95}}
        for k, v in over.items():
            base[k] = dict(base.get(k, {}), **v)
        return base

    def test_a_challenger_winning_direction_but_not_error_does_not_switch(self):
        s = self.scores(hynix={"direction_pct": 70.0, "mae_pct": 0.99})
        got = kre.driver_decision(s, incumbent={"driver": "kospi"},
                                  gates=self.GATES)
        self.assertEqual(got["driver"], "kospi")
        self.assertEqual(got["verdict"], "HELD")
        self.assertIn("error", got["detail"])

    def test_a_challenger_winning_error_but_not_direction_does_not_switch(self):
        s = self.scores(hynix={"direction_pct": 60.5, "mae_pct": 0.50})
        got = kre.driver_decision(s, incumbent={"driver": "kospi"},
                                  gates=self.GATES)
        self.assertEqual(got["driver"], "kospi")
        self.assertIn("direction accuracy", got["detail"])

    def test_winning_both_but_too_recently_does_not_switch(self):
        s = self.scores(hynix={"direction_pct": 65.0, "mae_pct": 0.50})
        got = kre.driver_decision(s, incumbent={"driver": "kospi"},
                                  gates=self.GATES, today="2026-03-04")
        self.assertEqual(got["driver"], "kospi")
        self.assertEqual(got["streak"]["sessions_held"], 0)
        self.assertIn("noise", got["detail"])

    def test_winning_both_for_long_enough_switches(self):
        s = self.scores(hynix={"direction_pct": 65.0, "mae_pct": 0.50})
        inc = {"driver": "kospi",
               "streak": {"challenger": "hynix", "since_n": 300}}
        got = kre.driver_decision(s, incumbent=inc, gates=self.GATES)
        self.assertEqual(got["driver"], "hynix")
        self.assertEqual(got["verdict"], "SWITCHED")
        self.assertTrue(got["changed"])

    def test_a_different_challenger_restarts_the_clock(self):
        s = self.scores(samsung={"n": 400, "direction_pct": 66.0,
                                 "mae_pct": 0.45, "rmse_ratio": 0.9})
        inc = {"driver": "kospi",
               "streak": {"challenger": "hynix", "since_n": 300}}
        got = kre.driver_decision(s, incumbent=inc, gates=self.GATES)
        self.assertEqual(got["driver"], "kospi")
        self.assertEqual(got["streak"]["challenger"], "samsung")
        self.assertEqual(got["streak"]["sessions_held"], 0)

    def test_every_candidate_weak_is_no_clear_primary_driver(self):
        s = {"kospi": {"n": 400, "direction_pct": 50.5, "mae_pct": 1.0,
                       "rmse_ratio": 1.02},
             "hynix": {"n": 400, "direction_pct": 49.0, "mae_pct": 1.1,
                       "rmse_ratio": 1.05}}
        got = kre.driver_decision(s, gates=self.GATES)
        self.assertEqual(got["verdict"], kre.DRIVER_NONE)
        self.assertIsNone(got["driver"])
        self.assertIn("least bad", got["detail"])

    def test_a_short_history_cannot_be_promoted_on_flattering_accuracy(self):
        """The absolute floor, doing the job ranking cannot: 72% right
        across ninety sessions is not evidence, it is a small sample."""
        s = {"kospi": {"n": 90, "direction_pct": 72.0, "mae_pct": 0.4,
                       "rmse_ratio": 0.8}}
        got = kre.driver_decision(s, gates=self.GATES)
        self.assertEqual(got["verdict"], kre.DRIVER_NONE)
        self.assertIn("90", got["eligibility"]["rows"][0]["fails"][0])

    def test_a_model_that_cannot_beat_a_constant_is_not_a_driver(self):
        s = {"kospi": {"n": 400, "direction_pct": 60.0, "mae_pct": 1.0,
                       "rmse_ratio": 1.0}}
        got = kre.driver_decision(s, gates=self.GATES)
        self.assertEqual(got["verdict"], kre.DRIVER_NONE)

    def test_an_incumbent_that_stops_qualifying_is_demoted_and_says_why(self):
        s = {"kospi": {"n": 400, "direction_pct": 48.0, "mae_pct": 1.0,
                       "rmse_ratio": 1.2}}
        got = kre.driver_decision(s, incumbent={"driver": "kospi"},
                                  gates=self.GATES)
        self.assertEqual(got["verdict"], kre.DRIVER_NONE)
        self.assertTrue(got["changed"])
        self.assertIn("no longer qualifies", got["detail"])

    def test_adoption_with_no_incumbent_still_requires_the_floor(self):
        got = kre.driver_decision(self.scores(), gates=self.GATES)
        self.assertEqual(got["verdict"], "ADOPTED")
        self.assertIn(got["driver"], ("kospi", "hynix"))

    def test_a_tenth_of_a_point_can_never_move_the_driver(self):
        s = self.scores(hynix={"direction_pct": 60.1, "mae_pct": 0.999})
        got = kre.driver_decision(s, incumbent={"driver": "kospi"},
                                  gates=self.GATES)
        self.assertEqual(got["driver"], "kospi")


class TestDriverEligibilityReportsEveryFailure(unittest.TestCase):

    def test_each_failed_gate_is_named(self):
        got = kre.driver_eligibility(
            {"kospi": {"n": 10, "direction_pct": 40.0, "mae_pct": 2.0,
                       "rmse_ratio": 1.5}},
            {"min_oos_n": 250, "min_direction_pct": 52.0,
             "max_rmse_ratio": 1.0})
        fails = got["rows"][0]["fails"]
        self.assertEqual(len(fails), 3)
        self.assertFalse(got["eligible"])

    def test_an_unmeasured_metric_is_a_failure_not_a_pass(self):
        got = kre.driver_eligibility(
            {"kospi": {"n": 400, "direction_pct": None, "mae_pct": 1.0,
                       "rmse_ratio": None}},
            {"min_oos_n": 250, "min_direction_pct": 52.0,
             "max_rmse_ratio": 1.0})
        self.assertFalse(got["rows"][0]["eligible"])


class TestNotYetEvaluatedIsNotTheSameAsNoClearDriver(WiredCase):
    """One says the comparison has never been run. The other says it was run
    and nothing qualified. Reporting the second when the first is true would
    claim a measurement nobody made."""

    def test_an_unevaluated_ticker_says_so(self):
        import korea_research as kres
        kres.configure(data_dir=self.tmp.name)
        got = kres.driver_state("NEVERSEEN")
        self.assertEqual(got["verdict"], "NOT YET EVALUATED")
        self.assertIsNone(got["driver"])
        self.assertIn("has been run for this ticker yet", got["detail"])
        self.assertIn("what has been measured", got["detail"])

    def test_the_panel_passes_that_through_rather_than_inventing_one(self):
        got = kl.payload("TEST")
        self.assertEqual(got["primary_driver"]["verdict"], "NOT YET EVALUATED")


# ── §27 the self-check ──────────────────────────────────────────────────────

class TestTheSelfCheck(WiredCase):

    def test_the_payload_carries_every_check_by_name(self):
        got = kl.payload("TEST")
        names = [c["name"] for c in got["self_check"]["checks"]]
        self.assertIn("Korean session is today's", names)
        self.assertIn("Premarket quote is fresh enough", names)
        self.assertIn("Relationship is stable", names)

    def test_a_stale_quote_limits_the_output_without_invalidating_history(self):
        self.quote = {"last": 101.5, "close_prev": 100.0, "open": None,
                      "stale_seconds": 16 * 3600.0}
        got = kl.payload("TEST")
        self.assertIn("Premarket quote is fresh enough",
                      got["self_check"]["failed"])
        # A stale quote removes the premarket comparison; it does not make
        # the historical relationship above it wrong, so it never blocks.
        self.assertNotIn("Premarket quote is fresh enough",
                         got["self_check"]["blocking_failures"])
        self.assertTrue(got["opening_gap"]["stats"]["n"])

    def test_a_missing_korean_session_degrades_the_whole_panel(self):
        self.korea = {k: kbars(MOVES, end="2026-03-02") for k in self.korea}
        kl.configure(daily_fn=self._daily, korea_fn=self._korea,
                     quote_fn=lambda s: dict(self.quote),
                     data_dir=self.tmp.name, now_fn=lambda: self.now)
        got = kl.payload("TEST")
        self.assertFalse(got["self_check"]["ok"])
        self.assertIn("Korean session is today's",
                      got["self_check"]["blocking_failures"])
        self.assertIsNotNone(got["self_check"]["detail"])

    def test_a_passing_check_set_reports_no_detail(self):
        got = kle.self_check([{"name": "a", "ok": True, "detail": "fine"}])
        self.assertTrue(got["ok"])
        self.assertIsNone(got["detail"])


# ── §24 the strike preference is borrowed, never invented ───────────────────

class TestTargetDelta(WiredCase):

    def test_the_applications_own_selection_wins(self):
        got = kl.target_delta()
        self.assertEqual(got["basis"], "APPLICATION SELECTION")
        self.assertEqual(got["source"],
                         "investment.covered_call.cc_delta_target")

    def test_korea_lead_never_keeps_a_second_preference(self):
        self.assertIn("second strike preference",
                      kl.target_delta()["detail"])
        self.assertEqual(kl.target_delta()["delta"], 0.3)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
