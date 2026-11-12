"""The worker service on a dev server: its three workers, the control queue of its build, the
drain on SIGTERM and the metrics endpoint, and the refusal of a missing variable."""

import asyncio
import dataclasses
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError
from wdf_artifactreaper import schedule
from wdf_storecheck.s3 import Creds, dump_credentials
from wdf_workflow.contract import naming
from wdf_workflow.service import (
    DRAIN,
    ENV,
    REAPER_ENV,
    Config,
    MissingEnv,
    real_control,
    serve,
)
from wdf_workflow.testing import FakeControl, FakePool, Succeed, connect
from wdf_workflow.testing.control import Commit

# A job of these tests ends within seconds, so a wedged service fails its test.
LIMIT_S = 120


def succeed(task) -> Succeed:
    return Succeed()


@pytest.mark.integration
def test_service_starts_three_workers(temporal, config, running, start_job, reap_report, caplog):
    caplog.set_level(logging.INFO, logger="wdf_workflow.service")
    control = FakeControl()

    async def body():
        client = await connect(temporal)
        async with running(config("three"), control), FakePool(client, "compile", succeed):
            # The workflow worker runs the job and the control worker its control activities.
            status = await (await start_job(client, control, "three")).result()
            # The reaper worker serves the Schedule that the bootstrap creates in the namespace.
            await schedule.ensure_schedule(client)
            return status, await schedule.trigger_schedule(client)

    status, report = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    assert {"load_plan", "cache_lookup", "commit", "cache_record", "emit"} <= set(control.calls)
    assert report == reap_report
    assert (
        "polling in namespace wdf: wdf-workflow as build three of wdf-workflow-worker,"
        " wdf-control-three and wdf-artifact-reaper"
    ) in caplog.messages


@pytest.mark.integration
def test_control_queue_per_build(temporal, config, running, start_job):
    """Each build serves the control queue named by its build id, and a job pinned to a build
    runs its control activities there only."""
    controls = {build: FakeControl() for build in ("build-a", "build-b")}
    polled = []

    async def body():
        client = await connect(temporal)
        async with FakePool(client, "compile", succeed):
            for build, control in controls.items():
                async with running(config(build), control):
                    polled.append((build, await _polled(client, f"wdf-control-{build}")))
                    # The server lists a poller that stopped for a while, so only the build
                    # that has not run yet shows that no other build polls its queue.
                    if build == "build-a":
                        polled.append(("build-b", await _polled(client, "wdf-control-build-b", 2)))
                    assert await (await start_job(client, control, build)).result() == "succeeded"

    asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert polled == [("build-a", True), ("build-b", False), ("build-b", True)]
    for control in controls.values():
        assert control.calls.count("load_plan") == 1
        assert len(control.committed) == 1


async def _polled(client, queue: str, wait_s: float = 10) -> bool:
    """Whether a worker polls the activity queue within `wait_s`, the server listing a poller a
    little after its first poll, and dropping one that stopped polling after a while."""
    request = DescribeTaskQueueRequest(
        namespace=client.namespace,
        task_queue=TaskQueue(name=queue),
        task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
    )
    for _ in range(int(wait_s / 0.5)):
        if (await client.workflow_service.describe_task_queue(request)).pollers:
            return True
        await asyncio.sleep(0.5)
    return False


class SlowCommit(FakeControl):
    """FakeControl whose commit takes COMMIT_S, long enough to be in flight at a SIGTERM."""

    COMMIT_S = 3

    def __init__(self):
        super().__init__()
        self.committing = asyncio.Event()
        self.finished: list[float] = []

    @activity.defn(name="commit")
    async def commit(self, request: Commit) -> None:
        self.committing.set()
        await asyncio.sleep(self.COMMIT_S)
        await FakeControl.commit(self, request)
        self.finished.append(time.monotonic())


@pytest.mark.integration
def test_sigterm_drains(temporal, config, running, start_job):
    """A SIGTERM stops the polls, lets the commit in flight finish and report, and returns; the
    job then finishes on the next start of the build without running the commit again."""
    control = SlowCommit()

    async def body():
        client = await connect(temporal)
        async with FakePool(client, "compile", succeed):
            async with running(config("drain"), control) as service:
                handle = await start_job(client, control, "drain")
                await control.committing.wait()
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.wait_for(asyncio.shield(service), DRAIN.total_seconds())
                stopped = time.monotonic()
            open_after_drain = (await handle.describe()).status
            async with running(config("drain"), control):
                return stopped, open_after_drain, await handle.result()

    stopped, open_after_drain, status = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    [finished] = control.finished
    assert finished <= stopped
    assert open_after_drain == WorkflowExecutionStatus.RUNNING
    assert status == "succeeded"
    assert control.calls.count("commit") == 1


