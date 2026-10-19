# OCC Site Visit Write-Up Process

How to turn a CompanyCam site visit into a written scope, drawings, and a JobTread budget. Proven on 7 Monet Dr. (Oct 2026) and the Test (1) run (Oct 19, 2026). Floor plan and elevation rules live in `floor-plan-drawing-rules.md`. Budget formatting, pricing and API rules live in the `jobtread-budget-builder` skill (and in this repo's CLAUDE.md: Estimate Generation Spec, markups, internal NOTES items). The tool being built to automate this is described in `site-scope-tool-build-plan.md`.

> **Oct 19 update:** the main input is moving from walkthrough-video transcripts to notes recorded in the scope page (audio transcribed on the server). Wherever this doc says "video transcripts", read "dictated notes, plus any video transcript that is already ready".

## Trigger
Jason says something like "write up the site visit for [address / job #]." In the built tool: the Start button on `/intake/scope`.

## Steps
1. **Find the project** in CompanyCam. Preferred link: JobTread `job.companycamId` holds the CompanyCam project ID (see sales-lead-tool `lib/companycam.ts`). Fallback: search by address or job number. Watch for duplicate projects at the same address - e.g., a JobTread-synced "Job 00XXX" project plus a manually created address project. Use the one with the photos/videos and mention the duplicate.
2. **Pull everything from the visit:**
   - Dictated notes (primary) and walkthrough video transcripts if ready (`get_video` with `include=transcript`). Jason narrates scope trade by trade, often addressed to a specific sub.
   - Photos - view them and check annotations.
   - Existing Pages (CompanyCam's own AI Walkthrough Summary) - read but don't trust over the notes/videos; it is a summary, not word-for-word.
   - Checklists, comments, project notepad if present.
3. **Write a description on every photo** (batches of up to 50): area, what's in the shot, what's changing, any measurement tied to it, and "not in scope" where relevant. Append to any description Jason wrote himself - never overwrite it.
4. **Draw the floor plan and elevations** when walls are dictated clockwise, per `floor-plan-drawing-rules.md`. Elevations only for walls with something on them. Every height labeled dictated / (est.) / (assumed).
5. **Create a scope Page on the project** titled "[Scope type] - Draft from [date] Site Visit", containing:
   - Short italic note on what sources it was built from.
   - Client goals.
   - Scope by trade (demo/framing, tile, plumbing, electrical, paint/drywall, countertops, glass, etc.), naming the sub when Jason addressed one.
   - Optional alternates priced separately.
   - **Measurement conflicts table** (source A vs. source B vs. action).
   - **Working quantities table** (sf, LF, etc.) with the basis for each and its source label (dictated / est. / assumed) - planning numbers, not ordering numbers.
   - **Missing info before bidding** checklist (task list).
   - Key photos section.
   - Also: append the word-for-word notes to the project's single "Site Visit Notes" Page (date/time heading per note). Nothing goes to the JobTread daily log.
6. **Ask one round of cost-driver questions** (3-6, each with a suggested answer): the conflicts, the gaps, and any missing cost driver that can't be inferred. Ask once, then build. Note that CompanyCam creates Pages with sharing on by default - drafts should have it turned off.
7. **Build the JobTread budget** from the scope Page, following the budget rules. Query the job fresh first - Jason may already have groups on it.

## Scope Page to JobTread budget mapping
| Scope Page / drawings | Becomes in JobTread |
|---|---|
| Each trade in "Scope by trade" | A cost group with a plain trade or area name (no markdown, under 60 characters) |
| Scope lines under a trade | Verb-first lines under "Scope Includes:", in build sequence (demo, structure, rough-in, finish, cleanup) |
| Sub Jason addressed | Subcontractor line items (cost x 1.45); multi-item work for one sub bundled as one visit or day rate |
| Materials | Material line items (cost x 1.65), checked for case/carton pricing |
| In-house work | Labor lines at $55/hr cost, $89/hr price |
| Optional alternates | Options parent group with child "Option 1 - ..." groups (min 0 / max 1 for add-ons, min 1 / max 1 for either/or); each option states its own tradeoff |
| Working quantities table | Line item quantities |
| "Not in scope" items from photo descriptions and notes | Exclusions in that group's client-facing NOTES |
| Measurement conflicts, (est.) and (assumed) quantities, missing info | The group's internal NOTES item ($0, "NOTES (internal - not shown on client documents)", showDescription false) so Jason sees them on review |
| Floor plan, elevations, scope Page | Referenced in the internal NOTES item with the CompanyCam Page title, so the budget traces back to its source |

## Quantities from the drawings
The drawings exist partly to produce budget quantities. Each quantity keeps its weakest source label - if any input is (est.) or (assumed), the quantity is too.
| Quantity | Derived from |
|---|---|
| Floor area (flooring, subfloor, floor tile) | Floor plan - room dimensions |
| Wall tile area | Elevations - tiled wall width x tile height, minus openings |
| Wall paint/drywall area | Plan perimeter x ceiling height, minus openings |
| Ceiling area | Floor plan |
| Base trim / shoe LF | Plan perimeter minus door openings |
| Casing LF | Door and window sizes from plan and elevations |
| Crown / chair rail LF | Plan perimeter (minus cabinet runs where they stop) |
| Backsplash area | Elevations - counter run x backsplash height |
| Countertop sf / LF | Plan - cabinet runs and depths |
| Deck surface / railing LF | Deck plan - outline and railed edges |

## Rules
- Spoken/dictated measurements are the source of truth for numbers; photos confirm materials, conditions, and layout.
- Never guess between conflicting numbers - flag them.
- Never silently "correct" a number that looks misheard in a transcript - ask.
- Drawn LiDAR measurements are optional and not part of the workflow.
- Mark anything assumed as "(assumed)"; photo-estimated heights as "(est.)".
- Floor plans and elevations (2D only, feet-and-inches) are drawn when walls are dictated clockwise per `floor-plan-drawing-rules.md`.
- Never invent prices for licensed trade work (electrical, plumbing, HVAC, structural) - use $0 lines named "(PRICING TBD)" unless a sub bid or rate anchor exists (rate anchors: the vendor invoice history CSVs in this repo).
- Quantities that rest on (est.) or (assumed) numbers are flagged in the internal NOTES item and in the question round before the budget goes to a client.
