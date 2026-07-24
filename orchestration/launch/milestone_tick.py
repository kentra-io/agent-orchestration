"""Deterministic per-milestone checklist tick (the edited-in-place issue mirror).

`milestone.yaml`'s `tick` step calls this module after `milestone_push`, so the
change's GitHub issue carries a single, always-current checklist of the run's
milestones (spec `github-mirror`: "Milestone progress mirrored as one
edited-in-place checklist"). It is the mirror counterpart to
`milestone_commit`/`milestone_push`: commit is the load-bearing durability
finish, push best-effort publishes the branch, and this step best-effort
projects that progress onto the issue. Like `notify_escalation`, every `gh`
side effect here is best effort — attempted, result recorded, NEVER raised, and
never a reason to fail the milestone or halt the run (design.md D1/D2, ADR-0005).

The checklist is **one** comment, located idempotently by a stable first-line
HTML marker `<!-- agent-orchestration:mirror:<change_id> -->` (design.md D4).
Each tick re-renders the WHOLE body from the milestone manifest, the checked
state parsed back out of the existing comment, and the current milestone's push
result — so the comment is self-healing (a garbled or hand-edited body is
repaired by the next full re-render) and a passing milestone edits in place
rather than posting a second comment. A completed milestone whose push failed is
still checked, but annotated `(local-only: push failed — <reason>)`; a later
tick whose push actually landed clears every such annotation, because the
accumulated branch is now published.

Authority note (same consent boundary as `milestone_commit`/`milestone_push`):
the mirror verb stays out of every LLM's hands — a `script` step run by the
launch context, never a cast persona.

Calling convention (mirrors `orchestration.harness.*` / `milestone_push` /
`notify_escalation`): invocable as a script
(`python -m orchestration.launch.milestone_tick`, JSON on argv[0] (inline or a
file path) or stdin), importable (`tick(payload) -> tuple[dict, int]`), emits
one pretty JSON object to stdout, exit code reflects the outcome.

Input JSON:
    {
      "change_id": str,          # required — keys the marker and the footer
      "branch": str,             # the run's named branch (named in the header)
      "milestone_manifest": ...,  # the run's milestones, a JSON list of
                                  # {id, title}; also accepted as a JSON-encoded
                                  # string (the milestone_commit `paths` idiom)
      "milestone_id": str|int,    # the current milestone (its item is checked)
      "commit": {...},            # the commit result (status/sha) — for completeness
      "push": {...},              # the push result (status/git_exit_code/git_stderr_tail)
      "repo": str,                # "owner/repo" — required when dry_run false
      "issue": int,               # issue number — required when dry_run false
      "dry_run": bool             # optional, default true (hermetic-tier default)
    }

`dry_run` (default true — wired as `workflow.input.notify_dry_run`, the same
pattern as `commit_dry_run`/`push_dry_run`) renders the body and reports the
`gh` call it WOULD make, with no `gh` invocation, no network I/O, and no GitHub
token — so the Stub tier exercises the progress path hermetically.

Output JSON:
    {
      "status": "dry_run" | "created" | "edited" | "mirror_failed" | "error",
      "mirrored": bool,
      "action": "dry_run" | "create" | "edit" | null,
      "comment_id": int | null,     # the edited/created comment (live only)
      "marker": str,
      "body": str,                   # the rendered comment body
      "gh_exit_code": int | null,    # the last `gh` subprocess exit (live only)
      "gh_stderr_tail": str | null,
      "would_run": [str, ...] | null,# the `gh` argv (status "dry_run" only)
      "reason": str | null           # detail (status "error"/"mirror_failed")
    }

The `gh_` prefix on the two subprocess fields keeps them from colliding with the
enclosing `script` step's own top-level `exit_code`/`stderr` keys (see
`orchestration/harness/README.md`).

Process exit code (the attention convention): 0 for "dry_run"/"created"/
"edited", 1 for "mirror_failed" (attempted a `gh` write and it failed — best
effort, so the run continues), 2 for "error" (a harness-level input error, e.g.
malformed input, or dry_run false without repo/issue).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

from orchestration.harness.common import (
    EXIT_ATTENTION,
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
    coerce_bool,
    emit,
    read_input,
    tail,
)

_MARKER_TEMPLATE = "<!-- agent-orchestration:mirror:{change_id} -->"
_FOOTER_TEMPLATE = (
    "_This checklist is a best-effort projection of run state; when it disagrees "
    "with local state, local state wins. Run `orch status {change_id}` for the "
    "authoritative view._"
)
_REASON_MAX_CHARS = 200

_CHECKBOX_RE = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.*?)\s*$")
_LABEL_RE = re.compile(r"^([^:]+):")
_LOCAL_ONLY_RE = re.compile(r"\(local-only:\s*push failed\s*[—-]\s*(.*?)\)\s*$")


def comment_marker(change_id: str) -> str:
    """The stable first-line HTML marker that keys the single mirror comment."""
    return _MARKER_TEMPLATE.format(change_id=change_id)


def _milestone_label(milestone_id: Any) -> str:
    raw = str(milestone_id).strip()
    return f"M{raw}" if raw.isdigit() else raw


def _sanitize_reason(push_result: dict[str, Any]) -> str:
    """A single-line, paren-free push-failure reason for the annotation."""
    stderr_tail = push_result.get("git_stderr_tail")
    code = push_result.get("git_exit_code")
    if isinstance(stderr_tail, str) and stderr_tail.strip():
        reason = stderr_tail.strip().splitlines()[-1].strip()
    elif code is not None:
        reason = f"git exit {code}"
    else:
        reason = "push failed"
    reason = reason.replace("(", "").replace(")", "").strip()
    return reason[:_REASON_MAX_CHARS] or "push failed"


def _push_outcome(push_result: dict[str, Any]) -> str:
    """Classify the current milestone's push: 'pushed' | 'failed' | 'dry_run'."""
    status = str((push_result or {}).get("status") or "").strip().lower()
    if status == "pushed":
        return "pushed"
    if status in ("push_failed", "error"):
        return "failed"
    return "dry_run"


