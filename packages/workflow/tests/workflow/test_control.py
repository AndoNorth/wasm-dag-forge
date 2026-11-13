"""The control signals on a dev server: cancel and terminate of tasks in flight, the order of
pause, resume and cancel over repeated, stale and racing sequences, the manual pause of a
checkpoint-capable task and the drain of the others, and the `Lost` answers of a move of the job
row."""

import asyncio
import time

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import CancelledError
from wdf_stages.recording import type_name
from wdf_workflow.job import JobWorkflow
from wdf_workflow.models import ControlSignal, FailureDetails, ResumeSignal
from wdf_workflow.testing import Die, Hang, Interruptible, connect

pytestmark = pytest.mark.integration

LIMIT_S = 180
# A checkpoint-capable simulate task.
ONE = {"sim": ("simulate", [])}
CHECKPOINTED = frozenset({"sim"})


def signal(seq: int) -> ControlSignal:
    return ControlSignal(control_seq=seq, reason="sent by the test")


def resume(seq: int) -> ResumeSignal:
    return ResumeSignal(control_seq=seq, manifest=None)


async def query(handle):
    """The status query, or None while the plan is not loaded."""
    try:
        return await handle.query(JobWorkflow.status)
    except Exception:  # noqa: BLE001 - a query before the plan loads is refused
        return None


async def until(check, timeout_s: float = 60) -> None:
    """Waits until the async `check` holds."""
    async with asyncio.timeout(timeout_s):
        while not await check():
            await asyncio.sleep(0.2)


async def job_status(handle, status: str) -> None:
    async def holds():
        return (found := await query(handle)) is not None and found.job_status == status

    await until(holds)


async def called(pool, count: int = 1) -> None:
    async def holds():
        return len(pool.calls) >= count

    await until(holds)


async def history(handle, activity: str = "run_task") -> list[int]:
    """The event types of one activity type and of the workflow's end, in history order."""
    scheduled: set[int] = set()
    kinds = []
    async for event in handle.fetch_history_events():
        attributes = event.activity_task_scheduled_event_attributes
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            if attributes.activity_type.name == activity:
                scheduled.add(event.event_id)
                kinds.append(event.event_type)
            continue
        for name in (
            "activity_task_cancel_requested_event_attributes",
            "activity_task_canceled_event_attributes",
            "activity_task_failed_event_attributes",
            "activity_task_timed_out_event_attributes",
            "activity_task_completed_event_attributes",
        ):
            if event.HasField(name) and getattr(event, name).scheduled_event_id in scheduled:
                kinds.append(event.event_type)
        if event.event_type in ENDS:
            kinds.append(event.event_type)
    return kinds


ENDS = {
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED,
}
SCHEDULED = EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
CANCEL_REQUESTED = EventType.EVENT_TYPE_ACTIVITY_TASK_CANCEL_REQUESTED
CANCELED = EventType.EVENT_TYPE_ACTIVITY_TASK_CANCELED
FAILED = EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED
TIMED_OUT = EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT


def job_events(control) -> list[str]:
    return [type_name(e.type) for e in control.events.values() if e.taskid is None]


def task_events(control, task: str) -> list[str]:
    return [type_name(e.type) for e in control.events.values() if e.taskid == task]


@pytest.mark.parametrize("by", ["signal", "request"])
def test_cancel_in_flight(temporal, rig, plan, by):
    """A cancel, by the `cancel` signal or by a cancel request of the workflow, cancels every
    task in flight, waits for each activity to answer the cancel, and only then ends the job
    `cancelled`."""
    fan = {"sim-a": ("simulate", []), "sim-b": ("simulate", []), "agg": ("aggregate", ["sim-a"])}

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": lambda task: Hang()}) as this:
            handle = await this.start(plan(fan))
            await called(this.pools["simulate"], 2)
            if by == "signal":
                await handle.signal(JobWorkflow.cancel, signal(1))
                result = await handle.result()
            else:
                await handle.cancel()
                with pytest.raises(WorkflowFailureError) as failed:
                    await handle.result()
                result = failed.value.cause
            return this, result, await query(handle), await history(handle)

    this, result, status, kinds = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    if by == "signal":
        assert result == "cancelled"
    else:
        assert isinstance(result, CancelledError)
    assert this.control.status == "cancelled"
    assert this.control.moves == [("admitted", "running", None), ("running", "cancelled", None)]
    assert status.job_status == "cancelled"
    # The aggregate waited behind a cancelled input, so it stays blocked.
    assert {t.id: t.state for t in status.tasks} == {
        "agg": "blocked",
        "sim-a": "cancelled",
        "sim-b": "cancelled",
    }
    # Each activity answered its cancel before the workflow ended.
    assert kinds.count(CANCEL_REQUESTED) == kinds.count(CANCELED) == 2
    assert job_events(this.control) == ["job.started", "job.cancelled"]
    for task in ("sim-a", "sim-b"):
        assert task_events(this.control, task) == ["task.placed"]


