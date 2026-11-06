# OCC Brain - Architecture

Planning notes from Nov 5, 2026 (claude.ai project "OCC Knowledge Base"); Pricing Knowledge Base section added Nov 6. Status: **design only - nothing built.** The first piece being built is the Site Scope Tool's Granola work (`docs/site-scope/site-scope-tool-build-plan.md`, "Next phases" - phases 9-14 and "Visit classification and safety gates"). This doc is the bigger picture those phases fit into.

**This repo copy is the source of truth.** When a decision changes, update this file in the same pull request.

## Goal (Jason, Nov 5)
- "I want a system that will always be gathering, organizing, and processing data that users are inputting."
- Ultimately the pipeline catches all Granola info and knows how to use it and where it goes. Jason expects this to take time to build out fully.
- Everything coming in gets processed and judged for whether it's worth keeping, so the company knowledge base keeps improving - e.g. working out which jobs are good jobs.
- **Constraint (Jason):** don't capture so much noise that the good info gets hard to reach. OCC already has systems that store data (CompanyCam keeps the photos and videos) - don't duplicate them.

## The shape: three layers
1. **Capture.** People keep working the way they do now. Sources:

   | Source | What comes in |
   |---|---|
   | Granola | Site visits, team meetings, company meetings, calls |
   | CompanyCam | Photos, videos, Pages, walkthrough summaries |
   | JobTread | Jobs, budgets, documents, bid requests and returned bids, Daily Logs, to-dos, vendor bills |
   | Google Calendar / Calendly | Which visit is where and when |
   | RingCentral | Client calls |
   | Gmail | Sub bids, client email |

2. **Classify and route.** Every item gets labeled: what it is (site visit, revisit, team meeting, client call, sub bid...), who and what it's about (job, client, sub, realtor, team member), and whether it is actionable, worth remembering, or neither.
3. **Two outputs.**
   - **Actions** - the gated workflows (first one: the site visit pipeline). Drafts only, never send, never overwrite.
   - **Memory** - the knowledge base: a small set of facts pulled out and linked back to their source.

Most items end up in neither output. That's correct, not a failure.

## Rule 1: links, not copies (Jason, Nov 5)
Every system keeps what it already keeps. The brain stores **only facts that live nowhere else**, each with a link back to the source.

| Where it lives | What it holds | Brain's role |
|---|---|---|
| CompanyCam | Photos, videos, Pages | Link only |
| Granola | Full transcripts | Link only |
| JobTread | Jobs, budgets, costs, bids, status, Daily Logs | Query live when needed - never copy |
| Gmail / RingCentral | Emails, call records | Link only |
| **The brain** | Decisions, lessons learned, why a job went sideways, what a client cares about, sub reliability notes, "we tried X and it didn't work" | **The only thing it actually stores** |

Example: "which jobs are good jobs" doesn't need the brain to store margins - those come from JobTread at the time of the question. The brain adds what JobTread can't hold: "client changed scope four times", "realtor pushed hard on price", "crawlspace was worse than the photos showed".

Because the raw material stays in its own system, nothing is lost by building the memory side later: the same Granola transcripts and CompanyCam photos can be processed once the extraction step exists. **To check:** how long Granola keeps notes and transcripts on the Business plan, and what happens to them if the plan lapses after the trial.

## Rule 2: capture broadly, act narrowly
- Nothing kicks off a workflow unless the item clearly qualifies. Uncertain = do nothing and put it on a short "needs your call" list.
- A team meeting never triggers the site visit pipeline, but it can still feed memory (decisions, action items).
- The site visit gates are written up in the build plan ("Visit classification and safety gates"). Every future workflow gets the same three things: a positive test for "this item is mine", a mode chosen from the current state of the record, and safety rails (drafts only, never overwrite, run once per item).

