from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest

from polarix.metrics import charts, indicators, ledger
from polarix.tools import metrics as mt

DAY = 86400.0


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "ledger.sqlite")
    monkeypatch.setenv("POLARIX_METRICS_DB", path)
    monkeypatch.setenv("POLARIX_REPORTS_DIR", str(tmp_path / "reports"))
    return path


def _block(tool, duration=100, warnings=None, browser=None, desktop=None, params=None):
    b = {"tool": tool, "duration_ms": duration}
    if warnings:
        b["warnings"] = warnings
    if browser:
        b["browser"] = browser
    if desktop:
        b["desktop"] = desktop
    if params:
        b["effective_params"] = params
    return b


def _step(i, action, ok=True, ms=50, match=None, healed=None, error=None, result=None):
    s = {
        "step": i,
        "action": action,
        "success": ok,
        "duration_ms": ms,
        "result": result,
    }
    if match is not None:
        s["locator_match_count"] = match
    if healed is not None:
        s["healed_locator"] = healed
    if error:
        s["error"] = error
    return s


def _seq_payload(steps):
    return {
        "steps_total": len(steps),
        "steps_succeeded": sum(1 for s in steps if s["success"]),
        "results": steps,
    }


DESK = {"driver": "fake", "window": {"title": "Editor", "process": "editor.exe"}}


def _seed(db, now):
    """Previous week: shaky. Current week: healthier. Both with ≥5 samples."""
    for d in range(7):  # previous window: now-13.5d .. now-7.5d
        ts = now - 13.5 * DAY + d * DAY
        steps = [
            _step(1, "click", match=1),
            _step(
                2,
                "set_text",
                match=0,
                ok=False,
                error="LookupError: Locator matched 0 controls",
            ),
            _step(3, "click", match=2),
        ]
        ledger.record_response(
            _seq_payload(steps),
            _block("desktop_execute_sequence", 400, desktop=DESK),
            ts=ts,
        )
        ledger.record_response(
            {
                "status": "failed" if d % 2 else "passed",
                "assertions": {"total": 2, "passed": 1 if d % 2 else 2},
                "duration_ms": 900,
                "first_failure": (
                    "steps step 2 (assert): AssertionError: x" if d % 2 else None
                ),
            },
            _block("desktop_run_scenario", 900, desktop=DESK),
            ts=ts,
        )
    for d in range(7):  # current window
        ts = now - 6.5 * DAY + d * DAY
        steps = [
            _step(1, "click", match=1, ms=30),
            _step(2, "set_text", match=1, ms=20, healed={"auto_id": "15"}),
            _step(3, "click_image", ms=80),
            _step(4, "wait_for", ms=200),
        ]
        ledger.record_response(
            _seq_payload(steps),
            _block("desktop_execute_sequence", 300, desktop=DESK),
            ts=ts,
        )
        ledger.record_response(
            {
                "status": "passed",
                "assertions": {"total": 2, "passed": 2},
                "duration_ms": 700,
                "first_failure": None,
            },
            _block("desktop_run_scenario", 700, desktop=DESK),
            ts=ts,
        )
    # two maps of the same window → drift
    ledger.record_response(
        {
            "controls": [
                {"auto_id": "1"},
                {"auto_id": "15"},
                {"control_type": "Text", "title": "Nome"},
                {"control_type": "Pane"},
            ],
            "menus": [{"title": "Arquivo"}],
        },
        _block("desktop_map_window", 50, desktop=DESK),
        ts=now - 2 * DAY,
    )
    ledger.record_response(
        {
            "controls": [
                {"auto_id": "1"},
                {"auto_id": "99"},
                {"control_type": "Text", "title": "Nome"},
            ],
            "menus": [{"title": "Arquivo"}, {"title": "Editar"}],
        },
        _block("desktop_map_window", 50, desktop=DESK),
        ts=now - 1 * DAY,
    )
    # a browser map and a browser sequence
    ledger.record_response(
        {
            "pages_mapped": 1,
            "pages": [
                {
                    "data_qa_elements": [
                        {"qa": "Login", "count": 1},
                        {"qa": "Pwd", "count": 1},
                    ],
                    "forms": [{"inputs": [{"id": "user"}, {"type": "submit"}]}],
                }
            ],
            "selector_index": {"Login": {}, "Pwd": {}},
        },
        _block(
            "browser_map_site", 1200, browser={"final_url": "https://app.example.com/"}
        ),
        ts=now - DAY,
    )
    ledger.record_response(
        _seq_payload(
            [
                {
                    "step": 1,
                    "action": "fill",
                    "success": True,
                    "duration_ms": 90,
                    "selector_match_count": 1,
                }
            ]
        ),
        _block(
            "browser_execute_sequence",
            500,
            browser={"final_url": "https://app.example.com/x"},
        ),
        ts=now - DAY,
    )


