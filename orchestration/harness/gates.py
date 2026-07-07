"""Gates aggregator - composes L1 + L2 + diff-paths + deviation-check into
one `{pass, report}` verdict for a single Conductor `script` step.

The milestone ladder (`workflows/milestone.yaml`) wants ONE step whose exit
code gates the ladder's routing (M5: "route on the checkers' EXIT CODE,
not a `pass` JSON field"). This module is that one step: it runs whichever
of the four deterministic checkers are configured (each is optional - a
milestone with no acceptance-check command simply omits `l1`, etc.) and
composes them per `orchestration/harness/README.md`'s documented
`diff_paths` vs `deviation_check` recommendation:

    1. Always run `diff_paths` (if configured) - if it passes, the
       milestone is fully in-scope.
    2. If `diff_paths` fails, run `deviation_check` (if configured) against
       the same diff. If it now passes, every out-of-scope file was
       declared - this checker does NOT hard-fail on that dimension (the
       README explicitly routes that case to the Verifier/human for a
       judgment call, not an automatic pass *or* an automatic fail).
       If `deviation_check` is absent or also fails, it's a hard,
       mechanical fail - no agent needed to catch it.

L1 and L2 are unconditional: both must pass (when configured) for the
overall verdict to pass.

Input JSON:
    {
      "l1": {...l1_acceptance payload...} | omitted,
      "l2": {...l2_healthcheck payload...} | omitted,
      "diff_paths": {...diff_paths payload...} | omitted,
      "deviation_check": {...deviation_check payload...} | omitted
    }
    All four keys are optional; an absent key means "this milestone has no
    contract for that dimension" and is treated as trivially passing (not
    run at all - e.g. a milestone with no declared acceptance-check command
    omits `l1`).

Output JSON:
    {
      "pass": bool,
      "report": {
        "l1": <l1_acceptance verdict> | null,
        "l2": <l2_healthcheck verdict> | null,
        "diff_paths": <diff_paths verdict> | null,
        "deviation_check": <deviation_check verdict> | null
      }
    }

Process exit code: 0 if pass, 1 if fail, 2 on a harness input error. Per the
shared convention (see README) - route on the exit code, not `report.pass`.
"""

import sys
from collections.abc import Sequence
from typing import Any

from orchestration.harness import deviation_check, diff_paths, l1_acceptance, l2_healthcheck
from orchestration.harness.common import (
    EXIT_ATTENTION,
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
    emit,
    read_input,
)


def check(payload: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "l1": None,
        "l2": None,
        "diff_paths": None,
        "deviation_check": None,
    }

    l1_ok = True
    if "l1" in payload and payload["l1"] is not None:
        report["l1"] = l1_acceptance.check(payload["l1"])
        l1_ok = report["l1"]["pass"]

    l2_ok = True
    if "l2" in payload and payload["l2"] is not None:
        report["l2"] = l2_healthcheck.check(payload["l2"])
        l2_ok = report["l2"]["pass"]

    paths_ok = True
    if "diff_paths" in payload and payload["diff_paths"] is not None:
        report["diff_paths"] = diff_paths.check(payload["diff_paths"])
        paths_ok = report["diff_paths"]["pass"]
        if not paths_ok and "deviation_check" in payload and payload["deviation_check"] is not None:
            report["deviation_check"] = deviation_check.check(payload["deviation_check"])
            # A declared deviation covers the path-set gate for this
            # aggregate verdict (defers the judgment call downstream to the
            # Verifier/human, per README) - it does not retroactively make
            # diff_paths itself pass.
            paths_ok = report["deviation_check"]["pass"]

    return {
        "pass": bool(l1_ok and l2_ok and paths_ok),
        "report": report,
    }


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        verdict = check(read_input(argv))
    except HarnessInputError as exc:
        emit({"error": str(exc)})
        return EXIT_ERROR
    emit(verdict)
    return EXIT_GOOD if verdict["pass"] else EXIT_ATTENTION


if __name__ == "__main__":
    raise SystemExit(main())
