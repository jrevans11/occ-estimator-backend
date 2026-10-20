"""Tests for Site Scope Tool phases 5-6: the build (scope_build.py), working
quantities (scope_quantities.py), the JobTread budget (scope_budget.py) and the
build's place in a run (scope_runs.py).

Run from the repo root:  python3 -m unittest discover tests

Reuses phase 4's fakes (tests/test_scope_runs.py): CompanyCam in the Public
API's {data, errors, meta} envelope, Claude at its streaming layer. This file
adds photo descriptions (one per photo, replaced on every write - so the code
must read and join) and Page creation, and a fake JobTread for the budget
(budget reads, cost group / cost item creates, Home Depot catalog search). The
drawing upload itself is tested in test_scope_uploads.py; here it is stubbed.
No keys or network needed.
"""

import copy
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import companycam_api as cc  # noqa: E402
import estimate_v2_draft as v2  # noqa: E402
import scope_budget as bud  # noqa: E402
import scope_build as sb  # noqa: E402
import scope_runs  # noqa: E402
import scope_store  # noqa: E402
import test_scope_runs as tr  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

if HAVE_PIL:
    import scope_quantities as sq


def sample_file():
    with open(tr.SAMPLE) as f:
        return json.load(f)


class FakeBuildCC(tr.FakeCC):
    """phase 4's fake plus photos by id, descriptions and Page creation."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.descriptions = {"9001": "Kitchen wide shot", "9002": ""}
        self.other_project = {"7777"}
        self.created_pages = []
        self.page_failures = []        # status codes to answer the next Page creates with

    def __call__(self, method, path, params=None, body=None):
        if path.startswith("/photos/") and path.endswith("/descriptions") and method == "POST":
            pid = path.split("/")[2]
            self.calls.append((method, path))
            self.descriptions[pid] = body["description"]["content"]
            return tr.env(self._photo(pid))
        if path.startswith("/photos/") and method == "GET":
            self.calls.append((method, path))
            pid = path.split("/")[2]
            if pid not in self.descriptions and pid not in self.other_project:
                raise cc.CompanyCamError(f"CompanyCam GET {path} returned HTTP 404: not found", 404)
            return tr.env(self._photo(pid))
        if path == f"/projects/{tr.PROJECT_ID}/pages" and method == "POST":
            self.calls.append((method, path))
            if self.page_failures:
                code = self.page_failures.pop(0)
                raise cc.CompanyCamError(f"CompanyCam POST {path} returned HTTP {code}: invalid", code)
            page = {"id": str(4000000 + len(self.created_pages)), "title": body["title"],
                    "tiptap_content": body["tiptap_content"], "shared": True, "share_url": "https://share"}
            self.created_pages.append(page)
            return tr.env(page)
        if path == f"/projects/{tr.PROJECT_ID}/pages" and method == "GET":
            self.calls.append((method, path))
            return tr.env(([{"id": "3006585", "title": "Site Visit Notes"}] if self.notes else [])
                          + [{"id": p["id"], "title": p["title"]} for p in self.created_pages])
        return super().__call__(method, path, params, body)

    def _photo(self, pid):
        p = tr.photo(pid, "2026-10-20T02:10:00Z", self.descriptions.get(pid) or None)
        p["project_id"] = "55555" if pid in self.other_project else tr.PROJECT_ID
        return p


def updated_room_file():
    rf = sample_file()
    rf.pop("project", None)
    rf["rooms"][0]["walls"][3]["length"] = {"in": 156, "src": "confirmed"}
    rf.update(client_goals=["Open the office to the hall"],
              conflicts=[{"what": "Wall 4 length", "source_a": "note: 13'-0\"", "source_b": "photo: about 12'",
                          "action": "Settled in Q1 - 13'-0\""}],
              scope_items=[{"id": "S1", "trade": "Carpentry", "area": "Office", "work": "Install a new door",
                            "quantity": {"value": 1, "unit": "ea", "src": "confirmed"}, "sub": None,
                            "licensed_trade": False, "photos": ["P1"], "notes": ["N1"], "detail": ""},
                           {"id": "S2", "trade": "Electrical", "area": "Office", "work": "Add two receptacles",
                            "quantity": {"value": 2, "unit": "ea", "src": "dictated"}, "sub": "Redland Electric",
                            "licensed_trade": True, "photos": [], "notes": ["N1"], "detail": ""}],
              photos=[{"ref": "P1", "id": "9001", "area": "Office", "wall": 1,
                       "description": "Door wall - new door goes left of the existing one."},
                      {"ref": "P2", "id": "9002", "area": "Office", "wall": None,
                       "description": "Window wall, drawn by the Site Scope Tool later."},
                      {"ref": "P3", "id": "7777", "area": "Office", "wall": 2, "description": "Wrong project."},
                      {"ref": "P4", "id": "", "area": "Office", "wall": 3, "description": "No id."}])
    return rf


PAGE = {"scope_type": "Office Remodel Scope", "client_goals": ["Open the office to the hall"],
        "trades": [{"trade": "Demolition and Framing", "sub": None,
                    "lines": ["Cut a new 2'-8\" door opening in Wall 1 (assumed position)"], "not_in_scope": []},
                   {"trade": "Electrical", "sub": "Redland Electric",
                    "lines": ["Add two receptacles on Wall 2 - by a licensed sub"],
                    "not_in_scope": ["Panel upgrade"]}],
        "alternates": [{"name": "Solid-core door", "description": "Quieter, about a day more lead time"}],
        "missing_info": ["Nearest circuit for the Wall 2 receptacles"],
        "key_photos": [{"ref": "P1", "caption": "Door wall"}, {"ref": "P9", "caption": "Not a real ref"}]}


def build_answer(page=PAGE, room_file=None):
    return json.dumps({"room_file": room_file or updated_room_file(), "page": page})


def answered_run(store=True):
    run = {"run_id": "r1", "status": "answered", "job": {"id": tr.JOB["id"], "number": "00489",
                                                           "name": tr.JOB["name"], "address": "1217 Fieldstone Ct"},
           "project_id": tr.PROJECT_ID, "visit_date": "2026-10-19",
           "inputs": {"visit_date": "2026-10-19", "notes": 3, "photos": 3, "video_transcripts": 1},
           "questions": [{"id": "Q1", "question": "How long is Wall 4?", "suggested_answer": "13'-0\""}],
           "answers": [{"id": "Q1", "question": "How long is Wall 4?", "answer": "13'-0\"", "used_suggestion": True}],
           "room_file": dict(sample_file(), photos=[], scope_items=[], conflicts=[], client_goals=[]),
           "created_at": 1, "updated_at": 1}
    run["room_file"]["project"]["companycam_project_id"] = tr.PROJECT_ID
    if store:
        scope_runs._save(run)
        scope_store.put(scope_runs._latest_key(tr.JOB["id"]), "r1")
    return run


def fake_upload(results=None):
    """Stands in for scope_uploads.upload_drawings: every drawing lands unless
    results says otherwise ({filename: None} = refused)."""
    calls = []

    def upload(project_id, drawings, base_url, description_for, **kw):
        calls.append([d["filename"] for d in drawings])
        out = []
        for i, d in enumerate(drawings):
            pid = (results or {}).get(d["filename"], f"77{len(calls)}{i}")
            out.append({"drawing": d["title"], "filename": d["filename"], "photo_id": pid,
                        "status": "in CompanyCam" if pid else "upload refused", "temp_url": "https://tmp",
                        "description": description_for(d)})
        return out
    return upload, calls


def jt_http_error(code, text="bad request"):
    return urllib.error.HTTPError("https://api.jobtread.com/pave", code, "err", {}, io.BytesIO(text.encode()))


class FakeJT:
    """JobTread's Pave API as the budget uses it: job lookups (like phase 4's
    fake), the job's budget groups, createCostGroup (with nested lineItems),
    createCostItem and the Home Depot catalog."""

    def __init__(self, existing=(), refuse_one_call=False, item_failures=0, products=None):
        self.groups = {}           # id -> {"name", "parent", "description", "min", "max", "how"}
        self.items = []            # {"group", ...args}
        self.calls = []
        self.refuse_one_call = refuse_one_call
        self.item_failures = item_failures
        self.products = products or {}
        for name in existing:
            self._group({"name": name}, None, "existing")

    def _group(self, args, parent, how):
        gid = f"G{len(self.groups) + 1}"
        self.groups[gid] = {"name": args["name"], "parent": parent, "description": args.get("description"),
                            "min": args.get("minSelectionsRequired"), "max": args.get("maxSelectionsAllowed"),
                            "how": how}
        for li in args.get("lineItems") or []:
            if li["_type"] == "costGroup":
                self._group(li, gid, how)
            else:
                self.items.append(dict({k: v for k, v in li.items() if k != "_type"}, group=gid))
        return gid

    def by_name(self, name):
        return next(g for g, v in self.groups.items() if v["name"] == name)

    def items_in(self, gid):
        return [i for i in self.items if i["group"] == gid]

    def __call__(self, query):
        self.calls.append(json.loads(json.dumps(query)))
        if "homeDepotProducts" in query:
            return {"homeDepotProducts": {"nodes": self.products.get(query["homeDepotProducts"]["$"]["searchQuery"], [])}}
        if "createCostGroup" in query:
            args = query["createCostGroup"]["$"]
            if "lineItems" in args and self.refuse_one_call:
                raise jt_http_error(400, "lineItems not allowed")
            gid = self._group(args, args.get("parentCostGroupId"), "one call" if "lineItems" in args else "bare")
            return {"createCostGroup": {"createdCostGroup": {"id": gid}}}
        if "createCostItem" in query:
            args = query["createCostItem"]["$"]
            if self.item_failures:
                self.item_failures -= 1
                raise jt_http_error(500, "server error")
            self.items.append(dict(args, group=args["costGroupId"]))
            return {"createCostItem": {"createdCostItem": {"id": f"I{len(self.items)}"}}}
        if "job" in query and "costGroups" in query["job"]:
            jid = query["job"]["$"]["id"]
            job = {"22PDLqnyG8eT": {"id": "22PDLqnyG8eT", "number": "00035", "name": "Test job- JT"}}.get(
                jid, {"id": tr.JOB["id"], "number": tr.JOB["number"], "name": tr.JOB["name"]})
            return {"job": dict(job, costGroups={"nodes": [
                {"id": g, "name": v["name"], "parentCostGroup": {"id": v["parent"]} if v["parent"] else None}
                for g, v in self.groups.items()]})}
        if "job" in query:
            return {"job": tr.JOB if query["job"]["$"]["id"] == tr.JOB["id"] else None}
        where = query["organization"]["jobs"]["$"]["where"]
        jobs = {"00489": tr.JOB, "00035": {"id": "22PDLqnyG8eT", "number": "00035", "name": "Test job- JT"}}
        return {"organization": {"jobs": {"nodes": [jobs[where[1]]] if where[1] in jobs else []}}}


def redland_anchor(word="receptacle"):
    return next(k for k, a in bud.load_anchors().items()
                if a["vendor"] == "Redland Electric" and word in (a["description"] + a["note"]).lower())


def budget_plan(qty_refs=True, options=True):
    paint = {"item": "Interior wall paint", "unit": "gal", "unit_cost": 38.0}
    paint.update({"qty_ref": "Q3", "per": 350, "factor": 2, "waste": 0.1} if qty_refs else {"qty": 2, "qty_src": "est"})
    plan = {"groups": [
        {"name": "Demolition and Framing \u2014 **Office**", "cost_code": "Framing", "scope_items": ["S1"],
         "scope_lines": ["Cut a new 2'-8\" door opening in Wall 1 (assumed position)", "Install a prehung door"],
         "client_notes": ["Price assumes a standard hollow-core prehung door."], "internal_notes": ["Check the header"],
         "conflicts": ["C1"], "missing_info": [], "licensed_trade": None, "sub": None,
         "labor": [{"task": "cut opening, frame and hang door", "trade": "carpentry", "hours": 8}],
         "materials": [{"item": "2x4x8 stud", "unit": "ea", "unit_cost": 4.5, "qty": 6, "qty_src": "est"}, paint],
         "sub_lines": []},
        {"name": "Electrical", "cost_code": "Electrical", "scope_items": ["S2"], "scope_lines": ["Add two receptacles"],
         "client_notes": [], "internal_notes": [], "conflicts": [], "missing_info": ["M1"],
         "licensed_trade": "electrical", "sub": "Redland Electric",
         "labor": [], "materials": [],
         "sub_lines": [{"description": "Add two receptacles on Wall 2", "anchor": redland_anchor(), "qty": 2}]},
    ], "options": []}
    if options:
        plan["options"] = [{"name": "Office Door Options", "kind": "add_on", "choices": [
            {"name": "Solid-core door", "cost_code": "Windows & Doors", "scope_items": ["S1"],
             "scope_lines": ["Install a solid-core door slab in place of the hollow-core door"],
             "client_notes": ["Quieter, about a day more lead time."],
             "materials": [{"item": "Solid core door slab", "unit": "ea", "unit_cost": 120, "qty": 1,
                            "qty_src": "dictated"}]}]}]
    return plan


def budget_answer(**kw):
    return json.dumps(budget_plan(**kw))


class BuildBase(tr.Base):
    def setUp(self):
        super().setUp()
        self.jt = FakeJT()
        self.jobtread = (self.jt, tr.ORG)
        os.environ.pop("SCOPE_BUDGET_JOB", None)
        self.cc = FakeBuildCC()
        p = mock.patch.object(cc, "_request", self.cc)
        p.start()
        self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {"SCOPE_PUBLIC_URL": "https://occ-estimator.onrender.com"})
        e.start()
        self.addCleanup(e.stop)
        self.upload, self.upload_calls = fake_upload()
        u = mock.patch.object(sb.scope_uploads, "upload_drawings", side_effect=lambda *a, **k: self.upload(*a, **k))
        u.start()
        self.addCleanup(u.stop)
        self.logs = []

    def log(self, msg):
        self.logs.append(msg)


def walk(node):
    yield node
    for c in node.get("content") or []:
        yield from walk(c)


def doc_text(doc):
    return " ".join(n.get("text", "") for n in walk(doc) if n.get("type") == "text")


# ── Quantities ───────────────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class QuantityTests(unittest.TestCase):
    def by_item(self, rf):
        return {q["item"]: q for q in sq.all_quantities(rf)}

    def test_sample_office(self):
        q = self.by_item(sample_file())
        self.assertEqual((q["Office - floor area"]["value"], q["Office - floor area"]["src"]), (156, "dictated"))
        self.assertEqual(q["Office - ceiling area"]["value"], 156)
        wall = q["Office - wall area (paint/drywall)"]
        self.assertEqual(wall["unit"], "sf")
        self.assertLess(wall["value"], 500)          # 50' x 10' less the openings
        self.assertEqual(wall["src"], "assumed")     # door heights are assumed
        self.assertIn("perimeter 50'-0\"", wall["basis"])
        self.assertLess(q["Office - base trim"]["value"], 50)
        self.assertGreater(q["Office - casing (this room's side)"]["value"], 0)
        self.assertNotIn("Office - crown", q)

    def simple_room(self, **extra):
        walls = [{"length": {"in": 120, "src": "dictated"}} for _ in range(4)]
        room = {"name": "Bath", "ceiling": {"in": 96, "src": "dictated"}, "walls": walls}
        room.update(extra)
        return {"schema": "occ-scope-rooms/1", "rooms": [room]}

    def test_box_room_numbers(self):
        rf = self.simple_room(finishes=[{"type": "crown"}])
        rf["rooms"][0]["walls"][0]["openings"] = [{"type": "door", "offset": {"in": 10, "src": "dictated"},
                                                   "width": {"in": 30, "src": "dictated"},
                                                   "height": {"in": 80, "src": "dictated"}}]
        q = self.by_item(rf)
        self.assertEqual(q["Bath - floor area"]["value"], 100)
        self.assertEqual(q["Bath - wall area (paint/drywall)"]["value"], round((4 * 120 * 96 - 30 * 80) / 144))
        self.assertEqual(q["Bath - base trim"]["value"], round((480 - 30) / 12))
        self.assertEqual(q["Bath - casing (this room's side)"]["value"], round((2 * 80 + 30) / 12))
        self.assertEqual(q["Bath - crown"]["value"], 40)
        self.assertTrue(all(x["src"] == "dictated" for x in q.values()))

    def test_tile_less_openings_and_estimated_source(self):
        rf = self.simple_room()
        w = rf["rooms"][0]["walls"][1]
        w["finishes"] = [{"type": "tile", "bottom": {"in": 0, "src": "dictated"}, "top": {"in": 84, "src": "est"}}]
        w["openings"] = [{"type": "window", "offset": {"in": 40, "src": "dictated"}, "width": {"in": 24, "src": "dictated"},
                          "sill": {"in": 48, "src": "dictated"}, "head": {"in": 72, "src": "dictated"}}]
        tile = self.by_item(rf)["Bath - wall tile area"]
        self.assertEqual(tile["value"], round((120 * 84 - 24 * 24) / 144))
        self.assertEqual(tile["src"], "est")

    def test_walls_that_dont_close_have_no_area(self):
        rf = self.simple_room()
        rf["rooms"][0]["walls"][0]["length"] = {"in": 150, "src": "dictated"}
        q = self.by_item(rf)
        self.assertNotIn("Bath - floor area", q)
        self.assertIn("Bath - base trim", q)

    def test_removed_openings_dont_count_and_scope_items_listed(self):
        rf = self.simple_room()
        rf["rooms"][0]["walls"][0]["openings"] = [{"type": "door", "status": "remove", "offset": 10, "width": 30}]
        rf["scope_items"] = [{"area": "Bath", "work": "Retile the floor", "quantity": {"value": 64, "unit": "sf",
                                                                                     "src": "est"}},
                             {"work": "No quantity", "quantity": None}]
        q = self.by_item(rf)
        self.assertEqual(q["Bath - base trim"]["value"], 40)
        self.assertEqual((q["Bath - Retile the floor"]["value"], q["Bath - Retile the floor"]["src"]), (64, "est"))
        self.assertEqual(len([x for x in q if "No quantity" in x]), 0)


# ── Photo descriptions ───────────────────────────────────────────────────────

class DescriptionTests(BuildBase):
    def test_text_and_drawing_mark_kept_out(self):
        self.assertEqual(sb.description_text({"area": "Office", "wall": 2, "description": "Window  wall."}),
                         "Office, Wall 2: Window wall.")
        self.assertEqual(sb.description_text({"description": "x"}), "x")
        self.assertEqual(sb.description_text({"area": "Office", "description": ""}), "")
        self.assertNotIn("Site Scope Tool", sb.description_text({"description": "Drawn by the site scope tool."}))

    def test_added_after_existing_never_replaced_and_safe_twice(self):
        rf = updated_room_file()
        r = sb.write_descriptions(rf, tr.PROJECT_ID, self.log)
        self.assertEqual((r["written"], r["already"]), (2, 0))
        self.assertTrue(r["done"])
        self.assertEqual(self.cc.descriptions["9001"],
                         "Kitchen wide shot\n\nOffice, Wall 1: Door wall - new door goes left of the existing one.")
        self.assertTrue(self.cc.descriptions["9002"].startswith("Office: Window wall"))
        self.assertNotIn("site scope tool", self.cc.descriptions["9002"].lower())
        self.assertEqual(r["photo_ids"], {"P1": "9001", "P2": "9002"})
        self.assertEqual(len(r["skipped"]), 2)           # other project, no id
        self.assertNotIn("7777", self.cc.descriptions)
        again = sb.write_descriptions(rf, tr.PROJECT_ID, self.log)
        self.assertEqual((again["written"], again["already"]), (0, 2))
        self.assertEqual(self.cc.descriptions["9001"].count("Door wall"), 1)

    def test_a_failed_write_leaves_the_step_unfinished(self):
        rf = updated_room_file()
        rf["photos"][0]["id"] = "4040"
        r = sb.write_descriptions(rf, tr.PROJECT_ID, self.log)
        self.assertFalse(r["done"])
        self.assertEqual(len(r["errors"]), 1)

    def test_full_description_is_left_alone(self):
        self.cc.descriptions["9001"] = "x" * 4990
        r = sb.write_descriptions(updated_room_file(), tr.PROJECT_ID, self.log)
        self.assertEqual(self.cc.descriptions["9001"], "x" * 4990)
        self.assertTrue(any("limit" in s for s in r["skipped"]))


# ── The scope Page ───────────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class PageTests(BuildBase):
    def setUp(self):
        super().setUp()
        self.run = answered_run(store=False)
        self.rf = updated_room_file()
        self.rf["project"] = self.run["room_file"]["project"]
        self.page = sb.clean_page(copy.deepcopy(PAGE))
        self.drawings = [{"photo_id": "8801", "title": "Office - Floor Plan"},
                         {"photo_id": None, "title": "Office - Wall 2 Elevation"}]

    def test_full_page(self):
        doc = sb.page_doc(self.run, self.rf, self.page, self.drawings, {"P1": "9001"})
        text = doc_text(doc)
        heads = [doc_text(n) for n in walk(doc) if n.get("type") == "heading"]
        for h in ("Client Goals", "Scope by Trade", "Demolition and Framing", "Electrical", "Optional Alternates",
                  "Drawings", "Measurement Conflicts", "Working Quantities", "Answers From the Site Questions",
                  "Missing Info Before Bidding", "Key Photos"):
            self.assertIn(h, heads)
        self.assertIn("Sub: Redland Electric", text)
        self.assertIn("Not in scope: Panel upgrade", text)
        self.assertIn("(suggested answer accepted)", text)
        self.assertNotIn("*", text)
        photo_ids = [n["attrs"]["photoId"] for n in walk(doc) if n.get("type") == "flexPhoto"]
        self.assertEqual(photo_ids, ["8801", "9001"])     # refused drawing and unknown ref left out
        for table in (n for n in walk(doc) if n.get("type") == "table"):
            for row in table["content"]:
                self.assertLessEqual(sum(c["attrs"]["colwidth"][0] for c in row["content"]), sb.PAGE_WIDTH)
        for s in (n for n in walk(doc) if n.get("type") == "section"):
            self.assertEqual(s["content"][0]["type"], "heading")
        tasks = [doc_text(n) for n in walk(doc) if n.get("type") == "taskItem"]
        self.assertIn("Nearest circuit for the Wall 2 receptacles", tasks)
        self.assertTrue(any(t.startswith("Office: ") or "Wall 4 window" in t for t in tasks))   # file flags too
        self.assertEqual(len(tasks), len(set(t.lower() for t in tasks)))

    def test_plain_fallbacks(self):
        doc = sb.page_doc(self.run, self.rf, self.page, self.drawings, {"P1": "9001"}, photos=False, tables=False)
        kinds = {n.get("type") for n in walk(doc)}
        self.assertFalse(kinds & {"section", "table", "flexGroup", "flexPhoto"})
        self.assertIn("Office - Floor Plan", doc_text(doc))
        self.assertIn("CompanyCam photo 9001", doc_text(doc))

    def test_title_and_refused_layout(self):
        self.cc.page_failures = [422]
        r = sb.write_page(self.run, self.rf, self.page, self.drawings, {"P1": "9001"}, self.log)
        self.assertEqual(r["title"], "Office Remodel Scope - Draft from Oct 19, 2026 Site Visit")
        self.assertEqual(r["layout"], "without photo cards")
        self.assertTrue(r["shared"])
        made = self.cc.created_pages[0]["tiptap_content"]
        self.assertFalse(any(n.get("type") == "flexGroup" for n in walk(made)))
        second = sb.write_page(self.run, self.rf, self.page, self.drawings, {}, self.log)
        self.assertEqual(second["title"], r["title"] + " (2)")

    def test_other_errors_are_not_retried(self):
        self.cc.page_failures = [500]
        with self.assertRaises(cc.CompanyCamError):
            sb.write_page(self.run, self.rf, self.page, [], {}, self.log)
        self.assertEqual(len([c for c in self.cc.calls if c == ("POST", f"/projects/{tr.PROJECT_ID}/pages")]), 1)

    def test_clean_page_shapes(self):
        p = sb.clean_page({"trades": [{"trade": ""}, {"trade": "Tile", "lines": "not a list"}], "key_photos": "x"})
        self.assertEqual(p["scope_type"], "Site Visit Scope")
        self.assertEqual(p["trades"], [{"trade": "Tile", "sub": None, "lines": [], "not_in_scope": []}])
        self.assertEqual(p["key_photos"], [])


# ── The budget (phase 6) ─────────────────────────────────────────────────────

QS = {"Q1": {"item": "Office - floor area", "value": 156, "unit": "sf", "basis": "floor plan", "src": "dictated"},
      "Q2": {"item": "Office - base trim", "value": 46, "unit": "lf", "basis": "perimeter", "src": "est"},
      "Q3": {"item": "Office - wall area (paint/drywall)", "value": 412, "unit": "sf", "basis": "perimeter x ceiling",
             "src": "assumed"}}
CS = {"C1": {"what": "Wall 4 length", "source_a": "note: 13'-0\"", "source_b": "photo: about 12'",
             "action": "Settled in Q1"}}
MS = {"M1": "Nearest circuit for the Wall 2 receptacles", "M2": "Office: Wall 4 window head height"}
SCOPE = {"S1": {"id": "S1", "work": "Install a new door", "licensed_trade": False},
         "S2": {"id": "S2", "work": "Add two receptacles", "licensed_trade": "electrical"}}


def priced(plan=None, products=None, scope=None):
    jt = FakeJT(products=products)
    rf = {"scope_items": list((scope or SCOPE).values())}
    return bud.price_plan(plan or budget_plan(), rf, QS, CS, MS, bud.load_anchors(), "Source: test Page",
                          jt, tr.ORG, log=lambda m: None), jt


def item(group, kind, word=""):
    return next(i for i in group["items"] if i["kind"] == kind and word in i["name"])


class BudgetPricingTests(unittest.TestCase):
    def test_anchors_from_every_vendor_file(self):
        anchors = bud.load_anchors()
        vendors = {a["vendor"].split(" (")[0] for a in anchors.values()}
        for v in ("Redland Electric", "Crawlspace Medic", "Tile with Style", "Greer Flooring", "Jordan Lumber",
                  "OCC trade group as Jason priced it"):
            self.assertIn(v, vendors)
        self.assertTrue(all(a["unit_price"] > 0 for a in anchors.values()))
        self.assertTrue(all(a["unit"] in bud.UNIT_IDS for a in anchors.values()))
        tile = next(a for a in anchors.values() if a["vendor"] == "Tile with Style" and a["unit"] == "sf")
        self.assertGreater(tile["unit_price"], 5)
        text = bud.system_blocks(anchors)[0]["text"]
        self.assertIn("R1 | Redland Electric", text)
        self.assertIn("Exterior Door Repairs", text)                # in-house calibration
        self.assertIn("Siding & Trim", text)                         # the real cost code names
        self.assertNotIn("{cost_codes}", text)

    def test_markups_quantities_and_anchor_math(self):
        p, _ = priced()
        framing, electrical = p["groups"]
        labor = item(framing, "labor")
        self.assertEqual((labor["qty"], labor["unit_cost"], labor["unit_price"]), (8, 55.0, 89.0))
        studs = item(framing, "material", "stud")
        self.assertEqual((studs["qty"], studs["unit_cost"], studs["unit_price"]), (6, 4.5, round(4.5 * 1.65, 2)))
        paint = item(framing, "material", "paint")
        self.assertEqual(paint["qty"], 3)                           # 412 sf x 2 coats x 1.10 / 350 = 2.6 -> 3 gal
        self.assertEqual(paint["unit"], "gal")
        anchor = bud.load_anchors()[redland_anchor()]
        sub = item(electrical, "sub")
        self.assertEqual((sub["qty"], sub["unit"], sub["unit_cost"]), (2, anchor["unit"], anchor["unit_price"]))
        self.assertEqual(sub["unit_price"], round(sub["unit_cost"] * 1.45, 2))
        self.assertEqual(electrical["cost"], round(anchor["unit_price"] * 2, 2))
        self.assertIn(f"Rate anchor {redland_anchor()}: Redland Electric", electrical["notes_text"])
        self.assertIn("(assumed)", framing["notes_text"])           # the paint quantity rests on an assumed number
        self.assertEqual((framing["cost"], framing["price"]),
                         bud.totals(framing["items"]))

    def test_licensed_work_without_an_anchor_or_bid_is_pricing_tbd(self):
        plan = budget_plan(options=False)
        plan["groups"][1]["sub_lines"] = [{"description": "Add two receptacles", "estimate": 400}]
        plan["groups"].append({"name": "Plumbing", "cost_code": "Plumbing", "licensed_trade": "plumbing",
                               "labor": [{"task": "move the sink drain", "hours": 4}]})
        plan["groups"].append({"name": "Tile", "cost_code": "Flooring", "sub_lines": [
            {"description": "Tile the floor", "estimate": 900},
            {"description": "Bid tile", "bid": 1000, "bid_source": "N2"}]})
        p, _ = priced(plan)
        electrical, plumbing, tile = p["groups"][1:]
        tbd = item(electrical, "tbd")
        self.assertTrue(tbd["name"].endswith("(PRICING TBD)"))
        self.assertEqual((tbd["unit_cost"], tbd["unit_price"]), (0, 0))
        self.assertIn("electrical is a licensed trade", electrical["notes_text"])
        self.assertEqual(item(plumbing, "tbd")["unit_cost"], 0)                 # added: no sub line at all
        self.assertIn("in-house labor is in a licensed-trade (plumbing) group", plumbing["notes_text"])
        self.assertEqual([i["unit_cost"] for i in tile["items"] if i["kind"] == "sub"], [900, 1000])   # not licensed
        self.assertIn("AI estimate", tile["notes_text"])
        self.assertIn("Sub bid $1,000.00 as Jason said it (N2)", tile["notes_text"])

    def test_scope_item_marked_licensed_makes_the_group_licensed(self):
        plan = budget_plan(options=False)
        plan["groups"][1]["licensed_trade"] = None
        plan["groups"][1]["sub_lines"] = [{"description": "Receptacles", "estimate": 300}]
        p, _ = priced(plan)
        self.assertEqual(p["groups"][1]["licensed"], "electrical")
        self.assertTrue(any(i["kind"] == "tbd" for i in p["groups"][1]["items"]))

    def test_client_text_rules(self):
        p, _ = priced()
        framing = p["groups"][0]
        self.assertEqual(framing["name"], "Demolition and Framing - Office")
        self.assertEqual(framing["description"],
                         "Scope Includes:\n\n- Cut a new 2'-8\" door opening in Wall 1 (assumed position)\n"
                         "- Install a prehung door\n\nNOTES:\n- Price assumes a standard hollow-core prehung door.")
        self.assertEqual(bud.clean_text("Seal \u2014 *now* \U0001F527 `x`"), "Seal - now x")
        self.assertLessEqual(len(bud.clean_name("Word " * 30)), 60)
        self.assertIsNone(bud.group_description([], []))

    def test_internal_notes_carry_conflicts_missing_info_and_source(self):
        p, _ = priced()
        framing, electrical = p["groups"]
        self.assertTrue(framing["notes_text"].startswith("- Source: test Page"))
        self.assertIn("Conflict C1: Wall 4 length", framing["notes_text"])
        self.assertIn("Check the header", framing["notes_text"])
        self.assertIn("Missing info M1: Nearest circuit", electrical["notes_text"])
        self.assertIn("Whole visit - missing info M2", framing["notes_text"])    # unassigned -> first group
        self.assertIn("Sub: Redland Electric", electrical["notes_text"])
        self.assertLessEqual(len(framing["notes_text"]), 4096)

    def test_scope_item_checks(self):
        plan = budget_plan(options=False)
        plan["groups"][1]["scope_items"] = ["S1"]          # S1 twice, S2 nowhere
        p, _ = priced(plan)
        self.assertTrue(any("S1 is in 2 groups" in w for w in p["warnings"]))
        self.assertTrue(any("S2" in w and "isn't in any group" in w for w in p["warnings"]))
        self.assertIn("Not budgeted anywhere: S2", p["groups"][0]["notes_text"])
        self.assertIn("also in Electrical", p["groups"][0]["notes_text"])

    def test_catalog_case_price_rejected_and_real_match_applied(self):
        plan = budget_plan(options=False)
        plan["groups"][0]["materials"] = [
            {"item": "Dryer vent elbow 4 in", "unit": "ea", "unit_cost": 8, "qty": 1, "qty_src": "dictated"},
            {"item": "2x4x8 stud", "unit": "ea", "unit_cost": 4.5, "qty": 6, "qty_src": "est"}]
        products = {"Dryer vent elbow 4 in": [{"name": "4 in. x 8 ft. Dryer Vent Elbow Kit", "unitCost": 69.99,
                                               "storeSkuNumber": "1"}],
                    "2x4x8 stud": [{"name": "2x4x8 Premium Stud Lumber", "unitCost": 3.98, "storeSkuNumber": "2",
                                    "brand": "Generic", "link": "https://homedepot.com/x"}]}
        p, jt = priced(plan, products)
        elbow, stud = item(p["groups"][0], "material", "elbow"), item(p["groups"][0], "material", "stud")
        self.assertEqual(elbow["unit_cost"], 8)                     # 3x / kit check kept the guess
        self.assertFalse(elbow["show_description"])                 # internal caveat stays off client documents
        self.assertIn("multipack", p["groups"][0]["notes_text"])
        self.assertEqual((stud["unit_cost"], stud["unit_price"]), (3.98, round(3.98 * 1.65, 2)))
        self.assertTrue(stud["show_description"])
        self.assertEqual(len([c for c in jt.calls if "homeDepotProducts" in c]), 2)

    def test_minimum_three_hours(self):
        plan = {"groups": [{"name": "Door Repair", "cost_code": "Windows & Doors", "scope_items": ["S1"],
                            "labor": [{"task": "adjust the door", "hours": 1}]}]}
        p, _ = priced(plan, scope={"S1": SCOPE["S1"]})
        hours = sum(i["qty"] for i in p["groups"][0]["items"] if i["kind"] == "labor")
        self.assertEqual(hours, 3)

    def test_bad_references_are_sent_back(self):
        parse = bud.parse_plan(bud.load_anchors(), QS)
        bad = budget_plan()
        bad["groups"][0]["materials"][1]["qty_ref"] = "Q99"
        bad["groups"][1]["sub_lines"][0]["anchor"] = "R9999"
        with self.assertRaises(ValueError) as e:
            parse(json.dumps(bad))
        self.assertIn("Q99", str(e.exception))
        self.assertIn("R9999", str(e.exception))
        self.assertEqual(len(parse("ok " + json.dumps(budget_plan()))["groups"]), 2)


class BudgetWriteTests(BuildBase):
    def setUp(self):
        super().setUp()
        self.run = answered_run(store=False)
        self.run["room_file"] = updated_room_file()
        self.run["build"] = {"page_content": sb.clean_page(copy.deepcopy(PAGE)), "usage": {"cost_usd": 0.1}}
        self.st = {}
        self.saves = 0

    def save(self):
        self.saves += 1
        json.dumps(self.run)                                         # the run must stay storable

    def go(self, answer=None):
        if answer is not False:
            self.claude(tr.sse(answer or budget_answer(qty_refs=False)))
        with mock.patch.object(bud, "_quantities", lambda rf, log: list(QS.values())):
            return bud.run_budget(self.run, self.st, "Office Remodel Scope - Draft from Oct 19, 2026 Site Visit",
                                  self.save, self.jobtread, self.log)

    def test_empty_job_gets_top_level_groups_in_one_call_each(self):
        st = self.go()
        self.assertTrue(st["done"], st)
        self.assertEqual(st["mode"], "top")
        top = {v["name"]: g for g, v in self.jt.groups.items() if v["parent"] is None}
        self.assertEqual(set(top), {"Demolition and Framing - Office", "Electrical", "Office Door Options"})
        self.assertTrue(all(v["how"] == "one call" for v in self.jt.groups.values()))
        framing = self.jt.items_in(top["Demolition and Framing - Office"])
        notes = [i for i in framing if i["name"] == v2.INTERNAL_NOTES_ITEM_NAME]
        self.assertEqual(len(notes), 1)
        self.assertEqual((notes[0]["unitCost"], notes[0]["unitPrice"], notes[0]["showDescription"]), (0, 0, False))
        self.assertIn("Office Remodel Scope - Draft from Oct 19, 2026 Site Visit", notes[0]["description"])
        labor = next(i for i in framing if i["name"].startswith("Labor"))
        self.assertEqual((labor["costTypeId"], labor["unitId"], labor["costCodeId"]),
                         (v2.COST_TYPE_LABOR, bud.UNIT_IDS["hr"], v2.COST_CODE_MAP["Framing"]))
        # Options: the parent picks 0 or 1 and has no NOTES item; each choice has one.
        opts = self.jt.groups[top["Office Door Options"]]
        self.assertEqual((opts["min"], opts["max"]), (0, 1))
        self.assertEqual(self.jt.items_in(top["Office Door Options"]), [])
        choice = next(g for g, v in self.jt.groups.items() if v["parent"] == top["Office Door Options"])
        self.assertEqual(self.jt.groups[choice]["name"], "Option 1 - Solid-core door")
        self.assertEqual(sum(1 for i in self.jt.items_in(choice) if i["name"] == v2.INTERNAL_NOTES_ITEM_NAME), 1)
        # Every write leaves client text clean.
        for v in self.jt.groups.values():
            self.assertNotIn("*", (v["name"] or "") + (v["description"] or ""))
            self.assertNotIn("\u2014", (v["name"] or "") + (v["description"] or ""))
        self.assertEqual(self.run["build"]["usage"]["cost_usd"], round(0.1 + st["usage"]["cost_usd"], 4))
        pub = bud.public(st)
        self.assertEqual((pub["groups"], pub["options"], pub["tbd_lines"], pub["job_number"]), (2, 1, 0, "00489"))
        self.assertEqual(pub["job_url"], f"https://app.jobtread.com/jobs/{tr.JOB['id']}")

    def test_job_with_groups_already_gets_a_parent_group(self):
        self.jt = FakeJT(existing=["Selections", "Site Scope Draft - Oct 19, 2026 Visit"])
        self.jobtread = (self.jt, tr.ORG)
        st = self.go()
        self.assertEqual((st["mode"], st["existing_groups"]), ("parent", 2))
        self.assertEqual(st["parent_name"], "Site Scope Draft - Oct 19, 2026 Visit (2)")
        parent = self.jt.by_name(st["parent_name"])
        mine = [v["name"] for v in self.jt.groups.values() if v["parent"] == parent]
        self.assertEqual(mine, ["Demolition and Framing - Office", "Electrical", "Office Door Options"])
        self.assertEqual(self.jt.items_in(self.jt.by_name("Selections")), [])    # Jason's group untouched
        self.assertEqual(bud.public(st)["parent_name"], st["parent_name"])

    def test_refused_one_call_writes_piece_by_piece_and_retry_finishes(self):
        self.jt.refuse_one_call = True
        self.jt.item_failures = 1
        st = self.go()
        self.assertFalse(st["done"])
        self.assertTrue(st["errors"])
        self.assertTrue(any("refused" in m for m in self.logs))
        bare = [i for i in self.jt.items if "unitId" in i or "customFieldValues" in i]
        self.assertEqual(bare, [])                                  # piece by piece uses the proven arguments
        groups, items = len(self.jt.groups), len(self.jt.items)
        self.claude()                                               # no second AI call
        st = self.go(answer=False)
        self.assertTrue(st["done"], st)
        self.assertEqual((len(self.jt.groups), len(self.jt.items)), (groups, items + 1))
        choice = self.jt.by_name("Option 1 - Solid-core door")
        self.assertEqual(self.jt.groups[choice]["parent"], self.jt.by_name("Office Door Options"))
        self.assertEqual(st["mode"], "top")                         # our own groups don't flip it to parent

    def test_stand_in_job(self):
        os.environ["SCOPE_BUDGET_JOB"] = "00035"
        self.addCleanup(os.environ.pop, "SCOPE_BUDGET_JOB", None)
        st = self.go()
        self.assertEqual((st["job"]["id"], st["job"]["number"], st["job"]["stand_in"]), ("22PDLqnyG8eT", "00035", True))
        self.assertTrue(all(c["createCostGroup"]["$"]["jobId"] == "22PDLqnyG8eT"
                            for c in self.jt.calls if "createCostGroup" in c))
        notes = next(i for i in self.jt.items if i["name"] == v2.INTERNAL_NOTES_ITEM_NAME)
        self.assertIn("Scope is from job 00489, budget written to a stand-in job", notes["description"])

    def test_no_scope_means_no_budget(self):
        self.run["room_file"]["scope_items"] = []
        self.run["build"]["page_content"]["trades"] = []
        st = self.go(answer=False)
        self.assertTrue(st["done"])
        self.assertIn("no work items", st["note"])
        self.assertEqual(self.jt.calls, [])

    def test_no_jobtread_connection(self):
        with self.assertRaises(bud.BudgetError):
            bud.run_budget(self.run, self.st, "t", self.save, None, self.log)


# ── The whole build ──────────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class RunBuildTests(BuildBase):
    def test_happy_path(self):
        fake = self.claude(tr.sse(build_answer()), tr.sse(budget_answer()))
        run = answered_run()
        sb.run_build(run, scope_runs._save, self.log, jobtread=self.jobtread)
        b = run["build"]
        self.assertEqual(b["state"], "done", b)
        self.assertEqual(run["room_file"]["rooms"][0]["walls"][3]["length"], {"in": 156, "src": "confirmed"})
        self.assertEqual(run["room_file"]["project"]["companycam_project_id"], tr.PROJECT_ID)   # code's, kept
        self.assertEqual(run["quick_room_file"]["scope_items"], [])
        self.assertGreater(b["usage"]["cost_usd"], 0)
        self.assertEqual(b["usage"]["calls"], 2)                    # the write and the budget
        self.assertEqual(b["usage"]["cost_usd"], round(2 * b["steps"]["budget"]["usage"]["cost_usd"], 4))
        body = fake.bodies[0]
        sent = json.dumps(body["messages"])
        self.assertIn("How long is Wall 4?", sent)
        self.assertNotIn("companycam_project_id", sent)
        self.assertIn("ROOM FILE FORMAT", body["system"][0]["text"])
        self.assertEqual(len(self.upload_calls), 1)
        self.assertTrue(self.upload_calls[0][0].endswith("floor-plan.png"))
        page = self.cc.created_pages[0]
        self.assertEqual(page["title"], "Office Remodel Scope - Draft from Oct 19, 2026 Site Visit")
        ids = [n["attrs"]["photoId"] for n in walk(page["tiptap_content"]) if n.get("type") == "flexPhoto"]
        self.assertIn("9001", ids)
        self.assertTrue(any(i.startswith("771") for i in ids))      # the drawings
        pub = sb.public(run)
        self.assertEqual(pub["steps"], {"write": True, "descriptions": True, "drawings": True, "page": True,
                                        "budget": True})
        self.assertEqual((pub["budget"]["groups"], pub["budget"]["options"], pub["budget"]["job_number"]),
                         (2, 1, "00489"))
        self.assertGreater(pub["budget"]["price"], pub["budget"]["cost"])
        budget_call = json.dumps(fake.bodies[1])
        self.assertIn("RATE ANCHORS", budget_call)
        self.assertIn("Office - wall area", budget_call)            # code's quantities go to the AI
        self.assertNotIn("page_content", json.dumps(pub))
        self.assertEqual(pub["descriptions"]["written"], 2)
        self.assertEqual(pub["drawings"]["in_companycam"], pub["drawings"]["total"])
        self.assertIn("turn sharing off", pub["sharing_note"])
        self.assertNotIn("page_content", json.dumps(pub))
        stored = scope_runs.load_run("r1")
        self.assertEqual(stored["build"]["state"], "done")

    def test_retry_redoes_only_what_failed(self):
        self.claude(tr.sse(build_answer()), tr.sse(budget_answer()))
        self.upload, self.upload_calls = fake_upload({"office-wall-2-elevation.png": None})
        self.cc.page_failures = [500]
        self.jt.refuse_one_call = True
        self.jt.item_failures = 1
        run = answered_run()
        sb.run_build(run, scope_runs._save, self.log, jobtread=self.jobtread)
        b = run["build"]
        self.assertEqual(b["state"], "partial")
        self.assertIn("drawings", b["error"])
        self.assertIn("page", b["error"])
        self.assertIn("budget", b["error"])
        self.assertTrue(b["steps"]["descriptions"]["done"])
        first_upload = self.upload_calls[0]
        groups_before, items_before = len(self.jt.groups), len(self.jt.items)

        self.upload, self.upload_calls = fake_upload()
        self.claude()   # no Claude call allowed on the retry
        sb.run_build(run, scope_runs._save, self.log, jobtread=self.jobtread)
        self.assertEqual(run["build"]["state"], "done", run["build"])
        self.assertEqual(len(self.jt.groups), groups_before)        # no group written twice
        self.assertEqual(len(self.jt.items), items_before + 1)      # only the line that failed
        self.assertEqual(self.upload_calls, [["office-wall-2-elevation.png"]])
        self.assertGreater(len(first_upload), 1)
        self.assertEqual(self.cc.descriptions["9001"].count("Door wall"), 1)
        self.assertEqual(len(self.cc.created_pages), 1)

    def test_unusable_answer_asked_again_then_fails_cleanly(self):
        fake = self.claude(tr.sse(json.dumps({"room_file": {}})), tr.sse("not json"))
        run = answered_run()
        sb.run_build(run, scope_runs._save, self.log, jobtread=self.jobtread)
        self.assertEqual(run["build"]["state"], "failed")
        self.assertIn("Tap Build", run["build"]["error"])
        self.assertEqual(len(fake.bodies), 2)
        self.assertIn("no page object", json.dumps(fake.bodies[1]["messages"]))
        self.assertEqual(self.cc.created_pages, [])
        self.assertEqual(self.jt.groups, {})

    def test_visit_with_no_rooms(self):
        rf = updated_room_file()
        rf["rooms"] = []
        self.claude(tr.sse(build_answer(room_file=rf)), tr.sse(budget_answer(qty_refs=False)))
        run = answered_run()
        sb.run_build(run, scope_runs._save, self.log, jobtread=self.jobtread)
        self.assertEqual(run["build"]["state"], "done")
        self.assertEqual(self.upload_calls, [])
        self.assertIn("nothing to draw", run["build"]["steps"]["drawings"]["note"])


# ── The build inside a run ───────────────────────────────────────────────────

@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class RunFlowTests(BuildBase):
    def start(self, answer):
        self.claude(tr.sse(answer), tr.sse(build_answer()), tr.sse(budget_answer()))
        status, body = scope_runs.start_run({"job": "00489"}, self.jt, tr.ORG, start_thread=tr.run_inline)
        return body["run_id"]

    def test_answers_start_the_build(self):
        run_id = self.start(tr.sample_answer())
        status, body = scope_runs.handle_post("/scope/runs/answers", {"run_id": run_id, "answers": {}},
                                              self.jt, tr.ORG, start_thread=tr.run_inline)
        self.assertEqual(status, 200)
        status, body = scope_runs.run_status({"run_id": run_id}, self.jt, tr.ORG)
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["build"]["state"], "done")
        self.assertEqual(body["build"]["page"]["title"], "Office Remodel Scope - Draft from Oct 19, 2026 Site Visit")
        self.assertEqual(body["build"]["budget"]["groups"], 2)
        self.assertIn("Demolition and Framing - Office", [g["name"] for g in self.jt.groups.values()])
        self.assertNotIn(run_id, scope_runs._running)
        status, body = scope_runs.handle_post("/scope/runs/answers", {"run_id": run_id, "answers": {}},
                                              tr.fake_jobtread(), tr.ORG, start_thread=tr.run_inline)
        self.assertEqual(status, 409)
        status, body = scope_runs.handle_post("/scope/runs/build", {"run_id": run_id}, tr.fake_jobtread(), tr.ORG,
                                              start_thread=tr.run_inline)
        self.assertEqual((status, body["build"]["state"]), (200, "done"))

    def test_no_questions_builds_straight_away(self):
        run_id = self.start(tr.sample_answer(questions=0))
        run = scope_runs.load_run(run_id)
        self.assertEqual(run["status"], "answered")
        self.assertEqual(run["build"]["state"], "done")

    def test_build_retry_endpoint(self):
        run = answered_run()
        run["build"] = {"state": "partial", "steps": {"write": {"done": True}}, "page_content": sb.clean_page(PAGE)}
        run["room_file"] = dict(updated_room_file(), project=run["room_file"]["project"])
        scope_runs._save(run)
        self.claude(tr.sse(budget_answer()))
        status, body = scope_runs.handle_post("/scope/runs/build", {"run_id": "r1"}, self.jt, tr.ORG,
                                              start_thread=tr.run_inline)
        self.assertEqual(status, 202)
        self.assertEqual(scope_runs.load_run("r1")["build"]["state"], "done")
        status, body = scope_runs.handle_post("/scope/runs/build", {"run_id": "nope"}, tr.fake_jobtread(), tr.ORG)
        self.assertEqual(status, 404)

    def test_build_cut_off_by_a_restart(self):
        run = answered_run()
        run["build"] = {"state": "building", "steps": {}}
        scope_runs._save(run)
        stored = scope_store.get(scope_runs._run_key("r1"))
        stored["updated_at"] = 0
        scope_store.put(scope_runs._run_key("r1"), stored)
        self.assertEqual(scope_runs.load_run("r1")["build"]["state"], "failed")

    def test_start_while_building_returns_that_run(self):
        run = answered_run()
        run["build"] = {"state": "building", "steps": {}}
        scope_runs._save(run)
        scope_runs._running.add("r1")
        status, body = scope_runs.start_run({"job": "00489"}, tr.fake_jobtread(), tr.ORG, start_thread=tr.run_inline)
        self.assertEqual((status, body["run_id"]), (200, "r1"))


if __name__ == "__main__":
    unittest.main()
