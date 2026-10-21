"""Tests for walkthrough recordings (scope_walkthrough.py, the walkthrough
path in scope_notes.py, and timed transcripts in scope_transcribe.py).

Run from the repo root:  python3 -m unittest discover tests

Reuses the CompanyCam and JobTread fakes from test_scope_notes.py and adds
the project's photos. Deepgram's paragraphs/sentences shape follows its
documented pre-recorded response. No keys or network needed.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import companycam_api as cc  # noqa: E402
import scope_notes  # noqa: E402
import scope_quickpass  # noqa: E402
import scope_transcribe  # noqa: E402
import scope_walkthrough as sw  # noqa: E402
from tests.test_scope_notes import (AUDIO, ORG, PROJECT_ID, BaseCase, FakePages, env,  # noqa: E402
                                     fake_jobtread, run_inline, ts)

# Oct 21, 2026 2:14:00 PM Greenville time (EDT)
TAP = ts("2026-10-21T18:14:00.000Z")


def sentence(text, start, end=None):
    return {"text": text, "start": start, "end": start + 2 if end is None else end}


def photo(pid, at, description=None):
    return {"id": pid, "captured_at": at, "description": {"plain_text_content": description or ""}}


class ChooseStartTests(unittest.TestCase):
    def test_tap_is_used_when_it_fits(self):
        # Tapped, recorded 10 minutes, stopped, sent 20 minutes later.
        self.assertEqual(sw.choose_start(TAP, TAP + 605, 600, TAP + 1800), (TAP, "tap"))
        self.assertEqual(sw.choose_start(TAP, None, 600, TAP + 1800), (TAP, "tap"))

    def test_tap_hours_before_the_recording_is_ignored(self):
        file_end = TAP + 5 * 3600
        self.assertEqual(sw.choose_start(TAP, file_end, 600, file_end + 60), (file_end - 600, "file"))

    def test_tap_too_late_for_the_recording_is_ignored(self):
        # A 20-minute recording that ended 5 minutes after the tap began before it.
        self.assertEqual(sw.choose_start(TAP, TAP + 300, 1200, TAP + 400), (TAP + 300 - 1200, "file"))

    def test_no_tap(self):
        self.assertEqual(sw.choose_start(None, TAP + 600, 600, TAP + 900), (TAP, "file"))
        self.assertEqual(sw.choose_start(None, None, 600, TAP + 900), (TAP + 300, "upload"))


class LayoutTests(unittest.TestCase):
    def test_photos_during_keeps_the_recording_window_only(self):
        photos = [
            photo("late", TAP + 640),               # 40 s after the end: out
            photo("p2", TAP + 300),
            photo("drawing", TAP + 200, "Floor plan - drawn by the Site Scope Tool"),
            photo("p1", TAP - 10),                  # just before Start: in
            photo("early", TAP - 600),
            photo("p3", TAP + 610),                 # just after Stop: in
        ]
        got = sw.photos_during(photos, TAP, 600)
        self.assertEqual([p["id"] for p in got], ["p1", "p2", "p3"])

    def test_markers_go_before_the_next_sentence(self):
        paragraphs = [[sentence("This is the hall bath.", 0), sentence("Vanity is 48 inches.", 30)],
                      [sentence("Moving to the kitchen.", 120)]]
        photos = [{"id": "a", "at": TAP + 5}, {"id": "b", "at": TAP + 25}, {"id": "c", "at": TAP + 125},
                  {"id": "d", "at": TAP + 400}]
        text = sw.plain_text(sw.layout(paragraphs, photos, TAP))
        self.assertEqual(text, "This is the hall bath. [Photo 1, 2:14:05 PM] [Photo 2, 2:14:25 PM] "
                               "Vanity is 48 inches.\n\nMoving to the kitchen."
                               "\n\n[Photo 3, 2:16:05 PM] [Photo 4, 2:20:40 PM]")

    def test_photo_before_any_talk(self):
        laid = sw.layout([[sentence("Front of the house.", 8)]], [{"id": "a", "at": TAP + 2}], TAP)
        self.assertEqual(sw.plain_text(laid), "[Photo 1, 2:14:02 PM] Front of the house.")
        nodes = sw.body_nodes(laid)
        self.assertEqual(nodes[0]["content"][0]["marks"], [{"type": "italic"}])
        self.assertEqual(scope_notes.node_text(nodes[0]), "[Photo 1, 2:14:02 PM] Front of the house.")

    def test_meta_sentence(self):
        self.assertIn("2 CompanyCam photos taken during it are marked", sw.meta_sentence(2, "tap"))
        self.assertIn("1 CompanyCam photo taken during it is marked", sw.meta_sentence(1, "tap"))
        self.assertIn("no CompanyCam photos", sw.meta_sentence(0, "file"))
        self.assertIn("from the recording file", sw.meta_sentence(0, "file"))
        self.assertIn("may be off", sw.meta_sentence(3, "upload"))


class TimedTranscriptTests(unittest.TestCase):
    def test_sentences_with_times(self):
        body = {"metadata": {"duration": 612.4}, "results": {"channels": [{"alternatives": [{
            "transcript": "Hall bath. Vanity is 48 inches. Kitchen next.",
            "paragraphs": {"transcript": "\nHall bath. Vanity is 48 inches.\n\nKitchen next.",
                           "paragraphs": [
                               {"sentences": [{"text": "Hall bath.", "start": 0.4, "end": 1.2},
                                              {"text": "Vanity is  48 inches.", "start": 30.1, "end": 32.0}]},
                               {"sentences": [{"text": "Kitchen next.", "start": 121.0, "end": 122.5}]}]}}]}]}}
        paragraphs, duration = scope_transcribe.parse_timed_response(body)
        self.assertEqual(duration, 612.4)
        self.assertEqual(paragraphs, [[sentence("Hall bath.", 0.4, 1.2), sentence("Vanity is 48 inches.", 30.1, 32.0)],
                                      [sentence("Kitchen next.", 121.0, 122.5)]])

    def test_falls_back_to_plain_paragraphs(self):
        body = {"results": {"channels": [{"alternatives": [{"transcript": "Hello there."}]}]}}
        self.assertEqual(scope_transcribe.parse_timed_response(body), ([[sentence("Hello there.", 0.0, 0.0)]], None))
        silent = {"results": {"channels": [{"alternatives": [{"transcript": ""}]}]}}
        self.assertEqual(scope_transcribe.parse_timed_response(silent), ([], None))


class PagesAndPhotos(FakePages):
    def __init__(self, photos):
        super().__init__()
        self.photos = photos

    def __call__(self, method, path, params=None, body=None):
        if method == "GET" and path == f"/projects/{PROJECT_ID}/photos":
            self.calls.append((method, path))
            return env(self.photos)
        return super().__call__(method, path, params, body)


class WalkthroughSubmitTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.cc = PagesAndPhotos([photo("901", TAP + 140), photo("900", TAP + 20), photo("old", TAP - 3600)])
        p = mock.patch.object(cc, "_request", self.cc)
        p.start()
        self.addCleanup(p.stop)
        self.paragraphs = [[sentence("Starting in the hall bath.", 1), sentence("The vanity is 48 inches.", 30)],
                           [sentence("Now the kitchen, the window on wall 4 is 36 by 48.", 150)]]
        self.duration = 600.0
        t = mock.patch.object(scope_transcribe, "transcribe_url_timed",
                              side_effect=lambda url: (self.paragraphs, self.duration))
        t.start()
        self.addCleanup(t.stop)

    def submit_walkthrough(self, note_id="walk-0001", **over):
        payload = {"kind": "walkthrough", "note_id": note_id, "job": "00999", "audio_url": AUDIO,
                   "tapped_at": "2026-10-21T18:14:00.000Z", "file_modified_at": "2026-10-21T18:24:05.000Z"}
        payload.update(over)
        return scope_notes.submit_note(payload, fake_jobtread(), ORG, start_thread=run_inline,
                                       now=TAP + 1500)

    def test_walkthrough_lands_with_photo_markers(self):
        status, reply = self.submit_walkthrough()
        self.assertEqual(status, 202)
        self.assertEqual(reply["kind"], "walkthrough")
        done = scope_notes.note_status("walk-0001")
        self.assertEqual(done["status"], "saved", done.get("error"))
        self.assertEqual(done["heading"], "Oct 21, 2026 - 2:14 PM")
        self.assertEqual(done["photo_count"], 2)
        self.assertEqual(done["start_source"], "tap")
        self.assertIn("[Photo 1, 2:14:20 PM] The vanity is 48 inches.", done["text"])
        texts = self.cc.notes_text()
        self.assertEqual(texts[1], "Oct 21, 2026 - 2:14 PM")
        self.assertTrue(texts[2].startswith("Recorded 2:14:00 PM to 2:24:00 PM (10:00 of audio). Walkthrough "
                                            "recording; 2 CompanyCam photos taken during it are marked in the text. "
                                            "Play the recording"), texts[2])
        self.assertEqual(texts[3], "Starting in the hall bath. [Photo 1, 2:14:20 PM] The vanity is 48 inches.")
        self.assertEqual(texts[4], "[Photo 2, 2:16:20 PM] Now the kitchen, the window on wall 4 is 36 by 48.")

        # The quick pass and preview read it like any other note.
        notes = scope_quickpass.parse_notes_page(self.cc.notes_page()["tiptap_content"])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["started"].strftime("%H:%M:%S"), "14:14:00")
        self.assertEqual(notes[0]["ended"].strftime("%H:%M:%S"), "14:24:00")
        self.assertEqual(notes[0]["visit_date"], "2026-10-21")
        self.assertIn("[Photo 2, 2:16:20 PM]", notes[0]["text"])

    def test_start_from_the_file_when_there_was_no_tap(self):
        self.submit_walkthrough(tapped_at=None)
        done = scope_notes.note_status("walk-0001")
        self.assertEqual(done["start_source"], "file")
        self.assertEqual(done["heading"], "Oct 21, 2026 - 2:14 PM")
        self.assertIn("worked out from the recording file", " ".join(self.cc.notes_text()))

    def test_filed_under_an_earlier_visit(self):
        self.submit_walkthrough(visit_date="2026-10-19")
        notes = scope_quickpass.parse_notes_page(self.cc.notes_page()["tiptap_content"])
        self.assertEqual(notes[0]["visit_date"], "2026-10-19")
        self.assertTrue(notes[0]["filed_later"])

    def test_silent_recording_writes_nothing(self):
        self.paragraphs = []
        self.submit_walkthrough()
        done = scope_notes.note_status("walk-0001")
        self.assertEqual(done["status"], "failed")
        self.assertIn("No speech", done["error"])
        self.assertIsNone(self.cc.notes_page())

    def test_needs_audio(self):
        with self.assertRaises(scope_notes.NoteError):
            self.submit_walkthrough(audio_url="")

    def test_retry_returns_the_same_walkthrough(self):
        self.submit_walkthrough()
        status, reply = self.submit_walkthrough()
        self.assertEqual(status, 200)
        self.assertEqual(reply["status"], "saved")
        self.assertEqual(sum(1 for t in self.cc.notes_text() if t == "Oct 21, 2026 - 2:14 PM"), 1)

    def test_plain_notes_still_work_alongside(self):
        self.submit_walkthrough()
        self.submit(note_id="note-0009")
        self.assertEqual(scope_notes.note_status("note-0009")["status"], "saved")
        self.assertEqual(len(scope_quickpass.parse_notes_page(self.cc.notes_page()["tiptap_content"])), 2)


if __name__ == "__main__":
    unittest.main()
