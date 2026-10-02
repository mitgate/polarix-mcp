from __future__ import annotations

import pytest

from polarix.vm import backends as vmb


class ScriptedRunner:
    """Maps the command tail (after the binary) to (rc, stdout, stderr)."""

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]):
        self.calls.append(cmd)
        key = " ".join(cmd[1:])
        for prefix, result in self.script.items():
            if key.startswith(prefix):
                return result
        return 1, "", f"unscripted: {key}"


def test_libvirt_list_start_snapshot_and_ip(monkeypatch):
    monkeypatch.setattr(vmb.shutil, "which", lambda b: "/usr/bin/virsh")
    state = {"win11": "shut off"}

    def runner(cmd):
        tail = " ".join(cmd[3:])  # skip virsh --connect URI
        if tail == "list --all --name":
            return 0, "win11\n\n", ""
        if tail == "domstate win11":
            return 0, state["win11"] + "\n", ""
        if tail == "start win11":
            state["win11"] = "running"
            return 0, "Domain started", ""
        if tail == "snapshot-list win11 --name":
            return 0, "clean\nafter-install\n", ""
        if tail.startswith("domifaddr win11"):
            return 0, " vnet0  52:54:00  ipv4  192.168.122.15/24\n", ""
        return 1, "", "nope"

    be = vmb.LibvirtBackend(runner, uri="qemu:///system")
    assert be.available()
    assert be.list_vms() == [{"name": "win11", "state": "off"}]
    assert be.start("win11") == {"vm": "win11", "state": "running"}
    assert [s["name"] for s in be.snapshot_list("win11")] == ["clean", "after-install"]
    assert be.guest_ip("win11") == "192.168.122.15"


def test_virtualbox_parsing():
    runner = ScriptedRunner(
        {
            "list vms": (0, '"win11" {uuid-1}\n"ubuntu" {uuid-2}\n', ""),
            "showvminfo win11": (0, 'name="win11"\nVMState="poweroff"\n', ""),
            "showvminfo ubuntu": (0, 'VMState="running"\n', ""),
            "snapshot win11 list": (
                0,
                'SnapshotName="clean"\nSnapshotName-1="x"\n',
                "",
            ),
            "guestproperty get win11": (0, "Value: 10.0.2.15\n", ""),
        }
    )
    be = vmb.VirtualBoxBackend(runner)
    assert be.list_vms() == [
        {"name": "win11", "state": "off"},
        {"name": "ubuntu", "state": "running"},
    ]
    assert [s["name"] for s in be.snapshot_list("win11")] == ["clean", "x"]
    assert be.guest_ip("win11") == "10.0.2.15"


def test_android_devices_and_keys(monkeypatch):
    monkeypatch.setattr(vmb.shutil, "which", lambda b: None)  # no `emulator`
    runner = ScriptedRunner(
        {
            "devices": (0, "List of devices attached\nemulator-5554\tdevice\n", ""),
            "-s emulator-5554 shell input": (0, "", ""),
        }
    )
    be = vmb.AndroidBackend(runner)
    assert be.list_vms() == [{"name": "emulator-5554", "state": "device"}]
    assert be.state("emulator-5554") == "running"
    be.send_keys("emulator-5554", ["KEYCODE_HOME", "hello world"])
    assert runner.calls[-1][-1] == "hello%sworld"
    assert be.tap("emulator-5554", 10, 20)["tap"] == [10, 20]


def test_command_failure_raises_vm_unavailable():
    be = vmb.LibvirtBackend(ScriptedRunner({}))
    with pytest.raises(vmb.VMUnavailable, match="failed"):
        be.state("ghost")


def test_get_backend_auto_without_any_cli(monkeypatch):
    monkeypatch.setattr(vmb.shutil, "which", lambda b: None)
    monkeypatch.delenv("POLARIX_VM_BACKEND", raising=False)
    assert vmb.detect_backends() == {
        "libvirt": False,
        "virtualbox": False,
        "android": False,
    }
    with pytest.raises(vmb.VMUnavailable, match="no hypervisor"):
        vmb.get_backend()
    with pytest.raises(vmb.VMUnavailable, match="unknown"):
        vmb.get_backend("xen")


def test_to_data_url_detects_png():
    assert vmb.to_data_url(b"\x89PNG\r\n\x1a\n" + b"x").startswith("data:image/png;")
    assert vmb.to_data_url(b"P6 1 1").startswith("data:image/x-portable-pixmap;")