## Classifying an item
| Kind | How it's recognized | What happens |
|---|---|---|
| Site visit - new | Granola note on a calendar event with an address that matches a job with no budget yet | Full site visit pipeline |
| Site visit - revisit | Same, but the job already has a budget or an earlier processed visit, not sold | Revisit mode (build plan) |
| Site visit - job in progress | Same, but the job is sold | Log-only mode (build plan) |
| Address, no matching job | Calendar address that doesn't match a job | Needs your call |
| Team / company meeting | No address and no job match | No workflow; memory only (decisions, action items), at the right access tier |
| Client call | RingCentral call matched to a contact | Later: notes onto the job, promises tracked |
| Sub bid / vendor email | JobTread returned bid, or email from a vendor account | Later: price rows in the Pricing Knowledge Base (below; build plan phase 12), sub scorecard |
| Personal / other | Anything else | Nothing |

## Keeping noise out
- **A high bar.** Each candidate fact has to pass: "would someone at OCC want to know this in six months?" Small talk, scheduling chatter and anything already in JobTread or CompanyCam is dropped. Most transcripts should produce zero to a handful of saved facts.
- **One fact, one place.** Facts are filed by subject. New information updates the existing entry instead of adding another, so a sub's notes stay one clean record, not 40 fragments.
- **Short-lived items expire.** Action items and promises ("I'll have a price to you by Friday") become JobTread to-dos and are done when checked off. They never go into long-term memory.
- **Unconfirmed vs. confirmed.** Facts the AI pulls out start as unconfirmed. Anything that becomes company truth - pricing lessons, SOPs, rules - needs a quick yes from Jason. Unconfirmed facts that never get used age out (proposed: 90 days).
- **Organized by subject, not by date:** job, client, sub/vendor, realtor, team/process, pricing. A question goes to the right subject instead of searching a pile of meeting dumps.
- **The test:** asking the brain something gets back a short, right answer with a link to the photo or transcript behind it. If answers get cluttered or wrong, the bar is too low.

## Pricing Knowledge Base (added Nov 6, 2026)
Status: **design only - nothing built.** Approved by Jason Nov 6 as part of the plan. Items marked *proposed* still need his yes.

### Goal (Jason, Nov 6)
- A constantly growing knowledge set of real pricing - sub bids, invoices and any other real prices - that the AI estimators use when they quote, and that stays up to date on its own.
- Payoff: AI budgets priced from what OCC actually pays, and fewer bid requests over time (the "bid optional" goal from build plan phase 12).

### Scope
- **In:** the closing-repair estimator (`estimate_v2_draft.py`) and the Site Scope budget step (`scope_budget.py`). Both price real repair and remodel scope off sub and supplier history, and both read the same pricing files today.
- **Out:** the Ballpark tool. It prices off the JobTread "Master Design Build Template" (`22PCTdp32jat`) and its own tier price table, and stays a separate build (`docs/ballpark/`), waiting on Tyler's final input (Jason, Nov 6).

### Where pricing lives today (Nov 6)
- Five vendor invoice-history CSVs in this repo (Tile with Style, Redland Electric, Crawlspace Medic, Greer Flooring, Jordan Lumber) plus `historical_crawlspace_medic_quotes.csv`. Real prices, but built once by hand - nothing adds to them.
- `consolidated_trade_group_calibration.csv` - Jason's hand-corrected trade groups (19 rows as of Oct 26). The only part that grows today, and only by hand.
- `pricing_library.csv` - 310 repair types in 22 categories, with every price column empty. A naming list, not prices.
- Prices Jason has given in Claude chats that no tool can read. Examples: plumber tub-drain cap $1,500-1,800 low / ~$3,500 high (job 00827); plumber sink/faucet/disposal/dishwasher reconnect ~$650 (00840); Andersen windows/doors at ~65% of client price (00716); Renew Painting ~$1,800-2,000 for a small second-story siding wall (00898); Jeff Lynch entry-level Frigidaire package as the appliance baseline; old Greer backsplash quote + 20-30%.
- **Gap:** no plumbing history at all. Plumbing stays a guess or PRICING TBD until bids start feeding in.

### The three layers
The AI is good at matching messy scope to past prices, adjusting for differences (size, access, condition), knowing when nothing fits, and explaining itself. It is bad at consistent math, knowing which price is current, OCC rules it was never told, and it will make up a number when it has none. So the work is split - the same split the Site Scope budget step already uses ("code does math and rules"; a licensed-trade line must cite a real anchor or it goes to $0 PRICING TBD).

