"""Reading operating measures out of SEC filing tables, strictly.

The traps in this file are all real ones, measured against real filings:
a percentage-change column printed before the dollars, a caption in millions
above a row printed in billions, a heading that names two windows at once,
five segment rows sharing one label, a column with no period on it. Each one
turns a right answer into a wrong one, and each one has to end in a refusal
rather than a number.
"""
import unittest

import filing_tables as T


def table(rows, caption="", attrs=""):
    """A filing table, with the spacer cells that real ones are full of."""
    out = [f"<p>{caption}</p><table{attrs}>"]
    for r in rows:
        cells = "".join(f"<td></td><td>{c}</td>" for c in r)
        out.append(f"<tr>{cells}</tr>")
    out.append("</table>")
    return "".join(out)


def doc(*parts) -> bytes:
    return ("<html><body>" + "".join(parts) + "</body></html>").encode()


class TestControlledAliases(unittest.TestCase):
    def test_a_listed_label_is_read(self):
        raw = doc(table([["", "June 30, 2026"],
                         ["Total client assets", "$13,084.9"]],
                        caption="(In billions, at quarter end)"))
        got = T.read(raw, "client_assets")
        self.assertAlmostEqual(got["value"], 13_084.9e9)
        self.assertEqual(got["provenance"]["period"], "2026-06-30")

    def test_assets_alone_is_not_client_assets(self):
        raw = doc(table([["", "June 30, 2026"], ["Assets", "$13,084.9"]],
                        caption="(In billions)"))
        got = T.read(raw, "client_assets")
        self.assertIsNone(got["value"])
        self.assertIn("No table", got["reason"])

    def test_a_footnote_marker_and_an_abbreviation_come_off_the_label(self):
        self.assertEqual(T.normalise_label("Total client assets (1)"),
                         "total client assets")
        self.assertEqual(
            T.normalise_label('Assets under administration (“AUA”)'),
            "assets under administration")
        self.assertEqual(T.normalise_label("Customer Equity (in billions) 1"),
                         "customer equity")


class TestAmbiguity(unittest.TestCase):
    def test_five_segment_rows_sharing_a_label_are_refused(self):
        """Travelers prints a combined ratio for every segment."""
        raw = doc(table([["", "June 30, 2026"], ["Combined ratio", "82.8%"]],
                        caption="Bond & Specialty Insurance"),
                  table([["", "June 30, 2026"], ["Combined ratio", "101.7%"]],
                        caption="Personal Insurance"))
        got = T.read(raw, "published_combined_ratio")
        self.assertIsNone(got["value"])
        self.assertIn(T.AMBIGUOUS, got["reason"])

    def test_two_tables_agreeing_are_not_ambiguous(self):
        """Realty Income prints the same figure in millions and in thousands."""
        raw = doc(table([["", "June 30, 2026"],
                         ["Funds from operations", "$996.6"]],
                        caption="(dollars in millions)"),
                  table([["", "June 30, 2026"],
                         ["Funds from operations", "$996,600"]],
                        caption="(in thousands)"))
        got = T.read(raw, "published_ffo")
        self.assertAlmostEqual(got["value"], 996_600_000.0)


class TestUnits(unittest.TestCase):
    def test_a_table_that_never_states_its_scale_is_refused(self):
        raw = doc(table([["", "June 30, 2026"],
                         ["Total client assets", "13,084.9"]]))
        got = T.read(raw, "client_assets")
        self.assertIsNone(got["value"])
        self.assertIn("thousands, millions or billions", got["reason"])

    def test_the_row_s_own_scale_beats_the_table_s(self):
        """Interactive Brokers prints billions inside a thousands table."""
        raw = doc(table([["", "June 30, 2026"],
                         ["Customer Equity (in billions)", "$930.3"]],
                        caption="(in thousands)"))
        got = T.read(raw, "client_assets")
        self.assertAlmostEqual(got["value"], 930.3e9)
        self.assertEqual(got["provenance"]["scale_word"], "billions")

    def test_a_hedged_caption_over_a_decimal_balance_is_refused(self):
        """T. Rowe prints AUM in billions inside a millions table."""
        raw = doc(table([["", "6/30/2026"],
                         ["Ending assets under management", "$1,893.4"]],
                        caption="(in millions, except per-share data)"))
        got = T.read(raw, "assets_under_management")
        self.assertIsNone(got["value"])
        self.assertIn("factor of a thousand", got["reason"])

    def test_a_hedged_caption_over_a_whole_number_is_used(self):
        """BlackRock's AUM really is on the caption's scale."""
        raw = doc(table([["", "6/30/2026"],
                         ["Assets under management", "$15,344,624"]],
                        caption="(in millions, except per share data)"))
        got = T.read(raw, "assets_under_management")
        self.assertAlmostEqual(got["value"], 15_344_624e6)

    def test_a_percentage_column_is_never_a_money_figure(self):
        """Schwab prints two percent-change columns before the dollars."""
        raw = doc(table([["", "Q2-26 % Change", "2026"],
                         ["Total client assets", "11%", "6%", "$13,084.9"]],
                        caption="(In billions, at quarter end)"))
        got = T.read(raw, "client_assets")
        self.assertAlmostEqual(got["value"], 13_084.9e9)


