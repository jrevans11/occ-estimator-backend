"""Site Scope Tool - the full build, CompanyCam side (phase 5).

Starts on its own once Jason sends the answers (scope_runs), on the run's own
background thread. Steps, each saved on the run as it finishes so a retry
only redoes what didn't work:

    write         one Claude call: apply the answers to the room file and write
                  the scope Page content (trades, alternates, missing info,
                  key photos). Works from the room file - the media was read
                  once, in the quick pass.
    descriptions  each visit photo gets the quick pass's description added
                  after whatever is already on it (never replaced)
    drawings      floor plans + elevations drawn from the updated room file
                  and uploaded as project photos (scope_uploads)
    page          the scope Page, "<Scope type> - Draft from <date> Site
                  Visit", with the drawings, conflicts, working quantities
                  (scope_quantities, code), answers and missing info

CompanyCam makes every Page shared (public link) and the API can't turn that
off (phase 3 live test), so the run says so and Jason turns sharing off in
CompanyCam (his call, Oct 20, 2026).

The JobTread budget (phase 6) builds from the same updated room file.
See docs/site-scope/site-scope-tool-build-plan.md (Phase 5 notes).
"""

import json
import re
import time
import traceback

import companycam_api as cc
import scope_quickpass as qp
import scope_uploads
import site_scope

STEPS = ("write", "descriptions", "drawings", "page")
MAX_DESCRIPTION_CHARS = 5000      # CompanyCam's limit on a photo description
MAX_KEY_PHOTOS = 12
PAGE_WIDTH = 624                  # table widths in a Page sum to at most this (Letter, 1" margins)
SHARING_NOTE = ("CompanyCam makes every new Page shared with a public link, and its API can't turn that off. "
                "Open the scope Page in CompanyCam and turn sharing off before anyone else sees the project.")
SRC_WORDS = {"dictated": "dictated / answered", "est": "est. from photos", "assumed": "assumed"}


class BuildError(Exception):
    """A problem to show Jason as-is."""


# ── Step 1: apply the answers, write the Page content ────────────────────────

INSTRUCTIONS = """You are the build step of Owners Choice Construction's Site Scope Tool. OCC is a residential repair and remodel contractor in Greenville, SC. Earlier, the quick pass read Jason's dictated notes and site photos into a structured room file and asked him a few cost-driver questions on site. He has answered them. You get the room file, the questions and his answers. You do two things.

1. APPLY THE ANSWERS TO THE ROOM FILE
- Where an answer gives a number or settles a choice, change the file to match. A measurement an answer gives (or a suggested number Jason accepted) becomes "src": "confirmed".
- Update scope_items to match (add, change or drop work the answers settle; keep ids stable, new ones continue the numbering), photo descriptions where an answer changes what a photo shows is happening, and conflicts (set "action" to how it was settled, e.g. "Settled in Q2 - 13'-0\\"").
- Remove flags the answers resolved. Keep every other flag.
- Change nothing else. Never change a dictated number unless an answer changes it, and never invent measurements.

2. WRITE THE SCOPE PAGE CONTENT
A working draft for Jason and his subs, not a client document.
- "scope_type": 2 to 5 words ending in "Scope", e.g. "Office Remodel Scope", "Exterior Repair Scope".
- "client_goals": short sentences, from the file.
- "trades": scope grouped by trade, in build order (demolition, framing/structure, plumbing, electrical, HVAC, insulation and drywall, doors/windows/trim, cabinets and countertops, tile, flooring, paint, exterior, cleanup). Group by trade, not by finding. Every scope item lands in exactly one trade. Each trade: {"trade": "Demolition and Framing", "sub": "the sub Jason addressed, or null", "lines": ["verb-first scope lines in build sequence"], "not_in_scope": ["things Jason said are out, or []"]}. A line with an (est.) or (assumed) number says so. Licensed-trade work (electrical, plumbing, HVAC, gas, structural) is described as Jason said it, never sized or engineered further; say "by a licensed sub" where that applies.
- "alternates": optional add-ons or either/or choices Jason mentioned: [{"name", "description" (what it changes and its tradeoff)}]. [] if none.
- "missing_info": what still has to be checked, measured or decided before bidding, one item each. Include the open flags.
- "key_photos": 4 to 10 photos that best show the work: [{"ref": "P3", "caption": "one plain sentence"}]. Only refs that are in the file's photos list.

WRITING RULES
No markdown, asterisks or emoji. A spaced hyphen " - " rather than a dash. Feet and inches as 13'-2". Plain sentences. No prices anywhere - pricing is the next step.

OUTPUT
Reply with one JSON object and nothing else:
{"room_file": {the whole updated room file, same format, every field kept}, "page": {"scope_type": "...", "client_goals": [...], "trades": [...], "alternates": [...], "missing_info": [...], "key_photos": [...]}}
"""


