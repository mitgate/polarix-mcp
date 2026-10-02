from __future__ import annotations

import pytest

from polarix.desktop.model import (
    Control,
    build_control_index,
    diff_inventories,
    find_controls,
    parse_locator,
    parse_window,
)


def test_parse_locator_accepts_dict_and_shorthand():
    assert parse_locator({"auto_id": "1"}) == {"auto_id": "1"}
    assert parse_locator("auto_id=btnSave") == {"auto_id": "btnSave"}
    assert parse_locator("title=Salvar;control_type=Button") == {
        "title": "Salvar",
        "control_type": "Button",
    }
    assert parse_locator("found_index=2;title=x") == {"found_index": 2, "title": "x"}
    assert parse_locator("Salvar") == {"title": "Salvar"}


@pytest.mark.parametrize("raw", [None, "", {}, {"nope": 1}, "bogus=1", 42])
def test_parse_locator_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_locator(raw)


def test_parse_window_string_means_title_contains():
    loc = parse_window("CadApp 2026")
    assert loc == {"title_re": r".*CadApp\ 2026.*"}
    assert parse_window({"process": "CadApp.exe"}) == {"process": "CadApp.exe"}
    with pytest.raises(ValueError):
        parse_window({"url": "x"})


def _controls():
    return [
        Control("Button", title="Salvar", auto_id="1", path="0", depth=0),
        Control("Button", title="Salvar", auto_id="7", path="1", depth=0),
        Control("Edit", title="Nome", auto_id="1001", path="2", value="a"),
        Control("Pane", path="3"),
    ]


def test_find_controls_and_found_index():
    cs = _controls()
    assert len(find_controls(cs, {"title": "Salvar"})) == 2
    assert find_controls(cs, {"title": "Salvar", "found_index": 1})[0].auto_id == "7"
    assert find_controls(cs, {"title": "Salvar", "found_index": 9}) == []
    assert find_controls(cs, {"title_re": "Sal.*", "control_type": "Button"})
    assert find_controls(cs, {"path": "2"})[0].control_type == "Edit"


def test_control_key_and_locator_prefer_auto_id():
    c = Control("Button", title="Ok", auto_id="9", path="0/1")
    assert c.key() == "Button#9"
    assert c.locator() == {"auto_id": "9"}
    anon = Control("Pane", path="0/3")
    assert anon.key() == "Pane@0/3"
    assert anon.locator() == {"path": "0/3"}


def test_build_control_index_groups_interactive_types_only():
    idx = build_control_index(_controls())
    assert set(idx) == {"Button", "Edit"}  # Pane without title/auto_id is skipped
    assert idx["Edit"][0] == {
        "path": "2",
        "title": "Nome",
        "auto_id": "1001",
        "value": "a",
    }


def test_diff_inventories_reports_added_removed_and_changes():
    before = _controls()
    after = [
        Control("Button", title="Salvar", auto_id="1", path="0", enabled=False),
        Control("Edit", title="Nome", auto_id="1001", path="2", value="obra.txt"),
        Control("Text", title="Pronto", path="5"),
    ]
    d = diff_inventories(before, after)
    assert d["summary"] == {
        "added": 1,
        "removed": 2,
        "text_changes": 1,
        "state_changes": 1,
    }
    assert d["changed_texts"][0]["after"]["value"] == "obra.txt"
    assert d["changed_state"][0]["after"]["enabled"] is False
