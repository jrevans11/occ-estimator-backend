"""Site Scope Tool - scope runs (phase 4: Start, questions, answers).

Jason taps Start on the scope page. The run then works on its own background
thread here (so a closed page or dropped signal doesn't stop it):

    reading    -> the quick pass is reading the notes, photos and transcripts
    questions  -> 3-6 cost-driver questions are waiting for answers
    answered   -> answers saved; the full build (phases 5-6) picks up from here
    failed     -> something went wrong; `error` says what, and Start tries again

A run is kept in scope_store (Upstash Redis, or memory if that isn't set up),
so its questions wait if the page is closed. The page asks for the newest run
on the job when it opens.

Endpoints - all POST with JSON and the X-Scope-Key header. They come in
through scope_notes.handle_post, which app.py already routes every POST
/scope/* to, so this phase needs no app.py change:
    POST /scope/runs           {job, date?, restart?}   start a run (or return the one going)
    POST /scope/runs/status    {run_id} or {job}         a run's state (job = the newest on that job)
    POST /scope/runs/answers   {run_id, answers: {Q1: "..."}}   save answers (blank = suggested)

Token counts and cost are logged per run and kept on the run (`usage`).
"""

import secrets
import threading
import time
import traceback

import companycam_api as cc
import scope_quickpass
import scope_store
import site_scope

MAX_RUNNING = 2              # quick passes at once on this server
RESTART_GRACE_SECONDS = 90   # a "reading" run not running here after this was cut off by a restart
MAX_ANSWER_CHARS = 1000

_running = set()
_running_lock = threading.Lock()
_job_locks = {}
_job_locks_lock = threading.Lock()


class RunError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _job_lock(job_id):
    with _job_locks_lock:
        return _job_locks.setdefault(str(job_id), threading.Lock())


def _run_key(run_id):
    return f"run:{run_id}"


def _latest_key(job_id):
    return f"job:{job_id}:latest"


def _save(run):
    run["updated_at"] = int(time.time())
    scope_store.put(_run_key(run["run_id"]), run)


def load_run(run_id):
    run = scope_store.get(_run_key(run_id))
    return _mark_if_cut_off(run) if run else None


def _mark_if_cut_off(run, now=None):
    """A run left "reading" by a server restart would wait forever - mark it failed."""
    now = time.time() if now is None else now
    if run.get("status") == "reading":
        with _running_lock:
            here = run["run_id"] in _running
        if not here and now - (run.get("updated_at") or 0) > RESTART_GRACE_SECONDS:
            run["status"] = "failed"
            run["error"] = "The server restarted while this run was reading the visit. Tap Start to run it again."
            _save(run)
    return run


def public(run, include_file=False):
    """What the page sees."""
    out = {"ok": True, "store": scope_store.kind()}
    for k in ("run_id", "status", "job", "visit_date", "inputs", "questions", "answers", "error",
              "warnings", "created_at", "updated_at", "answered_at"):
        out[k] = run.get(k)
    usage = run.get("usage") or {}
    out["usage"] = {k: usage.get(k) for k in ("model", "input_tokens", "output_tokens",
                                              "cache_read_input_tokens", "cost_usd", "seconds", "calls")}
    rf = run.get("room_file") or {}
    out["summary"] = {
        "rooms": [r.get("name") for r in rf.get("rooms") or []],
        "scope_items": len(rf.get("scope_items") or []),
        "photos_described": len(rf.get("photos") or []),
        "flags": len(rf.get("flags") or []) + sum(len(r.get("flags") or []) for r in rf.get("rooms") or []),
    }
    if include_file:
        out["room_file"] = rf
    return out


# ── The background work ──────────────────────────────────────────────────────

def _process(run_id):
    """Background thread: the quick pass. Never raises."""
    try:
        run = scope_store.get(_run_key(run_id))
        try:
            result = scope_quickpass.run_quick_pass(run["job"], run["project_id"], run.get("date_asked"))
            run.update(status="questions", visit_date=result["inputs"]["visit_date"], inputs=result["inputs"],
                       questions=result["questions"], room_file=result["room_file"], usage=result["usage"],
                       warnings=result["warnings"], error=None)
            if not result["questions"]:
                # Nothing to ask - straight to answered, ready for the build.
                run.update(status="answered", answers=[], answered_at=int(time.time()))
        except (scope_quickpass.QuickPassError, scope_quickpass.ClaudeError, cc.CompanyCamError) as e:
            run.update(status="failed", error=str(e))
            print(f"Scope run {run_id} failed: {e}")
        except Exception as e:
            run.update(status="failed", error=f"Unexpected error: {e}")
            print(f"Scope run {run_id} failed: {e}\n{traceback.format_exc()}")
        _save(run)
        print(f"Scope run {run_id} (job {run['job'].get('number')}): {run['status']}"
              f"{', cost $%.4f' % run['usage']['cost_usd'] if (run.get('usage') or {}).get('cost_usd') is not None else ''}")
    except Exception as e:
        print(f"Scope run {run_id}: could not save the result: {e}\n{traceback.format_exc()}")
    finally:
        with _running_lock:
            _running.discard(run_id)


