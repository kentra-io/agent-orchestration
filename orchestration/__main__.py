"""Delegation stub — the CLI moved to `orchestration.cli.main` (docs/cli-design.md §3)."""

from orchestration.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
