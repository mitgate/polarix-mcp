"""Tool registration — importing each module triggers @mcp.tool() decorators."""

from polarix.tools import (
    auth,
    desktop,
    execution,
    knowledge,
    metrics,
    testing,
    utilities,
    verification,
    vm,
)  # noqa: F401

__all__ = [
    "auth",
    "desktop",
    "execution",
    "knowledge",
    "metrics",
    "testing",
    "utilities",
    "verification",
    "vm",
]
