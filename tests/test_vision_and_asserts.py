from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from polarix.desktop import steps as steps_mod
from polarix.desktop import vision
from polarix.desktop.steps import run_assert, run_desktop_steps
from tests.conftest import EDITOR, SAVE_AS


def _crop(png: bytes, rect: list[int]) -> bytes:
    img = Image.open(io.BytesIO(png))
    out = io.BytesIO()
    img.crop(tuple(rect)).save(out, format="PNG")
    return out.getvalue()


def _window_rect(fake, locator: dict, auto_id: str) -> list[int]:
    ctl = fake.find(locator, {"auto_id": auto_id})[0]
    w = fake.window_state(locator)["rect"]
    return [
        ctl.rect[0] - w[0],
        ctl.rect[1] - w[1],
        ctl.rect[2] - w[0],
        ctl.rect[3] - w[1],
    ]


def test_fake_screenshot_renders_window_size(fake):
    png = fake.screenshot(EDITOR)
    img = Image.open(io.BytesIO(png))
    assert img.size == (800, 600)


def test_find_template_locates_control_crop(fake):
    shot = fake.screenshot(EDITOR)
    rect = _window_rect(fake, EDITOR, "1025")  # status bar
    found = vision.find_template(shot, _crop(shot, rect), threshold=0.9)
    assert found["found"] and found["best"]["score"] >= 0.99
    assert rect[0] <= found["best"]["x"] <= rect[2]
    assert rect[1] <= found["best"]["y"] <= rect[3]


def test_find_template_rejects_oversized_template(fake):
    shot = fake.screenshot(SAVE_AS) if False else fake.screenshot(EDITOR)
    big = Image.new("RGB", (900, 700), "white")
    out = io.BytesIO()
    big.save(out, format="PNG")
    with pytest.raises(ValueError, match="larger"):
        vision.find_template(shot, out.getvalue())


def test_load_image_arg_forms(tmp_path):
    raw = b"\x89PNG\r\n\x1a\nxyz"
    assert (
        vision.load_image_arg("data:image/png;base64," + base64.b64encode(raw).decode())
        == raw
    )
    assert vision.load_image_arg(base64.b64encode(raw).decode()) == raw
    p = tmp_path / "t.png"
    p.write_bytes(raw)
    assert vision.load_image_arg(str(p)) == raw
    with pytest.raises(ValueError):
        vision.load_image_arg("")


def test_click_image_step_hits_the_control(fake):
    shot = fake.screenshot(EDITOR)
    template = _crop(shot, _window_rect(fake, EDITOR, "15"))
    tpl_b64 = "data:image/png;base64," + base64.b64encode(template).decode()
    results, _, _ = run_desktop_steps(
        fake,
        EDITOR,
        [{"action": "click_image", "image": tpl_b64, "wait_after": 0}],
    )
    assert results[0]["success"], results[0]
    assert results[0]["result"]["hit"]["auto_id"] == "15"
    assert results[0]["result"]["match"]["score"] >= 0.99


def test_wait_for_image_and_image_asserts(fake):
    shot = fake.screenshot(EDITOR)
    tpl = base64.b64encode(_crop(shot, _window_rect(fake, EDITOR, "1025"))).decode()
    steps = [
        {"action": "wait_for", "image": tpl, "timeout": 1},
        {"action": "assert", "kind": "image_present", "image": tpl},
        {"action": "assert", "kind": "image_absent", "image": tpl},
    ]
    results, _, _ = run_desktop_steps(fake, EDITOR, steps, stop_on_error=False)
    assert [r["success"] for r in results] == [True, True, False]
    assert results[2]["error"].startswith("AssertionError")


def test_click_vision_uses_model_coordinates(fake, monkeypatch):
    async def fake_vision_json(prompt, png, model=None):
        return {
            "found": True,
            "x": 400,
            "y": 300,
            "confidence": 0.9,
            "reasoning": "editor",
        }

    monkeypatch.setattr(vision, "vision_json", fake_vision_json)
    results, _, _ = run_desktop_steps(
        fake,
        EDITOR,
        [{"action": "click_vision", "description": "the text area", "wait_after": 0}],
    )
    assert results[0]["success"]
    assert results[0]["result"]["hit"]["auto_id"] == "15"


