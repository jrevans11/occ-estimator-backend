"""
DRAFT — labor + material estimate rebuild (v2)
================================================
NOT wired into app.py. This is a review draft — nothing in production changes
until Jason signs off and it gets merged in.

WHAT THIS CHANGES vs. today's app.py:
  - Today: one CostGroup + one CostItem (qty 1) per repair, picked as a single
    blended price off a static ~150-line table.
  - Here: Claude reasons per repair like an estimator would —
      1. Estimate QUANTITY from the inspection report text + photos (the PDF
         is already sent to Claude natively, so it can see photos/markups —
         this just asks it to actually use them for sizing, not just scope).
      2. IN-HOUSE work -> broken into labor hour line(s) + material line(s),
         each with a stated quantity/unit and a confidence flag.
      3. SUB work -> stays a single scoped lump sum (see rationale below),
         but now grounded in real Redland Electric invoice history instead of
         guessed ranges.
  - add_cost_groups_v2() posts MULTIPLE cost items per group (one per labor
    line, one per material line) instead of one.

WHY SUB WORK STAYS LUMP-SUM (not itemized labor+material):
  20 real Redland Electric invoices (redland_electric_invoice_history.csv)
  show subs bill OCC scope-based or day-rate per visit, not itemized
  labor-hours + materials (one exception out of 20). Forcing a granular
  per-line model onto sub work would fabricate a precision that doesn't
  exist in how subs actually bill. So sub groups get ONE reasoned scope
  price, informed by real historical ranges, same as today's structure —
  just with better numbers behind it.

BUG FOUND IN CURRENT app.py WORTH FIXING EITHER WAY:
  add_cost_groups() today does `cost = client_price / 1.55` for EVERY group,
  regardless of whether it's sub (should back out at /1.45) or in-house
  (should back out material-only at /1.65, since labor isn't marked up).
  1.55 is a rough blended guess applied uniformly. add_cost_groups_v2 below
  fixes this by computing unitCost correctly per line type.

RESOLVED SINCE FIRST DRAFT (Jul 2026):
  - Real historical labor hours + material costs DO exist: 514 of 960
    historical cost groups (121 of 141 Closing Repairs docs) have real
    multi-line labor+material breakdowns, pulled via jobtread_explore.py into
    historical_cost_item_detail.csv (2,069 line items). Naive fuzzy-matching
    these to the old 310-item taxonomy produced bad pairings and was
    discarded — use the real historical examples directly as retrieval/
    reference examples instead of a rigid lookup table.
  - Home Depot Global Catalog search IS reachable via the API: the real
    field is "homeDepotProducts" (a top-level query field, not nested under
    organization — confirmed by watching JobTread's own frontend network
    traffic). See resolve_material_from_catalog() below. This means
    material_lines CAN resolve to a real, live-priced, specific Home Depot
    product instead of a freeform description + guessed cost.

  - Real cost codes: 173 total exist, split into a 3-digit set (32 codes)
    and a 4-digit CSI-style full-build set (141 codes). Jason has decided to
    use the 3-digit set — see COST_CODE_MAP below, now wired into
    add_cost_groups_v2() via a new "cost_code" field Claude assigns per
    group, replacing the hardcoded Uncategorized code.

RESOLVED (Jul 2026, this pass) — Task #8, historical data wired into the prompt:
  - Full historical pull expanded from 141 "Closing Repairs" docs to ALL 391
    Home Repair + Closing Repair type jobs (371 with data) — 5,353 clean
    deduped line items, see historical_home_and_closing_repairs_budget_
    detail_dedup.csv and CLAUDE.md for the full findings writeup.
  - build_full_estimating_prompt() below is the actual wiring: it calls
    load_historical_reference_examples() to pull 17 hand-selected real cost
    groups (by explicit job_id+group_name, NOT fuzzy text matching — see the
    comment above HISTORICAL_REFERENCE_GROUP_KEYS for why) spanning plumbing,
    electrical, HVAC/vents, doors/hardware, masonry, and crawlspace, and
    appends them to ESTIMATING_LOGIC_SECTION as few-shot calibration. This is
    the piece that was previously just a TODO comment.
  - BUG FIX: add_cost_groups_v2()'s labor line was writing unitCost ==
    unitPrice (both 89.00), tracking zero labor margin. Now writes unitCost =
    LABOR_COST_RATE (55.00, OCC's real confirmed internal labor cost) and
    unitPrice = the billed rate (89.00) — consistent with how "Hourly Rate"
    lines actually appear in 1,290+ real historical examples.
  - Re-tested add_cost_groups_v2() end to end against a mock query function
    after these changes — labor/material/sub cost+price math and cost code
    lookups all confirmed correct.

STILL OPEN / PROVISIONAL (do not treat these numbers as final):
  - The 17 curated reference examples are a first pass — could expand or
    rotate the selection later, but deliberately kept small/curated rather
    than dumping all 5,353 rows into the prompt (token cost + would dilute
    the signal).
  - RESOLVED (Jul 2026, this pass): resolve_material_lines_with_catalog()
    now wires search_home_depot_catalog() into the actual reasoning/output
    flow (Task #10) — it was previously a standalone function nothing
    called. For each material_line, it searches the live catalog, scores
    candidates by word-overlap against the LLM's item description, and
    either (a) auto-replaces the guessed unit_cost with the real catalog
    price when the match is confident (score >= 0.5 by default) and attaches
    catalog_match (name/sku/link/score) so the swap is visible, or (b)
    leaves the LLM's guess untouched but attaches catalog_candidates (top 3
    real options with real prices) for manual pick when no match is
    confident, or (c) leaves the line completely unchanged if the search
    fails/returns nothing — a bad catalog lookup should never block an
    estimate. Deliberately NOT the discarded fuzzy-matching approach from
    pricing_library.csv — that matched against an unrelated taxonomy with no
    fallback; this gates a REAL catalog search result and always has a safe
    fallback (keep the LLM's number) when confidence is low. Tested with a
    mock catalog covering all three branches (auto-match, weak-match ->
    candidates only, no results). NOT yet tested against the live Home Depot
    catalog API — mock only so far.

  - RESOLVED (Jul 2026, this pass): call_claude_v2() + build_claude_document_
    content() near the bottom of this file replace call_claude() from
    app.py. Both the addendum and inspection report now go to Claude as
    native PDF/vision (extract_pdf_text is no longer used for the
    addendum), and the four real-world cases Jason described are handled
    explicitly: clean typed addendum, scanned addendum, no separate
    addendum at all (inspection report carries the ask), and a marked-up
    inspection report used as the addendum. Tested the content-building
    logic against all four have/don't-have combinations — branches
    correctly and degrades gracefully if a "PDF" can't be page-counted.
    NOT yet tested against the real Anthropic API (only the content-
    building function was tested; the actual API call path is unchanged
    from call_claude() other than the payload contents).
  - search_home_depot_catalog() still isn't called anywhere in the
    reasoning/output flow — material_lines are still LLM-guessed costs, not
    resolved against live Home Depot pricing yet.
  - Still not tested against the real live JobTread API (only against a
    mock query function) — needs Jason's go-ahead before creating a real
    test cost group.
"""

import re
import io
import json
import base64
import urllib.request
import urllib.error
import http.client
import os

# Resolve the historical CSV relative to THIS file's directory, not the
# process's current working directory. Render (or any deployment) may not
# invoke app.py with the repo root as cwd, and a relative "just the
# filename" default would then silently fail to find the CSV (degrading
# gracefully per load_historical_reference_examples's own design — no
# crash, just quietly no historical examples in the prompt). Resolving
# against __file__ means it works regardless of cwd, as long as the CSV
# ships in the same directory as this module (it does — same repo).
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HISTORICAL_CSV = os.path.join(
    _MODULE_DIR, "historical_home_and_closing_repairs_budget_detail_dedup.csv"
)

# Redland Electric (OCC's electrical sub) real invoice/estimate history —
# rebuilt Jul 2026 by pulling 46 real Redland email threads (invoices,
# estimates, receipts) directly from Jason's Gmail via the Housecall Pro
# notification emails, which embed the actual line-item table (services,
# qty, unit price, amount, scope description) right in the email HTML for
# invoices — no PDF parsing needed. 21 unique jobs / 36 line items kept
# (Oct 2024-Jul 2026), replacing the earlier CSV of the same name that went
# missing from this repo. See redland_electric_invoice_history.csv itself
# for the full real scope text per line — this is the calibration source
# for STEP 3B's sub_scope_price reasoning (see load_redland_reference_examples()).
DEFAULT_REDLAND_CSV = os.path.join(
    _MODULE_DIR, "redland_electric_invoice_history.csv"
)


# ─────────────────────────────────────────────────────────────────────────
# Real 3-digit cost codes (Jason's chosen set — see cost_codes_3digit.csv).
# Claude assigns one of these exact category names per cost group (new
# "cost_code" field in the schema below); add_cost_groups_v2() looks up the
# real ID here instead of hardcoding Uncategorized. No 3-digit code exists
# for "Garage" specifically — garage-related repairs get whichever real
# trade code actually applies (a garage door opener is Electrical or
# Hardware, garage framing is Framing, etc.), not a dedicated bucket.
# ─────────────────────────────────────────────────────────────────────────

