"""Daemon-side resume: re-derive remaining work, respawn conductor (design §8).

The decision rule reuses M7's semantics without needing a stored plan-hash
baseline: re-derive the current plan's remaining milestones (by id, against
the checkpoint's completed set); if that list equals the checkpoint's own
baked-in tail, the plan is materially unchanged → `conductor resume --from
<checkpoint> --skip-gates` (cheap, in place). Anything else → a fresh
`conductor run` over just the remaining list (the checkpoint's baked-in
read_plan output would be stale — see orchestration/resume/README.md).

Plan source: `lifecycle apply` from the worktree first (the production
surface; the daemon image ships the binary), falling back to the
checkpoint's own plan_fixture_path (the hermetic tier, whose fixture file
IS its plan surface). The report records which one was used.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conductor.engine.checkpoint import CheckpointManager

from orchestration.launch.change import (
    MODULE_ROOT,
    _conductor_bin,
    build_conductor_argv,
    health_probe,
)
from orchestration.launch.checkpoint_env import (
    persistent_checkpoint_env,
    persistent_checkpoint_subprocess_env,
)
from orchestration.obs import registry
from orchestration.resume.checkpoint import load_execute_change_checkpoint
from orchestration.resume.plan import (
    PlanReadError,
    derive_remaining_milestones,
    load_milestones,
    load_milestones_from_apply,
    write_plan_fixture,
)


class ResumeError(ValueError):
    """Nothing resumable, or the resume recipe could not run."""


def preflight_box_auth(
    box: str,
    worktree: str,
    *,
    cb_bin: str = "cb",
    docker_bin: str = "docker",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Probe the box's `claude` auth BEFORE respawning conductor; heal once.

    A box's injected OAuth credentials are time-boxed, and a resume typically
    happens hours after launch (the 2026-07-15 incident: a ~19h human-gate gap
    outlived the token, and the dead credentials surfaced only as
    `claude subprocess exited with code 1` three seconds into the respawned
    run — harness `tasks/orchestration-box-auth-expiry.md`). So: run the same
    `health_probe` the launcher uses; on failure attempt ONE non-interactive
    `cb login` from the worktree (`cb` resolves the target container from
    cwd; stdin is /dev/null so an interactive OAuth fallback can never hang
    the daemon), then re-probe. Returns the final probe report (callers
    raise on `ok: False` with the classified cause + remedy).
    """
    report = health_probe(box, docker_bin=docker_bin)
    if report["ok"]:
        return report

    login: dict[str, Any] = {"attempted": True, "ok": False, "detail": ""}
    try:
        proc = subprocess.run(
            [cb_bin, "login"],
            cwd=worktree,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        login["ok"] = proc.returncode == 0
        login["detail"] = (proc.stderr.strip() or proc.stdout.strip())[-500:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        login["detail"] = f"`cb login` could not run: {exc}"

    report = health_probe(box, docker_bin=docker_bin)
    report["login"] = login
    return report


def find_latest_checkpoint_in(tmpdir: str | Path) -> Path | None:
    """Launch relocated TMPDIR to <tmpdir>/checkpoints, and CheckpointManager
    writes to $TMPDIR/conductor/checkpoints — so the run's checkpoints live at
    <tmpdir>/checkpoints/conductor/checkpoints/execute-change-*.json."""
    ckpt_dir = Path(tmpdir) / "checkpoints" / "conductor" / "checkpoints"
    files = list(ckpt_dir.glob("execute-change-*.json"))
    if not files:
        return None
    # Newest by created_at (matches CheckpointManager.list_checkpoints —
    # filename timestamps are only second-granular).
    return max(files, key=lambda p: CheckpointManager.load_checkpoint(p).created_at)


def current_milestones(
    worktree: str, change_id: str, fixture_path: str
) -> tuple[list[dict[str, Any]], str]:
    """The change's CURRENT plan: production surface first, fixture fallback."""
    try:
        return load_milestones_from_apply(change_id, cwd=worktree), "lifecycle-apply"
    except PlanReadError:
        return load_milestones(fixture_path), "fixture"


def resume(
    entry: dict[str, Any], *, web_port: int, proc_holder: dict[str, Any] | None = None
) -> dict[str, Any]:
    tmpdir = Path(entry["tmpdir"])
    worktree = entry["worktree"]
    change_id = entry["change_id"]

    if entry.get("box"):
        probe = preflight_box_auth(entry["box"], worktree)
        if not probe["ok"]:
            detail = str(probe.get("detail", ""))[:300]
            remedy = probe.get("remedy") or "run `cb login` from the worktree, then resume"
            raise ResumeError(
                f"box auth/health pre-flight failed [{probe.get('classified')}]: "
                f"{detail} — remedy: {remedy}"
            )

    ckpt_path = find_latest_checkpoint_in(tmpdir)
    if ckpt_path is None:
        raise ResumeError(
            f"no checkpoint found under {tmpdir}/checkpoints — nothing to resume "
            "(the run may never have started a milestone)"
        )
    ckpt = load_execute_change_checkpoint(ckpt_path)
    milestones, plan_source = current_milestones(worktree, change_id, ckpt.plan_fixture_path)
    remaining = derive_remaining_milestones(milestones, ckpt.completed_milestone_ids)
    if not remaining:
        raise ResumeError("nothing left to resume — every milestone is already completed")

    workflow = str(MODULE_ROOT / "workflows" / "execute-change.yaml")
    conductor_bin = _conductor_bin(None)
    provider = entry.get("provider")

    if remaining == ckpt.milestones[ckpt.cursor_index :]:
        mode = "resume-in-place"
        argv = [
            conductor_bin,
            "--silent",
            "resume",
            workflow,
            "--from",
            str(ckpt_path),
            "--skip-gates",
            "--web",
            "--web-port",
            str(web_port),
        ]
        if provider:
            argv += ["--provider", provider]
    else:
        mode = "fresh-run-remaining"
        fixture = write_plan_fixture(tmpdir / "plan-resume.json", remaining)
        inputs = {"plan_fixture_path": str(fixture)}
        if entry.get("box"):
            inputs["box"] = entry["box"]
            inputs["worktree"] = worktree
        argv = build_conductor_argv(
            conductor_bin=conductor_bin,
            workflow=workflow,
            silent=True,
            provider=provider,
            inputs=inputs,
            web=True,
            web_port=web_port,
        )

    env = persistent_checkpoint_subprocess_env(tmpdir / "checkpoints")
    venv_bin = Path(sys.executable).parent
    env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    env.update(entry.get("conductor_env") or {})
    env.update(persistent_checkpoint_env(tmpdir / "checkpoints"))  # always wins (P4)
    env["CONDUCTOR_WEB_BG"] = "1"

    # Same fixed log names launch uses — supervise._classify_from_entry reads
    # them; truncation is correct (they are THIS incarnation's logs now).
    stdout_path = tmpdir / "conductor.stdout.log"
    stderr_path = tmpdir / "conductor.stderr.log"
    with (
        open(stdout_path, "w", encoding="utf-8") as out,
        open(stderr_path, "w", encoding="utf-8") as err,
    ):
        proc = subprocess.Popen(
            argv,
            cwd=worktree,
            env=env,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            text=True,
        )
    if proc_holder is not None:
        proc_holder["proc"] = proc

    registry.append_incarnation(
        entry["repo_slug"],
        change_id,
        {
            "pid": proc.pid,
            "started_at": datetime.now(UTC).isoformat(),
            "web_port": web_port,
            "dashboard_url": f"http://localhost:{web_port}",
            "exit_code": None,
            "classified": None,
            "resumed": mode,
        },
    )

    return {
        "change_id": change_id,
        "mode": mode,
        "plan_source": plan_source,
        "remaining_milestones": [m["id"] for m in remaining],
        "completed_milestone_ids": ckpt.completed_milestone_ids,
        "pid": proc.pid,
        "dashboard_url": f"http://localhost:{web_port}",
        "checkpoint": str(ckpt_path),
        "conductor_argv": argv,
        "registry_path": str(registry.entry_path(entry["repo_slug"], change_id)),
    }
