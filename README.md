# Polarix MCP

**Browser and desktop automation for AI agents — Map First architecture.**

Web pages through Playwright on the host; native applications (a CAD suite, an ERP client, any
Windows program) inside a virtual machine that Polarix controls through a small guest
agent and the hypervisor. Same philosophy on both: map the UI first, then act with
real locators, then diff to prove what changed.

Polarix gives the AI a complete mental map of any website before writing a single automation step.
Named after the North Star: a fixed reference that sailors used to orient themselves before crossing
any sea. Polarix gives the AI that same fixed point — a full structural map of selectors, API calls,
and hidden elements — so it acts with knowledge instead of trial and error.

---

## Architecture

```
KNOWLEDGE   → understand the site first
  browser_map_site               BFS crawl → selector inventory across all pages
  browser_explore_page           Click triggers to reveal hidden dropdowns / modals / tabs
  browser_intercept_network      Capture live XHR/fetch: endpoints, payloads, responses
  browser_accessibility_tree     ARIA tree — works on any site without data-qa attributes
  browser_get_external_resources All external origins a page contacts (DOM + network)

EXECUTION   → act with real selectors
  browser_inject_js           Inject and run JavaScript; returns the result
  browser_run_playwright      Run Python Playwright code directly (no LLM in the loop)
  browser_execute_sequence    Typed JSON action sequence: goto / click / fill / wait_for / ...
  browser_auto_sequence       Map First in one call: map → explore → LLM plans → execute
  browser_run_task            LLM-driven natural language automation (fallback / exploration)

VERIFICATION → confirm what happened
  browser_diff_pages          Structural diff between two page states (before / after)
  browser_capture_console     Browser console output: errors, warnings, logs
  browser_get_storage         Read localStorage, sessionStorage, cookies

AUTHENTICATION
  browser_login               One-off login — saves session to a file path you specify
  browser_session_save        Named login — saves under a friendly name for reuse
  browser_session_check       Verify a named session is still active
  browser_session_list        List all saved sessions

UTILITIES
  browser_screenshot          Capture page as base64 PNG
  browser_get_page_content    Visible page text (no HTML), up to 20,000 characters
  browser_get_help            Return full documentation as a string

DESKTOP — native windows in a guest VM (or on this host when it is Windows)
  desktop_list_windows        Top-level windows: title, process, pid, handle
  desktop_map_window          UI Automation control tree + control_index + menu bar (the "site map")
  desktop_explore_menus       Open every menu and return the items it reveals as "Top->Item" paths
  desktop_launch              Start an application, returns its window locator
  desktop_execute_sequence    Typed steps: click / set_text / type / press / menu / wait_for / ...
  desktop_auto_sequence       Map First in one call: map → explore → LLM plans → execute
  desktop_diff_window         Control-tree diff before/after steps + windows opened/closed
  desktop_screenshot          Window as base64 PNG
  desktop_record_start/stop   Record real mouse/keyboard into a replayable macro (pynput)
  desktop_macro_*             save · list · run · delete named macros

VM — hypervisor control of the guest (libvirt/virsh, VirtualBox, Android adb)
  vm_backends · vm_list · vm_start · vm_stop
  vm_snapshot_list · vm_snapshot_save · vm_snapshot_restore   reset to a known state per run
  vm_screenshot · vm_send_keys · vm_tap                      work with no agent in the guest
  vm_guest_ip · vm_agent_check                               find and ping the Polarix agent

TESTING — scenarios, assertions, KPIs
  desktop_run_scenario        Steps + assertions → pass/fail, failure screenshot, HTML report
  desktop_run_suite           Many scenarios → pass rate, assertion rate, duration, failures
  desktop_scenario_from_macro Recorded macro → scenario with default assertions
  desktop_report_list         Saved reports and their KPIs

CANVAS FALLBACKS — pixels when the control tree has nothing
  desktop_find_image          OpenCV template match → window-relative x, y
  desktop_vision_locate       Multimodal model finds a described element → x, y
  desktop_vision_verify       Multimodal pass/fail judgement with reasoning
```

---

## How it works

