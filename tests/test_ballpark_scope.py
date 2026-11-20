"""Preliminary Ballpark scope prompt: the rules added after the Nov 17, 2026
dry runs (blank form fields, estimated quantities, demo man-days, weeks)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ballpark_scope as bs  # noqa: E402


class Prompt(unittest.TestCase):
    def setUp(self):
        self.p = bs.system_prompt()

    def test_blank_is_not_none(self):
        self.assertIn('is NOT "none"', self.p)
        self.assertIn("powder room, a detached garage", self.p)

    def test_estimate_instead_of_zero(self):
        self.assertIn("ESTIMATE the quantity; never leave it at 0", self.p)

    def test_demo_man_days_and_weeks(self):
        self.assertIn("MAN-days", self.p)
        self.assertIn("duration_range is in WEEKS", self.p)

    def test_paint_units_and_builtins_in_schema(self):
        self.assertIn('"paint_units"', self.p)
        self.assertIn("cabinetry.builtins_lf", self.p)


class TylerNov19Rules(unittest.TestCase):
    def setUp(self):
        self.p = bs.system_prompt()

    def test_new_schema_fields(self):
        for k in ('"paint_amount"', '"original_wood_windows"', '"specialty_windows"', '"door_types"',
                  '"builtins_low_lf"'):
            self.assertIn(k, self.p)

    def test_paint_windows_survey_rules(self):
        self.assertIn("exterior.paint_amount", self.p)
        self.assertIn("counts as 2", self.p)
        self.assertIn("ONLY when someone says a lot survey is needed", self.p)

    def test_framed_sf_is_floor_area(self):
        # Nov 19 rerun: reading framed_sf as wall surface gave Greenway 2,600 SF (Tyler 1,000).
        self.assertIn("framed_sf: FLOOR AREA", self.p)
        self.assertIn("NOT wall surface area", self.p)

    def test_door_types_merge_from_form(self):
        merged = bs.merge_form({"exterior": {"exterior_doors": 3, "door_types": {"single": 3}}},
                               {"exterior": {"door_types": {"french": 1}, "window_tier": "Luxury"}})
        self.assertEqual(merged["exterior"]["door_types"]["french"], 1)
        self.assertEqual(merged["exterior"]["door_types"]["single"], 3)
        self.assertEqual(merged["exterior"]["window_tier"], "Luxury")


class UserContent(unittest.TestCase):
    def test_duration_labelled_weeks(self):
        text = bs.build_user_content({"job": {"number": "00999"}}, {"duration_range": "20-25", "tier": "Premium"}, {})
        self.assertIn('"duration_range": "20-25 weeks"', text)

    def test_form_not_mutated_and_merge_still_drops_duration(self):
        form = {"duration_range": "20-25"}
        bs.build_user_content({}, form, {})
        self.assertEqual(form["duration_range"], "20-25")
        merged = bs.merge_form({"duration_range": "5-10", "rooms": []}, form)
        self.assertEqual(merged["duration_range"], "20-25")


class MergeKeepsExtraSpaces(unittest.TestCase):
    def test_powder_room_from_video_survives_form_spaces(self):
        scope = {"rooms": [{"name": "Kitchen", "type": "kitchen"}, {"name": "Powder", "type": "powder"},
                           {"name": "Hall bath", "type": "hall_bath"}]}
        form = {"tier": "Premium", "spaces": [{"type": "kitchen"}, {"type": "hall_bath"}]}
        merged = bs.merge_form(scope, form)
        self.assertIn("powder", [r["type"] for r in merged["rooms"]])


class HallBathWalkInShower(unittest.TestCase):
    """Barber, Nov 20 2026: secondary baths with tiled walk-in showers were read as "small"."""

    def test_walk_in_shower_bath_is_hall_bath(self):
        p = bs.system_prompt()
        self.assertIn("tiled walk-in shower; despite the name, a walk-in shower counts", p)
        self.assertIn("never small", p)
        self.assertNotIn("shower only", p)

if __name__ == "__main__":
    unittest.main()
