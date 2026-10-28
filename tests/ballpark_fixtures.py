"""Shared test data for the Preliminary Ballpark Tool: the Master Design Build
Template as it stood on Oct 27, 2026 (spec Appendix A), built into a
ballpark_pricing.Template without touching JobTread."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ballpark_pricing as bp  # noqa: E402

PHASES = [
    ("22PCTdp37bGe", "1000 Preparation and Management Fees"), ("22PCTdp37bGn", "1300 Preliminary Construction"),
    ("22PCTdp37bGy", "2001 Excavation and Foundation"), ("22PCTdp37bH6", "3100 Rough Structure"),
    ("22PCTdp37bHD", "3600 Rough Plumbing"), ("22PCTdp37bHQ", "3700 Rough Electrical"),
    ("22PCTdp37yBv", "3800 Rough HVAC"), ("22PCTdp37yBz", "3900 Tile Prep"), ("22PCTdp37yC4", "4100 Roofing"),
    ("22PCTdp37yC7", "4200 Masonry"), ("22PCTdp37yCE", "4500 Windows and Doors"),
    ("22PCTdp37yCT", "4700 Insulation"), ("22PCTdp37yCV", "4800 Exterior Cladding"),
    ("22PCTdp37yCd", "5000 Interior Finishing"), ("22PCTdp37yCq", "5300 Tile"),
    ("22PCTdp37yCv", "5400 Cabinetry and Storage"), ("22PCTdp37yD8", "5500 Appliances"),
    ("22PCTdp37yDB", "5600 Finish Plumbing"), ("22PCTdp37yDF", "5700 Finish Electrical"),
    ("22PCTdp37yDH", "5800 Finish HVAC"), ("22PCTdp37yDK", "5900 Interior Wall Coverings"),
    ("22PCTdp37yDS", "6100 Landscaping and Driveway"), ("22PCTdp37yDP", "6200 Completion"),
]

TYPES = {"L": bp.COST_TYPE_LABOR, "M": bp.COST_TYPE_MATERIALS, "S": bp.COST_TYPE_SUB, "O": bp.COST_TYPE_OTHER}

# key -> (phase, template unitCost, cost type). None = blank in the template.
ITEMS = {
    "permits": ("1000", 800, "O"), "cm": ("1000", 900, "L"), "port_a_john": ("1000", 110, "S"),
    "selection": ("1000", 125, "L"), "storage": ("1000", 1650, "L"),
    "dumpster": ("1300", 500, "O"), "dust": ("1300", 100, "M"), "demo": ("1300", 300, "S"),
    "temp_pole": ("1300", 0, "S"), "water_tap": ("1300", 3500, "S"), "rewa_fee": ("1300", 2750, "O"),
    "sewer_tap": ("1300", 9560, "S"),
    "footing": ("2001", 42, "S"), "slab": ("2001", 19.09, "S"), "waterproofing": ("2001", 50, "S"),
    "crawlspace": ("2001", 0, "S"), "termite": ("2001", 400, "S"),
    "framing_labor": ("3100", 300, "S"), "deck_framing": ("3100", 17.25, "M"), "decking": ("3100", 23.58, "M"),
    "framing_materials": ("3100", 20, "M"),
    "plumb_swap": ("3600", 625, "S"), "plumb_new_in": ("3600", 1325, "S"), "plumb_tub_in": ("3600", 1525, "S"),
    "plumb_vent": ("3600", 515.06, "S"), "plumb_new_out": ("3600", 927, "S"), "plumb_tub_out": ("3600", 1275, "S"),
    "plumb_water_sewer": ("3600", 572.97, "S"), "plumb_cut_cap": ("3600", 164.30, "S"),
    "wh_tank": ("3600", 4650.41, "S"), "wh_tankless": ("3600", 4897.32, "S"),
    "recessed": ("3700", 125, "L"), "surface": ("3700", 75, "M"), "receptacle": ("3700", 25, "S"),
    "switch": ("3700", 25, "S"), "gfci": ("3700", 75, "S"), "under_cabinet": ("3700", 875, "S"),
    "dimmer": ("3700", 75, "S"), "general_electrical": ("3700", 9, "L"),
    "hvac_system": ("3800", 0, "S"), "hvac_supply": ("3800", 475, "S"), "hvac_return": ("3800", 875, "S"),
    "tile_prep_shower": ("3900", 1600, "S"), "tile_prep_surround": ("3900", 900, "S"),
    "roofing": ("4100", 1100, "S"), "metal_roof": ("4100", 0, "S"),
    "chimney": ("4200", 13000, "S"), "fireplace_surround": ("4200", 7500, "L"), "fireplace": ("4200", 4000, "M"),
    "foundation_brick": ("4200", 64.25, "S"), "brick_facade": ("4200", 137, "S"),
    "windows": ("4500", 600, "L"), "ext_doors": ("4500", 900, "L"), "ext_door_hw": ("4500", 250, "L"),
    "ext_door_install": ("4500", 1450, "S"), "front_door": ("4500", 3500, "L"), "front_door_hw": ("4500", 400, "L"),
    "int_doors": ("4500", 275, "L"), "int_door_hw": ("4500", 75, "L"), "pocket_doors": ("4500", 700, "L"),
    "shower_door": ("4500", 2250, "L"), "garage_door": ("4500", 0, "S"), "int_door_install": ("4500", 75, "S"),
    "window_install": ("4500", None, "L"),
    "insulation": ("4700", 4.5, "S"),
    "siding_labor": ("4800", 500, "S"), "siding_materials": ("4800", 157, "M"),
    "ext_trim_materials": ("4800", 2.25, "M"), "ext_trim_labor": ("4800", 18, "S"),
    "ext_paint": ("4800", 0, "S"), "porch_screening": ("4800", 0, "L"),
    "drywall": ("5000", 2.78, "S"), "drywall_materials": ("5000", 0.5, "M"), "hardwood": ("5000", 10.25, "S"),
    "lvp_install": ("5000", 2, "M"), "carpet": ("5000", 4.25, "L"), "lvp": ("5000", 6.5, "L"),
    "hardwood_finishing": ("5000", 3.25, "S"), "int_trim_materials": ("5000", 3.25, "L"),
    "int_trim_labor": ("5000", 425, "S"),
    "tile_primary": ("5300", 0, "L"), "tile_hall": ("5300", 0, "L"), "tile_backsplash": ("5300", 0, "L"),
    "tile_ceramic": ("5300", 0, "S"),
    "cabinet_paint": ("5400", 75, "S"), "cabinets_kitchen": ("5400", 625, "S"), "vanities": ("5400", 625, "S"),
    "countertops": ("5400", 95, "S"), "cabinet_hw": ("5400", 450, "L"), "cabinet_install": ("5400", 65, "S"),
    "builtins": ("5400", 0, "L"), "closets": ("5400", 0, "M"),
    "appliances": ("5500", 16000, "L"), "appliance_install": ("5500", 75, "L"),
    "plumbing_fixtures": ("5600", 0, "M"), "finish_plumbing": ("5600", 75, "S"), "bath_hardware": ("5600", None, "M"),
    "electrical_fixtures": ("5700", 0, "L"), "finish_electrical": ("5700", 75, "S"),
    "finish_hvac": ("5800", 450, "M"),
    "int_paint": ("5900", 0, "S"),
    "landscaping": ("6100", 10000, "S"), "driveway": ("6100", 8.95, "S"), "gutters": ("6100", 0, "S"),
    "punch": ("6200", 1800, "L"), "cleaning": ("6200", 600, "S"), "warranty": ("6200", None, "L"),
}


def template():
    phases = [{"id": pid, "name": name, "number": bp._phase_number(name), "position": chr(ord("a") + i)}
              for i, (pid, name) in enumerate(PHASES)]
    items = {}
    for key, (phase, cost, kind) in ITEMS.items():
        item_id = bp.T[key]
        items[item_id] = {"id": item_id, "name": bp.T_NAMES[key], "unit_cost": cost, "unit": "",
                          "cost_type": TYPES[kind], "group_id": "", "position": "", "description": "",
                          "phase": phase}
    return bp.Template(phases, items)


TIER_PRICES = {
    "appliance": {"refrigerator": {"prices": {"Quality": 3000, "Premium": 9000, "Luxury": 15000}, "source": "test"},
                  "dishwasher": {"prices": {"Quality": 1200, "Premium": 2000, "Luxury": 3000}, "source": "test"}},
    "plumbing": {"toilet": {"prices": {"Quality": 400, "Premium": 900, "Luxury": 2000}, "source": "test"}},
    "lighting": {"pendant": {"prices": {"Quality": 250, "Premium": 600, "Luxury": 1500}, "source": "test"}},
}
