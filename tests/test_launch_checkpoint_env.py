"""Unit tests for `orchestration.launch.checkpoint_env` -- the
launcher-owned half of P4/ADR-0002 (checkpoint-dir relocation a workflow
template cannot do for itself; see the module docstring for why)."""

from __future__ import annotations

import os
from pathlib import Path

from orchestration.launch.checkpoint_env import (
    persistent_checkpoint_env,
    persistent_checkpoint_subprocess_env,
)


def test_returns_tmpdir_overlay_and_creates_the_directory(tmp_path: Path) -> None:
    target = tmp_path / "checkpoints" / "nested"
    assert not target.exists()

    overlay = persistent_checkpoint_env(target)

    assert overlay == {"TMPDIR": str(target.resolve())}
    assert target.is_dir()


def test_idempotent_on_an_existing_directory(tmp_path: Path) -> None:
    target = tmp_path / "checkpoints"
    target.mkdir()
    overlay = persistent_checkpoint_env(target)
    assert overlay["TMPDIR"] == str(target.resolve())


def test_subprocess_env_merges_over_os_environ(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOME_UNRELATED_VAR", "kept")
    target = tmp_path / "checkpoints"
    env = persistent_checkpoint_subprocess_env(target)
    assert env["TMPDIR"] == str(target.resolve())
    assert env["SOME_UNRELATED_VAR"] == "kept"
    assert env["PATH"] == os.environ["PATH"]
