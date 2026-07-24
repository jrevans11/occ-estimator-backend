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

# v2 closing-repair estimating logic (labor+material breakdown, real cost
# codes, historical reference examples, Home Depot catalog resolution,
# addendum vision support). See estimate_v2_draft.py module docstring for
# full history. Used ONLY by the two closing-repair flows below
# (process_sales_tool_closing_estimate and the Wufoo closing-repair
# handler) — deliberately NOT wired into call_claude()/add_cost_groups()/
# get_system_prompt(), which stay untouched because call_claude_general()
# (Home Repair, GVL, Remodel, Pre-listing, and the general sales-tool flow)
# still depends on their original flat-price schema and behavior.
import estimate_v2_draft as v2

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

LABOR CLASSIFICATION (who performs the work — drives which markup applies;
refined Jul 2026 from Jason's direct answers on real gray-area cases):
- SUBCONTRACTOR work (apply 45% markup): all electrical work; MAJOR HVAC
  repairs (system replacement, compressor, refrigerant, major ductwork);
  MAJOR plumbing (re-pipe, sewer/drain line, water heater replacement, slab
  leaks, cast iron drain work of any kind, ANY drain work located in the
  crawlspace, and any internal-parts replacement on a plumbing fixture such
  as a shower valve cartridge or valve body — too many variables for
  in-house crews); ALL crawlspace work related to moisture mitigation
  (vapor barrier, dehumidifier, clean-out, fungal/mold treatment, sump
  pump, crawlspace insulation repair/replacement) — no small-fix exception,
  always sub regardless of size.
- IN-HOUSE work (apply $89/hr labor + 65% material markup): everything else
  — drywall, paint, carpentry, trim, flooring, general handyman repairs,
  and minor plumbing/HVAC fixture work such as a sink pop-up assembly,
  securing a loose shower arm/faucet/fixture, clearing a simple
  slow-draining sink or vanity P-trap, exposed HVAC lineset insulation, and
  small/loose duct-work repairs or strapping. ALWAYS in-house regardless of
  what else is happening in the job: exterior wood rot repair, small
  siding or roofing repairs, window parts replacement, window/door
  replacement, and other general handyman-type repairs.
- HVAC INSPECTIONS: only add a sub HVAC inspection line item when the
  inspection report specifically calls for an HVAC inspection/diagnostic
  visit. Otherwise, small HVAC items (lineset insulation, minor duct
  repairs/strapping) stay in-house without a sub inspection fee attached.
- JOB-WIDE BUNDLING RULE (applies to plumbing, electrical, and HVAC alike):
  if a job already has enough sub-scope work in a trade to require calling
  that sub out anyway — e.g. plumbing: a water heater replacement/missing
  expansion tank, a re-pipe, several leaking drains, or any cast iron drain
  work; electrical: a panel job or major rewiring; HVAC: a system/
  compressor replacement — bundle the smaller same-trade items that would
  normally be in-house into that sub's visit too, since they're already
  on-site for it (e.g. a loose faucet, a slow drain, an exterior spigot).
  Still itemize each bundled item as its own line where possible rather
  than one lump sum. If a trade's ONLY issue in the job is a small,
  in-house-eligible item with no other sub-scope trigger in that same
  trade (e.g. a single exterior spigot replacement and nothing else
  plumbing-related), keep it in-house.

NEVER ESTIMATE — OUT OF SCOPE (OCC does not offer these):
- Radon remediation/mitigation
- Landscaping, grading, or regrading work
- Driveway crack repair or sealing
- Fireplace cleaning or chimney/fireplace inspections
- Pool repairs
If the requested work includes any out-of-scope item, exclude it from the estimate and list it under "skipped_items" with the reason "not offered by OCC", but still estimate everything else that is in scope.

REPAIR PRICING REFERENCE (36 real OCC estimates + 197 inspection reports)
Adjust for actual scope/site conditions. Apply $89/hr labor + 65% material markup for in-house work.

EXTERIOR:
  - Secure/repair/replace damaged siding: ~$897
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


def get_system_prompt_v2():
    """
    System prompt for the v2 closing-repair flow (call_claude_v2 +
    add_cost_groups_v2) — used ONLY by process_sales_tool_closing_estimate
    and the Wufoo closing-repair handler.

    Reuses SYSTEM_PROMPT's shared sections verbatim (company info, pricing
    rules, labor classification, out-of-scope rules, customer-facing output
    rules, scope rules, best-effort gating, description formatting, labor
    tagging) so any future edits to those sections apply automatically to
    both prompts. Swaps out exactly two pieces:
      1. The flat "REPAIR PRICING REFERENCE" lookup-table intro is replaced
         with v2.build_full_estimating_prompt() (the STEP 1-4 reasoning
         method + 17 real historical examples) — the old EXTERIOR/INTERIOR/
         GARAGE price tables themselves are KEPT, right after it, since
         build_full_estimating_prompt()'s own text explicitly frames them as
         a sanity-check reference ("the per-category price lists below").
      2. The old flat OUTPUT schema (single "price" field) is replaced with
         v2.EXAMPLE_OUTPUT_SCHEMA (cost_code, labor_lines, material_lines,
         sub_scope_price, confidence, quantity_note).

    Deliberately does NOT modify SYSTEM_PROMPT or get_system_prompt() —
    call_claude_general() (Home Repair/GVL/Remodel/Pre-listing/general
    sales-tool flows) shares those with the OLD add_cost_groups(), which
    only understands the flat "price" field. Changing the shared prompt's
    output schema would silently break estimate generation for all of those
    other job types.
    """
    price_ref_marker = "REPAIR PRICING REFERENCE"
    price_tables_marker = "EXTERIOR:"
    customer_facing_marker = "CUSTOMER-FACING OUTPUT RULES"
    output_marker = "OUTPUT: Respond with ONLY valid JSON, no markdown:"

    required = [price_ref_marker, price_tables_marker, customer_facing_marker, output_marker]
    if not all(m in SYSTEM_PROMPT for m in required):
        print("  WARNING: get_system_prompt_v2() couldn't find expected markers in "
              "SYSTEM_PROMPT — falling back to get_system_prompt() (old schema). "
              "This means the v2 closing-repair flow will silently behave like the "
              "old flow until SYSTEM_PROMPT's structure is reconciled.")
        return get_system_prompt()

    head = SYSTEM_PROMPT[:SYSTEM_PROMPT.index(price_ref_marker)]
    price_tables = SYSTEM_PROMPT[SYSTEM_PROMPT.index(price_tables_marker):SYSTEM_PROMPT.index(customer_facing_marker)]
    shared_rules = SYSTEM_PROMPT[SYSTEM_PROMPT.index(customer_facing_marker):SYSTEM_PROMPT.index(output_marker)]

    estimating_logic = v2.build_full_estimating_prompt()
    new_output_schema = "OUTPUT: Respond with ONLY valid JSON, no markdown:\n" + v2.EXAMPLE_OUTPUT_SCHEMA

    return (
        head
        + estimating_logic + "\n\n"
        + price_tables + "\n"
        + shared_rules
        + new_output_schema
    )


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

def call_claude(addendum_text, inspection_pdf_bytes, client_name, client_phone, client_email, address, notes):
    """
    Build Claude content and call API. Returns parsed estimate dict.

    The repair addendum is sent as extracted text (it's reliably a clean,
    text-based form). The inspection report is sent as a native PDF document
    block instead of pypdf-extracted text — inspection reports are often
    image-heavy with handwritten annotations circling/marking specific issues,
    which a text-only extraction would miss entirely. Native PDF mode lets
    Claude see the actual pages, photos, and handwriting directly.
    """
    content = []

    intro = f"""Generate a closing repairs estimate for Owners Choice Construction.

Client name: {client_name}
Client phone: {client_phone}
Client email: {client_email}
Property address: {address}
{f"Realtor notes: {notes}" if notes else ""}

Process the repair addendum first to identify all requested items, then cross-reference with the inspection report (provided as a PDF below — read both the printed text and any handwritten notes, circles, arrows, or markups on the pages) to write accurate scope descriptions and calibrate pricing based on described severity.
"""
    content.append({"type": "text", "text": intro})

    if addendum_text:
        content.append({"type": "text", "text": f"\n=== REPAIR ADDENDUM ===\n{addendum_text[:20000]}"})

    if inspection_pdf_bytes:
        try:
            from pypdf import PdfReader
            page_count = len(PdfReader(io.BytesIO(inspection_pdf_bytes)).pages)
            if page_count > 100:
                raise ValueError(
                    f"Inspection report has {page_count} pages — Claude's PDF support "
                    f"caps at 100 pages per document. Split the file and try again."
                )
        except ValueError:
            raise
        except Exception as e:
            print(f"  Could not pre-check inspection PDF page count: {e}")

        inspection_b64 = base64.b64encode(inspection_pdf_bytes).decode("utf-8")
        content.append({"type": "text", "text": "\n=== INSPECTION REPORT (PDF below — read text, photos, and handwritten annotations) ==="})
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": inspection_b64
            }
        })

    content.append({"type": "text", "text": "\nRespond with ONLY the raw JSON object. No markdown, no explanation."})

    base_payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 8000,  # raised from 4000 — large repair lists were getting truncated mid-JSON
        "system": get_system_prompt(),
        "messages": [{"role": "user", "content": content}]
    }

    last_error = None
    for attempt in range(1, 4):  # up to 3 attempts before giving up
        try:
            payload = json.dumps(base_payload).encode("utf-8")
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

            stop_reason = result.get("stop_reason")
            raw = "".join(block.get("text", "") for block in result.get("content", []))

            if stop_reason == "max_tokens":
                # Response was cut off — guaranteed-broken JSON, don't even try to parse
                raise ValueError(
                    f"Claude response truncated by max_tokens (attempt {attempt}), "
                    f"got {len(raw)} chars before cutoff"
                )

            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                raise ValueError(f"No JSON object found in Claude response: {raw[:500]}")

            return json.loads(match.group(0))

        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"  call_claude attempt {attempt} failed: {e}")
            if attempt < 3:
                print(f"  Retrying ({attempt + 1}/3)...")
                continue

    # All attempts exhausted — fail loudly with enough detail to debug,
    # rather than silently dropping the lead.
    raise Exception(
        f"call_claude failed after 3 attempts. Last error: {last_error}. "
        f"This lead needs to be entered manually — check Render logs for the raw Claude output."
    )


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
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Real bug found Jul 2026: a createJob 400 on a real submission
        # (Ashley Swann, 8 Hidden Hills Ct.) left nothing but "HTTP Error
        # 400: Bad Request" in the log, with NO indication of which field
        # JobTread actually rejected -- urlopen's default error handling
        # discards the response body. The Pave API returns a real JSON
        # error body (e.g. field-level validation messages) on a 400; read
        # and surface it here so the next failure is actually diagnosable
        # instead of a blind "Bad Request."
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = "<could not read error response body>"
        raise Exception(f"JobTread API error {e.code}: {body}") from e


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


# ── Input normalisation ───────────────────────────────────────────────────────

def normalize_name(name):
    """Title-case a person name: 'john smith' → 'John Smith', 'MARY JANE' → 'Mary Jane'.
    Handles hyphenated names (Mary-Jane → Mary-Jane), initials (j. → J.),
    and prefixes/suffixes (mcgee → McGee via simple title()).
    """
    if not name:
        return name
    return " ".join(part.capitalize() for part in name.strip().split())


def normalize_phone(phone):
    """Return a consistently formatted US phone number string.

    Stored in the contact custom field as (XXX) XXX-XXXX.
    Returns the original string unchanged if it doesn't look like a 10- or
    11-digit US number (so international numbers aren't mangled).
    """
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone.strip()  # couldn't normalise — return as-is


