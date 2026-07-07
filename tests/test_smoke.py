"""Smoke test: the package imports and exposes a version, so CI is green from genesis."""

import orchestration


def test_package_imports_and_has_version():
    assert isinstance(orchestration.__version__, str)
    assert orchestration.__version__
