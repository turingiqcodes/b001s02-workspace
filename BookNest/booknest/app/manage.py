"""Admin tasks.

  python -m app.manage migrate                      run sql/*.sql in order
  python -m app.manage create-admin USERNAME "Display Name" [EMAIL]
                                                    asks for the password
  python -m app.manage prune-keys [DAYS]            delete API keys expired or revoked
                                                    more than DAYS (default 30) ago
"""
import getpass
import sys

import psycopg2.errors

from .config import ROOT
from .db import transaction
from .security import hash_password


def migrate():
    for path in sorted((ROOT / "sql").glob("*.sql")):
        print("running", path.name)
        with transaction() as cur:
            cur.execute(path.read_text())
    print("done")


def create_admin(username, display_name, email=None):
    password = getpass.getpass("Password for the admin (8+ characters): ")
    if len(password) < 8 or password != getpass.getpass("Repeat it: "):
        sys.exit("Passwords must match and be at least 8 characters.")
    try:
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO public.app_user (username, display_name, email, password_hash, role)
                VALUES (%s, %s, %s, %s, 'admin') RETURNING user_id
                """,
                (username.lstrip("@").lower(), display_name, email, hash_password(password)),
            )
            print("created admin, user_id", cur.fetchone()["user_id"])
    except psycopg2.errors.UniqueViolation:
        sys.exit(f"Username {username!r} already exists.")


def prune_keys(days=30):
    # Keys stop working at expiry or revocation; "last sign-in" lives on app_user, so nothing else is lost.
    with transaction() as cur:
        cur.execute(
            "DELETE FROM public.api_key WHERE expires_at < now() - make_interval(days => %s) "
            "OR revoked_at < now() - make_interval(days => %s)", (days, days))
        print("deleted", cur.rowcount, "old API keys")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["migrate"]:
        migrate()
    elif args[:1] == ["create-admin"] and len(args) in (3, 4):
        create_admin(*args[1:])
    elif args[:1] == ["prune-keys"] and len(args) in (1, 2) and all(a.isdigit() for a in args[1:]):
        prune_keys(*map(int, args[1:]))
    else:
        sys.exit(__doc__)
