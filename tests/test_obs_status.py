import os

from orchestration.obs import registry
from orchestration.obs.status import Signals, collect, derive_state, tail_file


def _entry(**inc):
    e = registry.new_entry(
        repo="r", change_id="1-a", worktree="w", branch="b", box=None, tmpdir="t"
    )
    if inc:
        e["incarnations"].append(
            {
                "pid": 1,
                "started_at": "x",
                "web_port": None,
                "exit_code": None,
                "classified": None,
                **inc,
            }
        )
    return e


def test_no_incarnations_is_registered():
    assert derive_state(_entry(), Signals(None, None, None, None))["state"] == "registered"


def test_running_when_pid_alive():
    s = derive_state(_entry(), Signals(True, None, 10.0, 10.0))
    assert s["state"] == "running" and s["stalled"] is False


def test_running_stalled_when_both_signals_old():
    s = derive_state(_entry(), Signals(True, None, 700.0, 700.0), stall_threshold_s=600)
    assert s["state"] == "running" and s["stalled"] is True


def test_dead_pid_without_exit_is_unreconciled():
    assert derive_state(_entry(), Signals(False, None, None, None))["state"] == "dead: unreconciled"


def test_classified_exits_map_to_states():
    assert (
        derive_state(_entry(exit_code=0, classified="success"), Signals(False, None, None, None))[
            "state"
        ]
        == "done"
    )
    assert (
        derive_state(
            _entry(exit_code=1, classified="gate-pause"), Signals(False, None, None, None)
        )["state"]
        == "paused: gate"
    )
    assert (
        derive_state(
            _entry(exit_code=1, classified="oauth-expired"), Signals(False, None, None, None)
        )["state"]
        == "dead: oauth-expired"
    )


def test_tail_file(tmp_path):
    p = tmp_path / "log"
    p.write_bytes(b"x" * 10000 + b"THE END")
    assert tail_file(p, max_bytes=100).endswith("THE END")
    assert tail_file(tmp_path / "missing") == ""


def test_collect_reads_real_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "reg"))
    wt = tmp_path / "wt"
    (wt / ".conductor-tmp" / "checkpoints").mkdir(parents=True)
    (wt / "src").mkdir()
    (wt / "src" / "a.txt").write_text("hi")
    events = wt / ".conductor-tmp" / "checkpoints" / "run.events.jsonl"
    events.write_text("{}\n")
    entry = registry.new_entry(
        repo="r",
        change_id="1-a",
        worktree=str(wt),
        branch="b",
        box=None,
        tmpdir=str(wt / ".conductor-tmp"),
    )
    entry["incarnations"].append(
        {
            "pid": os.getpid(),
            "started_at": "x",
            "web_port": None,
            "exit_code": None,
            "classified": None,
        }
    )
    sig = collect(entry)
    assert sig.pid_alive is True
    assert sig.events_age_s is not None and sig.events_age_s < 60
    assert sig.worktree_mtime_age_s is not None and sig.worktree_mtime_age_s < 60
