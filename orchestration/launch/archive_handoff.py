"""Change-level finish, leg 2/2: the `lifecycle archive` hand-off (M8).

`execute-change.yaml`'s `archive_handoff` step (after `full_healthcheck`
passes) calls this module to fold a completed change into the living spec,
honoring `spec-lifecycle`'s tasks-completion gate (M3 --
`internal/archive/tasks_gate.go`): a change with any unchecked tracked Step
is refused, never archived, no matter how the healthcheck went.

Authority note (`orchestration.md` sec 7.3, restated in
`implementation-plan.md` P8): archiving rides the **launch context that
holds approval authority**, never a Mode-B agent's own tool surface -- this
script step runs as part of the launcher-driven workflow, the same
launch-context boundary every other `lifecycle`-touching step in this repo
respects (`lifecycle approve`/`archive` are never given to a cast persona).

Calling convention (mirrors `orchestration.harness.*` / `notify_escalation`
-- see their docstrings): invocable as a script
(`python -m orchestration.launch.archive_handoff`, JSON on argv[0] (inline
or a file path) or stdin), importable (`archive(payload) -> dict`), emits
one JSON object to stdout, exit code reflects the outcome.

Input JSON:
    {
      "worktree": str,        # optional, default "." -- cwd to run `lifecycle` from
      "change_id": str,       # required when dry_run is false; optional (may be "") when true
      "dry_run": bool,        # optional, default true
      "lifecycle_bin": str    # optional, default "lifecycle"
    }

`dry_run` (default true -- the hermetic-tier default, wired as
`workflow.input.archive_dry_run` in `execute-change.yaml`) skips the real
`lifecycle archive` call entirely and reports what WOULD run -- no
openspec/ tree, no `lifecycle` binary, needed. Set `dry_run: false` from a
real launch context with a real `lifecycle` binary and an `openspec/` tree
under `worktree`.

**No force/override flag is ever passed** (`--force-gates`,
`--force-incomplete-tasks`, `--force-conflicts` are all absent from the
invocation below, by design -- see this module's header for why: the
tasks-completion gate must be able to refuse).

Output JSON (`status` is the field `execute-change.yaml`'s `output` block
routes on -- see there):
    {
      "status": "dry_run" | "archived" | "refused" | "error",
      "change_id": str,
      "worktree": str,
      "exit_code": int | null,       # the real `lifecycle archive` process's exit code (null
                                      # for dry_run)
      "result": {...} | null,        # `lifecycle archive --format json`'s stdout, parsed
                                      # (status "archived" only)
      "reason": str | null,          # the gate's refusal reason / error message
                                      # (status "refused"/"error")
      "would_run": [str, ...] | null # the argv that WOULD run (status "dry_run" only)
    }

Process exit code: 0 for "dry_run"/"archived" (this script ran to
completion and reports a real outcome), 1 for "refused" (the gate did its
job -- surfaced, not a crash), 2 for "error" (a harness-level input problem,
or `lifecycle` could not be run / did not exit with one of the three
documented codes -- see `cmd/lifecycle/archive.go`'s exit-code doc comment:
0 ok, 1 refused, 2 could-not-run).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orchestration.harness.common import coerce_bool

EXIT_GOOD = 0
EXIT_ATTENTION = 1
EXIT_ERROR = 2

# `lifecycle archive`'s own exit-code contract (cmd/lifecycle/archive.go):
# verified 2026-07-09 against the M3-tip binary on PATH (`lifecycle version`
# v0.1.1-...-4d1f002...) -- 0 ok, 1 refused (gate/tasks-completion/conflict/
# fold -- nothing written), 2 could not run.
LIFECYCLE_EXIT_OK = 0
LIFECYCLE_EXIT_REFUSED = 1


class ArchiveHandoffInputError(ValueError):
    """The step's input JSON is missing, malformed, or fails validation."""


def _read_input(argv: Sequence[str]) -> dict[str, Any]:
    if not argv or argv[0] == "-":
        raw = sys.stdin.read()
        source = "stdin"
    else:
        candidate = argv[0]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            path = Path(candidate)
            if not path.is_file():
                raise ArchiveHandoffInputError(
                    f"argv[0] is neither valid inline JSON nor an existing file path: {candidate!r}"
                ) from None
            raw = path.read_text()
            source = str(path)
        else:
            if not isinstance(data, dict):
                raise ArchiveHandoffInputError(
                    f"input JSON from argv[0] (inline JSON) must be an object, "
                    f"got {type(data).__name__}"
                )
            return data

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArchiveHandoffInputError(f"invalid JSON from {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ArchiveHandoffInputError(
            f"input JSON from {source} must be an object, got {type(data).__name__}"
        )
    return data


def _emit(verdict: dict[str, Any]) -> None:
    print(json.dumps(verdict, indent=2, sort_keys=True))


def archive(payload: dict[str, Any]) -> dict[str, Any]:
    worktree = payload.get("worktree") or "."
    change_id = payload.get("change_id") or ""
    dry_run = coerce_bool(payload.get("dry_run", True), default=True)
    lifecycle_bin = payload.get("lifecycle_bin", "lifecycle")

    if not dry_run and not change_id:
        raise ArchiveHandoffInputError(
            "'change_id' (non-empty string) is required when dry_run is false"
        )

    argv = [lifecycle_bin, "archive", change_id, "--format", "json"]

    if dry_run:
        return {
            "status": "dry_run",
            "change_id": change_id,
            "worktree": str(worktree),
            "exit_code": None,
            "result": None,
            "reason": None,
            "would_run": argv,
        }

    try:
        proc = subprocess.run(
            argv,
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {
            "status": "error",
            "change_id": change_id,
            "worktree": str(worktree),
            "exit_code": None,
            "result": None,
            "reason": f"could not run `{' '.join(argv)}`: {exc}",
            "would_run": None,
        }

    if proc.returncode == LIFECYCLE_EXIT_OK:
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return {
                "status": "error",
                "change_id": change_id,
                "worktree": str(worktree),
                "exit_code": proc.returncode,
                "result": None,
                "reason": f"`lifecycle archive` exited 0 but printed non-JSON stdout: {exc}",
                "would_run": None,
            }
        return {
            "status": "archived",
            "change_id": change_id,
            "worktree": str(worktree),
            "exit_code": proc.returncode,
            "result": result,
            "reason": None,
            "would_run": None,
        }

    if proc.returncode == LIFECYCLE_EXIT_REFUSED:
        return {
            "status": "refused",
            "change_id": change_id,
            "worktree": str(worktree),
            "exit_code": proc.returncode,
            "result": None,
            "reason": proc.stderr.strip() or proc.stdout.strip(),
            "would_run": None,
        }

    return {
        "status": "error",
        "change_id": change_id,
        "worktree": str(worktree),
        "exit_code": proc.returncode,
        "result": None,
        "reason": proc.stderr.strip() or proc.stdout.strip(),
        "would_run": None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        report = archive(_read_input(argv))
    except ArchiveHandoffInputError as exc:
        _emit({"status": "error", "reason": str(exc)})
        return EXIT_ERROR
    _emit(report)
    return {
        "dry_run": EXIT_GOOD,
        "archived": EXIT_GOOD,
        "refused": EXIT_ATTENTION,
        "error": EXIT_ERROR,
    }[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
