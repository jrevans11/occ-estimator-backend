"""Site Scope Tool - the JobTread budget (phase 6).

The last step of the build (scope_build), run on its own once the scope Page
is written (Jason's call, Oct 20, 2026). Two parts, each saved on the run so a
retry only redoes what didn't finish:

    price   one Claude call turns the updated room file, the scope Page's
            trades and the working quantities into budget groups: scope
            lines and NOTES, in-house hours, material lines, sub lines with
            the rate anchors they rest on. Then code does the rest: Home
            Depot prices for the materials (with the case/multipack check),
            markups, $0 (PRICING TBD) for licensed work with no anchor, the
            description format, the internal NOTES item, totals.
    write   the groups go onto the JobTread job under one parent group,
            "Site Scope Draft - <visit date>" (Jason's call, Oct 20), so they
            sit apart from anything already on the job. One mutation per
            call; progress is saved after every group and item.

AI does judgment, code does math and rules (build plan, "AI vs. code split").
Markups: subs cost x 1.45, materials cost x 1.65, labor $55/hr cost -> $89/hr.
See docs/site-scope/site-scope-tool-build-plan.md (Phase 6 notes).
"""

import csv
import json
import os
import re
import traceback
import urllib.error

import estimate_v2_draft as v2
import scope_quickpass as qp
import site_scope

LICENSED_TRADES = {"electrical", "plumbing", "hvac", "gas", "structural"}
PARENT_PREFIX = "Site Scope Draft"
ALTERNATES_GROUP = "Optional Alternates"
TBD_SUFFIX = " (PRICING TBD)"
LABOR_PRICE_RATE = 89.00
MAX_DESCRIPTION = 4096
MAX_GROUP_NAME = 60
SRC_WORDS = {"dictated": "dictated / answered", "est": "est. from photos", "assumed": "assumed"}

_DIR = os.path.dirname(os.path.abspath(__file__))
# (file, vendor, trade) - the sub invoice histories, as rate anchors.
VENDOR_FILES = (
    ("redland_electric_invoice_history.csv", "Redland Electric", "electrical"),
    ("crawlspace_medic_invoice_history.csv", "Crawlspace Medic", "crawlspace"),
    ("tile_with_style_invoice_history.csv", "Tile with Style", "tile"),
    ("greer_flooring_invoice_history.csv", "Greer Flooring", "flooring"),
    ("jordan_lumber_invoice_history.csv", "Jordan Lumber Co.", "hardwood flooring"),
)
CALIBRATION_FILE = "consolidated_trade_group_calibration.csv"


class BudgetError(Exception):
    """A problem to show Jason as-is."""


# ── Rate anchors ─────────────────────────────────────────────────────────────

def _money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def _short(text, n=240):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


