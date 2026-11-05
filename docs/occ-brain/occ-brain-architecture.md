# OCC Brain - Architecture

Planning notes from Nov 5, 2026 (claude.ai project "OCC Knowledge Base"). Status: **design only - nothing built.** The first piece being built is the Site Scope Tool's Granola work (`docs/site-scope/site-scope-tool-build-plan.md`, "Next phases" - phases 9-14 and "Visit classification and safety gates"). This doc is the bigger picture those phases fit into.

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
| Sub bid / vendor email | JobTread returned bid, or email from a vendor account | Later: rate anchors (build plan phase 12), sub scorecard |
| Personal / other | Anything else | Nothing |

## Keeping noise out
- **A high bar.** Each candidate fact has to pass: "would someone at OCC want to know this in six months?" Small talk, scheduling chatter and anything already in JobTread or CompanyCam is dropped. Most transcripts should produce zero to a handful of saved facts.
- **One fact, one place.** Facts are filed by subject. New information updates the existing entry instead of adding another, so a sub's notes stay one clean record, not 40 fragments.
- **Short-lived items expire.** Action items and promises ("I'll have a price to you by Friday") become JobTread to-dos and are done when checked off. They never go into long-term memory.
- **Unconfirmed vs. confirmed.** Facts the AI pulls out start as unconfirmed. Anything that becomes company truth - pricing lessons, SOPs, rules - needs a quick yes from Jason. Unconfirmed facts that never get used age out (proposed: 90 days).
- **Organized by subject, not by date:** job, client, sub/vendor, realtor, team/process, pricing. A question goes to the right subject instead of searching a pile of meeting dumps.
- **The test:** asking the brain something gets back a short, right answer with a link to the photo or transcript behind it. If answers get cluttered or wrong, the bar is too low.

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
- Pricing from sub bid history so fewer bid requests go out (build plan phase 12).
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
5. **Scorecards and pattern analysis** once there's months of data behind them.

## Open questions
- **Where the brain's own facts live.** Options: on the JobTread record they're about (internal fields/notes - where the team already works), a small database, files in this repo, or Claude Project docs. Leaning: job-specific facts on the JobTread record; company-wide knowledge (SOPs, pricing lessons, sub notes) in one store - not decided.
- Granola retention on the Business plan, and after the trial.
- Whose Granola notes are in scope - Jason only for now; when the team joins (ties to the shared company AI platform evaluation).
- Exact access tier rules, and who confirms facts besides Jason.
