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
- `milestone.yaml` — the 3-attempt escalation ladder for ONE milestone:
  implementer → gates → verifier → counter → orchestrator/escalate (M5).
  **Directly runnable as its own root workflow** (not just as
  `execute-change.yaml`'s sub-workflow) — see the next section for why that
  matters.

Both land in **M5** (`The ladder — execute-change + milestone templates`),
wired against the StubProvider first (hermetic — `tests/test_workflows_
ladder.py`), then the live cast in M6.

### Why `milestone.yaml` is not (only) run nested — a load-bearing checkpoint-depth finding

P4/ADR-0002 requires the ladder's attempt counter to be crash-safe: a
`kill -9` mid-attempt must not lose the recorded failure count. This is
**verified true when `milestone.yaml` runs as the root workflow** (proven by
`tests/test_workflows_ladder.py::TestKillResume`) — but it is **NOT** true
when `milestone.yaml` runs *nested*, invoked via a `type: workflow` step
(e.g. from `execute-change.yaml`'s `for_each`). Verified directly against
the fork's own source at the pin (`kentra-io/conductor` @ `5461008`,
`src/conductor/engine/workflow.py`):

```python
@property
def _periodic_checkpoints_active(self) -> bool:
    """True when periodic checkpointing applies: root engine, and opt-in.

    Sub-workflow engines never write periodic checkpoints (their state is
    re-run from scratch on resume), and the feature is off unless a
    ``runtime.checkpoint`` trigger is configured.
    """
    return self._subworkflow_depth == 0 and self.config.workflow.runtime.checkpoint.is_enabled
```

`every_agent`/`every_seconds` checkpointing is gated on
`_subworkflow_depth == 0` — a child `WorkflowEngine` created for a `type:
workflow` step (`_execute_subworkflow`/`_execute_subworkflow_with_inputs`)
never takes one, **regardless of its own `runtime.checkpoint` config**. And
`_execute_subworkflow` runs the entire child via `await child_engine.run(...)`
as ONE atomic step from the parent's point of view — so a crash mid-child
is, on `conductor resume`, "re-run from scratch" (the fork's own docstring,
verbatim above).

**Consequence for `execute-change.yaml`:** a crash mid-milestone restarts
*that one milestone's* ladder from attempt 1 — already-completed milestones
are unaffected (the `for_each` item boundary is a root-level step boundary
and IS checkpointed). This is a real, weaker crash-safety unit than
`milestone.yaml` run standalone (which is crash-safe attempt-by-attempt).
It is shipped this way anyway for M5 (matches the plan's §4 template sketch,
and per-milestone atomicity is arguably an acceptable crash-safety grain —
some redundant agent calls, never a lost or double-counted attempt across
the *whole change*). A stricter fix (route-chaining the ladder directly into
`execute-change.yaml`'s root, forgoing `type: workflow` nesting entirely) is
available but out of scope for M5 — flagged here for M7/M8 to revisit if
change-wide per-attempt crash-safety is required.

### ADR-0002 reconciliation — checkpoint-dir relocation is launcher-owned, not template-owned

ADR-0002 says "every shipped workflow template ... relocates the checkpoint
dir to a persistent path." Literally, **a template cannot do this**: there
is no `runtime.checkpoint` (or any other) YAML key for *where* checkpoints
go — only *when* (`every_agent`/`every_seconds`/`keep_last`). The directory
is resolved via `tempfile.gettempdir()` (honors `TMPDIR` on POSIX), which is
a property of the *process invoking* `conductor`, not of the workflow file
it runs. Every template this module ships therefore discharges only the
`every_agent: true` + computed `max_iterations` half of ADR-0002's rule; the
directory-relocation half is discharged by
`orchestration.launch.checkpoint_env.persistent_checkpoint_env` (or
`persistent_checkpoint_subprocess_env`), which every launcher/test in this
repo calls before invoking `conductor run`/`conductor resume` — see
`tests/test_workflows_ladder.py`'s `_base_env` for the pattern the real M8
launcher should follow.

This is a wording gap in ADR-0002 as recorded, not a new rule — the M5
report flags it for the user to consider an ADR amendment (append a
clarifying ADR, per the append-only constitution log; this README note does
not itself amend the ADR).

### `retry:` is real for the live tier, inert under the Stub provider

`milestone.yaml`'s provider-backed steps (`implementer`/`verifier`/
`orchestrator`) each carry a `retry:` block (P5: transient provider errors —
`provider_error`/`timeout` — consume a retry, never the ladder's own
`counter`). Verified against the fork's source at the pin: retry is
resolved **per-provider**, inside each provider's own `execute()` (e.g.
`claude.py`/`claudebox.py`/`copilot.py` each call an internal
`_execute_with_retry`, resolving `AgentDef.retry` into their own retry
config) — there is no engine-level retry wrapper. `conductor.providers.
stub.StubProvider` implements no such loop at all, so a scripted `error`
entry propagates immediately as an unhandled `ProviderError` under the
hermetic Stub tier; it cannot itself exercise "retried, then succeeded."
`tests/test_workflows_ladder.py::TestTransientErrorDoesNotConsumeAnAttempt`
proves what IS true hermetically and is the actual structural guarantee P5
needs: a provider error aborts the step before `counter` ever runs (`counter`
only executes on the gates/verifier FAIL route). The retry loop itself is
real-provider-only and gets its first live exercise at M6.
