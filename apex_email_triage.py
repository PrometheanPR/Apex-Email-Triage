"""
Apex Operations — AI Email Triage
==================================
Standalone Python implementation of the n8n workflow: apex_email_triage.json

WHAT THIS SCRIPT DOES
----------------------
Polls a Gmail inbox for new client emails, classifies them using GPT-4o,
retrieves the relevant internal document from Google Drive, drafts a
professional reply grounded in that document, saves the draft to Gmail
(never sends automatically), logs the interaction to HubSpot (creating
or updating the contact), and notifies the correct Slack channel.

HOW TO CONFIGURE
-----------------
Copy .env.example to .env and fill in all values. Never commit .env to git.

PREREQUISITES
-------------
1. Gmail API enabled — https://console.cloud.google.com/apis/library/gmail.googleapis.com
   Download credentials.json (OAuth2 Desktop app) to this directory.
2. Google Drive API enabled — same GCP project, same credentials.json.
3. HubSpot Private App — create at app.hubspot.com with scopes:
   crm.objects.contacts.read, crm.objects.contacts.write, crm.objects.notes.write
4. Slack Bot Token (xoxb-...) — bot must be invited to #sales, #account-management, #admin
5. OpenAI API key with gpt-4o access.

PIP INSTALL
-----------
pip install openai google-auth google-auth-oauthlib google-api-python-client \
            slack-sdk python-dotenv requests schedule

RUNNING
-------
python apex_email_triage.py

The script polls every 5 minutes. Ctrl+C to stop.
For one-shot execution (e.g. cron): python apex_email_triage.py --once
"""

import os
import sys
import json
import base64
import logging
import time
import argparse
from datetime import datetime, timezone
from email.mime.text import MIMEText

import requests
import schedule
from dotenv import load_dotenv
from openai import OpenAI
from slack_sdk import WebClient as SlackClient
from slack_sdk.errors import SlackApiError

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build as google_build
import pickle

# ── Configuration ──────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Credentials from environment — see .env.example
OPENAI_API_KEY      = os.environ["OPENAI_API_KEY"]
HUBSPOT_TOKEN       = os.environ["HUBSPOT_TOKEN"]
SLACK_BOT_TOKEN     = os.environ["SLACK_BOT_TOKEN"]
HUBSPOT_PORTAL_ID   = os.environ.get("HUBSPOT_PORTAL_ID", "YOUR_PORTAL_ID")

# Google Drive internal document IDs (from brief)
GDRIVE_FOLDER_ID         = "13LPG5a-EBYRlxurpI1b0P_1TsAOE5CCn"
DOC_SERVICES_OVERVIEW    = "1esIaycXlDZz5lE-1zILm8QOggDiWR6rp3jG23-5ChRM"
DOC_BILLING              = "1Z396LRKYmC1ENInWY-X946U3NL5igobQ2-yhjrJo2n4"
DOC_FAQ                  = "1thH0AsJQaDpaTo9JgNhXbN6YUlrYZk6WKcu5WFm4aJ4"

# Gmail label to poll (falls back to INBOX if label not found)
GMAIL_LABEL = "Client Incoming"

# Slack channel routing
SLACK_CHANNEL_MAP = {
    "sales":           "#sales",
    "account_manager": "#account-management",
    "admin":           "#admin",
}

# Google API scopes
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ── Google Auth ────────────────────────────────────────────────

def get_google_service(service_name: str, version: str):
    """
    Authenticates with Google using OAuth2 and returns a service client.
    On first run, opens a browser for user consent and saves token.pickle.
    On subsequent runs, loads the saved token (refreshing if expired).
    """
    creds = None
    token_path = "token.pickle"
    creds_path = "credentials.json"

    if os.path.exists(token_path):
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, GOOGLE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "wb") as f:
            pickle.dump(creds, f)

    return google_build(service_name, version, credentials=creds)


# ── Node 1: Gmail Trigger ──────────────────────────────────────

