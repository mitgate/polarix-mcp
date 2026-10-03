"""Test scenarios and suites — turn step sequences into pass/fail evidence.

A scenario is a step list with `assert` steps, plus optional setup/teardown
and a VM snapshot to restore first. A suite is a list of scenarios; its
report carries the KPIs the test team needs (pass rate, duration, failures)
and renders to a self-contained HTML page with failure screenshots.

Scenario (JSON or YAML)
  name: "Salvar projeto"
  description: "..."                      optional
  tags: ["cad", "smoke"]              optional
  window: {"title_re": ".*Editor.*"}      starting window
  vm: {"name": "win11", "snapshot": "clean", "backend": "libvirt"}   optional
  setup:    [steps]                       optional, failures mark the scenario "error"
  steps:    [steps incl. {"action": "assert", "kind": ..., ...}]
  teardown: [steps]                       optional, always attempted
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from typing import Any, Callable, Optional

from polarix.config import env_setting
from polarix.desktop.driver import DesktopDriver
from polarix.desktop.model import parse_window
from polarix.desktop.steps import _png_data_url, run_desktop_steps
from polarix.telemetry import _elapsed_ms, _start


def reports_dir() -> str:
    import tempfile

    return env_setting(
        "REPORTS_DIR", os.path.join(tempfile.gettempdir(), "polarix_reports")
    )


# -------------------------------------------------------------------- loading
def parse_scenario_text(text: str) -> Any:
    """JSON first (strict), then YAML. Returns a dict or a list of dicts."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("scenario text is empty")
    if stripped[0] in "{[":
        return json.loads(stripped)
    import yaml

    return yaml.safe_load(stripped)


def load_scenarios(source: str) -> list[dict]:
    """`source` may be inline JSON/YAML, a scenario file, or a directory of files."""
    if os.path.isdir(source):
        out: list[dict] = []
        for name in sorted(os.listdir(source)):
            if name.endswith((".json", ".yaml", ".yml")):
                out.extend(load_scenarios(os.path.join(source, name)))
        return out
    if os.path.isfile(source):
        with open(source, encoding="utf-8") as f:
            data = parse_scenario_text(f.read())
            if isinstance(data, dict) and "scenarios" in data:
                data = data["scenarios"]
            items = data if isinstance(data, list) else [data]
            for item in items:
                item.setdefault("name", os.path.splitext(os.path.basename(source))[0])
                item["_source"] = source
            return items
    data = parse_scenario_text(source)
    if isinstance(data, dict) and "scenarios" in data:
        data = data["scenarios"]
    return data if isinstance(data, list) else [data]


def validate_scenario(sc: dict) -> dict:
    if not isinstance(sc, dict):
        raise ValueError("scenario must be an object")
    if not sc.get("name"):
        raise ValueError("scenario needs a name")
    if not isinstance(sc.get("steps"), list) or not sc["steps"]:
        raise ValueError(f"scenario '{sc['name']}' needs a non-empty steps list")
    for key in ("setup", "teardown"):
        if key in sc and not isinstance(sc[key], list):
            raise ValueError(f"scenario '{sc['name']}': {key} must be a list")
    if sc.get("window") is not None:
        sc["window"] = parse_window(sc["window"])
    return sc


# -------------------------------------------------------------------- running
def _assert_summary(results: list[dict]) -> dict:
    asserts = [r for r in results if r["action"] == "assert"]
    return {
        "total": len(asserts),
        "passed": sum(1 for r in asserts if r["success"]),
        "failed": sum(1 for r in asserts if not r["success"]),
    }


def _restore_vm(sc: dict, vm_backend: Any) -> Optional[dict]:
    vm = sc.get("vm") or {}
    if not vm.get("name") or not vm.get("snapshot") or vm_backend is None:
        return None
    return vm_backend.snapshot_restore(vm["name"], vm["snapshot"])


