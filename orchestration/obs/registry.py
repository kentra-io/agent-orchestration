"""Host-global run registry — one JSON file per change, facts only.

Design: docs/observability-design.md §4. Stored fields are *facts* (paths,
ids, pids, timestamps); run state is never stored — it is derived on read by
`orchestration.obs.status`, so a stale registry cannot lie. Keyed by change,
not process: resumes append to `incarnations`.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def registry_dir() -> Path:
    override = os.environ.get("ORCHESTRATION_REGISTRY_DIR")
    base = Path(override) if override else Path.home() / ".agent-orchestration" / "runs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def repo_slug(repo: str | Path) -> str:
    return Path(repo).name


def entry_path(slug: str, change_id: str) -> Path:
    return registry_dir() / f"{slug}--{change_id}.json"


def new_entry(
    *,
    repo: str | Path,
    change_id: str,
    worktree: str,
    branch: str,
    box: str | None,
    tmpdir: str,
    issue: int | None = None,
) -> dict[str, Any]:
    return {
        "repo_slug": repo_slug(repo),
        "repo": str(repo),
        "change_id": change_id,
        "worktree": worktree,
        "branch": branch,
        "box": box,
        "tmpdir": tmpdir,
        "issue": issue,
        "created_at": datetime.now(UTC).isoformat(),
        "incarnations": [],
    }


def write_entry(entry: dict[str, Any]) -> Path:
    path = entry_path(entry["repo_slug"], entry["change_id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entry, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_entry(slug: str, change_id: str) -> dict[str, Any] | None:
    path = entry_path(slug, change_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_entries() -> list[dict[str, Any]]:
    entries = []
    for path in sorted(registry_dir().glob("*.json")):
        entries.append(json.loads(path.read_text(encoding="utf-8")))
    return entries


def append_incarnation(slug: str, change_id: str, incarnation: dict[str, Any]) -> dict[str, Any]:
    entry = load_entry(slug, change_id)
    if entry is None:
        raise KeyError(f"no registry entry for {slug}--{change_id}")
    entry["incarnations"].append(incarnation)
    write_entry(entry)
    return entry


def update_incarnation(slug: str, change_id: str, **fields: Any) -> dict[str, Any]:
    """Update the LAST incarnation in place (the live/most-recent one)."""
    entry = load_entry(slug, change_id)
    if entry is None or not entry["incarnations"]:
        raise KeyError(f"no incarnation to update for {slug}--{change_id}")
    entry["incarnations"][-1].update(fields)
    write_entry(entry)
    return entry
