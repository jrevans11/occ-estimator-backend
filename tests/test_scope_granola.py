"""Tests for Site Scope Tool phase 9: Granola recordings (granola_api.py and
the G sources in scope_quickpass.py). Manual only: a Start on the scope page
reads that day's recordings for the job.

Run from the repo root:  python3 -m unittest discover tests

Granola is faked at granola_api._request (path, params) with notes shaped like
the public API docs. CompanyCam, Claude and the store are faked as in
test_scope_runs. No keys or network needed.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import granola_api as ga  # noqa: E402
import scope_quickpass as qp  # noqa: E402
import scope_runs  # noqa: E402
import test_scope_runs as tr  # noqa: E402

JOB_OUT = {"id": tr.JOB["id"], "number": "00489", "name": tr.JOB["name"], "address": tr.JOB["location"]["address"]}


def gnote(note_id="not_visit1", title="1217 Fieldstone (00489)", created="2026-10-20T02:09:00Z",
          transcript=None, event_title=None):
    n = {"id": note_id, "object": "note", "title": title, "owner": {"name": "Jason Evans"},
         "created_at": created, "calendar_event": {"event_title": event_title} if event_title else None}
    if transcript is not None:
        n["transcript"] = transcript
    return n


LINES = [
    {"speaker": {"source": "microphone", "diarization_label": "Speaker A"}, "text": "Kitchen first.",
     "start_time": "2026-10-20T02:09:05Z"},
    {"speaker": {"source": "microphone", "diarization_label": "Speaker A"}, "text": "Backsplash is 18 square feet.",
     "start_time": "2026-10-20T02:09:20Z"},
    {"speaker": {"source": "microphone", "diarization_label": "Speaker B"}, "text": "Can you paint the hall too?",
     "start_time": "2026-10-20T02:10:40Z"},
]


class FakeGranola:
    def __init__(self, notes=(), too_large=()):
        self.notes = {n["id"]: n for n in notes}
        self.too_large = set(too_large)
        self.calls = []

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/notes":
            return {"notes": [{k: v for k, v in n.items() if k != "transcript"} for n in self.notes.values()],
                    "hasMore": False, "cursor": None}
        parts = path.strip("/").split("/")
        note = self.notes.get(parts[1])
        if not note:
            raise ga.GranolaError("Granola answered HTTP 404: not found", 404, "NOT_FOUND")
        if len(parts) == 3:   # /notes/{id}/transcript, two pages
            lines = note.get("transcript") or []
            if params.get("cursor"):
                return {"transcript": lines[2:], "hasMore": False, "cursor": None}
            return {"transcript": lines[:2], "hasMore": True, "cursor": "c2"}
        if (params or {}).get("include") == "transcript":
            if note["id"] in self.too_large:
                raise ga.GranolaError("Granola answered HTTP 413: too large", 413, "TRANSCRIPT_TOO_LARGE")
            return dict(note)
        return {k: v for k, v in note.items() if k != "transcript"}


# ── granola_api ──────────────────────────────────────────────────────────────

class MatchingTests(unittest.TestCase):
    def test_job_number_in_titles(self):
        self.assertEqual(ga.job_number_in("100 Boxwood (00905)"), "00905")
        self.assertEqual(ga.job_number_in("Jonathan Hufford and Jason Evans (00899)"), "00899")
        self.assertEqual(ga.job_number_in("Walkthrough ( 0905 )"), "00905")
        self.assertEqual(ga.job_number_in("Visit 00905 walkthrough"), "00905")
        self.assertIsNone(ga.job_number_in("Repair Meeting"))
        self.assertIsNone(ga.job_number_in("3122 Wade Hampton Blvd, Taylors SC 29687"))
        self.assertIsNone(ga.job_number_in("Call (864) 252-4999"))

    def test_calendar_title_counts(self):
        job = {"number": "00905", "address": "100 Boxwood Ln, Greer, SC"}
        self.assertTrue(ga.note_matches_job(gnote(title="Site visit", event_title="Visit (00905)"), job))

    def test_address_match(self):
        job = {"number": "00700", "address": "316 Stewart St, Greenville, SC 29601"}
        self.assertTrue(ga.note_matches_job(gnote(title="316 Stewart. St."), job))
        self.assertTrue(ga.note_matches_job(gnote(title="Kitchen (00700)"), job))
        self.assertFalse(ga.note_matches_job(gnote(title="318 Stewart St"), job))
        self.assertFalse(ga.note_matches_job(gnote(title="316 Main St"), job))
        self.assertFalse(ga.note_matches_job(gnote(title="Repair Meeting"), job))


class TranscriptTests(unittest.TestCase):
    def test_speakers_merge_and_time_marks(self):
        text = ga.transcript_text(gnote(transcript=LINES))
        self.assertEqual(text.splitlines(), [
            "[10:09 PM] Speaker A: Kitchen first. Backsplash is 18 square feet.",
            "[10:10 PM] Speaker B: Can you paint the hall too?"])

    def test_attribution_and_relative_times(self):
        lines = [{"speaker": {"source": "microphone", "attribution": "me"}, "text": "Hi.", "start_time": 0},
                 {"speaker": {"source": "speaker", "attribution": "them"}, "text": "Hello.", "start_time": 30}]
        text = ga.transcript_text(gnote(transcript=lines))
        self.assertEqual(text.splitlines(), ["[10:09 PM] Jason Evans: Hi.", "Other speaker: Hello."])

    def test_long_transcript_is_cut(self):
        lines = [{"speaker": {"diarization_label": f"S{i % 2}"}, "text": "word " * 50} for i in range(100)]
        text = ga.transcript_text(gnote(transcript=lines), max_chars=2000)
        self.assertLess(len(text), 2100)
        self.assertTrue(text.endswith("too long.]"))

    def test_too_large_transcript_is_paged(self):
        fake = FakeGranola([gnote(transcript=LINES)], too_large={"not_visit1"})
        with mock.patch.object(ga, "_request", fake), mock.patch.dict(os.environ, {"GRANOLA_API_KEY": "grn_x"}):
            note = ga.get_note("not_visit1", transcript=True)
        self.assertEqual([x["text"] for x in note["transcript"]], [x["text"] for x in LINES])

    def test_bad_note_id(self):
        with self.assertRaises(ga.GranolaError):
            ga.get_note("../etc")

    def test_notes_for_job_on_day(self):
        fake = FakeGranola([gnote(), gnote("not_meet", "Repair Meeting"),
                            gnote("not_other_day", "1217 Fieldstone (00489)", "2026-10-21T15:00:00Z")])
        with mock.patch.object(ga, "_request", fake), mock.patch.dict(os.environ, {"GRANOLA_API_KEY": "grn_x"}):
            found = ga.notes_for_job_on_day(JOB_OUT, "2026-10-19")
        self.assertEqual([n["id"] for n in found], ["not_visit1"])


# ── The quick pass reads Granola ─────────────────────────────────────────────

class QuickPassGranolaTests(tr.Base):
    def setUp(self):
        super().setUp()
        os.environ["GRANOLA_API_KEY"] = "grn_x"
        self.granola = FakeGranola([gnote(transcript=LINES), gnote("not_meet", "Repair Meeting", transcript=LINES)])
        p = mock.patch.object(ga, "_request", self.granola)
        p.start()
        self.addCleanup(p.stop)

    def test_days_recording_is_a_g_source_in_time_order(self):
        visit = qp.gather_visit(tr.PROJECT_ID, None, job=JOB_OUT)
        self.assertEqual(visit["visit_date"], "2026-10-19")
        self.assertEqual([g["id"] for g in visit["granola"]], ["not_visit1"])   # not the "Repair Meeting"
        content = qp.build_content(visit, JOB_OUT)
        texts = [c["text"] for c in content if c["type"] == "text"]
        self.assertIn("1 Granola recording(s)", texts[0])
        g = next(i for i, x in enumerate(texts) if x.startswith("G1 - Granola recording \"1217 Fieldstone (00489)\""))
        p1 = next(i for i, x in enumerate(texts) if x.startswith("P1 - photo"))
        self.assertLess(g, p1)   # 10:09 recording before the 10:10 photo
        self.assertIn("Speaker B: Can you paint the hall too?", texts[g])

    def test_start_on_the_page_reads_the_recording(self):
        if not tr.HAVE_PIL:   # the room checks load Pillow; this test is about Granola
            for attr, value in [("room_problems", lambda room_file: []),
                                ("finish_room_file", lambda obj, *a: (obj["room_file"],
                                                                      qp._clean_questions(obj["questions"])))]:
                p = mock.patch.object(qp, attr, value)
                p.start()
                self.addCleanup(p.stop)
        fake = self.claude(tr.sse(tr.sample_answer()))
        code, run = tr.RunTests.post(self, "/scope/runs", {"job": "00489"})
        run = scope_runs.load_run(run["run_id"])
        self.assertEqual((run["status"], run["inputs"]["granola"]), ("questions", 1))
        self.assertIn("G1 - Granola recording", json.dumps(fake.bodies[0]))

    def test_no_key_no_granola(self):
        os.environ.pop("GRANOLA_API_KEY")
        visit = qp.gather_visit(tr.PROJECT_ID, "2026-10-19", job=JOB_OUT)
        self.assertEqual(visit["granola"], [])
        self.assertEqual(self.granola.calls, [])


if __name__ == "__main__":
    unittest.main()
