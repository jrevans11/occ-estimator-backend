"""Tests for the Preliminary Ballpark Tool run (ballpark.py) and the scope
merge (ballpark_scope.py), with JobTread and Claude faked.

Run from the repo root:  python3 -m unittest discover tests
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ballpark_fixtures as fx  # noqa: E402
from ballpark_fixtures import bp  # noqa: E402
import ballpark  # noqa: E402
import ballpark_inputs  # noqa: E402
import ballpark_scope  # noqa: E402
import scope_store  # noqa: E402

KEY = "test-key"
JOB = {"id": "JOB1", "number": "00999", "name": "Test Renovation", "companycamId": "", "parameters": None,
       "location": {"id": "LOC1", "address": "1 Test St, Greenville, SC"},
       "customFieldValues": {"nodes": [{"value": "Renovation", "customField": {"id": "22PAU2yp8N2U", "name": "Job Type"}}]}}


class FakeJobTread:
    def __init__(self, budget_groups=0, job=None, fail_on=None):
        self.budget_groups = budget_groups
        self.job = job or JOB
        self.calls = []
        self.fail_on = fail_on
        self.n = 0

    def __call__(self, q):
        self.calls.append(q)
        key = next(iter(q))
        if self.fail_on and key == self.fail_on:
            raise RuntimeError("JobTread said no")
        if key == "job":
            if "costGroups" in q["job"]:
                nodes = [{"id": f"G{i}", "document": None} for i in range(self.budget_groups)]
                nodes.append({"id": "D1", "document": {"id": "DOC"}})
                return {"job": {"costGroups": {"nodes": nodes, "nextPage": None}}}
            return {"job": self.job}
        if key == "organization":
            return {"organization": {"jobs": {"nodes": [self.job]}}}
        if key == "createCostGroup":
            self.n += 1
            return {"createCostGroup": {"createdCostGroup": {"id": f"NEW{self.n}"}}}
        if key == "createCostItem":
            return {"createCostItem": {"createdCostItem": {"id": "I"}}}
        return {}

    def mutations(self, name):
        return [q[name]["$"] for q in self.calls if name in q]


REPLY = {
    "scope": {"duration_weeks": 15, "punch_list_days": 2, "floor_plan_sf": 300,
              "rooms": [{"name": "Kitchen", "type": "kitchen", "tier": "Premium", "sf": 200}],
              "estimates": {"demo_days": {"value": 3, "reasoning": "one kitchen"}}},
    "sources": {"floor_plan_sf": "tool page"}, "conflicts": ["walkthrough said 2 ovens; summary said 1"],
    "open_questions": [], "confidence": "medium",
    "summary": {"project_overview": "*The proposed renovation* includes a kitchen remodel.",
                "further_items": ["Projected permitting and contractor fees"], "exclusions": ["landscaping"],
                "tier_language": ["Appliances: Premium - Cafe, JennAir, Thermador"]},
}
CONTEXT = {"job": {}, "companycam": {"transcripts": [], "missing_transcripts": [
    {"video_id": "V9", "captured_at": "2026-10-20", "reason": "no transcript"}], "photos": []},
    "comments": [], "daily_logs": [], "warnings": []}


def sync_thread(fn, *args):
    fn(*args)


class RunTests(unittest.TestCase):
    def setUp(self):
        os.environ["SCOPE_API_KEY"] = KEY
        scope_store._reset_memory()
        ballpark._running.clear()
        self.patches = [
            mock.patch.object(ballpark_inputs, "gather", return_value=CONTEXT),
            mock.patch.object(ballpark_scope, "extract", return_value=json.loads(json.dumps(REPLY))),
            mock.patch.object(bp, "load_template", return_value=fx.template()),
            mock.patch.object(bp, "load_tier_prices", return_value=fx.TIER_PRICES),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def post(self, jt, path="/ballpark", payload=None, key=KEY):
        return ballpark.handle_post(path, json.dumps(payload or {"job": "00999", "form": {}}),
                                    {"X-Scope-Key": key}, jt, "ORG", start_thread=sync_thread)

    def test_needs_key(self):
        status, body = self.post(FakeJobTread(), key="wrong")
        self.assertEqual(status, 401)

    def test_refuses_job_with_budget(self):
        jt = FakeJobTread(budget_groups=1)
        status, body = self.post(jt)
        self.assertEqual(status, 409)
        self.assertIn("already has a budget", body["error"])
        self.assertEqual(jt.mutations("createCostGroup"), [])

    def test_estimate_document_groups_dont_count(self):
        self.assertEqual(ballpark_inputs.budget_group_count(FakeJobTread(), "JOB1"), 0)

    def test_refuses_wrong_job_type(self):
        job = json.loads(json.dumps(JOB))
        job["customFieldValues"]["nodes"][0]["value"] = "Closing Repair"
        status, body = self.post(FakeJobTread(job=job))
        self.assertEqual(status, 409)
        self.assertIn("Renovation and Addition", body["error"])

    def test_full_run_writes_budget_log_and_todo(self):
        jt = FakeJobTread()
        status, body = self.post(jt)
        self.assertEqual(status, 202)
        logs = [m["notes"] for m in jt.mutations("createDailyLog")]
        self.assertTrue(logs[0].startswith("[BALLPARK] started"))
        self.assertTrue(logs[-1].startswith("[BALLPARK] Preliminary Ballpark"))
        self.assertIn("Price range:", logs[-1])
        self.assertIn("contractor margin of 21.9%", logs[-1])
        self.assertIn("V9", logs[-1])                       # missing transcript reported
        self.assertIn("Conflict: walkthrough", logs[-1])
        groups = [m["name"] for m in jt.mutations("createCostGroup")]
        self.assertEqual(groups[0], "1000 Preparation and Management Fees")
        self.assertEqual(groups[-1], "6200 Completion")
        notes = [m for m in jt.mutations("createCostItem") if m["name"] == bp.NOTES_ITEM_NAME]
        self.assertEqual(len(notes), len(groups))
        for n in notes:
            self.assertFalse(n["showDescription"])
            self.assertEqual(n["costCodeId"], bp.COST_CODE_INTERNAL_USE)
            self.assertEqual(n["unitCost"], 0)
        items = [m for m in jt.mutations("createCostItem") if m["name"] != bp.NOTES_ITEM_NAME]
        for i in items:
            self.assertAlmostEqual(i["unitPrice"], round(i["unitCost"] * 1.28, 2))
        self.assertTrue(any(i["name"].endswith("(PRICING TBD)") for i in items))   # framing/trim days missing
        todos = jt.mutations("createTask")
        self.assertEqual(todos[-1]["name"], "Review Preliminary Ballpark budget")
        self.assertEqual(todos[-1]["assignees"], [{"membershipId": ballpark.TYLER_MEMBERSHIP_ID}])
        s, b = self.post(jt, "/ballpark/status", {"job": "00999"})
        self.assertEqual(b["run"]["state"], "done")

    def test_budget_appearing_mid_run_stops_the_write(self):
        jt = FakeJobTread()
        calls = {"n": 0}

        def count(q, job_id):
            calls["n"] += 1
            return 0 if calls["n"] == 1 else 2
        with mock.patch.object(ballpark_inputs, "budget_group_count", side_effect=count):
            self.post(jt)
        self.assertEqual(jt.mutations("createCostGroup"), [])
        logs = [m["notes"] for m in jt.mutations("createDailyLog")]
        self.assertTrue(logs[-1].startswith("[BALLPARK-FAILED]"))
        self.assertEqual(jt.mutations("createTask")[-1]["name"], "Preliminary Ballpark run failed")

    def test_dry_run_writes_nothing(self):
        jt = FakeJobTread(budget_groups=3)       # a budget doesn't block a dry run
        status, body = self.post(jt, payload={"job": "00999", "form": {}, "dry_run": True})
        self.assertEqual(status, 202)
        for name in ("createDailyLog", "createCostGroup", "createCostItem", "createTask"):
            self.assertEqual(jt.mutations(name), [], name)
        s, b = self.post(jt, "/ballpark/status", {"job": "00999"})
        res = b["run"]["result"]
        self.assertIn("[BALLPARK] Preliminary Ballpark", res["summary"])
        self.assertGreater(res["total_price"], res["total_cost"])

    def test_one_run_per_job(self):
        ballpark._running.add("JOB1")
        status, body = self.post(FakeJobTread())
        self.assertEqual(status, 409)
        self.assertIn("already going", body["error"])


class MergeForm(unittest.TestCase):
    def test_form_values_win(self):
        scope = {"floor_plan_sf": 900, "exterior": {"windows": 4, "gutters": True}, "rooms": []}
        out = ballpark_scope.merge_form(scope, {"floor_plan_sf": 1200, "exterior": {"windows": 10, "gutters": None}})
        self.assertEqual(out["floor_plan_sf"], 1200)
        self.assertEqual(out["exterior"]["windows"], 10)
        self.assertTrue(out["exterior"]["gutters"])          # blank form value leaves Claude's

    def test_weeks_come_from_the_form_or_the_default(self):
        out = ballpark_scope.merge_form({"duration_weeks": 50, "whole_home": True}, {"duration_range": "15-20"})
        self.assertEqual(bp.duration_weeks(out), 17.5)
        out = ballpark_scope.merge_form({"duration_weeks": 50, "whole_home": True}, {})
        self.assertEqual(bp.duration_weeks(out), 32.5)

    def test_space_tiers_and_room_count(self):
        scope = {"rooms": [{"name": "Kitchen", "type": "kitchen", "tier": "Quality"},
                           {"name": "Den", "type": "other"}, {"name": "Hall", "type": "other"},
                           {"name": "Office", "type": "other"}]}
        form = {"spaces": [{"type": "kitchen", "tier": "Luxury"}, {"type": "powder", "tier": "Premium"}],
                "room_count": 4}
        out = ballpark_scope.merge_form(scope, form)
        kinds = [r["type"] for r in out["rooms"]]
        self.assertEqual(len(out["rooms"]), 4)
        self.assertIn("powder", kinds)
        self.assertEqual(next(r for r in out["rooms"] if r["type"] == "kitchen")["tier"], "Luxury")
        self.assertTrue(all(r.get("tier") for r in out["rooms"]))

    def test_parse_reply(self):
        reply = ballpark_scope.parse_reply('Here you go:\n{"scope": {"rooms": []}, "summary": {}}')
        self.assertEqual(reply["scope"], {"rooms": []})
        with self.assertRaises(ballpark_scope.ScopeError):
            ballpark_scope.parse_reply("no json")

    def test_prompt_assembles(self):
        p = ballpark_scope.system_prompt()
        self.assertIn("MALM EXAMPLE", p)
        self.assertIn('"estimates"', p)
        self.assertNotIn("{schema}", p)


if __name__ == "__main__":
    unittest.main()
