"""Escalation-resolve poll-seam — the M7 `orchestration.md` sec 7.2 implementation.

See `README.md` in this package for the design rationale (the spec-lifecycle
reality check, and why resume is checkpoint-based rather than a live
`--web-bg`/gate-respond session). Submodules:

- `plan` — plan-artifact loading, content-hashing, remaining-milestone
  re-derivation. Pure, no Conductor/subprocess dependency.
- `checkpoint` — reads a paused `execute-change.yaml` run's checkpoint
  (thin wrapper over Conductor's own `CheckpointManager`) to find which
  milestone is stuck and which are already done.
- `lifecycle_status` — detects "a human resolved this" from a real
  `lifecycle status --format json` payload.
- `events` — reads the paused run's event JSONL for the escalation queue's
  Verifier reports (used by `orchestration.mcp`).
- `watcher` — ties the above together: the pure `decide`/`poll_until_resolved`
  decision logic, plus the side-effecting `resume_in_place` /
  `start_fresh_run_over_remaining` actions.
"""
