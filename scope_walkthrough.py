"""Site Scope Tool - walkthrough recordings.

Jason taps "Start walkthrough" on the scope page. An iPhone Shortcut starts a
Voice Memo and opens the CompanyCam camera on the job, so he can talk the
whole time he takes photos (a web page's microphone stops when he switches
apps; Voice Memos keeps going). Afterwards he sends the memo from the scope
page; scope_notes.py takes it like a dictated note (kind "walkthrough") and
this module:

1. picks when the recording started (the Start tap, or the file's time minus
   its length),
2. finds the CompanyCam photos taken while it ran, and
3. lays the word-for-word transcript out for the Site Visit Notes Page with a
   marker where each photo was taken: "[Photo 3, 2:16:40 PM]".

The note uses the same heading and "Recorded X to Y" line as any other note,
so the quick pass and the preview read it with no changes.
"""

import companycam_api as cc
import site_scope

# The Start tap counts as the recording's start only if the recording could
# have begun then: no more than this long between the tap and the end of the
# recording (Voice Memos may be stopped a while before the file is sent).
MAX_TAP_GAP_SECONDS = 2 * 60 * 60
# The Shortcut takes a second or two between the tap and the memo starting,
# and phone clocks drift a little.
TAP_SLACK_SECONDS = 120
# Photos this close outside the recording still count (taken just before
# starting or right after stopping).
PHOTO_MARGIN_SECONDS = 15


def _clock(ts):
    return site_scope.to_local(ts).strftime("%I:%M:%S %p").lstrip("0")


def choose_start(tapped_ts, file_ts, duration, received_ts):
    """When the recording started, as (unix seconds, source).

    tapped_ts: when Start walkthrough was tapped on this phone (or None).
    file_ts: the recording file's time from the phone (when it was saved -
             about when it was stopped), or None.
    duration: the recording's length in seconds (from Deepgram).
    received_ts: when the server got it.
    source is "tap", "file" or "upload" (received time minus the length).
    """
    dur = float(duration or 0)
    latest_end = file_ts or received_ts
    if tapped_ts:
        gap = latest_end - tapped_ts
        if tapped_ts + dur <= latest_end + TAP_SLACK_SECONDS and gap <= dur + MAX_TAP_GAP_SECONDS:
            return int(tapped_ts), "tap"
    if file_ts:
        return int(round(file_ts - dur)), "file"
    return int(round(received_ts - dur)), "upload"


def photos_during(photos, start_ts, duration):
    """The project's photos taken while the recording ran, oldest first, as
    [{"id", "at"}]. The tool's own drawings are skipped."""
    end_ts = start_ts + float(duration or 0)
    out = []
    for p in photos:
        if site_scope.is_tool_drawing(p):
            continue
        at = cc.parse_timestamp(p.get("captured_at"))
        if at is None or not (start_ts - PHOTO_MARGIN_SECONDS <= at <= end_ts + PHOTO_MARGIN_SECONDS):
            continue
        out.append({"id": str(p.get("id") or ""), "at": at})
    out.sort(key=lambda x: x["at"])
    return out


def marker(number, at):
    return f"[Photo {number}, {_clock(at)}]"


def layout(paragraphs, photos, start_ts):
    """The transcript as paragraphs of pieces, with photo markers placed
    before the first sentence said after each photo was taken.

    paragraphs: scope_transcribe.parse_timed_response's paragraphs.
    Returns [[("text", str) | ("photo", str), ...], ...]."""
    queue = [(i + 1, p["at"]) for i, p in enumerate(photos)]
    out = []
    for sentences in paragraphs:
        pieces = []
        for s in sentences:
            while queue and queue[0][1] <= start_ts + s["start"]:
                n, at = queue.pop(0)
                pieces.append(("photo", marker(n, at)))
            pieces.append(("text", s["text"]))
        if pieces:
            out.append(pieces)
    if queue:
        out.append([("photo", marker(n, at)) for n, at in queue])
    return out


def plain_text(laid_out):
    """The laid-out transcript as text, paragraphs split by blank lines."""
    return "\n\n".join(" ".join(t for _, t in pieces) for pieces in laid_out)


def meta_sentence(photo_count, source):
    """The sentence after "Recorded X to Y (m:ss of audio)." on the Page."""
    photos = ("no CompanyCam photos were taken while it ran" if photo_count == 0 else
              f"{photo_count} CompanyCam photo{'' if photo_count == 1 else 's'} taken during it "
              f"{'is' if photo_count == 1 else 'are'} marked in the text")
    when = {"file": " Start time worked out from the recording file.",
            "upload": " Start time worked out from when it was sent, so photo markers may be off."}.get(source, "")
    return f"Walkthrough recording; {photos}.{when}"


def body_nodes(laid_out):
    """TipTap paragraphs for the laid-out transcript; markers in italics."""
    nodes = []
    for pieces in laid_out:
        content = []
        for i, (kind, text) in enumerate(pieces):
            node = {"type": "text", "text": (" " if i else "") + text}
            if kind == "photo":
                if i:
                    content.append({"type": "text", "text": " "})
                node = {"type": "text", "text": text, "marks": [{"type": "italic"}]}
            content.append(node)
        nodes.append({"type": "paragraph", "content": content})
    return nodes
