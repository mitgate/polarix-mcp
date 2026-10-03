"""FastMCP server instance and capability instructions for Polarix MCP."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from polarix.config import MCP_HOST, MCP_PORT

_INSTRUCTIONS = """
You are connected to Polarix MCP — browser AND desktop automation built on the Map First philosophy.

CORE PRINCIPLE: Always map before automating. On the web: browser_map_site → browser_explore_page →
browser_intercept_network. On the desktop: desktop_map_window → desktop_explore_menus. Only after
that should you write automation. Tools are prefixed by target: browser_* (Playwright, this host),
desktop_* (native windows in a guest VM via the Polarix agent, or on this host when it is Windows),
vm_* (hypervisor control of the guest: power, snapshots, screenshots, raw keys).

════════════════════════════════════════════════════════════════
 LAYER 1 — KNOWLEDGE  (always start here)
════════════════════════════════════════════════════════════════

browser_map_site(url, session_file, max_pages, wait_seconds, follow_links)
  BFS crawl of the entire site. Returns a JSON map with:
  • data_qa_elements per page — tag, count, visible texts
  • forms and inputs — id, name, type, placeholder
  • internal navigation links discovered
  • interactive_triggers (buttons, tabs, dropdowns)
  • selector_index — cross-page index of all [data-qa] attributes
  USE: Before writing any automation on a new site or page.

browser_explore_page(url, session_file, trigger_interactions, max_triggers, wait_seconds)
  Deep inspection of a single page. Clicks each interactive trigger and records
  which new [data-qa] elements appear — revealing dropdowns, modals, tabs, and
  context menus that are invisible in the static snapshot.
  USE: When map_site shows a trigger but you need to know what it reveals.

browser_intercept_network(url, session_file, actions_code, resource_types, filter_url_contains)
  Captures all XHR/fetch requests during page load and optional actions.
  Returns: method, URL, status, request body, response body (truncated).
  USE: To discover API endpoints the frontend calls — map the API layer automatically.

browser_accessibility_tree(url, session_file, interesting_only, max_depth)
  Extracts the full ARIA accessibility tree. Works on any site regardless of
  data-qa conventions. Returns flat + nested tree with roles and names.
  USE: On sites without data-qa, or to navigate by semantic meaning.

browser_get_external_resources(url, session_file, wait_seconds, include_requests)
  Collects every external origin a page contacts — via DOM scan (<script>,
  <link>, <img>, <iframe>, <a href>) and live network interception.
  Returns external_origins grouped by hostname with a category label
  (analytics, cdn, api, font, social, ads, other) and full URL list.
  USE: Discover the real API base URL, audit third-party tracking, or map
  the backend before using browser_intercept_network.

════════════════════════════════════════════════════════════════
 LAYER 2 — EXECUTION  (after you know the selectors)
════════════════════════════════════════════════════════════════

browser_inject_js(code, url, session_file, persistent, wait_seconds)
  Inject and execute arbitrary JavaScript in the page context. Returns the
  result (must be JSON-serializable). With persistent=True the script is
  re-executed on every navigation within the session (addInitScript) —
  useful for interceptors, helpers, or globals that survive SPA route changes.
  USE: Extract data buried in JS state, reconstruct split auth tokens from
  cookies, override window functions, set up event listeners.

browser_run_playwright(code, session_file, start_url, timeout_seconds)
  Executes Python Playwright code directly — no LLM in the loop.
  The code receives `page`, `context`, `asyncio`. Use `return {...}` to return data.
  USE: For precise, deterministic automation with selectors from the map.

browser_execute_sequence(steps_json, session_file, start_url, stop_on_error)
  Runs a typed JSON action sequence. Each step: {action, ...params}.
  Actions: goto · click · fill · select · press · hover · scroll ·
           wait_for · snapshot · screenshot · evaluate
  USE: Safer than run_playwright for predictable linear flows.

browser_auto_sequence(goal, url, session_file, model, explore, dry_run)
  Map First em uma única chamada: mapeia a página, explora triggers escondidos,
  gera automaticamente uma sequência de steps via LLM usando o mapa como contexto,
  e executa.
  Diferente de browser_run_task (LLM navega cegamente): aqui o LLM vê o mapa
  completo ANTES de planejar — gera a sequência toda de uma vez e executa de
  forma determinística.
  Com dry_run=True retorna apenas os steps gerados sem executar.
  USE: When you know the goal but not the selectors — Polarix figures out the how.
  ADVANTAGE OVER browser_run_task: the LLM sees the full selector map before
  planning, not just the current DOM; generates the entire sequence at once.

