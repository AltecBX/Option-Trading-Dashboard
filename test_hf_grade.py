"""test_hf_grade.py (v4.88) — guards for the outcome grader.

The grader's whole risk is overclaiming. It is the first part of this
feature that could be read as "the board predicts the market", and it does
not and cannot. So most of these tests assert restraint: that a verdict is
never scored right or wrong, that a share never appears without its sample
size and interval, that the base rate is computed on the same market so a
falling year cannot masquerade as a finding, and that clustered episodes are
counted and disclosed rather than quietly inflating N.

Run:  python3 test_hf_grade.py
"""

from __future__ import annotations

import re
import unittest
from datetime import date, timedelta
from pathlib import Path

import hf_grade as G


def walk(weeks, start=100.0, step=1.0):
    """A deterministic price path over a list of week keys."""
    out, v = {}, start
    for w in weeks:
        out[w] = v
        v += step
    return out


def week_span(first: str, n: int) -> list[str]:
    mon = G.week_monday(first)
    return [G.week_key(mon + timedelta(weeks=i)) for i in range(n)]


class Purity(unittest.TestCase):
    def test_no_io_and_no_clock(self):
        src = Path(__file__).resolve().parent.joinpath("hf_grade.py").read_text()
        body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        for bad in ("urlopen", "requests.", "socket", "datetime.now", "date.today",
                    "json.load", "Path("):
            self.assertNotIn(bad, body, f"hf_grade must not use {bad}")
        self.assertIsNone(re.search(r"(?<![A-Za-z_.])open\s*\(", body))


class Weeks(unittest.TestCase):
    def test_a_week_key_resolves_to_its_monday(self):
        self.assertEqual(G.week_monday("2026-W36"), date(2026, 8, 31))
        self.assertEqual(G.week_key(date(2026, 8, 31)), "2026-W36")

    def test_week_arithmetic_crosses_the_year(self):
        self.assertEqual(G.week_plus("2025-W52", 2), "2026-W02")
        self.assertEqual(G.week_plus("2026-W36", 8), "2026-W44")

    def test_a_malformed_week_is_refused_rather_than_guessed(self):
        for bad in ("", None, "2026", "not-a-week", "2026-W99"):
            self.assertIsNone(G.week_monday(bad), bad)
            self.assertIsNone(G.week_plus(bad, 4), bad)

    def test_horizons_are_dates_not_list_positions(self):
        # Stepping N places along a list of stored weeks would call a gap an
        # eight-week horizon. Four weeks after W36 is W40 whatever is stored.
        self.assertEqual(G.week_plus("2026-W36", 4), "2026-W40")


class Wilson(unittest.TestCase):
    def test_a_share_always_arrives_with_its_interval(self):
        w = G.wilson(11, 20)
        self.assertEqual((w["k"], w["n"]), (11, 20))
        self.assertAlmostEqual(w["share"], 0.55)
        self.assertLess(w["low"], 0.55)
        self.assertGreater(w["high"], 0.55)

    def test_a_small_sample_gets_a_wide_interval(self):
        narrow, wide = G.wilson(50, 100), G.wilson(5, 10)
        self.assertLess(narrow["high"] - narrow["low"], wide["high"] - wide["low"])

    def test_no_sample_is_not_a_zero(self):
        self.assertIsNone(G.wilson(0, 0))

    def test_the_interval_stays_inside_zero_and_one(self):
        for k, n in ((0, 3), (3, 3), (1, 2)):
            w = G.wilson(k, n)
            self.assertGreaterEqual(w["low"], 0.0)
            self.assertLessEqual(w["high"], 1.0)


class ForwardReturns(unittest.TestCase):
    WEEKS = week_span("2026-W01", 20)

    def test_a_return_is_measured_between_two_real_closes(self):
        px = walk(self.WEEKS)
        self.assertAlmostEqual(G.forward_return(px, "2026-W01", 4), 4 / 100.0)

    def test_a_horizon_that_has_not_arrived_is_not_a_zero(self):
        px = walk(self.WEEKS)
        self.assertIsNone(G.forward_return(px, "2026-W19", 8))

    def test_a_missing_starting_week_is_not_a_zero(self):
        self.assertIsNone(G.forward_return(walk(self.WEEKS), "2019-W01", 1))

    def test_a_zero_close_does_not_divide(self):
        self.assertIsNone(G.forward_return({"2026-W01": 0.0, "2026-W02": 5.0}, "2026-W01", 1))


