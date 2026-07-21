"""`orch validate <change-id> [--repo PATH]` — standalone plan validation.

Runs the change's plan through the same `lifecycle apply` surface the
launcher's pre-flight trusts (`orchestration/cli/launch_cmd.py`'s
`_validate_change`), but as its own verb: no daemon call, no docker. Because
validation is this command's ENTIRE job, a missing `lifecycle` binary is a
hard environment error (exit 2), not the launcher's warn-and-proceed — see
the cli-validate spec delta.

The repo is resolved the same way the launcher resolves it (git toplevel of
cwd, overridable with `--repo`); the `_git_toplevel`/`_resolve_repo` pair is
mirrored from `launch_cmd` rather than imported, keeping this a private helper
per module (tasks.md M1 step 1).

Exit codes (design §10): 0 valid · 1 user-fixable (bad/unknown change) ·
2 environment broken (no `lifecycle` on PATH).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from orchestration.resume.plan import PlanReadError, load_milestones_from_apply


def _git_toplevel() -> str | None:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _resolve_repo(args: argparse.Namespace) -> str | None:
    if args.repo:
        return str(Path(args.repo).resolve())
    top = _git_toplevel()
    if top is None:
        print("not inside a git repository — pass --repo", file=sys.stderr)
    return top


def cmd_validate(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args)
    if repo is None:
        return 1
    if shutil.which("lifecycle") is None:
        print(
            "`lifecycle` not on PATH — install spec-lifecycle so plan "
            "validation can run (this command's entire job); then retry",
            file=sys.stderr,
        )
        return 2
    try:
        milestones = load_milestones_from_apply(args.change_id, cwd=repo)
    except PlanReadError as exc:
        print(f"change {args.change_id!r} failed plan validation: {exc}", file=sys.stderr)
        changes_dir = Path(repo) / "openspec" / "changes"
        if changes_dir.is_dir():
            names = sorted(
                p.name for p in changes_dir.iterdir() if p.is_dir() and p.name != "archive"
            )
            print("available changes: " + ", ".join(names), file=sys.stderr)
        return 1
    for m in milestones:
        marker = "contract" if m.get("contract") else "no contract"
        print(f"{m.get('id')}  {m.get('title')}  [{marker}]")
    print(f"{len(milestones)} milestone(s), plan valid")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("validate", help="validate a change's plan without the daemon (design §7)")
    p.add_argument("change_id")
    p.add_argument("--repo", help="target repo (default: git toplevel of cwd)")
    p.set_defaults(func=cmd_validate)
