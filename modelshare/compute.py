"""Turn a fetched bundle plus stored daily rows into the dashboard payload.

Spend method: each model's daily list-price cost (uncached input, cached input
and output tokens at OpenRouter's catalog prices) is scaled so the model's
30-day total matches OpenRouter's own reported 30-day spend share. Shares are
OpenRouter's; absolute dollars are list-price estimates.
"""

from collections import defaultdict
from datetime import date, datetime

LABS = {
    "anthropic": ("Anthropic", "#d97757"),
    "openai": ("OpenAI", "#10a37f"),
    "google": ("Google", "#4285f4"),
    "x-ai": ("xAI", "#2b2b2b"),
    "deepseek": ("DeepSeek", "#1f3a93"),
    "qwen": ("Alibaba Qwen", "#7c4dff"),
    "moonshotai": ("Moonshot", "#00a3c4"),
    "z-ai": ("Z.ai", "#e0457b"),
    "minimax": ("MiniMax", "#f2a33a"),
    "meta": ("Meta", "#8e6c8a"),
    "mistralai": ("Mistral", "#fa520f"),
    "xiaomi": ("Xiaomi", "#c9a227"),
    "tencent": ("Tencent", "#5f9ea0"),
    "nvidia": ("Nvidia", "#76b900"),
}
LAB_ALIASES = {
    "meta-llama": "meta",
    "alibaba": "qwen",
    "deepseek-ai": "deepseek",
    "text-embedding-3-large": "openai",
    "text-embedding-3-small": "openai",
}
OTHER = ("other", "Other", "#b8bcc4")
OTHER_PALETTE = ["#8d99ae", "#a5a58d", "#b5838d", "#6d6875", "#9a8c98", "#84a59d"]


def author_of(permaslug: str) -> str:
    if not permaslug:
        return ""
    head = permaslug.split("/", 1)[0].lower() if "/" in permaslug else permaslug.lower()
    return head


def lab_of(author: str) -> str:
    a = LAB_ALIASES.get(author, author)
    return a if a in LABS else "other"


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _d(s: str) -> str:
    return (s or "")[:10]


def _prices(bundle: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in bundle.get("models") or []:
        if m["id"].endswith(":free"):
            continue
        out[m.get("canonical_slug") or m["id"]] = m.get("pricing") or {}
    for c in bundle.get("catalog") or []:
        if c.get("pricing") and c.get("permaslug") and c["permaslug"] not in out:
            out[c["permaslug"]] = c["pricing"]
    return out


def list_cost(row: dict, price: dict) -> float:
    pp, cp = _f(price.get("prompt")), _f(price.get("completion"))
    cr = _f(price.get("input_cache_read")) if price.get("input_cache_read") else pp
    prompt = int(row.get("prompt") or 0)
    cached = min(int(row.get("cached") or 0), prompt)
    return (prompt - cached) * pp + cached * cr + int(row.get("completion") or 0) * cp


def _official(rows: list[dict]) -> tuple[dict[str, float], dict[str, int]]:
    share: dict[str, float] = defaultdict(float)
    tokens: dict[str, int] = defaultdict(int)
    for r in rows or []:
        s = r.get("model_permaslug")
        if not s:
            continue
        share[s] += _f(r.get("rankingMetricValue"))
        tokens[s] += int(r.get("total_prompt_tokens") or 0) + int(
            r.get("total_completion_tokens") or 0
        )
    return share, tokens


def _window(rows: list[dict]) -> tuple[str, str]:
    ds = sorted({_d(r.get("date")) for r in rows or [] if r.get("date")})
    return (ds[0], ds[-1]) if ds else ("", "")


def daily_rows(bundle: dict) -> list[dict]:
    """Calibrated per-model daily rows for the complete days in the activity window."""
    prices = _prices(bundle)
    today = _d(bundle.get("fetched_at"))
    m_share, _ = _official(bundle["spend"]["month"])
    m_start, m_end = _window(bundle["spend"]["month"])

    rows: list[dict] = []
    for slug, act in (bundle.get("activity") or {}).items():
        if not act:
            continue
        price = prices.get(slug) or {}
        author = author_of(slug)
        for a in act:
            d = _d(a.get("date"))
            if not d or d >= today:
                continue
            r = {
                "date": d,
                "permaslug": slug,
                "variant": a.get("variant") or "standard",
                "author": author,
                "prompt": int(a.get("total_prompt_tokens") or 0),
                "completion": int(a.get("total_completion_tokens") or 0),
                "cached": int(a.get("total_native_tokens_cached") or 0),
                "reasoning": int(a.get("total_native_tokens_reasoning") or 0),
                "requests": int(a.get("count") or 0),
            }
            r["est_usd"] = list_cost(r, price)
            r["spend_usd"] = r["est_usd"]
            rows.append(r)

    in_win = [r for r in rows if m_start <= r["date"] <= m_end]
    total_est = sum(r["est_usd"] for r in in_win)
    by_model_est: dict[str, float] = defaultdict(float)
    by_model_tok: dict[str, int] = defaultdict(int)
    for r in in_win:
        by_model_est[r["permaslug"]] += r["est_usd"]
        by_model_tok[r["permaslug"]] += r["prompt"] + r["completion"]
    if total_est <= 0:
        return rows

    for r in rows:
        s = r["permaslug"]
        target = m_share.get(s, 0.0) * total_est
        if by_model_est[s] > 0:
            r["spend_usd"] = r["est_usd"] * target / by_model_est[s]
        elif by_model_tok[s] > 0:
            r["spend_usd"] = target * (r["prompt"] + r["completion"]) / by_model_tok[s]
        else:
            r["spend_usd"] = 0.0
    return rows


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, (date, datetime)) else _d(str(d))


