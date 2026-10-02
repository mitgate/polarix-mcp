"""Polarix desktop agent — runs INSIDE the guest (VM / emulator / another box).

It wraps a local DesktopDriver (pywinauto on Windows, the simulated app for
tests) behind a tiny JSON-over-HTTP endpoint so that Polarix running on the
host can drive the guest with RemoteDesktopDriver. Standard library only, so
the guest needs nothing beyond Python + pywinauto (+ pynput for recording).

    python -m polarix.desktop.agent --host 0.0.0.0 --port 8020 --token SECRET

Protocol
  GET  /health                 → {ok, driver, platform}
  POST /rpc  {"method": "...", "args": [...], "kwargs": {...}}
                               → {"ok": true, "result": ...}
                               → {"ok": false, "error": {"type", "message"}}
Control objects travel as dicts (Control.to_dict / Control(**dict)); bytes
(screenshots) travel base64 in {"__bytes__": "..."}.
"""

from __future__ import annotations

from polarix.config import env_setting
import argparse
import base64
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from polarix.desktop.driver import DesktopDriver, get_driver
from polarix.desktop.model import Control

logger = logging.getLogger("polarix-agent")

ALLOWED_METHODS = {
    "platform_info",
    "list_windows",
    "launch",
    "window_state",
    "focus",
    "close",
    "inventory",
    "find",
    "click",
    "set_text",
    "type_keys",
    "select",
    "menu_select",
    "menu_items",
    "wait_window",
    "wait_control",
    "screenshot",
    "control_from_point",
}


def encode(value: Any) -> Any:
    if isinstance(value, Control):
        return {"__control__": value.to_dict()}
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, tuple):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    return value


def decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "__control__" in value and len(value) == 1:
            data = dict(value["__control__"])
            data.setdefault("control_type", "Unknown")
            return Control(**data)
        if "__bytes__" in value and len(value) == 1:
            return base64.b64decode(value["__bytes__"])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


class AgentState:
    def __init__(self, driver: DesktopDriver, token: str = "") -> None:
        self.driver = driver
        self.token = token
        self.lock = threading.Lock()  # pywinauto is not thread-safe


def handle_rpc(state: AgentState, body: bytes) -> tuple[int, dict]:
    """Dispatch one /rpc request body. Returns (http_status, payload)."""
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        return 400, {"ok": False, "error": f"invalid JSON: {exc}"}
    method = str(req.get("method", ""))
    if method not in ALLOWED_METHODS:
        return 400, {"ok": False, "error": f"method not allowed: {method}"}
    try:
        args = decode(req.get("args", []))
        kwargs = decode(req.get("kwargs", {}))
        with state.lock:
            result = getattr(state.driver, method)(*args, **kwargs)
        return 200, {"ok": True, "result": encode(result)}
    except Exception as exc:
        return 200, {
            "ok": False,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


def handle_health(state: AgentState) -> tuple[int, dict]:
    try:
        return 200, {"ok": True, **state.driver.platform_info()}
    except Exception as exc:
        return 500, {"ok": False, "error": str(exc)}


def _make_handler(state: AgentState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PolarixAgent/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter default log
            logger.debug(fmt, *args)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not state.token:
                return True
            return self.headers.get("X-Polarix-Token", "") == state.token

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/health":
                self._send(404, {"ok": False, "error": "not found"})
                return
            self._send(*handle_health(state))

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/rpc":
                self._send(404, {"ok": False, "error": "not found"})
                return
            if not self._authorized():
                self._send(401, {"ok": False, "error": "invalid token"})
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            self._send(*handle_rpc(state, self.rfile.read(length)))

    return Handler


def serve(
    host: str = "127.0.0.1",
    port: int = 8020,
    token: str = "",
    driver: Optional[DesktopDriver] = None,
) -> ThreadingHTTPServer:
    """Create a server (not started). Call .serve_forever() or use start_in_thread."""
    state = AgentState(driver or get_driver(), token)
    return ThreadingHTTPServer((host, port), _make_handler(state))


def start_in_thread(
    host: str = "127.0.0.1",
    port: int = 0,
    token: str = "",
    driver: Optional[DesktopDriver] = None,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start the agent on a background thread (port 0 = pick a free port)."""
    server = serve(host, port, token, driver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def main() -> None:
    parser = argparse.ArgumentParser(description="Polarix desktop agent (guest side)")
    parser.add_argument("--host", default=env_setting("AGENT_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(env_setting("AGENT_PORT", "8020"))
    )
    parser.add_argument("--token", default=env_setting("AGENT_TOKEN", ""))
    parser.add_argument(
        "--driver",
        default=env_setting("DESKTOP_DRIVER", "auto"),
        help="auto | pywinauto | fake",
    )
    parser.add_argument("--backend", default=env_setting("DESKTOP_BACKEND", "uia"))
    ns = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    driver = get_driver(ns.driver, ns.backend)
    server = serve(ns.host, ns.port, ns.token, driver)
    info = driver.platform_info()
    logger.info("Polarix agent on http://%s:%s (%s)", ns.host, ns.port, info)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
