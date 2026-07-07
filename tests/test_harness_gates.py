"""Unit tests for `orchestration.harness.gates` -- the L1+L2+diff_paths+
deviation_check aggregator `workflows/milestone.yaml`'s `gates` step calls.
"""

from __future__ import annotations

import json
import subprocess
import sys

from orchestration.harness import gates


def test_only_l1_configured_pass() -> None:
    verdict = gates.check({"l1": {"command": "true"}})
    assert verdict["pass"] is True
    assert verdict["report"]["l1"]["pass"] is True
    assert verdict["report"]["l2"] is None
    assert verdict["report"]["diff_paths"] is None
    assert verdict["report"]["deviation_check"] is None


def test_only_l1_configured_fail() -> None:
    verdict = gates.check({"l1": {"command": "false"}})
    assert verdict["pass"] is False
    assert verdict["report"]["l1"]["pass"] is False


def test_no_checks_configured_trivially_passes() -> None:
    verdict = gates.check({})
    assert verdict["pass"] is True
    assert verdict["report"] == {
        "l1": None,
        "l2": None,
        "diff_paths": None,
        "deviation_check": None,
    }


def test_l1_and_l2_both_must_pass(testbed) -> None:
    verdict = gates.check(
        {
            "l1": {"command": "true"},
            "l2": {"commands": ["true"], "cwd": str(testbed.path)},
        }
    )
    assert verdict["pass"] is True

    verdict = gates.check(
        {
            "l1": {"command": "true"},
            "l2": {"commands": ["false"], "cwd": str(testbed.path)},
        }
    )
    assert verdict["pass"] is False
    assert verdict["report"]["l1"]["pass"] is True
    assert verdict["report"]["l2"]["pass"] is False


def test_diff_paths_pass_skips_deviation_check(testbed) -> None:
    testbed.plant_in_path_change()
    verdict = gates.check(
        {
            "diff_paths": {
                "repo_path": str(testbed.path),
                "base_ref": testbed.base_ref,
                "allowed_globs": testbed.allowed_globs,
            },
            "deviation_check": {
                "repo_path": str(testbed.path),
                "base_ref": testbed.base_ref,
                "allowed_globs": testbed.allowed_globs,
            },
        }
    )
    assert verdict["pass"] is True
    assert verdict["report"]["diff_paths"]["pass"] is True
    # diff_paths already passed -- deviation_check is never invoked.
    assert verdict["report"]["deviation_check"] is None


def test_diff_paths_fail_with_declared_deviation_covers_it(testbed) -> None:
    relpath = testbed.plant_out_of_path_file()
    testbed.declare_deviation(relpath, reason="approved after the fact", task_id="T1")
    verdict = gates.check(
        {
            "diff_paths": {
                "repo_path": str(testbed.path),
                "base_ref": testbed.base_ref,
                "allowed_globs": testbed.allowed_globs,
            },
            "deviation_check": {
                "repo_path": str(testbed.path),
                "base_ref": testbed.base_ref,
                "allowed_globs": testbed.allowed_globs,
            },
        }
    )
    # diff_paths itself still fails (its own strict, no-exceptions verdict)...
    assert verdict["report"]["diff_paths"]["pass"] is False
    # ...but the aggregate `pass` is True because deviation_check covers it.
    assert verdict["pass"] is True
    assert verdict["report"]["deviation_check"]["pass"] is True


def test_diff_paths_fail_with_no_deviation_check_hard_fails(testbed) -> None:
    testbed.plant_out_of_path_file()
    verdict = gates.check(
        {
            "diff_paths": {
                "repo_path": str(testbed.path),
                "base_ref": testbed.base_ref,
                "allowed_globs": testbed.allowed_globs,
            },
        }
    )
    assert verdict["pass"] is False
    assert verdict["report"]["deviation_check"] is None


def test_cli_exit_code_and_error_handling() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "orchestration.harness.gates", '{"l1": {"command": "true"}}'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["pass"] is True

    result = subprocess.run(
        [sys.executable, "-m", "orchestration.harness.gates", '{"l1": {}}'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "error" in json.loads(result.stdout)

    result = subprocess.run(
        [sys.executable, "-m", "orchestration.harness.gates", "not json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "error" in json.loads(result.stdout)
