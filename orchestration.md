# `orchestration` — Design Specification

*Version: v1 draft. Generated: 2026-07-07. Status: **DESIGN — pending user review.***

*Provisional name — the neutral repo/CLI name is an open item (§14.1); this doc says "the orchestration module" throughout.*

*The **execution** leg of Stage 3: the business logic that drives an approved plan to merged code through a fleet of agents, with a deterministic verify-and-escalate loop and a human as the final tier. It **uses and extends Microsoft Conductor** as its durable workflow engine — but, unlike the sibling primitives, it **does not own that engine**: Conductor is a tool we consume and thinly patch, and the orchestration business logic (the loop, the cast, the verification harness, the escalation ladder) lives here. It **consumes** the three settled primitives — [`agent-definition`](./agent-definition.md) (the agents it runs), [`spec-lifecycle`](./spec-lifecycle/spec-lifecycle.md) (the plan it executes and the gates it honors), [`adr-sourced-constitution`](./adr-sourced-constitution/) (the governed HOW) — and the vendored [`claudebox`](./claudebox/) runtime (the sandbox each agent runs in). Files are the canonical interface; Conductor holds the durable run-state; a human is the terminal accept.*

*Decision provenance: the harness's [`tasks/orchestration-runtime-handoff.md`](./tasks/orchestration-runtime-handoff.md) (Stage-3 design + locked decisions, 2026-07-04/05), the two `deep-research` passes it cites, the [`agent-definition`](./agent-definition.md) spec (its sibling — the agent-abstraction leg), and a 2026-07-07 design session that settled the control-flow model (Conductor-as-spine, "Option B"), the escalation ladder, and the three-layer verification model against live primary sources (Factory.ai, SWE-bench/Aider/Amp/Devin/Sculptor/Kiro/Tessl/Spec-Kit, plus a claudebox sandbox-cost spike). See §16.*

---

## 0. Terminology (locked)

