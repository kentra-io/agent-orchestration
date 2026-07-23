import json
from pathlib import Path
from types import SimpleNamespace

import orchestration.daemon.resume as dr
from orchestration.obs import registry

M1 = {"id": 1, "title": "one"}
M2 = {"id": 2, "title": "two"}
M2_EDITED = {"id": 2, "title": "two (rescoped by human)"}
M3 = {"id": 3, "title": "three"}


def _entry(tmp_path, *, box=None, provider="stub", env=None):
    e = registry.new_entry(
        repo="/r/proj",
        change_id="1-a",
        worktree=str(tmp_path / "wt"),
        branch="b",
        box=box,
        tmpdir=str(tmp_path / "tmp"),
        provider=provider,
        conductor_env=env or {},
    )
    (tmp_path / "wt").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    registry.write_entry(e)
    return e


def _ckpt(fixture_path, milestones, cursor):
    return SimpleNamespace(
        file_path=Path("/ck/execute-change-x.json"),
        current_agent="milestone_step",
        plan_fixture_path=str(fixture_path),
        milestones=milestones,
        cursor_index=cursor,
        completed_milestone_ids=[m["id"] for m in milestones[:cursor]],
    )


def _wire(monkeypatch, tmp_path, ckpt, current_milestones):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    monkeypatch.setattr(dr, "find_latest_checkpoint_in", lambda tmpdir: ckpt.file_path)
    monkeypatch.setattr(dr, "load_execute_change_checkpoint", lambda p: ckpt)
    monkeypatch.setattr(
        dr,
        "current_milestones",
        lambda worktree, change_id, fixture: (current_milestones, "fixture"),
    )
    spawned = {}

    class FakeProc:
        pid = 4242

    def fake_popen(argv, **kwargs):
        spawned["argv"] = argv
        spawned["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(dr.subprocess, "Popen", fake_popen)
    return spawned


def test_resume_in_place_when_plan_unchanged(monkeypatch, tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2]}))
    ckpt = _ckpt(fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2])
    entry = _entry(tmp_path)

    report = dr.resume(entry, web_port=42010)
    argv = spawned["argv"]
    assert "resume" in argv and "--from" in argv and "--skip-gates" in argv
    assert "--web-port" in argv and "42010" in argv
    assert "--provider" in argv and "stub" in argv
    assert report["mode"] == "resume-in-place"
    assert report["dashboard_url"] == "http://localhost:42010"
    stored = registry.load_entry("proj", "1-a")
    assert stored["incarnations"][-1]["pid"] == 4242


def test_fresh_run_when_plan_changed(monkeypatch, tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2_EDITED, M3]}))
    ckpt = _ckpt(fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2_EDITED, M3])
    entry = _entry(tmp_path)

    report = dr.resume(entry, web_port=42011)
    argv = spawned["argv"]
    assert "run" in argv and "resume" not in argv
    fixture_arg = next(a for a in argv if a.startswith("plan_fixture_path="))
    written = json.loads(Path(fixture_arg.split("=", 1)[1]).read_text())
    assert [m["id"] for m in written["milestones"]] == [2, 3]  # id 1 never re-runs
    assert report["mode"] == "fresh-run-remaining"


def test_no_checkpoint_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    monkeypatch.setattr(dr, "find_latest_checkpoint_in", lambda tmpdir: None)
    entry = _entry(tmp_path)
    try:
        dr.resume(entry, web_port=42012)
        raise AssertionError("expected ResumeError")
    except dr.ResumeError as exc:
        assert "no checkpoint" in str(exc)


# --- box auth/health pre-flight (harness tasks/orchestration-box-auth-expiry.md) ---


def _probe_report(ok, classified="oauth-expired"):
    return {
        "ok": ok,
        "classified": "success" if ok else classified,
        "remedy": None if ok else "run `cb login` from the worktree, then resume",
        "detail": "" if ok else "OAuth session expired and could not be refreshed",
    }


def test_box_preflight_ok_proceeds(monkeypatch, tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2]}))
    ckpt = _ckpt(fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2])
    monkeypatch.setattr(dr, "health_probe", lambda box, **kw: _probe_report(True))
    entry = _entry(tmp_path, box="box-1")

    report = dr.resume(entry, web_port=42013)
    assert report["mode"] == "resume-in-place"
    assert spawned["argv"]


def test_box_preflight_heals_via_cb_login_then_proceeds(monkeypatch, tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2]}))
    ckpt = _ckpt(fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2])

    probes = iter([_probe_report(False), _probe_report(True)])
    monkeypatch.setattr(dr, "health_probe", lambda box, **kw: next(probes))
    logins = []

    def fake_run(argv, **kwargs):
        logins.append((argv, kwargs.get("cwd")))

        class P:
            returncode = 0
            stdout = "Credentials provisioned into container"
            stderr = ""

        return P()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    entry = _entry(tmp_path, box="box-1")

    report = dr.resume(entry, web_port=42014)
    assert report["mode"] == "resume-in-place"
    assert logins and logins[0][0] == ["cb", "login"]
    assert logins[0][1] == entry["worktree"]  # cb resolves the box from cwd
    assert spawned["argv"]


def test_box_preflight_still_failing_raises_with_classified_remedy(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    monkeypatch.setattr(dr, "health_probe", lambda box, **kw: _probe_report(False))

    def fake_run(argv, **kwargs):
        class P:
            returncode = 1
            stdout = ""
            stderr = "host token also expired"

        return P()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    entry = _entry(tmp_path, box="box-1")
    try:
        dr.resume(entry, web_port=42015)
        raise AssertionError("expected ResumeError")
    except dr.ResumeError as exc:
        msg = str(exc)
        assert "oauth-expired" in msg
        assert "cb login" in msg


def test_no_box_skips_preflight(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dr, "health_probe", lambda box, **kw: (_ for _ in ()).throw(AssertionError("probed"))
    )
    monkeypatch.setattr(dr, "find_latest_checkpoint_in", lambda tmpdir: None)
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    entry = _entry(tmp_path)
    try:
        dr.resume(entry, web_port=42016)
        raise AssertionError("expected ResumeError")
    except dr.ResumeError as exc:
        assert "no checkpoint" in str(exc)
