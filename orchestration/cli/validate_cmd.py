"""`orch validate <change-id> [--repo PATH]` — standalone plan validation (design §7).

Validates a change's plan through the same `lifecycle apply` surface the
launcher's pre-flight trusts (`orchestration.resume.plan.load_milestones_from_apply`),
without contacting the daemon or docker. On success it prints one summary line
per milestone — id, title, and whether a structured validation contract is
present — followed by a milestone total, and exits 0. A plan-validation failure
(`PlanReadError`) prints the error plus the available (non-archive) change
folders to stderr and exits 1. A missing `lifecycle` binary is an environment
error (exit 2) — distinct from the launcher's warn-and-proceed, because
validation is this command's entire job (spec: cli-validate).

Exit codes (§10): 0 ok · 1 user-fixable · 2 environment broken.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from orchestration.resume.plan import PlanReadError, load_milestones_from_apply

EXIT_OK, EXIT_USER, EXIT_ENV = 0, 1, 2


def _git_toplevel() -> str | None:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _resolve_repo(args: argparse.Namespace) -> str | None:
    """Mirror the launcher's repo resolution (launch_cmd._resolve_repo): the git
    toplevel of cwd by default, overridable with --repo."""
    if args.repo:
        return str(Path(args.repo).resolve())
    top = _git_toplevel()
    if top is None:
        print("not inside a git repository — pass --repo", file=sys.stderr)
    return top


def cmd_validate(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args)
    if repo is None:
        return EXIT_USER
    if shutil.which("lifecycle") is None:
        print(
            "`lifecycle` not on PATH — install the spec-lifecycle CLI and ensure "
            "its binary is on PATH, then re-run `orch validate`",
            file=sys.stderr,
        )
        return EXIT_ENV
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
        return EXIT_USER
    for m in milestones:
        contract = "contract" if m.get("contract") else "no contract"
        print(f"{m.get('id')}  {m.get('title')}  [{contract}]")
    print(f"{len(milestones)} milestone(s), plan valid")
    return EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("validate", help="validate a change's plan (daemon-free, design §7)")
    p.add_argument("change_id")
    p.add_argument("--repo", help="target repo (default: git toplevel of cwd)")
    p.set_defaults(func=cmd_validate)
