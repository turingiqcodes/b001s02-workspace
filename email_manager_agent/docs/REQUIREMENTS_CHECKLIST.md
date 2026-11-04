# Requirements checklist

The SRS v1.0 wasn't available when this app was built. This checklist records what was
built in its place: the functional areas from the project brief, the status of each, and
where it's implemented. Where the SRS would normally set a number (step limits, timeouts,
working hours, sizes), the value was **assumed**. All the assumed values are listed at the
end, and every one of them can be changed in `.env`.

When the SRS becomes available, compare it with both tables and adjust the `.env` defaults
or the code to match.

Status key: ✅ implemented and tested · ⚠️ implemented with a stated limitation

## Functional requirements

| # | Area | Requirement | Status | Where |
|---|---|---|---|---|
| F1 | Search / read | Search Gmail with Gmail search syntax (default: inbox), capped result count | ✅ | tool `search_emails`; bridge `searchEmails` |
| F2 | Search / read | Read one email: headers, plain-text body (HTML stripped, truncated for the model), attachment list | ✅ | tool `read_email`; bridge `readEmail` |
| F3 | Search / read | Summarise emails in plain language | ✅ | model reply over `read_email` results (`backend/app/agent/`) |
| F4 | Compose | Send a new email (to/cc/bcc, UTF-8 subject and body) after approval | ✅ | tool `send_email`; bridge `sendEmail` / `buildRaw_` |
| F5 | Compose | Reply and reply-all in the same thread (In-Reply-To/References, "Re:"), never to yourself | ✅ | tool `reply_to_email`; bridge `replyToEmail` |
| F6 | Compose | Forward with the original message and attachments; attachments over the limit are skipped | ⚠️ total forwarded attachments ≤ `MAX_ATTACHMENT_MB` | tool `forward_email`; bridge `forwardEmail` |
| F7 | Drafts | List, create (optionally as a reply), update and send drafts | ✅ | tools `list_drafts`, `create_draft`, `update_draft`, `send_draft` |
| F8 | Labels | List labels; create user labels (no duplicates) | ✅ | tools `list_labels`, `create_label` |
| F9 | Labels | Add or remove labels on up to 100 emails. Only user labels and `INBOX`, `UNREAD`, `STARRED`, `IMPORTANT` can be changed | ✅ | tool `modify_labels`; bridge `modifyLabels` |
| F10 | Calendar | List events in a range (≤ 62 days), with optional text search | ✅ | tool `list_events`; bridge `listEvents` |
| F11 | Calendar | Find free slots of a given length inside working hours and days, skipping past times, 15-minute steps | ✅ | tool `find_free_slots`; `backend/app/services/` (availability logic) |
| F12 | Calendar | Create an event (attendees are invited only when listed) | ✅ | tool `create_event`; bridge `createEvent` |
| F13 | Calendar | Move an event and keep its duration when no new end is given | ✅ | tool `move_event` (backend fills `new_end`); bridge `moveEvent` |
| F14 | Calendar | Cancel an event, with approval plus an extra acknowledgement | ✅ | tool `cancel_event`; bridge `cancelEvent` |
| F15 | Calendar | Warn about conflicts and times outside working hours, and re-check conflicts just before running | ✅ | `backend/app/approvals.py`, `backend/app/services/` |
| F16 | Drive | Search files by name, type, modified date and folder, using structured, escaped filters | ✅ | tool `search_drive_files`; bridge `buildDriveQuery_` |
| F17 | Drive | Create folders and move files into folders | ✅ | tools `create_drive_folder`, `move_drive_file` |
| F18 | Drive | Save an email's attachments to a Drive folder (named folder created if missing); oversized files skipped with a warning | ✅ | tool `save_attachments_to_drive`; bridge `saveAttachmentsToDrive` |
| F19 | Approval gate | Every write becomes a pending operation. Nothing changes until the user approves it in the app | ✅ | `backend/app/approvals.py`, `POST /api/confirm` |
| F20 | Approval gate | Approvals are single use, tied to the session, expire, and run the stored parameters exactly | ✅ | `backend/app/approvals.py` |
| F21 | Approval gate | Warnings (conflict, outside working hours, skipped attachments, repeated action, cancellation, many recipients) require "I have reviewed these warnings" | ✅ | `backend/app/approvals.py`; frontend approval card |
| F22 | Approval gate | Reject cancels with no change. A new conversation clears pending approvals | ✅ | `POST /api/confirm`, `POST /api/reset` |
| F23 | Conversation | Multi-turn sessions in memory. The model is told the outcome of approvals on the next turn | ✅ | `backend/app/sessions.py`, `backend/app/agent/` |
| F24 | Local model | Runs with Ollama by default (tool calling, `num_ctx`, `keep_alive`, optional `think`) | ✅ | `backend/app/agent/` (Ollama client) |
| F25 | Local model | Optional Claude API provider (`LLM_PROVIDER=claude`) with effort and refusal fallback | ✅ | `backend/app/agent/` (Claude client) |
| F26 | Status | Status bar showing model and bridge configuration; "Check connection" pings Ollama and Google | ✅ | `GET /api/status`; frontend status bar |
| F27 | Deletion | No deletion of email or files anywhere | ✅ | no such tools; the bridge has no delete or trash operation (tested) |

