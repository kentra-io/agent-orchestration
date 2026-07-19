---
name: orchestration-monitor
description: Monitor agent-orchestration runs (launched via the orchestrator daemon). Use when asked "what is the run doing", "is the run stuck/dead", why a conductor/execute-change run failed, or to check run status from any project session.
---

# Monitoring an orchestration run

## The three surfaces

| Question | Surface |
|---|---|
| What is happening RIGHT NOW (turn-by-turn)? | The run's Conductor dashboard — URL from `orchestration runs` (one per live run; dies with the run — that is normal) |
| What state is each run in (all projects)? | `orchestration runs` — or the index page at the daemon URL (default `http://localhost:8765`, from a box: `http://host.docker.internal:8765`) |
| Deep status / why did it die? | `orchestration status <change-id>` — JSON with derived state, classified cause, and remedy |

`orchestration runs`/`status` work even when the daemon is down (they fall
back to reading `~/.agent-orchestration/runs/` directly).

## Reading states

- `running` — healthy. `stalled?` flag = no events AND no worktree writes for
  10+ min; a slow API turn looks the same, so treat as advisory, not a verdict.
- `paused: gate` — the EXPECTED crash-then-resume pause at a human gate.
  Not a death. Resolve via the issue, then resume.
- `dead: oauth-expired` — box OAuth expired. Remedy: `cb login` from the
  worktree, then resume.
- `dead: api-transient` — provider blip killed the run. Remedy: resume.
- `dead: unreconciled` — process gone, exit never observed; the daemon's next
  reconcile pass (or any `orchestration runs` call) classifies it.

## Sharp edges (learned the hard way — issue #7)

- **In-box `docker ps` LIES**: the claudebox socket-proxy filters it (the
  target box and even your own container are omitted). Only HOST `docker ps`
  is authoritative. Never diagnose "box gone" from inside a box.
- **`conductor.stdout.log` is empty during the whole run** — by contract it
  carries only the final JSON result. `conductor.stderr.log` is the live UI.
- **A frozen events JSONL is NOT a stall**: events flush minutes behind.
  Trust the status fold (it joins pid + worktree mtimes), not event freshness.
