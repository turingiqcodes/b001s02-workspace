# Email Manager Agent

A local web app for managing Gmail, Google Calendar and Google Drive with natural-language prompts.
It is built from SRS v1.0 (3 Nov 2026).
An AI model on **Groq** (`openai/gpt-oss-120b` by default) works out what you want. Read operations run immediately. **Every change
waits for your approval**, shown as a preview card with Approve and Cancel buttons.

```
React (Vite) ──REST──▶ FastAPI backend ──▶ Groq API
                             │
                             └──HTTPS + shared secret──▶ Google Apps Script bridge ──▶ Gmail / Calendar / Drive
```

The browser only talks to the backend. The API key and the bridge secret stay in `backend/.env`.
Google credentials never leave Google: the bridge runs inside your own account.

## Project layout

| Path | What it holds |
|---|---|
| `backend/app/api/routes.py` | REST endpoints `/api/status`, `/api/prompt`, `/api/confirm`, `/api/reset` |
| `backend/app/agent/` | Groq client, system prompt, tool registry, agent loop |
| `backend/app/services/` | Gmail / Calendar / Drive logic: argument validation, parsing, previews, free-slot search |
| `backend/app/approvals.py` | The only code path that runs a write (on approval) |
| `backend/app/sessions.py` | In-memory sessions and pending actions |
| `apps_script/Code.gs` | Google Apps Script bridge using the built-in `GmailApp`, `CalendarApp` and `DriveApp` services (allow-listed operations) |
| `frontend/src/` | Prompt box, results feed, connection status, approval cards |
| `backend/tests/`, `apps_script/tests/` | Automated tests (no Google account or API key needed) |

## Setup

