"""Indicators, windows and trends over the ledger.

An indicator is a function over the rows of a window. Each one declares which
direction is "better" so the trend engine can say improving / worsening when
it compares the current window with the one right before it.

Groups (Map First)
  map         how automatable the UI is and how fast it changes
  locators    whether the map still matches the application
  execution   steps and sequences
  tests       scenarios, assertions, flakiness
  speed       durations
"""

from __future__ import annotations

import json
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from polarix.metrics import ledger

MIN_SAMPLE = 5
_RATE_PP = 0.02  # absolute change (percentage points) that counts as movement
_REL = 0.10  # relative change for durations/counts


@dataclass(frozen=True)
class Indicator:
    name: str
    group: str
    better: str  # "higher" | "lower" | "neutral"
    unit: str  # "rate" | "ms" | "count"
    description: str
    compute: Callable[[dict], tuple[Optional[float], int]]  # → (value, sample size)


# -------------------------------------------------------------------- helpers
def parse_window(window: str) -> float:
    """'24h' → seconds; '7d'; '30d'; 'all' → very large."""
    text = (window or "7d").strip().lower()
    if text == "all":
        return 3650 * 86400.0
    hit = re.fullmatch(r"(\d+)\s*([hdw])", text)
    if not hit:
        raise ValueError("window must look like 24h, 7d, 4w or all")
    n, unit = int(hit.group(1)), hit.group(2)
    return n * {"h": 3600, "d": 86400, "w": 7 * 86400}[unit]


def _rate(num: int, den: int) -> tuple[Optional[float], int]:
    return (round(num / den, 4) if den else None), den


def _pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round((p / 100) * (len(ordered) - 1))))
    return float(ordered[k])


def _with_locator(steps: list[dict]) -> list[dict]:
    return [s for s in steps if s.get("match_count") is not None]


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def map_drifts(maps: list[dict]) -> list[dict]:
    """Consecutive-map drift per target: 1 - jaccard(identifiers)."""
    by_target: dict = defaultdict(list)
    for m in maps:
        by_target[(m.get("kind"), m.get("target"))].append(m)
    out: list[dict] = []
    for (kind, target), rows in by_target.items():
        rows.sort(key=lambda r: r["ts"])
        for prev, cur in zip(rows, rows[1:], strict=False):
            a = set(json.loads(prev.get("identifiers") or "[]"))
            b = set(json.loads(cur.get("identifiers") or "[]"))
            out.append(
                {
                    "kind": kind,
                    "target": target,
                    "ts": cur["ts"],
                    "drift": round(1 - jaccard(a, b), 4),
                    "lost": sorted(a - b)[:25],
                    "new": sorted(b - a)[:25],
                }
            )
    return out


def flaky_names(scenarios: list[dict]) -> list[str]:
    """Scenario names whose status flipped inside the window."""
    statuses: dict = defaultdict(set)
    for sc in scenarios:
        if sc.get("name") and sc.get("status") in ("passed", "failed"):
            statuses[sc["name"]].add(sc["status"])
    return sorted(n for n, s in statuses.items() if len(s) > 1)


# ------------------------------------------------------------------ computers
def _step_success(rows):
    steps = rows["steps"]
    return _rate(sum(1 for s in steps if s["success"]), len(steps))


def _locator_hit(rows):
    loc = _with_locator(rows["steps"])
    return _rate(sum(1 for s in loc if (s["match_count"] or 0) >= 1), len(loc))


def _broken_locator(rows):
    loc = _with_locator(rows["steps"])
    return _rate(sum(1 for s in loc if (s["match_count"] or 0) == 0), len(loc))


def _ambiguous_locator(rows):
    loc = _with_locator(rows["steps"])
    return _rate(sum(1 for s in loc if (s["match_count"] or 0) > 1), len(loc))


def _healing(rows):
    loc = _with_locator(rows["steps"])
    return _rate(sum(1 for s in loc if s["healed"]), len(loc))


def _pixel_fallback(rows):
    steps = [s for s in rows["steps"] if s["action"] in _INTERACTIVE]
    return _rate(sum(1 for s in steps if s["pixel"]), len(steps))


def _sequence_success(rows):
    runs = [r for r in rows["runs"] if r.get("steps_total")]
    return _rate(sum(1 for r in runs if r["steps_ok"] == r["steps_total"]), len(runs))


def _tool_failure(rows):
    runs = rows["runs"]
    return _rate(sum(1 for r in runs if not r["success"]), len(runs))


