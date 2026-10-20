from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

from modelshare import db

log = logging.getLogger(__name__)
_COLLECT_LOCK_ID = 731904121


class PayloadCache:
    def __init__(self, payload: dict[str, Any]):
        self.json_bytes = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        self.gzip_bytes = gzip.compress(self.json_bytes, mtime=0)
        self.etag = hashlib.sha1(self.json_bytes).hexdigest()
        self.payload = payload


_cache: PayloadCache | None = None
_next_run_at: datetime | None = None


def get_cache() -> PayloadCache | None:
    return _cache


def get_next_run_at() -> datetime | None:
    return _next_run_at


def _set_cache(payload: dict[str, Any]) -> None:
    global _cache
    _cache = PayloadCache(payload)


def _build(bundle: dict, stored_daily: list[dict]) -> tuple[dict, list[dict]]:
    from modelshare.compute import build

    return build(bundle, stored_daily)


async def _fetch_bundle() -> dict:
    bundle_file = os.environ.get("BUNDLE_FILE")
    if bundle_file:
        return json.loads(Path(bundle_file).read_text())

    from modelshare.sources import fetch_bundle

    return await fetch_bundle()


async def _load_daily(connection: asyncpg.Connection) -> list[dict]:
    rows = await connection.fetch(
        """
        SELECT date::text AS date, permaslug, variant, author, prompt, completion,
               cached, reasoning, requests, est_usd, spend_usd
        FROM daily
        ORDER BY date, author, permaslug, variant
        """
    )
    return [dict(row) for row in rows]


async def _upsert_daily(connection: asyncpg.Connection, rows: list[dict]) -> None:
    if not rows:
        return
    values = [
        (
            date.fromisoformat(str(row["date"])),
            row["permaslug"],
            row["variant"],
            row["author"],
            row["prompt"],
            row["completion"],
            row["cached"],
            row["reasoning"],
            row["requests"],
            row["est_usd"],
            row["spend_usd"],
        )
        for row in rows
    ]
    await connection.executemany(
        """
        INSERT INTO daily (
            date, permaslug, variant, author, prompt, completion, cached, reasoning,
            requests, est_usd, spend_usd
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (date, permaslug, variant) DO UPDATE SET
            author = EXCLUDED.author,
            prompt = EXCLUDED.prompt,
            completion = EXCLUDED.completion,
            cached = EXCLUDED.cached,
            reasoning = EXCLUDED.reasoning,
            requests = EXCLUDED.requests,
            est_usd = EXCLUDED.est_usd,
            spend_usd = EXCLUDED.spend_usd,
            updated_at = now()
        """,
        values,
    )


async def run() -> dict[str, Any]:
    started_monotonic = time.monotonic()
    started_at = datetime.now(UTC)
    pool = db.get_pool()
    async with pool.acquire() as connection:
        locked = await connection.fetchval("SELECT pg_try_advisory_lock($1)", _COLLECT_LOCK_ID)
        if not locked:
            return {"ok": False, "skipped": True, "error": "Another collector holds the lock"}

        run_id: int | None = None
        n_models: int | None = None
        n_rows = 0
        build_ms: float | None = None
        try:
            run_id = await connection.fetchval(
                "INSERT INTO runs (started_at) VALUES ($1) RETURNING id", started_at
            )
            bundle = await _fetch_bundle()
            n_models = len(bundle.get("activity") or bundle.get("models") or [])
            errors = bundle.get("errors") or []
            await connection.execute(
                """
                INSERT INTO snapshots (seconds, n_models, errors, bundle)
                VALUES ($1, $2, $3::jsonb, $4::jsonb)
                """,
                bundle.get("seconds"),
                n_models,
                json.dumps(errors),
                json.dumps(bundle),
            )
            await connection.execute(
                """
                DELETE FROM snapshots
                WHERE id NOT IN (
                    SELECT id FROM snapshots ORDER BY id DESC LIMIT 60
                )
                """
            )

            previous_daily = await _load_daily(connection)
            build_started = time.perf_counter()
            _, daily_rows = _build(bundle, previous_daily)
            build_ms = (time.perf_counter() - build_started) * 1000
            await _upsert_daily(connection, daily_rows)

            all_daily = await _load_daily(connection)
            build_started = time.perf_counter()
            payload, _ = _build(bundle, all_daily)
            build_ms += (time.perf_counter() - build_started) * 1000
            _set_cache(payload)
            n_rows = len(daily_rows)
            await connection.execute(
                """
                UPDATE runs SET finished_at = now(), ok = true, seconds = $2,
                    n_models = $3, n_rows = $4, build_ms = $5
                WHERE id = $1
                """,
                run_id,
                time.monotonic() - started_monotonic,
                n_models,
                n_rows,
                build_ms,
            )
            return {
                "ok": True,
                "n_models": n_models,
                "n_rows": n_rows,
                "build_ms": build_ms,
            }
        except Exception as exc:
            log.exception("ModelShare collection failed")
            if run_id is not None:
                await connection.execute(
                    """
                    UPDATE runs SET finished_at = now(), ok = false, seconds = $2,
                        n_models = $3, n_rows = $4, build_ms = $5, error = $6
                    WHERE id = $1
                    """,
                    run_id,
                    time.monotonic() - started_monotonic,
                    n_models,
                    n_rows,
                    build_ms,
                    str(exc)[:4000],
                )
            return {"ok": False, "error": str(exc)}
        finally:
            await connection.execute("SELECT pg_advisory_unlock($1)", _COLLECT_LOCK_ID)


async def restore_cache() -> None:
    pool = db.get_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT bundle FROM snapshots ORDER BY fetched_at DESC, id DESC LIMIT 1"
        )
        if row is None:
            return
        bundle = row["bundle"]
        if isinstance(bundle, str):
            bundle = json.loads(bundle)
        daily_rows = await _load_daily(connection)
    payload, _ = _build(bundle, daily_rows)
    _set_cache(payload)


def _interval_seconds() -> float:
    try:
        hours = float(os.environ.get("COLLECT_EVERY_HOURS", "6"))
    except ValueError:
        hours = 6.0
    return max(60.0, hours * 3600)


async def _snapshot_is_stale() -> bool:
    async with db.get_pool().acquire() as connection:
        fetched_at = await connection.fetchval(
            "SELECT fetched_at FROM snapshots ORDER BY fetched_at DESC, id DESC LIMIT 1"
        )
    if fetched_at is None:
        return True
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - fetched_at >= timedelta(seconds=_interval_seconds())


async def loop() -> None:
    global _next_run_at
    interval = _interval_seconds()
    while True:
        try:
            if await _snapshot_is_stale():
                await run()
        except Exception:
            log.exception("Collector loop iteration failed")
        _next_run_at = datetime.now(UTC) + timedelta(seconds=interval)
        await asyncio.sleep(interval)
