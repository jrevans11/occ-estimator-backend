"""Tests for Site Scope Tool phase 4: the quick pass (scope_quickpass.py), runs
(scope_runs.py), run storage (scope_store.py) and the POST /scope/runs routes.

Run from the repo root:  python3 -m unittest discover tests

CompanyCam is faked in the Public API's {data, errors, meta} envelope, with the
Site Visit Notes Page copied from the real one on job 00489 (Oct 19, 2026) and
photos shaped like real Test (1) photos (description as an object, ?d= size
links). Claude is faked at the HTTP layer with its streaming (SSE) events.
Upstash is faked at its REST command layer. No keys or network needed.
"""

import copy
import io
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import companycam_api as cc  # noqa: E402
import scope_notes  # noqa: E402
import scope_quickpass as qp  # noqa: E402
import scope_runs  # noqa: E402
import scope_store  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

KEY = "test-scope-key"
ORG = "22P9ppHePJKP"
PROJECT_ID = "102406478"
JOB = {"id": "22PV6j9qe42x", "number": "00489", "name": "New selections test", "companycamId": PROJECT_ID,
       "location": {"address": "1217 Fieldstone Ct, Royse City, TX"}}
SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "docs", "site-scope", "samples", "test1-office-2026-10-19.json")


def env(data):
    return {"data": data, "errors": [], "meta": {"next_cursor": None, "has_next": False}}


def t(text, marks=None):
    n = {"type": "text", "text": text}
    if marks:
        n["marks"] = marks
    return n


ITALIC = [{"type": "italic"}]
# The real Site Visit Notes Page on job 00489 (Oct 19, 2026), trimmed text.
NOTES_DOC = {"type": "doc", "content": [
    {"type": "paragraph", "content": [t("Word-for-word notes dictated on site visits, added by the OCC scope page.", ITALIC)]},
    {"type": "heading", "attrs": {"level": 2}, "content": [t("Oct 19, 2026 - 10:12 PM")]},
    {"type": "paragraph", "content": [t("Recorded 10:12:58 PM to 10:13:42 PM (0:44 of audio). ", ITALIC),
                                      t("Play the recording", ITALIC + [{"type": "link", "attrs": {"href": "https://x"}}])]},
    {"type": "paragraph", "content": [t("The kitchen wall number one is 12 feet six inches.")]},
    {"type": "paragraph", "content": [t("Backsplash is about 18 square feet.")]},
    {"type": "heading", "attrs": {"level": 2}, "content": [t("Oct 19, 2026 - 10:13 PM")]},
    {"type": "paragraph", "content": [t("Recorded 10:13:55 PM to 10:14:26 PM (0:31 of audio). ", ITALIC)]},
    {"type": "paragraph", "content": [t("Exterior front elevation. Replace about 40 linear feet of fascia and 12 feet of soffit.")]},
    {"type": "heading", "attrs": {"level": 2}, "content": [t("Oct 18, 2026 - 9:00 AM")]},
    {"type": "paragraph", "content": [t("Typed note, saved 9:00:12 AM.", ITALIC)]},
    {"type": "paragraph", "content": [t("An older visit's note.")]},
    {"type": "heading", "attrs": {"level": 2}, "content": [t("Oct 19, 2026 - 10:12 PM (corrected)")]},
    {"type": "paragraph", "content": [t("Typed note, saved 10:30:00 PM.", ITALIC)]},
    {"type": "paragraph", "content": [t("Wall one is 12 feet 8 inches.")]},
]}


def photo(pid, iso, desc=None):
    base = f"https://static.companycam.com/lambda/projects/{PROJECT_ID}/photos/abc{pid}.jpeg"
    p = {"id": pid, "captured_at": iso, "processing_status": "processed",
         "uris": [{"type": "original", "uri": base + "?d=4032x4032"}, {"type": "web", "uri": base + "?d=400x400"},
                  {"type": "thumbnail", "uri": base + "?d=250x250"}]}
    if desc is not None:
        p["description"] = {"id": "1", "plain_text_content": desc, "html_content": desc}
    return p


