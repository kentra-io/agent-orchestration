"""Tests for `orchestration.mcp.workflow_tools` against a REAL, paused
`execute-change.yaml` run (reuses the same fixture helper as
`test_resume_checkpoint_and_events.py` — real `conductor` + Stub provider,
no mocked subprocess for the Conductor-facing half; `resolve_gate` is
exercised against a fabricated `lifecycle status --format json` payload,
same convention as `test_resume_watcher_integration.py` — see
`orchestration/resume/README.md`'s "spec-lifecycle reality check")."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from test_resume_checkpoint_and_events import (
    EXECUTE_CHANGE_WORKFLOW,
    _produce_escalated_and_crashed_run,
)

from orchestration.mcp.workflow_tools import (
    inspect_escalation_queue,
    list_runs,
    resolve_gate,
)
from orchestration.resume.checkpoint import find_latest_checkpoint
from orchestration.resume.watcher import capture_baseline

CHANGE_ID = "042-test-change"


def _status(state: str, approved_at: str | None) -> dict:
    gate: dict = {"stage": "plan", "state": state}
    if approved_at is not None:
        gate["approvedAt"] = approved_at
    return {"changes": [{"change": CHANGE_ID, "type": "feature", "issue": None, "gates": [gate]}]}


@pytest.fixture
def _escalated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    tmp_dir, plan_path, env = _produce_escalated_and_crashed_run(tmp_path)
    monkeypatch.setenv("TMPDIR", env["TMPDIR"])
    monkeypatch.setattr(tempfile, "tempdir", None)
    return tmp_dir, plan_path, env


class TestListRuns:
    def test_lists_the_paused_run_scoped_to_the_workflow(self, _escalated) -> None:
        _tmp_dir, _plan_path, _env = _escalated
        runs = list_runs(EXECUTE_CHANGE_WORKFLOW)
        assert len(runs) >= 1
        latest = runs[0]  # list_runs / CheckpointManager sorts newest-first
        assert latest["workflow"] == "execute-change"
        assert latest["current_agent"] == "milestone_step"
        assert latest["trigger"] in ("failure", "periodic")

    def test_scoping_filters_by_workflow_stem(self, _escalated, tmp_path: Path) -> None:
        _tmp_dir, _plan_path, _env = _escalated
        # A workflow whose stem was never run has no checkpoints scoped to it
        # -- proves `list_runs`'s `workflow_path` filter actually filters,
        # not just "happens to return everything."
        never_run = tmp_path / "totally-unrelated-workflow-name.yaml"
        assert list_runs(never_run) == []

        # Every entry scoped to `execute-change.yaml` really is one of ITS
        # checkpoints (the nested `milestone.yaml` child writes its OWN,
        # separate checkpoint too -- scoping must not leak those in).
        scoped = list_runs(EXECUTE_CHANGE_WORKFLOW)
        assert scoped
        assert all(r["workflow"] == "execute-change" for r in scoped)


class TestInspectEscalationQueue:
    def test_reports_stuck_milestone_and_verifier_reports(self, _escalated) -> None:
        _tmp_dir, _plan_path, _env = _escalated
        result = inspect_escalation_queue(EXECUTE_CHANGE_WORKFLOW)
        assert result is not None
        assert result["stuck_milestone_id"] == 2
        assert result["completed_milestone_ids"] == [1]
        assert result["verifier_reports"] == [
            {"pass": False, "notes": "m2 fail 1"},
            {"pass": False, "notes": "m2 fail 2"},
            {"pass": False, "notes": "m2 fail 3"},
        ]

    def test_no_checkpoint_at_all_returns_none(self, tmp_path: Path) -> None:
        never_run = tmp_path / "never-run.yaml"
        never_run.write_text("workflow: {name: never-run}", encoding="utf-8")
        assert inspect_escalation_queue(never_run) is None


class TestResolveGate:
    def test_not_yet_resolved(self, _escalated) -> None:
        _tmp_dir, plan_path, _env = _escalated
        checkpoint_path = find_latest_checkpoint(EXECUTE_CHANGE_WORKFLOW)
        assert checkpoint_path is not None
        baseline = capture_baseline(
            CHANGE_ID,
            EXECUTE_CHANGE_WORKFLOW,
            plan_path,
            _status("approved", "2026-07-01T00:00:00Z"),
        )
        out = resolve_gate(baseline, _status("pending", None), checkpoint_path)
        assert out["action"] == "not_resolved"

    def test_resolved_reports_resume_in_place(self, _escalated) -> None:
        _tmp_dir, plan_path, _env = _escalated
        checkpoint_path = find_latest_checkpoint(EXECUTE_CHANGE_WORKFLOW)
        assert checkpoint_path is not None
        baseline = capture_baseline(
            CHANGE_ID,
            EXECUTE_CHANGE_WORKFLOW,
            plan_path,
            _status("approved", "2026-07-01T00:00:00Z"),
        )
        out = resolve_gate(baseline, _status("approved", "2026-07-05T12:00:00Z"), checkpoint_path)
        assert out["action"] == "resume_in_place"
        assert out["stuck_milestone_id"] == 2
        assert out["completed_milestone_ids"] == [1]
