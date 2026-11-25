"""Tests for the PM pilot Granola pull (granola_pm_notes.py).

Run from the repo root:  python3 -m unittest discover tests

JobTread is faked at the jobtread_query layer with the real pilot jobs as they
stood on Nov 25, 2026 (Lexi Menna: 00030, 00033, 00161, 00384, 00450, 00572;
Megan Butz: 00017, 00118; Andrighetti 00113 has no PM set). Granola is faked at
the HTTP-GET layer in the Public API's shapes (List Notes / Get Note /
Get Transcript). The store runs in memory. No keys or network needed.
"""

import copy
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import granola_pm_notes as g  # noqa: E402
import scope_store  # noqa: E402

ORG = "22P9ppHePJKP"


def _job(id_, number, name, address, account, pms, status="Active"):
    return {"id": id_, "number": number, "name": name,
            "location": {"address": address, "account": {"name": account}},
            "customFieldValues": {"nodes": (
                [{"value": p, "customField": {"id": g.PM_FIELD_ID}} for p in pms]
                + ([{"value": status, "customField": {"id": g.RENO_STATUS_FIELD_ID}}] if status else []))}}


JOBS = [
    _job("J17", "00017", "Hicklin Renovation", "210 Michaux Dr, Greenville, SC 29605, USA",
         "Anna Louise and Hunter Hicklin", ["Ben Creasman", "Megan Butz"]),
    _job("J30", "00030", "Miller + Richmond Renovation", "500 Old Altamont Rd, Greenville, SC 29609, USA",
         "Matt Miller", ["Ben Creasman", "Lexi Menna"]),
    _job("J33", "00033", "Buck Restoration", "11 Blazing Star Trail\nLandrum, SC 29356",
         "Eddie Buck", ["Ben Creasman", "Lexi Menna"]),
    _job("J113", "00113", "Andrighetti Renovation", "1007 Parkins Mill Rd, Greenville, SC 29607, USA",
         "JJ Andrighetti", []),
    _job("J118", "00118", "Leyva Renovation", "106 Spring Forest Rd, Greenville, SC 29615, USA",
         "Lillian Leyva", ["Ben Creasman", "Megan Butz"]),
    _job("J161", "00161", "Camp Renovation", "9 Latour Way, Greer, SC 29650, USA", "Tommy Camp", ["Lexi Menna"]),
    _job("J384", "00384", "Harris Kitchen renovation", "22 Latour Way, Greer, SC 29650, USA",
         "Tre' Harris", ["Lexi Menna"]),
    _job("J450", "00450", "Falk Renovation", "46 Woodvale Ave, Greenville, SC 29605, USA",
         "Rachel Falk", ["Lexi Menna"]),
    _job("J572", "00572", "Kaplan Bath Renovation", "2 Smilax Ct, Greenville, SC 29617, USA",
         "James Kaplan", ["Lexi Menna"]),
    _job("J800", "00800", "Job 00800", "318 E Faris Rd, Greenville, SC", "Lea Rohrbaugh", [], status=None),
]


class FakeJobTread:
    def __init__(self):
        self.logs, self.tasks, self.calls = [], {}, []
        self._n = 0

    def __call__(self, query):
        self.calls.append(copy.deepcopy(query))
        root = next(iter(query))
        args = query[root].get("$", {})
        if root == "organization":
            if "jobs" in query[root]:
                where = query[root]["jobs"]["$"].get("where")
                if where and where[0] == "number":
                    nodes = [{"id": j["id"], "number": j["number"], "name": j["name"]}
                             for j in JOBS if j["number"] == where[1]]
                    return {"organization": {"jobs": {"nodes": nodes}}}
                return {"organization": {"jobs": {"nodes": copy.deepcopy(JOBS), "nextPage": None}}}
            if "dailyLogs" in query[root]:
                pattern = query[root]["dailyLogs"]["$"]["where"][2].strip("%")
                return {"organization": {"dailyLogs": {"count": sum(pattern in l["notes"] for l in self.logs)}}}
        if root == "createDailyLog":
            self._n += 1
            self.logs.append(dict(args, id=f"DL{self._n}"))
            return {"createDailyLog": {"createdDailyLog": {"id": f"DL{self._n}"}}}
        if root == "createTask":
            self._n += 1
            tid = f"T{self._n}"
            self.tasks[tid] = dict(args, id=tid, progress=None)
            return {"createTask": {"createdTask": {"id": tid}}}
        if root == "updateTask":
            self.tasks[args["id"]].update({k: v for k, v in args.items() if k != "id"})
            return {"updateTask": {}}
        if root == "task":
            t = self.tasks.get(args["id"])
            return {"task": None if t is None else {"id": t["id"], "name": t["name"], "progress": t["progress"]}}
        raise AssertionError(f"unexpected JobTread query {root}")

    def mutation_roots(self):
        return [next(iter(c)) for c in self.calls if next(iter(c)).startswith(("create", "update", "delete", "send"))]