def load_anchors(directory=_DIR):
    """Every row of the sub invoice histories and Jason's priced trade groups,
    as [(anchor id, one line of text)]. Ids are stable while the files are.
    A missing file is skipped (logged), never fatal."""
    out = []

    def rows(name):
        path = os.path.join(directory, name)
        try:
            with open(path, newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        except OSError:
            print(f"Scope budget: rate anchor file {name} not found - skipped")
            return []

    for name, vendor, trade in VENDOR_FILES:
        for r in rows(name):
            date = r.get("invoice_date") or r.get("doc_date") or ""
            qty, unit = r.get("quantity") or "", r.get("unit") or ""
            each = r.get("unit_price") or ""
            amount = _money(r.get("line_amount"))
            math = f"{qty} {unit} x {_money(each)} = {amount}".replace("  ", " ") if qty and each else amount
            text = (f"{vendor} ({trade}) | {date} | {_short(r.get('line_item_description'), 120)} | {math} | "
                    f"job total {_money(r.get('job_total_billed'))} | {_short(r.get('scope_notes'))}")
            out.append((f"A{len(out) + 1}", text))
    for r in rows(CALIBRATION_FILE):
        model = (r.get("pricing_model") or "").strip()
        if model == "sub" and r.get("sub_cost"):
            price = f"sub cost {_money(r['sub_cost'])}"
        elif r.get("labor_hours") or r.get("material_cost_total"):
            price = (f"in-house {r.get('labor_hours') or '?'} hrs + {_money(r.get('material_cost_total')) or '$?'} "
                     "materials at cost")
        else:
            continue
        out.append((f"A{len(out) + 1}", f"OCC priced trade group, {r.get('source_job')} | {r.get('group_name')} | "
                                        f"{price} | {_short(r.get('scope_summary'))}"))
    return out


# ── The Claude call ──────────────────────────────────────────────────────────

INSTRUCTIONS = """You are the budget step of Owners Choice Construction's Site Scope Tool. OCC is a residential repair and remodel contractor in Greenville, SC (owner Jason Evans). The site visit was read into a room file, Jason answered the site questions, and a scope Page was written. You turn that scope into a draft JobTread budget that Jason only has to tweak. Code does all the math - markups, totals, the quantities off the drawings, live Home Depot prices - so you do the estimating judgment.

WHAT YOU GET
- The scope items (S1, S2...): trade, area, work, quantity and its source (dictated or confirmed; est = estimated from a photo; assumed), the sub Jason named, licensed_trade.
- The scope Page's trades and optional alternates.
- Working quantities (W1, W2...) worked out by code from the drawings, each with its source.
- Jason's answers to the site questions.
- Below: rate anchors (A1, A2...) - real costs to OCC from its subs' invoices and from trade groups Jason priced himself - and real past OCC in-house groups with hours and materials.

HOW TO BUILD IT
1. Group by trade or area, the way the scope Page groups its trades (Demolition, Framing and Structural, Electrical, Trim and Carpentry, Patch and Paint, Flooring...). Every scope item goes in exactly one group - list its id in scope_item_ids. Usually 3 to 12 groups.
2. Who does the work:
   - Licensed trades - electrical, plumbing, HVAC, gas, structural (lintels, beams, headers in bearing walls, engineering) - are always sub lines, with licensed_trade set. That includes a vanity or sink disconnect and reconnect (Jason, Oct 20, 2026: a plumber line, PRICING TBD until there's a bid), not in-house hours. Give a cost ONLY when a rate anchor clearly covers comparable work, and list those anchor ids; otherwise "cost": null and code writes it as $0 (PRICING TBD). Never price licensed work from general knowledge.
   - A trade Jason named a sub for is a sub line. Significant flooring or tile (a full room or more), significant masonry, crawlspace moisture work and all fireplace or gas appliance work are subs too. Bundle all of one sub's work in a group as one visit or day-rate line, not a pile of small charges.
   - Everything else is in-house: labor lines (hours) and material lines.
   - An unlicensed sub line (flooring, tile, masonry...) may be priced from an anchor, or from your own judgment when none fits - then "anchors": [] and say so in internal_notes.
   - Sub costs are OCC's cost from the sub, before OCC's markup.
3. In-house hours: price each group the way a crew lead would - setup, protection, layout, the work itself, trips, cleanup. Adding up per-task minimums lands low. Real anchors: OCC billed 26 hours for a group of exterior door repairs, 30 hours for exterior trim, shutters, fascia and paint, 16 for a bathroom repair group, 10 for garage repairs. Give hours only - the rate is code's.
4. Materials: real purchasable items at their single-unit retail cost (Home Depot, Greenville SC), in the unit you'd buy (8 ft piece, gallon, box, sheet). Work the quantity out from the working quantities or a scope item's quantity, plus waste, and put the W or S id it rests on in qty_ref. Code looks each item up in the Home Depot catalog and rejects case and multipack prices, so name it the way Home Depot sells it ("3-1/2 in. x 7/16 in. primed MDF casing, 8 ft"), not a description of the work. Small consumables (fasteners, caulk, tape, sandpaper) can be one line.
5. Client-facing text - scope_lines and notes. Scope lines start with an imperative verb and describe the work, not the defect, in build sequence (demo, structure, rough-in, finish, cleanup). Notes cover what the price assumes (quantities, method, day rate or measured), what is excluded that a reasonable person would assume is included, what discovery would change the price, and honest tradeoffs and expectations (new paint matches closely, never exactly; a patched wall can show). Explain the mechanism, not just the disclaimer. A quantity marked (est.) or (assumed) that the price rests on is stated in notes as an assumption. No prices in the text.
6. internal_notes are for Jason only and never print for the client: quantities never measured, prices that are yours, which anchors you leaned on and why, what to confirm with the sub, anything to catch before sending.
7. Alternates: each optional add-on or either/or on the scope Page becomes an alternate, the same shape as a group, whose notes state its own tradeoff. [] if none.

WRITING RULES (every name, line and note)
No markdown, asterisks, underscores or emoji. A spaced hyphen " - ", never a dash. Feet and inches as 13'-2". Group names: plain Title Case trade or area names, under 60 characters, no numbers in front. Say "the client", never "you". Spell out Owners Choice Construction if the company is named.

cost_code is one of these exact names: {cost_codes}

OUTPUT
Reply with one JSON object and nothing else:
{{"groups": [{{"name": "Trim and Carpentry", "cost_code": "Trim & Millwork", "scope_item_ids": ["S6"],
   "scope_lines": ["Install wood jambs on the four new cased openings, sized to each wall"],
   "notes": ["Pricing assumes 8 in. block walls; a different thickness changes the jamb stock."],
   "internal_notes": ["Block thickness assumed - measure before ordering jamb stock."],
   "labor": [{{"task": "Carpentry - jambs and casing", "hours": 14}}],
   "materials": [{{"item": "1x10 in. x 8 ft primed finger-joint pine board", "qty": 8, "unit": "8 ft piece", "unit_cost": 21.98, "qty_ref": "S6"}}],
   "subs": [{{"name": "Electrical relocation", "sub": "Redland Electric", "licensed_trade": "electrical", "cost": 600, "anchors": ["A10"], "basis": "two receptacle moves at about $150 each plus a trip, per A10"}}]}}],
 "alternates": []}}
Use [] for a group's empty lists. "sub" and "licensed_trade" are null when they don't apply.
"""


def system_blocks():
    text = INSTRUCTIONS.format(cost_codes=", ".join(v2.COST_CODE_MAP))
    anchors = load_anchors()
    if anchors:
        text += ("\n\n===== RATE ANCHORS (cost to OCC, before OCC's markup) =====\n"
                 + "\n".join(f"{aid} | {line}" for aid, line in anchors))
    try:
        history = v2.load_historical_reference_examples()
    except Exception as e:   # reference examples are a nice-to-have
        print(f"Scope budget: historical examples skipped: {e}")
        history = ""
    if history:
        text += ("\n\n===== PAST OCC IN-HOUSE GROUPS (closing repairs - real hours and materials; "
                 "a remodel group is usually larger) =====\n" + history)
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def working_quantities(room_file):
    """[(W id, quantity)] for every drawable room - code's numbers off the drawings."""
    try:
        import scope_quantities   # loads Pillow, so only here
        out = []
        for room in room_file.get("rooms") or []:
            out += scope_quantities.room_quantities(room)
    except Exception as e:   # a quantity bug must not cost Jason the budget
        print(f"Scope budget: working quantities failed: {e}\n{traceback.format_exc()}")
        out = []
    return [(f"W{i + 1}", q) for i, q in enumerate(out)]


def build_content(run, room_file, page, quantities):
    job = run["job"]
    items = [{k: s.get(k) for k in ("id", "trade", "area", "work", "quantity", "sub", "licensed_trade", "detail")}
             for s in room_file.get("scope_items") or [] if isinstance(s, dict)]
    trades = {"trades": page.get("trades") or [], "alternates": page.get("alternates") or [],
              "client_goals": page.get("client_goals") or room_file.get("client_goals") or []}
    wq = "\n".join(f"{wid} | {q['item']} | {q['value']} {q['unit']} | {q['basis']} | "
                   f"{SRC_WORDS.get(q['src'], q['src'])}" for wid, q in quantities) or "(none - no rooms were drawn)"
    qa = "\n".join(f"{a['id']}. {a['question']} - {a['answer'] or '(left blank)'}"
                   for a in run.get("answers") or []) or "(No questions were needed.)"
    rooms = [{"name": r.get("name"), "ceiling": r.get("ceiling"), "walls": len(r.get("walls") or []),
              "flags": r.get("flags") or []} for r in room_file.get("rooms") or []]
    visit = site_scope._pretty_date(run["visit_date"]) if run.get("visit_date") else "the site visit"
    return [
        {"type": "text", "text": f"Job {job.get('number')} - {job.get('name') or ''}, "
                                 f"{job.get('address') or 'no address on file'}. Site visit on {visit}."},
        {"type": "text", "text": "SCOPE ITEMS:\n" + json.dumps(items, indent=1)},
        {"type": "text", "text": "SCOPE PAGE TRADES AND ALTERNATES:\n" + json.dumps(trades, indent=1)},
        {"type": "text", "text": "ROOMS:\n" + json.dumps(rooms) + "\nOPEN FLAGS: "
                                 + json.dumps(room_file.get("flags") or [])},
        {"type": "text", "text": "WORKING QUANTITIES (code, off the drawings):\n" + wq},
        {"type": "text", "text": "JASON'S ANSWERS TO THE SITE QUESTIONS:\n" + qa},
        {"type": "text", "text": "Build the budget now, as one JSON object."},
    ]


def parse_answer(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict) or not isinstance(obj.get("groups"), list):
        raise ValueError("the reply has no groups list")
    if not any(isinstance(g, dict) and str(g.get("name") or "").strip() for g in obj["groups"]):
        raise ValueError("the reply has no named groups")
    return obj


# ── Cleaning and checks (code) ───────────────────────────────────────────────

_EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF☀-➿️]")


