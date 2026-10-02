"""Metrics tools — are the commands improving or getting worse?

metrics_summary · metrics_trend · metrics_dashboard · metrics_errors · metrics_targets ·
metrics_indicators · metrics_export

Everything Polarix runs (browser, desktop, vm, scenarios) is recorded in a local
SQLite ledger automatically. These tools read it. They are not recorded themselves.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import time
from typing import Any, Callable, Optional

from polarix.config import env_setting
from polarix.metrics import charts, indicators, ledger
from polarix.server import mcp
from polarix.telemetry import _polarix, _start, _wrap


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


def _fail(tool: str, t0: float, exc: Exception, params: Optional[dict] = None) -> str:
    return _wrap(
        {"success": False, "error": f"{type(exc).__name__}: {exc}"},
        _polarix(tool, t0, params=params, warnings=[str(exc)]),
    )


def _reports_dir() -> str:
    import tempfile

    return env_setting(
        "REPORTS_DIR", os.path.join(tempfile.gettempdir(), "polarix_reports")
    )


@mcp.tool()
async def metrics_summary(
    window: str = "7d",
    target: Optional[str] = None,
    kind: Optional[str] = None,
    include_definitions: bool = False,
) -> str:
    """Every indicator for the window, compared with the window right before it.

    Each indicator reports value, previous, delta, sample size and a status:
    improving · stable · worsening · insufficient (fewer than 5 samples).
    `health` is a 0–100 score built from success, pass, locator-hit, assertion,
    healing and flakiness rates. `regressions` lists what got worse, by size.

    Args:
        window: 24h, 7d, 4w, 30d or all (default: 7d). The previous window has the same length.
        target: Restrict to one site host (browser) or process/window (desktop).
        kind: browser | desktop | vm.
        include_definitions: Add the indicator definitions (what each one means).

    Returns:
        JSON: { health, indicators, regressions, improvements, counts, error_taxonomy,
                flaky_scenarios, definitions?, _polarix }
    """
    t0 = _start()
    try:
        data = await _run(indicators.summary, window, target, kind)
        if include_definitions:
            data["definitions"] = indicators.definitions()
    except ValueError as exc:
        return _fail("metrics_summary", t0, exc, {"window": window})
    return _wrap(
        data,
        _polarix(
            "metrics_summary",
            t0,
            params={"window": window, "target": target, "kind": kind},
        ),
    )


@mcp.tool()
async def metrics_trend(
    indicator: str,
    window: str = "30d",
    bucket: str = "1d",
    target: Optional[str] = None,
    kind: Optional[str] = None,
    chart: bool = True,
) -> str:
    """Time series of one indicator (bucketed) plus an SVG line chart.

    Args:
        indicator: Name from metrics_indicators (e.g. step_success_rate, healing_rate).
        window: Period to cover (default: 30d).
        bucket: Bucket size: 1h, 6h, 1d, 1w (default: 1d).
        target: Restrict to one target.
        kind: browser | desktop | vm.
        chart: Include `chart` as an SVG data URL (default: True).

    Returns:
        JSON: { indicator, description, better, points: [{from, to, value, n}], min, max, last, chart, _polarix }
    """
    t0 = _start()
    try:
        data = await _run(indicators.trend, indicator, window, bucket, target, kind)
        if chart:
            labels = charts._bucket_labels(data["points"])
            svg = charts.line_chart(
                {indicator: [p["value"] for p in data["points"]]},
                labels,
                f"{indicator} — {window} by {bucket}",
                data["unit"],
            )
            data["chart"] = charts.to_data_url(svg)
    except ValueError as exc:
        return _fail("metrics_trend", t0, exc, {"indicator": indicator})
    return _wrap(
        data,
        _polarix(
            "metrics_trend",
            t0,
            params={"indicator": indicator, "window": window, "bucket": bucket},
        ),
    )


@mcp.tool()
async def metrics_dashboard(
    window: str = "7d",
    bucket: str = "1d",
    target: Optional[str] = None,
    kind: Optional[str] = None,
    save: bool = True,
) -> str:
    """Build the HTML dashboard: health, indicator tiles with sparklines, trend charts,
    error taxonomy, top failures and map drift. Saved under POLARIX_REPORTS_DIR.

    Returns:
        JSON: { html_path, health, regressions, improvements, counts, _polarix }
    """
    t0 = _start()
    try:
        summary = await _run(indicators.summary, window, target, kind)
        trends = {}
        for name in indicators.INDICATORS:
            trends[name] = await _run(
                indicators.trend, name, window, bucket, target, kind
            )
        errors = await _run(indicators.error_report, window, target, kind, 10)
        page = charts.dashboard_html(summary, trends, errors)
        path = None
        if save:
            os.makedirs(_reports_dir(), exist_ok=True)
            path = os.path.join(
                _reports_dir(),
                f"metrics-dashboard-{time.strftime('%Y%m%d-%H%M%S')}.html",
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(page)
    except ValueError as exc:
        return _fail("metrics_dashboard", t0, exc, {"window": window})
    return _wrap(
        {
            "html_path": path,
            "health": summary["health"],
            "regressions": summary["regressions"],
            "improvements": summary["improvements"],
            "counts": summary["counts"],
            "html_bytes": len(page),
        },
        _polarix(
            "metrics_dashboard",
            t0,
            params={"window": window, "bucket": bucket, "target": target},
        ),
    )


@mcp.tool()
async def metrics_errors(
    window: str = "7d",
    target: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 10,
) -> str:
    """What is not working: error taxonomy, failures by action, the most frequent failing
    steps, locators that needed healing (with the locator that worked), map drift per
    target (identifiers lost/new between consecutive maps) and flaky scenarios.

    Returns:
        JSON: { failed_steps, error_taxonomy, failures_by_action, top_failures, healed_locators,
                map_drift, flaky_scenarios, _polarix }
    """
    t0 = _start()
    try:
        data = await _run(indicators.error_report, window, target, kind, limit)
    except ValueError as exc:
        return _fail("metrics_errors", t0, exc, {"window": window})
    return _wrap(
        data,
        _polarix("metrics_errors", t0, params={"window": window, "target": target}),
    )


@mcp.tool()
async def metrics_targets(window: str = "30d") -> str:
    """Targets seen in the window (site hosts, desktop processes/windows, VMs) with run
    counts, success and first/last seen — use the `target` value in the other tools.

    Returns:
        JSON: { targets: [{kind, target, runs, ok, first_seen, last_seen}], _polarix }
    """
    t0 = _start()
    try:
        since = time.time() - indicators.parse_window(window)
        rows = await _run(ledger.targets, since)
    except ValueError as exc:
        return _fail("metrics_targets", t0, exc, {"window": window})
    return _wrap(
        {"target_count": len(rows), "targets": rows},
        _polarix("metrics_targets", t0, params={"window": window}),
    )


@mcp.tool()
async def metrics_indicators() -> str:
    """Definitions of every indicator: group, which direction is better, unit, meaning,
    and whether it feeds the health score.

    Returns:
        JSON: { indicators: [...], health_weights, _polarix }
    """
    t0 = _start()
    return _wrap(
        {
            "indicators": indicators.definitions(),
            "health_weights": indicators.HEALTH_WEIGHTS,
            "min_sample": indicators.MIN_SAMPLE,
        },
        _polarix("metrics_indicators", t0),
    )


@mcp.tool()
async def metrics_export(
    table: str = "runs",
    window: str = "30d",
    fmt: str = "json",
    target: Optional[str] = None,
) -> str:
    """Export raw ledger rows (runs | steps | maps | scenarios) as JSON or CSV for an
    external BI tool. Written under POLARIX_REPORTS_DIR.

    Returns:
        JSON: { path, rows, _polarix }
    """
    t0 = _start()
    try:
        since = time.time() - indicators.parse_window(window)
        rows = await _run(ledger.fetch, table, since, None, target)
        os.makedirs(_reports_dir(), exist_ok=True)
        ext = "csv" if fmt.lower() == "csv" else "json"
        path = os.path.join(
            _reports_dir(), f"metrics-{table}-{time.strftime('%Y%m%d-%H%M%S')}.{ext}"
        )
        with open(path, "w", encoding="utf-8", newline="") as f:
            if ext == "csv":
                if rows:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
            else:
                json.dump(rows, f, ensure_ascii=False, indent=2)
    except ValueError as exc:
        return _fail("metrics_export", t0, exc, {"table": table})
    return _wrap(
        {"path": path, "rows": len(rows), "table": table, "format": ext},
        _polarix("metrics_export", t0, params={"table": table, "window": window}),
    )
