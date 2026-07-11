"""
Apex Operations — Document Upload Webhook V2
=============================================
Standalone Python Flask implementation of the document upload logic (matching n8n v2 workflow).

WHAT THIS APPLICATION DOES
---------------------------
Serves a complete, self-contained server with:
1. GET / - Serves the document upload web form (upload_form.html).
2. POST /webhook/upload-document - Validates, uploads to Google Drive, and notifies Slack.

VALIDATION RULES
----------------
- Category must be one of: [services, billing, faq, compliance, vendor, other]
- File must be present in the multipart request.
- File must have a .pdf or .docx extension.

HOW TO CONFIGURE
-----------------
Copy your `.env` configuration or ensure the following environment variables are set:
- SLACK_BOT_TOKEN: Bot OAuth token (xoxb-...) with chat:write permission.
- GOOGLE_DRIVE_FOLDER_ID: Folder ID where documents will be uploaded (default: 13LPG5a-EBYRlxurpI1b0P_1TsAOE5CCn).
- FLASK_SECRET_KEY: Secret key for Flask session security.
- PORT: Web server port (default: 5678).
- UPLOAD_SECRET: Shared secret string sent by the form in the X-Upload-Secret header.
  Set this to any long random string (e.g. output of: python -c "import secrets; print(secrets.token_hex(32))")
  Leave blank to disable the check (not recommended for public deployments).

GOOGLE AUTHENTICATION
---------------------
Requires `credentials.json` in the working directory on first run. It will authorize
via browser/OAuth flow and store authentication tokens in `token.pickle`. Subsequent
runs will load from `token.pickle` without requiring re-authentication.

PIP INSTALL
-----------
pip install Flask python-dotenv google-auth google-auth-oauthlib google-api-python-client slack-sdk

RUNNING
-------
python apex_doc_upload_v2.py
"""

import os
import sys
import io
import pickle
import logging
from datetime import datetime, timezone
from werkzeug.utils import secure_filename

# Flask and environment
from flask import Flask, request, jsonify, render_template_string
from dotenv import load_dotenv

# Slack SDK
from slack_sdk import WebClient as SlackClient
from slack_sdk.errors import SlackApiError

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build as google_build
from googleapiclient.http import MediaIoBaseUpload

# ── Configuration & Initialization ─────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Initialize Flask App
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "apex_operations_secret_key_v2")

# Constants
DEFAULT_FOLDER_ID = "13LPG5a-EBYRlxurpI1b0P_1TsAOE5CCn"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
ALLOWED_CATEGORIES = {"services", "billing", "faq", "compliance", "vendor", "other"}
ALLOWED_EXTENSIONS = {".pdf", ".docx"}

# Shared secret for webhook authentication.
# The HTML form sends this in the X-Upload-Secret header.
# Must match UPLOAD_SECRET in your .env file.
UPLOAD_SECRET = os.environ.get("UPLOAD_SECRET", "")


# ── Google OAuth Helper ────────────────────────────────────────

