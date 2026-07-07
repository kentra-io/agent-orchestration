"""Detecting "a human resolved the escalation" from `lifecycle status --format json`.

**Deviation from the M7 brief, recorded up front (see `README.md`'s
"spec-lifecycle reality check" for the full, corrected verification):** the
brief asks this module to "detect a change that has left `Needs human
input`" by polling `lifecycle status --format json`. Verified against
`spec-lifecycle`'s real `status` surface (`internal/status/status.go`,
main `4d1f002`) — `status`'s `StageState` enum is exactly `pending |
approved | rejected | skipped` — **`spec-lifecycle` has no "needs human
input" concept at all**, and per `orchestration.md` sec 7.1 / the
implementation plan's P7, this is deliberate: "Canonical = Conductor
run-state ... spec-lifecycle is untouched — its statuses are gates, not run
states." (`spec-lifecycle` main separately ships `lifecycle apply` too, as
of milestone M3 — see `orchestration/resume/plan.py`'s header docstring —
but `apply` is the machine-readable plan surface, not a new gate/run-state
value; it does not change anything about `status`'s state enum or this
module's reasoning below.)

So this module does NOT (and structurally cannot) watch `lifecycle status`
for an escalation *signal* — that signal is Conductor's own paused run
(see `orchestration.resume.checkpoint`). What it DOES watch `lifecycle
status --format json` for is the **resolution** half of sec 7.2: "a human
resolves in a Mode-A session [by editing the plan] ... then approves."
Concretely: the change's `plan` stage gate carries `state` and (per the
real, verified JSON shape) `approvedAt`/`drifted`. A human resolving an
escalation edits `tasks.md` (which flips the gate's `drifted` list
non-empty, if `spec-lifecycle` detects it) and re-runs `lifecycle approve
<change> --stage plan`, which stamps a fresh `approvedAt`. **"Resolved" =
the `plan` gate is `approved` again with a `approvedAt` newer than the
baseline captured when the run escalated.** This uses only real, shipped
`spec-lifecycle` surfaces — no invented status value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class LifecycleStatusError(ValueError):
    """The status JSON doesn't have the shape this module expects."""


@dataclass(frozen=True)
class GateSnapshot:
    """The one gate (stage) this module tracks, at a point in time."""

    change: str
    stage: str
    state: str
    """One of `spec-lifecycle`'s real `StageState` values: `pending`,
    `approved`, `rejected`, `skipped`."""
    approved_at: str | None
    """`approvedAt` from the real JSON shape (ISO-8601), or `None` if the
    gate has never been approved."""


def extract_gate(
    status_json: dict[str, Any],
    change: str,
    stage: str = "plan",
) -> GateSnapshot | None:
    """Pull one change's one gate out of a real `lifecycle status --format json` payload.

    Args:
        status_json: The parsed `{"changes": [{"change": ..., "gates": [...]}]}`
            payload (real shape, verified against `internal/status/status.go`).
        change: The change id to look up (matches the `"change"` field).
        stage: The stage/gate name to look up (matches a gate's `"stage"`
            field) — `"plan"` by default, since that's the gate whose
            re-approval signals "a human resolved the escalation" (sec 7.2:
            the human edits the plan artifact, then re-approves it).

    Returns:
        A `GateSnapshot`, or `None` if the change or stage isn't present
        (e.g. the change hasn't been created yet, or hasn't reached that
        stage) — a `None` baseline is a legitimate "nothing to compare
        against yet", not an error.

    Raises:
        LifecycleStatusError: `status_json` doesn't have the expected
            top-level shape at all (not a dict, or no `"changes"` list) —
            this IS an error, distinct from "the specific change/stage is
            absent".
    """
    if not isinstance(status_json, dict) or not isinstance(status_json.get("changes"), list):
        raise LifecycleStatusError(
            "status JSON must be an object with a 'changes' list "
            "(the real `lifecycle status --format json` shape)"
        )
    for change_entry in status_json["changes"]:
        if change_entry.get("change") != change:
            continue
        for gate in change_entry.get("gates", []):
            if gate.get("stage") == stage:
                return GateSnapshot(
                    change=change,
                    stage=stage,
                    state=gate.get("state", "pending"),
                    approved_at=gate.get("approvedAt"),
                )
        return None  # change found, but this stage's gate isn't in its list yet
    return None  # change not present at all


def is_resolved(baseline: GateSnapshot | None, current: GateSnapshot | None) -> bool:
    """True when `current` reflects a FRESH re-approval since `baseline`.

    "Fresh" means: `current.state == "approved"` AND its `approved_at` is
    a real timestamp that differs from `baseline`'s (or `baseline` had none
    at all, e.g. captured before the plan was ever approved — shouldn't
    normally happen since execution only starts after plan-approval, but
    handled rather than assumed).

    Deliberately does NOT key off `state` alone: the gate's `state` was
    already `"approved"` when the escalated run started (that's how
    execution began in the first place) and does not flip to anything else
    merely because a milestone's ladder is exhausted (`spec-lifecycle` has
    no view into Conductor's run state at all, per this module's header) —
    so a stale, unchanged `"approved"` must NOT read as "resolved", or the
    watcher would fire immediately on every poll before the human has done
    anything. A monotonically-newer `approved_at` is the one honest signal
    that a NEW `lifecycle approve` call happened since escalation.
    """
    if current is None or current.state != "approved" or not current.approved_at:
        return False
    if baseline is None or not baseline.approved_at:
        # No prior approval to compare against -- any real approval now is new.
        return True
    return current.approved_at != baseline.approved_at
