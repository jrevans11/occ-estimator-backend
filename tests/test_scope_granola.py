"""Tests for Site Scope Tool phase 9: Granola recordings (granola_api.py,
scope_granola.py, and the G sources in scope_quickpass.py).

Run from the repo root:  python3 -m unittest discover tests

Granola is faked at granola_api._request (path, params) with notes shaped like
the public API docs. CompanyCam, Claude and the store are faked as in
test_scope_runs. No keys or network needed.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import granola_api as ga  # noqa: E402
import scope_granola as sg  # noqa: E402
import scope_notes  # noqa: E402
import scope_push  # noqa: E402
import scope_quickpass as qp  # noqa: E402
import scope_runs  # noqa: E402
import scope_store  # noqa: E402
import test_scope_runs as tr  # noqa: E402

SECRET = "whsec_" + base64.b64encode(b"granola-test-secret-bytes").decode()
SUB = {"endpoint": "https://push.example.test/abc", "keys": {"p256dh": "p", "auth": "a"}}
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


def signed(body, secret=SECRET, ts=None, msg_id="msg_1"):
    ts = str(int(time.time()) if ts is None else ts)
    key = base64.b64decode(secret[len("whsec_"):])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.{body}".encode(), hashlib.sha256).digest()).decode()
    return {"webhook-id": msg_id, "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}"}


class Clock:
    def __init__(self, start=None):
        self.t = float(start if start is not None else time.time())

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


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

    def test_event_title_counts(self):
        self.assertEqual(ga.note_job_number(gnote(title="Site visit", event_title="100 Boxwood (00905)")), "00905")

    def test_looks_like_visit(self):
        self.assertTrue(ga.looks_like_visit("316 Stewart. St."))
        self.assertFalse(ga.looks_like_visit("Leadership meeting"))

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

    def test_named_recording_is_a_g_source_in_time_order(self):
        visit = qp.gather_visit(tr.PROJECT_ID, None, job=JOB_OUT, granola_note_ids=["not_visit1"])
        self.assertEqual(visit["visit_date"], "2026-10-19")
        self.assertEqual(len(visit["granola"]), 1)
        content = qp.build_content(visit, JOB_OUT)
        texts = [c["text"] for c in content if c["type"] == "text"]
        self.assertIn("1 Granola recording(s)", texts[0])
        g = next(i for i, x in enumerate(texts) if x.startswith("G1 - Granola recording \"1217 Fieldstone (00489)\""))
        p1 = next(i for i, x in enumerate(texts) if x.startswith("P1 - photo"))
        self.assertLess(g, p1)   # 10:09 recording before the 10:10 photo
        self.assertIn("Speaker B: Can you paint the hall too?", texts[g])

    def test_start_on_the_page_finds_the_days_recording(self):
        visit = qp.gather_visit(tr.PROJECT_ID, "2026-10-19", job=JOB_OUT)
        self.assertEqual([g["id"] for g in visit["granola"]], ["not_visit1"])

    def test_no_key_no_granola(self):
        os.environ.pop("GRANOLA_API_KEY")
        visit = qp.gather_visit(tr.PROJECT_ID, "2026-10-19", job=JOB_OUT)
        self.assertEqual(visit["granola"], [])
        self.assertEqual(self.granola.calls, [])


# ── The webhook and auto start ───────────────────────────────────────────────

class WebhookTests(tr.Base):
    def setUp(self):
        super().setUp()
        os.environ.update(GRANOLA_API_KEY="grn_x", GRANOLA_WEBHOOK_SECRET=SECRET)
        self.granola = FakeGranola([gnote(transcript=LINES),
                                    gnote("not_meet", "Repair Meeting", transcript=LINES),
                                    gnote("not_noid", "316 Stewart. St.", transcript=LINES),
                                    gnote("not_nojob", "99 Nowhere (00999)", transcript=LINES)])
        self.clock = Clock()
        self.pushed = []
        for target, attr, value in [(ga, "_request", self.granola), (sg, "_now", self.clock.now),
                                    (sg, "_sleep", self.clock.sleep),
                                    (scope_push, "_post", lambda payload: self.pushed.append(payload) or (200, {"ok": True}))]:
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        if not tr.HAVE_PIL:   # the room checks load Pillow; these tests are about Granola
            for attr, value in [("room_problems", lambda room_file: []),
                                ("finish_room_file", lambda obj, *a: (obj["room_file"],
                                                                      qp._clean_questions(obj["questions"])))]:
                p = mock.patch.object(qp, attr, value)
                p.start()
                self.addCleanup(p.stop)
        sg._active.clear()
        scope_runs._jobtread.clear()
        scope_push.remember(SUB)

    def hook(self, note_id, event="note.generated", headers=None):
        body = json.dumps({"event_id": "evt_1", "event_type": event, "note_id": note_id,
                           "occurred_at": "2026-10-20T02:40:00Z"})
        return scope_notes.handle_post("/scope/granola", body, headers or signed(body, ts=int(self.clock.t)), tr.fake_jobtread(), tr.ORG,
                                       start_thread=tr.run_inline)

    def test_signature(self):
        body = '{"a":1}'
        self.assertTrue(sg.verify_signature(body, signed(body)))
        self.assertFalse(sg.verify_signature(body + " ", signed(body)))
        self.assertFalse(sg.verify_signature(body, signed(body, ts=int(time.time()) - 900), now=time.time()))
        h = signed(body)
        h["webhook-signature"] = "v1,bm9wZQ== " + h["webhook-signature"]
        self.assertTrue(sg.verify_signature(body, h))
        self.assertFalse(sg.verify_signature(body, {}))

    def test_unsigned_and_unconfigured(self):
        code, _ = self.hook("not_visit1", headers={"webhook-id": "x", "webhook-timestamp": "1",
                                                   "webhook-signature": "v1,abc"})
        self.assertEqual(code, 401)
        os.environ.pop("GRANOLA_WEBHOOK_SECRET")
        code, _ = self.hook("not_visit1")
        self.assertEqual(code, 503)

    def test_no_api_key_asks_granola_to_retry(self):
        os.environ.pop("GRANOLA_API_KEY")
        code, body = self.hook("not_visit1")
        self.assertEqual(code, 503)
        self.assertIsNone(sg.load_record("not_visit1"))

    def test_visit_waits_for_photos_then_starts_a_run(self):
        fake = self.claude(tr.sse(tr.sample_answer()))
        t0 = self.clock.t
        code, body = self.hook("not_visit1")
        self.assertEqual((code, body["queued"]), (200, True))
        rec = sg.load_record("not_visit1")
        self.assertEqual(rec["status"], "started")
        self.assertGreaterEqual(self.clock.t - t0, sg.SETTLE_SECONDS)
        self.assertLess(self.clock.t - t0, sg.SETTLE_SECONDS + 2 * sg.POLL_SECONDS)
        self.assertEqual(rec["photo_count"], 4)
        run = scope_runs.load_run(rec["run_id"])
        self.assertEqual((run["status"], run["started_by"], run["granola_note_ids"]),
                         ("questions", "granola", ["not_visit1"]))
        self.assertEqual(run["visit_date"], "2026-10-19")
        self.assertEqual(run["push_sub"], SUB)
        sent = json.dumps(fake.bodies[0])
        self.assertIn("G1 - Granola recording", sent)
        self.assertEqual(self.pushed[-1]["title"].split(" - ")[0] if self.pushed else None,
                         scope_push.message(run, "questions")[0].split(" - ")[0])
        self.assertEqual(scope_store.get(sg.PENDING_KEY), [])
        # Granola sends it again (retry, edit): nothing new.
        code, body = self.hook("not_visit1", "note.edited")
        self.assertEqual((code, body["queued"]), (200, False))

    def test_meeting_is_left_alone(self):
        self.hook("not_meet")
        self.assertEqual(sg.load_record("not_meet")["status"], "ignored")
        self.assertEqual(self.pushed, [])

    def test_visit_without_job_number_alerts_then_rechecks_on_edit(self):
        self.hook("not_noid")
        self.assertEqual(sg.load_record("not_noid")["status"], "unmatched")
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("316 Stewart. St.", self.pushed[0]["title"])
        self.assertEqual(self.pushed[0]["url"], "/intake/scope")
        # Jason adds the job number to the title.
        self.granola.notes["not_noid"]["title"] = "316 Stewart. St. (00489)"
        self.claude(tr.sse(tr.sample_answer()))
        code, body = self.hook("not_noid", "note.edited")
        self.assertTrue(body["queued"])
        self.assertEqual(sg.load_record("not_noid")["status"], "started")

    def test_unknown_job_alerts(self):
        self.hook("not_nojob")
        rec = sg.load_record("not_nojob")
        self.assertEqual(rec["status"], "no_job")
        self.assertIn("00999", self.pushed[0]["title"])

    def test_wait_restarts_when_photos_keep_coming(self):
        photos = list(tr.PHOTOS)
        polls = []

        def count(project_id, day, note_at):
            polls.append(self.clock.t)
            if len(polls) == 4:
                photos.append(tr.photo("9010", "2026-10-20T02:40:00Z"))
            return len([p for p in photos if p["captured_at"].startswith("2026-10-20")])

        rec = {"note_id": "n1", "project_id": tr.PROJECT_ID, "visit_date": "2026-10-19", "received_at": self.clock.t}
        started = []
        with mock.patch.object(sg, "_photo_count", count), \
                mock.patch.object(sg, "_start", lambda r, s=None: started.append(self.clock.t)):
            sg._wait_and_start(rec)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0] - polls[3], sg.SETTLE_SECONDS)

    def test_wait_gives_up_after_an_hour(self):
        n = iter(range(1000))
        rec = {"note_id": "n1", "project_id": tr.PROJECT_ID, "visit_date": "2026-10-19",
               "wait_started": int(self.clock.t)}
        started = []
        with mock.patch.object(sg, "_photo_count", lambda *a: next(n)), \
                mock.patch.object(sg, "_start", lambda r, s=None: started.append(self.clock.t)):
            sg._wait_and_start(rec)
        self.assertGreaterEqual(started[0] - rec["wait_started"], sg.MAX_WAIT_SECONDS)

    def rec(self, received_at):
        return {"note_id": "not_visit1", "status": "waiting", "received_at": received_at, "title": "t",
                "job": JOB_OUT, "project_id": tr.PROJECT_ID, "visit_date": "2026-10-19"}

    def test_hand_started_run_after_the_recording_is_kept(self):
        self.claude(tr.sse(tr.sample_answer()))
        code, manual = tr.RunTests.post(self, "/scope/runs", {"job": "00489"})
        rec = self.rec(manual["created_at"] - 60)
        sg._start(rec, tr.run_inline)
        self.assertEqual((rec["status"], rec["run_id"]), ("already_running", manual["run_id"]))

    def test_older_waiting_questions_are_replaced(self):
        self.claude(tr.sse(tr.sample_answer()), tr.sse(tr.sample_answer()))
        code, manual = tr.RunTests.post(self, "/scope/runs", {"job": "00489"})
        rec = self.rec(manual["created_at"] + 60)
        sg._start(rec, tr.run_inline)
        self.assertEqual(rec["status"], "started")
        self.assertNotEqual(rec["run_id"], manual["run_id"])

    def test_busy_server_tries_again(self):
        self.claude(tr.sse(tr.sample_answer()))
        scope_runs._running.update({"a", "b"})
        waits = []

        def sleep(s):
            waits.append(s)
            scope_runs._running.clear()

        with mock.patch.object(sg, "_sleep", sleep):
            rec = self.rec(self.clock.t)
            sg._start(rec, tr.run_inline)
        self.assertEqual((rec["status"], waits), ("started", [sg.BUSY_WAIT_SECONDS]))

    def test_restart_picks_up_or_gives_up(self):
        old = self.rec(self.clock.t - sg.MISSED_AFTER_SECONDS - 10)
        old["note_id"] = "n_old"
        fresh = self.rec(self.clock.t - 600)
        for r in (old, fresh):
            sg._save_record(r)
            sg._set_pending(r["note_id"], True)
        self.clock.t += sg.STALE_AFTER_SECONDS + 5
        resumed = []
        sg.resume_pending(start_thread=lambda fn, *a: resumed.append(a[0]))
        self.assertEqual(resumed, ["not_visit1"])
        self.assertEqual(sg.load_record("n_old")["status"], "missed")
        self.assertIn("not read", self.pushed[-1]["title"])
        self.assertEqual(scope_store.get(sg.PENDING_KEY), ["not_visit1"])

    def test_keyed_test_route(self):
        code, body = scope_notes.handle_post("/scope/granola/test", json.dumps({"note_id": "not_visit1"}),
                                             {"X-Scope-Key": tr.KEY}, tr.fake_jobtread(), tr.ORG,
                                             start_thread=tr.run_inline)
        self.assertEqual(code, 200)
        self.assertEqual((body["match"]["status"], body["match"]["job_number"]), ("waiting", "00489"))
        self.assertIn("Kitchen first.", body["transcript_start"])
        code, _ = scope_notes.handle_post("/scope/granola/test", "{}", {}, tr.fake_jobtread(), tr.ORG)
        self.assertEqual(code, 401)


class PushMemoryTests(unittest.TestCase):
    def setUp(self):
        scope_store._reset_memory()

    def test_attach_remembers_latest(self):
        self.assertIsNone(scope_push.latest())
        run = {}
        self.assertTrue(scope_push.attach(run, SUB))
        self.assertEqual(scope_push.latest(), SUB)

    def test_granola_messages(self):
        title, body = scope_push.message({"granola_title": "316 Stewart. St."}, "granola_unmatched")
        self.assertIn("316 Stewart. St.", title)
        self.assertIn("(00905)", body)
        title, _ = scope_push.message({"job": JOB_OUT}, "granola_no_project")
        self.assertIn("1217 Fieldstone", title)


if __name__ == "__main__":
    unittest.main()