```
1. Map the site       →  know every selector before writing a single line
2. Explore pages      →  discover dropdowns / modals hidden behind interactions
3. External resources →  find all external origins (APIs, CDNs, analytics)
4. Intercept APIs     →  see every endpoint the frontend calls, with payloads
5. Inject JS          →  extract tokens, override functions, read JS-only state
6. Execute            →  use real selectors from the map (precise, deterministic)
7. Verify             →  diff the UI state before and after each action

Shortcut (steps 1–6 in one call):
  browser_auto_sequence("your goal", url) → maps, explores, plans via LLM, executes
```

When any AI agent connects to Polarix it receives a full capability briefing automatically
via the FastMCP `instructions` parameter — no manual setup required.

---

## System Requirements

| Requirement | Minimum |
|-------------|---------|
| Python | 3.11 or higher |
| Operating system | Linux or macOS for the Polarix server; a Windows guest (VM) for `desktop_*` |
| Internet access | Required (Playwright downloads Chromium) |
| LLM API key | At least one of: OpenAI or Anthropic |
| Hypervisor (optional) | libvirt/KVM (`virsh`), VirtualBox (`VBoxManage`) or Android SDK (`adb`) for `vm_*` |

---

## Python Dependencies

```
browser-use>=0.2.0
playwright>=1.44.0
mcp[server]>=1.0.0
```

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/polarix-mcp.git
cd polarix-mcp
```

### 2. Create and activate a Python 3.11+ virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\activate           # Windows
```

### 3. Install Python packages

```bash
pip install --upgrade pip
pip install browser-use playwright mcp
```

### 4. Install the Chromium browser

```bash
playwright install chromium
```

### 5. Set environment variables

Create a `.env` file in the project root (it is git-ignored):

```bash
# Required: at least one LLM key
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Optional overrides (these are the defaults)
BROWSER_USE_MODEL=gpt-4o-mini       # LLM for browser_run_task
BROWSER_HEADLESS=true               # false = show the browser window
POLARIX_SESSIONS_DIR=/tmp/polarix_sessions
MCP_HOST=127.0.0.1
MCP_PORT=8016
MCP_TRANSPORT=streamable-http
```

### 6. Start the server

```bash
./start.sh
```

Or directly:

```bash
source .venv/bin/activate
source .env  # or export the variables manually
python browser_python_mcp.py
```

The server listens on `http://127.0.0.1:8016/mcp` by default.

---

## Connecting an AI Client

### Claude Code / Cline / Zed (streamable-http)

Add to your MCP settings:

```json
{
  "polarix": {
    "type": "streamable-http",
    "url": "http://127.0.0.1:8016/mcp"
  }
}
```

### Goose (streamable_http with uri)

```json
{
  "polarix": {
    "type": "streamable_http",
    "uri": "http://127.0.0.1:8016/mcp"
  }
}
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Required for OpenAI models (e.g. `gpt-4o-mini`) |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic models (e.g. `claude-sonnet-4-6`) |
| `BROWSER_USE_MODEL` | `gpt-4o-mini` | LLM used by `browser_run_task` |
| `BROWSER_HEADLESS` | `true` | Set `false` to show the browser window |
| `POLARIX_SESSIONS_DIR` | `/tmp/polarix_sessions` | Directory for named session files |
| `MCP_HOST` | `127.0.0.1` | Server bind address |
| `MCP_PORT` | `8016` | Server port |
| `MCP_TRANSPORT` | `streamable-http` | MCP transport protocol |
| `POLARIX_DESKTOP_DRIVER` | `auto` | `remote` (agent in a VM) · `auto`/`pywinauto` (local Windows) · `fake` (simulated app) |
| `POLARIX_DESKTOP_AGENT_URL` | — | `http://<guest-ip>:8020` when `POLARIX_DESKTOP_DRIVER=remote` |
| `POLARIX_AGENT_TOKEN` | — | Shared secret sent as `X-Polarix-Token` to the guest agent |
| `POLARIX_DESKTOP_BACKEND` | `uia` | pywinauto backend: `uia` (UI Automation) or `win32` |
| `POLARIX_VM_BACKEND` | `auto` | `libvirt` · `virtualbox` · `android` (auto picks the first CLI found) |
| `POLARIX_MACROS_DIR` | `$TMP/polarix_macros` | Where recorded/saved macros live |
| `POLARIX_REPORTS_DIR` | `$TMP/polarix_reports` | Where scenario/suite reports (JSON + HTML) are written |
| `POLARIX_VISION_MODEL` | `BROWSER_USE_MODEL` | Multimodal model for `click_vision`, `assert vision`, `desktop_vision_*` |
| `POLARIX_DESKTOP_FAKE_APP` | — | JSON fixture for the simulated app (dev/tests) |

