"""Site Scope Tool - getting drawings into CompanyCam (phase 2).

CompanyCam's public API only adds a photo from a URL: POST
/projects/{id}/photos with {"photo": {"uri": ...}}, and CompanyCam downloads
the image itself after the call returns (same call sales-lead-tool's
pushPhoto() makes with Vercel Blob URLs). There is no documented direct
upload. So for each drawing this module:

1. keeps the PNG in memory under a long random token for a limited time,
2. serves it at GET /scope/files/<token>/<filename> (registered in app.py;
   no key, since CompanyCam can't send one - the token is the lock),
3. sends that URL to CompanyCam and waits until CompanyCam has downloaded
   and processed the image.

It also holds the phase 2 test: GET /scope/drawings-test draws the Oct 19
Test (1) office sample and uploads it to CompanyCam project "Test (1)".
"""

import json
import os
import secrets
import threading
import time
import traceback
from datetime import datetime, timezone

import companycam_api as cc
import site_scope

TEST_PROJECT_ID = "117058039"      # CompanyCam "Test (1)", Jason's office
SAMPLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "docs", "site-scope", "samples", "test1-office-2026-10-19.json")

TEMP_FILE_SECONDS = 60 * 60        # CompanyCam usually fetches within seconds
MAX_TEMP_BYTES = 25 * 1024 * 1024  # never hold more than this in memory
FILES_PATH = "/scope/files/"
WAIT_SECONDS = 180                 # how long to wait for CompanyCam to process
POLL_SECONDS = 5

_files = {}
_files_lock = threading.Lock()


# ── Temporary public files ───────────────────────────────────────────────────

def public_base_url():
    """The service's public https address. Render sets RENDER_EXTERNAL_URL on
    every web service; SCOPE_PUBLIC_URL overrides it."""
    url = os.environ.get("SCOPE_PUBLIC_URL", "").strip() or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    return url.rstrip("/")


def _purge(now):
    for token in [t for t, f in _files.items() if f["expires"] <= now]:
        del _files[token]


def put_temp_file(data, content_type, name, seconds=TEMP_FILE_SECONDS, now=None):
    """Hold bytes for `seconds` and return the token they are served under."""
    now = time.time() if now is None else now
    with _files_lock:
        _purge(now)
        total = sum(len(f["data"]) for f in _files.values())
        # Drop the oldest files first if this one would go over the cap.
        for token in sorted(_files, key=lambda t: _files[t]["expires"]):
            if total + len(data) <= MAX_TEMP_BYTES:
                break
            total -= len(_files.pop(token)["data"])
        token = secrets.token_urlsafe(32)
        _files[token] = {"data": data, "type": content_type, "name": name,
                         "expires": now + seconds, "fetches": 0}
        return token


def get_temp_file(token, now=None):
    """The held file for a token, or None if unknown or expired. Counts the
    fetch, so the test can tell whether CompanyCam actually downloaded it."""
    now = time.time() if now is None else now
    with _files_lock:
        _purge(now)
        f = _files.get(token)
        if f:
            f["fetches"] += 1
        return f


def fetch_count(token):
    with _files_lock:
        f = _files.get(token)
        return f["fetches"] if f else 0


def handle_file_request(path):
    """HTTP glue for GET /scope/files/<token>/<name>. Returns
    (status, content_type, body bytes)."""
    rest = path[len(FILES_PATH):] if path.startswith(FILES_PATH) else ""
    token = rest.split("/", 1)[0]
    f = get_temp_file(token) if token else None
    if not f:
        return 404, "text/plain; charset=utf-8", b"Not found (links expire after an hour)."
    return 200, f["type"], f["data"]


# ── Uploading ────────────────────────────────────────────────────────────────

def upload_drawings(project_id, drawings, base_url, description_for, wait_seconds=None,
                    poll_seconds=None, sleep=time.sleep, clock=time.time):
    """Send each drawing to a CompanyCam project as a photo.

    drawings: from scope_drawings.render_drawings(). description_for(drawing)
    gives the photo description. Never raises for one bad drawing - each
    result says what happened. Returns a list of result dicts."""
    wait_seconds = WAIT_SECONDS if wait_seconds is None else wait_seconds
    poll_seconds = POLL_SECONDS if poll_seconds is None else poll_seconds
    results = []
    for d in drawings:
        token = put_temp_file(d["png"], "image/png", d["filename"])
        uri = f"{base_url}{FILES_PATH}{token}/{d['filename']}"
        r = {"drawing": d["title"], "filename": d["filename"], "bytes": len(d["png"]), "_token": token,
             "temp_url": uri}
        try:
            photo = cc.create_project_photo(project_id, uri, description=description_for(d))
            r["photo_id"] = str(photo.get("id") or "") or None
            r["status"] = "sent" if r["photo_id"] else "CompanyCam did not return a photo ID"
        except cc.CompanyCamError as e:
            r["status"] = "upload refused"
            r["error"] = str(e)
        results.append(r)

    pending = [r for r in results if r.get("photo_id")]
    deadline = clock() + wait_seconds
    while pending and clock() < deadline:
        sleep(poll_seconds)
        for r in list(pending):
            try:
                photo = cc.get_photo(r["photo_id"])
            except cc.CompanyCamError as e:
                r["last_check_error"] = str(e)
                continue
            ready = cc.photo_is_ready(photo)
            if ready is None:
                r["status"] = f"CompanyCam could not process it ({photo.get('processing_status')})"
                pending.remove(r)
            elif ready:
                r["status"] = "in CompanyCam"
                r["photo_url"] = cc.photo_uri(photo, "web") or cc.photo_uri(photo, "original")
                if photo.get("photo_url"):
                    r["companycam_link"] = photo["photo_url"]
                pending.remove(r)
    for r in pending:
        r["status"] = f"still processing after {wait_seconds} seconds - check the project"
    for r in results:
        r["companycam_downloaded_it"] = fetch_count(r.pop("_token")) > 0
    return results