def test_terminate_hard_stop(temporal, rig, plan):
    """A terminate ends the job at once: the activity of a worker that never answers is asked to
    cancel, and the job is `terminated` long before its heartbeat timeout of 60 s."""

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": lambda task: Die()}) as this:
            handle = await this.start(plan(ONE))
            await called(this.pools["simulate"])
            sent = time.monotonic()
            await handle.signal(JobWorkflow.terminate, signal(1))
            result = await handle.result()
            took = time.monotonic() - sent
            every = [e.event_type async for e in handle.fetch_history_events()]
            return this, result, took, await query(handle), await history(handle), every

    this, result, took, status, kinds, every = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert result == "terminated"
    assert took < 15
    assert this.control.status == "terminated"
    assert this.control.moves[-1] == ("running", "terminated", None)
    assert [(t.id, t.state) for t in status.tasks] == [("sim", "cancelled")]
    assert kinds == [SCHEDULED, CANCEL_REQUESTED, EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED]
    # The paths left behind add no command after the completion.
    assert EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED not in every
    assert job_events(this.control) == ["job.started", "job.terminated"]


def test_signal_dedup(temporal, rig, plan):
    """A pause below the last applied sequence is ignored, a repeated pause pauses once, and a
    repeated cancel cancels once."""

    async def body():
        client = await connect(temporal)
        scripts = {"simulate": lambda task: Interruptible()}
        async with rig(client, scripts) as this:
            handle = await this.start(plan(ONE, checkpoint=CHECKPOINTED))
            await called(this.pools["simulate"])
            await handle.signal(JobWorkflow.resume, resume(5))

            async def applied():
                return (found := await query(handle)) is not None and found.control_seq == 5

            await until(applied)
            await handle.signal(JobWorkflow.pause, signal(4))
            await asyncio.sleep(2)
            stale = await query(handle)
            await handle.signal(JobWorkflow.pause, signal(6))
            await job_status(handle, "paused")
            await handle.signal(JobWorkflow.pause, signal(6))
            await handle.signal(JobWorkflow.cancel, signal(7))
            await handle.signal(JobWorkflow.cancel, signal(7))
            return this, stale, await handle.result(), await query(handle)

    this, stale, result, status = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert (stale.job_status, stale.control_seq, stale.tasks[0].state) == ("running", 5, "running")
    assert result == "cancelled"
    assert status.control_seq == 7
    assert job_events(this.control) == ["job.started", "job.paused", "job.cancelled"]
    assert [move[:2] for move in this.control.moves] == [
        ("admitted", "running"),
        ("running", "paused"),
        ("paused", "cancelled"),
    ]
    assert len(this.pools["simulate"].calls) == 1


async def paused_queued(this, handle) -> None:
    """Waits until the job's one checkpoint-capable task is queued, then pauses it."""

    async def queued():
        return SCHEDULED in await history(handle)

    await until(queued)
    await handle.signal(JobWorkflow.pause, signal(1))
    await job_status(handle, "paused")


@pytest.mark.parametrize("first", ["cancel", "resume"])
def test_resume_and_cancel_same_seq_cancel_wins(temporal, rig, plan, first):
    """A resume that carries the sequence of a cancel never hides it, whichever arrives first."""

    async def body():
        client = await connect(temporal)
        async with rig(client, idle=frozenset({"simulate"})) as this:
            handle = await this.start(plan(ONE, checkpoint=CHECKPOINTED))
            await paused_queued(this, handle)
            sends = [
                handle.signal(JobWorkflow.cancel, signal(2)),
                handle.signal(JobWorkflow.resume, resume(2)),
            ]
            for send in sends if first == "cancel" else reversed(sends):
                await send
            return this, await handle.result(), await query(handle)

    this, result, status = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert result == "cancelled"
    assert (status.job_status, status.control_seq) == ("cancelled", 2)
    assert this.control.status == "cancelled"
    assert job_events(this.control) == ["job.started", "job.paused", "job.cancelled"]


def test_cancel_while_resume_pending(temporal, rig, plan, meter):
    """A cancel of a paused job whose row control already moved to `resume-pending` loses once and
    moves the row from there, without counting a lost move."""

    async def body():
        client, buffer = await meter(temporal)
        async with rig(client, idle=frozenset({"simulate"})) as this:
            handle = await this.start(plan(ONE, checkpoint=CHECKPOINTED))
            await paused_queued(this, handle)
            this.control.status = "resume-pending"
            await handle.signal(JobWorkflow.cancel, signal(3))
            return this, await handle.result(), buffer.retrieve_updates()

    this, result, updates = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert result == "cancelled"
    assert this.control.status == "cancelled"
    assert this.control.moves == [
        ("admitted", "running", None),
        ("running", "paused", None),
        ("paused", "cancelled", "resume-pending"),
        ("resume-pending", "cancelled", None),
    ]
    assert "wdf_workflow_lost_transitions_total" not in {u.metric.name for u in updates}
    assert job_events(this.control) == ["job.started", "job.paused", "job.cancelled"]


