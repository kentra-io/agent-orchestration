import orchestration.cli.main as cli_main
from orchestration import client


def test_runs_prints_table(monkeypatch, capsys):
    monkeypatch.setattr(
        client,
        "get_runs",
        lambda: [
            {
                "repo_slug": "r",
                "change_id": "1-a",
                "derived": {"state": "running"},
                "incarnations": [{"dashboard_url": "http://localhost:42000"}],
            }
        ],
    )
    assert cli_main.main(["runs"]) == 0
    out = capsys.readouterr().out
    assert "1-a" in out and "http://localhost:42000" in out


def test_status_unknown_change_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(client, "get_status", lambda cid: None)
    assert cli_main.main(["status", "9-nope"]) == 1
    assert "no run registered" in capsys.readouterr().err


def test_launch_payload_inline_posts(monkeypatch, capsys):
    seen = {}

    def fake_post(payload):
        seen["payload"] = payload
        return {"report": {"pid": 1}}

    monkeypatch.setattr(client, "post_launch", fake_post)
    rc = cli_main.main(["launch", "--payload", '{"repo": "/r", "change_id": "1-a"}'])
    assert rc == 0
    assert seen["payload"]["change_id"] == "1-a"
