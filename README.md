# OCC Estimator Backend

Python web service (on Render) for Owners Choice Construction. It does three things:

1. **Turns intake forms into JobTread jobs with an AI-drafted estimate.** Wufoo
   forms and OCC's sales-lead tool post here. The service creates or matches the
   account, contact and location, creates the job, attaches the files, then asks
   Claude for an estimate and writes it into the job's budget as cost groups. If
   the estimate fails, the lead is still saved and a "needs manual review" to-do
   is added.
   - **Closing repairs** use `estimate_v2_draft.py`. Claude reads the repair
     addendum and inspection report as PDFs (including photos and handwritten
     markups) and returns labor hours, materials and sub prices per trade.
     Materials are priced against the live Home Depot catalog through JobTread.
   - **Home Repair, GVL Today, Remodel and Pre-listing** get a best-effort,
     flat-price estimate from the description, photos or inspection text, or a
     "consult needed" note when there isn't enough to price.
2. **Runs the to-do and status pipeline from JobTread webhooks.** It creates the
   next to-do when one is checked off, moves job status forward, marks a job Sent
   when an estimate is emailed and Closed Won when it's approved, and pauses
   follow-ups when a customer replies.
3. **Drafts follow-up emails.** A daily call to `/send-followups` creates
   "Review & send" to-dos with a Claude-drafted email (Day 3, 7 and 14, or a
   48-hour check-in for closing repairs). Checking the to-do off sends the email
   through JobTread.

## Endpoints

POST requests are answered with `200 OK` immediately, and the work runs in the
background, so check the Render logs for results. The endpoints have no
authentication.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | Health check |
| GET | `/send-followups` | Daily follow-up run. Returns 202 and runs in the background; 409 if a run is already going |
| GET | `/refresh-pricing` | Rebuilds a price-range table from past "Closing Repairs" estimates in JobTread and saves it as `PRICING_REFERENCE` (on Render too, if the Render keys are set). Used by the non-closing estimates. Takes minutes |
| POST | `/home-repairs`, `/home-repair` | Wufoo Home Repairs form |
| POST | `/gvl-today`, `/gvl`, `/gvltoday` | Wufoo GVL Today form (lead source "GVL Today Ad") |
| POST | `/remodel` | Wufoo Remodel form |
| POST | `/prelisting`, `/pre-listing`, `/prelisting-repairs` | Wufoo Pre-listing Repair form |
| POST | any other path (e.g. `/`) | Wufoo Closing Repairs form |
| POST | `/sales-tool-closing-estimate` | JSON from the sales tool for a closing-repair job it already created: `job_id`, `client_name`, `client_phone`, `client_email`, `address`, `notes_text`, `addendum_url`, `inspection_url` |
| POST | `/sales-tool-general-estimate` | Same for Home Repair / Remodel / Pre-listing jobs: `job_id`, `job_type`, client fields, `notes_text`, `description`, `photo_urls` |
| POST | `/rerun-estimate` | `{"job_id": "...", "force": false}`. Rebuilds a closing-repair estimate from the files already on the job. Also triggered by checking off the "🔄 Re-run AI estimate" to-do or commenting `[RERUN]` (`[RERUN FORCE]` to add groups to a job that already has a budget) |
| POST | `/jobtread-document-sent` | JobTread webhook: estimate emailed → status Sent, follow-up clock starts |
| POST | `/jobtread-document-updated` | JobTread webhook: estimate approved → Closed Won |
| POST | `/jobtread-job-created` | JobTread webhook: first to-do for a new lead |
| POST | `/jobtread-job-updated` | JobTread webhook: status changes (closed, long-term, dragged forward) |
| POST | `/jobtread-task-updated` | JobTread webhook: a to-do was checked off |
| POST | `/jobtread-comment-created` | JobTread webhook: customer replies and `[RERUN]` comments |

## Environment variables

Set these in the Render service's Environment tab. The `.env` file is not
committed.

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `JOBTREAD_API_KEY` | Yes | JobTread Pave API grant key |
| `WUFOO_API_KEY` | Yes | Downloads files attached to Wufoo submissions |
| `JOBTREAD_ORG_ID` | No | JobTread organization. Defaults to OCC's org (`22P9ppHePJKP`) |
| `CLOSING_ESTIMATE_MODEL` | No | Claude model for closing-repair estimates. Defaults to `claude-sonnet-4-6`. The other estimates and follow-up emails use `CLAUDE_MODEL` in `app.py` |
| `PRICING_REFERENCE` | No | Price-range table for the non-closing estimates. Written by `/refresh-pricing`; the built-in table is used when it's blank |
| `RENDER_API_KEY`, `RENDER_SERVICE_ID` | No | Let `/refresh-pricing` save `PRICING_REFERENCE` to Render so it survives restarts |
| `PORT` | No | Set by Render. Defaults to 8080 |

## Deploying on Render

The service is a Render **Web Service** connected to this GitHub repo
(`main` branch).

- Build command: `pip install -r requirements.txt`
- Start command: `python app.py`

With Render's auto-deploy on, a push to `main` redeploys it. After a deploy, open the service URL. It should
say "OCC Estimator Backend is running!".

The webhook senders point at the service URL plus the path from the table:
Wufoo (Form Settings → Notifications → WebHooks), the sales-lead tool, JobTread
webhooks, and the daily `/send-followups` scheduler.

## Files

- `app.py`: the web server, intake forms, JobTread writes, to-do/status
  automation and follow-up emails.
- `estimate_v2_draft.py`: closing-repair estimating (prompt, Claude call,
  Home Depot pricing, cost-group writer). The name is historical; it is live.
- CSVs read by the closing-repair prompt, which must ship with the code:
  `consolidated_trade_group_calibration.csv`,
  `historical_home_and_closing_repairs_budget_detail_dedup.csv`,
  `redland_electric_invoice_history.csv`, `crawlspace_medic_invoice_history.csv`,
  `tile_with_style_invoice_history.csv`, `greer_flooring_invoice_history.csv`,
  `jordan_lumber_invoice_history.csv`.
- `feedback_loop.py`: after every AI estimate is written (both
  `estimate_closing_job` and `estimate_general_job`), saves the untouched
  original as an `[AI-FEEDBACK-BASELINE-V1]` Daily Log on the job so it can
  later be compared with what was actually sent. The automated sweep and
  Google Sheet in the same file are still not wired in; it is the only user
  of `gspread`/`google-auth` in `requirements.txt`.
- Not used by the server: `repair_analyzer.py`, `repair_analyzer_retry.py` and
  `cluster_repair_items.py` (one-off local analysis scripts).
- `CLAUDE.md`: project history, pricing rules, and the reasoning behind them.
