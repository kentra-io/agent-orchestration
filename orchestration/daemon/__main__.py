"""`python -m orchestration.daemon` — the containerized entrypoint (design §6)."""

from __future__ import annotations

import argparse
import os

import uvicorn

from orchestration.daemon.app import create_app
from orchestration.daemon.supervise import Supervisor


def main() -> None:
    parser = argparse.ArgumentParser(prog="orchestration-daemon")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    app = create_app(Supervisor(), token=os.environ.get("ORCHESTRATION_DAEMON_TOKEN"))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