COST_CODE_MAP = {
    "Project Management": "22PETC5BN43J",
    "General Conditions": "22PETCH6zZhB",
    "In-House Labor": "22PETCKqJVWh",
    "Subcontractor Labor": "22PETCNDcfDc",
    "Internal Use": "22PETCRMQJUW",
    "Demolition": "22PETCV44N5r",
    "Temporary Protection": "22PETCZhucah",
    "Cleanup & Disposal": "22PEisAAMqHT",
    "Framing": "22PETCnHxzta",
    "Windows & Doors": "22PETCw3Y5uW",
    "Insulation Materials": "22PETCzzFKms",
    "Roofing Materials": "22PETD7NBVn6",
    "Siding & Trim": "22PETDKgbpyb",
    "Gutters": "22PETDUJiBpT",
    "Exterior Paint": "22PEirZPQTCv",
    "Plumbing": "22PEirbTJDhB",
    "Electrical": "22PEircqrwCW",
    "HVAC": "22PEirdn7FzR",
    "Gas Line Work": "22PEirfSpRRw",
    "Crawlspace Work": "22PEirgpMcRX",
    "Masonry Materials": "22PEiri3uvVN",
    "Concrete": "22PEirjXp7Vd",
    "Drywall": "22PEirkhpFRY",
    "Trim & Millwork": "22PEirnGfCx8",
    "Interior Paint": "22PEirpS2yfs",
    "Flooring": "22PEirrujXsu",
    "Cabinetry": "22PEirswMmvP",
    "Countertops": "22PEiruPd5SV",
    "Hardware": "22PEirvgWyqi",
    "Specialty Items": "22PEirws4B2K",
    "Shower Glass": "22PEiryGteTJ",
    "Appliance Selection": "22PEirzQU5Wx",
}

COST_CODE_UNCATEGORIZED = "22P9ppJUAHXn"  # fallback if Claude's pick isn't in the map above


# ─────────────────────────────────────────────────────────────────────────
# NEW SYSTEM PROMPT — replaces the "REPAIR PRICING REFERENCE" flat-price
# section of SYSTEM_PROMPT in app.py. Everything else in the current prompt
# (company info, out-of-scope rules, customer-facing output rules, scope
# rules) stays as-is; this is the piece that changes HOW pricing is derived.
# ─────────────────────────────────────────────────────────────────────────

ESTIMATING_LOGIC_SECTION = """
ESTIMATING METHOD — read carefully, this replaces flat price lookup:

For each repair item, reason through it the way an experienced OCC estimator
would, in this order:

STEP 1 — QUANTITY: Determine the actual quantity of work from the inspection
report's TEXT and PHOTOS (the report is provided as a native PDF — look at
the photos and any circled/handwritten markups, not just the printed
findings; photos often show the true extent of damage that the inspector's
one-line text doesn't capture, e.g. "damaged siding" might be 3 boards or an
entire wall section). State your quantity assumption explicitly in the
"quantity_note" field (e.g. "~24 linear ft of fascia based on photo showing
full rear eave" or "single fixture, no photo — assumed 1 unit"). If you
truly cannot tell, make the most reasonable assumption for a typical
closing-repair scope of that type and flag "confidence": "low".

IMPORTANT — photos are ground truth, the inspector's wording is not. Home
inspectors are generalists, not tradespeople, and sometimes misdescribe or
underdiagnose what they're looking at (e.g. calling an active plumbing leak
"water staining," describing a full missing tile section as "cracked tile,"
or missing that "wood rot" is actually a pest/termite issue). When a photo
clearly shows something DIFFERENT from what the report's text says — not
just a different quantity, but a different actual problem, cause, or
severity — trust what is visible in the photo and estimate/classify/describe
based on that, not the inspector's wording. Call this out explicitly in
"quantity_note" whenever it happens (e.g. "Report says 'minor water
staining' but photo shows an active supply-line leak — estimated as a
plumbing repair, not a cosmetic one") so Jason's team can see exactly where
and why the AI overrode the report's text. If a photo is unclear,
low-resolution, or genuinely isn't provided for a given item, say so plainly
in "quantity_note" instead of inventing visual detail you can't actually
see — the goal is trustworthy photo-based judgment, not the appearance of it.

STEP 2 — CLASSIFY LABOR: sub vs in_house per the LABOR CLASSIFICATION rules
elsewhere in this prompt (including the JOB-WIDE BUNDLING RULE — review the
WHOLE job's items in a trade together before classifying each one, not item
by item in isolation, since a small item can flip from in_house to sub
depending on what else is happening in that same trade on this job). If a
single root problem genuinely needs BOTH an in-house fix and a sub fix (e.g.
a sub-scope plumbing leak plus in-house drywall repair from the resulting
water damage), set "labor" to "mixed" instead of choosing one — see STEP 3C.

STEP 2B — ASSIGN A COST CODE: set "cost_code" to EXACTLY one of these real
category names (copy the spelling exactly, this maps directly to a real
JobTread cost code — do not invent a category that isn't in this list):
  Project Management, General Conditions, In-House Labor, Subcontractor
  Labor, Internal Use, Demolition, Temporary Protection, Cleanup &
  Disposal, Framing, Windows & Doors, Insulation Materials, Roofing
  Materials, Siding & Trim, Gutters, Exterior Paint, Plumbing, Electrical,
  HVAC, Gas Line Work, Crawlspace Work, Masonry Materials, Concrete,
  Drywall, Trim & Millwork, Interior Paint, Flooring, Cabinetry,
  Countertops, Hardware, Specialty Items, Shower Glass, Appliance Selection
Pick the single best-fitting trade/category for the repair — e.g. a leaking
exterior hose bib is "Plumbing," a GFCI outlet is "Electrical," a torn
window screen is "Windows & Doors," a cracked driveway is "Concrete," a
loose deck board is "Siding & Trim" (not "Framing" unless it's structural
framing underneath, not the visible board itself). There is no dedicated
"Garage" category — classify garage-related repairs by the actual trade
involved instead (a garage door opener repair is "Electrical" or
"Hardware," not a separate bucket).

STEP 3A — IF IN-HOUSE: break the work into
  - "labor_lines": one or more {{"trade": "...", "hours": N, "rate": 89.00}}
    entries. Estimate hours using realistic residential-repair production
    rates for that trade and quantity — calibrate against REAL_HISTORICAL_
    EXAMPLES below for similar repair types where available (e.g. drywall
    patch ~0.5-1.5 hrs per patch depending on size incl. texture/paint; deck
    board replacement ~0.5-0.75 hrs/board; exterior caulking ~15-20 linear
    ft/hr). Always use rate 89.00 (the billed rate — OCC's real internal
    labor cost is $55/hr, but that's applied automatically when this gets
    written to JobTread; Claude should only ever state the 89.00 billed
    rate here, never do the $55 math itself).
  - "material_lines": one or more {{"item": "...", "qty": N, "unit": "...",
    "unit_cost": N}}. Use realistic current US retail material costs (e.g.
    a standard 1x6 PT deck board ~$12-18 each, a tube of exterior sealant
    ~$6-9). unit_cost is COST, not client price — markup is applied
    separately, do not inflate it yourself.
  Multiple small in-house tasks can share one cost group if the inspection
  report groups them together (e.g. one "Exterior Wood Rot" group with
  separate labor + material lines per location).

  HOURS ROUNDING / NON-PRODUCTIVE TIME BUFFER: always pad your estimated
  hours upward a bit to cover realistic non-productive time (gathering
  tools, moving around the property, cleanup/pack-up) — this is deliberate
  policy, not sloppy rounding. It applies to bigger tasks too, not just
  quick ones (e.g. a task that might realistically take 3 hours often gets
  quoted closer to 4) — the ratio isn't fixed, use judgment, but default to
  generous rather than lean.

  JOB MINIMUM LABOR: every job that has any in-house labor at all is billed
  for AT LEAST 3 hours of in-house labor across the WHOLE job (this covers
  drive time and setup for the visit) — this is a whole-job floor, not a
  per-item minimum. If your itemized labor_lines already add up to 3+ hours
  across the job (common once there are a few items), no adjustment is
  needed. If the job only has one small in-house item, make sure its hours
  reflect this — do not quote a single tiny in-house item at well under 3
  hours total. (The system also enforces this floor programmatically as a
  safety net — see enforce_minimum_labor_hours() — but estimate it correctly
  yourself first.)

  STRUCTURAL SHORING / ELLIS JACKS: when an inspection report calls out
  improper temporary foundation support in the crawlspace — e.g. screw jack
  posts (the adjustable steel posts sold at Home Depot), a dry-stacked CMU
  block stack, or any other improvised point shoring — this is IN-HOUSE
  work (OCC's crew replaces these directly; it is not part of the
  crawlspace-moisture-mitigation sub scope). Always specify a real Ellis
  jack (search the Home Depot catalog for "Ellis jack" — OCC's own catalog
  lists them by model number, e.g. "STL 22"-style codes) sized to the
  span/height needed, paired with a 12x12 base plate (catalog item like
  "BASE12"). Use the same Ellis-jack-plus-base-plate approach for any drop
  girder repair or headered joist repair that needs a permanent support
  point, not just for replacing bad temporary shoring.

STEP 3B — IF SUB (electrical / major HVAC / major plumbing / crawlspace):
  do NOT itemize hours or materials — subs bill OCC scope-based or day-rate
  per visit, not itemized labor+materials, so a granular breakdown here
  would be fake precision. Instead reason to a single "sub_scope_price"
  (OCC's cost from the sub, before markup) using the real historical
  reference ranges below. Set both labor_lines and material_lines to empty
  arrays when using sub_scope_price.

  REAL SUB COST REFERENCE (from actual invoices — use as anchors, adjust for
  described scope/severity, do not just pick the midpoint blindly):
    Electrical (Redland Electric):
      - Trip fee / minimum service call: $200-250 flat, regardless of scope
      - Simple single-fixture repair (e.g. GFCI swap, box extension,
        neutral repair): $25-300 per item depending on complexity
      - Exterior GFCI receptacle install: ~$150-300/unit
      - Garage door opener rewiring/relocation: ~$400/unit
      - LED fixture install (per fixture, bulk): ~$250/fixture
      - Fixture correction/troubleshoot (e.g. code-compliance fix): ~$150-450/unit
      - Bundle of several small closing-repair-style items in one visit:
        day rate ~$1,500-1,875/day (roughly $187-235/hr effective for a
        multi-item punch list, NOT per-item)
      - Panel replacement/relocation, major rewiring/code corrections:
        $1,800-4,650 — highly scope-dependent, treat as "quote required"
        if the addendum/report doesn't give enough detail to size it
    Crawlspace/Foundation (Crawlspace Medic): use the ranges already listed
    in the CRAWLSPACE/FOUNDATION section of this prompt — those came from
    real quotes, not invoices, so treat them as reasonable but softer
    anchors than the electrical numbers above.

  QUOTE-REQUIRED CATEGORIES — do not guess a sub_scope_price for these if
  the addendum/report doesn't give enough detail to size them responsibly;
  instead OMIT the cost group entirely and add it to "skipped_items" with a
  reason like "requires an on-site sub quote — insufficient detail to price
  responsibly": panel replacement/relocation or major rewiring/code
  corrections; water heater replacement; major HVAC system/compressor
  replacement; cast iron drain replacement or any re-pipe work; sill plate
  replacement. This list expands the original panel-only safety net — when
  in doubt on a big-ticket sub item with vague scope, flag it for a real
  quote rather than fabricating a number.

STEP 3C — IF MIXED (one root problem needs both in-house AND sub work):
  set "labor" to "mixed" and fill in BOTH parts within this SAME cost
  group — do not split it into two groups. Fill "sub_scope_price" for the
  sub portion (per STEP 3B) AND "labor_lines"/"material_lines" for the
  in-house portion (per STEP 3A), itemized separately within the group so
  it's clear what's sub work vs. in-house work even though it's one line
  item on the report (e.g. a sub-scope plumbing leak repair plus the
  in-house drywall patch it caused).

STEP 4 — CONFIDENCE: set "confidence" to "high" (clear quantity + typical
scope), "medium" (reasonable assumption, some ambiguity), or "low" (report
gives little to go on — flag these prominently so Jason's team spot-checks
before the estimate goes to a client). A low-confidence item does NOT get
excluded — estimate your best case and flag it, per the existing best-effort
gating rules.

HISTORICAL BLENDED PRICE RANGES (sanity-check only — NOT a lookup table):
The per-category price lists below are real past OCC job totals (already
blended labor+material, already marked up). Do not pick from this list
directly anymore. Use it only as a plausibility check: if your computed
labor+material total for a comparable repair lands wildly outside this
historical range with no scope difference to explain it, reconsider your
quantity/hours/material assumptions before finalizing.

REAL_HISTORICAL_EXAMPLES below (inserted at prompt-build time by
build_full_estimating_prompt()) are actual past OCC cost groups — real
material names, real quantities, real hours. Use them as calibration for
"what does a realistic hours-and-materials breakdown look like for this kind
of repair," NOT as a lookup table to copy verbatim (the repair in front of
you will differ in scope/quantity every time).
"""


