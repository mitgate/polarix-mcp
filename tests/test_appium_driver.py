"""Appium driver over a scripted W3C server — Android and iOS, no device needed."""

from __future__ import annotations

import base64
import json

import pytest

from polarix.desktop import targets
from polarix.desktop.appium_driver import AppiumDriver, parse_source, xpath_for
from polarix.desktop.driver import get_driver
from polarix.desktop.steps import run_desktop_steps

ANDROID_SOURCE = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy index="0" class="hierarchy" rotation="0" width="1080" height="1920">
  <android.widget.FrameLayout index="0" package="com.android.calculator2" class="android.widget.FrameLayout" text="" resource-id="" content-desc="" enabled="true" bounds="[0,0][1080,1920]" displayed="true">
    <android.widget.EditText index="0" package="com.android.calculator2" class="android.widget.EditText" text="7+8" resource-id="com.android.calculator2:id/formula" content-desc="" enabled="true" bounds="[0,200][1080,400]" displayed="true"/>
    <android.widget.TextView index="1" package="com.android.calculator2" class="android.widget.TextView" text="15" resource-id="com.android.calculator2:id/result" content-desc="" enabled="true" bounds="[0,400][1080,560]" displayed="true"/>
    <android.widget.Button index="2" package="com.android.calculator2" class="android.widget.Button" text="+" resource-id="com.android.calculator2:id/op_add" content-desc="plus" clickable="true" enabled="true" bounds="[720,600][1080,900]" displayed="true"/>
    <android.widget.ImageButton index="3" package="com.android.calculator2" class="android.widget.ImageButton" text="" resource-id="" content-desc="More options" clickable="true" enabled="false" bounds="[900,0][1080,180]" displayed="true"/>
  </android.widget.FrameLayout>
</hierarchy>"""

IOS_SOURCE = """<?xml version="1.0" encoding="UTF-8"?>
<AppiumAUT>
  <XCUIElementTypeApplication type="XCUIElementTypeApplication" name="Calculator" label="Calculator" enabled="true" visible="true" x="0" y="0" width="393" height="852">
    <XCUIElementTypeWindow type="XCUIElementTypeWindow" enabled="true" visible="true" x="0" y="0" width="393" height="852">
      <XCUIElementTypeStaticText type="XCUIElementTypeStaticText" name="display" label="15" value="15" enabled="true" visible="true" x="20" y="100" width="353" height="80"/>
      <XCUIElementTypeButton type="XCUIElementTypeButton" name="plus" label="Add" enabled="true" visible="true" x="300" y="500" width="80" height="80"/>
      <XCUIElementTypeTextField type="XCUIElementTypeTextField" name="" label="" value="" enabled="true" visible="true" x="20" y="700" width="353" height="44"/>
      <XCUIElementTypeButton type="XCUIElementTypeButton" name="" label="" enabled="false" visible="false" x="0" y="0" width="0" height="0"/>
    </XCUIElementTypeWindow>
  </XCUIElementTypeApplication>