def system_blocks():
    text = INSTRUCTIONS
    doc = qp._read_doc("room-file-format.md")
    if doc:
        text += f"\n\n===== ROOM FILE FORMAT (room-file-format.md) =====\n\n{doc}"
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def build_content(run):
    job, rf = run["job"], dict(run["room_file"])
    rf.pop("project", None)   # code owns it
    lines = []
    for a in run.get("answers") or []:
        how = "accepted the suggested answer" if a.get("used_suggestion") else "answered"
        lines.append(f"{a['id']}. {a['question']}\nJason {how}: {a['answer'] or '(left blank)'}")
    qa = "\n\n".join(lines) or "(No questions were needed.)"
    visit = site_scope._pretty_date(run["visit_date"]) if run.get("visit_date") else "the site visit"
    return [
        {"type": "text", "text": f"Job {job.get('number')} - {job.get('name') or ''}, "
                                 f"{job.get('address') or 'no address on file'}. Site visit on {visit}."},
        {"type": "text", "text": "THE ROOM FILE (from the quick pass):\n" + json.dumps(rf, indent=1)},
        {"type": "text", "text": "THE QUESTIONS AND JASON'S ANSWERS:\n\n" + qa},
        {"type": "text", "text": "Apply the answers and write the Page content now, as one JSON object."},
    ]


def parse_answer(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict) or not isinstance(obj.get("room_file"), dict):
        raise ValueError("the reply has no room_file object")
    if not isinstance(obj.get("page"), dict):
        raise ValueError("the reply has no page object")
    return obj


def _strings(value, limit=None):
    out = [str(x).strip() for x in (value if isinstance(value, list) else []) if str(x or "").strip()]
    return out[:limit] if limit else out


def clean_page(page):
    """The page content with every field the right shape."""
    trades = []
    for t in page.get("trades") or []:
        if not isinstance(t, dict) or not str(t.get("trade") or "").strip():
            continue
        trades.append({"trade": str(t["trade"]).strip(), "sub": str(t.get("sub") or "").strip() or None,
                       "lines": _strings(t.get("lines")), "not_in_scope": _strings(t.get("not_in_scope"))})
    alternates = [{"name": str(a.get("name") or "").strip(), "description": str(a.get("description") or "").strip()}
                  for a in page.get("alternates") or [] if isinstance(a, dict) and a.get("name")]
    key_photos = [{"ref": str(k.get("ref") or "").strip(), "caption": str(k.get("caption") or "").strip()}
                  for k in page.get("key_photos") or [] if isinstance(k, dict) and k.get("ref")]
    scope_type = " ".join(str(page.get("scope_type") or "").split())[:60] or "Site Visit Scope"
    return {"scope_type": scope_type, "client_goals": _strings(page.get("client_goals")), "trades": trades,
            "alternates": alternates, "missing_info": _strings(page.get("missing_info")),
            "key_photos": key_photos[:MAX_KEY_PHOTOS]}


def apply_answers(run, log=print):
    """The write step. Returns (updated room file, page content, usage)."""
    label = f"Scope build job {run['job'].get('number')}"
    obj, usage = qp.ask_for_json(system_blocks(), build_content(run), parse_answer, label, log,
                                 refused="Claude declined to write this scope. Tap Build again; if it repeats, "
                                         "check the notes and answers.",
                                 retry_hint="Tap Build to try again.")
    rf = obj["room_file"]
    rf["schema"] = qp.SCHEMA
    rf["project"] = run["room_file"].get("project") or {}
    for key in ("rooms", "flags", "client_goals", "scope_items", "photos", "conflicts"):
        if not isinstance(rf.get(key), list):
            rf[key] = []
    qp.check_rooms(rf)
    return rf, clean_page(obj["page"]), usage


# ── Step 2: photo descriptions ───────────────────────────────────────────────

