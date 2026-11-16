"""Historical spend from Wayback Machine copies of openrouter.ai/rankings.

Until Aug 2025 the rankings page embedded per-model dollar spend ("volume") in
its server-rendered data:
- Nov 2023 to Jan 2025: a weekly chart of every model's spend since May 2023.
- Feb to mid-Aug 2025: leaderboard totals for the trailing 7 days. Later copies
  only carry sampled rows, which are skipped.
`build` turns a set of snapshots into one row per (week, model). The result is
committed as archive_weekly.json.gz so the app never calls the archive at runtime.

    python -m modelshare.archive [cache_dir] [--cached]   # download (or reuse), parse, write
"""

from __future__ import annotations

import contextlib
import gzip
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

ARCHIVE_FILE = Path(__file__).with_name("archive_weekly.json.gz")
CDX = "https://web.archive.org/cdx/search/cdx"
PAGE = "https://openrouter.ai/rankings"
HEADERS = {"User-Agent": "ModelShare/1.0 (+https://github.com/JaredSleeper/ModelShare)"}
FIRST_TS, LAST_TS = "20231101", "20251101"

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_LINE = re.compile(r"^([0-9a-f]+):([\[{].*)$")


def rsc_objects(html: str) -> list:
    """Decoded JSON rows of a Next.js page's inline RSC stream."""
    text = "".join(json.loads(m.group(1)) for m in _PUSH.finditer(html))
    out = []
    for line in text.split("\n"):
        m = _LINE.match(line)
        if m:
            with contextlib.suppress(ValueError):
                out.append(json.loads(m.group(2)))
    return out


def _arrays(obj, path: str = ""):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and "volume" in obj[0] and "date" in obj[0]:
            yield path, obj
            return
        for x in obj:
            yield from _arrays(x, path)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _arrays(v, f"{path}.{k}")


def _split(model: str, variant: str | None) -> tuple[str, str]:
    slug, _, suffix = model.partition(":")
    return slug, variant or suffix or "standard"


def _row(r: dict) -> dict:
    slug, variant = _split(r.get("model_permaslug") or r.get("model") or "", r.get("variant"))
    return {
        "permaslug": slug,
        "variant": variant,
        "spend_usd": float(r.get("volume") or 0),
        "prompt": int(r.get("total_prompt_tokens") or 0),
        "completion": int(r.get("total_completion_tokens") or 0),
        "requests": int(r.get("count") or 0),
    }


def parse_snapshot(html: str) -> dict:
    """{"weekly": {week: [rows]}, "window": [rows] | None} from one archived page."""
    weekly: dict[str, list[dict]] = {}
    windows: list[tuple[str, list[dict]]] = []
    for obj in rsc_objects(html):
        for path, arr in _arrays(obj):
            if path.endswith((".data", ".weekly")):
                for r in arr:
                    if r.get("model"):
                        weekly.setdefault(r["date"][:10], []).append(_row(r))
            # From late Aug 2025 the page only has sampled rows whose volume
            # no longer matches their tokens.
            elif path.endswith((".rankMap.week", ".rankingData")) and not any(
                r.get("sample_rate") is not None for r in arr
            ):
                windows.append((path, arr))
    window = None
    if windows:
        # Pages with several 7-day lists (top table, category tabs) carry the full
        # leaderboard as the largest one.
        _, arr = max(windows, key=lambda w: sum(float(r.get("volume") or 0) for r in w[1]))
        window = [_row(r) for r in arr if r.get("model_permaslug") or r.get("model")]
    return {"weekly": weekly, "window": window}


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _merge(rows: list[dict]) -> list[dict]:
    out: dict[tuple, dict] = {}
    for r in rows:
        k = (r["permaslug"], r["variant"])
        if k in out:
            for f in ("spend_usd", "prompt", "completion", "requests"):
                out[k][f] += r[f]
        else:
            out[k] = dict(r)
    return list(out.values())


def build(snapshots: dict[str, dict]) -> list[dict]:
    """One row per (week, model). `snapshots` maps Wayback timestamp -> parse_snapshot()."""
    chart: dict[str, tuple[str, list[dict]]] = {}
    trailing: dict[str, tuple[int, str, list[dict]]] = {}
    for ts in sorted(snapshots):
        taken = date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
        snap = snapshots[ts]
        for week, rows in snap["weekly"].items():
            start = date.fromisoformat(week)
            # Weekly chart points are Mondays; the newest one is a partial week.
            if start.weekday() == 0 and taken >= start + timedelta(days=7):
                chart[week] = (ts, rows)
        if snap["window"]:
            # Trailing 7 days ending yesterday, filed under the Monday it is closest to.
            start = taken - timedelta(days=7)
            week = _monday(start + timedelta(days=3))
            gap = abs((start - week).days)
            key = week.isoformat()
            if key not in trailing or gap < trailing[key][0]:
                trailing[key] = (gap, ts, snap["window"])

    out = []
    for week in sorted(set(chart) | set(trailing)):
        if week in chart:
            ts, rows, source = chart[week][0], chart[week][1], "weekly"
        else:
            _, ts, rows = trailing[week]
            source = "trailing7"
        for r in _merge(rows):
            out.append({"week": week, **r, "source": source, "snapshot": ts})
    return out


def load(path: Path = ARCHIVE_FILE) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(gzip.decompress(path.read_bytes()))


def _snapshot_list(client: httpx.Client) -> list[str]:
    r = client.get(
        CDX,
        params={
            "url": "openrouter.ai/rankings",
            "output": "json",
            "fl": "timestamp",
            "filter": "statuscode:200",
            "collapse": "timestamp:8",
            "from": FIRST_TS,
            "to": LAST_TS,
        },
        timeout=120,
    )
    r.raise_for_status()
    return [row[0] for row in r.json()[1:]]


def _fetch(client: httpx.Client, ts: str, cache: Path) -> str:
    f = cache / f"{ts}.txt"
    if f.exists():
        return f.read_text()
    for attempt in range(4):
        try:
            r = client.get(f"https://web.archive.org/web/{ts}id_/{PAGE}", timeout=120)
            r.raise_for_status()
            # Raw (id_) captures keep the original gzip body without its header.
            try:
                html = gzip.decompress(r.content).decode(errors="ignore")
            except OSError:
                html = r.content.decode(errors="ignore")
            f.write_text(html)
            time.sleep(1.5)
            return html
        except httpx.HTTPError:
            time.sleep(10 * (attempt + 1))
    return ""


def main(cache_dir: str, cached_only: bool = False) -> None:
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    snapshots = {}
    if cached_only:
        for f in sorted(cache.glob("*.txt")):
            if FIRST_TS <= f.stem < LAST_TS:
                snapshots[f.stem] = parse_snapshot(f.read_text())
    else:
        with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
            for ts in _snapshot_list(client):
                html = _fetch(client, ts, cache)
                if html:
                    snapshots[ts] = parse_snapshot(html)
    rows = build(snapshots)
    body = json.dumps(rows, separators=(",", ":")).encode()
    ARCHIVE_FILE.write_bytes(gzip.compress(body, mtime=0))
    weeks = sorted({r["week"] for r in rows})
    print(
        f"{len(snapshots)} snapshots, {len(rows)} rows, {len(weeks)} weeks", weeks[:1], weeks[-1:]
    )


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--cached"]
    main(args[0] if args else "data/wayback", cached_only="--cached" in sys.argv)
