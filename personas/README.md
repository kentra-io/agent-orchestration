# Cast personas (the behavioral contracts)

The three Mode-B agents the escalation ladder runs (`orchestration.md` §6), as
hand-materialized Claude Code subagent definitions (`.claude/agents/<role>.md`).

| File | Role | Model / effort | Contract |
|---|---|---|---|
| `implementer.md` | Implementer | Opus / medium | §6.2 — follow-to-letter, stop-and-ask, log deviations |
| `verifier.md` | Verifier | Opus / high | §6.3 / §5.2 — fresh, evidence-or-zero, intent-vs-actual, anchored L3 rubric |
| `orchestrator.md` | Orchestrator | Opus / high | §6.1 — stateless next-attempt guidance |

## What "owns the contract, not the schema" means

This module owns the **behavioral contracts** — what each agent must do to fit
the loop (§6). It does **not** own the agent *schema* — that is
[`agent-definition`](../../agent-definition.md). These files are neutral
defaults; a consuming (branded) project may override model/frontmatter or swap
in `agentdef compile` output (M9). Per **P9** they are **hand-materialized** in
v1, so orchestration's critical path does not block on `agent-definition`
shipping.

## How they are used at runtime

The `ClaudeboxProvider` invokes, per ladder step:

```
cb exec <box> claude -p "<task context>" --agent <role> \
  --model <model> --permission-mode bypassPermissions \
  --output-format stream-json --verbose
```

`--agent <role>` makes the persona the **primary driver** of the headless run:
its system prompt replaces the default and its `tools` / `disallowedTools`
restrictions apply. The workflow's inline `prompt:` is the *per-invocation task
context* (milestone id, summary, prior guidance, the diff to judge); the persona
is the *durable contract*.

For that to resolve, each persona must be present at
`<worktree>/.claude/agents/<role>.md` in the change's box. Materialization
(copying these files + `settings.json` into the box's `.claude`) is the
launcher's / `agent-definition`'s job, not the provider's — the provider only
`cb exec`s into a box that already has them.

## Tool policy (decision 2026-07-09, user-locked; supersedes the spec draft's per-role tool surgery)

**Every cast agent ships with the default Claude Code toolset — no `tools:`
allow-lists, no `disallowedTools`.** Restricting tools per role was judged
unnecessary complexity:

- Discipline is **behavioral** (each persona's contract: the Implementer's
  web-is-never-a-source-of-requirements rule, the Verifier's
  reports-does-not-fix rule, the Orchestrator's guidance-only rule) and
  **structural** (author ≠ verifier, deterministic gates re-run independently,
  Conductor's counter owns escalation, a human clears anything L3 touches) —
  not enforced by tool surgery.
- Field research (2026-07-09) backs default-allow for the doing agent: denying
  web has documented capability cost (Codex re-shipped internet 3 weeks after
  a no-internet launch; OpenHands-Versa +9.1pp from browsing; Factory telemetry
  = web use dominated by docs/API refs).
- **Eval / benchmark runs** (known-solution milestones, Stage-5 A/B) MAY add a
  `tools:` allow-list to the Implementer at materialization — the Factory
  pattern (web in production, revoked for its own SWE-bench run). Personas are
  plain files; that's a one-line edit, no knob machinery.

Do **not** use a box-wide `settings.json` `permissions.deny` for this:
settings-level deny is authoritative even under `bypassPermissions` and the
three roles share the change's box — it would strip tools from every role at
once. If an eval run restricts anything, per-role frontmatter is the point.

Residual (accepted, §8 threat model): egress is open and the image ships
`curl`/`bash`; tool policy was never an egress boundary. Fetched web content is
a prompt-injection surface in a box holding OAuth credentials — same residual
the sandbox spike already accepted.
