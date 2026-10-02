"""Verification tools — diff_pages, capture_console, get_storage."""

from __future__ import annotations

import asyncio
from typing import Optional

from playwright.async_api import async_playwright

from polarix.browser import (
    _attach_console_listener,
    _browser_state,
    _exec_actions_code,
    _fetch_title,
    _make_context,
    _page_perf,
)
from polarix.server import mcp
from polarix.snapshot import _snapshot
from polarix.telemetry import _polarix, _start, _wrap


@mcp.tool()
async def browser_diff_pages(
    url_a: str,
    url_b: Optional[str] = None,
    actions_code: Optional[str] = None,
    session_file: Optional[str] = None,
    wait_seconds: float = 5.0,
) -> str:
    """Compare two page states and return a structured diff of UI elements.

    Two modes:
    • url_b provided   — compares two different URLs side by side.
    • actions_code provided — compares url_a before and after the actions run.

    The diff reports:
    • added_qa     — [data-qa] elements that appeared in state B
    • removed_qa   — [data-qa] elements that disappeared from state A
    • changed_texts  — elements whose visible text changed
    • changed_counts — elements whose count changed (e.g. a list grew)

    Args:
        url_a: First URL (initial state).
        url_b: Second URL for cross-page comparison (optional).
        actions_code: Async Python code to transition from state A to B (optional).
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading each state.

    Returns:
        JSON: { added_qa, removed_qa, changed_texts, changed_counts, summary, _polarix }
    """
    t0 = _start()
    warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        await page.goto(url_a, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        snap_a = await _snapshot(page)

        if url_b:
            await page.goto(url_b, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(wait_seconds)
        elif actions_code:
            await _exec_actions_code(page, ctx, actions_code)
            await asyncio.sleep(1.5)
        else:
            warnings.append(
                "Neither url_b nor actions_code provided — diff will be empty"
            )

        snap_b = await _snapshot(page)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    qa_a = {el["qa"]: el for el in snap_a["data_qa_elements"]}
    qa_b = {el["qa"]: el for el in snap_b["data_qa_elements"]}

    added = [qa_b[qa] for qa in qa_b if qa not in qa_a]
    removed = [qa_a[qa] for qa in qa_a if qa not in qa_b]

    changed_texts, changed_counts = [], []
    for qa in qa_a:
        if qa not in qa_b:
            continue
        t_a = set(qa_a[qa].get("texts", []))
        t_b = set(qa_b[qa].get("texts", []))
        if t_a != t_b:
            changed_texts.append({"qa": qa, "before": list(t_a), "after": list(t_b)})
        c_a, c_b = qa_a[qa].get("count", 0), qa_b[qa].get("count", 0)
        if c_a != c_b:
            changed_counts.append({"qa": qa, "before": c_a, "after": c_b})

    if not added and not removed and not changed_texts and not changed_counts:
        warnings.append("No differences detected between the two states")

    return _wrap(
        {
            "snapshot_a": {"url": snap_a["url"], "title": snap_a["title"]},
            "snapshot_b": {"url": snap_b["url"], "title": snap_b["title"]},
            "added_qa": added,
            "removed_qa": removed,
            "changed_texts": changed_texts,
            "changed_counts": changed_counts,
            "summary": {
                "added": len(added),
                "removed": len(removed),
                "text_changes": len(changed_texts),
                "count_changes": len(changed_counts),
            },
        },
        _polarix(
            "browser_diff_pages",
            t0,
            browser=bstate,
            params={"url_a": url_a, "url_b": url_b},
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def browser_capture_console(
    url: str,
    session_file: Optional[str] = None,
    actions_code: Optional[str] = None,
    wait_seconds: float = 5.0,
    levels: str = "log,warn,error,info",
) -> str:
    """Capture all browser console output during page load and optional actions.

    Collects console.log / warn / error / info messages and uncaught page errors.
    When something fails silently in the UI, the root cause is almost always visible
    here first.

    Args:
        url: Starting URL.
        session_file: Session file for authenticated sites.
        actions_code: Optional async Python code to execute after load (receives `page`).
        wait_seconds: Seconds to wait after page load (default: 5.0).
        levels: Comma-separated console levels to capture (default: "log,warn,error,info").

    Returns:
        JSON: { errors, warnings_console, info, all_messages, messages_captured, _polarix }
    """
    t0 = _start()
    target_levels = {lv.strip() for lv in levels.split(",")}
    messages: list[dict] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()

        def on_console(msg):
            if msg.type not in target_levels:
                return
            loc = msg.location or {}
            messages.append(
                {
                    "type": msg.type,
                    "text": msg.text,
                    "location": f"{loc.get('url', '')}:{loc.get('lineNumber', '')}",
                }
            )

        def on_page_error(error):
            messages.append({"type": "pageerror", "text": str(error), "location": ""})

        page.on("console", on_console)
        page.on("pageerror", on_page_error)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        if actions_code:
            await _exec_actions_code(page, ctx, actions_code)
            await asyncio.sleep(1.0)

        perf = await _page_perf(page)
        error_count = sum(1 for m in messages if m["type"] in ("error", "pageerror"))
        bstate = _browser_state(page, session_file, error_count, perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    return _wrap(
        {
            "url": url,
            "messages_captured": len(messages),
            "errors": [m for m in messages if m["type"] in ("error", "pageerror")],
            "warnings_console": [m for m in messages if m["type"] == "warn"],
            "info": [m for m in messages if m["type"] in ("log", "info")],
            "all_messages": messages,
        },
        _polarix(
            "browser_capture_console", t0, browser=bstate, params={"levels": levels}
        ),
    )


@mcp.tool()
async def browser_get_storage(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
) -> str:
    """Read localStorage, sessionStorage, and cookies for a URL.

    Useful for inspecting auth tokens, persisted SPA state, feature flags,
    and any data the application stores in the browser between sessions.

    Args:
        url: Page to inspect.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 3.0).

    Returns:
        JSON: { local_storage, session_storage, cookies, *_keys, cookies_count, _polarix }
    """
    t0 = _start()

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        storage = await page.evaluate("""
            () => ({
                localStorage: Object.fromEntries(
                    Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])
                ),
                sessionStorage: Object.fromEntries(
                    Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)])
                ),
            })
        """)
        cookies = await ctx.cookies()
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    ls = storage.get("localStorage", {})
    ss = storage.get("sessionStorage", {})
    return _wrap(
        {
            "url": url,
            "local_storage": ls,
            "session_storage": ss,
            "cookies": cookies,
            "local_storage_keys": list(ls.keys()),
            "session_storage_keys": list(ss.keys()),
            "cookies_count": len(cookies),
        },
        _polarix("browser_get_storage", t0, browser=bstate),
    )
