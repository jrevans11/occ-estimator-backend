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

    def test_flat_markup_every_size(self):   # Tyler + Jason, Nov 19 2026
        for cost in (100_000, 390_625, 609_756, 2_000_000):
            self.assertEqual(bp.markup_bracket(cost), (0.28, 0.219))

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
        self.assertEqual(one(self.r, "dust")["quantity"], 11)        # 60% of 17.5 weeks, rounded up

    def test_port_a_john_and_dumpster_round_up(self):
        self.assertEqual(one(self.r, "port_a_john")["quantity"], 5)    # 17.5 / 4.33 = 4.04 -> 5
        self.assertEqual(one(self.r, "dumpster")["quantity"], 7)       # 17.5 / 2.5 = 7

    def test_selection_uses_highest_tier_on_small_jobs(self):
        self.assertLess(self.r["total_cost"], 160_000)
        self.assertEqual(one(self.r, "selection")["cost"], 3500)

    def test_selection_by_job_size(self):
        def hours(cost, tier="Quality"):
            return next(q for k, q, _, _ in bp._sized_lines({}, tier, cost, False) if k == "selection")
        self.assertEqual(hours(120_000, "Premium"), 20)
        self.assertEqual(hours(200_000), 32)
        self.assertEqual(hours(350_000), 40)
        self.assertEqual(hours(700_000), 60)
        self.assertEqual(hours(700_000, "Luxury"), 80)

    def test_demo_from_estimate(self):
        self.assertEqual(one(self.r, "demo")["cost"], 1200)

    def test_removed_lines_absent(self):
        names = {l["name"] for l in lines(self.r)}
        for gone in ("Interior Design", "Site Work", "Grading",
                     "Interior Concrete structures", "Whole Home Audio System", "Exterior Shutters"):
            self.assertNotIn(gone, names)

    def test_no_temp_pole_without_rewire(self):
        self.assertEqual(lines(self.r, "temp_pole"), [])

    def test_default_weeks(self):
        self.assertEqual(bp.duration_weeks({}), 15)
        self.assertEqual(bp.duration_weeks({"whole_home": True}), 32.5)


class PercentLines(unittest.TestCase):
    def test_permits_by_size_and_warranty_on_cost_before_it(self):
        r = run({"duration_weeks": 15, "punch_list_days": 2, "rooms": [{"type": "other"}]})
        self.assertEqual(one(r, "permits")["cost"], 800)            # small interior-only job
        before = sum(l["cost"] for l in lines(r) if l["key"] != "warranty")
        self.assertEqual(one(r, "warranty")["cost"], max(1200, round(before * 0.01, 2)))
        def permits(cost, scope):
            return next(q for k, q, _, _ in bp._sized_lines(scope, "Quality", cost, False) if k == "permits")
        addition = {"addition_sf": 400}
        self.assertEqual(permits(90_000, addition), 2)
        self.assertEqual(permits(150_000, addition), 4)
        self.assertEqual(permits(300_000, addition), 5)
        self.assertEqual(permits(550_000, addition), 7)
        self.assertEqual(permits(800_000, addition), 9)
        self.assertEqual(permits(120_000, {}), 1)                   # interior only, under $150K

    def test_size_driven_completion_lines(self):
        small = run({"duration_weeks": 10})
        self.assertEqual(lines(small, "storage"), [])               # none under $160K
        self.assertEqual(one(small, "punch")["quantity"], 1)
        self.assertEqual(one(small, "cleaning")["quantity"], 1)

    def test_warranty_minimum(self):
        r = run({"duration_weeks": 10})
        self.assertEqual(one(r, "warranty")["cost"], 1200)

    def test_second_storage_unit_over_390k_cost(self):
        storage = lambda cost: [q for k, q, _, _ in bp._sized_lines({}, "Quality", cost, False) if k == "storage"]
        self.assertEqual(storage(150_000), [])
        self.assertEqual(storage(250_000), [1])
        self.assertEqual(storage(400_000), [2])
        big = run({"duration_weeks": 45, "floor_plan_sf": 9000, "addition_sf": 2000, "whole_home": True,
                   "mechanical": {"full_rewire": True, "hvac_tons": 10, "new_ductwork": True},
                   "rooms": [{"type": "other"}] * 30, "exterior": {"windows": 30, "roof_squares": 40}})
        self.assertGreater(big["total_cost"], 390_625)
        self.assertEqual(one(big, "storage")["quantity"], 2)
        self.assertEqual(one(big, "cleaning")["quantity"], 2)