browser_run_task(task, start_url, model, max_steps, sensitive_data, session_file)
  LLM-driven automation in natural language (browser-use agent).
  USE: Fallback for unstructured exploration when selectors are not yet known.

════════════════════════════════════════════════════════════════
 LAYER 3 — VERIFICATION  (confirm what happened)
════════════════════════════════════════════════════════════════

browser_diff_pages(url_a, url_b, actions_code, session_file, wait_seconds)
  Structural diff between two page states.
  Returns: added_qa · removed_qa · changed_texts · changed_counts.
  Modes: compare two URLs, or before/after an action on the same URL.
  USE: To assert that an action produced the expected UI change.

browser_capture_console(url, session_file, actions_code, levels)
  Captures browser console output (log, warn, error, pageerror) during navigation.
  USE: To diagnose silent frontend failures — errors always appear here first.

browser_get_storage(url, session_file, wait_seconds)
  Reads localStorage, sessionStorage, and cookies for a URL.
  USE: To inspect auth tokens, SPA state, cached preferences.

════════════════════════════════════════════════════════════════
 AUTHENTICATION
════════════════════════════════════════════════════════════════

browser_login(login_url, username_value, password_value, session_file, ...)
  One-off login via Playwright. Saves session to a file path you specify.

browser_session_save(name, login_url, username_value, password_value, ...)
  Named login — saves session to POLARIX_SESSIONS_DIR/{name}.json.
  Preferred over browser_login for reusable sessions.

browser_session_check(name, check_url, login_redirect_patterns)
  Verifies a named session is still active (not expired or redirected to login).
  Call before using a session that may have aged out.

browser_session_list()
  Lists all saved named sessions with metadata and last-valid status.

════════════════════════════════════════════════════════════════
 UTILITIES
════════════════════════════════════════════════════════════════

browser_screenshot(url, session_file, wait_seconds, full_page)
  Returns a base64 PNG screenshot wrapped in JSON.

browser_get_page_content(url, session_file, wait_seconds)
  Returns visible page text (no HTML), truncated at 20,000 characters.

browser_get_help()
  Returns this documentation as a string.

════════════════════════════════════════════════════════════════
 DESKTOP — native application windows (guest VM or local Windows)
════════════════════════════════════════════════════════════════

Choosing WHERE a sequence runs (which machine / operating system):
  Every desktop_* tool takes `target`, a name from desktop_targets(). Built-ins: `fake`
  (simulated editor, any OS) and `local` (this host, Windows only). Register VMs in
  POLARIX_TARGETS_FILE (~/.config/polarix/targets.json) or POLARIX_TARGETS (inline JSON):
    {"win11": {"driver": "remote", "agent_url": "http://192.168.122.15:8020", "token": "...",
               "os": "windows", "vm": {"backend": "libvirt", "name": "win11-lab", "snapshot": "clean"}}}
  Scenarios take `target: "win11"` or `targets: ["win11", "win10"]` (one run per target).
  POLARIX_TARGET sets the default; without any target the process-wide driver applies:
  POLARIX_DESKTOP_DRIVER=remote + POLARIX_DESKTOP_AGENT_URL=http://<guest-ip>:8020
      → drives a Polarix agent running inside the VM (python -m polarix.desktop.agent)
  POLARIX_DESKTOP_DRIVER=auto   → pywinauto, when Polarix itself runs on Windows
  POLARIX_DESKTOP_DRIVER=fake   → simulated Notepad-like app, any OS (dev/tests)
  POLARIX_DESKTOP_DRIVER=android + POLARIX_ANDROID_SERIAL=emulator-5554
      → an Android device/emulator through adb + uiautomator (no agent on the device);
        target config {"driver": "android", "serial": "..."}. Window = foreground activity
        ({"process": "com.example.app"}); launch takes a package name; menu does not exist;
        shell runs on the device.
  Guest drivers today: Windows (pywinauto, via agent) and Android (adb). iPhone needs a
  macOS machine with Appium/XCUITest — not available yet. Linux/macOS guests: same agent
  contract, driver not written.

Window locator (every desktop_* tool): JSON {"title_re": ".*CadApp.*"} | {"process": "x.exe"} |
{"handle": 123} | {"title": "..."} — or a plain title substring.
Control locator (steps): {"auto_id": "1001"} | {"title": "Salvar", "control_type": "Button"} |
{"path": "0/2/1"} | {"title_re": "..."} | + "found_index" when ambiguous.

