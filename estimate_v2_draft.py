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
    leaves the LLM's guess completely untouched (catalog_no_match flag only,
    no candidate list — simplified Jul 2026, see resolve_material_lines_
    with_catalog's docstring) when no match is confident, or (c) leaves the
    line completely unchanged if the search fails/returns nothing — a bad
    catalog lookup should never block an estimate. Deliberately NOT the
    discarded fuzzy-matching approach from
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
or missing that "wood rot" is actually a pest/termite issue). A real example
Jason caught (Round 2 pricing Q&A): an inspector wrote "sealing at windows"
but the actual photos clearly showed missing/cracked window GLAZING
compound around the panes — a real glazing repair, not a simple sealant
job, and priced/scoped differently (different material, different labor).
When a photo clearly shows something DIFFERENT from what the report's text
says — not just a different quantity, but a different actual problem,
cause, or severity — DO NOT just flag the discrepancy and move on. Actually
LOOK at the photo, determine the real problem it shows, and scope/estimate
THAT real problem (correct trade, correct materials, correct quantity) —
the photo-based read must always win over the inspector's words in what you
actually estimate. Separately, ALSO call out the discrepancy explicitly in
"quantity_note" so Jason's team can see exactly where and why the AI
overrode the report's text (e.g. "Report says 'sealing at windows' but
photos show missing/cracked glazing compound around multiple panes —
estimated as a window glazing repair, not a sealant job" or "Report says
'minor water staining' but photo shows an active supply-line leak —
estimated as a plumbing repair, not a cosmetic one"). If a photo is
unclear, low-resolution, or genuinely isn't provided for a given item, say
so plainly in "quantity_note" instead of inventing visual detail you can't
actually see — the goal is trustworthy photo-based judgment, not the
appearance of it.

