"""Stand-in job load against a deployed Temporal frontend.

Each job is one workflow with two tasks, compile then simulate, each preceded by a `place` local
activity and running 50 ms of simulated work on a pool queue. At most 100 workflows are in flight.
The workflow worker and the pool worker run in this process on the host.
Run in the dev shell from the repository root, with the frontend forwarded to 127.0.0.1:
  nix develop -c uv run --frozen python docs/delivery/spikes/S10/code/load.py ADDRESS JOBS
Prints `figure <name> <value>` lines. Exit 0 every job finished, 1 otherwise.
"""

import asyncio
import sys
import time
import uuid
from datetime import timedelta

from google.protobuf.duration_pb2 import Duration
from temporalio import activity, workflow
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

NAMESPACE = "s10"
WORKFLOW_QUEUE = "s10-workflow"
POOL_QUEUE = "s10-pool"
IN_FLIGHT = 100
RETRY = RetryPolicy(maximum_attempts=5)


@activity.defn
async def place(task: str) -> str:
    return POOL_QUEUE


@activity.defn
async def run_task(task: str) -> str:
    await asyncio.sleep(0.05)
    return task


@workflow.defn
class StandInJob:
    @workflow.run
    async def run(self, job: str) -> int:
        for task in ("compile", "simulate"):
            queue = await workflow.execute_local_activity(
                place, task, start_to_close_timeout=timedelta(seconds=10), retry_policy=RETRY
            )
            await workflow.execute_activity(
                run_task,
                task,
                task_queue=queue,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY,
            )
        return 2


def figure(name: str, value) -> None:
    print(f"figure {name} {value}", flush=True)


async def register(address: str) -> None:
    request = RegisterNamespaceRequest(
        namespace=NAMESPACE, workflow_execution_retention_period=Duration(seconds=86400)
    )
    # The port-forward may still be starting, so the first connect is retried.
    for attempt in range(30):
        try:
            client = await Client.connect(address, namespace="default")
            break
        except RuntimeError:
            if attempt == 29:
                raise
            await asyncio.sleep(2)
    try:
        await client.workflow_service.register_namespace(request)
    except RPCError as e:
        if e.status != RPCStatusCode.ALREADY_EXISTS:
            raise
    # A new namespace reaches the frontend through its namespace cache, which refreshes on a timer.
    for _ in range(60):
        try:
            c = await Client.connect(address, namespace=NAMESPACE)
            await c.count_workflows()
            return
        except RPCError:
            await asyncio.sleep(2)
    raise RuntimeError(f"namespace {NAMESPACE} not ready")


async def main(address: str, jobs: int) -> int:
    await register(address)
    client = await Client.connect(address, namespace=NAMESPACE)
    run_id = uuid.uuid4().hex[:8]
    gate = asyncio.Semaphore(IN_FLIGHT)
    finished = failed = 0

    async def one(i: int) -> None:
        nonlocal finished, failed
        async with gate:
            try:
                await client.execute_workflow(
                    StandInJob.run,
                    str(i),
                    id=f"s10-{run_id}-{i}",
                    task_queue=WORKFLOW_QUEUE,
                    execution_timeout=timedelta(minutes=10),
                )
                finished += 1
            except Exception as e:
                failed += 1
                print(f"job {i} failed: {e!r}", file=sys.stderr, flush=True)

    async def progress() -> None:
        while True:
            await asyncio.sleep(15)
            print(f"progress finished={finished} failed={failed}", flush=True)

    workflows = Worker(
        client,
        task_queue=WORKFLOW_QUEUE,
        workflows=[StandInJob],
        activities=[place],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    pool = Worker(client, task_queue=POOL_QUEUE, activities=[run_task])
    async with workflows, pool:
        reporter = asyncio.create_task(progress())
        start = time.monotonic()
        await asyncio.gather(*(one(i) for i in range(jobs)))
        elapsed = time.monotonic() - start
        reporter.cancel()
    figure("jobs_started", jobs)
    figure("jobs_finished", finished)
    figure("jobs_failed", failed)
    figure("load_seconds", round(elapsed, 1))
    figure("jobs_per_second", round(finished / elapsed, 1))
    return 0 if finished == jobs else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], int(sys.argv[2]))))
