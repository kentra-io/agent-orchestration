<!--
  GENERATED FILE -- projection of the ADR log in constitution/adr/.
  Do not hand-edit; changes will be overwritten by the next "constitution
  regen". Only rule-bearing (## Rule) active ADRs project here; to change a
  rule, add, supersede, or deprecate an ADR instead.
-->

# Constitution

## architecture

### Fork Conductor at a pinned SHA; rebase only deliberately

The module depends on the `kentra-io/conductor` fork at an exact pinned
commit; upstream rebases are explicit decisions gated on the provider/Stub
test corpus passing unchanged, never an automatic upgrade. The patch-set
stays minimal (registration lines + one provider file) to keep rebases
cheap.

ADR-0001 · 2026-07-07

### Every shipped workflow template pins durability config

Every workflow template this module ships MUST set
`runtime.checkpoint.every_agent: true`, relocate the checkpoint dir to a
persistent path, and compute `max_iterations` from the plan.

ADR-0002 · 2026-07-07

### Engine-dictated Python; deterministic harness stays script-step-shaped

The module is authored in Python 3.12 (uv), engine-dictated by running
in-process inside Conductor's asyncio engine — the sole deliberate
deviation from the primitive family's Go default. Deterministic
verification logic MUST stay small and script-step-shaped so a later Go
port remains possible.

ADR-0003 · 2026-07-07

### GitHub writes are best-effort, bot-identity, dry-run-by-default side effects

Every GitHub side effect this module performs (comments, labels, issue close, branch push) MUST authenticate as the bot identity (KENTRA_BOT_GH_TOKEN via GH_TOKEN / gh auth setup-git — never a human's credentials), MUST be independent best effort (attempted, result recorded, never raised, never a step/run failure, never a reason to skip a sibling write), and MUST default to a hermetic dry_run performing no network I/O and requiring no token.

ADR-0005 · 2026-07-24
