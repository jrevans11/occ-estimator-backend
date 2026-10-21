"""Tests for the Site Scope Tool phase 1 preview (site_scope.py, companycam_api.py).

Run from the repo root:  python3 -m unittest discover tests

CompanyCam and JobTread are faked with responses shaped like the real ones
(CompanyCam's Public API v1 envelope, as returned for project 112543911 /
job 00768 on Oct 19, 2026), so no keys or
network are needed.
"""

import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import companycam_api as cc  # noqa: E402
import site_scope  # noqa: E402

KEY = "test-scope-key"
ORG = "22P9ppHePJKP"


def ts(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def env(data, next_cursor=None):
    """The new API's {data, errors, meta} envelope."""
    return {"data": data, "errors": [],
            "meta": {"next_cursor": next_cursor, "prev_cursor": None, "has_next": bool(next_cursor)}}


def photo(pid, captured_at, description=None):
    base = f"https://static.companycam.com/lambda/projects/117058039/photos/{pid}.jpeg"
    return {
        "id": pid,
        "project_id": "117058039",
        "captured_at": captured_at,
        "description": description,
        "uris": [
            {"type": "original", "uri": base + "?d=4032x4032", "url": base + "?d=4032x4032"},
            {"type": "web", "uri": base + "?d=400x400", "url": base + "?d=400x400"},
            {"type": "thumbnail", "uri": base + "?d=250x250", "url": base + "?d=250x250"},
        ],
    }


PROJECT = {
    "id": "117058039",
    "name": "Test (1)",
    "archived": False,
    "project_url": "https://app.companycam.com/projects/117058039",
    "capture_photo_deeplink": "ccam://camera/117058039/VGVzdCAoMSk%3D",
    "address": {"street_address_1": "3140 Wade Hampton Blvd", "city": "Taylors", "state": "SC"},
    "photo_count": 4,
}

# Oct 19 visit (EDT, UTC-4) plus one older photo from Oct 10.
PHOTOS = [
    photo("p4", "2026-10-19T16:25:00Z", "Wall 3"),
    photo("p3", "2026-10-19T16:21:30Z"),
    photo("p2", "2026-10-19T16:20:00Z"),
    photo("p1", "2026-10-10T14:00:00Z"),
]

VIDEOS = [
    {"id": "v2", "captured_at": "2026-10-19T16:29:00Z", "duration": 3},
    {"id": "v1", "captured_at": "2026-10-19T16:21:00Z", "duration": 120},
    {"id": "v0", "captured_at": "2026-10-10T14:05:00Z", "duration": 60},
]

PAGES = [
    {"id": "pg2", "title": "AI Walkthrough Summary"},
    {"id": "pg1", "title": "Site Visit Notes"},
]


def _t(text, italic=False):
    n = {"type": "text", "text": text}
    if italic:
        n["marks"] = [{"type": "italic"}]
    return n


def _h(text):
    return {"type": "heading", "attrs": {"level": 2}, "content": [_t(text)]}


def _p(text, italic=False):
    return {"type": "paragraph", "content": [_t(text, italic)]}


# One note during the Oct 19 visit, and one recorded Oct 20 about the Oct 10 photos.
NOTES_DOC = {"type": "doc", "content": [
    _p("Word-for-word notes dictated on site visits, added by the OCC scope page.", True),
    _h("Oct 19, 2026 - 12:22 PM"),
    _p("Recorded 12:22:00 PM to 12:23:10 PM (1:10 of audio). ", True),
    _p("Office wall 1 is 13 feet."),
    _h("Oct 20, 2026 - 9:05 AM"),
    _p("Recorded 9:05:00 AM to 9:06:00 AM (1:00 of audio). Filed under the Oct 10, 2026 visit. ", True),
    _p("Back porch from the earlier photos. The bottom step is rotted."),
]}

TODAY = "2026-10-20"

SEARCH = [
    PROJECT,
    {"id": "999", "name": "3140 Wade Hampton Blvd", "archived": False, "photo_count": 2,
     "project_url": "https://app.companycam.com/projects/999",
     "address": {"street_address_1": "3140 Wade Hampton Blvd"}},
    {"id": "998", "name": "Old archived copy", "archived": True,
     "address": {"street_address_1": "3140 Wade Hampton Blvd"}},
    {"id": "997", "name": "Somewhere else", "archived": False,
     "address": {"street_address_1": "3140 Wade Hampton Blvd Apt 2"}},
]

JOB = {
    "id": "22PDLqnyG8eT", "number": "00035", "name": "Test job- JT", "companycamId": "117058039",
    "location": {"address": "3122 Wade Hampton Blvd, Taylors, SC 29687, USA"},
}


class FakeCompanyCam:
    """Stands in for companycam_api._get. Records every call."""

    def __init__(self, videos_status=None, pages=PAGES, transcripts=None):
        self.calls = []
        self.videos_status = videos_status
        self.pages_status = None
        self.transcript_field_missing = False
        self.pages = pages
        self.transcripts = transcripts if transcripts is not None else {"v1": "Wall 1 is twelve feet."}

    def __call__(self, path, params=None):
        self.calls.append((path, params))
        if path == "/projects/117058039":
            return env(PROJECT)
        if path == "/projects/117058039/photos":
            return env(PHOTOS)
        if path == "/projects/117058039/videos":
            if self.videos_status:
                raise cc.CompanyCamError(f"HTTP {self.videos_status}", self.videos_status)
            return env(VIDEOS)
        if path.startswith("/videos/"):
            vid = path.rsplit("/", 1)[1]
            if self.transcript_field_missing:
                return env({"id": vid})
            text = self.transcripts.get(vid)
            transcript = {"text": text, "source": "mux_generated", "last_edited_at": None} if text else None
            return env({"id": vid, "transcript": transcript})
        if path == "/projects/117058039/pages":
            if self.pages_status:
                raise cc.CompanyCamError(f"HTTP {self.pages_status}", self.pages_status)
            return env(self.pages)
        if path == "/pages/pg1":
            return env({"id": "pg1", "title": "Site Visit Notes", "tiptap_content": NOTES_DOC})
        if path == "/projects/search":
            return env(SEARCH)
        raise cc.CompanyCamError(f"unexpected path {path}", 404)


def fake_jobtread(job=JOB):
    calls = []

    def q(query):
        calls.append(query)
        if "job" in query:
            return {"job": job if query["job"]["$"]["id"] == job["id"] else None}
        where = query["organization"]["jobs"]["$"]["where"]
        return {"organization": {"jobs": {"nodes": [job] if where[1] == job["number"] else []}}}

    q.calls = calls
    return q


class BaseCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"COMPANYCAM_API_KEY": "cc-test", "SCOPE_API_KEY": KEY})
        env.start()
        self.addCleanup(env.stop)
        self.cc = FakeCompanyCam()
        p = mock.patch.object(cc, "_get", self.cc)
        p.start()
        self.addCleanup(p.stop)
        d = mock.patch.object(site_scope, "today_local", return_value=TODAY)
        d.start()
        self.addCleanup(d.stop)

    def preview(self, job="00035", date="", jt=None):
        return site_scope.build_visit_preview(job, jt or fake_jobtread(), ORG, date)


