"""Dependency-free SVG charts and the HTML metrics dashboard.

SVG renders everywhere a data URL does (chat clients, browsers, reports) and
needs no native libraries on the host or in CI.
"""

from __future__ import annotations

import base64
import html
import time
from typing import Optional

PALETTE = ["#2962ff", "#2e7d32", "#ef6c00", "#c62828", "#6a1b9a", "#00838f"]
STATUS_COLOR = {
    "improving": "#2e7d32",
    "stable": "#607d8b",
    "worsening": "#c62828",
    "insufficient": "#9e9e9e",
    "changed": "#ef6c00",
}
STATUS_ARROW = {
    "improving": "▲",
    "stable": "■",
    "worsening": "▼",
    "insufficient": "·",
    "changed": "◆",
}


def _fmt(value: Optional[float], unit: str) -> str:
    if value is None:
        return "—"
    if unit == "rate":
        return f"{value * 100:.1f}%"
    if unit == "ms":
        return f"{value:,.0f} ms" if value < 10_000 else f"{value / 1000:,.1f} s"
    return f"{value:,.0f}"


def to_data_url(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def line_chart(
    series: dict[str, list[Optional[float]]],
    labels: list[str],
    title: str,
    unit: str = "rate",
    width: int = 720,
    height: int = 260,
) -> str:
    """Multi-series line chart. None values leave gaps."""
    pad_l, pad_r, pad_t, pad_b = 56, 16, 34, 40
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    values = [v for s in series.values() for v in s if v is not None]
    if unit == "rate":
        lo, hi = 0.0, 1.0
    else:
        lo = 0.0
        hi = max(values) * 1.1 if values else 1.0
    span = (hi - lo) or 1.0
    n = max(len(labels), 1)

    def x(i: int) -> float:
        return pad_l + (w * i / (n - 1) if n > 1 else w / 2)

    def y(v: float) -> float:
        return pad_t + h - (v - lo) / span * h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, Arial" font-size="11">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{pad_l}" y="20" font-size="14" font-weight="600">{html.escape(title)}</text>',
    ]
    for k in range(5):
        v = lo + span * k / 4
        yy = y(v)
        parts.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" '
            f'stroke="#e0e0e0"/>'
        )
        parts.append(
            f'<text x="{pad_l - 6}" y="{yy + 4:.1f}" text-anchor="end" fill="#666">'
            f"{html.escape(_fmt(v, unit))}</text>"
        )
    stride = max(1, n // 8)
    for i, lab in enumerate(labels):
        if i % stride == 0 or i == n - 1:
            parts.append(
                f'<text x="{x(i):.1f}" y="{height - pad_b + 16}" text-anchor="middle" '
                f'fill="#666">{html.escape(lab)}</text>'
            )
    for si, (name, vals) in enumerate(series.items()):
        color = PALETTE[si % len(PALETTE)]
        parts.extend(_series_marks(_segments(vals, x, y), color))
        lx = pad_l + si * 150
        parts.append(
            f'<rect x="{lx}" y="{height - 14}" width="10" height="10" fill="{color}"/>'
            f'<text x="{lx + 14}" y="{height - 5}" fill="#333">{html.escape(name)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _segments(vals: list[Optional[float]], x, y) -> list[list[str]]:
    """Split a series at None values into runs of 'x,y' points."""
    segments: list[list[str]] = []
    seg: list[str] = []
    for i, v in enumerate(vals):
        if v is None:
            if seg:
                segments.append(seg)
                seg = []
            continue
        seg.append(f"{x(i):.1f},{y(v):.1f}")
    if seg:
        segments.append(seg)
    return segments


def _series_marks(segments: list[list[str]], color: str) -> list[str]:
    marks = []
    for s in segments:
        if len(s) == 1:
            cx, cy = s[0].split(",")
            marks.append(f'<circle cx="{cx}" cy="{cy}" r="3" fill="{color}"/>')
        else:
            marks.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="2" '
                f'points="{" ".join(s)}"/>'
            )
    return marks


def bar_chart(
    items: list[tuple[str, float]],
    title: str,
    unit: str = "count",
    width: int = 720,
    height: int = 260,
    color: str = "#2962ff",
) -> str:
    pad_l, pad_r, pad_t, pad_b = 56, 16, 34, 60
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    hi = max((v for _, v in items), default=1.0) or 1.0
    n = max(len(items), 1)
    bw = w / n * 0.7
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, Arial" font-size="11">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{pad_l}" y="20" font-size="14" font-weight="600">{html.escape(title)}</text>',
        f'<line x1="{pad_l}" y1="{pad_t + h}" x2="{width - pad_r}" y2="{pad_t + h}" stroke="#999"/>',
    ]
    for i, (label, value) in enumerate(items):
        bh = h * (value / hi)
        bx = pad_l + w * i / n + (w / n - bw) / 2
        by = pad_t + h - bh
        parts.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{bx + bw / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle" fill="#333">'
            f"{html.escape(_fmt(value, unit))}</text>"
        )
        short = label if len(label) <= 16 else label[:15] + "…"
        parts.append(
            f'<text x="{bx + bw / 2:.1f}" y="{pad_t + h + 16}" text-anchor="middle" fill="#666">'
            f"{html.escape(short)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def sparkline(values: list[Optional[float]], width: int = 120, height: int = 28) -> str:
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"></svg>'
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    coords = []
    for i, v in enumerate(values):
        if v is None:
            continue
        xx = 2 + (width - 4) * i / max(len(values) - 1, 1)
        yy = height - 2 - (v - lo) / span * (height - 4)
        coords.append(f"{xx:.1f},{yy:.1f}")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f'<polyline fill="none" stroke="#2962ff" stroke-width="1.5" points="{" ".join(coords)}"/>'
        "</svg>"
    )


