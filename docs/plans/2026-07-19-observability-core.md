# Observability Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `observability-core` change from `docs/observability-design.md` (issue #7): host-global run registry, exit classifier, health probe, `--web` dashboard wiring, one shared orchestrator daemon (control plane + supervision + index), thin CLI client, daemon container, two conductor-fork patches, and two consumer skills.

**Architecture:** Everything privileged runs host-side inside one containerized daemon that spawns `conductor run --web` children and supervises them; state lives in a JSON-file registry keyed by change, with run state always *derived on read*; boxes/sessions talk to the daemon via a token-gated REST client. See `docs/observability-design.md` + `docs/observability-architecture.png` for the approved design; this plan implements its §9 change 1 only (github-mirror is a separate follow-up plan).

**Tech Stack:** Python 3.12, FastAPI + uvicorn (already present via conductor-cli), stdlib `urllib` client, pytest (hermetic tier), uv, Docker.

**Repo / branch:** `kentra-io/agent-orchestration`, branch `7-observability` (already pushed; design doc + diagram are on it).

---

## Subagent execution model (user-locked)

- **Implementer subagents: Sonnet** (`model: sonnet`). One fresh subagent per task below; the subagent gets the task text verbatim (it contains everything needed).
- **Verifier subagents: Opus** (`model: opus`). After each task, dispatch a verifier that (a) checks the diff against the task spec + `docs/observability-design.md`, (b) re-runs the task's test commands and confirms output, (c) reviews code quality. The verifier must NOT be the implementer (author≠verifier).
- The orchestrating session only dispatches, reviews verdicts, and commits are made by the implementer per task (verifier never edits).
- On a verifier rejection: dispatch a fresh Sonnet implementer with the verifier's findings appended. After 2 failed fix rounds, stop and ask the user.

## Environment contract (every subagent MUST follow)

Work dir: `/Users/jony/code/kentra/harness/agent-orchestration` (in a claudebox; host paths are mirrored).

1. **NEVER run `uv`, `pip`, or pytest against the default `.venv`** — it is a bind-mounted host venv; touching it from the container corrupts it (documented incident). All Python commands use the container-local venv:
   ```bash
   export UV_PROJECT_ENVIRONMENT=/home/agent/venv-agent-orchestration
   export PY=/home/agent/venv-agent-orchestration/bin/python
   export PATH="$HOME/go/bin:$PATH"   # pinned-commit lifecycle (Task 0)
   ```
2. **Test command** (hermetic tier + known-issue deselects, see Task 0):
   ```bash
   $PY -m pytest -q -m "not live" \
     --deselect "tests/test_launch_change.py::TestLaunchSpawnsConductor::test_completes_over_stub_provider_with_relocated_tmpdir" \
     --deselect "tests/test_launch_change.py::TestLaunchWaitFalse::test_wait_false_returns_immediately_then_the_child_completes" \
     --deselect "tests/test_m8_concurrency.py::TestTwoChangesRunConcurrentlyWithoutInterference::test_interleaved_commits_stay_isolated_per_worktree"
   ```
   Call this `RUNTESTS` below. The 3 deselected tests spawn a real conductor child which dies with a `TemplateError: Object of type StrictUndefined is not JSON serializable` **only inside the box** (pre-existing environment quirk, green on CI as of run 29686370031). CI is the arbiter for them — do not "fix" them, do not mask them, do not add new deselects.
3. **Format/lint** (never via `uv run`): `uvx ruff@0.15.20 format . && uvx ruff@0.15.20 check .` — run before every commit.
4. Commit after every task (messages given per task). Push at the end only (Task 14).

## File structure (created by this plan)

```
orchestration/obs/__init__.py          # empty package marker
orchestration/obs/registry.py          # JSON-file run registry (facts only)
orchestration/obs/classify.py          # pure exit classifier (Verdict)
orchestration/obs/status.py            # signals IO + derived state (the fold)
orchestration/daemon/__init__.py       # empty
orchestration/daemon/ports.py          # dashboard port allocator
orchestration/daemon/supervise.py      # child watch + classify + reconcile
orchestration/daemon/app.py            # FastAPI: /runs /launch, index page
orchestration/daemon/__main__.py       # uvicorn entrypoint
orchestration/client.py                # stdlib thin client + local fallback
orchestration/__main__.py              # CLI: runs / status / launch
container/daemon/Dockerfile            # daemon image
Makefile                               # daemon-image / daemon-run targets
skills/orchestration-monitor/SKILL.md  # consumer skill: monitoring runbook
skills/orchestration-launch/SKILL.md   # consumer skill: how to launch
.claude-plugin/plugin.json             # plugin catalog envelope
tests/test_obs_registry.py
tests/test_obs_classify.py
tests/test_obs_status.py
tests/test_launch_probe.py
tests/test_launch_web.py
tests/test_daemon_ports.py
tests/test_daemon_supervise.py
tests/test_daemon_app.py
tests/test_cli_client.py
```
Modified: `orchestration/launch/change.py`, `pyproject.toml`, and (separate repo) the `kentra-io/conductor` fork.

---

### Task 0: Environment bootstrap + baseline

**Files:** none (verification only).

- [ ] **Step 1: Bootstrap the container-local venv and lifecycle**

```bash
cd /Users/jony/code/kentra/harness/agent-orchestration
export UV_PROJECT_ENVIRONMENT=/home/agent/venv-agent-orchestration
uv sync --group dev
go install github.com/kentra-io/spec-lifecycle/cmd/lifecycle@4d1f002755ac
export PATH="$HOME/go/bin:$PATH" PY=/home/agent/venv-agent-orchestration/bin/python
lifecycle --help | head -3   # must NOT be v0.1.0's usage (needs `apply --format`)
```

- [ ] **Step 2: Confirm baseline**

Run: `RUNTESTS` (the full command from the Environment contract).
Expected: **139 passed**, 6 deselected/skipped live, 0 failed. If anything else fails, STOP — the baseline is broken; report instead of proceeding.

- [ ] **Step 3: Confirm branch**

Run: `git status --short --branch | head -2`
Expected: on `7-observability`, clean tree.

---

### Task 1: Run registry (`orchestration/obs/registry.py`)