Prerequisites: Python 3.10+, Node 18+, a Google account, and a Groq API key (https://console.groq.com/keys).

### 1. Deploy the Apps Script bridge

1. Go to <https://script.google.com> and create a new project.
2. Replace `Code.gs` with `apps_script/Code.gs`.
3. Open **Project Settings**, tick *Show "appsscript.json" manifest file*, then paste in `apps_script/appsscript.json`.
   - Set `"timeZone"` to your own time zone. (Times themselves follow your Google Calendar's time zone.)
   - Only built-in services are used, so **nothing needs to be added under "Services"**, and you don't need a Google Cloud OAuth client.
4. Still in **Project Settings**, go to **Script properties** and add `BRIDGE_SECRET`, set to a random string of at least 16 characters.
   For example: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
5. In the function dropdown pick `doGet` and click **Run** to grant access to Gmail, Calendar and Drive. On the
   "Google hasn't verified this app" screen, choose **Advanced → Go to (project) (unsafe)**. That's expected for your own script.
6. Click **Deploy → New deployment → Web app**. Set *Execute as*: **Me** and *Who has access*: **Anyone**.
7. Copy the web app URL (it ends in `/exec`).

> Access is "Anyone" because the backend calls the bridge without a Google login. Every request
> must carry `BRIDGE_SECRET`, and only allow-listed operations run. Keep the secret private.

After editing `Code.gs`, use **Deploy → Manage deployments → Edit → New version** so the same URL serves the new code.

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env    # then fill in GROQ_API_KEY, APPS_SCRIPT_URL, APPS_SCRIPT_SECRET
python run.py           # http://127.0.0.1:8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev             # http://127.0.0.1:5173 (proxies /api to the backend)
```

Open <http://127.0.0.1:5173>. The status bar should show the AI model, Bridge and your Gmail address as connected.

### Configuration (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | – | Groq API key (starts with `gsk_`) |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Any Groq model with tool calling, e.g. `llama-3.3-70b-versatile` |
| `GROQ_REASONING_EFFORT` | `medium` | `low` / `medium` / `high` for reasoning models; empty for models without reasoning |
| `GROQ_MAX_TOKENS` | `8192` | Max output tokens per model call |
| `APPS_SCRIPT_URL` | – | Bridge web app URL |
| `APPS_SCRIPT_SECRET` | – | Must equal the `BRIDGE_SECRET` script property |
| `BRIDGE_TIMEOUT_SECONDS` | `90` | Bridge timeout (allows for Apps Script cold starts) |
| `AGENT_MAX_STEPS` | `15` | Maximum model calls per prompt |
| `EMAIL_BODY_LIMIT` / `LIST_BODY_LIMIT` / `TOOL_RESULT_LIMIT` | `2500` / `300` / `8000` | Characters of email text sent to the AI (capped at the SRS maximums 6000 / 3000 / 60000) |
| `HISTORY_CHAR_BUDGET` | `10000` | Older tool results are trimmed to keep each request under this size |

> **Groq free plan:** `openai/gpt-oss-120b` allows 8,000 tokens per minute. The defaults above keep each request
> under that. Multi-step requests may pause up to a minute while Groq's limit resets; the backend waits and retries
> automatically. On Groq's Dev tier you can raise the limits above.
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Server binding. Keep it on localhost. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed browser origins |

## Running the tests

```bash
cd backend && pytest                 # 118 tests: API, approvals, services, security, Groq request shape
cd apps_script && npm test           # 34 tests: Code.gs run against in-memory GmailApp/CalendarApp/DriveApp fakes
```

Both suites use fakes (NFR-19): an in-memory bridge, a scripted LLM, and mocked Apps Script services.

## API

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/status` | – | `llm_configured`, `llm_model`, `bridge_configured`, `gmail_connected`, `gmail_address`, `time_zone`, `status` (`ready` / `not_configured` / `bridge_error`), `detail` |
| `POST /api/prompt` | `{"prompt", "session_id"?}` | `session_id`, `response`, `pending_actions[]`, `declined_actions[]` |
| `POST /api/confirm` | `{"action_id", "approved"}` | `{"action_id", "status": "executed" \| "declined" \| "failed", "message"}` |
| `POST /api/reset` | `{"session_id"?}` | `{"status": "reset", "message": "Session cleared."}` |

Errors come back as `{"detail": "<readable message>", "code": "<code>"}` with a suitable HTTP status:
422 for invalid input, 404 for an unknown action, 409 for a busy session or an action that's no longer
pending, 502/503 for AI-model or bridge problems.

## How approval is enforced

- Tools are either **read** or **write**. A read runs straight away. Independent reads in one model turn run concurrently.
- A write tool never touches Google. The backend validates the arguments, fetches what it needs for the preview
  (current values, conflicts, attachment sizes), and stores a `PendingAction`. That action holds the **exact bridge
  call** that will run. The model is told the action is waiting for approval.
- `POST /api/confirm` is the only path that executes a write. It runs the stored call unchanged (FR-35). Approving the
  same action twice returns 409 (NFR-15).
- When the user cancels, sends a new prompt (which declines anything still pending), or an action runs or fails,
  the outcome is passed to the model at the start of its next turn (FR-36, FR-37).
- Sending, deleting a draft, and cancelling an event show a red **"Approve (can't be undone)"** button (FR-38).
- Neither the backend nor the bridge has any operation that permanently deletes mail or files (NFR-06).

## Design decisions and assumptions

These are choices I made where the SRS left room. Review them before you rely on the app.

1. **Built-in Apps Script services only** (`GmailApp`, `CalendarApp`, `DriveApp`), as requested. These need broad
   scopes: full Gmail (`https://mail.google.com/`), `calendar`, `drive` and `userinfo.email`. Google's consent screen
   will list "permanently delete" for Gmail because the scope allows it, but `Code.gs` never calls any delete-forever
   method, and a test checks this.
   Because of how these services work:
   - **Labels and restore-from-Trash apply to the whole conversation.** GmailApp has no per-message label or untrash
     methods. This matches the Gmail web app. Trashing still moves just the one message. The preview names the
     conversation and its message count. System labels supported: `INBOX` (add = move to inbox, remove = archive),
     `UNREAD`, `STARRED`, `IMPORTANT`.
   - **Moved or cancelled meetings:** CalendarApp can't send Google Calendar's own update or cancel emails. When
     "notify attendees" is on, the bridge sends a short email from your Gmail with the new time or the cancellation,
     and the preview says so. Invites for *new* events are real Calendar invites.
   - Lists (`get_latest_emails`, `search_emails`) return one entry per conversation: its latest message.
   - Only your default calendar is used for events and free/busy checks.
2. **`draft_reply` threading.** The backend works out the recipients, `Re:` subject and Cc for the preview. The bridge
   then calls `createDraftReply` on the original message, so Gmail sets `In-Reply-To` and `References` and keeps the
   draft in the same thread. "Reply all" leaves out your own address.
3. **Double-booking (FR-26).** A conflicting create or move is refused and the model is told to ask you. It may retry with
   `allow_conflict=true` only after you agree, and the preview then shows a warning. All-day and "free" (transparent)
   events don't count as conflicts.
4. **Moving events (FR-24).** If only a new start is given, the duration stays the same.
5. **Labels (FR-17).** Matching is case-insensitive. Adding a label that doesn't exist creates it, and the preview warns
   you first. Removing a label that doesn't exist is an error.
6. **Time zone.** Date-times must carry a UTC offset. Each prompt includes the current time and time zone from the
   bridge (the Apps Script project time zone).
7. **Context limits (NFR-10).** Bodies are cut to 6,000 characters for single reads, 3,000 in lists, and 60,000 for
   any whole tool result.
8. **AI model: Groq instead of Claude.** The SRS names Claude; at your request this build uses Groq only. The agent
   keeps its history in a provider-neutral block format, and `GroqClient` (`backend/app/agent/llm.py`) converts it to
   Groq's OpenAI-style chat format. If Groq rejects a malformed tool call (`tool_use_failed`), it retries up to twice.
9. **Sessions.** Sessions are in memory only and are lost when the server restarts (NFR-16). The browser keeps the
   session id in `sessionStorage`.
10. **Local only (NFR-08).** The server binds to `127.0.0.1` and has no login. Don't expose it beyond localhost
    without adding authentication.

## Not included

- **Frontend automated tests (SRS 10.3).** They were left out of this first build by request. The UI was type-checked
  and built, and the API was smoke-tested through the Vite proxy.
- Anything SRS section 11 lists as out of scope: multi-user, login, persistent history, sending attachments,
  Outlook/IMAP, and so on.

## Requirement traceability (summary)

| Requirement | Where |
|---|---|
| FR-01–04 prompts, sessions, reset, step limit | `api/routes.py`, `sessions.py`, `agent/loop.py` |
| FR-05–19 Gmail | `services/gmail.py`, `Code.gs` (Gmail section) |
| FR-20–27 Calendar | `services/calendar.py`, `services/availability.py`, `Code.gs` |
| FR-28–32 Drive | `services/drive.py`, `Code.gs` |
| FR-33–39 approvals | `agent/loop.py` (`_prepare_write`), `approvals.py`, `ApprovalCard.tsx` |
| NFR-01–08 security | `config.py`, `services/bridge.py`, `Code.gs` (`checkSecret_`, `OPERATIONS`), `tests/test_security.py`, `apps_script/tests/bridge.test.js` |
| NFR-09–12 performance | bridge timeout, truncation, `asyncio.gather` for reads, per-session lock |
| NFR-13–16 reliability | `errors.py`, exception handlers in `main.py`, action state machine |
| AT-01–26 | Covered across `backend/tests/` and `apps_script/tests/` (docstrings and comments name the AT ids) |
