"""The job workflow on a dev server: the ready loop over fan-out and fan-in under max_parallel, the
status query, the events against the stage contract, the workflow metrics and the defect rule of
emit."""

import asyncio

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.runtime import MetricBuffer, Runtime, TelemetryConfig
from wdf_events import Event
from wdf_events.models import ATTEMPT_TYPES
from wdf_stages import Facts, run_stage_contract
from wdf_stages.facts import SAMPLE_DATA
from wdf_stages.recording import type_name
from wdf_temporal import event_data_converter
from wdf_workflow.contract import naming
from wdf_workflow.job import Identity, JobWorkflow, start_facts
from wdf_workflow.testing import FakeControl, Zombie, connect

pytestmark = pytest.mark.integration

# A job of these tests ends within seconds, so a wedged workflow fails its test.
LIMIT_S = 120


def types(control: FakeControl, task: str | None = None) -> list[str]:
    """The event types FakeControl stored, of one task or of the job, in the order written."""
    return [type_name(e.type) for e in control.events.values() if e.taskid == task]


def test_fan_out_fan_in(temporal, rig, plan, fan):
    async def body():
        client = await connect(temporal)
        async with rig(client) as this:
            handle = await this.start(plan(fan))
            return this, await handle.result(), await handle.query(JobWorkflow.status)

    this, status, query = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    pools = this.pools
    assert [t.request.taskid for t in pools["compile"].calls] == ["build"]
    assert sorted(t.request.taskid for t in pools["simulate"].calls) == [
        "sim-a",
        "sim-b",
        "sim-c",
        "sim-d",
    ]
    [aggregate] = pools["aggregate"].calls
    component = this.control.committed[(aggregate.request.jobid, "build")][0]
    for task in pools["simulate"].calls:
        assert task.request.data["inputs"] == [component.model_dump(mode="json")]
        assert task.params.model_dump() == {"workload": "pi", "args": {"n": 1000}, "seed": 7}
    # The aggregate reads the one index that build_index put over the four results.
    [index] = aggregate.request.data["inputs"]
    assert index["kind"] == "index"
    assert this.control.calls.count("build_index") == 1
    assert this.control.calls.index("build_index") < len(this.control.calls) - 1
    assert types(this.control) == ["job.started", "job.succeeded"]
    assert types(this.control, "build") == ["task.placed", "task.started", "task.succeeded"]
    assert types(this.control, "sim-a") == [
        "task.placed",
        "task.started",
        "task.succeeded",
        "task.result.ready",
    ]
    assert this.control.status == "succeeded"
    assert {t.id: t.state for t in query.tasks} == dict.fromkeys(sorted(fan), "succeeded")


def overlap(starts: list[float], seconds: float) -> int:
    """The largest number of runs of `seconds` each that were in flight at once."""
    edges = sorted([(s, 1) for s in starts] + [(s + seconds, -1) for s in starts])
    level = peak = 0
    for _, step in edges:
        level += step
        peak = max(peak, level)
    return peak


def test_max_parallel(temporal, rig, plan):
    seconds = 1.5
    starts: list[float] = []

    def script(task):
        starts.append(asyncio.get_running_loop().time())
        return Zombie(after_s=seconds)

    tasks = {f"sim-{n}": ("simulate", []) for n in range(5)}

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": script}) as this:
            handle = await this.start(plan(tasks, max_parallel=2))
            return await handle.result()

    assert asyncio.run(asyncio.wait_for(body(), LIMIT_S)) == "succeeded"
    assert len(starts) == 5
    # Two at a time, never three: each later start waits for an earlier task to end.
    assert overlap(starts, seconds - 0.2) == 2


def test_status_query(temporal, rig, plan, fan):
    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": lambda task: Zombie(after_s=2)}) as this:
            handle = await this.start(plan(fan))
            while not this.pools["simulate"].calls:
                await asyncio.sleep(0.1)
            during = await handle.query(JobWorkflow.status)
            await handle.result()
            return during, await handle.query(JobWorkflow.status)

    during, after = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert during.job_status == "running"
    assert during.pause_reason is None
    assert (during.resume_counter, during.control_seq) == (0, 0)
    assert [t.id for t in during.tasks] == sorted(fan)
    states = {t.id: (t.state, t.task_attempt) for t in during.tasks}
    assert states["build"] == ("succeeded", 1)
    assert states["sim-a"] == ("running", 1)
    # A task that never ran shows task attempt 0.
    assert states["agg"] == ("pending", 0)
    assert after.job_status == "succeeded"
    for task in after.tasks:
        assert (task.state, task.task_attempt) == ("succeeded", 1)
        assert (task.guest_left, task.infra_left, task.reschedules) == (1, 10, 0)
        assert (task.last_class, task.checkpoint) == (None, None)


