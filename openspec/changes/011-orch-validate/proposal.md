---
issue: "kentra-io/agent-orchestration#11"
designSkip: true  # CLI-veneer-only: one new argparse subcommand over an existing, already-trusted internal surface; no architecture touched
type: feature
---

# orch validate <change-id> — standalone plan validation command

## Why

The only way to check a change's plan today is to launch it (`orch launch`
runs an internal pre-flight) or shell out to `lifecycle apply` by hand. Plan
authors want a cheap, standalone "is this plan well-formed and executable?"
answer before ever touching the daemon.

## What Changes

- New `cli-validate` capability: `orch validate <change-id> [--repo PATH]`
  runs the change's tasks.md through the same `lifecycle apply` surface the
  launcher trusts (`load_milestones_from_apply`), prints a per-milestone
  summary (id, title, validation-contract presence), and exits 0/1/2 per the
  CLI's existing exit-code contract (docs/cli-design.md §10).
- No daemon involvement — purely local; the launcher's own pre-flight is
  unchanged.

## Impact

- Code: `orchestration/cli/` (new `validate_cmd.py`, subcommand registration
  in `main.py`); no changes to daemon, launcher, or workflows.
- Dependencies: none added (stdlib argparse veneer; reuses
  `orchestration.resume.plan.load_milestones_from_apply`).
- Docs/skills: README quickstart, docs/cli-design.md command table,
  `orchestration-launch` skill gain the new verb.
