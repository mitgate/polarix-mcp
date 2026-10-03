"""desktop_map_app — crawl every reachable feature of the simulated application."""

from __future__ import annotations

import asyncio
import copy
import json

from polarix.desktop.crawler import CrawlOptions, _classify, _shortcut, crawl_app
from polarix.desktop.fake_driver import DEFAULT_APP, FakeDesktopDriver
from polarix.metrics import ledger
from polarix.tools import desktop as dt
from tests.conftest import EDITOR

FAST = CrawlOptions(action_wait=0)


def test_crawl_maps_dialog_and_closes_it(fake):
    result = crawl_app(fake, EDITOR, FAST)
    titles = {w["title"] for w in result["windows"]}
    assert titles == {"Sem título - Editor", "Salvar como"}
    assert result["edges"] == [
        {
            "from": "Sem título - Editor",
            "via": {"action": "menu", "path": "Arquivo->Salvar", "label": "Salvar"},
            "to": "Salvar como",
        }
    ]
    dialog = next(w for w in result["windows"] if w["title"] == "Salvar como")
    assert dialog["depth"] == 1 and dialog["reached_by"][0]["path"] == "Arquivo->Salvar"
    assert {e["title"] for e in dialog["control_index"]["Button"]} == {
        "Salvar",
        "Cancelar",
    }
    # closed with Cancelar (Button#2), never with Salvar (Button#1): title unchanged, main window alone
    assert [w["title"] for w in fake.list_windows()] == ["Sem título - Editor"]
    clicked = [s["control"] for s in fake.log if s["action"] == "click"]
    assert "Button#2" in clicked and "Button#1" not in clicked


def test_exit_items_are_never_selected(fake):
    result = crawl_app(fake, EDITOR, FAST)
    skipped = {(s["via"]["path"], s["reason"]) for s in result["skipped"]}
    assert ("Arquivo->Sair", "exit") in skipped
    assert fake.list_windows()  # the app is still open
    assert result["coverage"]["actions_skipped"] == 1
    assert (
        result["coverage"]["windows_mapped"] == 2
        and result["coverage"]["dialogs_opened"] == 1
    )


def test_feature_index_lists_menus_dialogs_and_controls(fake):
    result = crawl_app(fake, EDITOR, FAST)
    kinds = result["coverage"]["features_by_kind"]
    assert kinds["menu"] == 7 and kinds["dialog"] == 1 and kinds["button"] == 2
    save = next(
        f
        for f in result["feature_index"]
        if f["kind"] == "button" and f["name"] == "Salvar"
    )
    assert save["window"] == "Salvar como" and save["locator"] == {"auto_id": "1"}
    assert save["reach"][0]["path"] == "Arquivo->Salvar"
    assert result["problems"] == [] and result["coverage"]["budget_exhausted"] is False


def test_destructive_items_need_opt_in():
    app = copy.deepcopy(DEFAULT_APP)
    app["windows"][0]["menus"]["Editar"].append(
        {"title": "Excluir tudo", "on_click": {"set_value": {"15": ""}}}
    )
    drv = FakeDesktopDriver(app)
    result = crawl_app(drv, EDITOR, FAST)
    assert any(
        s["via"]["path"] == "Editar->Excluir tudo" and s["reason"] == "destructive"
        for s in result["skipped"]
    )
    drv2 = FakeDesktopDriver(app)
    result2 = crawl_app(
        drv2, EDITOR, CrawlOptions(action_wait=0, allow_destructive=True)
    )
    assert not any(
        s["via"]["path"] == "Editar->Excluir tudo" for s in result2["skipped"]
    )


def test_window_budget_stops_the_crawl(fake):
    result = crawl_app(fake, EDITOR, CrawlOptions(action_wait=0, max_windows=1))
    assert result["coverage"]["windows_mapped"] == 1
    assert result["coverage"]["budget_exhausted"] is True
    assert [w["title"] for w in fake.list_windows()] == [
        "Sem título - Editor"
    ]  # dialog still closed


def test_classify_and_shortcut_helpers():
    assert _classify("E&xit") == "exit" and _classify("Sair") == "exit"
    assert (
        _classify("Delete project") == "destructive"
        and _classify("Formatar disco") == "destructive"
    )
    assert _classify("Save As...") is None
    assert _shortcut("Save\tCtrl+S") == "Ctrl+S" and _shortcut("Refresh F5") == "F5"
    assert _shortcut("Open...") is None


def test_tool_records_the_app_map_in_the_ledger(fake_env):
    out = json.loads(asyncio.run(dt.desktop_map_app(json.dumps(EDITOR), action_wait=0)))
    assert out["coverage"]["windows_mapped"] == 2
    assert out["_polarix"]["tool"] == "desktop_map_app"
    maps = ledger.fetch("maps")
    row = next(m for m in maps if "dialog:Salvar como" in (m["identifiers"] or ""))
    assert row["elements"] == out["coverage"]["controls_total"]
    assert "menu:Arquivo->Salvar" in row["identifiers"]
