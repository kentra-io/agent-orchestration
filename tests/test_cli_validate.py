import argparse

import orchestration.cli.validate_cmd as vc
from orchestration.resume.plan import PlanReadError


def _ns(**kw):
    base = {"change_id": "011-orch-validate", "repo": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_valid_plan_summarized(monkeypatch, tmp_path, capsys):
    # Scenario: Valid plan summarized — one line per milestone + total, exit 0.
    monkeypatch.setattr(vc.shutil, "which", lambda name: "/usr/local/bin/lifecycle")
    monkeypatch.setattr(
        vc,
        "load_milestones_from_apply",
        lambda change, cwd: [
            {"id": 1, "title": "subcommand", "contract": {"check": "x"}},
            {"id": 2, "title": "docs"},
        ],
    )
    rc = vc.cmd_validate(_ns(repo=str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "1  subcommand  [contract]" in out
    assert "2  docs  [no contract]" in out
    assert "2 milestone(s), plan valid" in out


def test_invalid_change_lists_available(monkeypatch, tmp_path, capsys):
    # Scenario: Invalid or unknown change rejected — error + available changes, exit 1.
    changes = tmp_path / "openspec" / "changes"
    (changes / "011-orch-validate").mkdir(parents=True)
    (changes / "010-prior").mkdir()
    (changes / "archive").mkdir()  # archive is filtered out

    monkeypatch.setattr(vc.shutil, "which", lambda name: "/usr/local/bin/lifecycle")

    def _raise(change, cwd):
        raise PlanReadError("tasks.md failed plan-stage validation")

    monkeypatch.setattr(vc, "load_milestones_from_apply", _raise)
    rc = vc.cmd_validate(_ns(change_id="nope", repo=str(tmp_path)))
    err = capsys.readouterr().err
    assert rc == 1
    assert "failed plan validation" in err
    assert "available changes: 010-prior, 011-orch-validate" in err
    assert "archive" not in err


def test_missing_lifecycle_is_env_error(monkeypatch, tmp_path, capsys):
    # Scenario: Missing lifecycle binary is an environment error — hint + exit 2.
    monkeypatch.setattr(vc.shutil, "which", lambda name: None)
    rc = vc.cmd_validate(_ns(repo=str(tmp_path)))
    err = capsys.readouterr().err
    assert rc == 2
    assert "`lifecycle` not on PATH" in err
