"""The per-change launcher (M8, P10: process-per-change concurrency).

Drives ONE `spec-lifecycle` change through its own git worktree + (optional)
claudebox + `conductor run workflows/execute-change.yaml` — the automation of
the M6 live-box recipe (proven manually 2026-07-09; see
`implementation-plan.md` M8 and `workflows/README.md`): `git worktree add` ->
materialize a minimal `.agent-claude` (`claude_dir_source`) + copy the cast
personas into `<worktree>/.claude/agents/` -> write
`<worktree>/.claudebox/config.yaml` -> `cb run` non-interactively -> resolve
the box via `docker ps --filter label=claudebox.project-path=<worktree>`.

Concurrency (P10) is process-per-change: this module spawns exactly one
`conductor run` child process per invocation, in the change's own worktree,
with `TMPDIR` relocated to a *persistent, worktree-scoped* directory (P4 —
see `orchestration.launch.checkpoint_env`) so two concurrent changes never
share a checkpoint/event-log directory by construction (each worktree is
already isolated by the caller's `worktree_root`/`change_id`, so defaulting
`conductor.tmpdir` to `<worktree>/.conductor-tmp` gets per-change isolation
for free, with no separate registry to keep in sync). `wait: false` spawns
the child and returns immediately (pid only) — that is what makes running
N changes concurrently from N `python -m orchestration.launch.change`
invocations meaningfully concurrent rather than serialized by an
accidentally-blocking launcher.

Calling convention (mirrors `orchestration.harness.*` / `notify_escalation`
— see their docstrings): invocable as a script
(`python -m orchestration.launch.change`, JSON on argv[0] (inline or a file
path) or stdin), importable (`launch(payload) -> dict`, plus the smaller
`create_worktree`/`materialize_box`/`start_box`/`resolve_plan` steps below,
each usable standalone by a test or a different launcher shape), emits one
JSON object to stdout, exit code reflects whether the launcher itself could
run (0 = launched, whatever the eventual `conductor run` exit code turns out
to be when `wait: true`; 2 = a harness-level error — bad input, worktree
creation failed, `lifecycle apply` refused/errored, `cb`/`docker` failed).
There is no "attention" exit code here (unlike the L1/L2/gates checkers):
this module launches a run, it does not itself grade one — a `wait: true`
child that exits non-zero is still a successful *launch*, surfaced via the
`returncode` field, not a process-level failure of this script.

Input JSON:
    {
      "repo": str,                    # required, abs path to the git repo/module root
      "change_id": str,               # required, the spec-lifecycle change id
      "branch": str,                  # optional, default "change/<change_id>"
      "worktree_root": str,           # optional, default "<repo>/.worktrees"
      "box": {
        "enabled": bool,              # default false -- hermetic runs are boxless (--provider stub)
        "start": bool,                # default true -- see the module note below re: tests
        "personas_dir": str,          # optional override, default "<repo>/personas"
        "cb_bin": str,                # default "cb"
        "docker_bin": str,            # default "docker"
        "cb_run_timeout": number      # default 120 (seconds)
      },
      "conductor": {
        "workflow": str,               # required, a workflow YAML path (relative to `repo` or abs)
        "provider": str | null,        # optional --provider override ("stub" for the hermetic tier)
        "inputs": {str: str, ...},     # optional extra --input key=value pairs
        "plan_fixture_path": str,      # optional -- bypass `lifecycle apply`, use this fixture
        "tmpdir": str,                 # optional, default "<worktree>/.conductor-tmp" (P4)
        "env": {str: str, ...},        # optional extra/overriding env vars for the child
        "silent": bool,                # default true -- passes `--silent` to `conductor`
        "conductor_bin": str,          # optional override, default resolved from this venv/PATH
        "lifecycle_bin": str           # optional override, default "lifecycle"
      },
      "wait": bool,                    # default true; false = spawn + return pid now (P10)
      "dry_run": bool                  # default false; true = skip spawning `conductor` entirely
    }

Output JSON: see `launch`'s docstring.

Why `box.start` exists alongside `box.enabled` (a deliberate deviation from
the brief's `{enabled: bool}` shape, logged here since it is a real, if
small, judgment call): `box.enabled=true` always does the (docker-free)
*materialization* half of the recipe -- `.agent-claude/` + persona copy +
`.claudebox/config.yaml` -- which is pure file I/O and fully hermetic. Only
`box.start=true` (the default) goes on to actually run `cb run` + resolve
the box via `docker ps`, which needs a real Docker daemon and a built
claudebox image -- not something an every-PR hermetic test should require.
`tests/test_launch_change.py` exercises `box.enabled=true, box.start=false`
to prove the materialization is byte-correct without Docker, and
`box.enabled=false` separately to prove the whole box step is skipped.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orchestration.launch.checkpoint_env import (
    persistent_checkpoint_env,
    persistent_checkpoint_subprocess_env,
)
from orchestration.resume.plan import (
    PlanReadError,
    load_milestones,
    load_milestones_from_apply,
    write_plan_fixture,
)

EXIT_GOOD = 0
EXIT_ERROR = 2

MODULE_ROOT = Path(__file__).resolve().parents[2]  # .../orchestration/launch/change.py -> repo root
DEFAULT_PERSONAS_DIR = MODULE_ROOT / "personas"
PERSONA_ROLES = ("implementer", "verifier", "orchestrator")


class ChangeLaunchError(ValueError):
    """The launcher's input is malformed, or a launch step failed to run at all."""