class Reversal(unittest.TestCase):
    def test_a_crowded_long_reverses_when_the_market_falls(self):
        self.assertEqual(G.reversal("long", -0.02), G.REVERSED)
        self.assertEqual(G.reversal("long", 0.02), G.HELD)

    def test_a_crowded_short_reverses_when_the_market_rises(self):
        self.assertEqual(G.reversal("short", 0.02), G.REVERSED)
        self.assertEqual(G.reversal("short", -0.02), G.HELD)

    def test_without_a_side_the_question_has_no_answer(self):
        self.assertIsNone(G.reversal(None, -0.02))
        self.assertIsNone(G.reversal("both", -0.02))

    def test_without_a_return_there_is_nothing_to_judge(self):
        self.assertIsNone(G.reversal("long", None))


class Episodes(unittest.TestCase):
    """Crowded weeks arrive in runs. Counting weeks as independent draws is
    what makes an interval look stronger than the evidence is."""

    ROWS = ([{"key": "sp500", "week": w, "state": "CROWDED"}
             for w in ("2026-W01", "2026-W02", "2026-W03", "2026-W10", "2026-W11")]
            + [{"key": "nasdaq", "week": "2026-W05", "state": "CROWDED"}]
            + [{"key": "sp500", "week": "2026-W20", "state": "NORMAL"}])

    def test_consecutive_weeks_are_one_episode(self):
        e = G.episodes(self.ROWS)
        self.assertEqual(e["per_market"]["sp500"], {"weeks": 5, "episodes": 2})

    def test_a_single_week_is_still_an_episode(self):
        self.assertEqual(G.episodes(self.ROWS)["per_market"]["nasdaq"]["episodes"], 1)

    def test_only_the_named_state_is_counted(self):
        self.assertEqual(G.episodes(self.ROWS)["total_weeks"], 6)

    def test_the_totals_are_reported_together(self):
        e = G.episodes(self.ROWS)
        self.assertEqual((e["total_weeks"], e["total_episodes"]), (6, 3))
        self.assertIn("five episodes are five events", e["note"])

class EpisodesMatchTheGradedSample(unittest.TestCase):
    """The episode count exists to temper the interval, so it has to
    describe the same rows the interval was computed from."""

    def _rows(self):
        weeks = week_span("2025-W01", 30)
        rows = [{"week": w, "market": "S&P 500", "key": "sp500", "state": "CROWDED",
                 "side": "long"} for w in weeks[:4]]
        # VIX is never graded; its episodes must not be counted beside a
        # sample that contains none of its weeks.
        rows += [{"week": w, "market": "VIX", "key": "vix", "state": "CROWDED",
                  "side": "long"} for w in weeks[:6]]
        return rows, weeks

    def test_an_ungraded_market_does_not_inflate_the_episode_count(self):
        rows, weeks = self._rows()
        out = G.grade_crowded_weeks(rows, {"SPY": walk(weeks + week_span("2025-W31", 10))},
                                    min_n=1)
        self.assertEqual(out["overall"]["1"]["episodes"], 1, "only the S&P run is graded")
        self.assertNotIn("vix", out["episodes"]["per_market"])

    def test_an_unmatured_episode_is_not_counted_at_a_horizon_it_missed(self):
        weeks = week_span("2025-W01", 12)
        rows = [{"week": w, "market": "S&P 500", "key": "sp500", "state": "CROWDED",
                 "side": "long"} for w in weeks]
        # Closes stop at week 12, so the 8-week horizon matures for fewer
        # weeks than the 1-week horizon does.
        out = G.grade_crowded_weeks(rows, {"SPY": walk(weeks)}, min_n=1)
        self.assertGreaterEqual(out["overall"]["1"]["crowded"]["n"],
                                out["overall"]["8"]["crowded"]["n"])
        self.assertIn("8", out["episodes"]["per_horizon"])

    def test_each_horizon_reports_its_own_count(self):
        rows, weeks = self._rows()
        out = G.grade_crowded_weeks(rows, {"SPY": walk(weeks + week_span("2025-W31", 10))},
                                    min_n=1)
        self.assertEqual(set(out["episodes"]["per_horizon"]), {"1", "2", "4", "8"})