STEP 1B — SOURCE PAGES (for reference photos): set "source_pages" to a list
of the inspection report's PDF page number(s) (1-indexed, counting pages of
THIS document only — not the repair addendum, if a separate one exists)
where a photo relevant to this repair appears. If this cost group bundles
several inspection findings together (e.g. one group covering several
electrical section numbers), include every page that has a relevant photo
for ANY of the bundled findings, not just the first one. If no inspection
report was provided, or you genuinely can't tell which page a photo is on,
return an empty list — never guess a page number. This is used to attach
the actual inspection photos to the cost group in JobTread, so accuracy
matters more than completeness; when unsure whether a page belongs, leave
it out rather than over-including.

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
    For paint, adhesive, or any other material sold in multiple container
    sizes, always name a SPECIFIC, SMALL, realistic size in "item" (e.g.
    "1 qt" or "1 gallon" touch-up paint, not just "exterior paint" with no
    size at all) — OCC's in-house crews never need 5-gallon quantities for
    closing-repair work (that scope always goes to a subcontractor instead),
    so an unspecified size risks matching a bulk contractor pail against the
    live Home Depot catalog. This applies just as much to COUNT-based
    packaging, not just volume — for caulk/sealant tubes, fasteners,
    batteries, or anything else sold as a single piece OR a multi-pack/case,
    always specify a SINGLE unit (e.g. "1 tube of exterior sealant," not
    "sealant" or "sealant, 12-pack") and set "qty" to the number of
    INDIVIDUAL units actually needed for the job (usually 1), never a pack
    or case size. A real bug this caused: an unspecified sealant line
    matched a 12-count case on the live catalog and got written to JobTread
    as qty 12 — i.e. 12 cases — when the job needed one tube.
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

  STRUCTURAL SHORING / ELLIS JACKS (confirmed IN-HOUSE, Jason's Round 2
  answer): when an inspection report calls out improper temporary
  foundation support in the crawlspace — e.g. screw jack posts (the
  adjustable steel posts sold at Home Depot), a dry-stacked CMU block
  stack, or any other improvised point shoring — this is IN-HOUSE work
  (OCC's crew replaces these directly; it is not part of the
  crawlspace-moisture-mitigation sub scope). Budget ~2 man-hours of labor
  for a SINGLE point-shore replacement (Jason's real number — scale up for
  multiple shore points in the same crawlspace). Every point-shore
  replacement needs THREE separate catalog items, not two: (1) an Ellis
  jack, (2) a 12x12 base plate, and (3) a double U-head bracket — search
  the Home Depot catalog for each by name ("Ellis jack," "base plate,"
  "double U head bracket"; OCC's own catalog lists the jack and base plate
  by model number, e.g. "STL 22"/"BASE12"-style codes). Size the jack to
  the actual span/height needed using visual clues from the inspection
  photos (crawlspace clearance height, joist depth, etc.) — Jason's
  guidance is to cross-reference Ellis Manufacturing's own published height
  ranges per model number when picking a size, so note in "quantity_note"
  which model/height range you selected and why (e.g. "~18in crawlspace
  clearance visible in photo -> sized to Ellis [model]'s stated range").
  Use the same Ellis-jack-plus-base-plate-plus-bracket approach for any drop
  girder repair or headered joist repair that needs a permanent support
  point, not just for replacing bad temporary shoring.

STEP 3B — IF SUB (electrical / major HVAC / major plumbing / crawlspace):
  do NOT itemize hours or materials — subs bill OCC scope-based or day-rate
  per visit, not itemized labor+materials, so an hours+materials breakdown
  here would be fake precision. Set both labor_lines and material_lines to
  empty arrays for sub work. Then price the sub scope one of two ways:

  - SINGLE-SCOPE sub work (one distinct task — e.g. one GFCI swap, one
    water heater TPRV extension): use a single "sub_scope_price" (OCC's
    cost from the sub, before markup), as before.
  - MULTI-SCOPE sub work (one sub visit covering several distinct scopes —
    most common with crawlspace moisture remediation, but applies to any
    trade): instead of one lump sum, break the visit into
    "sub_scope_lines": a list of {{"item": "...", "cost": N}} entries, one
    per distinct scope, each cost being OCC's cost from the sub for that
    scope before markup. Example — a crawlspace remediation visit should
    come through as separate lines like: fungal/mold treatment; crawlspace
    cleanout (always include a cleanout line whenever a new vapor barrier
    is being installed); vapor barrier install; dehumidifier supply +
    install; seal foundation vents; electrical circuit for the dehumidifier.
    This is still scope-based sub pricing (per-scope flat costs, NOT
    hours+materials) — it just shows the logic per scope instead of hiding
    it in one number, which is how OCC's own historical crawlspace budgets
    were written (see the real "Crawl Space and Moisture Control" example:
    clean-out / vapor barrier / dehumidifier / vent sealing as separate
    flat items). Use sub_scope_lines whenever the sub visit has 2+ distinct
    scopes; never use both sub_scope_price and sub_scope_lines in the same
    group.

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


def build_full_estimating_prompt(csv_path=None):
    """Assemble the piece of the system prompt that replaces the old flat
    price-lookup section: the estimating method/logic + real historical
    reference examples. This is what actually wires the historical data into
    the live prompt (Task #8) — everything above this point defines the
    pieces, this is where they get glued together.
    """
    examples_text = load_historical_reference_examples(csv_path)
    if examples_text:
        return ESTIMATING_LOGIC_SECTION + "\n\n" + examples_text
    return ESTIMATING_LOGIC_SECTION


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
      "source_pages": [4],
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
      "source_pages": [7, 8],
      "quantity_note": "3 exterior receptacles called out in addendum",
      "confidence": "high",
      "labor_lines": [],
      "material_lines": [],
      "sub_scope_price": 750.00,
      "notes": null
    },
    {
      "title": "9.2 - Crawlspace Moisture Remediation",
      "description": "- Treat fungal growth, clean out crawlspace, install new vapor barrier, install dehumidifier, and seal foundation vents...",
      "labor": "sub",
      "cost_code": "Crawlspace Work",
      "source_pages": [12],
      "quantity_note": "~1,800 sq ft crawlspace per report; visible fungal growth on joists in photos",
      "confidence": "medium",
      "labor_lines": [],
      "material_lines": [],
      "sub_scope_price": null,
      "sub_scope_lines": [
        {"item": "Fungal/mold treatment", "cost": 1200.00},
        {"item": "Crawlspace cleanout (required for new barrier)", "cost": 500.00},
        {"item": "Vapor barrier install (~1,800 sq ft)", "cost": 2070.00},
        {"item": "Dehumidifier supply and install", "cost": 1800.00},
        {"item": "Seal foundation vents", "cost": 350.00},
        {"item": "Electrical circuit for dehumidifier", "cost": 400.00}
      ],
      "notes": "multi-scope sub visit — itemized per scope (sub_scope_lines) instead of one lump sum, per STEP 3B"
    },
    {
      "title": "6.4 - Plumbing Leak and Resulting Drywall Damage",
      "description": "- Repair active supply line leak under kitchen sink (plumber).\\n- Patch and repaint water-damaged drywall below sink (in-house).",
      "labor": "mixed",
      "cost_code": "Plumbing",
      "source_pages": [9],
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


# The "true catalog link" mechanism (Jul 2026 investigation, read-only —
# see CLAUDE.md "Home Depot 'true catalog link' mechanism — SOLVED" entry
# for the full writeup). When Jason manually adds a Global Catalog item to a
# budget in the JobTread UI, the resulting costItem gets a real
# `sourceCostItem` relation pointing at a shared "master" costItem record
# that lives under a SEPARATE JobTread-owned organization (id
# "22NrfN7U8S9j", confirmed by reading a real linked item's own cached
# record) — every JobTread customer who imports the same product links back
# to the same shared master, rather than each org getting a private copy.
# Items our pipeline creates via plain createCostItem never get this
# relation set, which is exactly why they never show up on a real Home
# Depot order even though the price/name/SKU look identical.
#
# The recipe (two calls, not one):
#   1. createGlobalOrganizationCostItem({type, storeId, productId}) — get
#      back the shared master costItem id (idempotent: reuses the existing
#      master if any JobTread customer already imported that product).
#   2. Normal createCostItem, with `sourceCostItem: {id: <master id>}` added
#      to the args on top of everything already written (cost/price/
#      description/customFieldValues) — this is the actual link.
#
# Call #1's mutation shape (type/storeId/productId args, costGroup comes
# back null since it's a shared record not attached to any one job) was
# confirmed live in an earlier session. The "type" value was a guess
# ("HomeDepotProduct") until a real production run (Jul 2026, job
# 22PbQbX4bfWm) surfaced the actual API validation error, which names every
# valid option: 'The value {...} at "createGlobalOrganizationCostItem"."$"
# does not resolve to "heritage", "homeDepot", "poolcorp", "qxo", or "srs"'.
# So JobTread supports Global Catalog linking for (at least) 5 vendor
# integrations, and the real value for Home Depot is the camelCase
# "homeDepot" (matching the homeDepotProducts query field's own casing) —
# not the guessed "HomeDepotProduct". Fixed below. If this ever needs to
# support another one of these vendors, "type" is the field to change.
GLOBAL_CATALOG_PRODUCT_TYPE = "homeDepot"

# REAL FIX FOUND (Jul 2026) — re-enabled. createCostItem really does reject
# a nested `sourceCostItem: {id: "..."}` relation object ('no value is ever
# expected there', confirmed on a real production run, job 22PbQqvNG268).
# But that's not because the relation can't be set at all — it's because
# the wrong SHAPE was used. Found by fetching JobTread's own loaded frontend
# JS bundle straight out of the browser (no login/permission needed beyond
# what's already loaded on the budget page) and grepping it for
# "sourceCostItem": the app's own src/functions/get-line-item-args.js —
# the exact function JobTread's UI calls to build its own createCostItem
# args — flattens the relation to a plain scalar before sending:
# `sourceCostItemId: j && j.id` (same flattening pattern used for
# organizationCostItemId/jobCostItemId). So the real arg is `sourceCostItemId`
# (a flat id string), not a nested `sourceCostItem` object. Re-enabled with
# the corrected arg name — see the material-line loop in add_cost_groups_v2,
# which now also keeps this in its OWN retry tier (link_args) separate from
# description/customFieldValues (enriched_args), so a bad link attempt can
# never again wipe out the real product detail the way the old nested-object
# attempt did. NOT yet confirmed against a real live job — needs one more
# real submission to know for sure this is right.
ATTEMPT_TRUE_CATALOG_LINK = True


def link_catalog_master_item(jobtread_query_fn, org_id, product_id, store_id=None):
    """Get (or idempotently create) the shared "master" costItem record for
    a real Home Depot product, so a material line's createCostItem call can
    set `sourceCostItem` and become a TRULY linked catalog item (shows up on
    a real Home Depot order in JobTread, same as a manually-added item) —
    not just a costItem with the same price/name/SKU.

    Returns the master costItem id, or None on any failure. Never raises —
    org_id/product_id are required by the caller before this is even
    attempted; any API-level failure (wrong "type" value, permission issue,
    network problem) just means the line doesn't get truly linked this time,
    it still gets created with its real cost/price/description regardless.
    """
    try:
        resp = jobtread_query_fn({
            "createGlobalOrganizationCostItem": {
                "$": {
                    "type": GLOBAL_CATALOG_PRODUCT_TYPE,
                    "storeId": store_id or HOME_DEPOT_STORE_ID,
                    "productId": product_id,
                },
                "createdCostItem": {"id": {}}
            }
        })
        return resp["createGlobalOrganizationCostItem"]["createdCostItem"]["id"]
    except Exception as e:
        print(f"  Global catalog master-item link failed for product "
              f"{product_id}: {e}")
        return None


# Unit synonyms collapsed to one normalized form, used by _extract_size_tokens
# / _size_mismatch below (Jul 2026 matching-quality tightening — see
# resolve_material_lines_with_catalog docstring for why this exists: the
# 12 Tall Tree Lane job showed a real case of an exterior primer ordered as
# "1 qt" auto-matching to a real but wrong-size "5 gal" bucket — same product
# line, wrong container, and the old word-overlap score had no way to catch
# that since none of the overlapping words were about size at all).
_SIZE_UNIT_ALIASES = {
    "gallon": "gal", "gallons": "gal", "gal": "gal",
    "quart": "qt", "quarts": "qt", "qt": "qt",
    "ounce": "oz", "ounces": "oz", "oz": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb", "lb": "lb",
    "foot": "ft", "feet": "ft", "ft": "ft",
    "inch": "in", "inches": "in", "in": "in",
    # Count/pack-size units (added Jul 2026 — real bug: a 12-count case of
    # sealant tubes auto-matched against a single-tube request with no size
    # guard at all, since "ct"/"pack"/"case" weren't recognized as sizes,
    # only volume/weight/length were. See _is_bulk_without_size_request.
    "ct": "ct", "count": "ct", "counts": "ct",
    "pack": "ct", "packs": "ct", "pk": "ct",
}


def _extract_size_tokens(text):
    """Pull (value, normalized_unit) size tokens like (5.0, 'gal'),
    (1.0, 'qt'), or (12.0, 'ct') out of a product description/name.
    Best-effort regex, not a real unit parser — only needs to catch the
    common "N unit" pattern (plus "case/box of N" for count-based
    packaging) well enough to flag an obvious size conflict, not parse
    every possible container spec.
    """
    tokens = []
    text = text.lower()
    for value, unit in re.findall(
        r"(\d+(?:\.\d+)?)\s*[-]?\s*"
        r"(gallons?|gal|quarts?|qt|ounces?|oz|pounds?|lbs?|feet|foot|ft|inches?|in|"
        r"ct|counts?|packs?|pk)\b",
        text
    ):
        norm_unit = _SIZE_UNIT_ALIASES.get(unit, unit)
        try:
            tokens.append((float(value), norm_unit))
        except ValueError:
            continue
    # "case of 12" / "box of 12" — the count comes AFTER the word here, not
    # in the "N unit" shape the main regex above expects.
    for value in re.findall(r"(?:case|box)\s+of\s+(\d+(?:\.\d+)?)", text):
        try:
            tokens.append((float(value), "ct"))
        except ValueError:
            continue
    return tokens


# Base-unit conversions so a size check can compare across units within the
# same family (e.g. "1 qt" vs "5 gal" is the same family — volume — at very
# different quantities, not just a same-unit mismatch). Plain "oz" is
# genuinely ambiguous (fluid vs. weight), so it's included in BOTH families;
# a comparison only fires when both sides share at least one common family.
_VOLUME_OZ = {"gal": 128.0, "qt": 32.0, "oz": 1.0}
_WEIGHT_OZ = {"lb": 16.0, "oz": 1.0}
_LENGTH_IN = {"ft": 12.0, "in": 1.0}
_COUNT_EACH = {"ct": 1.0}  # count/pack/case — already in "each" units, no conversion


def _size_families(value, unit):
    """Return {family_name: value_converted_to_that_family's_base_unit} for
    whichever size families this unit participates in."""
    fams = {}
    if unit in _VOLUME_OZ:
        fams["volume"] = value * _VOLUME_OZ[unit]
    if unit in _WEIGHT_OZ:
        fams["weight"] = value * _WEIGHT_OZ[unit]
    if unit in _LENGTH_IN:
        fams["length"] = value * _LENGTH_IN[unit]
    if unit in _COUNT_EACH:
        fams["count"] = value * _COUNT_EACH[unit]
    return fams


def _size_mismatch(query, product_name):
    """True if both strings mention a size in the same family (volume,
    weight, or length) but NONE of the values in that family are within
    1.5x of each other after converting to a common base unit — a strong
    signal that a word-overlap match found the right product line but the
    wrong container size (e.g. "1 qt" primer vs. a real "5 gal" bucket of
    the same primer — different units entirely, but both volume, so a
    same-unit-only check would miss this). Only flags a real disagreement;
    doesn't penalize lines that just don't happen to mention a comparable
    size on both sides.

    Checks the BEST (closest) pair per family, not just any pair — real bug
    found reviewing job 765 Hannon Road (Jul 2026): a multi-dimension
    lumber product ("5/4 in. x 6 in. x 12 ft...") has THREE length-family
    numbers (4, 6, 144 in base inches), one of which (144) exactly matches
    the query's "12 ft" (also 144). The old any-pair check compared the
    query's 12 ft against the board's 4 in. cross-section width first,
    found a 36x "mismatch", and returned True before ever checking the
    144-vs-144 pair that actually matches — wrongly capping the score of
    the exact right product. A real product legitimately has multiple
    numbers in the same family (thickness, width, length are all
    "length"); only flag a mismatch if NO pairing in that family is
    plausible, not merely because some pairing isn't.
    """
    q_sizes = _extract_size_tokens(query)
    n_sizes = _extract_size_tokens(product_name)
    q_by_fam, n_by_fam = {}, {}
    for v, u in q_sizes:
        for fam, base in _size_families(v, u).items():
            q_by_fam.setdefault(fam, []).append(base)
    for v, u in n_sizes:
        for fam, base in _size_families(v, u).items():
            n_by_fam.setdefault(fam, []).append(base)

    for fam, q_bases in q_by_fam.items():
        n_bases = n_by_fam.get(fam)
        if not n_bases:
            continue
        best_ratio = min(
            max(qb, nb) / min(qb, nb)
            for qb in q_bases for nb in n_bases
            if qb > 0 and nb > 0
        )
        if best_ratio >= 1.5:
            return True
    return False


# Filler/category adjectives that Home Depot's own search sometimes weighs
# too heavily against a specific dimension, diluting an otherwise exact
# match. Real evidence (Jul 2026, job 765 Hannon Road, found while
# investigating why "Window glazing compound/putty" and "5/4x6
# pressure-treated deck board, 12 ft" both auto-matched poorly): replaying
# the exact literal item text through JobTread's own Global Catalog search
# UI showed the full text "5/4x6 pressure-treated deck board, 12 ft" ranked
# a *wrong-length* 8 ft board first and buried an unrelated "Pressure-
# Treated Pine Stair Stringer" (the actual candidate our pipeline surfaced
# to Jason) ahead of the real product. Dropping just "pressure-treated" --
# "5/4x6 deck board 12 ft" -- returned the EXACT right product ("5/4 in. x
# 6 in. x 12 ft. ... Pressure-Treated Lumber") as the #1 result, even though
# the word "pressure-treated" never appeared in the query at all (Home
# Depot's own catalog already knows this decking board is pressure-treated).
# Only ever tried as an ADDITIONAL fallback query variant (see
# _generate_search_query_variants) -- the original full-text query is
# always tried too, and scoring is always done against the real full item
# description, so this can only ever help recall, never silently swap in an
# untreated product for a treated one.
_QUERY_FILLER_PHRASES = [
    "pressure-treated", "pressure treated", "ground contact",
    "kiln-dried", "kiln dried",
]

# Matches a slash joining two WORDS (e.g. "compound/putty"), not a dimension
# fraction like "5/4" or "3/4" -- the digit case is deliberately excluded so
# "5/4x6" is never split.
_WORD_SLASH_RE = re.compile(r"\b([A-Za-z]+)/([A-Za-z]+)\b")


def _generate_search_query_variants(item_desc):
    """Build a small, ordered list of search-query strings to try against
    the live Home Depot catalog for one material line, instead of firing
    only the LLM's raw item text at the API verbatim.

    Real evidence this matters (Jul 2026, job 765 Hannon Road): the literal
    text Claude wrote for two real material lines -- "Window glazing
    compound/putty" and "5/4x6 pressure-treated deck board, 12 ft" -- both
    returned weak/irrelevant top candidates from the real Home Depot search
    API, even though the correct product clearly exists in the catalog and
    ranks #1 or #2 for a shorter/cleaner version of the same query
    (confirmed by replaying both searches directly through JobTread's own
    Global Catalog search UI). Two distinct problems, two variant strategies:

    1. Slash-joined word alternatives ("compound/putty") dilute relevance by
       asking for two different nouns at once -- searching "window glazing
       compound" alone or "window glazing putty" alone each independently
       found the real product; searching both together (slash OR space
       separated) did not. Fix: split a slash that joins two *words* and try
       each alternative as its own full query.
    2. Generic treatment/category adjectives ("pressure-treated") can bury
       an exact dimension match under a flood of other same-adjective
       products. Fix: also try a version with a short, curated list of these
       filler phrases stripped out, as a fallback only.

    Returns a de-duplicated, ordered list of query strings (sanitized
    original text always first).
    """
    base = re.sub(r"\s+", " ", (item_desc or "").replace(",", " ")).strip()
    variants = []

    def add(v):
        v = re.sub(r"\s+", " ", v).strip()
        if v and v not in variants:
            variants.append(v)

    add(base)

    m = _WORD_SLASH_RE.search(base)
    if m:
        for alt in (m.group(1), m.group(2)):
            add(base[:m.start()] + alt + base[m.end():])

    stripped = base
    for phrase in _QUERY_FILLER_PHRASES:
        stripped = re.sub(re.escape(phrase), "", stripped, flags=re.IGNORECASE)
    add(stripped)

    return variants


# Collapses "N in. x M in." cross-section notation (how Home Depot writes
# lumber dimensions) down to "NxM" (how Claude usually writes it, e.g.
# "5/4x6") so the two forms actually overlap as words once tokenized.
# Real bug found reviewing job 765 Hannon Road (Jul 2026): the query
# "5/4x6 pressure-treated deck board, 12 ft" scored only 0.4 against the
# exact right product, "5/4 in. x 6 in. x 12 ft. ... Pressure-Treated
# Lumber" -- well below the 0.5 auto-apply threshold -- because "4x6" (one
# token in the query) never matched "4", "in", "x", "6" (four separate
# tokens in the product name) under plain word-overlap scoring, even though
# they describe the identical dimension.
#
# The unit after the SECOND number is required (not optional) so this can
# only collapse a genuine "W in. x H in." cross-section pair -- e.g. it
# must NOT also swallow "6 in. x 12 ft." (a cross-section width followed by
# an unrelated LENGTH in feet) into a bogus "6x12".
_DIMENSION_RE = re.compile(
    r"(\d+(?:/\d+)?(?:\.\d+)?)\s*(?:in\.?|inch(?:es)?)?\s*x\s*"
    r"(\d+(?:/\d+)?(?:\.\d+)?)\s*(?:in\.?|inch(?:es)?)",
    re.IGNORECASE,
)


def _normalize_dimensions(text):
    return _DIMENSION_RE.sub(r"\1x\2", text)


# Jason's stated real business rule (Jul 2026, reviewing a real job): "We
# will likely never need 5 gallons of anything for in house work. If its
# that big, we get subcontractor to handle paint or drywall work." Real bug
# this catches: when Claude's own item text doesn't specify a size at all
# (e.g. just "exterior paint" with no "1 qt"/"1 gal"), _size_mismatch()
# never fires -- it only compares sizes when BOTH sides name one -- so a
# 5-gallon contractor pail can win purely on word overlap and get
# auto-applied with no sanity check on quantity at all. This is a separate,
# narrower guard: it only fires when the QUERY has no size opinion of its
# own, so it can never override a line where Claude (or the addendum)
# explicitly asked for a specific size -- that case is still handled by
# _size_mismatch.
_BULK_VOLUME_OZ_THRESHOLD = 5 * 128.0  # 5 gallons, in fluid oz

# Real bug found reviewing a job (Jul 2026): a "12 ct" case of sealant tubes
# auto-matched and got written to JobTread as qty 12 of the CASE'S unit cost
# — i.e. 12 cases, when a single tube (or at most a single case, qty 1) was
# what the job needed. Same root cause as the 5-gallon paint bug above:
# Claude's own item text named no pack size at all (e.g. just "exterior
# sealant"), so nothing constrained the match, and a multi-count
# case/pack/box product won purely on word overlap with zero sanity check.
# Threshold set at 6+ units — a 2-3 pack is a normal small retail purchase
# in-house crews might reasonably grab, but a 6+ count case is a contractor-
# bulk purchase OCC's closing-repair crews don't need (mirrors the same
# "if it's that big, it's not an in-house quantity" reasoning as the 5-gal
# paint rule).
_BULK_COUNT_THRESHOLD = 6


def _is_bulk_without_size_request(query, product_name):
    if _extract_size_tokens(query):
        return False
    for v, u in _extract_size_tokens(product_name):
        fams = _size_families(v, u)
        if fams.get("volume", 0) >= _BULK_VOLUME_OZ_THRESHOLD:
            return True
        if fams.get("count", 0) >= _BULK_COUNT_THRESHOLD:
            return True
    return False


def _match_score(query, product_name):
    """Cheap word-overlap confidence score between a material description
    and a candidate Home Depot product name. Not fuzzy/edit-distance
    matching (that approach already failed once on this project — see the
    pricing_library.csv fuzzy-matching caveat in CLAUDE.md — the difference
    here is this is a confidence gate on top of a REAL catalog search result,
    not a blind text-similarity match against an unrelated taxonomy, so a
    wrong guess just falls back to the LLM's cost instead of a wrong price).

    TIGHTENED (Jul 2026, after reviewing a real job's catalog matches):
    (1) widened the stopword list to also drop bare unit words (gal, qt, oz,
    lb, ft, in, etc.) so two products that both happen to be sold "per
    gallon" don't get credit for that as if it were a real word match; (2)
    added a size-mismatch penalty — if the query and candidate both name a
    size in the same unit but at very different quantities, cap the score
    well below any reasonable auto-apply threshold so a wrong-size product
    never gets auto-substituted, only ever offered (or not) as a candidate;
    (3) normalize "N in. x M in." dimension notation to "NxM" before
    tokenizing (see _normalize_dimensions) so Home Depot's verbose
    cross-section phrasing lines up with how Claude usually writes it; (4)
    stopped dropping 2-digit pure-number tokens (e.g. "12" as in "12 ft.")
    -- these are exactly the kind of size/length detail that distinguishes
    one real product from another and were being silently discarded by the
    old length>2 filter, which was tuned for word noise, not numbers; (5)
    added a bulk-container penalty (Jul 2026, Jason's real feedback: a real
    job auto-matched a 5-gallon paint pail for what should have been a
    small touch-up quantity) — when the query names no size at all, a
    candidate implying 5+ gallons gets capped the same way a size mismatch
    does, since OCC's in-house crews never buy that volume (that scope goes
    to a subcontractor instead); (6) same bulk-container penalty extended to
    COUNT-based packaging (Jul 2026, Jason's real feedback: a real job
    auto-matched a 12-count case of sealant tubes and wrote it to JobTread
    as qty 12 — i.e. 12 cases — when a single tube was needed) — when the
    query names no pack size at all, a candidate implying a 6+ count
    case/pack/box gets capped the same way, since that's a contractor-bulk
    quantity, not a normal in-house repair purchase.
    """
    stop = {"a", "an", "the", "of", "for", "with", "in", "to", "and", "or",
            "1", "1x", "each", "per", "or", "similar", "equiv", "equivalent",
            "standard", "approx", "gal", "gallon", "gallons", "qt", "quart",
            "quarts", "oz", "ounce", "ounces", "lb", "lbs", "pound", "pounds",
            "ft", "feet", "foot", "inch", "inches",
            "ct", "count", "counts", "pack", "packs", "pk", "case", "box"}

    def keep(w):
        if w in stop:
            return False
        if len(w) > 2:
            return True
        return w.isdigit() and len(w) == 2

    query_n = _normalize_dimensions(query)
    product_n = _normalize_dimensions(product_name)
    q_words = {w for w in re.findall(r"[a-z0-9]+", query_n.lower()) if keep(w)}
    n_words = {w for w in re.findall(r"[a-z0-9]+", product_n.lower()) if keep(w)}
    if not q_words:
        return 0.0
    overlap = q_words & n_words
    score = len(overlap) / len(q_words)
    if _size_mismatch(query, product_name) or _is_bulk_without_size_request(query, product_name):
        score = min(score, 0.3)
    return score


def resolve_material_lines_with_catalog(estimate, jobtread_query_fn, org_id,
                                         auto_apply_threshold=0.5, top_n=3,
                                         min_candidate_score=0.2):
    """Resolve each material_line's LLM-guessed cost against the live Home
    Depot Global Catalog (search_home_depot_catalog), so the estimate has
    real live-priced products where a confident match exists instead of a
    guessed cost everywhere.

    For each material line:
      - Search the catalog (under several query variants — see
        _generate_search_query_variants) using the line's "item" text.
      - Score candidates by word overlap with the real item description
        (see _match_score).
      - If the best match scores >= auto_apply_threshold: REPLACE unit_cost
        with the real catalog unitCost, and attach "catalog_match" (name,
        sku, link, etc.) so add_cost_groups_v2() can write real custom
        fields (SKU/Product Link/Brand/Model Number) onto the JobTread cost
        item and attempt a true catalog link.
      - Otherwise (weak match, no match, or the search itself failed):
        leave the line's unit_cost exactly as Claude guessed it, and just
        set "catalog_no_match" so callers can see this was AI-estimated —
        no candidate list attached. SIMPLIFIED Jul 2026 (Jason's direct
        feedback reviewing a real job): this used to show up to 3 "possible
        matches" for a weak-but-plausible result, which he found more
        clutter than help. Now there's exactly two outcomes — confidently
        resolved, or left as the AI's own estimate — never a list to sort
        through. top_n/min_candidate_score are kept as accepted params
        (now unused) so existing call sites don't need to change.
      - Never raises or blocks an estimate — a catalog lookup having a bad
        day just means that one line stays as Claude estimated it.

    Mutates and returns `estimate` in place. Returns (estimate, stats) where
    stats = {"searched": N, "auto_matched": N, "no_match": N}.
    """
    stats = {"searched": 0, "auto_matched": 0, "no_match": 0}

    for group in estimate.get("cost_groups", []) or []:
        for line in group.get("material_lines", []) or []:
            item_desc = (line.get("item", "") or "").strip()
            if not item_desc:
                continue

            stats["searched"] += 1

            # Search under several query variants (raw text, slash-split
            # alternatives, filler-phrase-stripped fallback — see
            # _generate_search_query_variants) rather than just the raw item
            # text verbatim. Real evidence (job 765 Hannon Road, Jul 2026):
            # the raw text alone returned weak/irrelevant top candidates for
            # two real material lines even though the correct product was
            # readily found by a cleaner variant of the same query. Every
            # candidate found under ANY variant is still scored against the
            # real, full item description (never the shortened query text),
            # so this only ever widens recall — it can't cause a worse match
            # to be picked over a better one that the raw query already found.
            query_variants = _generate_search_query_variants(item_desc)
            products_by_id = {}
            any_ok = False
            for q in query_variants:
                try:
                    ok, products = search_home_depot_catalog(q, jobtread_query_fn, org_id)
                except Exception as e:
                    print(f"  Catalog search failed for '{q[:60]}': {e}")
                    ok, products = False, []
                if ok:
                    any_ok = True
                for p in products or []:
                    pid = p.get("id")
                    key = pid if pid else id(p)
                    if key not in products_by_id:
                        products_by_id[key] = (p, q)

            if not any_ok or not products_by_id:
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
                (
                    (_match_score(item_desc, p.get("name", "")), p, q)
                    for p, q in products_by_id.values()
                ),
                key=lambda t: t[0], reverse=True
            )
            best_score, best_product, best_query = scored[0]

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
                    # Home Depot's own product id -- needed to request the
                    # shared "master" catalog costItem via
                    # link_catalog_master_item() so the item we create can be
                    # truly linked (sourceCostItem) instead of just having
                    # the right price/name. See CLAUDE.md "Home Depot true
                    # catalog link mechanism" entry (Jul 2026).
                    "product_id": best_product.get("id"),
                }
                # Only noted when the winning result came from a fallback
                # variant rather than the raw item text, so a reviewer can
                # see why the matched product name doesn't share every word
                # with the line item (see _generate_search_query_variants).
                if best_query != re.sub(r"\s+", " ", item_desc.replace(",", " ")).strip():
                    line["catalog_match"]["matched_via_query"] = best_query
                stats["auto_matched"] += 1
            else:
                # SIMPLIFIED (Jul 2026, Jason's direct feedback): this used
                # to show a "possible matches" list of up to 3 weak
                # candidates when nothing cleared the auto-apply threshold.
                # Jason found that list more clutter than help ("a lot of
                # info thats hard to sort through") and would rather the
                # estimate just carry the AI's own guessed cost when the
                # catalog can't confidently resolve an item, with no extra
                # review burden. So there's no more middle tier — anything
                # that doesn't clear auto_apply_threshold is treated the
                # same simple way, and top_n/min_candidate_score are no
                # longer used for a shown list (kept as no-op params so
                # existing callers don't break).
                line["catalog_no_match"] = True
                stats["no_match"] += 1

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
        # Itemized sub scope lines take precedence over the lump-sum
        # sub_scope_price when both exist (mirrors add_cost_groups_v2 —
        # counting both would double-charge the same visit).
        sub_lines = group.get("sub_scope_lines") or []
        if sub_lines:
            for line in sub_lines:
                line_cost = float(line.get("cost", 0) or 0)
                if line_cost > 0:
                    total += round(line_cost * SUB_MARKUP, 2)
        else:
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
    """Intentionally returns "" always — REMOVED (Jul 2026, Jason's direct
    feedback): this used to write a free-text description onto every
    material cost item (the real product name for a confident match, a
    "possible matches" list for a weak one, or a no-match warning). Jason
    found that text more clutter than help ("Im really struggling with the
    amount of item description to look at... lets not bring over the item
    descriptions at all"). The item's own Name field already shows what it
    is, and a confident catalog match still gets its real SKU/Product
    Link/Brand/Model Number written into proper custom fields (see
    _build_material_custom_field_values) — this function (and the
    "description" arg it used to produce) is kept only so add_cost_groups_v2
    doesn't need a structural change if a short description is ever wanted
    again later.
    """
    return ""


