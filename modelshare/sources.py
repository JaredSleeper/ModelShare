"""Fetch OpenRouter's public rankings feeds into one JSON-serialisable bundle."""

import asyncio
import logging
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

BASE = "https://openrouter.ai"
RANKINGS = f"{BASE}/api/frontend/v1/rankings"
ACTIVITY = f"{BASE}/api/frontend/v1/stats/model-activity"
HEADERS = {"User-Agent": "ModelShare/1.0 (+https://github.com/JaredSleeper/ModelShare)"}

RANKING_FEEDS = [
    "author-market-share",
    "model-rankings-chart",
    "task-spend",
    "apps",
    "open-closed",
    "session-cost",
]

CATALOG_FIELDS = (
    "slug",
    "permaslug",
    "name",
    "short_name",
    "author",
    "author_display_name",
    "created_at",
    "group",
    "hidden",
)
ACTIVITY_FIELDS = (
    "date",
    "model_permaslug",
    "variant",
    "total_prompt_tokens",
    "total_completion_tokens",
    "total_native_tokens_cached",
    "total_native_tokens_reasoning",
    "total_byok_prompt_tokens",
    "total_byok_completion_tokens",
    "count",
)
RANKED_FIELDS = (
    "date",
    "model_permaslug",
    "variant",
    "total_prompt_tokens",
    "total_completion_tokens",
    "count",
    "rankingMetricValue",
)
PRICE_FIELDS = ("prompt", "completion", "input_cache_read")


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None):
    last = None
    for attempt in range(4):
        try:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            last = repr(e)
        await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url} {params or ''}: {last}")


def _slim_catalog(rows: list[dict]) -> list[dict]:
    out = []
    for c in rows:
        p = (c.get("endpoint") or {}).get("pricing") or {}
        item = {k: c.get(k) for k in CATALOG_FIELDS}
        item["pricing"] = {k: p.get(k) for k in PRICE_FIELDS} if p else None
        out.append(item)
    return out


async def fetch_bundle(concurrency: int = 6) -> dict:
    """Everything the dashboard needs, as plain JSON.

    activity covers every model with spend in the last 30 days
    (daily input/output/cached tokens for the trailing 31 days).
    """
    started = datetime.now(UTC)
    errors: list[str] = []
    limits = httpx.Limits(max_connections=concurrency)
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, limits=limits) as c:
        models, catalog = await asyncio.gather(
            _get(c, f"{BASE}/api/v1/models"),
            _get(c, f"{BASE}/api/frontend/v1/catalog/models"),
        )
        spend = {}
        for view in ("day", "week", "month"):
            d = await _get(c, f"{RANKINGS}/models", {"view": view, "metric": "spend"})
            spend[view] = [{k: r.get(k) for k in RANKED_FIELDS} for r in d["data"]]
        feeds = {}
        for name in RANKING_FEEDS:
            try:
                feeds[name] = (await _get(c, f"{RANKINGS}/{name}"))["data"]
            except RuntimeError as e:
                errors.append(str(e))
                feeds[name] = None

        slugs = sorted({r["model_permaslug"] for r in spend["month"] if r["model_permaslug"]})
        sem = asyncio.Semaphore(concurrency)

        async def one(slug):
            async with sem:
                try:
                    d = await _get(c, ACTIVITY, {"permaslug": slug})
                except RuntimeError as e:
                    errors.append(str(e))
                    return slug, None
            rows = (d.get("data") or {}).get("analytics") or []
            return slug, [{k: r.get(k) for k in ACTIVITY_FIELDS} for r in rows]

        activity = dict(await asyncio.gather(*(one(s) for s in slugs)))

    return {
        "fetched_at": started.isoformat(),
        "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        "errors": errors,
        "models": [
            {
                "id": m["id"],
                "canonical_slug": m.get("canonical_slug"),
                "name": m.get("name"),
                "created": m.get("created"),
                "pricing": {k: (m.get("pricing") or {}).get(k) for k in PRICE_FIELDS},
            }
            for m in models["data"]
        ],
        "catalog": _slim_catalog(catalog["data"]),
        "spend": spend,
        "feeds": feeds,
        "activity": activity,
    }


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO)
    b = asyncio.run(fetch_bundle())
    out = sys.argv[1] if len(sys.argv) > 1 else "bundle.json"
    with open(out, "w") as f:
        json.dump(b, f)
    print(out, b["seconds"], "s", len(b["activity"]), "models", len(b["errors"]), "errors")
