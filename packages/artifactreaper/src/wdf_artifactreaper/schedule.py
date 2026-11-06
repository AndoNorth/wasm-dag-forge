"""The Temporal Schedule that runs a reaper pass, and the worker that serves it.

The Schedule starts one workflow whose single activity runs a pass and heartbeats once per batch.
Overlapping starts are skipped, so a slow pass is never doubled, and the pass itself also holds
a database lock for the case of a second worker.
Names are constants until the workflow epic pins the Temporal naming.
"""

import asyncio
import dataclasses
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

STUBBED = ()

SCHEDULE_ID = "wdf-artifact-reaper"
TASK_QUEUE = "wdf-artifact-reaper"
WORKFLOW_ID = "wdf-artifact-reaper-pass"
WORKFLOW = "ReapWorkflow"
ACTIVITY = "reap_pass"

INTERVAL = timedelta(minutes=15)
CATCHUP_WINDOW = timedelta(hours=1)
# One pass is bounded by its batches, and a missing heartbeat means the worker is gone.
PASS_TIMEOUT = timedelta(hours=1)
HEARTBEAT_TIMEOUT = timedelta(minutes=2)
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)
TRIGGER_TIMEOUT = timedelta(seconds=30)

ENV_DSN = "WDF_REAPER_DSN"
ENV_ENDPOINT = "WDF_REAPER_ENDPOINT"
ENV_CREDENTIALS = "WDF_REAPER_CREDENTIALS"

# A pass receives the heartbeat to call after each batch and returns the counters of its report.
RunPass = Callable[[Callable[[], None]], dict[str, Any]]


@workflow.defn(name=WORKFLOW)
class ReapWorkflow:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        return await workflow.execute_activity(
            ACTIVITY,
            start_to_close_timeout=PASS_TIMEOUT,
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
            retry_policy=RETRY,
        )


def reap_activity(run_pass: RunPass) -> Callable[[], dict[str, Any]]:
    @activity.defn(name=ACTIVITY)
    def reap_pass() -> dict[str, Any]:
        return run_pass(activity.heartbeat)

    return reap_pass


def default_pass() -> RunPass:
    """A pass against the database and the store that the environment names.

    A missing variable raises KeyError when the worker starts, not when the first pass runs.
    """
    dsn, endpoint, credentials = (os.environ[k] for k in (ENV_DSN, ENV_ENDPOINT, ENV_CREDENTIALS))

    def run_pass(heartbeat: Callable[[], None]) -> dict[str, Any]:
        import psycopg
        from wdf_storecheck.s3 import client as s3_client
        from wdf_storecheck.s3 import load_credentials

        from .reap import ROLE, load_windows, reap_once

        with psycopg.connect(dsn, autocommit=True) as conn:
            report = reap_once(
                conn,
                s3_client(endpoint, load_credentials(credentials)[ROLE]),
                datetime.now(UTC),
                load_windows(),
                heartbeat=heartbeat,
            )
        return {**report.counters(), "ran": report.ran}

    return run_pass


def definition() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(WORKFLOW, id=WORKFLOW_ID, task_queue=TASK_QUEUE),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=INTERVAL)]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=CATCHUP_WINDOW),
    )


def build_worker(client: Client, run_pass: RunPass, executor: ThreadPoolExecutor) -> Worker:
    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ReapWorkflow],
        activities=[reap_activity(run_pass)],
        activity_executor=executor,
        max_concurrent_activities=1,
    )


async def ensure_schedule(client: Client) -> None:
    """Creates the Schedule, or converges an existing one to the definition and keeps it paused
    or running as it is."""
    try:
        await client.create_schedule(SCHEDULE_ID, definition())
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            state = current.description.schedule.state
            return ScheduleUpdate(schedule=dataclasses.replace(definition(), state=state))

        await client.get_schedule_handle(SCHEDULE_ID).update(converge)


async def trigger_schedule(client: Client) -> dict[str, Any] | None:
    """Starts one pass now and returns its report.

    Nothing runs, and None comes back, when the Schedule is paused or a pass is already running.
    """
    handle = client.get_schedule_handle(SCHEDULE_ID)
    before = await handle.describe()
    if before.schedule.state.paused:
        return None
    await handle.trigger(overlap=ScheduleOverlapPolicy.SKIP)
    deadline = asyncio.get_running_loop().time() + TRIGGER_TIMEOUT.total_seconds()
    while True:
        info = (await handle.describe()).info
        if info.num_actions > before.info.num_actions:
            break
        if info.num_actions_skipped_overlap > before.info.num_actions_skipped_overlap:
            return None
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("the Schedule neither started a pass nor skipped the trigger")
        await asyncio.sleep(0.2)
    started = info.recent_actions[-1].action
    return await client.get_workflow_handle(
        started.workflow_id, run_id=started.first_execution_run_id
    ).result()


def ensure(temporal_address: str) -> None:
    async def run() -> None:
        await ensure_schedule(await Client.connect(temporal_address))

    asyncio.run(run())


def trigger(temporal_address: str) -> dict[str, Any] | None:
    async def run() -> dict[str, Any] | None:
        return await trigger_schedule(await Client.connect(temporal_address))

    return asyncio.run(run())


def worker(temporal_address: str) -> None:
    """Serves the Schedule's task queue until interrupted."""
    run_pass = default_pass()

    async def run() -> None:
        client = await Client.connect(temporal_address)
        with ThreadPoolExecutor(max_workers=1) as executor:
            await build_worker(client, run_pass, executor).run()

    asyncio.run(run())