def description_text(entry):
    """What the tool adds to a photo: "Office, Wall 2: <description>". The
    drawing mark is kept out, or the quick pass would skip the photo next time."""
    desc = " ".join(str(entry.get("description") or "").split())
    if not desc:
        return ""
    where = str(entry.get("area") or "").strip()
    if entry.get("wall") not in (None, ""):
        where = f"{where}, Wall {entry['wall']}" if where else f"Wall {entry['wall']}"
    text = f"{where}: {desc}" if where else desc
    return re.sub(re.escape(site_scope.DRAWING_MARK), "scope tool", text, flags=re.IGNORECASE)


def write_descriptions(room_file, project_id, log=print):
    """Add each photo's description after what's already on it. Safe to run
    again: a photo that already carries the text is left alone."""
    written, already, skipped, errors, photo_ids = 0, 0, [], [], {}
    for entry in room_file.get("photos") or []:
        ref, pid = str(entry.get("ref") or ""), str(entry.get("id") or "").strip()
        ours = description_text(entry)
        if not pid or not ours:
            skipped.append(f"{ref or 'a photo'}: no photo id or description")
            continue
        try:
            photo = cc.get_photo(pid)
        except cc.CompanyCamError as e:
            errors.append(f"{ref} (photo {pid}): {e}")
            continue
        if photo.get("project_id") and str(photo["project_id"]) != str(project_id):
            skipped.append(f"{ref}: photo {pid} isn't on this project")
            continue
        photo_ids[ref] = pid
        existing = site_scope._photo_description(photo)
        if " ".join(ours.split()) in " ".join(existing.split()):
            already += 1
            continue
        room = MAX_DESCRIPTION_CHARS - (len(existing) + 2 if existing else 0)
        if room < 40:
            skipped.append(f"{ref}: its description is already at CompanyCam's {MAX_DESCRIPTION_CHARS}-character limit")
            continue
        text = f"{existing}\n\n{ours[:room]}" if existing else ours[:room]
        try:
            cc.set_photo_description(pid, text)
            written += 1
        except cc.CompanyCamError as e:
            errors.append(f"{ref} (photo {pid}): {e}")
    log(f"Scope build: photo descriptions - {written} written, {already} already there, "
        f"{len(skipped)} skipped, {len(errors)} failed")
    return {"done": not errors, "written": written, "already": already, "skipped": skipped, "errors": errors,
            "photo_ids": photo_ids}


# ── Step 3: drawings ─────────────────────────────────────────────────────────

def upload_run_drawings(room_file, project_id, previous=None, log=print, **upload_kwargs):
    """Draw every room and upload the drawings not already in CompanyCam from
    an earlier try (previous: that try's uploads)."""
    if not room_file.get("rooms"):
        return {"done": True, "uploads": [], "note": "No rooms were dictated wall by wall, so there is nothing to draw."}
    base = scope_uploads.public_base_url()
    if not base:
        raise BuildError("This server doesn't know its public address (RENDER_EXTERNAL_URL or SCOPE_PUBLIC_URL), "
                         "so CompanyCam can't fetch the drawings.")
    import scope_drawings   # Pillow loads only when drawings are made
    before = scope_uploads.memory_snapshot()
    t0 = time.time()
    drawn = scope_drawings.render_drawings(room_file)
    seconds = round(time.time() - t0, 2)
    visit = scope_drawings.visit_words(room_file.get("project") or {})

    def describe(d):
        return f"{d['title']} - drawn by the {site_scope.DRAWING_MARK} {visit} notes and photos. " \
               "For planning and quoting only."

    kept = {u["filename"]: u for u in (previous or []) if u.get("photo_id")}
    todo = [d for d in drawn["drawings"] if d["filename"] not in kept]
    fresh = scope_uploads.upload_drawings(project_id, todo, base, describe, **upload_kwargs) if todo else []
    by_name = dict(kept, **{u["filename"]: {k: v for k, v in u.items() if k != "temp_url"} for u in fresh})
    uploads = [dict(by_name[d["filename"]], title=d["title"]) for d in drawn["drawings"]]
    ok = [u for u in uploads if u.get("photo_id")]
    log(f"Scope build: {len(ok)} of {len(uploads)} drawings in CompanyCam ({seconds}s to draw, memory before "
        f"{before}, after {scope_uploads.memory_snapshot()})")
    return {"done": len(ok) == len(uploads), "uploads": uploads, "flags": drawn["flags"], "render_seconds": seconds}


# ── Step 4: the scope Page ───────────────────────────────────────────────────

def _t(s, marks=None):
    node = {"type": "text", "text": str(s)}
    if marks:
        node["marks"] = marks
    return node


def _p(*parts):
    parts = [p if isinstance(p, dict) else _t(p) for p in parts if p]
    return {"type": "paragraph", "content": parts} if parts else {"type": "paragraph"}


