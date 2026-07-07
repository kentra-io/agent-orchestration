"""Plan-artifact reading, hashing, and remaining-milestone re-derivation.

`orchestration.md` sec 7.2: when a human resolves an escalation in a Mode-A
session, the resume seam must tell whether the human's edit **materially
changed the plan artifact** — if so, the remaining milestone list is
re-derived from the new plan rather than blindly resumed in place (whose
baked-in `read_plan.output.milestones` would still reflect the STALE list).

Pure, hermetic, no Conductor/subprocess dependency — every function here
takes/returns plain data so it is unit-testable without a workflow run.

Spec-lifecycle reality check (2026-07-07, verified against the shipped
v0.1.0 CLI): there is no `apply`/machine-readable-plan command at all
(`init`/`validate`/`approve`/`status`/`archive`/`guard` is the complete v1
verb set) — the plan artifact this module hashes/reads is the same fixture
JSON `execute-change.yaml`'s `read_plan` step already reads (see that
workflow's header comment), not a real `lifecycle apply` surface. A real
machine-readable plan surface is `spec-lifecycle`'s to build (out of scope
here, and not required by M7 -- see `orchestration/resume/README.md`).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class PlanReadError(ValueError):
    """The plan artifact is missing, malformed, or fails validation."""


def load_milestones(plan_path: str | Path) -> list[dict[str, Any]]:
    """Read a plan fixture JSON's `milestones` array.

    Args:
        plan_path: Path to a JSON file shaped like
            `{"milestones": [{"milestone_id": ..., "milestone_summary": ...}, ...]}`
            (the same shape `execute-change.yaml`'s `read_plan` step reads).

    Returns:
        The `milestones` list, in file order.

    Raises:
        PlanReadError: the file is missing, not valid JSON, not an object,
            or has no `milestones` list.
    """
    path = Path(plan_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanReadError(f"cannot read plan artifact {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanReadError(f"plan artifact {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("milestones"), list):
        raise PlanReadError(f"plan artifact {path} must be an object with a 'milestones' list")
    return data["milestones"]


def hash_plan(plan_path: str | Path) -> str:
    """Return a stable content hash (`sha256:<hex>`) of the plan artifact.

    Hashes the raw file bytes (not a parsed/canonicalized form) — this is
    deliberately the cheapest, least-clever check: "did the bytes on disk
    change since we captured the baseline at escalation time." A no-op
    edit that leaves the bytes identical (e.g. round-tripped through an
    editor that re-serializes identically) correctly reports "unchanged";
    anything else, including reordering or whitespace-only edits, reports
    "changed" — a false positive there just costs a redundant (but
    correct) re-derivation, never a missed one.
    """
    digest = hashlib.sha256(Path(plan_path).read_bytes()).hexdigest()
    return f"sha256:{digest}"


def derive_remaining_milestones(
    new_milestones: list[dict[str, Any]],
    completed_milestone_ids: list[str],
) -> list[dict[str, Any]]:
    """Compute the remaining milestone list after a plan edit.

    Args:
        new_milestones: The (possibly edited) plan's full milestone list, in
            its own order.
        completed_milestone_ids: IDs already completed against the OLD plan
            (see `orchestration.resume.checkpoint.completed_milestone_ids`).

    Returns:
        `new_milestones` filtered down to the ones NOT already completed,
        preserving the NEW plan's order. Set-based exclusion (by
        `milestone_id`) rather than positional/index-based, so it is robust
        to the human reordering, inserting, or removing milestones in the
        edit — the one invariant preserved is "never re-run a milestone
        whose id already completed under the old plan."

        A completed id that no longer appears in `new_milestones` at all
        (the human deleted it) is silently dropped, not an error — its
        work already happened and there is nothing left to schedule.
    """
    completed = set(completed_milestone_ids)
    return [m for m in new_milestones if m.get("milestone_id") not in completed]


def write_plan_fixture(dest_path: str | Path, milestones: list[dict[str, Any]]) -> Path:
    """Write a `{"milestones": [...]}` fixture file (the inverse of `load_milestones`).

    Used by the resume seam to materialize a fresh `plan_fixture_path` input
    for a re-derived (materially-changed-plan) resume: a brand-new
    `conductor run` over just the remaining milestones, cursor starting at 0.
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"milestones": milestones}, indent=2), encoding="utf-8")
    return dest
