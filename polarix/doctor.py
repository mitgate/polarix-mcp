"""Doctor — what works on this host, what is missing, and the command that fixes it.

Groups
  core      Python, the server's Python packages, Chromium for Playwright
  llm       API keys for the planner / vision tools
  vm        KVM, libvirt (and whether the daemon answers), VirtualBox
  android   adb and the devices it sees
  appium    appium binary, drivers, server reachable
  config    .env, targets / environments files, writable directories
  server    is the MCP port listening

Each check: {name, group, status, detail, fix?}. status is one of
  ok        works
  missing   needed for that group and not there
  warn      there, but something to look at
  optional  not installed and not needed unless you use that layer
Run as `python -m polarix.doctor` (exit 1 when a core check is missing) or through
the `polarix_doctor` MCP tool.
"""

from __future__ import annotations

import glob
import importlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

from polarix.config import env_setting

GROUPS = ("core", "llm", "vm", "android", "appium", "config", "server")


def _check(
    name: str, group: str, status: str, detail: str, fix: Optional[str] = None
) -> dict:
    out = {"name": name, "group": group, "status": status, "detail": detail}
    if fix:
        out["fix"] = fix
    return out


def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ---------------------------------------------------------------- core
def _core() -> list[dict]:
    out = []
    v = sys.version_info
    out.append(
        _check(
            "python",
            "core",
            "ok" if v >= (3, 11) else "missing",
            f"{platform.python_version()} at {sys.executable}",
            None if v >= (3, 11) else "install Python 3.11+ and re-run ./install.sh",
        )
    )
    for mod, pkg in (
        ("mcp", "mcp[cli]"),
        ("playwright", "playwright"),
        ("cv2", "opencv-python-headless"),
        ("numpy", "numpy"),
        ("PIL", "pillow"),
        ("yaml", "pyyaml"),
    ):
        try:
            m = importlib.import_module(mod)
            out.append(
                _check(
                    f"python:{pkg}", "core", "ok", getattr(m, "__version__", "present")
                )
            )
        except Exception as exc:  # ImportError or a broken native lib
            out.append(
                _check(
                    f"python:{pkg}",
                    "core",
                    "missing",
                    f"{type(exc).__name__}: {exc}"[:160],
                    f"{sys.executable} -m pip install {pkg}",
                )
            )
    try:
        importlib.import_module("mcp.server.fastmcp")
    except Exception as exc:
        out.append(
            _check(
                "python:mcp 1.x API",
                "core",
                "missing",
                f"mcp.server.fastmcp not importable ({type(exc).__name__}); mcp 2.x installed?",
                f'{sys.executable} -m pip install "mcp[cli]>=1,<2"',
            )
        )
    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(
        os.path.expanduser("~"), ".cache", "ms-playwright"
    )
    found = sorted(glob.glob(os.path.join(cache, "chromium-*")))
    out.append(
        _check(
            "chromium",
            "core",
            "ok" if found else "missing",
            (os.path.basename(found[-1]) if found else f"no chromium-* under {cache}"),
            None if found else f"{sys.executable} -m playwright install chromium",
        )
    )
    return out


# ---------------------------------------------------------------- llm
def _llm() -> list[dict]:
    keys = {
        "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "ANTHROPIC_API_KEY": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
    }
    status = "ok" if any(keys.values()) else "warn"
    detail = ", ".join(f"{k}={'set' if v else 'unset'}" for k, v in keys.items())
    return [
        _check(
            "llm keys",
            "llm",
            status,
            detail + f"; model={os.getenv('BROWSER_USE_MODEL', 'gpt-4o-mini')}",
            (
                None
                if status == "ok"
                else "set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env (needed by browser_run_task, "
                "*_auto_sequence, click_vision/assert vision; everything else works without)"
            ),
        )
    ]