class TestPeriods(unittest.TestCase):
    def test_a_figure_with_no_period_is_refused(self):
        raw = doc(table([["", "Current"], ["Total client assets", "$100.0"]],
                        caption="(in billions)"))
        got = T.read(raw, "client_assets")
        self.assertIsNone(got["value"])
        self.assertIn("names no period", got["reason"])

    def test_a_month_and_day_heading_pairs_with_the_year_beside_it(self):
        raw = doc(table([["", "Three Months Ended June 30,"], ["", "2026", "2025"],
                         ["Funds from operations", "$996.6", "$955.7"]],
                        caption="(dollars in millions)"))
        got = T.read(raw, "published_ffo")
        self.assertEqual(got["provenance"]["period"], "2026-06-30")

    def test_a_period_after_the_filing_date_is_not_a_period(self):
        raw = doc(table([["", "2026", "2025"],
                         ["Total client assets", "$100.0"]],
                        caption="(in billions) June 30, 2026"))
        got = T.read(raw, "client_assets", not_after="2026-08-03")
        self.assertEqual(got["provenance"]["period"], "2026-06-30")

    def test_a_quarter_end_beats_a_press_release_date(self):
        raw = doc(table([["", "July 15, 2026 June 30, 2026"],
                         ["Combined ratio", "90.0"]]))
        got = T.read(raw, "published_combined_ratio")
        self.assertEqual(got["provenance"]["period"], "2026-06-30")

    def test_a_slash_date_is_read(self):
        self.assertEqual(T.period_of("6/30/2026")["date"], "2026-06-30")

    def test_a_quarter_label_is_read(self):
        self.assertEqual(T.period_of("Q2-26")["date"], "2026-06-30")
        self.assertEqual(T.period_of("2Q2026")["date"], "2026-06-30")


class TestWindows(unittest.TestCase):
    def test_a_heading_naming_two_windows_is_ambiguous(self):
        self.assertEqual(
            T.window_of("Three months ended June 30, Six months ended June 30,"),
            "AMBIGUOUS")

    def test_a_quarter_and_a_year_to_date_are_told_apart(self):
        self.assertEqual(T.window_of("Three months ended June 30,"), "QUARTER")
        self.assertEqual(T.window_of("Year to date"), "YEAR TO DATE")


class TestPlausibility(unittest.TestCase):
    def test_a_combined_ratio_of_three_thousand_is_refused(self):
        raw = doc(table([["", "June 30, 2026"], ["Combined ratio", "3,120.0"]]))
        self.assertIsNone(T.read(raw, "published_combined_ratio")["value"])

    def test_a_balance_that_moved_a_thousandfold_is_refused(self):
        got = T.continuity("client_assets", 13_084.9e9, 13_084.9e6)
        self.assertFalse(got["ok"])
        self.assertIn("unit error", got["reason"])

    def test_a_balance_that_tripled_in_a_quarter_is_held_back(self):
        got = T.continuity("client_assets", 4e12, 1e12)
        self.assertFalse(got["ok"])

    def test_an_ordinary_quarter_passes(self):
        self.assertTrue(T.continuity("client_assets", 1.1e12, 1.0e12)["ok"])

    def test_a_flow_may_swing_hard_without_being_refused(self):
        self.assertTrue(T.continuity("net_new_assets", 100e9, 40e9)["ok"])


class TestNumbers(unittest.TestCase):
    def test_parentheses_mean_negative(self):
        self.assertEqual(T.parse_number("(15)"), -15.0)

    def test_a_dash_is_not_a_number(self):
        for cell in ("—", "-", "N/A", ""):
            self.assertIsNone(T.parse_number(cell))

    def test_a_currency_sign_and_commas_are_stripped(self):
        self.assertEqual(T.parse_number("$1,234.5"), 1234.5)


