"""Android driver over a scripted adb — no device needed."""

from __future__ import annotations

import pytest

from polarix.desktop import targets
from polarix.desktop.android_driver import AndroidAdbDriver, parse_hierarchy
from polarix.desktop.driver import get_driver
from polarix.desktop.steps import run_desktop_steps

DUMP = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
 <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.calculator2" content-desc="" enabled="true" bounds="[0,0][1080,1920]">
  <node index="0" text="7+8" resource-id="com.android.calculator2:id/formula" class="android.widget.EditText" package="com.android.calculator2" content-desc="" enabled="true" bounds="[0,200][1080,400]"/>
  <node index="1" text="15" resource-id="com.android.calculator2:id/result" class="android.widget.TextView" package="com.android.calculator2" content-desc="" enabled="true" bounds="[0,400][1080,560]"/>
  <node index="2" text="" resource-id="com.android.calculator2:id/pad" class="android.widget.LinearLayout" package="com.android.calculator2" content-desc="" enabled="true" bounds="[0,600][1080,1920]">
   <node index="0" text="7" resource-id="com.android.calculator2:id/digit_7" class="android.widget.Button" package="com.android.calculator2" content-desc="" clickable="true" enabled="true" bounds="[0,600][360,900]"/>
   <node index="1" text="+" resource-id="com.android.calculator2:id/op_add" class="android.widget.Button" package="com.android.calculator2" content-desc="plus" clickable="true" enabled="true" bounds="[720,600][1080,900]"/>
   <node index="2" text="" resource-id="" class="android.widget.ImageButton" package="com.android.calculator2" content-desc="More options" clickable="true" enabled="false" bounds="[900,0][1080,180]"/>
  </node>
 </node>
