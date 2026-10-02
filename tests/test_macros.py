from __future__ import annotations

import pytest

from polarix.desktop import macros


def test_save_load_list_delete_roundtrip(fake_env):
    doc = macros.save_macro("Salvar obra!", [{"action": "snapshot"}], {"title": "x"})
    assert doc["name"] == "Salvar_obra"
    assert doc["path"].endswith("Salvar_obra.json")
    loaded = macros.load_macro("Salvar obra!")
    assert loaded["steps"] == [{"action": "snapshot"}]
    assert loaded["window"] == {"title": "x"}
    listed = macros.list_macros()
    assert listed[0]["name"] == "Salvar_obra" and listed[0]["step_count"] == 1
    assert macros.delete_macro("Salvar obra!") is True
    assert macros.delete_macro("Salvar obra!") is False
    with pytest.raises(LookupError):
        macros.load_macro("Salvar obra!")


def test_empty_name_rejected(fake_env):
    with pytest.raises(ValueError):
        macros.save_macro("///", [], None)


def test_list_on_missing_dir_is_empty(fake_env):
    assert macros.list_macros() == []
