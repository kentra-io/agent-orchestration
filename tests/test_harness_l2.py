import sys

from orchestration.harness.l2_healthcheck import check

OK = f"{sys.executable} -c 'raise SystemExit(0)'"
BAD = f"{sys.executable} -c 'raise SystemExit(1)'"


def test_all_pass():
    verdict = check({"commands": [OK, OK, OK]})
    assert verdict["pass"] is True
    assert all(r["pass"] for r in verdict["results"])
    assert len(verdict["results"]) == 3


def test_one_fail_fails_overall():
    verdict = check({"commands": [OK, BAD, OK]})
    assert verdict["pass"] is False
    assert [r["pass"] for r in verdict["results"]] == [True, False, True]


def test_against_testbed_suite(testbed):
    verdict = check(
        {
            "commands": [f"{sys.executable} -m pytest tests/test_calc.py tests/test_stable.py -q"],
            "cwd": str(testbed.path),
        }
    )
    assert verdict["pass"] is True


def test_against_testbed_suite_with_a_break(testbed):
    testbed.write(
        "sample_pkg/calc.py",
        "def add(a, b):\n    return a - b  # bug\n\n\ndef sub(a, b):\n    return a - b\n",
    )
    verdict = check(
        {
            "commands": [f"{sys.executable} -m pytest tests/test_calc.py -q"],
            "cwd": str(testbed.path),
        }
    )
    assert verdict["pass"] is False
