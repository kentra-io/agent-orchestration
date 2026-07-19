"""Registry = facts only (paths/ids/pids); state is always derived on read."""

import json

from orchestration.obs import registry


def test_write_and_load_entry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    entry = registry.new_entry(
        repo="/Users/jony/code/kentra/kafka-dq",
        change_id="7-observability",
        worktree="/tmp/wt",
        branch="7-observability",
        box="kafka-dq-box",
        tmpdir="/tmp/wt/.conductor-tmp",
        issue=7,
    )
    path = registry.write_entry(entry)
    assert path == tmp_path / "kafka-dq--7-observability.json"
    loaded = registry.load_entry("kafka-dq", "7-observability")
    assert loaded == entry
    assert loaded["incarnations"] == []
    assert loaded["issue"] == 7


def test_append_and_update_incarnation(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    entry = registry.new_entry(
        repo="r", change_id="1-x", worktree="w", branch="b", box=None, tmpdir="t"
    )
    registry.write_entry(entry)
    registry.append_incarnation(
        "r",
        "1-x",
        {
            "pid": 123,
            "started_at": "2026-07-19T00:00:00+00:00",
            "web_port": 42001,
            "exit_code": None,
            "classified": None,
        },
    )
    registry.update_incarnation("r", "1-x", exit_code=1, classified="oauth-expired")
    loaded = registry.load_entry("r", "1-x")
    assert loaded["incarnations"][-1]["exit_code"] == 1
    assert loaded["incarnations"][-1]["classified"] == "oauth-expired"


def test_load_entries_lists_all(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    for cid in ("1-a", "2-b"):
        registry.write_entry(
            registry.new_entry(
                repo="r", change_id=cid, worktree="w", branch="b", box=None, tmpdir="t"
            )
        )
    assert {e["change_id"] for e in registry.load_entries()} == {"1-a", "2-b"}


def test_write_is_atomic_json(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    path = registry.write_entry(
        registry.new_entry(
            repo="r", change_id="1-a", worktree="w", branch="b", box=None, tmpdir="t"
        )
    )
    json.loads(path.read_text())  # valid JSON on disk
    assert not list(tmp_path.glob("*.tmp"))