def parse_manifest(raw: Any) -> list[dict[str, Any]]:
    """Decode the milestone manifest — a JSON list of {id, title}, also accepted
    as a JSON-encoded string (the `milestone_commit` `paths` idiom)."""
    if raw is None:
        raw = []
    if isinstance(raw, str):
        text = raw.strip()
        try:
            raw = json.loads(text) if text else []
        except json.JSONDecodeError as exc:
            raise HarnessInputError(
                f"'milestone_manifest' string is not valid JSON: {exc}"
            ) from exc
    if not isinstance(raw, list):
        raise HarnessInputError("'milestone_manifest' must be a JSON list of {id, title}")
    manifest: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or str(item.get("id", "")).strip() == "":
            raise HarnessInputError("each 'milestone_manifest' entry needs a non-empty 'id'")
        manifest.append({"id": item["id"], "title": item.get("title")})
    return manifest


def parse_prior_state(prior_body: str | None) -> dict[str, tuple[bool, str | None]]:
    """Parse checked-state + local-only reason back out of an existing comment,
    keyed by milestone label. Tolerant of human edits: any line that is not a
    recognizable checkbox item is ignored, so a garbled body simply falls back
    to the manifest baseline (unchecked, unannotated) on the next re-render."""
    parsed: dict[str, tuple[bool, str | None]] = {}
    for line in (prior_body or "").splitlines():
        box = _CHECKBOX_RE.match(line)
        if not box:
            continue
        checked = box.group(1).lower() == "x"
        rest = box.group(2)
        label_match = _LABEL_RE.match(rest)
        if not label_match:
            continue
        label = label_match.group(1).strip()
        local = _LOCAL_ONLY_RE.search(rest)
        reason = (local.group(1).strip() or "push failed") if local else None
        parsed[label] = (checked, reason)
    return parsed


