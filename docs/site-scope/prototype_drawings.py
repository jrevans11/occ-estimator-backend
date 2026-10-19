# PROTOTYPE ONLY - drew the Oct 19, 2026 Test (1) drawings in a chat workspace.
# Geometry reference for scope_drawings.py. Do NOT deploy: renders via Playwright/Chromium (too heavy for the 512 MB Render plan).
# Usage there: python draw.py svgs.json  (render.py then screenshotted each SVG).
"""Test (1) office - floor plan + elevations, drawn per OCC floor-plan-drawing-rules.md.
All dimensions in inches. Sources: D = dictated, E = (est.) from photo, A = (assumed)."""
import json, sys

S = 3.0  # px per inch
GRAY, RED, BLUE, INK, MUTED = "#6b6b6b", "#c62828", "#1565c0", "#222", "#777"
FONT = "font-family='Inter, Arial, sans-serif'"

def fi(inches):
    inches = round(inches)
    return f"{inches // 12}'-{inches % 12}\""

TAG = {"D": "", "E": " (est.)", "A": " (assumed)"}
def lab(v, src):
    return fi(v) + TAG[src]

def txt(x, y, s, size=11, color=INK, anchor="middle", weight="normal", rot=None):
    t = f" transform='rotate({rot} {x:.1f} {y:.1f})'" if rot else ""
    s = s.replace("&", "&amp;").replace('"', "&quot;")
    return f"<text x='{x:.1f}' y='{y:.1f}' {FONT} font-size='{size}' fill='{color}' text-anchor='{anchor}' font-weight='{weight}'{t}>{s}</text>"

def line(x1, y1, x2, y2, color=INK, w=1, dash=None):
    d = f" stroke-dasharray='{dash}'" if dash else ""
    return f"<line x1='{x1:.1f}' y1='{y1:.1f}' x2='{x2:.1f}' y2='{y2:.1f}' stroke='{color}' stroke-width='{w}'{d}/>"

def rect(x, y, w, h, stroke=INK, fill="none", sw=1, dash=None, op=1):
    d = f" stroke-dasharray='{dash}'" if dash else ""
    return f"<rect x='{x:.1f}' y='{y:.1f}' width='{w:.1f}' height='{h:.1f}' stroke='{stroke}' fill='{fill}' stroke-width='{sw}' fill-opacity='{op}'{d}/>"

def tick(x, y):
    return line(x - 4, y + 4, x + 4, y - 4, INK, 1)

def hdim(x0, y, segs, offset_label=12):
    """segs: list of (length_in, label). Draw horizontal dimension string starting at x0 (px)."""
    out, x = [], x0
    total = sum(s[0] for s in segs) * S
    out.append(line(x0, y, x0 + total, y, INK, 0.8))
    out.append(tick(x, y))
    for L, label in segs:
        nx = x + L * S
        out.append(tick(nx, y))
        if L * S > len(label) * 5.6 + 6:
            out.append(txt((x + nx) / 2, y - 4, label, 9.5))
        else:
            out.append(txt((x + nx) / 2, y + offset_label, label, 9.5))
        x = nx
    return out

def vdim(x, y_floor, marks):
    """marks: list of (height_in, label) measured from floor. Ticks at each height, label beside."""
    out = []
    top = max(m[0] for m in marks)
    out.append(line(x, y_floor, x, y_floor - top * S, INK, 0.8))
    out.append(tick(x, y_floor))
    last = None
    for h, label in sorted(marks):
        yy = y_floor - h * S
        out.append(tick(x, yy))
        ly = yy + 3.5
        if last is not None and ly > last - 12:
            ly = last - 12
        out.append(txt(x + 8, ly, label, 9.5, anchor="start"))
        last = ly
    return out

# ---------------------------------------------------------------- data
W1, W2, W3 = 144, 156, 144          # dictated (W1/W3 12', W2 13')
W4, W4src = 156, "D"                 # confirmed by Jason: same as Wall 2
CEIL = 120                           # dictated 10'
CMU, STUD = 8, 4.5                   # CMU 8" (assumed - not in rules table), drywall default 4-1/2"

