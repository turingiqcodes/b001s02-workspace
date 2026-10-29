# BookNest review checklist

These are things to look for in each area. Each item is a question to answer with the code open, not a finding by itself.

## 1. Bug detection

- **Spec vs code**: for each bullet in README "Database changes", does the route enforce it?
  - Author edit/delete only on books they're credited on (`titleauthor.au_id = user.au_id`)
  - Author delete only if they're the *sole* author **and** there are no sales
  - Book/author/employee/publisher/store/sale keys "can't change". Is the key ignored in the PUT body?
  - Employee job level must fit `jobs.min_lvl..max_lvl` on add **and** edit
  - Publisher delete blocked if it has titles or employees; store delete blocked if sales; author delete blocked if credited
  - Discounts have no key: edit/delete must match on current values and change exactly one row
- **Generated IDs** (author `123-45-6789`, employee initials+digits+`X`, publisher/store "next free 4-digit"):
  check for collision races between SELECT max and INSERT, overflow past 9999, and the format matching DB CHECK constraints.
- **Transactions**: a multi-statement write (delete book + titleauthor + roysched, publisher + pub_info) must sit in **one**
  `transaction()` block. Several `fetch_one` calls each commit separately.
- **Read-then-write** without `FOR UPDATE` (lost updates, check-then-act races).
- **NULL handling**: `price`, `advance`, `ytd_sales`, `au_id` on admin users, `SUM()` over no rows → NULL.
- **Money/dates**: `Decimal` vs float, `qty * price` when price is NULL, timezone-naive vs aware datetimes.
- **Error mapping**: `psycopg2.errors.*` (UniqueViolation, ForeignKeyViolation, CheckViolation) → 409/400, not 500.
- **Frontend contract**: field names in `app.js` fetch bodies vs the Pydantic models, and status codes the UI handles.
- **Session expiry**: when a 401 arrives mid-session, does the UI clear storage and go back to login?

## 2. Security review

- **Authorization on every route**: each `@app.*` handler must take `Depends(require_admin|require_author|current_user)`.
  Only `/api/login`, `/` and static files may be public. Check `/api/me/*` author routes for IDOR (another author's title_id).
- **Role checks**: `PATCH /api/me` and password change are admin-only. Confirm an author key gets 403.
- **SQL injection**: f-string SQL is fine **only** when it interpolates module constants (e.g. `SOLD`). Any request value
  must go through `%s` params. Look closely at dynamic `ORDER BY`, column lists, and `LIKE` patterns (escape `%`/`_`).
- **XSS**: `app.js` renders with `innerHTML`. Every DB string (title, names, notes, store names) must pass through the
  escaping helper. Check `title=`/`value=` attributes and `onclick` strings too.
- **API keys**: only the SHA-256 is stored, a prefix is kept for display, revoked on logout/password reset/deactivate,
  expiry enforced in SQL. Check that the key is never logged and never stored in localStorage unless "remember" is set.
- **Passwords**: scrypt params, constant-time compare, dummy-hash path for unknown users (timing), min length on
  *every* password set (admin reset, create author login), not just self-change.
- **Brute force**: is there any rate limit or lockout on `/api/login`?
- **Secrets**: `.env` must be git-ignored. No credentials in code, the README, or test fixtures that look real.
- **Transport/headers**: CORS config, `sslmode` default (`prefer` allows plaintext), security headers, cache headers on API responses.
- **Mass assignment**: can a Pydantic model accept `role`, `is_active`, or `au_id` where it shouldn't?

## 3. Performance and efficiency

- **N+1**: loops in Python that call `fetch_one`/`fetch_all` per row. These should be one query with a JOIN or `ANY(%s)`.
- **`SOLD` subquery**: it aggregates *all* sales and is joined into several queries. Check for per-request cost,
  repeated use in one request, and missing `WHERE title_id = …` pushdown.
- **Connections**: `fetch_one` + `transaction()` each check out a pool connection. Several separate calls in one handler
  are several round-trips and commits. `current_user` does an UPDATE on **every** request (`last_used_at`), so that's a write per read.
- **Pool size**: `ThreadedConnectionPool(1, 10)` vs FastAPI's sync threadpool (default 40). Exhaustion raises
  `PoolError` rather than waiting.
- **Indexes**: `api_key.key_hash` (looked up on every request), `app_user.username`, `titleauthor.au_id`, `sales.title_id`.
  Check `sql/001_booknest_accounts.sql`.
- **Unbounded lists**: list endpoints with no `LIMIT`/pagination.
- **scrypt cost** on the login path is intended. Make sure it isn't also run on unrelated paths.
- **Frontend**: full re-render via `innerHTML` on each change, duplicate fetches, no caching of static lookups (jobs, publishers).

## 4. Code quality

- `main.py` is ~1200 lines with every route in it. Suggest an `APIRouter` split (auth, books, authors, sales, stores…) only
  if it would clearly help. Don't count file size alone as a finding.
- Repeated patterns that could share a helper: "next free 4-digit ID", "404 if row missing", "409 on FK violation".
- Consistency: HTTP status codes for the same situation across routes, error message tone, Pydantic `Field` constraints
  matching DB column sizes (`varchar(40)` etc.).
- Dead code, unused imports, magic numbers, and comments that disagree with the code.
- `config._load_dotenv` hand-parser: quoting and `export` lines.
- Frontend: global state, very long functions, inline HTML string building.

## 5. Tests and recommendations

- `tests/test_api.py` is a script with `check()`, not pytest. Note whether converting it to pytest would help (fixtures, isolation, CI).
- Map coverage: for each route and each README rule, is there a `check(...)`? List the uncovered ones.
- Propose concrete cases, especially negative ones:
  - author A edits or deletes author B's book → 403/404
  - author deletes a co-authored book or one with sales → refused
  - expired key, revoked key, deactivated user's key → 401
  - author key on every admin route → 403 (parametrize over all routes)
  - SQL metacharacters and `<script>` in titles/names round-trip safely
  - employee level outside the job range on edit
  - concurrent "next free ID" inserts
  - discount edit when the row changed underneath → "reload" error
- Recommendations: rank by severity × effort. Put the quick security wins first.
