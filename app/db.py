"""Postgres connection pool (Neon in prod) and `python -m app.db init`.

Neon's pooled endpoint runs PgBouncer in transaction mode: no SET, LISTEN/NOTIFY or advisory
locks, so we pass prepare_threshold=None and keep every multi-statement change inside one
transaction (docs/DECISIONS.md section 11).
"""

from __future__ import annotations

import asyncio
import sys

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.config import settings

CONNECT_KWARGS = {"autocommit": True, "prepare_threshold": None}

_pool: AsyncConnectionPool | None = None


async def open_pool(url: str | None = None) -> AsyncConnectionPool:
    """Open the shared pool (idempotent). Call from the FastAPI lifespan."""
    global _pool
    if _pool is None:
        conninfo = url or settings.database_url
        if not conninfo:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = AsyncConnectionPool(
            conninfo, kwargs=CONNECT_KWARGS, min_size=1, max_size=5, open=False
        )
        await _pool.open(wait=True, timeout=15)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("database pool is not open")
    return _pool


async def _init() -> None:
    from app.jobs import init_schema

    async with await AsyncConnection.connect(settings.database_url, **CONNECT_KWARGS) as conn:
        await init_schema(conn)
    print("schema ready")


if __name__ == "__main__":
    if sys.argv[1:] != ["init"]:
        sys.exit("usage: python -m app.db init")
    asyncio.run(_init())
