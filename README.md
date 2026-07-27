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

**Status: live — implement→verify→escalate loop, 3-layer verification,
escalation ladder, and github-mirror all shipped (015 merged 2026-07-24).
Open: #30 (gate-time pytest TMPDIR fix), #32 (milestoned-plan-dag
consumption follow-on).** See
[`orchestration.md`](./orchestration.md) for the full design specification
and [`implementation-plan.md`](./implementation-plan.md) for the milestone
plan and locked decisions.

The durable **GitHub-issue mirror** — per-milestone branch push + an
edited-in-place checklist, daemon start/resume/finish/death comments with the
`run-died` label, and close-on-archive — ships via the `github-mirror`
capability (`openspec/changes/015-github-mirror/specs/github-mirror/spec.md`;
design in [`docs/observability-design.md`](./docs/observability-design.md)
§5.4). The mirror is **advisory: when GitHub and local state disagree, local
state wins** — read it via the `orchestration-monitor` /
`orchestration-launch` skills' issue-mirror guides.

## Install the CLI

```bash
uv tool install git+https://github.com/kentra-io/agent-orchestration
orch auth mint               # one-time (macOS): mints a long-lived Claude token into the keychain
orch daemon start             # pulls the public GHCR daemon image on first run
```

### Auth custody: the long-lived Claude token

Agent boxes authenticate with a ~1-year, non-rotating `claude setup-token`
credential, not the interactive OAuth session (harness issue #3). Custody
chain: `orch auth mint` runs the `claude setup-token` flow, verifies it live,
and stores it in the macOS keychain (service `kentra-orch-claude-token`) —
`orch daemon start` runs host-side, so it can read the keychain, and passes
the token **by value** into the daemon container as
`CLAUDE_CODE_LONG_LIVED_TOKEN` (same custody class as
`ORCHESTRATION_DAEMON_TOKEN`; no manual export, no keychain access needed
inside the container). `orch daemon start` refuses to start without a
token — a daemon that starts anyway would launch boxes that silently can't
authenticate, which is the pre-fix failure mode.

> **Verification status (2026-07-27):** the chain is live-verified end-to-end
> (bogus-token negative probe fails in ~3 s classified `oauth-expired`; boxes
> run credential-file-free). Two behaviors are pinned by unit tests only and
> have never been exercised live: (1) `orch daemon start` refusing when
> neither keychain nor environment provides a token
> (`tests/test_cli_daemon_cmd.py::test_cmd_start_errors_without_token` —
> live-testing it means tampering with the operator's keychain), and (2) the
> `needs-human-input` GitHub label on a real mid-run OAuth death
> (`tests/test_daemon_github_mirror.py::test_terminal_oauth_expired_adds_needs_human_label`
> — live-testing it means sabotaging a live run's auth). If you change either
> path, those tests are the spec.

Non-macOS hosts, or the `make daemon-run` build-from-source path, have no
keychain to read — export the token instead:

```bash
CLAUDE_CODE_LONG_LIVED_TOKEN=$(security find-generic-password -s kentra-orch-claude-token -w) make daemon-run
```

(`orch daemon start` also honors `CLAUDE_CODE_LONG_LIVED_TOKEN` in the
environment as an override, ahead of the keychain, on any host.)

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

## Run hardening knobs (2026-07-27)

| Knob | Where | Default | Notes |
|---|---|---|---|
| Stall watchdog | `CONDUCTOR_CLAUDEBOX_STALL_SECONDS` env, or workflow `runtime.provider: {name: claudebox, stall_timeout_seconds: N}` | 600 s | Kills an LLM step whose stream goes silent, raises a *retryable* provider error — the step's `retry:` restarts it without burning a ladder attempt. `<= 0` disables. Liveness is token-granular (`--include-partial-messages`), so long thinking turns don't trip it. Tune once telemetry shows real silence distributions. |
| In-box gates | automatic when the run has a box | on | L1 acceptance + change-level healthcheck execute inside the run's claudebox (`cb exec … bash -lc`) — same toolchain and warm build caches as the agents. Stub tier (no box) stays host-side. |
| Telemetry home | `conductor.tmpdir` launch payload key | `~/.agent-orchestration/runs/<slug>--<change>/conductor/` | events.jsonl, checkpoints, conductor.std{out,err}.log, plan.json — survives worktree cleanup; analyze past runs from here. |
| Model split | `workflows/milestone.yaml` + persona frontmatter | implementer=sonnet, verifier=opus, orchestrator=opus | 2026-07-27 decision; self-contained plans carry the context, the Opus verifier keeps the trust spine. |

### Build-cache facts

Within a run, all milestones share ONE box — gradle/go/npm caches warm up
across milestones, and (with in-box gates) the gate commands reuse them.
Docker *image* pulls (e.g. Testcontainers) already share the host daemon's
image cache across all boxes and runs: in-box `docker` talks to the host
daemon through claudebox's socket proxy. Verify:
`cb exec <box> docker images` lists the same images as host `docker images`.
Cross-run *dependency* caches (fresh box per run) are accepted cold-start
cost for now — revisit with shared named volumes if telemetry shows it
matters.

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
