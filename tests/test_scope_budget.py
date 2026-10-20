"""Tests for Site Scope Tool phase 6: the JobTread budget (scope_budget.py) and
its place at the end of the build (scope_build.py).

Run from the repo root:  python3 -m unittest discover tests

A fake JobTread records every group and item written (one mutation per call,
like the real Pave API) and answers Home Depot catalog searches. Claude is
faked at its streaming layer (tests/test_scope_runs.py). No keys or network.
"""

import io
import json
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import estimate_v2_draft as v2  # noqa: E402
import scope_budget as bud  # noqa: E402
import scope_build as sb  # noqa: E402
import scope_runs  # noqa: E402
import test_scope_build as tb  # noqa: E402
import test_scope_runs as tr  # noqa: E402


class FakeJobTread:
    """createCostGroup / createCostItem / the job's group names / catalog search.
    fail_items: {item name: exception} raised the first time that item is written."""

    def __init__(self, products=None, existing=(), fail_items=None, fail_selection=False):
        self.groups, self.items, self.calls = [], [], []
        self.products = products or {}
        self.existing = list(existing)
        self.fail_items = dict(fail_items or {})
        self.fail_selection = fail_selection

    def __call__(self, query):
        self.calls.append(query)
        if "homeDepotProducts" in query:
            q = query["homeDepotProducts"]["$"]["searchQuery"].lower()
            hits = [p for key, p in self.products.items() if key in q]
            return {"homeDepotProducts": {"nodes": hits, "nextPage": None}}
        if "createCostGroup" in query:
            args = query["createCostGroup"]["$"]
            if self.fail_selection and "maxSelectionsAllowed" in args:
                raise urllib.error.HTTPError("https://api.jobtread.com/pave", 400, "bad", {}, io.BytesIO(b"x"))
            gid = f"G{len(self.groups) + 1}"
            self.groups.append(dict(args, id=gid))
            return {"createCostGroup": {"createdCostGroup": {"id": gid}}}
        if "createCostItem" in query:
            args = query["createCostItem"]["$"]
            err = self.fail_items.pop(args["name"], None)
            if err:
                raise err
            iid = f"I{len(self.items) + 1}"
            self.items.append(dict(args, id=iid))
            return {"createCostItem": {"createdCostItem": {"id": iid}}}
        if "job" in query and "costGroups" in query["job"]:
            return {"job": {"costGroups": {"nodes": [{"name": n} for n in self.existing]}}}
        return tr.fake_jobtread()(query)

    def group(self, name):
        return next(g for g in self.groups if g["name"] == name)

    def items_in(self, name):
        gid = self.group(name)["id"]
        return [i for i in self.items if i["costGroupId"] == gid]


SCOPE_ITEMS = [
    {"id": "S1", "trade": "Carpentry", "area": "Office", "work": "Install a new door",
     "quantity": {"value": 1, "unit": "ea", "src": "confirmed"}, "sub": None, "licensed_trade": False},
    {"id": "S2", "trade": "Electrical", "area": "Office", "work": "Add two receptacles",
     "quantity": {"value": 2, "unit": "ea", "src": "dictated"}, "sub": "Redland Electric",
     "licensed_trade": "electrical"},
    {"id": "S3", "trade": "Structural", "area": "Office", "work": "Install a steel lintel",
     "quantity": {"value": 1, "unit": "ea", "src": "assumed"}, "sub": None, "licensed_trade": "structural"},
]