def _scenario_pass(rows):
    sc = rows["scenarios"]
    return _rate(sum(1 for s in sc if s["status"] == "passed"), len(sc))


def _assertion_pass(rows):
    sc = rows["scenarios"]
    total = sum(s.get("asserts_total") or 0 for s in sc)
    return _rate(sum(s.get("asserts_passed") or 0 for s in sc), total)


def _flakiness(rows):
    sc = rows["scenarios"]
    names = {s["name"] for s in sc if s.get("name")}
    return _rate(len(flaky_names(sc)), len(names))


def _first_failure_depth(rows):
    """How far a failing sequence gets before breaking (0..1, higher = later)."""
    depths: list[float] = []
    by_run: dict = defaultdict(list)
    for s in rows["steps"]:
        by_run[s["run_id"]].append(s)
    for steps in by_run.values():
        steps.sort(key=lambda s: s.get("idx") or 0)
        failed = [s for s in steps if not s["success"]]
        if failed and len(steps) > 1:
            depths.append(((failed[0].get("idx") or 1) - 1) / (len(steps) - 1))
    return (round(statistics.fmean(depths), 4) if depths else None), len(depths)


def _step_p50(rows):
    d = [s["duration_ms"] for s in rows["steps"] if s.get("duration_ms") is not None]
    return _pct(d, 50), len(d)


def _step_p95(rows):
    d = [s["duration_ms"] for s in rows["steps"] if s.get("duration_ms") is not None]
    return _pct(d, 95), len(d)


def _tool_p50(rows):
    d = [r["duration_ms"] for r in rows["runs"] if r.get("duration_ms") is not None]
    return _pct(d, 50), len(d)


def _wait_share(rows):
    steps = [s for s in rows["steps"] if s.get("duration_ms") is not None]
    total = sum(s["duration_ms"] for s in steps)
    waiting = sum(s["duration_ms"] for s in steps if s["action"] in _WAITS)
    return (round(waiting / total, 4) if total else None), len(steps)


def _map_coverage(rows):
    maps = rows["maps"]
    elements = sum(m.get("elements") or 0 for m in maps)
    return _rate(sum(m.get("stable_ids") or 0 for m in maps), elements)


def _map_drift(rows):
    drifts = [d["drift"] for d in map_drifts(rows["maps"])]
    return (round(statistics.fmean(drifts), 4) if drifts else None), len(drifts)


def _runs(rows):
    return float(len(rows["runs"])), len(rows["runs"])


_INTERACTIVE = {
    "click",
    "double_click",
    "right_click",
    "click_image",
    "click_vision",
    "fill",
    "set_text",
    "select",
    "hover",
}
_WAITS = {"wait_for", "wait_idle"}

INDICATORS: dict[str, Indicator] = {
    i.name: i
    for i in [
        Indicator(
            "map_stable_id_coverage",
            "map",
            "higher",
            "rate",
            "Share of mapped elements with a stable identifier (data-qa / auto_id). "
            "The automatable surface of the application.",
            _map_coverage,
        ),
        Indicator(
            "map_drift_rate",
            "map",
            "lower",
            "rate",
            "How much the identifier set changed between consecutive maps of the same "
            "target (1 - Jaccard). Rising drift means the application is changing "
            "under your scripts.",
            _map_drift,
        ),
        Indicator(
            "locator_hit_rate",
            "locators",
            "higher",
            "rate",
            "Locator steps that matched at least one element.",
            _locator_hit,
        ),
        Indicator(
            "broken_locator_rate",
            "locators",
            "lower",
            "rate",
            "Locator steps that matched nothing — the map no longer fits.",
            _broken_locator,
        ),
        Indicator(
            "ambiguous_locator_rate",
            "locators",
            "lower",
            "rate",
            "Locator steps that matched more than one element (first was used).",
            _ambiguous_locator,
        ),
        Indicator(
            "healing_rate",
            "locators",
            "lower",
            "rate",
            "Locator steps that only worked after healing. Rising healing is script "
            "debt: update the scripts with the healed locators.",
            _healing,
        ),
        Indicator(
            "pixel_fallback_rate",
            "locators",
            "lower",
            "rate",
            "Interactive steps that had to use image/vision instead of the tree.",
            _pixel_fallback,
        ),
        Indicator(
            "step_success_rate",
            "execution",
            "higher",
            "rate",
            "Executed steps that succeeded.",
            _step_success,
        ),
        Indicator(
            "sequence_success_rate",
            "execution",
            "higher",
            "rate",
            "Sequences where every step succeeded.",
            _sequence_success,
        ),
        Indicator(
            "tool_failure_rate",
            "execution",
            "lower",
            "rate",
            "Tool calls that returned an error envelope.",
            _tool_failure,
        ),
        Indicator(
            "first_failure_depth",
            "execution",
            "higher",
            "rate",
            "How far failing sequences get before the first failure (0 = first step, "
            "1 = last). Later failures mean the early path is solid.",
            _first_failure_depth,
        ),
        Indicator(
            "scenario_pass_rate",
            "tests",
            "higher",
            "rate",
            "Scenarios that passed.",
            _scenario_pass,
        ),
        Indicator(
            "assertion_pass_rate",
            "tests",
            "higher",
            "rate",
            "Assertions that passed across scenarios.",
            _assertion_pass,
        ),
        Indicator(
            "flakiness_rate",
            "tests",
            "lower",
            "rate",
            "Scenarios that both passed and failed inside the window.",
            _flakiness,
        ),
        Indicator(
            "step_duration_p50_ms",
            "speed",
            "lower",
            "ms",
            "Median step duration.",
            _step_p50,
        ),
        Indicator(
            "step_duration_p95_ms",
            "speed",
            "lower",
            "ms",
            "95th percentile step duration — the slow tail.",
            _step_p95,
        ),
        Indicator(
            "tool_duration_p50_ms",
            "speed",
            "lower",
            "ms",
            "Median tool call duration.",
            _tool_p50,
        ),
        Indicator(
            "wait_share",
            "speed",
            "lower",
            "rate",
            "Share of step time spent in explicit waits.",
            _wait_share,
        ),
        Indicator(
            "runs", "volume", "neutral", "count", "Tool calls in the window.", _runs
        ),
    ]
}