def _h(text, level=2):
    return {"type": "heading", "attrs": {"level": level}, "content": [_t(text)]}


def _bullets(items):
    return {"type": "bulletList", "content": [{"type": "listItem", "content": [_p(i)]} for i in items]}


def _tasks(items):
    return {"type": "taskList", "content": [{"type": "taskItem", "attrs": {"checked": False}, "content": [_p(i)]}
                                            for i in items]}


def _table(headers, rows, widths):
    def cell(kind, text, w):
        return {"type": kind, "attrs": {"colspan": 1, "rowspan": 1, "colwidth": [w]}, "content": [_p(text or "-")]}
    out = [{"type": "tableRow", "content": [cell("tableHeader", h, w) for h, w in zip(headers, widths)]}]
    out += [{"type": "tableRow", "content": [cell("tableCell", c, w) for c, w in zip(r, widths)]} for r in rows]
    return {"type": "table", "content": out}


def _photo_group(photos):
    """photos: [(photo id, caption)]."""
    cards = [{"type": "flexPhoto", "attrs": {"photoId": str(pid)},
              "content": [{"type": "flexPhoto_textSlot", "content": [_p(caption)]}]} for pid, caption in photos]
    return {"type": "flexGroup", "attrs": {"orientation": "vertical", "size": "large", "aspectRatio": "original",
                                           "showPhotoText": True, "showCaptureDetails": False, "showTags": False,
                                           "showPhotoLocation": False}, "content": cards}


def _section(kind, title, body):
    return {"type": "section", "attrs": {"sectionType": kind}, "content": [_h(title)] + body}


def _fmt_qty(q):
    v = q["value"]
    v = int(v) if isinstance(v, float) and v.is_integer() else v
    return f"{v} {q['unit']}"


def page_title(page, visit_date):
    when = site_scope._pretty_date(visit_date) if visit_date else "Site"
    return f"{page['scope_type']} - Draft from {when} Site Visit"


