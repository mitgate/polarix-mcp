"""SQLite ledger of everything Polarix executed.

record_response() receives the payload a tool is about to return plus its
_polarix block and extracts, without the tools knowing about it:

  runs       one row per tool call: kind (browser/desktop/vm), target, success,
             duration, warnings, step counts
  steps      one row per executed step (sequences, planner runs, scenario phases):
             action, success, duration, locator match count, healed, error type,
             pixel fallback, assertion kind
  maps       one row per map/explore call: element count, stable identifiers,
             the identifier list (for drift between consecutive maps)
  scenarios  one row per scenario result (single or inside a suite)

Recording is best effort: any failure is logged at DEBUG and never reaches
the caller. POLARIX_METRICS=off disables it.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from polarix.config import env_setting

logger = logging.getLogger("polarix-mcp")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  tool TEXT NOT NULL,
  kind TEXT NOT NULL,
  target TEXT,
  success INTEGER NOT NULL,
  duration_ms INTEGER,
  warnings INTEGER DEFAULT 0,
  steps_total INTEGER,
  steps_ok INTEGER,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS runs_ts ON runs(ts);
CREATE INDEX IF NOT EXISTS runs_target ON runs(target, ts);

CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  target TEXT,
  idx INTEGER,
  action TEXT,
  success INTEGER NOT NULL,
  duration_ms INTEGER,
  match_count INTEGER,
  healed INTEGER DEFAULT 0,
  pixel INTEGER DEFAULT 0,
  assert_kind TEXT,
  error_type TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS steps_ts ON steps(ts);
CREATE INDEX IF NOT EXISTS steps_target ON steps(target, ts);

CREATE TABLE IF NOT EXISTS maps (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  target TEXT,
  elements INTEGER,
  stable_ids INTEGER,
  identifiers TEXT
);
CREATE INDEX IF NOT EXISTS maps_target ON maps(target, ts);

CREATE TABLE IF NOT EXISTS scenarios (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  target TEXT,
  name TEXT,
  status TEXT,
  asserts_total INTEGER,
  asserts_passed INTEGER,
  duration_ms INTEGER,
  first_failure_type TEXT
);
CREATE INDEX IF NOT EXISTS scenarios_name ON scenarios(name, ts);
"""


def db_path() -> str:
    return env_setting(
        "METRICS_DB", os.path.join(tempfile.gettempdir(), "polarix_metrics.sqlite")
    )


def enabled() -> bool:
    return env_setting("METRICS", "on").strip().lower() not in ("off", "0", "false")


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    target = path or db_path()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


# ----------------------------------------------------------------- extraction
def _kind_of(tool: str) -> Optional[str]:
    for prefix in ("browser", "desktop", "vm"):
        if tool.startswith(prefix + "_"):
            return prefix
    return None


def _target_of(kind: str, data: dict, block: dict) -> Optional[str]:
    if kind == "browser":
        url = (block.get("browser") or {}).get("final_url") or (
            block.get("effective_params") or {}
        ).get("url")
        if url:
            host = urlparse(str(url)).hostname
            if host:
                return host
        return None
    desk = block.get("desktop") or {}
    if kind == "vm":
        return desk.get("vm") or desk.get("backend")
    win = desk.get("window") or {}
    if isinstance(win, dict):
        return win.get("process") or win.get("title") or None
    return None


def _error_type(error: Any) -> Optional[str]:
    if not error:
        return None
    text = str(error)
    head, sep, _ = text.partition(":")
    return head.strip() if sep and " " not in head.strip() else "Error"


def _step_rows(results: Iterable[dict]) -> list[dict]:
    rows = []
    for r in results:
        if not isinstance(r, dict) or "action" not in r:
            continue
        action = str(r.get("action", ""))
        result = r.get("result") if isinstance(r.get("result"), dict) else {}
        detail = None
        if r.get("error"):
            detail = str(r["error"])[:200]
        elif r.get("healed_locator") is not None:
            detail = json.dumps(r["healed_locator"], ensure_ascii=False)[:200]
        rows.append(
            {
                "idx": r.get("step"),
                "action": action,
                "success": bool(r.get("success")),
                "duration_ms": r.get("duration_ms"),
                "match_count": r.get(
                    "locator_match_count", r.get("selector_match_count")
                ),
                "healed": r.get("healed_locator") is not None,
                "pixel": action in ("click_image", "click_vision"),
                "assert_kind": result.get("kind") if action == "assert" else None,
                "error_type": _error_type(r.get("error")),
                "detail": detail,
            }
        )
    return rows


