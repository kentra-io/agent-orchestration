"""`orchestration.resume.checkpoint` and `orchestration.resume.events` against
a REAL paused `execute-change.yaml` run (real `conductor` + Stub provider;
"real" here means the actual engine/checkpoint/event-log machinery, not a
real LLM/box/network -- see tests/test_workflows_ladder.py's own docstring
for the same hermetic-tier convention this reuses).

Produces one genuinely-escalated, genuinely-crashed run (mirroring the
crash-then-resume mechanism documented in `orchestration/resume/README.md`)
and exercises both modules against its real checkpoint + event log.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from stub_provider import write_stub_script

from orchestration.launch.checkpoint_env import persistent_checkpoint_env
from orchestration.resume.checkpoint import (
    find_latest_checkpoint,
    load_execute_change_checkpoint,
)
from orchestration.resume.events import read_verifier_reports

REPO_ROOT = Path(__file__).parent.parent
EXECUTE_CHANGE_WORKFLOW = REPO_ROOT / "workflows" / "execute-change.yaml"
CONDUCTOR_BIN = Path(sys.executable).parent / "conductor"
VENV_BIN = Path(sys.executable).parent


def _write_plan(tmp_path: Path, milestone_ids: list[int]) -> Path:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {"milestones": [{"id": mid, "title": f"work for M{mid}"} for mid in milestone_ids]}
        ),
        encoding="utf-8",
    )
    return plan_path


def _produce_escalated_and_crashed_run(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Run a 2-milestone execute-change where M1 passes and M2 escalates
    (3 verifier fails), crashing at M2's nested `human_gate` (EOFError, no
    TTY, no `--skip-gates`) -- exactly the mechanism `orchestration.resume.
    watcher`'s README documents. Returns (tmp_dir, workflow_path, env).

    Deliberately only 2 milestones (not 3+): the Stub provider's per-step
    call cursor resets to index 0 in the RESUMED process (see
    `test_workflows_ladder.py::TestKillResume`'s docstring for the same,
    load-bearing caveat) -- with a 3rd milestone, the resumed process's
    verifier calls would keep consuming the SAME scripted list from index 0,
    making it impossible to script "M2's retry passes AND M3 passes" without
    conflating index positions that mean different things in the two
    processes. Stopping at M2 (the escalated, to-be-retried milestone) sidesteps
    that entirely -- what these tests need to prove (M1 never re-runs; the
    retry actually re-attempts M2) doesn't need a 3rd milestone.
    """
    plan_path = _write_plan(tmp_path, [1, 2])
    script_path = write_stub_script(
        tmp_path / "script",
        {
            "implementer": [{"content": {"diff_summary": "attempt", "halt": "none"}}],
            "verifier": [
                {
                    "content": {
                        "pass": True,
                        "notes": "m1 good",
                        "score": 1.0,
                        "violations": "none",
                    }
                },
                {
                    "content": {
                        "pass": False,
                        "notes": "m2 fail 1",
                        "score": 0.2,
                        "violations": "undeclared deviation: touched out-of-path file",
                    }
                },
                {
                    "content": {
                        "pass": False,
                        "notes": "m2 fail 2",
                        "score": 0.2,
                        "violations": "undeclared deviation: touched out-of-path file",
                    }
                },
                {
                    "content": {
                        "pass": False,
                        "notes": "m2 fail 3",
                        "score": 0.2,
                        "violations": "undeclared deviation: touched out-of-path file",
                    }
                },
            ],
            "orchestrator": [{"content": {"guidance": "try again", "infeasible": False}}],
        },
    )
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    env = {
        "PATH": f"{VENV_BIN}:/usr/bin:/bin",
        "HOME": str(tmp_dir),
        "CONDUCTOR_STUB_SCRIPT": str(script_path),
        **persistent_checkpoint_env(tmp_dir / "checkpoints"),
    }

    result = subprocess.run(
        [
            str(CONDUCTOR_BIN),
            "--silent",
            "run",
            str(EXECUTE_CHANGE_WORKFLOW),
            "--provider",
            "stub",
            "--no-interactive",
            "--input",
            f"plan_fixture_path={plan_path}",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0, "expected the run to crash at M2's human_gate (EOFError)"
    assert "EOFError" in result.stderr
    return tmp_dir, plan_path, env


class TestExecuteChangeCheckpoint:
    def test_reads_stuck_milestone_and_completed_ids_from_a_real_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tmp_dir, _plan_path, env = _produce_escalated_and_crashed_run(tmp_path)

        # `CheckpointManager` resolves its directory via `tempfile.
        # gettempdir()` -- the checkpoint was written by the subprocess
        # under its own (relocated) `TMPDIR`; reading it back from THIS
        # (pytest) process needs the same `TMPDIR` honored here too, exactly
        # like a real launcher would set it for both the `conductor`
        # subprocess and its own resume seam. `tempfile.gettempdir()` caches
        # its result in the `tempfile.tempdir` module global on first call
        # (per-process, not per-env-read) -- `monkeypatch.setenv` alone
        # doesn't invalidate that cache, so it must be cleared too.
        monkeypatch.setenv("TMPDIR", env["TMPDIR"])
        monkeypatch.setattr(tempfile, "tempdir", None)

        checkpoint_path = find_latest_checkpoint(EXECUTE_CHANGE_WORKFLOW)
        assert checkpoint_path is not None
        # The checkpoint really does live under our own persistent TMPDIR,
        # not the platform default -- confirms `persistent_checkpoint_env`
        # actually took effect for this run (P4/ADR-0002).
        assert tmp_dir in checkpoint_path.parents

        ckpt = load_execute_change_checkpoint(checkpoint_path)
        assert ckpt.current_agent == "milestone_step"
        assert ckpt.stuck_milestone_id == 2
        assert ckpt.completed_milestone_ids == [1]


class TestReadVerifierReports:
    def test_reads_the_three_verifier_reports_for_the_stuck_milestone(self, tmp_path: Path) -> None:
        tmp_dir, _plan_path, _env = _produce_escalated_and_crashed_run(tmp_path)

        event_logs = sorted(tmp_dir.rglob("*.events.jsonl"))
        assert len(event_logs) == 1, "root and nested-child events share one log file"

        reports = read_verifier_reports(event_logs[0])
        assert reports == [
            {
                "pass": False,
                "notes": "m2 fail 1",
                "score": 0.2,
                "violations": "undeclared deviation: touched out-of-path file",
            },
            {
                "pass": False,
                "notes": "m2 fail 2",
                "score": 0.2,
                "violations": "undeclared deviation: touched out-of-path file",
            },
            {
                "pass": False,
                "notes": "m2 fail 3",
                "score": 0.2,
                "violations": "undeclared deviation: touched out-of-path file",
            },
        ]
        # Milestone 1's PASSING verifier call is excluded -- only the calls
        # since `milestone_step` last started (i.e. milestone 2's) count.
        assert {
            "pass": True,
            "notes": "m1 good",
            "score": 1.0,
            "violations": "none",
        } not in reports
