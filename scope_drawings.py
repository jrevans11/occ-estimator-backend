"""Site Scope Tool - floor plan and wall elevation drawings.

Draws PNGs from a structured room file (the "occ-scope-rooms/1" format,
documented in docs/site-scope/room-file-format.md). Phase 4's quick pass
writes that file from the dictated notes and photos; this module only draws
what the file says, following docs/site-scope/floor-plan-drawing-rules.md:

- Walls are walked clockwise from Wall 1; each opening and item is placed by
  its distance from the wall's left corner (standing in the room facing it).
- Colors: existing gray, remove dashed red, new blue.
- Every number carries its source: dictated/confirmed (no tag), "(est.)"
  from a photo, "(assumed)" default.
- Closure check: when the walls do not meet up within 1", the plan shows the
  gap in red and says so instead of quietly fudging a wall.

Pillow only - no headless browser - so it fits the shared 512 MB Render
service. Nothing here touches the network.

Main entry point: render_drawings(data) -> {"drawings": [...], "flags": [...]}.
"""

import io
import math
import textwrap
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

SCHEMA = "occ-scope-rooms/1"

# Supersampling: draw at SS x the output size and scale down, which smooths
# arcs and circles (Pillow does not anti-alias shapes; text is always
# anti-aliased). 1 = off. At 2 a full Test (1) set peaked around 75 MB
# extra memory against about 20 MB at 1, for little visible gain on mostly
# straight lines - see the Phase 2 notes in the build plan.
SS = 1

WHITE = (255, 255, 255)
INK = (34, 34, 34)
MUTED = (110, 110, 110)
GRAY = (107, 107, 107)
RED = (198, 40, 40)
BLUE = (21, 101, 192)
LIGHT_LINE = (190, 190, 190)

STATUS_COLOR = {"existing": GRAY, "remove": RED, "new": BLUE, "replace": BLUE}

# Wall thickness by construction (floor-plan-drawing-rules.md section 4,
# plus the Oct 19 CMU decision: draw 8" and mark it assumed).
CONSTRUCTION = {
    "interior": (4.5, "Interior wall"),
    "plumbing": (6.5, "Plumbing wall"),
    "2x6": (6.5, "2x6 wall"),
    "exterior": (6.0, "Exterior wall"),
    "exterior_2x6": (8.0, "2x6 exterior"),
    "brick": (10.0, "Brick veneer"),
    "cmu": (8.0, "CMU block"),
    "half": (4.5, "Half wall"),
    "knee": (4.5, "Knee wall"),
}
WALL_FILL = {"cmu": (200, 200, 200), "brick": (214, 190, 182)}
DEFAULT_WALL_FILL = (228, 228, 228)

OPENING_TYPES = {"door", "window", "cased_opening", "pocket_door", "slider", "bifold", "french_door"}
DOOR_TYPES = {"door", "pocket_door", "slider", "bifold", "french_door"}
# Items drawn as a point (offset is to the center), not a box.
DEVICE_TYPES = {"outlet", "switch", "light", "valve", "device", "conduit"}
# Default plan depth for boxed items, inches (drawing rules section 6).
DEFAULT_DEPTH = {"base_cabinet": 24, "upper_cabinet": 12, "vanity": 21, "appliance": 24,
                 "tub": 30, "shower": 36, "toilet": 28, "chase": 12}
# Item types shown on the plan by default. Devices show only when new/removed.
PLAN_BY_DEFAULT = {"base_cabinet", "upper_cabinet", "vanity", "appliance", "tub", "shower", "toilet"}
DASHED_ON_PLAN = {"upper_cabinet", "chase"}
# Boxed items that finishes (base, wainscot, tile) stop at.
CUTS_FINISHES = {"base_cabinet", "upper_cabinet", "vanity", "appliance", "tub", "shower"}

TYPE_WORDS = {"door": "door", "window": "window", "cased_opening": "cased opening",
              "pocket_door": "pocket door", "slider": "sliding door", "bifold": "bifold door",
              "french_door": "French doors", "outlet": "receptacle", "switch": "switch",
              "light": "light", "base_cabinet": "base cabinets", "upper_cabinet": "upper cabinets"}
STATUS_WORDS = {"existing": "exist.", "remove": "REMOVE", "new": "NEW", "replace": "REPLACE"}

CLOSURE_TOLERANCE_IN = 1.0
SRC_RANK = {"dictated": 0, "est": 1, "assumed": 2}
SRC_TAG = {"dictated": "", "est": " (est.)", "assumed": " (assumed)"}

FOOTER = "For planning and quoting only - not a permit or construction drawing."
SOURCE_KEY = "No tag = dictated or confirmed.  (est.) = estimated from a photo.  (assumed) = default, nothing to go on."


class DrawingError(ValueError):
    """The room file can't be drawn (missing walls, bad numbers)."""


# ── Measurements ─────────────────────────────────────────────────────────────

def norm_src(src):
    s = str(src or "").strip().lower().rstrip(".")
    if s in ("dictated", "confirmed", "measured", "d"):
        return "dictated"
    if s in ("est", "estimated", "photo", "e"):
        return "est"
    return "assumed"


def measure(value, what="value"):
    """A measurement in the room file -> (inches, source), or None.

    Accepts {"in": 36, "src": "est"} or a bare number. A bare number has no
    source, so it is treated as assumed (drawing rules section 12)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise DrawingError(f"{what}: expected a number of inches, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value), "assumed"
    if isinstance(value, dict) and "in" in value:
        v = value["in"]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise DrawingError(f"{what}: 'in' must be a number of inches, got {v!r}")
        return float(v), norm_src(value.get("src"))
    raise DrawingError(f"{what}: expected a number or {{\"in\": ..., \"src\": ...}}, got {value!r}")


def weakest(*srcs):
    return max((s for s in srcs if s), key=lambda s: SRC_RANK[s], default="dictated")


def ft_in(inches):
    """Feet and inches, rounded to the nearest inch: 158 -> 13'-2"."""
    n = int(round(inches))
    sign = "-" if n < 0 else ""
    n = abs(n)
    return f"{sign}{n // 12}'-{n % 12}\""


def label(inches, src):
    return ft_in(inches) + SRC_TAG[src]


# ── Fonts and canvas ─────────────────────────────────────────────────────────

@lru_cache(maxsize=32)
def _font(px):
    try:
        return ImageFont.load_default(size=px)        # Pillow >= 10.1, scalable
    except (TypeError, OSError, AttributeError):
        for name in ("DejaVuSans.ttf", "Arial.ttf"):
            try:
                return ImageFont.truetype(name, px)
            except OSError:
                pass
        return ImageFont.load_default()


def _draw_text(draw, xy, s, font, color, bold, size):
    """Pillow has no bold for its built-in font: thicken by drawing twice one
    pixel apart (a stroke outline smears small text)."""
    spacing = int(size * 0.25 * SS)
    draw.multiline_text(xy, s, font=font, fill=color, spacing=spacing)
    if bold:
        draw.multiline_text((xy[0] + SS, xy[1]), s, font=font, fill=color, spacing=spacing)


def _rect_hit(a, b, pad=2):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


