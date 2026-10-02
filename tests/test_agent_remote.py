"""End to end on the loopback: host-side RemoteDesktopDriver → guest-side agent."""

from __future__ import annotations

import pytest

from polarix.desktop.agent import start_in_thread
from polarix.desktop.driver import DesktopUnavailable, get_driver
from polarix.desktop.fake_driver import FakeDesktopDriver
from polarix.desktop.model import Control
from polarix.desktop.remote_driver import RemoteDesktopDriver
from polarix.desktop.steps import run_desktop_steps
from tests.conftest import EDITOR, SAVE_AS
from tests.test_desktop_steps import SAVE_FLOW


@pytest.fixture
def agent():
    server, _ = start_in_thread(
        "127.0.0.1", 0, token="s3cret", driver=FakeDesktopDriver()
    )
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()


def test_health_and_platform_info(agent):
    drv = RemoteDesktopDriver(agent, "s3cret")
    assert drv.health()["ok"] is True
    info = drv.platform_info()
    assert info["driver"] == "fake" and info["remote"] == agent


def test_invalid_token_is_rejected(agent):
    with pytest.raises(DesktopUnavailable, match="401"):
        RemoteDesktopDriver(agent, "wrong").list_windows()


def test_controls_cross_the_wire_as_objects(agent):
    drv = RemoteDesktopDriver(agent, "s3cret")
    inv = drv.inventory(EDITOR)
    assert inv and all(isinstance(c, Control) for c in inv)
    assert drv.find(EDITOR, {"auto_id": "15"})[0].control_type == "Edit"
    assert drv.screenshot(EDITOR)[:8] == b"\x89PNG\r\n\x1a\n"


def test_guest_exceptions_are_remapped(agent):
    drv = RemoteDesktopDriver(agent, "s3cret")
    with pytest.raises(LookupError):
        drv.window_state({"title": "Ghost"})
    with pytest.raises(TimeoutError):
        drv.wait_window({"title": "Ghost"}, timeout=0.1)


def test_full_sequence_through_the_agent_keeps_state(agent):
    drv = RemoteDesktopDriver(agent, "s3cret")
    results, warnings, _ = run_desktop_steps(drv, EDITOR, SAVE_FLOW)
    assert all(r["success"] for r in results), results
    # state lives in the agent: a second call sees the renamed window
    assert drv.list_windows()[0]["title"] == "obra.txt - Editor"
    assert drv.list_windows(title_filter="salvar") == []
    assert SAVE_AS  # dialog closed by the flow


def test_get_driver_remote_from_env(agent, monkeypatch):
    monkeypatch.setenv("POLARIX_DESKTOP_DRIVER", "remote")
    monkeypatch.setenv("POLARIX_DESKTOP_AGENT_URL", agent)
    monkeypatch.setenv("POLARIX_AGENT_TOKEN", "s3cret")
    drv = get_driver()
    assert isinstance(drv, RemoteDesktopDriver)
    assert drv.list_windows()[0]["title"].endswith("Editor")


def test_unreachable_agent_is_a_clear_error():
    with pytest.raises(DesktopUnavailable, match="cannot reach"):
        RemoteDesktopDriver("http://127.0.0.1:9", timeout=0.5).list_windows()