KNOWLEDGE
  desktop_list_windows(title_filter)        top-level windows: title, process, pid, handle
  desktop_map_window(window_json, max_depth, include_menus)
      UI Automation control tree + control_index grouped by type + menu bar. The desktop "site map".
  desktop_explore_menus(window_json, max_items)
      Opens every top-level menu and returns the items it reveals as "Top->Item" paths.
  desktop_map_app(window_json, max_depth, max_windows, include_tabs, allow_destructive)
      The whole application, like browser_map_site: opens every menu item, "…" button and
      tab, maps each dialog that appears and closes it (Cancel/Escape, never OK). Returns
      windows, edges (how each dialog is reached), feature_index and coverage. Exit and
      destructive items are skipped. USE: first contact with an unknown application.

EXECUTION
  desktop_launch(path, args, title_re, wait_seconds)   start the app, returns window locator
  desktop_run_command(command, cwd, timeout)           shell command on the target (winget install,
                                                       fixtures, cleanup) → exit_code, stdout, stderr
  desktop_execute_sequence(steps_json, window_json, stop_on_error)
      Typed steps: launch · focus · click · double_click · right_click · set_text · type · press ·
      select · menu · wait_for(locator|window|image|seconds) · wait_idle · assert · shell ·
      snapshot · screenshot · close.
      click without locator takes {x, y} relative to the window — canvas fallback only.
  desktop_auto_sequence(goal, window_json, model, explore, dry_run)
      Map First in one call: map → explore menus → LLM plans from the control_index → execute.

VERIFICATION
  desktop_diff_window(window_json, steps_json)   control-tree diff before/after + windows opened/closed
  desktop_screenshot(window_json)                base64 PNG of a window

MACROS (record once, replay deterministically)
  desktop_record_start(name, window_json, stop_key)   records real mouse/keyboard → steps (pynput)
  desktop_record_stop(name, save)                     returns the steps, saved to POLARIX_MACROS_DIR
  desktop_macro_save(name, steps_json, window_json) · desktop_macro_list() ·
  desktop_macro_run(name, window_json, stop_on_error) · desktop_macro_delete(name)

Per-step telemetry: { step, action, success, duration_ms, locator_match_count, result, error }
  locator_match_count = 0 → control not found (re-map the window)
  locator_match_count > 1 → ambiguous locator, first was used (add found_index or auto_id)

════════════════════════════════════════════════════════════════
 VM — hypervisor control of the guest (libvirt/virsh, VirtualBox, Android adb)
════════════════════════════════════════════════════════════════

  vm_backends()                              which CLIs exist on this host
  vm_list(backend) · vm_start(vm) · vm_stop(vm, force)
  vm_snapshot_list(vm) · vm_snapshot_save(vm, name) · vm_snapshot_restore(vm, name)
      Restore a known snapshot before every test run — repeatability comes from here.
  vm_screenshot(vm)                          guest display via the hypervisor (no agent needed)
  vm_send_keys(vm, keys_json)                raw keystrokes via the hypervisor (no agent needed)
  vm_tap(vm, x, y)                           Android emulator tap
  vm_guest_ip(vm)                            IPv4 of the guest → POLARIX_DESKTOP_AGENT_URL
  vm_agent_check(agent_url, token)           GET /health on the Polarix agent inside the guest

TESTING — scenarios, assertions, KPIs
  desktop_run_scenario(scenario, restore_vm, save)
      One scenario = steps + {"action": "assert", "kind": ...}. Kinds: control_exists ·
      control_absent · control_enabled · control_disabled · text_equals · text_contains ·
      window_exists · window_absent · window_title_contains · image_present · image_absent ·
      vision (expectation judged by a multimodal model). Writes JSON + HTML report.
  desktop_run_suite(source, name, tags_json, restore_vm)
      Directory/file/inline list of scenarios → KPIs: pass_rate, assertion_pass_rate,
      duration, slowest, healed_locators, failures. HTML report with failure screenshots.
  desktop_scenario_from_macro(macro_name)   recorded macro → scenario with default asserts
  desktop_report_list()                     saved reports and their KPIs

CANVAS FALLBACKS — when the control tree has nothing (drawing areas)
  desktop_find_image(window_json, image, threshold)   OpenCV template match → window x,y
  desktop_vision_locate(window_json, description)     multimodal model → window x,y
  desktop_vision_verify(window_json, expectation)     multimodal pass/fail with reasoning
  Step forms: {"action": "click_image", "image": ...} · {"action": "click_vision", "description": ...}
              {"action": "wait_for", "image": ...} · {"action": "wait_idle"}

LOCATOR HEALING (automatic in every step with a locator)
  A locator that matches nothing is retried with the recorded hint (title + control_type)
  and then a fuzzy title match over the live tree. The step reports `healed_locator` and a
  warning — update the script with it. Suites count healed steps in kpis.healed_locators.

