"""Environments: lifecycle policies, decisions, matrix variables — on the fake driver."""

from __future__ import annotations

import json

import pytest

from polarix.desktop import environments as envs
from polarix.desktop.driver import get_driver
from polarix.desktop.scenarios import expand_matrix, substitute


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("POLARIX_TARGETS_FILE", "/nonexistent/targets.json")
    monkeypatch.delenv("POLARIX_TARGETS", raising=False)
    monkeypatch.delenv("POLARIX_TARGET", raising=False)
    monkeypatch.setenv("POLARIX_ENVIRONMENTS_FILE", "/nonexistent/environments.json")
    monkeypatch.setenv("POLARIX_ENV_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv(
        "POLARIX_ENVIRONMENTS",
        json.dumps(
            {
                "sim": {
                    "target": "fake",
                    "install": [{"action": "shell", "command": "echo install"}],
                    "uninstall": [{"action": "shell", "command": "echo uninstall"}],
                    "reset": [{"action": "shell", "command": "echo reset"}],
                    "variables": {"app": "C:/sim/editor.exe", "browser": {"name": "x"}},
                },
                "broken": {
                    "target": "fake",
                    "install": [{"action": "shell", "command": "exit 1"}],
                },
                "auto": {
                    "target": "fake",
                    "default_policy": "keep",
                    "default_keep_after": False,
                },
            }
        ),
    )
    return envs


def test_validate_rejects_bad_config(monkeypatch):
    monkeypatch.setenv("POLARIX_ENVIRONMENTS_FILE", "/nonexistent/x.json")
    monkeypatch.setenv("POLARIX_ENVIRONMENTS", '{"a": {"install": []}}')
    with pytest.raises(ValueError, match="needs a target"):
        envs.load_environments()
    monkeypatch.setenv(
        "POLARIX_ENVIRONMENTS", '{"a": {"target": "t", "default_policy": "x"}}'
    )
    with pytest.raises(ValueError, match="default_policy"):
        envs.load_environments()
    monkeypatch.setenv("POLARIX_ENVIRONMENTS", '{"a": {"target": "t", "reset": "no"}}')
    with pytest.raises(ValueError, match="must be a list"):
        envs.load_environments()


def test_no_policy_means_decision_not_a_guess(env):
    cfg = envs.resolve("sim")
    pol, keep, decision = envs.choose("sim", cfg, None, None)
    assert pol is None and keep is None and decision["needs_decision"] is True
    policies = [(o["policy"], o["keep_after"]) for o in decision["options"]]
    # not prepared yet → keep/reset are not offered, fresh is recommended
    assert ("keep", True) not in policies and ("reset", True) not in policies
    assert ("fresh", True) in policies and ("fresh", False) in policies
    assert [o for o in decision["options"] if o["recommended"]][0]["policy"] == "fresh"
    # asking for keep on a cold environment is also a decision, with the reason
    _, _, d2 = envs.choose("sim", cfg, "keep", True)
    assert d2["needs_decision"] and "not up" in d2["reason"]
    with pytest.raises(ValueError, match="policy must be"):
        envs.choose("sim", cfg, "bogus", True)


def test_defaults_skip_the_question(env):
    cfg = envs.resolve("auto")
    envs.prepare("auto", get_driver(target="fake"), "fresh")
    pol, keep, decision = envs.choose("auto", cfg, None, None)
    assert (pol, keep, decision) == ("keep", False, None)


def test_prepare_runs_phases_and_marks_state(env):
    drv = get_driver(target="fake")
    rep = envs.prepare("sim", drv, "fresh")
    assert rep["ok"] and list(rep["phases"]) == ["install"]
    assert rep["phases"]["install"]["steps_succeeded"] == 1
    st = envs.status("sim")
    assert st["prepared"] and st["last_policy"] == "fresh" and st["target"] == "fake"
    # now keep and reset become available and recommended = keep
    d = envs.decision("sim", envs.resolve("sim"))
    assert [o for o in d["options"] if o["recommended"]][0]["policy"] == "keep"
    rep = envs.prepare("sim", drv, "reinstall")
    assert list(rep["phases"]) == ["uninstall", "install"] and rep["ok"]
    rep = envs.prepare("sim", drv, "reset")
    assert list(rep["phases"]) == ["reset"] and rep["ok"]
    rep = envs.prepare("sim", drv, "keep")
    assert rep["phases"] == {} and rep["ok"]
    envs.record_run("sim")
    envs.record_run("sim")
    assert envs.status("sim")["runs"] == 2
    rep = envs.tear_down("sim", drv, "uninstall")
    assert rep["ok"] and not envs.status("sim")["prepared"]


def test_failed_install_does_not_mark_prepared(env):
    rep = envs.prepare("broken", get_driver(target="fake"), "fresh")
    assert rep["ok"] is False and envs.status("broken")["prepared"] is False


def test_fresh_restores_snapshot_when_target_has_vm(env, monkeypatch):
    monkeypatch.setenv(
        "POLARIX_TARGETS",
        '{"vmfake": {"driver": "fake", "vm": {"name": "win11", "snapshot": "clean", "backend": "libvirt"}}}',
    )
    monkeypatch.setenv(
        "POLARIX_ENVIRONMENTS",
        '{"e": {"target": "vmfake", "install": [], "uninstall": []}}',
    )
    calls = []

    class Backend:
        def snapshot_restore(self, vm, snap):
            calls.append((vm, snap))
            return {"ok": True, "vm": vm, "snapshot": snap}

    drv = get_driver(target="vmfake")
    rep = envs.prepare("e", drv, "fresh", vm_backend_factory=lambda b: Backend())
    assert calls == [("win11", "clean")] and rep["ok"]
    rep = envs.tear_down("e", drv, "both", vm_backend_factory=lambda b: Backend())
    assert calls[-1] == ("win11", "clean") and "snapshot_restore" in rep["phases"]
    assert envs.describe()[0]["target"] == "vmfake"


