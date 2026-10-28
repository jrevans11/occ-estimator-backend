# OCC Preliminary Ballpark Tool — Build Spec

Owners Choice Construction · Prepared Oct 27, 2026 · For the Claude Code build session

This is the hand-off from two days of scoping with Tyler Jarratt (who will run the tool) and Jason Evans. Everything under **Decided** came from Tyler's recorded answers or Jason's written answers and should be built as stated. Anything under **Recommended** is Claude's suggestion and should be confirmed with Jason before building. **Open** items have no answer yet; build them with the default shown and flag them in the output.

---

## 0. Starting the code session

1. Attach both repos to the session:
   - `jrevans11/sales-lead-tool` — Next.js internal tool suite on Vercel (`sales-lead-tool-new.vercel.app`, pages under `/intake`)
   - `jrevans11/occ-estimator-backend` — Python backend on Render (`occ-estimator.onrender.com`)
2. Give the session this file and say: *"Build the Preliminary Ballpark Tool from this spec. Read the whole spec first, then build in the order in section 12. Check with me before anything marked Recommended or Open."*
3. Jason normally edits in the GitHub web editor and Vercel/Render auto-deploy from `main`. In the code session, have Claude work on a branch and open pull requests. Don't push to `main` directly; Jason reviews with GitHub's "Files changed" tab and merges.

---

## 1. What the tool does

After a site visit on a **Renovation** or **Addition** job, Tyler opens the tool page, picks the job, enters a handful of variables, and submits. The tool:

1. Gathers the site-visit material: CompanyCam video transcripts, photos and notes for the job's address, JobTread job comments, Rendr measurements if imported, and Tyler's dictation note from the tool page.
2. Works out the scope and quantities.
3. Builds the **budget** on the JobTread job from the Master Design Build Template, priced with the rules in section 6 and marked up by the bracket in section 7.
4. Writes the **ballpark written summary** (Project Overview, Further Items, Exclusions, price range, margin sentence) as an **internal Daily Log** on the job, formatted like the 00844 (Malm) Preliminary Ballpark, so Tyler can paste it into the ballpark document.
5. **Stops there.** Tyler reviews the budget, creates the Preliminary Ballpark document and sends it himself.

**Out of scope:** the Bathroom Design Build Template (`22PKi9A5jyw4`); writing into the ballpark document itself; cost-driver/value-engineering callouts (dropped); GIS/Zillow property lookup (phase 2).

---

## 2. Users and guards (Decided)

- Tyler runs the tool and reviews every ballpark.
- Job types allowed: **Renovation** and **Addition** only (job custom field Job Type `22PAU2yp8N2U`).
- **Never write to a job that already has budget lines.** Check `job.costGroups` for groups with `document: null` (budget groups). Document-attached groups don't count. If any exist, refuse with a clear message.
- When inputs conflict, **the meeting notes / scope summary win** over earlier walkthrough material.
- Tyler records a **scope summary video** in CompanyCam at the end of each site visit. Treat it as the highest-priority transcript.

---

## 3. Architecture (Decided Oct 27, 2026)

- **Tool page** at `app/intake/ballpark/page.tsx` in the Next.js suite, linked from the **PM menu** (`/intake/pm`). Follow the suite's conventions: pages at `app/intake/[tool]/page.tsx`, API routes at `app/api/[endpoint]/route.ts`. Brand colors: header `#4a6675`, cards `#f5f0e8`, submit `#f48f41`.
- **Job picker:** reuse `/api/subbid/jobs` (server-side Job Type filter, returns lat/long and PM), filtered to Renovation + Addition.
- **Browser → Next.js → Render.** The page posts to a Next.js API route (e.g. `app/api/ballpark/route.ts`), which forwards to Render with the existing **`SCOPE_API_KEY`** attached. The key never reaches the browser. Follow the pattern the other tools already use with that key.
- **Backend:** new module **`ballpark.py`** on Render with **`POST /ballpark`**, which returns **202 immediately** and runs on a background thread (same pattern as `/send-followups`).
  - `app.py` is a plain `http.server` handler, so the only change there is a small, anchored edit: one branch in the POST handler that hands the request to `ballpark.py`. Don't rewrite or reformat `app.py`; full-file rewrites have overwritten Jason's newer commits before.
- **Run tracking:**
  - At the start of a run, write a short `[BALLPARK] started` Daily Log on the job.
  - On success: write the budget, the `[BALLPARK]` summary Daily Log, then a JobTread **to-do for Tyler** ("Review Preliminary Ballpark budget").
  - On failure: Daily Log `[BALLPARK-FAILED]` with the reason, plus a to-do.
  - A Render restart or deploy kills a background thread silently. A "started" log with no to-do means the run died.
- **Double-write protection:** per-job in-flight lock (like the closing-repair re-run), and re-check the "job already has budget groups" guard **immediately before writing**, not only at submit.
- **Page feedback:** after the 202, the page tells Tyler the budget will appear on the job with a to-do when it's done, so he doesn't wait on the page or resubmit.
- Render constraint: Jason will **not** upgrade the plan. Everything must fit in 512 MB RAM / 0.5 CPU.

### Secrets

- JobTread grant key, Anthropic API key, **CompanyCam API key**: all already in the backend. Reuse them.
- `SCOPE_API_KEY`: existing shared key between the Next.js app and the backend.

---

## 4. Inputs

### 4a. Pulled automatically