@pytest.mark.integration
def test_metrics_endpoint(temporal, config, running, start_job):
    """The endpoint serves the SDK's metrics and the workflow's own, with no id in a label."""
    control = FakeControl()
    served = config("metrics")

    async def body():
        client = await connect(temporal)
        async with running(served, control), FakePool(client, "compile", succeed):
            await (await start_job(client, control, "metrics")).result()
            url = f"http://{served.metrics_address}/metrics"
            return await asyncio.to_thread(lambda: urllib.request.urlopen(url).read().decode())

    text = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    assert any(line.startswith("temporal_workflow_completed") for line in lines)
    [count] = [line for line in lines if line.startswith("wdf_workflow_history_events_count")]
    assert 'workflow_type="JobWorkflow"' in count
    assert float(count.rsplit(" ", 1)[1]) > 0
    for line in lines:
        if line.startswith("wdf_"):
            assert not any(f"{label}=" in line for label in ("job_id", "jobid", "taskid"))


@pytest.mark.integration
def test_ready_file_while_polling(temporal, config, tmp_path):
    """The ready file exists once the workers poll and is gone once the service stops, and a
    namespace the server does not know fails the start without it, even when a killed process
    left one behind."""
    ready = tmp_path / "ready"
    # Left by a process that was killed, so it never removed its file.
    ready.touch()
    unknown = dataclasses.replace(config("unknown"), namespace="no-such-namespace")
    with pytest.raises(RPCError, match="no-such-namespace"):
        asyncio.run(serve(unknown, [], None, lambda heartbeat: {}, ready_file=ready))
    assert not ready.exists()

    async def body():
        client = await connect(temporal)
        stop = asyncio.Event()
        control = FakeControl()
        service = asyncio.create_task(
            serve(
                config("ready"),
                control.control_activities(),
                control.emit,
                lambda heartbeat: {},
                stop=stop,
                ready_file=ready,
            )
        )
        while not ready.exists():
            if service.done():
                await service
            await asyncio.sleep(0.1)
        polled = await _polled(client, "wdf-control-ready")
        stop.set()
        await service
        return polled

    assert asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert not ready.exists()


def test_missing_env_refused():
    """The service names every missing variable and exits 2 before it connects anywhere."""
    environ = {k: v for k, v in os.environ.items() if not k.startswith("WDF_")}
    given = {name: "x" for name in [*ENV.values(), *REAPER_ENV]}
    given |= {"WDF_TEMPORAL_ADDRESS": "127.0.0.1:1", "WDF_WORKFLOW_DSN": "", "WDF_REAPER_DSN": ""}
    del given["WDF_BUILD_ID"]
    done = subprocess.run(
        [sys.executable, "-m", "wdf_workflow.service"],
        env=environ | given,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 2, done.stderr
    assert done.stderr.strip().endswith(
        "missing environment: WDF_BUILD_ID, WDF_WORKFLOW_DSN, WDF_REAPER_DSN"
    )
    with pytest.raises(MissingEnv, match="WDF_BUILD_ID"):
        Config.from_env(given)
    filled = {"WDF_BUILD_ID": "b1", "WDF_WORKFLOW_DSN": "d", "WDF_REAPER_DSN": "r"}
    assert Config.from_env(given | filled).build_id == "b1"


def test_real_control_serves_every_name(tmp_path):
    """The real control activities cover every control activity of naming.yaml, and emit."""
    for role in ("workflow", "indexer"):
        dump_credentials(tmp_path / role, {role: Creds("key", "secret")})
    config = Config(
        **dict.fromkeys(ENV, "unused")
        | {
            "store_endpoint": "http://127.0.0.1:1",
            "workflow_credentials": str(tmp_path / "workflow"),
            "indexer_credentials": str(tmp_path / "indexer"),
        }
    )
    activities, emit = real_control(config)
    names = [activity._Definition.from_callable(fn).name for fn in [*activities, emit]]
    assert names == [a.name for a in naming().activities.control] + ["emit"]


def test_build_id_other_than_env_refused(monkeypatch):
    """A build id that is not the environment's, where the workflow reads its control queue, is
    refused before anything connects, and the printed configuration holds no DSN."""
    dsn = {"workflow_dsn": "postgresql://u:secret@db/app"}
    other = Config(**dict.fromkeys(ENV, "unused") | {"build_id": "other"} | dsn)
    monkeypatch.setenv("WDF_BUILD_ID", "env-build")
    with pytest.raises(ValueError, match="build id other is not the environment's env-build"):
        asyncio.run(serve(other, [], None, None))
    assert "secret" not in repr(other)
