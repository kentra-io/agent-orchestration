"""M8 concurrency DoD: two changes run concurrently in separate
worktrees/processes with zero interference, proven by git-ACID interleaved
commits (implementation-plan.md M8: "two changes run concurrently in
separate worktrees/boxes/processes with zero interference (git-ACID proven
by interleaved commits)").

Mechanism (documented, per the M8 brief's "pick the simplest honest
mechanism"): each change's `milestone.yaml` `gates` step runs a REAL shell
command as its L1 acceptance check (`gates_l1_command`, an existing
workflow input -- see `workflows/milestone.yaml`'s `gates` step). This test
scripts that command to sleep briefly (stretching the run over real
wall-clock time, giving the poll loop below an actual window to observe
both processes alive at once -- the same technique
`test_workflows_flatten.py` uses) and then write + `git commit` a
change-unique file INSIDE that change's own worktree. This is a real git
commit made by a real subprocess in a real worktree while a sibling change's
process is also running -- not a simulation of the Implementer, but an
honest stand-in for "the Implementer made a commit in its own worktree"
that needs no live LLM/box to exercise.

Both changes are launched via `orchestration.launch.change.launch(...,
wait=False)` -- the P10 process-per-change mechanism: `wait: false` is what
makes two `launch()` calls from the same test process genuinely concurrent
(two child OS processes) rather than accidentally serialized by a blocking
launcher.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from launch_testbed import git_env, init_repo, write_plan_fixture
from stub_provider import write_stub_script

from orchestration.launch.change import launch

REPO_ROOT = Path(__file__).parent.parent
EXECUTE_CHANGE_WORKFLOW = REPO_ROOT / "workflows" / "execute-change.yaml"

POLL_TIMEOUT_SECONDS = 15


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal (shouldn't happen for our own child)
    return True


def _launch_change(tmp_path: Path, repo: Path, change_id: str, script_path: Path) -> dict:
    plan_path = tmp_path / f"plan-{change_id}.json"
    plan = write_plan_fixture(plan_path, [{"id": 1, "title": "do the work"}])
    config = {
        "repo": str(repo),
        "change_id": change_id,
        "worktree_root": str(tmp_path / "worktrees"),
        "box": {"enabled": False},
        "conductor": {
            "workflow": str(EXECUTE_CHANGE_WORKFLOW),
            "provider": "stub",
            "plan_fixture_path": str(plan),
            "tmpdir": str(tmp_path / f"conductor-tmp-{change_id}"),
            "env": {"CONDUCTOR_STUB_SCRIPT": str(script_path), **git_env()},
            "inputs": {
                # The honest git-ACID mechanism this test's DoD relies on
                # (see the module docstring): a REAL commit, in THIS
                # change's own worktree, made by a real subprocess, with a
                # deliberate sleep so both changes' commits overlap in
                # wall-clock time.
                "gates_l1_command": (
                    f"sleep 0.3 && echo work > {change_id}.txt && git add -A "
                    f"&& git commit -q -m 'implementer work ({change_id})' && exit 0"
                ),
            },
        },
        "wait": False,
    }
    return launch(config)


class TestTwoChangesRunConcurrentlyWithoutInterference:
    def test_interleaved_commits_stay_isolated_per_worktree(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        main_sha_before = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "main"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        script_path = write_stub_script(
            tmp_path / "script",
            {
                "implementer": [{"content": {"diff_summary": "did it", "halt": "none"}}],
                "verifier": [
                    {
                        "content": {
                            "pass": True,
                            "notes": "good",
                            "score": 1.0,
                            "violations": "none",
                        }
                    }
                ],
            },
        )

        report_a = _launch_change(tmp_path, repo, "change-a", script_path)
        report_b = _launch_change(tmp_path, repo, "change-b", script_path)

        pid_a, pid_b = report_a["pid"], report_b["pid"]
        assert pid_a is not None and pid_b is not None
        assert pid_a != pid_b

        # Both PIDs alive simultaneously at least once -- the actual
        # concurrency proof, not just "both eventually finished".
        both_alive_at_once = False
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _pid_alive(pid_a) and _pid_alive(pid_b):
                both_alive_at_once = True
                break
            if not _pid_alive(pid_a) and not _pid_alive(pid_b):
                break
            time.sleep(0.01)
        assert both_alive_at_once, "never observed both change processes alive at the same time"

        _, status_a = os.waitpid(pid_a, 0)
        _, status_b = os.waitpid(pid_b, 0)
        assert os.WIFEXITED(status_a) and os.WEXITSTATUS(status_a) == 0, Path(
            report_a["stderr_path"]
        ).read_text(encoding="utf-8")
        assert os.WIFEXITED(status_b) and os.WEXITSTATUS(status_b) == 0, Path(
            report_b["stderr_path"]
        ).read_text(encoding="utf-8")

        worktree_a = Path(report_a["worktree"])
        worktree_b = Path(report_b["worktree"])
        assert worktree_a != worktree_b

        subjects_a = subprocess.run(
            ["git", "-C", str(worktree_a), "log", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        subjects_b = subprocess.run(
            ["git", "-C", str(worktree_b), "log", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()

        # Each worktree carries ONLY its own change's commit (plus the
        # shared base) -- never the sibling's.
        assert "implementer work (change-a)" in subjects_a
        assert "implementer work (change-b)" not in subjects_a
        assert "implementer work (change-b)" in subjects_b
        assert "implementer work (change-a)" not in subjects_b
        assert "base" in subjects_a and "base" in subjects_b

        assert (worktree_a / "change-a.txt").is_file()
        assert not (worktree_a / "change-b.txt").exists()
        assert (worktree_b / "change-b.txt").is_file()
        assert not (worktree_b / "change-a.txt").exists()

        # The shared repo's `main` was never touched -- each change worked
        # exclusively on its own branch/worktree (git-ACID isolation).
        main_sha_after = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "main"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert main_sha_after == main_sha_before
        assert (
            subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            == ""
        )
