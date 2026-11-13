"""Records the histories of JobWorkflow into a new release of the corpus, one per behaviour, on a
dev server with StaticPlacer, FakeControl and a FakePool per pool.

    uv run --frozen python packages/workflow/tests/replay/record.py <release>

The workers run as the service runs them: the workflow worker versioned under the build label of
`wdf_temporal.corpus`, the tracing interceptor on the client, and a fixed identity, so no host
name or process id lands in a committed history. A release that exists already is refused, since
the corpus only grows.
"""

import asyncio
import json
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkerDeploymentVersion
from temporalio.worker import Worker, WorkerDeploymentConfig
from wdf_events import Event, build_event
from wdf_events.models import JobParams
from wdf_jobdoc.plan_models import Plan
from wdf_keys import workflow_id
from wdf_temporal import event_data_converter, tracing_interceptor
from wdf_temporal.corpus import IDENTITY, build_label, workflow_type_of
from wdf_workflow import versioning
from wdf_workflow.contract import naming
from wdf_workflow.job import JobWorkflow
from wdf_workflow.models import ControlSignal, ResumeSignal
from wdf_workflow.placement import StaticPlacer, place_activity
from wdf_workflow.taskpath import control_queue
from wdf_workflow.testing import Fail, FakeControl, FakePool, Hang, Succeed
from wdf_workflow.testing.control import LoadedPlan

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
HASH = "a" * 64
SOURCE = {
    "uri": f"s3://wdf-sources/sha256/aa/{HASH}",
    "sha256": HASH,
    "size": 2048,
    "name": "bundle.tar.gz",
    "kind": "source",
}
PARAMS = {
    "compile": {"source": SOURCE, "entry": "sim"},
    "simulate": {"workload": "pi", "args": {"n": 1000}, "seed": 7},
    "aggregate": {"aggregation": "digest_list"},
}
RESOURCES = {
    "compile": {"cpu_milli": 1000, "memory_mib": 1024},
    "simulate": {"cpu_milli": 250, "memory_mib": 384},
    "aggregate": {"cpu_milli": 1000, "memory_mib": 1024},
}
OUTPUT = {"compile": "component", "simulate": "result"}
# A compile, two simulate tasks over it and an aggregate over them.
CHAIN = {
    "build": ("compile", []),
    "sim-a": ("simulate", ["build"]),
    "sim-b": ("simulate", ["build"]),
    "agg": ("aggregate", ["sim-a", "sim-b"]),
}
ONE = {"sim": ("simulate", [])}
# A guest failure retried from the guest budget, and the same failure handing over a checkpoint.
TIMED_OUT = Fail("timeout")
CHECKPOINTED = Fail("timeout", checkpoint=True)
LIMIT_S = 180


def plan(
    tasks: dict[str, tuple[str, list[str]]],
    *,
    retries: int = 1,
    checkpoint: frozenset[str] = frozenset(),
    allow_partial: frozenset[str] = frozenset(),
) -> Plan:
    """A bound plan from `{id: (type, depends_on)}`, whose inputs are the declared output of each
    dependency."""
    return Plan.model_validate(
        {
            "plan_version": 1,
            "profile": "ci",
            "max_parallel": 8,
            "parameters": {},
            "tasks": [
                {
                    "id": tid,
                    "type": kind,
                    "pool": kind,
                    "depends_on": sorted(deps),
                    "inputs": [
                        {"task": dep, "output": OUTPUT[tasks[dep][0]]} for dep in sorted(deps)
                    ],
                    **RESOURCES[kind],
                    "run_timeout_s": 600,
                    "retries": retries,
                    "checkpoint": tid in checkpoint,
                    "allow_partial": tid in allow_partial,
                    "params": PARAMS[kind],
                }
                for tid, (kind, deps) in tasks.items()
            ],
        }
    )


