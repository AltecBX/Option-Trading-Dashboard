"""Guards for hf_pulse.py — the aggregate math, offline and deterministic.

Everything here is built from synthetic series so each rule can be tested
alone, plus the two cases the live data actually produced: FINRA's capped
days-to-cover field, and sectors whose contracts differ in size by more
than tenfold.
"""

from __future__ import annotations

import unittest

import hf_pulse as P
import hf_sources as S


def series(field: str, values: list[float], start_date: str = "2026-09-01") -> list[dict]:
    """A newest-first weekly series of one field."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start_date)
    return [{"date": (d0 - timedelta(weeks=i)).isoformat(), field: v} for i, v in enumerate(values)]


def market(key="sp500", label="S&P 500", kind="index", sector=None, **fields) -> dict:
    n = len(next(iter(fields.values())))
    rows = []
    from datetime import date, timedelta
    d0 = date.fromisoformat("2026-09-01")
    for i in range(n):
        r = {"date": (d0 - timedelta(weeks=i)).isoformat()}
        for f, vals in fields.items():
            r[f] = vals[i]
        rows.append(r)
    m = {"key": key, "label": label, "kind": kind, "series": rows, "weeks": n,
         "as_of": rows[0]["date"]}
    if sector:
        m["sector"] = sector
    return m


class Primitives(unittest.TestCase):
    def test_percentile_places_a_value_in_its_own_history(self):
        self.assertEqual(P.percentile([1, 2, 3, 4, 5], 5), 100)
        self.assertEqual(P.percentile([1, 2, 3, 4, 5], 1), 20)
        self.assertEqual(P.percentile([1, 2, 3, 4, 5], 3), 60)
        self.assertIsNone(P.percentile([], 3))
        self.assertIsNone(P.percentile([1, 2], None))

    def test_percentile_ignores_gaps(self):
        self.assertEqual(P.percentile([1, None, 3], 3), 100)

    def test_weekly_changes_are_newest_first(self):
        s = series("x", [10, 7, 5, 5])
        self.assertEqual(P.weekly_changes(s, "x"), [3, 2, 0])

    def test_a_gap_ends_the_chain_rather_than_being_bridged(self):
        s = series("x", [10, 7, None, 2])
        self.assertEqual(P.weekly_changes(s, "x"), [3])

    def test_typical_is_the_median_absolute_move(self):
        self.assertEqual(P.typical([1, -3, 5]), 3)
        self.assertEqual(P.typical([2, -4]), 3)
        self.assertIsNone(P.typical([]))


class Streaks(unittest.TestCase):
    def test_four_straight_weeks_of_selling(self):
        st = P.streak([-5, -4, -6, -3, +2, -1])
        self.assertEqual(st["weeks"], 4)
        self.assertEqual(st["direction"], -1)
        self.assertIn("4 consecutive weeks of reducing", st["word"])

    def test_a_week_inside_the_noise_band_ends_a_streak(self):
        """Four weeks means four weeks — a week that rounds to nothing does
        not silently extend one."""
        self.assertEqual(P.streak([-5, -4, 0.01, -6], band=0.1)["weeks"], 2)

    def test_a_flat_week_starts_no_streak_either_way(self):
        st = P.streak([0.05, -4, -3], band=0.1)
        self.assertEqual(st["weeks"], 0)
        self.assertEqual(st["direction"], 0)
        self.assertIn("no clear direction", st["word"])

    def test_no_history_is_not_a_streak(self):
        self.assertEqual(P.streak([])["weeks"], 0)


class Persistence(unittest.TestCase):
    def test_n_of_the_last_m_weeks(self):
        p = P.persistence([-1] * 3 + [+1] + [-1] * 8)
        self.assertEqual(p["2"]["text"], "2 of the last 2 weeks")
        self.assertEqual(p["4"]["same"], 3)
        self.assertEqual(p["8"]["same"], 7)
        self.assertEqual(p["12"]["same"], 11)

    def test_a_window_with_too_little_history_says_nothing(self):
        p = P.persistence([-1, -1, -1])
        self.assertIsNotNone(p["2"])
        self.assertIsNone(p["4"], "four weeks of history is required to answer a four-week question")
        self.assertIsNone(p["8"])


class Confidence(unittest.TestCase):
    def _i(self, cls, change, weight=1.0, key=None):
        return P.make_input(key or f"{cls}{change}", "x", cls, 1, change, weight=weight)

    def test_one_source_is_one_source(self):
        c = P.confidence([self._i(S.REGULATORY, 5), self._i(S.REGULATORY, 7, key="b")])
        self.assertEqual(c["classes"], 1)
        self.assertEqual(c["level"], "LOW")

    def test_two_classes_agreeing_is_moderate(self):
        c = P.confidence([self._i(S.REGULATORY, 5), self._i(S.FLOW_PROXY, 7)])
        self.assertEqual(c["level"], "MODERATE")

    def test_three_classes_with_no_dissent_is_high(self):
        c = P.confidence([self._i(S.REGULATORY, 5), self._i(S.FLOW_PROXY, 7),
                          self._i(S.PRIME_BROKER, 2, weight=0.5)])
        self.assertEqual(c["level"], "HIGH")

    def test_a_supporting_input_can_never_create_a_verdict(self):
        """A prime-broker quote alone is a claim, not a reading."""
        c = P.confidence([self._i(S.PRIME_BROKER, 5, weight=0.5),
                          self._i(S.FLOW_PROXY, 5, weight=0.5)])
        self.assertEqual(c["level"], "NONE")
        self.assertEqual(c["classes"], 0)

    def test_dissent_lowers_confidence(self):
        c = P.confidence([self._i(S.REGULATORY, 5), self._i(S.REGULATORY, -5, key="b")])
        self.assertEqual(c["level"], "LOW")
        self.assertEqual(c["disagree"], 1)

    def test_conflicts_are_listed_not_averaged(self):
        ins = [self._i(S.REGULATORY, 5), self._i(S.REGULATORY, 6, key="b"), self._i(S.FLOW_PROXY, -9)]
        cf = P.conflicts(ins)
        self.assertEqual(len(cf), 1)
        self.assertEqual(cf[0]["direction"], -1)


class Verdicts(unittest.TestCase):
    def test_no_source_is_no_data_not_flat(self):
        v = P.build_verdict("q?", [P.make_input("a", "A", S.REGULATORY, None, None)])
        self.assertEqual(v["verdict"], P.NO_DATA)
        self.assertEqual(v["missing"], ["A"])

    def test_even_disagreement_is_mixed(self):
        ins = [P.make_input("a", "A", S.REGULATORY, 1, 5),
               P.make_input("b", "B", S.REGULATORY, 1, -5)]
        self.assertEqual(P.build_verdict("q?", ins)["verdict"], P.MIXED)

    def test_the_vocabulary_follows_the_question(self):
        ins = [P.make_input("a", "A", S.REGULATORY, 1, 5), P.make_input("b", "B", S.FLOW_PROXY, 1, 5)]
        self.assertEqual(P.build_verdict("q?", ins, vocab=(P.RISING, P.FALLING, P.FLAT))["verdict"], P.RISING)
        self.assertEqual(P.build_verdict("q?", ins, vocab=(P.ADDING_SHORTS, P.COVERING, P.FLAT))["verdict"],
                         P.ADDING_SHORTS)


class TheChangeNotTheLevel(unittest.TestCase):
    """Leveraged funds are structurally net short. A large short must not
    read as bearishness; only its change and its percentile may speak."""

    def test_a_deeply_short_but_improving_book_reads_as_adding(self):
        cftc = {"sp500": market(lev_net=[-300_000, -320_000, -340_000] + [-350_000] * 20,
                                lev_long=[1] * 23, lev_short=[1] * 23, lev_gross=[1] * 23)}
        v = P.exposure(cftc, None)
        self.assertEqual(v["verdict"], P.ADDING)
        self.assertLess(v["unusual"]["percentile"], 101)
        self.assertIn("structurally net short", v["unusual"]["note"])

    def test_the_note_says_the_level_is_not_a_view(self):
        cftc = {"sp500": market(lev_net=[-300_000] * 20, lev_gross=[1] * 20)}
        self.assertIn("level is structurally short", P.exposure(cftc, None)["note"])


class Leverage(unittest.TestCase):
    def test_gross_not_net_answers_the_leverage_question(self):
        """A book that doubles both legs has taken more risk and its NET
        does not move at all."""
        cftc = {"sp500": market(lev_net=[0] * 20, lev_gross=[400_000, 200_000] + [200_000] * 18)}
        v = P.leverage(cftc, None)
        self.assertEqual(v["verdict"], P.RISING)
        self.assertEqual(P.exposure(cftc, None)["verdict"], P.FLAT)

    def test_form_pf_is_context_with_its_own_date(self):
        ofr = {"lev_top10": {"value": 23.7, "prev": 18.2, "as_of": "2026-03-31", "n": 53,
                             "points": [["2026-03-31", 23.7]]}}
        v = P.leverage({"sp500": market(lev_gross=[1] * 20, lev_net=[0] * 20)}, ofr)
        ctx = v["context"][0]
        self.assertEqual(ctx["as_of"], "2026-03-31")
        self.assertIn("five months behind", ctx["note"])
        self.assertNotIn(ctx["label"], [i["label"] for i in v["inputs"]],
                         "a quarterly figure never votes on a weekly verdict")


class LongsAndShorts(unittest.TestCase):
    def test_the_two_legs_are_answered_separately(self):
        cftc = {"sp500": market(lev_long=[110, 100] + [100] * 18,
                                lev_short=[110, 100] + [100] * 18,
                                lev_net=[0] * 20, lev_gross=[220, 200] + [200] * 18)}
        longs, shorts = P.longs_and_shorts(cftc, None, None)
        self.assertEqual(longs["verdict"], P.ADDING)
        self.assertEqual(shorts["verdict"], P.ADDING_SHORTS)

    def test_short_interest_is_the_anchor_and_volume_only_supports(self):
        cftc = {"sp500": market(lev_long=[1] * 20, lev_short=[1] * 20, lev_net=[0] * 20, lev_gross=[1] * 20)}
        si = {"total": 1e10, "change": 5e8, "settlement": "2026-08-14", "n_symbols": 22482}
        sv = {"share": 51.0, "change": 3.0, "session": "2026-09-04", "n_days": 20,
              "history": [51.0, 48.0, 48.1, 47.9]}
        _, shorts = P.longs_and_shorts(cftc, si, sv)
        anchor = next(i for i in shorts["inputs"] if i["key"] == "si_total")
        proxy = next(i for i in shorts["inputs"] if i["key"] == "shvol")
        self.assertEqual(anchor["weight"], 1.0)
        self.assertEqual(proxy["weight"], 0.5)
        self.assertEqual(shorts["verdict"], P.ADDING_SHORTS)

    def test_a_tiny_move_in_the_short_share_is_not_a_direction(self):
        """The live data moved 0.19 points on a series whose typical day is
        about 1.5. Without a band from its own history that read as adding."""
        sv = {"share": 51.09, "change": 0.19, "session": "2026-09-04", "n_days": 20,
              "history": [51.09, 50.90, 49.2, 51.4, 49.9, 51.6, 50.1]}
        _, shorts = P.longs_and_shorts({}, None, sv)
        self.assertEqual(next(i for i in shorts["inputs"] if i["key"] == "shvol")["direction"], 0)


class Sectors(unittest.TestCase):
    def test_sectors_rank_in_their_own_terms_not_in_raw_contracts(self):
        """Financials trades roughly four times the size of Utilities. Raw
        contracts would put it on top every week regardless of what
        happened; multiples of its own typical week is the honest scale."""
        cftc = {
            "sec_financials": market("sec_financials", "Financials", "sector", "Financials",
                                     lev_net=[4000, 3000] + [3000 + 800 * (i % 2) for i in range(18)]),
            "sec_utilities": market("sec_utilities", "Utilities", "sector", "Utilities",
                                    lev_net=[1300, 1000] + [1000 + 20 * (i % 2) for i in range(18)]),
        }
        out = P.sectors(cftc, None, None, None)
        names = [r["sector"] for r in out["most_bought"]]
        self.assertEqual(names[0], "Utilities",
                         "the smaller contract moved far more in its own terms")
        self.assertIn("in its own terms", out["ranked_by"].lower())

    def test_the_four_sectors_with_no_contract_are_named(self):
        out = P.sectors({}, None, None, None)
        for name in ("Technology", "Consumer Discretionary", "Materials", "Real Estate"):
            row = next(r for r in out["rows"] if r["sector"] == name)
            self.assertFalse(row["has_futures"])
            self.assertIn("No leveraged-fund futures contract", row["note"])
        self.assertEqual(set(out["no_futures"]), set(S.CFTC_SECTORS_MISSING))

    def test_rising_short_interest_counts_as_selling(self):
        out = P.sectors({}, None, None, {"Technology": {"short": 1e9, "change": 5e7,
                                                        "settlement": "2026-08-14", "n_symbols": 300}})
        row = next(r for r in out["rows"] if r["sector"] == "Technology")
        self.assertEqual(row["inputs"][0]["direction"], -1)

    def test_a_sector_reports_how_many_inputs_it_actually_has(self):
        out = P.sectors({"sec_energy": market("sec_energy", "Energy", "sector", "Energy",
                                              lev_net=[100, 90] + [90] * 18)},
                        {"by_sector": {"Energy": {"net": 5e6, "as_of": "2026-09-04"}}}, None, None)
        row = next(r for r in out["rows"] if r["sector"] == "Energy")
        self.assertEqual(row["inputs_available"], 2)


class Crowding(unittest.TestCase):
    def test_a_book_both_one_sided_and_large_is_crowded(self):
        """Lean and size are the two measures, and both must be unusual."""
        n = 40
        # Newest week: everything net long (lean = 1.0, the max) on the
        # largest gross book in the record. Earlier weeks are balanced.
        net = [1000] + [10 * (i % 7) for i in range(n)]
        gross = [1000] + [400 + 5 * (i % 9) for i in range(n)]
        cftc = {"x": market("x", "X", "index", lev_net=net, lev_gross=gross,
                            lev_long=gross, lev_short=[1] * (n + 1))}
        row = P.crowding(cftc, None)["markets"][0]
        self.assertEqual(row["state"], P.CROWDED)
        self.assertEqual({f["what"] for f in row["flags"]}, {"one-sidedness", "gross position"})
        self.assertEqual(row["side"], "long")

    def test_one_sidedness_alone_is_not_a_crowd(self):
        """A hard lean on an ordinary-sized book is a lean, not a crowd."""
        n = 40
        net = [400] + [10 * (i % 7) for i in range(n)]
        gross = [400] + [400 + 5 * (i % 9) for i in range(n)]
        cftc = {"x": market("x", "X", "index", lev_net=net, lev_gross=gross,
                            lev_long=gross, lev_short=[1] * (n + 1))}
        row = P.crowding(cftc, None)["markets"][0]
        self.assertEqual([f["what"] for f in row["flags"]], ["one-sidedness"])
        self.assertNotEqual(row["state"], P.CROWDED)

    def test_net_and_one_sidedness_are_not_counted_as_two_facts(self):
        """When gross is steady, a high net drags one-sidedness up with it.
        Counting both put every quiet market with one notable reading into
        the crowded list."""
        n = 40
        cftc = {"x": market("x", "X", "index",
                            lev_net=[500] + [i * 10 for i in range(n)],
                            lev_gross=[1000] * (n + 1), lev_long=[1] * (n + 1), lev_short=[1] * (n + 1))}
        row = P.crowding(cftc, None)["markets"][0]
        self.assertEqual(row["percentiles"]["net"], 100)
        self.assertEqual(row["percentiles"]["one_sided"], 100)
        self.assertNotEqual(row["state"], P.CROWDED, "the same fact must not vote twice")

    def test_decrowding_is_a_lean_coming_off(self):
        n = 40
        # Was leaning at its maximum last week; this week noticeably less.
        net = [200, 1000] + [10 * (i % 7) for i in range(n)]
        gross = [1000] * (n + 2)
        cftc = {"x": market("x", "X", "index", lev_net=net, lev_gross=gross,
                            lev_long=gross, lev_short=[1] * (n + 2))}
        self.assertEqual(P.crowding(cftc, None)["markets"][0]["state"], P.DECROWDING)

    def test_a_flat_history_has_no_percentile_and_so_no_flag(self):
        """A series with no spread would otherwise sit at the 100th
        percentile by counting ties, flagging every quiet market."""
        self.assertIsNone(P.percentile([100] * 50, 100))
        flat = [1000] * 41
        cftc = {"x": market("x", "X", "index", lev_net=flat, lev_gross=flat,
                            lev_long=flat, lev_short=flat)}
        row = P.crowding(cftc, None)["markets"][0]
        self.assertEqual(row["flags"], [])
        self.assertEqual(row["state"], P.NORMAL)

    def test_a_thin_history_is_not_judged(self):
        cftc = {"x": market("x", "X", "index", lev_net=[1, 2, 3], lev_gross=[1, 2, 3])}
        self.assertEqual(P.crowding(cftc, None)["markets"], [])


class CrowdedShorts(unittest.TestCase):
    """FINRA caps days-to-cover at 1000, and 4,133 of the 22,482 rows in the
    August 14, 2026 report sit at that cap — all illiquid OTC tickers."""

    ROWS = {
        "CAPPED": {"symbol": "CAPPED", "days_to_cover": 1000.0, "short": 4e6, "adv": 746.0},
        "THIN": {"symbol": "THIN", "days_to_cover": 290.0, "short": 5e6, "adv": 60_000.0},
        "REAL": {"symbol": "REAL", "days_to_cover": 28.6, "short": 35e6, "adv": 1.2e6},
        "ALSOREAL": {"symbol": "ALSOREAL", "days_to_cover": 23.9, "short": 101e6, "adv": 4.3e6},
        "SMALLSHORT": {"symbol": "SMALLSHORT", "days_to_cover": 40.0, "short": 100.0, "adv": 2e6},
    }

    def test_the_capped_field_is_excluded(self):
        out = P.crowded_shorts(self.ROWS)
        self.assertNotIn("CAPPED", [r["symbol"] for r in out])

    def test_illiquid_names_are_excluded(self):
        self.assertNotIn("THIN", [r["symbol"] for r in P.crowded_shorts(self.ROWS)])

    def test_real_names_survive_and_rank_by_days_to_cover(self):
        out = P.crowded_shorts(self.ROWS)
        self.assertEqual([r["symbol"] for r in out], ["REAL", "ALSOREAL"])

    def test_a_trivial_short_is_not_crowded(self):
        self.assertNotIn("SMALLSHORT", [r["symbol"] for r in P.crowded_shorts(self.ROWS)])

    def test_it_names_securities_but_never_a_fund(self):
        for r in P.crowded_shorts(self.ROWS):
            self.assertEqual(r["class"], S.REGULATORY)
            self.assertNotIn("fund", r)


class Attribution(unittest.TestCase):
    def test_no_input_the_pulse_builds_can_carry_a_fund(self):
        cftc = {"sp500": market(lev_net=[1, 2] + [2] * 18, lev_gross=[1, 2] + [2] * 18,
                                lev_long=[1] * 20, lev_short=[1] * 20)}
        board = P.build(cftc, short_interest={"total": 1, "change": 1, "rows": {}})
        rows = [i for q in board["questions"] for i in q["inputs"]]
        rows += [i for r in board["sectors"]["rows"] for i in r["inputs"]]
        self.assertTrue(rows)
        self.assertTrue(S.attribution_ok(rows))
        for i in rows:
            self.assertIn(i["class"], (S.REGULATORY, S.FLOW_PROXY, S.PRIME_BROKER))

    def test_a_prime_broker_quote_corroborates_and_never_decides(self):
        cftc = {"sp500": market(lev_net=[10, 5] + [5] * 18, lev_gross=[1] * 20,
                                lev_long=[1] * 20, lev_short=[1] * 20)}
        quote = {"about": "exposure", "bank": "GS", "label": "Goldman prime brokerage, via Reuters",
                 "direction": 1, "as_of": "2026-09-04", "text": "net buyers for a fourth week"}
        with_q = P.build(cftc, prime_broker=[quote])
        self.assertIn(S.PRIME_BROKER, [i["class"] for i in with_q["exposure"]["inputs"]])
        pb = next(i for i in with_q["exposure"]["inputs"] if i["class"] == S.PRIME_BROKER)
        self.assertEqual(pb["weight"], 0.5)
        alone = P.build({}, prime_broker=[quote])
        self.assertEqual(alone["exposure"]["verdict"], P.NO_DATA,
                         "a quote with nothing behind it creates no verdict")


class ChangedSinceLastWeek(unittest.TestCase):
    def test_the_first_reading_says_so(self):
        out = P.changed_since({}, None)
        self.assertFalse(out["available"])
        self.assertIn("first reading", out["note"])

    def test_a_flipped_verdict_is_reported(self):
        prior = {"week": "2026-W35", "exposure": {"verdict": P.REDUCING},
                 "sectors": {"rows": [{"sector": "Energy", "verdict": P.ADDING}]},
                 "crowding": {"crowded": [{"key": "russell"}]}}
        now = {"exposure": {"verdict": P.ADDING, "question": "Are hedge funds increasing or reducing exposure?"},
               "sectors": {"rows": [{"sector": "Energy", "verdict": P.REDUCING}]},
               "crowding": {"crowded": [{"key": "nasdaq"}]}}
        out = P.changed_since(now, prior)
        kinds = {(c["from"], c["to"]) for c in out["changes"]}
        self.assertIn((P.REDUCING, P.ADDING), kinds)
        self.assertIn((P.ADDING, P.REDUCING), kinds)
        self.assertTrue(any("crowding" in c["what"] for c in out["changes"]))
        self.assertEqual(out["since"], "2026-W35")

    def test_nothing_changed_is_a_finding_not_an_error(self):
        prior = {"week": "2026-W35", "exposure": {"verdict": P.ADDING}}
        now = {"exposure": {"verdict": P.ADDING, "question": "q"}}
        out = P.changed_since(now, prior)
        self.assertTrue(out["available"])
        self.assertEqual(out["n"], 0)


class Purity(unittest.TestCase):
    def test_no_io_and_no_clock(self):
        src = open("hf_pulse.py", encoding="utf-8").read()
        for bad in ("import requests", "urllib", "open(", "datetime.now", "date.today",
                    "Path(", "json.load"):
            self.assertNotIn(bad, src, f"hf_pulse must stay pure: {bad}")


if __name__ == "__main__":
    unittest.main()
