"""Desktop tools — Map First for native application windows (guest VM or local).

KNOWLEDGE     desktop_list_windows · desktop_map_window · desktop_explore_menus
EXECUTION     desktop_launch · desktop_execute_sequence · desktop_auto_sequence
VERIFICATION  desktop_diff_window · desktop_screenshot
MACROS        desktop_record_start · desktop_record_stop · desktop_macro_save ·
              desktop_macro_list · desktop_macro_run · desktop_macro_delete

Where the automation runs is decided by POLARIX_DESKTOP_DRIVER:
  remote    → a Polarix agent inside the VM (POLARIX_DESKTOP_AGENT_URL)
  auto      → pywinauto when Polarix itself runs on Windows
  fake      → simulated app (development and tests on any OS)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Optional

from polarix.config import DEFAULT_MODEL
from polarix.desktop.driver import DesktopDriver, DesktopUnavailable, get_driver
from polarix.desktop.macros import (
    delete_macro,
    list_macros,
    load_macro,
    macros_dir,
    save_macro,
)
from polarix.desktop.model import (
    build_control_index,
    diff_inventories,
    parse_window,
)
from polarix.desktop.recorder import ACTIVE, MacroRecorder
from polarix.desktop.steps import _png_data_url, run_desktop_steps
from polarix.llm import generate_desktop_steps
from polarix.server import mcp
from polarix.telemetry import _polarix, _start, _wrap

_TOOL_ERRORS = (DesktopUnavailable, LookupError, TimeoutError, ValueError)


def _parse_json(raw: Optional[str], what: str) -> Any:
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {what}: {exc}") from exc


def _window_arg(window_json: Optional[str]) -> Optional[dict]:
    """Window argument: JSON object, or a plain string meaning 'title contains'."""
    if window_json is None or window_json.strip() == "":
        return None
    text = window_json.strip()
    if text.startswith("{"):
        return parse_window(_parse_json(text, "window_json"))
    return parse_window(text)


def _desktop_state(driver: DesktopDriver, window: Optional[dict]) -> dict:
    state: dict = {}
    try:
        state.update(driver.platform_info())
    except Exception as exc:
        state["platform_error"] = str(exc)
    if window is not None:
        try:
            state["window"] = driver.window_state(window)
        except Exception as exc:
            state["window"] = {"locator": window, "error": str(exc)}
    return state


def _fail(tool: str, t0: float, exc: Exception, params: Optional[dict] = None) -> str:
    hint = None
    if isinstance(exc, DesktopUnavailable):
        hint = (
            "Point POLARIX_DESKTOP_DRIVER=remote at a Polarix agent running in the "
            "guest (python -m polarix.desktop.agent), or use POLARIX_DESKTOP_DRIVER="
            "fake for the simulated app."
        )
    return _wrap(
        {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            **({"hint": hint} if hint else {}),
        },
        _polarix(tool, t0, params=params, warnings=[str(exc)]),
    )


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Drivers are synchronous; keep the event loop free."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def _map_window(driver: DesktopDriver, window: dict, max_depth: int, menus: bool):
    inv = driver.inventory(window, max_depth)
    data = {
        "window": driver.window_state(window),
        "control_count": len(inv),
        "controls": [c.to_dict() for c in inv],
        "control_index": build_control_index(inv),
    }
    if menus:
        try:
            data["menus"] = driver.menu_items(window, expand=False)
        except Exception as exc:
            data["menus"] = []
            data["menus_error"] = str(exc)
    return data, inv


# ════════════════════════════════════════════════════════════════════════
# KNOWLEDGE
# ════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def desktop_list_windows(
    title_filter: Optional[str] = None, target: Optional[str] = None
) -> str:
    """List top-level windows on the desktop target (title, process, pid, handle).

    Args:
        title_filter: Case-insensitive substring to filter titles (optional).

    Returns:
        JSON: { windows: [{title, class_name, process, pid, handle, rect, is_active}], _polarix }
    """
    t0 = _start()
    try:
        driver = get_driver(target=target)
        windows = await _run(driver.list_windows, title_filter)
    except _TOOL_ERRORS as exc:
        return _fail("desktop_list_windows", t0, exc, {"title_filter": title_filter})
    return _wrap(
        {"window_count": len(windows), "windows": windows},
        _polarix(
            "desktop_list_windows",
            t0,
            desktop=_desktop_state(driver, None),
            params={"title_filter": title_filter},
        ),
    )


@mcp.tool()
async def desktop_map_window(
    window_json: str,
    max_depth: int = 8,
    include_menus: bool = True,
    target: Optional[str] = None,
) -> str:
    """Inventory the UI Automation control tree of a window — the desktop "site map".

    Call this before automating anything: it returns every control with
    control_type, title, auto_id, path and rect, plus a compact control_index
    grouped by type (what desktop_auto_sequence feeds to the planner).

    Args:
        window_json: Window locator — JSON like {"title_re": ".*CadApp.*"} or
            {"process": "CadApp.exe"} — or a plain title substring.
        max_depth: Maximum tree depth to walk (default: 8).
        include_menus: Also list the top-level menu bar items (default: True).

    Returns:
        JSON: { window, control_count, controls, control_index, menus, _polarix }
    """
    t0 = _start()
    try:
        window = _window_arg(window_json)
        if window is None:
            raise ValueError("window_json is required")
        driver = get_driver(target=target)
        data, _ = await _run(_map_window, driver, window, max_depth, include_menus)
    except _TOOL_ERRORS as exc:
        return _fail("desktop_map_window", t0, exc, {"window_json": window_json})
    return _wrap(
        data,
        _polarix(
            "desktop_map_window",
            t0,
            desktop=_desktop_state(driver, window),
            params={"max_depth": max_depth, "include_menus": include_menus},
        ),
    )


@mcp.tool()
async def desktop_explore_menus(
    window_json: str, max_items: int = 12, target: Optional[str] = None
) -> str:
    """Open each top-level menu and record the items it reveals.

    Desktop twin of browser_explore_page: menus hide most commands of a native
    app, and their items only exist in the tree while the menu is open.

    Args:
        window_json: Window locator (see desktop_map_window).
        max_items: Maximum number of top-level menus to expand (default: 12).

    Returns:
        JSON: { window, menus: [{title, items: [...]}], menu_paths: ["Top->Item", ...], _polarix }
    """
    t0 = _start()
    warnings: list[str] = []
    try:
        window = _window_arg(window_json)
        if window is None:
            raise ValueError("window_json is required")
        driver = get_driver(target=target)
        menus = await _run(driver.menu_items, window, True)
    except _TOOL_ERRORS as exc:
        return _fail("desktop_explore_menus", t0, exc, {"window_json": window_json})
    menus = menus[:max_items]
    paths = [f"{m['title']}->{item}" for m in menus for item in m.get("items", [])]
    if not paths:
        warnings.append(
            "No menu items discovered — app may use a ribbon/toolbar instead of a "
            "menu bar; map the window and look for Button/TabItem controls"
        )
    return _wrap(
        {
            "window": await _run(driver.window_state, window),
            "menus": menus,
            "menu_paths": paths,
        },
        _polarix(
            "desktop_explore_menus",
            t0,
            desktop=_desktop_state(driver, window),
            params={"max_items": max_items},
            warnings=warnings or None,
        ),
    )


# ════════════════════════════════════════════════════════════════════════
# EXECUTION
# ════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def desktop_targets() -> str:
    """List the named targets a sequence can run on (which machine / operating system).

    Built-in: `fake` (simulated editor, any OS) and `local` (this machine, Windows only).
    Others come from POLARIX_TARGETS_FILE (~/.config/polarix/targets.json) or the
    POLARIX_TARGETS JSON env var — each with driver, agent_url, os and optional vm.
    Pass the name as `target` to any desktop tool, or set `target` / `targets` in a scenario.

    Returns:
        JSON: { targets: [{name, driver, os, description, default, agent_url?, token_set?, vm?}],
                default, targets_file, _polarix }
    """
    t0 = _start()
    from polarix.desktop.targets import default_target, describe, targets_file

    try:
        targets = describe()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _fail("desktop_targets", t0, exc, {"targets_file": targets_file()})
    return _wrap(
        {
            "target_count": len(targets),
            "targets": targets,
            "default": default_target(),
            "targets_file": targets_file(),
        },
        _polarix("desktop_targets", t0, params={"targets_file": targets_file()}),
    )


@mcp.tool()
async def desktop_map_app(
    window_json: str,
    max_depth: int = 2,
    max_windows: int = 25,
    include_tabs: bool = True,
    include_openers: bool = True,
    allow_destructive: bool = False,
    action_wait: float = 0.6,
    time_budget: float = 180.0,
    target: Optional[str] = None,
) -> str:
    """Map the whole application — the desktop twin of browser_map_site.

    From the given window, opens every menu item, every "…" button and every
    tab; each new window or dialog is mapped, crawled (down to max_depth) and
    closed again with Cancel/Close/Escape — never with OK or Save. Exit items
    are never selected; delete/remove/uninstall/format/reset items are skipped
    unless allow_destructive=True.

    Args:
        window_json: Main window locator (see desktop_map_window).
        max_depth: How many dialog levels to crawl below the main window (default: 2).
        max_windows: Stop after this many windows have been mapped (default: 25).
        include_tabs: Click each TabItem and record what it reveals (default: True).
        include_openers: Click buttons whose title ends with "…" (default: True).
        allow_destructive: Also select items that look destructive (default: False).
        action_wait: Seconds to wait after each action for the UI to settle (default: 0.6).
        time_budget: Overall time limit in seconds (default: 180).

    Returns:
        JSON: { root, windows: [{title, depth, reached_by, control_count, control_index, menus}],
                edges: [{from, via, to}], effects, tabs, skipped, feature_index: [{kind, name,
                window, reach, locator?, shortcut?}], coverage: {windows_mapped, dialogs_opened,
                controls_total, menu_paths, features_by_kind, actions_skipped, budget_exhausted,
                duration_ms}, problems, _polarix }
    """
    t0 = _start()
    try:
        window = _window_arg(window_json)
        if window is None:
            raise ValueError("window_json is required")
        from polarix.desktop.crawler import CrawlOptions, crawl_app

        driver = get_driver(target=target)
        result = await _run(
            crawl_app,
            driver,
            window,
            CrawlOptions(
                max_depth=max_depth,
                max_windows=max_windows,
                include_tabs=include_tabs,
                include_openers=include_openers,
                allow_destructive=allow_destructive,
                action_wait=action_wait,
                time_budget=time_budget,
            ),
        )
    except _TOOL_ERRORS as exc:
        return _fail("desktop_map_app", t0, exc, {"window_json": window_json})
    warnings = list(result.get("problems") or [])
    if result["coverage"]["budget_exhausted"]:
        warnings.append(
            "budget exhausted — raise max_windows or time_budget to go further"
        )
    return _wrap(
        result,
        _polarix(
            "desktop_map_app",
            t0,
            desktop=_desktop_state(driver, window),
            params={
                "max_depth": max_depth,
                "max_windows": max_windows,
                "include_tabs": include_tabs,
                "allow_destructive": allow_destructive,
            },
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def desktop_run_command(
    command: str,
    cwd: Optional[str] = None,
    timeout: float = 120.0,
    target: Optional[str] = None,
) -> str:
    """Run a shell command on the desktop target (inside the VM when the driver is remote).

    For setup and teardown around UI steps: install or remove an application
    (`winget install ...`), copy fixtures, clean files. Inside sequences and
    scenarios use the `shell` step, which fails unless the exit code matches.

    Args:
        command: Command line, run through the target's shell.
        cwd: Working directory on the target (optional).
        timeout: Seconds before the command is killed (default: 120).

    Returns:
        JSON: { exit_code, stdout, stderr, duration_ms, _polarix }
    """
    t0 = _start()
    try:
        driver = get_driver(target=target)
        outcome = await _run(driver.run_command, command, cwd=cwd, timeout=timeout)
    except _TOOL_ERRORS as exc:
        return _fail("desktop_run_command", t0, exc, {"command": command[:120]})
    return _wrap(
        {"success": outcome.get("exit_code") == 0, **outcome},
        _polarix(
            "desktop_run_command",
            t0,
            desktop=_desktop_state(driver, None),
            params={"command": command[:120], "timeout": timeout},
            warnings=(
                None
                if outcome.get("exit_code") == 0
                else [f"exit code {outcome.get('exit_code')}"]
            ),
        ),
    )


@mcp.tool()
async def desktop_launch(
    path: str,
    args: str = "",
    title_re: Optional[str] = None,
    wait_seconds: float = 3.0,
    target: Optional[str] = None,
) -> str:
    """Start an application on the desktop target and return its main window.

    Args:
        path: Executable path or command (e.g. "C:/Program Files/CadApp/CadApp.exe").
        args: Command-line arguments (optional).
        title_re: Regex to pick the main window when the app opens several.
        wait_seconds: Seconds to wait for the window after launch (default: 3.0).

    Returns:
        JSON: { window: {title, pid, handle, ...}, _polarix }
        Use window.handle (or title) as the window locator in later calls.
    """
    t0 = _start()
    try:
        driver = get_driver(target=target)
        info = await _run(
            driver.launch,
            path,
            args=args,
            wait_seconds=wait_seconds,
            title_re=title_re,
        )
    except _TOOL_ERRORS as exc:
        return _fail("desktop_launch", t0, exc, {"path": path, "args": args})
    window = (
        {"handle": info["handle"]} if info.get("handle") else {"title": info["title"]}
    )
    return _wrap(
        {"window": info, "window_locator": window},
        _polarix(
            "desktop_launch",
            t0,
            desktop=_desktop_state(driver, window),
            params={"path": path, "args": args, "title_re": title_re},
        ),
    )


@mcp.tool()
async def desktop_execute_sequence(
    steps_json: str,
    window_json: Optional[str] = None,
    stop_on_error: bool = True,
    target: Optional[str] = None,
) -> str:
    """Run a typed JSON action sequence against a window (deterministic, no LLM).

    Actions: launch · focus · click · double_click · right_click · set_text ·
    type · press · select · menu · wait_for · wait_idle · assert · click_image ·
    click_vision · shell · snapshot · screenshot · close.
    Locators: {"auto_id": ...} | {"title": ..., "control_type": ...} | {"path": "0/2/1"};
    click without a locator takes {"x", "y"} relative to the window (canvas fallback).

    Example:
      [{"action": "menu", "path": "Arquivo->Salvar"},
       {"action": "wait_for", "window": {"title_re": ".*Salvar como.*"}},
       {"action": "set_text", "locator": {"auto_id": "1001"}, "value": "obra.txt"},
       {"action": "click", "locator": {"auto_id": "1"}},
       {"action": "snapshot"}]

    Args:
        steps_json: JSON array of step objects.
        window_json: Starting window locator (optional when the sequence launches the app).
        stop_on_error: Stop on the first failed step (default: True).

    Returns:
        JSON: { steps_total, steps_succeeded, final_window, results: [per-step records], _polarix }
        Per-step: { step, action, success, duration_ms, locator_match_count, result, error }
    """
    t0 = _start()
    try:
        steps = _parse_json(steps_json, "steps_json")
        if not isinstance(steps, list):
            raise ValueError("steps_json must be a JSON array")
        window = _window_arg(window_json)
        driver = get_driver(target=target)
        results, warnings, final_window = await _run(
            run_desktop_steps, driver, window, steps, stop_on_error
        )
    except _TOOL_ERRORS as exc:
        return _fail("desktop_execute_sequence", t0, exc, {"window_json": window_json})
    return _wrap(
        {
            "steps_total": len(steps),
            "steps_succeeded": sum(1 for r in results if r["success"]),
            "final_window": final_window,
            "results": results,
        },
        _polarix(
            "desktop_execute_sequence",
            t0,
            desktop=_desktop_state(driver, final_window),
            params={"stop_on_error": stop_on_error, "steps_total": len(steps)},
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def desktop_auto_sequence(
    goal: str,
    window_json: str,
    model: Optional[str] = None,
    explore: bool = True,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> str:
    """Map First in one call for a desktop window: map → explore menus → plan via LLM → execute.

    The planner sees the full control_index and menu paths BEFORE writing the
    steps, then the steps run deterministically through desktop_execute_sequence.

    Args:
        goal: Objective in natural language (e.g. "save the document as obra.txt").
        window_json: Window locator (see desktop_map_window).
        model: LLM model id (default: BROWSER_USE_MODEL). Prefix "claude-" for Anthropic.
        explore: Expand menus to feed their items to the planner (default: True).
        dry_run: Return the generated steps without executing (default: False).

    Returns:
        JSON: { goal, map_summary, generated_steps, execution: {...}, _polarix }
    """
    t0 = _start()
    use_model = model or DEFAULT_MODEL
    warnings: list[str] = []
    try:
        window = _window_arg(window_json)
        if window is None:
            raise ValueError("window_json is required")
        driver = get_driver(target=target)
        data, _ = await _run(_map_window, driver, window, 8, True)
        menus = data.get("menus", [])
        if explore:
            try:
                menus = await _run(driver.menu_items, window, True)
            except Exception as exc:
                warnings.append(f"menu exploration failed: {exc}")
        map_context = {
            "window": data["window"],
            "control_index": data["control_index"],
            "menu_paths": [
                f"{m['title']}->{i}" for m in menus for i in m.get("items", [])
            ],
        }
        steps = await generate_desktop_steps(goal, map_context, use_model)
        if not steps:
            warnings.append("LLM returned no steps — check model and API key")
        payload: dict = {
            "goal": goal,
            "map_summary": {
                "control_count": data["control_count"],
                "control_types": sorted(data["control_index"].keys()),
                "menu_paths": len(map_context["menu_paths"]),
            },
            "generated_steps": steps,
        }
        final_window = window
        if dry_run:
            payload["dry_run"] = True
        else:
            results, step_warnings, final_window = await _run(
                run_desktop_steps, driver, window, steps, False
            )
            warnings.extend(step_warnings)
            payload["execution"] = {
                "steps_total": len(steps),
                "steps_succeeded": sum(1 for r in results if r["success"]),
                "final_window": final_window,
                "results": results,
            }
    except _TOOL_ERRORS as exc:
        return _fail("desktop_auto_sequence", t0, exc, {"goal": goal})
    return _wrap(
        payload,
        _polarix(
            "desktop_auto_sequence",
            t0,
            desktop=_desktop_state(driver, final_window),
            params={"model": use_model, "explore": explore, "dry_run": dry_run},
            warnings=warnings or None,
        ),
    )


# ════════════════════════════════════════════════════════════════════════
# VERIFICATION
# ════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def desktop_diff_window(
    window_json: str,
    steps_json: str,
    max_depth: int = 8,
    target: Optional[str] = None,
) -> str:
    """Snapshot a window, run steps, snapshot again and diff the control trees.

    Desktop twin of browser_diff_pages — asserts that an action produced the
    expected UI change (controls added/removed, texts or enabled state changed,
    windows opened or closed).

    Args:
        window_json: Window locator (see desktop_map_window).
        steps_json: JSON array of steps to run between the two snapshots.
        max_depth: Tree depth for both snapshots (default: 8).

    Returns:
        JSON: { added, removed, changed_texts, changed_state, windows_opened,
                windows_closed, summary, execution, _polarix }
    """
    t0 = _start()
    try:
        window = _window_arg(window_json)
        if window is None:
            raise ValueError("window_json is required")
        steps = _parse_json(steps_json, "steps_json")
        if not isinstance(steps, list):
            raise ValueError("steps_json must be a JSON array")
        driver = get_driver(target=target)

        def _work():
            before_windows = {w["title"] for w in driver.list_windows()}
            before = driver.inventory(window, max_depth)
            results, warns, final_window = run_desktop_steps(
                driver, window, steps, False
            )
            target = final_window or window
            try:
                after = driver.inventory(target, max_depth)
            except Exception:
                after = []
            after_windows = {w["title"] for w in driver.list_windows()}
            diff = diff_inventories(before, after)
            diff["windows_opened"] = sorted(after_windows - before_windows)
            diff["windows_closed"] = sorted(before_windows - after_windows)
            return diff, results, warns, final_window

        diff, results, warnings, final_window = await _run(_work)
    except _TOOL_ERRORS as exc:
        return _fail("desktop_diff_window", t0, exc, {"window_json": window_json})
    if not any(
        (
            diff["added"],
            diff["removed"],
            diff["changed_texts"],
            diff["changed_state"],
            diff["windows_opened"],
            diff["windows_closed"],
        )
    ):
        warnings.append("No differences detected between the two states")
    return _wrap(
        {
            **diff,
            "execution": {
                "steps_total": len(steps),
                "steps_succeeded": sum(1 for r in results if r["success"]),
                "results": results,
            },
        },
        _polarix(
            "desktop_diff_window",
            t0,
            desktop=_desktop_state(driver, final_window),
            params={"max_depth": max_depth, "steps_total": len(steps)},
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def desktop_screenshot(
    window_json: Optional[str] = None, target: Optional[str] = None
) -> str:
    """Capture a window (or the active one) as a base64 PNG.

    Args:
        window_json: Window locator; omit for the active window.

    Returns:
        JSON: { image: "data:image/png;base64,...", _polarix }
    """
    t0 = _start()
    try:
        window = _window_arg(window_json)
        driver = get_driver(target=target)
        png = await _run(driver.screenshot, window)
    except _TOOL_ERRORS as exc:
        return _fail("desktop_screenshot", t0, exc, {"window_json": window_json})
    return _wrap(
        {"image": _png_data_url(png)},
        _polarix("desktop_screenshot", t0, desktop=_desktop_state(driver, window)),
    )


# ════════════════════════════════════════════════════════════════════════
# MACROS — record once, replay deterministically
# ════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def desktop_record_start(
    name: str,
    window_json: Optional[str] = None,
    stop_key: str = "f10",
    target: Optional[str] = None,
) -> str:
    """Start recording real mouse/keyboard activity into a macro (needs pynput on the target).

    Clicks are resolved to the control under the cursor, so the macro uses
    locators instead of raw coordinates whenever the UI exposes the control.
    Stop with desktop_record_stop or by pressing `stop_key` on the target.

    Args:
        name: Macro name (saved on stop under POLARIX_MACROS_DIR).
        window_json: Window the macro is for (used to make coordinates relative).
        stop_key: Key that ends the recording from the keyboard (default: "f10").

    Returns:
        JSON: { recording: true, name, stop_key, _polarix }
    """
    t0 = _start()
    try:
        if name in ACTIVE and ACTIVE[name].status()["recording"]:
            raise ValueError(f"Recorder '{name}' is already running")
        window = _window_arg(window_json)
        driver = get_driver(target=target)
        rec = MacroRecorder(driver, window, name, stop_key=stop_key)
        await _run(rec.start)
        ACTIVE[name] = rec
    except _TOOL_ERRORS as exc:
        return _fail("desktop_record_start", t0, exc, {"name": name})
    return _wrap(
        {"recording": True, **rec.status()},
        _polarix("desktop_record_start", t0, desktop=_desktop_state(driver, window)),
    )


@mcp.tool()
async def desktop_record_stop(
    name: str, save: bool = True, target: Optional[str] = None
) -> str:
    """Stop a running recording; return (and by default save) the recorded steps.

    Args:
        name: Macro name given to desktop_record_start.
        save: Persist under POLARIX_MACROS_DIR/{name}.json (default: True).

    Returns:
        JSON: { name, steps, step_count, saved_to, _polarix }
    """
    t0 = _start()
    rec = ACTIVE.get(name)
    if rec is None:
        return _fail(
            "desktop_record_stop", t0, LookupError(f"No recorder named '{name}'")
        )
    steps = await _run(rec.stop)
    ACTIVE.pop(name, None)
    saved = None
    if save:
        saved = save_macro(
            name,
            steps,
            rec.window,
            source="recorded",
            meta={"started_at": rec.started_at, "stopped_at": rec.stopped_at},
        )["path"]
    return _wrap(
        {"name": name, "steps": steps, "step_count": len(steps), "saved_to": saved},
        _polarix(
            "desktop_record_stop",
            t0,
            desktop=_desktop_state(rec.driver, rec.window),
            params={"save": save},
        ),
    )


@mcp.tool()
async def desktop_macro_save(
    name: str,
    steps_json: str,
    window_json: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """Save a hand-written or generated step sequence as a named macro.

    Args:
        name: Macro name.
        steps_json: JSON array of steps (desktop_execute_sequence format).
        window_json: Window the macro targets (optional).

    Returns:
        JSON: { name, path, step_count, _polarix }
    """
    t0 = _start()
    try:
        steps = _parse_json(steps_json, "steps_json")
        if not isinstance(steps, list):
            raise ValueError("steps_json must be a JSON array")
        doc = save_macro(name, steps, _window_arg(window_json), source="manual")
    except _TOOL_ERRORS as exc:
        return _fail("desktop_macro_save", t0, exc, {"name": name})
    return _wrap(
        {"name": doc["name"], "path": doc["path"], "step_count": doc["step_count"]},
        _polarix("desktop_macro_save", t0, params={"macros_dir": macros_dir()}),
    )


@mcp.tool()
async def desktop_macro_list(target: Optional[str] = None) -> str:
    """List saved macros with step count, source (recorded/generated/manual) and window.

    Returns:
        JSON: { macros: [...], macros_dir, _polarix }
    """
    t0 = _start()
    macros = list_macros()
    return _wrap(
        {"macro_count": len(macros), "macros": macros, "macros_dir": macros_dir()},
        _polarix("desktop_macro_list", t0, params={"macros_dir": macros_dir()}),
    )


@mcp.tool()
async def desktop_macro_run(
    name: str,
    window_json: Optional[str] = None,
    stop_on_error: bool = True,
    target: Optional[str] = None,
) -> str:
    """Replay a saved macro (optionally against a different window).

    Args:
        name: Macro name.
        window_json: Override the window stored with the macro (optional).
        stop_on_error: Stop on the first failed step (default: True).

    Returns:
        JSON: { name, steps_total, steps_succeeded, final_window, results, _polarix }
    """
    t0 = _start()
    try:
        doc = load_macro(name)
        window = _window_arg(window_json) or doc.get("window")
        driver = get_driver(target=target)
        results, warnings, final_window = await _run(
            run_desktop_steps, driver, window, doc["steps"], stop_on_error
        )
    except _TOOL_ERRORS as exc:
        return _fail("desktop_macro_run", t0, exc, {"name": name})
    return _wrap(
        {
            "name": name,
            "steps_total": len(doc["steps"]),
            "steps_succeeded": sum(1 for r in results if r["success"]),
            "final_window": final_window,
            "results": results,
        },
        _polarix(
            "desktop_macro_run",
            t0,
            desktop=_desktop_state(driver, final_window),
            params={"stop_on_error": stop_on_error, "source": doc.get("source")},
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def desktop_macro_delete(name: str, target: Optional[str] = None) -> str:
    """Delete a saved macro.

    Returns:
        JSON: { name, deleted, _polarix }
    """
    t0 = _start()
    try:
        deleted = delete_macro(name)
    except ValueError as exc:
        return _fail("desktop_macro_delete", t0, exc, {"name": name})
    return _wrap(
        {"name": name, "deleted": deleted},
        _polarix("desktop_macro_delete", t0, params={"macros_dir": macros_dir()}),
    )
