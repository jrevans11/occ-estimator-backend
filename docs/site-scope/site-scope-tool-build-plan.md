# OCC Site Scope Tool - Build Plan

Planning notes from Oct 18-19, 2026. Status: **phases 1-2 of 8 built (Oct 19) - see Build phases.** Companion docs in this folder: `site-visit-writeup-process.md` (what the tool does, step by step), `floor-plan-drawing-rules.md` (drawing rules) and `room-file-format.md` (the structured room file the drawings and later phases work from). `prototype_drawings.py` is the throwaway script that drew the Oct 19 test drawings - a geometry reference only (it renders through a headless browser, which is too heavy for Render; see "Where the code lives").

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
  - Candidate services (prices from Oct 2026 comparison articles - confirm on vendor pages): AssemblyAI ~$0.0025-0.0035/min with $50 free credit; Deepgram Nova-3 ~$0.0077/min with $200 free credit; OpenAI gpt-4o-mini-transcribe ~$0.003/min, gpt-4o-transcribe ~$0.006/min, no free credit.
  - ~10 min of notes per visit x ~20 visits/month = ~200 min/month = roughly $0.50-1.50/month; free signup credits cover years.
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
- Run state (notes, questions, answers, status) stored in a small database (e.g. Upstash Redis / Vercel KV free tier) so questions wait if the page is closed.
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
- Model choice not confirmed by Jason yet (stronger model throughout suggested, ~$10/month difference).
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
2. Photo upload works from Render, so drawings land in CompanyCam. **Method decided Oct 19 (phase 2):** Render serves each drawing briefly at an unguessable link and sends CompanyCam that link (`POST /projects/{id}/photos` with `uri`). The Public API can also take the file itself (`multipart/form-data`) - kept as the fallback if CompanyCam can't reach the Render links. Confirm with the phase 2 test.
3. Pick the transcription service; test accuracy on a real jobsite note (background noise, numbers, trade words).
4. Web push from the Home Screen app on Jason's iPhone.
5. Lightweight drawing renderer runs within the shared 512 MB on Render. **Measured Oct 19 (phase 2):** Pillow, about 30-40 MB on top of the server for a full set (see Phase 2 notes); the phase 2 test reports the real Render number.
6. ~~CompanyCam REST API returns video transcripts~~ - yes, through the new Public API (see CompanyCam API finding).

## Constraints
- Render stays on the current plan (512 MB / 0.5 CPU) - no upgrade.
- Work on a branch, test before opening a pull request, and Jason merges in GitHub. Always start from the current main - never from an old copy of a file.

## Build phases (proposed Oct 19)
Each phase is one pull request Jason can test on its own before the next starts. Backend pieces first, then the page.

| # | Phase | Where | How Jason tests it | Verifies (list above) |
|---|---|---|---|---|
| 1 | **"What did we find" check** - JobTread job -> CompanyCam project via `companycamId`; lists the visit's photos, videos (transcript ready or not), whether a Site Visit Notes Page exists, and duplicate projects at the same address. Read-only. `GET /scope/preview`. **Built Oct 19** (`site_scope.py`, `companycam_api.py`). | Render | Open a link in a browser with a job number | 1, 6 |
| 2 | **Drawings** - `scope_drawings.py` draws floor plan + elevations from a structured room file with Pillow; a test endpoint draws the Oct 19 office data and uploads it to CompanyCam "Test (1)". `GET /scope/drawings-test`. **Built Oct 19** (`scope_drawings.py`, `scope_uploads.py`, `room-file-format.md`). | Render | Drawings appear on the Test (1) project | 2, 5 |
| 3 | **Scope page v1** - `/intake/scope` with job picker, Record/Stop notes, server transcription with OCC trade-word hints, notes appended to the "Site Visit Notes" Page, phase 1 check shown before Start | sales-lead-tool + Render | Record two notes on the phone; read them in CompanyCam | 3 |
| 4 | **Quick pass + questions** - Start runs in a Render background thread; Claude reads notes + photos into the structured file and writes 3-6 questions with suggested answers; run state saved so questions wait; tokens and cost logged per run | both | Tap Start on Test (1); answer the questions | - |
| 5 | **Full build, CompanyCam side** - photo descriptions (appended, never overwrite), drawings uploaded, scope Page with sharing off | Render | Check Test (1) for descriptions, drawings, scope Page | 2 |
| 6 | **Full build, JobTread side** - budget from the structured file; code does quantities, markups, rate anchors, `$0 (PRICING TBD)` for licensed trades, internal NOTES items | Render | Budget lands on test job 00035 | - |
| 7 | **Push notification** "Questions ready - <address>" from the Home Screen app; SMS fallback only if push fails | sales-lead-tool | Start a run, close the page, get the alert | 4 |
| 8 | **Accuracy runs** on past visits (901 McDaniel / 00768, 7 Monet Dr) and tuning | both | Compare line by line with the budgets that were sent | - |

