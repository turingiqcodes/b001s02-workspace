// BookNest frontend: hash-routed pages that call the BookNest API with the API key from login.
// Admin pages live under #/admin/..., author pages under #/author/... .
"use strict";

const app = document.getElementById("app");

// ---------- session (API key from /api/login) ----------

const SESSION_KEY = "booknest-session";

function loadSession() {
  for (const store of [sessionStorage, localStorage]) {
    try {
      const raw = store.getItem(SESSION_KEY);
      if (raw) return JSON.parse(raw);
    } catch (_) { /* storage blocked */ }
  }
  return null;
}

function saveSession(session, remember) {
  clearSession();
  try { (remember ? localStorage : sessionStorage).setItem(SESSION_KEY, JSON.stringify(session)); } catch (_) {}
}

function isRemembered() {
  try { return !!localStorage.getItem(SESSION_KEY); } catch (_) { return false; }
}

function clearSession() {
  for (const store of [sessionStorage, localStorage]) {
    try { store.removeItem(SESSION_KEY); } catch (_) {}
  }
}

let session = loadSession();

// ---------- API ----------

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (session) headers.Authorization = `Bearer ${session.api_key}`;
  const res = await fetch(`/api${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let message = data.detail;
    if (Array.isArray(message)) message = message.map((d) => `${d.loc?.at(-1) ?? "field"}: ${d.msg}`).join("; ");
    if (res.status === 401 && session) {
      // Key expired or revoked: back to the right login.
      const role = session.user.role;
      clearSession(); session = null;
      location.hash = `#/login/${role}`;
    }
    throw new ApiError(res.status, message || `Request failed (${res.status}).`);
  }
  return data;
}

// ---------- helpers ----------

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { style: "currency", currency: "USD" }));
const num = (v) => Number(v ?? 0).toLocaleString("en-US");
const date = (v) => (v ? new Date(v).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : "—");
const initial = (name) => esc((name || "?").trim()[0]?.toUpperCase() || "?");
const greeting = () => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; };

function toast(message) {
  const t = document.getElementById("toast");
  t.textContent = message;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 2600);
}

function table(columns, rows, empty = "Nothing here yet.") {
  if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
  const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((r) => `<tr>${columns.map((c) => {
    const cls = [c.num && "num", c.strong && "strong", c.muted && "muted"].filter(Boolean).join(" ");
    return `<td class="${cls}">${c.html ? c.html(r) : esc(c.value ? c.value(r) : r[c.key])}</td>`;
  }).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function tiles(items) {
  return `<section class="tiles">${items.map(([label, value, sub]) => `
    <div class="tile"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="sub">${esc(sub)}</div></div>`).join("")}
  </section>`;
}

// ---------- layout ----------

const ADMIN_NAV = [
  ["#/admin", "Home"], ["#/admin/books", "Books"], ["#/admin/authors", "Authors"], ["#/admin/sales", "Sales"],
  ["#/admin/stores", "Stores"], ["#/admin/discounts", "Discounts"], ["#/admin/employees", "Employees"],
  ["#/admin/publishers", "Publishers"], ["#/admin/account", "My Account"],
];
const AUTHOR_NAV = [["#/author/books", "My Books"], ["#/author/account", "My Account"]];

function shell({ active, title, sub = "", back = null, body }) {
  const role = session.user.role;
  const nav = role === "admin" ? ADMIN_NAV : AUTHOR_NAV;
  app.innerHTML = `
    <div class="shell">
      <nav class="sidebar" aria-label="Main">
        <div class="brand">BookNest</div>
        <div class="section">${role === "admin" ? "ADMIN · PUBLISHER" : "AUTHOR"}</div>
        ${nav.map(([href, label]) => `<a href="${href}" ${href === active ? 'aria-current="page"' : ""}>${label}</a>`).join("")}
        <div class="spacer"></div>
        <div class="logout"><button type="button" id="logout">Log out</button></div>
      </nav>
      <main>
        <header class="topbar">
          <div>
            ${back ? `<a class="back" href="${back[0]}">${esc(back[1])}</a>` : ""}
            <h1>${esc(title)}</h1>
            ${sub ? `<p>${esc(sub)}</p>` : ""}
          </div>
          <div class="avatar ${role}" title="${esc(session.user.display_name)}">${initial(session.user.display_name)}</div>
        </header>
        <div id="content" style="display:grid;gap:24px">${body ?? '<p class="loading">Loading…</p>'}</div>
      </main>
    </div>`;
  document.getElementById("logout").addEventListener("click", logout);
  return document.getElementById("content");
}

async function logout() {
  const role = session?.user.role || "author";
  try { await api("/logout", { method: "POST" }); } catch (_) { /* key may already be gone */ }
  clearSession(); session = null;
  location.hash = `#/login/${role}`;
}

// ---------- login ----------

function loginPage(role) {
  const isAdmin = role === "admin";
  app.innerHTML = `
    <div class="login-page">
      <div class="login-card">
        <div class="login-art">
          <div class="logo">BookNest</div>
          <blockquote>“A reader lives a thousand lives before he dies.”</blockquote>
          <cite>— George R.R. Martin</cite>
        </div>
        <form class="login-form" id="login" novalidate>
          <span class="role-badge ${role}">${isAdmin ? "ADMIN SIGN IN" : "AUTHOR SIGN IN"}</span>
          <h1>Welcome back</h1>
          <p class="center" style="text-align:left;margin:0">${isAdmin ? "Sign in to manage BookNest" : "Sign in to see your books"}</p>
          <label class="field"><span>Username</span>
            <input id="username" name="username" autocomplete="username" required placeholder="${isAdmin ? "Admin username" : "Username from your admin"}"></label>
          <label class="field"><span>Password</span>
            <input id="password" name="password" type="password" autocomplete="current-password" required></label>
          <div class="row">
            <label class="check"><input type="checkbox" id="remember"> Remember me</label>
            <span style="color:var(--muted);font-size:13px">${isAdmin ? "" : "Forgot it? Ask your admin"}</span>
          </div>
          <p class="error" id="login-error"></p>
          <button class="btn primary wide" type="submit">Sign in</button>
          <p class="center">${isAdmin ? "Admin accounts manage books, authors and all data." : "Use the login details your admin gave you."}</p>
          <p class="center"><a class="link" href="#/login/${isAdmin ? "author" : "admin"}">${isAdmin ? "Author? Use the author login →" : "Admin? Use the admin login →"}</a></p>
        </form>
      </div>
    </div>`;
  const form = document.getElementById("login");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("login-error");
    err.textContent = "";
    const username = form.username.value.trim(), password = form.password.value;
    if (!username || !password) { err.textContent = "Enter your username and password."; return; }
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const remember = document.getElementById("remember").checked;
      const res = await api("/login", { method: "POST", body: { username, password, role, remember } });
      session = { api_key: res.api_key, user: res.user };
      saveSession(session, remember);
      location.hash = isAdmin ? "#/admin" : "#/author/books";
    } catch (ex) {
      err.textContent = ex.message;
      button.disabled = false;
    }
  });
  document.getElementById("username").focus();
}

// ---------- admin pages ----------

async function adminHome() {
  const c = shell({ active: "#/admin", title: `${greeting()}, ${session.user.display_name}`, sub: "Admin overview · visible to the admin only" });
  const { totals: t, by_publisher, top_books } = await api("/summary");
  const top = Math.max(1, ...by_publisher.map((p) => Number(p.sold)));
  c.innerHTML = `
    ${tiles([["Books", num(t.books), "Titles in the catalog"], ["Authors", num(t.authors), `${num(t.author_accounts)} with BookNest accounts`],
             ["Copies sold", num(t.sold), `${num(t.orders)} orders · ${num(t.stores)} stores`], ["Revenue", money(t.revenue), "Quantity × list price"]])}
    <section class="grid-2">
      <div class="card"><h2>Copies sold by publisher</h2>
        <div class="bars" style="margin-top:16px">${by_publisher.map((p) => `
          <div class="bar-row"><span>${esc(p.pub_name)}</span>
            <span class="bar-track"><span class="bar" style="width:${(Number(p.sold) / top) * 100}%" title="${esc(p.pub_name)}: ${num(p.sold)} copies"></span><span class="bar-value">${num(p.sold)}</span></span></div>`).join("") || '<p class="empty">No sales yet.</p>'}
        </div></div>
      <div class="card"><h2>Top books by copies sold</h2>
        ${table([{ label: "Book", key: "title", strong: true }, { label: "Publisher", key: "pub_name", muted: true }, { label: "Sold", key: "sold", num: true, strong: true }], top_books, "No sales yet.")}</div>
    </section>
    <section class="card"><h2>Admin tools</h2><p class="sub">Only the admin sees these tabs. Authors only see My Books and My Account.</p>
      <div class="tools">${[
        ["#/admin/books", "Books", `${num(t.books)} titles`], ["#/admin/authors", "Authors", "Create accounts"],
        ["#/admin/sales", "Sales", `${num(t.order_lines)} order lines`], ["#/admin/stores", "Stores", `${num(t.stores)} stores`],
        ["#/admin/discounts", "Discounts", `${num(t.discounts)} discount types`], ["#/admin/employees", "Employees", `${num(t.employees)} employees`],
        ["#/admin/publishers", "Publishers", `${num(t.publishers)} publishers`],
      ].map(([href, name, sub]) => `<a href="${href}"><strong>${name}</strong><span>${esc(sub)}</span><em>Open →</em></a>`).join("")}</div>
    </section>`;
}

