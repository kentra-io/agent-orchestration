"""Milestone fixtures for the M6 live-tier DoD (tests/test_m6_live_verification.py).

Each fixture is a tiny, self-contained git repo -- `spec.md` (plain
requirements in the OpenSpec grammar), `tasks.md` (ONE milestone with the
spec-lifecycle ```contract block: check / criteria / paths -- the same format
`lifecycle apply` and the M4 harness consume), an empty `deviation.json`, a
trivial src package + smoke test (so L2 can be green), and the three cast
personas materialized at `<fixture>/.claude/agents/<role>.md` (P9,
hand-materialized -- see personas/README.md).

Unlike tests/testbed.py (which materializes a checked-in template), these
fixtures are generated from strings: the *content* of spec/tasks/diff is the
planted-defect catalogue itself, and each variant needs a different work
commit.

Variants (the git history is the point -- `HEAD~1..HEAD` is "the
Implementer's work" wherever a work commit exists):

- CLEAN                 -- work diff exactly implements the tasks; all boxes
                           ticked with evidence notes.
- UNDECLARED_DEVIATION  -- CLEAN + an out-of-path `billing/rogue.py`
                           traceable to no task; `deviation.json` stays [].
- FALSE_COMPLETION      -- task 2 ticked `[x]` with an evidence note but NO
                           corresponding change in the diff (the test file
                           was never written).
- AMBIGUOUS             -- no work commit; the single task ("add appropriate
                           caching to the data layer") is deliberately
                           under-specified and traces to no spec requirement.
                           Input for the Implementer QUESTION-halt scenario.
- LADDER                -- no work commit; a trivially small, fully-specified
                           milestone (greet()) for the real end-to-end
                           milestone.yaml run, where the live Implementer
                           does the work itself.

Fixtures MUST be created under the live box's worktree (M6_LIVE_WORKTREE) so
they are visible inside the box at the same absolute path (claudebox bind
mount) -- `build_milestone_fixture` creates a uniquely-named subdir there.
Fixtures are deliberately NOT auto-deleted: a live-tier failure is expensive
to reproduce, so the worktree residue is the post-mortem artifact.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

PERSONAS_DIR = Path(__file__).parent.parent / "personas"
PERSONA_ROLES = ("implementer", "verifier", "orchestrator")

CLEAN = "clean"
UNDECLARED_DEVIATION = "undeclared-deviation"
FALSE_COMPLETION = "false-completion"
AMBIGUOUS = "ambiguous"
LADDER = "ladder"

VERIFIER_VARIANTS = (CLEAN, UNDECLARED_DEVIATION, FALSE_COMPLETION)

# ---------------------------------------------------------------------------
# Fixture file contents
# ---------------------------------------------------------------------------

_GITIGNORE = "__pycache__/\n*.pyc\n.pytest_cache/\n"

# Root conftest so `python3 -m pytest -q` run from the fixture root puts the
# fixture root on sys.path (same trick as tests/fixtures/testbed/conftest.py).
_CONFTEST = "# Root conftest: makes the fixture root importable (src.*) under pytest.\n"

_SMOKE_TEST = "def test_smoke() -> None:\n    assert True\n"

_SLUG_SPEC = """\
# slug-tools -- fixture spec (M6 live verification)

## Requirements

### Requirement: Slug generation
The system SHALL provide `slugify(text)` in `src/slug.py` that lowercases
`text` and replaces each run of whitespace with a single hyphen.

#### Scenario: basic slug
- **GIVEN** the string "Hello World"
- **WHEN** `slugify` is called on it
- **THEN** it returns "hello-world"

### Requirement: Slug generation is tested
The change SHALL add `tests/test_slug.py` exercising the basic-slug scenario.

#### Scenario: test exists and passes
- **GIVEN** the repository test suite
- **WHEN** `python3 -m pytest -q` runs from the repo root
- **THEN** `tests/test_slug.py` passes, proving "Hello World" -> "hello-world"
"""

_SLUG_TASKS_UNTICKED = """\
## Milestone M6V: slugify
**Goal** -- implement and test `slugify` per spec.md.
**Deliverables** -- `src/slug.py`, `tests/test_slug.py`.
**Validation contract** -- checkable acceptance criteria, pre-committed:
  - `python3 -m pytest -q` passes from the repo root, including the new `tests/test_slug.py`.
  - Discharges **"Slug generation"** and **"Slug generation is tested"**.

  ```contract
  check: python3 -m pytest -q
  criteria: slugify lowercases and hyphenates whitespace per the spec, and the new test proves it.
  paths:
    - src/**
    - tests/**
    - tasks.md
    - deviation.json
  ```
**Steps** -- ordered breakdown:
  1. [ ] Implement `slugify(text)` in `src/slug.py` (lowercase, whitespace run -> one hyphen).
  2. [ ] Add `tests/test_slug.py` covering the "Hello World" -> "hello-world" scenario.
