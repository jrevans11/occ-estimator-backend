"""Site Scope Tool - the JobTread budget (phase 6).

The last step of the build (scope_build). Works from the room file the write
step left (Jason's answers applied) and the scope Page content, the same way
the scope Page does. Split per the build plan ("AI vs. code split"):

    AI (one Claude call)  groups the scope by trade into cost groups, writes the
                          names, "Scope Includes:" lines and client NOTES, sizes
                          in-house labor hours, lists materials with a per-unit
                          retail guess, and picks the sub price basis (a rate
                          anchor, a bid Jason said, or nothing)
    code                  every number after that: quantities from
                          scope_quantities (Q1..), sub prices from the vendor
                          invoice-history CSVs (R1..), markups ($55/$89 labor,
                          materials x1.65, subs x1.45), the Home Depot catalog
                          match with its 3x case-price check (the estimator's
                          own), $0 (PRICING TBD) for licensed trades with no
                          bid or anchor, the internal NOTES item on every
                          group, and the JobTread writes

Writing: the job is queried fresh first. An empty budget gets the groups at
the top level. If the job already has budget groups (Jason's own, or an
earlier run's), the new ones go inside one parent group, "Site Scope Draft -
<date> Visit", so nothing already there is mixed in or touched. Each group is
created with all its line items in one call; if JobTread refuses that, the
group and items are created one by one (the estimator's proven way). Every
group written is saved on the run, so a retry writes only what's missing.

Render env SCOPE_BUDGET_JOB (optional, a job number) sends every budget to
that job instead of the run's own - for test runs on real client visits
(phase 8 accuracy runs), so a draft never lands in a real client's budget.

See docs/site-scope/site-scope-tool-build-plan.md (Phase 6 notes) and
site-visit-writeup-process.md ("Scope Page to JobTread budget mapping").
"""

import csv
import json
import math
import os
import re
import traceback
import urllib.error

import estimate_v2_draft as v2
import scope_quickpass as qp
import site_scope

_DIR = os.path.dirname(os.path.abspath(__file__))

LABOR_PRICE = 89.00
MAX_DESCRIPTION = 4096            # JobTread's limit on a group or item description
MAX_GROUP_NAME = 60
LICENSED = ("electrical", "plumbing", "hvac", "gas", "structural")

# OCC's JobTread units (organization 22P9ppHePJKP, read through the API Oct 20, 2026).
UNIT_IDS = {
    "ea": "22P9ppJU9udM", "sf": "22P9ppJUAHXj", "lf": "22P9ppJUAHXg", "hr": "22P9ppJU9udP",
    "gal": "22P9ppJU9udN", "sq": "22P9ppJUAHXk", "day": "22P9ppJU9udL", "ls": "22P9ppJUAHXh",
    "box": "22PErmsugJR5", "roll": "22PErkZGKaah",
}
UNIT_ALIASES = {"each": "ea", "pc": "ea", "piece": "ea", "unit": "ea", "sqft": "sf", "lft": "lf", "ft": "lf",
                "hrs": "hr", "hour": "hr", "hours": "hr", "gallon": "gal", "gallons": "gal", "days": "day",
                "lot": "ls", "job": "ls", "lump sum": "ls", "visit": "ls", "boxes": "box", "rolls": "roll",
                "squares": "sq"}
# Units bought in whole pieces (a quantity worked out from square feet is rounded up).
WHOLE_UNITS = {"ea", "gal", "box", "roll", "ls"}

# Vendor invoice histories in this repo -> rate anchors (R1..).
VENDOR_CSVS = (
    ("redland_electric_invoice_history.csv", "Redland Electric", "electrical"),
    ("crawlspace_medic_invoice_history.csv", "Crawlspace Medic", "crawlspace"),
    ("historical_crawlspace_medic_quotes.csv", "Crawlspace Medic", "crawlspace"),
    ("tile_with_style_invoice_history.csv", "Tile with Style", "tile"),
    ("greer_flooring_invoice_history.csv", "Greer Flooring", "flooring"),
    ("jordan_lumber_invoice_history.csv", "Jordan Lumber", "hardwood flooring"),
)
CALIBRATION_CSV = "consolidated_trade_group_calibration.csv"


class BudgetError(Exception):
    """A problem to show Jason as-is."""


# ── Text clean-up (OCC Estimate Generation Spec, language rules) ─────────────

_EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF☀-➿️]")


def clean_text(s):
    """No markdown, no emoji, spaced hyphen instead of a dash, one line."""
    s = str(s or "")
    s = re.sub(r"\s*[—–]\s*", " - ", s)
    s = _EMOJI_RE.sub("", s).replace("*", "").replace("`", "").replace("#", "")
    return " ".join(s.split())


def clean_name(s, limit=MAX_GROUP_NAME):
    s = clean_text(s).strip(" -")
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" -,&")
    return s


def group_description(scope_lines, notes):
    """The exact client format: "Scope Includes:", a blank line, the lines,
    then a blank line and "NOTES:" with its lines straight under it."""
    lines = [clean_text(x) for x in scope_lines if clean_text(x)]
    notes = [clean_text(x) for x in notes if clean_text(x)]
    out = "Scope Includes:\n\n" + "\n".join(f"- {x}" for x in lines) if lines else ""
    if notes:
        out += ("\n\n" if out else "") + "NOTES:\n" + "\n".join(f"- {x}" for x in notes)
    return out[:MAX_DESCRIPTION] or None


def _money(x):
    return f"${x:,.2f}"