class TimeTests(unittest.TestCase):
    def test_eastern_daylight_and_standard_time(self):
        # 23:30 UTC on Oct 19 is still Oct 19 at 7:30 PM in Greenville (EDT).
        self.assertEqual(site_scope.to_local(ts("2026-10-19T23:30:00Z")), datetime(2026, 10, 19, 19, 30))
        # 03:30 UTC on Oct 20 is 11:30 PM on Oct 19.
        self.assertEqual(site_scope.to_local(ts("2026-10-20T03:30:00Z")).day, 19)
        # January is EST (UTC-5).
        self.assertEqual(site_scope.to_local(ts("2026-01-15T17:00:00Z")).hour, 12)
        # DST change days: Mar 8 2026 at 07:00 UTC flips to EDT; Nov 1 2026 at 06:00 UTC flips back.
        self.assertEqual(site_scope.to_local(ts("2026-03-08T06:59:00Z")).hour, 1)
        self.assertEqual(site_scope.to_local(ts("2026-03-08T07:00:00Z")).hour, 3)
        self.assertEqual(site_scope.to_local(ts("2026-11-01T05:59:00Z")).hour, 1)
        self.assertEqual(site_scope.to_local(ts("2026-11-01T06:00:00Z")).hour, 1)

    def test_parse_timestamp_forms(self):
        self.assertEqual(cc.parse_timestamp(1787077547), 1787077547)
        self.assertEqual(cc.parse_timestamp("1787077547"), 1787077547)
        self.assertEqual(cc.parse_timestamp("2026-08-18T18:25:47Z"), ts("2026-08-18T18:25:47Z"))
        self.assertIsNone(cc.parse_timestamp(None))
        self.assertIsNone(cc.parse_timestamp("not a date"))


