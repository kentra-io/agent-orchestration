---
name: lifecycle-new-feature
description: Intake for a feature idea that isn't tracked anywhere yet — clarify it, file the source-tracking issue on explicit human confirmation, seed a refine-ready change folder, and hand off to /lifecycle-refine. Invoke explicitly with /lifecycle-new-feature.
disable-model-invocation: true
---

# lifecycle-new-feature

Intake for ONE untracked feature idea, run before any lifecycle stage exists
for it. This is the on-ramp, not a lifecycle stage itself: it sits above
gate 1, produces no spec delta and no gate record, and its only job is to
get the idea from "someone's head" to "a source-tracking issue plus a
change folder `/lifecycle-refine` can pick up from scratch." If the idea is
actually a defect in behavior the spec already describes, stop here and
send the human to `/lifecycle-bug` instead — this skill is feature-only.

## What this stage produces

- A GitHub source-tracking issue, `type: feature`, created only after the
  human explicitly confirms the drafted text.
- A seeded change folder, `openspec/changes/<issue-number>-<slug>/`, holding
  a **stub** `proposal.md` — frontmatter filled in (`issue:`, `type: feature`),
  body left as a placeholder. No `specs/**/spec.md` delta, no `design.md`,
  no `approval-state.json` entry. Real requirements are `refine`'s job, not
  this one.

## Intake flow

1. **Clarify just enough to file a good issue.** Talk with the human until
   you have a crisp one-line title and a short Why/What paragraph — the
   problem and the shape of the fix, not full requirements. Do not draft
   Impact, alternatives, or a spec delta here; that scoping work belongs to
   `/lifecycle-refine`, which re-reads the issue from scratch anyway.

2. **Draft the issue text and show it verbatim.** Present the exact title
   and body you intend to create, then wait. Create the issue **only**
   after the human explicitly confirms this exact text — silence or moving
   on to other topics is not confirmation. If they haven't confirmed,
   create nothing and either revise the draft or stop.

   Once confirmed, run:
   ```
   gh issue create --title "<title>" --body "<body>"
   ```
   `gh` prints the new issue's URL
   (`https://github.com/<owner>/<repo>/issues/<n>`); derive the
   `<owner>/<repo>#<n>` reference from it. This is the `issue:` value the
   seeded `proposal.md` and, later, `refine`'s full proposal both key off
   (spec-lifecycle.md §10 sourceTracking join key).

3. **Seed the change folder.** Derive `<slug>` as a short, descriptive
   kebab-case handle for the change — NOT a literal kebab-case of the whole
   title sentence; match the curated style of the existing folders
   (`003-new-feature-intake`, `001-capability-size-warning`). The folder name
   follows `lifecycle.yml`'s `changeNaming` (`<issue-number>-<slug>`); that
   field states no digit width, so match the repo's observed `NNN-slug`
   convention of a 3-digit zero-padded issue number (the `042-user-auth`
   example in spec-lifecycle.md §10 and the existing `001-`/`003-` folders) —
   e.g. issue `#42` → `042-<slug>`. Create
   `openspec/changes/<issue-number>-<slug>/proposal.md` from
   `openspec/schemas/kentra-spec-lifecycle/templates/proposal.md` with only
   the frontmatter filled in — `issue: "<owner>/<repo>#<n>"` and
   `type: feature` — and the Why / What Changes / Impact sections left as
   the template's placeholders. Do not write real prose into them; a real
   body written here would be requirements work happening outside refine's
   gate.

4. **Stop and hand off.** Do not run `lifecycle validate`, do not run
   `lifecycle approve`, and do not touch `approval-state.json` — intake
   records nothing. Tell the human to start a **fresh session** and run
   `/lifecycle-refine <issue-number>-<slug>`. The handoff is deliberately a
   fresh session: refine's entire input is the approved artifacts on disk
   (spec-lifecycle.md §3.1, "the artifact is the interface"), so it re-reads
   the issue and this stub from scratch rather than trusting anything
   carried over from this conversation.

## Never

- Never create the GitHub issue without the human's explicit, conversational
  confirmation of the exact title and body — and never treat silence or a
  topic change as approval.
- Never write a `specs/<capability>/spec.md` delta — intake sits above
  gate 1 and produces no spec contract.
- Never write or append to `approval-state.json` — intake leaves no
  lifecycle gate record.
- Never modify any file under `openspec/specs/` — the living spec is
  untouched until a change is archived.
- Never write real Why/What/Impact content into the seeded `proposal.md` —
  leave the template placeholders for `refine` to fill in.
- Never use this skill for a defect in already-specced behavior — stop and
  direct the human to `/lifecycle-bug` instead.