Legacy `POLARIS_*` spellings are still read as a fallback.

---

## Desktop automation — VM targets

A desktop target is a virtual machine (or emulator) that Polarix owns. The Polarix
server stays on the host; two paths reach the guest:

```
            host (Fedora / macOS / Linux)                         guest VM
 ┌──────────────────────────────────────────┐        ┌─────────────────────────────────┐
 │ Polarix MCP                              │        │ Windows 10/11                   │
 │  desktop_*  ──► RemoteDesktopDriver ─────┼─ HTTP ─┼─► polarix.desktop.agent         │
 │                 (POLARIX_DESKTOP_AGENT_URL)│  :8020 │     └─ pywinauto (UIA / win32) │
 │  vm_*       ──► virsh / VBoxManage / adb ─┼────────┼─► power · snapshots · display  │
 └──────────────────────────────────────────┘        │     · raw keyboard (no agent)   │
                                                      └─────────────────────────────────┘
```

* **Agent path** (`desktop_*`): the control tree, locators, menus, macros. Needs the
  agent running in the guest session: `scripts\agent_windows.ps1 -Token SECRET`.
  The agent is standard-library HTTP; the guest needs only Python + `pywinauto`
  (+ `pynput` for recording, `pillow` for screenshots). No Playwright, no MCP SDK.
* **Hypervisor path** (`vm_*`): lifecycle and the "blind" fallback — screenshots and
  raw keystrokes through `virsh`/`VBoxManage`/`adb` work before any agent exists, and
  `vm_snapshot_restore` is what makes test runs repeatable.

### Setting up a Windows target on Fedora (KVM)

```bash
sudo dnf install libvirt qemu-kvm virt-install virt-viewer    # one-off
# create the VM (virt-manager or virt-install), install Windows + the apps under test,
# copy this repo into the guest and run scripts\agent_windows.ps1, then:
virsh snapshot-create-as win11-cadapp --name clean
```

```bash
# .env on the host
POLARIX_DESKTOP_DRIVER=remote
POLARIX_DESKTOP_AGENT_URL=http://192.168.122.15:8020     # vm_guest_ip tells you this
POLARIX_AGENT_TOKEN=SECRET
```

### Recommended desktop workflow

```python
vm_snapshot_restore("win11-cadapp", "clean")                 # 1. known state
vm_agent_check("http://192.168.122.15:8020", token="SECRET")  # 2. agent alive?
desktop_launch("C:/Program Files/CadApp/CadApp.exe")               # 3. open the app
desktop_map_window('{"title_re": ".*CadApp.*"}')             # 4. control_index + menus
desktop_explore_menus('{"title_re": ".*CadApp.*"}')          # 5. "Arquivo->Abrir...", ...
desktop_execute_sequence('[                                   # 6. act with real locators
  {"action": "menu", "path": "Arquivo->Salvar"},
  {"action": "wait_for", "window": {"title_re": ".*Salvar como.*"}},
  {"action": "set_text", "locator": {"auto_id": "1001"}, "value": "project.cad"},
  {"action": "click", "locator": {"auto_id": "1"}},
  {"action": "snapshot"}]', '{"title_re": ".*CadApp.*"}')
desktop_diff_window('{"title_re": ".*CadApp.*"}', '[...]')  # 7. prove the UI changed
desktop_record_start("lancar-viga", '{"title_re": ".*CadApp.*"}')   # 8. or record a manual
desktop_record_stop("lancar-viga")  ;  desktop_macro_run("lancar-viga")  # script and replay it
```

Or let the planner do steps 4–6: `desktop_auto_sequence("salvar o projeto como project.cad", '{"title_re": ".*CadApp.*"}')`.

### Step reference (`desktop_execute_sequence`, macros, planner output)