# Oct 19 10:10 PM EDT = Oct 20 02:10 UTC
PHOTOS = [
    photo("9003", "2026-10-20T02:14:10Z"),
    photo("9001", "2026-10-20T02:10:00Z", "Kitchen wide shot"),
    photo("9002", "2026-10-20T02:11:30Z"),
    photo("9900", "2026-10-20T02:20:00Z", "Office - Floor Plan - drawn by the Site Scope Tool (phase 2 test)."),
    photo("8000", "2026-10-18T13:00:00Z"),
]
VIDEOS = [
    {"id": "v1", "captured_at": "2026-10-20T02:16:00Z", "duration": 95},
    {"id": "v2", "captured_at": "2026-10-20T02:17:00Z", "duration": 2},
    {"id": "v3", "captured_at": "2026-10-20T02:18:00Z", "duration": 40},
]


class FakeCC:
    def __init__(self, notes=True, photos=PHOTOS, videos=VIDEOS):
        self.notes, self.photos, self.videos = notes, photos, videos
        self.calls = []

    def __call__(self, method, path, params=None, body=None):
        self.calls.append((method, path))
        if path == f"/projects/{PROJECT_ID}/pages":
            return env([{"id": "3006585", "title": "Site Visit Notes"}] if self.notes else [])
        if path == "/pages/3006585":
            return env({"id": "3006585", "title": "Site Visit Notes", "tiptap_content": copy.deepcopy(NOTES_DOC)})
        if path == f"/projects/{PROJECT_ID}/photos":
            return env(self.photos)
        if path == f"/projects/{PROJECT_ID}/videos":
            return env(self.videos)
        if path == "/videos/v1":
            return env({"id": "v1", "transcript": {"text": "Walking the hall bath. Vanity is 48 inches."}})
        if path == "/videos/v3":
            return env({"id": "v3", "transcript": None})
        raise cc.CompanyCamError(f"unexpected {method} {path}", 404)


def fake_jobtread(job=JOB):
    def q(query):
        if "job" in query:
            return {"job": job if query["job"]["$"]["id"] == job["id"] else None}
        where = query["organization"]["jobs"]["$"]["where"]
        return {"organization": {"jobs": {"nodes": [job] if where[1] == job["number"] else []}}}
    return q


def sample_answer(close=True, questions=3):
    with open(SAMPLE) as f:
        rf = json.load(f)
    rf.pop("project", None)
    if not close:
        rf["rooms"][0]["walls"][0]["length"] = {"in": 150, "src": "dictated"}
    rf.update(client_goals=["New door into the office"], conflicts=[],
              scope_items=[{"id": "S1", "trade": "Carpentry", "area": "Office", "work": "Install a new door",
                            "quantity": {"value": 1, "unit": "ea", "src": "dictated"}, "sub": None,
                            "licensed_trade": False, "photos": ["P1"], "notes": ["N1"], "detail": ""}],
              photos=[{"ref": "P1", "id": "9001", "area": "Office", "wall": 1, "description": "Wide shot."}])
    qs = [{"id": f"Q{i}", "question": f"Question {i}?", "why": "Changes labor.", "suggested_answer": f"Answer {i}",
           "choices": [f"Answer {i}", "Other"], "refs": ["S1"]} for i in range(1, questions + 1)]
    return json.dumps({"room_file": rf, "questions": qs})


USAGE = {"input_tokens": 30000, "output_tokens": 6000, "cache_creation_input_tokens": 9000,
         "cache_read_input_tokens": 0}


def sse(text, stop="end_turn", usage=USAGE, model="claude-opus-5-5"):
    """Bytes lines of a Messages API stream that answers `text`."""
    events = [
        ("message_start", {"type": "message_start", "message": {"model": model, "usage": {
            k: usage[k] for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
            | {"output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "thinking_delta", "thinking": ""}}),
        ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}),
    ]
    for i in range(0, len(text), 500):
        events.append(("content_block_delta", {"type": "content_block_delta", "index": 1,
                                               "delta": {"type": "text_delta", "text": text[i:i + 500]}}))
    events += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop},
                                  "usage": {"output_tokens": usage["output_tokens"]}}),
               ("message_stop", {"type": "message_stop"})]
    lines = []
    for name, data in events:
        lines += [f"event: {name}\n".encode(), f"data: {json.dumps(data)}\n".encode(), b"\n"]
    return lines


