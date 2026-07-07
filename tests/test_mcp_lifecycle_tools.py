"""Unit tests for `orchestration.mcp.lifecycle_tools` — mocked `subprocess.run`
(no real `lifecycle` binary needed): every real verb/flag combination this
module maps onto is asserted exactly against the verified v0.1.0 CLI shape
(see the module's own docstring table)."""

from __future__ import annotations

import json
from typing import Any

from orchestration.mcp.lifecycle_tools import (
    LifecycleCLIError,
    archive_change,
    get_state,
    record_approval,
    run_guard,
    validate_stage,
)


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _capture_args(monkeypatch, result: _FakeCompletedProcess) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: Any) -> _FakeCompletedProcess:
        calls.append(args)
        return result

    monkeypatch.setattr("orchestration.mcp.lifecycle_tools.subprocess.run", fake_run)
    return calls


class TestGetState:
    def test_no_change_scopes_to_repo_wide_status(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, json.dumps({"changes": []})))
        result = get_state()
        assert calls == [["lifecycle", "status", "--format", "json"]]
        assert result == {
            "exit_code": 0,
            "stdout": '{"changes": []}',
            "stderr": "",
            "json": {"changes": []},
        }

    def test_with_change_adds_the_flag(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, "{}"))
        get_state("042-a")
        assert calls == [["lifecycle", "status", "--change", "042-a", "--format", "json"]]

    def test_non_json_stdout_yields_json_none(self, monkeypatch) -> None:
        _capture_args(monkeypatch, _FakeCompletedProcess(2, "not json at all"))
        result = get_state()
        assert result["json"] is None
        assert result["exit_code"] == 2

    def test_binary_not_found_raises(self, monkeypatch) -> None:
        def fake_run(*_a: Any, **_k: Any):
            raise FileNotFoundError()

        monkeypatch.setattr("orchestration.mcp.lifecycle_tools.subprocess.run", fake_run)
        try:
            get_state()
            raise AssertionError("expected LifecycleCLIError")
        except LifecycleCLIError:
            pass


class TestValidateStage:
    def test_stage_and_change(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, "{}"))
        validate_stage("plan", "042-a")
        assert calls == [
            ["lifecycle", "validate", "--stage", "plan", "--change", "042-a", "--format", "json"]
        ]

    def test_stage_only(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, "{}"))
        validate_stage("refine")
        assert calls == [["lifecycle", "validate", "--stage", "refine", "--format", "json"]]


class TestRecordApproval:
    def test_approve_passes_the_approve_flag_unconditionally(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, ""))
        record_approval("042-a", "plan", approved_by="jan", notes="looks good")
        assert calls == [
            [
                "lifecycle",
                "approve",
                "042-a",
                "--stage",
                "plan",
                "--approve",
                "--approved-by",
                "jan",
                "--notes",
                "looks good",
            ]
        ]

    def test_reject_passes_reject_not_approve(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(1, ""))
        record_approval("042-a", "design", reject=True)
        assert calls == [["lifecycle", "approve", "042-a", "--stage", "design", "--reject"]]

    def test_design_skip(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, ""))
        record_approval("042-a", "design", design_skip=True)
        assert calls == [
            ["lifecycle", "approve", "042-a", "--stage", "design", "--approve", "--design-skip"]
        ]

    def test_approve_has_no_json_output_by_design(self, monkeypatch) -> None:
        """`approve` has no `--format json` in the real CLI (verified) --
        plain text stdout must not accidentally parse as JSON."""
        _capture_args(monkeypatch, _FakeCompletedProcess(0, "Approved refine for 042-a\n"))
        result = record_approval("042-a", "refine")
        assert result["json"] is None


class TestArchiveChange:
    def test_default_flags(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, "{}"))
        archive_change("042-a")
        assert calls == [["lifecycle", "archive", "042-a", "--format", "json"]]

    def test_force_flags(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, "{}"))
        archive_change("042-a", force_gates=True, force_conflicts=True)
        assert calls == [
            [
                "lifecycle",
                "archive",
                "042-a",
                "--force-gates",
                "--force-conflicts",
                "--format",
                "json",
            ]
        ]


class TestRunGuard:
    def test_no_args_beyond_format(self, monkeypatch) -> None:
        calls = _capture_args(monkeypatch, _FakeCompletedProcess(0, '{"findings": []}'))
        result = run_guard()
        assert calls == [["lifecycle", "guard", "--format", "json"]]
        assert result["json"] == {"findings": []}