def page_doc(run, room_file, page, drawings, photo_ids, photos=True, tables=True):
    """The scope Page as a TipTap document. photos/tables False give plainer
    versions, used if CompanyCam refuses the full one."""
    inputs = run.get("inputs") or {}
    answers = run.get("answers") or []
    visit = site_scope._pretty_date(run["visit_date"]) if run.get("visit_date") else "the site visit"
    made = [f"{inputs.get('notes', 0)} dictated note(s)", f"{inputs.get('photos', 0)} photo(s)"]
    if inputs.get("video_transcripts"):
        made.append(f"{inputs['video_transcripts']} video transcript(s)")
    italic = [{"type": "italic"}]
    content = [_p(_t(f"Draft by the OCC Site Scope Tool from the {visit} visit ({', '.join(made)}) and "
                     f"Jason's answers to {len(answers)} question(s). A working draft for OCC and its subs - not a "
                     "client document. (est.) = estimated from a photo, (assumed) = a default with nothing to go "
                     "on; check both before pricing.", italic))]

    def block(kind, title, body):
        """A titled block: a Page section when allowed, else a plain heading + body."""
        content.extend([_section(kind, title, body)] if tables else [_h(title)] + body)

    if page["client_goals"]:
        content += [_h("Client Goals"), _bullets(page["client_goals"])]

    content.append(_h("Scope by Trade"))
    for t in page["trades"]:
        content.append(_h(t["trade"], 3))
        if t["sub"]:
            content.append(_p(f"Sub: {t['sub']}"))
        if t["lines"]:
            content.append(_bullets(t["lines"]))
        if t["not_in_scope"]:
            content.append(_p("Not in scope: " + "; ".join(t["not_in_scope"])))
    if not page["trades"]:
        content.append(_p("No scope lines were written - see the notes and answers."))

    if page["alternates"]:
        content += [_h("Optional Alternates"),
                    _bullets([f"{a['name']} - {a['description']}" if a["description"] else a["name"]
                              for a in page["alternates"]])]

    ready = [(u["photo_id"], u.get("title") or u.get("drawing") or "Drawing") for u in drawings if u.get("photo_id")]
    if ready:
        if photos:
            block("photo", "Drawings", [_p("Drawn from the room file. Tags show where each number came from."),
                                        _photo_group(ready)])
        else:
            content += [_h("Drawings"), _p("Added to the project photos: " + "; ".join(t for _, t in ready) + ".")]

    conflicts = [c for c in room_file.get("conflicts") or [] if isinstance(c, dict)]
    if conflicts:
        rows = [[c.get("what"), c.get("source_a"), c.get("source_b"), c.get("action")] for c in conflicts]
        if tables:
            block("table", "Measurement Conflicts",
                  [_table(["What", "Source A", "Source B", "Action"], rows, [156, 156, 156, 156])])
        else:
            content += [_h("Measurement Conflicts"),
                        _bullets([f"{r[0]}: {r[1]} vs. {r[2]} - {r[3]}" for r in rows])]

    try:
        import scope_quantities   # loads Pillow (through scope_drawings), so only here
        quantities = scope_quantities.all_quantities(room_file)
    except Exception as e:   # a quantity bug must not cost Jason the whole Page
        print(f"Scope build: quantities failed: {e}\n{traceback.format_exc()}")
        quantities = []
    if quantities:
        rows = [[q["item"], _fmt_qty(q), q["basis"], SRC_WORDS.get(q["src"], q["src"])] for q in quantities]
        intro = _p("Planning numbers, not ordering numbers. Each keeps the weakest source it came from.")
        if tables:
            block("table", "Working Quantities",
                  [intro, _table(["Item", "Qty", "Basis", "Source"], rows, [222, 80, 214, 108])])
        else:
            content += [_h("Working Quantities"), intro,
                        _bullets([f"{r[0]}: {r[1]} ({r[2]}; {r[3]})" for r in rows])]

    if answers:
        content += [_h("Answers From the Site Questions"),
                    _bullets([f"{a['question']} - {a['answer'] or '(left blank)'}"
                              + (" (suggested answer accepted)" if a.get("used_suggestion") else "")
                              for a in answers])]

    missing, seen = [], set()
    flags = list(room_file.get("flags") or []) + [f"{r.get('name')}: {f}" for r in room_file.get("rooms") or []
                                                  for f in r.get("flags") or []]
    for item in page["missing_info"] + flags:
        key = " ".join(item.lower().split())
        if key not in seen:
            seen.add(key)
            missing.append(item)
    if missing:
        content += [_h("Missing Info Before Bidding"), _tasks(missing)]

    refs = {p.get("ref"): p for p in room_file.get("photos") or []}
    key = [(photo_ids[k["ref"]], k["caption"] or refs.get(k["ref"], {}).get("description") or "")
           for k in page["key_photos"] if k["ref"] in photo_ids]
    if key:
        if photos:
            block("photo", "Key Photos", [_p("From the visit."), _photo_group(key)])
        else:
            content += [_h("Key Photos"), _bullets([f"{cap} (CompanyCam photo {pid})" for pid, cap in key])]
    return {"type": "doc", "content": content}


def _free_title(project_id, title):
    """The title, or "<title> (2)" etc. if a Page already has it (a second run
    on the same visit makes its own Page rather than overwriting one Jason may
    have edited)."""
    try:
        taken = {(p.get("title") or "").strip().lower() for p in cc.list_project_pages(project_id)}
    except cc.CompanyCamError:
        return title
    n, out = 1, title
    while out.lower() in taken:
        n += 1
        out = f"{title} ({n})"
    return out


def write_page(run, room_file, page, drawings, photo_ids, log=print):
    """Create the scope Page. If CompanyCam refuses the full layout (photo
    cards or tables), try plainer versions before giving up."""
    project_id = run["project_id"]
    title = _free_title(project_id, page_title(page, run.get("visit_date")))
    last = None
    for photos, tables, what in ((True, True, "full"), (False, True, "without photo cards"),
                                 (False, False, "plain")):
        doc = page_doc(run, room_file, page, drawings, photo_ids, photos=photos, tables=tables)
        try:
            created = cc.create_page(project_id, title, doc)
        except cc.CompanyCamError as e:
            last = e
            if e.status in (400, 422):
                log(f"Scope build: CompanyCam refused the {what} scope Page ({e}); trying a plainer one")
                continue
            raise
        page_id = str(created.get("id") or "")
        if not page_id:
            raise BuildError("CompanyCam didn't return an id for the scope Page.")
        log(f"Scope build: scope Page {page_id} \"{title}\" written ({what}); shared={created.get('shared')}")
        return {"done": True, "page_id": page_id, "title": title, "layout": what,
                "shared": bool(created.get("shared", True))}
    raise BuildError(f"CompanyCam refused the scope Page every way it was tried ({last}).")


# ── The whole build ──────────────────────────────────────────────────────────