def budget_answer(**over):
    groups = [
        {"name": "Trim and Carpentry", "cost_code": "Trim & Millwork", "scope_item_ids": ["S1"],
         "scope_lines": ["Install a prehung door in the new opening", "Install **casing** on both sides"],
         "notes": ["Pricing assumes a standard 6-panel hollow-core door — the client picks the style."],
         "internal_notes": ["Door size assumed 2'-8\"."],
         "labor": [{"task": "Carpentry - door hang and casing", "hours": 6}],
         "materials": [{"item": "2-1/4 in. primed MDF casing, 7 ft", "qty": 5, "unit": "piece", "unit_cost": 9.5,
                        "qty_ref": "W5"},
                       {"item": "28 in. x 80 in. prehung interior door", "qty": 1, "unit": "ea",
                        "unit_cost": 120, "qty_ref": "S1"},
                       {"item": "wood filler", "qty": 1, "unit": "ea", "unit_cost": 0}],
         "subs": []},
        {"name": "3.1 Electrical", "cost_code": "Electrical", "scope_item_ids": ["S2"],
         "scope_lines": ["Add two receptacles on Wall 2"], "notes": [], "internal_notes": [],
         "labor": [], "materials": [],
         "subs": [{"name": "Receptacles", "sub": "Redland Electric", "licensed_trade": "electrical",
                   "cost": 300, "anchors": ["A9"], "basis": "two at $150 each"}]},
        {"name": "Structural", "cost_code": "Framing", "scope_item_ids": ["S3", "S1"],
         "scope_lines": ["Install a steel lintel"], "notes": [], "internal_notes": [], "labor": [], "materials": [],
         "subs": [{"name": "Steel lintel and engineer's letter", "sub": None, "licensed_trade": "structural",
                   "cost": 900, "anchors": [], "basis": "general knowledge"}]},
    ]
    alternates = [{"name": "Solid-core door", "cost_code": "Windows & Doors", "scope_item_ids": [],
                   "scope_lines": ["Upgrade the door to solid core"], "notes": ["Quieter, heavier."],
                   "internal_notes": [], "labor": [{"task": "Carpentry", "hours": 1}],
                   "materials": [{"item": "solid core door upcharge", "qty": 1, "unit": "ea", "unit_cost": 90}],
                   "subs": []}]
    out = {"groups": groups, "alternates": alternates}
    out.update(over)
    return json.dumps(out)


CASING = {"name": "2-1/4 in. x 7 ft Primed MDF Casing Moulding", "unitCost": 8.98, "storeSkuNumber": "111",
          "brand": "Alexandria", "link": "https://hd/casing", "modelNumber": "M1"}
DOOR_CASE = {"name": "28 in. x 80 in. Prehung Interior Door (Case of 6)", "unitCost": 900.0,
             "storeSkuNumber": "222"}


def budget_run():
    run = tb.answered_run(store=True)
    run["room_file"] = dict(tb.updated_room_file(), project=run["room_file"]["project"])
    run["room_file"]["scope_items"] = [dict(s) for s in SCOPE_ITEMS]
    run["build"] = {"state": "building", "page_content": sb.clean_page(tb.PAGE),
                    "steps": {n: {"done": True} for n in ("write", "descriptions", "drawings")}
                    | {"page": {"done": True, "title": "Office Remodel Scope - Draft from Oct 19, 2026 Site Visit"}}}
    scope_runs._save(run)
    return run


class CleaningTests(unittest.TestCase):
    def test_text_rules(self):
        self.assertEqual(bud.clean_text("Install **casing** — both sides \U0001F6E0"),
                         "Install casing - both sides")
        self.assertEqual(bud.clean_text("keep snake_case words, drop _emphasis_"),
                         "keep snake_case words, drop emphasis")
        self.assertEqual(bud.clean_name("3.1 Electrical"), "Electrical")
        self.assertEqual(bud.clean_name("12.5.1 - GFCI Outlets"), "GFCI Outlets")
        self.assertEqual(bud.clean_name("2x4 Wall Framing"), "2x4 Wall Framing")
        self.assertLessEqual(len(bud.clean_name("Word " * 30)), bud.MAX_GROUP_NAME)

    def test_description_format(self):
        g = bud.clean_group({"name": "Paint", "scope_lines": ["Paint the walls"], "notes": ["Matches closely."]})
        self.assertEqual(bud.description(g), "Scope Includes:\n\n- Paint the walls\n\nNOTES:\n- Matches closely.")
        g["notes"] = []
        self.assertEqual(bud.description(g), "Scope Includes:\n\n- Paint the walls")
        g["notes"] = ["x" * 3000, "y" * 3000]
        text = bud.description(g)
        self.assertLessEqual(len(text), bud.MAX_DESCRIPTION)
        self.assertIn("NOTES:\n- x", text)
        self.assertNotIn("yyy", text)

    def test_licensed_trade_words(self):
        self.assertEqual(bud._licensed("Electrical"), "electrical")
        self.assertEqual(bud._licensed("HVAC"), "hvac")
        self.assertEqual(bud._licensed("gas line"), "gas")
        self.assertIsNone(bud._licensed(False))
        self.assertIsNone(bud._licensed("null"))

    def test_anchors_load_from_the_repo_csvs(self):
        anchors = dict(bud.load_anchors())
        self.assertGreater(len(anchors), 50)
        self.assertTrue(any("Redland Electric" in t for t in anchors.values()))
        self.assertTrue(any("OCC priced trade group" in t for t in anchors.values()))
        self.assertEqual(bud.load_anchors(directory="/nonexistent"), [])

    def test_system_prompt(self):
        text = bud.system_blocks()[0]["text"]
        self.assertIn("RATE ANCHORS", text)
        self.assertIn("Trim & Millwork", text)
        self.assertIn('"groups"', text)


