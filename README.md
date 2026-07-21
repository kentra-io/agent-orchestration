# agent-orchestration

The **execution leg** of Stage 3: the business logic that drives an approved
[`spec-lifecycle`](https://github.com/kentra-io/spec-lifecycle) plan to merged
code through a fleet of agents, with a deterministic verify-and-escalate loop
and a human as the final tier.

Unlike its sibling primitives, this module does not own its engine. It
**extends** [Microsoft Conductor](https://github.com/microsoft/conductor) (MIT)
— consumed here as a pinned fork, [`kentra-io/conductor`](https://github.com/kentra-io/conductor)
— as the durable workflow spine: the attempt counter, the human-gate,
resumability, and crash-safety are Conductor's native machinery. This module
adds:

- the **implement → verify → escalate loop**, expressed as Conductor workflow
  templates over a change's milestones;
- **3-layer verification** — an executable acceptance check (L1), a generic
  project healthcheck (L2), and an advisory judging agent over plain-language
  criteria (L3) — with **author ≠ verifier** as the trust spine (the Verifier
  is always a fresh agent that never saw the Implementer's reasoning);
- a fixed **3-attempt escalation ladder** — one solo attempt, two
  Orchestrator-guided retries, then a human — via a durable Conductor-invoked
  state machine, never an agent's own judgment call;
- the `ClaudeboxProvider`, a thin fork-carried Conductor provider that runs a
  compiled agent-definition persona (`claude -p --agent`) inside a
  [`claudebox`](https://github.com/kentra-io/claudebox) sandbox.

It **consumes** the other primitives rather than absorbing them:
[`agent-definition`](https://github.com/kentra-io/agent-definition) (the
cast it runs), [`spec-lifecycle`](https://github.com/kentra-io/spec-lifecycle)
(the plan it executes and the gates it honors), and `claudebox` (the sandbox
runtime + skills/plugins overlay each agent runs in).

**Status: bootstrapped (M0) — lifecycle- and constitution-managed from day
one; the loop itself lands over the following milestones.** See
[`orchestration.md`](./orchestration.md) for the full design specification
and [`implementation-plan.md`](./implementation-plan.md) for the milestone
plan and locked decisions.

## Install the CLI

```bash
uv tool install git+https://github.com/kentra-io/agent-orchestration
docker login ghcr.io        # the daemon image is a private GHCR package
orch daemon start
```

## Quickstart

```bash
orch launch <change-id>            # production: box + real spec-lifecycle plan
orch launch demo --stub            # hermetic demo (stub provider, no box)
orch runs                          # all runs, all projects
orch status <change-id>            # folded JSON: derived state, classified cause, remedy
orch resume <change-id>            # after a pause/death — never re-runs completed milestones
```

Developing on a checkout? `make daemon-image && make daemon-run` stays the
build-from-source path; `orch daemon start --image agent-orchestration-daemon`
runs your local build.

## Shape

- Python 3.12+, managed with [`uv`](https://docs.astral.sh/uv/); the sole
  deliberate language deviation among the (otherwise Go) primitive family,
  because it runs in-process inside Conductor's asyncio engine.
- `orchestration/` — the module's business logic: `harness/` (deterministic
  verification), `launch/` (the execute-change launcher), `resume/` (the
  escalation poll-seam), `mcp/` (the Conductor-MCP operator surface).
- `workflows/` — the Conductor workflow templates (`execute-change.yaml`,
  `milestone.yaml`).
- `tests/` — hermetic Stub-tier tests (a scripted `StubProvider` test double)
  plus a fixture testbed with plantable defects.

MIT.