def build(bundle: dict, stored_daily: list[dict]) -> tuple[dict, list[dict]]:
    fresh = daily_rows(bundle)
    merged: dict[tuple, dict] = {}
    for row in stored_daily or []:
        r = dict(row, date=_iso(row["date"]))
        merged[(r["date"], r["permaslug"], r["variant"])] = r
    for r in fresh:
        merged[(r["date"], r["permaslug"], r["variant"])] = r
    return payload(bundle, list(merged.values())), fresh


def _catalog(bundle: dict) -> dict[str, dict]:
    out = {}
    for c in bundle.get("catalog") or []:
        if c.get("permaslug"):
            out[c["permaslug"]] = c
    return out


def _model_name(slug: str, cat: dict) -> str:
    c = cat.get(slug)
    if c and (c.get("short_name") or c.get("name")):
        return c.get("short_name") or c["name"]
    return slug.split("/", 1)[-1]


def payload(bundle: dict, rows: list[dict]) -> dict:
    cat = _catalog(bundle)
    prices = _prices(bundle)
    dates = sorted({r["date"] for r in rows})
    di = {d: i for i, d in enumerate(dates)}
    n = len(dates)

    lab_spend = defaultdict(lambda: [0.0] * n)
    lab_tok = defaultdict(lambda: [0] * n)
    model_spend = defaultdict(lambda: [0.0] * n)
    model_tok = defaultdict(lambda: [0] * n)
    for r in rows:
        i = di[r["date"]]
        lab = lab_of(r["author"] or author_of(r["permaslug"]))
        t = int(r["prompt"]) + int(r["completion"])
        lab_spend[lab][i] += r["spend_usd"] or 0.0
        lab_tok[lab][i] += t
        model_spend[r["permaslug"]][i] += r["spend_usd"] or 0.0
        model_tok[r["permaslug"]][i] += t

    off = {v: _official(bundle["spend"][v]) for v in ("day", "week", "month")}
    windows = {v: _window(bundle["spend"][v]) for v in ("day", "week", "month")}

    def lab_totals(share: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for s, v in share.items():
            out[lab_of(author_of(s))] += v
        return out

    lab_off = {v: lab_totals(off[v][0]) for v in off}
    lab_off_tok = {}
    for v in off:
        t: dict[str, float] = defaultdict(float)
        for s, x in off[v][1].items():
            t[lab_of(author_of(s))] += x
        tot = sum(t.values()) or 1
        lab_off_tok[v] = {k: x / tot for k, x in t.items()}

    def last(arr, k):
        return sum(arr[-k:]) if n else 0

    def prev(arr, k):
        return sum(arr[-2 * k : -k]) if n >= 2 * k else None

    tot_spend = [sum(lab_spend[lab][i] for lab in lab_spend) for i in range(n)]
    tot_s7, tot_p7 = last(tot_spend, 7), prev(tot_spend, 7)

    lab_ids = sorted(lab_spend, key=lambda k: -lab_off["month"].get(k, 0))
    lab_ids = [k for k in lab_ids if k != "other"] + (["other"] if "other" in lab_spend else [])
    for k in lab_off["month"]:
        if k not in lab_ids:
            lab_ids.append(k)

    models = []
    for s in set(model_spend) | set(off["month"][0]):
        sp = model_spend.get(s) or [0.0] * n
        tk = model_tok.get(s) or [0] * n
        c = cat.get(s) or {}
        p = prices.get(s) or {}
        tok30 = off["month"][1].get(s, 0)
        models.append(
            {
                "id": s,
                "name": _model_name(s, cat),
                "lab": lab_of(author_of(s)),
                "author": author_of(s),
                "created": _d(c.get("created_at")),
                "share_day": off["day"][0].get(s, 0.0),
                "share_week": off["week"][0].get(s, 0.0),
                "share_month": off["month"][0].get(s, 0.0),
                "tokens_month": tok30,
                "price_in": _f(p.get("prompt")) * 1e6,
                "price_out": _f(p.get("completion")) * 1e6,
                "spend": [round(x, 2) for x in sp],
                "tokens": tk,
            }
        )
    models.sort(key=lambda m: -m["share_month"])
    keep = [m for i, m in enumerate(models) if i < 300 or m["tokens_month"] > 0 and i < 400]

    labs = []
    pal = iter(OTHER_PALETTE)
    for k in lab_ids:
        name, color = LABS.get(k, OTHER[1:]) if k != "other" else OTHER[1:]
        sp, tk = lab_spend.get(k, [0.0] * n), lab_tok.get(k, [0] * n)
        s7, p7 = last(sp, 7), prev(sp, 7)
        top = next((m for m in models if m["lab"] == k), None)
        labs.append(
            {
                "id": k,
                "name": name,
                "color": color or next(pal, "#999"),
                "share_day": lab_off["day"].get(k, 0.0),
                "share_week": lab_off["week"].get(k, 0.0),
                "share_month": lab_off["month"].get(k, 0.0),
                "token_share_month": lab_off_tok["month"].get(k, 0.0),
                "token_share_week": lab_off_tok["week"].get(k, 0.0),
                "share_7d": s7 / tot_s7 if tot_s7 else 0,
                "share_prev_7d": (p7 / tot_p7) if (p7 is not None and tot_p7) else None,
                "spend_30d": sum(sp[-30:]),
                "tokens_30d": sum(tk[-30:]),
                "top_model": top["id"] if top else None,
                "n_models": sum(1 for m in models if m["lab"] == k and m["share_month"] > 0),
                "spend": [round(x, 2) for x in sp],
                "tokens": tk,
            }
        )

    feeds = bundle.get("feeds") or {}
    return {
        "fetched_at": bundle.get("fetched_at"),
        "windows": windows,
        "dates": dates,
        "labs": labs,
        "models": keep,
        "weekly_labs": _weekly(feeds.get("author-market-share"), by_lab=True),
        "weekly_models": _weekly(feeds.get("model-rankings-chart"), by_lab=False, cat=cat),
        "open_closed": feeds.get("open-closed") or [],
        "tasks": _tasks(feeds.get("task-spend")),
        "apps": _apps(feeds.get("apps")),
        "lab_meta": {k: {"name": v[0], "color": v[1]} for k, v in LABS.items()}
        | {"other": {"name": OTHER[1], "color": OTHER[2]}},
    }


def _weekly(feed, by_lab: bool, cat: dict | None = None) -> dict:
    data = (feed or {}).get("data") if isinstance(feed, dict) else feed
    data = data or []
    weeks = [w["x"] for w in data]
    series: dict[str, list[int]] = {}
    for i, w in enumerate(data):
        for k, v in w["ys"].items():
            if by_lab:
                key = "other" if k.lower() == "others" else lab_of(k.lower())
            else:
                key = "Others" if k == "Others" else k
            series.setdefault(key, [0] * len(data))
            series[key][i] += int(v or 0)
    out = {"weeks": weeks, "series": series}
    if not by_lab and cat is not None:
        out["names"] = {
            k: _model_name(k.split(":")[0], cat) + (" (free)" if k.endswith(":free") else "")
            for k in series
            if k != "Others"
        }
        out["labs"] = {k: lab_of(author_of(k)) for k in series if k != "Others"}
    return out


def _tasks(feed) -> dict:
    out = {}
    for metric in ("spend", "tokens"):
        f = (feed or {}).get(metric) or {}
        tasks = []
        for t in f.get("tasks") or []:
            shares: dict[str, float] = defaultdict(float)
            for m in t.get("models") or []:
                shares[lab_of(author_of(m["model"]))] += _f(m.get("share"))
            shares["other"] += max(0.0, 1 - sum(shares.values()))
            tasks.append(
                {
                    "tag": t["tag"],
                    "group": t.get("macroCategory"),
                    "share_of_total": _f(t.get("spendShareOfTotal") or t.get("shareOfTotal")),
                    "labs": dict(shares),
                    "top": [
                        {"model": m["model"], "share": m["share"], "delta_pp": m.get("deltaPp")}
                        for m in (t.get("models") or [])[:5]
                    ],
                }
            )
        out[metric] = {
            "window_days": f.get("windowDays"),
            "groups": f.get("macroCategories") or [],
            "tasks": tasks,
        }
    return out


def _apps(feed) -> list[dict]:
    rows = (feed or {}).get("month") or []
    out = []
    for r in rows[:25]:
        a = r.get("app") or {}
        out.append(
            {
                "title": a.get("title"),
                "url": a.get("origin_url") or a.get("main_url"),
                "categories": a.get("categories") or [],
                "tokens": int(r.get("total_tokens") or 0),
                "requests": int(r.get("total_requests") or 0),
            }
        )
    return out
