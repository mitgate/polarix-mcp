"""Utility tools — screenshot, get_page_content, get_help, doctor."""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
from typing import Optional

from playwright.async_api import async_playwright

from polarix.browser import (
    _attach_console_listener,
    _browser_state,
    _fetch_title,
    _make_context,
    _page_perf,
)
from polarix.server import _INSTRUCTIONS, mcp
from polarix.telemetry import _polarix, _start, _wrap


@mcp.tool()
async def browser_screenshot(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
    full_page: bool = False,
) -> str:
    """Capture a screenshot of a URL and return it as a base64 PNG.

    Args:
        url: URL to capture.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 3.0).
        full_page: If True, captures the full scrollable page (default: False = viewport only).

    Returns:
        JSON: { image: "data:image/png;base64,...", _polarix }
    """
    t0 = _start()

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        await page.screenshot(path=tmp, full_page=full_page)
        await browser.close()

    with open(tmp, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    os.unlink(tmp)

    return _wrap(
        {"image": f"data:image/png;base64,{data}"},
        _polarix(
            "browser_screenshot",
            t0,
            browser=bstate,
            params={"full_page": full_page, "wait_seconds": wait_seconds},
        ),
    )


@mcp.tool()
async def browser_get_page_content(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
) -> str:
    """Load a URL and return its visible text content (no HTML), up to 20,000 characters.

    Args:
        url: URL to load.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 3.0).

    Returns:
        JSON: { text, chars, truncated, _polarix }
    """
    t0 = _start()

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        raw = await page.evaluate("""
            () => {
                document.querySelectorAll('script,style,noscript').forEach(el => el.remove());
                return (document.body || document.documentElement).innerText;
            }
        """)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    cleaned = "\n".join(line for line in raw.splitlines() if line.strip())
    truncated = len(cleaned) > 20_000
    text = cleaned[:20_000]

    return _wrap(
        {"text": text, "chars": len(text), "truncated": truncated},
        _polarix("browser_get_page_content", t0, browser=bstate),
    )


@mcp.tool()
async def polarix_doctor(groups: Optional[str] = None) -> str:
    """Check this host: what works, what is missing, and the command that fixes it.

    Call it FIRST when a tool fails with "not found", "unreachable", "unavailable" or
    when deciding which layers (browser, desktop VM, android, appium) can be used here.

    Args:
        groups: Comma-separated subset of core, llm, vm, android, appium, config, server
            (default: all).

    Returns:
        JSON: { host, healthy, core_missing, counts, layers: {browser, desktop_vm, android, appium},
                checks: [{name, group, status: ok|warn|missing|optional, detail, fix?}],
                fixes: [{name, fix}], _polarix }
        `fixes` are shell commands or settings to hand to the person — do not run sudo
        commands on their behalf.
    """
    from polarix.doctor import GROUPS, diagnose

    t0 = _start()
    chosen = None
    if groups:
        chosen = [g.strip() for g in groups.split(",") if g.strip()]
        unknown = [g for g in chosen if g not in GROUPS]
        if unknown:
            return _wrap(
                {
                    "success": False,
                    "error": f"unknown groups {unknown}; use {list(GROUPS)}",
                },
                _polarix("polarix_doctor", t0),
            )
    report = await asyncio.to_thread(diagnose, chosen)
    return _wrap(
        report, _polarix("polarix_doctor", t0, params={"groups": chosen or "all"})
    )


@mcp.tool()
def browser_get_help() -> str:
    """Return full Polarix MCP documentation as a string."""
    return _INSTRUCTIONS
