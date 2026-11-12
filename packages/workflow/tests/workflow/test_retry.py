"""The retry loop on a dev server: a lost completion and a zombie retried from the infrastructure
budget, a schedule-to-start timeout that places again, a placement outage longer than a workflow
task timeout, deterministic classes never retried, checkpoints indexed and handed to the next
attempt, a lost cached component rebuilt, the history budget, and the history shape of a job."""

import asyncio

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.service import RPCError, RPCStatusCode
from wdf_jobdoc.plan_models import Plan
from wdf_stages.recording import type_name
from wdf_workflow.contract import naming
from wdf_workflow.job import JobWorkflow
from wdf_workflow.taskpath import LOCAL_RETRY_THRESHOLD
from wdf_workflow.testing import Die, Fail, Hang, Succeed, Zombie, connect

pytestmark = pytest.mark.integration

# A job of these tests ends within a minute, so a wedged workflow fails its test.
LIMIT_S = 180
# Short activity timeouts, so a timeout of the tests comes within seconds.
FAST = {"heartbeat_s": 2, "start_to_close_s": 4}
ONE = {"sim": ("simulate", [])}


def by_attempt(*outcomes):
    """A pool script that answers the n-th attempt of a task with the n-th outcome, and every
    later attempt with the last one."""
    return lambda task: outcomes[min(task.task_attempt, len(outcomes)) - 1]


def events_of(control, task: str | None = None) -> list[tuple[str, int | None]]:
    """The type and task attempt of each stored event of a task, or of the job, in write order."""
    return [(type_name(e.type), e.attempt) for e in control.events.values() if e.taskid == task]


def counted(updates, name: str) -> dict[str, int]:
    """The sum of a counter's updates by their `budget` label, or under `` without one."""
    totals: dict[str, int] = {}
    for update in updates:
        if update.metric.name == name:
            label = update.attributes.get("budget", "")
            totals[label] = totals.get(label, 0) + update.value
    return totals


async def scheduled(handle, activity: str = "run_task") -> list:
    """The ActivityTaskScheduled attributes of one activity type, in history order."""
    return [
        e.activity_task_scheduled_event_attributes
        async for e in handle.fetch_history_events()
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        and e.activity_task_scheduled_event_attributes.activity_type.name == activity
    ]


def test_lost_completion_job_finishes(temporal, rig, plan, placer, meter):
    """The worker takes the task and is never heard of again: the heartbeat timeout is an
    infrastructure failure, and the second attempt is the only one committed."""

    async def body():
        client, buffer = await meter(temporal)
        scripts = {"simulate": by_attempt(Die(), Succeed())}
        async with rig(client, scripts, placer=placer(FAST)) as this:
            handle = await this.start(plan(ONE))
            status = await handle.result()
            return this, status, await handle.query(JobWorkflow.status), buffer.retrieve_updates()

    this, status, query, updates = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    assert [t.task_attempt for t in this.pools["simulate"].calls] == [1, 2]
    [commit] = [c for c in this.control.commits if c.task == "sim"]
    assert commit.attempt == "r0a2"
    assert {r.producer.attempt for r in commit.references if r.producer} == {"r0a2"}
    [task] = query.tasks
    assert (task.state, task.task_attempt, task.last_class) == ("succeeded", 2, "heartbeat_timeout")
    assert (task.guest_left, task.infra_left) == (1, 9)
    # A worker that never beat hands over no start time, so the lost attempt has no task.started.
    assert events_of(this.control, "sim") == [
        ("task.placed", None),
        ("task.attempt.failed", 1),
        ("task.placed", None),
        ("task.started", 2),
        ("task.succeeded", 2),
        ("task.result.ready", 2),
    ]
    assert counted(updates, "wdf_workflow_retries_total") == {"infra": 1}


