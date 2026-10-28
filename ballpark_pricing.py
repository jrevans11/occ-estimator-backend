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
    "footing": "22PCTdp37bGz", "slab": "22PCTdp37bH2", "waterproofing": "22PCTdp37bH3",
    "crawlspace": "22PCTdp37bH4", "termite": "22PCTdp37bH5",
    "framing_labor": "22PCTdp37bH7", "deck_framing": "22PCTdp37bH8", "decking": "22PCTdp37bH9",
    "framing_materials": "22PCTdp37bHA",
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
    "demo": "Demolition", "temp_pole": "Temporary Power Pole", "water_tap": "New Water Tap and Meter",
    "rewa_fee": "Tap fee to be paid directly to ReWa", "sewer_tap": "Sewer Line Tap",
    "footing": "Footing and Foundation", "slab": "Pre Slab and Slab",
    "waterproofing": "Water Proofing - Foundation", "crawlspace": "Crawlspace Treatment",
    "termite": "Termite Protection", "framing_labor": "Framing Labor",
    "deck_framing": "Deck Framing Materials", "decking": "Decking Materials (Trex Enhanced Naturals)",
    "framing_materials": "Framing Materials",
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
EXTRA_LINES = {
    "tile_materials": {"name": "Tile Materials (allowance)", "phase": "5300", "cost_type": COST_TYPE_MATERIALS,
                       "unit": "Square Feet",
                       "description": "Tile material allowance for the tiled areas in scope. "
                                      "Final cost depends on the client's tile selections."},
    "mirrors": {"name": "Mirrors", "phase": "5400", "cost_type": COST_TYPE_MATERIALS, "unit": "Each",
                "description": "Mirror allowance."},
}

# ── Rates set by Tyler and Jason (spec section 6) ───────────────────────────

CM_PER_WEEK = 900
PORT_A_JOHN_PER_MONTH = 110
WEEKS_PER_MONTH = 4.33
DUMPSTER_PER_2_WEEKS = 500
DUST_PER_WEEK = 100
STORAGE_UNIT = 1650
TWO_STORAGE_UNITS_OVER_COST = 390625
SELECTION_HOURS = {"Quality": 12, "Premium": 20, "Luxury": 28}   # $1,500 / $2,500 / $3,500 at $125/hr
PERMIT_RATE = 0.008
TEMP_POLE = 1800
DEMO_PER_DAY = 300
CRAWLSPACE_TREATMENT = 4500
FRAMING_MATERIALS_PER_SF = 20
FRAMING_LABOR_PER_DAY = 300
BATH_HARDWARE = {"Quality": 300, "Premium": 850, "Luxury": 2200}
FINISH_HOURLY = 75
UNDER_CABINET_PER_KITCHEN = 875
GENERAL_ELECTRICAL_PER_SF = 9
HVAC_PER_TON = 5000
NEW_DUCTWORK = 7500
WINDOW_PRICE = {"Quality": 450, "Premium": 1100, "Luxury": 2100}
WINDOW_PRODUCT = {"Quality": "vinyl", "Premium": "Andersen 100 Series", "Luxury": "Andersen 400 Series"}
WINDOW_INSTALL = 400
GARAGE_DOOR_SINGLE = 3500
GARAGE_DOOR_DOUBLE = 7200
EXT_PAINT_PER_HEATED_SF = 8
PORCH_SCREENING_PER_LF = 140
GUTTERS_PLACEHOLDER = 2500
INSULATION_PER_SF = 4.50
INT_TRIM_MATERIALS_PER_SF = 3.25
INT_TRIM_LABOR_PER_DAY = 425
FLOORING = {"hardwood": 10.25, "hardwood_finishing": 3.25, "lvp": 6.50, "lvp_install": 2.00, "carpet": 4.25}
INT_PAINT_ROOM = 900
INT_PAINT_TALL_ROOM = 2000
TILE_LABOR = {"primary_bath": 6500, "hall_bath_tub": 3500, "backsplash": 1650, "small": 1200}
TILE_MATERIAL_PER_SF = {"Quality": 7.50, "Premium": 17.50, "Luxury": 50.00}
CABINET_PER_LF = 625
FULL_HEIGHT_PER_LF = 1250
BUILTINS_PER_LF = 1100
COUNTERTOP_PER_SF = {"Quality": 92.50, "Premium": 142.50, "Luxury": 237.50}
PRIMARY_CLOSET = 4500
SMALL_CLOSET = 350
MIRROR = 700
PUNCH_PER_DAY = 1800
WARRANTY_RATE = 0.01
WARRANTY_MIN = 1200

