"""M7 structural fix: `workflows/execute-change.yaml`'s flattened cursor loop.

DoD (per the M7 brief): "Prove the fix with a hermetic kill/resume test:
kill mid-milestone-2 of a >= 3-milestone plan, resume, assert milestone 1
does not re-execute and the run resumes at milestone 2."

This mirrors `test_workflows_ladder.py::TestKillResume`'s pattern (a real
`kill -9`, real OS-level signal, against the real `conductor` engine + the
Stub provider) but one level up: at the CHANGE level (multiple milestones),
not just within one milestone's ladder.

See `workflows/README.md` "Why milestone.yaml is not (only) run nested" for
the empirical BEFORE finding this replaces, and this module's own S3-spike
evidence (reproduced fresh during M7 review, see the PR body) showing the
OLD nested `for_each` structure re-ran milestone 1 after an identical
kill/resume (`for_each_item_completed` item_keys `['0','0','1','2']`, 5
implementer starts for 3 milestones) where THIS flattened structure does not.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from stub_provider import write_stub_script
from test_workflows_ladder import _agent_names, _base_env, _read_events

REPO_ROOT = Path(__file__).parent.parent
EXECUTE_CHANGE_WORKFLOW = REPO_ROOT / "workflows" / "execute-change.yaml"
CONDUCTOR_BIN = Path(sys.executable).parent / "conductor"


def _write_plan(tmp_path: Path, milestone_ids: list[int]) -> Path:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {"milestones": [{"id": mid, "title": f"work for M{mid}"} for mid in milestone_ids]}
        ),
        encoding="utf-8",
    )
    return plan_path


class TestFlattenKillResume:
    def test_kill_mid_milestone_2_of_3_then_resume_does_not_rerun_milestone_1(
        self, tmp_path: Path
    ) -> None:
        """3-milestone plan, all passing on attempt 1. Kill -9 partway through
        milestone 2's (real, sleeping) `gates` script step -- AFTER milestone
        1 has fully completed. `conductor resume` must continue at milestone
        2 without re-executing milestone 1's implementer/gates/verifier.
        """
        plan_path = _write_plan(tmp_path, [1, 2, 3])
        script_path = write_stub_script(
            tmp_path / "script",
            {
                "implementer": [{"content": {"diff_summary": "did it"}}],
                "verifier": [{"content": {"pass": True, "notes": "good"}}],
            },
        )
        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir()
        env = _base_env(tmp_dir, script_path)

        proc = subprocess.Popen(
            [
                str(CONDUCTOR_BIN),
                "--silent",
                "run",
                str(EXECUTE_CHANGE_WORKFLOW),
                "--provider",
                "stub",
                "--input",
                f"plan_fixture_path={plan_path}",
                # A real, sleeping subprocess (unlike the near-instant stub
                # calls) stretches each milestone's `gates` step over real
                # wall-clock time, giving the poll loop below an actual
                # window to land the kill mid-milestone-2 (after milestone 1
                # has fully committed to context).
                "--input",
                "gates_l1_command=sleep 0.4 && exit 0",
            ],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Wait for milestone 2's implementer to have started (proves
            # milestone 1 fully completed: implementer only reruns for a
            # NEW milestone once the previous one's whole ladder finished
            # and `cursor` advanced) -- then kill mid milestone-2's `gates` sleep.
            deadline = time.monotonic() + 15
            milestone_2_started = False
            while time.monotonic() < deadline:
                started = _agent_names(_read_events(tmp_dir), "agent_started")
                if started.count("implementer") >= 2:
                    milestone_2_started = True
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.01)
            assert milestone_2_started, "milestone 2 never started before the run finished"

            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        assert proc.returncode != 0  # confirms an actual kill, not a clean exit

        pre_kill_events = _read_events(tmp_dir)
        pre_kill_started = _agent_names(pre_kill_events, "agent_started")
        # Milestone 1 completed in full pre-kill; milestone 2's implementer
        # started but `gates` (the sleep) was killed mid-flight.
        assert pre_kill_started.count("implementer") == 2
        assert pre_kill_started.count("verifier") == 1  # only milestone 1's

        resume = subprocess.run(
            [
                str(CONDUCTOR_BIN),
                "--silent",
                "resume",
                str(EXECUTE_CHANGE_WORKFLOW),
                "--provider",
                "stub",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert resume.returncode == 0, f"stdout={resume.stdout!r} stderr={resume.stderr!r}"
        output = json.loads(resume.stdout)
        assert output["milestones_processed"] == 3
        assert output["status"] == "all_milestones_complete"

        all_events = _read_events(tmp_dir)
        all_started = _agent_names(all_events, "agent_started")
        # 4 implementer starts total: milestone 1 (pre-kill, completes),
        # milestone 2's pre-kill attempt (killed mid-`gates`, wasted --
        # milestone.yaml's own child re-runs its ladder from attempt 1 on a
        # crash, which is ACCEPTABLE per the brief -- only ACROSS-milestone
        # re-execution is the bug this flatten fixes), milestone 2's
        # POST-resume fresh attempt (succeeds), and milestone 3's. If
        # milestone 1 had re-executed too (the OLD for_each bug), this
        # would be 5, matching the OLD structure's empirically-reproduced
        # count (see the PR body's before/after event trace).
        assert all_started.count("implementer") == 4, (
            f"unexpected implementer-start count -- full agent_started sequence: {all_started}"
        )
        # Only 3 verifier calls succeed to completion (milestone 2's
        # pre-kill attempt never reached `verifier` -- it was killed at
        # `gates`): milestone 1, milestone 2's post-resume retry, milestone 3.
        assert all_started.count("verifier") == 3

        # THE direct, mechanical proof that milestone 1 specifically did
        # not re-run: `read_plan` and milestone 1's own `cursor` transition
        # (index 0 -> 1) appear exactly once, in the PRE-kill log, and are
        # never repeated post-resume -- resume lands directly on
        # `milestone_step` (milestone 2), never re-touching `read_plan`.
        post_kill_events = all_events[len(pre_kill_events) :]
        post_kill_started = _agent_names(post_kill_events, "agent_started")
        assert "read_plan" not in post_kill_started
        assert post_kill_started[0] == "milestone_step", (
            "resume must land directly on milestone_step (milestone 2), "
            f"not re-derive from read_plan/cursor -- got: {post_kill_started}"
        )
        # milestone 2's fresh retry + milestone 3.
        assert post_kill_started.count("implementer") == 2
        assert post_kill_started.count("cursor") == 2  # M2->M3 advance, M3->finish advance
        assert all_started.count("read_plan") == 1
        # idx0 (M1->M2), idx1 (pre-kill, wasted), idx2 (M2->M3), idx3 (M3->finish).
        assert all_started.count("cursor") == 4
