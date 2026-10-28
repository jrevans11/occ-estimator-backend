"""Preliminary Ballpark Tool: the /ballpark endpoints and the background run.

Tyler picks a Renovation or Addition job on /intake/ballpark, fills in the
variables and submits. The Next.js route forwards here with X-Scope-Key.

    POST /ballpark          {job, form, dry_run?}  start a run; answers 202 right away
    POST /ballpark/status   {job}                  the newest run on that job

A run (on its own thread, so a closed page doesn't stop it):
  1. Daily Log "[BALLPARK] started" on the job (skipped on a dry run)
  2. gather the visit material (ballpark_inputs) and read it into a scope
     with Claude (ballpark_scope)
  3. price it against the live Master Design Build Template (ballpark_pricing)
  4. re-check the job has no budget, then write one cost group per phase with
     its lines and a $0 internal NOTES line
  5. Daily Log "[BALLPARK]" with the written summary and price range, and a
     to-do for Tyler to review the budget
On failure: Daily Log "[BALLPARK-FAILED]" with the reason and a to-do.
A Render restart kills the thread silently: a "started" log with no to-do
means the run died.

A dry run does steps 2-3 only and keeps the result for /ballpark/status, so
the tool can be checked against past ballparks without touching JobTread.

Spec: docs/ballpark/BALLPARK_TOOL_BUILD_SPEC.md
"""

import json
import threading
import traceback
from datetime import date, datetime, timedelta, timezone

import ballpark_inputs as inputs
import ballpark_pricing as bp
import ballpark_scope
import scope_store
import site_scope

TYLER_MEMBERSHIP_ID = "22PBsSvmYBUj"
STORE_PREFIX = "ballpark:job:"
MAX_RUNNING = 2
MAX_LOG_CHARS = 15000
STARTED_MARKER = "[BALLPARK] started"
SUMMARY_MARKER = "[BALLPARK]"
FAILED_MARKER = "[BALLPARK-FAILED]"
PLACEHOLDER_SUFFIX = " (PRICING TBD)"
STALE_MINUTES = 15   # a "running" run not running on this server and quiet this long was cut off

_running = set()
_running_lock = threading.Lock()


class BallparkError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ── Run record ──────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save(run):
    run["updated_at"] = _now()
    try:
        scope_store.put(STORE_PREFIX + run["job"]["id"], run)
    except Exception as e:
        print(f"Ballpark: could not save run status ({e})")


def _load(job_id):
    try:
        return scope_store.get(STORE_PREFIX + job_id)
    except Exception as e:
        print(f"Ballpark: could not read run status ({e})")
        return None


def public(run):
    if not run:
        return None
    out = {k: run.get(k) for k in ("state", "step", "error", "dry_run", "started_at", "updated_at", "job")}
    res = run.get("result")
    if res:
        out["result"] = res
    return out


# ── JobTread writes ─────────────────────────────────────────────────────────

def _today():
    return date.today().isoformat()


