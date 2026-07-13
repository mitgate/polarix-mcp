"""Polaris MCP package — Browser automation with Map First architecture."""

__version__ = "0.1.0"

from polaris import tools as _tools  # noqa: F401 — registers all @mcp.tool() decorators

__all__ = ["__version__", "_tools"]
