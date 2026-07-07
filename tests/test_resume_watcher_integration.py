"""End-to-end integration for `orchestration.resume.watcher` — the M7 DoD's
first two bullets:

1. "Escalated change paused ... a Mode-A session edits the plan + approves
   ... the watcher detects it and resumes from the failed milestone"
   (proved via events: completed milestones NOT re-run).
2. "A materially-changed plan re-derives the remaining milestone list
   (hash-compare drives it; test both changed and unchanged cases)."

Uses a REAL `conductor` + Stub provider run to produce a genuinely paused
(crashed-at-`human_gate`) `execute-change.yaml` checkpoint (same mechanism
as `test_resume_checkpoint_and_events.py`), then drives
`orchestration.resume.watcher`'s real `decide`/`poll_until_resolved`
functions against a FABRICATED `lifecycle status --format json` payload
(see `orchestration/resume/README.md`'s "spec-lifecycle reality check" for
why: driving a real spec-lifecycle change through refine/design/plan/approve
is out of scope for a workflow-resume test) to prove the actual resume
actions work end to end.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from stub_provider import write_stub_script
from test_resume_checkpoint_and_events import (
    EXECUTE_CHANGE_WORKFLOW,
    _produce_escalated_and_crashed_run,
)
from test_workflows_ladder import _agent_names, _read_events

from orchestration.resume.checkpoint import find_latest_checkpoint
from orchestration.resume.plan import hash_plan, load_milestones
from orchestration.resume.watcher import (
    capture_baseline,
    decide,
    poll_until_resolved,
    resume_in_place,
    start_fresh_run_over_remaining,
)

CHANGE_ID = "042-test-change"


def _parse_output_json(stdout: str) -> dict:
    """Same convention as `test_workflows_ladder.py`'s helper: `--skip-gates`
    prints an "Auto-selecting: ..." console line ahead of the JSON output
    even under `--silent` -- the JSON is the last top-level object on stdout.
    """
    return json.loads(stdout[stdout.index("{") :])


def _status(state: str, approved_at: str | None) -> dict:
    gate: dict = {"stage": "plan", "state": state}
    if approved_at is not None:
        gate["approvedAt"] = approved_at
    return {"changes": [{"change": CHANGE_ID, "type": "feature", "issue": None, "gates": [gate]}]}


@pytest.fixture
def _escalated_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real, paused (crashed-at-human_gate) execute-change run over a
    3-milestone plan (M1 passes, M2 escalates), plus the baseline captured
    BEFORE the (simulated) human's edit+approval, and the checkpoint path
    (with TMPDIR/tempfile-cache wired so it resolves in THIS process too --
    see test_resume_checkpoint_and_events.py's identical note).
    """
    tmp_dir, plan_path, env = _produce_escalated_and_crashed_run(tmp_path)
    monkeypatch.setenv("TMPDIR", env["TMPDIR"])
    monkeypatch.setattr(tempfile, "tempdir", None)

    checkpoint_path = find_latest_checkpoint(EXECUTE_CHANGE_WORKFLOW)
    assert checkpoint_path is not None

    baseline = capture_baseline(
        CHANGE_ID,
        EXECUTE_CHANGE_WORKFLOW,
        plan_path,
        _status("approved", "2026-07-01T00:00:00Z"),
    )
    return tmp_dir, plan_path, env, checkpoint_path, baseline


class TestDecideNotYetResolved:
    def test_unresolved_status_never_triggers_a_resume_action(self, _escalated_run) -> None:
        _tmp_dir, _plan_path, _env, checkpoint_path, baseline = _escalated_run

        # Still pending, or still the SAME (stale) approval -- either way,
        # nothing has actually changed since the baseline.
        for status in (
            _status("pending", None),
            _status("approved", "2026-07-01T00:00:00Z"),  # identical to baseline
        ):
            decision = decide(baseline, status, checkpoint_path)
            assert decision.action == "not_resolved"


class TestResumeInPlaceUnchangedPlan:
    def test_resolved_with_unchanged_plan_resumes_in_place_without_rerunning_milestone_1(
        self, _escalated_run
    ) -> None:
        tmp_dir, _plan_path, env, checkpoint_path, baseline = _escalated_run

        pre_resume_events = _read_events(tmp_dir)
        pre_resume_started = _agent_names(pre_resume_events, "agent_started")
        # M1 (1 attempt, passes) + M2's full, exhausted 3-attempt ladder
        # (1 solo + 2 orchestrator-guided, all fail) before it escalates.
        assert pre_resume_started.count("implementer") == 4

        resolved_status = _status("approved", "2026-07-05T12:00:00Z")  # fresh approvedAt
        decision = decide(baseline, resolved_status, checkpoint_path)
        assert decision.action == "resume_in_place"
        assert decision.completed_milestone_ids == ["M1"]
        assert decision.stuck_milestone_id == "M2"

        # The action: a real `conductor resume --skip-gates`. The stub
        # script's verifier list resets to index 0 in the fresh process --
        # its first entry ("m1 good" -> pass) now represents "the human
        # fixed the underlying issue, M2's retry passes solo."
        result = resume_in_place(EXECUTE_CHANGE_WORKFLOW, provider="stub", env=env)
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        output = _parse_output_json(result.stdout)
        assert output["milestones_processed"] == 2
        assert output["status"] == "all_milestones_complete"

        all_events = _read_events(tmp_dir)
        post_resume_events = all_events[len(pre_resume_events) :]
        post_resume_started = _agent_names(post_resume_events, "agent_started")
        # Resume lands directly on milestone_step (M2's retry) -- read_plan
        # and milestone 1's own cursor transition are never re-touched.
        assert "read_plan" not in post_resume_started
        assert post_resume_started[0] == "milestone_step"
        assert post_resume_started.count("implementer") == 1  # M2's retry only (2 milestones total)


