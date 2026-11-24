"""Site Scope Tool - phase 9: a Granola recording starts the quick pass on its own.

Jason records a site visit in Granola while he shoots CompanyCam photos. When
Granola finishes the note it calls POST /scope/granola (a signed webhook).
This module then:

  1. reads the note and finds the job number in its title ("100 Boxwood
     (00905)") - a title with a street number but no job number gets a phone
     alert asking for the number; meetings are left alone;
  2. looks the job up in JobTread and checks it has a CompanyCam project;
  3. waits until no new photos have landed on that project for ~10 minutes
     (photos keep uploading after the recording stops), at most an hour;
  4. starts a normal quick pass (scope_runs.open_run) with the recording as a
     source. Questions wait for Jason on the scope page, as with a run he starts.

Jason's choices (Nov 24, 2026): questions always wait for him; every matched
visit gets a new run, even a revisit or a job with a budget; start once the
photos stop for ~10 minutes.

Render env vars: GRANOLA_WEBHOOK_SECRET (the signing secret Granola shows when
the webhook is added, whsec_...) and GRANOLA_API_KEY (granola_api.py).

Granola signs webhooks the Standard Webhooks way: headers webhook-id,
webhook-timestamp, webhook-signature ("v1,<base64>"), an HMAC-SHA256 of
"<id>.<timestamp>.<body>" keyed with the base64 secret after "whsec_". It wants
a 2xx within 15 seconds and retries failures for 4 days, so the work runs on a
background thread and the reply goes straight back.

State lives in scope_store: granola:note:<id> (what happened to each note) and
granola:pending (notes waiting for photos, so a server restart can pick them
up again on the next scope request).
"""

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import traceback

import companycam_api as cc
import granola_api
import scope_push
import scope_runs
import scope_store
import site_scope

SETTLE_SECONDS = 600            # start once the photo count hasn't changed for 10 minutes
POLL_SECONDS = 60
MAX_WAIT_SECONDS = 3600         # start anyway after an hour
STALE_AFTER_SECONDS = 4 * POLL_SECONDS   # a waiting note not saved for this long lost its thread (restart)
MISSED_AFTER_SECONDS = 24 * 3600         # too old to pick up after a restart - alert instead
SIGNATURE_TOLERANCE_SECONDS = 300
BUSY_RETRIES = 5                # two runs already going: try again this many times...
BUSY_WAIT_SECONDS = 120         # ...this far apart
PENDING_KEY = "granola:pending"
EVENTS = ("note.generated", "note.edited", "note.access_granted")
RECHECK = ("unmatched", "ignored", "no_job", "no_project", "error")   # an edited title gets another look

_sleep = time.sleep
_now = time.time
_lock = threading.Lock()
_active = set()                 # note ids with a thread on this server


class GranolaHookError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ── Records ──────────────────────────────────────────────────────────────────

def _note_key(note_id):
    return f"granola:note:{note_id}"


def load_record(note_id):
    return scope_store.get(_note_key(note_id))


def _save_record(rec):
    rec["updated_at"] = int(_now())
    scope_store.put(_note_key(rec["note_id"]), rec)


def _pending():
    return list(scope_store.get(PENDING_KEY) or [])


def _set_pending(note_id, on):
    with _lock:
        ids = [i for i in _pending() if i != note_id]
        if on:
            ids.append(note_id)
        scope_store.put(PENDING_KEY, ids[-50:])


def _alert(rec, kind):
    """Phone alert about a recording that didn't start a run. Never raises."""
    try:
        sub = scope_push.latest()
        if not sub:
            print(f"Granola note {rec.get('note_id')}: {kind}, but no phone has turned alerts on yet.")
            return
        fake_run = {"run_id": f"granola-{rec.get('note_id')}", "job": rec.get("job") or {},
                    "job_number": rec.get("job_number"), "granola_title": rec.get("title"),
                    "error": rec.get("error"), "push_sub": sub}
        scope_push.notify(fake_run, kind)
        rec["alert"] = ((fake_run.get("push") or {}).get("sent") or [None])[-1]
    except Exception as e:
        print(f"Granola note {rec.get('note_id')}: could not send the alert ({e}).")