**1. Price table (facts).** One row per real price:

| Field | Example |
|---|---|
| Trade, vendor | Crawlspace, Crawlspace Medic |
| Scope item - named from the `pricing_library.csv` list where one fits - plus the scope text as written | "Cleanout with barrier and debris removal" |
| Unit (SF / LF / each / lump), unit cost, quantity | each, $340, 1 |
| Size / context for lump prices | "full master bath, ~90 SF of wall tile" |
| Date, job | 2026-02, job 00xxx |
| Source link | JobTread bid / vendor bill / Gmail message / Granola note / chat |
| Type | invoiced, quoted, verbal, Jason's price |
| Status | unconfirmed, confirmed |

All amounts are OCC cost before markup. A lump-sum row without scope and size is not useful - the intake step must capture both or leave the row unconfirmed.

**2. Price-picking rules (code, not AI).**
- **Which price wins** when several rows match: invoiced > quoted > verbal > Jason's judgment; newer > older; same sub > different sub. Show the range, not just one number.
- **Age:** rows over ~12 months old are flagged. Where the history shows a trade's yearly increase (e.g. CSM's Santa Fe dehumidifier rising ~$100/yr), code bumps the price and says so in the NOTES line. *(Proposed: escalation per trade, only where there is evidence.)*
- **Units:** never compare per-SF to lump or each to LF without the size behind it.
- **Minimums and fixed adders:** e.g. Redland's $200 minimum trip, Tile with Style's setting-materials lump.
- Markups, totals and math stay in code (sub x1.45, material x1.65, labor $55 -> $89), as today.

**3. Pricing lessons (judgment, plain English).** The parts a table can't hold, e.g. "carry the worst case on a slab tub-drain cap and tell the client it comes down", "small-format shower floor costs less per SF than large-format with Tile with Style", "plumber doesn't demo the tub - OCC demos first". They live in the planned "OCC Budget Standards" doc (build plan phase 14) under a Pricing lessons section, so the backend prompts and the chat budget skill read the same rules. Every lesson needs Jason's yes before the estimators use it (same rule as all company truth in this doc).

**The AI's job:** match each scope item to rows, adjust for real differences, and write which rows it used and why into the internal NOTES line. It never invents a sub price.

### Confidence on every priced line
- Each sub line states what it rests on, e.g. "3 CSM invoices, 2025-26, $325-350" (high) vs. "1 verbal plumber number" (low).
- Confidence decides the bid step automatically: high = "bid optional", anything lower = sub bid draft (build plan phase 11). Unusual scope always gets a bid.
- *Proposed threshold for high:* 3+ invoiced/quoted rows for similar scope in the same unit within ~18 months, with a spread under ~25%. Tune after real use.

### How it grows (feeds)
| Feed | When | Notes |
|---|---|---|
| JobTread returned bid requests and vendor bills | Nightly | Read live; each becomes a row with a link back. Covers sub bids sent through JobTread bid request documents |
| Gmail sub bids and supplier quotes | Nightly | *Proposed:* a "Bids" label Jason or the team applies; the attachment is read and a row drafted. Most subs email or text bids today |
| Prices said out loud | After each Granola note / chat | Verbal numbers ("probably $650") become verbal rows, unconfirmed |
| Jason's corrections to AI budgets | 7-21 days after the estimate | The draft-vs-final comparison (`feedback_loop.py`, half-built) turns each changed price into a "Jason's price" row and drafts a lesson for him to approve |

New rows start **unconfirmed**. Invoiced and JobTread-bid rows can go straight to confirmed (*proposed*); verbal rows, Gmail-parsed rows and drafted lessons wait for Jason's quick yes.

**Links, not copies:** a price row is an extracted fact (a normalized price plus a link), not a copy of the bid or bill - consistent with Rule 1. The bid, bill or email stays where it is.

