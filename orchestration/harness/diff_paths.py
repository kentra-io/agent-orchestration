"""Diff-confined-to-declared-paths (spec orchestration.md sec 5.3).

The mechanical, no-LLM adherence gate: a milestone's validation contract
declares an allowed path-set (globs); this checker fails if any file in the
diff falls outside it. No exceptions - not even a declared deviation excuses
an out-of-path file here (that composition lives in `deviation_check`, which
additionally consults `deviation.json`). See
`orchestration/harness/README.md` sec "DEVIATION_SEMANTICS" for how the two
checkers compose without double-counting.

Input JSON:
    {
      "repo_path": str,          # optional, default "."
      "base_ref": str,            # one of base_ref/diff_range required - see common.git_diff_files
      "diff_range": str,           # (optional alternative to base_ref)
      "allowed_globs": [str, ...]  # required, non-empty list of glob patterns
    }

Output JSON:
    {
      "pass": bool,
      "changed_files": [str, ...],
      "out_of_path_files": [str, ...],
      "allowed_globs": [str, ...]
    }

Process exit code: 0 if pass, 1 if fail (an out-of-path file exists), 2 on a
harness input error.
"""

import sys
from collections.abc import Sequence
from typing import Any

from orchestration.harness.common import (
    EXIT_ATTENTION,
    EXIT_ERROR,
    EXIT_GOOD,
    HarnessInputError,
    emit,
    git_diff_files,
    path_matches_any,
    read_input,
)


def check(payload: dict[str, Any]) -> dict[str, Any]:
    repo_path = payload.get("repo_path", ".")
    base_ref = payload.get("base_ref")
    diff_range = payload.get("diff_range")
    allowed_globs = payload.get("allowed_globs")

    if not allowed_globs or not isinstance(allowed_globs, list):
        raise HarnessInputError("'allowed_globs' (non-empty list of glob strings) is required")

    changed = git_diff_files(repo_path, base_ref, diff_range)
    out_of_path = [f for f in changed if not path_matches_any(f, allowed_globs)]

    return {
        "pass": not out_of_path,
        "changed_files": changed,
        "out_of_path_files": out_of_path,
        "allowed_globs": allowed_globs,
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