async function adminBooks() {
  const c = shell({ active: "#/admin/books", title: "Books", sub: "The admin can see, add, edit and delete books" });
  const rows = await api("/books");
  c.innerHTML = `
    <div style="display:flex;justify-content:flex-end"><a class="btn primary" href="#/admin/books/new">+ Add book</a></div>
    <section class="card">${table([
      { label: "ID", key: "title_id", strong: true }, { label: "Title", key: "title" }, { label: "Type", key: "type", muted: true },
      { label: "Publisher", key: "pub_name", muted: true }, { label: "Price", value: (r) => money(r.price), num: true },
      { label: "Sold", key: "sold", num: true, strong: true },
      { label: "", html: (r) => `<a class="btn small" href="#/admin/books/${encodeURIComponent(r.title_id)}/edit">Edit</a>
        <button class="btn danger small" data-delete="${esc(r.title_id)}" data-title="${esc(r.title)}">Delete</button>`, num: true },
    ], rows, "No books.")}
    <p class="sub" style="margin-top:12px">Books that have sales can't be deleted, so the sales history stays complete.</p></section>`;
  c.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delete]");
    if (!b) return;
    if (b.dataset.confirm !== "1") {   // two clicks instead of a browser dialog
      b.dataset.confirm = "1"; b.textContent = "Confirm"; return;
    }
    b.disabled = true;
    try {
      await api(`/books/${encodeURIComponent(b.dataset.delete)}`, { method: "DELETE" });
      toast(`Deleted “${b.dataset.title}”.`);
      adminBooks();
    } catch (ex) { toast(ex.message); b.disabled = false; b.textContent = "Delete"; delete b.dataset.confirm; }
  });
}

// Add book (no titleId) and Edit book share this form.
async function bookForm(titleId) {
  const editing = Boolean(titleId);
  const c = shell({ active: "#/admin/books", title: editing ? "Edit book" : "Add book",
    sub: editing ? `${titleId} · only the admin can do this` : "Add a title to the catalog · only the admin can do this",
    back: ["#/admin/books", "← Books"] });
  const [{ types, publishers, authors }, book] = await Promise.all([
    api("/books/options"), editing ? api(`/books/${encodeURIComponent(titleId)}`) : null]);
  const b = book || { type: "business", pubdate: new Date().toISOString().slice(0, 10), authors: [] };
  const val = (v) => esc(v ?? "");
  const sel = (a, v) => (a === v ? "selected" : "");
  // A book with co-authors keeps them; only a single author can be changed here.
  const coAuthors = b.authors.length > 1;
  const current = b.authors[0]?.au_id ?? "";
  const typeList = types.includes(b.type) ? types : [...types, b.type];
  c.innerHTML = `
    <section class="card">
      <h2>Book details</h2><p class="sub">Saved to pubs.titles. Picking an author also links the book to them, so it shows on their My Books.</p>
      <form class="fields" id="book-form" novalidate>
        <label class="field" style="grid-column:1/-1"><span>Title</span><input name="title" required maxlength="80" autocomplete="off" value="${val(b.title)}"></label>
        <label class="field"><span>Type</span>
          <select name="type" required>${typeList.map((t) => `<option ${sel(t, b.type)}>${esc(t)}</option>`).join("")}</select></label>
        <label class="field"><span>Publisher</span>
          <select name="pub_id" required>${publishers.map((p) => `<option value="${esc(p.pub_id)}" ${sel(p.pub_id, b.pub_id)}>${esc(p.pub_name)}</option>`).join("")}</select></label>
        ${coAuthors ? `
        <label class="field"><span>Authors</span><input readonly value="${val(b.authors.map((a) => a.name).join(", "))}">
          <span class="hint">This book has several authors, so they can't be changed here.</span></label>` : `
        <label class="field"><span>Author (optional)</span>
          <select name="au_id"><option value="">— None —</option>${authors.map((a) => `<option value="${esc(a.au_id)}" ${sel(a.au_id, current)}>${esc(a.name)}</option>`).join("")}</select></label>`}
        ${editing ? `
        <label class="field"><span>Book ID</span><input readonly value="${val(b.title_id)}">
          <span class="hint">Sales and authors point at this ID, so it can't change.</span></label>` : `
        <label class="field"><span>Book ID (optional)</span><input name="title_id" maxlength="6" placeholder="e.g. BU1033" autocomplete="off">
          <span class="hint">Two letters and four digits. Leave empty to get the next free ID.</span></label>`}
        <label class="field"><span>Price ($)</span><input name="price" type="number" min="0" step="0.01" placeholder="19.99" value="${val(b.price)}"></label>
        <label class="field"><span>Advance ($, optional)</span><input name="advance" type="number" min="0" step="0.01" value="${val(b.advance)}"></label>
        <label class="field"><span>Royalty % (optional)</span><input name="royalty" type="number" min="0" max="100" step="1" value="${val(b.royalty)}"></label>
        <label class="field"><span>Publish date</span><input name="pubdate" type="date" value="${val(b.pubdate)}"></label>
        <label class="field" style="grid-column:1/-1"><span>Notes (optional)</span><input name="notes" maxlength="200" autocomplete="off" value="${val(b.notes)}"></label>
        <p class="error" id="form-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/admin/books">Cancel</a><button class="btn primary" type="submit">${editing ? "Save changes" : "Add book"}</button></div>
      </form>
    </section>`;
  const form = document.getElementById("book-form");
  const opt = (v) => (v.trim() === "" ? null : v.trim());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("form-error");
    err.textContent = "";
    if (!form.title.value.trim()) { err.textContent = "Enter a title."; return; }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const body = {
      title: form.title.value.trim(), type: form.type.value, pub_id: form.pub_id.value, price: opt(form.price.value),
      advance: opt(form.advance.value), royalty: opt(form.royalty.value), pubdate: opt(form.pubdate.value), notes: opt(form.notes.value) };
    if (!coAuthors) body.au_id = opt(form.au_id.value);
    try {
      if (editing) {
        await api(`/books/${encodeURIComponent(titleId)}`, { method: "PUT", body });
        toast(`Saved “${body.title}”.`);
      } else {
        body.title_id = opt(form.title_id.value);
        const res = await api("/books", { method: "POST", body });
        toast(`Added “${body.title}” as ${res.title_id}.`);
      }
      location.hash = "#/admin/books";
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
}

async function adminAuthors() {
  const c = shell({ active: "#/admin/authors", title: "Authors", sub: "Every author in pubs · only the admin can add, edit or delete authors and create logins" });
  const rows = await api("/authors");
  const withAccount = rows.filter((r) => r.user_id).length;
  c.innerHTML = `
    <div style="display:flex;justify-content:flex-end"><a class="btn primary" href="#/admin/authors/new">+ Add author</a></div>
    <section class="card">${table([
      { label: "Name", key: "name", strong: true },
      { label: "Location", value: (r) => [r.city, r.state].filter(Boolean).join(", ") || "—", muted: true },
      { label: "Phone", key: "phone", muted: true },
      { label: "Contract", value: (r) => (r.contract ? "Yes" : "No") },
      { label: "Books", key: "books", num: true },
      { label: "Account", html: (r) => (r.user_id
        ? `<a class="link" href="#/admin/authors/${r.user_id}">@${esc(r.username)}</a>${r.is_active ? "" : ' <span class="badge off">Off</span>'}`
        : "—") },
      { label: "", html: (r) => `<a class="btn small" href="#/admin/authors/${encodeURIComponent(r.au_id)}/edit">Edit</a>
        <button class="btn danger small" data-delete="${esc(r.au_id)}" data-name="${esc(r.name)}" data-account="${r.user_id ? 1 : ""}">Delete</button>`, num: true },
    ], rows, "No authors.")}
    <p class="sub" style="margin-top:12px">Authors of a book can't be deleted, so book credits stay complete. Deleting an author also deletes their login.</p></section>
    <div class="note"><strong>Admin only</strong>${rows.length} authors, ${withAccount} with a BookNest account. Edit changes the author's details in pubs.authors; click an account to change its login. Authors can't sign up; the admin creates each account.</div>`;
  c.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delete]");
    if (!b) return;
    if (b.dataset.confirm !== "1") {   // two clicks instead of a browser dialog
      b.dataset.confirm = "1"; b.textContent = b.dataset.account ? "Confirm (login too)" : "Confirm"; return;
    }
    b.disabled = true;
    try {
      const res = await api(`/authors/${encodeURIComponent(b.dataset.delete)}`, { method: "DELETE" });
      toast(`Deleted ${b.dataset.name}${res.account_deleted ? " and their login" : ""}.`);
      adminAuthors();
    } catch (ex) { toast(ex.message); b.disabled = false; b.textContent = "Delete"; delete b.dataset.confirm; }
  });
}