# ---------------------------------------------------------------- floor plan
def plan():
    ox, oy = 170, 110
    w, d = W1 * S, W2 * S
    o = []
    # walls: top (1, CMU), right (2, stud), bottom (3, CMU), left (4, CMU ext)
    t1, t2, t3, t4 = CMU * S, STUD * S, CMU * S, CMU * S
    o.append(rect(ox - t4, oy - t1, w + t4 + t2, t1, GRAY, "#cfcfcf", 1))
    o.append(rect(ox + w, oy - t1, t2, d + t1 + t3, GRAY, "#e6e6e6", 1))
    o.append(rect(ox - t4, oy + d, w + t4 + t2, t3, GRAY, "#cfcfcf", 1))
    o.append(rect(ox - t4, oy - t1, t4, d + t1 + t3, GRAY, "#cfcfcf", 1))
    o.append(rect(ox, oy, w, d, GRAY, "#fff", 1.5))

    # Wall 1 (top): existing door 32" (est.) starting 106" from left corner, hinge left jamb, swings into room
    dx0, dw = 106, 32
    o.append(rect(ox + dx0 * S, oy - t1 - 1, dw * S, t1 + 2, "#fff", "#fff", 0))
    hx, hy = ox + dx0 * S, oy
    o.append(line(hx, hy, hx, hy + dw * S, GRAY, 2))
    o.append(f"<path d='M {hx + dw*S:.1f} {hy:.1f} A {dw*S:.1f} {dw*S:.1f} 0 0 1 {hx:.1f} {hy + dw*S:.1f}' fill='none' stroke='{GRAY}' stroke-width='0.8' stroke-dasharray='3 3'/>")
    # Wall 1: NEW cased opening 32" starting 24" from left corner (assumed)
    nx0 = 24
    o.append(rect(ox + nx0 * S, oy - t1 - 1, dw * S, t1 + 2, "#fff", "#fff", 0))
    o.append(rect(ox + nx0 * S, oy - t1, dw * S, t1, BLUE, "none", 2))
    nhx = ox + (nx0 + dw) * S
    o.append(line(nhx, oy, nhx, oy + dw * S, BLUE, 2))
    o.append(f"<path d='M {ox + nx0*S:.1f} {oy:.1f} A {dw*S:.1f} {dw*S:.1f} 0 0 0 {nhx:.1f} {oy + dw*S:.1f}' fill='none' stroke='{BLUE}' stroke-width='0.8' stroke-dasharray='3 3'/>")
    o.append(txt(nhx + 6, oy + 40 * S, "NEW 2'-8\" door", 10, BLUE, anchor="start", weight="600"))
    o.append(txt(nhx + 6, oy + 40 * S + 12, "location + swing (assumed)", 9, BLUE, anchor="start"))
    o.append(txt(ox + (dx0 + dw / 2) * S, oy + dw * S + 14, "exist. door (est.)", 9, MUTED))
    # HVAC chase at left corner of wall 1 (high, dashed)
    o.append(rect(ox + 3 * S, oy, 20 * S, 10 * S, GRAY, "none", 1, "4 3"))
    o.append(txt(ox + 13 * S, oy + 44, "HVAC chase", 8.5, MUTED))
    o.append(txt(ox + 13 * S, oy + 54, "above (est.)", 8.5, MUTED))

    # Wall 2 (right): existing cased opening 36" (est.) starting 4" from top corner
    cy0, cw = 4, 36
    o.append(rect(ox + w - 1, oy + cy0 * S, t2 + 2, cw * S, "#fff", "#fff", 0))
    o.append(line(ox + w, oy + cy0 * S, ox + w + t2, oy + cy0 * S, GRAY, 1))
    o.append(line(ox + w, oy + (cy0 + cw) * S, ox + w + t2, oy + (cy0 + cw) * S, GRAY, 1))
    o.append(txt(ox + w - 8, oy + 46 * S, "exist. cased opening (est.)", 9, MUTED, anchor="end"))
    # interior window above, dashed in wall
    o.append(line(ox + w + t2 / 2, oy + 52 * S, ox + w + t2 / 2, oy + 124 * S, GRAY, 1.5, "5 3"))
    o.append(txt(ox + w - 8, oy + 88 * S, "interior window above (est.)", 9, MUTED, anchor="end"))
    # NEW outlets on wall 2 at 60" and 120" from left (top) corner (assumed)
    for p in (60, 120):
        cx, cy = ox + w - 7, oy + p * S
        o.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='5' fill='#fff' stroke='{BLUE}' stroke-width='1.6'/>")
        o.append(line(cx - 3, cy - 1.5, cx + 3, cy - 1.5, BLUE, 1.2)); o.append(line(cx - 3, cy + 1.5, cx + 3, cy + 1.5, BLUE, 1.2))
    o.append(txt(ox + w - 18, oy + 90 * S + 22, "NEW receptacles (2)", 9.5, BLUE, anchor="end", weight="600"))
    o.append(txt(ox + w - 18, oy + 90 * S + 34, "locations (assumed)", 9, BLUE, anchor="end"))

    # Wall 3 (bottom): NEW outlet at 84" from its left corner (= right side)
    px = ox + w - 84 * S
    cy = oy + d - 7
    o.append(f"<circle cx='{px:.1f}' cy='{cy:.1f}' r='5' fill='#fff' stroke='{BLUE}' stroke-width='1.6'/>")
    o.append(line(px - 3, cy - 1.5, px + 3, cy - 1.5, BLUE, 1.2)); o.append(line(px - 3, cy + 1.5, px + 3, cy + 1.5, BLUE, 1.2))
    o.append(txt(px, oy + d - 18, "NEW receptacle (location assumed)", 9.5, BLUE, weight="600"))

    # Wall 4 (left): window 96" starting 40" from its left corner (= bottom) - replace
    wy1 = oy + d - 40 * S
    wy0 = wy1 - 96 * S
    o.append(rect(ox - t4 - 1, wy0, t4 + 2, 96 * S, "#fff", "#fff", 0))
    o.append(rect(ox - t4, wy0, t4, 96 * S, RED, "none", 1.4, "5 3"))
    o.append(line(ox - t4 / 2, wy0, ox - t4 / 2, wy1, BLUE, 2.2))
    o.append(txt(ox + 10, (wy0 + wy1) / 2 - 6, "REPLACE window", 10, BLUE, anchor="start", weight="600"))
    o.append(txt(ox + 10, (wy0 + wy1) / 2 + 7, "8'-0\" W x 4'-0\" H", 9.5, BLUE, anchor="start"))

    # wall numbers
    def badge(x, y, n):
        return f"<circle cx='{x:.1f}' cy='{y:.1f}' r='10' fill='{INK}'/>" + txt(x, y + 4, str(n), 11, "#fff", weight="700")
    o.append(badge(ox + w / 2, oy - t1 - 34, 1))
    o.append(badge(ox + w + t2 + 74, oy + d / 2, 2))
    o.append(badge(ox + w / 2, oy + d + t3 + 58, 3))
    o.append(badge(ox - t4 - 120, oy + d / 2, 4))
    o.append(txt(ox - t4 - 120, oy + d / 2 + 26, "ext. CMU", 9, MUTED))

    # overall dims
    o += hdim(ox, oy - t1 - 14, [(nx0, lab(nx0, "A")), (dw, lab(dw, "D")), (dx0 - nx0 - dw, lab(dx0 - nx0 - dw, "E")), (dw, lab(dw, "E")), (W1 - dx0 - dw, lab(W1 - dx0 - dw, "E"))])
    o += hdim(ox, oy + d + t3 + 24, [(W3, "12'-0\"  Wall 3")])
    # vertical dims as rotated
    xr = ox + w + t2 + 30
    o.append(line(xr, oy, xr, oy + d, INK, 0.8)); o.append(tick(xr, oy)); o.append(tick(xr, oy + d))
    o.append(txt(xr + 12, oy + d / 2, "13'-0\"  Wall 2", 10, rot=90))
    xl = ox - t4 - 30
    o.append(line(xl, wy0, xl, wy1, INK, 0.8))
    for yy in (oy, wy0, wy1, oy + d):
        o.append(tick(xl, yy))
    o.append(line(xl, oy, xl, oy + d, INK, 0.8))
    o.append(txt(xl - 6, (oy + wy0) / 2, lab(W4 - 136, "E"), 9.5, rot=-90))
    o.append(txt(xl - 6, (wy0 + wy1) / 2, "8'-0\"", 9.5, rot=-90))
    o.append(txt(xl - 6, (wy1 + oy + d) / 2, lab(40, "E"), 9.5, rot=-90))
    o.append(txt(xl - 34, oy + d / 2, f"Wall 4: {lab(W4, W4src)}", 10, rot=-90))

    o.append(txt(ox + w / 2, oy + d / 2 - 6, "OFFICE", 13, INK, weight="700"))
    o.append(txt(ox + w / 2, oy + d / 2 + 10, "12'-0\" x 13'-0\"", 10, MUTED))
    o.append(txt(ox + w / 2, oy + d / 2 + 24, "Ceiling 10'-0\"", 10, MUTED))
    W, H = ox + w + t2 + 120, oy + d + t3 + 90
    return W, H, "".join(o)

