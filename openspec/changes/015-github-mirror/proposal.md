---
issue: "kentra-io/agent-orchestration#15"
designSkip: false  # crosses workflow + daemon + archive components, adds a GitHub write client with token/network concerns, and depends on the classifier's real-error-text source — needs a design stage
type: feature
---

# GitHub mirror — the issue as the full-lifecycle human progress view

## Why

Observability core (issue #7, merged) gave live runs a dashboard and a
derived-on-read registry, but neither survives as a durable, human-scannable
record: the dashboard dies with the conductor process and renders the
workflow *template* (one `milestone_step` loop node), so per-milestone
progress is invisible at a glance (first production run, 2026-07-21). The
change's source-tracking issue is the one surface that already exists per
change, is durable, and is where a human scanning issues — not run logs —
looks. This change makes that issue the full-lifecycle mirror of run state:
start → milestone progress → death/escalation → finish → close-on-archive.

## What Changes

- **New `github-mirror` capability** — the change's issue becomes the durable
  mirror of one run's lifecycle, fed by three writers split by *who can know*
  the fact:
  - **Workflow-side milestone push + ticks** — after each milestone's
    deterministic commit, the workflow **pushes it to the run's named branch**
    (launch `branch` input, default `change/<change_id>`) — the mirror
    reflects GitHub state, so the state must first *be* on GitHub — then
    updates a single checklist comment on the issue, edited in place
    (idempotent, no comment spam), naming that branch. All GitHub side
    effects are **independent best effort**: a push or issue-write failure
    never blocks the run (work proceeds on local commits), and a milestone
    whose push failed is recorded in the checklist as completed-but-local-only
    with the push problem noted. Extends the proven `notify_escalation`
    script-step pattern with the same `dry_run` hermetic default (no network,
    no `gh`, no token in CI).
  - **Daemon-side lifecycle comments** — run-started and run-finished
    comments, and, on a classified death, a `run-died` label plus a comment
    carrying the classified cause, its remedy, and the **real error text**
    (ending the "exited code 1, no stderr" masking). These are process-level
    truths a workflow cannot self-report, so the daemon (the child's parent /
    reconciler) owns them.
  - **Close-on-archive** — a successful `archive_handoff` closes the issue
    with a closing comment; a refused/failed archive leaves it open.
- **Label taxonomy** — a new `run-died` label (remedy: fix infra, resume),
  kept distinct from the existing `needs-human-input` label (remedy: fix
  plan, approve, resume). Different remedy, different label — never conflated.

## Impact

- Code:
  - `orchestration/launch/` — a per-milestone **push** step (after
    `milestone_commit.py`, which is deliberately push-free today) and a new
    milestone-tick notify step alongside `notify_escalation.py`; both wired
    as per-milestone `script` steps in `workflows/milestone.yaml` /
    `execute-change.yaml` (dry_run + branch inputs threaded like the
    escalate step's `notify_dry_run`; the branch name already exists as the
    launch `branch` input, default `change/<change_id>`).
  - `orchestration/daemon/` — the supervisor's exit events (`supervise.py`
    `poll_once`/`reconcile`) and the launch/finish paths gain a GitHub-write
    side effect; a small `gh`-shelling client (mirrors the module's existing
    `gh issue edit` convention).
  - `orchestration/launch/archive_handoff.py` — close-on-archive on
    `status == "archived"`.
- Depends on observability core's exit classifier (`orchestration/obs/
  classify.py`) for cause/remedy. The fork patch that surfaces the
  ProviderError stdout tail (design §7.1) has **already landed** in the
  pinned fork — `claudebox.py` now puts the stdout tail into the error
  detail — so the real-error-text source is satisfied, not a prerequisite.
- **Out of scope:** the issue's "`/resume` reachable through the daemon
  flow" item shipped separately with the `orch` CLI change (daemon
  `POST /resume` is live; cli-design.md §2 explicitly kept GitHub-mirror
  concerns out of it). This change only *references* resume in remedy
  text; it does not touch the resume flow.
- The `github-mirror` capability spec (this change) is the module's first
  lifecycle-tracked spec of the GitHub-mirror surface; observability core
  shipped pre-spec, so these requirements are all ADDED, not MODIFIED.
- Docs/skills: `orchestration-monitor` and `orchestration-launch` gain the
  issue-mirror reading guide (which flow posts what, label meanings, the
  edited-in-place checklist); README/observability-design cross-refs.
- Open design-stage concerns (from design §10 / §5.4): bot-token handling
  posture for the daemon writer (candidate constitution ADR), the idempotent
  checklist marker format, whether close-on-archive needs the parked
  spec-lifecycle §5.5 seam or `gh issue close` from the launch context
  suffices, who posts start vs finish when a run is launched `--direct`
  (daemon down) or resumed, and where the push step sits relative to the
  commit/`commit_failed` routing in `milestone.yaml` (push/mirror failures
  are non-blocking best effort — the routing must report them without
  failing the milestone).
