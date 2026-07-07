# `orchestration/harness/` - the deterministic verification harness

Pure Python, stdlib only. No LLM, no claudebox, no network, no Conductor
runtime dependency. Every checker here is a small, pure function of its
input: same input (same repo state, same files) -> byte-identical verdict
JSON. That determinism is the property this package exists to guarantee -
it is the "cheap structural gates" layer of the verification model
(`orchestration.md` sec 5.3), sitting underneath the judged, advisory L3
layer (which is out of scope for this package - it invokes an agent).

## Calling convention (every checker follows this)

Each checker is a module `orchestration.harness.<name>` that is:

1. **Invocable as a script**, in three equivalent ways:
   ```bash
   # inline JSON as argv[1]
   python -m orchestration.harness.l1_acceptance '{"command": "pytest -q"}'

   # a path to a JSON file
   python -m orchestration.harness.l1_acceptance /tmp/input.json

   # stdin (no arg, or "-")
   echo '{"command": "pytest -q"}' | python -m orchestration.harness.l1_acceptance
   ```
   This is what a Conductor `script` step calls - point the step at
   `python -m orchestration.harness.<name>` and hand it the step's input as
   one JSON blob (however Conductor's script-step calling convention passes
   argv/stdin at build time; both are supported so M5 isn't locked to one).

2. **Importable directly**: `from orchestration.harness.l1_acceptance import check`
   and call `check(payload: dict) -> dict` with the same JSON shape (as an
   already-parsed dict) to get the verdict dict back, no subprocess needed.
   This is how the harness's own tests exercise every checker, and how a
   future in-process caller (e.g. a Python-side workflow step) can use it
   without shelling out.

3. **Emits exactly one JSON object to stdout** - pretty-printed
   (`indent=2, sort_keys=True`) so it is both script-step-parseable and
   human-diffable in logs. Never prints anything else to stdout.

4. **Exit code reflects the verdict**, so a Conductor `script` step can
   route on it without parsing JSON if that's more convenient:
   - `0` - the "good" outcome (`pass: true`, or for `flakiness`,
     `quarantined: false`)
   - `1` - the "needs attention" outcome (`pass: false`, or `quarantined: true`)
   - `2` - a harness-level input error (malformed JSON, missing required
     field, malformed `deviation.json`, ...). No verdict was computed; stdout
     is `{"error": "<message>"}` instead of the checker's normal shape.

5. **Never embeds wall-clock data** (timestamps, durations) in the verdict.
   This is what makes "same input -> same verdict" a testable property
   instead of an aspiration - see `tests/test_harness_determinism.py`.

## The five checkers (plus one aggregator)

| Module | Purpose | Pass/fail meaning |
|---|---|---|
| `l1_acceptance` | run one milestone-specific command, gate on exit code | `pass` = exit code 0 |
| `l2_healthcheck` | run a list of commands (suite+build+lint), all must pass | `pass` = every command exited 0 |
| `diff_paths` | is the diff confined to the milestone's declared path-set | `pass` = no file outside `allowed_globs` |
| `deviation_check` | is every out-of-path change covered by a logged deviation | `pass` = no undeclared out-of-path change |
| `flakiness` | rerun a command N times, quarantine if pass-rate < threshold | `quarantined` (not `pass`) = pass-rate < threshold |
| `gates` | composes l1+l2+diff_paths+deviation_check into one verdict for a single Conductor `script` step (M5's `milestone.yaml` `gates` step) | `pass` = l1 and l2 and (diff_paths, or deviation_check covers every out-of-path file) |

`gates` follows the same calling convention as the five checkers above (see
its own docstring for the exact input/output shape) - it exists purely so a
workflow author wires **one** `script` step instead of four, with routing on
that one step's exit code (see `workflows/milestone.yaml`).

Full input/output JSON shapes are documented in each module's own
docstring (`l1_acceptance.py`, `l2_healthcheck.py`, `diff_paths.py`,
`deviation_check.py`, `flakiness.py`) - read those before wiring a step.

### `flakiness` has no `pass` key on purpose

A flakiness verdict is a **quarantine signal**, not a judgment of the code
under test's correctness - conflating "quarantined" with "failed" would let
a workflow author accidentally treat a merely-unreliable test the same as a
broken one (or worse, treat "not quarantined" as "definitely green", which
it also isn't - see below). Route on `quarantined`, and always surface the
`pass_rate` alongside whatever downstream signal you derive from it.

Note the asymmetry this implies: `quarantined: false` means the pass-rate
was *at or above* `threshold`, not that every run passed. A test that
fails 1 run out of 20 (95% pass-rate) is not quarantined at the default
0.95 threshold - it is only the sub-threshold cases that get flagged. This
matches the spec's framing ("a test under ~95% pass-rate ... is
quarantined") literally; tune `threshold` per call-site if a milestone
needs zero-tolerance.

## `diff_paths` vs `deviation_check` - composing without double-counting

These two checkers answer **different questions** over the same diff and
are meant to be run **together**, not merged into one verdict:

- **`diff_paths`** - "is the diff mechanically confined to the declared
  path-set?" A strict, no-exceptions gate: an out-of-path file fails this
  checker *even if it is declared in `deviation.json`*. This is the
  cheapest, highest-signal, always-on gate (spec sec 5.3) - nothing excuses
  an out-of-path file here, by design.
- **`deviation_check`** - "is every out-of-path change explained by a
  logged deviation?" It reuses the same `allowed_globs` (so an in-path
  change needs no individual declaration - being inside the declared
  path-set is itself sufficient cover for *this* checker) but additionally
  consults `deviation.json`: an out-of-path file that *is* declared there
  passes `deviation_check`, even though it still (and always will) fail
  `diff_paths`.

**A "material undeclared deviation"** (the condition `deviation_check`
fails on) is precisely: *a changed file that is outside `allowed_globs`
AND has no matching entry (`path` or `path_glob`) in the deviation log.*
In-path changes are never "undeclared deviations" for this checker - by
construction they don't need a log entry at all (tracing an in-path
change to a specific task/requirement is the Verifier/L3's semantic job,
not this deterministic checker's).

**Recommended composition for a milestone's `script` steps:**
1. Always run `diff_paths` - if it passes, the milestone is fully
   in-scope; move on.
2. If `diff_paths` fails, run `deviation_check` against the same diff. If
   it now passes, every out-of-scope file was logged and explained -
   route to the Verifier/human for a judgment call rather than an
   automatic hard-fail. If `deviation_check` also fails, the Implementer
   made an undeclared, unexplained change outside its lane - a hard,
   mechanical fail, no agent needed to catch it.

This composition is exercised directly in
`tests/test_harness_deviation_check.py::test_out_of_path_and_declared_composes_with_diff_paths`.

## Determinism scope

"Same input -> same verdict" holds within a fixed environment: same repo
state (same commits/working tree), same `git`/`python`/PATH. It is not a
claim of bit-for-bit reproducibility across different machines, OS
locales, or tool versions - that's out of scope (and unnecessary: Conductor
reruns a checker against the *same* worktree it just modified, not a fresh
machine). `tests/test_harness_determinism.py` asserts the property the
harness actually needs: repeated calls, same process/repo, identical JSON.