async function adminEditAuthor(auId) {
  const c = shell({ active: "#/admin/authors", title: "Edit author", sub: `${auId} · details in pubs.authors · only the admin can do this`,
    back: ["#/admin/authors", "← Authors"] });
  const a = await api(`/authors/${encodeURIComponent(auId)}`);
  const val = (v) => esc(v ?? "");
  c.innerHTML = `
    <section class="card">
      <h2>Author details</h2><p class="sub">Shown on their books and in the admin pages. Their login is changed on the account page.</p>
      <form class="fields" id="author-form" novalidate>
        <label class="field"><span>First name</span><input name="au_fname" required maxlength="20" autocomplete="off" value="${val(a.au_fname)}"></label>
        <label class="field"><span>Last name</span><input name="au_lname" required maxlength="40" autocomplete="off" value="${val(a.au_lname)}"></label>
        <label class="field"><span>Phone</span><input name="phone" required maxlength="12" autocomplete="off" value="${val(a.phone)}" placeholder="415 555-0100"></label>
        <label class="field"><span>Author ID</span><input readonly value="${val(a.au_id)}">
          <span class="hint">Books point at this ID, so it can't change.</span></label>
        <label class="field" style="grid-column:1/-1"><span>Address (optional)</span><input name="address" maxlength="40" autocomplete="off" value="${val(a.address)}"></label>
        <label class="field"><span>City (optional)</span><input name="city" maxlength="20" autocomplete="off" value="${val(a.city)}"></label>
        <label class="field"><span>State (optional)</span><input name="state" maxlength="2" autocomplete="off" value="${val(a.state)}" placeholder="CA"></label>
        <label class="field"><span>ZIP (optional)</span><input name="zip" maxlength="5" inputmode="numeric" autocomplete="off" value="${val(a.zip)}"></label>
        <label class="check"><input type="checkbox" name="contract" ${a.contract ? "checked" : ""}> Has a contract</label>
        <p class="error" id="form-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/admin/authors">Cancel</a><button class="btn primary" type="submit">Save changes</button></div>
      </form>
    </section>`;
  const form = document.getElementById("author-form");
  const opt = (v) => (v.trim() === "" ? null : v.trim());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("form-error");
    err.textContent = "";
    if (!form.au_fname.value.trim() || !form.au_lname.value.trim() || !form.phone.value.trim()) {
      err.textContent = "First name, last name and phone are required."; return;
    }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api(`/authors/${encodeURIComponent(auId)}`, { method: "PUT", body: {
        au_fname: form.au_fname.value.trim(), au_lname: form.au_lname.value.trim(), phone: form.phone.value.trim(),
        address: opt(form.address.value), city: opt(form.city.value), state: opt(form.state.value), zip: opt(form.zip.value),
        contract: form.contract.checked } });
      toast(`Saved ${form.au_fname.value.trim()} ${form.au_lname.value.trim()}.`);
      location.hash = "#/admin/authors";
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
}

function randomPassword() {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
  return Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => chars[b % chars.length]).join("");
}

// Two ways to add: a brand-new author in pubs.authors (optionally with a login), or a login
// for an author who is already in pubs.authors.
async function adminAddAuthor() {
  const c = shell({ active: "#/admin/authors", title: "Add author", sub: "Only the admin can add authors and create logins", back: ["#/admin/authors", "← Authors"] });
  const free = await api("/authors/without-account");
  const loginFields = (prefix) => `
        <label class="field"><span>Username</span><input name="${prefix}username" autocomplete="off" placeholder="e.g. hmcbadden"></label>
        <label class="field"><span>Password (8+ characters)</span><input name="${prefix}password" autocomplete="new-password">
          <button type="button" class="link" data-gen="${prefix}password" style="justify-self:start;font-size:12px">↻ Generate a password</button></label>
        <label class="field"><span>Email (optional)</span><input name="${prefix}email" type="email" autocomplete="off"></label>`;
  c.innerHTML = `
    <section class="card">
      <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:16px">
        <label class="check"><input type="radio" name="mode" value="new" checked> New author</label>
        <label class="check"><input type="radio" name="mode" value="existing"> Login for an existing author</label>
      </div>

      <form class="fields" id="new-author" novalidate>
        <p class="sub" style="grid-column:1/-1;margin:0">Adds the author to pubs.authors. Their ID is made for you unless you enter one.</p>
        <label class="field"><span>First name</span><input name="au_fname" maxlength="20" autocomplete="off"></label>
        <label class="field"><span>Last name</span><input name="au_lname" maxlength="40" autocomplete="off"></label>
        <label class="field"><span>Phone</span><input name="phone" maxlength="12" autocomplete="off" placeholder="415 555-0100"></label>
        <label class="field"><span>Author ID (optional)</span><input name="au_id" maxlength="11" autocomplete="off" placeholder="e.g. 123-45-6789">
          <span class="hint">Format 123-45-6789. Leave empty to get a free one.</span></label>
        <label class="field" style="grid-column:1/-1"><span>Address (optional)</span><input name="address" maxlength="40" autocomplete="off"></label>
        <label class="field"><span>City (optional)</span><input name="city" maxlength="20" autocomplete="off"></label>
        <label class="field"><span>State (optional)</span><input name="state" maxlength="2" autocomplete="off" placeholder="CA"></label>
        <label class="field"><span>ZIP (optional)</span><input name="zip" maxlength="5" inputmode="numeric" autocomplete="off"></label>
        <label class="check"><input type="checkbox" name="contract" checked> Has a contract</label>
        <label class="check" style="grid-column:1/-1"><input type="checkbox" name="with_login" checked> Also create a BookNest login for them</label>
        <div id="login-part" style="grid-column:1/-1;display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px 24px">
          ${loginFields("")}
        </div>
        <p class="error" id="new-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/admin/authors">Cancel</a><button class="btn primary" type="submit">Add author</button></div>
      </form>

      <div id="existing-part" hidden>
        ${free.length ? `
        <form class="fields" id="existing-author" novalidate>
          <p class="sub" style="grid-column:1/-1;margin:0">Creates a login for an author already in pubs.authors. They'll see that author's books.</p>
          <label class="field" style="grid-column:1/-1"><span>Author (from pubs.authors)</span>
            <select name="au_id">${free.map((a) => `<option value="${esc(a.au_id)}">${esc(a.name)} · ${esc(a.city || "")}${a.state ? ", " + esc(a.state) : ""}</option>`).join("")}</select></label>
          ${loginFields("")}
          <p class="error" id="existing-error" style="grid-column:1/-1"></p>
          <div class="actions"><a class="btn" href="#/admin/authors">Cancel</a><button class="btn primary" type="submit">Create login</button></div>
        </form>` : '<p class="empty">Every author in pubs.authors already has a login.</p>'}
      </div>
    </section>`;

  const newForm = document.getElementById("new-author");
  const existingForm = document.getElementById("existing-author");
  const existingPart = document.getElementById("existing-part");
  const loginPart = document.getElementById("login-part");
  c.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => {
    const existing = c.querySelector('input[name="mode"]:checked').value === "existing";
    // form.fields sets display:grid, which would override the hidden attribute
    newForm.style.display = existing ? "none" : ""; existingPart.hidden = !existing;
  }));
  newForm.with_login.addEventListener("change", () => { loginPart.style.display = newForm.with_login.checked ? "grid" : "none"; });
  c.addEventListener("click", (e) => {
    const g = e.target.closest("[data-gen]");
    if (g) g.closest("form")[g.dataset.gen].value = randomPassword();
  });
  const opt = (v) => (v.trim() === "" ? null : v.trim());
  const login = (f) => ({ username: f.username.value.trim(), password: f.password.value, email: opt(f.email.value) });

  newForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = newForm, err = document.getElementById("new-error");
    err.textContent = "";
    if (!f.au_fname.value.trim() || !f.au_lname.value.trim() || !f.phone.value.trim()) {
      err.textContent = "First name, last name and phone are required."; return;
    }
    if (f.with_login.checked && (f.username.value.trim().length < 3 || f.password.value.length < 8)) {
      err.textContent = "For the login, enter a username (3+ characters) and a password (8+ characters)."; return;
    }
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const res = await api("/authors", { method: "POST", body: {
        au_fname: f.au_fname.value.trim(), au_lname: f.au_lname.value.trim(), phone: f.phone.value.trim(), au_id: opt(f.au_id.value),
        address: opt(f.address.value), city: opt(f.city.value), state: opt(f.state.value), zip: opt(f.zip.value),
        contract: f.contract.checked, login: f.with_login.checked ? login(f) : null } });
      const name = `${f.au_fname.value.trim()} ${f.au_lname.value.trim()}`;
      if (res.user_id) {
        toast(`Added ${name} (${res.au_id}) with a login. Share the username and password with them.`);
        location.hash = `#/admin/authors/${res.user_id}`;
      } else {
        toast(`Added ${name} as ${res.au_id}.`);
        location.hash = "#/admin/authors";
      }
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });

  existingForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = existingForm, err = document.getElementById("existing-error");
    err.textContent = "";
    try {
      const res = await api("/accounts", { method: "POST", body: { au_id: f.au_id.value, ...login(f) } });
      toast("Login created. Share the username and password with the author.");
      location.hash = `#/admin/authors/${res.user_id}`;
    } catch (ex) { err.textContent = ex.message; }
  });
}

