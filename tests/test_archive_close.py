"""Close-on-archive DoD (M5, github-mirror spec: "Archiving a change closes its
issue"): a successful `archive` hand-off closes the change's issue with a closing
comment; a refused/errored/dry-run archive leaves it open; the close is a
best-effort annotation that NEVER changes the hand-off's own `status` or exit
code.

Hermetic by construction -- neither `lifecycle` nor `gh` is shelled for real. A
single fake `subprocess.run` (the `test_daemon_github_mirror` monkeypatch
pattern) stands in for BOTH: it routes on `argv[0]` -- a `gh …` invocation is
the issue-close mirror (recorded), anything else is the `lifecycle archive`
call. One patch covers both modules because `archive_handoff.subprocess` and
`github_mirror.subprocess` are the same module object. No binaries, no network,
no token.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import orchestration.launch.archive_handoff as ah
from orchestration.launch.archive_handoff import archive, main

_ARCHIVED_STDOUT = json.dumps({"change": "005-close", "records": [{"spec": "x"}]})


def _fake_run_factory(
    gh_calls,
    *,
    lifecycle_rc=0,
    lifecycle_stdout=_ARCHIVED_STDOUT,
    lifecycle_stderr="",
    gh_rc=0,
    gh_stderr="",
):
    def _run(cmd, **_kwargs):
        if cmd and cmd[0] == "gh":
            gh_calls.append(cmd)
            return SimpleNamespace(returncode=gh_rc, stdout="", stderr=gh_stderr)
        return SimpleNamespace(
            returncode=lifecycle_rc, stdout=lifecycle_stdout, stderr=lifecycle_stderr
        )

    return _run


def _live_payload(**over):
    payload = {
        "worktree": ".",
        "change_id": "005-close",
        "dry_run": False,
        "notify_repo": "kentra-io/proj",
        "notify_issue": 42,
        "notify_dry_run": False,
    }
    payload.update(over)
    return payload


def test_archived_closes_the_issue(monkeypatch):
    gh_calls: list = []
    monkeypatch.setattr(ah.subprocess, "run", _fake_run_factory(gh_calls))

    report = archive(_live_payload())

    assert report["status"] == "archived"
    assert report["exit_code"] == 0
    assert report["close_attempted"] is True
    assert report["closed"] is True
    assert report["gh_exit_code"] == 0
    # exactly one `gh issue close` for the right issue, carrying a closing comment.
    assert len(gh_calls) == 1
    cmd = gh_calls[0]
    assert cmd[:5] == ["gh", "issue", "close", "42", "--repo"]
    assert "--comment" in cmd
    body = cmd[cmd.index("--comment") + 1]
    assert "005-close" in body and "archive" in body.lower()


def test_close_failure_is_recorded_but_archive_still_succeeds(monkeypatch):
    gh_calls: list = []
    monkeypatch.setattr(
        ah.subprocess, "run", _fake_run_factory(gh_calls, gh_rc=1, gh_stderr="boom")
    )

    report = archive(_live_payload())

    # Local archive succeeded; the mirror is an annotation -- status/exit unchanged.
    assert report["status"] == "archived"
    assert report["exit_code"] == 0
    assert report["close_attempted"] is True
    assert report["closed"] is False
    assert report["gh_exit_code"] == 1
    assert report["gh_stderr_tail"] == "boom"
    assert len(gh_calls) == 1  # the close was attempted, best-effort


def test_close_failure_still_exits_zero_via_main(monkeypatch, capsys):
    monkeypatch.setattr(ah.subprocess, "run", _fake_run_factory([], gh_rc=1, gh_stderr="boom"))

    code = main([json.dumps(_live_payload())])
    report = json.loads(capsys.readouterr().out)

    assert code == 0  # "archived" -> EXIT_GOOD, a failed close never bumps it
    assert report["status"] == "archived"
    assert report["closed"] is False


def test_refused_never_invokes_gh(monkeypatch):
    gh_calls: list = []
    monkeypatch.setattr(
        ah.subprocess,
        "run",
        _fake_run_factory(gh_calls, lifecycle_rc=1, lifecycle_stderr="tasks not checked"),
    )

    report = archive(_live_payload())

    assert report["status"] == "refused"
    assert report["exit_code"] == 1
    assert report["close_attempted"] is False
    assert report["closed"] is False
    assert gh_calls == []  # a refused archive leaves the issue open -- no `gh` at all


def test_error_never_invokes_gh(monkeypatch):
    gh_calls: list = []
    monkeypatch.setattr(
        ah.subprocess,
        "run",
        _fake_run_factory(gh_calls, lifecycle_rc=2, lifecycle_stderr="could not run"),
    )

    report = archive(_live_payload())

    assert report["status"] == "error"
    assert report["close_attempted"] is False
    assert gh_calls == []


def test_dry_run_default_never_invokes_gh(monkeypatch):
    # No lifecycle call happens under dry_run; assert `gh` is never touched either.
    gh_calls: list = []
    monkeypatch.setattr(ah.subprocess, "run", _fake_run_factory(gh_calls))

    report = archive({"change_id": "005-close"})  # dry_run defaults true

    assert report["status"] == "dry_run"
    assert report["close_attempted"] is False
    assert gh_calls == []


def test_notify_dry_run_true_suppresses_close_even_when_archived(monkeypatch):
    gh_calls: list = []
    monkeypatch.setattr(ah.subprocess, "run", _fake_run_factory(gh_calls))

    report = archive(_live_payload(notify_dry_run=True))

    assert report["status"] == "archived"
    assert report["close_attempted"] is False
    assert gh_calls == []


def test_missing_repo_or_issue_suppresses_close(monkeypatch):
    gh_calls: list = []
    monkeypatch.setattr(ah.subprocess, "run", _fake_run_factory(gh_calls))

    no_repo = archive(_live_payload(notify_repo=""))
    no_issue = archive(_live_payload(notify_issue=None))

    assert no_repo["status"] == "archived" and no_repo["close_attempted"] is False
    assert no_issue["status"] == "archived" and no_issue["close_attempted"] is False
    assert gh_calls == []
