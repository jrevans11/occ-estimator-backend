"""Preliminary Ballpark Tool: template loader and pricing engine.

Takes a plain scope dict (what the site visit and Tyler's tool-page entries
say is in the job) and returns priced budget lines grouped by the Master
Design Build Template's phases, the markup bracket, the price range and the
margin. No JobTread writes happen here; ballpark.py does those.

The rules come from docs/ballpark/BALLPARK_TOOL_BUILD_SPEC.md section 6/7.
Prices marked "template" are read live from the template at run time; the
rest are Tyler's and Jason's Oct 27, 2026 decisions and live in this file.

Jason's Oct 27, 2026 answers to the spec's Recommended items:
  - Cost codes: each line gets the code that matches its phase group
    (PHASE_COST_CODES), not the template line's own code, several of which
    are wrong.
  - The template has no Tile Materials or Mirrors line; the tool adds both
    to each budget. The template itself is not edited.
  - Appliance / plumbing fixture / lighting allowances come from a cached
    tier price table (ballpark_tier_prices.json), not a live search per run.

Nov 17, 2026: rates and quantity rules replaced with Tyler's observed practice
on the 20 most recent real Preliminary Ballparks (docs/ballpark/case-studies/
pricing-patterns.md). Jason: "use what Tyler does, then tweak it if needed."

Nov 19, 2026: Tyler's answers to the review doc (Granola "Ballpark pricing
tool"): flat 28% markup, window levels $600 / $1,200 / $2,500, door types,
tile lumps are allowances, preconstruction as a % of the contract, survey
$1,850, built-ins $625 / $1,100 per LF, a dictated exterior paint number.

Nov 20, 2026 (Jason, Ballpark Pricing Inputs sheet): tile lumps are labor with
a separate per-SF tile material allowance; Ceramic Tile Installation units for
small spaces only; new door types and prices (wood, sidelights, Andersen tiers,
French, sliding) with a 10% bump for stained wood doors; mulled windows count
per sash (double 2, triple 3, large or odd 2).

Standard library only.
"""

import json
import math
import os

TEMPLATE_GROUP_ID = "22PCTdp32jat"   # Master Design Build Template (catalog cost group)

COST_TYPE_LABOR = "22P9ppJUAHYN"
COST_TYPE_MATERIALS = "22P9ppJUAHYP"
COST_TYPE_SUB = "22P9ppJUAHYQ"
COST_TYPE_OTHER = "22P9ppJUAHYR"
COST_CODE_INTERNAL_USE = "22PETCRMQJUW"

NOTES_ITEM_NAME = "NOTES (internal - not shown on client documents)"

TIERS = ("Quality", "Premium", "Luxury")

# Phase group number -> the matching cost code (Jason, Oct 27 2026).
# 6200 Completion uses "Completion" (6000): the org's 6200 code is Driveway.
PHASE_COST_CODES = {
    "1000": "22PCLxjWDXqn",  # 1000 Preparation and Management Fees
    "1300": "22PErKPZjXrL",  # 1300 Preliminary Construction
    "2001": "22PCLjmEf6NU",  # 2001 Excavation and Foundation
    "3100": "22PCLjPauHLC",  # 3100 Rough Structure
    "3600": "22PCLjihcR2C",  # 3600 Rough Plumbing
    "3700": "22PCLjLZVPfQ",  # 3700 Rough Electrical
    "3800": "22PCLjLbdqW8",  # 3800 Rough HVAC
    "3900": "22PCLjLcq5Rs",  # 3900 Tile Prep/Waterproofing
    "4100": "22PCLjLf4cqK",  # 4100 Roofing
    "4200": "22PCLjLg8Eeh",  # 4200 Masonry
    "4500": "22PCLjLh8Sab",  # 4500 Windows and Doors
    "4700": "22PCLjLiCnpf",  # 4700 Insulation
    "4800": "22PCLjLjCcha",  # 4800 Exterior Cladding
    "5000": "22PCLjLkHVdx",  # 5000 Interior Finishing
    "5300": "22PCLjLmS2Rb",  # 5300 Tile
    "5400": "22PCLjLnR6aN",  # 5400 Cabinetry and Storage
    "5500": "22PCLjLpXJm9",  # 5500 Appliances
    "5600": "22PCLjpB4kpq",  # 5600 Finish Plumbing
    "5700": "22PCLjPWFwbP",  # 5700 Finish Electrical
    "5800": "22PCLjPXMmEM",  # 5800 Finish HVAC
    "5900": "22PCLjPYUqye",  # 5900 Interior Wall Coverings
    "6100": "22PCLjPZnLGj",  # 6100 Landscaping and Driveway
    "6200": "22P9ppJUAHYM",  # Completion (6000)
}

# Template line IDs (Appendix A snapshot, Oct 27 2026). Looked up by ID first,
# then by name, so a renamed line still resolves.
T = {
    "permits": "22PCTdp37bGf", "cm": "22PCTdp37bGg", "port_a_john": "22PCTdp37bGh",
    "selection": "22PCTdp37bGi", "storage": "22PCTdp37bGj",
    "dumpster": "22PCTdp37bGr", "dust": "22PCTdp37bGs", "demo": "22PCTdp37bGt",
    "temp_pole": "22PCTdp37bGu", "water_tap": "22PCTdp37bGv", "rewa_fee": "22PCTdp37bGw",
    "sewer_tap": "22PCTdp37bGx",
    "preconstruction": "22PCTdp37bGm", "site_work": "22PCTdp37bGp", "grading": "22PCTdp37bGq",
    "footing": "22PCTdp37bGz", "slab": "22PCTdp37bH2", "waterproofing": "22PCTdp37bH3",
    "crawlspace": "22PCTdp37bH4", "termite": "22PCTdp37bH5",
    "framing_labor": "22PCTdp37bH7", "deck_framing": "22PCTdp37bH8", "decking": "22PCTdp37bH9",
    "framing_materials": "22PCTdp37bHA",
    "general_plumbing": "22PGSq65aGiZ", "paint_materials": "22PCTdp37yDL",
    "plumb_swap": "22PCTdp37bHE", "plumb_new_in": "22PCTdp37bHF", "plumb_tub_in": "22PCTdp37bHG",
    "plumb_vent": "22PCTdp37bHH", "plumb_new_out": "22PCTdp37bHJ", "plumb_tub_out": "22PCTdp37bHK",
    "plumb_water_sewer": "22PCTdp37bHL", "plumb_cut_cap": "22PCTdp37bHM",
    "wh_tank": "22PCTdp37bHN", "wh_tankless": "22PCTdp37bHP",
    "recessed": "22PCTdp37yBj", "surface": "22PCTdp37yBk", "receptacle": "22PCTdp37yBm",
    "switch": "22PCTdp37yBn", "gfci": "22PCTdp37yBq", "under_cabinet": "22PCTdp37yBr",
    "dimmer": "22PCTdp37yBs", "general_electrical": "22PCTdp37yBu",
    "hvac_system": "22PCTdp37yBw", "hvac_supply": "22PCTdp37yBx", "hvac_return": "22PCTdp37yBy",
    "tile_prep_shower": "22PCTdp37yC2", "tile_prep_surround": "22PCTdp37yC3",
    "roofing": "22PCTdp37yC5", "metal_roof": "22PCTdp37yC6",
    "chimney": "22PCTdp37yC8", "fireplace_surround": "22PCTdp37yCA", "fireplace": "22PCTdp37yCB",
    "foundation_brick": "22PCTdp37yCC", "brick_facade": "22PCTdp37yCD",
    "windows": "22PCTdp37yCF", "ext_doors": "22PCTdp37yCG", "ext_door_hw": "22PCTdp37yCH",
    "ext_door_install": "22PCTdp37yCJ", "front_door": "22PCTdp37yCK", "front_door_hw": "22PCTdp37yCL",
    "int_doors": "22PCTdp37yCM", "int_door_hw": "22PCTdp37yCN", "pocket_doors": "22PCTdp37yCP",
    "shower_door": "22PCTdp37yCQ", "garage_door": "22PCTdp37yCR", "int_door_install": "22PCTdp37yCS",
    "window_install": "22PGSqSUDMCY",
    "insulation": "22PCTdp37yCU",
    "siding_labor": "22PCTdp37yCW", "siding_materials": "22PCTdp37yCX",
    "ext_trim_materials": "22PCTdp37yCZ", "ext_trim_labor": "22PCTdp37yCa",
    "ext_paint": "22PCTdp37yCb", "porch_screening": "22PCTdp37yCc",
    "drywall": "22PCTdp37yCe", "drywall_materials": "22PCTdp37yCf",
    "hardwood": "22PCTdp37yCg", "lvp_install": "22PCTdp37yCh", "carpet": "22PCTdp37yCi",
    "lvp": "22PCTdp37yCj", "hardwood_finishing": "22PCTdp37yCk",
    "int_trim_materials": "22PCTdp37yCm", "int_trim_labor": "22PCTdp37yCp",
    "tile_primary": "22PCTdp37yCr", "tile_hall": "22PCTdp37yCs", "tile_backsplash": "22PCTdp37yCt",
    "tile_ceramic": "22PCTdp37yCu",
    "cabinet_paint": "22PCTdp37yCw", "cabinets_kitchen": "22PCTdp37yCx", "vanities": "22PCTdp37yCz",
    "countertops": "22PCTdp37yD3", "cabinet_hw": "22PCTdp37yD4", "cabinet_install": "22PCTdp37yD5",
    "builtins": "22PCTdp37yD6", "closets": "22PCTdp37yD7",
    "appliances": "22PCTdp37yD9", "appliance_install": "22PCTdp37yDA",
    "plumbing_fixtures": "22PCTdp37yDC", "finish_plumbing": "22PCTdp37yDE",
    "bath_hardware": "22PYRwwBbFub",
    "electrical_fixtures": "22PCTdp37yDD", "finish_electrical": "22PCTdp37yDG",
    "finish_hvac": "22PCTdp37yDJ",
    "int_paint": "22PCTdp37yDM",
    "landscaping": "22PCTdp37yDT", "driveway": "22PCTdp37yDU", "gutters": "22PCTdp37bHC",
    "punch": "22PCTdp37yDQ", "cleaning": "22PCTdp37yDR", "warranty": "22PHezSqGrHr",
}

