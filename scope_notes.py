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

A note can be filed under an earlier visit (Jason uploads photos from an
earlier visit and dictates about them later): the italic line then says
"Filed under the Oct 19, 2026 visit." and the quick pass reads it with that
visit instead of the day it was recorded.

A walkthrough (scope_walkthrough.py) is one long Voice Memo recorded while
Jason took the visit's CompanyCam photos. It comes in on the same route with
kind "walkthrough" and lands as one note with a marker where each photo was
taken.

Endpoints (registered in app.py; all need X-Scope-Key):
    POST /scope/notes          {note_id, job, audio_url | text, started_at, saved_at, visit_date?}
                               walkthrough: {kind: "walkthrough", note_id, job, audio_url, tapped_at?,
                                             file_modified_at?, visit_date?}
    GET  /scope/notes/status?id=<note_id>
    POST /scope/notes/fix      {job, heading, old_text, new_text}
    POST /scope/notes/visit    {job, heading, text, visit_date}   file a saved note under another visit
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
from datetime import datetime
from urllib.parse import urlparse

import companycam_api as cc
import scope_quickpass
import scope_runs
import scope_transcribe
import scope_walkthrough
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
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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


def visit_sentence(visit_date):
    """"Filed under the Oct 19, 2026 visit." - read back by scope_quickpass.VISIT_RE."""
    return f"Filed under the {site_scope._pretty_date(visit_date)} visit."


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


def note_nodes(heading, text, started_ts, saved_ts, duration=None, audio_url=None, link_audio=True,
               visit_date=None):
    """The TipTap nodes for one note: heading, an italic "when" line, the text.
    visit_date: the earlier visit this note is filed under, or None."""
    italic = [{"type": "italic"}]
    filed = f"{visit_sentence(visit_date)} " if visit_date else ""
    if audio_url:
        span = f"Recorded {_clock(started_ts, True)} to {_clock(saved_ts, True)}"
        if duration:
            span += f" ({_duration(duration)} of audio)"
        meta = [_text(span + ". " + filed, italic)]
        if link_audio:
            meta.append(_text("Play the recording", italic + [{"type": "link", "attrs": {"href": audio_url}}]))
        else:
            meta.append(_text(f"Recording: {audio_url}", italic))
    else:
        meta = [_text(f"Typed note, saved {_clock(saved_ts, True)}. {filed}".rstrip(), italic)]
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


def append_note(project_id, heading, text, started_ts, saved_ts, duration=None, audio_url=None,
                visit_date=None, walkthrough=None):
    """Add one note to the project's Site Visit Notes Page. If CompanyCam
    refuses the audio link, the link goes in as plain text instead.
    walkthrough: {"laid_out", "photo_count", "start_source"} for a
    walkthrough recording - its body has photo markers in it."""
    def nodes(link_audio):
        out = note_nodes(heading, "" if walkthrough else text, started_ts, saved_ts, duration, audio_url,
                         link_audio=link_audio, visit_date=visit_date)
        if walkthrough:
            meta = out[1]["content"][0]
            sentence = scope_walkthrough.meta_sentence(walkthrough["photo_count"], walkthrough["start_source"])
            meta["text"] = meta["text"].replace(". ", f". {sentence} ", 1)
            out += scope_walkthrough.body_nodes(walkthrough["laid_out"])
        return out

    with _project_lock(project_id):
        try:
            return _write(project_id, nodes(True))
        except cc.CompanyCamError as e:
            if not audio_url or e.status not in (400, 422):
                raise
            print(f"Site Visit Notes: CompanyCam refused the audio link ({e}); saving it as plain text")
            return _write(project_id, nodes(False))


def _notes_under(content, heading):
    """Each note under `heading` on the Page, newest first, as
    (heading index, meta line index or None, body start, end)."""
    starts = [i for i, n in enumerate(content)
              if n.get("type") == "heading" and _norm(node_text(n)) == _norm(heading)]
    for h in reversed(starts):
        end = next((i for i in range(h + 1, len(content)) if content[i].get("type") == "heading"), len(content))
        meta = h + 1 if h + 1 < end and node_text(content[h + 1]).startswith(META_PREFIXES) else None
        yield h, meta, (meta + 1 if meta is not None else h + 1), end


def _body_matches(content, body_start, end, text):
    return _norm(" ".join(node_text(n) for n in content[body_start:end])) == _norm(text)


def _filed_visit(meta_node):
    """The visit a note's italic line files it under, or None."""
    m = scope_quickpass.VISIT_RE.search(node_text(meta_node)) if meta_node else None
    return scope_quickpass.visit_iso(m) if m else None


