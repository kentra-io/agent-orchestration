"""Reading a paused run's event JSONL for the escalation queue's Verifier reports.

`orchestration.md` sec 10: the operator MCP's escalation-queue view must
show "the three Verifier reports" for a paused change. Nothing new needs to
be persisted for this — Conductor's own event log already records every
individual step call (not just the latest, which is all `context` keeps),
via one `agent_completed` event per call with the full rendered `output`
(verified directly against the fork's source, `engine/workflow.py`:
`self._emit("agent_completed", {"agent_name": ..., "output": output.content, ...})`).
Root and nested-child workflow runs share ONE event log file per process
(verified empirically — a nested `milestone.yaml` call's events land in the
SAME `*.events.jsonl` the root `execute-change` run writes, not a separate
file), so reading "the last N `verifier` completions for the currently-stuck
milestone" only requires isolating the most recent `milestone_step` attempt
window within that one log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_event_lines(event_log_path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in Path(event_log_path).read_text(encoding="utf-8").splitlines():
        if line:
            events.append(json.loads(line))
    return events


def read_verifier_reports(
    event_log_path: str | Path,
    milestone_step_name: str = "milestone_step",
    verifier_step_name: str = "verifier",
) -> list[dict[str, Any]]:
    """Return the Verifier's rendered outputs for the CURRENTLY stuck milestone, in call order.

    Args:
        event_log_path: Path to the run's `*.events.jsonl` (root event log —
            nested `type: workflow` calls share it, see module docstring).
        milestone_step_name: The root step name that invokes one milestone's
            ladder (`execute-change.yaml`'s `milestone_step`).
        verifier_step_name: The nested ladder's judging step name
            (`milestone.yaml`'s `verifier`).

    Returns:
        Each `verifier` call's `output` dict (`{"pass": bool, "notes": str}`)
        since the LAST `agent_started` event for `milestone_step_name` —
        i.e. only the calls belonging to the milestone the run is currently
        paused/stuck on, not every verifier call across the whole change.
        Empty if `milestone_step_name` never started, or no `verifier` call
        happened since it last did.
    """
    events = _read_event_lines(event_log_path)

    last_milestone_start = -1
    for i, event in enumerate(events):
        if (
            event.get("type") == "agent_started"
            and event.get("data", {}).get("agent_name") == milestone_step_name
        ):
            last_milestone_start = i

    if last_milestone_start == -1:
        return []

    return [
        event["data"]["output"]
        for event in events[last_milestone_start:]
        if event.get("type") == "agent_completed"
        and event.get("data", {}).get("agent_name") == verifier_step_name
    ]
