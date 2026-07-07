---
name: lifecycle-design
description: Conduct the design stage of a spec-lifecycle change — design.md with an NFR-discharge section and any ADR proposals, gated at gate 2 alongside the constitution plan-gate. Invoke explicitly with /lifecycle-design.
disable-model-invocation: true
---

# lifecycle-design

Conduct the design stage of ONE change, in a fresh session, and stop at
gate 2. Your entire input is the gate-1-approved `proposal.md` and
`specs/**/spec.md` in this change folder — re-read them from disk rather
than trusting a memory of an earlier conversation (spec-lifecycle.md §3.1,
"the artifact is the interface"). Skip this whole stage only if refine
proposed, and gate 1 approved, a design-skip (`designSkipped: true` on the
refine gate entry — check with `lifecycle status --change <change>`); go
straight to `/lifecycle-plan` in that case. The plan-gate still runs at
gate 3 regardless of a design-skip.

## What this stage produces

`design.md` — Context, Goals/Non-Goals, Decisions (with alternatives
considered), an explicit **NFR Discharge** section accounting for every NFR
the refine delta declared that belongs here (spec-lifecycle.md §4.1 —
internal-quality concerns with no externally observable behavior), and any
**ADR proposals** this design requires as separate files,
`adr-proposals/*.md`, in MADR body shape (Context and Problem Statement /
Decision Drivers / Considered Options / Decision Outcome). Fill it from
`openspec/schemas/kentra-spec-lifecycle/templates/design.md`. ADR proposals
are ephemeral drafts in the change folder — they never enter
`constitution/adr/` directly; only an accepted proposal, written via the
constitution primitive's own flow, becomes a real ADR (see step 2 below).

## The constitution seam (spec-lifecycle.md §7) — do this before requesting approval

1. Read `constitution/constitution.md` for context while drafting the
   design, same as any planning work in a constitution-governed repo.
2. Invoke `/plan-gate` (the companion `adr-sourced-constitution` primitive's
   skill) against `design.md`, **explicitly telling it to write its
   `deviation.json` report to `<changefolder>/deviation.json`** instead of
   its own default `./deviation.json` — this is a plain instruction to the
   skill, not a `lifecycle` CLI flag; `lifecycle approve` only ever reads
   `deviation.json` from the fixed path `<changefolder>/deviation.json`, so
   getting the write location right here is load-bearing. If this design
   proposes new ADRs, point `/plan-gate` at the design plus its
   `adr-proposals/*.md` so both the current constitution and the proposed
   amendments are in scope.
3. Resolve every deviation `/plan-gate` reports **conform or amend** before
   moving on: either change the design so it no longer conflicts, or accept
   an ADR proposal that amends the rule (the constitution primitive's
   consent flow — its own `adr-draft`/`constitution adr new` path, gated by
   its own permission prompt). Do not carry an unresolved deviation into the
   human review in step 3 below.
4. Each ADR proposal is reviewed and consented to **individually by the
   human** — never bundled into blanket design approval (spec-lifecycle.md
   §7 item 3, HARD RULE). An accepted proposal is written the moment the
   human accepts it (via the constitution primitive's own tooling); design
   approval below is a separate, later decision.

## Gate mechanics (spec-lifecycle.md §3.3)

1. Draft `design.md` and any ADR proposals; run the constitution seam above
   until every deviation is resolved.
2. Run `lifecycle validate --stage design`. Fix every finding (this checks
   the NFR-discharge section is present; it does not grade technical
   content).
3. Surface `design.md`, the validated `deviation.json`, and every ADR
   proposal's outcome to the human. Wait for explicit approval.
4. Only after that explicit approval, run:
   ```
   lifecycle approve --stage design --approve <change>
   ```
   This shells out to `constitution deviation validate <changefolder>/deviation.json`
   before it writes anything, and surfaces a failure instead of writing a
   gate entry — if that happens, treat `design.md` or the deviation report
   as not yet ready, fix it, and return to step 2. This command is
   mutating — never pre-grant it in any pre-approved-command /
   `allowed-tools` list; the harness permission prompt on this exact
   command is the independent consent checkpoint the `--approve` flag does
   not replace.

## Never

- Never bundle multiple ADR proposals into one approval decision.
- Never run `lifecycle approve --stage design` with an unresolved deviation
  still outstanding, or without the human's explicit approval.
- Never write into `constitution/adr/` or edit `constitution/constitution.md`
  yourself — only the constitution primitive's own tooling does that.
