"""Preliminary Ballpark Tool: the guided intake's question catalog.

The guided intake reads the site visit first (ballpark_read), then shows
Tyler one page per area of work, pre-filled from the read. This file turns
the read's scope into those pages and turns his answers back into the scope
that gets priced. No Claude call happens here.

Every question is a dot path into the scope ("exterior.trim_lf",
"rooms.2.tier", "estimates.demo_days.value"). Each one lands in one of three
groups on its page:

  ask    - "Needs your answer" (yellow): every tool estimate, every conflict,
           every judgment call, and the core fields the notes don't cover.
  found  - "From your notes" (collapsed): values someone stated, with source.
  more   - "Not mentioned" (collapsed): optional fields nobody brought up.

Pages only appear for work the read found: a bath job never sees the
exterior page. Design: docs/ballpark/GUIDED_INTAKE_DESIGN.md
"""

import copy
import re

import ballpark_pricing as bp

TIER_OPTIONS = [["Quality", "Quality"], ["Premium", "Premium"], ["Luxury", "Luxury"]]
WINDOW_LEVELS = [["Quality", "Quality (vinyl)"], ["Premium", "Premium (Andersen 100)"],
                 ["Luxury", "Luxury (Andersen 400)"]]
PREP_OPTIONS = [["walk_in_shower", "Walk-in shower"], ["tub_surround", "Tub surround"], ["", "Neither"]]
WATER_HEATER = [["none", "None"], ["tank", "Tank"], ["tankless", "Tankless"]]
ENGINEERING = [["none", "None"], ["letter", "Letter ($1,850)"], ["full", "Full ($2,800)"]]

STATUSES = ("stated", "estimated", "not_mentioned", "conflict")
SPACE_TYPES = {"kitchen": "Kitchen", "primary_bath": "Primary bath", "hall_bath": "Hall bath",
               "powder": "Powder room"}
TILE_KIND_FOR_ROOM = {"kitchen": "backsplash", "primary_bath": "primary_bath", "hall_bath": "hall_bath_tub",
                      "powder": "small"}


def F(path, label, kind="number", core=False, always=False, unit="", help="", options=None, show_when=None):
    """One question. kind: number | money | yesno | choice."""
    q = {"path": path, "label": label, "kind": kind, "core": core, "always": always}
    if unit:
        q["unit"] = unit
    if help:
        q["help"] = help
    if options:
        q["options"] = options
    if show_when:
        q["show_when"] = show_when
    return q


JOB_FIELDS = [
    F("tier", "Job tier", "choice", always=True, options=TIER_OPTIONS,
      help="Used for every room and item without its own tier."),
    F("whole_home", "Whole-home renovation or addition?", "yesno", always=True),
    F("duration_weeks", "Duration", always=True, unit="weeks",
      help="Blank = 15 weeks for a single space, 32.5 for whole-home or addition."),
    F("punch_list_days", "Punch list days", always=True, unit="days",
      help="Blank = by job size (1 under $160K, 2 to $300K, 3 to $400K, 5 over)."),
    F("floor_plan_sf", "Renovated area", core=True, unit="SF",
      help="General Electrical is $9 per SF of this."),
]

