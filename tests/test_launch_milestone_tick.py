"""Hermetic tests for `orchestration.launch.milestone_tick` (the edited-in-place
GitHub checklist mirror — see the module docstring, design.md D4, and the
github-mirror spec "Milestone progress mirrored as one edited-in-place
checklist").

Two layers, no network and no real `gh`:
- render-half unit tests exercise `render_body`/`parse_prior_state` directly
  (first render, checked-state merge, local-only add/clear, self-heal, footer);
- live-mode tests drive `tick`/`main` with a per-module `monkeypatch.setattr(
  mod.subprocess, "run", fake.run)` fake-gh (the `test_launch_notify_escalation`
  pattern) covering create-if-absent, marker-keyed edit-in-place, merge across
  ticks, annotation add-then-clear, garbled-comment self-heal, and gh failure.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

import orchestration.launch.milestone_tick as mod
from orchestration.harness.common import (
    EXIT_ATTENTION,
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
)
from orchestration.launch.milestone_tick import (
    comment_marker,
    main,
    parse_prior_state,
    render_body,
    tick,
)

CHANGE = "015-github-mirror"
BRANCH = "change/015-github-mirror"
REPO = "kentra-io/agent-orchestration"
ISSUE = 42
MARKER = comment_marker(CHANGE)

MANIFEST = [
    {"id": 1, "title": "milestone_push.py"},
    {"id": 2, "title": "milestone_tick.py"},
    {"id": 3, "title": "Workflow wiring"},
]

PUSHED = {"status": "pushed", "git_exit_code": 0, "git_stderr_tail": None}
DRY = {"status": "dry_run", "git_exit_code": None, "git_stderr_tail": None}
FAILED = {
    "status": "push_failed",
    "git_exit_code": 1,
    "git_stderr_tail": "! [rejected] main -> main non-fast-forward",
}


def _render(prior, current, push):
    return render_body(
        manifest=MANIFEST,
        prior_body=prior,
        current_id=current,
        branch=BRANCH,
        change_id=CHANGE,
        push_result=push,
    )


# --------------------------------------------------------------------------- #
# Render half                                                                 #
# --------------------------------------------------------------------------- #
class TestRender:
    def test_first_render_marker_header_footer_and_current_checked(self) -> None:
        body = _render(None, 1, PUSHED)
        assert body.splitlines()[0] == MARKER
        assert f"branch `{BRANCH}`" in body
        assert "- [x] M1: milestone_push.py" in body
        assert "- [ ] M2: milestone_tick.py" in body
        assert "- [ ] M3: Workflow wiring" in body
        # authoritative-check footer names `orch status <change_id>`
        assert f"orch status {CHANGE}" in body
        assert "local state wins" in body

    def test_merge_keeps_prior_checked_across_ticks(self) -> None:
        first = _render(None, 1, PUSHED)
        second = _render(first, 2, PUSHED)
        assert "- [x] M1: milestone_push.py" in second
        assert "- [x] M2: milestone_tick.py" in second
        assert "- [ ] M3: Workflow wiring" in second

    def test_push_failure_is_annotated_not_hidden(self) -> None:
        body = _render(None, 1, FAILED)
        assert "- [x] M1: milestone_push.py (local-only: push failed — " in body
        assert "non-fast-forward" in body

    def test_later_successful_push_clears_prior_local_only(self) -> None:
        failed_first = _render(None, 1, FAILED)
        assert "local-only" in failed_first
        cleared = _render(failed_first, 2, PUSHED)
        # M1 stays checked, but its local-only annotation is gone (branch
        # published), and M2 is now checked.
        assert "- [x] M1: milestone_push.py\n" in cleared
        assert "local-only" not in cleared
        assert "- [x] M2: milestone_tick.py" in cleared

    def test_dry_run_push_leaves_current_checked_without_annotation(self) -> None:
        body = _render(None, 1, DRY)
        assert "- [x] M1: milestone_push.py" in body
        assert "local-only" not in body

    def test_garbled_prior_body_self_heals_to_full_checklist(self) -> None:
        body = _render("total garbage a human pasted\n\nno checkboxes here", 2, PUSHED)
        assert body.splitlines()[0] == MARKER
        assert "- [ ] M1: milestone_push.py" in body
        assert "- [x] M2: milestone_tick.py" in body
        assert "- [ ] M3: Workflow wiring" in body

    def test_parse_prior_state_reads_checked_and_reason(self) -> None:
        body = _render(None, 1, FAILED)
        state = parse_prior_state(body)
        assert state["M1"][0] is True
        assert state["M1"][1] and "non-fast-forward" in state["M1"][1]
        assert state["M2"][0] is False


# --------------------------------------------------------------------------- #
# Fake gh                                                                      #
# --------------------------------------------------------------------------- #
class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGh:
    """An in-memory stand-in for `gh api` against one issue's comments."""

    def __init__(self) -> None:
        self.comments: dict[int, str] = {}
        self._next = 1000
        self.calls: list[list[str]] = []
        self.fail = False

    def seed(self, body: str) -> int:
        cid = self._next
        self._next += 1
        self.comments[cid] = body
        return cid

    def run(self, cmd, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(list(cmd))
        if self.fail:
            return _Completed(1, "", "gh: HTTP 500")
        assert cmd[0] == "gh"
        args = cmd[1:]
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        endpoint = next(a for a in args if a.startswith("repos/"))
        body = None
        if "-f" in args:
            field = args[args.index("-f") + 1]
            if field.startswith("body="):
                body = field[len("body=") :]

        if endpoint.endswith("/comments"):
            if method == "POST":
                cid = self.seed(body or "")
                return _Completed(0, json.dumps({"id": cid, "body": body}), "")
            arr = [{"id": cid, "body": b} for cid, b in self.comments.items()]
            return _Completed(0, json.dumps(arr), "")

        cid = int(endpoint.rsplit("/", 1)[1])
        if method == "PATCH":
            self.comments[cid] = body or ""
            return _Completed(0, json.dumps({"id": cid, "body": body}), "")
        return _Completed(0, json.dumps({"id": cid, "body": self.comments.get(cid)}), "")


@pytest.fixture
def fake_gh(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(mod.subprocess, "run", fake.run)
    return fake


def _live(fake: FakeGh, current, push):
    return tick(
        {
            "change_id": CHANGE,
            "branch": BRANCH,
            "milestone_manifest": json.dumps(MANIFEST),
            "milestone_id": current,
            "push": push,
            "repo": REPO,
            "issue": ISSUE,
            "dry_run": False,
        }
    )


# --------------------------------------------------------------------------- #
# Live mode                                                                    #
# --------------------------------------------------------------------------- #
class TestLive:
    def test_create_if_absent(self, fake_gh: FakeGh) -> None:
        verdict, code = _live(fake_gh, 1, PUSHED)
        assert code == EXIT_GOOD
        assert verdict["status"] == "created"
        assert verdict["mirrored"] is True
        assert len(fake_gh.comments) == 1
        (body,) = fake_gh.comments.values()
        assert body.splitlines()[0] == MARKER
        assert "- [x] M1: milestone_push.py" in body

    def test_edit_in_place_no_second_comment(self, fake_gh: FakeGh) -> None:
        _live(fake_gh, 1, PUSHED)
        verdict, code = _live(fake_gh, 2, PUSHED)
        assert code == EXIT_GOOD
        assert verdict["status"] == "edited"
        # still exactly one comment — marker match reused the existing one
        assert len(fake_gh.comments) == 1

    def test_checked_state_merges_across_ticks(self, fake_gh: FakeGh) -> None:
        _live(fake_gh, 1, PUSHED)
        _live(fake_gh, 2, PUSHED)
        (body,) = fake_gh.comments.values()
        assert "- [x] M1: milestone_push.py" in body
        assert "- [x] M2: milestone_tick.py" in body
        assert "- [ ] M3: Workflow wiring" in body

    def test_local_only_added_then_cleared(self, fake_gh: FakeGh) -> None:
        _live(fake_gh, 1, FAILED)
        (after_fail,) = fake_gh.comments.values()
        assert "local-only" in after_fail
        _live(fake_gh, 2, PUSHED)
        (after_push,) = fake_gh.comments.values()
        assert "local-only" not in after_push
        assert "- [x] M1: milestone_push.py" in after_push
        assert "- [x] M2: milestone_tick.py" in after_push

    def test_garbled_comment_self_heals_in_place(self, fake_gh: FakeGh) -> None:
        cid = fake_gh.seed(f"{MARKER}\n\nsomeone hand-mangled this comment")
        verdict, code = _live(fake_gh, 1, PUSHED)
        assert code == EXIT_GOOD
        assert verdict["status"] == "edited"
        assert verdict["comment_id"] == cid
        assert len(fake_gh.comments) == 1
        assert "- [x] M1: milestone_push.py" in fake_gh.comments[cid]

    def test_footer_present_in_live_body(self, fake_gh: FakeGh) -> None:
        verdict, _ = _live(fake_gh, 1, PUSHED)
        assert f"orch status {CHANGE}" in verdict["body"]

    def test_gh_failure_reports_exit_1_and_does_not_raise(self, fake_gh: FakeGh) -> None:
        fake_gh.fail = True
        verdict, code = _live(fake_gh, 1, PUSHED)
        assert code == EXIT_ATTENTION
        assert verdict["status"] == "mirror_failed"
        assert verdict["mirrored"] is False
        assert verdict["gh_exit_code"] == 1


# --------------------------------------------------------------------------- #
# Dry run                                                                      #
# --------------------------------------------------------------------------- #
class TestDryRun:
    def test_default_is_dry_run_no_gh_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: calls.append(a))
        verdict, code = tick(
            {
                "change_id": CHANGE,
                "branch": BRANCH,
                "milestone_manifest": json.dumps(MANIFEST),
                "milestone_id": 1,
                "push": DRY,
            }
        )
        assert code == EXIT_GOOD
        assert verdict["status"] == "dry_run"
        assert verdict["mirrored"] is False
        assert calls == []  # no gh, no network, no token
        assert verdict["body"].splitlines()[0] == MARKER
        assert "- [x] M1: milestone_push.py" in verdict["body"]
        assert verdict["would_run"][0] == "gh"

    def test_manifest_accepted_as_list_too(self) -> None:
        verdict, code = tick(
            {
                "change_id": CHANGE,
                "branch": BRANCH,
                "milestone_manifest": MANIFEST,
                "milestone_id": 2,
                "push": DRY,
            }
        )
        assert code == EXIT_GOOD
        assert "- [x] M2: milestone_tick.py" in verdict["body"]


# --------------------------------------------------------------------------- #
# Input errors                                                                 #
# --------------------------------------------------------------------------- #
class TestErrors:
    def test_missing_change_id_raises(self) -> None:
        with pytest.raises(HarnessInputError, match="change_id"):
            tick({"branch": BRANCH, "milestone_manifest": MANIFEST, "milestone_id": 1})

    def test_live_requires_repo_and_issue(self) -> None:
        base = {
            "change_id": CHANGE,
            "branch": BRANCH,
            "milestone_manifest": MANIFEST,
            "milestone_id": 1,
            "dry_run": False,
        }
        with pytest.raises(HarnessInputError, match="repo"):
            tick(base)
        with pytest.raises(HarnessInputError, match="issue"):
            tick({**base, "repo": REPO})

    def test_malformed_manifest_string_raises(self) -> None:
        with pytest.raises(HarnessInputError, match="milestone_manifest"):
            tick(
                {
                    "change_id": CHANGE,
                    "branch": BRANCH,
                    "milestone_manifest": "not json [",
                    "milestone_id": 1,
                }
            )

    def test_manifest_entry_without_id_raises(self) -> None:
        with pytest.raises(HarnessInputError, match="id"):
            tick(
                {
                    "change_id": CHANGE,
                    "branch": BRANCH,
                    "milestone_manifest": [{"title": "no id here"}],
                    "milestone_id": 1,
                }
            )


# --------------------------------------------------------------------------- #
# Script entry                                                                 #
# --------------------------------------------------------------------------- #
class TestScriptEntry:
    def test_main_dry_run_emits_verdict(self, capsys: pytest.CaptureFixture) -> None:
        payload = {
            "change_id": CHANGE,
            "branch": BRANCH,
            "milestone_manifest": json.dumps(MANIFEST),
            "milestone_id": 1,
            "push": DRY,
        }
        code = main([json.dumps(payload)])
        assert code == EXIT_GOOD
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "dry_run"
        assert out["marker"] == MARKER

    def test_main_malformed_json_exits_2(self, capsys: pytest.CaptureFixture) -> None:
        code = main(["this is not json ["])
        assert code == EXIT_ERROR
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"
        assert out["reason"]

    def test_main_missing_change_id_exits_2(self, capsys: pytest.CaptureFixture) -> None:
        code = main([json.dumps({"milestone_manifest": MANIFEST, "milestone_id": 1})])
        assert code == EXIT_ERROR
        assert json.loads(capsys.readouterr().out)["status"] == "error"

    def test_cli_subprocess_dry_run(self) -> None:
        payload = {
            "change_id": CHANGE,
            "branch": BRANCH,
            "milestone_manifest": json.dumps(MANIFEST),
            "milestone_id": 1,
            "push": DRY,
        }
        ok = subprocess.run(
            [sys.executable, "-m", "orchestration.launch.milestone_tick", json.dumps(payload)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert ok.returncode == EXIT_GOOD
        assert json.loads(ok.stdout)["status"] == "dry_run"