## Accounts and keys
| What | Set it in | Needed by | Notes |
|---|---|---|---|
| `COMPANYCAM_API_KEY` | Render | Phase 1 | Make a new CompanyCam access token for Render (Vercel hides the existing one, and a separate token can be revoked without breaking sales-lead-tool) |
| `SCOPE_API_KEY` | Render now; Vercel in phase 3 | Phase 1 | A long random password Jason makes up (password manager). Locks the scope endpoints, which return client addresses and photo links. With it unset the endpoints stay off |
| `COMPANYCAM_CREATOR_EMAIL` (optional) | Render | Phase 2 | Which CompanyCam user shows as creator on drawings/Pages; already used the same way in Vercel |
| Transcription service account + key | Vercel or Render (decide in phase 3) | Phase 3 | Recommend AssemblyAI (cheapest, $50 free credit, takes trade-word hints); Deepgram is the backup |
| Upstash Redis (free tier, add from Vercel's Storage tab) | Vercel (automatic); Render too if the backend writes run state | Phase 3-4 | Holds notes, questions, answers, run status |
| `SCOPE_BACKEND_URL` | Vercel | Phase 3 | The Render service URL |
| Web push keys (VAPID) | Vercel | Phase 7 | Generated, no account. iPhone: Home Screen app + allow notifications once |
| RingCentral SMS | - | Only if push fails | Not checked yet whether OCC's number can send |
| `ANTHROPIC_API_KEY`, `JOBTREAD_API_KEY`, `JOBTREAD_ORG_ID` | Render | - | Already set |

Decision still open before phase 4: Opus 5.5 throughout (~$8-25/month) or Sonnet 5.5 (~$4-12/month).

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
- Project search moved to `GET /projects/search?query=`. Photo upload is still by URL: `POST /projects/{id}/photos` with `{photo: {uri, captured_at (ISO string), description, tags}}`; `X-CompanyCam-User` only works with an admin-owned token (403 otherwise).
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
- **Upload to CompanyCam** (`scope_uploads.py`): each PNG is held in memory for 1 hour (25 MB cap) under a random token and served at `/scope/files/<token>/<name>.png` - no key, since CompanyCam can't send one; the token is the lock. Then `POST /projects/{id}/photos` with that URL on the Public API (`captured_at` sent as an ISO 8601 string; the reply is unwrapped from its `data` envelope), and the code checks `GET /photos/{id}` until `processing_status` is `processed` (field confirmed on a real Test (1) photo). It also records whether CompanyCam actually fetched the link. Render sets `RENDER_EXTERNAL_URL` automatically; `SCOPE_PUBLIC_URL` overrides it. Uploads made on Oct 19 from a chat workspace failed only because that sandbox blocked the upload host.
- **Test endpoint:** `GET /scope/drawings-test?key=<SCOPE_API_KEY>` answers right away and runs on a background thread (one at a time; errors are caught and recorded, like `/send-followups`). It draws the sample and uploads the 5 drawings to CompanyCam project 117058039 "Test (1)". `&status=1` shows how the last run went: each drawing's status, whether CompanyCam downloaded it, render time and memory.
- Drawings uploaded by the test are plain project photos with a description saying they are a phase 2 test. Delete them in CompanyCam after looking if they're in the way.
- Tests: `python3 -m unittest discover tests` - drawing tests skip without Pillow; the upload tests run a full round trip on a local server with a fake CompanyCam that downloads each drawing.
- **Moved to the Public API (Oct 19, after PR #4):** the upload was first written against the legacy API. It now sends `captured_at` as ISO 8601 and reads the photo out of the `data` envelope. Without that, every upload would have looked failed, because the photo ID came back empty. The fake CompanyCam in the tests now answers in the same envelope.
