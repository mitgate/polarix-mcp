"""Android driver — the DesktopDriver contract over `adb` + `uiautomator dump`.

No agent runs on the device: everything goes through adb from the host where
Polarix (or its agent) runs — an emulator on the same box, a device plugged in,
`adb connect <ip>:5555`, or a device farm that exposes adb.

Mapping of the contract
  window        the foreground activity ("package/Activity"); list_windows → one entry
  launch        package name (or "package/.Activity") → `am start`; returns the activity
  inventory     `uiautomator dump` XML → Control(control_type=class short name,
                title=content-desc (the accessibility name) or text, value=text,
                auto_id=resource-id without "pkg:id/", rect=bounds)
  click         tap at the element centre (`input tap`); coordinates are screen pixels
  set_text      tap, select all, `input text` (spaces → %s)
  type_keys     {ENTER} {BACK} {HOME} {TAB} {ESC} … → keyevents; plain text → input text
  menu_select   not available (Android has no menu bar) — click the overflow button
  run_command   `adb shell <command>` ON THE DEVICE
  screenshot    `exec-out screencap -p`
"""

from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from polarix.desktop.model import Control, find_controls

Runner = Callable[[list[str]], tuple[int, str, str]]

_KEYCODES = {
    "ENTER": "KEYCODE_ENTER",
    "TAB": "KEYCODE_TAB",
    "ESC": "KEYCODE_BACK",
    "ESCAPE": "KEYCODE_BACK",
    "BACK": "KEYCODE_BACK",
    "HOME": "KEYCODE_HOME",
    "BACKSPACE": "KEYCODE_DEL",
    "DELETE": "KEYCODE_FORWARD_DEL",
    "SPACE": "KEYCODE_SPACE",
    "UP": "KEYCODE_DPAD_UP",
    "DOWN": "KEYCODE_DPAD_DOWN",
    "LEFT": "KEYCODE_DPAD_LEFT",
    "RIGHT": "KEYCODE_DPAD_RIGHT",
    "MENU": "KEYCODE_MENU",
    "APP_SWITCH": "KEYCODE_APP_SWITCH",
    "POWER": "KEYCODE_POWER",
    "VOLUME_UP": "KEYCODE_VOLUME_UP",
    "VOLUME_DOWN": "KEYCODE_VOLUME_DOWN",
}
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_FOCUS = re.compile(r"mCurrentFocus=Window\{[^ ]+ u\d+ ([\w.]+)/([\w.$]+)")
_RESUMED = re.compile(r"(?:mResumedActivity|ResumedActivity)[^\n]*? ([\w.]+)/([\w.$]+)")


def _default_runner(cmd: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"adb timed out: {' '.join(cmd)}") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _default_binary_runner(cmd: list[str], timeout: float = 60.0) -> bytes:
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    return proc.stdout


def _short_class(name: str) -> str:
    return (name or "").rsplit(".", 1)[-1] or "View"


def _short_id(resource_id: str) -> str:
    return (
        resource_id.split("/", 1)[1]
        if "/" in (resource_id or "")
        else (resource_id or "")
    )


