---
name: orchestration-launch
description: Start or resume an agent-orchestration execute-change run from any session (host or claudebox) via the orchestrator daemon. Use when asked to "launch the change", "start the run", "execute the plan", or resume a paused/dead run.
---

# Launching an orchestration run

## Preconditions

- Host: `orch daemon start` (idempotent — pulls the image, generates the token
  into `~/.agent-orchestration/daemon.json`, runs the container).
- From a box: the daemon is reached via `ORCHESTRATION_DAEMON_URL=
  http://host.docker.internal:8765` + `ORCHESTRATION_DAEMON_TOKEN` (env-injected
  via the claudebox `config.yaml env:` pattern) — env always wins over daemon.json.

## Pre-launch check

Before `orch launch`, validate the plan (daemon-free, no run started):

```bash
orch validate <change-id> [--repo /path/to/project]
```

It summarizes the change's milestones (id, title, contract present or not) plus
a total and exits 0 when the plan folds; exit 1 on an unknown/invalid change
(prints the error + available change ids), exit 2 if `lifecycle` is missing from
PATH. Run it from inside the target repo (repo defaults to the git toplevel).

## Launch

```bash
orch launch <change-id> [--repo /path/to/project] [--issue N] [--branch B]
```

Run from inside the target repo (repo defaults to the git toplevel).
Production tier: box enabled, plan from `lifecycle apply <change> --format json`,
async return + dashboard auto-open. Hermetic demo: `orch launch <id> --stub`.

Raw-payload escape hatch (the old curl-era surface, unchanged semantics):

```bash
orch launch --payload '<json>'        # or a file path, or - for stdin
orch launch --payload payload.json --direct   # bypass a down daemon (dev checkout only)
```

The daemon then: runs a box health probe (fails LOUD with a classified cause
— e.g. `oauth-expired → cb login` — instead of dying mid-run), creates the
worktree, provisions the box, assigns a dashboard port, spawns
`conductor run --web`, and registers everything in
`~/.agent-orchestration/runs/`. The response carries the report: pid,
dashboard URL, registry path, log legend. The launcher creates the actual
worktree at `<worktree_root>/<change-id>` (`worktree_root` is optional — it
defaults to `<repo>/.worktrees`).

- `--direct` (with `--payload`) bypasses a down daemon (in-process spawn; the
  daemon reconciles the run's fate later). `worktree_root` MUST live under the
  daemon's mounted code root.

## Resume

```bash
orch resume <change-id> [--repo /path/to/project]
```

404 = nothing registered; 409 = still running (or nothing left to do). The
daemon re-derives remaining milestones from the CURRENT plan — completed
milestone ids are never re-run. After fixing the cause (`cb login`, plan edit
+ approval), resume from the target repo (not necessarily the worktree — the
daemon resumes the change's existing worktree itself).