def test_record_extracts_steps_maps_and_scenarios(db):
    now = time.time()
    _seed(db, now)
    runs = ledger.fetch("runs")
    assert {r["kind"] for r in runs} == {"desktop", "browser"}
    assert {r["target"] for r in runs if r["kind"] == "desktop"} == {"editor.exe"}
    assert {r["target"] for r in runs if r["kind"] == "browser"} == {"app.example.com"}
    steps = ledger.fetch("steps")
    assert any(s["healed"] for s in steps) and any(s["pixel"] for s in steps)
    assert any(s["error_type"] == "LookupError" for s in steps)
    assert any(
        s["match_count"] == 1 for s in steps if s["kind"] == "browser"
    )  # selector_match_count normalised
    maps = ledger.fetch("maps")
    assert len(maps) == 3
    web = next(m for m in maps if m["kind"] == "browser")
    assert web["elements"] == 4 and web["stable_ids"] == 3
    scenarios = ledger.fetch("scenarios")
    assert len(scenarios) == 14 and {s["status"] for s in scenarios} == {
        "passed",
        "failed",
    }
    assert any(s["first_failure_type"] == "AssertionError" for s in scenarios)


def test_metrics_tools_are_not_recorded_and_can_be_disabled(db, monkeypatch):
    assert ledger.record_response({}, _block("metrics_summary")) is None
    monkeypatch.setenv("POLARIX_METRICS", "off")
    assert ledger.record_response({}, _block("desktop_list_windows")) is None
    monkeypatch.delenv("POLARIX_METRICS")
    assert (
        ledger.record_response({"windows": []}, _block("desktop_list_windows"))
        is not None
    )


def test_summary_detects_improvement_and_regression(db):
    now = time.time()
    _seed(db, now)
    s = indicators.summary("7d", now=now)
    ind = s["indicators"]
    assert ind["step_success_rate"]["status"] == "improving"
    assert (
        ind["step_success_rate"]["value"] == 1.0
        and ind["step_success_rate"]["previous"] < 0.7
    )
    assert (
        ind["broken_locator_rate"]["status"] == "improving"
    )  # lower is better and it dropped
    assert ind["healing_rate"]["status"] == "worsening"  # went from 0 to 50%
    assert ind["scenario_pass_rate"]["status"] == "improving"
    assert ind["pixel_fallback_rate"]["value"] > 0
    assert s["health"]["score"] > s["health"]["previous"]
    assert (
        "healing_rate" in s["regressions"] and "step_success_rate" in s["improvements"]
    )
    assert s["counts"]["scenarios"] == 7 and s["error_taxonomy"] == {}


def test_insufficient_samples_and_neutral_indicators(db):
    now = time.time()
    ledger.record_response(
        _seq_payload([_step(1, "click", match=1)]),
        _block("desktop_execute_sequence", desktop=DESK),
        ts=now - 10,
    )
    s = indicators.summary("7d", now=now)
    assert s["indicators"]["step_success_rate"]["status"] == "insufficient"
    assert s["indicators"]["runs"]["better"] == "neutral"
    assert s["health"]["score"] is not None


def test_map_drift_and_flakiness(db):
    now = time.time()
    _seed(db, now)
    rows = indicators.load_rows(now - 7 * DAY, now)
    drifts = indicators.map_drifts(rows["maps"])
    d = next(x for x in drifts if x["kind"] == "desktop")
    assert 0 < d["drift"] < 1 and "id:15" in d["lost"] and "id:99" in d["new"]
    prev = indicators.load_rows(now - 14 * DAY, now - 7 * DAY)
    assert (
        indicators.flaky_names(prev["scenarios"]) == []
    )  # unnamed scenarios are ignored
    ledger.record_response(
        {"status": "passed", "assertions": {"total": 1, "passed": 1}, "name": "login"},
        _block("desktop_run_scenario", desktop=DESK),
        ts=now - 3,
    )
    ledger.record_response(
        {"status": "failed", "assertions": {"total": 1, "passed": 0}, "name": "login"},
        _block("desktop_run_scenario", desktop=DESK),
        ts=now - 2,
    )
    s = indicators.summary("7d", now=now)
    assert s["flaky_scenarios"] == ["login"]


