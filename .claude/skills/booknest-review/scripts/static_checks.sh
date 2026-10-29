#!/usr/bin/env bash
# Fast, read-only candidate scan for the BookNest review. Hits are leads to verify, not findings.
# Usage: static_checks.sh [path/to/booknest]
set -u
ROOT="${1:-BookNest/booknest}"
cd "$ROOT" || { echo "No such folder: $ROOT"; exit 1; }
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python

section() { printf '\n== %s\n' "$1"; }

section "Python compiles"
$PY -m py_compile app/*.py tests/*.py && echo "ok"

section "Routes without an auth dependency (expected public: login, index, middleware)"
grep -n -A2 '^@app\.\(get\|post\|put\|patch\|delete\)' app/main.py \
  | grep -E '^[0-9]+-(async )?def ' | grep -v 'Depends(' || echo "none"

section "f-string / format SQL (OK only if it interpolates module constants)"
grep -nE 'f"""|f"(SELECT|UPDATE|DELETE|INSERT|WITH)|\.format\(|%\s*\(' app/*.py | grep -v '^\s*#' || echo "none"
echo "-- {…} placeholders inside f-strings in main.py:"
grep -noE '\{[A-Za-z_][A-Za-z_0-9.]*\}' app/main.py | sort -t: -k2 -u | awk -F: '{print $2}' | sort | uniq -c

section "Multiple separate commits in one handler (fetch_one/fetch_all/transaction count per def)"
awk '/^(async )?def /{if(n>2)print name": "n; name=$0; n=0} /fetch_one\(|fetch_all\(|transaction\(\)/{n++} END{if(n>2)print name": "n}' app/main.py || true

section "Bare / broad excepts"
grep -nE 'except( Exception)?:' app/*.py || echo "none"

section "Frontend innerHTML sinks and escape helper"
echo "innerHTML uses: $(grep -c 'innerHTML' frontend/app.js)"
grep -nE 'function (esc|escape|h)\b|const (esc|escape|h) =' frontend/app.js || echo "no escape helper found"

section "Frontend token storage"
grep -nE 'localStorage|sessionStorage' frontend/app.js

section "Secrets hygiene"
grep -q '^\.env$' .gitignore 2>/dev/null && echo ".env is git-ignored" || echo "WARNING: .env not in .gitignore"
grep -rnEi '(password|secret|api_key)\s*=\s*["'"'"'][^"'"'"']{6,}' app/ frontend/ || echo "no hard-coded secrets matched"

section "Indexes in migration"
grep -niE 'create (unique )?index|unique|primary key' sql/*.sql

section "Pool size vs threadpool"
grep -n 'ThreadedConnectionPool' app/db.py

section "Lists without LIMIT (SELECT ... FROM in GET handlers; eyeball)"
grep -n -A1 '^@app.get' app/main.py | grep -oE 'def [a-z_]+' | sed 's/def //' | tr '\n' ' '; echo

section "Test coverage: route paths never mentioned in tests/test_api.py"
grep -oE '^@app\.(get|post|put|patch|delete)\("[^"]+"' app/main.py | sed -E 's/.*\("//; s/"$//' | sort -u | while read -r p; do
  pat=$(printf '%s' "$p" | sed -E 's/\{[^}]+\}/[^"]*/g')
  grep -qE "$pat" tests/test_api.py || echo "untested: $p"
done
