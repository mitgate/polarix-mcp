from __future__ import annotations

import asyncio
import json
import os

import pytest

from polarix.desktop import scenarios as sc
from polarix.desktop.macros import save_macro
from polarix.tools import testing as tt
from tests.conftest import EDITOR, SAVE_AS

SAVE_SCENARIO = {
    "name": "salvar-obra",
    "description": "salva o documento como obra.txt",
    "tags": ["smoke"],
    "window": EDITOR,
    "steps": [
        {"action": "menu", "path": "Arquivo->Salvar", "wait_after": 0},
        {"action": "wait_for", "window": SAVE_AS},
        {"action": "assert", "kind": "control_exists", "locator": {"auto_id": "1001"}},
        {"action": "set_text", "locator": {"auto_id": "1001"}, "value": "obra.txt"},
        {"action": "click", "locator": {"auto_id": "1"}, "wait_after": 0},
        {"action": "wait_for", "window": EDITOR},
        {"action": "assert", "kind": "window_title_contains", "expected": "obra.txt"},
        {"action": "assert", "kind": "window_absent", "window": SAVE_AS},
    ],
    "teardown": [{"action": "snapshot"}],
}

FAILING_SCENARIO = {
    "name": "falha-proposital",
    "tags": ["regression"],
    "window": EDITOR,
    "steps": [
        {
            "action": "assert",
            "kind": "text_equals",
            "locator": {"auto_id": "1025"},
            "expected": "Ln 9",
        },
        {"action": "snapshot"},
    ],
}


def test_run_scenario_passes_with_assertions(fake):
    r = sc.run_scenario(fake, SAVE_SCENARIO)
    assert r["status"] == "passed", r["first_failure"]
    assert r["assertions"] == {"total": 3, "passed": 3, "failed": 0}
    assert "teardown" in r["phases"] and r["steps_succeeded"] == r["steps_total"]
    assert r["failure_screenshot"] is None


def test_run_scenario_fails_with_screenshot_and_first_failure(fake):
    r = sc.run_scenario(fake, FAILING_SCENARIO)
    assert r["status"] == "failed"
    assert r["assertions"] == {"total": 1, "passed": 0, "failed": 1}
    assert "steps step 1 (assert)" in r["first_failure"]
    assert r["failure_screenshot"].startswith("data:image/png;base64,")
    assert len(r["phases"]["steps"]) == 1  # stopped at the failure


def test_setup_failure_is_an_error_and_teardown_still_runs(fake):
    r = sc.run_scenario(
        fake,
        {
            "name": "setup-quebrado",
            "window": EDITOR,
            "setup": [{"action": "bogus"}],
            "steps": [{"action": "snapshot"}],
            "teardown": [{"action": "snapshot"}],
        },
    )
    assert r["status"] == "error"
    assert "steps" not in r["phases"] and "teardown" in r["phases"]


def test_vm_restore_is_called_when_backend_given(fake):
    calls = []

    class Backend:
        def snapshot_restore(self, vm, name):
            calls.append((vm, name))
            return {"vm": vm, "snapshot": name, "state": "running"}

    scn = {**SAVE_SCENARIO, "vm": {"name": "win11", "snapshot": "clean"}}
    r = sc.run_scenario(fake, scn, vm_backend=Backend())
    assert calls == [("win11", "clean")] and r["vm_restore"]["state"] == "running"


def test_validate_scenario_errors():
    with pytest.raises(ValueError, match="name"):
        sc.validate_scenario({"steps": [{"action": "snapshot"}]})
    with pytest.raises(ValueError, match="steps"):
        sc.validate_scenario({"name": "x", "steps": []})


def test_run_suite_kpis_and_tags(fake):
    from polarix.desktop.fake_driver import FakeDesktopDriver

    report = sc.run_suite(
        FakeDesktopDriver(), [SAVE_SCENARIO, FAILING_SCENARIO], name="demo"
    )
    k = report["kpis"]
    assert k["scenarios"] == 2 and k["passed"] == 1 and k["failed"] == 1
    assert k["pass_rate"] == 0.5 and k["assertions_total"] == 4
    assert report["failures"][0]["name"] == "falha-proposital"
    only_smoke = sc.run_suite(
        FakeDesktopDriver(), [SAVE_SCENARIO, FAILING_SCENARIO], tags=["smoke"]
    )
    assert only_smoke["kpis"]["scenarios"] == 1


def test_load_scenarios_from_yaml_dir_and_inline(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "name: a\nwindow: Editor\nsteps:\n  - action: snapshot\n", encoding="utf-8"
    )
    (tmp_path / "b.json").write_text(
        json.dumps({"scenarios": [{"name": "b", "steps": [{"action": "snapshot"}]}]})
    )
    (tmp_path / "ignore.txt").write_text("x")
    loaded = sc.load_scenarios(str(tmp_path))
    assert [s["name"] for s in loaded] == ["a", "b"]
    assert sc.validate_scenario(loaded[0])["window"] == {"title_re": ".*Editor.*"}
    inline = sc.load_scenarios('[{"name": "c", "steps": [{"action": "snapshot"}]}]')
    assert inline[0]["name"] == "c"