# Template line names, for the name fallback when an ID is gone.
T_NAMES = {
    "permits": "Permits and Fees", "cm": "Construction Management And Service",
    "port_a_john": "Port a John", "selection": "Project Selection Coordination",
    "storage": "On Site Storage", "dumpster": "Dumpster Fees", "dust": "Dust Control Materials",
    "preconstruction": "Preconstruction costs", "site_work": "Site Work", "grading": "Grading",
    "demo": "Demolition", "temp_pole": "Temporary Power Pole", "water_tap": "New Water Tap and Meter",
    "rewa_fee": "Tap fee to be paid directly to ReWa", "sewer_tap": "Sewer Line Tap",
    "footing": "Footing and Foundation", "slab": "Pre Slab and Slab",
    "waterproofing": "Water Proofing - Foundation", "crawlspace": "Crawlspace Treatment",
    "termite": "Termite Protection", "framing_labor": "Framing Labor",
    "deck_framing": "Deck Framing Materials", "decking": "Decking Materials (Trex Enhanced Naturals)",
    "framing_materials": "Framing Materials",
    "general_plumbing": "General Plumbing", "paint_materials": "Paint Materials",
    "plumb_swap": "Plumbing Fixture swap out same location",
    "plumb_new_in": "New Fixture Location within same footprint",
    "plumb_tub_in": "New Fixture location in same footprint- Tub",
    "plumb_vent": "New Vent thru Roof",
    "plumb_new_out": "New Fixture Location outside footprint (requires Water and Sewer add-on)",
    "plumb_tub_out": "New Tub Location outside footprint (requires Water and Sewer add-on)",
    "plumb_water_sewer": "Water and Sewer installation (per 10 LF)",
    "plumb_cut_cap": "Cut and Cap drain and water prior to demo (per fixture)",
    "wh_tank": "Tanked Water Heater System", "wh_tankless": "Tankless Water Heater System",
    "recessed": "Recessed Lighting", "surface": "Surface Mount Light", "receptacle": "Receptacle",
    "switch": "One Way Switches", "gfci": "GFCI Receptacle", "under_cabinet": "Under Cabinet Lighting",
    "dimmer": "Dimmer Switches", "general_electrical": "General Electrical",
    "hvac_system": "New System and Duct work", "hvac_supply": "Relocation of Supply Duct",
    "hvac_return": "Relocation of Return Vent",
    "tile_prep_shower": "Tile Prep and Waterproofing - walk in shower",
    "tile_prep_surround": "Tile Prep and Water Proofing- tile surround",
    "roofing": "Roofing Specification", "metal_roof": "Metal Roof Accents",
    "chimney": "Brick Chimney", "fireplace_surround": "Fireplace Surround", "fireplace": "Fireplace",
    "foundation_brick": "Foundation Wall- Brick Facade", "brick_facade": "Brick Facade - entire wall",
    "windows": "Windows", "ext_doors": "Exterior Doors", "ext_door_hw": "Exterior Door Hardware",
    "ext_door_install": "Exterior Door Installation", "front_door": "Front Door",
    "front_door_hw": "Front Door Hardware", "int_doors": "Interior Doors - Pre-hung",
    "int_door_hw": "Door Hardware - Interior", "pocket_doors": "Pocket Doors",
    "shower_door": "Glass Shower Door", "garage_door": "Garage door",
    "int_door_install": "Interior Door Installation", "window_install": "Window installation",
    "insulation": "Insulation", "siding_labor": "Exterior Siding Labor",
    "siding_materials": "Exterior Siding Materials", "ext_trim_materials": "Exterior Trim Materials",
    "ext_trim_labor": "Exterior Trim Labor", "ext_paint": "Exterior Paint",
    "porch_screening": "Porch Screening", "drywall": "Drywall Hang and Finish",
    "drywall_materials": "Drywall Materials", "hardwood": "Hardwood Flooring",
    "lvp_install": "LVP Flooring Installation", "carpet": "Carpets", "lvp": "LVP Flooring",
    "hardwood_finishing": "Hardwood Finishing", "int_trim_materials": "Interior Trim Materials",
    "int_trim_labor": "Interior Trim Labor", "tile_primary": "Primary Bath Tile",
    "tile_hall": "Hall Bath Tile", "tile_backsplash": "Backsplash Tile",
    "tile_ceramic": "Ceramic Tile Installation", "cabinet_paint": "Cabinet Painting- Custom Cabinets ONLY",
    "cabinets_kitchen": "Custom Kitchen Cabinetry", "vanities": "Custom Bathroom Vanities",
    "countertops": "Countertop Materials", "cabinet_hw": "Cabinet and Room Hardware",
    "cabinet_install": "Cabinet Installation Costs", "builtins": "Custom Built Ins and Other Cabinetry",
    "closets": "Closet Systems", "appliances": "Appliances",
    "appliance_install": "5520 Appliance Installation", "plumbing_fixtures": "Plumbing Fixtures",
    "finish_plumbing": "5600 Finish Plumbing Installation", "bath_hardware": "Bathroom Hardware",
    "electrical_fixtures": "Electrical Fixtures",
    "finish_electrical": "5730 Electrical Finish Installation", "finish_hvac": "Finish HVAC",
    "int_paint": "Interior Paint", "landscaping": "6100 Landscaping Specifications",
    "driveway": "Driveway and Exterior Concrete paths", "gutters": "6125 Gutters and Downspouts",
    "punch": "Punch list to completion", "cleaning": "Professional Cleaning",
    "warranty": "Warranty and Quality Control",
}

# Lines the tool adds that the template doesn't have (Jason, Oct 27 2026).
# Jason, Nov 20 2026: tile material goes back in as its own per-SF allowance
# line under each tile area (the bath and backsplash lumps are labor).
EXTRA_LINES = {
    "tile_materials": {"name": "Tile Materials", "phase": "5300", "cost_type": COST_TYPE_MATERIALS,
                       "unit": "Square Feet", "description": "Tile material allowance."},
    "tile_install": {"name": "Tile Installation", "phase": "5300", "cost_type": COST_TYPE_SUB,
                     "unit": "Each", "description": "Tile installation, including setting materials."},
    "engineering": {"name": "Engineering", "phase": "1000", "cost_type": COST_TYPE_SUB, "unit": "Each",
                    "description": "Structural engineering for the work shown on the plans."},
    "survey": {"name": "Lot Survey", "phase": "1000", "cost_type": COST_TYPE_SUB, "unit": "Each",
               "description": "Lot survey."},
    "specialty_windows": {"name": "Specialty Windows", "phase": "4500", "cost_type": COST_TYPE_MATERIALS,
                          "unit": "Each", "description": "Specialty window units, priced during review."},
    "specialty_doors": {"name": "Specialty Exterior Doors", "phase": "4500", "cost_type": COST_TYPE_MATERIALS,
                        "unit": "Each", "description": "Specialty exterior door units, priced during review."},
}

# ── Rates set by Tyler and Jason (spec section 6) ───────────────────────────

