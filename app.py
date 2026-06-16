import os
import json
import base64
import re
import urllib.request
import urllib.parse
import io
from http.server import HTTPServer, BaseHTTPRequestHandler

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SENDGRID_KEY = os.environ.get("SENDGRID_API_KEY", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "jason@ownerschoiceconstruction.com")
WUFOO_API_KEY = os.environ.get("WUFOO_API_KEY", "")

SYSTEM_PROMPT = """You are an expert estimator for Owners Choice Construction LLC, a residential repair contractor in Greenville/Upstate SC. You create closing repair estimates from home inspection reports and repair addendums.

COMPANY INFO:
- Owners Choice Construction LLC
- Jason Evans
- (864) 252-4999
- jason@ownerschoiceconstruction.com
- 3122 Wade Hampton Blvd, Taylors, SC 29687

PRICING RULES (client-facing prices after markup):
- In-house labor: $89/hr billed (min 1 hr = $89, 1.5 hrs = $133.50, 2 hrs = $178)
- Subcontractor work: 45% markup over their cost
- Simple fixes (secure, tighten, adjust): $133 to $222
- Seal/caulk/minor exterior: $178 to $290
- Electrical minor (outlets, screws, bulbs): $60 to $217
- Smoke/CO detectors: $273 to $362
- GFCI outlet install: $178 to $217
- Window glass single pane: around $368
- Window glass double pane: around $762
- Plumbing minor (drain, shower head, valve adjust): $133 to $357
- Expansion tank install: $571 to $645
- Crawlspace insulation (sub): $1200 to $1740
- Foundation vent screen: $178 to $218
- Roofing evaluation plus selective repair (sub): $725 to $3300
- Vinyl siding repairs: $1200 to $2000
- Masonry/retaining wall (sub): $650 to $1500
- HVAC evaluation (sub): $285 to $357
- Garbage disposal replacement: $500 to $634
- Chimney cap measure/fab/install: around $1214
- Exterior door jamb/rot repair: $500 to $750
- Downspout extension/repair: $178 to $260
- Attic pulldown stair adjustment: $133 to $178
- Dryer vent cap replacement: $218 to $260
- Active leak evaluation: $133 to $178
- Panel screw replacement: $45 to $65
- Gas bonding: around $652
- Water heater relocation (sub): $3500 to $5300
- Subfloor evaluation: $133 to $178

SCOPE RULES:
1. Only include items in a general contractor scope.
2. Do NOT include: septic/sewer, termite, cosmetic items like carpet stains or paint.
3. Group related items when it makes sense.
4. Use inspection report section numbers as line item title prefix.
5. Write scope descriptions using bullet points starting with a dash.
6. Add NOTE: callouts where there are important caveats.

Always start with Disclaimer line item at $0.00:
This estimate has been prepared based on information provided in the inspection report and limited available details. Actual costs may vary depending on site conditions, accessibility, extent of damage, and any additional work required that was not visible or documented in the report. Any necessary adjustments to scope or pricing will be communicated and approved prior to proceeding with the work.

OUTPUT: Respond with ONLY valid JSON, no markdown:
{
  "property_address": "address",
  "client_name": "name",
  "client_phone": "phone",
  "client_email": "email",
  "line_items": [
    {"title": "Disclaimer", "description": "This estimate has been prepared...", "price": 0, "notes": null},
    {"title": "X.X.X - Title", "description": "- bullet one\n- bullet two", "price": 285.61, "notes": null}
  ],
  "total": 0.00,
  "skipped_items": ["item - reason"]
}"""


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}


def download_file(url):
    """Download a file from Wufoo, following redirects with auth."""
    import http.client
    auth = base64.b64encode(f"{WUFOO_API_KEY}:footastic".encode()).decode("utf-8")
    for _ in range(5):
        parts = urllib.parse.urlparse(url)
        conn = http.client.HTTPSConnection(parts.netloc, timeout=60)
        headers = {"User-Agent": "Mozilla/5.0"}
        if "wufoo.com" in parts.netloc:
            headers["Authorization"] = f"Basic {auth}"
        conn.request("GET", parts.path + ("?" + parts.query if parts.query else ""), headers=headers)
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308):
            url = resp.getheader("Location")
            if not url.startswith("http"):
                url = f"https://{parts.netloc}{url}"
            continue
        if resp.status == 200:
            return resp.read()
        raise Exception(f"HTTP {resp.status} downloading {url}")
    raise Exception("Too many redirects")


