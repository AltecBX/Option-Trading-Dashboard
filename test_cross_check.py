"""The company's own published figure beside the one rebuilt from XBRL.

The failure this file exists to prevent is a comparison that looks like a
check and is really a mismatch of definitions: Simon Property Group's FFO of
the Operating Partnership against a reconstruction of FFO attributable to
common shareholders, or a published quarter against a trailing twelve
months. Both would report a large disagreement that says nothing about
whether the reconstruction is right.
"""
import unittest

import cross_check as X


def compare(published, reconstructed, **kw):
    return X.compare("Funds from operations", published, reconstructed,
                     "USD", **kw)


class TestStates(unittest.TestCase):
    def test_the_same_number_twice_is_a_match(self):
        got = compare(1000.0, 1000.0)
        self.assertEqual(got["state"], X.MATCH)
        self.assertAlmostEqual(got["difference_pct"], 0.0)

    def test_a_rounding_apart_is_a_minor_difference(self):
        got = compare(1000.0, 970.0)
        self.assertEqual(got["state"], X.MINOR)

    def test_a_real_disagreement_is_material(self):
        got = compare(1000.0, 600.0)
        self.assertEqual(got["state"], X.MATERIAL)
        self.assertAlmostEqual(got["difference_pct"], 40.0)

    def test_no_published_figure_is_not_a_failure(self):
        got = compare(None, 1000.0)
        self.assertEqual(got["state"], X.UNAVAILABLE)
        self.assertTrue(got["reason"])

    def test_no_reconstruction_is_not_a_mismatch_either(self):
        got = compare(1000.0, None)
        self.assertEqual(got["state"], X.INCOMPATIBLE)


class TestLikeForLike(unittest.TestCase):
    def test_a_different_basis_is_incompatible(self):
        """Simon publishes FFO of the Operating Partnership and FFO allocable
        to common stockholders in one table, and they differ by the limited
        partners rather than by an error."""
        got = compare(1_184_945.0, 1_010_258.0,
                      published_basis="OPERATING PARTNERSHIP FFO",
                      reconstructed_basis="FFO ATTRIBUTABLE TO COMMON "
                                          "SHAREHOLDERS")
        self.assertEqual(got["state"], X.INCOMPATIBLE)
        self.assertIn("share a name", got["reason"])

    def test_a_different_period_is_incompatible(self):
        got = compare(1000.0, 1000.0, published_period="2025-12-31",
                      reconstructed_period="2026-06-30")
        self.assertEqual(got["state"], X.INCOMPATIBLE)
        self.assertIn("2025-12-31", got["reason"])

    def test_a_quarter_against_a_year_is_incompatible(self):
        got = compare(996.6, 3_900.0, published_window="QUARTER",
                      reconstructed_window="FULL YEAR")
        self.assertEqual(got["state"], X.INCOMPATIBLE)

    def test_a_window_the_table_names_twice_is_incompatible(self):
        """Realty Income heads one table with a quarter AND a half-year."""
        got = compare(996.6, 996.6, published_window="AMBIGUOUS",
                      reconstructed_window="AMBIGUOUS")
        self.assertEqual(got["state"], X.INCOMPATIBLE)
        self.assertIn("two windows at once", got["reason"])

    def test_a_segment_figure_is_never_the_company_figure(self):
        got = X.compare("Combined ratio", 82.8, 86.1, "percent",
                        published_scope="SEGMENT")
        self.assertEqual(got["state"], X.INCOMPATIBLE)
        self.assertIn("one segment", got["reason"])

    def test_a_consolidated_figure_is_compared(self):
        got = X.compare("Combined ratio", 83.6, 83.6, "percent",
                        published_scope="CONSOLIDATED",
                        published_period="2025-12-31",
                        reconstructed_period="2025-12-31")
        self.assertEqual(got["state"], X.MATCH)


class TestTheReport(unittest.TestCase):
    def test_a_material_mismatch_lowers_confidence(self):
        rep = X.report([compare(1000.0, 500.0)])
        self.assertEqual(rep["state"], X.MATERIAL)
        self.assertEqual(rep["confidence_penalty"], 1)
        self.assertEqual(rep["mismatches"], 1)

    def test_an_agreement_costs_nothing(self):
        rep = X.report([compare(1000.0, 1000.0)])
        self.assertEqual(rep["state"], X.MATCH)
        self.assertEqual(rep["confidence_penalty"], 0)
        self.assertIn("outside check", rep["reason"])

    def test_incompatible_alone_is_not_a_mismatch(self):
        rep = X.report([compare(1000.0, 500.0, published_window="QUARTER",
                                reconstructed_window="FULL YEAR")])
        self.assertEqual(rep["state"], X.INCOMPATIBLE)
        self.assertEqual(rep["mismatches"], 0)
        self.assertEqual(rep["confidence_penalty"], 0)

    def test_nothing_to_check_is_not_a_failure(self):
        rep = X.report([])
        self.assertEqual(rep["state"], X.NOT_CHECKED)
        self.assertTrue(rep["reason"])

    def test_one_mismatch_among_agreements_still_counts(self):
        rep = X.report([compare(1000.0, 1000.0), compare(100.0, 40.0)])
        self.assertEqual(rep["state"], X.MATERIAL)
        self.assertEqual(rep["checks_run"], 2)

    def test_neither_number_is_replaced_by_the_other(self):
        got = compare(1000.0, 600.0)
        self.assertAlmostEqual(got["published"], 1000.0)
        self.assertAlmostEqual(got["reconstructed"], 600.0)


class TestMeasureAudit(unittest.TestCase):
    def rows(self):
        return {
            "client_assets": {
                "value": 1.2e12, "label": "Client assets", "kind": "BALANCE",
                "confidence": "HIGH", "form": "10-Q", "filed": "2026-08-07",
                "provenance": {"period": "2026-06-30", "scope": "CONSOLIDATED",
                               "resolved_unit": "billions",
                               "unit_source": "row label",
                               "row_label": "Client assets (in billions)"}},
            "assets_under_management": {
                "value": 4.0e11, "label": "Assets under management",
                "kind": "BALANCE", "confidence": "HIGH", "form": "8-K",
                "filed": "2026-07-15",
                "provenance": {"period": "2026-03-31", "scope": "UNSTATED",
                               "resolved_unit": "millions",
                               "unit_source": "caption",
                               "row_label": "Assets under management"}},
        }

    def test_each_measure_keeps_its_own_period_scope_and_unit(self):
        got = X.audit_measures(self.rows())
        by = {r["metric"]: r for r in got["rows"]}
        self.assertEqual(by["client_assets"]["period"], "2026-06-30")
        self.assertEqual(by["assets_under_management"]["period"], "2026-03-31")
        self.assertEqual(by["client_assets"]["unit"], "billions")
        self.assertEqual(by["assets_under_management"]["unit"], "millions")

    def test_two_measures_are_never_added_or_swapped(self):
        got = X.audit_measures(self.rows())
        self.assertEqual(len(got["rows"]), 2)
        self.assertTrue(got["mixed_periods"])
        self.assertIn("None of them stands in for another", got["reason"])

    def test_nothing_read_is_said_plainly(self):
        got = X.audit_measures({})
        self.assertEqual(got["rows"], [])
        self.assertIn("will read", got["reason"])


if __name__ == "__main__":                               # pragma: no cover
    unittest.main()
