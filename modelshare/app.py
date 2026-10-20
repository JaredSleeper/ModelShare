from __future__ import annotations

import asyncio
import csv
import hashlib
import html
import io
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from modelshare import collector, db

STATIC_DIR = Path(__file__).resolve().parent / "static"
_ASSET_RE = re.compile(r"(/static/(?:app\.js|app\.css))(?:\?[^\"'\s]*)?")
_BASIC = HTTPBasic(auto_error=False)
_BASIC_CREDENTIALS = Depends(_BASIC)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.open_pool()
    try:
        try:
            await collector.restore_cache()
        except Exception:
            logging.getLogger(__name__).exception("Unable to restore the cached payload")
        task = asyncio.create_task(collector.loop(), name="modelshare-collector")
        yield
    finally:
        if "task" in locals():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await db.close_pool()


app = FastAPI(title="ModelShare", lifespan=lifespan)


class VersionedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        query = scope.get("query_string", b"").decode("latin-1")
        if re.search(r"(?:^|&)v=[0-9a-f]{8,40}(?:&|$)", query):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", VersionedStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=503, detail="Frontend is not available yet")
    content = index_path.read_text()
    for asset in ("app.js", "app.css"):
        asset_path = STATIC_DIR / asset
        if asset_path.is_file():
            version = hashlib.sha1(asset_path.read_bytes()).hexdigest()[:12]
            content = _ASSET_RE.sub(
                lambda match, current_asset=asset, current_version=version: (
                    f"{match.group(1)}?v={current_version}"
                    if match.group(1).endswith(current_asset)
                    else match.group(0)
                ),
                content,
            )
    return HTMLResponse(content)


def _accepts_gzip(header: str) -> bool:
    for item in header.lower().split(","):
        encoding, *params = item.strip().split(";")
        if encoding.strip() not in {"gzip", "*"}:
            continue
        quality = next((part.strip()[2:] for part in params if part.strip().startswith("q=")), "1")
        try:
            return float(quality) > 0
        except ValueError:
            return False
    return False


def _etag_matches(header: str, etag: str) -> bool:
    candidates = {part.strip() for part in header.split(",")}
    return "*" in candidates or f'"{etag}"' in candidates or f'W/"{etag}"' in candidates


@app.get("/api/data")
async def api_data(request: Request) -> Response:
    cached = collector.get_cache()
    if cached is None:
        return JSONResponse({"detail": "Data is not available yet"}, status_code=503)
    headers = {
        "ETag": f'"{cached.etag}"',
        "Cache-Control": "public, max-age=300",
        "Vary": "Accept-Encoding",
    }
    if _etag_matches(request.headers.get("if-none-match", ""), cached.etag):
        return Response(status_code=304, headers=headers)
    if _accepts_gzip(request.headers.get("accept-encoding", "")):
        headers["Content-Encoding"] = "gzip"
        return Response(cached.gzip_bytes, media_type="application/json", headers=headers)
    return Response(cached.json_bytes, media_type="application/json", headers=headers)


@app.get("/api/export.csv")
async def export_csv() -> Response:
    rows = await db.get_pool().fetch(
        """
        SELECT date, author, permaslug, variant, prompt, completion, cached,
               requests, est_usd, spend_usd
        FROM daily
        ORDER BY date, author, permaslug, variant
        """
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "date",
            "author",
            "permaslug",
            "variant",
            "prompt",
            "completion",
            "cached",
            "requests",
            "est_usd",
            "spend_usd",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["date"].isoformat(),
                row["author"],
                row["permaslug"],
                row["variant"],
                row["prompt"],
                row["completion"],
                row["cached"],
                row["requests"],
                row["est_usd"],
                row["spend_usd"],
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="modelshare-daily.csv"'},
    )


@app.get("/healthz")
async def healthz() -> Response:
    try:
        await db.get_pool().fetchval("SELECT 1")
    except (asyncpg.PostgresError, RuntimeError):
        return JSONResponse({"ok": False}, status_code=503)
    return JSONResponse({"ok": True})


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "Not scheduled"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