class PricingTests(tr.Base):
    def plan(self, answer=None, jt=None):
        self.jt = jt or FakeJobTread(products={"casing": CASING, "prehung": DOOR_CASE})
        run = budget_run()
        quantities = [(f"W{i}", {"item": f"Office - q{i}", "value": 10, "unit": "lf", "basis": "b",
                                 "src": "est" if i == 5 else "dictated"}) for i in range(1, 8)]
        return bud.make_plan(run, run["room_file"], json.loads(answer or budget_answer()), quantities,
                             "Office Remodel Scope - Draft", self.jt, tr.ORG, log=lambda m: None)

    def items(self, plan, key):
        return [i["args"] for i in next(g for g in plan["groups"] if g["key"] == key)["items"]]

    def test_markups_and_catalog(self):
        plan = self.plan()
        trim = self.items(plan, "g0")
        labor = next(i for i in trim if i["costTypeId"] == v2.COST_TYPE_LABOR)
        self.assertEqual((labor["quantity"], labor["unitCost"], labor["unitPrice"]), (6, 55.0, 89.0))
        casing = next(i for i in trim if i["name"].startswith("2-1/4"))
        self.assertEqual((casing["unitCost"], casing["unitPrice"]), (8.98, round(8.98 * 1.65, 2)))   # catalog price
        self.assertEqual(casing["customFieldValues"]["SKU"], "111")
        door = next(i for i in trim if i["name"].startswith("28 in."))
        self.assertEqual(door["unitCost"], 120)                    # case price rejected, AI cost kept
        # No Home Depot notes next to items (Jason, Oct 20, 2026).
        for i in trim:
            if i["costTypeId"] == v2.COST_TYPE_MATERIALS:
                self.assertNotIn("description", i)
        self.assertFalse(any(i["name"] == "wood filler" for i in trim))   # no cost -> dropped
        notes = trim[-1]
        self.assertEqual(notes["name"], v2.INTERNAL_NOTES_ITEM_NAME)
        self.assertEqual((notes["unitCost"], notes["unitPrice"], notes["showDescription"]), (0, 0, False))
        self.assertIn("Door size assumed", notes["description"])
        self.assertIn("Office - q5 (est. from photos)", notes["description"])
        self.assertNotIn("case/multipack", notes["description"])   # catalog caveats stay out of NOTES
        self.assertNotIn("Home Depot", notes["description"])
        self.assertIn("Office Remodel Scope - Draft", notes["description"])
        self.assertEqual(trim[0]["costCodeId"], v2.COST_CODE_MAP["Trim & Millwork"])

    def test_licensed_sub_needs_an_anchor(self):
        plan = self.plan()
        elec = self.items(plan, "g1")[0]
        self.assertEqual((elec["unitCost"], elec["unitPrice"]), (300, 435.0))
        self.assertEqual(elec["costTypeId"], v2.COST_TYPE_SUB)
        self.assertEqual(elec["name"], "Receptacles")
        lintel = self.items(plan, "g2")[0]
        self.assertEqual((lintel["unitCost"], lintel["unitPrice"]), (0, 0))
        self.assertTrue(lintel["name"].endswith("(PRICING TBD)"))
        self.assertIn("no rate anchor", self.items(plan, "g2")[-1]["description"])
        self.assertEqual(plan["tbd"], 1)

    def test_names_descriptions_totals_and_checks(self):
        plan = self.plan()
        names = [g["name"] for g in plan["groups"]]
        self.assertEqual(names, ["Trim and Carpentry", "Electrical", "Structural", "Project Management"])
        desc = plan["groups"][0]["description"]
        self.assertTrue(desc.startswith("Scope Includes:\n\n- Install a prehung door"))
        self.assertNotIn("*", desc)
        self.assertNotIn("—", desc)
        self.assertIn("\n\nNOTES:\n- Pricing assumes", desc)
        expected_cost = 6 * 55 + 5 * 8.98 + 120 + 300
        self.assertAlmostEqual(plan["cost"], expected_cost, places=2)
        base = 6 * 89 + 5 * round(8.98 * 1.65, 2) + round(120 * 1.65, 2) + 435
        self.assertAlmostEqual(plan["price"], base + round(base * 0.10, 2), places=2)   # + project management
        self.assertTrue(any("S1 is in more than one group" in w for w in plan["warnings"]))
        self.assertEqual(plan["alternates"][0]["name"], "Option 1 - Solid-core door")
        self.assertEqual(plan["parent_name"], "Site Scope Draft - Oct 19, 2026")

    def test_sub_names_stay_off_client_lines(self):
        self.assertEqual(bud.strip_sub_name("Quartz vanity tops and bench top - Randall", "Randall"),
                         "Quartz vanity tops and bench top")
        self.assertEqual(bud.strip_sub_name("Tile with Style - shower tile", "Tile with Style"), "Shower tile")
        self.assertEqual(bud.strip_sub_name("Mirror wiring (David)", "David"), "Mirror wiring")
        self.assertEqual(bud.strip_sub_name("Drywall by Shane", "Shane"), "Drywall")
        self.assertEqual(bud.strip_sub_name("Davidson trim", "David"), "Davidson trim")
        self.assertEqual(bud.strip_sub_name("Shane", "Shane"), "Shane")         # nothing left - keep it
        notes = self.items(self.plan(), "g1")[-1]["description"]
        self.assertIn("'Receptacles' is for Redland Electric.", notes)

    def test_allowances_haul_away_and_project_management(self):
        ans = json.loads(budget_answer())
        ans["groups"][0]["materials"].append(
            {"item": "Prehung door casing allowance", "qty": 1, "unit": "ea", "unit_cost": 200, "allowance": True})
        ans["groups"][0]["haul_away_loads"] = 2
        plan = self.plan(json.dumps(ans), jt=FakeJobTread(products={"casing": CASING}))
        searched = [q["homeDepotProducts"]["$"]["searchQuery"] for q in self.jt.calls if "homeDepotProducts" in q]
        self.assertFalse(any("allowance" in q.lower() for q in searched))      # allowances skip the catalog
        trim = self.items(plan, "g0")
        allowance = next(i for i in trim if i["name"] == "Prehung door casing allowance")
        self.assertEqual((allowance["unitCost"], allowance["unitPrice"]), (200, 330.0))
        haul = next(i for i in trim if i["name"] == "Haul Away")
        self.assertEqual((haul["quantity"], haul["unitCost"], haul["unitPrice"]), (2, 228.0, 350.0))
        self.assertEqual(haul["costTypeId"], v2.COST_TYPE_MATERIALS)
        self.assertIn("Haul Away: 2 load(s)", trim[-1]["description"])
        pm = plan["groups"][-1]
        base = sum(g["price"] for g in plan["groups"][:-1])
        self.assertEqual((pm["name"], pm["cost"], pm["price"]), ("Project Management", 0.0, round(base * 0.10, 2)))
        fee = pm["items"][0]["args"]
        self.assertEqual((fee["unitCost"], fee["unitPrice"], fee["costTypeId"]),
                         (0, round(base * 0.10, 2), v2.COST_TYPE_LABOR))
        self.assertEqual(fee["costCodeId"], v2.COST_CODE_MAP["Project Management"])
        self.assertIn("1 $0 PRICING TBD line(s)", pm["items"][1]["args"]["description"])
        self.assertFalse(pm["items"][1]["args"]["showDescription"])
        self.assertTrue(pm["description"].startswith("Scope Includes:\n\n- Schedule and coordinate"))
        self.assertNotIn("Project Management", [g["name"] for g in plan["alternates"]])

    def test_missing_scope_item_and_unpriced_sub(self):
        ans = json.loads(budget_answer())
        ans["groups"] = ans["groups"][:1]
        ans["groups"][0]["subs"] = [{"name": "Flooring patch", "sub": "Greer Flooring", "cost": None}]
        plan = self.plan(json.dumps(ans))
        self.assertTrue(any("S2" in w and "S3" in w for w in plan["warnings"]))
        tbd = next(i for i in self.items(plan, "g0") if i["costTypeId"] == v2.COST_TYPE_SUB)
        self.assertTrue(tbd["name"].endswith("(PRICING TBD)"))

    def test_catalog_failure_keeps_ai_costs(self):
        class Broken(FakeJobTread):
            def __call__(self, query):
                if "homeDepotProducts" in query:
                    raise OSError("down")
                return super().__call__(query)
        plan = self.plan(jt=Broken())
        casing = next(i for i in self.items(plan, "g0") if i["name"].startswith("2-1/4"))
        self.assertEqual(casing["unitCost"], 9.5)

    def test_no_groups_is_an_error(self):
        with self.assertRaises(bud.BudgetError):
            self.plan(json.dumps({"groups": [{"name": ""}], "alternates": []}))


