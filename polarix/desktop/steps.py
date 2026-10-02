"""run_desktop_steps() — the desktop twin of polarix.tools.execution._run_steps.

Same result record per step (step, action, success, duration_ms,
locator_match_count, result, error) so clients can reason about both sides
with one mental model.

Actions
  launch        {path, args?, title_re?, wait_seconds?}
  focus         {window}
  click | double_click | right_click
                {locator} or {x, y} (window coords — canvas fallback), wait_after?
  click_image   {image, threshold?, button?, double?, offset_x?, offset_y?}
  click_vision  {description, model?, button?}      (multimodal LLM picks x,y)
  set_text      {locator, value}
  type          {text} or {keys} (pywinauto key syntax), locator?
  press         {key}  e.g. "Enter", "ctrl+s", "alt+f", "F5", "{ENTER}"
  select        {locator, item}
  menu          {path}  e.g. "Arquivo->Salvar"
  wait_for      {locator, state?} | {window} | {image, threshold?} | {seconds}, timeout?
  wait_idle     {timeout?, interval?}   (tree stopped changing)
  assert        {kind, ...}  see ASSERT_KINDS — fails the step when false
  snapshot      {max_depth?}
  screenshot    {}
  close         {force?}

Locator healing: when a locator matches nothing, the runner tries the
`_recorded`/`fallback` hint (title + control_type), then a fuzzy title match
over the live tree. A healed step reports `healed_locator` and a warning so
the script can be updated — this is what keeps recorded macros alive across
application versions.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import re
import time
from typing import Any, Optional

from polarix.desktop.driver import DesktopDriver
from polarix.desktop.model import Control, parse_locator, parse_window
from polarix.telemetry import _elapsed_ms, _start

_KEY_NAMES = {
    "enter": "{ENTER}",
    "return": "{ENTER}",
    "tab": "{TAB}",
    "esc": "{ESC}",
    "escape": "{ESC}",
    "backspace": "{BACKSPACE}",
    "delete": "{DELETE}",
    "del": "{DELETE}",
    "space": "{SPACE}",
    "up": "{UP}",
    "down": "{DOWN}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
    "home": "{HOME}",
    "end": "{END}",
    "pageup": "{PGUP}",
    "pagedown": "{PGDN}",
    "insert": "{INSERT}",
}
_MODIFIERS = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+", "win": ""}

ASSERT_KINDS = (
    "control_exists",
    "control_absent",
    "control_enabled",
    "control_disabled",
    "text_equals",
    "text_contains",
    "window_exists",
    "window_absent",
    "window_title_contains",
    "image_present",
    "image_absent",
    "vision",
)


def key_to_pywinauto(key: str) -> str:
    """'ctrl+shift+s' → '^+s'; 'Enter' → '{ENTER}'; pass-through for raw syntax."""
    raw = key.strip()
    if not raw:
        raise ValueError("press needs a key")
    if "{" in raw or raw[0] in "^%+":
        return raw
    parts = [p.strip().lower() for p in re.split(r"\+", raw) if p.strip()]
    mods = "".join(_MODIFIERS[p] for p in parts[:-1] if p in _MODIFIERS)
    last = parts[-1]
    if last in _KEY_NAMES:
        return mods + _KEY_NAMES[last]
    if re.fullmatch(r"f([1-9]|1[0-2])", last):
        return mods + "{" + last.upper() + "}"
    if len(last) == 1:
        return mods + last
    return mods + "{" + last.upper() + "}"


def _png_data_url(data: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(data).decode()}"


def _run_async(coro: Any) -> Any:
    """Vision helpers are async (LLM SDKs); the step runner is sync."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a loop (tests calling the runner directly): use a thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ------------------------------------------------------------- locator healing
def _fuzzy_by_title(
    inventory: list[Control], loc: dict, cutoff: float = 0.8
) -> Optional[Control]:
    wanted = str(loc.get("title", "")).strip().lower()
    if not wanted:
        return None
    best, best_ratio = None, 0.0
    for c in inventory:
        if "control_type" in loc and c.control_type != loc["control_type"]:
            continue
        title = (c.title or "").strip().lower()
        if not title:
            continue
        ratio = difflib.SequenceMatcher(None, wanted, title).ratio()
        if wanted in title:
            ratio = max(ratio, 0.9)
        if ratio > best_ratio:
            best, best_ratio = c, ratio
    return best if best_ratio >= cutoff else None