**Files:**
- Create: `orchestration/obs/__init__.py` (empty), `orchestration/obs/registry.py`
- Test: `tests/test_obs_registry.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Registry = facts only (paths/ids/pids); state is always derived on read."""

import json

from orchestration.obs import registry


def test_write_and_load_entry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    entry = registry.new_entry(
        repo="/Users/jony/code/kentra/kafka-dq",
        change_id="7-observability",
        worktree="/tmp/wt",
        branch="7-observability",
        box="kafka-dq-box",
        tmpdir="/tmp/wt/.conductor-tmp",
        issue=7,
    )
    path = registry.write_entry(entry)
    assert path == tmp_path / "kafka-dq--7-observability.json"
    loaded = registry.load_entry("kafka-dq", "7-observability")
    assert loaded == entry
    assert loaded["incarnations"] == []
    assert loaded["issue"] == 7


def test_append_and_update_incarnation(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    entry = registry.new_entry(
        repo="r", change_id="1-x", worktree="w", branch="b", box=None, tmpdir="t"
    )
    registry.write_entry(entry)
    registry.append_incarnation(
        "r", "1-x", {"pid": 123, "started_at": "2026-07-19T00:00:00+00:00",
                     "web_port": 42001, "exit_code": None, "classified": None}
    )
    registry.update_incarnation("r", "1-x", exit_code=1, classified="oauth-expired")
    loaded = registry.load_entry("r", "1-x")
    assert loaded["incarnations"][-1]["exit_code"] == 1
    assert loaded["incarnations"][-1]["classified"] == "oauth-expired"


def test_load_entries_lists_all(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    for cid in ("1-a", "2-b"):
        registry.write_entry(registry.new_entry(
            repo="r", change_id=cid, worktree="w", branch="b", box=None, tmpdir="t"))
    assert {e["change_id"] for e in registry.load_entries()} == {"1-a", "2-b"}


def test_write_is_atomic_json(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    path = registry.write_entry(registry.new_entry(
        repo="r", change_id="1-a", worktree="w", branch="b", box=None, tmpdir="t"))
    json.loads(path.read_text())  # valid JSON on disk
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Run to verify failure** — `$PY -m pytest tests/test_obs_registry.py -q` → FAIL (`ModuleNotFoundError: orchestration.obs`).

- [ ] **Step 3: Implement**

```python
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
```

(`orchestration/obs/__init__.py` is an empty file.)

- [ ] **Step 4: Run tests** — `$PY -m pytest tests/test_obs_registry.py -q` → 4 passed.
- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.20 format . && uvx ruff@0.15.20 check .
git add orchestration/obs tests/test_obs_registry.py
git commit -m "obs: host-global run registry (facts-only JSON, change-keyed)"
```

---

### Task 2: Exit classifier (`orchestration/obs/classify.py`)

**Files:**
- Create: `orchestration/obs/classify.py`
- Test: `tests/test_obs_classify.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Classifier fixtures use the REAL observed error texts from the two
incidents (harness tasks/orchestration-box-auth-expiry.md and
tasks/orchestration-transient-api-error-kills-run.md)."""

from orchestration.obs.classify import classify


def test_exit_zero_is_success():
    v = classify(0, "", "", None)
    assert v.kind == "success" and v.remedy is None


def test_gate_pause_from_checkpoint_agent():
    v = classify(1, "", "", "human_gate")
    assert v.kind == "gate-pause"


def test_gate_pause_from_eoferror_tail():
    v = classify(1, "", "EOFError: EOF when reading a line", None)
    assert v.kind == "gate-pause"


def test_oauth_expiry():
    v = classify(1, "OAuth session expired and could not be refreshed", "", None)
    assert v.kind == "oauth-expired"
    assert "cb login" in v.remedy


def test_api_transient():
    v = classify(1, "API Error: Connection closed mid-response", "", None)
    assert v.kind == "api-transient"
    assert "resume" in v.remedy


def test_unknown_keeps_detail():
    v = classify(3, "something odd", "boom", None)
    assert v.kind == "unknown"
    assert "something odd" in v.detail and "boom" in v.detail
```

- [ ] **Step 2: Run** — `$PY -m pytest tests/test_obs_classify.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Pure exit classifier — design §5.3, precedence order fixed there.

`gate-pause` is the BY-DESIGN non-zero exit of the crash-then-resume gate
model (orchestration/resume/README.md): the process EOF-crashes at a
human_gate after checkpointing. It must never read as a death.
"""

from __future__ import annotations

from dataclasses import dataclass

GATE_AGENTS = ("human_gate", "milestone_step")


@dataclass(frozen=True)
class Verdict:
    kind: str  # success | gate-pause | oauth-expired | api-transient | unknown
    remedy: str | None
    detail: str


def classify(
    exit_code: int | None,
    stdout_tail: str = "",
    stderr_tail: str = "",
    checkpoint_agent: str | None = None,
) -> Verdict:
    text = f"{stdout_tail}\n{stderr_tail}"
    if exit_code == 0:
        return Verdict("success", None, "")
    if checkpoint_agent in GATE_AGENTS or "EOFError" in text:
        return Verdict(
            "gate-pause",
            "expected pause: resolve via the issue label + `conductor resume`",
            text.strip(),
        )
    if "OAuth" in text and ("expired" in text or "could not be refreshed" in text):
        return Verdict("oauth-expired", "run `cb login` from the worktree, then resume", text.strip())
    if "API Error" in text or "Connection closed" in text or "overloaded" in text.lower():
        return Verdict("api-transient", "transient provider failure: resume the run", text.strip())
    return Verdict("unknown", None, text.strip())
```

- [ ] **Step 4: Run** — 6 passed.
- [ ] **Step 5: Format + commit** — `git add orchestration/obs/classify.py tests/test_obs_classify.py && git commit -m "obs: pure exit classifier (success/gate-pause/oauth/api-transient/unknown)"`

---

### Task 3: Status fold (`orchestration/obs/status.py`)

**Files:**
- Create: `orchestration/obs/status.py`
- Test: `tests/test_obs_status.py`

- [ ] **Step 1: Write the failing tests**

```python
import os
import time

from orchestration.obs import registry
from orchestration.obs.status import Signals, collect, derive_state, tail_file


def _entry(**inc):
    e = registry.new_entry(repo="r", change_id="1-a", worktree="w", branch="b",
                           box=None, tmpdir="t")
    if inc:
        e["incarnations"].append({"pid": 1, "started_at": "x", "web_port": None,
                                  "exit_code": None, "classified": None, **inc})
    return e


def test_no_incarnations_is_registered():
    assert derive_state(_entry(), Signals(None, None, None, None))["state"] == "registered"


def test_running_when_pid_alive():
    s = derive_state(_entry(), Signals(True, None, 10.0, 10.0))
    assert s["state"] == "running" and s["stalled"] is False


def test_running_stalled_when_both_signals_old():
    s = derive_state(_entry(), Signals(True, None, 700.0, 700.0), stall_threshold_s=600)
    assert s["state"] == "running" and s["stalled"] is True


def test_dead_pid_without_exit_is_unreconciled():
    assert derive_state(_entry(), Signals(False, None, None, None))["state"] == "dead: unreconciled"


def test_classified_exits_map_to_states():
    assert derive_state(_entry(exit_code=0, classified="success"),
                        Signals(False, None, None, None))["state"] == "done"
    assert derive_state(_entry(exit_code=1, classified="gate-pause"),
                        Signals(False, None, None, None))["state"] == "paused: gate"
    assert derive_state(_entry(exit_code=1, classified="oauth-expired"),
                        Signals(False, None, None, None))["state"] == "dead: oauth-expired"


def test_tail_file(tmp_path):
    p = tmp_path / "log"
    p.write_bytes(b"x" * 10000 + b"THE END")
    assert tail_file(p, max_bytes=100).endswith("THE END")
    assert tail_file(tmp_path / "missing") == ""


def test_collect_reads_real_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    wt = tmp_path / "wt"
    (wt / ".conductor-tmp" / "checkpoints").mkdir(parents=True)
    (wt / "src").mkdir()
    (wt / "src" / "a.txt").write_text("hi")
    events = wt / ".conductor-tmp" / "checkpoints" / "run.events.jsonl"
    events.write_text("{}\n")
    entry = registry.new_entry(repo="r", change_id="1-a", worktree=str(wt),
                               branch="b", box=None, tmpdir=str(wt / ".conductor-tmp"))
    entry["incarnations"].append({"pid": os.getpid(), "started_at": "x",
                                  "web_port": None, "exit_code": None, "classified": None})
    sig = collect(entry)
    assert sig.pid_alive is True
    assert sig.events_age_s is not None and sig.events_age_s < 60
    assert sig.worktree_mtime_age_s is not None and sig.worktree_mtime_age_s < 60
```

