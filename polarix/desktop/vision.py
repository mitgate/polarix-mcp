"""Pixel-level fallbacks for what the accessibility tree cannot see (canvases).

Two strategies, both window-relative so the result feeds `click {x, y}` directly:

  find_template()   OpenCV template matching — deterministic, no LLM. Give it a
                    crop of the thing to click (toolbar icon, a drawn element).
  vision_locate()   Ask a multimodal model for the coordinates of a described
                    element. vision_verify() asks it to judge an expectation
                    (pass/fail + reasoning) — the assertion for drawn results.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Optional

import numpy as np

from polarix.config import VISION_MODEL


def load_image_arg(image: str) -> bytes:
    """Accept a data URL, raw base64, or a file path."""
    text = (image or "").strip()
    if not text:
        raise ValueError("image is required (data URL, base64 or file path)")
    if text.startswith("data:"):
        _, _, payload = text.partition(",")
        return base64.b64decode(payload)
    if os.path.exists(text):
        with open(text, "rb") as f:
            return f.read()
    try:
        return base64.b64decode(text, validate=True)
    except Exception as exc:
        raise ValueError(
            f"image is neither a file, data URL nor base64: {exc}"
        ) from exc


def _decode(png: bytes, gray: bool = True) -> np.ndarray:
    import cv2

    flag = cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR
    arr = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), flag)
    if arr is None:
        raise ValueError("could not decode image bytes")
    return arr


def find_template(
    screenshot_png: bytes,
    template_png: bytes,
    threshold: float = 0.85,
    max_results: int = 5,
) -> dict:
    """Locate template inside screenshot. Coordinates are the template's centre.

    Returns {found, best: {x, y, score, rect}, matches: [...], threshold}.
    `rect` is [left, top, right, bottom] in screenshot pixels.
    """
    import cv2

    hay = _decode(screenshot_png)
    needle = _decode(template_png)
    th, tw = needle.shape[:2]
    hh, hw = hay.shape[:2]
    if th > hh or tw > hw:
        raise ValueError(
            f"template ({tw}x{th}) is larger than the screenshot ({hw}x{hh})"
        )
    scores = cv2.matchTemplate(hay, needle, cv2.TM_CCOEFF_NORMED)
    matches: list[dict] = []
    work = scores.copy()
    for _ in range(max_results):
        _, score, _, (x, y) = cv2.minMaxLoc(work)
        if score < threshold:
            break
        matches.append(
            {
                "x": int(x + tw // 2),
                "y": int(y + th // 2),
                "score": round(float(score), 4),
                "rect": [int(x), int(y), int(x + tw), int(y + th)],
            }
        )
        # suppress this hit so the next best is a different location
        x0, y0 = max(0, x - tw // 2), max(0, y - th // 2)
        work[y0 : y + th // 2 + 1, x0 : x + tw // 2 + 1] = -1.0
    return {
        "found": bool(matches),
        "best": matches[0] if matches else None,
        "matches": matches,
        "threshold": threshold,
        "screenshot_size": [int(hw), int(hh)],
        "template_size": [int(tw), int(th)],
    }


# ------------------------------------------------------------------ vision LLM
def _vision_prompt_locate(description: str, size: tuple[int, int]) -> str:
    return (
        "You see a screenshot of an application window "
        f"({size[0]}x{size[1]} pixels, origin top-left). "
        f"Locate this element: {description}\n"
        "Answer ONLY a JSON object: "
        '{"found": true|false, "x": <int>, "y": <int>, "confidence": 0..1, '
        '"reasoning": "<one sentence>"}. '
        "x,y must be the centre of the element in screenshot pixels."
    )


def _vision_prompt_verify(expectation: str) -> str:
    return (
        "You see a screenshot of an application window after a test step. "
        f"Judge this expectation: {expectation}\n"
        "Answer ONLY a JSON object: "
        '{"passed": true|false, "confidence": 0..1, "reasoning": "<one sentence>", '
        '"observed": "<what you actually see, briefly>"}.'
    )


async def vision_json(prompt: str, png: bytes, model: Optional[str] = None) -> Any:
    """Send one image + prompt to a multimodal model and parse the JSON reply."""
    from polarix.config import ANTHROPIC_API_KEY, OPENAI_API_KEY

    use_model = model or VISION_MODEL
    b64 = base64.b64encode(png).decode()
    if use_model.startswith("claude"):
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=use_model,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        raw = resp.content[0].text
    else:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set")
        import openai

        client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model=use_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        raw = resp.choices[0].message.content
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start : end + 1] if start >= 0 else raw)


def _size(png: bytes) -> tuple[int, int]:
    arr = _decode(png)
    return int(arr.shape[1]), int(arr.shape[0])


async def vision_locate(
    screenshot_png: bytes, description: str, model: Optional[str] = None
) -> dict:
    size = _size(screenshot_png)
    out = await vision_json(
        _vision_prompt_locate(description, size), screenshot_png, model
    )
    result = {
        "found": bool(out.get("found")),
        "x": int(out["x"]) if out.get("found") and "x" in out else None,
        "y": int(out["y"]) if out.get("found") and "y" in out else None,
        "confidence": float(out.get("confidence", 0) or 0),
        "reasoning": str(out.get("reasoning", "")),
        "model": model or VISION_MODEL,
        "screenshot_size": list(size),
    }
    if result["found"] and not (
        0 <= result["x"] < size[0] and 0 <= result["y"] < size[1]
    ):
        result["found"] = False
        result["reasoning"] += " (coordinates outside the screenshot were rejected)"
    return result


async def vision_verify(
    screenshot_png: bytes, expectation: str, model: Optional[str] = None
) -> dict:
    out = await vision_json(_vision_prompt_verify(expectation), screenshot_png, model)
    return {
        "passed": bool(out.get("passed")),
        "confidence": float(out.get("confidence", 0) or 0),
        "reasoning": str(out.get("reasoning", "")),
        "observed": str(out.get("observed", "")),
        "model": model or VISION_MODEL,
    }