- **Conductor** — [Microsoft Conductor](https://github.com/microsoft/conductor) (MIT), the external, deterministic YAML `route`/`when`/`for_each` workflow engine with durable run-state. **We use and extend it; we do not reimplement it.** (Not Conductor.build, the Melty macOS app.)
- **The orchestration module (THIS)** — the business logic layered over Conductor: workflow templates, the ClaudeboxProvider, the verification harness, the escalation state machine, and the operator MCP. The thing this spec defines.
- **Mode A (interactive planning)** — a human collaborates with a Mode-A agent; the human approves conversationally; Conductor is passive. Produces/edits the plan, constitution, or spec.
- **Mode B (headless execution)** — Conductor drives; agents run headless in claudebox; no human in the inner loop. The steady state.
- **Change** — one `spec-lifecycle` change folder (`openspec/changes/<id>/`) with an approved plan (`tasks.md`). The unit of execution and the unit of concurrency (§9).
- **Milestone / task** — an ordered item in the change's `tasks.md`. Milestones run sequentially within a change (§9); each carries a **validation contract** (§5).
- **Validation contract** — the per-milestone acceptance definition authored at plan time: an optional **acceptance check** (a command whose exit code gates), the **generic healthcheck** requirement, **plain-language acceptance criteria**, and the **allowed path-set** the milestone may touch.
- **Implementer / Verifier / Orchestrator** — the Mode-B cast (agent-definition defs, §6). The Implementer does the work; the Verifier judges it; the Orchestrator resolves failures between the Implementer and the human.
- **Resolution attempt** — one pass of "Implementer produces work → Verifier judges it." Conductor **naively counts** these per milestone (§4).
- **Escalation ladder** — attempt 1 solo, attempts 2–3 Orchestrator-guided, then human. Three attempts total; the human is the third tier (§4).
- **`Needs human input`** — the durable status a change enters when the ladder is exhausted; it pauses the workflow and pulls a human into a Mode-A session (§7).
- **The three-layer verification** — L1 executable acceptance check, L2 generic healthcheck, L3 judging agent over plain-language criteria (§5).
- **Author ≠ verifier** — the invariant that the agent judging the work is a *fresh* agent that never saw the Implementer's reasoning, re-deriving coverage from the spec (§5). The trust spine.
- **Deviation** — any departure from the plan/spec. Declared deviations are logged (`deviation.json`); undeclared deviations are what the Verifier structurally catches (§5).
- **ClaudeboxProvider** — the custom Conductor provider (~250–400 LOC Python, fork-carried — verified 2026-07-07: Conductor has **no provider plugin API**, so the provider + ~15 lines of registration edits live on our fork's patch branch) that runs a compiled agent-def via `claude -p --agent` inside claudebox — **never** Conductor's default `claude` provider (§8).
- **Conductor-MCP** — the MCP surface for interim human-driven operation: the six `spec-lifecycle` verbs + a small Conductor workflow-control set (§10).

## 1. Purpose & scope

**What it is.** The composition layer that turns *"an approved plan exists"* into *"reviewed, merged code — or a change parked for a human."* It is the Stage-3 execution engine: it reads a `spec-lifecycle` plan, runs the cast against it in isolated worktrees, gates every milestone through the three-layer verification, counts failures, escalates on a fixed ladder, and hands the durable run-state to Conductor.

**What it is NOT (owned elsewhere):**
- **Not the workflow engine.** Durable run-state, `route`/`when`/`for_each`, retry primitives, and the human-gate mechanism are **Conductor's** (external). We author workflow *templates* and one thin fork patch (§8); we do not build a scheduler.
- **Not the agents.** The Implementer / Verifier / Orchestrator personas are [`agent-definition`](./agent-definition.md) defs — this module *runs* them, it does not define the schema. The concrete kentra cast is consuming-project data (§6, and agent-definition §12).
- **Not the plan or the gates.** Stage definitions, approval records, milestone validation contracts, the `deviation.json` record, and the living-spec fold are [`spec-lifecycle`](./spec-lifecycle/spec-lifecycle.md)'s. This module *consumes* the plan and *honors* the gates; it requires a small set of spec-lifecycle additions (§5.5) but owns none of them.
- **Not governance.** Authority (who may `approve`) rides the launch context, not any agent or workflow (§7). Governance content lives in the constitution.
- **Not a multi-repo driver.** **One run drives exactly one git repository.** The run's worktree is created with `git worktree add` from the repo that holds the plan, and that single root serves as plan-root, code-root, and commit-root simultaneously: the plan is read from it (`lifecycle apply` runs with that cwd), the box is mounted at it (the agents can neither see nor write outside it), and `milestone_commit` commits with `git -C <worktree>`. A change whose deliverable is a **new standalone repo**, or that **spans two repos / multiple modules with separate git roots**, therefore cannot be driven end-to-end — the plan and the code it produces must live in the same repository. Splitting the roots is deferred (§13, [#24](https://github.com/kentra-io/agent-orchestration/issues/24)); such changes must be split by hand today.

**The design spine (mirrors the siblings, with one deliberate difference):** *consume neutral primitives · own the business logic, not the engine · files are the canonical interface · determinism where it counts (the counter, the escalation), judgment where it must (the verifier), human at the boundary.* The difference from `spec-lifecycle`/`agent-definition`: those **own their engine** (a Go fold/compile engine over a neutral format); this one **extends a third-party engine** (Conductor) it deliberately does not own — so its "engine" is workflow templates + a thin provider + one fork patch, and its substance is the loop and the verification harness.

## 2. Position in the stack

```
  Human (Mode A)                                   ← approves conversationally; resolves
      interactive session, launch-context authority    `Needs human input`; edits plan/spec/constitution
  ─────────────────────────────────────────────
  Conductor (external, EXTENDED not owned)         ← durable run-state; route/when/for_each;
      workflow templates + one fork patch (ours)       retry primitive; human_gate; the deterministic
                                                        attempt counter + escalation state machine
  ─────────────────────────────────────────────
  THE ORCHESTRATION MODULE (THIS)                  ← the execution loop; the 3-layer verification
      workflow templates · ClaudeboxProvider ·         harness; the escalation ladder; the cast contract;
      verification harness · Conductor-MCP             the operator MCP
  ─────────────────────────────────────────────
  agent-definition   →  compiled personas         ← Implementer / Verifier / Orchestrator run via
  claudebox          →  per-agent sandbox+`.claude`  ClaudeboxProvider (`claude -p --agent`), one
  spec-lifecycle     →  the plan + gates + records   worktree per change, materialized `.claude` per agent
  adr-sourced-const. →  the governed HOW
```

**What "extends Conductor" means and costs** *(re-verified against live source 2026-07-07 — pinned `main` @ `7aaa589`, release v0.1.20; see the implementation plan §1 errata)*. We take Conductor as-is and add:
1. **Workflow templates** — YAML for the `execute-change` flow (and the Mode-A escalation-resume flow). Deterministic `routes`/`when`/`for_each` over a change's milestones, invoking the ClaudeboxProvider per step.
2. **The ClaudeboxProvider, fork-carried** — the custom step-executor that runs a compiled agent-def in claudebox (§8). This is where "a Conductor step" becomes "a real Claude Code agent in a sandbox." Conductor has **no provider plugin API** (providers are hard-coded in `Literal`/`match` sites), so the provider file plus ~15 lines of registration edits across ~5 sites live on a **fork patch branch** — a small, maintained patch-set, not a drop-in extension.
3. **Optional: the `AgentDef.metadata` add** — `AgentDef` is `extra=forbid` with no metadata field; a 1–2-line patch adds one for *per-step* correlation keys. **Run-level** correlation (`issue_id`) needs no patch at all: `WorkflowDef.metadata` is native (`conductor run -m key=value`), and the provider can stamp `experiment_id`/`variant` per invocation. Deferred until Stage 5 proves the per-step need.

Everything else — the counter, the human-gate, resumability, crash-safety — is Conductor's native machinery, which is *why* Conductor is the spine (§3). **One durability caveat (verified):** Conductor checkpoints **on failure only** by default, into `$TMPDIR` — crash-safety requires opting into per-step checkpoints (`runtime.checkpoint.every_agent: true`) and a persistent checkpoint dir, and the 3-attempt loop needs `limits.max_iterations` raised (default 10). All pinned in §12 / the plan.

## 3. Control-flow model — Conductor as the durable spine

One workflow, two modes. **Mode B (headless execution)** is the steady state; **Mode A (interactive planning)** is the entry and the exception-handler. The human is pulled in at exactly two bounded places: to produce the initial approved plan (Mode A, before execution) and to resolve a `Needs human input` escalation (Mode A, on demand). Between those, Conductor drives.

**Why Conductor is on top (not the orchestrator agent).** An LLM agent session is **not durable or crash-safe** — if it dies mid-run, any state it held (including an attempt counter) is lost. Therefore the durable, deterministic components — the **attempt counter** and the **escalation state machine** — cannot live inside an agent. They live in Conductor. This inverts the naive picture: the Orchestrator does **not** submit work up to Conductor and await escalations; **Conductor is on top and *calls* the Orchestrator** as a stateless resolution step. Same information flow (context down, escalation up), durable arrangement.

**Fine-grained (this spec), not coarse.** Two granularities were considered:
- **Coarse (rejected as the v1 target, kept as a mental stepping-stone):** Conductor wraps one smart Orchestrator agent as an opaque step; the Orchestrator spawns its own sub-agents via native Claude Code nesting; Conductor counts whole-run failures. Simplest, but the inner loop is opaque and non-durable, and it gives Stage-5 A/B nothing to score.
- **Fine-grained (THIS):** Conductor is the spine; each milestone's Implementer, Verifier, and (on failure) Orchestrator are **discrete Conductor-invoked steps**; the counter is per-milestone; every attempt is a durable, observable Conductor event. More plumbing, but it is the only arrangement that gives per-attempt observability and makes each agent an **independently swappable unit** — which the Stage-5 online A/B requires (swap one agent's config, score it on the same milestone). The coarse→fine difference is not cosmetic: fine-grained is a prerequisite for auto-improvement.

In the fine-grained model the Orchestrator does not spawn sub-agents; **Conductor spawns the agents**, and the Orchestrator's "decompose/re-plan" instinct becomes "emit resolution guidance Conductor then applies." Shared context is not ad-hoc message-passing — it is the governed worktree + the `spec-lifecycle` artifacts + the constitution (the file-canonical substrate every agent reads).

## 4. The execution loop and the escalation ladder

### 4.1 Trigger and outer shape

Execution begins when a change reaches **plan-approved** in `spec-lifecycle` (its `tasks.md` exists and its plan gate is approved). Conductor starts the `execute-change(id)` workflow:

1. Create/attach a **dedicated worktree** for the change (§9).
2. Read the plan → the ordered milestone list, each with its validation contract (§5).
3. `for_each` milestone, **in order** (sequential within a change): run the milestone loop (§4.2).
4. When all milestones pass: run the change-level finish (full healthcheck green, then hand off to `spec-lifecycle archive` — gated on tasks-complete, §5.5).

### 4.2 The milestone loop — the 3-attempt ladder

```
Attempt 1   Implementer works the milestone solo (in the worktree)
            → Verifier runs the 3-layer verification (§5)
            → PASS ─────────────────────────────────────────► next milestone
            → FAIL: Conductor increments the counter (durable)
   ↓ escalation #1  →  Orchestrator (resolution step)
Attempt 2   Orchestrator emits guidance → Implementer retries
            → Verifier → PASS ─────────────────────────────► next milestone
            → FAIL: Conductor increments
   ↓ escalation #2  →  Orchestrator (resolution step)
Attempt 3   Orchestrator emits guidance → Implementer retries
            → Verifier → PASS ─────────────────────────────► next milestone
            → FAIL
   ↓
   Conductor sets the change to  `Needs human input`  and PAUSES (human_gate)
            → human resolves in a Mode-A session (§7) → Conductor resumes (§7.2)
```

**Exactly three resolution attempts** — one solo, two Orchestrator-guided (the two escalations) — and the **human is the third tier**, reached only after attempt 3 fails. (Locked 2026-07-07.)

**Who owns what (the determinism split):**
- **Conductor** owns the **counter** and the **threshold flip** to `Needs human input`. The Orchestrator never decides *when* to escalate to the human — Conductor does, deterministically, after attempt 3. Because the counter lives in Conductor's durable run-state, a crashed agent mid-attempt does not lose the count.
- **The Orchestrator** is a **stateless, Conductor-invoked resolver**: given the failed verification + the Verifier's report + the Implementer's raised issue + the worktree/plan context, it emits next-attempt guidance (re-scope the milestone, supply missing context, tighten instructions, re-order). It is the middle resolution tier — not a persistent brain, and not the escalation decider.
- **The Implementer** does the work under strict plan-adherence rules (§6.2). It **naively fails or passes**; it does not self-escalate.
- **The Verifier** renders the pass/fail via the three-layer model (§5), as a *fresh* agent (author ≠ verifier).

The threshold (3) is a config constant (§12), tunable per deployment; 3 is the locked default.

## 5. The verification model — how a milestone passes

Corroborated by the field scan (§16): **no serious system lets an LLM's judgment be the terminal gate.** The gate is deterministic tests, a human, or a judge explicitly backed by both. So a milestone passes through three layers plus a structural adherence check, and the *trust* comes not from exhaustive test-authoring but from **author ≠ verifier**.

### 5.1 The three layers (the routing rule)

Author every criterion you cheaply can into L1; let L2 guard regressions; send only the genuine non-executable remainder to L3.

| Layer | What it checks | Nature | Authored where |
|---|---|---|---|
| **L1 — executable acceptance check** | a milestone-specific command whose **exit code** is pass/fail (a new test, a repro test flipping red→green, a build target) | deterministic, objective | plan artifact, per milestone — *where feasible* (optional) |
| **L2 — generic healthcheck** | the whole repo's **full test suite + build + lint** must be green | deterministic, objective | project-level, free per milestone (the regression guard) |
| **L3 — judging agent** | the **non-executable remainder** — did it satisfy intent, is it idiomatic, is the error-handling sound — graded against **plain-language acceptance criteria** | judged, **advisory + human-cleared** | plain-language criteria in the plan artifact |

**This resolves the "specs get too complicated" worry (2026-07-07).** You do *not* need a bespoke oracle per milestone: L2 is free, L3 grades cheap plain-language criteria, and L1 is used only where a check is natural (notably the bug repro-first flow, which is fully objective). Authoring burden stays low; trust comes from §5.2.

### 5.2 Author ≠ verifier — the trust spine

Every tool that relies on the *implementing* agent to respect a checklist hits a measured **~78% adherence ceiling** — self-reported compliance is unreliable by nature. The only two mechanisms that *reliably* catch deviation are executable tests (L1/L2) and a **fresh verifier that re-derives coverage from the spec, evidence-or-zero, and diffs intent-vs-actual** (the Tessl pattern). So the Verifier:
- is a **fresh agent** that never saw the Implementer's reasoning — it gets only `spec.md`, `tasks.md`, the git diff, the `deviation.json` log, and the test suite;
- builds a **coverage matrix** (every requirement/scenario → concrete evidence in the diff/tests; no evidence = UNMET);
- runs L1 + L2 (objective), then grades the L3 remainder;
- **diffs intent-vs-actual**: any file/behavior in the diff that maps to *no* task+requirement is an **undeclared deviation** → FAIL; any task ticked `[x]` with no corresponding change is a **false completion** → FAIL; any real deviation *not* in `deviation.json` means the Implementer hid it → FAIL.

This is what makes the Implementer's "follow the plan to the letter and flag deviations" honest: the Implementer self-declares (§6.2), but the independent Verifier is the enforcement.

### 5.3 The cheap structural gates (no LLM)

- **Diff-confined-to-declared-paths.** The milestone's validation contract declares an **allowed path-set**; Conductor checks the diff's file list against it. Any out-of-path file is a **mechanical** deviation flag (no LLM) — the cheapest, highest-signal adherence gate we have (from Factory's "diff confined to agreed paths").
- **Deviation log.** Declared deviations append to `spec-lifecycle`'s `deviation.json`; the Verifier cross-checks that every real deviation it finds was declared (§5.2).
- **Tamper-proofing (from the sandbox spike, §16; tool posture revised 2026-07-09, user-locked).** **Every cast agent runs the default Claude Code toolset — no per-role `tools:`/`disallowedTools` surgery** (judged unnecessary complexity). Discipline is *behavioral* (the persona contracts: web is never a source of requirements for the Implementer; the Verifier reports-does-not-fix; the Orchestrator is guidance-only) and *structural* (author ≠ verifier, deterministic gates, counter-owned escalation, human-cleared L3). Field research (2026-07-09) backs default-allow for the doing agent: web denial has documented capability cost (Codex re-shipped internet 3 weeks after a no-internet launch; OpenHands-Versa +9.1pp from browsing; Factory telemetry = web use dominated by docs/API refs). **Eval/benchmark runs MAY restrict the Implementer at materialization** (the Factory pattern: web in production, revoked for its own SWE-bench run) — a one-line persona-frontmatter edit, never a box-wide `settings.json` deny (settings-deny survives `bypassPermissions` and the roles share the box). Acceptance tests/spec/tasks are **read-only to the Implementer** via a `:ro` mount ridden on the claudebox `claude_dir_source` provisioning seam (§8). Network-egress control and git-history denial were **spiked and rejected** as not-cheap / not-relevant (§8, §16).

### 5.4 Judge hygiene (the L3 agent) and flakiness

- **L3 verdict format:** one call → a `0.0–1.0` score **plus** a hard pass/fail, against an **anchored rubric** tied to the plan's acceptance criteria (Anthropic's most-consistent format).
- **Read-only + grounded:** the judge *may run the tests* but changes nothing it grades — a behavioral contract rule (default toolset, per the 2026-07-09 tool posture — §5.3), backed structurally: the deterministic gates re-run independently and a human clears anything L3 touches. Grounding in real test output resists verbosity gaming.
- **Advisory, never terminal:** its verdict never auto-merges; a human clears anything L3 touches. Prefer one anchored judge over multi-agent debate (debate amplifies bias). Calibrate against human spot-checks; recalibrate the rubric past ~20–25% divergence.
- **L2 flakiness:** treat as first-class — a test under ~95% pass-rate over N reruns is **quarantined**, not silently retried; retry specific operations, not whole tests. "Green after retry" must not mask a deterministic failure.

### 5.5 Required `spec-lifecycle` additions (the dependency)

The verification model reuses structures `spec-lifecycle` already reserved — **milestone validation contracts**, the **`deviation.json`** record, and the **Given/When/Then** requirement grammar — but needs a small, separately-specced change set on the planning side:
1. **Plan template carries the validation contract per milestone:** optional acceptance-check command, plain-language acceptance criteria, and the allowed path-set.
2. **`archive` gains a tasks-completion gate** (no Go code parses checkboxes today; upstream OpenSpec had this gate) — a change cannot fold into the living spec with open tasks.
3. **The `apply` block** (the plan→execute handoff the schema already anticipates) becomes real: it is what this module reads to drive execution.

These are referenced-not-inlined here — they get their own `spec-lifecycle` change (§14.4).

## 6. The agent cast and their contracts

The cast are [`agent-definition`](./agent-definition.md) defs (consuming-project data). This module owns their **behavioral contracts** — what each must do to fit the loop — not their schema. Model/effort assignment follows Factory's shipped default and our own preference: **the Verifier gets more compute than the Implementer** (spend more checking than doing).

| Role | Mode | Model/effort (default) | Contract |
|---|---|---|---|
| **Business Analyst** | A | interactive | refine/requirements; may hold `lifecycle-approve` by launch context (§7) |
| **Tech Lead** | A | interactive | design / ADR proposals; Mode-A approval by launch context |
| **Implementer** | B | Opus, medium | §6.2 |
| **Verifier** | B | Opus, **high** | §6.3 — the fresh, author≠verifier grader |
| **Orchestrator** | B | Opus, high | §6.1 — the stateless resolution router |

### 6.1 Orchestrator (resolver)
Invoked by Conductor only on a failed attempt. Input: the Verifier's report, the Implementer's raised issue, the diff, the plan/worktree. Output: **next-attempt guidance** (re-scope, supply context, tighten, re-order) — or a signal that the milestone is infeasible as written. It **does not** decide human-escalation (Conductor's counter does), does not spawn agents, and holds no durable state.

### 6.2 Implementer (follow-to-letter, stop-and-ask, log deviations)
Behavioral rules, baked into its agent-def `system_prompt` (sourced from the plan-adherence research, §16). In imperative MUST/HALT form:
- Work `tasks.md` top-to-bottom, **one milestone at a time**; after each, tick `[x]` and write a one-line **evidence note** (which requirement it satisfies, how verified).
- **Every change MUST trace to a task and a spec requirement.** If it traces to neither — STOP, do not write it.
- **Ambiguity is a halt, not a guess.** If a task is under-specified or the spec is silent — STOP, emit a QUESTION, do not improvise.
- **Deviation is a logged halt.** For any departure from spec/plan, append to `deviation.json` *before* proceeding (task id, spec §, what the plan said, what you did and why, blast radius, status `BLOCKED-AWAITING-APPROVAL`). An undeclared deviation is a defect.
- **MUST NOT** edit `spec.md`/`tasks.md` content (only tick boxes), mark a task done without recorded evidence, expand scope beyond listed tasks, or touch files outside the declared path-set (§5.3).
- **Default toolset; web constrained in use, not in surface** (revised 2026-07-09 — §5.3): MAY consult the web for docs/API refs/dependency issues; the web is never a source of requirements (spec wins; a conflict is a QUESTION), and any found solution still traces line-for-line to tasks. Eval runs may restrict at materialization (§5.3/§8).

### 6.3 Verifier (fresh, evidence-or-zero)
The §5.2 procedure, as an agent-def: never saw the Implementer's reasoning; builds the coverage matrix; runs L1+L2; grades L3 against the anchored rubric; performs the intent-vs-actual diff and the deviation cross-check; outputs PASS only if coverage is fully MET, objective gates green, diff fully maps to plan+spec, and every real deviation was declared. **Reports, does not fix** — a behavioral rule (default toolset, §5.3): it changes nothing it grades.

## 7. Escalation, human-in-the-loop, and consent

### 7.1 `Needs human input`
On attempt-3 failure Conductor sets the change's durable status to `Needs human input`, records the accumulated failure context (the three Verifier reports + the Orchestrator guidance), pauses the workflow at a `human_gate`, and emits a notification (fleet dashboard / GitHub Projects). The **canonical home** of this status is an open item (§14.3) — Conductor run-state is the source of truth for the *run*; the *change-level* status should also be reflected where a human inspects changes.

### 7.2 Resume semantics
A human runs a **Mode-A session** (interactive) that edits the spec, constitution, or plan to unblock the change, then approves. Conductor detects the resolved state by polling `lifecycle status --format json` (the poll-seam from the handoff — this is the *one* bounded place a workflow spans the interactive-approval boundary) and **resumes from the failed milestone**. If the human's edit **materially changed the plan artifact**, Conductor **re-derives the remaining milestones** from the new `tasks.md` rather than blindly continuing.

### 7.3 Consent (inherited, launch-context-bound)
Authority rides the **launch context, not the agent** (agent-definition §7, locked). `lifecycle approve`/`archive` are **never** in a Conductor-spawned agent's tool surface — self-approval is structurally impossible. Approval happens only via a human-launched Mode-A session (carries the `lifecycle-approve` skill) or a Conductor `human_gate` → step run with `--approve` after a human releases it. The shipped `spec-lifecycle` `ConsentGate` **fails closed on non-TTY**, so a headless `claude -p` agent cannot self-approve even if a skill named the verb — belt to this suspenders.

## 8. Runtime and isolation

- **ClaudeboxProvider (the seam).** Every Mode-B step runs through the custom provider (~250–400 LOC Python, fork-carried — §2): compile the agent-def (agent-definition §5) → `claude -p --agent <role>` inside the change's claudebox → capture the structured result. **Never** Conductor's default `claude` provider (raw API — would never see the materialized agent, its skills, or the sandbox). Shared with agent-definition (§14.6).
- **Per-agent materialized `.claude`.** Each agent's sandbox gets a caller-materialized artificial `~/.claude` (skills/ + plugins/ + settings.json + role CLAUDE.md) copied in via claudebox's `provisioning.claude_dir_source` (agent-definition §5.1) — **no host `~/.claude` bind**, only `.credentials.json` injected — so the host operator's scope is physically absent, not just shadowed. The primitives' *ongoing* skills ride the worktree channel for free.
- **Sandbox constraints (from the 2026-07-07 spike, §16).** The spike costed the anti-reward-hacking constraints against the claudebox fork's actual source and concluded:
  - **Tool policy — REVISED 2026-07-09 (user-locked; supersedes the spike's "no web tools for the Implementer").** Every cast agent ships the **default Claude Code toolset** — per-role tool surgery judged unnecessary complexity. Discipline is behavioral (persona contracts) + structural (author≠verifier, deterministic gates, counter-owned escalation, human-cleared L3). Field research backs default-allow: web denial has documented capability cost (Codex re-shipped internet in 3 weeks; OpenHands-Versa +9.1pp from browsing; Factory telemetry = web use dominated by docs/API refs). Eval/benchmark runs MAY restrict the Implementer at materialization — the Factory pattern (web in production, revoked for its own SWE-bench run), a one-line persona-frontmatter edit; never a box-wide `settings.json` deny (survives `bypassPermissions`, and the roles share the box). (Residual, unchanged: the image ships `curl`/`bash` and egress is open — tool policy was never an egress boundary; fetched web content is injection surface in a box holding OAuth creds — accepted.)
  - **Read-only test/spec mounts** — a nested `:ro` mount over the RW worktree (~15–30 LOC). **Not** a standalone task: it rides the claudebox `claude_dir_source` provisioning seam we are already building (near-zero marginal cost there).
  - **Network-egress control — rejected.** `--network none` is a non-starter (the in-container `claude` needs `api.anthropic.com`); selective egress needs a fragile proxy+firewall that is effectively defeated on Docker Desktop/macOS — the fork's own `todo/host-isolation.md` already reached this conclusion and accepted the limitation.
  - **Git-history denial — rejected.** Meaningless for net-new feature work (no "fix commit" to find) and invasive to implement.

## 9. Concurrency model

- **Unit of concurrency = the change.** The fleet runs **many changes concurrently**, each in **its own git worktree** — git is the ACID substrate that keeps them isolated.
- **Sequential within a change.** Milestones run **in order** (they usually carry dependencies). Parallel-within-a-change is deferred.
- **One repo per run.** A change's worktree is a worktree *of the repo that holds its plan*, and is the only git root the run ever touches — plan-, code-, and commit-root at once. Multi-repo / multi-module (separate git roots) changes are out of scope (§1, §13).
- **Maps to Stage 5.** The per-change-worktree model is the same isolation the Stage-5 champion–challenger A/B uses per variant (isolated worktrees, paired scoring on the same milestone) — so the concurrency substrate built here is what Stage 5 reuses.

## 10. Operator surface — the Conductor-MCP and interim operation

Until the autonomous loop earns trust, a human drives and inspects the pipeline through an **interactive claude session equipped with the Conductor-MCP**. Its surface is wider than the six `spec-lifecycle` verbs:
- **`spec-lifecycle` verbs (1:1 wrapper):** `get_state` / `validate_stage` / `record_approval` / `archive_change` / `run_guard` (+ status). (Dropped invented verbs `submit_artifact`/`request_transition` per the 2026-07-05 reconciliation.)
- **Conductor workflow-control verbs (small, new):** list running workflows, inspect the `Needs human input` queue, resolve/resume a paused change. These let the interim session "understand all that" (2026-07-07).

This is the interim; the autonomous loop (§4) is the target. Both read the same file-canonical state.

## 11. The three layers — what the module ships

Mirrors the siblings' shape, adapted to a module that *extends* an engine rather than owning one.

### 11.1 Layer 1 — CORE: workflow templates + provider + verification harness + fork patch
- **Workflow templates** (Conductor YAML): `execute-change`, the escalation-resume flow.
- **ClaudeboxProvider** (~250–400 LOC **Python**, fork-carried — Conductor's provider seam is a Python ABC, `providers/base.py`; a provider implements async `execute()`, and with no plugin API its registration edits live on the fork patch branch): the step executor (§8).
- **Verification harness:** the L1/L2 runners, the diff-path checker, the deviation cross-check driver, the L3 judge invocation + rubric plumbing (§5). Deterministic where it can be; the L3 judge and the Verifier are agent-defs it invokes.
- **The one Conductor fork patch** (correlation metadata, §2).
- **Operator surface:** the Conductor-MCP (§10), and possibly a thin CLI (start/list/resolve) — CLI-vs-MCP-only is an open item (§14.7).

### 11.2 Layer 2 — AGENT SURFACE: skills + the cast contracts
The behavioral contracts (§6) shipped as the Implementer/Verifier/Orchestrator agent-defs (kentra's branded cast) + any operator skill (e.g. a `resolve-escalation` skill for the Mode-A human). Thin — this module is driven by Conductor, not conversationally.

### 11.3 Layer 3 — INTEGRATIONS
Conductor (extended), claudebox (runtime + `claude_dir_source` provisioning seam), agent-definition (the personas), spec-lifecycle (plan + gates + the §5.5 additions), adr-sourced-constitution (governed HOW), Stage 4 (LiteLLM+Langfuse — reads the correlation metadata), Stage 5 (the controller — reads the per-variant seam).

### 11.4 Neutral mechanism vs. branded methodology
Per ADR-0002. The **mechanism** — the Conductor-driving execution loop, the ClaudeboxProvider, the verification harness, the escalation state machine, the MCP — is framework-neutral and reusable → candidate **neutral primitive** (own repo + submodule). The **branded composition** — the specific kentra cast, the concrete workflow wiring, the methodology — is `kentra-`-layer content (harness / `kentra-sdlc`). The exact neutral/branded cut + the repo name are open (§14.1).

## 12. Configuration

```yaml
schemaVersion: 1
engine: { name: conductor, mode: extended }   # we use+extend Conductor; we do not own it
attemptThreshold: 3                            # locked default: 1 solo + 2 orchestrator, human on 3rd
verification:
  healthcheck: required                        # L2 always on
  judge: { mode: advisory, humanClears: true } # L3 never terminal
  flakinessQuarantineBelow: 0.95
concurrency: { unit: change, withinChange: sequential }
durability:                                    # verified 2026-07-07: Conductor defaults are insufficient
  checkpoint: every-agent                      # default = failure-only → opt into per-step checkpoints
  checkpointDir: persistent                    # default = $TMPDIR → relocate (mechanism = plan spike)
  maxIterations: computed                      # default 10 too low for the 3-attempt loop; sized per run
consentBoundary: launch-context                # §7 (defs/workflows carry no authority)
```

Versioned like the siblings (unknown `schemaVersion` ⇒ refuse; no migration machinery).

## 13. Deferred — explicitly not in v1

| Item | Why |
|---|---|
| Parallel milestones **within** a change | Sequential first; the worktree substrate supports it later |
| Multi-repo changes — a distinct **code-root/commit-root** from the plan-root, or a change whose deliverable is a **new standalone repo** | One run = one git repo is structural in v1 (§1, §9): the run worktree is a worktree *of the plan's repo* and is simultaneously the plan-, code-, and commit-root. Documented as a limitation; the fix is a real change to the worktree/mount model. [#24](https://github.com/kentra-io/agent-orchestration/issues/24) |
| The Stage-5 controller / evaluator / champion-promotion | This module ships the per-variant *seam* (§9 worktrees + correlation metadata); the loop is Stage 5 |
| A sophisticated Orchestrator (classify/route beyond guidance) | v1 Orchestrator emits guidance only; "make it more sophisticated later if we need to" (2026-07-07) |
| Auto-trigger on plan-approved | v1 may start `execute-change` by explicit invocation; auto-trigger (poll/webhook) is an ergonomics layer (§14.5) |
| A network-egress sandbox / git-history denial | Spiked and rejected (§8) |
| A persona-management / fleet GUI | Off-grain; operator surface stays MCP + optional CLI (agent-definition §5.2 rationale) |
| Fleet dashboard beyond thin custom | Claude-fleet-UI category churns; mine, don't depend (handoff) |

## 14. Open items — build-time spikes (not blockers)

1. **Neutral/branded cut + repo name (§11.4).** ✅ **RESOLVED 2026-07-07: `agent-orchestration`** (user lock); the cut is the plan's P1 — neutral repo = templates + harness + resume seam + MCP (+ the `kentra-io/conductor` fork carrying the provider); branded layer (kentra cast, hand-materialized personas, concrete wiring) stays in the harness.
2. **Coarse-then-fine or fine-direct.** ✅ **RESOLVED 2026-07-07: fine-direct.** The coarse wrap's inner loop (opaque native nesting) is throwaway; the plan sequences the shared plumbing first (`claude_dir_source` provisioning seam → ClaudeboxProvider → single-milestone loop → ladder → verification layers) so an early end-to-end demo still exists without building a disposable mode. See [`orchestration-implementation-plan.md`](./orchestration-implementation-plan.md).
3. **Canonical home of `Needs human input` (§7.1).** Conductor run-state vs. a `spec-lifecycle` change status vs. a GitHub Projects field — where the change-level status is authoritative and how the three views stay consistent.
4. **The `spec-lifecycle` §5.5 change** (plan-template validation contract + archive tasks-gate + real `apply` block). Its own change folder; this module depends on it. (The deferred "execution seam" from the handoff — the orchestrator now exists, so it's unblocked.)
5. **Trigger mechanism (§13).** Poll `lifecycle status` for plan-approved changes vs. explicit `execute-change` invocation for v1.
6. **ClaudeboxProvider** — build (~150 LOC), shared with agent-definition (§8); confirm the one-line Conductor fork patch (correlation metadata; `AgentDef.metadata` is `extra=forbid`).
7. **Operator surface shape (§11.1).** Conductor-MCP only, or MCP + a thin CLI for start/list/resolve.
8. **Judge calibration harness (§5.4).** How L3 verdicts are spot-checked against human judgment and the rubric recalibrated — a small tooling need before trusting the loop unattended.

## 15. Appendix — one change, end to end

```
plan-approved change #42 (tasks.md: M1, M2, M3; each with a validation contract)
  │
  ▼ Conductor: execute-change(42) → worktree wt-42
  M1 ─ Implementer(solo) → Verifier: L1 ✓ L2 ✓ L3 0.9/pass, diff in-paths, no undeclared dev → PASS
  M2 ─ Implementer(solo) → Verifier: L2 fails (regression) → FAIL (count=1)
        ↳ Orchestrator: "M2 broke the auth test; scope the fix to authz, re-run"
        Implementer(guided) → Verifier: L1 ✓ L2 ✓ L3 pass → PASS
  M3 ─ Implementer(solo) → Verifier: undeclared deviation (touched billing/, not in path-set) → FAIL (count=1)
        ↳ Orchestrator guidance → Implementer(guided) → Verifier: L3 0.4/fail (criteria unmet) → FAIL (count=2)
        ↳ Orchestrator guidance → Implementer(guided) → Verifier: L1 fails → FAIL (count=3)
        ↳ Conductor: change #42 → `Needs human input`, pause, notify
  ── human Mode-A session: M3's acceptance criterion was ambiguous; edits tasks.md, approves ──
  ▼ Conductor polls lifecycle status → resolved → resume from M3 (re-derive: M3 unchanged) → … → all pass
  ▼ full healthcheck green → spec-lifecycle archive (tasks-complete gate) → living-spec fold
```

## 16. Research provenance

- Harness [`tasks/orchestration-runtime-handoff.md`](./tasks/orchestration-runtime-handoff.md) (2026-07-04/05) and the sibling [`agent-definition.md`](./agent-definition.md): the Stage-3 locked decisions this spec composes — Conductor engine, claudebox runtime, launch-context consent, the ClaudeboxProvider, the Stage-5 A/B seam, the poll transition-seam.
- **2026-07-07 design session (this spec's core):** the control-flow model (Conductor-as-durable-spine; fine-grained "Option B" over the coarse wrap), the **3-attempt escalation ladder** (1 solo + 2 Orchestrator + human), and the **three-layer verification model**, settled against three live-source research passes:
  - **Factory.ai** (the implementer/verifier source): Mission Mode = orchestrator/worker/**validator** with the validator on *higher* effort; "done" = deterministic gates (tests+lint+typecheck+**diff-confined-to-paths**) with the LLM validator on top; drift red-flags; **no** built-in attempt-counter (our hard ladder is a deliberate addition). [docs.factory.ai, factory.ai/news]
  - **Verification field scan** (SWE-bench/Aider/Amp/Devin/Sculptor/Kiro/Tessl/Spec-Kit + the Verification-Horizon / reward-hacking / LLM-judge-bias literature): no system makes the LLM the terminal gate; the three-layer hybrid + judge-hygiene + flakiness-quarantine recommendations. [see the session's cited URLs]
  - **Plan-adherence / deviation research** (Spec-Kit `/analyze`, OpenSpec apply-gate, **Tessl author≠verifier evidence-or-zero**, Claude Code divergence→re-plan, Cursor MUST/TERMINATE wording): the ~78% self-adherence ceiling; the fresh-verifier intent-vs-actual diff as the only reliable undeclared-deviation catch; the Implementer prompt pattern (§6.2).
- **claudebox sandbox-cost spike (2026-07-07):** costed the anti-reward-hacking constraints against the fork's source (`internal/docker/container.go`, `run.go`, `network.go`, `proxy.go`, `todo/host-isolation.md`) → no-web-tools (free) + read-only test mounts (ride the overlay seam) + reject net-egress/git-history (§8).
- Sibling specs whose conventions this mirrors: [`spec-lifecycle`](./spec-lifecycle/spec-lifecycle.md) and [`agent-definition`](./agent-definition.md) — with the deliberate difference that this module **extends** its engine (Conductor) rather than owning it (§1).
