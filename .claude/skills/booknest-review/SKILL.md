---
name: booknest-review
description: Review the BookNest app (FastAPI + Postgres pubs DB + vanilla JS frontend, in BookNest/booknest/) across five areas — bug detection, security review, performance and efficiency, code quality, and test cases with recommendations — and write a ranked report. Use when the user asks to review, audit, check, or assess BookNest, or runs /booknest-review. Optional args - one or more of bugs, security, performance, quality, tests (default all), and a file path to narrow the scope.
---

# BookNest review

Review the BookNest project and produce one report covering five areas:

1. **Bug detection**: logic errors, wrong SQL, broken edge cases, frontend/backend contract mismatches
2. **Security review**: authN/authZ, injection, XSS, secrets, session handling
3. **Performance and efficiency**: query shape, N+1, connection pool use, hot paths
4. **Code quality**: duplication, structure, naming, error handling, maintainability
5. **Test cases and recommendations**: coverage gaps, concrete new test cases, and a prioritized fix list

## Project layout

Paths are relative to `BookNest/booknest/` in the workspace.

| Path | What it is |
|---|---|
| `app/main.py` | All API routes (~60), Pydantic models, SQL. Most findings live here. |
| `app/auth.py` | `current_user`, `require_admin`, `require_author`: Bearer API key checks |
| `app/security.py` | scrypt password hashing, API key generation/hashing |
| `app/db.py` | psycopg2 `ThreadedConnectionPool`, `transaction()`, `fetch_one/all` |
| `app/config.py` | env / `.env` loading, DB settings, key lifetimes |
| `app/manage.py` | CLI: migrate, create admin |
| `sql/001_booknest_accounts.sql` | `app_user` and `api_key` tables |
| `frontend/app.js` | Single-page UI, renders with `innerHTML` |
| `tests/test_api.py` | End-to-end script (not pytest) against a throwaway DB |
| `README.md` | Business rules: what each action may write. **Treat it as the spec.** |

## Procedure

1. **Parse args.** If areas are given, review only those; if a path is given, focus on it but still follow calls into other files.
2. **Run the static checks**: `bash .claude/skills/booknest-review/scripts/static_checks.sh BookNest/booknest`.
   Its output lists *candidates*, not findings. Every hit must be confirmed by reading the code.
3. **Read the spec**: the "Database changes" section of `README.md` lists every rule the code must enforce.
   Treat any rule the code doesn't enforce as a bug.
4. **Review each area** using `references/checklist.md`. Read the code itself. Don't guess from names.
   - `app/main.py` is large: read it in sections (route groups are separated by `# ---------- … ----------` comments).
   - For a broad review, you may split the five areas across parallel subagents. Give each one the paths above,
     its checklist section, and the evidence rule below.
5. **Verify every finding** before reporting it. A finding needs:
   - `file:line`
   - a concrete trigger (input or state → wrong result), e.g. "author with au_id X sends PUT /api/me/books/BU1032 → edits a book they aren't credited on"
   - severity: **Critical** (data loss, auth bypass, injection), **High** (wrong data, crash on normal use),
     **Medium** (edge case, notable slowness), **Low** (quality, style)
   Drop anything you can't tie to a line and a trigger. Mark it **Plausible** instead of **Confirmed** if you
   couldn't fully trace it.
6. **Never run `tests/test_api.py` against the configured DB.** `.env` points at the real pubs database.
   Run it only if the user gives an empty throwaway DB. Otherwise, reason about the tests statically.
   Read-only checks are fine: `.venv/bin/python -c "import app.main"`, `python -m py_compile`.
7. **Don't edit code** unless the user asks for fixes. This skill reports. When asked to fix, fix one finding at a time
   and keep the surrounding style (short docstrings, plain SQL strings, `transaction()` blocks).
8. **Write the report** in the format below to `BookNest/reviews/review-YYYY-MM-DD.md` (create the folder if needed),
   and give the user a short summary in chat: counts by severity, plus the top 3 issues.

## Report format

```markdown
# BookNest review — YYYY-MM-DD
Scope: <areas> · <files>

## Summary
| Area | Critical | High | Medium | Low |
|---|---|---|---|---|
...

## 1. Bugs
### [High] <one-line claim> — `app/main.py:123` (Confirmed)
**Trigger:** <input/state → wrong result>
**Why:** <2–3 lines on the cause>
**Fix:** <specific change>

## 2. Security
## 3. Performance & efficiency
## 4. Code quality
## 5. Tests & recommendations
### Coverage gaps
<routes/rules from README with no check in tests/test_api.py>
### New test cases
| # | Area | Case | Steps | Expected |
|---|---|---|---|---|
### Prioritized recommendations
1. ...
```

Use "None found" for an empty section; never pad a section with speculative findings.