def _bucket_labels(points: list[dict]) -> list[str]:
    labels = []
    for p in points:
        labels.append(time.strftime("%d/%m", time.localtime(p["from"])))
    return labels


def dashboard_html(summary: dict, trends: dict[str, dict], errors: dict) -> str:
    """Self-contained dashboard: health, indicator tiles, trend charts, errors."""
    ind = summary["indicators"]
    health = summary["health"]
    tiles = []
    for name, i in ind.items():
        if name == "runs":
            continue
        color = STATUS_COLOR.get(i["status"], "#999")
        spark = sparkline([p["value"] for p in trends.get(name, {}).get("points", [])])
        delta = (
            ""
            if i["delta"] is None
            else (
                _fmt(i["delta"], i["unit"]).replace("%", " pp")
                if i["unit"] == "rate"
                else _fmt(i["delta"], i["unit"])
            )
        )
        tiles.append(
            f'<div class="tile"><div class="name">{html.escape(name)}</div>'
            f'<div class="val">{html.escape(_fmt(i["value"], i["unit"]))}</div>'
            f'<div class="delta" style="color:{color}">{STATUS_ARROW.get(i["status"], "")} '
            f'{html.escape(i["status"])} {html.escape(delta)} <small>n={i["n"]}</small></div>'
            f'<div class="spark">{spark}</div>'
            f'<div class="group">{html.escape(i["group"])} · better: {html.escape(i["better"])}</div></div>'
        )
    charts = []
    rate_series = {
        k: [p["value"] for p in trends[k]["points"]]
        for k in ("step_success_rate", "scenario_pass_rate", "locator_hit_rate")
        if k in trends
    }
    if rate_series:
        labels = _bucket_labels(next(iter(trends.values()))["points"])
        charts.append(
            line_chart(rate_series, labels, "Success & locator health", "rate")
        )
    debt_series = {
        k: [p["value"] for p in trends[k]["points"]]
        for k in (
            "healing_rate",
            "broken_locator_rate",
            "pixel_fallback_rate",
            "map_drift_rate",
        )
        if k in trends
    }
    if debt_series:
        labels = _bucket_labels(next(iter(trends.values()))["points"])
        charts.append(
            line_chart(debt_series, labels, "Map debt (lower is better)", "rate")
        )
    speed_series = {
        k: [p["value"] for p in trends[k]["points"]]
        for k in ("step_duration_p50_ms", "step_duration_p95_ms")
        if k in trends
    }
    if speed_series:
        labels = _bucket_labels(next(iter(trends.values()))["points"])
        charts.append(line_chart(speed_series, labels, "Step duration", "ms"))
    if errors.get("error_taxonomy"):
        charts.append(
            bar_chart(
                list(errors["error_taxonomy"].items()),
                "Errors by type",
                "count",
                color="#c62828",
            )
        )
    if errors.get("failures_by_action"):
        charts.append(
            bar_chart(
                list(errors["failures_by_action"].items()),
                "Failures by action",
                "count",
                color="#ef6c00",
            )
        )
    failures = "".join(
        f"<tr><td>{html.escape(f['action'])}</td><td>{html.escape(f['error_type'])}</td>"
        f"<td>{html.escape(f['detail'])}</td><td>{f['count']}</td></tr>"
        for f in errors.get("top_failures", [])
    )
    drift = "".join(
        f"<tr><td>{html.escape(str(d['target']))}</td><td>{d['drift'] * 100:.0f}%</td>"
        f"<td>{html.escape(', '.join(d['lost'][:8]))}</td><td>{html.escape(', '.join(d['new'][:8]))}</td></tr>"
        for d in errors.get("map_drift", [])
    )
    hs = "—" if health["score"] is None else f"{health['score']:.0f}"
    hd = (
        ""
        if health["delta"] is None
        else f" ({health['delta']:+.1f} vs previous window)"
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Polarix metrics — {html.escape(summary['window'])}</title>
<style>
body{{font-family:system-ui,Segoe UI,Arial;margin:24px;color:#222;background:#fafafa}}
h1{{margin-bottom:4px}} .sub{{color:#666;margin-top:0}}
.health{{font-size:48px;font-weight:700;margin:12px 0}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;margin:16px 0}}
.tile{{background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:12px}}
.tile .name{{font-size:12px;color:#555}} .tile .val{{font-size:26px;font-weight:600}}
.tile .delta{{font-size:12px;margin:4px 0}} .tile .group{{font-size:11px;color:#888;margin-top:4px}}
.charts{{display:flex;flex-wrap:wrap;gap:16px}} .charts svg{{background:#fff;border:1px solid #e3e3e3;border-radius:10px}}
table{{border-collapse:collapse;width:100%;background:#fff;margin:12px 0}} td,th{{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;font-size:13px;vertical-align:top}}
th{{background:#f3f3f3}}
</style></head><body>
<h1>Polarix metrics</h1>
<p class="sub">window {html.escape(summary['window'])}{' · target ' + html.escape(summary['target']) if summary.get('target') else ''}
 · {summary['counts']['runs']} runs · {summary['counts']['steps']} steps · {summary['counts']['scenarios']} scenarios · {summary['counts']['maps']} maps</p>
<div class="health">Health {hs}<small style="font-size:16px;color:#666">/100{html.escape(hd)}</small></div>
<p><b>Regressions:</b> {html.escape(', '.join(summary['regressions']) or 'none')} &nbsp; <b>Improvements:</b> {html.escape(', '.join(summary['improvements']) or 'none')}</p>
<div class="tiles">{''.join(tiles)}</div>
<div class="charts">{''.join(charts)}</div>
<h2>Top failures</h2>
<table><thead><tr><th>Action</th><th>Error</th><th>Detail</th><th>Count</th></tr></thead><tbody>{failures or '<tr><td colspan=4>none</td></tr>'}</tbody></table>
<h2>Map drift</h2>
<table><thead><tr><th>Target</th><th>Drift</th><th>Identifiers lost</th><th>New</th></tr></thead><tbody>{drift or '<tr><td colspan=4>no consecutive maps</td></tr>'}</tbody></table>
<p class="sub">Flaky scenarios: {html.escape(', '.join(summary['flaky_scenarios']) or 'none')}</p>
</body></html>"""
