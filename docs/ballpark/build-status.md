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

More from the filled-form Malm run, biggest dollar effect first:
5. **Electrical with a full rewire** (tool +$17,850): Malm priced $9/SF General Electrical and left
   receptacles, switches, GFCI and dimmers at 0 - only lights were counted. Is the per-room device
   count supposed to be skipped when a full rewire is selected?
6. **Optional items** ($28,200 on Malm): Malm's total includes "(NOT INCLUDED)" options at full cost
   (crawlspace, chimney, firebox, fireplace surround). Should the tool add optional items, and should
   they count in the price range?
7. **Tile** (tool -$10,000): Malm used 16 x $1,200 "Ceramic Tile Installation" plus 5 x $2,200 "Other
   Bath Tile" and no tile material line. What were those units, and is tile material normally inside
   the labor numbers rather than a separate allowance?
8. **Construction management** (-$8,000): $900/wk (spec) vs $1,100/wk on Malm.
9. **Selection coordination** (-$7,500): 12/20/28 hrs by tier vs 80 hrs on Malm. Should a whole-home
   job get more hours?
10. **Interior paint** (-$7,100): $900/room incl. materials vs Malm's $1,100/room + $2,700 materials.
11. **Windows** (+$7,500): Premium $1,100 + $400 install vs Malm's $900 + $300.
12. **Gutters** (-$5,900): $2,500 placeholder vs Malm's 7 x $1,200. What is the unit (per run/side)?
13. **Insulation and interior trim materials** use the whole floor plan SF (3,200), like framing
    materials; Malm used 2,200. Same framed-area question as #3?
14. **Scope summary video:** on Malm the tool guessed the last video was the summary and got a basement
    walkthrough. Can Tyler say "scope summary" at the start of that video (or title it) so the tool
    finds it reliably?

## Next dry runs

Every past job's CompanyCam record is thin (Tyler's scope-summary videos started in Oct 2026): Senn,
McCarter and Strawderman have no videos; McNelly ~3.5 min, Taggart ~3 min, Greenway ~2.5 min. A
blank-form run on any past job will under-read the same way Malm did, so past jobs test the pricing
(with the form filled from the ballpark), not the read.

- McNelly (00845, $343,745 cost) and Taggart (00835, $312,968): smaller jobs in the 28% bracket, the
  most video of the past jobs. Run each twice once the tier fix is merged: job tier only, then filled.
- The real test of the read: the next site visit where Tyler records a scope summary video.

## McNelly and Taggart dry runs (Oct 28, 2026, after the tier/foundation fix)

| Job | Real cost | Job tier only | Form filled | Filled, framed areas* |
|---|---:|---:|---:|---:|
| McNelly (00845), Premium | $343,745 | $272,257 (-21%) | $436,824 (+27%) | ~$360,600 (+4.9%) |
| Taggart (00835), Quality | $312,968 | $296,819 (-5%) | $356,245 (+14%) | ~$307,500 (-1.7%) |

\* Recomputed with framing materials, insulation and interior trim materials on the SF Tyler actually
budgeted (McNelly 200 / 400 / 800, Taggart 400 / 250 / 400) instead of the whole floor plan SF.

What this shows:
- **Floor plan SF is the biggest error source.** Framing materials ($20), insulation ($4.50) and interior
  trim materials ($3.25) all multiply the whole renovated floor plan: $27.75/SF. On McNelly that's $74,850
  too much; on Taggart $47,850. With the areas Tyler used, both filled runs pass. Question 3/13 for Tyler
  is now the top question: these three lines need the framed / opened-up area, not the floor plan.
- **Electrical with a rewire double-counts** (question 5): McNelly got $27,000 rewire plus 124
  receptacles and 54 GFCIs from the per-room rule; Tyler's budget had the rewire and lights only.
- **Dumpsters and dust control:** Tyler's McNelly and Taggart budgets have 1 dumpster and 1 week of dust
  control; the spec rule gives 16-17 dumpsters and 32.5 weeks of dust control on Taggart (+$11,250).
  Malm used 20 and 14. New question for Tyler: is the per-2-weeks / per-week rule right?
- **Duration ranges are coarse:** Taggart's 26 weeks falls in "25-40", whose midpoint is 32.5 weeks
  (+$5,850 CM). A number field, or narrower ranges, would track Tyler's real durations better.
- **Tier-only Taggart passing (-5%) is luck:** it missed the siding and exterior paint (-$30,000) and
  over-counted framing materials (+$29,800) by about the same amount.
- **Blank-ish forms still under-read:** McNelly tier-only found almost no windows/doors (-$19,500) and
  no rewire. These jobs predate scope summary videos (~3 minutes of video each).

## Not done / open

- **No live write yet.** Dry runs work end to end (Claude ~$0.25 a run, ~90 seconds). Next: a live run on
  test job 00886.
- **Tier price table is a draft.** Several Luxury and Premium items have no published price (Waterworks,
  California Faucets, THG Paris, Visual Comfort Signature, Urban Electric, Apparatus, toilets). Tyler to review.
- Spec section 11 items still open: #51 template price check, the days/hours rules, countertop installed vs.
  material (default installed), client-facing tier language (tool drafts it).
- Template line prices are read live; if Tyler edits the template, the next run uses the new numbers.