- [ ] **Step 2: Run** — FAIL (module missing).
- [ ] **Step 3: Implement**

```python
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
    if not entry["incarnations"]:
        return {"state": "registered", "stalled": False, "classified": None}
    last = entry["incarnations"][-1]
    classified = last.get("classified")
    if last.get("exit_code") is None and classified is None:
        if signals.pid_alive:
            stalled = bool(
                signals.events_age_s is not None
                and signals.worktree_mtime_age_s is not None
                and signals.events_age_s > stall_threshold_s
                and signals.worktree_mtime_age_s > stall_threshold_s
            )
            return {"state": "running", "stalled": stalled, "classified": None}
        return {"state": "dead: unreconciled", "stalled": False, "classified": None}
    if classified == "success":
        return {"state": "done", "stalled": False, "classified": classified}
    if classified == "gate-pause":
        return {"state": "paused: gate", "stalled": False, "classified": classified}
    return {"state": f"dead: {classified}", "stalled": False, "classified": classified}
```

- [ ] **Step 4: Run** — `$PY -m pytest tests/test_obs_status.py -q` → 7 passed.
- [ ] **Step 5: Format + commit** — `git commit -m "obs: status fold (signals IO + derived state, events treated as lagging)"`

---

### Task 4: Pre-launch health probe (`orchestration/launch/change.py`)

**Files:**
- Modify: `orchestration/launch/change.py` (add function after `start_box`, wire into `launch()` right after the `if box_enabled:` block)
- Test: `tests/test_launch_probe.py`

- [ ] **Step 1: Write the failing tests** (fake `docker` binary on PATH — no real Docker)

```python
import os
import stat

import pytest

from orchestration.launch.change import ChangeLaunchError, health_probe


def _fake_docker(tmp_path, script_body: str) -> str:
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / "docker"
    p.write_text(f"#!/bin/sh\n{script_body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_probe_ok(tmp_path):
    docker = _fake_docker(tmp_path, "echo OK; exit 0")
    report = health_probe("some-box", docker_bin=docker)
    assert report["ok"] is True and report["classified"] == "success"


def test_probe_oauth_expiry_classified(tmp_path):
    docker = _fake_docker(
        tmp_path, "echo 'OAuth session expired and could not be refreshed'; exit 1")
    report = health_probe("some-box", docker_bin=docker)
    assert report["ok"] is False
    assert report["classified"] == "oauth-expired"
    assert "cb login" in report["remedy"]


def test_probe_failure_raises_in_launch_wrapper(tmp_path):
    docker = _fake_docker(tmp_path, "echo 'OAuth session expired'; exit 1")
    with pytest.raises(ChangeLaunchError) as exc:
        health_probe("some-box", docker_bin=docker, raise_on_fail=True)
    assert "oauth-expired" in str(exc.value)
    assert "cb login" in str(exc.value)
```

- [ ] **Step 2: Run** — FAIL (`ImportError: health_probe`).
- [ ] **Step 3: Implement** — add to `change.py` (imports: add `from orchestration.obs.classify import classify` at the top with the other imports):

```python
def health_probe(
    box: str,
    docker_bin: str = "docker",
    timeout: float = 60.0,
    raise_on_fail: bool = False,
) -> dict[str, Any]:
    """`docker exec <box> claude -p OK` before spawning conductor.

    Fails loud-and-early with a classified cause (design §5.1) instead of the
    run dying 3s into the first agent turn with a masked error.
    """
    try:
        proc = subprocess.run(
            [docker_bin, "exec", box, "claude", "-p", "OK"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        verdict = classify(proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:], None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        verdict = classify(1, "", f"probe could not run: {exc}", None)
    report = {
        "ok": verdict.kind == "success",
        "classified": verdict.kind,
        "remedy": verdict.remedy,
        "detail": verdict.detail[-2000:],
    }
    if raise_on_fail and not report["ok"]:
        raise ChangeLaunchError(
            f"box health probe failed [{report['classified']}]: {report['detail'][:300]}"
            + (f" — remedy: {report['remedy']}" if report["remedy"] else "")
        )
    return report
```

Wire into `launch()` — immediately after the `if box_enabled:` block (after `box_report["name"] = start_box(...)`), add:

```python
    if box_enabled and box_report.get("name") and bool(box_cfg.get("health_probe", True)):
        box_report["health_probe"] = health_probe(
            box_report["name"],
            docker_bin=box_cfg.get("docker_bin", "docker"),
            raise_on_fail=True,
        )
```

- [ ] **Step 4: Run** — `$PY -m pytest tests/test_launch_probe.py -q` → 3 passed; then `RUNTESTS` → 142 passed (139 + 3), no new failures. (Existing launch tests run with `box.enabled` false, so the probe is not triggered there.)
- [ ] **Step 5: Format + commit** — `git commit -m "launch: pre-launch box health probe with classified loud failure"`

---

### Task 5: `--web` wiring in the launcher

**Files:**
- Modify: `orchestration/launch/change.py` (`build_conductor_argv`, `launch()`)
- Test: `tests/test_launch_web.py`

- [ ] **Step 1: Write the failing tests**

```python
from orchestration.launch.change import build_conductor_argv


def test_argv_without_web_is_unchanged():
    argv = build_conductor_argv(
        conductor_bin="conductor", workflow="w.yaml", silent=True,
        provider=None, inputs={})
    assert "--web" not in argv and "--web-port" not in argv


def test_argv_with_web_appends_flags():
    argv = build_conductor_argv(
        conductor_bin="conductor", workflow="w.yaml", silent=True,
        provider="stub", inputs={"a": "1"}, web=True, web_port=42001)
    i = argv.index("--web")
    assert argv[i + 1 : i + 3] == ["--web-port", "42001"]
```

- [ ] **Step 2: Run** — FAIL (`TypeError: unexpected keyword argument 'web'`).
- [ ] **Step 3: Implement** — extend `build_conductor_argv` (keep existing params; add two keyword-only params with defaults so all call sites keep working):

```python
def build_conductor_argv(
    *,
    conductor_bin: str,
    workflow: str,
    silent: bool,
    provider: str | None,
    inputs: dict[str, str],
    web: bool = False,
    web_port: int = 0,
) -> list[str]:
    argv = [conductor_bin]
    if silent:
        argv.append("--silent")
    argv += ["run", workflow]
    if provider:
        argv += ["--provider", provider]
    if web:
        argv += ["--web", "--web-port", str(web_port)]
    for key, value in inputs.items():
        argv += ["--input", f"{key}={value}"]
    return argv
```

