import stat

import pytest

from orchestration.launch.change import ChangeLaunchError, health_probe


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
