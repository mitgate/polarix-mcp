"""Environment tools — decide how a test environment lives between runs.

ENVIRONMENTS  environment_list · environment_status · environment_plan ·
              environment_prepare · environment_run · environment_teardown

An environment = target (VM / device) + install / uninstall / reset steps +
variables. A run chooses a lifecycle policy (fresh · reinstall · reset · keep)
and whether to keep the environment up afterwards. When the policy is not
given and the environment has no default, the tools return `needs_decision`
with the options — the agent must ask the person, never pick silently.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Optional

from polarix.desktop import environments as envs
from polarix.desktop.driver import DesktopUnavailable, get_driver
from polarix.desktop.scenarios import (
    expand_matrix,
    load_scenarios,
    run_suite,
    save_report,
    validate_scenario,
)
from polarix.server import mcp
from polarix.telemetry import _polarix, _start, _wrap
from polarix.tools.desktop import _fail

_ERRORS = (DesktopUnavailable, LookupError, TimeoutError, ValueError, OSError)

PHASES = {
    "fresh": ["vm.snapshot_restore (if target.vm.snapshot)", "install", "run"],
    "reinstall": ["uninstall", "install", "run"],
    "reset": ["reset", "run"],
    "keep": ["run"],
}


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


def _vm_factory():
    from polarix.vm.backends import get_backend

    return get_backend


def _plan(name: str, policy: Optional[str], keep_after: Optional[bool]) -> dict:
    env = envs.resolve(name)
    pol, keep, decision = envs.choose(name, env, policy, keep_after)
    if decision:
        return decision
    phases = list(PHASES[pol])
    if not keep:
        phases += ["uninstall", "vm.snapshot_restore (if target.vm.snapshot)"]
    return {
        "needs_decision": False,
        "environment": name,
        "target": env["target"],
        "policy": pol,
        "keep_after": keep,
        "phases": phases,
        "variables": env["variables"],
        "state": envs.status(name),
    }


# ════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def environment_list() -> str:
    """List the configured test environments and whether each one is up.

    An environment binds a target (VM or device) to the steps that install,
    uninstall and reset the software under test, plus variables (`${app}`)
    that scenarios can use. Configure them in POLARIX_ENVIRONMENTS_FILE
    (~/.config/polarix/environments.json) or inline in POLARIX_ENVIRONMENTS:

      {"win11-editor": {"target": "win11",
         "install":   [{"action": "shell", "command": "winget install --id Example.Editor -e"}],
         "uninstall": [{"action": "shell", "command": "winget uninstall --id Example.Editor"}],
         "reset":     [{"action": "shell", "command": "del /q %APPDATA%\\\\Editor\\\\*"}],
         "variables": {"app": "C:/Program Files/Editor/editor.exe"}}}

    Returns:
        JSON: { environments: [{name, target, install_steps, uninstall_steps, reset_steps,
                variables, default_policy, default_keep_after, prepared, runs}],
                policies, config_file, state_file, _polarix }
    """
    t0 = _start()
    try:
        items = await _run(envs.describe)
    except (*_ERRORS, json.JSONDecodeError) as exc:
        return _fail("environment_list", t0, exc)
    return _wrap(
        {
            "environments": items,
            "policies": {
                "fresh": "restore the clean snapshot, install, run (installer tests, clean room)",
                "reinstall": "keep the VM, uninstall + install the app, run (new build)",
                "reset": "keep VM and app, clear the app's data, run (regression)",
                "keep": "touch nothing, run (fast iteration)",
            },
            "keep_after": "true leaves everything up for the next run; false uninstalls and reverts the snapshot (one-shot)",
            "config_file": envs.environments_file(),
            "state_file": envs.state_file(),
        },
        _polarix("environment_list", t0),
    )


@mcp.tool()
async def environment_status(name: str) -> str:
    """Is this environment up? Since when, under which policy, how many runs.

    Returns:
        JSON: { environment, prepared, prepared_at, last_policy, runs, last_run_at, target, _polarix }
    """
    t0 = _start()
    try:
        envs.resolve(name)
        st = await _run(envs.status, name)
    except (*_ERRORS, json.JSONDecodeError) as exc:
        return _fail("environment_status", t0, exc, {"name": name})
    return _wrap(st, _polarix("environment_status", t0))


@mcp.tool()
async def environment_plan(
    name: str, policy: Optional[str] = None, keep_after: Optional[bool] = None
) -> str:
    """Show what a run would do to the environment — or the question to ask first.

    Call this BEFORE environment_prepare / environment_run when the person has not
    said how to treat the environment. If `policy` is missing (and the environment
    has no default_policy) the answer is {"needs_decision": true, "question", "options"}:
    present the options to the person, then call again with their choice. If the
    policy is set, the answer lists the phases that will run.

    Args:
        name: Environment name (see environment_list).
        policy: fresh | reinstall | reset | keep.
        keep_after: Leave the environment up after the run (default: True).

    Returns:
        JSON: { needs_decision, question?, options?, policy?, keep_after?, phases?, state, _polarix }
    """
    t0 = _start()
    try:
        plan = await _run(_plan, name, policy, keep_after)
    except (*_ERRORS, json.JSONDecodeError) as exc:
        return _fail("environment_plan", t0, exc, {"name": name})
    return _wrap(plan, _polarix("environment_plan", t0))


@mcp.tool()
async def environment_prepare(
    name: str, policy: Optional[str] = None, keep_after: Optional[bool] = None
) -> str:
    """Bring the environment to the state the policy asks for, without running tests.

    fresh → restore the target's VM snapshot (if configured) and run `install`;
    reinstall → `uninstall` then `install`; reset → `reset`; keep → nothing.
    Without a policy the tool refuses with `needs_decision` — ask the person.
    After a successful prepare the environment is marked prepared and `keep`/`reset`
    runs become possible.

    Returns:
        JSON: { ok, environment, policy, phases: {install|uninstall|reset|snapshot_restore: {...}},
                state, _polarix }
    """
    t0 = _start()
    try:
        plan = await _run(_plan, name, policy, keep_after)
        if plan.get("needs_decision"):
            return _wrap(plan, _polarix("environment_prepare", t0))
        env = envs.resolve(name)
        driver = get_driver(target=env["target"])
        report = await _run(envs.prepare, name, driver, plan["policy"], _vm_factory())
        report["state"] = envs.status(name)
    except (*_ERRORS, json.JSONDecodeError) as exc:
        return _fail("environment_prepare", t0, exc, {"name": name, "policy": policy})
    return _wrap(
        report, _polarix("environment_prepare", t0, params={"policy": plan["policy"]})
    )


@mcp.tool()
async def environment_run(
    name: str,
    source: str,
    policy: Optional[str] = None,
    keep_after: Optional[bool] = None,
    suite_name: str = "suite",
    tags_json: Optional[str] = None,
    variables_json: Optional[str] = None,
    include_results: bool = False,
) -> str:
    """Prepare the environment, run a suite in it, then keep or tear it down.

    The whole lifecycle in one call: prepare(policy) → run_suite → (keep_after is
    false → uninstall + snapshot restore). Scenarios receive the environment's
    `variables` (plus `variables_json`) and may use `${app}`, `${browser.path}`…
    in any step. A scenario with `matrix: {"browser": [...]}` runs once per value —
    that is how one test runs across several browsers installed in the same VM.

    Without a policy (and no default_policy on the environment) nothing runs and the
    answer is `needs_decision` with the options to show the person.

    Args:
        name: Environment name.
        source: Scenario directory, file, or inline JSON/YAML (same as desktop_run_suite).
        policy: fresh | reinstall | reset | keep.
        keep_after: Leave the environment up afterwards (default: True).
        suite_name: Report name.
        tags_json: JSON array of tags to select scenarios.
        variables_json: Extra `${var}` values for this run (JSON object).
        include_results: Include per-scenario results (default: False).

    Returns:
        JSON: { needs_decision? | environment, policy, keep_after, prepare, kpis, failures,
                teardown?, report, state, _polarix }
    """
    t0 = _start()
    try:
        plan = await _run(_plan, name, policy, keep_after)
        if plan.get("needs_decision"):
            return _wrap(plan, _polarix("environment_run", t0))
        env = envs.resolve(name)
        extra = json.loads(variables_json) if variables_json else {}
        variables = {**env["variables"], **extra}
        scenarios = [
            validate_scenario(sc)
            for sc in expand_matrix(load_scenarios(source), variables)
        ]
        if not scenarios:
            raise ValueError("no scenarios found")
        for sc in scenarios:
            sc.setdefault("target", env["target"])
        driver = get_driver(target=env["target"])
        tags = json.loads(tags_json) if tags_json else None
        payload: dict[str, Any] = {
            "environment": name,
            "policy": plan["policy"],
            "keep_after": plan["keep_after"],
        }
        payload["prepare"] = await _run(
            envs.prepare, name, driver, plan["policy"], _vm_factory()
        )
        if not payload["prepare"]["ok"]:
            payload["error"] = "prepare failed; suite not run"
            payload["state"] = envs.status(name)
            return _wrap(payload, _polarix("environment_run", t0))
        report = await _run(
            run_suite,
            driver,
            scenarios,
            None,
            suite_name,
            tags,
            lambda sc: get_driver(target=sc.get("target") or env["target"]),
        )
        envs.record_run(name)
        payload.update(
            kpis=report["kpis"],
            failures=report["failures"],
            report=save_report(report, suite_name),
        )
        if include_results:
            payload["results"] = report["results"]
        if not plan["keep_after"]:
            payload["teardown"] = await _run(
                envs.tear_down, name, driver, "both", _vm_factory()
            )
        payload["state"] = envs.status(name)
    except (*_ERRORS, json.JSONDecodeError) as exc:
        return _fail("environment_run", t0, exc, {"name": name, "policy": policy})
    return _wrap(
        payload,
        _polarix(
            "environment_run",
            t0,
            params={"policy": plan["policy"], "keep_after": plan["keep_after"]},
        ),
    )


@mcp.tool()
async def environment_teardown(name: str, mode: str = "both") -> str:
    """Take the environment down: uninstall the software, revert the VM, or both.

    Args:
        name: Environment name.
        mode: uninstall (run `uninstall` steps) · snapshot (revert the target's VM to its
            snapshot) · both (default) · none (only mark it as not prepared).

    Returns:
        JSON: { ok, environment, mode, phases, state, _polarix }
    """
    t0 = _start()
    try:
        if mode not in ("uninstall", "snapshot", "both", "none"):
            raise ValueError("mode must be uninstall | snapshot | both | none")
        env = envs.resolve(name)
        driver = (
            get_driver(target=env["target"]) if mode in ("uninstall", "both") else None
        )
        report = await _run(envs.tear_down, name, driver, mode, _vm_factory())
        report["state"] = envs.status(name)
    except (*_ERRORS, json.JSONDecodeError) as exc:
        return _fail("environment_teardown", t0, exc, {"name": name, "mode": mode})
    return _wrap(report, _polarix("environment_teardown", t0, params={"mode": mode}))
