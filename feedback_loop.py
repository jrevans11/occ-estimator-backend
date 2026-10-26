"""
Feedback loop — steps 2-4 (Jul 2026)
=====================================
Step 1 (already shipped, see estimate_v2_draft.py/app.py) saves the AI's
untouched original estimate as "Original AI Estimate.csv" to a job's
JobTread Files at creation time. This module builds steps 2-4 on top of
that idea, per Jason's explicit request to prioritize this ("this is
important and needs to be finalized"):

  STEP 2 — DIFF SWEEP: periodically find jobs whose AI estimate is old
  enough that Jason's team has likely finished editing it, compare the
  CURRENT real cost items against what the AI originally proposed, and
  record every meaningful difference (hours changed, price changed, item
  added/removed/reclassified).

  STEP 3 — CORRECTIONS LOG: write those diffs to a Google Sheet (Jason's
  choice, Jul 2026 — "creating a google sheet and the info gets organized
  there") instead of an emailed digest, plus a monthly to-do reminder
  nudging him to go review it ("monthly there is a reminder for pricing
  update").

  STEP 4 — RULE UPDATES: deliberately NOT automated. The sheet is input for
  a manual review round, same as the Q&A rounds already done in this
  project (occ_pricing_logic_questions*.csv) — recurring patterns get
  turned into real SYSTEM_PROMPT/ESTIMATING_LOGIC_SECTION edits only after
  a human (Jason, or a future Claude session working from his notes) has
  actually looked at them. No code in this module writes to the prompt.

DESIGN NOTE — why the baseline lives in a Daily Log, not the CSV file:
JobTread's API has a proven way to UPLOAD a file (createUploadRequest +
files-on-createCostGroup, see estimate_v2_draft.py/app.py), but no proven
way to DOWNLOAD one back later — so the CSV snapshot is great for a human
to open, but not a reliable machine-readable baseline for this sweep to
re-fetch. Daily Logs, by contrast, are a normal text field already proven
both to write (create_job_daily_log() in app.py, live-tested) and are
presumed queryable back the same way every other JobTread object in this
codebase is (job.costGroups, job.customFieldValues, etc.) — see
fetch_daily_logs()'s docstring for the one real unverified assumption in
this whole module.

WHAT'S PROVEN VS. UNVERIFIED (be honest about this — same practice as every
other new JobTread mechanism in this project):
  - PROVEN: createDailyLog (jobId/date/notes/notify) — live-tested already.
  - PROVEN (Oct 26 2026): reading job.dailyLogs back. 15 baselines written
    between Jul 29 and Aug 19 2026 (jobs 00705-00767) were read back and
    parsed successfully during the first manual feedback review.
  - PROVEN (Oct 26 2026): job.costItems with where [["document","id"], null]
    returns ONLY budget lines. Plain job.costItems / job.costGroups also
    return a frozen copy of every line for each estimate document on the
    job, so anything that compares "current budget" must use that filter.
    Every cost item keeps its original createdAt, which is how the baseline
    capture below tells the AI's lines apart from anything added later.
  - UNVERIFIED: organization.jobs supports the pagination/customFieldValues
    shape used in find_candidate_jobs() — modeled directly on the already-
    proven pattern in send_followup_email_from_todo()/create_job_record(),
    but this exact combination (paginated + customFieldValues + createdAt)
    hasn't been run live yet.
  - NOT YET CONFIGURED: Google Sheets write access (needs a one-time Jason
    setup — see CLAUDE.md "FEEDBACK LOOP SETUP" for the exact steps) and
    the fixed internal "admin" job the monthly to-do reminder attaches to.

HISTORY — baseline capture went dark Aug 19 2026 and was restored Oct 26 2026:
The original capture (BaselineCollector + callbacks inside add_cost_groups_v2)
lived in the app.py lineage that was replaced on Aug 19 2026, so no baseline
was written for any estimate after job 00767. The restored capture,
capture_baseline_from_job(), does NOT hook into add_cost_groups_v2. It runs
right after an estimate is written, reads back every budget line created
since the run started, and writes the same [AI-FEEDBACK-BASELINE-V1] record.
That makes it independent of the writer's internals, so a future rewrite of
add_cost_groups_v2 / add_cost_groups can't silently switch it off again.
It is called from both estimate paths in app.py: estimate_closing_job()
(v2, closing repairs) and estimate_general_job() (flat-price Home Repair,
GVL, Remodel, Pre-listing and the general sales-tool endpoint).
"""
import json
import time
from datetime import datetime, date, timedelta, timezone