# ─────────────────────────────────────────────────────────────────────────
# REAL HISTORICAL REFERENCE EXAMPLES — few-shot calibration, not a lookup
# table. Selected explicitly by (job_id, group_name) from the 5,353-line
# deduped historical pull (historical_home_and_closing_repairs_budget_detail_
# dedup.csv, 371 real Home Repair / Closing Repair jobs). Picked for trade
# diversity (plumbing, electrical, HVAC/vents, doors/hardware, masonry,
# crawlspace, painting/caulk, mold) and for being clean 2-3 line examples
# (one labor line + 1-2 real material lines) rather than messy multi-visit
# groups. Deliberately NOT auto-matched by text-similarity — an earlier
# attempt to fuzzy-match group names against a separate 310-item taxonomy
# produced bad pairings (e.g. "Sink Drain Repair" -> "door stops" at 0.49
# similarity) and was discarded; explicit ID selection avoids that failure
# mode entirely.
#
# NOTE ON DATA QUALITY: identify labor lines by item_name == "Hourly Rate",
# NOT by the cost_type field — historically ~62% of Hourly Rate lines were
# mistagged cost_type=Materials instead of Labor (confirmed across the full
# dataset), so trusting cost_type would silently drop real labor examples.
# Also: many historical material lines have a blank quantity field even
# though a real unit_cost/unit_price exists — treat blank quantity as 1 when
# formatting, don't discard the line.
# ─────────────────────────────────────────────────────────────────────────

HISTORICAL_REFERENCE_GROUP_KEYS = [
    ("22PHKJegqZ2s", "Minor Toilet Repairs"),
    ("22PFG8syVQ2x", "8.8.1 - Exhaust Fan Replacement"),
    ("22PLwYmBE3yS", "3.4.1 - Vent Cover Replacement"),
    ("22PJCduXJieh", "Dryer Vent Repair"),
    ("22PWtiS28C2m", "Rusted Gas Line Repair"),
    ("22PZF72zyUkg", "Secure Bathroom Sink"),
    ("22PSgZKx7CiH", "7.4.1 - Interior Window Trim Repair"),
    ("22PFP4Q53TVd", "8.2.2 - Seal Gap at Back/Side Splash"),
    ("22PGv55Kwy4F", "Mold/Fungi Treatment"),
    ("22PLVzQJaLpn", "8.3.3 - Single Pole Breaker Replacement"),
    ("22PLbPHMRSaP", "Miscellaneous Door Adjustments"),
    ("22PXGHWVYdCu", "9.7.1 - Light Bulb Replacement"),
    ("22PSgZKx7CiH", "4.3.3 - Loose Stone Veneer Repair"),
    ("22PLVzQJaLpn", "8.5.2 - Secure Crawl Space Wiring"),
    ("22PTZ9YAVRai", "Exterior GFCI Outlet Installation"),
    ("22PHKJegqZ2s", "Exterior Spigot Repair"),
    ("22PHJvyu7ymv", "Caulk Countertop/Backsplash Gaps"),
]

# RESOLVED (Jul 2026 pricing Q&A): "Exterior Spigot Repair" above is CORRECT
# as an in-house example — Jason confirmed a spigot replacement is in-house
# when it's the ONLY plumbing item in the job. It only bundles into a
# plumber's sub visit under the JOB-WIDE BUNDLING RULE (see LABOR
# CLASSIFICATION in app.py's SYSTEM_PROMPT) when the same job ALSO has a
# real plumbing sub-scope trigger (water heater replacement, a re-pipe,
# several leaking drains, cast iron drain work). No conflict — this example
# stays in the historical reference list unchanged.


def load_historical_reference_examples(
    csv_path=None,
    group_keys=None,
):
    """Pull the exact real cost groups listed in HISTORICAL_REFERENCE_GROUP_KEYS
    out of the deduped historical CSV and format them as a compact few-shot
    text block for the prompt.

    Returns "" (not an exception) if the CSV isn't found, so prompt-building
    degrades gracefully instead of crashing when this runs somewhere the CSV
    isn't deployed yet. Default csv_path resolves relative to this module's
    own directory (DEFAULT_HISTORICAL_CSV), not the process cwd — important
    for deployment (Render may not run app.py with the repo root as cwd).
    """
    import csv as _csv

    csv_path = csv_path if csv_path is not None else DEFAULT_HISTORICAL_CSV
    group_keys = group_keys if group_keys is not None else HISTORICAL_REFERENCE_GROUP_KEYS
    if not os.path.exists(csv_path):
        print(f"  WARNING: historical reference CSV not found at '{csv_path}' — "
              f"prompt will build WITHOUT real historical examples. Check that "
              f"the CSV shipped alongside estimate_v2_draft.py in this deploy.")
        return ""

    wanted = set(group_keys)
    matched = {k: [] for k in group_keys}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            key = (row["job_id"], row["group_name"])
            if key in wanted:
                matched[key].append(row)

    lines = ["REAL_HISTORICAL_EXAMPLES (actual past OCC cost groups — real "
             "materials, real quantities, real hours; calibration only, do "
             "not copy verbatim):"]
    for key in group_keys:
        items = matched.get(key, [])
        if not items:
            continue
        lines.append(f"\n- {key[1]}")
        for it in items:
            name = it["item_name"].strip()
            qty = it["quantity"].strip() or "1"
            cost = it["unit_cost"].strip()
            price = it["unit_price"].strip()
            if name.lower() == "hourly rate":
                lines.append(f"    labor: {qty} hrs @ ${cost} cost / ${price or '89'} billed")
            else:
                lines.append(f"    material: {name} — qty {qty} @ ${cost} cost / ${price or '?'} price")
    return "\n".join(lines)