class Canvas:
    """Pillow drawing surface in output pixels, drawn at SS x and scaled down."""

    def __init__(self, width, height):
        self.w, self.h = int(math.ceil(width)), int(math.ceil(height))
        self.img = Image.new("RGB", (self.w * SS, self.h * SS), WHITE)
        self.d = ImageDraw.Draw(self.img)
        self.boxes = []   # placed text boxes, for label collision avoidance

    @staticmethod
    def _p(pt):
        return (pt[0] * SS, pt[1] * SS)

    def line(self, p0, p1, color=INK, w=1.0, dash=None):
        self.polyline([p0, p1], color, w, dash)

    def polyline(self, pts, color=INK, w=1.0, dash=None):
        width = max(1, int(round(w * SS)))
        if not dash:
            self.d.line([self._p(p) for p in pts], fill=color, width=width, joint="curve")
            return
        on, off = dash
        draw_on, left = True, on
        for a, b in zip(pts, pts[1:]):
            seg = math.dist(a, b)
            if seg == 0:
                continue
            ux, uy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
            pos = 0.0
            while pos < seg:
                step = min(left, seg - pos)
                if draw_on:
                    s = (a[0] + ux * pos, a[1] + uy * pos)
                    e = (a[0] + ux * (pos + step), a[1] + uy * (pos + step))
                    self.d.line([self._p(s), self._p(e)], fill=color, width=width)
                pos += step
                left -= step
                if left <= 1e-9:
                    draw_on = not draw_on
                    left = on if draw_on else off

    def polygon(self, pts, fill=None, outline=None, w=1.0, dash=None):
        if fill is not None:
            self.d.polygon([self._p(p) for p in pts], fill=fill)
        if outline is not None:
            self.polyline(list(pts) + [pts[0]], outline, w, dash)

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, w=1.0, dash=None):
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        self.polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], fill, outline, w, dash)

    def circle(self, c, r, fill=None, outline=None, w=1.0):
        x, y = self._p(c)
        rr = r * SS
        self.d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=fill, outline=outline,
                       width=max(1, int(round(w * SS))) if outline else 0)

    def text_size(self, s, size):
        f = _font(int(size * SS))
        lines = s.split("\n")
        width = max(f.getlength(ln) for ln in lines) / SS
        return width, size * 1.25 * len(lines)

    def text(self, x, y, s, size=13, color=INK, anchor="mm", bold=False, angle=0.0,
             avoid=False, step=(0.0, 0.0), tries=10):
        """Draw text; returns its box (x0, y0, x1, y1) in output px.

        anchor: Pillow-style two letters (l/m/r, t/m/b) for unrotated text;
        rotated text is always centered on (x, y). With avoid=True the text is
        nudged by `step` until it clears previously placed labels."""
        w, h = self.text_size(s, size)
        if angle:
            rad = math.radians(angle)
            bw = abs(w * math.cos(rad)) + abs(h * math.sin(rad))
            bh = abs(w * math.sin(rad)) + abs(h * math.cos(rad))
            ox, oy = -bw / 2, -bh / 2
        else:
            bw, bh = w, h
            ox = {"l": 0, "m": -w / 2, "r": -w}[anchor[0]]
            oy = {"t": 0, "m": -h / 2, "b": -h}[anchor[1]]
        box = (x + ox, y + oy, x + ox + bw, y + oy + bh)
        if avoid:
            for _ in range(tries):
                if not any(_rect_hit(box, b) for b in self.boxes):
                    break
                x, y = x + step[0], y + step[1]
                box = (box[0] + step[0], box[1] + step[1], box[2] + step[0], box[3] + step[1])
        self.boxes.append(box)
        f = _font(int(size * SS))
        if not angle:
            _draw_text(self.d, (box[0] * SS, box[1] * SS), s, f, color, bold, size)
        else:
            tmp = Image.new("RGBA", (int(w * SS) + 6, int(h * SS) + 4), (255, 255, 255, 0))
            _draw_text(ImageDraw.Draw(tmp), (2, 2), s, f, color + (255,), bold, size)
            rot = tmp.rotate(angle, expand=True, resample=Image.BICUBIC)
            self.img.paste(rot, (int(x * SS - rot.width / 2), int(y * SS - rot.height / 2)), rot)
            tmp.close()
            rot.close()
        return box

    def wrap(self, s, size, max_px):
        """Wrap a paragraph to max_px wide at this font size."""
        f = _font(int(size * SS))
        avg = max(1.0, f.getlength("abcdefghijklmnopqrstuvwxyz ") / 27 / SS)
        return textwrap.wrap(s, max(10, int(max_px / avg))) or [""]

    def png(self):
        out = self.img.reduce(SS) if SS > 1 else self.img
        buf = io.BytesIO()
        out.save(buf, "PNG", optimize=True)
        out.close()
        self.img.close()
        return buf.getvalue()


# ── Room file parsing ────────────────────────────────────────────────────────

def _status(obj):
    s = str(obj.get("status") or "existing").strip().lower()
    return s if s in STATUS_COLOR else "existing"


def _wall_thickness(wall):
    cons = str(wall.get("construction") or "interior").strip().lower()
    base, name = CONSTRUCTION.get(cons, (4.5, cons.replace("_", " ").title() or "Interior wall"))
    m = measure(wall.get("thickness"), f"Wall {wall.get('number')} thickness")
    if m:
        return m[0], m[1], name
    # A thickness from the table is only as good as the construction call;
    # CMU is always "assumed" (not in the rules table).
    csrc = norm_src(wall.get("construction_src")) if wall.get("construction") else "assumed"
    src = "assumed" if cons == "cmu" else weakest(csrc, "dictated")
    return base, src, name


def _opening(op, wall_no):
    what = f"Wall {wall_no} {op.get('type', 'opening')}"
    typ = str(op.get("type") or "door").strip().lower()
    if typ not in OPENING_TYPES:
        typ = "door" if "door" in typ else "window" if "window" in typ else "cased_opening"
    offset = measure(op.get("offset"), f"{what} offset")
    width = measure(op.get("width"), f"{what} width")
    if offset is None or width is None:
        raise DrawingError(f"{what}: needs an offset (from the left corner) and a width")
    sill = measure(op.get("sill"), f"{what} sill")
    height = measure(op.get("height"), f"{what} height")
    head = measure(op.get("head"), f"{what} head")
    if typ == "window":
        if sill and height and not head:
            head = (sill[0] + height[0], weakest(sill[1], height[1]))
        elif head and height and not sill:
            sill = (head[0] - height[0], weakest(head[1], height[1]))
        elif sill and head and not height:
            height = (head[0] - sill[0], weakest(head[1], sill[1]))
        if not (sill and head):
            raise DrawingError(f"{what}: needs two of sill, head, height")
    else:
        sill = (0.0, "dictated")
        if head is None:
            head = height or (80.0, "assumed")    # 6'-8" interior door head default
    return {
        "raw": op, "type": typ, "status": _status(op),
        "t0": offset[0], "t1": offset[0] + width[0], "offset": offset, "width": width,
        "sill": sill, "head": head,
        "hinge": "right" if str(op.get("hinge", "left")).lower().startswith("r") else "left",
        "swing": "out" if str(op.get("swing", "in")).lower() == "out" else "in",
        "sashes": int(op.get("sashes") or 1),
        "casing": op.get("casing"),
        "label": op.get("label"),
        "plan_label": op.get("plan_label"),
    }


def _item(it, wall_no, ceiling):
    what = f"Wall {wall_no} {it.get('type', 'item')}"
    typ = str(it.get("type") or "other").strip().lower()
    offset = measure(it.get("offset"), f"{what} offset")
    if offset is None:
        raise DrawingError(f"{what}: needs an offset from the left corner")
    width = measure(it.get("width"), f"{what} width")
    device = typ in DEVICE_TYPES or width is None
    out = {"raw": it, "type": typ, "status": _status(it), "device": device, "offset": offset,
           "width": width, "label": it.get("label"), "plan_label": it.get("plan_label")}
    if device:
        out["t0"] = out["t1"] = offset[0]
        out["height"] = measure(it.get("height"), f"{what} height")
    else:
        out["t0"], out["t1"] = offset[0], offset[0] + width[0]
    bottom = measure(it.get("bottom"), f"{what} bottom")
    top = (ceiling if it.get("top") == "ceiling" else measure(it.get("top"), f"{what} top"))
    h = measure(it.get("height"), f"{what} height") if not device else None
    defaults = {"base_cabinet": (0.0, 34.5), "vanity": (0.0, 34.0), "upper_cabinet": (54.0, 30.0)}
    if bottom is None and typ in defaults:
        bottom = (defaults[typ][0], "assumed")
    if typ == "conduit" and bottom is None:
        bottom = (0.0, "assumed")
    if h and bottom and not top:
        top = (bottom[0] + h[0], weakest(bottom[1], h[1]))
    if top and h and not bottom:
        bottom = (top[0] - h[0], weakest(top[1], h[1]))
    if bottom and not top and typ in defaults:
        top = (bottom[0] + defaults[typ][1], "assumed")
    out["bottom"], out["top"] = bottom, top
    depth = measure(it.get("depth"), f"{what} depth")
    out["depth"] = depth or ((DEFAULT_DEPTH.get(typ, 12.0), "assumed") if not device else None)
    show = it.get("plan")
    if show is None:
        show = typ in PLAN_BY_DEFAULT or (device and typ != "conduit" and out["status"] != "existing")
    out["plan"] = bool(show)
    return out


