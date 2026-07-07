"""Reading a paused `execute-change` run's checkpoint.

A thin wrapper over Conductor's own `CheckpointManager`.

M7's flattened `workflows/execute-change.yaml` (see its header comment and
`workflows/README.md`) puts the milestone-index `cursor` at ROOT workflow
depth specifically so its periodic checkpoint always reflects exactly how
many milestones are already committed to context. This module reads that
checkpoint back (via Conductor's own `conductor.engine.checkpoint.
CheckpointManager` — real API, not reimplemented) to answer the two
questions the resume seam needs:

1. Which milestone is the run paused/stuck on right now?
2. Which milestone ids are already done (so a re-derived plan can exclude
   them — see `orchestration.resume.plan.derive_remaining_milestones`)?

No Conductor *engine* import here beyond `CheckpointManager` (a pure
file-I/O class per its own docstring — "all methods are static, the
manager carries no instance state") — this module does not run a workflow,
it only reads the JSON a run already wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from conductor.engine.checkpoint import CheckpointData, CheckpointManager


class CheckpointReadError(ValueError):
    """A checkpoint exists but doesn't have the shape this module expects.

    Raised only for a checkpoint that is NOT paused on `execute-change.yaml`'s
    flattened structure (e.g. one from an unrelated workflow, or one
    predating the M7 flatten) — a genuine "I don't know how to read this"
    condition, not a missing-file case (that is a plain `None` return from
    the finder functions).
    """


@dataclass(frozen=True)
class ExecuteChangeCheckpoint:
    """The parts of an `execute-change.yaml` checkpoint the resume seam needs."""

    file_path: Path
    current_agent: str
    """The step that was about to run (periodic) or failed (failure-trigger)."""
    plan_fixture_path: str
    """`workflow.input.plan_fixture_path` at the time this checkpoint was taken."""
    milestones: list[dict[str, Any]]
    """`read_plan.output.milestones` — the OLD plan's full list, baked into context."""
    cursor_index: int
    """`cursor.output.index` — how many milestones are already committed (0 if
    `cursor` has not run yet, e.g. a checkpoint taken at `read_plan`)."""

    @property
    def completed_milestone_ids(self) -> list[int]:
        """IDs of milestones already committed to context (never re-run on resume)."""
        return [m["id"] for m in self.milestones[: self.cursor_index]]

    @property
    def stuck_milestone_id(self) -> int | None:
        """The milestone id the run is paused/stuck on, or `None` if between milestones."""
        if self.current_agent != "milestone_step":
            return None
        if self.cursor_index >= len(self.milestones):
            return None
        return self.milestones[self.cursor_index]["id"]


def find_latest_checkpoint(workflow_path: str | Path) -> Path | None:
    """Find the most recent checkpoint file for `workflow_path` (may be `None`)."""
    return CheckpointManager.find_latest_checkpoint(Path(workflow_path))


def load_execute_change_checkpoint(checkpoint_path: str | Path) -> ExecuteChangeCheckpoint:
    """Parse a checkpoint file into the shape the resume seam needs.

    Raises:
        CheckpointReadError: the checkpoint is not one of ours (missing
            `read_plan`/`plan_fixture_path` context — e.g. a checkpoint from
            a different workflow, or one taken before `read_plan` ever ran).
    """
    data: CheckpointData = CheckpointManager.load_checkpoint(Path(checkpoint_path))
    agent_outputs = data.context.get("agent_outputs", {})

    read_plan_output = agent_outputs.get("read_plan")
    if not isinstance(read_plan_output, dict) or "milestones" not in read_plan_output:
        raise CheckpointReadError(
            f"checkpoint {checkpoint_path} has no 'read_plan' output with a "
            "'milestones' list yet — resume it with plain `conductor resume` "
            "(nothing milestone-shaped has run)"
        )

    plan_fixture_path = data.inputs.get("plan_fixture_path")
    if not isinstance(plan_fixture_path, str):
        raise CheckpointReadError(
            f"checkpoint {checkpoint_path} inputs have no string 'plan_fixture_path'"
        )

    # `cursor.output.index` is ALREADY the up-to-date count of committed
    # milestones by the time it's stored (the `set` step's own template
    # computes `previous + 1` — see `workflows/execute-change.yaml`'s
    # `cursor` step) — read it as-is, do NOT re-increment here.
    cursor_output = agent_outputs.get("cursor")
    cursor_index = cursor_output.get("index", 0) if isinstance(cursor_output, dict) else 0

    return ExecuteChangeCheckpoint(
        file_path=Path(checkpoint_path),
        current_agent=data.current_agent,
        plan_fixture_path=plan_fixture_path,
        milestones=read_plan_output["milestones"],
        cursor_index=max(cursor_index, 0),
    )
