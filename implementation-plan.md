# `orchestration` — Implementation Plan (v1)

*Generated: 2026-07-07. Status: **PLAN v1 — LOCKED 2026-07-07** (P1 name + P2 language explicitly user-locked; P3–P10 stand as reviewed and each remains individually vetoable). Implementing session: start at **§12 Kickoff**. Companion to [orchestration.md](./orchestration.md) (the design spec); sibling in shape to [`spec-lifecycle/implementation-plan.md`](./spec-lifecycle/implementation-plan.md) and [`adr-sourced-constitution/implementation-plan.md`](./adr-sourced-constitution/implementation-plan.md). Lives at the harness root **pre-extraction** (decided 2026-07-07) and moves with the spec when the module gets its repo. Produced from the spec + the 2026-06-16 source recon ([`references/conductor-integration-notes.md`](./references/conductor-integration-notes.md)) + a **2026-07-07 live-source verification pass** against `microsoft/conductor` — pinned `main` @ `7aaa58975601ecfaf42cdf6d048e0b4cd3e36028`, latest release **v0.1.20** (2026-06-27). All Conductor claims below are verified at that SHA; the errata the pass surfaced are folded into the spec and recorded in §1.*

> **What this document is.** The sequenced, buildable plan for **v1** of the orchestration module. The spec decides *what it is*; this decides *what gets built, in what order, with which concrete stack*, pins every TBD the spec left open (§2), and records the errata verification surfaced (§1). Milestones carry validation contracts (Definition of Done); nothing is complete without proving it. Every decision here is an assumption the user can veto at review.

> **Fine-direct — the sequencing decision this plan implements (locked 2026-07-07).** The coarse walking-skeleton (Conductor wrapping one smart Orchestrator with an opaque nested inner loop) is **skipped**: its inner loop is throwaway — none of it survives into the fine-grained model. Instead the plan sequences the **shared plumbing first** (provider → deterministic harness → ladder template), so an early end-to-end demo exists by M5 without building a disposable mode.

---

## 0. v1 scope (proposed — vetoable at review)

