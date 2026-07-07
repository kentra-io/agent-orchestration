"""Plan-artifact reading, hashing, and remaining-milestone re-derivation.

`orchestration.md` sec 7.2: when a human resolves an escalation in a Mode-A
session, the resume seam must tell whether the human's edit **materially
changed the plan artifact** — if so, the remaining milestone list is
re-derived from the new plan rather than blindly resumed in place (whose
baked-in `read_plan.output.milestones` would still reflect the STALE list).

Pure, hermetic, no Conductor/subprocess dependency for the load/hash/derive
functions below — every one of them takes/returns plain data so it is
unit-testable without a workflow run. `load_milestones_from_apply` is the
one exception (a thin subprocess adapter over the real production surface),
kept separate for exactly that reason.

Spec-lifecycle reality check (corrected 2026-07-07 — supersedes this
module's original M7 claim): `spec-lifecycle` main (`4d1f002`, milestone
M3, "execution-handoff") ships `lifecycle apply <change> --format json` —
a real, machine-readable plan surface (`cmd/lifecycle/apply.go`,
`internal/validate/plan.go`). The earlier claim here ("no apply command
exists, verified against the shipped v0.1.0 CLI") was true only of the
`v0.1.0` tag this module was originally pinned against; it was stale by the
time M7 landed. `apply`'s JSON payload is:

    {
      "change": "<id>", "type": "<...>", "issue": "<...>",
      "milestones": [
        {
          "id": 1, "title": "...",
          "steps": [{"text": "...", "tracked": true, "checked": false}],
          "contract": {"check": "...", "criteria": "...", "paths": ["..."]}
        },
        ...
      ]
    }

`id` is an int (the milestone's heading number in `tasks.md`); `contract`
is omitted entirely when a milestone declares no ```contract block. The
`contract` fields (check/criteria/paths) feed the harness's L1 acceptance
check, L3 judge criteria, and diff-confined-paths gate — they MUST survive
`derive_remaining_milestones`' re-derivation untouched, which is why that
function preserves each surviving milestone's whole dict rather than
projecting out just an id/title pair.

`load_milestones_from_apply` (below) is the production adapter over this
real surface. Hermetic tests keep injecting a fixture file (same
`{"milestones": [...]}` shape, minus the `change`/`type`/`issue` envelope
`load_milestones` doesn't need) via `load_milestones`/`write_plan_fixture`
instead of shelling out to a real `lifecycle` binary — see
`orchestration/resume/README.md`'s "spec-lifecycle reality check" for the
full corrected account, including what's still real-but-out-of-scope (a
real `init→validate→approve` round-trip).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


class PlanReadError(ValueError):
    """The plan artifact is missing, malformed, or fails validation."""


def load_milestones(plan_path: str | Path) -> list[dict[str, Any]]:
    """Read a plan fixture JSON's `milestones` array.

    Args:
        plan_path: Path to a JSON file shaped like
            `{"milestones": [{"id": 1, "title": "...", "steps": [...],
            "contract": {...}}, ...]}` — the same per-milestone shape
            `lifecycle apply <change> --format json` emits (see this
            module's header docstring), minus the `change`/`type`/`issue`
            envelope this function doesn't need. This is the same shape
            `execute-change.yaml`'s `read_plan` step reads in the hermetic
            tier; `load_milestones_from_apply` is the production adapter
            over the real `lifecycle apply` surface.

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


def load_milestones_from_apply(
    change: str,
    *,
    cwd: str | Path | None = None,
    lifecycle_bin: str = "lifecycle",
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Fetch `<change>`'s milestones from the real production plan surface.

    Shells out to `lifecycle apply <change> --format json` (spec-lifecycle
    main, milestone M3, `4d1f002` — see this module's header docstring for
    the payload shape) — the real machine-readable plan surface a launcher
    wires against. Hermetic tests should keep injecting a fixture file via
    `load_milestones` instead of calling this; this function is the thin
    adapter, not something to mock through in a unit test.

    Args:
        change: The change id, exactly as passed to `lifecycle apply`.
        cwd: Directory to invoke `lifecycle` from (must contain an
            `openspec/` tree); defaults to the current process's cwd.
        lifecycle_bin: The `lifecycle` executable to invoke.
        timeout: Subprocess timeout, in seconds.

    Returns:
        The `milestones` list from `apply`'s JSON payload, in file order —
        each item real-shaped: `{"id": int, "title": str, "steps": [...],
        "contract": {...} }` (`contract` omitted when the milestone
        declares none — see `internal/validate/plan.go`'s `Milestone`/
        `Contract` types).

    Raises:
        PlanReadError: the subprocess couldn't be started/timed out, exited
            non-zero (exit 1 = refused — `<change>`'s `tasks.md` fails
            plan-stage validation; exit 2 = could not run at all, e.g. an
            unknown change or no `openspec/` tree — see
            `cmd/lifecycle/apply.go`'s exit-code doc comment), or its
            stdout wasn't the expected JSON shape.
    """
    try:
        result = subprocess.run(
            [lifecycle_bin, "apply", change, "--format", "json"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlanReadError(f"could not run `{lifecycle_bin} apply {change}`: {exc}") from exc
    if result.returncode != 0:
        raise PlanReadError(
            f"`{lifecycle_bin} apply {change} --format json` exited "
            f"{result.returncode}: {result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlanReadError(
            f"`{lifecycle_bin} apply {change} --format json` did not print valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("milestones"), list):
        raise PlanReadError(
            f"`{lifecycle_bin} apply {change} --format json` output must be an "
            "object with a 'milestones' list"
        )
    return data["milestones"]


def derive_remaining_milestones(
    new_milestones: list[dict[str, Any]],
    completed_milestone_ids: list[int],
) -> list[dict[str, Any]]:
    """Compute the remaining milestone list after a plan edit.

    Args:
        new_milestones: The (possibly edited) plan's full milestone list, in
            its own order.
        completed_milestone_ids: IDs already completed against the OLD plan
            (see `orchestration.resume.checkpoint.completed_milestone_ids`).

    Returns:
        `new_milestones` filtered down to the ones NOT already completed,
        preserving the NEW plan's order, with each surviving milestone's
        FULL dict untouched (including its `contract`, when present) — the
        harness's L1/L3/diff-confined-paths checks depend on that contract
        surviving re-derivation. Set-based exclusion (by `id`) rather than
        positional/index-based, so it is robust to the human reordering,
        inserting, or removing milestones in the edit — the one invariant
        preserved is "never re-run a milestone whose id already completed
        under the old plan."

        A completed id that no longer appears in `new_milestones` at all
        (the human deleted it) is silently dropped, not an error — its
        work already happened and there is nothing left to schedule.
    """
    completed = set(completed_milestone_ids)
    return [m for m in new_milestones if m.get("id") not in completed]


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
