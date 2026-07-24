## Milestone 1: `milestone_push.py` — best-effort push of the milestone commit
**Goal** — A deterministic script step that pushes the run branch to the GitHub remote, best-effort, dry-run by default, never force.
**Deliverables** — `orchestration/launch/milestone_push.py` (new); `tests/test_launch_milestone_push.py` (new).
**Validation contract** — checkable acceptance criteria, pre-committed:
  - `uv run pytest tests/test_launch_milestone_push.py -q` — all tests pass
  - `uv run ruff check orchestration tests && uv run ruff format --check orchestration tests` — clean
  - Makes pass (script-level halves): github-mirror scenarios "A committed milestone is pushed to the run branch", "Hermetic tier makes no push"

  ```contract
  check: uv run pytest tests/test_launch_milestone_push.py -q && uv run ruff check orchestration tests && uv run ruff format --check orchestration tests
  criteria: >-
    milestone_push follows the notify_escalation/milestone_commit calling convention (stdin/argv JSON in, one pretty JSON object out, importable push(payload)); input {worktree default ".", branch, remote default "origin", dry_run default true}; dry_run reports the would_run argv ["git","-C",worktree,"push",remote,"HEAD:refs/heads/<branch>"] and exits 0 with no git call; live mode runs exactly that argv (never --force, never a rewritten URL), check=False with a timeout, and never raises on git failure — exit 0 pushed, 1 attempted-and-failed (git_exit_code + git_stderr_tail captured, gh_/git_-prefixed fields so they don't collide with the script step's own exit_code/stderr), 2 input error (e.g. dry_run false with empty branch). Tests use real-git tmp_path repos pushing to a local `git init --bare` origin (no network): successful push lands the commit on the bare origin's branch; a non-fast-forward rejection reports exit 1 without force-pushing; a nonexistent remote reports exit 1; dry_run default touches nothing; malformed input exits 2.
  paths:
    - orchestration/launch/milestone_push.py
    - tests/test_launch_milestone_push.py
  ```
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml, spec-lifecycle.md §10):
  1. Write `orchestration/launch/milestone_push.py` on the `notify_escalation` skeleton (module docstring with input/output/exit-code contract, `_read_input`/`read_input` reuse from `orchestration.harness.common`, `push(payload) -> tuple[dict, int]` or verdict+code like `milestone_commit`, `main()`); validate input (branch required non-empty when `dry_run` false), `dry_run` default true via `coerce_bool`.
  2. Implement live mode with `milestone_commit._git`'s subprocess shape (`git -C <worktree> push <remote> HEAD:refs/heads/<branch>`, `check=False`, 60s timeout, catch `OSError`/`TimeoutExpired`); output `{status: "dry_run"|"pushed"|"push_failed"|"error", pushed, branch, git_exit_code, git_stderr_tail, would_run}`; exit 0/1/2 per the attention convention; no `--force` path exists anywhere.
  3. Write `tests/test_launch_milestone_push.py` on the `test_launch_milestone_commit` fixture pattern: tmp repo + local bare origin (success + branch visible on origin), pre-seeded diverged origin branch (non-FF rejection → exit 1, origin unchanged), remote pointing at a nonexistent path (exit 1), dry-run default (exit 0, `would_run` argv, origin untouched), missing branch with dry_run false (exit 2), malformed JSON (exit 2).

