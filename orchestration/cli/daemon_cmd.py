"""`orch daemon start|stop|status|logs` — docker shell-outs (design §6).

`build_run_argv` mirrors `make daemon-run`'s flags exactly; the one deliberate
difference is the token: the Makefile passes `-e ORCHESTRATION_DAEMON_TOKEN`
(from the caller's shell), the CLI passes it BY VALUE from daemon.json so no
manual export is ever needed. The Makefile stays the local-dev
(build-from-checkout) path.
"""

from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
from pathlib import Path

from orchestration import client
from orchestration.cli import config

CONTAINER = "agent-orchestration-daemon"


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


def docker_available() -> bool:
    try:
        return _run(["docker", "info"]).returncode == 0
    except FileNotFoundError:
        return False


def container_state() -> str | None:
    proc = _run(["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER])
    return proc.stdout.strip() if proc.returncode == 0 else None


def image_present(image: str) -> bool:
    return _run(["docker", "image", "inspect", image]).returncode == 0


def build_run_argv(image: str, token: str, code_root: str, home: str | None = None) -> list[str]:
    home = home or str(Path.home())
    return [
        "docker",
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--restart=always",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{home}/.agent-orchestration:/root/.agent-orchestration",
        "-v",
        f"{home}/.claude:/root/.claude:ro",
        "-v",
        f"{code_root}:{code_root}",
        "-e",
        "KENTRA_BOT_GH_TOKEN",
        "-e",
        f"ORCHESTRATION_DAEMON_TOKEN={token}",
        "-p",
        "8765:8765",
        "-p",
        "42000-42050:42000-42050",
        image,
    ]


def cmd_start(args: argparse.Namespace) -> int:
    if not docker_available():
        print(
            "docker is not reachable — install/start Docker Desktop (or the docker daemon) first",
            file=sys.stderr,
        )
        return 2
    if container_state() == "running":
        print(f"daemon already running: {config.resolve_url()}")
        return 0

    cfg = config.load_config()
    image = args.image or cfg.get("image") or config.DEFAULT_IMAGE
    code_root = args.code_root or cfg.get("code_root") or str(Path.home() / "code")
    token = cfg.get("token") or secrets.token_hex(16)
    cfg.update({"url": cfg.get("url", config.DEFAULT_URL), "token": token, "code_root": code_root})
    if args.image:
        cfg["image"] = args.image
    config.save_config(cfg)

    if not image_present(image):
        pull = _run(["docker", "pull", image])
        if pull.returncode != 0:
            print(pull.stderr.strip(), file=sys.stderr)
            print(
                f"could not pull {image} — the GHCR package is private; "
                "run `docker login ghcr.io` with a token that has read:packages",
                file=sys.stderr,
            )
            return 1

    _run(["docker", "rm", "-f", CONTAINER])  # clear any stopped leftover
    proc = _run(build_run_argv(image, token, code_root))
    if proc.returncode != 0:
        print(f"docker run failed: {proc.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"daemon started: {cfg['url']} (token persisted in {config.config_path()})")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    proc = _run(["docker", "rm", "-f", CONTAINER])
    if proc.returncode != 0:
        print(f"nothing to stop ({proc.stderr.strip()})", file=sys.stderr)
        return 1
    print("daemon stopped")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = container_state()
    print(f"container: {state or 'not present'}")
    if state == "running":
        image = _run(["docker", "inspect", "-f", "{{.Config.Image}}", CONTAINER]).stdout.strip()
        print(f"image:     {image}")
        try:
            print(f"runs:      {len(client.get_runs())} registered ({config.resolve_url()})")
        except OSError as exc:
            print(f"API:       unreachable ({exc})", file=sys.stderr)
            return 1
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    argv = ["docker", "logs"] + (["-f"] if args.follow else []) + [CONTAINER]
    return subprocess.call(argv)  # stream through, not captured


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("daemon", help="manage the containerized daemon (design §6)")
    dsub = p.add_subparsers(dest="daemon_cmd", required=True)
    p_start = dsub.add_parser("start", help="pull + run the daemon container (idempotent)")
    p_start.add_argument("--image", help="image ref override (persisted)")
    p_start.add_argument("--code-root", help="host code root to mount (default ~/code)")
    p_start.set_defaults(func=cmd_start)
    dsub.add_parser("stop", help="remove the daemon container").set_defaults(func=cmd_stop)
    dsub.add_parser("status", help="container + API health").set_defaults(func=cmd_status)
    p_logs = dsub.add_parser("logs", help="docker logs")
    p_logs.add_argument("-f", "--follow", action="store_true")
    p_logs.set_defaults(func=cmd_logs)
