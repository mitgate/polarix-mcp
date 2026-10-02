"""Browser helpers — context creation, page state, console listening, JS execution."""

from __future__ import annotations

import asyncio
import json
import os
import textwrap
from typing import Optional

from browser_use.browser import BrowserProfile
from browser_use.llm.anthropic.chat import ChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI
from playwright.async_api import Browser, BrowserContext, Page

from polarix.config import ANTHROPIC_API_KEY, HEADLESS, OPENAI_API_KEY, _AUTH_PATTERNS


async def _page_perf(page: Page) -> dict:
    try:
        return await page.evaluate("""() => {
            const nav = performance.getEntriesByType('navigation')[0];
            if (!nav) return {};
            const load = Math.round(nav.loadEventEnd - nav.startTime);
            const dom  = Math.round(nav.domContentLoadedEventEnd - nav.startTime);
            return load > 0 ? { page_load_ms: load, dom_ready_ms: dom } : {};
        }""")
    except Exception:
        return {}


async def _fetch_title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:
        return ""


def _browser_state(
    page: Page,
    session_file: Optional[str],
    console_errors: int,
    perf: dict,
) -> dict:
    final_url = page.url
    state: dict = {
        "final_url": final_url,
        "title": "",
        "headless": HEADLESS,
        "session_used": bool(session_file and os.path.exists(session_file or "")),
        "redirect_detected": any(p in final_url for p in _AUTH_PATTERNS),
        "console_errors": console_errors,
    }
    if perf:
        state["performance"] = perf
    return state


def _attach_console_listener(page: Page) -> list:
    errors: list = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


async def _make_context(
    p, session_file: Optional[str]
) -> tuple[Browser, BrowserContext]:
    browser = await p.chromium.launch(headless=HEADLESS)
    kwargs: dict = {"viewport": {"width": 1440, "height": 900}}
    if session_file and os.path.exists(session_file):
        kwargs["storage_state"] = session_file
    ctx = await browser.new_context(**kwargs)
    return browser, ctx


async def _exec_actions_code(page: Page, ctx: BrowserContext, code: str) -> None:
    cleaned = textwrap.dedent(code.rstrip())
    indented = "\n".join(f"    {line}" for line in cleaned.splitlines())
    fn_src = f"async def _actions(page, context, asyncio):\n{indented}\n"
    ns: dict = {"asyncio": asyncio, "json": json}
    exec(fn_src, ns)  # noqa: S102
    await ns["_actions"](page, ctx, asyncio)


def _get_llm(model: str):
    if model.startswith("claude"):
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        return ChatAnthropic(model=model, api_key=ANTHROPIC_API_KEY)
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return ChatOpenAI(model=model, api_key=OPENAI_API_KEY)


def _make_profile(
    headless: bool, storage_state: Optional[str] = None
) -> BrowserProfile:
    return BrowserProfile(
        headless=headless,
        minimum_wait_page_load_time=1.0,
        wait_for_network_idle_page_load_time=3.0,
        wait_between_actions=0.5,
        storage_state=storage_state,
    )
