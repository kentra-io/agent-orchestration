import argparse
import json
import subprocess
from datetime import UTC, datetime, timedelta

import orchestration.cli.auth_cmd as auth_cmd


class R:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_read_token_hits_keychain(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_LONG_LIVED_TOKEN", raising=False)

    def fake_run(argv, **kwargs):
        assert argv == ["security", "find-generic-password", "-s", auth_cmd.SERVICE, "-w"]
        return R(0, out="sk-ant-oat01-x\n")

    monkeypatch.setattr(auth_cmd.subprocess, "run", fake_run)
    assert auth_cmd.read_token() == "sk-ant-oat01-x"


def test_read_token_env_override(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_LONG_LIVED_TOKEN", "sk-ant-oat01-env")

    def fail_run(argv, **kwargs):
        raise AssertionError(f"must not hit keychain when env override present: {argv}")

    monkeypatch.setattr(auth_cmd.subprocess, "run", fail_run)
    assert auth_cmd.read_token() == "sk-ant-oat01-env"


def test_read_token_none_when_absent(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_LONG_LIVED_TOKEN", raising=False)

    def fake_run(argv, **kwargs):
        return R(44)  # security: item not found

    monkeypatch.setattr(auth_cmd.subprocess, "run", fake_run)
    assert auth_cmd.read_token() is None


def test_mint_rejects_bad_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["claude", "setup-token"]:
            return R(0)
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(auth_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(auth_cmd.getpass, "getpass", lambda *a, **k: "garbage")

    assert auth_cmd.cmd_mint(argparse.Namespace()) == 1
    assert not any(c[:2] == ["security", "add-generic-password"] for c in calls)


def test_mint_stores_and_stamps(monkeypatch, tmp_path):
    cfg_path = tmp_path / "daemon.json"
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(cfg_path))
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["claude", "setup-token"]:
            return R(0)
        if argv[:2] == ["claude", "-p"]:
            return R(0)  # live-verify ok
        if argv[:2] == ["security", "add-generic-password"]:
            return R(0)
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(auth_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(auth_cmd.getpass, "getpass", lambda *a, **k: "sk-ant-oat01-good")

    assert auth_cmd.cmd_mint(argparse.Namespace()) == 0

    store_call = next(c for c in calls if c[:2] == ["security", "add-generic-password"])
    assert "-U" in store_call
    assert store_call[store_call.index("-s") + 1] == auth_cmd.SERVICE
    assert store_call[-1] == "sk-ant-oat01-good"

    cfg = json.loads(cfg_path.read_text())
    assert "token_minted_at" in cfg
    datetime.fromisoformat(cfg["token_minted_at"])  # must be a valid ISO timestamp


def test_token_age_warning(monkeypatch, tmp_path):
    cfg_path = tmp_path / "daemon.json"
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(cfg_path))
    minted = (datetime.now(UTC) - timedelta(days=340)).isoformat()
    cfg_path.write_text(json.dumps({"token_minted_at": minted}))

    msg = auth_cmd.warn_if_stale()
    assert msg is not None
    assert "re-mint" in msg


def test_warn_if_stale_naive_timestamp_returns_none(monkeypatch, tmp_path):
    """A hand-written `token_minted_at` with no tz offset (plausible on a
    non-macOS host) must not crash `warn_if_stale` with an uncaught
    TypeError from subtracting a naive datetime from an aware one."""
    cfg_path = tmp_path / "daemon.json"
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(cfg_path))
    cfg_path.write_text(json.dumps({"token_minted_at": "2026-01-01T00:00:00"}))  # naive

    assert auth_cmd.warn_if_stale() is None


def test_mint_missing_claude_binary(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))

    def fake_run(argv, **kwargs):
        if argv[:2] == ["claude", "setup-token"]:
            raise FileNotFoundError(2, "No such file or directory")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(auth_cmd.subprocess, "run", fake_run)

    assert auth_cmd.cmd_mint(argparse.Namespace()) == 1
    err = capsys.readouterr().err
    assert "claude" in err and "not found" in err


def test_mint_verify_timeout(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))

    def fake_run(argv, **kwargs):
        if argv[:2] == ["claude", "setup-token"]:
            return R(0)
        if argv[:2] == ["claude", "-p"]:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=120)
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(auth_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(auth_cmd.getpass, "getpass", lambda *a, **k: "sk-ant-oat01-good")

    assert auth_cmd.cmd_mint(argparse.Namespace()) == 1
    assert "timed out" in capsys.readouterr().err


def test_mint_store_token_locked_keychain(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))

    def fake_run(argv, **kwargs):
        if argv[:2] == ["claude", "setup-token"]:
            return R(0)
        if argv[:2] == ["claude", "-p"]:
            return R(0)  # live-verify ok
        if argv[:2] == ["security", "add-generic-password"]:
            raise subprocess.CalledProcessError(1, argv, stderr="User interaction is not allowed.")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(auth_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(auth_cmd.getpass, "getpass", lambda *a, **k: "sk-ant-oat01-good")

    assert auth_cmd.cmd_mint(argparse.Namespace()) == 1
    err = capsys.readouterr().err
    assert "keychain" in err
    assert "User interaction is not allowed." in err


def test_mint_verify_failure_includes_stderr_tail(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ORCHESTRATION_CONFIG_PATH", str(tmp_path / "daemon.json"))

    def fake_run(argv, **kwargs):
        if argv[:2] == ["claude", "setup-token"]:
            return R(0)
        if argv[:2] == ["claude", "-p"]:
            return R(1, err="Error: invalid_token: the access token is invalid")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(auth_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(auth_cmd.getpass, "getpass", lambda *a, **k: "sk-ant-oat01-bad")

    assert auth_cmd.cmd_mint(argparse.Namespace()) == 1
    err = capsys.readouterr().err
    assert "token failed a live" in err
    assert "invalid_token" in err  # bad-token vs network-down now distinguishable