def render_body(
    *,
    manifest: list[dict[str, Any]],
    prior_body: str | None,
    current_id: Any,
    branch: str,
    change_id: str,
    push_result: dict[str, Any],
) -> str:
    """Re-render the WHOLE checklist body from the manifest, prior checked-state,
    and the current milestone's push result. Idempotent and self-healing."""
    prior = parse_prior_state(prior_body)
    outcome = _push_outcome(push_result)
    current_label = _milestone_label(current_id)

    state: dict[str, dict[str, Any]] = {}
    for milestone in manifest:
        label = _milestone_label(milestone["id"])
        checked, reason = prior.get(label, (False, None))
        state[label] = {"checked": checked, "local_only": reason}

    # The current milestone reached the tick, so its commit passed → checked.
    if current_label in state:
        state[current_label]["checked"] = True
        state[current_label]["local_only"] = (
            _sanitize_reason(push_result) if outcome == "failed" else None
        )

    # A push that actually landed publishes the whole accumulated branch, so
    # every prior local-only annotation is now stale — clear them all.
    if outcome == "pushed":
        for entry in state.values():
            entry["local_only"] = None

    lines = [comment_marker(change_id), "", f"### Run mirror — branch `{branch}`", ""]
    for milestone in manifest:
        label = _milestone_label(milestone["id"])
        entry = state[label]
        box = "x" if entry["checked"] else " "
        title = str(milestone.get("title") or "").strip().splitlines()
        text = f"{label}: {title[0]}" if title else label
        annotation = ""
        if entry["local_only"]:
            annotation = f" (local-only: push failed — {entry['local_only']})"
        lines.append(f"- [{box}] {text}{annotation}")
    lines += ["", _FOOTER_TEMPLATE.format(change_id=change_id)]
    return "\n".join(lines) + "\n"


