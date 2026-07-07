"""Hermetic (every-PR) tier: the shipped M1 claudebox smoke workflow, run
against Conductor's ``stub`` provider instead of a real box/LLM.

No claudebox box, no ``claude`` CLI, no network, no LLM call -- the
scripted StubProvider (fork-carried, ``kentra-patches``) drives the exact
same ``workflows/m1b-claudebox-smoke.yaml`` template used by the live tier
(see ``tests/test_workflows_live.py``, gated separately), proving the
control-flow/structured-output *shape* is identical: two steps, the
`echoer` -> `confirmer` route, and the declared `output:` schemas landing
in the final workflow output -- exactly what M1's DoD calls "Stub tier
runs the same template hermetically".
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from stub_provider import write_stub_script

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW = REPO_ROOT / "workflows" / "m1b-claudebox-smoke.yaml"
CONDUCTOR_BIN = Path(sys.executable).parent / "conductor"


def _run_stub(tmp_path: Path, steps: dict[str, list[dict]]) -> tuple[dict, list[dict]]:
    """Run the shipped workflow against the stub provider; return (output, events)."""
    script_path = write_stub_script(tmp_path / "script", steps)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    result = subprocess.run(
        [
            str(CONDUCTOR_BIN),
            "--silent",
            "run",
            str(WORKFLOW),
            "--provider",
            "stub",
            "--input",
            "box=unused-in-stub-tier",
            "--input",
            "worktree=unused-in-stub-tier",
        ],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CONDUCTOR_STUB_SCRIPT": str(script_path),
            "TMPDIR": str(tmp_dir),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    output = json.loads(result.stdout)

    events: list[dict] = []
    for jsonl_path in tmp_dir.rglob("*.events.jsonl"):
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if line:
                events.append(json.loads(line))
    return output, events


class TestStubTierMatchesLiveShape:
    def test_happy_path_two_step_control_flow_and_structured_output(
        self, tmp_path: Path
    ) -> None:
        output, events = _run_stub(
            tmp_path,
            {
                "echoer": [{"content": {"ok": True, "note": "stub-note"}}],
                "confirmer": [{"content": {"confirmed": True, "echoed_note": "stub-note"}}],
            },
        )

        # Same shape as the live 2-step run: both declared output fields
        # present, and confirmer's echoed_note carries echoer's note through
        # the template ({{ echoer.output.note }}) -- proving structured
        # output flows between steps identically under the stub tier.
        assert output == {
            "note": "stub-note",
            "confirmed": True,
            "echoed_note": "stub-note",
        }

        event_types = [e["type"] for e in events]
        assert event_types.count("agent_started") == 2
        assert event_types.count("agent_completed") == 2
        route_events = [e for e in events if e["type"] == "route_taken"]
        assert {"from_agent": "echoer", "to_agent": "confirmer"} in [
            {"from_agent": r["data"]["from_agent"], "to_agent": r["data"]["to_agent"]}
            for r in route_events
        ]
        assert event_types[-1] == "workflow_completed"
        # every_agent checkpointing (P4) fires identically under the stub
        # provider -- the durability config is workflow-level, not
        # provider-specific.
        assert "checkpoint_saved" in event_types

    def test_falsy_echoer_output_propagates_to_confirmer(self, tmp_path: Path) -> None:
        """Same control-flow, opposite branch -- confirmer sees ok=False."""
        output, _events = _run_stub(
            tmp_path,
            {
                "echoer": [{"content": {"ok": False, "note": "stub-not-ok"}}],
                "confirmer": [{"content": {"confirmed": False, "echoed_note": "stub-not-ok"}}],
            },
        )

        assert output["note"] == "stub-not-ok"
        assert output["confirmed"] is False