EXTERIOR_FIELDS = [
    F("exterior.siding_squares", "Siding", core=True, unit="squares"),
    F("exterior.trim_lf", "Exterior trim", core=True, unit="LF",
      help="All trim: fascia, soffit, frieze, corners, casing, porch beams. Materials = 3x this."),
    F("exterior.full_exterior_repaint", "Full exterior repaint?", "yesno", core=True),
    F("exterior.paint_amount", "Exterior paint figure", "money", core=True,
      help="Needed for a full repaint (no figure = a $0 line to fill in). For partial paint, a figure here "
           "wins over the units."),
    F("exterior.paint_units", "Partial exterior paint", unit="units ($1,200)",
      help="About 1-3 for a porch, 3-8 for an addition, 10-20 for most of the house.",
      show_when={"path": "exterior.full_exterior_repaint", "equals": False}),
    F("exterior.windows", "Windows", core=True, unit="units",
      help="Mulled double = 2, triple = 3, large or odd size = 2."),
    F("exterior.window_tier", "Window and Andersen door level", "choice", core=True, options=WINDOW_LEVELS),
    F("exterior.original_wood_windows", "New windows match original wood windows?", "yesno"),
    F("exterior.specialty_windows", "Specialty windows (priced in review)", unit="units"),
    F("exterior.front_door", "Front door replaced?", "yesno", core=True),
    F("exterior.door_types.single", "Single doors, fiberglass ($900)"),
    F("exterior.door_types.single_wood", "Single doors, wood ($2,500)"),
    F("exterior.door_types.sidelights", "Front door w/ sidelights, fiberglass ($3,500)"),
    F("exterior.door_types.sidelights_wood", "Front door w/ sidelights, wood ($6,000)"),
    F("exterior.door_types.front_andersen", "Andersen front door"),
    F("exterior.door_types.french", "French doors, fiberglass ($1,900)"),
    F("exterior.door_types.french_wood", "French doors, wood ($3,500)"),
    F("exterior.door_types.sliding", "Sliding doors, 2-panel ($2,000)"),
    F("exterior.door_types.sliding_andersen", "Andersen sliding doors"),
    F("exterior.door_types.specialty", "Specialty doors (priced in review)"),
    F("exterior.wood_doors_stained", "Wood doors stained, not painted (+10%)?", "yesno"),
    F("exterior.roof_squares", "Roofing", unit="squares"),
    F("exterior.gutters", "Gutters", core=True, unit="units ($1,200)",
      help="About 1-2 for a porch or addition, 5-7 for a whole house."),
    F("deck_sf", "Deck", unit="SF"),
    F("exterior.porch_screening_lf", "Porch screening", unit="LF"),
    F("exterior.garage_doors_single", "Garage doors, single"),
    F("exterior.garage_doors_double", "Garage doors, double"),
    F("exterior.driveway_sf", "Driveway / concrete paths", unit="SF"),
    F("exterior.brick_facade_lf", "Brick facade", unit="LF"),
    F("exterior.foundation_brick_lf", "Foundation brick", unit="LF"),
]

STRUCTURE_FIELDS = [
    F("estimates.demo_days.value", "Demo", always=True, unit="man-days ($300)",
      help="Tyler's ballparks: 3-5 small, 8-16 kitchen/bath/small addition, ~40 whole home."),
    F("estimates.framing_days.value", "Framing labor", unit="days ($450)",
      help="Tyler's ballparks: 5-12 small, 16-30 medium, 36-40 additions."),
    F("framed_sf", "Framed / reframed floor area", core=True, unit="SF",
      help="Floor area, not wall area. Framing materials ($20) and insulation ($4.50) price on it."),
    F("addition_sf", "Addition (new heated SF)", unit="SF"),
    F("foundation_sf", "New foundation footprint", unit="SF", help="0 for a second-story addition."),
    F("foundation_perimeter_lf", "New footing perimeter", unit="LF"),
    F("slab_sf", "Slab", unit="SF"),
    F("waterproofing_lf", "Foundation waterproofing", unit="LF"),
    F("site.engineering", "Engineering", "choice", options=ENGINEERING),
    F("site.site_work_units", "Site work", unit="units ($800)"),
    F("site.grading_units", "Grading", unit="units ($600)"),
    F("site.survey", "Lot survey needed? ($1,850)", "yesno"),
]

