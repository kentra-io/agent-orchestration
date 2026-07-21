import argparse
import json
from pathlib import Path

import orchestration.cli.launch_cmd as lc
from orchestration import client


def _ns(**kw):
    base = {
        "change_id": "1-a",
        "repo": None,
        "stub": False,
        "milestones_file": None,
        "issue": None,
        "branch": None,
        "no_open": False,
        "payload": None,
        "direct": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_materialize_stub_files_uses_packaged_demo(tmp_path):
    plan, script = lc._materialize_stub_files(str(tmp_path), "1-a", None)
    assert json.loads(Path(plan).read_text())["milestones"][0]["id"] == 1
    assert "implementer" in json.loads(Path(script).read_text())["steps"]
    assert plan.startswith(str(tmp_path / ".orchestration-stub" / "1-a"))


def test_stub_launch_posts_and_reports(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_post(payload):
        seen["payload"] = payload
        return {
            "report": {
                "dashboard_url": "http://localhost:42001",
                "pid": 7,
                "worktree": "w",
                "branch": "b",
                "registry_path": "p",
            }
        }

    monkeypatch.setattr(client, "post_launch", fake_post)
    opened = []
    monkeypatch.setattr(lc.webbrowser, "open", lambda url: opened.append(url))
    rc = lc.cmd_launch(_ns(stub=True, repo=str(tmp_path)))
    assert rc == 0
    assert seen["payload"]["conductor"]["provider"] == "stub"
    assert seen["payload"]["wait"] is False
    assert opened == []  # pytest's stdout is not a TTY → suppressed


def test_auto_open_on_tty_unless_no_open(monkeypatch, tmp_path):
    monkeypatch.setattr(
        client,
        "post_launch",
        lambda p: {"report": {"dashboard_url": "http://localhost:42002"}},
    )
    monkeypatch.setattr(lc.sys.stdout, "isatty", lambda: True)
    opened = []
    monkeypatch.setattr(lc.webbrowser, "open", lambda url: opened.append(url))
    assert lc.cmd_launch(_ns(stub=True, repo=str(tmp_path))) == 0
    assert opened == ["http://localhost:42002"]
    opened.clear()
    assert lc.cmd_launch(_ns(stub=True, repo=str(tmp_path), no_open=True)) == 0
    assert opened == []


def test_production_validation_skips_without_lifecycle(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(lc.shutil, "which", lambda name: None)
    monkeypatch.setattr(client, "post_launch", lambda p: {"report": {"dashboard_url": None}})
    assert lc.cmd_launch(_ns(repo=str(tmp_path))) == 0
    assert "skipping local plan validation" in capsys.readouterr().err


def test_production_budget_guard(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(lc.shutil, "which", lambda name: "/usr/local/bin/lifecycle")
    monkeypatch.setattr(
        lc,
        "load_milestones_from_apply",
        lambda change, cwd: [{"id": i, "title": "t"} for i in range(1, 30)],
    )
    assert lc.cmd_launch(_ns(repo=str(tmp_path))) == 1
    assert "max_iterations" in capsys.readouterr().err


def test_daemon_down_message(monkeypatch, tmp_path, capsys):
    import urllib.error

    def raise_down(payload):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(client, "post_launch", raise_down)
    assert lc.cmd_launch(_ns(stub=True, repo=str(tmp_path))) == 1
    assert "orch daemon start" in capsys.readouterr().err