def _t(minute, second=0):
    return (datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)
            + timedelta(minutes=minute, seconds=second)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_note(note_id, title, lines, summary="Painters back Monday. Gutter color still open.",
              created=None, owner="lexi@example.test"):
    return {
        "id": note_id, "object": "note", "title": title,
        "owner": {"name": "Owner", "email": owner},
        "created_at": created or _t(0), "updated_at": created or _t(30), "deleted_at": None,
        "web_url": f"https://notes.granola.ai/d/{note_id}",
        "calendar_event": None, "attachments": [], "attendees": [], "folder_membership": [],
        "summary_text": summary, "summary_markdown": None,
        "private_notes_text": "", "private_notes_markdown": None,
        "transcript": [{"speaker": {"source": "microphone"}, "text": text,
                        "start_time": _t(0, sec), "end_time": _t(0, sec + 4)} for sec, text in lines],
    }


class FakeGranola:
    def __init__(self):
        self.notes = {}     # key -> {note_id: note}
        self.bad_keys = set()
        self.too_large = set()

    def add(self, key, note):
        self.notes.setdefault(key, {})[note["id"]] = note

    def __call__(self, key, path, params=None):
        if key in self.bad_keys:
            raise g.GranolaAuthError("401")
        notes = self.notes.get(key, {})
        if path == "/notes":
            rows = [{k: n[k] for k in ("id", "object", "title", "owner", "created_at", "updated_at", "deleted_at")}
                    for n in notes.values()]
            return {"notes": sorted(rows, key=lambda r: r["created_at"], reverse=True), "hasMore": False, "cursor": None}
        parts = path.strip("/").split("/")
        note = copy.deepcopy(notes[parts[1]])
        if len(parts) == 3:  # /notes/{id}/transcript
            return {"transcript": note["transcript"], "hasMore": False, "cursor": None}
        if (params or {}).get("include") == "transcript":
            if parts[1] in self.too_large:
                raise g.TranscriptTooLarge("TRANSCRIPT_TOO_LARGE")
            return note
        note.pop("transcript")
        return note


NOW = datetime(2026, 12, 1, 4, 30, tzinfo=timezone.utc)   # Nov 30, 11:30 PM Eastern


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "GRANOLA_KEY_LEXI": "grn_lexi", "GRANOLA_KEY_MEGAN": "grn_megan",
            "UPSTASH_REDIS_REST_URL": "", "UPSTASH_REDIS_REST_TOKEN": "",
            "KV_REST_API_URL": "", "KV_REST_API_TOKEN": "", "PM_NOTES_START": "2026-11-30",
        })
        self.env.start()
        os.environ.pop("GRANOLA_KEY_LEXI_EXPIRES", None)
        os.environ.pop("GRANOLA_KEY_MEGAN_EXPIRES", None)
        os.environ.pop("PM_NOTES_RUN_KEY", None)
        scope_store._reset_memory()
        self.jt, self.gr = FakeJobTread(), FakeGranola()

    def tearDown(self):
        self.env.stop()

    def run_once(self, now=NOW):
        return g.run(self.jt, ORG, now=now, get=self.gr)


