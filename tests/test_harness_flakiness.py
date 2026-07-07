import sys

from orchestration.harness.flakiness import check


def test_flaky_test_is_quarantined(testbed):
    testbed.reset_flaky_state()
    verdict = check(
        {
            "command": f"{sys.executable} -m pytest tests/test_flaky.py -q",
            "cwd": str(testbed.path),
            "runs": 20,
            "threshold": 0.95,
            "env": {
                "FLAKY_STATE_FILE": str(testbed.path / ".flaky_state"),
                "FLAKY_FAIL_EVERY": "4",
            },
        }
    )
    # 20 runs, fails on run indices 0,4,8,12,16 (0-indexed, every 4th) -> 5 fails, 15 passes.
    assert verdict["quarantined"] is True
    assert verdict["runs"] == 20
    assert verdict["passes"] == 15
    assert verdict["pass_rate"] == 0.75
    assert len(verdict["results"]) == 20


def test_stable_test_is_not_quarantined(testbed):
    verdict = check(
        {
            "command": f"{sys.executable} -m pytest tests/test_stable.py -q",
            "cwd": str(testbed.path),
            "runs": 5,
            "threshold": 0.95,
        }
    )
    assert verdict["quarantined"] is False
    assert verdict["pass_rate"] == 1.0
    assert verdict["passes"] == 5


def test_a_few_intermittent_failures_still_above_threshold_are_not_quarantined(testbed):
    """19/20 = 95% pass-rate; the default threshold is a floor (< 0.95), not a
    ceiling, so this is NOT quarantined - reported faithfully either way."""
    testbed.reset_flaky_state()
    verdict = check(
        {
            "command": f"{sys.executable} -m pytest tests/test_flaky.py -q",
            "cwd": str(testbed.path),
            "runs": 20,
            "threshold": 0.95,
            "env": {
                "FLAKY_STATE_FILE": str(testbed.path / ".flaky_state"),
                "FLAKY_FAIL_EVERY": "20",
            },
        }
    )
    assert verdict["quarantined"] is False
    assert verdict["pass_rate"] == 0.95
