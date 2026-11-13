"""Worker versioning on a dev server: the current build at the first deploy and at a rollout, a job
pinned to the build that started it, the drained count, the move of an open job to a fixed build
and back, and the one versioned queue of the service.

Each test has a dev server of its own, since the worker deployment, its current build and its
counts belong to the server. Every build runs in this process with StaticPlacer, one control worker
and FakePool workers, so all builds share the control queue of the environment's build id.
"""

import asyncio
import socket
import time
import uuid
from collections import defaultdict
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType, TaskQueueType, WorkflowTaskFailedCause
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client, WorkflowHandle, WorkflowQueryFailedError
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.worker import UnsandboxedWorkflowRunner, Worker, WorkerDeploymentConfig
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from wdf_artifactreaper import schedule
from wdf_events import Event, build_event
from wdf_events.models import JobParams
from wdf_jobdoc.plan_models import Plan
from wdf_keys import workflow_id
from wdf_workflow import versioning
from wdf_workflow.contract import naming
from wdf_workflow.job import JobWorkflow
from wdf_workflow.placement import StaticPlacer, place_activity
from wdf_workflow.service import Config, serve
from wdf_workflow.testing import Fail, FakeControl, FakePool, Succeed, connect
from wdf_workflow.testing.control import Emit, Emitted, LoadedPlan

pytestmark = pytest.mark.integration

LIMIT_S = 180
# The control queue every build of these tests uses.
CONTROL_BUILD = "versioning"
HASH = "a" * 64


@pytest.fixture
def server(t1) -> str:
    """The address of a fresh dev server with the namespace of naming.yaml."""
    with t1.container(
        "temporalio/temporal",
        ports={0: 7233},
        command=(
            "server",
            "start-dev",
            "--ip",
            "0.0.0.0",
            "--headless",
            "--namespace",
            naming().namespace,
        ),
    ) as container:
        container.wait_ready(0)
        yield f"127.0.0.1:{container.host_ports[0]}"


@pytest.fixture(autouse=True)
def control_build(monkeypatch):
    monkeypatch.setenv(naming().workflow.build_id_env, CONTROL_BUILD)


class Rows(FakeControl):
    """FakeControl with a job row per job, each claimed as admitted, so jobs run side by side."""

    def __init__(self):
        super().__init__()
        self.rows: dict[str, str] = defaultdict(lambda: "admitted")

    @activity.defn(name="emit")
    async def emit(self, request: Emit) -> Emitted:
        self.status = self.rows[request.job_id]
        emitted = await FakeControl.emit(self, request)
        self.rows[request.job_id] = self.status
        return emitted


@workflow.defn(name=naming().workflow.type, versioning_behavior=VersioningBehavior.PINNED)
class Unguarded(JobWorkflow):
    """A build whose change before the first activity is not guarded by `workflow.patched`, so it
    cannot replay a history that another build started."""

    @workflow.run
    async def run(self, accepted: Event, params: JobParams) -> str:
        await workflow.sleep(0.1)
        return await super().run(accepted, params)


def plan(checkpoint: bool = False) -> Plan:
    """A compile task, then a simulate task over its component, checkpointed when asked."""
    task = {"run_timeout_s": 600, "retries": 1, "allow_partial": False}
    return Plan.model_validate(
        {
            "plan_version": 1,
            "profile": "ci",
            "max_parallel": 2,
            "parameters": {},
            "tasks": [
                {
                    **task,
                    "id": "build",
                    "type": "compile",
                    "pool": "compile",
                    "depends_on": [],
                    "inputs": [],
                    "cpu_milli": 1000,
                    "memory_mib": 1024,
                    "checkpoint": False,
                    "params": {
                        "source": {
                            "uri": f"s3://wdf-sources/sha256/aa/{HASH}",
                            "sha256": HASH,
                            "size": 2048,
                            "name": "bundle.tar.gz",
                            "kind": "source",
                        },
                        "entry": "sim",
                    },
                },
                {
                    **task,
                    "id": "sim",
                    "type": "simulate",
                    "pool": "simulate",
                    "depends_on": ["build"],
                    "inputs": [{"task": "build", "output": "component"}],
                    "cpu_milli": 250,
                    "memory_mib": 384,
                    "checkpoint": checkpoint,
                    "params": {"workload": "pi", "args": {"n": 1000}, "seed": 7},
                },
            ],
        }
    )


