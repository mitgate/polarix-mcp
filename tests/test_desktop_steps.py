from __future__ import annotations

import pytest

from polarix.desktop.steps import key_to_pywinauto, run_desktop_steps
from tests.conftest import EDITOR, SAVE_AS

SAVE_FLOW = [
    {"action": "menu", "path": "Arquivo->Salvar", "wait_after": 0},
    {"action": "wait_for", "window": SAVE_AS},
    {"action": "set_text", "locator": {"auto_id": "1001"}, "value": "obra.txt"},
    {"action": "select", "locator": {"auto_id": "1136"}, "item": "Todos (*.*)"},
    {"action": "click", "locator": {"auto_id": "1"}, "wait_after": 0},
    {"action": "wait_for", "window": EDITOR},
    {"action": "snapshot"},
]


def test_save_flow_renames_main_window(fake):
    results, warnings, window = run_desktop_steps(fake, EDITOR, SAVE_FLOW)
    assert [r["success"] for r in results] == [True] * len(SAVE_FLOW)
    assert warnings == []
    assert window == EDITOR
    assert fake.list_windows()[0]["title"] == "obra.txt - Editor"
    snap = results[-1]["result"]
    assert snap["window"]["title"] == "obra.txt - Editor"
    assert any(c["control_type"] == "Edit" for c in snap["controls"])


def test_missing_locator_fails_and_warns(fake):
    steps = [
        {"action": "click", "locator": {"auto_id": "nope"}},
        {"action": "snapshot"},
    ]
    results, warnings, _ = run_desktop_steps(fake, EDITOR, steps)
    assert results[0]["success"] is False
    assert results[0]["locator_match_count"] == 0
    assert "matched 0 controls" in warnings[0]
    assert len(results) == 1  # stop_on_error


def test_stop_on_error_false_continues(fake):
    steps = [{"action": "bogus"}, {"action": "snapshot"}]
    results, _, _ = run_desktop_steps(fake, EDITOR, steps, stop_on_error=False)
    assert [r["success"] for r in results] == [False, True]
    assert "Unknown action" in results[0]["error"]


def test_type_text_escapes_pywinauto_specials(fake):
    steps = [{"action": "type", "text": "a+b {c}", "locator": {"auto_id": "15"}}]
    results, _, _ = run_desktop_steps(fake, EDITOR, steps)
    assert results[0]["success"]
    edit = fake.find(EDITOR, {"auto_id": "15"})[0]
    assert edit.value == "a+b {c}"


def test_coordinate_click_hits_control_under_point(fake):
    results, _, _ = run_desktop_steps(
        fake, EDITOR, [{"action": "click", "x": 400, "y": 300, "wait_after": 0}]
    )
    assert results[0]["result"]["hit"]["auto_id"] == "15"


def test_launch_and_close(fake):
    fake.close(EDITOR)
    steps = [
        {"action": "launch", "path": "editor.exe", "wait_seconds": 0},
        {"action": "press", "key": "ctrl+s"},
        {"action": "screenshot"},
        {"action": "close"},
    ]
    results, _, window = run_desktop_steps(fake, None, steps)
    assert all(r["success"] for r in results)
    assert results[2]["result"].startswith("data:image/png;base64,")
    assert fake.list_windows() == []
    assert window is not None


@pytest.mark.parametrize(
    "key,expected",
    [
        ("Enter", "{ENTER}"),
        ("ctrl+s", "^s"),
        ("Ctrl+Shift+S", "^+s"),
        ("alt+f", "%f"),
        ("F5", "{F5}"),
        ("ctrl+F12", "^{F12}"),
        ("Escape", "{ESC}"),
        ("{ENTER}", "{ENTER}"),
        ("^c", "^c"),
        ("pagedown", "{PGDN}"),
    ],
)
def test_key_to_pywinauto(key, expected):
    assert key_to_pywinauto(key) == expected


def test_wait_for_timeout_on_missing_window(fake):
    results, _, _ = run_desktop_steps(
        fake, EDITOR, [{"action": "wait_for", "window": {"title": "Ghost"}}]
    )
    assert results[0]["success"] is False
    assert results[0]["error"].startswith("TimeoutError")