def _gh(args: list[str]) -> tuple[int | None, str, str]:
    """Run `gh` best-effort; return (exit_code_or_None, stdout, stderr). Never
    raises — a missing `gh` binary surfaces as exit None with the reason."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        return None, "", f"gh could not run: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def _find_mirror_comment(repo: str, issue: int, marker: str) -> tuple[int | None, int | None, str]:
    """Locate the mirror comment by its marker. Returns
    (comment_id_or_None, list_exit_code, stderr_tail). A None comment_id with a
    zero exit means 'absent'; a non-zero exit means the lookup itself failed."""
    code, out, err = _gh(["api", f"repos/{repo}/issues/{issue}/comments", "--paginate"])
    if code != 0:
        return None, code, tail(err) if err else ""
    try:
        comments = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        comments = []
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, dict) and marker in str(comment.get("body") or ""):
                cid = comment.get("id")
                if isinstance(cid, int):
                    return cid, code, ""
    return None, code, ""


def _write_comment(
    repo: str, issue: int, comment_id: int | None, marker: str, body: str
) -> tuple[dict[str, Any], int]:
    """Edit the located mirror comment in place, or create exactly one when
    absent — best effort, never raising. Marker match is the only idempotency
    key, so a found comment is PATCHed and never duplicated."""
    if comment_id is not None:
        action = "edit"
        code, _out, err = _gh(
            [
                "api",
                "-X",
                "PATCH",
                f"repos/{repo}/issues/comments/{comment_id}",
                "-f",
                f"body={body}",
            ]
        )
        edited_id: int | None = comment_id
    else:
        action = "create"
        code, out, err = _gh(
            ["api", "-X", "POST", f"repos/{repo}/issues/{issue}/comments", "-f", f"body={body}"]
        )
        edited_id = None
        if code == 0 and out.strip():
            try:
                created = json.loads(out)
            except json.JSONDecodeError:
                created = None
            if isinstance(created, dict) and isinstance(created.get("id"), int):
                edited_id = created["id"]

    if code == 0:
        return {
            "status": "edited" if action == "edit" else "created",
            "mirrored": True,
            "action": action,
            "comment_id": edited_id,
            "marker": marker,
            "body": body,
            "gh_exit_code": 0,
            "gh_stderr_tail": tail(err) if err else None,
            "would_run": None,
            "reason": None,
        }, EXIT_GOOD

    return {
        "status": "mirror_failed",
        "mirrored": False,
        "action": action,
        "comment_id": comment_id,
        "marker": marker,
        "body": body,
        "gh_exit_code": code,
        "gh_stderr_tail": tail(err) if err else None,
        "would_run": None,
        "reason": f"`gh` {action} failed",
    }, EXIT_ATTENTION


def tick(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    change_id = payload.get("change_id")
    if not change_id or not isinstance(change_id, str):
        raise HarnessInputError("'change_id' (non-empty string) is required")

    branch_raw = payload.get("branch")
    branch = branch_raw.strip() if isinstance(branch_raw, str) else str(branch_raw or "").strip()
    manifest = parse_manifest(payload.get("milestone_manifest"))
    current_id = payload.get("milestone_id")
    push_result = payload.get("push") or {}
    if not isinstance(push_result, dict):
        raise HarnessInputError("'push' must be an object (the push step's result)")
    dry_run = coerce_bool(payload.get("dry_run", True), default=True)

    marker = comment_marker(change_id)
    body = render_body(
        manifest=manifest,
        prior_body=None,
        current_id=current_id,
        branch=branch,
        change_id=change_id,
        push_result=push_result,
    )

    if dry_run:
        repo_hint = payload.get("repo") or "<repo>"
        issue_hint = payload.get("issue") or "<issue>"
        would_run = [
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{repo_hint}/issues/{issue_hint}/comments",
            "-f",
            "body=<rendered checklist>",
        ]
        return {
            "status": "dry_run",
            "mirrored": False,
            "action": "dry_run",
            "comment_id": None,
            "marker": marker,
            "body": body,
            "gh_exit_code": None,
            "gh_stderr_tail": None,
            "would_run": would_run,
            "reason": None,
        }, EXIT_GOOD

    repo = payload.get("repo")
    issue = payload.get("issue")
    if not repo or not isinstance(repo, str):
        raise HarnessInputError(
            "'repo' (non-empty 'owner/repo' string) is required when dry_run is false"
        )
    if not isinstance(issue, int):
        raise HarnessInputError("'issue' (int) is required when dry_run is false")

    # Live mode re-renders against the CURRENT on-issue body so prior checked
    # state is merged (and a garbled comment self-heals) — full re-render, not
    # an incremental patch.
    existing_id, list_code, list_err = _find_mirror_comment(repo, issue, marker)
    if list_code != 0:
        return {
            "status": "mirror_failed",
            "mirrored": False,
            "action": None,
            "comment_id": None,
            "marker": marker,
            "body": body,
            "gh_exit_code": list_code,
            "gh_stderr_tail": list_err or None,
            "would_run": None,
            "reason": "could not list the issue's comments",
        }, EXIT_ATTENTION

    prior_body = None
    if existing_id is not None:
        code, out, _err = _gh(["api", f"repos/{repo}/issues/comments/{existing_id}"])
        if code == 0 and out.strip():
            try:
                obj = json.loads(out)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict):
                prior_body = obj.get("body")

    body = render_body(
        manifest=manifest,
        prior_body=prior_body,
        current_id=current_id,
        branch=branch,
        change_id=change_id,
        push_result=push_result,
    )
    return _write_comment(repo, issue, existing_id, marker, body)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        payload = read_input(argv)
        verdict, code = tick(payload)
    except HarnessInputError as exc:
        emit(
            {
                "status": "error",
                "mirrored": False,
                "action": None,
                "comment_id": None,
                "marker": None,
                "body": None,
                "gh_exit_code": None,
                "gh_stderr_tail": None,
                "would_run": None,
                "reason": str(exc),
            }
        )
        return EXIT_ERROR
    emit(verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