def test_zombie_fenced(temporal, rig, plan, placer):
    """An attempt that completes after its heartbeat timed out is refused by the server, so its
    outputs never reach the commit."""

    async def body():
        client = await connect(temporal)
        scripts = {"simulate": by_attempt(Zombie(after_s=4), Succeed())}
        async with rig(client, scripts, placer=placer(FAST)) as this:
            handle = await this.start(plan(ONE))
            # The retry waits 10 s after the timeout, which leaves the zombie time to complete.
            async with asyncio.timeout(60):
                while not await _timed_out(handle):
                    await asyncio.sleep(0.2)
            [first, *_] = await scheduled(handle)
            zombie = client.get_async_activity_handle(
                workflow_id=handle.id, run_id=handle.result_run_id, activity_id=first.activity_id
            )
            refusals = []
            for call in (zombie.heartbeat, lambda: zombie.complete(None)):
                try:
                    await call()
                except RPCError as refused:
                    refusals.append(refused.status)
            status = await handle.result()
            return this, status, refusals

    this, status, refusals = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    assert refusals == [RPCStatusCode.NOT_FOUND, RPCStatusCode.NOT_FOUND]
    # The zombie did complete, and the server dropped its result.
    assert [t.task_attempt for t in this.pools["simulate"].zombies] == [1]
    assert [c.attempt for c in this.control.commits] == ["r0a2"]


async def _timed_out(handle) -> bool:
    return any(
        [
            e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT
            async for e in handle.fetch_history_events()
        ]
    )


def test_schedule_to_start_replaces(temporal, rig, plan, placer, meter):
    """A task no worker takes is placed again with a doubled schedule-to-start timeout, consuming
    no budget, and runs once a worker serves its pool."""

    async def body():
        client, buffer = await meter(temporal)
        slow = placer(FAST | {"schedule_to_start_s": 2})
        async with rig(client, placer=slow, idle=frozenset({"simulate"})) as this:
            handle = await this.start(plan(ONE))
            async with asyncio.timeout(60):
                while slow.calls < 2:
                    await asyncio.sleep(0.1)
            await this.serve("simulate")
            status = await handle.result()
            timeouts = [a.schedule_to_start_timeout.seconds for a in await scheduled(handle)]
            query = await handle.query(JobWorkflow.status)
            return this, status, timeouts, query, buffer.retrieve_updates()

    this, status, timeouts, query, updates = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    # A worker that starts slowly may let the second timeout pass too, and double it again.
    assert this.placer.calls == len(timeouts) >= 2
    assert timeouts[:2] == [2, 4]
    [task] = query.tasks
    reschedules = len(timeouts) - 1
    assert (task.task_attempt, task.reschedules, task.last_class) == (
        len(timeouts),
        reschedules,
        "schedule_to_start_timeout",
    )
    assert (task.guest_left, task.infra_left) == (1, 10)
    assert counted(updates, "wdf_workflow_reschedules_total") == {"": reschedules}
    assert "wdf_workflow_retries_total" not in {u.metric.name for u in updates}


def test_scheduler_down_beyond_task_timeout(temporal, rig, plan, placer):
    """`place` fails for longer than the workflow task timeout: its retries keep the workflow task
    alive, and the job finishes once the placement service is back."""
    outage_s = 15

    async def body():
        client = await connect(temporal)
        async with rig(client, placer=placer(down_s=outage_s)) as this:
            handle = await this.start(plan(ONE))
            status = await handle.result()
            history = [e async for e in handle.fetch_history_events()]
            return this, status, history

    this, status, history = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    task_timeout = history[0].workflow_execution_started_event_attributes.workflow_task_timeout
    assert outage_s > task_timeout.seconds
    # Retried at 1, 2, 4 and 8 s, the fifth call at 15 s is the first after the outage.
    assert this.placer.refused >= 4
    assert this.placer.calls == 1
    kinds = [e.event_type for e in history]
    assert EventType.EVENT_TYPE_WORKFLOW_TASK_TIMED_OUT not in kinds
    assert EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED not in kinds
    assert EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED in kinds


# A deterministic class of each source a simulate worker reports, by task.
DETERMINISTIC = {
    "sim-a": Fail("guest_memory_cap"),
    "sim-b": Fail("guest_failed", deterministic=True),
    "sim-c": Fail("STORE_FORBIDDEN"),
    "sim-d": Fail("OUTPUT_MISSING"),
    "sim-e": Fail("ARTIFACT_LOST"),
}


def test_deterministic_class_not_retried_in_workflow(temporal, rig, plan):
    async def body():
        client = await connect(temporal)
        scripts = {"simulate": lambda task: DETERMINISTIC[task.request.taskid]}
        async with rig(client, scripts) as this:
            tasks = {tid: ("simulate", []) for tid in DETERMINISTIC}
            handle = await this.start(plan(tasks, retries=3))
            return this, await handle.result(), await handle.query(JobWorkflow.status)

    this, status, query = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "failed"
    assert sorted(t.request.taskid for t in this.pools["simulate"].calls) == sorted(DETERMINISTIC)
    for task in query.tasks:
        assert (task.state, task.task_attempt) == ("failed", 1)
        assert task.last_class == DETERMINISTIC[task.id].failure
        assert (task.guest_left, task.infra_left) == (3, 10)
    assert this.control.commits == []