def get_file_extension(url, filename=""):
    """Determine file extension from URL or filename."""
    for src in [filename, url]:
        if src:
            ext = os.path.splitext(src.split("?")[0].lower())[1]
            if ext:
                return ext
    return ".pdf"


def extract_pdf_text(pdf_bytes):
    """Extract text from PDF bytes using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        return text[:60000]
    except Exception as e:
        print(f"  pypdf extraction failed: {e}")
        return ""


def is_image(ext):
    return ext.lower() in IMAGE_EXTENSIONS


def build_claude_content(files_data, client_name, client_phone, client_email, address, notes):
    """
    Build the Claude API message content list.
    files_data: list of {"bytes": ..., "ext": ..., "label": ...}
    Returns a list of content blocks (text and/or image).
    """
    content = []

    intro = f"""Generate a closing repairs estimate for Owners Choice Construction.

Client name: {client_name}
Client phone: {client_phone}
Client email: {client_email}
Property address: {address}
{f"Additional notes from form: {notes}" if notes else ""}

Only include items within a general contractor scope. Cross-reference the addendum with the inspection report to write accurate scope descriptions.
"""
    content.append({"type": "text", "text": intro})

    for fd in files_data:
        ext = fd["ext"].lower()
        label = fd["label"]
        file_bytes = fd["bytes"]

        if is_image(ext):
            media_type = IMAGE_MEDIA_TYPES.get(ext, "image/jpeg")
            b64 = base64.b64encode(file_bytes).decode("utf-8")
            content.append({"type": "text", "text": f"\n=== {label} (image) ==="})
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64
                }
            })
        else:
            # Treat as PDF
            text = extract_pdf_text(file_bytes)
            if text.strip():
                content.append({"type": "text", "text": f"\n=== {label} ===\n{text}"})
            else:
                # PDF text extraction failed — try sending as base64 document
                b64 = base64.b64encode(file_bytes).decode("utf-8")
                content.append({"type": "text", "text": f"\n=== {label} (PDF document) ==="})
                content.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64
                    }
                })

    content.append({
        "type": "text",
        "text": "\nRespond with ONLY the raw JSON object. No markdown, no explanation."
    })

    return content


def has_enough_info(files_data, notes):
    """Check if we have enough content to attempt an estimate."""
    has_file_content = any(fd["bytes"] and len(fd["bytes"]) > 100 for fd in files_data)
    has_notes = bool(notes and notes.strip() and notes.strip().lower() not in ["nope!", "no", "n/a", "none"])
    return has_file_content or has_notes


def call_claude(content_blocks):
    """Call Claude API with content blocks."""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content_blocks}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "pdfs-2024-09-25"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        result = json.loads(r.read().decode("utf-8"))

    raw = "".join(block.get("text", "") for block in result.get("content", []))
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise Exception(f"No JSON in Claude response: {raw[:500]}")
    return json.loads(match.group(0))


def fmt(price):
    if price == 0:
        return "$0.00"
    return "$" + f"{float(price):,.2f}"


def build_estimate_email_html(estimate):
    from datetime import datetime, timedelta
    today = datetime.now()
    issue = today.strftime("%B %d, %Y")
    expires = (today + timedelta(days=14)).strftime("%B %d, %Y")
    rows = ""
    for item in estimate.get("line_items", []):
        desc = item.get("description", "") or ""
        bullets = ""
        for line in desc.split("\n"):
            if line.startswith("-"):
                bullets += f"<li style='margin:2px 0;color:#555;font-size:12px'>{line[1:].strip()}</li>"
        if item["title"] == "Disclaimer":
            desc_html = f"<p style='margin:4px 0 0;color:#555;font-size:12px'>{desc}</p>"
        else:
            desc_html = f"<ul style='margin:4px 0 0 16px;padding:0'>{bullets}</ul>"
        note_html = f"<p style='margin:5px 0 0;font-size:11.5px;color:#666;font-style:italic'>{item['notes']}</p>" if item.get("notes") else ""
        rows += f"""<tr>
            <td style='padding:10px 0;border-bottom:0.5px solid #ddd;vertical-align:top'>
                <strong style='font-size:13px'>{item["title"]}</strong>
                {desc_html}{note_html}
            </td>
            <td style='padding:10px 0 10px 16px;border-bottom:0.5px solid #ddd;vertical-align:top;text-align:right;white-space:nowrap;font-weight:600;font-size:13px'>
                {fmt(item["price"])}
            </td>
        </tr>"""

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:Arial,sans-serif;font-size:13px;color:#1a1a1a;line-height:1.5;padding:40px;max-width:820px;margin:0 auto}}</style>
</head><body>
<div style='display:flex;justify-content:space-between;padding-bottom:14px;border-bottom:1px solid #bbb;margin-bottom:14px'>
    <div style='font-size:20px;font-weight:700'>Closing Repairs Estimate</div>
    <div style='text-align:right;font-size:12px;color:#555'><div>Issue Date {issue}</div><div>Expires {expires}</div></div>
</div>
<div style='display:grid;grid-template-columns:1fr 1fr;gap:20px;padding-bottom:14px;border-bottom:1px solid #bbb;margin-bottom:14px'>
    <div>
        <div style='font-size:10px;color:#888;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px'>Prepared By</div>
        <strong>Jason Evans</strong><div>Owners Choice Construction</div>
        <div>(864) 252-4999</div><div>jason@ownerschoiceconstruction.com</div>
        <div>3122 Wade Hampton Blvd, Taylors, SC 29687</div>
    </div>
    <div>
        <div style='font-size:10px;color:#888;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px'>Prepared For</div>
        <strong>{estimate.get("client_name","")}</strong>
        <div>{estimate.get("property_address","")}</div>
        <div>{estimate.get("client_phone","")}</div>
        <div>{estimate.get("client_email","")}</div>
    </div>
</div>
<div style='font-size:10px;color:#888;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px'>Closing Repairs Estimate Details</div>
<div style='font-size:15px;font-weight:700;margin:2px 0 14px'>{estimate.get("property_address","")}</div>
<table style='width:100%;border-collapse:collapse'>
    <thead><tr>
        <th style='font-size:10px;color:#888;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:5px 0;border-bottom:1.5px solid #999;text-align:left'>Description</th>
        <th style='font-size:10px;color:#888;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:5px 0;border-bottom:1.5px solid #999;text-align:right'>Total</th>
    </tr></thead>
    <tbody>{rows}</tbody>
    <tfoot><tr>
        <td style='padding:12px 0;border-top:1.5px solid #999;font-weight:700;font-size:14px'>TOTAL</td>
        <td style='padding:12px 0;border-top:1.5px solid #999;font-weight:700;font-size:14px;text-align:right'>{fmt(estimate.get("total",0))}</td>
    </tr></tfoot>
</table>
</body></html>"""


