---
id: ADR-0001
title: Fork Conductor at a pinned SHA; rebase only deliberately
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

The module depends on the `kentra-io/conductor` fork at an exact pinned
commit; upstream rebases are explicit decisions gated on the provider/Stub
test corpus passing unchanged, never an automatic upgrade. The patch-set
stays minimal (registration lines + one provider file) to keep rebases
cheap.

## Rule

The module depends on the `kentra-io/conductor` fork at an exact pinned
commit; upstream rebases are explicit decisions gated on the provider/Stub
test corpus passing unchanged, never an automatic upgrade. The patch-set
stays minimal (registration lines + one provider file) to keep rebases
cheap.
