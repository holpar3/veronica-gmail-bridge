"""
Veronica Gmail + Calendar Bridge
--------------------------------
A small FastAPI service that gives the home (Veronica 2.0) scoped access to the
Battalion Google account:

  - read + search email
  - create email DRAFTS  (NEVER send -- there is deliberately no send endpoint)
  - read calendar
  - create events on the BATTALION calendar ONLY

Two safety facts that are enforced by construction, not by instruction:
  1. There is no /email/send route. The bridge physically cannot send mail.
  2. /calendar/event always writes to BATTALION_CALENDAR_ID from the env and
     accepts no other calendar id, so the family + personal calendars are
     untouchable through this service.

Auth to Google:  a stored refresh token (minted once via google_auth.py).
Auth to THIS service:  a shared key in the X-Bridge-Key header, so the public
Render URL can't be used by anyone but the home.

Env vars (set in Render):
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REFRESH_TOKEN
  BRIDGE_API_KEY            random string; the home sends it in X-Bridge-Key
  BATTALION_CALENDAR_ID     default "primary"
"""

import os
import base64
import datetime
from email.mime.text import MIMEText

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
BRIDGE_API_KEY = os.environ["BRIDGE_API_KEY"]
CAL_ID = os.environ.get("BATTALION_CALENDAR_ID", "primary")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

app = FastAPI(title="Veronica Gmail + Calendar Bridge")

# Open WebUI fetches the OpenAPI spec from the browser (cross-origin) with an
# auth header, which triggers a CORS preflight. Without this, the home's
# "Add Connection" test fails before it can even read the spec.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _creds():
    return Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def gmail():
    return build("gmail", "v1", credentials=_creds(), cache_discovery=False)


def calendar():
    return build("calendar", "v3", credentials=_creds(), cache_discovery=False)


def require_key(authorization: str = Header(None), x_bridge_key: str = Header(None)):
    # Accept either Open WebUI's Bearer header OR the X-Bridge-Key the test
    # scripts and README contract use. Either one matching is enough.
    if authorization == f"Bearer {BRIDGE_API_KEY}" or x_bridge_key == BRIDGE_API_KEY:
        return
    raise HTTPException(status_code=401, detail="bad or missing credentials")


# ---------------- health ----------------
@app.get("/health")
def health():
    return {"ok": True, "service": "veronica-gmail-bridge"}


# ---------------- email ----------------
def _header(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _plain_body(payload):
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_body(part)
        if text:
            return text
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    return ""


@app.get("/email/search")
def email_search(q: str = "", max: int = 10, _=Depends(require_key)):
    svc = gmail()
    res = svc.users().messages().list(userId="me", q=q, maxResults=max).execute()
    out = []
    for m in res.get("messages", []):
        full = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        h = full.get("payload", {}).get("headers", [])
        out.append({
            "id": m["id"],
            "from": _header(h, "From"),
            "subject": _header(h, "Subject"),
            "date": _header(h, "Date"),
            "snippet": full.get("snippet", ""),
        })
    return {"messages": out}


@app.get("/email/message/{msg_id}")
def email_message(msg_id: str, _=Depends(require_key)):
    svc = gmail()
    full = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    h = full.get("payload", {}).get("headers", [])
    return {
        "id": msg_id,
        "from": _header(h, "From"),
        "to": _header(h, "To"),
        "subject": _header(h, "Subject"),
        "date": _header(h, "Date"),
        "body": _plain_body(full.get("payload", {})),
    }


class Draft(BaseModel):
    to: str
    subject: str
    body: str


@app.post("/email/draft")
def email_draft(d: Draft, _=Depends(require_key)):
    svc = gmail()
    msg = MIMEText(d.body)
    msg["to"] = d.to
    msg["subject"] = d.subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = svc.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}).execute()
    return {"draft_id": draft.get("id"), "status": "draft created (not sent)"}


# ---------------- calendar ----------------
@app.get("/calendar/events")
def calendar_events(days: int = 7, _=Depends(require_key)):
    now = datetime.datetime.utcnow().isoformat() + "Z"
    end = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat() + "Z"
    svc = calendar()
    res = svc.events().list(
        calendarId=CAL_ID, timeMin=now, timeMax=end,
        singleEvents=True, orderBy="startTime",
    ).execute()
    out = [{
        "id": e.get("id"),
        "summary": e.get("summary", ""),
        "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date")),
        "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date")),
        "location": e.get("location", ""),
    } for e in res.get("items", [])]
    return {"events": out}


class Event(BaseModel):
    summary: str
    start: str   # ISO 8601, e.g. 2026-06-20T14:00:00-04:00
    end: str
    description: str = ""
    location: str = ""


@app.post("/calendar/event")
def calendar_create(e: Event, _=Depends(require_key)):
    svc = calendar()
    body = {
        "summary": e.summary,
        "description": e.description,
        "location": e.location,
        "start": {"dateTime": e.start},
        "end": {"dateTime": e.end},
    }
    # Battalion calendar ONLY. CAL_ID is fixed from env; no other calendar
    # can be targeted through this endpoint -- family stays untouchable.
    created = svc.events().insert(calendarId=CAL_ID, body=body).execute()
    return {"event_id": created.get("id"), "calendar": CAL_ID, "link": created.get("htmlLink")}
