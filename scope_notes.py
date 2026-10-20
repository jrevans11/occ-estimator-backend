"""Site Scope Tool - dictated site notes (phase 3).

Jason records a note on the scope page (sales-lead-tool /intake/scope). The
phone uploads the audio to Vercel Blob, and Vercel forwards the note here.
This module then, on a background thread:

1. sends the audio link to Deepgram for text (scope_transcribe.py), and
2. adds the text word-for-word, under a date/time heading, to the project's
   one "Site Visit Notes" CompanyCam Page, creating the Page on first use.

Because the work happens here after the phone hands off, a dropped signal or
closed page after Stop still gets the note saved. The page polls
GET /scope/notes/status for the text; if Jason fixes a misheard number, POST
/scope/notes/fix swaps the text on the Page.

Endpoints (registered in app.py; all need X-Scope-Key):
    POST /scope/notes          {note_id, job, audio_url | text, started_at, saved_at}
    GET  /scope/notes/status?id=<note_id>
    POST /scope/notes/fix      {job, heading, old_text, new_text}
    POST /scope/runs...        phase 4 runs - handed to scope_runs.py
    GET  /scope/notes-test?key=..&job=..&text=..   typed test note, from a browser

Note status lives in memory only (a few seconds per note). The lasting record
is the audio in Vercel Blob plus the text on the CompanyCam Page; run state
in a database waits for phase 4.
"""

import json
import re
import threading
import time
import traceback
from urllib.parse import urlparse

import companycam_api as cc
import scope_runs
import scope_transcribe
import site_scope

NOTES_TITLE = site_scope.SITE_VISIT_NOTES_TITLE
# Audio has to come from the sales-lead-tool's Vercel Blob store.
AUDIO_HOST_SUFFIX = ".public.blob.vercel-storage.com"
NOTE_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MAX_TEXT_CHARS = 20000
KEEP_SECONDS = 24 * 60 * 60   # forget a note's status after a day
MAX_KEPT = 500
PAGE_INTRO = ("Word-for-word notes dictated on site visits, added by the OCC scope page. "
              "Each note is under the time it was started; the newest is at the bottom.")
META_PREFIXES = ("Recorded ", "Typed ")

_notes = {}
_notes_lock = threading.Lock()
_project_locks = {}
_project_locks_lock = threading.Lock()


class NoteError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ── Formatting ───────────────────────────────────────────────────────────────

def _clock(ts, seconds=False):
    t = site_scope.to_local(ts)
    return t.strftime("%I:%M:%S %p" if seconds else "%I:%M %p").lstrip("0")


def note_heading(started_ts):
    t = site_scope.to_local(started_ts)
    return f"{t.strftime('%b')} {t.day}, {t.year} - {_clock(started_ts)}"


def _duration(seconds):
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _text(s, marks=None):
    node = {"type": "text", "text": s}
    if marks:
        node["marks"] = marks
    return node


def _paragraph(*parts):
    return {"type": "paragraph", "content": list(parts)} if parts else {"type": "paragraph"}