# DEFINITIVELY CONFIRMED UNSUPPORTED (Jul 2026, real production run, job
# 22PbQbX4bfWm): createFile's own validation error names the full valid
# enum for "targetType" — "dailyLog", "document", "task", "job", "location",
# "contact", "account", "organization". "costItem" is not in that list and
# never will resolve, no matter how the args are shaped. This isn't a bug
# in our call, it's a real gap in what createFile can target. Set to False
# so every material line stops making a guaranteed-fail API call and
# cluttering the log — flip back to True only if JobTread ever adds
# costItem as a valid createFile target.
ATTEMPT_CATALOG_PHOTO_ATTACH = False


def _attach_catalog_image(jobtread_query_fn, org_id, cost_item_id, image_url, name):
    """Best-effort: attach a Home Depot product photo to a cost item, using
    the same createUploadRequest -> createFile(targetType, targetId, url)
    pattern app.py's attach_files() already uses successfully at the job
    level.

    CONFIRMED UNSUPPORTED (Jul 2026) — see ATTEMPT_CATALOG_PHOTO_ATTACH
    above. Left in place (dead code path, gated off by default) rather than
    deleted, in case createFile ever adds costItem support. Split into two
    try/excepts below so if it's ever re-enabled, a failure log pinpoints
    which call (createUploadRequest vs. createFile) is the problem instead
    of one generic wrapped message.
    """
    try:
        upload_resp = jobtread_query_fn({
            "createUploadRequest": {
                "$": {"organizationId": org_id, "url": image_url},
                "createdUploadRequest": {"id": {}}
            }
        })
        upload_id = upload_resp["createUploadRequest"]["createdUploadRequest"]["id"]
    except Exception as e:
        raise Exception(f"createUploadRequest failed: {e}")

    try:
        jobtread_query_fn({
            "createFile": {
                "$": {"targetType": "costItem", "targetId": cost_item_id,
                      "name": name, "uploadRequestId": upload_id},
                "createdFile": {"id": {}}
            }
        })
    except Exception as e:
        raise Exception(f"createFile (targetType=costItem) failed: {e}")


