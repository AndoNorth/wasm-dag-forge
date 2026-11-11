"""A fake pool worker that serves `run_task` with scripted outcomes, and the conformance test that
every pool worker passes."""

import asyncio
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

import yaml
from temporalio import activity, workflow
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import RawValue, RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from temporalio.workflow import ActivityCancellationType
from wdf_artifactclient.keys import attempt_key, content_key, kind_spec, uri_of
from wdf_events import format_time
from wdf_events.models import AttemptReference, CheckpointReference, ContentReference, ProducerRef
from wdf_keys import attempt_identity
from wdf_temporal import EventPayloadConverter

from ..contract import (
    Refused,
    check_bound,
    check_failure,
    check_task_result,
    declared_outputs,
    failure_classes,
    naming,
)
from ..models import (
    CONTRACT,
    FailureDetails,
    HeartbeatDetails,
    LogTailReference,
    RaisedFailure,
    TaskInput,
    TaskResult,
)

ENCODING = "json/plain"
CONTRACT_WORKFLOW = "WdfRunTaskOnce"


@dataclass(frozen=True)
class Succeed:
    """Writes the declared outputs and the log tail and returns their references."""


@dataclass(frozen=True)
class Fail:
    """Raises the failure class, with the row's deterministic value unless one is given."""

    failure: str
    deterministic: bool | None = None
    checkpoint: bool = False


@dataclass(frozen=True)
class Interruptible:
    """Heartbeats a new checkpoint each beat, answers a cancel with `operator_interrupt` and a
    final checkpoint, and succeeds after `seconds`."""

    seconds: float = 3600


@dataclass(frozen=True)
class Hang:
    """Heartbeats without progress, with a new checkpoint each beat when asked, never completes,
    and answers a cancel by being cancelled."""

    checkpoint: bool = False


@dataclass(frozen=True)
class Die:
    """Stops heartbeating and never completes, as a worker that is gone."""


@dataclass(frozen=True)
class Zombie:
    """Succeeds `after_s` seconds later, whatever happened to its attempt in the meantime."""

    after_s: float


Outcome = Succeed | Fail | Interruptible | Hang | Die | Zombie


@cache
def staged_outputs() -> dict[str, list[dict]]:
    """The declared outputs of each task type of staging.yaml, with their file names."""
    types = yaml.safe_load((CONTRACT / "staging.yaml").read_text())["task_types"]
    return {name: task["outputs"] for name, task in types.items()}


def producer_of(task: TaskInput) -> ProducerRef:
    return ProducerRef(
        job=task.request.jobid,
        task=task.request.taskid,
        attempt=attempt_identity(task.task_attempt, task.resume_counter),
    )


def _now() -> str:
    return format_time(datetime.now(UTC))


