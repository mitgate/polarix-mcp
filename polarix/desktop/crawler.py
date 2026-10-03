"""Application crawler — the desktop twin of browser_map_site.

Starting from one window, open every menu item, every "…" button and every tab;
whenever a new window or dialog appears, map it, crawl it (up to max_depth) and
close it again. The result is the application's feature map: which windows
exist, how each one is reached, what controls, menus and tabs it has, and which
actions were deliberately skipped (exit, destructive).

Safety
  * Items whose text means exit/quit/close are never selected.
  * Items whose text means delete/remove/uninstall/format/reset are skipped
    unless allow_destructive=True.
  * Dialogs are closed with Cancel/Close/No first, then Escape, then a window
    close — never with OK/Save/Yes.
  * A window budget and a time budget bound the crawl.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from polarix.desktop.driver import DesktopDriver
from polarix.desktop.model import Control, build_control_index, diff_inventories

EXIT_WORDS = {
    "exit",
    "quit",
    "close",
    "close window",
    "sair",
    "fechar",
    "encerrar",
    "salir",
    "beenden",
    "quitter",
    "shutdown",
    "log off",
    "logoff",
}
DESTRUCTIVE_WORDS = {
    "delete",
    "remove",
    "uninstall",
    "format",
    "erase",
    "reset",
    "clear all",
    "purge",
    "wipe",
    "excluir",
    "remover",
    "apagar",
    "desinstalar",
    "formatar",
    "redefinir",
    "limpar tudo",
    "zerar",
}
CANCEL_TITLES = ("Cancel", "Cancelar", "Close", "Fechar", "No", "Não", "Nein", "Non")
OPENER_RE = re.compile(r"(\.\.\.|…)\s*$")
_FEATURE_CONTROLS = (
    "Button",
    "SplitButton",
    "CheckBox",
    "RadioButton",
    "ComboBox",
    "Edit",
    "Hyperlink",
)


@dataclass
class CrawlOptions:
    max_depth: int = 2
    max_windows: int = 25
    include_tabs: bool = True
    include_openers: bool = True
    allow_destructive: bool = False
    action_wait: float = 0.6
    time_budget: float = 180.0


@dataclass
class _Node:
    title: str
    locator: dict
    depth: int
    reach: list[dict]
    info: dict = field(default_factory=dict)
    inventory: list[Control] = field(default_factory=list)
    menus: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- helpers
def _norm(text: str) -> str:
    return re.sub(r"[\s_.…]+", " ", (text or "").replace("&", "")).strip().lower()


def _classify(label: str) -> Optional[str]:
    """None = safe, 'exit' or 'destructive' otherwise."""
    norm = _norm(label)
    if not norm:
        return None
    if norm in EXIT_WORDS or any(norm.startswith(w + " ") for w in EXIT_WORDS):
        return "exit"
    if any(w in norm for w in DESTRUCTIVE_WORDS):
        return "destructive"
    return None


def _shortcut(label: str) -> Optional[str]:
    """Win32 menus carry accelerators as 'Save\\tCtrl+S'."""
    if "\t" in label:
        return label.split("\t", 1)[1].strip() or None
    hit = re.search(r"\b((?:Ctrl|Alt|Shift|Win)\+[A-Za-z0-9+]+|F\d{1,2})\s*$", label)
    return hit.group(1) if hit else None


def _titles(driver: DesktopDriver) -> set[str]:
    try:
        return {w["title"] for w in driver.list_windows() if w.get("title")}
    except Exception:
        return set()


def _exists(driver: DesktopDriver, locator: dict) -> bool:
    try:
        driver.window_state(locator)
        return True
    except Exception:
        return False


def _changed(summary: dict) -> bool:
    return any(
        summary.get(k) for k in ("added", "removed", "text_changes", "state_changes")
    )


def _click_cancel(driver: DesktopDriver, locator: dict, wait: float) -> Optional[str]:
    try:
        inventory = driver.inventory(locator, 12)
    except Exception:
        return None
    buttons = {_norm(c.title): c for c in inventory if c.control_type == "Button"}
    for title in CANCEL_TITLES:
        ctl = buttons.get(_norm(title))
        if ctl is None:
            continue
        try:
            driver.click(locator, ctl.locator())
            time.sleep(wait)
        except Exception:
            continue
        if not _exists(driver, locator):
            return f"button:{ctl.title}"
    return None


def _close_window(driver: DesktopDriver, locator: dict, wait: float) -> str:
    """Close a dialog without confirming anything. Returns how it was closed."""
    how = _click_cancel(driver, locator, wait)
    if how:
        return how
    for name, fn in (
        ("escape", lambda: driver.type_keys(locator, "{ESC}")),
        ("close", lambda: driver.close(locator, force=False)),
    ):
        try:
            fn()
            time.sleep(wait)
        except Exception:
            continue
        if not _exists(driver, locator):
            return name
    return "still_open"


# ---------------------------------------------------------------- crawler
class _Crawler:
    def __init__(self, driver: DesktopDriver, root: dict, opt: CrawlOptions) -> None:
        self.driver = driver
        self.opt = opt
        self.t0 = time.monotonic()
        self.deadline = self.t0 + opt.time_budget
        self.root = _Node(title="", locator=dict(root), depth=0, reach=[])
        self.nodes: dict[str, _Node] = {}
        self.queue: list[_Node] = []
        self.edges: list[dict] = []
        self.effects: list[dict] = []
        self.skipped: list[dict] = []
        self.tabs: list[dict] = []
        self.problems: list[str] = []
        self.budget_exhausted = False

    # -- lifecycle
    def run(self) -> dict:
        self._map(self.root)
        self.root.title = self.root.info.get("title", "")
        self.nodes[self.root.title] = self.root
        self.queue.append(self.root)
        while self.queue:
            node = self.queue.pop(0)
            if not self._ensure_open(node):
                continue
            self._crawl_node(node)
            if node.depth > 0 and _exists(self.driver, node.locator):
                _close_window(self.driver, node.locator, self.opt.action_wait)
            if not _exists(self.driver, self.root.locator):
                self.problems.append("root window closed during crawl; stopping")
                break
        return self._assemble()

    def _over_budget(self) -> bool:
        return (
            len(self.nodes) >= self.opt.max_windows or time.monotonic() > self.deadline
        )

    def _map(self, node: _Node) -> None:
        node.info = self.driver.window_state(node.locator)
        node.inventory = self.driver.inventory(node.locator, 12)
        try:
            node.menus = self.driver.menu_items(node.locator, expand=True)
        except Exception:
            node.menus = []

    def _ensure_open(self, node: _Node) -> bool:
        """Dialogs were closed after mapping; replay their reach path to reopen."""
        if _exists(self.driver, node.locator):
            return True
        if node.depth == 0:
            self.problems.append(f"'{node.title}' vanished before it could be crawled")
            return False
        try:
            self.driver.focus(self.root.locator)
            for via in node.reach:
                self._do(
                    self.root.locator if via is node.reach[0] else node.locator, via
                )
        except Exception as exc:
            self.problems.append(f"could not reopen '{node.title}': {exc}")
            return False
        if not _exists(self.driver, node.locator):
            self.problems.append(f"'{node.title}' did not reopen via {node.reach}")
            return False
        return True

    def _do(self, locator: dict, via: dict) -> None:
        if via["action"] == "menu":
            self.driver.menu_select(locator, via["path"])
        else:
            self.driver.click(locator, via["locator"])
        time.sleep(self.opt.action_wait)

    # -- per node
    def _actions_for(self, node: _Node) -> list[dict]:
        actions: list[dict] = []
        for menu in node.menus:
            for item in menu.get("items", []):
                actions.append(
                    {
                        "action": "menu",
                        "path": f"{menu['title']}->{item}",
                        "label": item,
                    }
                )
        if self.opt.include_openers:
            for c in node.inventory:
                if c.control_type in ("Button", "SplitButton") and OPENER_RE.search(
                    c.title or ""
                ):
                    actions.append(
                        {"action": "click", "locator": c.locator(), "label": c.title}
                    )
        if self.opt.include_tabs:
            for c in node.inventory:
                if c.control_type == "TabItem":
                    actions.append(
                        {
                            "action": "click",
                            "locator": c.locator(),
                            "label": c.title,
                            "tab": True,
                        }
                    )
        return actions

    def _crawl_node(self, node: _Node) -> None:
        for act in self._actions_for(node):
            if self._over_budget():
                self.budget_exhausted = True
                return
            self._perform(node, act)
            if not _exists(self.driver, self.root.locator):
                return

    def _perform(self, node: _Node, act: dict) -> None:
        label = act.get("label", "")
        kind = _classify(label)
        if kind == "exit" or (kind == "destructive" and not self.opt.allow_destructive):
            self.skipped.append({"window": node.title, "via": act, "reason": kind})
            return
        via = {k: v for k, v in act.items() if k != "tab"}
        before = _titles(self.driver)
        try:
            self.driver.focus(node.locator)
            self._do(node.locator, via)
        except Exception as exc:
            self.problems.append(f"{node.title}: {via} failed: {exc}")
            self._escape(node)
            return
        if act.get("tab"):
            self._record_tab(node, label)
        self._register_new_windows(node, via, before)

    def _escape(self, node: _Node) -> None:
        try:
            self.driver.type_keys(node.locator, "{ESC}")
        except Exception:
            pass

    def _record_tab(self, node: _Node, label: str) -> None:
        try:
            now = self.driver.inventory(node.locator, 12)
        except Exception:
            return
        d = diff_inventories(node.inventory, now)
        self.tabs.append(
            {
                "window": node.title,
                "tab": label,
                "revealed": d["added"][:40],
                "hidden": len(d["removed"]),
            }
        )
        node.inventory = now

    def _register_new_windows(self, node: _Node, via: dict, before: set[str]) -> None:
        new_titles = sorted(_titles(self.driver) - before)
        if not new_titles:
            self._record_effect(node, via)
            return
        for title in new_titles:
            if self._over_budget():
                self.budget_exhausted = True
                _close_window(self.driver, {"title": title}, self.opt.action_wait)
                continue
            child = _Node(
                title=title,
                locator={"title": title},
                depth=node.depth + 1,
                reach=node.reach + [via],
            )
            try:
                self._map(child)
            except Exception as exc:
                self.problems.append(f"could not map '{title}': {exc}")
            self.nodes.setdefault(title, child)
            self.edges.append({"from": node.title, "via": via, "to": title})
            if child.depth < self.opt.max_depth and self.nodes[title] is child:
                self.queue.append(child)
            if (
                _close_window(self.driver, child.locator, self.opt.action_wait)
                == "still_open"
            ):
                self.problems.append(f"'{title}' did not close; crawl continued")

    def _record_effect(self, node: _Node, via: dict) -> None:
        try:
            now = self.driver.inventory(node.locator, 12)
        except Exception:
            return
        d = diff_inventories(node.inventory, now)
        if _changed(d["summary"]):
            self.effects.append(
                {"window": node.title, "via": via, "summary": d["summary"]}
            )
            node.inventory = now

    # -- output
    def _assemble(self) -> dict:
        windows, features = [], []
        total_controls = 0
        for title, node in self.nodes.items():
            total_controls += len(node.inventory)
            windows.append(
                {
                    "title": title,
                    "process": node.info.get("process"),
                    "depth": node.depth,
                    "reached_by": node.reach,
                    "control_count": len(node.inventory),
                    "control_index": build_control_index(node.inventory),
                    "menus": node.menus,
                }
            )
            features.extend(_features_of(node))
        kinds: dict[str, int] = {}
        for f in features:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        return {
            "root": self.root.title,
            "windows": windows,
            "edges": self.edges,
            "effects": self.effects,
            "tabs": self.tabs,
            "skipped": self.skipped,
            "feature_index": features,
            "coverage": {
                "windows_mapped": len(self.nodes),
                "dialogs_opened": len(self.edges),
                "controls_total": total_controls,
                "menu_paths": kinds.get("menu", 0),
                "features_by_kind": kinds,
                "actions_skipped": len(self.skipped),
                "budget_exhausted": self.budget_exhausted,
                "duration_ms": round((time.monotonic() - self.t0) * 1000),
            },
            "problems": self.problems,
        }


def _features_of(node: _Node) -> list[dict]:
    out: list[dict] = []
    if node.depth > 0:
        out.append(
            {
                "kind": "dialog",
                "name": node.title,
                "window": node.title,
                "reach": node.reach,
            }
        )
    for menu in node.menus:
        for item in menu.get("items", []):
            path = f"{menu['title']}->{item}"
            entry: dict[str, Any] = {
                "kind": "menu",
                "name": path,
                "window": node.title,
                "reach": node.reach + [{"action": "menu", "path": path}],
            }
            if sc := _shortcut(item):
                entry["shortcut"] = sc
            out.append(entry)
    for c in node.inventory:
        if c.control_type in _FEATURE_CONTROLS:
            out.append(
                {
                    "kind": c.control_type.lower(),
                    "name": c.title or c.auto_id,
                    "window": node.title,
                    "locator": c.locator(),
                    "reach": node.reach,
                }
            )
        elif c.control_type == "TabItem":
            out.append(
                {
                    "kind": "tab",
                    "name": c.title,
                    "window": node.title,
                    "locator": c.locator(),
                    "reach": node.reach + [{"action": "click", "locator": c.locator()}],
                }
            )
    return out


def crawl_app(
    driver: DesktopDriver, root: dict, options: Optional[CrawlOptions] = None
) -> dict:
    """Crawl every reachable window of the application rooted at `root`."""
    return _Crawler(driver, root, options or CrawlOptions()).run()
