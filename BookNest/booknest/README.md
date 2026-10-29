# BookNest

Web app for the pubs database: FastAPI backend + HTML frontend, with API keys stored in the DB.

- **Admin (publisher):** Home, Books (see, add, edit and delete), Authors (add, edit and delete authors, create author logins), Sales (add, edit and delete), Stores (add, edit and delete),
  Discounts (add, edit and delete), Employees (add, edit and delete), Publishers (add, edit and delete), My Account.
- **Author:** My Books (only the books they published, with **Add**, **Edit** and **Delete** for their own books) and
  My Account (read-only). They can delete a book only when they're its only author and it has no sales. Authors can't sign up;
  they sign in with the username and password the admin gave them.

## How the API keys work

1. The frontend sends username, password and which login was used (admin or author) to `POST /api/login`.
2. The backend checks the scrypt password hash in `public.app_user` and returns a new random API key (`bn_…`).
   Only its SHA-256 hash is stored, in `public.api_key`, with an expiry (8 hours, or 30 days with "Remember me").
3. The frontend sends `Authorization: Bearer <key>` on every request. Each route checks the key and the role:
   admin-only routes answer 403 to authors.
4. Logging out, a password reset by the admin, or deactivating an account revokes the keys.

## Database changes

`sql/001_booknest_accounts.sql` adds two tables to the `public` schema, `app_user` and `api_key`.
`sql/002_case_insensitive_usernames.sql` lower-cases usernames and makes them unique regardless of case
(`MGreen` and `mgreen` are the same login).
`sql/003_last_login.sql` adds `app_user.last_login_at`, so "last sign-in" survives `prune-keys`.
Neither changes any pubs table. The only pubs data the app writes is:

- **Add book**: a `titles` row, plus a `titleauthor` row if an author is picked.
- **Add book (author)**: a `titles` row linked to the signed-in author through `titleauthor`; the ID is made
  automatically and advance/royalty are left for the admin.
- **Edit / delete book (author)**: only books the author is credited on; delete only when they are the only
  author and the book has no sales.
- **Edit book**: the `titles` row; the author link only for books with at most one author. The book ID can't change.
- **Add author**: a new `authors` row (ID made up as `123-45-6789` unless given), and optionally its login in the same step.
  "Login for an existing author" only adds an `app_user` row.
