import argparse
import json
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