HEALTH_WEIGHTS = {
    "step_success_rate": 0.25,
    "scenario_pass_rate": 0.25,
    "locator_hit_rate": 0.15,
    "assertion_pass_rate": 0.15,
    "healing_rate": 0.10,  # inverted
    "flakiness_rate": 0.10,  # inverted
}


# ---------------------------------------------------------------------- engine
def load_rows(
    since: float,
    until: float,
    target: Optional[str] = None,
    kind: Optional[str] = None,
    path: Optional[str] = None,
) -> dict:
    return {
        table: ledger.fetch(table, since, until, target, kind, path)
        for table in ("runs", "steps", "maps", "scenarios")
    }


def compute_all(rows: dict) -> dict[str, dict]:
    out = {}
    for name, ind in INDICATORS.items():
        value, n = ind.compute(rows)
        out[name] = {"value": value, "n": n}
    return out


def direction(
    ind: Indicator, now: Optional[float], prev: Optional[float], n_now: int, n_prev: int
) -> tuple[str, Optional[float]]:
    if now is None or prev is None or n_now < MIN_SAMPLE or n_prev < MIN_SAMPLE:
        return "insufficient", (
            None if now is None or prev is None else round(now - prev, 4)
        )
    delta = now - prev
    if ind.unit == "rate":
        moved = abs(delta) >= _RATE_PP
    else:
        moved = prev != 0 and abs(delta) / abs(prev) >= _REL
    if not moved:
        return "stable", round(delta, 4)
    if ind.better == "neutral":
        return "changed", round(delta, 4)
    good = delta > 0 if ind.better == "higher" else delta < 0
    return ("improving" if good else "worsening"), round(delta, 4)


def health_score(values: dict[str, dict]) -> Optional[float]:
    total_w, acc = 0.0, 0.0
    for name, w in HEALTH_WEIGHTS.items():
        v = values.get(name, {}).get("value")
        if v is None:
            continue
        score = 1 - v if INDICATORS[name].better == "lower" else v
        acc += w * max(0.0, min(1.0, score))
        total_w += w
    return round(100 * acc / total_w, 1) if total_w else None


