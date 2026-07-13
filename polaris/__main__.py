"""Polaris MCP — CLI entry point."""

import polaris  # noqa: F401 — triggers tool registration

from polaris.config import MCP_TRANSPORT
from polaris.server import mcp


def main() -> None:
    mcp.run(transport=MCP_TRANSPORT)


if __name__ == "__main__":
    main()
