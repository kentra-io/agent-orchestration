import json
import os
import subprocess
import sys
from datetime import datetime

import orchestration.daemon.github_mirror as gm
from orchestration.daemon.supervise import Supervisor, _classify_from_entry
from orchestration.obs import registry


def _write_root_event(tmpdir, event_type):
    checkpoints = tmpdir / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    (checkpoints / "run.events.jsonl").write_text(
        json.dumps({"type": event_type, "data": {}}) + "\n"
    )


def _register(tmp_path, change_id="1-a", pid=None):
    wt = tmp_path / f"wt-{change_id}"
    tmpdir = wt / ".conductor-tmp"
    tmpdir.mkdir(parents=True)
    entry = registry.new_entry(
        repo="r", change_id=change_id, worktree=str(wt), branch="b", box=None, tmpdir=str(tmpdir)
    )
    registry.write_entry(entry)
    registry.append_incarnation(
        "r",
        change_id,
        {"pid": pid, "started_at": "x", "web_port": None, "exit_code": None, "classified": None},
    )
    return tmpdir


def test_poll_once_classifies_exited_child(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])
    tmpdir = _register(tmp_path, pid=proc.pid)
    (tmpdir / "conductor.stdout.log").write_text("OAuth session expired")
    (tmpdir / "conductor.stderr.log").write_text("")
    sup = Supervisor()
    sup.adopt("r", "1-a", proc)
    proc.wait()
    events = sup.poll_once()
    assert events and events[0]["classified"] == "oauth-expired"
    loaded = registry.load_entry("r", "1-a")
    assert loaded["incarnations"][-1]["exit_code"] == 1
    assert loaded["incarnations"][-1]["classified"] == "oauth-expired"


def test_poll_once_keeps_running_children(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _register(tmp_path, pid=proc.pid)
        sup = Supervisor()
        sup.adopt("r", "1-a", proc)
        assert sup.poll_once() == []
        assert sup.tracked() == 1
    finally:
        proc.kill()


def test_reconcile_classifies_orphaned_death(tmp_path, monkeypatch):
    """A run that died while the daemon was down: pid gone, exit never seen."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    tmpdir = _register(tmp_path, pid=99999999)  # nonexistent pid
    (tmpdir / "conductor.stdout.log").write_text("API Error: Connection closed mid-response")
    sup = Supervisor()
    events = sup.reconcile()
    assert events and events[0]["classified"] == "api-transient"
    loaded = registry.load_entry("r", "1-a")
    assert loaded["incarnations"][-1]["classified"] == "api-transient"
    assert loaded["incarnations"][-1]["reconciled"] is True


def test_reconcile_classifies_live_pid_with_failed_root_event(tmp_path, monkeypatch):
    """harness issue #3, defect B: reconcile used to skip any live pid
    outright, so a run whose ROOT workflow already recorded workflow_failed
    kept reading as running for as long as the (stuck) process lingered.
    reconcile and poll_once are mutually exclusive via the `_procs` guard
    (this entry is never adopted, so poll_once never touches it); the
    classified-set check at the top of reconcile's loop prevents it being
    re-processed on the next tick; and the mirror's per-incarnation
    `terminal` fact prevents a double-post regardless."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    tmpdir = _register(tmp_path, pid=os.getpid())  # alive by construction
    _write_root_event(tmpdir, "workflow_failed")
    (tmpdir / "conductor.stdout.log").write_text("")
    (tmpdir / "conductor.stderr.log").write_text("OAuth session expired and could not be refreshed")
    sup = Supervisor()
    events = sup.reconcile()
    assert events and events[0]["classified"] == "oauth-expired"
    loaded = registry.load_entry("r", "1-a")
    assert loaded["incarnations"][-1]["classified"] == "oauth-expired"
    assert loaded["incarnations"][-1]["reconciled"] is True


def test_classify_from_entry_reads_agent_messages(tmp_path, monkeypatch):
    """harness issue #3: the OAuth death text arrived ONLY as an
    `agent_message` event (conductor's stdout/stderr logs stayed empty), so
    the classifier never saw it. `_classify_from_entry` must also feed the
    events-file agent_message text into `classify`."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    tmpdir = _register(tmp_path)
    (tmpdir / "conductor.stdout.log").write_text("")
    (tmpdir / "conductor.stderr.log").write_text("")
    checkpoints = tmpdir / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    (checkpoints / "run.events.jsonl").write_text(
        json.dumps(
            {
                "type": "agent_message",
                "data": {"content": "Failed to authenticate: OAuth session expired"},
            }
        )
        + "\n"
    )
    entry = registry.load_entry("r", "1-a")
    verdict = _classify_from_entry(entry, 1)
    assert verdict.kind == "oauth-expired"


def test_classify_from_entry_scopes_events_to_current_incarnation(tmp_path, monkeypatch):
    """Bug B (harness resume-input-and-event-scoping): a resumed run appends
    events into the PRIOR incarnation's relocated `*.events.jsonl` (only the
    stdout/stderr logs get truncated per-incarnation -- resume.py:216-222).
    A stale OAuth-death `agent_message` from BEFORE the current incarnation
    started must not misclassify a real, different, CURRENT death (here: a
    template-render crash surfaced only on stderr)."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    tmpdir = _register(tmp_path)
    started_at = "2026-07-24T12:00:00+00:00"
    registry.update_incarnation("r", "1-a", started_at=started_at)
    started_ts = datetime.fromisoformat(started_at).timestamp()

    (tmpdir / "conductor.stdout.log").write_text("")
    (tmpdir / "conductor.stderr.log").write_text(
        "Failed to render input_mapping key 'branch' for agent 'milestone_step': "
        "Undefined variable in template: 'dict object' has no attribute 'branch'"
    )
    checkpoints = tmpdir / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    (checkpoints / "run.events.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "type": "agent_message",
                    "data": {"content": "Failed to authenticate: OAuth session expired"},
                    "timestamp": started_ts - 3600,  # PRIOR incarnation's death text
                },
                {"type": "workflow_failed", "data": {}, "timestamp": started_ts + 1},
            ]
        )
        + "\n"
    )
    entry = registry.load_entry("r", "1-a")
    verdict = _classify_from_entry(entry, 1)
    assert verdict.kind != "oauth-expired"
    assert verdict.kind == "unknown"


