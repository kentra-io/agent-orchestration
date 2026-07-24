"""M5 DoD: the 3-attempt escalation ladder, hermetic Stub tier.

Drives `workflows/milestone.yaml` (and `workflows/execute-change.yaml`) with
Conductor's `stub` provider + scripted verdict sequences (see
`tests/stub_provider.py` / `conductor.providers.stub`'s own docstring) to
prove the locked ladder semantics end-to-end with NO box, NO LLM, NO
network -- exactly the M5 "fine-direct demo".

Each test class below maps 1:1 to one M5 DoD bullet; see each test's
docstring for exactly what it proves and why.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from stub_provider import write_stub_script

from orchestration.launch.checkpoint_env import persistent_checkpoint_env

REPO_ROOT = Path(__file__).parent.parent
MILESTONE_WORKFLOW = REPO_ROOT / "workflows" / "milestone.yaml"
EXECUTE_CHANGE_WORKFLOW = REPO_ROOT / "workflows" / "execute-change.yaml"
CONDUCTOR_BIN = Path(sys.executable).parent / "conductor"
VENV_BIN = Path(sys.executable).parent


def _base_env(tmp_dir: Path, script_path: Path) -> dict[str, str]:
    """Environment for a hermetic `conductor` invocation.

    - `CONDUCTOR_STUB_SCRIPT` wires the scripted verdict sequences.
    - `TMPDIR` is relocated to a *persistent* (test-owned) directory via
      `orchestration.launch.checkpoint_env` (the launcher-owned half of
      P4/ADR-0002) -- required for every one of these tests, not just the
      kill/resume one, so checkpoints/events always land somewhere this
      test controls rather than the platform's default `$TMPDIR`.
    - `PATH` puts this repo's own venv first so the `gates`/`escalate`
      script steps' `python3 -m orchestration...` calls resolve against an
      interpreter that has `orchestration` importable (editable install),
      regardless of the script step's cwd.
    """
    return {
        "PATH": f"{VENV_BIN}:/usr/bin:/bin",
        "HOME": str(tmp_dir),
        "CONDUCTOR_STUB_SCRIPT": str(script_path),
        **persistent_checkpoint_env(tmp_dir / "checkpoints"),
    }


def _read_events(tmp_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for jsonl_path in sorted(tmp_dir.rglob("*.events.jsonl")):
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if line:
                events.append(json.loads(line))
    return events


def _agent_names(events: list[dict[str, Any]], event_type: str) -> list[str]:
    return [e["data"]["agent_name"] for e in events if e["type"] == event_type]


def _completed_step_names(events: list[dict[str, Any]]) -> list[str]:
    """Step names with a `*_completed` event -- `agent_completed` for
    provider-backed steps, but `set_completed`/`script_completed` for
    `type: set`/`type: script` steps (Conductor emits a type-specific
    completion event, not a generic one, for those step kinds).
    """
    return [
        e["data"]["agent_name"]
        for e in events
        if e["type"].endswith("_completed") and "agent_name" in e.get("data", {})
    ]


def _run_ladder(
    tmp_path: Path,
    steps: dict[str, list[dict[str, Any]]],
    *,
    inputs: dict[str, str] | None = None,
    workflow: Path = MILESTONE_WORKFLOW,
    timeout: float = 30,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run a workflow to completion against the stub provider; return (output, events)."""
    script_path = write_stub_script(tmp_path / "script", steps)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    env = _base_env(tmp_dir, script_path)

    args = [
        str(CONDUCTOR_BIN),
        "--silent",
        "run",
        str(workflow),
        "--provider",
        "stub",
        # `human_gate` is an interactive Rich prompt by default (see
        # `conductor.gates.human.HumanGateHandler`) -- it EOFErrors with no
        # TTY. `--skip-gates` auto-selects the gate's first option so the
        # escalation test can observe the gate having been *reached*
        # (event + output assertions) without a real human/TTY in the loop;
        # the real poll+resume seam that answers the gate for a genuine
        # human decision is M7's job, not this harness's.
        "--skip-gates",
    ]
    for k, v in (inputs or {}).items():
        args += ["--input", f"{k}={v}"]

    result = subprocess.run(
        args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=timeout
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    output = _parse_output_json(result.stdout)
    return output, _read_events(tmp_dir)


def _parse_output_json(stdout: str) -> dict[str, Any]:
    """Parse the final `{...}` workflow output out of `conductor run`'s stdout.

    `--skip-gates` prints an "Auto-selecting: ..." console line ahead of
    the JSON output (even under `--silent`) whenever a `human_gate` is
    auto-resolved -- so the JSON is the *last* top-level object on stdout,
    not necessarily the only thing on it.
    """
    return json.loads(stdout[stdout.index("{") :])


# ---------------------------------------------------------------------------
# pass@1 / pass@2 / pass@3 -- the milestone advances, it does not escalate.
# ---------------------------------------------------------------------------


class TestPassAtAttempt:
    def test_pass_at_1_advances_without_orchestrator_or_escalation(self, tmp_path: Path) -> None:
        output, events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "did the work", "halt": "none"}}],
                "verifier": [
                    {
                        "content": {
                            "pass": True,
                            "notes": "looks good",
                            "score": 1.0,
                            "violations": "none",
                        }
                    }
                ],
            },
            inputs={"milestone_id": "M1"},
        )

        assert output["passed"] is True
        assert output["escalated"] is False
        assert _agent_names(events, "agent_started").count("implementer") == 1
        assert "orchestrator" not in _agent_names(events, "agent_started")
        assert "escalate" not in _agent_names(events, "agent_started")
        assert "human_gate" not in _agent_names(events, "agent_started")

    def test_pass_at_2_advances_after_one_orchestrator_guided_retry(self, tmp_path: Path) -> None:
        output, events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "attempt", "halt": "none"}}],
                "verifier": [
                    {
                        "content": {
                            "pass": False,
                            "notes": "missing tests",
                            "score": 0.2,
                            "violations": "undeclared deviation: touched out-of-path file",
                        }
                    },
                    {
                        "content": {
                            "pass": True,
                            "notes": "now covered",
                            "score": 1.0,
                            "violations": "none",
                        }
                    },
                ],
                "orchestrator": [
                    {"content": {"guidance": "add the missing tests", "infeasible": False}}
                ],
            },
            inputs={"milestone_id": "M1"},
        )

        assert output["passed"] is True
        assert output["escalated"] is False
        assert output["attempts"] == 1  # exactly one recorded failure
        assert _agent_names(events, "agent_started").count("implementer") == 2
        assert _agent_names(events, "agent_started").count("orchestrator") == 1
        assert "escalate" not in _agent_names(events, "agent_started")

    def test_pass_at_3_advances_after_two_orchestrator_guided_retries(self, tmp_path: Path) -> None:
        output, events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "attempt", "halt": "none"}}],
                "verifier": [
                    {
                        "content": {
                            "pass": False,
                            "notes": "fail 1",
                            "score": 0.2,
                            "violations": "undeclared deviation: touched out-of-path file",
                        }
                    },
                    {
                        "content": {
                            "pass": False,
                            "notes": "fail 2",
                            "score": 0.2,
                            "violations": "undeclared deviation: touched out-of-path file",
                        }
                    },
                    {
                        "content": {
                            "pass": True,
                            "notes": "pass",
                            "score": 1.0,
                            "violations": "none",
                        }
                    },
                ],
                "orchestrator": [
                    {"content": {"guidance": "guidance 1", "infeasible": False}},
                    {"content": {"guidance": "guidance 2", "infeasible": False}},
                ],
            },
            inputs={"milestone_id": "M1"},
        )

        assert output["passed"] is True
        assert output["escalated"] is False
        assert output["attempts"] == 2
        assert _agent_names(events, "agent_started").count("implementer") == 3
        assert _agent_names(events, "agent_started").count("orchestrator") == 2
        assert "escalate" not in _agent_names(events, "agent_started")


