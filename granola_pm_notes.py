"""PM field notes from Granola - the nightly pull for the PM pilot (Nov 30, 2026).

Lexi Menna and Megan Butz only hit record in Granola. They don't file, rename
or tag anything. Every night this module:

  1. Reads each pilot PM's new notes (summary, typed notes and transcript)
     through the Granola Public API, using that PM's own Personal API key.
  2. Matches each note to one of that PM's active JobTread jobs. A job is
     theirs when its "Project Manager" field (22PGmYmLA9dG) names them and its
     "Renovation Status" (22P9ppJUMrPu) is Active. Matching order:
       - the note title, then the title plus the first minute of the
         transcript, looking for the client's last name, the street, or the
         job number;
       - one job found = filed there;
       - more than one job found = the job number decides (said or typed);
       - otherwise the note goes to Jason as a "needs your call" to-do.
     It never guesses.
  3. Files a matched note as an internal Daily Log on the job (summary first,
     then the transcript, split across extra Daily Logs when it's long). Every
     log starts with "[PM-NOTE]" and carries "Granola note id: not_...", so
     the Friday weekly update finds the week's notes with one Daily Log query.
     JobTread's customer and vendor roles at OCC have no readDailyLogs
     permission (checked Nov 25, 2026), and the logs are created with
     notify: false and no assignees.

"Needs your call" to-dos are assigned to Jason at the organization level. To
file one, he adds the job number to the to-do's name and checks it off; the
next run files it on that job. Checking it off with no number means "not a job
note" and it's left alone.

Key expiry: Granola's API doesn't report when a key expires, so the date each
key was set to expire goes in Render next to the key. Jason gets a to-do 14
days and 3 days before, and right away if a key stops working (401) or is
missing once the pilot has started.

Nothing here can reach a client. The only JobTread writes allowed are
createDailyLog, createTask (always assigned to Jason) and updateTask (to close
a to-do this module made). _mutate() refuses anything else, so createComment,
sendDocument and the like can't be called from this file by mistake.

Render env vars, one set per PM (slugs LEXI and MEGAN):
  GRANOLA_KEY_LEXI           the PM's Personal API key (grn_...), Personal notes scope
  GRANOLA_KEY_LEXI_EXPIRES   the key's expiry date, YYYY-MM-DD (optional, recommended)
Optional:
  PM_NOTES_START             first day of notes to pull (default 2026-11-30)
  PM_NOTES_RUN_KEY           if set, /pm-notes/run needs ?key=<this value>

Endpoints (registered in app.py):
  GET /pm-notes/run      start a run in the background (cron-job.org, nightly)
  GET /pm-notes/status   counts and key status only - never note content

Run state lives in scope_store (Upstash when configured). JobTread is the
record: the Daily Log marker is checked before every write, so a lost store
can't produce duplicate logs.
"""

import hashlib
import json
import os
import re
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import scope_store

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - tzdata missing
    LOCAL_TZ = timezone(timedelta(hours=-5))

# ── Constants ────────────────────────────────────────────────────────────────

GRANOLA_API = "https://public-api.granola.ai/v1"

# (name exactly as it appears in JobTread's Project Manager field, env slug)
PILOT_PMS = (
    ("Lexi Menna", "LEXI"),
    ("Megan Butz", "MEGAN"),
)

PM_FIELD_ID = "22PGmYmLA9dG"            # Project Manager (job)
RENO_STATUS_FIELD_ID = "22P9ppJUMrPu"   # Renovation Status (job)
ACTIVE_STATUS = "Active"
JASON_MEMBERSHIP_ID = "22P9ppHePJKQ"

DEFAULT_START = "2026-11-30"
NOTE_TAG = "[PM-NOTE]"
NOTE_ID_MARKER = "Granola note id: "
DAILY_LOG_LIMIT = 10000                 # JobTread's createDailyLog notes cap
TASK_DESCRIPTION_LIMIT = 4096
MAX_TRANSCRIPT_PARTS = 8                # beyond this the rest stays in Granola
FIRST_MINUTE_SECONDS = 60
OVERLAP = timedelta(days=1)             # re-read a day back each run (late summaries)
EXPIRY_WARN_DAYS = (3, 14)          # smallest first: warn at 14 days, again at 3

ALLOWED_MUTATIONS = frozenset({"createDailyLog", "createTask", "updateTask"})

STORE_STATE = "pmnotes:state"
STORE_PENDING = "pmnotes:pending"
STORE_LAST_REPORT = "pmnotes:last-report"


def _note_key(note_id):
    return f"pmnotes:note:{note_id}"


# Words that are also job names but are ordinary English. They only count
# when they're in the note title - never from the transcript alone ("we set up
# camp", "pass the buck").
COMMON_WORD_NAMES = frozenset({
    "camp", "buck", "may", "wood", "woods", "rose", "king", "young", "white",
    "black", "brown", "green", "gray", "grey", "long", "short", "hill", "hall",
    "price", "cash", "mark", "bill", "frank", "rich", "joy", "hope", "faith",
    "love", "wells", "stone", "page", "rice", "bush", "lane", "ford", "field",
    "best", "little", "hunter", "carpenter", "mason", "cook", "baker", "miller",
    "parker", "walker", "turner", "fisher", "porter", "chase", "grant",
    "case", "light", "power", "fox", "bird", "swift", "hart", "nash", "cross",
})

