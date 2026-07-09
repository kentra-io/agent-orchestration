"""Shared fixtures for the M8 launcher tests (`test_launch_change.py`,
`test_m8_concurrency.py`): a tiny real git repo (the launcher's `repo`
input) and a minimal three-file personas directory (so `materialize_box`
tests don't depend on this repo's own real `personas/` content).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "m8-launch-test",
    "GIT_AUTHOR_EMAIL": "m8-launch-test@example.invalid",
    "GIT_COMMITTER_NAME": "m8-launch-test",
    "GIT_COMMITTER_EMAIL": "m8-launch-test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(GIT_ENV)
    return env


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=git_env()
    )


def init_repo(path: Path) -> Path:
    """A fresh git repo at `path` with one commit on `main`."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    (path / "README.md").write_text("m8 launch test repo\n", encoding="utf-8")
    _git("add", "-A", cwd=path)
    _git("commit", "-q", "-m", "base", cwd=path)
    return path


def write_personas(dest: Path) -> Path:
    """A minimal 3-file personas/ dir -- enough for `materialize_box` to copy;
    content is irrelevant (never fed to a real `claude` in the hermetic tier).
    """
    dest.mkdir(parents=True, exist_ok=True)
    for role in ("implementer", "verifier", "orchestrator"):
        (dest / f"{role}.md").write_text(f"---\nname: {role}\n---\n\nPlaceholder {role} persona.\n")
    return dest


def write_plan_fixture(path: Path, milestones: list[dict]) -> Path:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"milestones": milestones}, indent=2), encoding="utf-8")
    return path
