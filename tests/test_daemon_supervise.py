import subprocess
import sys

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
