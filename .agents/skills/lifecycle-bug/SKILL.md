---
name: lifecycle-bug
description: Conduct the minimal bug-flow profile of a spec-lifecycle change — repro-first, no spec delta by default, design skipped, promotable into the full feature flow. Invoke explicitly with /lifecycle-bug.
disable-model-invocation: true
---

# lifecycle-bug

Conduct the compressed bug profile of ONE change (spec-lifecycle.md §8).
This is not the feature flow with steps skipped — it is a different,
minimal artifact set built around one rule: **reproduce before you fix.**

## The compressed profile

- `proposal.md` (frontmatter `type: bug`) = the repro description plus a
  pointer to the **failing test** — this one artifact is *both* the
  requirement artifact and the validation contract; done means the test
  passes with no regressions. **Repro-first is mandatory: if you cannot
  reproduce the bug, stop and hand it back to the human as `Needs Input`
  rather than guessing at a fix.**
- **No spec delta by default.** A bug is, by definition, a failure to meet
  behavior the spec already describes — fixing it changes no contract. **If
  the repro reveals the behavior was never correctly specced**, this is
  spec-affecting: add a `specs/<capability>/spec.md` delta and gate it
  exactly like a feature refine (run `/lifecycle-refine`'s validate/approve
  steps for that delta, folded into this same change).
- `design` is skipped by default. `tasks.md` is optional and, when used,
  is usually a single milestone whose Validation contract is the repro
  test passing.

## Promotion hatch

If the fix turns out to span architecture, or would need a constitutional
deviation, **promote** this change into the full feature flow instead of
forcing it through the minimal profile: insert `design` and `plan` stages
into the same folder and hand off to `/lifecycle-design` then
`/lifecycle-plan`. A promoted bug's gate records may mix `repro`/`fix` with
`design`/`plan` in the same `approval-state.json` — that is expected;
`lifecycle status`/`guard`/`archive` key their gate-checks off the change
*type* recorded at intake, not a fixed stage list.

## Gate mechanics

1. Reproduce the bug. Write the failing test before touching the fix.
   If you cannot reproduce it, stop here and surface `Needs Input` to the
   human — do not proceed on a guessed diagnosis.
2. Run `lifecycle validate --stage repro`, fix any findings, surface the
   repro to the human, and on their explicit approval run
   `lifecycle approve --stage repro --approve <change>`.
3. Implement the fix. Run `lifecycle validate --stage fix`, fix any
   findings, surface the fix (test now passing) to the human, and on their
   explicit approval run `lifecycle approve --stage fix --approve <change>`.
4. Both `approve` invocations are mutating — never pre-grant either in any
   pre-approved-command / `allowed-tools` list; the harness permission
   prompt on the exact command is the independent consent checkpoint.
5. Archiving a delta-less bug is pure archival — `/lifecycle-archive`
   still applies unchanged; the ledger record simply carries an empty
   `deltaOps`.

## Never

- Never skip the repro step — an unreproduced bug goes back to the human,
  it does not get a best-effort fix.
- Never add a spec delta for behavior that was already correctly specced —
  that turns a bug fix into an undeclared spec change.
- Never force architecturally significant work through this minimal
  profile instead of using the promotion hatch.