In `launch()`: thread the config through. Where `argv = build_conductor_argv(...)` is called, add `web=bool(conductor_cfg.get("web", False)), web_port=int(conductor_cfg.get("web_port", 0))`. And right after the `env.update(persistent_checkpoint_env(...))` line, add:

```python
    if bool(conductor_cfg.get("web", False)):
        # bg-mode = auto-shutdown after workflow end + client disconnect; the
        # daemon (not bg_runner) owns the process, so only the env toggle is set.
        env["CONDUCTOR_WEB_BG"] = "1"
```

- [ ] **Step 4: Run** — new tests pass; `RUNTESTS` → no regressions (144 passed).
- [ ] **Step 5: Format + commit** — `git commit -m "launch: opt-in --web/--web-port wiring with CONDUCTOR_WEB_BG"`

---

### Task 6: Registry integration + log legend in `launch()`

**Files:**
- Modify: `orchestration/launch/change.py` (`launch()` signature + body)
- Test: append to `tests/test_launch_web.py`

- [ ] **Step 1: Write the failing test** (uses `dry_run` — no processes spawned; a git repo fixture is needed because `launch()` creates a real worktree. Copy the minimal-repo helper style already used in `tests/test_launch_change.py` — a `git init` + one commit in `tmp_path`):

```python
import subprocess

from orchestration.launch.change import launch
from orchestration.obs import registry


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo, check=True)
    return repo


def test_dry_run_registers_and_reports_legend(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    repo = _git_repo(tmp_path)
    report = launch({
        "repo": str(repo),
        "change_id": "9-test-change",
        "worktree_path": str(tmp_path / "wt"),
        "branch": "9-test-change",
        "conductor": {"workflow": "workflows/milestone.yaml",
                      "plan_fixture_path": "tests/fixtures/plan.json", "dry_run": True},
        "box": {"enabled": False},
        "issue": 9,
    })
    entry = registry.load_entry("repo", "9-test-change")
    assert entry is not None and entry["issue"] == 9
    assert entry["incarnations"] == []  # dry_run spawns nothing
    assert "final JSON result only" in report["log_legend"]["conductor.stdout.log"]
    assert "live progress" in report["log_legend"]["conductor.stderr.log"]
```

Note: if `launch()` requires an existing `plan_fixture_path`, reuse whichever fixture path `tests/test_launch_change.py` already passes for its dry-run test (read that file first and mirror its payload exactly, changing only `change_id`/paths — the assertion targets are the registry entry and the legend, not the plan).

- [ ] **Step 2: Run** — FAIL (`KeyError: 'log_legend'` or no registry entry).
- [ ] **Step 3: Implement** — in `launch()`:

(a) Change signature to `def launch(payload: dict[str, Any], proc_holder: dict[str, Any] | None = None) -> dict[str, Any]:` — the daemon (Task 8) needs the `Popen` handle for real `waitpid` supervision; when `proc_holder` is a dict, set `proc_holder["proc"] = proc` right after `subprocess.Popen(...)`.

(b) After the `tmpdir.mkdir(...)` line, register the run (import `from orchestration.obs import registry as obs_registry` at top):

```python
    entry = obs_registry.new_entry(
        repo=str(repo_path),
        change_id=change_id,
        worktree=str(worktree),
        branch=branch,
        box=box_report.get("name"),
        tmpdir=str(tmpdir),
        issue=payload.get("issue"),
    )
    obs_registry.write_entry(entry)
```

(c) Extend the `report` dict with:

```python
        "registry_path": str(obs_registry.entry_path(entry["repo_slug"], change_id)),
        "log_legend": {
            "conductor.stdout.log": "final JSON result only (empty until the run finishes)",
            "conductor.stderr.log": "live progress UI (Rich panels) — this is the healthy channel",
        },
```

(d) After `proc = subprocess.Popen(...)` (both wait branches share it), append the incarnation:

```python
    web_port = int(conductor_cfg.get("web_port", 0))
    obs_registry.append_incarnation(
        entry["repo_slug"], change_id,
        {
            "pid": proc.pid,
            "started_at": datetime.now(UTC).isoformat(),
            "web_port": web_port or None,
            "dashboard_url": f"http://localhost:{web_port}" if web_port else None,
            "exit_code": None,
            "classified": None,
        },
    )
```

(add `from datetime import UTC, datetime` to imports). In the `wait=True` branch, after `returncode = proc.wait()`, also record the exit: `obs_registry.update_incarnation(entry["repo_slug"], change_id, exit_code=returncode)`.

- [ ] **Step 4: Run** — new test passes; `RUNTESTS` → no regressions. (If existing dry-run launch tests fail because the registry now writes to `~/.agent-orchestration`, that is a real bug in this task: the registry MUST only be written under `ORCHESTRATION_REGISTRY_DIR` when set — it is; existing tests don't set it, so entries land in the real home dir. To keep hermetic tests hermetic, add an autouse fixture in `tests/conftest.py`: `@pytest.fixture(autouse=True)\ndef _isolate_registry(tmp_path, monkeypatch): monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "registry"))` — create `tests/conftest.py` if absent, or append if present.)
- [ ] **Step 5: Format + commit** — `git commit -m "launch: registry entry + incarnation + log legend + proc_holder seam"`

---

### Task 7: Port allocator (`orchestration/daemon/ports.py`)

**Files:**
- Create: `orchestration/daemon/__init__.py` (empty), `orchestration/daemon/ports.py`
- Test: `tests/test_daemon_ports.py`

- [ ] **Step 1: Write the failing tests**

```python
import socket

from orchestration.daemon.ports import PortAllocator, parse_range


def test_parse_range():
    assert parse_range("42000-42050") == (42000, 42050)


def test_allocates_free_port_and_skips_reserved():
    alloc = PortAllocator(42000, 42010)
    p1 = alloc.allocate()
    p2 = alloc.allocate()
    assert p1 != p2 and 42000 <= p1 <= 42010


def test_skips_ports_bound_by_others():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        used = s.getsockname()[1]
        alloc = PortAllocator(used, used + 3)
        assert alloc.allocate() != used
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement**

```python
"""Dashboard port allocation from the container's published range (design §6:
no reverse proxy — conductor binds CONDUCTOR_WEB_HOST inside the container and
the range is published verbatim, so container port == host port)."""

from __future__ import annotations

import socket


def parse_range(spec: str) -> tuple[int, int]:
    lo, _, hi = spec.partition("-")
    return int(lo), int(hi)


class PortAllocator:
    def __init__(self, low: int, high: int) -> None:
        self._low, self._high = low, high
        self._handed_out: set[int] = set()

    def allocate(self) -> int:
        for port in range(self._low, self._high + 1):
            if port in self._handed_out:
                continue
            try:
                with socket.socket() as s:
                    s.bind(("0.0.0.0", port))
            except OSError:
                continue
            self._handed_out.add(port)
            return port
        raise RuntimeError(f"no free dashboard port in {self._low}-{self._high}")
```

- [ ] **Step 4: Run** — 3 passed. **Step 5: Commit** — `git commit -m "daemon: dashboard port allocator over the published range"`

---### Task 8: Supervisor (`orchestration/daemon/supervise.py`)

**Files:**
- Create: `orchestration/daemon/supervise.py`
- Test: `tests/test_daemon_supervise.py`

- [ ] **Step 1: Write the failing tests**

```python
import subprocess
import sys
import time
from pathlib import Path

from orchestration.daemon.supervise import Supervisor
from orchestration.obs import registry


def _register(tmp_path, change_id="1-a", pid=None):
    wt = tmp_path / f"wt-{change_id}"
    tmpdir = wt / ".conductor-tmp"
    tmpdir.mkdir(parents=True)
    entry = registry.new_entry(repo="r", change_id=change_id, worktree=str(wt),
                               branch="b", box=None, tmpdir=str(tmpdir))
    registry.write_entry(entry)
    registry.append_incarnation("r", change_id, {
        "pid": pid, "started_at": "x", "web_port": None,
        "exit_code": None, "classified": None})
    return tmpdir


def test_poll_once_classifies_exited_child(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])
    tmpdir = _register(tmp_path, pid=proc.pid)
    (tmpdir / "conductor.stdout.log").write_text("OAuth session expired")
    (tmpdir / "conductor.stderr.log").write_text("")
    sup = Supervisor()
    sup.adopt("r", "1-a", proc)
    proc.wait()
    events = sup.poll_once()
    assert events and events[0]["classified"] == "oauth-expired"
    loaded = registry.load_entry("r", "1-a")
    assert loaded["incarnations"][-1]["exit_code"] == 1
    assert loaded["incarnations"][-1]["classified"] == "oauth-expired"


