"""Site Scope Tool - the quick pass (phase 4).

Reads one site visit - Jason's dictated notes from the project's "Site Visit
Notes" Page, the visit's CompanyCam photos and any video transcript that is
already ready - and has Claude turn it into:

  * the structured room file (docs/site-scope/room-file-format.md,
    occ-scope-rooms/1) that the drawings, scope Page and budget work from, and
  * 3-6 cost-driver questions, each with a suggested answer.

Everything is laid out for Claude in the order it happened (photos, notes and
videos by time), because Jason's habit is to shoot a room and then dictate it.
Photos go to Claude as CompanyCam links (Claude downloads them), so they never
pass through Render's memory.

Code checks what Claude sends back: each room is run through the drawing
code's own parser and closure check (scope_drawings), and a room whose walls
don't close gets a question if there's room for one. No prices are made here.

See docs/site-scope/site-scope-tool-build-plan.md (Flow, AI vs. code split).
"""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import companycam_api as cc
import site_scope

DEFAULT_MODEL = "claude-opus-5-5"     # Jason's call, Oct 20, 2026 (Render env SCOPE_MODEL overrides)
DEFAULT_EFFORT = "high"               # Opus 5.5 defaults to medium; this step needs the care
MAX_TOKENS = 64000
API_URL = "https://api.anthropic.com/v1/messages"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
SCHEMA = "occ-scope-rooms/1"

# $ per million tokens: input, output, cache read. Cache writes are 1.25x input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
}

MAX_PHOTOS = 40                # a visit is 10-20; past this is a whole-project dump
MAX_VIDEOS = 10
PHOTO_SIZE = "2000x2000"       # CompanyCam resizes on the fly; Opus reads up to ~1932 px
INLINE_PHOTO_SIZE = "1600x1600"  # only if Claude can't fetch the links: sent inline, kept smaller
INLINE_MAX_BYTES = 24 * 1024 * 1024
DRAWING_MARK = "Site Scope Tool"   # the tool's own drawings say this in their description
MAX_QUESTIONS = 6

_DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "site-scope")


class QuickPassError(Exception):
    """A problem to show Jason as-is."""


def model():
    return (os.environ.get("SCOPE_MODEL") or "").strip() or DEFAULT_MODEL


def effort():
    return (os.environ.get("SCOPE_EFFORT") or "").strip().lower() or DEFAULT_EFFORT


# ── Reading the Site Visit Notes Page ────────────────────────────────────────

_HEADING_RE = re.compile(r"^([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) - (\d{1,2}:\d{2} [AP]M)( \(corrected\))?$")
_RECORDED_RE = re.compile(r"^Recorded (\d{1,2}:\d{2}:\d{2} [AP]M) to (\d{1,2}:\d{2}:\d{2} [AP]M)")
_TYPED_RE = re.compile(r"^Typed note, saved (\d{1,2}:\d{2}:\d{2} [AP]M)")


def _node_text(node):
    if node.get("type") == "text":
        return node.get("text") or ""
    return "".join(_node_text(c) for c in node.get("content") or [])


def _at(day, clock):
    """date + "2:14:05 PM" / "2:14 PM" -> naive local datetime."""
    fmt = "%I:%M:%S %p" if clock.count(":") == 2 else "%I:%M %p"
    t = datetime.strptime(clock, fmt)
    return datetime(day.year, day.month, day.day, t.hour, t.minute, t.second)