def clean_text(s):
    """OCC's language rules: no markdown characters or emoji, a spaced hyphen
    for a dash, single spaces."""
    s = str(s or "")
    s = re.sub(r"\s*[—–]\s*", " - ", s)
    s = _EMOJI_RE.sub("", s)
    s = re.sub(r"[*`#]", "", s)
    s = re.sub(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])", "", s)
    return " ".join(s.split())


def clean_name(s, limit=MAX_GROUP_NAME):
    s = re.sub(r"^\d+(?:\.\d+)*(?:\s*-\s*|\s+)(?=[A-Za-z])", "", clean_text(s))   # "3.1 Electrical", not "2x4"
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" -,")
    return s


def _num(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def _lines(value):
    return [clean_text(x) for x in (value if isinstance(value, list) else []) if clean_text(x)]


def _licensed(value):
    s = str(value or "").strip().lower()
    if s in ("hvac", "mechanical"):
        return "hvac"
    return next((t for t in LICENSED_TRADES if t in s), None) if s not in ("", "none", "null", "false") else None


def clean_group(g):
    """One group from Claude, every field the right shape (or None)."""
    if not isinstance(g, dict) or not clean_name(g.get("name")):
        return None
    labor = [{"task": clean_text(x.get("task")) or "Labor", "hours": round(_num(x.get("hours")), 2)}
             for x in g.get("labor") or [] if isinstance(x, dict) and (_num(x.get("hours")) or 0) > 0]
    materials = []
    for x in g.get("materials") or []:
        if not isinstance(x, dict) or not clean_text(x.get("item")):
            continue
        materials.append({"item": clean_text(x["item"]), "qty": _num(x.get("qty")) or 0,
                          "unit": clean_text(x.get("unit")), "unit_cost": _num(x.get("unit_cost")) or 0,
                          "qty_ref": str(x.get("qty_ref") or "").strip().upper() or None})
    subs = []
    for x in g.get("subs") or []:
        if not isinstance(x, dict) or not clean_text(x.get("name")):
            continue
        cost = _num(x.get("cost"))
        subs.append({"name": clean_text(x["name"]), "sub": clean_text(x.get("sub")) or None,
                     "licensed_trade": _licensed(x.get("licensed_trade")),
                     "cost": cost if cost and cost > 0 else None,
                     "anchors": [str(a).strip().upper() for a in x.get("anchors") or [] if str(a).strip()],
                     "basis": clean_text(x.get("basis"))})
    return {"name": clean_name(g["name"]), "cost_code": str(g.get("cost_code") or "").strip(),
            "scope_item_ids": [str(s).strip().upper() for s in g.get("scope_item_ids") or [] if str(s).strip()],
            "scope_lines": _lines(g.get("scope_lines")), "notes": _lines(g.get("notes")),
            "internal_notes": _lines(g.get("internal_notes")), "labor": labor, "materials": materials, "subs": subs}


def description(group):
    """OCC's exact format: "Scope Includes:", a blank line, the lines; then
    "NOTES:" with no blank line after it. Notes drop from the end to fit."""
    head = ["Scope Includes:", ""] + [f"- {s}" for s in group["scope_lines"] or ["See the line items below."]]
    notes = list(group["notes"])
    while True:
        text = "\n".join(head + (["", "NOTES:"] + [f"- {n}" for n in notes] if notes else []))
        if len(text) <= MAX_DESCRIPTION or not notes:
            return text[:MAX_DESCRIPTION]
        notes.pop()


def check_coverage(groups, room_file):
    """Scope items left out of every group, and ones in more than one (the
    expensive error: double charging). Returns (missing ids, {id: [group names]})."""
    seen = {}
    for g in groups:
        for sid in g["scope_item_ids"]:
            seen.setdefault(sid, []).append(g["name"])
    ids = [str(s.get("id") or "").upper() for s in room_file.get("scope_items") or [] if isinstance(s, dict)]
    missing = [i for i in ids if i and i not in seen]
    doubled = {i: names for i, names in seen.items() if len(names) > 1}
    return missing, doubled


# ── Pricing (code) ───────────────────────────────────────────────────────────

def price_group(group, anchors, quantities, scope_items, page_ref):
    """Turn a cleaned group into JobTread item args (no costGroupId yet) plus
    the internal notes. Returns (items, flags, totals)."""
    code_id = v2.resolve_cost_code_id(group["cost_code"], context=group["name"])
    items, flags = [], list(group["internal_notes"])
    cost = price = 0.0
    tbd = 0

    for s in group["subs"]:
        licensed = s["licensed_trade"]
        good = [a for a in s["anchors"] if a in anchors]
        bad = [a for a in s["anchors"] if a not in anchors]
        unit_cost = s["cost"]
        why = None
        if unit_cost and licensed and not good:
            why = f"{licensed} work with no rate anchor behind the number"
            unit_cost = None
        name = f"{s['name']} - {s['sub']}" if s["sub"] and s["sub"].lower() not in s["name"].lower() else s["name"]
        if unit_cost:
            p = round(unit_cost * v2.SUB_MARKUP, 2)
            cost, price = cost + unit_cost, price + p
            items.append({"kind": "sub", "args": {"name": name[:250], "quantity": 1, "unitCost": round(unit_cost, 2),
                                                   "unitPrice": p, "costCodeId": code_id,
                                                   "costTypeId": v2.COST_TYPE_SUB}})
            src = ("anchors " + ", ".join(f"{a} ({_short(anchors[a], 90)})" for a in good)) if good \
                else "no anchor - the number is the AI's own"
            flags.append(f"Sub line '{name}': ${unit_cost:,.2f} cost from {src}"
                         + (f". {s['basis']}" if s["basis"] else "") + ". Confirm with the sub.")
        else:
            tbd += 1
            items.append({"kind": "sub", "args": {"name": (name + TBD_SUFFIX)[:250], "quantity": 1, "unitCost": 0,
                                                   "unitPrice": 0, "costCodeId": code_id,
                                                   "costTypeId": v2.COST_TYPE_SUB}})
            flags.append(f"'{name}' is $0 PRICING TBD - "
                         + (why or ("licensed trade, needs a sub bid" if licensed else "no price given"))
                         + (f" ({s['basis']})" if s["basis"] else "") + ".")
        if bad:
            flags.append(f"Unknown anchor id(s) cited on '{name}': {', '.join(bad)} - ignored.")

    for x in group["labor"]:
        hours = x["hours"]
        cost, price = cost + hours * v2.LABOR_COST_RATE, price + hours * LABOR_PRICE_RATE
        items.append({"kind": "labor", "args": {"name": f"{x['task']} labor"[:250] if "labor" not in x["task"].lower()
                                                else x["task"][:250], "quantity": hours,
                                                "unitCost": v2.LABOR_COST_RATE, "unitPrice": LABOR_PRICE_RATE,
                                                "costCodeId": code_id, "costTypeId": v2.COST_TYPE_LABOR}})

    for m in group["materials"]:
        if m["qty"] <= 0 or m["unit_cost"] <= 0:
            flags.append(f"Material '{m['item']}' dropped - no quantity or cost given.")
            continue
        unit_price = round(m["unit_cost"] * v2.MATERIAL_MARKUP, 2)
        cost, price = cost + m["qty"] * m["unit_cost"], price + m["qty"] * unit_price
        base = {"name": m["item"][:250], "quantity": m["qty"], "unitCost": round(m["unit_cost"], 2),
                "unitPrice": unit_price, "costCodeId": code_id, "costTypeId": v2.COST_TYPE_MATERIALS}
        enriched = dict(base)
        desc = v2._build_material_description(m)
        if desc:
            enriched["description"] = desc[:MAX_DESCRIPTION]
            if v2._is_internal_material_description(m):
                enriched["showDescription"] = False
        fields = v2._build_material_custom_field_values(m)
        if fields:
            enriched["customFieldValues"] = fields
        items.append({"kind": "material", "args": enriched, "fallback": base if enriched != base else None})
        ref = m.get("qty_ref")
        if ref:
            src = quantities.get(ref, {}).get("src") or scope_items.get(ref)
            if src in ("est", "assumed"):
                what = quantities.get(ref, {}).get("item") or f"scope item {ref}"
                flags.append(f"'{m['item']}' quantity rests on {what} ({SRC_WORDS[src]}) - verify before ordering.")

    if group.get("licensed_ids") and not any(s["licensed_trade"] for s in group["subs"]):
        flags.append(f"Licensed-trade scope item(s) {', '.join(group['licensed_ids'])} sit in this group with no "
                     "licensed sub line - check none of the in-house lines is licensed work.")
    flags.append(f"Drafted by the Site Scope Tool from {page_ref}. Markups: subs x1.45, materials x1.65, "
                 "labor $55/hr cost to $89/hr.")
    notes_text = "\n".join(f"- {f}" for f in flags)[:MAX_DESCRIPTION]
    items.append({"kind": "notes", "args": {"name": v2.INTERNAL_NOTES_ITEM_NAME, "quantity": 1, "unitCost": 0,
                                            "unitPrice": 0, "description": notes_text, "showDescription": False,
                                            "costCodeId": code_id, "costTypeId": v2.COST_TYPE_OTHER}})
    return items, flags, {"cost": round(cost, 2), "price": round(price, 2), "tbd": tbd}


def make_plan(run, room_file, answer, quantities, page_title, jobtread_query, org_id, log=print):
    """Claude's answer -> the full write plan (catalog prices looked up here)."""
    groups = [g for g in (clean_group(x) for x in answer.get("groups") or []) if g]
    alternates = [g for g in (clean_group(x) for x in answer.get("alternates") or []) if g]
    if not groups:
        raise BudgetError("The AI's budget had no usable groups. Tap Build to try again.")

    scope = {str(s.get("id") or "").upper(): s for s in room_file.get("scope_items") or [] if isinstance(s, dict)}
    for g in groups + alternates:
        g["licensed_ids"] = [i for i in g["scope_item_ids"] if _licensed((scope.get(i) or {}).get("licensed_trade"))]

    est = {"cost_groups": [{"material_lines": g["materials"]} for g in groups + alternates]}
    try:
        _, stats = v2.resolve_material_lines_with_catalog(est, jobtread_query, org_id)
        log(f"Scope budget: Home Depot catalog - {stats}")
    except Exception as e:   # catalog trouble keeps the AI's costs, flagged per line
        log(f"Scope budget: catalog lookup failed, keeping the AI's material costs: {e}")
        for g in groups + alternates:
            for m in g["materials"]:
                m["catalog_no_match"] = True

    anchors = dict(load_anchors())
    qmap = {wid: q for wid, q in quantities}
    scope_src = {}
    for sid, s in scope.items():
        q = s.get("quantity") if isinstance(s.get("quantity"), dict) else {}
        raw = str(q.get("src") or "").lower()
        scope_src[sid] = "dictated" if raw in ("dictated", "confirmed", "measured") else (
            "est" if raw in ("est", "estimated", "photo") else "assumed")
    page_ref = f"the CompanyCam Page \"{page_title}\"" if page_title else "the site visit scope"

    out, warnings = [], []
    missing, doubled = check_coverage(groups, room_file)
    for i, g in enumerate(groups):
        items, _, totals = price_group(g, anchors, qmap, scope_src, page_ref)
        out.append({"key": f"g{i}", "name": g["name"], "description": description(g), "items": items, **totals})
    for sid, names in doubled.items():
        warnings.append(f"Scope item {sid} is in more than one group ({', '.join(names)}) - check for double charging.")
    if missing:
        warnings.append("Scope item(s) in no group: " + ", ".join(
            f"{i} ({_short((scope.get(i) or {}).get('work'), 60)})" for i in missing) + ".")

    alt_out = []
    for i, g in enumerate(alternates):
        items, _, totals = price_group(g, anchors, qmap, scope_src, page_ref)
        alt_out.append({"key": f"a{i}", "name": clean_name(f"Option {i + 1} - {g['name']}"),
                        "description": description(g), "items": items, **totals})
    for w in warnings:
        log(f"Scope budget warning: {w}")
    when = site_scope._pretty_date(run["visit_date"]) if run.get("visit_date") else "Site Visit"
    return {"parent_name": f"{PARENT_PREFIX} - {when}", "groups": out, "alternates": alt_out, "warnings": warnings,
            "cost": round(sum(g["cost"] for g in out), 2), "price": round(sum(g["price"] for g in out), 2),
            "tbd": sum(g["tbd"] for g in out) + sum(g["tbd"] for g in alt_out),
            "alternates_price": round(sum(g["price"] for g in alt_out), 2)}


# ── Writing to JobTread ──────────────────────────────────────────────────────

def _free_parent_name(job_id, name, jobtread_query):
    """The parent group name, or "<name> (2)" etc. if the job has it already
    (a second build on the same visit never writes into Jason's edited one)."""
    try:
        resp = jobtread_query({"job": {"$": {"id": job_id}, "costGroups": {
            "$": {"where": ["name", "like", f"{PARENT_PREFIX}%"], "size": 100}, "nodes": {"name": {}}}}})
        taken = {(n.get("name") or "").strip().lower() for n in resp["job"]["costGroups"]["nodes"]}
    except Exception as e:
        print(f"Scope budget: couldn't read the job's groups ({e}); using {name!r}")
        return name
    n, out = 1, name
    while out.lower() in taken:
        n += 1
        out = f"{name} ({n})"
    return out


def _create_group(jobtread_query, job_id, args):
    resp = jobtread_query({"createCostGroup": {"$": dict(args, jobId=job_id), "createdCostGroup": {"id": {}}}})
    gid = ((resp or {}).get("createCostGroup") or {}).get("createdCostGroup", {}).get("id")
    if not gid:
        raise BudgetError(f"JobTread didn't return an id for the group \"{args.get('name')}\" ({_short(resp, 200)}).")
    return gid


def _create_item(jobtread_query, args):
    resp = jobtread_query({"createCostItem": {"$": args, "createdCostItem": {"id": {}}}})
    iid = ((resp or {}).get("createCostItem") or {}).get("createdCostItem", {}).get("id")
    if not iid:
        raise ValueError(f"no id returned ({_short(resp, 200)})")
    return iid


def _refused(e):
    """True when JobTread refused this one item (bad input), as opposed to a
    network or server problem worth retrying later."""
    if isinstance(e, urllib.error.HTTPError):
        return 400 <= e.code < 500 and e.code not in (408, 429)
    return isinstance(e, (ValueError, KeyError, TypeError))


def write_plan(plan, state, job_id, jobtread_query, save, log=print):
    """Write what isn't written yet. state holds the ids made so far and is
    saved (save()) after every group and item, so a retry picks up where this
    stopped. Raises on a network/server problem; an item JobTread refuses is
    skipped and listed in state["skipped"]."""
    if not state.get("parent_id"):
        name = _free_parent_name(job_id, plan["parent_name"], jobtread_query)
        state["parent_id"] = _create_group(jobtread_query, job_id, {"name": name})
        state["parent_name"] = name
        save()
    groups = state.setdefault("groups", {})
    skipped = state.setdefault("skipped", [])

    def write_group(g, parent_id):
        done = groups.setdefault(g["key"], {"id": None, "items": 0})
        if not done["id"]:
            done["id"] = _create_group(jobtread_query, job_id, {"parentCostGroupId": parent_id, "name": g["name"],
                                                                "description": g["description"]})
            save()
        while done["items"] < len(g["items"]):
            item = g["items"][done["items"]]
            try:
                _create_item(jobtread_query, dict(item["args"], costGroupId=done["id"]))
            except Exception as e:
                if not _refused(e):
                    raise
                if item.get("fallback"):
                    log(f"Scope budget: '{item['args']['name']}' refused with its description/fields ({e}); "
                        "retrying without them")
                    try:
                        _create_item(jobtread_query, dict(item["fallback"], costGroupId=done["id"]))
                    except Exception as e2:
                        if not _refused(e2):
                            raise
                        skipped.append(f"{g['name']}: {item['args']['name']} ({_short(e2, 120)})")
                else:
                    skipped.append(f"{g['name']}: {item['args']['name']} ({_short(e, 120)})")
            done["items"] += 1
            save()

    for g in plan["groups"]:
        write_group(g, state["parent_id"])
    if plan["alternates"]:
        if not state.get("alternates_id"):
            args = {"parentCostGroupId": state["parent_id"], "name": ALTERNATES_GROUP,
                    "minSelectionsRequired": 0, "maxSelectionsAllowed": len(plan["alternates"])}
            try:
                state["alternates_id"] = _create_group(jobtread_query, job_id, args)
            except Exception as e:
                if not _refused(e) and not isinstance(e, BudgetError):
                    raise
                log(f"Scope budget: option group refused with selection settings ({e}); writing a plain group")
                state["alternates_plain"] = True
                state["alternates_id"] = _create_group(jobtread_query, job_id, {
                    "parentCostGroupId": state["parent_id"], "name": ALTERNATES_GROUP})
            save()
        for g in plan["alternates"]:
            write_group(g, state["alternates_id"])
    if skipped:
        log(f"Scope budget: {len(skipped)} line item(s) JobTread refused: {skipped}")


# ── The step ─────────────────────────────────────────────────────────────────

def run_budget(run, step_state, save, jobtread_query, org_id, log=print):
    """The budget step of the build. step_state is run["build"]["steps"]["budget"]
    (created by the caller); updated in place. Returns it with done True/False.
    Raises BudgetError / qp errors for the caller to record."""
    if not jobtread_query or not org_id:
        raise BudgetError("This server can't reach JobTread for the budget (no JobTread connection was passed "
                          "to the build). Tap Build to try again.")
    job_id = run["job"].get("id")
    if not job_id:
        raise BudgetError("The run has no JobTread job id.")
    b = run.get("build") or {}
    page_title = ((b.get("steps") or {}).get("page") or {}).get("title")

    if not step_state.get("plan"):
        room_file, page = run["room_file"], b.get("page_content") or {}
        quantities = working_quantities(room_file)
        label = f"Scope budget job {run['job'].get('number')}"
        answer, usage = qp.ask_for_json(system_blocks(), build_content(run, room_file, page, quantities),
                                        parse_answer, label, log,
                                        refused="Claude declined to price this scope. Tap Build to try again.",
                                        retry_hint="Tap Build to try again.")
        step_state["usage"] = usage
        step_state["plan"] = make_plan(run, room_file, answer, quantities, page_title, jobtread_query, org_id, log)
        save()

    plan = step_state["plan"]
    written = step_state.setdefault("written", {})
    try:
        write_plan(plan, written, job_id, jobtread_query, save, log)
    except (urllib.error.URLError, OSError) as e:
        raise BudgetError(f"Couldn't reach JobTread ({e}). What was written is kept - tap Build to finish.")
    step_state["done"] = True
    step_state["error"] = None
    log(f"Scope budget job {run['job'].get('number')}: {len(plan['groups'])} groups under "
        f"\"{written.get('parent_name')}\", cost ${plan['cost']:,.2f}, price ${plan['price']:,.2f}, "
        f"{plan['tbd']} PRICING TBD line(s), {len(written.get('skipped') or [])} skipped")
    return step_state


def public(run):
    """The budget as the page sees it."""
    s = ((run.get("build") or {}).get("steps") or {}).get("budget") or {}
    plan = s.get("plan")
    if not plan:
        return None
    written = s.get("written") or {}
    return {"parent": written.get("parent_name") or plan["parent_name"], "groups": len(plan["groups"]),
            "alternates": len(plan["alternates"]), "cost": plan["cost"], "price": plan["price"],
            "alternates_price": plan.get("alternates_price"), "tbd": plan["tbd"],
            "warnings": plan.get("warnings") or [], "skipped": written.get("skipped") or [],
            "job_url": f"https://app.jobtread.com/jobs/{run['job'].get('id')}"}
