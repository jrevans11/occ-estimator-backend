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

LABOR CLASSIFICATION (who performs the work — drives which markup applies):
- SUBCONTRACTOR work (apply 45% markup): all electrical work; MAJOR HVAC repairs (system replacement, compressor, refrigerant, major ductwork); MAJOR plumbing (re-pipe, sewer/drain line, water heater replacement, slab leaks); crawlspace moisture remediation and clean-out (vapor barrier, dehumidifier, fungal/mold treatment, sump pump).
- IN-HOUSE work (apply $89/hr labor + 65% material markup): everything else — drywall, paint, carpentry, trim, doors, windows, flooring, general repairs, minor plumbing fixture work, minor HVAC service, exterior/roofing repairs, etc.
- Minor/routine HVAC and plumbing (filter swaps, fixture seals, leak repairs, condensate lines, toilet/faucet work) are IN-HOUSE, not sub.

NEVER ESTIMATE — OUT OF SCOPE (OCC does not offer these):
- Radon remediation/mitigation
- Landscaping, grading, or regrading work
If the requested work includes any out-of-scope item, exclude it from the estimate and list it under "skipped_items" with the reason "not offered by OCC", but still estimate everything else that is in scope.

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
4. Use inspection report section numbers as cost group title prefix when available.
5. Do NOT include a disclaimer — it is already built into the estimate template.

BEST-EFFORT GATING (for home repair / remodel / GVL / general inquiries without a formal inspection report):
- Only produce cost_groups when the description and/or photos give you enough concrete, itemizable detail to write a defensible scope and price.
- If the information is too vague to estimate responsibly (e.g. "need some work done", a remodel described only at a high level, no specifics on quantity/condition/location), return an EMPTY "cost_groups" array, set "needs_consult" to true, and put a short reason in "consult_reason". Do NOT guess or fabricate scope just to produce a number.
- A partial estimate is fine: estimate the items you CAN scope, and note the rest for the consult.

DESCRIPTION FORMATTING RULES:
- Write each bullet point as a complete, professional sentence. Not fragments.
- Be specific about what is being done — include the material, location, and action.
- Good example: "- Remove and replace deteriorated wood casing at the front entry door, including treatment of any affected framing behind."
- Bad example: "- Fix wood rot"
- Each bullet should stand alone and read clearly to a homeowner.
- Aim for 2-4 bullets per group depending on scope complexity.
- Add a NOTE: line (not a bullet) at the end when there are important caveats or conditions.
- For any repair that involves painting or finishing to match existing surfaces, always include this note at the end of the description:
  "NOTE: Client is encouraged to provide the existing paint color and sheen for best results. Paint matching is not guaranteed due to age, fading, and manufacturer variation."

LABOR TAGGING: For each cost group, set "labor" to "sub" if the work is performed by a subcontractor (all electrical; major HVAC; major plumbing; crawlspace moisture remediation/clean-out) or "in_house" for everything else.

