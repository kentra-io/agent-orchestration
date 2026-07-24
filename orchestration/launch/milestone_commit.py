"""Deterministic per-milestone commit (the durability finish for ONE milestone).

`milestone.yaml`'s `commit` step calls this module after the milestone's
gates + Verifier have passed, so the verified diff is persisted at the same
cadence as the workflow's checkpoint cursor — closing the durability gap
surfaced on the first live runs (harness
`tasks/orchestration-does-not-commit-milestones.md`): verified work used to
live only in the worktree until a human committed it out-of-band.

Authority note (same consent boundary as `archive_handoff`, spec
`orchestration.md` sec 7.3): the commit verb stays out of every LLM's hands
— this is a `script` step run by the launch context, never a cast persona.
It also preserves the author≠verifier spine: the Implementer never commits
its own work; the orchestration commits exactly what the Verifier judged.

Calling convention (mirrors `orchestration.harness.*` / `archive_handoff`):
invocable as a script (`python -m orchestration.launch.milestone_commit`,
JSON on argv[0] (inline or a file path) or stdin), importable
(`commit(payload) -> dict`), emits one JSON object to stdout, exit code
reflects the outcome.

Input JSON:
    {
      "worktree": str,          # optional, default "." — the git repo to commit in
      "milestone_id": str|int,  # required — plan milestone id (int from `lifecycle apply`)
      "milestone_title": str,   # optional — first line becomes the commit subject body
      "change_id": str,         # optional — appended as "(<change_id>)" when non-empty
      "dry_run": bool,          # optional, default true (hermetic-tier default)
      "paths": [str, ...]       # optional — pathspecs to confine `git add` to; the
                                # milestone's declared contract.paths in the live wiring.
                                # Empty/absent = stage the whole worktree (`git add -A`).
                                # Also accepted as a JSON-encoded string (the workflow
                                # forwards it through a string-typed input).
    }

`dry_run` (default true — wired as `workflow.input.commit_dry_run`, same
pattern as `notify_dry_run`/`archive_dry_run`) skips git entirely and
reports what WOULD run, so the hermetic stub tier never needs a git repo
and can never commit into a checkout.

Path confinement: when `paths` is non-empty, only files matching those
pathspecs are staged — an errant write outside the milestone's declared
paths stays uncommitted (visible in the next milestone's diff) instead of
being silently folded into the milestone commit. But when confinement
leaves NOTHING staged while the worktree is not actually clean, that is
the #23 defect, not a legitimate no-op — see "empty_paths" below.

Output JSON:
    {
      "status": "dry_run" | "committed" | "clean" | "empty_paths" | "error",
      "committed": bool,
      "sha": str | null,            # the new commit (status "committed" only)
      "message": str | null,        # the commit subject used / that would be used
      "reason": str | null,         # detail (status "empty_paths"/"error" only)
      "would_run": [[str, ...], ...] | null   # argv list (status "dry_run" only)
    }

Process exit code: 0 for "dry_run"/"committed"/"clean", 2 for "empty_paths"/
"error" (malformed input, not a git repo, a git command failed, or the #23
empty-paths trap described below) — a failed commit after verified work
must be LOUD, not silently swallowed.

"empty_paths" (#23): `paths` was declared non-empty but nothing under those
pathspecs had a diff to stage, AND the worktree has changes somewhere else
(`git status --porcelain` is non-empty outside the staged set). This is the
"declared contract.paths don't match where the verified work actually
landed" trap — silently reporting "clean" here would let the workflow
proceed as if the milestone passed while the real diff sits uncommitted,
to be lost on cleanup. Distinguished from a genuinely clean tree (also
`git diff --cached --quiet == 0`, but `git status --porcelain` is ALSO
empty), which stays "clean"/EXIT_GOOD — a legitimately no-diff milestone
(e.g. verification-only work) must not be flagged as a durability failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orchestration.harness.common import (
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
    coerce_bool,
    emit,
    read_input,
)

# Fallback identity so the commit is deterministic even where no git
# user.name/user.email is configured (e.g. the daemon container). A repo or
# environment identity (git config, GIT_AUTHOR_* / GIT_COMMITTER_* env, as
# the consuming project's box config already sets) always wins.
_FALLBACK_IDENT_NAME = "agent-orchestration"
_FALLBACK_IDENT_EMAIL = "agent-orchestration@localhost"

_GIT_TIMEOUT_SECONDS = 60.0


def build_message(milestone_id: Any, milestone_title: str | None, change_id: str | None) -> str:
    """`M<n>: <title first line> (<change_id>)` — the convention the first
    live change's hand-made milestone commits already used."""
    raw_id = str(milestone_id).strip()
    label = f"M{raw_id}" if raw_id.isdigit() else raw_id
    title = (milestone_title or "").strip().splitlines()[0] if milestone_title else ""
    subject = f"{label}: {title}" if title else label
    if change_id:
        subject += f" ({change_id})"
    return subject


