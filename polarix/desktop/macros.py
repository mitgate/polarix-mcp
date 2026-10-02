"""Named macro persistence — POLARIX_MACROS_DIR/{name}.json.

A macro is a step list (see steps.py) plus the window it was made for and
where it came from (recorded | generated | manual).
"""

from __future__ import annotations

from polarix.config import env_setting
import json
import os
import re
import tempfile
import time
from typing import Optional


def macros_dir() -> str:
    return env_setting(
        "MACROS_DIR", os.path.join(tempfile.gettempdir(), "polarix_macros")
    )


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()).strip("_")
    if not cleaned:
        raise ValueError("macro name cannot be empty")
    return cleaned


def macro_path(name: str) -> str:
    return os.path.join(macros_dir(), f"{_safe_name(name)}.json")


def save_macro(
    name: str,
    steps: list[dict],
    window: Optional[dict],
    source: str = "manual",
    meta: Optional[dict] = None,
) -> dict:
    os.makedirs(macros_dir(), exist_ok=True)
    doc = {
        "name": _safe_name(name),
        "window": window,
        "steps": steps,
        "source": source,
        "step_count": len(steps),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "meta": meta or {},
    }
    path = macro_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    doc["path"] = path
    return doc


def load_macro(name: str) -> dict:
    path = macro_path(name)
    if not os.path.exists(path):
        raise LookupError(f"Macro '{name}' not found in {macros_dir()}")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    doc["path"] = path
    return doc


def list_macros() -> list[dict]:
    out: list[dict] = []
    directory = macros_dir()
    if not os.path.isdir(directory):
        return out
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, fname), encoding="utf-8") as f:
                doc = json.load(f)
            out.append(
                {
                    "name": doc.get("name", fname[:-5]),
                    "step_count": doc.get("step_count", len(doc.get("steps", []))),
                    "source": doc.get("source"),
                    "window": doc.get("window"),
                    "created_at": doc.get("created_at"),
                }
            )
        except Exception as exc:
            out.append({"name": fname[:-5], "error": str(exc)})
    return out


def delete_macro(name: str) -> bool:
    path = macro_path(name)
    if os.path.exists(path):
        os.unlink(path)
        return True
    return False