OUTPUT: Respond with ONLY valid JSON, no markdown:
{
  "property_address": "address",
  "client_name": "name",
  "client_phone": "phone",
  "client_email": "email",
  "cost_groups": [
    {
      "title": "3.2 - Wood Rot at Front Door",
      "description": "- Remove and replace deteriorated wood casing at the front entry door, including treatment of any affected framing behind.\n- Apply primer and finish coat to all repaired surfaces.\n\nNOTE: Client is encouraged to provide the existing paint color and sheen for best results. Paint matching is not guaranteed due to age, fading, and manufacturer variation.",
      "price": 450.00,
      "labor": "in_house",
      "notes": null
    }
  ],
  "total": 0.00,
  "skipped_items": ["item - reason"],
  "needs_consult": false,
  "consult_reason": ""
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

def _sanitize_text(text):
    """
    Strip characters that corrupt a JSON payload sent to the Anthropic API:
    - Null bytes and ASCII control chars (keep tab, newline, carriage return)
    - Lone surrogates and other bad codepoints pypdf sometimes emits
    """
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
    return text


def extract_pdf_text(pdf_bytes):
    """Extract all text from a PDF, sanitizing for safe JSON encoding."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for i, page in enumerate(reader.pages):
            text = _sanitize_text(page.extract_text() or "")
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


# JobTread cost item constants
COST_CODE_UNCATEGORIZED = "22P9ppJUAHXn"
COST_TYPE_SUB           = "22P9ppJUAHYQ"  # Subcontractor
COST_TYPE_OTHER         = "22P9ppJUAHYR"  # Other (in-house)

# Fallback keywords if the model doesn't tag a group's labor type.
# Per OCC rules: sub = all electrical, MAJOR hvac/plumbing, crawlspace moisture work.
_SUB_FALLBACK_KEYWORDS = [
    "electric", "gfci", "breaker", "panel", "rewire", "outlet",
    "crawlspace", "crawl space", "vapor barrier", "dehumidif", "sump",
    "fungal", "mold", "moisture remediation", "encapsulat",
    "re-pipe", "repipe", "sewer line", "main drain line", "slab leak",
    "water heater replace", "hvac replace", "system replace", "compressor",
    "condenser replace", "ductwork replace",
]


def _contact_cfv(contact):
    """Return a {field_name: value} dict from a contact node's customFieldValues."""
    out = {}
    for n in (contact.get("customFieldValues", {}) or {}).get("nodes", []):
        name = (n.get("customField", {}) or {}).get("name", "")
        if name:
            out[name] = n.get("value", "")
    return out


def find_account_by_name(name):
    """Lightweight exact-name lookup. Returns the account id (str) or None.

    Kept intentionally small (id + name only) — requesting nested contacts/custom
    fields here trips JobTread's request-size limit and would silently fail dedup.
    """
    if not name or not name.strip():
        return None
    try:
        resp = jobtread_query({
            "organization": {
                "$": {"id": JOBTREAD_ORG},
                "accounts": {
                    "$": {"size": 20, "where": {"and": [["type", "customer"], ["name", "like", name.strip()]]}},
                    "nodes": {"id": {}, "name": {}}
                }
            }
        })
        nodes = resp.get("organization", {}).get("accounts", {}).get("nodes", [])
    except Exception as e:
        print(f"  Account lookup failed (treating as new): {e}")
        return None
    target = name.strip().lower()
    for n in nodes:
        if (n.get("name") or "").strip().lower() == target:
            return n["id"]
    return None


def get_account_contacts(account_id):
    """Fetch contacts (with their custom fields) for a single account. Small, focused query."""
    try:
        resp = jobtread_query({
            "account": {
                "$": {"id": account_id},
                "contacts": {"$": {"size": 25}, "nodes": {
                    "id": {}, "name": {},
                    "customFieldValues": {"$": {"size": 10}, "nodes": {
                        "customField": {"name": {}}, "value": {}
                    }}
                }}
            }
        })
        return resp.get("account", {}).get("contacts", {}).get("nodes", [])
    except Exception as e:
        print(f"  Contact fetch failed: {e}")
        return []


def find_matching_contact(contacts, email, contact_name):
    """Given a list of contact nodes, find one matching by email (preferred) or name."""
    email = (email or "").strip().lower()
    cname = (contact_name or "").strip().lower()
    for c in contacts:
        c_email = (_contact_cfv(c).get("Email") or "").strip().lower()
        if email and c_email and email == c_email:
            return c
    if not email:
        for c in contacts:
            if cname and (c.get("name") or "").strip().lower() == cname:
                return c
    return None


def set_primary_contact(account_id, contact_id):
    """Set the account's primary contact."""
    try:
        jobtread_query({
            "updateAccount": {
                "$": {"id": account_id, "primaryContactId": contact_id},
                "account": {"$": {"id": account_id}, "id": {}}
            }
        })
    except Exception as e:
        print(f"  Could not set primary contact: {e}")


def create_contact_record(account_id, name, email, phone, address):
    """Create a contact under an account and return its id."""
    cfv = {"Email": email or "", "Address": address or ""}
    if phone:
        cfv["Phone"] = phone
    resp = jobtread_query({
        "createContact": {
            "$": {"accountId": account_id, "name": name or "Unknown", "customFieldValues": cfv},
            "createdContact": {"id": {}}
        }
    })
    return resp["createContact"]["createdContact"]["id"]


def upsert_account_and_contact(cfg):
    """
    Find-or-create the account, and ensure the incoming contact exists & is primary.

    cfg keys used: account_name, account_type, lead_source, referred_by (opt),
    contact_name, contact_email, contact_phone, contact_address, dedup (bool).
    Returns account_id.
    """
    account_cfv = {"Type": cfg["account_type"], "Lead Source": cfg["lead_source"]}
    if cfg.get("referred_by"):
        account_cfv["Referred By"] = cfg["referred_by"]

    account_id = find_account_by_name(cfg["account_name"]) if cfg.get("dedup") else None

    if account_id:
        print(f"  Existing account matched: {account_id} ({cfg['account_name']})")
        # Keep referral attribution current if we have one
        if cfg.get("referred_by"):
            try:
                jobtread_query({"updateAccount": {"$": {"id": account_id,
                    "customFieldValues": {"Referred By": cfg["referred_by"]}},
                    "account": {"$": {"id": account_id}, "id": {}}}})
            except Exception as e:
                print(f"  Could not update Referred By: {e}")

        contacts = get_account_contacts(account_id)
        match = find_matching_contact(contacts, cfg["contact_email"], cfg["contact_name"])
        if match:
            print(f"  Existing contact matched: {match['id']} ({match.get('name')})")
            set_primary_contact(account_id, match["id"])
        else:
            print("  No matching contact — adding new contact and setting primary")
            new_contact_id = create_contact_record(
                account_id, cfg["contact_name"], cfg["contact_email"],
                cfg["contact_phone"], cfg["contact_address"])
            set_primary_contact(account_id, new_contact_id)
        return account_id

    # No existing account — create fresh
    print("  Creating JobTread account...")
    resp = jobtread_query({
        "createAccount": {
            "$": {
                "organizationId": JOBTREAD_ORG,
                "type": "customer",
                "name": cfg["account_name"],
                "suffixIfNecessary": True,
                "customFieldValues": account_cfv
            },
            "createdAccount": {"id": {}}
        }
    })
    account_id = resp["createAccount"]["createdAccount"]["id"]
    print(f"  Account created: {account_id}")
    contact_id = create_contact_record(
        account_id, cfg["contact_name"], cfg["contact_email"],
        cfg["contact_phone"], cfg["contact_address"])
    set_primary_contact(account_id, contact_id)
    return account_id


def _find_location(account_id, address):
    """Return the id of an existing location on the account matching the address, or None."""
    target = re.sub(r"\s+", " ", (address or "").strip().lower())
    if not target:
        return None
    try:
        resp = jobtread_query({
            "account": {
                "$": {"id": account_id},
                "locations": {"$": {"size": 50}, "nodes": {"id": {}, "address": {}}}
            }
        })
        for l in resp.get("account", {}).get("locations", {}).get("nodes", []):
            if re.sub(r"\s+", " ", (l.get("address") or "").strip().lower()) == target:
                return l["id"]
    except Exception as e:
        print(f"  Location lookup failed: {e}")
    return None


def create_location_record(account_id, address):
    """Find-or-create a location. JobTread forbids duplicate addresses on one account,
    so on the dedup path we reuse the existing location instead of erroring."""
    existing = _find_location(account_id, address)
    if existing:
        print(f"  Reusing existing location: {existing}")
        return existing
    try:
        resp = jobtread_query({
            "createLocation": {
                "$": {"accountId": account_id, "address": address},
                "createdLocation": {"id": {}}
            }
        })
        return resp["createLocation"]["createdLocation"]["id"]
    except Exception as e:
        # Likely a duplicate that didn't string-match (address normalization) — re-find
        print(f"  createLocation failed ({e}); re-checking for an existing location")
        existing = _find_location(account_id, address)
        if existing:
            return existing
        raise


def create_job_record(location_id, cfg):
    """Create the job. cfg: job_type, status_field, status_value, pm, projected_budget (opt),
    job_name (opt → None for auto Job #####), notes_text."""
    job_cfv = {
        "Job Type": cfg["job_type"],
        cfg["status_field"]: cfg["status_value"],
    }
    if cfg.get("pm"):
        job_cfv["Project Manager"] = cfg["pm"]
    if cfg.get("projected_budget"):
        job_cfv["Projected Budget"] = cfg["projected_budget"]

    job_input = {
        "locationId": location_id,
        "priceType": "fixed",
        "description": cfg.get("notes_text") or "",
        "customFieldValues": job_cfv,
    }
    # Only set a name for closing repairs; new forms leave it null → JobTread auto "Job #####"
    if cfg.get("job_name"):
        job_input["name"] = cfg["job_name"][:30]

    resp = jobtread_query({
        "createJob": {"$": job_input, "createdJob": {"id": {}}}
    })
    return resp["createJob"]["createdJob"]["id"]


def add_cost_groups(job_id, estimate):
    """Create cost groups + one cost item each from the estimate dict. Returns count added."""
    if not estimate:
        return 0
    added = 0
    cost_groups = estimate.get("cost_groups", estimate.get("line_items", [])) or []
    for group in cost_groups:
        title        = (group.get("title", "") or "").strip() or "Repair Item"
        client_price = float(group.get("price", 0) or 0)
        description  = (group.get("description", "") or "").strip()
        notes        = (group.get("notes", "") or "").strip()
        labor        = (group.get("labor", "") or "").strip().lower()

        group_description = description
        if notes:
            group_description += f"\n\nNOTE: {notes}" if group_description else f"NOTE: {notes}"

        cost = round(client_price / 1.55, 2) if client_price > 0 else 0.0

        item_name = re.sub(r'^[\d\.\s]+[-\u2013]?\s*', '', title).strip() or title
        item_name = item_name[:100]

        # Cost type: prefer the model's labor tag, else keyword fallback
        if labor == "sub":
            cost_type_id = COST_TYPE_SUB
        elif labor in ("in_house", "inhouse", "in-house"):
            cost_type_id = COST_TYPE_OTHER
        else:
            blob = f"{title} {description}".lower()
            cost_type_id = COST_TYPE_SUB if any(k in blob for k in _SUB_FALLBACK_KEYWORDS) else COST_TYPE_OTHER

        try:
            resp = jobtread_query({
                "createCostGroup": {
                    "$": {"jobId": job_id, "name": title[:100], "description": group_description or None},
                    "createdCostGroup": {"id": {}}
                }
            })
            group_id = resp["createCostGroup"]["createdCostGroup"]["id"]
            jobtread_query({
                "createCostItem": {
                    "$": {
                        "costGroupId": group_id, "name": item_name, "quantity": 1,
                        "unitCost": cost, "unitPrice": client_price,
                        "costCodeId": COST_CODE_UNCATEGORIZED, "costTypeId": cost_type_id
                    },
                    "createdCostItem": {"id": {}}
                }
            })
            added += 1
        except Exception as e:
            print(f"  Skipping group '{title[:50]}': {e}")
            continue
    print(f"  {added}/{len(cost_groups)} cost groups added")
    return added


def attach_files(job_id, file_urls):
    """Attach Wufoo files to the job via URL-based upload requests."""
    for label, url in file_urls:
        if not url:
            continue
        try:
            print(f"  Attaching file: {label}")
            resp = jobtread_query({
                "createUploadRequest": {
                    "$": {"organizationId": JOBTREAD_ORG, "url": url},
                    "createdUploadRequest": {"id": {}}
                }
            })
            upload_id = resp["createUploadRequest"]["createdUploadRequest"]["id"]
            jobtread_query({
                "createFile": {
                    "$": {"targetType": "job", "targetId": job_id, "name": label,
                          "uploadRequestId": upload_id},
                    "createdFile": {"id": {}}
                }
            })
            print(f"  File attached: {label}")
        except Exception as e:
            print(f"  File attach failed for {label}: {e}")


def create_new_lead_todos(job_id, job_type="Home Repair"):
    """
    Create the standard new-lead to-do chain on a job immediately after it is created.
    Timelines differ by job type — Closing Repairs get a tighter schedule.
    Tasks are auto-assigned: Jason for repairs/remodel/prelisting, Tyler for everything else.
    """
    from datetime import date, timedelta

    assignee_id = JASON_ID if job_type in JASON_JOB_TYPES else TYLER_ID

    def offset(n):
        return (date.today() + timedelta(days=n)).isoformat()

    if job_type == "Closing Repair":
        tasks = [
            ("📞 Call customer — introduce & qualify",   0),
            ("📅 Schedule site visit",                   1),
            ("🏠 Complete site visit",                   1),
            ("📝 Build estimate",                        1),
            ("📤 Send estimate to customer",             2),
        ]
    else:
        # Home Repair, Remodel, Pre-listing, GVL — standard cadence
        tasks = [
            ("📞 Call customer — introduce & qualify",   0),
            ("📅 Schedule site visit",                   1),
            ("🏠 Complete site visit",                   3),
            ("📝 Build estimate",                        5),
            ("📤 Send estimate to customer",             6),
        ]

    for name, days in tasks:
        due = offset(days)
        try:
            jobtread_query({
                "createTask": {
                    "$": {
                        "name": name,
                        "isToDo": True,
                        "targetType": "job",
                        "targetId": job_id,
                        "startDate": due,
                        "endDate": due,
                        "assignees": [{"membershipId": assignee_id}],
                    }
                }
            })
            print(f"  To-do created: {name} (due {due}, assigned to {assignee_id})")
        except Exception as e:
            print(f"  To-do failed '{name}': {e}")


# ── Follow-up to-do chain (post-estimate) ────────────────────────────────────

JASON_ID  = "22P9ppHePJKQ"
TYLER_ID  = "22PBsSvmYBUj"
JASON_JOB_TYPES = {"Home Repair", "Closing Repair", "Remodel", "Pre-listing Repair"}

# Status field IDs by job type
HOME_REPAIR_STATUS_FIELD    = "22PFPUHGUt4g"   # Home Repairs Status
CLOSING_REPAIR_STATUS_FIELD = "22PFPSefyzSp"   # Closing Repairs Status

# Status values that mean the job is closed — stop all follow-ups
TERMINAL_STATUSES = {"Closed Won", "Closed Lost"}
LONG_TERM_STATUS  = "Long Term Follow Up"

# Names used to identify follow-up to-dos (used for deletion)
FOLLOWUP_TODO_NAMES = {
    "📧 Follow-up email #1 — check in on estimate",
    "📞 Follow-up call #1 — any questions?",
    "📧 Follow-up email #2 — still interested?",
    "🚨 Final decision call — win or move on",
    "📅 Long term follow-up — check back in",
}


def get_job_info(job_id):
    """Fetch job type, current status field values, and open to-dos."""
    try:
        resp = jobtread_query({
            "job": {
                "$": {"id": job_id},
                "customFieldValues": {
                    "$": {"size": 20},
                    "nodes": {"customField": {"id": {}, "name": {}}, "value": {}}
                },
                "tasks": {
                    "$": {"size": 50},
                    "nodes": {"id": {}, "name": {}, "isToDo": {}, "progress": {}}
                }
            }
        })
        return resp.get("job", {})
    except Exception as e:
        print(f"  get_job_info failed: {e}")
        return {}


# Maps job type to which status field it uses
JOB_TYPE_STATUS_FIELD = {
    "Home Repair":        "Home Repairs Status",
    "Remodel":            "Home Repairs Status",
    "Pre-listing Repair": "Home Repairs Status",
    "Closing Repair":     "Closing Repairs Status",
}
# Everything else uses Renovation Status
RENOVATION_STATUS_FIELD = "Renovation Status"


def get_status_field_for_job_type(job_type):
    """Return the correct status field name for a given job type."""
    return JOB_TYPE_STATUS_FIELD.get(job_type, RENOVATION_STATUS_FIELD)


def get_job_type_and_status(job_info):
    """Extract job type and current status from job info."""
    job_type = None
    status   = None
    cfvs = (job_info.get("customFieldValues") or {}).get("nodes", [])
    # First pass — get job type
    for cfv in cfvs:
        field_name = (cfv.get("customField") or {}).get("name", "")
        if field_name == "Job Type":
            job_type = cfv.get("value")
            break
    # Second pass — get the right status field for this job type
    if job_type:
        target_field = get_status_field_for_job_type(job_type)
        for cfv in cfvs:
            field_name = (cfv.get("customField") or {}).get("name", "")
            if field_name == target_field:
                status = cfv.get("value")
                break
    return job_type, status


def delete_followup_todos(job_info):
    """Delete any open follow-up to-dos on a job."""
    deleted = 0
    for task in (job_info.get("tasks") or {}).get("nodes", []):
        if not task.get("isToDo"):
            continue
        if task.get("name") in FOLLOWUP_TODO_NAMES and task.get("progress") != 1:
            try:
                jobtread_query({"deleteTask": {"$": {"id": task["id"]}}})
                print(f"  Deleted to-do: {task['name']}")
                deleted += 1
            except Exception as e:
                print(f"  Could not delete to-do {task['id']}: {e}")
    return deleted


def create_followup_todos(job_id, job_type):
    """Create the post-estimate follow-up to-do chain assigned by job type."""
    from datetime import date, timedelta

    assignee_id = JASON_ID if job_type in JASON_JOB_TYPES else TYLER_ID

    def offset(n):
        return (date.today() + timedelta(days=n)).isoformat()

    tasks = [
        ("📧 Follow-up email #1 — check in on estimate", 3),
        ("📞 Follow-up call #1 — any questions?",         5),
        ("📧 Follow-up email #2 — still interested?",     7),
        ("🚨 Final decision call — win or move on",       14),
    ]

    for name, days in tasks:
        due = offset(days)
        try:
            jobtread_query({
                "createTask": {
                    "$": {
                        "name": name,
                        "isToDo": True,
                        "targetType": "job",
                        "targetId": job_id,
                        "startDate": due,
                        "endDate": due,
                        "assignees": [{"membershipId": assignee_id}],
                    }
                }
            })
            print(f"  Follow-up to-do created: {name} (due {due})")
        except Exception as e:
            print(f"  Follow-up to-do failed '{name}': {e}")


def create_longterm_todo(job_id, job_type):
    """Create a single 60-day long-term follow-up to-do."""
    from datetime import date, timedelta
    assignee_id = JASON_ID if job_type in JASON_JOB_TYPES else TYLER_ID
    due = (date.today() + timedelta(days=60)).isoformat()
    try:
        jobtread_query({
            "createTask": {
                "$": {
                    "name": "📅 Long term follow-up — check back in",
                    "isToDo": True,
                    "targetType": "job",
                    "targetId": job_id,
                    "startDate": due,
                    "endDate": due,
                    "assignees": [{"membershipId": assignee_id}],
                }
            }
        })
        print(f"  Long-term to-do created (due {due})")
    except Exception as e:
        print(f"  Long-term to-do failed: {e}")


def set_job_status(job_id, job_type, status_value):
    """Set the correct status custom field on a job based on job type."""
    field_name = get_status_field_for_job_type(job_type)
    try:
        jobtread_query({
            "updateJob": {
                "$": {
                    "id": job_id,
                    "customFieldValues": {field_name: status_value}
                },
                "job": {"$": {"id": job_id}, "id": {}}
            }
        })
        print(f"  Job status ({field_name}) set to '{status_value}'")
    except Exception as e:
        print(f"  Could not set job status: {e}")


# ── Webhook processors ────────────────────────────────────────────────────────

def process_document_sent(payload):
    """
    Fired when documentRecipientCreated/Updated in JobTread.
    On email delivery status change to pending (= just sent), process the estimate.
    """
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        event = data.get("createdEvent", {})
        event_data = event.get("data", {})
        next_state = event_data.get("next") or {}
        prev_state = event_data.get("previous") or {}

        # Only process when emailDeliveryStatus changes to "pending" (= just sent)
        # This fires exactly once per send
        new_status  = next_state.get("emailDeliveryStatus")
        prev_status = prev_state.get("emailDeliveryStatus")
        print(f"  document-sent: emailDeliveryStatus {prev_status} -> {new_status}")

        if new_status != "pending":
            print("  Not a fresh send — skipping")
            return

        # Get document ID from the event
        doc_id = next_state.get("documentId") or (event.get("document") or {}).get("id")
        if not doc_id:
            print("  No document ID found — skipping")
            return

        # Look up the document to get job ID and type
        doc_resp = jobtread_query({
            "document": {
                "$": {"id": doc_id},
                "id": {}, "type": {}, "status": {},
                "job": {"id": {}}
            }
        })
        doc_obj  = doc_resp.get("document", {})
        doc_type = doc_obj.get("type")
        job_id   = (doc_obj.get("job") or {}).get("id")
        print(f"  document-sent: doc_type={doc_type}, job_id={job_id}")

        if not job_id:
            print("  document-sent: no job ID found, skipping")
            return
        if doc_type != "customerOrder":
            print(f"  document-sent: type={doc_type}, not an estimate — skipping")
            return

        print(f"  document-sent: estimate sent on job {job_id}")
        job_info = get_job_info(job_id)
        job_type, current_status = get_job_type_and_status(job_info)

        if not job_type:
            print("  Could not determine job type — skipping")
            return

        # Don't touch closed or long-term jobs
        if current_status in TERMINAL_STATUSES or current_status == LONG_TERM_STATUS:
            print(f"  Job is '{current_status}' — skipping follow-up chain")
            return

        # Delete existing follow-up to-dos and create fresh ones
        deleted = delete_followup_todos(job_info)
        print(f"  Deleted {deleted} existing follow-up to-dos")
        create_followup_todos(job_id, job_type)

        # Flip status to Sent
        set_job_status(job_id, job_type, "Sent")

        # Log the sent date as a comment so the daily runner can find it
        from datetime import date as _date
        sent_date = _date.today().isoformat()
        try:
            jobtread_query({
                "createComment": {
                    "$": {
                        "targetType": "job",
                        "targetId": job_id,
                        "message": f"[OCC-AUTO] Estimate sent on {sent_date}. Follow-up emails scheduled for Day 3, 7, and 14.",
                    },
                    "createdComment": {"id": {}}
                }
            })
            print(f"  Sent date logged: {sent_date}")
        except Exception as ce:
            print(f"  Could not log sent date comment: {ce}")

    except Exception as e:
        import traceback
        print(f"process_document_sent error: {e}")
        traceback.print_exc()


# ── Daily follow-up email runner ─────────────────────────────────────────────

FOLLOWUP_MESSAGES = {
    3: (
        "Hi {first_name},\n\n"
        "I just wanted to follow up and make sure you received the estimate I sent over. "
        "Feel free to reach out if you have any questions or would like to talk through anything \u2014 "
        "I'm happy to help. You can view and approve the estimate using the button below."
    ),
    7: (
        "Hi {first_name},\n\n"
        "Checking in one more time on the estimate I sent over for your project. "
        "If you have any questions or if the scope of work needs any adjustments, "
        "just let me know and we can work through it together. "
        "I want to make sure we find the right solution for you."
    ),
    14: (
        "Hi {first_name},\n\n"
        "I wanted to reach out one last time regarding the estimate I sent over for your project. "
        "We'd love the opportunity to work with you. If you have any questions or would like to "
        "discuss the scope of work, please don't hesitate to give me a call. "
        "If the timing isn't right at the moment, I completely understand \u2014 "
        "just keep us in mind for the future."
    ),
}

FOLLOWUP_SENT_MARKERS = {
    3:  "[OCC-AUTO-F1]",
    7:  "[OCC-AUTO-F2]",
    14: "[OCC-AUTO-F3]",
}


def get_jobs_needing_followup():
    """
    Fetch all jobs at Sent status that need follow-up emails.
    Uses two-pass approach: first get job IDs, then fetch details per job.
    """
    results = []
    all_sent_job_ids = []

    # Pass 1: get all jobs with their custom fields only (small query)
    for status_field, job_types in [
        ("Home Repairs Status",    ["Home Repair", "Remodel", "Pre-listing Repair"]),
        ("Closing Repairs Status", ["Closing Repair"]),
    ]:
        try:
            resp = jobtread_query({
                "organization": {
                    "$": {"id": JOBTREAD_ORG},
                    "jobs": {
                        "$": {"size": 100},
                        "nodes": {
                            "id": {}, "name": {},
                            "customFieldValues": {
                                "$": {"size": 10},
                                "nodes": {"customField": {"name": {}}, "value": {}}
                            }
                        }
                    }
                }
            })
            jobs = resp.get("organization", {}).get("jobs", {}).get("nodes", [])
            for job in jobs:
                cfvs = {
                    cfv["customField"]["name"]: cfv["value"]
                    for cfv in (job.get("customFieldValues") or {}).get("nodes", [])
                    if cfv.get("customField")
                }
                if cfvs.get(status_field) != "Sent":
                    continue
                if cfvs.get("Job Type") not in job_types:
                    continue
                all_sent_job_ids.append(job["id"])
        except Exception as e:
            print(f"  get_jobs_needing_followup error: {e}")

    print(f"  Found {len(all_sent_job_ids)} jobs at Sent status")

    # Pass 2: fetch full details per job
    for job_id in all_sent_job_ids:
        try:
            resp = jobtread_query({
                "job": {
                    "$": {"id": job_id},
                    "id": {}, "name": {},
                    "customFieldValues": {
                        "$": {"size": 10},
                        "nodes": {"customField": {"name": {}}, "value": {}}
                    },
                    "comments": {
                        "$": {"size": 30},
                        "nodes": {"message": {}, "createdAt": {}}
                    },
                    "documents": {
                        "$": {"size": 5},
                        "nodes": {
                            "id": {}, "type": {}, "status": {},
                            "documentRecipients": {
                                "$": {"size": 5},
                                "nodes": {"id": {}, "user": {"name": {}}}
                            }
                        }
                    },
                    "tasks": {
                        "$": {"size": 20},
                        "nodes": {"id": {}, "name": {}, "isToDo": {}, "progress": {}}
                    }
                }
            })
            job = resp.get("job")
            if job:
                results.append(job)
        except Exception as e:
            print(f"  Could not fetch details for job {job_id}: {e}")

    return results


def parse_sent_date(comments):
    """Extract the estimate sent date from job comments."""
    import re
    from datetime import date
    for comment in (comments or []):
        body = comment.get("message", "")
        if "[OCC-AUTO] Estimate sent on" in body:
            match = re.search(r"sent on (\d{4}-\d{2}-\d{2})", body)
            if match:
                try:
                    return date.fromisoformat(match.group(1))
                except ValueError:
                    pass
    return None


def already_sent_followup(comments, day):
    """Check if a follow-up email for this day has already been sent."""
    marker = FOLLOWUP_SENT_MARKERS[day]
    return any(marker in (c.get("message") or "") for c in (comments or []))


def log_followup_sent(job_id, day):
    """Add a comment to the job recording that the follow-up was sent."""
    from datetime import date
    marker = FOLLOWUP_SENT_MARKERS[day]
    try:
        jobtread_query({
            "createComment": {
                "$": {
                    "targetType": "job",
                    "targetId": job_id,
                    "message": f"{marker} Day {day} follow-up email sent on {date.today().isoformat()}.",
                },
                "createdComment": {"id": {}}
            }
        })
    except Exception as e:
        print(f"  Could not log follow-up comment: {e}")


def process_send_followups():
    """
    Daily runner — check all Sent jobs and send follow-up emails on Day 3, 7, 14.
    Called via GET /send-followups from cron-job.org once per day.
    """
    from datetime import date
    today = date.today()
    print(f"Running daily follow-up check: {today.isoformat()}")

    jobs = get_jobs_needing_followup()
    print(f"  Found {len(jobs)} jobs at Sent status")

    sent_count = 0
    for job in jobs:
        job_id = job["id"]
        comments = (job.get("comments") or {}).get("nodes", [])
        sent_date = parse_sent_date(comments)

        if not sent_date:
            print(f"  Job {job_id}: no sent date found — skipping")
            continue

        days_since = (today - sent_date).days
        print(f"  Job {job_id}: {days_since} days since estimate sent")

        # Check if today is a follow-up day
        if days_since not in FOLLOWUP_MESSAGES:
            continue

        # Skip if already sent
        if already_sent_followup(comments, days_since):
            print(f"  Job {job_id}: Day {days_since} follow-up already sent — skipping")
            continue

        # Get the pending estimate document and recipient
        docs = (job.get("documents") or {}).get("nodes", [])
        recipient_id = None
        first_name = "there"

        for doc in docs:
            if doc.get("type") == "customerOrder" and doc.get("status") == "pending":
                recipients = (doc.get("documentRecipients") or {}).get("nodes", [])
                if recipients:
                    recipient_id = recipients[0]["id"]
                    full_name = (recipients[0].get("user") or {}).get("name", "")
                    first_name = full_name.split()[0].capitalize() if full_name else "there"
                break

        if not recipient_id:
            print(f"  Job {job_id}: no pending estimate recipient found — skipping")
            continue

        # Build and send the email
        message = FOLLOWUP_MESSAGES[days_since].format(first_name=first_name)
        try:
            jobtread_query({
                "sendDocument": {
                    "$": {
                        "documentRecipientId": recipient_id,
                        "emailMessage": message,
                    }
                }
            })
            log_followup_sent(job_id, days_since)
            print(f"  Job {job_id}: Day {days_since} follow-up sent to {first_name}")
            sent_count += 1

            # Mark the matching email follow-up to-do as complete
            email_todo_names = {
                3:  "📧 Follow-up email #1 — check in on estimate",
                7:  "📧 Follow-up email #2 — still interested?",
                14: "🚨 Final decision call — win or move on",
            }
            todo_name = email_todo_names.get(days_since)
            if todo_name:
                tasks = (job.get("tasks") or {}).get("nodes", [])
                for task in tasks:
                    if task.get("name") == todo_name and task.get("progress") != 1:
                        try:
                            jobtread_query({
                                "updateTask": {
                                    "$": {
                                        "id": task["id"],
                                        "progress": 1,
                                        "notify": False,
                                    }
                                }
                            })
                            print(f"  Checked off to-do: {todo_name}")
                        except Exception as te:
                            print(f"  Could not check off to-do: {te}")
                        break

        except Exception as e:
            print(f"  Job {job_id}: follow-up send failed: {e}")

    print(f"Daily follow-up run complete: {sent_count} emails sent")
    return sent_count


def process_job_updated(payload):
    """
    Fired when any job field is updated in JobTread.
    React to status changes: Closed Won/Lost → clean up. Long Term → 60-day to-do.
    """
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        job_id = (data.get("data") or {}).get("id")
        if not job_id:
            print("  job-updated: no job ID — skipping")
            return

        job_info = get_job_info(job_id)
        job_type, current_status = get_job_type_and_status(job_info)

        if not job_type or not current_status:
            return

        print(f"  job-updated: job {job_id} | type={job_type} | status={current_status}")

        if current_status in TERMINAL_STATUSES:
            # Clean up all open follow-up to-dos
            deleted = delete_followup_todos(job_info)
            print(f"  {current_status}: deleted {deleted} follow-up to-dos")

        elif current_status == LONG_TERM_STATUS:
            # Remove short-term follow-ups, add 60-day to-do
            deleted = delete_followup_todos(job_info)
            print(f"  Long Term Follow Up: deleted {deleted} follow-up to-dos")
            create_longterm_todo(job_id, job_type)

    except Exception as e:
        import traceback
        print(f"process_job_updated error: {e}")
        traceback.print_exc()


def create_job_full(cfg):
    """
    Unified job-creation flow used by all forms.
    1. Find-or-create account (+ contact, primary)
    2. Create location
    3. Create job (PM, status, projected budget)
    4. Cost groups (if estimate present)
    5. Attach files
    """
    account_id  = upsert_account_and_contact(cfg)
    location_id = create_location_record(account_id, cfg["location_address"])
    print(f"  Location: {location_id}")
    job_id = create_job_record(location_id, cfg)
    print(f"  Job created: {job_id}")
    add_cost_groups(job_id, cfg.get("estimate"))
    attach_files(job_id, cfg.get("file_urls", []))
    if cfg.get("status_value") == "New Lead":
        create_new_lead_todos(job_id, job_type=cfg.get("job_type", "Home Repair"))
    return job_id


def create_jobtread_job(estimate, notes_text, file_urls):
    """Closing-repairs job creation (account name = property address, realtor = contact)."""
    address = estimate.get("property_address", "")
    street  = address.split(",")[0].strip() if "," in address else address
    cfg = {
        "account_name":    address,
        "account_type":    "Closing Repair",
        "lead_source":     "Realtor",
        "referred_by":     None,
        "contact_name":    estimate.get("client_name", "Unknown"),
        "contact_email":   estimate.get("client_email", ""),
        "contact_phone":   estimate.get("client_phone", ""),
        "contact_address": address,
        "location_address": address,
        "job_type":        "Closing Repair",
        "status_field":    "Closing Repairs Status",
        "status_value":    "New Lead",
        "pm":              "Emily Peery",
        "projected_budget": None,
        "job_name":        street[:30],   # closing repairs keep the street name
        "notes_text":      notes_text,
        "estimate":        estimate,
        "file_urls":       file_urls,
        "dedup":           True,          # match on address; reuse + fix realtor primary contact
    }
    return create_job_full(cfg)


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


# ── Best-effort / general estimating (photos + description, with consult gating) ──

def map_lead_source(how_heard):
    """Map a Wufoo 'How did you hear about us?' value to a JobTread Lead Source option."""
    h = (how_heard or "").strip().lower()
    if not h or "select" in h:
        return "Unknown"
    if "gvl" in h:
        return "GVL Today Ad"
    if "online" in h:            # "Found Online" / "Online Search"
        return "Google"
    if "referral" in h:
        return "Referral"
    if "past client" in h:
        return "Referral"        # ASSUMPTION — pending confirmation; change here if needed
    return "Unknown"             # "Other" / anything unmapped


def _media_type(url):
    u = (url or "").lower().split("?")[0]
    if u.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if u.endswith(".png"):
        return "image/png"
    if u.endswith(".gif"):
        return "image/gif"
    if u.endswith(".webp"):
        return "image/webp"
    return None             # heic/pdf/unknown — skip as vision input (still attached as a file)


def download_image_block(url):
    """Download a Wufoo image and return an Anthropic image content block, or None."""
    mt = _media_type(url)
    if not mt:
        print(f"  Skipping unsupported image type for vision: {url}")
        return None
    try:
        raw = download_file(url)
        b64 = base64.b64encode(raw).decode("utf-8")
        return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}}
    except Exception as e:
        print(f"  Image fetch failed ({url}): {e}")
        return None


