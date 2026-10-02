"""Macro recorder — turns real mouse/keyboard activity into a step sequence.

Clicks are resolved to the control under the cursor (driver.control_from_point)
so the recorded step uses a locator, not raw coordinates; when nothing is found
(custom-drawn canvas, for instance) the click falls back to coordinates relative
to the window. Printable keys are coalesced into one `type` step; special keys
become `press` steps. pynput is imported lazily — the event handlers are plain
methods so the coalescing logic is testable without it.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from polarix.desktop.driver import DesktopDriver, DesktopUnavailable
from polarix.desktop.model import Control

_DOUBLE_CLICK_SECONDS = 0.4
_SPECIAL = {
    "enter": "Enter",
    "tab": "Tab",
    "esc": "Escape",
    "backspace": "Backspace",
    "delete": "Delete",
    "space": None,  # typed as a literal space
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "page_up": "PageUp",
    "page_down": "PageDown",
}
_MODIFIER_KEYS = {
    "ctrl",
    "ctrl_l",
    "ctrl_r",
    "alt",
    "alt_l",
    "alt_r",
    "alt_gr",
    "shift",
    "shift_l",
    "shift_r",
    "cmd",
}


class MacroRecorder:
    def __init__(
        self,
        driver: DesktopDriver,
        window: Optional[dict],
        name: str,
        stop_key: str = "f10",
    ) -> None:
        self.driver = driver
        self.window = window
        self.name = name
        self.stop_key = stop_key.lower()
        self.steps: list[dict] = []
        self.started_at: Optional[float] = None
        self.stopped_at: Optional[float] = None
        self._text: list[str] = []
        self._mods: set[str] = set()
        self._last_click: tuple[float, int, int] = (0.0, -1, -1)
        self._listeners: list[Any] = []
        self._lock = threading.Lock()
        self.stop_requested = threading.Event()

    # --------------------------------------------------------------- helpers
    def _flush_text(self) -> None:
        if self._text:
            self.steps.append({"action": "type", "text": "".join(self._text)})
            self._text = []

    def _locator_for(self, ctl: Optional[Control], x: int, y: int) -> dict:
        if ctl is not None and (ctl.auto_id or ctl.title):
            return {"locator": ctl.locator()}
        rel_x, rel_y = x, y
        try:
            if self.window is not None:
                rect = self.driver.window_state(self.window).get("rect")
                if rect:
                    rel_x, rel_y = x - rect[0], y - rect[1]
        except Exception:
            pass
        return {"x": rel_x, "y": rel_y}

    # ------------------------------------------------------------- handlers
    def handle_click(self, x: int, y: int, button: str = "left") -> None:
        with self._lock:
            self._flush_text()
            now = time.monotonic()
            last_t, last_x, last_y = self._last_click
            is_double = (
                button == "left"
                and now - last_t < _DOUBLE_CLICK_SECONDS
                and abs(x - last_x) <= 3
                and abs(y - last_y) <= 3
            )
            self._last_click = (now, x, y)
            if is_double and self.steps and self.steps[-1]["action"] == "click":
                self.steps[-1]["action"] = "double_click"
                return
            ctl = None
            try:
                ctl = self.driver.control_from_point(x, y)
            except Exception:
                ctl = None
            step: dict = {
                "action": "right_click" if button == "right" else "click",
                **self._locator_for(ctl, x, y),
            }
            if ctl is not None:
                step["_recorded"] = {
                    "control_type": ctl.control_type,
                    "title": ctl.title,
                }
            self.steps.append(step)

    def handle_key(self, name: str, char: Optional[str] = None) -> bool:
        """Return True when the stop key was pressed."""
        key = (name or "").lower()
        with self._lock:
            if key == self.stop_key:
                self._flush_text()
                return True
            if key in _MODIFIER_KEYS:
                self._mods.add(key.split("_")[0])
                return False
            if self._mods and (char or key):
                self._flush_text()
                combo = "+".join(sorted(self._mods)) + "+" + (char or key)
                self.steps.append({"action": "press", "key": combo})
                return False
            if key in _SPECIAL:
                mapped = _SPECIAL[key]
                if mapped is None:
                    self._text.append(" ")
                    return False
                self._flush_text()
                self.steps.append({"action": "press", "key": mapped})
                return False
            if key.startswith("f") and key[1:].isdigit():
                self._flush_text()
                self.steps.append({"action": "press", "key": key.upper()})
                return False
            if char:
                self._text.append(char)
            return False

    def handle_key_release(self, name: str) -> None:
        key = (name or "").lower()
        if key in _MODIFIER_KEYS:
            self._mods.discard(key.split("_")[0])

    # -------------------------------------------------------------- control
    def start(self) -> None:
        try:
            from pynput import keyboard, mouse
        except ImportError as exc:
            raise DesktopUnavailable(
                "Macro recording needs pynput: pip install pynput"
            ) from exc

        self.started_at = time.time()

        def on_click(x: int, y: int, button: Any, pressed: bool) -> None:
            if not pressed:
                return
            bname = "right" if "right" in str(button) else "left"
            self.handle_click(int(x), int(y), bname)

        def on_press(key: Any) -> Optional[bool]:
            char = getattr(key, "char", None)
            name = getattr(key, "name", None) or (char or "")
            if self.handle_key(str(name), char):
                self.stop_requested.set()
                return False
            return None

        def on_release(key: Any) -> None:
            self.handle_key_release(str(getattr(key, "name", "") or ""))

        self._listeners = [
            mouse.Listener(on_click=on_click),
            keyboard.Listener(on_press=on_press, on_release=on_release),
        ]
        for lst in self._listeners:
            lst.start()

    def stop(self) -> list[dict]:
        for lst in self._listeners:
            try:
                lst.stop()
            except Exception:
                pass
        self._listeners = []
        with self._lock:
            self._flush_text()
        self.stopped_at = time.time()
        if not self.steps or self.steps[-1]["action"] != "snapshot":
            self.steps.append({"action": "snapshot"})
        return self.steps

    def status(self) -> dict:
        return {
            "name": self.name,
            "window": self.window,
            "recording": bool(self._listeners) and not self.stop_requested.is_set(),
            "steps_so_far": len(self.steps),
            "started_at": self.started_at,
            "stop_key": self.stop_key,
        }


# name → active recorder (one process, one keyboard; still keyed to allow status)
ACTIVE: dict[str, MacroRecorder] = {}
