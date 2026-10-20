from __future__ import annotations

import os
from pathlib import Path

import asyncpg

_pool: asyncpg.Pool | None = None
_SCHEMA_LOCK_ID = 731904120


async def open_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5436/modelshare",
    )
    _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=8, command_timeout=60)
    schema = Path(__file__).with_name("init.sql").read_text()
    async with _pool.acquire() as connection:
        await connection.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK_ID)
        try:
            await connection.execute(schema)
        finally:
            await connection.execute("SELECT pg_advisory_unlock($1)", _SCHEMA_LOCK_ID)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool
