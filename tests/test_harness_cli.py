"""Proves the `python -m orchestration.harness.<checker>` CLI contract end to
end for one checker (l1_acceptance) - the plumbing (common.read_input /
common.emit / exit codes) is shared verbatim by all five, so this is
representative, not exhaustive; the other four are covered import-style in
their own test files."""

import json
import subprocess
import sys


def _run_cli(args, input_text=None):
    return subprocess.run(
        [sys.executable, "-m", "orchestration.harness.l1_acceptance", *args],
        input=input_text,
        capture_output=True,
        text=True,
    )


def test_cli_pass_exit_code_and_json():
    payload = json.dumps({"command": f"{sys.executable} -c 'raise SystemExit(0)'"})
    proc = _run_cli([payload])
    assert proc.returncode == 0
    verdict = json.loads(proc.stdout)
    assert verdict["pass"] is True


def test_cli_fail_exit_code():
    payload = json.dumps({"command": f"{sys.executable} -c 'raise SystemExit(1)'"})
    proc = _run_cli([payload])
    assert proc.returncode == 1
    verdict = json.loads(proc.stdout)
    assert verdict["pass"] is False


def test_cli_error_exit_code_on_bad_input():
    proc = _run_cli(["{}"])
    assert proc.returncode == 2
    verdict = json.loads(proc.stdout)
    assert "error" in verdict


def test_cli_reads_stdin_when_no_arg():
    payload = json.dumps({"command": f"{sys.executable} -c 'raise SystemExit(0)'"})
    proc = _run_cli([], input_text=payload)
    assert proc.returncode == 0
    verdict = json.loads(proc.stdout)
    assert verdict["pass"] is True


def test_cli_reads_a_json_file():
    import tempfile
    from pathlib import Path

    payload = json.dumps({"command": f"{sys.executable} -c 'raise SystemExit(0)'"})
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.json"
        input_path.write_text(payload)
        proc = _run_cli([str(input_path)])
        assert proc.returncode == 0
        verdict = json.loads(proc.stdout)
        assert verdict["pass"] is True
