"""Configuration — environment variables and constants for Polarix MCP."""

from __future__ import annotations

import logging
import os
import platform
import tempfile


def env_setting(name: str, default: str = "") -> str:
    """Read POLARIX_<name>, falling back to the legacy POLARIS_<name> spelling."""
    return os.getenv(f"POLARIX_{name}", os.getenv(f"POLARIS_{name}", default))


MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8016"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
DEFAULT_MODEL = os.getenv("BROWSER_USE_MODEL", "gpt-4o-mini")
HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() not in ("false", "0", "no")
SESSIONS_DIR = env_setting("SESSIONS_DIR", "/tmp/polarix_sessions")

_AUTH_PATTERNS = {"login", "auth", "keycloak", "signin", "sso", "realms"}

# Desktop automation (Windows). The driver is resolved at call time so tests can
# switch to the simulated app with POLARIX_DESKTOP_DRIVER=fake on any OS.
IS_WINDOWS = platform.system() == "Windows"
DESKTOP_DRIVER = env_setting("DESKTOP_DRIVER", "auto")  # auto | pywinauto | fake
DESKTOP_BACKEND = env_setting("DESKTOP_BACKEND", "uia")  # uia | win32
DESKTOP_FAKE_APP = env_setting("DESKTOP_FAKE_APP", "")  # JSON fixture path
MACROS_DIR = env_setting(
    "MACROS_DIR", os.path.join(tempfile.gettempdir(), "polarix_macros")
)
REPORTS_DIR = env_setting(
    "REPORTS_DIR", os.path.join(tempfile.gettempdir(), "polarix_reports")
)
# Multimodal model for click_vision / assert vision (defaults to the planner model)
VISION_MODEL = env_setting("VISION_MODEL", DEFAULT_MODEL)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("polarix-mcp")