# ── Memory ───────────────────────────────────────────────────────────────────

def memory_snapshot():
    """Current and peak memory of this whole server process, in MB. Linux
    (Render) reads /proc; elsewhere only the peak is available."""
    try:
        with open("/proc/self/status") as f:
            vals = {line.split(":")[0]: line.split()[1] for line in f if line.startswith(("VmRSS", "VmHWM"))}
        return {"now_mb": round(int(vals["VmRSS"]) / 1024, 1), "peak_mb": round(int(vals["VmHWM"]) / 1024, 1)}
    except (OSError, KeyError, ValueError, IndexError):
        import resource
        import sys
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
        return {"now_mb": None, "peak_mb": round(peak_mb, 1)}


# ── Phase 2 test run ─────────────────────────────────────────────────────────

_run_lock = threading.Lock()
_last_run = {"state": "not run yet since the server started"}


def last_run():
    return dict(_last_run)


def run_drawing_test(project_id=TEST_PROJECT_ID, base_url=None, sample_file=SAMPLE_FILE, **upload_kwargs):
    """Draw the sample room file and upload the drawings. Catches everything:
    a failure here is logged and recorded, never raised into the server."""
    global _last_run
    record = {"state": "running", "started": _now(), "project_id": project_id}
    _last_run = record
    try:
        before = memory_snapshot()
        import scope_drawings   # Pillow loads only when drawings are made
        with open(sample_file) as f:
            data = json.load(f)
        t0 = time.time()
        result = scope_drawings.render_drawings(data)
        render_seconds = round(time.time() - t0, 2)
        after = memory_snapshot()
        record["memory"] = {"before_render": before, "after_render": after,
                            "note": "Whole server process (estimator + follow-ups + this), in MB."}
        record["render_seconds"] = render_seconds
        record["flags"] = result["flags"]
        print(f"[scope drawings] drew {len(result['drawings'])} drawings in {render_seconds}s; "
              f"memory before {before}, after {after}")

        visit = scope_drawings.visit_words(data.get("project") or {})

        def describe(d):
            return (f"{d['title']} - drawn by the Site Scope Tool {visit} notes (phase 2 test). "
                    "For planning and quoting only.")

        uploads = upload_drawings(project_id, result["drawings"], base_url or public_base_url(), describe,
                                  **upload_kwargs)
        record["uploads"] = uploads
        ok = sum(1 for u in uploads if u["status"] == "in CompanyCam")
        record["summary"] = f"{ok} of {len(uploads)} drawings are in CompanyCam project {project_id}."
        record["state"] = "done"
        print(f"[scope drawings] {record['summary']}")
        for u in uploads:
            print(f"[scope drawings]   {u['filename']}: {u['status']}"
                  f" (CompanyCam downloaded it: {u['companycam_downloaded_it']})")
    except Exception as e:
        record["state"] = "failed"
        record["error"] = f"{type(e).__name__}: {e}"
        print(f"[scope drawings] test run failed: {e}\n{traceback.format_exc()}")
    finally:
        record["finished"] = _now()
    return record


def _run_and_release():
    try:
        run_drawing_test()
    finally:
        _run_lock.release()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def handle_drawing_test_request(params, headers, start_thread=None):
    """HTTP glue for GET /scope/drawings-test. Returns (status, body dict).

    ?key=...            start a test run (answers straight away; the work
                        runs on a background thread)
    ?key=...&status=1   show how the last run went
    """
    def param(name):
        return (params.get(name) or [""])[0]

    try:
        site_scope.check_api_key(headers.get("X-Scope-Key") or param("key"))
    except site_scope.PreviewError as e:
        return e.status, {"ok": False, "error": str(e)}

    if param("status"):
        return 200, {"ok": True, "running": _run_lock.locked(), "last_run": _public(last_run())}
    if not cc.is_configured():
        return 503, {"ok": False, "error": "COMPANYCAM_API_KEY is not set on this server."}
    if not public_base_url():
        return 503, {"ok": False, "error": "This server doesn't know its public address "
                                           "(RENDER_EXTERNAL_URL or SCOPE_PUBLIC_URL)."}
    if not _run_lock.acquire(blocking=False):
        return 409, {"ok": False, "error": "A drawing test is already running. Add &status=1 to see it."}
    try:
        (start_thread or _start_thread)(_run_and_release)
    except Exception as e:
        _run_lock.release()
        return 500, {"ok": False, "error": f"Could not start the test: {e}"}
    return 202, {"ok": True,
                 "message": "Drawing test started: the Oct 19 office drawings are being drawn and sent to "
                            f"CompanyCam project Test (1) ({TEST_PROJECT_ID}). Give it a minute or two, then "
                            "open this same link with &status=1 added to the end to see how it went."}


def _public(record):
    """The status view leaves out the temporary download links."""
    out = dict(record)
    if "uploads" in out:
        out["uploads"] = [{k: v for k, v in u.items() if k != "temp_url"} for u in out["uploads"]]
    return out


def _start_thread(target):
    t = threading.Thread(target=target, name="scope-drawings-test")
    t.daemon = True
    t.start()
