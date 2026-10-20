"""Tests for the Home Depot catalog match checks in estimate_v2_draft.py, which
both the closing-repair estimator and the Site Scope Tool budget use.

Run from the repo root:  python3 -m unittest discover tests

The wrong matches are the real ones from the Oct 20, 2026 scope budget on job
00489; the good matches are real products that must still take the catalog
price.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import estimate_v2_draft as v2  # noqa: E402


def applies(query, product, llm_cost, catalog_cost):
    ok, _ = v2._catalog_match_is_plausible(query, product)
    if ok:
        ok, _ = v2._catalog_price_passes_sanity_check(llm_cost, {"unitCost": catalog_cost, "name": product})
    return ok


class WrongMatchesFrom00489(unittest.TestCase):
    def test_decorative_glass_door_for_a_6_panel_door(self):
        self.assertFalse(applies(
            "36 in. x 80 in. smooth white primed fiberglass prehung left-hand inswing 6-panel front exterior door",
            "Architectural 36 in. x 80 in. Left-Hand/Inswing Full Decorative Glass White Primed Smooth Fiberglass "
            "Prehung Front Door", 400, 1071))

    def test_1x4_board_for_a_2x6(self):
        self.assertFalse(applies("2 in. x 6 in. x 8 ft. pressure-treated lumber",
                                 "1 in. x 4 in. x 8 ft. Ground Contact Pressure-Treated Board Southern Yellow Pine "
                                 "Lumber", 8, 5.08))

    def test_9_in_tape_for_4_in(self):
        self.assertFalse(applies("4 in. x 75 ft. butyl flashing tape",
                                 "Vycor Pro 9 in. x 75 ft. Roll Fully-Adhered Butyl Flashing Tape (56 sq. ft.)",
                                 30, 91.74))

    def test_much_cheaper_product_is_a_different_one(self):
        self.assertFalse(applies("Amerimax aluminum kick-out flashing", "5 in. x 7 in. Aluminum Flashing Shingle",
                                 11.98, 0.84))

    def test_fractional_size_still_checked(self):
        self.assertFalse(applies("1/2 in. gas shutoff valve", "3/4 In Earthquake Gas Shutoff Valve", 15, 14))


class GoodMatchesStillApply(unittest.TestCase):
    def test_real_products(self):
        for q, p, llm, cat in [
            ("3/8 in. x 7/8 in. x 20 in. braided stainless steel toilet supply line",
             "3/8 in. Compression x 7/8 in. Ballcock Nut x 20 in. Braided Stainless Steel Toilet Supply Line", 7.56, 7.56),
            ("48 in. W x 22 in. D x 34 in. H single sink bath vanity with top",
             "48 in. W x 22 in. D x 34 in. H Single Sink Freestanding White Bath Vanity with Engineered Marble Top",
             900, 1052.67),
            ("2 in. x 4 in. x 8 ft. stud", "2 in. x 4 in. x 8 ft. Premium Kiln-Dried Whitewood Stud", 4, 3.98),
            ("BEHR PREMIUM 1 gal. satin exterior paint",
             "1 gal. #N480-2 Urethane Alkyd Satin Enamel Interior/Exterior Paint", 38, 38.38),
            ("20 Amp 125-Volt GFCI outlet, white", "20 Amp 125-Volt Duplex SmarTest Self-Test GFCI Outlet, White",
             22, 23.6),
            ("Broan 50 CFM bathroom exhaust fan", "Broan-NuTone 50 CFM Ceiling/Wall Mount Bathroom Exhaust Fan",
             120, 150),
            # A small consumable can run 2-3x a guess; only big-ticket items get the 2x limit.
            ("Henry 10.1 oz. roof sealant", "10.1 oz. Black Exterior Roofing Blacktop & Roof Sealant", 4, 9.98),
        ]:
            self.assertTrue(applies(q, p, llm, cat), q)


class Sizes(unittest.TestCase):
    def test_inch_sizes(self):
        self.assertEqual(v2._inch_sizes("2-1/4 in. x 11/16 in. x 7 ft. casing"), {2.25, 0.6875})
        self.assertEqual(v2._inch_sizes("2x6 pressure treated"), {2.0, 6.0})
        self.assertEqual(v2._inch_sizes('36" x 80" door'), {36.0, 80.0})
        self.assertEqual(v2._inch_sizes("1 gal. satin paint, 8 ft"), set())


class NoCatalogNotes(unittest.TestCase):
    def test_no_description_on_any_material(self):
        for line in ({"catalog_match": {"name": "Real Product"}}, {"catalog_candidates": [{"name": "x"}]},
                     {"catalog_no_match": True}, {"catalog_price_rejected": True}, {}):
            self.assertEqual(v2._build_material_description(line), "")

    def test_notes_item_has_no_catalog_lines_but_the_log_does(self):
        group = {"title": "Doors", "confidence": "medium", "quantity_note": "one door",
                 "material_lines": [{"item": "door", "catalog_no_match": True}]}
        notes = v2.build_internal_notes_text(group)
        self.assertIn("Quantity assumption: one door", notes)
        self.assertNotIn("Home Depot", notes)
        self.assertNotIn("catalog", notes.lower())
        logged = v2.log_internal_review_notes({"cost_groups": [group]})
        self.assertTrue(any("No Home Depot catalog match on 'door'" in line for line in logged))

    def test_matched_product_still_goes_in_the_custom_fields(self):
        fields = v2._build_material_custom_field_values(
            {"catalog_match": {"sku": "123", "link": "https://www.homedepot.com/p/x/1", "brand": "B", "modelNumber": "M"}})
        self.assertEqual(fields, {"SKU": "123", "Product Link": "https://www.homedepot.com/p/x/1", "Brand": "B",
                                  "Model Number": "M"})


if __name__ == "__main__":
    unittest.main()