class Electrical(unittest.TestCase):
    def test_device_token_counts_and_recessed_per_room(self):
        r = run({"duration_weeks": 15, "floor_plan_sf": 800,
                 "rooms": [{"type": "kitchen"}, {"type": "other"}, {"type": "other"}]})
        self.assertEqual(one(r, "general_electrical")["cost"], 800 * 9)   # every job, not only rewires
        self.assertEqual(one(r, "receptacle")["quantity"], 1)
        self.assertEqual(one(r, "switch")["quantity"], 1)
        self.assertEqual(one(r, "dimmer")["quantity"], 1)
        self.assertEqual(one(r, "gfci")["quantity"], 1)
        self.assertEqual(one(r, "recessed")["quantity"], 5)       # 3 rooms x 1.5, rounded up
        self.assertEqual(one(r, "under_cabinet")["cost"], 875)
        self.assertEqual(one(r, "finish_electrical")["quantity"], 5)   # 3 x 1.5 = 4.5 -> 5

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
        self.assertEqual(sum(l["cost"] for l in lines(r, "int_paint")), 4 * 1100 + 2000)
        self.assertEqual(one(r, "paint_materials")["cost"], 450)
        self.assertEqual(one(r, "int_trim_labor")["quantity"], 5)            # 1 day per room
        self.assertEqual(one(r, "int_trim_materials")["quantity"], 250)      # 50 SF per room
        self.assertEqual(one(r, "drywall")["quantity"], 1500)                # 300 SF per room
        self.assertEqual(one(r, "drywall_materials")["quantity"], 2025)      # 1.35 x hang
        big = run({"duration_weeks": 30, "rooms": [{"type": "other"}] * 24})
        self.assertEqual(one(big, "paint_materials")["quantity"], 4)

    def test_tile_lumps_prep_and_ceramic_units(self):
        r = run({"duration_weeks": 15, "tile_areas": [
            {"name": "Primary bath", "kind": "primary_bath", "tier": "Premium", "prep": "walk_in_shower"},
            {"name": "Hall bath", "kind": "hall_bath_tub", "tier": "Quality", "prep": "tub_surround"},
            {"name": "Kitchen", "kind": "backsplash", "tier": "Premium"},
            {"name": "Laundry floor", "kind": "small", "tier": "Quality"},
        ]})
        # Jason, Nov 20 2026: lumps are labor, not allowances.
        self.assertEqual(one(r, "tile_primary")["cost"], 5500)
        self.assertFalse(one(r, "tile_primary")["allowance"])
        self.assertEqual(one(r, "tile_hall")["cost"], 2200)
        self.assertEqual(one(r, "tile_backsplash")["cost"], 1800)
        # Ceramic units only for small spaces.
        self.assertEqual(one(r, "tile_ceramic")["quantity"], 1)
        self.assertEqual(one(r, "tile_ceramic")["cost"], 1200)
        self.assertIn("Schluter", one(r, "tile_ceramic")["description"])
        self.assertEqual(one(r, "tile_prep_shower")["cost"], 1600)
        self.assertEqual(one(r, "tile_prep_surround")["cost"], 900)
        # One material allowance per area: typical SF x tier rate.
        mats = {l["name"].split(" - ")[1].split(" (")[0]: l for l in lines(r, "tile_materials")}
        self.assertEqual(len(mats), 4)
        self.assertEqual(mats["Primary bath"]["cost"], 180 * 17.50)
        self.assertEqual(mats["Hall bath"]["cost"], 110 * 7.50)
        self.assertEqual(mats["Kitchen"]["cost"], 30 * 17.50)
        self.assertEqual(mats["Laundry floor"]["cost"], 50 * 7.50)
        self.assertTrue(all(l["allowance"] for l in mats.values()))

    def test_hall_bath_with_walk_in_shower(self):
        r = run({"duration_weeks": 15, "tile_areas": [
            {"name": "Guest bath", "kind": "hall_bath_tub", "tier": "Premium", "prep": "walk_in_shower"}]})
        self.assertEqual(one(r, "tile_hall")["cost"], 2200)
        self.assertEqual(one(r, "tile_prep_shower")["cost"], 1600)
        self.assertEqual(lines(r, "tile_prep_surround"), [])
        self.assertEqual(lines(r, "tile_ceramic"), [])
        self.assertEqual(one(r, "tile_materials")["quantity"], 110)

    def test_tile_material_uses_entered_sf(self):
        r = run({"duration_weeks": 15, "tile_areas": [
            {"name": "Primary bath", "kind": "primary_bath", "tier": "Luxury", "sf": 240}]})
        self.assertEqual(one(r, "tile_materials")["quantity"], 240)
        self.assertEqual(one(r, "tile_materials")["cost"], 240 * 50)
        self.assertEqual(lines(r, "tile_ceramic"), [])

    def test_cabinetry_counters_closets_mirrors(self):
        r = run({"duration_weeks": 15, "rooms": [{"type": "kitchen"}],
                 "cabinetry": {"lowers_lf": 20, "uppers_lf": 14, "full_height_lf": 4, "vanities_lf": 6, "builtins_lf": 5},
                 "countertops": [{"name": "Kitchen", "sf": 50, "tier": "Luxury"}],
                 "closets": {"primary": 2, "small": 3}, "mirrors": 2})
        self.assertEqual(one(r, "cabinets_kitchen", "Kitchen Cabinetry")["cost"], 34 * 625)
        self.assertEqual(one(r, "cabinets_kitchen", "full height")["cost"], 4 * 1250)
        self.assertEqual(one(r, "vanities")["cost"], 6 * 625)
        self.assertEqual(one(r, "builtins")["cost"], 5 * 1100)
        low = run({"duration_weeks": 15, "cabinetry": {"builtins_low_lf": 8}})
        self.assertEqual(one(low, "builtins")["cost"], 8 * 625)
        self.assertEqual(lines(r, "cabinet_install"), [])
        self.assertEqual(lines(r, "cabinet_paint"), [])
        self.assertEqual(one(r, "countertops")["cost"], 50 * 237.5)
        self.assertEqual(one(r, "cabinet_hw")["cost"], 2500)          # kitchen, Quality
        self.assertEqual(one(r, "closets", "primary")["cost"], 9000)
        self.assertEqual(one(r, "closets", "shelf")["cost"], 1050)
        self.assertEqual(one(r, "mirrors")["cost"], 1400)
        self.assertEqual(one(r, "mirrors")["phase"], "5400")

    def test_bath_hardware_one_line_per_job(self):
        one_bath = run({"duration_weeks": 15, "rooms": [{"type": "primary_bath"}]})
        self.assertEqual(one(one_bath, "bath_hardware")["cost"], 950)
        two = run({"duration_weeks": 15, "rooms": [{"type": "primary_bath"}, {"type": "hall_bath"}]})
        self.assertEqual(one(two, "bath_hardware")["cost"], 1600)

    def test_general_plumbing_and_finish_hours_per_fixture(self):
        r = run({"duration_weeks": 15, "rooms": [{"type": "primary_bath"}, {"type": "kitchen"}]})
        self.assertEqual(one(r, "general_plumbing")["quantity"], 6)     # 4 + 2 by room
        self.assertEqual(one(r, "general_plumbing")["cost"], 7200)
        self.assertEqual(one(r, "finish_plumbing")["quantity"], 18)     # 3 hrs per fixture
        given = run({"duration_weeks": 15, "plumbing": {"fixture_count": 9}, "rooms": [{"type": "kitchen"}]})
        self.assertEqual(one(given, "general_plumbing")["quantity"], 9)

    def test_plumbing_location_lines_are_tokens_beside_general_plumbing(self):
        plumb = {"fixture_count": 12, "swap_same": 8, "new_in_footprint": 9, "tub_in_footprint": 2,
                 "new_outside_footprint": 3, "vent_thru_roof": 2, "cut_and_cap": 4}
        r = run({"duration_weeks": 15, "plumbing": plumb})
        self.assertEqual(one(r, "general_plumbing")["quantity"], 12)
        for key in ("plumb_swap", "plumb_new_in", "plumb_tub_in"):
            self.assertEqual(one(r, key)["quantity"], 1)
        self.assertEqual(one(r, "plumb_new_out")["quantity"], 3)
        self.assertEqual(one(r, "plumb_vent")["quantity"], 2)
        self.assertEqual(one(r, "plumb_cut_cap")["quantity"], 4)
        # No General Plumbing on the job: the read's counts stand.
        alone = run({"duration_weeks": 15, "plumbing": {"swap_same": 5}})
        self.assertEqual(lines(alone, "general_plumbing"), [])
        self.assertEqual(one(alone, "plumb_swap")["quantity"], 5)

    def test_flooring(self):
        r = run({"duration_weeks": 15, "flooring": {"hardwood_new_sf": 100, "hardwood_refinish_sf": 200,
                                                    "lvp_sf": 300, "carpet_sf": 50}})
        self.assertEqual(one(r, "hardwood")["cost"], 1025)
        self.assertEqual(sum(l["cost"] for l in lines(r, "hardwood_finishing")), 300 * 3.75)
        self.assertEqual(one(r, "lvp")["cost"] + one(r, "lvp_install")["cost"], 300 * 8.5)
        self.assertEqual(one(r, "carpet")["cost"], 212.5)


