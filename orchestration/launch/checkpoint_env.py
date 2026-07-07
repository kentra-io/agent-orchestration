"""Checkpoint-dir relocation - the launcher-owned half of P4/ADR-0002.

Conductor resolves its checkpoint directory (and the per-run event JSONL
directory) via `tempfile.gettempdir()`, which honors the `TMPDIR` environment
variable on POSIX (see `workflows/README.md` "Checkpoint relocation (S2
answer)"). There is no workflow-YAML key for this - `runtime.checkpoint`
only configures *when* a checkpoint is taken (`every_agent`, `every_seconds`,
`keep_last`), never *where* it is written. A shipped **template** can
therefore only ever do its half of ADR-0002 (`every_agent: true` + a computed
`max_iterations`); relocating the directory itself is necessarily a property
of the *process launching* `conductor`, not of the YAML it runs.

This module is that launcher-side half: a small, reusable helper so every
caller (this milestone's tests, and the real M8 launcher later) sets `TMPDIR`
the same way instead of each reinventing it - and so forgetting it is a
one-line diff to fix, not a silent footgun. See the ADR-0002 reconciliation
note in `workflows/README.md` for why the ADR's wording ("every template
MUST relocate the checkpoint dir") is aspirational-at-the-template-layer and
actually discharged here.
"""

from __future__ import annotations

import os
from pathlib import Path


def persistent_checkpoint_env(persistent_root: str | Path) -> dict[str, str]:
    """Return an environment overlay that relocates Conductor's checkpoint dir.

    Args:
        persistent_root: A directory that survives the process that writes
            to it (i.e. NOT the default `$TMPDIR`/`/tmp`, which on some
            platforms - notably this harness's own dev containers - is a
            small tmpfs that is wiped or size-capped independently of the
            `conductor` process's lifetime). Created if it does not exist.

    Returns:
        A `{"TMPDIR": str(resolved_path)}` overlay - merge this into the
        environment passed to `conductor run`/`conductor resume`
        (e.g. `env={**os.environ, **persistent_checkpoint_env(...)}`).

    This is the one config knob P4/ADR-0002 requires that a workflow
    template cannot set for itself (see the module docstring) - every
    launcher (this repo's tests, and the M8 launcher) MUST apply it, or
    crash-safe attempt counting silently degrades to Conductor's
    failure-only-checkpoint default.
    """
    root = Path(persistent_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {"TMPDIR": str(root)}


def persistent_checkpoint_subprocess_env(persistent_root: str | Path) -> dict[str, str]:
    """Like `persistent_checkpoint_env`, merged over a copy of `os.environ`.

    Convenience for callers building a full `env=` dict for
    `subprocess.run`/`asyncio.create_subprocess_exec` rather than merging
    the overlay themselves.
    """
    return {**os.environ, **persistent_checkpoint_env(persistent_root)}
