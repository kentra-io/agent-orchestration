---
id: ADR-0003
title: Engine-dictated Python; deterministic harness stays script-step-shaped
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

This module is authored in Python 3.12 (uv) because it runs in-process
inside Conductor's asyncio engine — the sole deliberate, engine-dictated
deviation from the primitive family's Go default (ADR-0003 mandates repo
shape, not language). Deterministic verification logic is kept small and
script-step-shaped so a later Go port remains possible.

## Rule

The module is authored in Python 3.12 (uv), engine-dictated by running
in-process inside Conductor's asyncio engine — the sole deliberate
deviation from the primitive family's Go default. Deterministic
verification logic MUST stay small and script-step-shaped so a later Go
port remains possible.