"""

_TASK_1_TICKED = (
    "  1. [x] Implement `slugify(text)` in `src/slug.py` (lowercase, each whitespace "
    'run -> single hyphen). -- evidence: satisfies "Slug generation"; verified via '
    "`python3 -m pytest -q` (suite green)."
)
_TASK_2_TICKED = (
    '  2. [x] Add `tests/test_slug.py` covering the "Hello World" -> "hello-world" '
    'scenario. -- evidence: satisfies "Slug generation is tested"; verified via '
    "`python3 -m pytest -q` (test_slug passed)."
)

_SLUG_IMPL = '''\
"""Slug helpers (M6 fixture)."""

import re


def slugify(text: str) -> str:
    """Lowercase `text` and replace each whitespace run with one hyphen."""
    return re.sub(r"\\s+", "-", text.strip().lower())
'''

_SLUG_TEST = """\
from src.slug import slugify


def test_basic_slug() -> None:
    assert slugify("Hello World") == "hello-world"
"""

_ROGUE_FILE = (
    "# Planted defect: out-of-path change traceable to no task/requirement.\nROGUE = True\n"
)

_AMBIGUOUS_SPEC = _SLUG_SPEC  # the spec is silent about caching -- that's the point

_AMBIGUOUS_TASKS = """\
## Milestone M6D: data-layer caching
**Goal** -- improve data-layer performance.
**Deliverables** -- caching in the data layer.
**Validation contract** -- checkable acceptance criteria, pre-committed:
  - `python3 -m pytest -q` passes from the repo root.

  ```contract
  check: python3 -m pytest -q
  criteria: Appropriate caching added to the data layer.
  paths:
    - src/**
    - tests/**
    - tasks.md
    - deviation.json
  ```
**Steps** -- ordered breakdown:
  1. [ ] Add appropriate caching to the data layer.
"""

_LADDER_SPEC = """\
# greeting -- fixture spec (M6 live ladder run)

## Requirements

### Requirement: Greeting
The system SHALL provide `greet(name)` in `src/greeting.py` returning exactly
the string `"Hello, " + name + "!"` (an f-string is fine).

#### Scenario: greet Ada
- **GIVEN** the name "Ada"
- **WHEN** `greet` is called on it
- **THEN** it returns "Hello, Ada!"

### Requirement: Greeting is tested
The change SHALL add `tests/test_greeting.py` exercising the greet-Ada scenario.

#### Scenario: test exists and passes
- **GIVEN** the repository test suite
- **WHEN** `python3 -m pytest -q` runs from the repo root
- **THEN** `tests/test_greeting.py` passes, proving greet("Ada") == "Hello, Ada!"
"""

_LADDER_TASKS = """\
## Milestone M6C: greeting
**Goal** -- implement and test `greet` per spec.md.
**Deliverables** -- `src/greeting.py`, `tests/test_greeting.py`.
**Validation contract** -- checkable acceptance criteria, pre-committed:
  - `python3 -m pytest -q` passes from the repo root, including the new `tests/test_greeting.py`.
  - Discharges **"Greeting"** and **"Greeting is tested"**.

  ```contract
  check: python3 -m pytest -q
  criteria: greet(name) returns exactly "Hello, <name>!" per the spec, and the new test proves it.
  paths:
    - src/greeting.py
    - tests/test_greeting.py
    - tasks.md
    - deviation.json
  ```
**Steps** -- ordered breakdown:
  1. [ ] Create `src/greeting.py` with `greet(name)` returning `f"Hello, {name}!"`.
  2. [ ] Add `tests/test_greeting.py` asserting `greet("Ada") == "Hello, Ada!"`.
