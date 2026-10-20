"""Tests for getting drawings into CompanyCam (scope_uploads.py and the
photo calls in companycam_api.py).

Run from the repo root:  python3 -m unittest discover tests

CompanyCam is faked. The round-trip test runs the real app.py Handler on a
local port and has the fake CompanyCam download each drawing from it, the
way the real one does from Render.
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import companycam_api as cc  # noqa: E402
import scope_uploads as su  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

KEY = "test-scope-key"


def drawing(name="office-floor-plan.png", title="Office - Floor Plan"):
    return {"filename": name, "title": title, "png": b"\x89PNG\r\n\x1a\nfake"}


class FakeCompanyCamWrites:
    """Stands in for companycam_api._request, answering in the Public API's
    {data, errors, meta} envelope.

    POST /projects/<id>/photos records the photo; optionally downloads its
    uri like CompanyCam does. GET /photos/<id> reports processing_status,
    "pending" for the first `pending_checks` looks, then `final`."""

    def __init__(self, download=False, pending_checks=1, final="processed", refuse=()):
        self.download = download
        self.pending_checks = pending_checks
        self.final = final
        self.refuse = set(refuse)
        self.posts, self.checks, self.downloaded = [], {}, {}
        self.lock = threading.Lock()

    def __call__(self, method, path, params=None, body=None):
        if method == "POST" and path.endswith("/photos"):
            photo = body["photo"]
            name = photo["uri"].rsplit("/", 1)[1]
            if name in self.refuse:
                raise cc.CompanyCamError("CompanyCam POST returned HTTP 422: uri invalid", 422)
            with self.lock:
                self.posts.append((path, body))
                pid = f"ph{len(self.posts)}"
            if self.download:
                with urllib.request.urlopen(photo["uri"], timeout=10) as r:
                    self.downloaded[pid] = (r.headers.get("Content-Type"), r.read())
            return {"data": {"id": pid, "processing_status": "pending", "uris": []}, "errors": [], "meta": {}}
        if method == "GET" and path.startswith("/photos/"):
            pid = path.rsplit("/", 1)[1]
            n = self.checks[pid] = self.checks.get(pid, 0) + 1
            status = "pending" if n <= self.pending_checks else self.final
            uris = [{"type": "web", "uri": f"https://img.companycam.com/{pid}-web.png"}] if status == "processed" else []
            return {"data": {"id": pid, "processing_status": status, "uris": uris}, "errors": [], "meta": {}}
        raise cc.CompanyCamError(f"unexpected {method} {path}", 404)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Base(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"COMPANYCAM_API_KEY": "cc-test", "SCOPE_API_KEY": KEY,
                                           "RENDER_EXTERNAL_URL": "https://occ-estimator.onrender.com"})
        env.start()
        self.addCleanup(env.stop)
        su._files.clear()
        self.addCleanup(su._files.clear)


class CompanyCamPhotoCallTests(Base):
    def test_create_project_photo_body_matches_public_api(self):
        seen = []
        reply = {"data": {"id": "1", "processing_status": "pending"}, "errors": [], "meta": {}}
        with mock.patch.object(cc, "_request", lambda *a, **k: seen.append((a, k)) or reply):
            photo = cc.create_project_photo("117058039", "https://x/y.png", captured_at=1760900000,
                                            description="Plan")
        (method, path), kwargs = seen[0]
        self.assertEqual((method, path), ("POST", "/projects/117058039/photos"))
        # The Public API takes captured_at as an ISO 8601 string, not Unix seconds.
        self.assertEqual(kwargs["body"], {"photo": {"uri": "https://x/y.png",
                                                    "captured_at": "2025-10-19T18:53:20Z",
                                                    "description": "Plan"}})
        self.assertEqual(photo["id"], "1")

    def test_get_photo_unwraps_envelope(self):
        reply = {"data": {"id": "7", "processing_status": "processed"}, "errors": [], "meta": {}}
        with mock.patch.object(cc, "_request", lambda *a, **k: reply):
            self.assertEqual(cc.get_photo("7"), {"id": "7", "processing_status": "processed"})

    def test_request_sends_json_and_auth(self):
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"id": "9"}'

        def fake_urlopen(req, timeout):
            captured["req"] = req
            return Resp()

        with mock.patch.object(cc.urllib.request, "urlopen", fake_urlopen):
            out = cc._post("/projects/1/photos", {"photo": {"uri": "u"}})
        req = captured["req"]
        self.assertEqual(out, {"id": "9"})
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("Authorization"), "Bearer cc-test")
        self.assertEqual(json.loads(req.data), {"photo": {"uri": "u"}})

    def test_photo_is_ready(self):
        self.assertTrue(cc.photo_is_ready({"processing_status": "processed"}))
        self.assertFalse(cc.photo_is_ready({"processing_status": "pending"}))
        self.assertIsNone(cc.photo_is_ready({"processing_status": "processing_error"}))
        self.assertTrue(cc.photo_is_ready({"uris": [{"type": "original", "uri": "https://x"}]}))
        self.assertFalse(cc.photo_is_ready({}))


class TempFileTests(Base):
    def test_put_and_get(self):
        token = su.put_temp_file(b"abc", "image/png", "a.png")
        self.assertGreaterEqual(len(token), 40)          # unguessable
        self.assertEqual(su.get_temp_file(token)["data"], b"abc")
        self.assertEqual(su.fetch_count(token), 1)
        self.assertIsNone(su.get_temp_file("nope"))

    def test_expires(self):
        token = su.put_temp_file(b"abc", "image/png", "a.png", seconds=60, now=1000)
        self.assertIsNotNone(su.get_temp_file(token, now=1059))
        self.assertIsNone(su.get_temp_file(token, now=1061))

    def test_memory_cap_drops_oldest(self):
        with mock.patch.object(su, "MAX_TEMP_BYTES", 10):
            a = su.put_temp_file(b"123456", "image/png", "a.png", now=1000)
            b = su.put_temp_file(b"123456", "image/png", "b.png", now=1001)
        self.assertIsNone(su.get_temp_file(a, now=1002))
        self.assertIsNotNone(su.get_temp_file(b, now=1002))

    def test_file_request(self):
        token = su.put_temp_file(b"png-bytes", "image/png", "plan.png")
        self.assertEqual(su.handle_file_request(f"/scope/files/{token}/plan.png"), (200, "image/png", b"png-bytes"))
        self.assertEqual(su.handle_file_request("/scope/files/wrong/plan.png")[0], 404)
        self.assertEqual(su.handle_file_request("/scope/files/")[0], 404)

    def test_public_base_url(self):
        self.assertEqual(su.public_base_url(), "https://occ-estimator.onrender.com")
        with mock.patch.dict(os.environ, {"SCOPE_PUBLIC_URL": "https://example.test/"}):
            self.assertEqual(su.public_base_url(), "https://example.test")
        with mock.patch.dict(os.environ, {"RENDER_EXTERNAL_URL": ""}):
            self.assertEqual(su.public_base_url(), "")


class UploadTests(Base):
    def upload(self, fake, drawings=None, wait=60):
        clock = Clock()
        with mock.patch.object(cc, "_request", fake):
            return su.upload_drawings("117058039", drawings or [drawing()], "https://occ.example",
                                      lambda d: f"{d['title']} - test", wait_seconds=wait, poll_seconds=5,
                                      sleep=clock.sleep, clock=clock)

    def test_sends_url_and_waits_until_processed(self):
        fake = FakeCompanyCamWrites(pending_checks=2)
        [r] = self.upload(fake)
        path, body = fake.posts[0]
        self.assertEqual(path, "/projects/117058039/photos")
        self.assertTrue(body["photo"]["uri"].startswith("https://occ.example/scope/files/"))
        self.assertTrue(body["photo"]["uri"].endswith("/office-floor-plan.png"))
        self.assertEqual(body["photo"]["description"], "Office - Floor Plan - test")
        self.assertEqual(r["status"], "in CompanyCam")
        self.assertEqual(r["photo_id"], "ph1")
        self.assertEqual(r["photo_url"], "https://img.companycam.com/ph1-web.png")
        self.assertEqual(fake.checks["ph1"], 3)
        self.assertFalse(r["companycam_downloaded_it"])   # this fake never fetched the URL

    def test_one_refused_upload_does_not_stop_the_others(self):
        fake = FakeCompanyCamWrites(refuse={"a.png"})
        results = self.upload(fake, [drawing("a.png", "A"), drawing("b.png", "B")])
        self.assertEqual([r["status"] for r in results], ["upload refused", "in CompanyCam"])
        self.assertIn("422", results[0]["error"])

    def test_processing_error(self):
        [r] = self.upload(FakeCompanyCamWrites(final="processing_error"))
        self.assertIn("could not process", r["status"])

    def test_gives_up_waiting(self):
        [r] = self.upload(FakeCompanyCamWrites(pending_checks=999), wait=20)
        self.assertEqual(r["status"], "still processing after 20 seconds - check the project")

    def test_status_checks_that_fail_keep_waiting(self):
        fake = FakeCompanyCamWrites()
        real = fake.__call__
        calls = {"n": 0}

        def flaky(method, path, params=None, body=None):
            if method == "GET":
                calls["n"] += 1
                if calls["n"] == 1:
                    raise cc.CompanyCamError("timed out")
            return real(method, path, params, body)

        [r] = self.upload(flaky)
        self.assertEqual(r["status"], "in CompanyCam")


class TestEndpointGlueTests(Base):
    def setUp(self):
        super().setUp()
        if su._run_lock.locked():
            su._run_lock.release()

    def call(self, query, start=None):
        params = {k: [v] for k, v in query.items()}
        return su.handle_drawing_test_request(params, {}, start_thread=start or (lambda f: None))

    def test_needs_the_key(self):
        self.assertEqual(self.call({})[0], 401)
        self.assertEqual(self.call({"key": "wrong"})[0], 401)
        with mock.patch.dict(os.environ, {"SCOPE_API_KEY": ""}):
            self.assertEqual(self.call({"key": KEY})[0], 503)

    def test_needs_companycam_key_and_public_address(self):
        with mock.patch.dict(os.environ, {"COMPANYCAM_API_KEY": ""}):
            self.assertEqual(self.call({"key": KEY})[0], 503)
        with mock.patch.dict(os.environ, {"RENDER_EXTERNAL_URL": ""}):
            status, body = self.call({"key": KEY})
        self.assertEqual(status, 503)
        self.assertIn("public address", body["error"])

    def test_starts_once_and_refuses_a_second_run(self):
        started = []
        status, body = self.call({"key": KEY}, start=started.append)
        self.assertEqual(status, 202)
        self.assertIn("&status=1", body["message"])
        self.assertEqual(len(started), 1)
        self.assertEqual(self.call({"key": KEY}, start=started.append)[0], 409)
        su._run_lock.release()

    def test_status_view_hides_temporary_links(self):
        with mock.patch.object(su, "_last_run", {"state": "done", "uploads": [{"status": "in CompanyCam",
                                                                                "temp_url": "https://secret"}]}):
            status, body = self.call({"key": KEY, "status": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(body["last_run"]["uploads"], [{"status": "in CompanyCam"}])

    def test_failed_run_is_recorded_not_raised(self):
        with mock.patch("builtins.print"):
            record = su.run_drawing_test(base_url="https://occ.example", sample_file="/no/such/file.json")
        self.assertEqual(record["state"], "failed")
        # Missing file (or, without Pillow installed, the import) - either way caught and recorded.
        self.assertRegex(record["error"], "No such file|PIL")
        self.assertEqual(su.last_run()["state"], "failed")

    def test_app_does_not_load_pillow_until_drawing(self):
        """The estimator's memory shouldn't grow just because this code exists."""
        out = subprocess.run([sys.executable, "-c", "import app, sys; print('PIL' in sys.modules)"],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "False", out.stderr)


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class RoundTripTests(Base):
    """GET /scope/drawings-test on the real Handler -> drawings rendered ->
    fake CompanyCam downloads each one from /scope/files/... -> status."""

    @classmethod
    def setUpClass(cls):
        import app
        cls.app = app

    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        env = mock.patch.dict(os.environ, {"RENDER_EXTERNAL_URL": self.base})
        env.start()
        self.addCleanup(env.stop)
        self.fake = FakeCompanyCamWrites(download=True)
        for p in (mock.patch.object(cc, "_request", self.fake), mock.patch.object(su, "POLL_SECONDS", 0.05)):
            p.start()
            self.addCleanup(p.stop)

    def get_json(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_draw_upload_and_report(self):
        with mock.patch("builtins.print"):
            status, body = self.get_json(f"/scope/drawings-test?key={KEY}")
            self.assertEqual(status, 202, body)
            deadline = time.time() + 30
            while time.time() < deadline:
                _, st = self.get_json(f"/scope/drawings-test?key={KEY}&status=1")
                if not st["running"] and st["last_run"].get("state") in ("done", "failed"):
                    break
                time.sleep(0.1)
        run = st["last_run"]
        self.assertEqual(run["state"], "done", run)
        self.assertEqual(run["summary"], "5 of 5 drawings are in CompanyCam project 117058039.")
        self.assertTrue(all(u["companycam_downloaded_it"] for u in run["uploads"]))
        self.assertIn("peak_mb", run["memory"]["after_render"])
        # What CompanyCam downloaded is the real PNG, served with the right type.
        for ctype, data in self.fake.downloaded.values():
            self.assertEqual(ctype, "image/png")
            self.assertTrue(data.startswith(b"\x89PNG"))
        descriptions = [b["photo"]["description"] for _, b in self.fake.posts]
        self.assertEqual(descriptions[0], "Office - Floor Plan - drawn by the Site Scope Tool from the Oct 19, "
                                          "2026 site visit notes (phase 2 test). For planning and quoting only.")

    def test_file_route_head_and_expiry(self):
        token = su.put_temp_file(b"\x89PNGdata", "image/png", "x.png")
        req = urllib.request.Request(f"{self.base}/scope/files/{token}/x.png", method="HEAD")
        with mock.patch("builtins.print"), urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Length"], "8")
        with mock.patch("builtins.print"), self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{self.base}/scope/files/not-a-token/x.png", timeout=10)
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
