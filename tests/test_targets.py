"""Named targets — choosing which machine / OS runs a sequence."""

from __future__ import annotations

import asyncio
import json

import pytest

from polarix.desktop import targets
from polarix.desktop.driver import DesktopUnavailable, get_driver
from polarix.desktop.fake_driver import FakeDesktopDriver
from polarix.desktop.remote_driver import RemoteDesktopDriver
from polarix.desktop.scenarios import expand_targets
from polarix.tools import desktop as dt
from polarix.tools import testing as tt
from tests.conftest import EDITOR

INLINE = json.dumps(
    {
        "win11": {
            "driver": "remote",
            "agent_url": "http://192.168.122.15:8020",
            "token": "s",
            "os": "windows",
            "vm": {"backend": "libvirt", "name": "win11-lab", "snapshot": "clean"},
        },
        "sim2": {"driver": "fake", "description": "second simulator"},
    }
)


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setenv("POLARIX_TARGETS_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("POLARIX_TARGETS", INLINE)
    monkeypatch.delenv("POLARIX_TARGET", raising=False)
    monkeypatch.setenv(
        "POLARIX_DESKTOP_DRIVER", "auto"
    )  # process default would fail on Linux
    monkeypatch.setattr("polarix.desktop.driver.platform.system", lambda: "Linux")


def test_builtins_plus_file_plus_env(registry, tmp_path, monkeypatch):
    names = set(targets.load_targets())
    assert {"fake", "local", "win11", "sim2"} <= names
    f = tmp_path / "t.json"
    f.write_text(
        json.dumps(
            {
                "ubuntu": {
                    "driver": "remote",
                    "agent_url": "http://10.0.0.5:8020",
                    "os": "linux",
                }
            }
        )
    )
    monkeypatch.setenv("POLARIX_TARGETS_FILE", str(f))
    assert targets.load_targets()["ubuntu"]["os"] == "linux"
    desc = {d["name"]: d for d in targets.describe()}
    assert (
        desc["win11"]["token_set"] is True
        and desc["win11"]["vm"]["name"] == "win11-lab"
    )
    assert desc["win11"]["os"] == "windows" and "token" not in desc["win11"]


def test_validation_errors(registry, monkeypatch):
    monkeypatch.setenv("POLARIX_TARGETS", json.dumps({"bad": {"driver": "remote"}}))
    with pytest.raises(ValueError, match="agent_url"):
        targets.load_targets()
    monkeypatch.setenv("POLARIX_TARGETS", json.dumps({"bad": {"driver": "xen"}}))
    with pytest.raises(ValueError, match="driver must be"):
        targets.load_targets()


def test_get_driver_by_target_overrides_process_default(registry):
    assert isinstance(get_driver(target="fake"), FakeDesktopDriver)
    assert isinstance(get_driver(target="sim2"), FakeDesktopDriver)
    remote = get_driver(target="win11")
    assert (
        isinstance(remote, RemoteDesktopDriver)
        and remote.url == "http://192.168.122.15:8020"
    )
    with pytest.raises(DesktopUnavailable, match="unknown target 'mars'"):
        get_driver(target="mars")
    with pytest.raises(DesktopUnavailable, match="Windows"):
        get_driver()  # no target, process default is auto on Linux


def test_default_target_env(registry, monkeypatch):
    monkeypatch.setenv("POLARIX_TARGET", "sim2")
    assert isinstance(get_driver(), FakeDesktopDriver)
    assert (
        targets.describe()[[d["name"] for d in targets.describe()].index("sim2")][
            "default"
        ]
        is True
    )


def call(coro):
    return json.loads(asyncio.run(coro))


def test_tools_accept_target(registry):
    out = call(dt.desktop_list_windows(target="sim2"))
    assert out["window_count"] == 1
    failed = call(dt.desktop_list_windows())
    assert failed["success"] is False
    listed = call(dt.desktop_targets())
    assert {t["name"] for t in listed["targets"]} >= {"fake", "local", "win11", "sim2"}


def test_scenario_target_and_suite_matrix(registry, monkeypatch, tmp_path):
    monkeypatch.setenv("POLARIX_REPORTS_DIR", str(tmp_path / "reports"))
    sc = {
        "name": "abre",
        "target": "sim2",
        "window": EDITOR,
        "steps": [{"action": "assert", "kind": "window_exists"}],
    }
    out = call(tt.desktop_run_scenario(json.dumps(sc)))
    assert out["status"] == "passed" and out["target"] == "sim2"
    matrix = {
        "name": "abre",
        "targets": ["fake", "sim2"],
        "window": EDITOR,
        "steps": [{"action": "assert", "kind": "window_exists"}],
    }
    assert [s["name"] for s in expand_targets([matrix])] == ["abre@fake", "abre@sim2"]
    suite = call(
        tt.desktop_run_suite(json.dumps([matrix]), name="matrix", include_results=True)
    )
    assert suite["kpis"]["scenarios"] == 2 and suite["kpis"]["pass_rate"] == 1.0
    assert sorted(r["target"] for r in suite["results"]) == ["fake", "sim2"]
