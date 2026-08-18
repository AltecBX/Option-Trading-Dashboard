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
                         ["FFO available to common stockholders", "$996.6"]],
                        caption="(dollars in millions)"),
                  table([["", "June 30, 2026"],
                         ["FFO available to common stockholders", "$996,600"]],
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
                         ["FFO available to common stockholders",
                          "$996.6", "$955.7"]],
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

    def test_a_balance_that_tripled_in_a_quarter_is_flagged_not_rewritten(self):
        """Continuity may raise a hand. It may not overrule the filing."""
        got = T.continuity("client_assets", 4e12, 1e12)
        self.assertEqual(got["state"], T.FLAGGED)
        self.assertTrue(got["ok"])
        self.assertIn("shown with this note", got["reason"])

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


# ── Phase 7 ──────────────────────────────────────────────────────────────────

class TestColumnAlignment(unittest.TestCase):
    """The figure comes from the column its period heads, not from the front
    of the row. Affiliated Managers prints 2025 before 2026."""

    def test_the_newest_column_wins_even_when_it_is_not_first(self):
        raw = doc(table([["", "As of and for the Three Months Ended June 30,"],
                         ["(in billions, except as noted)", "2025", "2026",
                          "% Change"],
                         ["Assets under management", "$771.0", "$942.4",
                          "22 %"]],
                        caption="key aggregate operating performance measures"))
        got = T.read(raw, "assets_under_management", not_after="2026-08-07")
        self.assertAlmostEqual(got["value"], 942.4e9)
        self.assertTrue(got["provenance"]["columns_aligned"])

    def test_a_heading_row_with_no_stub_still_lines_up(self):
        """Schwab heads its columns ['2026','2025'] with no label cell, and
        reading it the other way took the June 2025 figure."""
        raw = doc(table([["", "Three Months Ended June 30,", "Percent Change"],
                         ["2026", "2025", "22%"],
                         ["Client assets (in billions, at quarter end)",
                          "$13,084.9", "$10,757.3", "22%"]]))
        got = T.read(raw, "client_assets", not_after="2026-08-07")
        self.assertAlmostEqual(got["value"], 13_084.9e9)

    def test_a_stub_that_holds_a_label_is_not_a_column(self):
        """Interactive Brokers puts "Year over Year" in its stub cell."""
        raw = doc(table([["Year over Year", "2Q2026", "2Q2025", "% Change"],
                         ["Customer Equity (in billions)", "$930.3", "$664.6",
                          "40%"]]))
        got = T.read(raw, "client_assets", not_after="2026-08-06")
        self.assertAlmostEqual(got["value"], 930.3e9)

    def test_a_change_column_is_not_a_period(self):
        raw = doc(table([["", "June 30, 2026", "% Change"],
                         ["Total client assets", "$13,084.9", "22"]],
                        caption="(in billions)"))
        got = T.read(raw, "client_assets", not_after="2026-08-07")
        self.assertAlmostEqual(got["value"], 13_084.9e9)


class TestTwoPanelsInOneTable(unittest.TestCase):
    def test_a_row_with_a_word_in_the_middle_is_refused(self):
        """BlackRock lays two tables side by side, so its "Total net flows"
        row reads ['$191,700', '$67,737', 'EMEA', '55', '68']."""
        raw = doc(table([["(in millions,", "Q2", "Q2", "Q2", "YTD"],
                         ["except per share data)", "2026", "2025",
                          "(in billions)", "2026", "2026"],
                         ["Total net flows", "$191,700", "$67,737", "EMEA",
                          "55", "68"]]))
        got = T.read(raw, "net_flows", not_after="2026-07-15")
        self.assertIsNone(got["value"])

    def test_a_not_meaningful_marker_is_not_a_word(self):
        raw = doc(table([["", "June 30, 2026", "June 30, 2025"],
                         ["Net new assets", "$118.7", "n/m"]],
                        caption="(in billions)"))
        got = T.read(raw, "net_new_assets", not_after="2026-08-07")
        self.assertAlmostEqual(got["value"], 118.7e9)