def parse_notes_page(doc):
    """The notes on a Site Visit Notes Page (scope_notes.note_nodes format).

    Returns [{"heading", "started", "ended", "text", "corrected"}], times as
    naive Greenville-local datetimes. Paragraphs under a heading that isn't a
    note heading (someone typed their own in CompanyCam) stay with the note
    above them; text before the first note heading is the Page intro."""
    notes, current = [], None
    for node in (doc or {}).get("content") or []:
        text = " ".join(_node_text(node).split())
        if node.get("type") == "heading":
            m = _HEADING_RE.match(text)
            if m:
                day = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y")
                start = _at(day, m.group(4))
                current = {"heading": text, "started": start, "ended": start, "text": [],
                           "corrected": bool(m.group(5)), "_day": day, "_meta_seen": False}
                notes.append(current)
                continue
        if current is None or not text:
            continue
        if not current["_meta_seen"] and not current["text"]:
            rec, typed = _RECORDED_RE.match(text), _TYPED_RE.match(text)
            if rec or typed:
                current["_meta_seen"] = True
                if rec:
                    current["started"] = _at(current["_day"], rec.group(1))
                    current["ended"] = _at(current["_day"], rec.group(2))
                continue
        current["text"].append(text)
    out = []
    for n in notes:
        body = "\n\n".join(n["text"]).strip()
        if body:
            out.append({"heading": n["heading"], "started": n["started"], "ended": n["ended"],
                        "text": body, "corrected": n["corrected"]})
    return out


# ── Gathering the visit ──────────────────────────────────────────────────────

photo_description = site_scope._photo_description


def is_tool_drawing(photo):
    return DRAWING_MARK.lower() in photo_description(photo).lower()


def sized_photo_url(photo):
    """The original image link, asking CompanyCam for PHOTO_SIZE (it only
    shrinks, never enlarges)."""
    uri = cc.photo_uri(photo, "original") or cc.photo_uri(photo, "web")
    if not uri:
        return ""
    parts = urllib.parse.urlsplit(uri)
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if "d" in query:
        query["d"] = [PHOTO_SIZE]
        uri = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, doseq=True)))
    return uri


def _local(ts):
    return site_scope.to_local(ts) if ts is not None else None


def _clock(dt, seconds=False):
    return dt.strftime("%I:%M:%S %p" if seconds else "%I:%M %p").lstrip("0")


def gather_visit(project_id, visit_date=None):
    """Notes, photos and ready video transcripts for one visit day.

    visit_date "YYYY-MM-DD" or None for the day of the newest note, photo or
    video. Returns a dict; raises QuickPassError when there's nothing to read."""
    warnings = []

    notes = []
    notes_page_id = None
    try:
        for pg in cc.list_project_pages(project_id):
            if (pg.get("title") or "").strip().lower() == site_scope.SITE_VISIT_NOTES_TITLE.lower():
                notes_page_id = str(pg.get("id"))
                notes = parse_notes_page(cc.get_page(notes_page_id).get("tiptap_content"))
                break
    except cc.CompanyCamError as e:
        warnings.append(f"Could not read the Site Visit Notes Page ({e}).")

    photos = []
    for p in cc.list_project_photos(project_id):
        ts = cc.parse_timestamp(p.get("captured_at"))
        if ts is None or is_tool_drawing(p):
            continue
        photos.append({"raw": p, "at": _local(ts)})

    videos = []
    try:
        for v in cc.list_project_videos(project_id):
            ts = cc.parse_timestamp(v.get("captured_at"))
            if ts is None:
                continue
            duration = site_scope._video_duration(v)
            if duration is not None and duration < site_scope.MIN_VIDEO_SECONDS:
                continue
            videos.append({"raw": v, "at": _local(ts), "duration": duration})
    except cc.CompanyCamError as e:
        warnings.append(f"Could not list videos ({e}).")

    if not visit_date:
        stamps = [n["started"] for n in notes] + [p["at"] for p in photos] + [v["at"] for v in videos]
        if not stamps:
            raise QuickPassError("There are no notes, photos or videos on this job yet. Record a note "
                                 "or take photos with the CompanyCam camera, then tap Start.")
        visit_date = max(stamps).strftime("%Y-%m-%d")

    def on_day(dt):
        return dt.strftime("%Y-%m-%d") == visit_date

    notes = [n for n in notes if on_day(n["started"])]
    photos = sorted((p for p in photos if on_day(p["at"])), key=lambda p: p["at"])
    videos = sorted((v for v in videos if on_day(v["at"])), key=lambda v: v["at"])

    if len(photos) > MAX_PHOTOS:
        warnings.append(f"{len(photos)} photos on this day - only the first {MAX_PHOTOS} were read.")
        photos = photos[:MAX_PHOTOS]

    transcripts = []
    for v in videos[:MAX_VIDEOS]:
        try:
            text = cc.get_video_transcript(v["raw"].get("id"))
        except cc.CompanyCamError as e:
            warnings.append(f"Could not read the transcript for the {_clock(v['at'])} video ({e}).")
            continue
        if text and text is not cc.TRANSCRIPT_MISSING:
            transcripts.append({**v, "text": text})
        else:
            warnings.append(f"The {_clock(v['at'])} video has no transcript yet, so it was skipped.")

    if not notes and not photos and not transcripts:
        raise QuickPassError(f"Nothing from {site_scope._pretty_date(visit_date)} to read yet - no notes, "
                             "photos or video transcripts.")
    return {"visit_date": visit_date, "notes": notes, "photos": photos, "transcripts": transcripts,
            "notes_page_id": notes_page_id, "warnings": warnings}