# ---------------------------------------------------------------------------
# Step 1: worktree
# ---------------------------------------------------------------------------


def create_worktree(repo: str | Path, worktree_path: str | Path, branch: str) -> Path:
    """`git -C <repo> worktree add <worktree_path> [-b] <branch>`.

    Creates a new branch (`-b branch`) when `branch` does not already exist
    in `repo`; otherwise checks out the existing branch into the new
    worktree (a re-launch of the same change against a branch a prior
    launch already created). Returns the resolved worktree path.
    """
    repo = Path(repo)
    worktree_path = Path(worktree_path)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    exists = (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", branch],
            capture_output=True,
        ).returncode
        == 0
    )
    args = ["git", "-C", str(repo), "worktree", "add", str(worktree_path)]
    args += [branch] if exists else ["-b", branch]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ChangeLaunchError(
            f"`git worktree add` failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return worktree_path.resolve()


# ---------------------------------------------------------------------------
# Step 2: box materialization (docker-free) + start (docker-needed)
# ---------------------------------------------------------------------------


def materialize_box(worktree: str | Path, personas_dir: str | Path | None = None) -> dict[str, Any]:
    """Materialize the M6-recipe box inputs under `worktree` (no docker/cb call).

    - `<worktree>/.agent-claude/` — the `claude_dir_source` (skills/ + plugins/,
      both empty; `settings.json` = `{}`; a role `CLAUDE.md`).
    - `<worktree>/.claude/agents/<role>.md` — the cast personas, copied from
      `personas_dir` (default `<repo>/personas`).
    - `<worktree>/.claudebox/config.yaml` — `provisioning.claude_dir_source`
      pointed at the absolute `.agent-claude` path.

    Returns `{"claude_dir_source": str, "personas": [names], "config_path": str}`.
    """
    worktree = Path(worktree)
    personas_dir = Path(personas_dir) if personas_dir else DEFAULT_PERSONAS_DIR
    if not personas_dir.is_dir():
        raise ChangeLaunchError(
            f"personas_dir does not exist or is not a directory: {personas_dir}"
        )

    agent_claude = worktree / ".agent-claude"
    (agent_claude / "skills").mkdir(parents=True, exist_ok=True)
    (agent_claude / "plugins").mkdir(parents=True, exist_ok=True)
    (agent_claude / "settings.json").write_text("{}\n", encoding="utf-8")
    (agent_claude / "CLAUDE.md").write_text(
        "# Agent box\n\nMaterialized by orchestration.launch.change -- no host "
        "~/.claude bind; only credentials are injected separately.\n",
        encoding="utf-8",
    )

    agents_dir = worktree / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    personas: list[str] = []
    for role in PERSONA_ROLES:
        src = personas_dir / f"{role}.md"
        if not src.is_file():
            continue
        shutil.copyfile(src, agents_dir / f"{role}.md")
        personas.append(role)
    if not personas:
        raise ChangeLaunchError(f"no persona files ({PERSONA_ROLES}) found under {personas_dir}")

    claudebox_dir = worktree / ".claudebox"
    claudebox_dir.mkdir(parents=True, exist_ok=True)
    config_path = claudebox_dir / "config.yaml"
    config_path.write_text(
        f"provisioning:\n  claude_dir_source: {agent_claude.resolve()}\n",
        encoding="utf-8",
    )

    return {
        "claude_dir_source": str(agent_claude.resolve()),
        "personas": personas,
        "config_path": str(config_path.resolve()),
    }


def start_box(
    worktree: str | Path,
    *,
    cb_bin: str = "cb",
    docker_bin: str = "docker",
    timeout: float = 120.0,
) -> str:
    """`( cd <worktree> && cb run </dev/null )`, then resolve the box name.

    Non-interactive: stdin is `/dev/null` (`subprocess.DEVNULL`), matching
    the proven M6 live-box recipe. Resolves the box via
    `docker ps --filter "label=claudebox.project-path=<worktree>" --format
    '{{.Names}}'` (the same lookup `cb`'s own docs describe for locating a
    project's running box). Raises `ChangeLaunchError` if `cb run` fails or
    no matching box is found.
    """
    worktree = Path(worktree).resolve()
    proc = subprocess.run(
        [cb_bin, "run"],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise ChangeLaunchError(f"`cb run` failed (exit {proc.returncode}): {proc.stderr.strip()}")

    ps = subprocess.run(
        [
            docker_bin,
            "ps",
            "--filter",
            f"label=claudebox.project-path={worktree}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if ps.returncode != 0:
        raise ChangeLaunchError(f"`docker ps` failed (exit {ps.returncode}): {ps.stderr.strip()}")
    names = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not names:
        raise ChangeLaunchError(f"no running box found for project-path={worktree}")
    return names[0]


# ---------------------------------------------------------------------------
# Step 3: plan resolution (reuses orchestration.resume.plan -- M7)
# ---------------------------------------------------------------------------


def resolve_plan(
    worktree: str | Path,
    change_id: str,
    *,
    plan_fixture_path: str | Path | None = None,
    dest_dir: str | Path,
    lifecycle_bin: str = "lifecycle",
) -> Path:
    """Return a `plan_fixture_path` for `execute-change.yaml`'s `read_plan` step.

    If `plan_fixture_path` is given, it is used as-is (already the right
    `{"milestones": [...]}` shape -- see `orchestration.resume.plan.
    load_milestones`) -- this is the hermetic-tier / test escape hatch.
    Otherwise shells out to the real production surface,
    `lifecycle apply <change_id> --format json`, from `worktree` (per
    `orchestration.resume.plan.load_milestones_from_apply`'s docstring:
    exit 0 ok, 1 refused -- tasks.md fails plan-stage validation, 2 could
    not run), and writes a fresh fixture file under `dest_dir` in the exact
    shape `execute-change.yaml` reads.
    """
    if plan_fixture_path:
        path = Path(plan_fixture_path)
        load_milestones(path)  # validates the shape early, fails loud here not deep in conductor
        return path

    try:
        milestones = load_milestones_from_apply(
            change_id, cwd=worktree, lifecycle_bin=lifecycle_bin
        )
    except PlanReadError as exc:
        raise ChangeLaunchError(str(exc)) from exc
    return write_plan_fixture(Path(dest_dir) / "plan.json", milestones)


# ---------------------------------------------------------------------------
# Step 4: spawn `conductor run`
# ---------------------------------------------------------------------------


def _conductor_bin(override: str | None) -> str:
    if override:
        return override
    venv_candidate = Path(sys.executable).parent / "conductor"
    return str(venv_candidate) if venv_candidate.is_file() else "conductor"


def build_conductor_argv(
    *,
    conductor_bin: str,
    workflow: str,
    silent: bool,
    provider: str | None,
    inputs: dict[str, str],
) -> list[str]:
    argv = [conductor_bin]
    if silent:
        argv.append("--silent")
    argv += ["run", workflow]
    if provider:
        argv += ["--provider", provider]
    for key, value in inputs.items():
        argv += ["--input", f"{key}={value}"]
    return argv


def _find_events_path(tmp_dir: Path, timeout: float = 2.0) -> str | None:
    """Best-effort: poll briefly for the run's `*.events.jsonl` to appear."""
    deadline = time.monotonic() + timeout
    while True:
        matches = sorted(tmp_dir.rglob("*.events.jsonl"))
        if matches:
            return str(matches[0])
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


def launch(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the full M8 launch recipe for one change; return the report JSON.

    See the module docstring for the input/output shapes and the exit-code
    convention `main` applies on top of this.
    """
    repo = payload.get("repo")
    change_id = payload.get("change_id")
    if not repo or not isinstance(repo, str):
        raise ChangeLaunchError("'repo' (non-empty string, abs path) is required")
    if not change_id or not isinstance(change_id, str):
        raise ChangeLaunchError("'change_id' (non-empty string) is required")
    repo_path = Path(repo)

    conductor_cfg = payload.get("conductor") or {}
    workflow = conductor_cfg.get("workflow")
    if not workflow or not isinstance(workflow, str):
        raise ChangeLaunchError(
            "'conductor.workflow' (non-empty string, path to a workflow YAML) is required"
        )
    workflow_path = Path(workflow)
    if not workflow_path.is_absolute():
        workflow_path = repo_path / workflow_path

    branch = payload.get("branch") or f"change/{change_id}"
    worktree_root = Path(payload.get("worktree_root") or (repo_path / ".worktrees"))
    worktree_path = worktree_root / change_id

    dry_run = bool(payload.get("dry_run", False))
    wait = bool(payload.get("wait", True))

    worktree = create_worktree(repo_path, worktree_path, branch)

    box_cfg = payload.get("box") or {}
    box_enabled = bool(box_cfg.get("enabled", False))
    box_report: dict[str, Any] = {"enabled": box_enabled, "name": None}
    if box_enabled:
        materialized = materialize_box(worktree, box_cfg.get("personas_dir"))
        box_report.update(materialized)
        if bool(box_cfg.get("start", True)):
            box_report["name"] = start_box(
                worktree,
                cb_bin=box_cfg.get("cb_bin", "cb"),
                docker_bin=box_cfg.get("docker_bin", "docker"),
                timeout=float(box_cfg.get("cb_run_timeout", 120.0)),
            )

    tmpdir = Path(conductor_cfg.get("tmpdir") or (worktree / ".conductor-tmp"))
    tmpdir.mkdir(parents=True, exist_ok=True)

    plan_fixture_path = resolve_plan(
        worktree,
        change_id,
        plan_fixture_path=conductor_cfg.get("plan_fixture_path"),
        dest_dir=tmpdir,
        lifecycle_bin=conductor_cfg.get("lifecycle_bin", "lifecycle"),
    )

    inputs = dict(conductor_cfg.get("inputs") or {})
    inputs.setdefault("plan_fixture_path", str(plan_fixture_path))
    if box_enabled:
        inputs.setdefault("worktree", str(worktree))
        if box_report.get("name"):
            inputs.setdefault("box", box_report["name"])

    argv = build_conductor_argv(
        conductor_bin=_conductor_bin(conductor_cfg.get("conductor_bin")),
        workflow=str(workflow_path),
        silent=bool(conductor_cfg.get("silent", True)),
        provider=conductor_cfg.get("provider"),
        inputs=inputs,
    )

    env = persistent_checkpoint_subprocess_env(tmpdir / "checkpoints")
    venv_bin = Path(sys.executable).parent
    env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    env.update(conductor_cfg.get("env") or {})
    # The P4 checkpoint relocation is re-applied LAST so it always wins: a
    # caller-provided env that happens to carry the parent's TMPDIR (e.g. an
    # os.environ.copy() on macOS, where TMPDIR is always set) must never
    # silently collapse two concurrent changes onto one shared checkpoint/
    # event dir (review finding 2026-07-09; P4/ADR-0002).
    env.update(persistent_checkpoint_env(tmpdir / "checkpoints"))

    report: dict[str, Any] = {
        "worktree": str(worktree),
        "branch": branch,
        "box": box_report,
        "tmpdir": str(tmpdir),
        "plan_fixture_path": str(plan_fixture_path),
        "conductor_argv": argv,
        "wait": wait,
        "dry_run": dry_run,
        "pid": None,
        "returncode": None,
        "events_path": None,
        "stdout_path": None,
        "stderr_path": None,
    }

    if dry_run:
        return report

    stdout_path = tmpdir / "conductor.stdout.log"
    stderr_path = tmpdir / "conductor.stderr.log"
    report["stdout_path"] = str(stdout_path)
    report["stderr_path"] = str(stderr_path)

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

    if wait:
        returncode = proc.wait()
        report["returncode"] = returncode
        report["pid"] = proc.pid
        report["events_path"] = _find_events_path(tmpdir)
    else:
        report["pid"] = proc.pid
        report["events_path"] = _find_events_path(tmpdir, timeout=0.5)

    return report


def _read_input(argv: Sequence[str]) -> dict[str, Any]:
    if not argv or argv[0] == "-":
        raw = sys.stdin.read()
        source = "stdin"
    else:
        candidate = argv[0]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            path = Path(candidate)
            if not path.is_file():
                raise ChangeLaunchError(
                    f"argv[0] is neither valid inline JSON nor an existing file path: {candidate!r}"
                ) from None
            raw = path.read_text()
            source = str(path)
        else:
            if not isinstance(data, dict):
                raise ChangeLaunchError(
                    "input JSON from argv[0] (inline JSON) must be an object, "
                    f"got {type(data).__name__}"
                )
            return data

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ChangeLaunchError(f"invalid JSON from {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ChangeLaunchError(
            f"input JSON from {source} must be an object, got {type(data).__name__}"
        )
    return data


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        report = launch(_read_input(argv))
    except ChangeLaunchError as exc:
        _emit({"error": str(exc)})
        return EXIT_ERROR
    _emit(report)
    return EXIT_GOOD


if __name__ == "__main__":
    raise SystemExit(main())
