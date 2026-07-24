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

## The issue mirror (what a launch/resume posts)

**The mirror is a best-effort projection; when GitHub and local state
disagree, local state wins.** The issue is a read-only projection of local
state (the registry / `orch status`, the run branch, the change folder) —
never the source of truth, never synced back.

A launch/resume drives these writes to the change's issue:

- **On launch** — the daemon posts a run-**started** comment (a **resumed**
  variant on `orch resume`) after it adopts the run.
- **Per milestone** — the workflow **pushes** the milestone commit to the run
  branch and edits a single **checklist** comment in place (one checkbox per
  milestone). A push that fails is annotated `(local-only: push failed — …)`,
  never silently presented as being on GitHub.
- **On finish** — a run-**finished** comment on a `success` exit; a
  **death** comment + the `run-died` label on a death classification.
- **On archive** — the archive hand-off **closes** the issue.

### Which flags gate the writes

Every mirror write defaults to a hermetic `dry_run` that performs no `gh`
call, no network I/O, and needs no token — so the Stub tier exercises the
path without writing. The **real** writes happen only in the production (box)
tier and only when a **repo and issue are resolved**: the launcher derives
`owner/repo` from the repo's `origin` remote (an explicit `repo_gh` payload
field wins), threads `notify_repo`/`notify_issue`/`branch`, and flips
`push_dry_run`/`commit_dry_run`/`notify_dry_run` false. Pass `--issue N` /
`--branch B` to bind them; without a resolved repo+issue the mirror stays
dry-run and nothing is written.

### Absent auth degrades to a logged failure (ADR-0005)

GitHub side effects authenticate as the bot identity
(`KENTRA_BOT_GH_TOKEN`), are independent best effort, and default to a
hermetic dry-run. With no token or no reachability, each write is **attempted,
its failure recorded, and never raised** — the run still completes on its
local commits and reaches its normal terminal state. A quiet issue is a
mirror failure, not a run failure; confirm the run itself via
`orch status <change-id>`.