| # | Question | Decision |
|---|---|---|
| **P1** | Repo / name / neutral-branded cut | Two repos. **(a) `kentra-io/conductor`** — a public fork of `microsoft/conductor`, patch branch `kentra-patches` off the pinned SHA, carrying the ClaudeboxProvider + its ~15 lines of registration edits (+ the optional `AgentDef.metadata` add). **(b) the neutral module repo** — workflow templates, verification harness, escalation/resume seam, Conductor-MCP, docs. Name **✅ LOCKED 2026-07-07: `agent-orchestration`** (user). The **branded layer** (kentra cast defs, hand-materialized personas, concrete workflow wiring) stays in the harness. Per ADR-0001/0002: neutral repo + submodule; `kentra-` only at the branded layer. |
| **P2** | Language / stack | **✅ LOCKED 2026-07-07 (user).** **Python 3.12 + uv** — the engine dictates it (the provider runs in-process inside Conductor's asyncio loop; a Go detour would be a Python shim shelling out to a binary — complexity, no benefit) (Conductor is Python/Pydantic-v2/Jinja2; the provider seam is a Python ABC). This is the **only non-Go member of the primitive family** — a deliberate, engine-dictated deviation. ADR-0003 mandates repo *shape*, not language — no constitution conflict; optionally record an "engine-dictated language" ADR. Ruff + pytest; no Go in v1. |
| **P3** | Conductor consumption mode | **Fork + pin, rebase deliberately.** The module depends on the fork at an exact SHA (uv git dependency). Upstream rebases are an explicit decision (like `spec-lifecycle`'s format-drift posture), gated on the provider test corpus going green — never an upgrade treadmill. The patch-set stays minimal (~15 registration lines + 1 provider file + optional 2-line metadata add) precisely so rebases stay cheap. |
| **P4** | Durability configuration | Conductor's defaults are **insufficient** (verified): checkpoint on failure only, into `$TMPDIR`, `limits.max_iterations` default 10. Every workflow we ship sets `runtime.checkpoint.every_agent: true`, relocates the checkpoint dir to a persistent path (mechanism = spike S2), and computes `max_iterations` from the plan (≥ milestones × 4 + overhead). Without this, the spec's "crash-safe counter" claim is false — this is the plan's most load-bearing config pin. |
| **P5** | Ladder mechanism | **Explicit `set`-step counter per milestone** (`attempts: "{{ (counter.output.attempts \| default(0) \| int) + 1 }}"`), routed on by `when` conditions. Rejected: the `context.history`-filter idiom (too clever, harder to read in checkpoints). Native `retry:` is reserved for **transient provider errors only** (`retry_on: [provider_error, timeout]`) — orthogonal to resolution attempts, never counted against the ladder. Conductor's native `validator:` block is **NOT used for the ladder**: its hard-wired auto-1-retry would double-count attempts outside our counter. (Possible reuse *inside* L3 = spike S4; default is our own Verifier step.) |
| **P6** | Correlation | **Run-level, native**: `conductor run -m issue=42` → `WorkflowDef.metadata` → `workflow_started` event (no patch needed). Per-invocation `experiment_id`/`variant` are **stamped by our provider** (into events + LiteLLM-bound env/headers). The `AgentDef.metadata` fork patch is **deferred** until Stage 5 demonstrates a per-step need — it's a 2-line add on a fork we already maintain, so deferring costs nothing. |
| **P7** | `Needs human input` home (spec §14.3) | **Canonical = Conductor run-state** (the paused `human_gate` + its checkpoint). **Mirrored** to a GitHub issue label (`needs-human-input`) by a workflow notification step; the Conductor-MCP queue view reads both. `spec-lifecycle` is untouched — its statuses are gates, not run states. |
| **P8** | Operator surface + trigger | **MCP-only** (no thin CLI — spec §14.7): the six `lifecycle` verbs 1:1 + workflow-control (list runs, inspect the escalation queue, `gate-respond`, resume). Trigger = **explicit `conductor run`** in v1 (spec §13/§14.5 — auto-trigger deferred). |
| **P9** | Persona source (de-risking) | v1 runs on **hand-materialized** `.claude/agents/<role>.md` files checked into the branded layer — the ClaudeboxProvider only needs the file present in the box. `agentdef compile` swaps in when the sibling primitive ships (M9); orchestration's critical path does **not** block on `agent-definition` being built. |
| **P10** | Concurrency v1 | **Process-per-change**: one `conductor run` per change, each in its own worktree+box. The embedded-`WorkflowEngine` aggregator (recon §2) is real and stays the fleet-dashboard path — **deferred**; v1 needs isolation, not a daemon. |
| **P11** | Auth / billing (✅ LOCKED 2026-07-08, user) | **Subscription OAuth only, never the Anthropic API.** Every agent box runs `claude` on the box's injected `.credentials.json` (OAuth) — the same auth the operator's interactive claudebox uses daily. **`ANTHROPIC_API_KEY` must never be set in an agent box** (Claude Code prefers it and would silently bill the API). The `ANTHROPIC_BASE_URL/AUTH` env on the provider exec (§2, line 54) is **Stage-4-gateway-only** — absent for all Stage-3 (M6/M9) live runs. Cost is a non-constraint as long as everything runs through claudebox; the *only* failure mode to guard is an API key leaking into a box's env. This is why the default Conductor `claude.py` provider (raw `AsyncAnthropic`) is structurally unusable (erratum #8) — the whole ClaudeboxProvider exists to keep execution on subscription auth inside the box. |

**Non-goals for v1** (spec §13 + above): parallel milestones within a change · the Stage-5 controller/evaluator/promotion · auto-trigger on plan-approved · network-egress sandbox / git-history denial (spiked & rejected) · fleet dashboard / aggregator daemon · persona GUI · sophisticated Orchestrator (guidance-only in v1).

---

## 1. Spec errata — verification findings folded into the spec (2026-07-07)

All applied to `orchestration.md` (and the shared figure to `agent-definition.md`).

1. **There is no provider plugin API — "one fork patch" understated the coupling.** Providers are hard-coded in `Literal`/`match` sites (`providers/factory.py:86`, `factory.py:27`, `registry.py:18`, `config/schema.py:657`, `schema.py:1660`). The ClaudeboxProvider + ~15 lines of registration edits across ~5 sites land as a **fork patch branch**, not a drop-in extension. Spec §2 rewritten.
2. **Provider LOC: ~250–400 Python, not ~150 Go.** The seam is a Python ABC (`providers/base.py`: async `execute()`/`validate_connection()`/`close()` + class-level `CAPABILITIES` enforced at import). Reference providers run 969–2,374 lines; a minimal subprocess provider with structured-output parsing, error mapping, and event streaming budgets ~250–400 LOC. Spec §0/§8/§11.1 + agent-definition §2/§5/§11.5 corrected.
3. **Durability is checkpoint-based and off by default.** Failure-only checkpoints into `$TMPDIR`; per-step durability requires `runtime.checkpoint.every_agent: true` (or `every_seconds`); resume via `conductor resume` **re-runs the paused/failed step**; `limits.max_iterations` defaults to 10. Spec §2/§12 now carry the caveat; P4 pins the config.
4. **`AgentDef.metadata` patch is optional.** `AgentDef` is `extra=forbid` with **no** metadata field (the patch *adds* one, 1–2 lines) — but `WorkflowDef.metadata` is native, run-level, settable via `-m`, and surfaces in `workflow_started`. Run-level correlation needs no fork change (P6). Spec §2 corrected.
5. **The ladder is expressible natively** — loop-back `routes` + a `set`-step counter (or `context.history` filters); both serialize into checkpoints, so **the count survives crash/resume** (the spec's central durability claim holds, given erratum 3's config). Native `retry:` is transient-only and can't drive routing — consistent with P5's split.
6. **Conductor ships a native `validator:` block** (LLM grades output, auto re-runs the agent exactly once) and an `examples/implement.yaml` coder→reviewer→fixer example. Overlapping-but-different: the auto-retry conflicts with our attempt accounting (P5); our module remains additive. Evaluate `validator:` reuse *inside* L3 as spike S4.
7. **Headless gate-answering is native.** `conductor gate-respond --port N --choice X --token …` (HTTP, token-auth) answers a parked `human_gate` while the process lives; durable-across-death pause = checkpoint + `resume` re-running the gate step. The escalation-resolve leg needs less custom code than the spec assumed — the poll-seam (§7.2) drives *when* to respond/resume, not *how*.
8. **Default providers confirmed unusable for us** (validates the spec's core prohibition): `claude.py` hits the raw Anthropic API (`AsyncAnthropic`, `messages.create`); the experimental `claude-agent-sdk` provider drives the CLI but host-side with no `cwd`/`agents`/`--agent` equivalent. Neither sees a materialized persona, the box, or the worktree. The ClaudeboxProvider is genuinely necessary, not a preference.
9. **Recon `--bare` correction.** The 2026-06-16 recon's sample invocation used `claude --bare`; that predates the 2026-07-06 overlay decision (agent-definition §5.1). The provider invokes `claude -p --agent <role>` **without** `--bare` — auto-discovery is wanted (the worktree skills channel), and isolation comes from the overlay, not flag surgery.

---

## 2. The fork & the provider (the Conductor-facing core)

**Fork:** `kentra-io/conductor`, branch `kentra-patches`, based on pin `7aaa589` (v0.1.20). Contents, kept deliberately minimal (P3):
- `src/conductor/providers/claudebox.py` — the provider (below).
- Registration edits: `factory.py` (match arm + `ProviderType` Literal), `registry.py` Literal, `config/schema.py` `AgentDef.provider` + `ProviderSettings.name` Literals. ~15 lines total.
- *(Deferred, P6):* `AgentDef.metadata: dict[str, Any] = Field(default_factory=dict)`.

**ClaudeboxProvider** (~250–400 LOC), modeled on the recon §1 contract:
- `execute(agent, context, rendered_prompt, tools, interrupt_signal, event_callback)` → `asyncio.create_subprocess_exec("cb", "exec", <box>, "env", <ANTHROPIC_BASE_URL/AUTH for Stage 4>, "claude", "-p", rendered_prompt, "--agent", <role>, "--model", agent.model, "--permission-mode", "bypassPermissions", "--output-format", "stream-json", "--verbose")` — as the non-root `agent` user (bypass is refused as root). Streams `stream-json` lines → `event_callback` (`agent_turn_start`/`agent_message`/`agent_tool_*`); extracts the terminal `result` (usage, `total_cost_usd`) → `AgentOutput{content, raw_response, tokens…}`. Structured output: inject the step's `output:` schema instructions; parse JSON from the result (parse-recovery bounded like the reference providers).
- `CAPABILITIES`: `concurrent_safe=True`, `checkpoint_resume=False` (each step = fresh `claude` subprocess; the durable resume unit is the step, re-run from the worktree — exactly the artifact-is-the-interface model).
- `validate_connection()` — `cb` present + target box reachable. Box lifecycle is **outside** the provider: `cb run` (worktree + overlay mounts) happens at run start (a `script` step / the launcher); `execute()` only `cb exec`s into the existing box.
- Role→box/worktree resolution comes from workflow context (the `execute-change` template passes the change's box id + worktree path to every step).

---

## 3. Stack & repo layout

| Concern | Choice | Notes |
|---|---|---|
| Language / packaging | Python 3.12+, **uv**, `pyproject.toml` | mirrors Conductor itself (P2) |
| Engine dependency | `conductor @ git+…kentra-io/conductor@<SHA>` | fork pin (P3); rebases deliberate |
| Lint / test | ruff · pytest (+ pytest-asyncio) | |
| Deterministic harness code | small Python packages invoked as Conductor `script` steps | script steps merge parsed-JSON stdout into context → routable (verified) |
| MCP server | Python (same repo), stdio MCP | six `lifecycle` verbs 1:1 (shell-out to the `lifecycle` binary) + workflow-control (list runs via event-JSONL, `gate-respond`, `resume`) |
| CI | GitHub Actions: uv + ruff + pytest (Stub tier hermetic); live tier manual/labeled | no Go toolchain |

```
agent-orchestration/                 ← name pending user lock (P1)
  pyproject.toml                     ← dep: kentra-io/conductor @ pinned SHA
  orchestration/
    harness/                         ← L1 runner · L2 healthcheck · diff-path checker · deviation cross-check
    launch/                          ← execute-change launcher: worktree + `cb run` (overlay mounts) + `conductor run` wiring, durability config (P4)
    resume/                          ← the poll-seam: `lifecycle status --format json` watcher → gate-respond/resume
    mcp/                             ← the Conductor-MCP (P8)
  workflows/
    execute-change.yaml              ← the template (§4)
    milestone.yaml                   ← per-milestone sub-workflow (ladder lives here)
  tests/
    stub_provider.py                 ← scripted-verdict provider (§6)
    fixtures/testbed/                ← tiny real repo: tests + plantable defects
  docs/
  orchestration.md  implementation-plan.md  README.md  LICENSE (MIT)
```

Branded layer (harness repo, not this module): `agents/*.md` hand-materialized cast (P9), kentra-specific wiring/config.

## 4. The `execute-change` template — shape

```yaml
# execute-change.yaml (sketch; -m issue=42 at launch = run-level correlation, P6)
workflow:
  runtime: { checkpoint: { every_agent: true } }        # P4
  limits:  { max_iterations: <computed> }                # P4
agents:
  - name: read_plan            # script: milestones+contracts as JSON (from the §5.5 apply surface)
    type: script
  # for_each milestone → sub-workflow milestone.yaml, max_concurrent: 1 (sequential; ordering = spike S1)
  # milestone.yaml:
  - name: implementer          # provider: claudebox, agent implementer
  - name: gates                # script: L1 + L2 + diff-confined-paths + deviation cross-check → {pass, report}
  - name: verifier             # provider: claudebox, agent verifier (coverage matrix, L3, intent-vs-actual)
    routes:
      - { to: "$end",        when: "{{ gates.output.pass and verifier.output.pass }}" }
      - { to: counter,       when: "true" }
  - name: counter              # set: attempts += 1   (P5; checkpoint-durable)
    routes:
      - { to: orchestrator,  when: "{{ counter.output.attempts < 3 }}" }
      - { to: escalate,      when: "{{ counter.output.attempts >= 3 }}" }
  - name: orchestrator         # provider: claudebox — emits next-attempt guidance → implementer
  - name: escalate             # notify (GH label, P7) → human_gate → resume routes (§7.2 poll-seam)
```

Change-level finish (all milestones pass): full L2 healthcheck → `lifecycle archive` hand-off (tasks-completion gate, from the §5.5 change) — run via the launch context that holds approval authority, never a Mode-B agent (spec §7.3).

---

## 5. Milestones — each with a validation contract

Cross-repo work is marked; those milestones run through **their own repo's lifecycle gates**.

### M0 — Fork + pin + module bootstrap
*(Repos exist — created 2026-07-07: `kentra-io/conductor` fork with `main` @ the pin `7aaa589`; `kentra-io/agent-orchestration` public/empty. Bot has push on both.)* Cut `kentra-patches` @ `7aaa589` with the registration-edit skeleton (provider stub). Bootstrap the module repo: uv project, ruff/pytest, CI, fork consumed as pinned git dep, MIT license, spec + this plan moved in (extraction). Make it **lifecycle-managed from day one** (`lifecycle init` + `constitution init`, like every sibling) and **seed the constitution** with ADRs encoding the locked pins — the spec-lifecycle precedent (its 3 ADRs encode its plan's core decisions): (1) fork+pin+deliberate-rebase gated on the test corpus (P3); (2) mandatory durability config in every shipped template (P4); (3) engine-dictated Python, deterministic harness kept script-step-shaped (P2). Register the module as a harness submodule (ADR-0001).
**DoD:** CI green; `conductor run` executes a trivial example workflow from our fork pin inside the box; the stub `claudebox` provider type is registered and instantiable; `lifecycle status` + `constitution` work in the repo with the seed ADRs projected; harness submodule wired and pushed.

### M1 — ClaudeboxProvider + StubProvider  ★ the seam
The §2 provider against a real box (plain, pre-overlay); the **StubProvider** test double (§6) beside it.
**DoD:** a 2-step workflow runs a real `claude -p --agent <role>` (hand-materialized persona, P9) inside a box — structured output lands in context, events stream to the JSONL, cost/tokens captured; `validate_connection` fails informatively without a box; Stub tier runs the same template hermetically; kill the provider subprocess mid-step → step re-runs cleanly on `resume` (checkpoint_resume=False semantics proven).

### M2 — **[claudebox]** overlay isolation + `:ro` rider
The skills+plugins overlay seam (agent-definition §5.1/§11.3) + the read-only spec/tests mounts riding it (spec §8). In the claudebox fork, through its own gates.
**DoD:** a box launched with a per-agent overlay sees **only** the provisioned skills/plugins (host operator scope demonstrably absent); worktree-channel skills still discovered; `spec.md`/`tasks.md`/declared test paths are read-only to the agent (write attempt fails); plain-box behavior unchanged when no overlay requested.

### M3 — **[spec-lifecycle]** the §5.5 change
One lifecycle-managed change in `spec-lifecycle` (dogfooding it): (a) plan template carries the per-milestone **validation contract** (optional acceptance-check command, plain-language criteria, allowed path-set) + `lifecycle validate` enforces the structure; (b) `archive` gains the **tasks-completion gate**; (c) the **`apply` surface** becomes real and machine-readable (milestones + contracts as JSON — verb shape decided in that change's design stage).
**DoD:** that change's own gates + acceptance; from this module's side: `read_plan` gets milestones+contracts as JSON from a fixture change without bespoke markdown parsing.

### M4 — Deterministic verification harness
`orchestration/harness/`: L1 runner (exit-code gate), L2 healthcheck (configured suite/build/lint), diff-confined-to-declared-paths checker (git diff vs the milestone's path-set), deviation cross-check driver (`deviation.json` vs diff), flakiness quarantine (<95% over N reruns → quarantined, not retried-green). All as script-step-callable, JSON-out.
**DoD:** on the fixture testbed — planted out-of-path file → mechanical FAIL; planted undeclared deviation → cross-check FAIL; flaky test → quarantined with report, never silently green; all checks pure/deterministic (same input → same verdict), no LLM.

### M5 — The ladder — `execute-change` + `milestone` templates  ★ fine-direct demo
§4 wired end-to-end over the StubProvider: sequential `for_each` (S1), counter + routes, orchestrator resolution step, `human_gate` escalate, durability config (P4).
**DoD (Stub tier, hermetic):** scripted verdict sequences produce exactly the locked semantics — pass@1/2/3 advance; 3 fails → `Needs human input` + GH-label mirror step invoked; guidance from the orchestrator step reaches attempt N+1's prompt; **`kill -9` mid-attempt-2 → `conductor resume` continues with count intact**; transient provider error consumes `retry:`, **not** an attempt; `max_iterations` never bites on a 10-milestone plan.

### M6 — The cast prompts + live verification  ★ prompt-design risk lives here
The behavioral contracts (spec §6) as real personas (hand-materialized, P9), all on the default Claude Code toolset (2026-07-09 posture): Implementer MUST/HALT rules; Verifier (fresh, coverage matrix, evidence-or-zero, intent-vs-actual, L3 anchored rubric → 0.0–1.0 + pass/fail); Orchestrator (guidance-only).
**DoD (live tier, fixture testbed):** a planted **undeclared deviation** and a planted **false completion** (`[x]` with no change) are each caught by the Verifier; a clean milestone passes all three layers; the L3 verdict is well-formed against the rubric; the Implementer halts (QUESTION) on a deliberately ambiguous task instead of improvising. *(Tool posture revised 2026-07-09, user-locked: every cast agent ships the default Claude Code toolset — the original "Implementer has no WebSearch/WebFetch" DoD item is dropped; eval runs may restrict at materialization — spec §5.3/§8.)*

### M7 — Escalation-resolve + Conductor-MCP
`orchestration/resume/`: the poll-seam (`lifecycle status --format json` → detect resolution → `gate-respond`/`resume`; re-derive remaining milestones if `tasks.md` materially changed — hash-compare). `orchestration/mcp/`: the P8 surface.
**DoD:** end-to-end on the testbed — escalated change paused; a Mode-A session edits the plan + approves; the watcher detects it and resumes **from the failed milestone**; a materially-changed plan re-derives the remaining list; via MCP an operator lists runs, inspects the escalation queue with the three Verifier reports, and resolves a gate; `lifecycle approve`/`archive` absent from every Mode-B agent's surface (checked in the personas + template).

### M8 — Change-level finish + concurrency
Launcher (`orchestration/launch/`): worktree + `cb run` (overlay) + `conductor run` per change (P10); change-level full healthcheck; `lifecycle archive` hand-off honoring the tasks-completion gate (M3).
**DoD:** two changes run concurrently in separate worktrees/boxes/processes with zero interference (git-ACID proven by interleaved commits); a completed change archives and folds only when all tasks are ticked; an incomplete one is refused by the gate.

### M9 — Acceptance + agentdef integration seam
A real change end-to-end on the dogfood target (kafka-dq testbed or a harness-family repo): plan-approved → milestones → at least one **genuine** escalation exercised → resolve → resume → archive. If `agent-definition` has shipped: swap P9's hand-materialized personas for `agentdef compile` output (byte-diff the materialized files to prove equivalence); else record the swap as the sibling plan's first integration item.
**DoD:** the full spec §15 walkthrough demonstrated on real work; every layer (L1/L2/L3/paths/deviation) exercised at least once; run-level correlation visible in the event JSONL (the Stage-4 join point).

*Sequencing:* **M0→M1→M5 is the critical path** (fork+provider+ladder). M2 [claudebox] and M3 [spec-lifecycle] are parallel tracks — M2 gates M6's isolation claims, M3 gates M5's `read_plan` (a fixture JSON can stub it meanwhile). M4 precedes M5; M6 needs M1+M2+M4; M7 needs M5; M8 needs M5+M3; M9 last. Riskiest: **M6** (prompt design — the same "real design work" flag the sibling plan put on its skill bodies), then M5 (durability semantics) and M1 (stream-json parsing against the pinned CC version).

---

## 6. Testing strategy

- **Stub tier (hermetic, every PR).** The **StubProvider** — a fork-registered test provider returning scripted `AgentOutput` sequences per step name — makes the *entire control-flow* (ladder, counter, routes, gates, crash/resume, max_iterations) testable without an LLM, a box, or a network. Golden event-JSONL assertions for each scenario. This is the module's analogue of `spec-lifecycle`'s hermetic Go tier.
- **Harness unit tier (every PR).** The M4 checkers against the fixture testbed: property = deterministic verdicts, planted-defect catalogue (out-of-path, undeclared deviation, false completion, flaky).
- **Provider tier (every PR, no LLM).** ClaudeboxProvider against a fake `cb` (subprocess stub emitting canned `stream-json`): parse, usage extraction, error mapping, interrupt handling.
- **Live tier (M6/M9 + labeled runs, not per-PR).** Real boxes, real models, the M6/M9 DoD scripts. Costs money; runs on demand.
- **Fork-rebase corpus.** The Stub + provider tiers double as the P3 rebase gate: an upstream rebase is accepted only when both pass unchanged.

## 7. Owner prerequisites

1. **Lock P1's name**, then create the two public repos (`kentra-io/conductor` fork + the module repo); wire the module as a harness submodule.
2. **Extend the bot's write access + fine-grained PAT** to both repos (same recipe as spec-lifecycle plan §10; `Workflows: r/w` included).
3. No new secrets, tap entries, or registries — Python module, not a released binary, in v1. (Distribution/packaging decisions arrive with extraction, not before.)
4. A `needs-human-input` label in the target project repos (P7).

## 8. Risks

| Risk | Mitigation |
|---|---|
| **Fork drift** vs upstream Conductor | Pin + deliberate rebase (P3); minimal patch-set; Stub/provider corpus as the rebase gate (§6) |
| **Prompt-design quality** (M6 — Verifier rigor, Implementer adherence) | Live-tier planted-defect catalogue as the acceptance bar; judge hygiene from spec §5.4; calibration is a Stage-4 observability build |
| Durability semantics subtler than documented (resume re-runs, `$TMPDIR`, gate re-presentation) | M1/M5 DoD includes kill/resume tests; S2 spike; P4 config mandatory in every template |
| `for_each` ordering not guaranteed at `max_concurrent: 1` | Spike S1 before M5; fallback = route-chained sequential steps (uglier, fully ordered) |
| `stream-json`/`--agent` behavior drift across pinned CC versions | Pin the CC version in the box; provider tier fixtures per pinned version (shared caveat with agent-definition §11.2) |
| Two-language estate (Python module among Go siblings) | Engine-dictated (P2), scoped to this module; the deterministic harness stays small and script-step-shaped so a later Go port is possible if ever wanted |
| M3 ships late (cross-repo dependency) | `read_plan` develops against fixture JSON; only M8's archive-gate DoD hard-blocks on M3 |

## 9. Remaining spikes (scheduled, not blockers)

1. **S1 (M5): sequential `for_each`.** Confirm `max_concurrent: 1` preserves list order at the pinned SHA; else route-chained steps.
2. **S2 (M0/M5): checkpoint-dir relocation.** Env/config/patch mechanism to move checkpoints out of `$TMPDIR`; reboot-durability of a paused escalation.
3. **S3 (M7): long-pause lifecycle.** Process death while parked at `human_gate` → checkpoint + `resume` re-presents the gate — verify the re-presented gate + poll-seam interplay end-to-end.
4. **S4 (M6): `validator:` reuse inside L3.** Whether Conductor's native validator (with its auto-1-retry disabled/absorbed) can host the L3 judge call, or our own Verifier step stays cleaner. Default: own step.
5. **S5 (M3, in that change's design): the `apply` JSON surface.** New `lifecycle` verb vs extending `status --format json` — decided on the spec-lifecycle side.
6. **S6 (M1): `claude -p --agent` on the pinned in-box CC version** — the residual agent-definition §11.2 check, done once here for both modules.

## 10. Deferred (spec §13 + P-decisions)

Parallel milestones within a change · Stage-5 controller/evaluator/promotion · auto-trigger (poll/webhook) · aggregator daemon + fleet dashboard · `AgentDef.metadata` per-step correlation (P6) · thin operator CLI (P8) · module packaging/distribution (with extraction) · egress sandbox & git-history denial (rejected) · judge-calibration harness (Stage-4 observability; noted in `observability.md` §7).

## 11. Component → milestone map

| Spec component | Milestone |
|---|---|
| Fork + registration patch-set (§2) | M0 |
| ClaudeboxProvider (§8) + StubProvider | M1 |
| claudebox overlay + `:ro` mounts (§8) | **M2 [claudebox]** |
| spec-lifecycle §5.5 additions | **M3 [spec-lifecycle]** |
| Deterministic gates: L1/L2/diff-paths/deviation/flakiness (§5.1/§5.3) | M4 |
| Execution loop + 3-attempt ladder (§4) + durability (P4) | M5 |
| Cast contracts as personas + L3 judge (§5.2/§5.4/§6) | M6 |
| `Needs human input` + resume seam + Conductor-MCP (§7/§10) | M7 |
| Change-level finish + concurrency (§4.1/§9) | M8 |
| End-to-end acceptance + agentdef swap (§15) | M9 |

## 12. Kickoff — for the implementing session (fresh context)

Start here if you are the agent implementing this plan.

**Read first, in order:** (1) this plan; (2) [`orchestration.md`](./orchestration.md) — the spec, §0 terminology + §4/§5 are load-bearing; (3) [`agent-definition.md`](./agent-definition.md) §5 (materialization + overlay — M1/M2 context); (4) [`references/conductor-integration-notes.md`](./references/conductor-integration-notes.md) — the provider contract, event taxonomy, and YAML shapes (line numbers may drift from the pin; the contract was re-verified 2026-07-07); (5) [`tasks/orchestration-runtime-handoff.md`](./tasks/orchestration-runtime-handoff.md) for the wider Stage-3 state.

**Environment (a claudebox on the harness repo — verified 2026-07-07):**
- **`cb` is built from the submodule, not installed:** `cd claudebox && go build -buildvcs=false -o ~/.local/bin/cb .` — it works in-box against the host Docker daemon (sibling containers; host paths mirror 1:1, so bind mounts resolve). Rebuild after touching the claudebox fork (M2).
- **uv:** in the image after the next box rebuild (`.claudebox/Dockerfile`); until then `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **Token wrinkle:** a child `cb run` resolves `${KENTRA_BOT_GH_TOKEN}` from the shell env; inside the box it exists only as `GH_TOKEN` — `export KENTRA_BOT_GH_TOKEN="$GH_TOKEN"` first (verify at M1).
- **Worktrees for test changes:** always under a host-bind-mounted path — `cb worktree create` (→ `.worktrees/<branch>`) does this by construction. Never in container-local paths (`~`, `/tmp`): sibling boxes can't mount them.
- **PAT caveat:** pushing `.github/workflows/*` needs `Workflows: r/w` on the bot PAT — if the CI push is rejected, ask the owner to re-scope (§7).

**Rules (standing):** approve lifecycle gates only via `lifecycle approve` in a human-present session — never hand-edit `approval-state.json`, never teach a headless agent `--approve`. Cross-repo milestones (M2 claudebox, M3 spec-lifecycle) go through *those* repos' own lifecycle gates. Branch + PR per milestone; the bot identity is forced by the box env. Honor the harness constitution (ADR-0001..0003) and, from M0 on, the module repo's own seed ADRs.

**First moves (M0):** repos already exist and are writable (see M0 header). Cut `kentra-patches`, bootstrap the module repo per M0, wire the submodule, open the M0 PR, prove the DoD. Pause and surface to the user: any DoD you cannot demonstrate, any provisioning gap, and anything that contradicts this plan's pins — per-milestone review with the user is the default cadence.

## 13. Provenance

- Scope pins P1–P10 + errata E1–E9: this plan, 2026-07-07, **pending user lock** (P1's name explicitly so).
- Live-source verification (2026-07-07, background agent): `microsoft/conductor` `main` @ `7aaa58975601ecfaf42cdf6d048e0b4cd3e36028`, release v0.1.20; claims 1–8 verified file-level (`providers/base.py`, `providers/factory.py`, `providers/claude.py`, `providers/claude_agent_sdk.py`, `config/schema.py`, `engine/checkpoint.py`, `engine/context.py`, `gates/human.py`, `engine/router.py`, docs). Inspected copies parked at `/tmp/conductor-src/` (ephemeral).
- Build recon: [`references/conductor-integration-notes.md`](./references/conductor-integration-notes.md) (2026-06-16) — provider contract, event taxonomy, embedding pattern; its `--bare` sample corrected by erratum 9.
- Design decisions: [`orchestration.md`](./orchestration.md) §16 (the 2026-07-07 session: ladder, 3-layer verification, sandbox spike) + [`tasks/orchestration-runtime-handoff.md`](./tasks/orchestration-runtime-handoff.md) (Conductor/claudebox/consent, 2026-07-04/05). Fine-direct + plan-home: user decisions 2026-07-07.
- Sibling shape: [`spec-lifecycle/implementation-plan.md`](./spec-lifecycle/implementation-plan.md) (scope table · errata · pins · DoD milestones · tiered tests · owner prereqs), adapted for a module that extends a Python engine instead of owning a Go one.
