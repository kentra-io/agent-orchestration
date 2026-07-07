# `orchestration/mcp/` — the Conductor-MCP (M7)

`orchestration.md` sec 10 / `implementation-plan.md` P8: "Operator surface +
trigger: **MCP-only** (no thin CLI): the six `lifecycle` verbs 1:1 +
workflow-control (list runs, inspect the escalation queue, `gate-respond`,
resume)." This package is that surface.

## Two halves

1. **`lifecycle_tools`** — 1:1 wrappers over the six real, shipped
   `spec-lifecycle` v0.1.0 verbs (`init` is provisioning, not wired here;
   the other five are): `get_state` (`status`), `validate_stage`
   (`validate`), `record_approval` (`approve`), `archive_change`
   (`archive`), `run_guard` (`guard`). **No invented verbs** — the
   2026-07-05 reconciliation explicitly dropped `submit_artifact`/
   `request_transition`; this module doesn't reintroduce anything like them.
   Every function shells out to the real `lifecycle` binary and returns a
   uniform `{"exit_code", "stdout", "stderr", "json"}` (`"json"` parsed only
   when the CLI emitted valid JSON — `approve` has no `--format json` at
   all, verified, so its `"json"` is always `None`).

2. **`workflow_tools`** — the small, new Conductor workflow-control set:
   `list_runs` (wraps `CheckpointManager.list_checkpoints` — the same data
   `conductor checkpoints` shows, as structured dicts), `inspect_escalation_
   queue` (is a workflow paused mid-milestone right now, and if so: which
   milestone, which are done, and the Verifier's reports for it — built on
   `orchestration.resume.checkpoint`/`events`), and `resolve_gate` (a
   read-only wrapper over `orchestration.resume.watcher.decide` — tells an
   operator what a resume WOULD do, without doing it).

`server.py` wires both onto a stdio MCP server via the standard `mcp` SDK's
`FastMCP`. Every tool function has flat, JSON-schema-friendly parameters
(no dataclasses crossing the MCP boundary) so FastMCP derives its tool
schemas automatically — `orchestration.resume`'s richer dataclass API
(`EscalationBaseline`, `ResumeDecision`) stays internal, reconstructed from
flat args at the `server.py` boundary.

## The consent invariant (sec 7.3) — read this before wiring anything

This server **legitimately exposes `record_approval` and `archive_change`**
— that is the human operator's Mode-A consent act, and this server exists
specifically to be that surface (`orchestration.md` sec 10: "a human drives
and inspects the pipeline through an interactive claude session equipped
with the Conductor-MCP"). That is NOT a contradiction of sec 7.3's "`lifecycle
approve`/`archive` are never in a Conductor-spawned agent's tool surface" —
the two are about **different actors**:

- **This MCP server** → a human-driven, Mode-A, interactive session. Having
  `record_approval`/`archive_change` here is correct and intended.
- **A Conductor-spawned (Mode-B) agent's tool surface** (the Implementer /
  Verifier / Orchestrator personas, or any workflow step) → must NEVER have
  these verbs, under any name, in any form (an MCP tool, a skill, a raw
  shell command). That is what `tests/test_consent_invariant.py` actually
  checks — it inspects `workflows/*.yaml`'s agent/step definitions, not this
  server. See that test's own docstring for the current scope caveat (no
  materialized persona `.md` files exist in this module repo yet — those
  are the branded harness layer, M6/M9 — so the check today is necessarily
  narrower than the full spec's eventual surface).

Do not "fix" this by removing `record_approval`/`archive_change` from this
server — that would break the operator's actual job. Do not wire this
server (or any subset of it) into a workflow template's agent definition.

## What's NOT covered here (explicit scope notes)

- **Running this server against a real `spec-lifecycle` change.** Tests
  mock/stub the `lifecycle` subprocess boundary (`lifecycle_tools`'s tests)
  or drive real `conductor` runs for `workflow_tools` (reusing
  `orchestration.resume`'s own hermetic fixtures) — no test here drives a
  real change through `init`→`validate`→`approve`.
- **A persistent `EscalationBaseline` store.** `resolve_gate`'s flat args
  require the CALLER to already know the baseline (plan hash, gate
  snapshot) — this package doesn't persist that across a watcher-process
  restart. A real M8 launcher needs to record it somewhere (a small
  per-change state file) when execution starts; that persistence layer is
  out of scope here (see `orchestration/resume/README.md`'s own scope notes).
- **Auth/transport hardening.** `FastMCP`'s default stdio transport, no
  additional auth layer — appropriate for a locally-run, human-driven
  session (the target use case); a remote/multi-tenant deployment would
  need more.
