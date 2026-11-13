"""The path of one task through the job workflow: the compile cache lookup, `place` as a local
activity, one `run_task` on the queue it returned, then the commit of the accepted attempt and the
cache record of a compile, attempt after attempt as the failure class table decides.

Workflow code, run inside the workflow sandbox with the platform libraries passed through.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError
from temporalio.exceptions import TimeoutError as ActivityTimeout
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    import os

    from wdf_events import Event, format_time
    from wdf_events.models import ArtifactReference, CheckpointReference, ContentReference
    from wdf_keys import attempt_identity

    from .aggregate import index_request
    from .contract import failure_classes, naming
    from .failures import Failure, decide
    from .models import REASON_MAX, FailureDetails, HeartbeatDetails, Retry, TaskInput, TaskResult
    from .placement import ACTIVITY as PLACE
    from .placement import Placement, PlacementRequest, PlaceResult
    from .ready import DONE
    from .result import exhausted
    from .testing.control import (
        CacheAnswer,
        CacheInvalidate,
        CacheLookup,
        CacheRecord,
        Commit,
        IndexCheckpoint,
        error_types,
    )

NAMING = naming()

# Control activities run on the build's own worker, and an index over many results takes long.
CONTROL_START_TO_CLOSE = timedelta(minutes=5)
LOCAL_START_TO_CLOSE = timedelta(seconds=30)
# Above this backoff a local activity retry waits on a workflow timer, not inside the task.
LOCAL_RETRY_THRESHOLD = timedelta(seconds=60)
TIMEOUT_CLASSES = {
    "HEARTBEAT": "heartbeat_timeout",
    "START_TO_CLOSE": "start_to_close_timeout",
    "SCHEDULE_TO_START": "schedule_to_start_timeout",
}


class Withdrawn(Exception):
    """An attempt that did not start because a manual pause is in force: it is no failure, and
    the task runs after the resume."""


class TaskFailed(Exception):
    """An attempt that ended without a result, with its class of the failure class table, the
    deterministic flag and checkpoint its worker handed over, and what the attempt ran with."""

    def __init__(
        self,
        failure: str,
        reason: str,
        started_at: str | None = None,
        *,
        deterministic: bool | None = None,
        checkpoint: CheckpointReference | None = None,
    ):
        reason = reason if reason.startswith(f"{failure}: ") else f"{failure}: {reason}"
        # The reason goes into events, whose reasons are bounded.
        if len(reason) > REASON_MAX:
            reason = reason[: REASON_MAX - 3] + "..."
        super().__init__(reason)
        self.failure = failure
        self.reason = reason
        self.started_at = started_at
        self.deterministic = deterministic
        self.checkpoint = checkpoint
        # The schedule-to-start timeout and the inputs of the attempt, once it was scheduled.
        self.schedule_to_start_s: int | None = None
        self.inputs: list[ArtifactReference] = []


@dataclass(frozen=True)
class Next:
    """What the next attempt of a task takes from the decision on the one that failed."""

    # The doubled schedule-to-start timeout of a re-place.
    schedule_to_start_s: int | None = None
    # The placement in a larger memory class of an escalation.
    placement: Placement | None = None


def control_queue() -> str:
    """The control queue of the build this worker runs, the only one its workflows use.

    The build id is fixed for the life of the worker process, and a pinned workflow only ever runs
    on its own build, so the read is deterministic for every workflow of the process.
    """
    with workflow.unsafe.sandbox_unrestricted():
        build_id = os.environ[NAMING.workflow.build_id_env]
    return NAMING.control_queue.replace("<build id>", build_id)


def retry_policy(retry: Retry) -> RetryPolicy:
    return RetryPolicy(
        maximum_attempts=retry.maximum_attempts,
        initial_interval=timedelta(seconds=retry.initial_interval_s or 1),
        backoff_coefficient=retry.backoff_coefficient or 2.0,
        maximum_interval=(
            timedelta(seconds=retry.maximum_interval_s) if retry.maximum_interval_s else None
        ),
    )


def named(group: list, name: str):
    return next(entry for entry in group if entry.name == name)


async def control(name: str, argument, result_type):
    """A control activity of naming.yaml on the build's control queue, whose typed error is a
    TaskFailed of that class once its retries end.

    Any other error is a defect of this build and fails the workflow task, which Temporal retries.
    """
    entry = named(NAMING.activities.control, name)
    try:
        return await workflow.execute_activity(
            name,
            argument,
            task_queue=control_queue(),
            start_to_close_timeout=CONTROL_START_TO_CLOSE,
            retry_policy=retry_policy(entry.retry),
            result_type=result_type,
        )
    except ActivityError as error:
        cause = error.cause
        if isinstance(cause, CancelledError):
            raise
        if isinstance(cause, ApplicationError) and cause.type in error_types():
            raise TaskFailed(cause.type, str(cause)) from error
        raise RuntimeError(f"{name} failed with no typed error: {cause}") from error


async def local(name: str, args: list, result_type):
    """A local activity of naming.yaml, retried without limit as the contract pins.

    Only a non-retryable error ends the retries, which is a defect of this build, so it fails the
    workflow task, which Temporal retries until a fixed build runs it.
    """
    entry = named(NAMING.activities.local, name)
    try:
        return await workflow.execute_local_activity(
            name,
            args=args,
            start_to_close_timeout=LOCAL_START_TO_CLOSE,
            retry_policy=retry_policy(entry.retry),
            local_retry_threshold=LOCAL_RETRY_THRESHOLD,
            result_type=result_type,
        )
    except (ActivityError, ApplicationError) as error:
        # A local activity's failure may arrive without the activity error around it.
        cause = error.cause if isinstance(error, ActivityError) else error
        if isinstance(cause, CancelledError):
            raise
        raise RuntimeError(f"{name} refused its call: {cause}") from error


def _failure_of(error: ActivityError) -> TaskFailed:
    """The class, reason, start time and handed-over checkpoint of a pool activity that ended
    without a result, read from its failure details or, after a timeout, its last heartbeat."""
    cause = error.cause
    try:
        if isinstance(cause, ApplicationError) and cause.type:
            details = FailureDetails.model_validate(cause.details[0]) if cause.details else None
            if details is None:
                return TaskFailed(cause.type, str(cause))
            return TaskFailed(
                cause.type,
                details.reason,
                details.started_at,
                deterministic=details.deterministic,
                checkpoint=details.checkpoint,
            )
        if isinstance(cause, ActivityTimeout) and cause.type and cause.type.name in TIMEOUT_CLASSES:
            beats = cause.last_heartbeat_details
            beat = HeartbeatDetails.model_validate(beats[0]) if beats else None
            failure = TIMEOUT_CLASSES[cause.type.name]
            if beat is None:
                return TaskFailed(failure, str(cause))
            return TaskFailed(failure, str(cause), beat.started_at, checkpoint=beat.checkpoint)
    except ValueError as invalid:
        # Details that break their model still end the attempt, under the class reported.
        return TaskFailed(getattr(cause, "type", None) or "worker_shutdown", str(invalid))
    return TaskFailed("worker_shutdown", f"{type(cause).__name__}: {cause}")


def cancelled(error: BaseException) -> bool:
    """Whether an error is a cancel: the asyncio one of a cancelled wait, or Temporal's of a
    cancelled activity, which arrives inside an ActivityError."""
    return isinstance(error, asyncio.CancelledError) or (
        isinstance(error, ActivityError) and isinstance(error.cause, CancelledError)
    )


def halt(job) -> None:
    """Raises the cancel of a task once the job is being cancelled or terminated, so nothing of
    the task is committed or written after its activity returns."""
    if job.stop in ("cancel", "terminate"):
        raise asyncio.CancelledError("the job is stopping")


def _request(task) -> PlacementRequest:
    return PlacementRequest(
        task_type=task.type,
        cpu_milli=task.cpu_milli,
        memory_mib=task.memory_mib,
        run_timeout_s=task.run_timeout_s,
    )


async def run(job, task, attempt: int, after: Next) -> TaskResult:
    """Places the task, unless an escalation already did, and runs it once on the queue of its
    placement from the task's newest checkpoint, or raises TaskFailed."""
    placed = after.placement or (await local(PLACE, [_request(task), False], PlaceResult)).root
    if not isinstance(placed, Placement):
        raise TaskFailed(placed.failure, placed.reason)
    if after.schedule_to_start_s is not None:
        placed = placed.model_copy(update={"schedule_to_start_s": after.schedule_to_start_s})
    job.placements[task.id] += 1
    placement = job.placements[task.id]
    await job.emit(
        job.task_event("task.placed", task.id, {"queue": placed.queue}, sequence=placement)
    )
    # A simulate task reads the component of its compile, and an aggregate one index.
    if task.type == "aggregate":
        inputs = [task.params.index or await control(*index_request(job, task))]
    else:
        inputs = [job.outputs[ref.task][ref.output] for ref in task.inputs]
    now = workflow.now()
    data = {
        "task_type": task.type,
        "cpu_cores": task.cpu_milli / 1000,
        "memory_mib": task.memory_mib,
        "inputs": [ref.model_dump(mode="json") for ref in inputs],
        "deadline": format_time(now + timedelta(seconds=placed.start_to_close_s)),
    }
    if checkpoint := job.budgets[task.id].checkpoint:
        data["resume_checkpoint"] = checkpoint.model_dump(mode="json")
    request_event: Event = job.identity.event("task.requested", now, data, taskid=task.id)
    pool = NAMING.activities.pool[0]
    # A control request that arrived while the task was placed starts nothing.
    halt(job)
    if job.stop == "pause":
        raise Withdrawn()
    handle = workflow.start_activity(
        pool.name,
        TaskInput(
            request=request_event,
            task_attempt=attempt,
            resume_counter=job.resume_counter,
            batch_id=job.batch_id,
            seed=job.params.seed,
            params=task.params.model_dump(mode="json"),
            cache_key=job.cache_keys.get(task.id),
        ),
        task_queue=placed.queue,
        schedule_to_start_timeout=timedelta(seconds=placed.schedule_to_start_s),
        start_to_close_timeout=timedelta(seconds=placed.start_to_close_s),
        heartbeat_timeout=timedelta(seconds=placed.heartbeat_s),
        retry_policy=retry_policy(pool.retry),
        cancellation_type=ActivityCancellationType[pool.cancellation_type],
        result_type=TaskResult,
    )
    job.in_flight[task.id] = handle
    try:
        return await handle
    except ActivityError as error:
        if isinstance(error.cause, CancelledError):
            # Cancelled before it started, or the worker answered the cancel without a result.
            halt(job)
            raise Withdrawn() from error
        failure = _failure_of(error)
        if job.stop == "pause" and failure.failure == "schedule_to_start_timeout":
            raise Withdrawn() from error
        failure.schedule_to_start_s = placed.schedule_to_start_s
        failure.inputs = inputs
        raise failure from error
    finally:
        job.in_flight.pop(task.id, None)