CM_PER_WEEK = 900
PORT_A_JOHN_PER_MONTH = 110
WEEKS_PER_MONTH = 4.33
DUMPSTER_UNIT = 500
WEEKS_PER_DUMPSTER = 2.5          # Tyler: 1 per 2-2.5 weeks
DUST_CONTROL = 1500               # Jason, Nov 23 2026: $1,500 per job by default
STORAGE_UNIT = 1650
TWO_STORAGE_UNITS_OVER_COST = 390625
NO_STORAGE_UNDER_COST = 160000
SELECTION_HOURS = {"Quality": 12, "Premium": 20, "Luxury": 28}   # small jobs: $1,500 / $2,500 / $3,500 at $125/hr
PERMIT_UNIT = 800
TEMP_POLE = 1800
DEMO_PER_DAY = 300
SITE_WORK_UNIT = 800              # Tyler: Site Work $800 x 1-6 on 12 of 20 ballparks
GRADING_UNIT = 600                # Tyler: Grading $600 x 1-8
ENGINEERING = {"letter": 1850, "full": 2800}   # Frandsen engineering letter / McDowell engineering
SURVEY = 1850                     # Tyler, Nov 19 2026: only when Jason says a survey is needed
# Tyler, Nov 23 2026 (first guided-intake run): preconstruction goes in at $0
# and is added by hand when a job needs it. (Nov 19: 2% / 1.5% / 1% of the
# contract on every job.)
CRAWLSPACE_TREATMENT = 4500
FRAMING_MATERIALS_PER_SF = 20
FRAMING_LABOR_PER_DAY = 450
FOOTING_PER_LF = 42
FINISH_HOURLY = 75
UNDER_CABINET_PER_KITCHEN = 875
GENERAL_ELECTRICAL_PER_SF = 9
# Tyler, Nov 23 2026: no General Plumbing ($1,200 a fixture) for now; rough
# plumbing is itemized (same location, new location, tub, cut and cap, ...)
# at the read's counts. The fixture count still sets finish plumbing hours.
PLUMBING_FIXTURES_PER_ROOM = {"primary_bath": 4, "hall_bath": 3, "powder": 2, "kitchen": 2}
FINISH_PLUMBING_HRS_PER_FIXTURE = 3
FINISH_ELECTRICAL_HRS_PER_ROOM = 1.5
RECESSED_PER_ROOM = 1.5
HVAC_PER_TON = 5000
NEW_DUCTWORK = 7500
# Tyler, Nov 19 2026: three window levels. Jason, Nov 20 2026: a mulled double
# counts as 2 windows, a triple as 3, a large or unusual size as 2. Original
# wood windows to match = Luxury, plus a bump on interior paint for the sash.
WINDOW_PRICE = {"Quality": 600, "Premium": 1200, "Luxury": 2500}
WINDOW_PRODUCT = {"Quality": "vinyl", "Premium": "Andersen 100 Series", "Luxury": "Andersen 400 Series"}
WINDOW_INSTALL = 300
WOOD_WINDOW_PAINT_BUMP = 0.10     # Tyler: "add 5 to 10% of the paint price"
# Exterior door unit by type (Tyler Nov 19, Jason Nov 20 2026). Andersen units
# are tiered like windows: base, +50%, +150%. Front-door types go on the
# template's Front Door line (no install line, as Tyler does it); the rest go on
# Exterior Doors with hardware and installation per door.
EXT_DOOR_PRICE = {
    "single": 900, "single_wood": 2500,
    "sidelights": 3500, "sidelights_wood": 6000,
    "front_andersen": {"Quality": 3000, "Premium": 4500, "Luxury": 7500},
    "french": 1900, "french_wood": 3500,
    "sliding": 2000,
    "sliding_andersen": {"Quality": 2500, "Premium": 3750, "Luxury": 6250},
}
EXT_DOOR_LABEL = {
    "single": "single door unit, fiberglass", "single_wood": "single door unit, wood",
    "sidelights": "front door with sidelights or transom, fiberglass",
    "sidelights_wood": "front door with sidelights or transom, wood",
    "front_andersen": "Andersen front door unit",
    "french": "French door unit, fiberglass", "french_wood": "French door unit, wood",
    "sliding": "sliding door unit, 2-panel vinyl or fiberglass",
    "sliding_andersen": "Andersen sliding door unit",
}
FRONT_DOOR_TYPES = ("sidelights", "sidelights_wood", "front_andersen")
WOOD_DOOR_TYPES = ("single_wood", "sidelights_wood", "french_wood")
WOOD_DOOR_STAIN_BUMP = 0.10       # Jason: stained wood doors cost more to finish than painted
GARAGE_DOOR_SINGLE = 3500
GARAGE_DOOR_DOUBLE = 7200
EXT_PAINT_UNIT = 1200             # Tyler's partial-paint unit (1-20 units on 9 of 20 ballparks)
PORCH_SCREENING_PER_LF = 140
GUTTER_UNIT = 1200
GUTTER_UNITS_DEFAULT = {"whole_home": 6, "partial": 2}
INSULATION_PER_SF = 4.50
# Exterior trim: Tyler's materials qty runs ~3x his labor LF (Malm, Taggart 1,200/400;
# median ~3-4 across 11 ballparks), so materials = LF x 3.
EXT_TRIM_MATERIALS_PER_LF = 3
INT_TRIM_MATERIALS_PER_SF = 3.25
INT_TRIM_MATERIALS_SF_PER_ROOM = 50
INT_TRIM_LABOR_PER_DAY = 425
INT_TRIM_DAYS_PER_ROOM = 1
DRYWALL_SF_PER_ROOM = 300
DRYWALL_MATERIALS_RATIO = 1.35
FLOORING = {"hardwood": 10.25, "hardwood_finishing": 3.75, "lvp": 6.50, "lvp_install": 2.00, "carpet": 4.25}
INT_PAINT_ROOM = 1100
INT_PAINT_TALL_ROOM = 2000
PAINT_MATERIALS_UNIT = 450
# Jason, Nov 23 2026 (after Tyler's first guided-intake run): the per-space
# flat fees are the TILE ALLOWANCE (material) per bath / backsplash, and each
# space also gets its own Tile Installation line. The per-SF material
# allowance is now only for small areas (floor-only, laundry, powder, ...),
# which keep Ceramic Tile Installation units: labor plus setting materials
# (mortar, backer board), not tile or trim (bullnose, Schluter).
TILE_ALLOWANCE = {"primary_bath": 5500, "hall_bath_tub": 2200, "backsplash": 1800}
TILE_INSTALL = {"primary_bath": 7500, "hall_walk_in": 5500, "hall_tub": 4200, "backsplash": 2000}
TILE_LABOR = dict(TILE_ALLOWANCE, small=1200)   # every kind the read can give
TILE_MATERIAL_PER_SF = {"Quality": 7.50, "Premium": 17.50, "Luxury": 50}
TILE_SF_DEFAULT = {"primary_bath": 180, "hall_bath_tub": 110, "backsplash": 30, "small": 50}
CERAMIC_TILE_UNIT = 1200
CERAMIC_UNITS = {"primary_bath": 0, "hall_bath_tub": 0, "backsplash": 0, "small": 1}
CERAMIC_DESCRIPTION = ("Tile installation labor for small tile areas, including setting materials (mortar, "
                       "backer board). Tile and trim pieces (bullnose, Schluter) are not included.")
CABINET_PER_LF = 625
FULL_HEIGHT_PER_LF = 1250
BUILTINS_PER_LF = 1100            # full-height built-ins
BUILTINS_LOW_PER_LF = 625         # Tyler, Nov 19 2026: built-in uppers/lowers
COUNTERTOP_PER_SF = {"Quality": 105, "Premium": 125, "Luxury": 237.50}
BATH_HARDWARE_FIRST = 950         # Tyler: $950 on a one-bath job
BATH_HARDWARE_EACH_MORE = 650     # 2 baths = $1,600, matching his multi-bath jobs
CABINET_HW_NO_KITCHEN = 450
CABINET_HW_KITCHEN = {"Quality": 2500, "Premium": 3000, "Luxury": 3750}
PRIMARY_CLOSET = 4500
SMALL_CLOSET = 350
MIRROR = 700
PUNCH_PER_DAY = 1800
CLEANING_TWO_OVER_COST = 390625
WARRANTY_RATE = 0.01
WARRANTY_MIN = 1200

# Size-driven lines (Tyler's 20 ballparks): (job cost ceiling, value).
PERMIT_COUNTS = [(100000, 2), (200000, 4), (400000, 5), (700000, 7), (None, 9)]
PERMITS_SMALL_INTERIOR = (150000, 1)   # interior-only job under this cost: 1 permit
PERMITS_SINGLE_SPACE = 2               # Tyler, Nov 23 2026: kitchen, bath or porch job
SELECTION_BY_COST = [(160000, None), (250000, 32), (550000, 40), (None, 60)]   # None = by tier
SELECTION_LUXURY_LARGE = 80
PUNCH_DAYS_BY_COST = [(160000, 1), (300000, 2), (400000, 3), (None, 5)]

DURATION_RANGES = {   # tool-page dropdown -> midpoint weeks
    "10-15": 12.5, "15-20": 17.5, "20-25": 22.5, "25-40": 32.5, "40-52": 46.0,
}
DEFAULT_WEEKS_SINGLE_SPACE = 15
DEFAULT_WEEKS_WHOLE_HOME = 32.5

# Markup: flat 28% on every job (Tyler and Jason, Nov 19 2026; the old
# 28/25/23% bracket is gone). Tyler adjusts it by hand in review.
MARKUP = 0.28
MARGIN = round(MARKUP / (1 + MARKUP), 3)   # 0.219

ROOM_KINDS = ("kitchen", "primary_bath", "hall_bath", "powder", "other")
DEFAULT_CEILING_FT = 8
TALL_CEILING_FT = 18

TIER_PRICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ballpark_tier_prices.json")


class PricingError(Exception):
    pass


# ── Template ────────────────────────────────────────────────────────────────

def _phase_number(name):
    head = (name or "").strip().split(" ", 1)[0]
    return head if head.isdigit() else ""


class Template:
    """The Master Design Build Template as read from JobTread: phase groups in
    order, and each line's unitCost, unit, cost type and client description."""

    def __init__(self, phases, items):
        self.phases = phases            # [{"id", "name", "number", "position"}] in template order
        self.items = items              # {item_id: {...}}
        self.by_name = {}
        for item in items.values():
            self.by_name.setdefault(item["name"].strip().lower(), item)
        self.phase_by_number = {p["number"]: p for p in phases}

    def item(self, key):
        found = self.items.get(T.get(key, ""))
        if not found and key in T_NAMES:
            found = self.by_name.get(T_NAMES[key].lower())
        return found


def load_template(jobtread_query, group_id=TEMPLATE_GROUP_ID):
    """Read the template live: phase groups, then every line (paginated)."""
    resp = jobtread_query({"costGroup": {
        "$": {"id": group_id}, "name": {},
        "descendentCostGroups": {"$": {"size": 100}, "nodes": {
            "id": {}, "name": {}, "position": {}, "parentCostGroup": {"id": {}}}},
    }})
    group = (resp or {}).get("costGroup") or {}
    if not group:
        raise PricingError(f"Template cost group {group_id} not found in JobTread.")
    phases = []
    for g in (group.get("descendentCostGroups") or {}).get("nodes") or []:
        if ((g.get("parentCostGroup") or {}).get("id")) != group_id:
            continue
        phases.append({"id": g["id"], "name": g["name"], "number": _phase_number(g["name"]),
                       "position": g.get("position") or ""})
    phases.sort(key=lambda p: p["position"])

    items, page = {}, None
    for _ in range(10):
        args = {"size": 100}
        if page:
            args["page"] = page
        resp = jobtread_query({"costGroup": {"$": {"id": group_id}, "descendentCostItems": {
            "$": args,
            "nodes": {"id": {}, "name": {}, "unitCost": {}, "position": {}, "description": {},
                      "unit": {"name": {}}, "costType": {"id": {}}, "costGroup": {"id": {}}},
            "nextPage": {}}}})
        conn = ((resp or {}).get("costGroup") or {}).get("descendentCostItems") or {}
        for n in conn.get("nodes") or []:
            items[n["id"]] = {
                "id": n["id"], "name": n.get("name") or "", "unit_cost": n.get("unitCost"),
                "unit": (n.get("unit") or {}).get("name") or "",
                "cost_type": (n.get("costType") or {}).get("id") or COST_TYPE_OTHER,
                "group_id": (n.get("costGroup") or {}).get("id") or "",
                "position": n.get("position") or "", "description": n.get("description") or "",
            }
        page = conn.get("nextPage")
        if not page:
            break
    phase_ids = {p["id"]: p for p in phases}
    for item in items.values():
        item["phase"] = (phase_ids.get(item["group_id"]) or {}).get("number", "")
    return Template(phases, items)