def parse_room(room):
    """Validate one room and return a normalized copy used by the drawing code."""
    name = room.get("name") or room.get("id") or "Room"
    walls = room.get("walls") or []
    if len(walls) < 3:
        raise DrawingError(f"{name}: needs at least 3 walls (walked clockwise from Wall 1)")
    ceiling = measure(room.get("ceiling"), f"{name} ceiling") or (96.0, "assumed")
    out_walls = []
    for i, w in enumerate(walls):
        no = w.get("number", i + 1)
        length = measure(w.get("length"), f"{name} Wall {no} length")
        if length is None or length[0] <= 0:
            raise DrawingError(f"{name} Wall {no}: needs a length")
        th, th_src, cons_name = _wall_thickness(w)
        out_walls.append({
            "raw": w, "number": no, "length": length, "turn": float(w.get("turn", 90)),
            "thickness": (th, th_src), "construction": cons_name,
            "construction_key": str(w.get("construction") or "interior").lower(),
            "exterior": bool(w.get("exterior")), "status": _status(w),
            "openings": [_opening(o, no) for o in w.get("openings") or []],
            "items": [_item(it, no, ceiling) for it in w.get("items") or []],
            "finishes": list(w.get("finishes") or []) + list(room.get("finishes") or []),
            "wall_finishes": list(w.get("finishes") or []),
            "notes": list(w.get("notes") or []),
            "elevation": w.get("elevation"),
            "label": w.get("label"),
        })
    return {"_parsed": True, "raw": room, "id": room.get("id") or _slug(name), "name": name, "ceiling": ceiling,
            "walls": out_walls, "notes": list(room.get("notes") or []), "flags": list(room.get("flags") or [])}


def _slug(s):
    out = "".join(c.lower() if c.isalnum() else "-" for c in str(s))
    return "-".join(p for p in out.split("-") if p) or "room"


# ── Plan geometry ────────────────────────────────────────────────────────────

def _add(a, b, s=1.0):
    return (a[0] + b[0] * s, a[1] + b[1] * s)


def _intersect(p, d, q, e):
    cross = d[0] * e[1] - d[1] * e[0]
    if abs(cross) < 1e-9:
        return p
    s = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / cross
    return (p[0] + d[0] * s, p[1] + d[1] * s)


def plan_geometry(room):
    """Walk the walls clockwise (screen y points down; Wall 1 heads right) and
    return per-wall segments plus the closure result.

    Each wall's interior side is to the right of travel; wall thickness is
    drawn outward, outside the finished-surface line (rules section 3)."""
    segs, pos, ang = [], (0.0, 0.0), 0.0
    for w in room["walls"]:
        L = w["length"][0]
        d = (math.cos(ang), math.sin(ang))
        p1 = _add(pos, d, L)
        segs.append({"wall": w, "p0": pos, "p1": p1, "d": d, "inn": (-d[1], d[0]), "out": (d[1], -d[0]),
                     "L": L, "t": w["thickness"][0]})
        pos = p1
        ang += math.radians(w["turn"])
    gap = math.dist(pos, (0.0, 0.0))
    heading = math.degrees(ang) % 360
    heading_ok = min(heading, 360 - heading) < 0.5
    closes = gap <= CLOSURE_TOLERANCE_IN and heading_ok
    n = len(segs)
    for i, s in enumerate(segs):
        prev_s, next_s = segs[i - 1], segs[(i + 1) % n]
        a0 = _add(s["p0"], s["out"], s["t"])
        a1 = _add(s["p1"], s["out"], s["t"])
        if i == 0 and not closes:
            s["o0"] = a0
        else:
            s["o0"] = _intersect(a0, s["d"], _add(prev_s["p0"], prev_s["out"], prev_s["t"]), prev_s["d"])
        if i == n - 1 and not closes:
            s["o1"] = a1
        else:
            s["o1"] = _intersect(a1, s["d"], _add(next_s["p0"], next_s["out"], next_s["t"]), next_s["d"])
    return {"segs": segs, "closes": closes, "gap": gap, "end": pos, "heading_ok": heading_ok}


def check_closure(room):
    """Closure check (rules section 3): do the walls meet back at the start?

    Returns {"closes": bool, "gap_in": float, "message": str}."""
    g = plan_geometry(room if room.get("_parsed") else parse_room(room))
    if g["closes"]:
        return {"closes": True, "gap_in": round(g["gap"], 2), "message": "Walls close."}
    if not g["heading_ok"]:
        msg = "Wall turns don't add up to a closed room - check the corner directions."
    else:
        msg = (f"Walls don't close: the last wall ends {ft_in(g['gap'])} ({g['gap']:.1f}\") from where Wall 1 "
               f"starts. Opposite walls should match - confirm the wall lengths.")
    return {"closes": False, "gap_in": round(g["gap"], 2), "message": msg}


def wall_flags(room):
    """Checks that don't need drawing: openings/items past the wall end, and
    openings that overlap each other on the same wall."""
    flags = []
    for w in room["walls"]:
        L = w["length"][0]
        for o in w["openings"]:
            if o["t1"] > L + 0.5 or o["t0"] < -0.5:
                flags.append(f"Wall {w['number']}: {_default_label(o)} runs past the wall "
                             f"({ft_in(o['t0'])} to {ft_in(o['t1'])} on a {ft_in(L)} wall).")
            if o["head"][0] > room["ceiling"][0] + 0.5:
                flags.append(f"Wall {w['number']}: {_default_label(o)} head is above the ceiling.")
        ops = w["openings"]
        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                a, b = ops[i], ops[j]
                if a["status"] == "remove" or b["status"] == "remove":
                    continue
                if a["t0"] < b["t1"] - 0.5 and b["t0"] < a["t1"] - 0.5 and \
                        a["sill"][0] < b["head"][0] - 0.5 and b["sill"][0] < a["head"][0] - 0.5:
                    flags.append(f"Wall {w['number']}: {_default_label(a)} and {_default_label(b)} overlap.")
        for it in w["items"]:
            if it["t1"] > L + 0.5 or it["t0"] < -0.5:
                flags.append(f"Wall {w['number']}: {it['label'] or it['type']} runs past the wall.")
    return flags


def _default_label(o):
    if o.get("label"):
        return o["label"].split("\n")[0]
    words = TYPE_WORDS.get(o["type"], o["type"].replace("_", " "))
    return f"{STATUS_WORDS[o['status']]} {words}"


# ── Floor plan ───────────────────────────────────────────────────────────────

def _legend(c, x, y, size=13):
    """Existing / remove / new swatches. Returns the y below the legend."""
    for name, color, dash in (("Existing", GRAY, None), ("Remove", RED, (7, 5)), ("New", BLUE, None)):
        c.line((x, y), (x + 34, y), color, 3, dash)
        b = c.text(x + 42, y, name, size, INK, "lm")
        x = b[2] + 26
    return y + size


def _footer_block(c, x, y, width, flags, extra_notes=()):
    """Notes, open questions and the source key under a drawing."""
    for n in extra_notes:
        for ln in c.wrap(n, 13, width):
            c.text(x, y, ln, 13, MUTED, "lt")
            y += 18
    if extra_notes:
        y += 6
    if flags:
        c.text(x, y, "CHECK BEFORE PRICING", 13, RED, "lt", bold=True)
        y += 20
        for f in flags:
            lines = c.wrap(f, 13, width - 16)
            for k, ln in enumerate(lines):
                c.text(x, y, ("- " if k == 0 else "  ") + ln, 13, RED, "lt")
                y += 18
        y += 6
    _legend(c, x, y + 8)
    y += 28
    c.text(x, y, SOURCE_KEY, 12, MUTED, "lt")
    y += 18
    c.text(x, y, FOOTER, 12, MUTED, "lt")
    return y + 18


