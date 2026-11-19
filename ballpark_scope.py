"""Preliminary Ballpark Tool: turn the site-visit material into a priced-ready scope.

One Claude call reads the gathered context (ballpark_inputs.gather) and
Tyler's tool-page entries, and returns:
  - scope: the plain dict ballpark_pricing.price_scope() expects, with a
    source for every number;
  - the days/hours estimates the spec leaves open (demo, framing, trim,
    finish plumbing/electrical, appliance install), each with its reasoning;
  - the written-summary text (Project Overview, Further Items, Exclusions,
    tier language) in the style of the 00844 Malm Preliminary Ballpark.

Tyler's tool-page entries always win over what Claude reads (merge_form).
Claude may use web search only to price an appliance, fixture or light
whose specific brand was named on site and isn't in the tier table.
"""

import json
import time

import ballpark_pricing as bp
import scope_quickpass as qp

MAX_TOKENS = 32000
WEB_SEARCH_MAX_USES = 8

SCOPE_SCHEMA = """{
  "scope": {
    "whole_home": true,                      // whole-home renovation or addition
    "punch_list_days": 5,
    "floor_plan_sf": 2200,                   // renovated area
    "addition_sf": 0, "foundation_sf": 0, "foundation_perimeter_lf": 0, "framed_sf": 0,
    "slab_sf": 0, "deck_sf": 0, "heated_sf": 2800, "waterproofing_lf": 0,
    "wall_ceiling_sf": null,                 // only from Rendr; otherwise leave null
    "rooms": [                               // every renovated room (paint, devices, drywall)
      {"name": "Kitchen", "type": "kitchen|primary_bath|hall_bath|powder|other",
       "tier": "Quality|Premium|Luxury", "tall_ceiling": false, "sf": 220, "perimeter_lf": null, "walls": 4}
    ],
    "exterior": {"roof_squares": 0, "siding_squares": 0, "trim_lf": 0, "brick_facade_lf": 0,
                 "foundation_brick_lf": 0, "windows": 0, "window_tier": null, "exterior_doors": 0,
                 "front_door": false, "gutters": 0, "driveway_sf": 0, "garage_doors_single": 0,
                 "garage_doors_double": 0, "porch_screening_lf": 0, "full_exterior_repaint": false,
                 "paint_units": 0, "paint_amount": null, "original_wood_windows": false,
                 "specialty_windows": 0,
                 "door_types": {"single": 0, "sidelights": 0, "french": 0, "sliding": 0, "specialty": 0},
                 "brick_chimney": false, "landscaping": false, "metal_roof_accents": false},
    "flooring": {"tile_sf": 0, "hardwood_new_sf": 0, "hardwood_refinish_sf": 0, "lvp_sf": 0, "carpet_sf": 0},
    "tile_areas": [ {"name": "Primary bath", "kind": "primary_bath|hall_bath_tub|backsplash|small",
                     "tier": "Premium", "prep": "walk_in_shower|tub_surround|null"} ],
    "cabinetry": {"lowers_lf": 0, "uppers_lf": 0, "full_height_lf": 0, "vanities_lf": 0, "builtins_lf": 0,
                  "builtins_low_lf": 0},
    "countertops": [ {"name": "Kitchen", "sf": 60, "tier": "Premium"} ],
    "closets": {"primary": 0, "small": 0},
    "mirrors": 0, "interior_doors": 0, "pocket_doors": 0, "glass_shower_doors": 0,
    "fireplaces": 0, "fireplace_surrounds": 0, "framing": true,
    "mechanical": {"full_rewire": false, "hvac_tons": 0, "new_ductwork": false, "supply_relocations": 0,
                   "return_relocations": 0, "finish_hvac": 0, "water_heater": "none|tank|tankless",
                   "crawlspace": false},
    "plumbing": {"fixture_count": 0, "swap_same": 0, "new_in_footprint": 0, "tub_in_footprint": 0, "new_outside_footprint": 0,
                 "tub_outside_footprint": 0, "water_sewer_10lf": 0, "cut_and_cap": 0, "vent_thru_roof": 0},
    "new_service": {"water_tap": false, "rewa_fee": false, "sewer_tap": false},
    "site": {"site_work_units": null, "grading_units": null, "engineering": "none|letter|full",
             "preconstruction": false, "survey": false},
    "fixtures": [ {"category": "appliance|plumbing|lighting", "item": "refrigerator", "tier": "Premium",
                   "quantity": 1, "brand": null, "unit_price": null, "price_source": null, "room": "Kitchen"} ],
    "estimates": {
      "demo_days": {"value": 6, "reasoning": "..."},
      "framing_days": {"value": 10, "reasoning": "..."},
      "appliance_install_hours": {"value": 6, "reasoning": "..."}
    }
  },
  "sources": {"floor_plan_sf": "tool page", "exterior.windows": "scope summary video 0:42", "...": "..."},
  "conflicts": ["Walkthrough said 2 baths; scope summary says 3. Used 3 (scope summary wins)."],
  "open_questions": ["..."],
  "confidence": "low|medium|high",
  "summary": {
    "project_overview": "Markdown paragraphs. See the rules.",
    "further_items": ["Projected permitting and contractor fees", "..."],
    "exclusions": ["landscaping", "..."],
    "tier_language": ["Plumbing fixtures: Premium allowance - Brizo, California Faucets or Hansgrohe", "..."]
  }
}"""

