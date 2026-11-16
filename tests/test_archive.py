from __future__ import annotations

import json

from modelshare import archive, compute


def _page(payload: dict) -> str:
    line = "1a:" + json.dumps(["$", "$L1d", None, payload]) + "\n"
    return f"<script>self.__next_f.push([1,{json.dumps(line)}])</script>"


def _r(date, model, usd, variant=None):
    r = {
        "date": date,
        "model": model,
        "volume": usd,
        "total_prompt_tokens": 100,
        "total_completion_tokens": 10,
        "count": 1,
    }
    if variant:
        r["variant"] = variant
    return r


def test_parse_snapshot_reads_weekly_chart_and_window():
    html = _page(
        {
            "data": [
                _r("2024-04-22T00:00:00+00:00", "openai/gpt-4", 5.0),
                _r("2024-04-22T00:00:00+00:00", "openai/gpt-4:free", 0.0),
            ],
            "rankMap": {
                "week": [_r("2024-04-30", "anthropic/claude-3-opus", 7.5)],
                "day": [_r("2024-04-30", "x/y", 1.0)],
            },
        }
    )
    snap = archive.parse_snapshot(html)
    assert set(snap["weekly"]) == {"2024-04-22"}
    assert {(r["permaslug"], r["variant"]) for r in snap["weekly"]["2024-04-22"]} == {
        ("openai/gpt-4", "standard"),
        ("openai/gpt-4", "free"),
    }
    assert [r["permaslug"] for r in snap["window"]] == ["anthropic/claude-3-opus"]


def test_parse_snapshot_skips_sampled_rankings():
    sampled = {**_r("2025-08-20", "deepseek/deepseek-chat-v3.1", 0.6), "sample_rate": 0.0025}
    assert archive.parse_snapshot(_page({"rankingData": [sampled]}))["window"] is None


def test_parse_snapshot_reads_older_weekly_key():
    snap = archive.parse_snapshot(
        _page({"weekly": [_r("2023-05-08T00:00:00+00:00", "openai/gpt-3.5-turbo", 0.1)]})
    )
    assert list(snap["weekly"]) == ["2023-05-08"]


def test_build_skips_partial_weeks_and_prefers_chart_over_window():
    early = {
        "weekly": {
            "2024-04-22": [archive._row(_r("", "openai/gpt-4", 5.0))],
            "2024-04-29": [archive._row(_r("", "openai/gpt-4", 1.0))],
        },
        "window": None,
    }
    later = {
        "weekly": {"2024-04-29": [archive._row(_r("", "openai/gpt-4", 9.0))]},
        "window": [archive._row(_r("", "openai/gpt-4", 99.0))],
    }
    rows = archive.build({"20240501000000": early, "20240507000000": later})
    by_week = {r["week"]: r for r in rows}
    # The week of Apr 29 is partial in the May 1 copy, so the May 7 copy wins.
    assert by_week["2024-04-22"]["spend_usd"] == 5.0
    assert by_week["2024-04-29"] == {**by_week["2024-04-29"], "spend_usd": 9.0, "source": "weekly"}
    assert all(r["source"] == "weekly" for r in rows)


def test_build_merges_duplicate_rows():
    snap = {
        "weekly": {
            "2024-04-22": [
                archive._row(_r("", "openai/gpt-4", 2.0)),
                archive._row(_r("", "openai/gpt-4", 3.0)),
            ]
        },
        "window": None,
    }
    rows = archive.build({"20240501000000": snap})
    assert len(rows) == 1 and rows[0]["spend_usd"] == 5.0 and rows[0]["requests"] == 2


def test_history_marks_gap_and_appends_full_live_weeks():
    archived = [
        {
            "week": "2025-01-06",
            "permaslug": "anthropic/claude-3.5-sonnet",
            "variant": "standard",
            "spend_usd": 30.0,
            "prompt": 10,
            "completion": 1,
            "requests": 1,
            "source": "weekly",
        },
        {
            "week": "2025-01-06",
            "permaslug": "openai/gpt-4o",
            "variant": "standard",
            "spend_usd": 10.0,
            "prompt": 30,
            "completion": 3,
            "requests": 1,
            "source": "weekly",
        },
    ]
    live = [
        {
            "date": f"2025-01-{d:02d}",
            "permaslug": "openai/gpt-4o",
            "spend_usd": 2.0,
            "prompt": 1,
            "completion": 1,
        }
        for d in range(27, 32)
    ]
    live += [
        {
            "date": f"2025-02-0{d}",
            "permaslug": "openai/gpt-4o",
            "spend_usd": 2.0,
            "prompt": 1,
            "completion": 1,
        }
        for d in (1, 2)
    ]
    live += [
        {
            "date": "2025-02-03",
            "permaslug": "openai/gpt-4o",
            "spend_usd": 50.0,
            "prompt": 1,
            "completion": 1,
        }
    ]
    h = compute.history(archived, live, {}, min_week_usd=0)
    assert h["weeks"] == ["2025-01-06", "2025-01-13", "2025-01-20", "2025-01-27"]
    assert h["source"] == ["weekly", None, None, "live"]
    assert h["labs"]["anthropic"]["spend"] == [30, None, None, 0]
    assert h["labs"]["openai"]["spend"] == [10, None, None, 14]
    assert {m["id"] for m in h["models"]} == {"anthropic/claude-3.5-sonnet", "openai/gpt-4o"}


def test_archive_file_is_well_formed():
    rows = archive.load()
    assert rows, "archive_weekly.json.gz is missing"
    weeks = sorted({r["week"] for r in rows})
    assert weeks[0] <= "2023-06-01" and "2025-08-01" <= weeks[-1] < "2025-08-18"
    assert {r["source"] for r in rows} == {"weekly", "trailing7"}
    assert len({(r["week"], r["permaslug"], r["variant"]) for r in rows}) == len(rows)