def build_worker(client: Client, build_id: str, control: FakeControl, workflow_class=JobWorkflow):
    """The versioned workflow worker of a build, as the service runs it."""
    return Worker(
        client,
        task_queue=naming().workflow.queue,
        workflows=[workflow_class],
        activities=[place_activity(StaticPlacer()), control.emit],
        # The unguarded build is defined in this module, which the sandbox cannot re-import.
        workflow_runner=(
            SandboxedWorkflowRunner()
            if workflow_class is JobWorkflow
            else UnsandboxedWorkflowRunner()
        ),
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(naming().workflow.deployment, build_id),
            use_worker_versioning=True,
        ),
    )


@asynccontextmanager
async def running(*workers):
    async with AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        yield


def control_worker(client: Client, control: FakeControl) -> Worker:
    return Worker(
        client,
        task_queue=naming().control_queue.replace("<build id>", CONTROL_BUILD),
        activities=control.control_activities(),
    )


async def start_job(client: Client, control: FakeControl, job_plan: Plan) -> WorkflowHandle:
    """Starts a job as admission does, with no pin, so the current build takes it."""
    sha256 = uuid.uuid4().hex * 2
    accepted = build_event(
        "job.accepted",
        source="/wdf/dispatch",
        tenant="acme",
        jobid=str(uuid.uuid4()),
        priorityclass="standard",
        jobclass="simulate",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
        data={
            "manifest": {
                "uri": f"s3://wdf-sources/sha256/{sha256[:2]}/{sha256}",
                "sha256": sha256,
                "size": 2048,
                "name": "manifest.json",
                "kind": "manifest",
            },
            "batch_id": str(uuid.uuid4()),
            "submission_key": f"sub-{uuid.uuid4()}",
        },
    )
    control.plans[accepted.data["manifest"]["uri"]] = LoadedPlan(
        plan=job_plan, max_history_events=10_000
    )
    return await client.start_workflow(
        JobWorkflow.run,
        args=[accepted, JobParams(seed=7, parameters={})],
        id=workflow_id(accepted.jobid),
        task_queue=naming().workflow.queue,
    )


async def history(handle: WorkflowHandle) -> list:
    return [event async for event in handle.fetch_history_events()]


def task_builds(events: list) -> list[str]:
    """The build of each completed workflow task, in order."""
    return [
        e.workflow_task_completed_event_attributes.deployment_version.build_id
        for e in events
        if e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED
    ]


async def waiting_on(handle: WorkflowHandle, queue: str, timeout_s: float = 60) -> None:
    """Waits until the job has scheduled an activity on `queue`."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for event in await history(handle):
            if (
                event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                and event.activity_task_scheduled_event_attributes.task_queue.name == queue
            ):
                return
        await asyncio.sleep(0.3)
    raise TimeoutError(f"{handle.id} never scheduled an activity on {queue}")


async def paused(handle: WorkflowHandle, timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if (await handle.query(JobWorkflow.status)).job_status == "paused":
                return
        except WorkflowQueryFailedError:
            # The plan is not loaded yet.
            pass
        await asyncio.sleep(0.3)
    raise TimeoutError(f"{handle.id} never paused")


async def counted(
    client: Client, build_id: str, pinned: int, timeout_s: float = 30
) -> versioning.Drainage:
    """The drainage of the build once its count reaches `pinned`, since visibility follows a
    workflow's close a moment later, or the last one read."""
    deadline = time.monotonic() + timeout_s
    while (drained := await versioning.drainage(client, build_id)).pinned != pinned:
        if time.monotonic() > deadline:
            break
        await asyncio.sleep(0.3)
    return drained


def succeed(task) -> Succeed:
    return Succeed()


SIMULATE = naming().pools["simulate"]


def test_first_deploy_sets_current(server):
    """A deployment with no current build gives a new job no task, the raw call makes the first
    build current and the job runs; a rollout's call waits for the new build's pollers."""

    async def body():
        client = await connect(server)
        control = Rows()
        pools = [FakePool(client, pool, succeed) for pool in naming().pools]
        async with running(
            build_worker(client, "exit-a", control), control_worker(client, control), *pools
        ):
            await versioning.wait_pollers(client, "exit-a")
            before = await versioning.current(client)
            handle = await start_job(client, control, plan())
            await asyncio.sleep(3)
            tasks_before = task_builds(await history(handle))
            replaced_first = await versioning.set_current(client, "exit-a")
            status = await handle.result()

            # The next build is made current only once its pollers are seen.
            rollout = asyncio.create_task(versioning.set_current(client, "exit-b"))
            await asyncio.sleep(2)
            early = rollout.done(), await versioning.current(client)
            async with running(build_worker(client, "exit-b", control)):
                replaced = await rollout
                again = await versioning.set_current(client, "exit-b")
                return (
                    before,
                    tasks_before,
                    replaced_first,
                    status,
                    early,
                    replaced,
                    again,
                    await versioning.current(client),
                )

    before, tasks_before, replaced_first, status, early, replaced, again, now = asyncio.run(
        asyncio.wait_for(body(), LIMIT_S)
    )
    assert before is None
    assert tasks_before == []
    assert replaced_first is None
    assert status == "succeeded"
    assert early == (False, "exit-a")
    assert replaced == "exit-a"
    assert again == "exit-b"
    assert now == "exit-b"