def test_checkpoint_indexed_and_committed(temporal, rig, plan, placer):
    """Each checkpoint an attempt hands over, from its last heartbeat after a timeout or from its
    failure details, is indexed, announced, and is where the next attempt starts."""

    async def body():
        client = await connect(temporal)
        outcomes = by_attempt(Hang(checkpoint=True), Fail("timeout", checkpoint=True), Succeed())
        async with rig(client, {"simulate": outcomes}, placer=placer(FAST)) as this:
            handle = await this.start(plan(ONE, retries=2, checkpoint=frozenset({"sim"})))
            return this, await handle.result(), await handle.query(JobWorkflow.status)

    this, status, query = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    indexed = this.control.checkpoints
    assert [c.producer.attempt for c in indexed] == ["r0a1", "r0a2"]
    committed = [
        e for e in this.control.events.values() if type_name(e.type) == "task.checkpoint.committed"
    ]
    assert [(e.attempt, e.data["sequence"]) for e in committed] == [(1, 0), (2, 0)]
    assert [e.data["checkpoint"] for e in committed] == [c.model_dump(mode="json") for c in indexed]
    starts = [t.request.data.get("resume_checkpoint") for t in this.pools["simulate"].calls]
    assert starts == [None, *(c.model_dump(mode="json") for c in indexed)]
    [task] = query.tasks
    assert (task.checkpoint, task.task_attempt, task.guest_left, task.infra_left) == (
        indexed[1],
        3,
        1,
        9,
    )


def test_lost_cached_component_rebuilds(temporal, rig, plan, fan, accepted):
    """A job whose cached component is lost invalidates the entry once, compiles again, and runs
    every consumer again without charging a budget."""
    jobs: list[str] = []

    def simulate(task):
        lost = task.request.jobid == jobs[-1] and len(jobs) == 2 and task.task_attempt == 1
        return Fail("ARTIFACT_LOST") if lost else Succeed()

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": simulate}) as this:
            for _ in range(2):
                event = accepted()
                jobs.append(event.jobid)
                handle = await this.start(plan(fan), event)
                assert await handle.result() == "succeeded"
            return this, await handle.query(JobWorkflow.status)

    this, query = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert this.control.calls.count("cache_invalidate") == 1
    assert [t.request.jobid for t in this.pools["compile"].calls] == jobs
    states = {t.id: (t.state, t.task_attempt, t.guest_left, t.infra_left) for t in query.tasks}
    assert states["build"] == ("succeeded", 1, 1, 10)
    for sim in ("sim-a", "sim-b", "sim-c", "sim-d"):
        assert states[sim] == ("succeeded", 2, 1, 10)
    rebuilt = [e for e in this.control.events.values() if e.jobid == jobs[1]]
    assert [type_name(e.type) for e in rebuilt if e.taskid == "build"] == [
        "task.skipped",
        "task.placed",
        "task.started",
        "task.succeeded",
    ]


def test_history_budget_ends_job(temporal, rig, plan):
    """At the history budget no new task is placed, and the job fails once nothing runs."""
    limit = 40
    tasks = {f"sim-{n}": ("simulate", []) for n in range(6)}

    async def body():
        client = await connect(temporal)
        async with rig(client) as this:
            handle = await this.start(plan(tasks, max_parallel=1), max_history_events=limit)
            status = await handle.result()
            return this, status, await handle.query(JobWorkflow.status)

    this, status, query = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "failed"
    [failed] = [e for e in this.control.events.values() if type_name(e.type) == "job.failed"]
    prefix, length = failed.data["reason"].split(": ")
    assert prefix == "history-budget" and int(length) >= limit
    ran = len(this.pools["simulate"].calls)
    assert 0 < ran < len(tasks)
    states = [t.state for t in query.tasks]
    assert states == ["succeeded"] * ran + ["cancelled"] * (len(tasks) - ran)
    assert all(t.task_attempt == 0 for t in query.tasks[ran:])


