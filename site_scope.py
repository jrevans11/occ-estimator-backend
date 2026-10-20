"""Site Scope Tool - run logic.

Phase 1 (this file today): the "what did we find" check. Given a JobTread
job, find its CompanyCam project through job.companycamId and report what the
site visit captured - photos, videos (and whether their transcripts are
ready), and whether the project already has a "Site Visit Notes" Page. This
is what the scope page shows before Jason taps Start, and it proves the
backend can reach CompanyCam.

Read-only: nothing here writes to CompanyCam or JobTread.

See docs/site-scope/site-scope-tool-build-plan.md for the full plan.
"""

import hmac
import os
import re
from datetime import datetime, timedelta, timezone

import companycam_api as cc

SITE_VISIT_NOTES_TITLE = "Site Visit Notes"
# Videos shorter than this are accidental taps (build plan, Capture method).
MIN_VIDEO_SECONDS = 5
# Transcript lookups are one call each; a visit has a handful of videos.
MAX_TRANSCRIPT_CHECKS = 10
# This many photos sharing one capture minute = a camera-roll batch upload.
BATCH_UPLOAD_MIN_PHOTOS = 5


class PreviewError(Exception):
    """A problem the caller should see as-is. `status` is the HTTP status."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


# ── Time ─────────────────────────────────────────────────────────────────────

def _eastern_offset(utc_dt):
    """UTC offset for US Eastern time, without needing tzdata installed.
    DST runs from 2 AM on the second Sunday of March to 2 AM on the first
    Sunday of November."""
    y = utc_dt.year

    def nth_sunday(month, n):
        first = datetime(y, month, 1, tzinfo=timezone.utc)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))

    dst_start = nth_sunday(3, 2) + timedelta(hours=7)   # 2 AM EST = 07:00 UTC
    dst_end = nth_sunday(11, 1) + timedelta(hours=6)    # 2 AM EDT = 06:00 UTC
    return timedelta(hours=-4) if dst_start <= utc_dt < dst_end else timedelta(hours=-5)


def to_local(ts):
    """Unix seconds -> naive datetime in Greenville local time."""
    utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return (utc_dt + _eastern_offset(utc_dt)).replace(tzinfo=None)


def _fmt_local(ts):
    return to_local(ts).strftime("%Y-%m-%d %I:%M %p") if ts is not None else None


def _pretty_date(iso_date):
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"{d.strftime('%b')} {d.day}, {d.year}"


# ── Auth ─────────────────────────────────────────────────────────────────────

def check_api_key(provided):
    """The scope endpoints return client addresses and photo links, so they
    need the shared SCOPE_API_KEY. With no key configured they stay closed."""
    expected = os.environ.get("SCOPE_API_KEY", "").strip()
    if not expected:
        raise PreviewError("SCOPE_API_KEY is not set on this server, so the scope endpoints are turned off.", 503)
    if not provided or not hmac.compare_digest(provided.strip(), expected):
        raise PreviewError("Missing or wrong key.", 401)


# ── JobTread ─────────────────────────────────────────────────────────────────

_JOB_FIELDS = {
    "id": {}, "number": {}, "name": {}, "companycamId": {},
    "location": {"address": {}},
}


def find_job(job_ref, jobtread_query, org_id):
    """Look a job up by JobTread job ID or by job number ("00768" or "768")."""
    job_ref = (job_ref or "").strip()
    if not job_ref:
        raise PreviewError("Add ?job= with a JobTread job number (like 00768) or job ID.", 400)

    if job_ref.isdigit():
        number = job_ref.zfill(5)
        resp = jobtread_query({
            "organization": {
                "$": {"id": org_id},
                "jobs": {"$": {"where": ["number", number], "size": 1}, "nodes": _JOB_FIELDS},
            }
        })
        nodes = (((resp or {}).get("organization") or {}).get("jobs") or {}).get("nodes") or []
        job = nodes[0] if nodes else None
    else:
        resp = jobtread_query({"job": {"$": {"id": job_ref}, **_JOB_FIELDS}})
        job = (resp or {}).get("job")

    if not job or not job.get("id"):
        raise PreviewError(f"No JobTread job found for '{job_ref}'.", 404)
    return job


# ── Preview ──────────────────────────────────────────────────────────────────

def _video_duration(video):
    for key in ("duration", "duration_seconds", "length"):
        v = video.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _pick_visit_date(date_param, photos, videos):
    """Which day counts as "this visit".

    Default: the day of the newest photo or video, so a preview run the day
    after a visit still finds it. "all" = everything on the project.
    """
    date_param = (date_param or "").strip().lower()
    if date_param == "all":
        return None, "all photos and videos on the project"
    if date_param:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_param):
            raise PreviewError("date must look like 2026-10-19, or be 'all'.", 400)
        return date_param, "date you asked for"
    stamps = [cc.parse_timestamp(m.get("captured_at")) for m in photos + videos]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        return None, "no photos or videos yet"
    return to_local(max(stamps)).strftime("%Y-%m-%d"), "day of the newest photo or video"


def _in_visit(item, visit_date):
    if visit_date is None:
        return True
    ts = cc.parse_timestamp(item.get("captured_at"))
    return ts is not None and to_local(ts).strftime("%Y-%m-%d") == visit_date


def build_visit_preview(job_ref, jobtread_query, org_id, date_param=""):
    """Everything the scope page shows before Start. Returns a dict."""
    if not cc.is_configured():
        raise PreviewError("COMPANYCAM_API_KEY is not set on this server.", 503)

    job = find_job(job_ref, jobtread_query, org_id)
    job_out = {
        "id": job.get("id"),
        "number": job.get("number"),
        "name": job.get("name"),
        "address": (job.get("location") or {}).get("address"),
    }
    project_id = (job.get("companycamId") or "").strip()
    if not project_id:
        raise PreviewError(
            f"Job {job.get('number')} has no CompanyCam project linked yet. JobTread normally links one "
            "a few seconds after a job is created - check the job's CompanyCam tab.", 404)

    warnings = []
    try:
        project = cc.get_project(project_id)
    except cc.CompanyCamError as e:
        raise PreviewError(f"Could not open CompanyCam project {project_id}: {e}", 502)

    photos = cc.list_project_photos(project_id)

    try:
        videos = cc.list_project_videos(project_id)
    except cc.CompanyCamError as e:
        videos = []
        warnings.append(f"Could not list videos ({e}). Videos are optional, so the run can still go ahead.")

    visit_date, visit_rule = _pick_visit_date(date_param, photos, videos)

    visit_photos = [p for p in photos if _in_visit(p, visit_date)]
    photo_items = []
    for p in visit_photos:
        ts = cc.parse_timestamp(p.get("captured_at"))
        photo_items.append({
            "id": str(p.get("id")),
            "captured_at": _fmt_local(ts),
            "thumbnail": cc.photo_uri(p, "thumbnail"),
            "web": cc.photo_uri(p, "web"),
            "description": _photo_description(p),
        })
    photo_items.sort(key=lambda x: x["captured_at"] or "")  # capture order, like the visit
    batch_warning = _batch_upload_warning(visit_photos)
    if batch_warning:
        warnings.append(batch_warning)

    video_items = []
    transcript_checks = 0
    for v in videos:
        if not _in_visit(v, visit_date):
            continue
        ts = cc.parse_timestamp(v.get("captured_at"))
        duration = _video_duration(v)
        item = {
            "id": str(v.get("id")),
            "captured_at": _fmt_local(ts),
            "duration_seconds": duration,
            "transcript": "unknown",
            "skipped": False,
        }
        if duration is not None and duration < MIN_VIDEO_SECONDS:
            item["skipped"] = True
            item["transcript"] = "not needed (under 5 seconds - treated as an accidental tap)"
        elif transcript_checks < MAX_TRANSCRIPT_CHECKS:
            transcript_checks += 1
            try:
                text = cc.get_video_transcript(v.get("id"))
                if text is cc.TRANSCRIPT_MISSING:
                    item["transcript"] = "unknown (CompanyCam did not say)"
                else:
                    item["transcript"] = "ready" if text else "not ready"
            except cc.CompanyCamError as e:
                warnings.append(f"Could not check the transcript for video {item['id']} ({e}).")
        video_items.append(item)
    video_items.sort(key=lambda x: x["captured_at"] or "")

    notes_page = {"exists": False}
    try:
        for pg in cc.list_project_pages(project_id):
            title = (pg.get("title") or pg.get("name") or "").strip()
            if title.lower() == SITE_VISIT_NOTES_TITLE.lower():
                notes_page = {"exists": True, "id": str(pg.get("id")), "title": title}
                break
    except cc.CompanyCamError as e:
        notes_page = {"exists": None}
        warnings.append(f"Could not list Pages ({e}).")

    other_projects = _other_projects_at_address(project, warnings)

    result = {
        "ok": True,
        "job": job_out,
        "companycam_project": {
            "id": str(project.get("id") or project_id),
            "name": project.get("name"),
            "url": project.get("project_url") or f"https://app.companycam.com/projects/{project_id}",
            "photo_count_on_project": len(photos),
        },
        "visit_date": visit_date,
        "visit_date_rule": visit_rule,
        "photos": {"count": len(photo_items), "items": photo_items},
        "videos": {"count": len(video_items), "items": video_items},
        "site_visit_notes_page": notes_page,
        "other_projects_at_address": other_projects,
        "warnings": warnings,
    }
    result["summary"] = summarize(result)
    return result


def _photo_description(photo):
    """The Public API sends description as {plain_text_content, html_content, id}."""
    d = photo.get("description")
    if isinstance(d, dict):
        return (d.get("plain_text_content") or "").strip()
    return str(d or "").strip()


def _batch_upload_warning(photos):
    """Photos added from the camera roll get the upload time as captured_at,
    so a whole visit shows the same minute (job 00768: 15 photos at 2:25 PM,
    videos at 10:10-10:17 AM). Notes can't be matched to those by time."""
    if len(photos) < BATCH_UPLOAD_MIN_PHOTOS:
        return None
    minutes = {}
    for p in photos:
        ts = cc.parse_timestamp(p.get("captured_at"))
        if ts is not None:
            key = ts // 60
            minutes[key] = minutes.get(key, 0) + 1
    if not minutes:
        return None
    biggest = max(minutes.values())
    if biggest >= BATCH_UPLOAD_MIN_PHOTOS and biggest >= len(photos) * 0.8:
        return (f"{biggest} of {len(photos)} photos have the same capture minute, so they look like they were "
                "uploaded in a batch after the visit. Their times are upload times, so notes can't be matched "
                "to them by time. Take site photos with the CompanyCam camera to keep real capture times.")
    return None