def load_tier_prices(path=TIER_PRICES_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ── Small helpers ───────────────────────────────────────────────────────────

def _num(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _count(value):
    return max(0, int(round(_num(value))))


def _tier(value, default="Quality"):
    v = (value or "").strip().capitalize()
    return v if v in TIERS else default


def highest_tier(scope):
    tiers = [_tier(r.get("tier")) for r in scope.get("rooms") or [] if r.get("tier")]
    return max(tiers, key=TIERS.index) if tiers else _tier(scope.get("tier"))


def duration_weeks(scope):
    """Weeks from the dropdown range (its midpoint), else the default for the job shape."""
    if scope.get("duration_weeks"):
        return _num(scope["duration_weeks"])
    rng = DURATION_RANGES.get(str(scope.get("duration_range") or "").replace(" ", ""))
    if rng:
        return rng
    return DEFAULT_WEEKS_WHOLE_HOME if scope.get("whole_home") else DEFAULT_WEEKS_SINGLE_SPACE


def room_wall_lf(room):
    """Perimeter from Rendr when we have it; otherwise a square room of the
    entered SF (4 x sqrt(SF)). Returns (lf, how)."""
    if _num(room.get("perimeter_lf")) > 0:
        return _num(room["perimeter_lf"]), "Rendr/entered perimeter"
    sf = _num(room.get("sf"))
    if sf > 0:
        return round(4 * math.sqrt(sf), 1), f"estimated as a square room: 4 x sqrt({sf:g} SF)"
    return 0.0, "no size given"


def round_range_value(value, step):
    return int(round(value / step) * step)


def format_money_short(value):
    if value >= 1_000_000:
        s = f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"${s}M"
    return f"${int(round(value / 1000))}K"


def price_range(total_price):
    """Low = price x 0.98, high = price x 1.10, rounded to $10K under $250K else $25K."""
    step = 10_000 if total_price < 250_000 else 25_000
    low = round_range_value(total_price * 0.98, step)
    high = round_range_value(total_price * 1.10, step)
    if high <= low:   # a small job can round both ends to the same number
        high = low + step
    return {"low": low, "high": high, "step": step,
            "text": f"{format_money_short(low)} – {format_money_short(high)}"}


def by_cost(table, total_cost):
    """The value for this job size from a [(ceiling, value), ...] table."""
    for ceiling, value in table:
        if ceiling is None or total_cost < ceiling:
            return value
    return table[-1][1]


def markup_bracket(total_cost):
    """Flat 28% on every job size (kept as a function so callers don't change)."""
    return MARKUP, MARGIN


# ── The engine ──────────────────────────────────────────────────────────────

class _Budget:
    def __init__(self, template):
        self.template = template
        self.lines = []
        self.notes = {}          # phase number -> [str]
        self.review = []         # items for Tyler's internal review section

    def note(self, phase, text):
        self.notes.setdefault(phase, []).append(text)

    def add(self, key, quantity, unit_cost=None, name=None, unit=None, note=None, description=None,
            placeholder=None, allowance=False, cost_type=None, phase=None):
        """One budget line. unit_cost None = the template's price. A missing or
        blank price becomes a flagged $0 placeholder rather than a dropped line."""
        if key in EXTRA_LINES:
            spec = EXTRA_LINES[key]
            tpl = None
            phase = phase or spec["phase"]
            name = name or spec["name"]
            unit = unit or spec["unit"]
            cost_type = cost_type or spec["cost_type"]
            description = description if description is not None else spec["description"]
        else:
            tpl = self.template.item(key)
            if not tpl and not phase:
                self.review.append(f"Template line '{T_NAMES.get(key, key)}' is missing from the template; "
                                   f"not priced.")
                return None
            phase = phase or tpl["phase"]
            name = name or tpl["name"]
            unit = unit if unit is not None else tpl["unit"]
            cost_type = cost_type or tpl["cost_type"]
            if description is None:
                description = tpl["description"]
        from_template = unit_cost is None
        if unit_cost is None:
            unit_cost = (tpl or {}).get("unit_cost")
            if not unit_cost:
                placeholder = placeholder or f"template line '{name}' has no price"
                unit_cost = 0
        if placeholder:
            quantity = quantity or 1
            unit_cost = 0
            self.review.append(f"{name}: $0 placeholder - {placeholder}")
        line = {
            "key": key, "template_item_id": (tpl or {}).get("id"), "phase": phase, "name": name,
            "quantity": round(float(quantity), 2), "unit": unit, "unit_cost": round(float(unit_cost), 2),
            "cost_type": cost_type, "cost_code": PHASE_COST_CODES.get(phase, ""),
            "description": description or "", "allowance": allowance, "placeholder": placeholder or "",
            # The rule or source behind the line, for the dry-run line table.
            "source": (f"$0 placeholder: {placeholder}" if placeholder
                       else note or ("template price" if from_template else "tool rule")),
        }
        line["cost"] = round(line["quantity"] * line["unit_cost"], 2)
        self.lines.append(line)
        if note:
            self.note(phase, f"{name}: {note}")
        return line

    def cost(self, exclude=()):
        return round(sum(l["cost"] for l in self.lines if l["key"] not in exclude), 2)


def _estimate(scope, key):
    """A days/hours estimate Claude made from the scope (Open rules in the
    spec). Returns (value, reasoning) or (None, '')."""
    est = (scope.get("estimates") or {}).get(key) or {}
    if isinstance(est, (int, float)):
        return float(est), ""
    value = _num(est.get("value"), None)
    return value, (est.get("reasoning") or "").strip()


def has_ground_work(scope, ext=None):
    """New structure or flatwork on the lot: an addition, foundation, slab,
    deck, porch or driveway. Tyler adds Site Work and Grading on these."""
    ext = ext if ext is not None else (scope.get("exterior") or {})
    return any(_num(v) > 0 for v in (scope.get("addition_sf"), scope.get("foundation_sf"), scope.get("slab_sf"),
                                     scope.get("deck_sf"), ext.get("driveway_sf"), ext.get("porch_screening_lf")))


def _site_lines(b, scope, ext):
    """Site Work, Grading, Engineering, Preconstruction and Lot Survey, only
    when the scope calls for them (Jason, Nov 17 2026). Units come from the
    scope read; on ground work with no count, 1 of each."""
    site = scope.get("site") or {}
    ground = has_ground_work(scope, ext)
    for key, rate, field in (("site_work", SITE_WORK_UNIT, "site_work_units"),
                             ("grading", GRADING_UNIT, "grading_units")):
        given = site.get(field)
        if given is not None and _num(given) > 0:
            units, how = _num(given), "from the scope read"
        elif given is None and ground:
            units, how = 1, "default for new structure or flatwork on the lot"
        else:
            continue
        b.add(key, units, rate, unit="Each", note=f"{units:g} x ${rate:,} ({how})")
    eng = str(site.get("engineering") or "").lower()
    if eng in ENGINEERING:
        b.add("engineering", 1, ENGINEERING[eng],
              name="Engineering letter" if eng == "letter" else "Engineering",
              note=f"structural work calls for {'an engineering letter' if eng == 'letter' else 'engineering'}")
    # Preconstruction is priced in _finish as a share of the contract price.
    if site.get("survey"):
        b.add("survey", 1, SURVEY, note=f"a lot survey is needed (${SURVEY:,})")


def _days_line(b, scope, key, est_key, rate, label):
    value, why = _estimate(scope, est_key)
    if value is None or value <= 0:
        b.add(key, 1, placeholder=f"no {label} estimate - the spec has no rule yet, Tyler to set")
        return
    b.add(key, value, rate, note=f"{value:g} {label} x ${rate:,.0f} (tool estimate: {why or 'no reasoning given'})")


def _price_fixture(item, tier_prices):
    """Unit price for one appliance / plumbing fixture / light. A brand price
    looked up on site wins; then the cached tier table; else None."""
    if _num(item.get("unit_price")) > 0:
        return _num(item["unit_price"]), item.get("price_source") or "looked up for the named brand"
    category = item.get("category") or ""
    name = (item.get("item") or "").strip().lower()
    table = (tier_prices.get(category) or {})
    row = table.get(name)
    if row is None:
        for k, v in table.items():
            if k in name or name in k:
                row = v
                break
    if row:
        tier = _tier(item.get("tier"))
        price = _num((row.get("prices") or row).get(tier))
        if price > 0:
            return price, f"tier table: {tier} average ({row.get('source', 'ballpark_tier_prices.json')})"
    return None, ""


def _fixture_lines(b, scope, category, key, tier_prices, label, extra=()):
    """One allowance line per tier. Items with no tier price stay on that
    tier's line (named "some unpriced") and go to the review list, rather than
    a second line for the same tier (Tyler, Nov 23 2026). extra: already-priced
    items (tier, item, qty, unit price, source), e.g. mirrors."""
    items = [f for f in scope.get("fixtures") or [] if f.get("category") == category]
    by_tier = {}
    for f in items:
        by_tier.setdefault(_tier(f.get("tier")), []).append(f)
    for tier, item, qty, price, source in extra:
        by_tier.setdefault(_tier(tier), []).append({"item": item, "quantity": qty, "unit_price": price,
                                                    "price_source": source})
    for tier in TIERS:
        group = by_tier.get(tier)
        if not group:
            continue
        total, count, details, unpriced = 0.0, 0, [], []
        for f in group:
            qty = max(1, _count(f.get("quantity") or 1))
            price, source = _price_fixture(f, tier_prices)
            desc = f.get("item") or "item"
            if f.get("brand"):
                desc = f"{f['brand']} {desc}"
            if price is None:
                unpriced.append(f"{qty} x {desc}")
                continue
            total += qty * price
            count += qty
            details.append(f"{qty} x {desc} @ ${price:,.0f} ({source})")
        if not count:
            b.add(key, 1, name=f"{label} - {tier} (unpriced items)",
                  placeholder="no tier price for: " + ", ".join(unpriced))
            continue
        name = f"{label} - {tier} allowance ({count} item{'s' if count != 1 else ''}"
        name += ", some unpriced)" if unpriced else ")"
        if unpriced:
            details.append("NOT PRICED, add by hand: " + ", ".join(unpriced))
            b.review.append(f"{label} - {tier}: no tier price for {', '.join(unpriced)}; add it to that line by hand.")
        b.add(key, 1, total, name=name, allowance=True, note="; ".join(details))


def price_scope(scope, template, tier_prices=None):
    """Price a scope dict against the template. Returns the full result:
    lines, notes per phase, review items, totals, markup and range."""
    tier_prices = tier_prices if tier_prices is not None else load_tier_prices()
    scope = _with_job_tier(scope)
    b = _Budget(template)
    weeks = duration_weeks(scope)
    rooms = scope.get("rooms") or []
    ext = scope.get("exterior") or {}
    floors = scope.get("flooring") or {}
    cab = scope.get("cabinetry") or {}
    mech = scope.get("mechanical") or {}
    plumb = scope.get("plumbing") or {}
    service = scope.get("new_service") or {}
    floor_sf = _num(scope.get("floor_plan_sf"))
    addition_sf = _num(scope.get("addition_sf"))
    top_tier = highest_tier(scope)
    if not scope.get("tier") and not any(r.get("tier") for r in rooms):
        b.review.append("No tier was given for the job or any space, so everything is priced at Quality "
                        "(the lowest tier). Set the job tier and run again if that's wrong.")

    # 1000 / 1300 Preparation and management
    b.add("cm", weeks, CM_PER_WEEK, note=f"{weeks:g} weeks x ${CM_PER_WEEK}/wk")
    months = math.ceil(weeks / WEEKS_PER_MONTH)
    b.add("port_a_john", months, PORT_A_JOHN_PER_MONTH, note=f"{weeks:g} wks / 4.33 = {months} months (rounded up)")
    dumpsters = math.ceil(weeks / WEEKS_PER_DUMPSTER)
    b.add("dumpster", dumpsters, DUMPSTER_UNIT,
          note=f"{weeks:g} wks / {WEEKS_PER_DUMPSTER:g} = {dumpsters} (rounded up)")
    b.add("dust", 1, DUST_CONTROL, unit="Each", note=f"${DUST_CONTROL:,} per job (default)")
    if mech.get("full_rewire"):
        b.add("temp_pole", 1, TEMP_POLE, note="full electrical rewire selected")
    _days_line(b, scope, "demo", "demo_days", DEMO_PER_DAY, "days")
    for key in ("water_tap", "rewa_fee", "sewer_tap"):
        if service.get(key):
            b.add(key, 1, None, note=f"new service called for: {service.get(key) if isinstance(service.get(key), str) else 'yes'}")
    _site_lines(b, scope, ext)

    # 2001 / 3100 Structure and foundation. Footings go under new ground-floor
    # footprint only: a second-story addition sits on the existing house.
    # Tyler prices footings per LF of new perimeter, not per SF of footprint.
    if scope.get("foundation_sf") is not None:
        foundation_sf, why = _num(scope["foundation_sf"]), "new foundation SF"
    else:
        foundation_sf, why = addition_sf, "addition SF (no separate foundation SF given)"
    perimeter = _num(scope.get("foundation_perimeter_lf"))
    if perimeter > 0:
        b.add("footing", perimeter, FOOTING_PER_LF, unit="Linear Feet",
              note=f"{perimeter:g} LF of new footing perimeter")
    elif foundation_sf > 0:
        perimeter = math.ceil(3 * math.sqrt(foundation_sf))
        b.add("footing", perimeter, FOOTING_PER_LF, unit="Linear Feet",
              note=f"{perimeter} LF estimated from {foundation_sf:g} {why}: 3 sides of a square "
                   f"footprint (the 4th side is the existing house)")
    elif addition_sf > 0:
        b.note("2001", f"No footing priced: the {addition_sf:g} SF addition has no new foundation (second story).")
    if _num(scope.get("slab_sf")) > 0:
        b.add("slab", _num(scope["slab_sf"]), None, note=f"{_num(scope['slab_sf']):g} slab SF")
    if _num(scope.get("waterproofing_lf")) > 0:
        b.add("waterproofing", _num(scope["waterproofing_lf"]), None)
    if mech.get("crawlspace"):
        b.add("crawlspace", 1, CRAWLSPACE_TREATMENT)
    if addition_sf > 0:
        b.add("termite", 1, None, note="addition")
    framed_sf, framed_how = framed_area(scope)
    if framed_how.startswith("estimated"):
        b.review.append(f"Framed area not given; {framed_how}. Set framed_sf if that's off.")
    if framed_sf > 0 and scope.get("framing", True):
        b.add("framing_materials", framed_sf, FRAMING_MATERIALS_PER_SF, note=f"{framed_sf:g} SF ({framed_how})")
        _days_line(b, scope, "framing_labor", "framing_days", FRAMING_LABOR_PER_DAY, "days")
    if _num(scope.get("deck_sf")) > 0:
        d = _num(scope["deck_sf"])
        b.add("deck_framing", d, None, note=f"{d:g} deck SF")
        b.add("decking", d, None, note=f"{d:g} deck SF")

    # 3600 / 5600 Plumbing
    fixtures_n, fixtures_how = plumbing_fixture_count(scope)
    for key, field in (("plumb_swap", "swap_same"), ("plumb_new_in", "new_in_footprint"),
                       ("plumb_tub_in", "tub_in_footprint"), ("plumb_new_out", "new_outside_footprint"),
                       ("plumb_tub_out", "tub_outside_footprint"), ("plumb_water_sewer", "water_sewer_10lf"),
                       ("plumb_cut_cap", "cut_and_cap"), ("plumb_vent", "vent_thru_roof")):
        n = _count(plumb.get(field))
        if not n:
            continue
        b.add(key, n, None, note=plumb.get("source") or None)
    wh = (mech.get("water_heater") or "none").lower()
    if wh == "tank":
        b.add("wh_tank", 1, None)
    elif wh == "tankless":
        b.add("wh_tankless", 1, None)
    # Tyler, Nov 23 2026: mirrors go in the plumbing fixture allowance, not a line of their own.
    mirrors = _count(scope.get("mirrors"))
    extra = [(top_tier, "mirror", mirrors, MIRROR, f"${MIRROR} mirror allowance")] if mirrors else []
    _fixture_lines(b, scope, "plumbing", "plumbing_fixtures", tier_prices, "Plumbing Fixtures", extra)
    if fixtures_n:
        hrs = fixtures_n * FINISH_PLUMBING_HRS_PER_FIXTURE
        b.add("finish_plumbing", hrs, FINISH_HOURLY,
              note=f"{fixtures_n} fixtures x {FINISH_PLUMBING_HRS_PER_FIXTURE} hrs")
    elif any(f.get("category") == "plumbing" for f in scope.get("fixtures") or []):
        value, why = _estimate(scope, "finish_plumbing_hours")
        if value:
            b.add("finish_plumbing", value, FINISH_HOURLY, note=f"{value:g} hrs (tool estimate: {why})")
        else:
            b.add("finish_plumbing", 1, placeholder="no finish plumbing hours estimate")
    baths = sum(1 for r in rooms if r.get("type") in ("primary_bath", "hall_bath"))
    powders = sum(1 for r in rooms if r.get("type") == "powder")
    bath_units = baths + powders / 2
    if bath_units:
        amount = BATH_HARDWARE_FIRST + BATH_HARDWARE_EACH_MORE * max(0, bath_units - 1)
        b.add("bath_hardware", 1, amount, allowance=True, name="Bathroom Hardware - allowance",
              description="Bath accessory sets: towel bars, towel rings, toilet paper holders and robe hooks.",
              note=f"${BATH_HARDWARE_FIRST} first bath + ${BATH_HARDWARE_EACH_MORE} each more "
                   f"({baths} bath(s), {powders} powder room(s) at half)")

    # 3700 / 5700 Electrical. Tyler prices electrical as General Electrical
    # per SF of renovated area and leaves device lines at token counts.
    elec_sf = floor_sf or addition_sf
    if elec_sf > 0:
        b.add("general_electrical", elec_sf, GENERAL_ELECTRICAL_PER_SF,
              note=f"{elec_sf:g} SF of renovated area x ${GENERAL_ELECTRICAL_PER_SF}")
    if rooms:
        wet = sum(1 for r in rooms if r.get("type") in ("kitchen", "primary_bath", "hall_bath", "powder"))
        counts = {"receptacle": 1, "switch": 1, "dimmer": 1, "gfci": max(1, wet),
                  "recessed": math.ceil(len(rooms) * RECESSED_PER_ROOM),
                  "surface": max(1, math.ceil(len(rooms) / 4))}
        for key, n in counts.items():
            b.add(key, n, None)
        b.note("3700", f"Devices at Tyler's token counts; recessed = {len(rooms)} rooms x {RECESSED_PER_ROOM} "
                       f"(rounded up); GFCI = 1 per kitchen/bath")
    kitchens = sum(1 for r in rooms if r.get("type") == "kitchen")
    if kitchens:
        b.add("under_cabinet", kitchens, UNDER_CABINET_PER_KITCHEN, note=f"{kitchens} kitchen(s)")
    _fixture_lines(b, scope, "lighting", "electrical_fixtures", tier_prices, "Electrical Fixtures")
    if rooms:
        hrs = max(4, math.ceil(len(rooms) * FINISH_ELECTRICAL_HRS_PER_ROOM))
        b.add("finish_electrical", hrs, FINISH_HOURLY,
              note=f"{len(rooms)} rooms x {FINISH_ELECTRICAL_HRS_PER_ROOM} hrs (min 4)")
    elif any(f.get("category") == "lighting" for f in scope.get("fixtures") or []):
        value, why = _estimate(scope, "finish_electrical_hours")
        if value:
            b.add("finish_electrical", value, FINISH_HOURLY, note=f"{value:g} hrs (tool estimate: {why})")
        else:
            b.add("finish_electrical", 1, placeholder="no finish electrical hours estimate")

    # 3800 / 5800 HVAC
    tons = _num(mech.get("hvac_tons"))
    if tons > 0:
        b.add("hvac_system", tons, HVAC_PER_TON, name=f"New HVAC System ({tons:g} tons)", unit="Tons",
              note=f"{tons:g} tons x ${HVAC_PER_TON:,}/ton")
    if mech.get("new_ductwork"):
        b.add("hvac_system", 1, NEW_DUCTWORK, name="New Ductwork", unit="", description="New ductwork per plans.")
    for key, field in (("hvac_supply", "supply_relocations"), ("hvac_return", "return_relocations"),
                       ("finish_hvac", "finish_hvac")):
        n = _count(mech.get(field))
        if n:
            b.add(key, n, None)

    # 3900 / 5300 Tile (Jason, Nov 23 2026). Each bath / backsplash: prep line,
    # its flat tile allowance, and its Tile Installation line. Small areas:
    # Ceramic Tile Installation units and a per-SF material allowance by tier.
    ceramic_units, ceramic_parts = 0, []
    install_type = (b.template.item("tile_ceramic") or {}).get("cost_type")
    for area in scope.get("tile_areas") or []:
        kind = area.get("kind") or "small"
        if kind not in TILE_LABOR:
            kind = "small"
        prep = area.get("prep")
        label = area.get("name") or kind.replace("_", " ")
        if prep == "walk_in_shower":
            b.add("tile_prep_shower", 1, None, note=label)
        elif prep == "tub_surround":
            b.add("tile_prep_surround", 1, None, note=label)
        line_key = {"primary_bath": "tile_primary", "hall_bath_tub": "tile_hall",
                    "backsplash": "tile_backsplash"}.get(kind)
        if line_key:
            b.add(line_key, 1, TILE_ALLOWANCE[kind], unit="", allowance=True, cost_type=COST_TYPE_MATERIALS,
                  name=f"{T_NAMES[line_key]} - allowance",
                  description=f"Tile allowance for the {label.lower()}: tile and trim pieces. "
                              f"Installation is a separate line.",
                  note=f"{label}: ${TILE_ALLOWANCE[kind]:,} tile allowance")
            if kind == "hall_bath_tub":
                install_kind = "hall_walk_in" if prep == "walk_in_shower" else "hall_tub"
                what = ("walk-in shower" if prep == "walk_in_shower" else
                        "tub" if prep == "tub_surround" else "tub (no shower or tub given; priced as a tub)")
            else:
                install_kind, what = kind, ""
            amount = TILE_INSTALL[install_kind]
            b.add("tile_install", 1, amount, cost_type=install_type, name=f"Tile Installation - {label}",
                  description=f"Tile installation for the {label.lower()}, including setting materials.",
                  note=f"{label}{', ' + what if what else ''}: ${amount:,} installation")
            continue
        if CERAMIC_UNITS[kind]:
            ceramic_units += CERAMIC_UNITS[kind]
            ceramic_parts.append(f"{label} {CERAMIC_UNITS[kind]}")
        tier = _tier(area.get("tier"), top_tier)
        sf = _num(area.get("sf"))
        sf_note = f"{sf:g} SF" if sf > 0 else f"{TILE_SF_DEFAULT[kind]} SF (typical, no SF given)"
        sf = sf if sf > 0 else TILE_SF_DEFAULT[kind]
        rate = TILE_MATERIAL_PER_SF[tier]
        b.add("tile_materials", sf, rate, allowance=True,
              name=f"Tile Materials - {label} ({tier} allowance, ${rate:,.2f}/SF)",
              description=f"Tile material allowance for the {label.lower()}: ${rate:,.2f} per square foot. "
                          f"Includes tile and trim pieces.",
              note=f"{sf_note} x ${rate:,.2f}/SF ({tier})")
    if ceramic_units:
        b.add("tile_ceramic", ceramic_units, CERAMIC_TILE_UNIT, unit="Each", description=CERAMIC_DESCRIPTION,
              note=f"{ceramic_units} small-space units ({', '.join(ceramic_parts)}) x ${CERAMIC_TILE_UNIT:,}")

    # 4100-4800 / 6100 Exterior
    if _num(ext.get("roof_squares")) > 0:
        b.add("roofing", _num(ext["roof_squares"]), None)
    if ext.get("metal_roof_accents"):
        b.add("metal_roof", 1, placeholder="metal roof accents included by Tyler; price per job")
    if _num(ext.get("brick_facade_lf")) > 0:
        b.add("brick_facade", _num(ext["brick_facade_lf"]), None)
    if _num(ext.get("foundation_brick_lf")) > 0:
        b.add("foundation_brick", _num(ext["foundation_brick_lf"]), None)
    if ext.get("brick_chimney"):
        b.add("chimney", 1, None)
    windows = _count(ext.get("windows"))
    wood_match = bool(ext.get("original_wood_windows"))
    if windows:
        wt = "Luxury" if wood_match else _tier(ext.get("window_tier"), top_tier)
        b.add("windows", windows, WINDOW_PRICE[wt], allowance=True, unit="Each",
              name=f"Windows - {wt} allowance ({WINDOW_PRODUCT[wt]}, ${WINDOW_PRICE[wt]:,} each)",
              description=f"Window package, {WINDOW_PRODUCT[wt]}, to match the home unless the client "
                          f"chooses otherwise. Allowance is ${WINDOW_PRICE[wt]:,} per window; installation "
                          f"is a separate line.",
              note=f"{windows} window units, {wt} level" + (
                  " (original wood windows to match)" if wood_match else
                  "" if ext.get("window_tier") else " (job's highest tier)") + "; double counts as 2, triple 3, large or odd size 2")
        b.add("window_install", windows, WINDOW_INSTALL, unit="Each", description="Installation of windows.")
    if _count(ext.get("specialty_windows")):
        b.add("specialty_windows", _count(ext["specialty_windows"]),
              placeholder="specialty window (unusual size or shape); Tyler prices it in review")
    door_types = ext.get("door_types") or {}
    typed = {k: _count(door_types.get(k)) for k in EXT_DOOR_PRICE if _count(door_types.get(k))}
    if not typed and _count(ext.get("exterior_doors")):
        typed = {"single": _count(ext["exterior_doors"])}
    if ext.get("front_door") and not any(k in typed for k in FRONT_DOOR_TYPES):
        typed["sidelights"] = 1     # front door flagged with no type: fiberglass front door unit
    stained = bool(ext.get("wood_doors_stained"))
    door_tier = _tier(ext.get("window_tier"), top_tier)
    other_doors = 0
    for kind, n in typed.items():
        price = EXT_DOOR_PRICE[kind]
        label = EXT_DOOR_LABEL[kind]
        if isinstance(price, dict):
            price, label = price[door_tier], f"{label}, {door_tier} level"
        note = f"{n} x ${price:,} ({label})"
        if stained and kind in WOOD_DOOR_TYPES:
            price = round(price * (1 + WOOD_DOOR_STAIN_BUMP))
            label = f"{label}, stained"
            note += f" + {WOOD_DOOR_STAIN_BUMP:.0%} for staining = ${price:,} each"
        if kind in FRONT_DOOR_TYPES:
            b.add("front_door", n, price, name=f"Front Door - {label}", note=note)
            b.add("front_door_hw", n, None)
        else:
            b.add("ext_doors", n, price, name=f"Exterior Doors - {label}", note=note)
            other_doors += n
    if other_doors:
        b.add("ext_door_install", other_doors, None)
        b.add("ext_door_hw", other_doors, None)
    if _count(door_types.get("specialty")):
        b.add("specialty_doors", _count(door_types["specialty"]),
              placeholder="specialty exterior door; Tyler prices it in review")
    singles, doubles = _count(ext.get("garage_doors_single")), _count(ext.get("garage_doors_double"))
    if singles:
        b.add("garage_door", singles, GARAGE_DOOR_SINGLE, name="Garage door - single (incl. motor)")
    if doubles:
        b.add("garage_door", doubles, GARAGE_DOOR_DOUBLE, name="Garage door - double (incl. motor)")
    if _num(ext.get("siding_squares")) > 0:
        sq = _num(ext["siding_squares"])
        b.add("siding_materials", sq, None)
        b.add("siding_labor", sq, None)
    if _num(ext.get("trim_lf")) > 0:
        lf = _num(ext["trim_lf"])
        b.add("ext_trim_materials", lf * EXT_TRIM_MATERIALS_PER_LF, None,
              note=f"{lf:g} LF x {EXT_TRIM_MATERIALS_PER_LF} (Tyler's ballparks carry about 3 units of "
                   f"trim material per LF installed, e.g. Malm and Taggart 1,200 / 400)")
        b.add("ext_trim_labor", lf, None, unit="Linear Feet", note=f"{lf:g} LF of exterior trim")
    if _num(ext.get("paint_amount")) > 0:
        amount = _num(ext["paint_amount"])
        b.add("ext_paint", 1, amount, unit="Each",
              note=f"${amount:,.0f} exterior paint number given on site")
    elif ext.get("full_exterior_repaint"):
        # Tyler, Nov 23 2026: a full repaint is a yes/no plus the dollar figure
        # he enters; no heated-SF formula.
        b.add("ext_paint", 1, placeholder="full exterior repaint: enter the exterior paint figure")
    elif _num(ext.get("paint_units")) > 0:
        units = _num(ext["paint_units"])
        b.add("ext_paint", units, EXT_PAINT_UNIT, unit="Each",
              note=f"{units:g} units x ${EXT_PAINT_UNIT:,} (partial exterior paint)")
    if _num(ext.get("porch_screening_lf")) > 0:
        b.add("porch_screening", _num(ext["porch_screening_lf"]), PORCH_SCREENING_PER_LF, unit="Linear Feet")
    gutters = ext.get("gutters")
    if gutters:
        if not isinstance(gutters, bool) and _num(gutters) > 0:
            units, how = _num(gutters), "entered"
        else:
            shape = "whole_home" if scope.get("whole_home") else "partial"
            units = GUTTER_UNITS_DEFAULT[shape]
            how = "whole house" if shape == "whole_home" else "porch / addition / partial"
        b.add("gutters", units, GUTTER_UNIT, unit="Each",
              note=f"{units:g} units x ${GUTTER_UNIT:,} ({how})")
    if _num(ext.get("driveway_sf")) > 0:
        b.add("driveway", _num(ext["driveway_sf"]), None)
    if ext.get("landscaping"):
        b.add("landscaping", 1, None, note="landscaping mentioned on site")

    # 4500 Interior doors / shower doors
    n = _count(scope.get("interior_doors"))
    if n:
        b.add("int_doors", n, None)
        b.add("int_door_install", n, None)
        b.add("int_door_hw", n, None)
    if _count(scope.get("pocket_doors")):
        b.add("pocket_doors", _count(scope["pocket_doors"]), None)
    if _count(scope.get("glass_shower_doors")):
        b.add("shower_door", _count(scope["glass_shower_doors"]), None)

    # 4200 Fireplace
    if _count(scope.get("fireplaces")):
        b.add("fireplace", _count(scope["fireplaces"]), None)
    if _count(scope.get("fireplace_surrounds")):
        b.add("fireplace_surround", _count(scope["fireplace_surrounds"]), None)

    # 4700 / 5000 Interior finishes
    if framed_sf > 0 and scope.get("framing", True):
        b.add("insulation", framed_sf, INSULATION_PER_SF, note=f"{framed_sf:g} SF of framed/opened area")
    drywall_sf, how = drywall_area(scope)
    if drywall_sf > 0:
        b.add("drywall", drywall_sf, None, note=how)
        mat_sf = round(drywall_sf * DRYWALL_MATERIALS_RATIO)
        b.add("drywall_materials", mat_sf, None, note=f"{drywall_sf:g} hang SF x {DRYWALL_MATERIALS_RATIO}")
    if rooms:
        trim_sf = len(rooms) * INT_TRIM_MATERIALS_SF_PER_ROOM
        b.add("int_trim_materials", trim_sf, INT_TRIM_MATERIALS_PER_SF,
              note=f"{len(rooms)} rooms x {INT_TRIM_MATERIALS_SF_PER_ROOM} SF")
        days = len(rooms) * INT_TRIM_DAYS_PER_ROOM
        b.add("int_trim_labor", days, INT_TRIM_LABOR_PER_DAY,
              note=f"{len(rooms)} rooms x {INT_TRIM_DAYS_PER_ROOM} day x ${INT_TRIM_LABOR_PER_DAY}")
    elif floor_sf > 0:
        _days_line(b, scope, "int_trim_labor", "trim_days", INT_TRIM_LABOR_PER_DAY, "days")
    hw_new = _num(floors.get("hardwood_new_sf"))
    if hw_new > 0:
        b.add("hardwood", hw_new, FLOORING["hardwood"])
        b.add("hardwood_finishing", hw_new, FLOORING["hardwood_finishing"], note="new hardwood finished on site")
    if _num(floors.get("hardwood_refinish_sf")) > 0:
        b.add("hardwood_finishing", _num(floors["hardwood_refinish_sf"]), FLOORING["hardwood_finishing"],
              name="Hardwood Finishing - refinish existing")
    if _num(floors.get("lvp_sf")) > 0:
        b.add("lvp", _num(floors["lvp_sf"]), FLOORING["lvp"])
        b.add("lvp_install", _num(floors["lvp_sf"]), FLOORING["lvp_install"])
    if _num(floors.get("carpet_sf")) > 0:
        b.add("carpet", _num(floors["carpet_sf"]), FLOORING["carpet"])
    if _num(floors.get("tile_sf")) > 0:
        b.note("5000", f"{_num(floors['tile_sf']):g} SF of tile floor is priced in 5300 Tile, not here")
    regular = sum(1 for r in rooms if not r.get("tall_ceiling"))
    tall = sum(1 for r in rooms if r.get("tall_ceiling"))
    bump = 1 + WOOD_WINDOW_PAINT_BUMP if (wood_match and windows) else 1
    bump_note = f" + {WOOD_WINDOW_PAINT_BUMP:.0%} for painting wood window sash" if bump > 1 else ""
    if regular:
        b.add("int_paint", regular, INT_PAINT_ROOM * bump, unit="Rooms",
              note=f"{regular} rooms x ${INT_PAINT_ROOM:,}{bump_note}")
    if tall:
        b.add("int_paint", tall, INT_PAINT_TALL_ROOM * bump, unit="Rooms",
              name="Interior Paint - tall-ceiling rooms (18 ft+)",
              note=f"{tall} tall rooms x ${INT_PAINT_TALL_ROOM:,}{bump_note}")
    if rooms:
        n = 1 if len(rooms) < 20 else math.ceil(len(rooms) / 6)
        b.add("paint_materials", n, PAINT_MATERIALS_UNIT,
              note=f"{n} x ${PAINT_MATERIALS_UNIT} ({'1 per job' if n == 1 else '1 per 6 rooms on a 20+ room job'})")

    # 5400 Cabinetry and storage
    lowers_uppers = _num(cab.get("lowers_lf")) + _num(cab.get("uppers_lf"))
    full_height = _num(cab.get("full_height_lf"))
    vanities = _num(cab.get("vanities_lf"))
    builtins = _num(cab.get("builtins_lf"))
    builtins_low = _num(cab.get("builtins_low_lf"))
    if lowers_uppers:
        b.add("cabinets_kitchen", lowers_uppers, CABINET_PER_LF,
              note=f"{_num(cab.get('lowers_lf')):g} LF lowers + {_num(cab.get('uppers_lf')):g} LF uppers")
    if full_height:
        b.add("cabinets_kitchen", full_height, FULL_HEIGHT_PER_LF, name="Custom Cabinetry - full height")
    if vanities:
        b.add("vanities", vanities, CABINET_PER_LF)
    if builtins:
        b.add("builtins", builtins, BUILTINS_PER_LF, note=f"{builtins:g} LF full height x ${BUILTINS_PER_LF:,}")
    if builtins_low:
        b.add("builtins", builtins_low, BUILTINS_LOW_PER_LF,
              name="Custom Built Ins and Other Cabinetry - uppers and lowers",
              note=f"{builtins_low:g} LF uppers/lowers x ${BUILTINS_LOW_PER_LF:,}")
    cab_lf = lowers_uppers + full_height + vanities + builtins + builtins_low
    if cab_lf:
        # No Cabinet Installation / Cabinet Painting lines: Tyler used them on
        # 3 and 1 of ~10 cabinet jobs; his $625/LF reads as installed and finished.
        b.note("5400", f"{cab_lf:g} LF of custom cabinetry; per-LF price includes install and finish")
        if lowers_uppers or full_height:
            tier = _tier(next((r.get("tier") for r in rooms if r.get("type") == "kitchen"), None), top_tier)
            b.add("cabinet_hw", 1, CABINET_HW_KITCHEN[tier], allowance=True,
                  note=f"kitchen cabinetry, {tier} (${CABINET_HW_KITCHEN[tier]:,})")
        else:
            b.add("cabinet_hw", 1, CABINET_HW_NO_KITCHEN, note="no kitchen cabinetry")
    for top in scope.get("countertops") or []:
        sf = _num(top.get("sf"))
        if sf <= 0:
            continue
        tier = _tier(top.get("tier"))
        b.add("countertops", sf, COUNTERTOP_PER_SF[tier], allowance=True,
              name=f"Countertop Materials - {top.get('name') or 'countertops'} - {tier} allowance "
                   f"(${COUNTERTOP_PER_SF[tier]:g}/SF installed)",
              note=f"{sf:g} SF; tier price treated as installed (spec Open item default)")
    closets = scope.get("closets") or {}
    if _count(closets.get("primary")):
        b.add("closets", _count(closets["primary"]), PRIMARY_CLOSET, name="Closet Systems - primary closet",
              allowance=True)
    if _count(closets.get("small")):
        b.add("closets", _count(closets["small"]), SMALL_CLOSET, name="Closets - shelf and rod")

    # 5500 Appliances
    _fixture_lines(b, scope, "appliance", "appliances", tier_prices, "Appliances")
    if any(f.get("category") == "appliance" for f in scope.get("fixtures") or []):
        value, why = _estimate(scope, "appliance_install_hours")
        if value:
            b.add("appliance_install", value, FINISH_HOURLY, note=f"{value:g} hrs (tool estimate: {why})")
        else:
            b.add("appliance_install", 1, placeholder="no appliance installation hours estimate")

    # 6200 Completion: punch days entered on the form go in now; the rest of
    # the size-driven lines are set in _finish once the job's size is known.
    punch = _num(scope.get("punch_list_days"))
    if punch > 0:
        b.add("punch", punch, PUNCH_PER_DAY, note="entered on the tool page")
    _apply_stated_prices(b, scope)
    return _finish(b, scope, top_tier, punch_entered=punch > 0)


# Lines sized from the job total in _finish; a stated price can't replace them.
SIZED_KEYS = ("selection", "storage", "punch", "cleaning", "permits", "warranty", "preconstruction")
STATED_COST_TYPES = {"labor": COST_TYPE_LABOR, "materials": COST_TYPE_MATERIALS, "sub": COST_TYPE_SUB,
                     "other": COST_TYPE_OTHER}


def _apply_stated_prices(b, scope):
    """Dollar figures Tyler or Jason gave (notes box, video, comments) win over
    the tool's pricing (Jason, Nov 20 2026). Each entry is
    {"item", "lines": [template keys] or null, "amount", "phase", "cost_type", "source"}.
    With lines: every tool line under those keys becomes one line at the stated
    cost. Without: a new line in the given phase."""
    for sp in scope.get("stated_prices") or []:
        amount = _num(sp.get("amount"))
        item = (sp.get("item") or "").strip()
        source = (sp.get("source") or "given on site").strip()
        if amount <= 0 or not item:
            continue
        keys = sp.get("lines") or []
        if isinstance(keys, str):
            keys = [keys]
        keys = [k for k in keys if k in T or k in EXTRA_LINES]
        if any(k in SIZED_KEYS for k in keys):
            b.review.append(f"Stated price for {item} (${amount:,.0f}, {source}) not applied: that line is "
                            f"sized from the job total. Adjust it by hand if needed.")
            continue
        if keys:
            old = [l for l in b.lines if l["key"] in keys]
            old_cost = sum(l["cost"] for l in old)
            first = old[0] if old else None
            b.lines = [l for l in b.lines if l["key"] not in keys]
            placeholder_names = {l["name"] for l in old if l["placeholder"]}
            b.review = [r for r in b.review if not any(r.startswith(f"{n}: $0 placeholder") for n in placeholder_names)]
            line = b.add(keys[0], 1, amount, unit="Each", name=(first or {}).get("name"),
                         description=(first or {}).get("description"),
                         note=f"${amount:,.0f} stated price ({source}); replaces the tool's "
                              f"${old_cost:,.0f}" if old else f"${amount:,.0f} stated price ({source})")
        else:
            phase = str(sp.get("phase") or "").strip()
            if phase not in b.template.phase_by_number:
                b.review.append(f"Stated price for {item} (${amount:,.0f}, {source}) not added: no budget "
                                f"phase given for it. Add it by hand.")
                continue
            line = b.add("stated_price", 1, amount, name=item[:200], unit="Each", phase=phase, description="",
                         cost_type=STATED_COST_TYPES.get((sp.get("cost_type") or "").lower(), COST_TYPE_SUB),
                         note=f"${amount:,.0f} stated price ({source}); not a template line")
        if line:
            b.review.append(f"{line['name']}: ${amount:,.0f} stated price used ({source}).")


def _with_job_tier(scope):
    """A copy of scope where rooms, tile areas, countertops and fixtures with
    no tier of their own take the job tier (scope["tier"]), not Quality."""
    job_tier = scope.get("tier")
    if not job_tier:
        return scope
    scope = json.loads(json.dumps(scope))
    for key in ("rooms", "tile_areas", "countertops", "fixtures"):
        for x in scope.get(key) or []:
            if isinstance(x, dict) and not x.get("tier"):
                x["tier"] = job_tier
    return scope


def drywall_area(scope):
    """Drywall hang SF: Rendr wall + ceiling SF when imported, else Tyler's
    ~300 SF per renovated room (not every wall of a room gets new drywall).
    Returns (sf, how)."""
    if _num(scope.get("wall_ceiling_sf")) > 0:
        return _num(scope["wall_ceiling_sf"]), "wall + ceiling surface area from Rendr/entered"
    n = len(scope.get("rooms") or [])
    if n:
        return n * DRYWALL_SF_PER_ROOM, f"{n} rooms x {DRYWALL_SF_PER_ROOM} SF"
    return 0.0, ""


def framed_area(scope):
    """SF being framed or opened up (new walls, addition, opened walls) -
    what Tyler prices framing materials and insulation on, not the whole
    renovated floor plan. Returns (sf, how)."""
    if scope.get("framed_sf") is not None and _num(scope["framed_sf"]) >= 0:
        return _num(scope["framed_sf"]), "framed/opened area"
    addition_sf = _num(scope.get("addition_sf"))
    if addition_sf > 0:
        return addition_sf, "estimated as the addition SF"
    floor_sf = _num(scope.get("floor_plan_sf"))
    if floor_sf > 0:
        sf = round(floor_sf * 0.25)
        return sf, f"estimated as 25% of the {floor_sf:g} SF renovated area"
    return 0.0, ""


def plumbing_fixture_count(scope):
    """Plumbing fixtures for General Plumbing and finish plumbing hours: the
    count Claude/Tyler gave, else by room (primary bath 4, hall bath 3,
    powder 2, kitchen 2). Returns (n, how)."""
    given = _count((scope.get("plumbing") or {}).get("fixture_count"))
    if given:
        return given, "fixture count given"
    n, parts = 0, []
    for room in scope.get("rooms") or []:
        add = PLUMBING_FIXTURES_PER_ROOM.get(room.get("type"), 0)
        if add:
            n += add
            parts.append(f"{room.get('name') or room.get('type')} {add}")
    return n, "by room: " + ", ".join(parts) if n else ""


def is_single_space(scope):
    """One kitchen, one bath or one porch (Tyler's single-space job): not
    whole-home, no addition, and either exactly one kitchen / bath / powder
    room, or none and exterior work without siding or roofing (a porch or deck)."""
    if scope.get("whole_home") or _num(scope.get("addition_sf")) or _num(scope.get("foundation_sf")):
        return False
    spaces = sum(1 for r in scope.get("rooms") or [] if r.get("type") in ("kitchen", "primary_bath",
                                                                          "hall_bath", "powder"))
    if spaces == 1:
        return True
    ext = scope.get("exterior") or {}
    return (spaces == 0 and not _is_interior_only(scope)
            and not _num(ext.get("siding_squares")) and not _num(ext.get("roof_squares")))


def _is_interior_only(scope):
    if _num(scope.get("addition_sf")) or _num(scope.get("foundation_sf")) or _num(scope.get("deck_sf")):
        return False
    return not any(v for v in (scope.get("exterior") or {}).values())


def _sized_lines(scope, top_tier, job_cost, punch_entered):
    """The lines Tyler sizes by the job's total cost: selection hours,
    storage, punch days, cleaning, permits and warranty. job_cost is the
    whole job's cost (these lines included). Returns [(key, qty, unit_cost, note)]."""
    out = []
    hours = by_cost(SELECTION_BY_COST, job_cost)
    if hours is None:
        hours, why = SELECTION_HOURS[top_tier], f"{top_tier} (job's highest tier), job under $160K"
    elif hours == SELECTION_BY_COST[-1][1] and top_tier == "Luxury":
        hours, why = SELECTION_LUXURY_LARGE, "Luxury job over $550K"
    else:
        why = f"job cost ~${job_cost / 1000:,.0f}K"
    out.append(("selection", hours, None, f"{hours} hrs: {why}"))
    if job_cost >= NO_STORAGE_UNDER_COST:
        units = 2 if job_cost > TWO_STORAGE_UNITS_OVER_COST else 1
        out.append(("storage", units, STORAGE_UNIT, f"{units} unit(s) (job cost ~${job_cost / 1000:,.0f}K)"))
    if not punch_entered:
        days = by_cost(PUNCH_DAYS_BY_COST, job_cost)
        out.append(("punch", days, PUNCH_PER_DAY, f"{days} day(s) by job size (none entered)"))
    cleanings = 2 if job_cost > CLEANING_TWO_OVER_COST else 1
    out.append(("cleaning", cleanings, None, f"{cleanings} (2 on jobs over $390,625)"))
    if is_single_space(scope):
        permits, why = PERMITS_SINGLE_SPACE, "single-space job"
    elif _is_interior_only(scope) and job_cost < PERMITS_SMALL_INTERIOR[0]:
        permits, why = PERMITS_SMALL_INTERIOR[1], "small interior-only job"
    else:
        permits, why = by_cost(PERMIT_COUNTS, job_cost), f"job cost ~${job_cost / 1000:,.0f}K"
    out.append(("permits", permits, PERMIT_UNIT, f"{permits} x ${PERMIT_UNIT} ({why})"))
    return out


def _precon_line(b):
    """Preconstruction at $0 (Tyler, Nov 23 2026): the line is there so it can
    be priced by hand in JobTread when a job needs plans."""
    b.add("preconstruction", 1, 0, unit="Each", note="$0 - add by hand if this job needs plans or drawings")


def _finish(b, scope, top_tier, punch_entered=False):
    base = b.cost()
    template_cost = {"selection": (b.template.item("selection") or {}).get("unit_cost") or 125,
                     "cleaning": (b.template.item("cleaning") or {}).get("unit_cost") or 600}

    def sized_total(job_cost):
        lines = _sized_lines(scope, top_tier, job_cost, punch_entered)
        subtotal = base + sum(q * (u if u is not None else template_cost[k]) for k, q, u, _ in lines)
        warranty = max(WARRANTY_MIN, round(subtotal * WARRANTY_RATE, 2))
        return lines, subtotal, warranty

    # The sized lines depend on the total they are part of; settle it in a
    # few passes (it converges at once except right at a size boundary).
    job_cost = base
    for _ in range(5):
        lines, subtotal, warranty = sized_total(job_cost)
        if abs(subtotal + warranty - job_cost) < 1:
            break
        job_cost = subtotal + warranty
    for key, qty, unit_cost, note in lines:
        b.add(key, qty, unit_cost, note=note)
    b.add("warranty", 1, warranty,
          note=f"1% of ${subtotal:,.0f} cost before warranty, minimum ${WARRANTY_MIN:,}")

    # Preconstruction: a $0 line on every job, priced by hand (Tyler, Nov 23 2026).
    _precon_line(b)

    total_cost = b.cost()
    markup, margin = markup_bracket(total_cost)
    for line in b.lines:
        line["unit_price"] = round(line["unit_cost"] * (1 + markup), 2)
        line["price"] = round(line["quantity"] * line["unit_price"], 2)
    total_price = round(sum(l["price"] for l in b.lines), 2)

    order = {p["number"]: i for i, p in enumerate(b.template.phases)}
    phases = []
    for number in sorted({l["phase"] for l in b.lines}, key=lambda n: order.get(n, 99)):
        tp = b.template.phase_by_number.get(number) or {}
        lines = [l for l in b.lines if l["phase"] == number]
        phases.append({
            "number": number, "name": tp.get("name") or number, "cost_code": PHASE_COST_CODES.get(number, ""),
            "lines": lines, "notes": b.notes.get(number, []),
            "cost": round(sum(l["cost"] for l in lines), 2), "price": round(sum(l["price"] for l in lines), 2),
        })
    return {
        "weeks": duration_weeks(scope), "highest_tier": highest_tier(scope),
        "phases": phases, "review": b.review,
        "total_cost": total_cost, "total_price": total_price,
        "markup": markup, "margin": margin, "range": price_range(total_price),
        "inputs": scope_inputs(scope),
    }


def scope_inputs(scope):
    """The scope flags and sizes that drive pricing, for the dry-run line table."""
    ext = scope.get("exterior") or {}
    return {
        "whole_home": bool(scope.get("whole_home")),
        "full_exterior_repaint": bool(ext.get("full_exterior_repaint")),
        "gutters": ext.get("gutters"),
        "tier": highest_tier(scope),
        "duration_range": scope.get("duration_range") or "",
        "weeks": duration_weeks(scope),
        "punch_list_days": scope.get("punch_list_days"),
        "floor_plan_sf": scope.get("floor_plan_sf"),
        "heated_sf": scope.get("heated_sf"),
        "framed_sf": scope.get("framed_sf"),
        "addition_sf": scope.get("addition_sf"),
        "siding_squares": ext.get("siding_squares"),
        "trim_lf": ext.get("trim_lf"),
        "rooms": [{"name": r.get("name") or "", "type": r.get("type") or "", "tier": r.get("tier") or "",
                   "sf": r.get("sf")} for r in scope.get("rooms") or []],
    }
