"""Real Windows driver built on pywinauto (UI Automation or Win32 backend).

Only imported on Windows by driver.get_driver(). Everything here maps the
DesktopDriver contract onto pywinauto's Desktop / Application / WindowSpecification
APIs. Not exercised by the test-suite on Linux — see HANDOFF.md.
"""

from __future__ import annotations

import io
import re
import time
from typing import Any, Optional

from pywinauto import Desktop
from pywinauto.application import Application

from polarix.desktop.model import Control, find_controls

_WINDOW_KW = {"title", "title_re", "class_name", "handle"}


def _text(wrapper: Any) -> str:
    try:
        return (wrapper.window_text() or "").strip()
    except Exception:
        return ""


def _control_type(wrapper: Any) -> str:
    info = wrapper.element_info
    ct = getattr(info, "control_type", None)
    if ct:
        return str(ct)
    try:
        return wrapper.friendly_class_name()
    except Exception:
        return wrapper.__class__.__name__.replace("Wrapper", "")


def _auto_id(wrapper: Any) -> str:
    return str(getattr(wrapper.element_info, "automation_id", "") or "")


def _rect(wrapper: Any) -> Optional[list[int]]:
    try:
        r = wrapper.rectangle()
        return [r.left, r.top, r.right, r.bottom]
    except Exception:
        return None


def _value(wrapper: Any) -> Optional[str]:
    for attr in ("get_value", "texts"):
        fn = getattr(wrapper, attr, None)
        if fn is None:
            continue
        try:
            out = fn()
            if isinstance(out, list):
                out = " ".join(str(x) for x in out[1:] or out)
            return str(out) if out not in (None, "") else None
        except Exception:
            continue
    return None


def _process_name(pid: int) -> str:
    try:
        from pywinauto.application import process_module

        return process_module(pid)
    except Exception:
        return ""


