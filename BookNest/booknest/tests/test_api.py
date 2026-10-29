"""End-to-end API test against a THROWAWAY Postgres loaded with tests/pubs_sample.sql.

Point BOOKNEST_DB_* at an empty test database (never the real one), then:
    .venv/bin/python -m tests.test_api
"""
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, db, manage
from app import main as main_module
from app.db import fetch_all, fetch_one, transaction
from app.main import app
from app.security import hash_api_key, hash_password

HERE = Path(__file__).resolve().parent
client = TestClient(app)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def from_ip(ip, raise_errors=True):
    """A client whose requests come from `ip` (the sign-in limits count per IP)."""
    async def asgi(scope, receive, send):
        await app(dict(scope, client=(ip, 5000)) if scope["type"] == "http" else scope, receive, send)
    return TestClient(asgi, raise_server_exceptions=raise_errors)


def login(username, password, role, remember=False, via=None):
    return (via or client).post("/api/login", json={"username": username, "password": password, "role": role, "remember": remember})


def setup():
    with transaction() as cur:
        cur.execute("SELECT to_regclass('public.titles') AS t")
        if cur.fetchone()["t"]:
            sys.exit("This database already has pubs tables. Use an empty throwaway database.")
        cur.execute((HERE / "pubs_sample.sql").read_text())
    manage.migrate()
    manage.migrate()   # running it twice must be harmless
    with transaction() as cur:
        cur.execute("INSERT INTO public.app_user (username, display_name, email, password_hash, role) "
                    "VALUES ('newmoon', 'New Moon Books', 'admin@newmoonbooks.com', %s, 'admin')",
                    (hash_password("admin-pass-123"),))


