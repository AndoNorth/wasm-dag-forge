"""The helper against real containers, with the canary image of deploy/versions.yaml."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from wdf_itest.session import RUN_LABEL, image_ref

pytestmark = pytest.mark.integration

CHILD = Path(__file__).with_name("child_session.py")


def run_ids(run_id: str) -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={RUN_LABEL}={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def inspect(container_id: str) -> dict:
    out = subprocess.run(
        ["docker", "inspect", container_id], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)[0]


def test_container_carries_the_label_a_slot_port_and_the_memory_cap(t1):
    with t1.container("canary", ports={0: 8080}, command=["httpd", "-f", "-p", "8080"]) as c:
        c.wait_ready(0)
        info = inspect(c.id)
        assert info["Config"]["Labels"][RUN_LABEL] == t1.run_id
        assert c.host_ports[0] == 19000 + t1.slot * 10
        bindings = info["HostConfig"]["PortBindings"]["8080/tcp"]
        assert bindings == [{"HostIp": "127.0.0.1", "HostPort": str(c.host_ports[0])}]
        assert info["HostConfig"]["Memory"] == 1024**3
        assert run_ids(t1.run_id) == [c.id[:12]]
    assert run_ids(t1.run_id) == []


def test_a_second_container_cannot_start_while_one_runs(t1):
    with t1.container("canary", command=["sleep", "60"]):
        with pytest.raises(Exception, match="one container at a time"):
            with t1.container("canary", command=["sleep", "60"]):
                pass
    assert run_ids(t1.run_id) == []


def test_a_failing_test_body_still_removes_the_container(t1):
    with pytest.raises(ZeroDivisionError):
        with t1.container("canary", command=["sleep", "60"]):
            raise ZeroDivisionError
    assert run_ids(t1.run_id) == []


def test_only_the_containers_of_its_own_run_are_removed(t1):
    # A created container that never runs keeps the host at one running container.
    other = subprocess.run(
        ["docker", "create", "--label", f"{RUN_LABEL}=someone-else", image_ref("canary"), "true"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    try:
        with t1.container("canary", command=["sleep", "60"]):
            pass
        assert run_ids("someone-else") == [other[:12]]
    finally:
        subprocess.run(["docker", "rm", "-f", other], capture_output=True, check=True)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_interrupting_a_child_test_leaves_no_container(sig, tmp_path):
    run_id = f"itest-child-{sig.name.lower()}-{os.getpid()}"
    env = {
        **os.environ,
        "WDF_RUN_ID": run_id,
        "WDF_T1_LOCK_DIR": str(tmp_path),
    }
    child = subprocess.Popen(
        [sys.executable, "-m", "pytest", str(CHILD), "-q", "-p", "no:cacheprovider"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 180
        while not run_ids(run_id):
            assert child.poll() is None, child.stdout.read()
            assert time.monotonic() < deadline, "the child never started its container"
            time.sleep(0.5)
        child.send_signal(sig)
        child.wait(timeout=60)
        assert run_ids(run_id) == []
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        leftover = run_ids(run_id)
        if leftover:
            subprocess.run(["docker", "rm", "-f", *leftover], capture_output=True)


def test_an_unreachable_docker_fails_and_never_skips(tmp_path):
    env = {**os.environ, "DOCKER_HOST": "unix:///nonexistent", "WDF_T1_LOCK_DIR": str(tmp_path)}
    child = subprocess.run(
        [sys.executable, "-m", "pytest", str(CHILD), "-q", "-rs", "-p", "no:cacheprovider"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert child.returncode != 0
    assert "DockerUnavailable" in child.stdout
    assert "skipped" not in child.stdout