def build_review_email_html(client_name, client_phone, client_email, address, notes, files_data, reason):
    """Email to Jason when we can't generate a full estimate."""
    file_list = "".join(
        f"<li>{fd['label']}: {fd['ext']} ({len(fd['bytes'])} bytes)</li>"
        for fd in files_data
    )
    return f"""<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;font-size:13px;color:#1a1a1a;padding:30px;max-width:700px'>
<h2 style='color:#c0392b'>⚠️ Manual Review Required — OCC Estimator</h2>
<p>A new submission came in but the system could not generate a full estimate automatically. Please review and follow up manually.</p>
<hr>
<h3>Submission Details</h3>
<table style='border-collapse:collapse;width:100%'>
<tr><td style='padding:6px;font-weight:bold;width:150px'>Name</td><td style='padding:6px'>{client_name}</td></tr>
<tr><td style='padding:6px;font-weight:bold'>Phone</td><td style='padding:6px'>{client_phone}</td></tr>
<tr><td style='padding:6px;font-weight:bold'>Email</td><td style='padding:6px'>{client_email}</td></tr>
<tr><td style='padding:6px;font-weight:bold'>Address</td><td style='padding:6px'>{address}</td></tr>
<tr><td style='padding:6px;font-weight:bold'>Notes</td><td style='padding:6px'>{notes or "None"}</td></tr>
</table>
<h3>Files Submitted</h3>
<ul>{file_list}</ul>
<h3>Reason Auto-Estimate Failed</h3>
<p style='color:#c0392b'>{reason}</p>
<hr>
<p style='color:#888;font-size:11px'>Sent by OCC Estimator Backend</p>
</body></html>"""


