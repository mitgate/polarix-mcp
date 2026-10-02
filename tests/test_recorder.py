from __future__ import annotations

from polarix.desktop.recorder import MacroRecorder
from tests.conftest import EDITOR


def test_click_is_resolved_to_a_locator(fake):
    rec = MacroRecorder(fake, EDITOR, "demo")
    rec.handle_click(500, 400)  # inside the Edit "15" rect
    assert rec.steps == [
        {
            "action": "click",
            "locator": {"auto_id": "15"},
            "_recorded": {"control_type": "Edit", "title": "Editor de texto"},
        }
    ]


def test_click_outside_controls_falls_back_to_window_coords(fake):
    rec = MacroRecorder(fake, EDITOR, "demo")
    rec.handle_click(2000, 2000, button="right")
    assert rec.steps[0]["action"] == "right_click"
    assert rec.steps[0]["x"] == 1900 and rec.steps[0]["y"] == 1900  # minus [100,100]


def test_typing_is_coalesced_and_special_keys_flush(fake):
    rec = MacroRecorder(fake, EDITOR, "demo")
    for ch in "ola":
        rec.handle_key(ch, ch)
    rec.handle_key("space")
    rec.handle_key("x", "x")
    assert rec.handle_key("enter") is False
    rec.handle_key("ctrl_l")
    rec.handle_key("s", "s")
    rec.handle_key_release("ctrl_l")
    rec.handle_key("f5")
    assert rec.steps == [
        {"action": "type", "text": "ola x"},
        {"action": "press", "key": "Enter"},
        {"action": "press", "key": "ctrl+s"},
        {"action": "press", "key": "F5"},
    ]


def test_double_click_detection_and_stop_appends_snapshot(fake):
    rec = MacroRecorder(fake, EDITOR, "demo")
    rec.handle_click(500, 400)
    rec.handle_click(501, 401)
    assert rec.steps[-1]["action"] == "double_click"
    assert rec.handle_key("f10") is True  # stop key
    steps = rec.stop()
    assert steps[-1] == {"action": "snapshot"}
    assert rec.status()["recording"] is False
