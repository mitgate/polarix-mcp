"""MCP tool layer against the simulated app (FastMCP's decorator returns the function)."""

from __future__ import annotations

import asyncio
import json

import pytest

from polarix.tools import desktop as dt
from polarix.tools import vm as vmt
from polarix.vm import backends as vmb
from tests.conftest import EDITOR
from tests.test_desktop_steps import SAVE_FLOW

W = json.dumps(EDITOR)


def call(coro):
    return json.loads(asyncio.run(coro))


def test_list_and_map_window(fake_env):
    out = call(dt.desktop_list_windows())
    assert out["window_count"] == 1
    assert out["_polarix"]["desktop"]["driver"] == "fake"

    mapped = call(dt.desktop_map_window(W))
    assert mapped["window"]["title"] == "Sem título - Editor"
    assert "Edit" in mapped["control_index"]
    assert [m["title"] for m in mapped["menus"]] == ["Arquivo", "Editar", "Ajuda"]
    assert mapped["_polarix"]["desktop"]["window"]["title"].endswith("Editor")


def test_plain_string_window_locator(fake_env):
    out = call(dt.desktop_map_window("Editor", include_menus=False))
    assert out["control_count"] > 0 and "menus" not in out


def test_explore_menus_builds_paths(fake_env):
    out = call(dt.desktop_explore_menus(W))
    assert "Arquivo->Salvar" in out["menu_paths"]


def test_execute_sequence_and_telemetry(fake_env):
    out = call(dt.desktop_execute_sequence(json.dumps(SAVE_FLOW), W))
    assert out["steps_succeeded"] == len(SAVE_FLOW)
    assert out["_polarix"]["desktop"]["window"]["title"] == "obra.txt - Editor"
    assert out["_polarix"]["tool"] == "desktop_execute_sequence"


def test_execute_sequence_rejects_bad_json(fake_env):
    out = call(dt.desktop_execute_sequence("{not json", W))
    assert out["success"] is False and "Invalid JSON" in out["error"]


def test_diff_window_sees_dialog_open(fake_env):
    steps = json.dumps([{"action": "menu", "path": "Arquivo->Salvar", "wait_after": 0}])
    out = call(dt.desktop_diff_window(W, steps))
    assert out["windows_opened"] == ["Salvar como"]
    assert out["execution"]["steps_succeeded"] == 1


def test_auto_sequence_dry_run_uses_planner(fake_env, monkeypatch):
    seen = {}

    async def fake_plan(goal, map_context, model):
        seen.update(goal=goal, model=model, paths=map_context["menu_paths"])
        return [{"action": "menu", "path": "Arquivo->Salvar"}]

    monkeypatch.setattr(dt, "generate_desktop_steps", fake_plan)
    out = call(dt.desktop_auto_sequence("salvar", W, model="gpt-test", dry_run=True))
    assert out["dry_run"] is True
    assert out["generated_steps"][0]["path"] == "Arquivo->Salvar"
    assert seen["model"] == "gpt-test" and "Arquivo->Salvar" in seen["paths"]
    assert out["map_summary"]["control_types"] == sorted(
        out["map_summary"]["control_types"]
    )


def test_auto_sequence_executes_generated_steps(fake_env, monkeypatch):
    async def fake_plan(goal, map_context, model):
        return SAVE_FLOW

    monkeypatch.setattr(dt, "generate_desktop_steps", fake_plan)
    out = call(dt.desktop_auto_sequence("salvar como obra.txt", W))
    assert out["execution"]["steps_succeeded"] == len(SAVE_FLOW)


def test_macro_tools_roundtrip(fake_env):
    saved = call(dt.desktop_macro_save("salvar", json.dumps(SAVE_FLOW), W))
    assert saved["step_count"] == len(SAVE_FLOW)
    listed = call(dt.desktop_macro_list())
    assert listed["macro_count"] == 1 and listed["macros"][0]["name"] == "salvar"
    ran = call(dt.desktop_macro_run("salvar"))
    assert ran["steps_succeeded"] == len(SAVE_FLOW)
    assert call(dt.desktop_macro_delete("salvar"))["deleted"] is True
    assert call(dt.desktop_macro_run("salvar"))["success"] is False


def test_record_stop_without_start_is_an_error(fake_env):
    out = call(dt.desktop_record_stop("ghost"))
    assert out["success"] is False and "No recorder" in out["error"]


def test_driver_unavailable_gives_hint(monkeypatch):
    monkeypatch.setenv("POLARIX_DESKTOP_DRIVER", "auto")
    monkeypatch.setattr("polarix.desktop.driver.platform.system", lambda: "Linux")
    out = call(dt.desktop_list_windows())
    assert out["success"] is False
    assert "Windows" in out["error"] and "agent" in out["hint"]


def test_screenshot_tool(fake_env):
    out = call(dt.desktop_screenshot(W))
    assert out["image"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------- vm tools


class _FakeBackend:
    name = "libvirt"
    binary = "virsh"

    def list_vms(self):
        return [{"name": "win11", "state": "running"}]

    def snapshot_restore(self, vm, name):
        return {"vm": vm, "snapshot": name, "state": "running"}

    def guest_ip(self, vm):
        return "192.168.122.15"

    def screenshot(self, vm):
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 4


def test_vm_tools_use_backend(monkeypatch):
    monkeypatch.setattr(vmt, "get_backend", lambda name=None: _FakeBackend())
    assert call(vmt.vm_list())["vms"][0]["name"] == "win11"
    out = call(vmt.vm_snapshot_restore("win11", "clean"))
    assert out["snapshot"] == "clean" and out["_polarix"]["desktop"]["vm"] == "win11"
    ip = call(vmt.vm_guest_ip("win11"))
    assert ip["agent_url_hint"] == "http://192.168.122.15:8020"
    assert call(vmt.vm_screenshot("win11"))["image"].startswith("data:image/png")


def test_vm_tools_report_missing_hypervisor(monkeypatch):
    monkeypatch.setattr(vmb.shutil, "which", lambda b: None)
    monkeypatch.delenv("POLARIX_VM_BACKEND", raising=False)
    out = call(vmt.vm_backends())
    assert out["default"] is None and "dnf install" in out["install_hint"]
    failed = call(vmt.vm_list())
    assert failed["success"] is False and "no hypervisor" in failed["error"]


def test_vm_agent_check_unreachable():
    out = call(vmt.vm_agent_check("http://127.0.0.1:9"))
    assert out["reachable"] is False


@pytest.mark.parametrize("bad", ["not json", "{}"])
def test_vm_send_keys_validates_input(monkeypatch, bad):
    monkeypatch.setattr(vmt, "get_backend", lambda name=None: _FakeBackend())
    assert call(vmt.vm_send_keys("win11", bad))["success"] is False
