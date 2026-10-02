"""Metrics — are the commands getting better or worse?

Every tool response passes through telemetry._wrap(); the ledger turns it into
rows (runs, steps, maps, scenarios) in a local SQLite file. Indicators are
computed over a time window and compared with the window right before it, so
each one carries a direction: improving, stable, worsening.

The indicators follow the Map First ideology. The map is the knowledge; the
indicators tell how much of it is automatable (stable identifiers), how fast
it drifts (the application changed), how often locators break or need healing,
how reliably steps and scenarios pass, and how long things take.

Modules
  ledger      — SQLite store + record_response() extraction from tool payloads
  indicators  — registry, windows, trends, drift, flakiness, health score
  charts      — dependency-free SVG charts and the HTML dashboard
"""