def text_paragraphs(text):
    """One paragraph node per blank-line-separated block."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    return [_paragraph(_text(" ".join(b.split()))) for b in blocks]


def note_nodes(heading, text, started_ts, saved_ts, duration=None, audio_url=None, link_audio=True):
    """The TipTap nodes for one note: heading, an italic "when" line, the text."""
    italic = [{"type": "italic"}]
    if audio_url:
        span = f"Recorded {_clock(started_ts, True)} to {_clock(saved_ts, True)}"
        if duration:
            span += f" ({_duration(duration)} of audio)"
        meta = [_text(span + ". ", italic)]
        if link_audio:
            meta.append(_text("Play the recording", italic + [{"type": "link", "attrs": {"href": audio_url}}]))
        else:
            meta.append(_text(f"Recording: {audio_url}", italic))
    else:
        meta = [_text(f"Typed note, saved {_clock(saved_ts, True)}.", italic)]
    return ([{"type": "heading", "attrs": {"level": 2}, "content": [_text(heading)]}, _paragraph(*meta)]
            + text_paragraphs(text))


def node_text(node):
    if node.get("type") == "text":
        return node.get("text") or ""
    return "".join(node_text(c) for c in node.get("content") or [])


def _norm(s):
    return " ".join((s or "").split())


# ── The Site Visit Notes Page ────────────────────────────────────────────────

def _project_lock(project_id):
    """Notes are added by read-modify-write, so one at a time per project."""
    with _project_locks_lock:
        return _project_locks.setdefault(str(project_id), threading.Lock())


def find_notes_page(project_id):
    for pg in cc.list_project_pages(project_id):
        if (pg.get("title") or "").strip().lower() == NOTES_TITLE.lower():
            return pg
    return None


def _write(project_id, add_nodes):
    """Append nodes to the notes Page (creating it if needed). Returns the page id."""
    page = find_notes_page(project_id)
    if page:
        doc = cc.get_page(page["id"]).get("tiptap_content") or {}
        if doc.get("type") != "doc":
            doc = {"type": "doc", "content": []}
        doc["content"] = list(doc.get("content") or []) + add_nodes
        cc.update_page(page["id"], doc)
        return str(page["id"])
    intro = _paragraph(_text(PAGE_INTRO, [{"type": "italic"}]))
    created = cc.create_page(project_id, NOTES_TITLE, {"type": "doc", "content": [intro] + add_nodes})
    return str(created.get("id") or "")


def append_note(project_id, heading, text, started_ts, saved_ts, duration=None, audio_url=None):
    """Add one note to the project's Site Visit Notes Page. If CompanyCam
    refuses the audio link, the link goes in as plain text instead."""
    with _project_lock(project_id):
        try:
            return _write(project_id, note_nodes(heading, text, started_ts, saved_ts, duration, audio_url))
        except cc.CompanyCamError as e:
            if not audio_url or e.status not in (400, 422):
                raise
            print(f"Site Visit Notes: CompanyCam refused the audio link ({e}); saving it as plain text")
            return _write(project_id, note_nodes(heading, text, started_ts, saved_ts, duration, audio_url,
                                                 link_audio=False))


def replace_note_text(project_id, heading, old_text, new_text, now=None):
    """Swap a note's text on the Page. Finds the newest note under `heading`
    whose text still matches old_text. If someone has since edited it in
    CompanyCam, the fix is added as a new note instead, so nothing is lost.
    Returns "replaced" or "added"."""
    now = time.time() if now is None else now
    with _project_lock(project_id):
        page = find_notes_page(project_id)
        if not page:
            raise NoteError("This project has no Site Visit Notes Page yet.", 404)
        doc = cc.get_page(page["id"]).get("tiptap_content") or {}
        content = list(doc.get("content") or [])
        starts = [i for i, n in enumerate(content)
                  if n.get("type") == "heading" and _norm(node_text(n)) == _norm(heading)]
        for h in reversed(starts):
            end = next((i for i in range(h + 1, len(content)) if content[i].get("type") == "heading"), len(content))
            body_start = h + 1
            if body_start < end and node_text(content[body_start]).startswith(META_PREFIXES):
                body_start += 1
            body = content[body_start:end]
            if _norm(" ".join(node_text(n) for n in body)) == _norm(old_text):
                doc["content"] = content[:body_start] + text_paragraphs(new_text) + content[end:]
                cc.update_page(page["id"], doc)
                return "replaced"
        doc["content"] = content + note_nodes(f"{heading} (corrected)", new_text, now, now)
        cc.update_page(page["id"], doc)
        return "added"


# ── Note status (in memory) ──────────────────────────────────────────────────

def _forget_old(now):
    for nid in [n for n, r in _notes.items() if now - r["created"] > KEEP_SECONDS]:
        del _notes[nid]
    while len(_notes) > MAX_KEPT:
        del _notes[min(_notes, key=lambda n: _notes[n]["created"])]


def _public(record):
    return {"ok": True, **{k: record.get(k) for k in (
        "note_id", "status", "text", "heading", "error", "job_number", "project_id", "page_id",
        "duration_seconds", "page_result")}}


def _update(note_id, **fields):
    with _notes_lock:
        if note_id in _notes:
            _notes[note_id].update(fields)


def note_status(note_id):
    with _notes_lock:
        record = _notes.get(note_id)
        return _public(record) if record else None


def _process(note_id):
    """Background: transcribe, then write to the Page. Never raises."""
    with _notes_lock:
        r = dict(_notes[note_id])
    try:
        text, duration = r["text"], None
        if r["audio_url"]:
            text, duration = scope_transcribe.transcribe_url(r["audio_url"])
            if not text.strip():
                _update(note_id, status="failed", text="", duration_seconds=duration,
                        error="No speech was heard in the recording. Record it again.")
                print(f"Site Visit Notes {note_id}: no speech in {r['audio_url']}")
                return
            _update(note_id, status="saving", text=text, duration_seconds=duration)
        page_id = append_note(r["project_id"], r["heading"], text, r["started_ts"], r["saved_ts"],
                              duration, r["audio_url"])
        _update(note_id, status="saved", page_id=page_id)
        print(f"Site Visit Notes {note_id}: saved to page {page_id} on project {r['project_id']} "
              f"(job {r['job_number']}, {len(text)} chars)")
    except Exception as e:
        _update(note_id, status="failed", error=str(e))
        print(f"Site Visit Notes {note_id} failed: {e}\n{traceback.format_exc()}")


def _start_thread(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _timestamp(value, fallback):
    ts = cc.parse_timestamp(value)
    return ts if ts is not None else fallback


def _project_for_job(job_ref, jobtread_query, org_id):
    job = site_scope.find_job(job_ref, jobtread_query, org_id)
    project_id = (job.get("companycamId") or "").strip()
    if not project_id:
        raise NoteError(f"Job {job.get('number')} has no CompanyCam project linked yet.", 404)
    return job, project_id


def submit_note(payload, jobtread_query, org_id, start_thread=None, now=None):
    """Accept a note and start its background work. Returns (status, body).
    Sending the same note_id again (a retry) returns its current status."""
    now = time.time() if now is None else now
    note_id = str(payload.get("note_id") or "").strip()
    if not NOTE_ID_RE.match(note_id):
        raise NoteError("note_id is missing or not a valid id.")
    existing = note_status(note_id)
    if existing:
        return 200, existing

    audio_url = str(payload.get("audio_url") or "").strip()
    text = str(payload.get("text") or "").strip()
    if audio_url:
        u = urlparse(audio_url)
        if u.scheme != "https" or not (u.hostname or "").endswith(AUDIO_HOST_SUFFIX):
            raise NoteError("audio_url must be a Vercel Blob link.")
        if not scope_transcribe.is_configured():
            raise NoteError("DEEPGRAM_API_KEY is not set on this server.", 503)
        text = ""
    elif not text:
        raise NoteError("Send audio_url or text.")
    if len(text) > MAX_TEXT_CHARS:
        raise NoteError("That note is too long.")
    if not cc.is_configured():
        raise NoteError("COMPANYCAM_API_KEY is not set on this server.", 503)

    job, project_id = _project_for_job(payload.get("job"), jobtread_query, org_id)
    saved_ts = _timestamp(payload.get("saved_at"), int(now))
    started_ts = _timestamp(payload.get("started_at"), saved_ts)
    record = {
        "note_id": note_id, "created": now, "status": "transcribing" if audio_url else "saving",
        "audio_url": audio_url, "text": text, "error": None,
        "job_number": job.get("number"), "project_id": project_id, "page_id": None,
        "started_ts": started_ts, "saved_ts": saved_ts, "heading": note_heading(started_ts),
        "duration_seconds": None,
    }
    with _notes_lock:
        if note_id in _notes:     # a retry raced this one
            return 200, _public(_notes[note_id])
        _forget_old(now)
        _notes[note_id] = record
        reply = _public(record)   # before the thread starts changing it
    (start_thread or _start_thread)(_process, note_id)
    return 202, reply


def fix_note(payload, jobtread_query, org_id):
    new_text = str(payload.get("new_text") or "").strip()
    heading = str(payload.get("heading") or "").strip()
    if not new_text or not heading:
        raise NoteError("Send heading and new_text.")
    if len(new_text) > MAX_TEXT_CHARS:
        raise NoteError("That note is too long.")
    _, project_id = _project_for_job(payload.get("job"), jobtread_query, org_id)
    result = replace_note_text(project_id, heading, payload.get("old_text") or "", new_text)
    note_id = str(payload.get("note_id") or "")
    if note_id:
        _update(note_id, text=new_text, page_result=result)
    return {"ok": True, "result": result}


# ── HTTP glue ────────────────────────────────────────────────────────────────

def _guard(fn):
    try:
        return fn()
    except (site_scope.PreviewError, NoteError) as e:
        return e.status, {"ok": False, "error": str(e)}
    except cc.CompanyCamError as e:
        return 502, {"ok": False, "error": str(e)}
    except Exception as e:
        print(f"Site Visit Notes request failed: {e}\n{traceback.format_exc()}")
        return 500, {"ok": False, "error": f"Unexpected error: {e}"}


def handle_post(path, body, headers, jobtread_query, org_id, start_thread=None):
    """POST /scope/notes and /scope/notes/fix. Returns (status, body dict)."""
    def run():
        site_scope.check_api_key(headers.get("X-Scope-Key"))
        try:
            payload = json.loads(body or "{}")
        except ValueError:
            raise NoteError("Body must be JSON.")
        if not isinstance(payload, dict):
            raise NoteError("Body must be a JSON object.")
        if path == "/scope/notes":
            return submit_note(payload, jobtread_query, org_id, start_thread)
        if path == "/scope/notes/fix":
            return 200, fix_note(payload, jobtread_query, org_id)
        if path.startswith("/scope/runs"):
            # Phase 4 runs (scope_runs.py) share this route so app.py needs no change.
            return scope_runs.handle_post(path, payload, jobtread_query, org_id, start_thread)
        raise NoteError("Not found.", 404)
    return _guard(run)


def handle_status_request(params, headers):
    """GET /scope/notes/status?id=<note_id>."""
    def run():
        site_scope.check_api_key(headers.get("X-Scope-Key") or (params.get("key") or [""])[0])
        status = note_status((params.get("id") or [""])[0])
        if not status:
            raise NoteError("No note with that id here (the server may have restarted). "
                            "Check the Site Visit Notes Page in CompanyCam.", 404)
        return 200, status
    return _guard(run)


def handle_test_request(params, headers, jobtread_query, org_id):
    """GET /scope/notes-test?key=..&job=..&text=.. - adds a typed note right
    away (no audio) so the Page write can be checked from a browser."""
    def param(name):
        return (params.get(name) or [""])[0]

    def run():
        site_scope.check_api_key(headers.get("X-Scope-Key") or param("key"))
        text = param("text").strip() or "Test note from the scope tool backend. Wall 1 is 12 feet 6 inches."
        if len(text) > MAX_TEXT_CHARS:
            raise NoteError("That note is too long.")
        if not cc.is_configured():
            raise NoteError("COMPANYCAM_API_KEY is not set on this server.", 503)
        job, project_id = _project_for_job(param("job"), jobtread_query, org_id)
        now = int(time.time())
        heading = note_heading(now)
        page_id = append_note(project_id, heading, text, now, now)
        return 200, {"ok": True, "job_number": job.get("number"), "project_id": project_id, "page_id": page_id,
                     "heading": heading, "text": text,
                     "message": f"Added to the '{NOTES_TITLE}' Page on CompanyCam project {project_id}. "
                                "Open the project's Pages tab to read it."}
    return _guard(run)
