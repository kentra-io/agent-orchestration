<!-- BEGIN adr-sourced-constitution v1 (managed — do not edit by hand; `constitution init` updates it) -->
Before planning, read `constitution/constitution.md`; it is this project's governing constitution and takes precedence over inferred conventions.
<!-- END adr-sourced-constitution v1 -->

<!-- BEGIN spec-lifecycle v1 (managed — do not edit by hand; `lifecycle init` updates it) -->
This project uses `lifecycle` (spec-lifecycle) for staged, gated planning — see `openspec/`. Run `lifecycle status` for gate state; approve gates only via `lifecycle approve`, never by hand-editing `approval-state.json`.
<!-- END spec-lifecycle v1 -->

## TEMPORARY — verify durable telemetry on the next run (added 2026-08-07)

The 2026-07-27 run-hardening change moved Conductor's default tmpdir out of the
worktree to `~/.agent-orchestration/runs/<slug>--<change>/conductor/`, so run
telemetry survives worktree teardown (`orchestration/launch/change.py`,
`orchestration/obs/registry.py::run_dir`). The code is unit-tested and the daemon
and harness box were rebuilt onto it, but **no live plan has run against it yet**.

**After the next full plan run, verify:**

```bash
ls -la ~/.agent-orchestration/runs/<slug>--<change>/conductor/
```

Telemetry must be present there **after the run's worktree is removed** — that is
the property the change exists for. Check it is non-empty and that nothing was
written into the worktree's own tmpdir instead.

**Then delete this section** — it is a one-shot reminder, not standing guidance.
If the check fails, do not delete it: file an issue and leave the note until the
fix lands and passes.
