---
name: orchestration-launch
description: Start or resume an agent-orchestration execute-change run from any session (host or claudebox) via the orchestrator daemon. Use when asked to "launch the change", "start the run", "execute the plan", or resume a paused/dead run.
---

# Launching an orchestration run

## Preconditions

- The daemon is up (host: `make daemon-run` in agent-orchestration; check
  `http://localhost:8765`). From a box, reach it at
  `ORCHESTRATION_DAEMON_URL=http://host.docker.internal:8765`.
- `ORCHESTRATION_DAEMON_TOKEN` must be set (env-injected into boxes via the
  claudebox `config.yaml env:` pattern).

## Launch

```bash
orchestration launch '{
  "repo": "/Users/jony/code/kentra/<project>",
  "change_id": "<issue>-<slug>",
  "worktree_path": "/Users/jony/code/kentra/<project>-wt-<slug>",
  "branch": "<issue>-<slug>",
  "issue": <issue-number>,
  "box": {"enabled": true},
  "conductor": {"workflow": "workflows/execute-change.yaml"}
}'
```

The daemon then: runs a box health probe (fails LOUD with a classified cause
— e.g. `oauth-expired → cb login` — instead of dying mid-run), creates the
worktree, provisions the box, assigns a dashboard port, spawns
`conductor run --web`, and registers everything in
`~/.agent-orchestration/runs/`. The response carries the report: pid,
dashboard URL, registry path, log legend.

- `--direct` bypasses a down daemon (in-process spawn; the daemon reconciles
  the run's fate later). `worktree_path` MUST live under the daemon's mounted
  code root.

## Resume

Resume is CLI-direct in this version (`conductor resume` per the module's
crash-then-resume model) — see `orchestration/resume/README.md`. After fixing
the cause (`cb login`, plan edit + approval), resume from the worktree.
