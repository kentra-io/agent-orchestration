"""L2 - generic healthcheck.

Runs a configured list of commands (the project's full test suite, build,
lint - the regression guard) and passes iff every one of them passes. See
`orchestration/harness/README.md` for the shared calling convention.

Input JSON:
    {
      "commands": [str, ...],  # required, non-empty list of shell command strings
      "cwd": str,                # optional, default "."
      "timeout": number,         # optional, seconds, per command, default 600
      "env": {str: str}          # optional, extra/overriding env vars
    }

Output JSON:
    {
      "pass": bool,
      "results": [
        {"command": str, "pass": bool, "exit_code": int, "stdout_tail": str, "stderr_tail": str},
        ...
      ]
    }

Process exit code: 0 if pass (all commands passed), 1 if fail (any command
failed), 2 on a harness input error.
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
    commands = payload.get("commands")
    if not commands or not isinstance(commands, list):
        raise HarnessInputError("'commands' (non-empty list of strings) is required")

    cwd = payload.get("cwd", ".")
    timeout = payload.get("timeout", 600)
    env = payload.get("env")

    results = []
    for command in commands:
        if not isinstance(command, str) or not command:
            raise HarnessInputError(
                f"every entry in 'commands' must be a non-empty string, got {command!r}"
            )
        exit_code, stdout, stderr = run_command(
            command, cwd=cwd, timeout=timeout, env_overrides=env
        )
        results.append(
            {
                "command": command,
                "pass": exit_code == 0,
                "exit_code": exit_code,
                "stdout_tail": tail(stdout),
                "stderr_tail": tail(stderr),
            }
        )

    return {
        "pass": all(r["pass"] for r in results),
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
    return EXIT_GOOD if verdict["pass"] else EXIT_ATTENTION


if __name__ == "__main__":
    raise SystemExit(main())