def main():
    setup()

    # --- login and API keys ---
    check("wrong password is refused", login("newmoon", "nope", "admin").status_code == 401)
    check("unknown user is refused", login("ghost", "whatever1", "admin").status_code == 401)
    check("admin can't use the author login", login("newmoon", "admin-pass-123", "author").status_code == 403)
    r = login("@newmoon", "admin-pass-123", "admin")
    check("admin signs in and gets an API key", r.status_code == 200 and r.json()["api_key"].startswith("bn_"))
    admin_key = r.json()["api_key"]
    stored = fetch_one("SELECT key_hash, key_prefix FROM public.api_key ORDER BY key_id DESC LIMIT 1")
    check("only a hash of the key is stored", stored["key_hash"] != admin_key and len(stored["key_hash"]) == 64
          and admin_key not in str(fetch_one("SELECT * FROM public.api_key ORDER BY key_id DESC LIMIT 1")))
    check("no key -> 401", client.get("/api/books").status_code == 401)
    check("made-up key -> 401", client.get("/api/books", headers=auth("bn_fake")).status_code == 401)

    # --- admin data ---
    s = client.get("/api/summary", headers=auth(admin_key)).json()
    check("summary totals match pubs sample (18 books, 23 authors, 493 sold)",
          (s["totals"]["books"], s["totals"]["authors"], s["totals"]["sold"]) == (18, 23, 493))
    check("summary revenue is 6676.90", round(float(s["totals"]["revenue"]), 2) == 6676.90)
    for path, n in [("/api/books", 18), ("/api/sales", 21), ("/api/stores", 6), ("/api/discounts", 3),
                    ("/api/employees", 14), ("/api/publishers", 8)]:
        r = client.get(path, headers=auth(admin_key))
        check(f"admin GET {path} returns {n} rows", r.status_code == 200 and len(r.json()) == n)

    # --- create an author account ---
    free = client.get("/api/authors/without-account", headers=auth(admin_key)).json()
    check("23 authors have no account yet", len(free) == 23)
    r = client.post("/api/accounts", headers=auth(admin_key),
                    json={"au_id": "213-46-8915", "username": "mgreen", "password": "short"})
    check("password under 8 characters is refused", r.status_code == 422)
    r = client.post("/api/accounts", headers=auth(admin_key),
                    json={"au_id": "213-46-8915", "username": "mgreen", "password": "green-pass-1", "email": "marjorie@booknest.app"})
    check("admin creates Marjorie Green's account", r.status_code == 201)
    marjorie_id = r.json()["user_id"]
    r = client.post("/api/accounts", headers=auth(admin_key),
                    json={"au_id": "213-46-8915", "username": "mgreen2", "password": "green-pass-1"})
    check("a second account for the same author is refused", r.status_code == 409)
    r = client.post("/api/accounts", headers=auth(admin_key),
                    json={"au_id": "172-32-1176", "username": "mgreen", "password": "white-pass-1"})
    check("duplicate username is refused", r.status_code == 409)
    r = client.post("/api/accounts", headers=auth(admin_key),
                    json={"au_id": "999-99-9999", "username": "nobody", "password": "nobody-pass"})
    check("account for a non-existent author is refused", r.status_code == 404)
    r = client.post("/api/accounts", headers=auth(admin_key),
                    json={"au_id": "172-32-1176", "username": "jwhite", "password": "white-pass-1"})
    johnson_id = r.json()["user_id"]

    detail = client.get(f"/api/accounts/{marjorie_id}", headers=auth(admin_key)).json()
    check("admin sees Marjorie's 2 books, 50 copies sold",
          len(detail["books"]) == 2 and sum(b["sold"] for b in detail["books"]) == 50)

    # --- author access ---
    check("author can't use the admin login", login("mgreen", "green-pass-1", "admin").status_code == 403)
    r = login("mgreen", "green-pass-1", "author")
    check("author signs in with the details the admin set", r.status_code == 200)
    author_key = r.json()["api_key"]
    mine = client.get("/api/me/books", headers=auth(author_key)).json()
    check("author sees only their own 2 books (BU1032, BU2075), 50 sold",
          [b["title_id"] for b in mine["books"]] == ["BU1032", "BU2075"] and mine["totals"]["sold"] == 50)
    me = client.get("/api/me", headers=auth(author_key)).json()
    check("author /api/me shows their pubs author record", me["author"]["city"] == "Oakland" and me["role"] == "author")
    for path in ["/api/summary", "/api/books", "/api/accounts", "/api/sales", "/api/stores", "/api/discounts",
                 "/api/employees", "/api/publishers", f"/api/accounts/{johnson_id}"]:
        check(f"author is refused {path}", client.get(path, headers=auth(author_key)).status_code == 403)
    o = client.get("/api/me/books/options", headers=auth(author_key)).json()
    check("author gets add-book options", "business" in o["types"] and len(o["publishers"]) > 0)
    r = client.post("/api/me/books", headers=auth(author_key), json={
        "title": "Marjorie's Second Guide", "type": "business", "pub_id": "1389", "price": 15, "advance": 99999, "au_id": "172-32-1176"})
    mb = r.json().get("title_id")
    check("author adds a book under their own name", r.status_code == 201 and fetch_one(
        "SELECT ta.au_id, t.advance, t.royalty FROM public.titleauthor ta JOIN public.titles t USING (title_id) WHERE title_id = %s", (mb,))
        == {"au_id": "213-46-8915", "advance": None, "royalty": None})
    check("author's new book shows on My Books", mb in [b["title_id"] for b in client.get("/api/me/books", headers=auth(author_key)).json()["books"]])
    check("author: unknown publisher -> 404, empty title -> 422", [client.post("/api/me/books", headers=auth(author_key), json=b).status_code
          for b in [{"title": "X", "type": "business", "pub_id": "0000"}, {"title": "", "type": "business", "pub_id": "1389"}]] == [404, 422])
    check("admin can't use the author add-book route", client.post("/api/me/books", headers=auth(admin_key),
          json={"title": "X", "type": "business", "pub_id": "1389"}).status_code == 403)
    b = client.get(f"/api/me/books/{mb}", headers=auth(author_key)).json()
    check("author reads their new book", b.get("title") == "Marjorie's Second Guide")
    r = client.put(f"/api/me/books/{mb}", headers=auth(author_key), json={
        "title": "Marjorie's Better Guide", "type": "business", "pub_id": "0736", "price": 17.5, "pubdate": None, "notes": "Revised"})
    check("author edits their own book", r.status_code == 200 and fetch_one(
        "SELECT title, pub_id, price, notes FROM public.titles WHERE title_id = %s", (mb,))
        == {"title": "Marjorie's Better Guide", "pub_id": "0736", "price": 17.5, "notes": "Revised"})
    check("author can't read, edit or delete someone else's book (404)", [
        client.get("/api/me/books/PS2091", headers=auth(author_key)).status_code,
        client.put("/api/me/books/PS2091", headers=auth(author_key), json={"title": "X", "type": "business", "pub_id": "1389"}).status_code,
        client.delete("/api/me/books/PS2091", headers=auth(author_key)).status_code] == [404, 404, 404])
    check("author can't delete a co-authored book (409)", client.delete("/api/me/books/BU1032", headers=auth(author_key)).status_code == 409)
    check("author can't delete a book with sales (409)", client.delete("/api/me/books/BU2075", headers=auth(author_key)).status_code == 409)
    check("author deletes their own book with no sales", client.delete(f"/api/me/books/{mb}", headers=auth(author_key)).status_code == 200
          and fetch_one("SELECT 1 AS x FROM public.titles WHERE title_id = %s", (mb,)) is None)
    check("author can't delete books", client.delete("/api/books/MC3026", headers=auth(author_key)).status_code == 403)
    check("author can't add books", client.post("/api/books", headers=auth(author_key),
          json={"title": "Nope", "type": "business", "pub_id": "1389"}).status_code == 403)
    check("author can't edit books", client.put("/api/books/BU1032", headers=auth(author_key),
          json={"title": "Nope", "type": "business", "pub_id": "1389"}).status_code == 403)
    check("author can't delete authors", client.delete("/api/authors/893-72-1158", headers=auth(author_key)).status_code == 403)
    check("author can't edit publishers", client.put("/api/publishers/1389", headers=auth(author_key),
          json={"pub_name": "No", "country": "USA"}).status_code == 403)
    check("author can't add stores", client.post("/api/stores", headers=auth(author_key),
          json={"stor_name": "No"}).status_code == 403)
    check("author can't add sales", client.post("/api/sales", headers=auth(author_key), json={"stor_id": "6380", "ord_num": "X1",
          "title_id": "BU1032", "ord_date": "2026-10-01", "qty": 1, "payterms": "Net 30"}).status_code == 403)
    check("author can't edit sales", client.put("/api/sales/6380/BU1032/6871", headers=auth(author_key),
          json={"ord_date": "1994-09-14", "qty": 1, "payterms": "Net 60"}).status_code == 403)
    check("author can't delete sales", client.delete("/api/sales/6380/BU1032/6871", headers=auth(author_key)).status_code == 403)
    check("author can't add discounts", client.post("/api/discounts", headers=auth(author_key),
          json={"discounttype": "Nope", "discount": "1.00"}).status_code == 403)
    check("author can't edit discounts", client.put("/api/discounts", headers=auth(author_key), json={
          "original": {"discounttype": "Initial Customer", "discount": "10.50"},
          "discount": {"discounttype": "Nope", "discount": "1.00"}}).status_code == 403)
    check("author can't delete discounts", client.request("DELETE", "/api/discounts", headers=auth(author_key),
          json={"discounttype": "Initial Customer", "discount": "10.50"}).status_code == 403)
    check("author can't add employees", client.post("/api/employees", headers=auth(author_key), json={
          "fname": "No", "lname": "Pe", "job_id": 13, "pub_id": "0877", "hire_date": "2026-01-05"}).status_code == 403)
    check("author can't edit employees", client.put("/api/employees/PMA42628M", headers=auth(author_key), json={
          "fname": "No", "lname": "Pe", "job_id": 13, "pub_id": "0877", "hire_date": "1992-08-27"}).status_code == 403)
    check("author can't delete employees", client.delete("/api/employees/PMA42628M", headers=auth(author_key)).status_code == 403)
    check("author can't edit stores", client.put("/api/stores/6380", headers=auth(author_key),
          json={"stor_name": "No"}).status_code == 403)
    check("author can't delete stores", client.delete("/api/stores/6380", headers=auth(author_key)).status_code == 403)
    check("author can't add publishers", client.post("/api/publishers", headers=auth(author_key),
          json={"pub_name": "No", "country": "USA"}).status_code == 403)
    check("author can't delete publishers", client.delete("/api/publishers/9999", headers=auth(author_key)).status_code == 403)
    check("author can't add authors", client.post("/api/authors", headers=auth(author_key),
          json={"au_fname": "No", "au_lname": "Pe", "phone": "1", "contract": True}).status_code == 403)
    check("author can't edit authors", client.put("/api/authors/213-46-8915", headers=auth(author_key),
          json={"au_fname": "No", "au_lname": "Pe", "phone": "1", "contract": True}).status_code == 403)
    check("author can't create accounts", client.post("/api/accounts", headers=auth(author_key),
          json={"au_id": "893-72-1158", "username": "x123", "password": "whatever1"}).status_code == 403)
    check("author can't change their own profile", client.patch("/api/me", headers=auth(author_key),
          json={"display_name": "Hacker"}).status_code == 403)
    check("author can't change their own password", client.post("/api/me/password", headers=auth(author_key),
          json={"current_password": "green-pass-1", "new_password": "new-pass-123"}).status_code == 403)
    check("admin has no /api/me/books", client.get("/api/me/books", headers=auth(admin_key)).status_code == 403)

    # --- admin manages the author ---
    r = client.post(f"/api/accounts/{marjorie_id}/password", headers=auth(admin_key), json={"password": "new-green-pass"})
    check("admin sets a new password", r.status_code == 200)
    check("author's old API key stops working after a password reset",
          client.get("/api/me", headers=auth(author_key)).status_code == 401)
    check("old password no longer works", login("mgreen", "green-pass-1", "author").status_code == 401)
    r = login("mgreen", "new-green-pass", "author")
    check("new password works", r.status_code == 200)
    author_key = r.json()["api_key"]
    r = client.patch(f"/api/accounts/{marjorie_id}", headers=auth(admin_key),
                     json={"display_name": "Marjorie Green", "username": "mgreen", "email": None, "is_active": False})
    check("admin deactivates the account", r.status_code == 200)
    check("deactivated author is signed out", client.get("/api/me", headers=auth(author_key)).status_code == 401)
    check("deactivated author can't sign in", login("mgreen", "new-green-pass", "author").status_code == 401)

    # --- deleting books ---
    r = client.delete("/api/books/BU1032", headers=auth(admin_key))
    check("book with sales can't be deleted (409)", r.status_code == 409)
    r = client.delete("/api/books/MC3026", headers=auth(admin_key))
    check("book without sales is deleted", r.status_code == 200)
    r = client.delete("/api/books/PC9999", headers=auth(admin_key))
    check("book with an author but no sales is deleted", r.status_code == 200)
    check("deleted books are gone", len(client.get("/api/books", headers=auth(admin_key)).json()) == 16)
    check("unknown book -> 404", client.delete("/api/books/XX0000", headers=auth(admin_key)).status_code == 404)

    # --- adding books ---
    opts = client.get("/api/books/options", headers=auth(admin_key)).json()
    check("add-book options list types, publishers and authors",
          "business" in opts["types"] and len(opts["publishers"]) > 0 and len(opts["authors"]) == 23)
    r = client.post("/api/books", headers=auth(admin_key), json={
        "title": "Databases for Busy People", "type": "business", "pub_id": "1389", "price": 24.5,
        "pubdate": "2026-10-01", "au_id": "213-46-8915"})
    new_id = r.json().get("title_id")
    check("book is added with the next free business ID", r.status_code == 201 and new_id == "BU7833")
    row = fetch_one("SELECT title, price, pubdate::text AS d, ytd_sales FROM public.titles WHERE title_id = %s", (new_id,))
    check("new book is stored in titles", row and row["title"] == "Databases for Busy People"
          and float(row["price"]) == 24.5 and row["d"] == "2026-10-01" and row["ytd_sales"] == 0)
    check("new book is linked to its author",
          fetch_one("SELECT au_ord, royaltyper FROM public.titleauthor WHERE title_id = %s", (new_id,)) == {"au_ord": 1, "royaltyper": 100})
    check("new book shows in the list", len(client.get("/api/books", headers=auth(admin_key)).json()) == 17)
    r = client.post("/api/books", headers=auth(admin_key), json={"title_id": "ps0001", "title": "No Author", "type": "psychology", "pub_id": "0736"})
    check("book with a given ID and no author is added", r.status_code == 201 and r.json()["title_id"] == "PS0001")
    check("used book ID -> 409", client.post("/api/books", headers=auth(admin_key),
          json={"title_id": "BU1032", "title": "Dup", "type": "business", "pub_id": "1389"}).status_code == 409)
    check("unknown publisher -> 404", client.post("/api/books", headers=auth(admin_key),
          json={"title": "X", "type": "business", "pub_id": "0000"}).status_code == 404)
    check("unknown author -> 404", client.post("/api/books", headers=auth(admin_key),
          json={"title": "X", "type": "business", "pub_id": "1389", "au_id": "000-00-0000"}).status_code == 404)
    check("bad ID, empty title, negative price -> 422", all(client.post("/api/books", headers=auth(admin_key), json=b).status_code == 422 for b in [
        {"title_id": "B1", "title": "X", "type": "business", "pub_id": "1389"},
        {"title": "", "type": "business", "pub_id": "1389"},
        {"title": "X", "type": "business", "pub_id": "1389", "price": -1}]))

    # --- editing books ---
    b = client.get(f"/api/books/{new_id}", headers=auth(admin_key)).json()
    check("book detail has its fields and author", b["title"] == "Databases for Busy People" and b["pub_id"] == "1389"
          and [a["au_id"] for a in b["authors"]] == ["213-46-8915"])
    r = client.put(f"/api/books/{new_id}", headers=auth(admin_key), json={
        "title": "Databases for Very Busy People", "type": "business", "pub_id": "0736", "price": 29.99, "royalty": 12,
        "pubdate": None, "notes": "Second edition", "au_id": "172-32-1176"})
    row = fetch_one("SELECT title, pub_id, price, royalty, notes, pubdate::text AS d FROM public.titles WHERE title_id = %s", (new_id,))
    check("book is edited, publish date kept when empty", r.status_code == 200 and row["title"] == "Databases for Very Busy People"
          and row["pub_id"] == "0736" and float(row["price"]) == 29.99 and row["royalty"] == 12
          and row["notes"] == "Second edition" and row["d"] == "2026-10-01")
    authors_of = lambda t: [x["au_id"] for x in client.get(f"/api/books/{t}", headers=auth(admin_key)).json()["authors"]]
    check("editing changes the single author", authors_of(new_id) == ["172-32-1176"])
    client.put(f"/api/books/{new_id}", headers=auth(admin_key), json={"title": "T", "type": "business", "pub_id": "0736", "au_id": None})
    check("editing can remove the author", authors_of(new_id) == [])
    before = authors_of("BU1111")
    r = client.put("/api/books/BU1111", headers=auth(admin_key), json={"title": "Changed", "type": "business", "pub_id": "1389"})
    check("co-authored book is edited and keeps its authors", r.status_code == 200 and len(before) > 1 and authors_of("BU1111") == before)
    r = client.put("/api/books/BU1111", headers=auth(admin_key), json={"title": "Again", "type": "business", "pub_id": "1389", "au_id": None})
    check("co-authored book's author can't be changed (409), nothing saved", r.status_code == 409
          and fetch_one("SELECT title FROM public.titles WHERE title_id = 'BU1111'")["title"] == "Changed")
    check("edit unknown book -> 404", client.put("/api/books/XX0000", headers=auth(admin_key),
          json={"title": "X", "type": "business", "pub_id": "1389"}).status_code == 404)
    check("detail of unknown book -> 404", client.get("/api/books/XX0000", headers=auth(admin_key)).status_code == 404)

    # --- editing authors ---
    listed = client.get("/api/authors", headers=auth(admin_key)).json()
    green = next(a for a in listed if a["au_id"] == "213-46-8915")
    check("authors list has all 23 pubs authors, with accounts", len(listed) == 23 and green["username"] == "mgreen")
    a = client.get("/api/authors/213-46-8915", headers=auth(admin_key)).json()
    check("author detail has the pubs fields", (a["au_fname"], a["au_lname"]) == ("Marjorie", "Green") and "zip" in a)
    r = client.put("/api/authors/213-46-8915", headers=auth(admin_key), json={
        "au_fname": "Marjorie", "au_lname": "Green-Hale", "phone": "415 555-0199", "address": "1 New St.",
        "city": "Berkeley", "state": "ca", "zip": "94704", "contract": False})
    row = fetch_one("SELECT au_lname, phone, address, city, state, zip, contract FROM public.authors WHERE au_id = '213-46-8915'")
    check("author is edited (state upper-cased)", r.status_code == 200 and row == {
        "au_lname": "Green-Hale", "phone": "415 555-0199", "address": "1 New St.", "city": "Berkeley",
        "state": "CA", "zip": "94704", "contract": False})
    r = client.put("/api/authors/213-46-8915", headers=auth(admin_key), json={
        "au_fname": "Marjorie", "au_lname": "Green", "phone": "415 986-7020", "address": " ", "city": "", "contract": True})
    row = fetch_one("SELECT address, city, state, zip FROM public.authors WHERE au_id = '213-46-8915'")
    check("empty optional fields are cleared", r.status_code == 200 and set(row.values()) == {None})
    check("bad zip, state or empty name -> 422", all(client.put("/api/authors/213-46-8915", headers=auth(admin_key), json=b).status_code == 422 for b in [
        {"au_fname": "M", "au_lname": "G", "phone": "1", "zip": "123", "contract": True},
        {"au_fname": "M", "au_lname": "G", "phone": "1", "state": "CAL", "contract": True},
        {"au_fname": "", "au_lname": "G", "phone": "1", "contract": True}]))
    check("edit unknown author -> 404", client.put("/api/authors/000-00-0000", headers=auth(admin_key),
          json={"au_fname": "X", "au_lname": "Y", "phone": "1", "contract": True}).status_code == 404)

    # --- deleting authors ---
    check("author of a book can't be deleted (409)", client.delete("/api/authors/213-46-8915", headers=auth(admin_key)).status_code == 409)
    free = next(a for a in client.get("/api/authors", headers=auth(admin_key)).json() if a["books"] == 0)
    r = client.post("/api/accounts", headers=auth(admin_key),
                    json={"au_id": free["au_id"], "username": "nobooks", "password": "no-books-pass"})
    check("account made for an author with no books", r.status_code == 201)
    nb_key = login("nobooks", "no-books-pass", "author").json()["api_key"]
    r = client.delete(f"/api/authors/{free['au_id']}", headers=auth(admin_key))
    check("author with no books is deleted with their login", r.status_code == 200 and r.json()["account_deleted"] is True
          and fetch_one("SELECT 1 AS x FROM public.authors WHERE au_id = %s", (free["au_id"],)) is None
          and fetch_one("SELECT 1 AS x FROM public.app_user WHERE username = 'nobooks'") is None)
    check("deleted author's key stops working", client.get("/api/me", headers=auth(nb_key)).status_code == 401)
    check("delete unknown author -> 404", client.delete("/api/authors/000-00-0000", headers=auth(admin_key)).status_code == 404)

    # --- adding authors ---
    newbie = {"au_fname": "Ram", "au_lname": "Kumar", "phone": "408 555-0123", "city": "San Jose", "state": "ca", "zip": "95112", "contract": True}
    r = client.post("/api/authors", headers=auth(admin_key), json=newbie)
    ram_id = r.json().get("au_id", "")
    check("new author is added with a made-up pubs ID, no login", r.status_code == 201 and r.json()["user_id"] is None
          and len(ram_id) == 11 and ram_id[3] == "-" and ram_id[6] == "-"
          and fetch_one("SELECT au_fname, state FROM public.authors WHERE au_id = %s", (ram_id,)) == {"au_fname": "Ram", "state": "CA"})
    r = client.post("/api/authors", headers=auth(admin_key), json={**newbie, "au_fname": "Sita", "au_id": "111-22-3333",
                    "login": {"username": "sita", "password": "sita-pass-1", "email": "sita@example.com"}})
    check("new author with a given ID and a login", r.status_code == 201 and r.json()["au_id"] == "111-22-3333" and r.json()["user_id"])
    check("the new login works and sees no books yet", login("sita", "sita-pass-1", "author").status_code == 200)
    r = client.post("/api/authors", headers=auth(admin_key), json={**newbie, "au_fname": "Dup",
                    "login": {"username": "sita", "password": "another-pass"}})
    check("taken username -> 409 and no author row is left behind", r.status_code == 409
          and fetch_one("SELECT 1 AS x FROM public.authors WHERE au_fname = 'Dup'") is None)
    check("used author ID -> 409", client.post("/api/authors", headers=auth(admin_key),
          json={**newbie, "au_id": "111-22-3333"}).status_code == 409)
    check("bad author ID, short password or no phone -> 422", all(client.post("/api/authors", headers=auth(admin_key), json=b).status_code == 422 for b in [
        {**newbie, "au_id": "1112233"}, {**newbie, "login": {"username": "abc", "password": "short"}},
        {k: v for k, v in newbie.items() if k != "phone"}]))

    # --- editing and deleting sales (key: store, book, order number) ---
    sl = client.get("/api/sales/6380/BU1032/6871", headers=auth(admin_key)).json()
    check("sale detail", (sl["qty"], sl["payterms"], sl["ord_date"], sl["title"]) == (5, "Net 60", "1994-09-14", "The Busy Executive's Database Guide")
          and "Net 30" in sl["payterms_used"])
    r = client.put("/api/sales/6380/BU1032/6871", headers=auth(admin_key), json={"ord_date": "1994-10-01", "qty": 7, "payterms": "Net 30"})
    check("sale is edited", r.status_code == 200 and fetch_one(
        "SELECT qty, payterms, ord_date::date::text AS d FROM public.sales WHERE stor_id = '6380' AND ord_num = '6871' AND title_id = 'BU1032'")
        == {"qty": 7, "payterms": "Net 30", "d": "1994-10-01"})
    check("qty 0 or empty terms -> 422", all(client.put("/api/sales/6380/BU1032/6871", headers=auth(admin_key), json=b).status_code == 422
          for b in [{"ord_date": "1994-10-01", "qty": 0, "payterms": "Net 30"}, {"ord_date": "1994-10-01", "qty": 1, "payterms": ""}]))
    check("edit unknown sale -> 404", client.put("/api/sales/6380/BU1032/NOPE", headers=auth(admin_key),
          json={"ord_date": "1994-10-01", "qty": 1, "payterms": "Net 30"}).status_code == 404)
    with transaction() as cur:   # an order number with a slash in it
        cur.execute("INSERT INTO public.sales VALUES ('7066', 'QA/77', '1994-01-01', 2, 'Net 30', 'BU1032')")
    check("order number with a slash can be read", client.get("/api/sales/7066/BU1032/QA%2F77", headers=auth(admin_key)).json().get("ord_num") == "QA/77")
    check("order number with a slash can be deleted", client.delete("/api/sales/7066/BU1032/QA%2F77", headers=auth(admin_key)).status_code == 200
          and fetch_one("SELECT 1 AS x FROM public.sales WHERE ord_num = 'QA/77'") is None)
    check("delete unknown sale -> 404", client.delete("/api/sales/7066/BU1032/QA%2F77", headers=auth(admin_key)).status_code == 404)

    opts = client.get("/api/sales/options", headers=auth(admin_key)).json()
    check("sale options list stores, books and pay terms", len(opts["stores"]) > 0 and len(opts["titles"]) > 0 and "Net 60" in opts["payterms"])
    new_sale = {"stor_id": "7067", "ord_num": "NEW-1", "title_id": "BU1032", "ord_date": "2026-10-02", "qty": 4, "payterms": "Net 30"}
    r = client.post("/api/sales", headers=auth(admin_key), json=new_sale)
    check("sale is added", r.status_code == 201 and fetch_one(
        "SELECT qty, payterms, ord_date::date::text AS d FROM public.sales WHERE stor_id = '7067' AND ord_num = 'NEW-1' AND title_id = 'BU1032'")
        == {"qty": 4, "payterms": "Net 30", "d": "2026-10-02"})
    check("second book on the same order is added", client.post("/api/sales", headers=auth(admin_key),
          json={**new_sale, "title_id": "BU1111"}).status_code == 201)
    check("same store, order and book again -> 409", client.post("/api/sales", headers=auth(admin_key), json=new_sale).status_code == 409)
    check("unknown store or book -> 404", [client.post("/api/sales", headers=auth(admin_key), json=b).status_code
          for b in [{**new_sale, "stor_id": "0000"}, {**new_sale, "title_id": "XX0000"}]] == [404, 404])
    check("qty 0 or empty order number -> 422", all(client.post("/api/sales", headers=auth(admin_key), json=b).status_code == 422
          for b in [{**new_sale, "qty": 0}, {**new_sale, "ord_num": ""}]))

    # --- editing and deleting discounts (no key: rows are named by their values) ---
    volume = {"discounttype": "Volume Discount", "stor_id": None, "lowqty": 100, "highqty": 1000, "discount": "6.70"}
    r = client.put("/api/discounts", headers=auth(admin_key), json={"original": volume, "discount": {
        "discounttype": "Volume Discount", "stor_id": "8042", "lowqty": 50, "highqty": 1000, "discount": "7.25"}})
    row = fetch_one("SELECT stor_id, lowqty, highqty, discount::text AS d FROM public.discounts WHERE discounttype = 'Volume Discount'")
    check("discount is edited", r.status_code == 200 and row == {"stor_id": "8042", "lowqty": 50, "highqty": 1000, "d": "7.25"})
    check("editing a discount that changed meanwhile -> 409", client.put("/api/discounts", headers=auth(admin_key),
          json={"original": volume, "discount": volume}).status_code == 409)
    moved = {**volume, "stor_id": "8042", "lowqty": 50, "discount": "7.25"}
    check("high qty below low qty -> 422", client.put("/api/discounts", headers=auth(admin_key),
          json={"original": moved, "discount": {**moved, "lowqty": 500, "highqty": 10}}).status_code == 422)
    check("unknown store -> 404", client.put("/api/discounts", headers=auth(admin_key),
          json={"original": moved, "discount": {**moved, "stor_id": "0000"}}).status_code == 404)
    check("discount over 99.99 or negative -> 422", all(client.put("/api/discounts", headers=auth(admin_key),
          json={"original": moved, "discount": {**moved, "discount": v}}).status_code == 422 for v in ["100", "-1"]))
    with transaction() as cur:   # pubs allows identical rows; only one of them goes
        cur.execute("INSERT INTO public.discounts VALUES ('Initial Customer', NULL, NULL, NULL, 10.5)")
    initial = {"discounttype": "Initial Customer", "stor_id": None, "lowqty": None, "highqty": None, "discount": "10.50"}
    r = client.request("DELETE", "/api/discounts", headers=auth(admin_key), json=initial)
    check("deleting one of two identical discounts leaves the other", r.status_code == 200
          and fetch_one("SELECT COUNT(*) AS n FROM public.discounts WHERE discounttype = 'Initial Customer'")["n"] == 1)
    client.request("DELETE", "/api/discounts", headers=auth(admin_key), json=initial)
    check("deleting a discount that's gone -> 409", client.request("DELETE", "/api/discounts", headers=auth(admin_key),
          json=initial).status_code == 409)
    check("discount list after deletes", len(client.get("/api/discounts", headers=auth(admin_key)).json()) == 2)
    r = client.post("/api/discounts", headers=auth(admin_key), json={
        "discounttype": "Spring Sale", "stor_id": "7066", "lowqty": 10, "highqty": None, "discount": "12.50"})
    check("discount is added", r.status_code == 201 and fetch_one(
        "SELECT stor_id, lowqty, highqty, discount::text AS d FROM public.discounts WHERE discounttype = 'Spring Sale'")
        == {"stor_id": "7066", "lowqty": 10, "highqty": None, "d": "12.50"})
    check("discount for every store is added", client.post("/api/discounts", headers=auth(admin_key),
          json={"discounttype": "Everyone", "discount": "1"}).status_code == 201)
    check("bad new discount -> 422/404", [client.post("/api/discounts", headers=auth(admin_key), json=b).status_code for b in [
        {"discounttype": "", "discount": "1"}, {"discounttype": "X", "discount": "1", "lowqty": 9, "highqty": 1},
        {"discounttype": "X", "discount": "1", "stor_id": "0000"}]] == [422, 422, 404])
    check("discount list after adds", len(client.get("/api/discounts", headers=auth(admin_key)).json()) == 4)

    # --- editing and deleting employees ---
    opts = client.get("/api/employees/options", headers=auth(admin_key)).json()
    check("employee options list jobs and publishers", len(opts["jobs"]) == 12 and len(opts["publishers"]) > 0)
    emp = client.get("/api/employees/PMA42628M", headers=auth(admin_key)).json()
    check("employee detail", (emp["fname"], emp["lname"], emp["job_id"], emp["job_lvl"]) == ("Paolo", "Accorti", 13, 35))
    paolo = {"fname": "Paolo", "minit": "j", "lname": "Accorti-Ruiz", "job_id": 12, "job_lvl": 60, "pub_id": "0736", "hire_date": "1993-01-04"}
    r = client.put("/api/employees/PMA42628M", headers=auth(admin_key), json=paolo)
    row = fetch_one("SELECT minit, lname, job_id, job_lvl, pub_id, hire_date::text AS d FROM public.employee WHERE emp_id = 'PMA42628M'")
    check("employee is edited (initial upper-cased)", r.status_code == 200 and row == {
        "minit": "J", "lname": "Accorti-Ruiz", "job_id": 12, "job_lvl": 60, "pub_id": "0736", "d": "1993-01-04"})
    with transaction() as cur:   # a job with a narrow level range
        cur.execute("INSERT INTO public.jobs (job_id, job_desc, min_lvl, max_lvl) VALUES (99, 'Intern', 10, 20)")
    r = client.put("/api/employees/PMA42628M", headers=auth(admin_key), json={**paolo, "job_id": 99, "job_lvl": 60})
    check("level outside the new job's range -> 422", r.status_code == 422 and "10–20" in r.json()["detail"])
    check("level inside the range is saved", client.put("/api/employees/PMA42628M", headers=auth(admin_key),
          json={**paolo, "job_id": 99, "job_lvl": 15}).status_code == 200)
    with transaction() as cur:   # pubs data that is already outside its range
        cur.execute("UPDATE public.employee SET job_lvl = 30 WHERE emp_id = 'PMA42628M'")
    check("unchanged out-of-range level can stay while other fields change", client.put("/api/employees/PMA42628M", headers=auth(admin_key),
          json={**paolo, "job_id": 99, "job_lvl": 30, "lname": "Accorti"}).status_code == 200)
    check("unknown job or publisher -> 404", all(client.put("/api/employees/PMA42628M", headers=auth(admin_key), json=b).status_code == 404
          for b in [{**paolo, "job_id": 98}, {**paolo, "pub_id": "0000"}]))
    check("bad initial, empty name or no hire date -> 422", all(client.put("/api/employees/PMA42628M", headers=auth(admin_key), json=b).status_code == 422
          for b in [{**paolo, "minit": "JR"}, {**paolo, "fname": ""}, {k: v for k, v in paolo.items() if k != "hire_date"}]))
    check("edit unknown employee -> 404", client.put("/api/employees/XXX00000X", headers=auth(admin_key), json=paolo).status_code == 404)
    new_emp = {"fname": "Ída", "lname": "Ng", "job_id": 99, "job_lvl": 12, "pub_id": "1389", "hire_date": "2026-10-01"}
    r = client.post("/api/employees", headers=auth(admin_key), json=new_emp)
    made = r.json().get("emp_id", "")
    check("employee is added with an ID from the initials", r.status_code == 201
          and made[:3] == "IXN" and made[3:8].isdigit() and made[8] == "X")
    check("new employee is stored", fetch_one("SELECT fname, job_lvl FROM public.employee WHERE emp_id = %s", (made,))
          == {"fname": "Ída", "job_lvl": 12})
    r = client.post("/api/employees", headers=auth(admin_key), json={**new_emp, "emp_id": "abc12345f", "minit": "q"})
    check("employee with a given ID is added (upper-cased)", r.status_code == 201 and r.json()["emp_id"] == "ABC12345F")
    check("used employee ID -> 409", client.post("/api/employees", headers=auth(admin_key),
          json={**new_emp, "emp_id": "ABC12345F"}).status_code == 409)
    check("new employee's level must fit the job (422)", client.post("/api/employees", headers=auth(admin_key),
          json={**new_emp, "job_lvl": 60}).status_code == 422)
    check("bad employee ID -> 422", client.post("/api/employees", headers=auth(admin_key),
          json={**new_emp, "emp_id": "AB123"}).status_code == 422)
    check("employee is deleted", client.delete("/api/employees/PMA42628M", headers=auth(admin_key)).status_code == 200
          and len(client.get("/api/employees", headers=auth(admin_key)).json()) == 15)
    check("delete unknown employee -> 404", client.delete("/api/employees/PMA42628M", headers=auth(admin_key)).status_code == 404)

    # --- adding stores ---
    r = client.post("/api/stores", headers=auth(admin_key), json={"stor_name": "Corner Books", "city": "Spokane", "state": "wa", "zip": "99201"})
    check("store is added with the next free ID", r.status_code == 201 and r.json()["stor_id"] == "8043")
    row = fetch_one("SELECT stor_name, stor_address, city, state, zip FROM public.stores WHERE stor_id = '8043'")
    check("new store is stored", row == {"stor_name": "Corner Books", "stor_address": None, "city": "Spokane", "state": "WA", "zip": "99201"})
    r = client.post("/api/stores", headers=auth(admin_key), json={"stor_id": "1234", "stor_name": "Given ID"})
    check("store with a given ID is added", r.status_code == 201 and r.json()["stor_id"] == "1234")
    check("used store ID -> 409", client.post("/api/stores", headers=auth(admin_key),
          json={"stor_id": "6380", "stor_name": "Dup"}).status_code == 409)
    check("bad ID, zip or empty name -> 422", all(client.post("/api/stores", headers=auth(admin_key), json=b).status_code == 422 for b in [
        {"stor_id": "12", "stor_name": "X"}, {"stor_name": "X", "zip": "99"}, {"stor_name": ""}]))
    check("new stores show in the list", len(client.get("/api/stores", headers=auth(admin_key)).json()) == 8)

    # --- editing and deleting stores ---
    st = client.get("/api/stores/6380", headers=auth(admin_key)).json()
    check("store detail", st["stor_name"] == "Eric the Read Books" and st["zip"] == "98056")
    r = client.put("/api/stores/6380", headers=auth(admin_key), json={
        "stor_name": "Eric the Reader", "stor_address": "1 Pike St.", "city": "Tacoma", "state": "wa", "zip": "98402"})
    row = fetch_one("SELECT stor_name, stor_address, city, state, zip FROM public.stores WHERE stor_id = '6380'")
    check("store is edited (state upper-cased)", r.status_code == 200 and row == {
        "stor_name": "Eric the Reader", "stor_address": "1 Pike St.", "city": "Tacoma", "state": "WA", "zip": "98402"})
    check("bad zip, state or empty name -> 422", all(client.put("/api/stores/6380", headers=auth(admin_key), json=b).status_code == 422 for b in [
        {"stor_name": "X", "zip": "984"}, {"stor_name": "X", "state": "WAS"}, {"stor_name": ""}]))
    check("edit unknown store -> 404", client.put("/api/stores/0000", headers=auth(admin_key), json={"stor_name": "X"}).status_code == 404)
    check("store with sales can't be deleted (409)", client.delete("/api/stores/6380", headers=auth(admin_key)).status_code == 409)
    with transaction() as cur:   # a store with no sales but its own discount
        cur.execute("INSERT INTO public.stores (stor_id, stor_name) VALUES ('5555', 'Empty Shelf')")
        cur.execute("INSERT INTO public.discounts (discounttype, stor_id, discount) VALUES ('Shelf deal', '5555', 5)")
    r = client.delete("/api/stores/5555", headers=auth(admin_key))
    check("store with no sales is deleted with its discounts", r.status_code == 200
          and fetch_one("SELECT 1 AS x FROM public.stores WHERE stor_id = '5555'") is None
          and fetch_one("SELECT 1 AS x FROM public.discounts WHERE stor_id = '5555'") is None)
    check("delete unknown store -> 404", client.delete("/api/stores/0000", headers=auth(admin_key)).status_code == 404)

    # --- adding publishers ---
    r = client.post("/api/publishers", headers=auth(admin_key), json={"pub_name": "Harbor House", "city": "Seattle", "state": "wa", "country": "USA"})
    check("publisher is added with the next free ID (9999 taken -> lowest free)", r.status_code == 201 and r.json()["pub_id"] == "1000")
    row = fetch_one("SELECT pub_name, city, state, country FROM public.publishers WHERE pub_id = '1000'")
    check("new publisher is stored", row == {"pub_name": "Harbor House", "city": "Seattle", "state": "WA", "country": "USA"})
    r = client.post("/api/publishers", headers=auth(admin_key), json={"pub_id": "9004", "pub_name": "Given ID", "country": "Canada"})
    check("publisher with a given ID is added", r.status_code == 201 and r.json()["pub_id"] == "9004")
    check("used publisher ID -> 409", client.post("/api/publishers", headers=auth(admin_key),
          json={"pub_id": "1389", "pub_name": "Dup", "country": "USA"}).status_code == 409)
    check("bad ID, state or empty name -> 422", all(client.post("/api/publishers", headers=auth(admin_key), json=b).status_code == 422 for b in [
        {"pub_id": "12", "pub_name": "X", "country": "USA"}, {"pub_name": "X", "state": "WAS", "country": "USA"},
        {"pub_name": "", "country": "USA"}]))
    check("new publisher shows in the list", len(client.get("/api/publishers", headers=auth(admin_key)).json()) == 10)

    # --- editing and deleting publishers ---
    p = client.get("/api/publishers/1389", headers=auth(admin_key)).json()
    check("publisher detail", p["pub_name"] == "Algodata Infosystems" and p["country"] == "USA")
    r = client.put("/api/publishers/1389", headers=auth(admin_key),
                   json={"pub_name": "Algodata Inc.", "city": "Oakland", "state": "ca", "country": "USA"})
    row = fetch_one("SELECT pub_name, city, state, country FROM public.publishers WHERE pub_id = '1389'")
    check("publisher is edited (state upper-cased)", r.status_code == 200
          and row == {"pub_name": "Algodata Inc.", "city": "Oakland", "state": "CA", "country": "USA"})
    check("bad state or empty name/country -> 422", all(client.put("/api/publishers/1389", headers=auth(admin_key), json=b).status_code == 422 for b in [
        {"pub_name": "X", "state": "CAL", "country": "USA"}, {"pub_name": "", "country": "USA"}, {"pub_name": "X", "country": ""}]))
    check("edit unknown publisher -> 404", client.put("/api/publishers/0000", headers=auth(admin_key),
          json={"pub_name": "X", "country": "USA"}).status_code == 404)
    check("publisher with books can't be deleted (409)", client.delete("/api/publishers/1389", headers=auth(admin_key)).status_code == 409)
    unused = fetch_one("SELECT pub_id FROM public.publishers p WHERE NOT EXISTS (SELECT 1 FROM public.titles t WHERE t.pub_id = p.pub_id) "
                       "AND NOT EXISTS (SELECT 1 FROM public.employee e WHERE e.pub_id = p.pub_id) ORDER BY pub_id LIMIT 1")
    r = client.delete(f"/api/publishers/{unused['pub_id']}", headers=auth(admin_key))
    check("publisher with no books or employees is deleted", r.status_code == 200
          and fetch_one("SELECT 1 AS x FROM public.publishers WHERE pub_id = %s", (unused["pub_id"],)) is None)
    check("delete unknown publisher -> 404", client.delete("/api/publishers/0000", headers=auth(admin_key)).status_code == 404)

    # --- API key lifetime ---
    r = login("sita", "sita-pass-1", "author")
    hours = lambda r: (datetime.fromisoformat(r.json()["expires_at"]) - datetime.now(timezone.utc)).total_seconds() / 3600
    check("normal sign-in lasts 8 hours", abs(hours(r) - 8) < 0.1)
    check("'Remember me' lasts 30 days", abs(hours(login("sita", "sita-pass-1", "author", remember=True)) - 720) < 0.1)
    sita_key = r.json()["api_key"]
    with transaction() as cur:
        cur.execute("UPDATE public.api_key SET expires_at = now() - interval '1 second' WHERE key_hash = %s", (hash_api_key(sita_key),))
    check("expired key -> 401", client.get("/api/me", headers=auth(sita_key)).status_code == 401)

    # --- sign-in limit ---
    attacker, owner = from_ip("203.0.113.9"), from_ip("198.51.100.7")
    codes = [login("sita", "wrong-pass", "author", via=attacker).status_code for _ in range(5)]
    check("5 wrong passwords from one IP -> 401 each, then even the right one -> 429 there",
          codes == [401] * 5 and login("sita", "sita-pass-1", "author", via=attacker).status_code == 429)
    check("...but the real user signs in from their own IP (no lockout by others)",
          login("sita", "sita-pass-1", "author", via=owner).status_code == 200)
    check("another user can still sign in meanwhile", login("@NewMoon", "admin-pass-123", "admin").status_code == 200)
    spray = from_ip("192.0.2.50")
    codes = [login(f"ghost{i}", "x", "admin", via=spray).status_code for i in range(20)]
    check("20 failures from one IP (any usernames) -> that IP gets 429",
          codes == [401] * 20 and login("newmoon", "admin-pass-123", "admin", via=spray).status_code == 429)
    main_module._login_fails.clear()   # don't let the lockouts leak into later checks
    check("password over 128 characters -> 422", login("sita", "x" * 129, "author").status_code == 422)

    # --- usernames ignore case ---
    check("sign-in ignores username case", login("SITA", "sita-pass-1", "author").status_code == 200)
    sita_id = fetch_one("SELECT user_id FROM public.app_user WHERE username = 'sita'")["user_id"]
    r = client.patch(f"/api/accounts/{sita_id}", headers=auth(admin_key),
                     json={"display_name": "Sita Kumar", "username": "NewMoon", "is_active": True})
    check("renaming to a taken username (other case) -> 409", r.status_code == 409)
    r = client.patch(f"/api/accounts/{sita_id}", headers=auth(admin_key),
                     json={"display_name": "Sita Kumar", "username": "Sita.K", "is_active": True})
    check("rename is saved lower-cased", r.status_code == 200
          and fetch_one("SELECT username FROM public.app_user WHERE user_id = %s", (sita_id,))["username"] == "sita.k")
    check("the admin account can't be deactivated", client.patch("/api/accounts/1", headers=auth(admin_key),
          json={"display_name": "New Moon Books", "username": "newmoon", "is_active": False}).status_code == 400)

    # --- admin's own account ---
    other_key = login("newmoon", "admin-pass-123", "admin").json()["api_key"]
    check("admin profile is updated", client.patch("/api/me", headers=auth(admin_key),
          json={"display_name": "New Moon", "email": "  "}).status_code == 200
          and fetch_one("SELECT display_name, email FROM public.app_user WHERE user_id = 1") == {"display_name": "New Moon", "email": None})
    check("wrong current password -> 400", client.post("/api/me/password", headers=auth(admin_key),
          json={"current_password": "nope", "new_password": "admin-pass-456"}).status_code == 400)
    check("admin changes own password", client.post("/api/me/password", headers=auth(admin_key),
          json={"current_password": "admin-pass-123", "new_password": "admin-pass-456"}).status_code == 200)
    check("other admin sessions are signed out, this one stays", client.get("/api/me", headers=auth(other_key)).status_code == 401
          and client.get("/api/me", headers=auth(admin_key)).status_code == 200)
    check("new admin password works", login("newmoon", "admin-pass-456", "admin").status_code == 200)

    # --- hostile text and money ---
    nasty = "<img src=x onerror=alert(1)> 50% _off_ 'O\"Brien'; --"
    r = client.post("/api/books", headers=auth(admin_key), json={"title": nasty, "type": "business", "pub_id": "1389", "price": 19.99})
    nasty_id = r.json().get("title_id")
    b = client.get(f"/api/books/{nasty_id}", headers=auth(admin_key)).json()
    check("HTML/SQL characters round-trip unchanged", r.status_code == 201 and b["title"] == nasty and float(b["price"]) == 19.99)
    check("price with 3 decimals -> 422", client.post("/api/books", headers=auth(admin_key),
          json={"title": "X", "type": "business", "pub_id": "1389", "price": 1.234}).status_code == 422)
    client.delete(f"/api/books/{nasty_id}", headers=auth(admin_key))

    # --- publisher held only by an employee; store with two discounts ---
    pub = client.post("/api/publishers", headers=auth(admin_key), json={"pub_name": "Tiny Press", "country": "USA"}).json()["pub_id"]
    emp = client.post("/api/employees", headers=auth(admin_key), json={"fname": "Ann", "lname": "Lee", "job_id": 5,
                      "job_lvl": 150, "pub_id": pub, "hire_date": "2026-01-05"}).json()["emp_id"]
    r = client.delete(f"/api/publishers/{pub}", headers=auth(admin_key))
    check("publisher with only an employee can't be deleted (409)", r.status_code == 409 and "1 employee" in r.json()["detail"])
    client.delete(f"/api/employees/{emp}", headers=auth(admin_key))
    check("...and can be once the employee is gone", client.delete(f"/api/publishers/{pub}", headers=auth(admin_key)).status_code == 200)
    st = client.post("/api/stores", headers=auth(admin_key), json={"stor_name": "Two Deals"}).json()["stor_id"]
    for pct in ("5.00", "10.00"):
        client.post("/api/discounts", headers=auth(admin_key), json={"discounttype": "Deal", "stor_id": st, "discount": pct})
    shown = next(s for s in client.get("/api/stores", headers=auth(admin_key)).json() if s["stor_id"] == st)["discount"]
    check("store list shows its best discount", float(shown) == 10.0)

    # --- many requests at once ---
    def burst(n, call):
        codes, gate = [], threading.Barrier(n)

        def go():
            c = TestClient(app, raise_server_exceptions=False)
            gate.wait()
            codes.append(call(c))
        threads = [threading.Thread(target=go) for _ in range(n)]
        for t in threads: t.start()
        for t in threads: t.join()
        return codes

    sale = {"stor_id": "7066", "ord_num": "RACE-1", "title_id": "PS2091", "ord_date": "2026-10-27", "qty": 1, "payterms": "Net 30"}
    codes = burst(8, lambda c: c.post("/api/sales", headers=auth(admin_key), json=sale).status_code)
    check("8 identical sales at once: one 201, the rest 409, no 500", sorted(codes) == [201] + [409] * 7)
    codes = burst(8, lambda c: c.post("/api/stores", headers=auth(admin_key), json={"stor_name": "Rush"}).status_code)
    check("8 auto-ID stores at once: only 201/409, no 500", set(codes) <= {201, 409} and 201 in codes)
    codes = burst(30, lambda c: c.get("/api/books", headers=auth(admin_key)).status_code)
    check("30 requests at once all succeed (they wait for a connection)", codes == [200] * 30)

    # --- headers ---
    page, api_r = client.get("/"), client.get("/api/books", headers=auth(admin_key))
    check("page has a CSP and can't be framed", "script-src 'self'" in page.headers.get("content-security-policy", "")
          and page.headers.get("x-frame-options") == "DENY")
    check("API answers aren't cached", api_r.headers.get("cache-control") == "no-store"
          and api_r.headers.get("x-content-type-options") == "nosniff")

    # --- migrations on existing data ---
    with transaction() as cur:
        cur.execute("DROP INDEX public.app_user_username_lower_idx")   # as before 002 ran
        cur.execute("INSERT INTO public.app_user (username, display_name, password_hash, role, au_id) "
                    "VALUES ('RamK', 'Ram Kumar', %s, 'author', %s)", (hash_password("ram-pass-123"), ram_id))
    manage.migrate()
    check("002 lower-cases existing usernames", fetch_one(
          "SELECT username FROM public.app_user WHERE au_id = %s", (ram_id,))["username"] == "ramk")
    check("...and they sign in in any case", login("RAMK", "ram-pass-123", "author").status_code == 200)
    with transaction() as cur:
        cur.execute("DROP INDEX public.app_user_username_lower_idx")
        cur.execute("INSERT INTO public.app_user (username, display_name, password_hash, role) VALUES "
                    "('Twin', 'Twin 1', 'x', 'admin'), ('twin', 'Twin 2', 'x', 'admin')")
    try:
        manage.migrate()
        collided = False
    except Exception:
        collided = True
    check("002 refuses usernames that differ only by case, changing nothing", collided and sorted(
          r["username"] for r in fetch_all("SELECT username FROM public.app_user WHERE lower(username) = 'twin'")) == ["Twin", "twin"])
    with transaction() as cur:
        cur.execute("DELETE FROM public.app_user WHERE lower(username) = 'twin'")
    manage.migrate()

    # --- last sign-in survives prune-keys ---
    ram_user = fetch_one("SELECT user_id FROM public.app_user WHERE au_id = %s", (ram_id,))["user_id"]
    seen = client.get(f"/api/accounts/{ram_user}", headers=auth(admin_key)).json()["last_sign_in"]
    with transaction() as cur:
        cur.execute("UPDATE public.api_key SET expires_at = now() - interval '40 days' WHERE user_id = %s", (ram_user,))
    manage.prune_keys(30)
    check("last sign-in is still shown after old keys are pruned", seen is not None
          and fetch_one("SELECT COUNT(*) AS n FROM public.api_key WHERE user_id = %s", (ram_user,))["n"] == 0
          and client.get(f"/api/accounts/{ram_user}", headers=auth(admin_key)).json()["last_sign_in"] == seen)

    # --- races on links, busy server ---
    real_check = main_module._check_book_refs
    main_module._check_book_refs = lambda cur, body: None   # as if the publisher was deleted right after the check
    r = from_ip("127.0.0.1", raise_errors=False).post("/api/books", headers=auth(admin_key),
                                                      json={"title": "Orphan", "type": "business", "pub_id": "0000"})
    main_module._check_book_refs = real_check
    check("a reference deleted meanwhile -> 409, not 500", r.status_code == 409)
    real_free, real_wait = db._free, db.WAIT_SECONDS
    db._free, db.WAIT_SECONDS = threading.BoundedSemaphore(1), 0.1
    db._free.acquire()   # every connection is busy
    r = client.get("/api/books", headers=auth(admin_key))
    db._free, db.WAIT_SECONDS = real_free, real_wait
    check("no free connection within the wait -> 503", r.status_code == 503)

    # --- config ---
    check("remote DB defaults to sslmode=require, local to prefer",
          config._default_sslmode("db.example.com") == "require" and config._default_sslmode("localhost") == "prefer")
    env_file = HERE / "_test.env"
    env_file.write_text('export BOOKNEST_TEST_X="1"\n# comment\nBOOKNEST_TEST_Y = two\n')
    try:
        config._load_dotenv(env_file)
    finally:
        env_file.unlink()
    check(".env accepts 'export KEY=value', quotes and spaces",
          os.environ.get("BOOKNEST_TEST_X") == "1" and os.environ.get("BOOKNEST_TEST_Y") == "two")

    # --- delete account, logout ---
    check("admin deletes an author account", client.delete(f"/api/accounts/{johnson_id}", headers=auth(admin_key)).status_code == 200)
    check("admin account can't be deleted", client.delete("/api/accounts/1", headers=auth(admin_key)).status_code == 400)
    check("logout works", client.post("/api/logout", headers=auth(admin_key)).status_code == 200)
    check("key is dead after logout", client.get("/api/summary", headers=auth(admin_key)).status_code == 401)
    manage.prune_keys(0)
    check("prune-keys removes revoked and expired keys", fetch_one(
          "SELECT COUNT(*) AS n FROM public.api_key WHERE key_hash = %s OR expires_at < now()", (hash_api_key(admin_key),))["n"] == 0)

    # --- frontend is served ---
    check("frontend index is served", "BookNest" in client.get("/").text)
    check("frontend script is served", client.get("/static/app.js").status_code == 200)

    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
