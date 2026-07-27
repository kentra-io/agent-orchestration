"""Child supervision + lazy reconciliation (design §3.1).

The daemon is the conductor children's PARENT, so `Popen.poll()` gives real
exit codes. Reconciliation is the permanent second path: it classifies runs
whose exit was never observed (daemon restart, --direct launches) from the
pid + log tails alone, so a restarted daemon converges on the truth.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestration.obs import registry
from orchestration.obs.classify import classify
from orchestration.obs.status import (
    agent_message_tail,
    pid_alive,
    tail_file,
    terminal_root_event_type,
)


def _incarnation_cutoff(entry: dict[str, Any]) -> float | None:
    """Epoch cutoff for `agent_message_tail`'s `since_ts` — the LAST (i.e.
    current) incarnation's `started_at`. Resume appends events into the same
    relocated events.jsonl as the prior incarnation (module docstring in
    orchestration/obs/status.py), so classifying the current incarnation
    must not read agent_message text emitted before it started. Returns None
    (old, unscoped behavior) when there is no incarnation or `started_at` is
    absent/unparseable."""
    incarnations = entry.get("incarnations") or []
    if not incarnations:
        return None
    started_at = incarnations[-1].get("started_at")
    if not started_at:
        return None
    try:
        return datetime.fromisoformat(started_at).timestamp()
    except (TypeError, ValueError):
        return None


def _classify_from_entry(entry: dict[str, Any], exit_code: int | None) -> Any:
    tmpdir = Path(entry["tmpdir"])
    stdout_tail = tail_file(tmpdir / "conductor.stdout.log")
    events_tail = agent_message_tail(tmpdir, since_ts=_incarnation_cutoff(entry))
    if events_tail:
        stdout_tail = f"{stdout_tail}\n{events_tail}"
    return classify(
        exit_code,
        stdout_tail,
        tail_file(tmpdir / "conductor.stderr.log"),
        None,
    )


class Supervisor:
    def __init__(self) -> None:
        self._procs: dict[tuple[str, str], subprocess.Popen] = {}

    def adopt(self, slug: str, change_id: str, proc: subprocess.Popen) -> None:
        self._procs[(slug, change_id)] = proc

    def tracked(self) -> int:
        return len(self._procs)

    def poll_once(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for key in list(self._procs):
            proc = self._procs[key]
            exit_code = proc.poll()
            if exit_code is None:
                continue
            slug, change_id = key
            entry = registry.load_entry(slug, change_id)
            verdict = _classify_from_entry(entry, exit_code)
            registry.update_incarnation(
                slug,
                change_id,
                exit_code=exit_code,
                classified=verdict.kind,
                remedy=verdict.remedy,
                finished_at=datetime.now(UTC).isoformat(),
            )
            events.append(
                {
                    "slug": slug,
                    "change_id": change_id,
                    "exit_code": exit_code,
                    "classified": verdict.kind,
                    "remedy": verdict.remedy,
                    "detail": verdict.detail,
                }
            )
            del self._procs[key]
        return events

    def reconcile(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for entry in registry.load_entries():
            if not entry["incarnations"]:
                continue
            last = entry["incarnations"][-1]
            if last.get("exit_code") is not None or last.get("classified"):
                continue
            if (entry["repo_slug"], entry["change_id"]) in self._procs:
                continue  # actively tracked — poll_once owns it
            if (
                pid_alive(last.get("pid"))
                and terminal_root_event_type(Path(entry["tmpdir"])) != "workflow_failed"
            ):
                continue  # still running — leave it
            verdict = _classify_from_entry(entry, None)
            kind = verdict.kind if verdict.kind != "success" else "unknown"
            registry.update_incarnation(
                entry["repo_slug"],
                entry["change_id"],
                classified=kind,
                remedy=verdict.remedy,
                reconciled=True,
                finished_at=datetime.now(UTC).isoformat(),
            )
            events.append(
                {
                    "slug": entry["repo_slug"],
                    "change_id": entry["change_id"],
                    "exit_code": None,
                    "classified": kind,
                    "remedy": verdict.remedy,
                    "detail": verdict.detail,
                }
            )
        return events
