---
id: ADR-0002
title: Every shipped workflow template pins durability config
category: architecture
date: 2026-07-07
status: accepted
---

## Context and Problem Statement

Established at project bootstrap by `constitution init`.

## Considered Options

- Adopt this founding principle
- Leave the convention implicit

## Decision Outcome

Conductor's checkpoint defaults are insufficient (failure-only, into
`$TMPDIR`, `max_iterations` default 10). Every workflow template this
module ships sets `runtime.checkpoint.every_agent: true`, relocates the
checkpoint dir to a persistent path, and computes `max_iterations` from
the plan. Crash-safe attempt counting depends on this.

## Rule

Every workflow template this module ships MUST set
`runtime.checkpoint.every_agent: true`, relocate the checkpoint dir to a
persistent path, and compute `max_iterations` from the plan.