# ── The prompt ───────────────────────────────────────────────────────────────

def _read_doc(name):
    try:
        with open(os.path.join(_DOCS_DIR, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


INSTRUCTIONS = """You are the quick pass of Owners Choice Construction's Site Scope Tool. OCC is a residential repair and remodel contractor in Greenville, SC. Jason Evans (the owner) has just walked a job. He took photos with the CompanyCam camera and dictated notes into his phone. You turn that visit into one structured room file and a short round of follow-up questions he answers on his phone while he is still on site. Nothing you write goes to the client; later steps build the drawings, the scope write-up and the budget from your file and his answers.

WHAT YOU GET
- Everything from the visit in the order it happened, Greenville local time. Photos are labeled P1, P2...; dictated notes N1, N2...; video transcripts V1, V2....
- Jason's habit: photograph a room or area, then dictate it. Photos taken just before (or during) a note usually show what that note describes. Use the times to tie photos to notes and to walls.
- Notes are speech-to-text. Trade words are sometimes misheard ("Wayne's coat" is wainscot, "jam" is jamb, "p trap" is P-trap, "joist" may be plural). Read those as the trade word. NUMBERS ARE DIFFERENT: never silently change a dictated number. If a number looks misheard or impossible next to the photos (a 20 ft soffit run dictated as 12, a vanity wider than its wall), keep the dictated number, add a flag, and ask about it.

HOW TO READ IT
- Dictation sets the numbers; photos set the layout (which wall an opening is on, door swing, construction type, what is next to what) and fill in heights and sizes that were not said, marked "est".
- Anything with nothing to go on gets a sensible default marked "assumed".
- Conflicts between sources get flagged, never guessed.
- Only put a room or area in "rooms" when you can lay out at least 3 walls/edges clockwise with a length for each (dictated, estimated from photos, or assumed and flagged). Exterior repairs, single fixes and anything not dictated wall by wall go in "scope_items" only, with no room. A visit can have zero rooms.
- Walls are walked clockwise from Wall 1 (the entry/door wall, or the house wall for a deck). If Jason numbered walls, keep his numbers.

THE ROOM FILE
Follow the room file format and the drawing rules below exactly (sizes in inches, every measurement as {"in": n, "src": "dictated"|"est"|"assumed"}). Leave "project" out - code fills it in. Add these top-level fields, which the later steps read:
- "client_goals": short plain sentences on what the client wants, if said. [] if not.
- "scope_items": every piece of work Jason described or the photos clearly call for, one per line of work:
  {"id": "S1", "trade": "Tile", "area": "Hall Bath", "work": "Retile the shower walls with cement board, thinset and Schluter edge", "quantity": {"value": 65, "unit": "sf", "src": "dictated"} or null, "sub": "name of the sub Jason addressed, or null", "licensed_trade": true|false, "photos": ["P4", "P5"], "notes": ["N3"], "detail": "anything else that matters for pricing, or empty"}
  Units: sf, lf, ea, sq (roofing squares), hr, ls (lump sum). licensed_trade is true for electrical, plumbing, HVAC, gas and structural work. Work verbs first ("Replace", "Repoint", "Install"). Mark something "not in scope" in detail when Jason says so.
- "photos": one entry per photo: {"ref": "P1", "id": "<CompanyCam photo id>", "area": "Hall Bath", "wall": 2 or null, "description": "one or two plain sentences: what is in the shot, what is changing, any measurement tied to it"}
- "conflicts": [{"what": "...", "source_a": "...", "source_b": "...", "action": "asked in Q2" or "flagged"}]
Room and file "flags" are plain sentences listing what must be checked before pricing.

THE QUESTIONS
Write 3 to 6 questions - fewer only if the visit truly leaves nothing that moves the price. Each one is a cost driver: a number that looks misheard, a conflict, a missing dimension or quantity that changes the material or labor, repair vs. replace, who supplies a fixture, a grade or material choice, or access/condition that changes the work. Never ask about something already dictated clearly. Most important first.
Each question: {"id": "Q1", "question": "...", "why": "one short sentence on what it changes in the price", "suggested_answer": "your best answer, written so Jason can accept it as-is", "choices": ["2 to 4 short tap answers, the suggested one first"] or [], "refs": ["S3", "Hall Bath Wall 3"]}
Jason reads these on a phone on site: plain words, one decision per question, under 30 words, no internal labels on their own (say "the 2:14 PM photo of the front door", not "P7"). Where you used an assumed or estimated number in the file, the suggested answer is that number, so accepting it keeps the file as drawn.

WRITING RULES
No markdown, no asterisks or emoji. Use a spaced hyphen " - " rather than a dash. Feet and inches as 13'-2" in sentences. No prices anywhere - pricing is a later step.

OUTPUT
Reply with one JSON object and nothing else:
{"room_file": {"schema": "occ-scope-rooms/1", "rooms": [...], "flags": [...], "client_goals": [...], "scope_items": [...], "photos": [...], "conflicts": [...]}, "questions": [...]}
"""


def system_blocks():
    """Stable instructions + the rules docs, cached (they're identical on every run)."""
    text = INSTRUCTIONS
    for title, name in (("ROOM FILE FORMAT", "room-file-format.md"),
                        ("DRAWING RULES", "floor-plan-drawing-rules.md")):
        doc = _read_doc(name)
        if doc:
            text += f"\n\n===== {title} ({name}) =====\n\n{doc}"
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def build_content(visit, job):
    """The user message: the visit in time order, then the ask."""
    events = []
    for i, n in enumerate(visit["notes"], 1):
        n["ref"] = f"N{i}"
        events.append((n["started"], 1, "note", n))
    for i, p in enumerate(visit["photos"], 1):
        p["ref"] = f"P{i}"
        events.append((p["at"], 0, "photo", p))
    for i, v in enumerate(visit["transcripts"], 1):
        v["ref"] = f"V{i}"
        events.append((v["at"], 2, "video", v))
    events.sort(key=lambda e: (e[0], e[1]))

    pretty = site_scope._pretty_date(visit["visit_date"])
    content = [{"type": "text", "text": (
        f"Job {job.get('number')} - {job.get('name') or ''}, {job.get('address') or 'no address on file'}. "
        f"Site visit on {pretty}: {len(visit['notes'])} dictated note(s), {len(visit['photos'])} photo(s), "
        f"{len(visit['transcripts'])} video transcript(s). Everything follows in the order it happened.")}]
    for at, _, kind, item in events:
        if kind == "photo":
            p = item["raw"]
            label = f"{item['ref']} - photo taken {_clock(at, True)} (CompanyCam id {p.get('id')})"
            desc = photo_description(p)
            if desc:
                label += f". Description already on it: {desc}"
            url = sized_photo_url(p)
            content.append({"type": "text", "text": label})
            if url:
                content.append({"type": "image", "source": {"type": "url", "url": url}})
        elif kind == "note":
            span = f"{_clock(item['started'], True)} to {_clock(item['ended'], True)}"
            tag = " (Jason's correction of an earlier note)" if item["corrected"] else ""
            content.append({"type": "text", "text": f"{item['ref']} - dictated note, {span}{tag}:\n{item['text']}"})
        else:
            dur = f", {int(item['duration'] // 60)}:{int(item['duration'] % 60):02d} long" if item["duration"] else ""
            content.append({"type": "text",
                            "text": f"{item['ref']} - walkthrough video transcript, recorded {_clock(at)}{dur}:\n"
                                    f"{item['text']}"})
    content.append({"type": "text", "text": "Write the room file and the questions now, as one JSON object."})
    return content


# ── Calling Claude ───────────────────────────────────────────────────────────

class ClaudeError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def _post_stream(body, betas):
    """POST /v1/messages with stream=true. Returns (text, stop_reason, usage, model).
    Streaming keeps the connection busy for the minute or two the pass takes."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ClaudeError("ANTHROPIC_API_KEY is not set on this server.")
    headers = {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01",
               "accept": "text/event-stream"}
    if betas:
        headers["anthropic-beta"] = ",".join(betas)
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=180)   # per read: the API went quiet this long
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise ClaudeError(f"Claude API answered HTTP {e.code}: {detail}",
                          retryable=e.code in (408, 409, 429) or e.code >= 500)
    except (urllib.error.URLError, OSError) as e:
        raise ClaudeError(f"Could not reach the Claude API: {e}", retryable=True)

    parts, stop_reason, usage, served_by = [], None, {}, body.get("model")
    with resp:
        event = None
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except ValueError:
                continue
            kind = data.get("type") or event
            if kind == "message_start":
                msg = data.get("message") or {}
                served_by = msg.get("model") or served_by
                usage.update(msg.get("usage") or {})
            elif kind == "content_block_start" and (data.get("content_block") or {}).get("type") == "fallback":
                # A safety decline mid-answer: the fallback model starts over, so drop the partial text.
                parts = []
            elif kind == "content_block_delta":
                delta = data.get("delta") or {}
                if delta.get("type") == "text_delta":
                    parts.append(delta.get("text") or "")
            elif kind == "message_delta":
                stop_reason = (data.get("delta") or {}).get("stop_reason") or stop_reason
                for k, v in (data.get("usage") or {}).items():
                    if isinstance(v, (int, float)):
                        usage[k] = v
            elif kind == "error":
                err = data.get("error") or {}
                raise ClaudeError(f"Claude API error mid-answer: {err.get('type')}: {err.get('message')}",
                                  retryable=err.get("type") in ("overloaded_error", "api_error"))
    return "".join(parts), stop_reason, usage, served_by


def run_cost(usage, model_name):
    """Dollars for one call from its usage block."""
    price_in, price_out, price_cache = PRICES.get(model_name, PRICES[DEFAULT_MODEL])
    fresh = usage.get("input_tokens") or 0
    write = usage.get("cache_creation_input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    out = usage.get("output_tokens") or 0
    return round((fresh * price_in + write * price_in * 1.25 + read * price_cache + out * price_out) / 1e6, 4)


def inline_images(content):
    """Fallback for when Claude's servers can't download the CompanyCam links:
    fetch each photo here (smaller size) and send it inline. Returns new content."""
    out, total = [], 0
    for block in content:
        src = block.get("source") or {}
        if block.get("type") != "image" or src.get("type") != "url":
            out.append(block)
            continue
        url = src["url"].replace(f"d={PHOTO_SIZE}", f"d={INLINE_PHOTO_SIZE}")
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            data = r.read(INLINE_MAX_BYTES + 1)
            media = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
        total += len(data)
        if total > INLINE_MAX_BYTES:
            raise ClaudeError("The photos are too large to send inline.")
        out.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                "data": base64.b64encode(data).decode("ascii")}})
    return out


def call_claude(system, content, attempts=4):
    """One quick-pass call, with retries for a busy API. Returns
    (text, stop_reason, usage, model). Includes server-side refusal fallback
    ("default" routing); if the API rejects that option, it's dropped."""
    body = {"model": model(), "max_tokens": MAX_TOKENS, "stream": True,
            "system": system, "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": effort()}, "fallbacks": "default"}
    betas = [FALLBACK_BETA]
    inlined = False
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return _post_stream(body, betas)
        except ClaudeError as e:
            last = e
            msg = str(e).lower()
            if "fallback" in msg and betas:
                print(f"Scope quick pass: API refused the fallback option ({e}); retrying without it")
                body.pop("fallbacks", None)
                betas = []
                continue
            if not e.retryable and "image" in msg and not inlined:
                print(f"Scope quick pass: Claude couldn't use the photo links ({e}); sending the photos inline")
                body["messages"] = [{"role": "user", "content": inline_images(content)}]
                inlined = True
                continue
            if not e.retryable or attempt == attempts:
                raise
            wait = 10 * attempt
            print(f"Scope quick pass: attempt {attempt} failed ({e}); retrying in {wait}s")
            time.sleep(wait)
    raise last


# ── Checking the answer ──────────────────────────────────────────────────────

def parse_answer(text):
    """The JSON object in Claude's reply."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict) or not isinstance(obj.get("room_file"), dict):
        raise ValueError("the reply has no room_file object")
    if not isinstance(obj.get("questions"), list):
        raise ValueError("the reply has no questions list")
    return obj


def room_problems(room_file):
    """Rooms the drawing code can't read. [] when all are fine."""
    import scope_drawings   # loads Pillow; only needed once a run gets this far
    problems = []
    rooms = room_file.get("rooms")
    if rooms is not None and not isinstance(rooms, list):
        return ["rooms must be a list"]
    for room in rooms or []:
        try:
            scope_drawings.parse_room(room)
        except (scope_drawings.DrawingError, AttributeError, TypeError, ValueError) as e:
            problems.append(str(e))
    return problems


def _clean_questions(raw):
    out = []
    for q in raw:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or "").strip()
        if not text:
            continue
        choices = [str(c).strip() for c in (q.get("choices") or []) if str(c).strip()][:4]
        out.append({
            "id": f"Q{len(out) + 1}",
            "question": text,
            "why": str(q.get("why") or "").strip(),
            "suggested_answer": str(q.get("suggested_answer") or (choices[0] if choices else "")).strip(),
            "choices": choices,
            "refs": [str(r) for r in (q.get("refs") or [])][:6],
        })
        if len(out) == MAX_QUESTIONS:
            break
    return out


