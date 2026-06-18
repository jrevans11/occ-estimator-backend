import os
import json
import base64
import re
import io
import urllib.request
import urllib.parse
import http.client
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Environment ───────────────────────────────────────────────────────────────
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
WUFOO_API_KEY    = os.environ.get("WUFOO_API_KEY", "")
JOBTREAD_KEY     = os.environ.get("JOBTREAD_API_KEY", "")
JOBTREAD_ORG     = os.environ.get("JOBTREAD_ORG_ID", "22P9ppHePJKP")
RENDER_API_KEY   = os.environ.get("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "")

# ── System prompt ─────────────────────────────────────────────────────────────
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

CUSTOMER-FACING OUTPUT RULES — CRITICAL:
- Do NOT include subcontractor names, company names, or phone numbers anywhere in the estimate output.
- Do NOT include base costs, markup percentages, or any internal pricing details.
- Generic trade references are acceptable (e.g. "work to be performed by a licensed electrician").
- Line items should contain only: a clean scope description and relevant field notes.

SCOPE RULES:
1. Only include items in a general contractor scope.
2. Do NOT include: septic/sewer, termite, cosmetic items like carpet stains or paint.
3. Group related items when it makes sense.
4. Use inspection report section numbers as line item title prefix when available.
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

# ── Dynamic pricing ───────────────────────────────────────────────────────────

def get_system_prompt():
    """Build the full system prompt, injecting live pricing reference if available."""
    pricing = os.environ.get("PRICING_REFERENCE", "")
    if not pricing:
        return SYSTEM_PROMPT
    marker = "REPAIR PRICING REFERENCE"
    end_marker = "CUSTOMER-FACING OUTPUT RULES"
    if marker in SYSTEM_PROMPT and end_marker in SYSTEM_PROMPT:
        base = SYSTEM_PROMPT[:SYSTEM_PROMPT.index(marker)]
        tail = SYSTEM_PROMPT[SYSTEM_PROMPT.index(end_marker):]
        return base + pricing + "\n\n" + tail
    return SYSTEM_PROMPT




# ── PDF helpers ───────────────────────────────────────────────────────────────

def extract_pdf_text(pdf_bytes):
    """Extract all text from a PDF."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((i, text))
        return pages  # list of (page_index, text)
    except Exception as e:
        print(f"  PDF text extraction failed: {e}")
        return []


def extract_item_numbers(text):
    """Extract inspection item numbers like 1.1, 2.3.4, Item 5, #12 etc."""
    patterns = [
        r'\b\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\b',  # 1.1, 2.3, 3.4.1
        r'(?:Item|#)\s*(\d+)',                     # Item 5, #12
        r'\b([A-Z]\d+)\b',                         # A1, B2
    ]
    numbers = set()
    for pattern in patterns:
        numbers.update(re.findall(pattern, text))
    return numbers


def smart_extract_inspection_content(addendum_text, inspection_pages):
    """
    Use item numbers from the addendum to pull only relevant pages
    from the inspection report. Falls back to full text if no item
    numbers are found.
    Returns a string of targeted inspection content.
    """
    item_numbers = extract_item_numbers(addendum_text)
    print(f"  Found item numbers in addendum: {item_numbers}")

    if not item_numbers:
        # Fallback: return all inspection text (truncated)
        print("  No item numbers found — using full inspection text")
        all_text = "\n".join(text for _, text in inspection_pages)
        return all_text[:40000]

    # Match pages that contain any of the item numbers
    matched_pages = []
    for page_idx, page_text in inspection_pages:
        for num in item_numbers:
            if re.search(r'\b' + re.escape(str(num)) + r'\b', page_text):
                matched_pages.append((page_idx, page_text))
                break

    if not matched_pages:
        print("  Item numbers not matched in report — using full text fallback")
        all_text = "\n".join(text for _, text in inspection_pages)
        return all_text[:40000]

    print(f"  Matched {len(matched_pages)} of {len(inspection_pages)} inspection pages")
    combined = "\n\n".join(f"[Page {i+1}]\n{text}" for i, text in matched_pages)
    return combined[:40000]


# ── File download ─────────────────────────────────────────────────────────────

def download_file(url):
    """Download a file from Wufoo with auth, following redirects."""
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


# ── Claude ────────────────────────────────────────────────────────────────────

def call_claude(addendum_text, inspection_content, client_name, client_phone, client_email, address, notes):
    """Build Claude content and call API. Returns parsed estimate dict."""
    content = []

    intro = f"""Generate a closing repairs estimate for Owners Choice Construction.

Client name: {client_name}
Client phone: {client_phone}
Client email: {client_email}
Property address: {address}
{f"Realtor notes: {notes}" if notes else ""}

Process the repair addendum first to identify all requested items, then cross-reference with the inspection report content to write accurate scope descriptions and calibrate pricing based on described severity.
"""
    content.append({"type": "text", "text": intro})

    if addendum_text:
        content.append({"type": "text", "text": f"\n=== REPAIR ADDENDUM ===\n{addendum_text[:20000]}"})

    if inspection_content:
        content.append({"type": "text", "text": f"\n=== INSPECTION REPORT (targeted sections) ===\n{inspection_content}"})

    content.append({"type": "text", "text": "\nRespond with ONLY the raw JSON object. No markdown, no explanation."})

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 4000,
        "system": get_system_prompt(),
        "messages": [{"role": "user", "content": content}]
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


# ── JobTread ──────────────────────────────────────────────────────────────────

def jobtread_query(query):
    """Execute a JobTread Pave API query."""
    payload = json.dumps({
        "query": {
            "$": {"grantKey": JOBTREAD_KEY},
            **query
        }
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.jobtread.com/pave",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def create_jobtread_job(estimate, notes_text, file_urls):
    """
    Full JobTread job creation flow:
    1. Create account (customer)
    2. Create contact
    3. Create location
    4. Create job
    5. Create cost items (estimate line items)
    6. Attach files from Wufoo URLs
    """
    client_name  = estimate.get("client_name", "Unknown")
    client_email = estimate.get("client_email", "")
    client_phone = estimate.get("client_phone", "")
    address      = estimate.get("property_address", "")

    # 1. Create account
    print("  Creating JobTread account...")
    resp = jobtread_query({
        "createAccount": {
            "$": {
                "organizationId": JOBTREAD_ORG,
                "type": "customer",
                "name": client_name,
                "customFieldValues": {
                    "Type": "Closing Repair",
                    "Lead Source": "Realtor"
                }
            },
            "createdAccount": {"id": {}}
        }
    })
    account_id = resp["createAccount"]["createdAccount"]["id"]
    print(f"  Account created: {account_id}")

    # 2. Create contact
    print("  Creating contact...")
    contact_fields = {
        "accountId": account_id,
        "name": client_name,
        "customFieldValues": {}
    }
    if client_email:
        contact_fields["customFieldValues"]["Email"] = client_email
    if client_phone:
        contact_fields["customFieldValues"]["Phone"] = client_phone

    jobtread_query({
        "createContact": {
            "$": contact_fields,
            "createdContact": {"id": {}}
        }
    })

    # 3. Create location
    print("  Creating location...")
    resp = jobtread_query({
        "createLocation": {
            "$": {
                "accountId": account_id,
                "address": address
            },
            "createdLocation": {"id": {}}
        }
    })
    location_id = resp["createLocation"]["createdLocation"]["id"]
    print(f"  Location created: {location_id}")

    # 4. Create job
    print("  Creating job...")
    job_name = f"Closing Repairs — {address}"
    resp = jobtread_query({
        "createJob": {
            "$": {
                "locationId": location_id,
                "name": job_name,
                "priceType": "fixed",
                "description": notes_text or ""
            },
            "createdJob": {"id": {}}
        }
    })
    job_id = resp["createJob"]["createdJob"]["id"]
    print(f"  Job created: {job_id}")

    # 5. Create cost items from estimate line items
    print("  Adding cost items...")
    for item in estimate.get("line_items", []):
        title = item.get("title", "")
        price = float(item.get("price", 0))
        description = item.get("description", "") or ""
        notes = item.get("notes", "") or ""

        # Combine description and notes into a single name string
        full_name = title
        if description or notes:
            detail = []
            if description:
                detail.append(description.replace("\n", " ").strip())
            if notes:
                detail.append(f"NOTE: {notes}")
            full_name = f"{title} — {' | '.join(detail)}"[:500]

        jobtread_query({
            "createCostItem": {
                "$": {
                    "jobId": job_id,
                    "name": full_name,
                    "quantity": 1,
                    "unitCost": price,
                    "unitPrice": price
                },
                "createdCostItem": {"id": {}}
            }
        })

    print(f"  {len(estimate.get('line_items', []))} cost items added")

    # 6. Attach files from Wufoo using URL-based upload
    for label, url in file_urls:
        if not url:
            continue
        try:
            print(f"  Attaching file: {label}")
            # Create upload request using the public Wufoo URL
            resp = jobtread_query({
                "createUploadRequest": {
                    "$": {
                        "organizationId": JOBTREAD_ORG,
                        "url": url
                    },
                    "createdUploadRequest": {"id": {}}
                }
            })
            upload_id = resp["createUploadRequest"]["createdUploadRequest"]["id"]

            # Attach to job
            jobtread_query({
                "createFile": {
                    "$": {
                        "targetType": "job",
                        "targetId": job_id,
                        "name": label,
                        "uploadRequestId": upload_id
                    },
                    "createdFile": {"id": {}}
                }
            })
            print(f"  File attached: {label}")
        except Exception as e:
            print(f"  File attach failed for {label}: {e}")

    return job_id


def format_date(raw):
    """Convert Wufoo date format YYYYMMDD to MM/DD/YYYY."""
    if raw and len(raw) == 8:
        return f"{raw[4:6]}/{raw[6:8]}/{raw[0:4]}"
    return raw


def build_notes(due_diligence, closing_date, site_visit, non_neg_notes, extra_notes, inquiring_party):
    """Assemble the JobTread job notes field from all relevant Wufoo fields."""
    parts = []
    if inquiring_party:
        parts.append(f"Inquiring Party: {inquiring_party}")
    if site_visit:
        parts.append(f"Site Visit Requested: {site_visit}")
    if due_diligence:
        parts.append(f"Due Diligence Deadline: {format_date(due_diligence)}")
    if closing_date:
        parts.append(f"Anticipated Closing Date: {format_date(closing_date)}")
    if non_neg_notes and non_neg_notes.strip().lower() not in ["no", "n/a", "none", "nope!", ""]:
        parts.append(f"Non-Negotiable Repairs: {non_neg_notes.strip()}")
    if extra_notes and extra_notes.strip():
        parts.append(f"Additional Notes: {extra_notes.strip()}")
    return "\n".join(parts)


# ── HTTP handler ──────────────────────────────────────────────────────────────

# ── Pricing refresh ───────────────────────────────────────────────────────────

def build_pricing_reference():
    """
    Pull all Closing Repairs documents from JobTread in two passes:
    Pass 1 — collect all doc IDs (IDs only, small payload)
    Pass 2 — fetch cost groups for each doc individually
    Aggregate into a formatted pricing reference string.
    """
    from collections import defaultdict

    # ── Pass 1: collect all document IDs ─────────────────────────────────────
    doc_ids = []
    page = None
    while True:
        query = {
            "organization": {
                "$": {"id": JOBTREAD_ORG},
                "documents": {
                    "$": {
                        "size": 50,
                        "where": ["name", "like", "%Closing Repairs%"],
                        **( {"page": page} if page else {} )
                    },
                    "nextPage": {},
                    "nodes": {"id": {}}
                }
            }
        }
        resp = jobtread_query(query)
        if not resp:
            print(f"  WARNING: Empty response from JobTread")
            break
        if "error" in resp:
            print(f"  ERROR from JobTread: {resp}")
            break
        docs = resp.get("organization", {}).get("documents", {})
        if not docs:
            print(f"  WARNING: No documents in response: {list(resp.keys())}")
            break
        for node in docs.get("nodes", []):
            doc_ids.append(node["id"])
        page = docs.get("nextPage")
        print(f"  Collected {len(doc_ids)} doc IDs so far...")
        if not page:
            break

    print(f"  Total documents found: {len(doc_ids)}")

    # ── Pass 2: fetch cost groups per document ────────────────────────────────
    all_groups = defaultdict(list)

    for i, doc_id in enumerate(doc_ids):
        try:
            query = {
                "document": {
                    "$": {"id": doc_id},
                    "costGroups": {
                        "$": {"size": 30},
                        "nodes": {
                            "name": {},
                            "descendentCostItems": {"sum": {"$": "unitPrice"}}
                        }
                    }
                }
            }
            resp = jobtread_query(query)
            groups = resp.get("document", {}).get("costGroups", {}).get("nodes", [])
            for group in groups:
                name = (group.get("name") or "").strip()
                total = group.get("descendentCostItems", {}).get("sum")
                if name and total and total > 0:
                    # Normalize: strip leading inspection item numbers
                    clean = re.sub(r'^[\d\.\,\s&/]+[-\u2013:]?\s*', '', name).strip()
                    clean = re.sub(r'^[A-Z]\d+[-\u2013:]?\s*', '', clean).strip()
                    if len(clean) > 5:
                        all_groups[clean].append(round(total, 2))
        except Exception as e:
            print(f"  Skipping doc {doc_id}: {e}")
            continue

        if (i + 1) % 10 == 0:
            print(f"  Processed {i+1}/{len(doc_ids)} documents...")

    print(f"  Unique repair types found: {len(all_groups)}")

    # ── Build formatted output ────────────────────────────────────────────────
    lines = [
        f"REPAIR PRICING REFERENCE — Built from {len(doc_ids)} real OCC JobTread closing repair documents",
        "Ranges reflect actual billed prices. Use scope/photos to calibrate within range.",
        "Apply $89/hr labor + 65% material markup for in-house work.",
        ""
    ]

    def category_key(name):
        n = name.lower()
        if any(x in n for x in ["crawl", "vapor", "dehumid", "sump", "shoring", "girder", "joist", "sill", "foundation drain"]):
            return "1_CRAWLSPACE"
        if any(x in n for x in ["electrical", "gfci", "breaker", "panel", "junction", "outlet", "smoke", "co detector", "conduit", "disconnect", "rewire"]):
            return "2_ELECTRICAL"
        if any(x in n for x in ["hvac", "condensate", "refrigerant", "duct", "furnace", "air handler", "vent fan"]):
            return "3_HVAC"
        if any(x in n for x in ["plumb", "toilet", "drain", "spigot", "water heater", "expansion tank", "tpr", "gas bond", "gas line", "shower faucet", "bathtub", "sink drain", "supply line", "exhaust fan", "dryer vent", "copper pipe", "cast iron"]):
            return "4_PLUMBING"
        if any(x in n for x in ["roof", "shingle", "flashing", "gutter", "chimney", "soffit", "fascia", "siding", "deck", "porch", "door", "window", "wood rot", "railing", "handrail", "mortar", "brick", "weatherstrip", "downspout", "exterior"]):
            return "5_EXTERIOR"
        return "6_INTERIOR"

    sorted_groups = sorted(all_groups.items(), key=lambda x: (category_key(x[0]), x[0].lower()))
    current_cat = None
    cat_labels = {
        "1_CRAWLSPACE": "CRAWLSPACE/STRUCTURAL:",
        "2_ELECTRICAL": "ELECTRICAL:",
        "3_HVAC": "HVAC:",
        "4_PLUMBING": "PLUMBING:",
        "5_EXTERIOR": "EXTERIOR:",
        "6_INTERIOR": "INTERIOR:",
    }

    for name, prices in sorted_groups:
        cat = category_key(name)
        if cat != current_cat:
            if current_cat is not None:
                lines.append("")
            lines.append(cat_labels.get(cat, "OTHER:"))
            current_cat = cat
        n = len(prices)
        if n == 1:
            lines.append(f"  - {name}: ~${prices[0]:,.0f} ({n} job)")
        else:
            lo, hi, avg = min(prices), max(prices), sum(prices) / n
            if lo == hi:
                lines.append(f"  - {name}: ~${lo:,.0f} ({n} jobs, very consistent)")
            else:
                lines.append(f"  - {name}: ${lo:,.0f}\u2013${hi:,.0f} (avg ${avg:,.0f}, {n} jobs)")

    return "\n".join(lines)


def update_render_env(key, value):
    """Update an environment variable on Render via the API."""
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        print("  RENDER_API_KEY or RENDER_SERVICE_ID not set — skipping Render update")
        return

    # Get current env vars first
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars",
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        current = json.loads(r.read().decode("utf-8"))

    # Build updated list — replace existing key or add new
    env_vars = [{"key": e["envVar"]["key"], "value": e["envVar"]["value"]}
                for e in current if e["envVar"]["key"] != key]
    env_vars.append({"key": key, "value": value})

    # PUT updated list
    payload = json.dumps(env_vars).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars",
        data=payload,
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        method="PUT"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"  Render env update response: {r.status}")


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/refresh-pricing":
            self._handle_refresh()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OCC Estimator Backend is running!")

    def _handle_refresh(self):
        """Pull all closing repair cost groups from JobTread and update pricing."""
        try:
            print("Starting pricing refresh...")
            pricing_text = build_pricing_reference()
            update_render_env("PRICING_REFERENCE", pricing_text)
            # Also update in-process so current instance uses it immediately
            os.environ["PRICING_REFERENCE"] = pricing_text
            msg = f"Pricing reference updated successfully. {pricing_text.count(chr(10))} lines generated."
            print(msg)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(msg.encode("utf-8"))
        except Exception as e:
            import traceback
            err = f"Refresh failed: {e}\n{traceback.format_exc()}"
            print(err)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(err.encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        # Respond to Wufoo immediately — it has a short timeout
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except BrokenPipeError:
            pass

        # Process in background thread
        t = threading.Thread(target=self._process, args=(body,))
        t.daemon = True
        t.start()

    def _process(self, body):
        try:
            data = urllib.parse.parse_qs(body)

            def get(key):
                return data.get(key, [""])[0]

            # Parse all Wufoo fields
            first          = get("Field1")
            last           = get("Field2")
            client_name    = f"{first} {last}".strip()
            client_phone   = get("Field3")
            client_email   = get("Field4")
            inquiring_party = get("Field122")
            street         = get("Field5")
            city           = get("Field7")
            state          = get("Field8")
            zip_code       = get("Field9")
            address        = f"{street}, {city}, {state} {zip_code}".strip(", ")
            inspection_url = get("Field12-url")
            addendum_url   = get("Field13-url")
            extra_file_url = get("Field426-url")
            extra_file_name = get("Field426") or "Additional File"
            non_neg_notes  = get("Field121")
            site_visit_yes = get("Field324")
            site_visit_no  = get("Field325")
            closing_date   = get("Field428")
            due_diligence  = get("Field532")
            extra_notes    = get("Field424")

            site_visit = "Yes" if site_visit_yes else ("No" if site_visit_no else "")

            print(f"New submission: {client_name} — {address}")

            # Build notes for JobTread
            notes_text = build_notes(
                due_diligence, closing_date, site_visit,
                non_neg_notes, extra_notes, inquiring_party
            )

            # Download PDFs
            addendum_text     = ""
            inspection_pages  = []

            if addendum_url:
                print("  Downloading repair addendum...")
                try:
                    addendum_bytes = download_file(addendum_url)
                    pages = extract_pdf_text(addendum_bytes)
                    addendum_text = "\n".join(text for _, text in pages)
                    print(f"  Addendum: {len(addendum_text)} chars extracted")
                except Exception as e:
                    print(f"  Addendum download failed: {e}")

            if inspection_url:
                print("  Downloading inspection report...")
                try:
                    inspection_bytes = download_file(inspection_url)
                    inspection_pages = extract_pdf_text(inspection_bytes)
                    print(f"  Inspection: {len(inspection_pages)} pages extracted")
                except Exception as e:
                    print(f"  Inspection download failed: {e}")

            if not addendum_text and not inspection_pages:
                print("  No usable content — aborting")
                return

            # Smart extraction: use addendum item numbers to target inspection pages
            inspection_content = smart_extract_inspection_content(addendum_text, inspection_pages)

            # Call Claude
            print("  Calling Claude...")
            estimate = call_claude(
                addendum_text, inspection_content,
                client_name, client_phone, client_email, address, notes_text
            )
            total = estimate.get("total", 0)
            print(f"  Estimate total: ${total:,.2f}")

            # Build file list for JobTread attachment
            # Use Wufoo file URLs directly — JobTread fetches them
            file_urls = [
                ("Inspection Report", inspection_url),
                ("Repair Addendum",   addendum_url),
            ]
            if extra_file_url:
                file_urls.append((extra_file_name or "Additional File", extra_file_url))

            # Create job in JobTread
            print("  Creating JobTread job...")
            job_id = create_jobtread_job(estimate, notes_text, file_urls)
            print(f"  Done. JobTread job ID: {job_id}")

        except Exception as e:
            print(f"Error processing submission: {e}")
            import traceback
            traceback.print_exc()

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting OCC Estimator on port {port}")
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
