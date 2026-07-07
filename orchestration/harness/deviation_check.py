"""Deviation cross-check (spec orchestration.md sec 5.2/5.3).

A mechanical (no-LLM) proxy for the Verifier's full intent-vs-actual diff:
it cannot judge whether a change traces to a task/requirement (that needs an
agent), but it CAN mechanically decide whether every changed file is
*accounted for* - either because it sits within the milestone's declared
path-set, or because the Implementer logged it as a deviation in
`deviation.json`. Anything left over is a **material undeclared deviation**.

DEVIATION_SEMANTICS (coordinated with `diff_paths` so the two compose
without double-counting):
    - A changed file is COVERED (no problem, for this checker) iff:
        (a) it matches one of `allowed_globs` (an ordinary in-path change -
            `diff_paths` is the checker that cares about path confinement;
            this checker treats in-path changes as needing no individual
            declaration), OR
        (b) it matches a declared entry in the deviation log (by exact
            `path`, or by `path_glob`).
    - Otherwise it is a **material undeclared deviation** -> FAIL.
    - `diff_paths` and `deviation_check` answer different questions and are
      meant to be run together, not merged into one verdict:
        * `diff_paths` = "is the diff mechanically confined to the declared
          path-set" - a strict, no-exceptions gate.
        * `deviation_check` = "is every change outside that path-set
          explained by a logged deviation" - the gate that lets a
          *declared* out-of-path change through for downstream (Verifier /
          human) review, without ever calling it a silent pass in
          `diff_paths` itself.
      A file can therefore fail `diff_paths` (out of path) while passing
      `deviation_check` (declared) at the same time - that is intentional,
      not a bug: `diff_paths` keeps flagging it mechanically forever; the
      deviation log is what lets a human/Verifier decide it is acceptable.

Input JSON:
    {
      "repo_path": str,           # optional, default "."
      "base_ref": str,             # one of base_ref/diff_range required
      "diff_range": str,
      "allowed_globs": [str, ...], # required, same path-set diff_paths uses
      "deviation_log": str          # optional, default "deviation.json"
                                     # (resolved relative to repo_path unless absolute)
    }

deviation.json schema (an array; missing file == no declarations, i.e. []):
    [
      {"path": "relative/file.py", "reason": "...", "task_id": "M4-3"},
      {"path_glob": "scratch/**", "reason": "...", "task_id": "..."}
    ]
    Each entry has a non-empty "reason" and exactly one of "path"/"path_glob".

Output JSON:
    {
      "pass": bool,
      "changed_files": [str, ...],
      "allowed_globs": [str, ...],
      "undeclared_changes": [str, ...],
      "declared": [<the deviation-log entries that matched a changed file>, ...]
    }

Process exit code: 0 if pass, 1 if fail (an undeclared change exists), 2 on a
harness input error (including a malformed deviation log).
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orchestration.harness.common import (
    EXIT_ATTENTION,
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
    emit,
    git_diff_files,
    match_glob,
    path_matches_any,
    read_input,
)


def _load_deviation_log(repo_path: str, deviation_log: str) -> list[dict[str, Any]]:
    log_path = Path(deviation_log)
    if not log_path.is_absolute():
        log_path = Path(repo_path) / log_path
    if not log_path.is_file():
        return []

    try:
        data = json.loads(log_path.read_text())
    except json.JSONDecodeError as exc:
        raise HarnessInputError(f"invalid JSON in deviation log {log_path}: {exc}") from exc

    if not isinstance(data, list):
        raise HarnessInputError(
            f"deviation log {log_path} must be a JSON array, got {type(data).__name__}"
        )

    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise HarnessInputError(
                f"deviation log entry {i} must be an object, got {type(entry).__name__}"
            )
        has_path = "path" in entry
        has_glob = "path_glob" in entry
        if has_path == has_glob:
            raise HarnessInputError(
                f"deviation log entry {i} must have exactly one of 'path'/'path_glob': {entry!r}"
            )
        reason = entry.get("reason")
        if not reason or not isinstance(reason, str):
            raise HarnessInputError(
                f"deviation log entry {i} is missing a non-empty 'reason': {entry!r}"
            )

    return data


def _find_declaring_entry(
    changed_file: str, entries: list[dict[str, Any]]
) -> dict[str, Any] | None:
    for entry in entries:
        if "path" in entry and entry["path"] == changed_file:
            return entry
        if "path_glob" in entry and match_glob(changed_file, entry["path_glob"]):
            return entry
    return None


def check(payload: dict[str, Any]) -> dict[str, Any]:
    repo_path = payload.get("repo_path", ".")
    base_ref = payload.get("base_ref")
    diff_range = payload.get("diff_range")
    allowed_globs = payload.get("allowed_globs")
    deviation_log = payload.get("deviation_log", "deviation.json")

    if not allowed_globs or not isinstance(allowed_globs, list):
        raise HarnessInputError("'allowed_globs' (non-empty list of glob strings) is required")

    entries = _load_deviation_log(repo_path, deviation_log)
    changed = git_diff_files(repo_path, base_ref, diff_range)

    undeclared: list[str] = []
    matched: list[dict[str, Any]] = []
    for f in changed:
        if path_matches_any(f, allowed_globs):
            continue
        entry = _find_declaring_entry(f, entries)
        if entry is None:
            undeclared.append(f)
        elif entry not in matched:
            matched.append(entry)

    return {
        "pass": not undeclared,
        "changed_files": changed,
        "allowed_globs": allowed_globs,
        "undeclared_changes": undeclared,
        "declared": matched,
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
