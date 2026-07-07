---
name: adr-draft
description: Drafts a MADR decision-record body from the current conversation, decides whether it is a standing rule (gets a ## Rule section, projects into the constitution) or a point-in-time record (log only), writes it to a temp file, and on explicit human acceptance calls `constitution adr new`. Use when a decision worth recording as an ADR emerges. Does not pre-grant mutating commands — the Bash permission prompt is the consent checkpoint.
---

# adr-draft

Use this when a decision worth governing has emerged in conversation — an
architectural choice, a convention, a policy — and it should become an ADR in
this repo's constitution. You draft the record; **the human accepts it; the CLI
writes it.** You never write into `constitution/adr/` yourself.

If the `constitution` binary is not on PATH, install the prebuilt release —
`curl -sSfL https://raw.githubusercontent.com/kentra-io/adr-sourced-constitution/main/install.sh | sh`
— do **not** build from source or install a Go toolchain.

## The consent checkpoint — read this first

Do **not** add `constitution adr new` (or `supersede`, `deprecate`) to any
`allowed-tools` / pre-approved-command list, and do not try to route around the
permission prompt (no wrapper scripts, no `eval`, no pre-authorization). When
you finally run the CLI, your harness will ask the human to approve that exact
command. **That prompt is the consent gate** — it is how a human, not the agent,
authorizes every change to the log. Working around it defeats the one guarantee
this system makes. If the command is denied, stop and leave the log untouched.

## Flow

1. **Confirm there's a decision.** One decision per ADR. If the conversation
   settled several, draft them one at a time.
2. **Decide: standing rule, or point-in-time record?** Ask the human (or judge
   from the conversation) which of the two this is:
   - A **standing rule** is a normative constraint that should govern *future*
     planning — "always X", "never Y", "prefer Z". It belongs in the
     constitution. Give it a `## Rule` section (below); it will project into
     `constitution.md`.
   - A **point-in-time record** documents a decision made now (a migration, a
     one-off choice, an accepted tradeoff) that establishes no ongoing rule. It
     belongs in the log for history, but **not** in the constitution. Omit the
     `## Rule` section; the ADR stays a catalog-only record.

   When unsure, ask: "would you want an agent planning six months from now to be
   held to this?" If yes, it is a rule; if it is just *what we did*, it is a
   record.
3. **Check for an existing rule.** `cat constitution/constitution.md`. If an
   active rule already covers this and you now disagree with it, this is a
   *supersession*, not a new record — see below. (Only rule-bearing active ADRs
   appear here; a record you cannot find may still exist in the log under
   `constitution/adr/`.)
4. **Draft the MADR body to a temp file.** Write these `##` sections, in this
   order, and nothing above the first heading:

   ```
   ## Context and Problem Statement
   <what forces a decision — the situation and the question>

   ## Decision Drivers
   <the criteria that matter: constraints, priorities, forces>

   ## Considered Options
   - <option A>
   - <option B>

   ## Decision Outcome
   <the decision and its rationale, at whatever length it needs. This no longer
   projects into the constitution — it is the durable record.>

   ## Rule            ← ONLY for a standing rule; omit for a record
   <the standing rule, 1–3 lines, stated in the imperative — this is the exact
   text the constitution renders. Say what MUST/MUST NOT happen. Keep it terse;
   regen warns past 5 lines.>
   ```

   `Context and Problem Statement`, `Considered Options`, and `Decision Outcome`
   are mandatory (the CLI rejects a body missing any of them); include
   `Decision Drivers` too — it is standard MADR and makes the record legible.
   The `## Rule` section is optional and is what makes the ADR *rule-bearing*;
   an empty `## Rule` is rejected. You may instead pass the rule on the command
   line with `--rule '<text>'` (do not do both — that is an error). Write the
   body to a temp path outside the repo tree, e.g. `/tmp/adr-draft.md`.

5. **Show the human the exact bytes that will be written**, then the command
   you intend to run. Display the temp file's literal contents — `cat
   /tmp/adr-draft.md` — and show that output verbatim. What you show **MUST**
   be the exact byte content of the file you pass to `--body-file`: the
   harness permission prompt shows only the command, not the file bytes, so
   the shown-draft==written-file property is part of the consent guarantee. If
   the human requests changes, edit the file and `cat` it again — loop until
   they accept the shown bytes — before you ever invoke the CLI. Then show the
   command:

   ```
   constitution adr new --title "<short imperative title>" --category <category> --body-file /tmp/adr-draft.md
   ```

   Pick `--category` from the project's configured vocabulary (see
   `constitution.yml`; an unknown category is rejected unless you add
   `--new-category`, which you should only do with the human's explicit say-so).
   If the project's `sourceTracking.type` is not `none`, add `--source <ref>`.
   For a standing rule, either put a `## Rule` section in the body **or** append
   `--rule '<the rule>'` — one or the other, never both.

6. **Only after the human explicitly accepts, run the command.** The harness
   permission prompt appears; the human approves; the CLI validates the body,
   allocates the id, writes the ADR atomically, and re-renders
   `constitution.md`. Show the human the created path.

7. **If the human rejects or edits:** revise the temp file and re-present, or
   **delete the temp file** and stop. A rejected draft must never reach
   `constitution/adr/` — the log stays append-only by never writing an
   unaccepted record.

## Superseding an existing rule

Same flow, but the decision replaces an active *rule*. Draft the superseding
body the same way — including its `## Rule` section (or `--rule`), since the
replacement is itself a standing rule — then run:

```
constitution supersede <ADR-NNNN> --body-file /tmp/adr-draft.md --title "<title>" --category <category>
```

The CLI writes the new ADR, marks the old one `superseded`, links them, and
re-renders. Rewording a rule, promoting a record to a rule, or demoting a rule
to a record are all supersessions too — an accepted ADR's body (including its
Rule) is frozen, so the only way to change it is a new ADR. Deprecating a rule
with no replacement is `constitution deprecate <ADR-NNNN>`. The same
permission-prompt consent gate applies to every one of these.

## Never

- Never edit files under `constitution/adr/` or edit `constitution/constitution.md` directly.
- Never pre-approve or bypass the mutating command's permission prompt.
- Never write more than one decision into a single ADR.