DURATION_RANGES = {   # tool-page dropdown -> midpoint weeks
    "10-15": 12.5, "15-20": 17.5, "20-25": 22.5, "25-40": 32.5, "40-52": 46.0,
}
DEFAULT_WEEKS_SINGLE_SPACE = 15
DEFAULT_WEEKS_WHOLE_HOME = 32.5

# Markup bracket by total job cost (spec section 7).
BRACKETS = [
    (390625, 0.28, 0.219),
    (609756, 0.25, 0.200),
    (None, 0.23, 0.187),
]

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


def markup_bracket(total_cost):
    for ceiling, markup, margin in BRACKETS:
        if ceiling is None or total_cost < ceiling:
            return markup, margin
    return BRACKETS[-1][1], BRACKETS[-1][2]


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


def _fixture_lines(b, scope, category, key, tier_prices, label):
    items = [f for f in scope.get("fixtures") or [] if f.get("category") == category]
    if not items:
        return
    by_tier = {}
    for f in items:
        by_tier.setdefault(_tier(f.get("tier")), []).append(f)
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
        if count:
            b.add(key, 1, total, name=f"{label} - {tier} allowance ({count} item{'s' if count != 1 else ''})",
                  allowance=True, note="; ".join(details))
        if unpriced:
            b.add(key, 1, name=f"{label} - {tier} (unpriced items)",
                  placeholder="no tier price for: " + ", ".join(unpriced))


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
    dumpsters = math.ceil(weeks / 2)
    b.add("dumpster", dumpsters, DUMPSTER_PER_2_WEEKS, note=f"{weeks:g} wks / 2 = {dumpsters} (rounded up)")
    b.add("dust", weeks, DUST_PER_WEEK, note=f"{weeks:g} weeks x ${DUST_PER_WEEK}/wk")
    hours = SELECTION_HOURS[top_tier]
    b.add("selection", hours, None, note=f"{top_tier} (job's highest tier) = {hours} hrs at the template rate")
    if mech.get("full_rewire"):
        b.add("temp_pole", 1, TEMP_POLE, note="full electrical rewire selected")
    _days_line(b, scope, "demo", "demo_days", DEMO_PER_DAY, "days")
    for key in ("water_tap", "rewa_fee", "sewer_tap"):
        if service.get(key):
            b.add(key, 1, None, note=f"new service called for: {service.get(key) if isinstance(service.get(key), str) else 'yes'}")

    # 2001 / 3100 Structure and foundation. Footings go under new ground-floor
    # footprint only: a second-story addition sits on the existing house.
    if scope.get("foundation_sf") is not None:
        foundation_sf, why = _num(scope["foundation_sf"]), "new foundation SF"
    else:
        foundation_sf, why = addition_sf, "addition SF (no separate foundation SF given)"
    if foundation_sf > 0:
        b.add("footing", foundation_sf, None, note=f"{foundation_sf:g} {why}")
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
    if floor_sf > 0 and scope.get("framing", True):
        b.add("framing_materials", floor_sf, FRAMING_MATERIALS_PER_SF, note=f"{floor_sf:g} floor plan SF")
        _days_line(b, scope, "framing_labor", "framing_days", FRAMING_LABOR_PER_DAY, "days")
    if _num(scope.get("deck_sf")) > 0:
        d = _num(scope["deck_sf"])
        b.add("deck_framing", d, None, note=f"{d:g} deck SF")
        b.add("decking", d, None, note=f"{d:g} deck SF")

    # 3600 / 5600 Plumbing
    for key, field in (("plumb_swap", "swap_same"), ("plumb_new_in", "new_in_footprint"),
                       ("plumb_tub_in", "tub_in_footprint"), ("plumb_new_out", "new_outside_footprint"),
                       ("plumb_tub_out", "tub_outside_footprint"), ("plumb_water_sewer", "water_sewer_10lf"),
                       ("plumb_cut_cap", "cut_and_cap"), ("plumb_vent", "vent_thru_roof")):
        n = _count(plumb.get(field))
        if n:
            b.add(key, n, None, note=plumb.get("source") or None)
    wh = (mech.get("water_heater") or "none").lower()
    if wh == "tank":
        b.add("wh_tank", 1, None)
    elif wh == "tankless":
        b.add("wh_tankless", 1, None)
    _fixture_lines(b, scope, "plumbing", "plumbing_fixtures", tier_prices, "Plumbing Fixtures")
    if any(f.get("category") == "plumbing" for f in scope.get("fixtures") or []):
        value, why = _estimate(scope, "finish_plumbing_hours")
        if value:
            b.add("finish_plumbing", value, FINISH_HOURLY, note=f"{value:g} hrs (tool estimate: {why})")
        else:
            b.add("finish_plumbing", 1, placeholder="no finish plumbing hours estimate")
    for room in rooms:
        kind = room.get("type")
        if kind in ("primary_bath", "hall_bath", "powder"):
            tier = _tier(room.get("tier"))
            amount = BATH_HARDWARE[tier] / (2 if kind == "powder" else 1)
            b.add("bath_hardware", 1, amount, allowance=True,
                  name=f"Bathroom Hardware - {room.get('name') or kind} - {tier} allowance",
                  description="Bath accessory set: 24 in. towel bar, towel ring, toilet paper holder, 2 robe hooks.",
                  note=f"{tier} set ${BATH_HARDWARE[tier]:,}" + (" / 2 for a powder room" if kind == "powder" else ""))

    # 3700 / 5700 Electrical, by rule per renovated room
    counts = {"receptacle": 0, "gfci": 0, "switch": 0, "dimmer": 0, "recessed": 0, "surface": 0}
    kitchens = 0
    for room in rooms:
        kind = room.get("type") or "other"
        lf, how = room_wall_lf(room)
        walls = _count(room.get("walls") or 4)
        receptacles = walls + math.ceil(lf / 6) if lf else walls
        if kind in ("kitchen", "primary_bath", "hall_bath", "powder"):
            counts["gfci"] += receptacles
        else:
            counts["receptacle"] += receptacles
        counts["switch"] += 2
        counts["dimmer"] += 1
        counts["recessed"] += 8 if kind == "kitchen" else 4
        counts["surface"] += 1
        kitchens += kind == "kitchen"
        b.note("3700", f"{room.get('name') or kind}: {walls} walls + {lf:g} LF / 6 = {receptacles} receptacles "
                       f"({how})")
    for key, n in counts.items():
        if n:
            b.add(key, n, None, note=f"{n} by the per-room rule")
    if kitchens:
        b.add("under_cabinet", kitchens, UNDER_CABINET_PER_KITCHEN, note=f"{kitchens} kitchen(s)")
    if mech.get("full_rewire") and floor_sf > 0:
        b.add("general_electrical", floor_sf, GENERAL_ELECTRICAL_PER_SF, note=f"full rewire, {floor_sf:g} floor plan SF")
    _fixture_lines(b, scope, "lighting", "electrical_fixtures", tier_prices, "Electrical Fixtures")
    if any(f.get("category") == "lighting" for f in scope.get("fixtures") or []) or rooms:
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

    # 3900 / 5300 Tile
    tile_material_sf = {}
    for area in scope.get("tile_areas") or []:
        kind = area.get("kind") or "small"
        prep = area.get("prep")
        label = area.get("name") or kind.replace("_", " ")
        if prep == "walk_in_shower":
            b.add("tile_prep_shower", 1, None, note=label)
        elif prep == "tub_surround":
            b.add("tile_prep_surround", 1, None, note=label)
        line_key = {"primary_bath": "tile_primary", "hall_bath_tub": "tile_hall",
                    "backsplash": "tile_backsplash"}.get(kind, "tile_ceramic")
        b.add(line_key, 1, TILE_LABOR.get(kind, TILE_LABOR["small"]), unit="",
              name=None if line_key != "tile_ceramic" else f"Ceramic Tile Installation - {label}",
              note=f"{label}: {kind.replace('_', ' ')} lump ${TILE_LABOR.get(kind, TILE_LABOR['small']):,}")
        sf = _num(area.get("material_sf"))
        tier = _tier(area.get("tier"))
        if sf > 0:
            tile_material_sf[tier] = tile_material_sf.get(tier, 0) + sf
        else:
            b.review.append(f"Tile area '{label}' has no material SF; no tile material priced for it.")
    for tier in TIERS:
        sf = tile_material_sf.get(tier)
        if sf:
            b.add("tile_materials", sf, TILE_MATERIAL_PER_SF[tier], allowance=True,
                  name=f"Tile Materials - {tier} allowance (${TILE_MATERIAL_PER_SF[tier]:g}/SF)",
                  note=f"{sf:g} SF at the {tier} rate")

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
    if windows:
        wt = _tier(ext.get("window_tier"), top_tier)
        b.add("windows", windows, WINDOW_PRICE[wt], allowance=True, unit="Each",
              name=f"Windows - {wt} allowance ({WINDOW_PRODUCT[wt]}, ${WINDOW_PRICE[wt]:,} each)",
              description=f"Window package, {WINDOW_PRODUCT[wt]}, to match the home unless the client "
                          f"chooses otherwise. Allowance is ${WINDOW_PRICE[wt]:,} per window; installation "
                          f"is a separate line.",
              note=f"{windows} windows, {wt} tier" + ("" if ext.get("window_tier") else " (job's highest tier)"))
        b.add("window_install", windows, WINDOW_INSTALL, unit="Each", description="Installation of windows.")
    ext_doors = _count(ext.get("exterior_doors"))
    if ext_doors:
        b.add("ext_doors", ext_doors, None)
        b.add("ext_door_install", ext_doors, None)
        b.add("ext_door_hw", ext_doors, None)
    if ext.get("front_door"):
        b.add("front_door", 1, None)
        b.add("front_door_hw", 1, None)
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
        b.add("ext_trim_materials", lf, None, unit="Linear Feet",
              note=f"{lf:g} LF entered; the template prices trim materials per SF, so LF is used as SF")
        b.add("ext_trim_labor", lf, None)
    if ext.get("full_exterior_repaint"):
        heated = _num(scope.get("heated_sf"))
        if heated > 0:
            b.add("ext_paint", heated, EXT_PAINT_PER_HEATED_SF, unit="Square Feet",
                  note=f"{heated:g} heated SF x ${EXT_PAINT_PER_HEATED_SF}")
        else:
            b.add("ext_paint", 1, placeholder="full exterior repaint selected but no heated SF entered")
    if _num(ext.get("porch_screening_lf")) > 0:
        b.add("porch_screening", _num(ext["porch_screening_lf"]), PORCH_SCREENING_PER_LF, unit="Linear Feet")
    if ext.get("gutters"):
        b.add("gutters", 1, GUTTERS_PLACEHOLDER, note="$2,500 placeholder per the spec; confirm with a gutter quote")
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
    if floor_sf > 0:
        b.add("insulation", floor_sf, INSULATION_PER_SF, note=f"{floor_sf:g} floor plan SF")
    drywall_sf, how = drywall_area(scope)
    if drywall_sf > 0:
        b.add("drywall", drywall_sf, None, note=how)
        b.add("drywall_materials", drywall_sf, None)
    if floor_sf > 0:
        b.add("int_trim_materials", floor_sf, INT_TRIM_MATERIALS_PER_SF, note=f"{floor_sf:g} floor plan SF")
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
    if regular:
        b.add("int_paint", regular, INT_PAINT_ROOM, unit="Rooms", note=f"{regular} rooms x ${INT_PAINT_ROOM} incl. materials")
    if tall:
        b.add("int_paint", tall, INT_PAINT_TALL_ROOM, unit="Rooms", name="Interior Paint - tall-ceiling rooms (18 ft+)",
              note=f"{tall} tall rooms x ${INT_PAINT_TALL_ROOM:,}")

    # 5400 Cabinetry and storage
    lowers_uppers = _num(cab.get("lowers_lf")) + _num(cab.get("uppers_lf"))
    full_height = _num(cab.get("full_height_lf"))
    vanities = _num(cab.get("vanities_lf"))
    builtins = _num(cab.get("builtins_lf"))
    if lowers_uppers:
        b.add("cabinets_kitchen", lowers_uppers, CABINET_PER_LF,
              note=f"{_num(cab.get('lowers_lf')):g} LF lowers + {_num(cab.get('uppers_lf')):g} LF uppers")
    if full_height:
        b.add("cabinets_kitchen", full_height, FULL_HEIGHT_PER_LF, name="Custom Cabinetry - full height")
    if vanities:
        b.add("vanities", vanities, CABINET_PER_LF)
    if builtins:
        b.add("builtins", builtins, BUILTINS_PER_LF, name="Custom Built Ins and Mudroom Lockers")
    cab_lf = lowers_uppers + full_height + vanities + builtins
    if cab_lf:
        b.add("cabinet_install", cab_lf, None, note=f"{cab_lf:g} LF of custom cabinetry")
        b.add("cabinet_paint", cab_lf, None, note=f"{cab_lf:g} LF of custom cabinetry")
        cab_rooms = sum(1 for r in rooms if r.get("type") in ("kitchen", "primary_bath", "hall_bath", "powder"))
        b.add("cabinet_hw", max(1, cab_rooms), None,
              note=f"1 per kitchen/bath with cabinetry ({max(1, cab_rooms)})")
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
    if _count(scope.get("mirrors")):
        b.add("mirrors", _count(scope["mirrors"]), MIRROR, allowance=True)

    # 5500 Appliances
    _fixture_lines(b, scope, "appliance", "appliances", tier_prices, "Appliances")
    if any(f.get("category") == "appliance" for f in scope.get("fixtures") or []):
        value, why = _estimate(scope, "appliance_install_hours")
        if value:
            b.add("appliance_install", value, FINISH_HOURLY, note=f"{value:g} hrs (tool estimate: {why})")
        else:
            b.add("appliance_install", 1, placeholder="no appliance installation hours estimate")

    # 6200 Completion
    punch = _num(scope.get("punch_list_days"))
    if punch > 0:
        b.add("punch", punch, PUNCH_PER_DAY)
    else:
        b.add("punch", 1, placeholder="no punch list days entered")
    b.add("cleaning", 1, None)

    # Percentage lines last: storage depends on the job's size, and permits
    # and warranty are a share of everything before them.
    pct_keys = ("storage", "permits", "warranty")
    return _finish(b, scope, pct_keys)


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
    """Wall + ceiling SF: Rendr when imported, else per room (perimeter x
    ceiling height + floor SF). Returns (sf, how)."""
    if _num(scope.get("wall_ceiling_sf")) > 0:
        return _num(scope["wall_ceiling_sf"]), "wall + ceiling surface area from Rendr/entered"
    total, parts = 0.0, []
    for room in scope.get("rooms") or []:
        sf = _num(room.get("sf"))
        lf, _how = room_wall_lf(room)
        if not sf or not lf:
            continue
        height = TALL_CEILING_FT if room.get("tall_ceiling") else _num(room.get("ceiling_ft"), DEFAULT_CEILING_FT)
        area = lf * height + sf
        total += area
        parts.append(f"{room.get('name') or room.get('type')}: {lf:g} LF x {height:g} ft + {sf:g} SF")
    if total:
        return round(total), "estimated per room: " + "; ".join(parts)
    floor_sf = _num(scope.get("floor_plan_sf"))
    if floor_sf:
        return round(floor_sf * 3.6), f"no room sizes: estimated as 3.6 x {floor_sf:g} floor plan SF"
    return 0.0, ""


def _finish(b, scope, pct_keys):
    base = b.cost(exclude=pct_keys)

    def with_storage(units):
        subtotal = base + units * STORAGE_UNIT
        permits = round(subtotal * PERMIT_RATE, 2)
        warranty = max(WARRANTY_MIN, round(subtotal * WARRANTY_RATE, 2))
        return subtotal, permits, warranty

    units = 1
    subtotal, permits, warranty = with_storage(1)
    if subtotal + permits + warranty > TWO_STORAGE_UNITS_OVER_COST:
        units = 2
        subtotal, permits, warranty = with_storage(2)
    base_with_storage = subtotal
    b.add("storage", units, STORAGE_UNIT,
          note=f"{units} unit{'s' if units > 1 else ''}" + (" (job cost over $390,625)" if units == 2 else ""))
    b.add("permits", 1, permits, note=f"0.8% of ${base_with_storage:,.0f} cost before permits and warranty")
    b.add("warranty", 1, warranty,
          note=f"1% of ${base_with_storage:,.0f} cost before permits and warranty, minimum ${WARRANTY_MIN:,}")

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
    }