def retried_once(task):
    """Fails the first attempt of task `guest` with a guest class and of task `infra` with an
    infrastructure class."""
    first = {"guest": Fail("timeout"), "infra": Fail("host_error")}.get(task.request.taskid)
    return first if first and task.task_attempt == 1 else Succeed()


def test_history_shape(temporal, rig, plan, placer, capsys):
    """Prints the history events of a job, of each task and of each retry, and the events one hour
    of placement outage adds, measured on the dev server."""
    sims = {f"sim-{n}": ("simulate", []) for n in range(3)}

    async def length(this, plan: Plan) -> int:
        handle = await this.start(plan)
        assert await handle.result() == "succeeded"
        return len([e async for e in handle.fetch_history_events()])

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": retried_once}) as this:
            one = await length(this, plan(ONE))
            three = await length(this, plan(sims, max_parallel=1))
            compile_ = await length(this, plan({"build": ("compile", [])}))
            guest = await length(this, plan({"guest": ("simulate", [])}))
            infra = await length(this, plan({"infra": ("simulate", [])}))
        # Past the first 63 s of in-process retries, each retry of `place` waits on a timer.
        outage_s = 70
        async with rig(client, placer=placer(down_s=outage_s)) as this:
            handle = await this.start(plan(ONE))
            assert await handle.result() == "succeeded"
            history = [e async for e in handle.fetch_history_events()]
        return one, three, compile_, guest, infra, history

    one, three, compile_, guest, infra, history = asyncio.run(asyncio.wait_for(body(), 600))
    per_task = (three - one) // 2
    per_job = one - per_task
    added = len(history) - one
    kinds = [e.event_type for e in history]
    timers = kinds.count(EventType.EVENT_TYPE_TIMER_STARTED)
    cycle = _timer_cycle(kinds)
    in_process = added - timers * cycle
    hour = in_process + _timers_in(3600) * cycle
    with capsys.disabled():
        print(
            f"\nhistory_events_per_job: {per_job} (estimate 10)"
            f"\nhistory_events_per_task: simulate {per_task}, compile {compile_ - per_job}"
            " (estimate 8)"
            f"\nhistory_events_per_retry: guest {guest - one}, infra {infra - one} (estimate 6)"
            f"\nplacement outage: {added} events over {_outage_s(history):.0f} s, of which"
            f" {timers} timer retries of {cycle} events each and {in_process} events of the"
            f" first {_in_process_s():.0f} s of in-process retries"
            f"\nplacement outage of one hour: {hour} events"
            f" ({in_process} + {_timers_in(3600)} timer retries x {cycle})"
        )
    assert timers >= 1 and cycle > 0
    assert min(per_job, per_task, guest - one, infra - one) > 0


def _backoffs():
    """The backoffs of the `place` retry policy of naming.yaml, attempt after attempt."""
    [place] = [a for a in naming().activities.local if a.name == "place"]
    retry, n = place.retry, 0
    while True:
        yield min(retry.initial_interval_s * retry.backoff_coefficient**n, retry.maximum_interval_s)
        n += 1


def _in_process_s() -> float:
    """How long `place` retries in process before its backoff passes the local retry
    threshold."""
    total = 0.0
    for backoff in _backoffs():
        if backoff > LOCAL_RETRY_THRESHOLD.total_seconds():
            return total
        total += backoff


def _timers_in(seconds: float) -> int:
    """The timer retries of `place` that start within `seconds` of an outage."""
    elapsed, timers = 0.0, 0
    for backoff in _backoffs():
        if elapsed >= seconds:
            return timers
        if backoff > LOCAL_RETRY_THRESHOLD.total_seconds():
            timers += 1
        elapsed += backoff


def _timer_cycle(kinds) -> int:
    """The events one timer retry adds: from the workflow task that starts the timer to the one
    that runs `place` again."""
    started = kinds.index(EventType.EVENT_TYPE_TIMER_STARTED)
    first = max(
        i for i in range(started) if kinds[i] == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED
    )
    fired = kinds.index(EventType.EVENT_TYPE_TIMER_FIRED, started)
    last = kinds.index(EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED, fired)
    return last - first + 1


def _outage_s(history) -> float:
    start = history[0].event_time.ToDatetime()
    placed = next(
        e
        for e in history
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        and e.activity_task_scheduled_event_attributes.activity_type.name == "run_task"
    )
    return (placed.event_time.ToDatetime() - start).total_seconds()