# Diverse subset of real Redland job_numbers picked from
# redland_electric_invoice_history.csv for STEP 3B few-shot calibration —
# covers the full range a real closing-repair/sub-scope decision needs to
# span: trip-fee-only visit, several small single-fixture repairs in one
# invoice, per-unit code-correction pricing, a harder-access per-unit repair,
# a quote-that-changed-based-on-field-conditions example, a full day-rate
# multi-item punch-list job, a flat-price fixture-bundle job, and a
# big custom-quoted panel/feeder job (the "quote required" category).
REDLAND_REFERENCE_JOB_KEYS = [
    "INV-2086",  # minimum service-call trip charge, single small item
    "INV-1841",  # several small single-fixture repairs in one visit ($25-$175 each)
    "INV-1915",  # per-unit code-correction pricing + one harder-access repair
    "INV-1908",  # per-unit garage door opener rewiring
    "INV-1889",  # quote ($1,000) revised down to actual ($600) based on field conditions
    "INV-2036",  # full day-rate multi-item whole-house punch list + real materials
    "INV-2052",  # flat-price fixture-bundle job (exterior lighting), no materials line
    "EST-281",   # big custom-quoted panel/feeder rewire — "quote required" category anchor
]


def load_redland_reference_examples(csv_path=None, job_keys=None):
    """Pull a diverse subset of real Redland Electric jobs out of
    redland_electric_invoice_history.csv and format them as a compact
    few-shot text block for STEP 3B (sub-scope electrical pricing).

    Same graceful-degradation pattern as load_historical_reference_examples():
    returns "" instead of raising if the CSV isn't present, so a deploy that's
    missing this file just quietly falls back to the static REAL SUB COST
    REFERENCE ranges already in ESTIMATING_LOGIC_SECTION instead of crashing.
    """
    import csv as _csv

    csv_path = csv_path if csv_path is not None else DEFAULT_REDLAND_CSV
    job_keys = job_keys if job_keys is not None else REDLAND_REFERENCE_JOB_KEYS
    if not os.path.exists(csv_path):
        print(f"  WARNING: Redland reference CSV not found at '{csv_path}' — "
              f"prompt will build WITHOUT real Redland examples (falls back to "
              f"the static REAL SUB COST REFERENCE ranges only).")
        return ""

    wanted = set(job_keys)
    by_job = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            if row["job_number"] in wanted:
                by_job.setdefault(row["job_number"], []).append(row)

    lines = ["REAL_REDLAND_ELECTRIC_EXAMPLES (actual past invoices/estimates "
             "from OCC's electrical sub — real scope text, real billed prices "
             "= OCC's real cost before the 45% sub markup; calibration only, "
             "do not copy verbatim):"]
    for key in job_keys:
        items = by_job.get(key, [])
        if not items:
            continue
        addr = items[0]["service_address"]
        total = items[0]["job_total_billed"]
        lines.append(f"\n- {key} ({addr}) — job total billed by Redland: ${total}")
        for it in items:
            desc = it["line_item_description"].strip()
            qty = it["quantity"].strip() or "1"
            price = it["unit_price"].strip()
            amt = it["line_amount"].strip()
            lines.append(f"    {desc} — qty {qty} @ ${price} = ${amt}")
            notes = it["scope_notes"].strip()
            if notes:
                lines.append(f"      scope: {notes}")
    return "\n".join(lines)


def build_full_estimating_prompt(csv_path=None, redland_csv_path=None):
    """Assemble the piece of the system prompt that replaces the old flat
    price-lookup section: the estimating method/logic + real historical
    reference examples. This is what actually wires the historical data into
    the live prompt (Task #8) — everything above this point defines the
    pieces, this is where they get glued together.

    Also splices in real Redland Electric examples (load_redland_reference_
    examples()) right after the in-house historical examples, so STEP 3B's
    sub_scope_price reasoning has real scope-to-price anchors to work from,
    not just the static numeric ranges already in ESTIMATING_LOGIC_SECTION.
    """
    examples_text = load_historical_reference_examples(csv_path)
    redland_text = load_redland_reference_examples(redland_csv_path)

    prompt = ESTIMATING_LOGIC_SECTION
    if examples_text:
        prompt += "\n\n" + examples_text
    if redland_text:
        prompt += "\n\n" + redland_text
    return prompt


# ─────────────────────────────────────────────────────────────────────────
# NEW OUTPUT SCHEMA (replaces the old flat "price" field per cost group)
# ─────────────────────────────────────────────────────────────────────────

EXAMPLE_OUTPUT_SCHEMA = """
{
  "property_address": "address",
  "client_name": "name",
  "client_phone": "phone",
  "client_email": "email",
  "cost_groups": [
    {
      "title": "3.2 - Wood Rot at Front Door",
      "description": "- Remove and replace deteriorated wood casing...\\n\\nNOTE: ...",
      "labor": "in_house",
      "cost_code": "Siding & Trim",
      "quantity_note": "~8 linear ft of casing, based on photo showing damage across full door width",
      "confidence": "medium",
      "labor_lines": [
        {"trade": "carpentry", "hours": 2.0, "rate": 89.00}
      ],
      "material_lines": [
        {"item": "5/4x4 primed pine casing, 8 ft", "qty": 1, "unit": "board", "unit_cost": 14.00},
        {"item": "exterior wood filler/epoxy", "qty": 1, "unit": "tube", "unit_cost": 11.00},
        {"item": "primer + paint (prorated)", "qty": 1, "unit": "allowance", "unit_cost": 18.00}
      ],
      "sub_scope_price": null,
      "notes": null
    },
    {
      "title": "5.1 - GFCI Outlets (3)",
      "description": "- Install three new GFCI-protected exterior receptacles...",
      "labor": "sub",
      "cost_code": "Electrical",
      "quantity_note": "3 exterior receptacles called out in addendum",
      "confidence": "high",
      "labor_lines": [],
      "material_lines": [],
      "sub_scope_price": 750.00,
      "notes": null
    },
    {
      "title": "6.4 - Plumbing Leak and Resulting Drywall Damage",
      "description": "- Repair active supply line leak under kitchen sink (plumber).\\n- Patch and repaint water-damaged drywall below sink (in-house).",
      "labor": "mixed",
      "cost_code": "Plumbing",
      "quantity_note": "~2 sq ft drywall patch based on photo of water staining",
      "confidence": "medium",
      "labor_lines": [
        {"trade": "drywall", "hours": 1.5, "rate": 89.00}
      ],
      "material_lines": [
        {"item": "drywall patch kit + joint compound", "qty": 1, "unit": "kit", "unit_cost": 15.00},
        {"item": "matching interior paint (prorated)", "qty": 1, "unit": "allowance", "unit_cost": 12.00}
      ],
      "sub_scope_price": 300.00,
      "notes": "labor='mixed' — sub handles the plumbing repair (sub_scope_price), in-house handles the drywall patch (labor_lines/material_lines), both under one cost group since it's one root problem"
    }
  ],
  "total": 0.00,
  "skipped_items": ["item - reason"],
  "needs_consult": false,
  "consult_reason": ""
}
"""


# ─────────────────────────────────────────────────────────────────────────
# Home Depot Global Catalog search — CONFIRMED WORKING (Jul 2026).
# Real field is "homeDepotProducts", a TOP-LEVEL query field (sibling of
# "organization", not nested under it — that's why earlier guesses nested
# under organization.<name> all failed). Found by watching JobTread's own
# frontend network traffic while using Catalog -> Global Catalog -> Home
# Depot -> search, then confirmed working with the scripting API key too
# (not just a logged-in browser session).
#
# Not yet wired into the estimating flow below — this is the building block
# for it. Intended use: once Claude drafts a material_line description
# (e.g. "30-yr architectural shingles"), call this to search Home Depot and
# either pick the best real match automatically or attach a short list of
# real candidates (with live prices/SKUs) for Jason's team to confirm,
# rather than trusting Claude's guessed unit_cost.
# ─────────────────────────────────────────────────────────────────────────

HOME_DEPOT_STORE_ID = "1126"  # Greer, SC — OCC's default Home Depot store in JobTread


