"""Live tier (M1/M1b): NOT run by default -- costs real money and needs a
real, running claudebox box with `claude` authenticated inside it.

Mirrors the plan's live-tier posture (implementation-plan.md §6): "Live
tier (M6/M9 + labeled runs, not per-PR). Real boxes, real models... Costs
money; runs on demand." Registered under the `live` marker so it can be
excluded/selected explicitly; it also self-skips (no subprocess spawned,
no cost incurred) whenever the required env vars aren't set, so an
un-configured default `pytest` run -- CI included -- never spends money
just by collecting this file.

Opt in explicitly, against a box that already has `echoer`/`confirmer`
personas hand-materialized at ``<worktree>/.claude/agents/*.md`` (P9):

    M1B_LIVE_BOX=<box-name> M1B_LIVE_WORKTREE=<worktree-path> \\
      uv run pytest tests/test_workflows_live.py -m live -q

See the M1b DoD writeup for the concrete live run this codifies: a real
`cb exec <box> claude -p ... --agent <role>` executed for both steps,
structured output landed in workflow output, and cost/tokens were
captured on the result (visible in the `--log-file` output / event
JSONL, not asserted here to keep this test's own footprint minimal).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW = REPO_ROOT / "workflows" / "m1b-claudebox-smoke.yaml"
CONDUCTOR_BIN = Path(sys.executable).parent / "conductor"


def _require_live_env() -> tuple[str, str]:
    box = os.environ.get("M1B_LIVE_BOX")
    worktree = os.environ.get("M1B_LIVE_WORKTREE")
    if not box or not worktree:
        pytest.skip(
            "Live tier not configured -- set M1B_LIVE_BOX (a running claudebox "
            "box name with echoer/confirmer personas materialized at "
            "<worktree>/.claude/agents/*.md) and M1B_LIVE_WORKTREE. Skipping "
            "with zero cost incurred."
        )
    return box, worktree


class TestLiveClaudeboxSmoke:
    def test_two_step_workflow_runs_for_real(self, tmp_path: Path) -> None:
        box, worktree = _require_live_env()
        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir()

        result = subprocess.run(
            [
                str(CONDUCTOR_BIN),
                "--silent",
                "run",
                str(WORKFLOW),
                "--input",
                f"box={box}",
                "--input",
                f"worktree={worktree}",
            ],
            env={**os.environ, "TMPDIR": str(tmp_dir)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        output = json.loads(result.stdout)
        assert output["confirmed"] is True
        assert output["echoed_note"] == output["note"]

        jsonl_files = list(tmp_dir.rglob("*.events.jsonl"))
        assert jsonl_files, "expected an event JSONL to be written"
        events = [json.loads(line) for line in jsonl_files[0].read_text().splitlines() if line]
        completed = [e for e in events if e["type"] == "agent_completed"]
        assert {e["data"]["agent_name"] for e in completed} == {"echoer", "confirmer"}
        for e in completed:
            # Real cost/tokens captured on the result (M1 DoD).
            assert e["data"]["cost_usd"] > 0
            assert e["data"]["input_tokens"] > 0
            assert e["data"]["output_tokens"] > 0