def test_pinned_finishes_on_old_build(server):
    """A job open when the next build becomes current finishes on the build that started it, and a
    job started after runs on the next build."""

    async def body():
        client = await connect(server)
        control = Rows()
        async with running(
            build_worker(client, "exit-a", control),
            control_worker(client, control),
            FakePool(client, "compile", succeed),
        ):
            await versioning.set_current(client, "exit-a")
            open_job = await start_job(client, control, plan())
            await waiting_on(open_job, SIMULATE)
            async with running(build_worker(client, "exit-b", control)):
                await versioning.set_current(client, "exit-b")
                async with running(FakePool(client, "simulate", succeed)):
                    new_job = await start_job(client, control, plan())
                    statuses = await open_job.result(), await new_job.result()
                return (
                    statuses,
                    task_builds(await history(open_job)),
                    task_builds(await history(new_job)),
                    await counted(client, "exit-a", 0),
                )

    statuses, open_builds, new_builds, drained = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert statuses == ("succeeded", "succeeded")
    assert len(open_builds) > 2 and set(open_builds) == {"exit-a"}
    assert new_builds and set(new_builds) == {"exit-b"}
    assert drained.pinned == 0


def test_drain_detects_no_pinned(server):
    """The old build counts its open pinned jobs, a paused one included, is drained at a count of
    0 while the server's own status lags, and only then is its Deployment deleted."""
    deleted: list[str] = []

    async def body():
        client = await connect(server)
        control = Rows()
        fail = FakePool(client, "simulate", lambda task: Fail("guest_memory_cap"))
        async with running(
            build_worker(client, "exit-a", control),
            control_worker(client, control),
            FakePool(client, "compile", succeed),
        ):
            await versioning.set_current(client, "exit-a")
            async with running(fail):
                paused_job = await start_job(client, control, plan(checkpoint=True))
                await paused(paused_job)
            open_job = await start_job(client, control, plan())
            await waiting_on(open_job, SIMULATE)
            async with running(build_worker(client, "exit-b", control)):
                await versioning.set_current(client, "exit-b")
                busy = await counted(client, "exit-a", 2)
                with pytest.raises(versioning.NotDrained):
                    await versioning.retire(client, "exit-a", deleted.append)
                moved = await versioning.move(client, paused_job.id, "exit-b", wait_s=2)
                after_move = await counted(client, "exit-a", 1)
                async with running(FakePool(client, "simulate", succeed)):
                    status = await open_job.result()
                await counted(client, "exit-a", 0)
                started = time.monotonic()
                drained = await versioning.drainage(client, "exit-a")
                took = time.monotonic() - started
                retired = await versioning.retire(client, "exit-a", deleted.append)
                with pytest.raises(ValueError, match="current"):
                    await versioning.retire(client, "exit-b", deleted.append)
                return (
                    busy,
                    moved,
                    after_move,
                    status,
                    drained,
                    took,
                    retired,
                    await counted(client, "exit-b", 1),
                )

    busy, moved, after_move, status, drained, took, retired, on_b = asyncio.run(
        asyncio.wait_for(body(), LIMIT_S)
    )
    assert (busy.pinned, busy.drained) == (2, False)
    # A paused job waits on a signal, so it runs on the fixed build only at its next task.
    assert (moved.from_build, moved.state) == ("exit-a", "overridden")
    assert after_move.pinned == 1
    assert status == "succeeded"
    assert (drained.pinned, drained.drained) == (0, True)
    assert drained.server_status in ("draining", "drained")
    assert took < 5
    assert (retired.build_id, retired.pinned) == ("exit-a", 0)
    assert deleted == ["exit-a"]
    assert on_b.pinned == 1