def search_home_depot_catalog(search_query, jobtread_query_fn, org_id, page=1):
    """Search the live Home Depot Global Catalog via JobTread's Pave API.

    Returns (success, list_of_products). Each product dict has: id, name,
    brand, department, modelNumber, storeSkuNumber, unitCost (live price),
    unitOfMeasure, imageUrl, link (real homedepot.com product URL).

    jobtread_query_fn contract: takes a query dict, returns the parsed
    response dict directly (or raises on failure) — matching app.py's real
    jobtread_query(), same contract add_cost_groups_v2() already uses. This
    function used to assume jobtread_query_fn returned an (ok, resp) tuple
    instead, which would have silently broken every catalog search against
    the real jobtread_query() (caught the TypeError, but always returned
    "no match") — found and fixed Jul 2026 while wiring this into app.py.
    """
    try:
        resp = jobtread_query_fn({
            "homeDepotProducts": {
                "$": {"searchQuery": search_query, "organizationId": org_id,
                      "storeId": HOME_DEPOT_STORE_ID, "page": page},
                "nodes": {
                    "id": {}, "name": {}, "brand": {}, "department": {},
                    "modelNumber": {}, "storeSkuNumber": {}, "unitCost": {},
                    "unitOfMeasure": {}, "imageUrl": {}, "link": {}
                },
                "nextPage": {}
            }
        })
    except Exception as e:
        print(f"  Home Depot catalog search failed for '{search_query[:60]}': {e}")
        return False, []
    return True, resp.get("homeDepotProducts", {}).get("nodes", [])


def _match_score(query, product_name):
    """Cheap word-overlap confidence score between a material description
    and a candidate Home Depot product name. Not fuzzy/edit-distance
    matching (that approach already failed once on this project — see the
    pricing_library.csv fuzzy-matching caveat in CLAUDE.md — the difference
    here is this is a confidence gate on top of a REAL catalog search result,
    not a blind text-similarity match against an unrelated taxonomy, so a
    wrong guess just falls back to the LLM's cost instead of a wrong price).
    """
    stop = {"a", "an", "the", "of", "for", "with", "in", "to", "and", "or",
            "1", "1x", "each", "per"}
    q_words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in stop and len(w) > 2}
    n_words = {w for w in re.findall(r"[a-z0-9]+", product_name.lower()) if w not in stop and len(w) > 2}
    if not q_words:
        return 0.0
    overlap = q_words & n_words
    return len(overlap) / len(q_words)


def resolve_material_lines_with_catalog(estimate, jobtread_query_fn, org_id,
                                         auto_apply_threshold=0.5, top_n=3):
    """Resolve each material_line's LLM-guessed cost against the live Home
    Depot Global Catalog (search_home_depot_catalog), so the estimate has
    real live-priced products where a confident match exists instead of a
    guessed cost everywhere.

    For each material line:
      - Search the catalog using the line's "item" description.
      - Score candidates by word overlap with the description.
      - If the best match scores >= auto_apply_threshold: REPLACE unit_cost
        with the real catalog unitCost, and attach "catalog_match" (name,
        sku, link) to the line so Jason's team can see what was substituted.
      - Otherwise: leave the LLM's guessed unit_cost untouched, but attach
        "catalog_candidates" (top N real matches with real prices) so the
        team has real options to pick from manually instead of a blind
        guess with nothing to check it against.
      - If the search itself fails or returns nothing: leave the line as-is,
        unchanged, no crash — this must never block an estimate from going
        out just because a catalog lookup had a bad day.

    Mutates and returns `estimate` in place. Returns (estimate, stats) where
    stats = {"searched": N, "auto_matched": N, "candidates_only": N, "no_match": N}.
    """
    stats = {"searched": 0, "auto_matched": 0, "candidates_only": 0, "no_match": 0}

    for group in estimate.get("cost_groups", []) or []:
        for line in group.get("material_lines", []) or []:
            item_desc = (line.get("item", "") or "").strip()
            if not item_desc:
                continue

            stats["searched"] += 1
            try:
                ok, products = search_home_depot_catalog(item_desc, jobtread_query_fn, org_id)
            except Exception as e:
                print(f"  Catalog search failed for '{item_desc[:60]}': {e}")
                ok, products = False, []

            if not ok or not products:
                stats["no_match"] += 1
                # Jason's ask (Jul 2026 pricing Q&A): when there's no
                # catalog match at all, still flag it so his team knows to
                # manually check the price rather than silently trusting
                # Claude's own guessed cost with no visible signal. (A real
                # secondary price source — e.g. searching Amazon or another
                # retailer when Home Depot's catalog comes up empty — isn't
                # wired in yet; this is intentionally just a flag for now,
                # not a substitute lookup.)
                line["catalog_no_match"] = True
                continue

            scored = sorted(
                ((_match_score(item_desc, p.get("name", "")), p) for p in products),
                key=lambda t: t[0], reverse=True
            )
            best_score, best_product = scored[0]

            if best_score >= auto_apply_threshold and best_product.get("unitCost"):
                original_cost = line.get("unit_cost")
                line["unit_cost"] = float(best_product["unitCost"])
                # Full product detail carried through to add_cost_groups_v2()
                # so it can write a real description (+ attempt a photo
                # attachment) onto the JobTread cost item — not just swap
                # the price and drop everything else on the floor.
                line["catalog_match"] = {
                    "name": best_product.get("name"),
                    "brand": best_product.get("brand"),
                    "department": best_product.get("department"),
                    "modelNumber": best_product.get("modelNumber"),
                    "sku": best_product.get("storeSkuNumber"),
                    "unitOfMeasure": best_product.get("unitOfMeasure"),
                    "imageUrl": best_product.get("imageUrl"),
                    "link": best_product.get("link"),
                    "matched_score": round(best_score, 2),
                    "llm_guessed_cost": original_cost,
                }
                stats["auto_matched"] += 1
            else:
                line["catalog_candidates"] = [
                    {
                        "name": p.get("name"), "unit_cost": p.get("unitCost"),
                        "brand": p.get("brand"), "sku": p.get("storeSkuNumber"),
                        "imageUrl": p.get("imageUrl"), "link": p.get("link"),
                    }
                    for _, p in scored[:top_n]
                ]
                stats["candidates_only"] += 1

    return estimate, stats


# ─────────────────────────────────────────────────────────────────────────
# NEW JobTread write logic — replaces add_cost_groups() in app.py.
# Posts one CostItem per labor line and per material line (in-house), or
# one CostItem for the scoped sub price (sub) — instead of always one.
# ─────────────────────────────────────────────────────────────────────────

MATERIAL_MARKUP = 1.65   # cost + 65%
SUB_MARKUP = 1.45        # cost + 45%
LABOR_COST_RATE = 55.00  # OCC's real internal labor cost/hr — confirmed across
                          # 1,290 real historical "Hourly Rate" line items
                          # (~98% at exactly $55). Billed rate stays 89.00.
                          # Previously add_cost_groups_v2() recorded labor at
                          # unitCost == unitPrice (89.00 == 89.00), which
                          # tracked zero labor margin — this fixes that.

# Confirmed via jobtread_explore.py (Jul 2026 run) — real cost type IDs.
# JobTread already has dedicated Labor and Materials cost types; the current
# app.py never uses them, only Subcontractor/Other. Using the real ones here
# instead of lumping everything into "Other."
COST_TYPE_LABOR = "22P9ppJUAHYN"
COST_TYPE_MATERIALS = "22P9ppJUAHYP"
COST_TYPE_SUB = "22P9ppJUAHYQ"
COST_TYPE_OTHER = "22P9ppJUAHYR"  # kept for reference; no longer used below

# COST_CODE_MAP and COST_CODE_UNCATEGORIZED are defined above, near the top
# of the file, alongside the real 3-digit cost code data.

# Jason's rule (Jul 2026 pricing Q&A): "Generally we need to bill for at
# least 3 hours. If the job has only one small item that's not
# subcontractor then we need to minimum charge 3 hrs plus materials." This
# is a WHOLE-JOB floor on total in-house labor (covers drive time/setup for
# the visit), not a per-line-item minimum — see enforce_minimum_labor_hours().
MINIMUM_INHOUSE_LABOR_HOURS = 3.0


