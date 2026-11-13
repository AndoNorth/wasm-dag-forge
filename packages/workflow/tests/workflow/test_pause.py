"""Pause and resume on a dev server: the drain before a pause on an exhausted budget, the wait on
`resume` that adds no history, the resume from the head of a task's checkpoints under the next
resume counter, the fallback to the checkpoint behind a corrupt one, and the compile of a patched
source whose failure pauses the job again."""

import asyncio
import uuid

import pytest
from wdf_events.models import ManifestReference
from wdf_stages.recording import type_name
from wdf_workflow.job import JobWorkflow
from wdf_workflow.models import ControlSignal, ResumeSignal
from wdf_workflow.testing import Fail, Interruptible, Succeed, connect
from wdf_workflow.testing.control import LoadedPlan

pytestmark = pytest.mark.integration

LIMIT_S = 180
# A checkpoint-capable simulate task.
ONE = {"sim": ("simulate", [])}
CHECKPOINTED = frozenset({"sim"})
# A guest failure that hands over a checkpoint.
TIMED_OUT = Fail("timeout", checkpoint=True)
# The source of the compile tasks the conftest plans build.
FIRST_SOURCE = "a" * 64


def by_attempt(*outcomes):
    """A pool script that answers the n-th attempt of a task with the n-th outcome, and every
    later attempt with the last one."""
    return lambda task: outcomes[min(task.task_attempt, len(outcomes)) - 1]


def source(name: str) -> dict:
    sha256 = uuid.uuid5(uuid.NAMESPACE_URL, name).hex * 2
    return {
        "uri": f"s3://wdf-sources/sha256/{sha256[:2]}/{sha256}",
        "sha256": sha256,
        "size": 2048,
        "name": "bundle.tar.gz",
        "kind": "source",
    }


def manifest() -> ManifestReference:
    sha256 = uuid.uuid4().hex * 2
    return ManifestReference(
        uri=f"s3://wdf-sources/sha256/{sha256[:2]}/{sha256}",
        sha256=sha256,
        size=2048,
        name="manifest.json",
        kind="manifest",
    )


async def query(handle):
    """The status query, or None while the plan is not loaded."""
    try:
        return await handle.query(JobWorkflow.status)
    except Exception:  # noqa: BLE001 - a query before the plan loads is refused
        return None


async def paused(handle, resume_counter: int = 0):
    """Waits until the job is paused under `resume_counter`, and returns its status."""
    async with asyncio.timeout(60):
        while True:
            found = await query(handle)
            if found and (found.job_status, found.resume_counter) == ("paused", resume_counter):
                return found
            await asyncio.sleep(0.2)


async def granted(this, handle, seq: int, patched: ManifestReference | None = None) -> None:
    """Grants a resume as control and admission would: the row moves to `resume-pending`, then
    the paused workflow takes `resume` with the row's sequence and pending patch."""
    this.control.status = "resume-pending"
    await handle.signal(JobWorkflow.resume, ResumeSignal(control_seq=seq, manifest=patched))


def job_events(control) -> list[str]:
    return [type_name(e.type) for e in control.events.values() if e.taskid is None]


def task_events(control, task: str) -> list[str]:
    return [type_name(e.type) for e in control.events.values() if e.taskid == task]


