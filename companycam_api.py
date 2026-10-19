"""Small CompanyCam REST client for the Site Scope Tool.

Mirrors the calls sales-lead-tool makes in lib/companycam.ts (same base URL,
same Bearer auth, same optional X-CompanyCam-User header), using only the
standard library so it adds nothing to the Render service's memory footprint.

Phase 1 reads; phase 2 adds photo uploads (create_project_photo). CompanyCam's
public API only takes a photo by URL - it downloads the image itself - so a
drawing must sit at a public URL first (site_scope serves it briefly).
"""

import json
import os
import time
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


def _request(method, path, params=None, body=None):
    """Call a CompanyCam endpoint and return the parsed JSON body."""
    if not is_configured():
        raise CompanyCamError("COMPANYCAM_API_KEY is not set on this server")
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = _headers()
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise CompanyCamError(f"CompanyCam {method} {path} returned HTTP {e.code}: {detail}", e.code)
    except urllib.error.URLError as e:
        raise CompanyCamError(f"CompanyCam {method} {path} failed: {e.reason}")


def _get(path, params=None):
    return _request("GET", path, params)


def _post(path, body):
    return _request("POST", path, body=body)


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


def create_project_photo(project_id, uri, captured_at=None, description=None, tags=None):
    """Add a photo to a project from a public URL, like sales-lead-tool's
    pushPhoto(). CompanyCam downloads the image itself, after this returns,
    so the URL has to keep working for a while. Returns the new photo."""
    photo = {"uri": uri, "captured_at": int(captured_at if captured_at is not None else time.time())}
    if description:
        photo["description"] = description
    if tags:
        photo["tags"] = list(tags)
    return _post(f"/projects/{project_id}/photos", {"photo": photo})


def get_photo(photo_id):
    return _get(f"/photos/{photo_id}")


def photo_is_ready(photo):
    """True once CompanyCam has downloaded and processed the image, False
    while it is still working, None if it failed."""
    status = str((photo or {}).get("processing_status") or "").lower()
    if status in ("processing_error", "error", "failed"):
        return None
    if status in ("processed", "complete", "completed", "duplicate"):
        return True
    if status:
        return False
    # No status field: treat a real original image link as done.
    return bool(photo_uri(photo or {}, "original"))


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
