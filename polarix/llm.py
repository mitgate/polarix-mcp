"""LLM helpers — ask the model to plan a step sequence from a map.

generate_steps()          browser: site map → browser_execute_sequence steps
generate_desktop_steps()  desktop: control map → desktop_execute_sequence steps

Both share one JSON-only call path; both return [] (with a logged warning)
when the model call or the JSON parse fails, so callers can degrade cleanly.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from polarix.config import ANTHROPIC_API_KEY, OPENAI_API_KEY

logger = logging.getLogger("polarix-mcp")


async def _call_llm_json(prompt: str, model: str) -> Any:
    if model.startswith("claude"):
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
    else:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set")
        import openai

        client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
    return json.loads(raw)


def _extract_steps(parsed: Any, origin: str) -> list[dict]:
    steps = parsed.get("steps", parsed) if isinstance(parsed, dict) else parsed
    if isinstance(steps, list):
        return steps
    logger.warning("%s: unexpected shape — %s", origin, type(steps))
    return []


def build_browser_prompt(goal: str, map_context: dict) -> str:
    return (
        "You are a browser automation expert. Given a site map and a goal, "
        "generate a minimal and precise action sequence.\n\n"
        f"SITE MAP:\n{json.dumps(map_context, indent=2, ensure_ascii=False)}\n\n"
        f"GOAL: {goal}\n\n"
        "Available step types:\n"
        '  {"action": "goto", "url": "..."}\n'
        '  {"action": "click", "selector": "[data-qa=NAME]", "wait_after": 1.0}\n'
        '  {"action": "fill", "selector": "[data-qa=NAME]", "value": "..."}\n'
        '  {"action": "select", "selector": "...", "value": "..."}\n'
        '  {"action": "press", "key": "Enter"}\n'
        '  {"action": "wait_for", "selector": "[data-qa=NAME]"}\n'
        '  {"action": "wait_for", "seconds": 2}\n'
        '  {"action": "snapshot"}\n'
        '  {"action": "screenshot"}\n'
        '  {"action": "evaluate", "expression": "..."}\n\n'
        "Rules:\n"
        "- Use ONLY selectors from the site map above (prefer [data-qa=NAME])\n"
        "- Add wait_for or wait_after when actions trigger navigation or async loading\n"
        "- End with a snapshot step to capture the final state\n"
        '- Return ONLY a valid JSON object: {"steps": [...]}'
    )


def build_desktop_prompt(goal: str, map_context: dict) -> str:
    return (
        "You are a desktop (Windows UI Automation) test automation expert. Given a "
        "window map and a goal, generate a minimal and precise action sequence.\n\n"
        f"WINDOW MAP:\n{json.dumps(map_context, indent=2, ensure_ascii=False)}\n\n"
        f"GOAL: {goal}\n\n"
        "Locators are JSON objects with any of: auto_id, title, title_re, "
        "control_type, class_name, found_index, path. Prefer auto_id; otherwise "
        "title + control_type.\n\n"
        "Available step types:\n"
        '  {"action": "menu", "path": "File->Save"}\n'
        '  {"action": "click", "locator": {"auto_id": "1"}, "wait_after": 0.5}\n'
        '  {"action": "double_click", "locator": {...}}\n'
        '  {"action": "right_click", "locator": {...}}\n'
        '  {"action": "click", "x": 120, "y": 80}            (canvas fallback only)\n'
        '  {"action": "set_text", "locator": {...}, "value": "..."}\n'
        '  {"action": "type", "text": "..."}\n'
        '  {"action": "press", "key": "Enter"}   e.g. "ctrl+s", "alt+f", "F5"\n'
        '  {"action": "select", "locator": {...}, "item": "..."}\n'
        '  {"action": "wait_for", "locator": {...}, "timeout": 10}\n'
        '  {"action": "wait_for", "window": {"title_re": ".*Save As.*"}}\n'
        '  {"action": "wait_for", "seconds": 2}\n'
        '  {"action": "snapshot"}\n'
        '  {"action": "screenshot"}\n\n'
        "Rules:\n"
        "- Use ONLY controls and menu paths present in the window map above\n"
        "- When a step opens a dialog, add wait_for window before acting on it\n"
        "- Use coordinates only when the map shows no control for that area\n"
        "- End with a snapshot step to capture the final state\n"
        '- Return ONLY a valid JSON object: {"steps": [...]}'
    )


async def generate_steps(goal: str, map_context: dict, model: str) -> list[dict]:
    """Browser planner — steps compatible with browser_execute_sequence."""
    try:
        parsed = await _call_llm_json(build_browser_prompt(goal, map_context), model)
        return _extract_steps(parsed, "generate_steps")
    except Exception as exc:
        logger.warning("generate_steps failed: %s", exc)
        return []


async def generate_desktop_steps(
    goal: str, map_context: dict, model: str
) -> list[dict]:
    """Desktop planner — steps compatible with desktop_execute_sequence."""
    try:
        parsed = await _call_llm_json(build_desktop_prompt(goal, map_context), model)
        return _extract_steps(parsed, "generate_desktop_steps")
    except Exception as exc:
        logger.warning("generate_desktop_steps failed: %s", exc)
        return []
