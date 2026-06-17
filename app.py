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

PRICING RULES:
- In-house labor: $89/hr billed (min 1 hr = $89, 1.5 hrs = $133.50, 2 hrs = $178)
- Material markup: cost + 65%
- Subcontractor markup: cost + 45%

REPAIR PRICING REFERENCE (36 real OCC estimates + 197 inspection reports)
Adjust for actual scope/site conditions. Apply $89/hr labor + 65% material markup for in-house work.

EXTERIOR:
  - Secure/repair/replace damaged siding: ~$897
  - Repair/seal cracks in driveway: ~$152
  - Secure/install handrail or railing: ~$428
  - Seal exterior penetrations, gaps, and holes: ~$319
  - Repair/seal cracks in patio or walkway: ~$226
  - Repair/replace fascia and soffit: ~$981
  - Repoint or seal mortar joints: ~$204
  - Install/extend downspouts and splash blocks: ~$282
  - Repair/replace deck boards: ~$640
  - Repair/replace fence boards or sections: ~$534
  - Paint or repaint exterior wood trim/fascia/siding: ~$356
  - Repair/treat wood rot at exterior trim or framing: ~$888
  - Replace or repair window/door screens: ~$160
  - Repair/replace exterior stairs or walkway settlement: ~$610
  - Clean gutters and adjust for proper drainage: ~$379
  - Replace damaged weatherstripping on exterior doors: ~$261
  - Repair or install deck stair riser boards: ~$610
  - Install missing deck joist hangers or ledger strips: ~$2,162

PLUMBING:
  - Secure loose toilet to floor / replace wax ring: ~$218
  - Install expansion tank on water heater: ~$121
  - Repair or replace sink stopper / pop-up assembly: $221-$299
  - Seal gap at bathtub/shower surround, floor, or wall: ~$100
  - Repair or replace leaking showerhead / secure showerhead arm: ~$278
  - Adjust water heater temperature to safe level (120°F): ~$134
  - Repair/replace leaking exterior hose bib or faucet: ~$224
  - Secure/repair loose hose bib to exterior wall: ~$283
  - Install or adjust pressure regulating valve (PRV): ~$134
  - Repair active leak at drain line or plumbing fixture: ~$240
  - Clear slow-draining sink, tub, or drain: ~$89
  - Seal gaps around supply/drain line wall penetrations and countertops: ~$164
  - Water heater maintenance / ancillary repairs: ~$434
  - Secure loose sink faucet at base: ~$136
  - Install dishwasher drain high loop or air gap: ~$44
  - Repair or replace kitchen faucet / sprayer: ~$242
  - Repair shower diverter: ~$347
  - Scrape and paint rusted gas lines to prevent corrosion: ~$173
  - Locate/identify main water shut-off valve or meter: ~$890
  - Bond/ground CSST gas piping: ~$280
  - Repair reversed hot/cold water connections: ~$178
  - Replace washing machine supply hoses with stainless braided hoses: ~$109
  - Repair toilet functional issues (flush, leak at base): ~$143
  - Install missing sediment trap on gas supply line: ~$534
  - Evaluate/repair rusted or deteriorating drain lines: ~$178

WINDOWS/DOORS:
  - Replace/install missing or damaged window screens: ~$160
  - Adjust or trim sticking/binding door for proper operation: ~$194
  - Repair/adjust door latch, strike plate, or catch plate: ~$186
  - Repair/replace failed insulated glass seal (foggy/condensation between panes): ~$722
  - Free/repair inoperable or stuck windows (egress concern): ~$208
  - Repair/replace window latches, tilt latches, or crank hardware: ~$69
  - Seal gaps around window frames (air, water, insect infiltration): ~$466
  - Repair/replace window balances or counterbalance mechanism: ~$1,505
  - Repair/replace wood rot on exterior door casings or trim: ~$949
  - Repair/replace cracked or broken window glass: ~$648
  - Secure or replace loose/missing door knobs and locksets: ~$222
  - Repair/replace damaged garage door panels, trim, or opener: ~$239
  - Repair/replace storm door closer or screen door: ~$277
  - Caulk/seal around exterior door frames to prevent moisture intrusion: ~$315
  - Refinish, paint, or replace missing hardware on exterior doors: ~$222
  - Repair/replace damaged door frame or jamb: ~$4,512
  - Replace/repair door sweep: ~$261
  - Repair/replace door threshold or toeboard: ~$1,141
  - Install fire-rated door between garage and living space: ~$2,770