def send_email(subject, html_body):
    payload = json.dumps({
        "personalizations": [{"to": [{"email": NOTIFY_EMAIL}]}],
        "from": {"email": "jason@ownerschoiceconstruction.com", "name": "OCC Estimator"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}]
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {SENDGRID_KEY}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OCC Estimator Backend is running!")

    def do_POST(self):
        client_name = ""
        client_phone = ""
        client_email = ""
        address = ""
        notes = ""
        files_data = []

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = urllib.parse.parse_qs(body)

            def get(key):
                return data.get(key, [""])[0]

            first = get("Field1")
            last = get("Field2")
            client_name = f"{first} {last}".strip()
            client_phone = get("Field3")
            client_email = get("Field4")
            street = get("Field5")
            city = get("Field7")
            state = get("Field8")
            zip_code = get("Field9")
            address = f"{street}, {city}, {state} {zip_code}".strip(", ")
            notes = get("Field121")
            extra_notes = get("Field424")
            if extra_notes:
                notes = f"{notes}\n{extra_notes}".strip()

            print(f"New submission: {client_name} - {address}")

            # Collect all uploaded files
            for field_id, label in [("Field12", "Inspection Report"), ("Field13", "Repair Addendum"), ("Field426", "Additional File")]:
                url = get(f"{field_id}-url")
                filename = get(field_id)
                if not url:
                    continue
                ext = get_file_extension(url, filename)
                print(f"  Downloading {label}: {filename} ({ext})")
                try:
                    file_bytes = download_file(url)
                    print(f"  Downloaded {len(file_bytes)} bytes")
                    files_data.append({"bytes": file_bytes, "ext": ext, "label": label, "filename": filename})
                except Exception as e:
                    print(f"  Failed to download {label}: {e}")
                    files_data.append({"bytes": b"", "ext": ext, "label": label, "filename": filename})

            # Check if we have enough to work with
            if not has_enough_info(files_data, notes):
                reason = "No usable files were downloaded and no repair notes were provided in the form."
                print(f"Insufficient info — sending review email")
                html = build_review_email_html(client_name, client_phone, client_email, address, notes, files_data, reason)
                send_email(f"⚠️ Manual Review Needed - {address}", html)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK - review email sent")
                return

            # Build Claude content and generate estimate
            content = build_claude_content(files_data, client_name, client_phone, client_email, address, notes)
            estimate = call_claude(content)
            print(f"Estimate total: {estimate.get('total', 0)}")

            html = build_estimate_email_html(estimate)
            subject = f"Closing Repairs Estimate - {address} - {fmt(estimate.get('total', 0))}"
            send_email(subject, html)
            print(f"Estimate email sent to {NOTIFY_EMAIL}")

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

            # Try to send a fallback review email so Jason knows something came in
            try:
                reason = f"System error: {str(e)}"
                html = build_review_email_html(client_name, client_phone, client_email, address, notes, files_data, reason)
                send_email(f"⚠️ Manual Review Needed - {address or 'Unknown Address'}", html)
                print("Fallback review email sent")
            except Exception as e2:
                print(f"Failed to send fallback email: {e2}")

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting on port {port}")
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