"""

_SUMMARIES = {
    CLEAN: (
        "Implement and test slugify(text) in src/slug.py per spec.md "
        "(lowercase, each whitespace run -> single hyphen)."
    ),
    UNDECLARED_DEVIATION: (
        "Implement and test slugify(text) in src/slug.py per spec.md "
        "(lowercase, each whitespace run -> single hyphen)."
    ),
    FALSE_COMPLETION: (
        "Implement and test slugify(text) in src/slug.py per spec.md "
        "(lowercase, each whitespace run -> single hyphen)."
    ),
    AMBIGUOUS: "Add appropriate caching to the data layer.",
    LADDER: (
        "Create src/greeting.py with greet(name) returning exactly 'Hello, <name>!' "
        "plus tests/test_greeting.py proving greet('Ada') == 'Hello, Ada!'."
    ),
}

_MILESTONE_IDS = {
    CLEAN: "M6V",
    UNDECLARED_DEVIATION: "M6V",
    FALSE_COMPLETION: "M6V",
    AMBIGUOUS: "M6D",
    LADDER: "M6C",
}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "m6-testbed",
            "GIT_AUTHOR_EMAIL": "m6-testbed@example.invalid",
            "GIT_COMMITTER_NAME": "m6-testbed",
            "GIT_COMMITTER_EMAIL": "m6-testbed@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env
    )


@dataclass
class MilestoneFixture:
    path: Path
    variant: str
    milestone_id: str
    milestone_summary: str
    base_ref: str
    work_ref: str | None  # None for AMBIGUOUS / LADDER (no work commit yet)

    def git_status_porcelain(self) -> list[str]:
        """Changed/untracked paths (tracked + untracked), porcelain-parsed."""
        out = _git("status", "--porcelain", cwd=self.path).stdout
        paths = []
        for line in out.splitlines():
            if not line.strip():
                continue
            # "XY path" / "?? path"; renames ("a -> b") keep the new name.
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            paths.append(path)
        return paths


def materialize_personas(fixture_root: Path) -> None:
    """Copy the three cast personas to `<fixture>/.claude/agents/<role>.md`."""
    agents_dir = fixture_root / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for role in PERSONA_ROLES:
        shutil.copyfile(PERSONAS_DIR / f"{role}.md", agents_dir / f"{role}.md")


def _write(root: Path, relpath: str, content: str) -> None:
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _write_base(root: Path, variant: str) -> None:
    _write(root, ".gitignore", _GITIGNORE)
    _write(root, "conftest.py", _CONFTEST)
    _write(root, "deviation.json", "[]\n")
    _write(root, "src/__init__.py", "")
    _write(root, "tests/test_smoke.py", _SMOKE_TEST)

    if variant in VERIFIER_VARIANTS:
        _write(root, "spec.md", _SLUG_SPEC)
        _write(root, "tasks.md", _SLUG_TASKS_UNTICKED)
    elif variant == AMBIGUOUS:
        _write(root, "spec.md", _AMBIGUOUS_SPEC)
        _write(root, "tasks.md", _AMBIGUOUS_TASKS)
        # The slug milestone is already done at base -- gives "the data
        # layer" something plausibly adjacent to exist near, while the spec
        # stays silent on caching.
        _write(root, "src/slug.py", _SLUG_IMPL)
        _write(root, "tests/test_slug.py", _SLUG_TEST)
    elif variant == LADDER:
        _write(root, "spec.md", _LADDER_SPEC)
        _write(root, "tasks.md", _LADDER_TASKS)
    else:
        raise ValueError(f"unknown variant: {variant!r}")

    materialize_personas(root)


def _ticked_tasks(task1_done: bool, task2_done: bool) -> str:
    """The slug tasks.md with the requested boxes ticked + evidence notes."""
    lines = _SLUG_TASKS_UNTICKED.splitlines()
    out = []
    for line in lines:
        if task1_done and line.startswith("  1. [ ]"):
            out.append(_TASK_1_TICKED)
        elif task2_done and line.startswith("  2. [ ]"):
            out.append(_TASK_2_TICKED)
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _apply_work_commit(root: Path, variant: str) -> None:
    """Write the variant's 'Implementer work' on top of the base tree."""
    if variant == CLEAN:
        _write(root, "src/slug.py", _SLUG_IMPL)
        _write(root, "tests/test_slug.py", _SLUG_TEST)
        _write(root, "tasks.md", _ticked_tasks(task1_done=True, task2_done=True))
    elif variant == UNDECLARED_DEVIATION:
        _write(root, "src/slug.py", _SLUG_IMPL)
        _write(root, "tests/test_slug.py", _SLUG_TEST)
        _write(root, "tasks.md", _ticked_tasks(task1_done=True, task2_done=True))
        # The planted defect: an out-of-path file traceable to no task, with
        # deviation.json left empty (== hidden deviation).
        _write(root, "billing/rogue.py", _ROGUE_FILE)
    elif variant == FALSE_COMPLETION:
        # Task 1 genuinely done; task 2 ticked with an evidence note but NO
        # corresponding change (tests/test_slug.py never written). L1/L2
        # stay green (the smoke test passes), so only the Verifier's
        # intent-vs-actual diff can catch this.
        _write(root, "src/slug.py", _SLUG_IMPL)
        _write(root, "tasks.md", _ticked_tasks(task1_done=True, task2_done=True))
    else:
        raise ValueError(f"variant {variant!r} has no work commit")


def build_milestone_fixture(worktree_root: Path, variant: str) -> MilestoneFixture:
    """Build one milestone fixture under `worktree_root` (a live-box worktree).

    Creates a uniquely-named subdir, writes the base tree, `git init`s and
    commits it, then (for the verifier variants) applies + commits the
    variant's planted "Implementer work" so `HEAD~1..HEAD` is the diff the
    Verifier judges.
    """
    dest = Path(worktree_root) / f"m6-{variant}-{uuid.uuid4().hex[:8]}"
    dest.mkdir(parents=True)

    _write_base(dest, variant)
    _git("init", "-q", "-b", "main", cwd=dest)
    _git("add", "-A", cwd=dest)
    _git("commit", "-q", "-m", f"base: m6 {variant} fixture", cwd=dest)
    base_ref = _git("rev-parse", "HEAD", cwd=dest).stdout.strip()

    work_ref: str | None = None
    if variant in VERIFIER_VARIANTS:
        _apply_work_commit(dest, variant)
        _git("add", "-A", cwd=dest)
        _git("commit", "-q", "-m", f"implementer work ({variant})", cwd=dest)
        work_ref = _git("rev-parse", "HEAD", cwd=dest).stdout.strip()

    return MilestoneFixture(
        path=dest,
        variant=variant,
        milestone_id=_MILESTONE_IDS[variant],
        milestone_summary=_SUMMARIES[variant],
        base_ref=base_ref,
        work_ref=work_ref,
    )
