"""A session of its own for the container accounting tests of this package."""

import os

import pytest
from wdf_itest.session import MAX_SLOT, Session, slot_from_env


def slot_after(offset: int) -> str:
    return str((slot_from_env() + offset) % (MAX_SLOT + 1))


@pytest.fixture(scope="session")
def own_slot() -> str:
    """The slot after the one of the shared session, whose host ports are then left alone."""
    return slot_after(1)


@pytest.fixture(scope="session")
def child_slot() -> str:
    """The slot for a child pytest process, apart from the shared and the own session."""
    return slot_after(2)


@pytest.fixture(scope="module")
def own_session(tmp_path_factory, own_slot):
    """A two container session apart from the shared `t1` of the pytest session.

    The accounting tests count the containers of their run and the cap of their session, so they
    must not share either with a fixture that lives for the whole pytest session, such as the
    store of `wdf_storecheck.fixtures.store_session`.
    A test module takes it as its `t1`, and the child pytest files keep the shared one.
    Its lock files are private, which is why it can open while the shared session holds the host's,
    and its slot is the next one, so its host ports do not meet those of the shared session.
    """
    env = {
        "WDF_RUN_ID": f"itest-own-{os.getpid()}-{os.urandom(3).hex()}",
        "WDF_T1_LOCK_DIR": str(tmp_path_factory.mktemp("t1-locks")),
        "WDF_SLOT": own_slot,
    }
    saved = {key: os.environ.get(key) for key in env}
    shared, Session._open = Session._open, None
    os.environ.update(env)
    try:
        with Session(containers=2) as session:
            yield session
    finally:
        Session._open = shared
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