def test_substitute_and_matrix():
    vars_ = {"app": "C:/e.exe", "browser": {"name": "chrome", "path": "C:/c.exe"}}
    assert substitute("${app}", vars_) == "C:/e.exe"
    assert substitute("${browser}", vars_) == vars_["browser"]  # whole object
    assert (
        substitute("open ${browser.name} at ${app}", vars_) == "open chrome at C:/e.exe"
    )
    assert substitute("${missing} stays", vars_) == "${missing} stays"
    assert substitute({"a": ["${app}", 1, None]}, vars_) == {"a": ["C:/e.exe", 1, None]}

    sc = {
        "name": "open",
        "matrix": {
            "browser": [
                {"name": "chrome", "path": "C:/c.exe"},
                {"name": "ff", "path": "C:/f.exe"},
            ]
        },
        "targets": ["t1", "t2"],
        "steps": [{"action": "launch", "path": "${browser.path}"}],
    }
    out = expand_matrix([sc])
    assert len(out) == 4
    assert {o["name"] for o in out} == {
        "open@browser=chrome,t1",
        "open@browser=chrome,t2",
        "open@browser=ff,t1",
        "open@browser=ff,t2",
    }
    assert out[0]["target"] == "t1" and out[0]["steps"][0]["path"] == "C:/c.exe"
    assert "matrix" not in out[0] and "targets" not in out[0]
    # a matrix may point at a list that lives in the environment's variables
    via_var = expand_matrix(
        [{"name": "m", "matrix": {"browser": "${browsers}"}, "steps": []}],
        {"browsers": [{"name": "c"}, {"name": "f"}]},
    )
    assert [o["name"] for o in via_var] == ["m@browser=c", "m@browser=f"]
    with pytest.raises(ValueError, match="matrix.browser must be a list"):
        expand_matrix([{"name": "m", "matrix": {"browser": "${nope}"}, "steps": []}])
    # extra variables from the environment reach a plain scenario too
    plain = expand_matrix(
        [{"name": "p", "steps": [{"action": "launch", "path": "${app}"}]}], {"app": "X"}
    )
    assert plain[0]["steps"][0]["path"] == "X" and plain[0]["variables"] == {"app": "X"}


@pytest.mark.asyncio
async def test_tools_round_trip(env):
    from polarix.tools import environments as tools

    listing = json.loads(await tools.environment_list())
    assert {e["name"] for e in listing["environments"]} == {"sim", "broken", "auto"}
    assert "fresh" in listing["policies"]

    plan = json.loads(await tools.environment_plan("sim"))
    assert plan["needs_decision"] and plan["options"]
    run = json.loads(await tools.environment_run("sim", '[{"name": "x", "steps": []}]'))
    assert run["needs_decision"], "run without policy must ask, not run"

    plan = json.loads(await tools.environment_plan("sim", "fresh", False))
    assert plan["phases"] == [
        "vm.snapshot_restore (if target.vm.snapshot)",
        "install",
        "run",
        "uninstall",
        "vm.snapshot_restore (if target.vm.snapshot)",
    ]

    scenarios = json.dumps(
        [
            {
                "name": "typing",
                "window": {"title_re": ".*Editor.*"},
                "matrix": {"browser": [{"name": "a"}, {"name": "b"}]},
                "steps": [
                    {
                        "action": "set_text",
                        "locator": {"auto_id": "15"},
                        "value": "${app} ${browser.name}",
                        "wait_after": 0,
                    },
                    {
                        "action": "assert",
                        "kind": "text_contains",
                        "locator": {"auto_id": "15"},
                        "expected": "${browser.name}",
                    },
                ],
            }
        ]
    )
    run = json.loads(
        await tools.environment_run(
            "sim", scenarios, policy="fresh", keep_after=True, include_results=True
        )
    )
    assert (
        run["prepare"]["ok"]
        and run["kpis"]["scenarios"] == 2
        and run["kpis"]["passed"] == 2
    ), run
    assert {r["name"] for r in run["results"]} == {
        "typing@browser=a",
        "typing@browser=b",
    }
    assert (
        run["state"]["prepared"] and run["state"]["runs"] == 1 and "teardown" not in run
    )

    st = json.loads(await tools.environment_status("sim"))
    assert st["last_policy"] == "fresh"

    run = json.loads(
        await tools.environment_run("sim", scenarios, policy="keep", keep_after=False)
    )
    assert run["teardown"]["ok"] and run["state"]["prepared"] is False

    bad = json.loads(await tools.environment_run("broken", scenarios, policy="fresh"))
    assert bad["error"].startswith("prepare failed") and "kpis" not in bad

    down = json.loads(await tools.environment_teardown("sim", "none"))
    assert down["ok"] and down["state"]["prepared"] is False
    err = json.loads(await tools.environment_teardown("sim", "weird"))
    assert err["success"] is False
    err = json.loads(await tools.environment_status("nope"))
    assert "unknown environment" in err["error"]