async def cache_hit(job, task) -> bool:
    """Looks the compile up in the cache and, on a hit, takes its outputs and skips the task."""
    answer: CacheAnswer = await control(
        "cache_lookup",
        CacheLookup(
            source=ContentReference.model_validate(task.params.source.model_dump()),
            entry=task.params.entry,
            job_id=job.job_id,
        ),
        CacheAnswer,
    )
    # A patched source may have another key, or none.
    job.cache_keys.pop(task.id, None)
    if answer.key is not None:
        job.cache_keys[task.id] = answer.key
    if answer.component is None:
        job.cache_hits.discard(task.id)
        return False
    job.cache_hits.add(task.id)
    job.outputs[task.id] = {"component": answer.component, "build-info": answer.build_info}
    data = {"reason": "compile-cache-hit", "cache_key": answer.key}
    # A compile that hits again, after a rebuild or a patched resume, writes a skip of its own.
    hits = job.skips[task.id]
    job.skips[task.id] += 1
    await job.emit(job.task_event("task.skipped", task.id, data, sequence=hits))
    return True


async def path(job, task) -> None:
    """Runs one task to its end in this run, attempt after attempt as the failure class table
    decides, and leaves its state in `job.states`.

    A cancel or terminate cancels the path: the task ends `cancelled` and the path re-raises the
    cancel, whichever of asyncio's or Temporal's reached it.
    """
    try:
        await _path(job, task)
    except (asyncio.CancelledError, ActivityError) as error:
        if not (cancelled(error) and job.stop in ("cancel", "terminate")):
            raise
        job.finish(task.id, "cancelled")
        raise asyncio.CancelledError(f"{task.id} is cancelled") from error