def parse_hierarchy(xml_text: str, max_depth: int = 32) -> list[Control]:
    """uiautomator dump → flat Control list (document order, with depth and path)."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise ValueError(f"invalid uiautomator XML: {exc}") from exc
    out: list[Control] = []

    def walk(node: ET.Element, depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        kids = [k for k in node if k.tag == "node"]
        for i, kid in enumerate(kids):
            path = f"{prefix}{i}" if not prefix else f"{prefix}/{i}"
            bounds = _BOUNDS.search(kid.get("bounds", ""))
            rect = [int(bounds.group(j)) for j in range(1, 5)] if bounds else None
            cls = _short_class(kid.get("class", ""))
            text = kid.get("text", "") or ""
            desc = kid.get("content-desc", "") or ""
            grand = [g for g in kid if g.tag == "node"]
            out.append(
                Control(
                    control_type=cls,
                    title=desc or text,
                    auto_id=_short_id(kid.get("resource-id", "")),
                    class_name=kid.get("class", ""),
                    rect=rect,
                    enabled=kid.get("enabled", "true") == "true",
                    visible=True,
                    depth=depth,
                    path=path,
                    value=text or None,
                    children=len(grand),
                    extra={
                        "package": kid.get("package", ""),
                        "content_desc": desc,
                        "clickable": kid.get("clickable") == "true",
                        "checked": kid.get("checked") == "true",
                        "selected": kid.get("selected") == "true",
                        "scrollable": kid.get("scrollable") == "true",
                    },
                )
            )
            walk(kid, depth + 1, path)

    walk(root, 0, "")
    return out


class AndroidAdbDriver:
    """DesktopDriver for one Android device/emulator, through adb."""

    def __init__(
        self,
        serial: Optional[str] = None,
        adb: str = "adb",
        runner: Optional[Runner] = None,
        binary_runner: Optional[Callable[[list[str]], bytes]] = None,
        poll: float = 0.5,
    ) -> None:
        self.serial = serial
        self.adb = adb
        self.runner = runner or _default_runner
        self.binary_runner = binary_runner or _default_binary_runner
        self.poll = poll
        self._last_activity: Optional[str] = None

    # ----------------------------------------------------------- plumbing
    def _cmd(self, *args: str) -> list[str]:
        base = [self.adb] + (["-s", self.serial] if self.serial else [])
        return base + list(args)

    def _run(self, *args: str, ok: tuple[int, ...] = (0,)) -> str:
        rc, out, err = self.runner(self._cmd(*args))
        if rc not in ok:
            raise RuntimeError(
                f"adb {' '.join(args)} failed ({rc}): {(err or out).strip()[:300]}"
            )
        return out

    def _shell(self, command: str) -> str:
        return self._run("shell", command)

    def _current(self) -> Optional[tuple[str, str]]:
        for source in (
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'",
            "dumpsys activity activities | grep -E 'ResumedActivity'",
        ):
            try:
                out = self._shell(source)
            except RuntimeError:
                continue
            hit = _FOCUS.search(out) or _RESUMED.search(out)
            if hit:
                return hit.group(1), hit.group(2)
        return None

    def _activity_title(self) -> str:
        cur = self._current()
        if not cur:
            raise LookupError("no foreground activity (screen off or no device?)")
        pkg, act = cur
        return f"{pkg}/{act}"

    def _matches(self, title: str, window: Optional[dict]) -> bool:
        if not window:
            return True
        pkg = title.split("/", 1)[0]
        if "title" in window and window["title"] not in (title, pkg):
            return False
        if "title_re" in window and not re.match(window["title_re"], title):
            return False
        if "process" in window and window["process"] != pkg:
            return False
        return True

    # ----------------------------------------------------------- contract
    def platform_info(self) -> dict:
        info = {
            "driver": "android",
            "backend": "adb+uiautomator",
            "serial": self.serial,
        }
        try:
            info["platform"] = (
                "Android " + self._shell("getprop ro.build.version.release").strip()
            )
            info["model"] = self._shell("getprop ro.product.model").strip()
        except Exception:
            info["platform"] = "Android"
        return info

    def list_windows(self, title_filter: Optional[str] = None) -> list[dict]:
        cur = self._current()
        if not cur:
            return []
        title = f"{cur[0]}/{cur[1]}"
        if title_filter and title_filter.lower() not in title.lower():
            return []
        return [self.window_state({"title": title})]

    def launch(
        self,
        path: str,
        args: str = "",
        cwd: Optional[str] = None,
        wait_seconds: float = 3.0,
        title_re: Optional[str] = None,
    ) -> dict:
        target = path.strip()
        if "/" in target:
            self._shell(f"am start -n {target} {args}".strip())
        else:
            self._shell(f"monkey -p {target} -c android.intent.category.LAUNCHER 1")
        deadline = time.monotonic() + max(wait_seconds, 1.0)
        while True:
            cur = self._current()
            title = f"{cur[0]}/{cur[1]}" if cur else ""
            if (
                cur
                and cur[0] == target.split("/", 1)[0]
                and (not title_re or re.match(title_re, title))
            ):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll)
        return self.window_state({"process": target.split("/", 1)[0]})

    def window_state(self, window: dict) -> dict:
        title = self._activity_title()
        if not self._matches(title, window):
            raise LookupError(f"foreground activity is {title}, not {window}")
        pkg, act = title.split("/", 1)
        size = self._shell("wm size").strip()
        hit = re.search(r"(\d+)x(\d+)", size)
        rect = [0, 0, int(hit.group(1)), int(hit.group(2))] if hit else None
        return {
            "title": title,
            "class_name": act,
            "process": pkg,
            "pid": 0,
            "handle": 0,
            "rect": rect,
            "is_active": True,
        }

    def focus(self, window: dict) -> None:
        pkg = window.get("process") or str(window.get("title", "")).split("/", 1)[0]
        if pkg and not self._matches(self._activity_title(), window):
            self._shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")

    def close(self, window: dict, force: bool = False) -> None:
        pkg = window.get("process") or str(window.get("title", "")).split("/", 1)[0]
        if not pkg:
            pkg = self._activity_title().split("/", 1)[0]
        if force:
            self._shell(f"am force-stop {pkg}")
        else:
            self._shell("input keyevent KEYCODE_BACK")

    def inventory(self, window: dict, max_depth: int = 8) -> list[Control]:
        self.window_state(window)  # raises if another app is in front
        self._shell("uiautomator dump /sdcard/polarix_dump.xml")
        xml = self._shell("cat /sdcard/polarix_dump.xml")
        return parse_hierarchy(xml, max_depth)

    def find(self, window: dict, locator: dict) -> list[Control]:
        return find_controls(self.inventory(window, 32), locator)

    def _one(self, window: dict, locator: dict) -> Control:
        hits = self.find(window, locator)
        if not hits:
            raise LookupError(f"Control not found: {locator}")
        return hits[0]

    @staticmethod
    def _centre(ctl: Control) -> tuple[int, int]:
        if not ctl.rect:
            raise LookupError(f"control has no bounds: {ctl.locator()}")
        left, top, right, bottom = ctl.rect
        return (left + right) // 2, (top + bottom) // 2

    def click(
        self,
        window: dict,
        locator: Optional[dict] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        double: bool = False,
    ) -> dict:
        if locator is not None:
            ctl = self._one(window, locator)
            x, y = self._centre(ctl)
            hit: Any = ctl.to_dict()
        elif x is None or y is None:
            raise ValueError("click needs a locator or x/y coordinates")
        else:
            hit = None
        if button == "right" or double:
            # long press stands in for right-click; double tap for double-click
            if double:
                self._shell(f"input tap {x} {y}")
                self._shell(f"input tap {x} {y}")
            else:
                self._shell(f"input swipe {x} {y} {x} {y} 600")
        else:
            self._shell(f"input tap {x} {y}")
        return {"hit": hit, "x": x, "y": y}

    @staticmethod
    def _escape_text(text: str) -> str:
        out = text.replace("\\", "\\\\").replace(" ", "%s")
        for ch in "&|<>;()'\"":
            out = out.replace(ch, "\\" + ch)
        return out

    def set_text(self, window: dict, locator: dict, value: str) -> None:
        ctl = self._one(window, locator)
        x, y = self._centre(ctl)
        self._shell(f"input tap {x} {y}")
        self._shell("input keyevent KEYCODE_MOVE_END")
        self._shell(
            "input keyevent --longpress $(printf 'KEYCODE_DEL %.0s' $(seq 1 60))"
        )
        if value:
            self._shell(f"input text {self._escape_text(value)}")

    def type_keys(
        self, window: dict, keys: str, locator: Optional[dict] = None
    ) -> None:
        if locator:
            ctl = self._one(window, locator)
            x, y = self._centre(ctl)
            self._shell(f"input tap {x} {y}")
        buffer = ""
        for token in re.split(r"(\{[^}]+\})", keys):
            if not token:
                continue
            if token.startswith("{") and token.endswith("}"):
                if buffer:
                    self._shell(f"input text {self._escape_text(buffer)}")
                    buffer = ""
                name = token[1:-1].upper()
                if len(name) == 1:
                    buffer += name.lower()
                    continue
                self._shell(f"input keyevent {_KEYCODES.get(name, 'KEYCODE_' + name)}")
            else:
                buffer += (
                    token.replace("^", "")
                    .replace("%", "")
                    .replace("+", "")
                    .replace("~", "")
                )
        if buffer:
            self._shell(f"input text {self._escape_text(buffer)}")

    def select(self, window: dict, locator: dict, item: str) -> None:
        self.click(window, locator)
        time.sleep(self.poll)
        self.click(window, {"title": item})

    def menu_select(self, window: dict, path: str) -> None:
        raise LookupError(
            "Android has no menu bar; click the overflow button "
            '({"title": "More options"}) and then the item by title'
        )

    def menu_items(self, window: dict, expand: bool = False) -> list[dict]:
        return []

    def wait_window(self, window: dict, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.window_state(window)
            except LookupError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"activity did not appear: {window}") from None
                time.sleep(self.poll)

    def wait_control(
        self, window: dict, locator: dict, timeout: float = 10.0, state: str = "visible"
    ) -> Control:
        deadline = time.monotonic() + timeout
        while True:
            hits = self.find(window, locator)
            if hits and (state != "enabled" or hits[0].enabled):
                return hits[0]
            if time.monotonic() >= deadline:
                raise TimeoutError(f"control did not reach '{state}': {locator}")
            time.sleep(self.poll)

    def screenshot(self, window: Optional[dict] = None) -> bytes:
        data = self.binary_runner(self._cmd("exec-out", "screencap", "-p"))
        if not data.startswith(b"\x89PNG"):
            raise RuntimeError(
                "screencap did not return a PNG (is the device unlocked?)"
            )
        return data

    def control_from_point(self, x: int, y: int) -> Optional[Control]:
        try:
            inventory = self.inventory({}, 32)
        except Exception:
            return None
        hits = [c for c in inventory if c.contains_point(x, y)]
        return hits[-1] if hits else None

    def run_command(
        self, command: str, cwd: Optional[str] = None, timeout: float = 120.0
    ) -> dict:
        t0 = time.monotonic()
        rc, out, err = self.runner(self._cmd("shell", command))
        return {
            "exit_code": rc,
            "stdout": (out or "")[-20_000:],
            "stderr": (err or "")[-20_000:],
            "duration_ms": round((time.monotonic() - t0) * 1000),
            "on": "device",
        }
