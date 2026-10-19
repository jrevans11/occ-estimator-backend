"""Tests for the Site Scope Tool drawings (scope_drawings.py).

Run from the repo root:  python3 -m unittest discover tests

Needs Pillow (it's in requirements.txt, so Render has it). Without Pillow
the drawing tests skip; the geometry/format tests still run.
"""

import copy
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

if HAVE_PIL:
    import scope_drawings as sd  # noqa: E402
    from PIL import Image  # noqa: E402

SAMPLE = os.path.join(ROOT, "docs", "site-scope", "samples", "test1-office-2026-10-19.json")


def sample():
    with open(SAMPLE) as f:
        return json.load(f)


def m(inches, src="dictated"):
    return {"in": inches, "src": src}


def box_room(w1=144, w2=156, w3=144, w4=156):
    return {"id": "box", "name": "Box", "ceiling": m(96),
            "walls": [{"number": i + 1, "length": m(L)} for i, L in enumerate((w1, w2, w3, w4))]}


def l_room():
    """L-shaped: the inside corner after Wall 3 turns left."""
    lengths = (180, 96, 60, 48, 120, 144)
    room = {"id": "ell", "name": "Ell", "ceiling": m(96),
            "walls": [{"number": i + 1, "length": m(L)} for i, L in enumerate(lengths)]}
    room["walls"][2]["turn"] = -90
    return room


