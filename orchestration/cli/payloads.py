"""The two payload templates (design §7). Golden-tested — change the tests
when you change a template, deliberately.

Both templates always send top-level `wait: false` (the curl-era footgun:
`wait` nested under conductor{} was silently ignored and the call blocked).
`conductor.workflow` is deliberately ABSENT: the launcher defaults it to the
module's own workflows/execute-change.yaml (see orchestration.launch.change),
which resolves correctly both in the daemon container (/app/workflows) and a
dev checkout.
"""

from __future__ import annotations

from typing import Any


def production_payload(
    *,
    repo: str,
    change_id: str,
    branch: str | None = None,
    issue: int | None = None,
    repo_gh: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "repo": repo,
        "change_id": change_id,
        "box": {"enabled": True},
        "conductor": {},
        "wait": False,
    }
    if branch:
        payload["branch"] = branch
    if issue is not None:
        payload["issue"] = issue
    # Optional "owner/repo" override for the GitHub mirror (top-level, like
    # `issue`/`branch`); when absent the launcher derives it from the repo's
    # origin remote (orchestration.launch.change.derive_repo_gh).
    if repo_gh:
        payload["repo_gh"] = repo_gh
    return payload


def stub_payload(
    *,
    repo: str,
    change_id: str,
    plan_fixture_path: str,
    stub_script_path: str,
) -> dict[str, Any]:
    return {
        "repo": repo,
        "change_id": change_id,
        "box": {"enabled": False},
        "conductor": {
            "provider": "stub",
            "plan_fixture_path": plan_fixture_path,
            "env": {"CONDUCTOR_STUB_SCRIPT": stub_script_path},
        },
        "wait": False,
    }