class FakePool:
    """An activity worker for one pool queue whose `run_task` does what `script` returns for each
    task input.

    With `store`, an ArtifactClient of the pool's store role, every reference names an uploaded
    object. Without it, the references are computed from the fake bytes and nothing is uploaded.
    `calls` holds every task input in the order the worker took them.
    """

    def __init__(
        self,
        client: Client,
        pool: str,
        script: Callable[[TaskInput], Outcome],
        store: Any = None,
        queue: str | None = None,
        beat_s: float = 0.2,
    ):
        self.client = client
        self.pool = pool
        self.queue = queue or naming().pools[pool]
        self.script = script
        self.store = store
        self.beat_s = beat_s
        self.calls: list[TaskInput] = []
        self._worker: Worker | None = None

    async def __aenter__(self) -> "FakePool":
        [pool_activity] = naming().activities.pool

        @activity.defn(name=pool_activity.name)
        async def run_task(task: TaskInput) -> TaskResult:
            self.calls.append(task)
            return await self._run(task, self.script(task))

        self._worker = Worker(
            self.client,
            task_queue=self.queue,
            activities=[run_task],
            max_heartbeat_throttle_interval=timedelta(seconds=self.beat_s),
            default_heartbeat_throttle_interval=timedelta(seconds=self.beat_s),
        )
        await self._worker.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._worker.__aexit__(*exc)

    async def _reference(self, task: TaskInput, kind: str, name: str, segment: str | None = None):
        body = f"{task.request.taskid} {task.task_attempt} {kind} {segment}".encode()
        spec = kind_spec(kind)
        if spec["class"] == "content-addressed":
            if self.store is not None:
                return await asyncio.to_thread(self.store.put_content, body, kind, name)
            sha256 = hashlib.sha256(body).hexdigest()
            return ContentReference(
                uri=uri_of(kind, content_key(kind, sha256)),
                sha256=sha256,
                size=len(body),
                name=name,
                kind=kind,
            )
        where = (
            producer_of(task),
            task.request.tenant,
            task.batch_id,
            task.seed if "<seed>" in spec["key"] else None,
            segment,
        )
        if self.store is not None:
            return await asyncio.to_thread(self.store.put_attempt, body, kind, name, *where)
        sha256 = hashlib.sha256(body).hexdigest()
        return AttemptReference(
            uri=uri_of(kind, attempt_key(kind, name, *where)),
            sha256=sha256,
            size=len(body),
            name=name,
            kind=kind,
            producer=where[0],
        )

    async def _checkpoint(self, task: TaskInput, sequence: int) -> CheckpointReference:
        reference = await self._reference(task, "checkpoint", "checkpoint.json", str(sequence))
        return CheckpointReference.model_validate(reference.model_dump())

    async def _log_tail(self, task: TaskInput) -> LogTailReference:
        reference = await self._reference(task, "log-tail", "tail.log")
        return LogTailReference.model_validate(reference.model_dump())

    async def _succeed(self, task: TaskInput, started_at: str) -> TaskResult:
        outputs = []
        for output in staged_outputs()[task.request.data["task_type"]]:
            outputs.append(await self._reference(task, output["kind"], output["file"]))
        return TaskResult(
            outputs=outputs, log_tail=await self._log_tail(task), started_at=started_at
        )

    async def _fail(
        self,
        task: TaskInput,
        failure: str,
        started_at: str,
        deterministic: bool | None = None,
        checkpoint: CheckpointReference | None = None,
    ):
        row = failure_classes().get(failure)
        if deterministic is None:
            deterministic = row is not None and row.deterministic is True
        details = FailureDetails(
            deterministic=deterministic,
            reason=f"{failure}: scripted by FakePool",
            started_at=started_at,
            checkpoint=checkpoint,
            log_tail=await self._log_tail(task),
        )
        raise ApplicationError(details.reason, details, type=failure, non_retryable=deterministic)

    def _beat(self, started_at: str, checkpoint: CheckpointReference | None = None) -> None:
        activity.heartbeat(
            HeartbeatDetails(
                progress=None, step=None, steps=None, checkpoint=checkpoint, started_at=started_at
            )
        )

    async def _run(self, task: TaskInput, outcome: Outcome) -> TaskResult:
        started_at = _now()
        match outcome:
            case Succeed():
                return await self._succeed(task, started_at)
            case Fail(failure, deterministic, checkpoint):
                reference = await self._checkpoint(task, 0) if checkpoint else None
                await self._fail(task, failure, started_at, deterministic, reference)
            case Interruptible(seconds):
                end = asyncio.get_running_loop().time() + seconds
                sequence = 0
                try:
                    while asyncio.get_running_loop().time() < end:
                        self._beat(started_at, await self._checkpoint(task, sequence))
                        sequence += 1
                        await asyncio.sleep(self.beat_s)
                except asyncio.CancelledError:
                    final = await self._checkpoint(task, sequence)
                    await self._fail(task, "operator_interrupt", started_at, False, final)
                return await self._succeed(task, started_at)
            case Hang(checkpoint):
                sequence = 0
                while True:
                    reference = await self._checkpoint(task, sequence) if checkpoint else None
                    self._beat(started_at, reference)
                    sequence += 1
                    await asyncio.sleep(self.beat_s)
            case Die():
                await asyncio.Event().wait()
            case Zombie(after_s):
                end = asyncio.get_running_loop().time() + after_s
                while (left := end - asyncio.get_running_loop().time()) > 0:
                    try:
                        await asyncio.sleep(left)
                    except asyncio.CancelledError:
                        pass
                return await self._succeed(task, started_at)
        raise TypeError(f"{outcome!r} is not an outcome")