def test_vision_locate_rejects_out_of_bounds(fake, monkeypatch):
    async def fake_vision_json(prompt, png, model=None):
        return {"found": True, "x": 5000, "y": 5, "confidence": 0.9, "reasoning": "x"}

    monkeypatch.setattr(vision, "vision_json", fake_vision_json)
    out = steps_mod._run_async(vision.vision_locate(fake.screenshot(EDITOR), "x"))
    assert out["found"] is False and "rejected" in out["reasoning"]


def test_vision_assert(fake, monkeypatch):
    async def fake_vision_json(prompt, png, model=None):
        return {
            "passed": "error" not in prompt,
            "confidence": 0.8,
            "reasoning": "r",
            "observed": "o",
        }

    monkeypatch.setattr(vision, "vision_json", fake_vision_json)
    ok = run_assert(fake, EDITOR, {"kind": "vision", "expectation": "editor is open"})
    bad = run_assert(fake, EDITOR, {"kind": "vision", "expectation": "an error dialog"})
    assert ok["passed"] is True and bad["passed"] is False


@pytest.mark.parametrize(
    "step,expected",
    [
        ({"kind": "control_exists", "locator": {"auto_id": "15"}}, True),
        ({"kind": "control_absent", "locator": {"auto_id": "15"}}, False),
        ({"kind": "control_enabled", "locator": {"auto_id": "15"}}, True),
        (
            {
                "kind": "text_equals",
                "locator": {"auto_id": "1025"},
                "expected": "Ln 1, Col 1",
            },
            True,
        ),
        (
            {
                "kind": "text_contains",
                "locator": {"auto_id": "1025"},
                "expected": "col 1",
            },
            True,
        ),
        (
            {"kind": "text_equals", "locator": {"auto_id": "1025"}, "expected": "x"},
            False,
        ),
        ({"kind": "window_exists"}, True),
        ({"kind": "window_absent", "window": {"title": "Ghost"}}, True),
        ({"kind": "window_title_contains", "expected": "editor"}, True),
    ],
)
def test_assert_kinds(fake, step, expected):
    assert run_assert(fake, EDITOR, step)["passed"] is expected


def test_assert_unknown_kind_is_an_input_error(fake):
    with pytest.raises(ValueError, match="assert kind"):
        run_assert(fake, EDITOR, {"kind": "nope"})


def test_wait_idle_returns_when_tree_is_stable(fake):
    results, _, _ = run_desktop_steps(
        fake, EDITOR, [{"action": "wait_idle", "interval": 0}]
    )
    assert results[0]["success"] and results[0]["result"]["idle"] is True


def test_locator_healing_via_recorded_hint_and_fuzzy_title(fake):
    steps = [
        {
            "action": "click",
            "locator": {"auto_id": "renamed-in-new-version"},
            "_recorded": {"title": "Editor de texto", "control_type": "Edit"},
            "wait_after": 0,
        },
        {"action": "set_text", "locator": {"title": "editor de TEXTO"}, "value": "ok"},
    ]
    results, warnings, _ = run_desktop_steps(fake, EDITOR, steps)
    assert all(r["success"] for r in results), results
    assert results[0]["healed_locator"] == {
        "title": "Editor de texto",
        "control_type": "Edit",
    }
    assert results[1]["healed_locator"] == {"auto_id": "15"}
    assert any("healed via recorded hint" in w for w in warnings)
    assert any("healed via fuzzy title" in w for w in warnings)
    assert fake.find(EDITOR, {"auto_id": "15"})[0].value == "ok"


def test_locator_without_any_hint_still_fails(fake):
    results, _, _ = run_desktop_steps(
        fake, EDITOR, [{"action": "click", "locator": {"auto_id": "zzz"}}]
    )
    assert (
        results[0]["success"] is False and "matched 0 controls" in results[0]["error"]
    )
