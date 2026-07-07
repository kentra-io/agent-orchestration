"""Conductor workflow-control tools — the small, new half of the operator MCP surface.

`orchestration.md` sec 10: "Conductor workflow-control verbs (small, new):
list running workflows, inspect the `Needs human input` queue, resolve/resume
a paused change." Built directly on Conductor's own `CheckpointManager` (real
API) and `orchestration.resume`'s checkpoint/events/watcher modules — no
reimplementation of checkpoint parsing.

"Needs human input" here means: the latest checkpoint for a workflow is
paused on `execute-change.yaml`'s `milestone_step` (its nested ladder
exhausted and crashed at `human_gate` — see `orchestration/resume/README.md`
for the crash-then-resume mechanism this reads back). A checkpoint whose
`current_agent` is anything else is either mid-flight (a periodic checkpoint
taken while the run is still actively progressing) or a plain failure
unrelated to escalation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conductor.engine.checkpoint import CheckpointData, CheckpointManager

from orchestration.resume.checkpoint import (
    CheckpointReadError,
    load_execute_change_checkpoint,
)
from orchestration.resume.events import read_verifier_reports
from orchestration.resume.watcher import (
    EscalationBaseline,
    decide,
)


def list_runs(workflow_path: str | Path | None = None) -> list[dict[str, Any]]:
    """List known checkpoints (one per distinct run) — Conductor's own
    `conductor checkpoints` table, as structured data.

    Args:
        workflow_path: If given, only checkpoints for that workflow file.

    Returns:
        One entry per checkpoint, newest first: `{workflow, current_agent,
        trigger, created_at, error_type, file_path}`.
    """
    checkpoints: list[CheckpointData] = CheckpointManager.list_checkpoints(
        Path(workflow_path) if workflow_path else None
    )
    return [
        {
            "workflow": Path(cp.workflow_path).stem,
            "workflow_path": cp.workflow_path,
            "current_agent": cp.current_agent,
            "trigger": cp.trigger,
            "created_at": cp.created_at,
            "error_type": cp.failure.get("error_type"),
            "file_path": str(cp.file_path),
        }
        for cp in checkpoints
    ]


def inspect_escalation_queue(workflow_path: str | Path) -> dict[str, Any] | None:
    """Inspect the paused `execute-change.yaml` run's escalation state, if any.

    Args:
        workflow_path: Path to the `execute-change.yaml` (or equivalent)
            workflow file whose latest checkpoint should be inspected.

    Returns:
        `None` if there's no checkpoint at all, or the latest checkpoint
        isn't paused on a milestone (i.e. nothing is currently escalated).
        Otherwise: `{"stuck_milestone_id", "completed_milestone_ids",
        "checkpoint_path", "verifier_reports"}` — `verifier_reports` is the
        Verifier's last (up to 3) rendered outputs for the stuck milestone,
        each `{"pass": bool, "notes": str}` (see
        `orchestration.resume.events.read_verifier_reports`), oldest first.
    """
    checkpoint_path = CheckpointManager.find_latest_checkpoint(Path(workflow_path))
    if checkpoint_path is None:
        return None

    try:
        ckpt = load_execute_change_checkpoint(checkpoint_path)
    except CheckpointReadError:
        return None

    if ckpt.stuck_milestone_id is None:
        return None  # not paused mid-milestone -- nothing escalated right now

    raw = CheckpointManager.load_checkpoint(checkpoint_path)
    verifier_reports: list[dict[str, Any]] = []
    if raw.event_log_path and Path(raw.event_log_path).is_file():
        verifier_reports = read_verifier_reports(raw.event_log_path)

    return {
        "stuck_milestone_id": ckpt.stuck_milestone_id,
        "completed_milestone_ids": ckpt.completed_milestone_ids,
        "checkpoint_path": str(checkpoint_path),
        "verifier_reports": verifier_reports,
    }


def resolve_gate(
    baseline: EscalationBaseline,
    status_json: dict[str, Any],
    checkpoint_path: str | Path,
) -> dict[str, Any]:
    """Operator-driven manual resolve — decide what a resume would do, without acting.

    A thin, read-only wrapper over `orchestration.resume.watcher.decide`: an
    operator inspecting the escalation queue via MCP can call this to see
    whether `lifecycle_status` considers the change resolved yet and, if so,
    which resume action would fire and over which milestones — before
    actually triggering `resume_in_place`/`start_fresh_run_over_remaining`
    (kept as separate, explicit actions rather than folded into this
    read-only tool, so an operator inspecting state never accidentally
    triggers a real `conductor` invocation as a side effect of looking).
    """
    result = decide(baseline, status_json, checkpoint_path)
    return {
        "action": result.action,
        "stuck_milestone_id": result.stuck_milestone_id,
        "completed_milestone_ids": result.completed_milestone_ids,
        "remaining_milestones": result.remaining_milestones,
    }
