import argparse

import orchestration.cli.validate_cmd as vc
from orchestration.resume.plan import PlanReadError


def _ns(**kw):
    base = {"change_id": "011-orch-validate", "repo": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_valid_plan_summarized(monkeypatch, tmp_path, capsys):
    """Scenario: Valid plan summarized — one line per milestone (id, title,
    contract presence) plus a total, exit 0."""
    monkeypatch.setattr(vc.shutil, "which", lambda name: "/usr/local/bin/lifecycle")
    monkeypatch.setattr(
        vc,
        "load_milestones_from_apply",
        lambda change, cwd: [
            {"id": 1, "title": "first", "contract": {"check": "x"}},
            {"id": 2, "title": "second"},
        ],
    )
    rc = vc.cmd_validate(_ns(repo=str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "1  first  [contract]" in out
    assert "2  second  [no contract]" in out
    assert "2 milestone(s), plan valid" in out


def test_invalid_change_lists_available(monkeypatch, tmp_path, capsys):
    """Scenario: Invalid or unknown change rejected with guidance — error to
    stderr, available non-archive change folders listed, exit 1."""
    monkeypatch.setattr(vc.shutil, "which", lambda name: "/usr/local/bin/lifecycle")

    def boom(change, cwd):
        raise PlanReadError("tasks.md failed plan-stage validation")

    monkeypatch.setattr(vc, "load_milestones_from_apply", boom)
    changes = tmp_path / "openspec" / "changes"
    (changes / "001-alpha").mkdir(parents=True)
    (changes / "002-beta").mkdir()
    (changes / "archive").mkdir()
    rc = vc.cmd_validate(_ns(change_id="999-nope", repo=str(tmp_path)))
    err = capsys.readouterr().err
    assert rc == 1
    assert "failed plan validation" in err
    assert "001-alpha" in err and "002-beta" in err
    assert "archive" not in err


def test_missing_lifecycle_binary_exits_2(monkeypatch, tmp_path, capsys):
    """Scenario: Missing lifecycle binary is an environment error — install hint
    to stderr, exit 2."""
    monkeypatch.setattr(vc.shutil, "which", lambda name: None)
    rc = vc.cmd_validate(_ns(repo=str(tmp_path)))
    err = capsys.readouterr().err
    assert rc == 2
    assert "lifecycle" in err
    assert "PATH" in err


def test_registered_in_main_parser(monkeypatch, tmp_path):
    """`validate` is wired into build_parser() (main.py registration)."""
    import orchestration.cli.main as cli_main

    monkeypatch.setattr(vc.shutil, "which", lambda name: None)
    assert cli_main.main(["validate", "011-x", "--repo", str(tmp_path)]) == 2