def run_scenario(
    driver: DesktopDriver,
    scenario: dict,
    vm_backend: Any = None,
    screenshot_on_failure: bool = True,
) -> dict:
    sc = validate_scenario(dict(scenario))
    t0 = _start()
    window = sc.get("window")
    result: dict[str, Any] = {
        "name": sc["name"],
        "description": sc.get("description", ""),
        "tags": sc.get("tags", []),
        "target": sc.get("target"),
        "status": "passed",
        "phases": {},
        "warnings": [],
        "assertions": {"total": 0, "passed": 0, "failed": 0},
        "first_failure": None,
        "failure_screenshot": None,
        "vm_restore": None,
    }
    try:
        result["vm_restore"] = _restore_vm(sc, vm_backend)
    except Exception as exc:
        result.update(status="error", first_failure=f"vm restore: {exc}")
        result["duration_ms"] = _elapsed_ms(t0)
        return result

    def _phase(name: str, steps: list[dict], stop: bool) -> bool:
        if not steps:
            return True
        results, warns, new_window = run_desktop_steps(driver, window, steps, stop)
        result["phases"][name] = results
        result["warnings"].extend(f"{name}: {w}" for w in warns)
        failed = [r for r in results if not r["success"]]
        if failed and result["first_failure"] is None:
            f = failed[0]
            result["first_failure"] = (
                f"{name} step {f['step']} ({f['action']}): {f['error']}"
            )
            if screenshot_on_failure:
                try:
                    result["failure_screenshot"] = _png_data_url(
                        driver.screenshot(new_window or window)
                    )
                except Exception:
                    result["failure_screenshot"] = None
        return not failed

    if not _phase("setup", sc.get("setup", []), stop=True):
        result["status"] = "error"
    else:
        ok = _phase("steps", sc["steps"], stop=bool(sc.get("stop_on_error", True)))
        result["assertions"] = _assert_summary(result["phases"].get("steps", []))
        if not ok:
            result["status"] = "failed"
    _phase("teardown", sc.get("teardown", []), stop=False)

    result["steps_total"] = sum(len(v) for v in result["phases"].values())
    result["steps_succeeded"] = sum(
        1 for v in result["phases"].values() for r in v if r["success"]
    )
    result["duration_ms"] = _elapsed_ms(t0)
    return result


def expand_targets(scenarios: list[dict]) -> list[dict]:
    """A scenario with `targets: [a, b]` becomes one copy per target (name@target)."""
    out: list[dict] = []
    for sc in scenarios:
        targets = sc.get("targets")
        if isinstance(targets, list) and targets:
            for t in targets:
                copy_sc = {k: v for k, v in sc.items() if k != "targets"}
                copy_sc["target"] = t
                copy_sc["name"] = f"{sc.get('name', 'scenario')}@{t}"
                out.append(copy_sc)
        else:
            out.append(sc)
    return out


def run_suite(
    driver: DesktopDriver,
    scenarios: list[dict],
    vm_backend: Any = None,
    name: str = "suite",
    tags: Optional[list[str]] = None,
    driver_for: Optional[Callable[[dict], Any]] = None,
) -> dict:
    t0 = _start()
    selected = [
        sc for sc in scenarios if not tags or set(tags) & set(sc.get("tags", []))
    ]
    results = [
        run_scenario(driver_for(sc) if driver_for else driver, sc, vm_backend)
        for sc in selected
    ]
    counts = {
        s: sum(1 for r in results if r["status"] == s)
        for s in ("passed", "failed", "error")
    }
    total = len(results)
    asserts_total = sum(r["assertions"]["total"] for r in results)
    asserts_passed = sum(r["assertions"]["passed"] for r in results)
    return {
        "name": name,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kpis": {
            "scenarios": total,
            **counts,
            "pass_rate": round(counts["passed"] / total, 4) if total else None,
            "assertions_total": asserts_total,
            "assertions_passed": asserts_passed,
            "assertion_pass_rate": (
                round(asserts_passed / asserts_total, 4) if asserts_total else None
            ),
            "duration_ms": _elapsed_ms(t0),
            "mean_scenario_ms": (
                round(sum(r["duration_ms"] for r in results) / total) if total else 0
            ),
            "slowest": (
                max(results, key=lambda r: r["duration_ms"])["name"]
                if results
                else None
            ),
            "healed_locators": sum(
                1
                for r in results
                for v in r["phases"].values()
                for s in v
                if s.get("healed_locator")
            ),
        },
        "failures": [
            {
                "name": r["name"],
                "status": r["status"],
                "first_failure": r["first_failure"],
            }
            for r in results
            if r["status"] != "passed"
        ],
        "results": results,
    }


