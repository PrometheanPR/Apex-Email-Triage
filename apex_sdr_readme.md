# Apex SDR Pipeline — Patent Outreach

**Built with n8n · USPTO PatentsView · Apollo · Instantly · Google Sheets · Calendly**

*An automated sales development pipeline for service businesses targeting independent inventors and patent attorneys.*

---

## What This System Does

Runs weekly and fully autonomously. For every run:

1. **Discovers** unassigned U.S. utility patents from the USPTO PatentsView API — patents not yet owned by any company.
2. **Extracts** six fields per patent: inventor name, attorney name, patent number, one-line summary, location, and filing date. Logs all results to Google Sheets.
3. **Enriches** each inventor via Apollo to find their email address and LinkedIn URL.
4. **Scores** enrichment confidence on a 0–100 scale based on email presence, verification status, LinkedIn availability, and profile richness.
5. **Routes** each lead into one of three tracks:
   - `inventor_found` (score ≥ 70) — enters the inventor outreach sequence in Instantly
   - `attorney_fallback` (score 40–69) — Apollo enriches the patent attorney instead; if email found, enters attorney sequence
   - `needs_review` (score < 40) — flagged in the Sheet for manual review; never entered into a sequence automatically
6. **Logs** every decision back to the Google Sheet with enrichment data, confidence score, route, and sequence name.
7. **Books calls** — when a lead replies positively in Instantly, a second webhook workflow fires, updates the Sheet row to `call_booked`, and logs the Calendly booking link.

> No human involvement is required until a Zoom appointment lands in the calendar.

---

## Files in This Repository

| File | Purpose |
|------|---------|
| `apex_sdr_pipeline.json` | Main n8n workflow — patent discovery, enrichment, routing, outreach |
| `apex_sdr_booking.json` | Booking workflow — triggered by Instantly reply webhook |
| `apex_sdr_readme.md` | This file |
| `.env.example` | Environment variable template (copy to `.env`, never commit) |

---

## Google Sheet Structure

Create a Google Sheet with these exact column headers (order matters for the update nodes):

| Column | Description |
|--------|-------------|
| Patent Number | USPTO patent ID — used as the unique row key |
| Inventor Name | Full name from PatentsView |
| Attorney Name | Full name from PatentsView |
| Summary | Patent title / one-line description |
| Location | Inventor city, state |
| Filing Date | Patent grant date from USPTO |
| Inventor Email | Found by Apollo enrichment |
| Inventor LinkedIn | Found by Apollo enrichment |
| Confidence Score | 0–100 enrichment quality score |
| Route | `inventor_found` / `attorney_fallback` / `needs_review` |
| Sequence | Which Instantly campaign the lead entered |
| Status | `pending` → `in_sequence` → `✅ Call Booked` / `⚠️ Needs Manual Review` |
| Notes | Enrichment signals and audit trail |

---

## Confidence Scoring

| Signal | Points |
|--------|--------|
| Email address found | +40 |
| Email verified by Apollo | +20 |
| LinkedIn URL found | +20 |
| Apollo profile has > 3 data points | +10 |
| Inventor location matches Apollo record | +10 |
| **Maximum** | **100** |

**Thresholds:**
- **≥ 70** → `inventor_found` — high confidence, enter inventor sequence
- **40–69** → `attorney_fallback` — moderate confidence, fall back to attorney
- **< 40** → `needs_review` — low confidence, flag for manual review

To adjust thresholds, edit the Score Confidence Code node in `apex_sdr_pipeline.json`.

---

## Setup — Step by Step

### 1. PatentsView API
- Register at [patentsview.org/api/doc](https://patentsview.org/api/doc)
- No API key required for basic usage
- If you hit rate limits, add an API key header to the **Query PatentsView API** node

### 2. Google Sheets
- Create a new Google Sheet with the columns listed above
- Copy the Sheet ID from the URL: `https://docs.google.com/spreadsheets/d/SHEET_ID_HERE/edit`
- Replace `DEMO_SHEET_ID` in every Google Sheets node with your Sheet ID
- Connect your Google Sheets OAuth2 credential and replace `DEMO_SHEETS_CREDENTIAL_ID`

### 3. Apollo API Key
- Log in to Apollo → Settings → API Keys → Create Key
- Replace `DEMO_APOLLO_API_KEY` in the **Enrich Inventor via Apollo** and **Enrich Attorney via Apollo** nodes

### 4. Instantly
- Log in to Instantly → Settings → API → Copy your API key
- Replace `DEMO_INSTANTLY_API_KEY` in both Instantly HTTP Request nodes
- Create two campaigns: one for inventors, one for attorneys (different tone and cadence)
- Replace `DEMO_INVENTOR_CAMPAIGN_ID` and `DEMO_ATTORNEY_CAMPAIGN_ID` with your actual campaign IDs

### 5. Booking Workflow (apex_sdr_booking.json)
- Import this workflow separately in n8n
- Activate it — the Production webhook URL becomes live
- In Instantly → Settings → Webhooks → Add Webhook → paste the Production URL
- Select event type: **Reply** or **Meeting Booked**
- Replace `DEMO_CALENDLY_BOOKING_URL` in the **Update Sheet — Call Booked** node with your Calendly or Cal.com link

### 6. Activate
- Import `apex_sdr_pipeline.json` via n8n → Workflows → Import
- Swap all `DEMO_` placeholders
- Click **Publish** — the schedule goes live immediately

---

## How to Maintain This System

### Changing outreach sequences
Update campaign IDs in the two Instantly HTTP Request nodes. No other changes needed.

### Adjusting discovery criteria
Edit the query body in the **Query PatentsView API** node. PatentsView supports filtering by patent type, date range, inventor state, and more. See [patentsview.org/api/doc](https://patentsview.org/api/doc).

### Adjusting confidence thresholds
Edit the `if (score >= 70)` and `if (score >= 40)` thresholds in the **Score Confidence** Code node.

### Adding Clay as a secondary enrichment source
After the **Score Confidence** node, add an IF node: if score is between 40–69, call Clay's API before routing to attorney fallback. Merge the Clay result back and re-score before the Route Lead switch.

### Rate limiting
If Apollo returns 429 errors, add a **Wait** node (set to 2–3 seconds) between the **Enrich Inventor via Apollo** node and the **Score Confidence** node.

---

## Sender Reputation Protection

The confidence scoring layer is the primary protection for your Instantly sender reputation:

- Records scoring below 40 are **never entered into any sequence automatically**
- They appear in the Sheet with `⚠️ Needs Manual Review` status for a human to inspect
- The `skip_if_in_workspace: true` flag in both Instantly API calls prevents duplicate entries if the workflow runs again

---

*This template is maintained as a neutral starting point for service-based businesses. Replace all placeholder credentials, campaign IDs, and sheet IDs before production use.*
