---
name: lifecycle-refine
description: Conduct the refine stage of a spec-lifecycle change — proposal.md plus a specs/<capability>/spec.md delta, gated at gate 1. Invoke explicitly with /lifecycle-refine.
disable-model-invocation: true
---

# lifecycle-refine

Conduct the refine stage of ONE change, in a fresh session, and stop at gate 1.
Refine's approved artifacts are the entire input the design stage gets
(spec-lifecycle.md §3.1, "the artifact is the interface") — do not carry
implicit context forward from an earlier conversation about this issue; if
you have one, re-read the issue and this change folder from scratch before
drafting anything.

## What this stage produces

- `proposal.md` — Why / What Changes / Impact, with the source-tracking
  issue reference in frontmatter (`issue: "<owner>/<repo>#<n>"`, required and
  non-empty), `type: feature` (default) or `type: bug`, and — for small,
  local, architecturally inert work only — `designSkip: true` proposing to
  skip the `design` stage (spec-lifecycle.md §3.2). If this change is a bug,
  use `/lifecycle-bug` instead; its compressed profile replaces this one.
- `specs/<capability>/spec.md` — one delta per capability this change
  touches (`## ADDED|MODIFIED|REMOVED|RENAMED Requirements`, RFC-2119
  `### Requirement:` blocks each with at least one `#### Scenario:`
  GIVEN/WHEN/THEN). New capability → its exact kebab-case name; existing
  capability → its existing folder name under `openspec/specs/`.

Fill both from `openspec/schemas/kentra-spec-lifecycle/templates/{proposal,spec}.md`
(written by `lifecycle init`) — those templates carry the authoritative
section shape, not just this summary.

**NFR routing (spec-lifecycle.md §4.1):** a measurable, behavior-observable
NFR is an ordinary requirement in the spec delta, merged with the functional
ones — never a separate document. A cross-cutting/project-wide invariant
(stack choice, security posture) belongs in a constitution ADR instead
(raised at the design stage). An internal-quality concern with no observable
behavior belongs in `design.md`'s NFR-discharge section instead. Every NFR
this change declares must land in exactly one of those three homes.

## Gate mechanics (spec-lifecycle.md §3.3)

1. Draft `proposal.md` and every touched capability's `specs/<capability>/spec.md`
   in this change's folder (`openspec/changes/<change>/`).
2. Run `lifecycle validate --stage refine`. Fix every finding — this is the
   same delta-grammar/structure check `approve` re-runs before writing a
   gate entry, so an invalid artifact can never slip through by skipping
   this step.
3. Surface the proposal, every delta, and any design-skip proposal to the
   human, conversationally. Wait for their explicit approval or requested
   changes — do not proceed on your own judgment that the artifact "looks
   done."
4. Only after that explicit approval, run:
   ```
   lifecycle approve --stage refine --approve [--design-skip] <change>
   ```
   Add `--design-skip` only if the human approved skipping `design` for
   this specific change. This command is mutating — do not pre-grant it in
   any pre-approved-command / `allowed-tools` list; the harness's own
   permission prompt on this exact command is the second, independent
   consent checkpoint (`--approve` satisfies `lifecycle.yml`'s
   `consentPolicy: strict`; the permission prompt is the harness boundary
   that must also fire). If the human instead asks for changes, revise and
   re-validate — do not run `approve` until they say so again.
5. A skipped `design` does not skip the constitution gate — the plan-gate
   still runs at gate 3 (spec-lifecycle.md §3.2). Say so if you record a
   design-skip, so the human isn't surprised later.

## Never

- Never run `lifecycle approve` without the human's explicit, conversational
  approval of the exact artifacts you are about to record — and never treat
  a silent lack of objection as approval.
- Never hand-edit `approval-state.json` or any ledger file.
- Never propose `designSkip: true` for work that touches architecture,
  crosses capability boundaries, or needs a constitution ADR — that is what
  the design stage and its plan-gate run are for.
