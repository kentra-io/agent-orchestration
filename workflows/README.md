# workflows/

Conductor workflow templates for the execution loop.

- `m1b-claudebox-smoke.yaml` — the **M1/M1b seam-proving template**: a
  fixed 2-step ClaudeboxProvider workflow (`echoer` → `confirmer`), each
  step declaring an `output:` schema. Runs three ways:
  - **Live** (`--input box=<box> --input worktree=<path>`, real box, real
    `claude`, costs money) — see `tests/test_workflows_live.py` (opt-in,
    `live` marker, self-skips at zero cost unless
    `M1B_LIVE_BOX`/`M1B_LIVE_WORKTREE` are set).
  - **Stub tier** (`--provider stub` + `CONDUCTOR_STUB_SCRIPT=...`, hermetic,
    every-PR) — `tests/test_workflows_stub.py`.
  - **Kill/resume** — a real `cb exec`/`claude` subprocess killed mid-step,
    `conductor resume` re-running just that step cleanly (checkpoint_resume
    semantics). Manual so far (M1b); not yet a pytest.

  **Context seeding (reused by M5/M8):** `box`/`worktree` are **workflow
  inputs** (`conductor run ... --input box=<id> --input worktree=<path>`),
  not a context key set by an internal step — Conductor's
  `WorkflowContext.build_for_agent()` has no declarative way to land a bare
  top-level `context["box"]` from a `script`/`set` step. ClaudeboxProvider
  (`kentra-patches` @ M1b) falls back to
  `context["workflow"]["input"]["box"/"worktree"]` when the flat key is
  absent, so this works end-to-end through the plain CLI.

  **Checkpoint relocation (S2 answer):** set `TMPDIR` to a persistent,
  host-bind-mounted path before invoking `conductor` — `CheckpointManager`
  and the event-JSONL writer both resolve their directory via
  `tempfile.gettempdir()`, which honors `TMPDIR` on POSIX. No fork patch
  needed; every shipped workflow/launcher should set this (P4/ADR-0002).

- `execute-change.yaml` — the per-change template: reads the plan, `for_each`
  milestone, change-level finish (M5).
- `milestone.yaml` — the per-milestone sub-workflow: implementer → gates →
  verifier → counter → orchestrator/escalate (the 3-attempt ladder, M5).

Lands in **M5** (`The ladder — execute-change + milestone templates`), wired
against the StubProvider first (hermetic), then the live cast in M6.
