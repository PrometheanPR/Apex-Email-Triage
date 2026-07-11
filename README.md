# AI Email Triage — Automation Template

**Built with n8n · Python · OpenAI GPT-4o · Gmail · Google Drive · HubSpot · Slack**

*An automation template for service-based businesses that receive high volumes of client email.*

---

## What This Template Does

Automatically triages incoming client emails using AI.
For every new email received:

1. **Classifies** it into a category (new inquiry, client support, billing, escalation) and priority level using GPT-4o
2. **Retrieves** the most relevant internal document from Google Drive to ground the reply
3. **Drafts** a professional, document-grounded response and saves it to Gmail Drafts — **never auto-sends**
4. **Logs** the interaction to HubSpot, creating or updating the contact record
5. **Notifies** the correct Slack channel with a triage summary and HubSpot link

> ⚠️ **No email is ever sent automatically.** Every draft requires a human to review and send manually.

---

## Who This Is For

This template is designed for **service-based businesses** that:

- Receive a steady volume of client emails across multiple categories (inquiries, support, billing, escalations)
- Have internal documentation (SOPs, FAQs, service overviews) they want to use as the basis for replies
- Use HubSpot as their CRM and Slack for internal team communication
- Want AI assistance with drafting without removing humans from the sending decision

Typical use cases: consulting firms, agencies, managed service providers, professional services teams.

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
- Google Drive folder with internal docs (IDs configured in workflow)

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
git clone https://github.com/PrometheanPR/Apex-Email-Triage.git
cd Apex-Email-Triage

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

The workflow references these internal documents (pre-configured for this template):

| Document | ID |
|----------|----|
| Services Overview | `1esIaycXlDZz5lE-1zILm8QOggDiWR6rp3jG23-5ChRM` |
| Billing & Admin Procedures | `1Z396LRKYmC1ENInWY-X946U3NL5igobQ2-yhjrJo2n4` |
| Client Support FAQ | `1thH0AsJQaDpaTo9JgNhXbN6YUlrYZk6WKcu5WFm4aJ4` |
| Internal Docs Folder | `13LPG5a-EBYRlxurpI1b0P_1TsAOE5CCn` |

Replace these with your own Google Drive document IDs when adapting the template.

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

## Customising for a Project

1. Replace all Google Drive document IDs with your project's actual files
2. Update the Gmail label name if the project uses a different label
3. Adjust Slack channel names to match the workspace
4. Update `HUBSPOT_PORTAL_ID` to the correct portal ID
5. Swap all `DEMO_` credential placeholders for real values
6. Test with a single email before activating for production

---

## Versioning

| Version | Description |
|---------|-------------|
| v1 | Core email triage — classify, draft, log to HubSpot, notify Slack |
| v2 | Document upload form — upload new docs to Google Drive via a branded web form |
---

## Version 2 — Document Upload Form

v2 adds a self-service web form that lets team members upload new documents directly into the Google Drive knowledge base, without needing Drive access.

### New Files

| File | Purpose |
|------|---------|
| `apex_doc_upload_v2.json` | n8n webhook workflow — receives uploads, validates, stores to Drive, notifies Slack |
| `apex_doc_upload_v2.py` | Python Flask server — same logic, serves the form and handles uploads |
| `upload_form.html` | Branded HTML form — posts to either the n8n webhook or the Python server |

### How It Works

```
User fills out upload_form.html
    │  multipart/form-data POST
    ▼
Webhook (n8n) or Flask route (Python)
    │
    ▼
Validate: category, file type (PDF/DOCX), file present
    │
    ├── Invalid → 400 JSON error → form shows red error message
    │
    ▼
Upload file to Google Drive folder
    │
    ▼
Slack #admin notification (file name, category, description, uploader, Drive link)
    │
    ▼
200 JSON success → form shows green confirmation
```

### Option A — n8n Setup

1. Import `apex_doc_upload_v2.json` via Workflows → Import
2. Open the **Receive Document Upload** (Webhook) node and copy the Production webhook URL
3. Open `upload_form.html` and replace `YOUR_WEBHOOK_URL_HERE` with the copied URL
4. Configure Google Drive OAuth2 and Slack OAuth2 credentials
5. **Activate the workflow** before testing — webhook URLs only work when active
6. Open `upload_form.html` in a browser and test with a sample PDF

### Option B — Python Setup

```bash
# Install additional dependency
pip install flask

# Run the server (serves form at / and webhook at /webhook/upload-document)
python apex_doc_upload_v2.py

# Open your browser
open http://localhost:5678
```

Set `WEBHOOK_URL` in `upload_form.html` to `http://localhost:5678/webhook/upload-document` for local testing.

### Accepted File Types
- PDF (`.pdf`)
- Word Document (`.docx`)

### Document Categories
| Form Label | Internal Value |
|------------|---------------|
| Services Overview | `services` |
| Billing & Admin | `billing` |
| Client Support FAQ | `faq` |
| Compliance SOP | `compliance` |
| Vendor Management | `vendor` |
| Other | `other` |

### Security
The webhook is protected by a **shared secret header check**. Every request from the form includes an `X-Upload-Secret` header. The server rejects anything that doesn't match.

**To configure:**
1. Generate a secret: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Add `UPLOAD_SECRET=<your-secret>` to your `.env` file (Python) or n8n Environment Variables
3. Replace `UPLOAD_SECRET_HERE` in `upload_form.html` with the same value

**For full production security:** host the form behind an identity provider (Cloudflare Access, Google IAP) so only authenticated users can load the page at all.



---

*This template is maintained by [Promethean PR & Automation](mailto:tom@prometheanpr.com)*