async function adminAuthorDetail(id) {
  const c = shell({ active: "#/admin/authors", title: "Author", back: ["#/admin/authors", "← Authors"] });
  const a = await api(`/accounts/${id}`);
  document.querySelector(".topbar h1").textContent = a.display_name;
  const sold = a.books.reduce((s, b) => s + Number(b.sold), 0);
  c.innerHTML = `
    <div class="note"><strong>Admin view</strong>You're viewing an author's account. Changes are saved to this account.</div>
    <section class="grid-main">
      <div class="card">
        <h2>Profile and login details</h2><p class="sub">Only the admin can change these</p>
        <form class="fields" id="profile" novalidate>
          <label class="field"><span>Display name</span><input name="display_name" id="display_name" value="${esc(a.display_name)}" required></label>
          <label class="field"><span>Email</span><input name="email" id="email" type="email" value="${esc(a.email || "")}"></label>
          <label class="field"><span>Username</span><input name="username" id="username" value="${esc(a.username)}" required></label>
          <label class="field"><span>Location (pubs.authors)</span><input readonly value="${esc([a.author?.city, a.author?.state].filter(Boolean).join(", ") || "—")}"></label>
          <label class="check"><input type="checkbox" id="is_active" ${a.is_active ? "checked" : ""}> Account active (can sign in)</label>
          <p class="error" id="profile-error" style="grid-column:1/-1"></p>
          <div class="actions"><button class="btn primary" type="submit">Save changes</button></div>
        </form>
        <hr style="border:0;border-top:1px solid var(--card-line);margin:20px 0">
        <form class="fields" id="password-form" novalidate>
          <label class="field"><span>New password (8+ characters)</span><input name="password" id="set-password" autocomplete="new-password">
            <span class="hint">Give it to the author yourself. Their current sessions end.</span></label>
          <p class="error" id="password-error" style="grid-column:1/-1"></p>
          <div class="actions"><button class="btn" type="submit">Set new password</button></div>
        </form>
      </div>
      <div class="card">
        <h2>Books published</h2><p class="sub">${a.books.length} book${a.books.length === 1 ? "" : "s"} · ${num(sold)} copies sold</p>
        ${table([{ label: "Book", key: "title", strong: true }, { label: "Sold", key: "sold", num: true, strong: true }], a.books, "No books yet.")}
        <p class="sub" style="margin-top:16px">Last sign-in: ${a.last_sign_in ? date(a.last_sign_in) : "never"}</p>
      </div>
    </section>
    <section class="card" style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap">
      <div><h2>Delete this author account</h2><p class="sub" style="margin:0">Removes the login. Their books stay in the catalog.</p></div>
      <button class="btn danger" id="delete-account" type="button">Delete account</button>
    </section>`;

  document.getElementById("profile").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = document.getElementById("profile-error");
    err.textContent = "";
    try {
      await api(`/accounts/${id}`, { method: "PATCH", body: {
        display_name: f.display_name.value.trim(), email: f.email.value.trim() || null,
        username: f.username.value.trim(), is_active: document.getElementById("is_active").checked } });
      toast("Saved.");
      adminAuthorDetail(id);
    } catch (ex) { err.textContent = ex.message; }
  });
  document.getElementById("password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("password-error");
    err.textContent = "";
    try {
      await api(`/accounts/${id}/password`, { method: "POST", body: { password: e.target.password.value } });
      e.target.reset();
      toast("New password set. Give it to the author.");
    } catch (ex) { err.textContent = ex.message; }
  });
  const del = document.getElementById("delete-account");
  del.addEventListener("click", async () => {
    if (del.dataset.confirm !== "1") { del.dataset.confirm = "1"; del.textContent = "Click again to delete"; return; }
    try {
      await api(`/accounts/${id}`, { method: "DELETE" });
      toast("Account deleted.");
      location.hash = "#/admin/authors";
    } catch (ex) { toast(ex.message); }
  });
}

const saleKey = (r) => [r.stor_id, r.title_id, r.ord_num].map(encodeURIComponent).join("/");

async function adminSales() {
  const c = shell({ active: "#/admin/sales", title: "Sales", sub: "Every order line from pubs.sales · only the admin can add, edit or delete them" });
  const rows = await api("/sales");
  const orders = new Set(rows.map((r) => `${r.stor_id}|${r.ord_num}`)).size;
  c.innerHTML = `
    <div style="display:flex;justify-content:flex-end"><a class="btn primary" href="#/admin/sales/new">+ Add sale</a></div>
    ${tiles([["Orders", num(orders), `${num(rows.length)} order lines`], ["Copies sold", num(rows.reduce((s, r) => s + r.qty, 0)), "All titles"],
             ["Revenue", money(rows.reduce((s, r) => s + Number(r.amount || 0), 0)), "Quantity × list price"]])}
    <section class="card">${table([
      { label: "Order", key: "ord_num", strong: true }, { label: "Date", value: (r) => date(r.ord_date), muted: true },
      { label: "Store", key: "stor_name" }, { label: "Book", key: "title" }, { label: "Qty", key: "qty", num: true, strong: true },
      { label: "Terms", key: "payterms", muted: true }, { label: "Amount", value: (r) => money(r.amount), num: true, strong: true },
      { label: "", html: (r) => `<a class="btn small" href="#/admin/sales/${saleKey(r)}/edit">Edit</a>
        <button class="btn danger small" data-delete="${esc(saleKey(r))}" data-name="${esc(r.ord_num)}">Delete</button>`, num: true },
    ], rows, "No sales yet.")}
    <p class="sub" style="margin-top:12px">Each row is one order line (store, order number and book). Deleting it changes the sales figures.</p></section>`;
  c.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delete]");
    if (!b) return;
    if (b.dataset.confirm !== "1") {   // two clicks instead of a browser dialog
      b.dataset.confirm = "1"; b.textContent = "Confirm"; return;
    }
    b.disabled = true;
    try {
      await api(`/sales/${b.dataset.delete}`, { method: "DELETE" });
      toast(`Deleted the order line from ${b.dataset.name}.`);
      adminSales();
    } catch (ex) { toast(ex.message); b.disabled = false; b.textContent = "Delete"; delete b.dataset.confirm; }
  });
}