BASELINE_MARKER = "[AI-FEEDBACK-BASELINE-V1]"
SWEPT_MARKER = "[AI-FEEDBACK-SWEPT-V1]"

# Job types on the v2 pipeline (Jul 2026 migration) — these are the only
# ones that ever get a feedback-loop baseline written, so these are the
# only ones worth sweeping.
FEEDBACK_JOB_TYPES = [
    "Closing Repair", "Home Repair", "GVL Today", "Remodel", "Pre-listing Repair"
]

# A job is swept once it's "old enough" that Jason's team has likely
# finished editing it, but not so old that a missed cron run leaves it
# stuck forever. Window, not a single cutoff.
MIN_SWEEP_AGE_DAYS = 7
MAX_SWEEP_AGE_DAYS = 21

_NUMERIC_FIELDS = [("qty", "Quantity"), ("unit_cost", "Unit Cost"), ("unit_price", "Unit Price")]
_FLOAT_TOLERANCE = 0.01


class BaselineCollector:
    """Accumulates the real JobTread group/item IDs + starting values for
    ONE estimate as add_cost_groups_v2() creates them, via the
    on_group_created/on_item_created callback hooks it accepts. Building
    the baseline from real IDs (not names) means a later diff survives
    Jason renaming an item — matching by ID, not fuzzy text.

    NOT USED since Oct 26 2026 — the current add_cost_groups_v2 has no
    callback hooks. capture_baseline_from_job() replaced it (see the module
    docstring). Kept only because it documents the record format.
    """

    def __init__(self, job_id):
        self.job_id = job_id
        self.groups = {}  # group_id -> {"title":..., "cost_code":..., "items": {item_id: {...}}}

    def on_group_created(self, group_id, title, cost_code_name):
        if not group_id:
            return
        self.groups[group_id] = {"title": title, "cost_code": cost_code_name, "items": {}}

    def on_item_created(self, group_id, item_id, name, cost_type_id, qty, unit_cost, unit_price):
        if not item_id or group_id not in self.groups:
            return
        self.groups[group_id]["items"][item_id] = {
            "name": name,
            "cost_type_id": cost_type_id,
            "qty": qty,
            "unit_cost": unit_cost,
            "unit_price": unit_price,
        }

    def to_record(self):
        return {
            "job_id": self.job_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "groups": [
                {"group_id": gid, **gdata}
                for gid, gdata in self.groups.items()
                if gdata.get("items")  # skip groups that ended up with zero real items
            ],
        }


def write_baseline(jobtread_query_fn, job_id, baseline_record, log_date=None):
    """Write the baseline as one internal Daily Log entry via the
    already-proven createDailyLog mutation (see create_job_daily_log() in
    app.py — same shape, reused directly here). Marked with BASELINE_MARKER
    so a later sweep can find it. Never raises — non-fatal, same fail-open
    convention as everything else in the v2 pipeline; a failed baseline
    write just means this one job never gets swept, it never blocks the
    estimate itself.
    """
    log_date = log_date or date.today().isoformat()
    n_groups = len(baseline_record.get("groups", []))

    def _write(record):
        notes = f"{BASELINE_MARKER}{json.dumps(record, separators=(',', ':'))}"
        jobtread_query_fn({
            "createDailyLog": {
                "$": {"jobId": job_id, "date": log_date, "notes": notes, "notify": False},
                "createdDailyLog": {"id": {}}
            }
        })
        return len(notes)

    try:
        size = _write(baseline_record)
        print(f"  Feedback-loop baseline saved for job {job_id} ({n_groups} group(s), {size} chars)")
        return True
    except Exception as e:
        print(f"  Feedback-loop baseline save failed for job {job_id}: {e} — retrying without item names")

    # Fallback for an unusually large estimate: drop item names (the diff
    # matches by item ID, so quantities and prices are what matter) and
    # shorten group titles. Still parseable by parse_baseline_and_swept().
    try:
        slim = json.loads(json.dumps(baseline_record))
        slim["slim"] = True
        for g in slim.get("groups", []):
            g["title"] = (g.get("title") or "")[:60]
            for item in (g.get("items") or {}).values():
                item.pop("name", None)
        size = _write(slim)
        print(f"  Feedback-loop baseline saved for job {job_id} without item names ({size} chars)")
        return True
    except Exception as e:
        print(f"  Feedback-loop baseline save failed for job {job_id} (non-fatal): {e}")
        return False