class SiteLines(unittest.TestCase):
    def test_default_one_each_on_ground_work(self):
        r = run({"duration_weeks": 15, "deck_sf": 300})
        self.assertEqual(one(r, "site_work")["cost"], 800)
        self.assertEqual(one(r, "grading")["cost"], 600)

    def test_counts_from_scope_read(self):
        r = run({"duration_weeks": 22.5, "addition_sf": 750,
                 "site": {"site_work_units": 2, "grading_units": 5}})      # Greenway
        self.assertEqual(one(r, "site_work")["cost"], 1600)
        self.assertEqual(one(r, "grading")["cost"], 3000)

    def test_zero_means_none_and_interior_jobs_get_none(self):
        r = run({"duration_weeks": 15, "deck_sf": 300, "site": {"site_work_units": 0, "grading_units": 0}})
        self.assertEqual(lines(r, "site_work") + lines(r, "grading"), [])
        inside = run({"duration_weeks": 15, "rooms": [{"type": "kitchen", "tier": "Quality"}]})
        self.assertEqual(lines(inside, "site_work") + lines(inside, "grading"), [])

    def test_engineering_precon_survey(self):
        r = run({"duration_weeks": 15, "site": {"engineering": "letter", "preconstruction": True, "survey": True}})
        self.assertEqual(one(r, "engineering")["cost"], 1850)
        self.assertEqual(one(r, "survey")["cost"], 1850)
        precon = one(r, "preconstruction")
        self.assertFalse(precon["placeholder"])
        self.assertAlmostEqual(precon["price"], 0.02 * r["total_price"], delta=2)   # under $250K: 2%
        full = run({"duration_weeks": 15, "site": {"engineering": "full"}})
        self.assertEqual(one(full, "engineering")["cost"], 2800)
        self.assertEqual(lines(run({"duration_weeks": 15, "site": {"engineering": "none"}}), "engineering"), [])

    def test_precon_on_every_job(self):
        # Jason, Nov 19 2026: preconstruction goes on every job, plans or not.
        r = run({"duration_weeks": 15, "rooms": [{"type": "kitchen", "tier": "Quality"}]})
        self.assertAlmostEqual(one(r, "preconstruction")["price"], 0.02 * r["total_price"], delta=2)


