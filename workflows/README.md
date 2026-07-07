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

- `execute-change.yaml` — the per-change template: reads the plan, then a
  **flat, root-level milestone-index cursor loop** (M5's original `for_each`
  structure was replaced in **M7** — see the next section) → `milestone.yaml`
  per milestone → change-level finish.
- `milestone.yaml` — the 3-attempt escalation ladder for ONE milestone:
  implementer → gates → verifier → counter → orchestrator/escalate (M5).
  **Directly runnable as its own root workflow** (not just as
  `execute-change.yaml`'s nested call) — see the next section for why that
  matters. Unchanged since M5 — the M7 fix lives entirely in
  `execute-change.yaml`'s outer structure.

Both land in **M5** (`The ladder — execute-change + milestone templates`),
wired against the StubProvider first (hermetic — `tests/test_workflows_
ladder.py`), then the live cast in M6. `execute-change.yaml`'s outer loop was
restructured in **M7** (`tests/test_workflows_flatten.py`) to fix the
crash-safety gap the M5 verifier had flagged (below).

### Why `execute-change.yaml`'s milestone loop is a flat, root-level cursor (M7 fix) — a load-bearing checkpoint-depth finding

P4/ADR-0002 requires the ladder's attempt counter to be crash-safe: a
`kill -9` mid-attempt must not lose the recorded failure count, and — the
change-wide extension of that same requirement — a crash mid-change must
never re-execute an already-completed milestone. `milestone.yaml`'s own
attempt counter is crash-safe **when it runs as the root workflow** (proven
by `tests/test_workflows_ladder.py::TestKillResume`), because Conductor only
takes periodic (`every_agent`) checkpoints at the ROOT engine:

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

(Verified against the fork's source, `src/conductor/engine/workflow.py`.)
`every_agent`/`every_seconds` checkpointing is gated on
`_subworkflow_depth == 0` — a child `WorkflowEngine` created for a `type:
workflow` step never takes one, **regardless of its own `runtime.checkpoint`
config**, and `_execute_subworkflow` runs the entire child via `await
child_engine.run(...)` as ONE atomic step from the parent's point of view —
a crash mid-child is, on `conductor resume`, "re-run from scratch" (the
fork's own docstring, verbatim above).

**M5's ORIGINAL `execute-change.yaml` got this wrong at the change level.**
It sequenced milestones via a `for_each` group over `milestone.yaml` — but
the entire `for_each` group is itself a *single root step*, whose only
checkpoint is taken *before* the group begins (item outputs commit to
context only once the whole group finishes). A crash mid-change therefore
re-ran the `for_each` from milestone 1 on `conductor resume` —
**already-completed milestones re-executed**, not just the in-flight one.
Empirically confirmed twice: by the original M5 verifier (a kill mid-M2 of a
3-milestone run resumed with `for_each` `item_keys ['0','0','1','2']` — item
0 completed twice — and 5 implementer starts for 3 milestones), and
reproduced fresh during the M7 spike with the identical result (see the M7
PR body for that trace). The ladder invariant itself still held (no single
milestone *run* ever exceeded 3 attempts), but this was a materially weaker
crash-safety unit than `milestone.yaml` run standalone, and incompatible
with `orchestration.md` §7.2's "resumes from the failed milestone" —
escalation-resolve (M7) needs the RIGHT milestone to be exactly where a
resume lands, not milestone 1 replayed first.

**The M7 fix: flatten the milestone-index cursor to root depth.**
`execute-change.yaml` no longer has a `for_each:` section at all. Instead:

```
read_plan → cursor (root-level `set` step, index += 1) →
  ├─ milestone_step (nested `type: workflow` call to milestone.yaml,
  │  for milestones[cursor.output.index]) → loops back to cursor
  └─ finish (when cursor.output.index has reached the milestone count)
```

The milestone-index **`cursor`** is its own named, root-level `set` step —
not baked into a `for_each` group's internal bookkeeping — so its periodic
checkpoint (taken at the top of the root loop, before the next step
executes) always reflects exactly how many milestones are already committed
to context. `conductor resume` restores `current_agent_name` to whichever
root step was about to run (`cursor` or `milestone_step`) with every prior
milestone's output intact. **Proven** by
`tests/test_workflows_flatten.py::TestFlattenKillResume` (a real `kill -9`
mid-milestone-2 of a 3-milestone plan, then `conductor resume`): milestone 1
is never re-touched — `read_plan` and milestone 1's own `cursor` transition
appear exactly once, only in the pre-kill event log.

`milestone_step`'s own ladder (inside the nested `milestone.yaml` call) MAY
still restart from attempt 1 if the crash happens mid-ladder — that
per-milestone cost is unchanged from M5 and remains **ACCEPTABLE** (a fresh
child run always recounts from zero, so the 3-attempt invariant is never
violated); only the ACROSS-milestone re-execution is what this flatten
fixes. See `orchestration/resume/README.md` for how the M7 escalation-resolve
poll-seam (`orchestration/resume/`) builds on this same checkpoint shape to
detect a resolved escalation and resume from the correct milestone.

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
