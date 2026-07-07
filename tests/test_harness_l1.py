import sys

from orchestration.harness.l1_acceptance import check


def test_zero_exit_passes():
    verdict = check({"command": f"{sys.executable} -c 'raise SystemExit(0)'"})
    assert verdict["pass"] is True
    assert verdict["exit_code"] == 0


def test_nonzero_exit_fails():
    verdict = check({"command": f"{sys.executable} -c 'raise SystemExit(3)'"})
    assert verdict["pass"] is False
    assert verdict["exit_code"] == 3


def test_stdout_and_stderr_are_captured():
    verdict = check(
        {
            "command": f"{sys.executable} -c "
            '\'import sys; print("out-marker"); print("err-marker", file=sys.stderr)\''
        }
    )
    assert "out-marker" in verdict["stdout_tail"]
    assert "err-marker" in verdict["stderr_tail"]


def test_against_testbed_acceptance_command(testbed):
    verdict = check(
        {
            "command": f"{sys.executable} -m pytest tests/test_calc.py -q",
            "cwd": str(testbed.path),
        }
    )
    assert verdict["pass"] is True
    assert verdict["exit_code"] == 0


def test_against_testbed_failing_command(testbed):
    verdict = check(
        {
            "command": f"{sys.executable} -m pytest tests/nonexistent_module.py -q",
            "cwd": str(testbed.path),
        }
    )
    assert verdict["pass"] is False
    assert verdict["exit_code"] != 0
