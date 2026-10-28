"""Preliminary Ballpark Tool: gather the site-visit material for one job.

Pulls, for a Renovation or Addition job:
  - CompanyCam: video transcripts (walkthroughs and Tyler's scope summary),
    photo descriptions, Page text. The project comes from JobTread
    job.companycamId, falling back to a CompanyCam search by address.
  - JobTread: job comments, Daily Logs (minus [OCC-AUTO] and the tool's own
    [BALLPARK] logs), Rendr parameters (job.parameters, usually null today).

Returns one context dict; ballpark_scope.py turns it into a scope. Nothing
here writes anywhere.
"""

import companycam_api as cc
from site_scope import _photo_description, _video_duration

ALLOWED_JOB_TYPES = ("Renovation", "Addition")
JOB_TYPE_FIELD = "22PAU2yp8N2U"

MAX_VIDEOS = 25
MAX_TRANSCRIPT_CHARS = 150_000    # all transcripts together
MAX_PHOTOS = 300
MIN_VIDEO_SECONDS = 5
SKIP_LOG_MARKERS = ("[OCC-AUTO]", "[BALLPARK", "[AI-FEEDBACK")
SUMMARY_WORDS = ("scope summary", "summary", "scope")


class InputError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


_JOB_FIELDS = {
    "id": {}, "number": {}, "name": {}, "companycamId": {}, "parameters": {},
    "location": {"id": {}, "address": {}},
    "customFieldValues": {"$": {"size": 25}, "nodes": {"value": {}, "customField": {"id": {}, "name": {}}}},
}


def find_job(job_ref, jobtread_query, org_id):
    """A job by JobTread ID or job number ("00844" / "844")."""
    job_ref = (job_ref or "").strip()
    if not job_ref:
        raise InputError("Pick a job.")
    if job_ref.isdigit():
        resp = jobtread_query({"organization": {"$": {"id": org_id}, "jobs": {
            "$": {"where": ["number", job_ref.zfill(5)], "size": 1}, "nodes": _JOB_FIELDS}}})
        nodes = (((resp or {}).get("organization") or {}).get("jobs") or {}).get("nodes") or []
        job = nodes[0] if nodes else None
    else:
        job = ((jobtread_query({"job": {"$": {"id": job_ref}, **_JOB_FIELDS}}) or {}).get("job"))
    if not job or not job.get("id"):
        raise InputError(f"No JobTread job found for '{job_ref}'.", 404)
    fields = {}
    for n in ((job.get("customFieldValues") or {}).get("nodes") or []):
        cf = n.get("customField") or {}
        fields[cf.get("id")] = n.get("value")
        fields[cf.get("name")] = n.get("value")
    job["job_type"] = fields.get(JOB_TYPE_FIELD) or ""
    job["custom_fields"] = {k: v for k, v in fields.items() if k and not str(k).startswith("22P")}
    return job


def check_job_type(job):
    if job.get("job_type") not in ALLOWED_JOB_TYPES:
        raise InputError(f"Job {job.get('number')} is a '{job.get('job_type') or 'no type'}' job. "
                         "The ballpark tool only runs on Renovation and Addition jobs.", 409)


def budget_group_count(jobtread_query, job_id):
    """Budget cost groups on the job (document: null). Estimate-document copies
    don't count. JobTread rejects a where on document, so filter here."""
    count, page = 0, None
    for _ in range(20):
        args = {"size": 100}
        if page:
            args["page"] = page
        resp = jobtread_query({"job": {"$": {"id": job_id}, "costGroups": {
            "$": args, "nodes": {"id": {}, "document": {"id": {}}}, "nextPage": {}}}})
        conn = ((resp or {}).get("job") or {}).get("costGroups") or {}
        count += sum(1 for g in conn.get("nodes") or [] if not g.get("document"))
        page = conn.get("nextPage")
        if not page:
            break
    return count


def _paged(jobtread_query, job_id, field, nodes, size=100, max_pages=5):
    out, page = [], None
    for _ in range(max_pages):
        args = {"size": size}
        if page:
            args["page"] = page
        resp = jobtread_query({"job": {"$": {"id": job_id}, field: {"$": args, "nodes": nodes, "nextPage": {}}}})
        conn = ((resp or {}).get("job") or {}).get(field) or {}
        out.extend(conn.get("nodes") or [])
        page = conn.get("nextPage")
        if not page:
            break
    return out


def _companycam_project(job, warnings):
    pid = str(job.get("companycamId") or "").strip()
    if pid:
        return pid
    address = ((job.get("location") or {}).get("address") or "").strip()
    if not address:
        return ""
    street = address.split(",")[0]
    try:
        found = cc._data(cc._get("/projects", {"query": street})) or []
    except cc.CompanyCamError as e:
        warnings.append(f"CompanyCam search by address failed: {e}")
        return ""
    if found:
        warnings.append(f"Job has no CompanyCam link; used the project found by address '{street}'.")
        return str(found[0].get("id"))
    return ""


