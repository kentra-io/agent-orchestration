---
name: orchestration-monitor
description: Monitor agent-orchestration runs (launched via the orchestrator daemon). Use when asked "what is the run doing", "is the run stuck/dead", why a conductor/execute-change run failed, or to check run status from any project session.
---

# Monitoring an orchestration run

## The three surfaces

| Question | Surface |
|---|---|
| What is happening RIGHT NOW (turn-by-turn)? | The run's Conductor dashboard — URL from `orch runs` (one per live run; dies with the run — that is normal) |
| What state is each run in (all projects)? | `orch runs` — or the index page at the daemon URL (default `http://localhost:8765`, from a box: `http://host.docker.internal:8765`) |
| Deep status / why did it die? | `orch status <change-id>` — JSON with derived state, classified cause, and remedy |
| Is the daemon itself up? | `orch daemon status` (container state + `/runs` health); `orch daemon logs -f` for the daemon's own log tail |

`orch runs`/`status` work even when the daemon is down (they fall
back to reading `~/.agent-orchestration/runs/` directly). `orchestration` is
still registered as an alias for every subcommand (`orchestration runs`,
`orchestration status <change-id>`, ...).

## Reading states

- `running` — healthy. `stalled?` flag = no events AND no worktree writes for
  10+ min; a slow API turn looks the same, so treat as advisory, not a verdict.
- `paused: gate` — the EXPECTED crash-then-resume pause at a human gate.
  Not a death. Resolve via the issue, then resume.
- `dead: oauth-expired` — box OAuth expired. Remedy: `cb login` from the
  worktree, then resume.
- `dead: api-transient` — provider blip killed the run. Remedy: resume.
- `dead: unknown` — the run died with an unrecognized error; the classified
  cause was not one of the known kinds. Read `orch status <change-id>`
  for the raw detail tail, then decide (often a resume is safe).
- `dead: unreconciled` — process gone, exit never observed; the daemon's next
  reconcile pass (or any `orch runs` call) classifies it.

## The issue mirror (reading guide)

**The mirror is a best-effort projection; when GitHub and local state
disagree, local state wins.** Every mirror write is best-effort, so the
issue can lag or diverge from reality. The authoritative surfaces are all
local: `orch status <change-id>` / `orch runs` (the registry + derived
state), the run branch's commits, and the lifecycle artifacts in the change
folder. Never treat the issue as the source of truth — it is a read-only
projection of local state, never synced back the other way.

### Known divergence shapes → the local surface that answers them

| What the issue shows | What it really means | Where the truth is |
|---|---|---|
| Checklist item unticked, but the milestone is done | A checklist (mirror) write failed | `git log` on the run branch / `orch status <change-id>` |
| A checklist item annotated `(local-only: push failed — …)` | The commit landed locally but the push failed — the branch on GitHub is behind | `git log` on the run branch; a later successful push publishes the accumulated branch |
| No run-started comment, but there is checklist progress | Likely a `--direct` launch (no daemon adopt), or the start write failed | The registry / `orch runs` — check the entry exists and is `running` |
| No comments at all | Absent/expired token, or no repo+issue resolved at launch | `orch status <change-id>`; production launches need `gh` auth (ADR-0005) |

### Who posts what

- **Workflow** (in-run, per milestone): the branch **push** and the
  **checklist tick** — one comment edited in place.
- **Daemon** (process-level truths a workflow can't self-report): the
  run-**started** comment (and a **resumed** variant on resume), the
  run-**finished** comment on a `success` exit, and the **death** comment +
  `run-died` label on a death classification.
- **Archive hand-off**: **closes** the issue with a closing comment on a
  successful archive.

### Label taxonomy (two labels, never conflated)

- **`run-died`** — an infrastructure/runtime failure. Remedy: **fix the
  infra, resume**. The death comment carries the classified cause, the
  remedy, and the real error text (never the masked "exited code 1, no
  stderr").
- **`needs-human-input`** — a ladder-exhausted plan escalation. Remedy:
  **fix the plan, approve, resume**. A `gate-pause` exit is by design and is
  *not* a death — it gets neither label nor a death comment.

### Checklist / marker mechanics

The checklist is a **single** comment, edited in place — located
idempotently by a stable first-line HTML marker
`<!-- agent-orchestration:mirror:<change_id> -->`. A passing milestone never
posts a new comment; each tick re-renders the whole body from the milestone
manifest plus the checked-state parsed out of the existing comment. A
garbled or hand-edited comment self-heals on the next full re-render. The
body carries a standing footer stating the mirror is a best-effort
projection and naming `orch status <change-id>` as the authoritative check.

### The `--direct` asymmetry

A `--direct` launch (daemon down or bypassed) is invisible to the live
supervisor — nothing adopts it, so **no run-started comment is posted** until
a daemon lazily `reconcile`s the run's fate later. Such a run's issue shows
**checklist progress (workflow-side) without a start comment (daemon-side)**
until reconcile catches up. A missing start comment is therefore not a death
signal — check the registry.

## Sharp edges (learned the hard way — issue #7)

- **In-box `docker ps` LIES**: the claudebox socket-proxy filters it (the
  target box and even your own container are omitted). Only HOST `docker ps`
  is authoritative. Never diagnose "box gone" from inside a box.
- **`conductor.stdout.log` is empty during the whole run** — by contract it
  carries only the final JSON result. `conductor.stderr.log` is the live UI.
- **A frozen events JSONL is NOT a stall**: events flush minutes behind.
  Trust the status fold (it joins pid + worktree mtimes), not event freshness.