async def _path(job, task) -> None:
    after = Next()
    while True:
        if job.stop == "pause":
            # A manual pause places no new attempt, and the task runs after the resume.
            job.finish(task.id, "ready")
            return
        attempt = job.attempts[task.id] + 1
        try:
            # A compile looks the cache up before each attempt, so a lookup error is never a miss
            # and an entry another job recorded meanwhile is still taken.
            if task.type == "compile" and await cache_hit(job, task):
                job.finish(task.id, "skipped")
                return
            job.attempts[task.id] = attempt
            await _attempt(job, task, attempt, after)
            job.finish(task.id, "succeeded")
            return
        except Withdrawn:
            job.attempts[task.id] = attempt - 1
            job.finish(task.id, "ready")
            return
        except TaskFailed as failure:
            halt(job)
            job.attempts[task.id] = attempt
            following = await _failed(job, task, failure, attempt)
        if following is None:
            return
        after = following


async def _attempt(job, task, attempt: int, after: Next) -> None:
    """One attempt: run, commit the accepted outputs, record a compile's cache entry, and emit
    the task's success."""
    result = await run(job, task, attempt, after)
    halt(job)
    try:
        await control(
            "commit",
            Commit(
                job_id=job.job_id,
                task=task.id,
                attempt=attempt_identity(attempt, job.resume_counter),
                references=[*result.outputs, result.log_tail],
            ),
            None,
        )
        job.outputs[task.id] = {ref.kind: ref for ref in result.outputs}
        if task.type == "compile" and task.id in job.cache_keys:
            outputs = job.outputs[task.id]
            record = CacheRecord(
                key=job.cache_keys[task.id],
                component=outputs["component"],
                build_info=outputs["build-info"],
                job_id=job.job_id,
            )
            await control("cache_record", record, None)
    except TaskFailed as failure:
        failure.started_at = failure.started_at or result.started_at
        raise
    events = [
        job.task_event("task.started", task.id, {}, attempt=attempt, time=result.started_at),
        job.task_event("task.succeeded", task.id, {}, attempt=attempt),
    ]
    if task.type != "compile":
        [output] = result.outputs
        data = {"result": output.model_dump(mode="json")}
        events.append(job.task_event("task.result.ready", task.id, data, attempt=attempt))
    await job.emit(*events)


