---
id: ADR-0004
title: One run drives exactly one git repository
category: architecture
date: 2026-07-24
status: accepted
---

## Context and Problem Statement

A run's worktree is created with `git worktree add` from the repo that holds
the plan, and that single root serves as plan-root, code-root, and commit-root
simultaneously: the plan is read from it, the agents' box is mounted at it,
and `milestone_commit` commits with `git -C <worktree>`. Dogfooding harness#1
(001-dag-plan-primitive, whose deliverable was a new standalone repo) showed a
change whose plan and code live in different git roots cannot be driven
end-to-end (issue #24). The question: treat this as debt to fix (distinct
code-root/commit-root inputs, a second mounted checkout) or as a committed
design constraint.

## Considered Options

- Commit to one-run-one-repo as a design constraint; split multi-repo changes by hand
- Keep #24 open as deferred work toward a distinct plan-root/code-root model

## Decision Outcome

Committed constraint, not debt. The single root is what makes the run's trust
story simple: one mount = the agents' entire writable world, one commit root =
the only place verified diffs can land, and a plan whose milestones deliver
outside it now fails loudly at the commit step (#23's `empty_paths`). Multi-repo
and new-repo changes are split by hand: create the repo first, plan inside it.
Issue #24 is closed as by-design; this ADR is the standing record.
