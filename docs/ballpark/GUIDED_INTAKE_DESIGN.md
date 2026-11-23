# Ballpark Tool: Guided Intake (design, approved Nov 23 2026)

Status: **built Nov 23, 2026** (see "As built" at the end and `build-status.md`). Not yet tried on a live
job. The old one-page form still works at `/intake/ballpark/form`.

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

## As built (Nov 23, 2026)

Backend (no `app.py` change: the existing `/ballpark*` routing branch covers the new paths):

- `ballpark_read.py`: `POST /ballpark/read` {job, notes?} (202, background thread) and
  `POST /ballpark/read/status` {job}. Saved per job in `scope_store` under `ballpark:read:<job id>`.
  The status reply carries what was read (videos, scope summary found, photos, CompanyCam Pages,
  comments, Daily Logs, warnings) and, once done, the pages.
- `ballpark_questions.py`: the question catalog. Every question is a dot path into the scope
  (`exterior.trim_lf`, `rooms.1.tier`, `estimates.demo_days.value`). Groups: **ask** (always-asked
  judgment calls, every tool estimate, every conflict, core fields nobody mentioned), **found** (stated,
  with source), **more** (optional fields nobody mentioned). Pages: Job and timing; one per kitchen /
  bath / powder room with its tile area, countertop and fixtures; Other rooms; Interior finishes;
  Exterior; Structure and demo; Mechanical and plumbing. Interior, Exterior, Structure and Mechanical
  pages are always returned with `found`; a page the read saw no work for is offered on the review step
  as "add work the read didn't find".
- `ballpark_scope.py`: the read now returns `field_status` (stated / estimated / not_mentioned /
  conflict, same dot paths as `sources`) and a job `tier`. A value with no status is treated as a tool
  estimate, so it gets asked.
- `POST /ballpark` with `from_read: true, read_started_at, answers {path: value}, remove_rooms, dry_run`:
  applies the answers to the saved read (`confirmed_scope`) and prices it with no Claude call. Only
  questions on the pages can be answered; bad values are refused before anything starts. A tier change
  carries to the rooms, tile areas, countertops and fixtures that followed the old tier (not ones set by
  hand). Removing a space takes its tile area, countertop and fixtures with it (plumbing counts stay).
  Answers given are listed in the [BALLPARK] Daily Log review section ("Set on the guided intake").
  `read_started_at` must match the newest read, so answers never land on a newer read.

Tool page (sales-lead-tool): `app/intake/ballpark/page.tsx` is the guided flow (Start, pages with a
progress bar and Back/Next, Review with Notes / You / Tool / Default tags, dry run on by default, result
with `BudgetLines`). Answers are remembered on the phone per job and read (browser storage), so Tyler
can leave and come back. The old form moved to `app/intake/ballpark/form/page.tsx`. New API routes
`/api/ballpark/read` and `/api/ballpark/read/status`.

Not done: adding a missing kitchen or bath on the pages (add it to the notes and read again); per-room
vanity single/double (waits on the vanity rule); room SF is not asked (it doesn't affect price).
