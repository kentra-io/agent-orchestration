"""Flakiness quarantine (spec orchestration.md sec 5.4).

Runs a test command N times; if the pass-rate falls below `threshold` the
test is **quarantined** - reported as quarantined, never silently reported
as a clean green. A quarantine verdict is a routing signal (send to a human
/ mark unreliable), not a retried-until-green pass.

Input JSON:
    {
      "command": str,        # required: shell command string (usually a single test)
      "cwd": str,              # optional, default "."
      "runs": int,              # optional, default 10
      "threshold": number,      # optional, default 0.95 (pass_rate below this -> quarantined)
      "timeout": number,        # optional, seconds, per run, default 600
      "env": {str: str}         # optional, extra/overriding env vars
    }

Output JSON:
    {
      "quarantined": bool,
      "runs": int,
      "passes": int,
      "pass_rate": number,
      "threshold": number,
      "command": str,
      "results": [{"run": int, "pass": bool, "exit_code": int}, ...]
    }

Process exit code: 0 if NOT quarantined, 1 if quarantined, 2 on a harness
input error. (There is no "pass" key: a flakiness verdict is a quarantine
signal, not a pass/fail judgment of the code under test - see
`orchestration/harness/README.md`.)
"""

import sys
from collections.abc import Sequence
from typing import Any

from orchestration.harness.common import (
    EXIT_ATTENTION,
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
    emit,
    read_input,
    run_command,
)


def check(payload: dict[str, Any]) -> dict[str, Any]:
    command = payload.get("command")
    if not command or not isinstance(command, str):
        raise HarnessInputError("'command' (non-empty string) is required")

    cwd = payload.get("cwd", ".")
    runs = payload.get("runs", 10)
    threshold = payload.get("threshold", 0.95)
    timeout = payload.get("timeout", 600)
    env = payload.get("env")

    if not isinstance(runs, int) or runs < 1:
        raise HarnessInputError("'runs' must be an integer >= 1")

    results = []
    passes = 0
    for i in range(runs):
        exit_code, _stdout, _stderr = run_command(
            command, cwd=cwd, timeout=timeout, env_overrides=env
        )
        ok = exit_code == 0
        passes += int(ok)
        results.append({"run": i + 1, "pass": ok, "exit_code": exit_code})

    pass_rate = passes / runs
    return {
        "quarantined": pass_rate < threshold,
        "runs": runs,
        "passes": passes,
        "pass_rate": pass_rate,
        "threshold": threshold,
        "command": command,
        "results": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        verdict = check(read_input(argv))
    except HarnessInputError as exc:
        emit({"error": str(exc)})
        return EXIT_ERROR
    emit(verdict)
    return EXIT_ATTENTION if verdict["quarantined"] else EXIT_GOOD


if __name__ == "__main__":
    raise SystemExit(main())
