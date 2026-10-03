"""Testing tools — scenarios, suites, KPIs, and the pixel fallbacks for canvases.

SCENARIOS   desktop_run_scenario · desktop_run_suite · desktop_scenario_from_macro ·
            desktop_report_list
CANVAS      desktop_find_image · desktop_vision_locate · desktop_vision_verify
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable, Optional

from polarix.desktop.driver import DesktopUnavailable, get_driver
from polarix.desktop.macros import load_macro, save_macro
from polarix.desktop.scenarios import (
    load_scenarios,
    reports_dir,
    run_scenario,
    expand_matrix,
    run_suite,
    save_report,
    scenario_from_macro,
    validate_scenario,
)
from polarix.desktop.steps import ASSERT_KINDS
from polarix.desktop.vision import (
    find_template,
    load_image_arg,
    vision_locate,
    vision_verify,
)
from polarix.server import mcp
from polarix.telemetry import _polarix, _start, _wrap
from polarix.tools.desktop import _desktop_state, _fail, _window_arg

_ERRORS = (DesktopUnavailable, LookupError, TimeoutError, ValueError, OSError)


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


def _vm_backend_if_needed(scenarios: list[dict], restore: bool):
    if not restore or not any((sc.get("vm") or {}).get("snapshot") for sc in scenarios):
        return None
    from polarix.vm.backends import get_backend

    backend_name = next(
        ((sc.get("vm") or {}).get("backend") for sc in scenarios if sc.get("vm")), None
    )
    return get_backend(backend_name)


# ════════════════════════════════════════════════════════════════════════
# SCENARIOS
# ════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def desktop_run_scenario(
    scenario: str,
    restore_vm: bool = False,
    save: bool = True,
    target: Optional[str] = None,
) -> str:
    """Run one test scenario (steps + assertions) and return pass/fail evidence.

    `scenario` is inline JSON/YAML or a file path. Steps use the
    desktop_execute_sequence vocabulary plus {"action": "assert", "kind": ...}
    with kinds: control_exists · control_absent · control_enabled · control_disabled ·
    text_equals · text_contains · window_exists · window_absent · window_title_contains ·
    image_present · image_absent · vision (expectation judged by a multimodal model).

    Example:
      {"name": "salvar", "window": {"title_re": ".*Editor.*"},
       "steps": [
         {"action": "menu", "path": "Arquivo->Salvar"},
         {"action": "wait_for", "window": {"title_re": ".*Salvar como.*"}},
         {"action": "set_text", "locator": {"auto_id": "1001"}, "value": "obra.txt"},
         {"action": "click", "locator": {"auto_id": "1"}},
         {"action": "wait_for", "window": {"title_re": ".*Editor.*"}},
         {"action": "assert", "kind": "window_title_contains", "expected": "obra.txt"}]}

    Args:
        scenario: Inline JSON/YAML text, or path to a .json/.yaml file.
        restore_vm: Restore scenario.vm.snapshot through the hypervisor first (default: False).
        save: Also write JSON + HTML report to POLARIX_REPORTS_DIR (default: True).

    Returns:
        JSON: { status, assertions, first_failure, failure_screenshot, phases, report, _polarix }
    """
    t0 = _start()
    try:
        scenarios = load_scenarios(scenario)
        if len(scenarios) != 1:
            raise ValueError(
                f"expected one scenario, got {len(scenarios)} — use desktop_run_suite"
            )
        sc = validate_scenario(scenarios[0])
        driver = get_driver(target=target or sc.get("target"))
        backend = await _run(_vm_backend_if_needed, [sc], restore_vm)
        result = await _run(run_scenario, driver, sc, backend)
        report_paths = None
        if save:
            report = {
                "name": sc["name"],
                "kpis": {
                    "scenarios": 1,
                    "passed": int(result["status"] == "passed"),
                    "assertions_total": result["assertions"]["total"],
                    "assertions_passed": result["assertions"]["passed"],
                    "duration_ms": result["duration_ms"],
                },
                "results": [result],
            }
            report_paths = save_report(report, sc["name"])
    except (*_ERRORS, json.JSONDecodeError) as exc:
        return _fail("desktop_run_scenario", t0, exc, {"restore_vm": restore_vm})
    return _wrap(
        {**result, "report": report_paths},
        _polarix(
            "desktop_run_scenario",
            t0,
            desktop=_desktop_state(driver, sc.get("window")),
            params={"restore_vm": restore_vm, "save": save},
            warnings=result["warnings"] or None,
        ),
    )


@mcp.tool()
async def desktop_run_suite(
    source: str,
    name: str = "suite",
    tags_json: Optional[str] = None,
    restore_vm: bool = False,
    include_results: bool = False,
    target: Optional[str] = None,
) -> str:
    """Run a set of scenarios and return the KPIs (pass rate, duration, failures).

    Args:
        source: Directory of .json/.yaml scenario files, one file holding a list
            (or {"scenarios": [...]}), or inline JSON/YAML. A scenario with
            `targets: [...]` or `matrix: {"var": [values]}` runs once per combination,
            with `${var}` / `${var.field}` substituted in its steps. To install/reset
            the software first, use environment_run instead.
        name: Suite name used in the report file names (default: "suite").
        tags_json: JSON array of tags — only scenarios carrying one of them run.
        restore_vm: Restore each scenario's vm.snapshot before it runs (default: False).
        include_results: Return the full per-scenario results too (default: False — KPIs
            and failures only; the full report is always written to disk).

    Returns:
        JSON: { name, kpis: {scenarios, passed, failed, error, pass_rate, assertions_total,
                assertions_passed, assertion_pass_rate, duration_ms, mean_scenario_ms,
                slowest, healed_locators}, failures, report: {json, html}, _polarix }
    """
    t0 = _start()
    try:
        tags = json.loads(tags_json) if tags_json else None
        scenarios = [
            validate_scenario(sc) for sc in expand_matrix(load_scenarios(source))
        ]
        if not scenarios:
            raise ValueError("no scenarios found")
        # resolve per scenario (matrix of targets); the first one also reports state
        driver = get_driver(target=target or scenarios[0].get("target"))
        backend = await _run(_vm_backend_if_needed, scenarios, restore_vm)
        report = await _run(
            run_suite,
            driver,
            scenarios,
            backend,
            name,
            tags,
            lambda sc: get_driver(target=target or sc.get("target")),
        )
        paths = save_report(report, name)
    except (*_ERRORS, json.JSONDecodeError) as exc:
        return _fail("desktop_run_suite", t0, exc, {"source": source[:120]})
    payload = {
        "name": report["name"],
        "started_at": report["started_at"],
        "kpis": report["kpis"],
        "failures": report["failures"],
        "report": paths,
    }
    if include_results:
        payload["results"] = report["results"]
    return _wrap(
        payload,
        _polarix(
            "desktop_run_suite",
            t0,
            desktop=_desktop_state(driver, None),
            params={
                "scenarios": len(scenarios),
                "tags": tags,
                "restore_vm": restore_vm,
            },
        ),
    )


@mcp.tool()
async def desktop_scenario_from_macro(
    macro_name: str,
    scenario_name: Optional[str] = None,
    save_as_macro: bool = False,
    target: Optional[str] = None,
) -> str:
    """Turn a recorded macro into a scenario with default assertions.

    Each recorded click gains a `control_exists` assertion (so a renamed button
    fails loudly) and the scenario ends with `window_exists`. Edit the returned
    JSON to add text_equals / image_present / vision checks, then run it with
    desktop_run_scenario.

    Args:
        macro_name: Name of a saved macro (desktop_macro_list).
        scenario_name: Name for the scenario (default: "macro-<macro_name>").
        save_as_macro: Also store the scenario steps as a macro named `scenario_name`.

    Returns:
        JSON: { scenario, saved_macro, _polarix }
    """
    t0 = _start()
    try:
        macro = load_macro(macro_name)
        sc = scenario_from_macro(macro, scenario_name)
        saved = None
        if save_as_macro:
            saved = save_macro(
                sc["name"], sc["steps"], sc.get("window"), source="generated"
            )["path"]
    except _ERRORS as exc:
        return _fail("desktop_scenario_from_macro", t0, exc, {"macro_name": macro_name})
    return _wrap(
        {"scenario": sc, "saved_macro": saved, "assert_kinds": list(ASSERT_KINDS)},
        _polarix("desktop_scenario_from_macro", t0, params={"macro_name": macro_name}),
    )


@mcp.tool()
async def desktop_report_list(limit: int = 20, target: Optional[str] = None) -> str:
    """List saved test reports (newest first) with their KPIs.

    Returns:
        JSON: { reports: [{name, json, html, kpis, started_at}], reports_dir, _polarix }
    """
    t0 = _start()
    out: list[dict] = []
    directory = reports_dir()
    if os.path.isdir(directory):
        files = sorted(
            (f for f in os.listdir(directory) if f.endswith(".json")), reverse=True
        )
        for fname in files[:limit]:
            path = os.path.join(directory, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    rep = json.load(f)
                out.append(
                    {
                        "name": rep.get("name"),
                        "started_at": rep.get("started_at"),
                        "kpis": rep.get("kpis"),
                        "json": path,
                        "html": path[:-5] + ".html",
                    }
                )
            except Exception as exc:
                out.append({"json": path, "error": str(exc)})
    return _wrap(
        {"report_count": len(out), "reports": out, "reports_dir": directory},
        _polarix("desktop_report_list", t0, params={"limit": limit}),
    )


# ════════════════════════════════════════════════════════════════════════
# CANVAS FALLBACKS — pixels when the tree has nothing
# ════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def desktop_find_image(
    window_json: str,
    image: str,
    threshold: float = 0.85,
    max_results: int = 5,
    target: Optional[str] = None,
) -> str:
    """Locate a template image inside a window screenshot (OpenCV, no LLM).

    Use it for toolbar icons and drawn elements the UI Automation tree does not
    expose. Coordinates are window-relative: pass them to a click step as
    {"action": "click", "x": ..., "y": ...} or use {"action": "click_image", "image": ...}.

    Args:
        window_json: Window locator (see desktop_map_window).
        image: Template as data URL, base64, or file path (PNG/JPG).
        threshold: Minimum normalised correlation to count as a match (default: 0.85).
        max_results: Maximum matches to return (default: 5).

    Returns:
        JSON: { found, best: {x, y, score, rect}, matches, screenshot_size, _polarix }
    """
    t0 = _start()
    try:
        window = _window_arg(window_json)
        driver = get_driver(target=target)
        png = await _run(driver.screenshot, window)
        found = await _run(
            find_template, png, load_image_arg(image), threshold, max_results
        )
    except _ERRORS as exc:
        return _fail("desktop_find_image", t0, exc, {"threshold": threshold})
    return _wrap(
        found,
        _polarix(
            "desktop_find_image",
            t0,
            desktop=_desktop_state(driver, window),
            params={"threshold": threshold, "max_results": max_results},
            warnings=None if found["found"] else ["template not found above threshold"],
        ),
    )


@mcp.tool()
async def desktop_vision_locate(
    window_json: str,
    description: str,
    model: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """Ask a multimodal model where an element described in words is on the window.

    The last resort for custom-drawn areas (CAD canvas). Returns window-relative
    coordinates; prefer desktop_find_image when you have a reference crop.

    Args:
        window_json: Window locator.
        description: What to find, e.g. "the red beam between the two columns".
        model: Vision-capable model id (default: POLARIX_VISION_MODEL).

    Returns:
        JSON: { found, x, y, confidence, reasoning, model, screenshot_size, _polarix }
    """
    t0 = _start()
    try:
        window = _window_arg(window_json)
        driver = get_driver(target=target)
        png = await _run(driver.screenshot, window)
        located = await vision_locate(png, description, model)
    except (*_ERRORS, RuntimeError) as exc:
        return _fail("desktop_vision_locate", t0, exc, {"description": description})
    return _wrap(
        located,
        _polarix(
            "desktop_vision_locate",
            t0,
            desktop=_desktop_state(driver, window),
            params={"model": located["model"]},
        ),
    )


@mcp.tool()
async def desktop_vision_verify(
    window_json: str,
    expectation: str,
    model: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """Judge an expectation about what the window shows, using a multimodal model.

    The visual assertion: "a beam is drawn between the two columns", "the dialog
    shows no error". Also available inside scenarios as
    {"action": "assert", "kind": "vision", "expectation": "..."}.

    Returns:
        JSON: { passed, confidence, reasoning, observed, model, _polarix }
    """
    t0 = _start()
    try:
        window = _window_arg(window_json)
        driver = get_driver(target=target)
        png = await _run(driver.screenshot, window)
        verdict = await vision_verify(png, expectation, model)
    except (*_ERRORS, RuntimeError) as exc:
        return _fail("desktop_vision_verify", t0, exc, {"expectation": expectation})
    return _wrap(
        verdict,
        _polarix(
            "desktop_vision_verify",
            t0,
            desktop=_desktop_state(driver, window),
            params={"model": verdict["model"]},
        ),
    )