class TheBaseRate(unittest.TestCase):
    """The comparison is the finding. A raw share is not."""

    def _rows(self, n=40):
        weeks = week_span("2025-W01", n)
        return [{"week": w, "market": "S&P 500", "key": "sp500",
                 "state": "CROWDED" if i % 4 == 0 else "NORMAL", "side": "long"}
                for i, w in enumerate(weeks)]

    def test_a_market_that_only_falls_shows_no_lift(self):
        # Crowded longs reversed 100% of the time — and so did every other
        # week. Without the base rate this reads as a discovery.
        rows = self._rows()
        weeks = week_span("2025-W01", 60)
        falling = {"SPY": {w: 200.0 - i for i, w in enumerate(weeks)}}
        out = G.grade_crowded_weeks(rows, falling, min_n=5)
        o = out["overall"]["4"]
        self.assertEqual(o["crowded"]["share"], 1.0)
        self.assertEqual(o["base"]["share"], 1.0)
        self.assertEqual(o["lift"], 0.0)

    def test_the_base_rate_is_the_same_market_not_all_markets(self):
        rows = self._rows() + [{"week": w, "market": "Nasdaq 100", "key": "nasdaq",
                                "state": "NORMAL", "side": "short"}
                               for w in week_span("2025-W01", 40)]
        weeks = week_span("2025-W01", 60)
        closes = {"SPY": {w: 200.0 - i for i, w in enumerate(weeks)},
                  "QQQ": {w: 100.0 + i for i, w in enumerate(weeks)}}
        out = G.grade_crowded_weeks(rows, closes, min_n=5)
        sp = out["markets"]["sp500"]["horizons"]["4"]
        self.assertEqual(sp["base"]["share"], 1.0, "SPY fell every week")
        nd = out["markets"]["nasdaq"]["horizons"]["4"]
        self.assertEqual(nd["base"]["share"], 1.0, "QQQ rose every week against a short")


class WhatIsNotGraded(unittest.TestCase):
    def test_vix_is_excluded_and_the_reason_travels_with_it(self):
        rows = [{"week": w, "market": "VIX", "key": "vix", "state": "CROWDED", "side": "long"}
                for w in week_span("2025-W01", 30)]
        weeks = week_span("2025-W01", 40)
        out = G.grade_crowded_weeks(rows, {"SPY": walk(weeks)}, min_n=5)
        self.assertNotIn("vix", out["markets"])
        self.assertIn("vix", out["not_graded"])
        self.assertIn("roll", out["not_graded"]["vix"])

    def test_a_market_with_no_proxy_is_skipped_not_guessed(self):
        rows = [{"week": "2026-W01", "market": "Cocoa", "key": "cocoa",
                 "state": "CROWDED", "side": "long"}]
        out = G.grade_crowded_weeks(rows, {"SPY": walk(week_span("2026-W01", 20))})
        self.assertEqual(out["markets"], {})


class VerdictsAreNotForecasts(unittest.TestCase):
    """The four weekly questions are never scored right or wrong."""

    def test_no_hit_rate_is_produced_for_a_verdict(self):
        weeks = week_span("2026-W01", 20)
        readings = [{"week": w, "verdicts": {"exposure": "REDUCING"}} for w in weeks[:10]]
        out = G.grade_verdicts(readings, {"SPY": walk(weeks)}, min_n=3)
        block = out["by_question"]["exposure"]["REDUCING"]["4"]
        for banned in ("correct", "hit", "hits", "accuracy", "right", "score"):
            self.assertNotIn(banned, block, banned)
        self.assertIn("n", block)
        self.assertIn("median", block)

    def test_the_base_distribution_is_shown_beside_each_answer(self):
        weeks = week_span("2026-W01", 20)
        readings = [{"week": w, "verdicts": {"exposure": "REDUCING"}} for w in weeks[:10]]
        out = G.grade_verdicts(readings, {"SPY": walk(weeks)}, min_n=3)
        self.assertIn("4", out["base"])
        self.assertGreater(out["base"]["4"]["n"], 0)

    def test_the_refusal_is_stated_in_words(self):
        out = G.grade_verdicts([], {}, min_n=3)
        self.assertIn("not a forecast of returns", out["note"])


