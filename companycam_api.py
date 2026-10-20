"""Small CompanyCam client for the Site Scope Tool.

Uses CompanyCam's current Public API (https://app.companycam.com/public_api/v1),
not the legacy api.companycam.com/v2 that sales-lead-tool's lib/companycam.ts
still calls. Found on the Oct 19, 2026 live test: the legacy API answers
Pages with 401 "Bad credentials" and never returns video transcripts, and
CompanyCam retires it on Sep 1, 2027. The new API has both. Spec:
https://developers.companycam.com/openapi/public_api_v1.yaml

Every response is a {data, errors, meta} envelope; lists page with a cursor
(?after=meta.next_cursor); IDs are strings and timestamps ISO 8601.

Phase 1 reads; phase 2 adds photo uploads (create_project_photo); phase 3
adds Page writes (create_page, update_page) for the Site Visit Notes Page;
phase 5 adds photo descriptions (set_photo_description). Uploads go
by URL here - CompanyCam downloads the image itself - so a drawing must sit
at a public URL first (scope_uploads serves it briefly).

Standard library only, so it adds nothing to the Render service's memory.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://app.companycam.com/public_api/v1"
PAGE_LIMIT = 100  # the API's maximum
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
    # Optional - which CompanyCam user shows as creator on uploaded photos.
    # The API only honors it for admin-owned tokens (403 otherwise).
    creator = os.environ.get("COMPANYCAM_CREATOR_EMAIL", "").strip()
    if creator:
        h["X-CompanyCam-User"] = creator
    return h


def _error_detail(raw):
    """The API's own error message, e.g. 'insufficient_scope: ...'."""
    try:
        errors = json.loads(raw).get("errors") or []
        msgs = [f"{e.get('code')}: {e.get('message')}" if e.get("code") else str(e.get("message") or e)
                for e in errors if isinstance(e, dict)]
        if msgs:
            return "; ".join(msgs)
    except (ValueError, AttributeError):
        pass
    return raw[:300]


def _request(method, path, params=None, body=None):
    """Call a CompanyCam endpoint and return the parsed JSON body (the whole envelope)."""
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
        detail = _error_detail(e.read().decode("utf-8", "replace"))
        if e.code == 401:
            detail += (" - CompanyCam did not accept COMPANYCAM_API_KEY. Make a new Personal Access Token in "
                       "CompanyCam and put it in Render.")
        raise CompanyCamError(f"CompanyCam {method} {path} returned HTTP {e.code}: {detail}", e.code)
    except urllib.error.URLError as e:
        raise CompanyCamError(f"CompanyCam {method} {path} failed: {e.reason}")


def _get(path, params=None):
    return _request("GET", path, params)


def _post(path, body):
    return _request("POST", path, body=body)


def _data(body):
    """Unwrap the {data, errors, meta} envelope."""
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _list_all(path, params=None, max_pages=None, stop=None):
    """Follow meta.next_cursor through a list endpoint.
    stop(batch) -> True ends paging early."""
    params = dict(params or {}, limit=PAGE_LIMIT)
    out = []
    page = 0
    while True:
        page += 1
        body = _get(path, params)
        batch = _data(body) or []
        out.extend(batch)
        meta = (body or {}).get("meta") or {}
        cursor = meta.get("next_cursor")
        if not cursor or meta.get("has_next") is False or not batch:
            break
        if (max_pages and page >= max_pages) or (stop and stop(batch)):
            break
        params["after"] = cursor
    return out


def get_project(project_id):
    return _data(_get(f"/projects/{project_id}"))


def list_project_photos(project_id, stop_before_ts=None):
    """All photos on a project, newest first.

    stop_before_ts: optional Unix timestamp. Since results come newest first,
    paging stops once a page reaches photos older than this.
    """
    def older_than_cutoff(batch):
        if stop_before_ts is None:
            return False
        oldest = parse_timestamp(batch[-1].get("captured_at"))
        return oldest is not None and oldest < stop_before_ts

    return _list_all(f"/projects/{project_id}/photos", max_pages=MAX_PHOTO_PAGES, stop=older_than_cutoff)


def list_project_videos(project_id):
    return _list_all(f"/projects/{project_id}/videos", max_pages=5)


TRANSCRIPT_MISSING = object()


def get_video_transcript(video_id):
    """The transcript text, "" if CompanyCam has none yet, or
    TRANSCRIPT_MISSING if the response carried no transcript field at all
    (so the caller can say "unknown" rather than guess "not ready")."""
    video = _data(_get(f"/videos/{video_id}", {"include": "transcript"})) or {}
    if "transcript" not in video:
        return TRANSCRIPT_MISSING
    t = video.get("transcript")
    if isinstance(t, dict):
        return (t.get("text") or "").strip()
    if isinstance(t, str):
        return t.strip()
    return ""


def list_project_pages(project_id):
    return _list_all(f"/projects/{project_id}/pages", max_pages=5)


def get_page(page_id):
    """One Page with its TipTap document (tiptap_content: {type: "doc", content: [...]})."""
    return _data(_get(f"/pages/{page_id}", {"include": "tiptap_content"})) or {}


def create_page(project_id, title, doc):
    """New Page on a project. doc is a TipTap document ({type: "doc", content: [...]}).
    Body is unwrapped (title/tiptap_content at the top level), per the spec."""
    return _data(_post(f"/projects/{project_id}/pages", {"title": title, "tiptap_content": doc})) or {}


def update_page(page_id, doc):
    """Replace a Page's whole TipTap document. There is no append call, so to
    add to a Page: get_page, add nodes to its content, update_page."""
    return _data(_request("PATCH", f"/pages/{page_id}", body={"tiptap_content": doc})) or {}


def search_projects(query):
    """Projects matching a name or address fragment (first page only)."""
    return _data(_get("/projects/search", {"query": query, "limit": 25})) or []


def create_project_photo(project_id, uri, captured_at=None, description=None, tags=None):
    """Add a photo to a project from a public URL. CompanyCam downloads the
    image itself, after this returns, so the URL has to keep working for a
    while. captured_at: Unix seconds (default now). Returns the new photo.

    captured_at goes as an integer: the spec lists it as a string, but the
    live API rejects an ISO string with 422 "invalid value for Integer()"
    (Render test, Oct 20, 2026)."""
    photo = {"uri": uri, "captured_at": int(captured_at if captured_at is not None else time.time())}
    if description:
        photo["description"] = description
    if tags:
        photo["tags"] = list(tags)
    return _data(_post(f"/projects/{project_id}/photos", {"photo": photo})) or {}


def get_photo(photo_id):
    return _data(_get(f"/photos/{photo_id}")) or {}


def set_photo_description(photo_id, content):
    """Set a photo's description. A photo has ONE description and this call
    replaces it, so to add to what's there: read it (photo_description), join,
    then send the whole text. Returns the updated photo."""
    return _data(_post(f"/photos/{photo_id}/descriptions", {"description": {"content": content}})) or {}


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
    """Unix seconds from an ISO 8601 string (the API's format) or a number.
    Returns int or None."""
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
