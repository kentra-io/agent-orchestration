import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import orchestration.daemon.resume as dr
from orchestration.obs import registry


@pytest.fixture(autouse=True)
def _no_long_lived_token(monkeypatch):
    """Hermeticity: a dev machine / daemon env may export the custody-chain
    token; strip it by default so the cb-login-heal tests below (which
    assume legacy no-env-auth behavior) don't flap depending on where they
    run. Tests exercising the env-auth path set it explicitly."""
    monkeypatch.delenv("CLAUDE_CODE_LONG_LIVED_TOKEN", raising=False)


M1 = {"id": 1, "title": "one"}
M2 = {"id": 2, "title": "two"}
M2_EDITED = {"id": 2, "title": "two (rescoped by human)"}
M3 = {"id": 3, "title": "three"}


def _entry(tmp_path, *, box=None, provider="stub", env=None, branch="b", issue=None, repo_gh=None):
    e = registry.new_entry(
        repo="/r/proj",
        change_id="1-a",
        worktree=str(tmp_path / "wt"),
        branch=branch,
        box=box,
        tmpdir=str(tmp_path / "tmp"),
        provider=provider,
        conductor_env=env or {},
        issue=issue,
        repo_gh=repo_gh,
    )
    (tmp_path / "wt").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    registry.write_entry(e)
    return e


def _inputs_from_argv(argv):
    """Reconstruct the {key: value} --input map from a built conductor argv."""
    out = {}
    it = iter(argv)
    for item in it:
        if item == "--input":
            key, _, value = next(it).partition("=")
            out[key] = value
    return out


def _write_raw_checkpoint(path, *, inputs=None):
    """A minimal on-disk checkpoint JSON — real enough for
    `heal_checkpoint_inputs` (raw `json.load`, not `CheckpointManager`) to
    read/write, with both the top-level `inputs` dict and the nested
    `context.workflow_inputs` dict the live 007 checkpoints carry."""
    inputs = dict(inputs) if inputs is not None else {}
    payload = {
        "version": 1,
        "workflow_path": "/wf/execute-change.yaml",
        "workflow_hash": "sha256:deadbeef",
        "created_at": "2026-07-01T00:00:00+00:00",
        "failure": {"error_type": None, "message": None},
        "inputs": inputs,
        "current_agent": "milestone_step",
        "context": {"workflow_inputs": dict(inputs), "agent_outputs": {}},
        "limits": {},
    }
    path.write_text(json.dumps(payload))
    return path


def _ckpt(tmp_path, fixture_path, milestones, cursor, *, raw_inputs=None):
    ckpt_path = tmp_path / "ckpt.json"
    _write_raw_checkpoint(ckpt_path, inputs=raw_inputs)
    return SimpleNamespace(
        file_path=ckpt_path,
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
    ckpt = _ckpt(tmp_path, fixture, [M1, M2], cursor=1)
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
    ckpt = _ckpt(tmp_path, fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2_EDITED, M3])
    entry = _entry(tmp_path)

    report = dr.resume(entry, web_port=42011)
    argv = spawned["argv"]
    assert "run" in argv and "resume" not in argv
    fixture_arg = next(a for a in argv if a.startswith("plan_fixture_path="))
    written = json.loads(Path(fixture_arg.split("=", 1)[1]).read_text())
    assert [m["id"] for m in written["milestones"]] == [2, 3]  # id 1 never re-runs
    assert report["mode"] == "fresh-run-remaining"


def test_fresh_run_carries_branch_and_change_id(monkeypatch, tmp_path):
    """Bug A regression: fresh-run-remaining must forward every workflow
    input that `execute-change.yaml` templates read via `workflow.input.*`
    and that the launcher (orchestration.launch.change) derives unconditionally
    -- omitting `branch` crashed a real run in 0.02s (`Undefined variable in
    template: 'dict object' has no attribute 'branch'`)."""
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2_EDITED, M3]}))
    ckpt = _ckpt(tmp_path, fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2_EDITED, M3])
    entry = _entry(tmp_path, branch="change/1-a")

    report = dr.resume(entry, web_port=42020)
    assert report["mode"] == "fresh-run-remaining"
    inputs = _inputs_from_argv(spawned["argv"])
    assert inputs["branch"] == "change/1-a"
    assert inputs["change_id"] == "1-a"
    # No box/issue/repo_gh on this entry -- none of the box-tier or mirror
    # inputs should be forced in.
    for absent in (
        "notify_repo",
        "notify_issue",
        "box",
        "worktree",
        "commit_dry_run",
        "push_dry_run",
        "notify_dry_run",
    ):
        assert absent not in inputs