class FakeStream:
    def __init__(self, lines):
        self.lines = lines

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeClaude:
    """Stands in for urllib.request.urlopen inside scope_quickpass."""

    def __init__(self, answers):
        self.answers = list(answers)   # each: list of SSE lines, or an Exception to raise
        self.bodies = []
        self.headers = []

    def __call__(self, req, timeout=None):
        if getattr(req, "full_url", "") != qp.API_URL:
            raise AssertionError(f"unexpected fetch {req.full_url}")
        self.bodies.append(json.loads(req.data))
        self.headers.append(dict(req.header_items()))
        nxt = self.answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return FakeStream(nxt)


def http_error(code, text):
    return urllib.error.HTTPError(qp.API_URL, code, "err", {}, io.BytesIO(text.encode()))


def run_inline(fn, *args):
    fn(*args)


class Base(unittest.TestCase):
    def setUp(self):
        e = mock.patch.dict(os.environ, {"COMPANYCAM_API_KEY": "cc", "SCOPE_API_KEY": KEY,
                                         "ANTHROPIC_API_KEY": "sk-test"})
        e.start()
        self.addCleanup(e.stop)
        for k in ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_URL", "KV_REST_API_TOKEN",
                  "SCOPE_MODEL", "SCOPE_EFFORT"):
            os.environ.pop(k, None)
        self.cc = FakeCC()
        p = mock.patch.object(cc, "_request", self.cc)
        p.start()
        self.addCleanup(p.stop)
        s = mock.patch.object(qp.time, "sleep")
        s.start()
        self.addCleanup(s.stop)
        scope_store._reset_memory()
        scope_runs._running.clear()

    def claude(self, *answers):
        fake = FakeClaude(answers)
        p = mock.patch.object(qp.urllib.request, "urlopen", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake


# ── Notes and visit gathering ────────────────────────────────────────────────

class NotesPageTests(unittest.TestCase):
    def test_parses_real_page_shape(self):
        notes = qp.parse_notes_page(NOTES_DOC)
        self.assertEqual(len(notes), 4)
        first = notes[0]
        self.assertEqual(first["started"], datetime(2026, 10, 19, 22, 12, 58))
        self.assertEqual(first["ended"], datetime(2026, 10, 19, 22, 13, 42))
        self.assertEqual(first["text"], "The kitchen wall number one is 12 feet six inches.\n\n"
                                        "Backsplash is about 18 square feet.")
        self.assertNotIn("Recorded", first["text"])
        self.assertEqual(notes[2]["started"], datetime(2026, 10, 18, 9, 0))   # typed: heading time
        self.assertTrue(notes[3]["corrected"])

    def test_intro_and_empty_notes_are_skipped(self):
        doc = {"type": "doc", "content": NOTES_DOC["content"][:1] + [
            {"type": "heading", "attrs": {"level": 2}, "content": [t("Oct 19, 2026 - 1:00 PM")]},
            {"type": "paragraph", "content": [t("Typed note, saved 1:00:05 PM.", ITALIC)]}]}
        self.assertEqual(qp.parse_notes_page(doc), [])

    def test_own_heading_text_stays_with_the_note_above(self):
        doc = {"type": "doc", "content": NOTES_DOC["content"][1:5] + [
            {"type": "heading", "attrs": {"level": 3}, "content": [t("Extra")]},
            {"type": "paragraph", "content": [t("Typed in CompanyCam later.")]}]}
        notes = qp.parse_notes_page(doc)
        self.assertEqual(len(notes), 1)
        self.assertIn("Typed in CompanyCam later.", notes[0]["text"])


class GatherTests(Base):
    def test_newest_day_drawings_short_videos_and_missing_transcripts(self):
        v = qp.gather_visit(PROJECT_ID)
        self.assertEqual(v["visit_date"], "2026-10-19")
        self.assertEqual([n["text"][:8] for n in v["notes"]], ["The kitc", "Exterior", "Wall one"])
        self.assertEqual([p["raw"]["id"] for p in v["photos"]], ["9001", "9002", "9003"])   # time order, no drawing
        self.assertEqual([x["raw"]["id"] for x in v["transcripts"]], ["v1"])                 # v2 too short, v3 not ready
        self.assertTrue(any("no transcript yet" in w for w in v["warnings"]))
        self.assertNotIn(("GET", "/videos/v2"), self.cc.calls)

    def test_date_asked(self):
        v = qp.gather_visit(PROJECT_ID, "2026-10-18")
        self.assertEqual([n["text"] for n in v["notes"]], ["An older visit's note."])
        self.assertEqual([p["raw"]["id"] for p in v["photos"]], ["8000"])

    def test_nothing_to_read(self):
        self.cc.notes, self.cc.photos, self.cc.videos = False, [], []
        with self.assertRaises(qp.QuickPassError):
            qp.gather_visit(PROJECT_ID)
        with self.assertRaises(qp.QuickPassError):
            qp.gather_visit(PROJECT_ID, "2026-01-01")

    def test_notes_only_visit(self):
        self.cc.photos, self.cc.videos = [], []
        v = qp.gather_visit(PROJECT_ID)
        self.assertEqual((len(v["notes"]), len(v["photos"])), (3, 0))

    def test_sized_photo_url_and_description(self):
        p = PHOTOS[1]
        self.assertTrue(qp.sized_photo_url(p).endswith("?d=2000x2000"))
        self.assertEqual(qp.photo_description(p), "Kitchen wide shot")
        self.assertTrue(qp.is_tool_drawing(PHOTOS[3]))

    def test_content_is_in_time_order(self):
        v = qp.gather_visit(PROJECT_ID)
        content = qp.build_content(v, {"number": "00489", "name": "Test", "address": "1 Main"})
        labels = [b["text"].split(" - ")[0] for b in content if b["type"] == "text" and " - " in b["text"][:5]]
        # N3 is a typed correction under the 10:12 PM heading, so it sits by the note it corrects.
        self.assertEqual(labels, ["P1", "P2", "N3", "N1", "N2", "P3", "V1"])
        images = [b for b in content if b["type"] == "image"]
        self.assertEqual(len(images), 3)
        self.assertTrue(all(b["source"]["type"] == "url" and "d=2000x2000" in b["source"]["url"] for b in images))
        self.assertIn("Description already on it: Kitchen wide shot", content[1]["text"])
        n1 = next(b["text"] for b in content if b["type"] == "text" and b["text"].startswith("N1"))
        self.assertIn("10:12:58 PM to 10:13:42 PM", n1)

    def test_system_prompt_carries_the_rules_docs(self):
        text = qp.system_blocks()[0]["text"]
        self.assertIn("ROOM FILE FORMAT", text)
        self.assertIn("DRAWING RULES", text)
        self.assertIn("never silently change a dictated number", text)
        self.assertEqual(qp.system_blocks()[0]["cache_control"], {"type": "ephemeral"})


# ── Claude call ──────────────────────────────────────────────────────────────

class ClaudeCallTests(Base):
    def test_stream_request_and_usage(self):
        fake = self.claude(sse('{"a": 1}'))
        text, stop, usage, model = qp.call_claude([{"type": "text", "text": "sys"}], [{"type": "text", "text": "hi"}])
        self.assertEqual((text, stop, model), ('{"a": 1}', "end_turn", "claude-opus-5-5"))
        self.assertEqual(usage["output_tokens"], 6000)
        self.assertEqual(usage["cache_creation_input_tokens"], 9000)
        body, headers = fake.bodies[0], fake.headers[0]
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["output_config"], {"effort": "high"})
        self.assertEqual(body["fallbacks"], "default")
        self.assertTrue(body["stream"])
        self.assertNotIn("thinking", body)          # Opus 5.5: always adaptive; disabled is a 400
        self.assertNotIn("temperature", body)
        self.assertEqual(headers.get("Anthropic-beta"), qp.FALLBACK_BETA)

    def test_model_and_effort_from_env(self):
        fake = self.claude(sse("{}"))
        with mock.patch.dict(os.environ, {"SCOPE_MODEL": "claude-sonnet-5-5", "SCOPE_EFFORT": "medium"}):
            qp.call_claude([], [])
        self.assertEqual(fake.bodies[0]["model"], "claude-sonnet-5-5")
        self.assertEqual(fake.bodies[0]["output_config"], {"effort": "medium"})

    def test_cost(self):
        # 30k fresh x $4 + 9k cache write x $5 + 6k out x $20 = 0.12 + 0.045 + 0.12
        self.assertEqual(qp.run_cost(USAGE, "claude-opus-5-5"), 0.285)
        self.assertEqual(qp.run_cost(USAGE, "claude-sonnet-5-5"), 0.1425)

    def test_busy_api_is_retried(self):
        fake = self.claude(http_error(529, "overloaded"), sse("{}"))
        self.assertEqual(qp.call_claude([], [])[0], "{}")
        self.assertEqual(len(fake.bodies), 2)

    def test_bad_request_is_not_retried(self):
        fake = self.claude(http_error(400, "max_tokens too big"))
        with self.assertRaises(qp.ClaudeError):
            qp.call_claude([], [])
        self.assertEqual(len(fake.bodies), 1)

    def test_fallback_option_refused_is_dropped(self):
        fake = self.claude(http_error(400, "fallbacks: unknown field"), sse("{}"))
        qp.call_claude([], [])
        self.assertNotIn("fallbacks", fake.bodies[1])
        self.assertNotIn("Anthropic-beta", fake.headers[1])

    def test_photo_links_refused_are_sent_inline(self):
        fake = self.claude(http_error(400, "Unable to download the image from the URL"), sse("{}"))
        real_fake = fake

        def urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            if "companycam" in url:
                self.assertIn("d=1600x1600", url)
                return _Resp(b"\xff\xd8jpegbytes")
            return real_fake(req, timeout)

        with mock.patch.object(qp.urllib.request, "urlopen", urlopen):
            content = [{"type": "text", "text": "P1"},
                       {"type": "image", "source": {"type": "url", "url": qp.sized_photo_url(PHOTOS[1])}}]
            qp.call_claude([], content)
        img = fake.bodies[1]["messages"][0]["content"][1]
        self.assertEqual(img["source"]["type"], "base64")
        self.assertEqual(img["source"]["media_type"], "image/jpeg")

    def test_error_event_mid_stream(self):
        lines = [b"event: error\n", b'data: {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}\n']
        self.claude(lines, sse("{}"))
        self.assertEqual(qp.call_claude([], [])[0], "{}")

    def test_fallback_mid_answer_drops_the_partial_text(self):
        partial = sse('{"broken": ')[:-6]   # cut before message_delta/stop
        fb = [b"event: content_block_start\n",
              b'data: {"type": "content_block_start", "index": 2, "content_block": {"type": "fallback"}}\n']
        self.claude(partial + fb + sse('{"ok": true}', model="claude-opus-5"))
        text, stop, usage, model = qp.call_claude([], [])
        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(model, "claude-opus-5")

    def test_missing_key(self):
        os.environ.pop("ANTHROPIC_API_KEY")
        with self.assertRaises(qp.ClaudeError):
            qp.call_claude([], [])