def _utc_iso(dt_value):
    return dt_value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def run_started_marker(skew_seconds=60):
    """Timestamp to take just BEFORE an estimate's cost groups are written.
    Pass it to capture_baseline_from_job() afterwards. Backed off a minute
    so a small clock difference between Render and JobTread can't drop the
    first lines the estimator writes."""
    return _utc_iso(datetime.now(timezone.utc) - timedelta(seconds=skew_seconds))


def _fetch_budget_items(jobtread_query_fn, job_id, created_since=None, page_size=100):
    """Every BUDGET cost item on a job (estimate-document copies excluded),
    optionally only those created at or after `created_since` (ISO UTC).
    Paginates, so large estimates are never truncated. Raises on API error.
    """
    conditions = [[["document", "id"], None]]
    if created_since:
        conditions.append(["createdAt", ">=", created_since])
    where = {"and": conditions} if len(conditions) > 1 else conditions[0]

    nodes, page = [], None
    for _ in range(20):  # 20 pages x 100 lines is far beyond any real estimate
        args = {"where": where, "size": page_size,
                "sortBy": [{"field": "createdAt", "order": "asc"}]}
        if page:
            args["page"] = page
        resp = jobtread_query_fn({
            "job": {
                "$": {"id": job_id},
                "costItems": {
                    "$": args,
                    "nodes": {
                        "id": {}, "name": {}, "quantity": {}, "unitCost": {},
                        "unitPrice": {}, "createdAt": {},
                        "costType": {"id": {}, "name": {}},
                        "costCode": {"name": {}},
                        "costGroup": {"id": {}, "name": {}},
                    },
                    "nextPage": {},
                }
            }
        })
        conn = ((resp or {}).get("job") or {}).get("costItems") or {}
        nodes.extend(conn.get("nodes") or [])
        page = conn.get("nextPage")
        if not page:
            break
    return nodes


def capture_baseline_from_job(jobtread_query_fn, job_id, run_started_iso):
    """Save the AI's untouched estimate as the feedback-loop baseline.

    Call right after an estimate's cost groups are written, passing the
    run_started_marker() taken just before the write began. Reads back the
    budget lines created since then and writes them as one
    [AI-FEEDBACK-BASELINE-V1] Daily Log — the same record format the July
    2026 baselines used, so diff_baseline_vs_current() reads both.

    The $0 "NOTES (internal ...)" lines are skipped: they carry text, not
    pricing, and Jason edits them by design.

    Never raises. A failed capture only means this one job can't be
    compared later; it must never affect the estimate itself.
    """
    try:
        items = _fetch_budget_items(jobtread_query_fn, job_id, created_since=run_started_iso)
    except Exception as e:
        print(f"  Feedback-loop baseline: could not read back job {job_id} (non-fatal): {e}")
        return False

    groups = {}
    for it in items:
        name = it.get("name") or ""
        if name.startswith("NOTES ("):
            continue
        group = it.get("costGroup") or {}
        gid = group.get("id")
        item_id = it.get("id")
        if not gid or not item_id:
            continue
        g = groups.setdefault(gid, {
            "group_id": gid,
            "title": group.get("name") or "",
            "cost_code": (it.get("costCode") or {}).get("name") or "",
            "items": {},
        })
        g["items"][item_id] = {
            "name": name,
            "cost_type_id": (it.get("costType") or {}).get("id"),
            "qty": it.get("quantity"),
            "unit_cost": it.get("unitCost"),
            "unit_price": it.get("unitPrice"),
        }

    if not groups:
        print(f"  Feedback-loop baseline: no new budget lines found on job {job_id} — nothing saved")
        return False

    record = {
        "job_id": job_id,
        "created_at": _utc_iso(datetime.now(timezone.utc)),
        "source": "readback-v2",
        "groups": list(groups.values()),
    }
    return write_baseline(jobtread_query_fn, job_id, record)


