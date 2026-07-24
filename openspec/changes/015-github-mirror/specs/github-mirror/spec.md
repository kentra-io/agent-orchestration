## ADDED Requirements

### Requirement: Verified milestone commits are pushed to the run branch
After a milestone's deterministic commit succeeds, the workflow SHALL push
that commit to the run's named branch (the launch `branch` input, default
`change/<change_id>`) on the GitHub remote, so the state a human sees on
GitHub — branch, commits, and the issue mirror — is the state of the run.
Like the commit step, the push SHALL default to a hermetic `dry_run` mode
that performs no network I/O. The push is **best effort**: a push failure
(remote outage, auth) SHALL be reported but SHALL NOT fail the milestone or
halt the run — the run proceeds on its local commits, and a later successful
push publishes the accumulated branch.

#### Scenario: A committed milestone is pushed to the run branch
- **GIVEN** a milestone whose commit step reports `committed` with
  `dry_run` false
- **WHEN** the milestone's push runs
- **THEN** the new commit is pushed to the run's named branch on the remote

#### Scenario: A push failure does not halt the run
- **GIVEN** a milestone whose commit succeeded but whose push fails (e.g.
  remote outage)
- **WHEN** the milestone flow continues
- **THEN** the milestone completes on its local commit, the failure is
  reported, and the run proceeds to the next milestone

#### Scenario: Hermetic tier makes no push
- **GIVEN** a Stub-tier run with `dry_run` true (the default)
- **WHEN** a committed (or dry-run) milestone reaches the push
- **THEN** the step reports the push it would make and exits success without
  contacting the network

### Requirement: GitHub side effects are independent best effort
Every GitHub side effect of a run — branch push, checklist edit, lifecycle
comments, labels, close-on-archive — SHALL be attempted independently:
failure of one SHALL be logged but SHALL NOT fail the step or the run, and
SHALL NOT skip the others. A run with no GitHub reachability at all still
runs to completion locally.

#### Scenario: Issue unreachable, push still lands
- **GIVEN** a milestone whose checklist update fails (issue write rejected)
- **WHEN** the milestone's GitHub side effects run
- **THEN** the branch push is still attempted, and the run continues
  regardless of the mirror failure

#### Scenario: No GitHub at all, run completes locally
- **GIVEN** a run with no GitHub reachability (network down)
- **WHEN** milestones pass, commit, and fail to push or mirror
- **THEN** every milestone still completes on local commits and the run
  reaches its normal terminal state

### Requirement: Milestone progress mirrored as one edited-in-place checklist
The workflow SHALL mirror per-milestone progress to the change's issue as a
**single** checklist comment — one checkbox item per milestone, naming the
run's branch — that is **edited in place**, located idempotently by a stable
marker so a passing milestone never posts a new comment. A milestone's item
is checked once the milestone is verified and committed; when its commit was
**pushed to the run branch**, a checked box corresponds to state visible on
GitHub. When the push failed, the checklist SHALL still record the
milestone's completion but SHALL explicitly annotate it as local-only with
the push problem, so local-only progress is never silently presented as
being on GitHub. Like the existing escalation mirror it SHALL default to a
hermetic `dry_run` mode that performs no `gh` call, no network I/O, and
needs no GitHub token, so the progress path is exercised in the Stub tier;
the real write happens only when `dry_run` is false with a repo and issue
supplied.

#### Scenario: First completed milestone creates the checklist
- **GIVEN** a change whose issue has no existing mirror checklist comment
- **WHEN** the run's first milestone is committed and pushed with `dry_run`
  false
- **THEN** exactly one comment is created carrying the stable marker, the
  run branch's name, and a checklist of the change's milestones, with the
  completed milestone's item checked

#### Scenario: Later milestones edit the same comment in place
- **GIVEN** a change whose issue already has the mirror checklist comment
- **WHEN** a subsequent milestone is committed and pushed
- **THEN** the existing comment is found by its marker and edited so that
  milestone's item is checked, and no additional comment is posted

