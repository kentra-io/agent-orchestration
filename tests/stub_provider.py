"""StubProvider test double helpers — implemented in M1.

Conductor's own ``stub`` provider (fork-carried, ``kentra-patches``) reads a
JSON script file mapping step/agent name -> an ordered list of scripted
``AgentOutput``-shaped dicts (see ``conductor.providers.stub`` for the exact
format). This module is a small helper for *this repo's* tests: write a
script file into a scratch directory and hand back its path, so a workflow's
control-flow can be exercised with the real ``conductor`` CLI + ``--provider
stub`` — no LLM, no claudebox box, no network (see M1/M1b DoD).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_stub_script(dest_dir: Path, steps: dict[str, list[dict[str, Any]]]) -> Path:
    """Write a stub-provider script file (``{"steps": {...}}``) and return its path.

    Args:
        dest_dir: Directory to write the script into (typically ``tmp_path``).
        steps: Maps a workflow step/agent name to its ordered list of
            scripted entries, in the ``conductor.providers.stub`` format
            (each entry needs ``content`` unless it declares ``error``).

    Returns:
        The path to the written ``stub_script.json`` file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "stub_script.json"
    path.write_text(json.dumps({"steps": steps}, indent=2), encoding="utf-8")
    return path
