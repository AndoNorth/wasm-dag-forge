"""The Schedule, its workflow and its activity, on a real Temporal dev server and an in-process
worker whose pass is a function the test injects."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.client import Client, ScheduleOverlapPolicy
from wdf_artifactreaper import schedule
from wdf_artifactreaper.__main__ import main

pytestmark = pytest.mark.integration

REPORT = {"results_jobs": 3, "ran": True}


@pytest.fixture(scope="module")
def temporal(t1):
    """One dev server for this module, which is gone before other modules run."""
    with t1.container(
        "temporalio/temporal",
        ports={0: 7233},
        command=("server", "start-dev", "--ip", "0.0.0.0", "--headless"),
    ) as container:
        container.wait_ready(0)
        yield f"127.0.0.1:{container.host_ports[0]}"


class Passes:
    """The injected pass: it counts its calls, fails some of them, and can hold until released."""

    def __init__(self, fail_first=0, hold=False):
        self.calls = 0
        self.fail_first = fail_first
        self.started = threading.Event()
        self.release = threading.Event()
        if not hold:
            self.release.set()

    def __call__(self, heartbeat):
        self.calls += 1
        self.started.set()
        heartbeat()
        if self.calls <= self.fail_first:
            raise RuntimeError("the store was unreachable")
        assert self.release.wait(30)
        return REPORT


async def connect(address: str) -> Client:
    for _ in range(120):
        try:
            return await Client.connect(address)
        except RuntimeError:
            await asyncio.sleep(0.5)
    raise AssertionError("the Temporal server did not answer")


def scenario(temporal, body, run_pass):
    """Runs `body(client)` with the worker serving `run_pass`, against a schedule that is deleted
    afterwards, so every test starts from none."""

    async def run():
        client = await connect(temporal)
        with ThreadPoolExecutor(max_workers=2) as executor:
            async with schedule.build_worker(client, run_pass, executor):
                try:
                    return await body(client)
                finally:
                    try:
                        await client.get_schedule_handle(schedule.SCHEDULE_ID).delete()
                    except Exception:  # noqa: BLE001 - the test may have no schedule to delete
                        pass

    return asyncio.run(run())


async def schedules(client: Client) -> list[str]:
    return [s.id async for s in await client.list_schedules() if s.id == schedule.SCHEDULE_ID]


async def started(client: Client) -> int:
    return len([w async for w in client.list_workflows(f"WorkflowType = '{schedule.WORKFLOW}'")])


def test_ensure_twice_leaves_one_schedule(temporal):
    async def body(client):
        await schedule.ensure_schedule(client)
        await schedule.ensure_schedule(client)
        description = await client.get_schedule_handle(schedule.SCHEDULE_ID).describe()
        policy = description.schedule.policy
        assert await schedules(client) == [schedule.SCHEDULE_ID]
        assert description.schedule.spec.intervals[0].every == schedule.INTERVAL
        assert policy.overlap == ScheduleOverlapPolicy.SKIP
        assert policy.catchup_window == schedule.CATCHUP_WINDOW

    scenario(temporal, body, Passes())


def test_trigger_runs_one_pass_and_returns_its_report(temporal):
    passes = Passes()

    async def body(client):
        await schedule.ensure_schedule(client)
        assert await schedule.trigger_schedule(client) == REPORT

    scenario(temporal, body, passes)
    assert passes.calls == 1


def test_overlapping_trigger_is_skipped(temporal):
    passes = Passes(hold=True)

    async def body(client):
        await schedule.ensure_schedule(client)
        first = asyncio.create_task(schedule.trigger_schedule(client))
        await asyncio.to_thread(passes.started.wait, 30)
        assert await schedule.trigger_schedule(client) is None
        passes.release.set()
        assert await first == REPORT

    scenario(temporal, body, passes)
    assert passes.calls == 1


def test_activity_failure_is_retried(temporal):
    passes = Passes(fail_first=2)

    async def body(client):
        await schedule.ensure_schedule(client)
        assert await schedule.trigger_schedule(client) == REPORT

    scenario(temporal, body, passes)
    assert passes.calls == 3


def test_paused_schedule_runs_nothing(temporal):
    passes = Passes()

    async def body(client):
        await schedule.ensure_schedule(client)
        handle = client.get_schedule_handle(schedule.SCHEDULE_ID)
        before = await started(client)
        await handle.pause()
        assert await schedule.trigger_schedule(client) is None
        await schedule.ensure_schedule(client)
        assert (await handle.describe()).schedule.state.paused
        assert await started(client) == before

    scenario(temporal, body, passes)
    assert passes.calls == 0


def test_the_command_reports_an_unreachable_server():
    assert main(["schedule", "trigger", "--temporal-address", "127.0.0.1:1"]) == 3
