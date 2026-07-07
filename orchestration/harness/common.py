"""Shared plumbing for the deterministic verification-harness checkers.

Every checker in this package (`l1_acceptance`, `l2_healthcheck`, `diff_paths`,
`deviation_check`, `flakiness`) follows the same calling convention — see
`orchestration/harness/README.md` for the authoritative contract. This module
holds the bits that convention shares: input parsing, verdict emission, tail
truncation, subprocess execution, and glob matching.

Determinism note: checkers never embed wall-clock timestamps, durations, or
other non-reproducible data in their verdict JSON. "Same input -> same
verdict" holds within a fixed environment/repo state (the property the M4
tests assert); it does not claim byte-identical output across machines with
different `git`/`python` versions or PATH contents.
"""

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_TAIL_LINES = 100
DEFAULT_TAIL_CHARS = 20_000
DEFAULT_TIMEOUT = 600.0

# Process exit codes, shared by every checker's `main()`:
#   0 -> verdict is "good" (pass=True, or quarantined=False for `flakiness`)
#   1 -> verdict is "needs attention" (pass=False, or quarantined=True)
#   2 -> harness-level error (bad input) - no verdict was computed
EXIT_GOOD = 0
EXIT_ATTENTION = 1
EXIT_ERROR = 2

# subprocess.run's own sentinel for a command that timed out.
TIMEOUT_EXIT_CODE = 124


class HarnessInputError(ValueError):
    """The checker's input JSON is missing, malformed, or fails validation."""


def read_input(argv: Sequence[str]) -> dict[str, Any]:
    """Read a checker's JSON input per the shared convention.

    - `argv[0]`, if present and not `"-"`, is tried first as an inline JSON
      string, then (if that fails to parse) as a path to a JSON file.
    - If `argv` is empty, or `argv[0] == "-"`, the JSON is read from stdin.

    Always returns a `dict` (the input's top-level JSON value must be an
    object); raises `HarnessInputError` otherwise.
    """
    if not argv or argv[0] == "-":
        return _parse_and_validate(sys.stdin.read(), "stdin")

    candidate = argv[0]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        pass
    else:
        return _validate(data, "argv[0] (inline JSON)")

    path = Path(candidate)
    if not path.is_file():
        raise HarnessInputError(
            f"argv[0] is neither valid inline JSON nor an existing file path: {candidate!r}"
        )
    return _parse_and_validate(path.read_text(), str(path))


def _parse_and_validate(raw: str, source: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HarnessInputError(f"invalid JSON from {source}: {exc}") from exc
    return _validate(data, source)


def _validate(data: Any, source: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise HarnessInputError(
            f"input JSON from {source} must be an object, got {type(data).__name__}"
        )
    return data


def emit(verdict: dict[str, Any]) -> None:
    """Write a verdict to stdout as pretty, key-sorted JSON (one call, one blob)."""
    print(json.dumps(verdict, indent=2, sort_keys=True))


def tail(
    text: str, max_lines: int = DEFAULT_TAIL_LINES, max_chars: int = DEFAULT_TAIL_CHARS
) -> str:
    """Return the tail of `text`, capped at `max_lines` lines and `max_chars` chars."""
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[-max_chars:]
    return out


def run_command(
    command: str,
    cwd: str = ".",
    timeout: float = DEFAULT_TIMEOUT,
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a shell command string in `cwd`; return `(exit_code, stdout, stderr)`.

    Uses `shell=True` (a Conductor `script` step hands us a single command
    string, same as a shell invocation would). Never raises on a non-zero
    exit or on timeout: a timeout is reported as exit code 124 (matching the
    coreutils `timeout` convention) with whatever output was captured before
    the kill, plus a `[harness] command timed out ...` marker appended to
    stderr.
    """
    import os

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\n[harness] command timed out after {timeout}s\n"
        return TIMEOUT_EXIT_CODE, stdout, stderr
    return proc.returncode, proc.stdout, proc.stderr


@lru_cache(maxsize=256)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a POSIX-style glob into a regex.

    Supports `**` (matches across path separators, including zero segments
    when followed by `/`), `*` (matches within one path segment), and `?`
    (one character, not `/`). Patterns are matched against repo-relative,
    forward-slash paths (git's own `diff --name-only` convention).
    """
    pattern = pattern.replace("\\", "/")
    i, n = 0, len(pattern)
    parts: list[str] = []
    while i < n:
        c = pattern[i]
        if pattern[i : i + 2] == "**":
            if i + 2 < n and pattern[i + 2] == "/":
                parts.append("(?:.*/)?")
                i += 3
            else:
                parts.append(".*")
                i += 2
        elif c == "*":
            parts.append("[^/]*")
            i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def match_glob(path: str, pattern: str) -> bool:
    """True if `path` (repo-relative, `/`-separated) matches `pattern`."""
    return _glob_to_regex(pattern).match(path) is not None


def path_matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(match_glob(path, p) for p in patterns)


def git_diff_files(repo_path: str, base_ref: str | None, diff_range: str | None) -> list[str]:
    """Return the sorted, de-duplicated list of files changed per `git diff --name-only`.

    Exactly one of `base_ref` / `diff_range` must be given:
    - `diff_range`: passed through verbatim, e.g. `"main...HEAD"` (committed-only,
      merge-base diff) or `"main..HEAD"` (committed-only, direct diff).
    - `base_ref`: compares `base_ref` against the current working tree (`git
      diff <base_ref>`), so it also picks up staged and unstaged changes — the
      useful default mid-milestone, before the Implementer has committed.
    """
    if diff_range:
        args = ["git", "diff", "--name-only", diff_range]
    elif base_ref:
        args = ["git", "diff", "--name-only", base_ref]
    else:
        raise HarnessInputError("one of 'base_ref' or 'diff_range' is required")

    proc = subprocess.run(args, cwd=repo_path, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise HarnessInputError(f"git diff failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return sorted({line for line in proc.stdout.splitlines() if line})
