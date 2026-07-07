from orchestration.harness.deviation_check import check
from orchestration.harness.diff_paths import check as diff_paths_check


def _run(testbed):
    return check(
        {
            "repo_path": str(testbed.path),
            "base_ref": testbed.base_ref,
            "allowed_globs": testbed.allowed_globs,
            "deviation_log": "deviation.json",
        }
    )


def test_no_deviation_log_means_no_declarations(testbed):
    """Missing deviation.json == an empty declared set, not an error."""
    verdict = _run(testbed)
    assert verdict["pass"] is True
    assert verdict["declared"] == []


def test_in_path_change_needs_no_declaration(testbed):
    testbed.plant_in_path_change()
    verdict = _run(testbed)
    assert verdict["pass"] is True
    assert verdict["undeclared_changes"] == []


def test_undeclared_deviation_fails(testbed):
    relpath = testbed.plant_undeclared_deviation()
    verdict = _run(testbed)
    assert verdict["pass"] is False
    assert verdict["undeclared_changes"] == [relpath]


def test_declared_deviation_passes(testbed):
    relpath = testbed.plant_undeclared_deviation()
    testbed.declare_deviation(
        relpath, reason="needed an extra script not in the plan", task_id="T1"
    )
    verdict = _run(testbed)
    assert verdict["pass"] is True
    assert verdict["undeclared_changes"] == []
    assert any(d["path"] == relpath for d in verdict["declared"])


def test_declared_by_glob(testbed):
    relpath = testbed.plant_undeclared_deviation(relpath="scratch/anything.py")
    log_path = testbed.path / "deviation.json"
    log_path.write_text(
        '[{"path_glob": "scratch/**", "reason": "scratch is fine", "task_id": "T2"}]\n'
    )
    testbed.commit_all("declare glob deviation")
    verdict = _run(testbed)
    assert verdict["pass"] is True
    assert relpath not in verdict["undeclared_changes"]


def test_out_of_path_and_declared_composes_with_diff_paths(testbed):
    """A file outside allowed_globs that IS declared: diff_paths still
    (correctly) flags it mechanically; deviation_check clears it. Each
    checker answers a different question - see harness/README.md."""
    relpath = testbed.plant_out_of_path_file()
    testbed.declare_deviation(relpath, reason="ad-hoc scratch output, approved after the fact")

    paths_verdict = diff_paths_check(
        {
            "repo_path": str(testbed.path),
            "base_ref": testbed.base_ref,
            "allowed_globs": testbed.allowed_globs,
        }
    )
    deviation_verdict = _run(testbed)

    assert paths_verdict["pass"] is False  # mechanical gate: still out of path
    assert deviation_verdict["pass"] is True  # but explained by a logged deviation


def test_malformed_deviation_log_is_a_harness_error():
    import tempfile
    from pathlib import Path

    from orchestration.harness.common import HarnessInputError

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "deviation.json").write_text('[{"reason": "missing path/path_glob"}]')
        try:
            check(
                {
                    "repo_path": str(repo),
                    "base_ref": "HEAD",
                    "allowed_globs": ["**"],
                    "deviation_log": "deviation.json",
                }
            )
        except HarnessInputError as exc:
            assert "path" in str(exc) or "path_glob" in str(exc)
        else:
            raise AssertionError("expected HarnessInputError")
