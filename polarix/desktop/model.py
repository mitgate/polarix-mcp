"""Control model, locator parsing, control index and inventory diff.

Pure Python — no pywinauto here, so everything in this module is testable on
any platform and shared by the real and the simulated drivers.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

LOCATOR_KEYS = (
    "auto_id",
    "title",
    "title_re",
    "control_type",
    "class_name",
    "found_index",
    "path",
)

WINDOW_KEYS = ("title", "title_re", "process", "pid", "handle", "class_name")

# Control types worth listing in the compact index handed to the planner.
INTERACTIVE_TYPES = {
    "Button",
    "CheckBox",
    "ComboBox",
    "DataItem",
    "Document",
    "Edit",
    "Hyperlink",
    "ListItem",
    "Menu",
    "MenuBar",
    "MenuItem",
    "RadioButton",
    "Slider",
    "Spinner",
    "SplitButton",
    "TabItem",
    "Text",
    "TreeItem",
}

_INDEX_CAP_PER_TYPE = 60


@dataclass
class Control:
    """One node of a window's control tree."""

    control_type: str
    title: str = ""
    auto_id: str = ""
    class_name: str = ""
    rect: Optional[list[int]] = None  # [left, top, right, bottom], screen coords
    enabled: bool = True
    visible: bool = True
    depth: int = 0
    path: str = ""  # child indexes from the window root, e.g. "0/2/1"
    value: Optional[str] = None
    children: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("extra", None)
        return {
            k: v
            for k, v in data.items()
            if v not in ("", None, {}) or k in ("control_type", "path", "depth")
        }

    def key(self) -> str:
        """Stable identity for diffs: auto_id wins, path is the fallback."""
        if self.auto_id:
            return f"{self.control_type}#{self.auto_id}"
        return f"{self.control_type}@{self.path}"

    def locator(self) -> dict:
        """Smallest locator that re-finds this control from a map."""
        if self.auto_id:
            return {"auto_id": self.auto_id}
        if self.title:
            return {"title": self.title, "control_type": self.control_type}
        return {"path": self.path}

    def contains_point(self, x: int, y: int) -> bool:
        if not self.rect or len(self.rect) != 4:
            return False
        left, top, right, bottom = self.rect
        return left <= x <= right and top <= y <= bottom


def parse_locator(raw: Any) -> dict:
    """Accept a dict, a JSON-ish dict already parsed, or a shorthand string.

    Shorthand forms:
      "auto_id=btnSave"                 → {"auto_id": "btnSave"}
      "title=Salvar;control_type=Button" → both keys
      "Salvar"                          → {"title": "Salvar"}
    """
    if raw is None:
        raise ValueError("locator is required")
    if isinstance(raw, dict):
        unknown = set(raw) - set(LOCATOR_KEYS)
        if unknown:
            raise ValueError(
                f"Unknown locator keys {sorted(unknown)}; allowed: {LOCATOR_KEYS}"
            )
        if not raw:
            raise ValueError("locator cannot be empty")
        return dict(raw)
    if isinstance(raw, str):
        return _parse_locator_string(raw)
    raise ValueError(f"Unsupported locator type: {type(raw).__name__}")


def _parse_locator_string(raw: str) -> dict:
    text = raw.strip()
    if not text:
        raise ValueError("locator cannot be empty")
    if "=" not in text:
        return {"title": text}
    loc: dict = {}
    for part in text.split(";"):
        if not part.strip():
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        if key not in LOCATOR_KEYS:
            raise ValueError(f"Unknown locator key '{key}'")
        loc[key] = int(value) if key == "found_index" else value.strip()
    if not loc:
        raise ValueError("locator cannot be empty")
    return loc


def parse_window(raw: Any) -> dict:
    """Window locator: dict with WINDOW_KEYS, or a string meaning 'title contains'."""
    if raw is None:
        raise ValueError("window locator is required")
    if isinstance(raw, dict):
        unknown = set(raw) - set(WINDOW_KEYS)
        if unknown:
            raise ValueError(
                f"Unknown window keys {sorted(unknown)}; allowed: {WINDOW_KEYS}"
            )
        if not raw:
            raise ValueError("window locator cannot be empty")
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("window locator cannot be empty")
        return {"title_re": f".*{re.escape(text)}.*"}
    raise ValueError(f"Unsupported window locator type: {type(raw).__name__}")


def matches(control: Control, locator: dict) -> bool:
    if "auto_id" in locator and control.auto_id != str(locator["auto_id"]):
        return False
    if "title" in locator and control.title != locator["title"]:
        return False
    if "title_re" in locator and not re.match(locator["title_re"], control.title or ""):
        return False
    if "control_type" in locator and control.control_type != locator["control_type"]:
        return False
    if "class_name" in locator and control.class_name != locator["class_name"]:
        return False
    if "path" in locator and control.path != locator["path"]:
        return False
    return True


def find_controls(controls: list[Control], locator: dict) -> list[Control]:
    """All controls matching the locator. found_index narrows to one element."""
    hits = [c for c in controls if matches(c, locator)]
    idx = locator.get("found_index")
    if idx is not None and hits:
        try:
            return [hits[int(idx)]]
        except (IndexError, ValueError):
            return []
    return hits


def build_control_index(controls: list[Control]) -> dict:
    """Compact map grouped by control_type for the planner prompt.

    Only interactive types or nodes with a title/auto_id are listed, capped per
    type, mirroring what selector_index does for [data-qa] on the web side.
    """
    index: dict[str, list[dict]] = {}
    for c in controls:
        if c.control_type not in INTERACTIVE_TYPES and not (c.title or c.auto_id):
            continue
        bucket = index.setdefault(c.control_type, [])
        if len(bucket) >= _INDEX_CAP_PER_TYPE:
            continue
        entry: dict = {"path": c.path}
        if c.title:
            entry["title"] = c.title
        if c.auto_id:
            entry["auto_id"] = c.auto_id
        if c.value not in (None, ""):
            entry["value"] = c.value
        if not c.enabled:
            entry["enabled"] = False
        bucket.append(entry)
    return index


def diff_inventories(before: list[Control], after: list[Control]) -> dict:
    """Structural diff of two control inventories (same window, two moments)."""
    a = {c.key(): c for c in before}
    b = {c.key(): c for c in after}
    added = [b[k].to_dict() for k in b if k not in a]
    removed = [a[k].to_dict() for k in a if k not in b]
    changed_texts: list[dict] = []
    changed_state: list[dict] = []
    for k in a:
        if k not in b:
            continue
        ca, cb = a[k], b[k]
        if (ca.title, ca.value) != (cb.title, cb.value):
            changed_texts.append(
                {
                    "key": k,
                    "before": {"title": ca.title, "value": ca.value},
                    "after": {"title": cb.title, "value": cb.value},
                }
            )
        if (ca.enabled, ca.visible) != (cb.enabled, cb.visible):
            changed_state.append(
                {
                    "key": k,
                    "before": {"enabled": ca.enabled, "visible": ca.visible},
                    "after": {"enabled": cb.enabled, "visible": cb.visible},
                }
            )
    return {
        "added": added,
        "removed": removed,
        "changed_texts": changed_texts,
        "changed_state": changed_state,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "text_changes": len(changed_texts),
            "state_changes": len(changed_state),
        },
    }
