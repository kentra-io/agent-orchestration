# Handoff — observability-core live bring-up (host session)

**For:** the next agent, running `claude` **on the host** (NOT inside claudebox).
**Written:** 2026-07-20, by the in-box agent that implemented observability-core.
**Repo:** `kentra-io/agent-orchestration`, branch `7-observability` (PR #8, base `main`).

You are on the host, so — unlike the previous (in-box) agent — you **can** run
`docker`, reach the daemon, create git worktrees, and watch dashboards directly.
That is the whole point of moving to the host.

---

## 0. READ THESE FIRST (do not skip — you need the whole plan in context)

Before touching anything, read, in order:
1. **`docs/plans/2026-07-19-observability-core.md`** — the full 14-task
   implementation plan you are continuing. Read it end to end; it is the spec for
   every module below and defines the payload/registry/status contracts you will be
   debugging against.
2. **`docs/observability-design.md`** — the design this plan implements (issue #7,
   design §9 change 1): the *why* behind the daemon/registry/status-fold split.
3. This handoff (the live-run delta on top of that plan).

Do not act on the summaries below as a substitute for reading the plan — they are an
orientation map so the plan reads faster, not a replacement for it.

### Architecture in one screen (what observability-core built)
A host-side **control + observability plane** around Conductor `execute-change` runs:

- **`orchestration/obs/registry.py`** — a host-global JSON run registry at
  `~/.agent-orchestration/runs/`, one file per change (`<slug>--<change_id>.json`),
  **facts only** (repo, worktree, branch, box, incarnations w/ pid/port/exit).
  State is **never stored** — it is derived on read.
- **`orchestration/obs/classify.py`** — a pure exit classifier: `(exit_code,
  stdout/stderr tails, checkpoint_agent) → Verdict` of kind
  `success | gate-pause | oauth-expired | api-transient | unknown`, each with a
  remedy. This is the vocabulary the whole system speaks about *why a run stopped*.
- **`orchestration/obs/status.py`** — the status fold: joins pid liveness +
  events-JSONL age + worktree mtime into a derived state (`running`, `stalled?`,
  `paused: gate`, `dead: <cause>`, `registered`, `dead: unreconciled`). Events are
  treated as a **lagging** signal (they flush minutes late — a frozen events file
  is NOT a stall).
- **`orchestration/launch/change.py`** — the launcher: create worktree → (optional
  box + pre-launch **health probe** that fails LOUD with a classified cause) →
  resolve plan → spawn `conductor run [--web]` → write registry entry + incarnation
  → optionally wait + record exit. `dry_run` stops before the spawn (but still makes
  the worktree + resolves the plan — see §2).
- **`orchestration/daemon/`** — ONE containerized daemon = the control plane:
  `ports.py` (published-range allocator, no reverse proxy), `supervise.py`
  (`Popen.poll` + lazy reconcile of vanished pids from log tails), `app.py`
  (FastAPI: `GET /runs`, `POST /launch` [token-gated, forces `--web`], `GET /`
  HTML index, `POST /resume` → `501`), `__main__.py` (uvicorn).
- **`orchestration/client.py` + `__main__.py`** — a stdlib thin client + CLI
  (`orchestration runs|status|launch`) that talks to the daemon over HTTP and
  **falls back to reading the registry directly** when the daemon is down.
- **`container/daemon/`** — the host-built daemon image; **`Makefile`** targets
  `daemon-image`/`daemon-run`/`daemon-logs`.
- **2 consumer skills** (`orchestration-launch`, `orchestration-monitor`) +
  `.claude-plugin/plugin.json`.
- **Conductor fork patches** (`kentra-io/conductor@088e35c`): ProviderError stdout-
  noise tail + `CONDUCTOR_WEB_HOST` bind override (so `--web` binds `0.0.0.0` in the
  container for published-port dashboards).

Design drift resolved during the build (know these — they bite in live runs): the
index is plain HTML (no reverse proxy; ports published via `CONDUCTOR_WEB_HOST`);
`derive_state` is pid/signals-driven (not incarnation-gated); the launch payload key
is **`worktree_root`** (a *root dir*; the launcher derives `<root>/<change_id>`),
**not** `worktree_path`.

---

## 1. Where things stand

`observability-core` (the 14-task plan in
`docs/plans/2026-07-19-observability-core.md`) is **implemented and merged-pending**:
PR #8 is open, CI green (180 passed / 6 skipped). The Conductor fork is pinned at
`kentra-io/conductor@088e35c` (branch `kentra-patches`).

The user then stood the daemon up on the host for the **first live smoke** and we
hit two bugs. One is fixed-and-pushed, one is fixed-but-uncommitted (see §2).

### The daemon is ALREADY running on the host
The user ran `make daemon-image` + `make daemon-run`, has a token, and can load
`http://localhost:8765`. **BUT that container is the OLD image** — it predates the
Dockerfile fix in §2, so it will still fail worktree creation. You must rebuild +
restart it (§4) before any launch that creates a worktree can succeed.

Get the running token back at any time:
```bash
docker exec agent-orchestration-daemon printenv ORCHESTRATION_DAEMON_TOKEN
```

---

## 2. Two bugs found in the first live run

**Bug 1 — my curl, not the code.** `POST /launch` requires `conductor.workflow`
in the payload (`launch()` raises `ChangeLaunchError` without it). The first curl
omitted it → 500 with an empty body. Not a code bug; just include the field.

**Bug 2 — REAL, in the image (fix written, NOT yet committed).** `launch()` calls
`create_worktree()` → `git worktree add` **before** the `if dry_run` return. The
daemon runs as **root inside the container**, operating on repos bind-mounted from
the host (owned by `jony`). Git refuses these as *"detected dubious ownership"* and
the Dockerfile never trusted them. The in-box test suite could never catch this
(no Docker socket in a box → the daemon-spawns-worktree path never runs there).

Fix already applied to `container/daemon/Dockerfile` (uncommitted in the working
tree): a `RUN git config --system --add safe.directory '*'` line right after the
`apt-get install ... git`. Safe because the daemon only touches repos the operator
explicitly mounted.

### Git state to reconcile first
```
branch 7-observability
  cae6ef9  Makefile: resolve claudebox binary...   <- COMMITTED + PUSHED
  (working tree)  M container/daemon/Dockerfile     <- the §2 Bug-2 fix, UNCOMMITTED
```
**Action:** verify the Dockerfile diff is the `safe.directory` line, then commit +
push it to `7-observability` (it belongs on PR #8):
```bash
git -C <repo> add container/daemon/Dockerfile
git -C <repo> commit -m "daemon image: trust bind-mounted repos (git safe.directory) so worktree add works as root"
git -C <repo> push
```

> Heads-up on `dry_run`: it is NOT a zero-side-effect no-op. It still runs
> `create_worktree()` (real worktree + branch) and `resolve_plan()`; it only skips
> the Conductor **spawn**. The earlier "cheap empty-row demo" framing was wrong.
> Any worktree it makes lives at `<worktree_root|repo/.worktrees>/<change_id>` and
> is removed with `git -C <repo> worktree remove <path>` (+ delete the `change/<id>`
> branch).

---

## 3. The goal: a Level-1 hermetic stub run (watch a real dashboard, no LLM/cost)

Drive daemon → `conductor run --web` → live dashboard → supervisor poll →
`dead: success` in the registry, using `--provider stub` (scripted fake agents) and
`box.enabled: false`. No LLM, no tokens, no real box.

### Two fixture files are needed (the plan referenced but never created them)
`execute-change.yaml` names a canonical `tests/fixtures/execute_change_2_milestones.json`
in a comment, but **it does not exist**. You need:

1. **A plan fixture** — JSON shaped `{"milestones": [{"id": <int>, "title": <str>,
   "steps": [...], "contract": {...}}, ...]}` (see
   `orchestration/resume/plan.py::load_milestones` docstring; `contract` optional).
2. **A stub script** — JSON shaped `{"steps": {"<agent-step-name>": [<response>, ...]}}`,
   passed to the conductor subprocess via env `CONDUCTOR_STUB_SCRIPT=<path>`.
   Reference: `tests/fixtures/m1b_stub_script.json`.

**DO NOT hand-author the stub responses blind.** The agent step names the ladder
invokes are `implementer`, `gates`, `verifier`, `orchestrator` (from
`workflows/milestone.yaml`), and `verifier` must emit `pass: true` (+ `score`,
`violations`, `notes`) or the ladder escalates and the run won't go green. Getting
those verdict shapes right by guessing is error-prone.

**Instead, lift a known-good 2-milestone plan + stub script from the tests that
already run this exact path in stub mode:**
- `tests/test_workflows_flatten.py` — runs `execute-change.yaml` end-to-end over
  multiple milestones with `--provider stub`. This is the authoritative source.
- `tests/test_workflows_ladder.py` — the single-milestone ladder in stub mode.
- `tests/stub_provider.py` — how the stub script is consumed / shaped.
Copy their inline plan + script into the two fixture files above (place them under
a host path the daemon can see — anywhere under the mounted `CODE_ROOT`, e.g. in
the repo). The mount is identity-path (`$CODE_ROOT:$CODE_ROOT`), so the same
absolute path works inside the container.

---

## 4. Rebuild + restart the daemon (after §2 commit)
```bash
cd <repo>            # kentra-io/agent-orchestration checkout on the host
make daemon-image CB_BIN=$(command -v claudebox)     # `cb` is a zsh function; claudebox is the binary
ORCHESTRATION_DAEMON_TOKEN=$(openssl rand -hex 16) make daemon-run
# note: if your kentra repos are NOT under ~/code, pass CODE_ROOT=/your/path make daemon-run
export ORCHESTRATION_DAEMON_TOKEN=$(docker exec agent-orchestration-daemon printenv ORCHESTRATION_DAEMON_TOKEN)
open http://localhost:8765
```

## 5. Fire the hermetic run
```bash
curl -s -X POST http://localhost:8765/launch \
  -H "Authorization: Bearer $ORCHESTRATION_DAEMON_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "/Users/jony/code/kentra/agent-orchestration",
    "change_id": "stub-demo",
    "box": {"enabled": false},
    "conductor": {
      "workflow": "workflows/execute-change.yaml",
      "provider": "stub",
      "plan_fixture_path": "/Users/jony/code/kentra/agent-orchestration/tests/fixtures/execute_change_2_milestones.json",
      "env": {"CONDUCTOR_STUB_SCRIPT": "/Users/jony/code/kentra/agent-orchestration/tests/fixtures/execute_change_2_milestones.stub.json"}
    }
  }' | python3 -m json.tool
```
The daemon forces `web=True` and allocates a `42000`-range port. The `report`
carries the dashboard URL. Expect on the index page: `stub-demo` → `running` →
`dead: success`. If it escalates or dies, read `orchestration status stub-demo`
(or the raw registry file, or the run's `conductor.stderr.log` under the worktree's
`.conductor-tmp/`) and adjust the stub script — this is normal iteration.

`wait: true` is the default, so the curl blocks until the run finishes; the two
milestones are near-instant in stub mode. If you'd rather watch it live, add
`"wait": false` to the payload and refresh the dashboard.

### Cleanup between attempts
Each launch reuses the same worktree/branch for a given `change_id` (idempotent),
but to start clean: `git -C <repo> worktree remove --force <repo>/.worktrees/stub-demo`
and `git -C <repo> branch -D change/stub-demo`, and delete
`~/.agent-orchestration/runs/agent-orchestration--stub-demo.json`.

---

## 6. Diagnostics cheat-sheet
- `docker logs --tail 40 agent-orchestration-daemon` — daemon tracebacks (e.g. the
  §2 Bug-1 500 shows up here).
- `GET /runs` and `GET /` are **not** token-gated (that's why the browser loads);
  only `POST /launch` needs the Bearer token.
- Registry files: `~/.agent-orchestration/runs/<slug>--<change_id>.json` (facts
  only; state is derived on read, never stored).
- Per-run logs: `<worktree>/.conductor-tmp/conductor.stderr.log` is the live UI;
  `conductor.stdout.log` is EMPTY until the run finishes (final JSON only) — by
  contract, not a bug.

## 7. Environment notes for the host (differs from the box!)
- The in-box venv contract (`UV_PROJECT_ENVIRONMENT=/home/agent/venv-agent-orchestration`)
  is **box-only** — ignore it on the host. For the stub demo you mostly need
  `docker` + `curl` + `python3`; no module install required.
- If you DO need to run the module/tests on the host, use your own
  `UV_PROJECT_ENVIRONMENT` pointing at a NON-bind-mounted path — never `uv sync`
  against the repo's default `.venv` (it is shared with boxes and corrupts under
  concurrent host+container uv; see memory `conductor-shared-venv-thrash`).
- `lifecycle` binary: the daemon image builds its own; on the host, build from the
  pinned spec-lifecycle commit (`go install github.com/kentra-io/spec-lifecycle/cmd/lifecycle@4d1f002755ac`)
  only if you run the *production* (non-stub) plan-resolution path.

## 8. After the stub run is green — the user's remaining host-side steps
(from the plan's handoff; not blockers for the smoke)
1. First **real** live smoke: an actual approved spec-lifecycle change with
   `box.enabled: true` (real agents, real cost).
2. Inject `ORCHESTRATION_DAEMON_URL` + `ORCHESTRATION_DAEMON_TOKEN` into consuming
   projects' claudebox `config.yaml` `env:` so in-box sessions can launch.
3. One-line catalog PR in `kentra-io/kentra-agentic-plugins` listing this plugin.
4. Merge PR #8.
5. The **github-mirror** change (start/finish/death comments, milestone checklist,
   `run-died` label, close-on-archive, `/resume` through the daemon — currently
   `501`) is a **separate future plan**, out of scope here.

## 9. Pointers
- Design: `docs/observability-design.md`. Plan: `docs/plans/2026-07-19-observability-core.md`.
- Skills (consumer runbooks): `skills/orchestration-launch/SKILL.md`,
  `skills/orchestration-monitor/SKILL.md`.
- Memory: `observability-leg-design` (shipped state), `orchestration-box-auth-expiry`,
  `conductor-shared-venv-thrash`, `claudebox-no-tmp-writes`.
