---
name: lifecycle-plan
description: Conduct the plan stage of a spec-lifecycle change — tasks.md milestones with pre-committed validation contracts, gated at gate 3 alongside a re-run of the constitution plan-gate. Invoke explicitly with /lifecycle-plan.
disable-model-invocation: true
---

# lifecycle-plan

Conduct the plan stage of ONE change, in a fresh session, and stop at
gate 3. Your entire input is the approved artifacts already in this change
folder — `proposal.md`, every `specs/**/spec.md`, and `design.md` unless
gate 1 recorded a design-skip (spec-lifecycle.md §3.1, §3.2) — re-read them
from disk rather than trusting an earlier conversation's memory. This is
the last planning stage before execution: `tasks.md` is what an
implementer (or a future session) will be graded against, so it must stand
on its own.

## What this stage produces

`tasks.md` — milestones in the fixed shape (spec-lifecycle.md §4.2), one
block per milestone:

```
## Milestone <n>: <name>
**Goal** — one sentence.
**Deliverables** — files/components/behaviors produced.
**Validation contract** — checkable acceptance criteria, pre-committed:
  - commands/tests to run and their expected outcomes
  - which spec scenarios this milestone makes pass
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml).
```

Fill it from `openspec/schemas/kentra-spec-lifecycle/templates/tasks.md`.
**Planning owns the Validation contract; execution consumes it and never
re-authors it** — write every contract as if you will never see this
change folder again, because you won't (fresh-session discipline applies
to execution too, spec-lifecycle.md §3.1). `lifecycle validate --stage plan`
enforces that every milestone carries all four labels and at least one
checkable line under Validation contract — it is not advisory here.

## The constitution seam (spec-lifecycle.md §7) — re-run, don't reuse

The constitution may have moved since gate 2 (an accepted ADR from the
design stage). Do not reuse the gate-2 `deviation.json` — generate a fresh
one:

1. Invoke `/plan-gate` against `tasks.md` (and `design.md`, for full
   context on what was already accepted), **explicitly telling it to write
   its `deviation.json` report to `<changefolder>/deviation.json`**,
   overwriting the gate-2 report. As at gate 2, this is an instruction to
   the skill, not a `lifecycle` CLI flag.
2. Resolve every deviation conform-or-amend before requesting approval,
   same discipline as gate 2 — a plan that conflicts with a rule the design
   already passed under (because the rule changed) is exactly the case
   this re-run exists to catch.

## Gate mechanics (spec-lifecycle.md §3.3)

1. Draft `tasks.md`; run the constitution seam above until every deviation
   is resolved.
2. Run `lifecycle validate --stage plan`. Fix every finding.
3. Surface `tasks.md` and the fresh, validated `deviation.json` to the
   human. Wait for explicit approval.
4. Only after that explicit approval, run:
   ```
   lifecycle approve --stage plan --approve <change>
   ```
   This shells out to `constitution deviation validate <changefolder>/deviation.json`
   before writing anything and surfaces a failure instead of writing a gate
   entry. Mutating — never pre-grant it in any pre-approved-command /
   `allowed-tools` list; the harness permission prompt on this exact
   command is the independent consent checkpoint.

Execution (the implement→verify loop) is out of this primitive's scope
(spec-lifecycle.md §3.1) — this skill's job ends at an approved plan. Once
gate 3 is recorded, `/lifecycle-archive` is what folds this change into the
living spec.

## Never

- Never grade a milestone against anything but its own pre-committed
  Validation contract, and never leave a Validation contract vague enough
  that a future session could satisfy it without doing the work.
- Never reuse a stale gate-2 `deviation.json` at this gate — always
  re-invoke `/plan-gate`.
- Never run `lifecycle approve` without the human's explicit approval.