#### Scenario: A push failure is annotated, not hidden
- **GIVEN** a milestone that was verified and committed but whose push
  failed
- **WHEN** the mirror step updates the checklist
- **THEN** the milestone is recorded as completed with an explicit
  annotation that its commit is local-only because the push failed

#### Scenario: Hermetic tier makes no GitHub call
- **GIVEN** a Stub-tier run with `dry_run` true (the default)
- **WHEN** a milestone passes
- **THEN** the step reports the update it would make and exits success
  without invoking `gh`, contacting the network, or requiring a token

### Requirement: Run start and finish mirrored by the daemon
The daemon SHALL post a run-started comment to the change's issue when it
launches a run, and a run-finished comment when that run terminates with a
`success` classification. These comments are the daemon's responsibility
because they report process-level truths (launch, observed exit) that the
in-run workflow cannot self-report.

#### Scenario: Launch posts a started comment
- **GIVEN** a change with a source-tracking issue
- **WHEN** the daemon launches a run for that change
- **THEN** a run-started comment is posted to the issue identifying the run

#### Scenario: Successful completion posts a finished comment
- **GIVEN** a running change tracked by the daemon
- **WHEN** the run exits and is classified `success`
- **THEN** a run-finished comment is posted to the issue

### Requirement: Run death surfaced with the real error, not a masked exit
When a run terminates with a death classification (any classifier verdict
that is neither `success` nor the by-design `gate-pause`), the daemon SHALL
add the `run-died` label to the change's issue and post a comment containing
the classified cause, its remedy, and the real error text drawn from the
process output — never the masked "exited code 1, no stderr". A `gate-pause`
exit SHALL NOT be surfaced as a death.

#### Scenario: OAuth-expiry death is surfaced with cause and remedy
- **GIVEN** a run whose exit is classified `oauth-expired`
- **WHEN** the daemon observes (or reconciles) that exit
- **THEN** the issue gains the `run-died` label and a comment stating the
  cause, the `cb login` remedy, and the captured error text

#### Scenario: A gate pause is not a death
- **GIVEN** a run that exits classified `gate-pause` (the crash-then-resume
  human-gate pause)
- **WHEN** the daemon observes that exit
- **THEN** the `run-died` label is NOT applied and no death comment is posted

### Requirement: Distinct labels for infra death and plan escalation
The mirror SHALL keep two distinct issue labels with different remedies and
never conflate them: `run-died` (an infrastructure/runtime failure — remedy:
fix the infra, resume) versus the existing `needs-human-input` (a
ladder-exhausted plan escalation — remedy: fix the plan, approve, resume).

#### Scenario: Infra death uses run-died
- **GIVEN** a run that dies from an infrastructure/runtime cause
- **WHEN** the death is mirrored
- **THEN** the issue is labelled `run-died` and not `needs-human-input`

#### Scenario: Ladder escalation uses needs-human-input
- **GIVEN** a milestone that exhausts its 3-attempt ladder and escalates
- **WHEN** the escalation is mirrored
- **THEN** the issue is labelled `needs-human-input` and not `run-died`

### Requirement: Archiving a change closes its issue
On a successful `archive` hand-off (the fold completed and the change was
relocated to `archive/`), the mirror SHALL close the change's issue with a
closing comment referencing the archive. An archive that is refused (e.g. by
the tasks-completion gate) or errors SHALL leave the issue open.

#### Scenario: Successful archive closes the issue
- **GIVEN** a completed change whose `archive_handoff` reports status
  `archived`
- **WHEN** the archive hand-off finishes
- **THEN** the change's issue is closed with a closing comment

#### Scenario: A refused archive leaves the issue open
- **GIVEN** a change whose `archive_handoff` reports status `refused` (an
  unchecked tracked task blocks the fold)
- **WHEN** the archive hand-off finishes
- **THEN** the issue remains open and no closing comment is posted
