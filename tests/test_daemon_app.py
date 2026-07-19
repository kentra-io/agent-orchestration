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


def test_resume_is_501():
    assert _client().post("/resume", json={}).status_code == 501


def test_index_serves_html(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    r = _client().get("/")
    assert r.status_code == 200 and "agent-orchestration" in r.text
