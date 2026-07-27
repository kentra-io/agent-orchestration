import stat

import pytest

from orchestration.launch import change as change_mod
from orchestration.launch.change import ChangeLaunchError, health_probe


@pytest.fixture(autouse=True)
def _no_long_lived_token(monkeypatch):
    """Hermeticity: a dev machine / daemon env may export the custody-chain
    token; strip it by default so the plain-exec-shape tests below don't
    flap depending on where they run. Tests exercising the token path set
    it explicitly."""
    monkeypatch.delenv("CLAUDE_CODE_LONG_LIVED_TOKEN", raising=False)


def _fake_docker(tmp_path, script_body: str) -> str:
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / "docker"
    p.write_text(f"#!/bin/sh\n{script_body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_probe_ok(tmp_path):
    docker = _fake_docker(tmp_path, "echo OK; exit 0")
    report = health_probe("some-box", docker_bin=docker)
    assert report["ok"] is True and report["classified"] == "success"


def test_probe_oauth_expiry_classified(tmp_path):
    docker = _fake_docker(
        tmp_path, "echo 'OAuth session expired and could not be refreshed'; exit 1"
    )
    report = health_probe("some-box", docker_bin=docker)
    assert report["ok"] is False
    assert report["classified"] == "oauth-expired"
    assert "cb login" in report["remedy"]


def test_probe_failure_raises_in_launch_wrapper(tmp_path):
    docker = _fake_docker(tmp_path, "echo 'OAuth session expired'; exit 1")
    with pytest.raises(ChangeLaunchError) as exc:
        health_probe("some-box", docker_bin=docker, raise_on_fail=True)
    assert "oauth-expired" in str(exc.value)
    assert "cb login" in str(exc.value)


def _fake_run_recorder(recorded):
    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded["env"] = kwargs.get("env")

        class P:
            returncode = 0
            stdout = "OK"
            stderr = ""

        return P()

    return fake_run


def test_health_probe_forwards_long_lived_token(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_LONG_LIVED_TOKEN", "sk-ant-oat01-x")
    recorded: dict = {}
    monkeypatch.setattr(change_mod.subprocess, "run", _fake_run_recorder(recorded))

    report = health_probe("some-box", docker_bin="docker")

    assert recorded["argv"][:4] == ["docker", "exec", "-e", "CLAUDE_CODE_OAUTH_TOKEN"]
    assert recorded["argv"][4:] == ["some-box", "claude", "-p", "OK"]
    assert recorded["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-x"
    assert report["ok"] is True


def test_health_probe_plain_exec_without_token(monkeypatch):
    # the autouse fixture above already strips the env var; be explicit anyway.
    monkeypatch.delenv("CLAUDE_CODE_LONG_LIVED_TOKEN", raising=False)
    recorded: dict = {}
    monkeypatch.setattr(change_mod.subprocess, "run", _fake_run_recorder(recorded))

    report = health_probe("some-box", docker_bin="docker")

    assert recorded["argv"] == ["docker", "exec", "some-box", "claude", "-p", "OK"]
    assert recorded["env"] is None
    assert report["ok"] is True
