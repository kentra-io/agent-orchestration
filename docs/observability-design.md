# Observability leg — design

Status: **design approved in brainstorm 2026-07-19** (issue [#7](https://github.com/kentra-io/agent-orchestration/issues/7)); feeds `lifecycle` refine.
Supersedes the backlog framing in [`observability-notes.md`](./observability-notes.md) (pain points there remain the source incidents).
Component diagram: [`observability-architecture.png`](./observability-architecture.png) (editable source: [`observability-architecture.excalidraw`](./observability-architecture.excalidraw)).

## 1. Problem

Operating a live `execute-change` run has no single "what is happening right now"
surface (three uncorrelated artifacts under `.conductor-tmp/`), and when a run
dies the provider masks the real error ("exited code 1, no stderr" for both
OAuth expiry and transient API failures). Full findings: issue #7.

Additional requirements gathered during design:

- **Multiple parallel runs** — several worktrees per project *and* several
  projects on one host — must be first-class from day one.
- **GitHub Issues integration** — the change's issue becomes the durable,
  human-facing mirror of run state (full lifecycle: start → milestone progress
  → death/escalation → finish → close-on-archive).
- **Start/monitor from any Claude session**, including sessions inside a
  claudebox, without weakening the claudebox sandbox.
- Strictly **local/operator-facing**; the Stage-4 LiteLLM+Langfuse plan later
  consumes the same event stream independently. No trace export here.

## 2. Key insight: reuse Conductor's built-in web dashboard

The pinned fork already ships `conductor run --web` / `--web-bg`
(`conductor/web/server.py`): an in-process FastAPI+WebSocket server subscribed
directly to the live `WorkflowEventEmitter` — **no JSONL buffering lag** — with
event history for late joiners (`/api/state`, `/api/logs`) and a React
frontend. A `ReplayDashboard` renders any recorded events file with a timeline
slider (free post-mortems). We build **zero** per-run live-UI code.

Boundaries of the reuse:

- The dashboard lives and dies with the conductor process. Under the module's
  deliberate **crash-then-resume** gate model (`orchestration/resume/README.md`),
  a human-gate pause *exits* the process — so the dashboard is strictly the
  "live execution" surface, never the durable one.
- Its `gate-respond` API stays unused (crash-then-resume won that design).
- It does not solve: provider error masking, pre-launch health, multi-run
  indexing, mid-turn liveness, checkpoint disambiguation — the rest of this
  design does.

## 3. Architecture — three layers, three lifetimes

| Layer | Lifetime | Surface |
|---|---|---|
| **Live** | while a conductor process runs | Conductor `--web-bg` dashboard, one per run |
| **Durable local** | survives crashes / gate pauses | run registry + checkpoints/events, read by daemon API + status CLI |
| **Durable remote** | forever | the GitHub issue (full-lifecycle mirror) |

### 3.1 One shared orchestrator daemon

A single long-lived host service — **control plane + supervisor + index — in
one process**, containerized from day one (§6):

- **All launches route through it.** `orchestration launch <change>` becomes a
  thin client that POSTs to the daemon (works identically from the host or from
  inside any box). The daemon does the privileged work — worktree, box
  provisioning, registry entry — and spawns `conductor run` as **its own
  child**, giving it real `waitpid` on every concurrent run.
- **Central exit handling**: classify each exit (§5.3), update the registry,
  mirror to GitHub. One classifier, one GitHub client, one log.
- **Serves the surfaces**: `GET /runs` (the fold, JSON), `POST /launch`,
  `POST /resume`, the HTML index page, and reverse-proxied per-run dashboards
  (`/runs/<id>/dashboard/` → the child's in-container port; WebSocket-aware).

**Daemon death ≠ run death.** Conductor children are independent; a restarted
daemon can no longer `waitpid` orphans, so **lazy reconciliation** is a
permanent second path: on startup and periodically, pid-poll registry entries
and classify anything that terminated unobserved. Registry state is *derived,
never trusted* (§4). This also covers direct CLI launches made while the
daemon was down.

**Gates stay crash-then-resume.** The daemon observes gate pauses and mirrors
them; human resolution still flows through GitHub labels + the resume seam.
The daemon does not re-open the rejected "live process answers the gate" path.

### 3.2 Claudebox boundary (why in-box spawning is out)

Verified against claudebox `SECURITY.md`: the socket-proxy enforces
per-project bind-mount rules, label-protects box containers from
exec/visibility (why in-box `docker ps` omits the target box), and strips host
port bindings. An in-box-spawned supervisor container would need the real
socket, out-of-project mounts, and published ports — all three blocked, by
design. Boxes therefore get the *client*, not the machinery: they reach the
daemon over the host gateway (the established host-served pattern), gated by a
bearer token injected via the existing `env:` mechanism (any box can reach the
gateway IP — SECURITY.md §4 — so the token is the gate). A deliberate
"trusted orchestrator box" proxy capability is parked as claudebox roadmap.

## 4. Run registry

`~/.agent-orchestration/runs/<repo-slug>--<change-id>.json` — written by the
daemon (or a direct CLI launch), keyed by **change**, not process. Resumes
append to `incarnations[]` (pid, `workflow_hash`, started_at, dashboard URL,
exit, classified cause). Stored fields are *facts* (paths, ids, pids);
**state is always derived on read**:

```
pid alive?  checkpoint parked at human_gate?  events-tail age?  worktree-mtime age?
→ running | paused: gate | paused: escalated | dead: <cause> | done | archived
```

A stale registry cannot lie. Host-global ⇒ multi-project is automatic.
Recording `workflow_hash` per incarnation kills the "which checkpoint is the
live run" ambiguity (issue #7, sharp edge 4).

## 5. Components

### 5.1 Launch path — logic moves into the daemon, CLI becomes a thin client

The existing launch machinery (`create_worktree`, `materialize_box`,
`start_box`, `resolve_plan`, argv build) becomes library code the **daemon**
invokes on `POST /launch`; the `orchestration launch` CLI is a thin client of
that endpoint (identical from host or box). A `--direct` flag keeps today's
in-process spawn as a daemon-down fallback (reconciliation catches up).
The launch flow gains:

- **Pre-launch health probe**: `claude -p 'OK'` in the box before spawning;
  classified loud failure ("OAuth expired → run `cb login` in the worktree").
  Also runs on resume.
- `--web-bg` on the conductor argv, with a **daemon-assigned `--web-port`**
  (flag verified on `conductor run`; `0` = auto-select).
- Registry entry written before spawn.
- Report gains: dashboard URL, registry path, and a **log legend**
  (`conductor.stdout.log` = final JSON only, empty until done;
  `conductor.stderr.log` = live progress UI).

### 5.2 Status surfaces

- `orchestration status <change|worktree>` — the pure fold, pretty + `--json`
  (Claude's surface): state, current milestone/agent/turn, per-signal
  last-activity ages, dashboard URL, classified cause + remedy if dead.
- `orchestration runs [--json]` — cross-project table (client of the daemon;
  falls back to reading the registry directly).
- Daemon index page — run list, state badges, links to per-run dashboard +
  GitHub issue. Read-only.
- **Skills for operators AND consumer projects** — shipped in-repo
  (agent-agnostic, per the primitive conventions) and distributed via the
  plugin catalog so any project built on this harness (e.g. kafka-dq) gets
  them in its sessions/boxes:
  - `orchestration-monitor` — the runbook: which command answers what; in-box
    `docker ps` is filtered (host authoritative); empty stdout ≠ stuck;
    cause → remedy table; how to read the index page and a run dashboard.
  - `orchestration-launch` — how to start/resume a run from any session
    (thin client + token, what the daemon does, what artifacts appear where).

### 5.3 Exit classifier

Input: exit code + stdout/stderr tails + last checkpoint. Output, in order of
precedence:

1. `success` (terminal JSON on stdout)
2. `gate-pause` — the **by-design** non-zero exit of crash-then-resume; must
   never read as a death
3. `oauth-expired` (remedy: `cb login`)
4. `api-transient` (remedy: resume)
5. `unknown` (raw tails attached)

While a run lives, the daemon stamps events-age + worktree-mtime-age into the
registry and flags `stalled?` advisory when both exceed a threshold (a slow
API turn and a hang still look alike mid-turn; we stop *silently* misreading
it, we don't claim to distinguish it).

### 5.4 GitHub mirror

Join key exists: `changeNaming: <issue-number>-<slug>` + `notify_repo` input.
Split by *who can know*:

- **Workflow-side** (extends the proven `notify_escalation` script-step
  pattern; `dry_run` in the hermetic tier): **milestone ticks** — one
  checklist comment **edited in place** (HTML-comment marker for idempotent
  find-and-edit), no comment spam.
- **Daemon-side** (process-level truths a workflow can't self-report):
  run-started comment, finish comment, death `run-died` label + comment with
  the classified cause and real error text.
- **Archive-side**: close-on-archive wired into `archive_handoff` (pulls in
  the parked spec-lifecycle idea).

Label taxonomy: `needs-human-input` (existing; remedy = fix plan, approve,
resume) vs `run-died` (new; remedy = fix infra, resume). Different remedy,
different label.

## 6. Daemon container

One image (`agent-orchestration-daemon`), `--restart=always`, host-created (so
none of §3.2's blockers apply):

- **Baked**: Python 3.12 + the module venv (kills the host/container
  venv-thrash class permanently — supersedes the "conductor only on host"
  operational rule; the `.python-version` pin stays as harmless belt), docker
  CLI, `gh`, `git`, `lifecycle`, `cb`.
- **Mounts**: `/var/run/docker.sock` (drive boxes / spawn siblings);
  `~/.agent-orchestration` (registry); the **code root at an identical
  container path** (path parity is load-bearing: sibling-container `-v` paths
  resolve on the *host* daemon, so worktree paths must be host paths);
  `~/.claude` (read-only — credential source for box materialization).
- **Env**: `KENTRA_BOT_GH_TOKEN` (existing keychain→env pattern; keychain is
  unreachable in-container), daemon bearer token.
- **One published port**: API + index + reverse-proxied dashboards. Conductor
  children bind random localhost ports inside the container's netns; the
  daemon proxies them — no per-run port publishing.

## 7. Conductor fork patches (ADR-0001: patch fork, pin-bump)

1. **ProviderError stdout tail** — surface the bounded `noise_lines` tail in
   the error diagnostic (the classification half already landed —
   `conductor-fork-patches-pending.md` §2). Highest-value fix; the classifier
   consumes it.
2. ~~`dashboard.json`~~ — **likely unnecessary**: `conductor run` already
   accepts `--web-port` (verified; `0` = auto-select), so the daemon assigns
   deterministic ports and knows every dashboard URL without a patch. Confirm
   at refine whether anything still needs the gate token (we don't use
   gate-respond); if yes, revisit.
3. Event-flush cadence — **explicitly skipped**: the fold treats events as one
   lagging signal among three; no longer load-bearing.

## 8. Non-goals

Langfuse/LiteLLM export (Stage-4) · custom per-run live UI (Conductor's) ·
web gate-respond · push notifications (classifier + registry make this a
trivial later add) · GitHub Projects automation (label queries suffice) ·
claudebox "trusted orchestrator" proxy capability (parked, claudebox roadmap).

## 9. Scoping into lifecycle changes

Two changes off issue #7:

1. **observability-core** — registry, daemon (supervision + API + index +
   dashboard proxy + container), launcher-as-client, health probe, fork
   patch, status CLI, both skills (`orchestration-monitor` +
   `orchestration-launch`) including plugin-catalog listing.
2. **github-mirror** — the four outward flows + close-on-archive + label
   taxonomy; depends on core's classifier; own test story (`dry_run` tier);
   updates the skills with the issue-mirror reading guide.

Core is useful alone; the mirror has network/token concerns. The issue itself
predicted the split.

## 10. Open questions (for refine)

- Exact box-credential wiring in-container: what `materialize_box` needs from
  `~/.claude` and whether read-only suffices.
- WebSocket reverse-proxy details for the dashboard (Rich frontend uses WS;
  pick the proxy approach inside the daemon's FastAPI app).
- Registry entry GC policy (`archived` entries: prune vs keep as history).
- Daemon auth token distribution to boxes (reuse `config.yaml env:` injection
  verbatim, or a per-box token).
- Whether `orchestration resume` fully routes through the daemon in change 1
  or stays CLI-direct until the mirror change.
- Worktree placement convention: launch payloads must keep `worktree_path`
  under the mounted code root, or the daemon can't see/mount-map it — decide
  enforce-vs-document.

## 11. Decision log (brainstorm 2026-07-19)

| Decision | Choice |
|---|---|
| Consumers | Both Claude sessions (JSON/CLI) and human (browser) |
| Stage-4 seam | Strictly local now; Langfuse consumes events later |
| Multi-run | First-class from day one, host-global registry |
| Live UI | Reuse Conductor `--web-bg`; build no per-run UI |
| GH mirror scope | All four flows (progress, deaths, start/finish, close-on-archive) |
| Observer | Shared daemon (control plane + supervision merged) + lazy reconciliation net |
| Index | CLI + daemon-served index page |
| Supervisor placement | Host-side daemon; in-box spawning rejected (sandbox boundary, verified) |
| Daemon packaging | Containerized from day one |