class AuthTests(unittest.TestCase):
    def test_closed_when_no_key_configured(self):
        with mock.patch.dict(os.environ, {"SCOPE_API_KEY": ""}):
            status, body = site_scope.handle_preview_request({"job": ["00035"]}, {}, fake_jobtread(), ORG)
        self.assertEqual(status, 503)
        self.assertFalse(body["ok"])

    def test_wrong_or_missing_key(self):
        with mock.patch.dict(os.environ, {"SCOPE_API_KEY": KEY}):
            self.assertEqual(site_scope.handle_preview_request({"key": ["nope"]}, {}, fake_jobtread(), ORG)[0], 401)
            self.assertEqual(site_scope.handle_preview_request({}, {}, fake_jobtread(), ORG)[0], 401)

    def test_missing_companycam_key(self):
        with mock.patch.dict(os.environ, {"SCOPE_API_KEY": KEY, "COMPANYCAM_API_KEY": ""}):
            status, body = site_scope.handle_preview_request(
                {"key": [KEY], "job": ["00035"]}, {}, fake_jobtread(), ORG)
        self.assertEqual(status, 503)
        self.assertIn("COMPANYCAM_API_KEY", body["error"])


class JobLookupTests(BaseCase):
    def test_by_number_is_zero_padded(self):
        jt = fake_jobtread()
        self.assertEqual(self.preview(job="35", jt=jt)["job"]["number"], "00035")
        self.assertEqual(jt.calls[0]["organization"]["jobs"]["$"]["where"], ["number", "00035"])

    def test_by_id(self):
        self.assertEqual(self.preview(job="22PDLqnyG8eT")["job"]["id"], "22PDLqnyG8eT")

    def test_unknown_job(self):
        with self.assertRaises(site_scope.PreviewError) as e:
            self.preview(job="99999")
        self.assertEqual(e.exception.status, 404)

    def test_no_job_given(self):
        with self.assertRaises(site_scope.PreviewError) as e:
            self.preview(job="")
        self.assertEqual(e.exception.status, 400)

    def test_job_without_companycam_project(self):
        with self.assertRaises(site_scope.PreviewError) as e:
            self.preview(jt=fake_jobtread(dict(JOB, companycamId=None)))
        self.assertEqual(e.exception.status, 404)
        self.assertIn("no CompanyCam project", str(e.exception))