def _refile_meta(meta_node, visit_date):
    """The italic line with its "Filed under ..." sentence swapped for
    visit_date's (or dropped when visit_date is None). Everything else on
    the line, like the recording link, is kept."""
    meta = json.loads(json.dumps(meta_node))
    parts = [c for c in meta.get("content") or [] if c.get("type") == "text"]
    for c in parts:
        old = c.get("text") or ""
        if scope_quickpass.VISIT_RE.search(old):
            new = re.sub(r"\s*" + scope_quickpass.VISIT_RE.pattern, "", old).rstrip()
            c["text"] = new + (" " if new and old.endswith(" ") else "")
    if visit_date and parts:
        first = next((c for c in parts if c["text"]), parts[0])
        trailing = first["text"].endswith(" ")
        first["text"] = (first["text"].rstrip() + " " + visit_sentence(visit_date)).lstrip() + (" " if trailing else "")
    meta["content"] = [c for c in meta.get("content") or [] if c.get("type") != "text" or c.get("text")]
    return meta


def replace_note_text(project_id, heading, old_text, new_text, now=None):
    """Swap a note's text on the Page. Finds the newest note under `heading`
    whose text still matches old_text. If someone has since edited it in
    CompanyCam, the fix is added as a new note instead (filed under the same
    visit), so nothing is lost. Returns "replaced" or "added"."""
    now = time.time() if now is None else now
    with _project_lock(project_id):
        page = find_notes_page(project_id)
        if not page:
            raise NoteError("This project has no Site Visit Notes Page yet.", 404)
        doc = cc.get_page(page["id"]).get("tiptap_content") or {}
        content = list(doc.get("content") or [])
        visit_date = None
        for _, meta, body_start, end in _notes_under(content, heading):
            if visit_date is None and meta is not None:
                visit_date = _filed_visit(content[meta])
            if _body_matches(content, body_start, end, old_text):
                doc["content"] = content[:body_start] + text_paragraphs(new_text) + content[end:]
                cc.update_page(page["id"], doc)
                return "replaced"
        doc["content"] = content + note_nodes(f"{heading} (corrected)", new_text, now, now, visit_date=visit_date)
        cc.update_page(page["id"], doc)
        return "added"


def recorded_day(heading):
    """"Oct 20, 2026 - 9:02 AM" -> "2026-10-20" (None if it isn't a note heading)."""
    m = re.match(r"^([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) - ", _norm(heading))
    if not m:
        return None
    return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").strftime("%Y-%m-%d")


def _check_visit_date(visit_date, recorded):
    """A note can be filed under its recording day or an earlier visit.
    Returns the date to write on the Page (None = the recording day)."""
    if not visit_date:
        return None
    if not DATE_RE.match(visit_date):
        raise NoteError("visit_date must look like 2026-10-19.")
    if recorded and visit_date > recorded:
        raise NoteError("A note can't be filed under a visit after the day it was recorded.")
    return None if visit_date == recorded else visit_date


def set_note_visit(project_id, heading, text, visit_date):
    """File a saved note under another visit (or back under the day it was
    recorded) by rewriting its italic line. Finds the newest note under
    `heading` whose text matches. Returns the visit date it's now under."""
    recorded = recorded_day(heading)
    filed = _check_visit_date(visit_date, recorded)
    with _project_lock(project_id):
        page = find_notes_page(project_id)
        if not page:
            raise NoteError("This project has no Site Visit Notes Page yet.", 404)
        doc = cc.get_page(page["id"]).get("tiptap_content") or {}
        content = list(doc.get("content") or [])
        for _, meta, body_start, end in _notes_under(content, heading):
            if meta is None or not _body_matches(content, body_start, end, text):
                continue
            content[meta] = _refile_meta(content[meta], filed)
            doc["content"] = content
            cc.update_page(page["id"], doc)
            return filed or recorded
        raise NoteError("Couldn't find that note on the Site Visit Notes Page as it was saved (it may have been "
                        "edited in CompanyCam). Change its \"Filed under\" line there instead.", 404)


# ── Note status (in memory) ──────────────────────────────────────────────────

def _forget_old(now):
    for nid in [n for n, r in _notes.items() if now - r["created"] > KEEP_SECONDS]:
        del _notes[nid]
    while len(_notes) > MAX_KEPT:
        del _notes[min(_notes, key=lambda n: _notes[n]["created"])]


def _public(record):
    return {"ok": True, **{k: record.get(k) for k in (
        "note_id", "status", "text", "heading", "error", "job_number", "project_id", "page_id",
        "duration_seconds", "page_result", "visit_date", "kind", "photo_count", "start_source")}}


def _update(note_id, **fields):
    with _notes_lock:
        if note_id in _notes:
            _notes[note_id].update(fields)


def note_status(note_id):
    with _notes_lock:
        record = _notes.get(note_id)
        return _public(record) if record else None


