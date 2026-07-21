"""`~/.agent-orchestration/daemon.json` read/write + credential precedence (design §5).

Precedence: env ORCHESTRATION_DAEMON_URL / _TOKEN (boxes keep their
config.yaml env-injection pattern) > daemon.json > defaults.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8765"
DEFAULT_IMAGE = "ghcr.io/kentra-io/agent-orchestration-daemon:latest"


def config_path() -> Path:
    override = os.environ.get("ORCHESTRATION_CONFIG_PATH")
    return Path(override) if override else Path.home() / ".agent-orchestration" / "daemon.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(cfg: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 600 before it lands at the real path
    os.replace(tmp, path)
    return path


def resolve_url() -> str:
    return os.environ.get("ORCHESTRATION_DAEMON_URL") or load_config().get("url") or DEFAULT_URL


def resolve_token() -> str | None:
    return os.environ.get("ORCHESTRATION_DAEMON_TOKEN") or load_config().get("token")