class SmallSamples(unittest.TestCase):
    def test_below_the_floor_nothing_is_offered_as_a_finding(self):
        rows = [{"week": w, "market": "S&P 500", "key": "sp500", "state": "CROWDED",
                 "side": "long"} for w in week_span("2026-W01", 3)]
        out = G.grade_crowded_weeks(rows, {"SPY": walk(week_span("2026-W01", 20))}, min_n=20)
        self.assertFalse(out["overall"]["1"]["enough"])

    def test_the_headline_refuses_rather_than_reaching(self):
        card = G.build([], [], {})
        head = G.headline(card)
        self.assertFalse(head["available"])
        self.assertIn("Not enough graded weeks", head["text"])

    def test_summarize_reports_its_own_n(self):
        s = G.summarize([0.01, -0.02, 0.03], min_n=10)
        self.assertEqual(s["n"], 3)
        self.assertFalse(s["enough"])
        self.assertAlmostEqual(s["median"], 0.01)

    def test_an_empty_distribution_is_not_a_zero(self):
        s = G.summarize([])
        self.assertEqual(s["n"], 0)
        self.assertIsNone(s["median"])
        self.assertIsNone(s["share_up"])


class TheCard(unittest.TestCase):
    def setUp(self):
        weeks = week_span("2025-W01", 60)
        self.rows = [{"week": w, "market": "S&P 500", "key": "sp500",
                      "state": "CROWDED" if i % 5 == 0 else "NORMAL", "side": "long"}
                     for i, w in enumerate(weeks[:40])]
        self.closes = {"SPY": walk(weeks)}
        self.card = G.build(self.rows, [], self.closes, min_n=5)

    def test_the_card_counts_what_it_graded(self):
        self.assertEqual(self.card["n_market_weeks"], 40)
        self.assertGreater(self.card["n_crowded_weeks_graded"], 0)

    def test_a_week_graded_at_every_horizon_is_still_one_week(self):
        # It was the sum of the four per-horizon counts, so it read four
        # times the truth for any week old enough for all four to reach it:
        # 127 where 33 weeks had been graded.
        crowded = [r for r in self.rows if r["state"] == "CROWDED"]
        self.assertEqual(self.card["n_crowded_weeks"], len(crowded))
        per_horizon = sum((h.get("crowded") or {}).get("n") or 0
                          for h in self.card["crowding"]["overall"].values())
        self.assertLess(self.card["n_crowded_weeks_graded"], per_horizon,
                        "the union is smaller than the sum, or nothing was double counted")
        self.assertLessEqual(self.card["n_crowded_weeks_graded"], len(crowded),
                             "it can never grade more weeks than were crowded")
        self.assertGreaterEqual(
            self.card["n_crowded_weeks_graded"],
            max((h.get("crowded") or {}).get("n") or 0
                for h in self.card["crowding"]["overall"].values()),
            "the union covers at least the busiest single horizon")

    def test_the_crowded_total_counts_only_crowded_rows(self):
        # 51 crowded out of 1,303 reconstructed market-weeks is the shape of
        # the real record; "graded" means nothing without it beside.
        self.assertLess(self.card["n_crowded_weeks"], self.card["n_market_weeks"])
        self.assertEqual(G.build([], [], {}, min_n=5)["n_crowded_weeks"], 0)

    def test_the_limitations_state_the_independence_problem(self):
        blob = " ".join(self.card["limitations"]).lower()
        self.assertIn("optimistic", blob)
        self.assertIn("episode", blob)
        self.assertIn("does not claim positioning predicts", blob)

    def test_the_limitations_say_why_three_years_can_be_graded(self):
        blob = " ".join(self.card["limitations"]).lower()
        self.assertIn("reconstructed from the cftc series", blob)
        self.assertIn("never sees data that arrived later", blob)

    def test_the_same_inputs_build_the_same_card(self):
        again = G.build(self.rows, [], self.closes, min_n=5)
        self.assertEqual(self.card, again)

    def test_an_empty_card_does_not_raise(self):
        card = G.build([], [], {})
        self.assertEqual(card["n_market_weeks"], 0)
        self.assertEqual(card["crowding"]["markets"], {})


class Proxies(unittest.TestCase):
    def test_every_cftc_market_is_either_priced_or_explained(self):
        keys = {"sp500", "nasdaq", "russell", "djia", "vix", "sec_staples", "sec_energy",
                "sec_financials", "sec_health", "sec_industrials", "sec_utilities", "sec_comm"}
        covered = set(G.GRADE_PROXY) | set(G.NOT_GRADED)
        self.assertEqual(keys - covered, set(), "a market with neither a proxy nor a reason")

    def test_every_app_sector_has_a_proxy(self):
        self.assertEqual(len(G.SECTOR_PROXY), 11)
        self.assertEqual(len(set(G.SECTOR_PROXY.values())), 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