METRICS — are the commands improving or getting worse?
  Every browser_/desktop_/vm_ response is recorded in a SQLite ledger (runs, steps, maps,
  scenarios). Indicators compare the window with the one before it: improving · stable ·
  worsening · insufficient (< 5 samples). Health score 0–100.
  metrics_summary(window, target, kind, include_definitions)   all indicators + regressions
  metrics_trend(indicator, window, bucket, target)             series + SVG chart
  metrics_dashboard(window, bucket, target)                    HTML dashboard on disk
  metrics_errors(window, target)        taxonomy, top failing steps, healed locators, map drift
  metrics_targets(window) · metrics_indicators() · metrics_export(table, window, fmt)
  Groups: map (stable-id coverage, drift) · locators (hit, broken, ambiguous, healing, pixel
  fallback) · execution (step/sequence success, tool failures, first-failure depth) ·
  tests (scenario/assertion pass, flakiness) · speed (p50/p95, wait share).
  USE: after a batch of runs, call metrics_summary; act on `regressions` first.

RECOMMENDED DESKTOP WORKFLOW
  1. vm_snapshot_restore("win11-cadapp", "clean")  → known state
  2. vm_guest_ip("win11-cadapp") → vm_agent_check("http://<ip>:8020")
  3. desktop_launch("C:/Program Files/CadApp/CadApp.exe")   (or desktop_list_windows)
  4. desktop_map_window('{"title_re": ".*CadApp.*"}') → read control_index and menus
  5. desktop_explore_menus(...)                         → menu paths hidden behind the bar
  6. desktop_execute_sequence(steps) or desktop_auto_sequence(goal)
  7. desktop_diff_window(window, steps)                 → assert the UI changed as expected
  8. desktop_record_start/stop to turn a manual test script into a replayable macro

════════════════════════════════════════════════════════════════
 _polarix TELEMETRY (present in every tool response)
════════════════════════════════════════════════════════════════

Every tool response includes a `_polarix` block with observability data:

  _polarix.tool              — tool name that was called
  _polarix.duration_ms       — total wall-clock time in milliseconds
  _polarix.desktop           — desktop/vm tools: driver, backend, platform, remote agent URL,
                               window {title, pid, process, handle, is_active}
  _polarix.browser           — browser state at end of execution:
    .final_url               — where the browser ended up
    .title                   — page title at end
    .headless                — whether browser ran headless
    .session_used            — whether a session file was loaded
    .redirect_detected       — True if final_url looks like a login/auth redirect
    .console_errors          — count of JS errors during execution
    .performance             — page_load_ms and dom_ready_ms from the Performance API
  _polarix.effective_params  — key parameters actually resolved and used
  _polarix.warnings          — list of non-fatal issues detected during execution

Use _polarix to reason about:
  • Performance: page_load_ms high → site under load or misconfigured
  • Session health: redirect_detected=True → re-authenticate before retrying
  • Selector reliability: warnings about match counts → re-map the page
  • Step bottlenecks: per-step duration_ms in execute_sequence results

Per-step telemetry in browser_execute_sequence results:
  { step, action, success, duration_ms, selector_match_count, result, error }
  selector_match_count = 0 → broken selector
  selector_match_count > 1 → ambiguous selector, first element was used

════════════════════════════════════════════════════════════════
 RECOMMENDED WORKFLOW FOR A NEW SITE
════════════════════════════════════════════════════════════════

1. browser_session_save("myapp", "https://app.com/login", "user", "pass")
2. browser_map_site("https://app.com", session_file=".../myapp.json")
   → read selector_index to know every [data-qa] and where it lives
3. browser_explore_page("https://app.com/dashboard", trigger_interactions=True)
   → discover what dropdowns and modals are hidden behind triggers
4. browser_get_external_resources("https://app.com/dashboard", ...)
   → find the real API base URL and all external origins
5. browser_intercept_network("https://app.com/dashboard", filter_url_contains="api.")
   → capture exact endpoint calls and payloads
6. browser_inject_js("document.cookie", url="https://app.com/dashboard", ...)
   → extract JS-accessible data (tokens, state) directly from the page

Option A — precise, selector-based:
7. browser_execute_sequence('[{"action":"click","selector":"[data-qa=X]"}]', ...)
   → act with precision using real selectors
8. browser_diff_pages("https://app.com/dashboard", actions_code="...")
   → confirm the UI changed as expected

Option B — goal-driven (when you know what, not how):
7. browser_auto_sequence("fill the search form with 'Paris' and submit", url="https://app.com/dashboard")
   → Polarix maps the page, generates the steps via LLM, and executes
"""

mcp = FastMCP(
    "Polarix",
    host=MCP_HOST,
    port=MCP_PORT,
    instructions=_INSTRUCTIONS,
)
