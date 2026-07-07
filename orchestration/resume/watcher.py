"""The poll-seam: detect resolution, decide resume-in-place vs re-derive, act.

`orchestration.md` sec 7.2: "A human runs a Mode-A session that edits the
spec, constitution, or plan to unblock the change, then approves. Conductor
detects the resolved state by polling `lifecycle status --format json` ...
and resumes from the failed milestone. If the human's edit materially
changed the plan artifact, Conductor re-derives the remaining milestones."

This module is deliberately split into three layers so each is testable at
the right grain:

1. **Pure decision** (`decide`) — given a status snapshot and the paused
   run's checkpoint, decide what to do. No I/O beyond reading files already
   named by the caller; no subprocess, no sleeping, no Conductor CLI.
2. **Thin polling loop** (`poll_until_resolved`) — calls `decide` repeatedly
   against an injectable status source, with an injectable sleep function
   so hermetic tests never actually wait on a wall clock.
3. **Side-effecting actions** (`resume_in_place`, `start_fresh_run_over_remaining`)
   — the actual `conductor` subprocess calls. Kept separate from (1)/(2) so
   the decision logic is exercised without spawning anything, and the
   action functions are exercised directly (real `conductor` + the Stub
   provider — hermetic, just not mocked) rather than mocked into
   meaninglessness.

See `orchestration/resume/README.md` for the design rationale (why
checkpoint-based crash-then-resume instead of a live `--web-bg`/gate-respond
session, and the spec-lifecycle reality check behind `lifecycle_status.py`).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from orchestration.resume import checkpoint as checkpoint_mod
from orchestration.resume import lifecycle_status
from orchestration.resume import plan as plan_mod

ResumeAction = Literal["not_resolved", "resume_in_place", "fresh_run_remaining"]


@dataclass(frozen=True)
class EscalationBaseline:
    """Everything captured about a change BEFORE a human resolves its escalation.

    Capture this once execution starts (or as soon as the watcher begins
    tracking a change) — before the human's Mode-A edit, so `plan_hash` and
    `gate_baseline` are a true "before" snapshot to diff against.
    """

    change_id: str
    workflow_path: Path
    plan_path: Path
    plan_hash: str
    gate_baseline: lifecycle_status.GateSnapshot | None


def capture_baseline(
    change_id: str,
    workflow_path: str | Path,
    plan_path: str | Path,
    status_json: dict[str, Any],
    *,
    gate_stage: str = "plan",
) -> EscalationBaseline:
    """Build an `EscalationBaseline` from an already-fetched `lifecycle status` payload."""
    return EscalationBaseline(
        change_id=change_id,
        workflow_path=Path(workflow_path),
        plan_path=Path(plan_path),
        plan_hash=plan_mod.hash_plan(plan_path),
        gate_baseline=lifecycle_status.extract_gate(status_json, change_id, gate_stage),
    )


@dataclass(frozen=True)
class ResumeDecision:
    """What the watcher decided, and the data the corresponding action needs."""

    action: ResumeAction
    remaining_milestones: list[dict[str, Any]] | None = None
    completed_milestone_ids: list[int] | None = None
    stuck_milestone_id: int | None = None


def decide(
    baseline: EscalationBaseline,
    status_json: dict[str, Any],
    checkpoint_path: str | Path,
    *,
    gate_stage: str = "plan",
) -> ResumeDecision:
    """Pure decision: has the human resolved this, and did the plan materially change?

    Args:
        baseline: Captured via `capture_baseline` before the human's edit.
        status_json: A freshly-fetched `lifecycle status --format json` payload.
        checkpoint_path: The paused run's checkpoint file (see
            `orchestration.resume.checkpoint.find_latest_checkpoint`).
        gate_stage: Must match `capture_baseline`'s `gate_stage`.

    Returns:
        `ResumeDecision(action="not_resolved")` if the gate hasn't been
        freshly re-approved yet (see `lifecycle_status.is_resolved` for
        exactly what "freshly" means and why). Otherwise
        `"resume_in_place"` (plan artifact unchanged — trust the checkpoint's
        baked-in milestone list) or `"fresh_run_remaining"` (plan artifact
        changed — re-derive the remaining milestones from the NEW plan,
        excluding whatever the checkpoint says is already completed).
    """
    current_gate = lifecycle_status.extract_gate(status_json, baseline.change_id, gate_stage)
    if not lifecycle_status.is_resolved(baseline.gate_baseline, current_gate):
        return ResumeDecision(action="not_resolved")

    ckpt = checkpoint_mod.load_execute_change_checkpoint(checkpoint_path)
    current_hash = plan_mod.hash_plan(baseline.plan_path)

    if current_hash == baseline.plan_hash:
        return ResumeDecision(
            action="resume_in_place",
            completed_milestone_ids=ckpt.completed_milestone_ids,
            stuck_milestone_id=ckpt.stuck_milestone_id,
        )

    new_milestones = plan_mod.load_milestones(baseline.plan_path)
    remaining = plan_mod.derive_remaining_milestones(new_milestones, ckpt.completed_milestone_ids)
    return ResumeDecision(
        action="fresh_run_remaining",
        remaining_milestones=remaining,
        completed_milestone_ids=ckpt.completed_milestone_ids,
        stuck_milestone_id=ckpt.stuck_milestone_id,
    )


def poll_until_resolved(
    baseline: EscalationBaseline,
    status_source: Callable[[], dict[str, Any]],
    checkpoint_source: Callable[[], str | Path],
    *,
    sleep: Callable[[float], None] = time.sleep,
    interval_seconds: float = 5.0,
    max_polls: int | None = None,
    gate_stage: str = "plan",
) -> ResumeDecision:
    """Poll until resolved (or `max_polls` exhausted), sleeping between polls.

    Args:
        status_source: Called with no args, returns a fresh `lifecycle
            status --format json` payload each time — inject a fake in
            tests (a `list.pop(0)`-backed callable, or similar) so no real
            `lifecycle` subprocess or wall-clock wait is needed hermetically.
        checkpoint_source: Called with no args, returns the current
            checkpoint path to read (a real run's checkpoint path doesn't
            change once paused, but re-resolving it each poll costs nothing
            and stays correct if it does).
        sleep: Injectable so tests never actually wait — pass a no-op or a
            call-counting stub.
        max_polls: `None` = unbounded (real usage); a real caller should
            still pass a sane bound or run this in a supervised loop.

    Returns:
        The first decision whose `action != "not_resolved"`, or a final
        `"not_resolved"` decision once `max_polls` is exhausted.
    """
    polls = 0
    while max_polls is None or polls < max_polls:
        checkpoint_path = checkpoint_source()
        decision = decide(baseline, status_source(), checkpoint_path, gate_stage=gate_stage)
        if decision.action != "not_resolved":
            return decision
        polls += 1
        if max_polls is None or polls < max_polls:
            sleep(interval_seconds)
    return ResumeDecision(action="not_resolved")


# ---------------------------------------------------------------------------
# Side-effecting actions -- real `conductor` subprocess calls. Deliberately
# thin: each is a single, auditable command line. See README.md for why
# `--skip-gates` on the resume call is the correct (not a shortcut) way to
# answer the paused `human_gate`: our own watcher having reached this point
# in the first place IS the human's decision to continue, per
# `lifecycle_status.is_resolved`'s check on a fresh `lifecycle approve`.
# ---------------------------------------------------------------------------


def resume_in_place(
    workflow_path: str | Path,
    *,
    conductor_bin: str = "conductor",
    provider: str | None = None,
    env: dict[str, str] | None = None,
    extra_args: Sequence[str] | None = None,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """`conductor resume <workflow_path> --skip-gates [--provider ...]`.

    Resumes the checkpointed run in place, re-entering exactly at the
    stuck milestone (see `workflows/execute-change.yaml`'s flattened
    cursor -- completed milestones are not re-run).
    """
    args = [conductor_bin, "--silent", "resume", str(workflow_path), "--skip-gates"]
    if provider:
        args += ["--provider", provider]
    if extra_args:
        args += list(extra_args)
    return subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )


def start_fresh_run_over_remaining(
    workflow_path: str | Path,
    remaining_milestones: list[dict[str, Any]],
    plan_fixture_dest: str | Path,
    *,
    conductor_bin: str = "conductor",
    provider: str | None = None,
    env: dict[str, str] | None = None,
    extra_inputs: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Materialize the re-derived plan, then `conductor run` fresh over it.

    Used instead of `resume_in_place` when the plan artifact materially
    changed (`decide` returned `"fresh_run_remaining"`): the checkpoint's
    baked-in `read_plan.output.milestones` reflects the STALE plan, so a
    plain resume would continue against outdated milestone data. Starting
    fresh over just the filtered/remaining list (cursor starts at 0 over
    THIS list, which already excludes everything completed) achieves the
    same "don't redo completed work" outcome without depending on
    Conductor's checkpoint restoring stale context.
    """
    fixture_path = plan_mod.write_plan_fixture(plan_fixture_dest, remaining_milestones)
    args = [
        conductor_bin,
        "--silent",
        "run",
        str(workflow_path),
        "--input",
        f"plan_fixture_path={fixture_path}",
    ]
    if provider:
        args += ["--provider", provider]
    for key, value in (extra_inputs or {}).items():
        args += ["--input", f"{key}={value}"]
    return subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