SYSTEM_PROMPT = """You are the estimator for Owners Choice Construction (OCC), a design-build renovation \
contractor in Greenville, SC. After a site visit on a Renovation or Addition job, you read everything \
captured on the visit and write down the scope as quantities, so a pricing engine can build a \
Preliminary Ballpark budget. You do NOT price anything except brand-named fixtures (see below); the \
engine applies OCC's unit rates.

WHAT YOU READ (most trusted first):
1. Tyler's tool-page entries. Every value he entered is final: copy it exactly into the scope. A field \
he left blank is NOT "none": work it out from the other material below. His spaces list and room count \
are what he entered quickly on site, not a complete list: if the visit material clearly shows another \
space or area of work (a powder room, a detached garage, a breezeway, a driveway, a porch), include it \
in the scope and say so in conflicts. duration_range is in WEEKS (e.g. "20-25" = 20 to 25 weeks).
2. Tyler's scope summary video (recorded at the end of each site visit; marked likely_scope_summary). \
When anything conflicts, the scope summary and meeting notes win over earlier walkthrough material.
3. Job comments, Daily Logs and CompanyCam Pages (intake and meeting notes).
4. Walkthrough video transcripts and photo descriptions.
5. Rendr measurements (job parameters) when present.
Transcripts are machine-generated; read through mis-hearings.

RULES FOR THE SCOPE:
- Only include work that was actually discussed as in scope. Never invent scope to fill fields. Things \
discussed as optional or excluded go in summary.exclusions, not the scope.
- When work IS in scope but nobody gave a quantity, ESTIMATE the quantity; never leave it at 0. Use room \
sizes, the home's heated SF, photos and typical sizes, write "estimated from ..." in sources, and add the \
assumption to open_questions so Tyler can check it. Examples: new flooring in renovated rooms (sum the \
rooms' SF), window replacement (count from photos or about 1 per 100 heated SF), new siding (exterior \
trim LF too), built-ins mentioned (estimate their LF). Use 0, false or empty lists only for work that is \
not in scope.
- rooms: every room being renovated (bedrooms, closets used as rooms, hallways and living spaces count \
as rooms when they are renovated). Kitchens, primary baths, hall baths and powder rooms use their type; \
everything else is "other". Give each room's tier: use Tyler's tier for kitchens and baths, and the \
job's general tier for other rooms. A partial room still counts as a room. tall_ceiling is for 18 ft+ \
rooms such as a two-story foyer. Give sf when you can estimate it; perimeter_lf only from Rendr.
- addition_sf is all new heated SF. foundation_sf is only the new ground-floor footprint that needs \
footings: 0 for a second-story addition built on the existing house; for a two-story addition, its \
footprint, not both floors. foundation_perimeter_lf: the LF of new footing (usually 3 sides of an \
addition; the 4th is the existing house) when you can estimate it. framed_sf: FLOOR AREA (SF) of \
the space being framed or reframed, NOT wall surface area and NOT the whole renovated area; framing \
materials and insulation are priced on it. Use the addition's (or new garage's) floor SF, plus \
about 100-400 SF for interior walls moved or opened in the existing house. Tyler's budgets: 200 SF \
for a bath or kitchen remodel with a wall or two moved, 400 SF for a larger interior reconfiguration, \
about 1,000 SF for a 750 SF addition plus small reframes. Never add up wall heights times lengths.
- Exterior paint: full_exterior_repaint = true when the whole house is being painted (new siding \
usually means a full repaint). Otherwise, when only part of the exterior is painted (an addition, a \
porch, new trim, touch-up to match), set exterior.paint_units: Tyler's $1,200 units, about 1-3 for a \
porch or small area, 3-8 for an addition, 10-20 for most of the house. Exterior paint discussed on \
site is in scope unless it was explicitly excluded. When Jason or Tyler states a dollar number for the \
exterior paint ("painting the whole house is about $19,000"), put it in exterior.paint_amount; it wins \
over the units and the full-repaint rule.
- Windows: exterior.windows is the number of window UNITS. A large unit (an old slider, a big picture \
window, a mulled pair) counts as 2. window_tier is the window level: Quality (vinyl, about $600), \
Premium (Andersen 100 Series or similar, about $1,200), Luxury (Andersen 400 Series or similar, about \
$2,500). Set it from what was said on site; leave it null to use the job's tier. \
original_wood_windows = true when new windows must match the home's original wood windows (priced at \
Luxury, and interior paint goes up for the sash). specialty_windows: count of odd units (unusual \
size or shape, a pass-through) that Tyler prices by hand.
- Exterior doors: count each by type in exterior.door_types: single (a plain single door unit, about \
$900), sidelights (a door unit with sidelights and/or a transom, about $1,150), french, sliding, and \
specialty (custom or unusual units Tyler prices by hand). exterior_doors is the total. front_door \
stays true only for an upgraded front entry door.
- cabinetry.builtins_lf: LF of FULL-HEIGHT custom built-ins ($1,100/LF); cabinetry.builtins_low_lf: LF \
of built-in uppers and lowers or base-only built-ins ($625/LF). Measure from the plans when they are \
available. Built-ins means custom built-ins and other cabinetry outside the kitchen and vanities \
(bookcases, mudroom lockers, window seats, bar or pantry cabinetry, entertainment walls, laundry \
cabinets). Estimate the LF whenever any are mentioned (a mudroom bench and lockers about 6-8 LF, a pair \
of bookcases flanking a fireplace about 8-12 LF, a pantry or bar wall about 10-15 LF).
- site: work on the lot itself. site_work_units ($800 each: clearing, access, tree removal, utility \
trenching) and grading_units ($600 each) for any new structure or flatwork (addition, porch, deck, \
slab, garage, driveway): Tyler's run 1 site + 1 grading for a porch or deck, about 2 + 2-6 for an \
addition, up to 5-6 + 8 for a big addition on a sloped lot. Leave them null to let the engine use 1 \
each when there's ground work; 0 when there is truly none. engineering: "letter" when a load-bearing \
wall or beam is changed, "full" for an addition or major structural change, else "none". \
preconstruction: true when the job needs plans or drawings before construction (additions, porches, \
new structures, major layout changes); the engine prices it as a share of the contract. survey: true \
ONLY when someone says a lot survey is needed (clients often already have one); a mention of \
setbacks or property lines alone goes in open_questions, not survey. Grading units go up with the \
slope of the lot.
- mechanical.full_rewire: true only when a full rewire was called for on site (for example "we need \
a full rewire"). Plaster coming off is not by itself a rewire; put it in open_questions.
- gutters: number of gutter units (about 1-2 for a porch or addition, 5-7 for a whole house), 0 if \
no gutter work.
- plumbing.fixture_count: every plumbing fixture being installed (toilets, sinks, showers, tubs, \
dishwasher, laundry, tub filler). Leave 0 to let the engine count by room.
- A typical bathroom has 4 plumbing fixtures (toilet, sink, shower, tub or second sink). Classify each \
fixture's plumbing work: swap_same (same location), new_in_footprint (moved within the existing house), \
tub_in_footprint, new_outside_footprint / tub_outside_footprint (in an addition; also count \
water_sewer_10lf runs). cut_and_cap is one per fixture removed before demo.
- tile_areas: one per tiled space. kind = primary_bath (large primary bath), hall_bath_tub (hall bath \
with a tile tub surround), backsplash (kitchen backsplash), small (bath floor only, shower only, \
laundry, etc.). prep = walk_in_shower or tub_surround when that space has one.
- fixtures: list each appliance, plumbing fixture and light fixture named or clearly implied by the \
scope (a remodeled kitchen implies refrigerator, range or cooktop + oven, dishwasher, microwave, hood; \
a bath implies toilet, sink(s), faucet(s), shower valve/trim, tub filler if a tub). Use the item names \
from the TIER TABLE ITEMS list below whenever one fits. No package lumps.
- Brand-named items: if a specific brand was mentioned on site (e.g. "Sub-Zero fridge", "Kohler toilet"), \
set brand. If that brand+item is NOT one of the tier brands, you may use web search to find its typical \
normal retail price (not sale, not pro pricing) and set unit_price and price_source (site + URL). If \
the brand is one of the tier's own brands, leave unit_price null; the tier table covers it. Use web \
search for nothing else.
- estimates: OCC has no fixed rule yet for these, so estimate them from the scope and explain the \
math in one sentence each: demo_days (MAN-days, one worker for one day at $300; a crew of 3-4 \
for a week is 15-20 man-days. Tyler's ballparks run 3-5 for a porch or a single small room, 8-16 for a \
kitchen, a bath conversion or a small addition, about 14-16 for an addition with interior work, about \
40 for a whole-home renovation and 80 for the largest), framing_days (framing crew \
days at $450/day; Tyler's ballparks run 5-12 days small, 16-30 medium, 36-40 for additions), \
appliance_install_hours ($75/hr, about 16 hrs per kitchen). Trim days and finish plumbing/electrical \
hours come from the engine's per-room rules. Omit an estimate whose work isn't in scope.
- sources: for every number you set, say where it came from ("tool page", "scope summary video", \
"walkthrough video 3", "comment Oct 6", "estimated from ...").
- conflicts: every place two inputs disagreed, and which one you used.
- missing: anything you could not determine goes in open_questions.

RULES FOR THE WRITTEN SUMMARY (client-facing; Tyler pastes it into the ballpark document):
- project_overview: 3-6 short paragraphs in the style of the MALM EXAMPLE below. Each paragraph starts \
with an italic lead-in in single asterisks (e.g. "*Exterior improvements* will include ..."). Plain, \
confident, specific about the work.
- NEVER give approximate square footage, linear footage, counts of windows, or rough dimensions \
("approx 481 SF", "~1300 sq/ft", "approximately 25 windows"). Describe extent qualitatively ("a \
second-story addition", "replacement of the home's windows"). Product specs and allowance rates are fine.
- further_items: start from the MALM EXAMPLE list and edit it for this job.
- exclusions: start from the MALM EXAMPLE list and edit it for this job: only items that were discussed \
or that a client would reasonably assume are included. Lowercase, short.
- tier_language: one line per category present in this job (plumbing fixtures, appliances, lighting, \
countertops, tile, windows) naming the tier and its brands from the BRAND TABLE.
- No em dashes; use a spaced hyphen. Say "the client", never "you". No emoji.

BRAND TABLE (Tyler, Oct 2026):
Plumbing fixtures - Quality: Delta, Moen, Kohler | Premium: Brizo, California Faucets, Hansgrohe | \
Luxury: Waterworks, Newport Brass, Kallista, THG Paris
Appliances - Quality: Bosch, GE Profile, KitchenAid | Premium: Cafe, JennAir, Thermador | Luxury: \
Sub-Zero, Wolf, Miele, Gaggenau
Lighting - Quality: Kichler, Hinkley, Progress Lighting | Premium: Visual Comfort Studio, Hudson Valley \
Lighting, Hubbardton Forge | Luxury: Visual Comfort Signature, Urban Electric Co., Apparatus
Countertops - Quality: quartz (MSI Q Premium, Silestone), select granite | Premium: Cambria, premium \
quartzite, select marble, premium granite | Luxury: exotic quartzite, premium marble, high-end natural \
stone, specialty porcelain
Tile - Quality: Daltile, Marazzi, Emser, Florida Tile | Premium: Bedrosians, Zia Tile | Luxury: Ann \
Sacks, Artistic Tile, Fireclay Tile, New Ravenna
Windows - Quality: vinyl | Premium: Andersen 100 Series | Luxury: Andersen 400 Series

MALM EXAMPLE (job 00844; style reference only - its numbers break the no-approximate-SF rule):
{malm}

OUTPUT: one JSON object exactly in this shape, and nothing after it:
{schema}"""