def _call_anthropic(content):
    """Send a content array to Claude and parse the JSON estimate. Shared by general calls."""
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


def call_claude_general(form_label, client_name, client_phone, client_email,
                        address, description, notes, image_urls=None, pdf_text="",
                        best_effort=True):
    """
    Best-effort / full estimate from a homeowner inquiry (description + optional photos/PDF).
    Returns the parsed estimate dict (cost_groups may be empty with needs_consult=true).
    """
    content = []
    intro = f"""Generate a {form_label} estimate for Owners Choice Construction.

Client name: {client_name}
Client phone: {client_phone}
Client email: {client_email}
Property address: {address}
{f"Intake notes: {notes}" if notes else ""}

This is a {'best-effort estimate from a homeowner inquiry (no formal inspection report)' if best_effort else 'full estimate'}. Analyze the client's description{', the uploaded photos' if image_urls else ''}{', and the inspection report' if pdf_text else ''} to scope and price the work. Apply OCC pricing, labor classification, and scope/exclusion rules. If the information is too vague to estimate responsibly, return an empty cost_groups array with needs_consult=true and a short consult_reason — do not invent scope.
"""
    content.append({"type": "text", "text": intro})
    content.append({"type": "text", "text": f"\n=== CLIENT DESCRIPTION OF WORK ===\n{(description or '')[:8000]}"})
    if pdf_text:
        content.append({"type": "text", "text": f"\n=== INSPECTION REPORT ===\n{pdf_text[:30000]}"})
    for url in (image_urls or []):
        blk = download_image_block(url)
        if blk:
            content.append(blk)
    content.append({"type": "text", "text": "\nRespond with ONLY the raw JSON object. No markdown, no explanation."})
    return _call_anthropic(content)