def test_fresh_run_defaults_branch_when_entry_branch_missing(monkeypatch, tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2_EDITED, M3]}))
    ckpt = _ckpt(tmp_path, fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2_EDITED, M3])
    entry = _entry(tmp_path, branch="")

    dr.resume(entry, web_port=42021)
    inputs = _inputs_from_argv(spawned["argv"])
    assert inputs["branch"] == "change/1-a"


def test_fresh_run_carries_box_mirror_and_dry_run_inputs(monkeypatch, tmp_path):
    """Full input-gap audit vs orchestration.launch.change: box tier +
    GitHub mirror inputs (notify_repo/notify_issue/commit_dry_run/
    push_dry_run/notify_dry_run) must also be forwarded, matching launch's
    behavior for a box-enabled, mirror-resolved run."""
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2_EDITED, M3]}))
    ckpt = _ckpt(tmp_path, fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2_EDITED, M3])
    monkeypatch.setattr(dr, "health_probe", lambda box, **kw: _probe_report(True))
    entry = _entry(tmp_path, box="box-1", branch="change/1-a", issue=42, repo_gh="kentra-io/proj")

    dr.resume(entry, web_port=42022)
    inputs = _inputs_from_argv(spawned["argv"])
    assert inputs["branch"] == "change/1-a"
    assert inputs["change_id"] == "1-a"
    assert inputs["box"] == "box-1"
    assert inputs["worktree"] == entry["worktree"]
    assert inputs["notify_repo"] == "kentra-io/proj"
    assert inputs["notify_issue"] == "42"
    assert inputs["commit_dry_run"] == "false"
    assert inputs["push_dry_run"] == "false"
    assert inputs["notify_dry_run"] == "false"


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
    ckpt = _ckpt(tmp_path, fixture, [M1, M2], cursor=1)
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2])
    monkeypatch.setattr(dr, "health_probe", lambda box, **kw: _probe_report(True))
    entry = _entry(tmp_path, box="box-1")

    report = dr.resume(entry, web_port=42013)
    assert report["mode"] == "resume-in-place"
    assert spawned["argv"]


def test_box_preflight_heals_via_cb_login_then_proceeds(monkeypatch, tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2]}))
    ckpt = _ckpt(tmp_path, fixture, [M1, M2], cursor=1)
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


def test_preflight_skips_cb_login_heal_under_env_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_LONG_LIVED_TOKEN", "sk-ant-oat01-x")
    monkeypatch.setattr(dr, "health_probe", lambda box, **kw: _probe_report(False))

    def fake_run(argv, **kwargs):
        raise AssertionError(f"cb login heal must not run under env auth, got {argv!r}")

    monkeypatch.setattr(dr.subprocess, "run", fake_run)

    report = dr.preflight_box_auth("box-1", str(tmp_path / "wt"))

    assert report["ok"] is False
    assert report["classified"] == "oauth-expired"
    assert "login" not in report


