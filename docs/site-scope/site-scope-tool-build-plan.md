# OCC Site Scope Tool - Build Plan

Planning notes from Oct 18-19, 2026. Status: **phases 1-4 of 8 built and verified live (Oct 19-20). Next: the page fixes listed under "Phase 4 live test", then phase 5.** Companion docs in this folder: `site-visit-writeup-process.md` (what the tool does, step by step), `floor-plan-drawing-rules.md` (drawing rules) and `room-file-format.md` (the structured room file the drawings and later phases work from). `prototype_drawings.py` is the throwaway script that drew the Oct 19 test drawings - a geometry reference only (it renders through a headless browser, which is too heavy for Render; see "Where the code lives").

**This repo copy is the source of truth.** When a build session changes a decision, update this file in the same pull request. (Older copies exist in Jason's claude.ai project "Lidar scaning app" and will go stale.)

## Goal
Finish info-taking on site, trigger the analysis right there, get follow-up questions while still on site, answer quickly, and have everything else finished automatically: Site Visit Notes Page, photo descriptions, floor plan + elevations, CompanyCam scope Page, JobTread budget. As accurate as possible, as efficient as possible.

## Typical job size
- 10-20 photos per site visit, a few dictated notes (videos optional)
- 3-5 site visits per week (about 13-22 a month)

## Where the code lives (decided Oct 19)
- **Backend: this repo** (jrevans11/occ-estimator-backend, Render service occ-estimator) - not a new repo or a second Render service.
  - Reuses what is already here: JobTread helpers, vendor invoice-history CSVs (rate anchors), markup rules, 3x case-price check, background-thread pattern from `/send-followups`. No extra Render cost and no free-tier sleep delay when Start is tapped on site.
  - **New code goes in new files** - e.g. `site_scope.py` (run logic: fetch media, AI calls, questions, build) and `scope_drawings.py` (drawings). `app.py` only gets the few lines needed to register the new endpoints.
  - **Memory:** the service has 512 MB shared with the estimator and follow-ups. Render drawings with a lightweight Python library (e.g. Pillow, or pure-Python SVG-to-PNG if it installs cleanly on Render) - not a headless browser.
  - **Isolation:** each scope run runs in its own background thread with errors caught and logged, so a scope failure cannot take down follow-ups or the estimator.
  - Split into its own repo/service later only if it outgrows the shared memory or needs to change independently of the live estimator.
- **Frontend: jrevans11/sales-lead-tool** (Next.js 14 on Vercel), page at `app/intake/scope/page.tsx`, API routes under `app/api/`. See that repo's CLAUDE.md.

## Facts found in the repos (Oct 19)
- **Job -> CompanyCam project link:** JobTread `job.companycamId` holds the CompanyCam project ID (JobTread auto-creates the project a few seconds after a job is created). sales-lead-tool `lib/companycam.ts` already reads it (`waitForCompanycamProjectId`) and pushes photos/documents to CompanyCam with `COMPANYCAM_API_KEY` (set in Vercel). Use this instead of name/address matching.
- `components/VoiceMemo.tsx` in sales-lead-tool is an empty stub (returns null) - the recorder is new work.
- sales-lead-tool already ships a `manifest.json` and its README has Jason add it to the iPhone Home Screen - the prerequisite for web push on iPhone.
- Photos are compressed client-side and stored in Vercel Blob (`BLOB_READ_WRITE_TOKEN`) - reuse for note audio.
- Job photos in JobTread live on the `location` record, not the job.

## Capture method (decided Oct 19)
- **Photos in CompanyCam** - a wide shot of each room plus one straight-on shot of each wall that has something on it.
- **Dictated notes in the scope page, not video transcripts.** Jason talks the way he would in a walkthrough video. Any number of notes per visit (one per room or per scope). No 10-20 minute wait for video uploads and transcripts (Oct 19 test: a 2-minute video recorded 12:21 finished uploading 12:29, transcript ~1 minute later).
- **Videos are optional** - for Jason's own reference. The tool uses a video transcript only if one is already ready when he submits; it never waits on uploads. Skip videos under ~5 seconds (accidental taps).
- **Notes match photos by time:** each note records when it was started and saved; photos carry `captured_at`. Natural pattern: shoot a room's photos, switch to the tool, dictate that room, repeat.
- **Speech-to-text: record audio in the page, transcribe on the server** (Jason finds iPhone keyboard dictation mishears him). Big Record / Stop button per note; audio uploaded (Vercel Blob) and sent to a transcription API; the text shows on the page a few seconds after Stop so Jason can glance at it and fix a number.
  - **Service: Deepgram Nova-3 (decided Oct 20, Jason).** Vendor pages checked Oct 20: Deepgram Nova-3 $0.0043/min + $0.0013/min keyterm hints = $0.0056/min, $200 free credit that never expires; AssemblyAI Universal-3.5 Pro $0.21/hr + $0.05/hr keyterms = ~$0.0043/min, $50 free credit (expiry not stated). At ~200 min/month both cost ~$1/month, so the credit decides it: Deepgram's lasts ~15 years, AssemblyAI's ~5. Deepgram also returns the text in one call (AssemblyAI needs submit-then-poll). **AssemblyAI is the backup** - switching means rewriting only `scope_transcribe.py`. Accuracy on a real jobsite note is still to be checked in the phase 3 phone test.
  - ~10 min of notes per visit x ~20 visits/month = ~200 min/month = roughly $1/month.
  - Feed the service a list of OCC trade words (CMU, wainscot, LVP, soffit, fascia, sill, header, lintel, GFCI, etc.) where it supports keyword/vocabulary hints.
  - Keep the audio file so a questionable number can be replayed.
  - The AI never silently "corrects" a number it thinks was misheard - it asks.
  - Claude cannot take audio directly, so a speech-to-text service is required. Self-hosting Whisper does not fit the 512 MB Render plan.
- Notes save as they are recorded (not only on submit) so a closed page or dead signal loses nothing.
- **Notes record: one CompanyCam Page per project, "Site Visit Notes"** - word-for-word notes under a date/time heading per note; later visits append to the same Page. Word-for-word rather than a summary (the scope Page is the summary). **Nothing goes to the JobTread daily log** (Jason: adds noise).
- Narration habits from the Oct 19 test: say wall numbers ("Wall 3") instead of descriptions, give every wall a length, give "starts X from the left corner" for each opening/fixture.

## Trigger and question delivery (decided Oct 19)
- **Trigger: the "Scope this visit" page** `/intake/scope`. The same page holds the dictated notes, so taking notes and starting the run are one place.
  - Reuses the existing job picker with nearest-first proximity sorting.
  - Before starting, the page shows what it found: notes, CompanyCam photo count, any videos and whether their transcripts are ready. Then Start.
  - Vercel only forwards the request; the long-running work runs on Render in a background thread (Vercel functions time out).
- **Questions:** the page stays live and shows the questions when ready (~1 minute). If Jason has left, a **web push notification** ("Questions ready - <address>") opens the page. iPhone push requires the Home Screen app and notifications allowed once. Fallback: text message with the link (RingCentral SMS API - not yet checked whether OCC's number can send).
- Run state (status, questions, answers, room file) stored in **Upstash Redis (free tier, decided Oct 20 for phase 4)** so questions wait if the page is closed or the server restarts. Render writes and reads it; Vercel doesn't need it. In phase 3 the audio lives in Vercel Blob and the text on the CompanyCam Page.
- **CompanyCam tag trigger: deferred.** It would fire before uploads finish, has nowhere to show status or answer questions, and the "Project Labeled" webhook is unverified. Can be added later against the same backend endpoint.

## Flow (questions first)
1. Quick pass: read notes (plus any ready transcripts) + photos, turn them into one structured data file (rooms, walls, openings, heights, trim, scope items, subs), and write 3-6 cost-driver questions with suggested answers.
2. Questions show on the page (tap answers, or "go" to accept all defaults). They wait on the job if the page is closed.
3. Full build after answers: Site Visit Notes Page, photo descriptions, drawings (uploaded to CompanyCam as project photos and embedded in the scope Page), scope Page, JobTread budget. Run marked done.
4. Anything not asked is built with (est.) / (assumed) labels and flagged in internal NOTES.

## Drawing detail
- Drawings are rendered by code from the structured data, so any element in the data can be drawn: baseboard, door/window casing, crown, wainscot/chair rail (with panel or board-and-batten spacing), cabinets (base/uppers, door divisions, countertop, backsplash, appliance gaps).
- Profiles/styles are labels, not drawn shapes. Detail comes from dictation; items only visible in photos are drawn as (est.). Nothing invisible is invented.
- Trim lengths (base, crown, casing, chair rail) come off the drawings as budget quantities.

## AI vs. code split
- **AI does judgment only:** reading notes/transcripts/photos into structured data, choosing questions, writing scope lines and NOTES, grouping the budget.
- **Code does math and rules:** drawings from the structured data (exact geometry, automatic closure check), quantities (sf, LF, openings subtracted), markups (1.45x sub, 1.65x material, $55/$89 labor), rate lookups from the vendor invoice-history CSVs, and the existing checks (3x case-pricing sanity check, duplicate scope check).
- Read the media once; every later step works from the structured file.
- Cache the rules docs in the prompt.
- Send photos at full resolution.

## Cost estimate (Claude API, Oct 2026 pricing)
- Sonnet 5.5: $2 / $10 per million tokens (input/output). Opus 5.5: $4 / $20. Cache hits on both are 0.05x input price.
- Photos: Opus 5.5 is on the high-resolution image tier - a full-size phone photo is downscaled to ~1932 px square, ~4,800 input tokens (~$0.02 each on Opus). Resizing to ~1,100 px cuts that to ~1,500 tokens at some cost to small details.
- Two calls per visit (quick pass + build). Test (1) (9 photos + short transcript): ~$0.55-0.70 on Opus, ~$0.30-0.35 on Sonnet. A 20-photo visit ~$1.00-1.20 on Opus. Per month at 13-22 visits: ~$8-25 Opus, ~$4-12 Sonnet.
- **Model: Opus 5.5 (decided Oct 20, Jason)** - reading photos + talk into exact wall lengths and opening positions is the hardest step, and mistakes carry into drawings and budget. Effort `high` (Opus 5.5's default is medium). Render env `SCOPE_MODEL` / `SCOPE_EFFORT` switch it without a code change (e.g. `claude-sonnet-5-5`).
- Speech-to-text: ~$0.50-1.50/month, covered by free credits.
- Log token count and cost per run so real numbers replace estimates.

## Accuracy testing before live use
- Run the tool on 3-5 past site visits where Jason already built the final budget (candidates: 901 McDaniel / Job 00768, 7 Monet Dr). Compare line by line with what was sent; tune the rules until close (same approach as 102 Tuscany Way for the estimator).
- Jason expects a few practice runs or real jobs to settle how to talk and how much detail to dictate.

## Test run - Oct 19, 2026 (CompanyCam project "Test (1)", Jason's office, 3140 Wade Hampton Blvd)
- 9 photos then 1 video, run through the interim chat flow. Capture order worked; photos tied to walls cleanly by time and content.
- Transcript was word-for-word (source "mux_generated") but slow to arrive (see Capture method).
- Photos supplied what dictation didn't: wall construction (CMU block vs drywall), door swing, an HVAC chase and whiteboard in the way of the new opening, a two-sash window. 8" block courses / 16" block lengths worked as a scale reference.
- Question round caught: a wall length not dictated, opening offsets not dictated, one ambiguous wall reference. Jason answered: Wall 4 = 13', the new opening gets a door and will clear the HVAC chase, same-size window, Wall 3 outlet surface mount.
- Output: 9 photo descriptions, floor plan + 4 elevations, scope Page "Office Remodel Scope - Draft from Oct 19, 2026 Site Visit", budget on stand-in JobTread job 00035 "Test job- JT" (6 groups + Project Management; $3,549 cost / $5,803.35 price with masonry and electrical at $0 PRICING TBD; non-sub prices were estimates).
- Drawing upload to CompanyCam failed from the chat workspace (S3 upload host blocked there) - not expected to be a problem from Render; verify.

## To verify before/while building
1. CompanyCam API token available to the backend (`COMPANYCAM_API_KEY` exists in Vercel; add to Render, or have Vercel make the CompanyCam calls).
2. Photo upload works from Render, so drawings land in CompanyCam. **Method decided Oct 19 (phase 2):** Render serves each drawing briefly at an unguessable link and sends CompanyCam that link (`POST /projects/{id}/photos` with `uri`). The Public API can also take the file itself (`multipart/form-data`) - kept as the fallback if CompanyCam can't reach the Render links. **Confirmed Oct 20:** 5 of 5 drawings landed in Test (1) by this method, and CompanyCam fetched every link.
3. ~~Pick the transcription service~~ Deepgram (Oct 20). First accuracy test done (scripted notes, indoors - see Phase 3 live test); a noisy real-jobsite note is still untested.
4. Web push from the Home Screen app on Jason's iPhone.
5. Lightweight drawing renderer runs within the shared 512 MB on Render. **Measured on Render Oct 20:** the whole server went from 42 MB to a 62.5 MB peak drawing a full set (plan + 4 elevations) in 1.4 s - about 20 MB, well inside 512 MB. (Mac estimate was 30-40 MB.)
6. ~~CompanyCam REST API returns video transcripts~~ - yes, through the new Public API (see CompanyCam API finding).

## Constraints
- Render stays on the current plan (512 MB / 0.5 CPU) - no upgrade.
- Work on a branch and test before opening a pull request. Always start from the current main - never from an old copy of a file.
- Who merges (Oct 20): Claude merges PRs that change only Site Scope files once the full test suite passes; Jason merges anything touching `app.py`, the estimator, follow-ups or other shared code. Details in CLAUDE.md ("Who merges").

## Build phases (proposed Oct 19)
Each phase is one pull request Jason can test on its own before the next starts. Backend pieces first, then the page.

| # | Phase | Where | How Jason tests it | Verifies (list above) |
|---|---|---|---|---|
| 1 | **"What did we find" check** - JobTread job -> CompanyCam project via `companycamId`; lists the visit's photos, videos (transcript ready or not), whether a Site Visit Notes Page exists, and duplicate projects at the same address. Read-only. `GET /scope/preview`. **Built Oct 19** (`site_scope.py`, `companycam_api.py`). | Render | Open a link in a browser with a job number | 1, 6 |
| 2 | **Drawings** - `scope_drawings.py` draws floor plan + elevations from a structured room file with Pillow; a test endpoint draws the Oct 19 office data and uploads it to CompanyCam "Test (1)". `GET /scope/drawings-test`. **Built Oct 19, verified on Render Oct 20** (`scope_drawings.py`, `scope_uploads.py`, `room-file-format.md`). | Render | Drawings appear on the Test (1) project | 2, 5 |
| 3 | **Scope page v1** - `/intake/scope` with job picker, Record/Stop notes, server transcription with OCC trade-word hints, notes appended to the "Site Visit Notes" Page, phase 1 check shown before Start. **Built and verified on Jason's iPhone Oct 19-20** (backend PR #8; sales-lead-tool PRs #2, #3). | sales-lead-tool + Render | Record two notes on the phone; read them in CompanyCam | 3 |
| 4 | **Quick pass + questions** - Start runs in a Render background thread; Claude reads notes + photos into the structured file and writes 3-6 questions with suggested answers; run state saved so questions wait; tokens and cost logged per run. **Built Oct 20, verified on Jason's iPhone Oct 20** (backend PRs #10, #11; sales-lead-tool PR #5). | both | Tap Start on test job 00489; answer the questions | - |
| 5 | **Full build, CompanyCam side** - photo descriptions (appended, never overwrite), drawings uploaded, scope Page with sharing off | Render | Check Test (1) for descriptions, drawings, scope Page | 2 |
| 6 | **Full build, JobTread side** - budget from the structured file; code does quantities, markups, rate anchors, `$0 (PRICING TBD)` for licensed trades, internal NOTES items | Render | Budget lands on test job 00035 | - |
| 7 | **Push notification** "Questions ready - <address>" from the Home Screen app; SMS fallback only if push fails | sales-lead-tool | Start a run, close the page, get the alert | 4 |
| 8 | **Accuracy runs** on past visits (901 McDaniel / 00768, 7 Monet Dr) and tuning | both | Compare line by line with the budgets that were sent | - |

## Accounts and keys
| What | Set it in | Needed by | Notes |
|---|---|---|---|
| `COMPANYCAM_API_KEY` | Render | Phase 1 | Make a new CompanyCam access token for Render (Vercel hides the existing one, and a separate token can be revoked without breaking sales-lead-tool) |
| `SCOPE_API_KEY` | Render and Vercel (same value) | Phase 1 (Render), phase 3 (Vercel) | A long random password Jason makes up (password manager). Locks the scope endpoints, which return client addresses and photo links. With it unset the endpoints stay off |
| `COMPANYCAM_CREATOR_EMAIL` (optional) | Render | Phase 2 | Which CompanyCam user shows as creator on drawings/Pages; already used the same way in Vercel |
| `DEEPGRAM_API_KEY` | Render | Phase 3 | Deepgram console -> API Keys -> new key, expiration Never. Transcription runs on Render (decided Oct 20) |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | Render | Phase 4 | console.upstash.com -> Create Database (free) -> REST API section, .env tab. Holds runs (status, questions, answers, room file) for 30 days. Without them runs live in memory and a restart forgets them (the status reply says `store: memory`) |
| `SCOPE_MODEL`, `SCOPE_EFFORT` (optional) | Render | Phase 4 | Default `claude-opus-5-5` / `high` |
| `SCOPE_BACKEND_URL` | Vercel | Phase 3 | `https://occ-estimator.onrender.com` |
| Web push keys (VAPID) | Vercel | Phase 7 | Generated, no account. iPhone: Home Screen app + allow notifications once |
| RingCentral SMS | - | Only if push fails | Not checked yet whether OCC's number can send |
| `ANTHROPIC_API_KEY`, `JOBTREAD_API_KEY`, `JOBTREAD_ORG_ID` | Render | - | Already set |

Decided Oct 20: Opus 5.5 throughout (~$8-25/month), not Sonnet 5.5.

## Phase 1 notes (Oct 19)
- `GET /scope/preview?job=<job number or JobTread job ID>&key=<SCOPE_API_KEY>`; optional `&date=YYYY-MM-DD` or `&date=all`. The key can also go in an `X-Scope-Key` header (the scope page will use the header).
- "This visit" defaults to the day (Greenville time) of the newest photo or video, so a check the day after still finds it.
- Videos, transcripts, Pages and the duplicate-address search fail soft: a problem there shows as a warning and the photo list still comes back. Only a missing job, missing CompanyCam link, or CompanyCam being unreachable stops the check.
- The request log redacts `key=` so the key never reaches Render's logs.
- Job 00035's CompanyCam project (86663461) is archived with no photos, and "Test (1)" (117058039) is not linked to any JobTread job. For phase 1 testing use job 00768.
- Tests: `python3 -m unittest discover tests` (fakes shaped like real CompanyCam/JobTread responses; no keys needed).

## CompanyCam API finding (Oct 19, live test on job 00768)
- **Use CompanyCam's current Public API, not the legacy one.** Base `https://app.companycam.com/public_api/v1`, Bearer token, every response wrapped as `{data, errors, meta}`, lists paged with `?after=<meta.next_cursor>` (`limit` max 100), timestamps ISO 8601. Spec: https://developers.companycam.com/openapi/public_api_v1.yaml
- The first live test used the legacy `api.companycam.com/v2` (the same one sales-lead-tool's `lib/companycam.ts` uses). It read photos and videos fine, but answered Pages with **401 "Bad credentials"** and returned **no video transcripts**, even with a brand-new token. The new API has both: `GET /projects/{id}/pages`, `POST /projects/{id}/pages` (title + TipTap content), and `GET /videos/{id}?include=transcript` -> `transcript: {text, source, last_edited_at}` or null. `companycam_api.py` now uses only the new API.
- Project search moved to `GET /projects/search?query=`. Photo upload is still by URL: `POST /projects/{id}/photos` with `{photo: {uri, captured_at, description, tags}}` - `captured_at` must be **integer Unix seconds**: the spec says string, but the live API answers an ISO string with 422 "invalid value for Integer()" (Oct 20 Render test); `X-CompanyCam-User` only works with an admin-owned token (403 otherwise).
- **CompanyCam shuts the legacy API off on Sep 1, 2027.** sales-lead-tool's `lib/companycam.ts` (photo/document push) must move to the new API before then.
- A token CompanyCam rejects gives a clear "make a new Personal Access Token" message in the preview.
- **Photo timing:** job 00768's 15 photos all show 2:25 PM while its videos were 10:10-10:17 AM - photos added from the camera roll carry the upload time, not the capture time. Notes can't be matched to those by time, so site photos should be taken with the CompanyCam camera. The preview now warns when most of a visit's photos share one capture minute.
- Verify list: item 6 (video transcripts via the API) is answered - yes, through the new API.
- **Confirmed live on Render after PR #4 merged (Oct 19):** `/scope/preview?job=00768` read Pages with no error (`site_visit_notes_page.exists: false`), all 5 videos showed `transcript: ready`, and the batch-upload photo warning appeared.

## Phase 2 notes (Oct 19)
- **Room file format** defined in `room-file-format.md` (`occ-scope-rooms/1`). Phase 4 writes it; phases 5-6 read it. Sample: `samples/test1-office-2026-10-19.json` (Test (1) office with the Oct 19 answers: Wall 4 = 13', new opening gets a door clear of the HVAC chase, same-size window, Wall 3 surface-mount outlet).
- **Drawings** (`scope_drawings.py`, Pillow only): one floor plan per room plus an elevation for each wall with something on it. Walls with mitred corners and thickness by construction, door swings, every opening type in the rules, cabinets with door divisions and countertop, baseboard/crown/chair rail/wainscot/tile/backsplash, dimension strings with (est.)/(assumed) tags, wall number badges, notes, a red "CHECK BEFORE PRICING" list, legend and source key. Handles L-shaped rooms (`"turn": -90`).
- **Closure check:** if the walls don't meet within 1", the plan draws the gap in red ("DOES NOT CLOSE - off 0'-4"") and adds it to the flags. Also flags openings past the wall end and overlapping openings.
- A dimension that is exactly one opening keeps that opening's width source, so a dictated 8'-0" window stays 8'-0" even when its offset is (est.); gaps between things take the weaker source.
- **Memory** (macOS, Pillow 13, Oct 19 sample): Pillow adds about 11 MB once loaded; a full set (plan + 4 elevations, ~0.4 s) peaks about 30-40 MB above that. Drawing at 2x and shrinking (smoother arcs) peaked about 75-100 MB, for little visible gain on straight-line drawings, so it is off (`SS = 1` in `scope_drawings.py`). `app.py` only loads Pillow when a drawing is actually made, so the estimator's normal memory doesn't change. The test endpoint records the real Render numbers (whole-process memory before and after drawing) in its status view and the logs.
- **Upload to CompanyCam** (`scope_uploads.py`): each PNG is held in memory for 1 hour (25 MB cap) under a random token and served at `/scope/files/<token>/<name>.png` - no key, since CompanyCam can't send one; the token is the lock. Then `POST /projects/{id}/photos` with that URL on the Public API (`captured_at` as integer Unix seconds; the reply is unwrapped from its `data` envelope), and the code checks `GET /photos/{id}` until `processing_status` is `processed` (field confirmed on a real Test (1) photo). It also records whether CompanyCam actually fetched the link. Render sets `RENDER_EXTERNAL_URL` automatically; `SCOPE_PUBLIC_URL` overrides it. Uploads made on Oct 19 from a chat workspace failed only because that sandbox blocked the upload host.
- **Test endpoint:** `GET /scope/drawings-test?key=<SCOPE_API_KEY>` answers right away and runs on a background thread (one at a time; errors are caught and recorded, like `/send-followups`). It draws the sample and uploads the 5 drawings to CompanyCam project 117058039 "Test (1)". `&status=1` shows how the last run went: each drawing's status, whether CompanyCam downloaded it, render time and memory.
- Drawings uploaded by the test are plain project photos with a description saying they are a phase 2 test. Delete them in CompanyCam after looking if they're in the way.
- Tests: `python3 -m unittest discover tests` - drawing tests skip without Pillow; the upload tests run a full round trip on a local server with a fake CompanyCam that downloads each drawing.
- **Moved to the Public API (Oct 19, after PR #4):** the upload was first written against the legacy API. It now reads the photo out of the `data` envelope; without that, every upload would have looked failed, because the photo ID came back empty. The fake CompanyCam in the tests answers in the same envelope.
- **First Render run (Oct 20) - 0 of 5 uploaded:** PR #3 had switched `captured_at` to an ISO string to match the spec, and CompanyCam refused all 5 with 422 "invalid value for Integer()". Back to integer Unix seconds (what the legacy API took too); the fake CompanyCam now refuses a non-integer `captured_at` the same way, so the tests catch it. Drawing itself worked: 1.4 s, about 20 MB. **Lesson: where the spec and the live API disagree, the live API wins - note it here.**
- **Second Render run (Oct 20) - 5 of 5 in CompanyCam** (photo IDs 3677834241-3677834252, captured_at as integer). CompanyCam fetched every temporary link; drawings render cleanly there (floor plan checked). Memory: whole server 37.6 MB -> 59.9 MB peak, 1.4 s to draw. The 5 test drawings are still on Test (1) - Jason can delete them.

## Phase 3 notes (Oct 20)
- **Decisions (Jason, Oct 20):** Deepgram for speech-to-text (see Capture method); transcription and the Page write run **on Render**, not Vercel; Upstash Redis waits for phase 4; testing on a new JobTread job "Scope Tool Test" at the office address (JobTread creates its CompanyCam project), not a real client job.
- **Why Render:** after the phone uploads the audio to Vercel Blob and hands off, Render does the rest on a background thread, so a dropped signal or closed page after Stop still saves the note. Render already has the Public API CompanyCam client proven live; sales-lead-tool's `lib/companycam.ts` is on the legacy API, which can't write Pages. Vercel stays a thin forwarder that adds `SCOPE_API_KEY` server-side, so the key never reaches the phone.
- **Flow:** phone records (MediaRecorder; iPhone gives audio/mp4) -> uploads to Vercel Blob -> Vercel `POST /scope/notes` -> Render answers 202 at once, then sends the Blob link to Deepgram (Deepgram downloads it; the audio never passes through Render's memory) -> appends the text to the project's "Site Visit Notes" Page. The page polls `GET /scope/notes/status?id=` and shows the text a few seconds after Stop. Fixing a misheard number: `POST /scope/notes/fix` swaps that note's text on the Page.
- **Backend files:** `scope_notes.py` (endpoints, Page writes, note status), `scope_transcribe.py` (Deepgram; the OCC trade-word list `TRADE_KEYTERMS` lives here), `companycam_api.py` (`get_page`, `create_page`, `update_page`). `app.py` only registers the routes (`POST /scope/*` answers with JSON instead of the Wufoo-style instant "OK").
- **Page format:** one Page titled "Site Visit Notes" per project, created on the first note with a one-line italic intro. Each note: a heading with the start date/time ("Oct 21, 2026 - 2:14 PM", Greenville time), an italic line "Recorded 2:14:05 PM to 2:15:47 PM (1:42 of audio). Play the recording" (link to the Blob audio, so a questionable number can be replayed), then the text word-for-word, one paragraph per Deepgram paragraph. Phase 4 can read note times back from these lines to match photos.
- **Appending:** the Public API has no append call - read the Page (`GET /pages/{id}?include=tiptap_content`), add nodes to the end of its TipTap `content`, send the whole document back (`PATCH /pages/{id}` with `tiptap_content`). The rest of the document (including `attrs.pageSettings`) is kept as read. One write at a time per project (a lock on Render), so two notes saved together can't overwrite each other.
- **Spec vs. live to watch for in the first live write:** the spec's create/update body lists `attrs.sectionType` as required on top-level nodes, but real Pages (Test (1) scope Page, Ben's work orders) have plain top-level `heading`/`paragraph` nodes, so the code sends those. Link marks aren't in the spec at all: if CompanyCam refuses the audio link (400/422), the note is saved again with the link as plain text. Also check whether a Page made through the API starts out shared (`shared: true` with a `share_url` on every Page read so far).
- **Fixes:** the fix finds the newest note under that heading whose text still matches what the page last showed, and replaces only that text. If someone has edited it in CompanyCam since, the fix is added as a new note headed "... (corrected)" instead, so nothing is overwritten.
- **Retries and limits:** each note carries an id from the phone, so a retry after a dropped signal doesn't add it twice (while Render remembers it - in memory, a day). An empty transcript is reported back ("No speech was heard... Record it again") and nothing is written. Audio links must be Vercel Blob https links.
- **Browser test:** `GET /scope/notes-test?key=<SCOPE_API_KEY>&job=<job number>&text=<words>` adds a typed note right away (no audio, no Deepgram), to check the Page write before the scope page exists.
- Tests: `tests/test_scope_notes.py` (fake CompanyCam Pages and Deepgram in their real response shapes, including four notes saved at once).
- **Page side (sales-lead-tool PRs #2 and #3):** `app/intake/scope/page.tsx` on the Sales menu; `components/JobPicker.tsx` (the walk-through picker as its own component, no "active only" filter since visits are pre-sale; the page remembers the job for 12 hours); `components/SiteNotes.tsx` (Record/Stop); `lib/scope-backend.ts` forwards `/api/scope/*` to Render and adds `SCOPE_API_KEY` on the server; audio uploads only under `scope-notes/` in Blob.
- **Recorder behavior:** iPhone records `audio/mp4`, in one piece (no timeslice). Notes cap at 15 minutes. Switching apps mid-note (e.g. to CompanyCam for a photo) stops and saves the note, since iPhone pauses the mic for background pages. A recording that can't be sent stays in the phone's IndexedDB and is retried on Retry, when the signal returns, or the next time the page opens for that job; it's deleted once Render accepts it. The note list (not the audio) is kept in localStorage for 12 hours so a reopened page shows the visit's notes.

## Phase 3 live test (Oct 19, 2026, ~10:12-10:20 PM, Jason's iPhone)
- **Test job:** 00489 "New selections test" (JobTread test job, placeholder address 1217 Fieldstone Ct, Royse City TX; CompanyCam project 102406478). The planned "Scope Tool Test" job never showed up in JobTread, so this existing test job was used instead.
- **Result: verified.** Three scripted notes (kitchen numbers, exterior trade words, bathroom mixed) landed word-for-word on a new "Site Visit Notes" Page (id 3006585), each under its own heading with the italic recorded-at line and a working "Play the recording" link.
- **Retry path proven live:** the notes were recorded 10:12-10:15 PM before `DEEPGRAM_API_KEY` was in Render; the phone kept them, and they reached the Page at 10:19-10:20 PM once the key was added.
- **Live API vs. spec (CompanyCam Pages):** plain top-level `heading`/`paragraph` nodes are accepted on create and update (the spec's required `attrs.sectionType` is not needed); `link` marks are accepted, so the plain-text fallback wasn't used; the body is unwrapped (`title` / `tiptap_content` at the top level). **A Page made through the API starts out shared** (`shared: true` with a public `share_url`), and the update call has no `shared` field, so the API can't turn sharing off. Anyone with that link could read the notes and play the recordings. The link is only known inside CompanyCam, but Jason can turn sharing off on the Page in CompanyCam. Phase 5's plan for a scope Page "with sharing off" needs another way: check the UI, or ask CompanyCam.
- **Setup snags, for next time:** the phone app is Vercel project **sales-lead-tool-new** (sales-lead-tool-new.vercel.app). Two more Vercel projects build the same repo (`sales-lead-tool`, `sales-lead-tool-is2t`), and env vars set on the wrong one don't reach the app. Vercel only picks up env changes on a redeploy.
- **Deepgram accuracy (Nova-3 with keyterms, quiet room):**
  - Numbers: every dictated measurement in note 1 came through right ("12 feet six inches", "14 feet three and a half inches", "two feet four inches", "32 by 48", "42 inches AFF", "18 square feet"). Small numbers often stay as words ("six inches", "two by eight"), which is fine for reading and for phase 4.
  - One likely wrong number: note 2 "**12** feet of soffit" where the script said twenty. Jason to replay and confirm. This is the case the plan already covers: phase 4 must ask about numbers, never silently "correct" them.
  - Trade words right: GFCI, AFCI, AFF, fascia, soffit, LP SmartSide, drip edge, prehung, CMU, hose bib, quartz, Schluter, cement board, thinset, sister, on center, quarter round.
  - Misses: "wainscot" -> "**Wayne's coat**" (even though it's on the keyterm list), "jamb" -> "jam", "P-trap" -> "p trap", "joists" -> "joist", a stray "Hi," at the end of note 2, and a few missed sentence breaks. None of these changes the meaning of a scope; phase 4's quick pass reads the notes as text and can handle them.
  - **Decision for now: keep Deepgram.** Revisit only if a real noisy jobsite note does badly on numbers.
- **Not tested live this round:** switching apps mid-note, Airplane Mode while recording, the too-short tap, and Save fix (no fix was saved; the Page text is as transcribed).
- **Known UI issue (sales-lead-tool-wide, not scope-specific):** in the Home Screen app, the page's top bar (Back + title) sits under the iPhone status bar. No page in the app pads for the status bar (`env(safe-area-inset-top)`).

## Phase 4 notes (Oct 20)
- **Decisions (Jason, Oct 20):** Opus 5.5 (effort high) for the quick pass; Upstash Redis for run state, on Render only.
- **Endpoints** - all `POST` with JSON and `X-Scope-Key`, through the same `/scope/*` POST route as the notes, so **app.py did not change** (`scope_notes.handle_post` hands `/scope/runs*` to `scope_runs.py`):
  - `/scope/runs` `{job, date?, restart?}` - starts a run (202) on a background thread. If one is already reading, or questions are waiting, it returns that run instead (200); `restart: true` starts over when questions are waiting. At most 2 runs at once on the server.
  - `/scope/runs/status` `{run_id}` or `{job}` (newest run on that job; `{status: "none"}` if there isn't one). `include_file: true` adds the whole room file.
  - `/scope/runs/answers` `{run_id, answers: {Q1: "..."}}` - a blank answer means the suggested one. Status becomes `answered`; phases 5-6 build from there.
- **Run states:** `reading` -> `questions` -> `answered`, or `failed` with an `error` sentence. A `reading` run that isn't running on this server 90 s after its last change was cut off by a restart; it is marked failed ("tap Start to run it again") rather than waiting forever.
- **What the quick pass reads** (`scope_quickpass.py`): the notes on the "Site Visit Notes" Page (heading + "Recorded X to Y" / "Typed note, saved X" lines give each note's time; a "(corrected)" note sits by the one it corrects), the visit's photos, and transcripts of videos 5 s or longer that are already ready. **Visit day** = the day of the newest note, photo or video (the page can send `date`). The tool's own drawings (description contains "Site Scope Tool") are skipped, so phase 5 drawings must keep that phrase. Up to 40 photos, 10 videos.
- **Photos go to Claude as CompanyCam links**, resized by CompanyCam with `?d=2000x2000` (checked Oct 20: CompanyCam's `static.companycam.com` links take any `d=WxH` and only shrink). Render never downloads them. If Claude's side can't fetch the links (a 400 about the image), Render downloads them at 1600 px and sends them inline (24 MB cap).
- **Everything goes to Claude in time order** (photos P1.., notes N1.., videos V1..), with the rules docs (`room-file-format.md`, `floor-plan-drawing-rules.md`) cached in the system prompt (~6k tokens). Claude returns `{room_file, questions}`; the room file adds `client_goals`, `scope_items`, `photos` (descriptions for phase 5) and `conflicts` (see `room-file-format.md`). No prices in this pass.
- **Code checks:** every room goes through `scope_drawings.parse_room`; an unusable answer (bad JSON, a room the drawings can't read, cut off) is sent back once with the problem, then the run fails. Closure and wall checks are added to the room's flags, and walls that don't close get a code-written question if there are fewer than 6. More than 6 questions are trimmed to 6. Zero questions goes straight to `answered`.
- **Claude call:** streamed over plain `urllib` like the estimator (no SDK added to Render), `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) so a rare safety decline re-runs on Anthropic's fallback model; if the API ever rejects that option it is dropped and the call repeated. Busy/5xx answers retried up to 4 times.
- **Cost log:** each call logs model, stop reason, usage and dollars; the run keeps `usage` (tokens in/out, cache, `cost_usd`, seconds, calls) and the page shows the cost. Prices in `scope_quickpass.PRICES` (Opus 5.5 $4/$20, cache read $0.20, cache write 1.25x).
- **CompanyCam Public API:** photo `description` is an object (`{plain_text_content, html_content, id}`), not a string - the phase 1 preview was returning the object; fixed.
- Tests: `tests/test_scope_runs.py` (fake CompanyCam with job 00489's real notes Page, Claude faked at the stream level, Upstash faked at the command level, and a round trip through the real app.py handler).

## Phase 4 live test (Oct 20, 2026, ~8:12-8:40 AM, Jason's iPhone, job 00489)
- **Result: verified.** Upstash connected (status reply `store: upstash`, after fixing a mis-pasted `UPSTASH_REDIS_REST_URL`; PR #11 turns a bad URL into a plain message). Jason took 4 photos of his office with the CompanyCam camera (8:12-8:13 AM) and recorded 1 note, tapped Start, got 6 questions with suggested answers, changed the last one and sent. The page showed "Answers saved" with the edited answer, not the suggestion.
- **Visit-day rule worked as written:** the card read "Oct 20 visit: 1 note, 4 photos". Last night's three phase 3 test notes and 9 photos uploaded this morning but taken Oct 19 were left out (CompanyCam dates photos by `captured_at`, not upload time).
- **Questions were on target:** rooms = Office, 9 scope items. It flagged the window wall as a likely outside block wall, caught dictated 6'-0" openings being lower than the 6'-8" doors, asked whether to keep the doors, asked about an engineer and steel lintels instead of guessing (licensed-trade rule), and whether two walls were framed or block / interior or exterior.
- **Cost and time:** $0.63 and 250 s for the quick pass alone (4 photos + 1 short note). That is the whole-visit estimate in "Cost estimate" spent on one of the two calls, so a full visit will likely run ~$1.20+ on Opus; confirm once phase 5 runs. 250 s is slow for a quick pass - look at where the time goes (photo fetch vs. model) before phase 5.
- Jason ran the quick pass twice: after answering the first set he tapped the start button again, thinking it was the next step. The second run replaced the first set of questions (about $0.63 more). See the page fixes below.

### Page fixes from this test (sales-lead-tool, Jason merges)
1. **Job search by number** - the picker's search doesn't match the job number, and each row shows only the customer name (00489 shows as "Hilario Garza Test - JT", and sorts last because its JobTread address is in Texas). Search the number too and show it (and the job name) on each row.
2. **Notes list on the page** - shows notes recorded on this phone in the last 12 hours, but the 12-hour clock restarts for the whole list on every save, so old notes linger. Keep only the current visit's notes, each aging out on its own, plus a "clear from this screen" button (never deletes from CompanyCam).
3. **After "Answers saved"** - the "Start a new run" button looks like the next step. Relabel it and say plainly that nothing else happens until phase 5.

### Decision (Jason, Oct 20): earlier-visit photos and later notes
Jason wants to be able to upload photos from an earlier site visit and record notes about them later, so photo dates and note dates won't always match. Plan:
- **"Which visit?" picker** before Start, listing the days that have photos or notes ("Oct 19 - 9 photos, 3 notes", "Today - 1 note"); newest day picked by default. The backend already takes `date` on `/scope/runs`; only the page needs it.
- **Notes tagged to a visit:** a note recorded today about Oct 19 photos can be filed under Oct 19, so the quick pass reads them together. Needs a visit date on the note (Page heading or the italic line) that `parse_notes_page` reads in place of the recording time.
- **Skip a note** in the "What we found" list so a stray or test note stays out of a run without deleting it from CompanyCam.