@workflow.defn(name=CONTRACT_WORKFLOW)
class RunTaskOnce:
    """Runs one `run_task` as the job workflow does and returns what came back, raw."""

    def __init__(self):
        self.cancel_requested = False

    @workflow.signal
    def cancel(self) -> None:
        self.cancel_requested = True

    @workflow.run
    async def run(
        self, queue: str, task: TaskInput, cancel: bool, heartbeat_s: int, start_to_close_s: int
    ) -> dict:
        [pool_activity] = naming().activities.pool
        handle = workflow.start_activity(
            pool_activity.name,
            task,
            task_queue=queue,
            start_to_close_timeout=timedelta(seconds=start_to_close_s),
            heartbeat_timeout=timedelta(seconds=heartbeat_s),
            retry_policy=RetryPolicy(maximum_attempts=pool_activity.retry.maximum_attempts),
            cancellation_type=ActivityCancellationType[pool_activity.cancellation_type],
            result_type=RawValue,
        )
        if cancel:
            await workflow.wait_condition(lambda: self.cancel_requested or handle.done())
            handle.cancel()
        try:
            raw = await handle
        except ActivityError as error:
            cause = error.cause
            if isinstance(cause, ApplicationError):
                return {"type": cause.type, "details": list(cause.details)}
            if isinstance(cause, TimeoutError):
                return {"timeout": cause.type.name if cause.type else None}
            return {"error": f"{type(cause).__name__}: {cause}"}
        return {
            "encoding": raw.payload.metadata.get("encoding", b"").decode(),
            "result": raw.payload.data.decode(errors="replace"),
        }


