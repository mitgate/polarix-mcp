"""Appium driver — the DesktopDriver contract over the W3C WebDriver protocol.

One implementation for both mobile platforms:

  Android   Appium server + UiAutomator2 (anywhere adb works)
  iOS       Appium server + XCUITest — the server MUST run on a Mac (Xcode);
            Polarix itself can stay on Linux and talk to it over HTTP.

Only the standard library is used (urllib). The HTTP function is injectable so
the tests script a server instead of needing a device.

Mapping of the contract
  window        Android: "package/Activity" (current_package/current_activity);
                iOS: the active bundleId. list_windows → the one foreground app.
  launch        package or bundleId → `mobile: activateApp`; waits for it in front
  inventory     GET /source → Control(control_type=short class / XCUIElementType*,
                title=content-desc|text / label|name, value=text / value,
                auto_id=resource-id without "pkg:id/" / name, rect=bounds / x,y,w,h)
  click         element click when the control has an id/name/text (xpath); otherwise
                a W3C pointer tap at the centre. Long press = right click.
  set_text      element clear + value (xpath), fallback: tap + `mobile: type`
  type_keys     {ENTER} {BACK} {HOME} {TAB} … → pressKey (Android) / pressButton (iOS);
                plain text → `mobile: type` (Android) / `mobile: keys` (iOS)
  menu_select   not available on mobile
  run_command   Android: `mobile: shell` (server started with --relaxed-security);
                iOS: not available
  screenshot    GET /screenshot (PNG)

Capabilities are passed through untouched, e.g.
  {"platformName": "Android", "appium:automationName": "UiAutomator2",
   "appium:deviceName": "emulator-5554", "appium:noReset": true}
  {"platformName": "iOS", "appium:automationName": "XCUITest",
   "appium:deviceName": "iPhone 15", "appium:platformVersion": "17.4",
   "appium:udid": "...", "appium:bundleId": "com.example.app"}
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from polarix.desktop.model import Control, find_controls

Http = Callable[[str, str, Optional[dict]], tuple[int, dict]]

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

_ANDROID_KEYCODES = {
    "ENTER": 66,
    "TAB": 61,
    "ESC": 4,
    "ESCAPE": 4,
    "BACK": 4,
    "HOME": 3,
    "BACKSPACE": 67,
    "DELETE": 112,
    "SPACE": 62,
    "UP": 19,
    "DOWN": 20,
    "LEFT": 21,
    "RIGHT": 22,
    "MENU": 82,
    "APP_SWITCH": 187,
    "POWER": 26,
    "VOLUME_UP": 24,
    "VOLUME_DOWN": 25,
}
_IOS_BUTTONS = {"HOME": "home", "VOLUME_UP": "volumeUp", "VOLUME_DOWN": "volumeDown"}


def _default_http(method: str, url: str, body: Optional[dict]) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            payload = {"value": {"error": "unknown error", "message": str(exc)}}
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Appium server unreachable at {url}: {exc.reason}") from exc


def _raise_for(payload: dict, context: str) -> None:
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, dict) or "error" not in value:
        return
    kind = str(value.get("error", ""))
    message = f"{context}: {kind} — {str(value.get('message', ''))[:300]}"
    if kind in ("no such element", "no such window", "stale element reference"):
        raise LookupError(message)
    if kind in ("timeout", "script timeout"):
        raise TimeoutError(message)
    raise RuntimeError(message)


# ---------------------------------------------------------------- source
def _short(name: str) -> str:
    name = name or ""
    if name.startswith("XCUIElementType"):
        return name[len("XCUIElementType") :] or "Other"
    return name.rsplit(".", 1)[-1] or "View"


def _ios_rect(el: ET.Element) -> Optional[list[int]]:
    try:
        x, y = int(float(el.get("x"))), int(float(el.get("y")))
        w, h = int(float(el.get("width"))), int(float(el.get("height")))
    except (TypeError, ValueError):
        return None
    return [x, y, x + w, y + h]


def _android_control(el: ET.Element, depth: int, path: str, kids: int) -> Control:
    bounds = _BOUNDS.search(el.get("bounds", ""))
    text = el.get("text", "") or ""
    desc = el.get("content-desc", "") or ""
    rid = el.get("resource-id", "") or ""
    cls = el.get("class", "") or el.tag
    return Control(
        control_type=_short(cls),
        title=desc or text,
        auto_id=rid.split("/", 1)[1] if "/" in rid else rid,
        class_name=cls,
        rect=[int(bounds.group(j)) for j in range(1, 5)] if bounds else None,
        enabled=el.get("enabled", "true") == "true",
        visible=el.get("displayed", "true") != "false",
        depth=depth,
        path=path,
        value=text or None,
        children=kids,
        extra={
            "package": el.get("package", ""),
            "content_desc": desc,
            "resource_id": rid,
            "clickable": el.get("clickable") == "true",
            "checked": el.get("checked") == "true",
            "selected": el.get("selected") == "true",
            "scrollable": el.get("scrollable") == "true",
        },
    )


def _ios_control(el: ET.Element, depth: int, path: str, kids: int) -> Control:
    etype = el.get("type", "") or el.tag
    name = el.get("name", "") or ""
    label = el.get("label", "") or ""
    return Control(
        control_type=_short(etype),
        title=label or name,
        auto_id=name,
        class_name=etype,
        rect=_ios_rect(el),
        enabled=el.get("enabled", "true") == "true",
        visible=el.get("visible", "true") != "false",
        depth=depth,
        path=path,
        value=el.get("value") or None,
        children=kids,
        extra={"label": label, "name": name, "accessible": el.get("accessible")},
    )


def parse_source(xml_text: str, platform: str, max_depth: int = 32) -> list[Control]:
    """Appium page source (Android UiAutomator2 or iOS XCUITest) → Control list."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise ValueError(f"invalid Appium page source XML: {exc}") from exc
    build = _ios_control if platform == "ios" else _android_control
    out: list[Control] = []

    def walk(node: ET.Element, depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        for i, kid in enumerate(list(node)):
            path = f"{prefix}/{i}" if prefix else str(i)
            out.append(build(kid, depth, path, len(list(kid))))
            walk(kid, depth + 1, path)

    walk(root, 0, "")
    return out


def _xpath_literal(text: str) -> str:
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat(" + ', "\'", '.join(f"'{p}'" for p in parts) + ")"


def xpath_for(ctl: Control, platform: str) -> Optional[str]:
    """An xpath that re-finds the control on the device, or None (use coordinates)."""
    if platform == "ios":
        if ctl.auto_id:
            return f"//{ctl.class_name}[@name={_xpath_literal(ctl.auto_id)}]"
        if ctl.title:
            return f"//{ctl.class_name}[@label={_xpath_literal(ctl.title)}]"
        return None
    rid = ctl.extra.get("resource_id") or ""
    if rid:
        return f"//*[@resource-id={_xpath_literal(rid)}]"
    if ctl.extra.get("content_desc"):
        return f"//*[@content-desc={_xpath_literal(ctl.extra['content_desc'])}]"
    if ctl.value:
        return f"//{ctl.class_name}[@text={_xpath_literal(ctl.value)}]"
    return None


# ---------------------------------------------------------------- driver
class AppiumDriver:
    """DesktopDriver for one Appium session (Android via UiAutomator2, iOS via XCUITest)."""

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:4723",
        capabilities: Optional[dict] = None,
        http: Optional[Http] = None,
        poll: float = 0.5,
        session_id: Optional[str] = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.capabilities = dict(capabilities or {})
        self.http = http or _default_http
        self.poll = poll
        self.session_id = session_id
        self.platform = str(self.capabilities.get("platformName", "")).lower()
        self.session_caps: dict = {}

    # ----------------------------------------------------------- plumbing
    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        status, payload = self.http(method, f"{self.server_url}{path}", body)
        _raise_for(payload, f"{method} {path}")
        if status >= 400:
            raise RuntimeError(f"{method} {path} → HTTP {status}")
        return payload.get("value") if isinstance(payload, dict) else payload

    def _session(self) -> str:
        if self.session_id:
            return self.session_id
        if not self.capabilities:
            raise RuntimeError(
                "Appium driver needs capabilities (platformName, appium:automationName, "
                "appium:deviceName …) — set them on the target or POLARIX_APPIUM_CAPS"
            )
        value = self._call(
            "POST", "/session", {"capabilities": {"alwaysMatch": self.capabilities}}
        )
        self.session_id = value.get("sessionId") or value.get("session_id")
        if not self.session_id:
            raise RuntimeError(f"Appium did not return a sessionId: {value}")
        self.session_caps = value.get("capabilities") or {}
        self.platform = str(
            self.session_caps.get("platformName") or self.platform or "android"
        ).lower()
        return self.session_id

    def _s(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        return self._call(method, f"/session/{self._session()}{path}", body)

    def _execute(self, script: str, **args: Any) -> Any:
        return self._s("POST", "/execute/sync", {"script": script, "args": [args]})

    def quit(self) -> None:
        if self.session_id:
            try:
                self._call("DELETE", f"/session/{self.session_id}")
            finally:
                self.session_id = None

    # ----------------------------------------------------------- windows
    def _foreground(self) -> Optional[tuple[str, str]]:
        """(app id, title). Android: (package, package/Activity). iOS: (bundleId, bundleId)."""
        if self.platform == "ios":
            info = self._execute("mobile: activeAppInfo") or {}
            bundle = info.get("bundleId") or ""
            return (bundle, bundle) if bundle else None
        pkg = self._s("GET", "/appium/device/current_package") or ""
        act = self._s("GET", "/appium/device/current_activity") or ""
        if not pkg:
            return None
        return pkg, f"{pkg}/{act}" if act else pkg

    @staticmethod
    def _matches(app: str, title: str, window: Optional[dict]) -> bool:
        if not window:
            return True
        if "title" in window and window["title"] not in (title, app):
            return False
        if "title_re" in window and not re.match(window["title_re"], title):
            return False
        if "process" in window and window["process"] != app:
            return False
        return True

    @staticmethod
    def _app_of(window: dict) -> str:
        return str(
            window.get("process") or str(window.get("title", "")).split("/", 1)[0]
        )

    def _screen_rect(self) -> Optional[list[int]]:
        try:
            r = self._s("GET", "/window/rect") or {}
            return [
                int(r.get("x", 0)),
                int(r.get("y", 0)),
                int(r.get("x", 0)) + int(r["width"]),
                int(r.get("y", 0)) + int(r["height"]),
            ]
        except (KeyError, TypeError, ValueError, RuntimeError):
            return None

    # ----------------------------------------------------------- contract
    def platform_info(self) -> dict:
        self._session()
        return {
            "driver": "appium",
            "backend": self.session_caps.get("automationName")
            or self.capabilities.get("appium:automationName")
            or "appium",
            "platform": (
                f"{self.session_caps.get('platformName', self.platform)} "
                f"{self.session_caps.get('platformVersion', '')}"
            ).strip(),
            "device": self.session_caps.get("deviceName")
            or self.capabilities.get("appium:deviceName"),
            "server": self.server_url,
            "session_id": self.session_id,
        }

    def list_windows(self, title_filter: Optional[str] = None) -> list[dict]:
        fg = self._foreground()
        if not fg or (title_filter and title_filter.lower() not in fg[1].lower()):
            return []
        return [self.window_state({"title": fg[1]})]

    def launch(
        self,
        path: str,
        args: str = "",
        cwd: Optional[str] = None,
        wait_seconds: float = 3.0,
        title_re: Optional[str] = None,
    ) -> dict:
        app = path.strip().split("/", 1)[0]
        self._execute("mobile: activateApp", appId=app, bundleId=app)
        deadline = time.monotonic() + max(wait_seconds, 1.0)
        while True:
            fg = self._foreground()
            if fg and fg[0] == app and (not title_re or re.match(title_re, fg[1])):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll)
        return self.window_state({"process": app})

    def window_state(self, window: dict) -> dict:
        fg = self._foreground()
        if not fg:
            raise LookupError("no foreground app (device locked or no session?)")
        app, title = fg
        if not self._matches(app, title, window):
            raise LookupError(f"foreground app is {title}, not {window}")
        return {
            "title": title,
            "class_name": title.split("/", 1)[1] if "/" in title else app,
            "process": app,
            "pid": 0,
            "handle": 0,
            "rect": self._screen_rect(),
            "is_active": True,
        }

    def focus(self, window: dict) -> None:
        app = self._app_of(window)
        fg = self._foreground()
        if app and (not fg or fg[0] != app):
            self._execute("mobile: activateApp", appId=app, bundleId=app)

    def close(self, window: dict, force: bool = False) -> None:
        app = self._app_of(window) or (self._foreground() or ("", ""))[0]
        if force or self.platform == "ios":
            self._execute("mobile: terminateApp", appId=app, bundleId=app)
        else:
            self._execute("mobile: pressKey", keycode=_ANDROID_KEYCODES["BACK"])

    def inventory(self, window: dict, max_depth: int = 8) -> list[Control]:
        if window:
            self.window_state(window)
        return parse_source(self._s("GET", "/source") or "", self.platform, max_depth)

    def find(self, window: dict, locator: dict) -> list[Control]:
        return find_controls(self.inventory(window, 32), locator)

    def _one(self, window: dict, locator: dict) -> Control:
        hits = self.find(window, locator)
        if not hits:
            raise LookupError(f"Control not found: {locator}")
        return hits[0]

    def _element(self, ctl: Control) -> Optional[str]:
        xpath = xpath_for(ctl, self.platform)
        if not xpath:
            return None
        try:
            value = self._s("POST", "/element", {"using": "xpath", "value": xpath})
        except LookupError:
            return None
        if not isinstance(value, dict):
            return None
        return next((v for k, v in value.items() if "element" in k.lower()), None)

    @staticmethod
    def _centre(ctl: Control) -> tuple[int, int]:
        if not ctl.rect:
            raise LookupError(f"control has no bounds: {ctl.locator()}")
        left, top, right, bottom = ctl.rect
        return (left + right) // 2, (top + bottom) // 2

    def _tap(self, x: int, y: int, hold_ms: int = 50, times: int = 1) -> None:
        actions = []
        for _ in range(times):
            actions += [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": hold_ms},
                {"type": "pointerUp", "button": 0},
            ]
        self._s(
            "POST",
            "/actions",
            {
                "actions": [
                    {
                        "type": "pointer",
                        "id": "finger1",
                        "parameters": {"pointerType": "touch"},
                        "actions": actions,
                    }
                ]
            },
        )

    def click(
        self,
        window: dict,
        locator: Optional[dict] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        double: bool = False,
    ) -> dict:
        hit: Any = None
        if locator is not None:
            ctl = self._one(window, locator)
            hit = ctl.to_dict()
            x, y = self._centre(ctl)
            if button == "left" and not double:
                el = self._element(ctl)
                if el:
                    self._s("POST", f"/element/{el}/click")
                    return {"hit": hit, "x": x, "y": y, "via": "element"}
        elif x is None or y is None:
            raise ValueError("click needs a locator or x/y coordinates")
        if button == "right":
            self._tap(x, y, hold_ms=800)
        else:
            self._tap(x, y, times=2 if double else 1)
        return {"hit": hit, "x": x, "y": y, "via": "pointer"}

    def _type_text(self, text: str) -> None:
        if not text:
            return
        if self.platform == "ios":
            self._execute("mobile: keys", keys=list(text))
        else:
            self._execute("mobile: type", text=text)

    def set_text(self, window: dict, locator: dict, value: str) -> None:
        ctl = self._one(window, locator)
        el = self._element(ctl)
        if el:
            self._s("POST", f"/element/{el}/clear")
            self._s(
                "POST", f"/element/{el}/value", {"text": value, "value": list(value)}
            )
            return
        x, y = self._centre(ctl)
        self._tap(x, y)
        self._type_text(value)

    def _press(self, name: str) -> None:
        if self.platform == "ios":
            if name == "ENTER":
                self._execute("mobile: keys", keys=["\n"])
            elif name in _IOS_BUTTONS:
                self._execute("mobile: pressButton", name=_IOS_BUTTONS[name])
            else:
                raise LookupError(f"iOS has no key '{name}'")
            return
        code = _ANDROID_KEYCODES.get(name)
        if code is None:
            raise LookupError(f"unknown key '{name}' for Android")
        self._execute("mobile: pressKey", keycode=code)

    def type_keys(
        self, window: dict, keys: str, locator: Optional[dict] = None
    ) -> None:
        if locator:
            self.click(window, locator)
        buffer = ""
        for token in re.split(r"(\{[^}]+\})", keys):
            if not token:
                continue
            if token.startswith("{") and token.endswith("}") and len(token) > 3:
                self._type_text(buffer)
                buffer = ""
                self._press(token[1:-1].upper())
            elif token.startswith("{") and token.endswith("}"):
                buffer += token[1:-1]
            else:
                buffer += token.translate(str.maketrans("", "", "^%+~"))
        self._type_text(buffer)

    def select(self, window: dict, locator: dict, item: str) -> None:
        self.click(window, locator)
        time.sleep(self.poll)
        self.click(window, {"title": item})

    def menu_select(self, window: dict, path: str) -> None:
        raise LookupError(
            "mobile apps have no menu bar; click the overflow/more button and then "
            "the item by title"
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
                    raise TimeoutError(f"app did not come to front: {window}") from None
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
        data = base64.b64decode(self._s("GET", "/screenshot") or "")
        if not data.startswith(b"\x89PNG"):
            raise RuntimeError("Appium screenshot did not return a PNG")
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
        if self.platform == "ios":
            raise RuntimeError(
                "run_command is not available on iOS (no shell); use a `shell` step on "
                "the Mac target that hosts the Appium server instead"
            )
        t0 = time.monotonic()
        parts = command.split()
        try:
            out = self._execute(
                "mobile: shell",
                command=parts[0],
                args=parts[1:],
                timeout=int(timeout * 1000),
                includeStderr=True,
            )
        except RuntimeError as exc:
            if "relaxed" in str(exc).lower() or "insecure" in str(exc).lower():
                raise RuntimeError(
                    "mobile: shell needs the Appium server started with "
                    "--relaxed-security (or --allow-insecure=adb_shell)"
                ) from exc
            raise
        if isinstance(out, dict):
            stdout, stderr = str(out.get("stdout", "")), str(out.get("stderr", ""))
        else:
            stdout, stderr = str(out or ""), ""
        return {
            "exit_code": 0,
            "stdout": stdout[-20_000:],
            "stderr": stderr[-20_000:],
            "duration_ms": round((time.monotonic() - t0) * 1000),
            "on": "device",
        }