def test_override_procedure(server):
    """An override to a fixed build moves an open job at its next task, and an override to a build
    that cannot replay the history is put back on the build the job ran on."""

    async def body():
        client = await connect(server)
        control = Rows()
        async with running(
            build_worker(client, "exit-a", control),
            control_worker(client, control),
            FakePool(client, "compile", succeed),
        ):
            await versioning.set_current(client, "exit-a")
            fixed, broken = [await start_job(client, control, plan()) for _ in range(2)]
            for handle in (fixed, broken):
                await waiting_on(handle, SIMULATE)
            async with running(
                build_worker(client, "exit-b", control),
                build_worker(client, "exit-c", control, Unguarded),
            ):
                for build in ("exit-b", "exit-c"):
                    await versioning.wait_pollers(client, build)
                landed = [
                    await versioning.move(client, fixed.id, "exit-b", wait_s=0),
                    await versioning.move(client, broken.id, "exit-c", wait_s=0),
                ]
                left_on_a = await counted(client, "exit-a", 0)
                async with running(FakePool(client, "simulate", succeed)):
                    settled = [
                        await versioning.move(client, fixed.id, "exit-b"),
                        await versioning.move(client, broken.id, "exit-c"),
                    ]
                    statuses = await fixed.result(), await broken.result()
                broken_history = await history(broken)
                failed = [
                    e.workflow_task_failed_event_attributes.cause
                    for e in broken_history
                    if e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED
                ]
                return (
                    landed,
                    left_on_a,
                    settled,
                    statuses,
                    task_builds(await history(fixed)),
                    task_builds(broken_history),
                    failed,
                )

    landed, left_on_a, settled, statuses, fixed_builds, broken_builds, failed = asyncio.run(
        asyncio.wait_for(body(), LIMIT_S)
    )
    assert [(m.from_build, m.to_build, m.state) for m in landed] == [
        ("exit-a", "exit-b", "overridden"),
        ("exit-a", "exit-c", "overridden"),
    ]
    # The count follows the override before either job runs again.
    assert left_on_a.pinned == 0
    assert [(m.from_build, m.to_build, m.state) for m in settled] == [
        ("exit-a", "exit-b", "moved"),
        ("exit-a", "exit-c", "reverted"),
    ]
    assert statuses == ("succeeded", "succeeded")
    assert fixed_builds[0] == "exit-a" and fixed_builds[-1] == "exit-b"
    assert set(broken_builds) == {"exit-a"}
    assert failed and all(
        cause == WorkflowTaskFailedCause.WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR
        for cause in failed
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def _pollers(client: Client, queue: str, kind: int, wait_s: float = 30) -> list:
    request = DescribeTaskQueueRequest(
        namespace=client.namespace, task_queue=TaskQueue(name=queue), task_queue_type=kind
    )
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if pollers := (await client.workflow_service.describe_task_queue(request)).pollers:
            return list(pollers)
        await asyncio.sleep(0.5)
    raise TimeoutError(f"nobody polls {queue}")


def test_only_workflow_queue_versioned(server, monkeypatch):
    """The server sees deployment options on the pollers of the service's workflow queue only: its
    control and reaper workers poll unversioned."""
    build_id = "exit-a"
    control_queue = naming().control_queue.replace("<build id>", build_id)
    monkeypatch.setenv(naming().workflow.build_id_env, build_id)
    config = Config(
        temporal_address=server,
        namespace=naming().namespace,
        build_id=build_id,
        metrics_address=f"127.0.0.1:{_free_port()}",
        workflow_dsn="postgresql://unused",
        artifacts_dsn="postgresql://unused",
        store_endpoint="http://127.0.0.1:1",
        workflow_credentials="/unused",
        indexer_credentials="/unused",
    )
    queues = {
        (naming().workflow.queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
        (control_queue, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
        (schedule.TASK_QUEUE, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
        (schedule.TASK_QUEUE, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
    }

    async def body():
        client = await connect(server)
        control = FakeControl()
        stop = asyncio.Event()
        service = asyncio.create_task(
            serve(
                config,
                control.control_activities(),
                control.emit,
                lambda heartbeat: {},
                stop=stop,
            )
        )
        try:
            return {
                (queue, TaskQueueType.Name(kind)): sorted(
                    {
                        (p.deployment_options.deployment_name, p.deployment_options.build_id)
                        for p in await _pollers(client, queue, kind)
                    }
                )
                for queue, kind in sorted(queues)
            }
        finally:
            stop.set()
            await service

    seen = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert seen == {
        (naming().workflow.queue, "TASK_QUEUE_TYPE_WORKFLOW"): [
            (naming().workflow.deployment, build_id)
        ],
        (control_queue, "TASK_QUEUE_TYPE_ACTIVITY"): [("", "")],
        (schedule.TASK_QUEUE, "TASK_QUEUE_TYPE_ACTIVITY"): [("", "")],
        (schedule.TASK_QUEUE, "TASK_QUEUE_TYPE_WORKFLOW"): [("", "")],
    }
