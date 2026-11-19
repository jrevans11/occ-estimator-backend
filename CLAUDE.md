# OCC Estimator Backend

## Me
Jason Evans, owner of Owners Choice Construction LLC (OCC), residential closing-repairs contractor, Greenville/Upstate SC. Not a coder, doesn't use a terminal. (864) 252-4999, 3122 Wade Hampton Blvd, Taylors, SC 29687, ownerschoicerepairs.com.

This repo is the Render-hosted Python backend (`app.py`) that reads inspection reports + repair addendums and drafts estimates into JobTread, plus the Site Scope and Ballpark tools.

## Pricing rules
- Labor: $55/hr internal cost, $89/hr billed. Line item named "Hourly Rate".
- Materials (in-house): cost x 1.65. Subs: cost x 1.45.
- Minimum in-house labor: 3 hrs per job (all-sub jobs exempt).
- Sub trades: all electrical; fireplace/gas appliance (always); major HVAC; major plumbing (re-pipe, sewer, water heater, slab leak); crawlspace moisture; significant masonry (multi-location repointing, structural, retaining walls, pavers); significant flooring/tile (full room+, hardwood refinish, re-tile). Roofing: judgement, prefer sub on a close call.
- In-house: everything else, including small masonry/flooring/tile patches.
- Never estimate: radon, landscaping/grading, driveway/erosion, termite/WDO/pest treatment, water/mold testing, septic, structural engineering, anything marked "not evaluated". Walkways, steps, stoops, patios, exterior stairs ARE in scope. Carpentry repair of insect damage is in scope as an allowance.
- Never invent prices for licensed trades: `$0 (PRICING TBD)` unless a sub bid or rate anchor exists.

## Subs
Redland Electric (864) 909-4441 electrical · Crawlspace Medic (864) 478-8598 crawlspace/foundation · Tile with Style (864) 205-3723 tile · Greer Flooring (864) 331-3000 carpet/LVP/refinish/small grout · Jordan Lumber (864) 238-4251 hardwood (finish work billed separately by Greg Porter Floorsanding).
Rate anchors: `*_invoice_history.csv` in repo root.

## JobTread
- Org `22P9ppHePJKP`. Pave API `POST https://api.jobtread.com/pave`. Env: `JOBTREAD_API_KEY`, `JOBTREAD_ORG_ID`, `ANTHROPIC_API_KEY`, `WUFOO_API_KEY`, optional `RENDER_API_KEY`/`RENDER_SERVICE_ID` (Render, not in repo).
- Cost types: Labor `22P9ppJUAHYN`, Materials `22P9ppJUAHYP`, Subcontractor `22P9ppJUAHYQ`, Other `22P9ppJUAHYR`.
- Cost codes: use the 3-digit set only (`cost_codes_3digit.csv`, mapped in `COST_CODE_MAP`). Don't alias "Painting"; pick Interior or Exterior Paint.
- Home Depot catalog: top-level `homeDepotProducts` query (not under `organization`), `storeId: "1126"` (Greer). Auto-match is guarded (`_catalog_price_passes_sanity_check`, `_catalog_match_is_plausible`); tests in `tests/test_catalog_match.py`.
- API gotchas: one mutation per request; `createCostItem` needs `costGroupId`, `costCodeId`, `costTypeId`; `updateCostGroup.lineItems` replaces the whole list; query `size` max 100; never hard-delete scope (no undo); job photos are on `location.files`, not `job.files`; there is no `costItem` file target (don't attach images to cost items); cost item `description` is client-visible unless `showDescription: false`.
- Historical data: `historical_home_and_closing_repairs_budget_detail_dedup.csv` (use this one). Its `cost_type` is unreliable; identify labor by item name "Hourly Rate".

## Closing-repair estimator (v2)
- Only the two closing-repair flows (sales-tool + Wufoo) use v2 (`estimate_v2_draft.py`). Home Repair, GVL, Remodel, Pre-listing and general sales-tool still use `call_claude_general()` + old `add_cost_groups()` with the flat "price" schema. Don't change the shared `SYSTEM_PROMPT`'s output schema or delete the old functions without moving those flows first.
- v2 prompt = shared prompt minus superseded rules (`_strip_v2_superseded_rules()`) + v2 sections. Company-wide policy (trade rules, out-of-scope, language rules) goes in the shared prompt; closing-repair formatting goes in v2 only.
- Both PDFs go to Claude as native PDFs (vision). Handle a missing addendum.
- Output: group by trade (6-14 groups), Title Case names, no report numbers in names, `Scope Includes:` / `NOTES:` description format, plus a "Not Included In This Estimate" group. Spec: `OCC_Estimate_Generation_Spec.md`.
- Each group gets a $0 `NOTES (internal - not shown on client documents)` item with `showDescription: false` (Jason's choice, overrides spec 4.2).
- No Home Depot notes or warnings on material items (Jason, Oct 20 2026). Catalog detail goes to the Render log only.
- Calibration: `consolidated_trade_group_calibration.csv` (append future hand-corrected jobs) is the most important source; plus 17 historical examples and the vendor CSVs.
- Language: no markdown, emoji or ALL CAPS (except `NOTES:`); spaced hyphen not em dash; "the client"; spell out Owners Choice Construction.
- Known open items: in-house labor hours and material quantities still run low vs. hand-corrected jobs; sizing guidance not yet re-tested live.

## Active projects (read the docs first)
- Site Scope Tool: `docs/site-scope/` (build plan is source of truth; update it in the same PR). Bigger picture: `docs/occ-brain/occ-brain-architecture.md`.
- Preliminary Ballpark Tool: `docs/ballpark/BALLPARK_TOOL_BUILD_SPEC.md` and `build-status.md`.
- New code in new files; `app.py` only gets small anchored changes to register endpoints. Never rewrite `app.py`.
- Render has 512 MB shared with the estimator and follow-ups: no headless browsers, each run in its own background thread with all errors caught. No plan upgrade.

## Working rules
- Start from current `main`. Work on a branch, test, open a PR, explain it in plain language.
- Claude may merge (here and in sales-lead-tool). Before merging: run `python3 -m unittest discover tests`, re-read the diff for estimator/follow-up risk, don't merge during Jason's live tests. After: tell Jason what changed and how to test. Call out estimator or follow-up changes plainly.
- Tell Jason at natural breakpoints when a fresh session would work better, with a short hand-off. Update docs first.
- History of past decisions and tests: `docs/estimator/history.md`.