def finish_room_file(obj, job, project_id, visit):
    """Fill in what code owns (project block, closure checks) and return
    (room_file, questions)."""
    import scope_drawings
    rf = obj["room_file"]
    rf["schema"] = SCHEMA
    rf["project"] = {
        "name": job.get("name"), "address": job.get("address"), "visit_date": visit["visit_date"],
        "companycam_project_id": str(project_id), "jobtread_job_id": job.get("id"),
        "job_number": job.get("number"),
    }
    for key in ("rooms", "flags", "client_goals", "scope_items", "photos", "conflicts"):
        if not isinstance(rf.get(key), list):
            rf[key] = []
    questions = _clean_questions(obj["questions"])

    for room in rf["rooms"]:
        parsed = scope_drawings.parse_room(room)
        name = parsed["name"]
        closure = scope_drawings.check_closure(parsed)
        found = [] if closure["closes"] else [closure["message"]]
        found += scope_drawings.wall_flags(parsed)
        room.setdefault("flags", [])
        for f in found:
            if f not in room["flags"]:
                room["flags"].append(f)
        if not closure["closes"]:
            asked = any(name.lower() in (q["question"] + " ".join(q["refs"])).lower()
                        and ("close" in q["question"].lower() or "length" in q["question"].lower())
                        for q in questions)
            if not asked and len(questions) < MAX_QUESTIONS:
                questions.append({
                    "id": f"Q{len(questions) + 1}",
                    "question": f"{name}: the wall lengths don't close (off by {scope_drawings.ft_in(closure['gap_in'])}). "
                                "Which wall length is off, and what is it?",
                    "why": "The floor plan and every wall quantity come from these lengths.",
                    "suggested_answer": "", "choices": [], "refs": [name], "made_by": "code",
                })
    return rf, questions


