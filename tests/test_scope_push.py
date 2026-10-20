"""Tests for Site Scope Tool phase 7: phone alerts (scope_push.py) and how runs
send them (scope_runs.py).

Run from the repo root:  python3 -m unittest discover tests

The Vercel push route is faked at scope_push._post, so no keys or network are
needed. The run-flow tests reuse the phase 4-5 fakes (CompanyCam, Claude).
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scope_push  # noqa: E402
import scope_runs  # noqa: E402
import test_scope_build as tb  # noqa: E402
import test_scope_runs as tr  # noqa: E402

SUB = {"endpoint": "https://web.push.apple.com/QGx1c2gtZW5kcG9pbnQ", "expirationTime": None,
       "keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
                "auth": "tBHItJI5svbpez7KI4CCXg"}}


def a_run(**over):
    run = {"run_id": "abc123", "status": "questions", "visit_date": "2026-10-20",
           "job": {"id": "22PV6j9qe42x", "number": "00489", "name": "New selections test",
                   "address": "1217 Fieldstone Ct, Royse City, TX"},
           "questions": [{"id": "Q1"}, {"id": "Q2"}, {"id": "Q3"}], "push_sub": dict(SUB)}
    run.update(over)
    return run


class FakeRoute:
    """The sales-lead-tool /api/scope/push route."""

    def __init__(self, status=200, data=None, error=None):
        self.status, self.data, self.error = status, data if data is not None else {"ok": True}, error
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.status, self.data


class SubscriptionTests(unittest.TestCase):
    def test_keeps_only_endpoint_and_keys(self):
        self.assertEqual(scope_push.clean_subscription(SUB), {"endpoint": SUB["endpoint"], "keys": SUB["keys"]})

    def test_bad_shapes_are_refused(self):
        for bad in (None, "x", {}, {"endpoint": "http://not-https", "keys": SUB["keys"]},
                    {"endpoint": SUB["endpoint"]}, {"endpoint": SUB["endpoint"], "keys": {"p256dh": "x"}},
                    {"endpoint": SUB["endpoint"], "keys": {"p256dh": "x", "auth": "y" * 500}},
                    {"endpoint": "https://" + "a" * 2000, "keys": SUB["keys"]}):
            self.assertIsNone(scope_push.clean_subscription(bad), bad)

    def test_attach(self):
        run = {}
        self.assertFalse(scope_push.attach(run, None))
        self.assertNotIn("push_sub", run)
        self.assertTrue(scope_push.attach(run, SUB))
        self.assertEqual(run["push_sub"]["endpoint"], SUB["endpoint"])
        self.assertEqual(scope_push.public(run), {"on": True, "last": None})


class MessageTests(unittest.TestCase):
    def test_questions(self):
        self.assertEqual(scope_push.message(a_run(), "questions"),
                         ("Questions ready - 1217 Fieldstone Ct, Royse City, TX",
                          "3 questions for the Oct 20 visit. Tap to answer."))
        one = a_run(questions=[{"id": "Q1"}], visit_date=None)
        self.assertEqual(scope_push.message(one, "questions")[1], "1 question. Tap to answer.")

    def test_no_address_uses_the_job_name(self):
        run = a_run()
        run["job"]["address"] = None
        self.assertEqual(scope_push.message(run, "questions")[0], "Questions ready - New selections test")

    def test_built_with_budget(self):
        run = a_run(status="answered", build={"state": "done", "steps": {"budget": {"plan": {
            "parent_name": "Site Scope Draft - Oct 20, 2026", "groups": [{}] * 4, "alternates": [],
            "cost": 9000.0, "price": 12345.4, "tbd": 1}}}})
        self.assertEqual(scope_push.kind_for(run), "built")
        self.assertEqual(scope_push.message(run, "built"),
                         ("Scope Page and budget built - 1217 Fieldstone Ct, Royse City, TX",
                          "4 groups, $12,345 price. Tap to review."))

    def test_problems(self):
        failed = a_run(status="failed", error="CompanyCam didn't answer. " * 20)
        self.assertEqual(scope_push.kind_for(failed), "failed")
        title, body = scope_push.message(failed, "failed")
        self.assertTrue(title.startswith("Scope run didn't finish - "))
        self.assertLessEqual(len(body), scope_push.MAX_BODY_CHARS)
        partial = a_run(status="answered", build={"state": "partial", "error": "Not everything finished: budget."})
        self.assertEqual(scope_push.kind_for(partial), "build_problem")
        self.assertEqual(scope_push.message(partial, "build_problem")[1], "Not everything finished: budget.")

    def test_no_alert_while_working(self):
        self.assertIsNone(scope_push.kind_for(a_run(status="reading")))
        self.assertIsNone(scope_push.kind_for(a_run(status="answered", build={"state": "building"})))


class NotifyTests(unittest.TestCase):
    def setUp(self):
        e = mock.patch.dict(os.environ, {"SCOPE_API_KEY": "k"})
        e.start()
        self.addCleanup(e.stop)
        self.logs = []

    def test_sent(self):
        route = FakeRoute()
        run = a_run()
        self.assertTrue(scope_push.notify(run, log=self.logs.append, post=route))
        payload = route.calls[0]
        self.assertEqual(payload["subscription"], SUB)
        self.assertEqual(payload["url"], "/intake/scope?job=22PV6j9qe42x")
        self.assertEqual(payload["tag"], "scope-abc123")
        self.assertTrue(run["push"]["sent"][-1]["ok"])
        self.assertEqual(scope_push.public(run)["last"]["kind"], "questions")

    def test_gone_subscription_is_dropped(self):
        run = a_run()
        scope_push.notify(run, log=self.logs.append, post=FakeRoute(410, {"ok": False, "gone": True}))
        self.assertNotIn("push_sub", run)
        self.assertFalse(run["push"]["sent"][-1]["ok"])
        self.assertFalse(scope_push.public(run)["on"])

    def test_errors_never_raise(self):
        run = a_run()
        self.assertTrue(scope_push.notify(run, log=self.logs.append,
                                          post=FakeRoute(503, {"ok": False, "error": "VAPID keys not set"})))
        self.assertIn("VAPID keys not set", run["push"]["sent"][-1]["error"])
        self.assertTrue(scope_push.notify(run, log=self.logs.append, post=FakeRoute(error=OSError("timed out"))))
        self.assertIn("timed out", run["push"]["sent"][-1]["error"])
        self.assertIn("push_sub", run)   # only "gone" drops it

    def test_nothing_sent_without_a_subscription_or_key(self):
        route = FakeRoute()
        self.assertFalse(scope_push.notify(a_run(push_sub=None), log=self.logs.append, post=route))
        with mock.patch.dict(os.environ, {"SCOPE_API_KEY": ""}):
            self.assertFalse(scope_push.notify(a_run(), log=self.logs.append, post=route))
        self.assertEqual(route.calls, [])

    def test_keeps_the_last_few_results(self):
        run = a_run()
        for _ in range(scope_push.KEEP_SENT + 3):
            scope_push.notify(run, log=self.logs.append, post=FakeRoute())
        self.assertEqual(len(run["push"]["sent"]), scope_push.KEEP_SENT)

    def test_default_url_and_override(self):
        with mock.patch.dict(os.environ, {"SCOPE_PUSH_URL": ""}):
            self.assertEqual(scope_push.push_url(), scope_push.DEFAULT_PUSH_URL)
        with mock.patch.dict(os.environ, {"SCOPE_PUSH_URL": "https://x.vercel.app/api/scope/push"}):
            self.assertEqual(scope_push.push_url(), "https://x.vercel.app/api/scope/push")


@unittest.skipUnless(tr.HAVE_PIL, "Pillow not installed")
class RunAlertTests(tb.BuildBase):
    def setUp(self):
        super().setUp()
        self.route = FakeRoute()
        p = mock.patch.object(scope_push, "_post", self.route)
        p.start()
        self.addCleanup(p.stop)

    def kinds(self):
        return [c["title"].split(" - ")[0] for c in self.route.calls]

    def test_questions_then_build_alerts_go_to_the_phone(self):
        self.claude(tr.sse(tr.sample_answer()), tr.sse(tb.build_answer()))
        _, body = scope_runs.handle_post("/scope/runs", {"job": "00489", "push": SUB}, tr.fake_jobtread(), tr.ORG,
                                         start_thread=tr.run_inline)
        run_id = body["run_id"]
        self.assertEqual(self.kinds(), ["Questions ready"])
        self.assertEqual(self.route.calls[0]["subscription"]["endpoint"], SUB["endpoint"])
        _, status = scope_runs.run_status({"run_id": run_id}, tr.fake_jobtread(), tr.ORG)
        self.assertEqual(status["push"]["last"]["kind"], "questions")
        self.assertNotIn("push_sub", status)   # the page never gets the subscription back
        scope_runs.handle_post("/scope/runs/answers", {"run_id": run_id, "answers": {}}, tr.fake_jobtread(),
                               tr.ORG, start_thread=tr.run_inline)
        self.assertEqual(self.kinds(), ["Questions ready", "Scope Page and budget built"])

    def test_no_subscription_no_alert(self):
        self.claude(tr.sse(tr.sample_answer()))
        scope_runs.handle_post("/scope/runs", {"job": "00489"}, tr.fake_jobtread(), tr.ORG,
                               start_thread=tr.run_inline)
        self.assertEqual(self.route.calls, [])

    def test_alerts_turned_on_mid_run(self):
        held = []
        _, body = scope_runs.handle_post("/scope/runs", {"job": "00489"}, tr.fake_jobtread(), tr.ORG,
                                         start_thread=lambda fn, *a: held.append((fn, a)))
        code, out = scope_runs.handle_post("/scope/runs/push", {"run_id": body["run_id"], "push": SUB},
                                           tr.fake_jobtread(), tr.ORG)
        self.assertEqual((code, out["push"]["on"]), (200, True))
        code, out = scope_runs.handle_post("/scope/runs/push", {"run_id": body["run_id"], "push": {"x": 1}},
                                           tr.fake_jobtread(), tr.ORG)
        self.assertEqual(code, 400)
        self.claude(tr.sse(tr.sample_answer()))
        fn, args = held[0]
        fn(*args)   # the quick pass finishes
        self.assertEqual(self.kinds(), ["Questions ready"])

    def test_failed_run_alerts(self):
        self.cc.notes, self.cc.photos, self.cc.videos = False, [], []
        scope_runs.handle_post("/scope/runs", {"job": "00489", "push": SUB}, tr.fake_jobtread(), tr.ORG,
                               start_thread=tr.run_inline)
        self.assertEqual(self.kinds(), ["Scope run didn't finish"])

    def test_a_broken_push_route_doesnt_stop_the_run(self):
        self.route.error = OSError("connection refused")
        self.claude(tr.sse(tr.sample_answer()))
        _, body = scope_runs.handle_post("/scope/runs", {"job": "00489", "push": SUB}, tr.fake_jobtread(),
                                         tr.ORG, start_thread=tr.run_inline)
        _, status = scope_runs.run_status({"run_id": body["run_id"]}, tr.fake_jobtread(), tr.ORG)
        self.assertEqual(status["status"], "questions")
        self.assertIn("connection refused", status["push"]["last"]["error"])


if __name__ == "__main__":
    unittest.main()