async def paused_running(this, handle, count: int = 1) -> None:
    """Waits until the pool took `count` tasks, then pauses the job."""
    await called(this.pools["simulate"], count)
    await handle.signal(JobWorkflow.pause, signal(1))
    await job_status(handle, "paused")


def test_manual_pause_interrupts_checkpoint_task(temporal, rig, plan):
    """A manual pause cancels a running checkpoint-capable task, whose worker answers with
    `operator_interrupt`, which consumes no budget, and the job projects `paused` for `operator`."""

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": lambda task: Interruptible()}) as this:
            handle = await this.start(plan(ONE, checkpoint=CHECKPOINTED))
            await paused_running(this, handle)
            status = await query(handle)
            await handle.signal(JobWorkflow.cancel, signal(2))
            return this, status, await handle.result(), await history(handle)

    this, status, result, kinds = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert (status.job_status, status.pause_reason, status.control_seq) == ("paused", "operator", 1)
    [task] = status.tasks
    assert (task.state, task.task_attempt, task.last_class) == ("paused", 1, "operator_interrupt")
    assert (task.guest_left, task.infra_left, task.reschedules) == (1, 10, 0)
    # The worker answered the cancel with a failure, so the workflow saw its final checkpoint.
    assert kinds[:3] == [SCHEDULED, CANCEL_REQUESTED, FAILED]
    assert task_events(this.control, "sim") == [
        "task.placed",
        "task.started",
        "task.checkpoint.committed",
        "task.attempt.failed",
    ]
    assert result == "cancelled"
    assert this.control.moves[1:] == [("running", "paused", None), ("paused", "cancelled", None)]


def test_manual_pause_keeps_final_checkpoint(temporal, rig, plan):
    """The checkpoint the worker took when its task was interrupted is indexed, announced and kept
    as the head the task runs again from."""

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": lambda task: Interruptible()}) as this:
            handle = await this.start(plan(ONE, checkpoint=CHECKPOINTED))
            await paused_running(this, handle)
            status = await query(handle)
            reported = await interrupt_details(client, handle)
            await handle.terminate("the test is over")
            return this, status, reported

    this, status, reported = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    final = reported.checkpoint
    assert final is not None and final.producer.attempt == "r0a1"
    assert status.tasks[0].checkpoint == final
    assert this.control.checkpoints == [final]
    [committed] = [
        e for e in this.control.events.values() if type_name(e.type) == "task.checkpoint.committed"
    ]
    assert committed.data["checkpoint"] == final.model_dump(mode="json")


async def interrupt_details(client, handle) -> FailureDetails:
    """The failure details of the one `operator_interrupt` in the history."""
    converter = client.data_converter.payload_converter
    async for event in handle.fetch_history_events():
        if event.event_type == FAILED:
            failure = event.activity_task_failed_event_attributes.failure
            assert failure.application_failure_info.type == "operator_interrupt"
            payloads = failure.application_failure_info.details.payloads
            [details] = converter.from_payloads(payloads, [FailureDetails])
            return details
    raise AssertionError("no failed activity in the history")


def test_manual_pause_drains_others(temporal, rig, plan):
    """During a manual pause every task that is not checkpoint-capable runs to completion, no new
    task is placed, and the job projects `paused` only once nothing runs."""
    tasks = {
        "build": ("compile", []),
        "sim-a": ("simulate", ["build"]),
        "sim-b": ("simulate", ["build"]),
        "agg": ("aggregate", ["sim-b"]),
    }

    def simulate(task):
        return Interruptible() if task.request.taskid == "sim-a" else Interruptible(seconds=3)

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": simulate}) as this:
            handle = await this.start(plan(tasks, checkpoint=frozenset({"sim-a"})))
            await paused_running(this, handle, 2)
            status = await query(handle)
            await handle.signal(JobWorkflow.cancel, signal(2))
            return this, status, await handle.result(), await query(handle)

    this, status, result, ended = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    states = {t.id: (t.state, t.task_attempt) for t in status.tasks}
    assert states == {
        "agg": ("ready", 0),
        "build": ("succeeded", 1),
        "sim-a": ("paused", 1),
        "sim-b": ("succeeded", 1),
    }
    assert this.pools["aggregate"].calls == []
    events = [(type_name(e.type), e.taskid) for e in this.control.events.values()]
    assert events.index(("job.paused", None)) > events.index(("task.succeeded", "sim-b"))
    assert result == "cancelled"
    assert {t.id: t.state for t in ended.tasks} == {
        "agg": "cancelled",
        "build": "succeeded",
        "sim-a": "cancelled",
        "sim-b": "succeeded",
    }


