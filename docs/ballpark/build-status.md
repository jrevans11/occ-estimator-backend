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

## Case study: 20 real ballparks (Nov 17, 2026)

`case-studies/pricing-patterns.md` compares every priced line on the 20 most recent Preliminary
Ballparks (data in `case-studies/ballpark_line_items.csv`) with the tool's rules. About 20 rules differ
from Tyler's consistent practice: framing $450/day, footings on perimeter LF not SF, $9/SF General
Electrical on almost every job, $1,200 per plumbing fixture, $1,100/room paint + $450 materials, tile
$5,700 / $2,200 / $1,800 + ~5 ceramic units per primary bath, window install $300, gutters $1,200/unit.
Markup was 28% on 14 of 20 regardless of size.

## Tyler's practice adopted as the rules (Nov 17, 2026)

Jason: "use what Tyler does, then tweak it if needed." `ballpark_pricing.py` now follows the patterns
in `case-studies/pricing-patterns.md`:

- Rates: framing $450/day, window install $300, windows $600 / $900 / $1,800 by tier, hardwood
  finishing $3.75/SF, paint $1,100/room + one $450 Paint Materials per job (1 per 6 rooms on 20+ room
  jobs), tile $5,700 primary / $2,200 hall / $1,800 backsplash, countertops $105 / $125 / $237.50.
- Quantities: footing $42 x LF of new perimeter (estimated as 3 x sqrt(footprint) when not given);
  framing materials and insulation on the framed/opened area (`framed_sf`, else the addition SF, else
  25% of the renovated area with a review flag); General Electrical $9/SF of renovated area on every
  job; devices at Tyler's token counts; recessed 1.5 per room; General Plumbing $1,200 per fixture
  (count given, else by room: primary bath 4, hall bath 3, powder 2, kitchen 2) and finish plumbing
  3 hrs per fixture; finish electrical 1.5 hrs per room (min 4); trim 1 day and 50 SF of materials per
  room; drywall 300 SF per room with materials 1.35 x hang; Ceramic Tile Installation units (5 per
  primary bath, 3 per hall bath, 1 per small area) and no tile material line; gutters $1,200 per unit
  (count given, else 6 whole house / 2 partial); bathroom hardware one line, $950 + $650 per extra bath;
  cabinet hardware $450 without a kitchen, $2,500 / $3,000 / $3,750 with one; no Cabinet Installation
  or Cabinet Painting lines (Tyler's $625/LF includes them).
- Sized by job cost: dumpsters 1 per 2.5 weeks, dust 60% of the weeks, permits $800 x 1-9, selection
  hours (tier under $160K, then 32 / 40 / 60, Luxury 80 over $550K), storage none under $160K, punch
  days 1 / 2 / 3 / 5 when not entered, cleaning x2 over $390,625. Warranty unchanged.
- Scope prompt asks Claude for `framed_sf`, `foundation_perimeter_lf`, a gutter count and
  `plumbing.fixture_count`; trim days and finish hours are no longer Claude estimates.
- **Not changed: the markup bracket.** Tyler used 28% on 14 of 20 jobs at every size (Barber, $656K
  cost, on Nov 16). Whether the 28 / 25 / 23% bracket stays is Jason's call.

Checked by rebuilding each job's scope from Tyler's own budget quantities (demo and framing days taken
from his budget, since those are still Claude estimates) and pricing it with the old and new rules:

| Job | Real cost | Old rules | New rules |
|---|---|---|---|
| McNelly (00845) | $343,745 | +12.9% | **+1.4%** |
| Taggart (00835) | $312,968 | +13.5% | **+3.3%** |
| Malm (00844) | $751,325 (without $28,200 of NOT INCLUDED options) | - | **-3.3%** |

Biggest remaining gaps: dumpster and dust on McNelly/Taggart (Tyler entered 1 each; the rule gives
~$5,000 and ~$1,500), Taggart's General Electrical (Tyler priced 600 SF; the rule uses the 2,100 SF
renovated area), and Malm's CM at $1,100/wk vs the $900 rule. This tests the pricing, not Claude's
read of a visit; a live dry run is still the real check.

## Gentles (00895) live dry run on the new rules (Nov 17, 2026)

First live dry run after PR #36, through the intake page's dry-run checkbox (nothing written). Form
filled only with what Tyler knows on site: Premium, not whole-home, 15-20 wks, a primary bath, room
count 3, deck 300 SF. The tool read 4 CompanyCam transcripts, 4 photo descriptions and 1 comment
(confidence medium).

| | Cost |
|---|---|
| Tyler's real ballpark | $131,139 |
| Tool | $118,563 (**-9.6%**, pass) |
| Tyler's, less lines the tool never adds ($11,825: lot survey, preconstruction, site work, grading, under-deck waterproofing) | $119,314 (tool -0.6%) |

