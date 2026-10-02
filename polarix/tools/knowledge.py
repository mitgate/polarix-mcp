"""Knowledge tools — map_site, explore_page, intercept_network, accessibility_tree, get_external_resources."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

from polarix.browser import (
    _attach_console_listener,
    _browser_state,
    _exec_actions_code,
    _fetch_title,
    _make_context,
    _page_perf,
)
from polarix.server import mcp
from polarix.snapshot import _selector_index, _snapshot
from polarix.telemetry import _elapsed_ms, _polarix, _start, _wrap

_CATEGORY_HINTS: dict[str, list[str]] = {
    "analytics": [
        "analytics",
        "gtag",
        "google-analytics",
        "segment",
        "mixpanel",
        "hotjar",
        "heap",
        "amplitude",
        "clarity",
        "datadog",
        "newrelic",
    ],
    "cdn": [
        "cdn",
        "cloudfront",
        "fastly",
        "akamai",
        "cloudflare",
        "unpkg",
        "jsdelivr",
        "cdnjs",
        "static",
        "assets",
    ],
    "font": ["font", "typekit", "typography", "fonts.googleapis"],
    "social": [
        "facebook",
        "twitter",
        "linkedin",
        "instagram",
        "tiktok",
        "youtube",
        "whatsapp",
        "pinterest",
    ],
    "ads": [
        "doubleclick",
        "adnxs",
        "adroll",
        "adsystem",
        "advertising",
        "criteo",
        "taboola",
        "outbrain",
    ],
    "api": ["api.", "/api", "graphql", "rest", "backend", "service"],
}


def _categorize_host(hostname: str) -> str:
    h = hostname.lower()
    for cat, hints in _CATEGORY_HINTS.items():
        if any(x in h for x in hints):
            return cat
    return "other"


@mcp.tool()
async def browser_map_site(
    url: str,
    session_file: Optional[str] = None,
    max_pages: int = 10,
    wait_seconds: float = 5.0,
    follow_links: bool = True,
) -> str:
    """Crawl a site via BFS and return a complete structured mental map as JSON.

    For each page the map includes:
    • data_qa_elements — every [data-qa] with tag, count, and visible texts
    • forms — all inputs with id, name, type, placeholder, required
    • navigation_links — internal links discovered (used for BFS)
    • buttons_no_qa — visible button text for elements without data-qa
    • interactive_triggers — elements likely to open dropdowns/modals/tabs

    The top-level selector_index cross-references every [data-qa] attribute
    found across the entire site with total counts and which pages it appears on.

    Call this BEFORE writing any automation on a new site.

    Args:
        url: Entry point URL for the crawl.
        session_file: Session file from browser_login or browser_session_save.
        max_pages: Maximum pages to crawl via BFS (default: 10).
        wait_seconds: Seconds to wait after loading each page (default: 5.0).
        follow_links: If False, maps only the entry URL without following links.

    Returns:
        JSON: { base_url, pages_mapped, pages, selector_index, _polarix }
    """
    t0 = _start()
    parsed = urlparse(url)
    base_origin = f"{parsed.scheme}://{parsed.hostname}"

    visited: set[str] = set()
    queue: list[str] = [url]
    pages: list[dict] = []
    warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        while queue and len(pages) < max_pages:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            try:
                await page.goto(current, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(wait_seconds)

                if urlparse(page.url).hostname != parsed.hostname:
                    warnings.append(
                        f"Redirected off-domain to {page.url} — crawl stopped"
                    )
                    break

                snap = await _snapshot(page)
                pages.append(snap)

                if follow_links:
                    for link in snap.get("navigation_links", []):
                        full = urljoin(base_origin, link)
                        if full not in visited and full not in queue:
                            queue.append(full)

            except Exception as e:
                warnings.append(f"Error mapping {current}: {e}")

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    idx = _selector_index(pages)
    return _wrap(
        {
            "base_url": url,
            "pages_mapped": len(pages),
            "pages": pages,
            "selector_index": idx,
        },
        _polarix(
            "browser_map_site",
            t0,
            browser=bstate,
            params={
                "max_pages": max_pages,
                "follow_links": follow_links,
                "wait_seconds": wait_seconds,
                "session_used": bool(
                    session_file and os.path.exists(session_file or "")
                ),
            },
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def browser_explore_page(
    url: str,
    session_file: Optional[str] = None,
    trigger_interactions: bool = True,
    max_triggers: int = 8,
    wait_seconds: float = 5.0,
) -> str:
    """Deep-inspect a single page, including elements only visible after interactions.

    Takes a static snapshot first, then (if trigger_interactions=True) clicks each
    interactive trigger, records which new [data-qa] elements appear, and closes
    the trigger before testing the next one.

    This reveals dropdowns, context menus, modals, and tab panels that are invisible
    in the static DOM — giving you the complete selector set for a page.

    Args:
        url: Page to inspect.
        session_file: Session file for authenticated sites.
        trigger_interactions: Click triggers to reveal hidden elements (default: True).
        max_triggers: Maximum triggers to test per page (default: 8).
        wait_seconds: Seconds to wait after loading (default: 5.0).

    Returns:
        JSON: { static_elements, revealed_after_interactions, total_revealed_qa, _polarix }
    """
    t0 = _start()
    warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        static = await _snapshot(page)
        static_qa = {el["qa"] for el in static["data_qa_elements"]}
        revealed: list[dict] = []
        triggers_tested = 0

        if trigger_interactions:
            origin_url = page.url
            for trigger in static.get("interactive_triggers", [])[:max_triggers]:
                qa = trigger["qa"]
                ts = _start()
                try:
                    el = page.locator(f'[data-qa="{qa}"]').first
                    if not await el.is_visible(timeout=2_000):
                        continue

                    await el.click(force=True)
                    await asyncio.sleep(1.5)
                    triggers_tested += 1

                    if page.url != origin_url:
                        await page.goto(
                            origin_url, wait_until="domcontentloaded", timeout=20_000
                        )
                        await asyncio.sleep(wait_seconds)
                        continue

                    after = await _snapshot(page)
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
                                "trigger_duration_ms": _elapsed_ms(ts),
                            }
                        )

                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.8)
                except Exception as e:
                    warnings.append(f"Trigger '{qa}' could not be activated: {e}")
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    return _wrap(
        {
            "url": url,
            "path": static["path"],
            "title": static["title"],
            "static_elements": static,
            "revealed_after_interactions": revealed,
            "total_revealed_qa": sum(len(r["new_elements"]) for r in revealed),
        },
        _polarix(
            "browser_explore_page",
            t0,
            browser=bstate,
            params={
                "trigger_interactions": trigger_interactions,
                "triggers_tested": triggers_tested,
                "max_triggers": max_triggers,
            },
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def browser_intercept_network(  # noqa: C901
    url: str,
    session_file: Optional[str] = None,
    actions_code: Optional[str] = None,
    wait_seconds: float = 5.0,
    resource_types: str = "fetch,xhr",
    filter_url_contains: Optional[str] = None,
    max_body_chars: int = 2000,
) -> str:
    """Capture all network requests made during page load and optional actions.

    Intercepts XHR/fetch in real time — revealing which endpoints the frontend
    calls, with full request and response payloads. Use this to automatically
    discover and document the API layer of any web application.

    Args:
        url: Starting URL.
        session_file: Session file for authenticated sites.
        actions_code: Optional async Python code (receives `page`) to execute after
                      the page loads. Captures API calls triggered by actions.
        wait_seconds: Seconds to wait after page load (default: 5.0).
        resource_types: Comma-separated resource types to capture (default: "fetch,xhr").
        filter_url_contains: Only capture requests whose URL contains this string.
        max_body_chars: Truncate request/response bodies at this length (default: 2000).

    Returns:
        JSON: { requests_captured, entries, _polarix }
    """
    t0 = _start()
    target_types = {t.strip() for t in resource_types.split(",")}
    entries: list[dict] = []
    lock = asyncio.Lock()
    warnings: list[str] = []

    async def on_finished(request):
        if request.resource_type not in target_types:
            return
        if filter_url_contains and filter_url_contains not in request.url:
            return

        entry: dict = {
            "method": request.method,
            "url": request.url,
            "resource_type": request.resource_type,
            "request_body": None,
            "status": None,
            "response_body": None,
        }

        try:
            if request.post_data:
                try:
                    entry["request_body"] = json.loads(request.post_data)
                except Exception:
                    entry["request_body"] = request.post_data[:max_body_chars]
        except Exception:
            pass

        try:
            response = await request.response()
            if response:
                entry["status"] = response.status
                try:
                    body = await response.text()
                    try:
                        entry["response_body"] = json.loads(body)
                    except Exception:
                        entry["response_body"] = body[:max_body_chars]
                except Exception:
                    pass
        except Exception:
            pass

        async with lock:
            entries.append(entry)

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        page.on("requestfinished", on_finished)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        if actions_code:
            await _exec_actions_code(page, ctx, actions_code)

        await asyncio.sleep(1.5)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    if not entries:
        warnings.append(
            f"No {resource_types} requests captured — "
            "check resource_types or filter_url_contains"
        )

    return _wrap(
        {"url": url, "requests_captured": len(entries), "entries": entries},
        _polarix(
            "browser_intercept_network",
            t0,
            browser=bstate,
            params={
                "resource_types": resource_types,
                "filter_url_contains": filter_url_contains,
            },
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def browser_accessibility_tree(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 5.0,
    interesting_only: bool = True,
    max_depth: int = 6,
) -> str:
    """Extract the ARIA accessibility tree of a page.

    Works on any site regardless of data-qa conventions. Returns semantic roles
    and names that let you navigate by meaning rather than by CSS selector.
    Particularly useful for sites built with standard HTML semantics or
    accessible component libraries.

    Args:
        url: Page to inspect.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 5.0).
        interesting_only: Filter out nodes with no role or name (default: True).
        max_depth: Maximum tree depth in the flat representation (default: 6).

    Returns:
        JSON: { node_count, flat, tree, _polarix }
    """
    t0 = _start()

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        tree = await page.accessibility.snapshot(interesting_only=interesting_only)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    def flatten(
        node: Optional[dict], depth: int = 0, acc: Optional[list] = None
    ) -> list:
        if acc is None:
            acc = []
        if not node or depth > max_depth:
            return acc
        entry = {
            k: v
            for k, v in {
                "depth": depth,
                "role": node.get("role"),
                "name": node.get("name"),
                "value": node.get("value"),
                "description": node.get("description"),
                "checked": node.get("checked"),
                "expanded": node.get("expanded"),
                "disabled": node.get("disabled"),
            }.items()
            if v is not None and v != ""
        }
        acc.append(entry)
        for child in node.get("children", []):
            flatten(child, depth + 1, acc)
        return acc

    flat = flatten(tree)
    return _wrap(
        {"url": url, "node_count": len(flat), "flat": flat, "tree": tree},
        _polarix(
            "browser_accessibility_tree",
            t0,
            browser=bstate,
            params={"interesting_only": interesting_only, "max_depth": max_depth},
        ),
    )


@mcp.tool()
async def browser_get_external_resources(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 5.0,
    include_requests: bool = True,
) -> str:
    """Collect every external URL, domain, and resource a page loads or links to.

    Combines two sources for maximum coverage:
    1. Static DOM scan — <script src>, <link href>, <img src>, <iframe src>,
       <video src>, <source src>, and <a href> pointing to external origins.
    2. Live network interception — actual HTTP requests made during page load,
       capturing dynamic imports, fetch() calls, XHR, and CDN requests.

    Returns each unique external origin categorized:
      analytics | cdn | api | font | social | ads | other

    Use this to:
    • Find the real API base URL before narrowing browser_intercept_network
    • Audit third-party tracking and privacy exposure
    • Discover CDN origins and static asset hosts
    • Map the full backend surface area of a SPA

    Args:
        url: Page URL to analyze.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 5.0).
        include_requests: Capture live network requests in addition to DOM scan.

    Returns:
        JSON: { external_origin_count, external_origins, all_external_url_count, _polarix }
        external_origins is sorted by request count descending, each entry:
          { hostname, category, count, urls: [{url, kind}] }
    """
    t0 = _start()
    page_hostname = urlparse(url).hostname or ""
    live_requests: list[dict] = []
    lock = asyncio.Lock()

    async def _on_request(req):
        try:
            h = urlparse(req.url).hostname or ""
            if h and h != page_hostname:
                async with lock:
                    live_requests.append(
                        {
                            "url": req.url,
                            "hostname": h,
                            "kind": req.resource_type,
                        }
                    )
        except Exception:
            pass

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        if include_requests:
            page.on("requestfinished", _on_request)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        dom_items: list[dict] = await page.evaluate(
            """
            (pageHostname) => {
                const results = [];
                const add = (src, kind) => {
                    if (!src) return;
                    try {
                        const u = new URL(src, window.location.href);
                        if (u.hostname && u.hostname !== pageHostname) {
                            results.push({ url: u.href, hostname: u.hostname, kind });
                        }
                    } catch(e) {}
                };
                document.querySelectorAll('script[src]').forEach(el => add(el.src, 'script'));
                document.querySelectorAll('link[href]').forEach(el =>
                    add(el.href, el.rel || 'link'));
                document.querySelectorAll('img[src],img[data-src]').forEach(el =>
                    add(el.src || el.dataset.src, 'image'));
                document.querySelectorAll('iframe[src]').forEach(el =>
                    add(el.src, 'iframe'));
                document.querySelectorAll('video[src],source[src]').forEach(el =>
                    add(el.src, 'media'));
                document.querySelectorAll('a[href]').forEach(el => {
                    try {
                        const u = new URL(el.href, window.location.href);
                        if (u.hostname !== pageHostname) add(el.href, 'link');
                    } catch(e) {}
                });
                return results;
            }
        """,
            page_hostname,
        )

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    # Merge DOM items + live requests, deduplicate by URL
    all_items = list(dom_items) + live_requests
    seen: set[str] = set()
    unique: list[dict] = []
    for item in all_items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)

    # Group by hostname
    by_host: dict[str, dict] = {}
    for item in unique:
        h = item["hostname"]
        if h not in by_host:
            by_host[h] = {
                "hostname": h,
                "category": _categorize_host(h),
                "count": 0,
                "urls": [],
            }
        by_host[h]["urls"].append({"url": item["url"], "kind": item.get("kind", "")})
        by_host[h]["count"] += 1

    origins = sorted(by_host.values(), key=lambda x: -x["count"])

    return _wrap(
        {
            "url": url,
            "external_origin_count": len(origins),
            "external_origins": origins,
            "all_external_url_count": len(unique),
        },
        _polarix(
            "browser_get_external_resources",
            t0,
            browser=bstate,
            params={
                "include_requests": include_requests,
                "wait_seconds": wait_seconds,
                "session_used": bool(
                    session_file and os.path.exists(session_file or "")
                ),
            },
        ),
    )
