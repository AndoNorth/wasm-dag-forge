"""The lock files, the ports and the image pins, none of which needs Docker."""

import os

import pytest
from wdf_itest import session
from wdf_itest.session import LockTimeout, host_port, image_ref, lock_paths


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WDF_T1_LOCK_DIR", str(tmp_path))
    return tmp_path


def test_lock_files_are_two_named_files_in_the_lock_dir(lock_dir):
    assert lock_paths() == [lock_dir / "wdf-t1.0.lock", lock_dir / "wdf-t1.1.lock"]


def test_default_lock_files_are_the_host_wide_pair(monkeypatch):
    monkeypatch.delenv("WDF_T1_LOCK_DIR", raising=False)
    assert [str(p) for p in lock_paths()] == ["/tmp/wdf-t1.0.lock", "/tmp/wdf-t1.1.lock"]


def test_two_sessions_hold_one_file_each_and_a_third_times_out_naming_the_holders(lock_dir):
    first = session._acquire_lock("run-a", timeout=1)
    second = session._acquire_lock("run-b", timeout=1)
    try:
        assert first != second
        with pytest.raises(LockTimeout) as raised:
            session._acquire_lock("run-c", timeout=0.3, poll=0.05)
        message = str(raised.value)
        assert "run=run-a" in message and "run=run-b" in message
        assert f"pid={os.getpid()}" in message
    finally:
        os.close(first)
        os.close(second)


def test_a_released_file_is_taken_by_the_next_session(lock_dir):
    first = session._acquire_lock("run-a", timeout=1)
    held = session._acquire_lock("run-b", timeout=1)
    os.close(first)
    third = session._acquire_lock("run-c", timeout=1)
    assert (lock_dir / "wdf-t1.0.lock").read_text().strip().endswith("run=run-c")
    os.close(held)
    os.close(third)


@pytest.mark.parametrize(
    ("slot", "k", "port"), [(0, 0, 19000), (0, 9, 19009), (1, 0, 19010), (9, 9, 19099)]
)
def test_host_ports_stay_in_the_slot_range(slot, k, port):
    assert host_port(slot, k) == port


@pytest.mark.parametrize(("slot", "k"), [(0, 10), (0, -1)])
def test_a_port_index_of_10_or_more_is_refused(slot, k):
    with pytest.raises(ValueError):
        host_port(slot, k)


@pytest.mark.parametrize("slot", ["10", "-1"])
def test_a_slot_outside_0_to_9_is_refused(slot, monkeypatch):
    monkeypatch.setenv("WDF_SLOT", slot)
    with pytest.raises(ValueError):
        session.slot_from_env()


def test_the_canary_image_is_pinned_by_digest():
    ref = image_ref("canary")
    assert ref.startswith("busybox:") and "@sha256:" in ref
