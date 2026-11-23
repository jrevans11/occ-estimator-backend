"""Preliminary Ballpark Tool, guided intake step 1: read the site visit.

    POST /ballpark/read          {job, notes?}  start a read; answers 202 right away
    POST /ballpark/read/status   {job}          the newest read on the job, with its pages

A read runs on its own thread (2-3 minutes): it gathers the visit material
(ballpark_inputs.gather) and runs the same Claude scope read as the form
flow with an empty form (ballpark_scope.extract), then saves the reply per
job so Tyler can leave and come back. Nothing is written to JobTread.

The pages and questions are built from the saved reply each time the status
is asked for (ballpark_questions), so a catalog change applies to old reads.
Building the budget from the confirmed answers is POST /ballpark with
from_read (ballpark.py); it prices the answers directly, with no second
Claude call.

Design: docs/ballpark/GUIDED_INTAKE_DESIGN.md
"""

import threading
import traceback
from datetime import datetime, timezone

import ballpark_inputs as inputs
import ballpark_pricing as bp
import ballpark_questions as questions
import ballpark_scope
import scope_store

STORE_PREFIX = "ballpark:read:"
MAX_RUNNING = 2
MAX_NOTES_CHARS = 8000
STALE_MINUTES = 15

_running = set()
_running_lock = threading.Lock()


class ReadError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(record):
    record["updated_at"] = _now()
    try:
        scope_store.put(STORE_PREFIX + record["job"]["id"], record)
    except Exception as e:
        print(f"Ballpark read: could not save ({e})")


def load(job_id):
    try:
        return scope_store.get(STORE_PREFIX + job_id)
    except Exception as e:
        print(f"Ballpark read: could not load ({e})")
        return None


def what_was_read(context):
    """What the read looked at, for the Start page and for the [BALLPARK]
    summary's review section. Transcript and photo text is left out."""
    camera = context.get("companycam") or {}
    return {
        "videos": [{"video_id": t.get("video_id"), "captured_at": t.get("captured_at"),
                    "duration": t.get("duration"), "scope_summary": bool(t.get("likely_scope_summary"))}
                   for t in camera.get("transcripts") or []],
        "missing_videos": camera.get("missing_transcripts") or [],
        "photo_descriptions": len(camera.get("photos") or []),
        "photo_count": camera.get("photo_count") or 0,
        "pages": [p.get("title") for p in camera.get("pages") or []],
        "comments": len(context.get("comments") or []),
        "daily_logs": len(context.get("daily_logs") or []),
        "rendr": bool(context.get("rendr")),
        "warnings": context.get("warnings") or [],
    }


def summary_context(read):
    """A stand-in for the gathered context that ballpark.summary_text needs
    (counts, warnings, missing videos), rebuilt from the saved read."""
    seen = read.get("read") or {}
    return {
        "companycam": {"transcripts": seen.get("videos") or [],
                       "photos": [{}] * int(seen.get("photo_descriptions") or 0),
                       "missing_transcripts": seen.get("missing_videos") or []},
        "comments": [{}] * int(seen.get("comments") or 0),
        "daily_logs": [{}] * int(seen.get("daily_logs") or 0),
        "warnings": seen.get("warnings") or [],
    }


def _keep_reply(reply):
    keys = ("scope", "sources", "field_status", "conflicts", "open_questions", "confidence", "summary",
            "cost_usd", "model")
    return {k: reply.get(k) for k in keys}


def _process(record, jobtread_query, org_id):
    job = record["job"]

    def step(name):
        record["step"] = name
        save(record)
        print(f"Ballpark read job {job.get('number')}: {name}")

    try:
        step("reading the site visit")
        context = inputs.gather(job["id"], jobtread_query, org_id)
        record["read"] = what_was_read(context)
        step("working out the scope")
        form = {"notes": record["notes"]} if record.get("notes") else {}
        reply = ballpark_scope.extract(context, form, bp.load_tier_prices())
        record["reply"] = _keep_reply(reply)
        record["state"] = "done"
        record["step"] = "done"
        save(record)
        print(f"Ballpark read job {job.get('number')}: done (Claude ${reply.get('cost_usd') or 0:.2f})")
    except Exception as e:
        print(f"Ballpark read job {job.get('number')} failed: {e}\n{traceback.format_exc()}")
        record["state"] = "failed"
        record["error"] = str(e)
        save(record)
    finally:
        with _running_lock:
            _running.discard(job["id"])


def _start_thread(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def start(payload, jobtread_query, org_id, start_thread=None):
    job_ref = str(payload.get("job") or "").strip()
    notes = str(payload.get("notes") or "").strip()[:MAX_NOTES_CHARS]
    try:
        job = inputs.find_job(job_ref, jobtread_query, org_id)
        inputs.check_job_type(job)
    except inputs.InputError as e:
        raise ReadError(str(e), e.status)
    with _running_lock:
        if job["id"] in _running:
            raise ReadError(f"The site visit for job {job.get('number')} is already being read.", 409)
        if len(_running) >= MAX_RUNNING:
            raise ReadError("Two site visits are already being read. Try again in a few minutes.", 429)
        _running.add(job["id"])
    record = {"job": {"id": job["id"], "number": job.get("number"), "name": job.get("name")},
              "state": "running", "step": "starting", "error": None, "notes": notes, "started_at": _now()}
    save(record)
    (start_thread or _start_thread)(_process, record, jobtread_query, org_id)
    return 202, {"ok": True, "message": "Reading the site visit. This takes 2-3 minutes; you can leave and come "
                                        "back.", "read": public(record)}


def _age_minutes(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return 0


def public(record):
    """The read as the tool page sees it: status, what was read, and the
    pages once it's done."""
    if not record:
        return None
    out = {k: record.get(k) for k in ("state", "step", "error", "notes", "started_at", "updated_at", "job", "read")}
    reply = record.get("reply")
    if record.get("state") == "done" and reply:
        out["pages"] = questions.build_pages(reply)
        out["open_questions"] = reply.get("open_questions") or []
        out["conflicts"] = reply.get("conflicts") or []
        out["confidence"] = reply.get("confidence")
        out["claude_cost_usd"] = reply.get("cost_usd")
        out["rooms"] = [{"index": i, "name": r.get("name"), "type": r.get("type")}
                        for i, r in enumerate((reply.get("scope") or {}).get("rooms") or [])]
    return out


def status(payload, jobtread_query, org_id):
    job_ref = str(payload.get("job") or "").strip()
    try:
        job = inputs.find_job(job_ref, jobtread_query, org_id)
    except inputs.InputError as e:
        raise ReadError(str(e), e.status)
    record = load(job["id"])
    with _running_lock:
        here = job["id"] in _running
    if (record and record.get("state") == "running" and not here
            and _age_minutes(record.get("updated_at")) > STALE_MINUTES):
        record["state"] = "stopped"
        record["error"] = "The read stopped without finishing (the server most likely restarted). Start it again."
    return 200, {"ok": True, "read": public(record)}


def load_done(job_id, started_at=None):
    """The finished read a guided build prices from. started_at (from the
    page) must match, so answers never land on a newer read."""
    record = load(job_id)
    if not record or record.get("state") != "done" or not record.get("reply"):
        raise ReadError("There's no finished site-visit read for this job. Read the site visit first.", 409)
    if started_at and record.get("started_at") != started_at:
        raise ReadError("The site visit was read again since these answers were started. Reload the page to "
                        "see the newest read.", 409)
    return record
