"""The path of one task through the job workflow: the compile cache lookup, `place` as a local
activity, one `run_task` on the queue it returned, then the commit of the accepted attempt and the
cache record of a compile.

Workflow code, run inside the workflow sandbox with the platform libraries passed through.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    import os

    from wdf_events import Event, format_time
    from wdf_events.models import ContentReference
    from wdf_keys import attempt_identity

    from .aggregate import index_request
    from .contract import naming
    from .models import REASON_MAX, FailureDetails, HeartbeatDetails, Retry, TaskInput, TaskResult
    from .placement import ACTIVITY as PLACE
    from .placement import Placement, PlacementRequest, PlaceResult
    from .result import exhausted
    from .testing.control import CacheAnswer, CacheLookup, CacheRecord, Commit, error_types

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


class TaskFailed(Exception):
    """An attempt that ended without a result, with its class of the failure class table."""

    def __init__(self, failure: str, reason: str, started_at: str | None = None):
        reason = reason if reason.startswith(f"{failure}: ") else f"{failure}: {reason}"
        # The reason goes into events, whose reasons are bounded.
        if len(reason) > REASON_MAX:
            reason = reason[: REASON_MAX - 3] + "..."
        super().__init__(reason)
        self.failure = failure
        self.reason = reason
        self.started_at = started_at


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


def _named(group: list, name: str):
    return next(entry for entry in group if entry.name == name)


async def control(name: str, argument, result_type):
    """A control activity of naming.yaml on the build's control queue, whose typed error is a
    TaskFailed of that class once its retries end.

    Any other error is a defect of this build and fails the workflow task, which Temporal retries.
    """
    entry = _named(NAMING.activities.control, name)
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
        if isinstance(cause, ApplicationError) and cause.type in error_types():
            raise TaskFailed(cause.type, str(cause)) from error
        raise RuntimeError(f"{name} failed with no typed error: {cause}") from error


async def local(name: str, args: list, result_type):
    """A local activity of naming.yaml, retried without limit as the contract pins.

    Only a non-retryable error ends the retries, which is a defect of this build, so it fails the
    workflow task, which Temporal retries until a fixed build runs it.
    """
    entry = _named(NAMING.activities.local, name)
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
        raise RuntimeError(f"{name} refused its call: {cause}") from error


def _failure_of(error: ActivityError) -> TaskFailed:
    """The class, reason and start time of a pool activity that ended without a result."""
    cause = error.cause
    try:
        if isinstance(cause, ApplicationError) and cause.type:
            details = FailureDetails.model_validate(cause.details[0]) if cause.details else None
            reason = details.reason if details else str(cause)
            return TaskFailed(cause.type, reason, details and details.started_at)
        if isinstance(cause, TimeoutError) and cause.type and cause.type.name in TIMEOUT_CLASSES:
            beats = cause.last_heartbeat_details
            beat = HeartbeatDetails.model_validate(beats[0]) if beats else None
            failure = TIMEOUT_CLASSES[cause.type.name]
            return TaskFailed(failure, str(cause), beat and beat.started_at)
    except ValueError as invalid:
        # Details that break their model still end the attempt, under the class reported.
        return TaskFailed(getattr(cause, "type", None) or "worker_shutdown", str(invalid))
    return TaskFailed("worker_shutdown", f"{type(cause).__name__}: {cause}")


async def run(job, task, attempt: int) -> TaskResult:
    """Places the task and runs it once on the queue `place` returned, or raises TaskFailed."""
    request = PlacementRequest(
        task_type=task.type,
        cpu_milli=task.cpu_milli,
        memory_mib=task.memory_mib,
        run_timeout_s=task.run_timeout_s,
    )
    placed = (await local(PLACE, [request, False], PlaceResult)).root
    if not isinstance(placed, Placement):
        raise TaskFailed(placed.failure, placed.reason)
    await job.emit(
        job.task_event("task.placed", task.id, {"queue": placed.queue}, sequence=attempt)
    )
    # A simulate task reads the component of its compile, and an aggregate one index.
    if task.type == "aggregate":
        inputs = [task.params.index or await control(*index_request(job, task))]
    else:
        inputs = [job.outputs[ref.task][ref.output] for ref in task.inputs]
    now = workflow.now()
    request_event: Event = job.identity.event(
        "task.requested",
        now,
        {
            "task_type": task.type,
            "cpu_cores": task.cpu_milli / 1000,
            "memory_mib": task.memory_mib,
            "inputs": [ref.model_dump(mode="json") for ref in inputs],
            "deadline": format_time(now + timedelta(seconds=placed.start_to_close_s)),
        },
        taskid=task.id,
    )
    pool = NAMING.activities.pool[0]
    try:
        return await workflow.execute_activity(
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
    except ActivityError as error:
        raise _failure_of(error) from error


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
    if answer.key is not None:
        job.cache_keys[task.id] = answer.key
    if answer.component is None:
        return False
    job.outputs[task.id] = {"component": answer.component, "build-info": answer.build_info}
    data = {"reason": "compile-cache-hit", "cache_key": answer.key}
    await job.emit(job.task_event("task.skipped", task.id, data))
    return True


async def path(job, task) -> None:
    """Runs one task to its end in this run and leaves its state in `job.states`."""
    if task.type == "compile":
        try:
            if await cache_hit(job, task):
                job.finish(task.id, "skipped")
                return
        except TaskFailed as failure:
            # A lookup error is never a miss, so the task ends as a failed attempt.
            job.attempts[task.id] += 1
            await _end_failed(job, task, failure, job.attempts[task.id])
            return
    job.attempts[task.id] += 1
    attempt = job.attempts[task.id]
    try:
        result = await run(job, task, attempt)
        identity = attempt_identity(attempt, job.resume_counter)
        await control(
            "commit",
            Commit(
                job_id=job.job_id,
                task=task.id,
                attempt=identity,
                references=[*result.outputs, result.log_tail],
            ),
            None,
        )
    except TaskFailed as failure:
        await _end_failed(job, task, failure, attempt)
        return
    job.outputs[task.id] = {ref.kind: ref for ref in result.outputs}
    if task.type == "compile" and task.id in job.cache_keys:
        outputs = job.outputs[task.id]
        record = CacheRecord(
            key=job.cache_keys[task.id],
            component=outputs["component"],
            build_info=outputs["build-info"],
            job_id=job.job_id,
        )
        try:
            await control("cache_record", record, None)
        except TaskFailed as failure:
            await _end_failed(job, task, failure, attempt, result.started_at)
            return
    events = [
        job.task_event("task.started", task.id, {}, attempt=attempt, time=result.started_at),
        job.task_event("task.succeeded", task.id, {}, attempt=attempt),
    ]
    if task.type != "compile":
        [output] = result.outputs
        data = {"result": output.model_dump(mode="json")}
        events.append(job.task_event("task.result.ready", task.id, data, attempt=attempt))
    await job.emit(*events)
    job.finish(task.id, "succeeded")


async def _end_failed(
    job, task, failure: TaskFailed, attempt: int, started_at: str | None = None
) -> None:
    """Ends a task whose attempt failed: it pauses or fails as its plan flag says."""
    job.last_class[task.id] = failure.failure
    state = exhausted(job.plan, task.id, job.resume_counter)
    reason = {"reason": failure.reason}
    events = []
    if started_at := started_at or failure.started_at:
        events.append(job.task_event("task.started", task.id, {}, attempt=attempt, time=started_at))
    events.append(job.task_event("task.attempt.failed", task.id, reason, attempt=attempt))
    if state == "failed":
        events.append(job.task_event("task.failed", task.id, reason, attempt=attempt))
    await job.emit(*events)
    job.finish(task.id, state)