def resolve_locator(
    driver: DesktopDriver, window: dict, step: dict, loc: dict
) -> tuple[list[Control], dict, Optional[str]]:
    """Find controls for `loc`; on miss try the healing chain.

    Returns (hits, locator_used, healing_note). healing_note is None when the
    original locator worked.
    """
    hits = driver.find(window, loc)
    if hits:
        return hits, loc, None

    candidates: list[tuple[str, dict]] = []
    hint = step.get("_recorded") or step.get("fallback") or {}
    if isinstance(hint, dict) and hint.get("title"):
        cand = {"title": hint["title"]}
        if hint.get("control_type"):
            cand["control_type"] = hint["control_type"]
        if cand != loc:
            candidates.append(("recorded hint", cand))
    if "title" in loc:
        inventory = driver.inventory(window, 32)
        fuzzy = _fuzzy_by_title(inventory, loc)
        if fuzzy is not None:
            candidates.append((f"fuzzy title '{fuzzy.title}'", fuzzy.locator()))
    for note, cand in candidates:
        hits = driver.find(window, cand)
        if hits:
            return hits, cand, note
    return [], loc, None


# -------------------------------------------------------------------- asserts
def _control_text(ctl: Control) -> str:
    return ctl.value if ctl.value not in (None, "") else (ctl.title or "")


_CONTROL_ASSERTS = (
    "control_exists",
    "control_absent",
    "control_enabled",
    "control_disabled",
    "text_equals",
    "text_contains",
)
_WINDOW_ASSERTS = ("window_exists", "window_absent", "window_title_contains")


def _assert_control(driver, win: dict, step: dict, kind: str) -> tuple[bool, dict]:
    loc = parse_locator(step["locator"])
    hits, used, note = resolve_locator(driver, win, step, loc)
    detail: dict[str, Any] = {"locator": used, "matches": len(hits)}
    if note:
        detail["healed"] = note
    if kind == "control_exists":
        return bool(hits), detail
    if kind == "control_absent":
        return not hits, detail
    if not hits:
        detail["reason"] = "control not found"
        return False, detail
    if kind == "control_enabled":
        return hits[0].enabled, detail
    if kind == "control_disabled":
        return not hits[0].enabled, detail
    actual = _control_text(hits[0])
    expected = str(step.get("expected", ""))
    detail.update(expected=expected, actual=actual)
    if kind == "text_equals":
        return actual == expected, detail
    return expected.lower() in actual.lower(), detail


def _assert_window(driver, win: dict, step: dict, kind: str) -> tuple[bool, dict]:
    target = parse_window(step.get("window") or win)
    try:
        state = driver.window_state(target)
        exists = True
    except LookupError:
        state, exists = None, False
    detail: dict[str, Any] = {"window": target, "exists": exists}
    if kind == "window_exists":
        return exists, detail
    if kind == "window_absent":
        return not exists, detail
    expected = str(step.get("expected", ""))
    actual = (state or {}).get("title", "")
    detail.update(expected=expected, actual=actual)
    return exists and expected.lower() in actual.lower(), detail


def _assert_image(driver, window, step: dict, kind: str) -> tuple[bool, dict]:
    from polarix.desktop.vision import find_template, load_image_arg

    found = find_template(
        driver.screenshot(window),
        load_image_arg(step["image"]),
        float(step.get("threshold", 0.85)),
    )
    detail = {"best": found["best"], "threshold": found["threshold"]}
    return (found["found"] if kind == "image_present" else not found["found"]), detail


def _assert_vision(driver, window, step: dict) -> tuple[bool, dict]:
    from polarix.desktop.vision import vision_verify

    verdict = _run_async(
        vision_verify(
            driver.screenshot(window), str(step["expectation"]), step.get("model")
        )
    )
    return verdict["passed"], dict(verdict)


def run_assert(driver: DesktopDriver, window: Optional[dict], step: dict) -> dict:
    """Evaluate one assertion. Returns {kind, passed, detail}; raises on bad input."""
    kind = str(step.get("kind", "")).strip()
    if kind not in ASSERT_KINDS:
        raise ValueError(f"assert kind must be one of {ASSERT_KINDS}, got '{kind}'")
    win = window or {}
    if kind in _CONTROL_ASSERTS:
        passed, detail = _assert_control(driver, win, step, kind)
    elif kind in _WINDOW_ASSERTS:
        passed, detail = _assert_window(driver, win, step, kind)
    elif kind in ("image_present", "image_absent"):
        passed, detail = _assert_image(driver, window, step, kind)
    else:
        passed, detail = _assert_vision(driver, window, step)
    return {"kind": kind, "passed": passed, "detail": detail}