def enforce_minimum_labor_hours(estimate, minimum_hours=MINIMUM_INHOUSE_LABOR_HOURS,
                                 rate=89.00):
    """Enforce OCC's 3-hour minimum in-house labor charge per job, in code
    rather than trusting the LLM to self-police it consistently (same
    reasoning as compute_estimate_total() — Claude's own arithmetic/
    judgment on a hard business-policy floor shouldn't be the only thing
    standing between a real job and an underbilled one).

    Sums hours across every labor_lines entry in every cost group. If the
    job has ANY in-house labor at all and the total is below minimum_hours,
    appends a new, clearly-labeled cost group ("Minimum Labor Charge — Trip/
    Setup Time") with a single labor line making up the shortfall — a real,
    visible, auditable JobTread line item rather than silently inflating an
    existing one. Jobs with ZERO in-house labor (all-sub, or empty) are left
    untouched — the floor is specifically about dispatching OCC's own crew.

    Call this AFTER call_claude_v2() and BEFORE add_cost_groups_v2(), same
    place compute_estimate_total() gets called, so the written JobTread
    cost groups and the computed total both reflect the enforced floor.

    Mutates and returns the same estimate dict.
    """
    if not estimate:
        return estimate

    groups = estimate.get("cost_groups", []) or []
    total_hours = 0.0
    has_any_inhouse_labor = False
    for g in groups:
        for line in g.get("labor_lines", []) or []:
            hours = float(line.get("hours", 0) or 0)
            if hours > 0:
                has_any_inhouse_labor = True
                total_hours += hours

    if not has_any_inhouse_labor or total_hours >= minimum_hours:
        return estimate

    shortfall = round(minimum_hours - total_hours, 2)
    print(f"  Itemized in-house labor totaled {total_hours:.2f} hrs — below "
          f"the {minimum_hours:.0f}-hr job minimum, adding a {shortfall:.2f} hr "
          f"adjustment line")
    groups.append({
        "title": "Minimum Labor Charge — Trip/Setup Time",
        "description": ("- Minimum in-house labor charge for this visit, "
                         "covering trip time and job setup."),
        "labor": "in_house",
        "cost_code": "In-House Labor",
        "quantity_note": (f"Itemized in-house work totaled {total_hours:.2f} hrs "
                           f"— bumped to OCC's {minimum_hours:.0f}-hr job minimum "
                           f"per standing policy."),
        "confidence": "high",
        "labor_lines": [{"trade": "general", "hours": shortfall, "rate": rate}],
        "material_lines": [],
        "sub_scope_price": None,
        "notes": None,
    })
    estimate["cost_groups"] = groups
    return estimate


def compute_estimate_total(estimate):
    """Compute the real billed total programmatically from labor_lines,
    material_lines, and sub_scope_price — do NOT trust Claude's own "total"
    field for anything beyond a rough sanity check.

    Why: under the old flat schema, Claude's "price" per group already WAS
    the billed number, so summing them was correct. Under the new schema,
    Claude is explicitly told NOT to apply markup itself (material/labor
    lines are stated at cost/billed-rate, not client price), so its
    self-reported "total" field has no reliable way to reflect the real
    marked-up number. add_cost_groups_v2() already computes correct
    per-line pricing independently of this — this function exists so
    anything that needs a total (logging, email, projected budget) uses the
    same real math instead of trusting an LLM arithmetic guess.
    """
    if not estimate:
        return 0.0
    total = 0.0
    for group in estimate.get("cost_groups", []) or []:
        # NOTE (Jul 2026 pricing Q&A): a group's sub portion and in-house
        # portion are no longer mutually exclusive — "labor" can be "mixed"
        # when one root problem needs both a sub fix and an in-house fix
        # under the same cost group (see STEP 3C). So sum whichever pieces
        # of data are actually present, rather than branching on the
        # "labor" tag — a "mixed" group has both a real sub_scope_price AND
        # real labor_lines/material_lines, and both need to count.
        sub_cost = float(group.get("sub_scope_price", 0) or 0)
        if sub_cost > 0:
            total += round(sub_cost * SUB_MARKUP, 2)
        for line in group.get("labor_lines", []) or []:
            hours = float(line.get("hours", 0) or 0)
            rate = float(line.get("rate", 89.00) or 89.00)
            total += hours * rate
        for line in group.get("material_lines", []) or []:
            qty = float(line.get("qty", 0) or 0)
            unit_cost = float(line.get("unit_cost", 0) or 0)
            if qty > 0 and unit_cost > 0:
                total += round(qty * unit_cost * MATERIAL_MARKUP, 2)
    return round(total, 2)


# Real cost-item custom field NAMES on Jason's org (confirmed Jul 2026 via
# organization.customFields, filtered to targetType="costItem"): SKU (text,
# id 22PAMX7kB95M), Product Link (url, 22PAMX92jTKk), Service Category
# (option, 22PAMWeEp6jF), Specifications (text, 22PFnwKMHdVW).
#
# "Preferred Vendor" and "Internal Notes" were RENAMED by Jason (Jul 2026,
# same session) to "Brand" and "Model Number" respectively — same field ids
# (22PAKQnJBHGA, 22PCLg5dUPQ2), same text type, just relabeled — specifically
# to fix a semantic mismatch this code surfaced: those two fields were being
# reused to hold Brand/Model Number data (matching JobTread's own "Link to
# Global Catalog" import-mapping dialog default: Brand -> Preferred Vendor,
# Model Number -> Internal Notes), which was a confusing label for what was
# actually being stored there. Renaming (not adding new fields) was Jason's
# choice — keeps just the 6 existing fields, no new ones, and the Global
# Catalog import mapping should now show Brand -> Brand / Model Number ->
# Model Number automatically since it maps by field id, not label.
#
# createJob in app.py already proves the write mechanism: customFieldValues
# is passed as a plain {"Field Name": value} dict directly in the mutation's
# $ args (see job_cfv around app.py's create_job_record) — job-scoped custom
# fields resolve by NAME, not by id. createCostItem is expected to work the
# same way for costItem-scoped fields (not independently confirmed with a
# live write — my research key can only read, every create attempt returns
# "You don't have permission" regardless of whether the args are valid — so
# this needs a real check against the next live submission's JobTread job).
COST_ITEM_FIELD_SKU = "SKU"
COST_ITEM_FIELD_PRODUCT_LINK = "Product Link"
COST_ITEM_FIELD_BRAND = "Brand"
COST_ITEM_FIELD_MODEL_NUMBER = "Model Number"


def _build_material_custom_field_values(line):
    """For a confidently auto-matched material line, build the
    customFieldValues dict to write onto createCostItem — real SKU/link/
    brand/model into the actual custom fields Jason's team already uses,
    not a text blob. Returns {} if there's no confident catalog_match (the
    weak-match "candidates" case doesn't have one clean value per field, so
    that stays a text note — see _build_material_description).
    """
    catalog_match = line.get("catalog_match")
    if not catalog_match:
        return {}
    values = {}
    if catalog_match.get("sku"):
        values[COST_ITEM_FIELD_SKU] = str(catalog_match["sku"])
    if catalog_match.get("link"):
        values[COST_ITEM_FIELD_PRODUCT_LINK] = catalog_match["link"]
    if catalog_match.get("brand"):
        values[COST_ITEM_FIELD_BRAND] = catalog_match["brand"]
    if catalog_match.get("modelNumber"):
        # Field is now literally named "Model Number" (renamed from
        # "Internal Notes" by Jason) — no need for a "Model: " prefix on
        # the value itself anymore, the field label already says it.
        values[COST_ITEM_FIELD_MODEL_NUMBER] = catalog_match["modelNumber"]
    return values


def _build_material_description(line):
    """Build a short free-text description for a material cost item.
    Confirmed via the Pave API (Jul 2026) that costItem.description is a
    real, currently-unused field (queried an existing item, got back null).

    For a confident catalog_match, the STRUCTURED detail (SKU, link, brand,
    model) now goes into real custom fields instead (see
    _build_material_custom_field_values) — this just adds the real product
    name as a one-line description, so the item reads clearly at a glance.
    For a weak match (multiple candidates, nothing auto-selected), there's
    no single clean value to put in a structured field, so the candidate
    list stays here as free text.
    """
    catalog_match = line.get("catalog_match")
    if catalog_match:
        return catalog_match.get("name") or ""

    candidates = line.get("catalog_candidates")
    if candidates:
        lines = ["Possible Home Depot matches (not auto-selected — confirm before ordering):"]
        for c in candidates:
            bits = [c.get("name") or "Unnamed product"]
            if c.get("unit_cost"):
                bits.append(f"${c['unit_cost']}")
            if c.get("link"):
                bits.append(c["link"])
            lines.append("  - " + " | ".join(bits))
        return "\n".join(lines)

    if line.get("catalog_no_match"):
        # Jason's ask (Jul 2026 pricing Q&A): flag unmatched materials so
        # the team knows to manually verify price rather than trusting an
        # unlabeled AI guess. Standard 65% markup is still applied to the
        # guessed cost per his answer ("standard markup but flag it so I
        # can check") — this note is the visible check-flag, not a price
        # change.
        return "⚠️ No Home Depot catalog match found — cost is AI-estimated, please verify pricing before ordering."

    return ""


def _attach_catalog_image(jobtread_query_fn, org_id, cost_item_id, image_url, name):
    """Best-effort: attach a Home Depot product photo to a cost item, using
    the same createUploadRequest -> createFile(targetType, targetId, url)
    pattern app.py's attach_files() already uses successfully at the job
    level. NOT independently confirmed to work at targetType="costItem" —
    my research API key only has read access (creates return "You don't
    have permission"), so this couldn't be live-tested end to end. If it
    turns out targetType="costItem" isn't supported, this just fails
    silently (caller wraps it in try/except) and the estimate is unaffected
    either way — worth checking Render logs after the next real submission
    to confirm whether "Photo attach skipped" ever prints.
    """
    upload_resp = jobtread_query_fn({
        "createUploadRequest": {
            "$": {"organizationId": org_id, "url": image_url},
            "createdUploadRequest": {"id": {}}
        }
    })
    upload_id = upload_resp["createUploadRequest"]["createdUploadRequest"]["id"]
    jobtread_query_fn({
        "createFile": {
            "$": {"targetType": "costItem", "targetId": cost_item_id,
                  "name": name, "uploadRequestId": upload_id},
            "createdFile": {"id": {}}
        }
    })