def test_trend_buckets_and_chart(db):
    now = time.time()
    _seed(db, now)
    t = indicators.trend("step_success_rate", "7d", "1d", now=now)
    assert len(t["points"]) == 7 and t["last"] == 1.0
    svg = charts.line_chart(
        {"x": [p["value"] for p in t["points"]]},
        charts._bucket_labels(t["points"]),
        "t",
        "rate",
    )
    assert svg.startswith("<svg") and "polyline" in svg
    with pytest.raises(ValueError, match="unknown indicator"):
        indicators.trend("nope")
    with pytest.raises(ValueError, match="window"):
        indicators.parse_window("yesterday")


def test_error_report_lists_actionable_items(db):
    now = time.time()
    _seed(db, now)
    rep = indicators.error_report("14d", now=now)
    assert rep["error_taxonomy"] == {"LookupError": 7}
    assert rep["failures_by_action"] == {"set_text": 7}
    assert rep["top_failures"][0]["detail"].startswith("LookupError")
    assert rep["healed_locators"][0]["healed_to"] == '{"auto_id": "15"}'
    assert rep["map_drift"] and rep["map_drift"][0]["target"] == "editor.exe"


def test_charts_handle_gaps_and_empty():
    svg = charts.line_chart(
        {"a": [None, 0.5, None, 0.7]}, ["1", "2", "3", "4"], "gaps", "rate"
    )
    assert "circle" in svg and "polyline" not in svg or "polyline" in svg
    assert charts.bar_chart([], "empty").startswith("<svg")
    assert charts.sparkline([None, 1.0]).endswith("</svg>")
    assert charts.to_data_url("<svg/>").startswith("data:image/svg+xml;base64,")
    assert base64.b64decode(charts.to_data_url("<svg/>").split(",", 1)[1]) == b"<svg/>"


def call(coro):
    return json.loads(asyncio.run(coro))


def test_tools_end_to_end(db):
    now = time.time()
    _seed(db, now)
    summary = call(mt.metrics_summary("7d", include_definitions=True))
    assert summary["health"]["score"] and len(summary["definitions"]) == len(
        indicators.INDICATORS
    )
    assert summary["_polarix"]["tool"] == "metrics_summary"
    trend = call(mt.metrics_trend("healing_rate", "7d", "1d"))
    assert trend["chart"].startswith("data:image/svg+xml") and len(trend["points"]) == 7
    dash = call(mt.metrics_dashboard("7d"))
    assert dash["html_path"].endswith(".html") and dash["html_bytes"] > 2000
    html = open(dash["html_path"], encoding="utf-8").read()
    assert "Health" in html and "step_success_rate" in html and "<svg" in html
    errors = call(mt.metrics_errors("14d"))
    assert errors["failed_steps"] == 7
    targets = call(mt.metrics_targets("30d"))
    assert {t["target"] for t in targets["targets"]} == {
        "editor.exe",
        "app.example.com",
    }
    defs = call(mt.metrics_indicators())
    assert defs["min_sample"] == 5
    export = call(mt.metrics_export("steps", "30d", "csv"))
    assert export["rows"] > 0 and export["path"].endswith(".csv")
    assert call(mt.metrics_summary("soon"))["success"] is False
    # the metrics tools did not record themselves
    assert all(not r["tool"].startswith("metrics_") for r in ledger.fetch("runs"))


def test_wrap_records_desktop_tool_calls(db, fake_env):
    from polarix.tools import desktop as dt

    call(dt.desktop_list_windows())
    call(dt.desktop_map_window(json.dumps({"title_re": ".*Editor.*"})))
    runs = ledger.fetch("runs")
    assert [r["tool"] for r in runs] == ["desktop_list_windows", "desktop_map_window"]
    maps = ledger.fetch("maps")
    assert maps and maps[0]["stable_ids"] >= 3  # auto_ids 15, 1025 + menus