def _process_walkthrough(note_id, r):
    """Background, for a walkthrough: transcribe with sentence times, work out
    when it started, mark the photos taken during it, write it to the Page."""
    paragraphs, duration = scope_transcribe.transcribe_url_timed(r["audio_url"])
    if not paragraphs:
        _update(note_id, status="failed", text="", duration_seconds=duration,
                error="No speech was heard in the recording. Check it's the right Voice Memo.")
        print(f"Site Visit Notes {note_id}: no speech in walkthrough {r['audio_url']}")
        return
    start_ts, source = scope_walkthrough.choose_start(r["tapped_ts"], r["file_ts"], duration, r["saved_ts"])
    end_ts = int(round(start_ts + (duration or 0)))
    photos = scope_walkthrough.photos_during(
        cc.list_project_photos(r["project_id"], stop_before_ts=start_ts - 120), start_ts, duration)
    laid_out = scope_walkthrough.layout(paragraphs, photos, start_ts)
    text = scope_walkthrough.plain_text(laid_out)
    heading = note_heading(start_ts)
    _update(note_id, status="saving", text=text, duration_seconds=duration, heading=heading,
            photo_count=len(photos), start_source=source)
    page_id = append_note(r["project_id"], heading, text, start_ts, end_ts, duration, r["audio_url"],
                          r["filed_under"], walkthrough={"laid_out": laid_out, "photo_count": len(photos),
                                                         "start_source": source})
    _update(note_id, status="saved", page_id=page_id)
    print(f"Site Visit Notes {note_id}: walkthrough saved to page {page_id} on project {r['project_id']} "
          f"(job {r['job_number']}, {int(duration or 0)} s, {len(photos)} photos, start from {source})")


def _process(note_id):
    """Background: transcribe, then write to the Page. Never raises."""
    with _notes_lock:
        r = dict(_notes[note_id])
    try:
        if r.get("kind") == "walkthrough":
            return _process_walkthrough(note_id, r)
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
                              duration, r["audio_url"], r["filed_under"])
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

    kind = "walkthrough" if payload.get("kind") == "walkthrough" else "note"
    audio_url = str(payload.get("audio_url") or "").strip()
    text = str(payload.get("text") or "").strip()
    if kind == "walkthrough" and not audio_url:
        raise NoteError("Send the walkthrough's audio_url.")
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
    tapped_ts = file_ts = None
    if kind == "walkthrough":
        # The start isn't known until Deepgram gives the length; the day comes
        # from the Start tap or the file's time (both close enough for that).
        tapped_ts = cc.parse_timestamp(payload.get("tapped_at"))
        file_ts = cc.parse_timestamp(payload.get("file_modified_at"))
        saved_ts = int(now)
        started_ts = tapped_ts or file_ts or saved_ts
    else:
        saved_ts = _timestamp(payload.get("saved_at"), int(now))
        started_ts = _timestamp(payload.get("started_at"), saved_ts)
    recorded = site_scope.to_local(started_ts).strftime("%Y-%m-%d")
    filed_under = _check_visit_date(str(payload.get("visit_date") or "").strip(), recorded)
    record = {
        "note_id": note_id, "created": now, "status": "transcribing" if audio_url else "saving",
        "audio_url": audio_url, "text": text, "error": None,
        "job_number": job.get("number"), "project_id": project_id, "page_id": None,
        "started_ts": started_ts, "saved_ts": saved_ts,
        "heading": None if kind == "walkthrough" else note_heading(started_ts),
        "duration_seconds": None, "filed_under": filed_under, "visit_date": filed_under or recorded,
        "kind": kind, "tapped_ts": tapped_ts, "file_ts": file_ts, "photo_count": None, "start_source": None,
    }
    with _notes_lock:
        if note_id in _notes:     # a retry raced this one
            return 200, _public(_notes[note_id])
        _forget_old(now)
        _notes[note_id] = record
        reply = _public(record)   # before the thread starts changing it
    (start_thread or _start_thread)(_process, note_id)
    return 202, reply


def refile_note(payload, jobtread_query, org_id):
    heading = str(payload.get("heading") or "").strip()
    visit_date = str(payload.get("visit_date") or "").strip()
    if not heading or not visit_date:
        raise NoteError("Send heading, text and visit_date.")
    _, project_id = _project_for_job(payload.get("job"), jobtread_query, org_id)
    now_under = set_note_visit(project_id, heading, payload.get("text") or "", visit_date)
    note_id = str(payload.get("note_id") or "")
    if note_id:
        _update(note_id, visit_date=now_under)
    return {"ok": True, "visit_date": now_under}


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
        if path == "/scope/notes/visit":
            return 200, refile_note(payload, jobtread_query, org_id)
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
