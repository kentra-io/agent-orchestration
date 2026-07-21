"""The per-change launcher (M8, P10: process-per-change concurrency).

Drives ONE `spec-lifecycle` change through its own git worktree + (optional)
claudebox + `conductor run workflows/execute-change.yaml` — the automation of
the M6 live-box recipe (proven manually 2026-07-09; see
`implementation-plan.md` M8 and `workflows/README.md`): `git worktree add` ->
materialize a minimal `.agent-claude` (`claude_dir_source`) + copy the cast
personas into `<worktree>/.claude/agents/` -> write
`<worktree>/.claudebox/config.yaml` -> `cb run --detach` (ensure/provision the
box and exit 0 without an interactive attach) -> resolve the box via
`docker ps --filter label=claudebox.project-path=<worktree>`.

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
        "workflow": str,               # optional, default = module's workflows/execute-change.yaml
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
`box.start=true` (the default) goes on to actually run `cb run --detach` +
resolve the box via `docker ps`, which needs a real Docker daemon and a built
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestration.launch.checkpoint_env import (
    persistent_checkpoint_env,
    persistent_checkpoint_subprocess_env,
)
from orchestration.obs import registry as obs_registry
from orchestration.obs.classify import classify
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


def _registered_worktrees(repo: Path) -> dict[str, str | None]:
    """Map each registered worktree's real path -> its branch ref (or None if
    detached), parsed from `git worktree list --porcelain`."""
    out = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    entries: dict[str, str | None] = {}
    current: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = os.path.realpath(line[len("worktree ") :])
            entries[current] = None
        elif line.startswith("branch ") and current is not None:
            entries[current] = line[len("branch ") :]
    return entries


def create_worktree(repo: str | Path, worktree_path: str | Path, branch: str) -> Path:
    """`git -C <repo> worktree add <worktree_path> [-b] <branch>`.

    Creates a new branch (`-b branch`) when `branch` does not already exist
    in `repo`; otherwise checks out the existing branch into the new
    worktree (a re-launch of the same change against a branch a prior
    launch already created). Returns the resolved worktree path.

    A re-launch of the same change derives the *same* worktree path and
    branch (both from `change_id`), so an already-registered worktree at
    `worktree_path` for `branch` is an idempotent no-op — its path is
    returned unchanged. A worktree registered there for a *different* branch
    is a conflict and raises.
    """
    repo = Path(repo)
    worktree_path = Path(worktree_path)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    resolved = os.path.realpath(worktree_path)
    registered = _registered_worktrees(repo)
    if resolved in registered:
        existing_branch = registered[resolved]
        if existing_branch == f"refs/heads/{branch}":
            return Path(resolved)
        raise ChangeLaunchError(
            f"worktree {resolved} already exists on branch "
            f"{existing_branch or '(detached)'}, not the expected {branch}; "
            f"remove it with `git -C {repo} worktree remove {resolved}` and retry"
        )

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
    """`( cd <worktree> && cb run --detach )`, then resolve the box name.

    Non-interactive: `cb run --detach` ensures/creates/provisions the box and
    exits 0 after printing its name, WITHOUT attaching an interactive Claude
    session (stdin stays `subprocess.DEVNULL`). This replaces the earlier
    bare-`cb run` recipe, which — with a non-terminal stdin — unconditionally
    tried `docker exec -it ... claude` and exited 1 *after the box was already
    up*, so the launcher's strict `returncode != 0` check aborted a healthy
    launch. With `--detach` a nonzero exit now means a genuine
    ensure/provision failure. Resolves the box via
    `docker ps --filter "label=claudebox.project-path=<worktree>" --format
    '{{.Names}}'` (the same lookup `cb`'s own docs describe for locating a
    project's running box). Raises `ChangeLaunchError` if `cb run` fails or
    no matching box is found.

    Requires a `cb` build that understands `--detach`; older binaries reject
    the flag loudly (exit 1 + usage), which surfaces as a clear
    `ChangeLaunchError` — no version sniffing needed.
    """
    worktree = Path(worktree).resolve()
    # Resolve `cb` on PATH up front: a bare name that is only a shell
    # alias/function (or a PATH that differs from the interactive shell) would
    # otherwise surface as an opaque FileNotFoundError from execvp. An absolute
    # path is used as-is so callers can point `box.cb_bin` at the real binary.
    cb_path = cb_bin if os.path.isabs(cb_bin) else shutil.which(cb_bin)
    if not cb_path:
        raise ChangeLaunchError(
            f"`{cb_bin}` not found on PATH; if it is a shell alias/function set "
            f"`box.cb_bin` to the absolute path of the claudebox binary"
        )
    proc = subprocess.run(
        [cb_path, "run", "--detach"],
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


def launch(payload: dict[str, Any], proc_holder: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the full M8 launch recipe for one change; return the report JSON.

    See the module docstring for the input/output shapes and the exit-code
    convention `main` applies on top of this.

    `proc_holder`: optional out-param seam for a caller (e.g. a background
    daemon) that needs the live `subprocess.Popen` handle -- if given, the
    conductor child process is stashed at `proc_holder["proc"]` the moment
    it is spawned (dry runs never populate it).

    Registry (`orchestration.obs.registry`): every launch (including
    `dry_run`) writes a facts-only registry entry keyed by `repo_slug` +
    `change_id`; a spawned conductor child additionally appends an
    incarnation (pid/web_port/dashboard_url) and, once a synchronous
    (`wait: true`) launch's child exits, records its `exit_code`.
    """
    repo = payload.get("repo")
    change_id = payload.get("change_id")
    if not repo or not isinstance(repo, str):
        raise ChangeLaunchError("'repo' (non-empty string, abs path) is required")
    if not change_id or not isinstance(change_id, str):
        raise ChangeLaunchError("'change_id' (non-empty string) is required")
    repo_path = Path(repo)

    conductor_cfg = payload.get("conductor") or {}
    workflow = conductor_cfg.get("workflow") or str(
        MODULE_ROOT / "workflows" / "execute-change.yaml"
    )
    if not isinstance(workflow, str):
        raise ChangeLaunchError("'conductor.workflow' must be a string path to a workflow YAML")
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
    if box_enabled and box_report.get("name") and bool(box_cfg.get("health_probe", True)):
        box_report["health_probe"] = health_probe(
            box_report["name"],
            docker_bin=box_cfg.get("docker_bin", "docker"),
            raise_on_fail=True,
        )

    tmpdir = Path(conductor_cfg.get("tmpdir") or (worktree / ".conductor-tmp"))
    tmpdir.mkdir(parents=True, exist_ok=True)

    registry_entry = obs_registry.new_entry(
        repo=str(repo_path),
        change_id=change_id,
        worktree=str(worktree),
        branch=branch,
        box=box_report.get("name"),
        tmpdir=str(tmpdir),
        issue=payload.get("issue"),
        provider=conductor_cfg.get("provider"),
        conductor_env=conductor_cfg.get("env") or {},
    )
    obs_registry.write_entry(registry_entry)

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
        web=bool(conductor_cfg.get("web", False)),
        web_port=int(conductor_cfg.get("web_port", 0)),
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
    if bool(conductor_cfg.get("web", False)):
        # bg-mode = auto-shutdown after workflow end + client disconnect; the
        # daemon (not bg_runner) owns the process, so only the env toggle is set.
        env["CONDUCTOR_WEB_BG"] = "1"

    web_port = int(conductor_cfg.get("web_port", 0))

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
        "registry_path": str(obs_registry.entry_path(registry_entry["repo_slug"], change_id)),
        "dashboard_url": f"http://localhost:{web_port}" if web_port else None,
        "log_legend": {
            "conductor.stdout.log": "final JSON result only (empty until the run finishes)",
            "conductor.stderr.log": "live progress UI (Rich panels) — this is the healthy channel",
        },
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
        if proc_holder is not None:
            proc_holder["proc"] = proc

    obs_registry.append_incarnation(
        registry_entry["repo_slug"],
        change_id,
        {
            "pid": proc.pid,
            "started_at": datetime.now(UTC).isoformat(),
            "web_port": web_port or None,
            "dashboard_url": f"http://localhost:{web_port}" if web_port else None,
            "exit_code": None,
            "classified": None,
        },
    )

    if wait:
        returncode = proc.wait()
        report["returncode"] = returncode
        report["pid"] = proc.pid
        report["events_path"] = _find_events_path(tmpdir)
        obs_registry.update_incarnation(
            registry_entry["repo_slug"], change_id, exit_code=returncode
        )
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
