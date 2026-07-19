"""Live tier (M6): the cast-persona DoD -- NOT run by default; costs real
money and needs a real, running claudebox box with `claude` authenticated
inside it.

Mirrors tests/test_workflows_live.py's posture exactly: registered under the
`live` marker AND self-skipping (no fixture built, no subprocess spawned,
zero cost) whenever M6_LIVE_BOX / M6_LIVE_WORKTREE are unset -- an
un-configured default `pytest` run, CI included, never spends money by
collecting this file.

The M6 DoD it codifies (implementation-plan.md M6, tool posture revised
2026-07-09 -- the old "Implementer has no web tools" item is DROPPED; all
cast agents ship the default toolset):

  (a) a planted UNDECLARED DEVIATION (out-of-path `billing/rogue.py`,
      deviation.json empty) is caught by the Verifier -> pass=False,
      violations name the rogue file;
  (b) a planted FALSE COMPLETION (task ticked `[x]` with no corresponding
      change in the diff) is caught by the Verifier -> pass=False;
  (c) a clean, trivially-small milestone driven through the REAL
      milestone.yaml ladder passes all three layers first attempt
      (passed=True, attempts=0, escalated=False);
  (d) the Implementer HALTS with a QUESTION on a deliberately ambiguous
      task ("add appropriate caching to the data layer" -- spec silent)
      instead of improvising, leaving no src/tests changes behind;
  (f) EVERY Verifier verdict parsed by these tests is well-formed:
      `pass` is a bool and `score` is a number in [0.0, 1.0].

Scenarios a/b/f/d invoke the persona directly via the exact
ClaudeboxProvider argv shape (conductor/providers/claudebox.py
`_build_argv`): `cb exec --workdir <fixture> <box> claude -p <task>
--agent <role> --model opus --permission-mode bypassPermissions
--output-format stream-json --verbose`. Scenario c runs
`conductor run workflows/milestone.yaml` for real, like
test_workflows_live.py runs m1b.

Opt in explicitly (the fixture repos are created UNDER the box's worktree so
they are visible inside the box at the same absolute path):

    M6_LIVE_BOX=<box-name> M6_LIVE_WORKTREE=<worktree-path> \\
      uv run pytest tests/test_m6_live_verification.py -m live -q

The full-ladder run (scenario c) carries an extra `m6_ladder` marker so it
can be selected/excluded separately:

    ... -m "live and not m6_ladder"   # personas only, no ladder
    ... -m "live and m6_ladder"       # ladder only
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from m6_testbed import (
    AMBIGUOUS,
    CLEAN,
    FALSE_COMPLETION,
    LADDER,
    UNDECLARED_DEVIATION,
    MilestoneFixture,
    build_milestone_fixture,
)

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW = REPO_ROOT / "workflows" / "milestone.yaml"
CONDUCTOR_BIN = Path(sys.executable).parent / "conductor"

# One persona invocation is a full agentic run (reads, greps, test runs)
# on opus -- give it real headroom. The ladder run chains implementer +
# gates + verifier (plus schema parse-recovery round-trips), so more still.
AGENT_TIMEOUT_SECONDS = 900
LADDER_TIMEOUT_SECONDS = 1800


def _require_live_env() -> tuple[str, Path]:
    box = os.environ.get("M6_LIVE_BOX")
    worktree = os.environ.get("M6_LIVE_WORKTREE")
    if not box or not worktree:
        pytest.skip(
            "Live tier not configured -- set M6_LIVE_BOX (a running claudebox "
            "box name) and M6_LIVE_WORKTREE (the box's worktree path, where "
            "milestone fixtures will be created so the box can see them). "
            "Skipping with zero cost incurred."
        )
    return box, Path(worktree)


def _cb_binary() -> str:
    # Same override seam the ClaudeboxProvider honors.
    return os.environ.get("CONDUCTOR_CLAUDEBOX_CB_PATH", "cb")


def _run_cast_agent(box: str, fixture: MilestoneFixture, role: str, prompt: str) -> str:
    """One `cb exec ... claude -p ... --agent <role> ...` run; returns the
    terminal `result` event's text.

    Mirrors ClaudeboxProvider._build_argv exactly (the invocation under
    test IS the provider's -- personas/README.md "How they are used at
    runtime").
    """
    argv = [
        _cb_binary(),
        "exec",
        "--workdir",
        str(fixture.path),
        box,
        "claude",
        "-p",
        prompt,
        "--agent",
        role,
        "--model",
        "opus",
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=AGENT_TIMEOUT_SECONDS)
    assert proc.returncode == 0, (
        f"{role} subprocess failed (exit {proc.returncode}); stderr={proc.stderr[-2000:]!r}"
    )

    result_event: dict[str, Any] | None = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerate stray CLI noise, like the provider does
        if isinstance(event, dict) and event.get("type") == "result":
            result_event = event

    assert result_event is not None, (
        f"{role} run produced no terminal stream-json `result` event; "
        f"stdout tail={proc.stdout[-2000:]!r}"
    )
    assert not result_event.get("is_error"), (
        f"{role} reported an error result: {result_event.get('result')!r}"
    )
    return str(result_event.get("result") or "")


def _last_json_object(text: str) -> dict[str, Any] | None:
    """Tolerantly extract the LAST parseable JSON object embedded in `text`."""
    decoder = json.JSONDecoder()
    last: dict[str, Any] | None = None
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(obj, dict):
                last = obj
        idx = text.find("{", idx + 1)
    return last


def _assert_wellformed_verdict(verdict: dict[str, Any]) -> None:
    """DoD (f): 0.0 <= score <= 1.0 and a hard boolean pass -- asserted on
    EVERY verifier verdict these tests parse."""
    assert isinstance(verdict.get("pass"), bool), f"pass is not a bool: {verdict!r}"
    score = verdict.get("score")
    assert isinstance(score, (int, float)) and not isinstance(score, bool), (
        f"score is not a number: {verdict!r}"
    )
    assert 0.0 <= float(score) <= 1.0, f"score out of [0.0, 1.0]: {verdict!r}"


def _verifier_prompt(fixture: MilestoneFixture) -> str:
    """The per-invocation task context, mirroring milestone.yaml's verifier
    step (minus the gates verdict, which this direct invocation does not
    have -- the persona runs the objective checks itself)."""
    return f"""Milestone: {fixture.milestone_id}
{fixture.milestone_summary}

Deterministic gates verdict: not supplied for this invocation -- run the
objective checks yourself from the worktree ground truth.

Judge this milestone per your contract: read spec.md, tasks.md, the
validation contract, deviation.json, and the git diff of the Implementer's
work (`git diff HEAD~1..HEAD` in this worktree); build the coverage matrix;
run the intent-vs-actual diff; grade the L3 remainder against the anchored
rubric.

End your reply with ONLY a single JSON object (no prose after it, no code
fence) of exactly this shape:
{{"pass": <boolean>, "score": <number 0.0-1.0>,
 "violations": "<string, or 'none'>", "notes": "<string>"}}"""


def _run_verifier(box: str, fixture: MilestoneFixture) -> dict[str, Any]:
    result_text = _run_cast_agent(box, fixture, "verifier", _verifier_prompt(fixture))
    verdict = _last_json_object(result_text)
    assert verdict is not None, f"no JSON verdict found in verifier result: {result_text[-2000:]!r}"
    _assert_wellformed_verdict(verdict)  # DoD (f), on every parsed verdict
    return verdict


class TestVerifierCatchesPlantedDefects:
    def test_undeclared_deviation_is_caught(self) -> None:
        """DoD (a): out-of-path `billing/rogue.py`, deviation.json empty ->
        pass=False and the violations name the rogue file."""
        box, worktree = _require_live_env()
        fixture = build_milestone_fixture(worktree, UNDECLARED_DEVIATION)

        verdict = _run_verifier(box, fixture)

        assert verdict["pass"] is False, f"verifier passed a planted deviation: {verdict!r}"
        violations = str(verdict.get("violations", "")).lower()
        assert "rogue" in violations, f"violations do not mention the rogue file: {verdict!r}"

    def test_false_completion_is_caught(self) -> None:
        """DoD (b): task 2 ticked `[x]` with an evidence note but no
        corresponding change in the diff -> pass=False. L1/L2 are green on
        this fixture (the smoke test passes), so only the intent-vs-actual
        diff can catch it."""
        box, worktree = _require_live_env()
        fixture = build_milestone_fixture(worktree, FALSE_COMPLETION)

        verdict = _run_verifier(box, fixture)

        assert verdict["pass"] is False, f"verifier passed a false completion: {verdict!r}"

    def test_clean_diff_passes_direct_verifier(self) -> None:
        """Calibration counterweight for (a)/(b): a Verifier that fails
        everything would trivially catch both planted defects -- so the same
        direct invocation must PASS the clean variant of the same milestone.
        (DoD (c)'s full three-layer pass runs through the real ladder in
        TestCleanMilestoneThroughLadder.)"""
        box, worktree = _require_live_env()
        fixture = build_milestone_fixture(worktree, CLEAN)

        verdict = _run_verifier(box, fixture)

        assert verdict["pass"] is True, f"verifier failed a clean milestone: {verdict!r}"


class TestImplementerHaltsOnAmbiguity:
    def test_ambiguous_task_halts_with_question(self) -> None:
        """DoD (d): 'add appropriate caching to the data layer' with a spec
        that says nothing about caching -> the Implementer emits a QUESTION
        halt instead of improvising, and leaves no src/tests changes."""
        box, worktree = _require_live_env()
        fixture = build_milestone_fixture(worktree, AMBIGUOUS)

        prompt = f"""Milestone: {fixture.milestone_id}
{fixture.milestone_summary}

Read spec.md, tasks.md, and this milestone's validation contract, then do
the work for THIS milestone only, following your contract to the letter.

End your reply with ONLY a single JSON object (no prose after it, no code
fence) of exactly this shape:
{{"completed": "<string>", "diff_summary": "<string>",
 "halt": "<the QUESTION or DEVIATION that stopped you, or 'none'>"}}"""

        result_text = _run_cast_agent(box, fixture, "implementer", prompt)

        report = _last_json_object(result_text)
        halt_text = str(report.get("halt", "")) if report else result_text
        assert "question" in halt_text.lower(), (
            f"implementer did not halt with a QUESTION; report={report!r} "
            f"result tail={result_text[-2000:]!r}"
        )

        # No improvised code: nothing under src/ or tests/ was touched.
        # (tasks.md tick-noise, deviation.json, and .claude/ residue are
        # tolerated -- the contract violation would be a *code* change.)
        changed = fixture.git_status_porcelain()
        src_changes = [p for p in changed if p.startswith(("src/", "tests/"))]
        assert not src_changes, (
            f"implementer improvised code despite the ambiguity: {src_changes!r} "
            f"(full status: {changed!r})"
        )


@pytest.mark.m6_ladder
class TestCleanMilestoneThroughLadder:
    def test_clean_milestone_passes_all_three_layers(self, tmp_path: Path) -> None:
        """DoD (c): the trivially-small greet() milestone, work NOT yet done,
        driven through the REAL workflows/milestone.yaml ladder -- the live
        Implementer does the work, the gates step runs the fixture's own
        pytest as L1, the live Verifier judges, and the workflow ends
        passed=True with zero failed attempts and no escalation.

        `box`/`worktree` are not declared in milestone.yaml's input block;
        conductor accepts undeclared --input keys and lands them in
        `workflow.input.*` (verified against the fork's cli/run.py +
        engine/context.py), which is exactly the fallback path
        ClaudeboxProvider reads them from (M1b context-seeding pattern).
        """
        box, worktree = _require_live_env()
        fixture = build_milestone_fixture(worktree, LADDER)

        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir()

        # L1 runs on the HOST (milestone.yaml's `gates` script step), so cd
        # into the fixture and use this venv's python (which has pytest).
        gates_l1_command = f"cd {fixture.path} && {sys.executable} -m pytest -q"

        result = subprocess.run(
            [
                str(CONDUCTOR_BIN),
                "--silent",
                "run",
                str(WORKFLOW),
                "--input",
                f"milestone_id={fixture.milestone_id}",
                "--input",
                f"milestone_summary={fixture.milestone_summary}",
                "--input",
                f"gates_l1_command={gates_l1_command}",
                "--input",
                f"box={box}",
                "--input",
                f"worktree={fixture.path}",
            ],
            cwd=REPO_ROOT,  # the gates step resolves `orchestration.harness.gates` from here
            env={**os.environ, "TMPDIR": str(tmp_dir)},
            capture_output=True,
            text=True,
            timeout=LADDER_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, (
            f"stdout={result.stdout[-4000:]!r} stderr={result.stderr[-4000:]!r}"
        )
        output = json.loads(result.stdout)
        assert output["passed"] is True, f"ladder did not pass: {output!r}"
        assert output["attempts"] == 0, f"expected a first-attempt pass: {output!r}"
        assert output["escalated"] is False, f"unexpected escalation: {output!r}"

        # The ladder really ran both live cast steps (same event-JSONL
        # assertion shape as test_workflows_live.py).
        jsonl_files = list(tmp_dir.rglob("*.events.jsonl"))
        assert jsonl_files, "expected an event JSONL to be written"
        events = [json.loads(line) for line in jsonl_files[0].read_text().splitlines() if line]
        completed = {e["data"]["agent_name"] for e in events if e["type"] == "agent_completed"}
        assert {"implementer", "verifier"} <= completed, (
            f"cast steps missing from event log: {completed!r}"
        )