| Source | What to get | How |
|---|---|---|
| CompanyCam | Video transcripts (walkthroughs and the scope summary), photo descriptions/tags, project notes and pages | Find the project by the job's address (`search_projects`), then list videos and fetch each with `include=transcript` (machine-generated). Transcripts can lag several minutes after upload and sometimes never generate; if any video has no transcript, say so in the output instead of silently skipping it. |
| JobTread | Job comments, Daily Logs (intake notes live there), job custom fields, location files | `job.comments`, `job.dailyLogs`. Skip Daily Logs containing `[OCC-AUTO]`. Job files live on `job.location.files`, not the job. |
| Rendr (via JobTread) | Measurements, when imported | `job.parameters`. Tyler will import Rendr parameters on each job (JobTread job dashboard → Manage RENDR Spaces → Add to Job → **Parameters**; "Add a Prefix" for multiple spaces). Parameters include Paintable / Wall / Ceiling Surface Area, Square Footage, Perimeter, door/window/sink/toilet/tub counts, LF base and wall cabinets, countertop and backsplash SF. As of Oct 26, 2026 no OCC job had imported them, so expect `null` and fall back to tool-page entries. |
| Tool page | Everything in 4b | Form submit |

### 4b. Tool page fields (Decided)

**Job and timing**
- Job (picker; Renovation/Addition only)
- Whole-home renovation or addition? (yes/no). This drives the duration default, the selection rules and the temp-pole prompt.
- Duration dropdown: 10–15 wks · 15–20 · 20–25 · 25–40 · 40 wks–1 yr. Default 15 wks for single-space jobs, 32.5 wks for whole-home or addition. Selecting a range uses its **midpoint**.
- Punch list days
- Rendr scan scope note (what was scanned: whole house or work area)

**Spaces** (repeatable rows). Each space has:
- Type: Kitchen · Primary bath · Hall bath · Powder room · Other room
- **Finish tier per space**: Quality / Premium / Luxury (Decided Oct 27: tier is selected per space, mainly for kitchens and baths)
- Tall-ceiling flag (18 ft+, e.g. foyers), for paint

**Size**
- Floor plan SF (renovated area), addition SF, slab SF, deck SF, heated SF of the house (for exterior paint)
- Room count (for paint and device rules) and which rooms are kitchens (from the spaces list)

**Exterior** (quantities entered; priced at template unit rates)
- Roof squares, siding squares, exterior trim LF, brick facade LF, foundation brick LF, windows (count), exterior doors (count), front door (yes/no), gutters (yes/no), driveway SF, garage doors (single count, double count), porch screening LF, full exterior repaint (yes/no)

**Interior**
- Flooring SF by type: tile, hardwood (new), hardwood (refinish), LVP, carpet
- Cabinetry LF: lowers, uppers, full-height, vanities, built-ins/mudroom lockers
- Closets: primary closet systems (count), small closets with shelf and rod (count)
- Mirrors (count)
- Interior doors (count), pocket doors (count)

**Mechanical**
- Full electrical rewire? (yes/no). Not automatic for renovated areas.
- New HVAC system? tons (number). New ductwork? (yes/no)
- Water heater: none / tank / tankless
- Plumbing fixture moves by type (counts), if not counted from the video
- Crawlspace treatment? (yes/no)

**Notes**
- Dictation / free-text note for details not captured on site

---

## 5. The Master Design Build Template

- Catalog cost group **`22PCTdp32jat`** ("Master Design Build Template"): 23 phase groups, **124 line items**, each with quantity 1 and a real `unitCost`. It works as a unit-rate price book. Read it live at run time (paginate `descendentCostItems` with `size: 100` + `nextPage`); don't hard-code prices.
- The budget the tool writes should mirror the template's phase groups (1000 Preparation and Management Fees … 6200 Completion) and include only the lines in scope.
- **Known problem:** many template lines carry the wrong cost code (e.g. all 1300 Preliminary Construction lines are coded "1000 Preparation and Management Fees"; "6125 Gutters and Downspouts" is coded Rough Structure; "Window installation" is Uncategorized; "General Plumbing" is coded Project Management). Every budget built from it inherits that. **Recommended:** have the tool assign the correct cost code by phase instead of copying the template's (ask Jason; this also matters for his job-costing work).
- Template snapshot (Oct 27, 2026) is in Appendix A.

---

## 6. Pricing rules

All dollar amounts are **cost**. Markup is applied afterward (section 7). Where a rule says "template," read the live template price.

### 6a. Preparation and management (1000 / 1300)

| Line | Rule |
|---|---|
| Construction Management And Service | $900/wk × duration weeks |
| Port a John | $110/mo × (weeks ÷ 4.33), rounded up |
| Dumpster Fees | $500 per 2 wks × (weeks ÷ 2), rounded up |
| Dust Control Materials | $100/wk × weeks |
| On Site Storage | $1,650 per job (1 unit); **2 units ($3,300)** when the job prices over $500K (cost over $390,625) |
| Project Selection Coordination | $1,500 base + $1,000 per tier up: Quality $1,500 · Premium $2,500 · Luxury $3,500. Template line is $125/hr, so 12 / 20 / 28 hrs. **Open:** with tiers per space, use the job's highest tier (default). |
| Permits and Fees | 0.8% of job cost ($800 per $100K, smooth, not stepped), on cost before permits and warranty |
| Interior Design | **Remove** |
| Preconstruction costs | **Remove** |
| Site Work, Grading | **Remove** (priced per job, outside the tool) |
| Temporary Power Pole | $1,800, only when **full electrical rewire** is selected |
| Demolition | $300/day × days. **Open:** no days rule given; the tool estimates days from scope and shows its reasoning |
| New Water Tap and Meter, Tap fee (ReWa), Sewer Line Tap | Template prices, only when the transcripts or notes call for new service (typically additions) |

### 6b. Structure and foundation (2001 / 3100)

| Line | Rule |
|---|---|
| Footing and Foundation | $42/SF × addition SF (template) |
| Pre Slab and Slab | $19.09/SF × slab SF (template) |
| Water Proofing - Foundation | $50/LF (template), when called for |
| Crawlspace Treatment | **$4,500**, only when noted on site / checked |
| Termite Protection | $400 (template), additions |
| Framing Materials | $20/SF × **floor plan SF** (addition / reframed area) |
| Framing Labor | $300/day. **Open:** days estimated from scope |
| Deck Framing Materials / Decking (Trex) | $17.25 / $23.58 per SF × deck SF (template) |
| Interior Concrete structures | **Omit** |

