"""BookNest API and frontend.

Admin (publisher): every /api route.  Author: /api/me and /api/me/books only.
Run: uvicorn app.main:app --port 8000   (from the booknest/ folder)
"""
import secrets
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2.errors
import psycopg2.sql
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import current_user, require_admin, require_author
from .config import KEY_HOURS, KEY_HOURS_REMEMBER, ROOT
from .db import fetch_all, fetch_one, transaction
from .security import DUMMY_HASH, hash_api_key, hash_password, new_api_key, verify_password

app = FastAPI(title="BookNest API")


@app.exception_handler(psycopg2.errors.UniqueViolation)
def duplicate_key(request, exc):
    # Routes check for duplicates first; this catches the race where two requests pass that check together.
    return JSONResponse({"detail": "That was just added by another request. Reload and try again."}, status_code=409)


@app.exception_handler(psycopg2.errors.ForeignKeyViolation)
def missing_reference(request, exc):
    # Same idea for links: e.g. a publisher was deleted while a book for it was being added.
    return JSONResponse({"detail": "Something this refers to changed meanwhile. Reload and try again."}, status_code=409)


FRONTEND = ROOT / "frontend"

# Copies sold per title; joined into several queries.
SOLD = "(SELECT title_id, SUM(qty) AS sold, SUM(qty * t.price) AS revenue FROM public.sales s JOIN public.titles t USING (title_id) GROUP BY title_id)"


def _blank(v):
    """Trimmed text, or None when empty."""
    return (v or "").strip() or None


def _upper(v):
    return v.upper() if v else None


# ---------- sign in / out ----------

class LoginIn(BaseModel):
    username: str = Field(max_length=41)
    password: str = Field(max_length=128)
    role: str = Field(pattern="^(admin|author)$")   # which login screen was used
    remember: bool = False


# Failed sign-ins in the last LOGIN_WINDOW seconds. Kept in memory, so the limit is per server
# process and resets on restart. A guesser is stopped by the username+IP limit; the username-only
# limit is high on purpose, so nobody can lock someone else out with a few wrong passwords.
LOGIN_WINDOW = 15 * 60
LOGIN_MAX_FAILS = {"user_ip": 5, "ip": 20, "user": 100}
_login_fails = {}
_login_lock = threading.Lock()


def _login_keys(username, request):
    ip = request.client.host if request.client else "unknown"
    return {"user_ip": f"user_ip:{username}|{ip}", "ip": f"ip:{ip}", "user": f"user:{username}"}


def _too_many_fails(keys):
    cutoff = time.monotonic() - LOGIN_WINDOW
    with _login_lock:
        for kind, key in keys.items():
            recent = [t for t in _login_fails.get(key, []) if t > cutoff]
            if recent:
                _login_fails[key] = recent
            else:
                _login_fails.pop(key, None)
            if len(recent) >= LOGIN_MAX_FAILS[kind]:
                return True
    return False


LOGIN_SWEEP_AT = 10_000   # entries; made-up usernames are never retried, so their entries need sweeping


def _record_fail(keys):
    now = time.monotonic()
    with _login_lock:
        for key in keys.values():
            _login_fails.setdefault(key, []).append(now)
        if len(_login_fails) > LOGIN_SWEEP_AT:
            for key in [k for k, times in _login_fails.items() if times[-1] <= now - LOGIN_WINDOW]:
                del _login_fails[key]


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    username = body.username.strip().lstrip("@").lower()
    keys = _login_keys(username, request)
    # Checked before the password, so a locked-out guesser doesn't cost a scrypt run either.
    if _too_many_fails(keys):
        raise HTTPException(429, "Too many failed sign-ins. Wait 15 minutes and try again.")
    user = fetch_one(
        "SELECT user_id, username, display_name, role, password_hash, is_active FROM public.app_user WHERE lower(username) = %s",
        (username,),
    )
    ok = verify_password(body.password, user["password_hash"] if user else DUMMY_HASH)
    if not user or not ok or not user["is_active"]:
        _record_fail(keys)
        raise HTTPException(401, "Wrong username or password.")
    with _login_lock:
        _login_fails.pop(keys["user_ip"], None)
    if user["role"] != body.role:
        other = "admin" if user["role"] == "admin" else "author"
        raise HTTPException(403, f"This is an {other} account. Use the {other} login.")

    key = new_api_key()
    expires = datetime.now(timezone.utc) + timedelta(hours=KEY_HOURS_REMEMBER if body.remember else KEY_HOURS)
    with transaction() as cur:
        cur.execute(
            "INSERT INTO public.api_key (user_id, key_hash, key_prefix, expires_at) VALUES (%s, %s, %s, %s)",
            (user["user_id"], hash_api_key(key), key[:10], expires),
        )
        cur.execute("UPDATE public.app_user SET last_login_at = now() WHERE user_id = %s", (user["user_id"],))
    return {"api_key": key, "expires_at": expires,
            "user": {k: user[k] for k in ("user_id", "username", "display_name", "role")}}


@app.post("/api/logout")
def logout(user=Depends(current_user)):
    with transaction() as cur:
        cur.execute("UPDATE public.api_key SET revoked_at = now() WHERE key_id = %s", (user["key_id"],))
    return {"ok": True}


# ---------- the signed-in account (admin and author) ----------

@app.get("/api/me")
def me(user=Depends(current_user)):
    out = {k: user[k] for k in ("user_id", "username", "display_name", "email", "role")}
    row = fetch_one("SELECT created_at FROM public.app_user WHERE user_id = %s", (user["user_id"],))
    out["created_at"] = row["created_at"]
    if user["au_id"]:
        out["author"] = fetch_one(
            "SELECT au_id, au_fname, au_lname, phone, city, state FROM public.authors WHERE au_id = %s",
            (user["au_id"],))
    return out


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    email: str | None = Field(default=None, max_length=120)


@app.patch("/api/me")
def update_me(body: ProfileIn, user=Depends(require_admin)):
    # Authors can't change their own details; the admin does it for them.
    with transaction() as cur:
        cur.execute("UPDATE public.app_user SET display_name = %s, email = %s WHERE user_id = %s",
                    (body.display_name.strip(), _blank(body.email), user["user_id"]))
    return {"ok": True}


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@app.post("/api/me/password")
def change_my_password(body: PasswordChangeIn, user=Depends(require_admin)):
    row = fetch_one("SELECT password_hash FROM public.app_user WHERE user_id = %s", (user["user_id"],))
    if not verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(400, "Current password is wrong.")
    with transaction() as cur:
        cur.execute("UPDATE public.app_user SET password_hash = %s WHERE user_id = %s",
                    (hash_password(body.new_password), user["user_id"]))
        # Sign out everywhere else.
        cur.execute("UPDATE public.api_key SET revoked_at = now() WHERE user_id = %s AND key_id <> %s AND revoked_at IS NULL",
                    (user["user_id"], user["key_id"]))
    return {"ok": True}