class PywinautoDriver:
    def __init__(self, backend: str = "uia") -> None:
        self.backend = backend
        self.desktop = Desktop(backend=backend)
        self._apps: dict[int, Application] = {}

    # ------------------------------------------------------------------ meta
    def platform_info(self) -> dict:
        import platform

        return {
            "driver": "pywinauto",
            "backend": self.backend,
            "platform": f"{platform.system()} {platform.release()}",
        }

    # --------------------------------------------------------------- windows
    def _window_kwargs(self, window: dict) -> dict:
        kw = {k: v for k, v in window.items() if k in _WINDOW_KW}
        if "pid" in window:
            kw["process"] = int(window["pid"])
        if "process" in window and "pid" not in window:
            # process name → resolve to a pid via the window list
            for info in self.list_windows():
                if info["process"].lower() == str(window["process"]).lower():
                    kw["process"] = info["pid"]
                    break
        if "handle" in kw:
            kw["handle"] = int(kw["handle"])
        return kw

    def _spec(self, window: Optional[dict]):
        if window is None:
            top = self.desktop.windows(active_only=True)
            if not top:
                raise LookupError("No active window")
            return self.desktop.window(handle=top[0].handle)
        return self.desktop.window(**self._window_kwargs(window))

    def _wrapper(self, window: Optional[dict]):
        spec = self._spec(window)
        try:
            return spec.wrapper_object()
        except Exception as exc:
            raise LookupError(f"Window not found: {window}") from exc

    def _info(self, wrapper: Any) -> dict:
        info = wrapper.element_info
        pid = int(getattr(info, "process_id", 0) or 0)
        return {
            "title": _text(wrapper),
            "class_name": getattr(info, "class_name", "") or "",
            "process": _process_name(pid),
            "pid": pid,
            "handle": int(getattr(info, "handle", 0) or 0),
            "rect": _rect(wrapper),
            "is_active": bool(getattr(wrapper, "is_active", lambda: False)()),
        }

    def list_windows(self, title_filter: Optional[str] = None) -> list[dict]:
        out = []
        for w in self.desktop.windows():
            title = _text(w)
            if not title:
                continue
            if title_filter and title_filter.lower() not in title.lower():
                continue
            out.append(self._info(w))
        return out

    def launch(
        self,
        path: str,
        args: str = "",
        cwd: Optional[str] = None,
        wait_seconds: float = 3.0,
        title_re: Optional[str] = None,
    ) -> dict:
        cmd = f'"{path}" {args}'.strip() if " " in path else f"{path} {args}".strip()
        app = Application(backend=self.backend).start(cmd, work_dir=cwd)
        self._apps[app.process] = app
        time.sleep(wait_seconds)
        spec = (
            self.desktop.window(title_re=title_re, process=app.process)
            if title_re
            else app.top_window()
        )
        spec.wait("exists visible", timeout=max(wait_seconds, 10))
        return self._info(spec.wrapper_object())

    def window_state(self, window: dict) -> dict:
        return self._info(self._wrapper(window))

    def focus(self, window: dict) -> None:
        self._wrapper(window).set_focus()

    def close(self, window: dict, force: bool = False) -> None:
        w = self._wrapper(window)
        if force:
            pid = int(getattr(w.element_info, "process_id", 0) or 0)
            app = self._apps.get(pid) or Application(backend=self.backend).connect(
                process=pid
            )
            app.kill()
            return
        w.close()

    # -------------------------------------------------------------- controls
    def _walk(
        self, wrapper: Any, depth: int, prefix: str, max_depth: int, out: list
    ) -> None:
        if depth > max_depth:
            return
        try:
            kids = wrapper.children()
        except Exception:
            kids = []
        for i, kid in enumerate(kids):
            path = f"{prefix}{i}" if not prefix else f"{prefix}/{i}"
            try:
                grand = kid.children()
            except Exception:
                grand = []
            out.append(
                Control(
                    control_type=_control_type(kid),
                    title=_text(kid),
                    auto_id=_auto_id(kid),
                    class_name=getattr(kid.element_info, "class_name", "") or "",
                    rect=_rect(kid),
                    enabled=bool(kid.is_enabled()),
                    visible=bool(kid.is_visible()),
                    depth=depth,
                    path=path,
                    value=_value(kid) if _control_type(kid) in ("Edit",) else None,
                    children=len(grand),
                    extra={"wrapper": kid},
                )
            )
            self._walk(kid, depth + 1, path, max_depth, out)

    def inventory(self, window: dict, max_depth: int = 8) -> list[Control]:
        out: list[Control] = []
        self._walk(self._wrapper(window), 0, "", max_depth, out)
        return out

    def find(self, window: dict, locator: dict) -> list[Control]:
        return find_controls(self.inventory(window, max_depth=32), locator)

    def _child(self, window: Optional[dict], locator: dict):
        """WindowSpecification for a locator; path locators resolve via inventory."""
        if "path" in locator and len(locator) == 1:
            hits = self.find(window or {}, locator)
            if not hits:
                raise LookupError(f"Control not found: {locator}")
            return hits[0].extra["wrapper"]
        kw = dict(locator)
        if "found_index" in kw:
            kw["found_index"] = int(kw["found_index"])
        spec = self._spec(window).child_window(**kw)
        try:
            return spec.wrapper_object()
        except Exception as exc:
            raise LookupError(f"Control not found: {locator}") from exc

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
        if locator is not None:
            target = self._child(window, locator)
            target.click_input(button=button, double=double)
            return {"hit": {"title": _text(target), "auto_id": _auto_id(target)}}
        if x is None or y is None:
            raise ValueError("click needs a locator or x/y coordinates")
        w = self._wrapper(window)
        w.set_focus()
        w.click_input(button=button, double=double, coords=(int(x), int(y)))
        return {"hit": None, "x": x, "y": y}

    def set_text(self, window: dict, locator: dict, value: str) -> None:
        ctl = self._child(window, locator)
        for attr in ("set_edit_text", "set_text"):
            fn = getattr(ctl, attr, None)
            if fn is not None:
                fn(value)
                return
        ctl.set_focus()
        ctl.type_keys("^a{BACKSPACE}", set_foreground=True)
        ctl.type_keys(value, with_spaces=True, set_foreground=True)

    def type_keys(
        self, window: dict, keys: str, locator: Optional[dict] = None
    ) -> None:
        target = self._child(window, locator) if locator else self._wrapper(window)
        target.set_focus()
        target.type_keys(keys, with_spaces=True, set_foreground=True, pause=0.02)

    def select(self, window: dict, locator: dict, item: str) -> None:
        self._child(window, locator).select(item)

    def menu_select(self, window: dict, path: str) -> None:
        w = self._wrapper(window)
        w.set_focus()
        w.menu_select(path.replace(">", "->").replace("-->", "->"))

    def _menu_items_win32(self, w: Any, expand: bool) -> list[dict]:
        out: list[dict] = []
        try:
            items = w.menu().items()
        except Exception:
            return out
        for item in items:
            sub: list[str] = []
            if expand:
                try:
                    sub = [s.text().replace("&", "") for s in item.sub_menu().items()]
                except Exception:
                    sub = []
            out.append({"title": item.text().replace("&", ""), "items": sub})
        return out

    def _expand_uia_top_menu(self, w: Any, top: Any) -> list[str]:
        """Open one top-level menu, read the popup MenuItems, close it again."""
        sub: list[str] = []
        try:
            top.click_input()
            time.sleep(0.4)
            for menu in self.desktop.windows():
                if _control_type(menu) != "Menu":
                    continue
                sub.extend(
                    _text(mi).replace("&", "")
                    for mi in menu.children()
                    if _control_type(mi) == "MenuItem" and _text(mi)
                )
        except Exception:
            pass
        finally:
            try:
                w.type_keys("{ESC}", set_foreground=True)
                time.sleep(0.2)
            except Exception:
                pass
        return sub

    def menu_items(self, window: dict, expand: bool = False) -> list[dict]:
        w = self._wrapper(window)
        if self.backend == "win32":
            return self._menu_items_win32(w, expand)
        out: list[dict] = []
        for bar in (c for c in w.descendants() if _control_type(c) == "MenuBar"):
            for top in bar.children():
                sub = self._expand_uia_top_menu(w, top) if expand else []
                out.append({"title": _text(top).replace("&", ""), "items": sub})
        return out

    # ----------------------------------------------------------------- waits
    def wait_window(self, window: dict, timeout: float = 10.0) -> dict:
        spec = self._spec(window)
        try:
            spec.wait("exists visible", timeout=timeout)
        except Exception as exc:
            raise TimeoutError(f"Window did not appear: {window}") from exc
        return self._info(spec.wrapper_object())

    def wait_control(
        self,
        window: dict,
        locator: dict,
        timeout: float = 10.0,
        state: str = "visible",
    ) -> Control:
        kw = {k: v for k, v in locator.items() if k != "path"}
        spec = self._spec(window).child_window(**kw)
        try:
            spec.wait(state, timeout=timeout)
        except Exception as exc:
            raise TimeoutError(f"Control did not reach '{state}': {locator}") from exc
        hits = self.find(window, locator)
        return hits[0] if hits else Control(control_type="Unknown")

    # --------------------------------------------------------------- capture
    def screenshot(self, window: Optional[dict] = None) -> bytes:
        img = (
            self._wrapper(window).capture_as_image()
            if window
            else self.desktop.window(handle=self.desktop.windows()[0].handle)
            .wrapper_object()
            .capture_as_image()
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def control_from_point(self, x: int, y: int) -> Optional[Control]:
        try:
            el = self.desktop.from_point(int(x), int(y))
        except Exception:
            return None
        if el is None:
            return None
        return Control(
            control_type=_control_type(el),
            title=_text(el),
            auto_id=_auto_id(el),
            class_name=getattr(el.element_info, "class_name", "") or "",
            rect=_rect(el),
            enabled=bool(el.is_enabled()),
            visible=bool(el.is_visible()),
            extra={"wrapper": el},
        )


__all__ = ["PywinautoDriver", "re"]