# ---------------------------------------------------------------------------
# 3 fails -> escalate: `Needs human input` + the GH-label mirror step.
# ---------------------------------------------------------------------------


class TestEscalation:
    def test_three_fails_escalate_to_human_gate_with_gh_label_mirror(self, tmp_path: Path) -> None:
        """3 consecutive verifier failures -> Conductor's counter (not the
        Orchestrator) flips to escalation: the `escalate` step (the
        GitHub-label mirror, P7) runs, then `human_gate` is reached -- the
        canonical `Needs human input` pause (spec sec 7.1).

        The hermetic tier asserts the notify step *ran with the right
        label*, per the M5 DoD wording, without a real GH call --
        `notify_dry_run` defaults to true, so `orchestration.launch.
        notify_escalation` never shells out to `gh`.
        """
        output, events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "attempt", "halt": "none"}}],
                # Settles on "fail" forever once exhausted -- three real
                # verifier calls all fail.
                "verifier": [
                    {
                        "content": {
                            "pass": False,
                            "notes": "still failing",
                            "score": 0.2,
                            "violations": "undeclared deviation: touched out-of-path file",
                        }
                    }
                ],
                "orchestrator": [{"content": {"guidance": "try again", "infeasible": False}}],
            },
            inputs={"milestone_id": "M1"},
        )

        assert output["passed"] is False
        assert output["escalated"] is True
        assert output["attempts"] == 3
        assert output["escalation_label"] == "needs-human-input"
        assert output["escalation_notified"] is True

        started = _agent_names(events, "agent_started")
        assert started.count("implementer") == 3
        assert started.count("orchestrator") == 2
        assert started.count("escalate") == 1
        assert started.count("human_gate") == 1
        # escalate ran BEFORE human_gate (the label mirror precedes the pause).
        assert started.index("escalate") < started.index("human_gate")