class PreviewTests(BaseCase):
    def test_default_is_newest_visit_day(self):
        r = self.preview()
        self.assertEqual(r["visit_date"], "2026-10-19")
        self.assertEqual(r["photos"]["count"], 3)
        self.assertEqual([p["id"] for p in r["photos"]["items"]], ["p2", "p3", "p4"])  # capture order
        self.assertEqual(r["photos"]["items"][0]["captured_at"], "2026-10-19 12:20 PM")
        self.assertTrue(r["photos"]["items"][0]["thumbnail"].endswith("?d=250x250"))
        self.assertEqual(r["photos"]["items"][2]["description"], "Wall 3")
        self.assertEqual(r["companycam_project"]["photo_count_on_project"], 4)
        self.assertEqual(r["companycam_project"]["camera_url"], "ccam://camera/117058039")

    def test_date_all_and_specific_date(self):
        self.assertEqual(self.preview(date="all")["photos"]["count"], 4)
        r = self.preview(date="2026-10-10")
        self.assertEqual([p["id"] for p in r["photos"]["items"]], ["p1"])
        self.assertEqual([v["id"] for v in r["videos"]["items"]], ["v0"])

    def test_bad_date(self):
        with self.assertRaises(site_scope.PreviewError) as e:
            self.preview(date="10/19/2026")
        self.assertEqual(e.exception.status, 400)

    def test_videos_short_skipped_and_transcripts(self):
        vids = {v["id"]: v for v in self.preview()["videos"]["items"]}
        self.assertEqual(set(vids), {"v1", "v2"})
        self.assertTrue(vids["v2"]["skipped"])
        self.assertEqual(vids["v1"]["transcript"], "ready")
        # Short video's transcript is never fetched.
        self.assertNotIn("/videos/v2", [c[0] for c in self.cc.calls])

    def test_transcript_not_ready(self):
        self.cc.transcripts = {}
        vids = {v["id"]: v for v in self.preview()["videos"]["items"]}
        self.assertEqual(vids["v1"]["transcript"], "not ready")

    def test_video_listing_failure_is_a_warning_not_a_failure(self):
        self.cc.videos_status = 404
        r = self.preview()
        self.assertTrue(r["ok"])
        self.assertEqual(r["videos"]["count"], 0)
        self.assertTrue(any("videos" in w for w in r["warnings"]))
        self.assertEqual(r["photos"]["count"], 3)

    def test_transcript_field_missing_is_unknown_not_not_ready(self):
        self.cc.transcript_field_missing = True
        vids = {v["id"]: v for v in self.preview()["videos"]["items"]}
        self.assertEqual(vids["v1"]["transcript"], "unknown (CompanyCam did not say)")

    def test_pages_error_is_a_warning(self):
        self.cc.pages_status = 403
        r = self.preview()
        self.assertEqual(r["site_visit_notes_page"], {"exists": None})
        self.assertTrue(any("Pages" in w for w in r["warnings"]))

    def test_batch_uploaded_photos_warning(self):
        same_minute = [photo(f"b{i}", f"2026-08-18T18:25:{i:02d}Z") for i in range(15)]
        with mock.patch.object(sys.modules[__name__], "PHOTOS", same_minute):
            r = self.preview(date="2026-08-18")
        self.assertEqual(r["photos"]["count"], 15)
        self.assertTrue(any("15 of 15 photos" in w and "uploaded in a batch" in w for w in r["warnings"]))
        # A normal visit (photos minutes apart) gets no such warning.
        self.assertFalse(any("batch" in w for w in self.preview()["warnings"]))

    def test_notes_page_found_or_missing(self):
        self.assertEqual(self.preview()["site_visit_notes_page"], {"exists": True, "id": "pg1", "title": "Site Visit Notes"})
        self.cc.pages = PAGES[:1]
        self.assertEqual(self.preview()["site_visit_notes_page"], {"exists": False})

    def test_duplicate_projects_at_same_address(self):
        others = self.preview()["other_projects_at_address"]
        # Excludes itself, the archived copy, and a different street line.
        self.assertEqual([o["id"] for o in others], ["999"])

    def test_summary_line(self):
        s = self.preview()["summary"]
        self.assertEqual(
            s,
            "Job 00035: 3 photos, 1 note and 1 video (1 with a transcript ready) from Oct 19, 2026. "
            "The project already has a Site Visit Notes Page. "
            "Heads up: 1 other CompanyCam project(s) share this address.")

    def test_preview_never_writes(self):
        self.preview()
        # _get is the only call path used; no POST/PUT helper exists yet, but
        # make sure nothing outside the read endpoints was touched.
        paths = {c[0] for c in self.cc.calls}
        self.assertTrue(paths <= {"/projects/117058039", "/projects/117058039/photos",
                                  "/projects/117058039/videos", "/videos/v1",
                                  "/projects/117058039/pages", "/pages/pg1", "/projects/search"})

    # ── "Which visit?" picker and notes filed under a visit ──

    def test_visit_days_list(self):
        r = self.preview()
        self.assertEqual(r["today"], TODAY)
        self.assertEqual(r["visits"], [
            # Today is always offered; the Oct 20 note is filed under Oct 10, so today is empty.
            {"date": "2026-10-20", "today": True, "photos": 0, "notes": 0, "videos": 0},
            # The 3-second video is an accidental tap and isn't counted.
            {"date": "2026-10-19", "today": False, "photos": 3, "notes": 1, "videos": 1},
            {"date": "2026-10-10", "today": False, "photos": 1, "notes": 1, "videos": 1},
        ])
        self.assertEqual(r["visit_date"], "2026-10-19")   # newest day with something, not empty today

    def test_notes_for_the_visit(self):
        notes = self.preview()["notes"]
        self.assertEqual(notes["count"], 1)
        self.assertEqual(notes["items"][0]["snippet"], "Office wall 1 is 13 feet.")
        self.assertEqual(notes["items"][0]["recorded_at"], "2026-10-19 12:22 PM")
        self.assertFalse(notes["items"][0]["filed_later"])
        earlier = self.preview(date="2026-10-10")["notes"]["items"]
        self.assertEqual([(n["visit_date"], n["filed_later"], n["recorded_at"]) for n in earlier],
                         [("2026-10-10", True, "2026-10-20 09:05 AM")])
        self.assertEqual(self.preview(date="all")["notes"]["count"], 2)
        self.assertEqual(self.preview(date=TODAY)["notes"]["count"], 0)

    def test_note_filed_under_the_newest_visit_sets_the_default(self):
        doc = {"type": "doc", "content": [_h("Oct 20, 2026 - 9:05 AM"),
                                          _p("Typed note, saved 9:05:10 AM.", True), _p("Today's note.")]}
        with mock.patch.dict(globals(), {"NOTES_DOC": doc}):
            r = self.preview()
        self.assertEqual(r["visit_date"], TODAY)
        self.assertEqual(r["visits"][0], {"date": TODAY, "today": True, "photos": 0, "notes": 1, "videos": 0})

    def test_tool_drawings_are_not_visit_photos(self):
        drawing = photo("d1", "2026-10-20T12:00:00Z", "Office - Floor Plan - drawn by the Site Scope Tool.")
        with mock.patch.object(sys.modules[__name__], "PHOTOS", PHOTOS + [drawing]):
            r = self.preview()
            self.assertEqual(r["visit_date"], "2026-10-19")
            self.assertEqual(r["visits"][0]["photos"], 0)
            self.assertEqual(self.preview(date=TODAY)["photos"]["count"], 0)

    def test_picked_day_stays_on_a_long_list(self):
        many = [photo(f"m{i}", f"2026-09-{i:02d}T14:00:00Z") for i in range(1, 29)]
        with mock.patch.object(sys.modules[__name__], "PHOTOS", many):
            r = self.preview(date="2026-09-01")
        dates = [d["date"] for d in r["visits"]]
        self.assertEqual(len(dates), site_scope.MAX_VISIT_DAYS + 1)
        self.assertEqual(dates[-1], "2026-09-01")

    def test_unreadable_notes_page_is_a_warning(self):
        with mock.patch.dict(globals(), {"NOTES_DOC": None}):
            self.cc.pages = [{"id": "pg9", "title": "Site Visit Notes"}]   # the fake doesn't serve pg9
            r = self.preview()
        self.assertEqual(r["notes"]["count"], 0)
        self.assertTrue(any("Site Visit Notes Page" in w for w in r["warnings"]))