class MatchingTests(Base):
    def setUp(self):
        super().setUp()
        self.lexi = g.pm_active_jobs(g.load_open_jobs(self.jt, ORG), "Lexi Menna")

    def m(self, title, first=""):
        return g.match_note(title, first, self.lexi, "Lexi Menna")

    def test_pm_jobs_come_from_pm_field_and_active_status(self):
        self.assertEqual(sorted(j["number"] for j in self.lexi),
                         ["00030", "00033", "00161", "00384", "00450", "00572"])
        megan = g.pm_active_jobs(g.load_open_jobs(self.jt, ORG), "Megan Butz")
        self.assertEqual(sorted(j["number"] for j in megan), ["00017", "00118"])

    def test_client_name_in_title(self):
        self.assertEqual(self.m("Falk progress visit")["job"]["number"], "00450")

    def test_generic_title_falls_back_to_first_minute(self):
        got = self.m("New note", "Okay we're at the Falks, painters are back Monday")
        self.assertEqual(got["job"]["number"], "00450")
        self.assertIn("first minute", got["how"])

    def test_shared_street_needs_job_number(self):
        self.assertIsNone(self.m("Latour Way walk")["job"])
        self.assertEqual(self.m("Latour Way walk", "this is job 384")["job"]["number"], "00384")
        self.assertEqual(self.m("Latour walk 00161")["job"]["number"], "00161")

    def test_name_beats_bare_shared_street(self):
        self.assertEqual(self.m("Camp walk on Latour")["job"]["number"], "00161")

    def test_house_number_and_street(self):
        self.assertEqual(self.m("New note", "pulling up to 22 Latour now")["job"]["number"], "00384")

    def test_common_word_name_not_matched_from_transcript(self):
        self.assertIsNone(self.m("New note", "we set up camp in the kitchen and passed the buck")["job"])
        self.assertEqual(self.m("Camp check-in")["job"]["number"], "00161")

    def test_two_named_jobs_without_number_is_needs_call(self):
        got = self.m("Falk and Kaplan tile pickup")
        self.assertIsNone(got["job"])
        self.assertIn("more than one", got["how"])

    def test_number_for_someone_elses_job(self):
        got = self.m("Check on 00017")
        self.assertIsNone(got["job"])
        self.assertIn("isn't one of Lexi's", got["how"])

    def test_team_meeting_is_needs_call(self):
        self.assertIsNone(self.m("Weekly PM sync", "alright everyone, schedule review")["job"])

    def test_first_minute_only(self):
        tr = [{"text": "Morning, quick update.", "start_time": _t(0), "speaker": {}},
              {"text": "Over at Woodvale later.", "start_time": _t(3), "speaker": {}}]
        self.assertNotIn("Woodvale", g.first_minute_text(tr))


