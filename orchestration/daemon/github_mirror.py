"""The daemon's GitHub mirror — process-level lifecycle truths on the issue.

Two layers live here (design.md D5):

1. A small `gh`-shelling **client** (`comment`, `add_label`, `ensure_label`,
   `close_issue`, `list_comments`, `patch_comment`) with the
   `notify_escalation` failure posture — every call is `check=False`, captures a
   stderr tail, NEVER raises, and returns a dict carrying `gh_exit_code` /
   `gh_stderr_tail`. It is importable by the daemon here and by the scripts
   (close-on-archive reuses `close_issue`).

2. A daemon-side **notifier** (`mirror_started`, `mirror_terminal`) that maps
   supervision facts to writes: a run-started/-resumed comment at adopt time, a
   run-finished comment on `success`, and — for any death classification that is
   neither `success` nor the by-design `gate-pause` — the `run-died` label plus
   a death comment carrying the classified cause, its remedy, and the REAL error
   text (`verdict.detail`), never the masked "exited code 1, no stderr".

Writes fire only for entries carrying BOTH the `repo_gh` and `issue` facts —
recorded only by production launches, so hermetic registry entries (CI) lack
them and the daemon never shells `gh` or needs a token. Every performed write is
recorded as a dedupe fact on the incarnation (`mirror: {started, terminal}`) and
checked before writing, so a restarted daemon or a later `reconcile` pass — for
daemon-launched runs and `--direct` runs mirrored lazily alike — never
double-posts.

`gate-pause` is deliberately silent: the ladder's `escalate` step already owns
the `needs-human-input` label, and nothing on this daemon path ever applies
`needs-human-input` or applies `run-died` to a plan escalation — the two labels
stay distinct (spec: "Distinct labels for infra death and plan escalation").
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from orchestration.harness.common import tail
from orchestration.obs import registry

RUN_DIED_LABEL = "run-died"

# Labels ensured this process (best-effort `gh label create`), so a fresh
# consumer repo needs no manual label bootstrap and we attempt creation at most
# once per (repo, label) per daemon process.
_ensured_labels: set[tuple[str, str]] = set()


def _gh(args: list[str]) -> tuple[int | None, str, str]:
    """Run `gh` best-effort; return (exit_code_or_None, stdout, stderr). Never
    raises — a missing `gh` binary surfaces as exit None with the reason."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        return None, "", f"gh could not run: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def _result(code: int | None, stderr: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": code == 0,
        "gh_exit_code": code,
        "gh_stderr_tail": tail(stderr) if stderr else None,
        **extra,
    }


# --- the raw gh client (best-effort, never raises) --------------------------


def comment(repo: str, issue: int, body: str) -> dict[str, Any]:
    """Post a comment to the issue."""
    code, _out, err = _gh(
        ["api", "-X", "POST", f"repos/{repo}/issues/{issue}/comments", "-f", f"body={body}"]
    )
    return _result(code, err)


def add_label(repo: str, issue: int, label: str) -> dict[str, Any]:
    """Add a label to the issue."""
    code, _out, err = _gh(["issue", "edit", str(issue), "--repo", repo, "--add-label", label])
    return _result(code, err, label=label)


def ensure_label(repo: str, label: str) -> dict[str, Any]:
    """Best-effort `gh label create`, cached per process. Creation failing
    because the label already exists is fine — this only bootstraps a fresh
    consumer repo. We cache the (repo, label) attempt regardless of outcome so
    we shell `gh` at most once per process."""
    key = (repo, label)
    if key in _ensured_labels:
        return {"ok": True, "cached": True, "gh_exit_code": None, "gh_stderr_tail": None}
    code, _out, err = _gh(["label", "create", label, "--repo", repo])
    _ensured_labels.add(key)
    return _result(code, err, cached=False)


def close_issue(repo: str, issue: int, body: str) -> dict[str, Any]:
    """Close the issue with a closing comment (used by close-on-archive)."""
    code, _out, err = _gh(["issue", "close", str(issue), "--repo", repo, "--comment", body])
    return _result(code, err)


def list_comments(repo: str, issue: int) -> dict[str, Any]:
    """List the issue's comments (paginated). `comments` is the decoded list or
    empty on any failure."""
    code, out, err = _gh(["api", f"repos/{repo}/issues/{issue}/comments", "--paginate"])
    comments: Any = []
    if code == 0 and out.strip():
        try:
            comments = json.loads(out)
        except json.JSONDecodeError:
            comments = []
    return _result(code, err, comments=comments if isinstance(comments, list) else [])


def patch_comment(repo: str, comment_id: int, body: str) -> dict[str, Any]:
    """Edit an existing comment in place."""
    code, _out, err = _gh(
        ["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{comment_id}", "-f", f"body={body}"]
    )
    return _result(code, err)