def add_cost_groups_v2(job_id, estimate, jobtread_query_fn, org_id=None):
    """
    Create cost groups with MULTIPLE cost items each (labor + material
    lines for in-house work, a single scoped line for sub work) instead of
    today's one-item-per-group pattern.

    jobtread_query_fn: pass in app.py's jobtread_query() function so this
    stays a pure function you can unit test without hitting the real API.

    org_id: optional — only needed to attempt attaching a real Home Depot
    product photo to auto-matched material lines (see _attach_catalog_image).
    If omitted, materials still get a real text description (SKU/brand/link)
    just no photo attachment attempt.
    """
    if not estimate:
        return 0

    added_groups = 0
    cost_groups = estimate.get("cost_groups", []) or []

    for group in cost_groups:
        title = (group.get("title", "") or "").strip() or "Repair Item"
        description = (group.get("description", "") or "").strip()
        notes = (group.get("notes", "") or "").strip()
        labor = (group.get("labor", "") or "").strip().lower()
        quantity_note = (group.get("quantity_note", "") or "").strip()
        confidence = (group.get("confidence", "") or "").strip()
        cost_code_name = (group.get("cost_code", "") or "").strip()
        cost_code_id = COST_CODE_MAP.get(cost_code_name, COST_CODE_UNCATEGORIZED)
        if cost_code_name and cost_code_name not in COST_CODE_MAP:
            print(f"  NOTE: '{cost_code_name}' isn't a real cost code name — "
                  f"falling back to Uncategorized for group '{title[:50]}'")

        group_description = description
        if quantity_note:
            group_description += f"\n\n[Quantity assumption: {quantity_note}]"
        if confidence and confidence != "high":
            group_description += f"\n[Confidence: {confidence} — spot-check before sending]"
        if notes:
            group_description += f"\n\nNOTE: {notes}"

        try:
            resp = jobtread_query_fn({
                "createCostGroup": {
                    "$": {"jobId": job_id, "name": title[:100], "description": group_description or None},
                    "createdCostGroup": {"id": {}}
                }
            })
            group_id = resp["createCostGroup"]["createdCostGroup"]["id"]
        except Exception as e:
            print(f"  Skipping group '{title[:50]}' (create group failed): {e}")
            continue

        items_added_this_group = 0

        # NOTE (Jul 2026 pricing Q&A): sub and in-house are no longer
        # mutually exclusive at the group level. Jason confirmed that when
        # one root problem needs both a sub fix and an in-house fix (e.g. a
        # sub-scope plumbing leak plus the in-house drywall patch it
        # caused), he wants ONE cost group with multiple cost items inside
        # it, not two separate groups — see STEP 3C / "mixed" in
        # ESTIMATING_LOGIC_SECTION. So both blocks below run independently,
        # gated on whether their own data is actually present, instead of
        # branching on the "labor" tag (which used to force an either/or).
        # A normal "sub"-only group just won't have labor_lines/
        # material_lines populated, and a normal "in_house"-only group just
        # won't have a sub_scope_price — both fall through exactly as
        # before; only "mixed" groups now populate both blocks.

        # sub_scope_price is OCC's COST from the sub (before markup) — see
        # STEP 3B in ESTIMATING_LOGIC_SECTION ("OCC's cost from the sub,
        # before markup"). BUG FIX (Jul 2026): this used to divide
        # sub_scope_price by SUB_MARKUP to get "cost", i.e. treated it as
        # the already-marked-up CLIENT price instead — which would have
        # billed the client exactly OCC's real cost with zero margin on
        # every sub line item, while also writing a fabricated, too-low
        # "cost" into JobTread. Correct direction: sub_scope_price IS the
        # cost; multiply UP by SUB_MARKUP to get the billed price.
        sub_cost = float(group.get("sub_scope_price", 0) or 0)
        if sub_cost > 0:
            sub_price = round(sub_cost * SUB_MARKUP, 2)
            item_name = re.sub(r'^[\d\.\s]+[-–]?\s*', '', title).strip() or title
            try:
                jobtread_query_fn({
                    "createCostItem": {
                        "$": {
                            "costGroupId": group_id, "name": item_name[:100], "quantity": 1,
                            "unitCost": sub_cost, "unitPrice": sub_price,
                            "costCodeId": cost_code_id, "costTypeId": COST_TYPE_SUB
                        },
                        "createdCostItem": {"id": {}}
                    }
                })
                items_added_this_group += 1
            except Exception as e:
                print(f"  Failed to add sub cost item for '{title[:50]}': {e}")

        # In-house: one CostItem per labor line (costType=Labor), one per
        # material line (costType=Materials) — using JobTread's own real
        # cost types instead of lumping everything into "Other". Runs
        # whenever labor_lines/material_lines are present, regardless of
        # whether this group ALSO had a sub_scope_price above (mixed case).
        if True:
            for line in group.get("labor_lines", []) or []:
                hours = float(line.get("hours", 0) or 0)
                rate = float(line.get("rate", 89.00) or 89.00)
                trade = (line.get("trade", "") or "Labor").strip()
                if hours <= 0:
                    continue
                try:
                    jobtread_query_fn({
                        "createCostItem": {
                            "$": {
                                "costGroupId": group_id,
                                "name": f"{trade.capitalize()} labor"[:100],
                                "quantity": hours,
                                "unitCost": LABOR_COST_RATE,  # real internal cost ($55/hr)
                                "unitPrice": rate,             # billed rate (89.00)
                                "costCodeId": cost_code_id, "costTypeId": COST_TYPE_LABOR
                            },
                            "createdCostItem": {"id": {}}
                        }
                    })
                    items_added_this_group += 1
                except Exception as e:
                    print(f"  Failed to add labor line for '{title[:50]}': {e}")

            for line in group.get("material_lines", []) or []:
                qty = float(line.get("qty", 0) or 0)
                unit_cost = float(line.get("unit_cost", 0) or 0)
                item = (line.get("item", "") or "Material").strip()
                if qty <= 0 or unit_cost <= 0:
                    continue
                unit_price = round(unit_cost * MATERIAL_MARKUP, 2)
                item_description = _build_material_description(line)
                custom_field_values = _build_material_custom_field_values(line)
                base_args = {
                    "costGroupId": group_id,
                    "name": item[:100],
                    "quantity": qty,
                    "unitCost": unit_cost,
                    "unitPrice": unit_price,
                    "costCodeId": cost_code_id, "costTypeId": COST_TYPE_MATERIALS
                }
                enriched_args = dict(base_args)
                if item_description:
                    enriched_args["description"] = item_description
                if custom_field_values:
                    enriched_args["customFieldValues"] = custom_field_values

                # Try the enriched write first (description + custom fields).
                # Neither has been live-write-confirmed against createCostItem
                # specifically (description is a confirmed-real READ field;
                # customFieldValues is a confirmed-real WRITE arg on createJob,
                # not independently proven on createCostItem). If the enriched
                # call fails for ANY reason, fall back to the bare-minimum args
                # already proven to work in the first successful live run
                # (job 22PbETG4esX5) — so a bad new field costs us the extra
                # detail on that one line, never the line item itself.
                try:
                    resp = jobtread_query_fn({
                        "createCostItem": {"$": enriched_args, "createdCostItem": {"id": {}}}
                    })
                except Exception as enriched_err:
                    if enriched_args != base_args:
                        print(f"  Enriched material create failed for '{item[:50]}' "
                              f"({enriched_err}) — retrying with bare args (no description/custom fields)")
                        try:
                            resp = jobtread_query_fn({
                                "createCostItem": {"$": base_args, "createdCostItem": {"id": {}}}
                            })
                        except Exception as e:
                            print(f"  Failed to add material line for '{title[:50]}' (bare retry also failed): {e}")
                            resp = None
                    else:
                        print(f"  Failed to add material line for '{title[:50]}': {enriched_err}")
                        resp = None

                if resp is not None:
                    items_added_this_group += 1
                    # Best-effort: attach the real Home Depot product photo to
                    # the cost item, same way app.py's attach_files() already
                    # attaches job-level files (createUploadRequest -> createFile
                    # by URL). Never lets a failure here block the estimate —
                    # this is a nice-to-have, not confirmed with a live write
                    # test yet (see estimate_v2_draft.py module docstring).
                    catalog_match = line.get("catalog_match") or {}
                    image_url = catalog_match.get("imageUrl")
                    if image_url and org_id:
                        try:
                            created_id = resp["createCostItem"]["createdCostItem"]["id"]
                            _attach_catalog_image(jobtread_query_fn, org_id, created_id,
                                                   image_url, item[:100])
                        except Exception as e:
                            print(f"  Photo attach skipped for '{item[:50]}' (non-fatal): {e}")

        if items_added_this_group > 0:
            added_groups += 1
        else:
            print(f"  WARNING: group '{title[:50]}' created with zero cost items — check output schema")

    print(f"  {added_groups}/{len(cost_groups)} cost groups added (multi-line)")
    return added_groups


