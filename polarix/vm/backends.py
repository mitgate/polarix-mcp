"""Hypervisor backends behind one small contract.

Every backend takes a `runner(cmd: list[str]) -> (rc, stdout, stderr)` so the
tests can replace subprocess with a scripted fake, and so the real one can be
swapped for an SSH runner later without touching the backends.
"""

from __future__ import annotations

from polarix.config import env_setting
import base64
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Optional, Protocol

Runner = Callable[[list[str]], tuple[int, str, str]]


class VMUnavailable(RuntimeError):
    """Hypervisor CLI missing, or the VM/command failed."""


def subprocess_runner(cmd: list[str], timeout: float = 120.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise VMUnavailable(f"command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VMUnavailable(f"command timed out: {' '.join(cmd)}") from exc
    return proc.returncode, proc.stdout, proc.stderr


class VMBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def list_vms(self) -> list[dict]: ...

    def state(self, vm: str) -> str: ...

    def start(self, vm: str) -> dict: ...

    def stop(self, vm: str, force: bool = False) -> dict: ...

    def snapshot_list(self, vm: str) -> list[dict]: ...

    def snapshot_save(self, vm: str, name: str) -> dict: ...

    def snapshot_restore(self, vm: str, name: str) -> dict: ...

    def screenshot(self, vm: str) -> bytes: ...

    def send_keys(self, vm: str, keys: list[str]) -> dict: ...

    def guest_ip(self, vm: str) -> Optional[str]: ...


class _Base:
    binary: str = ""
    name: str = ""

    def __init__(self, runner: Optional[Runner] = None) -> None:
        self.runner: Runner = runner or subprocess_runner

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(self, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
        rc, out, err = self.runner([self.binary, *args])
        if rc not in ok_codes:
            raise VMUnavailable(
                f"{self.binary} {' '.join(args)} failed ({rc}): {err.strip() or out.strip()}"
            )
        return out

    def _wait_state(self, vm: str, wanted: set[str], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        last = self.state(vm)  # type: ignore[attr-defined]
        while last not in wanted and time.monotonic() < deadline:
            time.sleep(1.0)
            last = self.state(vm)  # type: ignore[attr-defined]
        return last


# ------------------------------------------------------------------ libvirt
class LibvirtBackend(_Base):
    binary = "virsh"
    name = "libvirt"

    def __init__(
        self, runner: Optional[Runner] = None, uri: Optional[str] = None
    ) -> None:
        super().__init__(runner)
        self.uri = uri or os.getenv("LIBVIRT_DEFAULT_URI", "qemu:///system")

    def _run(self, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
        return super()._run("--connect", self.uri, *args, ok_codes=ok_codes)

    def list_vms(self) -> list[dict]:
        out = self._run("list", "--all", "--name")
        return [
            {"name": n.strip(), "state": self.state(n.strip())}
            for n in out.splitlines()
            if n.strip()
        ]

    def state(self, vm: str) -> str:
        return self._run("domstate", vm).strip().replace("shut off", "off")

    def start(self, vm: str) -> dict:
        if self.state(vm) != "running":
            self._run("start", vm)
        return {"vm": vm, "state": self._wait_state(vm, {"running"}, 30)}

    def stop(self, vm: str, force: bool = False) -> dict:
        self._run("destroy" if force else "shutdown", vm)
        return {
            "vm": vm,
            "state": self._wait_state(vm, {"off"}, 60 if not force else 10),
        }

    def snapshot_list(self, vm: str) -> list[dict]:
        out = self._run("snapshot-list", vm, "--name")
        return [{"name": n.strip()} for n in out.splitlines() if n.strip()]

    def snapshot_save(self, vm: str, name: str) -> dict:
        self._run("snapshot-create-as", vm, "--name", name)
        return {"vm": vm, "snapshot": name}

    def snapshot_restore(self, vm: str, name: str) -> dict:
        self._run("snapshot-revert", vm, "--snapshotname", name, "--running")
        return {"vm": vm, "snapshot": name, "state": self.state(vm)}

    def screenshot(self, vm: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as f:
            tmp = f.name
        try:
            self._run("screenshot", vm, tmp)
            with open(tmp, "rb") as f:
                data = f.read()
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return _ppm_to_png(data)

    def send_keys(self, vm: str, keys: list[str]) -> dict:
        # virsh uses Linux keycode names: KEY_ENTER, KEY_LEFTCTRL, KEY_A ...
        self._run("send-key", vm, *keys)
        return {"vm": vm, "keys": keys}

    def guest_ip(self, vm: str) -> Optional[str]:
        out = self._run("domifaddr", vm, "--source", "agent", ok_codes=(0, 1))
        hit = re.search(r"ipv4\s+(\d+\.\d+\.\d+\.\d+)", out)
        if hit:
            return hit.group(1)
        out = self._run("domifaddr", vm, ok_codes=(0, 1))
        hit = re.search(r"ipv4\s+(\d+\.\d+\.\d+\.\d+)", out)
        return hit.group(1) if hit else None


# --------------------------------------------------------------- virtualbox
class VirtualBoxBackend(_Base):
    binary = "VBoxManage"
    name = "virtualbox"

    def list_vms(self) -> list[dict]:
        out = self._run("list", "vms")
        names = re.findall(r'^"(.+?)"', out, flags=re.M)
        return [{"name": n, "state": self.state(n)} for n in names]

    def state(self, vm: str) -> str:
        out = self._run("showvminfo", vm, "--machinereadable")
        hit = re.search(r'^VMState="(.+?)"', out, flags=re.M)
        state = hit.group(1) if hit else "unknown"
        return {"poweroff": "off", "aborted": "off", "saved": "saved"}.get(state, state)

    def start(self, vm: str) -> dict:
        if self.state(vm) != "running":
            self._run("startvm", vm, "--type", "headless")
        return {"vm": vm, "state": self._wait_state(vm, {"running"}, 30)}

    def stop(self, vm: str, force: bool = False) -> dict:
        self._run("controlvm", vm, "poweroff" if force else "acpipowerbutton")
        return {"vm": vm, "state": self._wait_state(vm, {"off"}, 60)}

    def snapshot_list(self, vm: str) -> list[dict]:
        out = self._run("snapshot", vm, "list", "--machinereadable", ok_codes=(0, 1))
        return [
            {"name": n} for n in re.findall(r'^SnapshotName[^=]*="(.+?)"', out, re.M)
        ]

    def snapshot_save(self, vm: str, name: str) -> dict:
        self._run("snapshot", vm, "take", name, "--live")
        return {"vm": vm, "snapshot": name}

    def snapshot_restore(self, vm: str, name: str) -> dict:
        if self.state(vm) == "running":
            self._run("controlvm", vm, "poweroff")
            self._wait_state(vm, {"off"}, 20)
        self._run("snapshot", vm, "restore", name)
        self.start(vm)
        return {"vm": vm, "snapshot": name, "state": self.state(vm)}

    def screenshot(self, vm: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        try:
            self._run("controlvm", vm, "screenshotpng", tmp)
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def send_keys(self, vm: str, keys: list[str]) -> dict:
        # VBoxManage wants raw scancodes; accept them as hex strings.
        self._run("controlvm", vm, "keyboardputscancode", *keys)
        return {"vm": vm, "keys": keys}

    def guest_ip(self, vm: str) -> Optional[str]:
        out = self._run(
            "guestproperty",
            "get",
            vm,
            "/VirtualBox/GuestInfo/Net/0/V4/IP",
            ok_codes=(0, 1),
        )
        hit = re.search(r"Value:\s*(\d+\.\d+\.\d+\.\d+)", out)
        return hit.group(1) if hit else None


# ------------------------------------------------------------------ android
class AndroidBackend(_Base):
    """Android emulators via adb (device serials) and `emulator` for AVD boot."""

    binary = "adb"
    name = "android"

    def _adb(self, serial: Optional[str], *args: str, ok_codes=(0,)) -> str:
        prefix = ("-s", serial) if serial else ()
        return self._run(*prefix, *args, ok_codes=ok_codes)

    def list_vms(self) -> list[dict]:
        out = self._run("devices")
        vms = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                vms.append({"name": parts[0], "state": parts[1]})
        emulator = shutil.which("emulator")
        if emulator:
            rc, avds, _ = self.runner([emulator, "-list-avds"])
            if rc == 0:
                running = {v["name"] for v in vms}
                for avd in avds.splitlines():
                    avd = avd.strip()
                    if avd and not any(avd in r for r in running):
                        vms.append({"name": avd, "state": "off", "kind": "avd"})
        return vms

    def state(self, vm: str) -> str:
        for dev in self.list_vms():
            if dev["name"] == vm:
                return "running" if dev["state"] == "device" else dev["state"]
        return "off"

    def start(self, vm: str) -> dict:
        if vm.startswith("emulator-"):
            return {"vm": vm, "state": self.state(vm)}
        emulator = shutil.which("emulator")
        if not emulator:
            raise VMUnavailable("`emulator` binary not on PATH (Android SDK)")
        subprocess.Popen(  # noqa: S603 - long-running, detached on purpose
            [emulator, "-avd", vm, "-no-snapshot-load", "-no-boot-anim"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._run("wait-for-device")
        serial = next(
            (d["name"] for d in self.list_vms() if d["name"].startswith("emulator-")),
            vm,
        )
        return {"vm": vm, "serial": serial, "state": "running"}

    def stop(self, vm: str, force: bool = False) -> dict:
        self._adb(vm, "emu", "kill", ok_codes=(0, 1))
        return {"vm": vm, "state": "off"}

    def snapshot_list(self, vm: str) -> list[dict]:
        out = self._adb(vm, "emu", "avd", "snapshot", "list", ok_codes=(0, 1))
        return [{"name": n} for n in re.findall(r"^\s*\d+\s+(\S+)", out, re.M)]

    def snapshot_save(self, vm: str, name: str) -> dict:
        self._adb(vm, "emu", "avd", "snapshot", "save", name)
        return {"vm": vm, "snapshot": name}

    def snapshot_restore(self, vm: str, name: str) -> dict:
        self._adb(vm, "emu", "avd", "snapshot", "load", name)
        return {"vm": vm, "snapshot": name, "state": self.state(vm)}

    def screenshot(self, vm: str) -> bytes:
        rc, out, err = self.runner(["adb", "-s", vm, "exec-out", "screencap", "-p"])
        if rc != 0:
            raise VMUnavailable(f"screencap failed: {err}")
        # text runner cannot carry PNG bytes; re-run in binary mode
        proc = subprocess.run(
            ["adb", "-s", vm, "exec-out", "screencap", "-p"],
            capture_output=True,
            check=False,
        )
        return proc.stdout

    def send_keys(self, vm: str, keys: list[str]) -> dict:
        for key in keys:
            if key.startswith("KEYCODE_") or key.isdigit():
                self._adb(vm, "shell", "input", "keyevent", key)
            else:
                self._adb(vm, "shell", "input", "text", key.replace(" ", "%s"))
        return {"vm": vm, "keys": keys}

    def tap(self, vm: str, x: int, y: int) -> dict:
        self._adb(vm, "shell", "input", "tap", str(x), str(y))
        return {"vm": vm, "tap": [x, y]}

    def guest_ip(self, vm: str) -> Optional[str]:
        # host reaches the emulator through adb forward, not an IP
        return "127.0.0.1"


BACKENDS: dict[str, type] = {
    "libvirt": LibvirtBackend,
    "virtualbox": VirtualBoxBackend,
    "android": AndroidBackend,
}


def detect_backends(runner: Optional[Runner] = None) -> dict:
    return {name: cls(runner).available() for name, cls in BACKENDS.items()}


def get_backend(name: Optional[str] = None, runner: Optional[Runner] = None):
    chosen = (name or env_setting("VM_BACKEND", "auto")).lower()
    if chosen == "auto":
        for cls in BACKENDS.values():
            backend = cls(runner)
            if backend.available():
                return backend
        raise VMUnavailable(
            "no hypervisor CLI found (virsh, VBoxManage or adb). Install libvirt "
            "(dnf install libvirt qemu-kvm virt-install) or VirtualBox, or set "
            "POLARIX_VM_BACKEND."
        )
    if chosen not in BACKENDS:
        raise VMUnavailable(f"unknown POLARIX_VM_BACKEND='{chosen}'")
    backend = BACKENDS[chosen](runner)
    if not backend.available():
        raise VMUnavailable(f"{backend.binary} not found on PATH for backend {chosen}")
    return backend


def _ppm_to_png(data: bytes) -> bytes:
    """virsh screenshot writes PPM; convert with Pillow when available."""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return data


def to_data_url(data: bytes) -> str:
    mime = (
        "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/x-portable-pixmap"
    )
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"