def accepted() -> Event:
    """The accepted event of a new job, whose manifest is named by a hash of its own."""
    sha256 = uuid.uuid4().hex * 2
    return build_event(
        "job.accepted",
        source="/wdf/dispatch",
        tenant="acme",
        jobid=str(uuid.uuid4()),
        priorityclass="standard",
        jobclass="simulate",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
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


class Rig:
    """The workflow worker of one build, the control worker and a FakePool per pool, whose
    scripts a recording swaps."""

    def __init__(self, client: Client, build: str):
        self.client = client
        self.control = FakeControl()
        self.scripts: dict[str, Callable] = {}
        self.workers = [
            Worker(
                client,
                task_queue=naming().workflow.queue,
                workflows=[JobWorkflow],
                activities=[place_activity(StaticPlacer()), self.control.emit],
                deployment_config=WorkerDeploymentConfig(
                    version=WorkerDeploymentVersion(naming().workflow.deployment, build),
                    use_worker_versioning=True,
                ),
            ),
            Worker(
                client,
                task_queue=control_queue(),
                activities=self.control.control_activities(),
            ),
            *(FakePool(client, pool, self.run(pool)) for pool in naming().pools),
        ]

    def run(self, pool: str) -> Callable:
        return lambda task: self.scripts.get(pool, lambda _: Succeed())(task)

    async def start(self, job: Plan) -> WorkflowHandle:
        """Starts a job as admission would, on the one job row of FakeControl claimed again."""
        event = accepted()
        self.control.status = "admitted"
        self.control.plans[event.data["manifest"]["uri"]] = LoadedPlan(
            plan=job, max_history_events=10_000
        )
        return await self.client.start_workflow(
            JobWorkflow.run,
            args=[event, JobParams(seed=7, parameters={})],
            id=workflow_id(event.jobid),
            task_queue=naming().workflow.queue,
        )


async def until(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(60):
        while not await check():
            await asyncio.sleep(0.2)


async def paused(handle: WorkflowHandle) -> None:
    async def holds() -> bool:
        try:
            return (await handle.query(JobWorkflow.status)).job_status == "paused"
        except Exception:  # noqa: BLE001 - a query before the plan loads is refused
            return False

    await until(holds)


async def plain(rig: Rig) -> WorkflowHandle:
    """Every task succeeds at its first attempt."""
    return await rig.start(plan(CHAIN))


async def retry(rig: Rig) -> WorkflowHandle:
    """A simulate task times out once and succeeds at its second attempt."""
    rig.scripts["simulate"] = lambda t: TIMED_OUT if t.task_attempt == 1 else Succeed()
    return await rig.start(plan(ONE))


async def cache_hit(rig: Rig) -> WorkflowHandle:
    """A second job's compile is skipped on the cache entry the first job recorded."""
    await (await rig.start(plan(CHAIN))).result()
    return await rig.start(plan(CHAIN))


async def partial(rig: Rig) -> WorkflowHandle:
    """An `allow_partial` aggregate runs over the input that succeeded and the job ends
    `partial`."""
    rig.scripts["simulate"] = lambda t: (
        Fail("guest_memory_cap") if t.request.taskid == "sim-b" else Succeed()
    )
    return await rig.start(plan(CHAIN, allow_partial=frozenset({"agg"})))


async def cancel(rig: Rig) -> WorkflowHandle:
    """A cancel signal stops a task in flight and the job ends `cancelled`."""
    rig.scripts["simulate"] = lambda t: Hang()
    handle = await rig.start(plan(ONE))
    simulate = next(w for w in rig.workers if isinstance(w, FakePool) and w.pool == "simulate")

    async def placed() -> bool:
        return bool(simulate.calls)

    await until(placed)
    await handle.signal(JobWorkflow.cancel, ControlSignal(control_seq=1, reason="recorded"))
    return handle


async def pause_resume(rig: Rig) -> WorkflowHandle:
    """A checkpointed task exhausts its guest budget, the job pauses, and a resume granted as
    control grants it runs the task again from its checkpoint to success."""
    rig.scripts["simulate"] = lambda t: CHECKPOINTED if t.task_attempt == 1 else Succeed()
    handle = await rig.start(plan(ONE, retries=0, checkpoint=frozenset({"sim"})))
    await paused(handle)
    rig.control.status = "resume-pending"
    await handle.signal(JobWorkflow.resume, ResumeSignal(control_seq=1, manifest=None))
    return handle


RECORDINGS: dict[str, Callable[[Rig], Awaitable[WorkflowHandle]]] = {
    "plain": plain,
    "retry": retry,
    "cache-hit": cache_hit,
    "partial-aggregate": partial,
    "cancel": cancel,
    "pause-resume": pause_resume,
}
ENDS = {
    "plain": "succeeded",
    "retry": "succeeded",
    "cache-hit": "succeeded",
    "partial-aggregate": "partial",
    "cancel": "cancelled",
    "pause-resume": "succeeded",
}


def scrubbed(node):
    """The history without the stack trace of any failure, which names the paths of the recording
    machine and which replay never reads."""
    if isinstance(node, dict):
        return {k: "" if k == "stackTrace" else scrubbed(v) for k, v in node.items()}
    if isinstance(node, list):
        return [scrubbed(v) for v in node]
    return node


async def _record(address: str, directory: Path, build: str) -> list[Path]:
    client = None
    for _ in range(120):
        try:
            client = await Client.connect(
                address,
                namespace=naming().namespace,
                data_converter=event_data_converter,
                interceptors=[tracing_interceptor()],
                identity=IDENTITY,
            )
            break
        except RuntimeError:
            await asyncio.sleep(0.5)
    if client is None:
        raise RuntimeError("the Temporal server did not answer")
    written = []
    for name, scenario in RECORDINGS.items():
        rig = Rig(client, build)
        for worker in rig.workers:
            await worker.__aenter__()
        try:
            await versioning.set_current(client, build)
            handle = await scenario(rig)
            async with asyncio.timeout(LIMIT_S):
                status = await handle.result()
            if status != ENDS[name]:
                raise RuntimeError(f"{name} ended {status}, not {ENDS[name]}")
            history = await handle.fetch_history()
        finally:
            for worker in reversed(rig.workers):
                await worker.__aexit__(None, None, None)
        path = directory / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "build": build,
                    "workflow_type": workflow_type_of(JobWorkflow),
                    "history": scrubbed(json.loads(history.to_json())),
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )
        written.append(path)
    return written


def record(release: str, root: Path = CORPUS) -> list[Path]:
    """Records every behaviour of RECORDINGS into `root/<release>/`, in one T1 container."""
    from wdf_itest import Session

    build = build_label()
    os.environ[naming().workflow.build_id_env] = build
    directory = root / release
    directory.mkdir(parents=True)
    with Session() as t1:
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
            address = f"127.0.0.1:{container.host_ports[0]}"
            return asyncio.run(_record(address, directory, build))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: record.py <release>")
    for written in record(sys.argv[1]):
        print(written.relative_to(CORPUS.parent))
