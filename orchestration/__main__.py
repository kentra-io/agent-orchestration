"""CLI: `orchestration runs|status|launch` (or `python -m orchestration`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from orchestration import client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("runs", help="list all runs (daemon, falls back to local registry)")
    p_status = sub.add_parser("status", help="one change's folded status")
    p_status.add_argument("change_id")
    p_launch = sub.add_parser("launch", help="launch via the daemon")
    p_launch.add_argument("payload", help="JSON string, file path, or - for stdin")
    p_launch.add_argument(
        "--direct",
        action="store_true",
        help="bypass the daemon: spawn in-process (reconciled later)",
    )
    args = parser.parse_args(argv)

    if args.cmd == "runs":
        for run in client.get_runs():
            last = run["incarnations"][-1] if run["incarnations"] else {}
            print(
                f"{run['repo_slug']:20} {run['change_id']:28} "
                f"{run['derived']['state']:24} {last.get('dashboard_url') or '-'}"
            )
        return 0
    if args.cmd == "status":
        run = client.get_status(args.change_id)
        if run is None:
            print(f"no run registered for change {args.change_id}", file=sys.stderr)
            return 1
        print(json.dumps(run, indent=2, sort_keys=True))
        return 0
    # launch
    raw = args.payload
    if raw == "-":
        raw = sys.stdin.read()
    elif Path(raw).is_file():
        raw = Path(raw).read_text()
    payload = json.loads(raw)
    if args.direct:
        from orchestration.launch.change import launch

        print(json.dumps(launch(payload), indent=2, sort_keys=True))
    else:
        print(json.dumps(client.post_launch(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
