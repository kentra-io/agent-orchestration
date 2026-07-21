# CLI design — `orch`

Status: **implemented (cli-core)** · 2026-07-21
Builds on: [`observability-design.md`](./observability-design.md) (daemon, registry,
supervision — all shipped in the observability-core change, PR #8).
Implementation plan: [`docs/plans/2026-07-21-cli-core.md`](./plans/2026-07-21-cli-core.md).

## 1. Problem & goals

Driving the module today means hand-writing JSON payloads against the daemon's
HTTP API (curl or `orchestration launch '<json>'`) and managing the daemon via
Makefile targets from a repo checkout. The CLI replaces both with a typed,
installable command:

- **`orch daemon start`** — bring up the containerized daemon with zero manual
  token/env wrangling.
- **`orch launch <change-id>`** — turn a spec-lifecycle change into a running
  workflow without authoring a payload.
- **`orch resume <change-id>`** — resume a paused/dead change through the same
  daemon machinery.

Non-goals (v1): watch/follow mode (the per-run Conductor dashboard is the watch
surface), GitHub issue mirroring (the separate github-mirror change), brew
packaging, multi-arch images, multi-daemon, a slim dependency split.

## 2. Decisions (locked in brainstorm, 2026-07-20/21)

| Decision | Choice |
|---|---|
| Plan input | spec-lifecycle **change id** (production seam); fixture JSON stays the hermetic escape hatch |
| Daemon distribution | **published image** on public GHCR (`ghcr.io/kentra-io/agent-orchestration-daemon`) |
| CLI install | `uv tool install git+https://github.com/kentra-io/agent-orchestration` (all deps public, incl. the conductor fork) |
| Launch UX | async (`wait: false`), **auto-open the dashboard** in the browser; `--no-open` and non-TTY suppress |
| Command name | **`orch`** (primary); `orchestration` kept as alias |
| Bare-launch defaults | **production tier** (box enabled, claude provider, plan via `lifecycle apply --format json`); `--stub` flips the whole hermetic tier in one flag |
| Resume | **in scope**, via a real daemon `POST /resume` (lifts observability-core's deliberate 501; GitHub-mirror concerns stay out) |

## 3. Shape & packaging

New subpackage `orchestration/cli/` — plain argparse, stdlib only, no new
dependencies:

```
orchestration/cli/
  main.py        # parser tree, exit-code policy
  config.py      # ~/.agent-orchestration/daemon.json read/write, precedence
  daemon_cmd.py  # start/stop/status/logs (docker shell-outs)
  launch_cmd.py  # launch + resume: change-id → payload, auto-open
  payloads.py    # the two payload templates (production / stub), golden-tested
```

`pyproject.toml` `[project.scripts]`: `orch` **and** `orchestration`, both →
`orchestration.cli.main:main`. `orchestration/__main__.py` becomes a delegation
stub so `python -m orchestration` keeps working. Existing behaviors (`runs`,
`status`, `launch --payload/-`, `--direct`) are preserved verbatim under the new
tree.

## 4. Command surface

```
orch daemon start [--image REF] [--code-root DIR]
orch daemon stop
orch daemon status
orch daemon logs [-f]

orch launch <change-id> [--repo PATH] [--stub] [--milestones-file F]
                        [--issue N] [--branch B] [--no-open]
orch launch --payload FILE|-|JSON [--direct]      # raw escape hatch, unchanged
orch resume <change-id> [--repo PATH] [--no-open]

orch runs                    # existing table
orch status <change-id>      # existing folded JSON
```

## 5. Config file & credential precedence

`orch daemon start` owns `~/.agent-orchestration/daemon.json` (mode 600):

```json
{"url": "http://127.0.0.1:8765", "token": "<hex>", "image": "<optional override>", "code_root": "~/code"}
```

Client lookup precedence (in `orchestration/client.py`): env
`ORCHESTRATION_DAEMON_URL` / `ORCHESTRATION_DAEMON_TOKEN` (boxes keep their
config.yaml env-injection pattern, unchanged) → `daemon.json` → current
defaults. This removes the manual token dance on host: after one
`orch daemon start`, every `orch` command authenticates from the file.

## 6. Daemon lifecycle

`orch daemon start`:
1. Preflight: docker reachable, else exit 2 with install pointer. Container
   already running → print status, exit 0 (idempotent).
2. Image: config/`--image` override, else
   `ghcr.io/kentra-io/agent-orchestration-daemon:latest`. Missing locally →
   `docker pull`; on auth failure, hint `docker login ghcr.io` (covers a
   private package or rate limiting).
3. Token: generate (`secrets.token_hex(16)`) if absent from config; persist.
4. `docker run` with exactly the flags `make daemon-run` uses today
   (docker.sock, `~/.agent-orchestration`, `~/.claude:ro`, code-root mount,
   `KENTRA_BOT_GH_TOKEN` passthrough, `-p 8765:8765 -p 42000-42050:42000-42050`,
   `--restart=always`), token passed from config.

`stop` = `docker rm -f agent-orchestration-daemon`. `status` = container state
+ `GET /runs` health + image ref. `logs` = `docker logs [-f]`. The Makefile
targets remain the local-dev path (build-from-checkout).

## 7. Launch semantics

`orch launch <change-id>`:
- `repo` = `git rev-parse --show-toplevel` of cwd; `--repo` overrides.
- **Production tier (default):** payload = box enabled, claude provider, plan
  source = the real spec-lifecycle surface (the workflow's `read_plan` runs
  `lifecycle apply <change> --format json` in the worktree at run time — per
  `execute-change.yaml`). At CLI time, `orch` runs the same command once to
  (a) validate the change exists and folds (else list available ids) and
  (b) count milestones to compute `max_iterations` (constitution ADR-0002:
  computed from the plan, never guessed).
- **`--stub`:** stub provider, box disabled, milestones from
  `--milestones-file` or the canonical demo fixture shipped **inside the
  package** (`orchestration/cli/data/stub_demo{,.stub}.json`, lifted from the
  `tests/fixtures/execute_change_2_milestones*` shapes — `tests/` is not in
  the wheel, so a uv-tool install must not depend on it). One flag = the whole
  hermetic tier; no change-dir validation.
- Both tiers live as constants in `cli/payloads.py`, golden-tested, and always
  send **top-level `wait: false`** — the CLI returns in ~2s.
- Output: change id, state, dashboard URL, `orch runs` hint; then auto-open the
  dashboard via stdlib `webbrowser`, suppressed by `--no-open` or when stdout
  is not a TTY (scripts and boxes never get a surprise browser).
- Enabler: the launch report gains a `dashboard_url` field (today it is written
  only to the registry incarnation, `orchestration/launch/change.py:623`), so
  the CLI needs no follow-up poll.
- No silent fallback: daemon down → exit 1, "run `orch daemon start`".
  `--direct` (in-process spawn, reconciled later) stays explicit-only.

## 8. Resume semantics

Daemon `POST /resume` becomes real (observability-core shipped it as a
deliberate 501; this change implements the process-resume half and leaves the
GitHub-mirror half — issue comments, labels, close-on-archive — to the
github-mirror change).

`POST /resume {repo, change_id}`, token-gated, as a sibling of `/launch`:
1. Look up the registry entry (404 → "nothing to resume"); reject if the
   current derived state is `running`.
2. Re-derive remaining milestones from the (possibly human-edited) plan via
   `orchestration/resume/plan.py` (`load_milestones_from_apply` +
   `derive_remaining_milestones` — already built, M7). This runs daemon-side:
   the daemon image already ships the `lifecycle` binary.
3. Allocate a dashboard port from the same `PortAllocator`, spawn
   `conductor resume` in the change's **existing worktree** from its checkpoint
   dir, adopt the process into the supervisor.
4. Append a new incarnation to the registry entry (the incarnations array
   exists precisely for this).

`orch resume <change-id>` posts, prints, auto-opens — same UX as launch.
Resume is the least-live-proven path in the module; the implementation plan
must include a stub-tier resume smoke (kill a run mid-flight, resume it).

## 9. GHCR publishing (new CI surface)

New workflow in this repo: on version-tag push, build the daemon image
(linux/arm64 — the Dockerfile is already aarch64-pinned) and push to
**public** `ghcr.io/kentra-io/agent-orchestration-daemon:{tag,latest}`
(GitHub defaults the first publish to private — flip visibility once).

The `bin/cb` copy-from-host hack moves into CI: a prior job step checks out
`kentra-io/claudebox` (public) at a pinned ref and builds `cb` for
linux/arm64 — no token or secret needed. The pinned claudebox ref lives in the workflow
file and is bumped deliberately, like the conductor fork pin (constitution
ADR-0001 spirit).

## 10. Error handling

Every failure mode carries a one-line remedy:

| Failure | Message gist | Exit |
|---|---|---|
| daemon down (launch/resume) | run `orch daemon start` | 1 |
| 401 from daemon | token mismatch — rerun `orch daemon start` / check daemon.json | 1 |
| GHCR pull denied | `docker login ghcr.io` hint | 1 |
| unknown change id | list of `openspec/changes/` entries | 1 |
| nothing to resume / already running | say which | 1 |
| docker absent/unreachable | install/start pointer | 2 |

Exit codes: 0 ok · 1 user-fixable · 2 environment broken.

## 11. Interface seams (what this module owns vs imports)

The module's composed surface is three contracts; it **owns one, imports two**:

- **Execution surface (owned):** the `orch` CLI + daemon HTTP API + workflow
  templates.
- **Plan schema (imported from spec-lifecycle):** `lifecycle apply <change>
  --format json` — milestones (`id`/`title`/`steps`) + structured `contract`
  (`check`/`criteria`/`paths`). This module never defines its own plan format;
  the hermetic fixture mirrors that shape.
- **Agent definitions (imported from agent-definition, future):** the planned
  `.agf.yaml` primitive. Interim stand-in: hand-authored
  `personas/{implementer,orchestrator,verifier}.md` in this repo. When agentdef
  ships, `personas/` is replaced by materialized output and the CLI grows a
  cast-selection seam (e.g. `--cast`) — out of scope now; nothing in this
  design may paint over that door.

## 12. Testing

Unit (CI): golden payload dicts for both tiers; config precedence (env > file >
default); docker argv construction (subprocess mocked); auto-open suppression
(non-TTY, `--no-open`); idempotent `daemon start`; `/resume` handler against a
faked registry entry (mirrors the existing `/launch` tests). CI does not run
docker-in-docker.

Live (manual, host — the acceptance demo): `orch daemon start && orch launch
my-demo --stub` replaces the curl instruction; plus one kill-and-`orch resume`
smoke on a stub run.

## 13. Sequencing

Branch off `main` **after PR #8 merges**. Order inside the change: cli package
skeleton + config → daemon commands → launch → `/resume` + resume command →
GHCR workflow → skills/README updates (`orchestration-launch` /
`orchestration-monitor` teach `orch`, not curl).
