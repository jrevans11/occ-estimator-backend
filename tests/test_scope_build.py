"""Tests for Site Scope Tool phase 5: the build (scope_build.py), working
quantities (scope_quantities.py) and the build's place in a run (scope_runs.py).

Run from the repo root:  python3 -m unittest discover tests

Reuses phase 4's fakes (tests/test_scope_runs.py): CompanyCam in the Public
API's {data, errors, meta} envelope, Claude at its streaming layer. This file
adds photo descriptions (one per photo, replaced on every write - so the code
must read and join) and Page creation. The drawing upload itself is tested in
test_scope_uploads.py; here it is stubbed. No keys or network needed.
"""

import copy
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import companycam_api as cc  # noqa: E402
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


class BuildBase(tr.Base):
    def setUp(self):
        super().setUp()
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
        # The budget (phase 6) is stubbed here; tests/test_scope_budget.py runs it for real.
        self.real_budget = mock.patch.object(sb.scope_budget, "run_budget",
                                             side_effect=lambda run, state, *a, **k: dict(state, done=True))
        self.real_budget.start()
        self.addCleanup(mock.patch.stopall)
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


# ── The whole build ──────────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class RunBuildTests(BuildBase):
    def test_happy_path(self):
        fake = self.claude(tr.sse(build_answer()))
        run = answered_run()
        sb.run_build(run, scope_runs._save, self.log)
        b = run["build"]
        self.assertEqual(b["state"], "done", b)
        self.assertEqual(run["room_file"]["rooms"][0]["walls"][3]["length"], {"in": 156, "src": "confirmed"})
        self.assertEqual(run["room_file"]["project"]["companycam_project_id"], tr.PROJECT_ID)   # code's, kept
        self.assertEqual(run["quick_room_file"]["scope_items"], [])
        self.assertGreater(b["usage"]["cost_usd"], 0)
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
        self.assertEqual(pub["descriptions"]["written"], 2)
        self.assertEqual(pub["drawings"]["in_companycam"], pub["drawings"]["total"])
        self.assertIn("turn sharing off", pub["sharing_note"])
        self.assertNotIn("page_content", json.dumps(pub))
        stored = scope_runs.load_run("r1")
        self.assertEqual(stored["build"]["state"], "done")

    def test_retry_redoes_only_what_failed(self):
        self.claude(tr.sse(build_answer()))
        self.upload, self.upload_calls = fake_upload({"office-wall-2-elevation.png": None})
        self.cc.page_failures = [500]
        run = answered_run()
        sb.run_build(run, scope_runs._save, self.log)
        b = run["build"]
        self.assertEqual(b["state"], "partial")
        self.assertIn("drawings", b["error"])
        self.assertIn("page", b["error"])
        self.assertTrue(b["steps"]["descriptions"]["done"])
        first_upload = self.upload_calls[0]

        self.upload, self.upload_calls = fake_upload()
        self.claude()   # no Claude call allowed on the retry
        sb.run_build(run, scope_runs._save, self.log)
        self.assertEqual(run["build"]["state"], "done", run["build"])
        self.assertEqual(self.upload_calls, [["office-wall-2-elevation.png"]])
        self.assertGreater(len(first_upload), 1)
        self.assertEqual(self.cc.descriptions["9001"].count("Door wall"), 1)
        self.assertEqual(len(self.cc.created_pages), 1)

    def test_unusable_answer_asked_again_then_fails_cleanly(self):
        fake = self.claude(tr.sse(json.dumps({"room_file": {}})), tr.sse("not json"))
        run = answered_run()
        sb.run_build(run, scope_runs._save, self.log)
        self.assertEqual(run["build"]["state"], "failed")
        self.assertIn("Tap Build", run["build"]["error"])
        self.assertEqual(len(fake.bodies), 2)
        self.assertIn("no page object", json.dumps(fake.bodies[1]["messages"]))
        self.assertEqual(self.cc.created_pages, [])

    def test_visit_with_no_rooms(self):
        rf = updated_room_file()
        rf["rooms"] = []
        self.claude(tr.sse(build_answer(room_file=rf)))
        run = answered_run()
        sb.run_build(run, scope_runs._save, self.log)
        self.assertEqual(run["build"]["state"], "done")
        self.assertEqual(self.upload_calls, [])
        self.assertIn("nothing to draw", run["build"]["steps"]["drawings"]["note"])


# ── The build inside a run ───────────────────────────────────────────────────

@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class RunFlowTests(BuildBase):
    def start(self, answer):
        self.claude(tr.sse(answer), tr.sse(build_answer()))
        status, body = scope_runs.start_run({"job": "00489"}, tr.fake_jobtread(), tr.ORG, start_thread=tr.run_inline)
        return body["run_id"]

    def test_answers_start_the_build(self):
        run_id = self.start(tr.sample_answer())
        status, body = scope_runs.handle_post("/scope/runs/answers", {"run_id": run_id, "answers": {}},
                                              tr.fake_jobtread(), tr.ORG, start_thread=tr.run_inline)
        self.assertEqual(status, 200)
        status, body = scope_runs.run_status({"run_id": run_id}, tr.fake_jobtread(), tr.ORG)
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["build"]["state"], "done")
        self.assertEqual(body["build"]["page"]["title"], "Office Remodel Scope - Draft from Oct 19, 2026 Site Visit")
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
        self.claude()
        status, body = scope_runs.handle_post("/scope/runs/build", {"run_id": "r1"}, tr.fake_jobtread(), tr.ORG,
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
