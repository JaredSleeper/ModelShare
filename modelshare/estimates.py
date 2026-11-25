"""Estimated weekly spend by lab for weeks with no reported dollars.

OpenRouter's rankings page stopped carrying dollar spend after mid-Aug 2025, and
this site's own daily data starts in Sep 2026. For those weeks (and the odd
archive week with no copy) spend is estimated as lab tokens x lab $/token:
- Lab tokens: OpenRouter's weekly token chart by maker, from archived copies of
  the rankings page and from the live feed (last 53 weeks).
- Lab $/token: reported spend / tokens in archived weeks; list-priced tokens of
  every model in the page's 7-day leaderboard (Aug 2025 to May 2026), scaled by
  each lab's reported / list-price ratio from mid-2025 copies that carry both;
  and this site's own estimates once full weeks are saved. Weeks between those anchors
  get a straight-line blend of the two nearest.
The result is committed as archive_estimates.json.gz.

    python -m modelshare.estimates PAGES_DIR PRICES_DIR LIVE_PAYLOAD_JSON
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from modelshare import archive, compute

FIRST_WEEK = "2024-09-30"
MIN_PRICED = 0.5
MIN_CALIBRATION_USD = 100_000


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _taken(ts: str) -> date:
    return date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))


def _charts(obj):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and "ys" in obj[0] and "x" in obj[0]:
            yield obj
            return
        for x in obj:
            yield from _charts(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _charts(v)


def _token_tables(obj):
    if isinstance(obj, list):
        first = obj[0] if obj and isinstance(obj[0], dict) else {}
        if "total_prompt_tokens" in first and "date" in first:
            yield obj
            return
        for x in obj:
            yield from _token_tables(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _token_tables(v)


def lab_chart(html: str, taken: date) -> dict[str, dict[str, int]]:
    """{monday: {lab: tokens}} from the page's weekly tokens-by-maker chart."""
    out: dict[str, dict[str, int]] = {}
    for obj in archive.rsc_objects(html):
        for chart in _charts(obj):
            keys = chart[-1]["ys"]
            if any("/" in k for k in keys) or not {"anthropic", "openai", "google"} & set(keys):
                continue
            for p in chart:
                # Maker chart weeks are labelled by the Sunday before the Monday.
                start = _monday(date.fromisoformat(p["x"][:10]) + timedelta(days=1))
                if taken < start + timedelta(days=7):
                    continue
                labs: dict[str, int] = defaultdict(int)
                for k, v in p["ys"].items():
                    lab = "other" if k.lower() == "others" else compute.lab_of(k.lower())
                    labs[lab] += int(v or 0)
                out[start.isoformat()] = dict(labs)
    return out


def leaderboard(html: str) -> list[dict]:
    """Full 7-day token leaderboard (no dollars), largest table on the page."""
    best: list[dict] = []
    for obj in archive.rsc_objects(html):
        for t in _token_tables(obj):
            if any(r.get("volume") is not None or r.get("sample_rate") is not None for r in t):
                continue
            if len(t) > len(best):
                best = t
    return best


class PriceBook:
    """OpenRouter list prices from archived copies of /api/v1/models."""

    def __init__(self, catalogs: dict[str, list[dict]]):
        self.by_slug: dict[str, list[tuple[date, dict]]] = defaultdict(list)
        for ts in sorted(catalogs):
            base = {m.get("canonical_slug") for m in catalogs[ts] if ":" not in m.get("id", "")}
            for m in catalogs[ts]:
                # Variants (":free", ":batch") share the base model's canonical slug.
                keys = {m.get("id")}
                variant = ":" in m.get("id", "")
                if not m.get("id", "").endswith(":free") and (
                    not variant or m.get("canonical_slug") not in base
                ):
                    keys.add(m.get("canonical_slug"))
                for k in keys - {None}:
                    self.by_slug[k].append((_taken(ts), m.get("pricing") or {}))

    def get(self, slug: str, when: date) -> dict | None:
        hits = self.by_slug.get(slug)
        if not hits:
            return None
        return min(hits, key=lambda h: abs((h[0] - when).days))[1]