@app.get("/api/me/books")
def my_books(user=Depends(require_author)):
    books = fetch_all(
        f"""
        SELECT t.title_id, t.title, t.type, t.price, t.pubdate, p.pub_name,
               COALESCE(s.sold, 0) AS sold, COALESCE(s.revenue, 0) AS revenue
        FROM public.titleauthor ta
        JOIN public.titles t USING (title_id)
        LEFT JOIN public.publishers p USING (pub_id)
        LEFT JOIN {SOLD} s USING (title_id)
        WHERE ta.au_id = %s
        ORDER BY t.title_id
        """,
        (user["au_id"],),
    )
    return {"books": books,
            "totals": {"books": len(books),
                       "sold": sum(b["sold"] for b in books),
                       "revenue": sum(b["revenue"] for b in books)}}


@app.get("/api/me/books/options")
def my_book_options(_=Depends(require_author)):
    """Choices for an author's Add book form."""
    return {
        "types": [r["type"] for r in fetch_all("SELECT DISTINCT type FROM public.titles ORDER BY type")],
        "publishers": fetch_all("SELECT pub_id, pub_name FROM public.publishers ORDER BY pub_name"),
    }


class MyBookIn(BaseModel):
    # Authors set the basics; advance and royalty stay with the admin.
    title: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=12)
    pub_id: str = Field(min_length=1, max_length=4)
    price: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    pubdate: date | None = None
    notes: str | None = Field(default=None, max_length=200)


@app.post("/api/me/books", status_code=201)
def add_my_book(body: MyBookIn, user=Depends(require_author)):
    with transaction() as cur:
        cur.execute("SELECT 1 FROM public.publishers WHERE pub_id = %s", (body.pub_id,))
        if not cur.fetchone():
            raise HTTPException(404, "No publisher with that ID.")
        title_id = _next_title_id(cur, body.type.strip())
        cur.execute(
            """
            INSERT INTO public.titles (title_id, title, type, pub_id, price, ytd_sales, notes, pubdate)
            VALUES (%s, %s, %s, %s, %s, 0, %s, COALESCE(%s, CURRENT_DATE))
            """,
            (title_id, body.title.strip(), body.type.strip(), body.pub_id, body.price, _blank(body.notes), body.pubdate))
        # The book is always the signed-in author's own.
        cur.execute("INSERT INTO public.titleauthor (au_id, title_id, au_ord, royaltyper) VALUES (%s, %s, 1, 100)",
                    (user["au_id"], title_id))
    return {"ok": True, "title_id": title_id}


def _my_book_or_404(cur, title_id, user):
    # Authors only reach books they are credited on; anything else looks like it doesn't exist.
    cur.execute("SELECT t.title FROM public.titles t JOIN public.titleauthor ta USING (title_id) WHERE t.title_id = %s AND ta.au_id = %s",
                (title_id, user["au_id"]))
    book = cur.fetchone()
    if not book:
        raise HTTPException(404, "None of your books has that ID.")
    return book


@app.get("/api/me/books/{title_id}")
def my_book(title_id: str, user=Depends(require_author)):
    with transaction() as cur:
        _my_book_or_404(cur, title_id, user)
        cur.execute("SELECT title_id, title, type, pub_id, price, pubdate, notes FROM public.titles WHERE title_id = %s", (title_id,))
        return cur.fetchone()


@app.put("/api/me/books/{title_id}")
def update_my_book(title_id: str, body: MyBookIn, user=Depends(require_author)):
    with transaction() as cur:
        _my_book_or_404(cur, title_id, user)
        cur.execute("SELECT 1 FROM public.publishers WHERE pub_id = %s", (body.pub_id,))
        if not cur.fetchone():
            raise HTTPException(404, "No publisher with that ID.")
        cur.execute(
            "UPDATE public.titles SET title = %s, type = %s, pub_id = %s, price = %s, notes = %s, pubdate = COALESCE(%s, pubdate) WHERE title_id = %s",
            (body.title.strip(), body.type.strip(), body.pub_id, body.price, _blank(body.notes), body.pubdate, title_id))
    return {"ok": True, "title_id": title_id}


@app.delete("/api/me/books/{title_id}")
def delete_my_book(title_id: str, user=Depends(require_author)):
    with transaction() as cur:
        book = _my_book_or_404(cur, title_id, user)
        cur.execute("SELECT COUNT(*) AS n FROM public.titleauthor WHERE title_id = %s", (title_id,))
        if cur.fetchone()["n"] > 1:
            raise HTTPException(409, f"“{book['title']}” has other authors too, so only the admin can delete it.")
        cur.execute("SELECT COUNT(*) AS n FROM public.sales WHERE title_id = %s", (title_id,))
        n = cur.fetchone()["n"]
        if n:
            raise HTTPException(409, f"“{book['title']}” has {n} sale{'' if n == 1 else 's'}, so it can't be deleted.")
        cur.execute("DELETE FROM public.titleauthor WHERE title_id = %s", (title_id,))
        cur.execute("DELETE FROM public.roysched WHERE title_id = %s", (title_id,))
        cur.execute("DELETE FROM public.titles WHERE title_id = %s", (title_id,))
    return {"ok": True, "deleted": title_id}


# ---------- admin: overview ----------

@app.get("/api/summary")
def summary(_=Depends(require_admin)):
    totals = fetch_one(
        """
        SELECT (SELECT COUNT(*) FROM public.titles) AS books,
               (SELECT COUNT(*) FROM public.authors) AS authors,
               (SELECT COUNT(*) FROM public.app_user WHERE role = 'author') AS author_accounts,
               (SELECT COALESCE(SUM(qty), 0) FROM public.sales) AS sold,
               (SELECT COALESCE(SUM(s.qty * t.price), 0) FROM public.sales s JOIN public.titles t USING (title_id)) AS revenue,
               (SELECT COUNT(*) FROM (SELECT DISTINCT stor_id, ord_num FROM public.sales) o) AS orders,
               (SELECT COUNT(*) FROM public.stores) AS stores,
               (SELECT COUNT(*) FROM public.sales) AS order_lines,
               (SELECT COUNT(*) FROM public.discounts) AS discounts,
               (SELECT COUNT(*) FROM public.employee) AS employees,
               (SELECT COUNT(*) FROM public.publishers) AS publishers
        """)
    by_publisher = fetch_all(
        """
        SELECT p.pub_name, SUM(s.qty) AS sold
        FROM public.sales s JOIN public.titles t USING (title_id) JOIN public.publishers p USING (pub_id)
        GROUP BY p.pub_name ORDER BY sold DESC
        """)
    top_books = fetch_all(
        f"""
        SELECT t.title, p.pub_name, s.sold
        FROM {SOLD} s JOIN public.titles t USING (title_id) LEFT JOIN public.publishers p USING (pub_id)
        ORDER BY s.sold DESC, t.title LIMIT 5
        """)
    return {"totals": totals, "by_publisher": by_publisher, "top_books": top_books}


