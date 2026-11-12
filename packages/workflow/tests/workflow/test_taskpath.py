"""The path of a task on a dev server: the compile cache check before placement, `place` recorded
as a marker and not called on replay, and a failed task that ends the job."""

import asyncio
import json

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.worker import Replayer
from wdf_stages.recording import type_name
from wdf_temporal import event_data_converter, tracing_interceptor
from wdf_workflow.job import JobWorkflow
from wdf_workflow.testing import Fail, Succeed, connect

pytestmark = pytest.mark.integration

# A job of these tests ends within seconds, so a wedged workflow fails its test.
LIMIT_S = 120


def test_cache_hit_skips(temporal, rig, plan, fan):
    async def body():
        client = await connect(temporal)
        async with rig(client) as this:
            first = await this.start(plan(fan))
            await first.result()
            second = await this.start(plan(fan))
            await second.result()
            return this, first.id, await second.query(JobWorkflow.status)

    this, first_id, status = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    jobs = [t.request.jobid for t in this.pools["compile"].calls]
    # The second job's compile was satisfied by the entry the first job recorded.
    assert len(jobs) == 1 and first_id == f"job-{jobs[0]}"
    [key] = this.control.cache
    [compiled] = this.pools["compile"].calls
    assert compiled.cache_key == key
    states = {t.id: (t.state, t.task_attempt) for t in status.tasks}
    assert states["build"] == ("skipped", 0)
    assert status.job_status == "succeeded"
    second_job = [j for j, _ in this.control.uses]
    assert len(second_job) == 1
    skipped = [
        e for e in this.control.events.values() if e.jobid == second_job[0] and e.taskid == "build"
    ]
    assert [type_name(e.type) for e in skipped] == ["task.skipped"]
    assert skipped[0].data == {"reason": "compile-cache-hit", "cache_key": key}
    # Placement never saw the skipped compile, and its simulate tasks read the cached component.
    component, _ = this.control.cache[key]
    second_sims = [t for t in this.pools["simulate"].calls if t.request.jobid == second_job[0]]
    assert len(second_sims) == 4
    for task in second_sims:
        assert task.request.data["inputs"] == [component.model_dump(mode="json")]
    assert this.control.calls.count("cache_record") == 1


def test_place_answer_recorded(temporal, rig, plan, fan):
    async def body():
        client = await connect(temporal)
        async with rig(client) as this:
            handle = await this.start(plan(fan))
            await handle.result()
            history = await handle.fetch_history()
        calls = this.placer.calls
        await Replayer(
            workflows=[JobWorkflow],
            data_converter=event_data_converter,
            interceptors=[tracing_interceptor()],
        ).replay_workflow(history)
        return history, calls, this.placer.calls

    history, calls, after_replay = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    markers = [
        e.marker_recorded_event_attributes
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_MARKER_RECORDED
    ]
    placed = [
        m
        for m in markers
        if json.loads(m.details["data"].payloads[0].data).get("activity_type") == "place"
    ]
    assert calls == len(fan) == len(placed)
    for marker in placed:
        [answer] = marker.details["result"].payloads
        assert json.loads(answer.data)["kind"] == "placement"
    # The replay reads every answer from its marker, and the placer is never called again.
    assert after_replay == calls


def test_failed_task_fails_job_and_blocks_its_dependents(temporal, rig, plan, fan):
    async def body():
        client = await connect(temporal)

        def simulate(task):
            return Fail("guest_memory_cap") if task.request.taskid == "sim-b" else Succeed()

        async with rig(client, {"simulate": simulate}) as this:
            handle = await this.start(plan(fan))
            return this, await handle.result(), await handle.query(JobWorkflow.status)

    this, status, query = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "failed"
    states = {t.id: (t.state, t.last_class) for t in query.tasks}
    assert states["sim-b"] == ("failed", "guest_memory_cap")
    assert states["sim-a"] == ("succeeded", None)
    assert states["agg"] == ("blocked", None)
    events = [e for e in this.control.events.values() if e.taskid == "sim-b"]
    assert [type_name(e.type) for e in events] == [
        "task.placed",
        "task.started",
        "task.attempt.failed",
        "task.failed",
    ]
    [failed] = [e for e in this.control.events.values() if type_name(e.type) == "job.failed"]
    assert failed.data == {"reason": "retries-exhausted: sim-b"}
    assert this.pools["aggregate"].calls == []