class SampleFileTests(unittest.TestCase):
    def test_sample_is_valid_json_with_schema(self):
        data = sample()
        self.assertEqual(data["schema"], "occ-scope-rooms/1")
        self.assertEqual(data["project"]["companycam_project_id"], "117058039")

    def test_sample_matches_oct_19_answers(self):
        walls = {w["number"]: w for w in sample()["rooms"][0]["walls"]}
        # Wall 4 = 13' (confirmed in the question round).
        self.assertEqual(walls[4]["length"], {"in": 156, "src": "confirmed"})
        # New opening gets a door; same-size window replaced; surface-mount outlet on Wall 3.
        self.assertIn("new", [o["status"] for o in walls[1]["openings"] if o["type"] == "door"])
        self.assertEqual(walls[4]["openings"][0]["status"], "replace")
        self.assertEqual([i.get("mount") for i in walls[3]["items"] if i["type"] == "outlet"], ["surface"])


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class MeasureTests(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(sd.measure(m(36, "dictated")), (36.0, "dictated"))
        self.assertEqual(sd.measure(m(36, "confirmed")), (36.0, "dictated"))
        self.assertEqual(sd.measure(m(36, "est")), (36.0, "est"))
        self.assertEqual(sd.measure(m(36, "estimated")), (36.0, "est"))
        self.assertEqual(sd.measure(m(36, "default")), (36.0, "assumed"))
        self.assertIsNone(sd.measure(None))

    def test_bare_number_is_assumed(self):
        self.assertEqual(sd.measure(36), (36.0, "assumed"))

    def test_bad_values(self):
        for bad in ("36", True, {"in": "36"}, [36]):
            with self.assertRaises(sd.DrawingError):
                sd.measure(bad)

    def test_feet_and_inches(self):
        self.assertEqual(sd.ft_in(158), "13'-2\"")
        self.assertEqual(sd.ft_in(11.6), "1'-0\"")
        self.assertEqual(sd.ft_in(4), "0'-4\"")
        self.assertEqual(sd.label(80, "assumed"), "6'-8\" (assumed)")
        self.assertEqual(sd.label(36, "est"), "3'-0\" (est.)")
        self.assertEqual(sd.label(96, "dictated"), "8'-0\"")


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class ParseTests(unittest.TestCase):
    def test_needs_three_walls_with_lengths(self):
        with self.assertRaises(sd.DrawingError):
            sd.parse_room({"name": "X", "walls": [{"length": m(10)}, {"length": m(10)}]})
        room = box_room()
        del room["walls"][2]["length"]
        with self.assertRaisesRegex(sd.DrawingError, "Wall 3: needs a length"):
            sd.parse_room(room)

    def test_opening_needs_offset_and_width(self):
        room = box_room()
        room["walls"][0]["openings"] = [{"type": "door", "offset": m(10)}]
        with self.assertRaisesRegex(sd.DrawingError, "offset .* and a width"):
            sd.parse_room(room)

    def test_window_needs_two_of_sill_head_height(self):
        room = box_room()
        room["walls"][0]["openings"] = [{"type": "window", "offset": m(10), "width": m(30), "sill": m(36)}]
        with self.assertRaises(sd.DrawingError):
            sd.parse_room(room)
        room["walls"][0]["openings"][0]["height"] = m(48, "est")
        o = sd.parse_room(room)["walls"][0]["openings"][0]
        self.assertEqual(o["head"], (84.0, "est"))   # head is only as good as its weakest part

    def test_door_head_defaults_to_6_8_assumed(self):
        room = box_room()
        room["walls"][0]["openings"] = [{"type": "door", "offset": m(10), "width": m(30)}]
        self.assertEqual(sd.parse_room(room)["walls"][0]["openings"][0]["head"], (80.0, "assumed"))

    def test_wall_thickness_from_construction(self):
        room = box_room()
        room["walls"][0].update(construction="cmu", construction_src="est")
        room["walls"][1].update(construction="brick", construction_src="dictated")
        room["walls"][2].update(thickness=m(5.5, "dictated"))
        walls = sd.parse_room(room)["walls"]
        self.assertEqual(walls[0]["thickness"], (8.0, "assumed"))     # CMU always assumed (rules, Oct 19)
        self.assertEqual(walls[1]["thickness"], (10.0, "dictated"))
        self.assertEqual(walls[2]["thickness"], (5.5, "dictated"))
        self.assertEqual(walls[3]["thickness"], (4.5, "assumed"))     # nothing said: interior default

    def test_unknown_status_is_existing(self):
        room = box_room()
        room["walls"][0]["openings"] = [{"type": "door", "status": "maybe", "offset": m(1), "width": m(30)}]
        self.assertEqual(sd.parse_room(room)["walls"][0]["openings"][0]["status"], "existing")

    def test_schema_mismatch(self):
        with self.assertRaisesRegex(sd.DrawingError, "schema"):
            sd.render_drawings({"schema": "something-else/9", "rooms": [box_room()]})
        with self.assertRaisesRegex(sd.DrawingError, "no rooms"):
            sd.render_drawings({"rooms": []})


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class ClosureTests(unittest.TestCase):
    def test_sample_office_closes(self):
        self.assertTrue(sd.check_closure(sample()["rooms"][0])["closes"])

    def test_rectangle_that_does_not_close(self):
        result = sd.check_closure(box_room(w3=140))
        self.assertFalse(result["closes"])
        self.assertAlmostEqual(result["gap_in"], 4.0)
        self.assertIn("0'-4\"", result["message"])

    def test_within_one_inch_closes(self):
        self.assertTrue(sd.check_closure(box_room(w3=144.75))["closes"])
        self.assertFalse(sd.check_closure(box_room(w3=142.5))["closes"])

    def test_l_shape_with_inside_corner_closes(self):
        self.assertTrue(sd.check_closure(l_room())["closes"])

    def test_wrong_turns_do_not_close(self):
        room = box_room(144, 144, 144, 144)
        room["walls"][3]["turn"] = 45
        result = sd.check_closure(room)
        self.assertFalse(result["closes"])
        self.assertIn("turns", result["message"])

    def test_wall_corners_meet_outside(self):
        """The outer face of each wall meets the next at the mitred corner."""
        g = sd.plan_geometry(sd.parse_room(box_room()))
        s1, s2 = g["segs"][0], g["segs"][1]
        self.assertEqual(s1["o1"], s2["o0"])
        self.assertAlmostEqual(s1["o1"][0], 144 + 4.5)
        self.assertAlmostEqual(s1["o1"][1], -4.5)


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class DimensionTests(unittest.TestCase):
    def test_dictated_width_stays_dictated_when_offset_is_estimated(self):
        wall = {w["number"]: w for w in sd.parse_room(sample()["rooms"][0])["walls"]}[4]
        segs = sd.dim_segments(156, "dictated", wall["openings"])
        self.assertEqual(segs, [(0.0, 40.0, "est"), (40.0, 136.0, "dictated"), (136.0, 156.0, "est")])

    def test_gap_between_openings_takes_the_weaker_source(self):
        wall = sd.parse_room(sample()["rooms"][0])["walls"][0]
        segs = sd.dim_segments(144, "dictated", wall["openings"])
        labels = [sd.label(t1 - t0, src) for t0, t1, src in segs]
        self.assertEqual(labels, ["2'-0\" (assumed)", "2'-8\"", "4'-2\" (assumed)", "2'-8\" (est.)", "0'-6\" (est.)"])

    def test_device_marks_a_point(self):
        wall = sd.parse_room(sample()["rooms"][0])["walls"][2]
        new = [i for i in wall["items"] if i["status"] == "new" and i["type"] == "outlet"]
        self.assertEqual(sd.dim_segments(144, "dictated", new), [(0.0, 84.0, "assumed"), (84.0, 144.0, "assumed")])


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class FlagTests(unittest.TestCase):
    def test_opening_past_wall_end(self):
        room = box_room()
        room["walls"][0]["openings"] = [{"type": "door", "offset": m(130), "width": m(32)}]
        flags = sd.wall_flags(sd.parse_room(room))
        self.assertTrue(any("runs past the wall" in f for f in flags))

    def test_overlapping_openings(self):
        room = box_room()
        room["walls"][0]["openings"] = [
            {"type": "door", "offset": m(10), "width": m(32)},
            {"type": "window", "offset": m(30), "width": m(30), "sill": m(36), "head": m(80)},
        ]
        self.assertTrue(any("overlap" in f for f in sd.wall_flags(sd.parse_room(room))))

    def test_removed_and_new_in_same_spot_is_fine(self):
        room = box_room()
        room["walls"][0]["openings"] = [
            {"type": "french_door", "status": "remove", "offset": m(40), "width": m(60)},
            {"type": "slider", "status": "new", "offset": m(40), "width": m(72)},
        ]
        self.assertEqual(sd.wall_flags(sd.parse_room(room)), [])

    def test_transom_above_door_is_not_an_overlap(self):
        room = box_room()
        room["walls"][0]["openings"] = [
            {"type": "door", "offset": m(10), "width": m(32)},
            {"type": "window", "offset": m(10), "width": m(32), "sill": m(84), "head": m(94)},
        ]
        self.assertEqual(sd.wall_flags(sd.parse_room(room)), [])


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = sd.render_drawings(sample())

    def png(self, filename):
        d = next(d for d in self.result["drawings"] if d["filename"] == filename)
        return Image.open(io.BytesIO(d["png"]))

    def test_sample_draws_plan_and_four_elevations(self):
        names = [d["filename"] for d in self.result["drawings"]]
        self.assertEqual(names, ["office-floor-plan.png"] + [f"office-wall-{n}-elevation.png" for n in (1, 2, 3, 4)])
        titles = [d["title"] for d in self.result["drawings"]]
        self.assertEqual(titles[0], "Office - Floor Plan")
        self.assertEqual(titles[2], "Office - Wall 2 Elevation")   # rules section 10 title format

    def test_pngs_are_real_and_phone_sized(self):
        for d in self.result["drawings"]:
            self.assertTrue(d["png"].startswith(b"\x89PNG"))
            self.assertLess(len(d["png"]), 600_000)
            im = Image.open(io.BytesIO(d["png"]))
            self.assertEqual(im.mode, "RGB")
            self.assertTrue(900 <= im.width <= 2200, im.size)
            self.assertTrue(700 <= im.height <= 2400, im.size)

    def test_file_level_questions_come_back_as_flags(self):
        self.assertEqual(len(self.result["flags"]), 3)
        self.assertTrue(self.result["flags"][0].startswith("Wall 4 window"))

    def test_drawing_uses_the_status_colors(self):
        colors = {c for _, c in self.png("office-wall-4-elevation.png").getcolors(1 << 20)}
        self.assertIn(sd.BLUE, colors)   # new window
        self.assertIn(sd.RED, colors)    # existing window coming out
        plan = {c for _, c in self.png("office-floor-plan.png").getcolors(1 << 20)}
        self.assertIn(sd.GRAY, plan)

    def test_plain_wall_gets_no_elevation(self):
        result = sd.render_drawings({"rooms": [box_room()]})
        self.assertEqual([d["kind"] for d in result["drawings"]], ["plan"])

    def test_elevation_can_be_forced(self):
        room = box_room()
        room["walls"][1]["elevation"] = True
        result = sd.render_drawings({"rooms": [room]})
        self.assertEqual([d["wall"] for d in result["drawings"]], [None, 2])

    def test_non_closing_room_still_draws_and_says_so(self):
        data = sample()
        data["rooms"][0]["walls"][2]["length"]["in"] = 140
        result = sd.render_drawings(data)
        self.assertEqual(len(result["drawings"]), 5)
        self.assertTrue(any("don't close" in f for f in result["flags"]))

    def test_everything_the_format_supports_draws(self):
        """Cabinets, finishes, every opening type, a removed wall, an L-shape."""
        room = l_room()
        room["finishes"] = [{"type": "baseboard", "height": m(5.25, "est"), "profile": "colonial", "status": "new"},
                            {"type": "crown", "height": m(4.5, "assumed")}]
        w = room["walls"]
        w[0].update(construction="exterior", construction_src="dictated")
        w[0]["openings"] = [{"type": "window", "offset": m(60), "width": m(36), "sill": m(42), "head": m(82),
                             "casing": {"width": m(3.5, "est"), "profile": "ranch"}, "sashes": 2}]
        w[0]["items"] = [
            {"type": "base_cabinet", "status": "new", "offset": m(0), "width": m(150), "doors": 6},
            {"type": "upper_cabinet", "status": "new", "offset": m(0), "width": m(54), "doors": 2},
            {"type": "appliance", "offset": m(150), "width": m(30), "bottom": m(0), "height": m(36), "label": "range"},
            {"type": "switch", "status": "new", "offset": m(170), "height": m(48)},
            {"type": "light", "status": "new", "offset": m(90), "height": m(90)},
        ]
        w[0]["finishes"] = [{"type": "backsplash", "status": "new", "offset": m(0), "width": m(150),
                             "bottom": m(36), "top": m(54), "tile_size": m(3)}]
        w[1]["status"] = "remove"
        w[3]["finishes"] = [{"type": "wainscot", "status": "new", "top": m(36), "spacing": m(12)},
                            {"type": "chair_rail", "status": "new", "height": m(36)},
                            {"type": "tile", "top": "ceiling"}]
        w[4]["openings"] = [{"type": "door", "status": "new", "offset": m(30), "width": m(30), "swing": "out"},
                            {"type": "pocket_door", "offset": m(75), "width": m(28, "est")}]
        w[5]["openings"] = [{"type": "french_door", "status": "remove", "offset": m(10), "width": m(60)},
                            {"type": "bifold", "status": "new", "offset": m(80), "width": m(48)},
                            {"type": "slider", "offset": m(10), "width": m(60)}]
        result = sd.render_drawings({"project": {"name": "Fake", "visit_date": "2026-10-19"}, "rooms": [room]})
        self.assertEqual([d["wall"] for d in result["drawings"]], [None, 1, 4, 5, 6])
        self.assertEqual(result["flags"], [])

    def test_does_not_change_the_input(self):
        data = sample()
        before = copy.deepcopy(data)
        sd.render_drawings(data)
        self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
