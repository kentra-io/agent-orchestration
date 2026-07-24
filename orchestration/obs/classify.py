"""Pure exit classifier — design §5.3, precedence order fixed there.

`gate-pause` is the BY-DESIGN non-zero exit of the crash-then-resume gate
model (orchestration/resume/README.md): the process EOF-crashes at a
human_gate after checkpointing. It must never read as a death.

`provider-exit` (issue #7 remaining tail): the provider-masking fix (fork pin
`ab0ff4c`) makes `ProviderError` carry real stderr/stdout in most deaths, so
the patterns below usually fire. But when the provider genuinely captured
nothing — the exit-code line with no stderr/stdout tail at all — there is no
cause to name. Runs checkpoint every completed milestone (design §8 /
orchestration/resume/README.md), so a resume is always safe-by-design after
*any* mid-run death: it can only replay from the last completed milestone,
never repeat one. An unexplained provider exit is exactly the case where
staying honest ("unknown cause") *and* actionable (suggest resume) both
matter — a production run died this way mid-milestone after hours of healthy
progress, and the true cause was transient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GATE_AGENTS = ("human_gate", "milestone_step")

# The historical empty-diagnostics shape: a bare provider/subprocess exit
# report with no real stderr/stdout tail, e.g. `claude subprocess exited 1
# (no diagnostics)` or the currently-vendored `claude subprocess exited with
# code 1: (no stderr or stdout diagnostics)` — anything naming the exit AND
# flagging its own diagnostics as absent. Deliberately narrow: the vendored
# provider only ever writes the word "diagnostics" into that placeholder —
# when it instead has real stderr/stdout, it substitutes that text in place
# of the placeholder, so a message carrying real content won't contain
# "diagnostics" and falls through to the oauth/api patterns above (or to bare
# "unknown") instead of landing here.
_SUBPROCESS_EXIT_RE = re.compile(r"subprocess exited\b", re.IGNORECASE)
_EMPTY_DIAGNOSTICS_RE = re.compile(r"\bdiagnostics\b", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    kind: str  # success | gate-pause | oauth-expired | api-transient | provider-exit | unknown
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
    if _SUBPROCESS_EXIT_RE.search(text) and _EMPTY_DIAGNOSTICS_RE.search(text):
        return Verdict(
            "provider-exit",
            "cause unknown (provider captured no diagnostics), but completed "
            "milestones are checkpointed — safe to resume: `orch resume <change-id>`",
            text.strip(),
        )
    return Verdict("unknown", None, text.strip())
