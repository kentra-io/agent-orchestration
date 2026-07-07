"""L1 - executable acceptance check.

Runs a milestone's declared acceptance-check command and gates pass/fail on
its exit code. See `orchestration/harness/README.md` for the shared calling
convention (how input is read, how the verdict is emitted, exit codes).

Input JSON:
    {
      "command": str,          # required: shell command string
      "cwd": str,               # optional, default "."
      "timeout": number,        # optional, seconds, default 600
      "env": {str: str}         # optional, extra/overriding env vars
    }

Output JSON:
    {
      "pass": bool,
      "exit_code": int,
      "stdout_tail": str,
      "stderr_tail": str,
      "command": str
    }

Process exit code: 0 if pass, 1 if fail, 2 on a harness input error.
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
    tail,
)


def check(payload: dict[str, Any]) -> dict[str, Any]:
    command = payload.get("command")
    if not command or not isinstance(command, str):
        raise HarnessInputError("'command' (non-empty string) is required")

    cwd = payload.get("cwd", ".")
    timeout = payload.get("timeout", 600)
    env = payload.get("env")

    exit_code, stdout, stderr = run_command(command, cwd=cwd, timeout=timeout, env_overrides=env)
    return {
        "pass": exit_code == 0,
        "exit_code": exit_code,
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
        "command": command,
    }


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        verdict = check(read_input(argv))
    except HarnessInputError as exc:
        emit({"error": str(exc)})
        return EXIT_ERROR
    emit(verdict)
    return EXIT_GOOD if verdict["pass"] else EXIT_ATTENTION


if __name__ == "__main__":
    raise SystemExit(main())