MECHANICAL_FIELDS = [
    F("mechanical.full_rewire", "Full electrical rewire?", "yesno", core=True),
    F("mechanical.hvac_tons", "New HVAC system", core=True, unit="tons"),
    F("mechanical.new_ductwork", "New ductwork?", "yesno"),
    F("mechanical.supply_relocations", "HVAC supply relocations"),
    F("mechanical.return_relocations", "HVAC return relocations"),
    F("mechanical.water_heater", "Water heater", "choice", core=True, options=WATER_HEATER),
    F("mechanical.crawlspace", "Crawlspace treatment?", "yesno"),
    F("plumbing.fixture_count", "Plumbing fixtures installed", core=True,
      help="Sets finish plumbing hours (3 per fixture). Blank = counted by room."),
    F("plumbing.swap_same", "Fixtures swapped, same location", core=True),
    F("plumbing.new_in_footprint", "Fixtures moved within the footprint", core=True),
    F("plumbing.tub_in_footprint", "Tubs moved within the footprint", core=True),
    F("plumbing.cut_and_cap", "Cut and cap", core=True),
    F("plumbing.new_outside_footprint", "Fixtures in a new location outside the footprint"),
    F("plumbing.tub_outside_footprint", "Tubs outside the footprint"),
    F("plumbing.water_sewer_10lf", "Water/sewer runs", unit="10 LF each"),
    F("plumbing.vent_thru_roof", "Vents through the roof"),
    F("new_service.water_tap", "New water tap?", "yesno"),
    F("new_service.sewer_tap", "New sewer tap?", "yesno"),
]

INTERIOR_FIELDS = [
    F("interior_doors", "Interior doors", core=True),
    F("flooring.hardwood_new_sf", "Hardwood, new", unit="SF"),
    F("flooring.hardwood_refinish_sf", "Hardwood, refinish", unit="SF"),
    F("flooring.lvp_sf", "LVP", unit="SF"),
    F("flooring.carpet_sf", "Carpet", unit="SF"),
    F("flooring.tile_sf", "Tile flooring", unit="SF"),
    F("cabinetry.vanities_lf", "Vanities", unit="LF"),
    F("cabinetry.builtins_lf", "Built-ins, full height ($1,100)", unit="LF"),
    F("cabinetry.builtins_low_lf", "Built-ins, uppers and lowers ($625)", unit="LF"),
    F("closets.primary", "Primary closet systems"),
    F("closets.small", "Small closets (shelf and rod)"),
    F("mirrors", "Mirrors", help="$700 each, added to the plumbing fixture allowance."),
    F("pocket_doors", "Pocket doors"),
    F("glass_shower_doors", "Glass shower doors"),
    F("fireplaces", "Fireplaces"),
    F("fireplace_surrounds", "Fireplace surrounds"),
]

KITCHEN_FIELDS = [
    F("cabinetry.lowers_lf", "Lower cabinets", core=True, unit="LF"),
    F("cabinetry.uppers_lf", "Upper cabinets", core=True, unit="LF"),
    F("cabinetry.full_height_lf", "Full-height cabinets", unit="LF"),
    F("estimates.appliance_install_hours.value", "Appliance install", unit="hours ($75)",
      help="About 16 hours per kitchen."),
]


# ── Scope paths ─────────────────────────────────────────────────────────────

def _parts(path):
    return [int(p) if p.isdigit() else p for p in path.split(".")]


def get_path(scope, path):
    cur = scope
    for p in _parts(path):
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                return None
            cur = cur[p]
        else:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
    return cur


def set_path(scope, path, value):
    parts = _parts(path)
    cur = scope
    for i, p in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                raise KeyError(path)
            if not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        else:
            if not isinstance(cur.get(p), (dict, list)):
                if isinstance(nxt, int):
                    raise KeyError(path)
                cur[p] = {}
            cur = cur[p]
    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(cur, list) or last >= len(cur):
            raise KeyError(path)
    cur[last] = value


def _blank(v):
    return v is None or v == "" or v == 0 or v is False


# ── Rooms and what belongs to them ──────────────────────────────────────────

