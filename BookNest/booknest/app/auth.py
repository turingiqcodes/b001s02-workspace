"""Request authentication: every API call sends `Authorization: Bearer <api key>`."""
from fastapi import Depends, Header, HTTPException

from .db import transaction
from .security import hash_api_key


def current_user(authorization: str = Header(default="")):
    scheme, _, key = authorization.partition(" ")
    if scheme.lower() != "bearer" or not key:
        raise HTTPException(401, "Sign in first: missing API key.")
    with transaction() as cur:
        cur.execute(
            """
            SELECT k.key_id, u.user_id, u.username, u.display_name, u.email, u.role, u.au_id,
                   k.last_used_at IS NULL OR k.last_used_at < now() - interval '5 minutes' AS stale
            FROM public.api_key k JOIN public.app_user u USING (user_id)
            WHERE k.key_hash = %s
              AND k.revoked_at IS NULL AND k.expires_at > now() AND u.is_active
            """,
            (hash_api_key(key),),
        )
        user = cur.fetchone()
        # Most requests only read; last_used_at is refreshed at most every 5 minutes per key.
        if user and user.pop("stale"):
            cur.execute("UPDATE public.api_key SET last_used_at = now() WHERE key_id = %s", (user["key_id"],))
    if not user:
        raise HTTPException(401, "Your session has ended. Sign in again.")
    return user


def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Only the admin can do this.")
    return user


def require_author(user=Depends(current_user)):
    if user["role"] != "author":
        raise HTTPException(403, "This page is for authors.")
    return user