## Milestone 2: `milestone_tick.py` — the edited-in-place checklist comment
**Goal** — A deterministic script step that renders the full milestone checklist and creates-or-edits the single marker-keyed mirror comment on the change's issue.
**Deliverables** — `orchestration/launch/milestone_tick.py` (new); `tests/test_launch_milestone_tick.py` (new).
**Validation contract** — checkable acceptance criteria, pre-committed:
  - `uv run pytest tests/test_launch_milestone_tick.py -q` — all tests pass
  - `uv run ruff check orchestration tests && uv run ruff format --check orchestration tests` — clean
  - Makes pass (script-level): github-mirror scenarios "First completed milestone creates the checklist", "Later milestones edit the same comment in place", "A push failure is annotated, not hidden", "Hermetic tier makes no GitHub call"

  ```contract
  check: uv run pytest tests/test_launch_milestone_tick.py -q && uv run ruff check orchestration tests && uv run ruff format --check orchestration tests
  criteria: >-
    milestone_tick follows the module's script-step convention (stdin JSON, pretty JSON out, importable, exit 0 good / 1 attempted-and-failed / 2 input error, never raises on gh failure, gh_-prefixed subprocess fields). Input carries repo, issue, change_id, branch, the milestone manifest (JSON list of {id, title} — accepted as a JSON-encoded string, the milestone_commit `paths` idiom), the current milestone id, the commit result (status/sha), the push result (status/exit code/stderr tail), dry_run default true. dry_run reports the rendered body + would-run and exits 0 with no gh call, no network, no token. Live mode: locates the mirror comment by the stable first-line marker `<!-- agent-orchestration:mirror:<change_id> -->` by paging the issue's comments via `gh api`; PATCHes it in place when found, POSTs exactly one new comment when absent — marker match is the only idempotency key. Each tick re-renders the WHOLE body from manifest + checked-state parsed from the existing comment + the current result: header names the run branch, one `- [ ]`/`- [x]` line per manifest milestone, a completed-but-push-failed milestone renders checked with an explicit `(local-only: push failed — <reason>)` annotation, prior local-only annotations are cleared by a tick whose push succeeded (the accumulated branch is now published), and a standing footer states the mirror is a best-effort projection and names `orch status <change_id>` as the authoritative check. A garbled/hand-edited comment self-heals on the next full re-render. Tests: dry-run default; live mode via per-module `monkeypatch.setattr(mod.subprocess, "run", _fake_run)` fake-gh (the test_launch_notify_escalation pattern) covering create-if-absent, marker-keyed edit-in-place (no second comment), checked-state merge across ticks, local-only annotation added then cleared, footer presence, garbled-comment self-heal, gh failure → exit 1.
  paths:
    - orchestration/launch/milestone_tick.py
    - tests/test_launch_milestone_tick.py
  ```
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml, spec-lifecycle.md §10):
  1. Write the render half: pure functions taking (manifest, prior-body-or-None, current milestone result, branch, change_id) → full comment body — marker first line, branch-naming header, checkbox list, `(local-only: push failed — <reason>)` annotation rules (add on push failure, clear all on push success), authoritative-check footer; parsing of checked state + annotations back out of an existing body (tolerant of human edits — anything unparseable falls back to the manifest baseline).
  2. Write the gh half: comment lookup (`gh api repos/<repo>/issues/<issue>/comments --paginate`, first marker match wins), create (`gh api -X POST`) / edit (`gh api -X PATCH .../issues/comments/<id>`), all `check=False` with captured stderr tails, never raising.
  3. Wire the script entry (input validation incl. manifest-as-JSON-string decoding, dry_run default true, exit-code mapping 0/1/2) mirroring `notify_escalation`.
  4. Write `tests/test_launch_milestone_tick.py`: render-half unit tests (first render, merge, annotation add/clear, self-heal) + fake-gh live-mode tests (create vs patch, single-comment invariant, failure → exit 1) + dry-run + input-error cases.