def build_estimate_snapshot_csv(estimate):
    """Render the AI-generated estimate as a CSV snapshot — the pre-edit
    baseline for the estimate feedback loop (Jason's request, Jul 2026).

    Written to the job's Files as "Original AI Estimate.csv" immediately
    after add_cost_groups_v2() succeeds, BEFORE any human edits, so a later
    diff sweep can compare what the AI originally produced against what the
    budget looks like after Jason's team reviewed/corrected it. Mirrors the
    exact same pricing math add_cost_groups_v2() uses when writing the real
    cost items (labor: qty=hours, cost=$55, price=billed rate; materials:
    cost*1.65; sub: cost*1.45), so the snapshot rows are directly comparable
    to the budget rows JobTread shows.

    One row per cost item plus one row per skipped item. Returns the CSV as
    a string.
    """
    import csv as _csv
    import io as _io

    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["group", "cost_code", "labor_type", "line_type", "item",
                "quantity", "unit_cost", "unit_price", "extended_price",
                "catalog_sku", "catalog_product", "confidence", "quantity_note"])

    for group in (estimate or {}).get("cost_groups", []) or []:
        title = (group.get("title", "") or "").strip() or "Repair Item"
        cost_code = (group.get("cost_code", "") or "").strip()
        labor_tag = (group.get("labor", "") or "").strip()
        confidence = (group.get("confidence", "") or "").strip()
        quantity_note = (group.get("quantity_note", "") or "").strip()

        sub_lines = group.get("sub_scope_lines") or []
        if sub_lines:
            for line in sub_lines:
                line_cost = float(line.get("cost", 0) or 0)
                if line_cost <= 0:
                    continue
                line_item = (line.get("item", "") or "").strip() or "Subcontractor scope"
                line_price = round(line_cost * SUB_MARKUP, 2)
                w.writerow([title, cost_code, labor_tag, "sub", line_item, 1,
                            line_cost, line_price, line_price, "", "",
                            confidence, quantity_note])
        else:
            sub_cost = float(group.get("sub_scope_price", 0) or 0)
            if sub_cost > 0:
                sub_price = round(sub_cost * SUB_MARKUP, 2)
                w.writerow([title, cost_code, labor_tag, "sub",
                            "Subcontractor scope", 1, sub_cost, sub_price,
                            sub_price, "", "", confidence, quantity_note])

        for line in group.get("labor_lines", []) or []:
            hours = float(line.get("hours", 0) or 0)
            rate = float(line.get("rate", 89.00) or 89.00)
            if hours <= 0:
                continue
            trade = (line.get("trade", "") or "Labor").strip()
            w.writerow([title, cost_code, labor_tag, "labor",
                        f"{trade.capitalize()} labor", hours, LABOR_COST_RATE,
                        rate, round(hours * rate, 2), "", "",
                        confidence, quantity_note])

        for line in group.get("material_lines", []) or []:
            qty = float(line.get("qty", 0) or 0)
            unit_cost = float(line.get("unit_cost", 0) or 0)
            if qty <= 0 or unit_cost <= 0:
                continue
            item = (line.get("item", "") or "Material").strip()
            unit_price = round(unit_cost * MATERIAL_MARKUP, 2)
            catalog_match = line.get("catalog_match") or {}
            w.writerow([title, cost_code, labor_tag, "material", item, qty,
                        unit_cost, unit_price, round(qty * unit_price, 2),
                        catalog_match.get("sku", ""),
                        catalog_match.get("name", ""),
                        confidence, quantity_note])

    for raw in (estimate or {}).get("skipped_items", []) or []:
        raw = (raw or "").strip()
        if raw:
            w.writerow(["Not Included In This Estimate", "", "", "skipped",
                        raw, "", "", "", "", "", "", "", ""])

    return buf.getvalue()


def _build_skipped_items_note(skipped_items):
    """Turn Claude's raw "skipped_items" list (each entry written as
    "item - reason", meant for internal reasoning — e.g. "Radon testing -
    not offered by OCC" or "Fireplace inspection - out of scope") into a
    short, neutral, professional client-facing note for the bottom of the
    estimate.

    Jason's direct request (Jul 2026): "Anything we cannot quote we can
    have at the bottom with a note that we are unable to include
    (professional comment)." Before this, skipped_items was only ever
    present in the raw JSON Claude returned — never actually written into
    JobTread anywhere, so Jason's team had no visibility into what got
    left out of an estimate or why.

    Deliberately drops the internal "reason" half of each entry (phrases
    like "not offered by OCC" read as internal/technical, not something to
    show a client) and keeps just the item name, in a single neutral
    sentence framing rather than Claude's per-item reasoning.
    """
    cleaned = []
    for raw in skipped_items or []:
        raw = (raw or "").strip()
        if not raw:
            continue
        item_name = raw.split(" - ")[0].strip() if " - " in raw else raw
        if item_name:
            cleaned.append(item_name)
    if not cleaned:
        return ""
    lines = ["We are unable to include pricing for the following item(s) in this estimate:"]
    lines.extend(f"- {name}" for name in cleaned)
    return "\n".join(lines)


def _strip_section_prefix(title):
    """Strip inspection-report section number prefix(es) off a group title
    to make a clean cost item name — handles MULTI-section prefixes too
    (real bug found in job 12 Tall Tree Ln's final budget, Jul 2026: the
    old regex `^[\\d\\.\\s]+[-–]?\\s*` only stripped the FIRST number of
    "9.3.1 / 9.4.1 / ... / 9.9.1 - Electrical Repairs", leaving a garbage
    item name starting "/ 9.4.1 / ...").

    The dash must be preceded by whitespace so a title that legitimately
    starts with a measurement (e.g. "1/2-in. hose bib") isn't eaten — only
    "sections - Title" patterns are stripped.
    """
    title = (title or "").strip()
    # Section prefixes can be joined by "/", "&", or "and" (real examples:
    # "9.3.1 / 9.4.1 - ...", "7.2.1 & 7.2.2 - ...") — all must be consumed.
    cleaned = re.sub(r'^[\d\./&\s]+(?:and\s+[\d\.\s]+)*\s[-–]\s*', '', title).strip()
    if cleaned == title:
        # No "sections - " pattern found; fall back to stripping a leading
        # dotted section number for titles like "2.3.1 Vent Boots" (no
        # dash). Requires at least one dot-number (\d+.\d+) so a real
        # measurement like "1/2-in. hose bib" is never eaten.
        cleaned = re.sub(r'^\d+(?:\.\d+)+\s*[-–]?\s*', '', title).strip()
    return cleaned or title


