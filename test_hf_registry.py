"""Guards for hf_registry.py — a manager is not a CIK.

The seed list is checked for the properties the card relies on (every CIK
verified, no duplicates, one current identity per filing manager), and the
successor logic is exercised on the case that produced it: Pershing Square
moving to a new EDGAR filer in 2026.
"""

from __future__ import annotations

import unittest

import hf_registry as R


class Seed(unittest.TestCase):
    def setUp(self):
        self.reg = R.load_seed()
        self.ms = self.reg["managers"]

    def test_the_brief_s_nine_names_are_present(self):
        names = " ".join(m["name"] for m in self.ms).lower()
        for want in ("millennium", "citadel", "bridgewater", "two sigma", "renaissance",
                     "pershing", "scion"):
            self.assertIn(want, names)
        people = " ".join(p for m in self.ms for p in m.get("people") or []).lower()
        self.assertIn("ackman", people)
        self.assertIn("burry", people)

    def test_a_real_watchlist_not_just_the_nine(self):
        self.assertGreaterEqual(len(self.ms), 30)

    def test_every_entry_validates(self):
        for m in self.ms:
            self.assertEqual(R.validate(m), [], m["key"])

    def test_no_cik_is_claimed_twice(self):
        seen = {}
        for m in self.ms:
            for c in R.all_ciks(m):
                self.assertNotIn(c, seen, f"CIK {c} on both {seen.get(c)} and {m['key']}")
                seen[c] = m["key"]

    def test_keys_are_unique(self):
        keys = [m["key"] for m in self.ms]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_filing_manager_has_exactly_one_current_identity(self):
        for m in self.ms:
            if m.get("status") == "CEASED":
                self.assertIsNone(R.current_cik(m), m["key"])
                continue
            open_ids = [c for c in m["ciks"] if not c.get("to")]
            self.assertEqual(len(open_ids), 1, f"{m['key']} has {len(open_ids)} open identities")
            self.assertEqual(R.current_cik(m), int(open_ids[0]["cik"]))

    def test_turnover_is_declared_for_every_manager(self):
        for m in self.ms:
            self.assertIn(m["turnover"], R.TURNOVER, m["key"])

    def test_the_multi_strategy_and_quant_books_are_opaque(self):
        for key in ("citadel", "millennium", "two_sigma", "renaissance", "deshaw", "point72", "balyasny"):
            m = next(x for x in self.ms if x["key"] == key)
            self.assertEqual(m["turnover"], "OPAQUE", key)

    def test_the_concentrated_books_are_readable(self):
        for key in ("pershing", "elliott", "appaloosa", "duquesne", "berkshire"):
            m = next(x for x in self.ms if x["key"] == key)
            self.assertEqual(m["turnover"], "READABLE", key)


class Identities(unittest.TestCase):
    def test_pershing_square_follows_its_successor(self):
        m = next(x for x in R.load_seed()["managers"] if x["key"] == "pershing")
        self.assertEqual(R.current_cik(m), 2026053)
        self.assertEqual(R.all_ciks(m), [1336528, 2026053])
        self.assertEqual(R.status_of(m), "SUCCESSOR")

    def test_scion_has_ceased_and_keeps_its_last_identity(self):
        m = next(x for x in R.load_seed()["managers"] if x["key"] == "scion")
        self.assertEqual(R.status_of(m), "CEASED")
        self.assertIsNone(R.current_cik(m))
        self.assertEqual(R.last_cik(m), 1649339)

    def test_cik_index_covers_old_identities_too(self):
        idx = R.cik_index(R.load_seed())
        self.assertEqual(idx[1336528]["key"], "pershing")
        self.assertEqual(idx[2026053]["key"], "pershing")
        self.assertEqual(idx[1079114]["key"], "greenlight")

    def test_link_successor_closes_the_old_identity(self):
        entry = {"key": "x", "name": "X Capital", "turnover": "READABLE",
                 "ciks": [{"cik": 111, "from": None}]}
        out = R.link_successor(entry, 222, "X Capital Inc.", "2026-06-30")
        self.assertEqual(out["ciks"][0]["to"], "2026-06-30")
        self.assertEqual(out["ciks"][1]["cik"], 222)
        self.assertEqual(out["ciks"][1]["from"], "2026-06-30")
        self.assertEqual(R.current_cik(out), 222)
        self.assertEqual(entry["ciks"][0].get("to"), None, "the input is not mutated")

    def test_link_successor_is_idempotent(self):
        entry = {"key": "x", "name": "X", "turnover": "READABLE",
                 "ciks": [{"cik": 111, "from": None, "to": "2026-06-30"}, {"cik": 222, "from": "2026-06-30"}]}
        out = R.link_successor(entry, 222, "X Inc", "2026-06-30")
        self.assertEqual(len(out["ciks"]), 2)


class Overlay(unittest.TestCase):
    def test_overlay_replaces_appends_and_removes(self):
        seed = {"version": 1, "managers": [
            {"key": "a", "name": "A", "turnover": "READABLE", "ciks": [{"cik": 1}]},
            {"key": "b", "name": "B", "turnover": "OPAQUE", "ciks": [{"cik": 2}]}]}
        overlay = {"managers": [{"key": "a", "name": "A renamed", "turnover": "READABLE", "ciks": [{"cik": 1}]},
                                {"key": "c", "name": "C", "turnover": "READABLE", "ciks": [{"cik": 3}]}],
                   "removed": ["b"]}
        out = R.merge(seed, overlay)
        self.assertEqual([m["key"] for m in out["managers"]], ["a", "c"])
        self.assertEqual(out["managers"][0]["name"], "A renamed")
        self.assertEqual(seed["managers"][0]["name"], "A", "the seed is never edited in place")

    def test_validate_names_every_problem(self):
        bad = {"key": "", "name": "", "turnover": "SOMETIMES", "ciks": [{"cik": "abc"}]}
        problems = R.validate(bad)
        self.assertTrue(any("key" in p for p in problems))
        self.assertTrue(any("name" in p for p in problems))
        self.assertTrue(any("turnover" in p for p in problems))
        self.assertTrue(any("CIK" in p for p in problems))


class PlainLanguage(unittest.TestCase):
    def test_an_opaque_book_is_described_as_hedges_not_views(self):
        m = next(x for x in R.load_seed()["managers"] if x["key"] == "citadel")
        self.assertIn("never", R.describe(m))
        self.assertIn("view", R.describe(m))

    def test_a_ceased_manager_is_described_as_statements_only(self):
        m = next(x for x in R.load_seed()["managers"] if x["key"] == "scion")
        self.assertIn("no longer files", R.describe(m))
        self.assertIn("not a position", R.describe(m))


if __name__ == "__main__":
    unittest.main()