INTERIOR:
  - Repair cracks in walls and ceilings (settling, minor, moderate): ~$1,042
  - Patch/repair drywall seam tape, nail pops, and holes: ~$750
  - Repair or replace damaged flooring (hardwood, laminate, vinyl, general): ~$155
  - Paint and finish walls and ceilings (touch-up, water stains, patches): $160-$216
  - Seal/caulk countertop gaps, backsplash, and bathroom fixtures: ~$128
  - Replace or repair cracked/damaged floor tiles: $97-$186
  - Repair or replace damaged cabinets and cabinet components: ~$272
  - Adjust, secure, or replace cabinet doors, hinges, and hardware: ~$285
  - Install or secure handrail at interior stairway: ~$336
  - Repair or replace water-damaged walls, ceilings, and flooring: ~$1,447
  - Repair or replace interior trim, molding, and baseboards: ~$616
  - Repair/seal tile grout and caulk in shower/tub surround: ~$92
  - Repair or replace attic pull-down stairs: ~$227
  - Evaluate and repair unlevel, sagging, or soft subfloor/floor framing: ~$2,733
  - Secure loose railings, fixtures, and bars to wall: ~$357
  - Repair or replace damaged/missing countertop: ~$183
  - Repair fireplace/flue components and clean firebox: ~$144
  - Evaluate and remediate mold/fungal growth on interior surfaces: ~$2,342
  - Install or replace doorstop and repair wall damage from doorknob: ~$53
  - Secure subfloor to eliminate squeaking floors: $97-$186
  - Repair or replace closet shelving: $122-$211
  - Secure loose transition strip: ~$30

HVAC:
  - Repair/replace refrigerant lineset insulation: ~$203
  - Replace dirty air filter(s): ~$138
  - Repair/replace condensate drain line (routing, sealing, reconnecting): ~$192
  - Clean and service HVAC unit: ~$267
  - Repair/replace or clean ductwork: ~$405
  - Evaluate and repair non-functioning or underperforming HVAC system: ~$343
  - Repair/replace ductwork insulation: ~$446
  - Level AC condensing unit pad: ~$198
  - Repair/replace or clean supply and return vent covers/registers: ~$267
  - Repair/seal flue vent connections and pipes: ~$334
  - Reroute bathroom exhaust fan to exterior: ~$375
  - Repair or install missing ductwork and duct connections: ~$267
  - Insulate condensate drain pipe: ~$654
  - Clean/inspect chimney flue and fireplace: ~$325
  - Install sediment trap at furnace gas line: ~$534

ROOFING:
  - Clean gutters and downspouts: ~$355
  - Repair or replace damaged/missing shingles: ~$320
  - Seal exposed nail heads on roof surface: ~$283
  - Install/extend downspout extensions or splash blocks: ~$254
  - Repair or replace vent pipe boots/flashing boots: ~$315
  - Repair or replace gutters and downspouts: ~$465
  - Evaluate and repair roof leaks or water intrusion: ~$178
  - Repair or seal chimney crown/mortar/masonry: ~$520
  - Install kick-out flashing at wall-to-roof junctions: ~$282
  - Repair or replace chimney flashing: ~$201
  - Repair loose or lifted flashing (non-chimney): ~$409
  - Install chimney cap or rain cap: ~$1,184
  - Repair or replace damaged roof decking/sheathing: $586-$792
  - Install or repair drip edge or valley flashing: ~$409

APPLIANCES:
  - Install dishwasher drain high loop or air gap: ~$44
  - Repair/replace garbage disposal: $169-$229
  - Repair or replace dryer vent duct and routing: ~$406
  - Repair/replace garage door opener or sensors: ~$301
  - Repair/replace damaged laundry appliance controls or components: ~$109
  - Clean lint buildup from dryer vent system: ~$267