def _other_projects_at_address(project, warnings):
    """Duplicate CompanyCam projects at the same street address (e.g. a
    JobTread-synced "Job 00XXX" project plus a hand-made address project).
    The photos may be on the other one, so the preview points them out."""
    street = ((project.get("address") or {}).get("street_address_1") or "").strip()
    if not street:
        return []
    try:
        matches = cc.search_projects(street)
    except cc.CompanyCamError as e:
        warnings.append(f"Could not check for duplicate projects at this address ({e}).")
        return []
    out = []
    for m in matches:
        if str(m.get("id")) == str(project.get("id")) or m.get("archived"):
            continue
        m_street = ((m.get("address") or {}).get("street_address_1") or "").strip()
        if m_street.lower() != street.lower():
            continue
        out.append({
            "id": str(m.get("id")),
            "name": m.get("name"),
            "photo_count": m.get("photo_count"),
            "url": m.get("project_url"),
        })
    return out


def summarize(result):
    """One plain-English line for the top of the response."""
    photos = result["photos"]["count"]
    videos = [v for v in result["videos"]["items"] if not v["skipped"]]
    when = _pretty_date(result["visit_date"]) if result["visit_date"] else "the whole project"
    parts = [f"{photos} photo{'s' if photos != 1 else ''}"]
    if videos:
        ready = sum(1 for v in videos if v["transcript"] == "ready")
        parts.append(f"{len(videos)} video{'s' if len(videos) != 1 else ''} ({ready} with a transcript ready)")
    line = f"Job {result['job']['number']}: {' and '.join(parts)} from {when}."
    page = result["site_visit_notes_page"]
    if page.get("exists") is True:
        line += " The project already has a Site Visit Notes Page."
    elif page.get("exists") is False:
        line += " No Site Visit Notes Page yet (the tool will create it)."
    if result["other_projects_at_address"]:
        line += f" Heads up: {len(result['other_projects_at_address'])} other CompanyCam project(s) share this address."
    return line


def handle_preview_request(params, headers, jobtread_query, org_id):
    """HTTP glue for GET /scope/preview. Returns (status, body dict).

    params: parsed query string ({name: [values]}).
    Key goes in the X-Scope-Key header, or ?key= for testing in a browser.
    """
    def param(name):
        return (params.get(name) or [""])[0]

    try:
        check_api_key(headers.get("X-Scope-Key") or param("key"))
        return 200, build_visit_preview(param("job"), jobtread_query, org_id, param("date"))
    except PreviewError as e:
        return e.status, {"ok": False, "error": str(e)}
    except cc.CompanyCamError as e:
        return 502, {"ok": False, "error": str(e)}
    except Exception as e:
        import traceback
        print(f"/scope/preview failed: {e}\n{traceback.format_exc()}")
        return 500, {"ok": False, "error": f"Unexpected error: {e}"}