def test_drain_then_pause(temporal, rig, plan):
    """A checkpoint-capable task that exhausts its guest budget pauses its chain, every
    independent task keeps running, one that becomes ready meanwhile included, and the job pauses
    for `retries-exhausted` only once nothing can run."""
    tasks = {
        "build": ("compile", []),
        "sim-x": ("simulate", ["build"]),
        "sim-y": ("simulate", ["build"]),
        "agg-x": ("aggregate", ["sim-x"]),
        "agg-y": ("aggregate", ["sim-y"]),
    }

    def simulate(task):
        return TIMED_OUT if task.request.taskid == "sim-x" else Interruptible(seconds=3)

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": simulate}) as this:
            handle = await this.start(plan(tasks, retries=0, checkpoint=frozenset({"sim-x"})))
            status = await paused(handle)
            await handle.signal(JobWorkflow.cancel, ControlSignal(control_seq=1, reason=None))
            return this, status, await handle.result()

    this, status, result = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status.pause_reason == "retries-exhausted"
    assert {t.id: t.state for t in status.tasks} == {
        "agg-x": "blocked",
        "agg-y": "succeeded",
        "build": "succeeded",
        "sim-x": "paused",
        "sim-y": "succeeded",
    }
    # The aggregate of the independent chain became ready after sim-x had exhausted its budget.
    assert [t.request.taskid for t in this.pools["aggregate"].calls] == ["agg-y"]
    assert task_events(this.control, "sim-x")[-1] == "task.retries.exhausted"
    assert job_events(this.control) == ["job.started", "job.paused", "job.cancelled"]
    assert [move[:2] for move in this.control.moves] == [
        ("admitted", "running"),
        ("running", "paused"),
        ("paused", "cancelled"),
    ]
    assert result == "cancelled"


def test_paused_costs_no_history(temporal, rig, plan):
    """A paused job waits on `resume` without a timer: its history does not grow over a 60 s
    pause, and the resume still wakes it."""

    async def body():
        client = await connect(temporal)
        scripts = {"simulate": by_attempt(TIMED_OUT, Succeed())}
        async with rig(client, scripts) as this:
            handle = await this.start(plan(ONE, retries=0, checkpoint=CHECKPOINTED))
            await paused(handle)
            before = (await handle.describe()).history_length
            await asyncio.sleep(60)
            after = (await handle.describe()).history_length
            await granted(this, handle, 1)
            return before, after, await handle.result()

    before, after, result = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    print(f"history length: {before} paused, {after} after 60 s")
    assert after == before
    assert result == "succeeded"


def test_resume_from_checkpoint(temporal, rig, plan):
    """A resume moves the row from `resume-pending` to `running` with `job.resumed` before
    anything runs, and the paused task runs again with fresh budgets from the head of its kept
    checkpoints under the next resume counter, its attempt counting on."""

    async def body():
        client = await connect(temporal)
        scripts = {"simulate": by_attempt(TIMED_OUT, TIMED_OUT, Succeed())}
        async with rig(client, scripts) as this:
            handle = await this.start(plan(ONE, retries=1, checkpoint=CHECKPOINTED))
            status = await paused(handle)
            await granted(this, handle, 1)
            return this, status, await handle.result(), await query(handle)

    this, status, result, final = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    first, second = this.control.checkpoints
    [task] = status.tasks
    assert (status.pause_reason, task.state, task.guest_left) == ("retries-exhausted", "paused", 0)
    assert task.checkpoint == second
    assert result == "succeeded"
    calls = this.pools["simulate"].calls
    assert [(c.task_attempt, c.resume_counter) for c in calls] == [(1, 0), (2, 0), (3, 1)]
    assert calls[1].request.data["resume_checkpoint"] == first.model_dump(mode="json")
    assert calls[2].request.data["resume_checkpoint"] == second.model_dump(mode="json")
    [commit] = this.control.commits
    assert commit.attempt == "r1a3"
    assert this.control.moves == [
        ("admitted", "running", None),
        ("running", "paused", None),
        ("resume-pending", "running", None),
        ("running", "succeeded", None),
    ]
    assert job_events(this.control) == ["job.started", "job.paused", "job.resumed", "job.succeeded"]
    [task] = final.tasks
    assert (final.job_status, final.pause_reason, final.resume_counter) == ("succeeded", None, 1)
    assert (task.state, task.task_attempt, task.guest_left, task.infra_left) == (
        "succeeded",
        3,
        1,
        10,
    )


