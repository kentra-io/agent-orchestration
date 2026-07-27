"""The status fold — design §4/§5.2: join pid + events age + worktree mtimes.

State is DERIVED, never stored. The events JSONL is treated as one lagging
signal among three (it flushes in chunks, minutes behind disk — issue #7
sharp edge 1), which is why liveness comes from the pid and worktree mtimes,
never from event freshness alone.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SKIP_DIRS = {".git", ".conductor-tmp", ".venv", "node_modules", "__pycache__"}
_MTIME_SCAN_CAP = 5000

# The engine tags a `workflow_completed`/`workflow_failed` event with
# `data.subworkflow_path` (a non-empty list) whenever it is emitted from
# inside a nested subworkflow call frame (e.g. the per-milestone
# `milestone_step` loop); the ROOT workflow's own terminal event carries no
# such key. Verified against a real `*.events.jsonl` (kafka-dq
# 001-e2e-poc): the root event is also the last line the engine ever
# writes, which is why a bounded tail read is enough (issue #14).
_TERMINAL_ROOT_EVENT_TYPES = {"workflow_completed", "workflow_failed"}
_TERMINAL_TAIL_BYTES = 65536


@dataclass(frozen=True)
class Signals:
    pid_alive: bool | None
    checkpoint_agent: str | None
    events_age_s: float | None
    worktree_mtime_age_s: float | None


def pid_alive(pid: int | None) -> bool | None:
    if pid is None:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def tail_file(path: str | Path, max_bytes: int = 4000) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    data = p.read_bytes()
    return data[-max_bytes:].decode("utf-8", errors="replace")


def _newest_mtime_age(root: Path) -> float | None:
    newest, seen = None, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            seen += 1
            if seen > _MTIME_SCAN_CAP:
                break
            try:
                mtime = (Path(dirpath) / name).stat().st_mtime
            except OSError:
                continue
            newest = mtime if newest is None else max(newest, mtime)
        if seen > _MTIME_SCAN_CAP:
            break
    return None if newest is None else max(0.0, time.time() - newest)


def _newest_events_file(tmpdir: Path) -> Path | None:
    candidates = sorted(
        tmpdir.glob("checkpoints/**/*.events.jsonl"), key=lambda p: p.stat().st_mtime
    )
    return candidates[-1] if candidates else None


def _events_age(tmpdir: Path) -> float | None:
    newest = _newest_events_file(tmpdir)
    if newest is None:
        return None
    return max(0.0, time.time() - newest.stat().st_mtime)


def terminal_root_event_type(tmpdir: Path) -> str | None:
    """The ROOT workflow's terminal event type (`workflow_completed` or
    `workflow_failed`), or None — see module docstring for the
    `subworkflow_path` discriminator. Reuses the same events-file glob as
    `_events_age`; no new data source. Public: consumed by
    `orchestration.daemon.supervise` too.
    """
    newest = _newest_events_file(tmpdir)
    if newest is None:
        return None
    tail = tail_file(newest, max_bytes=_TERMINAL_TAIL_BYTES)
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a tail read can start mid-line; skip the fragment
        if event.get("type") in _TERMINAL_ROOT_EVENT_TYPES and not event.get("data", {}).get(
            "subworkflow_path"
        ):
            return event.get("type")
    return None


def _has_terminal_root_event(tmpdir: Path) -> bool:
    """True once the ROOT workflow itself (not a subworkflow) has recorded
    its own terminal event. Thin wrapper over `terminal_root_event_type` —
    preserves the pre-existing bool call-site semantics.
    """
    return terminal_root_event_type(tmpdir) is not None


def agent_message_tail(tmpdir: Path, max_bytes: int = _TERMINAL_TAIL_BYTES) -> str:
    """Recent `agent_message` content from the newest events file — the OAuth
    death text arrives as an agent text block, not on stderr (issue #3).
    """
    newest = _newest_events_file(tmpdir)
    if newest is None:
        return ""
    parts: list[str] = []
    for line in tail_file(newest, max_bytes).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a tail read can start mid-line; skip the fragment
        if isinstance(event, dict) and event.get("type") == "agent_message":
            content = (event.get("data") or {}).get("content", "")
            if content:
                parts.append(content)
    return "\n".join(parts[-5:])


def collect(entry: dict[str, Any]) -> Signals:
    last = entry["incarnations"][-1] if entry["incarnations"] else {}
    return Signals(
        pid_alive=pid_alive(last.get("pid")),
        checkpoint_agent=None,  # gate detection rides the EOFError stderr tail (classify.py)
        events_age_s=_events_age(Path(entry["tmpdir"])),
        worktree_mtime_age_s=_newest_mtime_age(Path(entry["worktree"])),
    )


def derive_state(
    entry: dict[str, Any], signals: Signals, stall_threshold_s: float = 600.0
) -> dict[str, Any]:
    incarnations = entry["incarnations"]
    last = incarnations[-1] if incarnations else {}
    classified = last.get("classified")
    exit_code = last.get("exit_code")

    if classified is not None or exit_code is not None:
        if classified == "success":
            return {"state": "done", "stalled": False, "classified": classified}
        if classified == "gate-pause":
            return {"state": "paused: gate", "stalled": False, "classified": classified}
        if classified == "oauth-expired":
            return {"state": "needs-attention: auth", "stalled": False, "classified": classified}
        return {"state": f"dead: {classified}", "stalled": False, "classified": classified}

    if signals.pid_alive:
        # A root workflow_failed is death NOW, regardless of pid liveness or
        # events freshness — the 007 incident hid behind both for ~1h
        # (harness issue #3, defect B).
        terminal = terminal_root_event_type(Path(entry["tmpdir"]))
        if terminal == "workflow_failed":
            return {
                "state": "dead: workflow-failed (unreconciled)",
                "stalled": False,
                "classified": None,
            }
        stalled = bool(
            signals.events_age_s is not None
            and signals.worktree_mtime_age_s is not None
            and signals.events_age_s > stall_threshold_s
            and signals.worktree_mtime_age_s > stall_threshold_s
        )
        # A finished-but-lingering `--web-bg` process (launch/change.py sets
        # CONDUCTOR_WEB_BG=1) matches the stall signature exactly: the pid
        # is alive and both ages are old, yet the ROOT workflow already
        # reached a terminal event — the `terminal` read above already
        # tells us whether that's the case, no second events-file read
        # needed — issue #14.
        if stalled and terminal is not None:
            return {
                "state": "done: awaiting dashboard disconnect",
                "stalled": False,
                "classified": None,
            }
        return {"state": "running", "stalled": stalled, "classified": None}
    if signals.pid_alive is False:
        return {"state": "dead: unreconciled", "stalled": False, "classified": None}
    return {"state": "registered", "stalled": False, "classified": None}