def _start_thread(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


# ── Requests ─────────────────────────────────────────────────────────────────

def start_run(payload, jobtread_query, org_id, start_thread=None):
    if not cc.is_configured():
        raise RunError("COMPANYCAM_API_KEY is not set on this server.", 503)
    date = str(payload.get("date") or "").strip() or None
    if date and not (len(date) == 10 and date[4] == "-" and date[7] == "-"):
        raise RunError("date must look like 2026-10-21.")
    job = site_scope.find_job(payload.get("job"), jobtread_query, org_id)
    project_id = (job.get("companycamId") or "").strip()
    if not project_id:
        raise RunError(f"Job {job.get('number')} has no CompanyCam project linked yet.", 404)

    with _job_lock(job["id"]):
        latest_id = scope_store.get(_latest_key(job["id"]))
        latest = load_run(latest_id) if latest_id else None
        if latest and latest.get("status") == "reading":
            return 200, public(latest)
        if latest and latest.get("status") == "questions" and not payload.get("restart"):
            return 200, public(latest)
        with _running_lock:
            if len(_running) >= MAX_RUNNING:
                raise RunError("Two scope runs are already going on the server. Try again in a minute.", 429)
            run_id = secrets.token_hex(8)
            _running.add(run_id)
        now = int(time.time())
        run = {
            "run_id": run_id, "status": "reading", "created_at": now, "updated_at": now,
            "job": {"id": job.get("id"), "number": job.get("number"), "name": job.get("name"),
                    "address": (job.get("location") or {}).get("address")},
            "project_id": project_id, "date_asked": date, "visit_date": date,
            "inputs": None, "questions": [], "answers": None, "error": None, "warnings": [],
            "usage": {"model": scope_quickpass.model()},
        }
        try:
            _save(run)
            scope_store.put(_latest_key(job["id"]), run_id)
        except Exception:
            with _running_lock:
                _running.discard(run_id)
            raise
    print(f"Scope run {run_id} started for job {job.get('number')} (store: {scope_store.kind()})")
    (start_thread or _start_thread)(_process, run_id)
    return 202, public(run)


def run_status(payload, jobtread_query, org_id):
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id and payload.get("job"):
        job = site_scope.find_job(payload.get("job"), jobtread_query, org_id)
        run_id = scope_store.get(_latest_key(job["id"])) or ""
        if not run_id:
            return 200, {"ok": True, "status": "none", "store": scope_store.kind()}
    if not run_id:
        raise RunError("Send run_id or job.")
    run = load_run(run_id)
    if not run:
        raise RunError("No run with that id (it may have expired, or the server restarted without "
                       "a database set up). Tap Start to run it again.", 404)
    return 200, public(run, include_file=bool(payload.get("include_file")))


def save_answers(payload):
    run_id = str(payload.get("run_id") or "").strip()
    run = load_run(run_id) if run_id else None
    if not run:
        raise RunError("No run with that id. Tap Start to run it again.", 404)
    if run.get("status") not in ("questions", "answered"):
        raise RunError(f"This run isn't waiting for answers (it's {run.get('status')}).", 409)
    given = payload.get("answers") or {}
    if not isinstance(given, dict):
        raise RunError("answers must be an object like {\"Q1\": \"...\"}.")
    answers = []
    for q in run.get("questions") or []:
        text = str(given.get(q["id"]) or "").strip()[:MAX_ANSWER_CHARS]
        answers.append({"id": q["id"], "question": q["question"], "answer": text or q.get("suggested_answer") or "",
                        "used_suggestion": not text or text == q.get("suggested_answer")})
    run.update(status="answered", answers=answers, answered_at=int(time.time()))
    _save(run)
    used = sum(1 for a in answers if a["used_suggestion"])
    print(f"Scope run {run_id} (job {run['job'].get('number')}): {len(answers)} answers saved "
          f"({used} suggested as-is)")
    return 200, public(run)


def handle_post(path, payload, jobtread_query, org_id, start_thread=None):
    """Routed from scope_notes.handle_post (key already checked, body parsed)."""
    try:
        if path == "/scope/runs":
            return start_run(payload, jobtread_query, org_id, start_thread)
        if path == "/scope/runs/status":
            return run_status(payload, jobtread_query, org_id)
        if path == "/scope/runs/answers":
            return save_answers(payload)
        raise RunError("Not found.", 404)
    except RunError as e:
        return e.status, {"ok": False, "error": str(e)}
    except scope_store.StoreError as e:
        print(f"Scope run storage problem: {e}")
        return 503, {"ok": False, "error": f"Couldn't reach the run storage ({e})."}
