"""DesktopDriver contract and factory.

Drivers are synchronous (pywinauto is); tools run them through asyncio.to_thread.
`get_driver()` reads POLARIX_DESKTOP_DRIVER at call time:

  auto      — pywinauto on Windows, DesktopUnavailable anywhere else
  pywinauto — force the real driver (Polarix itself runs inside Windows)
  remote    — proxy to a Polarix agent in a guest VM (POLARIX_DESKTOP_AGENT_URL)
  android   — a device/emulator through adb + uiautomator (POLARIX_ANDROID_SERIAL)
  appium    — Android or iOS through an Appium server (POLARIX_APPIUM_URL, POLARIX_APPIUM_CAPS)
  fake      — the simulated app from fake_driver (any OS, used by the tests)
"""

from __future__ import annotations

from polarix.config import env_setting
import platform
from typing import Optional, Protocol

from polarix.desktop.model import Control


class DesktopUnavailable(RuntimeError):
    """Raised when no desktop driver can serve the request on this machine."""


class DesktopDriver(Protocol):
    """Everything the tools need from a desktop backend.

    `window` arguments are window locators as returned by model.parse_window().
    `locator` arguments are control locators from model.parse_locator().
    """

    def platform_info(self) -> dict: ...

    def list_windows(self, title_filter: Optional[str] = None) -> list[dict]: ...

    def launch(
        self,
        path: str,
        args: str = "",
        cwd: Optional[str] = None,
        wait_seconds: float = 3.0,
        title_re: Optional[str] = None,
    ) -> dict: ...

    def window_state(self, window: dict) -> dict: ...

    def focus(self, window: dict) -> None: ...

    def close(self, window: dict, force: bool = False) -> None: ...

    def inventory(self, window: dict, max_depth: int = 8) -> list[Control]: ...

    def find(self, window: dict, locator: dict) -> list[Control]: ...

    def click(
        self,
        window: dict,
        locator: Optional[dict] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        double: bool = False,
    ) -> dict: ...

    def set_text(self, window: dict, locator: dict, value: str) -> None: ...

    def type_keys(
        self, window: dict, keys: str, locator: Optional[dict] = None
    ) -> None: ...

    def select(self, window: dict, locator: dict, item: str) -> None: ...

    def menu_select(self, window: dict, path: str) -> None: ...

    def menu_items(self, window: dict, expand: bool = False) -> list[dict]: ...

    def wait_window(self, window: dict, timeout: float = 10.0) -> dict: ...

    def wait_control(
        self,
        window: dict,
        locator: dict,
        timeout: float = 10.0,
        state: str = "visible",
    ) -> Control: ...

    def screenshot(self, window: Optional[dict] = None) -> bytes: ...

    def control_from_point(self, x: int, y: int) -> Optional[Control]: ...

    def run_command(
        self, command: str, cwd: Optional[str] = None, timeout: float = 120.0
    ) -> dict:
        """Run a shell command on the target (install/uninstall, fixtures, cleanup).

        Returns {exit_code, stdout, stderr, duration_ms}.
        """
        ...


def get_driver(
    name: Optional[str] = None,
    backend: Optional[str] = None,
    target: Optional[str] = None,
) -> DesktopDriver:
    """Resolve the driver. A named `target` (or POLARIX_TARGET) wins over the
    process-wide POLARIX_DESKTOP_DRIVER; see polarix.desktop.targets."""
    if name is None:
        from polarix.desktop.targets import default_target, driver_for

        chosen_target = target or default_target()
        if chosen_target:
            return driver_for(chosen_target)
    chosen = (name or env_setting("DESKTOP_DRIVER", "auto")).strip().lower()
    backend = backend or env_setting("DESKTOP_BACKEND", "uia")

    if chosen == "fake":
        from polarix.desktop.fake_driver import FakeDesktopDriver

        return FakeDesktopDriver.from_env()

    if chosen == "remote":
        from polarix.desktop.remote_driver import RemoteDesktopDriver

        return RemoteDesktopDriver.from_env()

    if chosen == "appium":
        import json

        from polarix.desktop.appium_driver import AppiumDriver

        caps_raw = env_setting("APPIUM_CAPS", "").strip()
        return AppiumDriver(
            server_url=env_setting("APPIUM_URL", "http://127.0.0.1:4723"),
            capabilities=json.loads(caps_raw) if caps_raw else None,
        )

    if chosen == "android":
        from polarix.desktop.android_driver import AndroidAdbDriver

        return AndroidAdbDriver(
            serial=env_setting("ANDROID_SERIAL", "") or None,
            adb=env_setting("ADB", "adb"),
        )

    if chosen in ("auto", "pywinauto"):
        if platform.system() != "Windows":
            raise DesktopUnavailable(
                "Desktop automation needs Windows (this host is "
                f"{platform.system()}). Run Polarix inside the Windows VM, or set "
                "POLARIX_DESKTOP_DRIVER=fake to use the simulated app."
            )
        try:
            from polarix.desktop.pywinauto_driver import PywinautoDriver
        except ImportError as exc:  # pragma: no cover - Windows only
            raise DesktopUnavailable(
                "pywinauto is not installed: pip install pywinauto pynput pillow"
            ) from exc
        return PywinautoDriver(backend=backend)

    raise DesktopUnavailable(
        f"Unknown POLARIX_DESKTOP_DRIVER='{chosen}' "
        "(use auto, pywinauto, remote, android, appium or fake)"
    )
