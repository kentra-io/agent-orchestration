"""`orch auth mint|status` — long-lived Claude token custody (harness issue #3).

Custody chain: macOS Keychain (service kentra-orch-claude-token) -> read
host-side by `orch daemon start` -> `-e CLAUDE_CODE_LONG_LIVED_TOKEN` into the
daemon container -> mapped to CLAUDE_CODE_OAUTH_TOKEN only at each `claude`
invocation. The token is a ~1-year non-rotating subscription credential
(`claude setup-token`); no refresh -> no rotation race with interactive
sessions. Env var CLAUDE_CODE_LONG_LIVED_TOKEN overrides the keychain
(non-macOS hosts / `make daemon-run`).
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from datetime import UTC, datetime

from orchestration.cli import config

SERVICE = "kentra-orch-claude-token"
TOKEN_PREFIX = "sk-ant-oat"
STALE_AFTER_DAYS = 330  # ~11 months of a 1-year token


def read_token() -> str | None:
    env = os.environ.get("CLAUDE_CODE_LONG_LIVED_TOKEN")
    if env:
        return env
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None  # non-macOS host: env override is the only source
    return (proc.stdout.strip() or None) if proc.returncode == 0 else None


def store_token(token: str) -> None:
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", "orch", "-w", token],
        capture_output=True,
        text=True,
        check=True,
    )


def warn_if_stale() -> str | None:
    minted = config.load_config().get("token_minted_at")
    if not minted:
        return None
    try:
        age_days = (datetime.now(UTC) - datetime.fromisoformat(minted)).days
    except (ValueError, TypeError):
        # ValueError: malformed timestamp. TypeError: a naive (no-tz)
        # `token_minted_at` (e.g. hand-written on a non-macOS host) can't be
        # subtracted from the aware `datetime.now(UTC)` — treat it the same
        # as "can't tell the age", not a crash.
        return None
    if age_days >= STALE_AFTER_DAYS:
        return (
            f"long-lived Claude token is {age_days} days old (setup-tokens last ~1 year) "
            "— re-mint soon: `orch auth mint`"
        )
    return None


def _verify(token: str) -> tuple[bool, str]:
    """Live-verify the token with `claude -p OK`. Returns (ok, detail) — detail
    is the stderr tail on failure (empty on success), so the caller can
    distinguish a bad token from a network/outage failure."""
    proc = subprocess.run(
        ["claude", "-p", "OK"],
        env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or "")[-200:].strip()


def cmd_mint(args: argparse.Namespace) -> int:
    print(
        "Opening the `claude setup-token` browser flow — approve access, then "
        "copy the printed token."
    )
    try:
        subprocess.run(["claude", "setup-token"], check=False)  # interactive passthrough
        token = getpass.getpass("Paste the token (sk-ant-oat…): ").strip()
        if not token.startswith(TOKEN_PREFIX):
            print(
                f"that doesn't look like a setup-token (expected {TOKEN_PREFIX}…)",
                file=sys.stderr,
            )
            return 1
        print("Verifying against the API …")
        ok, detail = _verify(token)
        if not ok:
            suffix = f" ({detail})" if detail else ""
            print(f"token failed a live `claude -p OK` — not stored{suffix}", file=sys.stderr)
            return 1
        store_token(token)
    except FileNotFoundError as exc:
        print(
            f"`claude` binary not found ({exc.filename or 'claude'}) — is Claude Code "
            "installed and on PATH?",
            file=sys.stderr,
        )
        return 1
    except subprocess.TimeoutExpired:
        print(
            "verifying the token timed out (`claude -p OK` hung) — check your network "
            "and try again",
            file=sys.stderr,
        )
        return 1
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        suffix = f": {stderr}" if stderr else ""
        print(f"failed to store the token in the keychain (locked?){suffix}", file=sys.stderr)
        return 1

    cfg = config.load_config()
    cfg["token_minted_at"] = datetime.now(UTC).isoformat()
    config.save_config(cfg)
    print(f"Stored in keychain ({SERVICE}) and stamped token_minted_at.")
    print("Restart the daemon to pick it up: `orch daemon stop && orch daemon start`")
    print("(a restart kills in-flight runs — `orch runs` first; paused/dead runs resume after)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    token = read_token()
    if token is None:
        print("no long-lived token (keychain item absent, env unset) — run `orch auth mint`")
        return 1
    minted = config.load_config().get("token_minted_at") or "unknown"
    print(f"token: present ({token[:14]}…)  minted_at: {minted}")
    stale = warn_if_stale()
    if stale:
        print(f"warning: {stale}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("auth", help="long-lived Claude token custody (mint/status)")
    asub = p.add_subparsers(dest="auth_cmd", required=True)
    mint = asub.add_parser("mint", help="run `claude setup-token`, verify, store in keychain")
    mint.set_defaults(func=cmd_mint)
    asub.add_parser("status", help="token presence + age").set_defaults(func=cmd_status)