| Action | Fields | Notes |
|--------|--------|-------|
| `launch` | `path`, `args?`, `title_re?`, `wait_seconds?` | sets the current window |
| `focus` | `window` | switch the current window |
| `click` / `double_click` / `right_click` | `locator` **or** `x`,`y`; `wait_after?` | coordinates are relative to the window — canvas fallback |
| `set_text` | `locator`, `value` | Edit / Document / ComboBox |
| `type` | `text` **or** `keys`; `locator?` | `text` is typed literally; `keys` uses pywinauto syntax |
| `press` | `key` | `Enter`, `ctrl+s`, `alt+f`, `F5`, `shift+Tab`, or raw `{ENTER}` |
| `select` | `locator`, `item` | ComboBox / ListBox / Tab |
| `menu` | `path` | `"Arquivo->Salvar"` |
| `wait_for` | `locator` (+`state?`) **or** `window` **or** `seconds`; `timeout?` | `window` also switches the current window |
| `snapshot` | `max_depth?` | control inventory of the current window |
| `screenshot` | — | base64 PNG |
| `close` | `force?` | close (or kill) the current window |

Locators: `{"auto_id": "1001"}` · `{"title": "Salvar", "control_type": "Button"}` ·
`{"title_re": "Sal.*"}` · `{"path": "0/2/1"}` · add `"found_index": n` when ambiguous.
Shorthand strings also work: `"auto_id=1001"`, `"title=Salvar;control_type=Button"`.

### Developing without a Windows VM

`POLARIX_DESKTOP_DRIVER=fake` runs every desktop tool against a simulated
Notepad-like app with a "Salvar como" dialog — the whole map → plan → execute → diff
pipeline works on Linux, and the test-suite (`python3.11 -m pytest tests`) exercises
it, including the host→agent hop over the loopback. Start the agent with
`python -m polarix.desktop.agent --driver fake` to get a persistent simulated target.

### Test scenarios, assertions and KPIs

A scenario is a step sequence with `assert` steps. Run one with `desktop_run_scenario`,
many with `desktop_run_suite`; both write a JSON + HTML report (failure screenshots
inline) under `POLARIX_REPORTS_DIR`.

```yaml
# scenarios/save-as.yaml
name: save-as
description: saving the document renames the main window
tags: [smoke]
window: { title_re: ".*Editor.*" }
vm: { name: win11-cadapp, snapshot: clean }          # optional — restored with restore_vm=true
steps:
  - { action: menu, path: "File->Save" }
  - { action: wait_for, window: { title_re: ".*Save As.*" } }
  - { action: assert, kind: control_exists, locator: { auto_id: "1001" } }
  - { action: set_text, locator: { auto_id: "1001" }, value: project.txt }
  - { action: click, locator: { auto_id: "1" } }
  - { action: wait_for, window: { title_re: ".*Editor.*" } }
  - { action: assert, kind: window_title_contains, expected: project.txt }
  - { action: assert, kind: window_absent, window: { title_re: ".*Save As.*" } }
teardown:
  - { action: screenshot }
```

```python
desktop_run_suite("scenarios/", name="nightly", tags_json='["smoke"]', restore_vm=True)
# → kpis: {scenarios, passed, failed, error, pass_rate, assertions_total,
#          assertion_pass_rate, duration_ms, mean_scenario_ms, slowest, healed_locators}
#   failures: [{name, status, first_failure}], report: {json, html}
```

Assertion kinds: `control_exists` · `control_absent` · `control_enabled` · `control_disabled` ·
`text_equals` · `text_contains` · `window_exists` · `window_absent` · `window_title_contains` ·
`image_present` · `image_absent` · `vision` (an expectation in words, judged by a multimodal
model: `{"action": "assert", "kind": "vision", "expectation": "a beam is drawn between the columns"}`).

From a recorded macro to a test in one call: `desktop_scenario_from_macro("draw-beam")`
wraps the macro, adds `control_exists` before every recorded click and `window_exists`
at the end — edit the returned JSON to add the checks that matter, then run it.

### Locator healing

Application updates rename controls. When a locator matches nothing, the runner retries
with the hint recorded at capture time (`title` + `control_type`) and then a fuzzy title
match over the live tree. The step still runs, reports `healed_locator`, and adds a
warning so the script can be updated; suites count them in `kpis.healed_locators`.

### Canvas fallbacks — when the tree has nothing

Drawing areas are usually invisible to UI Automation. Three tools cover them:

