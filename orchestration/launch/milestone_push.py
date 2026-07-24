"""Deterministic per-milestone push (best-effort publish of ONE milestone commit).

`milestone.yaml`'s `push` step calls this module after `milestone_commit`
persists the verified diff, so the branch, commits, and issue mirror a human
sees on GitHub are the state of the run (spec `github-mirror`: "Verified
milestone commits are pushed to the run branch"). It is the deliberate
counterpart to `milestone_commit`, which stays push-free by design (design.md
D3): commit is deterministic-local and load-bearing (its failure terminates
the milestone); push is best-effort-network and MUST NOT fail the milestone or
halt the run. A push failure (remote outage, auth, non-fast-forward) is
reported, the run proceeds on its local commits, and a later successful push
publishes the accumulated branch.

Authority note (same consent boundary as `milestone_commit`): the push verb
stays out of every LLM's hands — this is a `script` step run by the launch
context, never a cast persona.

Calling convention (mirrors `orchestration.harness.*` / `milestone_commit` /
`notify_escalation`): invocable as a script
(`python -m orchestration.launch.milestone_push`, JSON on argv[0] (inline or a
file path) or stdin), importable (`push(payload) -> tuple[dict, int]`), emits
one pretty JSON object to stdout, exit code reflects the outcome.

Input JSON:
    {
      "worktree": str,   # optional, default "." — the git repo to push from
      "branch": str,     # the run's named branch; required non-empty when dry_run false
      "remote": str,     # optional, default "origin"
      "dry_run": bool    # optional, default true (hermetic-tier default)
    }

`dry_run` (default true — wired as `workflow.input.push_dry_run`, same pattern
as `commit_dry_run`/`notify_dry_run`) skips git entirely and reports the argv
that WOULD run, so the hermetic Stub tier exercises the push path with no
network, no git call, and no GitHub token.

Push mechanics (design.md D3): a plain fast-forward push of
`HEAD:refs/heads/<branch>` to the remote. **Never `--force`** — a
non-fast-forward rejection is reported as a push failure (attention exit) and
the run proceeds. Auth rides `gh`'s git credential helper (`gh auth setup-git`
in the daemon image); no raw token is ever written into a remote URL or argv.

Output JSON:
    {
      "status": "dry_run" | "pushed" | "push_failed" | "error",
      "pushed": bool,
      "branch": str | null,
      "git_exit_code": int | null,   # the `git push` subprocess exit code (live only)
      "git_stderr_tail": str | null, # tail of git's stderr (or the exception text)
      "would_run": [str, ...] | null,# the push argv (status "dry_run" only)
      "reason": str | null           # detail (status "error" only)
    }

The `git_` prefix on the two subprocess fields keeps them from colliding with
the enclosing `script` step's own top-level `exit_code`/`stderr` keys (see
`orchestration/harness/README.md`'s calling-convention note).

Process exit code (the attention convention): 0 for "dry_run"/"pushed", 1 for
"push_failed" (attempted the push and git rejected/failed it — best effort, so
the run continues), 2 for "error" (a harness-level input error, e.g. dry_run
false with an empty branch, or malformed input).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from typing import Any

from orchestration.harness.common import (
    EXIT_ATTENTION,
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
    coerce_bool,
    emit,
    read_input,
    tail,
)

_GIT_TIMEOUT_SECONDS = 60.0


def _push_argv(worktree: str, remote: str, branch: str) -> list[str]:
    """The exact push argv — plain fast-forward, no `--force`, no rewritten URL."""
    return ["git", "-C", worktree, "push", remote, f"HEAD:refs/heads/{branch}"]


def push(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    worktree = str(payload.get("worktree") or ".")
    remote = str(payload.get("remote") or "origin")
    branch_raw = payload.get("branch")
    if isinstance(branch_raw, str):
        branch = branch_raw.strip()
    elif branch_raw is None:
        branch = ""
    else:
        branch = str(branch_raw).strip()
    dry_run = coerce_bool(payload.get("dry_run", True), default=True)

    if not dry_run and not branch:
        raise HarnessInputError("'branch' (non-empty string) is required when dry_run is false")

    argv = _push_argv(worktree, remote, branch)

    if dry_run:
        return {
            "status": "dry_run",
            "pushed": False,
            "branch": branch or None,
            "git_exit_code": None,
            "git_stderr_tail": None,
            "would_run": argv,
            "reason": None,
        }, EXIT_GOOD

    # Live mode: best effort. Never raise on a git failure — a rejected or
    # failed push must be reported, not allowed to halt the run.
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "push_failed",
            "pushed": False,
            "branch": branch,
            "git_exit_code": None,
            "git_stderr_tail": f"git could not run: {exc}",
            "would_run": None,
            "reason": None,
        }, EXIT_ATTENTION

    stderr_tail = tail(proc.stderr) if proc.stderr else None
    if proc.returncode == 0:
        return {
            "status": "pushed",
            "pushed": True,
            "branch": branch,
            "git_exit_code": 0,
            "git_stderr_tail": stderr_tail,
            "would_run": None,
            "reason": None,
        }, EXIT_GOOD

    return {
        "status": "push_failed",
        "pushed": False,
        "branch": branch,
        "git_exit_code": proc.returncode,
        "git_stderr_tail": stderr_tail,
        "would_run": None,
        "reason": None,
    }, EXIT_ATTENTION


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        payload = read_input(argv)
        verdict, code = push(payload)
    except HarnessInputError as exc:
        emit(
            {
                "status": "error",
                "pushed": False,
                "branch": None,
                "git_exit_code": None,
                "git_stderr_tail": None,
                "would_run": None,
                "reason": str(exc),
            }
        )
        return EXIT_ERROR
    emit(verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