class PhotoPagingTests(BaseCase):
    def test_follows_cursor_until_no_next(self):
        full = [photo(f"x{i}", "2026-10-19T16:00:00Z") for i in range(cc.PAGE_LIMIT)]
        pages = {None: env(full, "c2"), "c2": env(full[:5])}
        calls = []

        def fake(path, params=None):
            calls.append(dict(params))
            return pages[params.get("after")]

        with mock.patch.object(cc, "_get", fake):
            self.assertEqual(len(cc.list_project_photos("1")), cc.PAGE_LIMIT + 5)
        self.assertEqual([c.get("after") for c in calls], [None, "c2"])
        self.assertTrue(all(c["limit"] == 100 for c in calls))

    def test_stops_paging_once_older_than_cutoff(self):
        old = [photo(f"o{i}", "2026-01-01T12:00:00Z") for i in range(cc.PAGE_LIMIT)]
        calls = []

        def fake(path, params=None):
            calls.append(params.get("after"))
            return env(old, "more")

        with mock.patch.object(cc, "_get", fake):
            cc.list_project_photos("1", stop_before_ts=ts("2026-10-01T00:00:00Z"))
        self.assertEqual(calls, [None])

    def test_real_00768_photo_shape(self):
        """A photo exactly as the new API returned it for job 00768 on Oct 19."""
        real = {
            "id": "3481825879", "project_id": "112543911", "status": "active", "origin": "external",
            "captured_at": "2026-08-18T18:25:47Z", "processing_status": "processed", "description": None,
            "uris": [{"type": "original", "uri": "https://static.companycam.com/a.jpeg?d=4032x4032",
                      "url": "https://static.companycam.com/a.jpeg?d=4032x4032"},
                     {"type": "thumbnail", "uri": "https://static.companycam.com/a.jpeg?d=250x250",
                      "url": "https://static.companycam.com/a.jpeg?d=250x250"}],
        }
        self.assertEqual(site_scope._fmt_local(cc.parse_timestamp(real["captured_at"])), "2026-08-18 02:25 PM")
        self.assertTrue(cc.photo_uri(real, "thumbnail").endswith("d=250x250"))


