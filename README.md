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
orch daemon start           # pulls the public GHCR daemon image on first run
```

## Quickstart

```bash
orch validate <change-id>          # daemon-free: summarize the plan's milestones, no run
orch launch <change-id>            # production: box + real spec-lifecycle plan
orch launch demo --stub            # hermetic demo (stub provider, no box)
orch runs                          # all runs, all projects
orch status <change-id>            # folded JSON: derived state, classified cause, remedy
orch resume <change-id>            # after a pause/death — never re-runs completed milestones
```

Developing on a checkout? `make daemon-image && make daemon-run` stays the
build-from-source path; `orch daemon start --image agent-orchestration-daemon`
runs your local build.

### Scope: one run drives one git repository

A run's worktree is created with `git worktree add` **from the repo that holds
the plan**, and that single root is the plan-root, the code-root, and the
commit-root at once — the plan is read from it, the agents' box is mounted at
it (so they cannot write outside it), and each milestone commit runs
`git -C <worktree>`.

So a change is only launchable when **its plan and the code it produces live in
the same repository**. Not supported:

- a change whose deliverable is a **new standalone repo**;
- a change **spanning two repos**, or a multi-module project whose modules have
  **separate git roots**.

This is a **committed design constraint** (ADR-0004), not a roadmap gap:
split such changes by hand (create the repo first, plan inside it; drive the
part that lives in one repo, do the rest manually). See
[#24](https://github.com/kentra-io/agent-orchestration/issues/24) (closed as
by-design) and `orchestration.md` §1 / §13.

## Wiring a consuming project's boxes to the daemon

The daemon is user-scoped (one per host; token minted into
`~/.agent-orchestration/daemon.json` by `orch daemon start`). A project opts
its boxes in — no secret in the file — by adding two lines to its
`.claudebox/config.yaml`:

```yaml
env:
  ORCHESTRATION_DAEMON_URL: http://host.docker.internal:8765
  ORCHESTRATION_DAEMON_TOKEN: ${ORCHESTRATION_DAEMON_TOKEN}
```

Boxes launched **by the daemon** (production `orch launch`) resolve the
`${...}` interpolation automatically — the daemon container carries the token.
For an **interactively started** box, export it into your shell first:

```bash
eval "$(orch daemon env)"   # transient — nothing lands in your shell rc
cb run
```

In-box sessions then reach `orch runs` / `orch status` through the daemon;
without the opt-in, in-box calls can't reach it (the local-registry fallback
is empty inside a box — the registry lives on the host).

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