# ---------------------------------------------------------------------------
# Guidance from the orchestrator reaches attempt N+1's implementer prompt.
# ---------------------------------------------------------------------------


class TestGuidanceReachesNextAttempt:
    def test_orchestrator_runs_between_the_two_implementer_calls(self, tmp_path: Path) -> None:
        """Dynamic half of the proof: the control-flow actually loops
        orchestrator's guidance back into another implementer call (not,
        say, straight back to gates/verifier with stale context).
        """
        _output, events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "attempt", "halt": "none"}}],
                "verifier": [
                    {
                        "content": {
                            "pass": False,
                            "notes": "fail",
                            "score": 0.2,
                            "violations": "undeclared deviation: touched out-of-path file",
                        }
                    },
                    {
                        "content": {
                            "pass": True,
                            "notes": "pass",
                            "score": 1.0,
                            "violations": "none",
                        }
                    },
                ],
                "orchestrator": [
                    {"content": {"guidance": "UNIQUE_GUIDANCE_MARKER", "infeasible": False}}
                ],
            },
            inputs={"milestone_id": "M1"},
        )
        started = _agent_names(events, "agent_started")
        # implementer(1) -> gates -> verifier(1, fail) -> counter -> orchestrator -> implementer(2)
        # ... -> verifier(2, pass) -> commit -> push -> tick
        # (dry-run milestone commit on the pass path, then the best-effort
        # mirror legs: push the run branch and tick the checklist comment;
        # both are unconditional report-only script steps, so each emits its
        # own agent_started event without ever failing the milestone).
        assert started == [
            "implementer",
            "gates",
            "verifier",
            "counter",
            "orchestrator",
            "implementer",
            "gates",
            "verifier",
            "commit",
            "push",
            "tick",
        ]

    def test_implementer_prompt_template_interpolates_orchestrator_guidance(self) -> None:
        """Static half of the proof: the *actual shipped* `implementer.prompt`
        Jinja template in `workflows/milestone.yaml`, when rendered with an
        `orchestrator.output.guidance` value in context (exactly the shape
        the engine builds after a real orchestrator call -- see
        `conductor.engine.context.WorkflowContext.build_for_agent`), contains
        that guidance text verbatim.

        Combined with the dynamic test above (orchestrator really does run
        between the two implementer calls), this proves "guidance reaches
        attempt N+1's prompt" without needing to capture a live LLM call --
        the StubProvider discards `rendered_prompt` entirely (see its
        docstring), so the *content* of what would have been sent to a real
        model can only be checked by rendering the template directly.
        """
        from conductor.config.loader import load_config
        from conductor.executor.template import TemplateRenderer

        config = load_config(MILESTONE_WORKFLOW)
        implementer = next(a for a in config.agents if a.name == "implementer")

        renderer = TemplateRenderer()
        rendered = renderer.render(
            implementer.prompt,
            {
                "workflow": {"input": {"milestone_id": "M1", "milestone_summary": "do the thing"}},
                "orchestrator": {"output": {"guidance": "UNIQUE_GUIDANCE_MARKER"}},
            },
        )
        assert "UNIQUE_GUIDANCE_MARKER" in rendered

        # And when orchestrator hasn't run yet (attempt 1), the guidance
        # section is absent entirely -- not a stale/empty rendering.
        rendered_attempt_1 = renderer.render(
            implementer.prompt,
            {"workflow": {"input": {"milestone_id": "M1", "milestone_summary": "do the thing"}}},
        )
        assert "Guidance from a prior failed attempt" not in rendered_attempt_1


