"""Materialize the fixture testbed (tests/fixtures/testbed/) into a real,
throwaway git repo, with helpers to plant the M4 planted-defect catalogue.

We copy the template tree into a `tmp_path` and `git init` it there rather
than committing a nested `.git` into this repo - see
`tests/fixtures/testbed/README.md` for why.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "fixtures" / "testbed"

# The fixture's own declared path-set: what a milestone touching sample_pkg
# and its tests is "supposed" to change, including the bookkeeping file
# (deviation.json) that legitimately changes whenever a deviation is
# declared. The harness applies no implicit exemptions for files like this -
# a real plan's validation contract is expected to list them explicitly if
# they're expected to change; the fixture does the same.
ALLOWED_GLOBS = ["sample_pkg/**", "tests/**", "deviation.json"]


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=_git_env(),
    )


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "testbed",
            "GIT_AUTHOR_EMAIL": "testbed@example.invalid",
            "GIT_COMMITTER_NAME": "testbed",
            "GIT_COMMITTER_EMAIL": "testbed@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return env


@dataclass
class Testbed:
    path: Path
    base_ref: str
    allowed_globs: list[str] = field(default_factory=lambda: list(ALLOWED_GLOBS))

    def write(self, relpath: str, content: str) -> Path:
        target = self.path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def commit_all(self, message: str) -> None:
        _git("add", "-A", cwd=self.path)
        _git("commit", "-q", "-m", message, cwd=self.path)

    def rev_parse(self, ref: str = "HEAD") -> str:
        return _git("rev-parse", ref, cwd=self.path).stdout.strip()

    def plant_in_path_change(
        self, relpath: str = "sample_pkg/extra_module.py", content: str = "VALUE = 1\n"
    ) -> str:
        """A change that stays within `allowed_globs` - the "nothing wrong here" case."""
        self.write(relpath, content)
        self.commit_all(f"in-path change: {relpath}")
        return relpath

    def plant_out_of_path_file(
        self,
        relpath: str = "scratch/oops.txt",
        content: str = "not part of the declared path-set\n",
    ) -> str:
        """A file outside `allowed_globs` - the planted defect for `diff_paths`."""
        self.write(relpath, content)
        self.commit_all(f"plant out-of-path file: {relpath}")
        return relpath

    def plant_undeclared_deviation(
        self, relpath: str = "scratch/undeclared_change.py", content: str = "# unplanned\n"
    ) -> str:
        """Mechanically identical to `plant_out_of_path_file` (a file outside
        `allowed_globs`, no `deviation.json` entry) - named separately because
        it's the scenario `deviation_check` (not `diff_paths`) is exercised
        against, and because a test may plant both defects side by side (see
        the composition test in test_harness_deviation_check.py) and needs
        them at distinct paths.
        """
        self.write(relpath, content)
        self.commit_all(f"plant undeclared deviation: {relpath}")
        return relpath

    def declare_deviation(self, path: str, reason: str, task_id: str | None = None) -> None:
        """Append an entry to deviation.json covering `path` and commit it."""
        log_path = self.path / "deviation.json"
        entries = json.loads(log_path.read_text()) if log_path.exists() else []
        entry: dict[str, str] = {"path": path, "reason": reason}
        if task_id:
            entry["task_id"] = task_id
        entries.append(entry)
        log_path.write_text(json.dumps(entries, indent=2) + "\n")
        self.commit_all(f"declare deviation: {path}")

    def reset_flaky_state(self) -> None:
        state = self.path / ".flaky_state"
        if state.exists():
            state.unlink()


def materialize_testbed(dest: Path) -> Testbed:
    shutil.copytree(
        TEMPLATE_DIR, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("README.md")
    )
    _git("init", "-q", "-b", "main", cwd=dest)
    _git("add", "-A", cwd=dest)
    _git("commit", "-q", "-m", "initial testbed commit", cwd=dest)
    base_ref = _git("rev-parse", "HEAD", cwd=dest).stdout.strip()
    return Testbed(path=dest, base_ref=base_ref)
