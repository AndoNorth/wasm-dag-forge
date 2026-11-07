"""A session that declares two holds two named containers, against real containers."""

import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from wdf_itest.session import RUN_LABEL, ContainerError

# A pair session holds both host lock files, which is what lets the child sessions below run
# two containers between them without the host ever running more than two.
pytestmark = [pytest.mark.integration, pytest.mark.pair]

CHILD = Path(__file__).with_name("child_hold.py")


@pytest.fixture(scope="module")
def t1(own_session):
    return own_session


def running_under(run_id: str) -> int:
    out = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"label={RUN_LABEL}={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return len(out.split())


def canary(t1, name, **kwargs):
    return t1.container(
        "canary", ports={0: 8080}, command=["httpd", "-f", "-p", "8080"], name=name, **kwargs
    )


def test_second_container_runs_beside_the_first_on_another_host_port(t1):
    with canary(t1, "store") as first, canary(t1, "database") as second:
        first.wait_ready(0)
        second.wait_ready(0)
        assert first.host_ports[0] != second.host_ports[0]
        assert first.name != second.name
        assert running_under(t1.run_id) == 2


def test_second_container_survives_the_close_of_the_first(t1):
    with ExitStack() as outer:
        inner = ExitStack()
        inner.enter_context(canary(t1, "store"))
        second = outer.enter_context(canary(t1, "database"))
        assert running_under(t1.run_id) == 2
        inner.close()
        assert running_under(t1.run_id) == 1
        second.wait_ready(0)
        assert second.exec("hostname").stdout.strip() == second.id[:12]
    assert running_under(t1.run_id) == 0


def test_a_failing_second_container_leaves_the_first_running(t1):
    with canary(t1, "store") as first:
        with pytest.raises(ContainerError):
            with t1.container("canary", command=["no-such-binary"], name="database"):
                pass
        first.wait_ready(0)
        assert running_under(t1.run_id) == 1


def test_three_containers_in_one_session_raise_container_error(t1):
    with canary(t1, "store"), canary(t1, "database"):
        with pytest.raises(ContainerError, match="at most 2 containers"):
            with canary(t1, "third"):
                pass
        assert running_under(t1.run_id) == 2


def test_three_containers_check_is_not_confused_by_a_reused_name(t1):
    with canary(t1, "store"):
        with pytest.raises(ContainerError, match="already running"):
            with canary(t1, "store"):
                pass
        assert running_under(t1.run_id) == 1


def child(run_id, lock_dir, hold, containers):
    env = {
        **os.environ,
        "WDF_RUN_ID": run_id,
        "WDF_T1_LOCK_DIR": str(lock_dir),
        "HOLD_SECONDS": str(hold),
        "HOLD_CONTAINERS": str(containers),
    }
    return subprocess.Popen(
        [sys.executable, str(CHILD)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_two_sessions_that_each_want_two_hold_two_containers_between_them_one_after_the_other(
    t1, tmp_path
):
    ids = [f"itest-two-{os.getpid()}-{i}" for i in range(2)]
    children = [child(run_id, tmp_path, hold=3, containers=2) for run_id in ids]
    peak = 0
    try:
        while any(c.poll() is None for c in children):
            peak = max(peak, sum(running_under(run_id) for run_id in ids))
            time.sleep(0.1)
        results = [c.communicate(timeout=60) for c in children]
    finally:
        for c in children:
            if c.poll() is None:
                c.kill()
                c.wait()
    for c, (_, err) in zip(children, results, strict=True):
        assert c.returncode == 0, err
        assert "LockTimeout" not in err
    assert peak == 2
    spans = sorted(
        (
            float(out.split("held ")[1].split()[0]),
            float(out.split("released ")[1].split()[0]),
        )
        for out, _ in results
    )
    assert spans[0][1] <= spans[1][0], spans


def test_a_session_opened_inside_a_session_shares_its_containers_and_ports(t1):
    from wdf_itest import Session

    with canary(t1, "store") as first:
        with (
            Session() as nested,
            nested.container(
                "canary", ports={0: 8080}, command=["httpd", "-f", "-p", "8080"]
            ) as second,
        ):
            assert nested.run_id == t1.run_id
            assert second.host_ports[0] != first.host_ports[0]
            assert running_under(t1.run_id) == 2
            with pytest.raises(ContainerError, match="at most 2 containers"):
                with nested.container("canary", command=["sleep", "60"]):
                    pass
        assert running_under(t1.run_id) == 1
    assert running_under(t1.run_id) == 0
