"""Pure exit classifier — design §5.3, precedence order fixed there.

`gate-pause` is the BY-DESIGN non-zero exit of the crash-then-resume gate
model (orchestration/resume/README.md): the process EOF-crashes at a
human_gate after checkpointing. It must never read as a death.
"""

from __future__ import annotations

from dataclasses import dataclass

GATE_AGENTS = ("human_gate", "milestone_step")


@dataclass(frozen=True)
class Verdict:
    kind: str  # success | gate-pause | oauth-expired | api-transient | unknown
    remedy: str | None
    detail: str


def classify(
    exit_code: int | None,
    stdout_tail: str = "",
    stderr_tail: str = "",
    checkpoint_agent: str | None = None,
) -> Verdict:
    text = f"{stdout_tail}\n{stderr_tail}"
    if exit_code == 0:
        return Verdict("success", None, "")
    if checkpoint_agent in GATE_AGENTS or "EOFError" in text:
        return Verdict(
            "gate-pause",
            "expected pause: resolve via the issue label + `conductor resume`",
            text.strip(),
        )
    if "OAuth" in text and ("expired" in text or "could not be refreshed" in text):
        return Verdict(
            "oauth-expired", "run `cb login` from the worktree, then resume", text.strip()
        )
    if "API Error" in text or "Connection closed" in text or "overloaded" in text.lower():
        return Verdict("api-transient", "transient provider failure: resume the run", text.strip())
    return Verdict("unknown", None, text.strip())