MALM_EXAMPLE = """## Project Overview
*The proposed renovation includes a comprehensive whole-home remodel and construction of a new \
second-story addition*, significantly expanding the home's living space and improving its overall \
functionality.

*Exterior improvements* will include new roofing, siding and trim modifications, replacement of the \
home's windows with Andersen 100 Series windows, new exterior doors, and an upgraded front entry. ...

*Major mechanical improvements* will include a complete electrical rewire of the home ...

*Interior renovations* will include a fully remodeled and expanded kitchen featuring custom cabinetry, \
countertops, tile backsplash, and a new appliance package. ...

*Interior finishing work* will include new insulation, drywall installation, hardwood flooring \
modifications, and refinishing of the home's existing hardwood floors. ...

*The project will conclude* with final plumbing and electrical fixture installations, quality-control \
inspections, punch list completion, and professional cleaning in preparation for client handoff.

### Further items included:
- Projected permitting and contractor fees
- Projected costs for selection coordinate by our in house design team
- Projected costs for project management
- Portojohn for the duration of the project
- Dumpster fees for the duration of the project
- Floor protectant and dust control for the duration of the project

### Exclusions:
- landscaping
- first floor HVAC system upgrade
- decorative trim/wallpaper
- relocated fireplace (existing will be demoed and capped on first story)"""


