## Milestone 1: `orch validate` subcommand
**Goal** — Add `orch validate <change-id> [--repo PATH]` as a standalone, daemon-free plan validation command.
**Deliverables** — `orchestration/cli/validate_cmd.py` (new); subcommand registration in `orchestration/cli/main.py`; `tests/test_cli_validate.py` (new).
**Validation contract** — checkable acceptance criteria, pre-committed:
  - `uv run pytest tests/test_cli_validate.py -q` — all tests pass
  - `uv run ruff check orchestration tests && uv run ruff format --check orchestration tests` — clean
  - Makes pass: cli-validate spec scenarios "Valid plan summarized", "Invalid or unknown change rejected with guidance", "Missing lifecycle binary is an environment error"

  ```contract
  check: uv run pytest tests/test_cli_validate.py -q && uv run ruff check orchestration tests && uv run ruff format --check orchestration tests
  criteria: orch validate resolves the repo like the launcher (git toplevel of cwd, overridable with --repo), loads milestones via orchestration.resume.plan.load_milestones_from_apply, prints one line per milestone (id, title, contract present or not) plus a total, and exits 0; a PlanReadError prints the error to stderr plus the available non-archive change folders and exits 1; lifecycle missing from PATH prints an install hint to stderr and exits 2. No daemon call, no docker call. Tests monkeypatch the lifecycle boundary (shutil.which and load_milestones_from_apply) so they are hermetic.
  paths:
    - orchestration/cli/validate_cmd.py
    - orchestration/cli/main.py
    - tests/test_cli_validate.py
  ```
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml, spec-lifecycle.md §10):
  1. Write `orchestration/cli/validate_cmd.py`: `cmd_validate(args)` resolving the repo (reuse the launcher's `_resolve_repo` pattern from `orchestration/cli/launch_cmd.py` — extract or mirror it, do not import a private helper across modules if extraction is cleaner), checking `shutil.which("lifecycle")` (absent → hint + exit 2), calling `load_milestones_from_apply(change_id, cwd=repo)`, printing `<id>  <title>  [contract|no contract]` per milestone plus `N milestone(s), plan valid`, and returning 0; on `PlanReadError`, print the error + available (non-archive) change folders to stderr and return 1. Include a `register(sub)` that adds the `validate` parser (`change_id` positional, `--repo`).
  2. Register the subcommand in `orchestration/cli/main.py`'s `build_parser()`.
  3. Write `tests/test_cli_validate.py` covering the three spec scenarios (monkeypatched `shutil.which` / `load_milestones_from_apply`): summary lines + exit 0; PlanReadError path prints available changes + exit 1; missing binary → exit 2.

## Milestone 2: Documentation for the new verb
**Goal** — Surface `orch validate` everywhere the CLI's verbs are documented.
**Deliverables** — README quickstart line; `docs/cli-design.md` command surface entry; `skills/orchestration-launch/SKILL.md` mention as the pre-launch check.
**Validation contract** — checkable acceptance criteria, pre-committed:
  - `grep -q "orch validate" README.md docs/cli-design.md skills/orchestration-launch/SKILL.md` — all three mention the verb
  - `uv run ruff check orchestration tests` — still clean (no code drift in a docs milestone)

  ```contract
  check: grep -q "orch validate" README.md && grep -q "orch validate" docs/cli-design.md && grep -q "orch validate" skills/orchestration-launch/SKILL.md && uv run ruff check orchestration tests
  criteria: README's Quickstart block gains an `orch validate <change-id>` line with a one-phrase description; docs/cli-design.md documents the verb, its --repo flag, and its 0/1/2 exit-code mapping consistent with §10; the orchestration-launch skill tells agents to run orch validate before orch launch. Wording stays consistent with the spec delta (summary lines + exit codes). No source files under orchestration/ change in this milestone.
  paths:
    - README.md
    - docs/cli-design.md
    - skills/orchestration-launch/SKILL.md
  ```
**Steps** — ordered breakdown, sized per `planGranularity` (lifecycle.yml, spec-lifecycle.md §10):
  1. Add `orch validate <change-id>` to README's Quickstart command block.
  2. Add the verb to docs/cli-design.md's command surface (flags + exit codes, matching §10's 0/1/2 contract).
  3. Update `skills/orchestration-launch/SKILL.md` to recommend `orch validate` as the pre-launch plan check.
