# OCC Estimator Backend

A simple Python webhook server that receives Wufoo form submissions,
downloads the PDFs, calls Claude to generate an estimate, and emails
the result to Jason.

## Environment Variables Required

Set these in Render's environment variables:

- `ANTHROPIC_API_KEY` - Your Anthropic API key
- `WUFOO_API_KEY` - Your Wufoo API key (for downloading files)
- `SMTP_USER` - Gmail address to send from
- `SMTP_PASS` - Gmail app password
- `NOTIFY_EMAIL` - Email to send estimates to (default: jason@ownerschoiceconstruction.com)

## Deploy to Render

1. Push this folder to a new GitHub repo
2. Connect repo to Render as a Web Service
3. Set start command: `python app.py`
4. Add environment variables
5. Copy the Render URL and set it as your Wufoo webhook

## Wufoo Webhook Setup

In Wufoo: Form Settings -> Notifications -> WebHooks
Set the webhook URL to your Render URL (e.g. https://occ-estimator.onrender.com)