class ScopeError(Exception):
    pass


def system_prompt():
    return (SYSTEM_PROMPT.replace("{malm}", MALM_EXAMPLE).replace("{schema}", SCOPE_SCHEMA))


def tier_table_items(tier_prices):
    rows = []
    for category, items in sorted((tier_prices or {}).items()):
        if isinstance(items, dict) and not category.startswith("_"):
            rows.append(f"{category}: " + ", ".join(sorted(k for k in items if not k.startswith("_"))))
    return "\n".join(rows) or "(tier table is empty)"


def build_user_content(context, form, tier_prices):
    """The visit material as one text block, in trust order."""
    parts = []
    job = context.get("job") or {}
    parts.append(f"JOB {job.get('number')} - {job.get('name')} - {job.get('address')} ({job.get('job_type')})")
    shown = dict(form or {})
    if shown.get("duration_range") and "week" not in str(shown["duration_range"]).lower():
        shown["duration_range"] = f"{shown['duration_range']} weeks"
    parts.append("TOOL-PAGE ENTRIES (copy every entered value exactly; blank fields are for you to work "
                 "out, not 'none'):\n" + json.dumps(shown, indent=1))
    camera = context.get("companycam") or {}
    summaries = [t for t in camera.get("transcripts") or [] if t.get("likely_scope_summary")]
    others = [t for t in camera.get("transcripts") or [] if not t.get("likely_scope_summary")]
    for i, t in enumerate(summaries, 1):
        parts.append(f"SCOPE SUMMARY VIDEO {i} (recorded {t.get('captured_at')}):\n{t['text']}")
    for label, items in (("JOB COMMENT", context.get("comments")), ("DAILY LOG", context.get("daily_logs"))):
        for c in items or []:
            parts.append(f"{label} ({c.get('at') or c.get('date')}):\n{c.get('text')}")
    for pg in camera.get("pages") or []:
        parts.append(f"COMPANYCAM PAGE '{pg['title']}':\n{pg['text']}")
    for i, t in enumerate(others, 1):
        parts.append(f"WALKTHROUGH VIDEO {i} (recorded {t.get('captured_at')}):\n{t['text']}")
    photos = [p for p in camera.get("photos") or [] if p.get("description") or p.get("tags")]
    if photos:
        parts.append("PHOTO DESCRIPTIONS AND TAGS:\n" + "\n".join(
            f"- {p.get('description') or ''} {('[' + ', '.join(p['tags']) + ']') if p.get('tags') else ''}".strip()
            for p in photos))
    if context.get("rendr"):
        parts.append("RENDR MEASUREMENTS (job parameters):\n" + json.dumps(context["rendr"])[:20000])
    missing = camera.get("missing_transcripts") or []
    if missing:
        parts.append(f"NOTE: {len(missing)} video(s) had no transcript and could not be read.")
    parts.append("TIER TABLE ITEMS (use these item names for fixtures):\n" + tier_table_items(tier_prices))
    parts.append("Write the JSON object now.")
    return "\n\n".join(parts)