class Exterior(unittest.TestCase):
    def test_window_tier_price_and_install(self):
        r = run({"duration_weeks": 15, "exterior": {"windows": 10, "window_tier": "Premium"}})
        self.assertEqual(one(r, "windows")["cost"], 12000)
        self.assertEqual(one(r, "window_install")["cost"], 3000)
        lux = run({"duration_weeks": 15, "exterior": {"windows": 2, "window_tier": "Quality"}})
        self.assertEqual(one(lux, "windows")["cost"], 1200)

    def test_original_wood_windows_luxury_and_paint_bump(self):
        r = run({"duration_weeks": 15, "rooms": [{"type": "other", "tier": "Quality"}],
                 "exterior": {"windows": 4, "window_tier": "Quality", "original_wood_windows": True}})
        self.assertEqual(one(r, "windows")["cost"], 4 * 2500)
        self.assertEqual(one(r, "int_paint")["cost"], 1210)

    def test_specialty_windows_and_doors_are_placeholders(self):
        r = run({"duration_weeks": 15, "exterior": {"specialty_windows": 1, "door_types": {"specialty": 2}}})
        for key in ("specialty_windows", "specialty_doors"):
            self.assertEqual(one(r, key)["cost"], 0)
            self.assertTrue(one(r, key)["placeholder"])

    def test_door_types(self):
        r = run({"duration_weeks": 15, "exterior": {"door_types": {
            "single": 2, "single_wood": 1, "french": 1, "french_wood": 1, "sliding": 1}}})
        doors = lines(r, "ext_doors")
        self.assertEqual(sorted(l["cost"] for l in doors), [1800, 1900, 2000, 2500, 3500])
        self.assertEqual(one(r, "ext_door_install")["quantity"], 6)
        self.assertEqual(one(r, "ext_door_hw")["quantity"], 6)
        self.assertEqual(lines(r, "front_door"), [])
        old = run({"duration_weeks": 15, "exterior": {"exterior_doors": 3}})
        self.assertEqual(one(old, "ext_doors")["cost"], 2700)

    def test_front_doors_go_on_front_door_line(self):
        r = run({"duration_weeks": 15, "exterior": {"front_door": True, "door_types": {"sidelights_wood": 1}}})
        self.assertEqual(one(r, "front_door")["cost"], 6000)
        self.assertEqual(one(r, "front_door_hw")["quantity"], 1)
        self.assertEqual(lines(r, "ext_doors"), [])
        self.assertEqual(lines(r, "ext_door_install"), [])
        flag_only = run({"duration_weeks": 15, "exterior": {"front_door": True}})
        self.assertEqual(one(flag_only, "front_door")["cost"], 3500)

    def test_andersen_doors_follow_tier(self):
        for tier, front, slider in (("Quality", 3000, 2500), ("Premium", 4500, 3750), ("Luxury", 7500, 6250)):
            r = run({"duration_weeks": 15, "exterior": {"window_tier": tier, "door_types": {
                "front_andersen": 1, "sliding_andersen": 1}}})
            self.assertEqual(one(r, "front_door")["cost"], front)
            self.assertEqual(one(r, "ext_doors")["cost"], slider)

    def test_stained_wood_doors_get_bump(self):
        r = run({"duration_weeks": 15, "exterior": {"wood_doors_stained": True, "door_types": {
            "french_wood": 1, "french": 1, "sidelights_wood": 1}}})
        self.assertEqual(sorted(l["cost"] for l in lines(r, "ext_doors")), [1900, 3850])
        self.assertEqual(one(r, "front_door")["cost"], 6600)

    def test_paint_amount_wins(self):
        r = run({"duration_weeks": 15, "heated_sf": 2500,
                 "exterior": {"paint_amount": 19000, "full_exterior_repaint": True}})
        self.assertEqual(one(r, "ext_paint")["cost"], 19000)

    def test_partial_exterior_paint_units(self):
        r = run({"duration_weeks": 15, "exterior": {"paint_units": 13}})
        self.assertEqual(one(r, "ext_paint")["cost"], 13 * 1200)          # Greenway
        full = run({"duration_weeks": 15, "heated_sf": 2500,
                    "exterior": {"full_exterior_repaint": True, "paint_units": 4}})
        self.assertEqual(one(full, "ext_paint")["cost"], 20000)            # full repaint wins
        self.assertEqual(lines(run({"duration_weeks": 15}), "ext_paint"), [])

    def test_garage_paint_screening_gutters(self):
        r = run({"duration_weeks": 15, "heated_sf": 2500,
                 "exterior": {"garage_doors_single": 1, "garage_doors_double": 1, "full_exterior_repaint": True,
                              "porch_screening_lf": 30, "gutters": True}})
        self.assertEqual(sum(l["cost"] for l in lines(r, "garage_door")), 10700)
        self.assertEqual(one(r, "ext_paint")["cost"], 20000)
        self.assertEqual(one(r, "porch_screening")["cost"], 4200)
        self.assertEqual(one(r, "gutters")["cost"], 2 * 1200)        # partial job default
        whole = run({"duration_weeks": 30, "whole_home": True, "exterior": {"gutters": True}})
        self.assertEqual(one(whole, "gutters")["quantity"], 6)
        counted = run({"duration_weeks": 15, "exterior": {"gutters": 3}})
        self.assertEqual(one(counted, "gutters")["cost"], 3600)

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

    def test_footing_per_lf_of_perimeter(self):
        r = run({"duration_weeks": 15, "addition_sf": 1000, "foundation_sf": 500, "foundation_perimeter_lf": 70})
        self.assertEqual(one(r, "footing")["cost"], 70 * 42)

    def test_footing_perimeter_estimated_from_footprint(self):
        r = run({"duration_weeks": 15, "addition_sf": 1000, "foundation_sf": 500})
        self.assertEqual(one(r, "footing")["quantity"], 68)           # 3 x sqrt(500) = 67.1 -> 68
        r = run({"duration_weeks": 15, "addition_sf": 400})
        self.assertEqual(one(r, "footing")["quantity"], 60)           # falls back to the addition SF

    def test_framing_and_insulation_on_framed_area(self):
        r = run({"duration_weeks": 15, "floor_plan_sf": 2000, "framed_sf": 300})
        self.assertEqual(one(r, "framing_materials")["quantity"], 300)
        self.assertEqual(one(r, "insulation")["quantity"], 300)
        est = run({"duration_weeks": 15, "floor_plan_sf": 2000})
        self.assertEqual(one(est, "framing_materials")["quantity"], 500)   # 25% of the floor plan
        self.assertTrue(any("Framed area not given" in x for x in est["review"]))

    def test_exterior_trim_materials_are_three_per_lf(self):
        r = run({"duration_weeks": 15, "exterior": {"trim_lf": 400}})
        self.assertEqual(one(r, "ext_trim_labor")["quantity"], 400)
        self.assertEqual(one(r, "ext_trim_materials")["quantity"], 1200)   # Malm / Taggart: 1,200 / 400
        self.assertEqual(one(r, "ext_trim_materials")["cost"], 1200 * 2.25)

    def test_no_tier_is_flagged(self):
        r = run({"duration_weeks": 15, "rooms": [{"type": "other"}]})
        self.assertTrue(any("No tier" in x for x in r["review"]))
        r = run({"duration_weeks": 15, "tier": "Premium", "rooms": [{"type": "other"}]})
        self.assertFalse(any("No tier" in x for x in r["review"]))
        self.assertEqual(r["highest_tier"], "Premium")

    def test_untiered_items_take_the_job_tier(self):
        r = run({"duration_weeks": 15, "tier": "Luxury",
                 "countertops": [{"name": "Vanity", "sf": 10}],
                 "fixtures": [{"category": "plumbing", "item": "toilet"}]})
        self.assertEqual(one(r, "countertops")["cost"], 10 * 237.5)
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
        self.assertTrue(one(r, "int_trim_labor")["placeholder"])     # no rooms to count trim days from

    def test_phases_in_template_order(self):
        r = run({"duration_weeks": 15, "floor_plan_sf": 500, "exterior": {"gutters": True}})
        numbers = [p["number"] for p in r["phases"]]
        order = [bp._phase_number(n) for _, n in fx.PHASES]
        self.assertEqual(numbers, sorted(numbers, key=order.index))
        self.assertEqual(numbers[0], "1000")
        self.assertEqual(numbers[-1], "6200")



