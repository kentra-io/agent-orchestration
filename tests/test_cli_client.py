import orchestration.client as client
from orchestration.obs import registry


def test_daemon_url_env(monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_DAEMON_URL", "http://host.docker.internal:8765")
    assert client.daemon_url() == "http://host.docker.internal:8765"


def test_runs_falls_back_to_local_registry(tmp_path, monkeypatch):
    """Daemon down → derive locally from the registry (design §5.2)."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_DAEMON_URL", "http://127.0.0.1:1")  # nothing listens
    registry.write_entry(
        registry.new_entry(
            repo="r",
            change_id="1-a",
            worktree=str(tmp_path),
            branch="b",
            box=None,
            tmpdir=str(tmp_path),
        )
    )
    runs = client.get_runs()
    assert runs[0]["change_id"] == "1-a"
    assert runs[0]["derived"]["state"] == "registered"


def test_status_filters_by_change(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_DAEMON_URL", "http://127.0.0.1:1")
    registry.write_entry(
        registry.new_entry(
            repo="r",
            change_id="1-a",
            worktree=str(tmp_path),
            branch="b",
            box=None,
            tmpdir=str(tmp_path),
        )
    )
    assert client.get_status("1-a")["change_id"] == "1-a"
    assert client.get_status("9-nope") is None
