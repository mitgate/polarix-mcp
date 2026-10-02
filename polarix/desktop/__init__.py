"""Desktop automation layer — Map First for native Windows applications.

The browser side of Polarix maps a page before acting on it. This package does
the same for desktop windows: inventory the UI Automation control tree, feed it
to the planner, execute a typed step sequence, diff the tree afterwards.

Modules
  model      — Control dataclass, locator parsing, control index, tree diff
  driver     — DesktopDriver contract and get_driver() factory
  fake_driver     — in-memory simulated app (runs on any OS; used by tests)
  pywinauto_driver — real Windows implementation (lazy import of pywinauto)
  steps      — run_desktop_steps(): the desktop twin of _run_steps()
  recorder   — macro recording from real mouse/keyboard events
  macros     — named macro persistence under POLARIX_MACROS_DIR
"""
