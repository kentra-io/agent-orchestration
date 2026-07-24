"""Unit tests for `orchestration.daemon.github_mirror` — the daemon's gh client
and lifecycle notifier. Fake-gh via per-module `monkeypatch.setattr(mod.subprocess,
"run", ...)` (the `test_launch_notify_escalation` pattern); the notifier tests use
a hermetic `ORCHESTRATION_REGISTRY_DIR` and monkeypatch the client functions.
"""

from __future__ import annotations

from types import SimpleNamespace

import orchestration.daemon.github_mirror as gm
from orchestration.obs import registry


def _fake_run_factory(calls, *, returncode=0, stdout="", stderr=""):
    def _run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return _run


# --- the raw gh client ------------------------------------------------------


def test_comment_shells_gh_api_post(monkeypatch):
    calls: list = []
    monkeypatch.setattr(gm.subprocess, "run", _fake_run_factory(calls))
    result = gm.comment("kentra-io/x", 42, "hello")
    assert result["ok"] is True and result["gh_exit_code"] == 0
    assert calls == [
        ["gh", "api", "-X", "POST", "repos/kentra-io/x/issues/42/comments", "-f", "body=hello"]
    ]


def test_add_label_shells_gh_issue_edit(monkeypatch):
    calls: list = []
    monkeypatch.setattr(gm.subprocess, "run", _fake_run_factory(calls))
    result = gm.add_label("kentra-io/x", 42, "run-died")
    assert result["ok"] is True and result["label"] == "run-died"
    assert calls == [
        ["gh", "issue", "edit", "42", "--repo", "kentra-io/x", "--add-label", "run-died"]
    ]


def test_close_issue_shells_gh_issue_close(monkeypatch):
    calls: list = []
    monkeypatch.setattr(gm.subprocess, "run", _fake_run_factory(calls))
    result = gm.close_issue("kentra-io/x", 42, "done")
    assert result["ok"] is True
    assert calls == [["gh", "issue", "close", "42", "--repo", "kentra-io/x", "--comment", "done"]]


def test_ensure_label_caches_per_process(monkeypatch):
    gm._ensured_labels.clear()
    calls: list = []
    monkeypatch.setattr(gm.subprocess, "run", _fake_run_factory(calls))
    first = gm.ensure_label("kentra-io/x", "run-died")
    second = gm.ensure_label("kentra-io/x", "run-died")
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1  # the second call is served from the process cache
    gm._ensured_labels.clear()


def test_client_failure_is_reported_not_raised(monkeypatch):
    calls: list = []
    monkeypatch.setattr(gm.subprocess, "run", _fake_run_factory(calls, returncode=1, stderr="boom"))
    result = gm.comment("kentra-io/x", 42, "hi")
    assert result["ok"] is False
    assert result["gh_exit_code"] == 1 and result["gh_stderr_tail"] == "boom"


def test_missing_gh_binary_never_raises(monkeypatch):
    def _raise(cmd, **_kwargs):
        raise OSError("gh not found")

    monkeypatch.setattr(gm.subprocess, "run", _raise)
    result = gm.comment("kentra-io/x", 42, "hi")
    assert result["ok"] is False and result["gh_exit_code"] is None


# --- the notifier -----------------------------------------------------------


def _entry(tmp_path, *, repo_gh="kentra-io/proj", issue=7, change_id="1-a"):
    e = registry.new_entry(
        repo="/r/proj",
        change_id=change_id,
        worktree=str(tmp_path),
        branch="change/1-a",
        box="box",
        tmpdir=str(tmp_path),
        issue=issue,
        repo_gh=repo_gh,
    )
    registry.write_entry(e)
    registry.append_incarnation(
        "proj",
        change_id,
        {"pid": 1, "started_at": "x", "web_port": None, "exit_code": None, "classified": None},
    )
    return registry.load_entry("proj", change_id)