def test_classify_from_entry_unparseable_started_at_keeps_old_behavior(tmp_path, monkeypatch):
    """`_register`'s started_at ("x") is not ISO-8601 -- the cutoff must fall
    back to None (old, unscoped behavior) rather than raise."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    tmpdir = _register(tmp_path)
    (tmpdir / "conductor.stdout.log").write_text("")
    (tmpdir / "conductor.stderr.log").write_text("")
    checkpoints = tmpdir / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    (checkpoints / "run.events.jsonl").write_text(
        json.dumps(
            {
                "type": "agent_message",
                "data": {"content": "Failed to authenticate: OAuth session expired"},
                "timestamp": 1.0,
            }
        )
        + "\n"
    )
    entry = registry.load_entry("r", "1-a")
    verdict = _classify_from_entry(entry, 1)
    assert verdict.kind == "oauth-expired"


def test_events_carry_remedy_and_detail(tmp_path, monkeypatch):
    """The mirror needs the verdict's remedy + the REAL error text, so the
    supervision events must carry them (spec: run death surfaced with the real
    error, not a masked exit)."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])
    tmpdir = _register(tmp_path, pid=proc.pid)
    (tmpdir / "conductor.stdout.log").write_text("OAuth session expired")
    (tmpdir / "conductor.stderr.log").write_text("")
    sup = Supervisor()
    sup.adopt("r", "1-a", proc)
    proc.wait()
    events = sup.poll_once()
    assert events[0]["remedy"] and "cb login" in events[0]["remedy"]
    assert "OAuth" in events[0]["detail"]


def test_terminal_event_records_dedupe_fact_on_incarnation(tmp_path, monkeypatch):
    """The supervise leg feeds a real classified event to the mirror, which lands
    a `mirror.terminal` dedupe fact on the incarnation (hermetic registry dir,
    fake gh client)."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])
    tmpdir = _register(tmp_path, pid=proc.pid)
    (tmpdir / "conductor.stdout.log").write_text("API Error: Connection closed")
    # Make the entry mirrorable (production facts) without a real gh call.
    entry = registry.load_entry("r", "1-a")
    entry["repo_gh"] = "kentra-io/r"
    entry["issue"] = 5
    registry.write_entry(entry)
    monkeypatch.setattr(gm, "comment", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(gm, "add_label", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(gm, "ensure_label", lambda *a, **k: {"ok": True})
    sup = Supervisor()
    sup.adopt("r", "1-a", proc)
    proc.wait()
    [event] = sup.poll_once()
    gm.mirror_terminal(registry.load_entry("r", "1-a"), event)
    loaded = registry.load_entry("r", "1-a")
    assert loaded["incarnations"][-1]["mirror"]["terminal"] is True
