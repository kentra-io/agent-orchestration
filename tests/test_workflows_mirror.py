"""M3 (github-mirror): the workflow-side push + tick mirror legs, wired into
`workflows/milestone.yaml` and `workflows/execute-change.yaml`, proven
end-to-end against Conductor's `stub` provider -- NO box, NO LLM, NO network,
NO `gh`/GitHub token.

Maps to the workflow-level github-mirror scenarios:
  - "A committed milestone is pushed to the run branch" / "Hermetic tier makes
    no push" -> the stub-tier run exercises push + tick in dry_run mode.
  - "A push failure does not halt the run" / "Issue unreachable, push still
    lands" / "No GitHub at all, run completes locally" -> a real-git run whose
    `origin` does not exist reports push failure yet still runs to its normal
    terminal state, with `tick` executed regardless.

The design constraint under test is D2's report-only routing: `commit` routes
to `push` only on exit 0, but `push` routes to `tick` and `tick` routes to
`$end` UNCONDITIONALLY -- so neither mirror leg can structurally fail the
milestone (a script step's exit code is not self-enforcing in Conductor;
routing is what turns it into a failure).

Reuses the hermetic Stub-provider harness from `test_workflows_ladder.py`
(`_base_env`, `_read_events`, the workflow paths, `CONDUCTOR_BIN`).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from stub_provider import write_stub_script
from test_workflows_ladder import (
    CONDUCTOR_BIN,
    EXECUTE_CHANGE_WORKFLOW,
    VENV_BIN,
    _agent_names,
    _base_env,
    _parse_output_json,
    _read_events,
)

_PASS_IMPLEMENTER = {"diff_summary": "did it", "halt": "none"}
_PASS_VERIFIER = {"pass": True, "notes": "good", "score": 1.0, "violations": "none"}


def _write_plan(tmp_path: Path, ids: list[int]) -> Path:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps({"milestones": [{"id": i, "title": f"work for M{i}"} for i in ids]}),
        encoding="utf-8",
    )
    return plan_path


def _run_execute_change(
    tmp_path: Path,
    inputs: dict[str, str],
    *,
    env_overrides: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], list[dict[str, Any]]]:
    """Run `execute-change.yaml` over the stub provider; return (result, output, events).

    Makes no assertion about the exit code -- callers assert it themselves
    (every mirror scenario here expects the run to REACH its terminal state,
    i.e. returncode 0, even when a mirror leg fails).
    """
    script_path = write_stub_script(
        tmp_path / "script",
        {
            "implementer": [{"content": _PASS_IMPLEMENTER}],
            "verifier": [{"content": _PASS_VERIFIER}],
        },
    )
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    env = _base_env(tmp_dir, script_path)
    if env_overrides:
        env.update(env_overrides)

    args = [
        str(CONDUCTOR_BIN),
        "--silent",
        "run",
        str(EXECUTE_CHANGE_WORKFLOW),
        "--provider",
        "stub",
    ]
    for k, v in inputs.items():
        args += ["--input", f"{k}={v}"]

    result = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    events = _read_events(tmp_dir)
    output = _parse_output_json(result.stdout) if result.returncode == 0 else {}
    return result, output, events


def _script_outputs(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """Parsed JSON stdout of every `script_completed` for step `name`."""
    outs: list[dict[str, Any]] = []
    for e in events:
        if e["type"] == "script_completed" and e["data"].get("agent_name") == name:
            outs.append(json.loads(e["data"]["stdout"]))
    return outs


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


# ---------------------------------------------------------------------------
# (a) Hermetic stub tier: push + tick run per milestone, dry_run, no network.
# ---------------------------------------------------------------------------


class TestStubTierExercisesMirrorDryRun:
    def test_push_and_tick_run_dry_run_per_milestone_and_run_completes(
        self, tmp_path: Path
    ) -> None:
        plan = _write_plan(tmp_path, [1, 2])

        result, output, events = _run_execute_change(
            tmp_path,
            inputs={
                "plan_fixture_path": str(plan),
                "gates_l1_command": "exit 0",
                "change_id": "015-mirror",
            },
        )

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        # The run reached its normal terminal state (archive dry-run default).
        assert output["milestones_processed"] == 2
        assert output["status"] == "dry_run"
        assert "workflow_completed" in {e["type"] for e in events}

        started = _agent_names(events, "agent_started")
        assert started.count("push") == 2
        assert started.count("tick") == 2

        push_outs = _script_outputs(events, "push")
        assert len(push_outs) == 2
        assert all(o["status"] == "dry_run" for o in push_outs)
        # Dry-run reports the push it WOULD make, contacts nothing, pushes nothing.
        assert all(o["pushed"] is False for o in push_outs)
        assert all(o["would_run"] and o["would_run"][0] == "git" for o in push_outs)

        tick_outs = _script_outputs(events, "tick")
        assert len(tick_outs) == 2
        assert all(o["status"] == "dry_run" for o in tick_outs)
        # No `gh` call was made (dry-run reports would_run, mirrored=False).
        assert all(o["mirrored"] is False for o in tick_outs)
        # The checklist body the tick WOULD post carries the marker + branch.
        assert all("agent-orchestration:mirror" in o["body"] for o in tick_outs)


# ---------------------------------------------------------------------------
# (b) A push failure does not halt the run (real git, nonexistent origin).
# ---------------------------------------------------------------------------


class TestPushFailureDoesNotHaltTheRun:
    def _live_repo(self, root: Path, origin: Path) -> Path:
        root.mkdir()
        _git(root, "init", "-q", "-b", "main")
        (root / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
        # An origin that does NOT exist -- every push against it fails.
        _git(root, "remote", "add", "origin", str(origin))
        return root

    def test_live_push_fails_but_every_milestone_commits_and_tick_still_runs(
        self, tmp_path: Path
    ) -> None:
        repo = self._live_repo(tmp_path / "repo", tmp_path / "does-not-exist.git")
        # A real pending change so the first milestone makes a genuine local commit.
        (repo / "work.txt").write_text("verified milestone work\n", encoding="utf-8")

        plan = _write_plan(tmp_path, [1, 2])

        result, output, events = _run_execute_change(
            tmp_path,
            inputs={
                "plan_fixture_path": str(plan),
                "gates_l1_command": "exit 0",
                "change_id": "015-mirror",
                "worktree": str(repo),
                "branch": "change/live-mirror",
                "commit_dry_run": "false",
                "push_dry_run": "false",
                # notify_dry_run stays default true -> tick renders but makes
                # no gh call (this leg proves push independence, not the tick
                # write path -- see the routing test below for tick failure).
            },
            env_overrides={
                "GIT_CONFIG_GLOBAL": str(tmp_path / "no-global-gitconfig"),
                "GIT_CONFIG_SYSTEM": str(tmp_path / "no-system-gitconfig"),
            },
        )

        # The run REACHES its normal terminal state despite every push failing.
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert output["milestones_processed"] == 2
        assert output["status"] == "dry_run"  # archive leg default dry-run
        assert "workflow_completed" in {e["type"] for e in events}
        assert "workflow_failed" not in {e["type"] for e in events}

        # Every push was attempted and reported failure (exit 1, best-effort).
        push_outs = _script_outputs(events, "push")
        assert len(push_outs) == 2
        assert all(o["status"] == "push_failed" for o in push_outs)
        assert all(o["pushed"] is False for o in push_outs)
        push_completions = [
            e
            for e in events
            if e["type"] == "script_completed" and e["data"].get("agent_name") == "push"
        ]
        assert all(e["data"]["exit_code"] == 1 for e in push_completions)

        # tick still ran after each failed push (unconditional routing).
        assert _agent_names(events, "agent_started").count("tick") == 2

        # The first milestone genuinely committed locally (the push failing did
        # not lose the work): base + one milestone commit on the local branch.
        log = _git(repo, "log", "--oneline").stdout
        assert log.count("\n") == 2
        # And nothing was published: the nonexistent origin has no branch.
        assert "does-not-exist" not in _git(repo, "branch", "-a").stdout


# ---------------------------------------------------------------------------
# (c) A tick failure also cannot fail the milestone (report-only routing).
# ---------------------------------------------------------------------------


class TestTickFailureCannotHaltTheRun:
    def _fake_gh_bin(self, tmp_path: Path) -> Path:
        """A `gh` that always exits 1 -- a mirror write failure with NO network."""
        bin_dir = tmp_path / "fakebin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        gh.chmod(0o755)
        return bin_dir

    def test_tick_exit_1_still_reaches_the_normal_terminal_state(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, [1])
        fake_bin = self._fake_gh_bin(tmp_path)

        # A live tick (notify_dry_run false) with a repo+issue target, but the
        # only `gh` on PATH exits 1 -> tick reports mirror_failed (exit 1)
        # without touching the network.
        result, output, events = _run_execute_change(
            tmp_path,
            inputs={
                "plan_fixture_path": str(plan),
                "gates_l1_command": "exit 0",
                "change_id": "015-mirror",
                "notify_dry_run": "false",
                "notify_repo": "acme/widgets",
                "notify_issue": "15",
            },
            env_overrides={"PATH": f"{fake_bin}:{VENV_BIN}:/usr/bin:/bin"},
        )

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert output["milestones_processed"] == 1
        assert "workflow_completed" in {e["type"] for e in events}
        assert "workflow_failed" not in {e["type"] for e in events}

        # tick attempted the write and failed (exit 1) -- yet the milestone and
        # the whole run still completed (unconditional tick -> $end route).
        tick_outs = _script_outputs(events, "tick")
        assert len(tick_outs) == 1
        assert tick_outs[0]["status"] == "mirror_failed"
        tick_completions = [
            e
            for e in events
            if e["type"] == "script_completed" and e["data"].get("agent_name") == "tick"
        ]
        assert tick_completions[0]["data"]["exit_code"] == 1