def _build_group_internal_notes(group):
    """Collect a group's internal reasoning (quantity assumption,
    non-high confidence flag, notes) into one text block, or "" if the
    group has nothing to flag.

    Written as a $0 "Internal Notes" line item INSIDE the cost group (see
    the call in add_cost_groups_v2) rather than on the group description —
    group descriptions print on client documents, but Jason confirmed
    (Jul 2026) his client-facing estimate template shows only cost group
    names/descriptions, never the line items below them, so a notes line
    item is internal-by-template and sits right in the budget where his
    team reviews (vs. a Daily Log, which took an extra click — his
    preference, replacing the Daily-Log approach built earlier the same
    day).
    """
    quantity_note = (group.get("quantity_note", "") or "").strip()
    confidence = (group.get("confidence", "") or "").strip()
    notes = (group.get("notes", "") or "").strip()
    lines = []
    if quantity_note:
        lines.append(f"Quantity assumption: {quantity_note}")
    if confidence and confidence.lower() != "high":
        lines.append(f"Confidence: {confidence} — spot-check before sending")
    if notes:
        lines.append(f"Note: {notes}")
    return "\n".join(lines)


MIN_REFERENCE_PHOTO_DIM_PX = 150  # below this on either side, treat as a
                                   # logo/icon/decorative graphic, not a real
                                   # inspection photo
MAX_REFERENCE_PHOTOS_PER_GROUP = 6
MIN_REFERENCE_PHOTO_BYTES = 8000  # fallback floor when PIL can't decode an
                                   # image at all (still filters obvious tiny
                                   # icons even without real dimensions)


def extract_reference_photos(pdf_bytes, page_numbers,
                              max_photos=MAX_REFERENCE_PHOTOS_PER_GROUP,
                              min_dim_px=MIN_REFERENCE_PHOTO_DIM_PX):
    """Pull the actual embedded photo(s) off specific 1-indexed pages of a
    PDF — for attaching real inspection-report photos to a JobTread cost
    group (Jason's request, Jul 2026: "just the images from the pdf that
    the inspector took... not any notes").

    Deliberately uses pypdf's own embedded-image extraction (page.images —
    the raw raster XObjects/inline images actually embedded in the page)
    rather than rendering the whole page to a picture. The inspector's
    typed findings and any handwritten text live in the page's separate
    text/vector content stream, which this never touches — only the real
    embedded photo bytes come back. This also means a hand-drawn circle or
    arrow (usually a vector annotation, not a raster image) is naturally
    left out, not captured as a "photo."

    Filters:
      - Drops anything under min_dim_px on either dimension (via Pillow) —
        catches report-template logos/icons/watermarks that appear on every
        page. If Pillow can't decode an image at all, falls back to a
        byte-size floor (MIN_REFERENCE_PHOTO_BYTES) as a weaker signal
        rather than silently including everything undecodable.
      - De-dupes identical image bytes seen across requested pages (a
        repeated header/footer logo shouldn't show up multiple times).
      - Caps total photos returned at max_photos (first-found order,
        iterating pages in the order given).

    Multi-finding pages (a page with photos for more than one inspection
    item) are accepted as-is per Jason's call (Jul 2026) — every real photo
    on a cited page comes back; an occasional unrelated photo tagging along
    is low-cost since these are internal reference photos, not client-facing.

    Never raises — a corrupt PDF, an out-of-range page number, or a
    single bad image just yields fewer/no photos, matching the fail-open
    pattern used everywhere else in this module. Returns a list of
    (image_bytes, mime_type) tuples.
    """
    photos = []
    if not pdf_bytes or not page_numbers:
        return photos

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f"  extract_reference_photos: could not read PDF ({e})")
        return photos

    total_pages = len(reader.pages)
    seen_hashes = set()

    for page_num in page_numbers:
        if len(photos) >= max_photos:
            break
        try:
            idx = int(page_num) - 1
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= total_pages:
            print(f"  extract_reference_photos: page {page_num} is out of "
                  f"range (document has {total_pages} pages) — skipping")
            continue

        try:
            images = reader.pages[idx].images
        except Exception as e:
            print(f"  extract_reference_photos: could not read images on "
                  f"page {page_num} ({e})")
            continue

        for img in images:
            if len(photos) >= max_photos:
                break
            try:
                data = img.data
                if not data:
                    continue

                width = height = None
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(io.BytesIO(data)) as im:
                        width, height = im.size
                except Exception:
                    pass

                if width is not None and height is not None:
                    if width < min_dim_px or height < min_dim_px:
                        continue
                elif len(data) < MIN_REFERENCE_PHOTO_BYTES:
                    continue

                digest = hash(data)
                if digest in seen_hashes:
                    continue
                seen_hashes.add(digest)

                name = (getattr(img, "name", "") or "").lower()
                ext = name.rsplit(".", 1)[-1] if "." in name else ""
                mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                        "png": "image/png", "gif": "image/gif"}.get(ext, "image/jpeg")
                photos.append((data, mime))
            except Exception as e:
                print(f"  extract_reference_photos: skipping one image on "
                      f"page {page_num} ({e})")
                continue

    return photos