# ---------------------------------------------------------------------------
# kill -9 mid-attempt-2 -> conductor resume -> count intact.
# ---------------------------------------------------------------------------


class TestKillResume:
    def test_kill_mid_attempt_2_then_resume_preserves_the_attempt_count(
        self, tmp_path: Path
    ) -> None:
        """Crash-safety of the counter (P4/P5), proven against the REAL
        engine + a real OS-level SIGKILL, not a simulated failure.

        Script design (see the long-form rationale in this test's inline
        comments): the StubProvider's per-step call cursor is IN-MEMORY and
        resets to 0 in the resumed process (a fresh `StubProvider`
        instance) -- so the verdict sequence must be robust to "first call
        in *this* process" semantics, not "the Nth call overall". A
        `[fail, pass]` verifier script (settles on `pass` after the first
        call) has the right property: whichever process makes it, the
        FIRST call to `verifier` fails and every call after passes. Attempt
        1 (killed process) consumes the first "fail"; attempt 2 (resumed
        process, fresh cursor) consumes ITS first call = "fail" again
        (correctly representing "attempt 2 also fails"); attempt 3 (same
        resumed process, cursor now at its 2nd call) = "pass". The
        `counter` value that matters -- `attempts` -- lives in Conductor's
        checkpointed *context*, not the stub's cursor, so it is NOT
        reset by the process restart: it must read exactly 2 (one
        increment pre-kill, one post-kill) when the run completes.
        """
        script_path = write_stub_script(
            tmp_path / "script",
            {
                "implementer": [{"content": {"diff_summary": "attempt", "halt": "none"}}],
                "verifier": [
                    {
                        "content": {
                            "pass": False,
                            "notes": "fail",
                            "score": 0.2,
                            "violations": "undeclared deviation: touched out-of-path file",
                        }
                    },
                    {
                        "content": {
                            "pass": True,
                            "notes": "pass",
                            "score": 1.0,
                            "violations": "none",
                        }
                    },
                ],
                "orchestrator": [{"content": {"guidance": "keep going", "infeasible": False}}],
            },
        )
        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir()
        env = _base_env(tmp_dir, script_path)

        proc = subprocess.Popen(
            [
                str(CONDUCTOR_BIN),
                "--silent",
                "run",
                str(MILESTONE_WORKFLOW),
                "--provider",
                "stub",
                "--input",
                "milestone_id=M1",
                # `gates` is a real subprocess (unlike the in-process,
                # near-instant stub calls) -- a deliberate `sleep` here is a
                # semantically-neutral perturbation (still exits 0, still
                # passes L1) that stretches the run over real wall-clock
                # time, so the poll loop below has an actual window to
                # observe `counter`'s first completion and land the SIGKILL
                # before the (otherwise sub-10ms) run finishes on its own.
                "--input",
                "gates_l1_command=sleep 0.5 && exit 0",
            ],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Poll the event JSONL (written incrementally) for `counter`'s
            # first completion -- by then `attempts: 1` is already
            # checkpointed (every_agent). Kill immediately after: whether
            # the NEXT checkpoint (for `orchestrator`, about to run) landed
            # or not, the counter's own checkpointed value is what we
            # assert on, so the test is robust to either outcome.
            deadline = time.monotonic() + 15
            counter_ran = False
            while time.monotonic() < deadline:
                if "counter" in _completed_step_names(_read_events(tmp_dir)):
                    counter_ran = True
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.005)
            assert counter_ran, "workflow completed before we could kill it mid-ladder"

            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        assert proc.returncode != 0  # confirms it was actually killed, not a clean exit

        resume = subprocess.run(
            [
                str(CONDUCTOR_BIN),
                "--silent",
                "resume",
                str(MILESTONE_WORKFLOW),
                # `--provider` is a CLI-time override (mutates the in-memory
                # config, never written into the checkpoint) -- `resume`
                # reloads the YAML fresh, so it must be re-supplied or the
                # resumed run falls back to the YAML's `runtime.provider:
                # claudebox` default.
                "--provider",
                "stub",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert resume.returncode == 0, f"stdout={resume.stdout!r} stderr={resume.stderr!r}"
        output = json.loads(resume.stdout)

        assert output["passed"] is True
        assert output["escalated"] is False
        assert output["attempts"] == 2, (
            "the attempt count must reflect BOTH the pre-kill failure and the "
            "post-kill failure -- neither lost (reset to 0) nor double-counted"
        )


# ---------------------------------------------------------------------------
# Transient provider error consumes `retry:`, NOT a ladder attempt.
# ---------------------------------------------------------------------------


class TestTransientErrorDoesNotConsumeAnAttempt:
    def test_retryable_provider_error_never_reaches_the_counter(self, tmp_path: Path) -> None:
        """`retry:` (native, per-provider) is orthogonal to the ladder's own
        `counter` set-step (P5) -- a transient provider error must never
        increment it.

        Load-bearing caveat (see `workflows/README.md` and the M5 report):
        `conductor.providers.stub.StubProvider` does not itself implement a
        retry loop -- retry is resolved per-provider (`claude.py`,
        `claudebox.py`, `copilot.py` each call `_execute_with_retry`
        internally; `stub.py` has no such call, confirmed by reading the
        fork's source at the pin). So a scripted `error` entry propagates
        immediately as an unhandled `ProviderError` under the hermetic Stub
        tier -- it cannot itself exercise the retry-then-succeed path. What
        IS provable hermetically, and is the actual structural guarantee
        P5 depends on, is that a provider error aborts the step BEFORE
        `counter` ever runs -- `counter` only executes on the gates/verifier
        FAIL route, never on a provider exception. `retry:` is wired on
        every provider-backed step in `workflows/milestone.yaml` for the
        live tier (M6+), where it is real and does apply.
        """
        script_path = write_stub_script(
            tmp_path / "script",
            {
                "implementer": [{"error": "rate limited", "error_retryable": True}],
            },
        )
        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir()
        env = _base_env(tmp_dir, script_path)

        result = subprocess.run(
            [
                str(CONDUCTOR_BIN),
                "--silent",
                "run",
                str(MILESTONE_WORKFLOW),
                "--provider",
                "stub",
                "--input",
                "milestone_id=M1",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0  # the (unretried, under Stub) error aborts the run

        events = _read_events(tmp_dir)
        assert "counter" not in _agent_names(events, "agent_started")
        assert "counter" not in _completed_step_names(events)


# ---------------------------------------------------------------------------
# max_iterations never bites on a 10-milestone plan.
# ---------------------------------------------------------------------------


class TestMaxIterationsHeadroom:
    def test_ten_milestone_plan_completes_without_hitting_the_iteration_cap(
        self, tmp_path: Path
    ) -> None:
        """`execute-change.yaml` sequences 10 milestones (each passing on
        attempt 1) through `for_each` -> `milestone.yaml`; the outer
        workflow's `limits.max_iterations: 60` must comfortably cover it
        (P4: "computed from the plan... generously oversized").
        """
        milestones = [{"id": i, "title": f"milestone {i}"} for i in range(1, 11)]
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({"milestones": milestones}), encoding="utf-8")

        output, events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "did it", "halt": "none"}}],
                "verifier": [
                    {
                        "content": {
                            "pass": True,
                            "notes": "good",
                            "score": 1.0,
                            "violations": "none",
                        }
                    }
                ],
            },
            inputs={"plan_fixture_path": str(plan_path)},
            workflow=EXECUTE_CHANGE_WORKFLOW,
            timeout=60,
        )

        assert output["milestones_processed"] == 10
        assert "iteration_limit" not in {e["type"] for e in events}
        assert "workflow_completed" in {e["type"] for e in events}


