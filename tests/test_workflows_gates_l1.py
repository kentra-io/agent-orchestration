"""#16 fix: the per-milestone `contract.check` must actually reach the L1
gate, not the static `gates_l1_command` no-op default.

Background (see the `gates` step + `contract_check`/`gates_l1_command`
input descriptions in `workflows/milestone.yaml`, and the `milestone_step`
input_mapping in `workflows/execute-change.yaml`): before this fix,
`execute-change.yaml` forwarded ONLY the static root `gates_l1_command`
input (default `"exit 0"`) into every milestone's ladder, so a real
milestone's own `contract.check` never reached the gate -- L1 was silently
a no-op in every production run. The fix threads a per-milestone
`contract_check` value through (mirroring `commit_paths`' existing
dict-get idiom) and gives it precedence over the static override; when
neither resolves to a real command, the `l1` key is omitted entirely
(native skip) rather than sent as an empty command (which
`orchestration.harness.l1_acceptance` rejects as a harness error).

These tests reuse `test_workflows_ladder.py`'s hermetic Stub-provider
harness (`_run_ladder`, `_base_env`, `_read_events`) -- no box, no LLM, no
network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from stub_provider import write_stub_script
from test_workflows_ladder import (
    EXECUTE_CHANGE_WORKFLOW,
    _base_env,
    _parse_output_json,
    _read_events,
    _run_ladder,
)

REPO_ROOT = Path(__file__).parent.parent
CONDUCTOR_BIN = Path(sys.executable).parent / "conductor"

_PASS_VERIFIER = {
    "pass": True,
    "notes": "looks good",
    "score": 1.0,
    "violations": "none",
}
_FAIL_VERIFIER = {
    "pass": False,
    "notes": "still failing",
    "score": 0.2,
    "violations": "undeclared deviation: touched out-of-path file",
}


class TestContractCheckPrecedence:
    def test_contract_check_passes_and_is_visible_in_output(self, tmp_path: Path) -> None:
        """A milestone with a real, passing `contract_check` gates clean, and
        the executed command text is surfaced in the workflow output for
        operators (milestone.yaml's `l1_command` output field, sourced from
        `gates.output.report.l1.command` -- gates.py's own output schema,
        not redesigned here)."""
        output, _events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "did the work", "halt": "none"}}],
                "verifier": [{"content": _PASS_VERIFIER}],
            },
            inputs={"milestone_id": "M1", "contract_check": "exit 0"},
        )

        assert output["passed"] is True
        assert output["escalated"] is False
        assert output["l1_command"] == "exit 0"

    def test_contract_check_fails_routes_to_escalation_not_a_silent_pass(
        self, tmp_path: Path
    ) -> None:
        """A milestone with a real, FAILING `contract_check` must take the
        ladder's failure/escalation route -- even when the (stub-scripted)
        Verifier itself says `pass: true` -- proving the L1 gate result,
        not just the Verifier, drives the routing (`gates.output.exit_code
        == 0 and verifier.output.pass` -- both must hold)."""
        output, events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "attempt", "halt": "none"}}],
                # Verifier alone would pass every attempt -- if the ladder
                # still escalates, the L1 gate is what's failing it.
                "verifier": [{"content": _PASS_VERIFIER}],
                "orchestrator": [{"content": {"guidance": "try again", "infeasible": False}}],
            },
            inputs={"milestone_id": "M1", "contract_check": "exit 1"},
        )

        assert output["passed"] is False
        assert output["escalated"] is True
        assert output["attempts"] == 3
        assert output["l1_command"] == "exit 1"

        started = [e["data"]["agent_name"] for e in events if e["type"] == "agent_started"]
        assert started.count("implementer") == 3
        assert started.count("escalate") == 1

    def test_no_contract_and_no_static_check_omits_l1_entirely(self, tmp_path: Path) -> None:
        """Neither `contract_check` (absent -> "") nor the static override
        (explicitly the "none" sentinel) resolve to a real command -- the
        `l1` key must be OMITTED from the gates payload (native skip), not
        sent empty (which `l1_acceptance.check` would reject as a harness
        error, exit 2). StrictUndefined must not trip either -- the
        workflow proceeds to a normal Verifier-driven pass."""
        output, _events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "did the work", "halt": "none"}}],
                "verifier": [{"content": _PASS_VERIFIER}],
            },
            inputs={"milestone_id": "M1", "gates_l1_command": "none"},
        )

        assert output["passed"] is True
        assert output["escalated"] is False
        assert output["l1_command"] is None

    def test_contract_check_with_special_chars_stays_valid_json(self, tmp_path: Path) -> None:
        """The check command is an arbitrary shell string that may contain
        quotes -- the gates step's stdin must stay valid JSON (`| tojson`
        escaping), not naive string interpolation."""
        tricky_command = 'echo "hi there" && exit 0'
        output, _events = _run_ladder(
            tmp_path,
            {
                "implementer": [{"content": {"diff_summary": "did the work", "halt": "none"}}],
                "verifier": [{"content": _PASS_VERIFIER}],
            },
            inputs={"milestone_id": "M1", "contract_check": tricky_command},
        )

        assert output["passed"] is True
        assert output["l1_command"] == tricky_command


class TestExecuteChangeForwardsPerMilestoneCheck:
    def test_each_milestones_own_contract_check_reaches_its_own_gate(self, tmp_path: Path) -> None:
        """Change-level proof (the actual bug in #16): `execute-change.yaml`
        must forward EACH milestone's own `contract.check`, not the static
        root `gates_l1_command` for every milestone. A 2-milestone plan:
        milestone 1 declares a passing check, milestone 2 declares a
        failing one -- inspecting each milestone's `gates` script_completed
        event proves the right command ran at the right milestone (not the
        root static default -- which is left at its own default here,
        untouched, to prove it is NOT what drove either milestone)."""
        milestones = [
            {"id": 1, "title": "milestone 1", "contract": {"check": "exit 0"}},
            {"id": 2, "title": "milestone 2", "contract": {"check": "exit 1"}},
        ]
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({"milestones": milestones}), encoding="utf-8")

        script_path = write_stub_script(
            tmp_path / "script",
            {
                "implementer": [{"content": {"diff_summary": "did it", "halt": "none"}}],
                "verifier": [{"content": _PASS_VERIFIER}],
                "orchestrator": [{"content": {"guidance": "try again", "infeasible": False}}],
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
                str(EXECUTE_CHANGE_WORKFLOW),
                "--provider",
                "stub",
                "--skip-gates",
                "--input",
                f"plan_fixture_path={plan_path}",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        output = _parse_output_json(result.stdout)
        assert output["milestones_processed"] == 2

        events = _read_events(tmp_dir)
        gates_calls: list[dict[str, Any]] = []
        for e in events:
            if e["type"] == "script_completed" and e["data"].get("agent_name") == "gates":
                gates_calls.append(json.loads(e["data"]["stdout"]))

        # Milestone 1 (1 attempt, passes): one gates call, command "exit 0".
        # Milestone 2 (fails every attempt -> escalates after 3): three
        # gates calls, all command "exit 1".
        assert len(gates_calls) == 4
        m1_call = gates_calls[0]
        assert m1_call["report"]["l1"]["command"] == "exit 0"
        assert m1_call["pass"] is True

        for m2_call in gates_calls[1:]:
            assert m2_call["report"]["l1"]["command"] == "exit 1"
            assert m2_call["pass"] is False