def test_corrupt_checkpoint_falls_back(temporal, rig, plan):
    """A resumed attempt that finds the head checkpoint corrupt costs no budget, and the next
    attempt starts from the checkpoint behind it."""

    async def body():
        client = await connect(temporal)
        outcomes = (TIMED_OUT, TIMED_OUT, Fail("CHECKPOINT_CORRUPT"), Succeed())
        async with rig(client, {"simulate": by_attempt(*outcomes)}) as this:
            handle = await this.start(plan(ONE, retries=1, checkpoint=CHECKPOINTED))
            await paused(handle)
            await granted(this, handle, 1)
            return this, await handle.result(), await query(handle)

    this, result, final = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    first, second = this.control.checkpoints
    assert result == "succeeded"
    calls = this.pools["simulate"].calls
    assert [(c.task_attempt, c.resume_counter) for c in calls] == [(1, 0), (2, 0), (3, 1), (4, 1)]
    assert calls[2].request.data["resume_checkpoint"] == second.model_dump(mode="json")
    assert calls[3].request.data["resume_checkpoint"] == first.model_dump(mode="json")
    [task] = final.tasks
    assert (task.last_class, task.checkpoint) == ("CHECKPOINT_CORRUPT", first)
    assert (task.guest_left, task.infra_left) == (1, 10)


def test_resume_compile_failure_stays_paused(temporal, rig, plan):
    """A resume with a patched source compiles it again, a deterministic failure of that compile
    moves the job from `running` back to `paused` for `resume-compile-failed`, and a later resume
    with a fixed source runs the chain on the new component."""
    tasks = {"build": ("compile", []), "sim": ("simulate", ["build"])}
    broken, fixed = source("broken"), source("fixed")

    def compile_(task):
        return (
            Fail("compile_failed") if task.params.source.sha256 == broken["sha256"] else Succeed()
        )

    def patched(this, bundle: dict) -> ManifestReference:
        """A patched manifest of the job whose compile builds `bundle`, as load_plan returns it."""
        reference = manifest()
        changed = plan(
            tasks, retries=0, checkpoint=CHECKPOINTED, compile={"source": bundle, "entry": "sim"}
        )
        this.control.plans[reference.uri] = LoadedPlan(plan=changed, max_history_events=10_000)
        return reference

    async def body():
        client = await connect(temporal)
        scripts = {"compile": compile_, "simulate": by_attempt(TIMED_OUT, Succeed())}
        async with rig(client, scripts) as this:
            handle = await this.start(plan(tasks, retries=0, checkpoint=CHECKPOINTED))
            await paused(handle)
            await granted(this, handle, 1, patched(this, broken))
            status = await paused(handle, resume_counter=1)
            moves = list(this.control.moves)
            await granted(this, handle, 2, patched(this, fixed))
            return this, status, moves, await handle.result(), await query(handle)

    this, status, moves, result, final = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    # The status table has no move from resume-pending to paused, so the job runs before it pauses.
    assert moves == [
        ("admitted", "running", None),
        ("running", "paused", None),
        ("resume-pending", "running", None),
        ("running", "paused", None),
    ]
    assert status.pause_reason == "resume-compile-failed"
    assert {t.id: t.state for t in status.tasks} == {"build": "paused", "sim": "blocked"}
    assert result == "succeeded"
    builds = this.pools["compile"].calls
    assert [(c.params.source.sha256, c.resume_counter) for c in builds] == [
        (FIRST_SOURCE, 0),
        (broken["sha256"], 1),
        (fixed["sha256"], 2),
    ]
    sims = this.pools["simulate"].calls
    assert [(c.task_attempt, c.resume_counter) for c in sims] == [(1, 0), (2, 2)]
    # The resumed simulate task reads the component of the fixed build.
    [old, new] = [c.request.data["inputs"][0]["uri"] for c in sims]
    assert old != new
    [build] = [refs for (_, task), refs in this.control.committed.items() if task == "build"]
    assert build[0].uri == new
    assert job_events(this.control) == [
        "job.started",
        "job.paused",
        "job.resumed",
        "job.paused",
        "job.resumed",
        "job.succeeded",
    ]
    assert (final.job_status, final.resume_counter, final.control_seq) == ("succeeded", 2, 2)


