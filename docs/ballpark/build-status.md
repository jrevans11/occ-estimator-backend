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

## Not done / open

- **Not run live yet.** No end-to-end run against the real Claude API, CompanyCam or JobTread. Next step
  after deploy: a dry run on Malm from the page, then a live run on test job 00886.
- **Tier price table is a draft.** Several Luxury and Premium items have no published price (Waterworks,
  California Faucets, THG Paris, Visual Comfort Signature, Urban Electric, Apparatus, toilets). Tyler to review.
- Spec section 11 items still open: #51 template price check, the days/hours rules, countertop installed vs.
  material (default installed), client-facing tier language (tool drafts it).
- Template line prices are read live; if Tyler edits the template, the next run uses the new numbers.