class RunTests(Base):
    def test_matched_note_files_internal_daily_log(self):
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa1", "Falk progress visit",
                                          [(0, "Painters back Monday."), (20, "Sconces are in.")]))
        report = self.run_once()
        self.assertEqual(len(report["filed"]), 1, report)
        self.assertEqual(len(self.jt.logs), 1)
        log = self.jt.logs[0]
        self.assertEqual(log["jobId"], "J450")
        self.assertEqual(log["date"], "2026-11-30")
        self.assertIs(log["notify"], False)
        self.assertNotIn("assignees", log)
        self.assertTrue(log["notes"].startswith("[PM-NOTE]"))
        self.assertIn("Granola note id: not_aaaaaaaaaaaaa1", log["notes"])
        self.assertIn("SUMMARY\nPainters back Monday", log["notes"])
        self.assertIn("[0:00] Lexi: Painters back Monday. Sconces are in.", log["notes"])

    def test_only_allowed_mutations_ever_run(self):
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa1", "Falk progress visit", [(0, "hi")]))
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa2", "Weekly PM sync", [(0, "hi")]))
        self.run_once()
        self.assertTrue(set(self.jt.mutation_roots()) <= g.ALLOWED_MUTATIONS)
        with self.assertRaises(g.ForbiddenMutation):
            g._mutate(self.jt, {"createComment": {"$": {"message": "x"}}})
        with self.assertRaises(g.ForbiddenMutation):
            g._mutate(self.jt, {"sendDocument": {"$": {}}})

    def test_second_run_does_not_duplicate(self):
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa1", "Falk progress visit", [(0, "hi")]))
        self.run_once()
        self.run_once(NOW + timedelta(days=1))
        self.assertEqual(len(self.jt.logs), 1)

    def test_lost_store_still_does_not_duplicate(self):
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa1", "Falk progress visit", [(0, "hi")]))
        self.run_once()
        scope_store._reset_memory()
        self.run_once(NOW + timedelta(days=1))
        self.assertEqual(len(self.jt.logs), 1)

    def test_notes_before_start_are_ignored(self):
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa1", "Falk visit", [(0, "hi")],
                                          created="2026-11-20T15:00:00Z"))
        self.run_once()
        self.assertEqual(self.jt.logs, [])

    def test_unmatched_note_goes_to_jason_then_files_from_todo(self):
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa2", "Tile pickup", [(0, "grabbing grout")]))
        report = self.run_once()
        self.assertEqual(len(report["needs_call"]), 1)
        self.assertEqual(self.jt.logs, [])
        (tid, task), = self.jt.tasks.items()
        self.assertEqual(task["targetType"], "organization")
        self.assertEqual(task["assignees"], [{"membershipId": g.JASON_MEMBERSHIP_ID}])
        self.assertTrue(task["isToDo"])
        self.assertIn("Granola note needs a job: Tile pickup (Lexi, Nov 30)", task["name"])
        self.assertIn("00450 Falk Renovation", task["description"])

        # Still open: nothing happens, no second to-do.
        self.run_once(NOW + timedelta(days=1))
        self.assertEqual(len(self.jt.tasks), 1)
        self.assertEqual(self.jt.logs, [])

        # Jason adds the job number and checks it off.
        self.jt.tasks[tid].update(name=task["name"] + " 00572", progress=1)
        report = self.run_once(NOW + timedelta(days=2))
        self.assertEqual(len(self.jt.logs), 1, report)
        self.assertEqual(self.jt.logs[0]["jobId"], "J572")
        self.assertIn("Jason picked the job", self.jt.logs[0]["notes"])
        self.run_once(NOW + timedelta(days=3))
        self.assertEqual(len(self.jt.logs), 1)

    def test_checked_off_without_number_is_left_alone(self):
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa2", "Team meeting", [(0, "agenda")]))
        self.run_once()
        (tid, _), = self.jt.tasks.items()
        self.jt.tasks[tid]["progress"] = 1
        report = self.run_once(NOW + timedelta(days=1))
        self.assertEqual(self.jt.logs, [])
        self.assertEqual(len(report["skipped"]), 1)

    def test_renamed_note_files_itself_and_closes_todo(self):
        note = make_note("not_aaaaaaaaaaaaa2", "New note", [(0, "quick update")])
        self.gr.add("grn_lexi", note)
        self.run_once()
        (tid, _), = self.jt.tasks.items()
        note["title"] = "Kaplan bath check"
        note["updated_at"] = "2026-12-01T14:00:00Z"
        self.run_once(NOW + timedelta(days=1))
        self.assertEqual([l["jobId"] for l in self.jt.logs], ["J572"])
        self.assertEqual(self.jt.tasks[tid]["progress"], 1)

    def test_megan_uses_her_own_key_and_jobs(self):
        self.gr.add("grn_megan", make_note("not_bbbbbbbbbbbbb1", "Hicklin client meeting",
                                           [(0, "Anna Louise wants the gutter color")], owner="megan@example.test"))
        self.run_once()
        self.assertEqual([l["jobId"] for l in self.jt.logs], ["J17"])
        self.assertIn("from Megan Butz", self.jt.logs[0]["notes"])

    def test_long_transcript_splits_into_parts(self):
        lines = [(i, f"Line {i} " + "drywall mud and tape details " * 12) for i in range(0, 1500, 2)]
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa3", "Falk walkthrough", lines))
        self.run_once()
        self.assertGreater(len(self.jt.logs), 1)
        self.assertTrue(all(len(l["notes"]) <= g.DAILY_LOG_LIMIT for l in self.jt.logs))
        self.assertTrue(all(l["notes"].startswith("[PM-NOTE]") for l in self.jt.logs))
        self.assertEqual(sum("Granola note id: not_aaaaaaaaaaaaa3" in l["notes"] for l in self.jt.logs), 1)
        self.assertIn("transcript part 2 of", self.jt.logs[1]["notes"])

    def test_transcript_too_large_uses_paged_endpoint(self):
        self.gr.add("grn_lexi", make_note("not_aaaaaaaaaaaaa4", "Falk visit", [(0, "Countertop is in.")]))
        self.gr.too_large.add("not_aaaaaaaaaaaaa4")
        self.run_once()
        self.assertIn("Countertop is in.", self.jt.logs[0]["notes"])