def add_cost_groups_v2(job_id, estimate, jobtread_query_fn, org_id=None,
                        inspection_pdf_bytes=None, photo_uploader=None,
                        on_group_created=None, on_item_created=None):
    """
    Create cost groups with MULTIPLE cost items each (labor + material
    lines for in-house work, a single scoped line for sub work) instead of
    today's one-item-per-group pattern.

    jobtread_query_fn: pass in app.py's jobtread_query() function so this
    stays a pure function you can unit test without hitting the real API.

    org_id: optional — only needed to attempt attaching a real Home Depot
    product photo to auto-matched material lines (see _attach_catalog_image)
    and to attach inspection-report reference photos to a cost group. If
    omitted, materials still get a real text description (SKU/brand/link)
    just no photo attachment attempt.

    inspection_pdf_bytes / photo_uploader: optional — enables attaching real
    inspection-report photos directly to each cost group (Jason's request,
    Jul 2026), matched via the "source_pages" field Claude returns per group
    (see STEP 1B in ESTIMATING_LOGIC_SECTION). When both are provided, each
    group's cited pages are run through extract_reference_photos() (pulls
    the actual embedded photo(s) off those pages — never the inspector's
    text/notes), each photo is uploaded via the injected photo_uploader
    callable, and the resulting files are attached directly on that group's
    createCostGroup call via the real "files" argument confirmed to exist on
    createCostGroup/updateCostGroup (found by reading JobTread's own frontend
    source — see CLAUDE.md "true inline attachment" entry, Jul 2026; the
    generic createFile mutation does NOT support costGroup/costItem targets,
    but createCostGroup/createCostItem accept a "files" array directly).
    photo_uploader: callable(photo_bytes, mime_type) -> uploadRequestId (or
    raises on failure). Supplied by app.py, since actually uploading bytes
    needs a temp URL this module has no business serving (Flask/HTTP-server
    access) — kept as an injected dependency so this function stays a pure,
    unit-testable function with a fake uploader, same pattern as
    jobtread_query_fn. If either inspection_pdf_bytes or photo_uploader is
    omitted, groups are created exactly as before with no files attached —
    this feature is fully additive and never blocks group creation on its
    own (a photo extraction/upload failure falls back to creating the group
    without photos, never skips the group entirely).

    on_group_created / on_item_created: optional callables for the
    feedback-loop baseline (Jul 2026 — see feedback_loop.py). Invoked with
    on_group_created(group_id, title, cost_code_name) right after a group
    is created, and on_item_created(group_id, item_id, name, cost_type_id,
    qty, unit_cost, unit_price) right after each REAL billable cost item is
    created (labor/material/sub lines only — the internal $0 NOTES item and
    the "Not Included In This Estimate" skipped-items group are
    deliberately excluded, since they're not billable work worth diffing
    later). Both are wrapped in try/except so a callback bug can never
    break estimate creation itself — this stays fully additive, same
    fail-open philosophy as the photo attachment above. Omit both (the
    default) and behavior is byte-for-byte unchanged from before this
    param existed.
    """
    def _safe_group_cb(group_id, title, cost_code_name):
        if on_group_created:
            try:
                on_group_created(group_id, title, cost_code_name)
            except Exception as e:
                print(f"  on_group_created callback failed (non-fatal): {e}")

    def _safe_item_cb(group_id, item_id, name, cost_type_id, qty, unit_cost, unit_price):
        if on_item_created and item_id:
            try:
                on_item_created(group_id, item_id, name, cost_type_id, qty, unit_cost, unit_price)
            except Exception as e:
                print(f"  on_item_created callback failed (non-fatal): {e}")
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

        # CLIENT-FACING: the group description shows up on customer
        # documents, so it carries ONLY Claude's clean scope description.
        # Internal reasoning (quantity assumptions, confidence flags,
        # internal notes) used to be appended here in brackets — Jason was
        # having to manually delete them from every estimate before sending
        # (Jul 2026: "it has logic and reasoning notes that are internal
        # but those are client viewed"). They now go to an internal Daily
        # Log on the job instead — see _post_internal_review_notes(),
        # called at the end of this function.
        group_description = description

        # Reference photos: extract + upload BEFORE creating the group, so
        # they can be attached in the SAME createCostGroup call via the real
        # "files" argument — no separate update round trip needed. Entirely
        # best-effort: any failure here (bad page number, extraction error,
        # upload error) just means fewer/no photos, never blocks the group.
        photo_files_arg = []
        source_pages = group.get("source_pages") or []
        if inspection_pdf_bytes and photo_uploader and org_id and source_pages:
            try:
                photos = extract_reference_photos(inspection_pdf_bytes, source_pages)
            except Exception as e:
                print(f"  Reference photo extraction failed for '{title[:50]}' (non-fatal): {e}")
                photos = []
            for photo_bytes, mime in photos:
                try:
                    upload_id = photo_uploader(photo_bytes, mime)
                except Exception as e:
                    print(f"  Reference photo upload failed for '{title[:50]}' (non-fatal): {e}")
                    continue
                if upload_id:
                    photo_files_arg.append({
                        "organizationId": org_id, "targetType": "costGroup",
                        "uploadRequestId": upload_id
                    })

        create_group_args = {
            "jobId": job_id, "name": title[:100],
            "description": group_description or None
        }
        if photo_files_arg:
            create_group_args["files"] = photo_files_arg

        try:
            resp = jobtread_query_fn({
                "createCostGroup": {"$": create_group_args, "createdCostGroup": {"id": {}}}
            })
            group_id = resp["createCostGroup"]["createdCostGroup"]["id"]
        except Exception as e:
            if photo_files_arg:
                print(f"  createCostGroup with photos failed for '{title[:50]}' "
                      f"({e}) — retrying without photos")
                try:
                    resp = jobtread_query_fn({
                        "createCostGroup": {
                            "$": {"jobId": job_id, "name": title[:100],
                                  "description": group_description or None},
                            "createdCostGroup": {"id": {}}
                        }
                    })
                    group_id = resp["createCostGroup"]["createdCostGroup"]["id"]
                except Exception as e2:
                    print(f"  Skipping group '{title[:50]}' (create group failed): {e2}")
                    continue
            else:
                print(f"  Skipping group '{title[:50]}' (create group failed): {e}")
                continue

        _safe_group_cb(group_id, title[:100], cost_code_name)
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
        # Sub work: either itemized per-scope lines (sub_scope_lines — one
        # cost item per distinct scope, Jason's request Jul 2026 reviewing
        # 12 Tall Tree Ln's lump-sum crawlspace group: "I would prefer the
        # pricing be broken up into multiple items based on the scope of
        # work... easier for me to see the logic when its broken up") or the
        # original single lump-sum sub_scope_price. Both are scope-based sub
        # pricing at 1.45x markup — sub_scope_lines just shows the per-scope
        # logic instead of hiding it in one number. If Claude ever returns
        # both, the itemized lines win and the lump sum is skipped (they'd
        # double-count otherwise).
        sub_lines = group.get("sub_scope_lines") or []
        sub_cost = float(group.get("sub_scope_price", 0) or 0)
        if sub_lines:
            if sub_cost > 0:
                print(f"  NOTE: group '{title[:50]}' has BOTH sub_scope_lines and "
                      f"sub_scope_price — using the itemized lines, ignoring the lump sum")
            for line in sub_lines:
                line_cost = float(line.get("cost", 0) or 0)
                line_item = (line.get("item", "") or "").strip() or "Subcontractor scope"
                if line_cost <= 0:
                    continue
                line_price = round(line_cost * SUB_MARKUP, 2)
                try:
                    resp = jobtread_query_fn({
                        "createCostItem": {
                            "$": {
                                "costGroupId": group_id, "name": line_item[:100], "quantity": 1,
                                "unitCost": line_cost, "unitPrice": line_price,
                                "costCodeId": cost_code_id, "costTypeId": COST_TYPE_SUB
                            },
                            "createdCostItem": {"id": {}}
                        }
                    })
                    items_added_this_group += 1
                    item_id = (resp or {}).get("createCostItem", {}).get("createdCostItem", {}).get("id")
                    _safe_item_cb(group_id, item_id, line_item[:100], COST_TYPE_SUB, 1, line_cost, line_price)
                except Exception as e:
                    print(f"  Failed to add sub scope line '{line_item[:50]}': {e}")
        elif sub_cost > 0:
            sub_price = round(sub_cost * SUB_MARKUP, 2)
            item_name = _strip_section_prefix(title)
            try:
                resp = jobtread_query_fn({
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
                item_id = (resp or {}).get("createCostItem", {}).get("createdCostItem", {}).get("id")
                _safe_item_cb(group_id, item_id, item_name[:100], COST_TYPE_SUB, 1, sub_cost, sub_price)
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
                labor_item_name = f"{trade.capitalize()} labor"[:100]
                try:
                    resp = jobtread_query_fn({
                        "createCostItem": {
                            "$": {
                                "costGroupId": group_id,
                                "name": labor_item_name,
                                "quantity": hours,
                                "unitCost": LABOR_COST_RATE,  # real internal cost ($55/hr)
                                "unitPrice": rate,             # billed rate (89.00)
                                "costCodeId": cost_code_id, "costTypeId": COST_TYPE_LABOR
                            },
                            "createdCostItem": {"id": {}}
                        }
                    })
                    items_added_this_group += 1
                    item_id = (resp or {}).get("createCostItem", {}).get("createdCostItem", {}).get("id")
                    _safe_item_cb(group_id, item_id, labor_item_name, COST_TYPE_LABOR,
                                  hours, LABOR_COST_RATE, rate)
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

                # TRUE catalog linking — REAL ARG NAME FOUND (Jul 2026),
                # not a guess this time. The two-step recipe's step 1
                # (link_catalog_master_item / createGlobalOrganizationCostItem)
                # was already confirmed working once GLOBAL_CATALOG_PRODUCT_TYPE
                # was fixed to "homeDepot". Step 2 kept failing with
                # createCostItem rejecting a nested `sourceCostItem: {id:...}`
                # relation object ('no value is ever expected there'). Root
                # cause found by reading JobTread's own frontend bundle
                # (src/functions/get-line-item-args.js, fetched directly from
                # the loaded JS and grepped for "sourceCostItem" — this is the
                # exact function JobTread's own UI calls to build its
                # createCostItem args): it flattens the relation to a plain
                # scalar before sending — `sourceCostItemId: j && j.id` (same
                # pattern used for organizationCostItemId/jobCostItemId). So
                # the real arg is a flat `sourceCostItemId` string, not a
                # nested `sourceCostItem` object. This is a separate tier from
                # description/customFieldValues (see the 3-tier retry below)
                # so a bad link attempt can never again cost us the real
                # product detail the way the old nested-object attempt did.
                link_args = dict(enriched_args)
                if ATTEMPT_TRUE_CATALOG_LINK:
                    catalog_match = line.get("catalog_match") or {}
                    product_id = catalog_match.get("product_id")
                    if product_id and org_id:
                        master_id = link_catalog_master_item(jobtread_query_fn, org_id, product_id)
                        if master_id:
                            link_args["sourceCostItemId"] = master_id

                # 3-tier retry: (1) full args including the true-catalog-link
                # scalar, (2) enriched args (description/customFieldValues,
                # no link) if the link tier fails, (3) bare args if THAT also
                # fails. Each tier only drops what actually caused the
                # failure — a bad link never again costs the real product
                # detail, and a bad custom field never costs the line item
                # itself.
                try:
                    resp = jobtread_query_fn({
                        "createCostItem": {"$": link_args, "createdCostItem": {"id": {}}}
                    })
                except Exception as link_err:
                    if link_args != enriched_args:
                        print(f"  Linked material create failed for '{item[:50]}' "
                              f"({link_err}) — retrying without sourceCostItemId")
                        try:
                            resp = jobtread_query_fn({
                                "createCostItem": {"$": enriched_args, "createdCostItem": {"id": {}}}
                            })
                        except Exception as enriched_err:
                            resp = None
                    else:
                        enriched_err = link_err
                        resp = None

                    if resp is None and enriched_args != base_args:
                        print(f"  Enriched material create failed for '{item[:50]}' "
                              f"({enriched_err}) — retrying with bare args (no description/custom fields)")
                        try:
                            resp = jobtread_query_fn({
                                "createCostItem": {"$": base_args, "createdCostItem": {"id": {}}}
                            })
                        except Exception as e:
                            print(f"  Failed to add material line for '{title[:50]}' (bare retry also failed): {e}")
                            resp = None
                    elif resp is None:
                        print(f"  Failed to add material line for '{title[:50]}': {enriched_err}")

                if resp is not None:
                    items_added_this_group += 1
                    item_id = (resp or {}).get("createCostItem", {}).get("createdCostItem", {}).get("id")
                    _safe_item_cb(group_id, item_id, item[:100], COST_TYPE_MATERIALS,
                                  qty, unit_cost, unit_price)
                    # Photo attachment via createFile(targetType="costItem")
                    # is CONFIRMED UNSUPPORTED (Jul 2026, real production run,
                    # job 22PbQbX4bfWm) — the API's own validation error names
                    # the full valid enum for targetType: "dailyLog",
                    # "document", "task", "job", "location", "contact",
                    # "account", "organization". "costItem" simply isn't one
                    # of them — this was never a shape bug we could fix, it's
                    # a real gap in what createFile can target. Disabled via
                    # ATTEMPT_CATALOG_PHOTO_ATTACH below rather than deleted,
                    # in case JobTread adds costItem support later.
                    if ATTEMPT_CATALOG_PHOTO_ATTACH:
                        catalog_match = line.get("catalog_match") or {}
                        image_url = catalog_match.get("imageUrl")
                        if image_url and org_id:
                            try:
                                created_id = resp["createCostItem"]["createdCostItem"]["id"]
                                _attach_catalog_image(jobtread_query_fn, org_id, created_id,
                                                       image_url, item[:100])
                            except Exception as e:
                                print(f"  Photo attach skipped for '{item[:50]}' (non-fatal): {e}")

        # Internal review notes as a $0 line item at the bottom of the
        # group (Jason's preference over a Daily Log — "right in the budget"
        # vs. an extra click). Safe because his client-facing estimate
        # template shows only group names/descriptions, never the line
        # items below them (confirmed Jul 2026). The note text goes in the
        # item's description; the name is a short fixed label. Never counts
        # toward items_added_this_group (it's a note, not billable work, so
        # a group with ONLY a notes item still triggers the zero-items
        # warning below). Non-fatal on failure.
        internal_notes = _build_group_internal_notes(group)
        if internal_notes:
            try:
                jobtread_query_fn({
                    "createCostItem": {
                        "$": {
                            "costGroupId": group_id,
                            "name": "NOTES (internal — not shown on client documents)",
                            "quantity": 1, "unitCost": 0, "unitPrice": 0,
                            "description": internal_notes,
                            "costCodeId": cost_code_id, "costTypeId": COST_TYPE_OTHER
                        },
                        "createdCostItem": {"id": {}}
                    }
                })
            except Exception as e:
                print(f"  Internal notes item failed for '{title[:50]}' (non-fatal): {e}")

        if items_added_this_group > 0:
            added_groups += 1
        else:
            print(f"  WARNING: group '{title[:50]}' created with zero cost items — check output schema")

    print(f"  {added_groups}/{len(cost_groups)} cost groups added (multi-line)")

    # Surface anything Claude couldn't quote (out-of-scope items, or scope
    # too vague to size responsibly) as one final, plainly-worded cost
    # group at the bottom of the budget — see _build_skipped_items_note.
    # Added last so it lands after every real repair group; zero cost
    # items is intentional here (it's a note, not billable work), so this
    # deliberately does NOT go through the same "zero cost items" warning
    # path as the main loop above.
    skipped_note = _build_skipped_items_note(estimate.get("skipped_items"))
    if skipped_note:
        item_count = skipped_note.count("\n- ")
        try:
            jobtread_query_fn({
                "createCostGroup": {
                    "$": {"jobId": job_id, "name": "Not Included In This Estimate",
                          "description": skipped_note},
                    "createdCostGroup": {"id": {}}
                }
            })
            print(f"  Added 'Not Included In This Estimate' note ({item_count} item(s))")
        except Exception as e:
            print(f"  Failed to add skipped-items note group (non-fatal): {e}")

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

# ─────────────────────────────────────────────────────────────────────────
# LARGE-DOCUMENT HANDLING (Aug 2026 — Candler / 102 Tuscany Way failure)
# ─────────────────────────────────────────────────────────────────────────
# BUG FIX #3: a submission came in with a 7,671,618-byte inspection report
# uploaded into BOTH the addendum and inspection slots (the Wufoo form
# requires a file in each slot, so when a realtor has no separate repair
# addendum they just upload the inspection report twice — in this case the
# submission notes also explicitly asked for every item in the report to be
# quoted, so estimating off the report alone was correct). All three
# attempts died with "The read operation timed out", ~20 minutes wasted
# before the lead got flagged for manual entry.
#
# Three separate things were wrong, and all three are fixed here:
#
#   1. THE SAME 7.6MB FILE WAS SENT TWICE. base64 inflates it ~33%, so the
#      request body carried ~20.4MB of duplicated PDF. Fixed by comparing
#      the two byte strings — if they're identical, only one copy is sent
#      and build_claude_document_content()'s existing "inspection report
#      only" prompt branch takes over (which already tells Claude that
#      single document carries BOTH the scope-of-work and the findings).
#      Nothing about photo/markup vision changes — it's still a native PDF
#      document block, Claude still reads the photos and handwritten
#      annotations, it just isn't asked to read the same 7.6MB twice.
#
#   2. THE CALL WASN'T STREAMED. urlopen()'s timeout is a per-socket-read
#      timeout, and a non-streamed request sends NOTHING back until the
#      whole generation finishes. With max_tokens=24000 and a big
#      photo-heavy report, total generation time ran past the 400s ceiling,
#      so the socket timed out even though the API was working fine. Fixed
#      by streaming (stream=true + SSE parsing in _stream_claude_message):
#      tokens arrive continuously, the socket never sits idle, and the
#      timeout now means "the API went quiet for N seconds" instead of
#      "the whole job took longer than N seconds". This is the real fix —
#      it removes read-timeout as a failure mode at ANY document size.
#
#   3. THE RETRIES COULDN'T HAVE WORKED. json.dumps() of the full payload
#      ran INSIDE the retry loop, so each attempt re-serialized ~20MB of
#      base64 (expensive on Render Starter's 0.5 CPU / 512MB) and then hit
#      the exact same deterministic timeout. Fixed: the payload is built
#      once outside the loop, and truncation now escalates max_tokens
#      instead of retrying into the same wall (see BUG FIX #2's note that
#      that failure mode is not transient either).
#
# Plus, for genuinely large files, the Files API (upload once, reference by
# file_id) replaces inline base64 — see _upload_pdf_to_files_api below.
#
# Deliberately NOT done: stripping or downscaling the images to shrink the
# PDF. The photos are the point — inspection photos routinely show extent
# that the inspector's one-line text finding doesn't, and the system prompt
# already tells Claude how to reconcile the two when they disagree.
# Degrading them to save bytes would trade away the most valuable signal
# in the document.

# Above this size, a PDF is uploaded to the Files API and referenced by
# file_id instead of being inlined as base64 in the request body. Inline
# base64 is kept for normal-sized files because it's one fewer network
# round-trip and it's the path that's been running in production all year.
INLINE_PDF_MAX_BYTES = 4 * 1024 * 1024   # 4MB

# Anthropic's Files API beta header (files-api-2025-04-14). Files persist
# until explicitly deleted; we delete ours in call_claude_v2's finally
# block so OCC's org storage doesn't accumulate a PDF per submission.
_FILES_API_BETA = "files-api-2025-04-14"


class NonRetryableEstimateError(Exception):
    """Raised for failures where retrying is provably pointless — e.g. a
    PDF over Claude's 100-page cap. _call_claude_v2_core lets these escape
    the retry loop immediately instead of burning three attempts (and, on
    a big file, several minutes) re-proving the same thing.
    """
    pass


def _upload_pdf_to_files_api(pdf_bytes, filename, anthropic_api_key, timeout=300):
    """Upload a PDF to the Anthropic Files API and return its file_id.

    Written against http.client rather than urllib so the multipart body
    can be sent in 256KB chunks off a memoryview instead of concatenated
    into one giant bytes object first. On Render Starter (512MB) that
    matters: the naive version would hold the original 7.6MB PDF, a 7.6MB
    copy inside the multipart blob, and urllib's own copy of the request
    body all at once.

    Why the Files API at all, when inline base64 already worked?
      - No base64 step, so no ~33% inflation and no second full-size copy
        of the document sitting in memory as a str.
      - A retry re-sends a ~30-character file_id instead of re-uploading
        and re-serializing megabytes.
      - It sidesteps the 32MB total request-size cap that inline documents
        count against (the 100-page-per-PDF cap still applies — that's
        what _check_pdf_page_count guards).
    """
    boundary = "----OCCFormBoundary" + os.urandom(16).hex()
    preamble = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")
    epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")

    conn = http.client.HTTPSConnection("api.anthropic.com", timeout=timeout)
    try:
        conn.putrequest("POST", "/v1/files")
        conn.putheader("x-api-key", anthropic_api_key)
        conn.putheader("anthropic-version", "2023-06-01")
        conn.putheader("anthropic-beta", _FILES_API_BETA)
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(len(preamble) + len(pdf_bytes) + len(epilogue)))
        conn.endheaders()

        conn.send(preamble)
        view = memoryview(pdf_bytes)
        chunk = 256 * 1024
        for offset in range(0, len(view), chunk):
            conn.send(view[offset:offset + chunk])
        conn.send(epilogue)

        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status not in (200, 201):
            raise Exception(f"Files API upload failed: HTTP {resp.status} — {body[:400]}")
        file_id = json.loads(body).get("id")
        if not file_id:
            raise Exception(f"Files API upload returned no id: {body[:400]}")
        return file_id
    finally:
        conn.close()


def _delete_files_api_file(file_id, anthropic_api_key, timeout=30):
    """Best-effort cleanup of an uploaded file. Never raises — a leaked
    file costs a little org storage, a raised exception here would throw
    away an estimate that already succeeded.
    """
    try:
        req = urllib.request.Request(
            f"https://api.anthropic.com/v1/files/{file_id}",
            headers={
                "x-api-key": anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": _FILES_API_BETA,
            },
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except Exception as e:
        print(f"  (non-fatal) could not delete uploaded file {file_id}: {e}")


def _stream_claude_message(payload_bytes, anthropic_api_key, timeout, beta_headers=None):
    """POST to /v1/messages with stream=true and reassemble the SSE events
    into (full_text, stop_reason).

    The timeout passed to urlopen() applies to each individual socket read,
    not to the request as a whole. Because a streaming response emits text
    deltas (and periodic `ping` events) the entire time the model is
    generating, the socket is never idle for long — so `timeout` now means
    "the API stopped responding for this many seconds", which is what we
    actually wanted to detect all along. A long-but-healthy generation no
    longer trips it, which is exactly the failure that killed the Candler
    submission three times in a row.
    """
    headers = {
        "Content-Type": "application/json",
        "x-api-key": anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "accept": "text/event-stream",
    }
    if beta_headers:
        headers["anthropic-beta"] = ",".join(beta_headers)

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload_bytes,
        headers=headers,
        method="POST",
    )

    text_parts = []
    stop_reason = None

    try:
        stream = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:600]
        except Exception:
            pass
        # A malformed request (bad block shape, expired/unknown file_id,
        # document over a hard limit) fails identically on all three
        # attempts. Only 429/5xx are worth retrying.
        if e.code in (400, 401, 403, 404, 413, 422):
            raise NonRetryableEstimateError(
                f"Anthropic API rejected the request: HTTP {e.code} — {body}")
        raise Exception(f"Anthropic API error: HTTP {e.code} — {body}")

    with stream as r:
        for raw_line in r:                      # iterates the stream line by line
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue                        # blank lines and `event:` lines
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_parts.append(delta.get("text", ""))
            elif etype == "message_delta":
                stop_reason = event.get("delta", {}).get("stop_reason") or stop_reason
            elif etype == "error":
                raise Exception(f"Claude stream error: {event.get('error')}")

    return "".join(text_parts), stop_reason


