from pathlib import Path

from launch_testbed import init_repo, write_plan_fixture

from orchestration.launch.change import build_conductor_argv, launch
from orchestration.obs import registry

REPO_ROOT = Path(__file__).parent.parent
EXECUTE_CHANGE_WORKFLOW = REPO_ROOT / "workflows" / "execute-change.yaml"


def test_argv_without_web_is_unchanged():
    argv = build_conductor_argv(
        conductor_bin="conductor", workflow="w.yaml", silent=True, provider=None, inputs={}
    )
    assert "--web" not in argv and "--web-port" not in argv


def test_argv_with_web_appends_flags():
    argv = build_conductor_argv(
        conductor_bin="conductor",
        workflow="w.yaml",
        silent=True,
        provider="stub",
        inputs={"a": "1"},
        web=True,
        web_port=42001,
    )
    i = argv.index("--web")
    assert argv[i + 1 : i + 3] == ["--web-port", "42001"]


def test_dry_run_registers_and_reports_legend(tmp_path):
    repo = init_repo(tmp_path / "repo")
    plan = write_plan_fixture(tmp_path / "plan.json", [{"id": 1, "title": "do the work"}])

    report = launch(
        {
            "repo": str(repo),
            "change_id": "9-test-change",
            "worktree_root": str(tmp_path / "worktrees"),
            "branch": "9-test-change",
            "conductor": {
                "workflow": str(EXECUTE_CHANGE_WORKFLOW),
                "plan_fixture_path": str(plan),
            },
            "box": {"enabled": False},
            "dry_run": True,
            "issue": 9,
        }
    )

    entry = registry.load_entry("repo", "9-test-change")
    assert entry is not None and entry["issue"] == 9
    assert entry["incarnations"] == []  # dry_run spawns nothing
    assert "final JSON result only" in report["log_legend"]["conductor.stdout.log"]
    assert "live progress" in report["log_legend"]["conductor.stderr.log"]