# ---------------------------------------------------------------- vm
def _vm() -> list[dict]:
    out = []
    kvm = os.path.exists("/dev/kvm")
    out.append(
        _check(
            "kvm",
            "vm",
            "ok" if kvm else "optional",
            "/dev/kvm present" if kvm else "/dev/kvm missing",
            (
                None
                if kvm
                else "enable VT-x/AMD-V in the firmware, or use a bare-metal cloud instance"
            ),
        )
    )
    virsh = shutil.which("virsh")
    if not virsh:
        out.append(
            _check(
                "libvirt", "vm", "optional", "virsh not found", "./install.sh --desktop"
            )
        )
    else:
        from polarix.vm.backends import LibvirtBackend

        rep = LibvirtBackend().connection_report()
        out.append(
            _check(
                "libvirt",
                "vm",
                "ok" if rep["reachable"] else "warn",
                f"virsh at {virsh}; {rep['uri']} "
                + ("reachable" if rep["reachable"] else "NOT reachable"),
                None if rep["reachable"] else " | ".join(rep.get("hints", [])),
            )
        )
    vbox = shutil.which("VBoxManage")
    out.append(
        _check(
            "virtualbox",
            "vm",
            "ok" if vbox else "optional",
            vbox or "VBoxManage not found (only needed for the virtualbox backend)",
        )
    )
    return out


# ---------------------------------------------------------------- android
def _android() -> list[dict]:
    adb = shutil.which(env_setting("ADB", "adb"))
    sdk = os.path.join(
        os.path.expanduser("~"), "Android", "Sdk", "platform-tools", "adb"
    )
    if not adb and os.access(sdk, os.X_OK):
        return [
            _check(
                "adb",
                "android",
                "warn",
                f"not on PATH but found at {sdk}",
                f"export POLARIX_ADB={sdk}  (or add platform-tools to PATH)",
            )
        ]
    if not adb:
        return [
            _check(
                "adb", "android", "optional", "adb not found", "./install.sh --android"
            )
        ]
    rc, text = _run([adb, "devices"])
    devices = [
        ln.split()[0] for ln in text.splitlines()[1:] if ln.strip() and "\tdevice" in ln
    ]
    return [
        _check(
            "adb",
            "android",
            "ok" if rc == 0 else "warn",
            f"{adb}; devices: {devices or 'none connected'}",
            None if rc == 0 else text[:200],
        )
    ]


# ---------------------------------------------------------------- appium
def _appium_drivers(binary: str) -> dict[str, str]:
    """Installed Appium drivers as {name: version}; `--json` first, text as fallback."""
    rc, text = _run([binary, "driver", "list", "--installed", "--json"], timeout=30)
    if rc == 0:
        try:
            data = json.loads(text[text.index("{") :])
            return {
                name: str((info or {}).get("version", "?"))
                for name, info in data.items()
                if isinstance(info, dict) and info.get("installed", True)
            }
        except (ValueError, AttributeError):
            pass
    rc, text = _run([binary, "driver", "list", "--installed"], timeout=30)
    return dict(re.findall(r"([a-z0-9-]+)@(\d[\w.-]*)", text))