@app.get("/status", response_class=HTMLResponse)
async def status() -> HTMLResponse:
    pool = db.get_pool()
    async with pool.acquire() as connection:
        runs = await connection.fetch(
            """
            SELECT started_at, ok, seconds, n_models, n_rows, build_ms, error
            FROM runs ORDER BY started_at DESC, id DESC LIMIT 30
            """
        )
        snapshot_count = await connection.fetchval("SELECT count(*) FROM snapshots")
        daily_stats = await connection.fetchrow(
            "SELECT count(*) AS count, min(date) AS first_date, max(date) AS last_date FROM daily"
        )
    cache = collector.get_cache()
    raw_size = len(cache.json_bytes) if cache else 0
    gzip_size = len(cache.gzip_bytes) if cache else 0
    password_set = bool(os.environ.get("ADMIN_PASSWORD"))

    run_rows = []
    for row in runs:
        ok = "Yes" if row["ok"] is True else "No" if row["ok"] is False else "Running"
        run_rows.append(
            "<tr>"
            f"<td>{html.escape(_format_time(row['started_at']))}</td>"
            f"<td>{ok}</td>"
            f"<td>{html.escape(str(row['seconds'] or ''))}</td>"
            f"<td>{html.escape(str(row['n_models'] or ''))}</td>"
            f"<td>{html.escape(str(row['n_rows'] or ''))}</td>"
            f"<td>{html.escape(str(row['build_ms'] or ''))}</td>"
            f"<td>{html.escape(row['error'] or '')}</td>"
            "</tr>"
        )
    runs_html = "".join(run_rows) or '<tr><td colspan="7">No runs recorded</td></tr>'
    first_date = daily_stats["first_date"].isoformat() if daily_stats["first_date"] else "None"
    last_date = daily_stats["last_date"].isoformat() if daily_stats["last_date"] else "None"
    disabled = " disabled" if not password_set else ""
    password_note = (
        "" if password_set else "<p>Manual collection is disabled: ADMIN_PASSWORD is unset.</p>"
    )
    next_run = _format_time(collector.get_next_run_at())
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ModelShare status</title>
<style>
body{{font:14px system-ui,sans-serif;margin:24px;color:#202124}}h1{{font-size:22px}}
table{{border-collapse:collapse;width:100%;margin:16px 0 28px}}th,td{{border:1px solid #ccc;
padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#f3f3f3}}
.metrics{{width:auto}}.metrics th{{min-width:170px}}
</style></head><body><h1>ModelShare status</h1>
<table class="metrics"><tbody>
<tr><th>Stored snapshots</th><td>{snapshot_count}</td></tr>
<tr><th>Daily rows</th><td>{daily_stats["count"]}</td></tr>
<tr><th>Daily date range</th><td>{first_date} to {last_date}</td></tr>
<tr><th>Payload size, raw</th><td>{raw_size:,} bytes</td></tr>
<tr><th>Payload size, gzip</th><td>{gzip_size:,} bytes</td></tr>
<tr><th>Collector next run</th><td>{html.escape(next_run)}</td></tr>
</tbody></table>
<form method="post" action="/status/run">
<button type="submit"{disabled}>Run now</button></form>{password_note}
<h2>Latest runs</h2><table><thead><tr>
<th>Time</th><th>OK</th><th>Seconds</th><th>Models</th><th>Rows</th><th>Build ms</th><th>Error</th>
</tr></thead><tbody>{runs_html}</tbody></table>
</body></html>"""
    return HTMLResponse(body)


@app.post("/status/run")
async def run_now(
    credentials: HTTPBasicCredentials | None = _BASIC_CREDENTIALS,
) -> Response:
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        raise HTTPException(status_code=403, detail="Manual collection is disabled")
    if (
        credentials is None
        or not secrets.compare_digest(credentials.username, "admin")
        or not secrets.compare_digest(credentials.password, password)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    await collector.run()
    return RedirectResponse("/status", status_code=303)