# ---------- admin: books ----------

@app.get("/api/books")
def books(_=Depends(require_admin)):
    return fetch_all(
        f"""
        SELECT t.title_id, t.title, t.type, t.price, t.pubdate, p.pub_name, COALESCE(s.sold, 0) AS sold
        FROM public.titles t
        LEFT JOIN public.publishers p USING (pub_id)
        LEFT JOIN {SOLD} s USING (title_id)
        ORDER BY t.title_id
        """)


@app.get("/api/books/options")
def book_options(_=Depends(require_admin)):
    """Choices for the Add and Edit book forms."""
    return {
        "types": [r["type"] for r in fetch_all("SELECT DISTINCT type FROM public.titles ORDER BY type")],
        "publishers": fetch_all("SELECT pub_id, pub_name FROM public.publishers ORDER BY pub_name"),
        "authors": fetch_all("SELECT au_id, au_fname || ' ' || au_lname AS name FROM public.authors ORDER BY au_lname, au_fname"),
    }


class BookFields(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=12)
    pub_id: str = Field(min_length=1, max_length=4)
    price: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    advance: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    royalty: int | None = Field(default=None, ge=0, le=100)
    pubdate: date | None = None
    notes: str | None = Field(default=None, max_length=200)
    au_id: str | None = None   # optional author, linked through titleauthor


class BookIn(BookFields):
    title_id: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}\d{4}$")   # e.g. BU1032; made up when empty


def _check_book_refs(cur, body):
    cur.execute("SELECT 1 FROM public.publishers WHERE pub_id = %s", (body.pub_id,))
    if not cur.fetchone():
        raise HTTPException(404, "No publisher with that ID.")
    if body.au_id:
        cur.execute("SELECT 1 FROM public.authors WHERE au_id = %s", (body.au_id,))
        if not cur.fetchone():
            raise HTTPException(404, "No author with that ID.")


def _next_title_id(cur, book_type):
    # pubs IDs are two letters and four digits. Reuse the letters the type's books already have
    # (mod_cook -> MC); a new type gets its first two letters.
    cur.execute("SELECT LEFT(title_id, 2) AS p FROM public.titles WHERE type = %s AND title_id ~ '^[A-Z]{2}[0-9]{4}$' "
                "GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 1", (book_type,))
    row = cur.fetchone()
    prefix = row["p"] if row else ("".join(ch for ch in book_type if ch.isalpha())[:2] or "XX").upper().ljust(2, "X")
    cur.execute("SELECT MAX(CAST(SUBSTRING(title_id FROM 3) AS integer)) AS n FROM public.titles "
                "WHERE title_id ~ %s", (f"^{prefix}[0-9]{{4}}$",))
    n = (cur.fetchone()["n"] or 1000) + 1
    if n > 9999:   # the top number is taken (pubs has PC9999): use the lowest free one instead
        cur.execute("SELECT MIN(g) AS n FROM generate_series(1001, 9999) g "
                    "WHERE NOT EXISTS (SELECT 1 FROM public.titles WHERE title_id = %s || LPAD(g::text, 4, '0'))", (prefix,))
        n = cur.fetchone()["n"]
        if n is None:
            raise HTTPException(409, f"No free book IDs left for {prefix}. Enter an ID yourself.")
    return f"{prefix}{n:04d}"


@app.post("/api/books", status_code=201)
def create_book(body: BookIn, _=Depends(require_admin)):
    with transaction() as cur:
        _check_book_refs(cur, body)
        title_id = body.title_id.upper() if body.title_id else _next_title_id(cur, body.type.strip())
        cur.execute("SELECT 1 FROM public.titles WHERE title_id = %s", (title_id,))
        if cur.fetchone():
            raise HTTPException(409, f"Book ID {title_id} is already used.")
        cur.execute(
            """
            INSERT INTO public.titles (title_id, title, type, pub_id, price, advance, royalty, ytd_sales, notes, pubdate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, COALESCE(%s, CURRENT_DATE))
            """,
            (title_id, body.title.strip(), body.type.strip(), body.pub_id, body.price, body.advance, body.royalty,
             _blank(body.notes), body.pubdate),
        )
        if body.au_id:
            cur.execute("INSERT INTO public.titleauthor (au_id, title_id, au_ord, royaltyper) VALUES (%s, %s, 1, 100)",
                        (body.au_id, title_id))
    return {"ok": True, "title_id": title_id}


@app.get("/api/books/{title_id}")
def book(title_id: str, _=Depends(require_admin)):
    row = fetch_one(
        "SELECT title_id, title, type, pub_id, price, advance, royalty, ytd_sales, notes, pubdate FROM public.titles WHERE title_id = %s",
        (title_id,))
    if not row:
        raise HTTPException(404, "No book with that ID.")
    row["authors"] = fetch_all(
        """
        SELECT a.au_id, a.au_fname || ' ' || a.au_lname AS name
        FROM public.titleauthor ta JOIN public.authors a USING (au_id)
        WHERE ta.title_id = %s ORDER BY ta.au_ord, a.au_lname
        """, (title_id,))
    return row