### Storage *(proposed, not decided)*
A Google Sheet on OCC's Workspace: free, Jason and the team can see and fix rows, and the backend can read and write it without upgrading Render (512 MB). Repo CSVs would need a commit for every new price. Ties to the "where the brain's own facts live" open question below.

### Rollout *(proposed)*
1. **Seed the table:** the five vendor CSVs, the CSM quotes file, the calibration CSV, and the chat-only prices listed above. Point both estimators at it in place of the separate CSVs.
2. **Which-price-wins rules + confidence** on every priced line.
3. **JobTread feed** (returned bids, vendor bills) - this is build plan phase 12.
4. **Corrections feed** - finish `feedback_loop.py`'s sweep.
5. **Gmail and verbal feeds.**
6. **Lessons** build up from corrections over time - no big up-front writing session.

## Access tiers
- Company meetings will include HR, pay and personnel topics. Those must not be searchable by the whole team or land anywhere client-facing.
- Proposed tiers: **Everyone**, **Office**, **Owner only**. Company and team meeting notes default to Owner only until classified.
- Nothing from a meeting is ever written to a client-facing field (budget group descriptions, documents, customer messages). Internal context on a job goes to the internal Notes field `22P9qDnqtWh6`, never the customer-facing description.
- Recording consent: a line to clients at the door that Jason is taking AI notes (already in the build plan's gaps list).

## Use cases (ideas, Nov 5 - not prioritized)
**Sales and estimating**
- Good job vs. bad job scoring: margin, change orders, client friction, payment speed - by job type, lead source, realtor and area.
- Win/loss reasons from what clients actually said on the walk.
- Estimate accuracy: estimated vs. actual cost by trade, feeding the budget step (ties to `feedback_loop.py`).
- Pricing from sub bid history so fewer bid requests go out - see "Pricing Knowledge Base" above (build plan phase 12).
- Promise tracking: commitments made on a walk or call become to-dos.

**Clients and realtors**
- Client profiles: decision makers, budget signals, concerns, preferences.
- Realtor relationship value: referral volume, close rate, job quality per agent.

**Subs and suppliers**
- Sub scorecards: bid response time, price vs. other subs, reliability, callbacks mentioned anywhere.
- "Who should get this bid?" by trade and area.

**Production**
- In-progress visit logs: issues found, change-order candidates, a progress narrative on the job.
- Patterns: recurring problems by house age, neighborhood or builder.

**Team and company meetings**
- Decisions and action items log: who owns what, by when; "what did we decide about X?"
- SOP drafts when the same process gets explained repeatedly.
- Onboarding material from how things actually get done.

**Company level**
- Profitability by job type and bottleneck visibility (ties to the financial visibility work).
- Ask-the-brain Q&A for the team: "how do we usually price a sliding door install?", "what did the Cliff Ridge client say about trim?"

## Build order (proposed Nov 5)
1. **Site visit pipeline with the gates** (build plan phases 9-11) - biggest immediate time saving.
2. **Classify every Granola note** - a label and a link only, no extraction yet. Cheap, and it shows how the gates sort real notes before anything depends on them.
3. **Promise and action-item tracking** - cheap and useful right away.
4. **Memory extraction** with the unconfirmed/confirmed step and access tiers.
   - The Pricing Knowledge Base runs alongside this order on its own rollout (see its section). Its first step - seeding the table and pointing both estimators at it - doesn't depend on Granola and can start any time.
5. **Scorecards and pattern analysis** once there's months of data behind them.

## Open questions
- **Where the brain's own facts live.** Options: on the JobTread record they're about (internal fields/notes - where the team already works), a small database, files in this repo, or Claude Project docs. Leaning: job-specific facts on the JobTread record; company-wide knowledge (SOPs, pricing lessons, sub notes) in one store - not decided.
- Granola retention on the Business plan, and after the trial.
- Whose Granola notes are in scope - Jason only for now; when the team joins (ties to the shared company AI platform evaluation).
- Exact access tier rules, and who confirms facts besides Jason.
- Pricing Knowledge Base: storage (Google Sheet proposed), the "high confidence" threshold, per-trade price escalation, and whether invoiced rows skip confirmation.