## Security requirements

| # | Requirement | Status | Where |
|---|---|---|---|
| S1 | Backend listens on loopback only; `run.py` refuses other hosts | ✅ | `backend/run.py` |
| S2 | Rejects a `Host` header other than `localhost`/`127.0.0.1`, and non-loopback clients | ✅ | `backend/app/main.py` |
| S3 | CORS allows only the local frontend | ✅ | `backend/app/main.py` |
| S4 | Secrets only in `.env` (git-ignored) and Apps Script properties; never returned by the API or logged | ✅ | `backend/app/config.py`; bridge `checkSetup` logs only the length |
| S5 | Logs record tool and operation names, never email content | ✅ | backend logging |
| S6 | Bridge requires the shared secret (constant-time compare) and validates every input again | ✅ | bridge `doPost`, `validate_` |
| S7 | Header injection blocked in email subjects (CR/LF removed) | ✅ | bridge `encodeHeader_` (tested) |
| S8 | Prompt injection: email, event and file content is treated as data. Writes still need approval | ✅ | system prompt; approval gate |
| S9 | Model output is validated against the tool schemas before anything runs | ✅ | `backend/app/agent/` (tool registry) |

## Error handling

| # | Requirement | Status | Where |
|---|---|---|---|
| E1 | Every API error has the form `{"error": {"code", "message"}}` with a plain-language message | ✅ | `backend/app/errors.py` |
| E2 | Ollama problems are named: not running, model not pulled, no tool calling, timeout | ✅ | Ollama client |
| E3 | Bridge problems are named: non-JSON (wrong deployment), bad secret, not configured, timeout, permission, quota | ✅ | `backend/app/services/` bridge client; bridge `toError_` |
| E4 | A write that timed out warns that it may have gone through | ✅ | `POST /api/confirm` |
| E5 | Step limit reached, refusal and truncated output are explained in the reply | ✅ | agent loop |

## Assumed values

| Setting | `.env` variable | Assumed default | Notes |
|---|---|---|---|
| Model provider | `LLM_PROVIDER` | `ollama` | or `claude` |
| Ollama model | `OLLAMA_MODEL` | *(none, required)* | e.g. `qwen3:8b` |
| Ollama URL | `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | |
| Ollama context window | `OLLAMA_NUM_CTX` | `16384` tokens | Ollama's own default is too small |
| Ollama timeout | `OLLAMA_TIMEOUT_SECONDS` | `600` | per model call |
| Ollama keep-alive | `OLLAMA_KEEP_ALIVE` | `30m` | |
| Ollama thinking | `OLLAMA_THINK` | *(unset)* | `false` is faster for qwen3 |
| Claude model | `CLAUDE_MODEL` | *(none, required for Claude)* | e.g. `claude-opus-5-5` |
| Claude effort | `CLAUDE_EFFORT` | `medium` | `low` … `max` |
| Claude refusal fallback | `CLAUDE_REFUSAL_FALLBACK` | `true` | |
| Bridge timeout | `BRIDGE_TIMEOUT_SECONDS` | `30` | |
| Time zone | `USER_TIMEZONE` | `UTC` | IANA name |
| Working days | `WORK_DAYS` | `[0,1,2,3,4]` | Monday = 0 |
| Working hours | `WORK_START` / `WORK_END` | `09:00` / `18:00` | |
| Model steps per request | `MAX_AGENT_STEPS` | `8` | |
| Prompt length | `MAX_PROMPT_CHARS` | `4000` | |
| Results per search/list | `MAX_SEARCH_RESULTS` | `25` | the bridge also caps at 25 (event listing used for availability: 250) |
| Email body given to the model | `MAX_EMAIL_BODY_CHARS` | `20000` | the bridge returns at most 50,000 |
| Attachment size | `MAX_ATTACHMENT_MB` | `20` MB | larger ones are skipped with a warning; the bridge enforces 20 MB too |
| Recipients per email | `MAX_RECIPIENTS` | `20` | to + cc + bcc; the bridge enforces 20 too |
| Approval lifetime | `APPROVAL_TTL_SECONDS` | `600` (10 minutes) | |
| Idle session lifetime | `SESSION_TTL_SECONDS` | `7200` (2 hours) | |
| Sessions kept | `MAX_SESSIONS` | `50` | oldest dropped first |
| Backend address | `BACKEND_HOST` / `BACKEND_PORT` | `127.0.0.1` / `8000` | must be loopback |
| Frontend origin (CORS) | `FRONTEND_ORIGIN` | `http://localhost:5173` | `http://127.0.0.1:5173` also allowed |
| Log level | `LOG_LEVEL` | `INFO` | |
| Fixed: labels per change | *(bridge)* | 100 emails, 20 labels | `Code.gs` constants |
| Fixed: free-slot search span | *(backend)* | ≤ 14 days, ≤ 20 slots, 15-minute steps | |
| Fixed: event range | *(bridge)* | ≤ 62 days to list, ≤ 31 days per event | |
| Fixed: many-recipient warning | *(backend)* | more than 5 recipients | |
