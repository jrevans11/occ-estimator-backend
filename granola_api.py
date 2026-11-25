"""Site Scope Tool - reading Jason's Granola recordings (phase 9).

Jason records site visits with Granola (AI note taker) while he takes photos
with the CompanyCam camera. This module reads those recordings through
Granola's public API and works out which job a recording belongs to. It has no
Site Scope state of its own - scope_quickpass uses it when Jason taps Start
on the scope page (the G1, G2... sources). Nothing starts on its own
(Jason, Nov 24, 2026: manual only).

Render env var: GRANOLA_API_KEY (a personal API key from Granola, Settings ->
API; starts with grn_). With no key the tool works as before, without Granola.

Granola public API facts (docs v1.7.0, read Nov 24, 2026):
  * Base https://public-api.granola.ai/v1, header Authorization: Bearer <key>.
  * GET /notes lists only notes that have a summary (created_after,
    created_before, cursor, page_size <= 30) -> {notes, hasMore, cursor}.
  * GET /notes/{id}?include=transcript -> the note: title, owner, created_at,
    calendar_event {event_title, ...}, transcript [{speaker, text, start_time,
    end_time}]. There is no location field, so the job comes from the title.
  * A long transcript answers 413 TRANSCRIPT_TOO_LARGE; then it is paged from
    GET /notes/{id}/transcript (cursor, page_size <= 100).
  * Rate limits: 25 requests per 5 seconds, 5 per second sustained.

Jason's visit titles carry the job number (occ-visit-prep puts it on the
calendar event): "100 Boxwood (00905)", "Jonathan Hufford and Jason Evans
(00899)". Meetings ("Repair Meeting", "Leadership meeting") have neither a job
number nor a street address and are left alone.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import companycam_api as cc
import site_scope

API_BASE = "https://public-api.granola.ai/v1"
TIMEOUT_SECONDS = 20
MAX_NOTE_PAGES = 5               # 30 notes a page - a day never has more than a handful
MAX_TRANSCRIPT_PAGES = 40        # 100 lines a page
MAX_TRANSCRIPT_CHARS = 80000     # ~20k tokens: a long walkthrough is ~35k characters
STAMP_EVERY_SECONDS = 60         # a [2:14 PM] time mark at most once a minute

_sleep = time.sleep


class GranolaError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def api_key():
    return (os.environ.get("GRANOLA_API_KEY") or "").strip()


def is_configured():
    return bool(api_key())


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _request(path, params=None):
    """GET one API path. Returns the parsed JSON. Retries a busy API twice."""
    key = api_key()
    if not key:
        raise GranolaError("GRANOLA_API_KEY is not set on this server.")
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    last = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            code = body.get("code") if isinstance(body, dict) else None
            message = (body.get("message") if isinstance(body, dict) else None) or raw[:200]
            last = GranolaError(f"Granola answered HTTP {e.code}: {message}", e.code, code)
            if e.code in (401, 403):
                last = GranolaError("Granola refused the API key (check GRANOLA_API_KEY in Render).", e.code, code)
            if e.code == 429 or e.code >= 500:
                _sleep(3 * (attempt + 1))
                continue
            raise last
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = GranolaError(f"Could not reach Granola: {e}")
            _sleep(3 * (attempt + 1))
    raise last


def get_note(note_id, transcript=False):
    """One note. With transcript=True the note's "transcript" list is filled,
    paging it in when it is too large to come with the note."""
    note_id = str(note_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{3,80}", note_id):
        raise GranolaError(f"'{note_id}' doesn't look like a Granola note id.")
    path = f"/notes/{urllib.parse.quote(note_id)}"
    if not transcript:
        return _request(path)
    try:
        return _request(path, {"include": "transcript"})
    except GranolaError as e:
        if e.status != 413 and e.code != "TRANSCRIPT_TOO_LARGE":
            raise
    note = _request(path)
    note["transcript"] = _paged_transcript(path)
    return note


def _paged_transcript(note_path):
    lines, cursor = [], None
    for _ in range(MAX_TRANSCRIPT_PAGES):
        page = _request(note_path + "/transcript", {"page_size": 100, "cursor": cursor})
        lines.extend(page.get("transcript") or [])
        cursor = page.get("cursor")
        if not page.get("hasMore") or not cursor:
            break
    return lines


def list_notes(created_after=None, created_before=None):
    """Summaries of notes (id, title, created_at...) in a time window, ISO strings."""
    out, cursor = [], None
    for _ in range(MAX_NOTE_PAGES):
        page = _request("/notes", {"created_after": created_after, "created_before": created_before,
                                   "page_size": 30, "cursor": cursor})
        out.extend(page.get("notes") or [])
        cursor = page.get("cursor")
        if not page.get("hasMore") or not cursor:
            break
    return out


# ── Reading a note ───────────────────────────────────────────────────────────

def note_ts(note):
    """Unix seconds the note was started (created_at)."""
    return cc.parse_timestamp((note or {}).get("created_at"))


def note_day(note):
    """The note's Greenville-local day, "YYYY-MM-DD", or None."""
    ts = note_ts(note)
    return site_scope.to_local(ts).strftime("%Y-%m-%d") if ts is not None else None