def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def links(scope):
    """Which tile areas, countertops and fixtures belong to each kitchen/bath.
    Tile areas match by name, then by kind in order; countertops by name,
    then "kitchen" for the first kitchen; fixtures by their room name.
    Returns {room_index: {"tile": [..], "counter": [..], "fixtures": [..]}}."""
    rooms = scope.get("rooms") or []
    tiles = scope.get("tile_areas") or []
    counters = scope.get("countertops") or []
    fixtures = scope.get("fixtures") or []
    out = {i: {"tile": [], "counter": [], "fixtures": []} for i, r in enumerate(rooms)}
    used_tile, used_counter = set(), set()

    for i, r in enumerate(rooms):
        name = _norm(r.get("name"))
        if not name:
            continue
        for j, t in enumerate(tiles):
            if j not in used_tile and _norm(t.get("name")) == name:
                out[i]["tile"].append(j)
                used_tile.add(j)
        for k, c in enumerate(counters):
            if k not in used_counter and _norm(c.get("name")) == name:
                out[i]["counter"].append(k)
                used_counter.add(k)
        for f, fx in enumerate(fixtures):
            if _norm(fx.get("room")) == name:
                out[i]["fixtures"].append(f)

    for i, r in enumerate(rooms):
        kind = TILE_KIND_FOR_ROOM.get(r.get("type"))
        if kind and not out[i]["tile"]:
            j = next((j for j, t in enumerate(tiles) if j not in used_tile and t.get("kind") == kind), None)
            if j is not None:
                out[i]["tile"].append(j)
                used_tile.add(j)
        if r.get("type") == "kitchen" and not out[i]["counter"]:
            k = next((k for k, c in enumerate(counters) if k not in used_counter and "kitchen" in _norm(c.get("name"))),
                     None)
            if k is not None:
                out[i]["counter"].append(k)
                used_counter.add(k)
        if r.get("type") in ("primary_bath", "hall_bath", "powder") and not out[i]["counter"]:
            word = "primary" if r.get("type") == "primary_bath" else None
            k = next((k for k, c in enumerate(counters) if k not in used_counter and "bath" in _norm(c.get("name"))
                      and (word is None or word in _norm(c.get("name")))), None)
            if k is not None:
                out[i]["counter"].append(k)
                used_counter.add(k)
    return out


# ── Building the pages ──────────────────────────────────────────────────────

def _status(path, value, field_status):
    st = (field_status or {}).get(path)
    if st not in STATUSES:
        # Claude didn't say. Anything with a value is treated as the tool's
        # guess so Tyler sees it; a blank one was simply not mentioned.
        st = "not_mentioned" if _blank(value) else "estimated"
    return st


def _reason(path, reply):
    scope = reply.get("scope") or {}
    if path.startswith("estimates.") and path.endswith(".value"):
        est = (scope.get("estimates") or {}).get(path.split(".")[1]) or {}
        if isinstance(est, dict) and est.get("reasoning"):
            return est["reasoning"]
    sources = reply.get("sources") or {}
    return sources.get(path) or ""


def _question(field, reply, path=None, label=None):
    scope = reply.get("scope") or {}
    q = dict(field)
    q["path"] = path or field["path"]
    if label:
        q["label"] = label
    value = get_path(scope, q["path"])
    status = _status(q["path"], value, reply.get("field_status"))
    if q["kind"] == "yesno" and value is not None:
        value = bool(value)
    q["value"] = value
    q["status"] = status
    q["source"] = _reason(q["path"], reply)
    if q.pop("always") or status in ("estimated", "conflict"):
        q["group"] = "ask"
    elif status == "stated":
        q["group"] = "found"
    else:
        q["group"] = "ask" if q["core"] else "more"
    q.pop("core")
    return q


def _page(pid, title, questions, info=None, room=None, found=True):
    """found=False: the read saw no work for this page. It is offered on the
    review step ("add work the read didn't find") instead of in the flow."""
    p = {"id": pid, "title": title, "questions": questions, "info": info or [], "found": found}
    if room is not None:
        p["room"] = room
    p["to_answer"] = sum(1 for q in questions if q["group"] == "ask") if found else 0
    return p


