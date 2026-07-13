"""
Lazy, pooled database access for the API.

The legacy Flask server opened its pool at *import* time, so the whole process
couldn't even start without the DB up. Here the pool is created on FastAPI
startup (see api/main.py's lifespan) and, if that fails, deferred to the first
real request -- so the app can boot, serve /health and /stations (which need no
DB), and only a DB-backed route errors when the DB is genuinely unavailable.
"""

from contextlib import contextmanager
from typing import Optional

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from db import DB_CONFIG  # core/db.py

from api import config

_pool: Optional[ThreadedConnectionPool] = None


def init_pool(minconn: int = None, maxconn: int = None) -> Optional[ThreadedConnectionPool]:
    """Create the pool if it doesn't exist yet. Never raises -- a failure here
    just leaves the pool uncreated so a later request can retry -- so app
    startup never hard-fails on a transient DB hiccup."""
    global _pool
    if _pool is not None:
        return _pool
    try:
        _pool = ThreadedConnectionPool(
            minconn if minconn is not None else config.POOL_MIN,
            maxconn if maxconn is not None else config.POOL_MAX,
            cursor_factory=RealDictCursor,
            **DB_CONFIG,
        )
    except Exception as e:  # noqa: BLE001 -- deliberately defer, don't crash startup
        print(f"[api.db] connection pool init deferred: {type(e).__name__}: {e}", flush=True)
        _pool = None
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def _require_pool() -> ThreadedConnectionPool:
    pool = _pool or init_pool()
    if pool is None:
        raise RuntimeError("database is unavailable (connection pool could not be created)")
    return pool


@contextmanager
def connection():
    """Borrow a pooled connection for the duration of a request, returning it
    afterwards. The workload is read-only, so the implicit transaction is rolled
    back on the way out to hand a clean connection back to the pool."""
    pool = _require_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
