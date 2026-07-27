"""In-box gate execution: optional `box`/`box_workdir` payload keys wrap the
L1/L2 command in `cb exec ... bash -lc <cmd>` so deterministic checks run on
the SAME toolchain (and warm caches) as the agents, not the daemon host."""

import shlex

from orchestration.harness import l1_acceptance, l2_healthcheck
from orchestration.harness.common import wrap_in_box


class TestWrapInBox:
    def test_wraps_with_workdir(self) -> None:
        wrapped = wrap_in_box("go test ./...", "mybox", "/w/tree")
        assert wrapped == (
            "cb exec --workdir /w/tree mybox bash -lc " + shlex.quote("go test ./...")
        )

    def test_wraps_without_workdir(self) -> None:
        assert wrap_in_box("exit 0", "mybox") == "cb exec mybox bash -lc 'exit 0'"

    def test_quotes_hostile_command(self) -> None:
        wrapped = wrap_in_box("echo 'a b' && ls; true", "box1", "/p")
        # The inner command must survive as ONE bash -lc argument.
        assert shlex.split(wrapped)[-1] == "echo 'a b' && ls; true"


class TestL1BoxWiring:
    def test_l1_wraps_command_when_box_present(self, monkeypatch) -> None:
        seen: dict = {}

        def fake_run(command, cwd=".", timeout=600, env_overrides=None):
            seen["command"] = command
            return 0, "ok", ""

        monkeypatch.setattr(l1_acceptance, "run_command", fake_run)
        verdict = l1_acceptance.check(
            {"command": "go test ./...", "box": "mybox", "box_workdir": "/w/tree"}
        )
        assert verdict["pass"] is True
        assert seen["command"].startswith("cb exec --workdir /w/tree mybox bash -lc ")
        # The report surfaces the wrapped command for observability.
        assert verdict["command"] == seen["command"]

    def test_l1_unwrapped_without_box(self, monkeypatch) -> None:
        seen: dict = {}

        def fake_run(command, cwd=".", timeout=600, env_overrides=None):
            seen["command"] = command
            return 0, "ok", ""

        monkeypatch.setattr(l1_acceptance, "run_command", fake_run)
        l1_acceptance.check({"command": "go test ./..."})
        assert seen["command"] == "go test ./..."


class TestL2BoxWiring:
    def test_l2_wraps_every_command_when_box_present(self, monkeypatch) -> None:
        seen: list = []

        def fake_run(command, cwd=".", timeout=600, env_overrides=None):
            seen.append(command)
            return 0, "ok", ""

        monkeypatch.setattr(l2_healthcheck, "run_command", fake_run)
        verdict = l2_healthcheck.check(
            {"commands": ["make build", "make test"], "box": "b1", "box_workdir": "/w"}
        )
        assert verdict["pass"] is True
        assert all(c.startswith("cb exec --workdir /w b1 bash -lc ") for c in seen)
        assert [r["command"] for r in verdict["results"]] == seen
