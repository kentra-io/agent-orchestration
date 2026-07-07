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


def _write_plan(path: Path, milestone_ids: list[int]) -> Path:
    path.write_text(
        json.dumps({"milestones": [{"id": mid, "title": f"do M{mid}"} for mid in milestone_ids]}),
        encoding="utf-8",
    )
    return path


class TestLoadMilestones:
    def test_reads_milestones_in_file_order(self, tmp_path: Path) -> None:
        plan_path = _write_plan(tmp_path / "plan.json", [1, 2, 3])
        milestones = load_milestones(plan_path)
        assert [m["id"] for m in milestones] == [1, 2, 3]

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
        a = _write_plan(tmp_path / "a.json", [1, 2])
        b = tmp_path / "b.json"
        b.write_bytes(a.read_bytes())
        assert hash_plan(a) == hash_plan(b)

    def test_different_bytes_hash_differently(self, tmp_path: Path) -> None:
        a = _write_plan(tmp_path / "a.json", [1, 2])
        b = _write_plan(tmp_path / "b.json", [1, 2, 3])
        assert hash_plan(a) != hash_plan(b)

    def test_hash_has_sha256_prefix(self, tmp_path: Path) -> None:
        a = _write_plan(tmp_path / "a.json", [1])
        assert hash_plan(a).startswith("sha256:")


class TestDeriveRemainingMilestones:
    def test_excludes_completed_ids_preserving_new_order(self) -> None:
        new_plan = [
            {"id": 1, "title": "one"},
            {"id": 2, "title": "two (edited)"},
            {"id": 3, "title": "three"},
        ]
        remaining = derive_remaining_milestones(new_plan, completed_milestone_ids=[1])
        assert [m["id"] for m in remaining] == [2, 3]
        # The edited title for the not-yet-run milestone 2 is preserved verbatim.
        assert remaining[0]["title"] == "two (edited)"

    def test_contract_survives_re_derivation_untouched(self) -> None:
        """The harness's L1/L3/diff-confined-paths checks depend on a
        surviving milestone's `contract` making it through re-derivation
        byte-for-byte -- `derive_remaining_milestones` must preserve the
        whole dict, not project out just id/title.
        """
        contract = {
            "check": "go test ./internal/foo/...",
            "criteria": "all foo tests pass",
            "paths": ["internal/foo/**"],
        }
        new_plan = [
            {"id": 1, "title": "one"},
            {
                "id": 2,
                "title": "two",
                "steps": [{"text": "s", "tracked": True, "checked": False}],
                "contract": contract,
            },
        ]
        remaining = derive_remaining_milestones(new_plan, completed_milestone_ids=[1])
        assert remaining == [new_plan[1]]
        assert remaining[0]["contract"] == contract

    def test_reordering_and_insertion_is_robust_by_id_not_position(self) -> None:
        """The human inserted a new milestone (id 4) and reordered -- id 1
        (already done) is still excluded regardless of its new position.
        """
        new_plan = [
            {"id": 3, "title": "three"},
            {"id": 4, "title": "new one"},
            {"id": 1, "title": "one"},
            {"id": 2, "title": "two"},
        ]
        remaining = derive_remaining_milestones(new_plan, completed_milestone_ids=[1])
        assert [m["id"] for m in remaining] == [3, 4, 2]

    def test_deleted_completed_milestone_is_silently_dropped_not_an_error(self) -> None:
        new_plan = [{"id": 2, "title": "two"}]
        remaining = derive_remaining_milestones(new_plan, completed_milestone_ids=[1, 2])
        assert remaining == []

    def test_no_completed_ids_returns_everything(self) -> None:
        new_plan = [{"id": 1, "title": "one"}]
        assert derive_remaining_milestones(new_plan, completed_milestone_ids=[]) == new_plan


class TestWritePlanFixtureRoundTrips:
    def test_write_then_load_round_trips(self, tmp_path: Path) -> None:
        milestones = [{"id": 9, "title": "s"}]
        dest = write_plan_fixture(tmp_path / "nested" / "remaining.json", milestones)
        assert load_milestones(dest) == milestones
