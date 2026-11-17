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


if __name__ == "__main__":
    unittest.main()
