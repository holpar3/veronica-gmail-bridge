# Veronica Gmail Bridge

Scoped Google access for the home (Veronica 2.0). Sibling to the OneDrive bridge.

## What it can do
- read + search the Battalion inbox
- create email **drafts** (never sends — there is no send route)
- read the calendar
- create events on the **Battalion calendar only**

## Deploy (Render)
1. New repo: `holpar3/veronica-gmail-bridge` — add `main.py` + `requirements.txt`.
2. New Render Web Service from the repo. Runtime: Python.
3. Start command:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
4. Environment variables:
   - `GOOGLE_CLIENT_ID` — from the downloaded client JSON
   - `GOOGLE_CLIENT_SECRET` — from the downloaded client JSON
   - `GOOGLE_REFRESH_TOKEN` — printed by `google_auth.py`
   - `BRIDGE_API_KEY` — a random string you generate; the home sends it in `X-Bridge-Key`
   - `BATTALION_CALENDAR_ID` — `primary` (the Battalion account's own calendar)
5. Deploy, then check `GET /health` → `{"ok": true}`.

## Calling it
All routes except `/health` require header `X-Bridge-Key: <BRIDGE_API_KEY>`.

- `GET  /health`
- `GET  /email/search?q=is:unread&max=10`
- `GET  /email/message/{id}`
- `POST /email/draft`            body: `{to, subject, body}`
- `GET  /calendar/events?days=7`
- `POST /calendar/event`         body: `{summary, start, end, description?, location?}`  (start/end ISO 8601)

## Safety (by construction)
- No send endpoint exists → cannot send mail.
- `/calendar/event` is hardwired to `BATTALION_CALENDAR_ID` → cannot touch family/personal calendars.
- Public Render URL is gated by `BRIDGE_API_KEY`.