// Add sale (no key) and Edit sale share this form.
async function saleForm(key) {
  const editing = Boolean(key);
  const c = shell({ active: "#/admin/sales", title: editing ? "Edit sale" : "Add sale",
    sub: editing ? "One order line in pubs.sales · only the admin can do this" : "Add an order line to pubs.sales · only the admin can do this",
    back: ["#/admin/sales", "← Sales"] });
  const [s, opts] = await Promise.all([editing ? api(`/sales/${key}`) : null, editing ? null : api("/sales/options")]);
  const val = (v) => esc(v ?? "");
  const today = new Date().toISOString().slice(0, 10);
  const terms = editing ? s.payterms_used || [] : opts.payterms;
  c.innerHTML = `
    <section class="card">
      <h2>Order line</h2><p class="sub">${editing
        ? "Store, order number and book identify this line, so they can't change. To move it, delete it and add a new one."
        : "One book in one order. To add more books to the same order, add a line for each with the same order number."}</p>
      <form class="fields" id="sale-form" novalidate>
        ${editing ? `
        <label class="field"><span>Order number</span><input readonly value="${val(s.ord_num)}"></label>
        <label class="field"><span>Store</span><input readonly value="${val(s.stor_name)} (${val(s.stor_id)})"></label>
        <label class="field" style="grid-column:1/-1"><span>Book</span><input readonly value="${val(s.title)} (${val(s.title_id)})"></label>` : `
        <label class="field"><span>Order number</span><input name="ord_num" required maxlength="20" autocomplete="off" placeholder="e.g. A2977"></label>
        <label class="field"><span>Store</span>
          <select name="stor_id" required>${opts.stores.map((st) => `<option value="${esc(st.stor_id)}">${esc(st.stor_name)} (${esc(st.stor_id)})</option>`).join("")}</select></label>
        <label class="field" style="grid-column:1/-1"><span>Book</span>
          <select name="title_id" required>${opts.titles.map((t) => `<option value="${esc(t.title_id)}">${esc(t.title)} (${esc(t.title_id)})</option>`).join("")}</select></label>`}
        <label class="field"><span>Order date</span><input name="ord_date" type="date" required value="${val(editing ? s.ord_date : today)}"></label>
        <label class="field"><span>Quantity</span><input name="qty" type="number" required min="1" max="32767" step="1" value="${val(editing ? s.qty : "")}"></label>
        <label class="field"><span>Pay terms</span><input name="payterms" required maxlength="12" list="payterms" autocomplete="off" value="${val(editing ? s.payterms : terms[0])}">
          <datalist id="payterms">${terms.map((p) => `<option value="${esc(p)}">`).join("")}</datalist></label>
        <p class="error" id="form-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/admin/sales">Cancel</a><button class="btn primary" type="submit">${editing ? "Save changes" : "Add sale"}</button></div>
      </form>
    </section>`;
  const form = document.getElementById("sale-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("form-error");
    err.textContent = "";
    const qty = Number(form.qty.value);
    if ((!editing && !form.ord_num.value.trim()) || !form.ord_date.value || !form.payterms.value.trim() || !Number.isInteger(qty) || qty < 1) {
      err.textContent = "Enter an order number, order date, pay terms and a quantity of at least 1."; return;
    }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const body = { ord_date: form.ord_date.value, qty, payterms: form.payterms.value.trim() };
    try {
      if (editing) {
        await api(`/sales/${key}`, { method: "PUT", body });
        toast(`Saved order ${s.ord_num}.`);
      } else {
        const res = await api("/sales", { method: "POST", body: {
          ...body, stor_id: form.stor_id.value, title_id: form.title_id.value, ord_num: form.ord_num.value.trim() } });
        toast(`Added order ${res.ord_num}.`);
      }
      location.hash = "#/admin/sales";
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
}

async function adminStores() {
  const c = shell({ active: "#/admin/stores", title: "Stores", sub: "From pubs.stores, with sales per store · only the admin can add, edit or delete them" });
  const rows = await api("/stores");
  c.innerHTML = `
    <div style="display:flex;justify-content:flex-end"><a class="btn primary" href="#/admin/stores/new">+ Add store</a></div>
    <section class="card">${table([
      { label: "ID", key: "stor_id", muted: true }, { label: "Store", key: "stor_name", strong: true },
      { label: "Address", key: "stor_address", muted: true }, { label: "City", value: (r) => [r.city, [r.state, r.zip].filter(Boolean).join(" ")].filter(Boolean).join(", ") },
      { label: "Orders", key: "orders", num: true }, { label: "Sold", key: "sold", num: true, strong: true },
      { label: "Revenue", value: (r) => money(r.revenue), num: true }, { label: "Discount", value: (r) => (r.discount == null ? "—" : `${Number(r.discount).toFixed(1)}%`), num: true },
      { label: "", html: (r) => `<a class="btn small" href="#/admin/stores/${encodeURIComponent(r.stor_id)}/edit">Edit</a>
        <button class="btn danger small" data-delete="${esc(r.stor_id)}" data-name="${esc(r.stor_name)}">Delete</button>`, num: true },
    ], rows, "No stores.")}
    <p class="sub" style="margin-top:12px">Stores that have sales can't be deleted, so the sales history stays complete.</p></section>`;
  c.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delete]");
    if (!b) return;
    if (b.dataset.confirm !== "1") {   // two clicks instead of a browser dialog
      b.dataset.confirm = "1"; b.textContent = "Confirm"; return;
    }
    b.disabled = true;
    try {
      await api(`/stores/${encodeURIComponent(b.dataset.delete)}`, { method: "DELETE" });
      toast(`Deleted ${b.dataset.name}.`);
      adminStores();
    } catch (ex) { toast(ex.message); b.disabled = false; b.textContent = "Delete"; delete b.dataset.confirm; }
  });
}

// Add store (no storId) and Edit store share this form.
async function storeForm(storId) {
  const editing = Boolean(storId);
  const c = shell({ active: "#/admin/stores", title: editing ? "Edit store" : "Add store",
    sub: editing ? `${storId} · details in pubs.stores · only the admin can do this` : "Add a store to pubs.stores · only the admin can do this",
    back: ["#/admin/stores", "← Stores"] });
  const st = editing ? await api(`/stores/${encodeURIComponent(storId)}`) : {};
  const val = (v) => esc(v ?? "");
  c.innerHTML = `
    <section class="card">
      <h2>Store details</h2><p class="sub">Shown on sales and the Stores page.</p>
      <form class="fields" id="store-form" novalidate>
        <label class="field"><span>Name</span><input name="stor_name" required maxlength="40" autocomplete="off" value="${val(st.stor_name)}"></label>
        ${editing ? `
        <label class="field"><span>Store ID</span><input readonly value="${val(st.stor_id)}">
          <span class="hint">Sales and discounts point at this ID, so it can't change.</span></label>` : `
        <label class="field"><span>Store ID (optional)</span><input name="stor_id" maxlength="4" inputmode="numeric" placeholder="e.g. 9104" autocomplete="off">
          <span class="hint">Four digits. Leave empty to get the next free ID.</span></label>`}
        <label class="field" style="grid-column:1/-1"><span>Address (optional)</span><input name="stor_address" maxlength="40" autocomplete="off" value="${val(st.stor_address)}"></label>
        <label class="field"><span>City (optional)</span><input name="city" maxlength="20" autocomplete="off" value="${val(st.city)}"></label>
        <label class="field"><span>State (optional)</span><input name="state" maxlength="2" autocomplete="off" value="${val(st.state)}" placeholder="WA"></label>
        <label class="field"><span>ZIP (optional)</span><input name="zip" maxlength="5" inputmode="numeric" autocomplete="off" value="${val(st.zip)}"></label>
        <p class="error" id="form-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/admin/stores">Cancel</a><button class="btn primary" type="submit">${editing ? "Save changes" : "Add store"}</button></div>
      </form>
    </section>`;
  const form = document.getElementById("store-form");
  const opt = (v) => (v.trim() === "" ? null : v.trim());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("form-error");
    err.textContent = "";
    if (!form.stor_name.value.trim()) { err.textContent = "Enter a store name."; return; }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const body = { stor_name: form.stor_name.value.trim(), stor_address: opt(form.stor_address.value), city: opt(form.city.value),
      state: opt(form.state.value), zip: opt(form.zip.value) };
    try {
      if (editing) {
        await api(`/stores/${encodeURIComponent(storId)}`, { method: "PUT", body });
        toast(`Saved ${body.stor_name}.`);
      } else {
        body.stor_id = opt(form.stor_id.value);
        const res = await api("/stores", { method: "POST", body });
        toast(`Added ${body.stor_name} as ${res.stor_id}.`);
      }
      location.hash = "#/admin/stores";
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
}

// pubs.discounts has no ID, so a discount is named by its values (see the API). The edit page
// carries them in its address as base64url JSON.
const discountKey = (r) => ({ discounttype: r.discounttype, stor_id: r.stor_id ?? null, lowqty: r.lowqty ?? null,
  highqty: r.highqty ?? null, discount: Number(r.discount).toFixed(2) });
const encodeRef = (obj) => btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(obj))))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const decodeRef = (ref) => JSON.parse(new TextDecoder().decode(
  Uint8Array.from(atob(ref.replace(/-/g, "+").replace(/_/g, "/")), (ch) => ch.charCodeAt(0))));

async function adminDiscounts() {
  const c = shell({ active: "#/admin/discounts", title: "Discounts", sub: "From pubs.discounts · only the admin can add, edit or delete them" });
  const rows = await api("/discounts");
  c.innerHTML = `
    <div style="display:flex;justify-content:flex-end"><a class="btn primary" href="#/admin/discounts/new">+ Add discount</a></div>
    <section class="card">${table([
      { label: "Discount type", key: "discounttype", strong: true }, { label: "Store", value: (r) => r.stor_name || "All stores", muted: true },
      { label: "Low qty", value: (r) => r.lowqty ?? "—", num: true }, { label: "High qty", value: (r) => r.highqty ?? "—", num: true },
      { label: "Discount", value: (r) => `${Number(r.discount).toFixed(2)}%`, num: true, strong: true },
      { label: "", html: (r) => `<a class="btn small" href="#/admin/discounts/edit/${encodeRef(discountKey(r))}">Edit</a>
        <button class="btn danger small" data-delete="${encodeRef(discountKey(r))}" data-name="${esc(r.discounttype)}">Delete</button>`, num: true },
    ], rows, "No discounts.")}</section>`;
  c.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delete]");
    if (!b) return;
    if (b.dataset.confirm !== "1") {   // two clicks instead of a browser dialog
      b.dataset.confirm = "1"; b.textContent = "Confirm"; return;
    }
    b.disabled = true;
    try {
      await api("/discounts", { method: "DELETE", body: decodeRef(b.dataset.delete) });
      toast(`Deleted the ${b.dataset.name}.`);
      adminDiscounts();
    } catch (ex) { toast(ex.message); b.disabled = false; b.textContent = "Delete"; delete b.dataset.confirm; }
  });
}