def build_general_notes(form, pref=None, how=None, work=None, budget=None,
                        inspection=None, realtor=None, realtor_email=None, other=None):
    """Assemble JobTread job notes for the non-closing-repair forms."""
    parts = [f"Source Form: {form}"]
    if pref:
        parts.append(f"Preferred Contact: {pref}")
    if how:
        parts.append(f"How they heard: {how}")
    if budget:
        parts.append(f"Stated Budget: {budget}")
    if inspection:
        parts.append(f"Inspection Completed: {inspection}")
    if realtor:
        parts.append(f"Realtor: {realtor}" + (f" ({realtor_email})" if realtor_email else ""))
    if work and work.strip():
        parts.append(f"\nWork Requested:\n{work.strip()}")
    if other and other.strip():
        parts.append(f"\nAdditional Info:\n{other.strip()}")
    return "\n".join(parts)


def _apply_estimate_gate(estimate, notes):
    """Given a returned estimate, decide budget vs consult. Returns (estimate_or_None, notes, projected)."""
    projected = None
    if not estimate:
        return None, notes, None
    cgs = estimate.get("cost_groups") or []
    if estimate.get("needs_consult") or not cgs:
        reason = estimate.get("consult_reason") or "insufficient detail for a budget"
        notes += f"\n\n⚠️ CONSULT / SITE VISIT NEEDED: {reason}"
    if not cgs:
        return None, notes, None
    total = estimate.get("total", 0) or 0
    if total > 0:
        projected = f"${total:,.0f}"
    return estimate, notes, projected


