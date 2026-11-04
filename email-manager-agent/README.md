# Email Manager Agent

A web app that manages Gmail, Google Calendar and Google Drive from natural-language prompts.
Claude (`claude-opus-5-5`) works out what to do. **Nothing changes in your Google account until you approve a preview of it.**

```
User ──► React frontend (Vite, :5173)
              │  /api (proxied)
              ▼
         FastAPI backend (:8000) ──► Claude API (Anthropic)
              │  POST {token, action, params}
              ▼
         Google Apps Script bridge (web app)
              ├──► Gmail     (gmail.modify)
              ├──► Calendar  (calendar.events)
              └──► Drive     (drive.file)
```

All Google access goes through the Apps Script bridge, Calendar included. The browser never holds Google or Anthropic credentials.

## How approval works (FR-08, FR-09)

1. You send a prompt. Claude runs **read tools** (search, read, list labels or drafts, list events, check availability) straight away.
2. When Claude calls a **write tool**, the backend pauses the agent loop and returns a preview card instead of running it. The card shows the recipients, subject and body, before → after values, the meeting details, any calendar clashes, and a warning when the action can't be undone.
3. You **Approve** or **Reject** each card. Approved actions run. Rejected ones are reported back to Claude as declined, and it won't retry them unless you ask. The loop then carries on.

Write tools: `create_draft`, `update_draft`, `send_draft`, `send_email`, `create_label`, `modify_labels`, `create_event`, `update_event`, `cancel_event`, `save_attachment_to_drive`.

## Requirement traceability

| Req | Where |
|---|---|
| FR-01 natural-language prompts | `frontend/src/App.tsx`, `backend/app/agent.py` |
| FR-02 search / read / summarize | `search_emails`, `read_email` tools; Claude writes the summary |
| FR-03 drafts create / update / send | `create_draft`, `update_draft`, `send_draft`, `send_email` |
| FR-04 organize & restore | `create_label`, `modify_labels` (add/remove user labels; restore = reverse a change) |
| FR-05 availability | `check_availability`, `list_events` |
| FR-06 meetings | `create_event`, `update_event`, `cancel_event` |
| FR-07 attachments → Drive | `save_attachment_to_drive` (default folder "Email Attachments") |
| FR-08 previews | `backend/app/tools.py::build_preview`, `ApprovalCard.tsx` |
| FR-09 approval gate | `EmailAgent.decide` in `backend/app/agent.py` |
| FR-10 plain-language errors | `backend/app/errors.py`, bridge error codes in `apps-script/Code.gs` |
| Security: secrets in backend env | `backend/.env` (gitignored), bridge token in Apps Script Script Properties |
| Privacy: minimum scopes | `apps-script/appsscript.json`, three narrow scopes via advanced services |
| Testability: mock services | `BRIDGE_MODE=mock` (`MockBridge`) and `tests/` with a scripted fake Claude |

## Setup

### Requirements
Python 3.10+, Node.js 18+, a Google account, and an Anthropic API key.

### 1. Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then set ANTHROPIC_API_KEY
uvicorn app.main:app --reload --port 8000
```
With `BRIDGE_MODE=mock` (the default) the app runs on built-in sample emails, events and Drive data, so you can try it before connecting Google.

### 2. Google Apps Script bridge
1. Go to <https://script.google.com> → **New project**.
2. **Project Settings** → tick *Show "appsscript.json" manifest file*. Replace its contents with `apps-script/appsscript.json`, and replace `Code.gs` with `apps-script/Code.gs`.
3. **Project Settings → Script Properties** → add `BRIDGE_TOKEN` = a long random string. Generate one with `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
4. **Deploy → New deployment → Web app**, with *Execute as: Me* and *Who has access: Anyone*. Authorize the three scopes when prompted.
5. In `backend/.env`, set `BRIDGE_MODE=apps_script`, set `APPS_SCRIPT_URL` to the `/exec` URL, and set `BRIDGE_TOKEN` to the same secret. Restart the backend.

"Anyone" access is needed so the backend can call the web app without a Google login. Every request must carry the shared `BRIDGE_TOKEN`, and requests without it are rejected. Keep the URL and token private. After you edit `Code.gs`, deploy a new version (Deploy → Manage deployments → Edit → New version).

### 3. Frontend
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api to :8000)
```

## Tests
```bash
cd backend
pytest
```
The tests use a scripted fake Claude client and the mock bridge. No network or API key is needed. They cover the approval gate (nothing runs before approval; rejected actions never run), parallel tool results, previews, input validation, error mapping, retrying after a failure, refusals, the step limit, the HTTP API, and the Apps Script client.

## Configuration (`backend/.env`)
| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Anthropic API key |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model |
| `CLAUDE_EFFORT` | `medium` | Reasoning effort (`low` … `max`) |
| `CLAUDE_ENABLE_FALLBACKS` | `true` | If Claude's safety filter declines a request, the API retries it on Anthropic's recommended fallback model |
| `BRIDGE_MODE` | `mock` | `mock` or `apps_script` |
| `APPS_SCRIPT_URL`, `BRIDGE_TOKEN` | – | Bridge connection |
| `USER_TIMEZONE` | `UTC` | Used only if the bridge can't report the calendar timezone |
| `CORS_ORIGINS` | `http://localhost:5173` | Allowed browser origins |

## Design notes and limits
- **Conversations are kept in memory.** Restarting the backend clears chats and any pending approvals.
- **Organizing email means user labels only.** System labels such as INBOX, UNREAD, STARRED and TRASH are blocked, so there is no archive, delete, read/unread or star. To allow them, relax `modifyLabels` in `Code.gs` and `_resolve_user_label` in the mock.
- **Prompt injection:** the system prompt tells Claude to treat email content as data. The real safeguard is that every write needs your approval.
- **Long email bodies** over 50,000 characters are cut off by the bridge with a visible "[Body truncated …]" note.
- **Drive access uses the `drive.file` scope**, so the bridge only sees files and folders it created itself.
- **Attachment IDs** are Gmail MIME part IDs, which stay the same between fetches. Gmail's own attachment IDs change.
