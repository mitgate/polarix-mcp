"""RemoteDesktopDriver — host-side proxy to a Polarix agent running in a guest.

Implements the DesktopDriver contract by forwarding every call to the agent's
/rpc endpoint (see agent.py). Selected with POLARIX_DESKTOP_DRIVER=remote and
POLARIX_DESKTOP_AGENT_URL=http://<guest-ip>:8020 (optionally
POLARIX_AGENT_TOKEN). Standard library only.
"""

from __future__ import annotations

from polarix.config import env_setting
import json
import urllib.error
import urllib.request
from typing import Any, Optional

from polarix.desktop.agent import decode, encode
from polarix.desktop.driver import DesktopUnavailable
from polarix.desktop.model import Control


class RemoteCallError(RuntimeError):
    """The agent raised; carries the guest-side exception type."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind


_REMAP = {
    "LookupError": LookupError,
    "TimeoutError": TimeoutError,
    "ValueError": ValueError,
}


class RemoteDesktopDriver:
    def __init__(self, url: str, token: str = "", timeout: float = 60.0) -> None:
        if not url:
            raise DesktopUnavailable(
                "POLARIX_DESKTOP_AGENT_URL is not set (http://<guest-ip>:8020)"
            )
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "RemoteDesktopDriver":
        return cls(
            env_setting("DESKTOP_AGENT_URL", "").strip(),
            env_setting("AGENT_TOKEN", ""),
            float(env_setting("AGENT_TIMEOUT", "60")),
        )

    # ------------------------------------------------------------- transport
    def _request(self, method: str, path: str, payload: Optional[dict]) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method.upper())
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("X-Polarix-Token", self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise DesktopUnavailable(
                f"agent at {self.url} answered HTTP {exc.code}: {body[:200]}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DesktopUnavailable(
                f"cannot reach Polarix agent at {self.url}: {exc}"
            ) from exc

    def health(self) -> dict:
        return self._request("GET", "/health", None)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        res = self._request(
            "POST",
            "/rpc",
            {"method": method, "args": encode(list(args)), "kwargs": encode(kwargs)},
        )
        if not res.get("ok"):
            err = res.get("error") or {}
            if isinstance(err, str):
                raise RemoteCallError("AgentError", err)
            kind = str(err.get("type", "RemoteError"))
            message = str(err.get("message", ""))
            if kind in _REMAP:
                raise _REMAP[kind](message)
            raise RemoteCallError(kind, message)
        return decode(res.get("result"))

    # -------------------------------------------------------------- contract
    def platform_info(self) -> dict:
        info = self._call("platform_info")
        info["remote"] = self.url
        return info

    def list_windows(self, title_filter: Optional[str] = None) -> list[dict]:
        return self._call("list_windows", title_filter)

    def launch(
        self,
        path: str,
        args: str = "",
        cwd: Optional[str] = None,
        wait_seconds: float = 3.0,
        title_re: Optional[str] = None,
    ) -> dict:
        return self._call(
            "launch",
            path,
            args=args,
            cwd=cwd,
            wait_seconds=wait_seconds,
            title_re=title_re,
        )

    def window_state(self, window: dict) -> dict:
        return self._call("window_state", window)

    def focus(self, window: dict) -> None:
        self._call("focus", window)

    def close(self, window: dict, force: bool = False) -> None:
        self._call("close", window, force=force)

    def inventory(self, window: dict, max_depth: int = 8) -> list[Control]:
        return self._call("inventory", window, max_depth=max_depth)

    def find(self, window: dict, locator: dict) -> list[Control]:
        return self._call("find", window, locator)

    def click(
        self,
        window: dict,
        locator: Optional[dict] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        double: bool = False,
    ) -> dict:
        return self._call(
            "click", window, locator=locator, x=x, y=y, button=button, double=double
        )

    def set_text(self, window: dict, locator: dict, value: str) -> None:
        self._call("set_text", window, locator, value)

    def type_keys(
        self, window: dict, keys: str, locator: Optional[dict] = None
    ) -> None:
        self._call("type_keys", window, keys, locator)

    def select(self, window: dict, locator: dict, item: str) -> None:
        self._call("select", window, locator, item)

    def menu_select(self, window: dict, path: str) -> None:
        self._call("menu_select", window, path)

    def menu_items(self, window: dict, expand: bool = False) -> list[dict]:
        return self._call("menu_items", window, expand=expand)

    def wait_window(self, window: dict, timeout: float = 10.0) -> dict:
        return self._call("wait_window", window, timeout=timeout)

    def wait_control(
        self,
        window: dict,
        locator: dict,
        timeout: float = 10.0,
        state: str = "visible",
    ) -> Control:
        return self._call("wait_control", window, locator, timeout=timeout, state=state)

    def screenshot(self, window: Optional[dict] = None) -> bytes:
        return self._call("screenshot", window)

    def control_from_point(self, x: int, y: int) -> Optional[Control]:
        return self._call("control_from_point", x, y)

    def run_command(
        self, command: str, cwd: Optional[str] = None, timeout: float = 120.0
    ) -> dict:
        return self._call("run_command", command, cwd=cwd, timeout=timeout)
