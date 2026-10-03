"""The `shell` step and desktop_run_command — setup/teardown around UI steps."""

from __future__ import annotations

import asyncio
import json

from polarix.desktop import scenarios as sc
from polarix.desktop.agent import start_in_thread
from polarix.desktop.fake_driver import FakeDesktopDriver
from polarix.desktop.remote_driver import RemoteDesktopDriver
from polarix.desktop.steps import run_desktop_steps
from polarix.tools import desktop as dt
from tests.conftest import EDITOR


def test_shell_step_success_and_expected_failure(fake):
    steps = [
        {"action": "shell", "command": "winget install --id 9WZDNCRFHVN5"},
        {"action": "shell", "command": "false", "expect_exit_code": 1},
        {"action": "shell", "command": "exit 1 please", "expect_exit_code": None},
    ]
    results, _, _ = run_desktop_steps(fake, EDITOR, steps)
    assert [r["success"] for r in results] == [True, True, True]
    assert results[0]["result"]["stdout"].startswith("simulated: winget install")
    assert results[1]["result"]["exit_code"] == 1
    assert fake.log[-1]["action"] == "run_command"


def test_shell_step_fails_on_unexpected_exit_code(fake):
    results, _, _ = run_desktop_steps(
        fake, EDITOR, [{"action": "shell", "command": "false"}, {"action": "snapshot"}]
    )
    assert results[0]["success"] is False
    assert "exited with 1 (expected 0)" in results[0]["error"]
    assert len(results) == 1


def test_lifecycle_scenario_runs_teardown_after_failure(fake):
    scenario = {
        "name": "lifecycle",
        "window": EDITOR,
        "setup": [{"action": "shell", "command": "winget install editor"}],
        "steps": [
            {
                "action": "assert",
                "kind": "text_equals",
                "locator": {"auto_id": "1025"},
                "expected": "nope",
            },
        ],
        "teardown": [
            {"action": "shell", "command": "winget uninstall editor"},
            {"action": "shell", "command": "false", "expect_exit_code": 1},
        ],
    }
    r = sc.run_scenario(fake, scenario)
    assert r["status"] == "failed"
    assert [s["success"] for s in r["phases"]["teardown"]] == [True, True]
    assert [s["action"] for s in fake.log if s["action"] == "run_command"].count(
        "run_command"
    ) == 3


def test_run_command_over_the_agent():
    server, _ = start_in_thread("127.0.0.1", 0, token="t", driver=FakeDesktopDriver())
    try:
        drv = RemoteDesktopDriver(f"http://127.0.0.1:{server.server_address[1]}", "t")
        out = drv.run_command("winget list", timeout=5)
        assert out["exit_code"] == 0 and "winget list" in out["stdout"]
    finally:
        server.shutdown()
        server.server_close()


def test_desktop_run_command_tool(fake_env):
    ok = json.loads(asyncio.run(dt.desktop_run_command("winget list")))
    assert ok["success"] is True and ok["exit_code"] == 0
    bad = json.loads(asyncio.run(dt.desktop_run_command("false")))
    assert bad["success"] is False and bad["_polarix"]["warnings"] == ["exit code 1"]
