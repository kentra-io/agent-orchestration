"""Unit tests for `orchestration.resume.lifecycle_status` — pure, fabricated
`lifecycle status --format json` payloads (the real shape, verified against
`spec-lifecycle`'s `internal/status/status.go` — see this module's and the
package README's "spec-lifecycle reality check")."""

from __future__ import annotations

import pytest

from orchestration.resume.lifecycle_status import (
    GateSnapshot,
    LifecycleStatusError,
    extract_gate,
    is_resolved,
)


def _status(changes: list[dict]) -> dict:
    return {"changes": changes}


def _change(change: str, gates: list[dict]) -> dict:
    return {"change": change, "type": "feature", "issue": None, "gates": gates}


def _gate(stage: str, state: str, approved_at: str | None = None) -> dict:
    g = {"stage": stage, "state": state}
    if approved_at is not None:
        g["approvedAt"] = approved_at
    return g


class TestExtractGate:
    def test_finds_the_matching_change_and_stage(self) -> None:
        payload = _status(
            [
                _change("042-a", [_gate("plan", "approved", "2026-07-01T00:00:00Z")]),
                _change("043-b", [_gate("plan", "pending")]),
            ]
        )
        gate = extract_gate(payload, "042-a", "plan")
        assert gate == GateSnapshot(
            change="042-a", stage="plan", state="approved", approved_at="2026-07-01T00:00:00Z"
        )

    def test_missing_change_returns_none(self) -> None:
        payload = _status([_change("042-a", [_gate("plan", "approved")])])
        assert extract_gate(payload, "999-missing", "plan") is None

    def test_missing_stage_on_present_change_returns_none(self) -> None:
        payload = _status([_change("042-a", [_gate("refine", "approved")])])
        assert extract_gate(payload, "042-a", "plan") is None

    def test_gate_with_no_approved_at_yet(self) -> None:
        payload = _status([_change("042-a", [_gate("plan", "pending")])])
        gate = extract_gate(payload, "042-a", "plan")
        assert gate is not None
        assert gate.approved_at is None

    def test_malformed_payload_raises(self) -> None:
        with pytest.raises(LifecycleStatusError):
            extract_gate({"nope": "not a real shape"}, "042-a", "plan")
        with pytest.raises(LifecycleStatusError):
            extract_gate("not even a dict", "042-a", "plan")  # type: ignore[arg-type]


class TestIsResolved:
    def test_unchanged_approved_at_is_not_resolved(self) -> None:
        """The gate was already 'approved' when execution started -- a
        stale, unchanged approval must NOT read as 'the human just fixed
        this', or the watcher would fire on the very first poll.
        """
        baseline = GateSnapshot("c", "plan", "approved", "2026-07-01T00:00:00Z")
        current = GateSnapshot("c", "plan", "approved", "2026-07-01T00:00:00Z")
        assert is_resolved(baseline, current) is False

    def test_fresh_approved_at_is_resolved(self) -> None:
        baseline = GateSnapshot("c", "plan", "approved", "2026-07-01T00:00:00Z")
        current = GateSnapshot("c", "plan", "approved", "2026-07-05T12:00:00Z")
        assert is_resolved(baseline, current) is True

    def test_still_pending_is_not_resolved(self) -> None:
        baseline = GateSnapshot("c", "plan", "approved", "2026-07-01T00:00:00Z")
        current = GateSnapshot("c", "plan", "pending", None)
        assert is_resolved(baseline, current) is False

    def test_current_none_is_not_resolved(self) -> None:
        baseline = GateSnapshot("c", "plan", "approved", "2026-07-01T00:00:00Z")
        assert is_resolved(baseline, None) is False

    def test_no_baseline_approval_any_real_approval_is_resolved(self) -> None:
        baseline = GateSnapshot("c", "plan", "pending", None)
        current = GateSnapshot("c", "plan", "approved", "2026-07-05T12:00:00Z")
        assert is_resolved(baseline, current) is True

    def test_baseline_none_any_real_approval_is_resolved(self) -> None:
        current = GateSnapshot("c", "plan", "approved", "2026-07-05T12:00:00Z")
        assert is_resolved(None, current) is True

    def test_rejected_state_is_not_resolved(self) -> None:
        baseline = GateSnapshot("c", "plan", "approved", "2026-07-01T00:00:00Z")
        current = GateSnapshot("c", "plan", "rejected", "2026-07-05T12:00:00Z")
        assert is_resolved(baseline, current) is False