def poll_gmail_for_new_emails(gmail_service) -> list[dict]:
    """
    Polls Gmail for unread emails in the 'Client Incoming' label (or INBOX).
    Marks each email as read after retrieval so it isn't processed twice.
    Maps to: Gmail Trigger node in n8n workflow.
    """
    # Find the label ID for 'Client Incoming'
    label_id = "INBOX"  # fallback
    try:
        labels_result = gmail_service.users().labels().list(userId="me").execute()
        for label in labels_result.get("labels", []):
            if label["name"].lower() == GMAIL_LABEL.lower():
                label_id = label["id"]
                break
    except Exception as e:
        log.warning(f"Could not fetch Gmail labels, falling back to INBOX: {e}")

    # Fetch unread messages in the label
    result = gmail_service.users().messages().list(
        userId="me",
        labelIds=[label_id, "UNREAD"],
        maxResults=10
    ).execute()

    messages = result.get("messages", [])
    emails = []

    for msg_stub in messages:
        msg = gmail_service.users().messages().get(
            userId="me", id=msg_stub["id"], format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
        body = _extract_gmail_body(msg["payload"])

        emails.append({
            "message_id": msg_stub["id"],
            "sender_email": _parse_email_address(headers.get("from", "")),
            "sender_name":  _parse_display_name(headers.get("from", "")),
            "subject":      headers.get("subject", "(no subject)"),
            "body":         body,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        })

        # Mark as read so we don't process it again
        gmail_service.users().messages().modify(
            userId="me", id=msg_stub["id"],
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()

    log.info(f"Fetched {len(emails)} new email(s) from Gmail.")
    return emails


def _extract_gmail_body(payload: dict) -> str:
    """Recursively extracts plain text body from Gmail message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    for part in payload.get("parts", []):
        result = _extract_gmail_body(part)
        if result:
            return result
    return ""


def _parse_email_address(from_header: str) -> str:
    """Extracts the email address from a 'From' header string."""
    if "<" in from_header:
        return from_header.split("<")[1].rstrip(">").strip()
    return from_header.strip()


def _parse_display_name(from_header: str) -> str:
    """Extracts the display name from a 'From' header string."""
    if "<" in from_header:
        return from_header.split("<")[0].strip().strip('"')
    return from_header.split("@")[0].strip()


# ── Node 2: AI Categorize Email ────────────────────────────────

def ai_categorize_email(email: dict, openai_client: OpenAI) -> dict:
    """
    Sends the email to GPT-4o for classification.
    Returns a dict with: category, priority, summary, route_to.
    Raises ValueError if the LLM returns malformed JSON.
    Maps to: AI Categorize Email + Parse AI Response nodes in n8n workflow.
    """
    system_prompt = (
        "You are an email triage assistant for a B2B operations consulting firm. "
        "Analyze the email and return ONLY a valid JSON object with these exact fields: "
        '{"category": "new_inquiry|client_support|billing|escalation", '
        '"priority": "critical|high|medium|low", '
        '"summary": "max 15 words describing the request", '
        '"route_to": "sales|account_manager|admin"}. Return no other text.'
    )
    user_message = (
        f"From: {email['sender_name']} <{email['sender_email']}>\n"
        f"Subject: {email['subject']}\n"
        f"Body:\n{email['body']}"
    )

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ]
    )

    raw = response.choices[0].message.content.strip()

    # Validate the response is proper JSON with expected fields
    try:
        triage = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"MALFORMED_AI_JSON: {raw}") from e

    valid_categories = {"new_inquiry", "client_support", "billing", "escalation"}
    valid_priorities = {"critical", "high", "medium", "low"}
    valid_routes     = {"sales", "account_manager", "admin"}

    if triage.get("category") not in valid_categories:
        raise ValueError(f"INVALID_CATEGORY: {triage.get('category')}")
    if triage.get("priority") not in valid_priorities:
        raise ValueError(f"INVALID_PRIORITY: {triage.get('priority')}")
    if triage.get("route_to") not in valid_routes:
        raise ValueError(f"INVALID_ROUTE: {triage.get('route_to')}")

    log.info(f"Categorized: {triage['category']} / {triage['priority']} → {triage['route_to']}")
    return triage


# ── Nodes 3/4a/4b: Document Retrieval ─────────────────────────

def select_and_fetch_document(category: str, drive_service) -> str:
    """
    Selects the correct internal document based on email category,
    then downloads its plain-text content from Google Drive.
    Maps to: Is New Inquiry? + Get Services Overview Doc +
             Select Doc by Category + Get Category Doc nodes in n8n workflow.
    """
    # Map category to document ID (mirrors the n8n IF/Switch logic)
    doc_map = {
        "new_inquiry":    DOC_SERVICES_OVERVIEW,
        "billing":        DOC_BILLING,
        "escalation":     DOC_FAQ,
        "client_support": DOC_FAQ,
    }

    file_id = doc_map.get(category)
    if not file_id:
        log.warning(f"No document mapped for category '{category}', using fallback text.")
        return "Documentation not found — please draft this response manually referencing your internal SOPs."

    try:
        # Export Google Doc as plain text
        content = drive_service.files().export(
            fileId=file_id, mimeType="text/plain"
        ).execute()
        text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)
        log.info(f"Retrieved document for category '{category}' ({len(text)} chars).")
        return text[:8000]  # cap at 8k chars for context window
    except Exception as e:
        log.error(f"Google Drive fetch failed for file {file_id}: {e}")
        return "Documentation not found — please draft this response manually referencing your internal SOPs."


# ── Node 5: Draft Email Response ───────────────────────────────

def draft_email_response(email: dict, doc_content: str, openai_client: OpenAI) -> str:
    """
    Calls GPT-4o to draft a professional email response grounded in the
    retrieved internal document. Prefixes the draft with [DRAFT FOR REVIEW].
    Maps to: Draft Email Response node in n8n workflow.
    """
    system_prompt = (
        "You are a professional consultant drafting a client email response on behalf of "
        "Apex Operations Consulting. Use only the internal documentation excerpt provided — "
        "do not invent facts not present in the document. Write in a warm, professional tone. "
        "Keep the response under 200 words. End with a clear next step or call to action. "
        "This draft will be reviewed by a human before sending — label the top of the "
        "response with [DRAFT FOR REVIEW]."
    )
    user_message = (
        f"Client email from {email['sender_name']}:\n{email['body']}\n\n"
        f"Relevant internal documentation:\n{doc_content}"
    )

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ]
    )

    draft = response.choices[0].message.content
    log.info("Draft response generated.")
    return draft


# ── Node 6: Gmail Create Draft ────────────────────────────────

def create_gmail_draft(email: dict, draft_text: str, gmail_service) -> str:
    """
    Saves the AI-generated draft to Gmail Drafts folder.
    NEVER sends the email — human review is required before sending.
    Maps to: Create Gmail Draft node in n8n workflow.
    """
    message = MIMEText(draft_text)
    message["to"]      = email["sender_email"]
    message["subject"] = f"Re: {email['subject']}"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    draft = gmail_service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw}}
    ).execute()

    log.info(f"Gmail draft created (id: {draft['id']}) — NOT sent. Awaiting human review.")
    return draft["id"]


# ── Node 7: HubSpot Search Contact ────────────────────────────

def hubspot_search_contact(sender_email: str) -> str | None:
    """
    Searches HubSpot CRM for an existing contact by email address.
    Returns the contact ID string if found, None if not found.
    Maps to: HubSpot Search Contact + Extract Contact ID nodes in n8n workflow.
    """
    url = "https://api.hubapi.com/crm/v3/objects/contacts/search"
    headers = {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }
    payload = {
        "filterGroups": [{"filters": [
            {"propertyName": "email", "operator": "EQ", "value": sender_email}
        ]}],
        "properties": ["email", "firstname", "lastname", "hs_object_id"],
        "limit": 1,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            contact_id = results[0]["id"]
            log.info(f"HubSpot contact found: {contact_id}")
            return contact_id
    except Exception as e:
        log.error(f"HubSpot search failed: {e}")

    log.info("No existing HubSpot contact found.")
    return None


# ── Node 9a: HubSpot Update Existing Contact ──────────────────

def hubspot_update_contact(contact_id: str, category: str, note_body: str):
    """
    Updates an existing HubSpot contact's last_inquiry_type property
    and creates a triage log note on their record.
    Maps to: Update HubSpot Contact + Create Note (Existing) nodes in n8n workflow.
    """
    headers = {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }

    # Update the last_inquiry_type property
    try:
        resp = requests.patch(
            f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
            headers=headers,
            json={"properties": {"last_inquiry_type": category}},
            timeout=10
        )
        resp.raise_for_status()
        log.info(f"Updated HubSpot contact {contact_id} last_inquiry_type={category}")
    except Exception as e:
        log.error(f"HubSpot contact update failed: {e}")

    # Create the triage note
    _hubspot_create_note(contact_id, note_body, headers)


# ── Node 9b: HubSpot Create New Contact ───────────────────────

def hubspot_create_contact(email: dict, category: str, note_body: str) -> str:
    """
    Creates a new HubSpot contact for first-time senders,
    then attaches a triage log note to the new record.
    Maps to: Create HubSpot Contact + Create Note (New) nodes in n8n workflow.
    """
    headers = {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }

    name_parts = email["sender_name"].strip().split(" ", 1)
    first_name = name_parts[0]
    last_name  = name_parts[1] if len(name_parts) > 1 else ""

    try:
        resp = requests.post(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers=headers,
            json={"properties": {
                "email":             email["sender_email"],
                "firstname":         first_name,
                "lastname":          last_name,
                "last_inquiry_type": category,
            }},
            timeout=10
        )
        resp.raise_for_status()
        contact_id = resp.json()["id"]
        log.info(f"Created new HubSpot contact: {contact_id}")
        _hubspot_create_note(contact_id, note_body, headers)
        return contact_id
    except Exception as e:
        log.error(f"HubSpot contact creation failed: {e}")
        return "unknown"


def _hubspot_create_note(contact_id: str, note_body: str, headers: dict):
    """Creates a HubSpot note associated with a contact."""
    try:
        resp = requests.post(
            "https://api.hubapi.com/crm/v3/objects/notes",
            headers=headers,
            json={
                "properties": {"hs_note_body": note_body},
                "associations": [{"to": {"id": contact_id}, "types": [
                    {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}
                ]}],
            },
            timeout=10
        )
        resp.raise_for_status()
        log.info(f"HubSpot note created for contact {contact_id}.")
    except Exception as e:
        log.error(f"HubSpot note creation failed: {e}")


# ── Node 10: Slack Notification ───────────────────────────────

def send_slack_notification(email: dict, triage: dict, contact_id: str, slack_client: SlackClient):
    """
    Sends a triage summary notification to the correct Slack channel
    based on the route_to value. Continues on failure — Gmail draft
    and HubSpot note are the primary records.
    Maps to: Route to Slack Channel + Slack notification nodes in n8n workflow.
    """
    channel = SLACK_CHANNEL_MAP.get(triage["route_to"], "#admin")
    hubspot_url = (
        f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/contact/{contact_id}"
    )
    message = (
        f":email: *New client email triaged*\n"
        f"*From:* {email['sender_name']} <{email['sender_email']}>\n"
        f"*Category:* {triage['category']} | *Priority:* {triage['priority']}\n"
        f"*Summary:* {triage['summary']}\n"
        f"*HubSpot:* {hubspot_url}\n"
        f"*Action required:* Gmail draft is ready for your review."
    )

    try:
        slack_client.chat_postMessage(channel=channel, text=message)
        log.info(f"Slack notification sent to {channel}.")
    except SlackApiError as e:
        # Non-fatal — log only, do not re-raise
        log.error(f"Slack notification failed (non-fatal): {e.response['error']}")


# ── Error Handler ──────────────────────────────────────────────

def send_triage_error_alert(sender_email: str, subject: str, error: str, slack_client: SlackClient):
    """
    Fires when AI categorization returns malformed JSON.
    Sends an alert to #admin for manual review.
    Maps to: AI Parse Error — Slack Alert node in n8n workflow.
    """
    try:
        slack_client.chat_postMessage(
            channel="#admin",
            text=(
                f":warning: *Email triage failed — malformed AI response.*\n"
                f"Manual review needed for email from: {sender_email}\n"
                f"Subject: {subject}\n"
                f"Error: {error}"
            )
        )
        log.error(f"Triage error alert sent to #admin for {sender_email}.")
    except SlackApiError as e:
        log.error(f"Could not send error alert to Slack: {e.response['error']}")


# ── Main Orchestrator ──────────────────────────────────────────

def process_emails():
    """
    Main orchestration function — runs one full triage cycle.
    Initialises all service clients, polls Gmail, and processes
    each new email through the full pipeline.
    """
    log.info("── Starting email triage cycle ──")

    # Initialise clients
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    slack_client  = SlackClient(token=SLACK_BOT_TOKEN)
    gmail_service = get_google_service("gmail", "v1")
    drive_service = get_google_service("drive", "v3")

    # Poll Gmail for new emails
    emails = poll_gmail_for_new_emails(gmail_service)

    if not emails:
        log.info("No new emails. Waiting for next cycle.")
        return

    for email in emails:
        log.info(f"Processing: '{email['subject']}' from {email['sender_email']}")

        # ── Step 1: AI categorization ──────────────────────────
        try:
            triage = ai_categorize_email(email, openai_client)
        except ValueError as e:
            send_triage_error_alert(email["sender_email"], email["subject"], str(e), slack_client)
            continue  # Skip this email — alert sent, move to next

        # ── Step 2: Retrieve relevant internal document ────────
        doc_content = select_and_fetch_document(triage["category"], drive_service)

        # ── Step 3: Draft the response ─────────────────────────
        draft_text = draft_email_response(email, doc_content, openai_client)

        # ── Step 4: Save to Gmail Drafts (NEVER auto-send) ─────
        create_gmail_draft(email, draft_text, gmail_service)

        # ── Step 5: HubSpot CRM logging ────────────────────────
        note_body = (
            f"AI Email Triage Log\n"
            f"Date: {email['timestamp']}\n"
            f"From: {email['sender_name']} <{email['sender_email']}>\n"
            f"Category: {triage['category']} | Priority: {triage['priority']}\n"
            f"Summary: {triage['summary']}\n"
            f"Routed to: {triage['route_to']}\n\n"
            f"AI Draft:\n{draft_text}"
        )

        contact_id = hubspot_search_contact(email["sender_email"])

        if contact_id:
            # Existing contact — update and log
            hubspot_update_contact(contact_id, triage["category"], note_body)
        else:
            # New contact — create and log
            contact_id = hubspot_create_contact(email, triage["category"], note_body)

        # ── Step 6: Slack notification ─────────────────────────
        send_slack_notification(email, triage, contact_id, slack_client)

    log.info("── Triage cycle complete ──")


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apex Operations — AI Email Triage")
    parser.add_argument("--once", action="store_true", help="Run once and exit (for cron)")
    args = parser.parse_args()

    if args.once:
        process_emails()
    else:
        log.info("Starting scheduler — polling every 5 minutes. Ctrl+C to stop.")
        schedule.every(5).minutes.do(process_emails)
        process_emails()  # Run immediately on start
        while True:
            schedule.run_pending()
            time.sleep(30)