def priced_rates(rows: list[dict], prices: PriceBook, when: date) -> dict[str, float]:
    """$/token by lab from one leaderboard, for labs whose tokens are mostly priced."""
    cost: dict[str, float] = defaultdict(float)
    priced: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for r in rows:
        name = r.get("model_permaslug") or r.get("model") or ""
        slug, variant = archive._split(name, r.get("variant"))
        lab = compute.lab_of(compute.author_of(slug))
        t = int(r.get("total_prompt_tokens") or 0) + int(r.get("total_completion_tokens") or 0)
        total[lab] += t
        p = {"prompt": 0, "completion": 0} if variant == "free" else prices.get(slug, when)
        if p is None:
            continue
        priced[lab] += t
        cost[lab] += compute.list_cost(
            {
                "prompt": int(r.get("total_prompt_tokens") or 0),
                "completion": int(r.get("total_completion_tokens") or 0),
                "cached": int(r.get("total_native_tokens_cached") or 0),
            },
            p,
        )
    return {
        lab: cost[lab] / priced[lab]
        for lab in total
        if priced[lab] and priced[lab] >= MIN_PRICED * total[lab]
    }


def calibration(pages: dict[str, str], prices: PriceBook) -> dict[str, float]:
    """Reported spend / list-priced tokens by lab, from copies whose 7-day table
    has both (mid-2025). List prices overstate what OpenRouter reports, most of
    all for Anthropic, so priced estimates are scaled by this. Key "" is all labs."""
    rep: dict[str, float] = defaultdict(float)
    lst: dict[str, float] = defaultdict(float)
    for ts, html in pages.items():
        tables = [
            arr
            for obj in archive.rsc_objects(html)
            for path, arr in archive._arrays(obj)
            if path.endswith(".rankingData")
            and not any(r.get("sample_rate") is not None for r in arr)
        ]
        if not tables:
            continue
        for r in max(tables, key=len):
            name = r.get("model_permaslug") or r.get("model") or ""
            slug, variant = archive._split(name, r.get("variant"))
            p = (
                {"prompt": 0, "completion": 0}
                if variant == "free"
                else prices.get(slug, _taken(ts))
            )
            if p is None:
                continue
            row = {
                "prompt": int(r.get("total_prompt_tokens") or 0),
                "completion": int(r.get("total_completion_tokens") or 0),
                "cached": int(r.get("total_native_tokens_cached") or 0),
            }
            for lab in ("", compute.lab_of(compute.author_of(slug))):
                rep[lab] += float(r.get("volume") or 0)
                lst[lab] += compute.list_cost(row, p)
    out = {"": rep[""] / lst[""]} if lst[""] else {"": 1.0}
    for lab in rep:
        k = rep[lab] / lst[lab] if lst[lab] else 0
        if lab and rep[lab] >= MIN_CALIBRATION_USD and 0.2 <= k <= 5:
            out[lab] = k
    return out


def _blend(anchors: list[tuple[date, float]], when: date) -> float:
    before = [a for a in anchors if a[0] <= when]
    after = [a for a in anchors if a[0] >= when]
    if not before:
        return after[0][1]
    if not after:
        return before[-1][1]
    (d0, r0), (d1, r1) = before[-1], after[0]
    if d0 == d1:
        return r0
    f = (when - d0).days / (d1 - d0).days
    return r0 + f * (r1 - r0)


