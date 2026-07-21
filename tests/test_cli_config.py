import json
import stat

from orchestration.cli import config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))
    monkeypatch.delenv("ORCHESTRATION_DAEMON_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATION_DAEMON_TOKEN", raising=False)
    assert config.load_config() == {}
    assert config.resolve_url() == "http://127.0.0.1:8765"
    assert config.resolve_token() is None


def test_file_beats_default_and_env_beats_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))
    monkeypatch.delenv("ORCHESTRATION_DAEMON_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATION_DAEMON_TOKEN", raising=False)
    config.save_config({"url": "http://file:1", "token": "file-tok"})
    assert config.resolve_url() == "http://file:1"
    assert config.resolve_token() == "file-tok"
    monkeypatch.setenv("ORCHESTRATION_DAEMON_URL", "http://env:2")
    monkeypatch.setenv("ORCHESTRATION_DAEMON_TOKEN", "env-tok")
    assert config.resolve_url() == "http://env:2"
    assert config.resolve_token() == "env-tok"


def test_save_config_mode_600_and_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "daemon.json"
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(path))
    config.save_config({"token": "sekrit"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["token"] == "sekrit"
    assert config.load_config() == {"token": "sekrit"}
