"""What scale a number in a filing table is printed on.

Every case here is a real one. A caption that says millions above a row that
says billions is not a curiosity — it is T. Rowe Price's assets under
management, and reading it the wrong way turns $1.9 trillion of other
people's money into $1.9 billion. The rule these tests hold the code to is
that the scale comes from words a filer wrote, at the most specific place it
was written, or the figure is refused. It is never inferred from how large
the number looks.
"""
import unittest

import filing_units as U


class TestPrecedence(unittest.TestCase):
    def test_the_row_s_own_label_beats_everything(self):
        got = U.resolve(row_label="AUM (at period end, in billions)",
                        column_header="6/30/2026",
                        table_heading="(in millions, except as noted)",
                        caption="(in millions)")
        self.assertEqual(got["unit"], "billions")
        self.assertEqual(got["source"], "row label")
        self.assertEqual(got["confidence"], U.HIGH)

    def test_a_section_heading_beats_the_table_and_the_caption(self):
        """T. Rowe heads a block "Assets under management (in billions)"
        eighteen rows into a table captioned in millions."""
        got = U.resolve(row_label="Ending assets under management",
                        section_heading="Assets under management (in billions) (4)",
                        table_heading="(in millions, except per-share data)",
                        caption="(in millions, except per-share data)")
        self.assertEqual(got["unit"], "billions")
        self.assertEqual(got["source"], "section heading")
        self.assertTrue(got["overrides_caption"])
        self.assertEqual(got["overridden_unit"], "millions")

    def test_a_column_header_beats_the_table(self):
        got = U.resolve(row_label="Total AUM", column_header="(in billions)",
                        table_heading="(in millions)")
        self.assertEqual(got["unit"], "billions")
        self.assertEqual(got["source"], "column header")

    def test_the_table_heading_beats_the_caption(self):
        got = U.resolve(row_label="Total AUM",
                        table_heading="(in billions) 30-Jun-26",
                        caption="dollars in millions")
        self.assertEqual(got["unit"], "billions")
        self.assertEqual(got["source"], "table heading")
        self.assertEqual(got["confidence"], U.MODERATE)

    def test_the_caption_is_the_last_resort(self):
        got = U.resolve(row_label="Total client assets",
                        caption="(In billions, at quarter end)")
        self.assertEqual(got["unit"], "billions")
        self.assertEqual(got["source"], "caption")
        self.assertEqual(got["confidence"], U.LOW)


class TestRefusals(unittest.TestCase):
    def test_nothing_says_the_scale_and_it_is_not_guessed(self):
        got = U.resolve(row_label="Total client assets",
                        column_header="June 30, 2026")
        self.assertIsNone(got["unit"])
        self.assertIn("factor of a thousand", got["reason"])

    def test_a_level_that_says_two_things_is_ambiguous(self):
        got = U.resolve(table_heading="(in millions, ...) (in billions)")
        self.assertIsNone(got["unit"])
        self.assertTrue(got.get("ambiguous"))
        self.assertIn("more than one unit", got["reason"])

    def test_a_conflict_does_not_fall_through_to_a_vaguer_level(self):
        """If the columns disagree about the column, the caption is not the
        tie-breaker."""
        got = U.resolve(column_header="(in millions) (in billions)",
                        caption="(in thousands)")
        self.assertIsNone(got["unit"])
        self.assertEqual(got["source"], "column header")

    def test_a_big_number_is_not_evidence_of_anything(self):
        """No magnitude guessing, ever."""
        for raw in (1_893.4, 15_344_624, 0.4):
            got = U.resolve(row_label="Assets under management")
            self.assertIsNone(got["unit"])
            self.assertIsNone(U.normalise(raw, got["unit"], "MONEY")["value"])


class TestTheHedge(unittest.TestCase):
    def test_a_caption_that_excepts_something_is_marked(self):
        self.assertTrue(U.hedged("(in millions, except per-share data)"))
        self.assertFalse(U.hedged("(in millions)"))

    def test_an_exception_is_not_a_declaration(self):
        """"except per-share data" does not make the table per-share."""
        self.assertEqual(U.units_in("(in millions, except per share data)"),
                         ["millions"])

    def test_a_hedged_caption_is_flagged_when_it_is_all_there_is(self):
        got = U.resolve(row_label="Ending assets under management",
                        caption="(in millions, except per-share data)")
        self.assertEqual(got["unit"], "millions")
        self.assertTrue(got["from_hedged_caption"])


class TestOtherUnits(unittest.TestCase):
    def test_basis_points_are_understood(self):
        self.assertIn("basis points", U.units_in("Net interest margin (basis points)"))
        self.assertAlmostEqual(
            U.normalise(125, "basis points", "RATIO")["value"], 1.25)

    def test_percent_is_declared_not_spotted(self):
        """"% Change" is the name of a column, not a statement of units."""
        self.assertEqual(U.units_in("% Change"), [])
        self.assertEqual(U.units_in("(in percent)"), ["percent"])

    def test_per_share_is_declared_not_spotted(self):
        self.assertEqual(U.units_in("Diluted earnings per share"), [])
        self.assertEqual(U.units_in("(per share)"), ["per share"])

    def test_thousands_and_trillions_both_read(self):
        self.assertEqual(U.units_in("($ in thousands, except per share data)",
                                    strict=True), ["thousands"])
        self.assertEqual(U.units_in("(in trillions)"), ["trillions"])


class TestStrictness(unittest.TestCase):
    def test_prose_about_a_billion_is_not_a_table_unit(self):
        """Invesco's caption reads "market returns which decreased AUM by $59
        billion" — a sentence about a change, not a scale."""
        self.assertEqual(
            U.units_in("market returns which decreased AUM by $59 billion. FX "
                       "increased AUM by $4.6 billion.", strict=True), [])

    def test_a_structural_label_may_be_terser(self):
        self.assertEqual(U.units_in("Customer Equity (in billions)"),
                         ["billions"])
        self.assertEqual(U.units_in("(billions)"), ["billions"])
        self.assertEqual(U.units_in("AUM, in billions"), ["billions"])


class TestNormalising(unittest.TestCase):
    def test_a_money_figure_is_converted_by_its_scale(self):
        self.assertAlmostEqual(
            U.normalise(1_893.4, "billions", "MONEY")["value"], 1_893.4e9)
        self.assertAlmostEqual(
            U.normalise(15_344_624, "millions", "MONEY")["value"],
            15_344_624e6)

    def test_a_ratio_is_not_a_money_amount(self):
        got = U.normalise(83.6, "percent", "MONEY")
        self.assertIsNone(got["value"])
        self.assertIn("cannot be", got["reason"])

    def test_a_per_share_figure_is_not_a_company_total(self):
        got = U.normalise(3.12, "per share", "MONEY")
        self.assertIsNone(got["value"])

    def test_every_reading_carries_its_raw_number_and_its_unit(self):
        got = U.resolve(row_label="Customer Equity (in billions)")
        norm = U.normalise(930.3, got["unit"], "MONEY")
        self.assertEqual(got["raw_unit"], "billions")
        self.assertEqual(norm["unit"], "billions")
        self.assertEqual(norm["family"], "MONEY")
        self.assertAlmostEqual(norm["value"], 930.3e9)


if __name__ == "__main__":                               # pragma: no cover
    unittest.main()
