# PM pilot - nightly Granola pull

Built Nov 25, 2026 for the PM pilot (Lexi Menna and Megan Butz start recording Mon Nov 30). Code: `granola_pm_notes.py`, tests: `tests/test_granola_pm_notes.py`. Plan of record for the PM track lives in the claude.ai project "OCC Knowledge Base" (`brain/plans/pm-track.md`).

## What it does
The PMs only hit record in Granola. They don't rename or file anything. Every night:

1. **Pull.** For each pilot PM, read their new Granola notes (summary, typed notes, transcript) with their own Personal API key. Only notes from `PM_NOTES_START` (Nov 30) on. Each run re-reads one day back so a note that finished summarizing late isn't missed.
2. **Match to a job.** Candidate jobs are that PM's own active jobs: JobTread "Project Manager" field (`22PGmYmLA9dG`) names them and "Renovation Status" (`22P9ppJUMrPu`) is Active. Read live each run, so a new job is picked up with no code change.
   - Look in the note title first, then the title plus the first minute of the transcript, for the client's last name, the street, or the job number.
   - One job found: filed there.
   - More than one: a client name, house number + street, or job number beats a bare street name (Camp 00161 and Harris 00384 are both on Latour Way). Still more than one: the job number decides.
   - Otherwise: "needs your call". It never guesses.
   - Names that are ordinary words (Camp, Buck, Miller...) only count from the title, never from the transcript alone.
3. **File it.** A matched note becomes an internal Daily Log on the job, dated the day it was recorded. Summary and the PM's typed notes first, then the transcript. Long transcripts continue in more Daily Logs ("transcript part 2 of 3"), up to 8; anything past that stays in the PM's Granola.
4. **Needs your call.** An unmatched note becomes a to-do for Jason (organization level, not on a job) with the reason, the PM's active jobs and the summary. To file it, add the job number to the to-do's name and check it off; the next run files it. Checked off with no number = not a job note, left alone. If the PM renames the note in Granola so it matches, it files itself and the to-do closes.
5. **Keys.** To-do for Jason 14 days and 3 days before a key's expiry date, right away if Granola rejects a key, and once if a key is missing after Nov 30.

## Safety
- **Nothing reaches a client.** The only JobTread writes this file can make are `createDailyLog`, `createTask` and `updateTask`. `_mutate()` refuses anything else (a test checks `createComment` and `sendDocument` are refused).
- Daily Logs: OCC's customer and vendor roles have no `readDailyLogs` permission (checked Nov 25, 2026 via `organization.roles`). Logs are created with `notify: false` and no assignees. Still worth one look in a client portal after the first run.
- To-dos are always assigned to Jason only, `notify: false`.
- No duplicates: before writing, it checks JobTread for a Daily Log carrying `Granola note id: not_...`, so even a wiped store can't double-file.
- `/pm-notes/status` shows counts and key status only - never note text or keys.
- An unmatched note's summary does land in Jason's to-do. If a PM records something private (a personal call, an HR conversation), they should pause Granola, same rule as leadership.
- **Departs from "links, not copies"** (architecture Rule 1) on purpose: the PMs' notes are in their personal Granola space, which nobody else can open, so the summary and transcript are copied onto the job where the Friday update can read them.

## Setup (Jason)
1. **Keys.** Lexi and Megan each open the Granola desktop app: Settings > Connectors > API keys > Create new key, **Personal notes** scope. They send you the key (starts `grn_`) and the expiry date if Granola asked for one.
2. **Render** (occ-estimator > Environment), add:
   - `GRANOLA_KEY_LEXI` = Lexi's key, `GRANOLA_KEY_LEXI_EXPIRES` = its expiry date as `2027-02-28`
   - `GRANOLA_KEY_MEGAN` = Megan's key, `GRANOLA_KEY_MEGAN_EXPIRES` = its expiry date
   - Optional: `PM_NOTES_RUN_KEY` = any password. If set, add `?key=<it>` to the URLs below.
   - Upstash (`UPSTASH_REDIS_REST_URL` / `_TOKEN`) is already used by Site Scope. The pull uses it too; without it, the "needs your call" list is forgotten on each deploy.
3. **cron-job.org**: new job, GET `https://occ-estimator.onrender.com/pm-notes/run`, every night at 11:30 PM Eastern.
4. **Check**: open `https://occ-estimator.onrender.com/pm-notes/status` in a browser. Both keys should show `key_set: true` and a `days_until_expiry`.
5. To run it right away (say, Friday morning before drafting): open `/pm-notes/run` in a browser.

## How the Friday weekly update reads it
All of a week's PM notes for one job:

```json
{"job": {"$": {"id": "<jobId>"}, "dailyLogs": {
  "$": {"where": {"and": [["notes", "like", "%[PM-NOTE]%"], ["date", ">=", "2026-11-30"], ["date", "<=", "2026-12-04"]]},
        "size": 100, "sortBy": [{"field": "date"}]},
  "nodes": {"date": {}, "notes": {}}}}}
```

Org-wide: the same `where` under `organization.dailyLogs`. In code: `granola_pm_notes.notes_for_job(jobtread_query, job_id, start, end)`.

## Open
- Granola's API doesn't say when a Personal API key expires, so the date has to be typed into Render. If Granola's key screen doesn't ask for an expiry, leave `_EXPIRES` empty; the rejected-key alert still covers it.
- Rate limit: Granola allows about 5 requests a second; the pull paces itself at 4.
- Next: the Friday weekly update drafts (pm-track build order step 1), reading these logs plus the JobTread schedule and CompanyCam.
