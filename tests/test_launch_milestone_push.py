"""Hermetic tests for `orchestration.launch.milestone_push` (the best-effort
per-milestone branch push -- see the module docstring, design.md D3, and the
github-mirror spec "Verified milestone commits are pushed to the run branch").

Real git throughout: tmp_path work repos pushing to a local `git init --bare`
origin, so real push semantics are exercised with zero network. Failure paths
use a non-fast-forward divergence and a nonexistent remote.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from orchestration.harness.common import (
    EXIT_ATTENTION,
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
)
from orchestration.launch.milestone_push import main, push

BRANCH = "change/015-github-mirror"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _neutralize_global_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize global/system git config so tests are hermetic; local
    identity is configured per-repo below."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "no-system-gitconfig"))


def _init_repo(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "user.email", "t@t")
    (root / "base.txt").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare origin repo -- the push target, no network."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return bare


@pytest.fixture
def worktree(tmp_path: Path, origin: Path) -> Path:
    """A work repo with a baseline commit and `origin` wired to the bare repo."""
    root = tmp_path / "wt"
    _init_repo(root)
    _git(root, "remote", "add", "origin", str(origin))
    return root


class TestDryRun:
    def test_default_is_dry_run_and_touches_no_git(self, worktree: Path, origin: Path) -> None:
        verdict, code = push({"worktree": str(worktree), "branch": BRANCH})
        assert code == EXIT_GOOD
        assert verdict["status"] == "dry_run"
        assert verdict["pushed"] is False
        assert verdict["would_run"] == [
            "git",
            "-C",
            str(worktree),
            "push",
            "origin",
            f"HEAD:refs/heads/{BRANCH}",
        ]
        assert "--force" not in verdict["would_run"]
        # origin has no branches -- nothing was pushed.
        refs = subprocess.run(
            ["git", "-C", str(origin), "for-each-ref", "--format=%(refname)"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert refs == ""

    def test_dry_run_honours_custom_remote(self, worktree: Path) -> None:
        verdict, code = push({"worktree": str(worktree), "branch": BRANCH, "remote": "upstream"})
        assert code == EXIT_GOOD
        assert verdict["would_run"][4] == "upstream"


class TestLivePush:
    def test_successful_push_lands_commit_on_origin(self, worktree: Path, origin: Path) -> None:
        verdict, code = push({"worktree": str(worktree), "branch": BRANCH, "dry_run": False})
        assert code == EXIT_GOOD
        assert verdict["status"] == "pushed"
        assert verdict["pushed"] is True
        assert verdict["branch"] == BRANCH
        assert verdict["git_exit_code"] == 0
        # the commit is visible on the origin's run branch
        head = _git(worktree, "rev-parse", "HEAD")
        landed = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
        assert landed == head

    def test_non_ff_rejection_reports_failure_without_force(
        self, worktree: Path, origin: Path
    ) -> None:
        # First push lands the run branch on origin.
        verdict, code = push({"worktree": str(worktree), "branch": BRANCH, "dry_run": False})
        assert code == EXIT_GOOD
        landed_before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")

        # Rewrite local history so HEAD is no longer a descendant of the
        # pushed tip -- the next push is a non-fast-forward.
        (worktree / "base.txt").write_text("rewritten\n")
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-q", "--amend", "-m", "amended base")

        verdict, code = push({"worktree": str(worktree), "branch": BRANCH, "dry_run": False})
        assert code == EXIT_ATTENTION
        assert verdict["status"] == "push_failed"
        assert verdict["pushed"] is False
        assert verdict["git_exit_code"] not in (0, None)
        # origin's branch was NOT force-updated -- it still points at the
        # original tip.
        landed_after = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
        assert landed_after == landed_before

    def test_nonexistent_remote_reports_failure(self, worktree: Path) -> None:
        verdict, code = push(
            {"worktree": str(worktree), "branch": BRANCH, "remote": "nope", "dry_run": False}
        )
        assert code == EXIT_ATTENTION
        assert verdict["status"] == "push_failed"
        assert verdict["pushed"] is False
        assert verdict["git_exit_code"] not in (0, None)


class TestDryRunStringCoercion:
    """Regression for #22: `push_dry_run` reaches this module through a Jinja
    `"{{ ... }}"` template as the STRING "false"/"False", not a bool. A bare
    `bool("false")` is truthy, which would silently skip every production
    push."""

    @pytest.mark.parametrize("falsey", ["false", "False", "FALSE", " false ", "0", "no", "off"])
    def test_falsey_strings_push_for_real(self, worktree: Path, origin: Path, falsey: str) -> None:
        verdict, code = push({"worktree": str(worktree), "branch": BRANCH, "dry_run": falsey})
        assert code == EXIT_GOOD
        assert verdict["status"] == "pushed", f"dry_run={falsey!r} should push"

    @pytest.mark.parametrize("truthy", ["true", "True", "1", "yes", "on"])
    def test_truthy_strings_stay_dry_run(self, worktree: Path, truthy: str) -> None:
        verdict, code = push({"worktree": str(worktree), "branch": BRANCH, "dry_run": truthy})
        assert code == EXIT_GOOD
        assert verdict["status"] == "dry_run"


class TestErrors:
    def test_missing_branch_with_dry_run_false_raises(self, worktree: Path) -> None:
        with pytest.raises(HarnessInputError, match="branch"):
            push({"worktree": str(worktree), "dry_run": False})

    def test_empty_branch_with_dry_run_false_raises(self, worktree: Path) -> None:
        with pytest.raises(HarnessInputError, match="branch"):
            push({"worktree": str(worktree), "branch": "   ", "dry_run": False})


class TestScriptEntry:
    def test_main_dry_run_emits_verdict(self, capsys: pytest.CaptureFixture) -> None:
        code = main([json.dumps({"branch": BRANCH})])
        assert code == EXIT_GOOD
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "dry_run"
        assert out["would_run"][-1] == f"HEAD:refs/heads/{BRANCH}"

    def test_main_missing_branch_dry_run_false_exits_2(self, capsys: pytest.CaptureFixture) -> None:
        code = main([json.dumps({"dry_run": False})])
        assert code == EXIT_ERROR
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"
        assert out["reason"]

    def test_main_malformed_json_exits_2(self, capsys: pytest.CaptureFixture) -> None:
        code = main(["this is not json ["])
        assert code == EXIT_ERROR
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"

    def test_cli_subprocess_dry_run_and_malformed(self) -> None:
        ok = subprocess.run(
            [
                sys.executable,
                "-m",
                "orchestration.launch.milestone_push",
                json.dumps({"branch": BRANCH}),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert ok.returncode == EXIT_GOOD
        assert json.loads(ok.stdout)["status"] == "dry_run"

        bad = subprocess.run(
            [sys.executable, "-m", "orchestration.launch.milestone_push", "not json ["],
            capture_output=True,
            text=True,
            check=False,
        )
        assert bad.returncode == EXIT_ERROR
        assert json.loads(bad.stdout)["status"] == "error"