def _wait_idle(driver: DesktopDriver, window: dict, timeout: float, interval: float):
    deadline = time.monotonic() + timeout
    fingerprint = None
    polls = 0
    while True:
        inv = driver.inventory(window, 32)
        current = tuple((c.key(), c.title, c.value, c.enabled) for c in inv)
        polls += 1
        if fingerprint is not None and current == fingerprint:
            return {"idle": True, "polls": polls, "controls": len(inv)}
        fingerprint = current
        if time.monotonic() >= deadline:
            return {"idle": False, "polls": polls, "controls": len(inv)}
        time.sleep(interval)


def _wait_for_image(driver, window, step) -> dict:
    from polarix.desktop.vision import find_template, load_image_arg

    template = load_image_arg(step["image"])
    threshold = float(step.get("threshold", 0.85))
    timeout = float(step.get("timeout", 10.0))
    deadline = time.monotonic() + timeout
    while True:
        found = find_template(driver.screenshot(window), template, threshold)
        if found["found"]:
            return found
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"image not found after {timeout}s (best score "
                f"{found['matches'][0]['score'] if found['matches'] else 'n/a'})"
            )
        time.sleep(float(step.get("interval", 0.5)))


def _click_image(driver, window, step) -> dict:
    from polarix.desktop.vision import find_template, load_image_arg

    png = driver.screenshot(window)
    found = find_template(
        png, load_image_arg(step["image"]), float(step.get("threshold", 0.85))
    )
    if not found["found"]:
        raise LookupError(
            "image not found in window (best score "
            f"{found['matches'][0]['score'] if found['matches'] else 'below threshold'})"
        )
    x = found["best"]["x"] + int(step.get("offset_x", 0))
    y = found["best"]["y"] + int(step.get("offset_y", 0))
    hit = driver.click(
        window,
        x=x,
        y=y,
        button=step.get("button", "left"),
        double=bool(step.get("double", False)),
    )
    return {"match": found["best"], "clicked": [x, y], **hit}


def _click_vision(driver, window, step) -> dict:
    from polarix.desktop.vision import vision_locate

    png = driver.screenshot(window)
    located = _run_async(
        vision_locate(png, str(step["description"]), step.get("model"))
    )
    if not located["found"]:
        raise LookupError(f"vision could not locate element: {located['reasoning']}")
    hit = driver.click(
        window,
        x=located["x"],
        y=located["y"],
        button=step.get("button", "left"),
        double=bool(step.get("double", False)),
    )
    return {"vision": located, "clicked": [located["x"], located["y"]], **hit}


def _click_locator(driver, window, step, action, sr, warnings, i) -> dict:
    button = "right" if action == "right_click" else "left"
    double = action == "double_click"
    loc = parse_locator(step["locator"])
    hits, used, note = resolve_locator(driver, window, step, loc)
    sr["locator_match_count"] = len(hits)
    if not hits:
        warnings.append(f"Step {i+1} ({action}): locator {loc} matched 0 controls")
        raise LookupError(f"Locator {loc} matched 0 controls")
    if note:
        sr["healed_locator"] = used
        warnings.append(
            f"Step {i+1} ({action}): locator {loc} healed via {note} → {used}"
        )
    if len(hits) > 1 and "found_index" not in used:
        warnings.append(
            f"Step {i+1} ({action}): locator {used} matched {len(hits)} controls, used first"
        )
    return driver.click(window, used, button=button, double=double)