# ── Form processors ───────────────────────────────────────────────────────────

def process_home_repairs(data, form_name="Home Repair", lead_source_override=None):
    """Home Repairs and GVL Today share the same field layout."""
    def g(k):
        return data.get(k, [""])[0]
    name    = f"{g('Field1')} {g('Field2')}".strip()
    phone   = g("Field3")
    email   = g("Field4")
    street  = g("Field5"); city = g("Field7"); state = g("Field8"); zc = g("Field9")
    address = f"{street}, {city}, {state} {zc}".strip(", ")
    pref    = g("Field428")
    how     = g("Field426")
    work    = g("Field121")
    p1_url, p2_url = g("Field13-url"), g("Field324-url")
    p1_nm   = g("Field13") or "Photo 1"
    p2_nm   = g("Field324") or "Photo 2"

    print(f"New {form_name} submission: {name} — {address}")
    lead_source = lead_source_override or map_lead_source(how)
    notes = build_general_notes(form_name, pref=pref,
                                how=(how if not lead_source_override else None), work=work)

    image_urls = [u for u in [p1_url, p2_url] if u]
    file_urls  = [(p1_nm, p1_url), (p2_nm, p2_url)]

    estimate = None
    try:
        estimate = call_claude_general(f"{form_name.lower()} repair", name, phone, email,
                                       address, work, notes, image_urls=image_urls)
        print(f"  Estimate total: ${estimate.get('total', 0) or 0:,.2f} | consult={estimate.get('needs_consult')}")
    except Exception as e:
        print(f"  Claude failed (continuing without estimate): {e}")
    estimate, notes, projected = _apply_estimate_gate(estimate, notes)

    cfg = {
        "account_name": name, "account_type": "Home Repair", "lead_source": lead_source,
        "referred_by": None, "contact_name": name, "contact_email": email,
        "contact_phone": phone, "contact_address": address, "location_address": address,
        "job_type": "Home Repair", "status_field": "Home Repairs Status",
        "status_value": "New Lead", "pm": "Emily Peery", "projected_budget": projected,
        "job_name": None, "notes_text": notes, "estimate": estimate,
        "file_urls": file_urls, "dedup": True,
    }
    return create_job_full(cfg)