# ── The webhook ──────────────────────────────────────────────────────────────

def _secret():
    return (os.environ.get("GRANOLA_WEBHOOK_SECRET") or "").strip()


def _header(headers, name):
    for n in (name, name.lower(), name.title()):
        try:
            v = headers.get(n)
        except Exception:
            v = None
        if v:
            return str(v).strip()
    return ""


def _key_bytes(secret):
    s = secret.strip()
    if s.startswith("whsec_"):
        s = s[len("whsec_"):]
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        return s.encode("utf-8")


def verify_signature(body, headers, secret=None, now=None):
    """True when the request carries a valid, fresh Granola signature."""
    secret = _secret() if secret is None else secret
    if not secret:
        return False
    msg_id, stamp, sigs = (_header(headers, h) for h in ("webhook-id", "webhook-timestamp", "webhook-signature"))
    if not (msg_id and stamp and sigs):
        return False
    try:
        if abs((_now() if now is None else now) - int(stamp)) > SIGNATURE_TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False
    raw = body if isinstance(body, bytes) else str(body or "").encode("utf-8")
    digest = hmac.new(_key_bytes(secret), f"{msg_id}.{stamp}.".encode("utf-8") + raw, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    for part in sigs.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


def _claim(note_id, event, force=False):
    """Decide whether this event needs work; mark the note active if so."""
    with _lock:
        if note_id in _active:
            return False
        rec = load_record(note_id)
        if rec and not force:
            stale = rec.get("status") in ("checking", "waiting") and \
                _now() - (rec.get("updated_at") or 0) > STALE_AFTER_SECONDS
            if rec.get("status") not in RECHECK and not stale:
                return False
        rec = rec or {"note_id": note_id, "received_at": int(_now())}
        rec.update(status="checking", event=event, error=None)
        _save_record(rec)
        _active.add(note_id)
        return True


def queue_note(note_id, event, start_thread=None, force=False):
    """Start work on a note in the background. Returns True if it was queued."""
    if not _claim(note_id, event, force):
        return False
    (start_thread or scope_runs._start_thread)(_handle_note, note_id, start_thread)
    return True


def handle_webhook(body, headers, jobtread_query, org_id, start_thread=None):
    """POST /scope/granola. Returns (status, body dict) fast."""
    if not _secret():
        return 503, {"ok": False, "error": "GRANOLA_WEBHOOK_SECRET is not set on this server."}
    if not verify_signature(body, headers):
        return 401, {"ok": False, "error": "Bad or missing Granola signature."}
    try:
        payload = json.loads(body or "{}")
    except ValueError:
        return 400, {"ok": False, "error": "Body must be JSON."}
    payload = payload if isinstance(payload, dict) else {}
    event, note_id = payload.get("event_type"), str(payload.get("note_id") or "").strip()
    if event not in EVENTS or not note_id:
        return 200, {"ok": True, "ignored": f"event {event!r}"}
    if not granola_api.is_configured():
        # Not 2xx, so Granola keeps the event and retries (for 4 days) until the key is in.
        return 503, {"ok": False, "error": "GRANOLA_API_KEY is not set on this server."}
    scope_runs._jobtread.update(query=jobtread_query, org_id=org_id)
    queued = queue_note(note_id, event, start_thread)
    resume_pending(start_thread)
    print(f"Granola webhook {event} for note {note_id}: {'queued' if queued else 'already handled'}")
    return 200, {"ok": True, "note_id": note_id, "queued": queued}


# ── One note ─────────────────────────────────────────────────────────────────

def match_note(note, jobtread_query, org_id):
    """What the note is about, without side effects:
    {status, title, visit_date, note_at, job_number, job, project_id, error}."""
    title = str(note.get("title") or "").strip() or granola_api.event_title(note)
    out = {"status": None, "title": title, "visit_date": granola_api.note_day(note),
           "note_at": granola_api.note_ts(note), "job_number": granola_api.note_job_number(note),
           "job": None, "project_id": None, "error": None}
    if not out["job_number"]:
        visit = granola_api.looks_like_visit(note.get("title")) or \
            granola_api.looks_like_visit(granola_api.event_title(note))
        out["status"] = "unmatched" if visit else "ignored"
        return out
    try:
        job = site_scope.find_job(out["job_number"], jobtread_query, org_id)
    except site_scope.PreviewError as e:
        out.update(status="no_job", error=str(e))
        return out
    out["job"] = {"id": job.get("id"), "number": job.get("number"), "name": job.get("name"),
                  "address": (job.get("location") or {}).get("address")}
    out["project_id"] = (job.get("companycamId") or "").strip() or None
    out["status"] = "waiting" if out["project_id"] else "no_project"
    return out


def _handle_note(note_id, start_thread=None):
    """Background thread for one note. Never raises."""
    rec = None
    try:
        rec = load_record(note_id) or {"note_id": note_id, "received_at": int(_now())}
        try:
            note = granola_api.get_note(note_id)
            if not scope_runs._jobtread.get("query"):
                raise GranolaHookError("The server has no JobTread connection yet.")
            found = match_note(note, scope_runs._jobtread["query"], scope_runs._jobtread.get("org_id"))
        except (granola_api.GranolaError, GranolaHookError) as e:
            rec.update(status="error", error=str(e))
            _save_record(rec)
            print(f"Granola note {note_id}: {e}")
            _alert(rec, "granola_not_started")
            _save_record(rec)
            return
        rec.update(found)
        if rec["status"] != "waiting":
            print(f"Granola note {note_id} ({rec.get('title')!r}): {rec['status']}")
            if rec["status"] != "ignored":
                _alert(rec, f"granola_{rec['status']}")
            _save_record(rec)
            return
        rec.update(wait_started=int(_now()), photo_count=None, steady_since=None)
        _save_record(rec)
        _set_pending(note_id, True)
        print(f"Granola note {note_id}: job {rec['job'].get('number')}, waiting for the photos to stop")
        _wait_and_start(rec, start_thread)
    except Exception as e:
        print(f"Granola note {note_id} failed: {e}\n{traceback.format_exc()}")
        try:
            if rec is not None:
                rec.update(status="error", error=f"Unexpected error: {e}")
                _alert(rec, "granola_not_started")
                _save_record(rec)
                _set_pending(note_id, False)
        except Exception:
            pass
    finally:
        with _lock:
            _active.discard(note_id)


def _photo_count(project_id, day, note_at):
    """Photos on the project taken on the visit's local day."""
    stop = (note_at - 12 * 3600) if note_at else None
    count = 0
    for p in cc.list_project_photos(project_id, stop_before_ts=stop):
        ts = cc.parse_timestamp(p.get("captured_at"))
        if ts is not None and site_scope.to_local(ts).strftime("%Y-%m-%d") == day:
            count += 1
    return count


def _wait_and_start(rec, start_thread=None):
    """Poll the photo count until it holds still for SETTLE_SECONDS, then start."""
    started = rec.get("wait_started") or int(_now())
    while True:
        now = int(_now())
        try:
            count = _photo_count(rec["project_id"], rec["visit_date"], rec.get("note_at"))
            rec["poll_error"] = None
        except cc.CompanyCamError as e:
            count, rec["poll_error"] = rec.get("photo_count"), str(e)
        if rec.get("steady_since") is None or count != rec.get("photo_count"):
            rec.update(photo_count=count, steady_since=now)
        _save_record(rec)
        if now - rec["steady_since"] >= SETTLE_SECONDS or now - started >= MAX_WAIT_SECONDS:
            break
        _sleep(POLL_SECONDS)
    _start(rec, start_thread)


def _start(rec, start_thread=None):
    received = rec.get("received_at") or 0

    def already_has_it(latest):
        # A run that already read this recording, or one Jason started by hand for
        # this visit after Granola finished (it finds the recording by job and day).
        return rec["note_id"] in (latest.get("granola_note_ids") or []) or \
            (latest.get("visit_date") == rec["visit_date"] and (latest.get("created_at") or 0) >= received)

    job = rec["job"]
    job_arg = {"id": job.get("id"), "number": job.get("number"), "name": job.get("name"),
               "location": {"address": job.get("address")}}
    extra = {"started_by": "granola", "granola_note_ids": [rec["note_id"]], "granola_title": rec.get("title")}
    for attempt in range(BUSY_RETRIES + 1):
        try:
            status, run = scope_runs.open_run(job_arg, rec["project_id"], rec["visit_date"], scope_push.latest(),
                                              restart=True, start_thread=start_thread, extra=extra,
                                              keep=already_has_it)
            rec.update(status="started" if status == 202 else "already_running", run_id=run.get("run_id"),
                       error=None)
            break
        except scope_runs.RunError as e:
            if e.status == 429 and attempt < BUSY_RETRIES:
                rec["error"] = str(e)
                _save_record(rec)
                _sleep(BUSY_WAIT_SECONDS)
                continue
            rec.update(status="not_started", error=str(e))
            _alert(rec, "granola_not_started")
            break
    _save_record(rec)
    _set_pending(rec["note_id"], False)
    print(f"Granola note {rec['note_id']} (job {job.get('number')}): {rec['status']} {rec.get('run_id') or ''}")


# ── After a restart ──────────────────────────────────────────────────────────

def _resume(note_id, start_thread=None):
    try:
        rec = load_record(note_id)
        if rec and rec.get("status") == "waiting":
            _wait_and_start(rec, start_thread)
    except Exception as e:
        print(f"Granola note {note_id}: resume failed: {e}\n{traceback.format_exc()}")
    finally:
        with _lock:
            _active.discard(note_id)


def resume_pending(start_thread=None):
    """Pick up notes whose wait was cut off by a server restart. Called on every
    keyed scope request and every webhook. Never raises."""
    try:
        now = _now()
        for note_id in _pending():
            with _lock:
                if note_id in _active:
                    continue
            rec = load_record(note_id)
            if not rec or rec.get("status") != "waiting":
                _set_pending(note_id, False)
                continue
            if now - (rec.get("updated_at") or 0) < STALE_AFTER_SECONDS:
                continue
            if now - (rec.get("received_at") or 0) > MISSED_AFTER_SECONDS:
                rec["status"] = "missed"
                _alert(rec, "granola_missed")
                _save_record(rec)
                _set_pending(note_id, False)
                continue
            with _lock:
                if note_id in _active:
                    continue
                _active.add(note_id)
            print(f"Granola note {note_id}: picking up the photo wait again after a restart")
            (start_thread or scope_runs._start_thread)(_resume, note_id, start_thread)
    except Exception as e:
        print(f"Granola: could not check waiting notes ({e}).")


# ── Keyed test route ─────────────────────────────────────────────────────────

def handle_test(payload, jobtread_query, org_id, start_thread=None):
    """POST /scope/granola/test {note_id, start?}: what the tool makes of a note.
    With start: true it is queued as if the webhook had come in."""
    if not granola_api.is_configured():
        raise GranolaHookError("GRANOLA_API_KEY is not set on this server.", 503)
    note_id = str(payload.get("note_id") or "").strip()
    try:
        note = granola_api.get_note(note_id, transcript=True)
    except granola_api.GranolaError as e:
        raise GranolaHookError(str(e), 502 if e.status is None or e.status >= 500 else 400)
    found = match_note(note, jobtread_query, org_id)
    text = granola_api.transcript_text(note)
    out = {"ok": True, "note_id": note_id, "match": found, "transcript_chars": len(text),
           "transcript_start": text[:600], "record": load_record(note_id)}
    if payload.get("start"):
        scope_runs._jobtread.update(query=jobtread_query, org_id=org_id)
        out["queued"] = queue_note(note_id, "test", start_thread, force=True)
    return 200, out