# ---------------------------------------------------------------- elevations
def elevation(width, items, hmarks, segs, notes):
    ox, oy = 40, 30
    fy = oy + CEIL * S
    o = [rect(ox, oy, width * S, CEIL * S, GRAY, "#fafafa", 1.2), line(ox - 10, fy, ox + width * S + 10, fy, INK, 3)]
    o.append(txt(ox + width * S / 2, oy - 10, "Ceiling 10'-0\" (exposed structure)", 9.5, MUTED))
    for it in items:
        kind = it[0]
        if kind == "rect":
            _, x, h0, w, h, color, dash, label, lc = it[:9]
            lh = it[9] if len(it) > 9 else h0 + h / 2
            o.append(rect(ox + x * S, fy - (h0 + h) * S, w * S, h * S, color, "none", 1.8 if color != GRAY else 1.1, dash))
            if label:
                o.append(txt(ox + (x + w / 2) * S, fy - lh * S + 4, label, 9.5, lc or color, weight="600" if color == BLUE else "normal"))
        elif kind == "fill":
            _, x, h0, w, h, color, label = it
            o.append(rect(ox + x * S, fy - (h0 + h) * S, w * S, h * S, color, color, 1, "3 2", 0.12))
            o.append(txt(ox + (x + w / 2) * S, fy - (h0 + h / 2) * S + 4, label, 9, color))
        elif kind == "outlet":
            _, x, h0, color = it
            cx, cy = ox + x * S, fy - h0 * S
            o.append(rect(cx - 6, cy - 9, 12, 18, color, "#fff", 1.6))
            o.append(f"<circle cx='{cx:.1f}' cy='{cy-4:.1f}' r='2' fill='{color}'/><circle cx='{cx:.1f}' cy='{cy+4:.1f}' r='2' fill='{color}'/>")
        elif kind == "vline":
            _, x, h0, h1, color, dash = it
            o.append(line(ox + x * S, fy - h0 * S, ox + x * S, fy - h1 * S, color, 1.6, dash))
        elif kind == "label":
            _, x, h0, s, color, anchor = it
            o.append(txt(ox + x * S, fy - h0 * S, s, 9.5, color, anchor=anchor, weight="600" if color == BLUE else "normal"))
    o += hdim(ox, fy + 22, segs)
    o += vdim(ox + width * S + 22, fy, hmarks)
    import textwrap
    W = max(ox + width * S + 170, 700)
    y = fy + 50
    for n in notes:
        for ln in textwrap.wrap(n, int((W - ox - 10) / 5.3)):
            o.append(txt(ox, y, ln, 9.5, MUTED, anchor="start")); y += 14
    return W, y + 6, "".join(o)