Price $151,762 at 28%, range $150K - $170K (Tyler's ~$168K sits at the top). The read got the scope
right (spare bedroom to primary bath + closet; deck rebuilt as a covered carport with a concrete pad),
estimated framing at 12 days (Tyler: 12) and raised the right open questions (sewer easement / survey,
deck material, under-deck drainage, drain route). Biggest miss: demo 4 days vs Tyler's 16 (~$3,600);
demo days are still a Claude estimate. Recurring gap: survey, preconstruction, site work and
waterproofing lines the spec removed - worth asking Tyler whether the tool should add them when the
scope calls for them (pricing-patterns.md question 4).

## Four more live dry runs (Nov 17, 2026)

Same method as Gentles: intake page, dry run, form filled only with what Tyler knows on site (tier,
whole-home, duration, spaces, room count, plus addition/slab SF on Greenway). The jobs were chosen
because they have the most CompanyCam video.

| Job | Form | Tyler's cost | Tool cost | Diff | Confidence |
|---|---|---:|---:|---:|---|
| Gentles (00895) | Premium, 15-20 wks, primary bath, 3 rooms, deck 300 | $131,139 | $118,563 | -9.6% | medium |
| Barber (00892) | Premium, 40-52 wks, kitchen, primary + 4 hall baths, 22 rooms | $655,952 | $629,448 | -4.0% | medium |
| McNelly (00845) | Premium, 20-25 wks, kitchen, primary + 3 hall baths, 15 rooms | $343,745 | $308,837 | -10.2% | low |
| Greenway (00822) | Quality, 20-25 wks, 8 rooms, 750 SF addition/foundation/slab | $298,728 | $236,035 | -21.0% | low |
| Taggart (00835) | Quality, 20-25 wks, kitchen, 14 rooms | $312,968 | $207,452 | -33.7% | low |

Every run came in low. The read itself was good (it understood each job's scope and asked sensible
open questions); the misses are things it left at 0 or excluded. Holes, biggest first:

1. **Blank read as "none".** The prompt says the tool page is final, and Claude also treats things
   Tyler didn't enter as not in scope. Greenway: the detached garage, breezeway and driveway in the
   scope video were dropped because the form only listed the addition (Tyler had driveway $12.5K,
   garage door $4.3K). McNelly: the powder room on the video was folded into a hall bath. Fix: the
   form is final for what it says; blank fields mean "work it out", not "none".
2. **Quantities left at 0 instead of estimated.** Taggart LVP 0 (Tyler 2,100 SF, ~$17.9K), exterior
   trim 0; McNelly windows 0 (Tyler 10, ~$9K). When the work is clearly in scope but unmeasured,
   Claude should estimate and flag it, not zero it.
3. **Exterior paint.** The engine only knows a full repaint priced on heated SF. Tyler uses $1,200
   units for partial work (Greenway 13 units, $15.6K) or a lump with new siding (Taggart $19.8K).
   Barber's read excluded exterior paint (Tyler $34K). Needs a partial-paint input in units.
4. **Demo days run low** (Gentles 4 vs 16, McNelly 10 vs 40, Greenway 5 vs 14). Tyler's demo days
   read as man-days (crew of 3-4); Claude estimates crew-days. The prompt should say man-days and give
   Tyler's ranges, as it now does for framing.
5. **Custom built-ins / extra cabinetry** not picked up as lumps (Greenway $28K, Taggart $16.5K).
6. **Duration units:** Claude asked twice whether "20-25" meant weeks. Label it weeks in the prompt.
7. **Windows on big jobs:** Barber read 47 windows x $900 = $42K; Tyler carried an $85K lump.
8. **Lines the spec removed** (preconstruction, survey, site work, grading, driveway/paths) keep
   showing up in Tyler's budgets (Greenway $6.2K precon + $12.5K concrete, Gentles $11.8K).
9. **Markup:** Barber priced at 23% (bracket) vs Tyler's 28% - $774K vs ~$840K price. Bracket kept
   for now per Jason.

### Holes 1-6 fixed (Nov 17, 2026)

- Scope prompt: Tyler's entered values are final, but a blank field means "work it out", and spaces
  the visit clearly shows (powder room, detached garage, breezeway, driveway, porch) are added and
  noted in conflicts. In-scope work with no stated quantity is estimated and flagged, never left at 0.
  Demo days are man-days with Tyler's ranges (3-5 small, 8-16 kitchen/bath/small addition, ~40
  whole home, 80 largest). duration_range is labelled weeks, in the prompt and in the form text
  Claude sees. Built-ins: estimate `cabinetry.builtins_lf` whenever any are mentioned.
- Pricing: new `exterior.paint_units` for partial exterior paint at $1,200 a unit (full repaint on
  heated SF still wins when both are set). Exterior paint discussed on site counts as in scope
  unless it was explicitly excluded.
- Holes 7-9 (big-job windows, removed spec lines, markup) still need a decision.

## Not done / open

- **No live write yet.** Dry runs work end to end (Claude ~$0.25 a run, ~90 seconds). Next: a live run on
  test job 00886.
- **Tier price table is a draft.** Several Luxury and Premium items have no published price (Waterworks,
  California Faucets, THG Paris, Visual Comfort Signature, Urban Electric, Apparatus, toilets). Tyler to review.
- Spec section 11 items still open: #51 template price check, the days/hours rules, countertop installed vs.
  material (default installed), client-facing tier language (tool drafts it).
- Template line prices are read live; if Tyler edits the template, the next run uses the new numbers.