// Add discount (no ref) and Edit discount share this form.
async function discountForm(ref) {
  const editing = Boolean(ref);
  const c = shell({ active: "#/admin/discounts", title: editing ? "Edit discount" : "Add discount",
    sub: editing ? "Details in pubs.discounts · only the admin can do this" : "Add a discount to pubs.discounts · only the admin can do this",
    back: ["#/admin/discounts", "← Discounts"] });
  let original = null;
  if (editing) {
    try { original = decodeRef(ref); } catch { location.hash = "#/admin/discounts"; return; }
  }
  const stores = await api("/stores");
  const d = original || {};
  const val = (v) => esc(v ?? "");
  c.innerHTML = `
    <section class="card">
      <h2>Discount details</h2><p class="sub">Leave the store empty for a discount that applies to every store.</p>
      <form class="fields" id="discount-form" novalidate>
        <label class="field"><span>Discount type</span><input name="discounttype" required maxlength="40" autocomplete="off" value="${val(d.discounttype)}"></label>
        <label class="field"><span>Store</span>
          <select name="stor_id"><option value="">All stores</option>${stores.map((st) =>
            `<option value="${esc(st.stor_id)}" ${st.stor_id === d.stor_id ? "selected" : ""}>${esc(st.stor_name)} (${esc(st.stor_id)})</option>`).join("")}</select></label>
        <label class="field"><span>Low qty (optional)</span><input name="lowqty" type="number" min="0" step="1" value="${val(d.lowqty)}"></label>
        <label class="field"><span>High qty (optional)</span><input name="highqty" type="number" min="0" step="1" value="${val(d.highqty)}"></label>
        <label class="field"><span>Discount (%)</span><input name="discount" type="number" required min="0" max="99.99" step="0.01" value="${val(d.discount)}"></label>
        <p class="error" id="form-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/admin/discounts">Cancel</a><button class="btn primary" type="submit">${editing ? "Save changes" : "Add discount"}</button></div>
      </form>
    </section>`;
  const form = document.getElementById("discount-form");
  const int = (v) => (v.trim() === "" ? null : Number(v));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("form-error");
    err.textContent = "";
    if (!form.discounttype.value.trim() || form.discount.value.trim() === "") { err.textContent = "Discount type and discount are required."; return; }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const discount = {
      discounttype: form.discounttype.value.trim(), stor_id: form.stor_id.value || null, lowqty: int(form.lowqty.value),
      highqty: int(form.highqty.value), discount: Number(form.discount.value).toFixed(2) };
    try {
      if (editing) {
        await api("/discounts", { method: "PUT", body: { original, discount } });
        toast(`Saved the ${discount.discounttype}.`);
      } else {
        await api("/discounts", { method: "POST", body: discount });
        toast(`Added the ${discount.discounttype}.`);
      }
      location.hash = "#/admin/discounts";
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
}

async function adminEmployees() {
  const c = shell({ active: "#/admin/employees", title: "Employees", sub: "From pubs.employee · only the admin can add, edit or delete them" });
  const rows = await api("/employees");
  c.innerHTML = `
    <div style="display:flex;justify-content:flex-end"><a class="btn primary" href="#/admin/employees/new">+ Add employee</a></div>
    <section class="card">${table([
      { label: "ID", key: "emp_id", muted: true }, { label: "Name", key: "name", strong: true }, { label: "Job", key: "job_desc" },
      { label: "Level", key: "job_lvl", num: true }, { label: "Publisher", key: "pub_name", muted: true },
      { label: "Hired", value: (r) => date(r.hire_date), num: true, muted: true },
      { label: "", html: (r) => `<a class="btn small" href="#/admin/employees/${encodeURIComponent(r.emp_id)}/edit">Edit</a>
        <button class="btn danger small" data-delete="${esc(r.emp_id)}" data-name="${esc(r.name)}">Delete</button>`, num: true },
    ], rows, "No employees.")}<p class="sub" style="margin-top:12px">${num(rows.length)} employees</p></section>`;
  c.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delete]");
    if (!b) return;
    if (b.dataset.confirm !== "1") {   // two clicks instead of a browser dialog
      b.dataset.confirm = "1"; b.textContent = "Confirm"; return;
    }
    b.disabled = true;
    try {
      await api(`/employees/${encodeURIComponent(b.dataset.delete)}`, { method: "DELETE" });
      toast(`Deleted ${b.dataset.name}.`);
      adminEmployees();
    } catch (ex) { toast(ex.message); b.disabled = false; b.textContent = "Delete"; delete b.dataset.confirm; }
  });
}

// Add employee (no empId) and Edit employee share this form.
async function employeeForm(empId) {
  const editing = Boolean(empId);
  const c = shell({ active: "#/admin/employees", title: editing ? "Edit employee" : "Add employee",
    sub: editing ? `${empId} · details in pubs.employee · only the admin can do this` : "Add an employee to pubs.employee · only the admin can do this",
    back: ["#/admin/employees", "← Employees"] });
  const [{ jobs, publishers }, found] = await Promise.all([
    api("/employees/options"), editing ? api(`/employees/${encodeURIComponent(empId)}`) : null]);
  // A new employee starts in the first job at its lowest level, hired today.
  const emp = found || { job_id: jobs[0]?.job_id, job_lvl: jobs[0]?.min_lvl, pub_id: publishers[0]?.pub_id,
    hire_date: new Date().toISOString().slice(0, 10) };
  const val = (v) => esc(v ?? "");
  const sel = (a, v) => (a === v ? "selected" : "");
  c.innerHTML = `
    <section class="card">
      <h2>Employee details</h2><p class="sub">Shown on the Employees page.</p>
      <form class="fields" id="employee-form" novalidate>
        <label class="field"><span>First name</span><input name="fname" required maxlength="20" autocomplete="off" value="${val(emp.fname)}"></label>
        <label class="field"><span>Middle initial (optional)</span><input name="minit" maxlength="1" autocomplete="off" value="${val(emp.minit)}"></label>
        <label class="field"><span>Last name</span><input name="lname" required maxlength="30" autocomplete="off" value="${val(emp.lname)}"></label>
        ${editing ? `
        <label class="field"><span>Employee ID</span><input readonly value="${val(emp.emp_id)}">
          <span class="hint">The ID can't change.</span></label>` : `
        <label class="field"><span>Employee ID (optional)</span><input name="emp_id" maxlength="9" placeholder="e.g. PMA42628M" autocomplete="off">
          <span class="hint">3 letters, 5 digits, 1 letter. Leave empty to make one from the initials.</span></label>`}
        <label class="field"><span>Job</span>
          <select name="job_id" required>${jobs.map((j) => `<option value="${j.job_id}" ${sel(j.job_id, emp.job_id)}>${esc(j.job_desc)}</option>`).join("")}</select></label>
        <label class="field"><span>Level</span><input name="job_lvl" type="number" min="0" max="250" step="1" value="${val(emp.job_lvl)}">
          <span class="hint" id="lvl-hint"></span></label>
        <label class="field"><span>Publisher</span>
          <select name="pub_id" required>${publishers.map((p) => `<option value="${esc(p.pub_id)}" ${sel(p.pub_id, emp.pub_id)}>${esc(p.pub_name)}</option>`).join("")}</select></label>
        <label class="field"><span>Hire date</span><input name="hire_date" type="date" required value="${val(emp.hire_date)}"></label>
        <p class="error" id="form-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/admin/employees">Cancel</a><button class="btn primary" type="submit">${editing ? "Save changes" : "Add employee"}</button></div>
      </form>
    </section>`;
  const form = document.getElementById("employee-form");
  const hint = () => {
    const j = jobs.find((x) => String(x.job_id) === form.job_id.value);
    const lvl = Number(form.job_lvl.value);
    const outside = form.job_lvl.value !== "" && (lvl < j.min_lvl || lvl > j.max_lvl);
    document.getElementById("lvl-hint").textContent = `${j.job_desc}: ${j.min_lvl}–${j.max_lvl}` +
      (outside ? (editing ? ". This level is outside the range; it can stay, but a new job or level must fit." : ". Pick a level in this range.") : "");
  };
  form.job_id.addEventListener("change", hint);
  form.job_lvl.addEventListener("input", hint);
  hint();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("form-error");
    err.textContent = "";
    if (!form.fname.value.trim() || !form.lname.value.trim() || !form.hire_date.value) {
      err.textContent = "First name, last name and hire date are required."; return;
    }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const body = {
      fname: form.fname.value.trim(), minit: form.minit.value.trim() || null, lname: form.lname.value.trim(),
      job_id: Number(form.job_id.value), job_lvl: form.job_lvl.value === "" ? null : Number(form.job_lvl.value),
      pub_id: form.pub_id.value, hire_date: form.hire_date.value };
    const name = `${body.fname} ${body.lname}`;
    try {
      if (editing) {
        await api(`/employees/${encodeURIComponent(empId)}`, { method: "PUT", body });
        toast(`Saved ${name}.`);
      } else {
        body.emp_id = form.emp_id.value.trim() || null;
        const res = await api("/employees", { method: "POST", body });
        toast(`Added ${name} as ${res.emp_id}.`);
      }
      location.hash = "#/admin/employees";
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
}

async function adminPublishers() {
  const c = shell({ active: "#/admin/publishers", title: "Publishers", sub: "From pubs.publishers · only the admin can add, edit or delete them" });
  const rows = await api("/publishers");
  c.innerHTML = `
    <div style="display:flex;justify-content:flex-end"><a class="btn primary" href="#/admin/publishers/new">+ Add publisher</a></div>
    <section class="card">${table([
      { label: "ID", key: "pub_id", muted: true }, { label: "Name", key: "pub_name", strong: true }, { label: "City", key: "city" },
      { label: "State", value: (r) => r.state || "—", muted: true }, { label: "Country", key: "country" },
      { label: "Books", key: "books", num: true }, { label: "Sold", key: "sold", num: true, strong: true },
      { label: "Revenue", value: (r) => (Number(r.revenue) ? money(r.revenue) : "—"), num: true },
      { label: "", html: (r) => `<a class="btn small" href="#/admin/publishers/${encodeURIComponent(r.pub_id)}/edit">Edit</a>
        <button class="btn danger small" data-delete="${esc(r.pub_id)}" data-name="${esc(r.pub_name)}">Delete</button>`, num: true },
    ], rows, "No publishers.")}
    <p class="sub" style="margin-top:12px">Publishers with books or employees can't be deleted, so those records keep their publisher.</p></section>`;
  c.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delete]");
    if (!b) return;
    if (b.dataset.confirm !== "1") {   // two clicks instead of a browser dialog
      b.dataset.confirm = "1"; b.textContent = "Confirm"; return;
    }
    b.disabled = true;
    try {
      await api(`/publishers/${encodeURIComponent(b.dataset.delete)}`, { method: "DELETE" });
      toast(`Deleted ${b.dataset.name}.`);
      adminPublishers();
    } catch (ex) { toast(ex.message); b.disabled = false; b.textContent = "Delete"; delete b.dataset.confirm; }
  });
}