```python
# 1. deterministic: a crop of the toolbar icon / drawn element you want to hit
desktop_find_image('{"title_re": ".*CadApp.*"}', image="icons/beam-tool.png")
# → {found: true, best: {x: 212, y: 48, score: 0.97, rect: [...]}, matches: [...]}

# 2. in a sequence
[{"action": "click_image", "image": "icons/beam-tool.png", "threshold": 0.9},
 {"action": "click", "x": 400, "y": 300},                 # first point on the canvas
 {"action": "click", "x": 700, "y": 300},                 # second point
 {"action": "wait_idle"},                                 # tree stopped changing
 {"action": "assert", "kind": "vision",
  "expectation": "a horizontal beam is drawn between the two columns"}]

# 3. last resort: describe it
desktop_vision_locate('{"title_re": ".*CadApp.*"}', "the red column on the left")
desktop_vision_verify('{"title_re": ".*CadApp.*"}', "no error dialog is visible")
```

`click_vision` / `assert vision` use `POLARIX_VISION_MODEL` (any vision-capable OpenAI or
`claude-*` model). Prefer `find_image` whenever you have a reference crop — it is
deterministic and free.

### Known limits

* The real `pywinauto` driver has not been exercised from this Linux host — see
  `HANDOFF.md` for the first checks to run inside the VM.
* Vision-based steps are only as reliable as the model; use them for assertions and for
  the first click on a canvas, and pin coordinates or crops once they are known.
* Only a Windows guest driver exists today. Linux (AT-SPI), macOS (Accessibility) and
  Android (uiautomator2) guests would implement the same `DesktopDriver` contract
  behind the same agent.

---

## Demos

Real captures from Polarix running against public websites — no mocking, no staging.

---

### 1 — Loading and screenshotting any page

Polarix opens a real Chromium browser (headless or visible), loads the page, and returns a
base64 PNG. Call `browser_screenshot` with any URL:

```python
browser_screenshot("https://en.wikipedia.org/wiki/Python_(programming_language)")
```

![Wikipedia loaded by Polarix](assets/demo-wikipedia.png)

> **_polarix telemetry**: `page_load_ms: 322`, `dom_ready_ms: 297`, `console_errors: 0`

---

### 2 — Mapping a site's full structure before writing automation