def event_title(note):
    ev = (note or {}).get("calendar_event") or {}
    return str(ev.get("event_title") or "").strip()


def _speaker(item, owner_name):
    sp = item.get("speaker")
    if isinstance(sp, str) and sp.strip():
        return sp.strip()
    sp = sp if isinstance(sp, dict) else {}
    for key in ("name", "diarization_label"):
        if str(sp.get(key) or "").strip():
            return str(sp[key]).strip()
    if sp.get("attribution") == "me":
        return owner_name or "Note owner"
    if sp.get("attribution") == "them":
        return "Other speaker"
    return "Microphone" if sp.get("source") == "microphone" else "Other audio"


def _item_ts(value, start_ts):
    """A transcript line's time: an ISO string, Unix seconds, or seconds from
    the start of the note. None when there's nothing usable."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
        v = float(value)
        if v > 1e9:
            return int(v)
        return int(start_ts + v) if start_ts is not None else None
    return cc.parse_timestamp(value)


def _clock(ts):
    return site_scope.to_local(ts).strftime("%I:%M %p").lstrip("0")


def transcript_text(note, max_chars=MAX_TRANSCRIPT_CHARS):
    """The transcript as plain lines, "Speaker A: ...", one line per turn,
    with a [2:14 PM] mark when the time is known (at most once a minute)."""
    owner = str(((note or {}).get("owner") or {}).get("name") or "").strip()
    start = note_ts(note)
    lines, last_label, last_stamp = [], None, None
    for item in (note or {}).get("transcript") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        label = _speaker(item, owner)
        ts = _item_ts(item.get("start_time"), start)
        stamp = _clock(ts) if ts is not None and (last_stamp is None or ts - last_stamp >= STAMP_EVERY_SECONDS) \
            else None
        if label == last_label and not stamp and lines:
            lines[-1] += " " + text
        else:
            lines.append(f"[{stamp}] {label}: {text}" if stamp else f"{label}: {text}")
            last_label = label
        if stamp:
            last_stamp = ts
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rsplit("\n", 1)[0] + "\n[The rest of the recording was cut off here - too long.]"
    return out


# ── Which job a note is about ────────────────────────────────────────────────

_PAREN_NUMBER = re.compile(r"\(\s*(\d{4,6})\s*\)")
_BARE_NUMBER = re.compile(r"(?<![\d\-.])(0\d{4})(?![\d\-])")   # OCC job numbers are 0xxxx; zip codes aren't
_STREET_WORDS_SKIP = {
    "st", "street", "dr", "drive", "rd", "road", "ln", "lane", "ct", "court", "way", "ave", "avenue", "blvd",
    "cir", "circle", "pl", "place", "ter", "terrace", "trl", "trail", "pkwy", "parkway", "hwy", "highway",
    "n", "s", "e", "w", "north", "south", "east", "west", "mt", "mount", "the", "unit", "apt", "suite", "ext",
}


def job_number_in(text):
    """The OCC job number in a title ("100 Boxwood (00905)" -> "00905"), or None.
    A number in parentheses wins; otherwise a bare 5-digit number starting with 0."""
    text = str(text or "")
    m = _PAREN_NUMBER.search(text)
    if m:
        return m.group(1).zfill(5)
    m = _BARE_NUMBER.search(text)
    return m.group(1) if m else None


def _words(text):
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _street(address):
    """("316", {"stewart"}) from "316 Stewart St, Greenville, SC 29601"."""
    first = str(address or "").split(",")[0]
    words = _words(first)
    if len(words) < 2 or not words[0].isdigit():
        return None, set()
    return words[0], {w for w in words[1:] if len(w) >= 3 and w not in _STREET_WORDS_SKIP and not w.isdigit()}


def note_matches_job(note, job):
    """Does this note's title (or calendar event title) name the job - by job
    number, or by house number plus a street name word?"""
    number = str((job or {}).get("number") or "").strip()
    titles = [str((note or {}).get("title") or ""), event_title(note)]
    if number and any(job_number_in(t) == number.zfill(5) for t in titles):
        return True
    house, street_words = _street((job or {}).get("address"))
    if not house or not street_words:
        return False
    for t in titles:
        words = _words(t)
        if words and words[0] == house and street_words & set(words[1:]):
            return True
    return False


def notes_for_job_on_day(job, day):
    """Notes started on the Greenville-local day "YYYY-MM-DD" whose title names
    the job. Oldest first. Raises GranolaError."""
    y, m, d = (int(x) for x in day.split("-"))
    # A day either side covers the UTC offset; the local day is checked below.
    after = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(time.mktime((y, m, d - 1, 12, 0, 0, 0, 0, -1))))
    before = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(time.mktime((y, m, d + 2, 12, 0, 0, 0, 0, -1))))
    found = [n for n in list_notes(after, before) if note_day(n) == day and note_matches_job(n, job)]
    return sorted(found, key=lambda n: note_ts(n) or 0)
