import pytest
from testbed import Testbed, materialize_testbed


@pytest.fixture
def testbed(tmp_path) -> Testbed:
    """A fresh, real git repo (see tests/testbed.py) for each test."""
    return materialize_testbed(tmp_path / "testbed")


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    """Hermetic isolation: launch()/registry tests must never write into (or
    read stale state from) the real `~/.agent-orchestration/runs`."""
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path / "registry"))


@pytest.fixture(autouse=True)
def _isolate_daemon_config(tmp_path_factory, monkeypatch):
    """Point the CLI config at a nonexistent per-session file so a developer's
    real ~/.agent-orchestration/daemon.json can never leak into tests."""
    monkeypatch.setenv(
        "ORCHESTRATION_CONFIG_PATH",
        str(tmp_path_factory.mktemp("cli-config") / "daemon.json"),
    )