class StatedPrices(unittest.TestCase):
    """Dollar figures Tyler or Jason gave win over the tool's pricing (Jason, Nov 20 2026)."""

    def test_stated_price_replaces_tool_lines(self):
        scope = {"duration_weeks": 40, "framed_sf": 2200, "estimates": {"framing_days": {"value": 38}},
                 "stated_prices": [{"item": "Framing labor", "lines": ["framing_labor"], "amount": 62000,
                                    "source": "tool-page notes"}]}
        r = run(scope)
        labor = one(r, "framing_labor")
        self.assertEqual((labor["quantity"], labor["unit_cost"], labor["cost"]), (1, 62000, 62000))
        self.assertEqual(one(r, "framing_materials")["quantity"], 2200)   # quantities still priced
        self.assertTrue(any("62,000 stated price" in x for x in r["review"]))

    def test_stated_price_replaces_a_placeholder(self):
        scope = {"duration_weeks": 15, "exterior": {"specialty_windows": 1},
                 "stated_prices": [{"item": "Bifold pass-through window", "lines": ["specialty_windows"],
                                    "amount": 6000}]}
        r = run(scope)
        line = one(r, "specialty_windows")
        self.assertEqual((line["cost"], line["placeholder"]), (6000, ""))
        self.assertFalse(any("$0 placeholder" in x and "Specialty Windows" in x for x in r["review"]))

    def test_stated_price_for_work_the_tool_did_not_price_adds_the_line(self):
        r = run({"duration_weeks": 15, "stated_prices": [
            {"item": "Fireplace, surround and gas line", "lines": ["fireplace", "fireplace_surround"],
             "amount": 17700}]})
        self.assertEqual(one(r, "fireplace")["cost"], 17700)
        self.assertEqual(lines(r, "fireplace_surround"), [])

    def test_stated_price_outside_the_template_gets_its_own_line(self):
        r = run({"duration_weeks": 15, "stated_prices": [
            {"item": "Outdoor pizza oven", "lines": None, "amount": 4500, "phase": "4200", "cost_type": "sub"}]})
        line = one(r, "stated_price")
        self.assertEqual((line["name"], line["phase"], line["cost"], line["cost_type"]),
                         ("Outdoor pizza oven", "4200", 4500, bp.COST_TYPE_SUB))

    def test_stated_price_without_a_phase_or_on_a_sized_line_is_flagged_not_applied(self):
        r = run({"duration_weeks": 15, "stated_prices": [
            {"item": "Something", "lines": None, "amount": 900},
            {"item": "Permits", "lines": ["permits"], "amount": 5000}]})
        self.assertEqual(lines(r, "stated_price"), [])
        self.assertNotEqual(one(r, "permits")["cost"], 5000)
        self.assertEqual(sum("not added" in x or "not applied" in x for x in r["review"]), 2)

    def test_zero_or_missing_amount_is_ignored(self):
        base = run({"duration_weeks": 15, "exterior": {"trim_lf": 100}})
        r = run({"duration_weeks": 15, "exterior": {"trim_lf": 100},
                 "stated_prices": [{"item": "Trim", "lines": ["ext_trim_labor"], "amount": None}]})
        self.assertEqual(r["total_cost"], base["total_cost"])