def _install_fakes(monkeypatch):
    calls = {"comment": [], "add_label": [], "ensure_label": []}

    def _comment(repo, issue, body):
        calls["comment"].append((repo, issue, body))
        return {"ok": True, "gh_exit_code": 0, "gh_stderr_tail": None}

    def _add_label(repo, issue, label):
        calls["add_label"].append((repo, issue, label))
        return {"ok": True, "gh_exit_code": 0, "gh_stderr_tail": None, "label": label}

    def _ensure_label(repo, label):
        calls["ensure_label"].append((repo, label))
        return {"ok": True, "cached": False, "gh_exit_code": 0, "gh_stderr_tail": None}

    monkeypatch.setattr(gm, "comment", _comment)
    monkeypatch.setattr(gm, "add_label", _add_label)
    monkeypatch.setattr(gm, "ensure_label", _ensure_label)
    return calls


def test_mirror_started_posts_once_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    entry = _entry(tmp_path)
    calls = _install_fakes(monkeypatch)

    gm.mirror_started(entry)
    assert len(calls["comment"]) == 1
    assert "started" in calls["comment"][0][2]

    loaded = registry.load_entry("proj", "1-a")
    assert loaded["incarnations"][-1]["mirror"]["started"] is True

    # A restarted daemon re-adopting the same incarnation must not double-post.
    gm.mirror_started(loaded)
    assert len(calls["comment"]) == 1


def test_mirror_started_resumed_variant(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    entry = _entry(tmp_path)
    calls = _install_fakes(monkeypatch)
    gm.mirror_started(entry, resumed=True)
    assert "resumed" in calls["comment"][0][2]


def test_mirror_skips_without_repo_gh_or_issue(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    entry = _entry(tmp_path, repo_gh=None, issue=None)
    calls = _install_fakes(monkeypatch)
    gm.mirror_started(entry)
    gm.mirror_terminal(entry, {"slug": "proj", "change_id": "1-a", "classified": "success"})
    assert calls["comment"] == []  # hermetic entries never shell gh


def test_mirror_terminal_success_posts_finished_no_label(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    entry = _entry(tmp_path)
    calls = _install_fakes(monkeypatch)
    gm.mirror_terminal(
        entry,
        {"slug": "proj", "change_id": "1-a", "classified": "success", "remedy": None, "detail": ""},
    )
    assert len(calls["comment"]) == 1 and "finished" in calls["comment"][0][2]
    assert calls["add_label"] == []
    loaded = registry.load_entry("proj", "1-a")
    assert loaded["incarnations"][-1]["mirror"]["terminal"] is True


def test_mirror_terminal_death_labels_run_died_with_real_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    entry = _entry(tmp_path)
    calls = _install_fakes(monkeypatch)
    gm.mirror_terminal(
        entry,
        {
            "slug": "proj",
            "change_id": "1-a",
            "classified": "oauth-expired",
            "remedy": "run `cb login` from the worktree, then resume",
            "detail": "OAuth token could not be refreshed\nthe real captured error",
        },
    )
    # Label taxonomy: the APPLIED label is run-died, never needs-human-input.
    assert calls["ensure_label"] == [("kentra-io/proj", "run-died")]
    assert calls["add_label"] == [("kentra-io/proj", 7, "run-died")]
    assert all(lbl != "needs-human-input" for (_repo, _issue, lbl) in calls["add_label"])
    body = calls["comment"][0][2]
    assert "oauth-expired" in body  # the classified cause
    assert "cb login" in body  # the remedy
    assert "the real captured error" in body  # verdict.detail, not a masked exit


def test_mirror_terminal_gate_pause_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    entry = _entry(tmp_path)
    calls = _install_fakes(monkeypatch)
    gm.mirror_terminal(
        entry, {"slug": "proj", "change_id": "1-a", "classified": "gate-pause", "detail": "EOF"}
    )
    assert calls["comment"] == [] and calls["add_label"] == []
    loaded = registry.load_entry("proj", "1-a")
    # No write happened, so no terminal fact is recorded.
    assert "terminal" not in (loaded["incarnations"][-1].get("mirror") or {})


def test_mirror_terminal_dedupes_across_reconcile_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    entry = _entry(tmp_path)
    calls = _install_fakes(monkeypatch)
    event = {
        "slug": "proj",
        "change_id": "1-a",
        "classified": "api-transient",
        "remedy": "resume",
        "detail": "API Error",
    }
    gm.mirror_terminal(entry, event)
    gm.mirror_terminal(registry.load_entry("proj", "1-a"), event)  # a later reconcile pass
    assert len(calls["comment"]) == 1 and len(calls["add_label"]) == 1