# ------------------------------------------------------------ macro → scenario
def scenario_from_macro(macro: dict, name: Optional[str] = None) -> dict:
    """Wrap a recorded macro as a scenario and add safe default assertions.

    Clicks that carry a `_recorded` hint become `control_exists` checks before
    the click, so a renamed button fails loudly instead of clicking the void.
    The scenario ends with `window_exists` for the macro's window.
    """
    steps: list[dict] = []
    for step in macro.get("steps", []):
        hint = step.get("_recorded") or {}
        if step.get("action") in ("click", "double_click", "right_click") and step.get(
            "locator"
        ):
            steps.append(
                {
                    "action": "assert",
                    "kind": "control_exists",
                    "locator": step["locator"],
                    "_recorded": hint,
                    "message": f"control for '{hint.get('title') or step['locator']}' is present",
                }
            )
        steps.append(step)
    if steps and steps[-1].get("action") == "snapshot":
        steps.pop()
    steps.append({"action": "assert", "kind": "window_exists"})
    return {
        "name": name or f"macro-{macro.get('name', 'unnamed')}",
        "description": f"Generated from macro '{macro.get('name')}' ({macro.get('source')})",
        "tags": ["macro"],
        "window": macro.get("window"),
        "steps": steps,
    }


# ------------------------------------------------------------------ reporting
def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "report"


def save_report(report: dict, name: Optional[str] = None) -> dict:
    os.makedirs(reports_dir(), exist_ok=True)
    base = _safe(name or report.get("name", "suite"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = os.path.join(reports_dir(), f"{base}-{stamp}.json")
    html_path = os.path.join(reports_dir(), f"{base}-{stamp}.html")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html_report(report))
    return {"json": json_path, "html": html_path}


def render_html_report(report: dict) -> str:
    k = report.get("kpis", {})
    rows = []
    for r in report.get("results", []):
        color = {"passed": "#2e7d32", "failed": "#c62828", "error": "#ef6c00"}.get(
            r["status"], "#555"
        )
        shot = (
            f'<details><summary>failure screenshot</summary><img src="{r["failure_screenshot"]}" '
            f'style="max-width:100%;border:1px solid #ccc"></details>'
            if r.get("failure_screenshot")
            else ""
        )
        warn = (
            "<ul>"
            + "".join(f"<li>{html.escape(w)}</li>" for w in r["warnings"])
            + "</ul>"
            if r.get("warnings")
            else ""
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(r['name'])}<br><small>{html.escape(r.get('description', ''))}</small></td>"
            f'<td style="color:{color};font-weight:bold">{r["status"]}</td>'
            f"<td>{r['assertions']['passed']}/{r['assertions']['total']}</td>"
            f"<td>{r.get('steps_succeeded', 0)}/{r.get('steps_total', 0)}</td>"
            f"<td>{r['duration_ms']} ms</td>"
            f"<td>{html.escape(r['first_failure'] or '')}{warn}{shot}</td>"
            "</tr>"
        )
    kpi_cells = "".join(
        f"<div class=kpi><div class=v>{html.escape(str(v))}</div><div class=l>{html.escape(str(key))}</div></div>"
        for key, v in k.items()
    )
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Polarix — {html.escape(report.get('name', 'suite'))}</title>
<style>
body{{font-family:system-ui,Segoe UI,Arial;margin:24px;color:#222}}
.kpis{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}}
.kpi{{border:1px solid #ddd;border-radius:8px;padding:10px 14px;min-width:120px;background:#fafafa}}
.kpi .v{{font-size:22px;font-weight:600}} .kpi .l{{font-size:12px;color:#666}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #e3e3e3;padding:8px;text-align:left;vertical-align:top}}
th{{background:#f3f3f3}} small{{color:#666}} ul{{margin:4px 0 0 16px;padding:0;font-size:12px;color:#8a6d00}}
</style></head><body>
<h1>Polarix test report — {html.escape(report.get('name', 'suite'))}</h1>
<p>started {html.escape(report.get('started_at', ''))}</p>
<div class=kpis>{kpi_cells}</div>
<table><thead><tr><th>Scenario</th><th>Status</th><th>Assertions</th><th>Steps</th><th>Duration</th><th>Details</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""