def summary(
    window: str = "7d",
    target: Optional[str] = None,
    kind: Optional[str] = None,
    now: Optional[float] = None,
    path: Optional[str] = None,
) -> dict:
    span = parse_window(window)
    end = now if now is not None else time.time()
    rows_now = load_rows(end - span, end, target, kind, path)
    rows_prev = load_rows(end - 2 * span, end - span, target, kind, path)
    cur, prev = compute_all(rows_now), compute_all(rows_prev)
    indicators = {}
    for name, ind in INDICATORS.items():
        status, delta = direction(
            ind,
            cur[name]["value"],
            prev[name]["value"],
            cur[name]["n"],
            prev[name]["n"],
        )
        indicators[name] = {
            "group": ind.group,
            "better": ind.better,
            "unit": ind.unit,
            "value": cur[name]["value"],
            "previous": prev[name]["value"],
            "delta": delta,
            "n": cur[name]["n"],
            "status": status,
        }
    score_now, score_prev = health_score(cur), health_score(prev)
    regressions = sorted(
        (n for n, i in indicators.items() if i["status"] == "worsening"),
        key=lambda n: -abs(indicators[n]["delta"] or 0),
    )
    improvements = sorted(
        (n for n, i in indicators.items() if i["status"] == "improving"),
        key=lambda n: -abs(indicators[n]["delta"] or 0),
    )
    errors = Counter(s["error_type"] for s in rows_now["steps"] if s.get("error_type"))
    return {
        "window": window,
        "target": target,
        "kind": kind,
        "period": {"from": end - span, "to": end, "previous_from": end - 2 * span},
        "health": {
            "score": score_now,
            "previous": score_prev,
            "delta": (
                round(score_now - score_prev, 1)
                if score_now is not None and score_prev is not None
                else None
            ),
        },
        "indicators": indicators,
        "regressions": regressions,
        "improvements": improvements,
        "counts": {
            "runs": len(rows_now["runs"]),
            "steps": len(rows_now["steps"]),
            "maps": len(rows_now["maps"]),
            "scenarios": len(rows_now["scenarios"]),
        },
        "error_taxonomy": dict(errors.most_common(10)),
        "flaky_scenarios": flaky_names(rows_now["scenarios"]),
    }


def trend(
    indicator: str,
    window: str = "30d",
    bucket: str = "1d",
    target: Optional[str] = None,
    kind: Optional[str] = None,
    now: Optional[float] = None,
    path: Optional[str] = None,
) -> dict:
    if indicator not in INDICATORS:
        raise ValueError(
            f"unknown indicator '{indicator}'. Known: {sorted(INDICATORS)}"
        )
    span, step = parse_window(window), parse_window(bucket)
    end = now if now is not None else time.time()
    rows = load_rows(end - span, end, target, kind, path)
    ind = INDICATORS[indicator]
    points = []
    start = end - span
    t = start
    while t < end:
        t_next = min(t + step, end)
        slice_rows = {
            table: [r for r in rows[table] if t <= r["ts"] < t_next] for table in rows
        }
        value, n = ind.compute(slice_rows)
        points.append({"from": t, "to": t_next, "value": value, "n": n})
        t = t_next
    values = [p["value"] for p in points if p["value"] is not None]
    return {
        "indicator": indicator,
        "group": ind.group,
        "better": ind.better,
        "unit": ind.unit,
        "description": ind.description,
        "window": window,
        "bucket": bucket,
        "points": points,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "last": values[-1] if values else None,
    }


def error_report(
    window: str = "7d",
    target: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 10,
    now: Optional[float] = None,
    path: Optional[str] = None,
) -> dict:
    span = parse_window(window)
    end = now if now is not None else time.time()
    rows = load_rows(end - span, end, target, kind, path)
    failed = [s for s in rows["steps"] if not s["success"]]
    taxonomy = Counter(s.get("error_type") or "Error" for s in failed)
    by_action = Counter(s["action"] for s in failed)
    top_failures = Counter(
        (s["action"], s.get("error_type") or "Error", (s.get("detail") or "")[:120])
        for s in failed
    )
    healed = Counter(
        (s["action"], s.get("detail") or "") for s in rows["steps"] if s["healed"]
    )
    drifts = sorted(map_drifts(rows["maps"]), key=lambda d: -d["drift"])[:limit]
    return {
        "window": window,
        "target": target,
        "failed_steps": len(failed),
        "error_taxonomy": dict(taxonomy.most_common(limit)),
        "failures_by_action": dict(by_action.most_common(limit)),
        "top_failures": [
            {"action": a, "error_type": e, "detail": d, "count": c}
            for (a, e, d), c in top_failures.most_common(limit)
        ],
        "healed_locators": [
            {"action": a, "healed_to": d, "count": c}
            for (a, d), c in healed.most_common(limit)
        ],
        "map_drift": drifts,
        "flaky_scenarios": flaky_names(rows["scenarios"]),
    }


def definitions() -> list[dict]:
    return [
        {
            "name": i.name,
            "group": i.group,
            "better": i.better,
            "unit": i.unit,
            "description": i.description,
            "in_health_score": i.name in HEALTH_WEIGHTS,
        }
        for i in INDICATORS.values()
    ]