def normalize_address(address):
    """Title-case a street address: '123 main st' → '123 Main St'.
    Leaves zip codes (all-digit tokens) alone. Uppercases 2-letter state
    abbreviations only when they appear in the city/state/zip component
    (the last comma-separated part that also contains a zip code), not in
    street suffixes like 'St', 'Dr', 'Ct' which should be title-cased.
    """
    if not address:
        return address
    parts = [p.strip() for p in address.split(",")]
    # The state abbreviation lives in the last component that contains a zip code.
    # e.g. "SC 29687" — we uppercase the alpha-only tokens there, title-case elsewhere.
    last_has_zip = parts and any(t.isdigit() for t in parts[-1].split())
    result = []
    for i, part in enumerate(parts):
        is_state_part = last_has_zip and (i == len(parts) - 1)
        tokens = part.split()
        cased = []
        for token in tokens:
            if token.isdigit():
                cased.append(token)  # zip code — leave as-is
            elif is_state_part and len(token) == 2 and token.isalpha():
                cased.append(token.upper())  # state abbreviation — uppercase
            else:
                cased.append(token.capitalize())  # everything else — title case
        result.append(" ".join(cased))
    return ", ".join(result)


def normalize_e164(phone):
    """Return E.164 format (+1XXXXXXXXXX) for use in API calls / email logic.
    Falls back to the raw string if it can't be normalised.
    """
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    return phone.strip()


def find_account_by_name(name, address=None):
    """Exact-name lookup with optional address tiebreaker.

    1. Fetch all customer accounts whose name matches (case-insensitive).
    2. If exactly one match — return it immediately.
    3. If multiple matches and an address was supplied — pick the one whose
       location address most closely matches (normalised string compare).
    4. If multiple matches and no address — return the first match so we
       still avoid creating a duplicate rather than always creating a new one.

    Kept intentionally small (id + name + locations) — requesting nested
    contacts/custom fields here would trip JobTread's request-size limit.
    """
    if not name or not name.strip():
        return None
    try:
        resp = jobtread_query({
            "organization": {
                "$": {"id": JOBTREAD_ORG},
                "accounts": {
                    "$": {"size": 20, "where": {"and": [["type", "customer"], ["name", "like", name.strip()]]}},
                    "nodes": {
                        "id": {}, "name": {},
                        "locations": {"$": {"size": 10}, "nodes": {"id": {}, "address": {}}}
                    }
                }
            }
        })
        nodes = resp.get("organization", {}).get("accounts", {}).get("nodes", [])
    except Exception as e:
        print(f"  Account lookup failed (treating as new): {e}")
        return None

    target_name = name.strip().lower()
    matches = [n for n in nodes if (n.get("name") or "").strip().lower() == target_name]

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]["id"]

    # Multiple accounts with the same name — use address as tiebreaker
    if address:
        target_addr = re.sub(r"\s+", " ", address.strip().lower())
        for acct in matches:
            for loc in (acct.get("locations") or {}).get("nodes", []):
                loc_addr = re.sub(r"\s+", " ", (loc.get("address") or "").strip().lower())
                if loc_addr and loc_addr == target_addr:
                    print(f"  Multiple name matches — address tiebreaker resolved to account {acct['id']}")
                    return acct["id"]

    # No address tiebreaker or no match on address — return first (avoids creating yet another duplicate)
    print(f"  Multiple name matches for '{name}' — no address tiebreaker, using first match {matches[0]['id']}")
    return matches[0]["id"]


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


