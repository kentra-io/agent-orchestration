# Observability — pain points & backlog

Status: **open backlog** (captured 2026-07-14 during the first live kafka-dq
`001-e2e-poc` execution). Operating a live run today is painful: there is no
single "what is happening right now" surface. An operator has to hand-correlate
three separate artifacts under `<worktree>/.conductor-tmp/` and reason about a
buffered event stream. This note records the sharp edges and the finding that
explains the most confusing one.

## The stdout/stderr question (not a bug — a Conductor contract)

Symptom that looks alarming: `conductor.stderr.log` carries **all** the healthy
progress (Rich panels, agent turns, workflow-inputs box) while
`conductor.stdout.log` is **0 bytes** for the entire run.

This is deliberate, enforced in the engine:

- `conductor/cli/run.py` defines `_SilentAwareConsole`, **locked to
  `stderr=True`**, with the documented contract: *"`--silent` runs emit JSON on
  stdout with nothing else; routing gated output to stdout would corrupt that
  channel."*
- So **stderr = the human-facing progress UI** (always, regardless of
  `--silent`), and **stdout = reserved exclusively for the final machine-readable
  JSON result**, printed only when the workflow terminates.

Therefore an empty `conductor.stdout.log` means only *"the run has not finished
yet."* It is not evidence of trouble. stdout fills in exactly once, at the end,
with the terminal result JSON. This is the standard Unix split (stdout = data,
stderr = diagnostics); Conductor follows it strictly. The M8 launcher
(`orchestration/launch/change.py`) redirects the child's stdout/stderr to those
two files verbatim, so it inherits the same split.

**Fix for operators' confusion:** the launcher should label these files (or emit
a one-line legend in the report) so nobody re-diagnoses this. See backlog below.

## Pain points observed on the first live run

1. **Event log lags disk by minutes.** The Implementer had already written
   `build.gradle.kts` / `settings.gradle.kts` / `gradle.properties` into the
   worktree while the events JSONL was still frozen ~5 min behind at the previous
   `agent_tool_complete`. The event stream is buffered/flushed in chunks, so
   "frozen event log" is NOT a reliable liveness signal — it triggered a false
   stall diagnosis. Ground truth was filesystem mtimes in the worktree.
2. **No liveness/heartbeat.** Nothing distinguishes "model is mid-turn on a slow
   API call" from "the in-box process died." We had to fall back to host
   `docker ps` (the box was healthy) + worktree `find -mmin` to tell them apart.
3. **`docker ps` from inside a claudebox is filtered** by the socket-proxy — it
   omits the target box (and even the caller's own container), so it falsely
   reads as "box gone." Only host `docker ps` is authoritative. An operator
   monitoring from an interactive box will be misled.
4. **Three uncorrelated artifacts.** To answer "what's happening" you must
   manually join: `conductor.stderr.log` (UI), `checkpoints/conductor/*.events.jsonl`
   (structured, lagging), and worktree mtimes (truth). No single tail.
5. **Checkpoint filenames are timestamp-named, not run-ordered.** Easy to anchor
   on the newest-*named* checkpoint that belongs to an *older* run; must
   disambiguate by `workflow_hash`. (Bit us earlier during box-forwarding debug.)

## Backlog (observability leg — not yet specced)

- [ ] **Operator status surface**: one command / one file that answers "current
      milestone, current agent, current turn, last activity age, health" by
      joining events + worktree mtimes. Candidate: a `orchestration.launch`
      companion `status` subcommand reading `.conductor-tmp/`.
- [ ] **Heartbeat / liveness**: periodic "still alive, agent X turn N" tick
      (either a launcher-side poll of the child pid + event age, or a provider
      keepalive) so a slow API turn is distinguishable from a dead process.
- [ ] **Flush events promptly** (or document the buffering) so the JSONL is a
      trustworthy real-time signal instead of lagging disk by minutes.
- [ ] **Label the log files**: launcher report should annotate
      `conductor.stdout.log` = "final JSON result only (empty until done)" and
      `conductor.stderr.log` = "live progress UI", killing the recurring
      "why is stdout empty?" question.
- [ ] **Warn on in-box `docker ps`**: any operator-facing monitoring doc must
      state that socket-proxied `docker ps` is filtered and only host
      `docker ps` is authoritative.
- [ ] **Run-ordered checkpoint view**: surface `workflow_hash` + start time so
      the active run is unambiguous vs. stale same-worktree checkpoints.
- [ ] Feeds the Stage-4 observability plan (LiteLLM + Langfuse) — the per-agent
      turn/tool stream is the natural export into a trace backend.
