"""Conductor-MCP — the operator surface (`orchestration.md` sec 10, P8).

See `README.md` in this package for the design rationale. Submodules:

- `lifecycle_tools` — 1:1 wrappers over the six real, shipped `spec-lifecycle`
  CLI verbs (`status`/`validate`/`approve`/`archive`/`guard`).
- `workflow_tools` — the small, new Conductor workflow-control set
  (list runs, inspect the escalation queue, decide a resume) built on
  `orchestration.resume`.
- `server` — wires both onto a stdio MCP server via `FastMCP`. Run with
  `python -m orchestration.mcp.server`.
"""
