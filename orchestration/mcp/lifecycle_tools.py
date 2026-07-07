"""1:1 wrappers over the six real, shipped `spec-lifecycle` CLI verbs.

`orchestration.md` sec 10 / P8: "`spec-lifecycle` verbs (1:1 wrapper):
`get_state` / `validate_stage` / `record_approval` / `archive_change` /
`run_guard` (+ status). Dropped invented verbs `submit_artifact`/
`request_transition` per the 2026-07-05 reconciliation."

Verified 2026-07-07 against the shipped `spec-lifecycle` v0.1.0 CLI
(`cmd/lifecycle/main.go`) — the real command names/flags this module maps
onto, **exactly**, with no invented verbs added:

| MCP tool name    | Real `lifecycle` command                                          |
|------------------|---------------------------------------------------------------------|
| `get_state`      | `lifecycle status [--change C] --format json`                      |
| `validate_stage` | `lifecycle validate --stage S [--change C] --format json`          |
| `record_approval`| `lifecycle approve C --stage S --approve [--reject] [--design-skip]` |
|                  | `  [--notes ...] [--approved-by ...]`                                |
| `archive_change` | `lifecycle archive C [--force-gates] [--force-conflicts] --format json` |
| `run_guard`      | `lifecycle guard --format json`                                    |

**Consent note (`orchestration.md` sec 7.3 — load-bearing):** `record_approval`
and `archive_change` are the human operator's Mode-A surface, reached only
through an interactive MCP client a human is driving — never wired into a
Conductor-spawned (Mode-B) agent's tool surface. See
`tests/test_consent_invariant.py`.

`lifecycle approve` has **no `--format json`** (verified — it's the one
verb without one); its result is reported the same way as every other
command here for a uniform tool surface: `{"exit_code", "stdout", "stderr",
"json"}`, where `"json"` is `None` whenever `--format json` either wasn't
requested or the output didn't parse as JSON.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


class LifecycleCLIError(RuntimeError):
    """The `lifecycle` binary could not be found or invoked at all (distinct
    from the CLI running and returning a non-zero exit code, which is a
    normal, reportable outcome — see each function's return shape)."""


def _run(
    args: list[str],
    *,
    lifecycle_bin: str = "lifecycle",
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run a `lifecycle` subcommand; return a uniform result dict.

    Never raises for a non-zero exit (that's a normal, reportable CLI
    outcome per `spec-lifecycle`'s own exit-code convention — 0 ok, 1
    refused/findings, 2 could-not-run) — only for the binary itself being
    unresolvable, which is a distinct operational failure.
    """
    try:
        proc = subprocess.run(
            [lifecycle_bin, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise LifecycleCLIError(f"'{lifecycle_bin}' not found on PATH") from exc

    parsed: Any = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = None

    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "json": parsed,
    }


def get_state(
    change: str | None = None,
    *,
    lifecycle_bin: str = "lifecycle",
    cwd: str | None = None,
) -> dict[str, Any]:
    """`lifecycle status [--change <change>] --format json`."""
    args = ["status", "--format", "json"]
    if change:
        args = ["status", "--change", change, "--format", "json"]
    return _run(args, lifecycle_bin=lifecycle_bin, cwd=cwd)


def validate_stage(
    stage: str,
    change: str | None = None,
    *,
    lifecycle_bin: str = "lifecycle",
    cwd: str | None = None,
) -> dict[str, Any]:
    """`lifecycle validate --stage <stage> [--change <change>] --format json`.

    `stage` must be one of `spec-lifecycle`'s real stage names: `refine`,
    `design`, `plan` (verified — this module does not itself validate the
    value, the CLI does, per its own exit-code-2 "could not run" convention).
    """
    args = ["validate", "--stage", stage, "--format", "json"]
    if change:
        args = ["validate", "--stage", stage, "--change", change, "--format", "json"]
    return _run(args, lifecycle_bin=lifecycle_bin, cwd=cwd)


def record_approval(
    change: str,
    stage: str,
    *,
    approved_by: str | None = None,
    notes: str | None = None,
    reject: bool = False,
    design_skip: bool = False,
    lifecycle_bin: str = "lifecycle",
    cwd: str | None = None,
) -> dict[str, Any]:
    """`lifecycle approve <change> --stage <stage> --approve [...]`.

    **This is the human operator's consent act** (sec 7.3) — `--approve` is
    passed unconditionally because this tool is only ever reached through an
    interactive MCP client a human is driving (never a Mode-B agent's tool
    surface — see `tests/test_consent_invariant.py`); `spec-lifecycle`'s own
    `ConsentGate` additionally fails closed on a non-TTY caller with no
    `--approve`, so this is belt-and-suspenders, not the only guard.

    `reject=True` passes `--reject` instead of approving.
    """
    args = ["approve", change, "--stage", stage]
    args += ["--reject"] if reject else ["--approve"]
    if design_skip:
        args.append("--design-skip")
    if approved_by:
        args += ["--approved-by", approved_by]
    if notes:
        args += ["--notes", notes]
    return _run(args, lifecycle_bin=lifecycle_bin, cwd=cwd)


def archive_change(
    change: str,
    *,
    force_gates: bool = False,
    force_conflicts: bool = False,
    lifecycle_bin: str = "lifecycle",
    cwd: str | None = None,
) -> dict[str, Any]:
    """`lifecycle archive <change> [--force-gates] [--force-conflicts] --format json`.

    Same consent note as `record_approval` — this is a Mode-A-only surface.
    """
    args = ["archive", change]
    if force_gates:
        args.append("--force-gates")
    if force_conflicts:
        args.append("--force-conflicts")
    args += ["--format", "json"]
    return _run(args, lifecycle_bin=lifecycle_bin, cwd=cwd)


def run_guard(
    *,
    lifecycle_bin: str = "lifecycle",
    cwd: str | None = None,
) -> dict[str, Any]:
    """`lifecycle guard --format json`."""
    return _run(["guard", "--format", "json"], lifecycle_bin=lifecycle_bin, cwd=cwd)
