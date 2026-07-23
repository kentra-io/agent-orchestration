import argparse
import json

import orchestration.cli.daemon_cmd as dc


class R:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def _ns(**kw):
    base = {"image": None, "code_root": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_build_run_argv_mirrors_makefile():
    argv = dc.build_run_argv(
        "ghcr.io/kentra-io/agent-orchestration-daemon:latest",
        "tok123",
        "/Users/u/code",
        home="/Users/u",
    )
    assert argv == [
        "docker",
        "run",
        "-d",
        "--name",
        "agent-orchestration-daemon",
        "--restart=always",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        "/Users/u/.agent-orchestration:/root/.agent-orchestration",
        "-v",
        "/Users/u/.claude:/root/.claude:ro",
        "-v",
        "/Users/u/code:/Users/u/code",
        "-e",
        "KENTRA_BOT_GH_TOKEN",
        "-e",
        "ORCHESTRATION_DAEMON_TOKEN=tok123",
        "-p",
        "8765:8765",
        "-p",
        "42000-42050:42000-42050",
        "ghcr.io/kentra-io/agent-orchestration-daemon:latest",
    ]


def test_start_idempotent_when_running(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return R(0)
        if argv[:2] == ["docker", "inspect"]:
            return R(0, out="running\n")
        raise AssertionError(f"unexpected docker call: {argv}")

    monkeypatch.setattr(dc, "_run", fake_run)
    assert dc.cmd_start(_ns()) == 0
    assert not any(a[:2] == ["docker", "run"] for a in calls)


def test_start_generates_and_persists_token(monkeypatch, tmp_path):
    cfg_path = tmp_path / "daemon.json"
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(cfg_path))
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return R(0)
        if argv[:2] == ["docker", "inspect"]:
            return R(1)  # no container
        if argv[:3] == ["docker", "image", "inspect"]:
            return R(0)  # image present, no pull needed
        if argv[:3] == ["docker", "rm", "-f"]:
            return R(0)
        if argv[:2] == ["docker", "run"]:
            return R(0, out="abc123\n")
        raise AssertionError(f"unexpected docker call: {argv}")

    monkeypatch.setattr(dc, "_run", fake_run)
    assert dc.cmd_start(_ns()) == 0
    token = json.loads(cfg_path.read_text())["token"]
    assert len(token) == 32  # secrets.token_hex(16)
    run_argv = next(a for a in calls if a[:2] == ["docker", "run"])
    assert f"ORCHESTRATION_DAEMON_TOKEN={token}" in run_argv


def test_start_exit2_when_docker_unreachable(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))
    monkeypatch.setattr(dc, "docker_available", lambda: False)
    assert dc.cmd_start(_ns()) == 2


def test_start_pull_denied_hints_ghcr_login(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))

    def fake_run(argv, **kw):
        if argv[:2] == ["docker", "info"]:
            return R(0)
        if argv[:2] == ["docker", "inspect"]:
            return R(1)
        if argv[:3] == ["docker", "image", "inspect"]:
            return R(1)  # not local
        if argv[:2] == ["docker", "pull"]:
            return R(1, err="denied")
        raise AssertionError(f"unexpected docker call: {argv}")

    monkeypatch.setattr(dc, "_run", fake_run)
    assert dc.cmd_start(_ns()) == 1
    assert "docker login ghcr.io" in capsys.readouterr().err


def test_env_prints_evalable_token_export(monkeypatch, tmp_path, capsys):
    cfg_path = tmp_path / "daemon.json"
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(cfg_path))
    cfg_path.write_text(json.dumps({"token": "tok123"}))
    assert dc.cmd_env(_ns()) == 0
    assert capsys.readouterr().out == "export ORCHESTRATION_DAEMON_TOKEN=tok123\n"


def test_env_exit1_before_first_start(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))
    assert dc.cmd_env(_ns()) == 1
    err = capsys.readouterr().err
    assert "orch daemon start" in err