class DryRunLineTable(unittest.TestCase):
    """Nov 23 2026: every line says where its number came from, and the result
    carries the input flags, so a dry run can be checked line by line."""

    def test_every_line_has_a_source(self):
        r = run({"duration_range": "10-15", "whole_home": True, "heated_sf": 3000, "framed_sf": 200,
                 "exterior": {"siding_squares": 60, "trim_lf": 720, "full_exterior_repaint": True},
                 "rooms": [{"name": "Primary bath", "type": "primary_bath", "tier": "Quality"}]})
        for line in lines(r):
            self.assertTrue(line["source"], line["name"])
        self.assertEqual(one(r, "cm")["source"], "12.5 weeks x $900/wk")
        self.assertEqual(one(r, "ext_paint")["source"], "3000 heated SF x $8")

    def test_inputs_are_returned(self):
        r = run({"duration_range": "10-15", "whole_home": True, "heated_sf": 3000, "framed_sf": 200,
                 "floor_plan_sf": 240, "punch_list_days": 3,
                 "exterior": {"full_exterior_repaint": True, "gutters": False, "trim_lf": 720},
                 "rooms": [{"name": "Primary bath", "type": "primary_bath", "tier": "Premium", "sf": 130}]})
        inp = r["inputs"]
        self.assertEqual((inp["whole_home"], inp["full_exterior_repaint"], inp["gutters"]), (True, True, False))
        self.assertEqual((inp["tier"], inp["weeks"], inp["duration_range"]), ("Premium", 12.5, "10-15"))
        self.assertEqual((inp["heated_sf"], inp["framed_sf"], inp["floor_plan_sf"]), (3000, 200, 240))
        self.assertEqual(inp["rooms"], [{"name": "Primary bath", "type": "primary_bath",
                                         "tier": "Premium", "sf": 130}])

    def test_placeholder_source_says_why(self):
        r = run({"duration_weeks": 15, "fixtures": [
            {"category": "plumbing", "item": "drop-in tub", "tier": "Quality", "quantity": 1}]})
        ph = [l for l in lines(r) if l["placeholder"]]
        self.assertTrue(ph and ph[0]["source"].startswith("$0 placeholder"))


if __name__ == "__main__":
    unittest.main()
