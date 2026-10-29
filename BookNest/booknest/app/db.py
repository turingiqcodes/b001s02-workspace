"""Postgres access: one connection pool, plus small query helpers."""
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
from fastapi import HTTPException

from .config import DB

MAX_CONNECTIONS = 10
# FastAPI runs up to 40 requests at once, but the pool raises instead of waiting when all
# connections are out. Requests wait here for a free one instead.
_free = threading.BoundedSemaphore(MAX_CONNECTIONS)
WAIT_SECONDS = 30   # then answer 503
_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, MAX_CONNECTIONS, **DB)
    return _pool


@contextmanager
def transaction():
    """Yields a dict cursor; commits on success, rolls back on error."""
    if not _free.acquire(timeout=WAIT_SECONDS):
        raise HTTPException(503, "The server is busy. Try again in a moment.")
    try:
        conn = pool().getconn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool().putconn(conn)
    finally:
        _free.release()


def fetch_all(sql, params=None):
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql, params=None):
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.fetchone()
