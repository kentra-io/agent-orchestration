# cli-validate Specification

## Purpose
TBD - created by archiving change 011-orch-validate. Update Purpose after archive.
## Requirements
### Requirement: Standalone plan validation via `orch validate`
The CLI SHALL provide `orch validate <change-id> [--repo PATH]`, which
validates the change's plan through the same `lifecycle apply` surface the
launcher's pre-flight trusts, without contacting the daemon. On success it
SHALL print one summary line per milestone — the milestone id, its title,
and whether a structured validation contract (```contract block) is present
— followed by a total count, and exit 0. The target repo defaults to the
git toplevel of the current directory and is overridable with `--repo`.

#### Scenario: Valid plan summarized
- **GIVEN** a repo whose `openspec/changes/<change-id>/tasks.md` passes
  plan-stage validation
- **WHEN** the user runs `orch validate <change-id>` from inside that repo
- **THEN** the command prints one line per milestone with id, title, and
  contract presence, plus a milestone total, and exits 0

#### Scenario: Invalid or unknown change rejected with guidance
- **GIVEN** a repo where `<change-id>` does not exist or its tasks.md fails
  plan-stage validation
- **WHEN** the user runs `orch validate <change-id>`
- **THEN** the command prints the validation error to stderr, lists the
  available (non-archive) change folders when `openspec/changes/` exists,
  and exits 1

#### Scenario: Missing lifecycle binary is an environment error
- **GIVEN** `lifecycle` is not on PATH
- **WHEN** the user runs `orch validate <change-id>`
- **THEN** the command prints an actionable install hint to stderr and
  exits 2 (environment broken — distinct from the launcher's warn-and-
  proceed behavior, because validation is this command's entire job)

