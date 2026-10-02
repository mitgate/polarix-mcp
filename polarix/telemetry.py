"""Telemetry helpers — timing, _polarix block construction, and JSON wrapping."""

from __future__ import annotations

import json
import time as _time
from typing import Optional


def _start() -> float:
    return _time.monotonic()


def _elapsed_ms(t0: float) -> int:
    return round((_time.monotonic() - t0) * 1000)


def _polarix(
    tool: str,
    t0: float,
    browser: Optional[dict] = None,
    params: Optional[dict] = None,
    warnings: Optional[list] = None,
    desktop: Optional[dict] = None,
) -> dict:
    block: dict = {"tool": tool, "duration_ms": _elapsed_ms(t0)}
    if browser:
        block["browser"] = browser
    if desktop:
        block["desktop"] = desktop
    if params:
        block["effective_params"] = params
    if warnings:
        block["warnings"] = warnings
    return block


def _wrap(data: dict, polarix_block: dict) -> str:
    """Attach the telemetry block, feed the metrics ledger, serialise."""
    try:
        from polarix.metrics.ledger import safe_record

        safe_record(data, polarix_block)
    except Exception:  # pragma: no cover - metrics must never break a tool
        pass
    data["_polarix"] = polarix_block
    return json.dumps(data, ensure_ascii=False, indent=2)