def _next_business_day(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def write_daily_log(jobtread_query, job_id, notes):
    jobtread_query({"createDailyLog": {"$": {"jobId": job_id, "date": _today(), "notes": notes[:MAX_LOG_CHARS],
                                              "notify": False}, "createdDailyLog": {"id": {}}}})


def create_todo(jobtread_query, job_id, name, description=""):
    due = _next_business_day(date.today()).isoformat()
    task = {"name": name, "isToDo": True, "targetType": "job", "targetId": job_id,
            "startDate": due, "endDate": due, "assignees": [{"membershipId": TYLER_MEMBERSHIP_ID}]}
    if description:
        task["description"] = description[:4090]
    jobtread_query({"createTask": {"$": task}})


def _create_group(jobtread_query, job_id, name, description=""):
    args = {"jobId": job_id, "name": name[:250]}
    if description:
        args["description"] = description[:4096]
    resp = jobtread_query({"createCostGroup": {"$": args, "createdCostGroup": {"id": {}}}})
    gid = (((resp or {}).get("createCostGroup") or {}).get("createdCostGroup") or {}).get("id")
    if not gid:
        raise BallparkError(f"JobTread returned no id for cost group '{name}'.", 502)
    return gid


def _item_args(line):
    name = line["name"] + (PLACEHOLDER_SUFFIX if line["placeholder"] else "")
    args = {"name": name[:250], "quantity": line["quantity"], "unitCost": line["unit_cost"],
            "unitPrice": line["unit_price"], "costCodeId": line["cost_code"], "costTypeId": line["cost_type"]}
    if line.get("description"):
        args["description"] = line["description"][:4096]
    return args


def phase_notes_text(phase, reply, result):
    """The internal NOTES line for one phase: the rule behind each line, its
    placeholders, and how much to trust the read."""
    out = [f"Preliminary Ballpark Tool, {_today()}. Confidence: {reply.get('confidence') or 'not stated'}."]
    out += [f"- {n}" for n in phase["notes"]]
    for l in phase["lines"]:
        if l["placeholder"]:
            out.append(f"- {l['name']}: $0 placeholder - {l['placeholder']}")
    out.append("Per-number sources and conflicts are in the [BALLPARK] Daily Log on this job.")
    return "\n".join(out)


def write_budget(jobtread_query, job_id, result, reply, log=print):
    """One top-level cost group per phase: its lines, then the $0 NOTES line."""
    written = []
    for phase in result["phases"]:
        gid = _create_group(jobtread_query, job_id, phase["name"])
        for line in phase["lines"]:
            args = dict(_item_args(line), costGroupId=gid)
            try:
                jobtread_query({"createCostItem": {"$": args, "createdCostItem": {"id": {}}}})
            except Exception as e:
                if "description" not in args:
                    raise
                log(f"Ballpark: '{line['name']}' refused with its description ({e}); retrying without it")
                args.pop("description")
                jobtread_query({"createCostItem": {"$": args, "createdCostItem": {"id": {}}}})
        jobtread_query({"createCostItem": {"$": {
            "costGroupId": gid, "name": bp.NOTES_ITEM_NAME, "quantity": 1, "unitCost": 0, "unitPrice": 0,
            "costCodeId": bp.COST_CODE_INTERNAL_USE, "costTypeId": bp.COST_TYPE_OTHER,
            "description": phase_notes_text(phase, reply, result)[:4096], "showDescription": False,
        }, "createdCostItem": {"id": {}}}})
        written.append(phase["name"])
    return written


# ── The written summary ─────────────────────────────────────────────────────

def _bullets(items):
    return "\n".join(f"- {i}" for i in items if i)


def summary_text(job, result, reply, context):
    s = reply.get("summary") or {}
    rng = result["range"]
    margin_pct = f"{result['margin'] * 100:.1f}%"
    parts = [
        f"{SUMMARY_MARKER} Preliminary Ballpark - job {job.get('number')} {job.get('name') or ''}".strip(),
        f"Price range: {rng['text']}",
        f"Budget: cost ${result['total_cost']:,.2f}, price ${result['total_price']:,.2f} "
        f"({result['markup'] * 100:.0f}% markup, {margin_pct} margin, {result['weeks']:g} weeks)",
        "",
        "## Project Overview",
        (s.get("project_overview") or "").strip(),
        "",
        "### Further items included:",
        _bullets(s.get("further_items") or []),
        "",
        "### Exclusions:",
        _bullets(s.get("exclusions") or []),
    ]
    if s.get("tier_language"):
        parts += ["", "### Selections and allowances:", _bullets(s["tier_language"])]
    parts += ["", f"The billed amount will reflect the contractor margin of {margin_pct}.", "",
              "---------- INTERNAL REVIEW (not for the client) ----------"]
    review = []
    review += [f"Placeholder / missing: {x}" for x in result.get("review") or []]
    camera = context.get("companycam") or {}
    for m in camera.get("missing_transcripts") or []:
        review.append(f"Video {m.get('video_id')} ({m.get('captured_at')}): {m.get('reason')}")
    review += [f"Conflict: {c}" for c in reply.get("conflicts") or []]
    for f in (reply.get("scope") or {}).get("fixtures") or []:
        if f.get("unit_price"):
            review.append(f"Web-priced: {f.get('brand') or ''} {f.get('item')} ${bp._num(f['unit_price']):,.0f} "
                          f"- {f.get('price_source') or 'no source given'}")
    review += [f"Open question: {q}" for q in reply.get("open_questions") or []]
    review += [f"Note: {w}" for w in context.get("warnings") or []]
    review.append(f"Confidence: {reply.get('confidence') or 'not stated'}. "
                  f"Read {len(camera.get('transcripts') or [])} transcript(s), "
                  f"{len(camera.get('photos') or [])} photo description(s), "
                  f"{len(context.get('comments') or [])} comment(s), "
                  f"{len(context.get('daily_logs') or [])} Daily Log(s).")
    parts.append(_bullets(review))
    sources = reply.get("sources") or {}
    if sources:
        parts += ["", "Sources:", _bullets(f"{k}: {v}" for k, v in sources.items())]
    for est_key, est in ((reply.get("scope") or {}).get("estimates") or {}).items():
        if isinstance(est, dict):
            parts.append(f"- Estimate {est_key}: {est.get('value')} - {est.get('reasoning')}")
    return "\n".join(parts)


# ── The run ─────────────────────────────────────────────────────────────────

def _process(run, form, jobtread_query, org_id):
    job = run["job"]
    dry = run["dry_run"]

    def step(name):
        run["step"] = name
        _save(run)
        print(f"Ballpark job {job.get('number')}: {name}")

    try:
        if not dry:
            write_daily_log(jobtread_query, job["id"], f"{STARTED_MARKER} {_now()} - building the Preliminary "
                            "Ballpark budget. If no review to-do appears, the run stopped and should be restarted.")
        step("reading the site visit")
        context = inputs.gather(job["id"], jobtread_query, org_id)
        tier_prices = bp.load_tier_prices()
        step("working out the scope")
        reply = ballpark_scope.extract(context, form, tier_prices)
        step("pricing")
        template = bp.load_template(jobtread_query)
        result = bp.price_scope(reply["scope"], template, tier_prices)
        text = summary_text(job, result, reply, context)
        run["result"] = {"total_cost": result["total_cost"], "total_price": result["total_price"],
                         "range": result["range"]["text"], "markup": result["markup"], "margin": result["margin"],
                         "phases": len(result["phases"]), "placeholders": len(result["review"]),
                         "claude_cost_usd": reply.get("cost_usd")}
        if dry:
            run["result"].update({"summary": text, "scope": reply["scope"], "budget": result})
        else:
            step("writing the budget")
            if inputs.budget_group_count(jobtread_query, job["id"]):
                raise BallparkError("The job got a budget while this run was working, so nothing was written.", 409)
            written = write_budget(jobtread_query, job["id"], result, reply)
            run["result"]["groups_written"] = len(written)
            step("writing the summary")
            write_daily_log(jobtread_query, job["id"], text)
            create_todo(jobtread_query, job["id"], "Review Preliminary Ballpark budget",
                        f"The ballpark tool built the budget ({rng_line(result)}). The written summary is in the "
                        "[BALLPARK] Daily Log on this job; its internal review section lists placeholders and "
                        "anything that needs a look.")
        run["state"] = "done"
        run["step"] = "done"
        _save(run)
        print(f"Ballpark job {job.get('number')}: done - cost ${result['total_cost']:,.2f}, "
              f"price ${result['total_price']:,.2f}, range {result['range']['text']}"
              + (" (dry run)" if dry else ""))
    except Exception as e:
        print(f"Ballpark job {job.get('number')} failed: {e}\n{traceback.format_exc()}")
        run["state"] = "failed"
        run["error"] = str(e)
        _save(run)
        if not dry:
            reason = f"{FAILED_MARKER} {_now()} - the Preliminary Ballpark run stopped at '{run.get('step')}': {e}"
            if run.get("step") == "writing the budget":
                reason += " Part of the budget may have been written; check the job's budget before re-running."
            try:
                write_daily_log(jobtread_query, job["id"], reason)
                create_todo(jobtread_query, job["id"], "Preliminary Ballpark run failed", reason)
            except Exception as e2:
                print(f"Ballpark job {job.get('number')}: could not record the failure in JobTread: {e2}")
    finally:
        with _running_lock:
            _running.discard(job["id"])


def rng_line(result):
    return f"{result['range']['text']}, cost ${result['total_cost']:,.0f}"


def start(payload, jobtread_query, org_id, start_thread=None):
    job_ref = str(payload.get("job") or "").strip()
    form = payload.get("form") or {}
    if not isinstance(form, dict):
        raise BallparkError("form must be an object.")
    dry = bool(payload.get("dry_run"))
    try:
        job = inputs.find_job(job_ref, jobtread_query, org_id)
        inputs.check_job_type(job)
    except inputs.InputError as e:
        raise BallparkError(str(e), e.status)
    if not dry and inputs.budget_group_count(jobtread_query, job["id"]):
        raise BallparkError(f"Job {job.get('number')} already has a budget. The ballpark tool only builds a "
                            "budget on a job with none, so nothing was changed.", 409)
    with _running_lock:
        if job["id"] in _running:
            raise BallparkError(f"A ballpark run is already going on job {job.get('number')}.", 409)
        if len(_running) >= MAX_RUNNING:
            raise BallparkError("Two ballpark runs are already going. Try again in a few minutes.", 429)
        _running.add(job["id"])
    run = {"job": {"id": job["id"], "number": job.get("number"), "name": job.get("name")},
           "state": "running", "step": "starting", "error": None, "dry_run": dry, "started_at": _now()}
    _save(run)
    (start_thread or _start_thread)(_process, run, form, jobtread_query, org_id)
    msg = ("Dry run started. Nothing will be written to JobTread; check back here in a few minutes."
           if dry else "Started. The budget will appear on the job with a review to-do for Tyler when it's done "
                       "(usually a few minutes). No need to wait here or submit again.")
    return 202, {"ok": True, "message": msg, "run": public(run)}


def _start_thread(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()


def status(payload, jobtread_query, org_id):
    job_ref = str(payload.get("job") or "").strip()
    try:
        job = inputs.find_job(job_ref, jobtread_query, org_id)
    except inputs.InputError as e:
        raise BallparkError(str(e), e.status)
    run = _load(job["id"])
    with _running_lock:
        here = job["id"] in _running
    if run and run.get("state") == "running" and not here and _age_minutes(run.get("updated_at")) > STALE_MINUTES:
        run["state"] = "stopped"
        run["error"] = ("The run stopped without finishing (the server most likely restarted). "
                        "Check the job for a partial budget, then start it again.")
    return 200, {"ok": True, "run": public(run)}


def _age_minutes(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return 0


def handle_post(path, body, headers, jobtread_query, org_id, start_thread=None):
    """Returns (status, body dict). app.py sends every POST /ballpark* here."""
    try:
        site_scope.check_api_key(headers.get("X-Scope-Key"))
        try:
            payload = json.loads(body or "{}")
        except ValueError:
            raise BallparkError("Body must be JSON.")
        if not isinstance(payload, dict):
            raise BallparkError("Body must be a JSON object.")
        if path == "/ballpark":
            return start(payload, jobtread_query, org_id, start_thread)
        if path == "/ballpark/status":
            return status(payload, jobtread_query, org_id)
        raise BallparkError("Not found.", 404)
    except (BallparkError, site_scope.PreviewError) as e:
        return e.status, {"ok": False, "error": str(e)}
    except Exception as e:
        print(f"Ballpark request failed: {e}\n{traceback.format_exc()}")
        return 500, {"ok": False, "error": f"Unexpected error: {e}"}