def fetch_daily_logs(jobtread_query_fn, job_id, size=50):
    """Pull a job's Daily Logs (id/date/notes).

    UNVERIFIED (Jul 2026): job.dailyLogs has never been read back via the
    API before this module — createDailyLog (the write side) is proven,
    but this read shape is a best-evidenced guess mirroring how every other
    nested object in this codebase is queried (job.costGroups,
    job.customFieldValues, etc.). If the field name is wrong, this will
    raise a clear JobTread API error that shows up in the sweep endpoint's
    logs on the first real run — fix it there the same way several other
    real field names got corrected earlier in this project.
    """
    try:
        resp = jobtread_query_fn({
            "job": {
                "$": {"id": job_id},
                "dailyLogs": {
                    "$": {"size": size},
                    "nodes": {"id": {}, "date": {}, "notes": {}}
                }
            }
        })
        return (resp.get("job") or {}).get("dailyLogs", {}).get("nodes", []) or []
    except Exception as e:
        print(f"  Could not read Daily Logs for job {job_id}: {e}")
        return []


def parse_baseline_and_swept(daily_logs):
    """Scan a job's Daily Log entries for the baseline JSON and whether a
    swept marker already exists. Returns (baseline_dict_or_None, already_swept_bool).
    """
    baseline = None
    swept = False
    for log in daily_logs:
        notes = log.get("notes") or ""
        if baseline is None and notes.startswith(BASELINE_MARKER):
            raw = notes[len(BASELINE_MARKER):]
            try:
                baseline = json.loads(raw)
            except Exception as e:
                print(f"  Could not parse feedback-loop baseline JSON: {e}")
        if notes.startswith(SWEPT_MARKER):
            swept = True
    return baseline, swept


def mark_swept(jobtread_query_fn, job_id, log_date=None):
    """Mark a job as already swept so it's never re-diffed. Non-fatal —
    if this write fails, the worst case is the job gets diffed again next
    run (duplicate rows in the sheet), not a crash.
    """
    log_date = log_date or date.today().isoformat()
    notes = f"{SWEPT_MARKER}Swept on {log_date}."
    try:
        jobtread_query_fn({
            "createDailyLog": {
                "$": {"jobId": job_id, "date": log_date, "notes": notes, "notify": False},
                "createdDailyLog": {"id": {}}
            }
        })
        return True
    except Exception as e:
        print(f"  Could not mark job {job_id} as swept (non-fatal, may be re-diffed next run): {e}")
        return False


def find_candidate_jobs(jobtread_query_fn, org_id, min_age_days=MIN_SWEEP_AGE_DAYS,
                         max_age_days=MAX_SWEEP_AGE_DAYS, job_types=None, page_size=50):
    """Find jobs of the right type, created in the sweep age window.

    UNVERIFIED (Jul 2026): mirrors the proven customFieldValues nodes
    {customField{name}, value} pattern used elsewhere in this codebase
    (e.g. send_followup_email_from_todo), but this exact paginated
    organization.jobs + customFieldValues + createdAt combination hasn't
    been run live. Filters by Job Type and date window CLIENT-SIDE (after
    the query returns), since a direct createdAt range filter argument on
    organization.jobs isn't confirmed to exist — safer to over-fetch a
    little and filter here than guess at a filter arg name.
    """
    job_types = job_types or FEEDBACK_JOB_TYPES
    now = datetime.utcnow()
    cutoff_new = (now - timedelta(days=min_age_days)).date()
    cutoff_old = (now - timedelta(days=max_age_days)).date()

    candidates = []
    page = 1
    while True:
        try:
            resp = jobtread_query_fn({
                "organization": {
                    "$": {"id": org_id},
                    "jobs": {
                        "$": {"size": page_size, "page": page},
                        "nodes": {
                            "id": {}, "name": {}, "createdAt": {},
                            "customFieldValues": {
                                "$": {"size": 10},
                                "nodes": {"customField": {"name": {}}, "value": {}}
                            }
                        },
                        "nextPage": {}
                    }
                }
            })
        except Exception as e:
            print(f"  find_candidate_jobs: page {page} query failed, stopping: {e}")
            break

        jobs_node = (resp.get("organization") or {}).get("jobs", {}) or {}
        jobs = jobs_node.get("nodes", []) or []
        if not jobs:
            break

        for j in jobs:
            cfvs = {
                v["customField"]["name"]: v.get("value")
                for v in (j.get("customFieldValues") or {}).get("nodes", [])
                if v.get("customField")
            }
            if cfvs.get("Job Type") not in job_types:
                continue
            created_raw = (j.get("createdAt") or "")[:10]
            if not created_raw:
                continue
            try:
                created_date = datetime.strptime(created_raw, "%Y-%m-%d").date()
            except Exception:
                continue
            if cutoff_old <= created_date <= cutoff_new:
                candidates.append({
                    "job_id": j.get("id"), "name": j.get("name"), "created_at": created_raw
                })

        next_page = jobs_node.get("nextPage")
        if not next_page:
            break
        page = next_page
        time.sleep(0.05)

    return candidates


