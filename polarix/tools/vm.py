"""VM tools — control of the guest from the host through the hypervisor.

vm_backends · vm_list · vm_start · vm_stop · vm_snapshot_list · vm_snapshot_save ·
vm_snapshot_restore · vm_screenshot · vm_send_keys · vm_guest_ip · vm_agent_check

Lifecycle and snapshots are what make desktop tests repeatable: restore a
known snapshot, run the macro, diff, throw the state away. Screenshot and raw
keys work even when the guest has no Polarix agent yet (vision fallback).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Optional

from polarix.desktop.driver import DesktopUnavailable
from polarix.desktop.remote_driver import RemoteDesktopDriver
from polarix.server import mcp
from polarix.telemetry import _polarix, _start, _wrap
from polarix.vm.backends import (
    AndroidBackend,
    VMUnavailable,
    detect_backends,
    get_backend,
    to_data_url,
)

_ERRORS = (VMUnavailable, ValueError, LookupError)


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


def _fail(tool: str, t0: float, exc: Exception, params: Optional[dict] = None) -> str:
    return _wrap(
        {"success": False, "error": f"{type(exc).__name__}: {exc}"},
        _polarix(tool, t0, params=params, warnings=[str(exc)]),
    )


def _vm_block(backend: Any, vm: Optional[str] = None, **extra: Any) -> dict:
    block = {"backend": backend.name, "binary": backend.binary, **extra}
    if vm:
        block["vm"] = vm
    return block


@mcp.tool()
async def vm_backends() -> str:
    """Report which hypervisor CLIs are available on this host.

    Returns:
        JSON: { available: {libvirt, virtualbox, android}, default, _polarix }
    """
    t0 = _start()
    avail = await _run(detect_backends)
    default = next((k for k, v in avail.items() if v), None)
    return _wrap(
        {
            "available": avail,
            "default": default,
            "install_hint": (
                None
                if default
                else "Fedora: sudo dnf install libvirt qemu-kvm virt-install; "
                "then create the Windows VM and take a snapshot named 'clean'."
            ),
        },
        _polarix("vm_backends", t0),
    )


@mcp.tool()
async def vm_list(backend: Optional[str] = None) -> str:
    """List VMs / emulators and their power state.

    Args:
        backend: libvirt | virtualbox | android (default: POLARIX_VM_BACKEND or auto).

    Returns:
        JSON: { vms: [{name, state}], _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        vms = await _run(be.list_vms)
    except _ERRORS as exc:
        return _fail("vm_list", t0, exc, {"backend": backend})
    return _wrap(
        {"vm_count": len(vms), "vms": vms},
        _polarix("vm_list", t0, desktop=_vm_block(be)),
    )


@mcp.tool()
async def vm_start(vm: str, backend: Optional[str] = None) -> str:
    """Power on a VM (or boot an Android AVD) and wait until it is running.

    Returns:
        JSON: { vm, state, _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        result = await _run(be.start, vm)
    except _ERRORS as exc:
        return _fail("vm_start", t0, exc, {"vm": vm})
    return _wrap(result, _polarix("vm_start", t0, desktop=_vm_block(be, vm)))


@mcp.tool()
async def vm_stop(vm: str, force: bool = False, backend: Optional[str] = None) -> str:
    """Shut a VM down (ACPI) or power it off (force=True).

    Returns:
        JSON: { vm, state, _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        result = await _run(be.stop, vm, force)
    except _ERRORS as exc:
        return _fail("vm_stop", t0, exc, {"vm": vm, "force": force})
    return _wrap(result, _polarix("vm_stop", t0, desktop=_vm_block(be, vm)))


@mcp.tool()
async def vm_snapshot_list(vm: str, backend: Optional[str] = None) -> str:
    """List snapshots of a VM.

    Returns:
        JSON: { vm, snapshots: [{name}], _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        snaps = await _run(be.snapshot_list, vm)
    except _ERRORS as exc:
        return _fail("vm_snapshot_list", t0, exc, {"vm": vm})
    return _wrap(
        {"vm": vm, "snapshot_count": len(snaps), "snapshots": snaps},
        _polarix("vm_snapshot_list", t0, desktop=_vm_block(be, vm)),
    )


@mcp.tool()
async def vm_snapshot_save(vm: str, name: str, backend: Optional[str] = None) -> str:
    """Take a snapshot of the VM's current state (e.g. "clean", "app-open").

    Returns:
        JSON: { vm, snapshot, _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        result = await _run(be.snapshot_save, vm, name)
    except _ERRORS as exc:
        return _fail("vm_snapshot_save", t0, exc, {"vm": vm, "name": name})
    return _wrap(result, _polarix("vm_snapshot_save", t0, desktop=_vm_block(be, vm)))


@mcp.tool()
async def vm_snapshot_restore(vm: str, name: str, backend: Optional[str] = None) -> str:
    """Revert the VM to a snapshot and leave it running — the reset before each test run.

    Returns:
        JSON: { vm, snapshot, state, _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        result = await _run(be.snapshot_restore, vm, name)
    except _ERRORS as exc:
        return _fail("vm_snapshot_restore", t0, exc, {"vm": vm, "name": name})
    return _wrap(result, _polarix("vm_snapshot_restore", t0, desktop=_vm_block(be, vm)))


@mcp.tool()
async def vm_screenshot(vm: str, backend: Optional[str] = None) -> str:
    """Capture the guest display through the hypervisor (works without an agent).

    Returns:
        JSON: { image: "data:image/png;base64,...", _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        data = await _run(be.screenshot, vm)
    except _ERRORS as exc:
        return _fail("vm_screenshot", t0, exc, {"vm": vm})
    return _wrap(
        {"image": to_data_url(data), "bytes": len(data)},
        _polarix("vm_screenshot", t0, desktop=_vm_block(be, vm)),
    )


@mcp.tool()
async def vm_send_keys(vm: str, keys_json: str, backend: Optional[str] = None) -> str:
    """Inject keystrokes into the guest through the hypervisor (no agent needed).

    Key names depend on the backend: libvirt uses Linux names
    (["KEY_LEFTCTRL","KEY_S"]), VirtualBox raw scancodes, Android KEYCODE_*
    or literal text.

    Returns:
        JSON: { vm, keys, _polarix }
    """
    t0 = _start()
    try:
        keys = json.loads(keys_json)
        if not isinstance(keys, list):
            raise ValueError("keys_json must be a JSON array of key names")
        be = get_backend(backend)
        result = await _run(be.send_keys, vm, [str(k) for k in keys])
    except (json.JSONDecodeError, *_ERRORS) as exc:
        return _fail("vm_send_keys", t0, exc, {"vm": vm})
    return _wrap(result, _polarix("vm_send_keys", t0, desktop=_vm_block(be, vm)))


@mcp.tool()
async def vm_tap(vm: str, x: int, y: int, backend: str = "android") -> str:
    """Tap a screen coordinate on an Android emulator (adb input tap).

    Returns:
        JSON: { vm, tap: [x, y], _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        if not isinstance(be, AndroidBackend):
            raise VMUnavailable("vm_tap is only available for the android backend")
        result = await _run(be.tap, vm, x, y)
    except _ERRORS as exc:
        return _fail("vm_tap", t0, exc, {"vm": vm})
    return _wrap(result, _polarix("vm_tap", t0, desktop=_vm_block(be, vm)))


@mcp.tool()
async def vm_guest_ip(vm: str, backend: Optional[str] = None) -> str:
    """Resolve the guest's IPv4 — what POLARIX_DESKTOP_AGENT_URL should point at.

    Returns:
        JSON: { vm, ip, agent_url_hint, _polarix }
    """
    t0 = _start()
    try:
        be = get_backend(backend)
        ip = await _run(be.guest_ip, vm)
    except _ERRORS as exc:
        return _fail("vm_guest_ip", t0, exc, {"vm": vm})
    return _wrap(
        {
            "vm": vm,
            "ip": ip,
            "agent_url_hint": f"http://{ip}:8020" if ip else None,
        },
        _polarix("vm_guest_ip", t0, desktop=_vm_block(be, vm)),
    )


@mcp.tool()
async def vm_agent_check(agent_url: str, token: str = "") -> str:
    """Ping a Polarix agent inside a guest (GET /health) and report its driver/platform.

    Args:
        agent_url: e.g. http://192.168.122.15:8020
        token: POLARIX_AGENT_TOKEN if the agent was started with one.

    Returns:
        JSON: { reachable, agent: {driver, backend, platform}, _polarix }
    """
    t0 = _start()
    try:
        drv = RemoteDesktopDriver(agent_url, token, timeout=5.0)
        health = await _run(drv.health)
    except DesktopUnavailable as exc:
        return _wrap(
            {"reachable": False, "error": str(exc)},
            _polarix("vm_agent_check", t0, params={"agent_url": agent_url}),
        )
    return _wrap(
        {"reachable": bool(health.get("ok")), "agent": health},
        _polarix("vm_agent_check", t0, params={"agent_url": agent_url}),
    )
