"""Unit tests for `orchestration.resume.plan` — pure, no subprocess/Conductor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestration.resume.plan import (
    PlanReadError,
    derive_remaining_milestones,
    hash_plan,
    load_milestones,
    write_plan_fixture,
)


def _write_plan(path: Path, milestone_ids: list[str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "milestones": [
                    {"milestone_id": mid, "milestone_summary": f"do {mid}"} for mid in milestone_ids
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


class TestLoadMilestones:
    def test_reads_milestones_in_file_order(self, tmp_path: Path) -> None:
        plan_path = _write_plan(tmp_path / "plan.json", ["M1", "M2", "M3"])
        milestones = load_milestones(plan_path)
        assert [m["milestone_id"] for m in milestones] == ["M1", "M2", "M3"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PlanReadError, match="cannot read"):
            load_milestones(tmp_path / "nope.json")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(PlanReadError, match="not valid JSON"):
            load_milestones(path)

    def test_missing_milestones_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"nope": []}), encoding="utf-8")
        with pytest.raises(PlanReadError, match="milestones"):
            load_milestones(path)


class TestHashPlan:
    def test_identical_bytes_hash_identically(self, tmp_path: Path) -> None:
        a = _write_plan(tmp_path / "a.json", ["M1", "M2"])
        b = tmp_path / "b.json"
        b.write_bytes(a.read_bytes())
        assert hash_plan(a) == hash_plan(b)

    def test_different_bytes_hash_differently(self, tmp_path: Path) -> None:
        a = _write_plan(tmp_path / "a.json", ["M1", "M2"])
        b = _write_plan(tmp_path / "b.json", ["M1", "M2", "M3"])
        assert hash_plan(a) != hash_plan(b)

    def test_hash_has_sha256_prefix(self, tmp_path: Path) -> None:
        a = _write_plan(tmp_path / "a.json", ["M1"])
        assert hash_plan(a).startswith("sha256:")


class TestDeriveRemainingMilestones:
    def test_excludes_completed_ids_preserving_new_order(self) -> None:
        new_plan = [
            {"milestone_id": "M1", "milestone_summary": "one"},
            {"milestone_id": "M2", "milestone_summary": "two (edited)"},
            {"milestone_id": "M3", "milestone_summary": "three"},
        ]
        remaining = derive_remaining_milestones(new_plan, completed_milestone_ids=["M1"])
        assert [m["milestone_id"] for m in remaining] == ["M2", "M3"]
        # The edited summary for the not-yet-run M2 is preserved verbatim.
        assert remaining[0]["milestone_summary"] == "two (edited)"

    def test_reordering_and_insertion_is_robust_by_id_not_position(self) -> None:
        """The human inserted a new milestone M1b and reordered -- M1
        (already done) is still excluded regardless of its new position.
        """
        new_plan = [
            {"milestone_id": "M3", "milestone_summary": "three"},
            {"milestone_id": "M1b", "milestone_summary": "new one"},
            {"milestone_id": "M1", "milestone_summary": "one"},
            {"milestone_id": "M2", "milestone_summary": "two"},
        ]
        remaining = derive_remaining_milestones(new_plan, completed_milestone_ids=["M1"])
        assert [m["milestone_id"] for m in remaining] == ["M3", "M1b", "M2"]

    def test_deleted_completed_milestone_is_silently_dropped_not_an_error(self) -> None:
        new_plan = [{"milestone_id": "M2", "milestone_summary": "two"}]
        remaining = derive_remaining_milestones(new_plan, completed_milestone_ids=["M1", "M2"])
        assert remaining == []

    def test_no_completed_ids_returns_everything(self) -> None:
        new_plan = [{"milestone_id": "M1", "milestone_summary": "one"}]
        assert derive_remaining_milestones(new_plan, completed_milestone_ids=[]) == new_plan


class TestWritePlanFixtureRoundTrips:
    def test_write_then_load_round_trips(self, tmp_path: Path) -> None:
        milestones = [{"milestone_id": "M9", "milestone_summary": "s"}]
        dest = write_plan_fixture(tmp_path / "nested" / "remaining.json", milestones)
        assert load_milestones(dest) == milestones