def test_manual_pause_queued_task_returns_ready(temporal, rig, plan):
    """A checkpoint-capable task that no worker took yet is cancelled at once by a manual pause,
    which is no failure: the task is `ready` again and never ran."""

    async def body():
        client = await connect(temporal)
        async with rig(client, idle=frozenset({"simulate"})) as this:
            handle = await this.start(plan(ONE, checkpoint=CHECKPOINTED))
            sent = time.monotonic()
            await paused_queued(this, handle)
            took = time.monotonic() - sent
            status = await query(handle)
            await handle.terminate("the test is over")
            return this, took, status, await history(handle)

    this, took, status, kinds = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    # Far below the schedule-to-start timeout of 1,800 s.
    assert took < 30
    [task] = status.tasks
    assert (task.state, task.task_attempt, task.last_class) == ("ready", 0, None)
    assert (task.guest_left, task.infra_left, task.reschedules) == (1, 10, 0)
    assert kinds[:3] == [SCHEDULED, CANCEL_REQUESTED, CANCELED]
    assert task_events(this.control, "sim") == ["task.placed"]
    assert this.control.status == "paused"


def test_manual_pause_no_replace(temporal, rig, plan, placer):
    """During a manual pause the schedule-to-start timeout of a task that is not
    checkpoint-capable is no failure: the task is not placed again and is `ready` for the
    resume."""

    async def body():
        client = await connect(temporal)
        slow = placer({"schedule_to_start_s": 5})
        async with rig(client, placer=slow, idle=frozenset({"simulate"})) as this:
            handle = await this.start(plan(ONE))
            await until(lambda: queued(handle))
            await handle.signal(JobWorkflow.pause, signal(1))
            await job_status(handle, "paused")
            status = await query(handle)
            await handle.terminate("the test is over")
            return this, status, await history(handle)

    this, status, kinds = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert this.placer.calls == 1
    [task] = status.tasks
    assert (task.state, task.task_attempt, task.reschedules, task.last_class) == (
        "ready",
        0,
        0,
        None,
    )
    assert kinds[:2] == [SCHEDULED, TIMED_OUT]
    assert task_events(this.control, "sim") == ["task.placed"]
    assert status.pause_reason == "operator"


async def queued(handle) -> bool:
    return SCHEDULED in await history(handle)


def test_lost_terminal_completes(temporal, rig, plan):
    """A row that control ended meanwhile makes the job's end move lose: the workflow writes
    nothing more and completes with the row's status."""

    async def body():
        client = await connect(temporal)
        scripts = {"simulate": lambda task: Interruptible(seconds=3)}
        async with rig(client, scripts) as this:
            handle = await this.start(plan(ONE))
            await called(this.pools["simulate"])
            this.control.status = "cancelled"
            return this, await handle.result(), await query(handle)

    this, result, status = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert result == "cancelled"
    assert status.job_status == "cancelled"
    assert this.control.moves == [
        ("admitted", "running", None),
        ("running", "succeeded", "cancelled"),
    ]
    assert job_events(this.control) == ["job.started"]


def test_unexpected_lost_resends(temporal, rig, plan, meter, recording):
    """A move that loses to a status the workflow cannot explain is sent again with backoff,
    counted, and nothing is placed until the row agrees."""

    async def body():
        client, buffer = await meter(temporal)
        control = recording(status="accepted")
        async with rig(client, control=control) as this:
            handle = await this.start(plan(ONE))

            async def lost_twice():
                return sum(lost is not None for *_, lost in this.control.moves) >= 2

            await until(lost_twice)
            placed = len(this.pools["simulate"].calls)
            written = len(this.control.events)
            this.control.status = "admitted"
            return this, placed, written, await handle.result(), buffer.retrieve_updates()

    this, placed, written, result, updates = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert (placed, written) == (0, 0)
    assert result == "succeeded"
    losses = [move for move in this.control.moves if move[2] is not None]
    assert len(losses) >= 2
    assert set(losses) == {("admitted", "running", "accepted")}
    assert this.control.moves[len(losses) :] == [
        ("admitted", "running", None),
        ("running", "succeeded", None),
    ]
    lost = [u for u in updates if u.metric.name == "wdf_workflow_lost_transitions_total"]
    assert sum(u.value for u in lost) == len(losses)
    assert job_events(this.control) == ["job.started", "job.succeeded"]