def _any_set(scope, fields):
    return any(not _blank(get_path(scope, f["path"])) for f in fields)


def _has_exterior(scope, reply):
    ext = scope.get("exterior") or {}
    fs = reply.get("field_status") or {}
    flat = {k: v for k, v in ext.items() if k != "door_types"}
    if any(not _blank(v) for v in flat.values()) or any(not _blank(v) for v in (ext.get("door_types") or {}).values()):
        return True
    if not _blank(scope.get("deck_sf")):
        return True
    return any(k.startswith("exterior.") and v in ("stated", "estimated", "conflict") for k, v in fs.items())


def _has_structure(scope):
    est = scope.get("estimates") or {}
    if any(k in est for k in ("demo_days", "framing_days")):
        return True
    return bool(scope.get("framing")) or _any_set(scope, STRUCTURE_FIELDS)


def _has_mechanical(scope):
    if _any_set(scope, MECHANICAL_FIELDS):
        return True
    return any((r.get("type") in SPACE_TYPES) for r in scope.get("rooms") or [])


def _fixture_line(fx):
    qty = bp._count(fx.get("quantity")) or 1
    brand = f"{fx['brand']} " if fx.get("brand") else ""
    return f"{qty} x {brand}{fx.get('item')} ({fx.get('tier') or 'job tier'})"


def _space_page(i, room, link, reply, counts):
    scope = reply.get("scope") or {}
    rtype = room.get("type")
    counts[rtype] = counts.get(rtype, 0) + 1
    title = room.get("name") or SPACE_TYPES.get(rtype, "Space")
    qs = [_question(F(f"rooms.{i}.tier", "Tier", "choice", core=True, options=TIER_OPTIONS), reply),
          _question(F(f"rooms.{i}.tall_ceiling", "Ceiling 18 ft or taller?", "yesno"), reply)]
    tiles = scope.get("tile_areas") or []
    for j in link["tile"]:
        t = tiles[j]
        if t.get("kind") == "small":
            # Small areas are still priced per SF of material by tier.
            qs.append(_question(F(f"tile_areas.{j}.sf", "Tile area", unit="SF",
                                  help="Blank = 50 SF."), reply))
            qs.append(_question(F(f"tile_areas.{j}.tier", "Tile tier", "choice", options=TIER_OPTIONS), reply))
        elif rtype in ("primary_bath", "hall_bath"):
            # Baths: a flat tile allowance plus installation; the shower or tub sets
            # the hall bath installation ($5,500 walk-in, $4,200 tub).
            qs.append(_question(F(f"tile_areas.{j}.prep", "Shower or tub", "choice", core=True,
                                  options=PREP_OPTIONS,
                                  help="Hall bath tile installation: $5,500 walk-in shower, $4,200 tub."), reply))
    counters = scope.get("countertops") or []
    for k in link["counter"]:
        qs.append(_question(F(f"countertops.{k}.sf", "Countertop", core=True, unit="SF"), reply))
        qs.append(_question(F(f"countertops.{k}.tier", "Countertop tier", "choice", options=TIER_OPTIONS), reply))
    if rtype == "kitchen" and counts[rtype] == 1:
        qs += [_question(f, reply) for f in KITCHEN_FIELDS]
    fixtures = scope.get("fixtures") or []
    info = []
    if link["fixtures"]:
        info.append("Fixtures read: " + "; ".join(_fixture_line(fixtures[f]) for f in link["fixtures"]))
    return _page(f"room-{i}", title, qs, info, room=i)