# ─────────────────────────────────────────────────────────────────────────
# call_claude_v2 — replaces call_claude() in app.py.
#
# WHY THIS CHANGES: Jason confirmed (Jul 2026) that what realtors actually
# send as the "repair addendum" is inconsistent and often visual, not the
# clean typed document today's app.py assumes (see comment at app.py:367,
# "it's reliably a clean [typed document]" — that assumption is wrong):
#   1. A clean typed PDF of the addendum document (the easy case)
#   2. A scanned paper copy that comes in as a PDF — effectively an image;
#      text extraction would be unreliable/garbage without OCR
#   3. No separate addendum at all — the realtor just sends the inspection
#      report (with its own photos), which doubles as the addendum
#   4. A marked-up inspection report (handwritten circles/notes on
#      inspection pages) used AS the addendum instead of a separate doc
#
# Cases 2 and 4 need vision to read at all; case 3 means the pipeline can't
# assume both files always exist and differ. So: BOTH documents now go to
# Claude as native PDF (vision) when present, exactly like the inspection
# report already does today — extract_pdf_text is no longer used for the
# addendum. When only one file is provided, the prompt tells Claude to
# treat that single document as carrying both the scope-of-work and the
# inspection findings, instead of assuming something is missing.
# ─────────────────────────────────────────────────────────────────────────

def _check_pdf_page_count(pdf_bytes, label, max_pages=100):
    """Raise ValueError if a PDF exceeds Claude's page cap. Best-effort —
    if the page count can't be determined (missing pypdf, corrupt header,
    etc.) this just logs and lets the API call itself be the real check.
    """
    try:
        from pypdf import PdfReader
        page_count = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        if page_count > max_pages:
            raise ValueError(
                f"{label} has {page_count} pages — Claude's PDF support caps "
                f"at {max_pages} pages per document. Split the file and try again."
            )
    except ValueError:
        raise
    except Exception as e:
        print(f"  Could not pre-check {label} page count: {e}")


def build_claude_document_content(addendum_pdf_bytes, inspection_pdf_bytes,
                                   client_name, client_phone, client_email,
                                   address, notes):
    """Build the `content` list for the Claude messages API call: intro text
    plus native PDF document blocks for whichever of (addendum, inspection)
    are actually present. Both are vision (native PDF), never text-extracted.
    """
    have_addendum = bool(addendum_pdf_bytes)
    have_inspection = bool(inspection_pdf_bytes)

    if have_addendum and have_inspection:
        doc_instructions = (
            "Two PDFs are provided below: the repair addendum first, then the "
            "inspection report. Process the repair addendum first to identify "
            "all requested items, then cross-reference with the inspection "
            "report to write accurate scope descriptions and calibrate pricing "
            "based on described severity. Read both documents' printed text "
            "AND their photos/handwritten circles, arrows, or markups — photos "
            "and markups often show the true extent of an issue that a "
            "one-line text finding doesn't capture."
        )
    elif have_inspection:
        doc_instructions = (
            "Only ONE document is provided below: the inspection report. No "
            "separate repair addendum was sent for this job — this is common "
            "(realtors often just send the inspection report itself, which "
            "carries both the findings AND the scope of what's being asked "
            "for). Treat this single document as authoritative for both what "
            "to fix and how to describe it. Read the printed text AND the "
            "photos/handwritten circles, arrows, or markups on the pages — "
            "sometimes this same document has been marked up by hand to "
            "indicate exactly which items are the actual ask."
        )
    elif have_addendum:
        doc_instructions = (
            "Only ONE document is provided below: the repair addendum. No "
            "separate inspection report was sent for this job. Read the "
            "printed text AND any photos/handwritten markups on the pages to "
            "determine scope and quantity."
        )
    else:
        doc_instructions = (
            "No documents were provided — this should not normally happen; "
            "flag needs_consult=true and explain in consult_reason."
        )

    content = []
    intro = f"""Generate a closing repairs estimate for Owners Choice Construction.

Client name: {client_name}
Client phone: {client_phone}
Client email: {client_email}
Property address: {address}
{f"Realtor notes: {notes}" if notes else ""}

{doc_instructions}
"""
    content.append({"type": "text", "text": intro})

    if have_addendum:
        _check_pdf_page_count(addendum_pdf_bytes, "Repair addendum")
        addendum_b64 = base64.b64encode(addendum_pdf_bytes).decode("utf-8")
        content.append({"type": "text", "text": "\n=== REPAIR ADDENDUM (PDF below — read text, photos, and handwritten annotations; may be a scan) ==="})
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": addendum_b64}
        })

    if have_inspection:
        _check_pdf_page_count(inspection_pdf_bytes, "Inspection report")
        inspection_b64 = base64.b64encode(inspection_pdf_bytes).decode("utf-8")
        content.append({"type": "text", "text": "\n=== INSPECTION REPORT (PDF below — read text, photos, and handwritten annotations) ==="})
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": inspection_b64}
        })

    content.append({"type": "text", "text": "\nRespond with ONLY the raw JSON object. No markdown, no explanation."})
    return content


def call_claude_v2(addendum_pdf_bytes, inspection_pdf_bytes, client_name,
                    client_phone, client_email, address, notes,
                    system_prompt, anthropic_api_key,
                    model="claude-sonnet-4-6", max_tokens=24000, timeout=400):
    """Replaces call_claude() in app.py. Same retry/parsing behavior, but:
      - addendum is now native PDF (vision), not extract_pdf_text
      - both documents are optional independently (see build_claude_document_content)

    system_prompt and anthropic_api_key are passed in rather than read from
    module globals, so this stays a pure, unit-testable function like
    add_cost_groups_v2 above (pass a fake urlopen/requests layer in tests).

    BUG FIX #1 (Jul 2026, 1st live submission): this used to inherit
    call_claude()'s timeout=120 and only retry on (json.JSONDecodeError,
    ValueError) — i.e. bad-JSON responses. A real submission with a large
    inspection report (1.9MB / many pages) hit a genuine TimeoutError,
    which isn't a JSONDecodeError/ValueError, so it skipped the retry loop
    entirely and failed on attempt 1 with no retry at all. Fixed: timeout
    raised 120 -> 300s (this call runs in a background thread — the
    webhook's HTTP response already returned before this runs — so a
    longer timeout costs nothing downstream), and the except clause
    broadened to also catch TimeoutError/OSError/HTTPException.

    BUG FIX #2 (Jul 2026, 2nd live submission, same real job resubmitted):
    with the timeout fixed, the SAME submission then failed differently —
    "Claude response truncated by max_tokens (attempt 1), got 27031 chars
    before cutoff", and again on attempt 2 (same failure, since retrying
    with the same max_tokens just truncates at the same point again — this
    failure mode is NOT transient, so the retry loop alone can't fix it).
    Root cause: the v2 schema asks for far more output per repair item
    (quantity_note, confidence, cost_code, multiple labor_lines and
    material_lines) than the old flat "price" field ever needed, and a
    real closing-repair inspection report can have many repair items — 8000
    max_tokens simply wasn't enough for a job with a lot of items. Checked
    Anthropic's docs: Claude Sonnet 4.5-class models support up to 64,000
    output tokens standard (no beta header needed), so 8000 had a lot of
    headroom to give. Raised max_tokens 8000 -> 24000 and timeout 300 ->
    400s (generation time scales with output length, so the timeout needed
    more room too) — not the max possible, but a big enough jump to clear
    a normal job's real item count with margin, without asking for enough
    tokens that a single truncated/hung generation eats an excessive amount
    of time before failing.
    """
    content = build_claude_document_content(
        addendum_pdf_bytes, inspection_pdf_bytes,
        client_name, client_phone, client_email, address, notes
    )

    base_payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": content}]
    }

    last_error = None
    for attempt in range(1, 4):
        try:
            payload = json.dumps(base_payload).encode("utf-8")
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "pdfs-2024-09-25"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                result = json.loads(r.read().decode("utf-8"))

            stop_reason = result.get("stop_reason")
            raw = "".join(block.get("text", "") for block in result.get("content", []))

            if stop_reason == "max_tokens":
                raise ValueError(
                    f"Claude response truncated by max_tokens (attempt {attempt}), "
                    f"got {len(raw)} chars before cutoff"
                )

            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                raise ValueError(f"No JSON object found in Claude response: {raw[:500]}")

            return json.loads(match.group(0))

        except (json.JSONDecodeError, ValueError, TimeoutError, OSError, http.client.HTTPException) as e:
            last_error = e
            print(f"  call_claude_v2 attempt {attempt} failed ({type(e).__name__}): {e}")
            if attempt < 3:
                print(f"  Retrying ({attempt + 1}/3)...")
                continue

    raise Exception(
        f"call_claude_v2 failed after 3 attempts. Last error: {last_error}. "
        f"This lead needs to be entered manually — check Render logs for the raw Claude output."
    )
