import pytest
from testbed import Testbed, materialize_testbed


@pytest.fixture
def testbed(tmp_path) -> Testbed:
    """A fresh, real git repo (see tests/testbed.py) for each test."""
    return materialize_testbed(tmp_path / "testbed")