def test_refused_patch_stays_paused(temporal, rig, plan):
    """A patch that the patch check refuses runs nothing, and the job pauses again for the reason
    it had."""

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": lambda task: TIMED_OUT}) as this:
            handle = await this.start(plan(ONE, retries=0, checkpoint=CHECKPOINTED))
            await paused(handle)
            reference = manifest()
            # A patch may not change a task's retries.
            changed = plan(ONE, retries=3, checkpoint=CHECKPOINTED)
            this.control.plans[reference.uri] = LoadedPlan(plan=changed, max_history_events=10_000)
            await granted(this, handle, 1, reference)
            status = await paused(handle, resume_counter=1)
            await handle.signal(JobWorkflow.cancel, ControlSignal(control_seq=2, reason=None))
            return this, status, await handle.result()

    this, status, result = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    [task] = status.tasks
    assert (status.pause_reason, task.state, task.guest_left) == ("retries-exhausted", "paused", 0)
    assert len(this.pools["simulate"].calls) == 1
    assert [move[:2] for move in this.control.moves] == [
        ("admitted", "running"),
        ("running", "paused"),
        ("resume-pending", "running"),
        ("running", "paused"),
        ("paused", "cancelled"),
    ]
    assert result == "cancelled"


async def called(pool, count: int) -> None:
    async with asyncio.timeout(60):
        while len(pool.calls) < count:
            await asyncio.sleep(0.2)


def test_pause_after_resume(temporal, rig, plan):
    """A manual pause of a resumed job pauses it again for `operator` under the resume counter,
    and the next resume runs the interrupted task once more under the counter after it."""

    async def body():
        client = await connect(temporal)
        async with rig(client, {"simulate": lambda task: Interruptible()}) as this:
            handle = await this.start(plan(ONE, checkpoint=CHECKPOINTED))
            await called(this.pools["simulate"], 1)
            await handle.signal(JobWorkflow.pause, ControlSignal(control_seq=1, reason=None))
            await paused(handle)
            await granted(this, handle, 2)
            await called(this.pools["simulate"], 2)
            await handle.signal(JobWorkflow.pause, ControlSignal(control_seq=3, reason=None))
            status = await paused(handle, resume_counter=1)
            await granted(this, handle, 4)
            await called(this.pools["simulate"], 3)
            await handle.signal(JobWorkflow.cancel, ControlSignal(control_seq=5, reason=None))
            return this, status, await handle.result()

    this, status, result = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    [task] = status.tasks
    assert (status.pause_reason, status.control_seq, task.state) == ("operator", 3, "paused")
    calls = this.pools["simulate"].calls
    assert [(c.task_attempt, c.resume_counter) for c in calls] == [(1, 0), (2, 1), (3, 2)]
    assert job_events(this.control) == [
        "job.started",
        "job.paused",
        "job.resumed",
        "job.paused",
        "job.resumed",
        "job.cancelled",
    ]
    assert result == "cancelled"


def test_resume_places_withdrawn_task_again(temporal, rig, plan):
    """A task that a manual pause withdrew before it started is placed again after the resume
    under the same attempt, and both placements are in the job's events."""

    async def body():
        client = await connect(temporal)
        async with rig(client, idle=frozenset({"simulate"})) as this:
            handle = await this.start(plan(ONE, checkpoint=CHECKPOINTED))
            async with asyncio.timeout(60):
                while "task.placed" not in task_events(this.control, "sim"):
                    await asyncio.sleep(0.2)
            await handle.signal(JobWorkflow.pause, ControlSignal(control_seq=1, reason=None))
            status = await paused(handle)
            await granted(this, handle, 2)
            await this.serve("simulate")
            return this, status, await handle.result()

    this, status, result = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    [task] = status.tasks
    assert (status.pause_reason, task.state, task.task_attempt) == ("operator", "ready", 0)
    assert result == "succeeded"
    [call] = this.pools["simulate"].calls
    assert (call.task_attempt, call.resume_counter) == (1, 1)
    assert task_events(this.control, "sim").count("task.placed") == 2