def _check_pdf_page_count(pdf_bytes, label, max_pages=100):
    """Raise NonRetryableEstimateError if a PDF exceeds Claude's page cap.
    Best-effort — if the page count can't be determined (missing pypdf,
    corrupt header, etc.) this just logs and lets the API call itself be
    the real check.

    Aug 2026: this used to raise ValueError, which _call_claude_v2_core's
    except clause caught and retried twice more. A 140-page report is
    still 140 pages on attempt 3, so that only delayed the inevitable —
    on a large file, by several minutes of re-serialization. It now raises
    a NonRetryableEstimateError that skips straight past the retry loop to
    the manual-review to-do, where a human can split the file.
    """
    try:
        from pypdf import PdfReader
        page_count = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        if page_count > max_pages:
            raise NonRetryableEstimateError(
                f"{label} has {page_count} pages — Claude's PDF support caps "
                f"at {max_pages} pages per document. Split the file and try again."
            )
    except NonRetryableEstimateError:
        raise
    except Exception as e:
        print(f"  Could not pre-check {label} page count: {e}")


def _pdf_document_block(pdf_bytes, file_id, cache=False):
    """Build one native-PDF document block, either as a Files API reference
    (file_id) or as inline base64. Identical to Claude either way — it's a
    full native PDF in both cases, with photos and handwritten markups
    intact. Only the transport differs.

    cache=True stamps a prompt-caching breakpoint on the block so a retry
    within the cache window re-reads the document from cache instead of
    re-processing every page of a large report from scratch.
    """
    if file_id:
        source = {"type": "file", "file_id": file_id}
    else:
        source = {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(pdf_bytes).decode("utf-8"),
        }
    block = {"type": "document", "source": source}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def build_claude_document_content(addendum_pdf_bytes, inspection_pdf_bytes,
                                   client_name, client_phone, client_email,
                                   address, notes,
                                   addendum_file_id=None, inspection_file_id=None):
    """Build the `content` list for the Claude messages API call: intro text
    plus native PDF document blocks for whichever of (addendum, inspection)
    are actually present. Both are vision (native PDF), never text-extracted.

    addendum_file_id / inspection_file_id (Aug 2026): when set, that
    document is referenced by Files API id instead of inlined as base64.
    call_claude_v2 decides which path per file based on size. This changes
    nothing about what Claude sees — same native PDF, same photo and
    markup reading — it just keeps multi-megabyte reports out of the
    request body (and out of Render's 512MB of RAM).
    """
    have_addendum = bool(addendum_pdf_bytes) or bool(addendum_file_id)
    have_inspection = bool(inspection_pdf_bytes) or bool(inspection_file_id)

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
        if addendum_pdf_bytes:
            _check_pdf_page_count(addendum_pdf_bytes, "Repair addendum")
        content.append({"type": "text", "text": "\n=== REPAIR ADDENDUM (PDF below — read text, photos, and handwritten annotations; may be a scan) ==="})
        content.append(_pdf_document_block(
            addendum_pdf_bytes, addendum_file_id,
            cache=not have_inspection,          # cache breakpoint on the LAST document
        ))

    if have_inspection:
        if inspection_pdf_bytes:
            _check_pdf_page_count(inspection_pdf_bytes, "Inspection report")
        content.append({"type": "text", "text": "\n=== INSPECTION REPORT (PDF below — read text, photos, and handwritten annotations) ==="})
        content.append(_pdf_document_block(
            inspection_pdf_bytes, inspection_file_id,
            cache=True,
        ))

    content.append({"type": "text", "text": "\nRespond with ONLY the raw JSON object. No markdown, no explanation."})
    return content