def _appium() -> list[dict]:
    out = []
    binary = shutil.which("appium")
    url = env_setting("APPIUM_URL", "http://127.0.0.1:4723").rstrip("/")
    if not binary:
        out.append(
            _check(
                "appium",
                "appium",
                "optional",
                "appium not found on this host (fine when the server runs elsewhere, e.g. a Mac)",
                "./install.sh --appium",
            )
        )
    else:
        drivers = _appium_drivers(binary)
        has_u2 = "uiautomator2" in drivers
        out.append(
            _check(
                "appium",
                "appium",
                "ok" if has_u2 else "warn",
                f"{binary}; drivers: {', '.join(f'{k}@{v}' for k, v in drivers.items()) or 'none'}",
                None if has_u2 else "appium driver install uiautomator2",
            )
        )
    try:
        with urllib.request.urlopen(f"{url}/status", timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        ready = (body.get("value") or {}).get("ready")
        out.append(
            _check(
                "appium server",
                "appium",
                "ok" if ready else "warn",
                f"{url} answered (ready={ready})",
            )
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out.append(
            _check(
                "appium server",
                "appium",
                "optional",
                f"{url} not answering ({type(exc).__name__})",
                "start it with `appium` (or set POLARIX_APPIUM_URL to the Mac that runs it)",
            )
        )
    return out


# ---------------------------------------------------------------- config
def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".polarix_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _config() -> list[dict]:
    from polarix import config
    from polarix.desktop import environments, targets

    out = []
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(root, ".env")
    out.append(
        _check(
            ".env",
            "config",
            "ok" if os.path.exists(env_file) else "warn",
            env_file if os.path.exists(env_file) else "no .env next to start.sh",
            (
                None
                if os.path.exists(env_file)
                else "./install.sh writes a commented template"
            ),
        )
    )
    for label, path, loader in (
        ("targets", targets.targets_file(), targets.load_targets),
        (
            "environments",
            environments.environments_file(),
            environments.load_environments,
        ),
    ):
        try:
            names = sorted(loader())
            exists = os.path.exists(path)
            out.append(
                _check(
                    label,
                    "config",
                    "ok" if exists else "warn",
                    f"{len(names)} defined ({', '.join(names[:6])}{'…' if len(names) > 6 else ''}); "
                    + (path if exists else f"no file at {path}, built-ins only"),
                    (
                        None
                        if exists
                        else f"cp {os.path.dirname(path)}/{label}.example.json {path} and edit"
                    ),
                )
            )
        except (ValueError, OSError) as exc:
            out.append(
                _check(
                    label, "config", "missing", f"invalid: {exc}"[:200], f"fix {path}"
                )
            )
    for label, path in (
        ("sessions dir", config.SESSIONS_DIR),
        ("macros dir", config.MACROS_DIR),
        ("reports dir", config.REPORTS_DIR),
        ("metrics db dir", os.path.dirname(config.METRICS_DB) or "."),
    ):
        ok = _writable(path)
        out.append(
            _check(
                label,
                "config",
                "ok" if ok else "missing",
                path,
                None if ok else f"mkdir -p {path}",
            )
        )
    return out


# ---------------------------------------------------------------- server
def _server() -> list[dict]:
    from polarix import config

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        listening = s.connect_ex((config.MCP_HOST, config.MCP_PORT)) == 0
    return [
        _check(
            "mcp port",
            "server",
            "ok" if listening else "warn",
            f"{config.MCP_HOST}:{config.MCP_PORT} {'listening' if listening else 'not listening'}",
            (
                None
                if listening
                else "./start.sh  (or systemctl --user start polarix-mcp)"
            ),
        )
    ]


_RUNNERS = {
    "core": _core,
    "llm": _llm,
    "vm": _vm,
    "android": _android,
    "appium": _appium,
    "config": _config,
    "server": _server,
}


def diagnose(groups: Optional[list[str]] = None) -> dict[str, Any]:
    chosen = [g for g in (groups or GROUPS) if g in _RUNNERS]
    checks: list[dict] = []
    for g in chosen:
        try:
            checks.extend(_RUNNERS[g]())
        except Exception as exc:  # a broken check must not hide the others
            checks.append(
                _check(g, g, "warn", f"check failed: {type(exc).__name__}: {exc}"[:200])
            )
    counts = {
        s: sum(1 for c in checks if c["status"] == s)
        for s in ("ok", "warn", "missing", "optional")
    }
    core_missing = [
        c["name"] for c in checks if c["group"] == "core" and c["status"] == "missing"
    ]
    return {
        "host": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "healthy": not core_missing,
        "core_missing": core_missing,
        "counts": counts,
        "checks": checks,
        "fixes": [{"name": c["name"], "fix": c["fix"]} for c in checks if c.get("fix")],
        "layers": {
            "browser": not core_missing,
            "desktop_vm": any(
                c["name"] == "libvirt" and c["status"] == "ok" for c in checks
            )
            or any(c["name"] == "virtualbox" and c["status"] == "ok" for c in checks),
            "android": any(c["name"] == "adb" and c["status"] == "ok" for c in checks),
            "appium": any(
                c["name"] == "appium server" and c["status"] == "ok" for c in checks
            ),
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    groups = [a for a in args if not a.startswith("--")] or None
    report = diagnose(groups)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        marks = {"ok": "ok ", "warn": "warn", "missing": "MISS", "optional": "opt "}
        print(f"Polarix doctor — {report['host']}")
        for c in report["checks"]:
            print(
                f"  [{marks[c['status']]}] {c['group']:<8} {c['name']:<22} {c['detail']}"
            )
            if c.get("fix"):
                print(f"          fix: {c['fix']}")
        layers = ", ".join(
            f"{k}={'yes' if v else 'no'}" for k, v in report["layers"].items()
        )
        print(f"layers: {layers}")
        print(
            "healthy"
            if report["healthy"]
            else f"core missing: {report['core_missing']}"
        )
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
