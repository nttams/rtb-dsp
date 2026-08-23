"""Async Postgres connection pool (psycopg3)."""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


def make_pool(database_url: str, *, open: bool = False, min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    """Create an async connection pool. Callers must ``await pool.open()`` (or pass
    ``open=True`` when running inside a running event loop) before use, and
    ``await pool.close()`` on shutdown.
    """
    return AsyncConnectionPool(conninfo=database_url, open=open, min_size=min_size, max_size=max_size)