OTHER:
  - Repair/service gas fireplace logs or pilot light: ~$89
  - Repair firebox masonry, mortar, or refractory panels: $335-$453
  - Clean dryer vent duct / replace with rigid metal duct: ~$405
  - Miscellaneous safety and property items: ~$233
  - Repair chimney crown, wash, or exterior masonry: ~$562
  - Pool fence/gate safety repair (self-closing, self-latching): ~$122
  - Install/replace smoke detectors or CO detectors: ~$293
  - Crawlspace cleaning, vapor barrier, or vent repair: ~$1,841
  - Install/relocate gas fireplace shutoff valve or damper clamp: ~$96
  - Garage door safety sensor, auto-reverse, or opener repair: ~$301
  - Repair/replace chimney cap, spark arrestor, or rain cap: ~$1,184
  - Garage door opener minor repairs (light cover, chain, wall switch): ~$44
  - Repair/replace attic pull-down stairs: ~$200
  - Surface fungi/mold treatment and moisture control: ~$6,218
  - Repair/replace bathroom exhaust fan: ~$398

GARAGE:
  - Seal/repair cracks in garage concrete slab: ~$152
  - Repair/replace garage door weather stripping: ~$261
  - Repair/replace garage door opener (unit or components): ~$239
  - Seal gaps/drywall for fire separation: ~$3,050
  - Upgrade/repair garage firewall to fire separation standards: ~$2,848
  - Patch holes in garage ceiling/walls for fire rating: ~$534
  - Repair garage door mounting/header system: ~$89
  - Repair/replace damaged garage wall paneling: ~$1,942
  - Investigate water staining on garage ceiling: ~$178

ELECTRICAL — Sub: Redland Electric (864) 909-4441, apply 45% markup:
  - GFCI outlet install: $178-217
  - Smoke/CO detector replacement: $273-362
  - Light fixture swap: $200-250
  - Ceiling fan replacement: $200-300
  - Panel repair (minor): $175-350
  - Ground rod/gas bond upgrade: $500-652
  - Recessed lighting (per fixture): $175-330
  - Breaker/wiring repair: quote required

CRAWLSPACE/FOUNDATION — Sub: Crawlspace Medic (864) 478-8598, apply 45% markup:
  - Crawlspace clean-out: $300-350
  - Vapor barrier install (10 mil): $984-2,227 depending on sqft
  - Dehumidifier install (Santa Fe Compact70): $1,895-2,095
  - Seal/secure foundation vents: $105-483
  - Crawlspace electrical outlet for dehumidifier: $550-700
  - Fungal/mold treatment: $750-2,112 depending on severity
  - R-19 floor insulation (remove + install): $3.15-7.86/sqft
  - Sump pump install (1/3 HP): $895
  - Pier/girder repair: $315-975 per location
  - Well vent install/repair: $210-315 each
  - Sill plate replacement: quote required (highly variable)

INSULATION — mostly sub work:
  - Attic insulation (add/replace): quote per sqft
  - Bathroom exhaust fan (install/repair): $178-290
  - Dryer vent cap replacement: $218-260
  - Pipe insulation: $89-178


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
        # Read body first
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        # Respond to Wufoo immediately before doing any work
        # Wufoo has a short timeout and will close the connection if we wait
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except BrokenPipeError:
            pass

        # Process in background thread so Wufoo connection is already closed
        import threading
        t = threading.Thread(target=self._process_submission, args=(body,))
        t.daemon = True
        t.start()

    def _process_submission(self, body):
        client_name = ""
        client_phone = ""
        client_email = ""
        address = ""
        notes = ""
        files_data = []

        try:
            pass  # placeholder to maintain try block
            data = urllib.parse.parse_qs(body)
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
                return

            # Build Claude content and generate estimate
            content = build_claude_content(files_data, client_name, client_phone, client_email, address, notes)
            estimate = call_claude(content)
            print(f"Estimate total: {estimate.get('total', 0)}")

            html = build_estimate_email_html(estimate)
            subject = f"Closing Repairs Estimate - {address} - {fmt(estimate.get('total', 0))}"
            send_email(subject, html)
            print(f"Estimate email sent to {NOTIFY_EMAIL}")

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

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting on port {port}")
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