def _footer_height(c, width, flags, extra_notes=()):
    h = 0
    for n in extra_notes:
        h += 18 * len(c.wrap(n, 13, width))
    h += 6 if extra_notes else 0
    if flags:
        h += 20 + sum(18 * len(c.wrap(f, 13, width - 16)) for f in flags) + 6
    return h + 28 + 18 + 18 + 18


def _text_angle(d):
    """Pillow rotation (counter-clockwise degrees) that writes text along d,
    kept upright."""
    theta = math.degrees(math.atan2(d[1], d[0]))   # clockwise on screen
    if theta > 90:
        theta -= 180
    elif theta <= -90:
        theta += 180
    return -theta


def dim_segments(L, Lsrc, things):
    """Split a wall of length L into dimension-string segments.

    things: openings/items with t0, t1, offset and width (width None for a
    device, which marks a single point). Each segment's source is the weaker
    of its two ends - except a segment that is exactly one opening or item,
    which carries that thing's width source (a dictated 8'-0" window stays
    dictated even when its offset is estimated).
    Returns [(t0, t1, src)]."""
    marks = {0.0: "dictated", L: Lsrc}
    spans = {}
    for th in things:
        ends = [(th["t0"], th["offset"][1])]
        if th.get("width") and th["t1"] > th["t0"]:
            ends.append((th["t1"], weakest(th["offset"][1], th["width"][1])))
        keys = []
        for t, src in ends:
            t = min(max(t, 0.0), L)
            key = next((m for m in marks if abs(m - t) < 0.5), t)
            marks[key] = src if key not in marks else min(marks[key], src, key=lambda x: SRC_RANK[x])
            keys.append(key)
        if len(keys) == 2:
            spans[(keys[0], keys[1])] = th["width"][1]
    m = sorted(marks.items())
    return [(t0, t1, spans.get((t0, t1), weakest(s0, s1))) for (t0, s0), (t1, s1) in zip(m, m[1:])
            if t1 - t0 >= 0.5]


def _dim_string(c, P, k, seg, offset_px, segments, color=INK):
    """Dimension string parallel to a plan wall, offset_px outside its outer
    face. segments: from dim_segments()."""
    d, out = seg["d"], seg["out"]
    marks = [(segments[0][0], None)] + [(t1, None) for _, t1, _ in segments]
    base = lambda t: P(_add(_add(seg["p0"], d, t), out, seg["t"]))   # noqa: E731
    shift = lambda p, px: (p[0] + out[0] * px, p[1] + out[1] * px)    # noqa: E731
    ang = _text_angle(d)
    a, b = shift(base(marks[0][0]), offset_px), shift(base(marks[-1][0]), offset_px)
    c.line(a, b, color, 1)
    tick = ((d[0] + out[0]) * 5, (d[1] + out[1]) * 5)
    for t, _ in marks:
        p = shift(base(t), offset_px)
        c.line(shift(base(t), 4), shift(p, 5), LIGHT_LINE, 1)
        c.line((p[0] - tick[0], p[1] - tick[1]), (p[0] + tick[0], p[1] + tick[1]), color, 1.4)
    for t0, t1, src in segments:
        L = t1 - t0
        txt = label(L, src)
        mid = shift(base((t0 + t1) / 2), offset_px)
        tw, th = c.text_size(txt, 12)
        fits = tw + 8 < L * k
        pos = shift(mid, th / 2 + 2) if fits else shift(mid, th / 2 + 2 + th)
        c.text(pos[0], pos[1], txt, 12, color, angle=ang, avoid=True, step=(out[0] * th, out[1] * th))


def _plan_opening(c, P, k, seg, o):
    d, inn, out, th = seg["d"], seg["inn"], seg["out"], seg["t"]
    t0, t1 = max(0.0, o["t0"]), min(seg["L"], o["t1"])
    if t1 <= t0:
        return
    pt = lambda t, depth=0.0: P(_add(_add(seg["p0"], d, t), out, depth))   # noqa: E731
    color = STATUS_COLOR[o["status"]]
    eps = 1.2 / k
    # Cut the wall.
    c.polygon([pt(t0, -eps), pt(t1, -eps), pt(t1, th + eps), pt(t0, th + eps)], fill=WHITE)
    jamb_color = GRAY if o["status"] == "replace" else color
    c.line(pt(t0, 0), pt(t0, th), jamb_color, 1.4)
    c.line(pt(t1, 0), pt(t1, th), jamb_color, 1.4)
    dash = (7, 5) if o["status"] == "remove" else None
    w_in = t1 - t0
    if o["type"] == "window":
        if o["status"] in ("remove", "replace"):
            c.polygon([pt(t0, 0), pt(t1, 0), pt(t1, th), pt(t0, th)], outline=RED, w=1.6, dash=(7, 5))
        if o["status"] != "remove":
            c.line(pt(t0, th / 2), pt(t1, th / 2), color, 2.6)
        else:
            c.line(pt(t0, th / 2), pt(t1, th / 2), RED, 1.4, (7, 5))
        if o["status"] == "existing":
            c.line(pt(t0, th * 0.3), pt(t1, th * 0.3), color, 1)
            c.line(pt(t0, th * 0.7), pt(t1, th * 0.7), color, 1)
        return
    if o["type"] == "cased_opening":
        return
    if o["type"] == "slider":
        c.line(pt(t0, th * 0.35), pt(t0 + w_in * 0.55, th * 0.35), color, 2, dash)
        c.line(pt(t1 - w_in * 0.55, th * 0.65), pt(t1, th * 0.65), color, 2, dash)
        return
    if o["type"] == "pocket_door":
        c.line(pt(t0 - w_in, th / 2), pt(t0, th / 2), color, 1.6, (6, 4))
        return
    if o["type"] == "bifold":
        for start, sign in ((t0, 1), (t1, -1)):
            a = P(_add(seg["p0"], d, start))
            mid = P(_add(_add(seg["p0"], d, start + sign * w_in / 4), inn, w_in / 4))
            end = P(_add(seg["p0"], d, start + sign * w_in / 2))
            c.polyline([a, mid, end], color, 1.6, dash)
        return
    leaves = [(t0, t1, o["hinge"])]
    if o["type"] == "french_door":
        leaves = [(t0, (t0 + t1) / 2, "left"), ((t0 + t1) / 2, t1, "right")]
    for a_t, b_t, hinge in leaves:
        lw = b_t - a_t
        hinge_t, latch_t = (a_t, b_t) if hinge == "left" else (b_t, a_t)
        face = 0.0 if o["swing"] == "in" else th
        normal = inn if o["swing"] == "in" else out
        h = _add(_add(seg["p0"], d, hinge_t), out, face)
        latch = _add(_add(seg["p0"], d, latch_t), out, face)
        tip = _add(h, normal, lw)
        c.line(P(h), P(tip), color, 2.6, dash)
        a0 = math.atan2(latch[1] - h[1], latch[0] - h[0])
        a1 = math.atan2(tip[1] - h[1], tip[0] - h[0])
        delta = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        arc = [P((h[0] + lw * math.cos(a0 + delta * i / 30), h[1] + lw * math.sin(a0 + delta * i / 30)))
               for i in range(31)]
        c.polyline(arc, color, 1, (5, 4))
        xs = [p[0] for p in arc] + [P(h)[0]]
        ys = [p[1] for p in arc] + [P(h)[1]]
        c.boxes.append((min(xs), min(ys), max(xs), max(ys)))


