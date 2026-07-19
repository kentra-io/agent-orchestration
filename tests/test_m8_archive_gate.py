"""M8 archive-gate DoD: `orchestration.launch.archive_handoff` against the
REAL `lifecycle` binary (M3 tip, on PATH -- see this repo's `CLAUDE.md`) and
a real `openspec/` tree (bootstrapped via `lifecycle init`, the CLI's own
supported bootstrap -- verified below, not the internal Go test package's
shortcuts).

DoD (implementation-plan.md M8): "a completed change archives and folds
only when all tasks are ticked; an incomplete one is refused by the gate."

Getting past the *approval* gates without ever touching the TASKS gate
under test: this module's changes are `type: bug`, which gates on
`repro`/`fix` (`internal/archive/gate.go` -- verified against the shipped
binary, see the module docstring below) -- both approved via the real,
documented, non-interactive path, `lifecycle approve --stage <s> --approve`
(no TTY, no `--force-gates` needed at all). `archive_handoff.py` itself
NEVER passes `--force-gates`/`--force-incomplete-tasks`/`--force-conflicts`
(see its own docstring) -- the tasks-completion gate is exercised for real,
both ways, in `TestTasksGateRefuses`/`TestTasksGatePasses` below.

Verified interactively before writing this file (session transcript, not
reproduced here): `lifecycle init` bootstraps a project without a change;
`lifecycle approve <id> --stage repro|fix --approve --approved-by <name>`
writes a real `approval-state.json` entry with no TTY; `lifecycle archive
<id> --format json` then refuses (exit 1, tasks-completion gate) or
archives (exit 0, folds + relocates to `openspec/changes/archive/<id>/`)
purely based on `tasks.md`'s checkbox state.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from orchestration.launch.archive_handoff import archive

_PROPOSAL = """\
---
issue: "kentra-io/kafka-dq#{n}"
type: bug
---

# Fix probe {n}

## Why
M8 archive-gate DoD fixture.
"""

_TASKS_TEMPLATE = """\
## Milestone 1: do the thing
**Goal** -- do it.
**Deliverables** -- a file.
**Validation contract** -- checkable acceptance criteria, pre-committed:
  - `exit 0` passes.

  ```contract
  check: exit 0
  criteria: trivially true.
  paths:
    - "**"
  ```
**Steps** -- ordered breakdown:
  1. [{box}] Do the thing.
"""


def _bootstrap_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["lifecycle", "init", "--runtimes", "claude-code", "--source-type", "none"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_change(root: Path, change_id: str, *, task_checked: bool) -> Path:
    change_dir = root / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(_PROPOSAL.format(n=change_id), encoding="utf-8")
    (change_dir / "tasks.md").write_text(
        _TASKS_TEMPLATE.format(box="x" if task_checked else " "), encoding="utf-8"
    )
    return change_dir


def _approve_bug_gates(root: Path, change_id: str) -> None:
    """The real, non-interactive, documented approval path -- no `--force-*`
    flag anywhere. `type: bug` changes gate on `repro`+`fix`
    (`internal/approve/types.go`'s `Stages`, `internal/archive/gate.go`'s
    `checkGates`)."""
    for stage in ("repro", "fix"):
        proc = subprocess.run(
            [
                "lifecycle",
                "approve",
                change_id,
                "--stage",
                stage,
                "--approve",
                "--approved-by",
                "m8-test",
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"approving stage {stage!r} failed (exit {proc.returncode}): {proc.stderr}"
        )


class TestTasksGateRefuses:
    def test_unticked_tracked_step_is_refused_and_nothing_is_written(self, tmp_path: Path) -> None:
        root = tmp_path / "proj"
        _bootstrap_project(root)
        change_id = "001-refuse"
        _write_change(root, change_id, task_checked=False)
        _approve_bug_gates(root, change_id)

        report = archive({"worktree": str(root), "change_id": change_id, "dry_run": False})

        assert report["status"] == "refused"
        assert report["exit_code"] == 1
        assert "not checked" in report["reason"]
        assert report["result"] is None

        # Nothing written: the change folder stays exactly where it was,
        # and no archive/ledger appears.
        assert (root / "openspec" / "changes" / change_id).is_dir()
        assert not (root / "openspec" / "changes" / "archive" / change_id).exists()
        assert not (root / "openspec" / "ledger.jsonl").exists()


class TestTasksGatePasses:
    def test_fully_ticked_change_archives_and_folds(self, tmp_path: Path) -> None:
        root = tmp_path / "proj"
        _bootstrap_project(root)
        change_id = "002-archive"
        _write_change(root, change_id, task_checked=True)
        _approve_bug_gates(root, change_id)

        report = archive({"worktree": str(root), "change_id": change_id, "dry_run": False})

        assert report["status"] == "archived"
        assert report["exit_code"] == 0
        assert report["reason"] is None
        assert report["result"]["change"] == change_id
        assert len(report["result"]["records"]) == 1

        # Folded: the change folder relocated, the ledger appended.
        assert not (root / "openspec" / "changes" / change_id).exists()
        assert (root / "openspec" / "changes" / "archive" / change_id).is_dir()
        ledger_lines = (root / "openspec" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(ledger_lines) == 1
        record = json.loads(ledger_lines[0])
        assert record["change"] == change_id


class TestDryRun:
    def test_dry_run_reports_without_touching_openspec(self, tmp_path: Path) -> None:
        root = tmp_path / "proj"
        _bootstrap_project(root)
        change_id = "003-dry-run"
        _write_change(root, change_id, task_checked=False)
        # Deliberately no approval at all -- dry_run must never shell out.

        report = archive({"worktree": str(root), "change_id": change_id, "dry_run": True})

        assert report["status"] == "dry_run"
        assert report["exit_code"] is None
        assert report["would_run"] == ["lifecycle", "archive", change_id, "--format", "json"]
        assert (root / "openspec" / "changes" / change_id).is_dir()
        assert not (root / "openspec" / "ledger.jsonl").exists()
