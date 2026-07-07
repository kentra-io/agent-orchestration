"""The Conductor-MCP — `orchestration.md` sec 10 / implementation-plan P8.

Wires `orchestration.mcp.lifecycle_tools` (the six real `spec-lifecycle`
verbs, 1:1) and `orchestration.mcp.workflow_tools` (the small, new
Conductor workflow-control set) onto a stdio MCP server via the standard
Python `mcp` SDK's `FastMCP`.

**This module is the human operator's Mode-A surface** (an interactive
`claude` session equipped with this MCP server — `orchestration.md` sec 10).
It legitimately exposes `record_approval`/`archive_change` — the human's
consent act (sec 7.3) — which is exactly why this server must never be
wired into a Conductor-spawned (Mode-B) agent's own tool surface. See
`tests/test_consent_invariant.py`, which asserts the OPPOSITE surface (no
Mode-B workflow/persona grants these verbs), not this module.

Every tool function here has flat, JSON-schema-friendly parameters (no
dataclasses) so FastMCP can derive its tool schemas automatically — the
richer dataclass-based API (`orchestration.resume.watcher.EscalationBaseline`
etc.) stays internal, reconstructed from flat args at this boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from orchestration.mcp import lifecycle_tools, workflow_tools
from orchestration.resume import lifecycle_status as lifecycle_status_mod
from orchestration.resume import plan as plan_mod
from orchestration.resume.watcher import EscalationBaseline, decide

mcp = FastMCP(
    name="agent-orchestration",
    instructions=(
        "Operator surface for the agent-orchestration module: the six "
        "spec-lifecycle verbs (get_state, validate_stage, record_approval, "
        "archive_change, run_guard) plus Conductor workflow-control "
        "(list_runs, inspect_escalation_queue, resolve_gate). Mode-A "
        "(human-driven) only -- never wire this server into a Conductor-"
        "spawned agent's tool surface."
    ),
)


# ---------------------------------------------------------------------------
# spec-lifecycle verbs, 1:1 (orchestration.md sec 10 / P8)
# ---------------------------------------------------------------------------


@mcp.tool()
def get_state(change: str | None = None) -> dict[str, Any]:
    """Get spec-lifecycle status (`lifecycle status --format json`), optionally scoped."""
    return lifecycle_tools.get_state(change)


@mcp.tool()
def validate_stage(stage: str, change: str | None = None) -> dict[str, Any]:
    """Validate a stage's artifacts (`lifecycle validate --stage <stage> --format json`)."""
    return lifecycle_tools.validate_stage(stage, change)


@mcp.tool()
def record_approval(
    change: str,
    stage: str,
    approved_by: str | None = None,
    notes: str | None = None,
    reject: bool = False,
    design_skip: bool = False,
) -> dict[str, Any]:
    """Approve (or reject) a change's stage gate (`lifecycle approve`).

    THE human consent act (sec 7.3) -- only reachable through this
    interactive, human-driven MCP session.
    """
    return lifecycle_tools.record_approval(
        change,
        stage,
        approved_by=approved_by,
        notes=notes,
        reject=reject,
        design_skip=design_skip,
    )


@mcp.tool()
def archive_change(
    change: str,
    force_gates: bool = False,
    force_conflicts: bool = False,
) -> dict[str, Any]:
    """Archive a change into the living spec (`lifecycle archive`)."""
    return lifecycle_tools.archive_change(
        change, force_gates=force_gates, force_conflicts=force_conflicts
    )


@mcp.tool()
def run_guard() -> dict[str, Any]:
    """Run the repo-wide living-spec replay guard (`lifecycle guard --format json`)."""
    return lifecycle_tools.run_guard()


# ---------------------------------------------------------------------------
# Conductor workflow-control (small, new -- orchestration.md sec 10)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_runs(workflow_path: str | None = None) -> list[dict[str, Any]]:
    """List known Conductor checkpoints (one per run), optionally scoped to one workflow file."""
    return workflow_tools.list_runs(workflow_path)


@mcp.tool()
def inspect_escalation_queue(workflow_path: str) -> dict[str, Any] | None:
    """Inspect a paused `execute-change`-shaped run: which milestone is stuck,
    which are done, and the Verifier's reports for the stuck milestone."""
    return workflow_tools.inspect_escalation_queue(workflow_path)


@mcp.tool()
def resolve_gate(
    change: str,
    workflow_path: str,
    plan_path: str,
    baseline_plan_hash: str,
    baseline_gate_state: str,
    baseline_gate_approved_at: str | None,
    checkpoint_path: str,
) -> dict[str, Any]:
    """Decide (read-only -- does not itself resume) what a resume would do right now.

    Reconstructs an `EscalationBaseline` from flat args (the baseline a real
    launcher captured when execution started -- see
    `orchestration.resume.watcher.capture_baseline`) and re-fetches the
    CURRENT `lifecycle status` to check for a fresh re-approval. Returns
    `{"action": "not_resolved" | "resume_in_place" | "fresh_run_remaining",
    ...}` -- an operator (or a supervising script) triggers the actual
    `conductor resume`/`run` themselves once satisfied; this tool never has
    a side effect on the run.
    """
    baseline = EscalationBaseline(
        change_id=change,
        workflow_path=Path(workflow_path),
        plan_path=Path(plan_path),
        plan_hash=baseline_plan_hash,
        gate_baseline=(
            lifecycle_status_mod.GateSnapshot(
                change=change,
                stage="plan",
                state=baseline_gate_state,
                approved_at=baseline_gate_approved_at,
            )
            if baseline_gate_state
            else None
        ),
    )
    current_status = lifecycle_tools.get_state(change)
    status_json = current_status.get("json") or {"changes": []}
    result = decide(baseline, status_json, checkpoint_path)
    return {
        "action": result.action,
        "stuck_milestone_id": result.stuck_milestone_id,
        "completed_milestone_ids": result.completed_milestone_ids,
        "remaining_milestones": result.remaining_milestones,
        "current_plan_hash": plan_mod.hash_plan(plan_path),
    }


def main() -> None:
    """Entry point for `python -m orchestration.mcp.server` (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