class _Resp(io.BytesIO):
    headers = {"Content-Type": "image/jpeg"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── The whole quick pass ─────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class QuickPassTests(Base):
    def test_happy_path(self):
        self.claude(sse("Here it is:\n" + sample_answer()))
        logs = []
        out = qp.run_quick_pass(JOB_OUT, PROJECT_ID, log=logs.append)
        rf = out["room_file"]
        self.assertEqual(rf["schema"], "occ-scope-rooms/1")
        self.assertEqual(rf["project"]["companycam_project_id"], PROJECT_ID)
        self.assertEqual(rf["project"]["visit_date"], "2026-10-19")
        self.assertEqual([q["id"] for q in out["questions"]], ["Q1", "Q2", "Q3"])
        self.assertEqual(out["inputs"], {"visit_date": "2026-10-19", "notes": 3, "photos": 3, "video_transcripts": 1})
        self.assertEqual(out["usage"]["cost_usd"], 0.285)
        self.assertEqual(out["usage"]["calls"], 1)
        self.assertTrue(any("cost=$0.2850" in line for line in logs))

    def test_walls_that_dont_close_get_a_question_and_flag(self):
        self.claude(sse(sample_answer(close=False)))
        out = qp.run_quick_pass(JOB_OUT, PROJECT_ID, log=lambda *_: None)
        self.assertEqual(len(out["questions"]), 4)
        self.assertIn("don't close", out["questions"][-1]["question"])
        self.assertEqual(out["questions"][-1]["made_by"], "code")
        self.assertTrue(any("don't close" in f for f in out["room_file"]["rooms"][0]["flags"]))

    def test_no_room_for_a_closure_question_still_flags(self):
        self.claude(sse(sample_answer(close=False, questions=6)))
        out = qp.run_quick_pass(JOB_OUT, PROJECT_ID, log=lambda *_: None)
        self.assertEqual(len(out["questions"]), 6)
        self.assertTrue(any("don't close" in f for f in out["room_file"]["rooms"][0]["flags"]))

    def test_too_many_questions_trimmed(self):
        self.claude(sse(sample_answer(questions=9)))
        out = qp.run_quick_pass(JOB_OUT, PROJECT_ID, log=lambda *_: None)
        self.assertEqual([q["id"] for q in out["questions"]], [f"Q{i}" for i in range(1, 7)])

    def test_bad_answer_is_asked_again_once(self):
        bad = json.loads(sample_answer())
        bad["room_file"]["rooms"][0]["walls"] = bad["room_file"]["rooms"][0]["walls"][:2]
        fake = self.claude(sse(json.dumps(bad)), sse(sample_answer()))
        out = qp.run_quick_pass(JOB_OUT, PROJECT_ID, log=lambda *_: None)
        self.assertEqual(out["usage"]["calls"], 2)
        self.assertEqual(out["usage"]["cost_usd"], 0.57)
        retry_note = fake.bodies[1]["messages"][0]["content"][-1]["text"]
        self.assertIn("needs at least 3 walls", retry_note)

    def test_unusable_twice_fails_cleanly(self):
        self.claude(sse("no json here"), sse("still nothing", stop="max_tokens"))
        with self.assertRaises(qp.QuickPassError) as ctx:
            qp.run_quick_pass(JOB_OUT, PROJECT_ID, log=lambda *_: None)
        self.assertIn("cut off", str(ctx.exception))

    def test_refusal(self):
        self.claude(sse("", stop="refusal"))
        with self.assertRaises(qp.QuickPassError):
            qp.run_quick_pass(JOB_OUT, PROJECT_ID, log=lambda *_: None)

    def test_zero_rooms_is_fine(self):
        a = json.loads(sample_answer())
        a["room_file"]["rooms"] = []
        self.claude(sse(json.dumps(a)))
        out = qp.run_quick_pass(JOB_OUT, PROJECT_ID, log=lambda *_: None)
        self.assertEqual(out["room_file"]["rooms"], [])


JOB_OUT = {"id": JOB["id"], "number": JOB["number"], "name": JOB["name"], "address": "1217 Fieldstone Ct"}


# ── Runs and storage ─────────────────────────────────────────────────────────

class FakeUpstash:
    def __init__(self):
        self.data, self.commands = {}, []

    def __call__(self, *args):
        self.commands.append(args)
        if args[0] == "SET":
            self.data[args[1]] = args[2]
            return "OK"
        if args[0] == "GET":
            return self.data.get(args[1])
        raise AssertionError(args)


class StoreTests(unittest.TestCase):
    def test_memory_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            scope_store._reset_memory()
            self.assertEqual(scope_store.kind(), "memory")
            scope_store.put("a", {"x": 1})
            self.assertEqual(scope_store.get("a"), {"x": 1})
            self.assertIsNone(scope_store.get("missing"))

    def test_upstash_commands(self):
        fake = FakeUpstash()
        with mock.patch.dict(os.environ, {"UPSTASH_REDIS_REST_URL": "https://x.upstash.io",
                                          "UPSTASH_REDIS_REST_TOKEN": "tok"}), \
                mock.patch.object(scope_store, "_command", fake):
            self.assertEqual(scope_store.kind(), "upstash")
            scope_store.put("run:1", {"status": "reading"})
            self.assertEqual(scope_store.get("run:1"), {"status": "reading"})
        self.assertEqual(fake.commands[0][:2], ("SET", "occ-scope:run:1"))
        self.assertEqual(fake.commands[0][3:], ("EX", scope_store.KEEP_SECONDS))

    def test_upstash_http_shape(self):
        seen = {}

        def urlopen(req, timeout=None):
            seen["url"], seen["auth"], seen["body"] = req.full_url, req.headers["Authorization"], json.loads(req.data)
            return _Resp(b'{"result": "OK"}')

        with mock.patch.dict(os.environ, {"KV_REST_API_URL": "https://y.upstash.io/", "KV_REST_API_TOKEN": "t2"}), \
                mock.patch.object(scope_store.urllib.request, "urlopen", urlopen):
            scope_store.put("k", [1])
        self.assertEqual(seen["url"], "https://y.upstash.io")
        self.assertEqual(seen["auth"], "Bearer t2")
        self.assertEqual(seen["body"][:3], ["SET", "occ-scope:k", "[1]"])

    def test_upstash_bad_token(self):
        with mock.patch.dict(os.environ, {"UPSTASH_REDIS_REST_URL": "https://x", "UPSTASH_REDIS_REST_TOKEN": "bad"}), \
                mock.patch.object(scope_store.urllib.request, "urlopen",
                                  side_effect=urllib.error.HTTPError("https://x", 401, "no", {}, io.BytesIO(b"unauth"))):
            with self.assertRaises(scope_store.StoreError) as ctx:
                scope_store.get("k")
        self.assertIn("UPSTASH_REDIS_REST_TOKEN", str(ctx.exception))


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class RunTests(Base):
    def post(self, path, payload, start_thread=run_inline):
        return scope_notes.handle_post(path, json.dumps(payload), {"X-Scope-Key": KEY}, fake_jobtread(), ORG,
                                       start_thread=start_thread)

    def test_start_questions_answers(self):
        self.claude(sse(sample_answer()))
        code, body = self.post("/scope/runs", {"job": "00489"})
        self.assertEqual(code, 202)
        run_id = body["run_id"]
        code, body = self.post("/scope/runs/status", {"run_id": run_id})
        self.assertEqual((code, body["status"]), (200, "questions"))
        self.assertEqual(len(body["questions"]), 3)
        self.assertEqual(body["usage"]["cost_usd"], 0.285)
        self.assertEqual(body["summary"]["rooms"], ["Office"])
        self.assertEqual(body["store"], "memory")
        self.assertNotIn("room_file", body)
        # The page reopens: newest run on the job.
        code, by_job = self.post("/scope/runs/status", {"job": "00489"})
        self.assertEqual(by_job["run_id"], run_id)
        code, full = self.post("/scope/runs/status", {"run_id": run_id, "include_file": True})
        self.assertEqual(full["room_file"]["schema"], "occ-scope-rooms/1")
        # Answer one, leave two blank (= suggested).
        code, body = self.post("/scope/runs/answers", {"run_id": run_id, "answers": {"Q2": "Wall 4 is 13 feet"}})
        self.assertEqual((code, body["status"]), (200, "answered"))
        self.assertEqual([a["answer"] for a in body["answers"]], ["Answer 1", "Wall 4 is 13 feet", "Answer 3"])
        self.assertEqual([a["used_suggestion"] for a in body["answers"]], [True, False, True])

    def test_start_again_returns_waiting_questions_unless_restart(self):
        self.claude(sse(sample_answer()), sse(sample_answer(questions=4)))
        _, first = self.post("/scope/runs", {"job": "00489"})
        code, again = self.post("/scope/runs", {"job": "00489"})
        self.assertEqual((code, again["run_id"]), (200, first["run_id"]))
        code, fresh = self.post("/scope/runs", {"job": "00489", "restart": True})
        self.assertEqual(code, 202)
        self.assertNotEqual(fresh["run_id"], first["run_id"])
        _, latest = self.post("/scope/runs/status", {"job": "00489"})
        self.assertEqual(len(latest["questions"]), 4)

    def test_a_run_in_progress_is_not_doubled(self):
        held = []
        code, first = self.post("/scope/runs", {"job": "00489"}, start_thread=lambda fn, *a: held.append((fn, a)))
        self.assertEqual(first["status"], "reading")
        code, again = self.post("/scope/runs", {"job": "00489", "restart": True})
        self.assertEqual((code, again["run_id"]), (200, first["run_id"]))
        self.assertEqual(len(held), 1)

    def test_failure_is_reported_and_start_tries_again(self):
        self.cc.notes, self.cc.photos, self.cc.videos = False, [], []
        _, body = self.post("/scope/runs", {"job": "00489"})
        _, status = self.post("/scope/runs/status", {"run_id": body["run_id"]})
        self.assertEqual(status["status"], "failed")
        self.assertIn("no notes, photos or videos", status["error"])
        self.assertEqual(scope_runs._running, set())
        self.cc.notes = True
        self.claude(sse(sample_answer()))
        code, again = self.post("/scope/runs", {"job": "00489"})
        self.assertEqual(code, 202)

    def test_run_cut_off_by_a_restart(self):
        held = []
        _, body = self.post("/scope/runs", {"job": "00489"}, start_thread=lambda fn, *a: held.append(1))
        scope_runs._running.clear()                  # the server restarted: nothing running here
        run = scope_store.get(f"run:{body['run_id']}")
        run["updated_at"] -= scope_runs.RESTART_GRACE_SECONDS + 5
        scope_store.put(f"run:{body['run_id']}", run)
        _, status = self.post("/scope/runs/status", {"run_id": body["run_id"]})
        self.assertEqual(status["status"], "failed")
        self.assertIn("restarted", status["error"])

    def test_no_questions_goes_straight_to_answered(self):
        self.claude(sse(sample_answer(questions=0)))
        _, body = self.post("/scope/runs", {"job": "00489"})
        _, status = self.post("/scope/runs/status", {"run_id": body["run_id"]})
        self.assertEqual(status["status"], "answered")

    def test_answers_on_wrong_state_and_unknown_run(self):
        held = []
        _, body = self.post("/scope/runs", {"job": "00489"}, start_thread=lambda fn, *a: held.append(1))
        code, err = self.post("/scope/runs/answers", {"run_id": body["run_id"], "answers": {}})
        self.assertEqual(code, 409)
        code, err = self.post("/scope/runs/answers", {"run_id": "nope", "answers": {}})
        self.assertEqual(code, 404)
        code, err = self.post("/scope/runs/status", {})
        self.assertEqual(code, 400)

    def test_job_with_no_run_yet(self):
        code, body = self.post("/scope/runs/status", {"job": "00489"})
        self.assertEqual((code, body["status"]), (200, "none"))

    def test_too_many_at_once(self):
        scope_runs._running.update({"a", "b"})
        code, body = self.post("/scope/runs", {"job": "00489"})
        self.assertEqual(code, 429)

    def test_key_required(self):
        code, body = scope_notes.handle_post("/scope/runs", "{}", {}, fake_jobtread(), ORG)
        self.assertEqual(code, 401)

    def test_runs_with_upstash(self):
        fake = FakeUpstash()
        self.claude(sse(sample_answer()))
        with mock.patch.dict(os.environ, {"UPSTASH_REDIS_REST_URL": "https://x", "UPSTASH_REDIS_REST_TOKEN": "t"}), \
                mock.patch.object(scope_store, "_command", fake):
            _, body = self.post("/scope/runs", {"job": "00489"})
            scope_store._reset_memory()
            _, status = self.post("/scope/runs/status", {"job": "00489"})
        self.assertEqual(status["status"], "questions")
        self.assertEqual(status["store"], "upstash")
        self.assertIn(f"occ-scope:job:{JOB['id']}:latest", fake.data)


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class HttpRouteTests(Base):
    """POST /scope/runs through the real app.py Handler - no app.py change needed."""

    @classmethod
    def setUpClass(cls):
        import app  # noqa: F401
        cls.app = app

    def setUp(self):
        super().setUp()
        p = mock.patch.object(self.app, "jobtread_query", fake_jobtread())
        p.start()
        self.addCleanup(p.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Scope-Key": KEY})
        try:
            with _real_urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_start_then_poll_then_answer(self):
        self.claude(sse(sample_answer()))
        code, body = self.call("/scope/runs", {"job": "00489"})
        self.assertIn(code, (200, 202))
        for _ in range(200):
            code, status = self.call("/scope/runs/status", {"run_id": body["run_id"]})
            if status["status"] != "reading":
                break
            time.sleep(0.02)
        self.assertEqual(status["status"], "questions")
        code, done = self.call("/scope/runs/answers", {"run_id": body["run_id"], "answers": {}})
        self.assertEqual((code, done["status"]), (200, "answered"))


_real_urlopen = urllib.request.urlopen


if __name__ == "__main__":
    unittest.main()
