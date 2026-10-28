"""Tests for the Preliminary Ballpark Tool pricing engine (ballpark_pricing.py),
checked against the rules and worked examples in the build spec (sections 6/7).

Run from the repo root:  python3 -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ballpark_fixtures as fx  # noqa: E402
from ballpark_fixtures import bp  # noqa: E402


def lines(result, key=None, name_has=None):
    out = [l for p in result["phases"] for l in p["lines"]]
    if key:
        out = [l for l in out if l["key"] == key]
    if name_has:
        out = [l for l in out if name_has.lower() in l["name"].lower()]
    return out


def one(result, key, name_has=None):
    found = lines(result, key, name_has)
    assert len(found) == 1, f"expected one {key} line, got {[l['name'] for l in found]}"
    return found[0]


def run(scope):
    return bp.price_scope(scope, fx.template(), fx.TIER_PRICES)


class RangeAndMarkup(unittest.TestCase):
    def test_spec_worked_examples(self):
        self.assertEqual(bp.price_range(186_400)["text"], "$180K – $210K")
        self.assertEqual(bp.price_range(412_750)["text"], "$400K – $450K")
        self.assertEqual(bp.price_range(958_815)["text"], "$950K – $1.05M")

    def test_brackets_by_cost(self):
        self.assertEqual(bp.markup_bracket(390_624), (0.28, 0.219))
        self.assertEqual(bp.markup_bracket(390_625), (0.25, 0.200))
        self.assertEqual(bp.markup_bracket(609_755), (0.25, 0.200))
        self.assertEqual(bp.markup_bracket(609_756), (0.23, 0.187))

    def test_one_markup_on_every_line(self):
        r = run({"duration_weeks": 15, "punch_list_days": 2, "rooms": [{"type": "kitchen", "tier": "Premium", "sf": 200}]})
        self.assertEqual(r["markup"], 0.28)
        for l in lines(r):
            self.assertAlmostEqual(l["unit_price"], round(l["unit_cost"] * 1.28, 2))
        self.assertAlmostEqual(r["total_cost"], sum(l["cost"] for l in lines(r)), places=1)


class PrepAndManagement(unittest.TestCase):
    def setUp(self):
        self.r = run({"duration_range": "15-20", "punch_list_days": 3,
                      "rooms": [{"type": "kitchen", "tier": "Luxury"}, {"type": "other", "tier": "Quality"}],
                      "estimates": {"demo_days": {"value": 4, "reasoning": "one kitchen"}}})

    def test_duration_uses_range_midpoint(self):
        self.assertEqual(self.r["weeks"], 17.5)
        self.assertEqual(one(self.r, "cm")["cost"], 17.5 * 900)
        self.assertEqual(one(self.r, "dust")["cost"], 17.5 * 100)

    def test_port_a_john_and_dumpster_round_up(self):
        self.assertEqual(one(self.r, "port_a_john")["quantity"], 5)    # 17.5 / 4.33 = 4.04 -> 5
        self.assertEqual(one(self.r, "dumpster")["quantity"], 9)       # 17.5 / 2 = 8.75 -> 9

    def test_selection_uses_highest_tier(self):
        self.assertEqual(one(self.r, "selection")["cost"], 3500)

    def test_demo_from_estimate(self):
        self.assertEqual(one(self.r, "demo")["cost"], 1200)

    def test_removed_lines_absent(self):
        names = {l["name"] for l in lines(self.r)}
        for gone in ("Interior Design", "Preconstruction costs", "Site Work", "Grading", "General Plumbing",
                     "Interior Concrete structures", "Whole Home Audio System", "Exterior Shutters"):
            self.assertNotIn(gone, names)

    def test_no_temp_pole_without_rewire(self):
        self.assertEqual(lines(self.r, "temp_pole"), [])

    def test_default_weeks(self):
        self.assertEqual(bp.duration_weeks({}), 15)
        self.assertEqual(bp.duration_weeks({"whole_home": True}), 32.5)


class PercentLines(unittest.TestCase):
    def test_permits_and_warranty_on_cost_before_them(self):
        r = run({"duration_weeks": 15, "punch_list_days": 2, "rooms": [{"type": "other"}]})
        before = sum(l["cost"] for l in lines(r) if l["key"] not in ("permits", "warranty"))
        self.assertAlmostEqual(one(r, "permits")["cost"], round(before * 0.008, 2), places=2)
        self.assertEqual(one(r, "warranty")["cost"], max(1200, round(before * 0.01, 2)))

    def test_warranty_minimum(self):
        r = run({"duration_weeks": 10})
        self.assertEqual(one(r, "warranty")["cost"], 1200)

    def test_second_storage_unit_over_390k_cost(self):
        small = run({"duration_weeks": 15})
        self.assertEqual(one(small, "storage")["quantity"], 1)
        big = run({"duration_weeks": 40, "floor_plan_sf": 7000, "mechanical": {"full_rewire": True, "hvac_tons": 5}})
        self.assertGreater(big["total_cost"], 390_625)
        self.assertEqual(one(big, "storage")["quantity"], 2)


class Electrical(unittest.TestCase):
    def test_device_rules_per_room(self):
        r = run({"duration_weeks": 15, "rooms": [
            {"type": "kitchen", "sf": 144, "perimeter_lf": 48},   # 4 walls + 48/6 = 12 GFCI
            {"type": "other", "sf": 100, "perimeter_lf": 40},     # 4 + ceil(40/6)=7 -> 11 receptacles
        ]})
        self.assertEqual(one(r, "gfci")["quantity"], 12)
        self.assertEqual(one(r, "receptacle")["quantity"], 11)
        self.assertEqual(one(r, "switch")["quantity"], 4)
        self.assertEqual(one(r, "dimmer")["quantity"], 2)
        self.assertEqual(one(r, "recessed")["quantity"], 12)       # 8 kitchen + 4
        self.assertEqual(one(r, "surface")["quantity"], 2)
        self.assertEqual(one(r, "under_cabinet")["cost"], 875)

    def test_wall_lf_estimated_from_sf(self):
        lf, how = bp.room_wall_lf({"sf": 144})
        self.assertEqual(lf, 48)
        self.assertIn("estimated", how)

    def test_rewire_adds_general_electrical_and_temp_pole(self):
        r = run({"duration_weeks": 15, "floor_plan_sf": 1000, "mechanical": {"full_rewire": True}})
        self.assertEqual(one(r, "general_electrical")["cost"], 9000)
        self.assertEqual(one(r, "temp_pole")["cost"], 1800)


class Finishes(unittest.TestCase):
    def test_paint_per_room_and_tall(self):
        r = run({"duration_weeks": 15, "rooms": [{"type": "other"}] * 4 + [{"type": "other", "tall_ceiling": True}]})
        self.assertEqual(sum(l["cost"] for l in lines(r, "int_paint")), 4 * 900 + 2000)

    def test_tile_lumps_prep_and_material_allowance(self):
        r = run({"duration_weeks": 15, "tile_areas": [
            {"name": "Primary bath", "kind": "primary_bath", "tier": "Premium", "prep": "walk_in_shower", "material_sf": 200},
            {"name": "Hall bath", "kind": "hall_bath_tub", "tier": "Quality", "prep": "tub_surround", "material_sf": 100},
            {"name": "Kitchen", "kind": "backsplash", "tier": "Premium", "material_sf": 30},
            {"name": "Laundry floor", "kind": "small", "tier": "Quality", "material_sf": 50},
        ]})
        self.assertEqual(one(r, "tile_primary")["cost"], 6500)
        self.assertEqual(one(r, "tile_hall")["cost"], 3500)
        self.assertEqual(one(r, "tile_backsplash")["cost"], 1650)
        self.assertEqual(one(r, "tile_ceramic")["cost"], 1200)
        self.assertEqual(one(r, "tile_prep_shower")["cost"], 1600)
        self.assertEqual(one(r, "tile_prep_surround")["cost"], 900)
        self.assertEqual(one(r, "tile_materials", "Premium")["cost"], 230 * 17.5)
        self.assertEqual(one(r, "tile_materials", "Quality")["cost"], 150 * 7.5)
        self.assertEqual(one(r, "tile_materials", "Premium")["phase"], "5300")

    def test_cabinetry_counters_closets_mirrors(self):
        r = run({"duration_weeks": 15, "rooms": [{"type": "kitchen"}],
                 "cabinetry": {"lowers_lf": 20, "uppers_lf": 14, "full_height_lf": 4, "vanities_lf": 6, "builtins_lf": 5},
                 "countertops": [{"name": "Kitchen", "sf": 50, "tier": "Luxury"}],
                 "closets": {"primary": 2, "small": 3}, "mirrors": 2})
        self.assertEqual(one(r, "cabinets_kitchen", "Kitchen Cabinetry")["cost"], 34 * 625)
        self.assertEqual(one(r, "cabinets_kitchen", "full height")["cost"], 4 * 1250)
        self.assertEqual(one(r, "vanities")["cost"], 6 * 625)
        self.assertEqual(one(r, "builtins")["cost"], 5 * 1100)
        self.assertEqual(one(r, "cabinet_install")["quantity"], 49)
        self.assertEqual(one(r, "countertops")["cost"], 50 * 237.5)
        self.assertEqual(one(r, "closets", "primary")["cost"], 9000)
        self.assertEqual(one(r, "closets", "shelf")["cost"], 1050)
        self.assertEqual(one(r, "mirrors")["cost"], 1400)
        self.assertEqual(one(r, "mirrors")["phase"], "5400")

    def test_bath_hardware_by_tier_and_powder_half(self):
        r = run({"duration_weeks": 15, "rooms": [{"type": "primary_bath", "tier": "Luxury"},
                                                 {"type": "powder", "tier": "Premium"}]})
        costs = sorted(l["cost"] for l in lines(r, "bath_hardware"))
        self.assertEqual(costs, [425, 2200])

    def test_flooring(self):
        r = run({"duration_weeks": 15, "flooring": {"hardwood_new_sf": 100, "hardwood_refinish_sf": 200,
                                                    "lvp_sf": 300, "carpet_sf": 50}})
        self.assertEqual(one(r, "hardwood")["cost"], 1025)
        self.assertEqual(sum(l["cost"] for l in lines(r, "hardwood_finishing")), 300 * 3.25)
        self.assertEqual(one(r, "lvp")["cost"] + one(r, "lvp_install")["cost"], 300 * 8.5)
        self.assertEqual(one(r, "carpet")["cost"], 212.5)


class Exterior(unittest.TestCase):
    def test_window_tier_price_and_install(self):
        r = run({"duration_weeks": 15, "exterior": {"windows": 10, "window_tier": "Premium"}})
        self.assertEqual(one(r, "windows")["cost"], 11000)
        self.assertEqual(one(r, "window_install")["cost"], 4000)

    def test_garage_paint_screening_gutters(self):
        r = run({"duration_weeks": 15, "heated_sf": 2500,
                 "exterior": {"garage_doors_single": 1, "garage_doors_double": 1, "full_exterior_repaint": True,
                              "porch_screening_lf": 30, "gutters": True}})
        self.assertEqual(sum(l["cost"] for l in lines(r, "garage_door")), 10700)
        self.assertEqual(one(r, "ext_paint")["cost"], 20000)
        self.assertEqual(one(r, "porch_screening")["cost"], 4200)
        self.assertEqual(one(r, "gutters")["cost"], 2500)

    def test_hvac(self):
        r = run({"duration_weeks": 15, "mechanical": {"hvac_tons": 3, "new_ductwork": True}})
        self.assertEqual(sum(l["cost"] for l in lines(r, "hvac_system")), 15000 + 7500)


class Fixtures(unittest.TestCase):
    def test_itemized_by_tier_no_package_lump(self):
        r = run({"duration_weeks": 15, "fixtures": [
            {"category": "appliance", "item": "refrigerator", "tier": "Premium", "quantity": 1},
            {"category": "appliance", "item": "dishwasher", "tier": "Premium", "quantity": 1},
            {"category": "appliance", "item": "range", "brand": "Wolf", "tier": "Luxury", "quantity": 1,
             "unit_price": 12000, "price_source": "wolfappliance.com"},
        ], "estimates": {"appliance_install_hours": {"value": 6, "reasoning": "3 appliances x 2 hrs"}}})
        premium = one(r, "appliances", "Premium")
        self.assertEqual(premium["cost"], 11000)
        self.assertTrue(premium["allowance"])
        self.assertEqual(one(r, "appliances", "Luxury")["cost"], 12000)
        self.assertFalse(any(l["cost"] == 16000 for l in lines(r)))
        self.assertEqual(one(r, "appliance_install")["cost"], 450)

    def test_unpriced_item_becomes_placeholder(self):
        r = run({"duration_weeks": 15, "fixtures": [{"category": "plumbing", "item": "steam generator", "tier": "Quality"}]})
        ph = one(r, "plumbing_fixtures", "unpriced")
        self.assertEqual(ph["cost"], 0)
        self.assertIn("steam generator", ph["placeholder"])
        self.assertTrue(any("steam generator" in x for x in r["review"]))


class FoundationAndTier(unittest.TestCase):
    def test_second_story_addition_gets_no_footing(self):
        r = run({"duration_weeks": 15, "addition_sf": 1000, "foundation_sf": 0})
        self.assertEqual(lines(r, "footing"), [])
        self.assertTrue(any("second story" in n for p in r["phases"] for n in p["notes"]))

    def test_footing_on_new_foundation_only(self):
        r = run({"duration_weeks": 15, "addition_sf": 1000, "foundation_sf": 500})
        self.assertEqual(one(r, "footing")["cost"], 500 * 42)

    def test_footing_falls_back_to_addition_sf(self):
        r = run({"duration_weeks": 15, "addition_sf": 300})
        self.assertEqual(one(r, "footing")["cost"], 300 * 42)

    def test_no_tier_is_flagged(self):
        r = run({"duration_weeks": 15, "rooms": [{"type": "other"}]})
        self.assertTrue(any("No tier" in x for x in r["review"]))
        r = run({"duration_weeks": 15, "tier": "Premium", "rooms": [{"type": "other"}]})
        self.assertFalse(any("No tier" in x for x in r["review"]))
        self.assertEqual(r["highest_tier"], "Premium")

    def test_untiered_items_take_the_job_tier(self):
        r = run({"duration_weeks": 15, "tier": "Luxury",
                 "tile_areas": [{"kind": "small", "material_sf": 10}],
                 "fixtures": [{"category": "plumbing", "item": "toilet"}]})
        self.assertEqual(one(r, "tile_materials")["cost"], 10 * 50)
        self.assertEqual(one(r, "plumbing_fixtures", "Luxury")["cost"], 2000)


class CostCodesAndPlaceholders(unittest.TestCase):
    def test_every_line_gets_its_phase_code(self):
        r = run({"duration_weeks": 15, "exterior": {"gutters": True, "windows": 2}, "rooms": [{"type": "other"}]})
        for l in lines(r):
            self.assertEqual(l["cost_code"], bp.PHASE_COST_CODES[l["phase"]], l["name"])
        self.assertEqual(one(r, "gutters")["cost_code"], bp.PHASE_COST_CODES["6100"])

    def test_missing_estimates_are_flagged_not_dropped(self):
        r = run({"duration_weeks": 15, "floor_plan_sf": 500})
        demo = one(r, "demo")
        self.assertEqual(demo["cost"], 0)
        self.assertTrue(demo["placeholder"])
        self.assertTrue(one(r, "framing_labor")["placeholder"])
        self.assertTrue(one(r, "int_trim_labor")["placeholder"])

    def test_phases_in_template_order(self):
        r = run({"duration_weeks": 15, "floor_plan_sf": 500, "exterior": {"gutters": True}})
        numbers = [p["number"] for p in r["phases"]]
        order = [bp._phase_number(n) for _, n in fx.PHASES]
        self.assertEqual(numbers, sorted(numbers, key=order.index))
        self.assertEqual(numbers[0], "1000")
        self.assertEqual(numbers[-1], "6200")


if __name__ == "__main__":
    unittest.main()
