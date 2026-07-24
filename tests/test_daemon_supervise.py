import subprocess
import sys

import orchestration.daemon.github_mirror as gm
from orchestration.daemon.supervise import Supervisor
from orchestration.obs import registry


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