- **Edit author**: the `authors` row (name, phone, address, contract). The author ID can't change.
- **Delete author**: the `authors` row and their BookNest login; authors of a book can't be deleted.
- **Add employee**: an `employee` row; the level must fit the job's range. Without a given ID one is made from
  the initials, five random digits and `X` (pubs ends IDs with M/F, which the app doesn't guess).
- **Edit employee**: the `employee` row. A changed job or level must fit the job's level range. The ID can't change.
- **Delete employee**: the `employee` row (nothing else points at it).
- **Add discount**: a `discounts` row (no store = every store).
- **Edit / delete discount**: one `discounts` row. The table has no key, so the request carries the row's current
  values and one matching row is changed; if none matches any more the app asks you to reload.
- **Add publisher**: a `publishers` row; the ID is the next free 4-digit number unless one is given.
- **Edit publisher**: the `publishers` row (name, city, state, country). The publisher ID can't change.
- **Delete publisher**: the `publishers` row and its `pub_info`; publishers with books or employees can't be deleted.
- **Add store**: a `stores` row; the ID is the next free 4-digit number unless one is given.
- **Add sale**: one `sales` line (store, order number, book, date, quantity, pay terms).
- **Edit sale**: date, quantity and pay terms of one `sales` line. Store, order number and book are its key and can't change.
- **Delete sale**: one `sales` line.
- **Edit store**: the `stores` row (name, address, city, state, zip). The store ID can't change.
- **Delete store**: the `stores` row and its `discounts`; stores that have sales can't be deleted.
- **Delete book**: removes the title and its `titleauthor`/`roysched` rows; books that have sales can't be deleted.

## Setup

```bash
cd booknest
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in the DB host, name, user and password; .env is git-ignored
.venv/bin/python -m app.manage migrate          # also after every update, before restarting the app
.venv/bin/python -m app.manage create-admin newmoon "New Moon Books" admin@newmoonbooks.com   # asks for a password
.venv/bin/python -m app.manage prune-keys          # optional, e.g. daily from cron: drops keys expired/revoked 30+ days ago
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Admins use the admin login; the admin then creates author accounts under **Authors**.

## API

| Method | Path | Who |
|---|---|---|
| POST | `/api/login`, `/api/logout` | anyone / signed in |
| GET | `/api/me` | signed in |
| PATCH | `/api/me`, POST `/api/me/password` | admin |
| GET | `/api/me/books`, `/api/me/books/options`; POST `/api/me/books` | author |
| GET/PUT/DELETE | `/api/me/books/{title_id}` (only books they are credited on) | author |
| GET | `/api/summary`, `/api/books`, `/api/sales`, `/api/stores`, `/api/discounts`, `/api/employees`, `/api/publishers` | admin |
| GET | `/api/books/options` (choices for Add book) | admin |
| POST | `/api/books` | admin |
| GET/PUT | `/api/books/{title_id}` | admin |
| DELETE | `/api/books/{title_id}` | admin |
| GET/POST | `/api/authors` (POST adds an author, optionally with a login) | admin |
| GET/PUT/DELETE | `/api/authors/{au_id}` | admin |
| POST | `/api/stores` | admin |
| GET | `/api/sales/options` (choices for Add sale), POST `/api/sales` | admin |
| GET/PUT/DELETE | `/api/sales/{stor_id}/{title_id}/{ord_num}` | admin |
| GET/PUT/DELETE | `/api/stores/{stor_id}` | admin |
| GET | `/api/employees/options` (choices for Add/Edit employee) | admin |
| POST | `/api/employees` | admin |
| GET/PUT/DELETE | `/api/employees/{emp_id}` | admin |
| POST | `/api/discounts` | admin |
| PUT/DELETE | `/api/discounts` (the row is named by its values in the body) | admin |
| POST | `/api/publishers` | admin |
| GET/PUT/DELETE | `/api/publishers/{pub_id}` | admin |
| GET/POST | `/api/accounts`, GET `/api/authors/without-account` | admin |
| GET/PATCH/DELETE | `/api/accounts/{id}`, POST `/api/accounts/{id}/password` | admin |

Interactive docs: http://127.0.0.1:8000/docs

## Tests

`tests/test_api.py` runs end-to-end checks (logins, keys, sign-in limits, role rules, every admin page's data rules, races, migrations) against a
**throwaway** Postgres loaded with `tests/pubs_sample.sql`. Never point it at the real database; it refuses to
run if pubs tables already exist.

```bash
BOOKNEST_DB_HOST=... BOOKNEST_DB_NAME=empty_test_db .venv/bin/python -m tests.test_api
```

## Sign-in limits

Usernames ignore case at sign-in.

`/api/login` answers 429 for 15 minutes after, within 15 minutes:

- 5 failed sign-ins for one username from one IP,
- 20 failed sign-ins from one IP, or
- 100 failed sign-ins for one username from anywhere.

So a wrong-password spree from one place can't lock the real user out of their account elsewhere.
The counts are kept in memory, so they are per server process and reset on restart.

**Behind a proxy**, the app must see each visitor's own IP, or every visitor shares one IP limit.
uvicorn takes the IP from `X-Forwarded-For` when the request comes from `127.0.0.1`, so the proxy must
send that header. For nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

If the proxy runs on another machine (e.g. a load balancer), also start uvicorn with
`--forwarded-allow-ips <proxy IP>`.

## Not included yet

- A shared sign-in limit across several server processes (see above).
- HTTPS: run behind a TLS proxy (or a load balancer) before exposing it beyond localhost.