def _collect_steps(data: dict) -> list[dict]:
    rows: list[dict] = []
    if isinstance(data.get("results"), list) and data["results"]:
        first = data["results"][0]
        if isinstance(first, dict) and "action" in first:
            rows += _step_rows(data["results"])
    execution = data.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("results"), list):
        rows += _step_rows(execution["results"])
    phases = data.get("phases")
    if isinstance(phases, dict):
        for phase_rows in phases.values():
            if isinstance(phase_rows, list):
                rows += _step_rows(phase_rows)
    return rows


def _ids_browser_map_site(data: dict) -> tuple[int, list[str]]:
    identifiers: list[str] = []
    elements = 0
    for page in data.get("pages", []) or []:
        for el in page.get("data_qa_elements", []) or []:
            elements += int(el.get("count", 1) or 1)
        for form in page.get("forms", []) or []:
            for inp in form.get("inputs", []) or []:
                elements += 1
                ident = inp.get("data_qa") or inp.get("id") or inp.get("name")
                if ident:
                    identifiers.append(f"input:{ident}")
    identifiers += [f"qa:{k}" for k in (data.get("selector_index") or {}).keys()]
    return elements, identifiers


def _ids_browser_explore_page(data: dict) -> tuple[int, list[str]]:
    identifiers: list[str] = []
    elements = 0
    static = data.get("static_elements") or {}
    for el in static.get("data_qa_elements", []) or []:
        elements += int(el.get("count", 1) or 1)
        identifiers.append(f"qa:{el.get('qa')}")
    for rev in data.get("revealed_after_interactions", []) or []:
        for el in rev.get("new_elements", []) or []:
            identifiers.append(f"qa:{el.get('qa')}")
    return elements, identifiers


def _ids_desktop_map_window(data: dict) -> tuple[int, list[str]]:
    identifiers: list[str] = []
    controls = data.get("controls", []) or []
    for c in controls:
        if c.get("auto_id"):
            identifiers.append(f"id:{c['auto_id']}")
        elif c.get("title"):
            identifiers.append(f"{c.get('control_type')}:{c['title']}")
    for menu in data.get("menus", []) or []:
        identifiers.append(f"menu:{menu.get('title')}")
    return len(controls), identifiers


def _ids_desktop_explore_menus(data: dict) -> tuple[int, list[str]]:
    paths = data.get("menu_paths", []) or []
    return len(paths), [f"menu:{p}" for p in paths]


def _ids_desktop_map_app(data: dict) -> tuple[int, list[str]]:
    """Whole-application map: every window's identifiers plus menu paths and dialogs."""
    identifiers: list[str] = []
    elements = 0
    for w in data.get("windows", []) or []:
        elements += int(w.get("control_count") or 0)
        if w.get("depth", 0) > 0 and w.get("title"):
            identifiers.append(f"dialog:{w['title']}")
        for entries in (w.get("control_index") or {}).values():
            for e in entries:
                if e.get("auto_id"):
                    identifiers.append(f"id:{e['auto_id']}")
                elif e.get("title"):
                    identifiers.append(f"{w.get('title')}/{e['title']}")
    for f in data.get("feature_index", []) or []:
        if f.get("kind") == "menu":
            identifiers.append(f"menu:{f['name']}")
    return elements, identifiers


_MAP_EXTRACTORS = {
    "browser_map_site": _ids_browser_map_site,
    "browser_explore_page": _ids_browser_explore_page,
    "desktop_map_window": _ids_desktop_map_window,
    "desktop_explore_menus": _ids_desktop_explore_menus,
    "desktop_map_app": _ids_desktop_map_app,
}
_STABLE_PREFIXES = ("qa", "id", "input", "menu")


def _map_row(tool: str, data: dict) -> Optional[dict]:
    """Element count, stable-identifier count and identifier list for map tools."""
    extractor = _MAP_EXTRACTORS.get(tool)
    if extractor is None:
        return None
    elements, identifiers = extractor(data)
    stable = {i for i in identifiers if i.split(":", 1)[0] in _STABLE_PREFIXES}
    return {
        "elements": elements,
        "stable_ids": len(stable),
        "identifiers": json.dumps(sorted(set(identifiers)), ensure_ascii=False),
    }


def _scenario_rows(data: dict) -> list[dict]:
    def row(sc: dict) -> dict:
        asserts = sc.get("assertions") or {}
        return {
            "name": sc.get("name"),
            "status": sc.get("status"),
            "asserts_total": asserts.get("total", 0),
            "asserts_passed": asserts.get("passed", 0),
            "duration_ms": sc.get("duration_ms"),
            "first_failure_type": _error_type(
                (sc.get("first_failure") or "").split("): ", 1)[-1]
            ),
        }

    if data.get("status") in ("passed", "failed", "error") and "assertions" in data:
        return [row(data)]
    if isinstance(data.get("results"), list) and data["results"]:
        first = data["results"][0]
        if isinstance(first, dict) and "status" in first and "assertions" in first:
            return [row(sc) for sc in data["results"]]
    return []


