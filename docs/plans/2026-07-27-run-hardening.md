# Run Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make orchestrated runs bounded (stall watchdog), correct (gates run in-box on the agents' toolchain), measurable (telemetry survives worktree cleanup), and cheap (Sonnet implements / Opus verifies; the verifier stops re-running the suite the gates just ran).

**Architecture:** Five independent hardening levers found by the 2026-07-24 run-duration forensics, landed as one change across two repos. The **conductor fork** (`kentra-io/conductor`, local checkout `/Users/jony/code/conductor-kentra-patches`) gets the stall watchdog + partial-message liveness in `ClaudeboxProvider`. The **agent-orchestration** repo (`/Users/jony/code/kentra/harness/agent-orchestration`) gets in-box gate execution, telemetry relocation to the run registry, model pins, persona edits, and docs — plus a pin bump to consume the new fork SHA.

**Tech Stack:** Python 3.12 (uv), asyncio, pytest (+pytest-asyncio in the fork), Jinja-templated Conductor workflow YAML, claudebox `cb` CLI.

**Background facts the engineer needs (verified against source 2026-07-27):**

- Each LLM step is ONE subprocess: `cb exec --workdir <worktree> <box> claude -p <prompt> --agent <role> --model <m> --permission-mode bypassPermissions --output-format stream-json --verbose`. The provider tails stdout line-by-line (`_read_loop` in `src/conductor/providers/claudebox.py`). There is a `max_session_seconds` hard-cap mechanism but it is unset everywhere; there is NO inactivity bound — the 007 run hung silently for 46.7 min, the 001 run for 245 min.
- `stream-json` emits events per completed turn. Measured legit inter-turn silences reach ~4.9 min (long Opus thinking turns), so the watchdog threshold defaults to 10 min. `--include-partial-messages` makes the CLI emit `stream_event` delta lines during generation; any stdout line resets the watchdog, so the flag makes liveness token-granular. `_process_line` no-ops on unknown JSON event types (forward-compatible), so no parsing changes are needed.
- `workflows/milestone.yaml` steps `implementer`/`verifier`/`orchestrator` carry `retry: {max_attempts: 3, retry_on: [provider_error, timeout]}` — a retryable `ProviderError` is retried at the step level and does NOT burn a ladder attempt (the ladder is the separate `counter` step). This is exactly the wanted "watchdog restart is env-suspect" semantics, already built.
- Gates (`orchestration.harness.gates`, composing `l1_acceptance` + `l2_healthcheck` + `diff_paths` + `deviation_check`) run as host-side `python3 -m` script steps at the worktree — a different toolchain than the box the agents build in (caused bug #30). The worktree is bind-mounted into the box at the same absolute path, so wrapping the L1/L2 *command* in `cb exec --workdir <worktree> <box> bash -lc <cmd>` runs it on the agents' toolchain with their warm caches. `diff_paths`/`deviation_check` are pure git/file reads and stay host-side.
- All run telemetry (events.jsonl via checkpoint env, checkpoints, `conductor.stdout.log`/`conductor.stderr.log`, plan.json) lands under `tmpdir`, which defaults to `<worktree>/.conductor-tmp` (`orchestration/launch/change.py:621`) and dies with worktree cleanup — 001's 8 hours are forensically gone. Only ONE site derives that default; every consumer reads the registry entry's `tmpdir` field. Relocating the default to `~/.agent-orchestration/runs/<slug>--<change_id>/conductor/` makes all telemetry durable by construction.
- `workflows/milestone.yaml` hard-pins `model: opus` on implementer (~line 226), verifier (~line 294), orchestrator (~line 462). Decision 2026-07-27: implementer → `sonnet`; verifier and orchestrator stay `opus`.
- The verifier persona (`personas/verifier.md`) instructs re-running L1 AND the full repo suite per milestone (~3 min Opus + suite runtime, duplicating the gates). Diet: consume the gates report, targeted spot-checks only.
- ao pins the fork in `pyproject.toml` `[tool.uv.sources]`: `conductor-cli = { git = "https://github.com/kentra-io/conductor.git", rev = "d0e04647fb75f02e20076b7c1f12f820065e4879" }`.

---

## Task 1: Stall watchdog in ClaudeboxProvider (conductor fork)

**Repo/branch setup (once, for Tasks 1–4):**

```bash
cd /Users/jony/code/conductor-kentra-patches
git fetch kentra
git checkout -b feat/claudebox-run-hardening kentra/main
```

**Files:**
- Modify: `src/conductor/providers/claudebox.py`
- Test: `tests/test_providers/test_claudebox.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers/test_claudebox.py` (reuse the file's existing `_make_agent` helper and mirror the `execute(...)` call shape used by `test_parses_terminal_result_and_usage` — same `agent=`/`prompt=`/`context=` arguments; adapt kwargs if that test passes them differently):

```python
class TestStallWatchdog:
    @pytest.mark.asyncio
    async def test_stall_kills_subprocess_and_raises_retryable(
        self, tmp_path: Path
    ) -> None:
        """No stdout for longer than the threshold -> retryable ProviderError."""
        script = tmp_path / "cb"
        script.write_text(
            "#!/bin/bash\n"
            'echo \'{"type":"system","subtype":"init","session_id":"s1","model":"m"}\'\n'
            "sleep 30\n"
        )
        script.chmod(0o755)
        provider = ClaudeboxProvider(cb_binary=str(script), stall_timeout_seconds=0.5)
        agent = _make_agent()
        with pytest.raises(ProviderError, match="stall") as exc_info:
            await provider.execute(
                agent=agent, prompt="p", context={"box": "b", "worktree": str(tmp_path)}
            )
        assert exc_info.value.is_retryable is True

    def test_zero_threshold_disables_watchdog(self) -> None:
        provider = ClaudeboxProvider(cb_binary="cb", stall_timeout_seconds=0)
        assert provider._stall_timeout is None

    def test_env_var_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONDUCTOR_CLAUDEBOX_STALL_SECONDS", "120")
        provider = ClaudeboxProvider(cb_binary="cb")
        assert provider._stall_timeout == 120.0

    def test_builtin_default_is_600(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CONDUCTOR_CLAUDEBOX_STALL_SECONDS", raising=False)
        provider = ClaudeboxProvider(cb_binary="cb")
        assert provider._stall_timeout == 600.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers/test_claudebox.py::TestStallWatchdog -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'stall_timeout_seconds'`

- [ ] **Step 3: Implement the watchdog**

In `src/conductor/providers/claudebox.py`:

(a) Module constants, next to `_DEFAULT_PARSE_RECOVERY_ATTEMPTS` (~line 110):

```python
# Stall watchdog: kill-and-raise-retryable when the `claude` subprocess
# produces NO stdout line for this long. stream-json emits per-turn; with
# `--include-partial-messages` (always passed, see _build_argv) it emits
# token-granular `stream_event` deltas, so silence past the threshold means
# a genuinely dead session (expired auth, wedged exec, network stall) -- the
# class of failure that hung real runs for 46-245 minutes unbounded.
# Retryable=True on purpose: workflow-level `retry: [provider_error]`
# restarts the step WITHOUT burning an escalation-ladder attempt.
_DEFAULT_STALL_SECONDS: Final[float] = 600.0
_STALL_ENV_VAR: Final[str] = "CONDUCTOR_CLAUDEBOX_STALL_SECONDS"


class _StallTimeoutError(Exception):
    """Internal: _read_loop saw no stdout line within the stall threshold."""

    def __init__(self, threshold: float) -> None:
        super().__init__(f"no stream output for {threshold:.0f}s")
        self.threshold = threshold
```

(b) Constructor (`__init__`, ~line 454): add the parameter after `cb_binary`:

```python
        stall_timeout_seconds: float | None = None,
```

and in the body, next to where `cb_binary` is resolved:

```python
        if stall_timeout_seconds is None:
            env_val = os.environ.get(_STALL_ENV_VAR)
            stall_timeout_seconds = float(env_val) if env_val else _DEFAULT_STALL_SECONDS
        # <= 0 disables the watchdog entirely.
        self._stall_timeout: float | None = (
            stall_timeout_seconds if stall_timeout_seconds > 0 else None
        )
```

Docstring addition for the arg (match the existing arg-doc style):

```
            stall_timeout_seconds: Inactivity watchdog -- if the claude
                subprocess emits no stdout line for this many seconds the
                subprocess is terminated and a retryable ProviderError is
                raised. Defaults to the CONDUCTOR_CLAUDEBOX_STALL_SECONDS
                env var, then 600. Values <= 0 disable the watchdog.
                Distinct from `max_session_seconds` (total wall-clock cap):
                the watchdog bounds *silence*, not total duration.
```

(c) `_read_loop` (~line 847): add a `stall_timeout: float | None` parameter (after `event_callback`) and replace the plain `asyncio.wait` call:

```python
            try:
                done, pending = await asyncio.wait(
                    waiters, timeout=stall_timeout, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                for t in waiters:
                    t.cancel()
                raise

            if not done:
                # Stall: neither a stdout line nor an interrupt within the
                # threshold. Cancel waiters and let _run_once terminate.
                for t in pending:
                    t.cancel()
                raise _StallTimeoutError(stall_timeout)  # type: ignore[arg-type]
```

(the existing `for t in pending: t.cancel()` after the try block stays for the normal path).

(d) `_run_once` (~line 770): pass the threshold at both `_read_loop` call sites and handle the stall — insert a new `except` arm between the existing `except TimeoutError` and `except asyncio.CancelledError`:

```python
        try:
            if timeout is not None:
                outcome = await asyncio.wait_for(
                    self._read_loop(
                        process, interrupt_signal, event_callback, self._stall_timeout
                    ),
                    timeout=timeout,
                )
            else:
                outcome = await self._read_loop(
                    process, interrupt_signal, event_callback, self._stall_timeout
                )
        except TimeoutError:
            await self._terminate(process)
            stderr_task.cancel()
            raise ProviderError(
                f"claudebox agent exceeded max_session_seconds={timeout:.0f}s",
                is_retryable=False,
            ) from None
        except _StallTimeoutError as exc:
            await self._terminate(process)
            stderr_task.cancel()
            raise ProviderError(
                f"claudebox agent stalled: no stream-json output for "
                f"{exc.threshold:.0f}s (stall watchdog; configure via "
                f"provider stall_timeout_seconds or {_STALL_ENV_VAR}; <=0 disables)",
                is_retryable=True,
            ) from None
        except asyncio.CancelledError:
            await self._terminate(process)
            stderr_task.cancel()
            raise
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_providers/test_claudebox.py -v`
Expected: ALL pass (new `TestStallWatchdog` plus every pre-existing test — the default threshold of 600s is far above any fixture's runtime, so nothing else may regress).

- [ ] **Step 5: Commit**

```bash
git add src/conductor/providers/claudebox.py tests/test_providers/test_claudebox.py
git commit -m "feat(claudebox): stall watchdog -- kill + retryable ProviderError on stream silence"
```

---

## Task 2: Token-granular liveness via --include-partial-messages (conductor fork)

**Files:**
- Modify: `src/conductor/providers/claudebox.py:711-735` (`_build_argv`)
- Test: `tests/test_providers/test_claudebox.py`

- [ ] **Step 1: Update the argv-shape test + add a stream_event tolerance test**

`test_argv_shape_matches_spec` (~line 265) asserts the exact argv. Add `"--include-partial-messages"` to its expected list (immediately after `"--verbose"`). Then append:

```python
class TestPartialMessages:
    @pytest.mark.asyncio
    async def test_stream_event_lines_are_ignored_not_noise(
        self, tmp_path: Path
    ) -> None:
        """`stream_event` delta lines (from --include-partial-messages) must be
        silently skipped: no agent_message events, no noise recording, and the
        terminal result still parses."""
        script = tmp_path / "cb"
        script.write_text(
            "#!/bin/bash\n"
            'echo \'{"type":"system","subtype":"init","session_id":"s1","model":"m"}\'\n'
            'echo \'{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"chunk"}}}\'\n'
            'echo \'{"type":"result","is_error":false,"result":"done","session_id":"s1","usage":{"input_tokens":1,"output_tokens":1}}\'\n'
        )
        script.chmod(0o755)
        provider = ClaudeboxProvider(cb_binary=str(script))
        agent = _make_agent()
        events: list[tuple[str, dict]] = []
        output = await provider.execute(
            agent=agent,
            prompt="p",
            context={"box": "b", "worktree": str(tmp_path)},
            event_callback=lambda t, d: events.append((t, d)),
        )
        assert output.raw_response["result"] == "done"
        assert not any(t == "agent_message" for t, _ in events)
```

(If `execute()` takes the event callback under a different keyword in this file's existing tests, match that spelling.)

- [ ] **Step 2: Run to verify the argv test fails**

Run: `uv run pytest tests/test_providers/test_claudebox.py -k "argv_shape or PartialMessages" -v`
Expected: `test_argv_shape_matches_spec` FAILS (argv missing the new flag); the tolerance test may already pass (unknown types are no-ops) — that is fine.

- [ ] **Step 3: Add the flag in `_build_argv`**

```python
        argv += [
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
            "--verbose",
            # Token-granular liveness for the stall watchdog: emits
            # `stream_event` delta lines DURING generation, so a long
            # thinking turn resets the watchdog instead of tripping it
            # (measured legit inter-turn silences reach ~5 min without it).
            # _process_line ignores the unknown type by design.
            "--include-partial-messages",
        ]
```

- [ ] **Step 4: Run the full provider test file**

Run: `uv run pytest tests/test_providers/test_claudebox.py -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add src/conductor/providers/claudebox.py tests/test_providers/test_claudebox.py
git commit -m "feat(claudebox): --include-partial-messages for token-granular stall liveness"
```

---

## Task 3: `stall_timeout_seconds` in ProviderSettings + factory forwarding (conductor fork)

**Files:**
- Modify: `src/conductor/config/schema.py` (~line 1637, `class ProviderSettings`)
- Modify: `src/conductor/providers/factory.py` (~line 212, `case "claudebox":`)
- Test: `tests/test_config/` (add to the file that already tests `ProviderSettings`; locate with `grep -rln ProviderSettings tests/test_config/`) and `tests/test_providers/test_claudebox.py` or the factory's test file (`grep -rln 'case .claudebox' -r tests/` → the factory tests live where `create_provider`/factory is tested; locate with `grep -rln claudebox tests/ | grep -v test_claudebox`)

- [ ] **Step 1: Write the failing tests**

In the ProviderSettings config test file:

```python
def test_claudebox_stall_timeout_seconds_field() -> None:
    settings = ProviderSettings(name="claudebox", stall_timeout_seconds=120)
    assert settings.stall_timeout_seconds == 120.0
```

Factory forwarding test — add to `tests/test_providers/test_claudebox.py` (the factory entry point is `conductor.providers.factory.create_provider`, signature verified: `create_provider(provider_type=..., validate=..., provider_settings=...)`):

```python
@pytest.mark.asyncio
async def test_factory_forwards_stall_timeout_to_claudebox_provider() -> None:
    from conductor.config.schema import ProviderSettings
    from conductor.providers.factory import create_provider

    settings = ProviderSettings(name="claudebox", stall_timeout_seconds=42)
    provider = await create_provider(
        provider_type="claudebox", validate=False, provider_settings=settings
    )
    assert provider._stall_timeout == 42.0
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL — `pydantic_core.ValidationError: Extra inputs are not permitted` (the model is `extra="forbid"`).

- [ ] **Step 3: Implement**

`schema.py` — add after the `auth_token` field:

```python
    stall_timeout_seconds: float | None = None
    """Stall-watchdog threshold in seconds. Claudebox-only.

    When the spawned ``claude`` subprocess emits no stdout line for this
    long, it is terminated and a *retryable* ProviderError is raised (a
    workflow ``retry: [provider_error]`` then restarts the step). Defaults
    to the ``CONDUCTOR_CLAUDEBOX_STALL_SECONDS`` env var, then 600. Values
    <= 0 disable the watchdog. See ClaudeboxProvider for details.
    """
```

Check for a `model_validator` on `ProviderSettings` that restricts non-`name` fields to `name: copilot` (`grep -n model_validator src/conductor/config/schema.py` around the class). If one exists, add `stall_timeout_seconds` to its claudebox-allowed set the same way `auth_token` is allowed.

`factory.py` — in the `case "claudebox":` arm:

```python
            claudebox_auth_token: str | None = None
            claudebox_base_url: str | None = None
            claudebox_stall: float | None = None
            if provider_settings is not None and provider_settings.name == "claudebox":
                if provider_settings.auth_token is not None:
                    claudebox_auth_token = provider_settings.auth_token.get_secret_value()
                claudebox_base_url = provider_settings.base_url
                claudebox_stall = provider_settings.stall_timeout_seconds
            provider = ClaudeboxProvider(
                model=default_model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                max_agent_iterations=max_agent_iterations,
                max_session_seconds=max_session_seconds,
                auth_token=claudebox_auth_token,
                base_url=claudebox_base_url,
                stall_timeout_seconds=claudebox_stall,
            )
```

- [ ] **Step 4: Run config + provider + factory tests**

Run: `uv run pytest tests/test_config/ tests/test_providers/ -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add src/conductor/config/schema.py src/conductor/providers/factory.py tests/
git commit -m "feat(claudebox): stall_timeout_seconds provider setting, forwarded by the factory"
```

---

## Task 4: Fork docs, full suite, push, merge, record SHA (conductor fork)

**Files:**
- Modify: the fork's provider documentation — `grep -rln claudebox docs/ README.md` and extend whichever file documents the claudebox provider's configuration (if none does, add a "Stall watchdog" subsection to the module docstring of `src/conductor/providers/claudebox.py` only).

- [ ] **Step 1: Document the watchdog**

Wherever claudebox provider config is documented, add:

```markdown
### Stall watchdog (claudebox)

The claudebox provider kills a `claude` subprocess that produces no stdout
for `stall_timeout_seconds` (default **600**; env override
`CONDUCTOR_CLAUDEBOX_STALL_SECONDS`; `<= 0` disables) and raises a
**retryable** ProviderError — a workflow `retry: [provider_error]` restarts
the step without consuming an escalation-ladder attempt. Liveness is
token-granular: the invocation always passes `--include-partial-messages`,
so long thinking turns emit delta lines and do not trip the watchdog.
This is distinct from `max_session_seconds`, which caps *total* duration.

```yaml
runtime:
  provider:
    name: claudebox
    stall_timeout_seconds: 600
```
```

- [ ] **Step 2: Run the full fork suite**

Run: `uv run pytest -x -q`
Expected: PASS (same result as `kentra/main` baseline plus the new tests; if a pre-existing test fails, verify it also fails on `kentra/main` before touching it — report, don't fix unrelated breakage in this branch).

- [ ] **Step 3: Commit docs, push, open PR**

```bash
git add -A
git commit -m "docs(claudebox): document stall watchdog config"
git push kentra feat/claudebox-run-hardening
gh pr create -R kentra-io/conductor --base main \
  --title "claudebox: stall watchdog + token-granular liveness + provider setting" \
  --body "Stall watchdog (retryable ProviderError on stream silence, default 600s, CONDUCTOR_CLAUDEBOX_STALL_SECONDS override), --include-partial-messages for token-granular liveness, stall_timeout_seconds ProviderSettings field + factory forwarding. Forensics: unbounded silent hangs cost 46.7 and 245 min in real runs.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 4: Merge and record the SHA**

After CI is green, merge the PR (squash or merge per repo convention), then:

```bash
git fetch kentra && git rev-parse kentra/main
```

Record the printed SHA — Task 5 needs it. **STOP here if the PR needs human review** — do not self-merge if the repo/branch protection expects review.

---

## Task 5: Bump the conductor pin in agent-orchestration

**Repo/branch setup (once, for Tasks 5–12):**

```bash
cd /Users/jony/code/kentra/harness/agent-orchestration
git checkout main && git pull && git checkout -b feat/run-hardening
```

**Files:**
- Modify: `pyproject.toml:23` (`[tool.uv.sources]` `conductor-cli` `rev`)
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1: Update the rev to the Task-4 SHA**

In `pyproject.toml` line 23, replace `rev = "d0e04647fb75f02e20076b7c1f12f820065e4879"` with the new fork `main` SHA from Task 4.

- [ ] **Step 2: Re-lock and sync**

Run: `uv lock && uv sync --all-extras`
Expected: lockfile updates the conductor-cli pin; sync succeeds.

- [ ] **Step 3: Smoke the suite against the new engine**

Run: `uv run pytest -x -q`
Expected: PASS (hermetic tier; live/m6 tests self-skip).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: bump conductor fork to <sha> (stall watchdog + partial-message liveness)"
```

---

## Task 6: In-box execution wrapper for L1/L2 (agent-orchestration)

**Files:**
- Modify: `orchestration/harness/common.py`
- Modify: `orchestration/harness/l1_acceptance.py`
- Modify: `orchestration/harness/l2_healthcheck.py`
- Modify: `orchestration/harness/README.md` (calling convention: new optional payload keys)
- Test: `tests/test_harness_box_exec.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_harness_box_exec.py`:

```python
"""In-box gate execution: optional `box`/`box_workdir` payload keys wrap the
L1/L2 command in `cb exec ... bash -lc <cmd>` so deterministic checks run on
the SAME toolchain (and warm caches) as the agents, not the daemon host."""

import shlex

from orchestration.harness import l1_acceptance, l2_healthcheck
from orchestration.harness.common import wrap_in_box


class TestWrapInBox:
    def test_wraps_with_workdir(self) -> None:
        wrapped = wrap_in_box("go test ./...", "mybox", "/w/tree")
        assert wrapped == (
            "cb exec --workdir /w/tree mybox bash -lc " + shlex.quote("go test ./...")
        )

    def test_wraps_without_workdir(self) -> None:
        assert wrap_in_box("exit 0", "mybox") == "cb exec mybox bash -lc 'exit 0'"

    def test_quotes_hostile_command(self) -> None:
        wrapped = wrap_in_box("echo 'a b' && ls; true", "box1", "/p")
        # The inner command must survive as ONE bash -lc argument.
        assert shlex.split(wrapped)[-1] == "echo 'a b' && ls; true"


class TestL1BoxWiring:
    def test_l1_wraps_command_when_box_present(self, monkeypatch) -> None:
        seen: dict = {}

        def fake_run(command, cwd=".", timeout=600, env_overrides=None):
            seen["command"] = command
            return 0, "ok", ""

        monkeypatch.setattr(l1_acceptance, "run_command", fake_run)
        verdict = l1_acceptance.check(
            {"command": "go test ./...", "box": "mybox", "box_workdir": "/w/tree"}
        )
        assert verdict["pass"] is True
        assert seen["command"].startswith("cb exec --workdir /w/tree mybox bash -lc ")
        # The report surfaces the wrapped command for observability.
        assert verdict["command"] == seen["command"]

    def test_l1_unwrapped_without_box(self, monkeypatch) -> None:
        seen: dict = {}

        def fake_run(command, cwd=".", timeout=600, env_overrides=None):
            seen["command"] = command
            return 0, "ok", ""

        monkeypatch.setattr(l1_acceptance, "run_command", fake_run)
        l1_acceptance.check({"command": "go test ./..."})
        assert seen["command"] == "go test ./..."


class TestL2BoxWiring:
    def test_l2_wraps_every_command_when_box_present(self, monkeypatch) -> None:
        seen: list = []

        def fake_run(command, cwd=".", timeout=600, env_overrides=None):
            seen.append(command)
            return 0, "ok", ""

        monkeypatch.setattr(l2_healthcheck, "run_command", fake_run)
        verdict = l2_healthcheck.check(
            {"commands": ["make build", "make test"], "box": "b1", "box_workdir": "/w"}
        )
        assert verdict["pass"] is True
        assert all(c.startswith("cb exec --workdir /w b1 bash -lc ") for c in seen)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_harness_box_exec.py -v`
Expected: FAIL — `ImportError: cannot import name 'wrap_in_box'`.

- [ ] **Step 3: Implement `wrap_in_box` in `common.py`**

Add near `run_command` (and add `import shlex` at the top of the file):

```python
def wrap_in_box(
    command: str,
    box: str,
    workdir: str | None = None,
    cb_binary: str = "cb",
) -> str:
    """Wrap a shell command string to execute inside a claudebox box.

    Deterministic gate commands (L1/L2) must run on the SAME toolchain the
    agents build with -- the box, not the daemon host (host/box toolchain
    split caused #30). The worktree is bind-mounted into the box at the same
    absolute path, so `--workdir <worktree>` lands in the same tree. The
    whole inner command is shell-quoted into ONE `bash -lc` argument.
    """
    argv = [cb_binary, "exec"]
    if workdir:
        argv += ["--workdir", workdir]
    argv += [box, "bash", "-lc", command]
    return " ".join(shlex.quote(a) for a in argv)
```

- [ ] **Step 4: Wire it into `l1_acceptance.check`**

In `l1_acceptance.py`, import `wrap_in_box` from `orchestration.harness.common` alongside the existing imports, then at the top of `check(...)` where `command` is read from the payload:

```python
    command = payload.get("command")
    if not command or not isinstance(command, str):
        raise HarnessInputError("'command' (non-empty string) is required")
    box = payload.get("box")
    if box:
        command = wrap_in_box(
            command, box, payload.get("box_workdir"), payload.get("cb_binary", "cb")
        )
```

(keep the existing validation error text if it differs — only ADD the box wrap after validation; the rest of `check` uses the possibly-wrapped `command`, including the `"command"` field of the emitted verdict). Update the module docstring's Input JSON block:

```
      "box": str,               # optional: claudebox box id -- run the command
                                #   in-box via `cb exec ... bash -lc <command>`
      "box_workdir": str,       # optional: --workdir for cb exec (the worktree)
      "cb_binary": str,         # optional, default "cb"
```

- [ ] **Step 5: Same wiring in `l2_healthcheck.check`**

In `l2_healthcheck.py` `check(...)`, after `commands` validation:

```python
    box = payload.get("box")
    box_workdir = payload.get("box_workdir")
    cb_binary = payload.get("cb_binary", "cb")
```

and inside the per-command loop, before `run_command` is called:

```python
        run_target = (
            wrap_in_box(command, box, box_workdir, cb_binary) if box else command
        )
```

passing `run_target` to `run_command` and reporting `"command": run_target` in the per-command result. Update its docstring's Input JSON the same way as L1's.

- [ ] **Step 6: Run the new tests + the whole harness suite**

Run: `uv run pytest tests/test_harness_box_exec.py tests/ -k harness -v`
Expected: ALL pass (existing harness tests never pass `box`, so behavior is unchanged for them).

- [ ] **Step 7: Document in `orchestration/harness/README.md`**

Add to the shared calling convention section:

```markdown
### In-box execution (`box` / `box_workdir`)

`l1_acceptance` and `l2_healthcheck` accept optional `box`, `box_workdir`,
and `cb_binary` payload keys. When `box` is non-empty the command is wrapped
as `cb exec --workdir <box_workdir> <box> bash -lc <command>` so it executes
on the agents' toolchain with their warm build caches (the host/box
toolchain split caused #30). `diff_paths` / `deviation_check` are pure
git/file reads and always run host-side. Omitting `box` (hermetic/stub tier)
keeps today's host-side execution.
```

- [ ] **Step 8: Commit**

```bash
git add orchestration/harness/ tests/test_harness_box_exec.py
git commit -m "feat(harness): optional in-box execution for L1/L2 via cb exec wrapper"
```

---

## Task 7: Workflow wiring — gates and full_healthcheck run in-box (agent-orchestration)

**Files:**
- Modify: `workflows/milestone.yaml` (the `gates` step's `stdin`, ~line 275)
- Modify: `workflows/execute-change.yaml` (the `full_healthcheck` step's `stdin`, ~line 292)
- Test: existing stub-tier suite (`uv run pytest -q`) — the stub tier passes `box=""` so payloads must stay box-free there

- [ ] **Step 1: Rewrite the `gates` stdin in `milestone.yaml`**

Replace the current `stdin` block of the `gates` step:

```yaml
    stdin: >-
      {% set _contract_check = workflow.input.contract_check | trim %}
      {% set _static_check = workflow.input.gates_l1_command | trim %}
      {% set _check = _contract_check if (_contract_check and
      _contract_check != 'none') else _static_check %}
      {% set _box = workflow.input.box | trim %}
      {% set _l1 = ({"command": _check, "box": _box,
      "box_workdir": workflow.input.worktree} if _box
      else {"command": _check}) %}
      {{ ({"l1": _l1} if (_check and _check != 'none') else {}) | tojson }}
```

Also update the step's `description` comment: append a sentence — `When workflow.input.box is non-empty the L1 command executes IN-BOX (cb exec) on the agents' toolchain; the stub tier (box="") keeps host-side execution.`

- [ ] **Step 2: Rewrite the `full_healthcheck` stdin in `execute-change.yaml`**

Replace:

```yaml
    stdin: '{{ {"commands": [workflow.input.healthcheck_command]} | tojson }}'
```

with:

```yaml
    stdin: >-
      {% set _box = workflow.input.box | trim %}
      {{ ({"commands": [workflow.input.healthcheck_command], "box": _box,
      "box_workdir": workflow.input.worktree} if _box
      else {"commands": [workflow.input.healthcheck_command]}) | tojson }}
```

(Verify `execute-change.yaml` declares `box`/`worktree` workflow inputs — it forwards both into `milestone_step`, so they exist; if the input names differ, match them.)

- [ ] **Step 3: Run the full hermetic suite**

Run: `uv run pytest -q`
Expected: PASS — in the stub tier `box` defaults to `""`, both templates take the box-free branch, payloads are byte-identical to before. If a workflow-template test snapshots the stdin template text, update the snapshot.

- [ ] **Step 4: Commit**

```bash
git add workflows/milestone.yaml workflows/execute-change.yaml
git commit -m "feat(workflows): L1 gate + full healthcheck execute in-box when a box is wired"
```

---

## Task 8: Telemetry survives the worktree — relocate the default tmpdir (agent-orchestration)

**Files:**
- Modify: `orchestration/obs/registry.py` (new `run_dir` helper)
- Modify: `orchestration/launch/change.py:621` (default tmpdir) + docstring lines ~16–19 and the payload comment at line ~63
- Modify: `README.md` (telemetry location — done in Task 11)
- Test: `tests/test_obs_registry.py` or wherever registry helpers are tested (`grep -rln registry_dir tests/`), plus the launch tests that cover tmpdir (`grep -rln conductor-tmp tests/`)

- [ ] **Step 1: Write the failing tests**

In the registry test file:

```python
def test_run_dir_is_sibling_of_entry_and_created(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATION_REGISTRY_DIR", str(tmp_path))
    d = registry.run_dir("myrepo", "007-yaml")
    assert d == tmp_path / "myrepo--007-yaml"
    assert d.is_dir()
```

Then the launch-side default. Procedure (the launch tests have an established testbed — do not invent a new one):

1. Run `grep -rln 'conductor-tmp\|tmpdir' tests/` — this finds every test asserting the current default (`<worktree>/.conductor-tmp`) and the tests proving an explicit `conductor.tmpdir` override wins.
2. In the file that asserts the default, copy its existing launch-invocation test verbatim, rename it `test_default_tmpdir_is_registry_run_dir`, set `ORCHESTRATION_REGISTRY_DIR` to a `tmp_path` subdir via `monkeypatch.setenv`, pass NO `conductor.tmpdir` in the payload, and change the assertion to:

```python
    assert report["tmpdir"] == str(
        registry_base / f"{repo_dir.name}--{change_id}" / "conductor"
    )
```

where `registry_base` is the path you set in `ORCHESTRATION_REGISTRY_DIR`, `repo_dir` is the testbed's repo path, and `change_id` is the testbed's change id (all three already exist as variables/fixtures in the copied test).
3. Keep the explicit-override test unchanged — it must still pass as-is.

- [ ] **Step 2: Run to verify they fail**

Expected: `AttributeError: module ... has no attribute 'run_dir'` / tmpdir assertion mismatch (`<worktree>/.conductor-tmp`).

- [ ] **Step 3: Implement**

`registry.py`, after `entry_path`:

```python
def run_dir(slug: str, change_id: str) -> Path:
    """Durable per-change run directory (telemetry home), sibling of the entry.

    Conductor's tmpdir (events.jsonl, checkpoints, conductor.std{out,err}.log,
    plan.json) defaults HERE rather than <worktree>/.conductor-tmp so run
    telemetry survives worktree cleanup -- the 001 run's 8 hours left zero
    analyzable artifacts because everything died with the worktree.
    """
    d = registry_dir() / f"{slug}--{change_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d
```

`change.py:621` — replace:

```python
    tmpdir = Path(conductor_cfg.get("tmpdir") or (worktree / ".conductor-tmp"))
```

with:

```python
    tmpdir = Path(
        conductor_cfg.get("tmpdir")
        or obs_registry.run_dir(obs_registry.repo_slug(repo_path), change_id) / "conductor"
    )
```

Update the module docstring (~lines 16–19) and the payload comment (~line 63): the default is now `~/.agent-orchestration/runs/<slug>--<change_id>/conductor` (per-change isolation now comes from the change-keyed registry dir; an explicit `tmpdir` still wins; telemetry survives worktree removal by construction). Note that everything downstream already reads the registry entry's `tmpdir` *field* (verified: `change.py:621` was the only derivation site; `obs/status.py` only lists `.conductor-tmp` in a skip-set, which stays harmless).

- [ ] **Step 4: Run the launch + obs + resume suites**

Run: `uv run pytest tests/ -k "registry or launch or resume or status or daemon" -v`
Expected: ALL pass. Any test asserting the old `<worktree>/.conductor-tmp` default gets updated to the new expectation (tests pinning an *explicit* tmpdir stay untouched).

- [ ] **Step 5: Commit**

```bash
git add orchestration/obs/registry.py orchestration/launch/change.py tests/
git commit -m "feat(obs): default conductor tmpdir to the registry run dir -- telemetry survives worktree cleanup"
```

---

## Task 9: Model split — Sonnet implements, Opus verifies (agent-orchestration)

**Files:**
- Modify: `workflows/milestone.yaml` (implementer step, ~line 226)
- Modify: `personas/implementer.md` (frontmatter `model:`)
- Test: `grep -rn '"opus"\|model.*opus' tests/` — update any test pinning the implementer's model

- [ ] **Step 1: Flip the pins**

`workflows/milestone.yaml` implementer step — replace:

```yaml
    model: opus # spec sec 6: Implementer = Opus, medium effort (effort in persona frontmatter)
```

with:

```yaml
    model: sonnet # 2026-07-27 decision (run-hardening): Sonnet implements, Opus
                  # verifies -- self-contained plans carry the context that made
                  # Opus necessary; the trust spine stays on the Opus verifier.
                  # (supersedes spec sec 6's Implementer = Opus)
```

`personas/implementer.md` frontmatter — replace `model: opus` with `model: sonnet` (the step's `--model` overrides it anyway; keep them consistent). Verifier and orchestrator stay `opus` — verify with:

Run: `grep -n 'model:' workflows/milestone.yaml personas/*.md`
Expected: implementer=sonnet (×2), verifier=opus (×2), orchestrator=opus (×2).

- [ ] **Step 2: Run the suite, fix model-pin assertions**

Run: `uv run pytest -q`
Expected: PASS after updating any test that asserted the implementer's `opus` pin.

- [ ] **Step 3: Commit**

```bash
git add workflows/milestone.yaml personas/implementer.md tests/
git commit -m "feat(cast): Sonnet implements, Opus verifies (2026-07-27 model-split decision)"
```

---

## Task 10: Verifier diet + env-failure reporting in personas (agent-orchestration)

**Files:**
- Modify: `personas/verifier.md`
- Modify: `personas/implementer.md`
- Modify: `personas/orchestrator.md`

These are prose contracts (no unit tests); the check is `grep` + reading the diff.

- [ ] **Step 1: Verifier diet — replace procedure step 2**

In `personas/verifier.md`, replace the entire step `2. **Objective gates (L1 + L2).** ...` (through `...not in prose.`) with:

```markdown
2. **Objective gates — consume the report, don't re-run the suite.** The
   deterministic gates step already ran this milestone's L1 acceptance
   command; its report (exit code, stdout/stderr tails) is in your prompt.
   Treat that report as ground truth for L1 — do not re-run the full test
   suite and do not wholesale re-run the L1 command. You MAY run *targeted*
   commands (one test file, one module's build) when the diff makes you
   suspect something the gates could not see — record each such command and
   its real output as evidence. The repo-wide suite/build/lint is the
   change-level healthcheck's job at run end, not yours per milestone. A
   test that only passes after a retry is **flaky → quarantine**, not a
   pass; do not let "green on the second run" mask a deterministic failure.
```

And in the `# What you are given` section, extend the ground-truth list to name the gates report: change `Only the ground truth: ...` to include `the deterministic gates report (L1 exit code + output, in your prompt),` before `the git **diff**`.

- [ ] **Step 2: Env-failure section — all three personas**

Append to `personas/implementer.md` (before any final section, matching the file's heading style):

```markdown
# Environment failures (report, don't fight)

If a command fails in a way that smells environmental rather than caused by
this milestone's code — authentication/token errors, `command not found` for
an expected toolchain binary, permission or mount errors, network
unreachable, disk full — do NOT retry around it, patch the environment, or
treat it as a code defect. Stop and report the failing command plus its
verbatim error in your `halt` field, prefixed `ENV:`. A misdiagnosed
environment failure burns the whole escalation ladder on retries that
cannot succeed.
```

Append to `personas/verifier.md`:

```markdown
# Environment failures (report, don't fight)

If a check fails in a way that smells environmental (auth/token errors,
`command not found`, permission/mount errors, network unreachable), do not
grade it as a code defect and do not retry around it. Report it in
`violations` prefixed `ENV:` with the verbatim error, FAIL the milestone,
and say in `notes` that the failure looks environmental so the orchestrator
and human triage it as such instead of burning attempts.
```

Append to `personas/orchestrator.md`:

```markdown
# Environment failures

If the verifier's or implementer's evidence contains `ENV:`-prefixed
failures (auth/token errors, missing toolchain binaries, mount/permission
errors, network), do not emit guidance that asks the implementer to work
around the environment. Say plainly in `guidance` that the failure looks
environmental and needs human attention; set `infeasible: true` only if the
milestone cannot pass in ANY environment as written.
```

- [ ] **Step 3: Sanity-check and commit**

Run: `grep -c 'ENV:' personas/implementer.md personas/verifier.md personas/orchestrator.md`
Expected: each ≥ 1.

```bash
git add personas/
git commit -m "feat(personas): verifier consumes gate report (no suite re-run); ENV-failure reporting contract"
```

---

## Task 11: README + docs — config visibility, telemetry home, cache facts (agent-orchestration)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Run hardening knobs" section to README.md**

Place it near the existing configuration/observability documentation:

```markdown
## Run hardening knobs (2026-07-27)

| Knob | Where | Default | Notes |
|---|---|---|---|
| Stall watchdog | `CONDUCTOR_CLAUDEBOX_STALL_SECONDS` env, or workflow `runtime.provider: {name: claudebox, stall_timeout_seconds: N}` | 600 s | Kills an LLM step whose stream goes silent, raises a *retryable* provider error — the step's `retry:` restarts it without burning a ladder attempt. `<= 0` disables. Liveness is token-granular (`--include-partial-messages`), so long thinking turns don't trip it. Tune once telemetry shows real silence distributions. |
| In-box gates | automatic when the run has a box | on | L1 acceptance + change-level healthcheck execute inside the run's claudebox (`cb exec … bash -lc`) — same toolchain and warm build caches as the agents. Stub tier (no box) stays host-side. |
| Telemetry home | `conductor.tmpdir` launch payload key | `~/.agent-orchestration/runs/<slug>--<change>/conductor/` | events.jsonl, checkpoints, conductor.std{out,err}.log, plan.json — survives worktree cleanup; analyze past runs from here. |
| Model split | `workflows/milestone.yaml` + persona frontmatter | implementer=sonnet, verifier=opus, orchestrator=opus | 2026-07-27 decision; self-contained plans carry the context, the Opus verifier keeps the trust spine. |

### Build-cache facts

Within a run, all milestones share ONE box — gradle/go/npm caches warm up
across milestones, and (with in-box gates) the gate commands reuse them.
Docker *image* pulls (e.g. Testcontainers) already share the host daemon's
image cache across all boxes and runs: in-box `docker` talks to the host
daemon through claudebox's socket proxy. Verify:
`cb exec <box> docker images` lists the same images as host `docker images`.
Cross-run *dependency* caches (fresh box per run) are accepted cold-start
cost for now — revisit with shared named volumes if telemetry shows it
matters.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: run-hardening knobs (stall watchdog, in-box gates, telemetry home, model split, cache facts)"
```

---

## Task 12: Full suite, PR, and post-merge wiring checklist

- [ ] **Step 1: Full hermetic suite, one last time**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/run-hardening
gh pr create --title "Run hardening: stall watchdog, in-box gates, durable telemetry, model split, verifier diet" \
  --body "Lands the 5 hardening levers from the 2026-07-24 duration forensics. Conductor fork counterpart merged as <fork-sha> (pin bumped here). Details in docs/plans/2026-07-27-run-hardening.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 3: Post-merge wiring (harness repo — human-visible checklist, do NOT skip silently)**

These follow the harness CLAUDE.md lockstep rule (every from-source box CLI pins an `ARG _REF`):

```
[ ] merge PR, note the new agent-orchestration main SHA
[ ] harness repo: git -C agent-orchestration checkout <sha>; bump ORCH_REF in .claudebox/Dockerfile to the same SHA (same commit)
[ ] rebuild the daemon image so the running daemon actually carries the new code (GHCR image predates this work); restart orch daemon
[ ] cb build for the harness box if in-box orch is used
[ ] verify freshness: compare /opt/uv/tools/agent-orchestration/*/direct_url.json against the pinned SHA
[ ] next live run: confirm telemetry lands in ~/.agent-orchestration/runs/<slug>--<change>/conductor/ and the watchdog config line appears in README
```

---

## Deliberately out of scope (tracked elsewhere)

- First-class `checkpoint: true` / plan-level healthcheck field — milestoned-plan-dag#1.
- Executor consumes mpd plans, `contract.check`/`criteria` forwarding, `paths: []` inversion — agent-orchestration#32 (post-007).
- spec-lifecycle skills rewrite (self-contained plans, sizing, checkpoint conventions) — next change after this one.
- Cross-run dependency-cache volumes, env-failure *classifier* (beyond persona reporting), judge miss-rate — parked issues.
