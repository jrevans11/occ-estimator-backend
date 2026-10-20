"""Tests for Site Scope Tool phase 3 notes (scope_notes.py, scope_transcribe.py,
the Page calls in companycam_api.py, and the routes in app.py).

Run from the repo root:  python3 -m unittest discover tests

CompanyCam Pages are faked in the Public API's {data, errors, meta} envelope
with a TipTap document shaped like the real "Office Remodel Scope" Page on
Test (1); Deepgram is faked with its documented response shape. No keys or
network needed.
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
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import companycam_api as cc  # noqa: E402
import scope_notes  # noqa: E402
import scope_quickpass  # noqa: E402
import scope_transcribe  # noqa: E402

KEY = "test-scope-key"
ORG = "22P9ppHePJKP"
PROJECT_ID = "117058039"
JOB = {"id": "22PTestJob01", "number": "00999", "name": "Scope Tool Test", "companycamId": PROJECT_ID,
       "location": {"address": "3122 Wade Hampton Blvd, Taylors, SC 29687"}}
AUDIO = "https://abc123.public.blob.vercel-storage.com/scope-notes/note-1-x8Yz.m4a"
# Oct 21, 2026 2:14:05 PM and 2:15:47 PM Greenville time (EDT, UTC-4)
STARTED = "2026-10-21T18:14:05.000Z"
SAVED = "2026-10-21T18:15:47.000Z"


def env(data, next_cursor=None):
    return {"data": data, "errors": [],
            "meta": {"next_cursor": next_cursor, "prev_cursor": None, "has_next": bool(next_cursor)}}


def ts(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def has_link(nodes):
    for n in nodes:
        if any(m.get("type") == "link" for m in n.get("marks") or []):
            return True
        if has_link(n.get("content") or []):
            return True
    return False


class FakePages:
    """Stands in for companycam_api._request: Pages on one project."""

    def __init__(self, pages=None, refuse_links=False):
        self.pages = pages if pages is not None else {
            "2999797": {"id": "2999797", "project_id": PROJECT_ID,
                        "title": "Office Remodel Scope - Draft from Oct 19, 2026 Site Visit",
                        "tiptap_content": {"type": "doc", "content": [
                            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Client goals"}]}]}},
        }
        self.refuse_links = refuse_links
        self.calls = []
        self.next_id = 3100000

    def __call__(self, method, path, params=None, body=None):
        self.calls.append((method, path))
        if method == "GET" and path == f"/projects/{PROJECT_ID}/pages":
            return env([{k: v for k, v in p.items() if k != "tiptap_content"} for p in self.pages.values()])
        if method == "POST" and path == f"/projects/{PROJECT_ID}/pages":
            if self.refuse_links and has_link(body["tiptap_content"]["content"]):
                raise cc.CompanyCamError("HTTP 422: invalid tiptap_content", 422)
            self.next_id += 1
            pid = str(self.next_id)
            self.pages[pid] = {"id": pid, "project_id": PROJECT_ID, "title": body["title"],
                               "tiptap_content": copy.deepcopy(body["tiptap_content"])}
            return env({k: v for k, v in self.pages[pid].items() if k != "tiptap_content"})
        if path.startswith("/pages/"):
            pid = path.rsplit("/", 1)[1]
            if pid not in self.pages:
                raise cc.CompanyCamError("HTTP 404", 404)
            if method == "GET":
                assert params == {"include": "tiptap_content"}
                return env(copy.deepcopy(self.pages[pid]))
            if method == "PATCH":
                if self.refuse_links and has_link(body["tiptap_content"]["content"]):
                    raise cc.CompanyCamError("HTTP 422: invalid tiptap_content", 422)
                self.pages[pid]["tiptap_content"] = copy.deepcopy(body["tiptap_content"])
                return env({k: v for k, v in self.pages[pid].items() if k != "tiptap_content"})
        raise cc.CompanyCamError(f"unexpected {method} {path}", 404)

    def notes_page(self):
        return next((p for p in self.pages.values() if p["title"] == "Site Visit Notes"), None)

    def notes_text(self):
        return [scope_notes.node_text(n) for n in self.notes_page()["tiptap_content"]["content"]]


def fake_jobtread(job=JOB):
    def q(query):
        if "job" in query:
            return {"job": job if query["job"]["$"]["id"] == job["id"] else None}
        where = query["organization"]["jobs"]["$"]["where"]
        return {"organization": {"jobs": {"nodes": [job] if where[1] == job["number"] else []}}}
    return q


def run_inline(fn, *args):
    fn(*args)


class BaseCase(unittest.TestCase):
    def setUp(self):
        e = mock.patch.dict(os.environ, {"COMPANYCAM_API_KEY": "cc-test", "SCOPE_API_KEY": KEY,
                                         "DEEPGRAM_API_KEY": "dg-test"})
        e.start()
        self.addCleanup(e.stop)
        self.cc = FakePages()
        p = mock.patch.object(cc, "_request", self.cc)
        p.start()
        self.addCleanup(p.stop)
        self.transcribed = []

        def fake_transcribe(url):
            self.transcribed.append(url)
            return self.transcript, 102.0

        self.transcript = "Wall 1 is 12 feet 6 inches.\n\nThe window on wall 4 is 8 by 4, CMU block wall."
        t = mock.patch.object(scope_transcribe, "transcribe_url", side_effect=fake_transcribe)
        self.transcribe_mock = t.start()
        self.addCleanup(t.stop)
        scope_notes._notes.clear()

    def submit(self, note_id="note-0001", **over):
        payload = {"note_id": note_id, "job": "00999", "audio_url": AUDIO, "started_at": STARTED, "saved_at": SAVED}
        payload.update(over)
        return scope_notes.submit_note(payload, fake_jobtread(), ORG, start_thread=run_inline)


class FormatTests(unittest.TestCase):
    def test_heading_in_greenville_time(self):
        self.assertEqual(scope_notes.note_heading(ts(STARTED)), "Oct 21, 2026 - 2:14 PM")
        # Standard time after Nov 1: 18:14 UTC = 1:14 PM EST
        self.assertEqual(scope_notes.note_heading(ts("2026-11-03T18:14:05Z")), "Nov 3, 2026 - 1:14 PM")

    def test_note_nodes(self):
        nodes = scope_notes.note_nodes("Oct 21, 2026 - 2:14 PM", "Line one.\n\nLine  two\nwraps.",
                                       ts(STARTED), ts(SAVED), 102.0, AUDIO)
        self.assertEqual(nodes[0], {"type": "heading", "attrs": {"level": 2},
                                    "content": [{"type": "text", "text": "Oct 21, 2026 - 2:14 PM"}]})
        meta = scope_notes.node_text(nodes[1])
        self.assertEqual(meta, "Recorded 2:14:05 PM to 2:15:47 PM (1:42 of audio). Play the recording")
        self.assertEqual(nodes[1]["content"][1]["marks"][1], {"type": "link", "attrs": {"href": AUDIO}})
        self.assertEqual([scope_notes.node_text(n) for n in nodes[2:]], ["Line one.", "Line two wraps."])

    def test_plain_link_and_typed_variants(self):
        plain = scope_notes.note_nodes("h", "x", ts(STARTED), ts(SAVED), None, AUDIO, link_audio=False)
        self.assertFalse(has_link(plain))
        self.assertIn(f"Recording: {AUDIO}", scope_notes.node_text(plain[1]))
        typed = scope_notes.note_nodes("h", "x", ts(STARTED), ts(SAVED))
        self.assertEqual(scope_notes.node_text(typed[1]), "Typed note, saved 2:15:47 PM.")


class SubmitTests(BaseCase):
    def test_first_note_creates_the_page(self):
        status, body = self.submit()
        self.assertEqual(status, 202)
        self.assertEqual(self.transcribed, [AUDIO])
        st = scope_notes.note_status("note-0001")
        self.assertEqual(st["status"], "saved")
        self.assertEqual(st["text"], self.transcript)
        self.assertEqual(st["heading"], "Oct 21, 2026 - 2:14 PM")
        page = self.cc.notes_page()
        self.assertEqual(st["page_id"], page["id"])
        texts = self.cc.notes_text()
        self.assertTrue(texts[0].startswith("Word-for-word notes"))
        self.assertEqual(texts[1:], ["Oct 21, 2026 - 2:14 PM",
                                     "Recorded 2:14:05 PM to 2:15:47 PM (1:42 of audio). Play the recording",
                                     "Wall 1 is 12 feet 6 inches.",
                                     "The window on wall 4 is 8 by 4, CMU block wall."])

    def test_second_note_appends_to_the_same_page(self):
        self.submit()
        self.transcript = "Kitchen. Wall 2 is 10 feet."
        self.submit("note-0002", started_at="2026-10-21T18:20:00Z", saved_at="2026-10-21T18:20:30Z")
        notes_pages = [p for p in self.cc.pages.values() if p["title"] == "Site Visit Notes"]
        self.assertEqual(len(notes_pages), 1)
        texts = self.cc.notes_text()
        self.assertEqual(texts[-3:], ["Oct 21, 2026 - 2:20 PM",
                                      "Recorded 2:20:00 PM to 2:20:30 PM (1:42 of audio). Play the recording",
                                      "Kitchen. Wall 2 is 10 feet."])
        self.assertIn(("PATCH", f"/pages/{notes_pages[0]['id']}"), self.cc.calls)
        # The other Page on the project is untouched.
        self.assertEqual(len(self.cc.pages["2999797"]["tiptap_content"]["content"]), 1)

    def test_existing_page_content_is_kept(self):
        self.cc.pages["555"] = {"id": "555", "project_id": PROJECT_ID, "title": "Site Visit Notes",
                                "tiptap_content": {"type": "doc", "attrs": {"pageSettings": {"showTags": True}},
                                                   "content": [{"type": "paragraph", "content": [
                                                       {"type": "text", "text": "Earlier visit."}]}]}}
        self.submit()
        doc = self.cc.pages["555"]["tiptap_content"]
        self.assertEqual(doc["attrs"], {"pageSettings": {"showTags": True}})
        self.assertEqual(scope_notes.node_text(doc["content"][0]), "Earlier visit.")
        self.assertEqual(scope_notes.node_text(doc["content"][1]), "Oct 21, 2026 - 2:14 PM")

    def test_retry_with_same_id_does_not_duplicate(self):
        self.submit()
        status, body = self.submit()
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "saved")
        self.assertEqual(self.transcribed, [AUDIO])
        self.assertEqual(self.cc.notes_text().count("Oct 21, 2026 - 2:14 PM"), 1)

    def test_link_refused_falls_back_to_plain_text(self):
        self.cc.refuse_links = True
        self.submit()
        self.assertEqual(scope_notes.note_status("note-0001")["status"], "saved")
        self.assertIn(f"Recording: {AUDIO}", self.cc.notes_text()[2])

    def test_no_speech_is_a_failure_and_writes_nothing(self):
        self.transcript = "   "
        self.submit()
        st = scope_notes.note_status("note-0001")
        self.assertEqual(st["status"], "failed")
        self.assertIn("No speech", st["error"])
        self.assertIsNone(self.cc.notes_page())

    def test_transcription_error_is_reported(self):
        self.transcribe_mock.side_effect = scope_transcribe.TranscribeError("Deepgram returned HTTP 402: out of credit")
        self.submit()
        st = scope_notes.note_status("note-0001")
        self.assertEqual(st["status"], "failed")
        self.assertIn("402", st["error"])
        self.assertIsNone(self.cc.notes_page())

    def test_typed_text_skips_transcription(self):
        self.submit(audio_url="", text="Typed: garage door header is 16 feet.")
        self.assertEqual(self.transcribed, [])
        self.assertEqual(self.cc.notes_text()[-1], "Typed: garage door header is 16 feet.")
        self.assertEqual(self.cc.notes_text()[-2], "Typed note, saved 2:15:47 PM.")

    def test_bad_inputs(self):
        for over, msg in [({"note_id": "x"}, "note_id"),
                          ({"audio_url": "https://evil.example.com/a.m4a"}, "Vercel Blob"),
                          ({"audio_url": AUDIO.replace("https", "http")}, "Vercel Blob"),
                          ({"audio_url": "", "text": ""}, "audio_url or text"),
                          ({"job": "12345"}, "No JobTread job")]:
            with self.assertRaises(Exception) as cm:
                self.submit(**over)
            self.assertIn(msg, str(cm.exception))

    def test_job_without_companycam_project(self):
        job = dict(JOB, companycamId=None)
        with self.assertRaises(scope_notes.NoteError) as cm:
            scope_notes.submit_note({"note_id": "note-0001", "job": "00999", "audio_url": AUDIO},
                                    fake_jobtread(job), ORG, start_thread=run_inline)
        self.assertEqual(cm.exception.status, 404)

    def test_missing_deepgram_key(self):
        with mock.patch.dict(os.environ, {"DEEPGRAM_API_KEY": ""}):
            with self.assertRaises(scope_notes.NoteError) as cm:
                self.submit()
        self.assertEqual(cm.exception.status, 503)

    def test_simultaneous_notes_both_land(self):
        real = self.cc

        def slow(method, path, params=None, body=None):
            if method in ("PATCH", "POST"):
                time.sleep(0.05)   # widen the read-modify-write window
            return real(method, path, params, body)

        with mock.patch.object(cc, "_request", slow):
            threads = [threading.Thread(target=self.submit, args=(f"note-000{i}",),
                                        kwargs={"started_at": f"2026-10-21T18:1{i}:00Z"}) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        headings = [t for t in self.cc.notes_text() if t.startswith("Oct 21")]
        self.assertEqual(sorted(headings), [f"Oct 21, 2026 - 2:1{i} PM" for i in range(4)])
        self.assertEqual(len([p for p in self.cc.pages.values() if p["title"] == "Site Visit Notes"]), 1)


class FixTests(BaseCase):
    def test_fix_replaces_the_text(self):
        self.submit()
        out = scope_notes.fix_note({"job": "00999", "note_id": "note-0001", "heading": "Oct 21, 2026 - 2:14 PM",
                                    "old_text": self.transcript, "new_text": "Wall 1 is 13 feet 6 inches."},
                                   fake_jobtread(), ORG)
        self.assertEqual(out["result"], "replaced")
        texts = self.cc.notes_text()
        self.assertEqual(texts[-2:], ["Recorded 2:14:05 PM to 2:15:47 PM (1:42 of audio). Play the recording",
                                      "Wall 1 is 13 feet 6 inches."])
        self.assertEqual(scope_notes.note_status("note-0001")["text"], "Wall 1 is 13 feet 6 inches.")

    def test_fix_only_touches_its_own_note(self):
        self.submit()
        first = self.transcript
        self.transcript = "Second note text."
        self.submit("note-0002", started_at="2026-10-21T18:20:00Z")
        scope_notes.fix_note({"job": "00999", "heading": "Oct 21, 2026 - 2:14 PM", "old_text": first,
                              "new_text": "Fixed first."}, fake_jobtread(), ORG)
        texts = self.cc.notes_text()
        self.assertIn("Fixed first.", texts)
        self.assertEqual(texts[-1], "Second note text.")

    def test_fix_after_edit_in_companycam_adds_a_corrected_note(self):
        self.submit()
        page = self.cc.notes_page()
        page["tiptap_content"]["content"][-1]["content"][0]["text"] = "Someone edited this in CompanyCam."
        out = scope_notes.fix_note({"job": "00999", "heading": "Oct 21, 2026 - 2:14 PM",
                                    "old_text": self.transcript, "new_text": "Wall 1 is 13 feet."},
                                   fake_jobtread(), ORG)
        self.assertEqual(out["result"], "added")
        texts = self.cc.notes_text()
        self.assertIn("Someone edited this in CompanyCam.", texts)
        self.assertEqual(texts[-3], "Oct 21, 2026 - 2:14 PM (corrected)")
        self.assertEqual(texts[-1], "Wall 1 is 13 feet.")


class VisitFilingTests(BaseCase):
    """Notes recorded after a visit, about its photos (build plan, Phase 4 live test)."""

    HEADING = "Oct 21, 2026 - 2:14 PM"

    def notes(self):
        return scope_quickpass.parse_notes_page(self.cc.notes_page()["tiptap_content"])

    def test_note_filed_under_an_earlier_visit(self):
        code, body = self.submit(visit_date="2026-10-19")
        self.assertEqual(body["visit_date"], "2026-10-19")
        self.assertEqual(self.cc.notes_text()[-3],
                         "Recorded 2:14:05 PM to 2:15:47 PM (1:42 of audio). Filed under the Oct 19, 2026 visit. "
                         "Play the recording")
        note = self.notes()[0]
        self.assertEqual((note["visit_date"], note["filed_later"]), ("2026-10-19", True))
        self.assertEqual(note["text"], self.transcript)

    def test_same_day_or_no_visit_date_writes_no_filing_line(self):
        code, body = self.submit(visit_date="2026-10-21")
        self.assertEqual(body["visit_date"], "2026-10-21")
        self.submit("note-0002", started_at="2026-10-21T18:20:00Z")
        self.assertFalse(any("Filed under" in t for t in self.cc.notes_text()))

    def test_typed_note_filed(self):
        scope_notes.submit_note({"note_id": "note-typed-1", "job": "00999", "text": "Porch step is rotted.",
                                 "saved_at": SAVED, "visit_date": "2026-10-10"},
                                fake_jobtread(), ORG, start_thread=run_inline)
        self.assertIn("Typed note, saved 2:15:47 PM. Filed under the Oct 10, 2026 visit.", self.cc.notes_text())
        self.assertEqual(self.notes()[0]["visit_date"], "2026-10-10")

    def test_bad_visit_dates(self):
        for bad, msg in (("10/19/2026", "look like"), ("2026-10-22", "after the day it was recorded")):
            with self.assertRaises(scope_notes.NoteError) as e:
                self.submit(f"note-bad-{bad[:2]}", visit_date=bad)
            self.assertIn(msg, str(e.exception))
        self.assertIsNone(self.cc.notes_page())   # nothing written

    def refile(self, visit_date, text=None, heading=None):
        return scope_notes.refile_note({"job": "00999", "note_id": "note-0001", "heading": heading or self.HEADING,
                                        "text": self.transcript if text is None else text,
                                        "visit_date": visit_date}, fake_jobtread(), ORG)

    def test_refile_a_saved_note_and_back(self):
        self.submit()
        self.assertEqual(self.refile("2026-10-19"), {"ok": True, "visit_date": "2026-10-19"})
        meta = self.cc.notes_text()[-3]
        self.assertEqual(meta, "Recorded 2:14:05 PM to 2:15:47 PM (1:42 of audio). Filed under the Oct 19, 2026 visit. "
                               "Play the recording")
        self.assertEqual(scope_notes.note_status("note-0001")["visit_date"], "2026-10-19")
        # Link to the recording survives.
        node = self.cc.notes_page()["tiptap_content"]["content"][-3]
        self.assertTrue(has_link([node]))
        # Another visit replaces the line rather than adding a second one.
        self.refile("2026-10-10")
        self.assertEqual(self.cc.notes_text()[-3].count("Filed under"), 1)
        self.assertEqual(self.notes()[0]["visit_date"], "2026-10-10")
        # Back to the day it was recorded: the line goes away, spacing intact.
        self.assertEqual(self.refile("2026-10-21")["visit_date"], "2026-10-21")
        self.assertEqual(self.cc.notes_text()[-3], "Recorded 2:14:05 PM to 2:15:47 PM (1:42 of audio). Play the recording")
        self.assertFalse(self.notes()[0]["filed_later"])

    def test_refile_when_companycam_merged_the_italic_text(self):
        # CompanyCam may join neighbouring text with the same marks into one node.
        self.submit(visit_date="2026-10-19")
        meta = self.cc.notes_page()["tiptap_content"]["content"][-3]
        meta["content"] = [{"type": "text", "text": scope_notes.node_text(meta), "marks": [{"type": "italic"}]}]
        self.refile("2026-10-21")
        self.assertEqual(self.cc.notes_text()[-3], "Recorded 2:14:05 PM to 2:15:47 PM (1:42 of audio). Play the recording")

    def test_refile_typed_note(self):
        scope_notes.submit_note({"note_id": "note-0001", "job": "00999", "text": "Typed one.", "saved_at": SAVED,
                                 "started_at": STARTED}, fake_jobtread(), ORG, start_thread=run_inline)
        self.refile("2026-10-19", text="Typed one.")
        self.assertIn("Typed note, saved 2:15:47 PM. Filed under the Oct 19, 2026 visit.", self.cc.notes_text())
        self.refile("2026-10-21", text="Typed one.")
        self.assertIn("Typed note, saved 2:15:47 PM.", self.cc.notes_text())

    def test_refile_only_touches_its_own_note(self):
        self.submit()
        first = self.transcript
        self.transcript = "Second note, same minute."
        self.submit("note-0002", started_at="2026-10-21T18:14:40Z")
        self.refile("2026-10-19", text=first)
        notes = self.notes()
        self.assertEqual([(n["text"][:6], n["visit_date"]) for n in notes],
                         [("Wall 1", "2026-10-19"), ("Second", "2026-10-21")])

    def test_refile_errors(self):
        self.submit()
        with self.assertRaises(scope_notes.NoteError) as e:
            self.refile("2026-10-19", text="Edited in CompanyCam since.")
        self.assertEqual(e.exception.status, 404)
        with self.assertRaises(scope_notes.NoteError) as e:
            self.refile("2026-10-25")
        self.assertIn("after the day it was recorded", str(e.exception))

    def test_corrected_note_stays_on_the_same_visit(self):
        self.submit(visit_date="2026-10-19")
        page = self.cc.notes_page()
        page["tiptap_content"]["content"][-1]["content"][0]["text"] = "Edited in CompanyCam."
        out = scope_notes.fix_note({"job": "00999", "heading": self.HEADING, "old_text": self.transcript,
                                    "new_text": "Wall 1 is 13 feet."}, fake_jobtread(), ORG)
        self.assertEqual(out["result"], "added")
        corrected = self.notes()[-1]
        self.assertTrue(corrected["corrected"])
        self.assertEqual(corrected["visit_date"], "2026-10-19")


class DeepgramTests(unittest.TestCase):
    def test_request_url(self):
        url = scope_transcribe.request_url()
        self.assertTrue(url.startswith("https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true"))
        self.assertIn("keyterm=GFCI", url)
        self.assertIn("keyterm=quarter+round", url)
        self.assertNotIn(",", url.split("keyterm=", 1)[1].split("&")[0])

    def test_keyterms_stay_well_under_the_500_token_cap(self):
        # Rough token count: about one token per 4 characters, at least one per word.
        approx = sum(max(len(t.split()), len(t) // 4 + 1) for t in scope_transcribe.TRADE_KEYTERMS)
        self.assertLess(approx, 350)

    def test_parse_response(self):
        body = {"metadata": {"duration": 101.6},
                "results": {"channels": [{"alternatives": [{
                    "transcript": "Wall 1 is 12 feet. Wall 2 is 10 feet.",
                    "paragraphs": {"transcript": "\nWall 1 is 12 feet.\n\nWall 2 is 10 feet."}}]}]}}
        self.assertEqual(scope_transcribe.parse_response(body), ("Wall 1 is 12 feet.\n\nWall 2 is 10 feet.", 101.6))
        no_para = {"results": {"channels": [{"alternatives": [{"transcript": "Hello."}]}]}}
        self.assertEqual(scope_transcribe.parse_response(no_para), ("Hello.", None))
        with self.assertRaises(scope_transcribe.TranscribeError):
            scope_transcribe.parse_response({"err_msg": "bad"})

    def test_calls_deepgram_with_the_audio_url(self):
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            seen.update(url=req.full_url, auth=req.get_header("Authorization"), body=json.loads(req.data))
            return Resp(json.dumps({"results": {"channels": [{"alternatives": [{"transcript": "Hi."}]}]}}).encode())

        with mock.patch.dict(os.environ, {"DEEPGRAM_API_KEY": "dg-key"}), \
                mock.patch.object(scope_transcribe.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(scope_transcribe.transcribe_url(AUDIO), ("Hi.", None))
        self.assertEqual(seen["auth"], "Token dg-key")
        self.assertEqual(seen["body"], {"url": AUDIO})
        self.assertIn("model=nova-3", seen["url"])

    def test_bad_key_message(self):
        def fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"err_code":"INVALID_AUTH"}'))

        with mock.patch.dict(os.environ, {"DEEPGRAM_API_KEY": "dg-key"}), \
                mock.patch.object(scope_transcribe.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(scope_transcribe.TranscribeError) as cm:
                scope_transcribe.transcribe_url(AUDIO)
        self.assertIn("HTTP 401", str(cm.exception))
        self.assertIn("Deepgram console", str(cm.exception))


class HttpEndpointTests(BaseCase):
    """Round trip through the real app.py Handler and a local server."""

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

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get("Content-Type") == "application/json" else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_for(self, note_id, status="saved"):
        for _ in range(100):
            code, body = self.call("GET", f"/scope/notes/status?id={note_id}", headers={"X-Scope-Key": KEY})
            if body.get("status") in (status, "failed"):
                return body
            time.sleep(0.02)
        self.fail("note never finished")

    def test_post_note_then_status(self):
        code, body = self.call("POST", "/scope/notes",
                               {"note_id": "note-http-1", "job": "00999", "audio_url": AUDIO,
                                "started_at": STARTED, "saved_at": SAVED}, {"X-Scope-Key": KEY})
        self.assertEqual(code, 202)
        self.assertEqual(body["status"], "transcribing")
        done = self.wait_for("note-http-1")
        self.assertEqual(done["status"], "saved")
        self.assertEqual(done["text"], self.transcript)
        self.assertIn("Wall 1 is 12 feet 6 inches.", self.cc.notes_text())

    def test_post_fix(self):
        self.call("POST", "/scope/notes", {"note_id": "note-http-2", "job": "00999", "audio_url": AUDIO,
                                           "started_at": STARTED}, {"X-Scope-Key": KEY})
        self.wait_for("note-http-2")
        code, body = self.call("POST", "/scope/notes/fix",
                               {"job": "00999", "note_id": "note-http-2", "heading": "Oct 21, 2026 - 2:14 PM",
                                "old_text": self.transcript, "new_text": "Fixed."}, {"X-Scope-Key": KEY})
        self.assertEqual((code, body), (200, {"ok": True, "result": "replaced"}))

    def test_post_visit(self):
        self.call("POST", "/scope/notes", {"note_id": "note-http-4", "job": "00999", "audio_url": AUDIO,
                                           "started_at": STARTED, "saved_at": SAVED}, {"X-Scope-Key": KEY})
        self.wait_for("note-http-4")
        code, body = self.call("POST", "/scope/notes/visit",
                               {"job": "00999", "note_id": "note-http-4", "heading": "Oct 21, 2026 - 2:14 PM",
                                "text": self.transcript, "visit_date": "2026-10-19"}, {"X-Scope-Key": KEY})
        self.assertEqual((code, body), (200, {"ok": True, "visit_date": "2026-10-19"}))
        code, body = self.call("GET", "/scope/notes/status?id=note-http-4", headers={"X-Scope-Key": KEY})
        self.assertEqual(body["visit_date"], "2026-10-19")

    def test_wrong_key_and_bad_body(self):
        code, body = self.call("POST", "/scope/notes", {"note_id": "note-http-3"}, {"X-Scope-Key": "nope"})
        self.assertEqual(code, 401)
        req = urllib.request.Request(self.base + "/scope/notes", data=b"not json", method="POST",
                                     headers={"X-Scope-Key": KEY})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)

    def test_status_unknown_note(self):
        code, body = self.call("GET", "/scope/notes/status?id=note-never", headers={"X-Scope-Key": KEY})
        self.assertEqual(code, 404)
        self.assertIn("Site Visit Notes Page", body["error"])

    def test_typed_test_endpoint(self):
        code, body = self.call("GET", f"/scope/notes-test?key={KEY}&job=00999&text=Hello+from+the+test")
        self.assertEqual(code, 200)
        self.assertEqual(body["text"], "Hello from the test")
        self.assertEqual(self.cc.notes_text()[-1], "Hello from the test")

    def test_other_posts_still_answer_ok_right_away(self):
        with mock.patch.object(self.app.Handler, "_route") as route:
            code, raw = self.call("POST", "/rerun-estimate", {})
        self.assertEqual((code, raw), (200, b"OK"))
        route.assert_called_once()


if __name__ == "__main__":
    unittest.main()
