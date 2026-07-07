"""The consent invariant (`orchestration.md` sec 7.3, M7 DoD bullet 4).

"`lifecycle approve`/`archive` must be absent from every Mode-B (Conductor-
spawned) agent's tool surface ... The operator MCP legitimately DOES expose
`record_approval`/`archive_change` — that's the human's Mode-A surface, not
a Mode-B agent. Do not conflate the two."

**Scope caveat, stated up front (also see `orchestration/mcp/README.md`'s
"consent invariant" section):** this module repo does not yet contain
materialized `.claude/agents/<role>.md` persona files at all — those are
the kentra-branded layer (agent-definition sec 12, P9: "hand-materialized
`.claude/agents/<role>.md` files checked into the branded layer"), which
lands with M6/M9 in the harness repo, not here. So the FULL spec's eventual
Mode-B tool surface (a persona's frontmatter grants) cannot be checked from
this repo today. What CAN be checked, and IS the complete Mode-B surface
THIS repo currently defines, is every Conductor `agent`-type step
definition in every shipped `workflows/*.yaml` template: their `prompt:`
text AND — a real finding from writing this test, correcting an assumption
in the M7 brief's "Conductor's `AgentDef` schema has no native `tools:`
field" — `AgentDef` DOES carry a native per-step `tools: list[str] | None`
field (`None` = all tools, `[]` = none; verified against
`conductor.config.schema.AgentDef`, `agent`-type steps only). None of this
repo's shipped steps set it today (all leave it `None`/unset), but this
test now scans it like every other field, and
`test_conductor_agentdef_schema_still_has_no_tools_or_skills_field` below
guards against a FUTURE tool/skill-shaped field silently going unwatched.
"""

from __future__ import annotations

import re
from pathlib import Path

from conductor.config.loader import load_config
from conductor.config.schema import AgentDef

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = REPO_ROOT / "workflows"

# Verbs/phrases that would constitute a Mode-B agent holding approval
# authority -- checked as substrings (case-insensitive) so "lifecycle
# approve", "lifecycle-approve" (the skill name), "lifecycle archive", and
# a bare "--approve"/"record_approval"/"archive_change" (the MCP tool
# names, in case someone pastes a tool invocation into a prompt) are all
# caught.
FORBIDDEN_PATTERNS = [
    r"lifecycle\s+approve",
    r"lifecycle-approve",
    r"lifecycle\s+archive",
    r"--approve\b",
    r"record_approval",
    r"archive_change",
]

_FORBIDDEN_RE = re.compile("|".join(FORBIDDEN_PATTERNS), re.IGNORECASE)

# The only fields Conductor's `AgentDef` schema defines that could plausibly
# carry a tool/verb grant or free text referencing one (verified against
# `conductor.config.schema.AgentDef` at the pinned SHA -- no `tools:`/
# `skills:` field exists at all today; if one is ever added, add its name
# here so this test starts covering it instead of silently missing it).
_SCANNABLE_FIELDS = ("prompt", "description", "command", "args", "stdin", "tools")

# Tool/skill-shaped `AgentDef` fields already known and covered by
# `_SCANNABLE_FIELDS` above -- the guard test below fails only when a field
# NOT in this set appears, so it stays a live tripwire for a genuinely new
# field instead of re-flagging `tools` (already handled) forever.
_KNOWN_TOOL_SKILL_LIKE_FIELDS = {"tools"}


def _agent_step_definitions() -> list[tuple[Path, AgentDef]]:
    """Every `agents:`-list step definition across every shipped workflow template.

    Loaded through Conductor's own `load_config` (the real, validated
    `AgentDef` pydantic model) rather than a raw YAML parse — this repo has
    no PyYAML dependency (Conductor itself uses `ruamel.yaml` internally),
    and going through the real schema is more robust anyway: it exercises
    the actual field set `AgentDef` validates, not whatever a hand-rolled
    YAML walk happens to find.
    """
    steps: list[tuple[Path, AgentDef]] = []
    for workflow_path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        config = load_config(workflow_path)
        for step in config.agents:
            steps.append((workflow_path, step))
        for group in config.for_each:
            steps.append((workflow_path, group.agent))
        for group in config.parallel:
            for member_name in group.agents:
                member = next((a for a in config.agents if a.name == member_name), None)
                if member is not None:
                    steps.append((workflow_path, member))
    return steps


def _step_text(step: AgentDef) -> str:
    """Concatenate every scannable text field of one step definition."""
    parts: list[str] = []
    for field in _SCANNABLE_FIELDS:
        value = getattr(step, field, None)
        if value is None:
            continue
        parts.append(value if isinstance(value, str) else " ".join(map(str, value)))
    return "\n".join(parts)


class TestNoConsentVerbInAnyWorkflowStep:
    """The Mode-B surface THIS repo defines today never grants approval authority."""

    def test_no_shipped_workflow_step_references_a_consent_verb(self) -> None:
        steps = _agent_step_definitions()
        assert steps, "expected at least one agent step across workflows/*.yaml"

        offenders = [
            (path.name, step.name, match.group(0))
            for path, step in steps
            if (match := _FORBIDDEN_RE.search(_step_text(step))) is not None
        ]
        assert offenders == [], (
            f"Mode-B workflow step(s) reference a consent verb: {offenders} -- "
            "lifecycle approve/archive must only be reachable through the "
            "operator MCP's Mode-A surface (orchestration/mcp/), never a "
            "Conductor-spawned workflow step."
        )

    def test_conductor_agentdef_schema_has_no_UNCOVERED_tools_or_skills_field(self) -> None:
        """Guard the scope caveat itself: `tools` is the one tool/skill-shaped
        `AgentDef` field known today (already scanned, see
        `_KNOWN_TOOL_SKILL_LIKE_FIELDS`/`_SCANNABLE_FIELDS`). If the pinned
        fork ever adds ANOTHER one (e.g. a future `skills:`), this test fails
        loudly so it gets added to `_SCANNABLE_FIELDS` too, instead of this
        invariant silently missing it.
        """
        field_names = set(AgentDef.model_fields.keys())
        suspicious = {f for f in field_names if "tool" in f.lower() or "skill" in f.lower()}
        uncovered = suspicious - _KNOWN_TOOL_SKILL_LIKE_FIELDS
        assert uncovered == set(), (
            f"AgentDef gained field(s) {uncovered} that look tool/skill-shaped and "
            "are NOT in _SCANNABLE_FIELDS -- extend both sets to cover them."
        )


class TestOperatorMCPLegitimatelyExposesConsentVerbs:
    """The positive half of the invariant: the OPERATOR (Mode-A) surface,
    unlike the Mode-B workflow surface above, legitimately has these verbs
    -- this is what distinguishes "conflating the two" from "checking the
    right one" (see this module's docstring and `orchestration/mcp/
    README.md`'s consent-invariant section).
    """

    def test_mcp_server_exposes_record_approval_and_archive_change(self) -> None:
        from orchestration.mcp.server import mcp

        tool_names = set(mcp._tool_manager._tools.keys())
        assert {"record_approval", "archive_change"} <= tool_names
