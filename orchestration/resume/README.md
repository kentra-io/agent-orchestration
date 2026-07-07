# `orchestration/resume/` — the escalation-resolve poll-seam (M7)

`orchestration.md` sec 7.2: "A human runs a Mode-A session that edits the
spec, constitution, or plan to unblock the change, then approves. Conductor
detects the resolved state by polling `lifecycle status --format json` ...
and resumes from the failed milestone. If the human's edit materially
changed the plan artifact, Conductor re-derives the remaining milestones."

This package is that poll-seam. Read this before wiring a real launcher
against it — several design choices here deviate from (or sharpen) the
M7 brief's literal wording, for reasons recorded below.

## Spec-lifecycle reality check (verified 2026-07-07 against the shipped v0.1.0 CLI)

The brief asks this module to "detect a change that has left `Needs human
input`" via `lifecycle status --format json`. Verified directly against
`spec-lifecycle`'s source (`cmd/lifecycle/main.go`, `internal/status/status.go`):

- The complete, shipped v1 verb set is exactly `init`, `validate`, `approve`,
  `status`, `archive`, `guard`. **There is no `apply` command.** The
  `execute-change.yaml` workflow's `read_plan` step therefore still reads a
  fixture JSON, same as M5 — not a real machine-readable plan surface.
- `status --format json`'s real shape is
  `{"changes": [{"change": ..., "gates": [{"stage": ..., "state": ...,
  "approvedAt": ..., "drifted": [...]}]}]}`. The `state` (`StageState`) enum
  is **exactly** `pending | approved | rejected | skipped` — there is no
  "needs human input" / escalation / paused value anywhere in it.

This is not an oversight to work around — it is the locked design (P7 in
`implementation-plan.md`, sec 7.1 in `orchestration.md`): **"Canonical =
Conductor run-state ... spec-lifecycle is untouched — its statuses are
gates, not run states."** `spec-lifecycle` was never meant to model
Conductor's escalation; this package doesn't ask it to.

So `orchestration.resume.lifecycle_status` watches `status --format json`
for a different, real signal: the change's `plan` stage gate being
**freshly re-approved** (`state == "approved"` with a newer `approvedAt`
than the baseline captured when the watcher started tracking the change).
That's the real, file-canonical trace of "a human, in a Mode-A session,
edited the plan and approved it" — using only shipped `spec-lifecycle`
surfaces, no invented status value. See `lifecycle_status.py`'s module
docstring for the full reasoning.

## Why checkpoint-based resume, not a live `--web-bg` / gate-respond session

Conductor's `human_gate` step has two live modes: an interactive terminal
prompt (blocks correctly with a real TTY attached), or (via `--web`/
`--web-bg`) an HTTP dashboard exposing `/api/gate-status` /
`/api/gate-respond` that a live, long-running process answers while it
stays up. Keeping a Mode-B launcher process alive for the hours-to-days a
real human resolution might take is undesirable operationally, so this
package uses the OTHER documented path (see `orchestration.md`'s errata 7 /
`implementation-plan.md` sec 1.7): **crash-then-resume**.

Empirically verified (2026-07-07, against the pinned fork): running
`conductor run` **without** `--skip-gates`, with `stdin` closed
(`subprocess.DEVNULL`) and `--no-interactive`, hitting a `human_gate` raises
`EOFError` — the process exits non-zero, but not before its periodic
checkpoint (taken at the top of the root loop, `runtime.checkpoint.
every_agent: true`) has already captured `current_agent: "human_gate"` (or,
for `execute-change.yaml`, `"milestone_step"` — the root step whose nested
child raised). This crash **is** the durable pause: no live process, no open
port, just a checkpoint file. `conductor resume <workflow> --skip-gates`
later re-enters exactly at that gate and auto-acknowledges it — which is
correct, not a shortcut: the watcher only ever calls `resume_in_place` /
`start_fresh_run_over_remaining` AFTER `lifecycle_status.is_resolved`
confirms a human genuinely re-approved the plan, so "auto-selecting the
gate's only option" IS the human's decision, already made out-of-band.

This means every hermetic test in `tests/test_resume_watcher.py` and
`tests/test_workflows_flatten.py` drives a **real** `conductor` CLI + the
Stub provider — no mocked subprocess for the actual resume mechanics — only
`orchestration.resume`'s own pure `decide`/`lifecycle_status`/`plan`
functions are exercised with fabricated (not real-CLI-produced)
`status --format json` payloads, since driving a real `spec-lifecycle`
change through refine→design→plan→approve is out of scope for a workflow
resume test.

## Re-deriving the remaining milestones

`orchestration.resume.checkpoint.load_execute_change_checkpoint` reads a
paused run's checkpoint (a thin wrapper over Conductor's own
`CheckpointManager` — real API, not reimplemented) to recover, from the
OLD plan baked into context: which milestones are already committed
(`completed_milestone_ids`) and which one is currently stuck
(`stuck_milestone_id`). `orchestration.resume.plan.derive_remaining_milestones`
then filters the NEW plan's milestone list down to whatever isn't in
`completed_milestone_ids`, **by id, not position** — robust to the human
reordering, inserting, or deleting milestones in their edit; the one
invariant preserved is "never re-run a milestone whose id already
completed."

When the plan hash is unchanged, none of this runs — `decide` returns
`"resume_in_place"` and the checkpoint's own baked-in milestone list is
trusted as-is (this is the common case, and it's a plain `conductor
resume`, no re-derivation machinery involved).

## Reading "the three Verifier reports" (`events.py`)

Nothing new is persisted for the operator MCP's escalation-queue view.
Conductor's event log already records every individual step call (not just
the latest — that's all `context` keeps) as a `agent_completed` event
carrying the full rendered `output`. Root and nested-child workflow runs
share ONE event log file per process (verified empirically), so
`read_verifier_reports` just isolates the `verifier` completions that
happened since the last time `milestone_step` started — i.e. the calls
belonging to whichever milestone is currently stuck, not the whole run's
history.

## What's NOT covered here (explicit scope notes)

- **A real `lifecycle approve` round-trip.** Tests fabricate the
  `status --format json` payload directly rather than driving a real
  `spec-lifecycle` change through its stages — that's `spec-lifecycle`'s
  own test surface, not this module's.
- **The live `--web-bg`/HTTP gate-respond path.** Documented above as a real
  alternative Conductor supports, but not exercised or wired here — the
  crash-then-resume path is what this package implements and tests.
- **A real launcher process** (a long-running daemon that owns
  `EscalationBaseline` capture at change-start and drives
  `poll_until_resolved` on a real interval against real `lifecycle`/
  `conductor` subprocesses). That's M8's launcher's job to wire; this
  package ships the seam it wires against.
