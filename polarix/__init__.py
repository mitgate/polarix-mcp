"""Polarix MCP package — browser and desktop automation with Map First architecture.

Importing this package is side-effect free so the guest-side agent
(`python -m polarix.desktop.agent`) can run with only pywinauto installed.
The MCP entry point (browser_python_mcp.py) imports `polarix.tools`, which
registers every @mcp.tool() and pulls in Playwright / browser-use.
"""

__version__ = "1.7.0"
