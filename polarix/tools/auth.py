"""Authentication tools — login, session_save, session_check, session_list."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

from playwright.async_api import async_playwright

from polarix.browser import (
    _attach_console_listener,
    _browser_state,
    _fetch_title,
    _make_context,
    _page_perf,
)
from polarix.config import HEADLESS, SESSIONS_DIR
from polarix.server import mcp
from polarix.telemetry import _polarix, _start, _wrap


@mcp.tool()
async def browser_login(
    login_url: str,
    username_value: str,
    password_value: str,
    username_selector: str = "input[type=email],input[name=username],#username",
    password_selector: str = "input[type=password],input[name=password],#password",
    submit_selector: str = "input[type=submit],button[type=submit],#kc-login",
    session_file: str = "/tmp/polarix_session.json",
    wait_after_login: float = 5.0,
) -> str:
    """Log in to a site via Playwright and save the session for later use.

    Recommended for OAuth/SSO sites where redirect flows make login tricky.
    After logging in, pass session_file to any tool that accepts it.

    For reusable named sessions prefer browser_session_save instead.

    Args:
        login_url: URL of the login page.
        username_value: Username or email to enter.
        password_value: Password to enter.
        username_selector: CSS selector(s) for the username field (comma = candidates).
        password_selector: CSS selector(s) for the password field.
        submit_selector: CSS selector(s) for the submit button.
        session_file: Path to save cookies and storage state.
        wait_after_login: Seconds to wait after submit before saving the session.

    Returns:
        JSON with final_url, title, session_file, and _polarix metadata.
    """
    t0 = _start()
    warnings: list[str] = []
    used_username_sel = username_selector.split(",")[0]
    used_password_sel = password_selector.split(",")[0]
    used_submit_sel = submit_selector.split(",")[0]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        await page.goto(login_url, wait_until="networkidle", timeout=30_000)

        matched_user = False
        for sel in username_selector.split(","):
            try:
                await page.fill(sel.strip(), username_value, timeout=3_000)
                used_username_sel = sel.strip()
                matched_user = True
                break
            except Exception:
                continue
        if not matched_user:
            warnings.append("No username selector matched — login may have failed")

        for sel in password_selector.split(","):
            try:
                await page.fill(sel.strip(), password_value, timeout=3_000)
                used_password_sel = sel.strip()
                break
            except Exception:
                continue

        for sel in submit_selector.split(","):
            try:
                await page.click(sel.strip(), timeout=3_000)
                used_submit_sel = sel.strip()
                break
            except Exception:
                continue

        await page.wait_for_load_state("networkidle", timeout=30_000)
        await asyncio.sleep(wait_after_login)

        perf = await _page_perf(page)
        title = await _fetch_title(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = title

        if bstate["redirect_detected"]:
            warnings.append(
                "Final URL contains auth pattern — login may not have completed"
            )

        await ctx.storage_state(path=session_file)
        await browser.close()

    return _wrap(
        {
            "session_file": session_file,
            "final_url": bstate["final_url"],
            "title": title,
        },
        _polarix(
            "browser_login",
            t0,
            browser=bstate,
            params={
                "username_selector": used_username_sel,
                "password_selector": used_password_sel,
                "submit_selector": used_submit_sel,
                "session_file": session_file,
                "wait_after_login": wait_after_login,
            },
            warnings=warnings or None,
        ),
    )


@mcp.tool()
async def browser_session_save(
    name: str,
    login_url: str,
    username_value: str,
    password_value: str,
    username_selector: str = "input[type=email],input[name=username],#username",
    password_selector: str = "input[type=password],input[name=password],#password",
    submit_selector: str = "input[type=submit],button[type=submit],#kc-login",
    wait_after_login: float = 5.0,
) -> str:
    """Log in and save the session under a friendly name for later reuse.

    Creates two files in POLARIX_SESSIONS_DIR (default: /tmp/polarix_sessions):
    • {name}.json       — Playwright storage state (cookies + localStorage)
    • {name}.meta.json  — metadata (username, login URL, timestamps)

    Pass the session file path from browser_session_list to any tool that
    accepts session_file.

    Args:
        name: Friendly session name (e.g. "admin", "qa-user", "read-only").
        login_url: URL of the login page.
        username_value: Username or email.
        password_value: Password.
        username_selector / password_selector / submit_selector: CSS selectors.
        wait_after_login: Seconds to wait after submit.

    Returns:
        JSON with name, session_file, login result, and _polarix metadata.
    """
    t0 = _start()
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    session_file = os.path.join(SESSIONS_DIR, f"{name}.json")
    meta_file = os.path.join(SESSIONS_DIR, f"{name}.meta.json")

    login_result_raw = await browser_login(
        login_url=login_url,
        username_value=username_value,
        password_value=password_value,
        username_selector=username_selector,
        password_selector=password_selector,
        submit_selector=submit_selector,
        session_file=session_file,
        wait_after_login=wait_after_login,
    )
    login_data = json.loads(login_result_raw)

    meta = {
        "name": name,
        "login_url": login_url,
        "username": username_value,
        "session_file": session_file,
        "created_at": datetime.now().isoformat(),
        "last_checked": None,
        "last_valid": True,
    }
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return _wrap(
        {
            "name": name,
            "session_file": session_file,
            "login_final_url": login_data.get("final_url"),
            "login_title": login_data.get("title"),
        },
        _polarix(
            "browser_session_save",
            t0,
            params={"name": name, "login_url": login_url, "session_file": session_file},
        ),
    )


@mcp.tool()
async def browser_session_check(
    name: str,
    check_url: str,
    login_redirect_patterns: str = "login,auth,keycloak,signin,sso",
) -> str:
    """Verify that a named session is still active (not expired or redirected to login).

    Navigates to check_url with the saved session. If the final URL contains any
    of the redirect patterns, the session is considered expired.

    Args:
        name: Session name (created by browser_session_save).
        check_url: A protected URL that requires authentication.
        login_redirect_patterns: Comma-separated URL substrings that indicate a
                                 redirect to the login page.

    Returns:
        JSON: { name, valid, final_url, reason, session_file, _polarix }
    """
    t0 = _start()
    session_file = os.path.join(SESSIONS_DIR, f"{name}.json")
    meta_file = os.path.join(SESSIONS_DIR, f"{name}.meta.json")

    if not os.path.exists(session_file):
        return _wrap(
            {
                "name": name,
                "valid": False,
                "reason": f"Session '{name}' not found in {SESSIONS_DIR}",
            },
            _polarix(
                "browser_session_check",
                t0,
                warnings=[f"Session file not found: {session_file}"],
            ),
        )

    patterns = [pt.strip() for pt in login_redirect_patterns.split(",")]

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(check_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        final_url = page.url
        await browser.close()

    redirected = any(pt in final_url for pt in patterns)
    valid = not redirected

    if os.path.exists(meta_file):
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)
        meta["last_checked"] = datetime.now().isoformat()
        meta["last_valid"] = valid
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    return _wrap(
        {
            "name": name,
            "valid": valid,
            "final_url": final_url,
            "session_file": session_file,
            "reason": (
                "Redirected to login — session expired"
                if redirected
                else "Session is active"
            ),
        },
        _polarix(
            "browser_session_check",
            t0,
            browser=bstate,
            params={"name": name, "check_url": check_url},
        ),
    )


@mcp.tool()
def browser_session_list() -> str:
    """List all saved named sessions with their metadata and last-known validity.

    Returns:
        JSON: { sessions, count, _polarix }
    """
    t0 = _start()
    sessions = []

    if os.path.exists(SESSIONS_DIR):
        for fname in sorted(os.listdir(SESSIONS_DIR)):
            if not fname.endswith(".meta.json"):
                continue
            try:
                with open(os.path.join(SESSIONS_DIR, fname), encoding="utf-8") as f:
                    meta = json.load(f)
                meta["session_file_exists"] = os.path.exists(
                    meta.get("session_file", "")
                )
                sessions.append(meta)
            except Exception:
                continue

    return _wrap(
        {"sessions": sessions, "count": len(sessions)},
        _polarix("browser_session_list", t0, params={"sessions_dir": SESSIONS_DIR}),
    )
