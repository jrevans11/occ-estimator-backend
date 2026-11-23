# Ballpark Tool: Guided Intake (design, approved Nov 23 2026)

Status: designed, not built. Build in a fresh session. New code in new files; `app.py` gets only the
anchored endpoint registration. Update `build-status.md` in the same PR.

## Why

Today the tool page is one long form (sizes, exterior, doors, flooring, cabinets, closets, mechanical,
plumbing). Tyler fills what he can, submits, and Claude fills the rest from the site visit in the same
run. Two problems:

1. The form is overwhelming, and most of it is already in Tyler's dictation (scope summary video,
   walkthroughs, notes).
2. Claude's guesses are invisible until the budget comes back. On Wharton 00903 most of the $52K gap
   vs Tyler came from unseen guesses: trim LF (720 vs 300), heated SF (3,000, drives exterior paint),
   demo days (16, incl. 6 siding tear-off days), framing days and SF, plus punch days and weeks.

## Flow

1. **Start.** Pick the job. Optional dictation box ("anything to add"). Button: "Read the site visit".
   Runs in a background thread (2-3 min). The result is saved per job, so Tyler can leave and come back.
   The page shows what was read (videos, photos, notes, comments).
2. **Pages**, only for work found in the visit (a bath job never sees roof or driveway questions):
   - Job and timing: tier, duration, punch days, whole-home (always asked; judgment calls).
   - One page per space: kitchen, each bath, other rooms (tier, vanity single/double, shower type,
     tub, tile, cabinets LF, countertop SF, fixture moves, flooring, closets, doors).
   - Exterior (siding squares, trim LF, heated SF / paint, windows, doors, roof, gutters).
   - Structure and demo (framing SF and days, demo days, addition, foundation), showing the tool's
     estimate and its reasoning.
   - Mechanical (rewire, HVAC, water heater, crawlspace).
3. **Each page has two parts:**
   - **Needs your answer** (yellow): fields the notes don't cover, conflicts between sources, and every
     tool estimate, with a one-line reason ("Guessed 720 from 60 siding squares").
   - **From your notes** (collapsed): values found, each with its source (e.g. "scope summary 0:42",
     "comment 11/14"). Tap to change.
   - A page with nothing to answer says so and moves on with one tap.
4. **Review.** Every number, tagged notes / you / tool. Build (or dry run).

## Backend (occ-estimator-backend)

- New `POST /ballpark/read` {job, notes?}: background thread runs `ballpark_inputs.gather` +
  `ballpark_scope.extract` with an empty form, stores the reply (scope, sources, conflicts,
  open_questions, estimates with reasoning, summary) as the job's "read" record. Status via
  `/ballpark/status` (or a `/ballpark/read/status`).
- Each scope field needs a status: `stated` / `estimated` / `not_mentioned`. Add an explicit
  `field_status` map to the prompt's output schema (don't infer from the `sources` text).
- New file for the question catalog: scope field -> page, label, input type, and when it is shown.
  The page list comes from what the read found (rooms by type, exterior work, structure, mechanical).
- `POST /ballpark` takes the confirmed scope and prices it directly (`price_scope`) plus the summary
  from the read. **No second Claude call**: what Tyler confirms is exactly what gets priced. Claude cost
  per job stays about the same (~$0.22). Same JobTread writes as today; dry run unchanged.
- Keep the current form-based `POST /ballpark` path working until the new flow is tested.
- 512 MB Render limit: one background thread per read, all errors caught, no headless browser.

## Tool page (sales-lead-tool `app/intake/ballpark/`)

- Step-by-step pages with a progress bar, Back/Next, sized for a phone.
- Keep `BudgetLines.tsx` (dry-run line table) on the result screen.
- Mockup (Nov 23 session): Start, Primary bath page, Exterior page, Review page.

## Not part of this build

- The pricing-rule changes from the Wharton review (see build-status "Wharton pricing fixes waiting
  for Jason"). They are separate and need Jason's approval first.
