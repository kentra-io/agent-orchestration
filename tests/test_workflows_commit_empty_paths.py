"""#23 fix: a milestone whose declared `contract.paths` stage nothing while
the worktree has changes elsewhere must FAIL the workflow loudly, not
silently complete as a passed/clean milestone.

Background (see `orchestration/launch/milestone_commit.py`'s "empty_paths"
status and `workflows/milestone.yaml`'s `commit` -> `commit_failed` route):
before this fix, `git add -A -- <paths>` matching zero diffs always reported
"clean"/EXIT_GOOD, and the `commit` step routed unconditionally to `$end`
regardless of its own exit code -- a script step's exit code is NOT
self-enforcing in Conductor (`conductor.executor.script.ScriptExecutor` only
records it on the output; routing decides whether it fails the workflow),
so an `empty_paths`/`error` exit used to reach `$end` exactly like a real
success.

These tests reuse `test_workflows_ladder.py`'s hermetic Stub-provider
harness (`_base_env`, `_read_events`, `MILESTONE_WORKFLOW`, `CONDUCTOR_BIN`)
but do NOT use `_run_ladder` itself, since that helper asserts
`returncode == 0` -- exactly the thing under test here is a *non-zero*
exit. Unlike the rest of the ladder suite (which never touches a real git
repo -- `commit_dry_run` defaults to true), these tests flip
`commit_dry_run=false` against a real scratch git repo, mirroring the
scratch-repo fixture pattern in `tests/test_launch_milestone_commit.py`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from stub_provider import write_stub_script
from test_workflows_ladder import CONDUCTOR_BIN, MILESTONE_WORKFLOW, _base_env, _read_events

REPO_ROOT = Path(__file__).parent.parent

_PASS_VERIFIER = {
    "pass": True,
    "notes": "looks good",
    "score": 1.0,
    "violations": "none",
}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _scratch_repo(root: Path) -> Path:
    """A fresh git repo with a baseline commit containing a TRACKED,
    unchanged `allowed/` directory -- the shape that reproduces #23: a
    `paths` pathspec that matches something real (so `git add -A --
    allowed/` succeeds, exit 0) but has no diff to stage this round."""
    root.mkdir()
    _git(root, "init", "-q")
    (root / "allowed").mkdir()
    (root / "allowed" / "in.txt").write_text("in\n")
    (root / "base.txt").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return root


def _run_milestone_raw(
    tmp_path: Path,
    steps: dict[str, list[dict[str, Any]]],
    inputs: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    """Like `test_workflows_ladder._run_ladder`, but makes NO assertion
    about the exit code -- callers here deliberately expect failure (or, in
    the counterpart test, success) and check `returncode` themselves.
    Also neutralizes global/system git config (same as the
    `milestone_commit` unit-test `repo` fixture) so the real `git commit`
    the `commit` step performs exercises the deterministic fallback
    identity rather than depending on the host's git config.
    """
    script_path = write_stub_script(tmp_path / "script", steps)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    env = _base_env(tmp_dir, script_path)
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "no-global-gitconfig")
    env["GIT_CONFIG_SYSTEM"] = str(tmp_path / "no-system-gitconfig")

    args = [
        str(CONDUCTOR_BIN),
        "--silent",
        "run",
        str(MILESTONE_WORKFLOW),
        "--provider",
        "stub",
        "--skip-gates",
    ]
    for k, v in inputs.items():
        args += ["--input", f"{k}={v}"]

    result = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    return result, _read_events(tmp_dir)


def _parse_stdout_json(stdout: str) -> dict[str, Any]:
    return json.loads(stdout[stdout.index("{") :])


class TestEmptyPathsFailsTheWorkflow:
    def test_commit_step_empty_paths_terminates_the_workflow_loudly(self, tmp_path: Path) -> None:
        """The actual #23 reproduction: the milestone's real (verified) work
        landed in `stray.txt`, but its declared `contract.paths` is
        `allowed/` -- a real, tracked, but UNCHANGED directory. The `commit`
        step must exit non-zero (`empty_paths`) and the workflow must
        terminate the run as a whole (non-zero process exit, no
        `workflow_completed` event) rather than silently reaching `$end` as
        a passed milestone.
        """
        repo = _scratch_repo(tmp_path / "repo")
        (repo / "stray.txt").write_text("real verified work landed here, not under allowed/\n")

        result, events = _run_milestone_raw(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "did it", "halt": "none"}}],
                "verifier": [{"content": _PASS_VERIFIER}],
            },
            inputs={
                "milestone_id": "M1",
                "worktree": str(repo),
                "commit_dry_run": "false",
                "commit_paths": '["allowed/"]',
            },
        )

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

        output = _parse_stdout_json(result.stdout)
        assert output["commit_status"] == "empty_paths"
        assert output["commit_sha"] in (None, "", "null")

        event_types = {e["type"] for e in events}
        assert "workflow_completed" not in event_types
        assert "workflow_failed" in event_types

        commit_calls = [
            e
            for e in events
            if e["type"] == "script_completed" and e["data"].get("agent_name") == "commit"
        ]
        assert len(commit_calls) == 1
        assert commit_calls[0]["data"]["exit_code"] == 2

        failed_steps = [e["data"]["agent_name"] for e in events if e["type"] == "agent_failed"]
        assert "commit_failed" in failed_steps

        # And the real repo genuinely has nothing committed -- the whole
        # point of #23 is that a "passed" milestone must not silently lose
        # the stray file to an uncommitted worktree.
        log = _git(repo, "log", "--oneline").stdout
        assert log.count("\n") == 1  # only the baseline commit
        status = _git(repo, "status", "--porcelain").stdout
        assert "stray.txt" in status

    def test_paths_matching_real_changes_still_commits_and_ends_normally(
        self, tmp_path: Path
    ) -> None:
        """Regression guard for the new conditional route: when the
        declared paths DO match real changes, the `commit` step's exit code
        is 0 and the workflow must still reach `$end` normally (not
        `commit_failed`) -- the new routing must not turn a legitimate
        success into a failure.
        """
        repo = _scratch_repo(tmp_path / "repo")
        (repo / "allowed" / "in.txt").write_text("changed this round\n")

        result, events = _run_milestone_raw(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "did it", "halt": "none"}}],
                "verifier": [{"content": _PASS_VERIFIER}],
            },
            inputs={
                "milestone_id": "M1",
                "worktree": str(repo),
                "commit_dry_run": "false",
                "commit_paths": '["allowed/"]',
            },
        )

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        output = _parse_stdout_json(result.stdout)
        assert output["commit_status"] == "committed"
        assert output["commit_sha"]

        event_types = {e["type"] for e in events}
        assert "workflow_completed" in event_types
        assert "workflow_failed" not in event_types
        assert "commit_failed" not in {
            e["data"].get("agent_name") for e in events if "agent_name" in e.get("data", {})
        }

        log = _git(repo, "log", "--oneline").stdout
        assert log.count("\n") == 2  # base + the new milestone commit