# Words dropped when pulling client names out of account and job names.
NAME_STOPWORDS = frozenset({
    "and", "the", "of", "renovation", "renovations", "reno", "restoration",
    "remodel", "addition", "kitchen", "bath", "bathroom", "basement", "job",
    "home", "house", "repair", "repairs", "project", "phase", "new", "sunroom",
    "porch", "deck", "garage", "exterior", "interior", "family", "jr", "sr",
    "ii", "iii", "mr", "mrs", "ms", "dr",
})

STREET_SUFFIXES = frozenset({
    "rd", "road", "st", "street", "ave", "avenue", "dr", "drive", "ct", "court",
    "ln", "lane", "way", "cir", "circle", "trl", "trail", "blvd", "boulevard",
    "pl", "place", "pkwy", "parkway", "hwy", "highway", "ter", "terrace", "loop",
})

# Street words too generic to identify a job on their own.
GENERIC_STREET_WORDS = frozenset({
    "rd", "road", "st", "street", "ave", "avenue", "dr", "drive", "ct", "court",
    "ln", "lane", "way", "cir", "circle", "trl", "trail", "blvd", "boulevard",
    "pl", "place", "pkwy", "parkway", "hwy", "highway", "ter", "terrace", "loop",
    "run", "pt", "point", "n", "s", "e", "w", "north", "south", "east", "west",
    "old", "new", "mill", "spring", "forest", "star", "park", "hill", "creek",
    "lake", "oak", "pine", "ridge", "view", "valley", "farm", "church", "main",
    "unit", "apt", "suite", "sc", "usa",
})


# ── Small helpers ────────────────────────────────────────────────────────────