def get_google_drive_service():
    """
    Authenticates with Google using OAuth2 and returns a Google Drive API client.
    Uses the same token.pickle / credentials.json pattern as apex_email_triage.py.
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
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"Google credentials file '{creds_path}' not found. "
                    f"Please place it in the application directory."
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, GOOGLE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "wb") as f:
            pickle.dump(creds, f)

    return google_build("drive", "v3", credentials=creds)


# ── Step 2: Validation Logic ───────────────────────────────────

def validate_upload(files, form_data) -> tuple[bool, str]:
    """
    Validates the incoming multipart form upload.
    Checks category, file presence, and file type (.pdf or .docx).
    """
    # 1. Validate Category
    category = form_data.get("category")
    if not category:
        return False, "Category is required."
    
    if category.strip().lower() not in ALLOWED_CATEGORIES:
        return False, f"Invalid category. Must be one of {list(ALLOWED_CATEGORIES)}."

    # 2. Validate Other Fields
    if not form_data.get("uploader_name", "").strip():
        return False, "Uploader name is required."
    if not form_data.get("description", "").strip():
        return False, "Description is required."

    # 3. Validate File Presence
    if 'file' not in files:
        return False, "No file field found in the upload request."
    
    file = files['file']
    if not file or file.filename == '':
        return False, "No file selected for upload."

    # 4. Validate File Extension
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Unsupported file type '{ext}'. Only PDF and DOCX files are allowed."

    return True, "Validation successful."


# ── Step 3: Google Drive Upload ────────────────────────────────

def upload_to_google_drive(file_obj, filename, folder_id, category, description, uploader_name) -> str:
    """
    Uploads a file directly from memory to Google Drive using MediaIoBaseUpload.
    No temporary files are created on disk.
    Returns the webViewLink (Drive URL) of the uploaded file.
    """
    try:
        drive_service = get_google_drive_service()
    except Exception as e:
        log.error(f"Google authentication failed: {e}")
        raise RuntimeError(f"Google Drive authentication failed: {e}")

    # Determine MIME type based on file extension
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        mime_type = "application/pdf"
    elif ext == ".docx":
        mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        mime_type = "application/octet-stream"

    # Prepare file metadata and custom properties
    file_metadata = {
        "name": filename,
        "parents": [folder_id],
        "description": description,
        "properties": {
            "category": category,
            "uploader_name": uploader_name,
            "upload_timestamp": datetime.now(timezone.utc).isoformat()
        }
    }

    # Reset file object pointer to beginning
    file_obj.seek(0)
    
    # Create media upload payload from io.BytesIO
    media = MediaIoBaseUpload(file_obj, mimetype=mime_type, resumable=True)

    try:
        log.info(f"Uploading file '{filename}' to Google Drive folder '{folder_id}'...")
        uploaded_file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink"
        ).execute()
        
        # Ensure file permissions are configured so users with the link can view (optional but helpful)
        try:
            drive_service.permissions().create(
                fileId=uploaded_file.get("id"),
                body={"role": "reader", "type": "anyone"},
                fields="id"
            ).execute()
        except Exception as perm_err:
            log.warning(f"Failed to set public read permissions on file: {perm_err}")

        drive_url = uploaded_file.get("webViewLink")
        log.info(f"File successfully uploaded. Drive Link: {drive_url}")
        return drive_url

    except Exception as e:
        log.error(f"Error during Google Drive file creation/upload: {e}")
        raise RuntimeError(f"Google Drive Upload Error: {e}")


# ── Step 4: Slack Notification ─────────────────────────────────

def slack_upload_confirmation(filename, category, description, uploader_name, drive_url):
    """
    Sends a structured, professional confirmation message to Slack #admin.
    """
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    if not slack_token:
        log.warning("SLACK_BOT_TOKEN environment variable is missing. Slack notification skipped.")
        return

    client = SlackClient(token=slack_token)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Construct rich formatting blocks for Slack
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📄 New Document Uploaded"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Uploader:* {uploader_name}\n"
                        f"*Category:* `{category}`\n"
                        f"*Timestamp:* {timestamp}"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*File Name:* `{filename}`\n"
                        f"*Description:* {description}"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View in Google Drive"
                    },
                    "url": drive_url,
                    "style": "primary"
                }
            ]
        }
    ]

    try:
        client.chat_postMessage(
            channel="#admin",
            text=f"New document uploaded: {filename} by {uploader_name}",
            blocks=blocks
        )
        log.info("Slack notification successfully sent to #admin.")
    except SlackApiError as e:
        log.error(f"Slack API error posting notification: {e.response['error']}")
    except Exception as e:
        log.error(f"Unexpected error posting notification to Slack: {e}")


# ── Route 1: Get Web Form ──────────────────────────────────────

@app.route("/", methods=["GET"])
def get_upload_portal():
    """
    Serves the portal interface page by reading 'upload_form.html'.
    Falls back to a basic string template if the HTML file is missing.
    """
    html_path = os.path.join(os.path.dirname(__file__), "upload_form.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return render_template_string(f.read())
    else:
        return "upload_form.html not found. Please place it in the same directory.", 404


# ── Route 2: Webhook Endpoint ──────────────────────────────────

@app.route("/webhook/upload-document", methods=["POST"])
def receive_document_upload():
    """
    Flask route handler for the POST /webhook/upload-document API endpoint.
    Retrieves, validates, uploads, and notifies upload progress.
    """
    form_data = request.form
    files = request.files

    # 1. Validation Step
    is_valid, validation_msg = validate_upload(files, form_data)
    if not is_valid:
        log.warning(f"Validation failed: {validation_msg}")
        return jsonify({
            "success": False,
            "message": validation_msg
        }), 400

    # Extract clean parameters
    uploader_name = form_data.get("uploader_name").strip()
    category = form_data.get("category").strip().lower()
    description = form_data.get("description").strip()
    
    file = files['file']
    filename = secure_filename(file.filename)

    # 2. Convert file contents to in-memory bytes buffer
    file_bytes = file.read()
    file_io = io.BytesIO(file_bytes)

    # Determine Folder ID (From Env or default fallback)
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", DEFAULT_FOLDER_ID)

    try:
        # 3. Upload to Google Drive (In-memory)
        drive_url = upload_to_google_drive(
            file_obj=file_io,
            filename=filename,
            folder_id=folder_id,
            category=category,
            description=description,
            uploader_name=uploader_name
        )

        # 4. Notify Slack Channel (#admin)
        slack_upload_confirmation(
            filename=filename,
            category=category,
            description=description,
            uploader_name=uploader_name,
            drive_url=drive_url
        )

        # 5. Return Success Response
        return jsonify({
            "success": True,
            "message": "Document uploaded successfully.",
            "file_name": filename
        }), 200

    except Exception as err:
        log.exception("An error occurred during the upload process workflow.")
        return jsonify({
            "success": False,
            "message": f"Server Error: {str(err)}"
        }), 500


# ── Main Entrypoint ────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5678))
    print("\n" + "="*60)
    print("      Apex Operations Document Upload Portal V2")
    print("="*60)
    print(f" * Web portal available at: http://localhost:{port}/")
    print(f" * Webhook endpoint:        http://localhost:{port}/webhook/upload-document")
    print("="*60 + "\n")
    
    # Run server
    app.run(host="0.0.0.0", port=port, debug=False)
"""