def build_pages(reply):
    """The intake pages for one read, in order. Each page: id, title,
    questions, info lines, to_answer count, found; space pages carry their
    room index. Interior, exterior, structure and mechanical pages are always
    returned; found says whether the read saw work for them."""
    scope = reply.get("scope") or {}
    rooms = scope.get("rooms") or []
    pages = []

    info = []
    for c in reply.get("conflicts") or []:
        info.append(f"Conflict: {c}")
    pages.append(_page("job", "Job and timing", [_question(f, reply) for f in JOB_FIELDS], info))

    link = links(scope)
    counts = {}
    for i, room in enumerate(rooms):
        if room.get("type") in SPACE_TYPES:
            pages.append(_space_page(i, room, link[i], reply, counts))

    others = [(i, r) for i, r in enumerate(rooms) if r.get("type") not in SPACE_TYPES]
    if others:
        qs = []
        for i, r in others:
            qs.append(_question(F(f"rooms.{i}.tier", f"{r.get('name') or 'Room'} tier", "choice",
                                  options=TIER_OPTIONS), reply))
            qs.append(_question(F(f"rooms.{i}.tall_ceiling", f"{r.get('name') or 'Room'}: ceiling 18 ft+?",
                                  "yesno"), reply))
        pages.append(_page("rooms", "Other rooms", qs,
                           [f"{len(others)} other room(s): " + ", ".join(r.get("name") or "Room" for _, r in others)
                            + ". Each one gets paint, drywall, trim and devices."]))

    qs = [_question(f, reply) for f in INTERIOR_FIELDS]
    if not any(r.get("type") == "kitchen" for r in rooms):
        qs += [_question(dict(f, core=False), reply) for f in KITCHEN_FIELDS[:3]]
    linked_t = {j for l in link.values() for j in l["tile"]}
    linked_c = {k for l in link.values() for k in l["counter"]}
    for j, t in enumerate(scope.get("tile_areas") or []):
        if j not in linked_t:
            qs.append(_question(F(f"tile_areas.{j}.sf", f"Tile: {t.get('name') or 'area'}", unit="SF"), reply))
    for k, c in enumerate(scope.get("countertops") or []):
        if k not in linked_c:
            qs.append(_question(F(f"countertops.{k}.sf", f"Countertop: {c.get('name') or 'area'}", unit="SF"),
                                reply))
    pages.append(_page("interior", "Interior finishes", qs, found=bool(rooms)))

    pages.append(_page("exterior", "Exterior", [_question(f, reply) for f in EXTERIOR_FIELDS],
                       found=_has_exterior(scope, reply)))
    pages.append(_page("structure", "Structure and demo", [_question(f, reply) for f in STRUCTURE_FIELDS],
                       found=_has_structure(scope)))
    pages.append(_page("mechanical", "Mechanical and plumbing", [_question(f, reply) for f in MECHANICAL_FIELDS],
                       found=_has_mechanical(scope)))
    return pages


def allowed_paths(reply):
    return {q["path"] for p in build_pages(reply) for q in p["questions"]}


# ── Turning answers into the priced scope ───────────────────────────────────

NUMBER_KINDS = ("number", "money")


def _clean(q, value):
    """An answer in the type its question expects. None = left blank."""
    if value is None or value == "":
        return None
    if q["kind"] in NUMBER_KINDS:
        try:
            n = float(str(value).replace(",", "").replace("$", "").strip())
        except ValueError:
            raise ValueError(f"'{q['label']}' needs a number (got '{value}').")
        if n < 0:
            raise ValueError(f"'{q['label']}' can't be negative.")
        return int(n) if n == int(n) else n
    if q["kind"] == "yesno":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "yes", "1")
    if q["kind"] == "choice":
        allowed = [o[0] for o in q.get("options") or []]
        if value not in allowed:
            raise ValueError(f"'{q['label']}' must be one of {', '.join(a or 'blank' for a in allowed)}.")
        return value or None
    return value


def _set_estimate(scope, path, value):
    key = path.split(".")[1]
    est = scope.setdefault("estimates", {})
    cur = est.get(key)
    if not isinstance(cur, dict):
        cur = {}
    if value is None:
        est.pop(key, None)
        return
    cur["value"] = value
    cur["reasoning"] = "entered by Tyler on the guided intake"
    est[key] = cur