def process_gvl(data):
    """GVL Today repairs form — same layout as Home Repairs, lead source hardcoded."""
    return process_home_repairs(data, form_name="GVL Today", lead_source_override="GVL Today Ad")


def process_remodel(data):
    def g(k):
        return data.get(k, [""])[0]
    name    = f"{g('Field1')} {g('Field2')}".strip()
    street  = g("Field3"); city = g("Field5"); state = g("Field6"); zc = g("Field7")
    address = f"{street}, {city}, {state} {zc}".strip(", ")
    email   = g("Field9")
    phone   = g("Field10")
    how     = g("Field26")
    budget  = g("Field24")
    desc    = g("Field22")

    print(f"New Remodel submission: {name} — {address}")
    lead_source = map_lead_source(how)
    notes = build_general_notes("Remodel", how=how, budget=budget, work=desc)

    estimate = None
    try:
        estimate = call_claude_general("remodel", name, phone, email, address, desc, notes)
        print(f"  Estimate total: ${estimate.get('total', 0) or 0:,.2f} | consult={estimate.get('needs_consult')}")
    except Exception as e:
        print(f"  Claude failed (continuing without estimate): {e}")
    estimate, notes, projected = _apply_estimate_gate(estimate, notes)

    # Remodels: the client's stated range is the budget anchor regardless of estimate
    if budget:
        projected = budget

    cfg = {
        "account_name": name, "account_type": "Remodel", "lead_source": lead_source,
        "referred_by": None, "contact_name": name, "contact_email": email,
        "contact_phone": phone, "contact_address": address, "location_address": address,
        "job_type": "Remodel", "status_field": "Home Repairs Status",
        "status_value": "New Lead", "pm": "Emily Peery", "projected_budget": projected,
        "job_name": None, "notes_text": notes, "estimate": estimate,
        "file_urls": [], "dedup": True,
    }
    return create_job_full(cfg)


