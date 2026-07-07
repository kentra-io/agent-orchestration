"""Unit tests for `orchestration.launch.notify_escalation` -- the `escalate`
step's GitHub-label mirror (P7). See M5's DoD: the hermetic tier must
exercise this without a real `gh` call or GitHub token.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from orchestration.launch.notify_escalation import NotifyInputError, notify


def test_dry_run_default_never_shells_out() -> None:
    verdict = notify({"label": "needs-human-input"})
    assert verdict == {
        "notified": True,
        "label": "needs-human-input",
        "mode": "dry_run",
        "gh_exit_code": None,
        "gh_stderr_tail": None,
    }


def test_dry_run_explicit_true_same_as_default() -> None:
    verdict = notify({"label": "needs-human-input", "dry_run": True})
    assert verdict["mode"] == "dry_run"
    assert verdict["notified"] is True


def test_missing_label_raises() -> None:
    with pytest.raises(NotifyInputError, match="label"):
        notify({})


def test_live_mode_requires_repo_and_issue() -> None:
    with pytest.raises(NotifyInputError, match="repo"):
        notify({"label": "needs-human-input", "dry_run": False})
    with pytest.raises(NotifyInputError, match="issue"):
        notify({"label": "needs-human-input", "dry_run": False, "repo": "kentra-io/x"})


def test_live_mode_shells_out_to_gh(monkeypatch) -> None:
    """Confirm the `gh` invocation shape without actually requiring `gh` to
    be installed -- monkeypatch `subprocess.run` to a fake that records the
    command and returns a canned CompletedProcess.
    """
    calls = []

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompleted()

    import orchestration.launch.notify_escalation as mod

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    verdict = notify(
        {"label": "needs-human-input", "dry_run": False, "repo": "kentra-io/x", "issue": 42}
    )
    assert verdict["mode"] == "gh"
    assert verdict["notified"] is True
    assert verdict["gh_exit_code"] == 0
    assert calls == [
        ["gh", "issue", "edit", "42", "--repo", "kentra-io/x", "--add-label", "needs-human-input"]
    ]


def test_cli_exit_codes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestration.launch.notify_escalation",
            '{"label": "needs-human-input"}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["notified"] is True

    result = subprocess.run(
        [sys.executable, "-m", "orchestration.launch.notify_escalation", "{}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "error" in json.loads(result.stdout)