def workflow_stage(emitted: list[Event]):
    """The workflow as a stage: it reads its accepted event as JobWorkflow.run does, and emits the
    events a run wrote, or else the events of `facts.emit` built by its own event builder."""

    def stage(inbound, facts: Facts) -> list[Event]:
        identity = Identity.of(inbound)
        start_facts(inbound)
        if emitted:
            return emitted
        return [
            identity.event(
                name,
                facts.time,
                facts.data.get(name, SAMPLE_DATA[name]),
                **({"taskid": facts.taskid} if name.startswith("task.") else {}),
                **({"attempt": facts.attempt} if name in ATTEMPT_TYPES else {}),
                resume_counter=facts.resume_counter,
                sequence=facts.sequence,
            )
            for name in facts.emit
        ]

    return stage


def test_stage_contract_workflow(temporal, rig, plan, fan, accepted):
    run_stage_contract("workflow", workflow_stage([]))
    event = accepted()

    async def body():
        client = await connect(temporal)
        async with rig(client) as this:
            await (await this.start(plan(fan), event)).result()
            return list(this.control.events.values())

    emitted = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert {type_name(e.type) for e in emitted} == {
        "job.started",
        "job.succeeded",
        "task.placed",
        "task.started",
        "task.succeeded",
        "task.result.ready",
    }
    run_stage_contract("workflow", workflow_stage(emitted), [(event, Facts())])


def test_workflow_metrics_recorded(temporal, rig, plan, fan):
    buffer = MetricBuffer(100_000)
    runtime = Runtime(telemetry=TelemetryConfig(metrics=buffer))

    async def client() -> Client:
        await connect(temporal)
        return await Client.connect(
            temporal,
            namespace=naming().namespace,
            data_converter=event_data_converter,
            runtime=runtime,
        )

    async def body():
        async with rig(await client()) as this:
            await (await this.start(plan(fan))).result()
        # A row another stage moved to paused: the start move loses, and the workflow task
        # fails and retries.
        async with rig(await client(), control=FakeControl(status="paused")) as this:
            handle = await this.start(plan(fan))
            for _ in range(100):
                if any(u.metric.name.endswith("lost_transitions_total") for u in seen):
                    break
                seen.extend(buffer.retrieve_updates())
                await asyncio.sleep(0.1)
            await handle.terminate("the test is over")

    seen: list = []
    asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    seen.extend(buffer.retrieve_updates())
    by_name = {}
    for update in seen:
        by_name.setdefault(update.metric.name, []).append(update)
    history = by_name["wdf_workflow_history_events"]
    assert history and all(u.value > 0 for u in history)
    assert history[0].metric.kind == 2  # histogram
    assert {u.attributes["workflow_type"] for u in history} == {"JobWorkflow"}
    assert sum(u.value for u in by_name["wdf_workflow_lost_transitions_total"]) >= 1
    for name, updates in by_name.items():
        if name.startswith("wdf_"):
            assert not {"job_id", "jobid", "task_id", "taskid"} & set(updates[0].attributes)


def test_emit_defect_fails_the_workflow_task(temporal, rig, plan, fan):
    """A typed error of emit fails the workflow task and not the workflow, which stays open."""

    async def body():
        client = await connect(temporal)
        async with rig(client, control=FakeControl(status=None)) as this:
            handle = await this.start(plan(fan))
            for _ in range(100):
                history = [e.event_type async for e in handle.fetch_history_events()]
                if EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED in history:
                    break
                await asyncio.sleep(0.1)
            description = await handle.describe()
            await handle.terminate("the test is over")
            return history, description.status

    history, status = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED in history
    assert status == WorkflowExecutionStatus.RUNNING
