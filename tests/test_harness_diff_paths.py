from orchestration.harness.diff_paths import check


def _run(testbed):
    return check(
        {
            "repo_path": str(testbed.path),
            "base_ref": testbed.base_ref,
            "allowed_globs": testbed.allowed_globs,
        }
    )


def test_confined_diff_passes(testbed):
    relpath = testbed.plant_in_path_change()
    verdict = _run(testbed)
    assert verdict["pass"] is True
    assert verdict["changed_files"] == [relpath]
    assert verdict["out_of_path_files"] == []


def test_out_of_path_file_fails(testbed):
    relpath = testbed.plant_out_of_path_file()
    verdict = _run(testbed)
    assert verdict["pass"] is False
    assert verdict["out_of_path_files"] == [relpath]
    assert verdict["changed_files"] == [relpath]


def test_declaring_a_deviation_does_not_clear_diff_paths(testbed):
    """diff_paths is the strict, no-exceptions gate - a declared deviation
    clears deviation_check (see test_harness_deviation_check.py) but never
    clears diff_paths itself."""
    relpath = testbed.plant_out_of_path_file()
    testbed.declare_deviation(relpath, reason="approved after the fact")
    verdict = _run(testbed)
    assert verdict["pass"] is False
    assert relpath in verdict["out_of_path_files"]


def test_mixed_in_path_and_out_of_path_changes(testbed):
    in_path = testbed.plant_in_path_change()
    out_of_path = testbed.plant_out_of_path_file()
    verdict = _run(testbed)
    assert verdict["pass"] is False
    assert sorted(verdict["changed_files"]) == sorted([in_path, out_of_path])
    assert verdict["out_of_path_files"] == [out_of_path]


def test_diff_range_alternative_to_base_ref(testbed):
    testbed.plant_in_path_change()
    verdict = check(
        {
            "repo_path": str(testbed.path),
            "diff_range": f"{testbed.base_ref}..HEAD",
            "allowed_globs": testbed.allowed_globs,
        }
    )
    assert verdict["pass"] is True
    assert verdict["changed_files"]