# ── The whole pass ───────────────────────────────────────────────────────────

def run_quick_pass(job, project_id, visit_date=None, log=print):
    """Gather, ask Claude, check. Returns a dict with room_file, questions,
    inputs, usage, warnings. Raises QuickPassError / ClaudeError."""
    t0 = time.time()
    visit = gather_visit(project_id, visit_date)
    inputs = {"visit_date": visit["visit_date"], "notes": len(visit["notes"]), "photos": len(visit["photos"]),
              "video_transcripts": len(visit["transcripts"])}
    log(f"Scope quick pass job {job.get('number')}: {inputs}")

    system = system_blocks()
    content = build_content(visit, job)
    calls, total_cost, usage_sum, served = 0, 0.0, {}, None
    obj, last_problem = None, None
    for attempt in (1, 2):
        text, stop, usage, served = call_claude(system, content)
        calls += 1
        cost = run_cost(usage, served or model())
        total_cost += cost
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            usage_sum[k] = usage_sum.get(k, 0) + (usage.get(k) or 0)
        log(f"Scope quick pass job {job.get('number')} call {attempt}: model={served}, stop={stop}, "
            f"usage={usage}, cost=${cost:.4f}")
        if stop == "refusal":
            raise QuickPassError("Claude declined to read this visit. Try Start again; if it repeats, "
                                 "check the photos and notes.")
        if stop == "max_tokens":
            last_problem = "the answer was cut off (too long)"
        else:
            try:
                obj = parse_answer(text)
                problems = room_problems(obj["room_file"])
                if not problems:
                    break
                last_problem = "; ".join(problems)
                obj = None
            except ValueError as e:
                last_problem = str(e)
        if attempt == 1:
            log(f"Scope quick pass job {job.get('number')}: answer not usable ({last_problem}); asking again")
            content = content + [
                {"type": "text", "text": f"Your previous answer could not be used: {last_problem}. "
                                         "Answer again with one complete JSON object that follows the format."}]
    if obj is None:
        raise QuickPassError(f"The AI's answer couldn't be used ({last_problem}). Tap Start to try again.")

    room_file, questions = finish_room_file(obj, job, project_id, visit)
    usage_sum.update({"calls": calls, "cost_usd": round(total_cost, 4), "model": served or model(),
                      "effort": effort(), "seconds": round(time.time() - t0, 1)})
    log(f"Scope quick pass job {job.get('number')} done: {len(questions)} questions, "
        f"{len(room_file['rooms'])} rooms, {len(room_file['scope_items'])} scope items, "
        f"tokens in={usage_sum.get('input_tokens')} out={usage_sum.get('output_tokens')} "
        f"cache_read={usage_sum.get('cache_read_input_tokens')}, cost=${total_cost:.4f}, "
        f"{usage_sum['seconds']}s")
    return {"room_file": room_file, "questions": questions, "inputs": inputs, "usage": usage_sum,
            "warnings": visit["warnings"]}
