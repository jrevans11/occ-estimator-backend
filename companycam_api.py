"""Small CompanyCam REST client for the Site Scope Tool.

Mirrors the calls sales-lead-tool makes in lib/companycam.ts (same base URL,
same Bearer auth, same optional X-CompanyCam-User header), using only the
standard library so it adds nothing to the Render service's memory footprint.

Read-only for now (phase 1). Later phases add page and photo writes here.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.companycam.com/v2"
PER_PAGE = 100
# A project with more photos than this is not a site-visit project; stop
# paging rather than walking thousands of photos on every preview.
MAX_PHOTO_PAGES = 10


class CompanyCamError(Exception):
    """A CompanyCam call failed. `status` is the HTTP status, or None."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def api_key():
    return os.environ.get("COMPANYCAM_API_KEY", "").strip()


def is_configured():
    return bool(api_key())


def _headers():
    h = {
        "Authorization": f"Bearer {api_key()}",
        "Accept": "application/json",
    }
    # Optional - which CompanyCam user shows as creator on anything we write.
    creator = os.environ.get("COMPANYCAM_CREATOR_EMAIL", "").strip()
    if creator:
        h["X-CompanyCam-User"] = creator
    return h


def _get(path, params=None):
    """GET a CompanyCam endpoint and return the parsed JSON body."""
    if not is_configured():
        raise CompanyCamError("COMPANYCAM_API_KEY is not set on this server")
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise CompanyCamError(f"CompanyCam GET {path} returned HTTP {e.code}: {detail}", e.code)
    except urllib.error.URLError as e:
        raise CompanyCamError(f"CompanyCam GET {path} failed: {e.reason}")


def _items(body):
    """CompanyCam list endpoints return a bare array; tolerate a {data: [...]} wrapper too."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        return body["data"]
    return []


def get_project(project_id):
    return _get(f"/projects/{project_id}")


def list_project_photos(project_id, stop_before_ts=None):
    """All photos on a project, newest first.

    stop_before_ts: optional Unix timestamp. Since results come newest first,
    paging stops once a page reaches photos older than this.
    """
    photos = []
    for page in range(1, MAX_PHOTO_PAGES + 1):
        batch = _items(_get(f"/projects/{project_id}/photos", {"page": page, "per_page": PER_PAGE}))
        photos.extend(batch)
        if len(batch) < PER_PAGE:
            break
        if stop_before_ts is not None:
            oldest = parse_timestamp(batch[-1].get("captured_at"))
            if oldest is not None and oldest < stop_before_ts:
                break
    return photos


def list_project_videos(project_id):
    return _items(_get(f"/projects/{project_id}/videos", {"per_page": PER_PAGE}))


def get_video_transcript(video_id):
    """Return the transcript text, or "" if CompanyCam has none yet."""
    body = _get(f"/videos/{video_id}", {"include": "transcript"})
    t = body.get("transcript") if isinstance(body, dict) else None
    if isinstance(t, dict):
        return (t.get("text") or t.get("content") or "").strip()
    if isinstance(t, str):
        return t.strip()
    return ""


def list_project_pages(project_id):
    return _items(_get(f"/projects/{project_id}/pages", {"per_page": PER_PAGE}))


def search_projects(query):
    return _items(_get("/projects", {"query": query, "per_page": 25}))


def parse_timestamp(value):
    """CompanyCam returns captured_at as Unix seconds; accept ISO strings too.
    Returns Unix seconds (int) or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def photo_uri(photo, kind):
    """URL of one size variant ("original", "web", "thumbnail")."""
    for u in photo.get("uris") or []:
        if u.get("type") == kind:
            return u.get("uri") or u.get("url") or ""
    return ""
