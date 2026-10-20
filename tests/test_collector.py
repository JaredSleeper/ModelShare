from __future__ import annotations

import gzip
import json
from pathlib import Path

import httpx
import pytest

from modelshare import app as app_module
from modelshare import collector, compute, db

FIXTURE = Path(__file__).parent / "fixtures" / "bundle.json"


def test_compute_build_returns_payload_and_daily_contract():
    bundle = json.loads(FIXTURE.read_text())
    payload, daily_rows = compute.build(bundle, [])

    assert {"dates", "labs", "models"} <= payload.keys()
    assert daily_rows
    row_fields = {
        "date",
        "permaslug",
        "variant",
        "author",
        "prompt",
        "completion",
        "cached",
        "reasoning",
        "requests",
        "est_usd",
        "spend_usd",
    }
    assert all(row_fields <= row.keys() for row in daily_rows)


@pytest.mark.asyncio
async def test_collection_persists_and_serves_cached_payload(monkeypatch):
    monkeypatch.setenv("BUNDLE_FILE", str(FIXTURE))
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5436/modelshare"
    )
    pool = await db.open_pool()
    async with pool.acquire() as connection:
        await connection.execute("TRUNCATE snapshots, daily, runs RESTART IDENTITY")

    def stub_build(bundle, stored_daily):
        daily_rows = [
            {
                "date": "2026-10-20",
                "permaslug": "anthropic/claude-test",
                "variant": "",
                "author": "anthropic",
                "prompt": 120,
                "completion": 40,
                "cached": 5,
                "reasoning": 2,
                "requests": 3,
                "est_usd": 0.25,
                "spend_usd": 0.2,
            }
        ]
        return {"count": len(stored_daily) + 1}, daily_rows

    monkeypatch.setattr(collector, "_build", stub_build)
    await collector.run()
    await collector.run()

    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM snapshots") == 2
        assert await connection.fetchval("SELECT count(*) FROM daily") == 1
        assert await connection.fetchval("SELECT count(*) FROM runs WHERE ok") == 2

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "GET", "/api/data", headers={"Accept-Encoding": "gzip"}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-encoding"] == "gzip"
            compressed = b"".join([chunk async for chunk in response.aiter_raw()])
            assert json.loads(gzip.decompress(compressed)) == {"count": 2}
            etag = response.headers["etag"]

        not_modified = await client.get(
            "/api/data",
            headers={"If-None-Match": etag, "Accept-Encoding": "gzip"},
        )
        assert not_modified.status_code == 304
        assert not_modified.headers["etag"] == etag

    async with pool.acquire() as connection:
        await connection.execute("TRUNCATE snapshots, daily, runs RESTART IDENTITY")
    await db.close_pool()
