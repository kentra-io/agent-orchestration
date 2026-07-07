"""A deterministically flaky test - flaky under control, not `random`.

Each `pytest` invocation is a fresh process, so "flaky across N reruns"
needs state that survives across processes: a plain counter file. On every
run we read-increment-write an integer, then fail exactly every
`FLAKY_FAIL_EVERY`-th run (0-indexed: run #0, #FAIL_EVERY, #2*FAIL_EVERY, ...).

Given a fresh (or reset) counter file, the pass/fail sequence over N runs is
a pure function of N and FLAKY_FAIL_EVERY - fully reproducible, no
`random`, no wall-clock dependence. `tests/testbed.py::Testbed.reset_flaky_state`
is what resets it between test-suite runs of the harness's own tests.
"""

import os
from pathlib import Path

STATE_FILE = Path(os.environ.get("FLAKY_STATE_FILE", ".flaky_state"))
FAIL_EVERY = int(os.environ.get("FLAKY_FAIL_EVERY", "4"))


def _next_run_index() -> int:
    n = int(STATE_FILE.read_text()) if STATE_FILE.exists() else 0
    STATE_FILE.write_text(str(n + 1))
    return n


def test_flaky_by_counter():
    n = _next_run_index()
    assert n % FAIL_EVERY != 0, (
        f"deterministic flake: run #{n} (fails every {FAIL_EVERY}th run, 0-indexed)"
    )
