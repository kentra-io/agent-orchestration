---
name: constitution-gov
description: Governs how the constitution is used — append-only log, consult-before-planning, amendments only via an ADR under the consent policy. Force-inlines the active constitution into context so governance does not depend on pointer compliance. Applies whenever planning or proposing changes in a governed repo.
---

# constitution-gov

This repository is governed by a **constitution**: an append-only log of
architectural decision records (ADRs) under `constitution/adr/`, projected into
`constitution/constitution.md`. The projection is **curated** — it renders only
the ADRs that carry a standing rule (a `## Rule` section). Point-in-time records
stay in the log as history but are deliberately absent from the constitution, so
the ruling document reads as a concise rulebook, not a decision archive. That
projection is the ruling authority for how work here is planned and built. Load
it into context now:

```
cat constitution/constitution.md
```

If the `constitution` binary is not on PATH (some steps below shell out to it),
install the prebuilt release —
`curl -sSfL https://raw.githubusercontent.com/kentra-io/adr-sourced-constitution/main/install.sh | sh`
— do **not** build from source or install a Go toolchain.

Do this at the start of every planning or architectural task, even if a pointer
already imported it — reading it directly is how you stay governed regardless of
which agent framework you are running under.

## Priority hierarchy (highest wins)

1. **The constitution** (`constitution/constitution.md`). Every active rule in
   it is binding and cites an `ADR-NNNN`.
2. **The human's current instruction**, when it conflicts with a rule — but
   surface the conflict first (see "Amending" below); do not silently break a
   rule to satisfy a request.
3. **Inferred conventions** (what the surrounding code seems to do). These lose
   to the constitution every time. If the codebase drifts from a rule, the rule
   is right and the code is the bug.

## Non-negotiable rules

- **The ADR log is append-only.** Never hand-edit a file under
  `constitution/adr/`, and never edit `constitution/constitution.md` — it is a
  generated projection. The only permitted change to an accepted ADR is its
  `status:` / `superseded-by:` line, and only the `constitution` CLI may make
  it. Editing these files directly corrupts the log; a `constitution guard` run
  will flag it.
- **Consult before deciding.** Before proposing a plan, an architecture, or a
  design change, check the constitution for a rule that already governs it. Cite
  the `ADR-NNNN` you are relying on when you do.
- **Amend only through the flow.** If a rule is wrong or in the way, you do not
  override it and you do not edit it. You propose a new ADR (the `adr-draft`
  skill) that supersedes it, and the human approves that write under the
  project's consent policy. Amendment is a governed act, never an edit.

## Why it works this way

Rules live in individual, immutable decision records so their history and
rationale survive; `constitution.md` is only a view of the currently-active,
rule-bearing set. Not every decision is a rule — a decision that establishes no
standing constraint is recorded in the log without a `## Rule` section and never
projects, keeping the constitution a tight list of what actually governs. That
is why edits go through the CLI (it preserves the append-only log and re-renders
the view) and why amendments are new records, not rewrites: the question "why is
this a rule, and what did it replace?" must always be answerable from the log.