### 6c. Plumbing (3600 / 5600)

| Line | Rule |
|---|---|
| Fixture moves (same location $625; new location in footprint $1,325; tub in footprint $1,525; outside footprint $927 / tub $1,275; water and sewer $572.97 per 10 LF; cut and cap $164.30/fixture; vent thru roof $515.06) | Template prices × counts. Fixture counts come from the video or tool page. **Reference: a typical bathroom has 4 plumbing fixtures.** |
| General Plumbing | **Omit.** Plumbing is priced from the itemized lines only. |
| Water heater | Tank $4,650.41 / tankless $4,897.32 (template), when selected |
| Plumbing Fixtures (5600) | Itemized fixtures named in the scope × tier price (section 6j). No "package" lumps. |
| 5600 Finish Plumbing Installation | $75/hr (template). **Open:** hours rule not given; tool estimates from fixture count and shows it |
| Bathroom Hardware | Per full bath, by that bath's tier: **Quality $300 · Premium $850 · Luxury $2,200**. Powder room: half. (Set = 24 in towel bar, towel ring, toilet paper holder, 2 robe hooks; built from Oct 27, 2026 retail prices. The Luxury number rests on Kallista and Newport Brass, since Waterworks and THG Paris don't publish prices.) |

### 6d. Electrical (3700 / 5700)

Device counts are **estimated by rule** per room, priced at template rates:

| Device | Rule | Template cost |
|---|---|---|
| Receptacles | 1 per wall + 1 per 6 LF of wall (confirmed: 1 per 6 LF is intended) | $25 ea |
| GFCI receptacles | Receptacles in kitchens and bathrooms are GFCI | $75 ea |
| One-way switches | 2 per room | $25 ea |
| Dimmer switches | 1 per room | $75 ea |
| Recessed lights | 4 per room; 8 in a kitchen | $125 ea |
| Surface mount lights | 1 per room | $75 ea |
| Under-cabinet lighting | Kitchen only | $875 per kitchen |
| General Electrical | $9/SF × **floor plan SF**, **only when full rewire** is selected | |
| Electrical Fixtures (5700) | Itemized fixtures named in scope × tier price (6j) | |
| 5730 Electrical Finish Installation | $75/hr. **Open:** hours rule; tool estimates | |
| Whole Home Audio | **Omit** (priced on request) | |

Wall LF per room: use Rendr Perimeter when imported; otherwise estimate from room SF and show the math.

### 6e. HVAC (3800 / 5800)

- New system: **$5,000 per ton** × tons entered on the tool page.
- New ductwork: **$7,500 lump** when selected (not per box).
- Relocation of supply duct $475 / return vent $875; Finish HVAC $450 ea (template), when called for.

### 6f. Exterior (4100–4800, 6100)

| Line | Rule |
|---|---|
| Roofing Specification | $1,100 per square (template) |
| Metal Roof Accents | Omit unless Tyler includes it; then a flagged $0 placeholder |
| Brick facade / foundation brick | $137 / $64.25 per LF (template) |
| Brick chimney | $13,000 (template), when called for |
| Windows | **Tier price per window: vinyl $450 (Quality) · Andersen 100 $1,100 (Premium) · Andersen 400 $2,100 (Luxury).** Use these instead of the template's $600. |
| Window installation | **$400 per window** (window prices exclude install) |
| Exterior doors / installation / hardware | $900 / $1,450 / $250 (template) |
| Front door / hardware | $3,500 / $400 (template) |
| Garage door (incl. motor) | **Single (≈9 × 7.5 ft) $3,500 · double $7,200**; custom features cost more |
| Exterior siding | $157 materials + $500 labor per square (template) |
| Exterior trim | $2.25/SF materials + $18/LF labor (template) |
| Exterior Paint | **$8/SF × heated SF of the house** for a full exterior repaint (covers siding, trim, windows, doors). A 2,500 SF house ≈ $20,000. |
| Porch Screening | **$140/LF** (template line is $0) |
| Exterior Shutters | **Omit** |
| 6125 Gutters and Downspouts | **$2,500 placeholder** when gutters are in scope |
| Driveway and Exterior Concrete paths | $8.95/SF (template) |
| 6100 Landscaping Specifications | $10,000 (template), only when mentioned on site |

### 6g. Interior finishes (4700, 5000, 5900)

| Line | Rule |
|---|---|
| Insulation | $4.50/SF × **floor plan SF** |
| Drywall Hang and Finish + Drywall Materials | ($2.78 + $0.50) × **wall + ceiling surface area** (Rendr, or estimated). Every fully renovated area gets new drywall. |
| Interior Trim Materials | $3.25/SF × **floor plan SF** |
| Interior Trim Labor | $425/day. **Open:** days estimated from scope |
| Interior Trim - Accents | **Omit** (priced on request) |
| Interior doors (pre-hung $275 + install $75 + hardware $75), pocket doors $700 | Template × counts |
| Flooring (same rate every tier) | Hardwood $10.25/SF (+ Hardwood Finishing $3.25/SF where finished on site / refinish) · LVP $6.50 + $2 install per SF · carpet $4.25/SF. Tile floors go through 6h. |
| Interior Paint | **$900 per room, including materials**; **$2,000** for a room with tall ceilings (18 ft+). Partial rooms count as a full room. 5 rooms = $4,500. |
| Paint Materials, Wall treatments | Leave out (included in the per-room price) |
| Glass Shower Door | $2,250 (template) |
| Fireplace / surround | $4,000 / $7,500 (template), same every tier |

### 6h. Tile (3900 / 5300)

