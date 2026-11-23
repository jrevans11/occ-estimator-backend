"""Tests for the Ballpark guided intake: the question catalog
(ballpark_questions.py), the site-visit read (ballpark_read.py) and the
guided build (POST /ballpark with from_read), with JobTread and Claude faked.

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
import ballpark_questions as bq  # noqa: E402
import ballpark_read  # noqa: E402
import ballpark_scope  # noqa: E402
import scope_store  # noqa: E402
from test_ballpark_run import CONTEXT, KEY, FakeJobTread, sync_thread  # noqa: E402

READ_REPLY = {
    "scope": {
        "tier": "Premium", "whole_home": False, "punch_list_days": 2, "floor_plan_sf": 400,
        "rooms": [{"name": "Kitchen", "type": "kitchen", "tier": "Premium"},
                  {"name": "Primary bath", "type": "primary_bath", "tier": "Premium"},
                  {"name": "Den", "type": "other", "tier": "Premium"}],
        "tile_areas": [{"name": "Primary bath", "kind": "primary_bath", "tier": "Premium",
                        "prep": "walk_in_shower", "sf": 160},
                       {"name": "Backsplash", "kind": "backsplash", "tier": "Premium", "sf": None},
                       {"name": "Laundry floor", "kind": "small", "tier": "Premium", "sf": 40}],
        "countertops": [{"name": "Kitchen", "sf": 55, "tier": "Premium"},
                        {"name": "Primary bath vanity", "sf": 12, "tier": "Premium"}],
        "fixtures": [{"category": "appliance", "item": "refrigerator", "tier": "Premium", "quantity": 1,
                      "room": "Kitchen"},
                     {"category": "plumbing", "item": "toilet", "tier": "Premium", "quantity": 1,
                      "room": "Primary bath"}],
        "cabinetry": {"lowers_lf": 18, "uppers_lf": 12, "vanities_lf": 6},
        "exterior": {"windows": 0, "gutters": 0, "trim_lf": 0},
        "interior_doors": 3,
        "framing": True, "framed_sf": 200,
        "estimates": {"demo_days": {"value": 12, "reasoning": "kitchen plus primary bath"},
                      "framing_days": {"value": 6, "reasoning": "one wall moved"}},
    },
    "sources": {"floor_plan_sf": "scope summary video 0:30", "framed_sf": "estimated from one wall moved",
                "cabinetry.lowers_lf": "comment Nov 14"},
    "field_status": {"tier": "stated", "floor_plan_sf": "stated", "cabinetry.lowers_lf": "stated",
                     "cabinetry.uppers_lf": "estimated", "framed_sf": "estimated", "exterior.windows": "not_mentioned",
                     "rooms.0.tier": "stated", "rooms.1.tier": "conflict", "interior_doors": "stated"},
    "conflicts": ["Walkthrough said Quality for the bath; summary said Premium. Used Premium."],
    "open_questions": ["Is the den getting new flooring?"],
    "confidence": "medium",
    "summary": {"project_overview": "*The proposed renovation* includes a kitchen and primary bath.",
                "further_items": [], "exclusions": [], "tier_language": []},
    "cost_usd": 0.21,
}


def reply():
    return json.loads(json.dumps(READ_REPLY))


def page(pages, pid):
    return next(p for p in pages if p["id"] == pid)


def question(pages, path):
    return next(q for p in pages for q in p["questions"] if q["path"] == path)


class Pages(unittest.TestCase):
    def test_pages_follow_the_work_found(self):
        pages = bq.build_pages(reply())
        found = [p["id"] for p in pages if p["found"]]
        self.assertEqual(found[0], "job")
        self.assertIn("room-0", found)          # kitchen
        self.assertIn("room-1", found)          # primary bath
        self.assertNotIn("room-2", found)       # the den goes on "Other rooms"
        self.assertIn("rooms", found)
        self.assertNotIn("exterior", found)     # nothing exterior was read ...
        self.assertEqual(page(pages, "exterior")["to_answer"], 0)   # ... so it's only offered on review
        self.assertIn("structure", found)
        self.assertIn("mechanical", found)      # kitchen and bath have plumbing

    def test_exterior_page_when_exterior_work_was_read(self):
        r = reply()
        r["scope"]["exterior"]["siding_squares"] = 20
        self.assertTrue(page(bq.build_pages(r), "exterior")["found"])

    def test_cabinet_runs_not_asked_without_a_kitchen(self):
        r = reply()
        r["scope"]["rooms"] = [r["scope"]["rooms"][1]]
        r["scope"]["cabinetry"] = {}
        r["field_status"].pop("cabinetry.lowers_lf")
        self.assertEqual(question(bq.build_pages(r), "cabinetry.lowers_lf")["group"], "more")

    def test_groups(self):
        pages = bq.build_pages(reply())
        self.assertEqual(question(pages, "floor_plan_sf")["group"], "found")          # stated
        self.assertEqual(question(pages, "floor_plan_sf")["source"], "scope summary video 0:30")
        self.assertEqual(question(pages, "tier")["group"], "ask")                     # always asked
        self.assertEqual(question(pages, "cabinetry.uppers_lf")["group"], "ask")      # estimated
        self.assertEqual(question(pages, "rooms.1.tier")["group"], "ask")             # conflict
        self.assertEqual(question(pages, "rooms.0.tier")["group"], "found")
        demo = question(pages, "estimates.demo_days.value")
        self.assertEqual((demo["group"], demo["value"]), ("ask", 12))
        self.assertEqual(demo["source"], "kitchen plus primary bath")
        self.assertEqual(question(pages, "mechanical.full_rewire")["group"], "ask")   # core, not mentioned
        self.assertEqual(question(pages, "site.survey")["group"], "more")             # optional, not mentioned
        # A value Claude gave no status for is treated as a guess.
        self.assertEqual(question(pages, "cabinetry.vanities_lf")["status"], "estimated")

    def test_space_pages_carry_their_tile_and_countertop(self):
        pages = bq.build_pages(reply())
        bath = page(pages, "room-1")
        paths = [q["path"] for q in bath["questions"]]
        self.assertNotIn("tile_areas.0.sf", paths)          # bath tile is a flat allowance + install
        self.assertIn("tile_areas.0.prep", paths)           # shower or tub sets the install price
        self.assertIn("countertops.1.sf", paths)
        self.assertIn("toilet", bath["info"][0])
        kitchen = [q["path"] for q in page(pages, "room-0")["questions"]]
        self.assertNotIn("tile_areas.1.sf", kitchen)       # backsplash: flat allowance + install
        self.assertIn("countertops.0.sf", kitchen)
        self.assertIn("cabinetry.lowers_lf", kitchen)
        interior = [q["path"] for q in page(pages, "interior")["questions"]]
        self.assertIn("tile_areas.2.sf", interior)         # laundry floor belongs to no space

    def test_to_answer_counts(self):
        for p in bq.build_pages(reply()):
            if p["found"]:
                self.assertEqual(p["to_answer"], sum(1 for q in p["questions"] if q["group"] == "ask"))


class Confirm(unittest.TestCase):
    def test_answers_land_in_the_scope(self):
        scope, changes = bq.confirmed_scope(reply(), {
            "exterior.windows": "6", "estimates.demo_days.value": 16, "duration_weeks": 18,
            "mechanical.full_rewire": False, "tile_areas.0.prep": "tub_surround"})
        self.assertEqual(scope["exterior"]["windows"], 6)
        self.assertEqual(scope["estimates"]["demo_days"]["value"], 16)
        self.assertIn("guided intake", scope["estimates"]["demo_days"]["reasoning"])
        self.assertEqual(bp.duration_weeks(scope), 18)
        self.assertEqual(scope["tile_areas"][0]["prep"], "tub_surround")
        labels = {c["path"] for c in changes}
        self.assertIn("exterior.windows", labels)
        self.assertNotIn("mechanical.full_rewire", labels)   # blank -> no is not a change

    def test_untouched_scope_prices_the_same_as_the_read(self):
        scope, changes = bq.confirmed_scope(reply(), {})
        self.assertEqual(changes, [])
        a = bp.price_scope(scope, fx.template(), fx.TIER_PRICES)
        b = bp.price_scope(reply()["scope"], fx.template(), fx.TIER_PRICES)
        self.assertEqual(a["total_cost"], b["total_cost"])

    def test_blank_estimate_becomes_a_placeholder(self):
        scope, _ = bq.confirmed_scope(reply(), {"estimates.framing_days.value": ""})
        self.assertNotIn("framing_days", scope["estimates"])

    def test_refuses_unknown_paths_and_bad_values(self):
        with self.assertRaises(ValueError):
            bq.confirmed_scope(reply(), {"exterior.landscaping": True})       # not a question
        with self.assertRaises(ValueError):
            bq.confirmed_scope(reply(), {"exterior.windows": "six"})
        with self.assertRaises(ValueError):
            bq.confirmed_scope(reply(), {"tier": "Gold"})
        with self.assertRaises(ValueError):
            bq.confirmed_scope(reply(), {"floor_plan_sf": -5})

    def test_job_tier_change_carries_to_items_that_followed_it(self):
        scope, _ = bq.confirmed_scope(reply(), {"tier": "Luxury", "rooms.1.tier": "Premium"})
        self.assertEqual(scope["rooms"][0]["tier"], "Luxury")       # kitchen followed the job tier
        self.assertEqual(scope["rooms"][1]["tier"], "Premium")      # bath set by Tyler
        self.assertEqual(scope["rooms"][2]["tier"], "Luxury")
        self.assertEqual(scope["countertops"][0]["tier"], "Luxury")   # kitchen's countertop
        self.assertEqual(scope["fixtures"][0]["tier"], "Luxury")      # kitchen's fridge
        self.assertEqual(scope["fixtures"][1]["tier"], "Premium")     # bath's toilet stays with the bath
        self.assertEqual(scope["tile_areas"][0]["tier"], "Premium")
        self.assertEqual(scope["tile_areas"][2]["tier"], "Luxury")    # laundry, no space

    def test_room_tier_change_carries_to_its_items(self):
        scope, _ = bq.confirmed_scope(reply(), {"rooms.1.tier": "Luxury", "countertops.1.tier": "Premium"})
        self.assertEqual(scope["fixtures"][1]["tier"], "Luxury")
        self.assertEqual(scope["tile_areas"][0]["tier"], "Luxury")
        self.assertEqual(scope["countertops"][1]["tier"], "Premium")  # answered on its own
        self.assertEqual(scope["rooms"][0]["tier"], "Premium")

    def test_remove_a_space(self):
        scope, changes = bq.confirmed_scope(reply(), {}, remove_rooms=[1])
        self.assertEqual([r["name"] for r in scope["rooms"]], ["Kitchen", "Den"])
        self.assertEqual([t["name"] for t in scope["tile_areas"]], ["Backsplash", "Laundry floor"])
        self.assertEqual([c["name"] for c in scope["countertops"]], ["Kitchen"])
        self.assertEqual([f["item"] for f in scope["fixtures"]], ["refrigerator"])
        self.assertIn("Removed Primary bath", changes[0]["label"])


class GuidedFlow(unittest.TestCase):
    def setUp(self):
        os.environ["SCOPE_API_KEY"] = KEY
        scope_store._reset_memory()
        ballpark._running.clear()
        ballpark_read._running.clear()
        self.extract = mock.patch.object(ballpark_scope, "extract", return_value=reply())
        self.patches = [
            mock.patch.object(ballpark_inputs, "gather", return_value=CONTEXT),
            mock.patch.object(bp, "load_template", return_value=fx.template()),
            mock.patch.object(bp, "load_tier_prices", return_value=fx.TIER_PRICES),
            self.extract,
        ]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def post(self, jt, path, payload):
        return ballpark.handle_post(path, json.dumps(payload), {"X-Scope-Key": KEY}, jt, "ORG",
                                    start_thread=sync_thread)

    def read(self, jt, notes="framing labor about $8,000"):
        s, b = self.post(jt, "/ballpark/read", {"job": "00999", "notes": notes})
        self.assertEqual(s, 202)
        s, b = self.post(jt, "/ballpark/read/status", {"job": "00999"})
        self.assertEqual(s, 200)
        return b["read"]

    def test_read_saves_pages_and_writes_nothing(self):
        jt = FakeJobTread(budget_groups=2)
        rd = self.read(jt)
        self.assertEqual(rd["state"], "done")
        self.assertEqual(rd["pages"][0]["id"], "job")
        self.assertEqual(rd["read"]["missing_videos"][0]["video_id"], "V9")
        self.assertEqual(rd["notes"], "framing labor about $8,000")
        form = self.mocks[3].call_args[0][1]
        self.assertEqual(form, {"notes": "framing labor about $8,000"})
        for name in ("createDailyLog", "createCostGroup", "createCostItem", "createTask"):
            self.assertEqual(jt.mutations(name), [], name)

    def test_status_with_no_read(self):
        s, b = self.post(FakeJobTread(), "/ballpark/read/status", {"job": "00999"})
        self.assertEqual((s, b["read"]), (200, None))

    def test_guided_dry_run_prices_the_answers_without_claude(self):
        jt = FakeJobTread()
        rd = self.read(jt)
        self.extract.stop()
        with mock.patch.object(ballpark_scope, "extract", side_effect=AssertionError("no second Claude call")):
            s, b = self.post(jt, "/ballpark", {"job": "00999", "from_read": True, "dry_run": True,
                                               "read_started_at": rd["started_at"],
                                               "answers": {"estimates.demo_days.value": 20}})
        self.extract.start()
        self.assertEqual(s, 202, b)
        s, b = self.post(jt, "/ballpark/status", {"job": "00999"})
        run = b["run"]
        self.assertEqual(run["state"], "done", run.get("error"))
        self.assertTrue(run["from_read"])
        res = run["result"]
        self.assertEqual(res["claude_cost_usd"], 0)
        self.assertEqual(res["scope"]["estimates"]["demo_days"]["value"], 20)
        demo = [l for p in res["budget"]["phases"] for l in p["lines"] if l["key"] == "demo"]
        self.assertEqual(demo[0]["quantity"], 20)
        self.assertIn("Set on the guided intake: Demo: 12 -> 20", res["summary"])
        self.assertEqual(res["intake_changes"][0]["path"], "estimates.demo_days.value")

    def test_guided_live_run_writes_the_budget(self):
        jt = FakeJobTread()
        rd = self.read(jt)
        s, b = self.post(jt, "/ballpark", {"job": "00999", "from_read": True, "read_started_at": rd["started_at"],
                                           "answers": {}})
        self.assertEqual(s, 202, b)
        self.assertEqual(self.mocks[3].call_count, 1)         # only the read called Claude
        self.assertTrue(jt.mutations("createCostGroup"))
        self.assertEqual(jt.mutations("createTask")[-1]["name"], "Review Preliminary Ballpark budget")

    def test_guided_build_needs_a_finished_matching_read(self):
        jt = FakeJobTread()
        s, b = self.post(jt, "/ballpark", {"job": "00999", "from_read": True, "answers": {}, "dry_run": True})
        self.assertEqual(s, 409)
        self.assertIn("Read the site visit first", b["error"])
        self.read(jt)
        s, b = self.post(jt, "/ballpark", {"job": "00999", "from_read": True, "answers": {}, "dry_run": True,
                                           "read_started_at": "2020-01-01T00:00:00+00:00"})
        self.assertEqual(s, 409)
        self.assertIn("read again", b["error"])

    def test_bad_answer_is_refused_before_the_run(self):
        jt = FakeJobTread()
        rd = self.read(jt)
        s, b = self.post(jt, "/ballpark", {"job": "00999", "from_read": True, "dry_run": True,
                                           "read_started_at": rd["started_at"], "answers": {"exterior.windows": "x"}})
        self.assertEqual(s, 400)
        self.assertIn("needs a number", b["error"])
        s, b = self.post(jt, "/ballpark/status", {"job": "00999"})
        self.assertIsNone(b["run"])

    def test_one_read_per_job(self):
        ballpark_read._running.add("JOB1")
        s, b = self.post(FakeJobTread(), "/ballpark/read", {"job": "00999"})
        self.assertEqual(s, 409)

    def test_read_failure_is_saved(self):
        self.extract.stop()
        with mock.patch.object(ballpark_scope, "extract", side_effect=RuntimeError("Claude down")):
            rd = self.read(FakeJobTread())
        self.extract.start()
        self.assertEqual(rd["state"], "failed")
        self.assertIn("Claude down", rd["error"])
        self.assertNotIn("pages", rd)


class Paths(unittest.TestCase):
    def test_get_and_set(self):
        s = {"rooms": [{"tier": "Premium"}], "exterior": {}}
        self.assertEqual(bq.get_path(s, "rooms.0.tier"), "Premium")
        self.assertIsNone(bq.get_path(s, "rooms.3.tier"))
        bq.set_path(s, "exterior.door_types.french", 2)
        self.assertEqual(s["exterior"]["door_types"]["french"], 2)
        bq.set_path(s, "site.engineering", "letter")
        self.assertEqual(s["site"]["engineering"], "letter")
        with self.assertRaises(KeyError):
            bq.set_path(s, "rooms.5.tier", "Luxury")


class Prompt(unittest.TestCase):
    def test_prompt_asks_for_field_status(self):
        p = ballpark_scope.system_prompt()
        self.assertIn("field_status", p)
        self.assertIn('"not_mentioned"', p)

    def test_parse_reply_defaults_field_status(self):
        obj = ballpark_scope.parse_reply('{"scope": {}, "field_status": "oops"}')
        self.assertEqual(obj["field_status"], {})


if __name__ == "__main__":
    unittest.main()
