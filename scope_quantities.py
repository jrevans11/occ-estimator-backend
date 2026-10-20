"""Site Scope Tool - working quantities from the room file (phase 5).

Code, not the AI, works out the numbers the drawings imply (build plan, "AI vs.
code split"; write-up process, "Quantities from the drawings"): floor and
ceiling area, wall paint/drywall area, base, casing, crown and chair rail
runs, and wall tile area. They go in the scope Page's working quantities
table now and become budget quantities in phase 6.

Every quantity keeps the weakest source of the numbers it came from, so a
floor area built on one estimated wall length is "est" too. These are
planning numbers, not ordering numbers.

Standard library plus scope_drawings' parser (which loads Pillow).
"""

import scope_drawings as sd

# Openings that stop the baseboard (windows don't).
FLOOR_OPENINGS = sd.DOOR_TYPES | {"cased_opening"}
# Things standing on the floor that the baseboard stops at (rules: finishes
# stop at cabinets/fixtures in front of them).
FLOOR_ITEMS = {"base_cabinet", "vanity", "appliance", "tub", "shower"}


def _q(item, value, unit, basis, *srcs):
    return {"item": item, "value": round(value), "unit": unit, "basis": basis, "src": sd.weakest(*srcs)}


def _polygon_area(points):
    """Shoelace area of the finished-surface outline, square inches."""
    n = len(points)
    return abs(sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
                   for i in range(n))) / 2.0


def _kept(things):
    return [x for x in things if x["status"] != "remove"]


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def room_quantities(room):
    """Quantities for one room (a room-file dict or parse_room output).
    Returns [{"item", "value", "unit", "basis", "src"}]."""
    room = room if room.get("_parsed") else sd.parse_room(room)
    name = room["name"]
    geo = sd.plan_geometry(room)
    walls = [w for w in room["walls"] if w["status"] != "remove"]
    ceiling, csrc = room["ceiling"]
    length_srcs = [w["length"][1] for w in room["walls"]]
    perimeter = sum(w["length"][0] for w in walls)
    out = []

    if geo["closes"]:
        area = _polygon_area([s["p0"] for s in geo["segs"]]) / 144.0
        out.append(_q(f"{name} - floor area", area, "sf", "floor plan, wall lengths", *length_srcs))
        out.append(_q(f"{name} - ceiling area", area, "sf", "same as the floor", *length_srcs))
    # A room that doesn't close has no trustworthy area; its flag already says so.

    wall_sf, wall_srcs, open_sf = 0.0, list(length_srcs) + [csrc], 0.0
    for w in walls:
        wall_sf += w["length"][0] * ceiling
        for o in _kept(w["openings"]):
            open_sf += o["width"][0] * max(0.0, o["head"][0] - o["sill"][0])
            wall_srcs += [o["width"][1], o["head"][1], o["sill"][1]]
    out.append(_q(f"{name} - wall area (paint/drywall)", (wall_sf - open_sf) / 144.0, "sf",
                  f"perimeter {sd.ft_in(perimeter)} x ceiling {sd.ft_in(ceiling)}, less openings", *wall_srcs))

    base, base_srcs = perimeter, list(length_srcs)
    casing, casing_srcs, casing_count = 0.0, [], 0
    for w in walls:
        for o in _kept(w["openings"]):
            width, height = o["width"][0], o["head"][0] - o["sill"][0]
            if o["type"] in FLOOR_OPENINGS:
                base -= o["width"][0]
                base_srcs.append(o["width"][1])
                casing += 2 * height + width          # two legs and a head
            else:
                casing += 2 * height + 2 * width      # windows cased all four sides
            casing_count += 1
            casing_srcs += [o["width"][1], o["head"][1], o["sill"][1]]
        for it in _kept(w["items"]):
            if it["type"] in FLOOR_ITEMS and it.get("width"):
                base -= it["width"][0]
                base_srcs.append(it["width"][1])
    out.append(_q(f"{name} - base trim", max(0.0, base) / 12.0, "lf",
                  "perimeter less door openings and floor cabinets/fixtures", *base_srcs))
    if casing_count:
        out.append(_q(f"{name} - casing (this room's side)", casing / 12.0, "lf",
                      f"{casing_count} opening(s): doors 2 legs + head, windows all 4 sides", *casing_srcs))

    finish_types = {str(f.get("type") or "").lower() for w in walls for f in w["finishes"]}
    for ftype, word in (("crown", "crown"), ("chair_rail", "chair rail")):
        if ftype in finish_types:
            out.append(_q(f"{name} - {word}", perimeter / 12.0, "lf", "room perimeter", *length_srcs))

    tile_sf, tile_srcs = 0.0, []
    for w in walls:
        for f in w["finishes"]:
            if str(f.get("type") or "").lower() not in ("tile", "backsplash"):
                continue
            x0 = (sd.measure(f.get("offset")) or (0.0, "dictated"))
            span = sd.measure(f.get("width")) or w["length"]
            default_bottom, default_top = ((36.0, 54.0) if f.get("type") == "backsplash" else (0.0, ceiling))
            bottom = sd.measure(f.get("bottom")) or (default_bottom, "assumed")
            top = (room["ceiling"] if f.get("top") == "ceiling" else sd.measure(f.get("top"))) or \
                (default_top, "assumed")
            a0, a1 = x0[0], x0[0] + span[0]
            area = (a1 - a0) * max(0.0, top[0] - bottom[0])
            for o in _kept(w["openings"]):
                area -= _overlap(a0, a1, o["t0"], o["t1"]) * _overlap(bottom[0], top[0], o["sill"][0], o["head"][0])
            tile_sf += max(0.0, area)
            tile_srcs += [x0[1], span[1], bottom[1], top[1]]
    if tile_sf:
        out.append(_q(f"{name} - wall tile area", tile_sf / 144.0, "sf",
                      "tiled width x tile height on the elevations, less openings", *tile_srcs))
    return out


def scope_item_quantities(room_file):
    """The quantities the scope items carry (dictated or estimated in the quick pass)."""
    out = []
    for s in room_file.get("scope_items") or []:
        q = s.get("quantity") or {}
        if not isinstance(q, dict) or not isinstance(q.get("value"), (int, float)):
            continue
        area = f"{s.get('area')} - " if s.get("area") else ""
        src = sd.norm_src(q.get("src"))
        basis = {"dictated": "dictated or answered on site", "est": "estimated from photos",
                 "assumed": "assumed"}[src]
        out.append({"item": f"{area}{s.get('work') or s.get('trade') or 'Scope item'}", "value": q["value"],
                    "unit": str(q.get("unit") or "ea"), "basis": basis, "src": src})
    return out


def all_quantities(room_file):
    """Room quantities for every drawable room, then the scope items' own."""
    out = []
    for room in room_file.get("rooms") or []:
        out += room_quantities(room)
    return out + scope_item_quantities(room_file)