## Milestone 3: Workflow wiring + launcher threading — the mirror becomes production-reachable
**Goal** — Wire push and tick into the milestone flow with report-only routing, and thread every mirror input from the launcher through both workflow layers so the production tier actually exercises them.
**Deliverables** — `workflows/milestone.yaml` (push + tick steps, new inputs, routing); `workflows/execute-change.yaml` (declares + forwards branch/push_dry_run/notify_repo/notify_issue/milestone manifest); `orchestration/launch/change.py` (owner/repo derivation, mirror inputs, registry fact); `orchestration/obs/registry.py` (`repo_gh` fact field); `tests/test_workflows_mirror.py` (new); updated `tests/test_launch_change.py`, `tests/test_workflows_stub.py` golden expectations if touched.
**Validation contract** — checkable acceptance criteria, pre-committed:
  - `uv run pytest tests/test_workflows_mirror.py tests/test_launch_change.py tests/test_workflows_stub.py tests/test_workflows_flatten.py tests/test_workflows_ladder.py tests/test_workflows_commit_empty_paths.py -q` — all pass
  - `uv run ruff check orchestration tests && uv run ruff format --check orchestration tests` — clean
  - Makes pass (workflow-level): github-mirror scenarios "A committed milestone is pushed to the run branch", "A push failure does not halt the run", "Hermetic tier makes no push", "Issue unreachable, push still lands", "No GitHub at all, run completes locally"

  ```contract
  check: uv run pytest tests/test_workflows_mirror.py tests/test_launch_change.py tests/test_workflows_stub.py tests/test_workflows_flatten.py tests/test_workflows_ladder.py tests/test_workflows_commit_empty_paths.py -q && uv run ruff check orchestration tests && uv run ruff format --check orchestration tests
  criteria: >-
    milestone.yaml gains `push` then `tick` script steps between `commit` and `$end` — commit routes to push only on `exit_code == 0` (else the unchanged `commit_failed` terminate sink), push routes to tick UNCONDITIONALLY, tick routes to `$end` UNCONDITIONALLY, so neither step can structurally fail the milestone (the escalate-step mechanism); tick's stdin receives the commit result and push.output fields so a failed push is annotated, not hidden. milestone.yaml declares `branch`, `push_dry_run` (default true), `milestone_manifest` (string, default "") inputs; execute-change.yaml declares branch/push_dry_run/notify_repo/notify_issue and forwards them plus `milestone_manifest` (`read_plan.output.milestones | tojson`) through milestone_step.input_mapping — closing the today-unreachable gap where notify_repo/notify_issue were never forwarded. The launcher derives `owner/repo` from the repo's `origin` remote URL (explicit payload field `repo_gh` wins), stores it as a new registry fact `repo_gh` on the entry, threads --input notify_repo/notify_issue/branch, and in the box (production) tier flips push_dry_run false (alongside commit_dry_run) and notify_dry_run false when repo+issue resolved; the stub tier leaves every mirror flag defaulted true. Hermetic proof in tests/test_workflows_mirror.py: a stub-tier execute-change run completes with push+tick both executed in dry_run mode (no gh, no network, no token); a run with commit_dry_run false + push_dry_run false against a tmp repo whose remote does not exist completes every milestone on local commits with push reporting failure (exit 1) and tick still executed — the run reaches its normal terminal state. All pre-existing workflow tests stay green (routing/outputs for the ladder, flatten, and commit_failed paths unchanged).
  paths:
    - workflows/milestone.yaml
    - workflows/execute-change.yaml
    - orchestration/launch/change.py
    - orchestration/obs/registry.py
    - orchestration/cli/payloads.py
    - tests/test_workflows_mirror.py
    - tests/test_launch_change.py
    - tests/test_workflows_stub.py
    - tests/test_cli_payloads.py
  ```
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml, spec-lifecycle.md §10):
  1. `workflows/milestone.yaml`: declare `branch`/`push_dry_run`/`milestone_manifest` inputs (documented like `commit_dry_run`/`notify_dry_run`); add the `push` script step (`python3 -m orchestration.launch.milestone_push`, stdin from worktree/branch/push_dry_run) and the `tick` step (`python3 -m orchestration.launch.milestone_tick`, stdin from notify_repo/notify_issue/change_id/branch/milestone_manifest/milestone_id + `commit.output` status/sha + `push.output` status/exit/stderr fields, dry_run from `notify_dry_run`); rewire routing `commit --exit_code==0--> push --always--> tick --always--> $end` (commit_failed sink untouched); surface push/tick results in the workflow `output:` block (e.g. `push_status`, `mirror_mode`).
  2. `workflows/execute-change.yaml`: declare `branch`, `push_dry_run`, `notify_repo`, `notify_issue` root inputs; extend `milestone_step.input_mapping` to forward them plus `milestone_manifest: "{{ read_plan.output.milestones | tojson }}"` and the existing notify_dry_run.
  3. `orchestration/obs/registry.py`: add `repo_gh` to `new_entry` (a launch-time fact, default None); `orchestration/launch/change.py`: derive owner/repo from `git -C <repo> remote get-url origin` (SSH + HTTPS forms; payload `repo_gh` override wins; None when underivable), store it on the entry, and setdefault the conductor inputs — `branch`, `notify_repo`/`notify_issue` (when known), `push_dry_run=false` in the box tier next to the existing `commit_dry_run=false`, `notify_dry_run=false` only when repo+issue both resolved.
  4. `orchestration/cli/payloads.py` + golden tests: pass through the optional `repo_gh` override field on the production payload (top-level, like `issue`/`branch`).
  5. Write `tests/test_workflows_mirror.py`: (a) hermetic stub-tier execute-change run (2-milestone fixture) — asserts push+tick ran dry_run per milestone and the run ends in the normal terminal state; (b) live-git run with `commit_dry_run=false`/`push_dry_run=false` and a worktree whose `origin` is a nonexistent path — asserts milestones commit locally, push reports failure, tick still runs, run completes ("A push failure does not halt the run" + independence); (c) routing assertion that a tick failure (fake exit 1) also cannot fail the milestone.
  6. Update `tests/test_launch_change.py` for `repo_gh` derivation/override/absence and the new input threading; refresh any golden expectations in `tests/test_workflows_stub.py`/`tests/test_cli_payloads.py` deliberately.

