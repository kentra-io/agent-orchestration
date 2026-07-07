"""Property test: same input -> byte-identical verdict JSON, every checker.

This is the core property the M4 harness exists to guarantee (see
orchestration/harness/README.md, "Determinism scope"). We serialize each
verdict the same way the checkers themselves do (sorted keys) and assert the
two serializations are exactly equal.
"""

import json
import sys

from orchestration.harness import (
    deviation_check,
    diff_paths,
    flakiness,
    l1_acceptance,
    l2_healthcheck,
)


def _serialize(verdict: dict) -> str:
    return json.dumps(verdict, indent=2, sort_keys=True)


def test_l1_is_deterministic():
    payload = {"command": f"{sys.executable} -c 'print(\"x\"); raise SystemExit(0)'"}
    first = _serialize(l1_acceptance.check(payload))
    second = _serialize(l1_acceptance.check(payload))
    assert first == second


def test_l2_is_deterministic():
    payload = {
        "commands": [
            f"{sys.executable} -c 'raise SystemExit(0)'",
            f"{sys.executable} -c 'raise SystemExit(1)'",
        ]
    }
    first = _serialize(l2_healthcheck.check(payload))
    second = _serialize(l2_healthcheck.check(payload))
    assert first == second


def test_diff_paths_is_deterministic(testbed):
    testbed.plant_out_of_path_file()
    payload = {
        "repo_path": str(testbed.path),
        "base_ref": testbed.base_ref,
        "allowed_globs": testbed.allowed_globs,
    }
    first = _serialize(diff_paths.check(payload))
    second = _serialize(diff_paths.check(payload))
    assert first == second


def test_deviation_check_is_deterministic(testbed):
    relpath = testbed.plant_undeclared_deviation()
    testbed.declare_deviation(relpath, reason="x")
    payload = {
        "repo_path": str(testbed.path),
        "base_ref": testbed.base_ref,
        "allowed_globs": testbed.allowed_globs,
        "deviation_log": "deviation.json",
    }
    first = _serialize(deviation_check.check(payload))
    second = _serialize(deviation_check.check(payload))
    assert first == second


def test_flakiness_is_deterministic_given_a_reset_counter(testbed):
    payload = {
        "command": f"{sys.executable} -m pytest tests/test_flaky.py -q",
        "cwd": str(testbed.path),
        "runs": 8,
        "env": {
            "FLAKY_STATE_FILE": str(testbed.path / ".flaky_state"),
            "FLAKY_FAIL_EVERY": "4",
        },
    }
    testbed.reset_flaky_state()
    first = _serialize(flakiness.check(payload))
    testbed.reset_flaky_state()
    second = _serialize(flakiness.check(payload))
    assert first == second