def fetch_current_cost_items(jobtread_query_fn, job_id):
    """Pull a job's CURRENT budget lines, keyed by real item ID.

    Fixed Oct 26 2026: the earlier version read job.costGroups, which also
    returns every estimate document's frozen copy of the budget (each with
    new item IDs, so every one would have shown up as an "added" line), and
    it capped each group at 20 lines. This reads job.costItems filtered to
    budget lines only, paginated. NOTES lines are skipped to match the
    baseline.
    """
    try:
        nodes = _fetch_budget_items(jobtread_query_fn, job_id)
    except Exception as e:
        print(f"  fetch_current_cost_items failed for job {job_id}: {e}")
        return {}

    items = {}
    for it in nodes:
        item_id = it.get("id")
        name = it.get("name") or ""
        if not item_id or name.startswith("NOTES ("):
            continue
        group = it.get("costGroup") or {}
        items[item_id] = {
            "group_id": group.get("id"),
            "group_name": group.get("name"),
            "name": name,
            "qty": it.get("quantity"),
            "unit_cost": it.get("unitCost"),
            "unit_price": it.get("unitPrice"),
            "cost_type": (it.get("costType") or {}).get("name"),
        }
    return items


def diff_baseline_vs_current(baseline_record, current_items):
    """Compare the AI's original baseline (matched by real item ID)
    against the CURRENT state of the job's cost items. Returns a list of
    diff row dicts: {job_id, group_title, item_name, change_type, field,
    before, after}. change_type is "changed", "removed", or "added".

    Matching by ID rather than name means a renamed item is correctly
    tracked as "changed" (not a false remove+add), and restructuring
    (Jason splitting a lump sum into itemized lines, or merging several
    items into one) shows up honestly as real removed/added rows — which
    is itself useful signal, not noise to suppress.
    """
    job_id = baseline_record.get("job_id")
    rows = []
    baseline_item_ids = set()

    for group in baseline_record.get("groups", []):
        group_title = group.get("title", "")
        for item_id, base_item in (group.get("items") or {}).items():
            baseline_item_ids.add(item_id)
            current = current_items.get(item_id)
            if current is None:
                rows.append({
                    "job_id": job_id, "group_title": group_title,
                    "item_name": base_item.get("name"), "change_type": "removed",
                    "field": "", "before": "", "after": "",
                })
                continue

            if (current.get("name") or "") != (base_item.get("name") or ""):
                rows.append({
                    "job_id": job_id, "group_title": group_title,
                    "item_name": base_item.get("name"), "change_type": "changed",
                    "field": "name", "before": base_item.get("name"),
                    "after": current.get("name"),
                })

            for key, label in _NUMERIC_FIELDS:
                before_val = float(base_item.get(key) or 0)
                after_val = float(current.get(key) or 0)
                if abs(before_val - after_val) > _FLOAT_TOLERANCE:
                    rows.append({
                        "job_id": job_id, "group_title": group_title,
                        "item_name": current.get("name") or base_item.get("name"),
                        "change_type": "changed", "field": label,
                        "before": before_val, "after": after_val,
                    })

    for item_id, current in current_items.items():
        if item_id not in baseline_item_ids:
            rows.append({
                "job_id": job_id, "group_title": current.get("group_name", ""),
                "item_name": current.get("name"), "change_type": "added",
                "field": "", "before": "", "after": current.get("unit_price"),
            })

    return rows


def append_diff_rows_to_sheet(rows, sheet_id, service_account_json, job_label_lookup=None):
    """Append diff rows to a Google Sheet via a service account — no OAuth
    flow needed from Jason; he shares the target Sheet once with the
    service account's email as an Editor. Requires `gspread` + `google-auth`
    (added to requirements.txt). Raises clearly if not configured yet
    (FEEDBACK_SHEET_ID / GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON missing) —
    callers should catch and log, never let this crash the sweep, since
    the sweep/diff logic is useful on its own even before the sheet is
    wired up. See CLAUDE.md "FEEDBACK LOOP SETUP" for the one-time setup
    steps.
    """
    if not sheet_id or not service_account_json:
        raise RuntimeError(
            "Google Sheets not configured — set FEEDBACK_SHEET_ID and "
            "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON to enable writing to the sheet."
        )

    import gspread
    from google.oauth2.service_account import Credentials

    creds_dict = json.loads(service_account_json)
    creds = Credentials.from_service_account_info(
        creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id).sheet1

    values = []
    now_str = datetime.utcnow().isoformat() + "Z"
    for r in rows:
        job_label = (job_label_lookup or {}).get(r["job_id"], r["job_id"])
        values.append([
            now_str, job_label, r.get("group_title", ""), r.get("item_name", ""),
            r.get("change_type", ""), r.get("field", ""),
            str(r.get("before", "")), str(r.get("after", "")),
        ])
    if values:
        sheet.append_rows(values, value_input_option="USER_ENTERED")
    return len(values)