def test_resume_fallback_remedy_names_cb_login_without_env_auth(monkeypatch, tmp_path):
    """A remedy-less verdict (e.g. a probe timeout) with no remedy field must
    still fall back to something — legacy (no env token) path names `cb
    login`."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    monkeypatch.setattr(
        dr,
        "health_probe",
        lambda box, **kw: {"ok": False, "classified": None, "remedy": None, "detail": "timeout"},
    )

    def fake_run(argv, **kwargs):
        class P:
            returncode = 1
            stdout = ""
            stderr = "still down"

        return P()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    entry = _entry(tmp_path, box="box-1")
    try:
        dr.resume(entry, web_port=42018)
        raise AssertionError("expected ResumeError")
    except dr.ResumeError as exc:
        assert "cb login" in str(exc)


def test_resume_fallback_remedy_is_token_aware_under_env_auth(monkeypatch, tmp_path):
    """Same remedy-less verdict, but under CLAUDE_CODE_LONG_LIVED_TOKEN the
    box has no session file for `cb login` to heal — the fallback must name
    `orch auth mint` + a daemon restart instead (harness issue #3)."""
    monkeypatch.setenv("CLAUDE_CODE_LONG_LIVED_TOKEN", "sk-ant-oat01-x")
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    monkeypatch.setattr(
        dr,
        "health_probe",
        lambda box, **kw: {"ok": False, "classified": None, "remedy": None, "detail": "timeout"},
    )

    def fake_run(argv, **kwargs):
        raise AssertionError(f"cb login heal must not run under env auth, got {argv!r}")

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    entry = _entry(tmp_path, box="box-1")
    try:
        dr.resume(entry, web_port=42019)
        raise AssertionError("expected ResumeError")
    except dr.ResumeError as exc:
        msg = str(exc)
        assert "orch auth mint" in msg
        assert "cb login" not in msg


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


# --- checkpoint input healing (harness: 007's third live death 2026-07-27) ---
#
# `conductor resume` has no `--input` flag -- resume-in-place takes its
# inputs SOLELY from the checkpoint file, and checkpoints saved by OLDER
# workflow versions lack inputs the CURRENT execute-change.yaml now renders
# (`branch`, `notify_repo`, ...). The engine does not backfill schema
# defaults on the resume path, so the daemon must heal the checkpoint copy
# it hands to `--from` itself.


def test_heal_checkpoint_inputs_backfills_missing_keys(tmp_path):
    ckpt_path = tmp_path / "ckpt.json"
    _write_raw_checkpoint(
        ckpt_path,
        inputs={
            "change_id": "1-a",
            "commit_dry_run": "true",  # differs from the passed-in value below
            "worktree": "/wt",
        },
    )
    original_bytes = ckpt_path.read_bytes()

    healed = dr.heal_checkpoint_inputs(
        ckpt_path,
        tmp_path,
        {
            "change_id": "1-a",
            "branch": "change/1-a",
            "notify_repo": "kentra-io/proj",
            "commit_dry_run": "false",
        },
    )

    assert healed != ckpt_path
    assert healed.name == f"healed-{ckpt_path.name}"
    healed_data = json.loads(healed.read_text())
    # missing keys backfilled, in BOTH dicts
    assert healed_data["inputs"]["branch"] == "change/1-a"
    assert healed_data["inputs"]["notify_repo"] == "kentra-io/proj"
    assert healed_data["context"]["workflow_inputs"]["branch"] == "change/1-a"
    assert healed_data["context"]["workflow_inputs"]["notify_repo"] == "kentra-io/proj"
    # existing values win -- never overwritten, even when the passed-in set disagrees
    assert healed_data["inputs"]["commit_dry_run"] == "true"
    assert healed_data["context"]["workflow_inputs"]["commit_dry_run"] == "true"
    # untouched keys survive
    assert healed_data["inputs"]["change_id"] == "1-a"
    assert healed_data["inputs"]["worktree"] == "/wt"
    # original checkpoint is NEVER modified
    assert ckpt_path.read_bytes() == original_bytes


def test_heal_checkpoint_inputs_noop_when_nothing_missing(tmp_path):
    ckpt_path = tmp_path / "ckpt.json"
    _write_raw_checkpoint(ckpt_path, inputs={"change_id": "1-a", "branch": "change/1-a"})

    result = dr.heal_checkpoint_inputs(
        ckpt_path, tmp_path, {"change_id": "1-a", "branch": "change/1-a"}
    )

    assert result == ckpt_path
    assert not (tmp_path / f"healed-{ckpt_path.name}").exists()


def test_resume_in_place_heals_checkpoint_missing_branch(monkeypatch, tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2]}))
    ckpt = _ckpt(tmp_path, fixture, [M1, M2], cursor=1, raw_inputs={"change_id": "1-a"})
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2])
    entry = _entry(tmp_path, branch="change/1-a")

    report = dr.resume(entry, web_port=42030)

    assert report["mode"] == "resume-in-place"
    argv = spawned["argv"]
    from_path = Path(argv[argv.index("--from") + 1])
    assert from_path != ckpt.file_path
    assert from_path.name.startswith("healed-")
    assert report["healed_checkpoint"] == str(from_path)
    healed_data = json.loads(from_path.read_text())
    assert healed_data["inputs"]["branch"] == "change/1-a"
    assert healed_data["context"]["workflow_inputs"]["branch"] == "change/1-a"


def test_resume_in_place_uses_original_checkpoint_when_inputs_complete(monkeypatch, tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(json.dumps({"milestones": [M1, M2]}))
    ckpt = _ckpt(
        tmp_path,
        fixture,
        [M1, M2],
        cursor=1,
        raw_inputs={"change_id": "1-a", "branch": "change/1-a"},
    )
    spawned = _wire(monkeypatch, tmp_path, ckpt, [M1, M2])
    entry = _entry(tmp_path, branch="change/1-a")

    report = dr.resume(entry, web_port=42031)

    assert report["mode"] == "resume-in-place"
    argv = spawned["argv"]
    from_path = Path(argv[argv.index("--from") + 1])
    assert from_path == ckpt.file_path
    assert report["healed_checkpoint"] is None
