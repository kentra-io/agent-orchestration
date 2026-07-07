# workflows/

Conductor workflow templates for the execution loop.

- `execute-change.yaml` — the per-change template: reads the plan, `for_each`
  milestone, change-level finish (M5).
- `milestone.yaml` — the per-milestone sub-workflow: implementer → gates →
  verifier → counter → orchestrator/escalate (the 3-attempt ladder, M5).

Lands in **M5** (`The ladder — execute-change + milestone templates`), wired
against the StubProvider first (hermetic), then the live cast in M6.