def _plan_item(c, P, k, seg, it):
    d, inn = seg["d"], seg["inn"]
    color = STATUS_COLOR[it["status"]]
    dash = (7, 5) if it["status"] == "remove" else None
    at = lambda t, depth=0.0: P(_add(_add(seg["p0"], d, t), inn, depth))   # noqa: E731
    if it["device"]:
        p = at(it["t0"], 9 / k)
        if it["type"] == "switch":
            c.text(p[0], p[1], "S", 15, color, bold=True)
        elif it["type"] == "light":
            c.circle(p, 7, WHITE, color, 1.6)
            c.line((p[0] - 5, p[1] - 5), (p[0] + 5, p[1] + 5), color, 1.4)
            c.line((p[0] - 5, p[1] + 5), (p[0] + 5, p[1] - 5), color, 1.4)
        else:
            c.circle(p, 7, WHITE, color, 1.8)
            nx, ny = d
            for s in (-2.2, 2.2):
                q = (p[0] + inn[0] * s, p[1] + inn[1] * s)
                c.line((q[0] - nx * 4.5, q[1] - ny * 4.5), (q[0] + nx * 4.5, q[1] + ny * 4.5), color, 1.4)
        return 12 / k
    depth = it["depth"][0]
    if it["type"] in DASHED_ON_PLAN or (it["raw"].get("above") is True):
        dash = dash or (7, 5)
    pts = [at(it["t0"]), at(it["t1"]), at(it["t1"], depth), at(it["t0"], depth)]
    c.polygon(pts, outline=color, w=1.8 if it["status"] != "existing" else 1.2, dash=dash)
    c.boxes.append((min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)))
    if it["type"] == "base_cabinet" and it["raw"].get("countertop", True):
        c.line(at(it["t0"], depth - 1.5), at(it["t1"], depth - 1.5), color, 1)
    return depth


def _place_plan_label(c, P, k, seg, t, depth_in, txt, color, bold=False):
    inn = seg["inn"]
    p = P(_add(_add(seg["p0"], seg["d"], t), inn, depth_in))
    p = (p[0] + inn[0] * 10, p[1] + inn[1] * 10)
    if abs(inn[0]) > abs(inn[1]):
        anchor = "lm" if inn[0] > 0 else "rm"
    else:
        anchor = "mt" if inn[1] > 0 else "mb"
    lines = txt.count("\n") + 1
    step = (inn[0] * 14, inn[1] * 18 * lines)
    c.text(p[0], p[1], txt, 13, color, anchor, bold=bold, avoid=True, step=step, tries=24)


def render_plan(room, project=None, extra_flags=()):
    """Floor plan PNG for one parsed room. Returns (png_bytes, flags)."""
    project = project or {}
    g = plan_geometry(room)
    segs = g["segs"]
    pts = [p for s in segs for p in (s["p0"], s["p1"], s["o0"], s["o1"])]
    minx, maxx = min(p[0] for p in pts), max(p[0] for p in pts)
    miny, maxy = min(p[1] for p in pts), max(p[1] for p in pts)
    span = max(maxx - minx, maxy - miny, 1.0)
    k = max(2.0, min(9.0, 1000.0 / span))            # px per inch
    margin = 215
    flags = []
    closure = check_closure(room)
    if not closure["closes"]:
        flags.append(closure["message"])
    flags += wall_flags(room) + list(room["flags"]) + list(extra_flags)
    body_w = (maxx - minx) * k + 2 * margin
    width = max(body_w, 1100)
    probe = Canvas(10, 10)
    foot_h = _footer_height(probe, width - 80, flags, room["notes"])
    probe.img.close()
    top = 90
    height = top + (maxy - miny) * k + 2 * margin + foot_h + 80
    c = Canvas(width, height)
    ox = (width - (maxx - minx) * k) / 2
    oy = top + margin

    def P(p):
        return (ox + (p[0] - minx) * k, oy + (p[1] - miny) * k)

    title = f"{room['name']} - Floor Plan"
    c.text(40, 30, title, 26, INK, "lt", bold=True)
    sub = " - ".join(x for x in (project.get("name"), project.get("address"), visit_words(project)) if x)
    if sub:
        c.text(40, 66, sub, 14, MUTED, "lt")

    # Walls.
    for s in segs:
        w = s["wall"]
        fill = WALL_FILL.get(w["construction_key"], DEFAULT_WALL_FILL)
        outline = STATUS_COLOR[w["status"]]
        dash = (8, 5) if w["status"] == "remove" else None
        if w["status"] == "new":
            fill = (214, 228, 245)
        c.polygon([P(s["p0"]), P(s["p1"]), P(s["o1"]), P(s["o0"])], fill=fill, outline=outline,
                  w=1.4 if w["status"] == "existing" else 2.2, dash=dash)
    inner = [P(s["p0"]) for s in segs] + ([P(segs[0]["p0"])] if g["closes"] else [P(g["end"])])
    c.polyline(inner, GRAY, 2)
    if not g["closes"]:
        a, b = P(g["end"]), P(segs[0]["p0"])
        c.line(a, b, RED, 2.4, (6, 4))
        c.circle(a, 6, None, RED, 2)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        c.text(mid[0] + 12, mid[1] + 12, f"DOES NOT CLOSE - off {ft_in(g['gap'])}", 14, RED, "lt", bold=True)

    # Openings and items.
    for s in segs:
        for o in s["wall"]["openings"]:
            _plan_opening(c, P, k, s, o)
        for it in s["wall"]["items"]:
            if it["plan"]:
                it["_plan_depth"] = _plan_item(c, P, k, s, it)

    # Room label.
    cx, cy = _centroid([s["p0"] for s in segs])
    cpx = P((cx, cy))
    c.text(cpx[0], cpx[1] - 26, room["name"].upper(), 22, INK, bold=True)
    walls = room["walls"]
    if len(walls) == 4 and all(abs(w["turn"] - 90) < 0.01 for w in walls):
        c.text(cpx[0], cpx[1] + 2, f"{label(*walls[0]['length'])} x {label(*walls[1]['length'])}", 15, MUTED)
    ceil_note = (room["raw"].get("ceiling") or {}).get("note") if isinstance(room["raw"].get("ceiling"), dict) else None
    c.text(cpx[0], cpx[1] + 24, f"Ceiling {label(*room['ceiling'])}" + (f" ({ceil_note})" if ceil_note else ""),
           14, MUTED)

    # Labels for openings and plan items, inside the room near their wall.
    for s in segs:
        for o in s["wall"]["openings"]:
            txt = o["plan_label"] or _default_label(o)
            if o["type"] in ("door", "french_door") and o["swing"] == "in":
                depth = min(o["t1"] - o["t0"], 40) + 2
            else:
                depth = 4
            _place_plan_label(c, P, k, s, (o["t0"] + o["t1"]) / 2, depth, txt, STATUS_COLOR[o["status"]],
                              bold=o["status"] != "existing")
        for it in s["wall"]["items"]:
            if not it["plan"]:
                continue
            txt = it["plan_label"] if it["plan_label"] is not None else it["label"]
            if not txt:
                continue
            _place_plan_label(c, P, k, s, (it["t0"] + it["t1"]) / 2, it.get("_plan_depth", 4), txt,
                              STATUS_COLOR[it["status"]], bold=it["status"] != "existing")

    # Dimensions, wall numbers and construction outside each wall.
    for s in segs:
        w = s["wall"]
        L, Lsrc = w["length"]
        ops = sorted(w["openings"], key=lambda o: o["t0"])
        tier = 34
        if ops:
            _dim_string(c, P, k, s, tier, dim_segments(L, Lsrc, ops))
            tier += 40
        a = P(_add(s["p0"], s["out"], s["t"]))
        b = P(_add(s["p1"], s["out"], s["t"]))
        sh = lambda p, px: (p[0] + s["out"][0] * px, p[1] + s["out"][1] * px)   # noqa: E731
        c.line(sh(a, tier), sh(b, tier), INK, 1)
        for p in (a, b):
            c.line(sh(p, 4), sh(p, tier + 5), LIGHT_LINE, 1)
            q = sh(p, tier)
            tk = ((s["d"][0] + s["out"][0]) * 5, (s["d"][1] + s["out"][1]) * 5)
            c.line((q[0] - tk[0], q[1] - tk[1]), (q[0] + tk[0], q[1] + tk[1]), INK, 1.4)
        mid = sh(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), tier + 12)
        c.text(mid[0], mid[1], f"Wall {w['number']}: {label(L, Lsrc)}", 14, INK, angle=_text_angle(s["d"]),
               bold=True, avoid=True, step=(s["out"][0] * 16, s["out"][1] * 16))
        # Construction only when the notes/photos said something (the plain
        # interior default would just be noise on every wall).
        midp = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        badge_at = tier + 52
        if w["label"] or w["raw"].get("construction") or w["raw"].get("thickness"):
            cpos = sh(midp, tier + 32)
            c.text(cpos[0], cpos[1], w["label"] or _construction_label(w), 12, MUTED, angle=_text_angle(s["d"]),
                   avoid=True, step=(s["out"][0] * 16, s["out"][1] * 16))
            badge_at = tier + 64
        for _ in range(12):
            badge = sh(midp, badge_at)
            box = (badge[0] - 17, badge[1] - 17, badge[0] + 17, badge[1] + 17)
            if not any(_rect_hit(box, bb) for bb in c.boxes):
                break
            badge_at += 20
        c.boxes.append(box)
        c.circle(badge, 15, INK)
        c.text(badge[0], badge[1] - 1, str(w["number"]), 16, WHITE, bold=True)

    # Start the notes below whatever label or badge ended up lowest.
    foot_y = max([oy + (maxy - miny) * k + margin - 40] + [b[3] + 24 for b in c.boxes])
    _footer_block(c, 40, min(foot_y, c.h - foot_h - 10), width - 80, flags, room["notes"])
    return c.png(), flags