def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def start_date():
    raw = _env("PM_NOTES_START", DEFAULT_START)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.fromisoformat(DEFAULT_START)


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _local(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def _fmt_local(dt):
    loc = _local(dt)
    if loc is None:
        return "unknown time"
    hour = loc.strftime("%I").lstrip("0") or "12"
    return f"{loc.strftime('%a %b')} {loc.day} {loc.year}, {hour}:{loc.strftime('%M %p')}"


def _first_name(full):
    return (full or "").split(" ")[0] or "PM"


def _norm(text):
    """Lowercase, letters and digits only, single spaces, padded for word matching."""
    text = (text or "").lower().replace("'", "").replace("’", "")
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


def _has_phrase(norm_text, phrase):
    phrase = _norm(phrase).strip()
    return bool(phrase) and f" {phrase} " in norm_text


def _fingerprint(secret):
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()[:10]


def _iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── JobTread writes: the only three allowed ──────────────────────────────────

class ForbiddenMutation(Exception):
    pass


def _mutate(jt_query, query):
    """Run a JobTread mutation, refusing anything that isn't on the allow-list."""
    roots = set(query.keys())
    bad = roots - ALLOWED_MUTATIONS
    if bad:
        raise ForbiddenMutation(f"granola_pm_notes may not call {sorted(bad)}")
    if "createDailyLog" in query:
        args = query["createDailyLog"]["$"]
        args["notify"] = False
        args.pop("assignees", None)
    if "createTask" in query:
        args = query["createTask"]["$"]
        args["isToDo"] = True
        args["assignees"] = [{"membershipId": JASON_MEMBERSHIP_ID}]
        args.pop("assignedMembershipIds", None)
        args["notify"] = False
    return jt_query(query)


# ── Granola API ──────────────────────────────────────────────────────────────

class GranolaError(Exception):
    pass


class GranolaAuthError(GranolaError):
    pass


class TranscriptTooLarge(GranolaError):
    pass


_last_call = [0.0]
_pace_lock = threading.Lock()


def _granola_get(api_key, path, params=None):
    """One GET against the Granola Public API. Paced under 5 req/s, retries 429."""
    url = GRANOLA_API + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    for attempt in range(5):
        with _pace_lock:
            wait = 0.25 - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "occ-estimator-pm-notes/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code == 401:
                raise GranolaAuthError(f"Granola rejected the key (401): {body}")
            if e.code == 413:
                raise TranscriptTooLarge(body)
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise GranolaError(f"Granola HTTP {e.code} on {path}: {body}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise GranolaError(f"Could not reach Granola: {e}")
    raise GranolaError(f"Granola kept rate-limiting {path}")


def list_notes(api_key, updated_after, get=None):
    """Every note the key can read that changed after updated_after (oldest first)."""
    get = get or _granola_get
    notes, cursor = [], None
    for _ in range(50):  # 50 pages x 30 = far more than a PM records in a day
        params = {"updated_after": _iso_z(updated_after), "page_size": 30}
        if cursor:
            params["cursor"] = cursor
        page = get(api_key, "/notes", params)
        notes.extend(page.get("notes") or [])
        cursor = page.get("cursor")
        if not page.get("hasMore") or not cursor:
            break
    notes.sort(key=lambda n: n.get("created_at") or "")
    return notes


def fetch_note(api_key, note_id, get=None):
    """Full note with transcript. Falls back to the paged transcript on a 413."""
    get = get or _granola_get
    try:
        return get(api_key, f"/notes/{note_id}", {"include": "transcript"})
    except TranscriptTooLarge:
        note = get(api_key, f"/notes/{note_id}")
        items, cursor = [], None
        for _ in range(200):
            params = {"page_size": 100}
            if cursor:
                params["cursor"] = cursor
            page = get(api_key, f"/notes/{note_id}/transcript", params)
            items.extend(page.get("transcript") or [])
            cursor = page.get("cursor")
            if not page.get("hasMore") or not cursor:
                break
        note["transcript"] = items
        return note


# ── JobTread reads ───────────────────────────────────────────────────────────

def load_open_jobs(jt_query, org_id):
    """All open jobs with what matching needs: number, names, address, PMs, status."""
    jobs, page = [], None
    for _ in range(30):
        args = {"where": ["closedOn", None], "size": 100}
        if page:
            args["page"] = page
        resp = jt_query({"organization": {"$": {"id": org_id}, "jobs": {
            "$": args,
            "nodes": {
                "id": {}, "number": {}, "name": {},
                "location": {"address": {}, "account": {"name": {}}},
                "customFieldValues": {
                    "$": {"where": [["customField", "id"], "in", [PM_FIELD_ID, RENO_STATUS_FIELD_ID]],
                          "size": 10},
                    "nodes": {"value": {}, "customField": {"id": {}}},
                },
            },
            "nextPage": {},
        }}})
        conn = ((resp or {}).get("organization") or {}).get("jobs") or {}
        for node in conn.get("nodes") or []:
            pms, status = [], None
            for v in ((node.get("customFieldValues") or {}).get("nodes") or []):
                fid = (v.get("customField") or {}).get("id")
                if fid == PM_FIELD_ID and v.get("value"):
                    pms.append(v["value"])
                elif fid == RENO_STATUS_FIELD_ID:
                    status = v.get("value")
            loc = node.get("location") or {}
            jobs.append({
                "id": node.get("id"),
                "number": node.get("number") or "",
                "name": node.get("name") or "",
                "address": loc.get("address") or "",
                "account": (loc.get("account") or {}).get("name") or "",
                "pms": pms,
                "status": status,
            })
        page = conn.get("nextPage")
        if not page:
            break
    return jobs


def pm_active_jobs(jobs, pm_name):
    return [j for j in jobs if pm_name in j["pms"] and j["status"] == ACTIVE_STATUS]


def _find_job_by_number(jt_query, org_id, number, open_jobs):
    for j in open_jobs:
        if j["number"] == number:
            return j
    resp = jt_query({"organization": {"$": {"id": org_id}, "jobs": {
        "$": {"where": ["number", number], "size": 1},
        "nodes": {"id": {}, "number": {}, "name": {}}}}})
    nodes = (((resp or {}).get("organization") or {}).get("jobs") or {}).get("nodes") or []
    if nodes:
        n = nodes[0]
        return {"id": n["id"], "number": n["number"], "name": n.get("name") or ""}
    return None


def already_filed(jt_query, org_id, note_id):
    """True when a Daily Log for this Granola note already exists (store-independent)."""
    resp = jt_query({"organization": {"$": {"id": org_id}, "dailyLogs": {
        "$": {"where": ["notes", "like", f"%{NOTE_ID_MARKER}{note_id}%"], "size": 1},
        "count": {}}}})
    conn = ((resp or {}).get("organization") or {}).get("dailyLogs") or {}
    return (conn.get("count") or 0) > 0


def notes_for_job(jt_query, job_id, start, end):
    """The PM-NOTE Daily Logs on a job between two dates (for the Friday update)."""
    resp = jt_query({"job": {"$": {"id": job_id}, "dailyLogs": {
        "$": {"where": {"and": [["notes", "like", f"%{NOTE_TAG}%"],
                                ["date", ">=", str(start)], ["date", "<=", str(end)]]},
              "size": 100, "sortBy": [{"field": "date"}]},
        "nodes": {"id": {}, "date": {}, "notes": {}}}}})
    return (((resp or {}).get("job") or {}).get("dailyLogs") or {}).get("nodes") or []


# ── Matching ─────────────────────────────────────────────────────────────────

def job_signals(job):
    """Names and street phrases that identify a job, each flagged common or not."""
    names = set()
    words = re.findall(r"[A-Za-z][A-Za-z'’-]+", job.get("account") or "")
    if words:
        names.add(words[-1])                # last name on the account
    for w in re.findall(r"[A-Za-z][A-Za-z'’-]+", job.get("name") or ""):
        names.add(w)
    names = {n.lower().replace("'", "").replace("’", "") for n in names}
    names = {n for n in names if len(n) >= 3 and n not in NAME_STOPWORDS}

    # Street: "46 woodvale" and "spring forest" are strong; a lone street word
    # ("latour") is weak, because two jobs can sit on the same street.
    strong_streets, weak_streets = set(), set()
    first_line = re.split(r"[\n,]", job.get("address") or "")[0]
    tokens = _norm(first_line).split()
    num = tokens[0] if tokens and tokens[0].isdigit() else None
    rest = tokens[1:] if num else tokens
    street_words = [t for t in rest if t not in STREET_SUFFIXES]
    phrase = " ".join(street_words)
    if num and phrase:
        strong_streets.add(f"{num} {phrase}")
    if len(street_words) >= 2:
        weak_streets.add(phrase)
    for t in street_words:
        if t not in GENERIC_STREET_WORDS and len(t) >= 5 and not t.isdigit():
            weak_streets.add(t)
    return {"names": names, "strong_streets": strong_streets, "weak_streets": weak_streets}


_JOB_NUMBER_RES = (
    re.compile(r"(?<!\d)(\d{5})(?!\d)"),
    re.compile(r"\b(?:job|jobs|number|no|#)\s*(?:number|no|#)?\s*#?\s*(\d{2,5})(?!\d)", re.I),
)


def job_numbers_in(text):
    found = set()
    for rx in _JOB_NUMBER_RES:
        for m in rx.finditer(text or ""):
            found.add(m.group(1).zfill(5))
    return found


def _jobs_named_in(text, jobs, allow_common):
    """{job number: (job, reasons, strong)} for every job the text points at.

    strong = a client name, house number + street, or the job number. A lone
    street word is weak.
    """
    norm = _norm(text)
    numbers = job_numbers_in(text)
    hits = {}
    for j in jobs:
        sig = job_signals(j)
        why, strong = [], False
        for n in sorted(sig["names"]):
            if n in COMMON_WORD_NAMES and not allow_common:
                continue
            if _has_phrase(norm, n) or _has_phrase(norm, n + "s"):
                why.append(f'"{n}"')
                strong = True
        for s in sorted(sig["strong_streets"]):
            if _has_phrase(norm, s):
                why.append(f'"{s}"')
                strong = True
        for s in sorted(sig["weak_streets"]):
            if _has_phrase(norm, s):
                why.append(f'"{s}"')
        if j["number"] in numbers:
            why.append(f"job {j['number']}")
            strong = True
        if why:
            hits[j["number"]] = (j, why, strong)
    return hits


def first_minute_text(transcript):
    items = transcript or []
    if not items:
        return ""
    start = _parse_dt(items[0].get("start_time"))
    if start is None:
        return " ".join((i.get("text") or "") for i in items)[:1200]
    out = []
    for i in items:
        t = _parse_dt(i.get("start_time"))
        if t is not None and (t - start).total_seconds() > FIRST_MINUTE_SECONDS:
            break
        out.append(i.get("text") or "")
    return " ".join(out)


def match_note(title, first_minute, pm_jobs, pm_name):
    """Return {"job": job or None, "how": str, "candidates": [jobs]}."""
    title = title or ""
    first = _first_name(pm_name)

    def decide(hits, where, number_text):
        if len(hits) > 1:
            strong = {num: h for num, h in hits.items() if h[2]}
            if len(strong) == 1:
                hits = strong
        if len(hits) == 1:
            job, why, _ = next(iter(hits.values()))
            return {"job": job, "how": f"{', '.join(why)} in the {where}", "candidates": [job]}
        said = job_numbers_in(number_text)
        narrowed = [h for num, h in hits.items() if num in said]
        if len(narrowed) == 1:
            job = narrowed[0][0]
            return {"job": job, "how": f"job number {job['number']} in the {where}",
                    "candidates": [h[0] for h in hits.values()]}
        return None

    title_hits = _jobs_named_in(title, pm_jobs, allow_common=True)
    if title_hits:
        got = decide(title_hits, "note title", title + " " + (first_minute or ""))
        if got:
            return got
        names = ", ".join(f"{h[0]['number']} {h[0]['name']}" for h in title_hits.values())
        return {"job": None, "candidates": [h[0] for h in title_hits.values()],
                "how": f"the title fits more than one of {first}'s jobs ({names}) and no job number was given"}

    combined = title + "\n" + (first_minute or "")
    hits = _jobs_named_in(first_minute or "", pm_jobs, allow_common=False)
    for num, h in _jobs_named_in(title, pm_jobs, allow_common=True).items():
        hits.setdefault(num, h)
    if hits:
        got = decide(hits, "first minute of the recording", combined)
        if got:
            return got
        names = ", ".join(f"{h[0]['number']} {h[0]['name']}" for h in hits.values())
        return {"job": None, "candidates": [h[0] for h in hits.values()],
                "how": f"the first minute mentions more than one of {first}'s jobs ({names}) and no job number was given"}

    others = sorted(job_numbers_in(combined) - {j["number"] for j in pm_jobs})
    if others:
        return {"job": None, "candidates": [],
                "how": f"mentions job {', '.join(others)}, which isn't one of {first}'s active jobs"}
    return {"job": None, "candidates": [],
            "how": f"no client name, street or job number from {first}'s active jobs in the title or first minute"}


# ── Formatting the Daily Logs ────────────────────────────────────────────────

def _speaker(item, pm_name):
    sp = item.get("speaker") or {}
    if sp.get("name"):
        return sp["name"]
    if sp.get("diarization_label"):
        return sp["diarization_label"]
    if sp.get("attribution") == "me" or (sp.get("source") == "microphone" and not sp.get("attribution")):
        return _first_name(pm_name)
    return "Other"


def transcript_lines(transcript, pm_name):
    items = transcript or []
    start = _parse_dt(items[0].get("start_time")) if items else None
    lines, last_speaker = [], None
    for i in items:
        text = (i.get("text") or "").strip()
        if not text:
            continue
        who = _speaker(i, pm_name)
        if who == last_speaker and lines:
            lines[-1] += " " + text
            continue
        t = _parse_dt(i.get("start_time"))
        secs = int((t - start).total_seconds()) if (t and start) else 0
        lines.append(f"[{secs // 60}:{secs % 60:02d}] {who}: {text}")
        last_speaker = who
    return lines


def _chunks(lines, first_room, room):
    """Split transcript lines into pieces that fit (first piece has less room)."""
    pieces, cur, limit = [], "", first_room
    for line in lines:
        while len(line) > room:            # one enormous line: hard-split it
            head, line = line[:room - 1], line[room - 1:]
            if cur:
                pieces.append(cur)
                cur, limit = "", room
            pieces.append(head)
        add = (("\n" if cur else "") + line)
        if len(cur) + len(add) > limit:
            pieces.append(cur)
            cur, limit = line, room
        else:
            cur += add
    if cur:
        pieces.append(cur)
    return pieces


def build_daily_logs(note, pm_name, how):
    """The Daily Log texts for one note: [part 1 (header + summary + transcript start), part 2, ...]."""
    note_id = note.get("id")
    recorded = _parse_dt((note.get("calendar_event") or {}).get("scheduled_start_time")) \
        or _parse_dt(note.get("created_at"))
    first = _first_name(pm_name)
    summary = (note.get("summary_text") or note.get("summary_markdown") or "").strip() or "(no summary)"
    typed = (note.get("private_notes_text") or "").strip()

    header = (
        f"{NOTE_TAG} Granola note from {pm_name} - internal only, not visible to the client\n"
        f"Recorded: {_fmt_local(recorded)}\n"
        f"Title: {note.get('title') or 'Untitled'}\n"
        f"Filed here because: {how}\n"
        f"{NOTE_ID_MARKER}{note_id} (only {first} can open it in Granola)\n"
    )
    body = f"\nSUMMARY\n{summary}\n"
    if typed:
        body += f"\n{first.upper()}'S TYPED NOTES\n{typed}\n"
    lines = transcript_lines(note.get("transcript"), pm_name)

    part_tag = f"{NOTE_TAG} Granola note {note_id} - transcript part {{n}} of {{total}}\n\n"
    tail_room = DAILY_LOG_LIMIT - len(part_tag) - 40
    first_text = header + body
    if len(first_text) > DAILY_LOG_LIMIT - 400:
        first_text = first_text[:DAILY_LOG_LIMIT - 400] + "\n(summary cut short)\n"
    if not lines:
        return [first_text + "\nTRANSCRIPT\n(none)"]

    first_room = DAILY_LOG_LIMIT - len(first_text) - 80
    pieces = _chunks(lines, max(first_room, 0), tail_room)
    if pieces and first_room < 500:
        pieces = [""] + _chunks(lines, tail_room, tail_room)
    truncated = len(pieces) > MAX_TRANSCRIPT_PARTS
    pieces = pieces[:MAX_TRANSCRIPT_PARTS]
    total = len(pieces)

    logs = []
    for n, piece in enumerate(pieces, start=1):
        label = f"TRANSCRIPT (part {n} of {total})" if total > 1 else "TRANSCRIPT"
        if n == 1:
            text = first_text + f"\n{label}\n" + (piece or "(continues in part 2)")
        else:
            text = part_tag.format(n=n, total=total) + piece
        if truncated and n == total:
            text += f"\n\n(transcript continues - the rest is only in {first}'s Granola)"
        logs.append(text[:DAILY_LOG_LIMIT])
    return logs


def log_date(note):
    recorded = _parse_dt((note.get("calendar_event") or {}).get("scheduled_start_time")) \
        or _parse_dt(note.get("created_at"))
    loc = _local(recorded)
    return (loc.date() if loc else date.today()).isoformat()


# ── Store helpers ────────────────────────────────────────────────────────────

def _store_get(key, default=None):
    try:
        val = scope_store.get(key)
    except Exception as e:
        print(f"  pm-notes: store read failed for {key}: {e}")
        return default
    return default if val is None else val


def _store_put(key, value):
    try:
        scope_store.put(key, value)
    except Exception as e:
        print(f"  pm-notes: store write failed for {key}: {e}")


# ── To-dos for Jason ─────────────────────────────────────────────────────────

def _todo(jt_query, org_id, name, description, due):
    resp = _mutate(jt_query, {"createTask": {
        "$": {
            "targetType": "organization", "targetId": org_id,
            "name": name[:200], "description": description[:TASK_DESCRIPTION_LIMIT - 10],
            "startDate": due, "endDate": due,
        },
        "createdTask": {"id": {}},
    }})
    return (((resp or {}).get("createTask") or {}).get("createdTask") or {}).get("id")


def _alert_once(jt_query, org_id, state, key, name, description, due, report):
    alerts = state.setdefault("alerts", {})
    if key in alerts:
        return
    try:
        _todo(jt_query, org_id, name, description, due)
        alerts[key] = date.today().isoformat()
        report["alerts"].append(name)
    except Exception as e:
        report["errors"].append(f"alert to-do failed ({name}): {e}")


NEW_KEY_STEPS = (
    "How to replace it:\n"
    "1. {first} opens the Granola desktop app: Settings > Connectors > API keys > Create new key, "
    "Personal notes scope, and sends you the key.\n"
    "2. In Render (occ-estimator > Environment), paste it into {env} and set {env}_EXPIRES to the new "
    "expiry date (YYYY-MM-DD). Save - Render redeploys on its own.\n"
    "3. Revoke the old key in Granola once the new one is in."
)


def check_key(jt_query, org_id, state, pm_name, slug, today, report):
    """Expiry and missing-key warnings. Returns the key or None."""
    env = f"GRANOLA_KEY_{slug}"
    first = _first_name(pm_name)
    key = _env(env)
    due = today.isoformat()
    steps = NEW_KEY_STEPS.format(first=first, env=env)
    if not key:
        report["pms"][pm_name]["key"] = "missing"
        if today >= start_date():
            _alert_once(jt_query, org_id, state, f"missing:{slug}",
                        f"Granola pull: {first}'s API key isn't in Render yet",
                        f"The nightly PM notes pull has no {env} in Render, so none of {first}'s Granola "
                        f"notes are being filed.\n\n{steps}", due, report)
        return None
    report["pms"][pm_name]["key"] = "set"
    raw_exp = _env(env + "_EXPIRES")
    if raw_exp:
        try:
            expires = date.fromisoformat(raw_exp)
        except ValueError:
            report["errors"].append(f"{env}_EXPIRES is not a YYYY-MM-DD date: {raw_exp!r}")
            return key
        days = (expires - today).days
        report["pms"][pm_name]["key_expires"] = raw_exp
        report["pms"][pm_name]["days_left"] = days
        for threshold in EXPIRY_WARN_DAYS:
            if days <= threshold:
                when = "has expired" if days < 0 else f"expires {expires.strftime('%b')} {expires.day}"
                _alert_once(jt_query, org_id, state, f"expiry:{slug}:{raw_exp}:{threshold}",
                            f"Granola pull: {first}'s API key {when} - get a new one",
                            f"{first}'s Granola Personal API key {when} ({days} days). After that, {first}'s notes "
                            f"stop getting filed on her jobs.\n\n{steps}", due, report)
                break
    return key


# ── Filing ───────────────────────────────────────────────────────────────────

def file_note(jt_query, org_id, note, pm_name, job, how):
    """Write the Daily Logs. Returns the number written (0 if already there)."""
    if already_filed(jt_query, org_id, note["id"]):
        return 0
    written = 0
    for text in build_daily_logs(note, pm_name, how):
        _mutate(jt_query, {"createDailyLog": {
            "$": {"jobId": job["id"], "date": log_date(note), "notes": text},
            "createdDailyLog": {"id": {}},
        }})
        written += 1
    return written


def _close_todo(jt_query, task_id):
    if not task_id:
        return
    try:
        _mutate(jt_query, {"updateTask": {"$": {"id": task_id, "progress": 1, "notify": False}}})
    except Exception as e:
        print(f"  pm-notes: could not close to-do {task_id}: {e}")


def needs_call_todo(jt_query, org_id, note, pm_name, match, pm_jobs, today):
    first = _first_name(pm_name)
    recorded = _parse_dt(note.get("created_at"))
    loc = _local(recorded)
    short = f"{loc.strftime('%b')} {loc.day}" if loc else "?"
    title = note.get("title") or "Untitled"
    jobs_list = "\n".join(f"- {j['number']} {j['name']}" for j in pm_jobs) or "- (none found)"
    summary = (note.get("summary_text") or note.get("summary_markdown") or "").strip()
    desc = (
        f"{pm_name} recorded this {_fmt_local(recorded)} and the pull couldn't tell which job it's for.\n"
        f"Why: {match['how']}.\n\n"
        f"{first}'s active jobs:\n{jobs_list}\n\n"
        "To file it: add the job number to this to-do's name (for example 00450) and check it off. "
        "It's filed on that job at the next run. Not a job note (team meeting, personal)? Just check it "
        "off and it's left alone.\n\n"
        f"Granola note id: {note['id']}\n\nSummary:\n"
    )
    desc += summary[:TASK_DESCRIPTION_LIMIT - len(desc) - 20] or "(no summary)"
    name = f"Granola note needs a job: {title} ({first}, {short})"
    return _todo(jt_query, org_id, name, desc, today.isoformat())


def process_note(jt_query, org_id, api_key, summary_row, pm_name, pm_jobs, today, report, get=None):
    note_id = summary_row["id"]
    rec = _store_get(_note_key(note_id), {}) or {}
    if rec.get("status") in ("filed", "skipped"):
        return
    note = fetch_note(api_key, note_id, get=get)
    match = match_note(note.get("title"), first_minute_text(note.get("transcript")), pm_jobs, pm_name)
    base = {"pm": pm_name, "title": note.get("title"), "created_at": note.get("created_at")}

    if match["job"]:
        job = match["job"]
        n = file_note(jt_query, org_id, note, pm_name, job, match["how"])
        _close_todo(jt_query, rec.get("todo_id"))
        _store_put(_note_key(note_id), {**base, "status": "filed", "job": job["number"],
                                        "job_id": job["id"], "logs": n})
        report["filed"].append(f"{pm_name}: {note.get('title') or 'Untitled'} -> {job['number']}"
                               + ("" if n else " (already there)"))
        return

    if rec.get("status") == "needs_call":
        return  # already on Jason's list; re-checked each time the note changes
    todo_id = needs_call_todo(jt_query, org_id, note, pm_name, match, pm_jobs, today)
    _store_put(_note_key(note_id), {**base, "status": "needs_call", "todo_id": todo_id,
                                    "why": match["how"]})
    pending = _store_get(STORE_PENDING, []) or []
    if note_id not in pending:
        pending.append(note_id)
        _store_put(STORE_PENDING, pending)
    report["needs_call"].append(f"{pm_name}: {note.get('title') or 'Untitled'} ({match['how']})")


def resolve_pending(jt_query, org_id, open_jobs, keys, report, get=None):
    """Act on "needs your call" to-dos Jason has checked off."""
    pending = _store_get(STORE_PENDING, []) or []
    keep = []
    for note_id in pending:
        rec = _store_get(_note_key(note_id), {}) or {}
        if rec.get("status") != "needs_call":
            continue
        task = None
        if rec.get("todo_id"):
            try:
                resp = jt_query({"task": {"$": {"id": rec["todo_id"]}, "id": {}, "name": {}, "progress": {}}})
                task = (resp or {}).get("task")
            except Exception as e:
                if "unknown" not in str(e).lower() and "permission" not in str(e).lower():
                    report["errors"].append(f"could not read to-do for {note_id}: {e}")
                    keep.append(note_id)
                    continue
        if task is None:
            rec["status"] = "skipped"
            rec["why"] = "to-do was deleted"
            _store_put(_note_key(note_id), rec)
            continue
        if task.get("progress") != 1:
            keep.append(note_id)
            continue
        numbers = sorted(job_numbers_in(task.get("name") or ""))
        if not numbers:
            rec["status"] = "skipped"
            _store_put(_note_key(note_id), rec)
            report["skipped"].append(f"{rec.get('pm')}: {rec.get('title') or 'Untitled'} (checked off, no job number)")
            continue
        job = _find_job_by_number(jt_query, org_id, numbers[0], open_jobs)
        key = keys.get(rec.get("pm"))
        if not job or not key:
            report["errors"].append(f"could not file {note_id}: "
                                    + ("no job " + numbers[0] if not job else "no Granola key for " + str(rec.get("pm"))))
            keep.append(note_id)
            continue
        try:
            note = fetch_note(key, note_id, get=get)
            n = file_note(jt_query, org_id, note, rec["pm"], job, "Jason picked the job from the to-do")
        except Exception as e:
            report["errors"].append(f"could not file {note_id} on {numbers[0]}: {e}")
            keep.append(note_id)
            continue
        rec.update({"status": "filed", "job": job["number"], "job_id": job["id"], "logs": n})
        _store_put(_note_key(note_id), rec)
        report["filed"].append(f"{rec.get('pm')}: {rec.get('title') or 'Untitled'} -> {job['number']} (your pick)")
    _store_put(STORE_PENDING, keep)


# ── The run ──────────────────────────────────────────────────────────────────

def run(jt_query, org_id, now=None, get=None):
    """One full pull for every pilot PM. Returns a report dict (also stored)."""
    now = now or datetime.now(timezone.utc)
    today = _local(now).date()
    report = {"started_at": _iso_z(now), "store": scope_store.kind(), "pms": {},
              "filed": [], "needs_call": [], "skipped": [], "alerts": [], "errors": []}
    state = _store_get(STORE_STATE, {}) or {}
    floor = datetime.combine(start_date(), datetime.min.time(), tzinfo=LOCAL_TZ)

    try:
        open_jobs = load_open_jobs(jt_query, org_id)
    except Exception as e:
        report["errors"].append(f"could not load JobTread jobs: {e}")
        _store_put(STORE_LAST_REPORT, report)
        return report

    keys = {}
    for pm_name, slug in PILOT_PMS:
        report["pms"][pm_name] = {"notes_seen": 0}
        key = check_key(jt_query, org_id, state, pm_name, slug, today, report)
        if not key:
            continue
        keys[pm_name] = key
        pm_jobs = pm_active_jobs(open_jobs, pm_name)
        report["pms"][pm_name]["active_jobs"] = [j["number"] for j in pm_jobs]
        pm_state = state.setdefault("pms", {}).setdefault(slug, {})
        last = _parse_dt(pm_state.get("last_success"))
        since = max(floor, (last - OVERLAP) if last else floor)
        try:
            rows = list_notes(key, since, get=get)
        except GranolaAuthError as e:
            _alert_once(jt_query, org_id, state, f"broken:{slug}:{_fingerprint(key)}",
                        f"Granola pull: {_first_name(pm_name)}'s API key stopped working",
                        f"Granola rejected GRANOLA_KEY_{slug} ({e}). {_first_name(pm_name)}'s notes aren't filed until it's "
                        f"replaced.\n\n" + NEW_KEY_STEPS.format(first=_first_name(pm_name), env=f"GRANOLA_KEY_{slug}"),
                        today.isoformat(), report)
            report["pms"][pm_name]["key"] = "rejected"
            continue
        except Exception as e:
            report["errors"].append(f"{pm_name}: could not list notes: {e}")
            continue
        ok = True
        for row in rows:
            if row.get("deleted_at"):
                continue
            created = _parse_dt(row.get("created_at"))
            if created and created < floor:
                continue
            report["pms"][pm_name]["notes_seen"] += 1
            try:
                process_note(jt_query, org_id, key, row, pm_name, pm_jobs, today, report, get=get)
            except ForbiddenMutation:
                raise
            except Exception as e:
                ok = False
                report["errors"].append(f"{pm_name}: note {row.get('id')}: {e}")
                traceback.print_exc()
        if ok:
            pm_state["last_success"] = _iso_z(now)

    try:
        resolve_pending(jt_query, org_id, open_jobs, keys, report, get=get)
    except Exception as e:
        report["errors"].append(f"resolving checked-off to-dos failed: {e}")

    report["finished_at"] = _iso_z(datetime.now(timezone.utc))
    _store_put(STORE_STATE, state)
    _store_put(STORE_LAST_REPORT, report)
    print(f"  pm-notes run: filed {len(report['filed'])}, needs your call {len(report['needs_call'])}, "
          f"alerts {len(report['alerts'])}, errors {len(report['errors'])}")
    for line in report["filed"] + report["needs_call"] + report["errors"]:
        print(f"    {line}")
    return report


# ── HTTP entry points (called from app.py) ───────────────────────────────────

_run_lock = threading.Lock()


def _authorized(params):
    need = _env("PM_NOTES_RUN_KEY")
    return not need or (params.get("key") or [""])[0] == need


def handle_run_request(params, jt_query, org_id, start_thread=None):
    if not _authorized(params):
        return 403, {"error": "wrong or missing key"}
    if not _run_lock.acquire(blocking=False):
        return 409, {"status": "a PM notes run is already going"}

    def work():
        try:
            run(jt_query, org_id)
        except Exception as e:
            print(f"  pm-notes run crashed: {e}")
            traceback.print_exc()
        finally:
            _run_lock.release()

    if start_thread:
        start_thread(work)
    else:
        t = threading.Thread(target=work, daemon=True)
        t.start()
    return 202, {"status": "PM notes run started - results go to the Render log and /pm-notes/status"}


def handle_status_request(params):
    if not _authorized(params):
        return 403, {"error": "wrong or missing key"}
    today = _local(datetime.now(timezone.utc)).date()
    keys = {}
    for pm_name, slug in PILOT_PMS:
        info = {"key_set": bool(_env(f"GRANOLA_KEY_{slug}"))}
        raw = _env(f"GRANOLA_KEY_{slug}_EXPIRES")
        if raw:
            try:
                info["days_until_expiry"] = (date.fromisoformat(raw) - today).days
            except ValueError:
                info["days_until_expiry"] = f"bad date {raw!r}"
        keys[pm_name] = info
    last = _store_get(STORE_LAST_REPORT, {}) or {}
    pending = _store_get(STORE_PENDING, []) or []
    return 200, {
        "store": scope_store.kind(),
        "start_date": start_date().isoformat(),
        "keys": keys,
        "waiting_on_your_call": len(pending),
        "last_run": {
            "started_at": last.get("started_at"),
            "finished_at": last.get("finished_at"),
            "filed": len(last.get("filed") or []),
            "needs_call": len(last.get("needs_call") or []),
            "alerts": len(last.get("alerts") or []),
            "errors": last.get("errors") or [],
        },
    }