class ClientErrorTests(unittest.TestCase):
    """The real _request: envelope errors and 401 wording."""

    def call_with_http_error(self, code, body):
        err = urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body.encode()))
        with mock.patch.dict(os.environ, {"COMPANYCAM_API_KEY": "k"}), \
                mock.patch.object(cc.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(cc.CompanyCamError) as e:
                cc.get_project("1")
        return e.exception

    def test_401_explains_the_token(self):
        e = self.call_with_http_error(401, json.dumps(
            {"data": {}, "errors": [{"code": "invalid_token", "message": "The access token is invalid"}], "meta": {}}))
        self.assertEqual(e.status, 401)
        self.assertIn("invalid_token: The access token is invalid", str(e))
        self.assertIn("Personal Access Token", str(e))

    def test_403_scope_message_passes_through(self):
        e = self.call_with_http_error(403, json.dumps(
            {"errors": [{"code": "insufficient_scope", "message": "missing pages:read"}]}))
        self.assertIn("insufficient_scope: missing pages:read", str(e))

    def test_calls_new_api_with_bearer_token(self):
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            seen["url"], seen["auth"] = req.full_url, req.headers.get("Authorization")
            return Resp(json.dumps(env(PROJECT)).encode())

        with mock.patch.dict(os.environ, {"COMPANYCAM_API_KEY": "tok"}), \
                mock.patch.object(cc.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(cc.get_project("117058039")["name"], "Test (1)")
        self.assertEqual(seen["url"], "https://app.companycam.com/public_api/v1/projects/117058039")
        self.assertEqual(seen["auth"], "Bearer tok")


class HttpEndpointTests(BaseCase):
    """Round trip through the real app.py Handler and a real local server."""

    @classmethod
    def setUpClass(cls):
        import app  # noqa: F401  (imports estimate_v2_draft etc. - no network at import)
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

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), json.loads(e.read())

    def test_preview_with_key_in_query(self):
        status, ctype, body = self.get(f"/scope/preview?job=00035&key={KEY}")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/json")
        self.assertEqual(body["photos"]["count"], 3)
        self.assertTrue(body["summary"].startswith("Job 00035: 3 photos"))

    def test_preview_with_key_in_header(self):
        status, _, body = self.get("/scope/preview?job=00035&date=all", {"X-Scope-Key": KEY})
        self.assertEqual(status, 200)
        self.assertEqual(body["photos"]["count"], 4)

    def test_preview_rejects_wrong_key(self):
        status, _, body = self.get("/scope/preview?job=00035&key=wrong")
        self.assertEqual(status, 401)
        self.assertFalse(body["ok"])

    def test_key_is_redacted_from_request_log(self):
        with mock.patch("builtins.print") as printed:
            self.get(f"/scope/preview?job=00035&key={KEY}&date=all")
        logged = " ".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn("key=REDACTED&date=all", logged)
        self.assertNotIn(KEY, logged)

    def test_companycam_outage_is_502_not_crash(self):
        def down(path, params=None):
            raise cc.CompanyCamError("CompanyCam GET failed: timed out")

        with mock.patch.object(cc, "_get", down):
            status, _, body = self.get(f"/scope/preview?job=00035&key={KEY}")
        self.assertEqual(status, 502)
        self.assertIn("Could not open CompanyCam project", body["error"])
        # Server still answers afterwards.
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            self.assertEqual(r.read(), b"OCC Estimator Backend is running!")


if __name__ == "__main__":
    unittest.main()