# --- comment bodies ---------------------------------------------------------


def _started_body(change_id: str, branch: str, resumed: bool) -> str:
    verb = "resumed" if resumed else "started"
    icon = "▶️" if resumed else "🚀"
    branch_part = f" on branch `{branch}`" if branch else ""
    return (
        f"{icon} Run {verb} for `{change_id}`{branch_part}.\n\n"
        "_Posted by the agent-orchestration daemon. Local state is the "
        "source of truth; run `orch status " + change_id + "` for the "
        "authoritative view._"
    )


def _finished_body(change_id: str) -> str:
    return (
        f"✅ Run finished for `{change_id}` — classified `success`.\n\n"
        "_Posted by the agent-orchestration daemon._"
    )


def _death_body(change_id: str, kind: str, remedy: str | None, detail: str) -> str:
    lines = [
        f"🔴 Run died for `{change_id}` — classified `{kind}`.",
        "",
        f"**Cause:** {kind}",
        f"**Remedy:** {remedy or 'fix the infrastructure cause, then resume the run'}",
        "",
        "**Captured error:**",
        "",
        "```",
        detail.strip() if detail and detail.strip() else "(no error text captured)",
        "```",
        "",
        "_Posted by the agent-orchestration daemon. The `run-died` label marks an "
        "infrastructure/runtime failure (fix the infra + resume) — distinct from "
        "`needs-human-input` (a plan escalation)._",
    ]
    return "\n".join(lines)


# --- dedupe facts -----------------------------------------------------------


def _mirror_facts(entry: dict[str, Any]) -> dict[str, Any]:
    incs = entry.get("incarnations") or []
    if not incs:
        return {}
    return dict(incs[-1].get("mirror") or {})


def _record_fact(slug: str, change_id: str, key: str) -> None:
    """Merge `key: True` into the last incarnation's `mirror` fact. Reloads
    fresh so concurrent facts (started + terminal) are not clobbered."""
    fresh = registry.load_entry(slug, change_id)
    if fresh is None or not fresh.get("incarnations"):
        return
    facts = dict(fresh["incarnations"][-1].get("mirror") or {})
    facts[key] = True
    registry.update_incarnation(slug, change_id, mirror=facts)


# --- the notifier -----------------------------------------------------------


def _resolve(entry: dict[str, Any]) -> tuple[str, int] | None:
    """Return (repo_gh, issue) only when BOTH facts are present — otherwise the
    entry is not a mirrorable production launch and we stay silent."""
    repo = entry.get("repo_gh")
    issue = entry.get("issue")
    if not repo or not isinstance(repo, str) or not isinstance(issue, int):
        return None
    return repo, issue


def mirror_started(entry: dict[str, Any], resumed: bool = False) -> dict[str, Any]:
    """Post a run-started (or run-resumed) comment, once per incarnation."""
    resolved = _resolve(entry)
    if resolved is None:
        return {"skipped": "no repo_gh/issue facts"}
    repo, issue = resolved
    slug = entry["repo_slug"]
    change_id = entry["change_id"]

    fresh = registry.load_entry(slug, change_id) or entry
    if _mirror_facts(fresh).get("started"):
        return {"skipped": "already mirrored started"}

    result = comment(repo, issue, _started_body(change_id, entry.get("branch") or "", resumed))
    _record_fact(slug, change_id, "started")
    return {"action": "resumed" if resumed else "started", "comment": result}


def mirror_terminal(entry: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Map a supervision terminal event to issue writes, once per incarnation.

    success ⇒ run-finished comment; gate-pause ⇒ nothing (not a death);
    any other classification ⇒ ensure + add `run-died` label and a death comment
    with the classified cause, remedy, and the real error text.
    """
    resolved = _resolve(entry)
    if resolved is None:
        return {"skipped": "no repo_gh/issue facts"}
    repo, issue = resolved
    slug = event.get("slug") or entry["repo_slug"]
    change_id = event.get("change_id") or entry["change_id"]
    kind = event.get("classified")

    if kind == "gate-pause":
        return {"skipped": "gate-pause is not a death"}

    fresh = registry.load_entry(slug, change_id) or entry
    if _mirror_facts(fresh).get("terminal"):
        return {"skipped": "already mirrored terminal"}

    writes: dict[str, Any] = {}
    if kind == "success":
        writes["comment"] = comment(repo, issue, _finished_body(change_id))
    else:
        ensure_label(repo, RUN_DIED_LABEL)
        writes["label"] = add_label(repo, issue, RUN_DIED_LABEL)
        body = _death_body(
            change_id, kind or "unknown", event.get("remedy"), event.get("detail") or ""
        )
        writes["comment"] = comment(repo, issue, body)

    _record_fact(slug, change_id, "terminal")
    return {"action": "finished" if kind == "success" else "died", "writes": writes}