class WriteTests(tr.Base):
    def setUp(self):
        super().setUp()
        self.run = budget_run()

    def go(self, jt, answer=None):
        self.claude(tr.sse(answer or budget_answer()))
        step = self.run["build"]["steps"].setdefault("budget", {})
        return bud.run_budget(self.run, step, lambda: scope_runs._save(self.run), jt, tr.ORG, log=lambda m: None)

    def test_writes_under_one_parent(self):
        jt = FakeJobTread(products={"casing": CASING}, existing=["Site Scope Draft - Oct 19, 2026"])
        step = self.go(jt)
        self.assertTrue(step["done"])
        parent = jt.groups[0]
        self.assertEqual(parent["name"], "Site Scope Draft - Oct 19, 2026 (2)")
        self.assertNotIn("parentCostGroupId", parent)
        self.assertEqual(parent["jobId"], tr.JOB["id"])
        trim = jt.group("Trim and Carpentry")
        self.assertEqual(trim["parentCostGroupId"], parent["id"])
        self.assertTrue(trim["description"].startswith("Scope Includes:"))
        self.assertEqual(jt.items_in("Trim and Carpentry")[-1]["name"], v2.INTERNAL_NOTES_ITEM_NAME)
        alts = jt.group(bud.ALTERNATES_GROUP)
        self.assertEqual((alts["minSelectionsRequired"], alts["maxSelectionsAllowed"]), (0, 1))
        self.assertEqual(jt.group("Option 1 - Solid-core door")["parentCostGroupId"], alts["id"])
        writes = [q for q in jt.calls if "createCostGroup" in q or "createCostItem" in q]
        self.assertTrue(all(len(q) == 1 for q in writes))           # one mutation per call
        pub = bud.public(self.run)
        self.assertEqual((pub["groups"], pub["alternates"], pub["tbd"]), (4, 1, 1))
        self.assertEqual(pub["parent"], "Site Scope Draft - Oct 19, 2026 (2)")
        self.assertIn(tr.JOB["id"], pub["job_url"])
        stored = scope_runs.load_run("r1")["build"]["steps"]["budget"]
        self.assertEqual(stored["written"]["parent_id"], parent["id"])

    def test_network_failure_resumes_without_duplicates(self):
        jt = FakeJobTread(fail_items={"Receptacles": urllib.error.URLError("timed out")})
        with self.assertRaises(bud.BudgetError) as ctx:
            self.go(jt)
        self.assertIn("tap Build", str(ctx.exception))
        written_before = len(jt.items)
        step = self.run["build"]["steps"]["budget"]
        self.claude()                                   # the retry must not ask Claude again
        bud.run_budget(self.run, step, lambda: scope_runs._save(self.run), jt, tr.ORG, log=lambda m: None)
        self.assertTrue(step["done"])
        self.assertEqual(len([g for g in jt.groups if g["name"].startswith("Site Scope Draft")]), 1)
        self.assertEqual(len([g for g in jt.groups if g["name"] == "Electrical"]), 1)
        self.assertEqual(len([i for i in jt.items if i["name"] == "Receptacles"]), 1)
        self.assertGreater(len(jt.items), written_before)
        names = [i["name"] for i in jt.items]
        self.assertEqual(len(names), len(set(zip(names, [i["costGroupId"] for i in jt.items]))))

    def test_refused_item_is_skipped_and_fallbacks_used(self):
        bad = urllib.error.HTTPError("https://api.jobtread.com/pave", 400, "bad", {}, io.BytesIO(b"x"))
        jt = FakeJobTread(products={"casing": CASING}, fail_items={
            "2-1/4 in. primed MDF casing, 7 ft": bad, "Receptacles": bad}, fail_selection=True)
        step = self.go(jt)
        self.assertTrue(step["done"])
        casing = [i for i in jt.items if i["name"].startswith("2-1/4")]
        self.assertEqual(len(casing), 1)
        self.assertNotIn("customFieldValues", casing[0])        # bare retry
        self.assertTrue(any("Receptacles" in s for s in step["written"]["skipped"]))
        self.assertTrue(step["written"]["alternates_plain"])
        self.assertNotIn("maxSelectionsAllowed", jt.group(bud.ALTERNATES_GROUP))

    def test_no_jobtread_connection(self):
        with self.assertRaises(bud.BudgetError):
            bud.run_budget(self.run, {}, lambda: None, None, None)


