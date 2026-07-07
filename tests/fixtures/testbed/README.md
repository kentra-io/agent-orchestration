# tests/fixtures/testbed/

A tiny sample Python project - `sample_pkg/` (two functions) plus
`tests/test_calc.py` (passing tests), `tests/test_stable.py` (a control,
never-flaky test), and `tests/test_flaky.py` (deterministically flaky, see
its docstring) - used to prove the deterministic verification harness (M4)
and, later, the live cast's verifier rigor (M6) against concrete,
reproducible failure cases.

This directory is a plain template tree, **not itself a git repository**:
committing a nested `.git` here would make git record this directory as an
opaque gitlink in the outer `agent-orchestration` repo instead of tracking
its files normally. Instead, `tests/testbed.py::materialize_testbed` copies
this tree into a pytest `tmp_path` at test time and `git init`s it there,
giving every test a fresh, real, throwaway git repo to plant defects in and
diff against:

- **out-of-path file** - `Testbed.plant_out_of_path_file()` writes+commits a
  file outside the fixture's declared path-set (`sample_pkg/**`,
  `tests/**`, `deviation.json`) - the planted defect for `diff_paths`.
- **undeclared deviation** - `Testbed.plant_undeclared_deviation()` is the
  same mechanical shape (a file outside the path-set, no log entry) under a
  name that matches the `deviation_check` scenario; `Testbed.declare_deviation()`
  appends a matching entry to `deviation.json` to clear it.
- **flaky test** - `tests/test_flaky.py`, driven by `Testbed.reset_flaky_state()`
  plus the `FLAKY_STATE_FILE`/`FLAKY_FAIL_EVERY` env vars (see that file's
  docstring for why it's reproducible without `random`).

Lands in **M4** (harness) and is exercised further in **M6** (live
verification) and **M9** (acceptance).