def _num(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


# ── Reference data: rate anchors and in-house calibration ────────────────────

def _float(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _unit(u):
    u = str(u or "").strip().lower()
    u = UNIT_ALIASES.get(u, u)
    return u if u in UNIT_IDS else "ea"


def _rows(name):
    path = os.path.join(_DIR, name)
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError as e:
        print(f"Scope budget: rate anchors from {name} skipped ({e})")
        return []


def load_anchors():
    """Real sub prices OCC has paid, as {"R1": {...}}. The AI picks one by id;
    code multiplies its unit price - the AI never types a sub price from these."""
    anchors = []
    for name, vendor, trade in VENDOR_CSVS:
        for r in _rows(name):
            desc = r.get("line_item_description") or r.get("line_item") or ""
            if "unit_price" in r:          # per-unit lines (Redland, Tile with Style, Greer, Jordan)
                price, qty = _float(r.get("unit_price")), _float(r.get("quantity"))
            else:                          # flat per-scope lines (Crawlspace Medic)
                price, qty = _float(r.get("line_amount") or r.get("amount")), 1
            if not desc or not price or price <= 0:   # discounts and blanks aren't rates
                continue
            unit = _unit(r.get("unit")) if r.get("unit") else ("ea" if r.get("quantity") else "ls")
            ref = " ".join(x for x in (r.get("job_number") or r.get("quote_id") or "",
                                       r.get("invoice_date") or r.get("doc_date") or r.get("date") or "") if x)
            anchors.append({"vendor": vendor, "trade": trade, "ref": ref, "description": clean_text(desc)[:220],
                            "unit_price": round(price, 2), "unit": unit, "billed_qty": qty,
                            "note": clean_text(r.get("scope_notes") or r.get("notes") or "")[:200]})
    for r in _rows(CALIBRATION_CSV):
        cost = _float(r.get("sub_cost"))
        if (r.get("pricing_model") or "").strip() == "sub" and cost and cost > 0:
            anchors.append({"vendor": f"OCC trade group as Jason priced it ({r.get('source_job')})",
                            "trade": clean_text(r.get("group_name")), "ref": f"{r.get('findings_count')} findings",
                            "description": clean_text(r.get("scope_summary"))[:220], "unit_price": round(cost, 2),
                            "unit": "ls", "billed_qty": 1, "note": ""})
    return {f"R{i}": a for i, a in enumerate(anchors, 1)}


def anchors_text(anchors):
    out = []
    for rid, a in anchors.items():
        line = f"{rid} | {a['vendor']} ({a['trade']}) | {a['ref']} | {a['description']} | " \
               f"{_money(a['unit_price'])} per {a['unit']}"
        if a.get("billed_qty") not in (None, 1, 1.0):
            line += f" (billed qty {_num(a['billed_qty'])})"
        if a.get("note"):
            line += f" | {a['note']}"
        out.append(line)
    return "\n".join(out)


def calibration_text():
    out = []
    for r in _rows(CALIBRATION_CSV):
        if (r.get("pricing_model") or "").startswith("in_house") and (r.get("labor_hours") or r.get("material_cost_total")):
            bits = [f"{r.get('findings_count')} findings"]
            if r.get("labor_hours"):
                bits.append(f"{r['labor_hours']} labor hours")
            if r.get("material_cost_total"):
                bits.append(f"${r['material_cost_total']} materials at cost")
            out.append(f"- {clean_text(r.get('group_name'))} ({', '.join(bits)}): "
                       f"{clean_text(r.get('scope_summary'))[:200]}")
    return "\n".join(out)


# ── The AI call ──────────────────────────────────────────────────────────────

INSTRUCTIONS = """You are the budget step of Owners Choice Construction's Site Scope Tool. OCC is a residential repair and remodel contractor in Greenville, SC. Jason Evans (the owner) walked a job, dictated notes and took photos; earlier steps turned that into a room file, asked him a few questions, and wrote a scope Page. You now turn the scope into a JobTread budget: you group the work and size it. Code does all the arithmetic afterwards - it looks up the quantities and sub prices you point at, applies OCC's markups, checks material prices against the Home Depot catalog and writes the budget. So never apply a markup and never multiply anything yourself.

You get: the room file (scope_items S1..), the scope Page content (scope by trade, alternates), working quantities worked out by code (Q1..), measurement conflicts (C1..), missing info (M1..), and Jason's answers. Below: real sub prices OCC has paid (rate anchors R1..) and in-house calibration.

GROUPS
- One cost group per trade or area, in build order (site protection, demolition, framing and structure, plumbing, electrical, HVAC, insulation and drywall, doors, windows and trim, cabinets and countertops, tile, flooring, paint, exterior, cleanup). Group by trade, not by finding - a visit is usually 3 to 10 groups.
- Add "Site Protection & Debris Disposal" when work happens in a lived-in or furnished space (moving contents, floor protection, dust control, haul-off), and "Project Management" (coordination hours) when two or more trades or subs are involved.
- Every scope item goes in exactly one group: list its ids in "scope_items". Alternates go under "options", not in groups.
- "name": plain Title Case trade or area name, under 60 characters, no numbers, e.g. "Demolition and Framing", "Office Drywall and Paint".
- "cost_code": exactly one of: {cost_codes}.
- "scope_lines": verb-first lines describing the work (not the defect), in build sequence. A line that rests on an (est.) or (assumed) number says so.
- "client_notes": these print on client documents. In this order: what the price assumes (quantities, method); what is excluded (include the scope Page's "not in scope" items for that trade); what would change the price; one plain safety sentence only for a real hazard. Explain the reason, not just the disclaimer, and name tradeoffs honestly (new paint matches closely, never exactly; patched drywall can show under raking light). No prices, no doubts meant for Jason, no "Jason".
- "internal_notes": anything Jason should check before sending that isn't already a conflict or missing-info item.
- "conflicts": ["C2"] and "missing_info": ["M1"] - the ones that affect this group.

PRICING EACH GROUP - a group may mix these.
1. In-house labor, "labor": [{"task": "frame and drywall the new opening", "trade": "carpentry", "hours": 6}]. Hours as the crew would really spend them for the whole group, including setup, protection, ladder moves, cleanup and return trips (drywall mud and paint coats need separate days). Summing small per-item guesses comes out low. OCC's crew does drywall, paint, carpentry, trim, doors and windows, small exterior and roofing repairs, and small flooring or tile patches.
2. Materials, "materials": [{"item": a plain product name a Home Depot search would find, "unit": "ea", "sf", "lf", "gal", "box" or "roll", "unit_cost": your per-unit retail price before markup, plus the quantity}]. Per-unit prices, never a case or pack price unless the unit is "box". Include the small stuff (fasteners, caulk, tape, mud, primer). A whole group's materials under about $75 usually means something was left out.
3. Subcontractor, "sub_lines": [{"description": "what the sub does", plus one price basis}]. One line per sub visit - bundle a sub's several items into one visit or day rate, the way subs bill. Name the sub in "sub" on the group when Jason addressed one. Price basis, exactly one:
   - "anchor": "R12" with the quantity in that anchor's unit - only when the anchor is the same kind of work.
   - "bid": the dollars OCC pays, with "bid_source": the note or answer it came from ("N3", "Q2") - only when Jason said a sub's price.
   - "estimate": your dollar estimate of OCC's cost from the sub - only for trades that are not licensed (tile, flooring, crawlspace, masonry, roofing).
   - none of these: leave all three out, and code writes a $0 (PRICING TBD) line.
OCC subs out: all electrical; major plumbing and HVAC; gas and fireplaces; structural engineering; crawlspace moisture work; full-room flooring or tile; significant masonry. Everything else is in-house.
LICENSED TRADES (electrical, plumbing, HVAC, gas, structural): never estimate a price and never price the work as in-house labor. Use an anchor of the same kind of work, a bid Jason gave, or leave it for PRICING TBD. Set "licensed_trade" on the group to one of electrical, plumbing, hvac, gas, structural - else null. In-house support work in that group (patching the drywall the electrician opened) may still be labor and materials.

QUANTITIES
For materials and per-unit sub lines, point at a working quantity whenever one fits instead of typing a number: "qty_ref": "Q3", with optional "per" (how many of the quantity's units one purchase unit covers: 350 sf per gallon, 32 sf per drywall sheet, 8 lf per stick), "factor" (2 coats) and "waste" (0.10). Code does the math and keeps the quantity's source label. Otherwise give "qty" and "qty_src": "dictated", "est" or "assumed".

OPTIONS
Each alternate on the scope Page becomes an options set: {"name": "<Area> Options", "kind": "add_on" (an optional extra the client may add) or "either_or" (the client picks exactly one way to do the same work - then that work is only in the options set, not also in a group), "choices": [groups in the same shape as above, "name" = the choice's short descriptor]}. Each choice carries its full scope and states its own tradeoff in client_notes; the cheapest choice names what the client gives up. [] if there are no alternates.

WRITING RULES
No markdown, asterisks or emoji. A spaced hyphen " - ", never a dash. Feet and inches as 13'-2". "The client", not "you". Spell out Owners Choice Construction. No ALL CAPS.

OUTPUT
Reply with one JSON object and nothing else:
{"groups": [{"name", "cost_code", "scope_items", "scope_lines", "client_notes", "internal_notes", "conflicts", "missing_info", "licensed_trade", "sub", "labor", "materials", "sub_lines"}], "options": [{"name", "kind", "choices": [groups]}]}
"""


def system_blocks(anchors):
    text = INSTRUCTIONS.replace("{cost_codes}", ", ".join(v2.COST_CODE_MAP))
    text += "\n\n===== RATE ANCHORS (real sub prices OCC paid; price per unit) =====\n" + (anchors_text(anchors) or "(none)")
    cal = calibration_text()
    if cal:
        text += ("\n\n===== IN-HOUSE CALIBRATION (whole trade groups as Jason priced them, closing repairs; "
                 "labor at $55/hr cost) =====\n" + cal)
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def missing_items(room_file, page):
    """The scope Page's missing info plus the room file's open flags, deduped
    (the same list the Page's checklist shows)."""
    out, seen = [], set()
    flags = list(room_file.get("flags") or []) + [f"{r.get('name')}: {f}" for r in room_file.get("rooms") or []
                                                  for f in r.get("flags") or []]
    for item in list(page.get("missing_info") or []) + flags:
        key = " ".join(str(item).lower().split())
        if key and key not in seen:
            seen.add(key)
            out.append(str(item))
    return out


def reference_lists(room_file, page, quantities):
    """The numbered lists the AI points at: Q (quantities), C (conflicts), M (missing info)."""
    qs = {f"Q{i}": q for i, q in enumerate(quantities, 1)}
    cs = {f"C{i}": c for i, c in enumerate([c for c in room_file.get("conflicts") or [] if isinstance(c, dict)], 1)}
    ms = {f"M{i}": m for i, m in enumerate(missing_items(room_file, page), 1)}
    return qs, cs, ms


SRC_TAG = {"dictated": "", "est": " (est.)", "assumed": " (assumed)"}


def build_content(run, room_file, page, qs, cs, ms):
    job = run["job"]
    rf = {k: room_file.get(k) for k in ("client_goals", "scope_items", "conflicts")}
    rf["rooms"] = [r.get("name") for r in room_file.get("rooms") or []]
    qa = "\n".join(f"{a['id']}. {a['question']} - {a['answer'] or '(left blank)'}"
                   for a in run.get("answers") or []) or "(No questions were needed.)"
    q_lines = "\n".join(f"{qid}: {q['item']} = {_num(q['value'])} {q['unit']}{SRC_TAG.get(q['src'], '')} "
                        f"({q['basis']})" for qid, q in qs.items()) or "(none - no rooms were dictated wall by wall)"
    c_lines = "\n".join(f"{cid}: {c.get('what')} - {c.get('source_a')} vs. {c.get('source_b')} - {c.get('action')}"
                        for cid, c in cs.items()) or "(none)"
    m_lines = "\n".join(f"{mid}: {m}" for mid, m in ms.items()) or "(none)"
    visit = site_scope._pretty_date(run["visit_date"]) if run.get("visit_date") else "the site visit"
    return [
        {"type": "text", "text": f"Job {job.get('number')} - {job.get('name') or ''}, "
                                 f"{job.get('address') or 'no address on file'}. Site visit on {visit}."},
        {"type": "text", "text": "THE ROOM FILE (answers applied):\n" + json.dumps(rf, indent=1)},
        {"type": "text", "text": "THE SCOPE PAGE CONTENT:\n" + json.dumps(page, indent=1)},
        {"type": "text", "text": "JASON'S ANSWERS:\n" + qa},
        {"type": "text", "text": "WORKING QUANTITIES (code):\n" + q_lines},
        {"type": "text", "text": "MEASUREMENT CONFLICTS:\n" + c_lines},
        {"type": "text", "text": "MISSING INFO:\n" + m_lines},
        {"type": "text", "text": "Write the budget now, as one JSON object."},
    ]


def _list(v):
    return v if isinstance(v, list) else []


def check_plan(obj, anchors, qs):
    """Shape problems that would break the math are sent back to the AI once."""
    problems = []
    groups = [g for g in _list(obj.get("groups")) if isinstance(g, dict)]
    choices = [c for o in _list(obj.get("options")) if isinstance(o, dict)
               for c in _list(o.get("choices")) if isinstance(c, dict)]
    for g in groups + choices:
        label = g.get("name") or "a group"
        if not str(g.get("name") or "").strip():
            problems.append("a group has no name")
        for line in _list(g.get("materials")) + _list(g.get("sub_lines")):
            if not isinstance(line, dict):
                problems.append(f"{label}: a line isn't an object")
                continue
            ref = line.get("qty_ref")
            if ref and ref not in qs:
                problems.append(f"{label}: qty_ref {ref} isn't in the working quantities")
            anchor = line.get("anchor")
            if anchor and anchor not in anchors:
                problems.append(f"{label}: anchor {anchor} isn't in the rate anchors")
    return problems


def parse_plan(anchors, qs):
    def parse(text):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in the reply")
        obj = json.loads(text[start:end + 1])
        if not isinstance(obj, dict) or not isinstance(obj.get("groups"), list):
            raise ValueError("the reply has no groups list")
        problems = check_plan(obj, anchors, qs)
        if problems:
            raise ValueError("; ".join(problems[:8]))
        return obj
    return parse


def ask_for_plan(run, room_file, page, qs, cs, ms, anchors, log=print):
    label = f"Scope budget job {run['job'].get('number')}"
    try:
        return qp.ask_for_json(system_blocks(anchors), build_content(run, room_file, page, qs, cs, ms),
                               parse_plan(anchors, qs), label, log,
                               refused="Claude declined to write this budget. Tap Build to try again.",
                               retry_hint="Tap Build to try again.", check=None)
    except (qp.QuickPassError, qp.ClaudeError) as e:
        raise BudgetError(str(e))


# ── Pricing (code) ───────────────────────────────────────────────────────────

def _src(s):
    s = str(s or "").strip().lower()
    return "dictated" if s in ("dictated", "confirmed") else ("assumed" if s == "assumed" else "est")


def resolve_qty(line, qs, unit):
    """(quantity, source, basis sentence or None). A qty_ref is worked out here:
    quantity x factor x (1 + waste) / per, rounded up for whole pieces."""
    ref = line.get("qty_ref")
    if ref and ref in qs:
        q = qs[ref]
        per = _float(line.get("per")) or 1.0
        factor = _float(line.get("factor")) or 1.0
        waste = min(max(_float(line.get("waste")) or 0.0, 0.0), 0.5)
        value = q["value"] * factor * (1 + waste) / (per if per > 0 else 1.0)
        value = math.ceil(value - 1e-9) if unit in WHOLE_UNITS else round(value, 1)
        how = []
        if factor != 1:
            how.append(f"x {_num(factor)}")
        if waste:
            how.append(f"+ {round(waste * 100)}% waste")
        if per != 1:
            how.append(f"/ {_num(per)} {q['unit']} per {unit}")
        basis = f"{ref} {q['item']} {_num(q['value'])} {q['unit']}{SRC_TAG.get(q['src'], '')}" + \
                (" " + " ".join(how) if how else "")
        return max(value, 0), q["src"], basis
    qty = _float(line.get("qty"))
    if qty is None or qty <= 0:
        return 0, "assumed", None
    return qty, _src(line.get("qty_src")), None


def _licensed(group, scope_by_id):
    lt = str(group.get("licensed_trade") or "").strip().lower()
    if lt in ("", "none", "null", "false", "no"):
        lt = ""
    for sid in _list(group.get("scope_items")):
        s = scope_by_id.get(sid) or {}
        v = str(s.get("licensed_trade") or "").strip().lower()
        if v and v not in ("none", "null", "false", "no") and not lt:
            lt = v
    return lt


def price_group(g, qs, anchors, scope_by_id):
    """One AI group -> a priced group: client name/description, line items with
    cost and price, and the facts for its internal NOTES. No API calls."""
    name = clean_name(g.get("name")) or "Scope"
    cost_code = str(g.get("cost_code") or "").strip()
    licensed = _licensed(g, scope_by_id)
    items, notes = [], []

    for line in _list(g.get("labor")):
        if not isinstance(line, dict):
            continue
        hours = _float(line.get("hours"))
        if not hours or hours <= 0:
            continue
        task = clean_text(line.get("task") or line.get("trade") or "labor")
        items.append({"kind": "labor", "name": f"Labor - {task}"[:100], "qty": round(hours, 2), "unit": "hr",
                      "unit_cost": v2.LABOR_COST_RATE, "unit_price": LABOR_PRICE, "cost_type": v2.COST_TYPE_LABOR})

    materials = []
    for line in _list(g.get("materials")):
        if not isinstance(line, dict) or not clean_text(line.get("item")):
            continue
        unit = _unit(line.get("unit"))
        qty, src, basis = resolve_qty(line, qs, unit)
        unit_cost = _float(line.get("unit_cost"))
        if qty <= 0 or not unit_cost or unit_cost <= 0:
            notes.append(f"Material left out (no quantity or price): {clean_text(line.get('item'))}")
            continue
        m = {"item": clean_text(line.get("item"))[:100], "qty": qty, "unit_cost": round(unit_cost, 2),
             "unit": unit, "src": src, "basis": basis}
        materials.append(m)
        if basis or src != "dictated":
            notes.append(f"{m['item']}: {_num(qty)} {unit}" + (f" from {basis}" if basis else SRC_TAG[src]))

    for line in _list(g.get("sub_lines")):
        if not isinstance(line, dict):
            continue
        desc = clean_text(line.get("description")) or f"{name} - subcontractor"
        anchor = anchors.get(line.get("anchor")) if line.get("anchor") else None
        bid, est = _float(line.get("bid")), _float(line.get("estimate"))
        cost, why, qty, unit = None, None, 1, "ls"
        if anchor:
            qty, src, basis = resolve_qty(line, qs, anchor["unit"])
            qty, unit = qty or 1, anchor["unit"]
            cost = anchor["unit_price"]
            why = (f"Rate anchor {line['anchor']}: {anchor['vendor']} {anchor['ref']} - {anchor['description'][:90]} - "
                   f"{_money(cost)} per {unit} x {_num(qty)}" + (f" ({basis})" if basis else SRC_TAG[src])
                   + " = " + _money(round(cost * qty, 2)))
        elif bid and bid > 0:
            cost = round(bid, 2)
            why = f"Sub bid {_money(cost)} as Jason said it ({clean_text(line.get('bid_source')) or 'source not given'})."
        elif est and est > 0 and not licensed:
            cost = round(est, 2)
            why = f"AI estimate of the sub's price, no bid or rate anchor: {_money(cost)} - confirm with the sub."
        if cost:
            items.append({"kind": "sub", "name": desc[:100], "qty": qty, "unit": unit, "unit_cost": cost,
                          "unit_price": round(cost * v2.SUB_MARKUP, 2), "cost_type": v2.COST_TYPE_SUB})
            notes.append(why)
        else:
            reason = (f"{licensed} is a licensed trade and there is no bid or rate anchor for it" if licensed
                      else "there is no bid, rate anchor or estimate for it")
            items.append({"kind": "tbd", "name": f"{desc[:80]} (PRICING TBD)", "qty": 1, "unit": "ls",
                          "unit_cost": 0, "unit_price": 0, "cost_type": v2.COST_TYPE_SUB})
            notes.append(f"PRICING TBD on '{desc[:80]}': {reason}. Get a sub price before sending.")

    if licensed and not any(i["kind"] in ("sub", "tbd") for i in items):
        items.append({"kind": "tbd", "name": f"{name} - licensed {licensed} work (PRICING TBD)"[:100], "qty": 1,
                      "unit": "ls", "unit_cost": 0, "unit_price": 0, "cost_type": v2.COST_TYPE_SUB})
        notes.append(f"PRICING TBD: {licensed} is a licensed trade and no sub line was priced. Get a sub price.")
    if licensed and any(i["kind"] == "labor" for i in items):
        notes.append(f"Check: in-house labor is in a licensed-trade ({licensed}) group - it should be support "
                     "work only (patching, protection), not the licensed work itself.")
    return {"name": name, "cost_code": cost_code, "licensed": licensed or None, "sub": clean_text(g.get("sub")) or None,
            "scope_items": [s for s in _list(g.get("scope_items")) if isinstance(s, str)],
            "description": group_description(_list(g.get("scope_lines")), _list(g.get("client_notes"))),
            "items": items, "materials": materials, "notes": notes,
            "internal_notes": [clean_text(x) for x in _list(g.get("internal_notes")) if clean_text(x)],
            "conflicts": [c for c in _list(g.get("conflicts")) if isinstance(c, str)],
            "missing_info": [m for m in _list(g.get("missing_info")) if isinstance(m, str)]}


def apply_catalog(groups, jobtread_query, org_id, log=print):
    """The estimator's Home Depot match, with its plausibility and 3x
    case-price checks, run on every material line. A confident, sane match
    replaces the AI's guess; anything else keeps the guess and gets flagged."""
    estimate = {"cost_groups": [{"material_lines": g["materials"]} for g in groups]}
    try:
        _, stats = v2.resolve_material_lines_with_catalog(estimate, jobtread_query, org_id)
        log(f"Scope budget: Home Depot catalog {stats}")
        return stats
    except Exception as e:   # the catalog must never block the budget
        log(f"Scope budget: catalog check skipped ({e})")
        for g in groups:
            for m in g["materials"]:
                m["catalog_no_match"] = True
        return {"error": str(e)}


def finish_materials(group):
    """Material line items from the (catalog-checked) material lines."""
    for m in group["materials"]:
        item = {"kind": "material", "name": m["item"], "qty": m["qty"], "unit": m["unit"],
                "unit_cost": round(float(m["unit_cost"]), 2),
                "unit_price": round(float(m["unit_cost"]) * v2.MATERIAL_MARKUP, 2),
                "cost_type": v2.COST_TYPE_MATERIALS}
        desc = v2._build_material_description(m)
        if desc:
            item["description"] = desc[:MAX_DESCRIPTION]
            item["show_description"] = not v2._is_internal_material_description(m)
        if m.get("catalog_match"):
            fields = v2._build_material_custom_field_values(m)
            if fields:
                item["custom_fields"] = fields
        group["items"].append(item)
    group["notes"] += v2._internal_note_flags({"material_lines": group["materials"]})


def enforce_minimum_hours(groups):
    """OCC bills at least 3 in-house hours a job (estimate_v2_draft's rule)."""
    hours = sum(i["qty"] for g in groups for i in g["items"] if i["kind"] == "labor")
    short = round(v2.MINIMUM_INHOUSE_LABOR_HOURS - hours, 2)
    if 0 < hours and short > 0:
        g = next(g for g in groups if any(i["kind"] == "labor" for i in g["items"]))
        g["items"].append({"kind": "labor", "name": "Labor - minimum charge, trip and setup time", "qty": short,
                           "unit": "hr", "unit_cost": v2.LABOR_COST_RATE, "unit_price": LABOR_PRICE,
                           "cost_type": v2.COST_TYPE_LABOR})
        g["notes"].append(f"Added {_num(short)} hr to reach OCC's {_num(v2.MINIMUM_INHOUSE_LABOR_HOURS)}-hour "
                          "minimum for the job.")


def totals(items):
    cost = round(sum(i["qty"] * i["unit_cost"] for i in items), 2)
    price = round(sum(i["qty"] * i["unit_price"] for i in items), 2)
    return cost, price


def internal_notes_text(group, cs, ms, source_line):
    parts = [source_line]
    if group.get("sub"):
        parts.append(f"Sub: {group['sub']}.")
    parts += group["notes"] + group["internal_notes"]
    for cid in group["conflicts"]:
        c = cs.get(cid)
        if c:
            parts.append(f"Conflict {cid}: {c.get('what')} - {c.get('source_a')} vs. {c.get('source_b')} - "
                         f"{c.get('action')}")
    for mid in group["missing_info"]:
        if mid in ms:
            parts.append(f"Missing info {mid}: {ms[mid]}")
    for line in group.get("extra_notes") or []:
        parts.append(line)
    parts.append("Labor hours, material prices and AI estimates are the Site Scope Tool's - review before sending.")
    text = "\n".join(f"- {clean_text(p)}" for p in parts if clean_text(p))
    return text[:MAX_DESCRIPTION]


def price_plan(plan, room_file, qs, cs, ms, anchors, source_line, jobtread_query, org_id, log=print):
    """The AI's plan -> priced groups and option sets, with internal NOTES.
    Returns {"groups": [...], "options": [...], "warnings": [...]}."""
    scope_by_id = {s.get("id"): s for s in room_file.get("scope_items") or [] if isinstance(s, dict)}
    groups = [price_group(g, qs, anchors, scope_by_id) for g in _list(plan.get("groups")) if isinstance(g, dict)]
    options = []
    for o in _list(plan.get("options")):
        if not isinstance(o, dict):
            continue
        choices = [price_group(c, qs, anchors, scope_by_id) for c in _list(o.get("choices")) if isinstance(c, dict)]
        if choices:
            kind = "either_or" if str(o.get("kind") or "").lower() == "either_or" else "add_on"
            options.append({"name": clean_name(o.get("name")) or "Options", "kind": kind, "choices": choices})
    every = groups + [c for o in options for c in o["choices"]]
    apply_catalog(every, jobtread_query, org_id, log)
    for g in every:
        finish_materials(g)
    enforce_minimum_hours(groups)

    # Code checks: every scope item placed once (options may repeat - they're alternatives).
    warnings, placed = [], {}
    for g in groups:
        for sid in g["scope_items"]:
            placed.setdefault(sid, []).append(g["name"])
    for sid, names in placed.items():
        if len(names) > 1:
            warnings.append(f"{sid} is in {len(names)} groups ({', '.join(names)}) - check for a double charge")
            for g in groups:
                if g["name"] in names:
                    g.setdefault("extra_notes", []).append(
                        f"Check: scope item {sid} is also in {', '.join(n for n in names if n != g['name'])}.")
    in_options = {sid for o in options for c in o["choices"] for sid in c["scope_items"]}
    unplaced = [s for sid, s in scope_by_id.items() if sid and sid not in placed and sid not in in_options]
    if groups:
        first = groups[0].setdefault("extra_notes", [])
        for s in unplaced:
            warnings.append(f"{s.get('id')} ({s.get('work')}) isn't in any group")
            first.append(f"Not budgeted anywhere: {s.get('id')} - {s.get('area') or ''} {s.get('work') or ''}")
        used_c = {c for g in every for c in g["conflicts"]}
        used_m = {m for g in every for m in g["missing_info"]}
        for cid, c in cs.items():
            if cid not in used_c:
                first.append(f"Whole visit - conflict {cid}: {c.get('what')} - {c.get('source_a')} vs. "
                             f"{c.get('source_b')} - {c.get('action')}")
        for mid, m in ms.items():
            if mid not in used_m:
                first.append(f"Whole visit - missing info {mid}: {m}")

    for g in every:
        g["notes_text"] = internal_notes_text(g, cs, ms, source_line)
        g["cost"], g["price"] = totals(g["items"])
    return {"groups": groups, "options": options, "warnings": warnings}


# ── JobTread writes ──────────────────────────────────────────────────────────

def _jt_error(e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        return f"JobTread answered HTTP {e.code}{': ' + body if body else ''}"
    return str(e) or e.__class__.__name__


def _item_args(item, cost_code_id, full=True):
    args = {"name": item["name"][:250], "quantity": item["qty"], "unitCost": item["unit_cost"],
            "unitPrice": item["unit_price"], "costCodeId": cost_code_id, "costTypeId": item["cost_type"]}
    if item.get("description"):
        args["description"] = item["description"]
        args["showDescription"] = bool(item.get("show_description", True))
    if full:
        if UNIT_IDS.get(item.get("unit")):
            args["unitId"] = UNIT_IDS[item["unit"]]
        if item.get("custom_fields"):
            args["customFieldValues"] = item["custom_fields"]
    return args


def node_items(group):
    """A priced group's line items plus its internal NOTES item."""
    notes = {"kind": "notes", "name": v2.INTERNAL_NOTES_ITEM_NAME, "qty": 1, "unit": "ea", "unit_cost": 0,
             "unit_price": 0, "cost_type": v2.COST_TYPE_OTHER, "description": group["notes_text"],
             "show_description": False}
    return group["items"] + [notes]


def build_tree(priced):
    """Nodes to write, keyed so a retry can tell what's already in JobTread."""
    nodes = []
    for i, g in enumerate(priced["groups"]):
        nodes.append({"key": f"g{i}", "name": g["name"], "description": g["description"],
                      "cost_code_id": v2.resolve_cost_code_id(g["cost_code"], context=g["name"]),
                      "items": node_items(g), "children": []})
    for i, o in enumerate(priced["options"]):
        children = []
        for j, c in enumerate(o["choices"], 1):
            children.append({"key": f"o{i}/{j}", "name": clean_name(f"Option {j} - {c['name']}"),
                             "description": c["description"],
                             "cost_code_id": v2.resolve_cost_code_id(c["cost_code"], context=c["name"]),
                             "items": node_items(c), "children": []})
        # A selection parent's line items are the choices, so it gets no NOTES item of its own.
        nodes.append({"key": f"o{i}", "name": o["name"], "description": None, "cost_code_id": None, "items": [],
                      "children": children, "min": 1 if o["kind"] == "either_or" else 0, "max": 1})
    return nodes


def _group_args(node, job_id, parent_id):
    args = {"jobId": job_id, "name": node["name"][:250]}
    if node.get("description"):
        args["description"] = node["description"]
    if parent_id:
        args["parentCostGroupId"] = parent_id
    if node.get("max"):
        args["minSelectionsRequired"] = node["min"]
        args["maxSelectionsAllowed"] = node["max"]
    return args


def _line_items(node):
    out = [dict(_item_args(it, node["cost_code_id"]), _type="costItem") for it in node["items"]]
    for child in node["children"]:
        c = {"_type": "costGroup", "name": child["name"][:250], "lineItems": _line_items(child)}
        if child.get("description"):
            c["description"] = child["description"]
        if child.get("max"):
            c.update(minSelectionsRequired=child["min"], maxSelectionsAllowed=child["max"])
        out.append(c)
    return out


def _all_keys(node):
    yield node["key"]
    for c in node["children"]:
        yield from _all_keys(c)


def _created_id(resp, what):
    try:
        return resp[f"create{what}"][f"created{what}"]["id"]
    except (KeyError, TypeError):
        raise BudgetError(f"JobTread didn't return an id for the new {what.lower()}.")


def write_node(node, job_id, parent_id, written, query, save, log, one_call=True):
    """Write one group (and its children). written[key] records progress:
    {"id", "items": [indexes done], "done"}. Returns True when fully written."""
    rec = written.get(node["key"])
    if rec and rec.get("done"):
        return True
    if not rec and one_call:
        try:
            args = dict(_group_args(node, job_id, parent_id), lineItems=_line_items(node))
            gid = _created_id(query({"createCostGroup": {"$": args, "createdCostGroup": {"id": {}}}}), "CostGroup")
            for k in _all_keys(node):
                written[k] = {"id": gid if k == node["key"] else None, "done": True, "how": "one call"}
            save()
            return True
        except Exception as e:
            log(f"Scope budget: one-call write of '{node['name']}' refused ({_jt_error(e)}); writing it piece by piece")
    if not rec:
        try:
            gid = _created_id(query({"createCostGroup": {"$": _group_args(node, job_id, parent_id),
                                                         "createdCostGroup": {"id": {}}}}), "CostGroup")
        except Exception as e:
            raise BudgetError(f"Couldn't create the group '{node['name']}' ({_jt_error(e)}).")
        rec = written[node["key"]] = {"id": gid, "items": [], "done": False, "how": "piece by piece"}
        save()
    ok = True
    for idx, item in enumerate(node["items"]):
        if idx in rec["items"]:
            continue
        args = dict(_item_args(item, node["cost_code_id"], full=False), costGroupId=rec["id"])
        try:
            query({"createCostItem": {"$": args, "createdCostItem": {"id": {}}}})
            rec["items"].append(idx)
        except Exception as e:
            ok = False
            log(f"Scope budget: line '{item['name'][:60]}' in '{node['name']}' failed ({_jt_error(e)})")
    for child in node["children"]:
        try:
            ok = write_node(child, job_id, rec["id"], written, query, save, log, one_call=False) and ok
        except BudgetError as e:
            ok = False
            log(f"Scope budget: {e}")
    rec["done"] = ok
    save()
    return ok


# ── The step ─────────────────────────────────────────────────────────────────

def _budget_groups(job_id, query):
    """The job's budget groups right now (estimate documents carry copies; those are left out)."""
    try:
        resp = query({"job": {"$": {"id": job_id}, "id": {}, "number": {}, "name": {},
                              "costGroups": {"$": {"size": 100, "where": [["document", "id"], None]},
                                             "nodes": {"id": {}, "name": {}, "parentCostGroup": {"id": {}}}}}})
    except Exception as e:
        raise BudgetError(f"Couldn't read job {job_id}'s budget in JobTread ({_jt_error(e)}).")
    job = (resp or {}).get("job")
    if not job:
        raise BudgetError(f"JobTread has no job {job_id}.")
    return job, ((job.get("costGroups") or {}).get("nodes") or [])


def target_job(run, query, org_id):
    """The run's job, or the stand-in job in SCOPE_BUDGET_JOB."""
    override = (os.environ.get("SCOPE_BUDGET_JOB") or "").strip()
    if not override:
        return {"id": run["job"]["id"], "number": run["job"].get("number"), "stand_in": False}
    try:
        job = site_scope.find_job(override, query, org_id)
    except Exception as e:
        raise BudgetError(f"SCOPE_BUDGET_JOB is set to '{override}', but that job couldn't be found ({e}).")
    return {"id": job["id"], "number": job.get("number"), "stand_in": True}


def _quantities(room_file, log):
    try:
        import scope_quantities   # loads Pillow (through scope_drawings), so only here
        return scope_quantities.all_quantities(room_file)
    except Exception as e:   # a quantity bug costs the references, not the budget
        log(f"Scope budget: quantities failed: {e}\n{traceback.format_exc()}")
        return []


def run_budget(run, st, page_title, save, jobtread, log=print):
    """The budget step. st is the step's dict on the run, updated in place and
    saved as it goes (plan and prices first, then each group written).
    Returns st. Raises BudgetError for a problem that stops it."""
    if not jobtread or not jobtread[0]:
        raise BudgetError("The build wasn't given a JobTread connection (server problem). Tap Build to try again.")
    query, org_id = jobtread
    room_file, page = run["room_file"], (run.get("build") or {}).get("page_content") or {}
    st["done"] = False
    st.pop("error", None)
    if not st.get("priced") and not (room_file.get("scope_items") or page.get("trades")):
        st.update(done=True, note="The scope has no work items, so there is no budget to write.")
        return st

    if not st.get("priced"):
        quantities = _quantities(room_file, log)
        qs, cs, ms = reference_lists(room_file, page, quantities)
        anchors = load_anchors()
        plan, usage = ask_for_plan(run, room_file, page, qs, cs, ms, anchors, log)
        visit = site_scope._pretty_date(run["visit_date"]) if run.get("visit_date") else "the site visit"
        source = (f"Source: CompanyCam scope Page \"{page_title}\" ({visit} visit), with the drawings and photo "
                  f"descriptions on CompanyCam project {run.get('project_id')}. Site Scope Tool run {run.get('run_id')}.")
        if (os.environ.get("SCOPE_BUDGET_JOB") or "").strip():
            source += f" Scope is from job {run['job'].get('number')}, budget written to a stand-in job."
        st.update(priced=price_plan(plan, room_file, qs, cs, ms, anchors, source, query, org_id, log),
                  usage=usage, page_title=page_title)
        _add_usage(run, usage)
        save()

    priced = st["priced"]
    job, existing = None, []
    if not st.get("job"):
        st["job"] = target_job(run, query, org_id)
    job, existing = _budget_groups(st["job"]["id"], query)
    st["job"].update(number=job.get("number") or st["job"].get("number"), name=job.get("name"))
    written = st.setdefault("written", {})
    ours = {r.get("id") for r in written.values() if r.get("id")} | {st.get("parent_id")}
    others = [g for g in existing if g.get("id") not in ours]
    if "mode" not in st:
        st["mode"] = "parent" if others else "top"
        st["existing_groups"] = len(others)
        log(f"Scope budget: job {st['job']['number']} has {len(others)} budget group(s) already - "
            f"{'writing inside a parent group' if others else 'writing at the top level'}")
        save()

    parent_id = None
    if st["mode"] == "parent":
        if not st.get("parent_id"):
            visit = site_scope._pretty_date(run["visit_date"]) if run.get("visit_date") else "Site"
            base = f"Site Scope Draft - {visit} Visit"
            taken = {(g.get("name") or "").strip().lower() for g in existing}
            name, n = base, 1
            while name.lower() in taken:
                n += 1
                name = f"{base} ({n})"
            try:
                resp = query({"createCostGroup": {"$": {"jobId": st["job"]["id"], "name": name},
                                                  "createdCostGroup": {"id": {}}}})
            except Exception as e:
                raise BudgetError(f"Couldn't create the parent group '{name}' ({_jt_error(e)}).")
            st.update(parent_id=_created_id(resp, "CostGroup"), parent_name=name)
            save()
        parent_id = st["parent_id"]

    errors = []
    for node in build_tree(priced):
        try:
            if not write_node(node, st["job"]["id"], parent_id, written, query, save, log):
                errors.append(f"'{node['name']}': some lines didn't save")
        except BudgetError as e:
            errors.append(str(e))
            log(f"Scope budget: {e}")
    st["errors"] = errors
    st["done"] = not errors
    cost, price = totals([i for g in priced["groups"] for i in g["items"]])
    st["totals"] = {"cost": cost, "price": price}
    n_tbd = sum(1 for g in priced["groups"] + [c for o in priced["options"] for c in o["choices"]]
                for i in g["items"] if i["kind"] == "tbd")
    log(f"Scope budget job {st['job']['number']}: {len(priced['groups'])} groups, {len(priced['options'])} option "
        f"set(s), cost {_money(cost)}, price {_money(price)}, {n_tbd} PRICING TBD line(s), "
        f"{'done' if st['done'] else 'not finished: ' + '; '.join(errors)}")
    for w in priced.get("warnings") or []:
        log(f"Scope budget check: {w}")
    return st


def _add_usage(run, usage):
    """The budget call's tokens and cost go into the build's running total."""
    b = run.setdefault("build", {})
    total = b.setdefault("usage", {})
    for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "calls"):
        total[k] = (total.get(k) or 0) + (usage.get(k) or 0)
    total["cost_usd"] = round((total.get("cost_usd") or 0) + (usage.get("cost_usd") or 0), 4)
    for k in ("model", "effort"):
        total.setdefault(k, usage.get(k))


def public(st):
    """The budget as the page sees it."""
    if not st or not st.get("priced"):
        return None
    priced = st["priced"]
    job = st.get("job") or {}
    tbd = sum(1 for g in priced["groups"] + [c for o in priced["options"] for c in o["choices"]]
              for i in g["items"] if i["kind"] == "tbd")
    out = {"job_number": job.get("number"), "stand_in": bool(job.get("stand_in")),
           "groups": len(priced["groups"]), "options": len(priced["options"]),
           "cost": (st.get("totals") or {}).get("cost"), "price": (st.get("totals") or {}).get("price"),
           "tbd_lines": tbd, "parent_name": st.get("parent_name"), "checks": len(priced.get("warnings") or []),
           "job_url": f"https://app.jobtread.com/jobs/{job['id']}" if job.get("id") else None}
    if out["cost"] is None:
        out["cost"], out["price"] = totals([i for g in priced["groups"] for i in g["items"]])
    return out