def parse_reply(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ScopeError("Claude's reply had no JSON object.")
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError as e:
        raise ScopeError(f"Claude's reply was not valid JSON: {e}")
    if not isinstance(obj.get("scope"), dict):
        raise ScopeError("Claude's reply has no scope object.")
    obj.setdefault("summary", {})
    for key in ("sources", "conflicts", "open_questions"):
        obj.setdefault(key, {} if key == "sources" else [])
    return obj


def call_claude(user_text, attempts=3):
    body = {"model": qp.model(), "max_tokens": MAX_TOKENS, "stream": True,
            "system": system_prompt(), "messages": [{"role": "user", "content": user_text}],
            "output_config": {"effort": qp.effort()},
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}]}
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return qp._post_stream(body, [])
        except qp.ClaudeError as e:
            last = e
            if not e.retryable or attempt == attempts:
                break
            time.sleep(10 * attempt)
    raise ScopeError(f"Claude call failed: {last}")


# ── Tool-page entries win ───────────────────────────────────────────────────

def _blank(v):
    return v is None or v == "" or v == [] or v == {}


def _merge(base, over):
    for k, v in (over or {}).items():
        if _blank(v):
            continue
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def merge_form(scope, form):
    """Tyler's entries override Claude's. Spaces (kitchens/baths with tiers)
    set the tier on matching rooms; room_count pads or trims "other" rooms."""
    form = dict(form or {})
    spaces = form.pop("spaces", None) or []
    room_count = form.pop("room_count", None)
    for k in ("job", "notes", "rendr_scope_note"):
        form.pop(k, None)
    scope = dict(scope or {})
    # Weeks come only from Tyler's dropdown or the spec default (15 / 32.5).
    scope.pop("duration_weeks", None)
    scope.pop("duration_range", None)
    scope = _merge(scope, form)
    rooms = scope.setdefault("rooms", [])

    used = set()
    for sp in spaces:
        kind = sp.get("type") or "other"
        match = next((i for i, r in enumerate(rooms) if i not in used and r.get("type") == kind), None)
        if match is None:
            rooms.append({"name": sp.get("name") or kind.replace("_", " ").title(), "type": kind})
            match = len(rooms) - 1
        used.add(match)
        if sp.get("tier"):
            rooms[match]["tier"] = sp["tier"]
        if sp.get("tall_ceiling") is not None:
            rooms[match]["tall_ceiling"] = bool(sp["tall_ceiling"])

    # The job tier (required on the page) covers every room without its own
    # space tier; with only space tiers, the highest one does.
    job_tier = form.get("tier") or (bp.highest_tier({"rooms": [{"tier": s.get("tier")} for s in spaces
                                                                if s.get("tier")]}) if spaces else None)
    if job_tier:
        scope["tier"] = job_tier
    for i, r in enumerate(rooms):
        if i in used:
            continue
        # Tyler's job tier beats Claude's guess; otherwise only fill blanks.
        if form.get("tier") or (job_tier and not r.get("tier")):
            r["tier"] = job_tier

    n = bp._count(room_count)
    if n:
        while len(rooms) < n:
            rooms.append({"name": f"Room {len(rooms) + 1}", "type": "other", "tier": job_tier})
        while len(rooms) > n:
            extra = next((i for i in range(len(rooms) - 1, -1, -1)
                          if rooms[i].get("type") == "other" and i not in used), None)
            if extra is None:
                break
            rooms.pop(extra)
    return scope


def extract(context, form, tier_prices):
    """Run the Claude call; returns the parsed reply with form entries applied."""
    text, stop, usage, served = call_claude(build_user_content(context, form, tier_prices))
    if stop == "max_tokens":
        raise ScopeError("Claude's reply was cut off (max tokens).")
    if stop == "pause_turn":
        raise ScopeError("Claude paused mid-way through its web searches; start the run again.")
    reply = parse_reply(text)
    reply["scope"] = merge_form(reply["scope"], form)
    reply["usage"] = usage
    reply["model"] = served
    try:
        reply["cost_usd"] = qp.run_cost(usage, served)
    except Exception:
        reply["cost_usd"] = None
    return reply
