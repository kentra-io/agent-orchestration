"""`orch` — the module CLI (docs/cli-design.md §3-§4). Argparse, stdlib only.

Exit codes (§10): 0 ok · 1 user-fixable · 2 environment broken.
"""

from __future__ import annotations

import argparse
import json
import sys

from orchestration import client
from orchestration.cli import daemon_cmd, launch_cmd, validate_cmd

EXIT_OK, EXIT_USER, EXIT_ENV = 0, 1, 2


def cmd_runs(args: argparse.Namespace) -> int:
    for run in client.get_runs():
        last = run["incarnations"][-1] if run["incarnations"] else {}
        print(
            f"{run['repo_slug']:20} {run['change_id']:28} "
            f"{run['derived']['state']:24} {last.get('dashboard_url') or '-'}"
        )
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    run = client.get_status(args.change_id)
    if run is None:
        print(f"no run registered for change {args.change_id}", file=sys.stderr)
        return EXIT_USER
    print(json.dumps(run, indent=2, sort_keys=True))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orch", description="agent-orchestration CLI (docs/cli-design.md)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_runs = sub.add_parser("runs", help="list all runs (daemon, falls back to local registry)")
    p_runs.set_defaults(func=cmd_runs)

    p_status = sub.add_parser("status", help="one change's folded status")
    p_status.add_argument("change_id")
    p_status.set_defaults(func=cmd_status)

    p_launch = sub.add_parser("launch", help="launch a change via the daemon (design §7)")
    p_launch.add_argument(
        "--payload", help="raw payload escape hatch: JSON string, file path, or - for stdin"
    )
    p_launch.add_argument(
        "--direct",
        action="store_true",
        help="with --payload: bypass the daemon, spawn in-process",
    )
    launch_cmd.register_launch_args(p_launch)
    launch_cmd.register_resume(sub)

    validate_cmd.register(sub)

    daemon_cmd.register(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
