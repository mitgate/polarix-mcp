"""Polarix MCP — CLI entry point (`polarix-mcp` console script)."""

import polarix.tools  # noqa: F401 — registers every @mcp.tool()

from polarix.config import MCP_TRANSPORT
from polarix.server import mcp


def main() -> None:
    mcp.run(transport=MCP_TRANSPORT)


if __name__ == "__main__":
    main()
