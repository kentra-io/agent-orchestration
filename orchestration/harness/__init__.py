"""Deterministic verification harness (L1/L2/paths/deviation/flakiness).

Pure Python, no LLM, no claudebox, no network, no Conductor runtime. Every
checker is invocable as a Conductor `script` step (JSON in, JSON out, exit
code reflects the verdict) and importable directly (`check(payload) -> dict`).

Modules:
    l1_acceptance    - milestone-specific executable acceptance check (exit-code gate)
    l2_healthcheck   - the whole repo's suite/build/lint, all must pass
    diff_paths       - diff-confined-to-declared-paths (mechanical, no exceptions)
    deviation_check  - declared-deviation cross-check (composes with diff_paths)
    flakiness        - N-rerun quarantine (never a silent green)

See `orchestration/harness/README.md` for the full input/output contract
each checker follows, and how they compose - this is the M5 workflow
author's contract for wiring these in as Conductor `script` steps.
"""