class TestFreshRunOverRemainingChangedPlan:
    def test_resolved_with_materially_changed_plan_re_derives_remaining_milestones(
        self, tmp_path: Path, _escalated_run
    ) -> None:
        tmp_dir, plan_path, env, checkpoint_path, baseline = _escalated_run

        # Simulate the Mode-A edit: the human reworked M2's summary and
        # added a brand-new M2b -- a materially different plan artifact.
        edited_plan = tmp_path / "plan_edited.json"
        edited_plan.write_text(
            json.dumps(
                {
                    "milestones": [
                        {"milestone_id": "M1", "milestone_summary": "work for M1"},
                        {"milestone_id": "M2", "milestone_summary": "REWORKED scope for M2"},
                        {
                            "milestone_id": "M2b",
                            "milestone_summary": "a new milestone the human added",
                        },
                        {"milestone_id": "M3", "milestone_summary": "work for M3"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        # The baseline's `plan_path` is the ORIGINAL fixture; re-point a
        # baseline at the SAME path but re-write its content in place, since
        # `decide` re-reads `baseline.plan_path` fresh each time (matching
        # how a real launcher would have the human edit the file at a fixed
        # path, not swap paths).
        plan_path.write_text(edited_plan.read_text(encoding="utf-8"), encoding="utf-8")
        assert hash_plan(plan_path) != baseline.plan_hash

        resolved_status = _status("approved", "2026-07-05T12:00:00Z")
        decision = decide(baseline, resolved_status, checkpoint_path)
        assert decision.action == "fresh_run_remaining"
        assert decision.completed_milestone_ids == ["M1"]
        assert [m["milestone_id"] for m in decision.remaining_milestones] == ["M2", "M2b", "M3"]
        assert decision.remaining_milestones[0]["milestone_summary"] == "REWORKED scope for M2"

        # The action: write the filtered fixture, `conductor run` fresh over
        # it. A fresh script (M2's rework passes solo, M2b and M3 pass too)
        # represents "the human's fix actually worked."
        fresh_script = write_stub_script(
            tmp_path / "fresh_script",
            {
                "implementer": [{"content": {"diff_summary": "reworked"}}],
                "verifier": [{"content": {"pass": True, "notes": "good now"}}],
            },
        )
        fresh_env = {**env, "CONDUCTOR_STUB_SCRIPT": str(fresh_script)}
        result = start_fresh_run_over_remaining(
            EXECUTE_CHANGE_WORKFLOW,
            decision.remaining_milestones,
            tmp_path / "remaining_plan.json",
            provider="stub",
            env=fresh_env,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        output = _parse_output_json(result.stdout)
        # 3 remaining milestones (M2, M2b, M3) -- M1 is never scheduled again.
        assert output["milestones_processed"] == 3

        remaining_written = load_milestones(tmp_path / "remaining_plan.json")
        assert [m["milestone_id"] for m in remaining_written] == ["M2", "M2b", "M3"]


class TestPollUntilResolved:
    def test_polls_until_resolved_using_injected_status_source_and_sleep(
        self, _escalated_run
    ) -> None:
        """Pure control-flow proof: no real wall-clock wait, no real
        `lifecycle` subprocess -- `status_source`/`sleep` are fakes.
        """
        _tmp_dir, _plan_path, _env, checkpoint_path, baseline = _escalated_run

        responses = [
            _status("pending", None),
            _status("approved", "2026-07-01T00:00:00Z"),  # stale, same as baseline
            _status("approved", "2026-07-05T12:00:00Z"),  # fresh -- resolved
        ]
        call_log: list[float] = []

        def fake_status_source() -> dict:
            return responses.pop(0)

        def fake_sleep(seconds: float) -> None:
            call_log.append(seconds)

        decision = poll_until_resolved(
            baseline,
            fake_status_source,
            lambda: checkpoint_path,
            sleep=fake_sleep,
            interval_seconds=0.001,
            max_polls=10,
        )
        assert decision.action == "resume_in_place"
        assert call_log == [0.001, 0.001]  # slept between poll 1->2 and 2->3, not after resolving
        assert responses == []  # all three canned responses were consumed, none left over

    def test_gives_up_after_max_polls_if_never_resolved(self, _escalated_run) -> None:
        _tmp_dir, _plan_path, _env, checkpoint_path, baseline = _escalated_run

        decision = poll_until_resolved(
            baseline,
            lambda: _status("pending", None),
            lambda: checkpoint_path,
            sleep=lambda _seconds: None,
            interval_seconds=0.001,
            max_polls=3,
        )
        assert decision.action == "not_resolved"