def _inches(v):
    """4.5 -> 4-1/2", 8 -> 8" (wall thicknesses)."""
    whole, frac = int(v), v - int(v)
    fracs = {0.25: "1/4", 0.5: "1/2", 0.75: "3/4"}
    part = next((txt for f, txt in fracs.items() if abs(frac - f) < 0.01), None)
    if part:
        return f"{whole}-{part}\"" if whole else f"{part}\""
    return f"{round(v):d}\""


def _construction_label(w):
    th, src = w["thickness"]
    name = w["construction"]
    if w["exterior"] and "xterior" not in name:
        name = "Exterior " + name
    return f"{name} {_inches(th)}{SRC_TAG[src]}"


def _centroid(poly):
    a = cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cr = x0 * y1 - x1 * y0
        a += cr
        cx += (x0 + x1) * cr
        cy += (y0 + y1) * cr
    if abs(a) < 1e-9:
        return sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n
    return cx / (3 * a), cy / (3 * a)


def visit_words(project):
    v = project.get("visit_date")
    if not v:
        return ""
    try:
        from datetime import date
        dt = date.fromisoformat(str(v))
        return f"from the {dt.strftime('%b')} {dt.day}, {dt.year} site visit"
    except ValueError:
        return f"from the {v} site visit"


# ── Elevations ───────────────────────────────────────────────────────────────

def needs_elevation(wall):
    """Rules section 10: only walls with something on them, unless asked."""
    if wall["elevation"] is not None:
        return bool(wall["elevation"])
    return bool(wall["openings"] or wall["items"] or wall["wall_finishes"])


def _tint(color, amount=0.85):
    return tuple(int(c + (255 - c) * amount) for c in color)


def _minus_spans(a, b, cuts):
    """[a, b] minus each (c0, c1) in cuts -> list of spans."""
    spans = [(a, b)]
    for c0, c1 in cuts:
        nxt = []
        for s0, s1 in spans:
            if c1 <= s0 or c0 >= s1:
                nxt.append((s0, s1))
                continue
            if c0 > s0:
                nxt.append((s0, c0))
            if c1 < s1:
                nxt.append((c1, s1))
        spans = nxt
    return [s for s in spans if s[1] - s[0] > 0.25]


def render_elevation(room, wall, project=None):
    """Elevation PNG for one parsed wall, as seen from inside the room.
    Left edge of the drawing = the wall's left corner in the notes."""
    project = project or {}
    L, Lsrc = wall["length"]
    H, Hsrc = room["ceiling"]
    k = max(2.0, min(9.0, 1250.0 / L, 720.0 / H))
    left, right_pad, top = 70, 360, 150
    wall_w, wall_h = L * k, H * k
    width = max(left + wall_w + right_pad, 1150)
    probe = Canvas(10, 10)
    notes = wall["notes"]
    wflags = [f for f in wall_flags({"walls": [wall], "ceiling": room["ceiling"]})]
    foot_h = _footer_height(probe, width - 2 * left, wflags, notes)
    probe.img.close()
    height = top + wall_h + 170 + foot_h
    c = Canvas(width, height)
    fy = top + wall_h            # floor line y

    def X(t):
        return left + t * k

    def Y(h):
        return fy - h * k

    c.text(40, 30, f"{room['name']} - Wall {wall['number']} Elevation", 26, INK, "lt", bold=True)
    sub = " - ".join(x for x in (project.get("name"), project.get("address"), visit_words(project)) if x)
    c.text(40, 66, sub, 14, MUTED, "lt")
    c.text(40, 88, "Viewed from inside the room. Left edge = the wall's left corner in the notes. "
                   f"{_construction_label(wall)}.", 13, MUTED, "lt")

    c.rect(X(0), Y(H), X(L), Y(0), fill=(250, 250, 250), outline=GRAY, w=1.2)
    ceil_note = (room["raw"].get("ceiling") or {}).get("note") if isinstance(room["raw"].get("ceiling"), dict) else None
    c.text(X(L / 2), Y(H) - 14, f"Ceiling {label(H, Hsrc)}" + (f" ({ceil_note})" if ceil_note else ""), 13, MUTED)

    vmarks = [(H, Hsrc, "ceiling")]
    # Finishes stop at openings, and at cabinets/fixtures that sit in front
    # of them (no baseboard behind a base cabinet).
    cut_spans = [(o["t0"], o["t1"], o["sill"][0], o["head"][0]) for o in wall["openings"] if o["status"] != "remove"]
    cut_spans += [(it["t0"], it["t1"], it["bottom"][0], it["top"][0]) for it in wall["items"]
                  if it["type"] in CUTS_FINISHES and it["status"] != "remove" and it["bottom"] and it["top"]]

    # Finishes first (fills sit behind openings and items).
    for f in wall["finishes"]:
        _draw_finish(c, X, Y, L, H, f, cut_spans, vmarks)

    for o in wall["openings"]:
        _elev_opening(c, X, Y, o, vmarks)
    for it in wall["items"]:
        _elev_item(c, X, Y, H, it, vmarks)

    c.line((X(0) - 14, fy), (X(L) + 14, fy), INK, 4)

    # Horizontal dimension strings: openings, then new/removed items, then overall.
    y = fy + 34
    ops = sorted(wall["openings"], key=lambda o: o["t0"])
    if ops:
        _elev_hdim(c, X, y, L, dim_segments(L, Lsrc, ops))
        y += 44
    its = [it for it in wall["items"] if it["status"] != "existing" or it["raw"].get("dimension")]
    if its:
        _elev_hdim(c, X, y, L, dim_segments(L, Lsrc, its))
        y += 44
    c.line((X(0), y), (X(L), y), INK, 1)
    for t in (0, L):
        c.line((X(t), fy + 6), (X(t), y + 6), LIGHT_LINE, 1)
        c.line((X(t) - 5, y + 5), (X(t) + 5, y - 5), INK, 1.4)
    c.text(X(L / 2), y + 14, f"Wall {wall['number']}: {label(L, Lsrc)}", 14, INK, "mt", bold=True)

    _elev_vdim(c, X(L) + 34, Y, vmarks)
    _footer_block(c, left, y + 52, width - 2 * left, wflags, notes)
    return c.png()


