"""Site Scope Tool - phone alerts (phase 7).

When a run's questions are ready, or its build finishes, the phone that
started it gets a web push alert ("Questions ready - <address>") that opens
the scope page on that job. The phone sends its push subscription (the
browser's alert address: an https endpoint plus two public keys) with Start,
Send answers, Build or POST /scope/runs/push; it is kept on the run.

Render doesn't hold the alert keys. It POSTs the message to the sales-lead-tool
route /api/scope/push (X-Scope-Key header), which signs it with the VAPID keys
in Vercel and hands it to Apple's or Google's push service. So no new Python
package and no new key on Render.

    SCOPE_PUSH_URL   (optional) default https://sales-lead-tool-new.vercel.app/api/scope/push

A push never stops a run: every failure is logged and recorded under
run["push"]["sent"]. A subscription the push service says is gone (the phone
turned alerts off or removed the app) is dropped from the run.
"""

import json
import os
import time
import urllib.error
import urllib.request

import scope_budget

DEFAULT_PUSH_URL = "https://sales-lead-tool-new.vercel.app/api/scope/push"
TIMEOUT_SECONDS = 15
MAX_ENDPOINT_CHARS = 1000
MAX_KEY_CHARS = 200
KEEP_SENT = 5
MAX_BODY_CHARS = 160


def push_url():
    return (os.environ.get("SCOPE_PUSH_URL") or DEFAULT_PUSH_URL).strip()


def _scope_key():
    return (os.environ.get("SCOPE_API_KEY") or "").strip()


def clean_subscription(sub):
    """The browser's PushSubscription.toJSON(), checked and trimmed - or None."""
    if not isinstance(sub, dict):
        return None
    endpoint = sub.get("endpoint")
    keys = sub.get("keys")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > MAX_ENDPOINT_CHARS:
        return None
    if not isinstance(keys, dict):
        return None
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    for k in (p256dh, auth):
        if not isinstance(k, str) or not k or len(k) > MAX_KEY_CHARS:
            return None
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def attach(run, sub):
    """Keep the phone's subscription on the run. Returns True if one was kept."""
    clean = clean_subscription(sub)
    if not clean:
        return False
    run["push_sub"] = clean
    return True


def public(run):
    """What the page sees: whether alerts are on for this run, and the last one sent."""
    sent = (run.get("push") or {}).get("sent") or []
    return {"on": bool(run.get("push_sub")), "last": sent[-1] if sent else None}


def _place(run):
    job = run.get("job") or {}
    return job.get("address") or job.get("name") or (f"job {job.get('number')}" if job.get("number") else "the job")


def _short(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS - 1].rstrip() + "…"


def _pretty_date(iso):
    try:
        y, m, d = (int(x) for x in str(iso).split("-"))
        return time.strftime("%b %-d", (y, m, d, 0, 0, 0, 0, 0, -1))
    except Exception:
        return ""


def message(run, kind):
    """(title, body) for one alert kind: questions, failed, built, build_problem."""
    place = _place(run)
    if kind == "questions":
        n = len(run.get("questions") or [])
        day = _pretty_date(run.get("visit_date"))
        return (f"Questions ready - {place}",
                f"{n} question{'s' if n != 1 else ''}{f' for the {day} visit' if day else ''}. Tap to answer.")
    if kind == "failed":
        return (f"Scope run didn't finish - {place}", _short(run.get("error") or "Open the page to start it again."))
    build = run.get("build") or {}
    if kind == "built":
        try:
            bud = scope_budget.public(run) or {}
        except Exception:
            bud = {}
        if bud.get("price") is not None:
            groups = bud.get("groups") or 0
            return (f"Scope Page and budget built - {place}",
                    f"{groups} group{'s' if groups != 1 else ''}, ${bud['price']:,.0f} price. Tap to review.")
        return (f"Scope Page and budget built - {place}", "Tap to review.")
    return (f"Build needs a look - {place}", _short(build.get("error") or "Open the page and tap Build."))


def kind_for(run):
    """Which alert the run's current state calls for, or None."""
    status = run.get("status")
    if status == "questions":
        return "questions"
    if status == "failed":
        return "failed"
    state = (run.get("build") or {}).get("state")
    if status == "answered" and state == "done":
        return "built"
    if status == "answered" and state in ("partial", "failed"):
        return "build_problem"
    return None


def _post(payload):
    """POST to the Vercel push route. Returns (http_status, parsed_json_or_text)."""
    req = urllib.request.Request(push_url(), data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Scope-Key": _scope_key()})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            status, text = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", "replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text[:200]


def notify(run, kind=None, log=print, post=None):
    """Send the alert for the run's state, if it has a subscription. Never raises.
    Returns True when the run changed (a result was recorded) and should be saved."""
    try:
        kind = kind or kind_for(run)
        sub = run.get("push_sub")
        if not kind or not sub:
            return False
        if not _scope_key():
            log("Scope push: SCOPE_API_KEY is not set, so no alert was sent.")
            return False
        title, body = message(run, kind)
        job_id = (run.get("job") or {}).get("id") or ""
        payload = {"subscription": sub, "title": title, "body": body,
                   "url": f"/intake/scope?job={job_id}", "tag": f"scope-{run.get('run_id')}"}
        result = {"kind": kind, "at": int(time.time()), "ok": False, "error": None}
        try:
            status, data = (post or _post)(payload)
            if status == 200 and isinstance(data, dict) and data.get("ok"):
                result["ok"] = True
            elif isinstance(data, dict) and data.get("gone"):
                run.pop("push_sub", None)
                result["error"] = "The phone turned alerts off (subscription gone); dropped it."
            else:
                err = data.get("error") if isinstance(data, dict) else data
                result["error"] = f"HTTP {status}: {err}"
        except Exception as e:
            result["error"] = f"Couldn't reach the push route ({e})."
        push = run.setdefault("push", {})
        push["sent"] = (push.get("sent") or [])[-(KEEP_SENT - 1):] + [result]
        job_no = (run.get("job") or {}).get("number")
        log(f"Scope push {kind} for run {run.get('run_id')} (job {job_no}): "
            f"{'sent' if result['ok'] else result['error']}")
        return True
    except Exception as e:
        log(f"Scope push: unexpected error ({e}); no alert sent.")
        return False