- Tile prep and waterproofing: walk-in shower $1,600, tile tub surround $900 (template).
- **Tile labor, lump by area** (use the template's Primary Bath Tile / Hall Bath Tile / Backsplash Tile lines):
  - Large primary bath **$6,500**
  - Hall bath with tile tub surround **$3,500**
  - Kitchen backsplash **$1,650**
  - Smaller tile areas (bath floor only, shower only, laundry, etc.) **$1,200 per space** (Ceramic Tile Installation line)
- **Tile material allowance**, by the space's tier, middle of range: **Quality $7.50/SF · Premium $17.50/SF · Luxury $50/SF**.
  - **Open:** the template has **no tile material line**. Recommended: add a "Tile Materials (allowance)" cost item under 5300 Tile.

### 6i. Cabinetry and storage (5400)

- **Only custom cabinetry is budgeted at the ballpark stage**, same rate every tier:
  - Lowers $625/LF, uppers $625/LF, full-height cabinets **$1,250/LF**
  - Vanities **$625/LF**
  - Built-ins and mudroom lockers (double height) **$1,100/LF**
  - Cabinet installation $65/LF and Cabinet Painting (custom only) $75/LF (template)
- Semi Custom Cabinetry: not used at this stage.
- Custom Add ons: leave out.
- Countertop Materials: tier price per SF, middle of range: **Quality $92.50 · Premium $142.50 · Luxury $237.50** (replaces the template's $95). **Open:** whether that's installed or material only; default to installed.
- Cabinet and Room Hardware: $450 (template), same every tier.
- **Closet Systems:** primary closet system **$4,500 per closet** (his-and-hers = 2 × $4,500); small closet with shelf and rod **$350**.
- **Mirrors: $700 per mirror.** The template has no mirror line; add one (Recommended: under 5400 or 5600).

### 6j. Appliances, plumbing fixtures, lighting (brand-based allowances)

**Decided:**
- No "package" lumps. The tool prices the **itemized items named in the scope** (e.g. fridge, range, dishwasher, microwave, hood; sink, faucet, toilet, shower valve/trim, tub filler; pendants, sconces, chandeliers) and sums them. This replaces the template's $16,000 Appliances lump.
- If a brand is mentioned on site (e.g. KitchenAid, Baldwin, Delta), look up that brand's typical **normal retail** price for the item.
- If no brand is mentioned, use the **average of the tier's brands** for that item.
- Rough is fine for a ballpark.

**Tier brands (Tyler):**

| Category | Quality | Premium | Luxury |
|---|---|---|---|
| Plumbing fixtures | Delta, Moen, Kohler | Brizo, California Faucets, Hansgrohe | Waterworks, Newport Brass, Kallista, THG Paris |
| Appliances | Bosch, GE Profile, KitchenAid | Café, JennAir, Thermador | Sub-Zero, Wolf, Miele, Gaggenau |
| Lighting | Kichler, Hinkley, Progress Lighting | Visual Comfort Studio, Hudson Valley Lighting, Hubbardton Forge | Visual Comfort Signature, Urban Electric Co., Apparatus |
| Countertops | Quartz (MSI Q Premium, Silestone), select granite | Cambria, premium quartzite, select marble, premium granite | Exotic quartzite, premium marble, high-end natural stone, specialty porcelain |
| Tile | Daltile, Marazzi, Emser, Florida Tile | Bedrosians, Zia Tile | Ann Sacks, Artistic Tile, Fireclay Tile, New Ravenna |
| Windows | Vinyl | Andersen 100 Series | Andersen 400 Series |

Categories with no brands (cabinetry, flooring, hardware, doors, closets) use the default numbers above.

**Recommended approach (confirm with Jason):** build a cached **tier price table** (item × tier → average retail of the tier's brands) once, using the Claude API's web search, saved in the repo as JSON/CSV for Tyler to review. Only do a live web lookup when a specific off-list brand is mentioned. This keeps run time and cost down and gives repeatable numbers between runs. Record each looked-up price and its source in the internal NOTES line.

- Appliance installation $75/hr (template). **Open:** hours rule; tool estimates.

### 6k. Completion (6200)

- Punch list to completion: $1,800/day × punch list days (tool page).
- Professional Cleaning: $600 (template).
- **Warranty and Quality Control: 1% of job cost, minimum $1,200.**

---

## 7. Markup, range and margin (Decided)

**Markup bracket — picked by total job cost** (switched from price on Oct 27 to remove the gaps):

| Job cost | Markup | Margin | ≈ Price line |
|---|---|---|---|
| under $390,625 | 28% | 21.9% | under $500K |
| $390,625 – $609,756 | 25% | 20.0% | $500K – $750K |
| over $609,756 | 23% | 18.7% | over $750K |

- One rate applies to the whole job: `unitPrice = unitCost × (1 + markup)`.
- **No price floor** at the bracket edges. A slightly bigger job may price lower; Tyler reviews.
- Design-build is cost-plus. Allowances are stated to clients at **cost**.

**Price range** for the summary:
- Low = total price × 0.98; high = total price × 1.10.
- Round to the **nearest $10K** if the total price is under $250K, otherwise the **nearest $25K**.
- Display like Malm: "$950K – $1.05M".
- Worked examples: $186,400 → $180K – $210K; $412,750 → $400K – $450K; $958,815 (Malm) → $950K – $1.05M.

**Footer margin sentence:** keep it; fill in the bracket's margin (21.9% / 20.0% / 18.7%).

---

## 8. Output

### 8a. The budget (JobTread)

- One top-level cost group per template phase that has lines in scope, using template names and order.
- Each cost item: template name, the correct cost code and type, quantity, unitCost, unitPrice (marked up).
- Allowance lines (fixtures, appliances, lighting, countertops, tile material, bath hardware) carry the allowance in the item name or description, e.g. "Plumbing Fixtures — Premium allowance (8 fixtures)", so Tyler can see what was allocated.
- Every phase group gets a **$0 "NOTES (internal — not shown on client documents)"** line item (`showDescription: false`, cost code Internal Use `22PETCRMQJUW`) holding the quantity assumptions, the rule used, the source (which video, Rendr or tool page) and a confidence flag. OCC's standing rule: never delete these.
- Anything the tool couldn't price (an Open rule, a missing input) goes in as a flagged **$0 placeholder** with the reason in NOTES. Never silently drop it.

### 8b. The written summary (JobTread Daily Log)

- Tag the log `[BALLPARK]` so it can be found later (`organization.dailyLogs` accepts `where: ["notes", "like", "%[BALLPARK]%"]`).
- Format it like the 00844 (Malm) Preliminary Ballpark: **Project Overview**, **Further Items Included**, **Exclusions**, plus the **price range** and the **margin sentence**.
  - Malm's ballpark document is `22PgNkQEDHEC` on job `22PfPqQ6hwYk`. The Overview / Further Items / Exclusions text is that document's `description`; the About / FAQ / billing text is its `footer`. Read both as the style and default-text reference.
- **Further Items and Exclusions:** default to Malm's standard text, edited per job.
- **Tier language:** Open. Default: the tool drafts one line per category per tier from the brand table; Tyler edits.
- Add an internal review section at the end listing: Open-rule placeholders, missing transcripts, inputs that conflicted and how they were resolved, and every web-priced item with its source.
- **Ballpark rule (Jason, Sept 2026):** in client-facing text, **no approximate SF/LF or rough dimensions** ("approx 481 SF"); describe extent qualitatively. Product specs and allowance rates are fine.

---

## 9. JobTread API notes (verified at OCC)

- Org ID `22P9ppHePJKP`. Cost types: Labor `22P9ppJUAHYN`, Materials `22P9ppJUAHYP`, Subcontractor `22P9ppJUAHYQ`, Other `22P9ppJUAHYR`.
- `createCostGroup` accepts a `lineItems` array, including **nested** `{"_type":"costGroup", ...}` entries, so a whole phase (or the whole budget) can be created in one call. Item entries: `{"_type":"costItem", name, costCodeId, costTypeId, quantity, unitCost, unitPrice, description, showDescription}`. The discriminators are `"costItem"` / `"costGroup"`.
- `createCostGroup` / `updateCostGroup` return no `id`. Send an empty selection `{}` and re-query `job.costGroups`.
- **One mutation per request.** Aliased/batched mutations silently drop items or throw 500s.
- `updateCostGroup`'s `lineItems` **replaces** the whole item list. Any item left out is deleted.
- `createCostItem` requires `costCodeId`.
- Paginate everything (`size` max 100, follow `nextPage`). Jobs can have 60+ cost groups.
- `job.costGroups` returns budget groups (`document: null`) **and** each estimate document's groups. Filter client-side; `where` on `document` is rejected.
- Group totals: `descendentCostItems` with aliased sums `{"costSum": {"_":"sum","$":"cost"}, "priceSum": {"_":"sum","$":"price"}}`. Large selections can hit "Request Entity Too Large"; query per group if so.
- Cost group `description` max 4,096 characters.
- Job files live on `job.location.files`; CompanyCam videos are mirrored there as "CompanyCam <id>" files.
- Daily Logs: create on the job; read back with `job.dailyLogs { date, notes, createdAt }`.
- To-dos: `createTask { targetType: "job", targetId, isToDo: true, name, startDate, endDate, assignedMembershipIds, notify }`.
- JobTread's help articles are queryable: `{"tutorials": {"$": {"search": "..."}, "id":{}, "name":{}}}`.

---

## 10. Testing (Decided)

- **Pass = tool budget total within 10%** of a trusted budget.
- Test cases:
  - Malm (00844): job `22PfPqQ6hwYk`; budget cost $779,524, price $958,815 (built at 23%, 18.7% margin). Has CompanyCam videos with transcripts; **no** Rendr parameters imported.
  - OCC will pull together past Preliminary Ballparks once the tool is built enough to test.
- Build a **dry-run mode** that computes everything and writes the budget and summary to a file / JSON response **without touching JobTread**. Test against Malm and the past ballparks in dry-run first.
- Use a test job for live writes (OCC has used job 00886 as a test job).

---

## 11. Still open

| # | Item | Default until answered |
|---|---|---|
| 51 | Verify all 124 template prices before testing, or alongside testing? | Test on today's template prices; flag any line the test shows is off |
| — | Days/hours rules: Demolition, Framing Labor, Interior Trim Labor (days); finish plumbing, finish electrical, appliance installation (hours) | Tool estimates from scope and shows its reasoning in NOTES |
| — | Selection coordination with mixed tiers | Use the job's highest tier |
| — | Countertop tier prices installed or material only | Installed |
| — | No tile-material or mirror line in the template | Add the lines (confirm with Jason first) |
| — | Correct cost codes vs. template's miscoded ones | Ask Jason |
| — | Client-facing tier language | Tool drafts from the brand table |

---

## 12. Suggested build order

1. **Read-only first.** Template loader (live, paginated) and a pricing engine that takes a plain JSON scope and returns priced lines, markup and range. Unit-test it with the worked examples in section 7 and the rules in section 6.
2. **Input gathering.** CompanyCam (by address), JobTread comments and Daily Logs, Rendr parameters. Return one assembled context object.
3. **Scope extraction.** Claude call that turns the context + tool-page fields into the scope JSON the engine expects (spaces, counts, quantities, brands mentioned), with a source for each number.
4. **Dry run** end-to-end on Malm. Compare with its real budget.
5. **Tool page** (`/intake/ballpark`, PM menu) + Next.js API route forwarding with `SCOPE_API_KEY` + `ballpark.py` with `POST /ballpark` (202 + background thread), wired into `app.py` with one anchored edit.
6. **JobTread writes:** `[BALLPARK] started` log, budget groups and items, `[BALLPARK]` summary Daily Log, review to-do for Tyler. Guards: per-job in-flight lock; refuse if budget groups exist, re-checked right before writing.
7. Tier price table for brand lookups.
8. Test against past ballparks; tune.

---

## Appendix A — Master Design Build Template snapshot (Oct 27, 2026)

Read live at run time; this is for reference. `unitCost` null means blank in the template. "Code" is the cost code currently on the line (several are wrong; see section 5).

| Phase group | Line | ID | Unit | Cost | Type | Code |
|---|---|---|---|---|---|---|
| 1000 Prep & Mgmt | Permits and Fees | 22PCTdp37bGf | — | 800 | Other | 1000 Prep & Mgmt |
| 1000 Prep & Mgmt | Construction Management And Service | 22PCTdp37bGg | Weeks | 900 | Labor | 1000 Prep & Mgmt |
| 1000 Prep & Mgmt | Port a John | 22PCTdp37bGh | Months | 110 | Sub | 1000 Prep & Mgmt |
| 1000 Prep & Mgmt | Project Selection Coordination | 22PCTdp37bGi | Hours | 125 | Labor | 1000 Prep & Mgmt |
| 1000 Prep & Mgmt | On Site Storage | 22PCTdp37bGj | Unit | 1650 | Labor | 1000 Prep & Mgmt |
| 1000 Prep & Mgmt | Interior Design | 22PCTdp37bGk | Hours | 125 | Labor | 1000 Prep & Mgmt |
| 1000 Prep & Mgmt | Preconstruction costs | 22PCTdp37bGm | — | 85 | Labor | 1000 Prep & Mgmt |
| 1300 Preliminary | Site Work | 22PCTdp37bGp | — | 0 | Sub | 1000 Prep & Mgmt |
| 1300 Preliminary | Grading | 22PCTdp37bGq | — | 0 | Sub | 1000 Prep & Mgmt |
| 1300 Preliminary | Dumpster Fees | 22PCTdp37bGr | 2 Weeks | 500 | Other | 1000 Prep & Mgmt |
| 1300 Preliminary | Dust Control Materials | 22PCTdp37bGs | Weeks | 100 | Materials | 1000 Prep & Mgmt |
| 1300 Preliminary | Demolition | 22PCTdp37bGt | Days | 300 | Sub | 1000 Prep & Mgmt |
| 1300 Preliminary | Temporary Power Pole | 22PCTdp37bGu | — | 0 | Sub | 1000 Prep & Mgmt |
| 1300 Preliminary | New Water Tap and Meter | 22PCTdp37bGv | Per Tap | 3500 | Sub | 1000 Prep & Mgmt |
| 1300 Preliminary | Tap fee to be paid directly to ReWa | 22PCTdp37bGw | — | 2750 | Other | 1000 Prep & Mgmt |
| 1300 Preliminary | Sewer Line Tap | 22PCTdp37bGx | — | 9560 | Sub | 1000 Prep & Mgmt |
| 2001 Excavation & Foundation | Footing and Foundation | 22PCTdp37bGz | SF | 42 | Sub | Excavation and Foundation |
| 2001 Excavation & Foundation | Pre Slab and Slab | 22PCTdp37bH2 | SF | 19.09 | Sub | Excavation and Foundation |
| 2001 Excavation & Foundation | Water Proofing - Foundation | 22PCTdp37bH3 | LF | 50 | Sub | Excavation and Foundation |
| 2001 Excavation & Foundation | Crawlspace Treatment | 22PCTdp37bH4 | — | 0 | Sub | Excavation and Foundation |
| 2001 Excavation & Foundation | Termite Protection | 22PCTdp37bH5 | Each | 400 | Sub | Excavation and Foundation |
| 3100 Rough Structure | Framing Labor | 22PCTdp37bH7 | Days | 300 | Sub | Rough Structure |
| 3100 Rough Structure | Deck Framing Materials | 22PCTdp37bH8 | SF | 17.25 | Materials | Rough Structure |
| 3100 Rough Structure | Decking Materials (Trex Enhanced Naturals) | 22PCTdp37bH9 | SF | 23.58 | Materials | Rough Structure |
| 3100 Rough Structure | Framing Materials | 22PCTdp37bHA | SF | 20 | Materials | Rough Structure |
| 3100 Rough Structure | Interior Concrete structures | 22PCTdp37bHB | — | 0 | Sub | Rough Structure |
| 3600 Rough Plumbing | Plumbing Fixture swap out same location | 22PCTdp37bHE | Each | 625 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | New Fixture Location within same footprint | 22PCTdp37bHF | Each | 1325 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | New Fixture location in same footprint- Tub | 22PCTdp37bHG | Each | 1525 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | New Vent thru Roof | 22PCTdp37bHH | Each | 515.06 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | New Fixture Location outside footprint (requires Water and Sewer add-on) | 22PCTdp37bHJ | Each | 927 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | New Tub Location outside footprint (requires Water and Sewer add-on) | 22PCTdp37bHK | Each | 1275 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | Water and Sewer installation (per 10 LF) | 22PCTdp37bHL | 10 LF | 572.97 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | Cut and Cap drain and water prior to demo (per fixture) | 22PCTdp37bHM | Each | 164.30 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | Tanked Water Heater System | 22PCTdp37bHN | Each | 4650.41 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | Tankless Water Heater System | 22PCTdp37bHP | Each | 4897.32 | Sub | Rough Plumbing |
| 3600 Rough Plumbing | General Plumbing | 22PGSq65aGiZ | — | null | Sub | Project Management |
| 3700 Rough Electrical | Pop Up Countertop Receptacle | 22PCTdp37yBh | Each | 275 | Materials | Rough Electrical |
| 3700 Rough Electrical | USB Outlets | 22PCTdp37yBi | — | 75 | Labor | Rough Electrical |
| 3700 Rough Electrical | Recessed Lighting | 22PCTdp37yBj | — | 125 | Labor | Rough Electrical |
| 3700 Rough Electrical | Surface Mount Light | 22PCTdp37yBk | Each | 75 | Materials | Rough Electrical |
| 3700 Rough Electrical | Receptacle | 22PCTdp37yBm | Each | 25 | Sub | Rough Electrical |
| 3700 Rough Electrical | One Way Switches | 22PCTdp37yBn | Each | 25 | Sub | Rough Electrical |
| 3700 Rough Electrical | Recessed Fan/Light Combo | 22PCTdp37yBp | Each | 450 | Sub | Rough Electrical |
| 3700 Rough Electrical | GFCI Receptacle | 22PCTdp37yBq | Each | 75 | Sub | Rough Electrical |
| 3700 Rough Electrical | Under Cabinet Lighting | 22PCTdp37yBr | Each | 875 | Sub | Rough Electrical |
| 3700 Rough Electrical | Dimmer Switches | 22PCTdp37yBs | Each | 75 | Sub | Rough Electrical |
| 3700 Rough Electrical | 3 Way Switches | 22PCTdp37yBt | Each | 35 | Sub | Rough Electrical |
| 3700 Rough Electrical | General Electrical | 22PCTdp37yBu | SF | 9 | Labor | Rough Electrical |
| 3700 Rough Electrical | Whole Home Audio System | 22PCgLYKTsq7 | Each | null | Labor | Rough Electrical |
| 3800 Rough HVAC | New System and Duct work | 22PCTdp37yBw | Each | 0 | Sub | Rough HVAC |
| 3800 Rough HVAC | Relocation of Supply Duct | 22PCTdp37yBx | Each | 475 | Sub | Rough HVAC |
| 3800 Rough HVAC | Relocation of Return Vent | 22PCTdp37yBy | Each | 875 | Sub | Rough HVAC |
| 3900 Tile Prep | Tile Prep and Waterproofing - walk in shower | 22PCTdp37yC2 | Each | 1600 | Sub | Tile Prep/Waterproofing |
| 3900 Tile Prep | Tile Prep and Water Proofing- tile surround | 22PCTdp37yC3 | Each | 900 | Sub | Tile Prep/Waterproofing |
| 4100 Roofing | Roofing Specification | 22PCTdp37yC5 | Squares | 1100 | Sub | Roofing |
| 4100 Roofing | Metal Roof Accents | 22PCTdp37yC6 | — | 0 | Sub | Roofing |
| 4200 Masonry | Brick Chimney | 22PCTdp37yC8 | Each | 13000 | Sub | Masonry |
| 4200 Masonry | Fireplace Surround | 22PCTdp37yCA | Unit | 7500 | Labor | Masonry |
| 4200 Masonry | Fireplace | 22PCTdp37yCB | Each | 4000 | Materials | Masonry |
| 4200 Masonry | Foundation Wall- Brick Facade | 22PCTdp37yCC | LF | 64.25 | Sub | Masonry |
| 4200 Masonry | Brick Facade - entire wall | 22PCTdp37yCD | LF | 137 | Sub | Masonry |
| 4500 Windows & Doors | Windows | 22PCTdp37yCF | — | 600 | Labor | Windows and Doors |
| 4500 Windows & Doors | Exterior Doors | 22PCTdp37yCG | — | 900 | Labor | Windows and Doors |
| 4500 Windows & Doors | Exterior Door Hardware | 22PCTdp37yCH | Each | 250 | Labor | Windows and Doors |
| 4500 Windows & Doors | Exterior Door Installation | 22PCTdp37yCJ | Each | 1450 | Sub | Windows and Doors |
| 4500 Windows & Doors | Front Door | 22PCTdp37yCK | Unit | 3500 | Labor | Windows and Doors |
| 4500 Windows & Doors | Front Door Hardware | 22PCTdp37yCL | Unit | 400 | Labor | Windows and Doors |
| 4500 Windows & Doors | Interior Doors - Pre-hung | 22PCTdp37yCM | Each | 275 | Labor | Windows and Doors |
| 4500 Windows & Doors | Door Hardware - Interior | 22PCTdp37yCN | Each | 75 | Labor | Windows and Doors |
| 4500 Windows & Doors | Pocket Doors | 22PCTdp37yCP | Each | 700 | Labor | Windows and Doors |
| 4500 Windows & Doors | Glass Shower Door | 22PCTdp37yCQ | Each | 2250 | Labor | Windows and Doors |
| 4500 Windows & Doors | Garage door | 22PCTdp37yCR | Each | 0 | Sub | Windows and Doors |
| 4500 Windows & Doors | Interior Door Installation | 22PCTdp37yCS | Each | 75 | Sub | Windows and Doors |
| 4500 Windows & Doors | Window installation | 22PGSqSUDMCY | Each | null | Labor | Uncategorized |
| 4700 Insulation | Insulation | 22PCTdp37yCU | SF | 4.50 | Sub | Insulation |
| 4800 Exterior Cladding | Exterior Siding Labor | 22PCTdp37yCW | Squares | 500 | Sub | Exterior Cladding |
| 4800 Exterior Cladding | Exterior Siding Materials | 22PCTdp37yCX | Squares | 157 | Materials | Exterior Cladding |
| 4800 Exterior Cladding | Exterior Shutters | 22PCTdp37yCY | — | 0 | Materials | Exterior Cladding |
| 4800 Exterior Cladding | Exterior Trim Materials | 22PCTdp37yCZ | SF | 2.25 | Materials | Exterior Cladding |
| 4800 Exterior Cladding | Exterior Trim Labor | 22PCTdp37yCa | LF | 18 | Sub | Exterior Cladding |
| 4800 Exterior Cladding | Exterior Paint | 22PCTdp37yCb | Unit | 0 | Sub | Exterior Cladding |
| 4800 Exterior Cladding | Porch Screening | 22PCTdp37yCc | Unit | 0 | Labor | Exterior Cladding |
| 5000 Interior Finishing | Drywall Hang and Finish | 22PCTdp37yCe | SF | 2.78 | Sub | Interior Finishing |
| 5000 Interior Finishing | Drywall Materials | 22PCTdp37yCf | SF | 0.50 | Materials | Interior Finishing |
| 5000 Interior Finishing | Hardwood Flooring | 22PCTdp37yCg | SF | 10.25 | Sub | Interior Finishing |
| 5000 Interior Finishing | LVP Flooring Installation | 22PCTdp37yCh | SF | 2 | Materials | Interior Finishing |
| 5000 Interior Finishing | Carpets | 22PCTdp37yCi | SF | 4.25 | Labor | Interior Finishing |
| 5000 Interior Finishing | LVP Flooring | 22PCTdp37yCj | SF | 6.50 | Labor | Interior Finishing |
| 5000 Interior Finishing | Hardwood Finishing | 22PCTdp37yCk | SF | 3.25 | Sub | Interior Finishing |
| 5000 Interior Finishing | Interior Trim Materials | 22PCTdp37yCm | SF | 3.25 | Labor | Interior Finishing |
| 5000 Interior Finishing | Interior Trim - Accents | 22PCTdp37yCn | — | 0 | Materials | Interior Finishing |
| 5000 Interior Finishing | Interior Trim Labor | 22PCTdp37yCp | Days | 425 | Sub | Interior Finishing |
| 5300 Tile | Primary Bath Tile | 22PCTdp37yCr | Unit | 0 | Labor | Tile |
| 5300 Tile | Hall Bath Tile | 22PCTdp37yCs | — | 0 | Labor | Tile |
| 5300 Tile | Backsplash Tile | 22PCTdp37yCt | Unit | 0 | Labor | Tile |
| 5300 Tile | Ceramic Tile Installation | 22PCTdp37yCu | Unit | 0 | Sub | Tile |
| 5400 Cabinetry & Storage | Cabinet Painting- Custom Cabinets ONLY | 22PCTdp37yCw | LF | 75 | Sub | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Custom Kitchen Cabinetry | 22PCTdp37yCx | LF | 625 | Sub | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Custom Add ons | 22PCTdp37yCy | Unit | 0 | Labor | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Custom Bathroom Vanities | 22PCTdp37yCz | LF | 625 | Sub | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Countertop Materials | 22PCTdp37yD3 | SF | 95 | Sub | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Cabinet and Room Hardware | 22PCTdp37yD4 | Each | 450 | Labor | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Cabinet Installation Costs | 22PCTdp37yD5 | LF | 65 | Sub | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Custom Built Ins and Other Cabinetry | 22PCTdp37yD6 | LF | 0 | Labor | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Closet Systems | 22PCTdp37yD7 | Unit | 0 | Materials | Cabinetry and Storage |
| 5400 Cabinetry & Storage | Semi Custom Cabinetry | 22PYwcwnZNHM | Total | null | Materials | Cabinetry and Storage |
| 5500 Appliances | Appliances | 22PCTdp37yD9 | — | 16000 | Labor | Appliances |
| 5500 Appliances | 5520 Appliance Installation | 22PCTdp37yDA | Hours | 75 | Labor | Appliances |
| 5600 Finish Plumbing | Plumbing Fixtures | 22PCTdp37yDC | Unit | 0 | Materials | Finish Plumbing |
| 5600 Finish Plumbing | 5600 Finish Plumbing Installation | 22PCTdp37yDE | Hours | 75 | Sub | Finish Plumbing |
| 5600 Finish Plumbing | Bathroom Hardware | 22PYRwwBbFub | Total | null | Materials | Finish Plumbing Materials |
| 5700 Finish Electrical | Electrical Fixtures | 22PCTdp37yDD | Unit | 0 | Labor | Finish Plumbing |
| 5700 Finish Electrical | 5730 Electrical Finish Installation | 22PCTdp37yDG | Hours | 75 | Sub | Finish Electrical |
| 5800 Finish HVAC | Finish HVAC | 22PCTdp37yDJ | Each | 450 | Materials | Finish HVAC |
| 5900 Interior Wall Coverings | Paint Materials | 22PCTdp37yDL | Unit | 0 | Labor | Interior Wall Coverings |
| 5900 Interior Wall Coverings | Interior Paint | 22PCTdp37yDM | Unit | 0 | Sub | Interior Wall Coverings |
| 5900 Interior Wall Coverings | Wall treatments | 22PCTdp37yDN | Unit | 0 | Labor | Interior Wall Coverings |
| 6100 Landscaping & Driveway | 6100 Landscaping Specifications | 22PCTdp37yDT | Each | 10000 | Sub | Landscaping and Driveway |
| 6100 Landscaping & Driveway | Driveway and Exterior Concrete paths | 22PCTdp37yDU | SF | 8.95 | Sub | Landscaping and Driveway |
| 6100 Landscaping & Driveway | 6125 Gutters and Downspouts | 22PCTdp37bHC | — | 0 | Sub | Rough Structure |
| 6200 Completion | Punch list to completion | 22PCTdp37yDQ | Days | 1800 | Labor | Completion |
| 6200 Completion | Professional Cleaning | 22PCTdp37yDR | Each | 600 | Sub | Completion |
| 6200 Completion | Warranty and Quality Control | 22PHezSqGrHr | Total | null | Labor | 1000 Prep & Mgmt |

---

## Appendix B — Where the decisions came from

- `OCC_Ballpark_Tool_Setup_Worksheet.docx`: the original setup worksheet (Oct 26).
- `OCC_Ballpark_Tool_Worksheet_Answers.docx`: Tyler's 12 answer videos, CompanyCam project "Internal estimating" (id 117589290), Oct 26.
- `OCC_Ballpark_Tool_Pricing_Inputs_Answers.docx`: the 53-item pricing list with Tyler's two Oct 27 videos (23864513, 23864803) and Jason's follow-up answers. 52 of 53 settled; #51 open.