def run_feedback_sweep(jobtread_query_fn, org_id, sheet_id=None, service_account_json=None,
                        min_age_days=MIN_SWEEP_AGE_DAYS, max_age_days=MAX_SWEEP_AGE_DAYS):
    """Full Step-2/3 sweep: find candidate jobs -> read baseline -> pull
    current cost items -> diff -> append to Google Sheet -> mark swept.

    Returns a summary dict: {jobs_checked, jobs_diffed, rows_written, errors}.
    Entirely best-effort per job — one bad job (a query failure, a
    corrupted baseline) is logged and skipped, never stops the sweep.
    Safe to call even before Google Sheets is configured: the sweep still
    runs and marks jobs swept, it just can't persist the rows anywhere
    (logged clearly, counted in `errors`) until sheet_id/service_account_json
    are set.
    """
    summary = {"jobs_checked": 0, "jobs_diffed": 0, "rows_written": 0, "errors": []}
    candidates = find_candidate_jobs(jobtread_query_fn, org_id, min_age_days, max_age_days)
    summary["jobs_checked"] = len(candidates)

    all_rows = []
    job_label_lookup = {}
    for cand in candidates:
        job_id = cand["job_id"]
        job_label_lookup[job_id] = cand.get("name") or job_id
        try:
            logs = fetch_daily_logs(jobtread_query_fn, job_id)
            baseline, already_swept = parse_baseline_and_swept(logs)
            if already_swept or not baseline:
                continue
            current_items = fetch_current_cost_items(jobtread_query_fn, job_id)
            rows = diff_baseline_vs_current(baseline, current_items)
            all_rows.extend(rows)
            mark_swept(jobtread_query_fn, job_id)
            summary["jobs_diffed"] += 1
        except Exception as e:
            summary["errors"].append(f"{job_id}: {e}")
            print(f"  Feedback sweep: job {job_id} failed (non-fatal, skipping): {e}")

    if all_rows:
        try:
            written = append_diff_rows_to_sheet(all_rows, sheet_id, service_account_json, job_label_lookup)
            summary["rows_written"] = written
        except Exception as e:
            summary["errors"].append(f"sheet write: {e}")
            print(f"  Feedback sweep: writing to Google Sheet failed: {e}")

    print(f"  Feedback sweep complete: {summary}")
    return summary


def create_monthly_review_todo(jobtread_query_fn, admin_job_id, assignee_membership_id, sheet_url=""):
    """Create the monthly 'go review the feedback-loop Google Sheet' to-do
    (Jason's answer, Jul 2026: "monthly there is a reminder for pricing
    update"). Reuses the exact createTask mutation shape already proven by
    create_single_todo() in app.py, targeted at a fixed internal admin job
    since this reminder isn't tied to any one client job — Jason creates
    that one job once in JobTread (e.g. "AI Estimating — Internal") and
    sets its ID as FEEDBACK_ADMIN_JOB_ID. See CLAUDE.md for setup steps.
    """
    name = f"\U0001F4CA Monthly AI Estimate Pricing Review — {date.today().strftime('%B %Y')}"
    description = "Review this month's logged AI-estimate corrections and decide if any recurring patterns should become new pricing rules."
    if sheet_url:
        description += f"\n\nSheet: {sheet_url}"
    today = date.today().isoformat()
    try:
        jobtread_query_fn({
            "createTask": {
                "$": {
                    "name": name,
                    "description": description,
                    "isToDo": True,
                    "targetType": "job",
                    "targetId": admin_job_id,
                    "startDate": today,
                    "endDate": today,
                    "assignees": [{"membershipId": assignee_membership_id}],
                }
            }
        })
        print(f"  Monthly pricing-review to-do created on job {admin_job_id}")
        return True
    except Exception as e:
        print(f"  Monthly pricing-review to-do failed: {e}")
        return False
