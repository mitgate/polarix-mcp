"""Named desktop targets — choose which machine (and operating system) runs a sequence.

A target is a name bound to a driver configuration. Two are always available:

  fake   the simulated editor (any OS)
  local  the machine Polarix itself runs on (pywinauto; Windows only)

Others come from a JSON file (POLARIX_TARGETS_FILE, default
~/.config/polarix/targets.json) and/or the POLARIX_TARGETS environment variable
(same JSON, inline). Example:

  {
    "win11":  {"driver": "remote", "agent_url": "http://192.168.122.15:8020",
               "token": "SECRET", "os": "windows",
               "vm": {"backend": "libvirt", "name": "win11-lab", "snapshot": "clean"}},
    "win10":  {"driver": "remote", "agent_url": "http://192.168.122.16:8020", "os": "windows"},
    "ubuntu": {"driver": "remote", "agent_url": "http://192.168.122.20:8020", "os": "linux"},
    "pixel":  {"driver": "android", "serial": "emulator-5554"}
  }

Every desktop tool takes `target`; scenarios take `target` (one) or `targets`
(a matrix). POLARIX_TARGET sets the default when none is given.
"""

from __future__ import annotations

import json
import os
import platform
from typing import Any, Optional

from polarix.config import env_setting

_DRIVERS = ("remote", "fake", "auto", "pywinauto", "android")


def targets_file() -> str:
    return env_setting(
        "TARGETS_FILE",
        os.path.join(os.path.expanduser("~"), ".config", "polarix", "targets.json"),
    )


def builtin_targets() -> dict[str, dict]:
    return {
        "fake": {
            "driver": "fake",
            "os": "simulated",
            "description": "simulated editor, any OS",
        },
        "local": {
            "driver": "auto",
            "os": platform.system().lower() or "unknown",
            "description": "the machine Polarix runs on (pywinauto, Windows only)",
        },
    }


def _validate(name: str, cfg: Any) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError(f"target '{name}' must be an object")
    driver = str(cfg.get("driver", "remote")).lower()
    if driver not in _DRIVERS:
        raise ValueError(f"target '{name}': driver must be one of {_DRIVERS}")
    if driver == "remote" and not cfg.get("agent_url"):
        raise ValueError(f"target '{name}': remote targets need agent_url")
    out = dict(cfg)
    out["driver"] = driver
    defaults = {"remote": "windows", "pywinauto": "windows", "android": "android"}
    out.setdefault("os", defaults.get(driver, "unknown"))
    return out


def load_targets() -> dict[str, dict]:
    """Built-ins, then the file, then the inline env var (later wins)."""
    targets = builtin_targets()
    path = targets_file()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for name, cfg in (json.load(f) or {}).items():
                targets[name] = _validate(name, cfg)
    inline = env_setting("TARGETS", "").strip()
    if inline:
        for name, cfg in json.loads(inline).items():
            targets[name] = _validate(name, cfg)
    return targets


def default_target() -> Optional[str]:
    return env_setting("TARGET", "").strip() or None


def resolve(name: str) -> dict:
    targets = load_targets()
    if name not in targets:
        from polarix.desktop.driver import DesktopUnavailable

        raise DesktopUnavailable(
            f"unknown target '{name}'. Known targets: {sorted(targets)}. "
            f"Add it to {targets_file()} or POLARIX_TARGETS."
        )
    return targets[name]


def driver_for(name: str):
    """Build the driver a target describes."""
    cfg = resolve(name)
    driver = cfg["driver"]
    if driver == "fake":
        from polarix.desktop.fake_driver import FakeDesktopDriver

        fixture = cfg.get("fixture")
        if fixture:
            with open(fixture, encoding="utf-8") as f:
                return FakeDesktopDriver(json.load(f))
        return FakeDesktopDriver()
    if driver == "android":
        from polarix.desktop.android_driver import AndroidAdbDriver

        return AndroidAdbDriver(serial=cfg.get("serial"), adb=cfg.get("adb", "adb"))
    if driver == "remote":
        from polarix.desktop.remote_driver import RemoteDesktopDriver

        return RemoteDesktopDriver(
            str(cfg["agent_url"]),
            str(cfg.get("token", "")),
            float(cfg.get("timeout", 60)),
        )
    from polarix.desktop.driver import get_driver

    return get_driver(driver, cfg.get("backend"))


def describe() -> list[dict]:
    default = default_target()
    out = []
    for name, cfg in load_targets().items():
        entry = {
            "name": name,
            "driver": cfg["driver"],
            "os": cfg.get("os"),
            "description": cfg.get("description"),
            "default": name == default,
        }
        if cfg.get("agent_url"):
            entry["agent_url"] = cfg["agent_url"]
            entry["token_set"] = bool(cfg.get("token"))
        if cfg.get("vm"):
            entry["vm"] = cfg["vm"]
        out.append(entry)
    return out