def e1():
    items = [
        ("rect", 106, 0, 32, 80, GRAY, None, "exist. door", None),
        ("rect", 24, 0, 32, 80, BLUE, None, "NEW 2'-8\" door", None, 14),
        ("fill", 3, 82, 20, 38, GRAY, "HVAC chase"),
        ("rect", 20, 36, 51, 30, RED, "5 4", "relocate whiteboard", RED, 60),
        ("vline", 101, 0, 120, GRAY, None),
        ("outlet", 101, 14, GRAY), ("outlet", 101, 48, GRAY),
    ]
    hm = [(14, "exist. recept. (est.)"), (80, "6'-8\" head (assumed)"), (82, "HVAC chase bottom 6'-10\" (est.)"), (120, "10'-0\"")]
    segs = [(24, lab(24, "A")), (32, "2'-8\""), (50, lab(50, "E")), (32, lab(32, "E")), (6, lab(6, "E"))]
    notes = ["New opening gets a door (confirmed). HVAC chase (est. 3\"-23\" from left corner, bottom ~6'-10\") - Jason: opening will clear it.",
             "Existing whiteboard (est. 20\"-71\") overlaps the new door - remove/relocate. Block wall: saw cut + lintel. Location from corner and swing (assumed)."]
    return elevation(W1, items, hm, segs, notes)