def test_scenario_from_macro_adds_assertions():
    macro = {
        "name": "gravado",
        "source": "recorded",
        "window": EDITOR,
        "steps": [
            {
                "action": "click",
                "locator": {"auto_id": "15"},
                "_recorded": {"title": "Editor de texto", "control_type": "Edit"},
            },
            {"action": "type", "text": "ola"},
            {"action": "snapshot"},
        ],
    }
    scn = sc.scenario_from_macro(macro)
    kinds = [s.get("kind") for s in scn["steps"] if s["action"] == "assert"]
    assert kinds == ["control_exists", "window_exists"]
    assert scn["steps"][-1]["kind"] == "window_exists"
    assert scn["steps"][-2]["action"] == "type"  # trailing snapshot removed
    assert scn["name"] == "macro-gravado" and "macro" in scn["tags"]


def test_save_report_writes_json_and_html(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("POLARIX_REPORTS_DIR", str(tmp_path / "reports"))
    report = sc.run_suite(fake, [FAILING_SCENARIO], name="rel")
    paths = sc.save_report(report, "rel")
    assert os.path.exists(paths["json"]) and os.path.exists(paths["html"])
    html = open(paths["html"], encoding="utf-8").read()
    assert (
        "falha-proposital" in html and "pass_rate" in html and "data:image/png" in html
    )


# ------------------------------------------------------------------ tool layer
def call(coro):
    return json.loads(asyncio.run(coro))


def test_tool_run_scenario_and_report_list(fake_env, monkeypatch, tmp_path):
    monkeypatch.setenv("POLARIX_REPORTS_DIR", str(tmp_path / "reports"))
    out = call(tt.desktop_run_scenario(json.dumps(SAVE_SCENARIO)))
    assert out["status"] == "passed" and out["report"]["html"].endswith(".html")
    assert out["_polarix"]["desktop"]["driver"] == "fake"
    listed = call(tt.desktop_report_list())
    assert listed["report_count"] == 1 and listed["reports"][0]["kpis"]["passed"] == 1


def test_tool_run_suite_from_dir_with_tags(fake_env, monkeypatch, tmp_path):
    monkeypatch.setenv("POLARIX_REPORTS_DIR", str(tmp_path / "reports"))
    d = tmp_path / "scen"
    d.mkdir()
    (d / "ok.json").write_text(json.dumps(SAVE_SCENARIO))
    (d / "bad.json").write_text(json.dumps(FAILING_SCENARIO))
    out = call(tt.desktop_run_suite(str(d), name="hack", tags_json='["smoke"]'))
    assert out["kpis"]["scenarios"] == 1 and out["kpis"]["pass_rate"] == 1.0
    full = call(tt.desktop_run_suite(str(d), name="hack2", include_results=True))
    assert full["kpis"]["failed"] == 1 and len(full["results"]) == 2


def test_tool_run_scenario_rejects_lists_and_bad_text(fake_env):
    assert (
        call(
            tt.desktop_run_scenario(
                '[{"name":"a","steps":[{"action":"snapshot"}]},{"name":"b","steps":[{"action":"snapshot"}]}]'
            )
        )["success"]
        is False
    )
    assert call(tt.desktop_run_scenario("{bad"))["success"] is False


def test_tool_scenario_from_macro(fake_env):
    save_macro(
        "gravado",
        [{"action": "click", "locator": {"auto_id": "15"}}, {"action": "snapshot"}],
        EDITOR,
        "recorded",
    )
    out = call(tt.desktop_scenario_from_macro("gravado", save_as_macro=True))
    assert out["scenario"]["name"] == "macro-gravado" and out["saved_macro"].endswith(
        "macro-gravado.json"
    )
    assert "vision" in out["assert_kinds"]


def test_tool_find_image_and_vision(fake_env, monkeypatch):
    import base64
    import io

    from PIL import Image

    from polarix.desktop import vision
    from polarix.desktop.fake_driver import FakeDesktopDriver

    drv = FakeDesktopDriver()
    shot = drv.screenshot(EDITOR)
    img = Image.open(io.BytesIO(shot)).crop((0, 580, 800, 600))  # status bar strip
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    tpl = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    found = call(tt.desktop_find_image(json.dumps(EDITOR), tpl))
    assert found["found"] and found["best"]["y"] >= 580

    async def fake_vision_json(prompt, png, model=None):
        return {
            "found": True,
            "x": 10,
            "y": 10,
            "confidence": 1,
            "reasoning": "r",
            "passed": True,
            "observed": "o",
        }

    monkeypatch.setattr(vision, "vision_json", fake_vision_json)
    loc = call(tt.desktop_vision_locate(json.dumps(EDITOR), "menu bar"))
    assert loc["found"] and loc["x"] == 10
    ver = call(tt.desktop_vision_verify(json.dumps(EDITOR), "editor is open"))
    assert ver["passed"] is True
