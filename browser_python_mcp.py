"""Polarix MCP — entry point.

Imports polarix.tools (which registers all @mcp.tool() decorators) and starts
the MCP server. The polarix package itself stays import-light so the guest
agent can reuse polarix.desktop without Playwright.
"""

import polarix.tools  # noqa: F401 — triggers tool registration

from polarix.config import MCP_TRANSPORT
from polarix.server import mcp

if __name__ == "__main__":
    mcp.run(transport=MCP_TRANSPORT)