`browser_map_site` crawls the site via BFS and returns every form, link, selector and
interactive trigger. Here is a real crawl of [quotes.toscrape.com](https://quotes.toscrape.com):

![quotes.toscrape.com loaded by Polarix](assets/demo-screenshot.png)

```json
// browser_map_site("https://quotes.toscrape.com/", max_pages=3)
{
  "pages_mapped": 3,
  "pages": [
    {
      "url": "https://quotes.toscrape.com/",
      "title": "Quotes to Scrape",
      "navigation_links": ["/login", "/author/Albert-Einstein", "/tag/change/page/1/", "...46 more"],
      "forms": [],
      "interactive_triggers": []
    },
    {
      "url": "https://quotes.toscrape.com/login",
      "forms": [{
        "inputs": [
          {"name": "csrf_token", "type": "hidden"},
          {"id": "username",     "type": "text"},
          {"id": "password",     "type": "password"},
          {"type": "submit"}
        ]
      }]
    }
  ],
  "_polarix": {"duration_ms": 12984, "browser": {"console_errors": 0}}
}
```

In 13 seconds Polarix discovered the login form, all navigation links, and the full site
structure — without you writing a single selector.

---

### 3 — Auditing what a site sends to third parties

`browser_get_external_resources` combines a static DOM scan with live network interception
to reveal every external origin a page contacts. Here is BBC.com:

```json
// browser_get_external_resources("https://www.bbc.com/")
{
  "external_origin_count": 30,
  "total_external_urls": 202,
  "external_origins": [
    {"hostname": "static.files.bbci.co.uk",       "category": "cdn",       "count": 90},
    {"hostname": "ichef.bbci.co.uk",              "category": "cdn",       "count": 65},
    {"hostname": "securepubads.g.doubleclick.net", "category": "ads",       "count": 4},
    {"hostname": "cdn.cxense.com",                "category": "analytics", "count": 3},
    {"hostname": "cdn.privacy-mgmt.com",          "category": "cdn",       "count": 3},
    {"hostname": "uk-script.dotmetrics.net",      "category": "analytics", "count": 3},
    {"hostname": "cdn.optimizely.com",            "category": "analytics", "count": 1},
    {"hostname": "prebid.the-ozone-project.com",  "category": "ads",       "count": 5}
  ]
}
```

30 external origins, 202 external resources — DoubleClick ads, Optimizely A/B testing,
Cxense analytics, Permutive audience segmentation, all visible in one call.

---

### 4 — Extracting structured data via JavaScript injection

`browser_inject_js` runs arbitrary JavaScript inside the live page and returns the result.
No HTML scraping needed — read directly from the DOM or JS state:

```python
browser_inject_js("""
    (() => {
        const infobox = {};
        document.querySelectorAll('.infobox tr').forEach(row => {
            const label = row.querySelector('th')?.innerText?.trim();
            const value = row.querySelector('td')?.innerText?.trim();
            if (label && value) infobox[label] = value;
        });
        return {
            title: document.querySelector('h1')?.innerText,
            first_paragraph: document.querySelector('.mw-parser-output > p')?.innerText?.slice(0, 200),
            infobox: infobox,
        };
    })()
""", url="https://en.wikipedia.org/wiki/Python_(programming_language)")
```

```json
{
  "result": {
    "title": "Python (programming language)",
    "first_paragraph": "Python is a high-level, general-purpose programming language that emphasizes code readability...",
    "infobox": {
      "Paradigm":       "Multi-paradigm: object-oriented, procedural, functional, structured",
      "Designed by":    "Guido van Rossum",
      "Developer":      "Python Software Foundation",
      "First appeared": "20 February 1991; 35 years ago",
      "Stable release": "3.14.6 / 10 June 2026"
    }
  },
  "_polarix": {"duration_ms": 3661, "browser": {"console_errors": 0}}
}
```

---

### 5 — Goal-driven automation: browser_auto_sequence

Give Polarix a goal in plain English. It maps the page, explores hidden triggers,
asks an LLM to generate the optimal step sequence using the map as context, and executes.

```python
# Login to quotes.toscrape.com — Polarix discovers the form, generates the steps, executes
browser_auto_sequence(
    goal="Log in with username 'admin' and password 'admin', then navigate to the main page",
    url="https://quotes.toscrape.com/login",
)
```

```json
{
  "goal": "Log in with username 'admin' and password 'admin', then navigate to the main page",
  "generated_steps": [
    {"action": "fill",    "selector": "#username", "value": "admin"},
    {"action": "fill",    "selector": "#password", "value": "admin"},
    {"action": "click",   "selector": "input[type=submit]", "wait_after": 2.0},
    {"action": "wait_for","seconds": 2},
    {"action": "snapshot"}
  ],
  "execution": {
    "steps_total": 5,
    "steps_succeeded": 5,
    "final_url": "https://quotes.toscrape.com/",
    "results": [
      {"step": 1, "action": "fill",     "success": true, "duration_ms": 312},
      {"step": 2, "action": "fill",     "success": true, "duration_ms": 198},
      {"step": 3, "action": "click",    "success": true, "duration_ms": 2145},
      {"step": 4, "action": "wait_for", "success": true, "duration_ms": 2001},
      {"step": 5, "action": "snapshot", "success": true, "duration_ms": 87}
    ]
  }
}
```

The LLM never touched the browser directly. It received the site map, wrote the steps,
and Polarix executed them deterministically. No hallucinated selectors, no retries.

---

### 6 — The login form discovered, filled, and submitted

![Login form discovered by browser_map_site](assets/demo-login-form.png)

Every field (`#username`, `#password`, `input[type=submit]`) was discovered by
`browser_map_site` — the form structure above is real data from the crawl, not
guesswork.

---

## Quick Start Example

```python
# 1. Save a named session
browser_session_save("myapp", "https://app.example.com/login", "user@x.com", "password")

# 2. Map the entire site — get all selectors before writing any automation
browser_map_site("https://app.example.com", session_file="/tmp/polarix_sessions/myapp.json")
# → selector_index: {"AddButton": {"count_total": 1, "pages_found": ["/dashboard"]}, ...}

# 3. Deep-inspect a page to discover hidden dropdowns and modals
browser_explore_page("https://app.example.com/dashboard", trigger_interactions=True)

# 4. Find all external origins the page contacts (APIs, CDNs, analytics)
browser_get_external_resources("https://app.example.com/dashboard")
# → [{"hostname": "api.app.com", "category": "api", "count": 12, "urls": [...]}]

# 5. Map the API layer — discover every endpoint the page calls
browser_intercept_network("https://app.example.com/dashboard", filter_url_contains="api.")

# 6. Inject JS to extract data only visible in JS state
browser_inject_js("JSON.stringify(window.__APP_CONFIG__)", url="https://app.example.com/dashboard")

# 7. Execute with real selectors from the map
browser_execute_sequence('[
  {"action": "goto", "url": "https://app.example.com/dashboard"},
  {"action": "click", "selector": "[data-qa=AddButton]"},
  {"action": "fill",  "selector": "[data-qa=NameInput]", "value": "New Item"},
  {"action": "click", "selector": "[data-qa=SaveButton]"}
]', session_file="/tmp/polarix_sessions/myapp.json")

# 8. Verify the UI changed as expected
browser_diff_pages(
  "https://app.example.com/dashboard",
  actions_code='await page.click("[data-qa=AddButton]")'
)
```

---

## Tool Reference

### KNOWLEDGE

**`browser_map_site`** — BFS crawl, up to `max_pages` pages.
Returns: `{ pages_mapped, pages: [...], selector_index: {...} }`

**`browser_explore_page`** — Static snapshot + click-triggered discovery.
Returns: `{ static_elements, revealed_after_interactions: [{trigger_qa, new_elements}] }`

**`browser_intercept_network`** — Live XHR/fetch capture during load and optional actions.
Returns: `{ requests_captured, entries: [{method, url, status, request_body, response_body}] }`

**`browser_accessibility_tree`** — Full ARIA tree, flat + nested.
Returns: `{ node_count, flat: [{depth, role, name}], tree }`

**`browser_get_external_resources`** — All external URLs a page loads or links to.
Combines static DOM scan + live network interception.
Returns: `{ external_origin_count, external_origins: [{hostname, category, count, urls}] }`
Categories: `analytics` · `cdn` · `api` · `font` · `social` · `ads` · `other`

### EXECUTION

**`browser_inject_js`** — Evaluate JavaScript in the live page context.
Returns the result (must be JSON-serializable). With `persistent=True` the script re-runs on every navigation via `addInitScript`.
Use for: extracting split auth tokens from cookies, reading `window.__store__`, overriding `window.fetch`.

**`browser_run_playwright`** — Execute Python Playwright code. Receives `page`, `context`, `asyncio`.
Use `return {...}` to pass data back. Always use selectors from `browser_map_site`.

**`browser_execute_sequence`** — Typed JSON action sequence.
Actions: `goto` · `click` · `fill` · `select` · `press` · `hover` · `scroll` · `wait_for` · `snapshot` · `screenshot` · `evaluate`

**`browser_auto_sequence`** — Map First em uma única chamada.
Mapeia a página, explora triggers, gera sequência via LLM e executa.
Com `dry_run=True` retorna apenas os steps sem executar.
Returns: `{ goal, generated_steps, execution: { steps_succeeded, final_url, results } }`

**`browser_run_task`** — Natural language task via an LLM agent (browser-use).
Fallback for unstructured exploration when selectors are not yet known.

### VERIFICATION

**`browser_diff_pages`** — Compares two URLs, or before/after an action.
Returns: `{ added_qa, removed_qa, changed_texts, changed_counts }`

**`browser_capture_console`** — Console output during load and actions.
Returns: `{ errors, warnings, info, all_messages }`

**`browser_get_storage`** — localStorage, sessionStorage, cookies for a URL.

### AUTHENTICATION

**`browser_login`** — One-off login, saves session to a file path.

**`browser_session_save`** — Named login saved under `POLARIX_SESSIONS_DIR/{name}.json`.

**`browser_session_check`** — Verifies a named session is still active.

**`browser_session_list`** — Lists all saved sessions with metadata.

### UTILITIES

**`browser_screenshot`** — Returns `data:image/png;base64,...`

**`browser_get_page_content`** — Visible text up to 20,000 characters.

**`browser_get_help`** — Returns full documentation as a string.

---

## License

MIT
