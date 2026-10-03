"""Test environments — build once and keep, or build, test and throw away.

An environment binds a target (machine / OS) to three layers:

  infrastructure  the VM and its baseline snapshot        (target.vm.snapshot)
  system          what gets installed on it: the app under test, browsers, fixtures
                  (`install` / `uninstall` steps)
  state           the app's data between runs             (`reset` steps)

A run picks a lifecycle policy:

  fresh      restore the snapshot, run `install`, test      — installer tests, clean room
  reinstall  keep the VM, `uninstall` + `install`, test     — new build of the app
  reset      keep VM and app, run `reset`, test             — regression on develop
  keep       touch nothing, test                            — iterate quickly

and `keep_after`: True leaves everything up for the next run; False runs
`uninstall` and reverts the snapshot (one-shot).

When a run involves an environment and no policy was given, the tools do not
guess: they return `needs_decision` with the options and a recommendation so
the agent can ask the person. `default_policy` / `default_keep_after` in the
environment config turn that off.

Config: POLARIX_ENVIRONMENTS_FILE (default ~/.config/polarix/environments.json)
and/or POLARIX_ENVIRONMENTS (inline JSON):

  {"win11-editor": {
     "target": "win11",
     "install":   [{"action": "shell", "command": "winget install --id Example.Editor -e"}],
     "uninstall": [{"action": "shell", "command": "winget uninstall --id Example.Editor"}],
     "reset":     [{"action": "shell", "command": "del /q %APPDATA%\\Editor\\*"}],
     "variables": {"app": "C:/Program Files/Editor/editor.exe"},
     "default_policy": null, "default_keep_after": null}}

The built-in `sim` environment (target `fake`) is always available for trying the
flow without a VM; a file/inline entry with the same name overrides it.

State (which environments are up, since when, how many runs) lives in
POLARIX_ENV_STATE (default $TMP/polarix_env_state.json).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Optional

from polarix.config import env_setting
from polarix.desktop.steps import run_desktop_steps

POLICIES = ("fresh", "reinstall", "reset", "keep")

# Always present, like the built-in `fake` target: lets anyone try the lifecycle
# flow (decision → prepare → run → teardown) on the simulated app, no VM needed.
BUILTIN_ENVIRONMENTS: dict[str, dict] = {
    "sim": {
        "target": "fake",
        "install": [{"action": "shell", "command": "echo install simulated editor"}],
        "uninstall": [
            {"action": "shell", "command": "echo uninstall simulated editor"}
        ],
        "reset": [{"action": "shell", "command": "echo reset simulated editor data"}],
        "variables": {"app": "C:/Simulated/editor.exe"},
    }
}


# ------------------------------------------------------------------ config
def environments_file() -> str:
    return env_setting(
        "ENVIRONMENTS_FILE",
        os.path.join(
            os.path.expanduser("~"), ".config", "polarix", "environments.json"
        ),
    )


def state_file() -> str:
    return env_setting(
        "ENV_STATE", os.path.join(tempfile.gettempdir(), "polarix_env_state.json")
    )


def _validate(name: str, cfg: Any) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError(f"environment '{name}' must be an object")
    if not cfg.get("target"):
        raise ValueError(f"environment '{name}' needs a target")
    for key in ("install", "uninstall", "reset"):
        if key in cfg and not isinstance(cfg[key], list):
            raise ValueError(f"environment '{name}': {key} must be a list of steps")
    pol = cfg.get("default_policy")
    if pol is not None and pol not in POLICIES:
        raise ValueError(
            f"environment '{name}': default_policy must be one of {POLICIES}"
        )
    out = dict(cfg)
    out.setdefault("install", [])
    out.setdefault("uninstall", [])
    out.setdefault("reset", [])
    out.setdefault("variables", {})
    return out


def load_environments() -> dict[str, dict]:
    envs: dict[str, dict] = {
        name: _validate(name, cfg) for name, cfg in BUILTIN_ENVIRONMENTS.items()
    }
    path = environments_file()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for name, cfg in (json.load(f) or {}).items():
                envs[name] = _validate(name, cfg)
    inline = env_setting("ENVIRONMENTS", "").strip()
    if inline:
        for name, cfg in json.loads(inline).items():
            envs[name] = _validate(name, cfg)
    return envs


def resolve(name: str) -> dict:
    envs = load_environments()
    if name not in envs:
        raise LookupError(
            f"unknown environment '{name}'. Known: {sorted(envs)}. "
            f"Add it to {environments_file()} or POLARIX_ENVIRONMENTS."
        )
    return envs[name]


# ------------------------------------------------------------------- state
def load_state() -> dict:
    path = state_file()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f) or {}


def _save_state(state: dict) -> None:
    path = state_file()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def status(name: str) -> dict:
    st = load_state().get(name) or {}
    return {
        "environment": name,
        "prepared": bool(st.get("prepared")),
        "prepared_at": st.get("prepared_at"),
        "last_policy": st.get("last_policy"),
        "runs": int(st.get("runs", 0)),
        "last_run_at": st.get("last_run_at"),
        "target": st.get("target"),
    }


def _update_state(name: str, **fields: Any) -> dict:
    state = load_state()
    entry = state.get(name) or {}
    entry.update(fields)
    state[name] = entry
    _save_state(state)
    return entry


# --------------------------------------------------------------- decisions
def decision(name: str, env: dict, reason: str = "no policy given") -> dict:
    st = status(name)
    prepared = st["prepared"]
    options = [
        {
            "policy": "keep",
            "keep_after": True,
            "available": prepared,
            "label": "Keep everything and just run (fastest; the app and its data stay as they are)",
        },
        {
            "policy": "reset",
            "keep_after": True,
            "available": prepared and bool(env.get("reset")),
            "label": "Keep the VM and the app, clear the app's data first (regression on a known build)",
        },
        {
            "policy": "reinstall",
            "keep_after": True,
            "available": bool(env.get("install")),
            "label": "Keep the VM, uninstall and reinstall the app (new build), then run",
        },
        {
            "policy": "fresh",
            "keep_after": True,
            "available": True,
            "label": "Restore the clean snapshot, install everything, run, and leave it up for more runs",
        },
        {
            "policy": "fresh",
            "keep_after": False,
            "available": True,
            "label": "Restore the clean snapshot, install, run, then uninstall and revert (one-shot, e.g. installer tests)",
        },
    ]
    recommended = "keep" if prepared else "fresh"
    for opt in options:
        opt["recommended"] = opt["policy"] == recommended and opt["keep_after"]
    return {
        "needs_decision": True,
        "environment": name,
        "reason": reason,
        "state": st,
        "question": (
            f"Environment '{name}' is {'already up' if prepared else 'not prepared'}. "
            "How should this run treat it? (pass `policy` and `keep_after` to the tool)"
        ),
        "options": [o for o in options if o["available"]],
    }


def choose(
    name: str, env: dict, policy: Optional[str], keep_after: Optional[bool]
) -> tuple[Optional[str], Optional[bool], Optional[dict]]:
    """Resolve policy/keep_after from arguments and config; None → decision needed."""
    pol = policy or env.get("default_policy")
    keep = keep_after if keep_after is not None else env.get("default_keep_after")
    if pol is None:
        return None, None, decision(name, env)
    if pol not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    if keep is None:
        keep = True
    st = status(name)
    if pol in ("keep", "reset") and not st["prepared"]:
        return (
            None,
            None,
            decision(
                name,
                env,
                reason=f"policy '{pol}' needs a prepared environment and '{name}' is not up",
            ),
        )
    return pol, bool(keep), None


# ---------------------------------------------------------------- lifecycle
def _run_phase(driver, env: dict, steps: list[dict], window: Optional[dict]) -> dict:
    if not steps:
        return {"steps_total": 0, "steps_succeeded": 0, "results": []}
    results, warnings, _ = run_desktop_steps(driver, window, steps, stop_on_error=True)
    return {
        "steps_total": len(steps),
        "steps_succeeded": sum(1 for r in results if r["success"]),
        "results": results,
        "warnings": warnings,
        "ok": all(r["success"] for r in results),
    }


def _vm_of(env: dict) -> Optional[dict]:
    from polarix.desktop.targets import resolve as resolve_target

    cfg = resolve_target(env["target"])
    vm = cfg.get("vm") or {}
    return vm if vm.get("name") else None


def prepare(name: str, driver, policy: str, vm_backend_factory=None) -> dict:
    """Bring the environment to the state the policy asks for. Returns a report."""
    env = resolve(name)
    report: dict[str, Any] = {"environment": name, "policy": policy, "phases": {}}
    vm = _vm_of(env)
    if policy == "fresh":
        if vm and vm.get("snapshot") and vm_backend_factory is not None:
            backend = vm_backend_factory(vm.get("backend"))
            report["phases"]["snapshot_restore"] = backend.snapshot_restore(
                vm["name"], vm["snapshot"]
            )
        report["phases"]["install"] = _run_phase(driver, env, env["install"], None)
    elif policy == "reinstall":
        report["phases"]["uninstall"] = _run_phase(driver, env, env["uninstall"], None)
        report["phases"]["install"] = _run_phase(driver, env, env["install"], None)
    elif policy == "reset":
        report["phases"]["reset"] = _run_phase(driver, env, env["reset"], None)
    ok = all(
        p.get("ok", True) for p in report["phases"].values() if isinstance(p, dict)
    )
    report["ok"] = ok
    if ok:
        _update_state(
            name,
            prepared=True,
            prepared_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            last_policy=policy,
            target=env["target"],
        )
    return report


def record_run(name: str) -> None:
    st = load_state().get(name) or {}
    _update_state(
        name,
        runs=int(st.get("runs", 0)) + 1,
        last_run_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def tear_down(
    name: str, driver, mode: str = "uninstall", vm_backend_factory=None
) -> dict:
    """mode: uninstall (run uninstall steps) | snapshot (revert VM) | both | none."""
    env = resolve(name)
    report: dict[str, Any] = {"environment": name, "mode": mode, "phases": {}}
    if mode in ("uninstall", "both"):
        report["phases"]["uninstall"] = _run_phase(driver, env, env["uninstall"], None)
    vm = _vm_of(env)
    if (
        mode in ("snapshot", "both")
        and vm
        and vm.get("snapshot")
        and vm_backend_factory is not None
    ):
        backend = vm_backend_factory(vm.get("backend"))
        report["phases"]["snapshot_restore"] = backend.snapshot_restore(
            vm["name"], vm["snapshot"]
        )
    if mode != "none":
        _update_state(
            name, prepared=False, torn_down_at=time.strftime("%Y-%m-%dT%H:%M:%S")
        )
    report["ok"] = all(
        p.get("ok", True) for p in report["phases"].values() if isinstance(p, dict)
    )
    return report


def describe() -> list[dict]:
    out = []
    for name, env in load_environments().items():
        st = status(name)
        out.append(
            {
                "name": name,
                "target": env["target"],
                "install_steps": len(env["install"]),
                "uninstall_steps": len(env["uninstall"]),
                "reset_steps": len(env["reset"]),
                "variables": sorted(env["variables"]),
                "default_policy": env.get("default_policy"),
                "default_keep_after": env.get("default_keep_after"),
                "prepared": st["prepared"],
                "runs": st["runs"],
            }
        )
    return out
