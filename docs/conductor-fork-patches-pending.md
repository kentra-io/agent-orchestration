# Conductor-fork patch record (all upstreamed)

Per constitution ADR-0001, provider fixes belong in the `kentra-io/conductor`
fork patch-set (branch `kentra-patches`), not as edits to the installed `.venv`.

**Status 2026-07-23: nothing pending.** Both fixes below are upstreamed in
fork commit `ab0ff4c` (branch `kentra-patches`) with regression tests, and the
pin in `pyproject.toml` / `uv.lock` is bumped to it. The related auth-expiry
*masking* fix (surface the stdout noise tail in `ProviderError` so "OAuth
session expired" is visible — `tasks/orchestration-box-auth-expiry.md` in the
harness repo) had already shipped in fork commit `088e35c`. This file stays as
the patch record + upstream procedure for future fixes.

## 1. ClaudeboxProvider StreamReader 64 KiB line-limit (workflow-killer) — UPSTREAMED `ab0ff4c`

**File:** `conductor/providers/claudebox.py`

**Symptom:** live run dies with
`ValueError: Separator is found, but chunk is longer than limit` →
`workflow_failed`. Hit on the first kafka-dq `001-e2e-poc` run (2026-07-14),
mid-Milestone-1, after the Implementer had scaffolded real files.

**Cause:** the streaming subprocess is spawned with
`asyncio.create_subprocess_exec(*argv, stdout=PIPE, stderr=PIPE, env=env)` — no
`limit=` — so the StreamReader uses asyncio's 64 KiB default. Output is read with
`process.stdout.readline()`, but `claude --output-format stream-json` emits one
JSON object **per line**; a single large `tool_result` (big Bash/gradle output)
or file-write body exceeds 64 KiB and `readline()` raises.

**Patch (applied to `.venv`, replicate in fork):**
- Add module constant:
  `_STREAM_READ_LIMIT: Final[int] = 64 * 1024 * 1024  # 64 MiB`
- Pass `limit=_STREAM_READ_LIMIT` to the **streaming** `create_subprocess_exec`
  (the `cb exec … claude …` spawn; NOT the `cb ls` healthcheck spawn).

**Follow-up to consider upstream:** even with a 64 MiB cap a pathological line
could still exceed it. A fully robust reader would chunk with `.read(n)` and
split on newlines manually, or catch `ValueError` from `readline()` and
`readuntil()`-drain. The constant cap is sufficient for realistic outputs; note
it as a known bound.

## 2. ClaudeboxProvider transient-API-error retry gap (workflow-killer) — UPSTREAMED `ab0ff4c`

**File:** `conductor/providers/claudebox.py`

**Upstreaming deviation (deliberate):** the classification `diag` joins
stderr + the retained stdout noise-line tail + `result_error_message` —
CLI-authored signals only. Agent-generated content (`content_parts`,
`result_text`) is deliberately EXCLUDED, unlike the original `.venv` stopgap
sketch: keyword-matching agent prose ("connection", "timed out", ...) would
misclassify genuine failures as retryable.

**Symptom:** live run dies with
`API Error: Connection closed mid-response` →
`claude subprocess exited with code 1: (no stderr output)` → `ProviderError` →
`subworkflow_failed` → `workflow_failed`. Hit on the second kafka-dq
`001-e2e-poc` run (2026-07-14) at **6/7 milestones**, after M6's tests already
passed — a transient network blip terminated a ~2.5h run. All work was intact in
the tree but uncommitted. Full incident write-up:
`harness/tasks/orchestration-transient-api-error-kills-run.md`.

**Cause (subtler than it looks):** `_classify_retryable` **already** matches
`connection`/`network`/`econnreset`/`timed out`/5xx as retryable. The bug is the
*input*: the non-zero-exit path called `_classify_retryable(stderr_text, …)` —
**stderr only** — but the CLI prints `API Error: Connection closed mid-response`
as a **plain stdout line**, which `_process_line` discards (non-JSON → skip).
stderr was empty, so the classifier saw no signal → `is_retryable=False` → a
retryable blip killed the run. The escalation ladder covers *verification*
failures, not *provider crashes*, and the experimental provider has no checkpoint
resume, so the misclassification was fatal.

**Patch (applied to `.venv`, replicate in fork):**
- Add `_MAX_NOISE_LINES = 50` and a `_RunOutcome.noise_lines` bounded tail;
  `_process_line` retains discarded non-JSON stdout lines instead of dropping
  them.
- At the `exit_code != 0` branch, classify against a `diag` string joining
  `stderr_text` + `noise_lines` + streamed content (`content_parts`,
  `result_text`, `result_error_message`), not stderr alone. The existing
  `connection` keyword then fires and the error is retried by the engine.
- No classifier-keyword change needed; no bespoke backoff added (engine attempt
  machinery handles the retry once `is_retryable=True`).

**Test to add upstream:** a non-zero exit whose stdout carried a non-JSON
`API Error: Connection closed …` line must classify `is_retryable=True`
(regression guard for the stderr-only path).

**Follow-up to consider upstream:** milestone-boundary resume and/or
auto-commit-per-verified-milestone would make transient blips a non-event even
without retry — track in the incident note, not here.

## Upstream procedure (per fix)
1. `git clone https://github.com/kentra-io/conductor` (no local clone exists yet).
2. Branch from the pinned rev, apply the patch, keep the diff minimal.
3. Add/extend a provider test that streams a >64 KiB stream-json line.
4. Push; bump `rev` in `agent-orchestration/pyproject.toml` + regenerate
   `uv.lock`; `uv sync` **on host** (never in-container — shared-venv thrash).