// Add publisher (no pubId) and Edit publisher share this form.
async function publisherForm(pubId) {
  const editing = Boolean(pubId);
  const c = shell({ active: "#/admin/publishers", title: editing ? "Edit publisher" : "Add publisher",
    sub: editing ? `${pubId} · details in pubs.publishers · only the admin can do this` : "Add a publisher to pubs.publishers · only the admin can do this",
    back: ["#/admin/publishers", "← Publishers"] });
  const p = editing ? await api(`/publishers/${encodeURIComponent(pubId)}`) : { country: "USA" };
  const val = (v) => esc(v ?? "");
  c.innerHTML = `
    <section class="card">
      <h2>Publisher details</h2><p class="sub">Shown on books, sales and the Home page.</p>
      <form class="fields" id="publisher-form" novalidate>
        <label class="field"><span>Name</span><input name="pub_name" required maxlength="40" autocomplete="off" value="${val(p.pub_name)}"></label>
        ${editing ? `
        <label class="field"><span>Publisher ID</span><input readonly value="${val(p.pub_id)}">
          <span class="hint">Books and employees point at this ID, so it can't change.</span></label>` : `
        <label class="field"><span>Publisher ID (optional)</span><input name="pub_id" maxlength="4" inputmode="numeric" placeholder="e.g. 9004" autocomplete="off">
          <span class="hint">Four digits. Leave empty to get the next free ID.</span></label>`}
        <label class="field"><span>City (optional)</span><input name="city" maxlength="20" autocomplete="off" value="${val(p.city)}"></label>
        <label class="field"><span>State (optional)</span><input name="state" maxlength="2" autocomplete="off" value="${val(p.state)}" placeholder="MA">
          <span class="hint">Two letters. Leave empty outside the US.</span></label>
        <label class="field"><span>Country</span><input name="country" required maxlength="30" autocomplete="off" value="${val(p.country)}"></label>
        <p class="error" id="form-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/admin/publishers">Cancel</a><button class="btn primary" type="submit">${editing ? "Save changes" : "Add publisher"}</button></div>
      </form>
    </section>`;
  const form = document.getElementById("publisher-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("form-error");
    err.textContent = "";
    if (!form.pub_name.value.trim() || !form.country.value.trim()) { err.textContent = "Name and country are required."; return; }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const body = { pub_name: form.pub_name.value.trim(), city: form.city.value.trim() || null,
      state: form.state.value.trim() || null, country: form.country.value.trim() };
    try {
      if (editing) {
        await api(`/publishers/${encodeURIComponent(pubId)}`, { method: "PUT", body });
        toast(`Saved ${body.pub_name}.`);
      } else {
        body.pub_id = form.pub_id.value.trim() || null;
        const res = await api("/publishers", { method: "POST", body });
        toast(`Added ${body.pub_name} as ${res.pub_id}.`);
      }
      location.hash = "#/admin/publishers";
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
}

async function adminAccount() {
  const c = shell({ active: "#/admin/account", title: "My Account", sub: "Your sign-in details and profile" });
  const me = await api("/me");
  c.innerHTML = `
    <section class="grid-main">
      <div class="card">
        <h2>Profile</h2><p class="sub">The publisher account that runs BookNest</p>
        <form class="fields" id="me-form" novalidate>
          <label class="field"><span>Display name</span><input id="me-name" name="display_name" value="${esc(me.display_name)}" required></label>
          <label class="field"><span>Email</span><input id="me-email" name="email" type="email" value="${esc(me.email || "")}"></label>
          <label class="field"><span>Username</span><input readonly value="@${esc(me.username)}"></label>
          <p class="error" id="me-error" style="grid-column:1/-1"></p>
          <div class="actions"><button class="btn primary" type="submit">Save changes</button></div>
        </form>
        <hr style="border:0;border-top:1px solid var(--card-line);margin:20px 0">
        <form class="fields" id="pw-form" novalidate>
          <label class="field"><span>Current password</span><input id="pw-current" name="current" type="password" autocomplete="current-password"></label>
          <label class="field"><span>New password (8+ characters)</span><input id="pw-new" name="next" type="password" autocomplete="new-password"></label>
          <p class="error" id="pw-error" style="grid-column:1/-1"></p>
          <div class="actions"><button class="btn" type="submit">Change password</button></div>
        </form>
      </div>
      <div class="card">
        <h2>Account type</h2>
        <p><span class="badge admin">Admin</span> <span class="sub">Publisher · since ${date(me.created_at)}</span></p>
        <ul class="checks">
          <li>Add, edit and delete authors, create author logins</li><li>See, add, edit and delete books (authors can manage their own)</li><li>Add, edit and delete publishers</li><li>Add, edit and delete stores</li><li>Add, edit and delete employees</li><li>Add, edit and delete discounts</li><li>Add, edit and delete sales</li><li>See sales, stores and discounts</li>
          <li>See employees and publishers</li><li>Edit or delete any author account</li>
        </ul>
      </div>
    </section>`;
  document.getElementById("me-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("me-error");
    err.textContent = "";
    try {
      await api("/me", { method: "PATCH", body: { display_name: e.target.display_name.value.trim(), email: e.target.email.value.trim() || null } });
      session.user.display_name = e.target.display_name.value.trim();
      saveSession(session, isRemembered());
      toast("Saved.");
      adminAccount();
    } catch (ex) { err.textContent = ex.message; }
  });
  document.getElementById("pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("pw-error");
    err.textContent = "";
    try {
      await api("/me/password", { method: "POST", body: { current_password: e.target.current.value, new_password: e.target.next.value } });
      e.target.reset();
      toast("Password changed. Other sessions were signed out.");
    } catch (ex) { err.textContent = ex.message; }
  });
}

// ---------- author pages ----------

async function authorBooks() {
  const c = shell({ active: "#/author/books", title: "My Books", sub: "The books you published · only you and the admin can see this page" });
  const { books, totals } = await api("/me/books");
  c.innerHTML = `
    <div style="display:flex;justify-content:flex-end"><a class="btn primary" href="#/author/books/new">+ Add book</a></div>
    ${tiles([["Books published", num(totals.books), "Under your name"], ["Copies sold", num(totals.sold), "Across all stores"], ["Sales", money(totals.revenue), "Quantity × list price"]])}
    <section class="card"><h2>Your books</h2>${table([
      { label: "ID", key: "title_id", muted: true }, { label: "Title", key: "title", strong: true }, { label: "Type", key: "type", muted: true },
      { label: "Publisher", key: "pub_name" }, { label: "Published", value: (r) => date(r.pubdate), muted: true },
      { label: "Price", value: (r) => money(r.price), num: true }, { label: "Sold", key: "sold", num: true, strong: true },
      { label: "", html: (r) => `<a class="btn small" href="#/author/books/${encodeURIComponent(r.title_id)}/edit">Edit</a>
        <button class="btn danger small" data-delete="${esc(r.title_id)}" data-title="${esc(r.title)}">Delete</button>`, num: true },
    ], books, "You have no published books yet.")}
    <p class="sub" style="margin-top:12px">You can delete a book only if you're its only author and it has no sales.</p></section>
    <div class="note"><strong>What you can see</strong>Your own account and the books you published, with their copies sold. You can add, edit and delete your own books.</div>`;
  c.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delete]");
    if (!b) return;
    if (b.dataset.confirm !== "1") {   // two clicks instead of a browser dialog
      b.dataset.confirm = "1"; b.textContent = "Confirm"; return;
    }
    b.disabled = true;
    try {
      await api(`/me/books/${encodeURIComponent(b.dataset.delete)}`, { method: "DELETE" });
      toast(`Deleted “${b.dataset.title}”.`);
      authorBooks();
    } catch (ex) { toast(ex.message); b.disabled = false; b.textContent = "Delete"; delete b.dataset.confirm; }
  });
}