@unittest.skipUnless(tb.HAVE_PIL, "Pillow not installed")
class BuildWithBudgetTests(tb.BuildBase):
    def setUp(self):
        super().setUp()
        self.real_budget.stop()       # BuildBase stubs the budget out; these tests run it

    def test_build_ends_with_the_budget(self):
        fake = self.claude(tr.sse(tb.build_answer()), tr.sse(budget_answer()))
        jt = FakeJobTread()
        run = tb.answered_run()
        sb.run_build(run, scope_runs._save, self.log, jobtread_query=jt, org_id=tr.ORG)
        b = run["build"]
        self.assertEqual(b["state"], "done", b)
        self.assertEqual(len(fake.bodies), 2)
        self.assertIn("WORKING QUANTITIES", json.dumps(fake.bodies[1]["messages"]))
        self.assertIn("Install a new door", json.dumps(fake.bodies[1]["messages"]))
        pub = sb.public(run)
        self.assertTrue(pub["steps"]["budget"])
        self.assertEqual(pub["budget"]["groups"], 4)   # 3 trade groups + Project Management
        self.assertEqual(pub["usage"]["calls"], 2)
        self.assertAlmostEqual(pub["usage"]["cost_usd"],
                               b["usage"]["cost_usd"] + b["steps"]["budget"]["usage"]["cost_usd"], places=4)
        notes = next(i for i in jt.items if i["name"] == v2.INTERNAL_NOTES_ITEM_NAME)
        self.assertIn(pub["page"]["title"], notes["description"])

    def test_budget_failure_leaves_the_build_partial(self):
        self.claude(tr.sse(tb.build_answer()), tr.sse("not json"), tr.sse("still not json"))
        run = tb.answered_run()
        sb.run_build(run, scope_runs._save, self.log, jobtread_query=FakeJobTread(), org_id=tr.ORG)
        self.assertEqual(run["build"]["state"], "partial")
        self.assertIn("budget", run["build"]["error"])
        self.assertTrue(run["build"]["steps"]["page"]["done"])
        self.assertIsNone(sb.public(run)["budget"])

    def test_build_button_adds_the_budget_to_an_older_run(self):
        run = budget_run()                     # built before phase 6: done, no budget step
        run["build"]["state"] = "done"
        scope_runs._save(run)
        self.claude(tr.sse(budget_answer()))
        jt = FakeJobTread()
        status, body = scope_runs.handle_post("/scope/runs/build", {"run_id": "r1"}, jt, tr.ORG,
                                              start_thread=tr.run_inline)
        self.assertEqual(status, 202)
        stored = scope_runs.load_run("r1")
        self.assertEqual(stored["build"]["state"], "done", stored["build"])
        self.assertTrue(stored["build"]["steps"]["budget"]["done"])
        self.assertTrue(jt.groups[0]["name"].startswith("Site Scope Draft"))
        status, body = scope_runs.handle_post("/scope/runs/build", {"run_id": "r1"}, jt, tr.ORG,
                                              start_thread=tr.run_inline)
        self.assertEqual(status, 200)                  # finished - nothing more to do
        self.assertEqual(len([g for g in jt.groups if g["name"].startswith("Site Scope Draft")]), 1)


if __name__ == "__main__":
    unittest.main()