def confirmed_scope(reply, answers=None, remove_rooms=None):
    """The read's scope with Tyler's answers applied: exactly what gets priced.

    answers: {path: value} for questions on the pages; any other path is
    refused. remove_rooms: room indexes (from the read) to take out, along
    with their tile areas, countertops and fixtures. A tier change carries to
    the items that followed the old tier and that Tyler didn't set himself.
    Returns (scope, changes) where changes lists what Tyler changed."""
    answers = dict(answers or {})
    read_scope = reply.get("scope") or {}
    scope = copy.deepcopy(read_scope)
    by_path = {q["path"]: q for p in build_pages(reply) for q in p["questions"]}
    unknown = [p for p in answers if p not in by_path]
    if unknown:
        raise ValueError("Unknown question(s): " + ", ".join(sorted(unknown)[:5]))

    changes = []
    for path, raw in answers.items():
        q = by_path[path]
        value = _clean(q, raw)
        before = get_path(read_scope, path)
        if path.startswith("estimates.") and path.endswith(".value"):
            _set_estimate(scope, path, value)
        else:
            set_path(scope, path, value)
        if (before if not _blank(before) else None) != (value if not _blank(value) else None):
            changes.append({"path": path, "label": q["label"], "from": before, "to": value})

    _carry_tiers(read_scope, scope, set(answers))

    rooms = scope.get("rooms") or []
    gone = sorted({int(i) for i in remove_rooms or [] if str(i).isdigit() and int(i) < len(rooms)}, reverse=True)
    if gone:
        link = links(read_scope)
        drop_tile = sorted({j for i in gone for j in link[i]["tile"]}, reverse=True)
        drop_counter = sorted({k for i in gone for k in link[i]["counter"]}, reverse=True)
        drop_fix = sorted({f for i in gone for f in link[i]["fixtures"]}, reverse=True)
        for i in gone:
            changes.append({"path": f"rooms.{i}", "label": f"Removed {rooms[i].get('name') or 'space'}",
                            "from": rooms[i].get("name"), "to": None})
            rooms.pop(i)
        for key, idxs in (("tile_areas", drop_tile), ("countertops", drop_counter), ("fixtures", drop_fix)):
            items = scope.get(key) or []
            for j in idxs:
                if j < len(items):
                    items.pop(j)

    # Weeks come only from Tyler (or the spec default), never the read.
    scope.pop("duration_range", None)
    if _blank(scope.get("duration_weeks")):
        scope.pop("duration_weeks", None)
    return scope, changes


def _carry_tiers(read_scope, scope, answered):
    old_job, new_job = read_scope.get("tier"), scope.get("tier")
    rooms_before = read_scope.get("rooms") or []
    rooms = scope.get("rooms") or []
    if new_job and new_job != old_job:
        for i, r in enumerate(rooms):
            if f"rooms.{i}.tier" not in answered and (not r.get("tier") or r.get("tier") == old_job):
                r["tier"] = new_job
    link = links(read_scope)
    claimed = {"tile_areas": set(), "countertops": set(), "fixtures": set()}
    for i, r in enumerate(rooms):
        old = (rooms_before[i] if i < len(rooms_before) else {}).get("tier")
        new = r.get("tier")
        for key, idxs in (("tile_areas", link[i]["tile"]), ("countertops", link[i]["counter"]),
                          ("fixtures", link[i]["fixtures"])):
            claimed[key].update(idxs)
            if not new or new == old:
                continue
            for j in idxs:
                item = (scope.get(key) or [])[j]
                if f"{key}.{j}.tier" not in answered and (not item.get("tier") or item.get("tier") == old):
                    item["tier"] = new
    if new_job and new_job != old_job:
        for key in ("tile_areas", "countertops", "fixtures"):
            for j, item in enumerate(scope.get(key) or []):
                if j in claimed[key] or f"{key}.{j}.tier" in answered:
                    continue
                if not item.get("tier") or item.get("tier") == old_job:
                    item["tier"] = new_job