def _git(worktree: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", worktree, *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def _has_identity(worktree: str) -> bool:
    for key in ("user.name", "user.email"):
        proc = _git(worktree, ["config", key])
        if proc.returncode != 0 or not proc.stdout.strip():
            return False
    return True


def commit(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    milestone_id = payload.get("milestone_id")
    if milestone_id is None or str(milestone_id).strip() == "":
        raise HarnessInputError("'milestone_id' is required")
    worktree = str(payload.get("worktree") or ".")
    dry_run = coerce_bool(payload.get("dry_run", True), default=True)
    paths = payload.get("paths") or []
    if isinstance(paths, str):
        # The workflow forwards contract.paths through a string-typed input
        # (`commit_paths`), JSON-encoded via `| tojson`.
        text = paths.strip()
        try:
            paths = json.loads(text) if text else []
        except json.JSONDecodeError as exc:
            raise HarnessInputError(f"'paths' string is not valid JSON: {exc}") from exc
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise HarnessInputError("'paths' must be a list of pathspec strings")

    message = build_message(milestone_id, payload.get("milestone_title"), payload.get("change_id"))
    add_argv = ["git", "add", "-A"] + (["--", *paths] if paths else [])
    commit_argv = ["git", "commit", "-m", message]

    if dry_run:
        return {
            "status": "dry_run",
            "committed": False,
            "sha": None,
            "message": message,
            "would_run": [add_argv, commit_argv],
            "reason": None,
        }, EXIT_GOOD

    def error(reason: str) -> tuple[dict[str, Any], int]:
        return {
            "status": "error",
            "committed": False,
            "sha": None,
            "message": message,
            "would_run": None,
            "reason": reason,
        }, EXIT_ERROR

    try:
        # #30: refuse to commit unless `worktree` is itself the repo toplevel.
        # A bare directory nested inside a real checkout (e.g. a pytest
        # tmp_path relocated into the run worktree) would otherwise resolve
        # the ENCLOSING repo and sweep its dirty state onto the live branch.
        toplevel = _git(worktree, ["rev-parse", "--show-toplevel"])
        if toplevel.returncode != 0:
            return error(
                f"`git rev-parse --show-toplevel` failed "
                f"(exit {toplevel.returncode}): {toplevel.stderr.strip()}"
            )
        if Path(toplevel.stdout.strip()).resolve() != Path(worktree).resolve():
            return error(
                "worktree is not a git repo toplevel; refusing to commit into "
                "the enclosing repo (#30)"
            )

        add = _git(worktree, add_argv[1:])
        if add.returncode != 0:
            return error(f"`git add` failed (exit {add.returncode}): {add.stderr.strip()}")

        staged = _git(worktree, ["diff", "--cached", "--quiet"])
        if staged.returncode == 0:
            if paths:
                # Nothing under the declared paths had a diff to stage --
                # but is the REST of the worktree also clean, or is there
                # verified work sitting outside the declared contract.paths
                # (#23)? `git status --porcelain` covers the whole worktree,
                # not just `paths`, so any output here is necessarily
                # outside those paths (had it been inside, `git add -A --
                # <paths>` above would have staged it, and `diff --cached`
                # would not be empty).
                worktree_status = _git(worktree, ["status", "--porcelain"])
                if worktree_status.returncode != 0:
                    return error(
                        f"`git status` failed (exit {worktree_status.returncode}): "
                        f"{worktree_status.stderr.strip()}"
                    )
                if worktree_status.stdout.strip():
                    return {
                        "status": "empty_paths",
                        "committed": False,
                        "sha": None,
                        "message": message,
                        "would_run": None,
                        "reason": (
                            f"declared contract.paths {paths!r} matched no changes to "
                            "stage, but the worktree has changes outside those paths "
                            "-- refusing to report a clean/passed milestone while "
                            "verified work sits uncommitted (#23). "
                            f"git status --porcelain:\n{worktree_status.stdout.strip()}"
                        ),
                    }, EXIT_ERROR
            return {
                "status": "clean",
                "committed": False,
                "sha": None,
                "message": message,
                "would_run": None,
                "reason": None,
            }, EXIT_GOOD
        if staged.returncode != 1:
            return error(
                f"`git diff --cached` failed (exit {staged.returncode}): {staged.stderr.strip()}"
            )

        ident: list[str] = []
        if not _has_identity(worktree):
            ident = [
                "-c",
                f"user.name={_FALLBACK_IDENT_NAME}",
                "-c",
                f"user.email={_FALLBACK_IDENT_EMAIL}",
            ]
        committed = _git(worktree, [*ident, *commit_argv[1:]])
        if committed.returncode != 0:
            detail = (committed.stderr.strip() or committed.stdout.strip())[:500]
            return error(f"`git commit` failed (exit {committed.returncode}): {detail}")

        sha = _git(worktree, ["rev-parse", "HEAD"]).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return error(f"git could not run: {exc}")

    return {
        "status": "committed",
        "committed": True,
        "sha": sha,
        "message": message,
        "would_run": None,
        "reason": None,
    }, EXIT_GOOD


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        payload = read_input(argv)
        verdict, code = commit(payload)
    except HarnessInputError as exc:
        emit(
            {
                "status": "error",
                "committed": False,
                "sha": None,
                "message": None,
                "would_run": None,
                "reason": str(exc),
            }
        )
        return EXIT_ERROR
    emit(verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