@app.put("/api/books/{title_id}")
def update_book(title_id: str, body: BookFields, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute("SELECT 1 FROM public.titles WHERE title_id = %s", (title_id,))
        if not cur.fetchone():
            raise HTTPException(404, "No book with that ID.")
        _check_book_refs(cur, body)
        cur.execute(
            """
            UPDATE public.titles SET title = %s, type = %s, pub_id = %s, price = %s, advance = %s, royalty = %s,
                   notes = %s, pubdate = COALESCE(%s, pubdate)
            WHERE title_id = %s
            """,
            (body.title.strip(), body.type.strip(), body.pub_id, body.price, body.advance, body.royalty,
             _blank(body.notes), body.pubdate, title_id),
        )
        # The author only changes when the form sent one; books with co-authors keep their links.
        if "au_id" in body.model_fields_set:
            cur.execute("SELECT au_id FROM public.titleauthor WHERE title_id = %s", (title_id,))
            current = [r["au_id"] for r in cur.fetchall()]
            if len(current) > 1:
                raise HTTPException(409, "This book has several authors, so its author can't be changed here.")
            if current != ([body.au_id] if body.au_id else []):
                cur.execute("DELETE FROM public.titleauthor WHERE title_id = %s", (title_id,))
                if body.au_id:
                    cur.execute("INSERT INTO public.titleauthor (au_id, title_id, au_ord, royaltyper) VALUES (%s, %s, 1, 100)",
                                (body.au_id, title_id))
    return {"ok": True, "title_id": title_id}


@app.delete("/api/books/{title_id}")
def delete_book(title_id: str, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute("SELECT title FROM public.titles WHERE title_id = %s", (title_id,))
        book = cur.fetchone()
        if not book:
            raise HTTPException(404, "No book with that ID.")
        cur.execute("SELECT COUNT(*) AS n FROM public.sales WHERE title_id = %s", (title_id,))
        n = cur.fetchone()["n"]
        if n:
            # Deleting would also delete sales history, so refuse instead.
            raise HTTPException(409, f"“{book['title']}” has {n} sales, so it can't be deleted.")
        cur.execute("DELETE FROM public.titleauthor WHERE title_id = %s", (title_id,))
        cur.execute("DELETE FROM public.roysched WHERE title_id = %s", (title_id,))
        cur.execute("DELETE FROM public.titles WHERE title_id = %s", (title_id,))
    return {"ok": True, "deleted": title_id}


# ---------- admin: author accounts ----------

@app.get("/api/accounts")
def accounts(_=Depends(require_admin)):
    return fetch_all(
        """
        SELECT u.user_id, u.username, u.display_name, u.email, u.role, u.au_id, u.is_active, u.created_at,
               (SELECT COUNT(*) FROM public.titleauthor ta WHERE ta.au_id = u.au_id) AS books
        FROM public.app_user u
        ORDER BY u.role, u.display_name
        """)


@app.get("/api/authors/without-account")
def authors_without_account(_=Depends(require_admin)):
    return fetch_all(
        """
        SELECT a.au_id, a.au_fname || ' ' || a.au_lname AS name, a.city, a.state
        FROM public.authors a
        WHERE a.au_id NOT IN (SELECT au_id FROM public.app_user WHERE au_id IS NOT NULL)
        ORDER BY a.au_lname, a.au_fname
        """)


@app.get("/api/authors")
def authors(_=Depends(require_admin)):
    """Every pubs author, with their book count and BookNest account if they have one."""
    return fetch_all(
        """
        SELECT a.au_id, a.au_fname || ' ' || a.au_lname AS name, a.phone, a.city, a.state, a.contract,
               (SELECT COUNT(*) FROM public.titleauthor ta WHERE ta.au_id = a.au_id) AS books,
               u.user_id, u.username, u.is_active
        FROM public.authors a LEFT JOIN public.app_user u ON u.au_id = a.au_id
        ORDER BY a.au_lname, a.au_fname
        """)


@app.get("/api/authors/{au_id}")
def author(au_id: str, _=Depends(require_admin)):
    row = fetch_one(
        "SELECT au_id, au_fname, au_lname, phone, address, city, state, zip, contract FROM public.authors WHERE au_id = %s",
        (au_id,))
    if not row:
        raise HTTPException(404, "No author with that ID.")
    return row


class AuthorIn(BaseModel):
    au_fname: str = Field(min_length=1, max_length=20)
    au_lname: str = Field(min_length=1, max_length=40)
    phone: str = Field(min_length=1, max_length=12)
    address: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    zip: str | None = Field(default=None, pattern=r"^\d{5}$")
    contract: bool


class AuthorLoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^@?[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=120)


class NewAuthorIn(AuthorIn):
    au_id: str | None = Field(default=None, pattern=r"^\d{3}-\d{2}-\d{4}$")   # e.g. 172-32-1176; made up when empty
    login: AuthorLoginIn | None = None   # also create a BookNest login


def _new_au_id(cur):
    # pubs author IDs look like 172-32-1176; pick an unused random one.
    for _ in range(50):
        n = f"{secrets.randbelow(10**9):09d}"
        au_id = f"{n[:3]}-{n[3:5]}-{n[5:]}"
        cur.execute("SELECT 1 FROM public.authors WHERE au_id = %s", (au_id,))
        if not cur.fetchone():
            return au_id
    raise HTTPException(409, "Couldn't find a free author ID. Enter one yourself.")


@app.post("/api/authors", status_code=201)
def create_author(body: NewAuthorIn, _=Depends(require_admin)):
    user_id = None
    try:
        with transaction() as cur:
            au_id = body.au_id or _new_au_id(cur)
            cur.execute("SELECT 1 FROM public.authors WHERE au_id = %s", (au_id,))
            if cur.fetchone():
                raise HTTPException(409, f"Author ID {au_id} is already used.")
            cur.execute(
                """
                INSERT INTO public.authors (au_id, au_fname, au_lname, phone, address, city, state, zip, contract)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (au_id, body.au_fname.strip(), body.au_lname.strip(), body.phone.strip(), _blank(body.address), _blank(body.city),
                 _upper(body.state), body.zip, body.contract))
            if body.login:
                # Same transaction: if the username is taken, the author isn't created either.
                cur.execute(
                    """
                    INSERT INTO public.app_user (username, display_name, email, password_hash, role, au_id)
                    VALUES (%s, %s, %s, %s, 'author', %s) RETURNING user_id
                    """,
                    (body.login.username.lstrip("@").lower(), f"{body.au_fname.strip()} {body.au_lname.strip()}",
                     _blank(body.login.email), hash_password(body.login.password), au_id))
                user_id = cur.fetchone()["user_id"]
    except psycopg2.errors.UniqueViolation as e:
        if e.diag.table_name != "app_user":
            raise   # the author ID was taken meanwhile: the app-wide handler answers 409
        raise HTTPException(409, "That username is taken.")
    return {"ok": True, "au_id": au_id, "user_id": user_id}


@app.put("/api/authors/{au_id}")
def update_author(au_id: str, body: AuthorIn, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute(
            """
            UPDATE public.authors SET au_fname = %s, au_lname = %s, phone = %s, address = %s, city = %s,
                   state = %s, zip = %s, contract = %s
            WHERE au_id = %s
            """,
            (body.au_fname.strip(), body.au_lname.strip(), body.phone.strip(), _blank(body.address), _blank(body.city),
             _upper(body.state), body.zip, body.contract, au_id))
        if not cur.rowcount:
            raise HTTPException(404, "No author with that ID.")
    return {"ok": True, "au_id": au_id}


@app.delete("/api/authors/{au_id}")
def delete_author(au_id: str, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute("SELECT au_fname || ' ' || au_lname AS name FROM public.authors WHERE au_id = %s", (au_id,))
        found = cur.fetchone()
        if not found:
            raise HTTPException(404, "No author with that ID.")
        cur.execute("SELECT COUNT(*) AS n FROM public.titleauthor WHERE au_id = %s", (au_id,))
        n = cur.fetchone()["n"]
        if n:
            # Deleting would drop them from their books' credits, so refuse instead.
            raise HTTPException(409, f"{found['name']} is an author of {n} book{'' if n == 1 else 's'}, so they can't be deleted.")
        # Their login goes too; api_key rows go with it (ON DELETE CASCADE).
        cur.execute("DELETE FROM public.app_user WHERE au_id = %s", (au_id,))
        account_deleted = cur.rowcount > 0
        cur.execute("DELETE FROM public.authors WHERE au_id = %s", (au_id,))
    return {"ok": True, "deleted": au_id, "account_deleted": account_deleted}


class AccountIn(BaseModel):
    au_id: str
    username: str = Field(min_length=3, max_length=40, pattern=r"^@?[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=120)


@app.post("/api/accounts", status_code=201)
def create_account(body: AccountIn, _=Depends(require_admin)):
    author = fetch_one("SELECT au_fname, au_lname FROM public.authors WHERE au_id = %s", (body.au_id,))
    if not author:
        raise HTTPException(404, "No author with that ID.")
    name = (body.display_name or "").strip() or f"{author['au_fname']} {author['au_lname']}"
    try:
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO public.app_user (username, display_name, email, password_hash, role, au_id)
                VALUES (%s, %s, %s, %s, 'author', %s) RETURNING user_id
                """,
                (body.username.lstrip("@").lower(), name, _blank(body.email),
                 hash_password(body.password), body.au_id),
            )
            user_id = cur.fetchone()["user_id"]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "That username is taken, or this author already has an account.")
    return {"user_id": user_id}


def _account_or_404(user_id):
    row = fetch_one(
        "SELECT user_id, username, display_name, email, role, au_id, is_active, created_at, last_login_at FROM public.app_user WHERE user_id = %s",
        (user_id,))
    if not row:
        raise HTTPException(404, "No account with that ID.")
    return row


@app.get("/api/accounts/{user_id}")
def account(user_id: int, _=Depends(require_admin)):
    row = _account_or_404(user_id)
    row["author"] = fetch_one(
        "SELECT au_id, au_fname, au_lname, phone, city, state FROM public.authors WHERE au_id = %s",
        (row["au_id"],)) if row["au_id"] else None
    row["books"] = fetch_all(
        f"""
        SELECT t.title_id, t.title, t.price, p.pub_name, COALESCE(s.sold, 0) AS sold
        FROM public.titleauthor ta JOIN public.titles t USING (title_id)
        LEFT JOIN public.publishers p USING (pub_id) LEFT JOIN {SOLD} s USING (title_id)
        WHERE ta.au_id = %s ORDER BY t.title_id
        """, (row["au_id"],)) if row["au_id"] else []
    row["last_sign_in"] = row.pop("last_login_at")
    return row


class AccountUpdateIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    email: str | None = Field(default=None, max_length=120)
    username: str = Field(min_length=3, max_length=40, pattern=r"^@?[A-Za-z0-9_.-]+$")
    is_active: bool = True


@app.patch("/api/accounts/{user_id}")
def update_account(user_id: int, body: AccountUpdateIn, _=Depends(require_admin)):
    target = _account_or_404(user_id)
    if target["role"] == "admin" and not body.is_active:
        raise HTTPException(400, "The admin account can't be deactivated.")
    try:
        with transaction() as cur:
            cur.execute(
                "UPDATE public.app_user SET display_name = %s, email = %s, username = %s, is_active = %s WHERE user_id = %s",
                (body.display_name.strip(), _blank(body.email), body.username.lstrip("@").lower(),
                 body.is_active, user_id))
            if not body.is_active:
                cur.execute("UPDATE public.api_key SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "That username is taken.")
    return {"ok": True}


class PasswordSetIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@app.post("/api/accounts/{user_id}/password")
def set_password(user_id: int, body: PasswordSetIn, _=Depends(require_admin)):
    _account_or_404(user_id)
    with transaction() as cur:
        cur.execute("UPDATE public.app_user SET password_hash = %s WHERE user_id = %s",
                    (hash_password(body.password), user_id))
        # The old login details stop working everywhere.
        cur.execute("UPDATE public.api_key SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
    return {"ok": True}


@app.delete("/api/accounts/{user_id}")
def delete_account(user_id: int, _=Depends(require_admin)):
    target = _account_or_404(user_id)
    if target["role"] == "admin":
        raise HTTPException(400, "The admin account can't be deleted here.")
    with transaction() as cur:
        cur.execute("DELETE FROM public.app_user WHERE user_id = %s", (user_id,))  # keys go with it
    return {"ok": True}


# ---------- admin: pubs data ----------

@app.get("/api/sales")
def sales(_=Depends(require_admin)):
    return fetch_all(
        """
        SELECT s.stor_id, s.ord_num, s.title_id, s.ord_date, st.stor_name, t.title, s.qty, s.payterms, s.qty * t.price AS amount
        FROM public.sales s
        JOIN public.titles t USING (title_id)
        LEFT JOIN public.stores st USING (stor_id)
        ORDER BY s.ord_date DESC, s.ord_num
        """)


@app.get("/api/sales/options")
def sale_options(_=Depends(require_admin)):
    """Choices for the Add sale form."""
    return {
        "stores": fetch_all("SELECT stor_id, stor_name FROM public.stores ORDER BY stor_name, stor_id"),
        "titles": fetch_all("SELECT title_id, title FROM public.titles ORDER BY title"),
        "payterms": [r["payterms"] for r in fetch_all("SELECT DISTINCT payterms FROM public.sales ORDER BY payterms")],
    }


class SaleIn(BaseModel):
    stor_id: str = Field(min_length=1, max_length=4)
    ord_num: str = Field(min_length=1, max_length=20)
    title_id: str = Field(min_length=1, max_length=6)
    ord_date: date
    qty: int = Field(gt=0, le=32767)
    payterms: str = Field(min_length=1, max_length=12)


@app.post("/api/sales", status_code=201)
def create_sale(body: SaleIn, _=Depends(require_admin)):
    ord_num = body.ord_num.strip()
    with transaction() as cur:
        cur.execute("SELECT 1 FROM public.stores WHERE stor_id = %s", (body.stor_id,))
        if not cur.fetchone():
            raise HTTPException(404, "No store with that ID.")
        cur.execute("SELECT 1 FROM public.titles WHERE title_id = %s", (body.title_id,))
        if not cur.fetchone():
            raise HTTPException(404, "No book with that ID.")
        cur.execute("SELECT 1 FROM public.sales WHERE stor_id = %s AND ord_num = %s AND title_id = %s",
                    (body.stor_id, ord_num, body.title_id))
        if cur.fetchone():
            raise HTTPException(409, f"Order {ord_num} at this store already has a line for this book. Edit that line instead.")
        cur.execute("INSERT INTO public.sales (stor_id, ord_num, ord_date, qty, payterms, title_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    (body.stor_id, ord_num, body.ord_date, body.qty, body.payterms.strip(), body.title_id))
    return {"ok": True, "stor_id": body.stor_id, "title_id": body.title_id, "ord_num": ord_num}


# A sale line is keyed by store, book and order number. The order number goes last as a
# path, because pubs order numbers may contain "/".

@app.get("/api/sales/{stor_id}/{title_id}/{ord_num:path}")
def sale(stor_id: str, title_id: str, ord_num: str, _=Depends(require_admin)):
    row = fetch_one(
        """
        SELECT s.stor_id, s.ord_num, s.title_id, s.ord_date::date AS ord_date, s.qty, s.payterms, st.stor_name, t.title,
               (SELECT array_agg(DISTINCT payterms ORDER BY payterms) FROM public.sales) AS payterms_used
        FROM public.sales s JOIN public.titles t USING (title_id) LEFT JOIN public.stores st USING (stor_id)
        WHERE s.stor_id = %s AND s.title_id = %s AND s.ord_num = %s
        """, (stor_id, title_id, ord_num))
    if not row:
        raise HTTPException(404, "No sale with that store, book and order number.")
    return row


class SaleUpdateIn(BaseModel):
    ord_date: date
    qty: int = Field(gt=0, le=32767)
    payterms: str = Field(min_length=1, max_length=12)


@app.put("/api/sales/{stor_id}/{title_id}/{ord_num:path}")
def update_sale(stor_id: str, title_id: str, ord_num: str, body: SaleUpdateIn, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute("UPDATE public.sales SET ord_date = %s, qty = %s, payterms = %s WHERE stor_id = %s AND title_id = %s AND ord_num = %s",
                    (body.ord_date, body.qty, body.payterms.strip(), stor_id, title_id, ord_num))
        if not cur.rowcount:
            raise HTTPException(404, "No sale with that store, book and order number.")
    return {"ok": True}


@app.delete("/api/sales/{stor_id}/{title_id}/{ord_num:path}")
def delete_sale(stor_id: str, title_id: str, ord_num: str, _=Depends(require_admin)):
    # Nothing points at a sale line; removing it only changes the sales figures.
    with transaction() as cur:
        cur.execute("DELETE FROM public.sales WHERE stor_id = %s AND title_id = %s AND ord_num = %s", (stor_id, title_id, ord_num))
        if not cur.rowcount:
            raise HTTPException(404, "No sale with that store, book and order number.")
    return {"ok": True}


@app.get("/api/stores")
def stores(_=Depends(require_admin)):
    return fetch_all(
        """
        SELECT st.stor_id, st.stor_name, st.stor_address, st.city, st.state, st.zip,
               COUNT(DISTINCT s.ord_num) AS orders, COALESCE(SUM(s.qty), 0) AS sold,
               COALESCE(SUM(s.qty * t.price), 0) AS revenue,
               (SELECT MAX(d.discount) FROM public.discounts d WHERE d.stor_id = st.stor_id) AS discount   -- best one if several
        FROM public.stores st
        LEFT JOIN public.sales s USING (stor_id)
        LEFT JOIN public.titles t USING (title_id)
        GROUP BY st.stor_id, st.stor_name, st.stor_address, st.city, st.state, st.zip
        ORDER BY st.stor_id
        """)


@app.get("/api/stores/{stor_id}")
def store(stor_id: str, _=Depends(require_admin)):
    row = fetch_one("SELECT stor_id, stor_name, stor_address, city, state, zip FROM public.stores WHERE stor_id = %s", (stor_id,))
    if not row:
        raise HTTPException(404, "No store with that ID.")
    return row


class StoreIn(BaseModel):
    stor_name: str = Field(min_length=1, max_length=40)
    stor_address: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    zip: str | None = Field(default=None, pattern=r"^\d{5}$")


class NewStoreIn(StoreIn):
    stor_id: str | None = Field(default=None, pattern=r"^\d{4}$")   # e.g. 9104; made up when empty


def _next_free_id(cur, table, column, what):
    """Next 4-digit ID after the highest one (stores, publishers); the lowest free one once 9999 is taken."""
    ref = {"t": psycopg2.sql.Identifier("public", table), "c": psycopg2.sql.Identifier(column)}
    cur.execute(psycopg2.sql.SQL("SELECT MIN(g) AS n FROM generate_series(COALESCE((SELECT MAX({c}::integer) FROM {t} "
                                 "WHERE {c} ~ '^[0-9]{{4}}$'), 999) + 1, 9999) g").format(**ref))
    n = cur.fetchone()["n"]
    if n is None:
        cur.execute(psycopg2.sql.SQL("SELECT MIN(g) AS n FROM generate_series(1000, 9999) g WHERE NOT EXISTS "
                                     "(SELECT 1 FROM {t} WHERE {c} = g::text)").format(**ref))
        n = cur.fetchone()["n"]
        if n is None:
            raise HTTPException(409, f"No free {what} IDs left. Enter an ID yourself.")
    return f"{n:04d}"


@app.post("/api/stores", status_code=201)
def create_store(body: NewStoreIn, _=Depends(require_admin)):
    with transaction() as cur:
        stor_id = body.stor_id or _next_free_id(cur, "stores", "stor_id", "store")
        cur.execute("SELECT 1 FROM public.stores WHERE stor_id = %s", (stor_id,))
        if cur.fetchone():
            raise HTTPException(409, f"Store ID {stor_id} is already used.")
        cur.execute(
            "INSERT INTO public.stores (stor_id, stor_name, stor_address, city, state, zip) VALUES (%s, %s, %s, %s, %s, %s)",
            (stor_id, body.stor_name.strip(), _blank(body.stor_address), _blank(body.city),
             _upper(body.state), body.zip))
    return {"ok": True, "stor_id": stor_id}


@app.put("/api/stores/{stor_id}")
def update_store(stor_id: str, body: StoreIn, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute(
            "UPDATE public.stores SET stor_name = %s, stor_address = %s, city = %s, state = %s, zip = %s WHERE stor_id = %s",
            (body.stor_name.strip(), _blank(body.stor_address), _blank(body.city), _upper(body.state),
             body.zip, stor_id))
        if not cur.rowcount:
            raise HTTPException(404, "No store with that ID.")
    return {"ok": True, "stor_id": stor_id}


@app.delete("/api/stores/{stor_id}")
def delete_store(stor_id: str, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute("SELECT stor_name FROM public.stores WHERE stor_id = %s", (stor_id,))
        found = cur.fetchone()
        if not found:
            raise HTTPException(404, "No store with that ID.")
        cur.execute("SELECT COUNT(*) AS n FROM public.sales WHERE stor_id = %s", (stor_id,))
        n = cur.fetchone()["n"]
        if n:
            # Deleting would also delete sales history, so refuse instead.
            raise HTTPException(409, f"{found['stor_name']} has {n} sale{'' if n == 1 else 's'}, so it can't be deleted.")
        # Discounts tied to this store only apply to it.
        cur.execute("DELETE FROM public.discounts WHERE stor_id = %s", (stor_id,))
        cur.execute("DELETE FROM public.stores WHERE stor_id = %s", (stor_id,))
    return {"ok": True, "deleted": stor_id}


@app.get("/api/discounts")
def discounts(_=Depends(require_admin)):
    return fetch_all(
        """
        SELECT d.discounttype, d.stor_id, st.stor_name, d.lowqty, d.highqty, d.discount
        FROM public.discounts d LEFT JOIN public.stores st USING (stor_id)
        ORDER BY d.discount DESC
        """)


# pubs.discounts has no key (and may hold identical rows), so a discount is named by
# all of its values: edits and deletes say what the row looked like and change one such row.

class DiscountIn(BaseModel):
    discounttype: str = Field(min_length=1, max_length=40)
    stor_id: str | None = Field(default=None, max_length=4)   # empty = every store
    lowqty: int | None = Field(default=None, ge=0, le=32767)
    highqty: int | None = Field(default=None, ge=0, le=32767)
    discount: Decimal = Field(ge=0, le=Decimal("99.99"), decimal_places=2)


class DiscountChangeIn(BaseModel):
    original: DiscountIn
    discount: DiscountIn


def _find_discount(cur, d):
    cur.execute(
        """
        SELECT ctid FROM public.discounts
        WHERE discounttype = %s AND stor_id IS NOT DISTINCT FROM %s AND lowqty IS NOT DISTINCT FROM %s
          AND highqty IS NOT DISTINCT FROM %s AND discount = %s
        LIMIT 1 FOR UPDATE
        """, (d.discounttype, d.stor_id, d.lowqty, d.highqty, d.discount))
    row = cur.fetchone()
    if not row:
        raise HTTPException(409, "This discount has changed or was deleted since the page loaded. Reload and try again.")
    return row["ctid"]


def _check_discount(cur, d):
    if d.lowqty is not None and d.highqty is not None and d.highqty < d.lowqty:
        raise HTTPException(422, "High qty can't be below low qty.")
    if d.stor_id:
        cur.execute("SELECT 1 FROM public.stores WHERE stor_id = %s", (d.stor_id,))
        if not cur.fetchone():
            raise HTTPException(404, "No store with that ID.")


@app.post("/api/discounts", status_code=201)
def create_discount(body: DiscountIn, _=Depends(require_admin)):
    with transaction() as cur:
        _check_discount(cur, body)
        cur.execute(
            "INSERT INTO public.discounts (discounttype, stor_id, lowqty, highqty, discount) VALUES (%s, %s, %s, %s, %s)",
            (body.discounttype.strip(), body.stor_id or None, body.lowqty, body.highqty, body.discount))
    return {"ok": True}


@app.put("/api/discounts")
def update_discount(body: DiscountChangeIn, _=Depends(require_admin)):
    d = body.discount
    with transaction() as cur:
        ctid = _find_discount(cur, body.original)
        _check_discount(cur, d)
        cur.execute(
            "UPDATE public.discounts SET discounttype = %s, stor_id = %s, lowqty = %s, highqty = %s, discount = %s WHERE ctid = %s::tid",
            (d.discounttype.strip(), d.stor_id or None, d.lowqty, d.highqty, d.discount, ctid))
    return {"ok": True}


@app.delete("/api/discounts")
def delete_discount(body: DiscountIn, _=Depends(require_admin)):
    with transaction() as cur:
        ctid = _find_discount(cur, body)
        cur.execute("DELETE FROM public.discounts WHERE ctid = %s::tid", (ctid,))
    return {"ok": True}


@app.get("/api/employees")
def employees(_=Depends(require_admin)):
    return fetch_all(
        """
        SELECT e.emp_id, e.fname || ' ' || e.lname AS name, j.job_desc, e.job_lvl, p.pub_name, e.hire_date
        FROM public.employee e
        LEFT JOIN public.jobs j USING (job_id)
        LEFT JOIN public.publishers p USING (pub_id)
        ORDER BY e.lname, e.fname
        """)


@app.get("/api/employees/options")
def employee_options(_=Depends(require_admin)):
    """Choices for the Add and Edit employee forms."""
    return {
        "jobs": fetch_all("SELECT job_id, job_desc, min_lvl, max_lvl FROM public.jobs ORDER BY job_id"),
        "publishers": fetch_all("SELECT pub_id, pub_name FROM public.publishers ORDER BY pub_name"),
    }


@app.get("/api/employees/{emp_id}")
def employee(emp_id: str, _=Depends(require_admin)):
    row = fetch_one("SELECT emp_id, fname, minit, lname, job_id, job_lvl, pub_id, hire_date FROM public.employee WHERE emp_id = %s",
                    (emp_id,))
    if not row:
        raise HTTPException(404, "No employee with that ID.")
    return row


class EmployeeIn(BaseModel):
    fname: str = Field(min_length=1, max_length=20)
    minit: str | None = Field(default=None, pattern=r"^[A-Za-z]$")
    lname: str = Field(min_length=1, max_length=30)
    job_id: int
    job_lvl: int | None = Field(default=None, ge=0, le=250)
    pub_id: str = Field(min_length=1, max_length=4)
    hire_date: date


class NewEmployeeIn(EmployeeIn):
    emp_id: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}\d{5}[A-Za-z]$")   # e.g. PMA42628M; made up when empty


def _check_employee(cur, body, current=None):
    """Job and publisher must exist; a new or changed job/level must fit the job's level range."""
    cur.execute("SELECT job_desc, min_lvl, max_lvl FROM public.jobs WHERE job_id = %s", (body.job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(404, "No job with that ID.")
    cur.execute("SELECT 1 FROM public.publishers WHERE pub_id = %s", (body.pub_id,))
    if not cur.fetchone():
        raise HTTPException(404, "No publisher with that ID.")
    # Some pubs rows are already outside their range, so an unchanged job and level may stay.
    changed = current is None or (body.job_id, body.job_lvl) != (current["job_id"], current["job_lvl"])
    if changed and body.job_lvl is not None and not job["min_lvl"] <= body.job_lvl <= job["max_lvl"]:
        raise HTTPException(422, f"Level for {job['job_desc']} must be {job['min_lvl']}–{job['max_lvl']}.")


def _new_emp_id(cur, body):
    # pubs IDs are initials + five digits + a letter (pubs uses M/F there; we don't guess, so X).
    # Accents are dropped (Ída -> I); a name with no A-Z letter gives X.
    letter = lambda v: next((ch for ch in unicodedata.normalize("NFKD", v or "").upper() if "A" <= ch <= "Z"), "X")
    initials = letter(body.fname) + letter(body.minit) + letter(body.lname)
    for _ in range(50):
        emp_id = f"{initials}{secrets.randbelow(100000):05d}X"
        cur.execute("SELECT 1 FROM public.employee WHERE emp_id = %s", (emp_id,))
        if not cur.fetchone():
            return emp_id
    raise HTTPException(409, "Couldn't find a free employee ID. Enter one yourself.")


@app.post("/api/employees", status_code=201)
def create_employee(body: NewEmployeeIn, _=Depends(require_admin)):
    with transaction() as cur:
        _check_employee(cur, body)
        emp_id = body.emp_id.upper() if body.emp_id else _new_emp_id(cur, body)
        cur.execute("SELECT 1 FROM public.employee WHERE emp_id = %s", (emp_id,))
        if cur.fetchone():
            raise HTTPException(409, f"Employee ID {emp_id} is already used.")
        cur.execute(
            """
            INSERT INTO public.employee (emp_id, fname, minit, lname, job_id, job_lvl, pub_id, hire_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (emp_id, body.fname.strip(), _upper(body.minit), body.lname.strip(), body.job_id,
             body.job_lvl, body.pub_id, body.hire_date))
    return {"ok": True, "emp_id": emp_id}


@app.put("/api/employees/{emp_id}")
def update_employee(emp_id: str, body: EmployeeIn, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute("SELECT job_id, job_lvl FROM public.employee WHERE emp_id = %s", (emp_id,))
        current = cur.fetchone()
        if not current:
            raise HTTPException(404, "No employee with that ID.")
        _check_employee(cur, body, current)
        cur.execute(
            """
            UPDATE public.employee SET fname = %s, minit = %s, lname = %s, job_id = %s, job_lvl = %s, pub_id = %s, hire_date = %s
            WHERE emp_id = %s
            """,
            (body.fname.strip(), _upper(body.minit), body.lname.strip(), body.job_id, body.job_lvl,
             body.pub_id, body.hire_date, emp_id))
    return {"ok": True, "emp_id": emp_id}


@app.delete("/api/employees/{emp_id}")
def delete_employee(emp_id: str, _=Depends(require_admin)):
    # Nothing in pubs points at an employee, so the row can go on its own.
    with transaction() as cur:
        cur.execute("DELETE FROM public.employee WHERE emp_id = %s", (emp_id,))
        if not cur.rowcount:
            raise HTTPException(404, "No employee with that ID.")
    return {"ok": True, "deleted": emp_id}


@app.get("/api/publishers")
def publishers(_=Depends(require_admin)):
    return fetch_all(
        f"""
        SELECT p.pub_id, p.pub_name, p.city, p.state, p.country,
               COUNT(t.title_id) AS books, COALESCE(SUM(s.sold), 0) AS sold, COALESCE(SUM(s.revenue), 0) AS revenue
        FROM public.publishers p
        LEFT JOIN public.titles t USING (pub_id)
        LEFT JOIN {SOLD} s USING (title_id)
        GROUP BY p.pub_id, p.pub_name, p.city, p.state, p.country
        ORDER BY p.pub_id
        """)


@app.get("/api/publishers/{pub_id}")
def publisher(pub_id: str, _=Depends(require_admin)):
    row = fetch_one("SELECT pub_id, pub_name, city, state, country FROM public.publishers WHERE pub_id = %s", (pub_id,))
    if not row:
        raise HTTPException(404, "No publisher with that ID.")
    return row


class PublisherIn(BaseModel):
    pub_name: str = Field(min_length=1, max_length=40)
    city: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    country: str = Field(min_length=1, max_length=30)


class NewPublisherIn(PublisherIn):
    pub_id: str | None = Field(default=None, pattern=r"^\d{4}$")   # e.g. 9004; made up when empty


@app.post("/api/publishers", status_code=201)
def create_publisher(body: NewPublisherIn, _=Depends(require_admin)):
    with transaction() as cur:
        pub_id = body.pub_id or _next_free_id(cur, "publishers", "pub_id", "publisher")
        cur.execute("SELECT 1 FROM public.publishers WHERE pub_id = %s", (pub_id,))
        if cur.fetchone():
            raise HTTPException(409, f"Publisher ID {pub_id} is already used.")
        cur.execute(
            "INSERT INTO public.publishers (pub_id, pub_name, city, state, country) VALUES (%s, %s, %s, %s, %s)",
            (pub_id, body.pub_name.strip(), _blank(body.city), _upper(body.state),
             body.country.strip()))
    return {"ok": True, "pub_id": pub_id}


@app.put("/api/publishers/{pub_id}")
def update_publisher(pub_id: str, body: PublisherIn, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute(
            "UPDATE public.publishers SET pub_name = %s, city = %s, state = %s, country = %s WHERE pub_id = %s",
            (body.pub_name.strip(), _blank(body.city), _upper(body.state),
             body.country.strip(), pub_id))
        if not cur.rowcount:
            raise HTTPException(404, "No publisher with that ID.")
    return {"ok": True, "pub_id": pub_id}


@app.delete("/api/publishers/{pub_id}")
def delete_publisher(pub_id: str, _=Depends(require_admin)):
    with transaction() as cur:
        cur.execute("SELECT pub_name FROM public.publishers WHERE pub_id = %s", (pub_id,))
        found = cur.fetchone()
        if not found:
            raise HTTPException(404, "No publisher with that ID.")
        cur.execute("SELECT (SELECT COUNT(*) FROM public.titles WHERE pub_id = %s) AS books, "
                    "(SELECT COUNT(*) FROM public.employee WHERE pub_id = %s) AS employees", (pub_id, pub_id))
        n = cur.fetchone()
        # Books and employees would lose their publisher, so refuse instead.
        held = [f"{n[k]} {k if n[k] != 1 else k[:-1]}" for k in ("books", "employees") if n[k]]
        if held:
            raise HTTPException(409, f"{found['pub_name']} has {' and '.join(held)}, so it can't be deleted.")
        # pub_info (logo, PR text) belongs to the publisher; not every pubs copy has that table.
        cur.execute("SELECT to_regclass('public.pub_info') AS t")
        if cur.fetchone()["t"]:
            cur.execute("DELETE FROM public.pub_info WHERE pub_id = %s", (pub_id,))
        cur.execute("DELETE FROM public.publishers WHERE pub_id = %s", (pub_id,))
    return {"ok": True, "deleted": pub_id}


# ---------- frontend ----------

# The frontend loads only its own script and stylesheet; app.js sets some inline style="" attributes.
# /docs is left out because Swagger UI loads its files from a CDN.
FRONTEND_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path == "/" or path.startswith("/static/"):
        # Browsers must recheck the page and its script on every load, so an updated
        # frontend shows up without a hard refresh (unchanged files still answer 304).
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Content-Security-Policy"] = FRONTEND_CSP
        response.headers["X-Frame-Options"] = "DENY"
    elif path.startswith("/api/"):
        # Answers hold account and sales data: never keep them in a browser or proxy cache.
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/", include_in_schema=False)
def index():
    # Version the script and stylesheet by their change time, so a browser holding an
    # older copy has to fetch the new one.
    html = (FRONTEND / "index.html").read_text()
    for name in ("app.js", "styles.css"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((FRONTEND / name).stat().st_mtime)}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
