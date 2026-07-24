"""Hermetic tests for `orchestration.launch.milestone_commit` (the
deterministic per-milestone durability commit -- see the module docstring
and harness `tasks/orchestration-does-not-commit-milestones.md`)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from orchestration.harness.common import EXIT_ERROR, EXIT_GOOD, HarnessInputError
from orchestration.launch.milestone_commit import build_message, commit, main


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh git repo with one baseline commit and NO configured identity
    (global/system git config neutralized), so the fallback-identity path is
    what the tests exercise unless a test configures one."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "no-system-gitconfig"))
    root = tmp_path / "wt"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "base.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        check=True,
    )
    return root


def _log_subject(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class TestBuildMessage:
    def test_numeric_id_gets_m_prefix_and_change_suffix(self) -> None:
        assert build_message(2, "Wire the frobnicator", "001-e2e") == (
            "M2: Wire the frobnicator (001-e2e)"
        )

    def test_string_id_kept_and_no_suffix_without_change_id(self) -> None:
        assert build_message("M7", "Ship it", "") == "M7: Ship it"

    def test_multiline_title_truncated_to_first_line(self) -> None:
        assert build_message(1, "subject\nbody detail", None) == "M1: subject"


class TestDryRun:
    def test_default_is_dry_run_and_touches_no_git(self, tmp_path: Path) -> None:
        # worktree deliberately NOT a git repo: dry_run must not care.
        verdict, code = commit({"worktree": str(tmp_path), "milestone_id": 1})
        assert code == EXIT_GOOD
        assert verdict["status"] == "dry_run"
        assert verdict["committed"] is False
        assert verdict["would_run"][0][:3] == ["git", "add", "-A"]


class TestRealCommit:
    def test_commits_dirty_tree_with_fallback_identity(self, repo: Path) -> None:
        (repo / "work.txt").write_text("done\n")
        verdict, code = commit(
            {
                "worktree": str(repo),
                "milestone_id": 3,
                "milestone_title": "Do the work",
                "change_id": "001-x",
                "dry_run": False,
            }
        )
        assert code == EXIT_GOOD
        assert verdict["status"] == "committed"
        assert verdict["committed"] is True
        assert verdict["sha"]
        assert _log_subject(repo) == "M3: Do the work (001-x)"

    def test_clean_tree_is_a_no_op(self, repo: Path) -> None:
        verdict, code = commit({"worktree": str(repo), "milestone_id": 1, "dry_run": False})
        assert code == EXIT_GOOD
        assert verdict["status"] == "clean"
        assert verdict["committed"] is False

    def test_paths_confine_the_stage_to_declared_pathspecs(self, repo: Path) -> None:
        (repo / "allowed").mkdir()
        (repo / "allowed" / "in.txt").write_text("in\n")
        (repo / "stray.txt").write_text("out\n")
        verdict, code = commit(
            {
                "worktree": str(repo),
                "milestone_id": 1,
                "dry_run": False,
                "paths": ["allowed/"],
            }
        )
        assert code == EXIT_GOOD
        assert verdict["status"] == "committed"
        show = subprocess.run(
            ["git", "-C", str(repo), "show", "--stat", "--name-only", "--format="],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "allowed/in.txt" in show
        assert "stray.txt" not in show
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--short"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "stray.txt" in status  # left visible, not silently folded in

    def test_paths_accepted_as_json_encoded_string(self, repo: Path) -> None:
        (repo / "allowed").mkdir()
        (repo / "allowed" / "in.txt").write_text("in\n")
        verdict, code = commit(
            {
                "worktree": str(repo),
                "milestone_id": 1,
                "dry_run": False,
                "paths": '["allowed/"]',
            }
        )
        assert code == EXIT_GOOD
        assert verdict["status"] == "committed"


class TestDryRunStringCoercion:
    """Regression for #22: the workflow forwards `commit_dry_run` through a
    Jinja `"{{ ... }}"` template, so `dry_run` reaches this module as the
    STRING "false"/"False" (the launcher's production value), not a bool. A
    bare `bool("false")` is truthy, which would silently skip every production
    commit."""

    @pytest.mark.parametrize("falsey", ["false", "False", "FALSE", " false ", "0", "no", "off"])
    def test_falsey_strings_do_not_suppress_the_commit(self, repo: Path, falsey: str) -> None:
        (repo / "work.txt").write_text("done\n")
        verdict, code = commit({"worktree": str(repo), "milestone_id": 3, "dry_run": falsey})
        assert code == EXIT_GOOD
        assert verdict["status"] == "committed", (
            f"dry_run={falsey!r} should commit, not {verdict['status']}"
        )
        assert verdict["committed"] is True

    @pytest.mark.parametrize("truthy", ["true", "True", "1", "yes", "on"])
    def test_truthy_strings_stay_dry_run(self, repo: Path, truthy: str) -> None:
        (repo / "work.txt").write_text("done\n")
        verdict, code = commit({"worktree": str(repo), "milestone_id": 3, "dry_run": truthy})
        assert code == EXIT_GOOD
        assert verdict["status"] == "dry_run"
        assert verdict["committed"] is False

    @pytest.mark.parametrize("ambiguous", ["", "  ", "maybe"])
    def test_empty_or_unrecognized_falls_back_to_safe_dry_run(
        self, repo: Path, ambiguous: str
    ) -> None:
        # Unset/garbage must never silently commit — default to the hermetic-safe dry-run.
        (repo / "work.txt").write_text("done\n")
        verdict, code = commit({"worktree": str(repo), "milestone_id": 3, "dry_run": ambiguous})
        assert code == EXIT_GOOD
        assert verdict["status"] == "dry_run"


class TestErrors:
    def test_missing_milestone_id_raises_input_error(self) -> None:
        with pytest.raises(HarnessInputError):
            commit({"worktree": "."})

    def test_non_repo_worktree_is_a_loud_error(self, tmp_path: Path) -> None:
        verdict, code = commit({"worktree": str(tmp_path), "milestone_id": 1, "dry_run": False})
        assert code == EXIT_ERROR
        assert verdict["status"] == "error"
        assert verdict["reason"]

    def test_bad_paths_json_string_is_an_input_error(self) -> None:
        with pytest.raises(HarnessInputError):
            commit({"milestone_id": 1, "paths": "not json ["})


class TestScriptEntry:
    def test_main_with_inline_json_emits_verdict(self, capsys: pytest.CaptureFixture) -> None:
        code = main([json.dumps({"milestone_id": 5, "milestone_title": "t"})])
        assert code == EXIT_GOOD
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "dry_run"
        assert out["message"] == "M5: t"

    def test_main_input_error_exits_2_with_error_json(self, capsys: pytest.CaptureFixture) -> None:
        code = main([json.dumps({})])
        assert code == EXIT_ERROR
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"