def process_prelisting(data):
    def g(k):
        return data.get(k, [""])[0]
    name    = f"{g('Field1')} {g('Field2')}".strip()
    street  = g("Field3"); city = g("Field5"); state = g("Field6"); zc = g("Field7")
    address = f"{street}, {city}, {state} {zc}".strip(", ")
    email   = g("Field9")
    phone   = g("Field10")
    how     = g("Field24")
    insp_done = g("Field12")
    insp_url  = g("Field20-url")
    insp_name = g("Field20") or "Inspection Report"
    has_realtor = g("Field14")
    realtor_name = f"{g('Field16')} {g('Field17')}".strip()
    realtor_email = g("Field18")
    other   = g("Field22")

    print(f"New Pre-listing submission: {name} — {address}")

    # Realtor presence overrides the 'how did you hear' dropdown for lead source
    if realtor_name or has_realtor.strip().lower() == "yes":
        lead_source = "Realtor"
        referred_by = realtor_name or None
    else:
        lead_source = map_lead_source(how)
        referred_by = None

    notes = build_general_notes("Pre-listing Repair", how=how, inspection=insp_done,
                                realtor=(realtor_name or None),
                                realtor_email=(realtor_email or None), other=other)
    if insp_done.strip().lower() == "yes" and not insp_url:
        notes += "\n\n⚠️ Client indicated an inspection report but none was attached — follow up to obtain it."

    # Parse inspection PDF for full-AI treatment when present
    pdf_text = ""
    if insp_url:
        try:
            pdf_text = "\n".join(t for _, t in extract_pdf_text(download_file(insp_url)))
            print(f"  Inspection report: {len(pdf_text)} chars extracted")
        except Exception as e:
            print(f"  Inspection PDF failed: {e}")

    file_urls = [(insp_name, insp_url)] if insp_url else []

    estimate = None
    try:
        estimate = call_claude_general("pre-listing repair", name, phone, email, address,
                                       other, notes, pdf_text=pdf_text,
                                       best_effort=(not pdf_text))
        print(f"  Estimate total: ${estimate.get('total', 0) or 0:,.2f} | consult={estimate.get('needs_consult')}")
    except Exception as e:
        print(f"  Claude failed (continuing without estimate): {e}")
    estimate, notes, projected = _apply_estimate_gate(estimate, notes)

    cfg = {
        "account_name": name, "account_type": "Home Repair", "lead_source": lead_source,
        "referred_by": referred_by, "contact_name": name, "contact_email": email,
        "contact_phone": phone, "contact_address": address, "location_address": address,
        "job_type": "Pre-listing Repair", "status_field": "Home Repairs Status",
        "status_value": "New Lead", "pm": "Emily Peery", "projected_budget": projected,
        "job_name": None, "notes_text": notes, "estimate": estimate,
        "file_urls": file_urls, "dedup": True,
    }
    return create_job_full(cfg)


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
        elif path == "/send-followups":
            self._handle_send_followups()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OCC Estimator Backend is running!")

    def _handle_send_followups(self):
        """Daily cron endpoint — send follow-up emails for Day 3, 7, 14."""
        try:
            count = process_send_followups()
            msg = f"Follow-up run complete: {count} emails sent."
            self.send_response(200)
            self.end_headers()
            self.wfile.write(msg.encode("utf-8"))
        except Exception as e:
            import traceback
            err = f"Follow-up run failed: {e}\n{traceback.format_exc()}"
            print(err)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(err.encode("utf-8"))

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
        path = self.path.split("?")[0].rstrip("/").lower()

        print(f"POST received: path={path} length={length}")

        # Respond to Wufoo immediately — it has a short timeout
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except BrokenPipeError:
            pass

        # Process in background thread, routed by path
        t = threading.Thread(target=self._route, args=(path, body))
        t.daemon = True
        t.start()

    def _route(self, path, body):
        """Dispatch each incoming POST to its processor."""
        try:
            if path in ("/home-repairs", "/home-repair"):
                process_home_repairs(urllib.parse.parse_qs(body))
            elif path in ("/gvl-today", "/gvl", "/gvltoday"):
                process_gvl(urllib.parse.parse_qs(body))
            elif path == "/remodel":
                process_remodel(urllib.parse.parse_qs(body))
            elif path in ("/prelisting", "/pre-listing", "/prelisting-repairs"):
                process_prelisting(urllib.parse.parse_qs(body))
            elif path == "/jobtread-document-sent":
                process_document_sent(body)
            elif path == "/jobtread-job-updated":
                process_job_updated(body)
            else:
                # Default = closing repairs Wufoo form
                self._process(body)
        except Exception as e:
            print(f"Routing error on '{path}': {e}")
            import traceback
            traceback.print_exc()

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
