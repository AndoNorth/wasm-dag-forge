"""The `t1` fixture, registered as a pytest plugin by the package entry point."""

import pytest

from .session import Session


@pytest.fixture(scope="session")
def t1():
    """A T1 session. A missing Docker fails the test and is never a skip."""
    with Session() as session:
        yield session