// An author's Add book (no titleId) and Edit book share this form.
async function authorBookForm(titleId) {
  const editing = Boolean(titleId);
  const c = shell({ active: "#/author/books", title: editing ? "Edit book" : "Add book",
    sub: editing ? `${titleId} · one of your books` : "Add a book under your name", back: ["#/author/books", "← My Books"] });
  const [{ types, publishers }, found] = await Promise.all([
    api("/me/books/options"), editing ? api(`/me/books/${encodeURIComponent(titleId)}`) : null]);
  const b = found || { type: "business", pubdate: new Date().toISOString().slice(0, 10) };
  const val = (v) => esc(v ?? "");
  const sel = (a, v) => (a === v ? "selected" : "");
  const typeList = types.includes(b.type) ? types : [...types, b.type];
  c.innerHTML = `
    <section class="card">
      <h2>Book details</h2><p class="sub">${editing ? "Advance and royalty are set by your admin." : "The book is linked to you, so it shows on My Books. Its ID is made for you."}</p>
      <form class="fields" id="book-form" novalidate>
        <label class="field" style="grid-column:1/-1"><span>Title</span><input name="title" required maxlength="80" autocomplete="off" value="${val(b.title)}"></label>
        <label class="field"><span>Type</span>
          <select name="type" required>${typeList.map((t) => `<option ${sel(t, b.type)}>${esc(t)}</option>`).join("")}</select></label>
        <label class="field"><span>Publisher</span>
          <select name="pub_id" required>${publishers.map((p) => `<option value="${esc(p.pub_id)}" ${sel(p.pub_id, b.pub_id)}>${esc(p.pub_name)}</option>`).join("")}</select></label>
        <label class="field"><span>Price ($, optional)</span><input name="price" type="number" min="0" step="0.01" placeholder="19.99" value="${val(b.price)}"></label>
        <label class="field"><span>Publish date</span><input name="pubdate" type="date" value="${val(b.pubdate)}"></label>
        <label class="field" style="grid-column:1/-1"><span>Notes (optional)</span><input name="notes" maxlength="200" autocomplete="off" value="${val(b.notes)}"></label>
        <p class="error" id="form-error" style="grid-column:1/-1"></p>
        <div class="actions"><a class="btn" href="#/author/books">Cancel</a><button class="btn primary" type="submit">${editing ? "Save changes" : "Add book"}</button></div>
      </form>
    </section>`;
  const form = document.getElementById("book-form");
  const opt = (v) => (v.trim() === "" ? null : v.trim());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("form-error");
    err.textContent = "";
    if (!form.title.value.trim()) { err.textContent = "Enter a title."; return; }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const body = { title: form.title.value.trim(), type: form.type.value, pub_id: form.pub_id.value, price: opt(form.price.value),
      pubdate: opt(form.pubdate.value), notes: opt(form.notes.value) };
    try {
      if (editing) {
        await api(`/me/books/${encodeURIComponent(titleId)}`, { method: "PUT", body });
        toast(`Saved “${body.title}”.`);
      } else {
        const res = await api("/me/books", { method: "POST", body });
        toast(`Added “${body.title}” as ${res.title_id}.`);
      }
      location.hash = "#/author/books";
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
}

async function authorAccount() {
  const c = shell({ active: "#/author/account", title: "My Account", sub: "Your profile and login details · set by your admin" });
  const me = await api("/me");
  const place = [me.author?.city, me.author?.state].filter(Boolean).join(", ");
  c.innerHTML = `
    <section class="grid-main">
      <div class="card">
        <h2>Profile and login details</h2><p class="sub">Your admin created this account. Ask them to change anything.</p>
        <div class="fields" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px 24px">
          <label class="field"><span>Display name</span><input readonly value="${esc(me.display_name)}"></label>
          <label class="field"><span>Email</span><input readonly value="${esc(me.email || "—")}"></label>
          <label class="field"><span>Username</span><input readonly value="@${esc(me.username)}"></label>
          <label class="field"><span>Location</span><input readonly value="${esc(place || "—")}"></label>
        </div>
        <p class="sub" style="margin-top:20px">Password: set by your admin. Ask them for a new one if you forget it.</p>
      </div>
      <div class="card">
        <h2>Account type</h2>
        <p><span class="badge author">User</span> <span class="sub">Author · since ${date(me.created_at)}</span></p>
        <ul class="checks">
          <li>See your own account</li><li>See the books you published</li><li>Add, edit and delete your own books</li><li class="no">Change your login details</li><li class="no">See other authors or books</li><li class="no">See sales, employees or other tabs</li>
        </ul>
      </div>
    </section>`;
}

// ---------- router ----------

const ADMIN_ROUTES = {
  "": adminHome, books: adminBooks, "books/new": () => bookForm(), authors: adminAuthors, "authors/new": adminAddAuthor, sales: adminSales, "sales/new": () => saleForm(),
  stores: adminStores, "stores/new": () => storeForm(), discounts: adminDiscounts, "discounts/new": () => discountForm(), employees: adminEmployees, "employees/new": () => employeeForm(), publishers: adminPublishers, "publishers/new": () => publisherForm(), account: adminAccount,
};
const AUTHOR_ROUTES = { books: authorBooks, "books/new": () => authorBookForm(), account: authorAccount };

async function route() {
  const path = location.hash.replace(/^#\/?/, "");
  const [area, ...rest] = path.split("/");
  const sub = rest.join("/");

  if (area === "login") {
    if (session) { location.hash = session.user.role === "admin" ? "#/admin" : "#/author/books"; return; }
    return loginPage(sub === "admin" ? "admin" : "author");
  }
  if (!session) { location.hash = area === "admin" ? "#/login/admin" : "#/login/author"; return; }

  const role = session.user.role;
  // Authors never get admin pages; the API refuses them too.
  if (area !== role) { location.hash = role === "admin" ? "#/admin" : "#/author/books"; return; }

  let view;
  if (role === "admin") {
    const detail = sub.match(/^authors\/(\d+)$/);
    const bookEdit = sub.match(/^books\/([^/]+)\/edit$/);
    const authorEdit = sub.match(/^authors\/([^/]+)\/edit$/);
    const publisherEdit = sub.match(/^publishers\/([^/]+)\/edit$/);
    const storeEdit = sub.match(/^stores\/([^/]+)\/edit$/);
    const employeeEdit = sub.match(/^employees\/([^/]+)\/edit$/);
    const discountEdit = sub.match(/^discounts\/edit\/([A-Za-z0-9_-]+)$/);
    const saleEdit = sub.match(/^sales\/([^/]+\/[^/]+\/[^/]+)\/edit$/);
    view = detail ? () => adminAuthorDetail(detail[1])
      : bookEdit ? () => bookForm(decodeURIComponent(bookEdit[1]))
      : authorEdit ? () => adminEditAuthor(decodeURIComponent(authorEdit[1]))
      : publisherEdit ? () => publisherForm(decodeURIComponent(publisherEdit[1]))
      : storeEdit ? () => storeForm(decodeURIComponent(storeEdit[1]))
      : employeeEdit ? () => employeeForm(decodeURIComponent(employeeEdit[1]))
      : discountEdit ? () => discountForm(discountEdit[1])
      : saleEdit ? () => saleForm(saleEdit[1]) : ADMIN_ROUTES[sub];
  } else {
    const myBookEdit = sub.match(/^books\/([^/]+)\/edit$/);
    view = myBookEdit ? () => authorBookForm(decodeURIComponent(myBookEdit[1])) : AUTHOR_ROUTES[sub];
  }
  if (!view) { location.hash = role === "admin" ? "#/admin" : "#/author/books"; return; }
  try {
    await view();
  } catch (ex) {
    if (ex.status === 401) return;   // already sent to login
    const content = document.getElementById("content");
    if (content) content.innerHTML = `<div class="note"><strong>Couldn't load this page</strong>${esc(ex.message)}</div>`;
  }
}

window.addEventListener("hashchange", route);
route();
