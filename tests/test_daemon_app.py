import os

from fastapi.testclient import TestClient

import orchestration.daemon.app as app_mod
from orchestration.daemon.app import create_app
from orchestration.daemon.supervise import Supervisor
from orchestration.obs import registry


def _client(token=None):
    return TestClient(create_app(Supervisor(), token=token))


def test_runs_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    assert _client().get("/runs").json() == {"runs": []}


def test_runs_returns_entry_with_derived_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    e = registry.new_entry(
        repo="r",
        change_id="1-a",
        worktree=str(tmp_path),
        branch="b",
        box=None,
        tmpdir=str(tmp_path),
    )
    registry.write_entry(e)
    runs = _client().get("/runs").json()["runs"]
    assert runs[0]["change_id"] == "1-a" and runs[0]["derived"]["state"] == "registered"


def test_launch_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    c = _client(token="sekrit")
    assert c.post("/launch", json={}).status_code == 401
    assert c.post("/launch", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_launch_calls_launcher_and_adopts(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_WEB_PORT_RANGE", "42020-42030")
    seen = {}

    def fake_launch(payload, proc_holder=None):
        seen["payload"] = payload
        return {"pid": 4242, "worktree": "w"}

    monkeypatch.setattr(app_mod, "_launch_fn", fake_launch)
    c = _client(token="sekrit")
    resp = c.post(
        "/launch",
        json={"repo": "/r", "change_id": "1-a"},
        headers={"Authorization": "Bearer sekrit"},
    )
    assert resp.status_code == 200 and resp.json()["report"]["pid"] == 4242
    assert seen["payload"]["conductor"]["web"] is True
    assert 42020 <= seen["payload"]["conductor"]["web_port"] <= 42030


AUTH = {"Authorization": "Bearer sekrit"}


def _dead_entry(tmp_path, change_id="1-a"):
    e = registry.new_entry(
        repo="/r/proj",
        change_id=change_id,
        worktree=str(tmp_path),
        branch="b",
        box=None,
        tmpdir=str(tmp_path),
    )
    e["incarnations"].append(
        {
            "pid": 1,
            "started_at": "x",
            "web_port": 42001,
            "dashboard_url": "http://localhost:42001",
            "exit_code": 1,
            "classified": "run-died",
        }
    )
    registry.write_entry(e)
    return e


def test_resume_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    c = _client(token="sekrit")
    assert c.post("/resume", json={}).status_code == 401


def test_resume_404_when_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    c = _client(token="sekrit")
    r = c.post("/resume", json={"repo": "/r/proj", "change_id": "9-x"}, headers=AUTH)
    assert r.status_code == 404


def test_resume_409_when_running(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    e = registry.new_entry(
        repo="/r/proj",
        change_id="1-live",
        worktree=str(tmp_path),
        branch="b",
        box=None,
        tmpdir=str(tmp_path),
    )
    e["incarnations"].append(
        {
            "pid": os.getpid(),
            "started_at": "x",
            "web_port": 42001,
            "dashboard_url": "http://localhost:42001",
            "exit_code": None,
            "classified": None,
        }
    )
    registry.write_entry(e)
    c = _client(token="sekrit")
    r = c.post("/resume", json={"repo": "/r/proj", "change_id": "1-live"}, headers=AUTH)
    assert r.status_code == 409


def test_resume_calls_resumer_and_adopts(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_WEB_PORT_RANGE", "42020-42030")
    _dead_entry(tmp_path)
    seen = {}

    def fake_resume(entry, *, web_port, proc_holder=None):
        seen["entry"] = entry
        seen["web_port"] = web_port
        return {"pid": 777, "mode": "resume-in-place"}

    monkeypatch.setattr(app_mod, "_resume_fn", fake_resume)
    c = _client(token="sekrit")
    r = c.post("/resume", json={"repo": "/r/proj", "change_id": "1-a"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["report"]["pid"] == 777
    assert seen["entry"]["change_id"] == "1-a"
    assert 42020 <= seen["web_port"] <= 42030


def _mirrorable_entry(tmp_path, change_id="1-a"):
    e = registry.new_entry(
        repo="/r/proj",
        change_id=change_id,
        worktree=str(tmp_path),
        branch="change/1-a",
        box="box",
        tmpdir=str(tmp_path),
        issue=7,
        repo_gh="kentra-io/proj",
    )
    registry.write_entry(e)
    registry.append_incarnation(
        "proj",
        change_id,
        {"pid": 1, "started_at": "x", "web_port": None, "exit_code": None, "classified": None},
    )


def _install_fake_client(monkeypatch):
    calls = {"comment": [], "add_label": [], "ensure_label": []}
    monkeypatch.setattr(
        app_mod.github_mirror,
        "comment",
        lambda repo, issue, body: calls["comment"].append(body) or {"ok": True},
    )
    monkeypatch.setattr(
        app_mod.github_mirror,
        "add_label",
        lambda repo, issue, label: calls["add_label"].append(label) or {"ok": True},
    )
    monkeypatch.setattr(
        app_mod.github_mirror,
        "ensure_label",
        lambda repo, label: calls["ensure_label"].append(label) or {"ok": True},
    )
    return calls


def test_launch_posts_started_comment(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_WEB_PORT_RANGE", "42020-42030")
    calls = _install_fake_client(monkeypatch)

    def fake_launch(payload, proc_holder=None):
        _mirrorable_entry(tmp_path, payload["change_id"])
        proc_holder["proc"] = object()
        return {"pid": 123}

    monkeypatch.setattr(app_mod, "_launch_fn", fake_launch)
    c = _client(token="sekrit")
    r = c.post("/launch", json={"repo": "/r/proj", "change_id": "1-a"}, headers=AUTH)
    assert r.status_code == 200
    assert len(calls["comment"]) == 1 and "started" in calls["comment"][0]


def test_terminal_success_posts_finished(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    _mirrorable_entry(tmp_path)
    calls = _install_fake_client(monkeypatch)
    app_mod._mirror_terminal_events(
        [{"slug": "proj", "change_id": "1-a", "classified": "success", "detail": ""}]
    )
    assert len(calls["comment"]) == 1 and "finished" in calls["comment"][0]
    assert calls["add_label"] == []


def test_terminal_death_labels_and_comments(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    _mirrorable_entry(tmp_path)
    calls = _install_fake_client(monkeypatch)
    app_mod._mirror_terminal_events(
        [
            {
                "slug": "proj",
                "change_id": "1-a",
                "classified": "unknown",
                "remedy": None,
                "detail": "the real error tail",
            }
        ]
    )
    assert calls["add_label"] == ["run-died"]
    assert "the real error tail" in calls["comment"][0]


def test_terminal_gate_pause_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    _mirrorable_entry(tmp_path)
    calls = _install_fake_client(monkeypatch)
    app_mod._mirror_terminal_events(
        [{"slug": "proj", "change_id": "1-a", "classified": "gate-pause", "detail": "EOF"}]
    )
    assert calls["comment"] == [] and calls["add_label"] == []


def test_reconcile_after_restart_no_double_post(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    _mirrorable_entry(tmp_path)
    calls = _install_fake_client(monkeypatch)
    event = {"slug": "proj", "change_id": "1-a", "classified": "api-transient", "detail": "boom"}
    app_mod._mirror_terminal_events([event])  # first observation
    app_mod._mirror_terminal_events([event])  # daemon restart / later reconcile pass
    assert len(calls["comment"]) == 1 and len(calls["add_label"]) == 1


def test_index_serves_html(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    r = _client().get("/")
    assert r.status_code == 200 and "agent-orchestration" in r.text


def test_index_dashboard_link_only_while_running(tmp_path, monkeypatch):
    """The dashboard is served by the run's own conductor process — dead/done
    runs must not render their (stale, possibly re-allocated) URL as a link."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    running = registry.new_entry(
        repo="r1",
        change_id="1-live",
        worktree=str(tmp_path),
        branch="b",
        box=None,
        tmpdir=str(tmp_path),
    )
    running["incarnations"].append(
        {
            "pid": os.getpid(),
            "started_at": "x",
            "web_port": 42001,
            "dashboard_url": "http://localhost:42001",
            "exit_code": None,
            "classified": None,
        }
    )
    registry.write_entry(running)
    done = registry.new_entry(
        repo="r2",
        change_id="2-done",
        worktree=str(tmp_path),
        branch="b",
        box=None,
        tmpdir=str(tmp_path),
    )
    done["incarnations"].append(
        {
            "pid": 1,
            "started_at": "x",
            "web_port": 42002,
            "dashboard_url": "http://localhost:42002",
            "exit_code": 0,
            "classified": "success",
        }
    )
    registry.write_entry(done)
    text = _client().get("/").text
    assert "http://localhost:42001" in text
    assert "http://localhost:42002" not in text