def run_build(run, save, log=print, upload_kwargs=None):
    """Run the steps not done yet. Updates run["build"] (and run["room_file"]
    once the answers are applied), calling save(run) after each step. Never
    raises - a failure is recorded on the run."""
    b = run.setdefault("build", {})
    steps = b.setdefault("steps", {})
    b.update(state="building", error=None, started_at=int(time.time()), finished_at=None)
    t0 = time.time()
    try:
        save(run)
        if not steps.get("write", {}).get("done"):
            room_file, page, usage = apply_answers(run, log)
            run.setdefault("quick_room_file", run["room_file"])
            run["room_file"] = room_file
            b["page_content"], b["usage"] = page, usage
            steps["write"] = {"done": True}
            save(run)
        room_file, page, project_id = run["room_file"], b["page_content"], run["project_id"]

        def step(name, fn):
            if steps.get(name, {}).get("done"):
                return
            try:
                steps[name] = fn()
            except (BuildError, cc.CompanyCamError) as e:
                steps[name] = dict(steps.get(name) or {}, done=False, error=str(e))
                log(f"Scope build {name} failed: {e}")
            except Exception as e:
                steps[name] = dict(steps.get(name) or {}, done=False, error=f"Unexpected error: {e}")
                log(f"Scope build {name} failed: {e}\n{traceback.format_exc()}")
            save(run)

        step("descriptions", lambda: write_descriptions(room_file, project_id, log))
        step("drawings", lambda: upload_run_drawings(room_file, project_id, (steps.get("drawings") or {})
                                                     .get("uploads"), log, **(upload_kwargs or {})))
        step("page", lambda: write_page(run, room_file, page, (steps.get("drawings") or {}).get("uploads") or [],
                                        (steps.get("descriptions") or {}).get("photo_ids") or {}, log))
        left = [n for n in STEPS if not steps.get(n, {}).get("done")]
        b["state"] = "partial" if left else "done"
        b["error"] = ("Not everything finished: " + ", ".join(left) + ". Tap Build to finish it.") if left else None
    except (qp.QuickPassError, qp.ClaudeError) as e:
        b.update(state="failed", error=str(e))
        log(f"Scope build failed: {e}")
    except Exception as e:
        b.update(state="failed", error=f"Unexpected error: {e}")
        log(f"Scope build failed: {e}\n{traceback.format_exc()}")
    b["finished_at"] = int(time.time())
    b["seconds"] = round(time.time() - t0, 1)
    try:
        save(run)
    except Exception as e:
        log(f"Scope build: could not save the result: {e}")
    cost = (b.get("usage") or {}).get("cost_usd")
    log(f"Scope build job {run['job'].get('number')}: {b['state']}"
        f"{', cost $%.4f' % cost if cost is not None else ''}, {b['seconds']}s")
    return run


def public(run):
    """The build as the page sees it (no page content or room file)."""
    b = run.get("build")
    if not b:
        return None
    steps = b.get("steps") or {}
    desc, dwg, pg = steps.get("descriptions") or {}, steps.get("drawings") or {}, steps.get("page") or {}
    uploads = dwg.get("uploads") or []
    usage = b.get("usage") or {}
    out = {
        "state": b.get("state"), "error": b.get("error"), "seconds": b.get("seconds"),
        "started_at": b.get("started_at"), "finished_at": b.get("finished_at"),
        "steps": {n: bool((steps.get(n) or {}).get("done")) for n in STEPS},
        "step_errors": {n: (steps.get(n) or {}).get("error") for n in STEPS if (steps.get(n) or {}).get("error")},
        "descriptions": {"written": desc.get("written"), "already": desc.get("already"),
                         "skipped": len(desc.get("skipped") or []), "failed": len(desc.get("errors") or [])}
        if desc else None,
        "drawings": {"in_companycam": sum(1 for u in uploads if u.get("photo_id")), "total": len(uploads),
                     "note": dwg.get("note")} if dwg else None,
        "page": {"id": pg.get("page_id"), "title": pg.get("title"), "shared": pg.get("shared")}
        if pg.get("page_id") else None,
        "usage": {k: usage.get(k) for k in ("model", "effort", "input_tokens", "output_tokens",
                                            "cache_read_input_tokens", "cache_creation_input_tokens",
                                            "cost_usd", "calls")},
        "project_url": f"https://app.companycam.com/projects/{run.get('project_id')}",
    }
    if pg.get("page_id") and pg.get("shared"):
        out["sharing_note"] = SHARING_NOTE
    return out