## Milestone 4: Daemon mirror — start/finish comments, `run-died` label, real error text
**Goal** — The daemon posts process-level lifecycle truths (start, resume, finish, classified death with the real error) to the change's issue, deduped via registry facts, on the seam where classification events are produced today.
**Deliverables** — `orchestration/daemon/github_mirror.py` (new shared gh client + daemon notifier); `orchestration/daemon/supervise.py` (events carry remedy + detail); `orchestration/daemon/app.py` (start/resume hooks, event hand-off); `tests/test_daemon_github_mirror.py` (new); updated `tests/test_daemon_supervise.py`, `tests/test_daemon_app.py`.
**Validation contract** — checkable acceptance criteria, pre-committed:
  - `uv run pytest tests/test_daemon_github_mirror.py tests/test_daemon_supervise.py tests/test_daemon_app.py -q` — all pass
  - `uv run ruff check orchestration tests && uv run ruff format --check orchestration tests` — clean
  - Makes pass: github-mirror scenarios "Launch posts a started comment", "Successful completion posts a finished comment", "OAuth-expiry death is surfaced with cause and remedy", "A gate pause is not a death", "Infra death uses run-died", "Ladder escalation uses needs-human-input"

  ```contract
  check: uv run pytest tests/test_daemon_github_mirror.py tests/test_daemon_supervise.py tests/test_daemon_app.py -q && uv run ruff check orchestration tests && uv run ruff format --check orchestration tests
  criteria: >-
    github_mirror.py is a small importable gh-shelling client (post comment, add label, ensure label exists — best-effort `gh label create` cached per process, close issue, list/patch for reuse) with the notify_escalation failure posture: check=False, stderr tails captured, NEVER raises, every result a dict — plus a daemon-side notifier that maps supervision events to writes. supervise.py's poll_once/reconcile events additionally carry the verdict's remedy and detail (the real log-tail error text). app.py stops discarding those events: success → run-finished comment; any classification that is neither success nor gate-pause → ensure + add the `run-died` label and post a death comment containing the classified cause (kind), the remedy, and the real error text (verdict.detail) — never the masked "exited code 1, no stderr"; gate-pause → no label, no comment (the workflow-side escalate step already owns needs-human-input, and nothing in the daemon path ever applies needs-human-input or applies run-died to an escalation). POST /launch posts a run-started comment after adopt; POST /resume posts a resumed-variant started comment. Writes fire only for entries carrying the repo_gh + issue facts (recorded only by production launches — hermetic registry entries lack them, so CI never shells gh and needs no token); every performed write is recorded as a dedupe fact on the incarnation (e.g. mirror: {started, terminal}) and checked before writing, so a restarted daemon or a later reconcile pass never double-posts, including for --direct runs mirrored lazily by reconcile. Tests: fake-gh unit tests for the client (per-module monkeypatch, no shared fixture); test_daemon_supervise asserts events carry remedy/detail and dedupe facts land on the incarnation under a hermetic ORCHESTRATION_REGISTRY_DIR; test_daemon_app asserts start/finish/death/gate-pause behavior and reconcile-after-restart no-double-post via the fake client.
  paths:
    - orchestration/daemon/github_mirror.py
    - orchestration/daemon/supervise.py
    - orchestration/daemon/app.py
    - tests/test_daemon_github_mirror.py
    - tests/test_daemon_supervise.py
    - tests/test_daemon_app.py
  ```
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml, spec-lifecycle.md §10):
  1. Write `orchestration/daemon/github_mirror.py`: the raw client functions (`comment`, `add_label`, `ensure_label` with per-process cache, `close_issue`, `list_comments`, `patch_comment`) — subprocess `gh`, check=False, never raise, dict results with `gh_exit_code`/`gh_stderr_tail`.
  2. Add the daemon notifier layer in the same module: `mirror_started(entry, resumed=False)`, `mirror_terminal(entry, event)` — resolve repo_gh/issue from the entry (silently skip when absent), consult + record `mirror` dedupe facts via `registry.update_incarnation`, map event kinds (success → finished comment; gate-pause → nothing; else → ensure+add `run-died` + death comment with kind/remedy/detail).
  3. Extend `supervise.py` `poll_once`/`reconcile` event dicts with `remedy` and `detail` from the verdict (registry update unchanged).
  4. Hook `app.py`: launch handler calls `mirror_started` after adopt; resume handler the resumed variant; the lifespan poll loop hands `poll_once()`+`reconcile()` events to `mirror_terminal` (via `run_in_threadpool`/sync-safe call, never letting a mirror error kill the loop).
  5. Write/extend the three test modules per the contract (fake-gh monkeypatch pattern; hermetic registry dir; short-lived real child for the supervise leg; gate-pause and needs-human-input non-conflation assertions).

