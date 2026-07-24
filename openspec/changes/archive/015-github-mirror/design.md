# GitHub mirror — the issue as the full-lifecycle progress view — Design

## Context

Observability core (issue #7) shipped the ephemeral/local legs: per-run
Conductor dashboard, facts-only run registry, exit classifier, daemon
(supervision + API + index). The durable, human-scannable leg — the change's
source-tracking issue as the full-lifecycle mirror (start → milestone
progress → death/escalation → finish → close-on-archive) — is this change
(issue #15; `docs/observability-design.md` §5.4). The refine-approved spec
delta (`specs/github-mirror/spec.md`) declares seven ADDED requirements:
branch push, independent best-effort side effects, edited-in-place
checklist, daemon start/finish comments, death surfacing with real error
text, the `run-died` vs `needs-human-input` label taxonomy, and
close-on-archive.

Code facts this design builds on (verified 2026-07-24):

- **The script-step convention** — `orchestration/launch/notify_escalation.py`
  is the module's only `gh` shell-out: stdin JSON input, `dry_run` default
  true, `gh_`-prefixed subprocess result fields, exit codes 0 (good) /
  1 (attention) / 2 (input error), never raises on `gh` failure.
- **Script exit codes are not self-enforcing** (`workflows/milestone.yaml`
  ~315): Conductor's script executor only records `exit_code`; *routing*
  turns it into failure. `commit` fails the milestone only because an
  explicit `when: exit_code == 0` guard routes elsewhere to a
  `type: terminate` sink; `escalate` routes unconditionally and can never
  fail the run. This is the exact mechanism "best effort" hangs on.
- **The workflow-side mirror is unreachable in production today**:
  `milestone.yaml` declares `notify_repo`/`notify_issue`, but
  `execute-change.yaml` neither declares nor forwards them in
  `milestone_step.input_mapping` — only `notify_dry_run` passes through.
- **`milestone_commit.py` is deliberately push-free** (verbs: add, commit,
  rev-parse only; no `git push` exists anywhere in the module).
- **The daemon does zero GitHub today** but its container already receives
  `KENTRA_BOT_GH_TOKEN` and ships `gh` + `git`. Exit classification happens
  in `supervise.py` `poll_once`/`reconcile`; both return event dicts that
  `daemon/app.py` currently **discards** — a clean notifier seam. The
  classifier verdict is `Verdict(kind, remedy, detail)` where `detail`
  carries the real log-tail text (the fork patch that un-masks
  ProviderError has landed).
- **Registry has `issue` but no `owner/repo`**: `entry["repo"]` is a local
  path; `repo_slug` is its basename; the dashboard hardcodes `kentra-io`.
- **`--direct` launches are invisible to the live supervisor** (no
  `adopt`); only lazy `reconcile` ever classifies them.

## Goals / Non-Goals

**Goals:**

- Implement the seven spec-delta requirements with three writers split by
  *who can know* the fact (workflow / daemon / archive hand-off).
- Make the workflow-side mirror **production-reachable**: thread
  repo/issue/branch/dry-run inputs through all three layers
  (launcher payload → `execute-change.yaml` → `milestone.yaml` → step stdin).
- Preserve the hermetic Stub tier untouched-by-default: every new step
  defaults `dry_run: true` — no network, no `gh`, no token in CI.
- Introduce no new blocking failure mode: a run with zero GitHub
  reachability completes exactly as today.
- Update `orchestration-monitor` / `orchestration-launch` skills with the
  issue-mirror reading guide (which flow posts what, label meanings, the
  edited-in-place checklist) — including the explicit rule that the mirror
  can be out of sync with local state and that **local state is the source
  of truth** (D10).

**Non-Goals:**

- The resume flow (daemon `POST /resume` shipped with the `orch` CLI
  change; this change only *references* resume in remedy text).
- Any `spec-lifecycle` change (see D7 — close-on-archive stays in this
  module).
- Web gate-respond, GitHub Projects automation, Langfuse/LiteLLM export,
  per-run live UI, launch-time workflow unrolling (parked alternatives from
  issue #15 / observability design §8).
- Mirroring `gate-pause` as any kind of event (by-design pause; the
  existing workflow-side `escalate` step already owns the
  `needs-human-input` label).
- Retro-mirroring runs launched before this change ships.

## Decisions

### D1 — Three writers, split by who can know (placement confirmed)

- **Workflow-side** (runs inside the conductor child, so it fires for
  daemon-launched *and* `--direct` runs): per-milestone branch **push** and
  checklist **tick**, as two new script steps in `milestone.yaml` following
  the `notify_escalation` conventions.
- **Daemon-side** (process-level truths the workflow cannot self-report):
  run-started / run-finished comments, `run-died` label + death comment —
  hooked on `adopt` (launch/resume) and on the classification events
  `poll_once`/`reconcile` already emit.
- **Archive-side**: close-on-archive inside
  `orchestration/launch/archive_handoff.py` on `status == "archived"`.

*Alternative considered*: daemon-only mirroring (single writer, single
token). Rejected — the daemon cannot see milestone-grain progress (it
watches a process, not workflow internals), and `--direct` runs would lose
the checklist entirely. The who-can-know split is what the approved
observability design (§5.4) prescribes.

### D2 — Push and tick are separate steps with report-only routing

New step sequence in `milestone.yaml`:

```
commit ── exit_code == 0 ──▶ push ──(unconditional)──▶ tick ──(unconditional)──▶ $end
   └── else ──▶ commit_failed (terminate, unchanged)
```

- `push` (`orchestration/launch/milestone_push.py`) runs only after a
  successful (or dry-run) commit; `tick` runs **unconditionally after
  push**, regardless of push outcome. Unconditional routing — not exit-code
  guards — is the mechanism that makes both best-effort: like `escalate`,
  they structurally *cannot* fail the milestone.
- Two steps, not one combined "mirror" step, because the spec requires
  **independent** attempts (issue unreachable ⇒ push still lands, and vice
  versa) and because they shell different tools (git network vs `gh`).
- `tick` receives `push.output.exit_code` (and the commit status) in its
  stdin template, so a failed push is annotated in the checklist as
  completed-but-local-only — recorded, never hidden.
- Exit-code contract for both: 0 good / 1 attention (side effect attempted
  and failed) / 2 input error — `notify_escalation`'s convention. All three
  route onward.

*Alternative considered*: guard `tick` on `push.exit_code == 0`. Rejected —
it would hide exactly the local-only state the spec requires the checklist
to surface.

### D3 — Push mechanics: plain push to the run branch, no force, gh-managed auth

- `milestone_push.py` mirrors `milestone_commit._git`'s shape
  (`git -C <worktree>`, timeout, `check=False`) and pushes
  `origin HEAD:refs/heads/<branch>` where `<branch>` is the launch `branch`
  input (default `change/<change_id>`), threaded into the step like
  `change_id` is today.
- **Never `--force`**: a non-fast-forward rejection is reported as a push
  failure (attention exit) and the run proceeds — a later successful push
  publishes the accumulated branch, per the spec.
- Push auth rides `gh`'s git credential helper (`gh auth setup-git` in the
  daemon container image), so the same bot-token identity covers git-push
  and `gh` API writes; no raw token ever appears in a remote URL or argv.
- `dry_run` default true: prints `would_run` argv, touches nothing —
  same contract as `milestone_commit`.

*Alternative considered*: pushing from `milestone_commit.py` itself.
Rejected — commit is deterministic-local and load-bearing (its failure
terminates the milestone); push is best-effort-network. Different failure
policies must not share a step, and commit stays push-free by prior design.

### D4 — Checklist: one comment, stable HTML marker, full re-render

- The tick step locates the mirror comment by a stable first-line HTML
  marker: `<!-- agent-orchestration:mirror:<change_id> -->`. Lookup pages
  the issue's comments via `gh api`; edit-in-place via
  `gh api -X PATCH .../issues/comments/<id>`; create-if-absent. Marker
  match, not comment position or author, is the idempotency key.
- Each tick **re-renders the whole checklist body** from: the change's
  milestone manifest (ids + titles — the list the launcher already
  resolves from the plan, threaded down as a workflow input), the checked
  state parsed from the existing comment, plus the current milestone's
  result. Re-render is idempotent and self-healing (a lost edit is
  repaired by the next tick); an incremental line-patch would drift.
- Body: header naming the run branch, one `- [ ]`/`- [x]` item per
  milestone; a milestone whose push failed renders checked **with an
  explicit `(local-only: push failed — <reason>)` annotation**, cleared by
  a later successful push's tick.

*Alternative considered*: comment-per-milestone (append-only). Rejected —
comment spam; the approved design explicitly chose edited-in-place.
*Alternative*: deriving checked state purely from the previous comment
without a manifest. Rejected — the first tick could not render the full
milestone list, and a garbled comment could never self-heal.

### D5 — Daemon mirror client on the discarded-events seam; facts-only dedupe

- New `orchestration/daemon/github_mirror.py`: a small `gh`-shelling client
  (list/create/patch comment, add label, ensure label exists, close issue),
  `check=False`, tail-captured stderr, never raises — the
  `notify_escalation` failure posture, importable by daemon and scripts.
- Hook points: `app.py`'s launch/resume handlers post **run-started**
  (adopt time); the supervision loop stops discarding
  `poll_once`/`reconcile` events and hands them to the mirror —
  `success` ⇒ run-finished comment; any kind that is neither `success` nor
  `gate-pause` ⇒ ensure + add `run-died` label and post the death comment
  with `verdict.kind` (cause), `verdict.remedy`, and `verdict.detail` (the
  real error text). `gate-pause` ⇒ nothing, per spec.
- **Dedupe via registry facts**: performed writes are recorded on the
  incarnation (e.g. `mirror: {started: true, terminal: true}`) so a
  restarted daemon or a later `reconcile` pass never double-posts. These
  are facts (side effects that happened), so they respect the
  registry's facts-only rule — state stays derived-on-read.
- The `run-died` label is ensured (`gh label create` best-effort, cached
  per daemon process) before first use, so a fresh consumer repo needs no
  manual label bootstrap.

*Alternative considered*: a webhook/Actions-based mirror. Rejected — the
daemon already owns exit classification and the token; adding a second
infrastructure leg for the same facts violates simplicity-first.

### D6 — Who posts start/finish for `--direct` and resumed runs

- **Daemon-launched runs**: `POST /launch` posts run-started; `POST
  /resume` posts a resumed variant of the started comment (same writer,
  incarnation-aware wording). The spec's "when it launches a run" binds
  the daemon path — resume is a new incarnation and is worth the same
  breadcrumb.
- **`--direct` runs** (daemon down or bypassed): no start comment — nothing
  is alive to post one, and the workflow must not fake a process-level
  truth. Terminal state is still mirrored **lazily and best-effort** by
  `reconcile` when the daemon next observes the dead pid (the registry
  entry — written by direct launches too — carries `issue` + repo
  identity + the dedupe facts). Workflow-side push/tick fire regardless,
  because they run inside the conductor child.
- This asymmetry is documented in the `orchestration-launch` skill's
  reading guide: a `--direct` run's issue shows checklist progress but no
  start comment until/unless a daemon reconciles it.

*Alternative considered*: workflow-side start/finish comments so `--direct`
runs mirror fully. Rejected — the workflow cannot know observed-exit truths
(that is the whole who-can-know split), and a self-reported "finished"
comment from inside the run would be unreliable exactly when it matters
(crash paths).

### D7 — Close-on-archive lives in `archive_handoff.py`; no spec-lifecycle change

- On `status == "archived"` (lifecycle exit 0, fold completed),
  `archive_handoff.py` closes the issue with a closing comment referencing
  the archive, via the shared `gh` client, gated by the same
  `dry_run`/repo/issue inputs. `refused` / `error` leave the issue open.
- Best-effort: a close failure is reported in the hand-off's JSON verdict
  but never alters `archive_handoff`'s status or exit code — archiving
  locally succeeded; the mirror is an annotation on that fact.
- The parked "spec-lifecycle closes the issue on archive" seam is
  **explicitly not taken**: `spec-lifecycle` is a neutral, GitHub-free
  primitive (harness constitution "Neutral mechanism, branded
  methodology"); wiring `gh` into it would leak a branded/tooling concern
  into the neutral layer. The orchestration module — which already owns
  the launch context and the token posture — is the right home.

*Alternative considered*: daemon-side close (observe the workflow's
`archive_handoff` output in the final state). Rejected — the archive verb
runs inside the workflow; the hand-off script is the first place the
`archived` fact exists, and closing there also covers `--direct` runs.

### D8 — Token posture: bot identity everywhere, ambient never (ADR proposed)

All GitHub writes — daemon comments/labels, workflow-side tick, branch
push, close-on-archive — authenticate as the **bot identity** via
`KENTRA_BOT_GH_TOKEN` mapped to `GH_TOKEN` for `gh` (and to git via `gh
auth setup-git`), in the daemon container where both the conductor children
(script steps) and the daemon writer run. Host-side `--direct` runs use the
same env var when present and otherwise inherit ambient `gh` auth — writes
stay best-effort, so absent auth degrades to logged failures, never a
blocked run. No mirror write ever uses the human's OAuth/session
credentials by design. Formalized as an ADR proposal because it is a
standing rule for **every future** GitHub side effect, not just this
change's writers: see `adr-proposals/github-writes-posture.md`.

### D9 — Input threading closes the production gap

`execute-change.yaml` declares and forwards (via `milestone_step.
input_mapping`) the full mirror input set: `notify_repo`, `notify_issue`,
`branch`, `push_dry_run`, plus the milestone manifest the checklist
renders from; `milestone.yaml` declares the matching inputs. The launcher
(`payloads.production_payload` / launch path) supplies them: `notify_issue`
from the existing `--issue` flag, `notify_repo` derived from the repo's
`origin` remote URL (overridable by an explicit payload field), `branch`
from the existing launch input. The same `owner/repo` derivation is stored
on the registry entry (new fact field, e.g. `repo_gh`) at launch so the
daemon writer never guesses an org (removing the dashboard's hardcoded
`kentra-io` assumption for mirror purposes). `push_dry_run` is a separate
flag from `notify_dry_run` (matching the existing per-step
`commit_dry_run`/`notify_dry_run` precedent); the production launcher flips
all mirror flags together, the Stub tier leaves all defaulted true.

*Alternative considered*: one global `mirror_dry_run` flag. Rejected —
existing per-step flags set the precedent, and independent flags keep each
step's contract self-contained (a step never inspects another's config).

### D10 — The mirror is advisory: local state is the source of truth

Every mirror write is best-effort, so the issue **can** lag or diverge from
reality: a failed push (checklist ahead of GitHub), a failed comment write
(GitHub behind the run), a down daemon or `--direct` launch (no start
comment), an absent/expired token (nothing mirrored at all). The standing
rule — surfaced to users, not just implied by the failure posture — is:
**when GitHub and local state disagree, local state wins.** The
authoritative surfaces are local: the registry + derived state
(`orch status` / `orch runs`), the worktree's branch and commits, and the
lifecycle artifacts in the change folder. The rule is made visible in two
places:

- **Both skills** (`orchestration-monitor`, `orchestration-launch`) open
  their issue-mirror reading guide with this rule and enumerate the known
  divergence shapes, each mapped to the local surface that answers it
  (e.g. checklist unticked but milestone committed → `git log` on the run
  branch / `orch status`; no start comment → the run may be `--direct`,
  check the registry).
- **The mirror flags divergence where it can know it**: the
  completed-but-local-only checklist annotation (D4), and a standing
  checklist footer stating the mirror is a best-effort projection and
  naming `orch status <change>` as the authoritative check — so a human
  reading only the issue is told when (and how) to distrust it.

*Alternative considered*: reconciling GitHub → local (treating the issue
as a writable source and syncing back). Rejected — writes flow strictly
local → GitHub; the issue is a projection, and bidirectional sync would
manufacture conflicts the run model has no way to resolve.

### D11 — Test story follows the module's existing hermetic patterns

- `milestone_push.py`: real-git `tmp_path` repos (the `milestone_commit`
  fixture pattern) pushing to a **local `git init --bare` origin** — real
  push semantics, zero network; failure paths via a nonexistent remote.
- Tick + daemon mirror + close-on-archive: dry-run verdict assertions plus
  live-mode `monkeypatch.setattr(mod.subprocess, "run", _fake_run)`
  fake-gh (the `notify_escalation` test pattern; still no shared fixture —
  local monkeypatch per test module, per convention).
- Supervisor hooks: hermetic `ORCHESTRATION_REGISTRY_DIR` + short-lived
  real child processes (the `test_daemon_supervise` pattern), asserting
  mirror-dedupe facts land on the incarnation.
- CI stays tokenless: every new step defaults `dry_run: true`; no test
  contacts the network.

## NFR Discharge

(none declared) — the refine delta expresses its quality constraints
(hermetic dry-run default, idempotent single-comment editing, non-blocking
best-effort independence) as behavioral requirements with scenarios in
`specs/github-mirror/spec.md` itself; no internal-quality NFR without
externally observable behavior was declared, so nothing routes to this
section. The one cross-cutting internal posture (token identity) routes to
an ADR proposal instead (D8).

## ADR proposals

- `adr-proposals/github-writes-posture.md` — **GitHub writes are
  best-effort, bot-identity, dry-run-by-default side effects** (from D8):
  a standing rule for all current and future GitHub side effects in this
  module. **Accepted individually at gate 2 (2026-07-24) → ADR-0005**,
  written via the constitution primitive's own flow and projected into
  `constitution/constitution.md`.

## Risks / Trade-offs

- **[Non-FF push rejection]** (someone pushed to the run branch outside
  the run) → never force-pushed over; reported as attention + annotated
  local-only in the checklist; human resolves the branch. Mitigation:
  branch naming (`change/<change_id>`) makes outside writes unlikely.
- **[Checklist comment corrupted/deleted by a human]** → full re-render
  from manifest + marker self-heals on the next tick; worst case a
  duplicate marker comment is created once and the older one goes stale
  (lookup takes the first match; documented in the skill guide).
- **[Reconcile double-posting after daemon restarts]** → dedupe facts on
  the incarnation (D5); reconcile checks before writing.
- **[`owner/repo` derivation from `origin` breaks on forks/mirrors]** →
  explicit payload override field wins over derivation (D9).
- **[Bot token expired/absent]** → every write degrades to a logged
  attention result; runs never block; the daemon's existing logs plus the
  `gh_stderr_tail` fields surface the auth failure. Out-of-band remedy
  unchanged (recreate container from fresh shell per the established
  token-refresh pattern).
- **[Two writers, one issue]** (workflow tick vs daemon comments) → no
  write overlap: the tick edits only the marker comment; the daemon only
  creates its own comments/labels. Milestones are sequential within a run,
  so tick-vs-tick races don't arise.
- **[gh CLI as the API surface]** (vs a Python GitHub client) → accepted:
  matches the module's only existing convention, keeps the dependency set
  unchanged, and `gh` handles auth/retries/pagination; the cost is
  subprocess-shaped error handling, already idiomatic here.