async def _failed(job, task, failure: TaskFailed, attempt: int) -> Next | None:
    """Decides on a failed attempt and carries the decision out: what the next attempt takes, or
    None once the task's state in this run is settled."""
    events = []
    if failure.started_at:
        when = failure.started_at
        events.append(job.task_event("task.started", task.id, {}, attempt=attempt, time=when))
    checkpoint = await _kept(job, task, failure.checkpoint, attempt, events)
    reason = {"reason": failure.reason}
    events.append(job.task_event("task.attempt.failed", task.id, reason, attempt=attempt))
    budgets = job.budgets[task.id]
    try:
        decision = decide(
            Failure(
                failure.failure,
                deterministic=failure.deterministic,
                checkpoint=checkpoint,
                cached_component_lost=_lost_component(job, task, failure) is not None,
                larger_class=(larger := await _larger_class(job, task, failure)) is not None,
                schedule_to_start_s=failure.schedule_to_start_s,
            ),
            task,
            budgets,
            job.limits,
        )
    except ValueError as defect:
        # A failure the table cannot decide, such as a refused commit of another attempt's
        # references, would repeat on every attempt, so it ends the task.
        workflow.logger.warning("%s of %s ends the task: %s", failure.failure, task.id, defect)
        return await _end(job, task, events, failure.reason, "failed")
    job.budgets[task.id] = decision.budgets
    if decision.kind == "end":
        return await _end(job, task, events, failure.reason, None)
    if job.history_spent():
        # Past the history budget no new attempt is placed, and the job fails once nothing runs.
        return await _end(job, task, events, failure.reason, "failed")
    await job.emit(*events)
    if decision.kind == "wait":
        job.finish(task.id, "paused")
        return None
    if decision.kind == "re-place":
        job.reschedules.add(1)
        return Next(schedule_to_start_s=decision.schedule_to_start_s)
    job.retries.add(1, {"budget": decision.charged or "free"})
    if decision.kind == "rebuild":
        compile_task, lost = _lost_component(job, task, failure)
        try:
            await rebuild(job, compile_task, lost)
        except TaskFailed as refused:
            return await _end(job, task, [], refused.reason, None)
        if job.states[compile_task.id] not in DONE:
            # The compile did not come back, so the task waits on it as on any failed dependency.
            job.finish(task.id, "pending")
            return None
    elif decision.backoff_s:
        # A control request ends the wait early.
        try:
            backoff = timedelta(seconds=decision.backoff_s)
            await workflow.wait_condition(lambda: job.stop is not None, timeout=backoff)
        except TimeoutError:
            pass
    if job.history_spent():
        return await _end(job, task, [], failure.reason, "failed")
    return Next(placement=larger if decision.escalate else None)