class KeyTests(Base):
    def todo_names(self):
        return [t["name"] for t in self.jt.tasks.values()]

    def test_expiry_warning_once_per_threshold(self):
        os.environ["GRANOLA_KEY_LEXI_EXPIRES"] = "2026-12-10"   # 9 days out
        self.run_once()
        self.run_once(NOW + timedelta(days=1))
        names = self.todo_names()
        self.assertEqual(len(names), 1)
        self.assertIn("Lexi's API key expires Dec 10", names[0])
        self.run_once(datetime(2026, 12, 8, 4, 30, tzinfo=timezone.utc))   # 3 days out
        self.assertEqual(len(self.todo_names()), 2)

    def test_far_expiry_no_warning(self):
        os.environ["GRANOLA_KEY_LEXI_EXPIRES"] = "2027-03-01"
        self.run_once()
        self.assertEqual(self.todo_names(), [])

    def test_rejected_key_alerts_once(self):
        self.gr.bad_keys.add("grn_lexi")
        self.run_once()
        self.run_once(NOW + timedelta(days=1))
        names = self.todo_names()
        self.assertEqual(len(names), 1)
        self.assertIn("stopped working", names[0])
        self.assertNotIn("grn_lexi", self.jt.tasks[next(iter(self.jt.tasks))]["description"])

    def test_missing_key_alerts_once_after_start(self):
        del os.environ["GRANOLA_KEY_MEGAN"]
        self.run_once(datetime(2026, 11, 27, 4, 0, tzinfo=timezone.utc))   # before Nov 30
        self.assertEqual(self.todo_names(), [])
        self.run_once()
        self.run_once(NOW + timedelta(days=1))
        self.assertEqual(len(self.todo_names()), 1)
        self.assertIn("Megan's API key isn't in Render", self.todo_names()[0])


class EndpointTests(Base):
    def test_run_answers_202_and_status_has_no_content(self):
        started = []
        status, body = g.handle_run_request({}, self.jt, ORG, start_thread=lambda fn: started.append(fn))
        self.assertEqual(status, 202)
        self.assertEqual(len(started), 1)
        g._run_lock.release()
        status, body = g.handle_status_request({})
        self.assertEqual(status, 200)
        self.assertTrue(body["keys"]["Lexi Menna"]["key_set"])
        self.assertNotIn("grn_lexi", str(body))

    def test_optional_run_key(self):
        os.environ["PM_NOTES_RUN_KEY"] = "s3cret"
        self.assertEqual(g.handle_run_request({}, self.jt, ORG, start_thread=lambda fn: None)[0], 403)
        status, _ = g.handle_run_request({"key": ["s3cret"]}, self.jt, ORG, start_thread=lambda fn: None)
        self.assertEqual(status, 202)
        g._run_lock.release()


if __name__ == "__main__":
    unittest.main()