def _page_text(doc):
    def walk(node):
        if node.get("type") == "text":
            return node.get("text") or ""
        inner = "".join(walk(c) for c in node.get("content") or [])
        return inner + ("\n" if node.get("type") in ("paragraph", "heading", "listItem") else "")
    return walk(doc or {}).strip()


def _is_summary(video, index, total):
    name = " ".join(str(video.get(k) or "") for k in ("name", "title", "description")).lower()
    return any(w in name for w in SUMMARY_WORDS) or index == total - 1


def gather_companycam(project_id, warnings):
    out = {"project_id": project_id, "transcripts": [], "missing_transcripts": [], "photos": [], "pages": []}
    if not project_id:
        warnings.append("No CompanyCam project for this job, so no videos, photos or notes were read.")
        return out
    if not cc.is_configured():
        warnings.append("COMPANYCAM_API_KEY is not set on this server; CompanyCam was skipped.")
        return out

    try:
        videos = cc.list_project_videos(project_id)
    except cc.CompanyCamError as e:
        videos = []
        warnings.append(f"Could not list CompanyCam videos: {e}")
    videos = [v for v in videos if (_video_duration(v) is None or _video_duration(v) >= MIN_VIDEO_SECONDS)]
    videos.sort(key=lambda v: cc.parse_timestamp(v.get("captured_at")) or 0)
    videos = videos[-MAX_VIDEOS:]
    chars = 0
    for i, v in enumerate(videos):
        vid = str(v.get("id"))
        when = v.get("captured_at")
        try:
            text = cc.get_video_transcript(vid)
        except cc.CompanyCamError as e:
            out["missing_transcripts"].append({"video_id": vid, "captured_at": when, "reason": f"error: {e}"})
            continue
        if text is cc.TRANSCRIPT_MISSING or not text:
            out["missing_transcripts"].append({"video_id": vid, "captured_at": when,
                                               "reason": "no transcript (not generated yet, or never)"})
            continue
        if chars + len(text) > MAX_TRANSCRIPT_CHARS:
            out["missing_transcripts"].append({"video_id": vid, "captured_at": when,
                                               "reason": "skipped: transcripts too long to send together"})
            continue
        chars += len(text)
        out["transcripts"].append({"video_id": vid, "captured_at": when, "duration": _video_duration(v),
                                   "likely_scope_summary": _is_summary(v, i, len(videos)), "text": text})

    try:
        photos = cc.list_project_photos(project_id)
    except cc.CompanyCamError as e:
        photos = []
        warnings.append(f"Could not list CompanyCam photos: {e}")
    for p in photos[:MAX_PHOTOS]:
        desc = _photo_description(p)
        tags = [t.get("display_value") or t.get("value") for t in (p.get("tags") or []) if isinstance(t, dict)]
        if desc or tags:
            out["photos"].append({"captured_at": p.get("captured_at"), "description": desc,
                                  "tags": [t for t in tags if t]})
    out["photo_count"] = len(photos)

    try:
        for pg in cc.list_project_pages(project_id):
            title = (pg.get("title") or pg.get("name") or "").strip()
            try:
                text = _page_text(cc.get_page(pg.get("id")).get("tiptap_content"))
            except cc.CompanyCamError as e:
                warnings.append(f"Could not read CompanyCam Page '{title}': {e}")
                continue
            if text:
                out["pages"].append({"title": title, "text": text[:20000]})
    except cc.CompanyCamError as e:
        warnings.append(f"Could not list CompanyCam Pages: {e}")
    return out


def gather(job_ref, jobtread_query, org_id):
    """Everything the scope extraction reads, for one job."""
    warnings = []
    job = find_job(job_ref, jobtread_query, org_id)
    check_job_type(job)

    comments = _paged(jobtread_query, job["id"], "comments", {"message": {}, "createdAt": {}})
    logs = _paged(jobtread_query, job["id"], "dailyLogs", {"date": {}, "notes": {}, "createdAt": {}})
    logs = [l for l in logs if l.get("notes") and not any(m in l["notes"] for m in SKIP_LOG_MARKERS)]

    rendr = job.get("parameters")
    if not rendr:
        warnings.append("No Rendr parameters imported on this job; sizes come from the tool page or are estimated.")

    project_id = _companycam_project(job, warnings)
    camera = gather_companycam(project_id, warnings)
    if camera["missing_transcripts"]:
        warnings.append(f"{len(camera['missing_transcripts'])} CompanyCam video(s) had no transcript and were "
                        "not read.")

    return {
        "job": {"id": job["id"], "number": job.get("number"), "name": job.get("name"),
                "address": (job.get("location") or {}).get("address"),
                "location_id": (job.get("location") or {}).get("id"),
                "job_type": job.get("job_type"), "custom_fields": job.get("custom_fields") or {}},
        "comments": [{"at": c.get("createdAt"), "text": c.get("message")} for c in comments if c.get("message")],
        "daily_logs": [{"date": l.get("date"), "text": l.get("notes")} for l in logs],
        "rendr": rendr,
        "companycam": camera,
        "warnings": warnings,
    }
