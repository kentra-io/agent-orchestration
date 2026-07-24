"""Escalation notify - the `escalate` step's GitHub-label mirror (P7).

`orchestration.md` sec 7.1: on attempt-3 failure the change's durable status
becomes `Needs human input`; the **canonical** home of that status is
Conductor's own paused `human_gate` run-state (unaffected by this module -
the ladder's `human_gate` step IS the canonical pause). This module is the
**mirror**: a `needs-human-input` label on the target GitHub issue, so a
human scanning issues (not run logs) still sees the change is stuck.

This is deliberately the thinnest possible mirror for M5 - a real
`resolve`/`gate-respond` seam (watching for the label to be cleared and
resuming the paused run) is M7's job (`orchestration/resume/`). M5 only
needs the label mirror to fire, observably, when the ladder escalates - so
the hermetic Stub-tier tests can assert "the notify step ran with label
`needs-human-input`" without a real GitHub call or token (see `dry_run`).

Calling convention (mirrors `orchestration.harness.*`, see its README):
invocable as a script (`python -m orchestration.launch.notify_escalation`,
inline JSON / file path / stdin), importable (`notify(payload) -> dict`),
emits one pretty JSON object to stdout, exit code reflects the outcome.

Input JSON:
    {
      "label": str,               # required, e.g. "needs-human-input"
      "repo": str,                 # optional, "owner/repo" - required if not dry_run
      "issue": int,                 # optional, issue number - required if not dry_run
      "dry_run": bool               # optional, default true - see below
    }

`dry_run` (default `true`) skips the real `gh issue edit --add-label` call
entirely and returns success immediately - this is what every hermetic
Stub-tier workflow run should set, so the escalation path is exercised with
**no network, no `gh`, no GitHub token** (M5 DoD: "don't require a real GH
call in the hermetic tier"). Set `dry_run: false` (live tier only, M7+) to
shell out to the real `gh` CLI.

Output JSON:
    {
      "notified": bool,
      "label": str,
      "mode": "dry_run" | "gh",
      "gh_exit_code": int | null,   # the `gh` subprocess's exit code (mode "gh" only)
      "gh_stderr_tail": str | null
    }

Note: this step is invoked from a Conductor `script` step, whose own output
is `{stdout, stderr, exit_code}` with this JSON's keys merged on top (see
`orchestration.harness.README.md`'s calling-convention note) - the `gh_`
prefix on the two `gh`-subprocess fields avoids colliding with the script
step's own top-level `exit_code`/`stderr` keys.

Process exit code: 0 if `notified`, 1 if attempted and failed (mode "gh"
only - `dry_run` always succeeds), 2 on a harness-level input error (e.g.
missing `label`, or `dry_run: false` without `repo`/`issue`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orchestration.harness.common import coerce_bool

EXIT_GOOD = 0
EXIT_ATTENTION = 1
EXIT_ERROR = 2


class NotifyInputError(ValueError):
    """The step's input JSON is missing, malformed, or fails validation."""


def _read_input(argv: Sequence[str]) -> dict[str, Any]:
    if not argv or argv[0] == "-":
        raw = sys.stdin.read()
        source = "stdin"
    else:
        candidate = argv[0]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            path = Path(candidate)
            if not path.is_file():
                raise NotifyInputError(
                    f"argv[0] is neither valid inline JSON nor an existing file path: {candidate!r}"
                ) from None
            raw = path.read_text()
            source = str(path)
        else:
            if not isinstance(data, dict):
                raise NotifyInputError(
                    f"input JSON from argv[0] (inline JSON) must be an object, "
                    f"got {type(data).__name__}"
                )
            return data

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotifyInputError(f"invalid JSON from {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise NotifyInputError(
            f"input JSON from {source} must be an object, got {type(data).__name__}"
        )
    return data


def _emit(verdict: dict[str, Any]) -> None:
    print(json.dumps(verdict, indent=2, sort_keys=True))


def notify(payload: dict[str, Any]) -> dict[str, Any]:
    label = payload.get("label")
    if not label or not isinstance(label, str):
        raise NotifyInputError("'label' (non-empty string) is required")

    dry_run = coerce_bool(payload.get("dry_run", True), default=True)
    if not dry_run:
        repo = payload.get("repo")
        issue = payload.get("issue")
        if not repo or not isinstance(repo, str):
            raise NotifyInputError(
                "'repo' (non-empty 'owner/repo' string) is required when dry_run is false"
            )
        if not isinstance(issue, int):
            raise NotifyInputError("'issue' (int) is required when dry_run is false")

        proc = subprocess.run(
            ["gh", "issue", "edit", str(issue), "--repo", repo, "--add-label", label],
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "notified": proc.returncode == 0,
            "label": label,
            "mode": "gh",
            "gh_exit_code": proc.returncode,
            "gh_stderr_tail": proc.stderr[-2000:] if proc.stderr else None,
        }

    return {
        "notified": True,
        "label": label,
        "mode": "dry_run",
        "gh_exit_code": None,
        "gh_stderr_tail": None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        verdict = notify(_read_input(argv))
    except NotifyInputError as exc:
        _emit({"error": str(exc)})
        return EXIT_ERROR
    _emit(verdict)
    return EXIT_GOOD if verdict["notified"] else EXIT_ATTENTION


if __name__ == "__main__":
    raise SystemExit(main())