def test_poll_once_keeps_running_children(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _register(tmp_path, pid=proc.pid)
        sup = Supervisor()
        sup.adopt("r", "1-a", proc)
        assert sup.poll_once() == []
        assert sup.tracked() == 1
    finally:
        proc.kill()


def test_reconcile_classifies_orphaned_death(tmp_path, monkeypatch):
    """A run that died while the daemon was down: pid gone, exit never seen."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    tmpdir = _register(tmp_path, pid=99999999)  # nonexistent pid
    (tmpdir / "conductor.stdout.log").write_text("API Error: Connection closed mid-response")
    sup = Supervisor()
    events = sup.reconcile()
    assert events and events[0]["classified"] == "api-transient"
    loaded = registry.load_entry("r", "1-a")
    assert loaded["incarnations"][-1]["classified"] == "api-transient"
    assert loaded["incarnations"][-1]["reconciled"] is True
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement**

```python
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
from orchestration.obs.status import pid_alive, tail_file


def _classify_from_entry(entry: dict[str, Any], exit_code: int | None) -> Any:
    tmpdir = Path(entry["tmpdir"])
    return classify(
        exit_code,
        tail_file(tmpdir / "conductor.stdout.log"),
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
                slug, change_id,
                exit_code=exit_code,
                classified=verdict.kind,
                remedy=verdict.remedy,
                finished_at=datetime.now(UTC).isoformat(),
            )
            events.append({"slug": slug, "change_id": change_id,
                           "exit_code": exit_code, "classified": verdict.kind})
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
            if pid_alive(last.get("pid")):
                continue  # still running (e.g. a --direct launch) — leave it
            verdict = _classify_from_entry(entry, None)
            kind = verdict.kind if verdict.kind != "success" else "unknown"
            registry.update_incarnation(
                entry["repo_slug"], entry["change_id"],
                classified=kind,
                remedy=verdict.remedy,
                reconciled=True,
                finished_at=datetime.now(UTC).isoformat(),
            )
            events.append({"slug": entry["repo_slug"], "change_id": entry["change_id"],
                           "exit_code": None, "classified": kind})
        return events
```

Note the reconcile subtlety the tests pin down: `classify(None, ...)` never returns `success` for a vanished pid (exit code unknown ⇒ `exit_code == 0` is false), and reconciled incarnations keep `exit_code: null` + `reconciled: true` so the honest "exit never observed" fact is preserved.

- [ ] **Step 4: Run** — 3 passed; `RUNTESTS` no regressions. **Step 5: Commit** — `git commit -m "daemon: supervisor with parent-poll classification + lazy reconciliation"`

---

### Task 9: Daemon app (`orchestration/daemon/app.py`)

**Files:**
- Create: `orchestration/daemon/app.py`
- Test: `tests/test_daemon_app.py`

- [ ] **Step 1: Write the failing tests** (`fastapi.testclient` — httpx is in the locked env)

```python
from fastapi.testclient import TestClient

import orchestration.daemon.app as app_mod
from orchestration.daemon.app import create_app
from orchestration.daemon.supervise import Supervisor
from orchestration.obs import registry


def _client(token=None):
    return TestClient(create_app(Supervisor(), token=token))


def test_runs_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    assert _client().get("/runs").json() == {"runs": []}


def test_runs_returns_entry_with_derived_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    e = registry.new_entry(repo="r", change_id="1-a", worktree=str(tmp_path),
                           branch="b", box=None, tmpdir=str(tmp_path))
    registry.write_entry(e)
    runs = _client().get("/runs").json()["runs"]
    assert runs[0]["change_id"] == "1-a" and runs[0]["derived"]["state"] == "registered"


def test_launch_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    c = _client(token="sekrit")
    assert c.post("/launch", json={}).status_code == 401
    assert c.post("/launch", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_launch_calls_launcher_and_adopts(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_WEB_PORT_RANGE", "42020-42030")
    seen = {}

    def fake_launch(payload, proc_holder=None):
        seen["payload"] = payload
        return {"pid": 4242, "worktree": "w"}

    monkeypatch.setattr(app_mod, "_launch_fn", fake_launch)
    c = _client(token="sekrit")
    resp = c.post("/launch", json={"repo": "/r", "change_id": "1-a"},
                  headers={"Authorization": "Bearer sekrit"})
    assert resp.status_code == 200 and resp.json()["report"]["pid"] == 4242
    assert seen["payload"]["conductor"]["web"] is True
    assert 42020 <= seen["payload"]["conductor"]["web_port"] <= 42030


def test_resume_is_501():
    assert _client().post("/resume", json={}).status_code == 501


def test_index_serves_html(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    r = _client().get("/")
    assert r.status_code == 200 and "agent-orchestration" in r.text
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement**

```python
"""The daemon app: control plane + index (design §3.1, §5.2).

POST /resume is deliberately 501 in observability-core: resume stays
CLI-direct until the github-mirror change (design §10, resolved).
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from orchestration.daemon.ports import PortAllocator, parse_range
from orchestration.daemon.supervise import Supervisor
from orchestration.launch.change import launch as _launch_fn
from orchestration.obs import registry
from orchestration.obs.status import collect, derive_state

POLL_INTERVAL_S = 2.0


def _folded_runs() -> list[dict[str, Any]]:
    runs = []
    for entry in registry.load_entries():
        try:
            derived = derive_state(entry, collect(entry))
        except OSError:
            derived = {"state": "unknown (fold error)", "stalled": False, "classified": None}
        runs.append({**entry, "derived": derived})
    return runs


def create_app(supervisor: Supervisor, token: str | None = None) -> FastAPI:
    lo, hi = parse_range(os.environ.get("ORCHESTRATION_WEB_PORT_RANGE", "42000-42050"))
    allocator = PortAllocator(lo, hi)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async def loop() -> None:
            while True:
                supervisor.poll_once()
                supervisor.reconcile()
                await asyncio.sleep(POLL_INTERVAL_S)

        task = asyncio.create_task(loop())
        yield
        task.cancel()

    app = FastAPI(title="agent-orchestration daemon", lifespan=lifespan)

    def _check_token(request: Request) -> None:
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="bad or missing bearer token")

    @app.get("/runs")
    async def runs() -> dict[str, Any]:
        return {"runs": _folded_runs()}

    @app.post("/launch")
    async def launch_run(request: Request) -> dict[str, Any]:
        _check_token(request)
        payload = await request.json()
        if not payload.get("repo") or not payload.get("change_id"):
            raise HTTPException(status_code=422, detail="repo and change_id are required")
        conductor_cfg = dict(payload.get("conductor") or {})
        conductor_cfg["web"] = True
        conductor_cfg["web_port"] = allocator.allocate()
        payload["conductor"] = conductor_cfg
        holder: dict[str, Any] = {}
        report = await run_in_threadpool(_launch_fn, payload, holder)
        if holder.get("proc") is not None:
            supervisor.adopt(
                registry.repo_slug(payload["repo"]), payload["change_id"], holder["proc"]
            )
        return {"report": report}

    @app.post("/resume")
    async def resume() -> None:
        raise HTTPException(
            status_code=501,
            detail="resume stays CLI-direct in observability-core (see design §10)",
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        rows = []
        for run in _folded_runs():
            last = run["incarnations"][-1] if run["incarnations"] else {}
            dash = last.get("dashboard_url")
            issue = run.get("issue")
            repo = run.get("repo", "")
            issue_link = (
                f'<a href="https://github.com/kentra-io/{run["repo_slug"]}/issues/{issue}">#{issue}</a>'
                if issue else ""
            )
            rows.append(
                "<tr>"
                f"<td>{html.escape(run['repo_slug'])}</td>"
                f"<td>{html.escape(run['change_id'])}</td>"
                f"<td>{html.escape(run['derived']['state'])}"
                f"{' ⚠ stalled?' if run['derived']['stalled'] else ''}</td>"
                f"<td>{f'<a href={chr(34)}{dash}{chr(34)}>dashboard</a>' if dash else ''}</td>"
                f"<td>{issue_link}</td>"
                "</tr>"
            )
        return (
            "<html><head><title>agent-orchestration</title></head><body>"
            "<h1>agent-orchestration runs</h1>"
            "<table border=1 cellpadding=6><tr><th>project</th><th>change</th>"
            "<th>state</th><th>live</th><th>issue</th></tr>"
            + "".join(rows)
            + "</table></body></html>"
        )

    return app
```

- [ ] **Step 4: Run** — `$PY -m pytest tests/test_daemon_app.py -q` → 6 passed; `RUNTESTS` no regressions.
- [ ] **Step 5: Commit** — `git commit -m "daemon: FastAPI control plane (/runs, /launch, index; resume=501)"`

---

### Task 10: Entrypoint, client, CLI

**Files:**
- Create: `orchestration/daemon/__main__.py`, `orchestration/client.py`, `orchestration/__main__.py`
- Modify: `pyproject.toml` (add `[project.scripts]`)
- Test: `tests/test_cli_client.py`

- [ ] **Step 1: Write the failing tests**

```python
import json

import orchestration.client as client
from orchestration.obs import registry


def test_daemon_url_env(monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_DAEMON_URL", "http://host.docker.internal:8765")
    assert client.daemon_url() == "http://host.docker.internal:8765"


def test_runs_falls_back_to_local_registry(tmp_path, monkeypatch):
    """Daemon down → derive locally from the registry (design §5.2)."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_DAEMON_URL", "http://127.0.0.1:1")  # nothing listens
    registry.write_entry(registry.new_entry(
        repo="r", change_id="1-a", worktree=str(tmp_path), branch="b",
        box=None, tmpdir=str(tmp_path)))
    runs = client.get_runs()
    assert runs[0]["change_id"] == "1-a"
    assert runs[0]["derived"]["state"] == "registered"


def test_status_filters_by_change(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_DAEMON_URL", "http://127.0.0.1:1")
    registry.write_entry(registry.new_entry(
        repo="r", change_id="1-a", worktree=str(tmp_path), branch="b",
        box=None, tmpdir=str(tmp_path)))
    assert client.get_status("1-a")["change_id"] == "1-a"
    assert client.get_status("9-nope") is None
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement**

`orchestration/client.py`:

```python
"""Thin client for the daemon — stdlib only, with a local-registry fallback
so `runs`/`status` still answer when the daemon is down (design §5.2).

In-box sessions reach the daemon via ORCHESTRATION_DAEMON_URL=
http://host.docker.internal:8765 and ORCHESTRATION_DAEMON_TOKEN (env-injected
per the claudebox config.yaml pattern).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from orchestration.obs import registry
from orchestration.obs.status import collect, derive_state


def daemon_url() -> str:
    return os.environ.get("ORCHESTRATION_DAEMON_URL", "http://127.0.0.1:8765")


def _request(method: str, path: str, payload: dict | None = None) -> Any:
    req = urllib.request.Request(daemon_url() + path, method=method)
    token = os.environ.get("ORCHESTRATION_DAEMON_TOKEN")
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
```

`orchestration/__main__.py`:

```python
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
    p_launch.add_argument("--direct", action="store_true",
                          help="bypass the daemon: spawn in-process (reconciled later)")
    args = parser.parse_args(argv)

    if args.cmd == "runs":
        for run in client.get_runs():
            last = run["incarnations"][-1] if run["incarnations"] else {}
            print(f"{run['repo_slug']:20} {run['change_id']:28} "
                  f"{run['derived']['state']:24} {last.get('dashboard_url') or '-'}")
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
```

`orchestration/daemon/__main__.py`:

```python
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
```

`pyproject.toml` — add (top level, after `[project]`'s dependencies block):

```toml
[project.scripts]
orchestration = "orchestration.__main__:main"
```

- [ ] **Step 4: Run** — new tests pass; `RUNTESTS` no regressions; then re-sync so the script lands: `uv sync --group dev` (with `UV_PROJECT_ENVIRONMENT` still exported) and smoke: `$PY -m orchestration runs` prints (possibly empty) table.
- [ ] **Step 5: Commit** — `git commit -m "daemon entrypoint + stdlib thin client + orchestration CLI"`

---

### Task 11: Conductor fork patches + pin bump

**Files (separate repo):** clone of `kentra-io/conductor` in `.claudebox/tmp/conductor-fork/`
- Modify (fork): `conductor/providers/claudebox.py`, `conductor/cli/run.py`
- Modify (this repo): `pyproject.toml` (the `rev` pin)

- [ ] **Step 1: Clone the fork and find the patch branch**

```bash
git clone https://github.com/kentra-io/conductor.git .claudebox/tmp/conductor-fork
cd .claudebox/tmp/conductor-fork
git branch -r --contains 5461008b7d5adf0beae30f9459e4b088c9d4d7f9
```
Expected: one `origin/<patch-branch>` name (the fork's patch branch per ADR-0001). `git checkout <patch-branch>`. If NO branch contains the pin, create one from it: `git checkout -b kentra-patches 5461008b7d5adf0beae30f9459e4b088c9d4d7f9`.

- [ ] **Step 2: Patch A — surface the stdout tail in ProviderError.** In `conductor/providers/claudebox.py`, find the non-zero-exit raise (~line 815). Replace exactly this:

```python
            raise ProviderError(
                f"claude subprocess exited with code {exit_code}: "
                f"{stderr_text.strip() or '(no stderr output)'}",
                is_retryable=_classify_retryable(diag, exit_code or 0),
            )
```

with:

```python
            stdout_tail = " | ".join(
                line.strip() for line in outcome.noise_lines[-5:] if line.strip()
            )
            detail = stderr_text.strip() or stdout_tail or "(no stderr or stdout diagnostics)"
            raise ProviderError(
                f"claude subprocess exited with code {exit_code}: {detail}",
                is_retryable=_classify_retryable(diag, exit_code or 0),
            )
```

- [ ] **Step 3: Patch B — env-overridable dashboard bind host.** In `conductor/cli/run.py` there are exactly TWO `WebDashboard(` construction sites (~lines 1577 and 2205), both with `host="127.0.0.1",`. Change both to:

```python
                host=os.environ.get("CONDUCTOR_WEB_HOST", "127.0.0.1"),
```

(`os` is already imported.) This lets the daemon container set `CONDUCTOR_WEB_HOST=0.0.0.0` so published-port-range dashboards work without a reverse proxy.

- [ ] **Step 4: Run the fork's own tests**

```bash
cd .claudebox/tmp/conductor-fork
UV_PROJECT_ENVIRONMENT=/home/agent/venv-conductor-fork uv sync --all-groups 2>/dev/null || \
  UV_PROJECT_ENVIRONMENT=/home/agent/venv-conductor-fork uv sync
/home/agent/venv-conductor-fork/bin/python -m pytest -q -x -k "claudebox or web" 2>&1 | tail -3
```
Expected: pass (or, if the fork has no test extras that install cleanly, note the exact obstacle in the task report and rely on Step 6 — the module suite is the ADR-0001 corpus).

- [ ] **Step 5: Commit + push the fork**

```bash
git add conductor/providers/claudebox.py conductor/cli/run.py
git commit -m "Surface stdout noise tail in ProviderError + CONDUCTOR_WEB_HOST bind override"
git push origin HEAD
git rev-parse HEAD   # <NEW_SHA>
```
(Push uses the ambient `gh`/git credentials; if push is rejected for permissions, STOP and report — do not force anything.)

- [ ] **Step 6: Pin-bump this repo and validate**

In `agent-orchestration/pyproject.toml`, replace `rev = "5461008b7d5adf0beae30f9459e4b088c9d4d7f9"` with `rev = "<NEW_SHA>"`. Then:

```bash
cd /Users/jony/code/kentra/harness/agent-orchestration
uv lock                        # refresh uv.lock to the new rev FIRST
uv sync --group dev            # UV_PROJECT_ENVIRONMENT still exported
RUNTESTS                       # full module suite = the rebase-gate corpus (ADR-0001)
grep -n "CONDUCTOR_WEB_HOST" /home/agent/venv-agent-orchestration/lib/python3.12/site-packages/conductor/cli/run.py
```
Expected: suite passes unchanged; the grep hits both sites.

- [ ] **Step 7: Commit** — `git add pyproject.toml uv.lock && git commit -m "Bump conductor fork pin: ProviderError stdout tail + CONDUCTOR_WEB_HOST"`

---

### Task 12: Daemon container image

**Files:**
- Create: `container/daemon/Dockerfile`, `Makefile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# agent-orchestration daemon (design §6). Build FROM THE HOST (the image needs
# the real docker socket at runtime; building in-box is pointless).
#   make daemon-image CB_BIN=/path/to/cb
FROM golang:1.26-bookworm AS lifecycle-build
RUN go install github.com/kentra-io/spec-lifecycle/cmd/lifecycle@4d1f002755ac

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# docker CLI (static client only — the daemon talks to the mounted host socket)
RUN curl -fsSL https://download.docker.com/linux/static/stable/aarch64/docker-27.5.1.tgz \
    | tar -xz --strip-components=1 -C /usr/local/bin docker/docker
# gh CLI
RUN curl -fsSL https://github.com/cli/cli/releases/download/v2.86.0/gh_2.86.0_linux_arm64.tar.gz \
    | tar -xz --strip-components=2 -C /usr/local/bin gh_2.86.0_linux_arm64/bin/gh
COPY --from=lifecycle-build /go/bin/lifecycle /usr/local/bin/lifecycle
# cb (claudebox CLI) — private repo, so the binary is provided by the build context
COPY bin/cb /usr/local/bin/cb

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY orchestration ./orchestration
COPY workflows ./workflows
COPY personas ./personas
RUN pip install --no-cache-dir uv==0.11.27 \
    && UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev
ENV PATH="/opt/venv/bin:${PATH}" \
    CONDUCTOR_WEB_HOST=0.0.0.0 \
    ORCHESTRATION_WEB_PORT_RANGE=42000-42050
EXPOSE 8765
ENTRYPOINT ["python", "-m", "orchestration.daemon"]
```

(Note: on an x86 host change the two arch strings `aarch64`/`arm64` to `x86_64`/`amd64`; the Makefile does not auto-detect in v1.)

- [ ] **Step 2: Write the Makefile**

```makefile
CB_BIN ?= $(shell which cb)
CODE_ROOT ?= $(HOME)/code

daemon-image:
	@test -n "$(CB_BIN)" || (echo "cb binary not found; pass CB_BIN=/path/to/cb" && exit 1)
	mkdir -p bin && cp "$(CB_BIN)" bin/cb
	docker build -f container/daemon/Dockerfile -t agent-orchestration-daemon .

daemon-run:
	docker rm -f agent-orchestration-daemon 2>/dev/null || true
	docker run -d --name agent-orchestration-daemon --restart=always \
	  -v /var/run/docker.sock:/var/run/docker.sock \
	  -v $(HOME)/.agent-orchestration:/root/.agent-orchestration \
	  -v $(HOME)/.claude:/root/.claude:ro \
	  -v $(CODE_ROOT):$(CODE_ROOT) \
	  -e KENTRA_BOT_GH_TOKEN -e ORCHESTRATION_DAEMON_TOKEN \
	  -p 8765:8765 -p 42000-42050:42000-42050 \
	  agent-orchestration-daemon
	@echo "daemon: http://localhost:8765"

daemon-logs:
	docker logs -f agent-orchestration-daemon
```

Add `bin/` to `.gitignore` (append a line `bin/`).

- [ ] **Step 3: Validate what's validatable in-box.** `docker build` and `docker run` here require the HOST (socket mount + port publish are stripped in-box). In-box validation is limited to: `make -n daemon-image` renders sensibly, and the Dockerfile parses (`docker build` dry-parse is not available — a careful read by the verifier stands in). Mark the task report clearly: **image build + `make daemon-run` are HOST steps for the user**, listed again in Task 14's handoff.
- [ ] **Step 4: Commit** — `git add container Makefile .gitignore && git commit -m "daemon container image + make targets (host-built)"`

---

### Task 13: Consumer skills + plugin envelope

**Files:**
- Create: `skills/orchestration-monitor/SKILL.md`, `skills/orchestration-launch/SKILL.md`, `.claude-plugin/plugin.json`

- [ ] **Step 1: Write `skills/orchestration-monitor/SKILL.md`**

```markdown
---
name: orchestration-monitor
description: Monitor agent-orchestration runs (launched via the orchestrator daemon). Use when asked "what is the run doing", "is the run stuck/dead", why a conductor/execute-change run failed, or to check run status from any project session.
---

# Monitoring an orchestration run

## The three surfaces

| Question | Surface |
|---|---|
| What is happening RIGHT NOW (turn-by-turn)? | The run's Conductor dashboard — URL from `orchestration runs` (one per live run; dies with the run — that is normal) |
| What state is each run in (all projects)? | `orchestration runs` — or the index page at the daemon URL (default `http://localhost:8765`, from a box: `http://host.docker.internal:8765`) |
| Deep status / why did it die? | `orchestration status <change-id>` — JSON with derived state, classified cause, and remedy |

`orchestration runs`/`status` work even when the daemon is down (they fall
back to reading `~/.agent-orchestration/runs/` directly).

## Reading states

- `running` — healthy. `stalled?` flag = no events AND no worktree writes for
  10+ min; a slow API turn looks the same, so treat as advisory, not a verdict.
- `paused: gate` — the EXPECTED crash-then-resume pause at a human gate.
  Not a death. Resolve via the issue, then resume.
- `dead: oauth-expired` — box OAuth expired. Remedy: `cb login` from the
  worktree, then resume.
- `dead: api-transient` — provider blip killed the run. Remedy: resume.
- `dead: unreconciled` — process gone, exit never observed; the daemon's next
  reconcile pass (or any `orchestration runs` call) classifies it.

## Sharp edges (learned the hard way — issue #7)

- **In-box `docker ps` LIES**: the claudebox socket-proxy filters it (the
  target box and even your own container are omitted). Only HOST `docker ps`
  is authoritative. Never diagnose "box gone" from inside a box.
- **`conductor.stdout.log` is empty during the whole run** — by contract it
  carries only the final JSON result. `conductor.stderr.log` is the live UI.
- **A frozen events JSONL is NOT a stall**: events flush minutes behind.
  Trust the status fold (it joins pid + worktree mtimes), not event freshness.
```

- [ ] **Step 2: Write `skills/orchestration-launch/SKILL.md`**

```markdown
---
name: orchestration-launch
description: Start or resume an agent-orchestration execute-change run from any session (host or claudebox) via the orchestrator daemon. Use when asked to "launch the change", "start the run", "execute the plan", or resume a paused/dead run.
---

# Launching an orchestration run

## Preconditions

- The daemon is up (host: `make daemon-run` in agent-orchestration; check
  `http://localhost:8765`). From a box, reach it at
  `ORCHESTRATION_DAEMON_URL=http://host.docker.internal:8765`.
- `ORCHESTRATION_DAEMON_TOKEN` must be set (env-injected into boxes via the
  claudebox `config.yaml env:` pattern).

## Launch

```bash
orchestration launch '{
  "repo": "/Users/jony/code/kentra/<project>",
  "change_id": "<issue>-<slug>",
  "worktree_path": "/Users/jony/code/kentra/<project>-wt-<slug>",
  "branch": "<issue>-<slug>",
  "issue": <issue-number>,
  "box": {"enabled": true},
  "conductor": {"workflow": "workflows/execute-change.yaml"}
}'
```

The daemon then: runs a box health probe (fails LOUD with a classified cause
— e.g. `oauth-expired → cb login` — instead of dying mid-run), creates the
worktree, provisions the box, assigns a dashboard port, spawns
`conductor run --web`, and registers everything in
`~/.agent-orchestration/runs/`. The response carries the report: pid,
dashboard URL, registry path, log legend.

- `--direct` bypasses a down daemon (in-process spawn; the daemon reconciles
  the run's fate later). `worktree_path` MUST live under the daemon's mounted
  code root.

## Resume

Resume is CLI-direct in this version (`conductor resume` per the module's
crash-then-resume model) — see `orchestration/resume/README.md`. After fixing
the cause (`cb login`, plan edit + approval), resume from the worktree.
```

- [ ] **Step 3: Write `.claude-plugin/plugin.json`** (same envelope shape as `kentra-io/kentra-skills`):

```json
{
  "name": "agent-orchestration-skills",
  "description": "Operator and consumer skills for the agent-orchestration module: monitor live execute-change runs (status fold, classified death causes, dashboard/index surfaces) and launch/resume runs through the orchestrator daemon from any session.",
  "version": "0.1.0",
  "author": { "name": "Kentra" },
  "homepage": "https://github.com/kentra-io/agent-orchestration",
  "repository": "https://github.com/kentra-io/agent-orchestration",
  "license": "MIT",
  "keywords": ["orchestration", "conductor", "claudebox", "observability", "agentic"]
}
```

- [ ] **Step 4: Commit** — `git add skills .claude-plugin && git commit -m "skills: orchestration-monitor + orchestration-launch + plugin envelope"`

(Listing in the `kentra-io/kentra-agentic-plugins` catalog is a one-line PR in that repo — deferred to the user at handoff; noted in Task 14.)

---

### Task 14: Final verification + handoff

**Files:** none.

- [ ] **Step 1: Full gate**

```bash
uvx ruff@0.15.20 format --check . && uvx ruff@0.15.20 check .
RUNTESTS
```
Expected: format clean, checks clean, ~165 passed (139 baseline + ~26 new), 0 failed.

- [ ] **Step 2: Push and confirm CI**

```bash
git push origin 7-observability
gh run watch $(gh run list --branch 7-observability --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```
Expected: CI green (CI runs the FULL suite including the 3 in-box-deselected tests — they must pass there).

- [ ] **Step 3: Report the host-side handoff to the user** (things only they can do):
  1. `make daemon-image CB_BIN=$(which cb)` then `ORCHESTRATION_DAEMON_TOKEN=<generate one> make daemon-run` — on the HOST.
  2. Add `ORCHESTRATION_DAEMON_URL` + `ORCHESTRATION_DAEMON_TOKEN` to the claudebox `config.yaml env:` blocks of consuming projects.
  3. One-line catalog PR in `kentra-io/kentra-agentic-plugins` listing this repo's plugin.
  4. First live smoke: launch a real change via `orchestration launch`, open `http://localhost:8765`, click into the run dashboard.
  5. Open the PR from `7-observability` → `main`.

---

## Out of scope (per design §9 — do NOT build here)

The **github-mirror** change: start/finish/death comments, milestone-tick
checklist comment, `run-died` label, close-on-archive, `/resume` through the
daemon. It gets its own plan once observability-core is merged.
```