def build(
    reported: list[dict],
    charts: dict[str, dict[str, int]],
    boards: dict[str, list[dict]],
    prices: PriceBook,
    site: dict,
    last_week: str,
    scale: dict[str, float] | None = None,
) -> list[dict]:
    """Rows {week, lab, tokens, spend_usd, basis} for weeks without reported spend,
    from FIRST_WEEK up to and including `last_week`."""
    anchors: dict[str, dict[str, float]] = defaultdict(dict)
    rep_usd: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    rep_tok: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in reported:
        lab = compute.lab_of(compute.author_of(r["permaslug"]))
        rep_usd[r["week"]][lab] += float(r["spend_usd"] or 0)
        rep_tok[r["week"]][lab] += int(r["prompt"]) + int(r["completion"])
    # Rates use the chart's token counts where there is one, so they match the
    # tokens they are applied to (row-level counts can include cached input).
    for wk in rep_usd:
        for lab, t in (charts.get(wk) or rep_tok[wk]).items():
            if t and lab in rep_usd[wk]:
                anchors[lab][wk] = rep_usd[wk][lab] / t

    priced_weeks = set()
    for ts, rows in boards.items():
        taken = _taken(ts)
        wk = _monday(taken - timedelta(days=4)).isoformat()
        for lab, rate in priced_rates(rows, prices, taken).items():
            anchors[lab][wk] = rate * (scale or {}).get(lab, (scale or {}).get("", 1.0))
        priced_weeks.add(wk)

    for i, wk in enumerate(site.get("weeks") or []):
        if (site.get("source") or [None] * (i + 1))[i] != "live":
            continue
        for lab, s in site["labs"].items():
            t, usd = (charts.get(wk) or {}).get(lab, s["tokens"][i]), s["spend"][i]
            if t and usd is not None:
                anchors[lab][wk] = usd / t

    series = {
        lab: sorted((date.fromisoformat(w), r) for w, r in a.items()) for lab, a in anchors.items()
    }
    everyone = sorted((d, r) for a in series.values() for d, r in a)
    out = []
    for wk in sorted(charts):
        if wk < FIRST_WEEK or wk > last_week or wk in rep_usd:
            continue
        when = date.fromisoformat(wk)
        basis = "priced tokens" if wk in priced_weeks else "interpolated price"
        for lab, t in sorted(charts[wk].items()):
            if not t:
                continue
            rate = _blend(series.get(lab) or everyone, when)
            usd = round(t * rate, 2)
            out.append({"week": wk, "lab": lab, "tokens": t, "spend_usd": usd, "basis": basis})
    return out


def main(pages_dir: str, prices_dir: str, live_payload: str) -> None:
    charts: dict[str, dict[str, int]] = {}
    boards: dict[str, list[dict]] = {}
    overlap: dict[str, str] = {}
    for f in sorted(Path(pages_dir).glob("*.txt")):
        if not f.stem.isdigit() or f.stem < "20250601":
            continue
        html = f.read_text()
        if f.stem < "20250818":
            overlap[f.stem] = html
            continue
        charts.update(lab_chart(html, _taken(f.stem)))
        board = leaderboard(html)
        if len(board) >= 100:
            boards[f.stem] = board
    live = json.loads(Path(live_payload).read_text())
    wl = live["weekly_labs"]
    # The live feed is the full maker list; it wins over archived copies.
    for i, wk in enumerate(wl["weeks"][:-1]):
        charts[wk[:10]] = {lab: s[i] for lab, s in wl["series"].items() if s[i]}
    catalogs = {f.stem: json.loads(f.read_text())["data"] for f in Path(prices_dir).glob("*.json")}
    site = live["history"]
    first_live = min(
        (w for w, s in zip(site["weeks"], site["source"], strict=True) if s == "live"),
        default="9999",
    )
    last_week = (date.fromisoformat(first_live) - timedelta(days=7)).isoformat()
    prices = PriceBook(catalogs)
    scale = calibration(overlap, prices)
    print("reported / list price:", {k or "all": round(v, 2) for k, v in scale.items()})
    rows = build(archive.load(), charts, boards, prices, site, last_week, scale)
    body = json.dumps(rows, separators=(",", ":")).encode()
    archive.ESTIMATE_FILE.write_bytes(gzip.compress(body, mtime=0))
    weeks = sorted({r["week"] for r in rows})
    print(
        f"{len(boards)} leaderboards, {len(rows)} rows, {len(weeks)} weeks", weeks[:1], weeks[-1:]
    )


if __name__ == "__main__":
    main(*sys.argv[1:4])
