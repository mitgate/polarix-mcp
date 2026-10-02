"""Execution tools — run_playwright, execute_sequence, run_task, inject_js, auto_sequence."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import textwrap
from typing import Optional

from playwright.async_api import async_playwright

from polarix.browser import (
    _attach_console_listener,
    _browser_state,
    _fetch_title,
    _get_llm,
    _make_context,
    _make_profile,
    _page_perf,
)
from polarix.config import DEFAULT_MODEL, HEADLESS
from polarix.llm import generate_steps
from polarix.server import mcp
from polarix.snapshot import _selector_index, _snapshot
from polarix.telemetry import _elapsed_ms, _polarix, _start, _wrap


async def _run_steps(
    page, ctx, steps: list[dict], stop_on_error: bool = True
) -> tuple[list[dict], list[str]]:
    """Execute steps on an already-open page. Returns (results, warnings)."""
    results: list[dict] = []
    warnings: list[str] = []

    for i, step in enumerate(steps):
        action = step.get("action", "")
        st = _start()
        sr: dict = {
            "step": i + 1,
            "action": action,
            "success": False,
            "duration_ms": 0,
            "selector_match_count": None,
            "result": None,
            "error": None,
        }

        try:
            if action == "goto":
                await page.goto(
                    step["url"], wait_until="domcontentloaded", timeout=30_000
                )
                sr["result"] = {"url": page.url}

            elif action in ("click", "fill", "hover"):
                sel = step["selector"]
                count = await page.locator(sel).count()
                sr["selector_match_count"] = count
                if count == 0:
                    warnings.append(
                        f"Step {i+1} ({action}): selector '{sel}' matched 0 elements"
                    )
                    raise ValueError(f"Selector '{sel}' matched 0 elements")
                if count > 1:
                    warnings.append(
                        f"Step {i+1} ({action}): selector '{sel}' matched {count} elements, used first"
                    )

                if action == "click":
                    el = page.locator(sel)
                    if step.get("index") is not None:
                        el = el.nth(step["index"])
                    await el.click(
                        force=step.get("force", False),
                        timeout=step.get("timeout", 10_000),
                    )
                    await asyncio.sleep(step.get("wait_after", 1.0))
                    sr["result"] = {"url": page.url}
                elif action == "fill":
                    el = page.locator(sel)
                    if step.get("index") is not None:
                        el = el.nth(step["index"])
                    await el.wait_for(
                        state="visible", timeout=step.get("timeout", 10_000)
                    )
                    await el.clear()
                    await el.fill(step["value"])
                elif action == "hover":
                    el = page.locator(sel)
                    if step.get("index") is not None:
                        el = el.nth(step["index"])
                    await el.hover()

            elif action == "select":
                await page.locator(step["selector"]).select_option(step["value"])

            elif action == "press":
                await page.keyboard.press(step["key"])

            elif action == "scroll":
                x, y = step.get("x", 0), step.get("y", 500)
                await page.evaluate(f"window.scrollBy({x}, {y})")

            elif action == "wait_for":
                timeout = step.get("timeout", 10_000)
                if sel := step.get("selector"):
                    count = await page.locator(sel).count()
                    sr["selector_match_count"] = count
                    await page.locator(sel).wait_for(
                        state=step.get("state", "visible"), timeout=timeout
                    )
                elif txt := step.get("text"):
                    await page.get_by_text(txt).wait_for(
                        state="visible", timeout=timeout
                    )
                elif secs := step.get("seconds"):
                    await asyncio.sleep(secs)

            elif action == "snapshot":
                sr["result"] = await _snapshot(page)

            elif action == "screenshot":
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    tmp = f.name
                await page.screenshot(path=tmp, full_page=step.get("full_page", False))
                with open(tmp, "rb") as f:
                    sr["result"] = (
                        f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
                    )
                os.unlink(tmp)

            elif action == "evaluate":
                sr["result"] = await page.evaluate(step["expression"])

            else:
                raise ValueError(f"Unknown action: '{action}'")

            sr["success"] = True

        except Exception as e:
            sr["error"] = str(e)
            sr["duration_ms"] = _elapsed_ms(st)
            results.append(sr)
            if stop_on_error:
                break
            continue

        sr["duration_ms"] = _elapsed_ms(st)
        results.append(sr)

    return results, warnings


@mcp.tool()
async def browser_execute_sequence(  # noqa: C901
    steps_json: str,
    session_file: Optional[str] = None,
    start_url: Optional[str] = None,
    stop_on_error: bool = True,
) -> str:
    """Execute a typed JSON action sequence in the browser.

    Safer than browser_run_playwright for predictable linear flows: each action
    has a declared type, built-in error handling, and a per-step result record.

    Available actions and their required fields:
      { "action": "goto",       "url": "https://..." }
      { "action": "click",      "selector": "[data-qa=X]", "force": false,
                                "index": 0, "wait_after": 1.0, "timeout": 10000 }
      { "action": "fill",       "selector": "#email", "value": "text" }
      { "action": "select",     "selector": "select", "value": "option" }
      { "action": "press",      "key": "Enter" }
      { "action": "hover",      "selector": "[data-qa=X]" }
      { "action": "scroll",     "x": 0, "y": 500 }
      { "action": "wait_for",   "selector": "[data-qa=X]", "state": "visible" }
      { "action": "wait_for",   "text": "Success", "timeout": 5000 }
      { "action": "wait_for",   "seconds": 2 }
      { "action": "snapshot" }
      { "action": "screenshot", "full_page": false }
      { "action": "evaluate",   "expression": "document.title" }

    Each step result includes duration_ms and selector_match_count (for click/fill/
    hover/wait_for steps) so the AI can reason about performance and selector quality.

    Args:
        steps_json: JSON array of step objects.
        session_file: Session file for authenticated sites.
        start_url: URL to navigate to before running the sequence.
        stop_on_error: Stop on the first failed step (default: True).

    Returns:
        JSON: { steps_total, steps_succeeded, final_url, results, _polarix }
    """
    t0 = _start()
    try:
        steps: list[dict] = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return _wrap(
            {"success": False, "error": f"Invalid JSON in steps_json: {e}"},
            _polarix("browser_execute_sequence", t0),
        )

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        if start_url:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)

        results, seq_warnings = await _run_steps(page, ctx, steps, stop_on_error)

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        final_url = page.url
        await browser.close()

    return _wrap(
        {
            "steps_total": len(steps),
            "steps_executed": len(results),
            "steps_succeeded": sum(1 for r in results if r["success"]),
            "final_url": final_url,
            "results": results,
        },
        _polarix(
            "browser_execute_sequence",
            t0,
            browser=bstate,
            params={
                "steps_total": len(steps),
                "stop_on_error": stop_on_error,
                "session_used": bool(
                    session_file and os.path.exists(session_file or "")
                ),
            },
            warnings=seq_warnings or None,
        ),
    )


@mcp.tool()
async def browser_run_playwright(
    code: str,
    session_file: Optional[str] = None,
    start_url: Optional[str] = None,
    timeout_seconds: int = 60,
) -> str:
    """Execute Python Playwright code directly — no LLM in the automation loop.

    The code body receives `page` (Playwright Page), `context` (BrowserContext),
    and `asyncio`. Write it as the body of an async function. Use `return` to
    return structured data back to the caller.

    Always use selectors discovered via browser_map_site or browser_explore_page
    rather than guessing.

    Example:
        count = await page.locator('[data-qa="ItemCard"]').count()
        title = await page.title()
        return {"title": title, "item_count": count}

    Args:
        code: Async function body with access to `page`, `context`, `asyncio`.
        session_file: Session file for authenticated sites.
        start_url: URL to navigate to before running the code.
        timeout_seconds: Total execution timeout in seconds (default: 60).

    Returns:
        JSON: { success, result, final_url, error, _polarix }
    """
    t0 = _start()
    cleaned = textwrap.dedent(code.rstrip())
    indented = "\n".join(f"    {line}" for line in cleaned.splitlines())
    fn_src = f"async def _scenario(page, context, asyncio):\n{indented}\n"

    ns: dict = {"asyncio": asyncio, "json": json}
    try:
        exec(fn_src, ns)  # noqa: S102
    except SyntaxError as e:
        return _wrap(
            {"success": False, "result": None, "error": f"SyntaxError: {e}"},
            _polarix("browser_run_playwright", t0),
        )

    bstate: dict = {}
    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        try:
            if start_url:
                await page.goto(
                    start_url, wait_until="domcontentloaded", timeout=30_000
                )
                await asyncio.sleep(3)

            result = await asyncio.wait_for(
                ns["_scenario"](page, ctx, asyncio),
                timeout=timeout_seconds,
            )
            perf = await _page_perf(page)
            bstate = _browser_state(page, session_file, len(errors), perf)
            bstate["title"] = await _fetch_title(page)
            output = {
                "success": True,
                "result": result,
                "final_url": page.url,
                "error": None,
            }

        except asyncio.TimeoutError:
            perf = await _page_perf(page)
            bstate = _browser_state(page, session_file, len(errors), perf)
            bstate["title"] = await _fetch_title(page)
            output = {
                "success": False,
                "result": None,
                "final_url": page.url,
                "error": f"Execution timed out after {timeout_seconds}s",
            }
        except Exception as e:
            perf = await _page_perf(page)
            bstate = _browser_state(page, session_file, len(errors), perf)
            bstate["title"] = await _fetch_title(page)
            output = {
                "success": False,
                "result": None,
                "final_url": page.url,
                "error": str(e),
            }
        finally:
            await browser.close()

    return _wrap(
        output,
        _polarix(
            "browser_run_playwright",
            t0,
            browser=bstate,
            params={
                "timeout_seconds": timeout_seconds,
                "start_url": start_url,
                "session_used": bool(
                    session_file and os.path.exists(session_file or "")
                ),
            },
        ),
    )


@mcp.tool()
async def browser_run_task(
    task: str,
    start_url: Optional[str] = None,
    model: Optional[str] = None,
    max_steps: int = 30,
    sensitive_data: Optional[dict] = None,
    session_file: Optional[str] = None,
) -> str:
    """Execute a natural-language automation task using an LLM agent (browser-use).

    This is the unstructured fallback — use it for initial exploration when selectors
    are not yet known. For anything requiring precision or repeatability, prefer
    browser_run_playwright or browser_execute_sequence with selectors from browser_map_site.

    Args:
        task: Natural language description of what to do.
              Use {placeholder} syntax to reference keys in sensitive_data.
        start_url: URL to navigate to before executing the task.
        model: LLM model ID (default: gpt-4o-mini). Prefix with "claude-" for Anthropic.
        max_steps: Maximum agent action steps (default: 30).
        sensitive_data: Dict of sensitive values not exposed to the LLM internally.
        session_file: Session file for authenticated sites.

    Returns:
        JSON: { result, model_used, _polarix }
    """
    from browser_use import Agent

    t0 = _start()
    use_model = model or DEFAULT_MODEL
    llm = _get_llm(use_model)
    full_task = f"Navigate to {start_url}. Then: {task}" if start_url else task
    profile = _make_profile(HEADLESS, storage_state=session_file)

    agent = Agent(
        task=full_task,
        llm=llm,
        browser_profile=profile,
        max_actions_per_step=5,
        sensitive_data=sensitive_data or {},
    )
    run_result = await agent.run(max_steps=max_steps)
    final = run_result.final_result()
    if not final:
        history = run_result.action_results()
        final = (
            str(history[-1].extracted_content or history[-1].error or "")
            if history
            else ""
        )

    return _wrap(
        {"result": str(final), "model_used": use_model},
        _polarix(
            "browser_run_task",
            t0,
            params={
                "model": use_model,
                "max_steps": max_steps,
                "session_used": bool(
                    session_file and os.path.exists(session_file or "")
                ),
            },
        ),
    )


@mcp.tool()
async def browser_inject_js(
    code: str,
    url: str,
    session_file: Optional[str] = None,
    persistent: bool = False,
    wait_seconds: float = 3.0,
) -> str:
    """Inject and execute JavaScript in the live page context.

    Evaluates arbitrary JS and returns the result. With persistent=True, the
    script is installed via addInitScript so it re-runs on every navigation
    within this session — useful for interceptors, helpers, or globals that
    must survive SPA route changes.

    The code must return a JSON-serializable value (object, array, string,
    number, boolean, or null). Returning a DOM element or a Promise that
    resolves to one will cause a serialization error.

    Use cases:
    • Extract data structures buried in JS state (window.__store__, etc.)
    • Reconstruct auth tokens split across multiple cookies
    • Override window.fetch or XMLHttpRequest to intercept calls
    • Inject event listeners or UI helpers before an action
    • Read IndexedDB or other browser APIs not exposed in the DOM

    Args:
        code: JavaScript expression or IIFE to evaluate.
        url: Page URL to load before executing the code.
        session_file: Session file for authenticated sites.
        persistent: Re-execute on every page navigation via addInitScript.
        wait_seconds: Seconds to wait after load before executing (default: 3.0).

    Returns:
        JSON: { result, persistent, _polarix }
    """
    t0 = _start()
    warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        if persistent:
            await ctx.add_init_script(code)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        try:
            result = await page.evaluate(code)
        except Exception as e:
            result = None
            warnings.append(f"JS evaluation error: {e}")

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    return _wrap(
        {"result": result, "persistent": persistent},
        _polarix(
            "browser_inject_js",
            t0,
            browser=bstate,
            params={
                "persistent": persistent,
                "wait_seconds": wait_seconds,
                "session_used": bool(
                    session_file and os.path.exists(session_file or "")
                ),
            },
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def browser_auto_sequence(  # noqa: C901
    goal: str,
    url: str,
    session_file: Optional[str] = None,
    model: Optional[str] = None,
    explore: bool = True,
    dry_run: bool = False,
) -> str:
    """Dado um objetivo em linguagem natural, mapeia a página, explora triggers
    escondidos, gera automaticamente uma sequência de steps via LLM usando o
    mapa como contexto, e executa.

    Diferente de browser_run_task (LLM navega cegamente): aqui o LLM vê o mapa
    completo ANTES de planejar — gera a sequência toda de uma vez e executa de
    forma determinística.

    dry_run=True retorna apenas os steps gerados sem executar.

    Args:
        goal: Objetivo em linguagem natural (e.g. "fill the search form and submit").
        url: Starting URL to map and automate.
        session_file: Session file for authenticated sites.
        model: LLM model ID (default: DEFAULT_MODEL). Prefix with "claude-" for Anthropic.
        explore: Click interactive triggers to discover hidden elements (default: True).
        dry_run: If True, return only the generated steps without executing (default: False).

    Returns:
        JSON: { goal, generated_steps, execution: { steps_succeeded, final_url, results }, _polarix }
        With dry_run=True: { goal, generated_steps, dry_run: true, _polarix }
    """
    t0 = _start()
    use_model = model or DEFAULT_MODEL
    warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        # Step 1: navigate and snapshot
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)

        snap = await _snapshot(page)
        idx = _selector_index([snap])

        # Step 2: explore triggers to discover hidden elements
        revealed: list[dict] = []
        if explore:
            origin_url = page.url
            for trigger in snap.get("interactive_triggers", [])[:8]:
                qa = trigger["qa"]
                try:
                    el = page.locator(f'[data-qa="{qa}"]').first
                    if not await el.is_visible(timeout=2_000):
                        continue
                    await el.click(force=True)
                    await asyncio.sleep(1.5)
                    if page.url != origin_url:
                        await page.goto(
                            origin_url, wait_until="domcontentloaded", timeout=20_000
                        )
                        await asyncio.sleep(3)
                        continue
                    after = await _snapshot(page)
                    static_qa = {el["qa"] for el in snap["data_qa_elements"]}
                    new_qa = {e["qa"] for e in after["data_qa_elements"]} - static_qa
                    if new_qa:
                        revealed.append(
                            {
                                "trigger_qa": qa,
                                "new_elements": [
                                    e
                                    for e in after["data_qa_elements"]
                                    if e["qa"] in new_qa
                                ],
                            }
                        )
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.8)
                except Exception as exc:
                    warnings.append(f"Trigger '{qa}' explore failed: {exc}")
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass

        # Step 3: build map context for LLM
        map_context = {
            "title": snap.get("title", ""),
            "selector_index": idx,
            "forms": snap.get("forms", []),
            "buttons_no_qa": snap.get("buttons_no_qa", []),
            "revealed_elements": revealed,
        }

        # Step 4: call LLM to generate steps
        steps = await generate_steps(goal, map_context, use_model)
        if not steps:
            warnings.append("LLM returned no steps — check model and API key")

        if dry_run:
            perf = await _page_perf(page)
            bstate = _browser_state(page, session_file, len(errors), perf)
            bstate["title"] = await _fetch_title(page)
            await browser.close()
            return _wrap(
                {"goal": goal, "generated_steps": steps, "dry_run": True},
                _polarix(
                    "browser_auto_sequence",
                    t0,
                    browser=bstate,
                    params={
                        "goal": goal,
                        "url": url,
                        "model": use_model,
                        "explore": explore,
                        "dry_run": True,
                    },
                    warnings=warnings or None,
                ),
            )

        # Step 5: navigate back and execute steps
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)

        results, step_warnings = await _run_steps(page, ctx, steps, stop_on_error=False)
        warnings.extend(step_warnings)

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        final_url = page.url
        await browser.close()

    return _wrap(
        {
            "goal": goal,
            "generated_steps": steps,
            "execution": {
                "steps_total": len(steps),
                "steps_succeeded": sum(1 for r in results if r["success"]),
                "final_url": final_url,
                "results": results,
            },
        },
        _polarix(
            "browser_auto_sequence",
            t0,
            browser=bstate,
            params={
                "goal": goal,
                "url": url,
                "model": use_model,
                "explore": explore,
                "dry_run": False,
                "session_used": bool(
                    session_file and os.path.exists(session_file or "")
                ),
            },
            warnings=warnings or None,
        ),
    )
