from __future__ import annotations

import json
from datetime import date

import pytest

from modelshare import archive, compute, estimates


def _page(payload: dict) -> str:
    line = "1a:" + json.dumps(["$", "$L1d", None, payload]) + "\n"
    return f"<script>self.__next_f.push([1,{json.dumps(line)}])</script>"


def _tok(model, prompt, completion=0, variant="standard"):
    return {
        "date": "2025-11-16 00:00:00",
        "model_permaslug": model,
        "variant": variant,
        "total_prompt_tokens": prompt,
        "total_completion_tokens": completion,
        "total_native_tokens_cached": 0,
    }


def test_lab_chart_moves_sunday_weeks_to_monday_and_drops_partial_week():
    chart = [
        {"x": "2025-11-02", "ys": {"anthropic": 10, "meta-llama": 3, "others": 1}},
        {"x": "2025-11-09", "ys": {"anthropic": 99, "google": 1}},
    ]
    models = [{"x": "2025-11-03", "ys": {"openai/gpt-5": 5}}]
    html = _page({"a": {"data": chart}, "b": {"data": models}})
    assert estimates.lab_chart(html, date(2025, 11, 12)) == {
        "2025-11-03": {"anthropic": 10, "meta": 3, "other": 1}
    }


def test_leaderboard_skips_dollar_and_sampled_tables():
    full = [_tok(f"x/m{i}", 1) for i in range(3)]
    sampled = [{**_tok("x/a", 1), "sample_rate": 0.0025} for _ in range(5)]
    dollars = [{**_tok("x/b", 1), "volume": 1.0} for _ in range(5)]
    html = _page({"rankingData": full, "s": {"rankingData": sampled}, "d": {"x": dollars}})
    assert estimates.leaderboard(html) == full


def test_priced_rates_counts_free_variants_and_skips_mostly_unpriced_labs():
    book = estimates.PriceBook(
        {"20251101000000": [{"id": "anthropic/claude", "pricing": {"prompt": "0.00001"}}]}
    )
    rows = [
        _tok("anthropic/claude", 300),
        _tok("anthropic/claude", 100, variant="free"),
        _tok("google/unknown", 100),
    ]
    rates = estimates.priced_rates(rows, book, date(2025, 11, 17))
    assert rates == {"anthropic": 300 * 0.00001 / 400}


def test_build_blends_rates_between_anchors():
    reported = [
        {
            "week": "2025-08-04",
            "permaslug": "anthropic/c",
            "spend_usd": 10.0,
            "prompt": 10,
            "completion": 0,
        }
    ]
    site = {
        "weeks": ["2025-08-18"],
        "source": ["live"],
        "labs": {"anthropic": {"spend": [30], "tokens": [10]}},
    }
    charts = {"2025-08-04": {"anthropic": 10}, "2025-08-11": {"anthropic": 100}}
    rows = estimates.build(reported, charts, {}, estimates.PriceBook({}), site, "2025-08-11")
    assert rows == [
        {
            "week": "2025-08-11",
            "lab": "anthropic",
            "tokens": 100,
            "spend_usd": 200.0,
            "basis": "interpolated price",
        }
    ]


def test_t30d_spreads_weeks_and_switches_to_live_days():
    archived = [{"week": "2025-01-06", "permaslug": "openai/gpt-4o", "spend_usd": 70.0}]
    est = [{"week": "2025-01-13", "lab": "anthropic", "spend_usd": 140.0}]
    live = [
        {"date": f"2025-01-{d}", "author": "google", "permaslug": "google/g", "spend_usd": 5.0}
        for d in range(18, 21)
    ]
    out = compute.t30d(archived, est, live, days=30, window=7)
    assert [d["date"] for d in out] == [f"2025-01-{d}" for d in range(12, 21)]
    first, last = out[0], out[-1]
    assert first["basis"] == "reported" and first["labs"] == {"openai": 70.0}
    assert last["basis"] == "estimated+site"
    assert round(last["labs"]["anthropic"]) == 80 and last["labs"]["google"] == 15.0


def test_estimate_file_covers_gap_until_live_data():
    rows = archive.load(archive.ESTIMATE_FILE)
    weeks = sorted({r["week"] for r in rows})
    assert "2025-08-18" in weeks and "2026-09-14" in weeks
    assert not {r["week"] for r in archive.load()} & set(weeks)


def test_calibration_is_reported_over_list_price_by_lab():
    book = estimates.PriceBook(
        {"20250701000000": [{"id": "anthropic/c", "pricing": {"prompt": "0.00001"}}]}
    )
    row = {
        "date": "2025-07-01",
        "model_permaslug": "anthropic/c",
        "variant": "standard",
        "volume": 250_000.0,
        "total_prompt_tokens": 50_000_000_000,
        "total_completion_tokens": 0,
    }
    html = _page({"rankingData": [row]})
    k = estimates.calibration({"20250701000000": html}, book)
    assert k == pytest.approx({"": 0.5, "anthropic": 0.5})


def test_price_book_ignores_free_variant_for_paid_slug():
    free = {"id": "x/m:free", "canonical_slug": "x/m-2025", "pricing": {"prompt": "0"}}
    paid = {"id": "x/m", "canonical_slug": "x/m-2025", "pricing": {"prompt": "0.000001"}}
    book = estimates.PriceBook({"20251101000000": [free, paid]})
    assert book.get("x/m-2025", date(2025, 11, 1)) == {"prompt": "0.000001"}
