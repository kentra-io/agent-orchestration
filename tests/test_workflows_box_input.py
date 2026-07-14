"""The claudebox `box`/`worktree` input path is unbroken end-to-end at the
template level (`tasks/execute-change-box-input-spec.md`, diagnosed during
the first live `kafka-dq/001-e2e-poc` run).

The M8 launcher passes `--input box=<id> --input worktree=<path>`, but
Conductor only threads *declared* inputs into a workflow's context, and a
nested `type: workflow` child's `workflow.input` is populated ONLY from the
parent step's `input_mapping` (not inherited from the root). All three cast
agents that call the ClaudeboxProvider live in the nested `milestone.yaml`,
so the box has to be (a) declared at the `execute-change.yaml` root, (b)
forwarded through `milestone_step.input_mapping`, and (c) declared by
`milestone.yaml`. If any link breaks, the launcher's `--input box=` is
silently discarded and the provider raises `ClaudeboxProvider requires a
'box' key`.

This is the check the stub tier structurally cannot make: `--provider stub`
never requires a box, so a hermetic run of these templates stays green even
with the box path completely severed. These template-level assertions are
what stop that gap from silently returning.

Note on `required: false`: box/worktree are declared optional (default "")
NOT required. Conductor does not validate required-input *presence* at run
start (engine `_apply_input_defaults` silently omits a missing required
input), and templates render under Jinja `StrictUndefined` -- so a
required-but-absent box would crash the stub tier on template render, not
fail loudly at launch. The empty default renders cleanly in the stub tier;
the live tier's loud failure is the provider's own non-retryable
ProviderError when box is empty at the first cast-agent turn. These tests
therefore assert the inputs are DECLARED and FORWARDED, not their
required-ness.
"""

from __future__ import annotations

from pathlib import Path

from conductor.config.loader import load_config

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = REPO_ROOT / "workflows"


class TestBoxInputPathIsUnbroken:
    def test_execute_change_declares_box_and_worktree_inputs(self) -> None:
        config = load_config(WORKFLOWS_DIR / "execute-change.yaml")
        for name in ("box", "worktree"):
            assert name in config.workflow.input, (
                f"execute-change.yaml must declare a '{name}' input so the "
                f"launcher's `--input {name}=` is threaded into context "
                "instead of silently discarded"
            )

    def test_execute_change_forwards_box_and_worktree_to_milestone_step(self) -> None:
        config = load_config(WORKFLOWS_DIR / "execute-change.yaml")
        step = next((a for a in config.agents if a.name == "milestone_step"), None)
        assert step is not None, "execute-change.yaml must ship a milestone_step step"
        assert step.type == "workflow", (
            f"milestone_step must be type=workflow (the nested ladder), got {step.type!r}"
        )
        mapping = step.input_mapping or {}
        for name in ("box", "worktree"):
            assert name in mapping, (
                f"milestone_step.input_mapping must forward '{name}' -- a nested "
                "type: workflow child's workflow.input comes ONLY from this mapping, "
                "not inherited from the root"
            )
            assert f"workflow.input.{name}" in mapping[name], (
                f"milestone_step.input_mapping['{name}'] must source from "
                f"workflow.input.{name} (the root's forwarded value), got "
                f"{mapping[name]!r}"
            )

    def test_milestone_declares_box_and_worktree_inputs(self) -> None:
        config = load_config(WORKFLOWS_DIR / "milestone.yaml")
        for name in ("box", "worktree"):
            assert name in config.workflow.input, (
                f"milestone.yaml must declare a '{name}' input -- this is what "
                f"makes context['workflow']['input']['{name}'] populated for the "
                "implementer/verifier/orchestrator cast agents that call the provider"
            )