def find_matching_contact(contacts, phone, contact_name):
    """Given a list of contact nodes, find one matching by name AND phone.

    Both name and phone must match (case/whitespace insensitive, digits-only
    comparison for phone) to be considered the same person.  If phone is
    blank we fall back to name-only so we still deduplicate when a phone
    number wasn't captured on the original entry.
    """
    cname = (contact_name or "").strip().lower()
    # Strip to digits for a reliable phone comparison
    cphone_digits = re.sub(r"\D", "", phone or "")

    for c in contacts:
        cfv = _contact_cfv(c)
        c_name_match = (c.get("name") or "").strip().lower() == cname

        if cphone_digits:
            c_phone_digits = re.sub(r"\D", "", cfv.get("Phone") or "")
            if c_name_match and c_phone_digits and c_phone_digits == cphone_digits:
                return c
        else:
            # No phone supplied — match on name alone
            if cname and c_name_match:
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
    """Create a contact under an account and return its id.
    Normalises name (title case), phone (display format), and address (title case)
    before writing so records are always clean regardless of how the data arrived.
    """
    clean_name    = normalize_name(name) or "Unknown"
    clean_phone   = normalize_phone(phone) if phone else None
    clean_address = normalize_address(address) if address else None
    cfv = {"Email": (email or "").strip().lower(), "Address": clean_address or ""}
    if clean_phone:
        cfv["Phone"] = clean_phone
    resp = jobtread_query({
        "createContact": {
            "$": {"accountId": account_id, "name": clean_name, "customFieldValues": cfv},
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

    account_id = find_account_by_name(cfg["account_name"], address=cfg.get("location_address")) if cfg.get("dedup") else None

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
        match = find_matching_contact(contacts, cfg["contact_phone"], cfg["contact_name"])
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
    clean_account_name = normalize_name(cfg["account_name"]) or cfg["account_name"]
    resp = jobtread_query({
        "createAccount": {
            "$": {
                "organizationId": JOBTREAD_ORG,
                "type": "customer",
                "name": clean_account_name,
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


# JobTread's "Notes" job custom field has a real, confirmed hard limit
# (Jul 2026): a real submission's createJob call failed outright with
# "Unable to save custom field 'Notes': Value cannot be more than 1024
# characters" -- this crashed job creation completely, before ANY of the
# rest of the pipeline (estimate, cost groups, files) ever ran. Separately,
# Jason confirmed the old assumption in the comment below ("internal-only,
# not shown on customer documents") was WRONG -- Notes actually does show
# up on customer-facing order documents. So long text (sometimes literally
# a realtor's full repair list, pasted into a Wufoo text field instead of
# a separate PDF) shouldn't go in this field at all, not just when it's
# over the hard limit. See _split_notes_for_job() / create_job_daily_log().
JOB_NOTES_FIELD_SAFE_LIMIT = 1000  # real cap is 1024; leave a little headroom


def _split_notes_for_job(notes_text):
    """Split submission notes into (job_field_notes, overflow_notes).

    If notes_text fits safely within JobTread's real ~1024-char cap on the
    job-level "Notes" custom field, it's used as-is and overflow_notes is
    None (nothing else to do). If it's too long, job_field_notes becomes a
    short pointer (not the real content -- avoids the customer-facing-
    document problem too), and overflow_notes carries the FULL original
    text so the caller can post it somewhere internal instead (see
    create_job_daily_log) — nothing is ever silently dropped.
    """
    notes_text = notes_text or ""
    if len(notes_text) <= JOB_NOTES_FIELD_SAFE_LIMIT:
        return notes_text, None
    return ("See Daily Log for full submission notes "
            "(too long for this field)."), notes_text


def create_job_daily_log(job_id, notes_text, log_date=None):
    """Post an internal Daily Log entry on a job.

    Confirmed real mutation (Jul 2026, live-tested on a real job — posted a
    clearly-labeled test entry, verified the exact request via network
    capture, then deleted it): createDailyLog takes jobId/date/notes/notify
    directly, no targetType/targetId indirection like createTask/createFile
    use. Daily Logs default to nobody having explicit access ("Daily Log
    Access: Nobody has been given direct access to this Daily Log" in the
    UI) and notify=False suppresses any notification — unlike the job-level
    "Notes" custom field, this does NOT show up on customer-facing order
    documents. Best-effort: never blocks job creation if this fails.
    """
    from datetime import date as _date
    log_date = log_date or _date.today().isoformat()
    try:
        jobtread_query({
            "createDailyLog": {
                "$": {
                    "jobId": job_id,
                    "date": log_date,
                    "notes": notes_text,
                    "notify": False,
                },
                "createdDailyLog": {"id": {}}
            }
        })
        print(f"  Daily Log created for job {job_id} (full notes, {len(notes_text)} chars)")
    except Exception as e:
        print(f"  Daily Log creation failed for job {job_id} (non-fatal): {e}")


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
    if cfg.get("notes_text"):
        # See JOB_NOTES_FIELD_SAFE_LIMIT / _split_notes_for_job() above —
        # only the safe, short portion (or a pointer, if the real notes are
        # too long) goes in this customer-visible field. create_job_full()
        # posts the full text as a Daily Log separately when it's too long.
        job_field_notes, _ = _split_notes_for_job(cfg["notes_text"])
        job_cfv["Notes"] = job_field_notes

    job_input = {
        "locationId": location_id,
        "priceType": "fixed",
        "scheduleIsPublished": True,
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


def next_business_day(d):
    """
    Roll a date forward to the next Monday if it lands on a Saturday or
    Sunday. Jason doesn't work weekends, so no to-do should ever be due on
    one — anything that would land there gets pushed to the following Monday.
    """
    # Monday=0 ... Saturday=5, Sunday=6
    if d.weekday() == 5:      # Saturday → Monday
        from datetime import timedelta
        return d + timedelta(days=2)
    if d.weekday() == 6:      # Sunday → Monday
        from datetime import timedelta
        return d + timedelta(days=1)
    return d


def create_single_todo(job_id, job_type, name, due_offset=0):
    """Create a single to-do on a job assigned to the correct team member.
    Due date is rolled forward off a weekend to the following Monday."""
    from datetime import date, timedelta
    assignee_id = JASON_ID if job_type in JASON_JOB_TYPES else TYLER_ID
    due_date = next_business_day(date.today() + timedelta(days=due_offset))
    due = due_date.isoformat()
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
        print(f"  To-do created: {name} (due {due})")
    except Exception as e:
        print(f"  To-do failed '{name}': {e}")


def create_new_lead_todos(job_id, job_type="Home Repair"):
    """
    Create ONLY the first to-do when a new lead is created.
    Each subsequent to-do is created when the previous one is checked off (domino effect).
    This keeps the action items view clean — only the current step is visible.
    """
    if job_type not in AUTOMATION_ENABLED_JOB_TYPES:
        print(f"  Skipping to-dos — {job_type} not in automation scope")
        return
    create_single_todo(job_id, job_type, "📞 Call customer — introduce & qualify", due_offset=0)


# ── Follow-up to-do chain (post-estimate) ────────────────────────────────────

JASON_ID  = "22P9ppHePJKQ"
TYLER_ID  = "22PBsSvmYBUj"
JASON_JOB_TYPES = {"Home Repair", "Closing Repair", "Remodel", "Pre-listing Repair"}

# User IDs whose task completions trigger pipeline automation.
# These are USER IDs (from webhook payload createdByUser.id) — NOT membership IDs.
# Only these users can trigger status changes via to-do check-off.
# To enable for Tyler or Ben, uncomment their lines and redeploy.
AUTOMATION_USER_IDS = {
    "22P9ppHdzeEn",  # Jason Evans
    # "22PBsSvmYYP4",  # Tyler Jarratt
    # "22PBsT3aS3cy",  # Ben Creasman
}

# ── Automation scope ──────────────────────────────────────────────────────────
# Only these job types participate in the full automation pipeline
# (to-dos, follow-up emails, status flips, closed/long-term cleanup).
# To enable automation for additional job types (e.g. Tyler's jobs),
# simply add them to this set.
AUTOMATION_ENABLED_JOB_TYPES = {
    "Home Repair",
    "Closing Repair",
    "Remodel",
    "Pre-listing Repair",
}

# Status field IDs by job type
HOME_REPAIR_STATUS_FIELD    = "22PFPUHGUt4g"   # Home Repairs Status
CLOSING_REPAIR_STATUS_FIELD = "22PFPSefyzSp"   # Closing Repairs Status

# Status values that mean the job is closed — stop all follow-ups
TERMINAL_STATUSES = {"Closed Won", "Closed Lost"}
LONG_TERM_STATUS  = "Long Term Follow Up"

# ── Pipeline order ───────────────────────────────────────────────────────────
# Used to determine if a status change is forward or backward in the pipeline.
# Only forward moves trigger to-do syncing.
PIPELINE_ORDER = [
    "New Lead",
    "Appointment Set",
    "Estimating",
    "Sent",
    "Sent 1st Follow Up",
    "Sent 2nd Follow Up",
    "Sent Final Follow UP",
    "Revising",
    "Closed Won",
    "Long Term Follow Up",
    "Closed Lost",
]

def is_forward_move(from_status, to_status):
    """Return True only if to_status is ahead of from_status in the pipeline."""
    try:
        return PIPELINE_ORDER.index(to_status) > PIPELINE_ORDER.index(from_status)
    except ValueError:
        return False  # Unknown status — don't act

# ── To-do chain definition ───────────────────────────────────────────────────
# Each to-do, when checked off, creates the next one automatically (domino effect).
# offset = days from today when the next to-do is due.
# status = pipeline status to flip when this to-do is checked off (optional).
# For Closing Repair the offsets are tighter — handled in get_next_todo().

TODO_CHAIN = [
    {
        "name":    "📞 Call customer — introduce & qualify",
        "status":  None,
        "next":    "📅 Schedule site visit",
        "offset":  1,
    },
    {
        "name":    "📅 Schedule site visit",
        "status":  "Appointment Set",
        "next":    "📝 Build estimate",
        "offset":  4,
    },
    {
        "name":    "📝 Build estimate",
        "status":  "Estimating",
        "next":    "📤 Send estimate to customer",
        "offset":  1,
    },
    {
        "name":    "📤 Send estimate to customer",
        "status":  "Sent",
        "next":    None,   # Estimate sent webhook takes over from here
        "offset":  0,
    },
]

# Closing Repair uses same chain but tighter offsets
TODO_CHAIN_CLOSING = [
    {
        "name":    "📞 Call customer — introduce & qualify",
        "status":  None,
        "next":    "📅 Schedule site visit",
        "offset":  1,
    },
    {
        "name":    "📅 Schedule site visit",
        "status":  "Appointment Set",
        "next":    "📝 Build estimate",
        "offset":  1,
    },
    {
        "name":    "📝 Build estimate",
        "status":  "Estimating",
        "next":    "📤 Send estimate to customer",
        "offset":  1,
    },
    {
        "name":    "📤 Send estimate to customer",
        "status":  "Sent",
        "next":    None,
        "offset":  0,
    },
]

# Build lookup dicts from chain definitions
def _build_lookups(chain):
    to_status = {}
    to_next   = {}
    to_offset = {}
    for step in chain:
        name = step["name"]
        if step["status"]:
            to_status[name] = step["status"]
        if step["next"]:
            to_next[name]   = step["next"]
            to_offset[name] = step["offset"]
    return to_status, to_next, to_offset

TODO_TO_STATUS,  TODO_TO_NEXT,  TODO_TO_OFFSET  = _build_lookups(TODO_CHAIN)
TODO_TO_STATUS_C, TODO_TO_NEXT_C, TODO_TO_OFFSET_C = _build_lookups(TODO_CHAIN_CLOSING)

# Maps status → to-do name to check off when that status is set by dragging
STATUS_TO_TODO = {
    "Appointment Set": "📅 Schedule site visit",
    "Estimating":      "📝 Build estimate",
    "Sent":            "📤 Send estimate to customer",
}

# Names used to identify follow-up to-dos (used for deletion)
FOLLOWUP_TODO_NAMES = {
    "📞 Follow-up call #1 — any questions?",
    "🚨 Final decision call — win or move on",
    "📅 Long term follow-up — check back in",
    "💬 Customer replied — check in personally",
    # Review to-dos — created by cron, sent when Jason checks them off.
    # These now ARE the follow-up to-dos — no separate email-placeholder chain.
    "📧 Review & send — Day 3 follow-up",
    "📧 Review & send — Day 7 follow-up",
    "📧 Review & send — Day 14 follow-up",
    "📧 Review & send — Closing repair check-in",
}

# Maps follow-up day → to-do name Jason reviews before sending
FOLLOWUP_REVIEW_TODO_NAMES = {
    3:  "📧 Review & send — Day 3 follow-up",
    7:  "📧 Review & send — Day 7 follow-up",
    14: "📧 Review & send — Day 14 follow-up",
}

# Maps follow-up day → pipeline status to set once the email is confirmed sent
FOLLOWUP_STATUS_MAP = {
    3:  "Sent 1st Follow Up",
    7:  "Sent 2nd Follow Up",
    14: "Sent Final Follow UP",
}

# Pending review markers — logged as comments so the cron doesn't create a
# second review to-do if it runs before Jason has checked the first one off
FOLLOWUP_PENDING_MARKERS = {
    3:  "[OCC-PENDING-F1]",
    7:  "[OCC-PENDING-F2]",
    14: "[OCC-PENDING-F3]",
    2:  "[OCC-PENDING-CR]",  # closing repair 48-hr check-in
}

# ── Follow-up manual call steps ──────────────────────────────────────────────
# The cron (process_send_followups) creates the "Review & send" to-do for
# Day 3 / 7 / 14 directly off the real elapsed time since sending — there is
# no separate placeholder chain anymore. The only thing that dominoes off a
# review to-do being checked off is an optional MANUAL call step (a human
# action, not an email) for Jason to do next. Day 7 has no manual step.
MANUAL_CALL_AFTER_DAY = {
    3:  {"name": "📞 Follow-up call #1 — any questions?", "offset": 7},
    14: {"name": "🚨 Final decision call — win or move on", "offset": 0},
}

# The to-do created when a customer reply is detected mid-chain.
# Replaces whatever follow-up to-do would have fired next — automation stands
# down and hands the conversation back to a human.
CUSTOMER_REPLY_TODO = "💬 Customer replied — check in personally"

# Marker used to detect that automation already paused on this job, so we
# don't keep creating duplicate "customer replied" to-dos on every check.
PAUSE_LOGGED_MARKER = "[OCC-AUTO] Automation paused — customer replied"



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


def has_pending_reply_pause(job_id):
    """
    Quick check: has automation already been paused on this job due to a
    customer reply? Prevents duplicate pause to-dos/comments on repeated
    triggers (webhook + cron both calling check_for_customer_reply_and_pause).
    """
    try:
        resp = jobtread_query({
            "job": {
                "$": {"id": job_id},
                "comments": {
                    "$": {"size": 10, "sortBy": [{"field": "createdAt", "order": "desc"}]},
                    "nodes": {"message": {}}
                }
            }
        })
        comments = (resp.get("job") or {}).get("comments", {}).get("nodes", [])
        return any(PAUSE_LOGGED_MARKER in (c.get("message") or "") for c in comments)
    except Exception as e:
        print(f"  has_pending_reply_pause check failed: {e}")
        return False


def check_for_customer_reply_and_pause(job_id, sent_date=None):
    """
    Shared logic: if the customer has replied (a comment on this job that
    came in via email, or from someone who isn't our automation user) since
    the estimate was sent, stop the canned follow-up chain and hand the
    conversation back to a human.

    Called from two places:
    - process_comment_created: fires immediately when the reply lands
    - process_send_followups: runs as a backup check before any scheduled send

    Returns True if a pause was applied (caller should not proceed with a
    scheduled automated send), False otherwise.
    """
    try:
        job_info = get_job_info(job_id)
        job_type, current_status = get_job_type_and_status(job_info)

        if not job_type or job_type not in AUTOMATION_ENABLED_JOB_TYPES:
            return False
        if current_status in TERMINAL_STATUSES or current_status == LONG_TERM_STATUS:
            return False

        resp = jobtread_query({
            "job": {
                "$": {"id": job_id},
                "comments": {
                    "$": {"size": 30, "sortBy": [{"field": "createdAt", "order": "asc"}]},
                    "nodes": {
                        "message": {}, "createdAt": {}, "isFromEmail": {},
                        "createdByUser": {"id": {}}
                    }
                }
            }
        })
        comments = (resp.get("job") or {}).get("comments", {}).get("nodes", [])

        if sent_date is None:
            sent_date = parse_sent_date(comments)
        if not sent_date:
            return False  # No estimate-sent marker — nothing to pause against

        # Find the precise timestamp of the sent-marker comment so we only
        # treat comments AFTER it as a reply. Using only the date (not the
        # exact moment) would wrongly catch stale comments from earlier in
        # the same day — e.g. a prior test reply, or activity logged before
        # this particular estimate was sent.
        sent_marker_at = None
        for c in comments:
            if "[OCC-AUTO] Estimate sent on" in (c.get("message") or ""):
                sent_marker_at = c.get("createdAt")
                # Keep scanning — if the chain was sent more than once
                # (re-send), use the MOST RECENT sent marker.
        if not sent_marker_at:
            return False  # Marker date parsed but exact comment not found — be safe and skip

        already_paused = any(
            PAUSE_LOGGED_MARKER in (c.get("message") or "") and c.get("createdAt", "") > sent_marker_at
            for c in comments
        )
        if already_paused:
            return True  # Already paused since this send — treat as "don't send", don't repeat

        reply_found = False
        for c in comments:
            if c.get("createdAt", "") <= sent_marker_at:
                continue  # comment predates this estimate send — stale, ignore
            msg = c.get("message") or ""
            if "[OCC-AUTO]" in msg:
                continue  # our own automated comments don't count as a reply
            created_by = (c.get("createdByUser") or {}).get("id")
            if c.get("isFromEmail") or (created_by and created_by not in AUTOMATION_USER_IDS):
                reply_found = True
                break

        if not reply_found:
            return False

        print(f"  Customer reply detected on job {job_id} — pausing automated follow-ups")
        deleted = delete_followup_todos(job_info)
        print(f"  Deleted {deleted} remaining follow-up to-dos")
        create_single_todo(job_id, job_type, CUSTOMER_REPLY_TODO, due_offset=0)

        try:
            from datetime import date as _date
            jobtread_query({
                "createComment": {
                    "$": {
                        "targetType": "job",
                        "targetId": job_id,
                        "message": f"{PAUSE_LOGGED_MARKER} on {_date.today().isoformat()}. "
                                   f"Switched to manual check-in to-do.",
                    },
                    "createdComment": {"id": {}}
                }
            })
        except Exception as ce:
            print(f"  Could not log pause comment: {ce}")

        return True

    except Exception as e:
        import traceback
        print(f"check_for_customer_reply_and_pause error: {e}")
        traceback.print_exc()
        return False


def process_comment_created(payload):
    """
    Fired when any comment is created in JobTread (commentCreated webhook).
    If it lands on a job that's mid follow-up-chain and looks like a genuine
    customer reply, pause the canned automation immediately rather than
    waiting for the next cron run.
    """
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        event = data.get("createdEvent") or {}
        comment = event.get("comment") or {}
        job_id = (comment.get("job") or {}).get("id") or (event.get("job") or {}).get("id")

        if not job_id:
            return  # Comment not attached to a job — nothing to do

        message = comment.get("message") or ""
        if "[OCC-AUTO]" in message:
            return  # Ignore our own automated comments

        check_for_customer_reply_and_pause(job_id)

    except Exception as e:
        import traceback
        print(f"process_comment_created error: {e}")
        traceback.print_exc()



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


# create_followup_todos removed — the cron (process_send_followups) creates
# the first real to-do ("Review & send — Day 3 follow-up") itself once 3 days
# have actually elapsed. No placeholder is created when the estimate is sent.


def create_longterm_todo(job_id, job_type):
    """Create a single 60-day long-term follow-up to-do."""
    from datetime import date, timedelta
    assignee_id = JASON_ID if job_type in JASON_JOB_TYPES else TYLER_ID
    due = next_business_day(date.today() + timedelta(days=60)).isoformat()
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

        if new_status != "pending":
            return  # delivery/open status update — not a fresh send, nothing to do

        print(f"  document-sent: emailDeliveryStatus {prev_status} -> {new_status}")

        # Get document ID from the event
        doc_id = next_state.get("documentId") or (event.get("document") or {}).get("id")
        if not doc_id:
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

        if not job_id:
            return
        if doc_type != "customerOrder":
            return

        print(f"  document-sent: estimate sent on job {job_id}")
        job_info = get_job_info(job_id)
        job_type, current_status = get_job_type_and_status(job_info)

        if not job_type or job_type not in AUTOMATION_ENABLED_JOB_TYPES:
            return

        if current_status in TERMINAL_STATUSES or current_status == LONG_TERM_STATUS:
            return

        # Clear out any stale follow-up to-dos from a prior send. The cron
        # (process_send_followups) will create the real Day 3/7/14 review
        # to-dos itself once that much time has actually elapsed — no
        # placeholder is created here.
        deleted = delete_followup_todos(job_info)
        print(f"  Deleted {deleted} existing follow-up to-dos")

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

FOLLOWUP_SENT_MARKERS = {
    2:  "[OCC-AUTO-CR]",   # closing repair 48-hr check-in
    3:  "[OCC-AUTO-F1]",
    7:  "[OCC-AUTO-F2]",
    14: "[OCC-AUTO-F3]",
}

# ── AI-powered follow-up email writer ─────────────────────────────────────────

FOLLOWUP_EMAIL_SYSTEM_PROMPT = """You write follow-up emails on behalf of Jason Evans, owner of Owners Choice Construction LLC, a residential repair contractor in Greenville/Upstate SC.

JASON'S VOICE — study these real examples carefully:

Example 1 (checking in after no reply):
"Good morning Sandy, Thank you for your feedback. We will make sure we make it right as far as the additional cost go. Would it be ok if I gave you a call later this morning so we can make sure there is clarity around this as we finalize these last few details? Thanks!"

Example 2 (warm close after no deal):
"Sounds good Jeff, Feel free to connect with us on this same email thread if you want, or you can reach us through our website ownerschoicerepairs.com any time as well. We would love the opportunity to work with you if the need arises. Have a great rest of the week!"

Example 3 (casual update on timeline):
"Good morning Kyle, Yes I did and Im sorry I haven't replied yet. Ill see if I can find some photos to send once I get back at my desk later this afternoon or possibly tomorrow depending on how the day goes :) Thanks!"

Example 4 (explaining a technical detail warmly):
"Good afternoon Cathy, I would not estimate there would be any pricing changes and less for some reason the foam insulation product price skyrockets, but I doubt that will happen so I would be pretty confident this price would not change. The two different sizes are for the two different size water piping you have in your crawlspace. Some of it is three-quarter inch copper and/or pecks and some of it is half inch copper. The actual foam is the same thickness, but the hole in the middle is what is different to accommodate the different size piping. Hopefully, that helps clarify what that means. You can just approve the estimate whenever it gets a little closer to when you would like to move forward, and after that, we will contact you about deposit and scheduling :) Thanks. Let me know if you have any other questions or concerns. Have a great day"

Example 5 (project update mid-job):
"Good evening Dominic, Thanks for the information. We did manage to locate the paint specs in the basement storage when we were down there as well. I'm told due to the paint being a flat, you will want 2 coats on anything that gets painted or you risk it not being an even finish. Certainly look around the house and let me know if there are areas you'd rather not touch from a paint standpoint. Alternatively if you don't see something listed that you would like included, let us know and we can make those adjustments. I'll send a version tomorrow morning with everything minus the paint for now and you can keep us updated on how to proceed with the paint later on. Have a good evening and I'll be in touch tomorrow :)"

Example 6 (scheduling answer):
"Good afternoon Lea, We are likely looking into the middle of June at the moment. Once deposits are paid then we can solidify a space on the calendar for the work. So the calendar is always changing. But that being said, the project will likely be done over the course of a week depending on what we find when opening up some of those areas of wood rot."

Example 7 (coordinating with multiple parties):
"Good afternoon Lora, Thank you for the update. That sounds like a sensible plan. I will get this revised proposal back over to Stephanie and Dominic and I'll also connect with my painter to get a rough estimated timeframe for completing everything listed. Some of it will depend on when the approval and deposit comes in but we will work as expeditiously as possible :) I'm also still in works on the window replacements for the failed seals. The glass may be under warranty but the labor to replace the glass is something I'm getting priced out. It's a slow process working with the mfg on this but I'm getting close I think. I'll keep everyone informed on that as well. Thanks!"

VOICE RULES — non-negotiable:
- Always open with time of day: "Good morning", "Good afternoon", or "Good evening" based on time of day provided. Then first name only. No "Hi" or "Hey" or "Dear".
- Short paragraphs, one thought each. 3-6 sentences total for the whole email.
- Use "I" for Jason personally, "we/us" for the company — both can appear in the same message naturally.
- Reference something specific about THEIR project — the actual work scoped, or a detail from the notes. Never generic.
- Do NOT mention or restate the property address in the body — the client already knows what property is being discussed, so naming it back to them reads as generated. Use project/scope details instead to make it feel specific.
- End with something warm and forward-looking: "Have a great day", "I'll be in touch", "Thanks!", etc.
- Contractions always: "I'm", "I'll", "we'd", "don't". Never stiff.
- NO: "I hope this email finds you well", "please don't hesitate", "as per", "moving forward", "circle back", "reach out", "valued customer", "at your earliest convenience", bullet points, numbered lists, exclamation points on every sentence.
- The :) emoji is fine used once, naturally, not forced.
- Never mention that this is an automated or scheduled email.
- Never invent facts about the project that aren't in the context provided.

FORMATTING — non-negotiable:
- The greeting line ("Good afternoon Daryl,") stands alone as its own line, followed by a blank line before the body starts.
- The closing line ("Hope you're having a great week!") stands alone as its own line, with a blank line before it separating it from the body.
- Use a blank line between paragraphs if there is more than one paragraph in the body.
- Structure: greeting line \\n\\n body paragraph(s) \\n\\n closing line. Never run the greeting or closing into the same paragraph as the body text.

OUTPUT: Return ONLY the plain text email body, using real blank lines (\\n\\n) between the greeting, body, and closing as described above. No subject line. No signature (Jason's name and contact info is added automatically). No markdown."""


def generate_followup_email(first_name, days_since=None, job_type=None, address=None,
                            notes=None, cost_group_names=None, day=None):
    """
    Call Claude to write a personalised follow-up email in Jason's voice.
    Falls back to a safe static message if the API call fails.

    days_since: 3 = first follow-up, 7 = second, 14 = final
    cost_group_names: list of repair scope titles from the estimate (may be empty)
    """
    # Accept either days_since= (legacy) or day= (closing repair path)
    days_since = days_since if days_since is not None else day
    from datetime import datetime
    hour = datetime.now().hour
    if hour < 12:
        time_of_day = "morning"
    elif hour < 17:
        time_of_day = "afternoon"
    else:
        time_of_day = "evening"

    followup_context = {
        2:  "This is a single 48-hour check-in for a closing repair estimate sent to a realtor. Tone: brief and practical — just making sure the estimate arrived and asking if they have any questions. Closing repairs are time-sensitive so keep it tight and professional. One paragraph max.",
        3:  "This is the first follow-up, sent 3 days after the estimate. Tone: warm check-in, make sure they got it, invite questions. Light and easy — no pressure.",
        7:  "This is the second follow-up, sent 7 days after the estimate. Tone: still warm but a little more direct. Acknowledge they may be weighing options. Offer to adjust scope or answer questions.",
        14: "This is the third and final follow-up, sent 14 days after the estimate. Tone: genuinely warm close. We'd love the work but completely understand if timing isn't right. Leave the door open for the future without being pushy.",
    }

    scope_text = ""
    if cost_group_names:
        # Strip leading inspection numbers like "3.2 - " to get clean repair names
        clean = [re.sub(r'^[\d\.\s]+[-\u2013]?\s*', '', n).strip() for n in cost_group_names[:8]]
        clean = [n for n in clean if n]
        if clean:
            scope_text = f"Repair scope: {', '.join(clean)}"

    prompt = f"""Write a follow-up email from Jason to this customer.

Customer first name: {first_name}
Time of day: {time_of_day}
Property address (context only — do NOT mention this in the email body): {address or 'not specified'}
Job type: {job_type or 'Home Repair'}
{scope_text}
{f'Project notes: {notes[:500]}' if notes and notes.strip() else ''}

Follow-up context: {followup_context.get(days_since, followup_context[14])}

Write the email now."""

    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 400,
            "system": FOLLOWUP_EMAIL_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}]
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode("utf-8"))
        body = "".join(block.get("text", "") for block in result.get("content", [])).strip()
        if body:
            print(f"  AI email generated for {first_name} (day {days_since}, {len(body)} chars)")
            return body
    except Exception as e:
        print(f"  AI email generation failed: {e} — using fallback")

    # Static fallback — plain, on-brand, never fails
    fallbacks = {
        2:  f"Good morning {first_name},\n\nJust wanted to make sure the closing repair estimate we sent over came through okay. Let me know if you have any questions or need anything adjusted.\n\nThanks!",
        3:  f"Good morning {first_name},\n\nJust wanted to make sure you received the estimate we sent over. Let me know if you have any questions or if there's anything you'd like to go over — happy to help.\n\nThanks!",
        7:  f"Good morning {first_name},\n\nChecking in one more time on the estimate for your project. If anything needs adjusting or you have questions about the scope, just let me know and we can work through it.\n\nHave a great day!",
        14: f"Good morning {first_name},\n\nI wanted to follow up one last time on the estimate we put together for you. We'd love the opportunity to work with you — but if the timing isn't right, no worries at all. Feel free to reach back out whenever the time comes.\n\nTake care!",
    }
    return fallbacks.get(days_since, fallbacks[14])


def get_jobs_needing_followup():
    """
    Fetch all jobs at Sent status that need follow-up emails.
    Uses two-pass approach: first get job IDs, then fetch details per job.
    """
    results = []
    all_sent_job_ids = []

    # Only look at jobs created in the last 90 days
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=90)).isoformat()

    # Pass 1: get jobs with custom fields using pagination (small pages to avoid 413)
    for status_field, job_types in [
        ("Home Repairs Status",    ["Home Repair", "Remodel", "Pre-listing Repair"]),
        ("Closing Repairs Status", ["Closing Repair"]),
    ]:
        page = None
        while True:
            try:
                jobs_input = {"size": 20, "where": ["createdAt", ">=", cutoff]}
                if page:
                    jobs_input["page"] = page
                resp = jobtread_query({
                    "organization": {
                        "$": {"id": JOBTREAD_ORG},
                        "jobs": {
                            "$": jobs_input,
                            "nextPage": {},
                            "nodes": {
                                "id": {}, "name": {},
                                "customFieldValues": {
                                    "$": {"size": 5},
                                    "nodes": {"customField": {"name": {}}, "value": {}}
                                }
                            }
                        }
                    }
                })
                jobs_data = resp.get("organization", {}).get("jobs", {})
                jobs = jobs_data.get("nodes", [])
                next_page = jobs_data.get("nextPage")
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
                    if job["id"] not in all_sent_job_ids:
                        all_sent_job_ids.append(job["id"])
                if not next_page:
                    break
                page = next_page
            except Exception as e:
                print(f"  get_jobs_needing_followup error: {e}")
                break

    print(f"  Found {len(all_sent_job_ids)} jobs at Sent status")

    # Pass 2: fetch full details per job
    for job_id in all_sent_job_ids:
        try:
            resp = jobtread_query({
                "job": {
                    "$": {"id": job_id},
                    "id": {}, "name": {}, "description": {},
                    "location": {"address": {}},
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
                    },
                    "costGroups": {
                        "$": {"size": 30},
                        "nodes": {"name": {}}
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
    label = "Closing repair check-in" if day == 2 else f"Day {day} follow-up"
    try:
        jobtread_query({
            "createComment": {
                "$": {
                    "targetType": "job",
                    "targetId": job_id,
                    "message": f"{marker} {label} email sent on {date.today().isoformat()}.",
                },
                "createdComment": {"id": {}}
            }
        })
    except Exception as e:
        print(f"  Could not log follow-up comment: {e}")


def already_pending_review(comments, day):
    """Check if a review to-do for this day has already been queued."""
    marker = FOLLOWUP_PENDING_MARKERS.get(day, "")
    return marker and any(marker in (c.get("message") or "") for c in (comments or []))


def log_pending_review(job_id, day):
    """Add a comment marking that a review to-do was created for this day."""
    from datetime import date
    marker = FOLLOWUP_PENDING_MARKERS.get(day, "")
    if not marker:
        return
    try:
        jobtread_query({
            "createComment": {
                "$": {
                    "targetType": "job",
                    "targetId": job_id,
                    "message": f"{marker} Review to-do created on {date.today().isoformat()}.",
                },
                "createdComment": {"id": {}}
            }
        })
    except Exception as e:
        print(f"  Could not log pending review comment: {e}")


def create_review_todo(job_id, job_type, todo_name, email_body):
    """Create the review to-do with the AI email in the description field."""
    assignee_id = JASON_ID if job_type in JASON_JOB_TYPES else TYLER_ID
    from datetime import date
    today = next_business_day(date.today()).isoformat()
    # Truncate to 4096 chars (JobTread description limit)
    description = email_body[:4090] if email_body else ""
    try:
        jobtread_query({
            "createTask": {
                "$": {
                    "name": todo_name,
                    "isToDo": True,
                    "targetType": "job",
                    "targetId": job_id,
                    "startDate": today,
                    "endDate": today,
                    "description": description,
                    "assignees": [{"membershipId": assignee_id}],
                }
            }
        })
        print(f"  Review to-do created: '{todo_name}'")
    except Exception as e:
        print(f"  Could not create review to-do '{todo_name}': {e}")


def get_recipient_and_name(job):
    """Extract the pending estimate recipient ID and customer first name from a job dict."""
    docs = (job.get("documents") or {}).get("nodes", [])
    for doc in docs:
        if doc.get("type") == "customerOrder" and doc.get("status") == "pending":
            recipients = (doc.get("documentRecipients") or {}).get("nodes", [])
            if recipients:
                recipient_id = recipients[0]["id"]
                full_name = (recipients[0].get("user") or {}).get("name", "")
                first_name = full_name.split()[0].capitalize() if full_name else "there"
                return recipient_id, first_name
    return None, "there"


def process_send_followups():
    """
    Daily runner — check all Sent jobs and queue follow-up review to-dos on
    Day 3, 7, and 14 for non-closing-repair jobs.  For Closing Repairs, check
    at Day 2 whether the estimate has been viewed; if not, queue a single
    check-in review to-do.

    Emails are NOT sent here — instead a to-do with the AI-drafted body in
    the description field is created for Jason to review.  When Jason checks
    it off, process_task_updated fires and sends the actual email at that point.

    Called via GET /send-followups from cron-job.org once per day.
    """
    from datetime import date
    today = date.today()
    print(f"Running daily follow-up check: {today.isoformat()}")

    jobs = get_jobs_needing_followup()
    print(f"  Found {len(jobs)} jobs at Sent status")

    queued_count = 0
    for job in jobs:
        job_id = job["id"]
        comments = (job.get("comments") or {}).get("nodes", [])
        sent_date = parse_sent_date(comments)

        if not sent_date:
            print(f"  Job {job_id}: no sent date found — skipping")
            continue

        days_since = (today - sent_date).days
        print(f"  Job {job_id}: {days_since} days since estimate sent")

        job_cfvs = {
            cfv["customField"]["name"]: cfv["value"]
            for cfv in (job.get("customFieldValues") or {}).get("nodes", [])
            if cfv.get("customField")
        }
        job_type_label = job_cfvs.get("Job Type", "")

        # ── Closing Repair: single 48-hr check-in if not viewed ──────────────
        if job_type_label == "Closing Repair":
            if days_since != 2:
                continue
            if already_sent_followup(comments, 2) or already_pending_review(comments, 2):
                print(f"  Job {job_id}: closing repair check-in already queued/sent — skipping")
                continue

            # Check documentLastViewedAt on the recipient
            recipient_id, first_name = get_recipient_and_name(job)
            if not recipient_id:
                print(f"  Job {job_id}: no pending estimate recipient — skipping")
                continue

            viewed = False
            try:
                r = jobtread_query({
                    "documentRecipient": {
                        "$": {"id": recipient_id},
                        "documentLastViewedAt": {},
                        "emailDeliveryStatus": {},
                    }
                })
                dr = r.get("documentRecipient") or {}
                viewed = bool(dr.get("documentLastViewedAt"))
                print(f"  Job {job_id}: documentLastViewedAt={dr.get('documentLastViewedAt')} emailStatus={dr.get('emailDeliveryStatus')}")
            except Exception as e:
                print(f"  Job {job_id}: could not check view status: {e}")

            if viewed:
                print(f"  Job {job_id}: estimate already viewed — no check-in needed")
                continue

            # Not viewed — queue a closing repair check-in review to-do
            job_address = (job.get("location") or {}).get("address", "")
            job_notes   = job_cfvs.get("Notes", "") or ""
            email_body = generate_followup_email(
                first_name, day=2, job_type="Closing Repair",
                address=job_address, notes=job_notes, cost_group_names=[]
            )
            create_review_todo(
                job_id, "Closing Repair",
                "📧 Review & send — Closing repair check-in", email_body
            )
            log_pending_review(job_id, 2)
            queued_count += 1
            continue

        # ── Home Repair / Remodel / Pre-listing: Day 3, 7, 14 chain ─────────
        # Day 2 is closing-repair-only — skip it for all other job types
        if days_since == 2:
            continue
        if days_since not in FOLLOWUP_SENT_MARKERS:
            continue

        if already_sent_followup(comments, days_since):
            print(f"  Job {job_id}: Day {days_since} follow-up already sent — skipping")
            continue

        if already_pending_review(comments, days_since):
            print(f"  Job {job_id}: Day {days_since} review to-do already queued — skipping")
            continue

        if check_for_customer_reply_and_pause(job_id, sent_date=sent_date):
            print(f"  Job {job_id}: automation paused on customer reply — skipping")
            continue

        recipient_id, first_name = get_recipient_and_name(job)
        if not recipient_id:
            print(f"  Job {job_id}: no pending estimate recipient — skipping")
            continue

        job_address      = (job.get("location") or {}).get("address", "")
        job_notes        = job_cfvs.get("Notes", "") or ""
        cost_group_names = [
            cg.get("name", "")
            for cg in (job.get("costGroups") or {}).get("nodes", [])
            if cg.get("name")
        ]
        email_body = generate_followup_email(
            first_name, days_since, job_type_label, job_address, job_notes, cost_group_names
        )

        todo_name = FOLLOWUP_REVIEW_TODO_NAMES[days_since]
        create_review_todo(job_id, job_type_label, todo_name, email_body)
        log_pending_review(job_id, days_since)
        queued_count += 1
        print(f"  Job {job_id}: Day {days_since} review to-do queued for {first_name}")

    print(f"Daily follow-up run complete: {queued_count} review to-dos created")
    return queued_count


def send_followup_email_from_todo(task_id, task_name, job_id, job_type):
    """
    Called from process_task_updated when Jason checks off a review to-do.
    Reads the task description (which contains the email body — edited or
    not), sends it via sendDocument, logs it, flips status, and advances
    the follow-up chain domino to the next step.
    """
    # Determine which day this review corresponds to
    day_map = {v: k for k, v in FOLLOWUP_REVIEW_TODO_NAMES.items()}
    day_map["📧 Review & send — Closing repair check-in"] = 2
    days_since = day_map.get(task_name)
    if days_since is None:
        print(f"  send_followup: unrecognised review to-do name '{task_name}' — skipping")
        return

    # Fetch the task to read the description (may have been edited by Jason)
    try:
        task_resp = jobtread_query({
            "task": {
                "$": {"id": task_id},
                "description": {}
            }
        })
        email_body = (task_resp.get("task") or {}).get("description") or ""
    except Exception as e:
        print(f"  send_followup: could not read task description: {e}")
        return

    if not email_body.strip():
        print(f"  send_followup: task description is empty — cannot send")
        return

    # Find the pending estimate recipient on this job
    try:
        job_resp = jobtread_query({
            "job": {
                "$": {"id": job_id},
                "documents": {
                    "$": {"size": 5},
                    "nodes": {
                        "type": {}, "status": {},
                        "documentRecipients": {
                            "$": {"size": 5},
                            "nodes": {"id": {}, "user": {"name": {}}}
                        }
                    }
                },
                "customFieldValues": {
                    "$": {"size": 10},
                    "nodes": {"customField": {"name": {}}, "value": {}}
                }
            }
        })
    except Exception as e:
        print(f"  send_followup: could not fetch job {job_id}: {e}")
        return

    job_data = job_resp.get("job") or {}
    recipient_id, first_name = get_recipient_and_name(job_data)

    if not recipient_id:
        print(f"  send_followup: no pending estimate recipient on job {job_id} — cannot send")
        return

    # Send the email
    try:
        jobtread_query({
            "sendDocument": {
                "$": {
                    "documentRecipientId": recipient_id,
                    "emailMessage": email_body,
                }
            }
        })
        print(f"  send_followup: Day {days_since} follow-up sent to {first_name} on job {job_id}")
    except Exception as e:
        print(f"  send_followup: sendDocument failed: {e}")
        return

    # Log sent marker and email body as a comment
    log_followup_sent(job_id, days_since)
    try:
        jobtread_query({
            "createComment": {
                "$": {
                    "targetType": "job",
                    "targetId": job_id,
                    "message": f"[OCC-AUTO] Day {days_since} follow-up email sent:\n\n{email_body}",
                },
                "createdComment": {"id": {}}
            }
        })
    except Exception as ce:
        print(f"  send_followup: could not log email body comment: {ce}")

    # Flip pipeline status
    cfvs = {
        cfv["customField"]["name"]: cfv["value"]
        for cfv in (job_data.get("customFieldValues") or {}).get("nodes", [])
        if cfv.get("customField")
    }
    job_type_label = cfvs.get("Job Type", job_type or "")
    target_status = FOLLOWUP_STATUS_MAP.get(days_since)
    if target_status and job_type_label:
        current_info = get_job_info(job_id)
        _, current_status = get_job_type_and_status(current_info)
        if (current_status not in TERMINAL_STATUSES and
                current_status != LONG_TERM_STATUS):
            set_job_status(job_id, job_type_label, target_status)

    # After sending, create the next MANUAL step (a human call), if any.
    # Day 2 (closing repair) has no manual step — single check-in only.
    # Day 7 has no manual step either — straight to Day 14's review to-do
    # via the cron. Day 3 → phone call; Day 14 → final decision call.
    manual_step = MANUAL_CALL_AFTER_DAY.get(days_since)
    if manual_step:
        if has_pending_reply_pause(job_id):
            print(f"  send_followup: automation paused — not creating '{manual_step['name']}'")
        else:
            create_single_todo(job_id, job_type_label, manual_step["name"], due_offset=manual_step["offset"])
            print(f"  send_followup: created manual step → '{manual_step['name']}'")


def complete_todo_by_name(job_info, todo_name):
    """Find a to-do by name on a job and mark it complete if not already done."""
    tasks = (job_info.get("tasks") or {}).get("nodes", [])
    for task in tasks:
        if task.get("name") == todo_name and task.get("isToDo") and task.get("progress") != 1:
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
                return True
            except Exception as e:
                print(f"  Could not check off to-do '{todo_name}': {e}")
    return False


def process_task_updated(payload):
    """
    Fired when any task is updated in JobTread.
    If one of our named to-dos is checked off, flip the job status forward.
    Safeguards:
    - Only act on our specific to-do names
    - Only act if job type is in automation scope
    - Only flip status if it would move forward in the pipeline
    - Only flip if not already at or past the target status
    """
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        event = data.get("createdEvent") or {}
        event_data = event.get("data") or {}
        next_state = event_data.get("next") or {}
        prev_state = event_data.get("previous") or {}

        # ── Fast pre-filters (no API call needed) ────────────────────────────
        new_progress = next_state.get("progress")
        old_progress = prev_state.get("progress")
        task_id = (event.get("task") or {}).get("id")
        job_id  = (event.get("job") or {}).get("id")
        org_id  = (event.get("organization") or {}).get("id")
        has_account = bool(event.get("account"))
        changed_by  = (event.get("createdByUser") or {}).get("id", "unknown")

        # 1. Must be a completion event (progress → 1)
        if new_progress != 1 or old_progress == 1:
            return

        # 2. Must have a task ID and job ID
        if not task_id or not job_id:
            return

        # 3. Must belong to our organization
        if org_id and org_id != JOBTREAD_ORG:
            return

        # 4. Must belong to an account
        if not has_account:
            return

        # 5. Must be completed by someone in our automation user set
        if changed_by not in AUTOMATION_USER_IDS:
            return
        # ─────────────────────────────────────────────────────────────────────

        # Payload only includes task ID — look up task name via API
        # (only reaches here if all pre-filters pass)

        # Look up the task to get its name
        try:
            task_resp = jobtread_query({
                "task": {
                    "$": {"id": task_id},
                    "id": {}, "name": {}, "isToDo": {}, "progress": {}
                }
            })
            task_obj  = task_resp.get("task") or {}
            task_name = task_obj.get("name")
            is_todo   = task_obj.get("isToDo")
        except Exception as e:
            print(f"  task-updated: could not fetch task {task_id}: {e}")
            return

        if not task_name or not is_todo:
            return  # Not a to-do or couldn't get name — ignore silently

        print(f"  task-updated: '{task_name}' completed on job {job_id}")

        if not task_name or not job_id:
            print(f"  task-updated: missing task name or job ID — skipping")
            return

        job_info = get_job_info(job_id)
        job_type, current_status = get_job_type_and_status(job_info)

        if not job_type:
            print("  task-updated: could not determine job type — skipping")
            return

        if job_type not in AUTOMATION_ENABLED_JOB_TYPES:
            return

        is_closing = (job_type == "Closing Repair")

        # ── Review to-do checked off → send the email now ─────────────────────
        # Jason reviewed (and possibly edited) the draft in the to-do description.
        # We read whatever is there and send it immediately.
        all_review_names = set(FOLLOWUP_REVIEW_TODO_NAMES.values()) | {
            "📧 Review & send — Closing repair check-in"
        }
        if task_name in all_review_names:
            send_followup_email_from_todo(task_id, task_name, job_id, job_type)
            return

        # Manual call steps ("Follow-up call #1", "Final decision call") are
        # terminal when checked off — they don't create a next to-do. The
        # cron (process_send_followups) independently creates the next
        # "Review & send" to-do once enough real time has elapsed, so there
        # is nothing to advance here.
        if task_name in FOLLOWUP_TODO_NAMES:
            return
        # ─────────────────────────────────────────────────────────────────────

        # Handle "Call customer" — no status flip, just chain the next to-do
        if task_name == "📞 Call customer — introduce & qualify":
            next_name   = (TODO_TO_NEXT_C if is_closing else TODO_TO_NEXT).get(task_name)
            next_offset = (TODO_TO_OFFSET_C if is_closing else TODO_TO_OFFSET).get(task_name, 1)
            if next_name:
                create_single_todo(job_id, job_type, next_name, due_offset=next_offset)
            return

        # For all others, look up the status to flip
        target_status = (TODO_TO_STATUS_C if is_closing else TODO_TO_STATUS).get(task_name)
        if not target_status:
            return  # Not one of our trigger to-dos — ignore silently

        print(f"  task-updated: '{task_name}' completed on job {job_id} → target status '{target_status}'")

        if not current_status:
            print("  task-updated: no current status found — skipping")
            return

        # Only flip if it's a forward move from current status
        if not is_forward_move(current_status, target_status):
            return  # not a forward move — ignore silently

        # Re-fetch current status right before setting to handle simultaneous check-offs
        fresh_info = get_job_info(job_id)
        _, fresh_status = get_job_type_and_status(fresh_info)
        if fresh_status and not is_forward_move(fresh_status, target_status):
            print(f"  task-updated: status already at '{fresh_status}', skipping '{target_status}'")
            return

        set_job_status(job_id, job_type, target_status)

        # ── Domino: create the next to-do in the chain ────────────────────────
        next_name   = (TODO_TO_NEXT_C if is_closing else TODO_TO_NEXT).get(task_name)
        next_offset = (TODO_TO_OFFSET_C if is_closing else TODO_TO_OFFSET).get(task_name, 1)
        if next_name:
            create_single_todo(job_id, job_type, next_name, due_offset=next_offset)
        # ─────────────────────────────────────────────────────────────────────

    except Exception as e:
        import traceback
        print(f"process_task_updated error: {e}")
        traceback.print_exc()


def process_document_updated(payload):
    """
    Fired when any document is updated in JobTread.
    If a customerOrder (estimate) moves from pending → approved, flip job to Closed Won.
    Safeguards:
    - Only act on pending → approved transitions
    - Only act on customerOrder type with includeInBudget = true
    - Skip if job is already Closed Won, Closed Lost, or Long Term Follow Up
    - Skip if job type not in automation scope
    """
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        event = data.get("createdEvent") or {}
        event_data = event.get("data") or {}
        next_state = event_data.get("next") or {}
        prev_state = event_data.get("previous") or {}

        # Only act on pending → approved
        new_status = next_state.get("status")
        old_status = prev_state.get("status")

        if old_status != "pending" or new_status != "approved":
            return  # not a pending→approved transition

        doc_id = (event.get("document") or {}).get("id")
        if not doc_id:
            return

        try:
            doc_resp = jobtread_query({
                "document": {
                    "$": {"id": doc_id},
                    "id": {}, "type": {}, "includeInBudget": {},
                    "job": {"id": {}}
                }
            })
            doc_obj = doc_resp.get("document") or {}
            doc_type = doc_obj.get("type")
            include_in_budget = doc_obj.get("includeInBudget")
            job_id = (doc_obj.get("job") or {}).get("id")
        except Exception as e:
            print(f"  doc-updated: could not fetch document {doc_id}: {e}")
            return

        if not job_id or doc_type != "customerOrder" or include_in_budget is False:
            return

        print(f"  doc-updated: estimate approved on job {job_id} — flipping to Closed Won")

        job_info = get_job_info(job_id)
        job_type, current_status = get_job_type_and_status(job_info)

        if not job_type or job_type not in AUTOMATION_ENABLED_JOB_TYPES:
            return

        if current_status in TERMINAL_STATUSES or current_status == LONG_TERM_STATUS:
            return

        # Flip to Closed Won and clean up follow-up to-dos
        set_job_status(job_id, job_type, "Closed Won")
        deleted = delete_followup_todos(job_info)
        print(f"  doc-updated: Closed Won set, deleted {deleted} follow-up to-dos")

    except Exception as e:
        import traceback
        print(f"process_document_updated error: {e}")
        traceback.print_exc()


def process_job_created(payload):
    """
    Fired when any job is created in JobTread — whether manually, via form, or API.
    Creates the new-lead to-do chain if:
    - Job type is in AUTOMATION_ENABLED_JOB_TYPES
    - Status is "New Lead"
    - No to-dos already exist on the job (prevents duplicates from form submissions)
    """
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        event = data.get("createdEvent") or {}
        job_id = (event.get("job") or {}).get("id")

        if not job_id:
            return

        job_info = get_job_info(job_id)
        job_type, current_status = get_job_type_and_status(job_info)

        if not job_id or job_type not in AUTOMATION_ENABLED_JOB_TYPES:
            return

        if current_status != "New Lead":
            return

        # Brief pause to let any to-dos created by the sales lead tool (route.ts)
        # or another concurrent process propagate before we check for duplicates.
        import time
        time.sleep(3)

        # Re-fetch job info after sleep so we see any to-dos that just landed
        job_info = get_job_info(job_id)

        existing_todos = [
            t for t in (job_info.get("tasks") or {}).get("nodes", [])
            if t.get("isToDo")
        ]
        if existing_todos:
            return

        print(f"  job-created: creating to-do chain for {job_type} on job {job_id}")
        create_new_lead_todos(job_id, job_type)

    except Exception as e:
        import traceback
        print(f"process_job_created error: {e}")
        traceback.print_exc()


def process_job_updated(payload):
    """
    Fired when any job field is updated in JobTread.
    React to status changes: Closed Won/Lost → clean up. Long Term → 60-day to-do.
    """
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        # Payload structure: createdEvent.job.id
        event = data.get("createdEvent") or {}
        job_id = (
            (event.get("job") or {}).get("id") or
            ((event.get("data") or {}).get("next") or {}).get("id") or
            (data.get("data") or {}).get("id")
        )
        if not job_id:
            return

        job_info = get_job_info(job_id)
        job_type, current_status = get_job_type_and_status(job_info)

        if not job_type or not current_status:
            return

        if job_type not in AUTOMATION_ENABLED_JOB_TYPES:
            return

        print(f"  job-updated: job {job_id} | type={job_type} | status={current_status}")

        # Get previous status from payload to check direction
        event_data = (event.get("data") or {})
        prev_custom = (event_data.get("previous") or {}).get("custom") or {}
        next_custom = (event_data.get("next") or {}).get("custom") or {}

        # Find which status field changed IN THIS UPDATE
        # If the status field is not in next_custom, the status didn't change
        # in this update — don't sync to-dos from a drag
        prev_status = None
        status_changed_in_this_update = False
        for field_name in ("Home Repairs Status", "Closing Repairs Status"):
            if field_name in next_custom:
                prev_status = prev_custom.get(field_name)
                status_changed_in_this_update = True
                break

        if current_status in TERMINAL_STATUSES:
            # Clean up all open follow-up to-dos
            deleted = delete_followup_todos(job_info)
            print(f"  {current_status}: deleted {deleted} follow-up to-dos")

        elif current_status == LONG_TERM_STATUS:
            # Remove short-term follow-ups, add 60-day to-do
            deleted = delete_followup_todos(job_info)
            print(f"  Long Term Follow Up: deleted {deleted} follow-up to-dos")
            create_longterm_todo(job_id, job_type)

        # Sync to-do if status moved forward via drag (and we have a mapping for it)
        # Only act if the status actually changed in this update
        if status_changed_in_this_update and prev_status and is_forward_move(prev_status, current_status):
            todo_to_check = STATUS_TO_TODO.get(current_status)
            if todo_to_check:
                complete_todo_by_name(job_info, todo_to_check)

    except Exception as e:
        import traceback
        print(f"process_job_updated error: {e}")
        traceback.print_exc()


def create_job_stub(cfg):
    """
    Create the job record in JobTread with no estimate — account, contact,
    location, job, and files.  Returns job_id.

    To-dos are created by the jobCreated webhook (process_job_created) which
    fires automatically after job creation — do NOT create them here or they
    will be duplicated.
    """
    account_id  = upsert_account_and_contact(cfg)
    location_id = create_location_record(account_id, cfg["location_address"])
    print(f"  Location: {location_id}")
    job_id = create_job_record(location_id, cfg)
    print(f"  Job created: {job_id}")

    # See create_job_full()'s matching comment — same overflow handling
    # applies here since this path also runs create_job_record().
    _, overflow_notes = _split_notes_for_job(cfg.get("notes_text", ""))
    if overflow_notes:
        create_job_daily_log(job_id, overflow_notes)

    attach_files(job_id, cfg.get("file_urls", []))
    return job_id


def create_job_full(cfg):
    """
    Unified job-creation flow used by routes that have the estimate ready
    at creation time (sales-lead-tool endpoints, closing-repair Wufoo).
    1. Find-or-create account (+ contact, primary)
    2. Create location
    3. Create job (PM, status, projected budget)
    4. Cost groups (if estimate present)
    5. Attach files

    To-dos are created by the jobCreated webhook — do NOT create them here.
    """
    account_id  = upsert_account_and_contact(cfg)
    location_id = create_location_record(account_id, cfg["location_address"])
    print(f"  Location: {location_id}")
    job_id = create_job_record(location_id, cfg)
    print(f"  Job created: {job_id}")

    # If the real submission notes were too long for the job-level "Notes"
    # custom field, create_job_record() only wrote a short pointer there —
    # the full text was held back and needs to go somewhere now that we
    # have a job_id. Daily Log, not the customer-facing Notes field (see
    # _split_notes_for_job() / create_job_daily_log() above for why).
    _, overflow_notes = _split_notes_for_job(cfg.get("notes_text", ""))
    if overflow_notes:
        create_job_daily_log(job_id, overflow_notes)

    add_cost_groups(job_id, cfg.get("estimate"))
    attach_files(job_id, cfg.get("file_urls", []))
    return job_id


def process_sales_tool_closing_estimate(body):
    """
    Fired by the sales-lead-tool (route.ts) after it creates a Closing Repair
    job directly in JobTread. The job/account/contact/files already exist —
    this endpoint only runs the AI estimate and attaches cost groups,
    reusing the same logic as the Wufoo closing-repair flow.

    Expects a JSON body: { job_id, client_name, client_phone, client_email,
                            address, notes_text, addendum_url, inspection_url }

    Fire-and-forget from the caller's perspective — route.ts does not wait
    on this; the lead is already saved in JobTread regardless of outcome here.
    """
    try:
        data = json.loads(body)
    except Exception as e:
        print(f"  sales-tool-closing-estimate: invalid JSON body: {e}")
        return

    job_id          = data.get("job_id")
    client_name     = data.get("client_name", "")
    client_phone    = data.get("client_phone", "")
    client_email    = data.get("client_email", "")
    address         = data.get("address", "")
    notes_text      = data.get("notes_text", "")
    addendum_url    = data.get("addendum_url", "")
    inspection_url  = data.get("inspection_url", "")

    if not job_id:
        print("  sales-tool-closing-estimate: missing job_id — aborting")
        return

    if not addendum_url and not inspection_url:
        print(f"  sales-tool-closing-estimate: job {job_id} has no addendum/inspection URL — nothing to estimate")
        return

    print(f"  sales-tool-closing-estimate: starting AI estimate for job {job_id}")

    try:
        addendum_pdf_bytes   = b""
        inspection_pdf_bytes = b""

        # v2: addendum now goes to Claude as native PDF (vision), same as the
        # inspection report — NOT extract_pdf_text. Realtors send addendums
        # as clean typed PDFs, scans, or sometimes not at all (the inspection
        # report alone carries the ask), or as a marked-up inspection report.
        # See CLAUDE.md "repair addendum vision support" for the full finding.
        if addendum_url:
            print("  Downloading repair addendum...")
            addendum_pdf_bytes = download_file(addendum_url)
            print(f"  Addendum: {len(addendum_pdf_bytes)} bytes downloaded (sent to Claude as native PDF)")

        if inspection_url:
            print("  Downloading inspection report...")
            inspection_pdf_bytes = download_file(inspection_url)
            print(f"  Inspection: {len(inspection_pdf_bytes)} bytes downloaded (sent to Claude as native PDF)")

        if not addendum_pdf_bytes and not inspection_pdf_bytes:
            raise Exception("Files provided but neither could be downloaded")

        print("  Calling Claude (v2 — labor+material breakdown)...")
        estimate = v2.call_claude_v2(
            addendum_pdf_bytes, inspection_pdf_bytes,
            client_name, client_phone, client_email, address, notes_text,
            system_prompt=get_system_prompt_v2(), anthropic_api_key=ANTHROPIC_KEY
        )
        # Enforce OCC's 3-hr minimum in-house labor charge per job (Jul 2026
        # pricing Q&A) before computing the total or writing cost groups, so
        # both reflect the enforced floor if the itemized hours fell short.
        estimate = v2.enforce_minimum_labor_hours(estimate)

        # Don't trust Claude's own "total" field here — under the new schema
        # it's not told to apply markup itself, so its self-reported total
        # has no reliable basis. Compute the real billed total the same way
        # add_cost_groups_v2() actually prices each line.
        total = v2.compute_estimate_total(estimate)
        print(f"  Estimate total (computed, not LLM-reported): ${total:,.2f}")

        print("  Resolving material lines against Home Depot catalog...")
        estimate, catalog_stats = v2.resolve_material_lines_with_catalog(estimate, jobtread_query, JOBTREAD_ORG)
        print(f"  Catalog resolution: {catalog_stats}")

        added = v2.add_cost_groups_v2(job_id, estimate, jobtread_query, org_id=JOBTREAD_ORG)
        print(f"  {added} cost groups added to job {job_id}")

    except Exception as e:
        import traceback
        print(f"  AI estimate failed for job {job_id}: {e}")
        traceback.print_exc()
        flag_failed_estimate(job_id, error=str(e))


def update_job_custom_fields(job_id, custom_field_values):
    """Patch custom field values on an already-existing job (e.g. Projected Budget after the fact)."""
    jobtread_query({
        "updateJob": {
            "$": {"id": job_id, "customFieldValues": custom_field_values},
            "updatedJob": {"id": {}}
        }
    })


def process_sales_tool_general_estimate(body):
    """
    Fired by the sales-lead-tool (route.ts) after it creates a Home Repair,
    Remodel, or Pre-listing Repair job directly in JobTread, with photos
    already attached. The job/account/contact/files already exist — this
    endpoint runs the same best-effort AI estimate used by the Wufoo forms
    (call_claude_general) and patches in cost groups + a projected budget.

    Expects a JSON body: { job_id, job_type, client_name, client_phone,
                            client_email, address, notes_text, description,
                            photo_urls: [...] }

    Fire-and-forget from the caller's perspective — route.ts does not wait
    on this; the lead is already saved in JobTread regardless of outcome here.
    """
    try:
        data = json.loads(body)
    except Exception as e:
        print(f"  sales-tool-general-estimate: invalid JSON body: {e}")
        return

    job_id       = data.get("job_id")
    job_type     = data.get("job_type", "Home Repair")
    client_name  = data.get("client_name", "")
    client_phone = data.get("client_phone", "")
    client_email = data.get("client_email", "")
    address      = data.get("address", "")
    notes_text   = data.get("notes_text", "")
    description  = data.get("description", "")
    photo_urls   = data.get("photo_urls") or []

    if not job_id:
        print("  sales-tool-general-estimate: missing job_id — aborting")
        return

    if not description and not photo_urls:
        print(f"  sales-tool-general-estimate: job {job_id} has no description/photos — nothing to estimate")
        return

    form_label_map = {
        "Home Repair":        "home repair",
        "Remodel":            "remodel",
        "Pre-listing Repair": "pre-listing repair",
    }
    form_label = form_label_map.get(job_type, "home repair")

    print(f"  sales-tool-general-estimate: starting AI estimate for job {job_id} ({job_type})")

    try:
        estimate = call_claude_general(
            form_label, client_name, client_phone, client_email,
            address, description, notes_text, image_urls=photo_urls
        )
        estimate, gated_notes, projected = _apply_estimate_gate(estimate, notes_text)

        if estimate:
            total = estimate.get("total", 0) or 0
            print(f"  Estimate total: ${total:,.2f} | consult={estimate.get('needs_consult')}")
            added = add_cost_groups(job_id, estimate)
            print(f"  {added} cost groups added to job {job_id}")
        else:
            print(f"  Needs consult — no cost groups generated for job {job_id}")

        update_fields = {}
        if projected:
            update_fields["Projected Budget"] = projected
        if gated_notes and gated_notes != notes_text:
            # _apply_estimate_gate appended a "needs consult" note — surface it
            # as a comment rather than silently rewriting the job description.
            try:
                jobtread_query({
                    "createComment": {
                        "$": {
                            "targetType": "job",
                            "targetId": job_id,
                            "message": f"[OCC-AUTO] {gated_notes.split(chr(10)+chr(10))[-1]}",
                        },
                        "createdComment": {"id": {}}
                    }
                })
            except Exception as ce:
                print(f"  Could not log consult-needed comment on job {job_id}: {ce}")

        if update_fields:
            try:
                update_job_custom_fields(job_id, update_fields)
                print(f"  Updated job {job_id} custom fields: {update_fields}")
            except Exception as ue:
                print(f"  Projected Budget field update failed (budget still saved): {ue}")

    except Exception as e:
        import traceback
        print(f"  AI estimate failed for job {job_id}: {e}")
        traceback.print_exc()
        flag_failed_estimate(job_id, error=str(e), job_type=job_type)


def flag_failed_estimate(job_id, error="", job_type="Closing Repair"):
    """
    Called when the AI estimate step fails after the job/lead was already
    created successfully. Surfaces the failure directly in JobTread so the
    lead isn't silently lost — a to-do plus a comment with the error detail.
    """
    try:
        create_single_todo(
            job_id, job_type=job_type,
            name="🚨 Estimate generation failed — needs manual review",
            due_offset=0
        )
    except Exception as e:
        print(f"  Could not create failed-estimate to-do on job {job_id}: {e}")

    try:
        jobtread_query({
            "createComment": {
                "$": {
                    "targetType": "job",
                    "targetId": job_id,
                    "message": f"[OCC-AUTO] AI estimate generation failed: {error[:500]}. "
                               f"The lead, contact info, and uploaded files were saved successfully — "
                               f"only the automatic cost estimate needs to be built manually.",
                },
                "createdComment": {"id": {}}
            }
        })
    except Exception as e:
        print(f"  Could not log failed-estimate comment on job {job_id}: {e}")


def create_jobtread_job(client_name, client_phone, client_email, address, notes_text, file_urls, estimate=None):
    """
    Closing-repairs job creation (account name = property address, realtor = contact).
    estimate may be None — job/account/contact/files are created regardless;
    cost groups are added only if/when an estimate is available.
    """
    street = address.split(",")[0].strip() if "," in address else address
    cfg = {
        "account_name":    address,
        "account_type":    "Closing Repair",
        "lead_source":     "Realtor",
        "referred_by":     None,
        "contact_name":    client_name or "Unknown",
        "contact_email":   client_email or "",
        "contact_phone":   client_phone or "",
        "contact_address": address,
        "location_address": address,
        "job_type":        "Closing Repair",
        "status_field":    "Closing Repairs Status",
        "status_value":    "New Lead",
        "pm":              "Emily Peery",
        "projected_budget": None,
        "job_name":        street[:30],   # closing repairs keep the street name
        "notes_text":      notes_text,
        "estimate":        estimate,      # may be None — add_cost_groups handles that
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
    payload_base = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 8000,  # raised from 4000 — avoid truncated/invalid JSON on larger estimates
        "system": get_system_prompt(),
        "messages": [{"role": "user", "content": content}]
    }

    last_error = None
    for attempt in range(1, 4):
        try:
            payload = json.dumps(payload_base).encode("utf-8")
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

            if result.get("stop_reason") == "max_tokens":
                raise ValueError(f"Claude response truncated by max_tokens (attempt {attempt})")

            raw = "".join(block.get("text", "") for block in result.get("content", []))
            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                raise ValueError(f"No JSON in Claude response: {raw[:500]}")
            return json.loads(match.group(0))

        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"  _call_anthropic attempt {attempt} failed: {e}")
            if attempt < 3:
                continue

    raise Exception(f"_call_anthropic failed after 3 attempts. Last error: {last_error}")


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


def _coerce_budget_number(value):
    """Turn a budget value (possibly a string like '$50,000' or '50k') into a
    plain number for JobTread's Projected Budget field. Returns None if nothing
    numeric can be parsed."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value))
    s = str(value).strip().lower().replace(",", "").replace("$", "").replace(" ", "")
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult = 1000
        s = s[:-1]
    try:
        return round(float(s) * mult)
    except ValueError:
        return None


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
        projected = round(float(total))
    return estimate, notes, projected


# ── Form processors ───────────────────────────────────────────────────────────

def process_home_repairs(data, form_name="Home Repair", lead_source_override=None):
    """Home Repairs and GVL Today share the same field layout."""
    def g(k):
        return data.get(k, [""])[0]
    name    = normalize_name(f"{g('Field1')} {g('Field2')}".strip())
    phone   = normalize_phone(g("Field3"))
    email   = g("Field4").strip().lower()
    street  = g("Field5"); city = g("Field7"); state = g("Field8"); zc = g("Field9")
    address = normalize_address(f"{street}, {city}, {state} {zc}".strip(", "))
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

    cfg = {
        "account_name": name, "account_type": "Home Repair", "lead_source": lead_source,
        "referred_by": None, "contact_name": name, "contact_email": email,
        "contact_phone": phone, "contact_address": address, "location_address": address,
        "job_type": "Home Repair", "status_field": "Home Repairs Status",
        "status_value": "New Lead", "pm": "Emily Peery", "projected_budget": None,
        "job_name": None, "notes_text": notes, "file_urls": file_urls, "dedup": True,
    }

    # ── Step 1: Create the job first — lead is captured no matter what ────────
    job_id = create_job_stub(cfg)

    # ── Step 2: Best-effort AI estimate — failures are flagged, not fatal ─────
    try:
        estimate = call_claude_general(f"{form_name.lower()} repair", name, phone, email,
                                       address, work, notes, image_urls=image_urls)
        print(f"  Estimate total: ${estimate.get('total', 0) or 0:,.2f} | consult={estimate.get('needs_consult')}")
        estimate, gated_notes, projected = _apply_estimate_gate(estimate, notes)
        if estimate:
            add_cost_groups(job_id, estimate)
        update_fields = {}
        if projected:
            update_fields["Projected Budget"] = projected
        if gated_notes != notes:
            try:
                jobtread_query({
                    "createComment": {
                        "$": {
                            "targetType": "job", "targetId": job_id,
                            "message": f"[OCC-AUTO] {gated_notes.split(chr(10)+chr(10))[-1]}",
                        },
                        "createdComment": {"id": {}}
                    }
                })
            except Exception as ce:
                print(f"  Could not log consult comment: {ce}")
        if update_fields:
            try:
                update_job_custom_fields(job_id, update_fields)
            except Exception as ue:
                print(f"  Projected Budget field update failed (budget still saved): {ue}")
    except Exception as e:
        import traceback
        print(f"  AI estimate failed for job {job_id}: {e}")
        traceback.print_exc()
        flag_failed_estimate(job_id, error=str(e), job_type="Home Repair")

    return job_id


def process_gvl(data):
    """GVL Today repairs form — same layout as Home Repairs, lead source hardcoded."""
    return process_home_repairs(data, form_name="GVL Today", lead_source_override="GVL Today Ad")


def process_remodel(data):
    def g(k):
        return data.get(k, [""])[0]
    name    = normalize_name(f"{g('Field1')} {g('Field2')}".strip())
    street  = g("Field3"); city = g("Field5"); state = g("Field6"); zc = g("Field7")
    address = normalize_address(f"{street}, {city}, {state} {zc}".strip(", "))
    email   = g("Field9").strip().lower()
    phone   = normalize_phone(g("Field10"))
    how     = g("Field26")
    budget  = g("Field24")
    desc    = g("Field22")

    print(f"New Remodel submission: {name} — {address}")
    lead_source = map_lead_source(how)
    notes = build_general_notes("Remodel", how=how, budget=budget, work=desc)

    cfg = {
        "account_name": name, "account_type": "Remodel", "lead_source": lead_source,
        "referred_by": None, "contact_name": name, "contact_email": email,
        "contact_phone": phone, "contact_address": address, "location_address": address,
        "job_type": "Remodel", "status_field": "Home Repairs Status",
        "status_value": "New Lead", "pm": "Emily Peery", "projected_budget": None,
        "job_name": None, "notes_text": notes, "file_urls": [], "dedup": True,
    }

    # ── Step 1: Create the job first ─────────────────────────────────────────
    job_id = create_job_stub(cfg)

    # ── Step 2: Best-effort AI estimate ──────────────────────────────────────
    try:
        estimate = call_claude_general("remodel", name, phone, email, address, desc, notes)
        print(f"  Estimate total: ${estimate.get('total', 0) or 0:,.2f} | consult={estimate.get('needs_consult')}")
        estimate, gated_notes, projected = _apply_estimate_gate(estimate, notes)
        # Remodels: client's stated budget anchors the projected field over AI total
        if budget:
            projected = _coerce_budget_number(budget)
        if estimate:
            add_cost_groups(job_id, estimate)
        update_fields = {}
        if projected:
            update_fields["Projected Budget"] = projected
        if gated_notes != notes:
            try:
                jobtread_query({
                    "createComment": {
                        "$": {
                            "targetType": "job", "targetId": job_id,
                            "message": f"[OCC-AUTO] {gated_notes.split(chr(10)+chr(10))[-1]}",
                        },
                        "createdComment": {"id": {}}
                    }
                })
            except Exception as ce:
                print(f"  Could not log consult comment: {ce}")
        if update_fields:
            try:
                update_job_custom_fields(job_id, update_fields)
            except Exception as ue:
                print(f"  Projected Budget field update failed (budget still saved): {ue}")
    except Exception as e:
        import traceback
        print(f"  AI estimate failed for job {job_id}: {e}")
        traceback.print_exc()
        flag_failed_estimate(job_id, error=str(e), job_type="Remodel")

    return job_id


def process_prelisting(data):
    def g(k):
        return data.get(k, [""])[0]
    name    = normalize_name(f"{g('Field1')} {g('Field2')}".strip())
    street  = g("Field3"); city = g("Field5"); state = g("Field6"); zc = g("Field7")
    address = normalize_address(f"{street}, {city}, {state} {zc}".strip(", "))
    email   = g("Field9").strip().lower()
    phone   = normalize_phone(g("Field10"))
    how     = g("Field24")
    insp_done = g("Field12")
    insp_url  = g("Field20-url")
    insp_name = g("Field20") or "Inspection Report"
    has_realtor = g("Field14")
    realtor_name = f"{g('Field16')} {g('Field17')}".strip()
    realtor_email = g("Field18")
    other   = g("Field22")

    print(f"New Pre-listing submission: {name} — {address}")

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

    file_urls = [(insp_name, insp_url)] if insp_url else []

    cfg = {
        "account_name": name, "account_type": "Home Repair", "lead_source": lead_source,
        "referred_by": referred_by, "contact_name": name, "contact_email": email,
        "contact_phone": phone, "contact_address": address, "location_address": address,
        "job_type": "Pre-listing Repair", "status_field": "Home Repairs Status",
        "status_value": "New Lead", "pm": "Emily Peery", "projected_budget": None,
        "job_name": None, "notes_text": notes, "file_urls": file_urls, "dedup": True,
    }

    # ── Step 1: Create the job first ─────────────────────────────────────────
    job_id = create_job_stub(cfg)

    # ── Step 2: Best-effort AI estimate ──────────────────────────────────────
    try:
        pdf_text = ""
        if insp_url:
            try:
                pdf_text = "\n".join(t for _, t in extract_pdf_text(download_file(insp_url)))
                print(f"  Inspection report: {len(pdf_text)} chars extracted")
            except Exception as e:
                print(f"  Inspection PDF failed: {e}")

        estimate = call_claude_general("pre-listing repair", name, phone, email, address,
                                       other, notes, pdf_text=pdf_text,
                                       best_effort=(not pdf_text))
        print(f"  Estimate total: ${estimate.get('total', 0) or 0:,.2f} | consult={estimate.get('needs_consult')}")
        estimate, gated_notes, projected = _apply_estimate_gate(estimate, notes)
        if estimate:
            add_cost_groups(job_id, estimate)
        update_fields = {}
        if projected:
            update_fields["Projected Budget"] = projected
        if gated_notes != notes:
            try:
                jobtread_query({
                    "createComment": {
                        "$": {
                            "targetType": "job", "targetId": job_id,
                            "message": f"[OCC-AUTO] {gated_notes.split(chr(10)+chr(10))[-1]}",
                        },
                        "createdComment": {"id": {}}
                    }
                })
            except Exception as ce:
                print(f"  Could not log consult comment: {ce}")
        if update_fields:
            try:
                update_job_custom_fields(job_id, update_fields)
            except Exception as ue:
                # Budget/cost groups already saved — don't flag the whole estimate
                # as failed just because the Projected Budget field write errored.
                print(f"  Projected Budget field update failed (budget still saved): {ue}")
    except Exception as e:
        import traceback
        print(f"  AI estimate failed for job {job_id}: {e}")
        traceback.print_exc()
        flag_failed_estimate(job_id, error=str(e), job_type="Pre-listing Repair")

    return job_id


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
            elif path == "/jobtread-task-updated":
                process_task_updated(body)
            elif path == "/jobtread-document-updated":
                process_document_updated(body)
            elif path == "/jobtread-job-created":
                process_job_created(body)
            elif path == "/jobtread-comment-created":
                process_comment_created(body)
            elif path == "/sales-tool-closing-estimate":
                process_sales_tool_closing_estimate(body)
            elif path == "/sales-tool-general-estimate":
                process_sales_tool_general_estimate(body)
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
            client_name    = normalize_name(f"{first} {last}".strip())
            client_phone   = normalize_phone(get("Field3"))
            client_email   = get("Field4").strip().lower()
            inquiring_party = get("Field122")
            street         = get("Field5")
            city           = get("Field7")
            state          = get("Field8")
            zip_code       = get("Field9")
            address        = normalize_address(f"{street}, {city}, {state} {zip_code}".strip(", "))
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

            # Build file list for JobTread attachment
            # Use Wufoo file URLs directly — JobTread fetches them
            file_urls = [
                ("Inspection Report", inspection_url),
                ("Repair Addendum",   addendum_url),
            ]
            if extra_file_url:
                file_urls.append((extra_file_name or "Additional File", extra_file_url))

            if not inspection_url and not addendum_url:
                print("  No files attached — aborting (nothing to build a lead from)")
                return

            # ── Step 1: Create the job FIRST, with no estimate yet ────────────
            # This guarantees the lead, contact info, and files are captured in
            # JobTread no matter what happens next. The AI estimate is a
            # best-effort enhancement, not a precondition for the lead existing.
            print("  Creating JobTread job...")
            job_id = create_jobtread_job(
                client_name, client_phone, client_email, address,
                notes_text, file_urls, estimate=None
            )
            print(f"  Job created: {job_id}")

            # ── Step 2: Best-effort AI estimate — failures are flagged, not fatal ──
            try:
                addendum_pdf_bytes   = b""
                inspection_pdf_bytes = b""

                # v2: addendum now goes to Claude as native PDF (vision), same
                # as the inspection report — NOT extract_pdf_text. See
                # CLAUDE.md "repair addendum vision support" for why (scans,
                # marked-up inspection reports, or no separate file at all).
                if addendum_url:
                    print("  Downloading repair addendum...")
                    addendum_pdf_bytes = download_file(addendum_url)
                    print(f"  Addendum: {len(addendum_pdf_bytes)} bytes downloaded (sent to Claude as native PDF)")

                if inspection_url:
                    print("  Downloading inspection report...")
                    inspection_pdf_bytes = download_file(inspection_url)
                    print(f"  Inspection: {len(inspection_pdf_bytes)} bytes downloaded (sent to Claude as native PDF)")

                if not addendum_pdf_bytes and not inspection_pdf_bytes:
                    raise Exception("Files attached but neither could be downloaded")

                print("  Calling Claude (v2 — labor+material breakdown)...")
                estimate = v2.call_claude_v2(
                    addendum_pdf_bytes, inspection_pdf_bytes,
                    client_name, client_phone, client_email, address, notes_text,
                    system_prompt=get_system_prompt_v2(), anthropic_api_key=ANTHROPIC_KEY
                )
                estimate = v2.enforce_minimum_labor_hours(estimate)
                total = v2.compute_estimate_total(estimate)
                print(f"  Estimate total (computed, not LLM-reported): ${total:,.2f}")

                print("  Resolving material lines against Home Depot catalog...")
                estimate, catalog_stats = v2.resolve_material_lines_with_catalog(estimate, jobtread_query, JOBTREAD_ORG)
                print(f"  Catalog resolution: {catalog_stats}")

                added = v2.add_cost_groups_v2(job_id, estimate, jobtread_query, org_id=JOBTREAD_ORG)
                print(f"  {added} cost groups added to job {job_id}")

            except Exception as e:
                import traceback
                print(f"  AI estimate failed for job {job_id}: {e}")
                traceback.print_exc()
                flag_failed_estimate(job_id, error=str(e))

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