async def _end(job, task, events: list[Event], reason: str, state: str | None) -> None:
    """Ends a task in this run: `state`, or else paused or failed as its plan flag says.

    A paused task's cause stays in the history through `task.retries.exhausted`, and a failed
    task's through `task.failed`.
    """
    state = state or exhausted(job.plan, task.id, job.resume_counter)
    attempt = job.attempts[task.id]
    if state == "failed":
        data = {"reason": reason}
        events.append(job.task_event("task.failed", task.id, data, attempt=attempt))
    else:
        data = {"attempts": attempt, "reason": reason}
        sequence = job.resume_counter
        events.append(job.task_event("task.retries.exhausted", task.id, data, sequence=sequence))
    await job.emit(*events)
    job.finish(task.id, state)


async def _kept(job, task, checkpoint, attempt: int, events: list[Event]):
    """The checkpoint an attempt handed over, indexed and announced with
    `task.checkpoint.committed` the first time the workflow receives it, or None when there is
    none or the index refused it."""
    if checkpoint is None or checkpoint in job.budgets[task.id].checkpoints:
        return checkpoint
    request = IndexCheckpoint(
        reference=checkpoint,
        job_id=job.job_id,
        task=task.id,
        attempt=attempt_identity(attempt, job.resume_counter),
    )
    try:
        await control("index_checkpoint", request, None)
    except TaskFailed as refused:
        # An unindexed checkpoint is not one the next attempt may start from.
        workflow.logger.warning("the checkpoint of %s is dropped: %s", task.id, refused.reason)
        return None
    data = {"sequence": 0, "checkpoint": checkpoint.model_dump(mode="json")}
    events.append(job.task_event("task.checkpoint.committed", task.id, data, attempt=attempt))
    return checkpoint


async def _larger_class(job, task, failure: TaskFailed) -> Placement | None:
    """The placement in a larger memory class that the placer offers a task whose failure may
    escalate, or None."""
    row = failure_classes().get(failure.failure)
    budgets = job.budgets[task.id]
    oom_again = failure.failure == "oom_killed" and budgets.oom_streak > 0
    if row is None or budgets.escalated or not (row.action == "escalate-once" or oom_again):
        return None
    placed = (await local(PLACE, [_request(task), True], PlaceResult)).root
    return placed if isinstance(placed, Placement) and placed.escalated else None


def _lost_component(job, task, failure: TaskFailed):
    """The compile task and the component its cache hit gave this attempt, when the attempt lost
    an artifact and read such a component, or None."""
    if failure.failure != "ARTIFACT_LOST":
        return None
    for ref in task.inputs:
        if ref.output == "component" and ref.task in job.cache_hits:
            used = next((r for r in failure.inputs if r.kind == "component"), None)
            return (job.tasks[ref.task], used) if used else None
    return None


async def rebuild(job, compile_task, lost: ContentReference) -> None:
    """Compiles again the cached component that consumers lost, once for all of them.

    The invalidation retires the cache entry only while it still names the lost component, and
    the compile looks the cache up again, so an entry another job recorded meanwhile is taken.
    """
    key = (compile_task.id, lost.uri)
    if key not in job.rebuilds:
        job.rebuilds[key] = asyncio.ensure_future(_rebuild(job, compile_task, lost))
    try:
        await job.rebuilds[key]
    except TaskFailed:
        # A refused invalidation is tried again by the next consumer that loses the component.
        job.rebuilds.pop(key, None)
        raise


async def _rebuild(job, compile_task, lost: ContentReference) -> None:
    # The tasks that have not started yet wait for the new component.
    before = job.states[compile_task.id]
    job.finish(compile_task.id, "running")
    invalidate = CacheInvalidate(key=job.cache_keys[compile_task.id], component=lost)
    try:
        await control("cache_invalidate", invalidate, bool)
    except BaseException:
        # A refusal or a cancel leaves the compile as it was, so nothing waits on a path that
        # never started.
        job.finish(compile_task.id, before)
        raise
    await path(job, compile_task)
