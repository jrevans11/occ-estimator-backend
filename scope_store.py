"""Site Scope Tool - where scope runs are kept (phase 4).

A run (its status, questions, answers and room file) has to outlive a closed
page and a Render restart, so it goes to Upstash Redis through Upstash's REST
API (decided Oct 20, 2026). Standard library only - one HTTPS call per read
or write, nothing loaded into the server's memory.

Render env vars: UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN (copied
from the Upstash console, REST API section). The names Vercel's Upstash
integration uses (KV_REST_API_URL / KV_REST_API_TOKEN) work too.

With neither set, runs are kept in this process's memory instead, so the tool
still works - but a restart (every deploy) forgets them. The status reply
says which store is in use.
"""

import json
import os
import threading
import urllib.error
import urllib.request

KEEP_SECONDS = 30 * 24 * 60 * 60   # a run is kept 30 days after its last change
PREFIX = "occ-scope:"


class StoreError(Exception):
    pass


def _config():
    url = (os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL") or "").strip()
    token = (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN") or "").strip()
    return url.rstrip("/"), token


def is_durable():
    url, token = _config()
    return bool(url and token)


def kind():
    return "upstash" if is_durable() else "memory"


def _command(*args):
    """Run one Redis command through the Upstash REST API. Returns `result`."""
    url, token = _config()
    req = urllib.request.Request(
        url, data=json.dumps([str(a) for a in args]).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        hint = " - check UPSTASH_REDIS_REST_TOKEN in Render." if e.code in (401, 403) else ""
        raise StoreError(f"Upstash answered HTTP {e.code}: {detail}{hint}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise StoreError(f"Could not reach Upstash: {e}")
    if isinstance(body, dict) and body.get("error"):
        raise StoreError(f"Upstash: {body['error']}")
    return body.get("result") if isinstance(body, dict) else None


# ── Memory fallback ──────────────────────────────────────────────────────────

_mem = {}
_mem_lock = threading.Lock()


def _reset_memory():
    """Tests only."""
    with _mem_lock:
        _mem.clear()


# ── Public calls ─────────────────────────────────────────────────────────────

def put(key, value):
    """Save a JSON-able value under key (kept KEEP_SECONDS)."""
    raw = json.dumps(value, separators=(",", ":"))
    if is_durable():
        _command("SET", PREFIX + key, raw, "EX", KEEP_SECONDS)
    else:
        with _mem_lock:
            _mem[key] = raw


def get(key):
    """The saved value, or None."""
    if is_durable():
        raw = _command("GET", PREFIX + key)
    else:
        with _mem_lock:
            raw = _mem.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