## Milestone 5: Close-on-archive
**Goal** — A successful archive hand-off closes the change's issue with a closing comment; refused or failed archives leave it open; the close never alters the hand-off's own verdict.
**Deliverables** — `orchestration/launch/archive_handoff.py` (close leg); `workflows/execute-change.yaml` (threads notify inputs into the archive step); updated `tests/test_m8_archive_gate.py` (or a new `tests/test_archive_close.py`).
**Validation contract** — checkable acceptance criteria, pre-committed:
  - `uv run pytest tests/test_m8_archive_gate.py tests/test_archive_close.py -q` — all pass (whichever module(s) exist after the split)
  - `uv run ruff check orchestration tests && uv run ruff format --check orchestration tests` — clean
  - Makes pass: github-mirror scenarios "Successful archive closes the issue", "A refused archive leaves the issue open"

  ```contract
  check: uv run pytest tests/test_m8_archive_gate.py tests/test_archive_close.py -q && uv run ruff check orchestration tests && uv run ruff format --check orchestration tests
  criteria: >-
    archive_handoff's payload gains optional notify_repo / notify_issue / notify_dry_run (default true); only when the archive outcome is status "archived" AND notify_dry_run is false AND repo+issue are present does it attempt `gh` close-with-comment (via the shared github_mirror client) referencing the archive; "refused"/"error"/"dry_run" outcomes never attempt a close. The close result is recorded in new output fields (e.g. close_attempted/closed/gh_exit_code/gh_stderr_tail) and NEVER changes `status` or the process exit code — archiving locally succeeded; the mirror is an annotation (a failed close still exits 0/"archived"). execute-change.yaml threads notify_repo/notify_issue/notify_dry_run into the archive_handoff stdin. Tests (fake-gh + the existing archive-gate patterns): archived + close success; archived + close failure (exit code still 0, status still "archived", failure recorded); refused → no gh invocation at all; default dry-run → no gh invocation; test_consent_invariant still passes (the step stays type: script).
  paths:
    - orchestration/launch/archive_handoff.py
    - workflows/execute-change.yaml
    - tests/test_m8_archive_gate.py
    - tests/test_archive_close.py
  ```
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml, spec-lifecycle.md §10):
  1. Extend `archive_handoff.py`: parse the three new optional payload fields; after a real "archived" outcome, call the shared client's `close_issue` (closing comment referencing the archive + change id); merge the close result into the output dict without touching `status`/exit-code mapping.
  2. Thread `notify_repo`/`notify_issue`/`notify_dry_run` into the `archive_handoff` step's stdin template in `execute-change.yaml`.
  3. Extend the archive tests: the four contract cases (close on archived; close failure non-fatal; refused → untouched; dry-run default → untouched), fake-gh monkeypatched per module.