class TestSegmentScope(unittest.TestCase):
    def test_a_consolidated_table_beats_the_segment_ones(self):
        """Travelers prints a combined ratio five times and heads one of them
        consolidated."""
        filler = "<p>" + ("Results of operations continued. " * 40) + "</p>"
        raw = doc(table([["", "June 30, 2026"], ["Combined ratio", "86.8%"]],
                        caption="OF OPERATIONS BY SEGMENT Business Insurance"),
                  filler,
                  table([["", "June 30, 2026"], ["Combined ratio", "83.6%"]],
                        caption="CONSOLIDATED OVERVIEW Consolidated Results"),
                  filler,
                  table([["", "June 30, 2026"], ["Combined ratio", "79.5%"]],
                        caption="Personal Insurance segment results"))
        got = T.read(raw, "published_combined_ratio", not_after="2026-07-17")
        self.assertAlmostEqual(got["value"], 83.6)
        self.assertEqual(got["provenance"]["scope"], T.CONSOLIDATED)

    def test_segment_tables_alone_are_not_a_company_figure(self):
        filler = "<p>" + ("Results of operations continued. " * 40) + "</p>"
        raw = doc(table([["", "June 30, 2026"], ["Combined ratio", "86.8%"]],
                        caption="Segment results — Business Insurance"),
                  filler,
                  table([["", "June 30, 2026"], ["Combined ratio", "79.5%"]],
                        caption="Segment results — Personal Insurance"))
        got = T.read(raw, "published_combined_ratio", not_after="2026-07-17")
        self.assertIsNone(got["value"])
        self.assertIn("one segment", got["reason"])


class TestTransposedTables(unittest.TestCase):
    def test_periods_in_rows_with_a_total_column(self):
        """Invesco publishes assets under management with dates down the side
        and asset classes across the top."""
        raw = doc(table([["Total Assets Under Management"],
                         ["(in billions)", "Total", "ETFs", "Fixed Income"],
                         ["July 31, 2026", "$2,447.1", "$750.5", "$315.7"],
                         ["June 30, 2026", "$2,470.3", "$753.5", "$315.5"]]))
        got = T.read(raw, "assets_under_management", not_after="2026-08-11")
        self.assertAlmostEqual(got["value"], 2_447.1e9)
        self.assertEqual(got["provenance"]["period"], "2026-07-31")
        self.assertIn("total column", got["provenance"]["layout"])

    def test_a_strategy_column_is_never_taken_for_the_company(self):
        raw = doc(table([["Total Assets Under Management"],
                         ["(in billions)", "ETFs", "Fixed Income"],
                         ["July 31, 2026", "$750.5", "$315.7"]]))
        got = T.read(raw, "assets_under_management", not_after="2026-08-11")
        self.assertIsNone(got["value"])


class TestMorePeriodShapes(unittest.TestCase):
    def test_a_day_month_year_heading_is_read(self):
        """Franklin Resources heads every column 30-Jun-26."""
        self.assertEqual(T.period_of("30-Jun-26")["date"], "2026-06-30")
        self.assertEqual(T.period_of("31-Mar-2026")["date"], "2026-03-31")

    def test_a_curly_apostrophe_quarter_is_read(self):
        """Blackstone writes 2Q’26."""
        self.assertEqual(T.period_of("2Q’26")["date"], "2026-06-30")

    def test_a_bare_quarter_over_a_bare_year_is_paired(self):
        raw = doc(table([["", "Q2", "Q2"], ["", "2026", "2025"],
                         ["Total client assets", "$13,084.9", "$10,757.3"]],
                        caption="(in billions)"))
        got = T.read(raw, "client_assets", not_after="2026-07-15")
        self.assertEqual(got["provenance"]["period"], "2026-06-30")


class TestFundsFromOperationsBasis(unittest.TestCase):
    def test_the_common_shareholder_basis_is_the_one_used(self):
        for label in ("FFO available to common stockholders",
                      "Funds from operations attributable to common "
                      "shareholders",
                      "Dilutive FFO allocable to common stockholders",
                      "Nareit FFO attributable to American Tower Corporation "
                      "common stockholders"):
            self.assertEqual(T.ffo_basis(label), T.FFO_COMMON, label)

    def test_the_operating_partnership_basis_is_refused(self):
        for label in ("FFO of the Operating Partnership",
                      "FFO of the Operating Partnership excluding non-cash "
                      "impacts",
                      "Diluted FFO allocable to unitholders"):
            self.assertEqual(T.ffo_basis(label), T.FFO_PARTNERSHIP, label)

    def test_core_normalized_and_adjusted_are_their_own_measures(self):
        self.assertEqual(T.ffo_basis("Core FFO"), T.FFO_CORE)
        self.assertEqual(T.ffo_basis("Normalized FFO"), T.FFO_NORMALIZED)
        self.assertEqual(T.ffo_basis("AFFO"), T.FFO_AFFO)
        self.assertEqual(T.ffo_basis("Adjusted funds from operations"),
                         T.FFO_AFFO)

    def test_a_per_share_figure_is_not_a_company_total(self):
        self.assertEqual(T.ffo_basis("Diluted FFO per share"), T.FFO_PER_SHARE)
        self.assertEqual(T.ffo_basis("Basic and Diluted FFO per Share (FFOPS)"),
                         T.FFO_PER_SHARE)

    def test_a_bare_label_does_not_say_whose(self):
        self.assertEqual(T.ffo_basis("Funds from operations"),
                         T.FFO_UNQUALIFIED)

    def test_a_row_that_is_not_ffo_at_all_is_not_matched(self):
        self.assertIsNone(T.ffo_basis("Net income"))

    def test_only_the_common_basis_reaches_the_metric(self):
        raw = doc(table([["", "June 30, 2026"],
                         ["FFO of the Operating Partnership", "$1,184,945"],
                         ["Dilutive FFO allocable to common stockholders",
                          "$1,010,258"],
                         ["Real Estate FFO", "$1,248,564"]],
                        caption="(in thousands)"))
        got = T.read(raw, "published_ffo", not_after="2026-08-10")
        self.assertAlmostEqual(got["value"], 1_010_258e3)
        self.assertEqual(got["provenance"]["basis"], T.FFO_COMMON)


