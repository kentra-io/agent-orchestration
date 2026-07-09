"""M8 launcher unit tests: `orchestration.launch.change` against a tmp git
repo -- worktree creation, box materialization (docker-free), plan
resolution (both the fixture-injection path and the real `lifecycle apply`
adapter), and spawning `conductor run` over the Stub provider (hermetic --
no box, no LLM, no network; see `tests/stub_provider.py`).

Concurrency itself (two changes running at once, with proof of git-ACID
isolation) is `tests/test_m8_concurrency.py`'s job, not this file's --
these tests exercise ONE launch call at a time.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from launch_testbed import git_env, init_repo, write_personas, write_plan_fixture
from stub_provider import write_stub_script

from orchestration.launch.change import (
    create_worktree,
    launch,
    main,
    materialize_box,
    resolve_plan,
)

REPO_ROOT = Path(__file__).parent.parent
EXECUTE_CHANGE_WORKFLOW = REPO_ROOT / "workflows" / "execute-change.yaml"


def _base_config(tmp_path: Path, change_id: str, **overrides) -> dict:
    repo = init_repo(tmp_path / "repo")
    plan = write_plan_fixture(tmp_path / "plan.json", [{"id": 1, "title": "do the work"}])
    config = {
        "repo": str(repo),
        "change_id": change_id,
        "worktree_root": str(tmp_path / "worktrees"),
        "box": {"enabled": False},
        "conductor": {
            "workflow": str(EXECUTE_CHANGE_WORKFLOW),
            "plan_fixture_path": str(plan),
        },
        "dry_run": True,
    }
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# create_worktree
# ---------------------------------------------------------------------------


class TestCreateWorktree:
    def test_creates_a_new_branch_and_worktree(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")

        worktree = create_worktree(repo, tmp_path / "wt" / "c1", "change/c1")

        assert worktree.is_dir()
        assert (worktree / "README.md").is_file()
        branches = subprocess.run(
            ["git", "-C", str(repo), "branch", "--list", "change/c1"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "change/c1" in branches

    def test_reuses_an_existing_branch_on_a_relaunch(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        first = create_worktree(repo, tmp_path / "wt1", "change/c1")
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", str(first)],
            check=True,
            capture_output=True,
            text=True,
        )

        second = create_worktree(repo, tmp_path / "wt2", "change/c1")

        assert second.is_dir()
        assert (second / "README.md").is_file()


# ---------------------------------------------------------------------------
# materialize_box (docker-free half of the M6 recipe)
# ---------------------------------------------------------------------------


class TestMaterializeBox:
    def test_materializes_agent_claude_personas_and_claudebox_config(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        worktree = create_worktree(repo, tmp_path / "wt", "change/c1")
        personas_dir = write_personas(tmp_path / "personas")

        result = materialize_box(worktree, personas_dir)

        assert set(result["personas"]) == {"implementer", "verifier", "orchestrator"}
        assert result["claude_dir_source"] == str((worktree / ".agent-claude").resolve())

        agent_claude = worktree / ".agent-claude"
        assert (agent_claude / "skills").is_dir()
        assert not any((agent_claude / "skills").iterdir())
        assert (agent_claude / "plugins").is_dir()
        assert not any((agent_claude / "plugins").iterdir())
        assert (agent_claude / "settings.json").read_text(encoding="utf-8").strip() == "{}"
        assert (agent_claude / "CLAUDE.md").is_file()

        for role in ("implementer", "verifier", "orchestrator"):
            persona_path = worktree / ".claude" / "agents" / f"{role}.md"
            source_path = personas_dir / f"{role}.md"
            assert persona_path.is_file()
            assert persona_path.read_text(encoding="utf-8") == source_path.read_text(
                encoding="utf-8"
            )

        config_text = (worktree / ".claudebox" / "config.yaml").read_text(encoding="utf-8")
        assert "provisioning:" in config_text
        assert f"claude_dir_source: {agent_claude.resolve()}" in config_text


# ---------------------------------------------------------------------------
# launch() -- box step honors box.enabled / box.start
# ---------------------------------------------------------------------------


class TestLaunchBoxStep:
    def test_box_step_skipped_when_disabled(self, tmp_path: Path) -> None:
        config = _base_config(tmp_path, "c-box-off", box={"enabled": False})

        report = launch(config)

        assert report["box"] == {"enabled": False, "name": None}
        assert not (Path(report["worktree"]) / ".agent-claude").exists()
        assert not (Path(report["worktree"]) / ".claudebox").exists()

    def test_box_materializes_without_docker_when_start_is_false(self, tmp_path: Path) -> None:
        personas_dir = write_personas(tmp_path / "personas")
        config = _base_config(
            tmp_path,
            "c-box-materialize-only",
            box={"enabled": True, "start": False, "personas_dir": str(personas_dir)},
        )

        report = launch(config)

        assert report["box"]["enabled"] is True
        assert report["box"]["name"] is None  # start=False -- no cb/docker call made
        assert Path(report["box"]["claude_dir_source"]).is_dir()
        assert set(report["box"]["personas"]) == {"implementer", "verifier", "orchestrator"}
        assert (Path(report["worktree"]) / ".claudebox" / "config.yaml").is_file()


# ---------------------------------------------------------------------------
# launch() -- dry_run does everything except spawn `conductor`
# ---------------------------------------------------------------------------


class TestLaunchDryRun:
    def test_dry_run_builds_the_argv_but_spawns_nothing(self, tmp_path: Path) -> None:
        config = _base_config(tmp_path, "c-dry-run", dry_run=True)

        report = launch(config)

        assert report["dry_run"] is True
        assert report["pid"] is None
        assert report["returncode"] is None
        assert report["stdout_path"] is None
        assert report["stderr_path"] is None
        assert "conductor" in report["conductor_argv"][0]
        assert str(EXECUTE_CHANGE_WORKFLOW) in report["conductor_argv"]
        assert Path(report["plan_fixture_path"]).is_file()

    def test_caller_env_tmpdir_cannot_defeat_the_per_change_relocation(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """P4 regression (review finding 2026-07-09): a caller-provided env
        that carries the parent's TMPDIR (e.g. `os.environ.copy()` on macOS,
        where TMPDIR is always set) must NOT override the per-change
        checkpoint relocation -- otherwise two concurrent changes silently
        share one checkpoint/event dir. The relocation is re-applied last
        and must win.
        """
        captured: dict = {}

        class _FakeProc:
            pid = 12345

            def wait(self):
                return 0

        real_popen = subprocess.Popen

        def _capture_popen(argv, *args, **kwargs):
            # Intercept only the conductor child; delegate everything else
            # (git/lifecycle subprocesses run via subprocess.run -> Popen).
            if argv and "conductor" in Path(str(argv[0])).name:
                captured["env"] = kwargs["env"]
                return _FakeProc()
            return real_popen(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", _capture_popen)

        poisoned = str(tmp_path / "shared-parent-tmp")
        config = _base_config(tmp_path, "c-env-poison", dry_run=False)
        config["conductor"]["tmpdir"] = str(tmp_path / "conductor-tmp")
        config["conductor"]["env"] = {
            **config["conductor"].get("env", {}),
            "TMPDIR": poisoned,  # the poison: parent/shared TMPDIR in caller env
        }
        config["wait"] = True

        report = launch(config)

        child_tmpdir = captured["env"]["TMPDIR"]
        assert child_tmpdir != poisoned, (
            "caller env TMPDIR overrode the P4 per-change checkpoint relocation"
        )
        assert child_tmpdir == str(Path(report["tmpdir"]) / "checkpoints")


# ---------------------------------------------------------------------------
# launch() -- actually spawns `conductor run` over the Stub provider
# ---------------------------------------------------------------------------


class TestLaunchSpawnsConductor:
    def test_completes_over_stub_provider_with_relocated_tmpdir(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        plan = write_plan_fixture(tmp_path / "plan.json", [{"id": 1, "title": "do the work"}])
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
        tmpdir = tmp_path / "conductor-tmp"
        config = {
            "repo": str(repo),
            "change_id": "c-stub",
            "worktree_root": str(tmp_path / "worktrees"),
            "box": {"enabled": False},
            "conductor": {
                "workflow": str(EXECUTE_CHANGE_WORKFLOW),
                "provider": "stub",
                "plan_fixture_path": str(plan),
                "tmpdir": str(tmpdir),
                "env": {"CONDUCTOR_STUB_SCRIPT": str(script_path), **git_env()},
                "inputs": {"gates_l1_command": "exit 0"},
            },
            "wait": True,
        }

        report = launch(config)

        assert report["returncode"] == 0, Path(report["stderr_path"]).read_text(encoding="utf-8")
        assert report["pid"] is not None
        assert report["events_path"] is not None
        events_path = Path(report["events_path"])
        assert events_path.is_file()
        assert tmpdir.resolve() in events_path.resolve().parents
        assert (tmpdir / "checkpoints").is_dir()
        # Relocation actually matters: this is not the platform default tmpdir.
        assert tmpdir.resolve() != Path(tempfile.gettempdir()).resolve()

        output = json.loads(Path(report["stdout_path"]).read_text(encoding="utf-8"))
        assert output["milestones_processed"] == 1


class TestLaunchWaitFalse:
    def test_wait_false_returns_immediately_then_the_child_completes(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        plan = write_plan_fixture(tmp_path / "plan.json", [{"id": 1, "title": "do the work"}])
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
        config = {
            "repo": str(repo),
            "change_id": "c-wait-false",
            "worktree_root": str(tmp_path / "worktrees"),
            "box": {"enabled": False},
            "conductor": {
                "workflow": str(EXECUTE_CHANGE_WORKFLOW),
                "provider": "stub",
                "plan_fixture_path": str(plan),
                "tmpdir": str(tmp_path / "conductor-tmp"),
                "env": {"CONDUCTOR_STUB_SCRIPT": str(script_path), **git_env()},
                "inputs": {"gates_l1_command": "exit 0"},
            },
            "wait": False,
        }

        report = launch(config)

        assert report["pid"] is not None
        assert report["returncode"] is None  # wait: false -- launch() does not block

        _, status = os.waitpid(report["pid"], 0)
        assert os.WIFEXITED(status)
        assert os.WEXITSTATUS(status) == 0


# ---------------------------------------------------------------------------
# resolve_plan -- the real `lifecycle apply` production adapter
# ---------------------------------------------------------------------------


class TestResolvePlanRealLifecycleApply:
    def test_resolve_plan_shells_out_to_real_lifecycle_apply(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir(parents=True)
        subprocess.run(
            ["lifecycle", "init", "--runtimes", "claude-code", "--source-type", "none"],
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        )
        change_dir = project / "openspec" / "changes" / "001-test"
        change_dir.mkdir(parents=True)
        (change_dir / "proposal.md").write_text(
            '---\nissue: "kentra-io/kafka-dq#1"\ntype: bug\n---\n\n# Fix probe\n\n## Why\nTest.\n',
            encoding="utf-8",
        )
        (change_dir / "tasks.md").write_text(
            "## Milestone 1: do the thing\n"
            "**Goal** -- do it.\n"
            "**Deliverables** -- a file.\n"
            "**Validation contract** -- checkable acceptance criteria, pre-committed:\n"
            "  - `exit 0` passes.\n\n"
            "  ```contract\n"
            "  check: exit 0\n"
            "  criteria: trivially true.\n"
            "  paths:\n"
            '    - "**"\n'
            "  ```\n"
            "**Steps** -- ordered breakdown:\n"
            "  1. [ ] Do the thing.\n",
            encoding="utf-8",
        )
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        fixture_path = resolve_plan(project, "001-test", dest_dir=dest_dir)

        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert data["milestones"][0]["id"] == 1
        assert data["milestones"][0]["title"] == "do the thing"
        assert data["milestones"][0]["contract"]["check"] == "exit 0"
        assert data["milestones"][0]["contract"]["paths"] == ["**"]


# ---------------------------------------------------------------------------
# main() -- the script CLI entry point
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_main_emits_json_and_exits_zero_on_a_dry_run(self, tmp_path: Path, capsys) -> None:
        config = _base_config(tmp_path, "c-cli", dry_run=True)

        exit_code = main([json.dumps(config)])

        assert exit_code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["dry_run"] is True

    def test_main_reports_a_harness_error_on_bad_input(self, capsys) -> None:
        exit_code = main([json.dumps({"repo": "/tmp/does-not-matter"})])  # missing change_id

        assert exit_code == 2
        report = json.loads(capsys.readouterr().out)
        assert "error" in report
