# Empty on purpose: its only job is to sit at the testbed repo's root so
# pytest adds this directory to `sys.path`, making `sample_pkg` importable
# from `tests/test_calc.py` when pytest is run from this root (which is
# exactly how the M4 harness checkers invoke it - see ../../testbed.py).
