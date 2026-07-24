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

import yaml
from launch_testbed import git_env, init_repo, write_personas, write_plan_fixture
from stub_provider import write_stub_script

from orchestration.launch import change as change_mod
from orchestration.launch.change import (
    ChangeLaunchError,
    create_worktree,
    derive_repo_gh,
    launch,
    main,
    materialize_box,
    resolve_plan,
    start_box,
)
from orchestration.obs import registry as obs_registry

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


def _add_origin(repo: Path, url: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", url],
        check=True,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# derive_repo_gh -- owner/repo from the origin remote (D9 mirror wiring)
# ---------------------------------------------------------------------------


class TestDeriveRepoGh:
    def test_ssh_scp_form(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        _add_origin(repo, "git@github.com:acme/widgets.git")
        assert derive_repo_gh(repo) == "acme/widgets"

    def test_https_form(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        _add_origin(repo, "https://github.com/acme/widgets.git")
        assert derive_repo_gh(repo) == "acme/widgets"

    def test_https_form_without_git_suffix(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        _add_origin(repo, "https://github.com/acme/widgets")
        assert derive_repo_gh(repo) == "acme/widgets"

    def test_ssh_url_form(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        _add_origin(repo, "ssh://git@github.com/acme/widgets.git")
        assert derive_repo_gh(repo) == "acme/widgets"

    def test_absent_remote_is_none(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")  # init_repo adds no remote
        assert derive_repo_gh(repo) is None


# ---------------------------------------------------------------------------
# launch() -- repo_gh fact + mirror input threading (D9)
# ---------------------------------------------------------------------------


class TestMirrorInputThreading:
    def _config(self, tmp_path: Path, change_id: str, **overrides) -> dict:
        repo = init_repo(tmp_path / "repo")
        origin = overrides.pop("origin", None)
        if origin:
            _add_origin(repo, origin)
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

    @staticmethod
    def _input_val(argv: list[str], key: str) -> str | None:
        """Return the value of the LAST `--input key=value` for `key`, or None."""
        val = None
        for tok in argv:
            if tok.startswith(f"{key}="):
                val = tok[len(key) + 1 :]
        return val

    def test_stub_tier_threads_branch_repo_issue_but_leaves_dry_run_defaults(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "registry"))
        config = self._config(
            tmp_path,
            "c-stub-thread",
            origin="git@github.com:acme/widgets.git",
            issue=15,
        )

        report = launch(config)
        argv = report["conductor_argv"]

        # Facts threaded unconditionally.
        assert self._input_val(argv, "branch") == "change/c-stub-thread"
        assert self._input_val(argv, "notify_repo") == "acme/widgets"
        assert self._input_val(argv, "notify_issue") == "15"
        # Stub tier leaves every mirror dry-run flag defaulted true (absent).
        assert self._input_val(argv, "push_dry_run") is None
        assert self._input_val(argv, "notify_dry_run") is None
        assert self._input_val(argv, "commit_dry_run") is None

        # The derived owner/repo lands on the registry entry as a fact.
        entry = obs_registry.load_entry("repo", "c-stub-thread")
        assert entry is not None
        assert entry["repo_gh"] == "acme/widgets"
        assert entry["issue"] == 15

    def test_box_tier_flips_push_and_notify_dry_run_false_when_repo_and_issue_resolved(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "registry"))
        personas_dir = write_personas(tmp_path / "personas")
        config = self._config(
            tmp_path,
            "c-box-thread",
            origin="git@github.com:acme/widgets.git",
            issue=15,
            box={"enabled": True, "start": False, "personas_dir": str(personas_dir)},
        )

        report = launch(config)
        argv = report["conductor_argv"]

        assert self._input_val(argv, "commit_dry_run") == "false"
        assert self._input_val(argv, "push_dry_run") == "false"
        assert self._input_val(argv, "notify_dry_run") == "false"
        assert self._input_val(argv, "notify_repo") == "acme/widgets"
        assert self._input_val(argv, "notify_issue") == "15"

    def test_box_tier_without_issue_keeps_notify_dry_run_default(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "registry"))
        personas_dir = write_personas(tmp_path / "personas")
        config = self._config(
            tmp_path,
            "c-box-no-issue",
            origin="git@github.com:acme/widgets.git",
            box={"enabled": True, "start": False, "personas_dir": str(personas_dir)},
        )

        report = launch(config)
        argv = report["conductor_argv"]

        # push still flips false (branch publish is repo-agnostic), but the
        # checklist mirror stays dry-run without a resolved issue target.
        assert self._input_val(argv, "push_dry_run") == "false"
        assert self._input_val(argv, "notify_dry_run") is None
        assert self._input_val(argv, "notify_issue") is None

    def test_payload_repo_gh_override_wins_over_derivation(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "registry"))
        config = self._config(
            tmp_path,
            "c-override",
            origin="git@github.com:acme/widgets.git",
            repo_gh="fork-owner/mirror",
        )

        report = launch(config)
        argv = report["conductor_argv"]

        assert self._input_val(argv, "notify_repo") == "fork-owner/mirror"
        entry = obs_registry.load_entry("repo", "c-override")
        assert entry is not None
        assert entry["repo_gh"] == "fork-owner/mirror"

    def test_absent_origin_yields_no_notify_repo_and_null_fact(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "registry"))
        config = self._config(tmp_path, "c-no-origin")  # no origin remote added

        report = launch(config)
        argv = report["conductor_argv"]

        assert self._input_val(argv, "notify_repo") is None
        # branch is still threaded (a fact independent of the remote).
        assert self._input_val(argv, "branch") == "change/c-no-origin"
        entry = obs_registry.load_entry("repo", "c-no-origin")
        assert entry is not None
        assert entry["repo_gh"] is None


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

    def test_reuses_the_worktree_when_the_same_path_already_exists(self, tmp_path: Path) -> None:
        # A re-launch of the same change derives the SAME worktree path and
        # branch (both from change_id), so `git worktree add` would hit an
        # already-registered worktree. That is an idempotent no-op, not an error.
        repo = init_repo(tmp_path / "repo")
        path = tmp_path / "wt" / "c1"
        first = create_worktree(repo, path, "change/c1")

        second = create_worktree(repo, path, "change/c1")

        assert second == first
        assert second.is_dir()
        assert (second / "README.md").is_file()

    def test_rejects_a_worktree_path_registered_to_another_branch(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        path = tmp_path / "wt" / "shared"
        create_worktree(repo, path, "change/c1")

        try:
            create_worktree(repo, path, "change/c2")
            raise AssertionError("expected a ChangeLaunchError")
        except ChangeLaunchError as exc:
            assert "change/c1" in str(exc)


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

    def test_merges_into_existing_config_instead_of_clobbering_it(self, tmp_path: Path) -> None:
        """#27 regression: a project's own .claudebox/config.yaml (env/security/
        pre-existing provisioning subkeys) must survive materialize_box -- only
        `provisioning.claude_dir_source` gets set."""
        repo = init_repo(tmp_path / "repo")
        worktree = create_worktree(repo, tmp_path / "wt", "change/c-merge")
        personas_dir = write_personas(tmp_path / "personas")

        config_path = worktree / ".claudebox" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "env:\n"
            "  GH_TOKEN: ${GH_TOKEN}\n"
            "  GIT_AUTHOR_NAME: someone\n"
            "security:\n"
            "  network: restricted\n"
            "provisioning:\n"
            "  claude_dir_source: /old/stale/path\n"
            "  extra_flag: true\n",
            encoding="utf-8",
        )

        result = materialize_box(worktree, personas_dir)

        merged = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert merged["env"] == {"GH_TOKEN": "${GH_TOKEN}", "GIT_AUTHOR_NAME": "someone"}
        assert merged["security"] == {"network": "restricted"}
        assert merged["provisioning"]["extra_flag"] is True
        assert merged["provisioning"]["claude_dir_source"] == result["claude_dir_source"]
        assert merged["provisioning"]["claude_dir_source"] == str(
            (worktree / ".agent-claude").resolve()
        )

    def test_malformed_existing_config_raises_and_is_not_overwritten(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        worktree = create_worktree(repo, tmp_path / "wt", "change/c-bad-yaml")
        personas_dir = write_personas(tmp_path / "personas")

        config_path = worktree / ".claudebox" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        bad_yaml = "env:\n  FOO: [unterminated\n"
        config_path.write_text(bad_yaml, encoding="utf-8")

        try:
            materialize_box(worktree, personas_dir)
            raise AssertionError("expected a ChangeLaunchError")
        except ChangeLaunchError as exc:
            assert "not valid YAML" in str(exc)

        assert config_path.read_text(encoding="utf-8") == bad_yaml


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
# start_box -- ensures the box via `cb run --detach` (no interactive attach)
# ---------------------------------------------------------------------------


class TestStartBox:
    def test_cb_run_uses_detach_flag(self, tmp_path: Path, monkeypatch) -> None:
        """`cb run --detach` ensures/provisions the box and exits 0 without an
        interactive attach; the bare `cb run` used to exit 1 under DEVNULL stdin
        after the box was already up, aborting a healthy launch."""
        worktree = tmp_path / "wt"
        worktree.mkdir()
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            # cb run --detach -> exit 0; docker ps -> the resolved box name.
            return subprocess.CompletedProcess(argv, 0, stdout="claudebox-abc\n", stderr="")

        monkeypatch.setattr(change_mod.subprocess, "run", fake_run)

        # Absolute cb_bin so shutil.which() isn't consulted.
        name = start_box(worktree, cb_bin="/abs/mycb", docker_bin="/abs/docker")

        assert name == "claudebox-abc"
        assert calls[0] == ["/abs/mycb", "run", "--detach"]


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


class TestReportDefaults:
    def test_report_carries_dashboard_url_and_default_workflow(self, tmp_path: Path) -> None:
        import json as _json
        import subprocess as _sp

        from orchestration.launch.change import MODULE_ROOT, launch

        repo = tmp_path / "repo"
        repo.mkdir()
        _sp.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "f").write_text("x")
        _sp.run(["git", "-C", str(repo), "add", "."], check=True)
        _sp.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "init",
            ],
            check=True,
        )
        fixture = tmp_path / "plan.json"
        fixture.write_text(_json.dumps({"milestones": [{"id": 1, "title": "t"}]}))

        report = launch(
            {
                "repo": str(repo),
                "change_id": "1-a",
                "dry_run": True,
                "conductor": {
                    "web": True,
                    "web_port": 42007,
                    "plan_fixture_path": str(fixture),
                },
            }
        )
        assert report["dashboard_url"] == "http://localhost:42007"
        run_idx = report["conductor_argv"].index("run")
        assert report["conductor_argv"][run_idx + 1] == str(
            MODULE_ROOT / "workflows" / "execute-change.yaml"
        )


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