async def run_task_once(
    client: Client,
    queue: str,
    task: TaskInput,
    *,
    heartbeat_s: int = 5,
    start_to_close_s: int = 300,
    interrupt: bool = False,
    seen: list[HeartbeatDetails] | None = None,
) -> dict:
    """Runs `run_task` once on `queue`, as the job workflow does, and returns what came back:
    `{encoding, result}` raw, `{type, details}` for an ApplicationError, `{timeout}` with the
    timeout type, or `{error}` for any other cause.

    With `interrupt`, the activity is cancelled once a heartbeat carries a checkpoint. Every
    heartbeat seen meanwhile is appended to `seen`, and one that is not HeartbeatDetails raises.
    """
    contract_queue = f"wdf-run-task-once-{uuid.uuid4()}"
    async with Worker(
        client,
        task_queue=contract_queue,
        workflows=[RunTaskOnce],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await client.start_workflow(
            RunTaskOnce.run,
            args=[queue, task, interrupt, heartbeat_s, start_to_close_s],
            id=f"run-task-once-{uuid.uuid4()}",
            task_queue=contract_queue,
        )
        if interrupt:
            await _cancel_after_checkpoint(client, handle, seen if seen is not None else [])
        return await handle.result()


async def _cancel_after_checkpoint(client, handle, seen: list[HeartbeatDetails]) -> None:
    converter = client.data_converter.payload_converter
    deadline = asyncio.get_running_loop().time() + 120
    while asyncio.get_running_loop().time() < deadline:
        description = await handle.describe()
        if description.status != WorkflowExecutionStatus.RUNNING:
            return
        for pending in description.raw_description.pending_activities:
            if pending.heartbeat_details.payloads:
                [beat] = converter.from_payloads(
                    pending.heartbeat_details.payloads, [HeartbeatDetails]
                )
                seen.append(beat)
                if beat.checkpoint is not None:
                    await handle.signal(RunTaskOnce.cancel)
                    return
        await asyncio.sleep(0.2)
    raise AssertionError("no heartbeat with a checkpoint within 120 s")


@dataclass(frozen=True)
class ContractCases:
    """Task inputs that make the worker under test succeed, fail, and run a checkpoint-capable
    simulate task until it is interrupted, which a pool without one leaves None."""

    succeeds: TaskInput
    fails: TaskInput
    interrupts: TaskInput | None = None


def _refused(problems: list[str], where: str, check: Callable[[], None]) -> None:
    try:
        check()
    except Refused as refused:
        problems.append(f"{where}: {refused.reason}: {refused}")


def _check_producers(problems, where, task: TaskInput, references) -> None:
    expected = producer_of(task)
    for reference in references:
        producer = getattr(reference, "producer", None)
        if producer is not None and producer != expected:
            problems.append(f"{where}: {reference.uri} names {producer}, not {expected}")


def _check_result(problems: list[str], task: TaskInput, outcome: dict) -> None:
    if "result" not in outcome:
        problems.append(f"succeeds: the worker did not succeed: {outcome}")
        return
    if outcome["encoding"] != ENCODING:
        problems.append(f"succeeds: the result is {outcome['encoding']}, not {ENCODING}")
        return
    try:
        result = TaskResult.model_validate_json(outcome["result"])
    except ValueError as error:
        problems.append(f"succeeds: the result is not a TaskResult of references: {error}")
        return
    _refused(problems, "succeeds", lambda: check_bound("task-result", result))
    _refused(problems, "succeeds", lambda: check_task_result(result))
    kinds = [reference.kind for reference in result.outputs]
    task_type = task.request.data["task_type"]
    if kinds != declared_outputs()[task_type]:
        problems.append(f"succeeds: {kinds} are not the declared outputs of {task_type}")
    _check_producers(problems, "succeeds", task, [*result.outputs, result.log_tail])


def _check_failure(
    problems: list[str], where: str, task: TaskInput, outcome: dict
) -> RaisedFailure | None:
    if "type" not in outcome:
        problems.append(f"{where}: the worker raised no ApplicationError: {outcome}")
        return None
    if len(outcome["details"]) != 1:
        problems.append(f"{where}: the failure has {len(outcome['details'])} details, not one")
        return None
    try:
        failure = RaisedFailure.model_validate(
            {"type": outcome["type"], "details": outcome["details"][0]}
        )
    except ValueError as error:
        problems.append(f"{where}: the details are not FailureDetails: {error}")
        return None
    _refused(problems, where, lambda: check_bound("failure-details", failure.details))
    _refused(problems, where, lambda: check_failure(failure))
    details = failure.details
    _check_producers(
        problems, where, task, [r for r in (details.checkpoint, details.log_tail) if r]
    )
    return failure


async def activity_contract(client: Client, queue: str, cases: ContractCases) -> None:
    """Raises AssertionError listing every rule that the worker serving `run_task` on `queue`
    breaks: a result that is not references to the declared outputs of the attempt, a failure
    whose type is not a reportable row of the failure class table or whose details break their
    row, and a cancel of a checkpoint-capable task answered without `operator_interrupt` and its
    final checkpoint.

    `client` uses the payload converter of wdf_temporal, as the job workflow does.
    """
    assert client.data_converter.payload_converter_class is EventPayloadConverter, (
        "the client must use wdf_temporal.event_data_converter"
    )
    problems: list[str] = []
    _check_result(problems, cases.succeeds, await run_task_once(client, queue, cases.succeeds))
    _check_failure(problems, "fails", cases.fails, await run_task_once(client, queue, cases.fails))
    if cases.interrupts is not None:
        seen: list[HeartbeatDetails] = []
        try:
            outcome = await run_task_once(
                client, queue, cases.interrupts, interrupt=True, seen=seen
            )
        except (AssertionError, ValueError) as error:
            problems.append(f"interrupts: {error}")
        else:
            failure = _check_failure(problems, "interrupts", cases.interrupts, outcome)
            if failure is not None and failure.type != "operator_interrupt":
                problems.append(f"interrupts: a cancel was answered with {failure.type}")
            elif failure is not None and failure.details.checkpoint is None:
                problems.append("interrupts: operator_interrupt carries no final checkpoint")
    assert not problems, "activity contract:\n" + "\n".join(problems)