</hierarchy>"""

FOCUS = "  mCurrentFocus=Window{1a2b3c u0 com.android.calculator2/com.android.calculator2.Calculator}\n"


class ScriptedAdb:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.focus = FOCUS

    def __call__(self, cmd):
        self.calls.append(cmd)
        tail = (
            " ".join(cmd[cmd.index("shell") + 1 :])
            if "shell" in cmd
            else " ".join(cmd[1:])
        )
        if tail.startswith("dumpsys window"):
            return 0, self.focus, ""
        if tail.startswith("uiautomator dump"):
            return 0, "UI hierchary dumped to: /sdcard/polarix_dump.xml", ""
        if tail.startswith("cat /sdcard/polarix_dump.xml"):
            return 0, DUMP, ""
        if tail == "wm size":
            return 0, "Physical size: 1080x1920\n", ""
        if tail.startswith("getprop ro.build.version.release"):
            return 0, "14\n", ""
        if tail.startswith("getprop ro.product.model"):
            return 0, "sdk_gphone64_x86_64\n", ""
        if tail.startswith("monkey -p com.android.calculator2"):
            self.focus = FOCUS
            return 0, "Events injected: 1", ""
        if tail.startswith(("input ", "am ")):
            return 0, "", ""
        if tail.startswith("echo"):
            return 0, tail[5:] + "\n", ""
        return 1, "", f"unscripted: {tail}"

    def binary(self, cmd):
        self.calls.append(cmd)
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


@pytest.fixture
def adb():
    return ScriptedAdb()


@pytest.fixture
def drv(adb):
    return AndroidAdbDriver(
        serial="emulator-5554", runner=adb, binary_runner=adb.binary, poll=0
    )


def test_parse_hierarchy_maps_attributes():
    controls = parse_hierarchy(DUMP)
    seven = next(c for c in controls if c.auto_id == "digit_7")
    assert (
        seven.control_type == "Button"
        and seven.title == "7"
        and seven.rect == [0, 600, 360, 900]
    )
    assert (
        seven.path == "0/2/0" and seven.depth == 2 and seven.extra["clickable"] is True
    )
    formula = next(c for c in controls if c.auto_id == "formula")
    assert formula.control_type == "EditText" and formula.value == "7+8"
    more = next(c for c in controls if c.title == "More options")
    assert (
        more.control_type == "ImageButton"
        and more.enabled is False
        and more.auto_id == ""
    )
    with pytest.raises(ValueError, match="invalid uiautomator XML"):
        parse_hierarchy("<broken")


def test_window_state_and_list(drv):
    win = drv.window_state({"process": "com.android.calculator2"})
    assert win["title"] == "com.android.calculator2/com.android.calculator2.Calculator"
    assert win["rect"] == [0, 0, 1080, 1920] and win["class_name"].endswith(
        "Calculator"
    )
    assert drv.list_windows("calculator")[0]["process"] == "com.android.calculator2"
    assert drv.list_windows("maps") == []
    with pytest.raises(LookupError):
        drv.window_state({"process": "com.other.app"})
    info = drv.platform_info()
    assert info["driver"] == "android" and info["platform"] == "Android 14"


def test_click_taps_the_centre_and_set_text_types(drv, adb):
    win = {"process": "com.android.calculator2"}
    hit = drv.click(win, {"auto_id": "digit_7"})
    assert hit["x"] == 180 and hit["y"] == 750
    assert adb.calls[-1][-1] == "input tap 180 750"
    drv.set_text(win, {"auto_id": "formula"}, "1 + 1")
    assert adb.calls[-1][-1] == "input text 1%s+%s1"
    drv.type_keys(win, "ab{ENTER}{BACK}")
    tails = [c[-1] for c in adb.calls[-3:]]
    assert tails == [
        "input text ab",
        "input keyevent KEYCODE_ENTER",
        "input keyevent KEYCODE_BACK",
    ]
    assert drv.screenshot()[:4] == b"\x89PNG"
    assert drv.control_from_point(1000, 100).title == "More options"
    with pytest.raises(LookupError, match="menu bar"):
        drv.menu_select(win, "File->Open")


def test_launch_close_and_shell(drv, adb):
    win = drv.launch("com.android.calculator2", wait_seconds=0)
    assert win["process"] == "com.android.calculator2"
    assert any(c[-1].startswith("monkey -p com.android.calculator2") for c in adb.calls)
    drv.close({"process": "com.android.calculator2"}, force=True)
    assert adb.calls[-1][-1] == "am force-stop com.android.calculator2"
    out = drv.run_command("echo hi")
    assert (
        out["exit_code"] == 0
        and out["stdout"].strip() == "hi"
        and out["on"] == "device"
    )
    assert adb.calls[-1][:3] == ["adb", "-s", "emulator-5554"]


def test_steps_run_unchanged_on_android(drv):
    steps = [
        {"action": "click", "locator": {"auto_id": "digit_7"}, "wait_after": 0},
        {
            "action": "click",
            "locator": {"title": "plus", "control_type": "Button"},
            "wait_after": 0,
        },
        {
            "action": "assert",
            "kind": "text_equals",
            "locator": {"auto_id": "result"},
            "expected": "15",
        },
        {
            "action": "assert",
            "kind": "control_disabled",
            "locator": {"title": "More options"},
        },
        {"action": "shell", "command": "echo ok"},
    ]
    results, warnings, _ = run_desktop_steps(
        drv, {"process": "com.android.calculator2"}, steps
    )
    assert [r["success"] for r in results] == [True] * 5, results
    assert warnings == []


def test_android_target_and_env_driver(monkeypatch, adb):
    monkeypatch.setenv(
        "POLARIX_TARGETS", '{"pixel": {"driver": "android", "serial": "emulator-5554"}}'
    )
    monkeypatch.setenv("POLARIX_TARGETS_FILE", "/nonexistent/targets.json")
    cfg = targets.load_targets()["pixel"]
    assert cfg["os"] == "android"
    drv = get_driver(target="pixel")
    assert isinstance(drv, AndroidAdbDriver) and drv.serial == "emulator-5554"
    monkeypatch.setenv("POLARIX_DESKTOP_DRIVER", "android")
    monkeypatch.setenv("POLARIX_ANDROID_SERIAL", "R58M1234")
    monkeypatch.delenv("POLARIX_TARGET", raising=False)
    assert get_driver().serial == "R58M1234"
