"""`orch launch <change-id>` / raw-payload escape hatch (design §7).

Production tier: validate via `lifecycle apply` (the same surface the
workflow's read_plan uses at run time — the CLI runs it once, for early
loud failure + the iteration-budget guard), then POST a box-enabled payload.
`--stub` flips the entire hermetic tier with one flag.

Stub fixtures are materialized under `<repo>/.orchestration-stub/<change-id>/`
— NOT the CLI venv — because the daemon container must be able to read them:
the code root is mounted at the SAME path in-container, while the CLI's own
install location is not.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import webbrowser
from importlib import resources
from pathlib import Path

from orchestration import client
from orchestration.cli import payloads
from orchestration.resume.plan import PlanReadError, load_milestones_from_apply

# execute-change.yaml burns ~2 root iterations per milestone (cursor +
# milestone_step) plus read_plan/full_healthcheck/archive_handoff overhead
# against its max_iterations: 60 budget (ADR-0002: computed, never guessed).
MAX_MILESTONES = 27


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


def _validate_change(repo: str, change_id: str) -> bool:
    """Early loud failure for the production tier. True = proceed."""
    if shutil.which("lifecycle") is None:
        print(
            "warning: `lifecycle` not on PATH — skipping local plan validation "
            "(the daemon still validates at launch)",
            file=sys.stderr,
        )
        return True
    try:
        milestones = load_milestones_from_apply(change_id, cwd=repo)
    except PlanReadError as exc:
        print(f"change {change_id!r} failed plan validation: {exc}", file=sys.stderr)
        changes_dir = Path(repo) / "openspec" / "changes"
        if changes_dir.is_dir():
            names = sorted(
                p.name for p in changes_dir.iterdir() if p.is_dir() and p.name != "archive"
            )
            print("available changes: " + ", ".join(names), file=sys.stderr)
        return False
    if len(milestones) > MAX_MILESTONES:
        print(
            f"plan has {len(milestones)} milestones; execute-change.yaml's "
            f"max_iterations budget (60) supports at most {MAX_MILESTONES} — "
            "split the change or raise the workflow limit",
            file=sys.stderr,
        )
        return False
    return True


def _materialize_stub_files(
    repo: str, change_id: str, milestones_file: str | None
) -> tuple[str, str]:
    dest = Path(repo) / ".orchestration-stub" / change_id
    dest.mkdir(parents=True, exist_ok=True)
    data = resources.files("orchestration.cli") / "data"
    plan_text = (
        Path(milestones_file).read_text(encoding="utf-8")
        if milestones_file
        else (data / "stub_demo.json").read_text(encoding="utf-8")
    )
    (dest / "plan.json").write_text(plan_text, encoding="utf-8")
    (dest / "stub_script.json").write_text(
        (data / "stub_demo.stub.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return str(dest / "plan.json"), str(dest / "stub_script.json")


def _print_and_open(report: dict, no_open: bool) -> None:
    summary = {
        k: report.get(k) for k in ("worktree", "branch", "pid", "dashboard_url", "registry_path")
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("track it: orch runs · orch status <change-id>")
    dash = report.get("dashboard_url")
    if dash and not no_open and sys.stdout.isatty():
        webbrowser.open(dash)


def _post(payload: dict, no_open: bool, post_fn) -> int:
    try:
        resp = post_fn(payload)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            print(
                "daemon rejected the token — rerun `orch daemon start` or check "
                "~/.agent-orchestration/daemon.json",
                file=sys.stderr,
            )
        else:
            print(
                f"daemon error {exc.code}: {exc.read().decode(errors='replace')}",
                file=sys.stderr,
            )
        return 1
    except (urllib.error.URLError, OSError):
        print("daemon unreachable — run `orch daemon start`", file=sys.stderr)
        return 1
    _print_and_open(resp["report"], no_open)
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    if args.payload is not None:  # raw escape hatch, unchanged semantics
        raw = args.payload
        if raw == "-":
            raw = sys.stdin.read()
        elif Path(raw).is_file():
            raw = Path(raw).read_text()
        payload = json.loads(raw)
        if args.direct:
            from orchestration.launch.change import launch

            print(json.dumps(launch(payload), indent=2, sort_keys=True))
            return 0
        print(json.dumps(client.post_launch(payload), indent=2, sort_keys=True))
        return 0

    if not args.change_id:
        print("a <change-id> (or --payload) is required", file=sys.stderr)
        return 1
    repo = _resolve_repo(args)
    if repo is None:
        return 1

    if args.stub:
        plan_path, script_path = _materialize_stub_files(repo, args.change_id, args.milestones_file)
        payload = payloads.stub_payload(
            repo=repo,
            change_id=args.change_id,
            plan_fixture_path=plan_path,
            stub_script_path=script_path,
        )
    else:
        if not _validate_change(repo, args.change_id):
            return 1
        payload = payloads.production_payload(
            repo=repo, change_id=args.change_id, branch=args.branch, issue=args.issue
        )
    return _post(payload, args.no_open, client.post_launch)


def cmd_resume(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args)
    if repo is None:
        return 1
    return _post({"repo": repo, "change_id": args.change_id}, args.no_open, client.post_resume)


def register_resume(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("resume", help="resume a paused/dead change via the daemon (design §8)")
    p.add_argument("change_id")
    p.add_argument("--repo", help="target repo (default: git toplevel of cwd)")
    p.add_argument("--no-open", action="store_true", help="don't open the dashboard")
    p.set_defaults(func=cmd_resume)


def register_launch_args(p_launch: argparse.ArgumentParser) -> None:
    p_launch.add_argument("change_id", nargs="?", help="spec-lifecycle change id")
    p_launch.add_argument("--repo", help="target repo (default: git toplevel of cwd)")
    p_launch.add_argument("--stub", action="store_true", help="hermetic tier in one flag")
    p_launch.add_argument("--milestones-file", help="stub tier: custom milestones JSON")
    p_launch.add_argument("--issue", type=int, help="GitHub issue number to record")
    p_launch.add_argument("--branch", help="branch override (default change/<change-id>)")
    p_launch.add_argument("--no-open", action="store_true", help="don't open the dashboard")
    p_launch.set_defaults(func=cmd_launch)
