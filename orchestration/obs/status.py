"""The status fold — design §4/§5.2: join pid + events age + worktree mtimes.

State is DERIVED, never stored. The events JSONL is treated as one lagging
signal among three (it flushes in chunks, minutes behind disk — issue #7
sharp edge 1), which is why liveness comes from the pid and worktree mtimes,
never from event freshness alone.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SKIP_DIRS = {".git", ".conductor-tmp", ".venv", "node_modules", "__pycache__"}
_MTIME_SCAN_CAP = 5000


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


def _events_age(tmpdir: Path) -> float | None:
    candidates = sorted(
        tmpdir.glob("checkpoints/**/*.events.jsonl"), key=lambda p: p.stat().st_mtime
    )
    if not candidates:
        return None
    return max(0.0, time.time() - candidates[-1].stat().st_mtime)


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
        return {"state": f"dead: {classified}", "stalled": False, "classified": classified}

    if signals.pid_alive:
        stalled = bool(
            signals.events_age_s is not None
            and signals.worktree_mtime_age_s is not None
            and signals.events_age_s > stall_threshold_s
            and signals.worktree_mtime_age_s > stall_threshold_s
        )
        return {"state": "running", "stalled": stalled, "classified": None}
    if signals.pid_alive is False:
        return {"state": "dead: unreconciled", "stalled": False, "classified": None}
    return {"state": "registered", "stalled": False, "classified": None}