class TestProvenance(unittest.TestCase):
    def test_every_reading_carries_where_it_came_from(self):
        raw = doc(table([["", "June 30, 2026"],
                         ["Total client assets", "$13,084.9"]],
                        caption="(In billions, at quarter end)"))
        prov = T.read(raw, "client_assets")["provenance"]
        for key in ("table_index", "row_label", "column_label", "raw_text",
                    "raw_value", "scale_word", "period", "period_precision",
                    "window", "method", "tables_version"):
            self.assertIn(key, prov)
        self.assertEqual(prov["row_label"], "Total client assets")
        self.assertEqual(prov["raw_text"], "$13,084.9")


class TestCache(unittest.TestCase):
    def setUp(self):
        T._MEM.clear()
        T.configure(None)

    def tearDown(self):
        T._MEM.clear()
        T.configure(None)

    def test_a_reading_is_written_once(self):
        self.assertTrue(T.remember("0001-26-1", "a.htm", {"metrics": {}}))
        self.assertFalse(T.remember("0001-26-1", "a.htm", {"metrics": {"x": 1}}))
        self.assertEqual(T.cached("0001-26-1", "a.htm"), {"metrics": {}})

    def test_a_different_accession_is_a_different_entry(self):
        T.remember("0001-26-1", "a.htm", {"metrics": {"n": 1}})
        self.assertTrue(T.remember("0001-26-2", "a.htm", {"metrics": {"n": 2}}))
        self.assertEqual(T.cached("0001-26-1", "a.htm")["metrics"]["n"], 1)

    def test_a_filing_never_read_is_absent_rather_than_empty(self):
        self.assertIsNone(T.cached("nope", "nope.htm"))


class FakeSec:
    def __init__(self, docs, rows, index):
        self.docs, self.rows, self.index = docs, rows, index
        self.fetched = []

    def cik_for(self, symbol):
        return 42

    def filings(self, symbol):
        return list(self.rows)

    def _fetch(self, url, limit=None):
        self.fetched.append(url)
        return self.docs[url]


class TestCollect(unittest.TestCase):
    def setUp(self):
        T._MEM.clear()
        T.configure(None)
        import json
        self.newest = doc(table(
            [["", "June 30, 2026"], ["Total client assets", "$13,084.9"]],
            caption="(In billions, at quarter end)"))
        self.older = doc(table(
            [["", "June 30, 2025"], ["Total client assets", "$10,000.0"]],
            caption="(In billions, at quarter end)"))
        idx = "https://www.sec.gov/Archives/edgar/data/42/000126002/index.json"
        idx2 = "https://www.sec.gov/Archives/edgar/data/42/000126001/index.json"
        base = "https://www.sec.gov/Archives/edgar/data/42/"
        self.sec = FakeSec(
            {idx: json.dumps({"directory": {"item": [
                {"name": "ex991.htm", "size": "5000"},
                {"name": "R1.htm", "size": "9999"}]}}).encode(),
             idx2: json.dumps({"directory": {"item": [
                 {"name": "ex991.htm", "size": "5000"}]}}).encode(),
             base + "000126002/ex991.htm": self.newest,
             base + "000126001/ex991.htm": self.older},
            [{"form": "8-K", "date": "2026-07-21", "accession": "0001-26-002"},
             {"form": "8-K", "date": "2025-07-21", "accession": "0001-26-001"}],
            None)

    def tearDown(self):
        T._MEM.clear()

    def test_it_returns_the_newest_reading_and_the_one_before(self):
        got = T.collect(self.sec, "SCHW", ("client_assets",))
        row = got["readings"]["client_assets"]
        self.assertAlmostEqual(row["value"], 13_084.9e9)
        self.assertAlmostEqual(row["previous"], 10_000.0e9)
        self.assertTrue(row["usable"])

    def test_the_xbrl_viewer_renderings_are_never_fetched(self):
        T.collect(self.sec, "SCHW", ("client_assets",))
        self.assertFalse(any("R1.htm" in u for u in self.sec.fetched))

    def test_history_is_kept_newest_first(self):
        got = T.collect(self.sec, "SCHW", ("client_assets",))
        periods = [(r["provenance"] or {}).get("period")
                   for r in got["history"]["client_assets"]]
        self.assertEqual(periods, sorted(periods, reverse=True))

    def test_a_second_pass_reads_nothing_new(self):
        T.collect(self.sec, "SCHW", ("client_assets",))
        before = len(self.sec.fetched)
        T.collect(self.sec, "SCHW", ("client_assets",))
        after = len(self.sec.fetched)
        # the index is listed again; the documents are not re-read
        self.assertLess(after - before, before)


if __name__ == "__main__":
    unittest.main()