def call_claude_v2(addendum_pdf_bytes, inspection_pdf_bytes, client_name,
                    client_phone, client_email, address, notes,
                    system_prompt, anthropic_api_key,
                    model="claude-sonnet-4-6", max_tokens=24000, timeout=180):
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
    addendum_pdf_bytes = addendum_pdf_bytes or b""
    inspection_pdf_bytes = inspection_pdf_bytes or b""

    # ── Dedupe (BUG FIX #3) ────────────────────────────────────────────────
    # The Wufoo form requires a file in both the addendum and the inspection
    # slot. When a realtor has no separate repair addendum they upload the
    # inspection report into both — so the two downloads come back
    # byte-identical. Sending it twice doubled the payload for no added
    # information and contributed directly to the Candler timeout.
    #
    # bytes.__eq__ is a length check followed by memcmp, so this costs
    # essentially nothing and allocates nothing (no hashing needed).
    if addendum_pdf_bytes and inspection_pdf_bytes and addendum_pdf_bytes == inspection_pdf_bytes:
        print(
            f"  Addendum and inspection report are byte-identical "
            f"({len(inspection_pdf_bytes):,} bytes) — realtor uploaded the same "
            f"file to both slots (normal when there's no separate addendum). "
            f"Sending ONE copy; prompt switches to inspection-report-only mode."
        )
        addendum_pdf_bytes = b""

    # ── Files API for large documents ──────────────────────────────────────
    # Anything over INLINE_PDF_MAX_BYTES is uploaded once and referenced by
    # id, so retries don't re-serialize megabytes and the request body stays
    # small. Falls back to inline base64 if the upload fails for any reason
    # — a slow path that works beats a fast path that doesn't.
    addendum_file_id = None
    inspection_file_id = None
    uploaded_ids = []

    try:
        if len(addendum_pdf_bytes) > INLINE_PDF_MAX_BYTES:
            try:
                print(f"  Addendum is {len(addendum_pdf_bytes):,} bytes — uploading via Files API...")
                addendum_file_id = _upload_pdf_to_files_api(
                    addendum_pdf_bytes, "repair_addendum.pdf", anthropic_api_key)
                uploaded_ids.append(addendum_file_id)
                print(f"  Addendum uploaded: {addendum_file_id}")
            except Exception as e:
                print(f"  Files API upload failed for addendum ({e}) — falling back to inline base64")
                addendum_file_id = None

        if len(inspection_pdf_bytes) > INLINE_PDF_MAX_BYTES:
            try:
                print(f"  Inspection report is {len(inspection_pdf_bytes):,} bytes — uploading via Files API...")
                inspection_file_id = _upload_pdf_to_files_api(
                    inspection_pdf_bytes, "inspection_report.pdf", anthropic_api_key)
                uploaded_ids.append(inspection_file_id)
                print(f"  Inspection report uploaded: {inspection_file_id}")
            except Exception as e:
                print(f"  Files API upload failed for inspection report ({e}) — falling back to inline base64")
                inspection_file_id = None

        content = build_claude_document_content(
            addendum_pdf_bytes if not addendum_file_id else b"",
            inspection_pdf_bytes if not inspection_file_id else b"",
            client_name, client_phone, client_email, address, notes,
            addendum_file_id=addendum_file_id,
            inspection_file_id=inspection_file_id,
        )

        # Page-count pre-check still has to run against the real bytes even
        # when the document went up via the Files API — build_claude_document
        # _content can only check what it was handed, and the 100-page cap
        # applies to Files API documents exactly the same way.
        if addendum_file_id:
            _check_pdf_page_count(addendum_pdf_bytes, "Repair addendum")
        if inspection_file_id:
            _check_pdf_page_count(inspection_pdf_bytes, "Inspection report")

        return _call_claude_v2_core(
            content, system_prompt, anthropic_api_key,
            model=model, max_tokens=max_tokens, timeout=timeout,
            caller_label="call_claude_v2",
            beta_headers=[_FILES_API_BETA] if uploaded_ids else None,
        )
    finally:
        # Uploaded files persist until deleted. One PDF per submission would
        # accumulate forever against OCC's org storage, and we have no reason
        # to keep them — the estimate is already written to JobTread and the
        # originals still live in Wufoo and on the JobTread job.
        for fid in uploaded_ids:
            _delete_files_api_file(fid, anthropic_api_key)


def _call_claude_v2_core(content, system_prompt, anthropic_api_key,
                          model="claude-sonnet-4-6", max_tokens=24000, timeout=180,
                          caller_label="call_claude_v2", beta_headers=None):
    """Shared retry/timeout/parsing core behind BOTH call_claude_v2 (closing
    repairs — two optional PDFs) and call_claude_v2_general (Home Repair /
    GVL / Remodel / Pre-listing / general sales-tool — one optional PDF
    and/or photos and/or a plain description). Only the `content` list
    differs between the two callers; the actual API call, retry policy,
    and response parsing are identical, so this was pulled out of
    call_claude_v2 (Jul 2026, migrating the other four job-type flows onto
    the same v2 labor+material pipeline) rather than duplicated a second
    time. See call_claude_v2's docstring for the history behind the
    timeout/max_tokens defaults and the broadened retry exception list.
    """
    # The system prompt is large and identical on every call (full estimating
    # logic + price tables + historical reference examples). Sending it as a
    # cached text block lets repeat calls inside the cache window skip
    # re-processing it — which mostly matters for retries, where the whole
    # point is to not pay full freight a second and third time.
    system_blocks = [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]

    current_max_tokens = max_tokens
    last_error = None

    for attempt in range(1, 4):
        try:
            # Serialized INSIDE the loop only because max_tokens can change
            # between attempts (see the truncation branch below) — but the
            # `content` list itself is built once by the caller, and with the
            # Files API path a large document is a ~30-char id here rather
            # than megabytes of base64. This is no longer the 20MB-per-attempt
            # re-serialization that made the Candler retries so expensive.
            payload = json.dumps({
                "model": model,
                "max_tokens": current_max_tokens,
                "system": system_blocks,
                "stream": True,
                "messages": [{"role": "user", "content": content}],
            }).encode("utf-8")

            raw, stop_reason = _stream_claude_message(
                payload, anthropic_api_key, timeout, beta_headers=beta_headers)

            if stop_reason == "max_tokens":
                # Not transient — retrying with the same ceiling truncates at
                # exactly the same place (BUG FIX #2 learned this the hard
                # way). Escalate instead, up to the model's 64k output cap.
                bumped = min(64000, int(current_max_tokens * 1.5))
                if bumped > current_max_tokens and attempt < 3:
                    print(f"  Response truncated at max_tokens={current_max_tokens} "
                          f"({len(raw)} chars) — raising to {bumped} for the next attempt")
                    current_max_tokens = bumped
                raise ValueError(
                    f"Claude response truncated by max_tokens (attempt {attempt}), "
                    f"got {len(raw)} chars before cutoff"
                )

            if not raw.strip():
                raise ValueError("Claude returned an empty response")

            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                raise ValueError(f"No JSON object found in Claude response: {raw[:500]}")

            return json.loads(match.group(0))

        except NonRetryableEstimateError:
            # Page cap and friends — retrying proves nothing. Straight to the
            # manual-review to-do so a human can act on it now, not in twenty
            # minutes.
            raise

        except (json.JSONDecodeError, ValueError, TimeoutError, OSError, http.client.HTTPException) as e:
            last_error = e
            print(f"  {caller_label} attempt {attempt} failed ({type(e).__name__}): {e}")
            if attempt < 3:
                print(f"  Retrying ({attempt + 1}/3)...")
                continue

    raise Exception(
        f"{caller_label} failed after 3 attempts. Last error: {last_error}. "
        f"This lead needs to be entered manually — check Render logs for the raw Claude output."
    )


def build_general_document_content(job_type_label, client_name, client_phone,
                                    client_email, address, notes, description="",
                                    pdf_bytes=None, pdf_label="Inspection Report",
                                    image_blocks=None, best_effort=True):
    """General-purpose Claude document-content builder for the four
    non-closing-repair flows (Home Repair, GVL Today, Remodel, Pre-listing
    Repair) plus the general sales-tool endpoint (Jul 2026 — Jason's
    request to bring the same labor+material reasoning/catalog-matching
    pipeline closing repairs already has to these other job types too).

    Each of these flows has a genuinely different real input shape than
    closing repairs (no repair addendum, often no PDF at all, sometimes
    just a couple of photos, sometimes just a text description), so this
    builds whatever combination of (description text, one optional native-
    PDF document, zero or more photo image blocks) is actually available
    for the lead, rather than assuming two PDFs the way
    build_claude_document_content() does for closing repairs.

    image_blocks: a pre-built list of Claude image content blocks (see
    download_image_block() in app.py) — built by the caller since
    downloading images needs app.py's network helper; keeps this function
    pure/unit-testable like build_claude_document_content().

    pdf_label: what to call the PDF in the prompt text (e.g. "Inspection
    Report" for Pre-listing, which sometimes gets a real inspection report;
    generic callers can pass whatever's most accurate).
    """
    have_pdf = bool(pdf_bytes)
    have_images = bool(image_blocks)
    have_description = bool((description or "").strip())

    if have_pdf:
        doc_instructions = (
            f"A {pdf_label} PDF is provided below — read its printed text AND "
            f"any photos or handwritten markups on the pages; photos often "
            f"show the true extent of an issue that a one-line text finding "
            f"doesn't capture."
        )
    elif have_images:
        doc_instructions = (
            "Photo(s) of the property/issue are provided below — look at "
            "them closely; they're the best available evidence of actual "
            "scope and severity here, since there's no formal inspection "
            "report for this lead."
        )
    else:
        doc_instructions = (
            "No inspection report or photos were provided for this lead — "
            "this is common for a best-effort inquiry. Base the estimate "
            "entirely on the client's own description below."
        )

    content = []
    best_effort_line = (
        "This is a best-effort estimate from a homeowner inquiry (no formal "
        "inspection report)." if best_effort else "This is a full estimate."
    )
    consult_line = (
        "If the information is too vague to estimate responsibly, return an "
        "empty cost_groups array with needs_consult=true and a short "
        "consult_reason — do not invent scope."
        if best_effort else ""
    )
    intro = f"""Generate a {job_type_label} estimate for Owners Choice Construction.

Client name: {client_name}
Client phone: {client_phone}
Client email: {client_email}
Property address: {address}
{f"Intake notes: {notes}" if notes else ""}

{best_effort_line} {doc_instructions}
{consult_line}
"""
    content.append({"type": "text", "text": intro})

    if have_description:
        content.append({"type": "text",
                         "text": f"\n=== CLIENT DESCRIPTION OF WORK ===\n{description[:8000]}"})

    if have_pdf:
        _check_pdf_page_count(pdf_bytes, pdf_label)
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        content.append({"type": "text",
                         "text": f"\n=== {pdf_label.upper()} (PDF below — read text, "
                                 f"photos, and handwritten annotations; may be a scan) ==="})
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}
        })

    if have_images:
        content.append({"type": "text", "text": "\n=== PHOTOS ==="})
        content.extend(image_blocks)

    content.append({"type": "text", "text": "\nRespond with ONLY the raw JSON object. No markdown, no explanation."})
    return content


def call_claude_v2_general(job_type_label, client_name, client_phone, client_email,
                            address, notes, system_prompt, anthropic_api_key,
                            description="", pdf_bytes=None, pdf_label="Inspection Report",
                            image_blocks=None, best_effort=True,
                            model="claude-sonnet-4-6", max_tokens=24000, timeout=180):
    """v2 entry point for Home Repair / GVL Today / Remodel / Pre-listing
    Repair / general sales-tool — same underlying labor+material reasoning,
    real cost codes, historical-example calibration, and Home Depot catalog
    matching as call_claude_v2() (closing repairs), just built from
    whatever input shape that job type actually has (see
    build_general_document_content). Shares call_claude_v2's exact retry/
    timeout/parsing behavior via _call_claude_v2_core — same defaults, same
    reasons for those defaults (see call_claude_v2's docstring).
    """
    content = build_general_document_content(
        job_type_label, client_name, client_phone, client_email, address, notes,
        description=description, pdf_bytes=pdf_bytes, pdf_label=pdf_label,
        image_blocks=image_blocks, best_effort=best_effort
    )
    return _call_claude_v2_core(content, system_prompt, anthropic_api_key,
                                 model=model, max_tokens=max_tokens, timeout=timeout,
                                 caller_label="call_claude_v2_general")
