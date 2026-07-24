---
id: ADR-0005
title: GitHub writes are best-effort, bot-identity, dry-run-by-default side effects
category: architecture
date: 2026-07-24
status: accepted
---

## Context and Problem Statement

The github-mirror change (015) introduces the module's first daemon-side
GitHub writes and its first branch pushes, joining the one existing
workflow-side write (`notify_escalation`). Each writer must decide three
postures: what identity it authenticates as, what happens when the write
fails, and what a hermetic (CI / Stub-tier) invocation does. Deciding this
per-writer invites drift — one blocking write or one write under a human's
OAuth session would change the module's failure and audit story. What
standing rule should govern every GitHub side effect this module performs?

## Decision Drivers

- A run must never depend on GitHub reachability: local completion is the
  invariant, the mirror is an annotation (spec `github-mirror`,
  "independent best effort").
- CI and the Stub tier must stay tokenless and network-free.
- Writes should be attributable to the machine actor (audit trail), and
  must not consume or expose a human's credentials; subscription OAuth
  creds are locked to agent auth (P11) and unavailable to infrastructure.
- One identity across daemon, workflow steps, and git-push keeps token
  rotation a single operation (the established
  keychain → `KENTRA_BOT_GH_TOKEN` → container env flow).

## Considered Options

1. **Bot identity + best-effort + dry-run-by-default** for every GitHub
   side effect (writes shell `gh` with `GH_TOKEN` mapped from
   `KENTRA_BOT_GH_TOKEN`; git-push auth via `gh auth setup-git`; failures
   are logged/reported, never raised; every entry point defaults
   `dry_run: true`).
2. **Ambient auth** — writers use whatever `gh auth` state the host or
   container happens to have (possibly the human's login).
3. **Blocking writes** — mirror failures fail the step/run, guaranteeing
   the issue always reflects reality.
4. **GitHub App installation tokens** — a dedicated app identity with
   scoped, short-lived tokens per repo.

## Decision Outcome

Chosen option: **1 — bot identity, best-effort, dry-run-by-default**, as a
standing rule for all current and future GitHub side effects (comments,
labels, issue close, branch push):

- **Identity**: all writes authenticate as the bot via
  `KENTRA_BOT_GH_TOKEN` (mapped to `GH_TOKEN`; git credential helper via
  `gh auth setup-git`). Human credentials are never used for machine
  writes. Where the env var is absent (host-side `--direct` fallback),
  ambient `gh` auth may incidentally serve, acceptable only because of the
  next clause.
- **Failure posture**: every GitHub write is independent best effort —
  attempted, result recorded (exit code + stderr tail), never raised,
  never a step/run failure, never a reason to skip a sibling write.
- **Hermetic default**: every entry point that can reach GitHub defaults
  `dry_run: true` (no network, no `gh`, no token) so CI and the Stub tier
  exercise the code paths tokenlessly; real writes are an explicit opt-in
  by the production launcher.

Option 2 is rejected as the *rule* (identity drift, human-credential
leakage, unauditable actor); option 3 contradicts the local-completion
invariant and couples run health to GitHub uptime; option 4 is better
security posture in principle but disproportionate for a single-operator
module today — it can supersede this ADR if the operator surface grows.

## Rule

Every GitHub side effect this module performs (comments, labels, issue close, branch push) MUST authenticate as the bot identity (KENTRA_BOT_GH_TOKEN via GH_TOKEN / gh auth setup-git — never a human's credentials), MUST be independent best effort (attempted, result recorded, never raised, never a step/run failure, never a reason to skip a sibling write), and MUST default to a hermetic dry_run performing no network I/O and requiring no token.