def _merge_marks(vmarks):
    out = []
    for h, src, what in sorted(vmarks, key=lambda m: m[0]):
        if out and abs(out[-1][0] - h) < 0.5:
            ph, psrc, pwhat = out[-1]
            if what not in pwhat.split(" / "):
                pwhat = f"{pwhat} / {what}"
            out[-1] = (ph, min(psrc, src, key=lambda s: SRC_RANK[s]), pwhat)
        else:
            out.append((h, src, what))
    return out


def _elev_vdim(c, x, Y, vmarks):
    marks = _merge_marks([(0.0, "dictated", "floor")] + vmarks)
    c.line((x, Y(0)), (x, Y(marks[-1][0])), INK, 1)
    room_px = max(120, c.w - (x + 16) - 20)
    last_top = None
    for h, src, what in marks:
        y = Y(h)
        c.line((x - 5, y + 5), (x + 5, y - 5), INK, 1.4)
        if h == 0:
            continue
        txt = "\n".join(c.wrap(f"{label(h, src)} {what}", 13, room_px))
        _, th = c.text_size(txt, 13)
        ly = y if last_top is None else min(y, last_top - 3 - th / 2)
        if abs(ly - y) > 1:
            c.line((x + 6, y), (x + 14, ly), LIGHT_LINE, 1)
        box = c.text(x + 16, ly, txt, 13, INK, "lm")
        last_top = box[1]


def _elev_hdim(c, X, y, L, segments):
    c.line((X(0), y), (X(L), y), INK, 1)
    for t in [segments[0][0]] + [t1 for _, t1, _ in segments]:
        c.line((X(t) - 5, y + 5), (X(t) + 5, y - 5), INK, 1.4)
    for t0, t1, src in segments:
        txt = label(t1 - t0, src)
        tw, _ = c.text_size(txt, 12)
        mx = (X(t0) + X(t1)) / 2
        if tw + 8 < X(t1) - X(t0):
            c.text(mx, y - 4, txt, 12, INK, "mb", avoid=True, step=(0, 16))
        else:
            c.text(mx, y + 6, txt, 12, INK, "mt", avoid=True, step=(0, 16))


def _elev_label(c, X, Y, x0, x1, b, t, txt, color, bold):
    """Label inside the box when it fits, otherwise just above it."""
    if not txt:
        return
    tw, th = c.text_size(txt, 13)
    cx = (X(x0) + X(x1)) / 2 if x1 > x0 else X(x0)
    if x1 > x0 and tw + 10 < X(x1) - X(x0) and th + 6 < Y(b) - Y(t):
        c.text(cx, (Y(b) + Y(t)) / 2, txt, 13, color, bold=bold, avoid=True, step=(0, th + 2))
    else:
        c.text(cx, Y(t) - 6, txt, 13, color, "mb", bold=bold, avoid=True, step=(0, -(th + 2)))


def _elev_opening(c, X, Y, o, vmarks):
    color = STATUS_COLOR[o["status"]]
    x0, x1 = o["t0"], o["t1"]
    b, t = o["sill"][0], o["head"][0]
    casing = o["casing"]
    if casing:
        cw = measure(casing.get("width"), "casing width") or (3.5, "assumed")
        cc = STATUS_COLOR.get(str(casing.get("status") or o["status"]).lower(), color)
        cb = b - cw[0] if o["type"] == "window" else b
        c.rect(X(x0 - cw[0]), Y(max(cb, 0)), X(x1 + cw[0]), Y(t + cw[0]), fill=WHITE, outline=cc, w=1.1)
    c.rect(X(x0), Y(b), X(x1), Y(t), fill=WHITE)
    if o["status"] == "replace":
        c.rect(X(x0), Y(b), X(x1), Y(t), outline=BLUE, w=2.4)
        c.rect(X(x0) - 6, Y(b) + 6, X(x1) + 6, Y(t) - 6, outline=RED, w=1.6, dash=(8, 5))
    else:
        c.rect(X(x0), Y(b), X(x1), Y(t), outline=color, w=2.4 if o["status"] != "existing" else 1.4,
               dash=(8, 5) if o["status"] == "remove" else None)
    if o["type"] == "window":
        for i in range(1, o["sashes"]):
            xx = x0 + (x1 - x0) * i / o["sashes"]
            c.line((X(xx), Y(b)), (X(xx), Y(t)), color, 1.2, (6, 4))
        vmarks.append((b, o["sill"][1], "sill"))
        vmarks.append((t, o["head"][1], "head"))
    else:
        if o["type"] in ("door", "french_door"):
            latch = [x1 - 3] if o["hinge"] == "left" else [x0 + 3]
            if o["type"] == "french_door":
                latch = [(x0 + x1) / 2 - 3, (x0 + x1) / 2 + 3]
            for lx in latch:
                c.circle((X(lx), Y(36)), 4, None, color, 1.4)
        vmarks.append((t, o["head"][1], "door head" if o["type"] in DOOR_TYPES else "opening head"))
    txt = o["label"] or _default_label(o)
    if casing and casing.get("profile"):
        txt += f"\ncasing: {casing['profile']}"
    lc = color if o["status"] != "existing" else MUTED
    if o["type"] == "window":
        _elev_label(c, X, Y, x0, x1, b, t, txt, lc, o["status"] != "existing")
    else:
        c.text((X(x0) + X(x1)) / 2, Y(16), txt, 13, lc, bold=o["status"] != "existing", avoid=True, step=(0, -18))


def _elev_item(c, X, Y, H, it, vmarks):
    color = STATUS_COLOR[it["status"]]
    dash = (8, 5) if it["status"] == "remove" else None
    typ = it["type"]
    if typ == "conduit":
        b = it["bottom"][0] if it["bottom"] else 0.0
        t = it["top"][0] if it["top"] else H
        c.line((X(it["t0"]), Y(b)), (X(it["t0"]), Y(t)), color, 2, dash or ((6, 4) if it["status"] == "new" else None))
        if it["label"]:
            c.text(X(it["t0"]) + 8, Y((b + t) / 2), it["label"], 12, color, "lm", avoid=True, step=(0, 16))
        return
    if it["device"]:
        h = it["height"] or (16.0, "assumed")
        x, y = X(it["t0"]), Y(h[0])
        c.rect(x - 7, y - 11, x + 7, y + 11, fill=WHITE, outline=color, w=1.8, dash=dash)
        if typ == "switch":
            c.line((x, y - 5), (x, y + 5), color, 2)
        elif typ == "light":
            c.circle((x, y), 4, color)
        else:
            c.circle((x, y - 4.5), 2.2, color)
            c.circle((x, y + 4.5), 2.2, color)
        word = {"outlet": "recept.", "switch": "switch", "light": "light"}.get(typ, typ)
        vmarks.append((h[0], h[1], word))
        if it["label"]:
            c.text(x, y + 16, it["label"], 12, color, "mt", bold=it["status"] != "existing",
                   avoid=True, step=(0, 16))
        return
    if not (it["bottom"] and it["top"]):
        return
    b, t = it["bottom"][0], it["top"][0]
    fill = None
    if typ == "chase":
        fill = _tint(GRAY, 0.8)
    c.rect(X(it["t0"]), Y(b), X(it["t1"]), Y(t), fill=fill, outline=color,
           w=1.8 if it["status"] != "existing" else 1.2, dash=dash or ((7, 5) if typ in ("appliance",) else None))
    if typ in ("base_cabinet", "upper_cabinet", "vanity"):
        doors = int(it["raw"].get("doors") or 0)
        drawer = typ != "upper_cabinet" and it["raw"].get("drawers", True)
        door_b = b + (4 if typ != "upper_cabinet" else 0)
        door_t = t - (6 if drawer else 0)
        if typ != "upper_cabinet":
            c.line((X(it["t0"]), Y(4)), (X(it["t1"]), Y(4)), color, 1)
        if drawer:
            c.line((X(it["t0"]), Y(door_t)), (X(it["t1"]), Y(door_t)), color, 1)
        for i in range(1, doors):
            xx = it["t0"] + (it["t1"] - it["t0"]) * i / doors
            c.line((X(xx), Y(door_b)), (X(xx), Y(door_t)), color, 1)
        if typ == "base_cabinet" and it["raw"].get("countertop", True):
            c.rect(X(it["t0"] - 1), Y(t), X(it["t1"] + 1), Y(t + 1.5), fill=_tint(color, 0.6), outline=color, w=1)
            t += 1.5
    if b > 0:
        vmarks.append((b, it["bottom"][1], f"{_short(it)} bottom"))
    if t < H - 0.5:
        vmarks.append((t, it["top"][1], f"{_short(it)} top"))
    _elev_label(c, X, Y, it["t0"], it["t1"], b, t, it["label"] or TYPE_WORDS.get(typ, typ.replace("_", " ")),
                color if it["status"] != "existing" else MUTED, it["status"] != "existing")


