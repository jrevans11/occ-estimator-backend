# Preliminary Ballpark Tool - build status

Source of truth for decisions is `BALLPARK_TOOL_BUILD_SPEC.md` (this folder). This file records what was
built against it and what is still open. Update both in the same PR when a decision changes.

## Built (Oct 27, 2026)

| Piece | Where |
|---|---|
| Template loader + pricing engine (spec 5, 6, 7) | `ballpark_pricing.py` |
| Input gathering: CompanyCam videos/transcripts, photo descriptions, Pages; JobTread comments, Daily Logs, Rendr parameters | `ballpark_inputs.py` |
| Scope extraction (one Claude call, web search only for off-list brands) + tool-page merge | `ballpark_scope.py` |
| `POST /ballpark` (202 + background thread), `POST /ballpark/status`, writes, guards | `ballpark.py`, one routing branch in `app.py` |
| Tier price table (draft) | `ballpark_tier_prices.json` |
| Tool page `/intake/ballpark`, PM menu card, `/api/ballpark` + `/api/ballpark/status` | sales-lead-tool |
| Tests | `tests/test_ballpark_pricing.py`, `tests/test_ballpark_run.py` |

## Jason's answers to the spec's Recommended items (Oct 27, 2026)

- **Cost codes:** each line gets the code matching its phase group (`PHASE_COST_CODES`), not the template
  line's own code. 6200 Completion uses "Completion" (6000), because the org's 6200 code is Driveway.
- **Missing template lines:** the tool adds "Tile Materials (allowance)" under 5300 and "Mirrors" under 5400
  on each budget. The template itself is not edited.
- **Fixture / appliance / lighting prices:** cached tier table, not a live search per run. Claude only
  web-searches a specific brand named on site that isn't one of the tier's brands.

## Choices made where the spec was silent

- Open rules (demo / framing / trim days; finish plumbing / electrical / appliance-install hours): Claude
  estimates them from the scope and the reasoning goes in NOTES and the Daily Log. A missing estimate
  becomes a `$0 (PRICING TBD)` line.
- Permits (0.8%) and warranty (1%, min $1,200) are taken on cost before permits and warranty, including
  storage. Storage goes to 2 units when the job's total cost passes $390,625.
- Price range: when low and high round to the same number (small jobs), high goes up one step.
- Windows use `exterior.window_tier`, else the job's highest tier (flagged in NOTES).
- Exterior trim materials: the template prices per SF but the page collects LF, so LF is used for both
  lines (flagged in NOTES).
- Cabinet and Room Hardware: one per kitchen/bath with cabinetry.
- Weeks come only from Tyler's dropdown (range midpoint) or the spec default (15 / 32.5), never Claude.
- Budget writes use the proven per-item path (createCostGroup returning an id, then createCostItem per
  line) rather than nested `lineItems`.

## Malm (00844) check

Malm's own quantities (reverse-read from its budget, Premium tier) run through the engine:
**$738,950 cost vs $779,525 actual (-5.2%)**, within the spec's 10% pass line. This tests the rules,
not the Claude read of the visit; that needs a dry run on Render. Gaps worth Tyler's look:

| Phase | Tool | Malm | Why |
|---|---:|---:|---|
| 3100 Rough Structure | $73,000 | $106,000 | Framing labor at $300/day can't reach Malm's $62,000 lump without ~200 days |
| 1300 Preliminary | $21,800 | $35,000 | Demolition at $300/day vs Malm's $22,000 lump |
| 1000 Prep & Mgmt | $48,707 | $66,400 | CM $900/wk vs Malm's $1,100; selection 20 hrs vs 80 |
| 3700 Rough Electrical | $55,525 | $36,275 | Per-room device rule over 22 rooms runs higher than Malm |
| 5400 Cabinetry | $115,860 | $100,400 | LF guessed from Malm's lumps |
| 6100 Gutters | $2,500 | $8,400 | $2,500 placeholder per spec |

## First live dry runs on Malm (Oct 27-28, 2026)

| Run | Cost | vs $779,525 | Range |
|---|---:|---:|---|
| Blank form | $341,512 | -56% | $425K - $475K |
| Form filled the way Tyler would have | $727,459 | **-6.7% (pass)** | $875K - $975K |

The blank-form run was low because Malm's visit record is nearly empty: 3 CompanyCam videos of 53, 6
and 30 seconds (none of them a scope summary), no Pages, no photo descriptions, and one 4-line job
comment. The tool priced only what it could find and said so (confidence low, 17 open questions).
**The tool can only be as complete as the scope summary video or the form.**

Fixed after the blank-form run:
- No tier entered meant everything priced at Quality. The page now requires a **job tier**; rooms,
  tile, countertops and fixtures without their own tier take it, and a run with no tier is flagged.
- A second-story addition was charged a $42,000 footing. Footings now go on `foundation_sf` (new
  ground-floor footprint, 0 for a second story), falling back to `addition_sf` only when not given.

Still under Malm in the filled run, all from the rules rather than the read: framing labor and demo
at $300/day, CM at $900/wk vs Malm's $1,100, selection 20 hrs vs 80, gutters $2,500 vs $8,400.
Electrical (per-room device rule) and cabinetry run over.

## Waiting on Tyler: framing and demo labor (Oct 28, 2026)

Jason's read: framing is $300/day **per guy**, crews of 3-5 on large projects. The tool currently
prices Claude's day estimate as one guy (Malm: 25 days x $300 = $7,500). Per guy, Malm's filled-form
run would land -3.9% (3 guys) to -2.0% (5 guys); at $450/guy, -2.5% to +0.5%. No code change until
Tyler confirms.

From ~45 past Renovation/Addition budgets (JobTread, Oct 28):
- Framing Labor is almost always **$450/day** (Subcontractor), not the template's $300, x 6-90 days.
  Quantities that large (Frandsen 90, Panning 80, Rush 60) read as man-days, not crew-days.
- Demolition is $300/day x up to 80 (Frandsen), 40 (Rush, Parker, McNelly): also reads as per guy.
- Framing Materials is a separate line at $20/SF on almost every job, and its SF is the framed area
  (200-1,200 SF; Malm 2,200), **not** the whole renovated floor plan. The tool currently uses floor
  plan SF (Malm: 3,200 SF = $64,000 vs Malm's $44,000).
- Exceptions: Frandsen (00749) put materials inside its $40,500 labor line (materials line $20).
  Rama Addition (00518) has a $118,400 framing sub quote that includes materials, trusses, crane and
  steel, **and** a separate $52,000 Framing Materials line - looks double-counted.

Questions for Tyler:
1. Is $300 (template) or $450 (past budgets) the per-guy framing day rate?
2. Is demo $300 per guy per day too? Typical crew size for demo?
3. Should framing materials SF be the framed / added area rather than the whole renovated floor plan?
4. Is Malm's $62,000 framing labor only (materials in the separate $44,000 line)?

## Not done / open

- **No live write yet.** Dry runs work end to end (Claude ~$0.25 a run, ~90 seconds). Next: a live run on
  test job 00886.
- **Tier price table is a draft.** Several Luxury and Premium items have no published price (Waterworks,
  California Faucets, THG Paris, Visual Comfort Signature, Urban Electric, Apparatus, toilets). Tyler to review.
- Spec section 11 items still open: #51 template price check, the days/hours rules, countertop installed vs.
  material (default installed), client-facing tier language (tool drafts it).
- Template line prices are read live; if Tyler edits the template, the next run uses the new numbers.