# ---------------------------------------------------------------------------
# S1 spike: does `max_concurrent: 1` preserve for_each item order?
# ---------------------------------------------------------------------------


class TestForEachOrderingS1:
    def test_max_concurrent_1_output_order_matches_input_order(self, tmp_path: Path) -> None:
        """S1 (implementation-plan.md sec 9.1): confirm `max_concurrent: 1`
        on a `for_each` group over `milestone.yaml` preserves the
        milestones' declared list order (vs. batches processed out of
        order, which would break "milestones run sequentially, in order" --
        orchestration.md sec 4.1).

        Distinguishable-by-content milestones (different `milestone_id`s)
        run through the REAL for_each + sub-workflow nesting (a probe
        workflow structurally identical to `execute-change.yaml`'s
        `milestone_runs` group, with the aggregated order surfaced directly
        in `output:` rather than inferred from events), proving the
        aggregated `for_each` output list preserves input order.
        """
        milestones = [{"milestone_id": mid, "milestone_summary": ""} for mid in ["A", "B", "C"]]
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({"milestones": milestones}), encoding="utf-8")

        script_path = write_stub_script(
            tmp_path / "script",
            {
                "implementer": [{"content": {"diff_summary": "did it", "halt": "none"}}],
                "verifier": [
                    {
                        "content": {
                            "pass": True,
                            "notes": "good",
                            "score": 1.0,
                            "violations": "none",
                        }
                    }
                ],
            },
        )
        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir()
        env = _base_env(tmp_dir, script_path)

        # A second workflow, structurally identical to execute-change.yaml's
        # for_each but with milestone_id surfaced directly in the aggregated
        # `output:` block, so ordering is asserted directly rather than
        # inferred from events.
        probe_workflow = tmp_path / "for_each_order_probe.yaml"
        probe_workflow.write_text(
            f"""
workflow:
  name: for-each-order-probe
  entry_point: read_plan
  runtime:
    provider: claudebox
  input:
    plan_fixture_path: {{type: string, required: true}}
  limits:
    max_iterations: 30
agents:
  - name: read_plan
    type: script
    command: cat
    args: ["{{{{ workflow.input.plan_fixture_path }}}}"]
    routes:
      - to: milestone_runs
for_each:
  - name: milestone_runs
    type: for_each
    source: read_plan.output.milestones
    as: milestone
    max_concurrent: 1
    agent:
      name: milestone_runner
      type: workflow
      workflow: {MILESTONE_WORKFLOW}
      input_mapping:
        milestone_id: "{{{{ milestone.milestone_id }}}}"
        milestone_summary: "{{{{ milestone.milestone_summary }}}}"
    routes:
      - to: "$end"
output:
  order: "{{{{ milestone_runs.outputs | map(attribute='milestone_id') | list | tojson }}}}"
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                str(CONDUCTOR_BIN),
                "--silent",
                "run",
                str(probe_workflow),
                "--provider",
                "stub",
                "--input",
                f"plan_fixture_path={plan_path}",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        output = _parse_output_json(result.stdout)
        assert output["order"] == ["A", "B", "C"]