</AppiumAUT>"""

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8).decode()


class ScriptedAppium:
    """Just enough of the W3C/Appium surface for the driver to work."""

    def __init__(self, platform: str):
        self.platform = platform
        self.calls: list[tuple[str, str, dict | None]] = []
        self.front = (
            ("com.android.calculator2", "com.android.calculator2.Calculator")
            if platform == "android"
            else ("com.apple.calculator", "")
        )
        self.relaxed = True

    def __call__(self, method, url, body):
        path = "/" + (url.split("/", 3)[3] if url.count("/") >= 3 else "")
        self.calls.append((method, path, body))
        if path == "/session" and method == "POST":
            return self._new_session(body)
        assert path.startswith("/session/S1"), path
        sub = path[len("/session/S1") :]
        if method == "DELETE" and sub == "":
            return 200, {"value": None}
        if sub == "/element":
            return self._find(body)
        if sub == "/execute/sync":
            return self._script(body["script"], body["args"][0])
        if sub.startswith("/element/E1/") or sub == "/actions":
            return 200, {"value": None}
        return self._static(sub)

    def _new_session(self, body):
        caps = body["capabilities"]["alwaysMatch"]
        return 200, {
            "value": {
                "sessionId": "S1",
                "capabilities": {
                    **{k.replace("appium:", ""): v for k, v in caps.items()},
                    "platformName": caps["platformName"],
                    "platformVersion": "17.4" if self.platform == "ios" else "14",
                },
            }
        }

    def _static(self, sub):
        size = (1080, 1920) if self.platform == "android" else (393, 852)
        table = {
            "/source": ANDROID_SOURCE if self.platform == "android" else IOS_SOURCE,
            "/appium/device/current_package": self.front[0],
            "/appium/device/current_activity": self.front[1],
            "/window/rect": {"x": 0, "y": 0, "width": size[0], "height": size[1]},
            "/screenshot": PNG,
        }
        if sub in table:
            return 200, {"value": table[sub]}
        return 404, {"value": {"error": "unknown command", "message": sub}}

    def _find(self, body):
        xp = body["value"]
        known = (
            ("op_add", "formula", "result", "More options")
            if self.platform == "android"
            else ("plus", "display")
        )
        if any(k in xp for k in known):
            return 200, {"value": {"element-6066-11e4-a52e-4f735466cecf": "E1"}}
        return 404, {"value": {"error": "no such element", "message": xp}}

    def _script(self, script, args):
        if script == "mobile: activeAppInfo":
            return 200, {"value": {"bundleId": self.front[0], "pid": 42}}
        if script == "mobile: activateApp":
            app = args.get("appId") or args.get("bundleId")
            self.front = (
                app,
                (
                    "com.android.calculator2.Calculator"
                    if self.platform == "android"
                    else ""
                ),
            )
            return 200, {"value": None}
        if script == "mobile: terminateApp":
            self.front = (
                ("com.android.launcher", "Home")
                if self.platform == "android"
                else ("com.apple.springboard", "")
            )
            return 200, {"value": True}
        if script == "mobile: shell":
            if not self.relaxed:
                return 500, {
                    "value": {
                        "error": "unknown error",
                        "message": "Potentially insecure feature 'adb_shell' has not been enabled. Use --relaxed-security",
                    }
                }
            return 200, {
                "value": {
                    "stdout": " ".join([args["command"], *args["args"]]) + "\n",
                    "stderr": "",
                }
            }
        if script in (
            "mobile: pressKey",
            "mobile: pressButton",
            "mobile: type",
            "mobile: keys",
        ):
            return 200, {"value": None}
        return 500, {"value": {"error": "unknown method", "message": script}}


@pytest.fixture
def android():
    server = ScriptedAppium("android")
    drv = AppiumDriver(
        "http://appium.lan:4723/",
        {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:deviceName": "emulator-5554",
        },
        http=server,
        poll=0,
    )
    return drv, server


@pytest.fixture
def ios():
    server = ScriptedAppium("ios")
    drv = AppiumDriver(
        "http://mac-mini.lan:4723",
        {
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:deviceName": "iPhone 15",
        },
        http=server,
        poll=0,
    )
    return drv, server


def _scripts(server):
    return [b["script"] for m, p, b in server.calls if p.endswith("/execute/sync")]


def test_parse_android_source_uses_class_tags():
    controls = parse_source(ANDROID_SOURCE, "android")
    plus = next(c for c in controls if c.auto_id == "op_add")
    assert plus.control_type == "Button" and plus.title == "plus" and plus.value == "+"
    assert plus.rect == [720, 600, 1080, 900] and plus.path == "0/2" and plus.depth == 1
    more = next(c for c in controls if c.title == "More options")
    assert more.enabled is False and more.auto_id == ""
    assert (
        xpath_for(plus, "android")
        == "//*[@resource-id='com.android.calculator2:id/op_add']"
    )
    assert xpath_for(more, "android") == "//*[@content-desc='More options']"
    with pytest.raises(ValueError, match="invalid Appium page source"):
        parse_source("<broken", "android")


def test_parse_ios_source_maps_xcui_types():
    controls = parse_source(IOS_SOURCE, "ios")
    app = controls[0]
    assert (
        app.control_type == "Application"
        and app.title == "Calculator"
        and app.children == 1
    )
    plus = next(c for c in controls if c.auto_id == "plus")
    assert plus.control_type == "Button" and plus.title == "Add"
    assert plus.rect == [300, 500, 380, 580] and plus.path == "0/0/1"
    display = next(c for c in controls if c.auto_id == "display")
    assert display.value == "15" and display.title == "15"
    field = next(c for c in controls if c.control_type == "TextField")
    assert (
        field.title == "" and xpath_for(field, "ios") is None
    )  # nothing to find it by
    hidden = controls[-1]
    assert hidden.visible is False and hidden.enabled is False
    assert xpath_for(plus, "ios") == "//XCUIElementTypeButton[@name='plus']"
    assert xpath_for(display, "ios") == "//XCUIElementTypeStaticText[@name='display']"


def test_android_session_windows_and_platform(android):
    drv, server = android
    info = drv.platform_info()
    assert info["driver"] == "appium" and info["backend"] == "UiAutomator2"
    assert info["platform"] == "Android 14" and info["session_id"] == "S1"
    assert server.calls[0][1] == "/session"
    win = drv.window_state({"process": "com.android.calculator2"})
    assert win["title"] == "com.android.calculator2/com.android.calculator2.Calculator"
    assert win["rect"] == [0, 0, 1080, 1920] and win["class_name"].endswith(
        "Calculator"
    )
    assert drv.list_windows("calculator")[0]["process"] == "com.android.calculator2"
    assert drv.list_windows("maps") == []
    with pytest.raises(LookupError):
        drv.window_state({"process": "com.other"})
    drv.quit()
    assert server.calls[-1][:2] == ("DELETE", "/session/S1")
    assert drv.session_id is None


def test_android_click_prefers_element_and_falls_back_to_pointer(android):
    drv, server = android
    win = {"process": "com.android.calculator2"}
    out = drv.click(win, {"auto_id": "op_add"})
    assert out["via"] == "element" and out["x"] == 900 and out["y"] == 750
    assert ("POST", "/session/S1/element/E1/click", None) in server.calls
    # a control nothing can find by attribute → W3C pointer tap at the centre
    out = drv.click(win, x=10, y=20)
    assert out["via"] == "pointer"
    actions = server.calls[-1][2]["actions"][0]["actions"]
    assert actions[0] == {"type": "pointerMove", "duration": 0, "x": 10, "y": 20}
    # right click = long press (800 ms), double = two taps
    drv.click(win, x=1, y=1, button="right")
    assert server.calls[-1][2]["actions"][0]["actions"][2]["duration"] == 800
    drv.click(win, x=1, y=1, double=True)
    assert len(server.calls[-1][2]["actions"][0]["actions"]) == 8


def test_android_text_keys_shell_and_screenshot(android):
    drv, server = android
    win = {"process": "com.android.calculator2"}
    drv.set_text(win, {"auto_id": "formula"}, "1+1")
    assert ("POST", "/session/S1/element/E1/clear", None) in server.calls
    assert server.calls[-1][2]["text"] == "1+1"
    drv.type_keys(win, "ab{ENTER}{+}c{BACK}")
    scripts = _scripts(server)[-4:]
    assert scripts == [
        "mobile: type",
        "mobile: pressKey",
        "mobile: type",
        "mobile: pressKey",
    ]
    bodies = [b["args"][0] for m, p, b in server.calls if p.endswith("/execute/sync")][
        -4:
    ]
    assert bodies[0]["text"] == "ab" and bodies[1]["keycode"] == 66
    assert bodies[2]["text"] == "+c" and bodies[3]["keycode"] == 4
    assert drv.screenshot()[:4] == b"\x89PNG"
    out = drv.run_command("pm list packages")
    assert out["stdout"].strip() == "pm list packages" and out["on"] == "device"
    server.relaxed = False
    with pytest.raises(RuntimeError, match="relaxed-security"):
        drv.run_command("id")
    with pytest.raises(LookupError, match="menu bar"):
        drv.menu_select(win, "File->Open")
    assert drv.control_from_point(1000, 100).title == "More options"


def test_android_launch_and_close(android):
    drv, server = android
    server.front = ("com.android.launcher", "Home")
    win = drv.launch("com.android.calculator2", wait_seconds=0)
    assert win["process"] == "com.android.calculator2"
    assert _scripts(server)[-1] == "mobile: activateApp"
    drv.close(win)  # soft close = BACK on Android
    assert _scripts(server)[-1] == "mobile: pressKey"
    drv.close(win, force=True)
    assert _scripts(server)[-1] == "mobile: terminateApp"
    with pytest.raises(LookupError):
        drv.window_state({"process": "com.android.calculator2"})


def test_ios_contract(ios):
    drv, server = ios
    info = drv.platform_info()
    assert info["platform"] == "iOS 17.4" and info["backend"] == "XCUITest"
    win = drv.window_state({"process": "com.apple.calculator"})
    assert win["title"] == "com.apple.calculator" and win["rect"] == [0, 0, 393, 852]
    out = drv.click(win, {"auto_id": "plus"})
    assert out["via"] == "element" and out["x"] == 340
    drv.set_text(win, {"control_type": "TextField"}, "hello")  # no name → tap + keys
    assert _scripts(server)[-1] == "mobile: keys"
    body = [b for m, p, b in server.calls if p.endswith("/execute/sync")][-1]
    assert body["args"][0]["keys"] == list("hello")
    drv.type_keys(win, "x{ENTER}{HOME}")
    assert _scripts(server)[-3:] == [
        "mobile: keys",
        "mobile: keys",
        "mobile: pressButton",
    ]
    with pytest.raises(LookupError, match="iOS has no key"):
        drv.type_keys(win, "{BACK}")
    with pytest.raises(RuntimeError, match="not available on iOS"):
        drv.run_command("ls")
    drv.close(win)
    assert _scripts(server)[-1] == "mobile: terminateApp"


def test_steps_run_unchanged_on_ios(ios):
    drv, _ = ios
    steps = [
        {"action": "click", "locator": {"auto_id": "plus"}, "wait_after": 0},
        {
            "action": "assert",
            "kind": "text_equals",
            "locator": {"auto_id": "display"},
            "expected": "15",
        },
        {
            "action": "assert",
            "kind": "control_exists",
            "locator": {"title": "Add", "control_type": "Button"},
        },
        {"action": "assert", "kind": "window_title_contains", "expected": "calculator"},
    ]
    results, warnings, _ = run_desktop_steps(
        drv, {"process": "com.apple.calculator"}, steps
    )
    assert [r["success"] for r in results] == [True] * 4, results
    assert warnings == []


def test_errors_and_missing_capabilities():
    def http(method, url, body):
        return 404, {"value": {"error": "no such element", "message": "nope"}}

    drv = AppiumDriver(http=http, capabilities={"platformName": "Android"})
    with pytest.raises(LookupError, match="no such element"):
        drv._call("POST", "/session")
    with pytest.raises(RuntimeError, match="needs capabilities"):
        AppiumDriver(http=http).platform_info()

    def timeout(method, url, body):
        return 500, {"value": {"error": "timeout", "message": "slow"}}

    with pytest.raises(TimeoutError):
        AppiumDriver(http=timeout, capabilities={"platformName": "iOS"})._call(
            "GET", "/x"
        )


def test_appium_target_and_env_driver(monkeypatch):
    caps = {
        "platformName": "iOS",
        "appium:automationName": "XCUITest",
        "appium:deviceName": "iPhone 15",
    }
    monkeypatch.setenv("POLARIX_TARGETS_FILE", "/nonexistent/targets.json")
    monkeypatch.setenv(
        "POLARIX_TARGETS",
        json.dumps(
            {
                "iphone": {
                    "driver": "appium",
                    "server_url": "http://mac.lan:4723",
                    "capabilities": caps,
                }
            }
        ),
    )
    cfg = targets.load_targets()["iphone"]
    assert cfg["os"] == "ios"
    drv = get_driver(target="iphone")
    assert isinstance(drv, AppiumDriver) and drv.server_url == "http://mac.lan:4723"
    assert drv.platform == "ios" and drv.capabilities == caps
    entry = next(t for t in targets.describe() if t["name"] == "iphone")
    assert (
        entry["automation"] == "XCUITest"
        and entry["server_url"] == "http://mac.lan:4723"
    )
    monkeypatch.setenv("POLARIX_TARGETS", json.dumps({"bad": {"driver": "appium"}}))
    with pytest.raises(ValueError, match="need capabilities"):
        targets.load_targets()
    monkeypatch.delenv("POLARIX_TARGETS")
    monkeypatch.delenv("POLARIX_TARGET", raising=False)
    monkeypatch.setenv("POLARIX_DESKTOP_DRIVER", "appium")
    monkeypatch.setenv("POLARIX_APPIUM_URL", "http://127.0.0.1:4724")
    monkeypatch.setenv("POLARIX_APPIUM_CAPS", json.dumps({"platformName": "Android"}))
    drv = get_driver()
    assert drv.server_url == "http://127.0.0.1:4724" and drv.platform == "android"
