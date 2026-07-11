# Apex Operations — AI Email Triage (Demo)

**A portfolio demonstration by Promethean PR & Automation**
*Built with n8n · Python · OpenAI GPT-4o · Gmail · Google Drive · HubSpot · Slack*

---

## What This Project Does

Automatically triages incoming client emails for a B2B operations consulting firm.
For every new email received:

1. **Classifies** it into a category (new inquiry, client support, billing, escalation) and priority level using GPT-4o
2. **Retrieves** the most relevant internal document from Google Drive to ground the reply
3. **Drafts** a professional, document-grounded response and saves it to Gmail Drafts — **never auto-sends**
4. **Logs** the interaction to HubSpot, creating or updating the contact record
5. **Notifies** the correct Slack channel with a triage summary and HubSpot link

> ⚠️ **No email is ever sent automatically.** Every draft requires a human to review and send manually.

---

## Files in This Repository

| File | Purpose |
|------|---------|
| `apex_email_triage.json` | Importable n8n workflow — drag into n8n via Workflows → Import |
| `apex_email_triage.py` | Standalone Python equivalent — runs without n8n |
| `.env.example` | Template for environment variables (copy to `.env`, never commit) |
| `README.md` | This file |

---

## Architecture

```
Gmail (poll every 5 min)
    │
    ▼
GPT-4o Categorization
    │  category / priority / summary / route_to
    ▼
IF new_inquiry ──────────────────┐
    │                            │
    ▼                            ▼
Services Overview Doc     Category Doc (billing/FAQ)
    │                            │
    └──────────┬─────────────────┘
               ▼
        Draft LLM (GPT-4o)
               │
               ▼
        Gmail → Create Draft (NOT Send)
               │
               ▼
        HubSpot Search Contact
               │
        ┌──────┴──────┐
        ▼             ▼
   Exists?         New contact?
   Update +        Create +
   Log Note        Log Note
        │             │
        └──────┬───────┘
               ▼
        Slack Switch
     ┌────┬────┬─────┐
     ▼    ▼    ▼
  #sales #acct #admin
```

---

## Option A — Run with n8n (No-Code)

### Prerequisites
- n8n Cloud account or self-hosted n8n instance
- Gmail label called `Client Incoming` (create in Gmail Settings → Labels)
- HubSpot Private App (see HubSpot setup below)
- Slack bot invited to `#sales`, `#account-management`, `#admin`
- OpenAI API key with `gpt-4o` access
- Google Drive folder with internal docs (IDs pre-configured in workflow)

### Import Steps
1. Open your n8n instance
2. Go to **Workflows → Import from JSON**
3. Paste or upload `apex_email_triage.json`
4. In each node, click the credential field and select (or create) the matching credential:

| Node | Credential Type | Placeholder to Replace |
|------|----------------|----------------------|
| Gmail Trigger | Gmail OAuth2 | `DEMO_GMAIL_OAUTH` |
| Get Services Overview Doc | Google Drive OAuth2 | `DEMO_GDRIVE_OAUTH` |
| Get Category Doc | Google Drive OAuth2 | `DEMO_GDRIVE_OAUTH` |
| AI Categorize Email | HTTP Header Auth | `sk-DEMO-xxx...` |
| Draft Email Response | HTTP Header Auth | `sk-DEMO-xxx...` |
| HubSpot nodes | HTTP Header Auth | `hsp_DEMO-xxx...` |
| Slack nodes | Slack OAuth2 | `DEMO_SLACK_OAUTH` |

5. Click **Save**, then **Activate**

### Testing
- Send a test email to the Gmail account with subject `Test Inquiry`
- Wait up to 5 minutes for the trigger to poll, or execute manually
- Verify: Gmail Draft created, HubSpot contact created/updated, Slack message sent

---

## Option B — Run with Python (Code)

### Prerequisites
- Python 3.11+
- Google Cloud project with Gmail API and Drive API enabled
- `credentials.json` (OAuth2 Desktop App) downloaded from Google Cloud Console

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/apex-email-triage.git
cd apex-email-triage

# 2. Install dependencies
pip install openai google-auth google-auth-oauthlib google-api-python-client \
            slack-sdk python-dotenv requests schedule

# 3. Configure credentials
cp .env.example .env
# Edit .env with your real values

# 4. Run (first run opens browser for Google OAuth consent)
python apex_email_triage.py

# Or run once (for cron jobs)
python apex_email_triage.py --once
```

### Environment Variables

See `.env.example` for all required variables. At minimum:

```
OPENAI_API_KEY=sk-...
HUBSPOT_TOKEN=hsp_...
SLACK_BOT_TOKEN=xoxb-...
HUBSPOT_PORTAL_ID=12345678
```

---

## HubSpot Setup

1. Go to **Settings → Integrations → Private Apps** in HubSpot
2. Create a new Private App named `AI Email Triage`
3. Grant scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.objects.notes.write`
4. Copy the access token (starts with `hsp_`)
5. Add a custom contact property: **Settings → Properties → Create property**
   - Object: Contact
   - Label: `Last Inquiry Type`
   - Internal name: `last_inquiry_type`
   - Type: Single-line text

---

## Slack Setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App**
2. Add Bot Token Scopes: `chat:write`, `chat:write.public`
3. Install to workspace and copy the Bot Token (`xoxb-...`)
4. Invite the bot to each channel: `/invite @YourBotName` in `#sales`, `#account-management`, `#admin`

---

## Google Drive Document IDs

The workflow references these internal document IDs for Apex Operations (demo):

| Document | ID |
|----------|----|
| Services Overview | `1esIaycXlDZz5lE-1zILm8QOggDiWR6rp3jG23-5ChRM` |
| Billing & Admin Procedures | `1Z396LRKYmC1ENInWY-X946U3NL5igobQ2-yhjrJo2n4` |
| Client Support FAQ | `1thH0AsJQaDpaTo9JgNhXbN6YUlrYZk6WKcu5WFm4aJ4` |
| Internal Docs Folder | `13LPG5a-EBYRlxurpI1b0P_1TsAOE5CCn` |

For a real client deployment, replace these with the client's actual Google Drive document IDs.

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| AI returns malformed JSON | Slack alert to `#admin`, email skipped, workflow continues |
| Google Drive doc not found | Fallback text passed to draft LLM, draft notes doc unavailable |
| HubSpot API error | Error logged, workflow continues to Slack notification |
| Slack notification fails | Logged only — Gmail draft and HubSpot note are primary records |

---

## Security Notes

- **No credentials are stored in this repository.** All secrets use environment variables.
- The `.env` file is listed in `.gitignore` — never commit it.
- `token.pickle` (Google OAuth token) is also gitignored.
- HubSpot tokens in the n8n workflow JSON are placeholder values only (`hsp_DEMO_xxx`).
- OpenAI keys in the n8n workflow JSON are placeholder values only (`sk-DEMO-xxx`).

---

## Customising for a Real Client

1. Replace all Google Drive document IDs with the client's actual files
2. Update the Gmail label name if the client uses a different label
3. Adjust Slack channel names to match the client's workspace
4. Update `HUBSPOT_PORTAL_ID` to the client's portal ID
5. Swap all `DEMO_` credential placeholders for real values
6. Test with a single email before activating for production

---

## About

Built by **Tom Blackstone** · [Promethean PR & Automation](mailto:tom@prometheanpr.com)

This is a portfolio demonstration workflow. The fictional client (Apex Operations Consulting) and all document IDs are for demo purposes only.
