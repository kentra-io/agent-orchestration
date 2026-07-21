"""Thin client for the daemon — stdlib only, with a local-registry fallback
so `runs`/`status` still answer when the daemon is down (design §5.2).

In-box sessions reach the daemon via ORCHESTRATION_DAEMON_URL=
http://host.docker.internal:8765 and ORCHESTRATION_DAEMON_TOKEN (env-injected
per the claudebox config.yaml pattern).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from orchestration.cli import config as cli_config
from orchestration.obs import registry
from orchestration.obs.status import collect, derive_state


def daemon_url() -> str:
    return cli_config.resolve_url()


def _request(method: str, path: str, payload: dict | None = None) -> Any:
    req = urllib.request.Request(daemon_url() + path, method=method)
    token = cli_config.resolve_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(payload).encode()
    with urllib.request.urlopen(req, data=data, timeout=30) as resp:
        return json.loads(resp.read())


def _local_runs() -> list[dict[str, Any]]:
    runs = []
    for entry in registry.load_entries():
        try:
            derived = derive_state(entry, collect(entry))
        except OSError:
            derived = {"state": "unknown (fold error)", "stalled": False, "classified": None}
        runs.append({**entry, "derived": derived})
    return runs


def get_runs() -> list[dict[str, Any]]:
    try:
        return _request("GET", "/runs")["runs"]
    except (urllib.error.URLError, OSError, TimeoutError):
        return _local_runs()


def get_status(change_id: str) -> dict[str, Any] | None:
    for run in get_runs():
        if run["change_id"] == change_id:
            return run
    return None


def post_launch(payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/launch", payload)


def post_resume(payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/resume", payload)