class TestUnitProvenance(unittest.TestCase):
    def test_every_reading_says_where_its_scale_came_from(self):
        raw = doc(table([["", "June 30, 2026"],
                         ["Customer Equity (in billions)", "$930.3"]],
                        caption="(in thousands)"))
        prov = T.read(raw, "client_assets", not_after="2026-08-06")["provenance"]
        self.assertEqual(prov["resolved_unit"], "billions")
        self.assertEqual(prov["unit_source"], "row label")
        self.assertEqual(prov["unit_confidence"], "HIGH")
        self.assertAlmostEqual(prov["raw_value"], 930.3)
        self.assertTrue(prov["unit_overrides_caption"])
        self.assertEqual(prov["unit_overridden"], "thousands")

    def test_a_section_heading_lifts_a_row_out_of_the_caption(self):
        """T. Rowe Price's assets under management, recovered."""
        raw = doc(table([["", "6/30/2026"],
                         ["Investment advisory fees", "$1,744.8"],
                         ["Assets under management (in billions) (4)"],
                         ["Ending assets under management", "$1,893.4"]],
                        caption="(in millions, except per-share data)"))
        got = T.read(raw, "assets_under_management", not_after="2026-07-31")
        self.assertAlmostEqual(got["value"], 1_893.4e9)
        self.assertEqual(got["provenance"]["unit_source"], "section heading")


class TestDocumentSelection(unittest.TestCase):
    def _sec(self, names):
        class Sec:
            def _fetch(self, url, limit=None):
                import json as _j
                return _j.dumps({"directory": {"item": [
                    {"name": n, "size": z} for n, z in names]}}).encode()
        return Sec()

    def test_an_indenture_and_a_cover_page_are_not_earnings_releases(self):
        """Realty Income files bond-offering 8-Ks carrying 700KB indentures."""
        docs = T.documents(self._sec([("tm123_ex4-1.htm", 732310),
                                      ("tm123_ex10-1.htm", 313427),
                                      ("tm123_8k.htm", 86164),
                                      ("tm123_ex99-1.htm", 21436)]),
                           726728, "0001-23-000001", "8-K")
        self.assertEqual([d["name"] for d in docs], ["tm123_ex99-1.htm"])

    def test_a_release_whose_filename_never_says_exhibit_is_still_read(self):
        """T. Rowe Price files its earnings release as
        earningsreleaseq22026.htm, and the EDGAR directory entry carries only
        a name and a size — not the exhibit type."""
        docs = T.documents(self._sec([("earningsreleaseq22026.htm", 220000),
                                      ("trow_8k.htm", 5000)]),
                           1113169, "0001-26-000002", "8-K")
        self.assertEqual([d["name"] for d in docs],
                         ["earningsreleaseq22026.htm"])

    def test_the_exhibit_number_ninety_nine_survives_every_spelling(self):
        for name in ("blk-ex99_1.htm", "exhibit991q3fy26.htm",
                     "a2q26exhibit991.htm", "d153439dex991.htm",
                     "amgq22026ex991.htm"):
            docs = T.documents(self._sec([(name, 100)]), 1, "0001-1-1", "8-K")
            self.assertEqual([d["name"] for d in docs], [name], name)

    def test_a_ten_q_reads_its_main_document(self):
        class Sec:
            def _fetch(self, url, limit=None):
                import json as _j
                return _j.dumps({"directory": {"item": [
                    {"name": "o-20260630.htm", "size": 5073501},
                    {"name": "o-063026ex311.htm", "size": 10834}]}}).encode()
        docs = T.documents(Sec(), 726728, "0001-23-000002", "10-Q")
        self.assertEqual(docs[0]["name"], "o-20260630.htm")