def e2():
    items = [
        ("rect", 4, 0, 36, 80, GRAY, None, "exist. cased opening", MUTED),
        ("rect", 52, 86, 72, 24, GRAY, None, "interior window (est.)", MUTED),
        ("rect", 50, 36, 96, 48, GRAY, "4 3", "schedule whiteboard (est.)", MUTED),
        ("outlet", 60, 16, BLUE), ("outlet", 120, 16, BLUE),
        ("label", 90, 6, "NEW receptacles (2) - locations (assumed)", BLUE, "middle"),
    ]
    hm = [(16, "recept. 1'-4\" (assumed)"), (36, "3'-0\" (est.)"), (80, "6'-8\" (assumed)"), (84, "7'-0\" (est.)"), (110, "9'-2\" (est.)"), (120, "10'-0\"")]
    segs = [(4, lab(4, "E")), (36, lab(36, "E")), (20, lab(20, "A")), (60, lab(60, "A")), (36, lab(36, "A"))]
    notes = ["Painted drywall wall (stud cavity) - receptacles can be fished in. Desk return at right end.", "Open: power source / nearest circuit."]
    return elevation(W2, items, hm, segs, notes)

def e3():
    items = [
        ("rect", 22, 36, 96, 48, GRAY, "4 3", "Subcontractors whiteboard (est.)", MUTED),
        ("vline", 84, 84, 120, GRAY, None),
        ("vline", 84, 16, 36, BLUE, "4 3"),
        ("outlet", 84, 16, BLUE),
        ("label", 84, 4, "NEW receptacle (assumed loc.)", BLUE, "middle"),
    ]
    hm = [(16, "recept. 1'-4\" (assumed)"), (36, "3'-0\" (est.)"), (84, "7'-0\" (est.)"), (120, "10'-0\"")]
    segs = [(84, lab(84, "A")), (60, lab(60, "A"))]
    notes = ["Block wall - surface-mount EMT and box like the existing ones (confirmed), fed from existing conduit (gray) (route assumed).",
             "Wall 3 taken as the intended wall (suggested default, not changed). Location along the wall (assumed)."]
    return elevation(W3, items, hm, segs, notes)

def e4():
    items = [
        ("rect", 40, 36, 96, 48, BLUE, None, "REPLACE window 8'-0\" x 4'-0\"", None, 90),
        ("rect", 40, 36, 96, 48, RED, "5 4", None, None),
        ("vline", 88, 36, 84, BLUE, "4 3"),
    ]
    hm = [(36, "sill 3'-0\" (est.)"), (84, "head 7'-0\" (est.)"), (120, "10'-0\"")]
    segs = [(40, lab(40, "E")), (96, "8'-0\""), (20, lab(20, "E"))]
    notes = ["Exterior CMU wall. Wall length 13'-0\" (confirmed, same as Wall 2). Window W x H dictated; sill/head est. from 8\" block courses.",
             "Appears to be 2 sashes side by side (center dashed) - drapes hide the middle; confirm 1 twin unit vs 2 units.",
             "Red dashed = existing removed, blue = new, same size (confirmed). Move/reset drapes, cabinet, file cabinet."]
    return elevation(W4, items, hm, segs, notes)

def svg(W, H, body):
    return f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W:.0f} {H:.0f}' width='{W:.0f}' height='{H:.0f}'><rect width='100%' height='100%' fill='#fff'/>{body}</svg>"

out = {"plan": svg(*plan()), "e1": svg(*e1()), "e2": svg(*e2()), "e3": svg(*e3()), "e4": svg(*e4())}
json.dump(out, open(sys.argv[1], "w"))
print("ok")
