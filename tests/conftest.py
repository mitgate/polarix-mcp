"""Shared fixtures — every desktop test runs against the simulated app."""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from polarix.desktop.fake_driver import FakeDesktopDriver  # noqa: E402

EDITOR = {"title_re": ".*Editor.*"}
SAVE_AS = {"title_re": ".*Salvar como.*"}


@pytest.fixture(autouse=True, scope="session")
def _isolated_metrics_db(tmp_path_factory):
    """Every tool call records metrics; keep the suite out of the real ledger."""
    path = str(tmp_path_factory.mktemp("metrics") / "ledger.sqlite")
    previous = os.environ.get("POLARIX_METRICS_DB")
    os.environ["POLARIX_METRICS_DB"] = path
    yield path
    if previous is None:
        os.environ.pop("POLARIX_METRICS_DB", None)
    else:
        os.environ["POLARIX_METRICS_DB"] = previous


@pytest.fixture
def fake() -> FakeDesktopDriver:
    return FakeDesktopDriver()


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    """Tools resolve the driver per call; force the simulated app everywhere."""
    monkeypatch.setenv("POLARIX_DESKTOP_DRIVER", "fake")
    monkeypatch.delenv("POLARIX_DESKTOP_FAKE_APP", raising=False)
    monkeypatch.setenv("POLARIX_MACROS_DIR", str(tmp_path / "macros"))
    return tmp_path