def run_desktop_steps(  # noqa: C901
    driver: DesktopDriver,
    window: Optional[dict],
    steps: list[dict],
    stop_on_error: bool = True,
) -> tuple[list[dict], list[str], Optional[dict]]:
    """Execute steps against the driver. Returns (results, warnings, window).

    `window` is the window locator in effect; launch / focus / wait_for(window)
    update it, so later steps act on the window the sequence opened.
    """
    results: list[dict] = []
    warnings: list[str] = []
    current = dict(window) if window else None

    for i, step in enumerate(steps):
        action = str(step.get("action", "")).strip()
        st = _start()
        sr: dict[str, Any] = {
            "step": i + 1,
            "action": action,
            "success": False,
            "duration_ms": 0,
            "locator_match_count": None,
            "result": None,
            "error": None,
        }
        win = current or {}
        try:
            if action == "launch":
                info = driver.launch(
                    step["path"],
                    args=step.get("args", ""),
                    cwd=step.get("cwd"),
                    wait_seconds=float(step.get("wait_seconds", 3.0)),
                    title_re=step.get("title_re"),
                )
                current = (
                    {"handle": info["handle"]}
                    if info.get("handle")
                    else {"title": info["title"]}
                )
                sr["result"] = info

            elif action == "focus":
                current = parse_window(step.get("window") or current)
                driver.focus(current)
                sr["result"] = driver.window_state(current)

            elif action in ("click", "double_click", "right_click"):
                if step.get("locator") is not None:
                    sr["result"] = _click_locator(
                        driver, win, step, action, sr, warnings, i
                    )
                else:
                    sr["result"] = driver.click(
                        win,
                        x=step.get("x"),
                        y=step.get("y"),
                        button="right" if action == "right_click" else "left",
                        double=action == "double_click",
                    )
                time.sleep(float(step.get("wait_after", 0.5)))

            elif action == "click_image":
                sr["result"] = _click_image(driver, win, step)
                time.sleep(float(step.get("wait_after", 0.5)))

            elif action == "click_vision":
                sr["result"] = _click_vision(driver, win, step)
                time.sleep(float(step.get("wait_after", 0.5)))

            elif action == "set_text":
                loc = parse_locator(step["locator"])
                hits, used, note = resolve_locator(driver, win, step, loc)
                sr["locator_match_count"] = len(hits)
                if note:
                    sr["healed_locator"] = used
                    warnings.append(
                        f"Step {i+1} (set_text): healed via {note} → {used}"
                    )
                driver.set_text(win, used, str(step["value"]))

            elif action == "type":
                keys = step.get("keys")
                if keys is None:
                    keys = str(step["text"])
                    # literal text: escape pywinauto specials so '+' and '{' type as-is
                    keys = re.sub(r"([{}^%+~()])", r"{\1}", keys)
                loc = parse_locator(step["locator"]) if step.get("locator") else None
                driver.type_keys(win, keys, loc)

            elif action == "press":
                driver.type_keys(win, key_to_pywinauto(str(step["key"])))

            elif action == "select":
                loc = parse_locator(step["locator"])
                hits, used, note = resolve_locator(driver, win, step, loc)
                sr["locator_match_count"] = len(hits)
                if note:
                    sr["healed_locator"] = used
                driver.select(win, used, str(step["item"]))

            elif action == "menu":
                driver.menu_select(win, str(step["path"]))
                time.sleep(float(step.get("wait_after", 0.5)))

            elif action == "wait_for":
                timeout = float(step.get("timeout", 10.0))
                if step.get("locator") is not None:
                    loc = parse_locator(step["locator"])
                    ctl = driver.wait_control(
                        win, loc, timeout, step.get("state", "visible")
                    )
                    sr["locator_match_count"] = len(driver.find(win, loc))
                    sr["result"] = ctl.to_dict()
                elif step.get("window") is not None:
                    target = parse_window(step["window"])
                    sr["result"] = driver.wait_window(target, timeout)
                    current = target
                elif step.get("image") is not None:
                    sr["result"] = _wait_for_image(driver, win, step)
                elif step.get("seconds") is not None:
                    time.sleep(float(step["seconds"]))
                else:
                    raise ValueError("wait_for needs locator, window, image or seconds")

            elif action == "wait_idle":
                sr["result"] = _wait_idle(
                    driver,
                    win,
                    float(step.get("timeout", 10.0)),
                    float(step.get("interval", 0.3)),
                )
                if not sr["result"]["idle"]:
                    raise TimeoutError("window kept changing until the timeout")

            elif action == "assert":
                outcome = run_assert(driver, current, step)
                sr["result"] = outcome
                if outcome["detail"].get("healed"):
                    sr["healed_locator"] = outcome["detail"].get("locator")
                if not outcome["passed"]:
                    raise AssertionError(
                        step.get("message")
                        or f"{outcome['kind']} failed: {outcome['detail']}"
                    )

            elif action == "snapshot":
                inv = driver.inventory(win, int(step.get("max_depth", 8)))
                sr["result"] = {
                    "window": driver.window_state(win),
                    "control_count": len(inv),
                    "controls": [c.to_dict() for c in inv],
                }

            elif action == "screenshot":
                sr["result"] = _png_data_url(driver.screenshot(current))

            elif action == "close":
                driver.close(win, force=bool(step.get("force", False)))

            else:
                raise ValueError(f"Unknown action: '{action}'")

            sr["success"] = True

        except Exception as exc:
            sr["error"] = f"{type(exc).__name__}: {exc}"
            sr["duration_ms"] = _elapsed_ms(st)
            results.append(sr)
            if stop_on_error:
                break
            continue

        sr["duration_ms"] = _elapsed_ms(st)
        results.append(sr)

    return results, warnings, current