def record_response(
    data: dict, block: dict, ts: Optional[float] = None, path: Optional[str] = None
) -> Optional[int]:
    """Persist one tool response. Returns the run id, or None when skipped."""
    tool = str(block.get("tool", ""))
    kind = _kind_of(tool)
    if kind is None or not enabled():
        return None
    now = ts if ts is not None else time.time()
    target = _target_of(kind, data, block)
    success = data.get("success") is not False and not (
        data.get("error") and data.get("success") is None and "results" not in data
    )
    steps = _collect_steps(data)
    scenarios = _scenario_rows(data)
    map_row = _map_row(tool, data)
    steps_total = data.get("steps_total")
    steps_ok = data.get("steps_succeeded")
    if steps_total is None and steps:
        steps_total, steps_ok = len(steps), sum(1 for s in steps if s["success"])
    extra = {
        k: v
        for k, v in (block.get("effective_params") or {}).items()
        if isinstance(v, (str, int, float, bool)) and len(str(v)) < 120
    }
    conn = connect(path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO runs (ts, tool, kind, target, success, duration_ms, warnings,"
                " steps_total, steps_ok, extra) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    tool,
                    kind,
                    target,
                    int(bool(success)),
                    block.get("duration_ms"),
                    len(block.get("warnings") or []),
                    steps_total,
                    steps_ok,
                    json.dumps(extra, ensure_ascii=False) if extra else None,
                ),
            )
            run_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO steps (run_id, ts, kind, target, idx, action, success,"
                " duration_ms, match_count, healed, pixel, assert_kind, error_type, detail)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        run_id,
                        now,
                        kind,
                        target,
                        s["idx"],
                        s["action"],
                        int(s["success"]),
                        s["duration_ms"],
                        s["match_count"],
                        int(s["healed"]),
                        int(s["pixel"]),
                        s["assert_kind"],
                        s["error_type"],
                        s["detail"],
                    )
                    for s in steps
                ],
            )
            if map_row:
                conn.execute(
                    "INSERT INTO maps (run_id, ts, kind, target, elements, stable_ids,"
                    " identifiers) VALUES (?,?,?,?,?,?,?)",
                    (
                        run_id,
                        now,
                        kind,
                        target,
                        map_row["elements"],
                        map_row["stable_ids"],
                        map_row["identifiers"],
                    ),
                )
            conn.executemany(
                "INSERT INTO scenarios (run_id, ts, kind, target, name, status,"
                " asserts_total, asserts_passed, duration_ms, first_failure_type)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        run_id,
                        now,
                        kind,
                        target,
                        sc["name"],
                        sc["status"],
                        sc["asserts_total"],
                        sc["asserts_passed"],
                        sc["duration_ms"],
                        sc["first_failure_type"],
                    )
                    for sc in scenarios
                ],
            )
        return run_id
    finally:
        conn.close()


def safe_record(data: dict, block: dict) -> None:
    """Hook used by telemetry._wrap — never raises."""
    try:
        record_response(data, block)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("metrics ledger skipped: %s", exc)


# -------------------------------------------------------------------- queries
def fetch(
    table: str,
    since: Optional[float] = None,
    until: Optional[float] = None,
    target: Optional[str] = None,
    kind: Optional[str] = None,
    path: Optional[str] = None,
) -> list[dict]:
    if table not in ("runs", "steps", "maps", "scenarios"):
        raise ValueError(f"unknown table {table}")
    where, args = ["1=1"], []
    if since is not None:
        where.append("ts >= ?")
        args.append(since)
    if until is not None:
        where.append("ts < ?")
        args.append(until)
    if target:
        where.append("target = ?")
        args.append(target)
    if kind:
        where.append("kind = ?")
        args.append(kind)
    conn = connect(path)
    try:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY ts", args
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def targets(since: Optional[float] = None, path: Optional[str] = None) -> list[dict]:
    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT kind, target, COUNT(*) AS runs, SUM(success) AS ok,"
            " MIN(ts) AS first_seen, MAX(ts) AS last_seen"
            " FROM runs WHERE ts >= ? AND target IS NOT NULL"
            " GROUP BY kind, target ORDER BY runs DESC",
            (since or 0,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