## Milestone 6: Reading guide + docs — the mirror is advisory, local state wins
**Goal** — Humans (and agents) reading the issue mirror are told exactly who posts what, what the labels mean, and that local state is the source of truth when they disagree.
**Deliverables** — `skills/orchestration-monitor/SKILL.md` + `skills/orchestration-launch/SKILL.md` issue-mirror reading guides; `README.md` + `docs/observability-design.md` cross-refs/status update.
**Validation contract** — checkable acceptance criteria, pre-committed:
  - `grep -qi "local state" skills/orchestration-monitor/SKILL.md && grep -qi "local state" skills/orchestration-launch/SKILL.md` — the source-of-truth rule opens both guides
  - `grep -q "run-died" skills/orchestration-monitor/SKILL.md && grep -q "needs-human-input" skills/orchestration-monitor/SKILL.md` — the label taxonomy is documented
  - `uv run pytest -q` — the FULL suite is green (change-level regression guard)
  - `uv run ruff check orchestration tests && uv run ruff format --check orchestration tests` — clean
  - Makes pass: no new spec scenarios (documentation of D10's standing rule + the shipped behavior); guards every scenario already made pass above via the full-suite run

  ```contract
  check: grep -qi "local state" skills/orchestration-monitor/SKILL.md && grep -qi "local state" skills/orchestration-launch/SKILL.md && grep -q "run-died" skills/orchestration-monitor/SKILL.md && grep -q "needs-human-input" skills/orchestration-monitor/SKILL.md && uv run pytest -q && uv run ruff check orchestration tests && uv run ruff format --check orchestration tests
  criteria: >-
    both skills open their issue-mirror section with the D10 rule — the mirror is a best-effort projection; when GitHub and local state disagree, local state wins — and enumerate the known divergence shapes each mapped to the local surface that answers it (checklist unticked but committed → git log on the run branch / orch status; no start comment → possibly a --direct launch, check the registry; local-only annotation → push failed, branch behind). They document who posts what (workflow: push + checklist tick; daemon: start/resume/finish/death; archive hand-off: close), both labels with their distinct remedies (run-died: fix infra + resume; needs-human-input: fix plan + approve + resume), the marker-comment mechanics, and the --direct asymmetry (checklist without a start comment until a daemon reconciles). README and docs/observability-design.md §5.4 gain a shipped-status cross-ref to the github-mirror capability spec. Full pytest suite green proves the whole change coheres.
  paths:
    - skills/orchestration-monitor/SKILL.md
    - skills/orchestration-launch/SKILL.md
    - README.md
    - docs/observability-design.md
  ```
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml, spec-lifecycle.md §10):
  1. Write the issue-mirror reading guide in `skills/orchestration-monitor/SKILL.md`: D10 rule first, divergence-shape table → authoritative local surface, writer map, label taxonomy, marker/checklist mechanics, `--direct` asymmetry.
  2. Mirror the launch-relevant subset into `skills/orchestration-launch/SKILL.md` (what a launch/resume will post, which flags gate it, the D10 rule, and that absent auth degrades to logged failures — ADR-0005).
  3. Add README + `docs/observability-design.md` §5.4 cross-refs marking the durable GitHub-mirror leg shipped by this change.
  4. Run the full suite + ruff as the change-level regression guard.