def _short(it):
    word = it["raw"].get("short")
    if not word and it["type"] in ("appliance", "other") and it["label"]:
        word = it["label"]
    word = word or TYPE_WORDS.get(it["type"], it["type"].replace("_", " "))
    return word.split(" (")[0]


def _draw_finish(c, X, Y, L, H, f, cut_spans, vmarks):
    """Baseboard, crown, chair rail, wainscot, tile, backsplash, accent."""
    typ = str(f.get("type") or "").lower()
    status = _status(f)
    color = STATUS_COLOR[status]
    off = measure(f.get("offset"), f"{typ} offset") or (0.0, "dictated")
    wid = measure(f.get("width"), f"{typ} width")
    x0 = off[0]
    x1 = min(L, x0 + wid[0]) if wid else L
    profile = f.get("profile")
    if typ in ("baseboard", "base"):
        h = measure(f.get("height"), "baseboard height") or (3.5, "assumed")
        b, t = 0.0, h[0]
        name = "base"
    elif typ == "crown":
        h = measure(f.get("height"), "crown height") or (3.5, "assumed")
        b, t = H - h[0], H
        name = "crown"
    elif typ == "chair_rail":
        h = measure(f.get("height"), "chair rail height") or (36.0, "assumed")
        b, t = h[0] - 1.25, h[0] + 1.25
        name = "chair rail"
    else:
        top = measure(f.get("top") if f.get("top") != "ceiling" else {"in": H, "src": "dictated"}, f"{typ} top")
        top = top or measure(f.get("height"), f"{typ} height")
        bottom = measure(f.get("bottom"), f"{typ} bottom") or (0.0 if typ != "backsplash" else 36.0, "assumed")
        if top is None:
            top = (54.0 if typ == "backsplash" else 36.0 if typ == "wainscot" else H, "assumed")
        h = top
        b, t = bottom[0], top[0]
        name = {"wainscot": "wainscot", "tile": "tile", "backsplash": "backsplash"}.get(typ, typ or "finish")
        if bottom[0] > 0:
            vmarks.append((b, bottom[1], f"{name} bottom"))
    cuts = [(o0, o1) for o0, o1, ob, ot in cut_spans if ob < t - 0.1 and ot > b + 0.1]
    spans = _minus_spans(x0, x1, cuts)
    fill = _tint(color, 0.86 if typ in ("tile", "backsplash", "accent", "paint") else 0.9)
    dash = (8, 5) if status == "remove" else None
    for s0, s1 in spans:
        c.rect(X(s0), Y(b), X(s1), Y(t), fill=fill, outline=color, w=1, dash=dash)
        if typ == "wainscot":
            spacing = measure(f.get("spacing"), "wainscot spacing")
            if spacing and spacing[0] > 1:
                n = int((s1 - s0) // spacing[0])
                for i in range(1, n + 1):
                    xx = s0 + i * spacing[0]
                    if xx < s1 - 0.5:
                        c.line((X(xx), Y(b + 4)), (X(xx), Y(t - 3)), color, 1)
            c.line((X(s0), Y(t)), (X(s1), Y(t)), color, 2.2)
        if typ in ("tile", "backsplash"):
            size = measure(f.get("tile_size"), "tile size")
            if size and size[0] >= 2:
                hh = b + size[0]
                while hh < t - 0.5:
                    c.line((X(s0), Y(hh)), (X(s1), Y(hh)), _tint(color, 0.55), 1)
                    hh += size[0]
    top_word = {"base": "base", "crown": "crown bottom", "chair rail": "chair rail"}.get(name, f"{name} top")
    if typ == "crown":
        vmarks.append((b, h[1], top_word))
    elif typ == "chair_rail":
        vmarks.append((h[0], h[1], top_word))
    else:
        vmarks.append((t, h[1], top_word))
    if spans:
        s0, s1 = max(spans, key=lambda s: s[1] - s[0])
        word = f.get("label") or (f"{STATUS_WORDS[status]} {name}" if status != "existing" else name)
        if profile:
            word += f" - {profile}"
        if typ in ("tile", "wainscot", "backsplash") and not f.get("label"):
            word += f" to {label(t, h[1])}"
        anchor_y = Y(t) - 4 if (t - b) * 1 < 6 else (Y(b) + Y(t)) / 2
        c.text(X(s0) + 8, anchor_y, word, 12, color if status != "existing" else MUTED,
               "lb" if (t - b) < 6 else "lm", avoid=True, step=(0, -16))


# ── Whole file ───────────────────────────────────────────────────────────────

def render_drawings(data):
    """Draw every room in a room file.

    Returns {"drawings": [{"filename", "title", "kind", "room_id", "wall",
    "png"}...], "flags": [...]}. Raises DrawingError for a file that can't
    be drawn at all."""
    if not isinstance(data, dict):
        raise DrawingError("Room file must be a JSON object")
    schema = data.get("schema")
    if schema and schema != SCHEMA:
        raise DrawingError(f"Room file schema is {schema!r}; this code draws {SCHEMA!r}")
    rooms = data.get("rooms") or []
    if not rooms:
        raise DrawingError("Room file has no rooms")
    project = data.get("project") or {}
    shared_flags = list(data.get("flags") or [])
    drawings, all_flags = [], list(shared_flags)
    for i, raw in enumerate(rooms):
        room = parse_room(raw)
        # File-level open questions print on the first room's plan only.
        extra = shared_flags if i == 0 else []
        png, flags = render_plan(room, project, extra_flags=extra)
        all_flags += [f"{room['name']}: {f}" for f in flags if f not in extra]
        drawings.append({"filename": f"{room['id']}-floor-plan.png", "title": f"{room['name']} - Floor Plan",
                         "kind": "plan", "room_id": room["id"], "wall": None, "png": png})
        for w in room["walls"]:
            if needs_elevation(w):
                drawings.append({
                    "filename": f"{room['id']}-wall-{w['number']}-elevation.png",
                    "title": f"{room['name']} - Wall {w['number']} Elevation",
                    "kind": "elevation", "room_id": room["id"], "wall": w["number"],
                    "png": render_elevation(room, w, project),
                })
    return {"drawings": drawings, "flags": all_flags}


if __name__ == "__main__":
    # Draw a room file to a folder and report memory, e.g.
    #   python3 scope_drawings.py docs/site-scope/samples/test1-office-2026-10-19.json /tmp/drawings
    import json
    import os
    import resource
    import sys
    import time

    def _peak_mb():
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024

    src, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    with open(src) as f:
        room_file = json.load(f)
    before, t0 = _peak_mb(), time.time()
    drawn = render_drawings(room_file)
    for dwg in drawn["drawings"]:
        with open(os.path.join(out_dir, dwg["filename"]), "wb") as f:
            f.write(dwg["png"])
        print(f"{dwg['filename']}: {len(dwg['png']) // 1024} KB")
    for flag in drawn["flags"]:
        print(f"CHECK: {flag}")
    print(f"{time.time() - t0:.2f}s; peak memory {before:.0f} MB before drawing, {_peak_mb():.0f} MB after")
