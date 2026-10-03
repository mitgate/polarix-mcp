"""Simulated desktop app — lets the whole desktop pipeline run on any OS.

The fixture is a dict of windows; each window carries a nested control tree
and a menu bar. Controls may declare `on_click` effects (open/close windows,
set the window title from control values, reveal or hide controls), which is
enough to exercise map → plan → execute → diff end to end without Windows.

Default fixture: a Notepad-like editor with a "Save as" dialog. Override with
POLARIX_DESKTOP_FAKE_APP=/path/to/app.json.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import platform
import re
import time
from typing import Any, Optional

from polarix.desktop.model import Control, find_controls

# 1x1 transparent PNG — enough to prove the screenshot path works.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

DEFAULT_APP: dict = {
    "name": "Editor",
    "windows": [
        {
            "title": "Sem título - Editor",
            "class_name": "EditorMain",
            "process": "editor.exe",
            "pid": 4242,
            "launch_path": "editor.exe",
            "open": True,
            "rect": [100, 100, 900, 700],
            "menus": {
                "Arquivo": [
                    {"title": "Novo", "on_click": {"set_value": {"15": ""}}},
                    {"title": "Abrir..."},
                    {"title": "Salvar", "on_click": {"open_window": "Salvar como"}},
                    {"title": "Sair", "on_click": {"close_window": True}},
                ],
                "Editar": [{"title": "Desfazer"}, {"title": "Selecionar tudo"}],
                "Ajuda": [{"title": "Sobre o Editor"}],
            },
            "controls": [
                {
                    "control_type": "MenuBar",
                    "title": "Aplicativo",
                    "rect": [100, 130, 900, 150],
                    "children": [
                        {"control_type": "MenuItem", "title": "Arquivo"},
                        {"control_type": "MenuItem", "title": "Editar"},
                        {"control_type": "MenuItem", "title": "Ajuda"},
                    ],
                },
                {
                    "control_type": "Edit",
                    "title": "Editor de texto",
                    "auto_id": "15",
                    "class_name": "Edit",
                    "rect": [100, 150, 900, 680],
                    "value": "",
                },
                {
                    "control_type": "StatusBar",
                    "title": "Ln 1, Col 1",
                    "auto_id": "1025",
                    "rect": [100, 680, 900, 700],
                },
            ],
        },
        {
            "title": "Salvar como",
            "class_name": "#32770",
            "process": "editor.exe",
            "pid": 4242,
            "open": False,
            "rect": [300, 300, 700, 500],
            "menus": {},
            "controls": [
                {
                    "control_type": "Text",
                    "title": "Nome:",
                    "rect": [310, 340, 360, 360],
                },
                {
                    "control_type": "Edit",
                    "title": "Nome do arquivo",
                    "auto_id": "1001",
                    "rect": [370, 340, 690, 360],
                    "value": "",
                },
                {
                    "control_type": "ComboBox",
                    "title": "Tipo",
                    "auto_id": "1136",
                    "rect": [370, 370, 690, 390],
                    "value": "Texto (*.txt)",
                },
                {
                    "control_type": "Button",
                    "title": "Salvar",
                    "auto_id": "1",
                    "rect": [500, 450, 590, 480],
                    "on_click": {
                        "close_window": True,
                        "set_title": {
                            "window": "Sem título - Editor",
                            "template": "{1001} - Editor",
                        },
                    },
                },
                {
                    "control_type": "Button",
                    "title": "Cancelar",
                    "auto_id": "2",
                    "rect": [600, 450, 690, 480],
                    "on_click": {"close_window": True},
                },
            ],
        },
    ],
}


def render_window(w: dict, controls: list[Control]) -> bytes:
    """Deterministic PNG of a fixture window: one filled box per control + title."""
    import hashlib
    import io

    from PIL import Image, ImageDraw

    left, top, right, bottom = w.get("rect") or [0, 0, 400, 300]
    width, height = max(right - left, 1), max(bottom - top, 1)
    img = Image.new("RGB", (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, 24], fill=(60, 60, 90))
    draw.text((6, 6), w["title"], fill=(255, 255, 255))
    for c in controls:
        if not c.rect:
            continue
        x0, y0, x1, y1 = (
            c.rect[0] - left,
            c.rect[1] - top,
            c.rect[2] - left,
            c.rect[3] - top,
        )
        digest = hashlib.md5(c.key().encode()).digest()
        fill = (120 + digest[0] % 120, 120 + digest[1] % 120, 120 + digest[2] % 120)
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=fill, outline=(30, 30, 30))
        label = c.value if c.value not in (None, "") else c.title
        if label:
            draw.text((x0 + 4, y0 + 3), str(label)[:40], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeDesktopDriver:
    """In-memory DesktopDriver. State lives for the lifetime of the instance."""

    def __init__(self, app: Optional[dict] = None) -> None:
        self.app = copy.deepcopy(app or DEFAULT_APP)
        self.windows: list[dict] = self.app["windows"]
        self.active: Optional[dict] = next(
            (w for w in self.windows if w.get("open")), None
        )
        self.log: list[dict] = []

    @classmethod
    def from_env(cls) -> "FakeDesktopDriver":
        path = os.getenv("POLARIX_DESKTOP_FAKE_APP", "").strip()
        if path:
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f))
        return cls()

    # ------------------------------------------------------------------ meta
    def platform_info(self) -> dict:
        return {
            "driver": "fake",
            "backend": "fake",
            "platform": platform.system(),
            "app": self.app.get("name", "fake"),
        }

    def _record(self, action: str, **data: Any) -> None:
        self.log.append({"action": action, "t": time.time(), **data})

    # --------------------------------------------------------------- windows
    def _window_info(self, w: dict) -> dict:
        return {
            "title": w["title"],
            "class_name": w.get("class_name", ""),
            "process": w.get("process", ""),
            "pid": w.get("pid", 0),
            "handle": abs(hash(w["title"])) % 100_000,
            "rect": w.get("rect"),
            "is_active": w is self.active,
        }

    def _window_by_title(self, title: str) -> Optional[dict]:
        return next((w for w in self.windows if w["title"] == title), None)

    def _window_matches(self, w: dict, window: dict) -> bool:
        info = self._window_info(w)
        checks = {
            "title": lambda v: w["title"] == v,
            "title_re": lambda v: re.match(v, w["title"]) is not None,
            "process": lambda v: w.get("process") == v,
            "pid": lambda v: w.get("pid") == int(v),
            "class_name": lambda v: w.get("class_name") == v,
            "handle": lambda v: info["handle"] == int(v),
        }
        return all(checks[k](v) for k, v in window.items() if k in checks)

    def _resolve(self, window: Optional[dict], must_be_open: bool = True) -> dict:
        if window is None:
            if self.active is None:
                raise LookupError("No active window")
            return self.active
        for w in self.windows:
            if must_be_open and not w.get("open"):
                continue
            if self._window_matches(w, window):
                return w
        raise LookupError(f"Window not found: {window}")

    def list_windows(self, title_filter: Optional[str] = None) -> list[dict]:
        out = []
        for w in self.windows:
            if not w.get("open"):
                continue
            if title_filter and title_filter.lower() not in w["title"].lower():
                continue
            out.append(self._window_info(w))
        return out

    def launch(
        self,
        path: str,
        args: str = "",
        cwd: Optional[str] = None,
        wait_seconds: float = 3.0,
        title_re: Optional[str] = None,
    ) -> dict:
        target = next(
            (w for w in self.windows if w.get("launch_path") == path), None
        ) or next((w for w in self.windows if not w.get("open")), None)
        if target is None:
            raise LookupError(f"Nothing to launch for '{path}'")
        target["open"] = True
        self.active = target
        self._record("launch", path=path, args=args)
        return self._window_info(target)

    def window_state(self, window: dict) -> dict:
        return self._window_info(self._resolve(window))

    def focus(self, window: dict) -> None:
        self.active = self._resolve(window)
        self._record("focus", title=self.active["title"])

    def close(self, window: dict, force: bool = False) -> None:
        w = self._resolve(window)
        w["open"] = False
        if self.active is w:
            self.active = next((x for x in self.windows if x.get("open")), None)
        self._record("close", title=w["title"], force=force)

    # -------------------------------------------------------------- controls
    def _flatten(
        self, nodes: list[dict], depth: int, prefix: str, max_depth: int
    ) -> list[Control]:
        out: list[Control] = []
        if depth > max_depth:
            return out
        for i, node in enumerate(nodes):
            if node.get("hidden"):
                continue
            path = f"{prefix}{i}" if not prefix else f"{prefix}/{i}"
            kids = node.get("children", [])
            out.append(
                Control(
                    control_type=node.get("control_type", "Custom"),
                    title=node.get("title", ""),
                    auto_id=str(node.get("auto_id", "")),
                    class_name=node.get("class_name", ""),
                    rect=node.get("rect"),
                    enabled=node.get("enabled", True),
                    visible=node.get("visible", True),
                    depth=depth,
                    path=path,
                    value=node.get("value"),
                    children=len(kids),
                    extra={"node": node},
                )
            )
            out.extend(self._flatten(kids, depth + 1, path, max_depth))
        return out

    def inventory(self, window: dict, max_depth: int = 8) -> list[Control]:
        w = self._resolve(window)
        return self._flatten(w.get("controls", []), 0, "", max_depth)

    def find(self, window: dict, locator: dict) -> list[Control]:
        return find_controls(self.inventory(window, max_depth=32), locator)

    def _one(self, window: dict, locator: dict) -> Control:
        hits = self.find(window, locator)
        if not hits:
            raise LookupError(f"Control not found: {locator}")
        return hits[0]

    # --------------------------------------------------------------- effects
    def _values(self, w: dict) -> dict:
        return {
            c.auto_id: (c.value if c.value is not None else c.title)
            for c in self._flatten(w.get("controls", []), 0, "", 32)
            if c.auto_id
        }

    def _effect_open_window(self, w: dict, title: str) -> None:
        target = self._window_by_title(title)
        if target is None:
            raise LookupError(f"Fixture has no window '{title}'")
        target["open"] = True
        self.active = target

    def _effect_set_value(self, w: dict, values: dict) -> None:
        for auto_id, value in values.items():
            node = self._one({"title": w["title"]}, {"auto_id": str(auto_id)}).extra
            node["node"]["value"] = value

    def _effect_set_title(self, w: dict, spec: dict) -> None:
        target = self._window_by_title(spec["window"]) or w
        template: str = spec["template"]
        for auto_id, value in self._values(w).items():
            template = template.replace("{" + auto_id + "}", str(value))
        target["title"] = template

    def _effect_visibility(self, w: dict, auto_ids: list, hidden: bool) -> None:
        wanted = {str(a) for a in auto_ids}
        for c in self._flatten(w.get("controls", []), 0, "", 32):
            if c.auto_id in wanted:
                c.extra["node"]["hidden"] = hidden

    def _effect_close_window(self, w: dict) -> None:
        w["open"] = False
        if self.active is w:
            self.active = next((x for x in self.windows if x.get("open")), None)

    def _apply(self, w: dict, effect: Optional[dict]) -> None:
        """Apply an `on_click` effect declared in the fixture."""
        if not effect:
            return
        if title := effect.get("open_window"):
            self._effect_open_window(w, title)
        if "set_value" in effect:
            self._effect_set_value(w, effect["set_value"])
        if spec := effect.get("set_title"):
            self._effect_set_title(w, spec)
        if effect.get("reveal"):
            self._effect_visibility(w, effect["reveal"], hidden=False)
        if effect.get("hide"):
            self._effect_visibility(w, effect["hide"], hidden=True)
        if effect.get("close_window"):
            self._effect_close_window(w)

    # --------------------------------------------------------------- actions
    def click(
        self,
        window: dict,
        locator: Optional[dict] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        double: bool = False,
    ) -> dict:
        w = self._resolve(window)
        if locator is not None:
            ctl = self._one(window, locator)
        elif x is not None and y is not None:
            left, top = (w.get("rect") or [0, 0, 0, 0])[:2]
            ctl = next(
                (
                    c
                    for c in reversed(self.inventory(window, 32))
                    if c.contains_point(left + x, top + y)
                ),
                None,
            )
            if ctl is None:
                self._record("click_coords", x=x, y=y, button=button)
                return {"hit": None, "x": x, "y": y}
        else:
            raise ValueError("click needs a locator or x/y coordinates")
        if not ctl.enabled:
            raise RuntimeError(f"Control is disabled: {ctl.locator()}")
        self._record("click", control=ctl.key(), button=button, double=double)
        if button == "left":
            self._apply(w, ctl.extra["node"].get("on_click"))
        return {"hit": ctl.to_dict()}

    def set_text(self, window: dict, locator: dict, value: str) -> None:
        ctl = self._one(window, locator)
        if ctl.control_type not in ("Edit", "Document", "ComboBox"):
            raise RuntimeError(f"Cannot set text on a {ctl.control_type}")
        ctl.extra["node"]["value"] = value
        self._record("set_text", control=ctl.key(), value=value)

    def type_keys(
        self, window: dict, keys: str, locator: Optional[dict] = None
    ) -> None:
        w = self._resolve(window)
        target = (
            self._one(window, locator)
            if locator
            else next(
                (c for c in self.inventory(window, 32) if c.control_type == "Edit"),
                None,
            )
        )
        # Escape closes a dialog (class #32770), like Windows does.
        if "{ESC}" in keys and w.get("class_name") == "#32770":
            self._record("escape", window=w["title"])
            self._effect_close_window(w)
            return
        # pywinauto syntax: {x} types a literal x, {NAME} is a named key,
        # bare ^ % + ~ are modifiers. Keep literals, drop the rest.
        plain = re.sub(r"\{(.)\}", lambda m: "\x00" + m.group(1), keys)
        plain = re.sub(r"(?<!\x00)\{[^{}\x00]*\}", "", plain)
        plain = re.sub(r"(?<!\x00)[\^%+~]", "", plain).replace("\x00", "")
        if target is not None and plain:
            node = target.extra["node"]
            node["value"] = (node.get("value") or "") + plain
        self._record("type_keys", window=w["title"], keys=keys)

    def select(self, window: dict, locator: dict, item: str) -> None:
        ctl = self._one(window, locator)
        ctl.extra["node"]["value"] = item
        self._record("select", control=ctl.key(), item=item)

    def menu_select(self, window: dict, path: str) -> None:
        w = self._resolve(window)
        parts = [p.strip() for p in re.split(r"->|>", path) if p.strip()]
        if len(parts) < 2:
            raise ValueError("menu path must be 'Top->Item' (use -> as separator)")
        items = w.get("menus", {}).get(parts[0])
        if items is None:
            raise LookupError(f"Menu '{parts[0]}' not found in '{w['title']}'")
        item = next((it for it in items if it["title"] == parts[1]), None)
        if item is None:
            raise LookupError(f"Menu item '{parts[1]}' not found under '{parts[0]}'")
        self._record("menu_select", window=w["title"], path=path)
        self._apply(w, item.get("on_click"))

    def menu_items(self, window: dict, expand: bool = False) -> list[dict]:
        w = self._resolve(window)
        return [
            {"title": top, "items": [it["title"] for it in items]}
            for top, items in w.get("menus", {}).items()
        ]

    # ----------------------------------------------------------------- waits
    def wait_window(self, window: dict, timeout: float = 10.0) -> dict:
        try:
            return self._window_info(self._resolve(window))
        except LookupError as exc:
            raise TimeoutError(f"Window did not appear: {window}") from exc

    def wait_control(
        self,
        window: dict,
        locator: dict,
        timeout: float = 10.0,
        state: str = "visible",
    ) -> Control:
        hits = self.find(window, locator)
        if not hits:
            raise TimeoutError(f"Control did not appear: {locator}")
        ctl = hits[0]
        if state == "enabled" and not ctl.enabled:
            raise TimeoutError(f"Control never became enabled: {locator}")
        return ctl

    # --------------------------------------------------------------- capture
    def screenshot(self, window: Optional[dict] = None) -> bytes:
        """Render the window from its control rects so image matching is testable.

        Pixel (0, 0) is the window's top-left corner, like pywinauto's
        capture_as_image(); matched coordinates feed click {x, y} directly.
        """
        self._record("screenshot")
        try:
            w = self._resolve(window)
        except LookupError:
            return _PNG_1X1
        return render_window(w, self.inventory({"title": w["title"]}, 32))

    def run_command(
        self, command: str, cwd: Optional[str] = None, timeout: float = 120.0
    ) -> dict:
        """Simulated shell: commands starting with 'false' or containing 'exit 1' fail."""
        self._record("run_command", command=command, cwd=cwd)
        text = command.strip()
        failed = text.startswith("false") or "exit 1" in text
        return {
            "exit_code": 1 if failed else 0,
            "stdout": "" if failed else f"simulated: {text}",
            "stderr": "simulated failure" if failed else "",
            "duration_ms": 1,
        }

    def control_from_point(self, x: int, y: int) -> Optional[Control]:
        if self.active is None:
            return None
        for c in reversed(self.inventory({"title": self.active["title"]}, 32)):
            if c.contains_point(x, y):
                return c
        return None
