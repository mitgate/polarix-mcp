"""Doctor: every check reports a status and a fix; nothing explodes when tools are absent."""

from __future__ import annotations

import json

import pytest

from polarix import doctor


@pytest.fixture
def bare_host(monkeypatch, tmp_path):
    """A host with none of the optional tooling and a clean config."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        doctor.os.path,
        "exists",
        lambda p: False if p == "/dev/kvm" else _real_exists(p),
    )
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/Android/Sdk here
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "pw"))
    monkeypatch.setenv("POLARIX_TARGETS_FILE", str(tmp_path / "targets.json"))
    monkeypatch.setenv("POLARIX_ENVIRONMENTS_FILE", str(tmp_path / "environments.json"))
    monkeypatch.delenv("POLARIX_TARGETS", raising=False)
    monkeypatch.delenv("POLARIX_ENVIRONMENTS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("POLARIX_APPIUM_URL", "http://127.0.0.1:1")  # nothing listens
    return tmp_path


_real_exists = doctor.os.path.exists


def test_bare_host_is_honest_and_does_not_crash(bare_host):
    report = doctor.diagnose()
    names = {c["name"]: c for c in report["checks"]}
    assert (
        names["chromium"]["status"] == "missing"
        and "playwright install" in names["chromium"]["fix"]
    )
    assert names["kvm"]["status"] == "optional"
    assert (
        names["libvirt"]["status"] == "optional"
        and "--desktop" in names["libvirt"]["fix"]
    )
    assert names["adb"]["status"] == "optional" and "--android" in names["adb"]["fix"]
    assert names["appium"]["status"] == "optional"
    assert names["appium server"]["status"] == "optional"
    assert names["llm keys"]["status"] == "warn"
    assert (
        names["targets"]["status"] == "warn"
        and "built-ins only" in names["targets"]["detail"]
    )
    assert names["environments"]["status"] == "warn"
    assert report["layers"] == {
        "browser": report["healthy"],
        "desktop_vm": False,
        "android": False,
        "appium": False,
    }
    assert all("fix" in f and f["fix"] for f in report["fixes"])
    assert set(report["counts"]) == {"ok", "warn", "missing", "optional"}
    # python packages are really installed in the test environment
    assert names["python:playwright"]["status"] == "ok"


def test_groups_filter_and_broken_check_is_reported(monkeypatch):
    def boom():
        raise RuntimeError("kaput")

    monkeypatch.setitem(doctor._RUNNERS, "vm", boom)
    report = doctor.diagnose(["vm", "nope"])
    assert len(report["checks"]) == 1
    assert (
        report["checks"][0]["status"] == "warn"
        and "kaput" in report["checks"][0]["detail"]
    )


def test_invalid_targets_file_is_a_missing_config(monkeypatch, tmp_path):
    bad = tmp_path / "targets.json"
    bad.write_text('{"x": {"driver": "remote"}}')  # remote without agent_url
    monkeypatch.setenv("POLARIX_TARGETS_FILE", str(bad))
    monkeypatch.delenv("POLARIX_TARGETS", raising=False)
    report = doctor.diagnose(["config"])
    targets = next(c for c in report["checks"] if c["name"] == "targets")
    assert targets["status"] == "missing" and "agent_url" in targets["detail"]


def test_adb_with_devices_and_libvirt_reachable(monkeypatch):
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("adb", "virsh") else None,
    )
    monkeypatch.setattr(
        doctor,
        "_run",
        lambda cmd, timeout=15.0: (
            0,
            "List of devices attached\nemulator-5554\tdevice\nR58M\toffline\n",
        ),
    )

    class FakeBackend:
        def connection_report(self):
            return {"uri": "qemu:///session", "reachable": True}

    import polarix.vm.backends as backends

    monkeypatch.setattr(backends, "LibvirtBackend", FakeBackend)
    report = doctor.diagnose(["vm", "android"])
    names = {c["name"]: c for c in report["checks"]}
    assert names["adb"]["status"] == "ok" and "emulator-5554" in names["adb"]["detail"]
    assert "R58M" not in names["adb"]["detail"]
    assert names["libvirt"]["status"] == "ok" and report["layers"]["desktop_vm"] is True
    assert report["layers"]["android"] is True


def test_cli_text_and_json(bare_host, capsys):
    code = doctor.main(["config", "llm"])
    out = capsys.readouterr().out
    assert "Polarix doctor" in out and "llm keys" in out and "fix:" in out
    assert code == 0  # config/llm are never core
    doctor.main(["--json", "core"])
    data = json.loads(capsys.readouterr().out)
    assert "checks" in data and data["checks"][0]["group"] == "core"


@pytest.mark.asyncio
async def test_tool_wraps_report(bare_host):
    from polarix.tools import utilities

    data = json.loads(await utilities.polarix_doctor("llm,config"))
    assert {c["group"] for c in data["checks"]} == {"llm", "config"}
    assert data["_polarix"]["tool"] == "polarix_doctor"
    bad = json.loads(await utilities.polarix_doctor("bogus"))
    assert bad["success"] is False
