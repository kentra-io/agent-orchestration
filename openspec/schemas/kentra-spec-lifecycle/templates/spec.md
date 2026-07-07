<!--
  One capability delta per file: openspec/changes/<change>/specs/<capability>/spec.md.
  Functional and non-functional requirements are merged here (spec-lifecycle.md
  §4.1) — a measurable, behavior-observable NFR is an ordinary requirement
  below, not a separate document.

  This file demonstrates the ADDED case. A delta may instead (or in
  addition) carry any of these sections, each holding one or more full
  requirement blocks (or, for REMOVED, just names):
  - `## MODIFIED Requirements` — paste the requirement's ENTIRE existing
    block from openspec/specs/<capability>/spec.md and edit it; a partial
    block loses detail at archive time (spec-lifecycle.md §6.1 fold).
  - `## REMOVED Requirements` — a bare `### Requirement: <name>` header (or
    a `- \`### Requirement: <name>\`` bullet) is enough; no body needed.
  - `## RENAMED Requirements` — `FROM:`/`TO:` bullet pairs naming the old
    and new requirement text.
-->

## ADDED Requirements

### Requirement: <name>
The system SHALL <behavior>.

#### Scenario: <name>
- **GIVEN** <precondition>
- **WHEN** <action>
- **THEN** <outcome>
